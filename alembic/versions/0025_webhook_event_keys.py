"""Move webhook event idempotency markers into PostgreSQL."""

from alembic import op


revision = "0025_webhook_event_keys"
down_revision = "0024_durable_media_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE webhook_event_keys (
            event_digest TEXT PRIMARY KEY,
            first_seen_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_webhook_event_keys_digest
                CHECK (event_digest ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_webhook_event_keys_expiry
                CHECK (expires_at > first_seen_at)
        );

        CREATE INDEX ix_webhook_event_keys_expires_at
            ON webhook_event_keys (expires_at, event_digest);
        """
    )


def downgrade() -> None:
    populated = op.get_bind().exec_driver_sql(
        "SELECT EXISTS (SELECT 1 FROM webhook_event_keys)"
    ).scalar_one()
    if populated:
        raise RuntimeError(
            "webhook event idempotency state exists; refusing a data-losing downgrade"
        )
    op.execute("DROP INDEX ix_webhook_event_keys_expires_at")
    op.execute("DROP TABLE webhook_event_keys")
