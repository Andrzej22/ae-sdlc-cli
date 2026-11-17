from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import __version__, installer

# Commands implemented by the ./ae-sdlc file that `init` writes into a repository.
_PROJECT_COMMANDS = {"prepare-work", "execute-work", "workflow", "status", "validate", "backend", "work"}

_PROJECT_HELP = """
Inside a repository, after ae-sdlc init:

  ae-sdlc validate
  ae-sdlc status [--ready|--blocked] [SCOPE]
  ae-sdlc prepare-work "<request>"
  ae-sdlc execute-work SPEC-001
  ae-sdlc workflow nightly EPIC-001
  ae-sdlc backend {connect,use,show,doctor}

ae-sdlc <command> --help shows that command's options.
The repository file ./ae-sdlc runs the same commands. Skills call the installed ae-sdlc command.
"""


def _choose_interactively() -> list[str]:
    names = list(installer.PROVIDERS)
    print("Providers to install:")
    for i, n in enumerate(names, 1):
        print(f"  {i}. {n} ({installer.PROVIDERS[n][2]})")
    raw = input("Numbers or names, comma separated, or 'all': ").strip()
    return _parse(raw)


def _parse(raw: str) -> list[str]:
    names = list(installer.PROVIDERS)
    if raw.strip().lower() == "all":
        return names
    picked: list[str] = []
    for tok in (t.strip() for t in raw.split(",") if t.strip()):
        name = names[int(tok) - 1] if tok.isdigit() and 0 < int(tok) <= len(names) else tok
        if name not in installer.PROVIDERS:
            raise SystemExit(f"unknown provider: {tok} (available: {', '.join(names)})")
        if name not in picked:
            picked.append(name)
    if not picked:
        raise SystemExit("no providers selected")
    return picked


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.dir).resolve()
    if not target.is_dir():
        raise SystemExit(f"not a directory: {target}")
    if args.providers:
        providers = _parse(args.providers)
    elif sys.stdin.isatty():
        providers = _choose_interactively()
    else:
        raise SystemExit("--providers is required in non-interactive mode (e.g. --providers claude,codex or all)")

    plan = installer.build_plan(providers, target)
    res = installer.apply(plan, target, dry_run=args.dry_run)

    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}ae-sdlc init -> {target}  (providers: {', '.join(providers)})")
    print(f"{prefix}created {len(res.created)}, unchanged {len(res.unchanged)}, conflicts {len(res.conflicts)}")
    if args.dry_run or args.verbose:
        for rel in res.created:
            print(f"  + {rel}")
    if res.conflicts:
        print("\nExisting files were left untouched. Harness versions were written next to them;")
        print("merge by hand, then delete the sidecar:")
        for rel in res.conflicts:
            print(f"  ! {rel[:-len(installer.SIDECAR)]}  <-  {rel}")
    print("\nNext: ae-sdlc validate")
    return 0


def _split_project_command(argv: list[str]) -> tuple[Path | None, list[str]] | None:
    """Split a leading `--dir` from a project command. `init` and `_work` stay with argparse.

    Only flags before the first positional are inspected, so `_work --root <dir> work`
    is not mistaken for the project `work` command.
    """
    explicit: Path | None = None
    forwarded: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("--dir", "-d", "--provider", "-p"):
            if i + 1 >= len(argv):
                return None
            if tok in ("--dir", "-d"):
                explicit = Path(argv[i + 1])
            else:
                forwarded.extend((tok, argv[i + 1]))
            i += 2
            continue
        if tok.startswith("--dir="):
            explicit = Path(tok.split("=", 1)[1])
            i += 1
            continue
        if tok.startswith("--provider="):
            forwarded.append(tok)
            i += 1
            continue
        if tok in _PROJECT_COMMANDS:
            return explicit, [*forwarded, *argv[i:]]
        return None
    return None


def _project_root(start: Path) -> Path:
    start = start.resolve()
    if start.is_file():
        start = start.parent
    for candidate in (start, *start.parents):
        if (candidate / "ae-sdlc").is_file() and (candidate / ".ae-sdlc" / "manifest.json").is_file():
            return candidate
    raise SystemExit(
        "ae-sdlc: no initialized project found (looked for ae-sdlc and .ae-sdlc/manifest.json).\n"
        "Run ae-sdlc init in the repository first."
    )


def _run_project(root: Path, argv: list[str]) -> int:
    script = root / "ae-sdlc"
    try:
        return subprocess.run([str(script), *argv], cwd=root).returncode
    except PermissionError:
        return subprocess.run([sys.executable, str(script), *argv], cwd=root).returncode
    except OSError as exc:
        raise SystemExit(f"ae-sdlc: cannot run {script}: {exc}") from exc


def cmd_work(args: argparse.Namespace) -> int:
    from . import backend_cli  # imported here so `init` does not load the MCP SDK

    root = Path(args.root).resolve()
    if args.area == "work":
        return backend_cli.run_work(root, args.argv)
    if args.area == "workflow":
        return backend_cli.run_workflow(root, args.argv)
    if args.area == "status":
        return backend_cli.run_status(root, args.scope, args.status)
    return backend_cli.run(root, args.action, args.provider)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    project = _split_project_command(argv)
    if project is not None:
        explicit, forwarded = project
        start = Path.cwd() if explicit is None else explicit
        if explicit is not None and not start.is_dir():
            raise SystemExit(f"not a directory: {start}")
        return _run_project(_project_root(start), forwarded)

    ap = argparse.ArgumentParser(
        prog="ae-sdlc",
        description="Install and run the AE-SDLC harness.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_PROJECT_HELP,
    )
    ap.add_argument("--version", action="version", version=f"ae-sdlc {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="install the harness into a project")
    p.add_argument("--providers", "-p", help=f"comma-separated: {', '.join(installer.PROVIDERS)}, or 'all'")
    p.add_argument("--dir", "-d", default=".", help="target project directory (default: .)")
    p.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    p.add_argument("--verbose", "-v", action="store_true", help="list created files")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("_work", help=argparse.SUPPRESS)  # internal: called by the generated ./ae-sdlc
    p.add_argument("--root", required=True)
    p.set_defaults(func=cmd_work)
    areas = p.add_subparsers(dest="area", required=True)
    area = areas.add_parser("backend")
    area.add_argument("action", choices=["connect", "doctor", "use"])
    area.add_argument("provider", choices=["linear", "jira"])
    area = areas.add_parser("status")
    area.add_argument("scope", nargs="?")
    area.add_argument("--status", choices=["READY", "BLOCKED"])
    for name in ("work", "workflow"):
        area = areas.add_parser(name, add_help=False)
        area.add_argument("argv", nargs=argparse.REMAINDER)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
