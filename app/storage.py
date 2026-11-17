import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_initialized_databases: set[str] = set()
_initialization_lock = threading.Lock()


def _postgres_url() -> str | None:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        return None
    if value.startswith("postgres://"):
        return "postgresql://" + value.removeprefix("postgres://")
    if value.startswith("postgresql+psycopg://"):
        return value.replace("postgresql+psycopg://", "postgresql://", 1)
    if value.startswith("postgresql://"):
        return value
    raise ValueError("DATABASE_URL must be a PostgreSQL connection URL.")


def _execute(connection: Any, query: str, parameters: tuple = ()):
    if _postgres_url():
        query = query.replace("?", "%s")
    return connection.execute(query, parameters)


def _api_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _initialize_sqlite(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS business_profile (
            owner_id TEXT PRIMARY KEY,
            profile_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    profile_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(business_profile)")
    }
    if "owner_id" not in profile_columns:
        connection.execute("ALTER TABLE business_profile RENAME TO business_profile_legacy")
        connection.execute(
            """
            CREATE TABLE business_profile (
                owner_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            INSERT INTO business_profile (owner_id, profile_json, updated_at)
            SELECT 'local', profile_json, updated_at FROM business_profile_legacy
            WHERE id = 1
            """
        )
        connection.execute("DROP TABLE business_profile_legacy")

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            file_name TEXT NOT NULL,
            page_count INTEGER NOT NULL,
            document_count INTEGER NOT NULL DEFAULT 1,
            analysis_json TEXT,
            status TEXT NOT NULL DEFAULT 'complete',
            error TEXT,
            owner_id TEXT NOT NULL DEFAULT 'local'
        )
        """
    )
    columns = connection.execute("PRAGMA table_info(analyses)").fetchall()
    column_names = {row["name"] for row in columns}
    for name, declaration in (
        ("document_count", "INTEGER NOT NULL DEFAULT 1"),
        ("status", "TEXT NOT NULL DEFAULT 'complete'"),
        ("error", "TEXT"),
        ("owner_id", "TEXT NOT NULL DEFAULT 'local'"),
    ):
        if name not in column_names:
            connection.execute(f"ALTER TABLE analyses ADD COLUMN {name} {declaration}")
    analysis_json = next((row for row in columns if row["name"] == "analysis_json"), None)
    if analysis_json and analysis_json["notnull"]:
        legacy_names = {
            row["name"] for row in connection.execute("PRAGMA table_info(analyses)")
        }
        connection.execute("ALTER TABLE analyses RENAME TO analyses_legacy")
        connection.execute(
            """
            CREATE TABLE analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                file_name TEXT NOT NULL,
                page_count INTEGER NOT NULL,
                document_count INTEGER NOT NULL DEFAULT 1,
                analysis_json TEXT,
                status TEXT NOT NULL DEFAULT 'complete',
                error TEXT,
                owner_id TEXT NOT NULL DEFAULT 'local'
            )
            """
        )
        select_parts = [
            "id",
            "created_at",
            "file_name",
            "page_count",
            "document_count" if "document_count" in legacy_names else "1",
            "analysis_json",
            "status" if "status" in legacy_names else "'complete'",
            "error" if "error" in legacy_names else "NULL",
            "owner_id" if "owner_id" in legacy_names else "'local'",
        ]
        connection.execute(
            """
            INSERT INTO analyses
                (id, created_at, file_name, page_count, document_count, analysis_json, status, error, owner_id)
            SELECT """ + ", ".join(select_parts) + " FROM analyses_legacy"
        )
        connection.execute("DROP TABLE analyses_legacy")


def _initialize_postgres(connection: Any) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS business_profile (
            owner_id TEXT PRIMARY KEY,
            profile_json TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            file_name TEXT NOT NULL,
            page_count INTEGER NOT NULL,
            document_count INTEGER NOT NULL DEFAULT 1,
            analysis_json TEXT,
            status TEXT NOT NULL DEFAULT 'complete',
            error TEXT,
            owner_id TEXT NOT NULL DEFAULT 'local'
        )
        """
    )
    connection.execute(
        "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS document_count INTEGER NOT NULL DEFAULT 1"
    )
    connection.execute(
        "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'complete'"
    )
    connection.execute("ALTER TABLE analyses ADD COLUMN IF NOT EXISTS error TEXT")
    connection.execute(
        "ALTER TABLE analyses ADD COLUMN IF NOT EXISTS owner_id TEXT NOT NULL DEFAULT 'local'"
    )
    connection.execute("ALTER TABLE analyses ALTER COLUMN analysis_json DROP NOT NULL")


