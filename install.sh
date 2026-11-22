#!/bin/bash
# serverdash 安装脚本（幂等，可重复执行）
#
# 用法:
#   bash install.sh                      装到当前用户 ~/.local/bin
#   bash install.sh --with-smart         同时配 smartctl 免密
#   bash install.sh --system             装到 /usr/local/bin（所有用户可用）
#   sudo bash install.sh --user alice    以 root 运行但装给指定用户
#
# 会改动的地方（全部先备份）:
#   ~/.local/bin/serverdash                 主程序（或 --system 时 /usr/local/bin）
#   ~/.config/serverdash/config.toml        配置（仅当不存在时创建）
#   ~/.bash_profile                         登录钩子（带标记，幂等）
#   /etc/sudoers.d/serverdash-smart         smartctl 免密（--with-smart）
#   /usr/local/bin/serverdash               全局符号链接（--system）

set -uo pipefail

APP=serverdash
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_PY="${SRC_DIR}/serverdash.py"

WITH_SMART=0
NO_HOOK=0
SYSTEM_INSTALL=0
TARGET_USER=""

while [ $# -gt 0 ]; do
    case "$1" in
        --with-smart) WITH_SMART=1 ;;
        --no-hook)    NO_HOOK=1 ;;
        --system)     SYSTEM_INSTALL=1 ;;
        --user)       shift; TARGET_USER="${1:-}" ;;
        -h|--help)
            cat <<EOF
用法: $0 [选项]

  --with-smart     配置 smartctl 免密（/etc/sudoers.d）
  --system         装到 /usr/local/bin，所有用户都能用
  --user NAME      以 root 运行时，指定装给哪个用户
  --no-hook        只装程序，不动登录钩子
  -h, --help       显示本帮助
EOF
            exit 0 ;;
        *) echo "未知参数: $1"; exit 2 ;;
    esac
    shift
done

