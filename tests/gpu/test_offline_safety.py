"""Run safety probes in child interpreters so permanent guards cannot leak into pytest."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def probe(tmp_path, code):
    (tmp_path / ".env").write_text(
        "STUDIO_PASSWORD=dotenv-must-not-load\nDATABASE_URL=sqlite:///production.db\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("GOOGLE_", "GPU_", "DATABASE_", "STUDIO_", "DATA_", "SESSION_", "ENCRYPTION_"))
    }
    env.update(
        PYTHONPATH=str(ROOT / "api"),
        STUDIO_OFFLINE_TEST="1",
        GPU_REAL_ENABLED="0",
        STUDIO_OFFLINE_AUDIT=str(tmp_path / "audit.json"),
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(result.stdout)


def test_offline_ignores_dotenv_and_forces_real_provider_off(tmp_path):
    result = probe(
        tmp_path,
        """
import json,os
from app.config import settings
from app.gpu.compute import GpuConfig
assert os.environ['GPU_REAL_ENABLED']=='0'
print(json.dumps({'password':settings.studio_password,'database':settings.database_url,
                  'real':GpuConfig.from_env().real_enabled,'storage':os.getenv('GPU_STORAGE')}))
""",
    )
    assert result["password"] == ""
    assert result["database"] != "sqlite:///production.db"
    assert result["real"] is False
    assert result["storage"] == "local"


def test_default_transport_is_blocked_and_mock_transport_still_works(tmp_path):
    result = probe(
        tmp_path,
        """
import json,httpx,socket
from app.config import settings
from app.offline import COUNTS,ATTEMPTS
with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'mock':True}))) as client:
    assert client.get('https://colaboratory.googleapis.com/fake').json()['mock']
for url in ['https://colaboratory.googleapis.com/v1beta/runtimes','https://oauth2.googleapis.com/token']:
    try:
        httpx.post(url,json={})
    except RuntimeError as exc:
        assert 'OFFLINE_OUTBOUND_DENIED' in str(exc)
    else:
        raise AssertionError('real transport not blocked')
with socket.socket() as sock:
    try:
        sock.connect_ex(('203.0.113.1',443))
    except RuntimeError:
        pass
    else:
        raise AssertionError('socket not blocked')
print(json.dumps({'counts':COUNTS,'attempts':len(ATTEMPTS),'stacks':all(a['stack'] for a in ATTEMPTS)}))
""",
    )
    assert result["attempts"] == 3
    assert result["stacks"]
    assert result["counts"]["real_runtime_create_count"] == 1
    assert result["counts"]["real_colab_oauth_call_count"] == 1
