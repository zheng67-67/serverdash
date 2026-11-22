# serverdash — 服务器状态仪表盘

一个 **只读、不联网、零第三方依赖** 的 Linux 终端仪表盘。

SSH 登录时显示一行摘要横幅，输入 `serverdash` 打开全屏 TUI。

```
$ ssh host

╭──────────────────────────────────────────────────────────────────────────────╮
│myserver  Ubuntu 24.04.5 LTS  up 12天3时41分                                  │
│ CPU █░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   2.9%  800-3900MHz                   │
│ 温度 封装 52°C   NVMe 45°C                                                   │
│ 内存 ██████░░░░░░░░░░░░░░░░░░░░░░░░░░░░ 3.2G/19.3G  16%                      │
│ 负载 0.09 0.12 0.06   进程 1120                                              │
│ 磁盘 / 11%   /data 42%                                                       │
│ 电池 █████████████████ 100%  Not charging                                    │
╰──────────────────────────────────────────────────────────────────────────────╯
 serverdash 查看完整仪表盘   serverdash --help
user@myserver:~$
```

## 它解决什么问题

日常 SSH 进服务器，想知道「机器现在什么状态」要敲 `uptime`、`free -h`、`df -h`、`sensors`、`who` 五条命令。

serverdash 把这些压成一屏，且**登录就能看见**，不占终端、不挡 shell。

---

## 快速开始

```bash
# 一路装到底（推荐）
git clone https://github.com/YOUR_NAME/serverdash.git
cd serverdash
bash install.sh

# 或者装完立刻能用（含 SMART 权限）
sudo bash install.sh --user "$(id -un)" --with-smart
```

**装完立即体验：**

```bash
serverdash              # 全屏 TUI
sudo serverdash         # root 版，能看 RAPL 功耗
```

---

## 三种用法

| 命令 | 输出 | 场景 |
|---|---|---|
| `serverdash` | 全屏 TUI，2 秒刷新 | 想看详细状态 |
| `serverdash --banner` | 7 行摘要 | SSH 登录钩子（默认装好） |
| `serverdash --json` | JSON | 接监控系统 / 脚本 |
| `serverdash --once` | 纯文本快照 | 日志记录 / 一次性检查 |

### 全屏 TUI 按键

| 键 | 作用 |
|---|---|
| `1` / `2` / `5` | 切换刷新间隔 1s / 2s / 5s |
| `r` | 立即刷新 |
| `q` / `Esc` | 退出（终端自动恢复） |

### 布局自适应

| 终端尺寸 | 行为 |
|---|---|
| ≥ 80×24 | 双列完整布局 |
| < 80×24 | **自动切精简单列**（`[系统]` `[CPU]` 这样的分区标题） |

**不用 Nerd Font 图标**，进度条全是 `█ ░` ASCII。
**非 UTF-8 终端**自动回退纯英文文案，不乱码。

---

## 显示什么

### 系统
主机名、发行版、内核、架构、开机时长、负载（1/5/15）、进程数、CPU 型号、是否虚拟化

### CPU
每核实时频率、总体与每核利用率（`/proc/stat` 两次采样，不 fork `top`）、封装温度、RAPL 功耗（需 root）

### 温度
主板、芯片组、CPU 封装与各核心、NVMe/SSD、GPU、网卡、机箱/进风口（全部来自 hwmon + thermal_zone）

### 内存与存储
内存/交换已用/总量/百分比、buff/cache；每个分区使用率 + inode 使用率；每块盘型号、容量、SMART 健康/温度/磨损/通电时长

### 网络
每张网卡的内网 IP、链路速率、实时收/发速率、累计流量（**公网 IP 不显示**）

### 用户
当前会话（用户名/tty/来源 IP/登录时间）、每用户进程数与内存占用、最近登录、失败登录（需 root）、物理控制台是哪个 tty

---

## 安全设计

这个工具在**生产环境**用，所以：

