"""Shared PostgreSQL integration-test setup.

Set CAI_TEST_DATABASE_URL to a disposable PostgreSQL database to run the
database-backed tests.  They are skipped locally when PostgreSQL is absent;
pure webhook/model tests remain runnable without external services.
"""

import os
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config

from src.core.db import close_database, initialize_database

TEST_DATABASE_URL = os.environ.get("CAI_TEST_DATABASE_URL")
POSTGRES_MODULES = {
    "test_ia_history_db.py",
    "test_ticket_assignments.py",
    "test_digisac_directory.py",
    "test_postgres_evolution.py",
    "test_conversation_cycles_db.py",
    "test_identity_resolution.py",
    "test_department_mapping.py",
}
_schema_ready = False


def pytest_configure(config):
    config.addinivalue_line("markers", "postgres: requires a PostgreSQL database")


def pytest_collection_modifyitems(config, items):
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="CAI_TEST_DATABASE_URL is not configured")
    for item in items:
        if item.path.name in POSTGRES_MODULES or item.get_closest_marker("postgres"):
            item.add_marker(skip)


@pytest_asyncio.fixture(autouse=True)
async def postgres_state(request):
    global _schema_ready
    needs_database = (
        request.node.path.name in POSTGRES_MODULES
        or request.node.get_closest_marker("postgres") is not None
    )
    if not needs_database:
        yield
        return
    assert TEST_DATABASE_URL
    from src.core.config import settings

    previous_url = settings.database_url
    previous_environment_url = os.environ.get("DATABASE_URL")
    settings.database_url = TEST_DATABASE_URL
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    await close_database()
    if not _schema_ready:
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        command.upgrade(config, "head")
        _schema_ready = True
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute(
            """
            TRUNCATE TABLE
                conversation_cycle_identity_resolutions,
                conversation_cycle_department_mappings,
                acessorias_request_reconciliations,
                acessorias_request_operations,
                department_mapping_transitions,
                department_mapping_rules,
                identity_admin_commands,
                digisac_acessorias_reconciliation_executions,
                identity_company_link_transitions,
                identity_match_evidence,
                identity_company_links,
                digisac_contact_hydrations,
                digisac_contacts,
                acessorias_company_departments,
                acessorias_company_contacts,
                acessorias_directory_sync_executions,
                acessorias_departments,
                acessorias_companies,
                conversation_cycle_messages,
                conversation_processing_cycles,
                classification_messages,
                ia_classifications,
                message_transcriptions,
                message_image_extractions,
                ticket_assignment_history,
                ticket_assignment_event_keys,
                digisac_departments,
                digisac_users,
                digisac_directory_sync_state
            RESTART IDENTITY CASCADE
            """
        )
    await initialize_database()
    try:
        yield
    finally:
        await close_database()
        settings.database_url = previous_url
        if previous_environment_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_environment_url
