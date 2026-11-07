from app.db import Store
from app.gpu.repository import Repository
from app.gpu.workbench import WorkbenchService


def test_describe_reads_assets_from_application_store(tmp_path):
    repo = Repository(Store(f'sqlite:///{tmp_path / "wb.db"}'))
    result = WorkbenchService(repo).describe('owner', {
        'id': 'instance', 'owner_id': 'owner', 'state': 'ready', 'mock': True,
    })
    assert result['artifacts'] == []
    assert 'queue_reviewed_workflow' in result['capabilities']
