from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, installer


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
    print("\nNext: ./ae-sdlc validate")
    return 0


def cmd_work(args: argparse.Namespace) -> int:
    from . import backend_cli  # imported here so `init` does not load the MCP SDK

    return backend_cli.run(Path(args.root).resolve(), args.action, args.provider)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ae-sdlc", description="Thin command-driven AE-SDLC harness installer")
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
    p.add_argument("area", choices=["backend"])
    p.add_argument("action", choices=["connect", "doctor", "use"])
    p.add_argument("provider", choices=["linear", "jira"])
    p.set_defaults(func=cmd_work)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
