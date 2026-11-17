import json
import os
import sqlite3
from pathlib import Path
from typing import Any


def _database_path() -> Path:
    return Path(os.getenv("TENDERREADY_DB_PATH", "data/tenderready.sqlite3")).expanduser()


def _connect() -> sqlite3.Connection:
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS business_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            profile_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            file_name TEXT NOT NULL,
            page_count INTEGER NOT NULL,
            analysis_json TEXT NOT NULL
        )
        """
    )
    return connection


def get_profile() -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT profile_json FROM business_profile WHERE id = 1"
        ).fetchone()
    return json.loads(row["profile_json"]) if row else None


def save_profile(profile: dict[str, Any]) -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO business_profile (id, profile_json, updated_at)
            VALUES (1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                profile_json = excluded.profile_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (json.dumps(profile),),
        )


def save_analysis(file_name: str, page_count: int, analysis: dict[str, Any]) -> int:
    with _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO analyses (file_name, page_count, analysis_json) VALUES (?, ?, ?)",
            (file_name, page_count, json.dumps(analysis)),
        )
        return int(cursor.lastrowid)


def list_analyses(limit: int = 10) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, created_at, file_name, page_count
            FROM analyses ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_analysis(analysis_id: int) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT id, created_at, file_name, page_count, analysis_json
            FROM analyses WHERE id = ?
            """,
            (analysis_id,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["analysis"] = json.loads(result.pop("analysis_json"))
    return result
