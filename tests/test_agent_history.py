import contextlib, datetime, json, os, pathlib, sqlite3, subprocess, sys, tempfile, unittest
from unittest import mock

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from agent_history import find, install, store  # noqa: E402

TODAY = datetime.date.today().isoformat()
TOMORROW = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
CLAUDE_TRANSCRIPT = '{"message":{"model":"claude-a"},"effort":"xhigh"}\n{"type":"ai-title","aiTitle":"Auto title"}\n{"x":"ABC-1"}\n{"x":"ABC-1 --dry-run"}\n'


class HomeTestCase(unittest.TestCase):
    """A throwaway HOME per test, seen by in-process calls and by the CLI subprocess alike."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = pathlib.Path(directory.name)
        patcher = mock.patch.dict(os.environ, HOME=str(self.home))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *args: str, event: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "agent_history", *args], input=json.dumps(event) if event is not None else None, stdin=None if event is not None else subprocess.DEVNULL, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(SRC)))

    def cli(self, *args: str, event: dict | None = None) -> str:
        result = self.run_cli(*args, event=event)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def rows(self) -> dict[str, dict]:
        with contextlib.closing(sqlite3.connect(self.home / store.DB_NAME)) as conn:
            conn.row_factory = sqlite3.Row
            return {row["session_id"]: dict(row) for row in conn.execute("SELECT * FROM sessions")}

    def write(self, relative: str, text: str) -> pathlib.Path:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


class RecordTests(HomeTestCase):
    def claude_session(self) -> None:
        transcript = self.home / "t.jsonl"
        base = {"session_id": "s1", "transcript_path": str(transcript), "cwd": "/w1"}
        self.assertIn(store.DB_NAME, self.cli("anthropic", event={**base, "hook_event_name": "SessionStart", "source": "startup"}))
        self.cli("anthropic", event={**base, "hook_event_name": "UserPromptSubmit", "prompt": "hi"})
        transcript.write_text(CLAUDE_TRANSCRIPT)
        self.cli("anthropic", event={**base, "hook_event_name": "Stop"})
        self.cli("anthropic", event={**base, "hook_event_name": "CwdChanged", "new_cwd": "/w2"})
        self.cli("anthropic", event={**base, "cwd": "/w2", "hook_event_name": "PostModelSwitch", "to_model": "claude-b"})

    def test_hook_events_fill_the_row_by_precedence(self):
        self.claude_session()
        self.cli("anthropic", "--session", "s1", "--topic", "Manual topic")
        self.cli("anthropic", event={"session_id": "s1", "transcript_path": str(self.home / "t.jsonl"), "cwd": "/w2", "hook_event_name": "UserPromptSubmit", "prompt": "more"})  # stale transcript must not win
        row = self.rows()["s1"]
        self.assertEqual((row["model"], row["effort"], row["session_topic"], row["session_workspace"]), ("claude-b", "xhigh", "Manual topic", "/w2"))
        self.assertTrue(row["last_message_datetime"] and row["session_creation_datetime"])

    def test_claude_topic_defaults_to_ai_title(self):
        self.claude_session()
        self.assertEqual(self.rows()["s1"]["session_topic"], "Auto title")

    def test_codex_topic_is_first_prompt(self):
        self.cli("openai", event={"session_id": "s2", "cwd": "/w3", "model": "gpt-x", "hook_event_name": "UserPromptSubmit", "prompt": "Codex  first\nprompt"})
        self.cli("openai", event={"session_id": "s2", "cwd": "/w3", "hook_event_name": "UserPromptSubmit", "prompt": "second prompt"})
        row = self.rows()["s2"]
        self.assertEqual((row["model"], row["session_topic"]), ("gpt-x", "Codex first prompt"))

    def test_legacy_database_is_moved_on_first_use(self):
        with contextlib.closing(sqlite3.connect(self.home / store.LEGACY_DB_NAME)) as conn:
            conn.execute("CREATE TABLE sessions (session_id PRIMARY KEY, session_topic DEFAULT '')")
            conn.execute("INSERT INTO sessions VALUES ('old', 'Before the rename')")
            conn.commit()
        self.assertIn("Before the rename", self.cli())
        self.assertFalse((self.home / store.LEGACY_DB_NAME).exists())
        self.assertIn("old", self.rows())


class FindTests(HomeTestCase):
    def setUp(self):
        super().setUp()
        transcript = self.write("t.jsonl", CLAUDE_TRANSCRIPT)
        self.cli("anthropic", event={"session_id": "s1", "transcript_path": str(transcript), "cwd": "/work/one", "hook_event_name": "UserPromptSubmit", "prompt": "hi"})
        self.cli("openai", event={"session_id": "s2", "cwd": "/work/two", "model": "gpt-x", "hook_event_name": "UserPromptSubmit", "prompt": "Codex first prompt"})
        self.cli("anthropic", event={"session_id": "s3", "cwd": "/work/three", "hook_event_name": "SessionStart"})  # never typed in

    def test_ranked_by_transcript_line_count(self):
        out = self.cli("find", "ABC-1")
        self.assertIn("s1", out)
        self.assertTrue(out.split("\n")[1].startswith("2 "), out)

    def test_topic_hit_outranks_transcript_hits(self):
        self.assertTrue(self.cli("find", "codex").split("\n")[1].startswith("1000 "))

    def test_every_term_must_hit(self):
        self.assertNotIn("s1", self.cli("find", "ABC-1", "nowhere"))

    def test_provider_filter(self):
        out = self.cli("find", "-p", "openai", "codex")
        self.assertIn("s2", out)
        self.assertNotIn("s1", out)

    def test_dates_bound_last_message(self):
        self.assertNotIn("s1", self.cli("find", "--until", TODAY))
        self.assertIn("s1", self.cli("find", "--since", TODAY))

    def test_dates_leave_out_sessions_never_typed_in(self):
        out = self.cli("find", "--until", TOMORROW)
        self.assertIn("s1", out)
        self.assertNotIn("s3", out)

    def test_invalid_date_is_rejected(self):
        result = self.run_cli("find", "--since", "yesterday")
        self.assertEqual(result.returncode, 2)
        self.assertIn("expected YYYY-MM-DD", result.stderr)

    def test_term_starting_with_dash_is_grepped_literally(self):
        self.assertIn("s1", self.cli("find", "--", "--dry-run"))

    def test_resume_line_quotes_the_workspace(self):
        row = {"ai_provider": "anthropic", "session_id": "s9", "session_workspace": "/work/it's here"}
        self.assertEqual(find.resume_command(row), "cd '/work/it'\"'\"'s here' && claude --resume s9")


class BackfillTests(HomeTestCase):
    def test_runs_on_a_machine_without_history(self):
        self.assertIn("imported claude=0 codex=0", self.cli("backfill"))

    def test_imports_both_harnesses_once(self):
        self.write(".claude/projects/-work-one/c1.jsonl", '{"cwd":"/work/one","timestamp":"2026-09-01T10:00:00Z","message":{"model":"claude-a"},"effort":"high"}\n{"type":"ai-title","aiTitle":"Old   title"}\n{"timestamp":"2026-09-01T11:00:00Z"}\n')
        self.write(".claude/projects/-tmp/c2.jsonl", '{"cwd":"/private/tmp/claude-501/x","timestamp":"2026-09-01T10:00:00Z"}\n')
        self.write(".claude/history.jsonl", '{"sessionId":"c1","timestamp":1788256800000}\nnot json\n')
        self.write(".codex/history.jsonl", '{"session_id":"x1","ts":1788256800,"text":"Fix  the\\nbuild"}\n{"session_id":"x1","ts":1788260400,"text":"later"}\n')
        self.write(".codex/sessions/2026/09/01/rollout-2026-09-01T10-00-00-x1.jsonl", '{"timestamp":"2026-09-01T10:00:00Z","payload":{"cwd":"/work/two","model":"gpt-x"}}\n')
        self.assertIn("imported claude=1 codex=1; last_message_datetime filled on 2 rows; total=2", self.cli("backfill"))
        self.assertIn("imported claude=0 codex=0; last_message_datetime filled on 0 rows; total=2", self.cli("backfill"))
        rows = self.rows()
        self.assertEqual((rows["c1"]["model"], rows["c1"]["effort"], rows["c1"]["session_topic"], rows["c1"]["session_workspace"]), ("claude-a", "high", "Old title", "/work/one"))
        self.assertEqual((rows["x1"]["model"], rows["x1"]["session_topic"], rows["x1"]["session_workspace"]), ("gpt-x", "Fix the build", "/work/two"))
        self.assertEqual(rows["x1"]["last_message_datetime"], datetime.datetime.fromtimestamp(1788260400).strftime(store.TIME_FORMAT))


class InstallTests(HomeTestCase):
    FOREIGN = {"type": "command", "command": "bash \"$HOME/.codex/hooks/mempalace-hook.sh\" stop", "timeout": 30}

    def own_hooks(self, config: dict, event: str) -> list[str]:
        return [hook["command"] for group in config["hooks"].get(event, []) for hook in group["hooks"] if install.OWN_HOOK_RE.search(hook["command"])]

    def test_merge_keeps_foreign_hooks_and_replaces_legacy_ones(self):
        config = {"model": "opus", "hooks": {"Stop": [{"matcher": "*", "hooks": [self.FOREIGN, {"type": "command", "command": "/old/bin/agent-monitor openai"}]}], "PreCompact": [{"hooks": [self.FOREIGN]}]}}
        merged = install.merge_hooks(config, ("SessionStart", "Stop"), "/bin/agent-history openai")
        self.assertEqual(merged["model"], "opus")
        self.assertEqual(merged["hooks"]["Stop"][0], {"matcher": "*", "hooks": [self.FOREIGN]})
        self.assertEqual(merged["hooks"]["PreCompact"], [{"hooks": [self.FOREIGN]}])
        self.assertEqual(self.own_hooks(merged, "Stop"), ["/bin/agent-history openai"])
        self.assertEqual(self.own_hooks(merged, "SessionStart"), ["/bin/agent-history openai"])

    def test_merge_drops_events_it_no_longer_hooks(self):
        config = {"hooks": {"Notification": [{"hooks": [{"type": "command", "command": "agent-history anthropic"}]}]}}
        self.assertNotIn("Notification", install.merge_hooks(config, ("Stop",), "agent-history anthropic")["hooks"])

    def test_install_is_idempotent_and_non_destructive(self):
        self.write(".claude/settings.json", json.dumps({"effortLevel": "high"}))
        self.write(".codex/hooks.json", json.dumps({"description": "MemPalace", "hooks": {"Stop": [{"matcher": "*", "hooks": [self.FOREIGN]}]}}))
        install.install("/opt/bin/agent-history")
        report = install.install("/opt/bin/agent-history")
        claude = json.loads((self.home / ".claude/settings.json").read_text())
        codex = json.loads((self.home / ".codex/hooks.json").read_text())
        self.assertEqual(claude["effortLevel"], "high")
        self.assertEqual(self.own_hooks(claude, "PostModelSwitch"), ["/opt/bin/agent-history anthropic"])
        self.assertEqual(codex["hooks"]["Stop"][0]["hooks"], [self.FOREIGN])
        self.assertEqual(self.own_hooks(codex, "Stop"), ["/opt/bin/agent-history openai"])
        for harness in (".claude", ".codex"):
            self.assertIn("name: find-session", (self.home / harness / "skills/find-session/SKILL.md").read_text())
        self.assertFalse(any(line.startswith("skipped") for line in report), report)

    def test_install_writes_through_a_symlinked_settings_file(self):
        real = self.write("dotfiles/settings.json", "{}")
        (self.home / ".claude").mkdir()
        (self.home / ".claude/settings.json").symlink_to(real)
        install.install("/opt/bin/agent-history")
        self.assertTrue((self.home / ".claude/settings.json").is_symlink())
        self.assertIn("agent-history anthropic", real.read_text())

    def test_install_skips_missing_harnesses_and_real_skill_dirs(self):
        self.write(".claude/skills/find-session/SKILL.md", "hand-written")
        report = install.install("/opt/bin/agent-history")
        self.assertIn("skipped Codex: ~/.codex not found", report)
        self.assertTrue(any("already exists and is not a symlink" in line for line in report), report)
        self.assertEqual((self.home / ".claude/skills/find-session/SKILL.md").read_text(), "hand-written")

    def test_dry_run_writes_nothing(self):
        (self.home / ".claude").mkdir()
        install.install("/opt/bin/agent-history", dry_run=True)
        self.assertEqual(list(self.home.rglob("*")), [self.home / ".claude"])

    def test_invalid_settings_json_is_left_alone(self):
        self.write(".claude/settings.json", "{not json")
        with self.assertRaises(SystemExit):
            install.install("/opt/bin/agent-history")
        self.assertEqual((self.home / ".claude/settings.json").read_text(), "{not json")


if __name__ == "__main__":
    unittest.main()
