"""Search the sessions table: filter by column, then rank by where each TERM hits."""
import pathlib, shlex, sqlite3, subprocess
from typing import Iterable

from agent_history.providers import PROVIDERS

TOPIC_HIT = 1000


def transcript_hits(term: str, path: str) -> int:
    if not path or not pathlib.Path(path).exists():
        return 0
    return int(subprocess.run(["grep", "-ciF", "-e", term, "--", path], capture_output=True, text=True).stdout.strip() or 0)


def score(row: dict, terms: Iterable[str]) -> int | None:
    """None unless every term hits; a topic/workspace hit outranks any transcript line count."""
    total = 0
    for term in terms:
        # ponytail: one grep per (session, term); transcripts embed the memory index and skill list,
        # so a single stray line is noise and the line COUNT is what ranks real sessions to the top
        hits = TOPIC_HIT if term.lower() in f"{row['session_topic']} {row['session_workspace']}".lower() else transcript_hits(term, row["transcript_path"])
        if not hits:
            return None
        total += hits
    return total


def search(conn: sqlite3.Connection, terms: Iterable[str] = (), provider: str = "", model: str = "", workspace: str = "", since: str = "", until: str = "", limit: int = 10) -> list[dict]:
    """since/until bound last_message_datetime (when you last typed), so sessions never typed in are left out."""
    sql, args = "SELECT * FROM sessions WHERE 1=1", []
    for column, value in (("ai_provider", provider), ("model", model), ("session_workspace", workspace)):
        if value:
            sql += f" AND {column} LIKE ?"
            args.append(f"%{value}%")
    if since or until:
        sql += " AND last_message_datetime != ''"
    if since:
        sql += " AND last_message_datetime >= ?"
        args.append(since)
    if until:
        sql += " AND last_message_datetime < ?"
        args.append(until)
    hits = []
    for row in conn.execute(sql + " ORDER BY last_message_datetime DESC, last_seen DESC", args):
        row = dict(row)
        row["score"] = score(row, terms)
        if row["score"] is not None:
            hits.append(row)
    hits.sort(key=lambda row: -row["score"])  # stable: keeps newest-first within equal scores
    return hits[:limit]


def table(rows: list[dict], columns: list[str]) -> str:
    widths = [max([len(column)] + [len(str(row.get(column) or "")) for row in rows]) for column in columns]
    return "\n".join("  ".join(str(row.get(column) or "").ljust(width) for column, width in zip(columns, widths)).rstrip() for row in [dict(zip(columns, columns))] + rows)


def resume_command(row: dict) -> str:
    provider = PROVIDERS.get(row["ai_provider"])
    return provider.resume.format(**{key: shlex.quote(str(value)) for key, value in row.items()}) if provider else row["session_id"]
