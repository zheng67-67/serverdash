# serverdash 一键安装
#
# 用法（两种）：
#   curl -fsSL https://raw.githubusercontent.com/YOUR_NAME/serverdash/main/get.sh | bash
#   wget -qO- https://raw.githubusercontent.com/YOUR_NAME/serverdash/main/get.sh | bash
#
# 带参数：
#   curl -fsSL ... | bash -s -- --with-smart
#
# 环境变量：
#   SERVERDASH_REPO     ← 仓库地址，默认下方 REPO
#   SERVERDASH_BRANCH   ← 分支，默认 main

set -euo pipefail

REPO="${SERVERDASH_REPO:-https://github.com/YOUR_NAME/serverdash}"
BRANCH="${SERVERDASH_BRANCH:-main}"
TMPDIR_BASE="${TMPDIR:-/tmp}"
WORK="$(mktemp -d "${TMPDIR_BASE}/serverdash.XXXXXX")"

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT INT TERM

say()  { printf '  %s\n' "$*"; }
ok()   { printf '\033[38;5;114m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[38;5;214m  ! %s\033[0m\n' "$*"; }
err()  { printf '\033[38;5;203m  ✗ %s\033[0m\n' "$*"; }

echo "=============================================="
echo " serverdash 一键安装"
echo "=============================================="
echo "  仓库: $REPO"
echo "  分支: $BRANCH"
echo

# ---------- 前置检查 ----------
echo "[1/4] 环境检查"

command -v python3 >/dev/null 2>&1 || { err "缺 python3"; echo "  Ubuntu/Debian: sudo apt install python3"; exit 1; }
PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
ok "python3 $PYV"

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' || {
    err "Python 版本过低，需要 3.8+"; exit 1; }
ok "版本满足 (>=3.8)"

python3 -c 'import curses' 2>/dev/null || {
    err "缺 curses 模块"
    echo "  Ubuntu/Debian: sudo apt install python3-curses"
    echo "  Alpine:        apk add python3 ncurses"
    exit 1; }
ok "curses 可用"

command -v git >/dev/null 2>&1 || command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || {
    err "需要 git / curl / wget 之一"; exit 1; }
ok "下载工具就绪"

# ---------- 获取源码 ----------
echo
echo "[2/4] 下载源码"

fetch_ok=0

# 方式 1: git clone
if [ "$fetch_ok" = "0" ] && command -v git >/dev/null 2>&1; then
    say "尝试 git clone..."
    if git clone --depth 1 --branch "$BRANCH" "$REPO.git" "$WORK/src" >/dev/null 2>&1 \
       || git clone --depth 1 --branch "$BRANCH" "$REPO" "$WORK/src" >/dev/null 2>&1; then
        ok "git clone 成功"
        fetch_ok=1
    else
        warn "git clone 失败，尝试其他方式"
    fi
fi

# 方式 2: 下载 tarball
if [ "$fetch_ok" = "0" ]; then
    for tool in curl wget; do
        command -v "$tool" >/dev/null 2>&1 || continue
        say "尝试 $tool 下载 tarball..."
        URL="${REPO}/archive/refs/heads/${BRANCH}.tar.gz"
        TARBALL="$WORK/src.tar.gz"
        if [ "$tool" = "curl" ]; then
            curl -fsSL --connect-timeout 15 -o "$TARBALL" "$URL" 2>/dev/null || continue
        else
            wget -q --timeout=15 -O "$TARBALL" "$URL" 2>/dev/null || continue
        fi
        mkdir -p "$WORK/src"
        if tar xzf "$TARBALL" -C "$WORK/src" --strip-components=1 2>/dev/null; then
            ok "$tool 下载成功"
            fetch_ok=1
            break
        fi
    done
fi

[ "$fetch_ok" = "1" ] || {
    err "源码下载失败"
    echo
    echo "  手动安装："
    echo "    git clone $REPO"
    echo "    cd serverdash && bash install.sh"
    exit 1
}

[ -f "$WORK/src/serverdash.py" ] || { err "下载的包里没有 serverdash.py"; exit 1; }
[ -f "$WORK/src/install.sh" ] || { err "下载的包里没有 install.sh"; exit 1; }
ok "文件完整"

# ---------- 执行安装 ----------
echo
echo "[3/4] 安装"

cd "$WORK/src"
chmod +x install.sh 2>/dev/null || true

if [ "$(id -u)" = "0" ] && [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
    bash install.sh --user "$SUDO_USER" "$@"
elif [ "$(id -u)" = "0" ]; then
    bash install.sh "$@"
else
    bash install.sh "$@"
fi

# ---------- 验证 ----------
echo
echo "[4/4] 验证"

FOUND=0
for p in "$HOME/.local/bin/serverdash" "/usr/local/bin/serverdash"; do
    if [ -x "$p" ]; then
        ok "已安装: $p"
        "$p" --version 2>/dev/null | sed 's/^/    /'
        FOUND=1
        break
    fi
done

if [ "$FOUND" = "0" ]; then
    warn "未在预期位置找到，请检查 install.sh 输出"
fi

echo
echo "=============================================="
echo " 安装完成"
echo "=============================================="
echo
echo "  立即体验:"
echo "    serverdash            # 全屏 TUI"
echo "    serverdash --banner   # 摘要横幅"
echo
echo "  下次 SSH 登录会自动显示横幅。"
echo "  卸载: bash <安装目录>/uninstall.sh"
echo
