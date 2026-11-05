"""Add batches/executions without changing existing task or asset rows."""
from alembic import op
from app.gpu.repository import GpuBatchRow, GpuExecutionRow

revision = '0002_batch_execution'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    GpuBatchRow.__table__.create(bind, checkfirst=True)
    GpuExecutionRow.__table__.create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError('保留批次与执行证据；回退应用代码并保留增量表，或恢复经验证的独立备份。')