say() { printf '  %s\n' "$*"; }
ok()  { printf '\033[38;5;114m  ✓ %s\033[0m\n' "$*"; }
warn(){ printf '\033[38;5;214m  ! %s\033[0m\n' "$*"; }
err() { printf '\033[38;5;203m  ✗ %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------
# 关键：解析目标用户与 HOME
#
# 踩过的坑：
#   sudo bash install.sh 时，$HOME 被 sudo 重置成 /root，
#   $USER 变成 root。结果程序装到 /root/.local/bin，
#   而目标用户根本用不了。
#
# 解决：优先用 --user，其次 SUDO_USER，最后才是当前用户。
# ---------------------------------------------------------------
CUR_UID=$(id -u)
if [ -n "$TARGET_USER" ]; then
    DEST_USER="$TARGET_USER"
elif [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    DEST_USER="$SUDO_USER"
elif [ "$CUR_UID" = "0" ]; then
    err "当前以 root 运行，但没指定 --user"
    say "请二选一："
    say "  1) sudo bash $0 --user alice --with-smart   （装给指定用户）"
    say "  2) sudo bash $0 --system --with-smart       （装到 /usr/local，所有用户可用）"
    exit 1
else
    DEST_USER="$(id -un)"
fi

DEST_HOME=$(getent passwd "$DEST_USER" 2>/dev/null | cut -d: -f6)
[ -z "$DEST_HOME" ] && { err "用户 $DEST_USER 不存在"; exit 1; }

if [ "$SYSTEM_INSTALL" = "1" ]; then
    BIN_DIR="/usr/local/bin"
    BIN="${BIN_DIR}/${APP}"
else
    BIN_DIR="${DEST_HOME}/.local/bin"
    BIN="${BIN_DIR}/${APP}"
fi

echo "=============================================="
echo " ${APP} 安装"
echo "=============================================="
echo "  目标用户: ${DEST_USER}"
echo "  HOME:     ${DEST_HOME}"
echo "  安装位置: ${BIN}"
echo

# ---------- 0. 前置检查 ----------
echo "[0/6] 前置检查"

[ -f "$SRC_PY" ] || { err "找不到 $SRC_PY"; exit 1; }
ok "主程序: $SRC_PY"

command -v python3 >/dev/null || { err "缺 python3"; exit 1; }
ok "python3 $(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"

python3 -c 'import curses' 2>/dev/null || { err "缺 curses: apt install python3-curses"; exit 1; }
ok "curses 可用"

# ---------- 1. 安装主程序 ----------
echo
echo "[1/6] 安装主程序"

if [ "$SYSTEM_INSTALL" = "1" ] && [ "$CUR_UID" != "0" ]; then
    err "--system 需要 root 权限"
    say "请用: sudo bash $0 --system"
    exit 1
fi

BACKUP_DIR="${DEST_HOME}/.local/share/${APP}/backups"
if [ "$SYSTEM_INSTALL" = "1" ]; then
    mkdir -p "$BIN_DIR"
    if [ -f "$BIN" ]; then
        mkdir -p "$BACKUP_DIR"
        cp -a "$BIN" "${BACKUP_DIR}/${APP}.$(date +%Y%m%d-%H%M%S)" 2>/dev/null
        ok "已备份旧版本"
    fi
    install -m 0755 "$SRC_PY" "$BIN"
else
    mkdir -p "$BIN_DIR"
    if [ -f "$BIN" ]; then
        mkdir -p "$BACKUP_DIR"
        cp -a "$BIN" "${BACKUP_DIR}/${APP}.$(date +%Y%m%d-%H%M%S)" 2>/dev/null
        ok "已备份旧版本"
    fi
    install -m 0755 "$SRC_PY" "$BIN"
    chown "${DEST_USER}:" "$BIN" 2>/dev/null || true
fi
ok "安装到 $BIN"

# ---------- 2. 全局命令 ----------
echo
echo "[2/6] 全局命令"

GLOBAL_LINK=/usr/local/bin/${APP}
if [ "$SYSTEM_INSTALL" = "1" ]; then
    ok "已在 /usr/local/bin（所有用户可用）"
elif [ -L "$GLOBAL_LINK" ] || [ -f "$GLOBAL_LINK" ]; then
    ok "$GLOBAL_LINK 已存在"
elif [ "$CUR_UID" = "0" ]; then
    ln -sfn "$BIN" "$GLOBAL_LINK"
    ok "已建符号链接 $GLOBAL_LINK -> $BIN"
else
    warn "未建全局链接（普通用户无权限）"
    say "  想 root 下也能用，执行:"
    say "    sudo ln -sfn $BIN $GLOBAL_LINK"
    say "  或重装: sudo bash $0 --user $DEST_USER --system"
fi

# ---------- 3. 配置文件 ----------
echo
echo "[3/6] 配置文件"

CONF_DIR="${DEST_HOME}/.config/${APP}"
CONF="${CONF_DIR}/config.toml"
mkdir -p "$CONF_DIR"
if [ -f "$CONF" ]; then
    ok "配置已存在，保持不动"
else
    if [ "$CUR_UID" = "0" ] && [ "$DEST_USER" != "root" ]; then
        sudo -u "$DEST_USER" -H "$BIN" --init >/dev/null 2>&1 || "$BIN" --init >/dev/null 2>&1
    else
        "$BIN" --init >/dev/null 2>&1
    fi
    [ -f "$CONF" ] && ok "已生成 $CONF" || warn "配置生成失败，用内置默认值"
fi
chown -R "${DEST_USER}:" "$CONF_DIR" 2>/dev/null || true

# ---------- 4. 登录钩子 ----------
echo
echo "[4/6] 登录钩子"

PROFILE="${DEST_HOME}/.bash_profile"
MARK_BEGIN="# >>> serverdash >>>"
MARK_END="# <<< serverdash <<<"

if [ "$NO_HOOK" = "1" ]; then
    warn "跳过（--no-hook）"
else
    # 幂等：先删旧块
    if [ -f "$PROFILE" ] && grep -qF "$MARK_BEGIN" "$PROFILE"; then
        cp -a "$PROFILE" "${BACKUP_DIR}/bash_profile.$(date +%Y%m%d-%H%M%S)" 2>/dev/null
        awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
            $0 == b {skip=1; next}
            $0 == e {skip=0; next}
            !skip {print}
        ' "$PROFILE" > "${PROFILE}.tmp" && mv "${PROFILE}.tmp" "$PROFILE"
        ok "已移除旧钩子块"
    elif [ -f "$PROFILE" ]; then
        mkdir -p "$BACKUP_DIR"
        cp -a "$PROFILE" "${BACKUP_DIR}/bash_profile.$(date +%Y%m%d-%H%M%S)" 2>/dev/null
        ok "已备份 .bash_profile"
    fi

    touch "$PROFILE"

    # 关键：存在 .bash_profile 后 bash 不再读 .profile，
    # 而 Ubuntu 把 PATH="$HOME/.local/bin:$PATH" 写在 .profile 里。
    if [ -f "${DEST_HOME}/.profile" ] && ! grep -qF '.profile' "$PROFILE"; then
        cat >> "$PROFILE" <<'PROFILE_HOOK'
# 让 ~/.profile 继续生效（.bash_profile 存在时 bash 默认跳过 .profile）
if [ -f "$HOME/.profile" ]; then
    . "$HOME/.profile"
fi
PROFILE_HOOK
        ok "已 source .profile（保住 PATH）"
    fi

    cat >> "$PROFILE" <<'HOOK'

# >>> serverdash >>>
# 仅在「交互式 + 真 tty + 非 scp/sftp/管道」时显示横幅
if [ -z "${SERVERDASH_SKIP:-}" ] \
   && [ -t 0 ] && [ -t 1 ] \
   && [ -n "${PS1:-}" ] \
   && case "$-" in *i*) true;; *) false;; esac \
   && [ -z "${_SERVERDASH_SHOWN:-}" ] \
   && command -v serverdash >/dev/null 2>&1; then
    export _SERVERDASH_SHOWN=1
    serverdash --banner 2>/dev/null || true
