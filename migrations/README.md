# Legacy SQLite migrations

These SQL files document the schema history of the pre-PostgreSQL SQLite
database. They are retained only to support investigation and the one-time
SQLite-to-PostgreSQL importer; application startup never executes them.

All new production schema changes must be added as Alembic revisions under
`alembic/versions/` and applied with `alembic upgrade head`.