| 约束 | 实现 |
|---|---|
| **绝不联网** | 不监听端口、不 curl、不上报遥测。`strace -e trace=network` 实测零 socket 调用 |
| **只读** | 仅读 `/proc` `/sys` 和本地命令，不改系统 |
| **零第三方依赖** | 只用 Python 标准库（`curses` 是标准库） |
| **不污染全局** | 默认不碰 `/etc/profile`、`/etc/bash.bashrc`，只写目标用户 `~/.bash_profile` |
| **敏感信息遮罩** | MAC 默认显示 `98:2c:bc:xx:xx:xx`，DMI 序列号完全不采集 |
| **优雅降级** | 读不到的项显示 `N/A` 或 `需 root`，不崩溃 |

---

## 安装详解

```bash
bash install.sh                       # 装到当前用户 ~/.local/bin
sudo bash install.sh --user alice --with-smart   # 以 root 装给 alice
sudo bash install.sh --system         # 装到 /usr/local/bin（全局）
bash install.sh --no-hook             # 只装程序，不动登录钩子
```

### 会改动的地方（全部先备份到 `~/.local/share/serverdash/backups/`）

| 路径 | 动作 |
|---|---|
| `~/.local/bin/serverdash` | 主程序（覆盖前备份） |
| `/usr/local/bin/serverdash` | 全局符号链接（需 root，方便 root 下直接用） |
| `~/.config/serverdash/config.toml` | 仅当不存在时创建 |
| `~/.bash_profile` | 追加登录钩子（带 `# >>> serverdash >>>` 标记，幂等） |
| `/etc/sudoers.d/serverdash-smart` | 仅 `--with-smart` 时写入 |

### ⚠️ 两个常见坑

**坑 1：`~` 在 root 下不是你的家目录**

```bash
# ❌ 已经是 root 时
sudo bash ~/serverdash-src/install.sh
# → bash: /root/serverdash-src/install.sh: 没有那个文件或目录

# ✅ 用绝对路径，或退出 root
bash /home/alice/serverdash-src/install.sh
```

原因：`~` 由**当前 shell** 展开。root 下 `~` = `/root`。

**坑 2：`sudo` 会把 `$HOME` 重置成 `/root`**

```bash
# ❌ 会装到 /root/.local/bin，目标用户用不了
sudo bash install.sh

# ✅ 明确指定
sudo bash install.sh --user alice --with-smart
```

新版 `install.sh` 已处理：优先 `--user`，其次自动识别 `SUDO_USER`，都没有就报错提示。

---

## 配置

`~/.config/serverdash/config.toml`，改完下次运行生效：

```toml
interval = 2.0          # 刷新间隔（秒）

temp_warn   = 60.0      # 温度警告阈值 (°C)
temp_danger = 80.0      # 温度危险阈值 (°C)

disk_warn   = 80.0      # 磁盘使用率警告 (%)
disk_danger = 90.0

mem_warn    = 80.0      # 内存使用率警告 (%)
mem_danger  = 92.0

show_battery = true
show_smart   = true
show_user    = true
show_network = true
show_rapl    = true

cache_slow = 30.0       # smartctl / lastb 缓存秒数

banner_enabled = true   # 登录横幅开关
banner_color   = true

mask_serial = true      # 序列号遮罩
mask_mac    = true      # MAC 遮罩
```

### 环境变量

```bash
SERVERDASH_FORCE=1 serverdash     # 强制显示（调试样式用）
SERVERDASH_SKIP=1   serverdash    # 跳过横幅
```

---

## 关闭 / 卸载

**临时关闭**（当前 shell）：

```bash
export SERVERDASH_SKIP=1
```

**彻底卸载**：

```bash
bash uninstall.sh

# 以 root 卸载指定用户的
sudo bash uninstall.sh --user alice

# 保留配置文件
bash uninstall.sh --keep-config
```

会清理：主程序、`/usr/local/bin` 链接、`.bash_profile` 钩子块、`/etc/sudoers.d/serverdash-smart`。

---

## SMART 权限

`/dev/nvme0n1`、`/dev/sda` 的 SMART 数据只有 root 能读。两种方式：

**方式 A：sudoers 免密（推荐）**

```bash
sudo bash install.sh --user "$(id -un)" --with-smart
```

写入 `/etc/sudoers.d/serverdash-smart`：

```
alice ALL=(root) NOPASSWD: /usr/sbin/smartctl
```

**方式 B：不加 sudoers**

磁盘区显示 `需 root`，其余功能不受影响。

```bash
sudo apt install smartmontools    # 约 500 KB
```

---

