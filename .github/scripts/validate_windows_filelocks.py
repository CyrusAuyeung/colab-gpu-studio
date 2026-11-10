"""Bounded Windows process tests for a supplied Studio api directory; no GPU/network."""
import argparse
import contextlib
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

PARSER = argparse.ArgumentParser()
PARSER.add_argument("--api-root", required=True)
PARSER.add_argument("--report")
PARSER.add_argument("--child")
PARSER.add_argument("--kind", default="direct")
PARSER.add_argument("--work")
PARSER.add_argument("--prefix")
ARGS = PARSER.parse_args()
API = Path(ARGS.api_root).resolve()
sys.path.insert(0, str(API))
os.environ.update(GPU_REAL_ENABLED="0", GPU_STORAGE="local", GOOGLE_APPLICATION_CREDENTIALS="")
from app.filelocks import file_lock


@contextlib.contextmanager
def lock(work, kind, blocking):
    if kind == "direct":
        with (work / "shared.lock").open("a+b") as handle, file_lock(handle, blocking=blocking) as acquired:
            yield acquired
    else:
        from app.db import Store
        store = Store("sqlite:///" + str(work / "test.db"))
        context = {"dispatch": store.dispatch_lock, "admission": store._admission_try_lock,
                   "upload": lambda: store.upload_lock("synthetic-job")}[kind]
        with context() as acquired:
            yield acquired


def child():
    work, prefix = Path(ARGS.work), Path(ARGS.prefix)
    os.environ["DATA_DIR"] = str(work / "data")
    if ARGS.child == "artifact":
        from app.gpu.artifacts import LocalArtifactStore
        value = LocalArtifactStore(work / "artifacts").publish_file("synthetic-owner", "synthetic-execution", work / "source.png", "image/png")
        prefix.with_suffix(".result").write_text(json.dumps({"artifact_id": value["artifact_id"], "sha256": value["sha256"], "verified": value["upload_state"] == "verified"}))
        return
    if ARGS.child == "counter":
        for _ in range(20):
            with lock(work, "direct", True) as acquired:
                assert acquired
                counter = work / "counter.txt"
                value = int(counter.read_text())
                time.sleep(0.001)
                counter.write_text(str(value + 1))
        prefix.with_suffix(".result").write_text('{"ok":true}')
        return
    prefix.with_suffix(".attempt").touch()
    with lock(work, ARGS.kind, ARGS.child == "wait") as acquired:
        prefix.with_suffix(".result").write_text(json.dumps({"acquired": acquired}))
        if ARGS.child == "hold" and acquired:
            sys.stdin.buffer.readline()


class WindowsLocks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="studio-win-lock-")
        self.work = Path(self.temporary.name)
        self.processes = []

    def tearDown(self):
        for process, _ in self.processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        assert self.work.resolve().parent == Path(tempfile.gettempdir()).resolve()
        assert self.work.name.startswith("studio-win-lock-")
        self.temporary.cleanup()

    def spawn(self, action, kind="direct"):
        prefix = self.work / uuid.uuid4().hex
        env = {**os.environ, "DATA_DIR": str(self.work / "data")}
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--api-root", str(API),
                                    "--child", action, "--kind", kind, "--work", str(self.work), "--prefix", str(prefix)],
                                   cwd=self.work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.processes.append((process, prefix))
        return process, prefix

    def wait_file(self, process, path):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if path.exists() and path.stat().st_size:
                try:
                    return json.loads(path.read_text())
                except ValueError:
                    pass
            if process.poll() is not None:
                self.fail("Test child exited before result: " + process.stderr.read().decode(errors="replace")[-1500:])
            time.sleep(0.02)
        self.fail("Timed out waiting for test child")

    def result(self, child):
        process, prefix = child
        value = self.wait_file(process, prefix.with_suffix(".result"))
        return value

    def finish(self, child):
        process, _ = child
        process.wait(timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr.read().decode(errors="replace"))

    def release(self, holder):
        process, _ = holder
        process.stdin.close()
        process.stdin = None
        self.finish(holder)

    def exclusion(self, kind):
        if kind != "direct":
            from app.db import Store
            prepared = Store("sqlite:///" + str(self.work / "test.db"))
            prepared.engine.dispose()
        holder = self.spawn("hold", kind)
        self.assertTrue(self.result(holder)["acquired"])
        contender = self.spawn("try", kind)
        self.assertFalse(self.result(contender)["acquired"])
        self.finish(contender)
        self.release(holder)
        successor = self.spawn("try", kind)
        self.assertTrue(self.result(successor)["acquired"])
        self.finish(successor)

    def test_direct_exclusion_and_normal_release(self):
        self.exclusion("direct")

    def test_sqlite_dispatch_lock(self):
        self.exclusion("dispatch")

    def test_sqlite_admission_lock(self):
        self.exclusion("admission")

    def test_sqlite_upload_lock(self):
        self.exclusion("upload")

    def test_blocking_waiter_acquires_after_release(self):
        holder = self.spawn("hold")
        self.assertTrue(self.result(holder)["acquired"])
        waiter = self.spawn("wait")
        process, prefix = waiter
        deadline = time.monotonic() + 15
        while not prefix.with_suffix(".attempt").exists() and time.monotonic() < deadline:
            self.assertIsNone(process.poll())
            time.sleep(0.02)
        self.assertTrue(prefix.with_suffix(".attempt").exists())
        time.sleep(0.25)
        self.assertFalse(prefix.with_suffix(".result").exists())
        self.release(holder)
        self.assertTrue(self.result(waiter)["acquired"])
        self.finish(waiter)

    def test_forced_process_exit_releases_lock(self):
        holder = self.spawn("hold")
        self.assertTrue(self.result(holder)["acquired"])
        holder[0].kill()
        holder[0].wait(timeout=10)
        successor = self.spawn("try")
        self.assertTrue(self.result(successor)["acquired"])
        self.finish(successor)

    def test_six_processes_preserve_120_serialized_updates(self):
        (self.work / "counter.txt").write_text("0")
        children = [self.spawn("counter") for _ in range(6)]
        for item in children:
            self.finish(item)
        self.assertEqual(int((self.work / "counter.txt").read_text()), 120)

    def test_concurrent_artifact_publish_is_verified_and_idempotent(self):
        content = b"synthetic-lock-test" * 1024
        (self.work / "source.png").write_bytes(content)
        children = [self.spawn("artifact") for _ in range(6)]
        values = []
        for item in children:
            values.append(self.result(item))
            self.finish(item)
        self.assertEqual(len({item["artifact_id"] for item in values}), 1)
        self.assertTrue(all(item["verified"] and item["sha256"] == hashlib.sha256(content).hexdigest() for item in values))


if ARGS.child:
    child()
else:
    assert os.name == "nt", "Run this validation on Windows"
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(WindowsLocks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {"platform": platform.platform(), "python": platform.python_version(),
              "filelocks_sha256": hashlib.sha256((API / "app/filelocks.py").read_bytes()).hexdigest(),
              "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "passed": result.wasSuccessful(), "uses_network": False, "uses_gpu": False}
    if ARGS.report:
        Path(ARGS.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)
