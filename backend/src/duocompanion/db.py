import sqlite3
from datetime import UTC, datetime
from pathlib import Path

# Append-only list; PRAGMA user_version records how many have been applied.
MIGRATIONS = [
    """
    CREATE TABLE raw_payloads (
        id             INTEGER PRIMARY KEY,
        client_id      TEXT NOT NULL UNIQUE,
        kind           TEXT NOT NULL,
        received_at    TEXT NOT NULL,
        captured_at    TEXT,
        source_url     TEXT,
        script_version TEXT,
        body           TEXT NOT NULL
    );
    CREATE INDEX raw_payloads_kind ON raw_payloads (kind, id);
    """,
]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")


def store_raw(
    conn: sqlite3.Connection,
    *,
    client_id: str,
    kind: str,
    body: str,
    captured_at: str | None,
    source_url: str | None,
    script_version: str | None,
) -> tuple[int, bool]:
    """Insert a raw payload. Returns (row id, created); a repeated client_id is a no-op."""
    with conn:
        cur = conn.execute(
            """
            INSERT INTO raw_payloads
                (client_id, kind, received_at, captured_at, source_url, script_version, body)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (client_id) DO NOTHING
            """,
            (
                client_id,
                kind,
                datetime.now(UTC).isoformat(timespec="seconds"),
                captured_at,
                source_url,
                script_version,
                body,
            ),
        )
        if cur.rowcount:
            return cur.lastrowid, True
    row = conn.execute("SELECT id FROM raw_payloads WHERE client_id = ?", (client_id,)).fetchone()
    return row["id"], False
