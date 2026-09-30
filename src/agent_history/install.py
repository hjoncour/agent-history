"""Wire agent-history into Claude Code and Codex: merge its hooks into their config, link the find-session skill."""
import importlib.resources, json, os, pathlib, re, shlex, shutil

from agent_history.providers import OPENAI, PROVIDERS

HOOK_TIMEOUT = 5
OWN_HOOK_RE = re.compile(r"\bagent-(?:history|monitor)\b")  # agent-monitor: installs from before the rename
SKILL = "find-session"


def merge_hooks(config: dict, events: tuple[str, ...], command: str) -> dict:
    """Swap any earlier agent-history hook for one fresh hook per event; every other hook is left as it was."""
    hooks = config.setdefault("hooks", {})
    for event in list(hooks):
        groups = []
        for group in hooks[event]:
            kept = [hook for hook in group.get("hooks", []) if not OWN_HOOK_RE.search(str(hook.get("command", "")))]
            if kept or not group.get("hooks"):
                groups.append({**group, "hooks": kept} if "hooks" in group else group)
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    for event in events:
        hooks.setdefault(event, []).append({"hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT}]})
    return config


def read_json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except ValueError as error:
        raise SystemExit(f"agent-history: {path} is not valid JSON ({error}); fix it or add the hooks by hand")


def write_json(path: pathlib.Path, data: dict) -> None:
    """Atomic, and through symlinks, so a settings file managed from a dotfiles repo stays linked."""
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.agent-history.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    if target.exists():
        shutil.copymode(target, temporary)
    os.replace(temporary, target)


def link_skill(shared: pathlib.Path, link: pathlib.Path, dry_run: bool) -> str:
    if link.exists() and not link.is_symlink():
        return f"skipped {link}: already exists and is not a symlink"
    if not dry_run:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.unlink(missing_ok=True)
        link.symlink_to(shared)
    return f"linked {link} -> {shared}"


def install(executable: str, dry_run: bool = False) -> list[str]:
    """Idempotent; returns one line per action taken (or, with dry_run, that would be taken)."""
    report = []
    shared = pathlib.Path.home() / ".agents/skills" / SKILL
    if not dry_run:
        shared.mkdir(parents=True, exist_ok=True)
        (shared / "SKILL.md").write_text((importlib.resources.files("agent_history") / "skills" / SKILL / "SKILL.md").read_text())
    report.append(f"wrote {shared / 'SKILL.md'}")
    for provider in PROVIDERS.values():
        if not provider.path(".").is_dir():
            report.append(f"skipped {provider.harness}: ~/{provider.home} not found")
            continue
        hooks_file = provider.path(provider.hooks_file)
        config = merge_hooks(read_json(hooks_file), provider.hook_events, f"{shlex.quote(executable)} {provider.name}")
        if not dry_run:
            write_json(hooks_file, config)
        report.append(f"hooked {', '.join(provider.hook_events)} in {hooks_file}")
        if provider is OPENAI:
            report.append("run /hooks inside Codex once to trust the new hooks")
        report.append(link_skill(shared, provider.path("skills") / SKILL, dry_run))
    return report
