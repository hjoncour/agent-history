"""Import sessions that predate the hooks, from Claude Code / Codex transcripts and prompt history.

Idempotent: session ids already in the table are skipped, and last_message_datetime is only filled where empty.
"""
import contextlib, datetime, json, pathlib, re
from typing import Iterator

from agent_history import store, transcript
from agent_history.providers import ANTHROPIC, OPENAI

HEADLESS_PREFIX = "/private/tmp/claude-"  # headless test sessions run from a scratch dir; not worth resuming


def local(iso: str) -> str:
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime(store.TIME_FORMAT)


def from_epoch(seconds: float) -> str:
    return datetime.datetime.fromtimestamp(seconds).strftime(store.TIME_FORMAT)


def jsonl(path: pathlib.Path) -> Iterator[dict]:
    """Objects in a JSONL file; malformed lines are skipped and a missing file yields nothing."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        with contextlib.suppress(ValueError):
            entry = json.loads(line)
            if isinstance(entry, dict):
                yield entry


def claude_sessions(existing: set[str]) -> Iterator[dict[str, str]]:
    for path in ANTHROPIC.path("projects").glob("*/*.jsonl"):
        if path.stem in existing:
            continue
        text = path.read_text(errors="replace")
        cwd = re.search(transcript.CWD_RE, text)
        timestamps = re.findall(transcript.TIMESTAMP_RE, text)
        if not cwd or not timestamps or cwd.group(1).startswith(HEADLESS_PREFIX):
            continue
        yield {"session_id": path.stem, "ai_provider": ANTHROPIC.name, "model": transcript.last(ANTHROPIC.model_re, text), "effort": transcript.last(transcript.EFFORT_RE, text), "session_creation_datetime": local(timestamps[0]), "session_topic": transcript.topic(transcript.title(text)), "session_workspace": cwd.group(1), "last_message_datetime": "", "last_seen": local(timestamps[-1]), "transcript_path": str(path)}


def codex_sessions(existing: set[str]) -> Iterator[dict[str, str]]:
    first_prompts = {}
    for entry in jsonl(OPENAI.path("history.jsonl")):
        if "session_id" in entry and "ts" in entry:
            first_prompts.setdefault(entry["session_id"], entry)
    for session_id, entry in first_prompts.items():
        if session_id in existing:
            continue
        path = transcript.codex_rollout(session_id)
        text = transcript.read(path)
        cwd = re.search(transcript.CWD_RE, text)
        timestamps = re.findall(transcript.TIMESTAMP_RE, text)
        if cwd and cwd.group(1).startswith(HEADLESS_PREFIX):
            continue
        created = from_epoch(entry["ts"])
        yield {"session_id": session_id, "ai_provider": OPENAI.name, "model": transcript.last(OPENAI.model_re, text), "effort": transcript.last(transcript.EFFORT_RE, text), "session_creation_datetime": created, "session_topic": transcript.topic(entry.get("text", "")), "session_workspace": cwd.group(1) if cwd else "", "last_message_datetime": "", "last_seen": local(timestamps[-1]) if timestamps else created, "transcript_path": path}


def last_prompts() -> dict[str, float]:
    """Newest prompt per session (epoch seconds), from both harnesses' prompt history."""
    newest = {}
    for entry in jsonl(ANTHROPIC.path("history.jsonl")):
        if "sessionId" in entry and "timestamp" in entry:
            newest[entry["sessionId"]] = max(newest.get(entry["sessionId"], 0), entry["timestamp"] / 1000)
    for entry in jsonl(OPENAI.path("history.jsonl")):
        if "session_id" in entry and "ts" in entry:
            newest[entry["session_id"]] = max(newest.get(entry["session_id"], 0), entry["ts"])
    return newest


def backfill() -> str:
    with contextlib.closing(store.connect()) as conn:
        existing = {row[0] for row in conn.execute("SELECT session_id FROM sessions")}
        rows, prompts = [*claude_sessions(existing), *codex_sessions(existing)], last_prompts()
        imported = {ANTHROPIC.name: 0, OPENAI.name: 0}
        with store.transaction(conn):  # scan first, lock only for the writes, so live hooks don't wait on it
            for row in rows:
                imported[row["ai_provider"]] += store.add(conn, row)
            filled = sum(conn.execute("UPDATE sessions SET last_message_datetime = ? WHERE session_id = ? AND (last_message_datetime = '' OR last_message_datetime IS NULL)", (from_epoch(seconds), session_id)).rowcount for session_id, seconds in prompts.items())
            total = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
    return f"imported claude={imported[ANTHROPIC.name]} codex={imported[OPENAI.name]}; last_message_datetime filled on {filled} rows; total={total}"