## 需要 root 才能看到的项

| 信息 | 普通用户 | root |
|---|---|---|
| **SMART 健康/温度/磨损/通电时长** | 配 sudoers 后可读 | ✅ |
| **RAPL 功耗（package/core/dram/psys）** | `需 root` | ✅ |
| **失败登录（lastb）** | `需 root` | ✅ |
| **DMI 序列号** | **完全不采集**（隐私） | 不采集 |
| **完整 MAC** | `98:2c:bc:xx:xx:xx` | 同样遮罩 |

**建议只开 SMART**，功耗用 `sudo serverdash` 临时看。

---

## 数据来源（维护用）

| 区块 | 数据 | 来源 |
|---|---|---|
| 系统 | 发行版 | `/etc/os-release` |
| | 内核/架构/主机名 | `os.uname()` |
| | 开机时长 | `/proc/uptime` |
| | 负载/进程数 | `/proc/loadavg` |
| | 虚拟化 | `/proc/cpuinfo` 的 `hypervisor` flag + DMI |
| CPU | 每核利用率 | `/proc/stat` 两次采样差值 |
| | 每核频率 | `/sys/devices/system/cpu/cpuN/cpufreq/scaling_cur_freq` |
| | RAPL 功耗 | `/sys/class/powercap/intel-rapl:*/energy_uj` 两次差值 |
| 温度 | hwmon | `/sys/class/hwmon/hwmonN/tempM_input`（毫摄氏度） |
| | ACPI | `/sys/class/thermal/thermal_zoneN/temp` |
| 风扇 | 转速/PWM | `/sys/class/hwmon/hwmonN/fanM_input` / `pwmM` |
| 内存 | 用量 | `/proc/meminfo` |
| 存储 | 挂载/使用率 | `/proc/mounts` + `os.statvfs()` |
| | 块设备 | `/sys/block/*/device/model`、`queue/rotational` |
| | SMART | `smartctl -H -A`（缓存 30s） |
| 网络 | 收发字节 | `/proc/net/dev` 两次差值 |
| | IPv4 | `ioctl(SIOCGIFADDR)`，仅内网 |
| 用户 | 会话 | `/var/run/utmp`（直接解析，不 fork `who`） |
| | 每用户进程/RSS | `/proc/*/status` |
| 电池 | 电量/状态 | `/sys/class/power_supply/BAT*/*` |

**代码里每个采集函数的 docstring 都标了对应的 sysfs 路径。**

---

## 自测结果（v1.0.0）

在 Ubuntu 24.04 / i5-8265U / 8 线程 上实测：

### 功能

| 项目 | 结果 |
|---|---|
| CLI 全参数（`--version/--help/--banner/--json/--once`） | ✅ 全部 exit=0 |
| 错误参数 exit code | ✅ exit=2 |
| `--banner` UTF-8 中文 | ✅ |
| `--banner` LANG=C 英文回退 | ✅ 零中文字符 |
| `--once` CPU 采样 | ✅ 2.4%（非 0） |
| `--json` 字段完整性 | ✅ 15/15 无缺失 |
| `banner_enabled=false` 静默 | ✅ |
| 自定义阈值生效 | ✅ |
| TUI 40×15 / 80×24 / 150×45 | ✅ 零异常 |
| TUI 极端尺寸 20×10 / 250×70 | ✅ 零异常 |
| 运行中 resize（120×40→40×15→200×60） | ✅ 零异常 |
| install.sh / uninstall.sh | ✅ 装后可用，卸后干净 |
| 重复安装幂等（连装 3 次） | ✅ 钩子块始终 1 个 |

### 性能

| 指标 | 实测 | 目标 |
|---|---|---|
| CPU（pidstat 8 次采样） | **1.56%** | < 3% ✅ |
| CPU（/proc 25 秒精确算） | **1.56%** | < 3% ✅ |
| 内存 RSS | **21.8 MB** | — |
| 5 分钟内存变化 | 21888 → 21972 kB（**+84 KB**） | 无泄漏 ✅ |

### 安全

| 项目 | 结果 |
|---|---|
| `strace -e trace=network` 对外 connect | ✅ **0 次** |
| 网络库 import（socket/http/urllib/requests） | ✅ **无** |
| 监听端口 | ✅ **无** |
| 非 tty 跑 TUI | ✅ 明确报错 exit=2 |

