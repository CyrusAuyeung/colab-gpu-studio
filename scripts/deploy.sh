#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  printf '%s\n' '请先执行 uv run python scripts/init_env.py --site-url https://你的域名'
  exit 1
fi
if [ ! -f docker-compose.yml ]; then
  cp docker-compose.example.yml docker-compose.yml
  printf '%s\n' '已从 docker-compose.example.yml 复制出 docker-compose.yml，按需修改端口/挂载后再跑'
fi
docker compose config -q
docker compose build
docker compose up -d --wait --wait-timeout 180
docker compose ps
