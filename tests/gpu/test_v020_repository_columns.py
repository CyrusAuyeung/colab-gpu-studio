from app.db import Store
from app.gpu.repository import Repository
from sqlalchemy import select


def test_generic_writes_keep_instance_claim_columns_in_sync():
    repo = Repository(Store('sqlite:///:memory:'))
    row = repo.create('instances', 'owner', {'state': 'busy', 'current_job_id': 'j1', 'account_id': 'a1'})
    model = repo._model('instances')
    with repo.store.Session() as session:
        stored = session.scalar(select(model).where(model.id == row['id']))
        assert stored.current_job_id == 'j1'
        assert stored.account_id == 'a1'
    repo.update('instances', row['id'], {'current_job_id': None, 'account_id': 'a2'}, 'owner')
    with repo.store.Session() as session:
        stored = session.get(model, row['id'])
        assert stored.current_job_id is None
        assert stored.account_id == 'a2'