fi
# <<< serverdash <<<
HOOK

    chown "${DEST_USER}:" "$PROFILE" 2>/dev/null || true
    chmod 644 "$PROFILE"
    ok "钩子已写入 $PROFILE"
fi

# ---------- 5. smartctl ----------
echo
echo "[5/6] smartctl 权限"

if [ "$WITH_SMART" = "0" ]; then
    warn "跳过（未加 --with-smart）"
    say "  需要时: sudo bash $0 --user $DEST_USER --with-smart"
elif ! command -v smartctl >/dev/null 2>&1; then
    warn "smartctl 未安装"
    say "  安装: sudo apt install smartmontools"
elif [ "$CUR_UID" != "0" ]; then
    err "配 sudoers 需要 root"
    say "  请用: sudo bash $0 --user $DEST_USER --with-smart"
else
    SUDOERS=/etc/sudoers.d/serverdash-smart
    SMARTCTL=$(command -v smartctl)
    TMPF=$(mktemp)
    cat > "$TMPF" <<EOF
# serverdash: 允许 ${DEST_USER} 免密读取 SMART
# 由 install.sh 生成，卸载用 uninstall.sh
${DEST_USER} ALL=(root) NOPASSWD: ${SMARTCTL}
EOF
    if visudo -c -q -f "$TMPF" >/dev/null 2>&1; then
        install -m 0440 -o root -g root "$TMPF" "$SUDOERS"
        ok "已写入 $SUDOERS"
    else
        err "sudoers 语法校验失败"
    fi
    rm -f "$TMPF"
fi

# ---------- 6. 自检 ----------
echo
echo "[6/6] 自检"

RUNAS=""
if [ "$CUR_UID" = "0" ] && [ "$DEST_USER" != "root" ]; then
    RUNAS="sudo -u $DEST_USER -H"
fi

$RUNAS "$BIN" --banner --no-color >/dev/null 2>&1 && ok "横幅正常" || err "横幅失败"
$RUNAS "$BIN" --json >/dev/null 2>&1 && ok "JSON 正常" || err "JSON 失败"

echo
echo "=============================================="
echo " 安装完成"
echo "=============================================="
echo
echo "  用法:"
echo "    serverdash              # 全屏 TUI"
echo "    serverdash --banner     # 横幅"
echo "    serverdash --help       # 帮助"
echo
echo "  root 下使用:"
if [ -L "$GLOBAL_LINK" ]; then
    echo "    sudo serverdash         # 全局链接已就绪，root 可直接用"
    echo "    （可读 RAPL 功耗、完整 SMART）"
else
    echo "    sudo $BIN               # 用绝对路径，或先建全局链接"
fi
echo
echo "  卸载: bash ${SRC_DIR}/uninstall.sh"
echo
