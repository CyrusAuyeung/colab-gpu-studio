"""Local-only API/UI runner. Never loads production dotenv or credentials.

Usage: uv run python scripts/offline_server.py --port 18788
Credentials printed here are fake local demo credentials, never Google credentials.
"""

import argparse
import asyncio
import os
import secrets
import sys
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18788)
    parser.add_argument('--fail-first-start',action='store_true',help='Offline fault fixture: fail first attempt only')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024..65535")
    audit_path=os.environ.get('STUDIO_OFFLINE_AUDIT_PATH')
    root = Path(tempfile.mkdtemp(prefix="studio-mock-"))
    for key in list(os.environ):
        if key.startswith(
            ("GOOGLE_", "GPU_", "STUDIO_", "DATABASE_", "DATA_", "ENCRYPTION_", "SESSION_", "ATELIER_")
        ):
            os.environ.pop(key, None)
    os.environ.update(
        STUDIO_OFFLINE_TEST="1",
        GPU_REAL_ENABLED="0",
        GPU_STORAGE="local",
        DATABASE_URL="sqlite:///" + str(root / "mock.db"),
        DATA_DIR=str(root / "media"),
        STUDIO_PASSWORD="mock-local-only",
        SESSION_SECRET=secrets.token_urlsafe(48),
        ENVIRONMENT="offline-test",
        SITE_URL=f"http://127.0.0.1:{args.port}",
        STUDIO_OFFLINE_AUDIT=audit_path or str(
            Path(__file__).resolve().parents[1] / "docs/offline-evidence/application-network-audit.json"
        ),
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
    from cryptography.fernet import Fernet

    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    from app.offline import audit, install

    install()
    import uvicorn
    from app.gpu.scheduler import Scheduler
    from app.main import app
    from starlette.responses import JSONResponse

    original_lifespan = app.router.lifespan_context

    @app.middleware("http")
    async def offline_routes(request, call_next):
        path = request.url.path
        if any(
            segment in path
            for segment in (
                "/google/auth",
                "/google/connect",
                "/google/oauth",
                "/loras/upload",
                "/admin/compute/smoke",
            )
        ):
            return JSONResponse(
                {"error": {"code": "OFFLINE_ONLY", "message": "离线环境禁止真实授权与算力操作"}},
                status_code=403,
            )
        return await call_next(request)

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            st = application.state.store
            if not st.list("projects", "owner"):
                st.create("projects", "owner", {"title": "Mock / 演示项目"})

            async def tick():
                while True:
                    if args.fail_first_start:
                        from app.gpu.compute import MockGPUExecutor
                        scheduler=Scheduler(st,executor_factory=lambda job:MockGPUExecutor(fail_on_start=int(job.get('attempt_number') or 0)<=1))
                    else:
                        scheduler = Scheduler(st)
                    await asyncio.to_thread(scheduler.tick)
                    await asyncio.to_thread(scheduler.watchdog)
                    audit()
                    await asyncio.sleep(0.15)

            task = asyncio.create_task(tick())
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                audit()

    app.router.lifespan_context = lifespan
    print(
        f"MOCK ONLY: http://127.0.0.1:{args.port}; demo password: mock-local-only; isolated root: {root}",
        flush=True,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
