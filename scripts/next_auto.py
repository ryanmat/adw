#!/usr/bin/env python3
# Description: Unattended /adw:next auto orchestrator: every git and gh op of a run, plus run state.
# Description: Stdlib only; imports next_plan. gh is a single seam; git runs against real repos.

from __future__ import annotations

import sys

if sys.version_info < (3, 12):  # noqa: UP036  (the guard runs below the floor by design)
    sys.stderr.write(
        f"adw: python3 is {sys.version_info[0]}.{sys.version_info[1]}; "
        "the plugin needs 3.12 or later on PATH\n"
    )
    sys.exit(2)

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import next_plan

OWNERS_VAR = "ADW_OWNERS"
NEVER_AUTO_VAR = "ADW_NEVER_AUTO"


def _env_set(name: str) -> frozenset[str]:
    """The comma-separated set in the environment variable `name`; empty when it is unset."""
    return frozenset(p.strip() for p in os.environ.get(name, "").split(",") if p.strip())


# The GitHub owners whose repositories an unattended run may target, from ADW_OWNERS in the
# user's settings. Nothing is allowed until it is set: preflight, start and merge refuse.
OWNER = _env_set(OWNERS_VAR)
# Repositories refused by name before any gh call, whatever their plan says: owner/name slugs
# from ADW_NEVER_AUTO.
NEVER_AUTO = _env_set(NEVER_AUTO_VAR)


def owner_reason(owner: str | None, *, noun: str = "origin owner") -> str | None:
    """The refusal for `owner`, or None when the allow-list admits it."""
    if not OWNER:
        return (
            f"{OWNERS_VAR} is unset; set it to the GitHub owners whose repositories auto runs"
            " may target"
        )
    if owner not in OWNER:
        return f"{noun} is {owner!r}, not one of {', '.join(sorted(OWNER))}"
    return None
# The merge gate requires the preflight plugin's refs/notes/preflight note on the head only
# when this variable is exactly "required"; unset, the gate never reads notes (sweep spec R1).
PREFLIGHT_NOTE_VAR = "ADW_PREFLIGHT_NOTE"
# the suffix of the RUN STOPPED line when the run committed no step and so opened no pull
# request (carries spec R11); the factory's stream regex leaves its PR group empty on it
NO_PR_SUFFIX = "NO PR: no step committed"
# Outside .claude/: Claude Code treats every path under .claude/ as a sensitive file and
# denies tool writes there in non-interactive mode, which would leave the implementer
# unable to write into the run worktree.
RUN_SUBDIR = ".next-auto"
# attended-mode records live beside the run dirs and never under a state.json marker, so
# the guard hook stays unarmed and auto preflight's no-run rule is unaffected
ATTENDED_SUBDIR = "attended"
MAX_BYTES = 1_048_576
# the gate-config files, one source with the plan grammar's plan-time warning
GATE_CONFIG_FILES = next_plan.GATE_CONFIG_FILES
# the run cap, one source with the plan grammar's per-step cap (next_plan.RUN_SIZE_LIMIT)
RUN_SIZE_LIMIT = next_plan.RUN_SIZE_LIMIT
# steps one `/adw:next auto` invocation works by default: `start`'s --steps, and the batch
# preflight reads when it refuses a plan whose next steps include a Test-repo step
DEFAULT_STEPS = 3
# seconds an exempt step's external Test may run before its process group is killed: the Stop
# hook's TEST_LIMIT (hooks/stop-gate.py), below the 600 s hooks.json gives the hook. The hook
# loads this module only after it has found a plan and its tests patch its own constant, so the
# value is defined twice and held equal by test (tests/test_next_auto.py).
EXTERNAL_TEST_LIMIT = 540
# The delivery record (ai-dlc-record R1): docs/records/<slug>.md, appended through the plan's
# Record command. A commit that changes only files under this directory is a record commit,
# which the merge gate walks past to the reviewed commit (R12).
RECORD_DIR = "docs/records"
# the plugin root, where the manifest and the agent definitions the record names are read
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
# the correctness verdicts `merge <n>` accepts for a PR head, recorded by the loop under
# .next-auto/attended/: the chunk reviewer's Approved in verdict-<sha>.json, or the done
# check's APPROVED over the done claim in its own done-<sha>.json
VERDICT_REVIEWER = "adw:next-reviewer"
DONE_REVIEWER_VAR = "ADW_DONE_REVIEWER"
# the reviewer a done-check verdict record names: `verdict` writes it and `merge` requires it
DONE_REVIEWER = os.environ.get(DONE_REVIEWER_VAR, "done-check")
PR_VIEW_FIELDS = (
    "headRefOid,baseRefName,additions,deletions,files,reviewDecision,mergeStateStatus,isDraft"
)
# The security surface `merge <n>` never merges on its own (Requirement 19): paths by
# directory component or basename glob, and the hunks that touch the guard's own names.
# A timer line is the surface only under hooks/ and scripts/, where the guard and the
# gate live; a client timeout elsewhere is ordinary code.
SECURITY_SURFACE_DIRS = frozenset({"hooks", "rulesets"})
SECURITY_SURFACE_PATH_PREFIXES = (".github/workflows/",)
SECURITY_SURFACE_GLOBS = (
    "settings*.json",
    ".pre-commit-config.yaml",
    ".env*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
)
SECURITY_SURFACE_SYMBOLS = (
    "OWNER",
    "NEVER_AUTO",
    "SECRET_PATTERNS",
    "scan_secrets",
    "_scrub_paths",
)
SECURITY_SURFACE_TIMER_DIRS = ("hooks/", "scripts/")
PR_BODY_NAME = "pr-body.md"
# Secret shapes the gate and wrap-up refuse. Named so a hit says which family, never the
# match itself. The bare word token matches nothing; an assignment needs a 17+ character
# value with no `$`, `<`, or `{`, so `GH_TOKEN=${FACTORY_GH_TOKEN}` and `TOKEN=<pat>` pass.
# An unquoted value directly followed by `(` is a call, not a secret, so
# `API_KEY: ecs.Secret.fromSecretsManager(apiKey)` passes too, unless the call's first
# argument is itself a quoted 17+ character literal, as in `unsafePlainText("...")`. A quoted
# value matches whatever follows it, since a `(` inside a string is data. A name opening
# `${` is a shell default: a 17+ character literal after `:-`, `-`, `:=`, `=` or `:` matches,
# quoted or not, with space or a newline around the operator, and a default starting with `/`
# or `.` is a path, such as an SSM parameter's name, and passes. Whole files are scanned, not
# added lines, so a step that edits a file already holding a secret fails.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("sk- key", re.compile(r"\bsk-[A-Za-z0-9_-]{32,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("Daytona key", re.compile(r"\bdtn_[A-Za-z0-9]{20,}")),
    ("PEM private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Authorization Bearer", re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    (
        "KEY/TOKEN/SECRET assignment",
        re.compile(
            r"(?<!\$\{)\b[A-Za-z0-9_]*(?:KEY|TOKEN|SECRET)\s*[=:]\s*"
            r"(?:['\"][A-Za-z0-9_./+-]{17,}"
            r"|[A-Za-z0-9_./+-]{17,}(?![A-Za-z0-9_./+-])"
            r"(?!\((?!\s*['\"][A-Za-z0-9_./+-]{17,})))"
            r"|\$\{[A-Za-z0-9_]*(?:KEY|TOKEN|SECRET)\s*(?::?[-=]|:(?![-=]))\s*"
            r"['\"]?[A-Za-z0-9_+-][A-Za-z0-9_./+-]{16,}"
        ),
    ),
)
# Absolute paths a PR body on a public repo must not carry: the run root becomes <repo>,
# anything else under a user or temp root becomes <path>.
_ABS_PATH_RE = re.compile(
    r"(?<![\w/<])/(?:Users|home|private|tmp|var|opt|mnt|root|srv)/[^\s`'\")\]]*"
)
# subcommands that only make sense inside a run: each starts by reading state.json
# (state included, so no run is the same one-line refusal there, not {} with exit 0)
RUN_COMMANDS = frozenset(
    {
        "state",
        "size",
        "step-begin",
        "gate",
        "prove-red",
        "commit",
        "review-package",
        "revert",
        "set",
        "append",
        "wrap-up",
    }
)


# ---- git helpers ------------------------------------------------------------


def git(
    root: Path | str, *args: str, check: bool = True, index: Path | None = None
) -> subprocess.CompletedProcess:
    """One git call at `root`; `index` points it at an index file other than the repo's own."""
    env = None if index is None else {**os.environ, "GIT_INDEX_FILE": str(index)}
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=check, env=env
    )


def gh(args: list[str], root: Path | None = None) -> subprocess.CompletedProcess:
    """The single gh seam. All GitHub calls go through here; the eval exercises the real path.
    A missing binary is reported as a failed call so callers fail closed with one line."""
    try:
        return subprocess.run(
            ["gh", *args], capture_output=True, text=True, cwd=str(root) if root else None
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            ["gh", *args], 127, stdout="", stderr="gh: command not found on PATH"
        )


