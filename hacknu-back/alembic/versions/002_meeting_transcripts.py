"""add meeting_transcripts table

Revision ID: 002
Revises: 001
Create Date: 2026-04-05
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "meeting_transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("room_id", sa.String(255), nullable=False),
        sa.Column("speaker", sa.String(255), nullable=False, server_default="Unknown"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_meeting_transcripts_room", "meeting_transcripts", ["room_id", "created_at"])


def downgrade() -> None:
    op.drop_table("meeting_transcripts")
