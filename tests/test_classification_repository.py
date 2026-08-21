import os
import subprocess
import sys

import src.core.classification_repository as classification_repository
import src.core.db as database


def test_database_facade_reexports_classification_repository_contract() -> None:
    assert database.ClassificationIdentity is classification_repository.ClassificationIdentity
    assert database.insert_classification is classification_repository.insert_classification
    assert database.update_analysis_protocol is classification_repository.update_analysis_protocol
    assert database.classification_exists is classification_repository.classification_exists
    assert database.ticket_has_classification is classification_repository.ticket_has_classification


def test_classification_repository_can_be_imported_before_database_facade() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = "/app"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import src.core.classification_repository as repository; "
            "assert repository.ClassificationIdentity",
        ],
        cwd="/app",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
