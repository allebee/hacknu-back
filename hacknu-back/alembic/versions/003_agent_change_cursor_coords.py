"""add cursor coords to agent_changes

Revision ID: 003
Revises: 002
Create Date: 2026-04-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_changes", sa.Column("x", sa.Float(), nullable=True))
    op.add_column("agent_changes", sa.Column("y", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_changes", "y")
    op.drop_column("agent_changes", "x")
