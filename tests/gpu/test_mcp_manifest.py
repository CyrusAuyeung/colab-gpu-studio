import json
import uuid
from pathlib import Path

from app.gpu.mcp import build_poll_code


def test_poll_probe_prefers_manifest_without_reading_media(monkeypatch,tmp_path,capsys):
    execution=str(uuid.uuid4())
    root=tmp_path/execution
    root.mkdir()
    (root/'status.json').write_text(json.dumps({'state':'completed'}))
    artifacts=[{'artifact_id':'a'*32,'filename':'mock.mp4','size_bytes':500*1024**2,
                'sha256':'b'*64,'mime':'video/mp4','upload_state':'verified',
                'storage':{'provider':'mock','object_key':'a'*32}}]
    (root/'meta.json').write_text(json.dumps({'artifacts':artifacts,'outputs':[{'path':'forbidden.mp4'}]}))
    code=build_poll_code(execution).replace('/content/ai-studio',str(tmp_path))
    def forbid(*args,**kwargs):
        raise AssertionError('manifest probe must not read media bytes')
    monkeypatch.setattr(Path,'read_bytes',forbid)
    exec(compile(code,'<fixed-offline-poll>','exec'),{})
    result=json.loads(capsys.readouterr().out)
    assert result['artifacts']==artifacts
    assert result['execution_id']==execution
    assert not result.get('outputs')
