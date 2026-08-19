"""sourcekind: добавить значение private

Личный чат — полноценный Source: «Избранное» это личный текстовый архив, и
индексировать его хочется той же машинерией, что и каналы.

Revision ID: c3d9e5a714b2
Revises: 8f1c4b2a91d7
Create Date: 2026-08-19 19:25:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c3d9e5a714b2'
down_revision: Union[str, Sequence[str], None] = '8f1c4b2a91d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE sourcekind ADD VALUE IF NOT EXISTS 'private'")


def downgrade() -> None:
    """Downgrade schema.

    Postgres не умеет удалять значение enum, поэтому тип пересоздаётся. Если
    хоть один Source уже помечен private, приведение типа упадёт — это и нужно:
    молча потерять такие строки хуже, чем громко отказаться.
    """
    op.execute("ALTER TABLE sources ALTER COLUMN kind TYPE text USING kind::text")
    op.execute("DROP TYPE sourcekind")
    op.execute("CREATE TYPE sourcekind AS ENUM ('channel', 'comment_chat', 'group')")
    op.execute(
        "ALTER TABLE sources ALTER COLUMN kind TYPE sourcekind USING kind::sourcekind"
    )
