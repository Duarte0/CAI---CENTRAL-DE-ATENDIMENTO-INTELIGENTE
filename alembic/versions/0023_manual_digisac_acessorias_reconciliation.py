"""Add durable state for the manual DigiSac/Acessórias reconciliation."""

from alembic import op


revision = "0023_manual_reconciliation"
down_revision = "0022_identity_discovery_command"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE digisac_acessorias_reconciliation_executions (
            execution_id UUID PRIMARY KEY,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            acessorias_snapshot_hash TEXT,
            digisac_snapshot_hash TEXT,
            acessorias_page_count INTEGER NOT NULL DEFAULT 0,
            acessorias_request_attempt_count INTEGER NOT NULL DEFAULT 0,
            digisac_page_count INTEGER NOT NULL DEFAULT 0,
            digisac_request_attempt_count INTEGER NOT NULL DEFAULT 0,
            acessorias_company_count INTEGER NOT NULL DEFAULT 0,
            acessorias_contact_count INTEGER NOT NULL DEFAULT 0,
            acessorias_department_count INTEGER NOT NULL DEFAULT 0,
            acessorias_relationship_count INTEGER NOT NULL DEFAULT 0,
            digisac_contact_count INTEGER NOT NULL DEFAULT 0,
            digisac_duplicate_count INTEGER NOT NULL DEFAULT 0,
            new_count INTEGER NOT NULL DEFAULT 0,
            changed_count INTEGER NOT NULL DEFAULT 0,
            unchanged_count INTEGER NOT NULL DEFAULT 0,
            historical_retained_count INTEGER NOT NULL DEFAULT 0,
            discovered_count INTEGER NOT NULL DEFAULT 0,
            candidate_count INTEGER NOT NULL DEFAULT 0,
            ambiguous_count INTEGER NOT NULL DEFAULT 0,
            unresolved_count INTEGER NOT NULL DEFAULT 0,
            confirmed_preserved_count INTEGER NOT NULL DEFAULT 0,
            matching_retry_count INTEGER NOT NULL DEFAULT 0,
            started_at TIMESTAMPTZ NOT NULL,
            completed_at TIMESTAMPTZ,
            failure_category TEXT,
            failure_message TEXT,
            report_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_dac_reconciliation_mode CHECK (
                mode IN ('dry_run', 'apply')
            ),
            CONSTRAINT ck_dac_reconciliation_status CHECK (
                status IN (
                    'started',
                    'dry_run',
                    'succeeded',
                    'matching_failed',
                    'failed'
                )
            ),
            CONSTRAINT ck_dac_reconciliation_counts_nonnegative CHECK (
                acessorias_page_count >= 0
                AND acessorias_request_attempt_count >= 0
                AND digisac_page_count >= 0
                AND digisac_request_attempt_count >= 0
                AND acessorias_company_count >= 0
                AND acessorias_contact_count >= 0
                AND acessorias_department_count >= 0
                AND acessorias_relationship_count >= 0
                AND digisac_contact_count >= 0
                AND digisac_duplicate_count >= 0
                AND new_count >= 0
                AND changed_count >= 0
                AND unchanged_count >= 0
                AND historical_retained_count >= 0
                AND discovered_count >= 0
                AND candidate_count >= 0
                AND ambiguous_count >= 0
                AND unresolved_count >= 0
                AND confirmed_preserved_count >= 0
                AND matching_retry_count >= 0
            ),
            CONSTRAINT ck_dac_reconciliation_hashes_nonblank CHECK (
                (acessorias_snapshot_hash IS NULL OR btrim(acessorias_snapshot_hash) <> '')
                AND (digisac_snapshot_hash IS NULL OR btrim(digisac_snapshot_hash) <> '')
            ),
            CONSTRAINT ck_dac_reconciliation_failure_category_safe CHECK (
                failure_category IS NULL OR failure_category ~ '^[a-z0-9_:-]+$'
            ),
            CONSTRAINT ck_dac_reconciliation_report_object CHECK (
                jsonb_typeof(report_json) = 'object'
            )
        );

        CREATE INDEX ix_dac_reconciliation_status_started
            ON digisac_acessorias_reconciliation_executions (status, started_at DESC);
        CREATE INDEX ix_dac_reconciliation_snapshot_hashes
            ON digisac_acessorias_reconciliation_executions
                (acessorias_snapshot_hash, digisac_snapshot_hash);
        """
    )


def downgrade() -> None:
    populated = op.get_bind().exec_driver_sql(
        """
        SELECT EXISTS (
            SELECT 1 FROM digisac_acessorias_reconciliation_executions
        )
        """
    ).scalar_one()
    if populated:
        raise RuntimeError(
            "manual DigiSac/Acessórias reconciliation state exists; refusing a data-losing downgrade"
        )
    op.execute("DROP INDEX ix_dac_reconciliation_snapshot_hashes")
    op.execute("DROP INDEX ix_dac_reconciliation_status_started")
    op.execute("DROP TABLE digisac_acessorias_reconciliation_executions")
