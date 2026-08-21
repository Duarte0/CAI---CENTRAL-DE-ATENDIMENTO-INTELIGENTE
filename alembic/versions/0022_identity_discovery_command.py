"""Allow the shared administrative command ledger to store discovery results."""

from alembic import op


revision = "0022_identity_discovery_command"
down_revision = "0021_identity_admin_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE identity_admin_commands
            ALTER COLUMN acessorias_company_id DROP NOT NULL;

        ALTER TABLE identity_admin_commands
            DROP CONSTRAINT ck_identity_admin_commands_operation;

        ALTER TABLE identity_admin_commands
            ADD CONSTRAINT ck_identity_admin_commands_operation CHECK (
                operation IN (
                    'identity_link_confirmation',
                    'identity_link_rejection',
                    'identity_discovery'
                )
            );

        ALTER TABLE identity_admin_commands
            ADD CONSTRAINT ck_identity_admin_commands_target CHECK (
                (operation = 'identity_discovery'
                 AND acessorias_company_id IS NULL)
                OR (operation <> 'identity_discovery'
                    AND acessorias_company_id IS NOT NULL)
            );
        """
    )


def downgrade() -> None:
    populated = op.get_bind().exec_driver_sql(
        """
        SELECT EXISTS (
            SELECT 1
            FROM identity_admin_commands
            WHERE operation = 'identity_discovery'
               OR acessorias_company_id IS NULL
        )
        """
    ).scalar_one()
    if populated:
        raise RuntimeError(
            "identity discovery command state exists; refusing a data-losing downgrade"
        )
    op.execute(
        """
        ALTER TABLE identity_admin_commands
            DROP CONSTRAINT ck_identity_admin_commands_target;

        ALTER TABLE identity_admin_commands
            DROP CONSTRAINT ck_identity_admin_commands_operation;

        ALTER TABLE identity_admin_commands
            ADD CONSTRAINT ck_identity_admin_commands_operation CHECK (
                operation IN (
                    'identity_link_confirmation',
                    'identity_link_rejection'
                )
            );

        ALTER TABLE identity_admin_commands
            ALTER COLUMN acessorias_company_id SET NOT NULL;
        """
    )
