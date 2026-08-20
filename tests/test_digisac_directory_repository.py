import inspect
import os
import subprocess
import sys

from src.core import db, digisac_directory_repository


def test_digisac_directory_persistence_is_owned_by_repository() -> None:
    public_operations = (
        "upsert_digisac_directory",
        "mark_directory_sync_attempt",
        "directory_refresh_is_due",
        "resolve_user_names",
    )

    for name in public_operations:
        operation = getattr(db, name)
        assert operation is getattr(digisac_directory_repository, name)
        assert inspect.getmodule(operation) is digisac_directory_repository

    assert not hasattr(db, "_upsert_digisac_directory_sync")
    assert not hasattr(db, "_mark_directory_sync_attempt_sync")
    assert not hasattr(db, "_directory_refresh_is_due_sync")
    assert not hasattr(db, "_resolve_user_names_sync")


def test_digisac_directory_repository_can_be_imported_before_database_facade() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = "/app"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import src.core.digisac_directory_repository as repository; "
            "assert repository.upsert_digisac_directory",
        ],
        cwd="/app",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
