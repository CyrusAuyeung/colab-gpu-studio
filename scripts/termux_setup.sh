#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

echo "========================================================"
echo "  Colab GPU Studio — Android Termux 一键安装与启动脚本"
echo "========================================================"

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Android shared storage (/storage/emulated/0 or /sdcard) blocks symlinks and exec permissions.
# Automatically relocate/sync into Termux $HOME/colab-gpu-studio when run from shared storage.
case "$SRC_DIR" in
  /storage/*|/sdcard/*)
    TARGET_DIR="$HOME/colab-gpu-studio"
    echo "[提示] 检测到当前目录位于安卓共享存储 ($SRC_DIR)，不支持软链接与可执行权限。"
    echo "       正在自动同步到 Termux 内部目录: $TARGET_DIR ..."
    mkdir -p "$TARGET_DIR"
    cp -rf "$SRC_DIR"/* "$TARGET_DIR"/
    if [ -f "$SRC_DIR/.env.example" ]; then
      cp -f "$SRC_DIR/.env.example" "$TARGET_DIR/.env.example"
    fi
    if [ -f "$SRC_DIR/.env" ] && [ ! -f "$TARGET_DIR/.env" ]; then
      cp -f "$SRC_DIR/.env" "$TARGET_DIR/.env"
    fi
    ROOT_DIR="$TARGET_DIR"
    ;;
  *)
    ROOT_DIR="$SRC_DIR"
    ;;
esac

cd "$ROOT_DIR"

if ! command -v pkg >/dev/null 2>&1; then
  echo "提示：未检测到 Termux pkg 命令，将尝试使用当前系统 Python 环境运行。"
else
  echo "[1/4] 正在安装 Termux 预编译扩展包 (python-cryptography, python-pillow) 与编译工具..."
  pkg update -y
  pkg install -y python python-pip python-cryptography python-pillow rust clang make pkg-config libffi openssl git
fi

if [ ! -x ".venv/bin/python" ] || ! grep -q "include-system-site-packages = true" ".venv/pyvenv.cfg" 2>/dev/null; then
  rm -rf .venv
  echo "[2/4] 正在创建本地 Python 虚拟环境（复用 Termux 预编译扩展）..."
  python3 -m venv --system-site-packages .venv
fi

echo "[3/4] 正在安装 Python 依赖（开启 --no-build-isolation 免除重复构建隔离环境）..."
./.venv/bin/pip install --upgrade pip setuptools wheel cffi maturin typing-extensions
grep -Ev '^(psycopg|cryptography==|pillow==)' requirements.lock > .requirements-termux.txt
./.venv/bin/pip install --no-build-isolation -r .requirements-termux.txt
rm -f .requirements-termux.txt

echo "[4/4] 启动 Colab GPU Studio..."
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock || true
  echo "已开启 Termux WakeLock 防止后台休眠。"
fi

exec ./.venv/bin/python -m app.launcher --host 127.0.0.1 --port 8788
