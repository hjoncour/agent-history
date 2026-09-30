---
name: find-session
description: >-
  Find and resume a lost Claude Code or Codex session from fuzzy clues (topic keywords,
  ticket or card id, model such as "Opus 5", provider, rough date, project folder) by
  querying the local session log in ~/.agent_history.db through the agent-history CLI.
  Use whenever the user says they lost a session, asks which session they were working
  on something in, wants to resume "the session about X", or asks what sessions ran recently.
license: MIT
---

# find-session

Hooks log every Claude Code and Codex session into `~/.agent_history.db`, table `sessions`
(session_id, ai_provider, model, effort, session_creation_datetime, session_topic,
session_workspace, last_message_datetime, last_seen, transcript_path). `agent-history find` queries it; any TERM not
found in the topic or workspace is grepped inside the session transcript, so ticket ids work. Results are
ranked: a topic or workspace hit scores 1000, a transcript hit scores its number of matching lines, and the
top 10 are shown (`-n` to change). Transcripts embed the memory index and skill list, so a score of 1 to 3 on a
generic term is boilerplate noise; real sessions score tens to hundreds.

## Steps

1. Turn the clues into flags and terms:
   - provider: Claude -> `-p anthropic`; Codex or GPT -> `-p openai`
   - model: `-m opus-5` for "Opus 5", `-m opus-4-8`, `-m fable`, `-m sonnet`, `-m gpt-5.6` (substring of the model id; never pass a model name as a free TERM)
   - folder or repo: `-w <substring of the path>`
   - time: `--since DATE` / `--until DATE` on last_message_datetime, i.e. when the user last typed in that session.
     "yesterday" -> `--since <yesterday> --until <today>`; "last week" -> `--since <7 days ago>`; check today's date first
   - everything else (ABC-123, billing, migration, refund) -> free TERMs, one word or id each
2. Run it, then loosen if nothing matches: drop the least certain TERM first, then the flags.
   ```
   agent-history find -m opus-5 --since 2026-09-01 billing ABC-123
   agent-history find --since 2026-09-15 --until 2026-09-16      # last typed yesterday
   agent-history            # everything, newest first
   ```
3. Still ambiguous: read the candidates' transcripts (paths via
   `sqlite3 -readonly ~/.agent_history.db "select session_id, transcript_path from sessions where ..."`) and grep the clue there.
4. Answer with the matching rows (topic, model, created, workspace) and the `resume:` line printed under the
   table. Never run the resume command yourself; the user runs it in their own terminal.

## Notes

- session_topic is Claude's auto-generated title or Codex's first prompt, so ticket ids are often only in the transcript. Keep at least one free TERM.
- Rows imported by `agent-history backfill` come from old transcripts; model or effort may be blank on them.
