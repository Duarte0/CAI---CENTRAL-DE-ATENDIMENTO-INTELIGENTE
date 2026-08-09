"""Run the repository's canonical verification matrix on disposable PostgreSQL."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit

import psycopg

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.test.yml"
TEST_DATABASE = "cai_test"
TEST_USER = "cai_test"
TEST_PASSWORD = "cai_test"
EXPECTED_SCHEMA = "0014_retry_scheduling"


class RunnerFailure(RuntimeError):
    """A canonical verification stage or safety check failed."""


@dataclass(frozen=True)
class StageResult:
    name: str
    status: str


def parse_compose_endpoint(output: str) -> int:
    """Return the published host port from ``docker compose port`` output."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Docker Compose did not return a PostgreSQL endpoint")
    endpoint = lines[-1]
    try:
        _host, port_text = endpoint.rsplit(":", 1)
        port = int(port_text)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid Docker Compose endpoint: {endpoint!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"Invalid PostgreSQL host port: {port}")
    return port


def build_target_url(port: int) -> str:
    """Build the only database URL this runner is allowed to use."""
    if not 1 <= port <= 65535:
        raise ValueError(f"Invalid PostgreSQL host port: {port}")
    return (
        f"postgresql://{TEST_USER}:{TEST_PASSWORD}@127.0.0.1:{port}/"
        f"{TEST_DATABASE}"
    )


def build_network_target_url() -> str:
    """Build the Docker-network form used when the runner itself is containerized."""
    return f"postgresql://{TEST_USER}:{TEST_PASSWORD}@postgres-test:5432/{TEST_DATABASE}"


