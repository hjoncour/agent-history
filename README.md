# agent-history

One row per Claude Code / Codex session in `~/.agent_history.db` (sqlite), written by harness hooks, so a session
can be found and resumed after a laptop restart or a crashed terminal. A shared `find-session` skill lets any agent
search it from fuzzy clues (topic, ticket id, model, provider, dates, folder).

Columns: session_id, ai_provider, model, effort, session_creation_datetime, session_topic, session_workspace,
last_message_datetime (when you last typed), last_seen (any hook event), transcript_path.

Python 3.10+, standard library only.

## Install

```sh
uv tool install .            # or: pipx install .
agent-history install        # hooks + skill; --dry-run to preview
agent-history backfill       # optional: import sessions from before the hooks existed
```

`agent-history install` is idempotent and only touches its own entries:

| Harness | Hooks added to | Events |
|---|---|---|
| Claude Code | `~/.claude/settings.json` | SessionStart, UserPromptSubmit, Stop, CwdChanged, PostModelSwitch |
| Codex | `~/.codex/hooks.json` (run `/hooks` inside Codex once to trust them) | SessionStart, UserPromptSubmit, Stop |

Other hooks in those files are kept. The skill is written to `~/.agents/skills/find-session/` and symlinked into
`~/.claude/skills/` and `~/.codex/skills/`. A harness whose config dir doesn't exist is skipped.

Each hook runs `<absolute path to agent-history> anthropic|openai` with a 5 second timeout, and reads the hook
payload from stdin.

### Upgrading from agent-monitor

`agent-history install` replaces any `agent-monitor` hooks in place, and the first run moves `~/.agent_monitor.db`
to `~/.agent_history.db`. Delete the old `~/.local/bin/agent-monitor` afterwards.

## Use

```sh
agent-history                                        # all sessions, newest first
agent-history find -m opus-5 billing ABC-123         # ranked; every TERM must hit topic/workspace/transcript
agent-history find --since 2026-09-15 --until 2026-09-16   # last typed yesterday
agent-history find -p openai -w my-service --since 2026-09-01
```

Each hit prints a `resume:` line (`cd <workspace> && claude --resume <id>` or `codex resume <id>`).

## How fields are filled

- model / effort: switch or hook payload first, then the transcript, then harness config as fallback.
- topic: explicit `agent-history <provider> --session <id> --topic "..."` wins and sticks; else Claude's auto title
  (aiTitle); else, for Codex, the first prompt. Agents get one line injected at SessionStart telling them to update
  the topic when the subject shifts.
- workspace: current cwd on every event, immediate on CwdChanged.
- find ranking: topic/workspace hit = 1000, transcript hit = matching-line count. Claude transcripts embed the memory
  index and skill list, so 1 to 3 lines on a generic term is noise; real sessions score tens to hundreds.

## Layout

| Path | Purpose |
|---|---|
| `src/agent_history/cli.py` | argparse entry point, one handler per subcommand |
| `src/agent_history/record.py` | hook handler: folds one event into the session's row |
| `src/agent_history/find.py` | filtering, ranking, table output, resume command |
| `src/agent_history/backfill.py` | import of sessions that predate the hooks |
| `src/agent_history/install.py` | hook merge and skill links |
| `src/agent_history/store.py` | database location, schema, transactions |
| `src/agent_history/transcript.py` | field extraction from transcript files |
| `src/agent_history/providers.py` | per-harness paths, hook events, model pattern, resume command |
| `src/agent_history/skills/find-session/SKILL.md` | the search skill, shipped as package data |

## Development

```sh
python3 -m unittest discover -s tests     # every test runs against a throwaway HOME
```
