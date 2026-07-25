"""Apply the versioned PostgreSQL schema migrations."""

from alembic.config import CommandLine


def main() -> None:
    CommandLine().main(["upgrade", "head"])


if __name__ == "__main__":
    main()
