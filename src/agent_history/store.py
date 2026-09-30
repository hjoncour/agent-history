"""The sessions table in ~/.agent_history.db."""
import contextlib, datetime, pathlib, sqlite3
from typing import Iterator

DB_NAME = ".agent_history.db"
LEGACY_DB_NAME = ".agent_monitor.db"  # pre-rename location, moved on first connect
COLUMNS = ["session_id", "ai_provider", "model", "effort", "session_creation_datetime", "session_topic", "session_workspace", "last_message_datetime", "last_seen", "transcript_path"]
VIEW_COLUMNS = COLUMNS[:-1]  # transcript_path is for `find`, not for eyes
INSERT = f"INTO sessions ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def db_path() -> pathlib.Path:
    return pathlib.Path.home() / DB_NAME


def now() -> str:
    return datetime.datetime.now().strftime(TIME_FORMAT)


def connect() -> sqlite3.Connection:
    path = db_path()
    legacy = path.with_name(LEGACY_DB_NAME)
    if not path.exists() and legacy.exists():
        with contextlib.suppress(FileNotFoundError):  # a concurrent hook may have moved it first
            legacy.rename(path)
    conn = sqlite3.connect(path, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (session_id PRIMARY KEY)")
    have = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    for column in COLUMNS:  # adding a column = append it to COLUMNS; existing rows get ''
        if column not in have:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} DEFAULT ''")
    return conn


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Write lock for the whole block, so concurrent hooks on one session can't lose each other's update."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def get(conn: sqlite3.Connection, session_id: str) -> dict[str, str]:
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    return {column: (row[column] if row else "") or "" for column in COLUMNS}


def put(conn: sqlite3.Connection, row: dict[str, str]) -> None:
    conn.execute(f"INSERT OR REPLACE {INSERT}", [row[column] for column in COLUMNS])


def add(conn: sqlite3.Connection, row: dict[str, str]) -> bool:
    """Insert unless the session is already there; False when it was."""
    return conn.execute(f"INSERT OR IGNORE {INSERT}", [row[column] for column in COLUMNS]).rowcount == 1
