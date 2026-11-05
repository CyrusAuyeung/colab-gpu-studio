"""Local process death must not leave a persisted worker forever running."""
import contextlib
import io
import json
import os
import subprocess
import sys
import uuid

import pytest
from app.gpu.mcp import build_poll_code


def test_killed_worker_is_reported_failed_without_restarting_or_deleting_output(tmp_path):
    execution=str(uuid.uuid4())
    root=tmp_path/execution
    root.mkdir()
    output=root/'partial.bin'
    output.write_bytes(b'preserved partial evidence')
    # Benign local CPU process: no GPU, credentials, or cloud operations.
    process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
    try:
        (root/'marker.json').write_text(json.dumps({'execution_id':execution,'pid':process.pid}))
        (root/'status.json').write_text(json.dumps({'state':'running','stage':'sampling'}))
        process.kill()
        process.wait(timeout=5)
        capture=io.StringIO()
        code=build_poll_code(execution).replace('/content/ai-studio',str(tmp_path))
        with contextlib.redirect_stdout(capture):
            exec(compile(code,'trusted-process-probe','exec'),{})
        result=json.loads(capture.getvalue())
        assert result['state']=='failed'
        assert result['error_code']=='gpu_worker_exited'
        assert output.read_bytes()==b'preserved partial evidence'
        assert json.loads((root/'status.json').read_text())['state']=='running'
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize('state', ['running', 'completed'])
def test_alive_worker_or_completed_status_is_not_misclassified(tmp_path,state):
    execution=str(uuid.uuid4())
    root=tmp_path/execution
    root.mkdir()
    (root/'marker.json').write_text(json.dumps({'execution_id':execution,'pid':os.getpid()}))
    (root/'status.json').write_text(json.dumps({'state':state}))
    output=io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(build_poll_code(execution).replace('/content/ai-studio',str(tmp_path)),'probe','exec'),{})
    assert json.loads(output.getvalue())['state']==state
