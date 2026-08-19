"""thread_roots: ondelete CASCADE на оба FK

Модель объявляет ondelete="CASCADE" для source_id и channel_source_id, а
начальная миграция создала FK без него.

Revision ID: 8f1c4b2a91d7
Revises: 2027ac7f3df1
Create Date: 2026-08-19 19:05:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = '8f1c4b2a91d7'
down_revision: Union[str, Sequence[str], None] = '2027ac7f3df1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FKS = (
    ('thread_roots_source_id_fkey', 'source_id'),
    ('thread_roots_channel_source_id_fkey', 'channel_source_id'),
)


def upgrade() -> None:
    """Upgrade schema."""
    for name, column in _FKS:
        op.drop_constraint(name, 'thread_roots', type_='foreignkey')
        op.create_foreign_key(
            name, 'thread_roots', 'sources', [column], ['id'], ondelete='CASCADE'
        )


def downgrade() -> None:
    """Downgrade schema."""
    for name, column in _FKS:
        op.drop_constraint(name, 'thread_roots', type_='foreignkey')
        op.create_foreign_key(name, 'thread_roots', 'sources', [column], ['id'])
