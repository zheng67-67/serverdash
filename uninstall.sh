#!/bin/bash
# serverdash 卸载脚本（完整回滚）
#
# 用法:
#   bash uninstall.sh                 卸载当前用户的
#   sudo bash uninstall.sh --user alice   以 root 卸载指定用户的
#   sudo bash uninstall.sh --system   卸载 /usr/local/bin 全局版

set -uo pipefail

APP=serverdash
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WITH_SMART_CLEAN=1
TARGET_USER=""
SYSTEM_MODE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --user) shift; TARGET_USER="${1:-}" ;;
        --system) SYSTEM_MODE=1 ;;
        --keep-config) WITH_SMART_CLEAN=0 ;;
        -h|--help)
            echo "用法: $0 [--user NAME] [--system] [--keep-config]"
            exit 0 ;;
    esac
    shift
done

say() { printf '  %s\n' "$*"; }
ok()  { printf '\033[38;5;114m  ✓ %s\033[0m\n' "$*"; }
warn(){ printf '\033[38;5;214m  ! %s\033[0m\n' "$*"; }

CUR_UID=$(id -u)
if [ -n "$TARGET_USER" ]; then
    DEST_USER="$TARGET_USER"
elif [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    DEST_USER="$SUDO_USER"
else
    DEST_USER="$(id -un)"
fi

DEST_HOME=$(getent passwd "$DEST_USER" 2>/dev/null | cut -d: -f6)
[ -z "$DEST_HOME" ] && { echo "用户 $DEST_USER 不存在"; exit 1; }

echo "=============================================="
echo " ${APP} 卸载"
echo "=============================================="
echo "  目标用户: ${DEST_USER}"
echo "  HOME:     ${DEST_HOME}"
echo

MARK_BEGIN="# >>> serverdash >>>"
MARK_END="# <<< serverdash <<<"
PROFILE="${DEST_HOME}/.bash_profile"
SUDOERS=/etc/sudoers.d/serverdash-smart
GLOBAL_LINK=/usr/local/bin/${APP}

# ---------- 1. 主程序 ----------
echo "[1/5] 删除主程序"
for b in "${DEST_HOME}/.local/bin/${APP}" "/usr/local/bin/${APP}"; do
    if [ -f "$b" ] || [ -L "$b" ]; then
        rm -f "$b" 2>/dev/null || sudo rm -f "$b" 2>/dev/null
        [ -e "$b" ] && warn "无法删除 $b（需 root）" || ok "已删除 $b"
    fi
done

# ---------- 2. 全局链接 ----------
echo
echo "[2/5] 全局符号链接"
if [ -L "$GLOBAL_LINK" ]; then
    # 只删指向本项目的链接，别误删别的
    TARGET=$(readlink "$GLOBAL_LINK")
    case "$TARGET" in
        *"${APP}"*) rm -f "$GLOBAL_LINK" 2>/dev/null || sudo rm -f "$GLOBAL_LINK" 2>/dev/null
                    ok "已删除 $GLOBAL_LINK（指向 $TARGET）" ;;
        *)          warn "$GLOBAL_LINK 指向 $TARGET，不是本项目，保留" ;;
    esac
else
    say "不存在，跳过"
fi

# ---------- 3. 登录钩子 ----------
echo
echo "[3/5] 移除登录钩子"
if [ -f "$PROFILE" ] && grep -qF "$MARK_BEGIN" "$PROFILE"; then
    cp -a "$PROFILE" "${DEST_HOME}/.local/share/${APP}/backups/bash_profile.before-uninstall.$(date +%Y%m%d-%H%M%S)" 2>/dev/null
    awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
        $0 == b {skip=1; next}
        $0 == e {skip=0; next}
        !skip {print}
    ' "$PROFILE" > "${PROFILE}.tmp" && mv "${PROFILE}.tmp" "$PROFILE"
    # 如果只剩 "source .profile" 那段，且这个文件原本不存在，就整个删掉
    REMAIN=$(grep -vE '^\s*$|^#|source \.profile|^if \[ -f "\$HOME/\.profile" \]|^\s*\.\s+"\$HOME/\.profile"|^fi$' "$PROFILE" 2>/dev/null | wc -l)
    if [ "$REMAIN" -eq 0 ]; then
        rm -f "$PROFILE"
        ok "已移除钩子，且 .bash_profile 只剩空壳 → 已删除该文件"
    else
        ok "已移除钩子（保留其他 $REMAIN 行内容）"
    fi
else
    say "未发现钩子，跳过"
fi

# ---------- 4. sudoers ----------
echo
echo "[4/5] 移除 sudoers 规则"
if [ -f "$SUDOERS" ]; then
    rm -f "$SUDOERS" 2>/dev/null || sudo rm -f "$SUDOERS" 2>/dev/null
    [ -e "$SUDOERS" ] && warn "删除失败，请手动: sudo rm -f $SUDOERS" || ok "已删除 $SUDOERS"
else
    say "不存在，跳过"
fi

# ---------- 5. 配置 ----------
echo
echo "[5/5] 配置与数据"
if [ "$WITH_SMART_CLEAN" = "1" ]; then
    for d in "${DEST_HOME}/.config/${APP}" "${DEST_HOME}/.local/share/${APP}"; do
        [ -d "$d" ] || continue
        printf '  删除 %s ? [y/N] ' "$d"
        read -r ans
        case "${ans:-N}" in
            y|Y) rm -rf "$d" 2>/dev/null || sudo rm -rf "$d" 2>/dev/null
                 ok "已删除 $d" ;;
            *)   say "保留 $d" ;;
        esac
    done
else
    say "保留（--keep-config）"
fi

# ---------- 验证 ----------
echo
echo "[验证]"
if command -v "$APP" >/dev/null 2>&1; then
    warn "命令仍可执行: $(command -v $APP)"
else
    ok "命令已不可用"
fi
[ -f "$PROFILE" ] && grep -qF "$MARK_BEGIN" "$PROFILE" && warn "钩子仍存在" || ok "钩子已清除"

echo
echo "=============================================="
echo " 卸载完成"
echo "=============================================="