> 注：代码里会创建 4 个 `AF_INET SOCK_DGRAM` socket 用于 `ioctl(SIOCGIFADDR)` 查本地网卡 IPv4 地址。
> **不 connect、不 bind、不发数据**。如果你要求连 socket 都不能有，可以改成解析 `/proc/net/fib_trie`，但格式解析更容易出 bug。

### 兼容性

| TERM | 结果 |
|---|---|
| xterm-256color / xterm / screen / linux | ✅ 正常 |
| dumb / unknown | ✅ 不崩溃（降级显示） |
| LANG=C | ✅ 回退英文 |

---
## 性能

```
CPU   pidstat 平均 1.62%    /proc 实测 1.00%   （目标 < 3%）
内存  VmRSS 22 MB   VmPeak 32 MB
```

**重命令缓存 30 秒** —— `smartctl`、`lastb` 不会每次刷新都调用。

**单进程，无常驻服务，无后台线程。**

---

## 兼容性

| 项目 | 要求 |
|---|---|
| Python | 3.8+（用了 `X | Y` 类型注解，3.10+ 更稳） |
| 依赖 | 仅标准库（`curses`） |
| 系统 | 任何有 `/proc` `/sys` 的 Linux |
| 测试过 | Ubuntu 24.04 / Debian 12 / Kali |
| 终端 | 任何支持 256 色或 16 色的终端 |

**没测过的**：CentOS/RHEL（应该可以）、Alpine（musl + ncurses 要装 `python3-curses`）、WSL（部分传感器不可用）

---

## 已知限制

**① 笔记本没有风扇传感器**

笔记本风扇由 EC 控制，内核不通过 hwmon 暴露 `fan*_input`。所以风扇区块在笔记本上是空的。

**② RAPL 需要 root**

`/sys/class/powercap/*/energy_uj` 是 `0400 root:root`。普通用户读不到。

**③ NVIDIA 显卡温度可能读不到**

如果用的是 nouveau 开源驱动，`/sys/class/hwmon/hwmonN/temp1_input` 可能返回空。装官方驱动 + `nvidia-smi` 才行（但本工具不依赖 `nvidia-smi`）。

**④ 只能看单机**

没有多机聚合、没有历史曲线、没有告警推送。要这些请用 netdata / glances。

---

## 故障排查

**登录看不到横幅**

```bash
bash -n ~/.bash_profile                # 检查语法
echo $PATH | grep local/bin            # 检查 PATH
serverdash --banner                    # 手动跑一次
```

**TUI 界面错位**

```bash
echo $TERM      # 应为 xterm-256color
echo $LANG      # 非 UTF-8 会回退英文
stty size       # 确认行列数
```

**退出后终端乱码**

```bash
reset           # 万一 curses 没恢复
```

（正常不需要，`curses.wrapper` 保证恢复。）

**root 下命令找不到**

```bash
/usr/local/bin/serverdash    # 用绝对路径
# 或重建链接
sudo ln -sfn "$HOME/.local/bin/serverdash" /usr/local/bin/serverdash
```

---

## 文件清单

```
serverdash.py     主程序（单文件，约 1900 行，零第三方依赖）
install.sh        安装（幂等，自动备份）
uninstall.sh      卸载（完整回滚）
get.sh            一键安装脚本（curl | bash）
README.md         本文档
LICENSE           MIT
```

---

## 类似工具

如果你要更多功能，看看这些：

| 工具 | 语言 | Stars | 差异 |
|---|---|---|---|
| [btop](https://github.com/aristocratos/btop) | C++ | 25k+ | 图形化 TUI、曲线图、鼠标 |
| [glances](https://github.com/nicolargo/glances) | Python | 28k+ | Web+TUI+API+告警 |
| [netdata](https://github.com/netdata/netdata) | C | 74k+ | 秒级采集、Web、多机 |
| [htop](https://github.com/htop-dev/htop) | C | 6k+ | 进程管理、树状视图 |

**serverdash 的定位**：不跟它们比功能全，只守住「**登录即见摘要**」这个位置，做到零依赖、只读、够快。

---

## 许可证

MIT — 随便用、改、商用，保留版权声明即可。
