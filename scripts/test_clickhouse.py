"""Run live regressions in a disposable database; never use the application database."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import clickhouse_connect
from no_more_500s_api.core.config import Settings


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    settings = Settings()
    database = "nm5_test_" + uuid4().hex
    client = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )
    try:
        ddl = (root / "infra/clickhouse/init.sql").read_text().replace("nomore500s", database)
        for statement in ddl.split(";"):
            if statement.strip():
                client.command(statement)
        with tempfile.TemporaryDirectory(prefix="nm5-history-tests-") as temp:
            env = {
                **os.environ,
                "NM5_TEST_CLICKHOUSE": "1",
                "NM5_CLICKHOUSE_DATABASE": database,
                "NM5_HISTORY_HOME": str(Path(temp) / "history"),
            }
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "apps/api/tests/test_clickhouse_repository.py",
                    "apps/api/tests/test_history_reset.py",
                ],
                cwd=root,
                env=env,
                check=False,
            ).returncode
    finally:
        client.command(f"DROP DATABASE IF EXISTS {database} SYNC")
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
