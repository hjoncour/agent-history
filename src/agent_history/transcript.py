"""Field extraction from Claude Code and Codex transcript files (JSONL, scanned as text)."""
import json, pathlib, re

EFFORT_RE = r'"effort":"(\w+)"'
TITLE_RE = r'"aiTitle":"((?:[^"\\]|\\.)*)"'
CWD_RE = r'"cwd":"([^"]+)"'
TIMESTAMP_RE = r'"timestamp":"(\d{4}-\d\d-\d\dT[^"]+)"'
TOPIC_LENGTH = 120


def last(pattern: str, text: str) -> str:
    matches = re.findall(pattern, text)
    return matches[-1] if matches else ""


def read(path: str) -> str:
    # ponytail: whole-file read on every event; tail it if transcripts ever make prompts feel slow
    return pathlib.Path(path).read_text(errors="replace") if path and pathlib.Path(path).exists() else ""


def title(text: str) -> str:
    """Claude's auto-generated session title (aiTitle), JSON-unescaped."""
    raw = last(TITLE_RE, text)
    return json.loads(f'"{raw}"') if raw else ""


def topic(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:TOPIC_LENGTH]


def codex_rollout(session_id: str) -> str:
    """Codex may omit transcript_path; its rollout file name carries the session id."""
    return next(map(str, (pathlib.Path.home() / ".codex/sessions").rglob(f"*{session_id}.jsonl")), "")
