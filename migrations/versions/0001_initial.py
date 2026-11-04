"""Independent initial database; no changes to Atelier tables."""
from alembic import op
from app.db import Base
from app.gpu import repository
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('为保护任务、租约与作品，不支持自动破坏性降级；使用备份恢复到独立测试库。')
