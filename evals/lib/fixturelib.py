#!/usr/bin/env python3
# Description: Declarative git fixture builder for the tests and evals: a real repo in a temp dir,
# Description: a local bare origin, an exact commit sequence, and never a push. Stdlib only.

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# fixture trees store dotfiles under safe names so they commit cleanly into this repo
NAME_MAP = {
    "mcp.json": ".mcp.json",
    "settings.json": ".claude/settings.json",
    "settings.local.json": ".claude/settings.local.json",
    "dotenv": ".env",
    "gitignore": ".gitignore",
}
DROP = {"README-fixture.md"}

IDENTITY = (
    "-c",
    "user.name=fixture",
    "-c",
    "user.email=fixture@example.invalid",
    "-c",
    "commit.gpgsign=false",
    # an unset core.excludesFile falls back to $XDG_CONFIG_HOME/git/ignore; pin it to nothing
    "-c",
    "core.excludesFile=/dev/null",
)


class FixtureError(RuntimeError):
    """A description that cannot be built. Raised before any git command runs."""


@dataclass(frozen=True)
class Fixture:
    """A repo described as files plus an ordered commit sequence. Paths are final
    (dotted) paths in the built tree; the order of `commits` is the history, so a
    file committed before the ignore rule that names it ends up tracked and ignored."""

    name: str
    files: dict[str, str]
    commits: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass
class BuiltRepo:
    name: str
    path: Path
    origin: Path
    root: Path


def _env() -> dict[str, str]:
    """The operator's git config never reaches a fixture: no global excludes file, no
    system config, no prompts. Built at call time so a test's PATH override is honoured."""
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=cwd, env=_env(), capture_output=True, text=True, check=True
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return _run(["git", "-C", str(repo), *IDENTITY, *args])


def validate(fixture: Fixture) -> None:
    if not fixture.commits:
        raise FixtureError(f"{fixture.name}: a fixture needs at least one commit")
    seen: set[str] = set()
    for message, paths in fixture.commits:
        for p in paths:
            if p not in fixture.files:
                raise FixtureError(f"{fixture.name}: commit {message!r} names undeclared {p}")
            seen.add(p)
    uncommitted = set(fixture.files) - seen
    if uncommitted:
        raise FixtureError(f"{fixture.name}: files never committed: {sorted(uncommitted)}")


def build(fixture: Fixture, root: Path | None = None, dirname: str | None = None) -> BuiltRepo:
    """Materialise `fixture` under `root` (a fresh temp dir when None) as
    `<root>/<dirname>` with a bare `<root>/<dirname>.origin.git` as its origin."""
    validate(fixture)
    root = Path(tempfile.mkdtemp(prefix="d6-fixture-")) if root is None else Path(root)
    root.mkdir(parents=True, exist_ok=True)
    dirname = dirname or fixture.name
    work = root / dirname
    origin = root / f"{dirname}.origin.git"
    if origin.exists():
        raise FixtureError(f"{fixture.name}: {origin} already exists")
    if work.exists():
        # a scaffold populates the run's empty working directory in place
        if any(work.iterdir()):
            raise FixtureError(f"{fixture.name}: {work} exists and is not empty")
    else:
        work.mkdir()
    _run(["git", *IDENTITY, "init", "-q", "--bare", str(origin)])
    _run(["git", *IDENTITY, "init", "-q", str(work)])
    _git(work, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(work, "remote", "add", "origin", str(origin))
    for message, paths in fixture.commits:
        for p in paths:
            dest = work / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(fixture.files[p], encoding="utf-8")
        _git(work, "add", "--", *paths)
        _git(work, "commit", "-q", "-m", message)
    return BuiltRepo(name=fixture.name, path=work, origin=origin, root=root)


def load_tree(tree: Path) -> dict[str, str]:
    """Read a stored fixture tree, mapping safe names onto their real dotted paths."""
    files: dict[str, str] = {}
    for p in sorted(tree.rglob("*")):
        if not p.is_file() or p.name in DROP:
            continue
        rel = p.relative_to(tree).as_posix()
        files[NAME_MAP.get(rel, rel)] = p.read_text(encoding="utf-8")
    return files
