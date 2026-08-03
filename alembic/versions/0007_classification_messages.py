"""Normalize classification-to-message membership additively."""

from alembic import op

revision = "0007_class_messages"
down_revision = "0006_identity_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE classification_messages (
            classification_id BIGINT NOT NULL,
            message_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT pk_classification_messages
                PRIMARY KEY (classification_id, message_id),
            CONSTRAINT uq_classification_messages_position
                UNIQUE (classification_id, position),
            CONSTRAINT fk_classification_messages_classification
                FOREIGN KEY (classification_id)
                REFERENCES ia_classifications(id) ON DELETE CASCADE,
            CONSTRAINT ck_classification_messages_message_nonblank
                CHECK (btrim(message_id) <> ''),
            CONSTRAINT ck_classification_messages_position_nonnegative
                CHECK (position >= 0)
        );
        CREATE INDEX idx_classification_messages_message_classification
            ON classification_messages(message_id, classification_id);
        """
    )


def downgrade() -> None:
    count = (
        op.get_bind()
        .exec_driver_sql("SELECT COUNT(*) FROM classification_messages")
        .scalar_one()
    )
    if count:
        raise RuntimeError(
            "classification_messages is not empty; refusing a data-losing downgrade"
        )
    op.execute("DROP TABLE classification_messages")
