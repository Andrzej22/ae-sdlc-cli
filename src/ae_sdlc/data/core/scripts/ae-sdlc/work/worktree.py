"""Snapshots of the git working tree, so the runner can see what an agent run really changed.

A snapshot is the tree object of the whole working tree (tracked and untracked files, minus ignored ones),
written through a temporary copy of the index. The real index, HEAD and files are never touched, and commits
the agent makes do not hide its changes. Content of new or modified files is stored as loose objects that
`git gc` removes later.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _git(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, env=env, check=False)


def snapshot(root: Path) -> str | None:
    """Return a tree id for the current working tree, or None when it is not a git work tree."""
    if shutil.which("git") is None or _git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        return None
    index = _git(root, "rev-parse", "--path-format=absolute", "--git-path", "index").stdout.strip()
    with tempfile.TemporaryDirectory(prefix="ae-sdlc-index-") as tmp:
        temp_index = Path(tmp) / "index"
        if index and Path(index).exists():
            shutil.copyfile(index, temp_index)  # reuse the stat cache, so only changed files are hashed
        env = {**os.environ, "GIT_INDEX_FILE": str(temp_index)}
        if _git(root, "add", "--all", ".", env=env).returncode != 0:
            return None
        tree = _git(root, "write-tree", env=env)
    return tree.stdout.strip() if tree.returncode == 0 else None


def changed_paths(root: Path, before: str | None, after: str | None) -> set[str] | None:
    """Paths, relative to `root`, that differ between two snapshots; None when either is missing."""
    if not before or not after:
        return None
    diff = _git(root, "diff", "--relative", "--name-only", "--no-renames", "-z", before, after)
    if diff.returncode != 0:
        return None
    return {p for p in diff.stdout.split("\0") if p}


def is_ignored(root: Path, path: str) -> bool:
    return _git(root, "check-ignore", "-q", "--", path).returncode == 0
