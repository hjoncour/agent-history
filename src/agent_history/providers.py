"""Per-harness knowledge: where Claude Code and Codex keep their config, which hooks they fire, how to resume."""
import dataclasses, json, pathlib, re

from agent_history import transcript


@dataclasses.dataclass(frozen=True)
class Provider:
    name: str                     # stored in ai_provider, and the hook subcommand
    harness: str
    home: str                     # config dir under $HOME
    config_file: str              # holds the default reasoning effort
    hooks_file: str
    hook_events: tuple[str, ...]
    model_re: str
    resume: str

    def path(self, name: str) -> pathlib.Path:
        return pathlib.Path.home() / self.home / name

    def config_effort(self, model: str) -> str:
        """Fallback until the first turn lands in the transcript."""
        try:
            text = self.path(self.config_file).read_text()
            if self.name == "openai":
                return transcript.last(r'model_reasoning_effort\s*=\s*"(\w+)"', text)
            settings = json.loads(text)
        except (OSError, ValueError):
            return ""
        base = re.sub(r"\[.*\]$", "", model)  # claude-x[1m] -> claude-x
        return settings.get("modelSettings", {}).get(base, {}).get("effortLevel") or settings.get("effortLevel", "")


ANTHROPIC = Provider(name="anthropic", harness="Claude Code", home=".claude", config_file="settings.json", hooks_file="settings.json", hook_events=("SessionStart", "UserPromptSubmit", "Stop", "CwdChanged", "PostModelSwitch"), model_re=r'(?:"message":\{"model"|"modelId"):"([^"<]+)"', resume="cd {session_workspace} && claude --resume {session_id}")
OPENAI = Provider(name="openai", harness="Codex", home=".codex", config_file="config.toml", hooks_file="hooks.json", hook_events=("SessionStart", "UserPromptSubmit", "Stop"), model_re=r'"model":"([^"]+)"', resume="codex resume {session_id}")
PROVIDERS = {provider.name: provider for provider in (ANTHROPIC, OPENAI)}
