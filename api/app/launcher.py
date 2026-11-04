"""All-in-one standalone launcher for Colab GPU Studio.

Starts the FastAPI web server, background task worker, and timeout watchdog
together in one command. Automatically generates `.env` on first launch.
"""

from __future__ import annotations

import argparse
import os
import secrets
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


def _ensure_env(root: Path, host: str, port: int, site_url: str | None) -> dict[str, str]:
    env_path = root / ".env"
    if not env_path.exists():
        from cryptography.fernet import Fernet

        resolved_url = (site_url or f"http://127.0.0.1:{port}").rstrip("/")
        password = secrets.token_urlsafe(18)
        values = {
            "ENVIRONMENT": "production",
            "SITE_URL": resolved_url,
            "DATABASE_URL": "sqlite:///./data/studio.db",
            "DATA_DIR": "./data",
            "STUDIO_PASSWORD": password,
            "ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "SESSION_SECRET": secrets.token_urlsafe(48),
            "STUDIO_API_TOKEN": secrets.token_urlsafe(40),
            "GPU_AGENT_TOKEN": secrets.token_urlsafe(40),
            "GPU_AGENT_OWNER_ID": "owner",
            "GPU_REAL_ENABLED": "0",
            "GPU_STORAGE": "local",
            "GPU_MAX_RUNTIME_MINUTES": "30",
            "GPU_DAILY_UNITS": "10",
            "GPU_MONTHLY_UNITS": "100",
            "GPU_MAX_JOB_UNITS": "5",
            "GPU_UNIT_RATE": "",
            "GPU_GOOGLE_CLIENT_ID": "",
            "GPU_GOOGLE_CLIENT_SECRET": "",
            "GPU_GOOGLE_REDIRECT_URI": "",
            "BIND_IP": host,
            "BIND_PORT": str(port),
        }
        fd = os.open(env_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
        print("=" * 64)
        print("  首次启动：已自动生成配置文件 .env")
        print(f"  初始登录密码 (STUDIO_PASSWORD): {password}")
        print("  （密码保存在当前目录 .env 文件中，可随时查看或修改）")
        print("=" * 64)

    config: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        config[k.strip()] = v.strip()
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description="Colab GPU Studio 一键启动器")
    parser.add_argument("--host", default=os.getenv("BIND_IP", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("BIND_PORT", "8788")))
    parser.add_argument("--site-url", default=None)
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--self-test", action="store_true", help="启动自检通过后自动退出（用于打包验收）")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    os.chdir(root)
    (root / "data").mkdir(parents=True, exist_ok=True)
    config = _ensure_env(root, args.host, args.port, args.site_url)

    child_env = os.environ.copy()
    child_env.update(config)
    site_pkgs = root / "runtime" / "site-packages"
    if site_pkgs.is_dir():
        child_env["PYTHONPATH"] = f"{root / 'api'}{os.pathsep}{site_pkgs}"
    else:
        child_env["PYTHONPATH"] = str(root / "api")
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"

    py = sys.executable
    cmds = [
        [py, "-m", "uvicorn", "app.main:app", "--host", args.host, "--port", str(args.port), "--no-access-log"],
        [py, "-m", "app.worker", "worker"],
        [py, "-m", "app.worker", "watchdog", "--interval", "5"],
    ]

    procs: list[subprocess.Popen] = []

    def _shutdown(*_a):
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    for cmd in cmds:
        procs.append(subprocess.Popen(cmd, cwd=root, env=child_env))

    url = f"http://127.0.0.1:{args.port}" if args.host in {"0.0.0.0", "127.0.0.1", "localhost"} else f"http://{args.host}:{args.port}"
    print(f"正在启动 Colab GPU Studio ({url}) ...")

    import urllib.request

    ready = False
    for _ in range(40):
        if any(p.poll() is not None for p in procs):
            _shutdown()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/api/health", timeout=2) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(0.5)

    if not ready:
        print("启动超时，请检查端口是否被占用。", file=sys.stderr)
        _shutdown()

    print(f"Colab GPU Studio 已就绪：{url}")
    print("按 Ctrl+C 可停止全部服务。")

    if args.self_test:
        print("SELF_TEST_OK")
        _shutdown()

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    while True:
        for p in procs:
            if p.poll() is not None:
                _shutdown()
        time.sleep(1)


if __name__ == "__main__":
    main()
