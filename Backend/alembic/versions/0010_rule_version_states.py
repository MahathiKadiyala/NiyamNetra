"""rule_version_states

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-24 18:15:00.000000

Adds the rule_version_states table to persist active/inactive state of statutory
rule versions for compliance evaluation.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    try:
        return name in sa.inspect(bind).get_table_names()
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "rule_version_states"):
        op.create_table(
            "rule_version_states",
            sa.Column("id", sa.String(length=40), primary_key=True, nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "rule_version_states"):
        op.drop_table("rule_version_states")
