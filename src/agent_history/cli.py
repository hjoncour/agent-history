"""agent-history: one row per Claude Code / Codex session in ~/.agent_history.db, kept current by harness hooks.

  hook     : <hook JSON on stdin> | agent-history anthropic|openai
  agent    : agent-history anthropic --session ID --topic "new topic"   (topic changed mid-session)
  list     : agent-history                                             (sessions, newest first)
  find     : agent-history find [-p PROVIDER] [-m MODEL] [-w WORKSPACE] [--since DATE] [--until DATE] [-n 10] [TERM ...]
             dates apply to last_message_datetime (when you last typed); every TERM must appear in the
             topic, the workspace, or the transcript, and results are ranked by how often
  backfill : agent-history backfill                                    (import sessions from before install)
  install  : agent-history install [--dry-run]                         (hooks + find-session skill, idempotent)
"""
import argparse, contextlib, datetime, json, shutil, sys

from agent_history import backfill, find, install, record, store
from agent_history.providers import PROVIDERS


def date_arg(value: str) -> str:
    """Normalized to the stored format, because since/until are compared as strings."""
    try:
        return datetime.datetime.fromisoformat(value).strftime(store.TIME_FORMAT)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD or YYYY-MM-DD HH:MM, got {value!r}") from None


def run_hook(args: argparse.Namespace) -> None:
    if not args.session and sys.stdin.isatty():
        args.parser.error("pipe hook JSON on stdin, or pass --session ID")
    event = {} if args.session else json.loads(sys.stdin.read() or "{}")
    message = record.record(PROVIDERS[args.command], event, args.session or "", args.topic or "")
    if message:
        print(message)


def run_list(args: argparse.Namespace) -> None:
    with contextlib.closing(store.connect()) as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM sessions ORDER BY last_seen DESC")]
    print(find.table(rows, store.VIEW_COLUMNS))


def run_find(args: argparse.Namespace) -> None:
    with contextlib.closing(store.connect()) as conn:
        rows = find.search(conn, args.terms, args.provider, args.model, args.workspace, args.since or "", args.until or "", args.limit)
    print(find.table(rows, ["score"] + store.VIEW_COLUMNS))
    for row in rows:
        print("\nresume:", find.resume_command(row))


def run_backfill(args: argparse.Namespace) -> None:
    print(backfill.backfill())


def run_install(args: argparse.Namespace) -> None:
    executable = shutil.which("agent-history")
    if not executable:
        args.parser.error("agent-history is not on PATH; install the package first (uv tool install . or pipx install .)")
    print("\n".join(f"[dry run] {line}" if args.dry_run else line for line in install.install(executable, args.dry_run)))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="agent-history", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    root.set_defaults(handler=run_list, parser=root)
    commands = root.add_subparsers(dest="command", metavar="COMMAND")
    for provider in PROVIDERS.values():
        hook = commands.add_parser(provider.name, help=f"record a {provider.harness} hook event")
        hook.add_argument("--session", help="session id (skips reading hook JSON from stdin)")
        hook.add_argument("--topic", help="set the session topic explicitly")
        hook.set_defaults(handler=run_hook, parser=hook)
    commands.add_parser("list", help="all sessions, newest first (the default)").set_defaults(handler=run_list)
    search = commands.add_parser("find", help="ranked search by provider, model, workspace, dates and free terms")
    search.add_argument("terms", nargs="*", metavar="TERM")
    search.add_argument("-p", "--provider", default="")
    search.add_argument("-m", "--model", default="")
    search.add_argument("-w", "--workspace", default="")
    search.add_argument("--since", type=date_arg, metavar="DATE", help="last message on/after, e.g. 2026-09-15")
    search.add_argument("--until", type=date_arg, metavar="DATE", help="last message before, e.g. 2026-09-16")
    search.add_argument("-n", "--limit", type=int, default=10)
    search.set_defaults(handler=run_find)
    commands.add_parser("backfill", help="import sessions that predate the hooks").set_defaults(handler=run_backfill)
    setup = commands.add_parser("install", help="add hooks to Claude Code / Codex and link the find-session skill")
    setup.add_argument("--dry-run", action="store_true", help="print what would change without writing anything")
    setup.set_defaults(handler=run_install, parser=setup)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.handler(args)
    return 0