def validate_runner_target(url: str, *, expected_port: int) -> None:
    """Reject URLs that could point outside the runner-owned local database."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise ValueError("runner database URL must use PostgreSQL")
    if parsed.hostname not in {"127.0.0.1", "localhost", "postgres-test"}:
        raise ValueError("runner database URL must target the runner-owned PostgreSQL")
    if parsed.username != TEST_USER or parsed.password != TEST_PASSWORD:
        raise ValueError("runner database URL must use the test credentials")
    if parsed.hostname == "postgres-test" and parsed.port != 5432:
        raise ValueError("Docker-network runner URL must use PostgreSQL port 5432")
    if parsed.hostname != "postgres-test" and parsed.port != expected_port:
        raise ValueError("runner database URL does not match the published port")
    if parsed.path.removeprefix("/") != TEST_DATABASE:
        raise ValueError("runner database URL must target the disposable test database")
    if parsed.query or parsed.fragment:
        raise ValueError("runner database URL must not contain query or fragment data")


def prepare_test_environment(
    source: Mapping[str, str], target_url: str
) -> dict[str, str]:
    """Return a child environment that cannot inherit an unsafe database target."""
    target_port = urlsplit(target_url).port
    if target_port is None:
        raise ValueError("runner target URL has no host port")
    validate_runner_target(target_url, expected_port=target_port)
    environment = dict(source)
    environment["CAI_TEST_DATABASE_URL"] = target_url
    environment["DATABASE_URL"] = target_url
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(ROOT)
        if not existing_pythonpath
        else f"{ROOT}{os.pathsep}{existing_pythonpath}"
    )
    return environment


def compose_command(project: str, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project,
        "--file",
        str(COMPOSE_FILE),
        *arguments,
    ]


def run_stage(
    name: str,
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run and report one canonical stage without hiding its output."""
    print(f"\n[stage] {name}")
    try:
        result = subprocess.run(
            list(command),
            cwd=ROOT,
            env=dict(environment) if environment is not None else None,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RunnerFailure(f"{name} unavailable: {exc}") from exc
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        raise RunnerFailure(f"{name} failed with exit code {result.returncode}")
    print(f"[stage] {name}: PASS")
    return result


def discover_host_port(project: str) -> int:
    try:
        result = subprocess.run(
            compose_command(project, "port", "postgres-test", "5432"),
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RunnerFailure(f"cannot inspect the disposable PostgreSQL endpoint: {exc}") from exc
    if result.returncode:
        raise RunnerFailure("Docker Compose did not publish the disposable PostgreSQL endpoint")
    try:
        return parse_compose_endpoint(result.stdout)
    except ValueError as exc:
        raise RunnerFailure(str(exc)) from exc


def verify_database_connection(target_url: str, *, expected_port: int) -> None:
    """Prove the host process can reach the exact URL later passed to pytest."""
    validate_runner_target(target_url, expected_port=expected_port)
    try:
        with psycopg.connect(target_url, connect_timeout=10) as connection:
            database, user, version = connection.execute(
                "SELECT current_database(), current_user, version()"
            ).fetchone()
    except psycopg.Error as exc:
        raise RunnerFailure(f"host PostgreSQL connectivity failed: {exc}") from exc
    if database != TEST_DATABASE or user != TEST_USER:
        raise RunnerFailure("host PostgreSQL connectivity reached an unexpected database")
    if not str(version).startswith("PostgreSQL 16"):
        raise RunnerFailure("disposable target is not PostgreSQL 16")


def connect_runner_container(project: str) -> str:
    """Attach only the current runner container to the unique Compose network."""
    if not Path("/.dockerenv").exists():
        raise RunnerFailure(
            "host PostgreSQL port is unreachable and no Docker-network runner is available"
        )
    container_id = os.uname().nodename
    network = f"{project}_default"
    try:
        result = subprocess.run(
            ["docker", "network", "connect", network, container_id],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RunnerFailure(f"cannot connect the runner container to its test network: {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip()
        raise RunnerFailure(
            "cannot connect the runner container to its test network"
            + (f": {detail}" if detail else "")
        )
    return network


def disconnect_runner_container(network: str) -> str | None:
    container_id = os.uname().nodename
    try:
        result = subprocess.run(
            ["docker", "network", "disconnect", network, container_id],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return f"cannot disconnect the runner container from its test network: {exc}"
    if result.returncode:
        detail = result.stderr.strip()
        return "cannot disconnect the runner container from its test network" + (
            f": {detail}" if detail else ""
        )
    return None


def verify_schema_head(target_url: str) -> None:
    try:
        with psycopg.connect(target_url, connect_timeout=10) as connection:
            revision = connection.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
    except psycopg.Error as exc:
        raise RunnerFailure(f"Alembic head verification failed: {exc}") from exc
    if not revision or revision[0] != EXPECTED_SCHEMA:
        actual = revision[0] if revision else "none"
        raise RunnerFailure(
            f"Alembic head verification found {actual!r}, expected {EXPECTED_SCHEMA!r}"
        )


def new_project_name() -> str:
    return f"cai-verification-{os.getpid()}-{secrets.token_hex(4)}"


def run() -> int:
    results: list[StageResult] = []
    project = new_project_name()
    compose_started = False
    runner_network: str | None = None
    cleanup_error: str | None = None
    failure: str | None = None

    try:
        run_stage(
            "compileall",
            [
                sys.executable,
                "-m",
                "compileall",
                "-q",
                "src",
                "tests",
                "alembic",
                "scripts",
            ],
        )
        results.append(StageResult("compileall", "PASS"))
        run_stage("Pyright", ["npx", "--yes", "pyright"])
        results.append(StageResult("Pyright", "PASS"))

        offline_environment = dict(os.environ)
        offline_environment.pop("CAI_TEST_DATABASE_URL", None)
        run_stage(
            "offline pytest",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--ignore=tests/test_webhook_local.py",
            ],
            environment=offline_environment,
        )
        results.append(StageResult("offline pytest", "PASS"))

        if shutil.which("docker") is None:
            raise RunnerFailure("Docker is unavailable; PostgreSQL verification cannot run")

        compose_started = True
        run_stage("start disposable PostgreSQL 16", compose_command(project, "up", "-d", "--wait"))
        results.append(StageResult("start disposable PostgreSQL 16", "PASS"))
        port = discover_host_port(project)
        host_target_url = build_target_url(port)
        try:
            verify_database_connection(host_target_url, expected_port=port)
            target_url = host_target_url
            connection_stage = "host PostgreSQL connection"
        except RunnerFailure:
            runner_network = connect_runner_container(project)
            target_url = build_network_target_url()
            verify_database_connection(target_url, expected_port=5432)
            connection_stage = "Docker-network PostgreSQL connection"
        print(f"[stage] {connection_stage}: PASS (PostgreSQL 16, runner-owned target)")
        results.append(StageResult(connection_stage, "PASS"))

        test_environment = prepare_test_environment(os.environ, target_url)
        run_stage(
            "Alembic upgrade head",
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            environment=test_environment,
        )
        verify_schema_head(target_url)
        print(f"[stage] Alembic head verification: PASS ({EXPECTED_SCHEMA})")
        results.append(StageResult("Alembic head", "PASS"))

        postgres_result = run_stage(
            "PostgreSQL pytest",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-m",
                "postgres",
                "--ignore=tests/test_webhook_local.py",
            ],
            environment=test_environment,
        )
        if "CAI_TEST_DATABASE_URL is not configured" in (
            postgres_result.stdout + postgres_result.stderr
        ):
            raise RunnerFailure("PostgreSQL pytest reported an unavailable database prerequisite")
        results.append(StageResult("PostgreSQL pytest", "PASS"))
    except RunnerFailure as exc:
        failure = str(exc)
        print(f"\n[runner] FAILED: {failure}", file=sys.stderr)
    finally:
        if runner_network:
            network_error = disconnect_runner_container(runner_network)
            if network_error:
                cleanup_error = network_error
        if compose_started:
            try:
                cleanup = subprocess.run(
                    compose_command(project, "down", "--volumes", "--remove-orphans"),
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if cleanup.stdout:
                    print(cleanup.stdout, end="")
                if cleanup.stderr:
                    print(cleanup.stderr, end="", file=sys.stderr)
                if cleanup.returncode:
                    cleanup_error = f"scoped Compose cleanup failed with exit code {cleanup.returncode}"
                else:
                    print(f"[cleanup] removed runner project {project}")
            except OSError as exc:
                cleanup_error = f"scoped Compose cleanup unavailable: {exc}"
        if cleanup_error:
            print(f"[runner] FAILED: {cleanup_error}", file=sys.stderr)

    print("\n[runner] Stage summary")
    for result in results:
        print(f"- {result.name}: {result.status}")
    if failure or cleanup_error:
        return 1
    print("[runner] Verification complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
