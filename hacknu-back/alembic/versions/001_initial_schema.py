"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-04
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # agents table
    op.create_table(
        "agents",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("room_id", sa.String(255), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(50), nullable=False, server_default="chatbot"),
        sa.Column("is_default", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # agent_changes table
    op.create_table(
        "agent_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("room_id", sa.String(255), nullable=False),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("operations", postgresql.JSONB(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("user_feedback", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_agent_changes_room_agent", "agent_changes", ["room_id", "agent_id"])

    # chat_messages table
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("role", sa.String(20), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("change_id", sa.String(255), nullable=True),
        sa.Column("change_status", sa.String(20), nullable=True),
        sa.Column("operations_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_chat_messages_agent", "chat_messages", ["agent_id", "created_at"])


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("agent_changes")
    op.drop_table("agents")
