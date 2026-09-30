"""Hook handler: fold one hook event (or an explicit --topic) into the session's row."""
import contextlib

from agent_history import store, transcript
from agent_history.providers import ANTHROPIC, OPENAI, Provider


def auto_topic(provider: Provider, event: dict, text: str) -> str:
    # ponytail: Claude titles its own sessions (aiTitle); Codex doesn't, so its topic is the first prompt
    return transcript.title(text) if provider is ANTHROPIC else event.get("prompt") or event.get("user_input") or ""


def record(provider: Provider, event: dict, session_id: str = "", topic: str = "") -> str:
    """Upsert the row; returns the line to print, which SessionStart hooks inject into the agent's context."""
    session_id = session_id or event.get("session_id") or ""
    if not session_id:
        return ""
    with contextlib.closing(store.connect()) as conn:
        path = event.get("transcript_path") or store.get(conn, session_id)["transcript_path"]
        if not path and provider is OPENAI:
            path = transcript.codex_rollout(session_id)
        text = transcript.read(path)
        effort = transcript.last(transcript.EFFORT_RE, text)
        with store.transaction(conn):
            row = store.get(conn, session_id)
            row.update(session_id=session_id, ai_provider=provider.name, last_seen=store.now(), transcript_path=path or "")
            row["session_creation_datetime"] = row["session_creation_datetime"] or store.now()
            if event.get("hook_event_name") == "UserPromptSubmit":
                row["last_message_datetime"] = store.now()
            row["session_workspace"] = event.get("new_cwd") or event.get("cwd") or row["session_workspace"]
            row["model"] = event.get("to_model") or event.get("model") or row["model"] or transcript.last(provider.model_re, text)
            row["effort"] = effort or row["effort"] or provider.config_effort(row["model"])
            row["session_topic"] = transcript.topic(topic or row["session_topic"] or auto_topic(provider, event, text))
            store.put(conn, row)
    if event.get("hook_event_name") != "SessionStart":
        return ""
    return f"agent-history: this session is logged in ~/{store.DB_NAME} as {session_id}. If the conversation moves to a clearly different topic, run: agent-history {provider.name} --session {session_id} --topic \"<short topic>\""
