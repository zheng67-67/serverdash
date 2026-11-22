#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
serverdash — 本机服务器状态仪表盘（单文件，零第三方依赖）

用法:
    serverdash              全屏 TUI 仪表盘
    serverdash --banner     SSH 登录横幅摘要（不占屏，不挡 shell）
    serverdash --json       输出 JSON（调试/脚本用）
    serverdash --once       输出一次纯文本快照

设计原则:
    - 绝不联网：不监听端口，不发起任何网络请求
    - 只读：仅读 /proc /sys 和本地命令，不写系统
    - 零依赖：仅 Python 标准库（curses 属于标准库）
    - 优雅降级：读不到的项显示 N/A 或「不可用」，不崩溃
    - 区分「读不到(N/A)」和「真的是 0」

每条数据的来源在函数 docstring 里标明。
"""

from __future__ import annotations

import argparse
import curses
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------

APP = "serverdash"
VERSION = "1.0.0"
CONFIG_DIR = Path.home() / ".config" / APP
CONFIG_FILE = CONFIG_DIR / "config.toml"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------


@dataclass
class Config:
    """阈值与开关。读 ~/.config/serverdash/config.toml，缺失则用默认值。"""

    # 刷新
    interval: float = 2.0
    # 温度阈值 (°C)
    temp_warn: float = 60.0
    temp_danger: float = 80.0
    # 磁盘使用率阈值 (%)
    disk_warn: float = 80.0
    disk_danger: float = 90.0
    # 内存使用率阈值 (%)
    mem_warn: float = 80.0
    mem_danger: float = 92.0
    # 功能开关
    show_battery: bool = True
    show_smart: bool = True
    show_user: bool = True
    show_network: bool = True
    show_rapl: bool = True
    # 缓存秒数
    cache_slow: float = 30.0   # smartctl / lastb 这类重命令
    # 横幅
    banner_enabled: bool = True
    banner_color: bool = True
    # 敏感信息
    mask_mac: bool = True

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if not CONFIG_FILE.is_file():
            return cfg
        try:
            text = CONFIG_FILE.read_text(encoding="utf-8")
        except OSError:
            return cfg
        # 极简 TOML 解析：只支持 key = value，忽略 [section]
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or "=" not in line or line.startswith("["):
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if not hasattr(cfg, k):
                continue
            cur = getattr(cfg, k)
            try:
                if isinstance(cur, bool):
                    setattr(cfg, k, v.lower() in ("1", "true", "yes", "on"))
                elif isinstance(cur, float):
                    setattr(cfg, k, float(v))
                elif isinstance(cur, int):
                    setattr(cfg, k, int(float(v)))
            except (ValueError, TypeError):
                pass
        return cfg


CONFIG = Config.load()

# ----------------------------------------------------------------------------
# 终端与宽字符
# ----------------------------------------------------------------------------


def term_width(s: str) -> int:
    """按 wcwidth 规则算显示宽度。中文=2，ASCII=1。

    使用标准库 unicodedata.east_asian_width，不引入 wcwidth 包。
    """
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        ea = unicodedata.east_asian_width(ch)
        w += 2 if ea in ("W", "F") else 1
    return w


def trunc(s: str, width: int, ellipsis: str = "…") -> str:
    """按显示宽度截断字符串。"""
    if width <= 0:
        return ""
    if term_width(s) <= width:
        return s
    out, w = [], 0
    ew = term_width(ellipsis)
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width - ew:
            break
        out.append(ch)
        w += cw
    return "".join(out) + ellipsis


def pad(s: str, width: int, align: str = "<") -> str:
    """按显示宽度补齐/截断到 width。"""
    s = trunc(s, width)
    gap = width - term_width(s)
    if gap <= 0:
        return s
    if align == ">":
        return " " * gap + s
    if align == "^":
        left = gap // 2
        return " " * left + s + " " * (gap - left)
    return s + " " * gap


def bar(pct: float, width: int, full: str = "█", empty: str = "░") -> str:
    """ASCII 进度条，不用 Nerd Font 图标。"""
    pct = max(0.0, min(100.0, pct))
    n = int(round(width * pct / 100.0))
    return full * n + empty * (width - n)


def is_utf8() -> bool:
    """判断终端能否安全输出 UTF-8 中文。

    LANG / LC_ALL 里含 UTF-8 才认为可以；否则回退纯 ASCII。
    """
    for var in ("LC_ALL", "LC_CTYPE", "LANG"):
        v = os.environ.get(var, "")
        if v:
            return "UTF-8" in v.upper() or "UTF8" in v.upper()
    return False


UTF8 = is_utf8()

# 非 UTF-8 环境下的文案回退表
T = {
    "title": "SERVER DASHBOARD" if not UTF8 else "服务器状态仪表盘",
    "system": "SYSTEM" if not UTF8 else "系统",
    "cpu": "CPU",
    "temp": "TEMPS" if not UTF8 else "温度",
    "mem": "MEMORY" if not UTF8 else "内存",
    "disk": "DISK" if not UTF8 else "存储",
    "net": "NETWORK" if not UTF8 else "网络",
    "user": "USERS" if not UTF8 else "用户",
    "na": "N/A",
    "unavail": "N/A" if not UTF8 else "不可用",
    "need_root": "need root" if not UTF8 else "需 root",
    # 横幅标签
    "lbl_temp": "TEMP" if not UTF8 else "温度",
    "lbl_mem": "MEM " if not UTF8 else "内存",
    "lbl_load": "LOAD" if not UTF8 else "负载",
    "lbl_disk": "DISK" if not UTF8 else "磁盘",
    "lbl_bat": "BAT " if not UTF8 else "电池",
    "lbl_pkg": "PKG" if not UTF8 else "封装",
    "lbl_proc": "proc" if not UTF8 else "进程",
    "hint_full": "run serverdash for full dashboard" if not UTF8 else "查看完整仪表盘",
}

# ----------------------------------------------------------------------------
# 基础读取工具
# ----------------------------------------------------------------------------


def read(p: str | Path, default: str | None = None) -> str | None:
    """读一个小文件，失败返回 default。"""
    try:
        return Path(p).read_text(encoding="utf-8", errors="replace").strip()
    except (OSError, ValueError):
        return default


def read_int(p: str | Path) -> int | None:
    v = read(p)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str]:
    """执行本地命令（不涉及网络）。返回 (returncode, stdout+stderr)。"""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def is_root() -> bool:
    return os.geteuid() == 0


def human_bytes(n: float | int | None) -> str:
    if n is None:
        return T["na"]
    n = float(n)
    for unit in ("B", "K", "M", "G", "T", "P"):
        if abs(n) < 1024.0:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}E"


def human_uptime(sec: float) -> str:
    """把秒数转成 "1天6时30分"；非 UTF-8 终端回退 "1d 6h 30m"。"""
    sec = int(sec)
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, _ = divmod(sec, 60)
    if UTF8:
        parts = []
        if d:
            parts.append(f"{d}天")
        if h or d:
            parts.append(f"{h}时")
        parts.append(f"{m}分")
        return "".join(parts)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h or d:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


# ----------------------------------------------------------------------------
# 慢命令缓存（smartctl / lastb 等）
# ----------------------------------------------------------------------------


class Cache:
    """带 TTL 的内存缓存。单进程内有效，进程退出即失效。"""

    def __init__(self) -> None:
        self._d: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, ttl: float):
        item = self._d.get(key)
        if item is None:
            return None
        ts, val = item
        if time.monotonic() - ts > ttl:
            return None
        return val

    def put(self, key: str, val: Any) -> None:
        self._d[key] = (time.monotonic(), val)


CACHE = Cache()

# ----------------------------------------------------------------------------
# A. 主机与系统
# ----------------------------------------------------------------------------


def detect_virt() -> str:
    """判断是否运行在虚拟机上。

    可靠性排序（从高到低）:
        1. /proc/cpuinfo 的 hypervisor flag   ← 最终依据，最可靠
        2. /sys/hypervisor/type               Xen PV
        3. /sys/class/dmi/id/product_name     需 root，可细分厂商

    ⚠ 不能用 /sys/module/xen_blkfront 之类的模块存在性判断：
      Ubuntu 内核默认编译/加载了 xen_blkfront、xen_netfront 等驱动，
      物理机上这些目录同样存在，会造成误判。
    """
    cpuinfo = read("/proc/cpuinfo", "") or ""
    has_hv_flag = False
    for line in cpuinfo.splitlines():
        if line.startswith("flags"):
            flags = line.split(":", 1)[1].split() if ":" in line else []
            has_hv_flag = "hypervisor" in flags
            break

    # 先看 Xen PV（没有 hypervisor flag 也有 /sys/hypervisor/type）
    t = read("/sys/hypervisor/type")
    if t:
        return t.capitalize()

    # 没有 hypervisor flag → 物理机
    if not has_hv_flag:
        return "none"

    # 有 hypervisor flag，再用 DMI 细分厂商
    product = (read("/sys/class/dmi/id/product_name") or "").lower()
    sysvendor = (read("/sys/class/dmi/id/sys_vendor") or "").lower()
    board = (read("/sys/class/dmi/id/board_vendor") or "").lower()
    dmi = f"{sysvendor} {product} {board}"

    if "virtualbox" in dmi:
        return "VirtualBox"
    if "vmware" in dmi:
        return "VMware"
    if "qemu" in dmi:
        return "QEMU"
    if "kvm" in dmi:
        return "KVM"
    if "hyper-v" in dmi or "microsoft" in dmi:
        return "Hyper-V"
    if "xen" in dmi:
        return "Xen"
    if "amazon" in dmi:
        return "AWS"
    if "google" in dmi:
        return "GCE"
    return "VM"


def collect_system() -> dict[str, Any]:
    """主机信息。

    来源:
        /etc/os-release     发行版
        os.uname()          内核/架构/主机名
        /proc/uptime        开机时长
        /proc/loadavg       负载
        /proc/stat          procs_running / procs_blocked
        /sys/fs/cgroup      虚拟化判断辅助
    """
    d: dict[str, Any] = {}

    # 发行版
    osrel: dict[str, str] = {}
    osrel_text = read("/etc/os-release") or ""
    for line in osrel_text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            osrel[k.strip()] = v.strip().strip('"').strip("'")
    d["distro"] = osrel.get("PRETTY_NAME") or osrel.get("NAME") or "Linux"

    u = os.uname()
    d["kernel"] = u.release
    d["arch"] = u.machine
    d["hostname"] = u.nodename

    up = read("/proc/uptime")
    d["uptime_sec"] = float(up.split()[0]) if up else None

    la = read("/proc/loadavg")
    if la:
        parts = la.split()
        d["load"] = (parts[0], parts[1], parts[2])
        if len(parts) > 3 and "/" in parts[3]:
            r, t = parts[3].split("/", 1)
            d["procs_run"] = r
            d["procs_total"] = t
        else:
            d["procs_run"] = d["procs_total"] = None
    else:
        d["load"] = (T["na"],) * 3
        d["procs_run"] = d["procs_total"] = None

    # 线程总数：统计 /proc/*/status 的 Threads 太重（几百次 open），
    # 改用 /proc/stat 的 processes 累计值不合适。
    # 这里用轻量办法：读 /proc/loadavg 无解，于是从 /sys/kernel 拿不到，
    # 直接把线程数标为 N/A，避免高开销。
    d["threads"] = None

    # 虚拟化判断
    # 关键：host 上有 /sys/module/kvm 不代表自己是虚拟机（那是宿主机的 KVM 模块）。
    # 正确依据是 CPU 的 hypervisor flag，或 DMI 产品名。
    d["virt"] = detect_virt()

    # CPU 型号
    for line in (read("/proc/cpuinfo", "") or "").splitlines():
        if line.startswith("model name"):
            d["cpu_model"] = line.split(":", 1)[1].strip()
            break
    else:
        d["cpu_model"] = T["na"]

    d["ncpu"] = os.cpu_count() or len(
        [p for p in Path("/sys/devices/system/cpu").glob("cpu[0-9]*")]
    )
    return d


# ----------------------------------------------------------------------------
# B. CPU
# ----------------------------------------------------------------------------


def read_cpu_times() -> dict[str, tuple[int, int]]:
    """读 /proc/stat 每核 (total, idle)。"""
    out: dict[str, tuple[int, int]] = {}
    text = read("/proc/stat", "") or ""
    for line in text.splitlines():
        if not line.startswith("cpu"):
            continue
        parts = line.split()
        key = parts[0]
        if key == "cpu":
            continue
        try:
            nums = [int(x) for x in parts[1:]]
        except ValueError:
            continue
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
        total = sum(nums)
        out[key] = (total, idle)
    return out


def cpu_percent(prev: dict[str, tuple[int, int]],
                cur: dict[str, tuple[int, int]]) -> dict[str, float]:
    """两次采样算利用率。来源: /proc/stat。"""
    res: dict[str, float] = {}
    for k, (t2, i2) in cur.items():
        if k not in prev:
            continue
        t1, i1 = prev[k]
        dt = t2 - t1
        di = i2 - i1
        if dt <= 0:
            continue
        res[k] = max(0.0, min(100.0, (1.0 - di / dt) * 100.0))
    return res


def collect_cpu_freq() -> list[float] | None:
    """每核当前频率 (MHz)。

    来源: /sys/devices/system/cpu/cpuN/cpufreq/scaling_cur_freq (kHz)
    退化: /proc/cpuinfo 的 "cpu MHz"
    """
    freqs: list[float] = []
    for i in range(os.cpu_count() or 0):
        v = read_int(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq")
        if v is not None:
            freqs.append(v / 1000.0)
        else:
            freqs.append(-1.0)
    if all(f < 0 for f in freqs):
        # 回退
        vals = []
        for line in (read("/proc/cpuinfo", "") or "").splitlines():
            if line.startswith("cpu MHz"):
                try:
                    vals.append(float(line.split(":", 1)[1].strip()))
                except ValueError:
                    pass
        return vals or None
    return freqs


def collect_rapl() -> dict[str, float] | None:
    """RAPL 功耗 (W)。

    来源: /sys/class/powercap/intel-rapl:*/energy_uj (微焦耳)
    注意: energy_uj 在多数发行版是 0400 root:root，非 root 读不到。
          读不到时返回 {"__need_root__": 1.0} 供渲染层显示「需 root」。

    两次采样差值换算成功率。
    """
    if not Path("/sys/class/powercap").is_dir():
        return None

    zones = sorted(Path("/sys/class/powercap").glob("intel-rapl:*"))
    if not zones:
        return None

    # 先探权限
    probe = zones[0] / "energy_uj"
    if not probe.exists():
        return None
    try:
        probe.read_text()
    except PermissionError:
        return {"__need_root__": 1.0}
    except OSError:
        return None

    def snapshot() -> tuple[dict[str, int], float]:
        t = time.monotonic()
        out: dict[str, int] = {}
        for z in zones:
            name = read(z / "name")
            e = read_int(z / "energy_uj")
            if name and e is not None:
                out[name] = e
        return out, t

    s1, t1 = snapshot()
    time.sleep(0.25)
    s2, t2 = snapshot()
    dt = t2 - t1
    if dt <= 0:
        return None

    out: dict[str, float] = {}
    for name, e2 in s2.items():
        e1 = s1.get(name)
        if e1 is None:
            continue
        de = e2 - e1
        if de < 0:      # 计数器回绕（max_energy_range_uj 循环）
            continue
        out[name] = (de / 1e6) / dt
    return out or None


# ----------------------------------------------------------------------------
# C. 温度
# ----------------------------------------------------------------------------


@dataclass
class Sensor:
    source: str      # 芯片名或 thermal_zone 类型
    label: str       # 具体点位
    celsius: float
    path: str        # 数据来源路径


def collect_temps() -> list[Sensor]:
    """所有温度传感器。

    来源:
        /sys/class/hwmon/hwmonN/tempM_input   (毫摄氏度)
        /sys/class/hwmon/hwmonN/tempM_label
        /sys/class/thermal/thermal_zoneN/temp (毫摄氏度)
    """
    out: list[Sensor] = []

    for h in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        chip = read(h / "name") or h.name
        for t in sorted(h.glob("temp*_input")):
            v = read_int(t)
            if v is None:
                continue
            lbl = read(t.with_name(t.name.replace("_input", "_label")))
            out.append(Sensor(
                source=chip,
                label=lbl or t.name.replace("temp", "T").replace("_input", ""),
                celsius=v / 1000.0,
                path=str(t),
            ))

    # thermal_zone 补充（hwmon 没覆盖到的 ACPI 传感器）
    seen_types = {s.source for s in out}
    for z in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        ztype = read(z / "type")
        v = read_int(z / "temp")
        if ztype is None or v is None:
            continue
        if ztype in ("INT3400 Thermal",):  # 这是阈值配置，不是温度
            continue
        if ztype in seen_types:
            continue
        out.append(Sensor(
            source="thermal",
            label=ztype,
            celsius=v / 1000.0,
            path=str(z / "temp"),
        ))
    return out


# ----------------------------------------------------------------------------
# D. 风扇（本机未暴露，保留接口）
# ----------------------------------------------------------------------------


@dataclass
class Fan:
    chip: str
    label: str
    rpm: int
    pwm_pct: float | None


def collect_fans() -> list[Fan]:
    """风扇转速。

    来源: /sys/class/hwmon/hwmonN/fanM_input (RPM)
          /sys/class/hwmon/hwmonN/pwmM       (0-255)
    本机（笔记本）通常为空列表。
    """
    fans: list[Fan] = []
    for h in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        chip = read(h / "name") or h.name
        for f in sorted(h.glob("fan*_input")):
            v = read_int(f)
            if v is None:
                continue
            lbl = read(f.with_name(f.name.replace("_input", "_label")))
            idx = re.sub(r"\D", "", f.name) or "1"
            pwm = read_int(h / f"pwm{idx}")
            fans.append(Fan(
                chip=chip,
                label=lbl or f"fan{idx}",
                rpm=v,
                pwm_pct=(pwm / 255.0 * 100.0) if pwm is not None else None,
            ))
    return fans


# ----------------------------------------------------------------------------
# E. 内存与存储
# ----------------------------------------------------------------------------


def collect_mem() -> dict[str, Any]:
    """内存信息。来源: /proc/meminfo（整数单位 kB）。"""
    info: dict[str, int] = {}
    for line in (read("/proc/meminfo", "") or "").splitlines():
        m = re.match(r"(\w+):\s+(\d+)", line)
        if m:
            info[m.group(1)] = int(m.group(2)) * 1024  # 转字节
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    used = total - avail
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    return {
        "total": total,
        "used": used,
        "avail": avail,
        "free": info.get("MemFree", 0),
        "buff_cache": info.get("Buffers", 0) + info.get("Cached", 0),
        "pct": (used / total * 100.0) if total else 0.0,
        "swap_total": swap_total,
        "swap_used": swap_total - swap_free,
        "swap_pct": ((swap_total - swap_free) / swap_total * 100.0) if swap_total else 0.0,
    }


def collect_disks() -> list[dict[str, Any]]:
    """挂载点使用率。

    来源: /proc/mounts + os.statvfs()
    """
    out: list[dict[str, Any]] = []
    skip_fs = {
        "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2",
        "securityfs", "pstore", "bpf", "debugfs", "tracefs", "configfs",
        "fusectl", "mqueue", "hugetlbfs", "autofs", "binfmt_misc", "efivarfs",
        "squashfs", "overlay", "ramfs", "nsfs", "fuse.gvfsd-fuse",
    }
    seen: set[str] = set()
    for line in (read("/proc/mounts", "") or "").splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        dev, mnt, fstype, opts = parts[0], parts[1], parts[2], parts[3]
        if fstype in skip_fs:
            continue
        if mnt in seen:
            continue
        seen.add(mnt)
        try:
            st = os.statvfs(mnt)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total == 0:
            continue
        free = st.f_bavail * st.f_frsize
        used = total - st.f_bfree * st.f_frsize
        pct = used / total * 100.0 if total else 0.0
        inode_total = st.f_files
        inode_free = st.f_favail
        inode_pct = ((inode_total - inode_free) / inode_total * 100.0) if inode_total else 0.0
        ro = "ro" in opts.split(",")
        out.append({
            "dev": dev,
            "mount": mnt,
            "fstype": fstype,
            "total": total,
            "used": used,
            "free": free,
            "pct": pct,
            "inode_pct": inode_pct,
            "ro": ro,
        })
    return out


def collect_block_devices() -> list[dict[str, Any]]:
    """物理块设备清单（不含 loop）。

    来源: /sys/block/*/ (size 扇区数、model、rotational) + /proc/mounts 反查挂载点
    """
    devs: list[dict[str, Any]] = []
    mounts = {}
    for line in (read("/proc/mounts", "") or "").splitlines():
        p = line.split()
        if len(p) >= 2:
            mounts[p[0]] = p[1]

    for b in sorted(Path("/sys/block").glob("*")):
        name = b.name
        if name.startswith(("loop", "ram", "zram", "dm-", "md")):
            continue
        size_b = (read_int(b / "size") or 0) * 512
        if size_b == 0:
            continue
        model = read(b / "device" / "model") or read(b / "device" / "model_name") or ""
        rota = read_int(b / "queue" / "rotational")
        devnode = f"/dev/{name}"
        devs.append({
            "name": name,
            "devnode": devnode,
            "size": size_b,
            "model": model.strip() or T["na"],
            "rotational": bool(rota) if rota is not None else None,
            "mounts": [m for d, m in mounts.items() if d.startswith(devnode)],
        })
    return devs


def collect_smart() -> dict[str, dict[str, Any]]:
    """SMART 健康与温度（需 root 或 sudoers 规则）。

    来源: smartctl -H / -A
    缓存 30 秒，避免频繁调用。
    """
    cached = CACHE.get("smart", CONFIG.cache_slow)
    if cached is not None:
        return cached

    if not CONFIG.show_smart or not have("smartctl"):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for dev in collect_block_devices():
        dn = dev["devnode"]
        code, out = run(["smartctl", "-H", "-A", dn], timeout=8)
        if code < 0 or "Permission denied" in out or "failed:" in out:
            tries = ["sudo", "-n", "smartctl", "-H", "-A", dn]
            code2, out2 = run(tries, timeout=10)
            if code2 >= 0 and "Permission denied" not in out2:
                out = out2
            else:
                result[dn] = {"status": T["need_root"]}
                continue
        health = T["na"]
        m = re.search(r"SMART overall-health self-assessment test result:\s*(\w+)", out)
        if m:
            health = m.group(1)
        elif "SMART Health Status:" in out:
            m = re.search(r"SMART Health Status:\s*(\w+)", out)
            if m:
                health = m.group(1)
        elif "PASSED" in out:
            health = "PASSED"
        elif "FAILED" in out:
            health = "FAILED"

        temp = None
        for pat in (r"^Temperature:\s+(\d+)", r"Temperature_Celsius.*?\s(\d+)\s*\(",
                    r"Airflow_Temperature_Cel.*?\s(\d+)\s*\("):
            mm = re.search(pat, out, re.M)
            if mm:
                temp = int(mm.group(1))
                break

        used_pct = None
        mm = re.search(r"Percentage Used:\s+(\d+)%", out)
        if mm:
            used_pct = int(mm.group(1))

        hours = None
        mm = re.search(r"Power On Hours:\s+([\d,]+)", out)
        if mm:
            hours = int(mm.group(1).replace(",", ""))

        result[dn] = {
            "health": health,
            "temp": temp,
            "used_pct": used_pct,
            "hours": hours,
        }
    CACHE.put("smart", result)
    return result


# ----------------------------------------------------------------------------
# F. 网络
# ----------------------------------------------------------------------------


def is_private_ip(ip: str) -> bool:
    """只保留内网地址（含 CGNAT 100.64/10，Tailscale 用这一段）。"""
    if ":" in ip:
        return (ip.startswith(("fe80:", "fd", "fc"))
                or ip.startswith("::1"))
    if ip.startswith("127."):
        return True
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return (a == 10
            or (a == 172 and 16 <= b <= 31)
            or (a == 192 and b == 168)
            or (a == 100 and 64 <= b <= 127))   # CGNAT / Tailscale


def collect_net() -> list[dict[str, Any]]:
    """网卡信息。

    来源:
        /sys/class/net/*/address          MAC
        /sys/class/net/*/operstate        链路状态
        /sys/class/net/*/speed            链路速率
        /proc/net/dev                     收发字节
        /proc/net/fib_trie                IPv4（改用 ioctl 更简单，这里用 /proc/net/route 反查）
    IPv4 通过 socket ioctl 获取，仅保留内网地址。
    """
    import socket
    import struct
    import fcntl

    def iface_ips(name: str) -> list[str]:
        ips: list[str] = []
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                packed = struct.pack("256s", name.encode()[:15])
                res = fcntl.ioctl(s.fileno(), 0x8915, packed)  # SIOCGIFADDR
                ip = socket.inet_ntoa(res[20:24])
                if is_private_ip(ip):
                    ips.append(ip)
            finally:
                s.close()
        except OSError:
            pass
        return ips

    # 接收发计数
    rx: dict[str, int] = {}
    tx: dict[str, int] = {}
    for line in (read("/proc/net/dev", "") or "").splitlines()[2:]:
        if ":" not in line:
            continue
        name, data = line.split(":", 1)
        name = name.strip()
        nums = data.split()
        if len(nums) >= 9:
            try:
                rx[name] = int(nums[0])
                tx[name] = int(nums[8])
            except ValueError:
                pass

    out: list[dict[str, Any]] = []
    for i in sorted(Path("/sys/class/net").glob("*")):
        name = i.name
        if name == "lo":
            continue
        mac = read(i / "address") or ""
        if CONFIG.mask_mac and len(mac) >= 11:
            mac = mac[:8] + ":xx:xx:xx"
        speed = read(i / "speed")
        try:
            speed_mbps: float | None = float(speed) if speed and speed.lstrip("-").isdigit() else None
        except ValueError:
            speed_mbps = None
        if speed_mbps is not None and speed_mbps < 0:
            speed_mbps = None
        out.append({
            "name": name,
            "mac": mac,
            "state": read(i / "operstate") or T["na"],
            "speed": speed_mbps,
            "ips": iface_ips(name),
            "rx": rx.get(name),
            "tx": tx.get(name),
        })
    return out


def net_rates(prev: dict[str, tuple[int, int]],
              cur: dict[str, tuple[int, int]], dt: float) -> dict[str, tuple[float, float]]:
    """收发速率 (B/s)。来源: /proc/net/dev 两次差值。"""
    out: dict[str, tuple[float, float]] = {}
    if dt <= 0:
        return out
    for name, (r2, t2) in cur.items():
        if name not in prev:
            continue
        r1, t1 = prev[name]
        out[name] = ((r2 - r1) / dt, (t2 - t1) / dt)
    return out


# ----------------------------------------------------------------------------
# G. 用户
# ----------------------------------------------------------------------------


@dataclass
class Session:
    user: str
    tty: str
    host: str
    login_time: float
    idle: float | None = None


def _parse_utmp() -> list[Session]:
    """直接解析 /var/run/utmp，避免 fork `who`。

    utmp 记录结构 (Linux x86_64, 384 字节):
        0   short  ut_type
        4   int    ut_pid
        8   char[32] ut_line
        40  char[4]  ut_id
        44  char[32] ut_user
        76  char[256] ut_host
        332 int32  ut_tv.tv_sec
        336 int32  ut_tv.tv_usec
    """
    out: list[Session] = []
    data = None
    for p in ("/var/run/utmp", "/run/utmp"):
        try:
            data = Path(p).read_bytes()
            break
        except OSError:
            continue
    if not data:
        return out

    REC = 384
    USER_OFF, USER_LEN = 44, 32
    LINE_OFF, LINE_LEN = 8, 32
    HOST_OFF, HOST_LEN = 76, 256
    TVSEC_OFF = 332
    import struct

    for off in range(0, len(data) - REC + 1, REC):
        rec = data[off:off + REC]
        try:
            ut_type = struct.unpack_from("<h", rec, 0)[0]
        except struct.error:
            continue
        if ut_type != 7:  # USER_PROCESS
            continue
        try:
            user = rec[USER_OFF:USER_OFF + USER_LEN].split(b"\x00")[0].decode("utf-8", "replace")
            line = rec[LINE_OFF:LINE_OFF + LINE_LEN].split(b"\x00")[0].decode("utf-8", "replace")
            host = rec[HOST_OFF:HOST_OFF + HOST_LEN].split(b"\x00")[0].decode("utf-8", "replace")
            tvsec = struct.unpack_from("<i", rec, TVSEC_OFF)[0]
        except (struct.error, UnicodeDecodeError):
            continue
        if not user.strip():
            continue
        out.append(Session(user=user, tty=line, host=host, login_time=float(tvsec)))
    return out


def collect_users() -> dict[str, Any]:
    """登录用户与安全态势。

    来源:
        /var/run/utmp        当前会话（直接解析，不 fork who）
        /proc/*/status       每用户进程/内存统计
        /proc/uptime         空闲时间基准
        last -n 5            最近登录
        lastb                失败登录（需 root）
    """
    sessions = _parse_utmp()

    # 每用户进程数与 RSS
    per_user: dict[str, dict[str, int]] = {}
    for pdir in Path("/proc").iterdir():
        if not pdir.name.isdigit():
            continue
        stat = read(pdir / "status")
        if not stat:
            continue
        uid_line = None
        rss_line = None
        for line in stat.splitlines():
            if line.startswith("Uid:"):
                uid_line = line.split()[1]
            elif line.startswith("VmRSS:"):
                try:
                    rss_line = int(line.split()[1]) * 1024
                except (ValueError, IndexError):
                    pass
        if uid_line is None:
            continue
        try:
            import pwd
            uname = pwd.getpwuid(int(uid_line)).pw_name
        except (KeyError, ValueError):
            uname = f"uid{uid_line}"
        rec = per_user.setdefault(uname, {"procs": 0, "rss": 0})
        rec["procs"] += 1
        rec["rss"] += rss_line or 0

    # 最近登录（缓存 30s）
    last_out = CACHE.get("last", CONFIG.cache_slow)
    if last_out is None:
        code, out = run(["last", "-n", "5", "-w"], timeout=5)
        last_out = [l for l in out.splitlines() if l.strip() and not l.startswith(("wtmp begins", "btmp begins"))][:5]
        CACHE.put("last", last_out)

    # 失败登录（需 root）
    lastb_out = CACHE.get("lastb", CONFIG.cache_slow)
    if lastb_out is None:
        code, out = run(["lastb", "-n", "3"], timeout=5)
        if "Permission denied" in out or code != 0:
            lastb_out = [T["need_root"]]
        else:
            lastb_out = [l for l in out.splitlines() if l.strip()][:3]
        CACHE.put("lastb", lastb_out)

    # 本机物理控制台 tty（seat0 上、且不是 pts）
    console_tty = None
    for s in sessions:
        if s.tty.startswith("tty") and not s.tty.startswith("ttyS"):
            console_tty = s.tty
            break

    return {
        "sessions": sessions,
        "per_user": per_user,
        "last": last_out,
        "lastb": lastb_out,
        "console_tty": console_tty,
        "has_root": any(s.user == "root" for s in sessions),
        "count": len(sessions),
    }


# ----------------------------------------------------------------------------
# 电池
# ----------------------------------------------------------------------------


def collect_battery() -> dict[str, Any] | None:
    """电池状态（笔记本）。

    来源: /sys/class/power_supply/BAT*/  (capacity, status, power_now, energy_now)
    """
    if not CONFIG.show_battery:
        return None
    base = None
    for b in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        base = b
        break
    if base is None:
        return None

    cap = read_int(base / "capacity")
    status = read(base / "status") or T["na"]
    power = read_int(base / "power_now")      # µW
    energy = read_int(base / "energy_now")    # µWh
    energy_full = read_int(base / "energy_full")
    # 部分机器用 charge_* 而非 energy_*
    if energy is None:
        energy = read_int(base / "charge_now")
        energy_full = read_int(base / "charge_full")

    return {
        "capacity": cap,
        "status": status,
        "power_w": (power / 1e6) if power else None,
        "energy_wh": (energy / 1e6) if energy else None,
        "energy_full_wh": (energy_full / 1e6) if energy_full else None,
    }


# ----------------------------------------------------------------------------
# 全量采集
# ----------------------------------------------------------------------------


def collect_all(prev_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """一次性采集所有数据。prev_state 用于算速率。"""
    now = time.monotonic()
    cpu_now = read_cpu_times()
    net_now: dict[str, tuple[int, int]] = {}
    for line in (read("/proc/net/dev", "") or "").splitlines()[2:]:
        if ":" not in line:
            continue
        name, data = line.split(":", 1)
        nums = data.split()
        if len(nums) >= 9:
            try:
                net_now[name.strip()] = (int(nums[0]), int(nums[8]))
            except ValueError:
                pass

    state = {
        "t": now,
        "cpu": cpu_now,
        "net": net_now,
    }

    data: dict[str, Any] = {
        "ts": time.time(),
        "system": collect_system(),
        "temp": collect_temps(),
        "fan": collect_fans(),
        "mem": collect_mem(),
        "disk": collect_disks(),
        "block": collect_block_devices(),
        "smart": collect_smart(),
        "net": collect_net(),
        "user": collect_users(),
        "battery": collect_battery(),
        "freq": collect_cpu_freq(),
    }

    if prev_state:
        dt = now - prev_state.get("t", now)
        data["cpu_pct"] = cpu_percent(prev_state.get("cpu", {}), cpu_now)
        data["net_rate"] = net_rates(prev_state.get("net", {}), net_now, dt)
    else:
        data["cpu_pct"] = {}
        data["net_rate"] = {}

    # RAPL 单独算（内部自己采样）
    data["rapl"] = collect_rapl() if CONFIG.show_rapl else None

    data["_state"] = state
    return data


# ----------------------------------------------------------------------------
# 渲染：横幅（SSH 登录时显示，不占全屏）
# ----------------------------------------------------------------------------

# 256 色
C_RESET = "\033[0m"
C_DIM = "\033[38;5;244m"
C_LABEL = "\033[38;5;250m"
C_VALUE = "\033[38;5;255m"
C_OK = "\033[38;5;114m"
C_WARN = "\033[38;5;214m"
C_DANGER = "\033[38;5;203m"
C_ACCENT = "\033[38;5;117m"


def _c(s: str, color: str, enabled: bool) -> str:
    return f"{color}{s}{C_RESET}" if enabled else s


def _pct_color(pct: float, warn: float, danger: float) -> str:
    if pct >= danger:
        return C_DANGER
    if pct >= warn:
        return C_WARN
    return C_OK


def render_banner(data: dict[str, Any], width: int | None = None) -> str:
    """生成横幅文本（多行）。不占全屏，不阻塞 shell。"""
    use_color = CONFIG.banner_color and sys.stdout.isatty()
    W = width or shutil.get_terminal_size((80, 24)).columns
    W = max(60, min(W, 120))

    sysinfo = data["system"]
    mem = data["mem"]
    cpu_pct = data.get("cpu_pct", {})
    overall = sum(cpu_pct.values()) / len(cpu_pct) if cpu_pct else 0.0

    freq = data.get("freq") or []
    freq_txt = f"{min(freq):.0f}-{max(freq):.0f}MHz" if freq else T["na"]

    # 温度：取 CPU 封装
    pkg = None
    for s in data["temp"]:
        if "Package" in s.label or s.source == "coretemp" and "Package" in s.label:
            pkg = s.celsius
            break
    if pkg is None:
        for s in data["temp"]:
            if s.source == "coretemp":
                pkg = s.celsius
                break

    nvme_t = None
    for s in data["temp"]:
        if s.source == "nvme":
            nvme_t = s.celsius
            break

    lines: list[str] = []
    barw = max(10, W - 46)

    # 顶栏
    title = f" {T['title']} "
    host = sysinfo["hostname"]
    uptime = human_uptime(sysinfo["uptime_sec"] or 0)
    left = f"{_c(host, C_ACCENT, use_color)}  {sysinfo['distro']}  up {uptime}"
    lines.append(_c("╭" + "─" * (W - 2) + "╮", C_DIM, use_color))
    lines.append(_c("│", C_DIM, use_color) + pad(left, W - 2) + _c("│", C_DIM, use_color))

    # CPU
    c1 = _pct_color(overall, 70, 90)
    cpu_line = (f"{_c('CPU ', C_LABEL, use_color)}"
                f"{_c(bar(overall, barw), c1, use_color)} "
                f"{overall:5.1f}%  {freq_txt}")
    lines.append(_c("│", C_DIM, use_color) + " " + pad(cpu_line, W - 3) + _c("│", C_DIM, use_color))

    # 温度
    tparts = []
    if pkg is not None:
        tparts.append(f"{T['lbl_pkg']} {_c(f'{pkg:.0f}°C', _pct_color(pkg, CONFIG.temp_warn, CONFIG.temp_danger), use_color)}")
    if nvme_t is not None:
        tparts.append(f"NVMe {_c(f'{nvme_t:.0f}°C', _pct_color(nvme_t, CONFIG.temp_warn, CONFIG.temp_danger), use_color)}")
    if not tparts:
        tparts.append(_c(T["unavail"], C_DIM, use_color))
    tline = _c(T["lbl_temp"] + " ", C_LABEL, use_color) + "   ".join(tparts)
    lines.append(_c("│", C_DIM, use_color) + " " + pad(tline, W - 3) + _c("│", C_DIM, use_color))

    # 内存
    mc = _pct_color(mem["pct"], CONFIG.mem_warn, CONFIG.mem_danger)
    mline = (f"{_c(T['lbl_mem'] + ' ', C_LABEL, use_color)}"
             f"{_c(bar(mem['pct'], barw), mc, use_color)} "
             f"{human_bytes(mem['used'])}/{human_bytes(mem['total'])}  {mem['pct']:.0f}%")
    lines.append(_c("│", C_DIM, use_color) + " " + pad(mline, W - 3) + _c("│", C_DIM, use_color))

    # 负载
    la = sysinfo["load"]
    lline = f"{_c(T['lbl_load'] + ' ', C_LABEL, use_color)}{la[0]} {la[1]} {la[2]}   {T['lbl_proc']} {sysinfo.get('procs_total') or '?'}"
    lines.append(_c("│", C_DIM, use_color) + " " + pad(lline, W - 3) + _c("│", C_DIM, use_color))

    # 磁盘（最多 2 个）
    dparts = []
    for d in data["disk"][:2]:
        dc = _pct_color(d["pct"], CONFIG.disk_warn, CONFIG.disk_danger)
        dparts.append(f"{d['mount']} {_c(f'{d['pct']:.0f}%', dc, use_color)}")
    if dparts:
        dline = _c(T["lbl_disk"] + " ", C_LABEL, use_color) + "   ".join(dparts)
        lines.append(_c("│", C_DIM, use_color) + " " + pad(dline, W - 3) + _c("│", C_DIM, use_color))

    # 电池
    bat = data.get("battery")
    if bat and bat.get("capacity") is not None:
        bc = _pct_color(100 - bat["capacity"], 15, 5)
        bline = (f"{_c(T['lbl_bat'] + ' ', C_LABEL, use_color)}"
                 f"{_c(bar(bat['capacity'], barw // 2), bc, use_color)} "
                 f"{bat['capacity']}%  {bat['status']}")
        lines.append(_c("│", C_DIM, use_color) + " " + pad(bline, W - 3) + _c("│", C_DIM, use_color))

    # 底栏提示
    hint = f" {_c('serverdash', C_ACCENT, use_color)} {T['hint_full']}   {_c('serverdash --help', C_DIM, use_color)}"
    lines.append(_c("╰" + "─" * (W - 2) + "╯", C_DIM, use_color))
    lines.append(hint)
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# 渲染：全屏 TUI (curses)
# ----------------------------------------------------------------------------


class Dashboard:
    """curses 全屏仪表盘。"""

    def __init__(self, stdscr, cfg: Config):
        self.scr = stdscr
        self.cfg = cfg
        self.interval = cfg.interval
        self.prev_state: dict[str, Any] | None = None
        self.data: dict[str, Any] = {}
        self.scroll = 0
        self.running = True
        self.last_refresh = 0.0
        self.w = 80
        self.h = 24
        self.tiny = False

        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.scr.nodelay(True)
        self.scr.keypad(True)

        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                bg = -1
            except curses.error:
                bg = curses.COLOR_BLACK
            pairs = {
                "dim": (244, bg),
                "label": (250, bg),
                "value": (255, bg),
                "ok": (114, bg),
                "warn": (214, bg),
                "danger": (203, bg),
                "accent": (117, bg),
                "title": (231, 24),
            }
            self.cp: dict[str, int] = {}
            for i, (name, (fg, bgc)) in enumerate(pairs.items(), start=1):
                try:
                    curses.init_pair(i, fg, bgc)
                    self.cp[name] = curses.color_pair(i)
                except curses.error:
                    self.cp[name] = 0
        else:
            self.cp = {k: 0 for k in
                       ("dim", "label", "value", "ok", "warn", "danger", "accent", "title")}

    # ---- 绘制原语 ----

    def _attr(self, name: str) -> int:
        return self.cp.get(name, 0)

    def put(self, y: int, x: int, text: str, color: str = "value") -> None:
        """在 (y,x) 写字符串，自动按显示宽度裁剪。"""
        if y < 0 or y >= self.h or x >= self.w:
            return
        avail = self.w - x
        if avail <= 0:
            return
        s = trunc(text, avail)
        try:
            self.scr.addstr(y, x, s, self._attr(color))
        except curses.error:
            pass

    def hline(self, y: int, ch: str = "─", color: str = "dim") -> None:
        if 0 <= y < self.h:
            self.put(y, 0, ch * self.w, color)

    def header(self, y: int, text: str, color: str = "accent") -> int:
        """画分区标题，返回下一行 y。"""
        if self.tiny:
            line = f"[{text}]"
        else:
            line = f"── {text} " + "─" * max(0, self.w - term_width(text) - 4)
        self.put(y, 0, line, color)
        return y + 1

    # ---- 各分区 ----

    def draw_system(self, y: int) -> int:
        d = self.data.get("system", {})
        if self.tiny:
            self.put(y, 0, f"{d.get('hostname','?')} {d.get('distro','?')}", "value"); y += 1
            self.put(y, 0, f"{d.get('kernel','?')} {d.get('arch','?')}", "dim"); y += 1
            y = self.header(y, T["system"])
            self.put(y, 0, f"up {human_uptime(d.get('uptime_sec') or 0)}", "value"); y += 1
            self.put(y, 0, f"load {d['load'][0]} {d['load'][1]} {d['load'][2]}", "value"); y += 1
            self.put(y, 0, f"proc {d.get('procs_total') or '?'} virt {d.get('virt','?')}", "value"); y += 1
            return y

        y = self.header(y, T["system"])
        col2 = max(40, self.w // 2)

        rows_l = [
            ("主机", d.get("hostname") or "?"),
            ("发行版", d.get("distro") or "?"),
            ("内核", f"{d.get('kernel','?')} ({d.get('arch','?')})"),
            ("虚拟化", d.get("virt") or "?"),
        ]
        rows_r = [
            ("开机时长", human_uptime(d.get("uptime_sec") or 0)),
            ("负载 1/5/15", f"{d['load'][0]} {d['load'][1]} {d['load'][2]}"),
            ("进程 / 线程", f"{d.get('procs_total') or '?'} / {d.get('threads') or T['na']}"),
            ("CPU", trunc(d.get("cpu_model") or T["na"], col2 - 12)),
        ]
        for i in range(max(len(rows_l), len(rows_r))):
            if i < len(rows_l):
                k, v = rows_l[i]
                self.put(y + i, 0, f"{pad(k, 10)}", "label")
                self.put(y + i, 10, trunc(v, col2 - 12), "value")
            if i < len(rows_r):
                k, v = rows_r[i]
                self.put(y + i, col2, f"{pad(k, 14)}", "label")
                self.put(y + i, col2 + 14, trunc(v, self.w - col2 - 15), "value")
        return y + max(len(rows_l), len(rows_r)) + 1

    def draw_cpu(self, y: int) -> int:
        pct = self.data.get("cpu_pct", {})
        freq = self.data.get("freq") or []
        rapl = self.data.get("rapl")

        if self.tiny:
            y = self.header(y, T["cpu"])
            overall = sum(pct.values()) / len(pct) if pct else 0.0
            self.put(y, 0, f"{bar(overall, 20)} {overall:.0f}%", "ok"); y += 1
            if rapl:
                tot = rapl.get("package-0")
                if tot is not None:
                    self.put(y, 0, f"{tot:.1f}W", "value"); y += 1
            return y

        y = self.header(y, T["cpu"])
        # 总利用率
        cores = [k for k in sorted(pct.keys(), key=lambda s: int(s[3:]) if s[3:].isdigit() else 0)]
        overall = sum(pct.values()) / len(pct) if pct else 0.0
        barw = min(40, max(10, self.w // 3))
        self.put(y, 0, "总体", "label")
        self.put(y, 6, bar(overall, barw), _pct_color(overall, 70, 90))
        self.put(y, 6 + barw + 1, f"{overall:5.1f}%", "value")
        y += 1

        # 每核：频率 + 利用率，两列布局
        per_col = 2
        n = len(cores)
        rows = max(1, (n + per_col - 1) // per_col)
        colw = max(20, self.w // 2)
        barw2 = max(6, colw - 24)
        for idx, core in enumerate(cores):
            c = idx // rows
            r = idx % rows
            yy = y + r
            xx = c * colw
            num = core[3:]
            f = freq[idx] if idx < len(freq) else -1
            ftxt = f"{f:.0f}M" if f >= 0 else T["na"]
            p = pct[core]
            self.put(yy, xx, f"核{pad(num,2)}", "label")
            self.put(yy, xx + 5, bar(p, barw2), _pct_color(p, 70, 90))
            self.put(yy, xx + 5 + barw2 + 1, f"{p:5.1f}%", "value")
            self.put(yy, xx + colw - 7, f"{ftxt:>6}", "dim")
        y += rows + 1

        # RAPL 功耗
        if rapl:
            if rapl.get("__need_root__"):
                self.put(y, 0, "功耗", "label")
                self.put(y, 6, T["need_root"] + "（/sys/class/powercap 为 0400）", "dim")
            else:
                parts = []
                for k in ("package-0", "core", "uncore", "dram", "psys"):
                    if k in rapl:
                        parts.append(f"{k} {rapl[k]:.1f}W")
                if parts:
                    self.put(y, 0, "功耗", "label")
                    self.put(y, 6, "  ".join(parts), "value")
            y += 1
        return y

    def draw_temps(self, y: int) -> int:
        temps = self.data.get("temp", [])
        if self.tiny:
            y = self.header(y, T["temp"])
            for s in temps[:4]:
                self.put(y, 0, f"{s.source[:10]} {s.celsius:.1f}°C",
                         _pct_color(s.celsius, self.cfg.temp_warn, self.cfg.temp_danger))
                y += 1
            return y

        y = self.header(y, T["temp"])
        if not temps:
            self.put(y, 0, T["unavail"] + "（无 hwmon / thermal_zone）", "dim")
            return y + 2

        colw = max(28, self.w // 2)
        for i, s in enumerate(temps):
            c = i // ((len(temps) + 1) // 2) if len(temps) > 1 else 0
            r = i % ((len(temps) + 1) // 2) if len(temps) > 1 else i
            yy = y + r
            xx = c * colw
            color = _pct_color(s.celsius, self.cfg.temp_warn, self.cfg.temp_danger)
            name = f"{s.source[:12]}/{s.label[:14]}"
            self.put(yy, xx, pad(name, colw - 8), "label")
            self.put(yy, xx + colw - 8, f"{s.celsius:5.1f}°C", color)
        rows = (len(temps) + 1) // 2
        y += rows + 1

        # 电池
        bat = self.data.get("battery")
        if bat and bat.get("capacity") is not None:
            bc = _pct_color(100 - bat["capacity"], 15, 5)
            extra = f"  {bat['power_w']:.1f}W" if bat.get("power_w") else ""
            self.put(y, 0, "电池", "label")
            self.put(y, 6, bar(bat["capacity"], 20), bc)
            self.put(y, 28, f"{bat['capacity']:3d}%  {bat['status']}{extra}", "value")
            y += 1
        return y

    def draw_mem_disk(self, y: int) -> int:
        mem = self.data.get("mem", {})
        disks = self.data.get("disk", [])
        blocks = self.data.get("block", [])
        smart = self.data.get("smart", {})

        if self.tiny:
            y = self.header(y, T["mem"])
            self.put(y, 0, f"{human_bytes(mem.get('used'))}/{human_bytes(mem.get('total'))} "
                           f"{mem.get('pct',0):.0f}%",
                     _pct_color(mem.get("pct", 0), self.cfg.mem_warn, self.cfg.mem_danger))
            y += 1
            y = self.header(y, T["disk"])
            for d in disks[:3]:
                self.put(y, 0, f"{d['mount']} {d['pct']:.0f}%",
                         _pct_color(d["pct"], self.cfg.disk_warn, self.cfg.disk_danger))
                y += 1
            return y

        y = self.header(y, T["mem"])
        barw = min(30, max(8, self.w // 4))
        mc = _pct_color(mem.get("pct", 0), self.cfg.mem_warn, self.cfg.mem_danger)
        self.put(y, 0, "物理内存", "label")
        self.put(y, 10, bar(mem.get("pct", 0), barw), mc)
        self.put(y, 10 + barw + 1,
                 f"{human_bytes(mem.get('used'))} / {human_bytes(mem.get('total'))}  "
                 f"{mem.get('pct',0):.1f}%", "value")
        y += 1
        if mem.get("swap_total"):
            sc = _pct_color(mem["swap_pct"], self.cfg.mem_warn, self.cfg.mem_danger)
            self.put(y, 0, "交换分区", "label")
            self.put(y, 10, bar(mem["swap_pct"], barw), sc)
            self.put(y, 10 + barw + 1,
                     f"{human_bytes(mem['swap_used'])} / {human_bytes(mem['swap_total'])}  "
                     f"{mem['swap_pct']:.1f}%", "value")
            y += 1
        self.put(y, 0, "buff/cache", "label")
        self.put(y, 10, human_bytes(mem.get("buff_cache")), "dim")
        y += 2

        # 分区表（用 pad() 处理中文宽度，不能用 f-string 的 :<20）
        y = self.header(y, T["disk"])
        c_mount = 22
        c_fs = 10
        c_usage = 20
        c_pct = 9
        hdr = (pad("挂载点", c_mount) + pad("类型", c_fs)
               + pad("已用/总量", c_usage, ">") + pad("使用率", c_pct, ">") + " inode")
        self.put(y, 0, hdr, "dim")
        y += 1
        for d in disks[:6]:
            dc = _pct_color(d["pct"], self.cfg.disk_warn, self.cfg.disk_danger)
            usage = f"{human_bytes(d['used'])}/{human_bytes(d['total'])}"
            x = 0
            self.put(y, x, pad(d["mount"], c_mount), "value"); x += c_mount
            self.put(y, x, pad(d["fstype"], c_fs), "dim"); x += c_fs
            self.put(y, x, pad(usage, c_usage, ">") + " ", "value"); x += c_usage + 1
            b = bar(d["pct"], 8)
            self.put(y, x, b, dc); x += 8
            self.put(y, x, f"{d['pct']:5.1f}%", dc); x += 7
            if d.get("inode_pct"):
                self.put(y, x, f"{d['inode_pct']:5.1f}%",
                         _pct_color(d["inode_pct"], 80, 95))
            if d.get("ro"):
                self.put(y, min(x + 8, self.w - 3), "RO", "warn")
            y += 1

        # 物理盘 + SMART
        if blocks:
            y += 1
            for b in blocks[:4]:
                line = f"{b['name']:<10} {human_bytes(b['size']):>8}  {trunc(b['model'], 24)}"
                kind = "HDD" if b.get("rotational") else "SSD"
                line += f"  {kind}"
                self.put(y, 0, line, "value")
                s = smart.get(b["devnode"], {})
                if s:
                    if "status" in s:
                        self.put(y, 52, s["status"], "dim" if s["status"] == T["need_root"] else "warn")
                    else:
                        h = s.get("health", T["na"])
                        hc = "ok" if h == "PASSED" else ("danger" if h == "FAILED" else "dim")
                        self.put(y, 52, f"{h}", hc)
                        if s.get("temp") is not None:
                            tc = _pct_color(s["temp"], self.cfg.temp_warn, self.cfg.temp_danger)
                            self.put(y, 62, f"{s['temp']}°C", tc)
                        if s.get("used_pct") is not None:
                            self.put(y, 68, f"磨损{s['used_pct']}%",
                                     _pct_color(s["used_pct"], 70, 90))
                        if s.get("hours") is not None:
                            self.put(y, 78, f"{s['hours']}h", "dim")
                y += 1
        return y + 1

    def draw_net(self, y: int) -> int:
        nets = self.data.get("net", [])
        rates = self.data.get("net_rate", {})
        if not self.cfg.show_network:
            return y
        y = self.header(y, T["net"])
        if self.tiny:
            for n in nets[:2]:
                r = rates.get(n["name"], (0, 0))
                self.put(y, 0, f"{n['name'][:10]} {human_bytes(r[0])}/s", "value")
                y += 1
            return y

        for n in nets:
            r = rates.get(n["name"], (0.0, 0.0))
            ips = ", ".join(n["ips"]) or T["na"]
            spd = f"{n['speed']:.0f}Mb/s" if n.get("speed") else T["na"]
            self.put(y, 0, pad(n["name"], 14), "accent")
            self.put(y, 15, pad(n["state"], 8), "ok" if n["state"] == "up" else "dim")
            self.put(y, 24, pad(ips, 20), "value")
            self.put(y, 45, f"↓{human_bytes(r[0])}/s ↑{human_bytes(r[1])}/s", "value")
            self.put(y, 70, spd, "dim")
            y += 1
            self.put(y, 0, f"  累计 RX {human_bytes(n.get('rx'))}  TX {human_bytes(n.get('tx'))}",
                     "dim")
            y += 1
        return y + 1

    def draw_users(self, y: int) -> int:
        u = self.data.get("user", {})
        if not self.cfg.show_user:
            return y
        y = self.header(y, T["user"])

        sess = u.get("sessions", [])
        per = u.get("per_user", {})

        if self.tiny:
            for s in sess[:3]:
                self.put(y, 0, f"{s.user}@{s.tty or s.host}", "value")
                y += 1
            return y

        self.put(y, 0, f"在线 {u.get('count', 0)} 个会话"
                       + ("  ⚠ 有 root 会话" if u.get("has_root") else ""),
                 "warn" if u.get("has_root") else "ok")
        y += 1

        hdr = f"{'用户':<12} {'终端':<10} {'来源':<18} {'登录时间':<18} {'进程':>5} {'内存':>9}"
        self.put(y, 0, hdr, "dim"); y += 1
        for s in sess[:6]:
            t = time.strftime("%m-%d %H:%M", time.localtime(s.login_time))
            src = s.host or ("本地" if s.tty.startswith("tty") and "S" not in s.tty else T["na"])
            pu = per.get(s.user, {})
            uc = "danger" if s.user == "root" else "value"
            self.put(y, 0, pad(s.user, 12), uc)
            self.put(y, 13, pad(s.tty or T["na"], 10), "label")
            self.put(y, 24, pad(src, 18), "value")
            self.put(y, 43, pad(t, 18), "dim")
            self.put(y, 62, f"{pu.get('procs', 0):>5}", "value")
            self.put(y, 68, f"{human_bytes(pu.get('rss', 0)):>9}", "value")
            y += 1

        if u.get("console_tty"):
            y += 1
            self.put(y, 0, f"物理控制台: {u['console_tty']}", "accent")
            y += 1

        # 最近登录
        last = u.get("last", [])
        if last:
            y += 1
            self.put(y, 0, "最近登录", "label"); y += 1
            for line in last[:3]:
                self.put(y, 0, trunc(line, self.w), "dim")
                y += 1

        lb = u.get("lastb", [])
        if lb:
            self.put(y, 0, "失败登录", "label"); y += 1
            for line in lb[:2]:
                self.put(y, 0, trunc(line, self.w),
                         "dim" if line == T["need_root"] else "warn")
                y += 1
        return y + 1

    # ---- 主绘制 ----

    def draw(self) -> None:
        self.scr.erase()
        self.h, self.w = self.scr.getmaxyx()
        self.tiny = self.w < 80 or self.h < 24

        # 标题栏
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        title = f" {T['title']} v{VERSION} "
        right = f"{ts}  {self.interval:.0f}s  [1/2/5 间隔] [r 刷新] [q 退出] "
        self.put(0, 0, title, "title")
        rx = self.w - term_width(right)
        if rx > term_width(title):
            self.put(0, rx, right, "dim")
        self.hline(1)

        y = 2
        # 逐块绘制，超出屏幕就停（用滚轮/翻页）
        for fn in (self.draw_system, self.draw_cpu, self.draw_temps,
                   self.draw_mem_disk, self.draw_net, self.draw_users):
            if y >= self.h:
                break
            y = fn(y)

        self.scr.refresh()

    def handle_key(self) -> None:
        key = self.scr.getch()
        if key == -1:
            return
        if key in (ord("q"), ord("Q")):
            self.running = False
        elif key == ord("1"):
            self.interval = 1.0
        elif key == ord("2"):
            self.interval = 2.0
        elif key == ord("5"):
            self.interval = 5.0
        elif key in (ord("r"), ord("R")):
            self.last_refresh = 0.0
        elif key == 27:  # ESC
            self.running = False
        elif key == curses.KEY_RESIZE:
            self.scr.erase()

    def run(self) -> int:
        try:
            while self.running:
                now = time.monotonic()
                if now - self.last_refresh >= self.interval or not self.data:
                    data = collect_all(self.prev_state)
                    self.prev_state = data["_state"]
                    self.data = data
                    self.last_refresh = now
                    self.draw()
                # 非阻塞等按键
                try:
                    self.scr.timeout(200)
                except curses.error:
                    pass
                self.handle_key()
        finally:
            # ⚠ 不要在这里调 curses.endwin()：
            #   curses.wrapper() 退出时会自己调用，重复调用会抛异常。
            #   只把 keypad 关掉即可，其余交给 wrapper。
            try:
                self.scr.keypad(False)
            except curses.error:
                pass
        return 0


def run_tui(cfg: Config) -> int:
    os.environ.setdefault("ESCDELAY", "25")
    return curses.wrapper(lambda scr: Dashboard(scr, cfg).run())


# ----------------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        prog=APP,
        description="服务器状态仪表盘（本机只读，不联网）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  serverdash            全屏 TUI
  serverdash --banner   SSH 登录横幅
  serverdash --once     输出一次纯文本
  serverdash --json     输出 JSON
  serverdash --init     生成默认配置文件
""",
    )
    ap.add_argument("--banner", action="store_true", help="输出登录横幅（不占全屏）")
    ap.add_argument("--once", action="store_true", help="输出一次纯文本快照")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--init", action="store_true", help="生成默认配置文件")
    ap.add_argument("--no-color", action="store_true", help="禁用颜色")
    ap.add_argument("--version", action="version", version=f"{APP} {VERSION}")
    args = ap.parse_args()

    if args.no_color:
        CONFIG.banner_color = False

    if args.init:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            print(f"配置已存在: {CONFIG_FILE}")
            return 0
        CONFIG_FILE.write_text(DEFAULT_CONFIG, encoding="utf-8")
        print(f"已生成: {CONFIG_FILE}")
        return 0

    if args.json:
        data = collect_all(None)
        data.pop("_state", None)
        data["temp"] = [vars(s) for s in data["temp"]]
        data["fan"] = [vars(f) for f in data["fan"]]
        data["user"]["sessions"] = [vars(s) for s in data["user"]["sessions"]]
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.banner:
        if not CONFIG.banner_enabled:
            # 配置里关掉了横幅：静默退出，钩子不用改
            return 0
        # 需要两次采样才能算 CPU 利用率
        prev = collect_all(None)
        time.sleep(0.6)
        data = collect_all(prev["_state"])
        print(render_banner(data))
        return 0

    if args.once:
        # 同样需要两次采样，否则 CPU 恒为 0.0%
        prev = collect_all(None)
        time.sleep(0.6)
        data = collect_all(prev["_state"])
        print(render_banner(data))
        print()
        print("== 温度明细 ==")
        if data["temp"]:
            for s in data["temp"]:
                print(f"  {s.source:20s} {s.label:20s} {s.celsius:6.1f}°C")
        else:
            print("  （无传感器）")
        return 0

    # 默认：全屏 TUI
    if not sys.stdout.isatty():
        print("错误：全屏模式需要 tty。请用 --banner / --once / --json。", file=sys.stderr)
        return 2
    return run_tui(CONFIG)


DEFAULT_CONFIG = """# serverdash 配置
# 修改后重新运行即可生效；不在文件里的项使用内置默认值。

# ---- 刷新 ----
# 默认刷新间隔（秒），运行时按 1/2/5 可临时切换
interval = 2.0

# ---- 温度阈值 (°C) ----
temp_warn = 60.0
temp_danger = 80.0

# ---- 磁盘使用率阈值 (%) ----
disk_warn = 80.0
disk_danger = 90.0

# ---- 内存使用率阈值 (%) ----
mem_warn = 80.0
mem_danger = 92.0

# ---- 功能开关 ----
show_battery = true
show_smart = true
show_user = true
show_network = true
show_rapl = true

# ---- 慢命令缓存（秒），smartctl / lastb 之类 ----
cache_slow = 30.0

# ---- SSH 登录横幅 ----
banner_enabled = true
banner_color = true

# ---- 敏感信息 ----
mask_mac = true
"""

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