def gh_repo_view(root: Path) -> dict:
    r = gh(["repo", "view", "--json", "owner,isFork,defaultBranchRef"], root=root)
    if r.returncode != 0:
        raise RuntimeError(f"gh repo view failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


def list_next_auto_prs(root: Path) -> tuple[list[tuple[int, str]] | None, str]:
    """Open PRs labeled next-auto as (number, head branch), or (None, cause) when gh cannot
    say. A gh outage used to read as "no open PR", which switched the one-open-PR gate off
    exactly when the operator was not looking."""
    fields = ["--json", "number,headRefName"]
    r = gh(["pr", "list", "--label", "next-auto", "--state", "open", *fields], root=root)
    if r.returncode != 0:
        detail = (r.stderr.strip() or r.stdout.strip() or f"gh exited {r.returncode}").splitlines()
        return None, detail[-1]
    try:
        rows = json.loads(r.stdout)
        return [(int(item["number"]), str(item["headRefName"])) for item in rows], ""
    except (json.JSONDecodeError, TypeError, KeyError, ValueError):
        return None, "gh pr list returned no JSON"


def parse_origin_owner(url: str) -> str | None:
    url = (url or "").strip()
    m = re.search(r"github\.com[/:]([^/]+)/", url)
    return m.group(1) if m else None


def origin_owner(root: Path) -> str | None:
    r = git(root, "remote", "get-url", "origin", check=False)
    return parse_origin_owner(r.stdout) if r.returncode == 0 else None


def parse_origin_repo(url: str) -> str | None:
    """`owner/repo` from a GitHub origin URL, or None for anything else."""
    url = (url or "").strip()
    m = re.search(r"github\.com[/:]([^/]+)/([^/\s]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def origin_repo(root: Path) -> str | None:
    r = git(root, "remote", "get-url", "origin", check=False)
    return parse_origin_repo(r.stdout) if r.returncode == 0 else None


def _ignore_probe(pattern: str) -> str:
    # a trailing-slash pattern matches directories; probe a child so check-ignore can match
    return pattern.rstrip("/") + "/probe" if pattern.endswith("/") else pattern


def ensure_ignored(root: Path, pattern: str) -> bool:
    probe = _ignore_probe(pattern)
    if git(root, "check-ignore", probe, check=False).returncode == 0:
        return True
    # resolved through git so a linked worktree (where .git is a file) reaches the
    # common dir's exclude, which every worktree of the repo shares
    raw = Path(git(root, "rev-parse", "--git-path", "info/exclude").stdout.strip())
    exclude = raw if raw.is_absolute() else root / raw
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    if pattern not in existing:
        with exclude.open("a") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(pattern + "\n")
    return git(root, "check-ignore", probe, check=False).returncode == 0


def plan_is_tracked(root: Path, plan_name: str) -> bool:
    """True when the plan is in the index at the root. project-a and project-b keep theirs
    untracked on purpose (info/exclude, public mirrors), and an untracked plan never
    reaches a worktree built from the default branch unless start carries it over."""
    return git(root, "ls-files", "--error-unmatch", plan_name, check=False).returncode == 0


def repo_is_private(slug: str, cwd: Path) -> bool | None:
    """GitHub's answer for the repo's visibility, or None when gh cannot say. Callers
    treat None as public: posting the terse body is never a leak."""
    r = gh(["repo", "view", slug, "--json", "isPrivate"], root=cwd)
    if r.returncode != 0:
        return None
    try:
        value = json.loads(r.stdout).get("isPrivate")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, bool) else None


# ---- secrets ------------------------------------------------------------------


def scan_secrets(text: str) -> list[str]:
    """The names of every secret family that matches in `text`, in pattern order."""
    return [name for name, rx in SECRET_PATTERNS if rx.search(text)]


def _scrub_paths(text: str, root: str) -> str:
    return _ABS_PATH_RE.sub("<path>", text.replace(root, "<repo>"))


# ---- state ------------------------------------------------------------------


def run_base(root: Path) -> Path:
    return root / RUN_SUBDIR


def find_state_file(root: Path) -> Path | None:
    base = run_base(root)
    if not base.is_dir():
        return None
    hits = sorted(base.glob("*/state.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def load_state(root: Path) -> dict | None:
    sf = find_state_file(root)
    if sf is None:
        return None
    return json.loads(sf.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    run_dir = Path(state["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    tmp = run_dir / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(run_dir / "state.json")


# ---- delivery record ----------------------------------------------------------


def plan_record(plan: next_plan.Plan) -> tuple[list[str], str] | None:
    """The plan's Record line as (appender command, slug): the slug is the value's last word
    and the command is the rest. None when the plan carries no Record."""
    words = shlex.split(plan.conventions.get(next_plan.RECORD_KEY, ""))
    return (words[:-1], words[-1]) if words else None


def record_path(slug: str) -> str:
    return f"{RECORD_DIR}/{slug}.md"


def _run_appender(argv: list[str], tree: Path) -> subprocess.CompletedProcess:
    """A command that cannot start is reported as a failed call, as the gh seam does."""
    try:
        return subprocess.run(argv, cwd=str(tree), capture_output=True, text=True)
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 127, stdout="", stderr=f"{argv[0]}: {exc}")


def _tracked_file(tree: Path, word: str) -> str | None:
    """The tree-relative path when `word` names one file the tree tracks, else None. git
    lists a tracked directory's files even when the working tree has deleted it, so the one
    name it lists must be the word's own path."""
    r = git(tree, "--literal-pathspecs", "ls-files", "--", word, check=False)
    names = r.stdout.splitlines()
    own = os.path.relpath(os.path.normpath(os.path.join(tree, word)), tree)
    ok = r.returncode == 0 and names == [own] and not (tree / word).is_dir()
    return names[0] if ok else None


def _default_branch_ref(root: Path, default: str | None) -> tuple[str | None, str | None]:
    """origin/<default> for a run, origin/HEAD's branch otherwise, or why there is none."""
    if default:
        return f"origin/{default}", None
    r = git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
    if r.returncode != 0 or not r.stdout.strip():
        return None, (
            "cannot find the default branch's appender: origin/HEAD is not set; "
            "run git remote set-head origin -a"
        )
    return r.stdout.strip(), None


def link_node_modules(tree: Path, scratch: Path) -> None:
    """Give a scratch worktree every node_modules the tree has beside a directory the scratch
    holds, each linked at its own path, where the scratch has none; removing the scratch
    removes only the links. The scratch holds the base's tracked directories and the ones the
    step's tests were written into, so walking it, not the tree, skips ignored build output
    such as cdk.out; a nested install (infra/node_modules) is linked like the top one."""
    for dirpath, dirnames, _files in os.walk(scratch):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
        here = Path(dirpath)
        deps = tree / here.relative_to(scratch) / "node_modules"
        dest = here / "node_modules"
        if deps.is_dir() and not dest.exists() and not dest.is_symlink():
            dest.symlink_to(deps.resolve(), target_is_directory=True)


def run_record(
    root: Path,
    tree: Path,
    record: tuple[list[str], str],
    subcommand: str,
    *flags: str,
    default: str | None = None,
) -> subprocess.CompletedProcess:
    """Call the appender as `<command> <subcommand> <slug> [flags]`, cwd at the tree that holds
    the record, as the appender is committed on origin's default branch (ai-dlc-record R12): a
    step may rewrite the tree's copy, never the one that runs. Each word of the command that
    names a file the tree tracks becomes that file in a scratch worktree at origin/<default>,
    which also gets the tree's node_modules and is removed on every exit path. A command that
    names no such file has no committed copy to run, so it is apparatus, never run as given.
    What cannot run is reported as a failed call."""
    command, slug = record
    tracked = {w: name for w in command if (name := _tracked_file(tree, w))}
    if not tracked:
        return subprocess.CompletedProcess(
            command, 2, stdout="", stderr=(
                "the Record command names no tracked file, so it cannot run as committed on "
                "the default branch"
            ),
        )  # fmt: skip
    base, problem = _default_branch_ref(root, default)
    if problem:
        return subprocess.CompletedProcess(command, 2, stdout="", stderr=problem)
    try:
        with scratch_worktree(root, base) as scratch:
            link_node_modules(tree, scratch)
            argv = [str(scratch / tracked[w]) if w in tracked else w for w in command]
            return _run_appender([*argv, subcommand, slug, *flags], tree)
    except subprocess.CalledProcessError as exc:
        detail = _last_line(exc.stderr or "")
        return subprocess.CompletedProcess(
            command, 2, stdout="", stderr=f"cannot check out {base} for the appender: {detail}"
        )


def _record_detail(r: subprocess.CompletedProcess) -> str:
    return f"exited {r.returncode}: {_last_line(r.stderr or r.stdout)}"


class DefinitionError(RuntimeError):
    """The plugin manifest or an agent definition the record names cannot be read."""


class ProveRedApparatusError(RuntimeError):
    """Prove-red's base run failed only because the scratch could not load a module the step
    does not add, so the red proves nothing and the step cannot fix it (ruled D1: apparatus)."""


class ExternalTestUnavailableError(RuntimeError):
    """An exempt step's external Test could not run: the command does not exist, or ssh itself
    failed (exit 255, a connection and not a test result), so nothing was tested."""


class ExternalTestTimeoutError(RuntimeError):
    """An exempt step's external Test ran past its limit and was killed with its group."""


class LintUnavailableError(RuntimeError):
    """check N's Lint could not start, so nothing was linted: apparatus, as a prove-red base
    that cannot load a module the step does not add is."""


def agent_identity(agent: str) -> tuple[str, str]:
    """(`<plugin>:<name>`, model) for one of the plugin's agents, read from the plugin
    manifest and the definition's frontmatter, so the record names what the loop dispatched.
    Raises DefinitionError, which the callers report as apparatus on one line."""
    try:
        manifest = (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        text = (PLUGIN_ROOT / "agents" / f"{agent}.md").read_text(encoding="utf-8")
        fields = {}
        for line in text.split("---\n", 2)[1].splitlines():
            key, sep, value = line.partition(":")
            if sep:
                fields[key.strip()] = value.strip()
        return f"{json.loads(manifest)['name']}:{fields['name']}", fields["model"]
    except (OSError, ValueError, KeyError, IndexError) as exc:
        raise DefinitionError(f"cannot read the {agent} definition: {exc}") from exc


def plan_approval(root: Path, plan: str) -> tuple[int, str]:
    """Whether the record approves this plan's hash (ai-dlc-record R13): (0, "") when it does
    or the plan carries no Record, (1, reason) naming the missing approval, the hash the
    engine computed, and the appender's last stderr line when it printed one, (2, detail)
    when the record file is missing, `approved` cannot run, or its exit and stdout are not 0
    or 1 with exactly the engine's hash."""
    text = (root / plan).read_bytes().decode("utf-8")
    record = plan_record(next_plan.parse_plan(text))
    if record is None:
        return 0, ""
    command, slug = record
    if not (root / record_path(slug)).is_file():
        init = shlex.join([*command, "init", slug])
        return 2, f"no record at {record_path(slug)}; run {init} first"
    r = run_record(root, root, record, "approved", "--plan", plan)
    expected = next_plan.plan_hash(text)
    # the appender prints the hash it computed either way; any other answer is not one
    if r.returncode == 0 and r.stdout.strip() == expected:
        return 0, ""
    if r.returncode == 1 and r.stdout.strip() == expected:
        said = f" (appender: {_last_line(r.stderr)})" if r.stderr.strip() else ""
        return 1, (
            f"no plan.approved line in {record_path(slug)} for "
            f"{expected}; approve the plan on the record first{said}"
        )
    return 2, f"record approved {_record_detail(r)}"


def attended_result_path(root: Path, n: int) -> Path:
    return attended_dir(root) / f"result-{n}.json"


def run_result_path(state: dict, n: int) -> Path:
    return Path(state["run_dir"]) / f"result-{n}.json"


def stage_check_index(root: Path, index: Path, seed: Path) -> None:
    """Stage the tree into `index` as the commit will take it, never writing the real index.
    `index` starts from the real index's entries without their stat data, so a path the step
    untracked or force-added counts as it will in the commit, and every file is hashed."""
    # the real index's tree, read through a copy of it: read-tree of that tree gives the
    # check index the same entries, with no stat data to trust
    real = git(root, "rev-parse", "--path-format=absolute", "--git-path", "index").stdout
    if Path(real.strip()).is_file():
        shutil.copyfile(real.strip(), seed)
        tree = git(root, "write-tree", index=seed).stdout.strip()
        git(root, "read-tree", tree, index=index)
    stage_all(root, index=index)


def tree_hash(root: Path, plan: str) -> str:
    """The tree as it stands, hashed as `check N` hashes the tree it records as passed_tree:
    staged into a scratch index outside the tree, the plan left out (ai-dlc-record R14)."""
    with tempfile.TemporaryDirectory() as scratch:
        index, seed = Path(scratch) / "index", Path(scratch) / "seed"
        stage_check_index(root, index, seed)
        return staged_tree(root, [plan], index=index)


def staged_tree(tree: Path, exclude: list[str], index: Path | None = None) -> str:
    """A hash of the index's paths and blob ids, the tree a result file vouches for, leaving
    out only the plan, which the loop edits between a pass and its commit (the step's done
    note). The record stays in: nothing but the engine's append after commit writes it."""
    entries = git(tree, "ls-files", "-s", "-z", index=index).stdout.split("\0")
    kept = [e for e in entries if e and e.split("\t", 1)[1] not in exclude]
    return sha256_bytes("\0".join(kept).encode("utf-8"))


def write_result(
    path: Path,
    n: int,
    mode: str,
    head: str,
    test_out: str,
    lint: str,
    *,
    tree: str,
    size_estimate: int | None = None,
    size_changed: int | None = None,
    external: dict | None = None,
    test_repo: str | None = None,
    guard_mutation: dict | None = None,
    red: str | None = None,
    red_reason: str | None = None,
) -> None:
    """A passing step's evidence for the record (ai-dlc-record R11). `red` is `proved`
    because prove-red is a pass condition of both check and gate, or `external` under the
    Test-repo exemption, where the file also carries the repository label and the external
    command with its exit and last line; `tree` is the staged tree the pass covered; an auto
    step also names the implementer definition that ran it. A step proved by its guard
    mutation keeps `red: proved` and carries `guard_mutation` (path, old, new). `red` and
    `red_reason` name a pass that ran no prove-red, as a comment-only fix round.
    `size_estimate` is the step's Size: line and `size_changed` the measured count, insertions
    plus deletions against the base, the pair the planner's size rule is re-fitted from."""
    passed, collected, _skipped, _xfailed = summary_counts(test_out)
    result: dict = {
        "step": n,
        "mode": mode,
        "head": head,
        "passed": passed,
        "collected": collected,
        "red": red or ("proved" if external is None else "external"),
        "lint": lint,
        "tree": tree,
        "size_estimate": size_estimate,
        "size_changed": size_changed,
    }
    if external is not None:
        result["test_repo"] = test_repo
        result["external_test"] = external
    if guard_mutation is not None:
        result["guard_mutation"] = guard_mutation
    if red_reason is not None:
        result["red_reason"] = red_reason
    if mode == "auto":
        result["agent"], result["model"] = agent_identity("next-implementer")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")


def mutation_value(plan: next_plan.Plan, n: int) -> str:
    """step.done's `mutation` (ai-dlc-record R3), from the step as the plan stands: `n/a` when
    its Mutation line says n/a, `reported` when its Status note says `mutation killed`, and
    `missing` otherwise, since the check is run by hand and leaves no machine record."""
    step = next((s for s in plan.steps if s.number == n), None)
    if step is not None and step.mutation.startswith("n/a"):
        return "n/a"
    if step is not None and "mutation killed" in step.note:
        return "reported"
    return "missing"


def record_step(
    root: Path,
    tree: Path,
    plan: next_plan.Plan,
    n: int,
    sha: str,
    result_path: Path,
    record: tuple[list[str], str] | None,
    *,
    plan_text: str,
    default: str | None = None,
) -> str | None:
    """After commit N: add the commit, the mutation value, the plan hash and the run key to the
    step's result file when one exists, then, when the plan carries Record, append step.done
    from it. The two values travel in the evidence file, since the appender refuses an
    evidence key as a flag; with no run key nothing is appended (D9, D11). Returns why that
    failed, or None; the commit stands either way."""
    if not result_path.is_file():
        return None
    key, no_key = run_key()
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["sha"] = sha
        result["mutation"] = mutation_value(plan, n)
        result["plan"] = next_plan.plan_hash(plan_text)
        if key is not None:
            result["runkey"] = key
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        return f"result file {result_path}: {exc}"
    if record is None:
        return None
    if key is None:
        return f"step.done needs a run key: {no_key.removeprefix('no run key: ')}"
    flags = ("--evidence", str(result_path))
    r = run_record(root, tree, record, "append", "step.done", *flags, default=default)
    return None if r.returncode == 0 else _record_detail(r)


def commit_record(tree: Path, slug: str, subject: str | None = None) -> str | None:
    """Commit the record alone as `chore(record): <slug>` (ai-dlc-record R2, R12), or as
    `subject` when given, and return the sha, or None when it has no change. Other staged or
    dirty files stay as they were."""
    path = record_path(slug)
    git(tree, "add", "--", path)
    if git(tree, "diff", "--cached", "--quiet", "--", path, check=False).returncode == 0:
        return None
    message = subject or f"chore(record): {slug}"
    r = git(tree, "commit", "-m", message, "--", path, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"record commit failed: {_last_line(r.stderr or r.stdout)}")
    return git(tree, "rev-parse", "HEAD").stdout.strip()


def record_pr_opened(
    root: Path,
    tree: Path,
    record: tuple[list[str], str],
    pr: int | None,
    head: str,
    branch: str,
    mode: str,
    *,
    default: str | None = None,
) -> tuple[str | None, str | None]:
    """Once the PR exists (ai-dlc-record R12): append pr.opened with the head it opened on,
    the mode that opened it, `auto` from wrap-up and `attended` from pr-opened (R5), and the
    run key (D9, D11), commit the record alone, and push HEAD to the PR's branch. With no run
    key nothing is appended. Returns (record commit sha, None) or (None, why a step failed);
    the PR stands either way."""
    if pr is None:
        return None, "no PR number to record"
    key, no_key = run_key()
    if key is None:
        return None, f"pr.opened needs a run key: {no_key.removeprefix('no run key: ')}"
    flags = ("--pr", str(pr), "--head", head, "--mode", mode, "--runkey", key)
    r = run_record(root, tree, record, "append", "pr.opened", *flags, default=default)
    if r.returncode != 0:
        return None, f"pr.opened append {_record_detail(r)}"
    try:
        sha = commit_record(tree, record[1])
    except RuntimeError as exc:
        return None, str(exc)
    if sha is None:
        return None, "pr.opened append exited 0 but the record did not change"
    push = git(tree, "push", "origin", f"HEAD:{branch}", check=False)
    if push.returncode != 0:
        return None, f"git push failed for {branch}: {_last_line(push.stderr)}"
    return sha, None


HEAD_RE = re.compile(r"[0-9a-fA-F]{7,40}")


def resolve_head(root: Path, head: str) -> tuple[str | None, int, str]:
    """The full sha of the commit `head` names (done-note counts spec, Requirement 7), as
    (sha, 0, ""), or (None, 1, why) for a refusal and (None, 2, why) when git itself could not
    answer: anything that is not seven to forty hex characters is refused before git is asked;
    otherwise one `git rev-parse --verify` call both checks that the repository holds the
    commit and expands a unique prefix, so no session types a sha again. An ambiguous prefix
    is refused naming it, and a prefix or sha that names no commit is git's `Needed a single
    revision`; any other failure (no repository at the root, an unreadable object store) is
    apparatus, carrying git's last line. git says all of this on stderr, which `--quiet` would
    silence, so the call runs without it and the stderr is read, never shown."""
    if not HEAD_RE.fullmatch(head):
        return None, 1, (
            f"head {head} is not a commit sha or a unique prefix of seven or more hex characters"
        )
    r = git(
        root, "rev-parse", "--verify", "--end-of-options", f"{head}^{{commit}}", check=False
    )
    if r.returncode == 0:
        return r.stdout.strip(), 0, ""
    if "ambiguous" in r.stderr:
        return None, 1, f"head {head} is ambiguous in this repository"
    if "Needed a single revision" in r.stderr:
        return None, 1, f"{head} is not a commit of this repository"
    return None, 2, f"git rev-parse: {_last_line(r.stderr)}"


# the record's findings grammar (countFindings in the delivery record's appender):
# a severity header, then `- ` or `* ` bullets at the margin, each with the indented lines under it
_SEVERITIES = {"#### Critical": "Critical", "#### Important": "Important", "#### Minor": "Minor"}
_HEADING_RE = re.compile(r"#{1,6}\s")
_BULLET_RE = re.compile(r"[-*] ")
# the `file:line` a finding starts with, backticked or bare
_FINDING_AT_RE = re.compile(r"[-*] `?([^\s`]+?):(\d+)")


def _findings(lines: list[str]) -> list[tuple[str, int, int, int]]:
    """Each finding under a severity header as (severity, header line, first line, end), its
    lines lines[first:end] with the blank lines after it left out. Read in countFindings'
    order: a heading first, then a bullet at the margin, then a line indented under it."""
    out: list[tuple[str, int, int, int]] = []
    severity = header = first = last = None
    for i, raw in enumerate(lines):
        trimmed = raw.strip()
        heading = _HEADING_RE.match(trimmed)
        bullet = not heading and severity is not None and _BULLET_RE.match(raw)
        if first is not None and not (heading or bullet) and (not trimmed or raw[:1].isspace()):
            if trimmed:
                last = i
            continue
        if first is not None:
            out.append((severity, header, first, last + 1))
            first = None
        if heading:
            severity, header = _SEVERITIES.get(trimmed), i
        elif bullet:
            first = last = i
    if first is not None:
        out.append((severity, header, first, last + 1))
    return out


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _unticked(line: str) -> str:
    return line[1:-1] if len(line) > 1 and line[0] == line[-1] == "`" else line


def _drop_reason(
    block: list[str], root: Path, head: str, files: dict[str, str | None]
) -> tuple[str, str | None]:
    """A finding's `file:line` label, and why it is dropped or None when its span occurs in
    that file at the head."""
    at = _FINDING_AT_RE.match(block[0])
    if not at:
        return "no file:line", "no file:line at the start of the finding"
    path = at.group(1)
    if Path(path).is_absolute() and Path(path).resolve().is_relative_to(root.resolve()):
        path = Path(path).resolve().relative_to(root.resolve()).as_posix()
    label = f"{path}:{at.group(2)}"
    quoted = [ln.strip()[2:] for ln in block[1:] if ln.strip().startswith("> ")]
    if not quoted:
        return label, "no `> ` line quoting the file"
    if path not in files:
        shown = git(root, "show", f"{head}:{path}", check=False)
        files[path] = _collapse(shown.stdout) if shown.returncode == 0 else None
    text = files[path]
    if text is None:
        return label, f"{path} is not in the tree at {head[:8]}"
    # the span as written, then with each line's backticks taken off
    spans = (_collapse(" ".join(quoted)), _collapse(" ".join(_unticked(q) for q in quoted)))
    if not any(span and span in text for span in spans):
        return label, f"the quoted span does not occur in {path} at {head[:8]}"
    return label, None


def filter_findings(report_text: str, root: Path, head: str) -> tuple[str, list[str]]:
    """verdict N's span check (adw 3.5.0 spec item 12). A finding under a severity header
    stays when it starts with `file:line` and its `> ` lines, joined and whitespace collapsed,
    occur in that file at the reviewed head, whitespace collapsed too; an absolute path under
    `root` reads as its repository path. Every other finding moves, with its reason, to a
    `### Dropped findings` section at the report's end, after `### Verdict`, and a severity
    section left with no finding reads `None`. Returns the report to keep, byte-identical when
    nothing moved, and the dropped findings' `file:line` labels in report order."""
    lines = report_text.split("\n")
    findings = _findings(lines)
    files: dict[str, str | None] = {}
    dropped: list[tuple[str, int, int, str]] = []
    labels: list[str] = []
    for severity, _header, first, end in findings:
        label, reason = _drop_reason(lines[first:end], root, head, files)
        if reason is not None:
            dropped.append((severity, first, end, reason))
            labels.append(label)
    if not dropped:
        return report_text, []
    gone = {i for _s, first, end, _r in dropped for i in range(first, end)}
    emptied = {}
    for _s, header, first, _end in findings:
        emptied.setdefault(header, first)
        if first not in gone:
            emptied[header] = None
    none_at = {first for first in emptied.values() if first is not None}
    kept = []
    for i, line in enumerate(lines):
        if i in none_at:
            kept.append("None")
        if i not in gone:
            kept.append(line)
    moved = []
    for severity, first, end, reason in dropped:
        moved.extend(lines[first:end])
        moved.append(f"  Dropped from {severity}: {reason}")
    body = "\n".join(kept).rstrip("\n")
    return f"{body}\n\n### Dropped findings\n" + "\n".join(moved) + "\n", labels


def record_verdict(
    root: Path,
    n: int,
    head: str,
    round_: int,
    verdict: str,
    report: Path,
    *,
    plan: str = "prompt_plan.md",
) -> tuple[int, str]:
    """One reviewer round (ai-dlc-record R11, R12): write the verdict file with the reviewer
    definition's identity and model, keep the report beside it, and, when the plan carries
    Record, append review.verdict from the two. Attended files are keyed by the reviewed
    head, a run's by step and round. The kept report is filter_findings' (spec item 12), the
    reviewer's text beside it as `.orig.md` when a finding was dropped, so the record counts
    the kept findings; the verdict is the reviewer's, unchanged. Exit 0 written, 1 refused, 2
    when the definition cannot be read or the append failed after the files were written."""
    resolved, rc, why = resolve_head(root, head)
    if rc == 2:
        return 2, f"VERDICT APPARATUS step {n}: {why}"
    if resolved is None:
        return 1, f"VERDICT REFUSED step {n}: {why}"
    head = resolved
    if not report.is_file():
        return 1, f"VERDICT REFUSED step {n}: no report at {report}"
    state = load_state(root)
    if state is None:
        verdict_file = verdict_path(root, head)
        kept = attended_dir(root) / f"review-{head}-{round_}.md"
    else:
        verdict_file = Path(state["run_dir"]) / f"verdict-{n}-{round_}.json"
        kept = Path(state["run_dir"]) / f"review-{n}-{round_}.md"
    try:
        reviewer, model = agent_identity("next-reviewer")
    except DefinitionError as exc:
        return 2, f"VERDICT APPARATUS step {n}: {exc}"
    original = report.read_bytes()
    reviewed = Path(state["worktree"]) if state else root
    kept_text, dropped = filter_findings(original.decode("utf-8"), reviewed, head)
    body = {"head": head, "verdict": verdict, "reviewer": reviewer, "model": model, "round": round_}
    verdict_file.parent.mkdir(parents=True, exist_ok=True)
    verdict_file.write_text(json.dumps(body, indent=2), encoding="utf-8")
    if dropped:
        kept.with_name(f"{kept.stem}.orig.md").write_bytes(original)
        kept.write_text(kept_text, encoding="utf-8")
    elif report.resolve() != kept.resolve():
        kept.write_bytes(original)
    tree, record, _name = _record_tree(root, plan)
    if record is not None:
        flags = ("--evidence", str(verdict_file), "--report", str(kept))
        default = state["default"] if state else None
        r = run_record(root, tree, record, "append", "review.verdict", *flags, default=default)
        if r.returncode != 0:
            return 2, f"VERDICT {verdict_file} RECORD FAILED step {n}: {_record_detail(r)}"
    gone = f" (dropped {len(dropped)}: {', '.join(dropped)})" if dropped else ""
    return 0, f"VERDICT {verdict} {verdict_file}{gone}"


def record_done_verdict(
    root: Path, head: str, verdict: str, model: str, report: Path, *, plan: str = "prompt_plan.md"
) -> tuple[int, str]:
    """`done-verdict`, attended (ai-dlc-record R12): the done check's verdict over the claim
    in its own .next-auto/attended/done-<sha>.json, as the merge gate reads it, with the
    report kept beside it as done-<sha>.md and, when the plan carries Record, done.verdict
    appended through the default branch's appender. The done-check agent's definition lives
    in another plugin, so its model is the one value the session supplies. Exit 0 written,
    1 refused, 2 when the append failed after the files were written."""
    resolved, rc, why = resolve_head(root, head)
    if rc == 2:
        return 2, f"DONE-VERDICT APPARATUS: {why}"
    if resolved is None:
        return 1, f"DONE-VERDICT REFUSED: {why}"
    head = resolved
    if not report.is_file():
        return 1, f"DONE-VERDICT REFUSED: no report at {report}"
    done_file = done_path(root, head)
    kept = attended_dir(root) / f"done-{head}.md"
    body = {"head": head, "verdict": verdict, "reviewer": DONE_REVIEWER, "model": model}
    done_file.parent.mkdir(parents=True, exist_ok=True)
    done_file.write_text(json.dumps(body, indent=2), encoding="utf-8")
    if report.resolve() != kept.resolve():
        kept.write_bytes(report.read_bytes())
    record = plan_record(next_plan.parse_plan((root / plan).read_text(encoding="utf-8")))
    if record is not None:
        flags = ("--evidence", str(done_file), "--report", str(kept))
        r = run_record(root, root, record, "append", "done.verdict", *flags)
        if r.returncode != 0:
            return 2, f"DONE-VERDICT {done_file} RECORD FAILED: {_record_detail(r)}"
    return 0, f"DONE-VERDICT {verdict} {done_file}"


# ---- preflight --------------------------------------------------------------


def check_plan(root: Path, plan_path: Path) -> tuple[list[str], list[str]]:
    """The plan checks, decided from the tree alone: the file exists and validates.
    Preflight runs them before anything that reaches GitHub, so a missing or broken
    plan is reported as such and never masked by a gh failure. Returns the refusals and,
    for a plan that validates, validate's warnings (the gate-config stems a step names
    without the basename), which refuse nothing: the preflight command prints them once."""
    resolved = Path(plan_path)
    if not resolved.is_absolute():
        resolved = root / resolved
    if not resolved.is_file():
        return [f"no plan at {resolved}"], []
    text = resolved.read_text(encoding="utf-8")
    if next_plan.validate_text(text):
        reason = "plan does not validate (run next_plan.py validate for line-numbered reasons)"
        return [reason], []
    return [], [msg for _ln, msg in next_plan.validate_warnings(text)]


def _exempt_in_next_batch(root: Path, plan_path: Path) -> int | None:
    """The number of the first Test-repo step among the next DEFAULT_STEPS pending steps, the
    batch an auto run would work (test-repo exemption spec, Requirement 6), else None."""
    resolved = plan_path if plan_path.is_absolute() else root / plan_path
    plan = next_plan.parse_plan(resolved.read_text(encoding="utf-8"))
    pending = [s for s in plan.steps if s.state in ("PENDING", "IN PROGRESS")][:DEFAULT_STEPS]
    return next((s.number for s in pending if s.test_repo is not None), None)


def _unsized_pending(root: Path, plan_path: Path) -> int | None:
    """The number of the first PENDING or IN PROGRESS step with no Size: line, every open step
    scanned (spec item 4; the scan has no batch bound, Decisions), else None."""
    resolved = plan_path if plan_path.is_absolute() else root / plan_path
    plan = next_plan.parse_plan(resolved.read_text(encoding="utf-8"))
    pending = (s for s in plan.steps if s.state in ("PENDING", "IN PROGRESS"))
    return next((s.number for s in pending if s.size is None), None)


def unit_prs(prs: list[tuple[int, str]], issue: str | None) -> list[int]:
    """The open next-auto PRs that refuse a run of the plan's unit: those on a branch that
    begins `next-auto/<issue>-`, the plan's Issue lowercased as start names the branch, or every
    one when the plan has no Issue."""
    if not issue:
        return [number for number, _branch in prs]
    prefix = f"next-auto/{issue.lower()}-"
    return [number for number, branch in prs if branch.startswith(prefix)]


def preflight_checks(
    root: Path,
    plan_path: Path,
    *,
    origin_owner: str | None,
    is_fork: bool,
    default_branch: str | None,
    open_next_auto_prs: list[tuple[int, str]] | None,
    pr_listing_cause: str = "",
) -> list[str]:
    reasons, _warnings = check_plan(root, plan_path)
    if reasons and reasons[0].startswith("no plan at"):
        # No plan is the end of it: every later check reads the plan.
        return reasons
    if not reasons:
        # an exempt step runs attended: its tests live in another repository the implementer
        # worktree cannot reach, so a batch that would meet one is refused before it starts
        exempt = _exempt_in_next_batch(root, plan_path)
        if exempt is not None:
            reasons.append(
                f"step {exempt} carries Test-repo and runs attended; auto mode works the next "
                f"{DEFAULT_STEPS} pending steps"
            )
        # an unestimated open step is the one the size gate has been catching for money
        unsized = _unsized_pending(root, plan_path)
        if unsized is not None:
            reasons.append(
                f"plan: Step {unsized} carries no Size: line; auto runs need the estimate"
            )

    owner_why = owner_reason(origin_owner)
    if owner_why:
        reasons.append(f"{owner_why}; auto runs only in repos they own")
    if is_fork:
        reasons.append("repository is a fork; refusing so PRs never route to the upstream")
    if not default_branch:
        reasons.append("could not determine the default branch")

    if default_branch:
        anc = git(
            root,
            "merge-base",
            "--is-ancestor",
            f"origin/{default_branch}",
            default_branch,
            check=False,
        )
        if anc.returncode != 0:
            reasons.append(
                f"local {default_branch} is behind origin/{default_branch} "
                "(origin is not an ancestor); pull before an auto run"
            )

    if git(root, "status", "--porcelain").stdout.strip():
        reasons.append("working tree is not clean; commit or stash before an auto run")

    if find_state_file(root) is not None:
        reasons.append(
            "a next-auto run already exists (state file present); finish or clear it first"
        )

    if open_next_auto_prs is None:
        cause = pr_listing_cause or "gh pr list failed"
        reasons.append(f"could not determine open next-auto PRs ({cause})")
    else:
        resolved = plan_path if plan_path.is_absolute() else root / plan_path
        issue = next_plan.parse_plan(resolved.read_text(encoding="utf-8")).conventions.get(
            next_plan.ISSUE_KEY
        )
        same = unit_prs(open_next_auto_prs, issue)
        if same:
            unit = f" for {issue}" if issue else ""
            numbers = ", ".join(f"#{n}" for n in same)
            reasons.append(f"an open PR labeled next-auto already exists{unit} ({numbers})")

    return reasons


# ---- start ------------------------------------------------------------------


def _slug(plan_text: str) -> str:
    plan = next_plan.parse_plan(plan_text)
    title = plan.title.lstrip("# ").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return (slug or "run")[:40]


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%d}-{uuid.uuid4().hex[:6]}"


# the run key's one form, which the projection imports: the Claude Code session id
RUN_KEY_FORM = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def run_key(env: Mapping[str, str] = os.environ) -> tuple[str | None, str | None]:
    """The run key for attended and unattended work alike (D1, D11): the Claude Code session
    id, which Claude Code sets as CLAUDE_CODE_SESSION_ID in every Bash and hook subprocess.
    Returns (key, None), or (None, why there is none)."""
    value = env.get("CLAUDE_CODE_SESSION_ID")
    if value is None:
        return None, "no run key: CLAUDE_CODE_SESSION_ID is unset"
    if not RUN_KEY_FORM.fullmatch(value):
        return None, f"no run key: CLAUDE_CODE_SESSION_ID is not a session id: {value}"
    return value, None


def start(
    root: Path,
    plan_name: str,
    default: str,
    *,
    run_id: str | None = None,
    steps_per_invocation: int = DEFAULT_STEPS,
    sync: bool = True,
) -> dict:
    root = Path(root)
    run_id = run_id or new_run_id()
    run_dir = run_base(root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_bytes = (root / plan_name).read_bytes()
    plan_text = plan_bytes.decode("utf-8")
    tracked = plan_is_tracked(root, plan_name)
    # the plan's Issue key, lowercased, leads the branch name so the tracker links the run
    issue = next_plan.parse_plan(plan_text).conventions.get(next_plan.ISSUE_KEY)
    branch = f"next-auto/{issue.lower() + '-' if issue else ''}{_slug(plan_text)}-{run_id}"
    wt = run_dir / "wt"
    git(root, "worktree", "add", "--no-track", "-b", branch, str(wt), default)
    if not tracked:
        # Same bytes at the same relative path. info/exclude is shared with the worktree,
        # so once the plan is excluded at the root no `git add -A` in the worktree can
        # stage it; ensure_ignored covers a fresh clone that has no entry yet.
        (wt / plan_name).parent.mkdir(parents=True, exist_ok=True)
        (wt / plan_name).write_bytes(plan_bytes)
        ensure_ignored(root, plan_name)

    venv = ""
    if sync and (wt / "pyproject.toml").exists():
        try:
            venv = sync_worktree_env(wt)
        except RuntimeError:
            git(root, "worktree", "remove", "--force", str(wt), check=False)
            git(root, "branch", "-D", branch, check=False)
            shutil.rmtree(run_dir, ignore_errors=True)
            raise

    state = {
        "venv": venv,
        "run_id": run_id,
        "root": str(root),
        "default": default,
        "branch": branch,
        "worktree": str(wt),
        "run_dir": str(run_dir),
        "plan": plan_name,
        "plan_tracked": tracked,
        "holdout": next_plan.parse_plan(plan_text).conventions.get("Holdout", ""),
        "steps_per_invocation": steps_per_invocation,
        "current_step": None,
        "base_head": None,
        "attempt": 0,
        "steps": {},
        "stop_reason": None,
    }
    save_state(state)
    return state


def sync_worktree_env(wt: Path) -> str:
    """`uv sync --locked` in the worktree, then prove the interpreter `uv run` picks is
    the worktree's own (C3: tests must not run against main's environment). Returns
    the environment prefix."""
    r = subprocess.run(["uv", "sync", "--locked"], cwd=str(wt), capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"uv sync --locked failed in the worktree: {tail[0]}")
    probe = subprocess.run(
        ["uv", "run", "python", "-c", "import sys; print(sys.prefix)"],
        cwd=str(wt),
        capture_output=True,
        text=True,
    )
    prefix = probe.stdout.strip()
    if probe.returncode != 0 or not prefix:
        raise RuntimeError(f"uv run probe failed in the worktree: {probe.stderr.strip()[-200:]}")
    if not Path(prefix).resolve().is_relative_to(wt.resolve()):
        raise RuntimeError(f"worktree environment resolves outside the worktree: {prefix}")
    return prefix


# ---- step-begin -------------------------------------------------------------


def _set_plan_status(
    worktree: Path, plan_name: str, n: int, cli_state: str, note: str = ""
) -> None:
    plan_path = worktree / plan_name
    data = plan_path.read_bytes()
    plan_path.write_bytes(next_plan.set_status_bytes(data, n, cli_state, note))


def set_step(root: Path, n: int, cli_state: str, note: str = "") -> None:
    """Set a step's Status in the worktree plan, the only copy a run may change."""
    state = load_state(root)
    _set_plan_status(Path(state["worktree"]), state["plan"], n, cli_state, note)


def append_plan(root: Path, section: str, entry: str) -> None:
    """Append one entry to Decisions or Findings in the worktree plan, tagged with the
    run id so an agent's unreviewed ruling reads differently from one the owner wrote."""
    state = load_state(root)
    plan_path = Path(state["worktree"]) / state["plan"]
    tag = f"[run {state['run_id']}]"
    body = entry.strip()
    # an entry whose note already ends with this run's tag carries it once; another run's tag
    # is part of the entry and stays
    if body.endswith(tag):
        body = body[: -len(tag)].rstrip()
    tagged = f"{body} {tag}"
    plan_path.write_bytes(next_plan.append_section_bytes(plan_path.read_bytes(), section, tagged))


def plan_commands(worktree: Path, plan_name: str) -> tuple[list[str], list[str]]:
    """The Test and Lint commands the plan's Conventions bind every step to. Lint runs up to
    its first `&&` outside quotes, which validate refuses and an older plan may carry; a
    quoted `&&` stays inside its argument."""
    plan = next_plan.parse_plan((worktree / plan_name).read_text(encoding="utf-8"))
    test_cmd = shlex.split(plan.conventions.get("Test", ""))
    lint = next_plan.command_tokens(plan.conventions.get("Lint", ""))
    lint_cmd = lint[: lint.index("&&")] if "&&" in lint else lint
    return test_cmd, lint_cmd


def attended_dir(root: Path) -> Path:
    return run_base(root) / ATTENDED_SUBDIR


def attended_record_path(root: Path, n: int) -> Path:
    return attended_dir(root) / f"step-{n}.json"


def other_work_trees(top: Path) -> list[Path]:
    """The repository's other work trees, in the order `git worktree list --porcelain` lists
    them, leaving out top; a listed path that no longer exists stays, and holds no plan."""
    out = git(top, "worktree", "list", "--porcelain").stdout
    prefix = "worktree "
    trees = [Path(ln[len(prefix) :]) for ln in out.splitlines() if ln.startswith(prefix)]
    here = top.resolve()
    return [t for t in trees if t.resolve() != here]


def shares_conflict(root: Path, plan: str) -> str | None:
    """The refusal check begin and start give when another work tree of the repository has a
    live plan sharing a resource class with this one, docs aside (ai-dlc-record R14, the Shares
    line), else None. A tree is live when its plan has a step IN PROGRESS, a check record under
    .next-auto/attended/ or a run marker under .next-auto/; a finished plan counts for nothing,
    and a plan with no Shares line shares nothing."""
    mine = next_plan.plan_shares(next_plan.parse_plan((root / plan).read_text(encoding="utf-8")))
    mine -= {"docs"}
    if not mine:
        return None
    for tree in other_work_trees(root):
        if not (tree / plan).is_file():
            continue
        other = next_plan.parse_plan((tree / plan).read_text(encoding="utf-8"))
        if all(s.state == "DONE" for s in other.steps):
            continue
        live = (
            any(s.state == "IN PROGRESS" for s in other.steps)
            or any(attended_dir(tree).glob("step-*.json"))
            or find_state_file(tree) is not None
        )
        shared = mine & next_plan.plan_shares(other)
        if live and shared:
            return f"the work tree {tree} has a live plan sharing {', '.join(sorted(shared))}"
    return None


def check_log_path(root: Path, n: int) -> Path:
    return attended_dir(root) / f"check-{n}.log"


def check_begin(
    root: Path,
    n: int,
    *,
    plan: str = "prompt_plan.md",
    test_cmd: list[str] | None = None,
    lint_cmd: list[str] | None = None,
) -> tuple[int, str]:
    """Record the base of an attended step: HEAD, baseline counts, the rendered brief.

    Writes only under .next-auto/attended/ and ensures .next-auto/ is ignored through
    .git/info/exclude, never the tracked .gitignore. Returns (exit code, one line):
    0 recorded, 1 refused (no such step, or a tree dirty beyond the plan), 2 apparatus
    (git or the test command cannot run).
    """
    parsed = next_plan.parse_plan((root / plan).read_text(encoding="utf-8"))
    step = next((s for s in parsed.steps if s.number == n), None)
    if step is None:
        return 1, f"CHECK FAIL step {n}: no step {n} in plan"
    rc, reason = plan_approval(root, plan)
    if rc == 1:
        return 1, f"CHECK FAIL step {n}: {reason}"
    if rc == 2:
        return 2, f"CHECK APPARATUS step {n}: {reason}"
    # one builder at a time on a shared resource: a live plan in another work tree that
    # shares a class with this one refuses the begin (rul-3c4db5a3fb)
    try:
        conflict = shares_conflict(root, plan)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        return 2, f"CHECK APPARATUS step {n}: {exc}"
    if conflict:
        return 1, f"CHECK FAIL step {n}: {conflict}"
    # a pending record line is the loop's own edit, as the plan's status edit is (R12)
    plan_rec = plan_record(parsed)
    record_file = record_path(plan_rec[1]) if plan_rec else None
    own = [plan, record_file] if record_file else [plan]
    try:
        ensure_ignored(root, RUN_SUBDIR + "/")
        # the base is HEAD plus the plan's own status edit, nothing else: a tree that
        # already carries the step's work would baseline the counts against that work, and
        # a repeat begin after a failed step would reset the failure count (Requirement 3d)
        dirty = [
            ln[3:].strip()
            for ln in git(root, "status", "--porcelain").stdout.splitlines()
            if ln.strip() and ln[3:].strip() not in own
        ]
        base_head = git(root, "rev-parse", "HEAD").stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        return 2, f"CHECK APPARATUS step {n}: {exc}"
    if dirty:
        return 1, (
            f"CHECK FAIL step {n}: working tree has changes beyond {' and '.join(own)} "
            f"({', '.join(dirty)}); commit or revert them before check begin"
        )
    plan_test, plan_lint = plan_commands(root, plan)
    test_cmd = test_cmd or plan_test
    lint_cmd = lint_cmd or plan_lint
    d = attended_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    # a record outlives its step only until the next step begins; the commit path also
    # removes it, so anything left here is stale
    for stale in list(d.glob("step-*.json")) + list(d.glob("brief-*.md")):
        if stale.stem.split("-", 1)[1] != str(n):
            stale.unlink()
    # a new attempt at the step: an earlier attempt's pass must not reach the record
    attended_result_path(root, n).unlink(missing_ok=True)
    # every earlier check's log, the step's own included, so a log is always this step's
    for log in d.glob("check-*.log"):
        log.unlink()
    # the record as the step found it; check N refuses a step that edited it (R12)
    for old in d.glob("record-snapshot-*.md"):
        old.unlink()
    record_sha256 = None
    if record_file and (root / record_file).is_file():
        record_bytes = (root / record_file).read_bytes()
        (d / f"record-snapshot-{n}.md").write_bytes(record_bytes)
        record_sha256 = sha256_bytes(record_bytes)
    brief_path = d / f"brief-{n}.md"
    brief_path.write_text(next_plan.render_brief(parsed, n), encoding="utf-8")
    try:
        base_counts = list(pytest_counts(root, test_cmd)) if test_cmd else None
    except OSError as exc:
        return 2, f"CHECK APPARATUS step {n}: test command unavailable: {exc}"
    record = {
        "base_head": base_head,
        "base_counts": base_counts,
        "brief_path": str(brief_path),
        "test_cmd": test_cmd,
        "lint_cmd": lint_cmd,
        "test_files": next_plan.plan_test_globs(parsed),
        # the plan's Toolchain-files globs, the CLAUDE.md and .claude/ paths check N admits
        "toolchain_files": next_plan.plan_toolchain_globs(parsed),
        # the step's Deletes: line, which lowers the count floor by the files it names
        "deletes": step.deletes,
        # the step's Guard-mutation: line as [path, old, new], which prove-red applies when
        # the step's tests pass at base
        "guard_mutation": list(step.guard_mutation) if step.guard_mutation else None,
        # the step's Size: line, the estimate the result file records beside the measurement
        "size": step.size,
        # a begin on a DONE step is its fix round, which a comment-only change passes
        "fix_round": step.state == "DONE",
        "plan": plan,
        "failures": 0,
        "record_file": record_file,
        "record_sha256": record_sha256,
        # the Test-repo exemption: where the step's tests live and the command that runs
        # them from this machine, both None for a step that builds here
        "test_repo": step.test_repo,
        "external_test_cmd": shlex.split(step.test_cmd) if step.test_cmd else None,
    }
    attended_record_path(root, n).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return 0, f"CHECK BEGIN step {n} base={base_head[:8]}"


def _last_line(out: str) -> str:
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return lines[-1].strip() if lines else "(no output)"


def run_external_test(
    cmd: list[str], cwd: Path, *, limit: int = EXTERNAL_TEST_LIMIT
) -> tuple[int, str]:
    """Run an exempt step's external Test (the Test-repo exemption) as one process group and
    return its exit code with the last non-empty line of its combined output. A command that
    cannot start, and ssh's own exit 255, raise ExternalTestUnavailableError; a run past `limit`
    seconds is killed with its group and raises ExternalTestTimeoutError naming the limit."""
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True,
        )  # fmt: skip
    except OSError as exc:
        raise ExternalTestUnavailableError(f"{cmd[0]}: {exc}") from exc
    with proc:
        try:
            out, _ = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise ExternalTestTimeoutError(f"external Test still running after {limit} s") from None
    if proc.returncode == 255:
        raise ExternalTestUnavailableError(f"exit 255 from {cmd[0]}: {_last_line(out)}")
    return proc.returncode, _last_line(out)


def evidence_clause(result: dict) -> str:
    """The clause `set N done` appends to a note from the step's result file (done-note counts
    spec, Requirement 2): every value read from the file, none typed, byte-stable for a file."""
    clause = (
        f"check: {result['passed']} passed of {result['collected']} collected at base "
        f"{str(result['head'])[:8]}, lint {result['lint']}, red {result['red']}"
    )
    external = result.get("external_test")
    if result.get("red") == "external" and external is not None:
        clause += f", external Test exit {external['exit']}: {external['last_line']}"
    return clause


# The suite's own numbers as a note types them (done-note counts spec, Requirement 3): a fixed
# list, so a count with another unit (mutants, fixture cases, commits, another repository's
# PASS lines) is not judged.
NOTE_COUNT_RES = tuple(
    re.compile(p) for p in (r"(\d+) passed", r"suite (\d+)", r"(\d+) tests? green", r"(\d+) green")
)
NOTE_BASE_RE = re.compile(r"CHECK PASS(?: step \d+)? at base ([0-9a-fA-F]{4,40})")
# the recorded-gap form a note needs with no result file (Requirement 4): `no check N`, a colon
# before the sentence ends, and a reason after it
GAP_RE = re.compile(r"no check (\d+)\b[^.:]*:\s*[^\s.]")
NO_RESULT_REFUSAL = (
    'SET REFUSED step {n}: no result file; run check {n}, or write "no check {n}: <why>" '
    "in the note"
)


def note_conflicts(note: str, result: dict, n: int) -> str | None:
    """The refusal line when the note's suite numbers disagree with the step's result file
    (done-note counts spec, Requirement 3), else None: a count under one of NOTE_COUNT_RES that
    is not the record's `passed`, or a `CHECK PASS at base <sha>` whose sha is no prefix of the
    record's head."""
    passed = int(result["passed"])
    for pattern in NOTE_COUNT_RES:
        for m in pattern.finditer(note):
            if int(m.group(1)) != passed:
                return (
                    f"SET REFUSED step {n}: the note says {m.group(0)}, "
                    f"the check record says {passed}"
                )
    head = str(result["head"])
    for m in NOTE_BASE_RE.finditer(note):
        if not head.lower().startswith(m.group(1).lower()):
            return (
                f"SET REFUSED step {n}: the note says {m.group(0)}, "
                f"the check record says {head[:8]}"
            )
    return None


def records_gap(note: str, n: int) -> bool:
    """Whether the note carries `no check N: <why>` for this step (Requirement 4)."""
    return any(int(m.group(1)) == n for m in GAP_RE.finditer(note))


MUTATION_CLAIM_RE = re.compile(r"\bmutation killed\b")

# the clause `evidence_clause` writes, as a DONE step's Status carries it (3.5.10 spec R11): a
# step whose note holds one is a committed step, and a second `set N done` on it is a fix round
EVIDENCE_CLAUSE_RE = re.compile(
    r"check: \d+ passed of \d+ collected at base [0-9a-fA-F]{8}, lint \S+, red \S+"
)


def mutation_claim_conflict(step: next_plan.Step, note: str) -> str | None:
    """The refusal line when the note's mutation claim disagrees with the step's `Mutation:`
    header (done-note counts spec, Requirement 5), else None: the words `mutation killed` on
    an n/a step, or their absence on a required one."""
    claims = MUTATION_CLAIM_RE.search(note) is not None
    if step.mutation.startswith("n/a") and claims:
        return (
            f"SET REFUSED step {step.number}: Mutation: n/a step claims a mutation kill; "
            "change the header line before the check"
        )
    if step.mutation == "required" and not claims:
        return (
            f"SET REFUSED step {step.number}: Mutation: required step lacks the words "
            "mutation killed; change the header line or run the check"
        )
    return None


RUN_NO_RESULT_REFUSAL = (
    "SET REFUSED step {n}: no result file in the run; gate {n} writes it on a pass"
)


def set_done_from_record(
    plan_path: Path, result_path: Path, n: int, note: str, *, in_run: bool = False
) -> tuple[int, str]:
    """`set N done` for either mode: refuse a note whose mutation claim disagrees with the
    step's header; with a result file, refuse one a commit already used (it carries `sha`),
    then decide idempotence (a note that already ends with the file's clause gets no second
    one) and scan the typed part of the note alone for suite numbers that disagree with the
    file, so the clause's own text, an external last line included, is never judged; with no
    result file, refuse unless the note records the gap and why, and in a run refuse outright,
    since `gate K` writes the file on every pass. On a step already `DONE` whose Status carries
    an evidence clause (3.5.10 spec R11, invariant I5): the plan's own note passed back is judged
    by nothing and changes nothing; a result whose clause the Status already ends with is the
    same round, and the typed note replaces that round's note (the whole Status on a one-clause
    step, the last fix round entry otherwise); a new clause is a fix round, appended after the
    step's record as `; fix round: <typed>; <clause>`, so the same call again changes nothing.
    Then write the plan's Status line. Returns (exit code, one line): 1 for a refusal, 2 when the
    result file does not parse; neither writes the plan."""
    data = plan_path.read_bytes()
    plan = next_plan.parse_plan(data.decode("utf-8"))
    step = next((s for s in plan.steps if s.number == n), None)
    if step is not None:
        conflict = mutation_claim_conflict(step, note)
        if conflict is not None:
            return 1, conflict
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return 2, f"SET APPARATUS step {n}: result file unreadable: {exc}"
        if result.get("sha"):
            return 1, (
                f"SET REFUSED step {n}: the result file was used by commit "
                f"{str(result['sha'])[:8]}; run check {n} again"
            )
        clause = evidence_clause(result)
        current = step.note if step is not None and step.state == "DONE" else ""
        if current and note == current:
            pass  # the plan's own note passed back: nothing typed, nothing to judge or write
        else:
            typed = note[: -len(clause)].rstrip("; ") if note.endswith(clause) else note
            conflict = note_conflicts(typed, result, n)
            if conflict is not None:
                return 1, conflict
            if EVIDENCE_CLAUSE_RE.search(current):
                if current.endswith(clause):
                    # the same round: the typed note replaces this round's note alone
                    cut = current.rfind("; fix round: ")
                    head = current[:cut] if cut != -1 else ""
                    entry = f"fix round: {typed}; {clause}" if head else f"{typed}; {clause}"
                    note = f"{head}; {entry}" if head else entry
                else:
                    note = f"{current}; fix round: {typed}; {clause}"
            elif typed is note:
                note = f"{note}; {clause}" if note else clause
    elif in_run:
        return 1, RUN_NO_RESULT_REFUSAL.format(n=n)
    elif not records_gap(note, n):
        return 1, NO_RESULT_REFUSAL.format(n=n)
    plan_path.write_bytes(next_plan.set_status_bytes(data, n, "done", note))
    return 0, f"SET step {n} done"


def set_done_attended(
    root: Path, n: int, note: str, *, plan: str = "prompt_plan.md"
) -> tuple[int, str]:
    """The attended `set N done`, run-less, against `.next-auto/attended/result-N.json`."""
    return set_done_from_record(root / plan, attended_result_path(root, n), n, note)


def set_done_run(root: Path, n: int, note: str) -> tuple[int, str]:
    """`set K done` in a run, against `<run-dir>/result-K.json` and the worktree plan, the
    only copy a run may change."""
    state = load_state(root)
    return set_done_from_record(
        Path(state["worktree"]) / state["plan"], run_result_path(state, n), n, note, in_run=True
    )


def _exempt_staging_ok(path: str, rec: dict) -> bool:
    """Under the Test-repo exemption a step stages here only the plan, the delivery record and
    files under docs/ (test-repo exemption spec, Requirement 4): the engine cannot prove a red
    in another repository, so it refuses to let code land here under the same step."""
    own = {rec.get("plan", "prompt_plan.md"), rec.get("record_file")}
    return path in own or path.startswith("docs/")


def check_step(root: Path, n: int) -> tuple[int, list[str]]:
    """Run the gate's four checks and the suite against an attended step, run-less.

    Stages the tree into an index of its own (GIT_INDEX_FILE) and reads everything from it,
    so the real index is never written: a rewrite would reset the index timestamp git's
    racy-entry check reads, and a later same-size edit could slip past the commit's add. The
    check index starts from the real index's entries without their stat data, so a path the
    step untracked or force-added counts as it will in the commit, and every file is hashed.
    The working tree is left as it was, apart from the check's own files under
    .next-auto/attended/. Returns (exit code, lines):
    0 with one CHECK PASS line, 1 with one CHECK FAIL line per reason in the gate's
    wording, 2 with one CHECK APPARATUS line when git, the test command or Lint cannot run.
    A pass writes the step's result file for the record; every other outcome leaves none.
    """
    result_path = attended_result_path(root, n)
    result_path.unlink(missing_ok=True)
    # written again on each check, and only when its Test runs
    log_path = check_log_path(root, n)
    log_path.unlink(missing_ok=True)
    rec_path = attended_record_path(root, n)
    if not rec_path.is_file():
        return 1, [f"CHECK FAIL step {n}: no record; run check begin {n} first"]
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    if int(rec.get("failures", 0)) >= 2:
        # the batch has stopped; nothing runs until check begin starts the step again
        return 1, [SECOND_FAILURE.format(n=n)]
    test_cmd: list[str] = rec["test_cmd"]
    brief = Path(rec["brief_path"])
    brief_text = brief.read_text(encoding="utf-8") if brief.is_file() else ""
    check_index = attended_dir(root) / f"index-{n}.check"
    seed = attended_dir(root) / f"index-{n}.seed"
    reasons: list[str] = []
    proof: dict = {}
    red_reason = None
    base = rec["base_head"]
    # the step is read against its base, so a HEAD that left the base's line (a reset, or a
    # rebase that rewrote the base) would make other commits' files the step's own; a pull of
    # any kind keeps an upstream base on HEAD's line and is not caught here
    if git(root, "merge-base", "--is-ancestor", base, "HEAD", check=False).returncode != 0:
        return 2, [
            f"CHECK APPARATUS step {n}: HEAD no longer descends from the step's base {base[:8]}; "
            f"commit or set aside the step's work and run check begin {n} on the new HEAD"
        ]
    try:
        stage_check_index(root, check_index, seed)
        # against the base check begin recorded, so a commit made part way through the step
        # stays the step's change: a red test committed on its own, a test deleted early
        staged = staged_files(root, index=check_index, base=rec["base_head"])
        if not staged:
            return 1, _count_failure(
                rec_path, rec, brief, n, ["nothing staged: the step produced no changes"]
            )
        # the tree a pass vouches for, the plan aside (R11), and the step's measured size,
        # read through the same index so the real one is never written
        checked = staged_tree(root, [rec.get("plan", "prompt_plan.md")], index=check_index)
        changed = _diff_lines(root, "--cached", base, index=check_index)
        for reason in (
            _check_record_since_begin(root, rec, n),
            check_staged_safe(root, staged, toolchain=rec.get("toolchain_files")),
            check_gate_config(staged, brief_text),
        ):
            if reason:
                reasons.append(reason)
        apparatus: list[Exception] = []
        external = None
        if rec.get("test_repo"):
            # the Test-repo exemption: the step's tests live in another repository, so the
            # staging rule and the external command stand in for prove-red and the counts; a
            # staging refusal runs nothing, since the step is malformed rather than red
            offside = [f for f in staged if not _exempt_staging_ok(f, rec)]
            if offside:
                reasons.append(
                    "a Test-repo step stages only the plan, the record and docs/ here; "
                    f"{', '.join(offside)} need a test in this repository"
                )
                return 1, _count_failure(rec_path, rec, brief, n, reasons)
            if reasons:
                # a step the record, staged-safe or gate-config already refused spends no ssh
                # round trip, and its reasons are never lost behind an apparatus line
                return 1, _count_failure(rec_path, rec, brief, n, reasons)
            try:
                code, last = run_external_test(
                    rec["external_test_cmd"], root, limit=EXTERNAL_TEST_LIMIT
                )
            except ExternalTestUnavailableError as exc:
                return 2, [f"CHECK APPARATUS step {n}: external Test unavailable: {exc}"]
            except ExternalTestTimeoutError as exc:
                return 2, [f"CHECK APPARATUS step {n}: {exc}"]
            external = {"command": rec["external_test_cmd"], "exit": code, "last_line": last}
            if code != 0:
                reasons.append(f"external Test failed: {last}")
            rc, out = run_check(root, test_cmd)
            command = shlex.join(rec["external_test_cmd"])
            log_path.write_text(f"external Test: {command} exit {code}\n{out}", encoding="utf-8")
            if rc != 0:
                reasons.append(f"tests failed: {_last_line(out)}")
        else:
            guard = rec.get("guard_mutation")
            if rec.get("fix_round") and comment_only_round(root, base, staged, index=check_index):
                # a fix round of comments and documentation has no behaviour to prove red;
                # Test, the counts and Lint still run
                red_reason, reason = COMMENT_ONLY_ROUND, None
            else:
                try:
                    reason = prove_red(
                        root, root, rec["base_head"], staged, test_cmd,
                        patterns=rec.get("test_files"), index=check_index,
                        guard=tuple(guard) if guard else None, proof=proof,
                    )  # fmt: skip
                except ProveRedApparatusError as exc:
                    apparatus.append(exc)
                    reason = None
            if reason:
                reasons.append(reason)
            rc, out = run_check(root, test_cmd)
            log_path.write_text(out, encoding="utf-8")
            if rc != 0:
                reasons.append(f"tests failed: {_last_line(out)}")
            floor = count_floor(
                root, root, rec["base_head"], rec.get("base_counts"), rec.get("deletes"),
                test_cmd, patterns=rec.get("test_files"), index=check_index,
            )  # fmt: skip
            reason = check_counts(floor, parse_counts(out))
            if reason:
                reasons.append(reason)
        try:
            reason = _lint_value(root, rec["lint_cmd"])
        except LintUnavailableError as exc:
            apparatus.append(exc)
            reason = None
        if reason:
            reasons.append(reason)
        if apparatus:
            # a step that also failed on its own, Test, the counts and Lint included, counts
            # that failure with each apparatus line; apparatus alone counts none, and prints
            # one CHECK APPARATUS line per apparatus
            if not reasons:
                return 2, [f"CHECK APPARATUS step {n}: {exc}" for exc in apparatus]
            reasons.extend(str(exc) for exc in apparatus)
    except (
        OSError, subprocess.CalledProcessError, ProveRedApparatusError, LintUnavailableError
    ) as exc:  # fmt: skip
        return 2, [f"CHECK APPARATUS step {n}: {exc}"]
    finally:
        check_index.unlink(missing_ok=True)
        seed.unlink(missing_ok=True)
    if reasons:
        return 1, _count_failure(rec_path, rec, brief, n, reasons)
    write_result(
        result_path, n, "attended", rec["base_head"], out, "ok", tree=checked,
        size_estimate=rec.get("size"), size_changed=changed,
        external=external, test_repo=rec.get("test_repo"),
        guard_mutation=proof.get("guard_mutation"),
        red="n/a" if red_reason else None, red_reason=red_reason,
    )  # fmt: skip
    # the HEAD this pass saw: a commit made part way through the step moves HEAD off the base,
    # and the Stop hook keeps the step's DONE record live while HEAD is still this one
    rec["passed_head"] = git(root, "rev-parse", "HEAD").stdout.strip()
    # the tree this pass vouches for: a stop at the same tree runs no Test (R14)
    rec["passed_tree"] = checked
    rec_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return 0, [f"CHECK PASS step {n}"]


def _check_record_since_begin(root: Path, rec: dict, n: int) -> str | None:
    """The record must match the check record's hash, which check begin N takes and the Stop
    hook's gate.overridden append refreshes: during a step only the engine's appends write it
    (ai-dlc-record R12)."""
    record_file = rec.get("record_file")
    if not record_file:
        return None
    current = root / record_file
    now = sha256_bytes(current.read_bytes()) if current.is_file() else None
    if now != rec.get("record_sha256"):
        snap = attended_dir(root) / f"record-snapshot-{n}.md"
        return (
            f"{record_file} changed during the step; the step may not edit the record "
            f"(snapshot: {snap})"
        )
    return None


def _lint_value(tree: Path, lint_cmd: list[str]) -> str | None:
    """check N judges Lint as the gate does, after Test and the counts: a non-zero exit is the
    reason `Lint failed: <last line>`, a red attempt like a failing Test; a Lint that cannot
    start raises LintUnavailableError, `Lint unavailable: <text>`, apparatus that counts no
    attempt unless the step also failed on its own. None on a pass, which the result file
    records as `lint: ok`."""
    try:
        rc, out = run_check(tree, lint_cmd)
    except OSError as exc:
        raise LintUnavailableError(f"Lint unavailable: {exc}") from exc
    return None if rc == 0 else f"Lint failed: {_last_line(out)}"


SECOND_FAILURE = "CHECK FAIL step {n}: second failure; stop the batch"


def _count_failure(
    rec_path: Path, rec: dict, brief: Path, n: int, reasons: list[str]
) -> list[str]:
    """Record one failed check against the step: one CHECK FAIL line per reason, and on the
    second failure the brief goes and the stop line prints so the batch stops (Requirement
    3d). The record stays with failures at two: the Stop hook reads it (ai-dlc-record R14),
    and check begin replaces it when the step begins again."""
    lines = [f"CHECK FAIL step {n}: {r}" for r in reasons]
    rec["failures"] = int(rec.get("failures", 0)) + 1
    rec_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    if rec["failures"] >= 2:
        brief.unlink(missing_ok=True)
        lines.append(SECOND_FAILURE.format(n=n))
    return lines


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def holdout_hash(tree: Path, holdout: str) -> str:
    """SHA-256 over the relative path and bytes of every file under the Holdout path in
    `tree`, in sorted path order (the one file when the path is a file), or the fixed text
    `absent` when the path does not exist. Each file adds its path, its length and its
    bytes, so no two trees hash alike. Files only: a mode change or an empty directory is
    not seen, and the guard's write-time rules stay the first line."""
    target = tree / holdout
    if target.is_file():
        files = [target]
    elif target.is_dir():
        files = [p for p in target.rglob("*") if p.is_file()]
    else:
        return "absent"
    h = hashlib.sha256()
    for rel in sorted(p.relative_to(tree).as_posix() for p in files):
        data = (tree / rel).read_bytes()
        h.update(f"{rel}\0{len(data)}\0".encode())
        h.update(data)
    return h.hexdigest()


def snapshot_path(state: dict, n: int) -> Path:
    return Path(state["run_dir"]) / f"plan-snapshot-{n}.md"


def record_snapshot_path(state: dict, n: int) -> Path:
    return Path(state["run_dir"]) / f"record-snapshot-{n}.md"


# a fix round re-begins a step at its commit; the step's first base and snapshots stay as
# its origin, so revert can still drop the whole step
def plan_origin_path(state: dict, n: int) -> Path:
    return Path(state["run_dir"]) / f"plan-origin-{n}.md"


def record_origin_path(state: dict, n: int) -> Path:
    return Path(state["run_dir"]) / f"record-origin-{n}.md"


def _keep_origin(state: dict, n: int) -> None:
    origin = {
        "base_head": state["base_head"],
        "plan_snapshot_sha256": state["plan_snapshot_sha256"],
    }
    shutil.copyfile(snapshot_path(state, n), plan_origin_path(state, n))
    if state.get("record_snapshot_sha256"):
        shutil.copyfile(record_snapshot_path(state, n), record_origin_path(state, n))
        origin["record_snapshot_sha256"] = state["record_snapshot_sha256"]
    state["step_origin"] = origin


def _reopen_from_origin(state: dict, n: int) -> None:
    """After a whole-step revert the run stands as after the step's first step-begin: its
    first base and snapshots are current again and the step has no commit."""
    origin = state.pop("step_origin")
    shutil.copyfile(plan_origin_path(state, n), snapshot_path(state, n))
    state["base_head"] = origin["base_head"]
    state["plan_snapshot_sha256"] = origin["plan_snapshot_sha256"]
    state.pop("record_snapshot_sha256", None)
    if origin.get("record_snapshot_sha256"):
        shutil.copyfile(record_origin_path(state, n), record_snapshot_path(state, n))
        state["record_snapshot_sha256"] = origin["record_snapshot_sha256"]
    state.get("steps", {}).pop(str(n), None)


def step_begin(root: Path, n: int, *, test_cmd: list[str] | None = None) -> dict:
    state = load_state(root)
    wt = Path(state["worktree"])
    # a begin on the step this run just committed is its fix round, based at that commit;
    # a repeated fix-round begin keeps the origin it found
    # and keeps the step's attempt count, so a whole-step revert's patch takes a new name
    fix_round = state.get("current_step") == n and str(n) in state.get("steps", {})
    if fix_round:
        if "step_origin" not in state:
            _keep_origin(state, n)
    else:
        state.pop("step_origin", None)
        state["attempt"] = 1
    state["fix_round"] = fix_round
    state["base_head"] = git(wt, "rev-parse", "HEAD").stdout.strip()
    state["current_step"] = n
    _set_plan_status(wt, state["plan"], n, "in-progress")
    plan_bytes = (wt / state["plan"]).read_bytes()
    # The plan as handed to the implementer. The gate requires the worktree copy to
    # still match it; the hash sits in state.json, which the hook protects, so the
    # reference cannot be rewritten from inside the run.
    snapshot_path(state, n).write_bytes(plan_bytes)
    state["plan_snapshot_sha256"] = sha256_bytes(plan_bytes)
    plan = next_plan.parse_plan(plan_bytes.decode("utf-8"))
    # the record's pending lines are the orchestrator's, like the plan's notes: revert puts
    # them back from this snapshot, so a dropped attempt leaves no line behind (R12)
    record = plan_record(plan)
    record_file = wt / record_path(record[1]) if record else None
    state.pop("record_snapshot_sha256", None)
    if record_file is not None and record_file.is_file():
        record_bytes = record_file.read_bytes()
        record_snapshot_path(state, n).write_bytes(record_bytes)
        state["record_snapshot_sha256"] = sha256_bytes(record_bytes)
    # the holdout as handed to the implementer; the gate recomputes it, so a write the
    # guard does not see (a script, an interpreter) still cannot change the holdout
    state.pop("holdout_sha256", None)
    if state.get("holdout"):
        state["holdout_sha256"] = holdout_hash(wt, state["holdout"])
    brief = next_plan.render_brief(plan, n)
    (Path(state["run_dir"]) / f"brief-{n}.md").write_text(brief, encoding="utf-8")
    # Continuity travels beside the brief, never inside it (see render_context).
    context = next_plan.render_context(plan, n)
    (Path(state["run_dir"]) / f"context-{n}.md").write_text(context, encoding="utf-8")

    # baseline test counts, so the gate can require collected non-decreasing and skips
    # non-increasing. Unrunnable baselines record None and the gate skips the comparison.
    cmd = test_cmd or plan_commands(wt, state["plan"])[0]
    try:
        state["base_counts"] = list(pytest_counts(wt, cmd)) if cmd else None
    except OSError:
        state["base_counts"] = None
    save_state(state)
    return state


# ---- gate sub-checks --------------------------------------------------------


def check_head_unchanged(worktree: Path, base_head: str) -> str | None:
    now = git(worktree, "rev-parse", "HEAD").stdout.strip()
    if now != base_head:
        return (
            f"HEAD moved during the step ({base_head[:8]} -> {now[:8]}); "
            "an implementer commit is a rule break"
        )
    return None


def _verified_file(
    snap: Path, recorded: str | None, what: str, n: int
) -> tuple[bytes | None, str | None]:
    """A snapshot's bytes, or a reason it cannot be trusted: its hash sits in state.json,
    which the hook protects, so the file cannot be rewritten from inside the run."""
    if not snap.exists() or not recorded:
        return None, f"no {what} snapshot for step {n} (run step-begin first)"
    data = snap.read_bytes()
    if sha256_bytes(data) != recorded:
        return None, f"{what} snapshot {snap} does not match the hash recorded at step-begin"
    return data, None


def _verified_snapshot(state: dict, n: int) -> tuple[bytes | None, str | None]:
    """The step's plan snapshot bytes, or a reason it cannot be trusted."""
    return _verified_file(snapshot_path(state, n), state.get("plan_snapshot_sha256"), "plan", n)


def check_plan_unchanged(state: dict, n: int) -> str | None:
    """The worktree plan must be byte-identical to its step-begin snapshot. The
    implementer never edits prompt_plan.md; the orchestrator's own set and append run
    after the gate and land in the next step's snapshot."""
    snapshot, reason = _verified_snapshot(state, n)
    if reason:
        return reason
    current = (Path(state["worktree"]) / state["plan"]).read_bytes()
    if current != snapshot:
        return (
            f"{state['plan']} changed during the step; the implementer may not edit the "
            f"plan (snapshot: {snapshot_path(state, n)})"
        )
    return None


def check_record_unchanged(state: dict, n: int) -> str | None:
    """With Record, the worktree record must be byte-identical to its step-begin snapshot,
    or absent when it was absent then: during a step only the engine's append after commit
    writes it (ai-dlc-record R12)."""
    record = plan_record(_worktree_plan(state))
    if record is None:
        return None
    expected = None
    if recorded := state.get("record_snapshot_sha256"):
        expected, reason = _verified_file(record_snapshot_path(state, n), recorded, "record", n)
        if reason:
            return reason
    current_file = Path(state["worktree"]) / record_path(record[1])
    current = current_file.read_bytes() if current_file.is_file() else None
    if current != expected:
        return (
            f"{record_path(record[1])} changed during the step; the implementer may not edit "
            f"the record (snapshot: {record_snapshot_path(state, n)})"
        )
    return None


def check_holdout_unchanged(state: dict) -> str | None:
    """The Holdout path must hash as it did at step-begin; a plan with no Holdout key
    recorded no hash and skips the check."""
    recorded = state.get("holdout_sha256")
    if not recorded:
        return None
    if holdout_hash(Path(state["worktree"]), state["holdout"]) != recorded:
        return (
            f"Holdout {state['holdout']} changed during the step; the implementer may not "
            "edit the holdout"
        )
    return None


def stage_all(worktree: Path, index: Path | None = None) -> None:
    git(worktree, "add", "-A", index=index)


def staged_files(worktree: Path, index: Path | None = None, base: str | None = None) -> list[str]:
    """The paths the index changes against `base`, or against HEAD when there is none."""
    against = [base] if base else []
    out = git(worktree, "diff", "--cached", "--name-only", *against, index=index).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def check_staged_safe(
    worktree: Path,
    staged: list[str],
    *,
    untracked_plan: str | None = None,
    toolchain: list[str] | None = None,
) -> str | None:
    """The first reason a staged path may not be committed, or None. `toolchain` is the
    plan's Toolchain-files globs: a CLAUDE.md or .claude/ path one of them matches is
    admitted, and every other rule still applies to it."""
    for f in staged:
        parts = Path(f).parts
        base = Path(f).name
        if untracked_plan is not None and Path(f) == Path(untracked_plan):
            return f"staged the plan, which this repo keeps untracked by design: {f}"
        if base.startswith(".env") or ".env" in parts or any(p.startswith(".env") for p in parts):
            return f"staged a secret-shaped file: {f}"
        if (base == "CLAUDE.md" or ".claude" in parts) and not any(
            glob_match(f, g) for g in toolchain or []
        ):
            return f"staged toolchain file: {f}"
        if RUN_SUBDIR in parts:
            return f"staged run-directory file: {f}"
        blob = worktree / f
        if not blob.is_file():
            continue
        size = blob.stat().st_size
        if size > MAX_BYTES:
            return f"staged file over 1 MB: {f} ({size} bytes)"
        data = blob.read_bytes()
        if b"\x00" in data:
            continue  # binary; the size cap is its only check
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        hits = scan_secrets(text)
        if hits:
            return f"staged secret-shaped content in {f}: {', '.join(hits)}"
    return None


def check_gate_config(staged: list[str], brief_text: str) -> str | None:
    for f in staged:
        base = Path(f).name
        if base in GATE_CONFIG_FILES and base not in brief_text and f not in brief_text:
            return f"changed gate/config file not named in the brief: {f}"
    return None


def _segment_regex(segment: str) -> str:
    """One path segment of a glob as a regex: `*` and `?` never cross a `/`; `[...]` is a
    character class, `[!...]` its negation, and an unclosed `[` is literal."""
    out, i = [], 0
    while i < len(segment):
        c = segment[i]
        i += 1
        if c == "*":
            if not out or out[-1] != "[^/]*":
                out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = i + (1 if segment[i : i + 1] == "!" else 0)
            j += 1 if segment[j : j + 1] == "]" else 0
            while j < len(segment) and segment[j] != "]":
                j += 1
            if j >= len(segment):
                out.append("\\[")
                continue
            chars = segment[i:j].replace("\\", "\\\\")
            i = j + 1
            if chars.startswith("!"):
                chars = "^" + chars[1:]
            elif chars.startswith("^"):
                chars = "\\" + chars
            out.append(f"[{chars}]")
        else:
            out.append(re.escape(c))
    return "".join(out)


def glob_match(path: str, pattern: str) -> bool:
    """True when the whole path matches the glob, as pathlib's PurePath.full_match does from
    Python 3.13 (the scripts run on 3.12, which lacks it): `**` spans zero or more segments,
    other wildcards stay within one, and hidden names match."""
    parts = str(PurePosixPath(pattern)).split("/")
    out = []
    for idx, part in enumerate(parts):
        last = idx == len(parts) - 1
        if part == "**":
            if last:
                out.append(".*")
            elif parts[idx + 1] != "**":
                out.append("(?:.+/)?")
            continue
        out.append("[^/]+" if part == "*" else _segment_regex(part))
        if not last:
            out.append("/")
    return re.fullmatch("".join(out), str(PurePosixPath(path)), re.S) is not None


def _staged_test_files(staged: list[str], patterns: list[str] | None = None) -> list[str]:
    """The staged test files prove-red applies to base: those matching a plan's Test-files
    globs when it has them, else the built-in Python rule."""
    if patterns is not None:
        return [f for f in staged if any(glob_match(f, p) for p in patterns)]
    out = []
    for f in staged:
        base = Path(f).name
        is_test = (
            base.startswith("test_")
            or base.endswith("_test.py")
            or "tests/" in f.replace("\\", "/")
        )
        if is_test and f.endswith(".py"):
            out.append(f)
    return out


# what a comment-only fix round may touch: documentation, and comments by each language's
# markers; a Python docstring is code. In the slash languages a line starting `*` is a comment
# only inside a block, since `*count += 1`, `*p = v` and `*[Symbol.iterator]() {` are code
DOC_SUFFIXES = (".md", ".rst", ".txt")
_HASH_MARKERS = ("#",)
_SLASH_MARKERS = ("//", "/*", "*/")
COMMENT_MARKERS = {
    **dict.fromkeys((".py", ".sh", ".toml", ".yaml", ".yml"), _HASH_MARKERS),
    **dict.fromkeys((".ts", ".tsx", ".js", ".mjs", ".cjs", ".go", ".rs"), _SLASH_MARKERS),
}
COMMENT_ONLY_ROUND = "comment-only fix round"
# a whole file's context in one hunk, so a changed `*` line is read against the lines above it
_WHOLE_FILE = "--unified=1000000"


def _in_block(lines: list[str], i: int) -> bool:
    """Line i sits inside a `/* ... */` block: the lines above it, blank and `*` lines
    passed over, lead back to a line opening one, and none of them closes one."""
    for prev in reversed(lines[:i]):
        s = prev.strip()
        if "*/" in s:
            return False
        if s.startswith("/*"):
            return True
        if s and not s.startswith("*"):
            return False
    return False


def comment_only(path: str, lines: list[str], changed: list[int] | None = None) -> bool:
    """True for a documentation file, or a file whose changed lines (all of `lines` when
    `changed` is None), blank lines aside, are each a comment in its language; `lines` is one
    side of the file in order, which a `*` line is read against. False for a suffix with no
    markers here."""
    suffix = PurePosixPath(path).suffix
    if suffix in DOC_SUFFIXES:
        return True
    markers = COMMENT_MARKERS.get(suffix)
    if markers is None:
        return False
    for i in range(len(lines)) if changed is None else changed:
        s = lines[i].strip()
        if not s or s.startswith(markers):
            continue
        if markers is _SLASH_MARKERS and s.startswith("*") and _in_block(lines, i):
            continue
        return False
    return True


def _diff_sides(
    tree: Path, base: str, path: str, *, index: Path | None = None
) -> tuple[tuple[list[str], list[int]], tuple[list[str], list[int]]]:
    """One staged path against `base` as its two sides, each the side's lines in order with
    the indexes of its removed or added lines; rename detection is off, so a renamed file
    reads as its whole removal and addition."""
    out = git(
        tree, "diff", "--cached", _WHOLE_FILE, "--no-renames", base, "--", path, index=index
    ).stdout
    old: tuple[list[str], list[int]] = ([], [])
    new: tuple[list[str], list[int]] = ([], [])
    body = False
    for ln in out.splitlines():
        if ln.startswith("@@"):
            body = True
            continue
        if not body or ln.startswith("\\"):
            continue
        mark, text = ln[:1], ln[1:]
        for side, changed_mark in ((old, "-"), (new, "+")):
            if mark in (" ", changed_mark):
                if mark == changed_mark:
                    side[1].append(len(side[0]))
                side[0].append(text)
    return old, new


def _block_states(lines: list[str]) -> list[bool]:
    """For each line, whether it starts inside a `/* ... */` block opened on a line above it;
    a `//` before any opener ends the line's reading, and markers inside string literals are
    not parsed."""
    states = []
    inside = False
    for ln in lines:
        states.append(inside)
        j = 0
        while True:
            if inside:
                k = ln.find("*/", j)
                if k < 0:
                    break
                inside, j = False, k + 2
                continue
            k = ln.find("/*", j)
            slash = ln.find("//", j)
            if k < 0 or 0 <= slash < k:
                break
            inside, j = True, k + 2
    return states


def _unchanged_block_states(lines: list[str], changed: list[int]) -> list[bool]:
    """The block states of one side's unchanged lines, in order."""
    skip = set(changed)
    return [s for i, s in enumerate(_block_states(lines)) if i not in skip]


def comment_only_round(
    tree: Path, base: str, staged: list[str], *, index: Path | None = None
) -> bool:
    """Every staged path of a fix round is comment_only against the round's base, on both
    sides of its diff, and in the slash languages no unchanged line moves into or out of a
    `/* */` block between the sides, as one does when the round deletes the `/*` and `*/`
    around code or adds them around live code."""
    for f in staged:
        old, new = _diff_sides(tree, base, f, index=index)
        if not (comment_only(f, *old) and comment_only(f, *new)):
            return False
        slash = COMMENT_MARKERS.get(PurePosixPath(f).suffix) is _SLASH_MARKERS
        if slash and _unchanged_block_states(*old) != _unchanged_block_states(*new):
            return False
    return True


# module-resolution failures as each runner prints them: pytest's names the dotted module, node's
# (an ES import, or a CommonJS require and its stack) and Bun's name the specifier and the
# importing file
_PYTEST_UNRESOLVED_RE = re.compile(r"^E\s+ModuleNotFoundError: No module named '([^']+)'", re.M)
_NODE_UNRESOLVED_RE = re.compile(
    r"Cannot find (?:module|package) '([^']+)' imported from ([^\s\"]+)"
)
_NODE_REQUIRE_RE = re.compile(r"Cannot find module '([^']+)'\n[ #]*Require stack:\n[ #]*- (\S+)")
# a failed node test point: the comment lines just above it, its indent, its title and its YAML
# block. A test's own error is the YAML's `error:` field; a file that fails to load prints its
# error as comment lines above its `# Subtest:`, from the last error header on, and its YAML
# has no `error:` field (an ES module) or an `exitCode:` beside "test failed" (a CommonJS file
# that crashed its test process). A todo or skip never fails the run; a suite whose subtests
# failed is no failure of its own, where a test whose subtests failed counts under fail; a
# test cancelled by its parent is explained only by that parent's failed hook. A logged
# message above a test that passed is not read.
_NODE_FAILED_RE = re.compile(
    r"((?:^ *#[^\n]*\n)*)^( *)not ok \d+ - ([^\n]*)\n *---\n(.*?)^ *\.\.\.$", re.M | re.S
)
_NODE_ERROR_HEAD_RE = re.compile(r"^ *# (?:\w+\.)*\w*Error(?: \[\w+\])?: ", re.M)
_BUN_UNRESOLVED_RE = re.compile(r"Cannot find (?:module|package) '([^']+)' from '([^']+)'")
# Bun prints a file that fails to load as an unhandled error between tests, once per module
# that failed, and an empty block for each other file that imported it; such a file counts
# under fail when its tests never registered and under error alone when they did. A test
# that ran and failed prints its error lines and then a `(fail) ` line (U+2717 under
# FORCE_COLOR), after its file's `<path>:` header or the failed test before it; a passing test
# prints no line of its own, so its logged output joins the next failed test's section.
_BUN_UNHANDLED_RE = re.compile(r"^# Unhandled error between tests\n-+\n(.*?)^-+$", re.M | re.S)
_BUN_RESULT_RE = re.compile(r"^(?:\((pass|fail|skip|todo)\)|(\u2717)) ")
# a timed-out test prints this under its result line and no error of its own
_BUN_TIMEOUT_RE = re.compile(r"^ *\^ this test timed out", re.M)
_BUN_FILE_RE = re.compile(r"^\S+:$")
# pytest's last line (`3 errors in 0.07s`, `=== 1 failed, 2 passed in 0.12s ===`), or the
# conftest that failed to load, which ends the run before any summary
_PYTEST_RUN_RE = re.compile(
    r"^=*\s*(?:\d+ \w+(?:, )?)+ in [\d.]+s|^ImportError while loading conftest", re.M
)
_JS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".json")


_PYTHON_ROOTS = ("", "src/")


def _python_roots(tree: Path) -> tuple[str, ...]:
    """The tree-relative roots a dotted Python name resolves under: the top level and src/
    (ruled D2), plus every entry of `[tool.pytest.ini_options].pythonpath` in the tree's
    pyproject.toml (D2 amended 2026-10-17 for a plugin's `scripts/` layout), since pytest
    imports from those on the step's tree. A tree with no pyproject, or one whose pyproject
    names no pythonpath, keeps the two defaults; a pyproject that does not parse fails Test
    itself before prove-red reads it, so it is not caught here."""
    pyproject = tree / "pyproject.toml"
    if not pyproject.is_file():
        return _PYTHON_ROOTS
    options = tomllib.loads(pyproject.read_text()).get("tool", {}).get("pytest", {})
    declared = options.get("ini_options", {}).get("pythonpath", [])
    if isinstance(declared, str):
        declared = [declared]
    extra = tuple(
        f"{entry.strip('/')}/" for entry in declared if isinstance(entry, str) and entry.strip("/")
    )
    return _PYTHON_ROOTS + tuple(root for root in extra if root not in _PYTHON_ROOTS)


def _module_paths(
    spec: str, importer: str | None, scratch: Path, roots: tuple[str, ...] = _PYTHON_ROOTS
) -> list[str]:
    """The tree paths that would hold a module, relative to the scratch: a dotted Python name
    under one of `roots` (the top level and src/ by D2, plus the tree's declared pytest
    pythonpath), a relative or absolute JavaScript specifier with its extensions and index
    files. A bare package name holds none: it is never the step's."""
    if importer is None:
        stem = spec.replace(".", "/")
        return [f"{root}{stem}{tail}" for root in roots for tail in (".py", "/__init__.py")]
    if spec.startswith("/"):
        target = Path(spec)
    elif spec.startswith("."):
        target = Path(importer).parent / spec
    else:
        return []
    rel = os.path.relpath(os.path.realpath(target), os.path.realpath(scratch))
    if rel.startswith(".."):
        return []
    stem = rel.removesuffix(Path(rel).suffix) if Path(rel).suffix in _JS_SUFFIXES else rel
    return [rel] + [stem + x for x in _JS_SUFFIXES] + [f"{rel}/index{x}" for x in _JS_SUFFIXES]


def _node_misses(text: str, failed: int) -> list[tuple[str, str]] | None:
    """The unresolved modules node's failed tests name, when every failed test is one and
    the failed tests read are exactly `# fail`, else None."""
    entries = []
    for above, indent, title, yaml in _NODE_FAILED_RE.findall(text):
        if re.search(r" # (?:TODO|SKIP)\b", title, re.I):
            continue
        kind = re.search(r"^ *failureType: '(\w+)'", yaml, re.M)
        entries.append((len(indent), kind.group(1) if kind else "", above, yaml))
    counted, misses = 0, []
    for i, (indent, kind, above, yaml) in enumerate(entries):
        # node's `# fail` counts tests, never suites
        test = not re.search(r"^ *type: 'suite'", yaml, re.M)
        if kind == "subtestsFailed":
            counted += test
            continue
        if kind == "cancelledByParent":
            parent = next((e for e in entries[i + 1 :] if e[0] < indent), None)
            if parent is None or parent[1] != "hookFailed":
                return None
            continue
        has_error = re.search(r"^ *error: ", yaml, re.M)
        if has_error and not re.search(r"^ *exitCode: ", yaml, re.M):
            source = yaml
        else:
            heads = list(_NODE_ERROR_HEAD_RE.finditer(above))
            source = above[heads[-1].start() :] if heads else ""
        found = _NODE_UNRESOLVED_RE.findall(source) + _NODE_REQUIRE_RE.findall(source)
        if not found:
            return None
        counted += test
        misses += found
    return misses if counted == failed else None


def _bun_misses(text: str, counts: list[dict]) -> list[tuple[str, str]] | None:
    """The unresolved modules Bun's unhandled errors and failed tests name, when every one of
    them is a resolution failure and they cover its fail count, else None; [] for a run that
    counted no failure and no error. A timed-out test is a failure of another kind."""
    failed = sum(c["fail"] for c in counts)
    if not failed and not sum(c["error"] for c in counts):
        return []
    if _BUN_TIMEOUT_RE.search(text):
        return None
    blocks = _BUN_UNHANDLED_RE.findall(text)
    misses: list[tuple[str, str]] = []
    for block in blocks:
        if block.strip():
            found = _BUN_UNRESOLVED_RE.findall(block)
            if not found:
                return None
            misses += found
    fails, section = 0, []
    for line in _BUN_UNHANDLED_RE.sub("", text).splitlines():
        result = _BUN_RESULT_RE.match(line)
        if result and (result.group(1) == "fail" or result.group(2)):
            errors = [ln for ln in section if ln.startswith("error: ")]
            if not errors or not all(_BUN_UNRESOLVED_RE.search(ln) for ln in errors):
                return None
            misses += [f for ln in errors for f in _BUN_UNRESOLVED_RE.findall(ln)]
            fails += 1
        if result or _BUN_FILE_RE.match(line):
            section = []
        else:
            section.append(line)
    return misses if misses and fails + len(blocks) >= failed else None


def _runner_misses(text: str) -> list[tuple[str, str | None]] | None:
    """Every unresolved module a failing run names, when each failure it counted is one, or
    None when any failure is something else or no runner the engine reads printed a summary
    (ruled D3: any other runner keeps the old rule). Node's are read from its failed tests
    against `# fail`, on a fail or a cancelled count; Bun's from its unhandled errors, since a
    file that imports a module another file already failed on prints an empty block, and from
    its failed tests, against its fail and error counts; pytest's from its `E` lines against
    its failed and errors."""
    node = _block_counts(text, _NODE_BLOCK_RE, _NODE_SUMMARY_RE)
    bun = _block_counts(text, _BUN_BLOCK_RE, _BUN_SUMMARY_RE)
    misses: list[tuple[str, str | None]] = []
    if node and sum(c["fail"] + c["cancelled"] for c in node):
        found = _node_misses(text, sum(c["fail"] for c in node))
        if found is None:
            return None
        misses += found
    if bun:
        found = _bun_misses(text, bun)
        if found is None:
            return None
        misses += found
    if not node and not bun:
        if not _PYTEST_RUN_RE.search(text):
            return None
        found = {k: rx.search(text) for k, rx in _SUMMARY_RE.items()}
        failed = sum(int(found[k].group(1)) for k in ("failed", "error") if found[k])
        python = _PYTEST_UNRESOLVED_RE.findall(text)
        if len(python) < max(failed, 1):
            return None
        misses += [(spec, None) for spec in python]
    return misses


def foreign_resolution(output: str, scratch: Path, changed: set[str], tree: Path) -> str | None:
    """Why a base run's red proves nothing, or None when it stands: every failure the run
    counted is a module that did not resolve, and one of those modules is not the step's. A
    module is the step's when it is a path the step adds or changes, or a relative or absolute
    JavaScript module the step's tree holds at none of its paths (ruled 2026-10-02, OPEN 5 (B)):
    the tree no longer needs it, or Test fails there and counts the attempt. A module the tree
    holds and the step does not add, such as an ignored generated file, stays foreign, as does
    a package, a Python name and a path outside the scratch. A run with any other failure
    stands as red."""
    misses = _runner_misses(_ANSI_RE.sub("", output))
    roots = _python_roots(tree)
    for spec, importer in misses or []:
        paths = _module_paths(spec, importer, scratch, roots)
        if any(path in changed for path in paths):
            continue
        if importer is not None and paths and not any((tree / p).exists() for p in paths):
            continue
        where = f" (imported from {os.path.relpath(importer, scratch)})" if importer else ""
        return (
            f"prove-red: every failure on base is a module that does not resolve, and "
            f"{spec!r}{where} is not one the step adds, so the red proves nothing; the "
            "scratch cannot load it"
        )
    return None


def prove_red(
    root: Path,
    worktree: Path,
    base_head: str,
    staged: list[str],
    test_cmd: list[str],
    *,
    patterns: list[str] | None = None,
    index: Path | None = None,
    guard: tuple[str, str, str] | None = None,
    proof: dict | None = None,
) -> str | None:
    """Apply the step's staged test files onto base in a temp worktree; require a red run.
    `index` names the index the step was staged into when it is not the tree's own.
    A base run that passes is red by the step's `guard` when it carries one: (path, old,
    new), with old occurring once in path at base, replaced in the same scratch and Test run
    again, a non-zero exit proving the guard; `proof` then gains `guard_mutation`. A red base
    never runs the mutation.
    The scratch has no installed dependencies, so every node_modules the tree has beside a
    directory the scratch holds is linked in once the step's tests are written, a new
    package's own install among them: a red made only of modules the scratch cannot load,
    where one is not the step's, proves nothing and raises ProveRedApparatusError. Node would
    otherwise resolve packages up the parent chain, which from the scratch under the root
    reaches the root's node_modules, not a run worktree's.
    A test file the step deletes, or renames away from, has no staged blob at that path; it
    is removed from the scratch so the base runs without it, and it never counts as the
    step's test change. Rename detection is off for that list, since a rename's old path is
    otherwise listed nowhere. The list is read against `base_head`, so a test deleted in a
    commit part way through an attended step leaves the scratch too."""
    test_files = _staged_test_files(staged, patterns)
    deleted = deleted_files(worktree, base_head, index=index)
    written = [f for f in test_files if f not in deleted]
    if not written:
        return "no staged test files; a step must add or change a test first (Mutation/test-first)"
    with scratch_worktree(root, base_head) as scratch:
        # removals first, so a path the step turns from a file into a directory is free
        for f in _staged_test_files(sorted(deleted), patterns):
            (scratch / f).unlink(missing_ok=True)
        for f in written:
            content = git(worktree, "show", f":{f}", index=index).stdout
            dest = scratch / f
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        # after the writes, so a package the step adds gets its own install too
        link_node_modules(worktree, scratch)
        r = subprocess.run(
            test_cmd, cwd=str(scratch), capture_output=True, text=True, env=test_env(test_cmd)
        )
        changed = set(staged) - deleted
        # a base that ran no test is apparatus, never red, so it is read before the modules
        nothing = collected_nothing(r.returncode, r.stdout + r.stderr)
        unproved = None if r.returncode == 0 or nothing else foreign_resolution(
            r.stdout + r.stderr, scratch, changed, worktree
        )
        if r.returncode == 0 and guard is not None:
            return _guard_red(scratch, guard, test_cmd, proof)
    if r.returncode == 0:
        return (
            "prove-red failed: the step's new/changed tests PASS against the base commit, "
            "so they do not demonstrate test-first"
        )
    if nothing:
        raise ProveRedApparatusError(f"Test collected no test at base: {nothing}")
    if unproved:
        raise ProveRedApparatusError(unproved)
    return None


def _guard_red(
    scratch: Path, guard: tuple[str, str, str], test_cmd: list[str], proof: dict | None
) -> str | None:
    """The guard mutation's run in prove-red's scratch: None when Test goes red with `old`
    replaced by `new` in `path`, else the refusal."""
    path, old, new = guard
    target = scratch / path
    text = target.read_text(encoding="utf-8") if target.is_file() else ""
    k = text.count(old)
    if k != 1:
        return f"Guard-mutation: {old} occurs {k} times in {path} at base"
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    r = subprocess.run(
        test_cmd, cwd=str(scratch), capture_output=True, text=True, env=test_env(test_cmd)
    )
    if r.returncode == 0:
        return (
            "prove-red failed: the step's tests still pass with the guard mutation applied to "
            f"{path}"
        )
    if proof is not None:
        proof["guard_mutation"] = {"path": path, "old": old, "new": new}
    return None


def deleted_files(worktree: Path, base_head: str, *, index: Path | None = None) -> set[str]:
    """The paths the staged step deletes, or renames away from, against `base_head`. Rename
    detection is off, since a rename's old path is otherwise listed nowhere."""
    return set(
        git(
            worktree, "diff", "--cached", "--name-only", "--no-renames", "--diff-filter=D",
            base_head, index=index,
        ).stdout.splitlines()
    )


def adjusted_base_counts(
    root: Path, base: str, named_deleted: list[str], test_cmd: list[str], *, tree: Path
) -> tuple[int, int, int]:
    """The base's counts with the step's named, deleted test files taken out: Test run in a
    scratch worktree at `base` with those files unlinked and `tree`'s installs linked in, as
    the base counts were measured in `tree`."""
    with scratch_worktree(root, base) as scratch:
        for f in named_deleted:
            (scratch / f).unlink(missing_ok=True)
        link_node_modules(tree, scratch)
        return pytest_counts(scratch, test_cmd)


def count_floor(
    root: Path,
    tree: Path,
    base: str,
    base_counts: list | None,
    named: list[str] | None,
    test_cmd: list[str],
    *,
    patterns: list[str] | None = None,
    index: Path | None = None,
) -> list | tuple | None:
    """The counts a step is held to: the base's, or the base's without the test files the step
    deletes that its `Deletes:` line also names. A deletion the line does not name keeps the
    base's floor, a named file the step keeps changes nothing, and with nothing qualifying no
    scratch run is paid for."""
    if not base_counts or not named:
        return base_counts
    gone = _staged_test_files(sorted(deleted_files(tree, base, index=index)), patterns)
    qualifying = [f for f in gone if f in set(named)]
    if not qualifying:
        return base_counts
    return adjusted_base_counts(root, base, qualifying, test_cmd, tree=tree)


@contextlib.contextmanager
def scratch_worktree(root: Path, sha: str):
    """A detached worktree at `sha` under the run directory, removed on every exit path so
    `git worktree list` is as it was. Shared by prove_red (the step's tests on base) and
    merge (the suite and lint on a PR head)."""
    run_base(root).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="next-auto-scratch-", dir=str(run_base(root))))
    scratch = tmp / "wt"
    try:
        git(root, "worktree", "add", "--detach", str(scratch), sha)
        yield scratch
    finally:
        git(root, "worktree", "remove", "--force", str(scratch), check=False)
        _remove_tree(tmp)


def _remove_tree(path: Path) -> None:
    """Best-effort removal of a scratch directory under the run dir. A failure is reported on
    stderr and never raised: the gate's verdict does not depend on the cleanup, but a
    leftover tree should say why it is there."""

    def report(_func, failed_path, exc):
        print(f"prove-red: could not remove {failed_path}: {exc}", file=sys.stderr)

    shutil.rmtree(path, onexc=report)


_SUMMARY_RE = {
    "passed": re.compile(r"(\d+) passed"),
    "failed": re.compile(r"(\d+) failed"),
    "skipped": re.compile(r"(\d+) skipped"),
    "xfailed": re.compile(r"(\d+) xfailed"),
    "error": re.compile(r"(\d+) error"),
}


# node 23 and later print the spec reporter when their output is captured, and the readers below
# are TAP readers, so a Test runs with the TAP reporter pinned through NODE_OPTIONS; a reporter
# the command or the variable already names is left alone, since a second reporter without its
# destination is a node error. A runner that is not node ignores the variable.
_NODE_TAP_OPTIONS = "--test-reporter=tap --test-reporter-destination=stdout"


def test_env(cmd: list[str]) -> dict[str, str]:
    """The environment a Test or Lint command runs in: the process environment, with node's TAP
    reporter pinned unless the command or NODE_OPTIONS names a reporter."""
    env = dict(os.environ)
    held = env.get("NODE_OPTIONS", "")
    if any("--test-reporter" in a for a in cmd) or "--test-reporter" in held:
        return env
    env["NODE_OPTIONS"] = f"{held} {_NODE_TAP_OPTIONS}".strip()
    return env


def run_check(worktree: Path, cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=str(worktree), capture_output=True, text=True, env=test_env(cmd))
    return r.returncode, r.stdout + r.stderr


# the node test runner's summary as it prints it when its output is captured (TAP): a block
# of `# <name> <number>` lines from `# tests` on, with a pass and a fail line among them
_NODE_BLOCK_RE = re.compile(r"^# tests \d+\n(?:# [a-z_]+ [\d.]+(?:\n|$))+", re.M)
_NODE_SUMMARY_RE = {
    k: re.compile(rf"^# {k} (\d+)$", re.M)
    for k in ("tests", "pass", "fail", "cancelled", "skipped", "todo")
}
# Bun's summary, which `bun test` prints after the failures: count lines from `pass` on, with a
# `snapshots:` line when a snapshot was written, then `Ran N tests`, which counts every test,
# skipped and todo ones included; a file that fails to import counts as one failed test
_BUN_BLOCK_RE = re.compile(
    r"^ *\d+ pass\n(?:(?: *\d+ |snapshots: )[^\n]*\n)*Ran \d+ tests? across \d+ files?\.", re.M
)
_BUN_SUMMARY_RE = {
    "tests": re.compile(r"^Ran (\d+) tests? across", re.M),
    "pass": re.compile(r"^ *(\d+) pass$", re.M),
    "fail": re.compile(r"^ *(\d+) fail$", re.M),
    "skipped": re.compile(r"^ *(\d+) skip$", re.M),
    "todo": re.compile(r"^ *(\d+) todo$", re.M),
    "error": re.compile(r"^ *(\d+) errors?$", re.M),
}
# the color codes a runner writes when FORCE_COLOR is set
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _block_counts(text: str, block: re.Pattern[str], fields: dict[str, re.Pattern[str]]) -> list:
    """Each whole summary block one runner printed, read field by field; a line shaped like a
    summary line but standing apart from a block is a test's own output and counts nothing."""
    out = []
    for m in block.finditer(text):
        got = {k: rx.search(m.group(0)) for k, rx in fields.items()}
        if got["pass"] and got["fail"]:
            out.append({k: int(g.group(1)) if g else 0 for k, g in got.items()})
    return out


def summary_counts(text: str) -> tuple[int, int, int, int]:
    """(passed, collected, skipped, xfailed) from a suite's output: the node test runner's
    `# tests N` summary and Bun's `Ran N tests` summary, summed when one Test runs both, with
    todo standing in for xfailed, else pytest's summary line. Any other runner reads as
    zeros."""
    text = _ANSI_RE.sub("", text)
    found = _block_counts(text, _NODE_BLOCK_RE, _NODE_SUMMARY_RE) + _block_counts(
        text, _BUN_BLOCK_RE, _BUN_SUMMARY_RE
    )
    if found:
        get = {k: sum(c[k] for c in found) for k in ("tests", "pass", "skipped", "todo")}
        return get["pass"], get["tests"], get["skipped"], get["todo"]
    counts = _pytest_counts(text)
    return counts["passed"], sum(counts.values()), counts["skipped"], counts["xfailed"]


def _pytest_counts(text: str) -> dict[str, int]:
    """pytest's summary line read field by field, each field absent from it a zero."""
    return {
        k: (int(m.group(1)) if (m := rx.search(text)) else 0) for k, rx in _SUMMARY_RE.items()
    }


# the zero-test lines each runner prints, as live runs print them: pytest's exit-5 summary,
# with or without -q, and Bun's two no-test-file lines; the node runner's is its `# tests 0`
# block, read by _NODE_BLOCK_RE
_PYTEST_NOTHING_RE = re.compile(r"^=*\s*(?:\d+ deselected|no tests ran) in [\d.]+s\s*=*$", re.M)
_BUN_NOTHING_RE = re.compile(
    r"^(?:The following filters did not match any test files|error: 0 test files matching) ",
    re.M,
)


def collected_nothing(rc: int, text: str) -> str | None:
    """The last line of a run that exited non-zero having collected no test, in a shape read
    here (pytest's exit 5, the node runner's `# tests 0` block, Bun's no-test-file lines), or
    None: a run that exited 0, prints nothing read here, or in which any runner it ran
    reports a test keeps its red. Each runner's summary is read on its own, so a Test that
    chains node and pytest counts pytest's tests too."""
    if rc == 0:
        return None
    clean = _ANSI_RE.sub("", text)
    node = _block_counts(clean, _NODE_BLOCK_RE, _NODE_SUMMARY_RE)
    bun = _block_counts(clean, _BUN_BLOCK_RE, _BUN_SUMMARY_RE)
    zero = (
        (rc == 5 and _PYTEST_NOTHING_RE.search(clean) is not None)
        or _BUN_NOTHING_RE.search(clean) is not None
        or any(b["tests"] == 0 for b in node)
    )
    ran = sum(b["tests"] for b in node + bun) + sum(_pytest_counts(clean).values())
    if zero and ran == 0:
        return _last_line(clean)
    return None


def parse_counts(text: str) -> tuple[int, int, int]:
    _passed, collected, skipped, xfailed = summary_counts(text)
    return collected, skipped, xfailed


def pytest_counts(worktree: Path, test_cmd: list[str]) -> tuple[int, int, int]:
    return parse_counts(run_check(worktree, test_cmd)[1])


def check_counts(base: tuple | list | None, cur: tuple[int, int, int]) -> str | None:
    """Collected must not shrink; skips and xfails must not grow (C6, gate-gaming)."""
    if not base:
        return None
    b_collected, b_skipped, b_xfailed = base
    c_collected, c_skipped, c_xfailed = cur
    if c_collected < b_collected:
        return f"collected tests dropped from {b_collected} to {c_collected}"
    if c_skipped > b_skipped:
        return f"skipped tests grew from {b_skipped} to {c_skipped}"
    if c_xfailed > b_xfailed:
        return f"xfailed tests grew from {b_xfailed} to {c_xfailed}"
    return None


def _diff_lines(worktree: Path, *args: str, index: Path | None = None) -> int:
    out = git(worktree, "diff", "--shortstat", *args, index=index).stdout
    ins = re.search(r"(\d+) insertion", out)
    dels = re.search(r"(\d+) deletion", out)
    return (int(ins.group(1)) if ins else 0) + (int(dels.group(1)) if dels else 0)


def check_step_size(
    worktree: Path,
    base_head: str,
    limit: int = next_plan.STEP_SIZE_DEFAULT,
    *,
    estimate: int | None = None,
    changed: int | None = None,
) -> str | None:
    """The per-step size gate: the staged diff against the base, insertions plus deletions,
    against the plan's limit. The refusal names the step's Size: estimate beside the measured
    count, so the two are on record where the plan missed. A caller that has measured the
    staged diff already passes it as changed; the diff is read only without it."""
    if changed is None:
        changed = _diff_lines(worktree, "--cached", base_head)
    if changed > limit:
        tail = f"estimated {estimate}" if estimate is not None else "no estimate"
        return f"step changes {changed} lines, over the {limit}-line per-step cap ({tail})"
    return None


def run_size(worktree: Path, default: str) -> int:
    return _diff_lines(worktree, f"{default}...HEAD")


# ---- composed gate ----------------------------------------------------------


def gate(
    root: Path,
    n: int,
    *,
    test_cmd: list[str] | None = None,
    lint_cmd: list[str] | None = None,
) -> tuple[bool, str]:
    """Deterministic, orchestrator-side. Returns (ok, reason). Never fixes anything. A pass
    writes the step's result file for the record; every other outcome leaves none. Raises
    DefinitionError when the pass cannot read the implementer definition it records, and
    ProveRedApparatusError when every check passes but prove-red's base failed only on modules
    that do not resolve, one of them not the step's."""
    state = load_state(root)
    wt = Path(state["worktree"])
    base = state["base_head"]
    result_path = run_result_path(state, n)
    result_path.unlink(missing_ok=True)

    reason = check_head_unchanged(wt, base)
    if reason:
        return False, reason

    reason = check_plan_unchanged(state, n)
    if reason:
        return False, reason

    reason = check_record_unchanged(state, n)
    if reason:
        return False, reason

    reason = check_holdout_unchanged(state)
    if reason:
        return False, reason

    # a Test-repo step's tests live in another repository the implementer cannot reach, so
    # the step is refused before anything is staged or run (test-repo exemption spec, R6)
    plan = _worktree_plan(state)
    step = next((s for s in plan.steps if s.number == n), None)
    if step is not None and step.test_repo is not None:
        return False, (
            "a Test-repo step runs attended; the implementer cannot build in another repository"
        )

    stage_all(wt)
    staged = staged_files(wt)
    if not staged:
        return False, "nothing staged: the step produced no changes"

    untracked_plan = None if state.get("plan_tracked", True) else state["plan"]
    reason = check_staged_safe(wt, staged, untracked_plan=untracked_plan)
    if reason:
        return False, reason

    brief_path = Path(state["run_dir"]) / f"brief-{n}.md"
    brief_text = brief_path.read_text(encoding="utf-8") if brief_path.exists() else ""
    reason = check_gate_config(staged, brief_text)
    if reason:
        return False, reason

    # the plan's own cap (Step-size:, or the default) and the step's estimate (Size:); the
    # staged diff is measured once, for the cap and for the result file
    estimate = step.size if step else None
    changed = _diff_lines(wt, "--cached", base)
    reason = check_step_size(
        wt, base, next_plan.step_size_limit(plan), estimate=estimate, changed=changed
    )
    if reason:
        return False, reason

    plan_test, plan_lint = plan_commands(wt, state["plan"])
    test_cmd = test_cmd or plan_test
    lint_cmd = lint_cmd or plan_lint

    patterns = next_plan.plan_test_globs(plan)
    apparatus = None
    proof: dict = {}
    red_reason = None
    if state.get("fix_round") and comment_only_round(wt, base, staged):
        # a fix round of comments and documentation has no behaviour to prove red; Test, the
        # counts and Lint still run
        red_reason, reason = COMMENT_ONLY_ROUND, None
    else:
        try:
            reason = prove_red(
                root, wt, base, staged, test_cmd, patterns=patterns,
                guard=step.guard_mutation if step else None, proof=proof,
            )  # fmt: skip
        except ProveRedApparatusError as exc:
            # raised only once every check after it passes: a red the step can fix is its own
            apparatus, reason = exc, None
    if reason:
        return False, reason

    rc, test_out = run_check(wt, test_cmd)
    if rc != 0:
        return False, f"Test is red after the step:\n{test_out[-2000:]}"

    floor = count_floor(
        root, wt, base, state.get("base_counts"), step.deletes if step else None, test_cmd,
        patterns=patterns,
    )  # fmt: skip
    reason = check_counts(floor, parse_counts(test_out))
    if reason:
        return False, reason

    rc, out = run_check(wt, lint_cmd)
    if rc != 0:
        return False, f"Lint failed:\n{out[-2000:]}"

    if apparatus is not None:
        raise apparatus

    tree = staged_tree(wt, [state["plan"]])
    write_result(
        result_path, n, "auto", base, test_out, "ok", tree=tree,
        size_estimate=estimate, size_changed=changed,
        guard_mutation=proof.get("guard_mutation"),
        red="n/a" if red_reason else None, red_reason=red_reason,
    )  # fmt: skip
    return True, "gate passed"


# ---- review package ---------------------------------------------------------


def _render_review(title: str, tree: Path, base: str, extra: list[str]) -> str:
    parts = [
        f"# {title}",
        "",
        "## Commits",
        "",
        git(tree, "log", "--oneline", f"{base}..HEAD").stdout or "(none)",
        "",
        "## Stat",
        "",
        git(tree, "diff", "--stat", base, "HEAD").stdout or "(none)",
        "",
        "## Diff",
        "",
        "```diff",
        git(tree, "diff", "-U10", base, "HEAD").stdout,
        "```",
        "",
        *extra,
    ]
    return "\n".join(parts)


def _chunk_steps_section(root: Path, n: int, plan: str = "prompt_plan.md") -> list[str]:
    """The chunk's step headers and statuses, so the reviewer sees the whole boundary."""
    parsed = next_plan.parse_plan((root / plan).read_text(encoding="utf-8"))
    me = next((s for s in parsed.steps if s.number == n), None)
    members = [s for s in parsed.steps if me is not None and s.chunk == me.chunk]
    lines = ["## Steps in this chunk", ""]
    for s in members:
        note = f" ({s.note})" if s.note else ""
        lines += [f"### Step {s.number}: {s.title}", f"Status: {s.state}{note}", ""]
    return lines


def review_package(root: Path, n: int, base: str | None = None) -> Path:
    """Render the reviewer's package. With a run it covers the run's step from the run's
    base; with `base` it is the attended chunk boundary, run-less: the diff from `base`
    to HEAD at the root plus the chunk's steps, written under .next-auto/attended/."""
    if base is not None:
        d = attended_dir(root)
        d.mkdir(parents=True, exist_ok=True)
        title = f"Review package: chunk ending at step {n} (attended, base {base[:8]})"
        text = _render_review(title, root, base, _chunk_steps_section(root, n))
        path = d / f"review-{n}.md"
        path.write_text(text, encoding="utf-8")
        return path
    state = load_state(root)
    wt = Path(state["worktree"])
    title = f"Review package: step {n} ({state['run_id']})"
    text = _render_review(title, wt, state["base_head"], [])
    path = Path(state["run_dir"]) / f"review-{n}.md"
    path.write_text(text, encoding="utf-8")
    return path


# ---- commit / revert --------------------------------------------------------


def _hook_modified_files(worktree: Path) -> bool:
    """True when the working tree differs from the index: a pre-commit formatter rewrote
    a tracked file (second status column) or wrote a new one (`??`). A hook that merely
    refused leaves the index staged and the tree untouched, which this reads as False."""
    for ln in git(worktree, "status", "--porcelain").stdout.splitlines():
        if ln.startswith("??") or (len(ln) > 1 and ln[1] != " "):
            return True
    return False


# a title that opens with a lowercase conventional type, as `feat(step-9): ` or `fix: `; the
# commit adds the step's own prefix, so such a title would carry two (3.4.8's c9dd833)
CONVENTIONAL_PREFIX_RE = re.compile(r"^[a-z]+(\([^)]*\))?!?: ")


class TitleRefusedError(ValueError):
    """A commit title that already carries a conventional prefix; nothing is committed."""


def _refuse_prefixed(title: str) -> None:
    prefix = CONVENTIONAL_PREFIX_RE.match(title)
    if prefix:
        raise TitleRefusedError(
            f'the title already carries a conventional prefix ("{prefix.group(0)}"); '
            "pass the subject only"
        )


def _commit_index(worktree: Path, message: str, what: str) -> str:
    """One `git commit` of the index with hooks on, the call every engine commit goes through.
    A pre-commit formatter may have modified files; re-add and retry once (M2). A hook that
    refused without modifying anything gets no second run."""
    r = git(worktree, "commit", "-m", message, check=False)
    if r.returncode != 0 and _hook_modified_files(worktree):
        git(worktree, "add", "-A")
        r = git(worktree, "commit", "-m", message, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"commit failed for {what}: {r.stdout}\n{r.stderr}")
    return git(worktree, "rev-parse", "HEAD").stdout.strip()


def commit_step(worktree: Path, n: int, title: str) -> str:
    _refuse_prefixed(title)
    return _commit_index(worktree, f"feat(step-{n}): {title}", f"step {n}")


def commit_attended(
    root: Path, n: int, title: str = "", *, plan: str = "prompt_plan.md"
) -> tuple[int, str]:
    """The attended loop's commit, run-less: stage the tree, run the staged-safe scan over
    what is staged (the plan note written after `check N` restored the index reaches git
    only here), commit at the root with the step's conventional message, and remove the
    step's check record and brief. Writes no state.json, so the guard stays unarmed.
    With a result file from `check N` it adds the commit and the mutation value; with
    Record in the plan it requires that file before committing and appends step.done
    after (R11, R12), refusing a result file a commit already used or one whose tree is not
    the tree it would commit, the plan aside. Returns (exit code, one line):
    0 committed, 1 refused or the commit failed (a hook that would not pass), 2 apparatus
    (git cannot run) or the append failed after the commit, which stands."""
    result_path = attended_result_path(root, n)
    try:
        # the same excludes preflight writes: a clone that never ran `check begin` here
        # would otherwise commit the record and brief and then unlink them, leaving
        # staged deletions; an untracked plan (project-a, project-b) is kept out the same way
        ensure_ignored(root, RUN_SUBDIR + "/")
        plan_tracked = plan_is_tracked(root, plan)
        if not plan_tracked:
            ensure_ignored(root, plan)
        # the plan as it stands now, with the step's done note on it
        plan_text = (root / plan).read_text(encoding="utf-8")
        parsed = next_plan.parse_plan(plan_text)
        record = plan_record(parsed)
        if record is not None:
            reason = _result_unusable(result_path, n, "check")
            if reason:
                return 1, f"COMMIT REFUSED step {n}: {reason}"
        stage_all(root)
        staged = staged_files(root)
        if not staged:
            return 1, f"COMMIT REFUSED step {n}: nothing to commit"
        reason = check_staged_safe(
            root,
            staged,
            untracked_plan=None if plan_tracked else plan,
            toolchain=next_plan.plan_toolchain_globs(parsed),
        )
        if reason:
            return 1, f"COMMIT REFUSED step {n}: {reason}"
        if record is not None:
            reason = _tree_moved(root, result_path, [plan], n, "check")
            if reason:
                return 1, f"COMMIT REFUSED step {n}: {reason}"
        if not title:
            step = next((s for s in parsed.steps if s.number == n), None)
            title = step.title if step else f"step {n}"
        try:
            sha = commit_step(root, n, title)
        except TitleRefusedError as exc:
            return 1, f"COMMIT REFUSED step {n}: {exc}"
        except RuntimeError as exc:
            return 1, f"COMMIT FAIL step {n}: {' '.join(str(exc).split())}"
    except (OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        return 2, f"COMMIT APPARATUS step {n}: {exc}"
    for stale in (
        attended_record_path(root, n), attended_dir(root) / f"brief-{n}.md", check_log_path(root, n)
    ):
        stale.unlink(missing_ok=True)
    failed = record_step(root, root, parsed, n, sha, result_path, record, plan_text=plan_text)
    if failed:
        return 2, f"COMMIT {sha} RECORD FAILED step {n}: {failed}"
    return 0, f"COMMIT {sha}"


BOUNDARY_SUBJECT = "record the review findings"


def _boundary_subject(parsed: next_plan.Plan) -> str:
    """The default subject of the boundary commit: the chunk of the last DONE step, so the
    commit names the chunk the review covered; the fixed subject for a plan with no chunks."""
    done = [s for s in parsed.steps if s.state == "DONE" and s.chunk]
    if not done:
        return BOUNDARY_SUBJECT
    label = next_plan.chunk_label(done[-1].chunk)
    return f"record the Chunk {label} review findings"


def boundary_commit(
    root: Path, title: str = "", *, plan: str = "prompt_plan.md"
) -> tuple[int, str]:
    """`boundary-commit` (sweep spec R3), run-less: commit the plan alone at the chunk boundary,
    after the Findings append, as `docs(plan): <subject>`, with the record beside it under
    Record. Nothing else is staged: the plan and the record are the boundary's own changes.
    A plan untracked by design is skipped before the tree is read (3.5.10 spec R3).
    Returns (exit code, one line): 0 committed or skipped, 1 refused or the commit failed,
    2 apparatus."""
    try:
        if load_state(root) is not None:
            return 1, "COMMIT REFUSED boundary: a run is live"
        try:
            _refuse_prefixed(title)
        except TitleRefusedError as exc:
            return 1, f"COMMIT REFUSED boundary: {exc}"
        if not plan_is_tracked(root, plan):
            # project-a and project-b keep the plan out of git by design (info/exclude), so it never
            # shows in the status read below: nothing to commit, and nothing to refuse
            return 0, (
                "COMMIT SKIPPED boundary: the plan is untracked by design; "
                "the Findings stay in the root plan"
            )
        parsed = next_plan.parse_plan((root / plan).read_text(encoding="utf-8"))
        record = plan_record(parsed)
        allowed = [plan] + ([record_path(record[1])] if record else [])
        status = [(ln[:2], ln[3:]) for ln in git(root, "status", "--porcelain").stdout.splitlines()]
        changed = [path for _, path in status]
        if plan not in changed:
            return 1, "COMMIT REFUSED boundary: the plan has no change"
        # the first path that is not the boundary's own, by its index state: a step's work
        # beside the plan would otherwise ride in a docs(plan) commit
        for xy, path in status:
            if path in allowed:
                continue
            how = "untracked" if xy == "??" else "modified" if xy[0] == " " else "staged"
            return 1, (
                f"COMMIT REFUSED boundary: {path} is {how}; "
                "only the plan and the record may change"
            )
        git(root, "add", "--", *[p for p in allowed if p in changed])
        reason = check_staged_safe(
            root,
            staged_files(root),
            untracked_plan=None,
            toolchain=next_plan.plan_toolchain_globs(parsed),
        )
        if reason:
            return 1, f"COMMIT REFUSED boundary: {reason}"
        message = f"docs(plan): {title or _boundary_subject(parsed)}"
        try:
            sha = _commit_index(root, message, "boundary")
        except RuntimeError as exc:
            return 1, f"COMMIT FAIL boundary: {' '.join(str(exc).split())}"
    except (OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        return 2, f"COMMIT APPARATUS boundary: {exc}"
    return 0, f"COMMIT {sha}"


def commit_run(root: Path, n: int, title: str = "") -> tuple[int, str]:
    """A run's commit K in the worktree, on the index the gate staged; the step's state goes
    into state.json. The result file and Record rules are the attended commit's: with Record
    the gate's result file is required first, and step.done is appended after (R11, R12).
    Returns (exit code, one line): 0 committed, 1 refused, 2 the append failed after the
    commit, which stands."""
    state = load_state(root)
    wt = Path(state["worktree"])
    plan_text = (wt / state["plan"]).read_text(encoding="utf-8")
    parsed = next_plan.parse_plan(plan_text)
    record = plan_record(parsed)
    result_path = run_result_path(state, n)
    if record is not None:
        try:
            reason = _result_unusable(result_path, n, "gate") or _tree_moved(
                wt, result_path, [state["plan"]], n, "gate"
            )
        except (OSError, json.JSONDecodeError) as exc:
            return 2, f"COMMIT APPARATUS step {n}: {exc}"
        if reason:
            return 1, f"COMMIT REFUSED step {n}: {reason}"
    if not title:
        step = next((s for s in parsed.steps if s.number == n), None)
        title = step.title if step else f"step {n}"
    try:
        sha = commit_step(wt, n, title)
    except TitleRefusedError as exc:
        return 1, f"COMMIT REFUSED step {n}: {exc}"
    state.setdefault("steps", {})[str(n)] = {"state": "DONE", "note": f"commit {sha[:8]}"}
    save_state(state)
    failed = record_step(
        root, wt, parsed, n, sha, result_path, record, plan_text=plan_text, default=state["default"]
    )
    if failed:
        return 2, f"COMMIT {sha} RECORD FAILED step {n}: {failed}"
    return 0, f"COMMIT {sha}"


def _result_unusable(result_path: Path, n: int, check: str) -> str | None:
    """Why a result file cannot back a commit with Record: there is none, or a commit
    already used it (ai-dlc-record R11)."""
    if not result_path.is_file():
        return f"no result file for step {n}; run {check} {n} first"
    used = json.loads(result_path.read_text(encoding="utf-8")).get("sha")
    if used:
        return (
            f"the result file for step {n} was already used by commit {used[:8]}; "
            f"run {check} {n} again"
        )
    return None


def _tree_moved(tree: Path, result_path: Path, own: list[str], n: int, check: str) -> str | None:
    """Why the index is not the tree the result file vouches for, the plan aside, since the
    loop sets the step done between the pass and the commit."""
    vouched = json.loads(result_path.read_text(encoding="utf-8")).get("tree")
    if staged_tree(tree, own) != vouched:
        return f"the tree changed since {check} {n} passed; run {check} {n} again"
    return None


def _record_tree(root: Path, plan: str) -> tuple[Path, tuple[list[str], str] | None, str]:
    """The tree holding the record (the run's worktree, else the root), the plan's Record,
    and the plan's name, for the record subcommands that work in both modes."""
    state = load_state(root)
    tree, name = (Path(state["worktree"]), state["plan"]) if state else (root, plan)
    return tree, plan_record(next_plan.parse_plan((tree / name).read_text(encoding="utf-8"))), name


def record_commit(root: Path, *, plan: str = "prompt_plan.md") -> tuple[int, str]:
    """`record-commit` (ai-dlc-record R12): commit docs/records/<slug>.md alone, in the run's
    worktree or at the root, after the last boundary line and after the done check's line.
    Exit 0 committed or unchanged, 1 refused or the commit failed, 2 apparatus."""
    try:
        tree, record, name = _record_tree(root, plan)
        if record is None:
            return 1, f"RECORD REFUSED: {name} carries no Record"
        sha = commit_record(tree, record[1])
    except RuntimeError as exc:
        return 1, f"RECORD FAILED: {exc}"
    except (OSError, subprocess.CalledProcessError) as exc:
        return 2, f"RECORD APPARATUS: {exc}"
    if sha is None:
        return 0, f"RECORD UNCHANGED {record_path(record[1])}"
    return 0, f"RECORD COMMIT {sha}"


def pr_opened_attended(root: Path, pr: int, *, plan: str = "prompt_plan.md") -> tuple[int, str]:
    """`pr-opened <n>`, the attended session's pull-request step (ai-dlc-record R12), run-less:
    after `gh pr create`, read the PR's head and branch from GitHub, and when the head is HEAD
    append pr.opened with it, commit the record alone, and push HEAD to that branch. Exit 0
    recorded, 1 refused, 2 when gh cannot answer, a record step failed, or git cannot run;
    the PR stands."""
    parsed = next_plan.parse_plan((root / plan).read_text(encoding="utf-8"))
    record = plan_record(parsed)
    if record is None:
        return 1, f"PR-OPENED REFUSED PR #{pr}: {plan} carries no Record"
    view = gh(["pr", "view", str(pr), "--json", "headRefOid,headRefName"], root=root)
    if view.returncode != 0:
        return 2, f"PR-OPENED APPARATUS PR #{pr}: {_gh_detail(view)}"
    try:
        answer = json.loads(view.stdout)
        pr_head, branch = str(answer["headRefOid"]), str(answer["headRefName"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return 2, f"PR-OPENED APPARATUS PR #{pr}: gh pr view returned no headRefOid or headRefName"
    try:
        head = git(root, "rev-parse", "HEAD").stdout.strip()
        if pr_head != head:
            return 1, f"PR-OPENED REFUSED PR #{pr}: PR head {pr_head} is not local HEAD {head}"
        sha, failure = record_pr_opened(root, root, record, pr, head, branch, "attended")
    except (OSError, subprocess.CalledProcessError) as exc:
        return 2, f"PR-OPENED APPARATUS PR #{pr}: {exc}"
    if failure:
        return 2, f"PR-OPENED FAILED PR #{pr}: {failure}"
    return 0, f"PR-OPENED PR #{pr} head {head} record {sha}"


GITHUB_LOGIN = re.compile(r"[A-Za-z0-9-]+")


def plan_approved(root: Path, by: str, *, plan: str = "prompt_plan.md") -> tuple[int, str]:
    """`plan-approved --by <login>` (ai-dlc-record R13), run-less: append plan.approved for the
    plan through the appender committed on the default branch (R12), so an approval never runs
    the tree's copy, and print the hash it approved. `by` is the login of the person who
    approved, the one fact a person supplies. Exit 0 appended, 1 refused, 2 when the record
    is missing, the append failed, or git cannot run; nothing is committed, since the line
    rides in the next commit that touches the record (R2)."""
    text = (root / plan).read_bytes().decode("utf-8")
    record = plan_record(next_plan.parse_plan(text))
    if record is None:
        return 1, f"PLAN-APPROVED REFUSED: {plan} carries no Record"
    if not GITHUB_LOGIN.fullmatch(by):
        return 1, f"PLAN-APPROVED REFUSED: --by {by!r} is not a GitHub login"
    command, slug = record
    path = root / record_path(slug)
    if not path.is_file():
        init = shlex.join([*command, "init", slug])
        return 2, f"PLAN-APPROVED APPARATUS: no record at {record_path(slug)}; run {init} first"
    before = path.read_bytes()
    try:
        r = run_record(root, root, record, "append", "plan.approved", "--plan", plan, "--by", by)
    except (OSError, subprocess.CalledProcessError) as exc:
        return 2, f"PLAN-APPROVED APPARATUS: {exc}"
    if r.returncode != 0:
        return 2, f"PLAN-APPROVED FAILED: plan.approved append {_record_detail(r)}"
    if path.read_bytes() == before:
        return 2, (
            "PLAN-APPROVED FAILED: plan.approved append exited 0 but the record did not change"
        )
    return 0, f"PLAN-APPROVED {next_plan.plan_hash(text)} by {by}"


def revert_step(root: Path, worktree: Path, base_head: str, patch_path: Path) -> None:
    """Save the attempt as a patch and reset the worktree to the step's base, then put
    the plan back exactly as the step began. The reset alone drops every uncommitted
    plan edit (the previous step's DONE note, this step's IN PROGRESS), and the plan is
    the orchestrator's to keep, not part of the attempt being discarded. The record, when
    the plan carries one, goes back the same way: its pending lines are the orchestrator's,
    and a line naming the dropped commit must not survive it. With a fix round open the
    snapshots are the step's origin and `base_head` is its first base, so the whole step
    goes, both commits included, and the run stands as after its first step-begin."""
    state = load_state(root)
    n = state["current_step"]
    origin = state.get("step_origin")
    source = origin or state
    plan_snap = plan_origin_path(state, n) if origin else snapshot_path(state, n)
    record_snap = record_origin_path(state, n) if origin else record_snapshot_path(state, n)
    snapshot, reason = _verified_file(plan_snap, source.get("plan_snapshot_sha256"), "plan", n)
    if reason:
        raise RuntimeError(f"cannot revert: {reason}")
    record_bytes = None
    if recorded := source.get("record_snapshot_sha256"):
        record_bytes, reason = _verified_file(record_snap, recorded, "record", n)
        if reason:
            raise RuntimeError(f"cannot revert: {reason}")
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    stage_all(worktree)
    diff = git(worktree, "diff", "--cached", base_head).stdout
    patch_path.write_text(diff, encoding="utf-8")
    git(worktree, "reset", "--hard", base_head)
    git(worktree, "clean", "-fd")
    (worktree / state["plan"]).write_bytes(snapshot)
    record = plan_record(next_plan.parse_plan(snapshot.decode("utf-8")))
    if record is not None and record_bytes is not None:
        (worktree / record_path(record[1])).write_bytes(record_bytes)
    # the attempt's result file names what was just dropped
    run_result_path(state, n).unlink(missing_ok=True)
    if origin:
        _reopen_from_origin(state, n)
        save_state(state)


# ---- merge ------------------------------------------------------------------


def verdict_path(root: Path, sha: str) -> Path:
    return attended_dir(root) / f"verdict-{sha}.json"


def done_path(root: Path, sha: str) -> Path:
    return attended_dir(root) / f"done-{sha}.json"


def read_verdict(root: Path, sha: str) -> dict | None:
    """The chunk reviewer's verdict record for a commit, or None when there is none to read."""
    return _read_record_file(verdict_path(root, sha))


def read_done(root: Path, sha: str) -> dict | None:
    """The done check's record for a commit, or None when there is none to read."""
    return _read_record_file(done_path(root, sha))


def _read_record_file(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def reviewed_chain(root: Path, sha: str) -> list[str]:
    """The commits a PR head's verdict may be keyed to (ai-dlc-record R12): the head, then each
    first parent below it while the commit above changes only record files, down to and
    including the commit where that stops. A record file is a regular file (mode 100644 on
    both sides, or added or deleted as one) directly under docs/records/ ending in .md, so a
    symlink, a gitlink, or an executable of that name stops the walk; so do a root commit, a
    commit that changes nothing, and any other path, with renames split into a delete and an
    add. Decided from git alone, whatever the plan says."""
    chain = [sha]
    while True:
        current = chain[-1]
        parent = git(root, "rev-parse", "--verify", "--quiet", f"{current}^1", check=False)
        if parent.returncode != 0:
            return chain
        first = parent.stdout.strip()
        raw = git(root, "diff", "--raw", "--no-renames", "-z", first, current, check=False)
        fields = raw.stdout.split("\0")
        changes = list(zip(fields[0::2], fields[1::2], strict=False))
        if raw.returncode != 0 or not changes:
            return chain
        if not all(_is_record_change(meta, path) for meta, path in changes):
            return chain
        chain.append(first)


def _is_record_change(meta: str, path: str) -> bool:
    old_mode, new_mode = meta.lstrip(":").split()[:2]
    return {old_mode, new_mode} - {"000000"} == {"100644"} and _is_record_file(path)


def _verdict_problem(record: dict, sha: str) -> str | None:
    if record.get("head") != sha:
        return f"verdict record head {record.get('head')} is not {sha}"
    if record.get("verdict") != "Approved":
        return f"verdict is {record.get('verdict')!r}, not Approved"
    if record.get("reviewer") != VERDICT_REVIEWER:
        return f"verdict from {record.get('reviewer')!r}, not {VERDICT_REVIEWER}"
    return None


def _done_problem(record: dict, sha: str) -> str | None:
    if record.get("head") != sha:
        return f"done record head {record.get('head')} is not {sha}"
    if record.get("verdict") != "APPROVED":
        return f"done check is {record.get('verdict')!r}, not APPROVED"
    if record.get("reviewer") != DONE_REVIEWER:
        return f"done check from {record.get('reviewer')!r}, not {DONE_REVIEWER}"
    return None


def _chain_verdict_reason(root: Path, chain: list[str]) -> str | None:
    """A done check that REJECTED the claim anywhere on the chain refuses the merge, whatever
    else approves. Otherwise None when a commit on the chain has the chunk reviewer's Approved
    verdict-<sha>.json or the done check's APPROVED done-<sha>.json; else the first problem,
    looking from the commit where the walk stopped up to the PR head, or the missing records
    named with the chain's range."""
    for sha in reversed(chain):
        done = read_done(root, sha)
        if done is not None and done.get("verdict") == "REJECTED":
            return f"done check REJECTED at {sha[:8]}"
    problems = []
    for sha in reversed(chain):
        for record, problem_of in ((read_verdict(root, sha), _verdict_problem),
                                   (read_done(root, sha), _done_problem)):  # fmt: skip
            if record is None:
                continue
            problem = problem_of(record, sha)
            if problem is None:
                return None
            problems.append(problem)
    if problems:
        return problems[0]
    return (
        f"no readable verdict-<sha>.json or done-<sha>.json under "
        f"{attended_dir(root).relative_to(root)}/ for {chain[0][:8]}..{chain[-1][:8]}"
    )


def _is_record_file(path: str) -> bool:
    posix = PurePosixPath(path)
    return posix.parent == PurePosixPath(RECORD_DIR) and posix.suffix == ".md"


def preflight_noted(root: Path, sha: str) -> bool:
    """True when the preflight plugin's note is on `sha` (its pre-push hook reads the same)."""
    return git(root, "notes", "--ref=preflight", "show", sha, check=False).returncode == 0


def preflight_note_required() -> bool:
    """True when ADW_PREFLIGHT_NOTE is exactly `required`: the private estate's setting, where the
    preflight plugin writes the note; a public install has no writer and is not asked for one."""
    return os.environ.get(PREFLIGHT_NOTE_VAR) == "required"


def _gh_detail(r: subprocess.CompletedProcess) -> str:
    return r.stderr.strip() or r.stdout.strip() or f"gh exited {r.returncode}"


def on_security_surface(path: str) -> bool:
    """True for a changed path the merge gate never merges on its own."""
    norm = PurePosixPath(path.replace("\\", "/"))
    parts = norm.parts
    if any(part in SECURITY_SURFACE_DIRS for part in parts[:-1]):
        return True
    if str(norm).startswith(SECURITY_SURFACE_PATH_PREFIXES):
        return True
    return any(fnmatch.fnmatchcase(norm.name, glob) for glob in SECURITY_SURFACE_GLOBS)


def security_surface_hunks(diff: str) -> list[tuple[str, str]]:
    """(file, symbol) for every added or removed diff line that names a guard symbol, and
    for a `timeout=` line under hooks/ or scripts/; each pair once, in diff order."""
    hits: list[tuple[str, str]] = []
    current = ""
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            current = line.split(" b/", 1)[1] if " b/" in line else ""
            continue
        if line.startswith(("+++ ", "--- ")) or not line.startswith(("+", "-")):
            continue
        body = line[1:]
        for symbol in SECURITY_SURFACE_SYMBOLS:
            if symbol in body and (current, symbol) not in hits:
                hits.append((current, symbol))
        if (
            "timeout=" in body
            and current.startswith(SECURITY_SURFACE_TIMER_DIRS)
            and (current, "timeout=") not in hits
        ):
            hits.append((current, "timeout="))
    return hits


def merge_pr(
    root: Path,
    n: int,
    *,
    repo: str | None = None,
    test_cmd: list[str] | None = None,
    lint_cmd: list[str] | None = None,
    plan: str = "prompt_plan.md",
) -> tuple[int, list[str]]:
    """The merge gate and the merge, run-less (Requirement 19): every condition is
    evaluated and every failed one is its own MERGE REFUSED line, so the operator reads
    the whole state at once. When all hold it runs `gh pr merge --squash --delete-branch`
    (never --auto, never --admin) and prints the merge sha. Exit 0 merged, 1 refused or
    the merge call failed, 2 apparatus (gh cannot answer). Writes no state.json."""
    view_args = ["repo", "view", *([repo] if repo else []), "--json", "owner,nameWithOwner"]
    view = gh(view_args, root=root)
    if view.returncode != 0:
        return 2, [f"MERGE APPARATUS PR #{n}: {_gh_detail(view)}"]
    meta = json.loads(view.stdout)
    owner = (meta.get("owner") or {}).get("login")
    slug = repo or meta.get("nameWithOwner")
    reasons: list[str] = []
    owner_why = owner_reason(owner, noun="repo owner")
    if owner_why:
        reasons.append(owner_why)
    pv = gh(["pr", "view", str(n), "--repo", slug, "--json", PR_VIEW_FIELDS], root=root)
    if pv.returncode != 0:
        return 2, [f"MERGE APPARATUS PR #{n}: {_gh_detail(pv)}"]
    pr = json.loads(pv.stdout)
    sha = str(pr.get("headRefOid") or "")
    base = str(pr.get("baseRefName") or "")
    # the head usually lives only on the remote; one fetch makes it a local commit, and a
    # fetch that fails leaves the local-commit check below to say so
    git(root, "fetch", "origin", sha, check=False)
    # the verdict is for the head or any commit below it on its record-only chain
    chain = [sha]
    if git(root, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode != 0:
        reasons.append(f"head {sha} is not a local commit")
    else:
        chain = reviewed_chain(root, sha)
        if test_cmd is None or lint_cmd is None:
            plan_test, plan_lint = plan_commands(root, plan)
            test_cmd, lint_cmd = test_cmd or plan_test, lint_cmd or plan_lint
        with scratch_worktree(root, sha) as wt:
            rc, out = run_check(wt, test_cmd)
            if rc != 0:
                reasons.append(f"Test red at {sha}: {_last_line(out)}")
            rc, out = run_check(wt, lint_cmd)
            if rc != 0:
                reasons.append(f"Lint red at {sha}: {_last_line(out)}")
        diff = git(root, "diff", f"origin/{base}...{sha}", check=False)
        if diff.returncode != 0:
            reasons.append(f"could not diff origin/{base}...{sha}: {_last_line(diff.stderr)}")
        else:
            for path, symbol in security_surface_hunks(diff.stdout):
                reasons.append(f"security surface hunk: {path} {symbol}")
    for entry in pr.get("files") or []:
        path = str((entry or {}).get("path") or "")
        if path and on_security_surface(path):
            reasons.append(f"security surface: {path}")
    reason = _chain_verdict_reason(root, chain)
    if reason:
        reasons.append(reason)
    if preflight_note_required() and not preflight_noted(root, sha):
        reasons.append(f"no preflight note on {sha}")
    size = int(pr.get("additions") or 0) + int(pr.get("deletions") or 0)
    if size > RUN_SIZE_LIMIT:
        reasons.append(f"PR diff {size} lines, over {RUN_SIZE_LIMIT}")
    if pr.get("reviewDecision") == "REVIEW_REQUIRED" or pr.get("mergeStateStatus") == "BLOCKED":
        reasons.append(f"ruleset requires an approval; the owner merges PR #{n} by number")
    if pr.get("isDraft"):
        reasons.append("PR is a draft")
    if reasons:
        return 1, [f"MERGE REFUSED PR #{n}: {r}" for r in reasons]
    merged = gh(["pr", "merge", str(n), "--repo", slug, "--squash", "--delete-branch"], root=root)
    if merged.returncode != 0:
        return 1, [f"MERGE FAILED gh: {_gh_detail(merged)}"]
    after = gh(["pr", "view", str(n), "--repo", slug, "--json", "mergeCommit"], root=root)
    oid = None
    if after.returncode == 0:
        try:
            oid = (json.loads(after.stdout).get("mergeCommit") or {}).get("oid")
        except json.JSONDecodeError:
            oid = None
    return 0, [f"MERGED PR #{n} {oid or f'(merge sha unavailable: {_gh_detail(after)})'}"]


# ---- wrap-up ----------------------------------------------------------------


def _worktree_plan(state: dict) -> next_plan.Plan:
    text = (Path(state["worktree"]) / state["plan"]).read_text(encoding="utf-8")
    return next_plan.parse_plan(text)


def derive_reason(plan: next_plan.Plan) -> str:
    """The stop reason, read from the plan's step states (the one source of truth). A
    BLOCKED step is the reason only when the run reached it: a plan that pre-marks a
    later step BLOCKED (a human-only deploy) and stopped at an earlier PENDING step
    stopped there."""
    for step in plan.steps:
        if step.state in ("PENDING", "IN PROGRESS"):
            break
        if step.state == "BLOCKED":
            kind = "apparatus" if step.note.startswith("apparatus") else "decision"
            return f"BLOCKED ({kind}) at step {step.number}"
    for step in plan.steps:
        if step.state == "FAILED":
            return f"FAILED step {step.number}"
    done = [s for s in plan.steps if s.state == "DONE"]
    if done and len(done) == len(plan.steps):
        return f"completed {len(done)} step{'s' if len(done) != 1 else ''}"
    if done:
        return f"stopped after {len(done)} of {len(plan.steps)} steps"
    return "stopped before any step completed"


# the plan header's Spec: and Intent: fields up to their first `. `, and a record's approval line
_SPEC_RE = re.compile(r"^Spec: (.*?)(?:\. |$)", re.M)
_INTENT_RE = re.compile(r"^Intent: (.*?)(?:\. |$)", re.M)
_APPROVED_RE = re.compile(r"\bplan\.approved plan=(\S+) by=(\S+)")


def body_lines(state: dict) -> list[str]:
    """The plain lines that end both bodies (R18, D32): `Spec:` from the plan header,
    `Closes:` the DONE steps' ids in plan order without repeats, `Intent:` from the plan
    header's first such line (sweep spec R6), and `Approved-by:` the `by` of the last
    plan.approved line in the worktree's record for the plan's hash. A line with no value is
    left out; a plan with Record whose record approves no line for its hash raises, so wrap-up
    stops before the push with the marker kept."""
    wt = Path(state["worktree"])
    text = (wt / state["plan"]).read_text(encoding="utf-8")
    plan = next_plan.parse_plan(text)
    lines = []
    header = text.split("\n## ", 1)[0]
    spec = _SPEC_RE.search(header)
    if spec and spec.group(1).strip():
        lines.append(f"Spec: {spec.group(1).strip()}")
    closes = dict.fromkeys(i for s in plan.steps if s.state == "DONE" for i in s.closes)
    if closes:
        lines.append(f"Closes: {', '.join(closes)}")
    intent = _INTENT_RE.search(header)
    if intent and intent.group(1).strip():
        lines.append(f"Intent: {intent.group(1).strip()}")
    record = plan_record(plan)
    if record is not None:
        want = next_plan.plan_hash(text)
        path = wt / record_path(record[1])
        held = path.read_text(encoding="utf-8") if path.is_file() else ""
        by = [m.group(2) for m in _APPROVED_RE.finditer(held) if m.group(1) == want]
        if not by:
            raise RuntimeError(f"wrap-up: no plan.approved line for {want}")
        lines.append(f"Approved-by: @{by[-1]}")
    return lines


def size_line(result: dict) -> str:
    """The sentence a step's result file gives the summary: the measured count against the
    plan's estimate, the pair the planner's size rule is re-fitted from (spec item 5)."""
    estimate = result.get("size_estimate")
    against = f"an estimate of {estimate}" if estimate is not None else "no estimate"
    return f"Size: {result['size_changed']} changed lines against {against}"


def _step_size_line(state: dict, n: int) -> str | None:
    """The size sentence for step n, or None without a result file that parses and carries
    the measured count (a step whose gate failed writes none)."""
    path = run_result_path(state, n)
    if not path.is_file():
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # the summary still renders; the step's size sentence is the only loss, and it is named
        print(f"summary: result file {path} unreadable, no size line: {exc}", file=sys.stderr)
        return None
    if not isinstance(result, dict) or not isinstance(result.get("size_changed"), int):
        return None
    return size_line(result)


def summary_sections(state: dict, reason: str | None = None) -> list[tuple[str, str]]:
    """The run summary as named sections. Step states come from the worktree plan;
    state.json only ever records commits. Step reports follow, only present when the
    implementer wrote any, and the body lines close it, so the secret scan reads them with
    the rest. A step with a result file carries its size sentence on its line and under its
    report header."""
    run_dir = Path(state["run_dir"])
    plan = _worktree_plan(state)
    reason = reason or derive_reason(plan)
    head = [
        f"# next-auto run {state['run_id']}",
        "",
        "## Run summary",
        "",
        f"- Branch: {state['branch']}",
        f"- Stop reason: {reason}",
    ]
    if not state.get("steps"):
        head.append("- No pull request: the run committed no step")
    steps = ["### Steps", ""]
    for step in plan.steps:
        note = f" ({step.note})" if step.note else ""
        size = _step_size_line(state, step.number)
        size = f"; {size}" if size else ""
        steps.append(f"- Step {step.number}: {step.title}: {step.state}{note}{size}")
    decisions = ["### Decisions", "", plan.decisions.strip() or "(none)"]
    findings = ["### Findings", "", plan.findings.strip() or "(none)"]
    sections = [
        ("Run summary", "\n".join(head)),
        ("Steps", "\n".join(steps)),
        ("Decisions", "\n".join(decisions)),
        ("Findings", "\n".join(findings)),
    ]
    reports = sorted(run_dir.glob("report-*.md"))
    if reports:
        lines = ["### Step reports", ""]
        for r in reports:
            lines.append(f"#### {r.stem}")
            number = r.stem.rsplit("-", 1)[-1]
            size = _step_size_line(state, int(number)) if number.isdigit() else None
            if size:
                lines.append(size)
            lines += ["", r.read_text(encoding="utf-8").strip(), ""]
        sections.append(("Step reports", "\n".join(lines).rstrip("\n")))
    tail = body_lines(state)
    if tail:
        sections.append(("Body lines", "\n".join(tail)))
    return sections


def pr_body_sections(state: dict, reason: str | None = None) -> list[tuple[str, str]]:
    """The sections a public PR may carry: no step reports, no absolute paths."""
    return [
        (name, _scrub_paths(text, state["root"]))
        for name, text in summary_sections(state, reason)
        if name != "Step reports"
    ]


def _render(sections: list[tuple[str, str]]) -> str:
    return "\n\n".join(text for _name, text in sections) + "\n"


def write_summary(state: dict, reason: str | None = None) -> Path:
    """summary.md (everything, run dir only) and pr-body.md (the public shape) in the run
    dir. Returns the summary path; the PR body sits beside it as PR_BODY_NAME."""
    run_dir = Path(state["run_dir"])
    path = run_dir / "summary.md"
    path.write_text(_render(summary_sections(state, reason)), encoding="utf-8")
    (run_dir / PR_BODY_NAME).write_text(_render(pr_body_sections(state, reason)), encoding="utf-8")
    return path


def _bring_plan_home(root: Path, state: dict, wt: Path) -> None:
    """An untracked plan rides no branch, so neither the merge nor the local run branch can
    carry its state back: the worktree copy (Status notes, Decisions, Findings) becomes the
    root copy, with the PR open or at the hand-back, so the next preflight reads the run's
    stop; the entries appended during the run carry their [run id] tag. A tracked plan's
    state is the chore(plan) commit on the run branch."""
    if not state.get("plan_tracked", True):
        (Path(root) / state["plan"]).write_bytes((wt / state["plan"]).read_bytes())


def wrap_up(root: Path, *, repo_slug: str | None = None, reason: str | None = None) -> dict:
    """Close a run: commit the plan, write the summary, push, open the hold-only PR,
    and only then remove the marker. Any failure before the PR exists leaves the marker
    in place so the guard hook stays armed. With Record in the plan, pending record lines
    are committed alone after the plan and before the push, and the PR is then recorded
    (pr.opened on the head it opened on, the record committed alone, a second push); a
    failure after the PR exists is returned under `record` and the run still ends, since a
    second wrap-up would try to open the PR again."""
    state = load_state(root)
    if not state:
        raise RuntimeError("no active run")
    wt = Path(state["worktree"])
    branch = state["branch"]
    plan = _worktree_plan(state)
    reason = reason or derive_reason(plan)
    tracked = state.get("plan_tracked", True)

    if tracked:
        git(wt, "add", state["plan"])
        if git(wt, "diff", "--cached", "--quiet", check=False).returncode != 0:
            git(wt, "commit", "-m", f"chore(plan): record run {state['run_id']} step states")
    record = plan_record(plan)
    if record is not None:
        # the record's pending lines ride the head the PR opens on, whatever the loop did
        commit_record(wt, record[1])
        # then the appender's sync, as committed on the default branch (ai-dlc-record D8),
        # its change committed alone; a failed sync stops before anything leaves the machine
        synced = run_record(root, wt, record, "sync", default=state["default"])
        if synced.returncode != 0:
            raise RuntimeError(
                f"wrap-up: record sync failed: {_last_line(synced.stderr or synced.stdout)}"
            )
        commit_record(wt, record[1], subject=f"chore(record): sync {record[1]}")

    summary = write_summary(state, reason)

    if not state.get("steps"):
        # the hand-back (carries spec R11): the run committed no step, so there is nothing to
        # push and no pull request to open; the summary and the local run branch keep what the
        # run decided, no gh call is made, and the marker goes so the guard hook stands down
        _bring_plan_home(root, state, wt)
        state["stop_reason"] = reason
        state["pr_url"] = ""
        save_state(state)
        marker = find_state_file(root)
        if marker is not None:
            marker.unlink()
        return {
            "reason": reason,
            "pr_number": None,
            "pr_url": "",
            "summary": str(summary),
            "record": None,
            "no_pr": True,
        }

    slug = repo_slug or origin_repo(root)
    if not slug:
        raise RuntimeError("could not derive owner/repo from the origin URL")
    # A private repo gets summary.md, reports included; a public one, or one whose
    # visibility gh cannot confirm, gets the terse body. Either body is scanned section
    # by section before anything leaves the machine, so a secret in a Finding stops the
    # push as well as the PR and the marker stays.
    private = repo_is_private(slug, wt)
    if private is True:
        body, sections = summary, summary_sections(state, reason)
    else:
        body, sections = summary.with_name(PR_BODY_NAME), pr_body_sections(state, reason)
    for name, text in sections:
        hits = scan_secrets(text)
        if hits:
            raise RuntimeError(f"secret-shaped content in {name} ({', '.join(hits)})")

    opened_on = git(wt, "rev-parse", "HEAD").stdout.strip()
    push = git(wt, "push", "-u", "origin", branch, check=False)
    if push.returncode != 0:
        raise RuntimeError(f"git push failed for {branch}: {push.stderr.strip()}")

    title = f"next-auto run {state['run_id']}: {reason}"
    pr = gh(
        ["pr", "create", "--repo", slug, "--head", branch, "--title", title,
         "--label", "next-auto", "--body-file", str(body)],
        root=wt,
    )
    if pr.returncode != 0:
        raise RuntimeError(f"gh pr create failed: {pr.stderr.strip() or pr.stdout.strip()}")
    pr_url = pr.stdout.strip().splitlines()[-1] if pr.stdout.strip() else ""
    m = re.search(r"/pull/(\d+)", pr_url)
    pr_number = int(m.group(1)) if m else None

    record_failure = None
    # R12 records pr.opened; the run committed a step, or it would have handed back above
    if record is not None:
        _sha, record_failure = record_pr_opened(
            root, wt, record, pr_number, opened_on, branch, "auto", default=state["default"]
        )

    _bring_plan_home(root, state, wt)

    state["stop_reason"] = reason
    state["pr_url"] = pr_url
    save_state(state)
    marker = find_state_file(root)
    if marker is not None:
        marker.unlink()
    return {
        "reason": reason,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "summary": str(summary),
        "record": record_failure,
        "no_pr": False,
    }


GOAL_LINE = (
    "/goal every step in prompt_plan.md is DONE, FAILED, or BLOCKED per /adw:next auto, "
    "or /adw:next auto printed RUN STOPPED, or stop after 60 turns"
)


# ---- CLI --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="next_auto.py")
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("preflight")
    p.add_argument("plan")

    s = sub.add_parser("start")
    s.add_argument("plan")
    s.add_argument("--run-id")
    s.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    s.add_argument("--no-sync", action="store_true")

    sub.add_parser("state")
    sub.add_parser("size")
    w = sub.add_parser("wrap-up")
    w.add_argument("--reason", default=None)
    w.add_argument("--repo", default=None, help="owner/repo; default from origin")

    st = sub.add_parser("set")
    st.add_argument("n", type=int)
    st.add_argument("state", choices=sorted(next_plan.CLI_STATES))
    st.add_argument("--note-file", default=None)

    apd = sub.add_parser("append")
    apd.add_argument("section", choices=["decisions", "findings"])
    apd.add_argument("--note-file", required=True)

    for name in ("step-begin", "gate", "prove-red", "commit", "review-package", "revert"):
        sp = sub.add_parser(name)
        sp.add_argument("n", type=int)
        if name == "commit":
            sp.add_argument("--title", default="")
        if name == "review-package":
            sp.add_argument("--base", default=None, help="attended: render from this sha, run-less")

    # attended: `check begin N` records the base, `check N` runs the gates; run-less by design
    ck = sub.add_parser("check")
    ck.add_argument("words", nargs="+", metavar="begin|N")

    # the merge gate over a PR by number; run-less, outside RUN_COMMANDS
    mg = sub.add_parser("merge")
    mg.add_argument("n", type=int)
    mg.add_argument("--repo", default=None, help="owner/repo; defaults to the origin's")

    # the delivery record's call points: `verdict` and `record-commit` act on the run when
    # one is live and at the root otherwise; `pr-opened` and `plan-approved` are the attended
    # session's
    vd = sub.add_parser("verdict")
    vd.add_argument("n", type=int)
    vd.add_argument("--head", required=True, help="the commit the review covered")
    vd.add_argument("--round", type=int, required=True)
    vd.add_argument("--verdict", required=True, choices=["Approved", "NeedsFixes"])
    vd.add_argument("--report", required=True, help="the reviewer's report as saved")
    sub.add_parser("record-commit")
    # the attended chunk boundary's commit of the plan alone; run-less, outside RUN_COMMANDS
    bc = sub.add_parser("boundary-commit")
    bc.add_argument("--title", default="", help="the subject after docs(plan):")
    po = sub.add_parser("pr-opened")
    po.add_argument("n", type=int)
    pa = sub.add_parser("plan-approved")
    pa.add_argument("--by", required=True, help="the login of the person who approved the plan")
    dv = sub.add_parser("done-verdict")
    dv.add_argument("--head", required=True, help="the commit the done check covered")
    dv.add_argument("--verdict", required=True, choices=["APPROVED", "REJECTED"])
    dv.add_argument("--model", required=True, choices=["opus", "sonnet", "haiku"])
    dv.add_argument("--report", required=True, help="the done check's report as saved")

    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    # Every subcommand past start reads state.json. Without a run there is nothing to
    # act on, and the operator gets that in one line, not a TypeError from a None state.
    # `review-package N --base SHA` is the attended chunk boundary and needs no run;
    # `commit N` with no run is the attended loop's one git operation per call, and
    # `set N <state>` with no run is its plan edit, `done` reading the step's check record
    run_less = (args.cmd == "review-package" and getattr(args, "base", None)) or (
        args.cmd in ("commit", "set") and load_state(root) is None
    )
    if args.cmd in RUN_COMMANDS and not run_less and load_state(root) is None:
        print(f"NO ACTIVE RUN: no state.json under {run_base(root)}", file=sys.stderr)
        return 1

    if args.cmd == "check":
        words = args.words
        if words[0] == "begin" and len(words) == 2 and words[1].isdigit():
            rc, lines = check_begin(root, int(words[1]))
            lines = [lines]
        elif len(words) == 1 and words[0].isdigit():
            rc, lines = check_step(root, int(words[0]))
        else:
            ap.error("check takes 'begin N' or 'N'")
        for line in lines:
            print(line, file=sys.stderr if rc else sys.stdout)
        return rc

    if args.cmd == "merge":
        rc, lines = merge_pr(root, args.n, repo=args.repo)
        for line in lines:
            print(line, file=sys.stderr if rc else sys.stdout)
        return rc

    if args.cmd == "boundary-commit":
        rc, line = boundary_commit(root, args.title)
        print(line, file=sys.stderr if rc else sys.stdout)
        return rc

    if args.cmd in ("verdict", "done-verdict", "record-commit", "pr-opened", "plan-approved"):
        if args.cmd == "verdict":
            report = Path(args.report)
            rc, line = record_verdict(root, args.n, args.head, args.round, args.verdict, report)
        elif args.cmd == "done-verdict":
            report = Path(args.report)
            rc, line = record_done_verdict(root, args.head, args.verdict, args.model, report)
        elif args.cmd == "record-commit":
            rc, line = record_commit(root)
        elif args.cmd == "plan-approved":
            rc, line = plan_approved(root, args.by)
        else:
            rc, line = pr_opened_attended(root, args.n)
        print(line, file=sys.stderr if rc else sys.stdout)
        return rc

    if args.cmd == "preflight":
        owner = origin_owner(root)
        # Decided from the origin URL alone, before any network call: there is no
        # reason to reach GitHub for a repo already refused, and a gh error here
        # would mask the real reason.
        owner_why = owner_reason(owner)
        if owner_why:
            print(
                f"PREFLIGHT REFUSED: {owner_why}; auto runs only in repos they own",
                file=sys.stderr,
            )
            return 1
        slug = origin_repo(root)
        if slug in NEVER_AUTO:
            print(f"PREFLIGHT REFUSED: {slug} is never an unattended target", file=sys.stderr)
            return 1
        # The plan is decided from the tree, so it comes before gh: with gh
        # unavailable the operator still reads "no plan", not the gh failure.
        plan_refusals, plan_warnings = check_plan(root, Path(args.plan))
        for r in plan_refusals:
            print(f"PREFLIGHT REFUSED: {r}", file=sys.stderr)
            return 1
        # An untracked plan in a fresh clone shows as `??` until it is excluded, and the
        # dirty-tree check below would refuse the run for the pipeline's own file.
        if not plan_is_tracked(root, args.plan):
            ensure_ignored(root, args.plan)
        try:
            view = gh_repo_view(root)
            is_fork = bool(view.get("isFork"))
            default = (view.get("defaultBranchRef") or {}).get("name")
        except Exception as exc:  # gh unavailable -> fail closed with one line
            print(f"PREFLIGHT FAILED gh: {exc}", file=sys.stderr)
            return 1
        git(root, "fetch", "origin", check=False)
        prs, cause = list_next_auto_prs(root)
        reasons = preflight_checks(
            root,
            Path(args.plan),
            origin_owner=owner,
            is_fork=is_fork,
            default_branch=default,
            open_next_auto_prs=prs,
            pr_listing_cause=cause,
        )
        if reasons:
            for r in reasons:
                print(f"PREFLIGHT REFUSED: {r}", file=sys.stderr)
            return 1
        gh(["label", "create", "next-auto", "--force"], root=root)
        ensure_ignored(root, ".claude/")
        ensure_ignored(root, RUN_SUBDIR + "/")
        if (root / ".env").exists():
            ensure_ignored(root, ".env")
        # the plan-time warnings, once, after every check has passed: a step that names a
        # gate-config stem without its basename will fail the gate at its edit
        for w in plan_warnings:
            print(f"PREFLIGHT WARN {w}", file=sys.stderr)
        print("PREFLIGHT OK")
        return 0

    if args.cmd == "start":
        # same order as preflight: the origin URL, then the plan from the tree, then gh
        slug = origin_repo(root)
        if slug in NEVER_AUTO:
            print(f"START FAILED: {slug} is never an unattended target", file=sys.stderr)
            return 1
        # the owner allow-list, as preflight and merge apply it (sweep spec R2)
        owner_why = owner_reason(slug.split("/", 1)[0] if slug else None)
        if owner_why:
            print(f"START FAILED: {owner_why}; auto runs only in repos they own", file=sys.stderr)
            return 1
        plan_refusals, _warnings = check_plan(root, Path(args.plan))
        for r in plan_refusals:
            print(f"START FAILED: {r}", file=sys.stderr)
            return 1
        # the plan's approval is on the record before any step runs (ai-dlc-record R13)
        rc, reason = plan_approval(root, args.plan)
        if rc:
            print(f"START FAILED{':' if rc == 1 else ''} {reason}", file=sys.stderr)
            return 1
        try:
            conflict = shares_conflict(root, args.plan)
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            print(f"START FAILED: {exc!r}", file=sys.stderr)
            return 1
        if conflict:
            print(f"START FAILED: {conflict}", file=sys.stderr)
            return 1
        try:
            view = gh_repo_view(root)
        except Exception as exc:  # gh unavailable -> fail closed with one line, no run
            print(f"START FAILED gh: {exc}", file=sys.stderr)
            return 1
        default = (view.get("defaultBranchRef") or {}).get("name") or "main"
        try:
            state = start(
                root,
                args.plan,
                default,
                run_id=args.run_id,
                steps_per_invocation=args.steps,
                sync=not args.no_sync,
            )
        except RuntimeError as exc:
            print(f"START FAILED {exc}", file=sys.stderr)
            return 1
        print(f"RUN STARTED {state['run_id']}")
        print(GOAL_LINE)
        return 0

    if args.cmd == "size":
        state = load_state(root)
        n = run_size(Path(state["worktree"]), state["default"])
        over = n > RUN_SIZE_LIMIT
        tail = f" over {RUN_SIZE_LIMIT}, stop the run" if over else ""
        print(f"RUN SIZE {n} lines{tail}")
        return 1 if over else 0

    if args.cmd == "state":
        state = load_state(root)
        print(json.dumps(state, indent=2) if state else "{}")
        return 0

    if args.cmd == "step-begin":
        state = step_begin(root, args.n)
        print(f"STEP {args.n} BEGIN base={state['base_head'][:8]}")
        return 0

    if args.cmd == "gate":
        try:
            ok, reason = gate(root, args.n)
        except (DefinitionError, ProveRedApparatusError) as exc:
            print(f"GATE APPARATUS step {args.n}: {exc}", file=sys.stderr)
            return 2
        print(f"GATE {'PASS' if ok else 'FAIL'} step {args.n}: {reason}")
        return 0 if ok else 1

    if args.cmd == "prove-red":
        state = load_state(root)
        wt = Path(state["worktree"])
        stage_all(wt)
        test_cmd = plan_commands(wt, state["plan"])[0]
        plan = _worktree_plan(state)
        patterns = next_plan.plan_test_globs(plan)
        step = next((s for s in plan.steps if s.number == args.n), None)
        staged = staged_files(wt)
        proof: dict = {}
        try:
            reason = prove_red(
                root, wt, state["base_head"], staged, test_cmd, patterns=patterns,
                guard=step.guard_mutation if step else None, proof=proof,
            )  # fmt: skip
        except ProveRedApparatusError as exc:
            print(f"PROVE-RED APPARATUS: {exc}", file=sys.stderr)
            return 2
        guard = proof.get("guard_mutation")
        if guard:
            line = f"the guard mutation applied to {guard['path']} turns the step's tests red"
        else:
            line = reason or "tests are red on base"
        print(f"PROVE-RED {'PASS' if reason is None else 'FAIL'}: {line}")
        return 0 if reason is None else 1

    if args.cmd == "commit" and load_state(root) is None:
        rc, line = commit_attended(root, args.n, args.title)
        print(line, file=sys.stdout if rc == 0 else sys.stderr)
        return rc

    if args.cmd == "commit":
        rc, line = commit_run(root, args.n, args.title)
        print(line, file=sys.stdout if rc == 0 else sys.stderr)
        return rc

    if args.cmd == "review-package":
        print(f"REVIEW {review_package(root, args.n, base=args.base)}")
        return 0

    if args.cmd == "revert":
        state = load_state(root)
        wt = Path(state["worktree"])
        attempt = state.get("attempt", 1)
        patch = Path(state["run_dir"]) / f"step-{args.n}-attempt-{attempt}.patch"
        # with a fix round open, the step's first base: the whole step goes
        base = (state.get("step_origin") or state)["base_head"]
        try:
            revert_step(root, wt, base, patch)
        except RuntimeError as exc:
            print(f"REVERT FAILED: {exc}", file=sys.stderr)
            return 1
        state = load_state(root)
        state["attempt"] = attempt + 1
        save_state(state)
        print(f"REVERTED step {args.n}; patch saved at {patch}")
        return 0

    if args.cmd == "set":
        note = Path(args.note_file).read_text(encoding="utf-8").strip() if args.note_file else ""
        if load_state(root) is None:
            # attended, run-less: `done` reads the step's check record and appends its
            # evidence clause (done-note counts spec, R1 and R2); any other state is the
            # plan edit alone, as next_plan.py set makes it
            if args.state == "done":
                rc, line = set_done_attended(root, args.n, note)
                print(line, file=sys.stdout if rc == 0 else sys.stderr)
                return rc
            _set_plan_status(root, "prompt_plan.md", args.n, args.state, note)
            print(f"SET step {args.n} {args.state}")
            return 0
        if args.state == "done":
            # in a run: the same core over <run-dir>/result-K.json and the worktree plan
            rc, line = set_done_run(root, args.n, note)
            print(line, file=sys.stdout if rc == 0 else sys.stderr)
            return rc
        set_step(root, args.n, args.state, note)
        print(f"SET step {args.n} {args.state}")
        return 0

    if args.cmd == "append":
        append_plan(root, args.section, Path(args.note_file).read_text(encoding="utf-8"))
        print(f"APPENDED {args.section}")
        return 0

    if args.cmd == "wrap-up":
        try:
            result = wrap_up(root, repo_slug=args.repo, reason=args.reason)
        except RuntimeError as exc:
            print(f"WRAP-UP FAILED: {exc}", file=sys.stderr)
            return 1
        if result["no_pr"]:
            print(f"RUN STOPPED {result['reason']} {NO_PR_SUFFIX}")
            return 0
        pr = f" PR #{result['pr_number']}" if result["pr_number"] is not None else ""
        print(f"RUN STOPPED {result['reason']}{pr}")
        print(result["pr_url"])
        if result["record"]:
            print(f"RECORD FAILED after{pr or ' the PR'}: {result['record']}", file=sys.stderr)
            return 2
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
