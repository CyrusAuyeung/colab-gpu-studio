import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_additive_migration_preserves_old_rows_and_replays(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'old.db'))
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE legacy_marker (id TEXT PRIMARY KEY, payload TEXT)'))
        connection.execute(text("INSERT INTO legacy_marker VALUES ('keep','unchanged')"))
        source = Path(__file__).resolve().parents[2] / 'migrations/versions/0002_batch_execution.py'
        spec = importlib.util.spec_from_file_location('batch_migration', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        module.upgrade()
        assert {'gpu_batches', 'gpu_executions', 'legacy_marker'} <= set(inspect(connection).get_table_names())
        assert connection.execute(text('SELECT payload FROM legacy_marker')).scalar_one() == 'unchanged'
        assert any(set(item['column_names']) == {'owner_id','idempotency_key'}
                   for item in inspect(connection).get_unique_constraints('gpu_batches'))
