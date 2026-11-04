#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

echo "========================================================"
echo "  Colab GPU Studio — Android Termux 一键安装与启动脚本"
echo "========================================================"

if ! command -v pkg >/dev/null 2>&1; then
  echo "提示：未检测到 Termux pkg 命令，将尝试使用当前系统 Python 环境运行。"
else
  echo "[1/4] 正在检查并安装 Termux 依赖包 (python, rust, clang, libjpeg-turbo, zlib)..."
  pkg update -y
  pkg install -y python rust clang make pkg-config libjpeg-turbo zlib libffi openssl git
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  echo "[2/4] 正在创建本地 Python 虚拟环境..."
  python3 -m venv .venv
fi

echo "[3/4] 正在安装 Python 依赖..."
# Termux uses SQLite by default; strip psycopg binary requirement if present so no libpq is needed
grep -v '^psycopg' requirements.lock > .requirements-termux.txt
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r .requirements-termux.txt
rm -f .requirements-termux.txt

echo "[4/4] 启动 Colab GPU Studio..."
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock || true
  echo "已开启 Termux WakeLock 防止后台休眠。"
fi

exec ./.venv/bin/python -m app.launcher --host 127.0.0.1 --port 8788