def _connect():
    url = _postgres_url()
    if url:
        import psycopg
        from psycopg.rows import dict_row

        connection = psycopg.connect(url, row_factory=dict_row)
        with _initialization_lock:
            if url not in _initialized_databases:
                _initialize_postgres(connection)
                connection.commit()
                _initialized_databases.add(url)
        return connection

    path = Path(os.getenv("TENDERREADY_DB_PATH", "data/tenderready.sqlite3")).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    key = str(path.resolve())
    with _initialization_lock:
        if key not in _initialized_databases:
            _initialize_sqlite(connection)
            connection.commit()
            _initialized_databases.add(key)
    return connection


def get_profile(owner_id: str = "local") -> dict[str, Any] | None:
    with _connect() as connection:
        row = _execute(
            connection,
            "SELECT profile_json FROM business_profile WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()
    return json.loads(row["profile_json"]) if row else None


def save_profile(profile: dict[str, Any], owner_id: str = "local") -> None:
    with _connect() as connection:
        _execute(
            connection,
            """
            INSERT INTO business_profile (owner_id, profile_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(owner_id) DO UPDATE SET
                profile_json = excluded.profile_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (owner_id, json.dumps(profile)),
        )


def save_analysis(
    file_name: str,
    page_count: int,
    document_count: int,
    analysis: dict[str, Any] | None = None,
    *,
    owner_id: str = "local",
    status: str = "processing",
    error: str | None = None,
) -> int:
    with _connect() as connection:
        query = """
            INSERT INTO analyses
                (file_name, page_count, document_count, analysis_json, status, error, owner_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        parameters = (
            file_name,
            page_count,
            document_count,
            json.dumps(analysis) if analysis is not None else None,
            status,
            error,
            owner_id,
        )
        if _postgres_url():
            row = _execute(connection, query + " RETURNING id", parameters).fetchone()
            return int(row["id"])
        cursor = _execute(connection, query, parameters)
        return int(cursor.lastrowid)


def update_analysis_status(
    analysis_id: int,
    *,
    status: str,
    analysis: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with _connect() as connection:
        _execute(
            connection,
            """
            UPDATE analyses
            SET status = ?, analysis_json = ?, error = ?
            WHERE id = ?
            """,
            (
                status,
                json.dumps(analysis) if analysis is not None else None,
                error,
                analysis_id,
            ),
        )


def list_analyses(owner_id: str = "local", limit: int = 10) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = _execute(
            connection,
            """
            SELECT id, created_at, file_name, page_count, document_count, status, error
            FROM analyses WHERE owner_id = ? ORDER BY id DESC LIMIT ?
            """,
            (owner_id, limit),
        ).fetchall()
    records = [dict(row) for row in rows]
    for record in records:
        record["created_at"] = _api_timestamp(record["created_at"])
    return records


def get_analysis(analysis_id: int, owner_id: str = "local") -> dict[str, Any] | None:
    with _connect() as connection:
        row = _execute(
            connection,
            """
            SELECT id, created_at, file_name, page_count, document_count,
                analysis_json, status, error
            FROM analyses WHERE id = ? AND owner_id = ?
            """,
            (analysis_id, owner_id),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    raw = result.get("analysis_json")
    result["analysis"] = json.loads(raw) if raw else None
    result.pop("analysis_json", None)
    result["created_at"] = _api_timestamp(result["created_at"])
    return result
