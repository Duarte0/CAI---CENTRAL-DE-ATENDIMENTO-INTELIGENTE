"""Persist canonical DigiSac ticket-contact provenance for cycle preparation."""

from alembic import op


revision = "0020_cycle_contact_provenance"
down_revision = "0019_acessorias_request_creation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE conversation_processing_cycles
            ADD COLUMN digisac_contact_external_id TEXT;
        ALTER TABLE conversation_processing_cycles
            ADD CONSTRAINT ck_conversation_cycles_contact_external_id_nonblank
            CHECK (
                digisac_contact_external_id IS NULL
                OR btrim(digisac_contact_external_id) <> ''
            );
        CREATE INDEX ix_conversation_cycles_digisac_contact_external_id
            ON conversation_processing_cycles (digisac_contact_external_id)
            WHERE digisac_contact_external_id IS NOT NULL;

        ALTER TABLE acessorias_request_operations
            ADD COLUMN preparation_recovery_json JSONB NOT NULL DEFAULT '{}'::jsonb;
        ALTER TABLE acessorias_request_operations
            ADD CONSTRAINT ck_acessorias_request_operation_preparation_recovery_object
            CHECK (jsonb_typeof(preparation_recovery_json) = 'object');
        """
    )


def downgrade() -> None:
    populated = op.get_bind().exec_driver_sql(
        """
        SELECT EXISTS (
            SELECT 1
            FROM conversation_processing_cycles
            WHERE digisac_contact_external_id IS NOT NULL
        ) OR EXISTS (
            SELECT 1
            FROM acessorias_request_operations
            WHERE preparation_recovery_json <> '{}'::jsonb
        )
        """
    ).scalar_one()
    if populated:
        raise RuntimeError(
            "cycle contact provenance or preparation recovery state exists; "
            "refusing a data-losing downgrade"
        )
    op.execute(
        "ALTER TABLE acessorias_request_operations "
        "DROP CONSTRAINT ck_acessorias_request_operation_preparation_recovery_object"
    )
    op.execute(
        "ALTER TABLE acessorias_request_operations "
        "DROP COLUMN preparation_recovery_json"
    )
    op.execute(
        "DROP INDEX ix_conversation_cycles_digisac_contact_external_id"
    )
    op.execute(
        "ALTER TABLE conversation_processing_cycles "
        "DROP CONSTRAINT ck_conversation_cycles_contact_external_id_nonblank"
    )
    op.execute(
        "ALTER TABLE conversation_processing_cycles "
        "DROP COLUMN digisac_contact_external_id"
    )
