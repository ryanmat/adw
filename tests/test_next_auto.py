# Description: Tests for next_auto.py, the /next auto orchestrator's git and state machine.
# Description: Real git repos, bare origin, real worktrees and pytest runs; gh is an eval-only seam.

import hashlib
import inspect
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import next_auto
import next_plan
import pytest

# -B: no bytecode, so the fixture tree stays clean between the checks that read it
TEST_CMD = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"]


@pytest.fixture(autouse=True)
def session_id(monkeypatch):
    """Every test runs with one fixed run key, as Claude Code sets it in a session, so the suite
    reads the same in a session, a terminal and CI; a test that needs another sets its own."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ATTENDED_SESSION_ID)


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


PLAN = """\
# Fixture Implementation Plan
Spec: spec.md. Owner: the owner. Written: 2026-09-11.

## Conventions binding every step
- Test: uv run pytest
- Lint: uv run ruff check .
- Run: uv run python -m fixturepkg

### Step 1: first
Status: PENDING
Eval: no
Mutation: required
Size: 20
Write a failing test then implement.

### Step 2: second
Status: PENDING
Eval: no
Mutation: n/a (docs only)
Size: 10
Second step.

## Decisions
(none)

## Findings
(none)
"""


def make_repo(tmp_path, dirty=False):
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "t@t.t")
    git(repo, "config", "user.name", "t")
    # no background repack: the Stop hook tests compare every file under .git before and after
    # a stop, and an auto maintenance run packing loose objects between the two reads failed one
    git(repo, "config", "gc.auto", "0")
    git(repo, "config", "maintenance.auto", "false")
    git(repo, "remote", "add", "origin", str(origin))
    (repo / "prompt_plan.md").write_text(PLAN)
    (repo / "src.py").write_text("# Description: fixture\n# Description: two\nX = 1\n")
    (repo / "conftest.py").write_text("")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_base.py").write_text("def test_base():\n    assert True\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "init")
    git(repo, "push", "-u", "origin", "main")
    if dirty:
        (repo / "dirty.txt").write_text("uncommitted\n")
    return repo, origin


# ---- parse_origin_owner -----------------------------------------------------


def test_parse_origin_owner_variants():
    assert next_auto.parse_origin_owner("https://github.com/ryanmat/repo-one.git") == "ryanmat"
    assert next_auto.parse_origin_owner("git@github.com:ryanmat/repo-one.git") == "ryanmat"
    assert next_auto.parse_origin_owner("https://github.com/other-org/thing") == "other-org"
    assert next_auto.parse_origin_owner("/local/path/origin.git") is None


# ---- ensure_ignored ---------------------------------------------------------


def test_ensure_ignored_appends_exclude(tmp_path):
    repo, _ = make_repo(tmp_path)
    assert git(repo, "check-ignore", ".claude", check=False).returncode != 0
    assert next_auto.ensure_ignored(repo, ".claude/") is True
    assert git(repo, "check-ignore", ".claude/x", check=False).returncode == 0
    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert ".claude/" in exclude


# ---- preflight refusals -----------------------------------------------------


def base_kwargs():
    return {
        "origin_owner": "ryanmat",
        "is_fork": False,
        "default_branch": "main",
        "open_next_auto_prs": [],
    }


def test_preflight_passes_clean(tmp_path):
    repo, _ = make_repo(tmp_path)
    git(repo, "fetch", "origin")
    assert next_auto.preflight_checks(repo, repo / "prompt_plan.md", **base_kwargs()) == []


def test_preflight_refuses_foreign_owner(tmp_path):
    repo, _ = make_repo(tmp_path)
    git(repo, "fetch", "origin")
    kw = base_kwargs()
    kw["origin_owner"] = "other-org"
    reasons = next_auto.preflight_checks(repo, repo / "prompt_plan.md", **kw)
    assert any("owner" in r.lower() for r in reasons)


def test_preflight_refuses_fork(tmp_path):
    repo, _ = make_repo(tmp_path)
    git(repo, "fetch", "origin")
    kw = base_kwargs()
    kw["is_fork"] = True
    reasons = next_auto.preflight_checks(repo, repo / "prompt_plan.md", **kw)
    assert any("fork" in r.lower() for r in reasons)


def test_preflight_refuses_dirty_tree(tmp_path):
    repo, _ = make_repo(tmp_path, dirty=True)
    git(repo, "fetch", "origin")
    reasons = next_auto.preflight_checks(repo, repo / "prompt_plan.md", **base_kwargs())
    assert any("clean" in r.lower() or "dirty" in r.lower() for r in reasons)


def test_preflight_refuses_existing_state(tmp_path):
    repo, _ = make_repo(tmp_path)
    git(repo, "fetch", "origin")
    rd = repo / ".next-auto" / "20260101-aaaaaa"
    rd.mkdir(parents=True)
    (rd / "state.json").write_text("{}")
    reasons = next_auto.preflight_checks(repo, repo / "prompt_plan.md", **base_kwargs())
    assert any("run" in r.lower() and "exist" in r.lower() for r in reasons)


def test_preflight_refuses_unpushed_ancestor(tmp_path):
    repo, _ = make_repo(tmp_path)
    # advance origin ahead of local so origin/main is not an ancestor of local main
    (repo / "extra.txt").write_text("more\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "second")
    git(repo, "push", "origin", "main")
    git(repo, "reset", "--hard", "HEAD~1")
    git(repo, "fetch", "origin")
    reasons = next_auto.preflight_checks(repo, repo / "prompt_plan.md", **base_kwargs())
    assert any("ancestor" in r.lower() or "behind" in r.lower() for r in reasons)


def test_preflight_refuses_open_pr(tmp_path):
    repo, _ = make_repo(tmp_path)
    git(repo, "fetch", "origin")
    kw = base_kwargs()
    kw["open_next_auto_prs"] = [(3, "next-auto/first-20260101-aaaaaa")]
    reasons = next_auto.preflight_checks(repo, repo / "prompt_plan.md", **kw)
    assert any("pr" in r.lower() for r in reasons)


def test_preflight_refuses_invalid_plan(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "prompt_plan.md").write_text("# broken\n\nno conventions no steps\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "break plan")
    git(repo, "push", "origin", "main")
    git(repo, "fetch", "origin")
    reasons = next_auto.preflight_checks(repo, repo / "prompt_plan.md", **base_kwargs())
    assert any("plan" in r.lower() or "conventions" in r.lower() for r in reasons)


# ---- start / state ----------------------------------------------------------


def test_start_layout(tmp_path):
    repo, _ = make_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    run_dir = repo / ".next-auto" / "20260911-abc123"
    assert (run_dir / "state.json").exists()
    wt = run_dir / "wt"
    assert wt.is_dir()
    branch = git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert branch == state["branch"]
    assert branch.startswith("next-auto/")
    assert (wt / "prompt_plan.md").exists()
    loaded = next_auto.load_state(repo)
    assert loaded["run_id"] == "20260911-abc123"
    assert loaded["default"] == "main"


def test_step_begin_records_head_and_brief(tmp_path):
    repo, _ = make_repo(tmp_path)
    next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = repo / ".next-auto" / "20260911-abc123" / "wt"
    head = git(wt, "rev-parse", "HEAD").stdout.strip()
    state = next_auto.step_begin(repo, 1)
    assert state["base_head"] == head
    assert state["current_step"] == 1
    brief = repo / ".next-auto" / "20260911-abc123" / "brief-1.md"
    assert brief.exists()
    assert "### Step 1: first" in brief.read_text()
    # plan in the worktree marked IN PROGRESS
    import next_plan

    plan = next_plan.parse_plan((wt / "prompt_plan.md").read_text())
    assert plan.steps[0].state == "IN PROGRESS"


# ---- gate sub-checks --------------------------------------------------------


def start_wt(tmp_path):
    repo, _ = make_repo(tmp_path)
    next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = repo / ".next-auto" / "20260911-abc123" / "wt"
    return repo, wt


def test_check_head_unchanged(tmp_path):
    _repo, wt = start_wt(tmp_path)
    head = git(wt, "rev-parse", "HEAD").stdout.strip()
    assert next_auto.check_head_unchanged(wt, head) is None
    (wt / "x.py").write_text("x=1\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-m", "sneaky")
    assert next_auto.check_head_unchanged(wt, head) is not None


def test_check_staged_safe_flags_env(tmp_path):
    _repo, wt = start_wt(tmp_path)
    (wt / ".env").write_text("SECRET=1\n")
    (wt / "ok.py").write_text("ok=1\n")
    git(wt, "add", "-A", "-f")
    staged = next_auto.staged_files(wt)
    reason = next_auto.check_staged_safe(wt, staged)
    assert reason and ".env" in reason


def test_check_staged_safe_flags_big_file(tmp_path):
    _repo, wt = start_wt(tmp_path)
    (wt / "big.bin").write_bytes(b"0" * (1_048_577))
    git(wt, "add", "-A")
    staged = next_auto.staged_files(wt)
    reason = next_auto.check_staged_safe(wt, staged)
    assert reason and "big.bin" in reason


def test_check_staged_safe_flags_claude_dir(tmp_path):
    _repo, wt = start_wt(tmp_path)
    (wt / "CLAUDE.md").write_text("toolchain\n")
    git(wt, "add", "-A", "-f")
    staged = next_auto.staged_files(wt)
    reason = next_auto.check_staged_safe(wt, staged)
    assert reason and "CLAUDE.md" in reason


def test_check_staged_safe_allows_normal(tmp_path):
    _repo, wt = start_wt(tmp_path)
    (wt / "feature.py").write_text("y=2\n")
    git(wt, "add", "-A")
    staged = next_auto.staged_files(wt)
    assert next_auto.check_staged_safe(wt, staged) is None


def test_check_staged_safe_admits_toolchain_paths_the_globs_name(tmp_path):
    """toolchain-files R3: a plan's globs admit the CLAUDE.md and .claude/ paths they name;
    with no globs the first staged toolchain path is refused as before."""
    _repo, wt = start_wt(tmp_path)
    (wt / "CLAUDE.md").write_text("@AGENTS.md\n")
    (wt / ".claude").mkdir()
    (wt / ".claude" / "settings.json").write_text("{}\n")
    git(wt, "add", "-A", "-f")
    staged = next_auto.staged_files(wt)
    toolchain = ["CLAUDE.md", ".claude/**"]
    assert next_auto.check_staged_safe(wt, staged, toolchain=toolchain) is None
    assert (
        next_auto.check_staged_safe(wt, staged, toolchain=[])
        == "staged toolchain file: .claude/settings.json"
    )


def test_check_staged_safe_still_refuses_a_toolchain_path_no_glob_names(tmp_path):
    """toolchain-files R3: a glob matches the whole path, so CLAUDE.md admits the root file
    and not a nested one."""
    _repo, wt = start_wt(tmp_path)
    (wt / "apps" / "web").mkdir(parents=True)
    (wt / "apps" / "web" / "CLAUDE.md").write_text("@AGENTS.md\n")
    git(wt, "add", "-A", "-f")
    staged = next_auto.staged_files(wt)
    reason = next_auto.check_staged_safe(wt, staged, toolchain=["CLAUDE.md"])
    assert reason == "staged toolchain file: apps/web/CLAUDE.md"


def test_check_staged_safe_still_refuses_a_secret_shaped_name_under_any_glob(tmp_path):
    """toolchain-files R3: the secret-shaped name rule runs ahead of the admission."""
    _repo, wt = start_wt(tmp_path)
    (wt / ".env").write_text("SECRET=1\n")
    git(wt, "add", "-A", "-f")
    staged = next_auto.staged_files(wt)
    reason = next_auto.check_staged_safe(wt, staged, toolchain=["**"])
    assert reason == "staged a secret-shaped file: .env"


def test_check_staged_safe_still_caps_an_admitted_file_at_1_mb(tmp_path):
    """toolchain-files R3: an admitted path still meets the 1 MB cap."""
    _repo, wt = start_wt(tmp_path)
    (wt / ".claude").mkdir()
    (wt / ".claude" / "big.bin").write_bytes(b"0" * (1_048_577))
    git(wt, "add", "-A", "-f")
    staged = next_auto.staged_files(wt)
    reason = next_auto.check_staged_safe(wt, staged, toolchain=[".claude/**"])
    assert reason == "staged file over 1 MB: .claude/big.bin (1048577 bytes)"


def test_check_gate_config_flags_conftest():
    reason = next_auto.check_gate_config(
        ["conftest.py", "src.py"], brief_text="implement the thing"
    )
    assert reason and "conftest.py" in reason


def test_check_gate_config_allows_when_brief_names_it(tmp_path):
    reason = next_auto.check_gate_config(
        ["pytest.ini"], brief_text="update pytest.ini to add a marker"
    )
    assert reason is None


def test_remove_tree_logs_what_it_could_not_remove(tmp_path, capsys):
    """prove_red's scratch cleanup used to swallow OSError with a bare pass. A cleanup that
    fails leaves a worktree-sized directory under the run dir with no trace of why; the rule
    is log before swallow."""
    tmp = tmp_path / "next-auto-red-x"
    locked = tmp / "locked"
    locked.mkdir(parents=True)
    (locked / "keep.txt").write_text("x")
    os.chmod(locked, 0o500)  # no write bit: unlinking keep.txt raises PermissionError
    try:
        next_auto._remove_tree(tmp)
    finally:
        os.chmod(locked, 0o700)
    err = capsys.readouterr().err
    assert "prove-red: could not remove" in err
    assert "keep.txt" in err
    assert tmp.exists()  # best effort: the failure is reported, not raised


def test_remove_tree_is_silent_on_success(tmp_path, capsys):
    tmp = tmp_path / "next-auto-red-y"
    (tmp / "a" / "b").mkdir(parents=True)
    (tmp / "a" / "b" / "f.txt").write_text("x")
    next_auto._remove_tree(tmp)
    assert not tmp.exists()
    assert capsys.readouterr().err == ""


def test_prove_red_passes_when_new_test_fails_on_base(tmp_path):
    repo, wt = start_wt(tmp_path)
    head = git(wt, "rev-parse", "HEAD").stdout.strip()
    # a NEW test that fails against base (feature not implemented yet)
    (wt / "tests" / "test_feature.py").write_text(
        "from src import FEATURE\n\ndef test_feature():\n    assert FEATURE == 42\n"
    )
    git(wt, "add", "-A")
    staged = next_auto.staged_files(wt)
    # prove_red returns None when the new test is genuinely red on base
    assert next_auto.prove_red(repo, wt, head, staged, TEST_CMD) is None


def test_prove_red_fails_when_new_test_green_on_base(tmp_path):
    repo, wt = start_wt(tmp_path)
    head = git(wt, "rev-parse", "HEAD").stdout.strip()
    # a NEW test that already passes on base -> not test-first -> prove_red flags it
    (wt / "tests" / "test_trivial.py").write_text("def test_trivial():\n    assert 1 == 1\n")
    git(wt, "add", "-A")
    staged = next_auto.staged_files(wt)
    reason = next_auto.prove_red(repo, wt, head, staged, TEST_CMD)
    assert reason is not None


def test_pytest_counts(tmp_path):
    _repo, wt = start_wt(tmp_path)
    (wt / "tests" / "test_more.py").write_text(
        "import pytest\n\n"
        "def test_a():\n    assert True\n\n"
        "@pytest.mark.skip('x')\ndef test_b():\n    assert True\n"
    )
    collected, skipped, _xfailed = next_auto.pytest_counts(wt, TEST_CMD)
    assert collected >= 3
    assert skipped >= 1


# ---- commit / revert --------------------------------------------------------


def test_commit_step_formatter_retry(tmp_path):
    _repo, wt = start_wt(tmp_path)
    # a pre-commit hook that reformats a file and fails the first time only
    # in a worktree, hooks live in the common dir; resolve it
    common = git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    hook = Path(common) / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "#!/bin/sh\n"
        'marker="$(git rev-parse --git-common-dir)/reformatted"\n'
        'if [ ! -f "$marker" ]; then\n'
        '  printf "\\n# formatted\\n" >> feature.py\n'
        '  touch "$marker"\n'
        "  exit 1\n"
        "fi\n"
        "exit 0\n"
    )
    hook.chmod(0o755)
    (wt / "feature.py").write_text("z = 3\n")
    git(wt, "add", "-A")
    sha = next_auto.commit_step(wt, 1, "first")
    assert sha
    # the reformat is included in the commit
    show = git(wt, "show", "--stat", "HEAD").stdout
    assert "feature.py" in show
    assert git(wt, "status", "--porcelain").stdout.strip() == ""


def test_revert_step_saves_patch_and_resets(tmp_path):
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1)
    base = git(wt, "rev-parse", "HEAD").stdout.strip()
    (wt / "feature.py").write_text("broken = True\n")
    git(wt, "add", "-A")
    patch = repo / ".next-auto" / "20260911-abc123" / "step-1-attempt-1.patch"
    next_auto.revert_step(repo, wt, base, patch)
    assert patch.exists()
    assert "feature.py" in patch.read_text()
    assert not (wt / "feature.py").exists()
    assert git(wt, "rev-parse", "HEAD").stdout.strip() == base
    # only the plan is modified: restored to its step-begin state, uncommitted as then
    assert git(wt, "status", "--porcelain").stdout.strip() == "M prompt_plan.md"


# ---- wrap-up summary --------------------------------------------------------


def test_write_summary_reads_step_states_from_the_plan(tmp_path):
    """The worktree plan is the one source of truth for step states. state.json only
    ever learned DONE (from commit), so a summary built from it reported a blocked run
    as completed and omitted the blocked step (eval decision-stop, 2026-09-11)."""
    repo, _ = make_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    next_auto.set_step(repo, 1, "done", "12 tests green, aaa111")
    next_auto.set_step(repo, 2, "blocked-decision", "which backend?")
    next_auto.append_plan(repo, "decisions", "Step 2: sqlite or json? Cost if wrong: a rewrite.")
    path = next_auto.write_summary(state, reason="BLOCKED (decision) at step 2")
    assert path.exists()
    body = path.read_text()
    assert "Run summary" in body
    assert "Stop reason: BLOCKED (decision) at step 2" in body
    assert "Step 1" in body and "DONE" in body
    assert "Step 2" in body and "BLOCKED" in body
    assert "which backend" in body
    assert "sqlite or json" in body


# ---- count / size checks ----------------------------------------------------


def test_check_counts_flags_fewer_collected():
    assert next_auto.check_counts((10, 0, 0), (9, 0, 0)) is not None


def test_check_counts_flags_more_skips():
    reason = next_auto.check_counts((10, 1, 0), (11, 3, 0))
    assert reason and "skip" in reason.lower()


def test_check_counts_flags_more_xfail():
    reason = next_auto.check_counts((10, 0, 0), (11, 0, 2))
    assert reason and "xfail" in reason.lower()


def test_check_counts_ok_when_growing():
    assert next_auto.check_counts((10, 2, 1), (13, 2, 1)) is None


def test_check_counts_ok_when_base_unknown():
    assert next_auto.check_counts(None, (5, 0, 0)) is None


def test_check_step_size_flags_over_cap(tmp_path):
    _repo, wt = start_wt(tmp_path)
    base = git(wt, "rev-parse", "HEAD").stdout.strip()
    (wt / "huge.py").write_text("\n".join(f"x{i} = {i}" for i in range(400)) + "\n")
    git(wt, "add", "-A")
    reason = next_auto.check_step_size(wt, base, limit=300)
    assert reason and "300" in reason


def test_check_step_size_ok_under_cap(tmp_path):
    _repo, wt = start_wt(tmp_path)
    base = git(wt, "rev-parse", "HEAD").stdout.strip()
    (wt / "small.py").write_text("y = 1\n")
    git(wt, "add", "-A")
    assert next_auto.check_step_size(wt, base, limit=300) is None


# ---- composed gate ----------------------------------------------------------


LINT_OK = [sys.executable, "-c", "raise SystemExit(0)"]


def good_step(wt):
    """A test-first step: a new test that is red on base, plus the implementation."""
    (wt / "tests" / "test_feature.py").write_text(
        "from src import FEATURE\n\n\ndef test_feature():\n    assert FEATURE == 42\n"
    )
    (wt / "src.py").write_text("# Description: fixture\n# Description: two\nX = 1\nFEATURE = 42\n")


def test_gate_passes_on_test_first_step(tmp_path):
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert ok, reason


def test_gate_fails_when_head_moved(tmp_path):
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    git(wt, "add", "-A")
    git(wt, "commit", "-m", "implementer committed, which is a rule break")
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert not ok
    assert "HEAD moved" in reason


def test_gate_blocks_conftest_gaming(tmp_path):
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    # sneak a collection-skipping conftest the brief never named
    (wt / "conftest.py").write_text("collect_ignore_glob = ['tests/*']\n")
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert not ok
    assert "conftest.py" in reason


def test_gate_blocks_test_that_is_green_on_base(tmp_path):
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    (wt / "tests" / "test_vacuous.py").write_text("def test_vacuous():\n    assert True\n")
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert not ok
    assert "prove-red" in reason


def test_gate_blocks_staged_secret(tmp_path):
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    (wt / ".env").write_text("TOKEN=abc\n")
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert not ok
    assert ".env" in reason


def test_gate_fails_when_lint_fails(tmp_path):
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    lint_bad = [sys.executable, "-c", "raise SystemExit(1)"]
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=lint_bad)
    assert not ok
    assert "Lint" in reason


# ---- review package ---------------------------------------------------------


def test_review_package_renders_diff(tmp_path):
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    git(wt, "add", "-A")
    next_auto.commit_step(wt, 1, "first")
    path = next_auto.review_package(repo, 1)
    assert path.exists()
    body = path.read_text()
    assert "FEATURE = 42" in body
    assert "tests/test_feature.py" in body


# ---- preflight ordering -----------------------------------------------------


def test_owner_refusal_precedes_any_network_call(tmp_path, monkeypatch):
    """A foreign owner is decided from the origin URL alone. Calling gh first would
    turn a clear owner refusal into an opaque gh failure, and would reach the network
    for a repo already refused."""
    repo, _ = make_repo(tmp_path)
    git(repo, "remote", "set-url", "origin", "https://github.com/other-org/thing.git")
    called = []

    def boom(_root):
        called.append(True)
        raise AssertionError("gh must not be called once the owner is refused")

    monkeypatch.setattr(next_auto, "gh_repo_view", boom)
    rc = next_auto.main(["--root", str(repo), "preflight", "prompt_plan.md"])
    assert rc != 0
    assert called == []


def test_missing_plan_refuses_with_one_line(tmp_path, capsys, monkeypatch):
    """Preflight fails closed with one line, not a traceback. A stack trace in an
    unattended run is noise the operator has to decode. Owner and gh are made to pass
    so the missing plan is the only thing left to refuse on."""
    repo, _ = make_repo(tmp_path)
    git(repo, "remote", "set-url", "origin", "https://github.com/ryanmat/thing.git")
    (repo / "prompt_plan.md").unlink()
    git(repo, "commit", "-am", "remove the plan")
    monkeypatch.setattr(
        next_auto,
        "gh_repo_view",
        lambda _root: {"isFork": False, "defaultBranchRef": {"name": "main"}},
    )
    monkeypatch.setattr(next_auto, "list_next_auto_prs", lambda _root: ([], ""))
    rc = next_auto.main(["--root", str(repo), "preflight", "prompt_plan.md"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "PREFLIGHT REFUSED" in err
    assert "Traceback" not in err
    assert len([ln for ln in err.strip().splitlines() if ln.strip()]) == 1


def test_missing_plan_is_refused_before_gh_is_reached(tmp_path, capsys, monkeypatch):
    """Design order is owner, then plan, then gh. With gh unavailable, a ryanmat repo
    without a plan must say "no plan", not report the gh failure; every later check
    reads the plan, so there is nothing for gh to decide yet."""
    repo, _ = make_repo(tmp_path)
    git(repo, "remote", "set-url", "origin", "https://github.com/ryanmat/thing.git")
    (repo / "prompt_plan.md").unlink()
    git(repo, "commit", "-am", "remove the plan")
    called = []

    def boom(_root):
        called.append(True)
        raise RuntimeError("gh repo view failed: gh: command not found on PATH")

    monkeypatch.setattr(next_auto, "gh_repo_view", boom)
    rc = next_auto.main(["--root", str(repo), "preflight", "prompt_plan.md"])
    assert rc != 0
    assert called == []
    err = capsys.readouterr().err
    assert "PREFLIGHT REFUSED: no plan at" in err
    assert "gh" not in err
    assert len([ln for ln in err.strip().splitlines() if ln.strip()]) == 1


def test_invalid_plan_is_refused_before_gh_is_reached(tmp_path, capsys, monkeypatch):
    """Same order for a plan that exists but does not validate."""
    repo, _ = make_repo(tmp_path)
    git(repo, "remote", "set-url", "origin", "https://github.com/ryanmat/thing.git")
    (repo / "prompt_plan.md").write_text(PLAN.replace("Status: PENDING", "Status: MAYBE", 1))
    git(repo, "commit", "-am", "break the plan")
    called = []

    def boom(_root):
        called.append(True)
        raise RuntimeError("gh repo view failed: gh: command not found on PATH")

    monkeypatch.setattr(next_auto, "gh_repo_view", boom)
    rc = next_auto.main(["--root", str(repo), "preflight", "prompt_plan.md"])
    assert rc != 0
    assert called == []
    err = capsys.readouterr().err
    assert "PREFLIGHT REFUSED: plan does not validate" in err
    assert "gh" not in err


def test_start_refuses_missing_or_invalid_plan_before_gh(tmp_path, capsys, monkeypatch):
    """start has the same order as preflight: the plan is decided from the tree, so with
    gh absent a missing plan says "no plan", not the gh failure, and nothing is created."""
    repo, _ = make_repo(tmp_path)
    called = []

    def boom(_root):
        called.append(True)
        raise RuntimeError("gh repo view failed: gh: command not found on PATH")

    monkeypatch.setattr(next_auto, "gh_repo_view", boom)
    rc = next_auto.main(["--root", str(repo), "start", "missing_plan.md", "--no-sync"])
    assert rc == 1
    assert called == []
    err = capsys.readouterr().err
    assert err.strip().splitlines() == [f"START FAILED: no plan at {repo / 'missing_plan.md'}"]
    assert not (repo / ".next-auto").exists()

    (repo / "prompt_plan.md").write_text(PLAN.replace("Status: PENDING", "Status: MAYBE", 1))
    git(repo, "commit", "-am", "break the plan")
    rc = next_auto.main(["--root", str(repo), "start", "prompt_plan.md", "--no-sync"])
    assert rc == 1
    assert called == []
    err = capsys.readouterr().err
    assert err.startswith("START FAILED: plan does not validate")
    assert not (repo / ".next-auto").exists()


# ---- no active run ------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["gate", "1"],
        ["step-begin", "1"],
        ["revert", "1"],
        ["size"],
        ["prove-red", "1"],
        ["review-package", "1"],
        ["append", "findings", "--note-file", "NOTE"],
        ["wrap-up"],
        ["state"],
    ],
    ids=lambda a: a[0],
)
def test_run_commands_fail_with_one_line_when_no_run_is_active(tmp_path, capsys, argv):
    """Every subcommand that reads state.json fails closed with one line when there is
    no run, instead of a TypeError traceback on state["worktree"]."""
    repo, _ = make_repo(tmp_path)
    note = tmp_path / "note.txt"
    note.write_text("n\n")
    argv = [str(note) if a == "NOTE" else a for a in argv]
    rc = next_auto.main(["--root", str(repo), *argv])
    assert rc == 1
    out = capsys.readouterr()
    assert "Traceback" not in out.err
    want = f"NO ACTIVE RUN: no state.json under {repo / '.next-auto'}"
    assert out.err.strip().splitlines() == [want]
    assert out.out == ""


# ---- run directory location ---------------------------------------------------


def test_run_dir_is_not_under_claude():
    """Claude Code classifies every path under .claude/ as a sensitive file and denies
    Write, Edit, and shell redirects there in non-interactive mode. A worktree under
    .claude/ made the implementer unable to write a single test (eval decision-stop,
    2026-09-11)."""
    assert ".claude" not in Path(next_auto.RUN_SUBDIR).parts
    assert next_auto.RUN_SUBDIR == ".next-auto"


def test_check_staged_safe_rejects_run_dir_files(tmp_path):
    _repo, wt = start_wt(tmp_path)
    assert next_auto.check_staged_safe(wt, [".next-auto/r1/state.json"]) is not None
    assert next_auto.check_staged_safe(wt, [".next-auto/r1/wt/x.py"]) is not None
    assert next_auto.check_staged_safe(wt, ["src/x.py"]) is None


def test_preflight_cli_ignores_run_dir(tmp_path, monkeypatch):
    repo, _ = make_repo(tmp_path)
    git(repo, "fetch", "origin")
    monkeypatch.setattr(
        next_auto,
        "gh_repo_view",
        lambda _root: {"isFork": False, "defaultBranchRef": {"name": "main"}},
    )
    monkeypatch.setattr(next_auto, "list_next_auto_prs", lambda _root: ([], ""))
    monkeypatch.setattr(next_auto, "gh", lambda *_a, **_k: None)
    monkeypatch.setattr(next_auto, "origin_owner", lambda _root: "ryanmat")
    rc = next_auto.main(["--root", str(repo), "preflight", "prompt_plan.md"])
    assert rc == 0
    assert git(repo, "check-ignore", ".next-auto/r1/state.json", check=False).returncode == 0
    assert git(repo, "check-ignore", ".claude/settings.json", check=False).returncode == 0


# ---- set / append target the worktree plan -----------------------------------


def test_set_step_and_append_target_the_worktree_plan(tmp_path):
    """The orchestrator once ran next_plan.py set against the root copy by mistake and
    had to restore it. The auto-run commands take no path: they always edit the plan
    inside the worktree, the only copy a run may change (C1)."""
    repo, wt = start_wt(tmp_path)
    root_before = (repo / "prompt_plan.md").read_bytes()
    next_auto.set_step(repo, 1, "done", "3 tests green")
    next_auto.append_plan(repo, "decisions", "Ruling: kept X / because Y / cost: Z")
    next_auto.append_plan(repo, "findings", "Important: naming")
    import next_plan

    plan = next_plan.parse_plan((wt / "prompt_plan.md").read_text())
    assert plan.steps[0].state == "DONE" and "3 tests green" in plan.steps[0].note
    assert "kept X" in plan.decisions
    assert "naming" in plan.findings
    assert (repo / "prompt_plan.md").read_bytes() == root_before


def test_set_and_append_cli(tmp_path, capsys):
    repo, wt = start_wt(tmp_path)
    # `set K done` in a run reads <run-dir>/result-K.json, as gate K writes it on a pass, and
    # Step 1 is a Mutation: required step, so the note carries the words
    state = next_auto.load_state(repo)
    next_auto.run_result_path(state, 1).write_text(
        json.dumps(dict(RESULT_FIXTURE, step=1, mode="auto", mutation="reported"), indent=2)
    )
    note = tmp_path / "note.txt"
    note.write_text("commit abc123, mutation killed both ways")
    root = ["--root", str(repo)]
    assert next_auto.main([*root, "set", "1", "done", "--note-file", str(note)]) == 0
    note.write_text("Step 2: which store?")
    assert next_auto.main([*root, "append", "decisions", "--note-file", str(note)]) == 0
    text = (wt / "prompt_plan.md").read_text()
    assert f"Status: DONE (commit abc123, mutation killed both ways; {FIXTURE_CLAUSE})" in text
    assert "- Step 2: which store?" in text
    assert "Status: PENDING" in (repo / "prompt_plan.md").read_text()


# ---- wrap-up: commit, push, PR, RUN STOPPED, marker gone ------------------------


def test_parse_origin_repo_variants():
    parse = next_auto.parse_origin_repo
    assert parse("https://github.com/ryanmat/repo-one.git") == "ryanmat/repo-one"
    assert parse("git@github.com:ryanmat/repo-one.git") == "ryanmat/repo-one"
    assert parse("https://github.com/other-org/thing") == "other-org/thing"
    assert next_auto.parse_origin_repo("/local/path/origin.git") is None


def fake_gh(calls, url="https://github.com/ryanmat/fixture/pull/7"):
    def _gh(args, root=None):
        calls.append(list(args))
        return subprocess.CompletedProcess(["gh", *args], 0, stdout=url + "\n", stderr="")

    return _gh


def fake_gh_visibility(calls, *, private, url="https://github.com/ryanmat/fixture/pull/7"):
    """The gh seam answering `repo view --json isPrivate` and `pr create`."""

    def _gh(args, root=None):
        calls.append(list(args))
        if args[:2] == ["repo", "view"]:
            return subprocess.CompletedProcess(
                ["gh", *args], 0, stdout=json.dumps({"isPrivate": private}) + "\n", stderr=""
            )
        return subprocess.CompletedProcess(["gh", *args], 0, stdout=url + "\n", stderr="")

    return _gh


def test_wrap_up_commits_plan_pushes_opens_pr_and_disarms(tmp_path, monkeypatch, capsys):
    """Everything the design says wrap-up does, on real git with a bare origin. The first
    implementation only wrote summary.md, so the branch never carried the final plan
    state, no PR opened, and the marker stayed armed (eval decision-stop, 2026-09-11)."""
    repo, origin = make_repo(tmp_path)
    next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    state = next_auto.step_begin(repo, 1)
    wt = Path(state["worktree"])
    (wt / "feature.py").write_text("# Description: f\n# Description: g\nY = 2\n")
    next_auto.stage_all(wt)
    sha = next_auto.commit_step(wt, 1, "first")
    next_auto.set_step(repo, 1, "done", f"2 tests green, {sha[:8]}")
    next_auto.step_begin(repo, 2)
    next_auto.append_plan(repo, "decisions", "Step 2: sqlite or json?")
    next_auto.set_step(repo, 2, "blocked-decision", "sqlite or json?")
    calls = []
    # the fixture repo is private, so the full summary with reports is the body
    monkeypatch.setattr(next_auto, "gh", fake_gh_visibility(calls, private=True))

    result = next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")

    branch = state["branch"]
    # the final plan state is committed on the run branch and pushed
    committed = git(repo, "show", f"{branch}:prompt_plan.md").stdout
    assert "Status: DONE (2 tests green" in committed
    assert "Status: BLOCKED (decision: sqlite or json?)" in committed
    assert "- Step 2: sqlite or json?" in committed
    remote = subprocess.run(
        ["git", "-C", str(origin), "rev-parse", f"refs/heads/{branch}"],
        capture_output=True, text=True,
    )
    assert remote.returncode == 0
    assert remote.stdout.strip() == git(wt, "rev-parse", "HEAD").stdout.strip()
    # the PR was opened hold-only against the right repo with the label and the summary
    pr = [c for c in calls if c[:2] == ["pr", "create"]]
    assert len(pr) == 1
    c = pr[0]
    assert "--repo" in c and c[c.index("--repo") + 1] == "ryanmat/fixture"
    assert "--head" in c and c[c.index("--head") + 1] == branch
    assert "--label" in c and c[c.index("--label") + 1] == "next-auto"
    assert "--body-file" in c and c[c.index("--body-file") + 1].endswith("summary.md")
    assert not any(c[:2] == ["pr", "merge"] for c in calls)
    assert result["pr_number"] == 7
    assert result["reason"].startswith("BLOCKED (decision) at step 2")
    summary = (Path(state["run_dir"]) / "summary.md").read_text()
    assert "Run summary" in summary and "sqlite or json" in summary
    # the marker is gone, so the guard hook disarms
    assert next_auto.find_state_file(repo) is None
    assert (Path(state["run_dir"]) / "wt").is_dir()
    assert (Path(state["run_dir"]) / "brief-1.md").exists()


def test_wrap_up_cli_prints_run_stopped(tmp_path, monkeypatch, capsys):
    repo, _ = make_repo(tmp_path)
    next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    next_auto.step_begin(repo, 1)
    next_auto.set_step(repo, 1, "failed", "gate red twice")
    monkeypatch.setattr(next_auto, "gh", fake_gh([]))
    monkeypatch.setattr(next_auto, "origin_repo", lambda _root: "ryanmat/fixture")
    rc = next_auto.main(["--root", str(repo), "wrap-up"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "RUN STOPPED FAILED step 1 PR #7" in out
    assert next_auto.find_state_file(repo) is None


def test_wrap_up_reason_completed_when_all_done(tmp_path, monkeypatch):
    repo, _ = make_repo(tmp_path)
    next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    next_auto.set_step(repo, 1, "done", "a")
    next_auto.set_step(repo, 2, "done", "b")
    monkeypatch.setattr(next_auto, "gh", fake_gh([]))
    result = next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    assert result["reason"] == "completed 2 steps"


def test_wrap_up_keeps_marker_when_push_fails(tmp_path, monkeypatch):
    """A half-finished wrap-up must not disarm the hook: the run is still live until the
    branch is on the remote and the PR exists."""
    repo, _ = make_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    next_auto.set_step(repo, 1, "done", "a")
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    monkeypatch.setattr(next_auto, "gh", fake_gh([]))
    with pytest.raises(RuntimeError, match="push"):
        next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    assert next_auto.find_state_file(repo) is not None
    assert (Path(state["run_dir"]) / "summary.md").exists()


def test_wrap_up_keeps_marker_when_pr_fails(tmp_path, monkeypatch):
    repo, _ = make_repo(tmp_path)
    next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    next_auto.set_step(repo, 1, "done", "a")

    def failing_gh(args, root=None):
        return subprocess.CompletedProcess(["gh", *args], 1, stdout="", stderr="label missing")

    monkeypatch.setattr(next_auto, "gh", failing_gh)
    with pytest.raises(RuntimeError, match="pr create"):
        next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    assert next_auto.find_state_file(repo) is not None


def test_gh_seam_reports_missing_binary_as_failed_call(monkeypatch, tmp_path):
    """A machine without gh must fail wrap-up with one line and keep the marker, not
    traceback out of the seam."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    r = next_auto.gh(["--version"])
    assert r.returncode != 0
    assert "gh" in r.stderr


def test_revert_step_drops_a_committed_attempt(tmp_path):
    """A step is committed on a green gate before review. When review then fails it
    (Critical survives the fix round), revert must drop that commit and keep its diff
    as the patch. The orchestrator once did this by hand with git reset --hard."""
    repo, wt = start_wt(tmp_path)
    state = next_auto.step_begin(repo, 1)
    base = state["base_head"]
    (wt / "gamed.py").write_text("# Description: g\n# Description: h\nZ = 3\n")
    next_auto.stage_all(wt)
    sha = next_auto.commit_step(wt, 1, "gamed")
    assert sha != base
    patch = repo / ".next-auto" / "20260911-abc123" / "step-1-attempt-2.patch"
    next_auto.revert_step(repo, wt, base, patch)
    assert git(wt, "rev-parse", "HEAD").stdout.strip() == base
    assert not (wt / "gamed.py").exists()
    assert "gamed.py" in patch.read_text() and "Z = 3" in patch.read_text()
    assert git(wt, "status", "--porcelain").stdout.strip() == "M prompt_plan.md"


# ---- review gaps, 2026-09-11 ------------------------------------------------------


def test_gate_config_covers_pyproject_and_ruff_config():
    """pytest and ruff config live in pyproject.toml, ruff.toml, .ruff.toml, setup.cfg.
    C6 says the diff may not touch pytest or ruff config unless the brief names the file."""
    for f in ("pyproject.toml", "ruff.toml", ".ruff.toml", "setup.cfg", "pkg/pyproject.toml"):
        assert next_auto.check_gate_config([f], "brief text") is not None, f
    assert next_auto.check_gate_config(["pyproject.toml"], "add httpx to pyproject.toml") is None
    assert next_auto.check_gate_config(["src/x.py"], "") is None


def test_size_cli_reports_run_level_diff(tmp_path, capsys):
    repo, wt = start_wt(tmp_path)
    assert next_auto.main(["--root", str(repo), "size"]) == 0
    assert "RUN SIZE" in capsys.readouterr().out
    body = "\n".join(f"X{i} = {i}" for i in range(600))
    (wt / "big.py").write_text("# Description: a\n# Description: b\n" + body + "\n")
    next_auto.stage_all(wt)
    next_auto.commit_step(wt, 1, "big")
    assert next_auto.main(["--root", str(repo), "size"]) == 1
    out = capsys.readouterr().out
    assert "RUN SIZE" in out and "over 500" in out


def test_start_cli_fails_closed_when_gh_unavailable(tmp_path, monkeypatch, capsys):
    """No silent fallback to main: a gh failure at start is one line and no run."""
    repo, _ = make_repo(tmp_path)

    def boom(_root):
        raise RuntimeError("gh repo view failed: not logged in")

    monkeypatch.setattr(next_auto, "gh_repo_view", boom)
    rc = next_auto.main(["--root", str(repo), "start", "prompt_plan.md", "--no-sync"])
    err = capsys.readouterr().err
    assert rc == 1
    assert err.strip().startswith("START FAILED")
    assert len(err.strip().splitlines()) == 1
    assert next_auto.find_state_file(repo) is None
    assert not (repo / ".next-auto").exists() or not list((repo / ".next-auto").glob("*/wt"))


def make_uv_repo(tmp_path, *, lock=True):
    repo, origin = make_repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "fixt"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n'
        "dependencies = []\n"
    )
    if lock:
        subprocess.run(["uv", "lock"], cwd=repo, check=True, capture_output=True)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "uv project")
    git(repo, "push", "origin", "main")
    return repo, origin


def test_start_sync_uses_the_worktree_venv(tmp_path):
    """C3: tests must run against the worktree's environment, not main's."""
    repo, _ = make_uv_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=True)
    wt = Path(state["worktree"])
    assert Path(state["venv"]).is_relative_to(wt)
    assert (wt / ".venv").is_dir()


def test_start_sync_failure_is_one_line_and_leaves_no_run(tmp_path):
    repo, _ = make_uv_repo(tmp_path, lock=False)
    with pytest.raises(RuntimeError, match="uv sync"):
        next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=True)
    assert next_auto.find_state_file(repo) is None
    assert not (repo / ".next-auto" / "20260911-abc123" / "wt").exists()
    branches = git(repo, "branch", "--list", "next-auto/*").stdout.strip()
    assert branches == ""


# ---- plan snapshot: the implementer may never edit prompt_plan.md ------------------
# The agent rule forbids it, but a rule the gate cannot see is only a request. The plan
# as handed to the implementer is snapshotted at step-begin, and the gate requires the
# worktree copy to be byte-identical to it. The snapshot's hash lives in state.json,
# which the hook already protects, so the reference itself cannot be rewritten.


def test_step_begin_writes_plan_snapshot(tmp_path):
    repo, wt = start_wt(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    snap = Path(state["run_dir"]) / "plan-snapshot-1.md"
    assert snap.exists()
    assert snap.read_bytes() == (wt / "prompt_plan.md").read_bytes()
    assert b"Status: IN PROGRESS" in snap.read_bytes()
    assert state["plan_snapshot_sha256"] == next_auto.sha256_bytes(snap.read_bytes())


def test_gate_fails_when_the_plan_changed_during_the_step(tmp_path):
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    plan = wt / "prompt_plan.md"
    plan.write_text(plan.read_text().replace("Status: IN PROGRESS", "Status: DONE (I say so)"))
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert not ok
    assert "prompt_plan.md" in reason and "snapshot" in reason


def test_gate_fails_without_a_snapshot(tmp_path):
    repo, wt = start_wt(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    (Path(state["run_dir"]) / "plan-snapshot-1.md").unlink()
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert not ok
    assert "snapshot" in reason


def second_step(wt):
    (wt / "tests" / "test_second.py").write_text(
        "from src import SECOND\n\n\ndef test_second():\n    assert SECOND == 2\n"
    )
    (wt / "src.py").write_text(
        "# Description: fixture\n# Description: two\nX = 1\nFEATURE = 42\nSECOND = 2\n"
    )


def test_orchestrator_set_and_append_after_the_gate_are_not_flagged(tmp_path):
    """The orchestrator's own plan edits (set done, append) run after the gate and are
    part of the next step's snapshot, so they never trip the check."""
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert ok, reason
    next_auto.commit_step(wt, 1, "first")
    next_auto.set_step(repo, 1, "done", "3 tests green")
    next_auto.append_plan(repo, "decisions", "Ruling: kept X / spec R1 / cost: none")
    state = next_auto.step_begin(repo, 2, test_cmd=TEST_CMD)
    snap = (Path(state["run_dir"]) / "plan-snapshot-2.md").read_text()
    assert "Status: DONE (3 tests green)" in snap and "kept X" in snap
    second_step(wt)
    ok, reason = next_auto.gate(repo, 2, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert ok, reason


def test_revert_restores_the_plan_from_the_snapshot(tmp_path):
    """A hard reset to base drops every uncommitted plan edit: step K-1's DONE note and
    step K's IN PROGRESS. The double-fail-stop run showed the orchestrator re-running
    `set 1 done` after a revert. The plan is the orchestrator's, not the implementer's,
    so revert puts it back exactly as the step began."""
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    next_auto.stage_all(wt)
    next_auto.commit_step(wt, 1, "first")
    next_auto.set_step(repo, 1, "done", "3 tests green")
    state = next_auto.step_begin(repo, 2, test_cmd=TEST_CMD)
    snap = Path(state["run_dir"]) / "plan-snapshot-2.md"
    # the implementer edits the plan and does some work
    plan = wt / "prompt_plan.md"
    plan.write_text(plan.read_text().replace("Second step.", "Second step, reworded."))
    (wt / "junk.py").write_text("j = 1\n")
    ok, reason = next_auto.gate(repo, 2, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert not ok and "prompt_plan.md" in reason
    patch = Path(state["run_dir"]) / "step-2-attempt-1.patch"
    next_auto.revert_step(repo, wt, state["base_head"], patch)
    assert plan.read_bytes() == snap.read_bytes()
    assert "Status: DONE (3 tests green)" in plan.read_text()
    assert not (wt / "junk.py").exists()
    # the retry's honest work passes the plan check
    second_step(wt)
    ok, reason = next_auto.gate(repo, 2, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert ok, reason


def test_revert_refuses_a_tampered_snapshot(tmp_path):
    repo, wt = start_wt(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    snap = Path(state["run_dir"]) / "plan-snapshot-1.md"
    snap.write_text(snap.read_text().replace("Status: IN PROGRESS", "Status: DONE (forged)"))
    (wt / "junk.py").write_text("j = 1\n")
    patch = Path(state["run_dir"]) / "step-1-attempt-1.patch"
    with pytest.raises(RuntimeError, match="snapshot"):
        next_auto.revert_step(repo, wt, state["base_head"], patch)
    assert next_auto.main(["--root", str(repo), "revert", "1"]) == 1


# ---- untracked plan round trip (review gap 3, 2026-09-11) --------------------------------
# project-a and project-b keep their plans untracked by design (info/exclude, public mirrors), so a
# worktree built from the default branch never had one and the run failed at step-begin.
# start copies the root plan in, the shared exclude keeps it out of every stage, and wrap-up
# copies it back once the PR exists so rulings and findings survive the night.


def make_untracked_plan_repo(tmp_path, *, excluded=True):
    """The plan exists at the root and is absent from the default branch. excluded=True is
    project-a and project-b (info/exclude already names it); excluded=False is a fresh clone with
    the plan copied in and no exclude entry yet."""
    repo, origin = make_repo(tmp_path)
    git(repo, "rm", "--cached", "-q", "prompt_plan.md")
    git(repo, "commit", "-q", "-m", "keep the plan out of the repo")
    git(repo, "push", "-q", "origin", "main")
    if excluded:
        (repo / ".git" / "info" / "exclude").write_text("prompt_plan.md\n")
    assert git(repo, "ls-files", "--error-unmatch", "prompt_plan.md", check=False).returncode != 0
    return repo, origin


def test_start_copies_untracked_plan_into_worktree(tmp_path):
    repo, _ = make_untracked_plan_repo(tmp_path)
    root_bytes = (repo / "prompt_plan.md").read_bytes()
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = Path(state["worktree"])
    assert (wt / "prompt_plan.md").read_bytes() == root_bytes
    assert state["plan_tracked"] is False
    assert next_auto.load_state(repo)["plan_tracked"] is False
    assert git(wt, "ls-files", "--error-unmatch", "prompt_plan.md", check=False).returncode != 0


def test_start_records_tracked_plan(tmp_path):
    repo, _ = make_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    assert state["plan_tracked"] is True


def test_start_ensures_plan_ignored_in_fresh_clone(tmp_path):
    repo, _ = make_untracked_plan_repo(tmp_path, excluded=False)
    assert git(repo, "check-ignore", "prompt_plan.md", check=False).returncode != 0
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = Path(state["worktree"])
    assert git(repo, "check-ignore", "prompt_plan.md", check=False).returncode == 0
    # info/exclude is shared with the worktree, so `git add -A` there skips the plan too
    assert git(wt, "check-ignore", "prompt_plan.md", check=False).returncode == 0
    next_auto.stage_all(wt)
    assert "prompt_plan.md" not in next_auto.staged_files(wt)


def test_preflight_excludes_an_untracked_plan_before_the_dirty_check(tmp_path, monkeypatch):
    """A fresh clone with the plan copied in shows it as `??` until it is excluded, and the
    dirty-tree check would refuse the run before start ever ran. The plan is the pipeline's
    own file, so preflight excludes an untracked one before judging the tree, the same
    local-only edit it already makes for .claude/ and the run dir after the checks."""
    repo, _ = make_untracked_plan_repo(tmp_path, excluded=False)
    git(repo, "fetch", "origin")
    assert "?? prompt_plan.md" in git(repo, "status", "--porcelain").stdout
    monkeypatch.setattr(
        next_auto,
        "gh_repo_view",
        lambda _root: {"isFork": False, "defaultBranchRef": {"name": "main"}},
    )
    monkeypatch.setattr(next_auto, "list_next_auto_prs", lambda _root: ([], ""))
    monkeypatch.setattr(next_auto, "gh", lambda *_a, **_k: None)
    monkeypatch.setattr(next_auto, "origin_owner", lambda _root: "ryanmat")
    rc = next_auto.main(["--root", str(repo), "preflight", "prompt_plan.md"])
    assert rc == 0
    assert git(repo, "check-ignore", "prompt_plan.md", check=False).returncode == 0
    assert git(repo, "status", "--porcelain").stdout.strip() == ""


def test_gate_never_stages_untracked_plan(tmp_path):
    """The orchestrator's own set and append edit the worktree plan between steps; the shared
    exclude keeps those edits out of `git add -A`. Forced in by hand, the gate refuses the
    plan by name (an edit would already fail the snapshot check, tested above)."""
    repo, _ = make_untracked_plan_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = Path(state["worktree"])
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    next_auto.stage_all(wt)
    assert "prompt_plan.md" not in next_auto.staged_files(wt)
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert ok, reason
    git(wt, "add", "-f", "prompt_plan.md")
    staged = next_auto.staged_files(wt)
    assert "prompt_plan.md" in staged
    reason = next_auto.check_staged_safe(wt, staged, untracked_plan="prompt_plan.md")
    assert reason and "prompt_plan.md" in reason
    assert next_auto.check_staged_safe(wt, staged) is None  # a tracked plan may be staged
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert not ok and "prompt_plan.md" in reason


def test_wrap_up_untracked_plan_absent_from_branch_and_written_to_root(tmp_path, monkeypatch):
    repo, _ = make_untracked_plan_repo(tmp_path)
    root_before = (repo / "prompt_plan.md").read_bytes()
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = Path(state["worktree"])
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    next_auto.stage_all(wt)
    sha = next_auto.commit_step(wt, 1, "first")
    next_auto.set_step(repo, 1, "done", f"3 tests green, {sha[:8]}")
    next_auto.append_plan(repo, "decisions", "Step 2: sqlite or json?")
    next_auto.append_plan(repo, "findings", "Important: naming")
    next_auto.set_step(repo, 2, "blocked-decision", "sqlite or json?")
    assert (repo / "prompt_plan.md").read_bytes() == root_before
    calls = []
    monkeypatch.setattr(next_auto, "gh", fake_gh(calls))

    result = next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")

    branch = state["branch"]
    assert "chore(plan)" not in git(repo, "log", "--format=%s", f"main..{branch}").stdout
    assert git(repo, "ls-tree", "--name-only", branch, "prompt_plan.md").stdout.strip() == ""
    root_after = (repo / "prompt_plan.md").read_bytes()
    assert root_after == (wt / "prompt_plan.md").read_bytes()
    text = root_after.decode()
    assert "Status: DONE (3 tests green" in text
    assert "Status: BLOCKED (decision: sqlite or json?)" in text
    assert "- Step 2: sqlite or json? [run 20260911-abc123]" in text
    assert "- Important: naming [run 20260911-abc123]" in text
    assert result["pr_number"] == 7
    assert next_auto.find_state_file(repo) is None
    # the root copy is still untracked and still excluded (preflight, not start, excludes
    # the run dir, so only the plan's absence from status is the claim here)
    assert "prompt_plan.md" not in git(repo, "status", "--porcelain").stdout
    summary = (Path(state["run_dir"]) / "summary.md").read_text()
    assert "sqlite or json? [run 20260911-abc123]" in summary


def test_wrap_up_untracked_plan_stays_put_when_the_pr_fails(tmp_path, monkeypatch):
    repo, _ = make_untracked_plan_repo(tmp_path)
    root_before = (repo / "prompt_plan.md").read_bytes()
    next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    next_auto.set_step(repo, 1, "done", "a")

    def failing_gh(args, root=None):
        return subprocess.CompletedProcess(["gh", *args], 1, stdout="", stderr="label missing")

    monkeypatch.setattr(next_auto, "gh", failing_gh)
    with pytest.raises(RuntimeError, match="pr create"):
        next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    assert (repo / "prompt_plan.md").read_bytes() == root_before
    assert next_auto.find_state_file(repo) is not None


def test_tracked_plan_behavior_unchanged(tmp_path, monkeypatch):
    """A tracked plan rides the branch: the chore(plan) commit carries its state into the PR
    and the root copy waits for the merge."""
    repo, _ = make_repo(tmp_path)
    root_before = (repo / "prompt_plan.md").read_bytes()
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    next_auto.set_step(repo, 1, "done", "a")
    next_auto.append_plan(repo, "decisions", "Step 2: sqlite or json?")
    monkeypatch.setattr(next_auto, "gh", fake_gh([]))
    next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    branch = state["branch"]
    log = git(repo, "log", "--format=%s", f"main..{branch}").stdout
    assert "chore(plan): record run 20260911-abc123 step states" in log
    committed = git(repo, "show", f"{branch}:prompt_plan.md").stdout
    assert "Status: DONE (a)" in committed
    assert "- Step 2: sqlite or json? [run 20260911-abc123]" in committed
    assert (repo / "prompt_plan.md").read_bytes() == root_before


def test_append_plan_tags_entries_with_the_run_id(tmp_path):
    """A ruling or finding an agent wrote during a run reads differently from one the owner wrote
    by hand, which matters most when wrap-up copies an untracked plan straight back."""
    repo, wt = start_wt(tmp_path)
    next_auto.append_plan(repo, "decisions", "Ruling: kept X / spec R1 / cost: none\n")
    next_auto.append_plan(repo, "findings", "Minor: naming\nsecond line")
    text = (wt / "prompt_plan.md").read_text()
    assert "- Ruling: kept X / spec R1 / cost: none [run 20260911-abc123]" in text
    assert "- Minor: naming second line [run 20260911-abc123]" in text


# ---- secret scan and the public PR body (review gap 4, 2026-09-11) --------------------------
# wrap-up posted summary.md verbatim, every implementer report included, and the gate checked
# file names only. On a public repo that is the toolchain fingerprint plus an unscanned leak
# channel. Samples below are synthetic: right shape, wrong checksum, never issued.

# every sample is built by concatenation so the file at rest never carries a secret-shaped
# string: the staged-safe check scans whole staged files, this one included
SECRET_SAMPLES = [
    ("ghp_" + "A" * 36, "GitHub token"),
    ("github_pat_" + "B" * 22 + "_" + "C" * 59, "GitHub token"),
    ("gho_" + "D" * 36, "GitHub token"),
    ("sk-ant-api03-" + "x" * 40, "Anthropic key"),
    ("sk-" + "Z" * 48, "sk- key"),
    ("AKIA" + "IOSFODNN7EXAMPLE", "AWS access key"),
    ("xoxb-" + "1234567890-abcdefghijklmnop", "Slack token"),
    ("xoxp-" + "1234567890-abcdefghijklmnop", "Slack token"),
    ("123456789:AA" + "F" * 33, "Telegram bot token"),
    ("dtn_" + "q" * 32, "Daytona key"),
    ("-----BEGIN " + "RSA PRIVATE KEY-----", "PEM private key"),
    ("-----BEGIN " + "OPENSSH PRIVATE KEY-----", "PEM private key"),
    ("Authorization: Bearer " + "t" * 40, "Authorization Bearer"),
    ("API_KEY=" + "k" * 24, "KEY/TOKEN/SECRET assignment"),
    ("GH_TOKEN=" + "k" * 24, "KEY/TOKEN/SECRET assignment"),
    ('CLIENT_SECRET="' + "s" * 24 + '"', "KEY/TOKEN/SECRET assignment"),
    ("export LANGSMITH_API_KEY=lsv2_" + "p" * 40, "KEY/TOKEN/SECRET assignment"),
    ("TOKEN=" + "Kp9vQ2mL." + "x7RtZw4N." + "s8aZqW3e", "KEY/TOKEN/SECRET assignment"),
]


@pytest.mark.parametrize(("sample", "family"), SECRET_SAMPLES, ids=[f for _, f in SECRET_SAMPLES])
def test_scan_secrets_matches_each_family(sample, family):
    hits = next_auto.scan_secrets(f"some prose before\n{sample}\nand after\n")
    assert family in hits, hits


def test_scan_secrets_ignores_documentation_mentions():
    text = "\n".join(
        [
            "set GH_TOKEN in your environment before running",
            "the word token alone, and TOKEN in caps, and a secret kept elsewhere",
            "Authorization: Bearer <token>",
            "Authorization: Bearer $TOKEN",
            "keys start with sk-ant- and run about a hundred characters",
            "AKIA is the AWS access key prefix",
            "GH_TOKEN=<pat> goes in the sandbox Secrets",
            "GH_TOKEN=${FACTORY_GH_TOKEN} inside the sandbox",
            "ghp_ tokens are classic PATs; github_pat_ ones are fine-grained",
            "xoxb- is a bot token prefix",
            "dtn_ is the Daytona key prefix",
            "commit 4f4571f feat(next-auto): carry an untracked plan through the run and back",
            "plan_snapshot_sha256: " + "a" * 64,
            "- Step 2: sqlite or json? [run 20260911-abc123]",
            "CLAUDE_CODE_OAUTH_TOKEN from claude setup-token (allowed host api.anthropic.com)",
            "TOKEN: the machine account PAT expires in 90 days",
        ]
    )
    assert next_auto.scan_secrets(text) == []


def test_scan_secrets_ignores_a_key_bound_to_a_call():
    # the shapes acme/platform's CDK stacks carry: a secret's name bound to the call that
    # reads it from Secrets Manager, never the secret itself
    text = "\n".join(
        [
            "        ANTHROPIC_API_KEY: ecs.Secret.fromSecretsManager(anthropicApiKey),",
            "        OTLP_INGEST_TOKEN: ecs.Secret.fromSecretsManager(ingestToken),",
            "      AGENT_TELEMETRY_BEARER_TOKEN: ecs.Secret.fromSecretsManager(",
            "const SESSION_KEY = secretsmanager.Secret.fromSecretNameV2(this, 'Key', name);",
        ]
    )
    assert next_auto.scan_secrets(text) == []


@pytest.mark.parametrize(
    "line",
    [
        'API_KEY = "' + "k" * 24 + '"; client.init(API_KEY)',
        "TOKEN=" + "k" * 24 + " exec(run)",
        # a quoted literal is data, so a ( inside it is not a call
        "SECRET_KEY = 'django-insecure-" + "k3j9x" + "(8v!q2w#e4r%t6y&u'",
        # a call whose first argument is itself the secret
        'API_KEY: cdk.SecretValue.unsafePlainText("' + "Kp9vQ2mLx7RtZ" + "w4Ns8aZqW3e" + '")',
        'API_KEY = base64.urlsafe_b64decode("' + "Kp9vQ2mLx7RtZ" + "w4Ns8aZqW3e" + '")',
    ],
)
def test_scan_secrets_flags_a_literal_on_a_line_that_also_calls(line):
    assert next_auto.scan_secrets(line) == ["KEY/TOKEN/SECRET assignment"]


def test_scan_secrets_passes_a_shell_default_that_names_a_parameter():
    # acme/platform's scripts/obs-token.sh:12 and its smoke-test twin: the default is the
    # SSM parameter's name, a path, never the secret
    text = "\n".join(
        [
            'TOKEN_SECRET="${TOKEN_SECRET:-/acme/dev/observability/otlp-ingest-token}"',
            "TOKEN_SECRET=${TOKEN_SECRET-/acme/dev/observability/otlp-ingest-token}",
            ': "${API_KEY:=/acme/dev/agentgateway/virtual-key-name}"',
            '[ -n "${ANTHROPIC_API_KEY:-}" ] || missing+=("ANTHROPIC_API_KEY")',
            "PRIVATE_KEY=${PRIVATE_KEY:-./certs/client-private-key.pem}",
            "TOKEN_SECRET=${TOKEN_SECRET:- /acme/dev/observability/otlp-ingest-token}",
            "TOKEN_SECRET=${TOKEN_SECRET:-'/acme/dev/observability/otlp-ingest-token'}",
        ]
    )
    assert next_auto.scan_secrets(text) == []


@pytest.mark.parametrize(
    "line",
    [
        'export API_KEY="${API_KEY:-' + "Kp9vQ2mLx7RtZ" + "w4Ns8aZqW3e" + '}"',
        "GH_TOKEN=${GH_TOKEN:=" + "Kp9vQ2mLx7RtZ" + "w4Ns8aZqW3e" + "}",
        "CLIENT_SECRET=\"${CLIENT_SECRET-'" + "Kp9vQ2mLx7RtZ" + "w4Ns8aZqW3e" + "'}\"",
        # a Spring placeholder's default follows a bare colon
        'api.key = "${API_KEY:' + "Kp9vQ2mLx7RtZ" + "w4Ns8aZqW3e" + '}"',
        # forms 3.4.5 caught: space after the operator, a newline, a literal led by a dash
        "api.key: ${API_KEY: " + "Kp9vQ2mLx7RtZ" + "w4Ns8aZqW3e" + "}",
        "${API_KEY= " + "Kp9vQ2mLx7RtZ" + "w4Ns8aZqW3e" + "}",
        "${API_KEY = " + "Kp9vQ2mLx7RtZ" + "w4Ns8aZqW3e" + "}",
        "${API_KEY:\n  " + "Kp9vQ2mLx7RtZ" + "w4Ns8aZqW3e" + "}",
        "${API_KEY:--" + "Kp9vQ2mLx7RtZ" + "w4Ns8aZqW3e" + "}",
        "${API_KEY=-" + "Kp9vQ2mLx7RtZ" + "w4Ns8aZqW3e" + "}",
    ],
)
def test_scan_secrets_flags_a_literal_shell_default(line):
    assert next_auto.scan_secrets(line) == ["KEY/TOKEN/SECRET assignment"]


def test_check_staged_safe_flags_secret_shaped_content(tmp_path):
    _repo, wt = start_wt(tmp_path)
    (wt / "config.py").write_text('API_KEY = "sk-ant-api03-' + "x" * 40 + '"\n')
    (wt / "ok.py").write_text("ok = 1\n")
    (wt / "blob.bin").write_bytes(b"\x00\x01" + b"sk-" + b"Z" * 48)
    git(wt, "add", "-A")
    staged = next_auto.staged_files(wt)
    reason = next_auto.check_staged_safe(wt, staged)
    assert reason and "config.py" in reason and "Anthropic key" in reason
    git(wt, "reset", "-q", "config.py")
    (wt / "config.py").unlink()
    reason = next_auto.check_staged_safe(wt, next_auto.staged_files(wt))
    assert reason is None, reason  # a binary blob is not scanned as text


def test_gate_fails_on_secret_shaped_staged_content(tmp_path):
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    (wt / "src_settings.py").write_text("GH_TOKEN=ghp_" + "A" * 36 + "\n")
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert not ok
    assert "src_settings.py" in reason and "GitHub token" in reason


def _run_with_report_and_paths(tmp_path):
    repo, _ = make_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = Path(state["worktree"])
    run_dir = Path(state["run_dir"])
    next_auto.set_step(repo, 1, "done", "3 tests green, aaa111")
    next_auto.append_plan(repo, "findings", f"Minor: the patch sits at {run_dir}/step-2.patch")
    next_auto.append_plan(repo, "decisions", "Step 2: sqlite or json?")
    (run_dir / "report-1.md").write_text(
        f"# Report step 1\n\nWrote {wt}/src.py and ran `uv run pytest` in {wt}.\n"
    )
    return repo, state, wt, run_dir


def test_pr_body_has_no_reports_and_no_absolute_paths_and_summary_does(tmp_path):
    _repo, state, wt, run_dir = _run_with_report_and_paths(tmp_path)
    summary_path = next_auto.write_summary(state, reason="stopped after 1 of 2 steps")
    body_path = run_dir / "pr-body.md"
    assert summary_path == run_dir / "summary.md"
    summary = summary_path.read_text()
    body = body_path.read_text()
    for text in (summary, body):
        assert "## Run summary" in text
        assert "Stop reason: stopped after 1 of 2 steps" in text
        assert "Step 1: first: DONE (3 tests green, aaa111)" in text
        assert "sqlite or json? [run 20260911-abc123]" in text
        assert "Minor: the patch sits at" in text
    assert "### Step reports" in summary and "Report step 1" in summary
    assert str(wt) in summary and str(run_dir) in summary
    assert "Step reports" not in body and "Report step 1" not in body
    assert str(run_dir) not in body and str(wt) not in body
    assert not any(tok.startswith(("/private/", "/tmp/", "/Users/", "/home/", "/var/"))
                   for tok in body.replace("`", " ").split())


@pytest.mark.parametrize(("private", "expected"), [(True, "summary.md"), (False, "pr-body.md")])
def test_wrap_up_posts_pr_body_when_public_and_summary_when_private(
    tmp_path, monkeypatch, private, expected
):
    repo, _state, _wt, run_dir = _run_with_report_and_paths(tmp_path)
    calls = []
    monkeypatch.setattr(next_auto, "gh", fake_gh_visibility(calls, private=private))
    result = next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    assert result["pr_number"] == 7
    view = [c for c in calls if c[:2] == ["repo", "view"]]
    assert view and "isPrivate" in view[0][-1]
    pr = next(c for c in calls if c[:2] == ["pr", "create"])
    assert pr[pr.index("--body-file") + 1] == str(run_dir / expected)
    assert (run_dir / "summary.md").exists() and (run_dir / "pr-body.md").exists()


def test_wrap_up_treats_unknown_visibility_as_public(tmp_path, monkeypatch):
    """A gh that cannot say whether the repo is private gets the terse body: posting less
    is never a leak. The existing fake_gh answers every call with a PR URL, which is
    exactly that case."""
    repo, _state, _wt, run_dir = _run_with_report_and_paths(tmp_path)
    calls = []
    monkeypatch.setattr(next_auto, "gh", fake_gh(calls))
    next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    pr = next(c for c in calls if c[:2] == ["pr", "create"])
    assert pr[pr.index("--body-file") + 1] == str(run_dir / "pr-body.md")


def test_wrap_up_refuses_secret_in_body_and_keeps_marker(tmp_path, monkeypatch, capsys):
    repo, origin = make_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    next_auto.set_step(repo, 1, "done", "a")
    next_auto.append_plan(repo, "findings", "the token is ghp_" + "A" * 36 + " (rotate it)")
    calls = []
    monkeypatch.setattr(next_auto, "gh", fake_gh_visibility(calls, private=True))
    with pytest.raises(RuntimeError, match="secret-shaped content in Findings"):
        next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    assert next_auto.find_state_file(repo) is not None
    assert not any(c[:2] == ["pr", "create"] for c in calls)
    ref = "refs/heads/" + state["branch"]
    remote = subprocess.run(
        ["git", "-C", str(origin), "rev-parse", "--verify", "--quiet", ref],
        capture_output=True, text=True,
    )
    assert remote.returncode != 0  # nothing was pushed
    rc = next_auto.main(["--root", str(repo), "wrap-up", "--repo", "ryanmat/fixture"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.strip().splitlines() == [
        "WRAP-UP FAILED: secret-shaped content in Findings (GitHub token)"
    ]
    assert next_auto.find_state_file(repo) is not None


def test_wrap_up_scans_step_reports_when_they_are_posted(tmp_path, monkeypatch):
    """Private repos get summary.md with the reports, so the reports are scanned too."""
    repo, _state, _wt, run_dir = _run_with_report_and_paths(tmp_path)
    (run_dir / "report-1.md").write_text("Report\n\nAKIAIOSFODNN7EXAMPLE\n")
    monkeypatch.setattr(next_auto, "gh", fake_gh_visibility([], private=True))
    with pytest.raises(RuntimeError, match="secret-shaped content in Step reports"):
        next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    assert next_auto.find_state_file(repo) is not None
    # the same run on a public repo posts pr-body.md, which carries no reports, and passes
    monkeypatch.setattr(next_auto, "gh", fake_gh_visibility([], private=False))
    assert next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")["pr_number"] == 7


# ---- derive_reason, fail-closed PR listing, NEVER_AUTO (review, 2026-09-11) ------------------


def _plan_with_states(states):
    """A validating plan whose steps carry the given Status values, in order."""
    head = (
        "# Fixture Implementation Plan\n\n## Conventions binding every step\n"
        "- Test: uv run pytest\n- Lint: uv run ruff check .\n\n"
    )
    body = "".join(
        f"### Step {i}: step {i}\nStatus: {st}\nEval: no\nMutation: required\nbody\n\n"
        for i, st in enumerate(states, start=1)
    )
    text = head + body + "## Decisions\n(none)\n\n## Findings\n(none)\n"
    import next_plan

    assert next_plan.validate_text(text) == [], next_plan.validate_text(text)
    return next_plan.parse_plan(text)


def test_derive_reason_ignores_preblocked_later_step():
    """project-a's Increment 2 plan pre-marks Step 6 BLOCKED (human-only deploy). A night
    that finished steps 1 to 3 stopped there, not at step 6."""
    plan = _plan_with_states(
        ["DONE (a)", "DONE (b)", "DONE (c)", "PENDING", "PENDING",
         "BLOCKED (decision: human-only deploy)", "PENDING", "PENDING", "PENDING"]
    )
    assert next_auto.derive_reason(plan) == "stopped after 3 of 9 steps"
    plan = _plan_with_states(["DONE (a)", "IN PROGRESS", "BLOCKED (decision: x)"])
    assert next_auto.derive_reason(plan) == "stopped after 1 of 3 steps"
    plan = _plan_with_states(
        ["DONE (a)", "FAILED (gate red twice)", "PENDING", "BLOCKED (decision: x)"]
    )
    assert next_auto.derive_reason(plan) == "FAILED step 2"


def test_derive_reason_reports_block_when_reached():
    plan = _plan_with_states(["DONE (a)", "BLOCKED (decision: which store?)", "PENDING"])
    assert next_auto.derive_reason(plan) == "BLOCKED (decision) at step 2"
    plan = _plan_with_states(["DONE (a)", "DONE (b)", "BLOCKED (apparatus: no gpu)"])
    assert next_auto.derive_reason(plan) == "BLOCKED (apparatus) at step 3"
    plan = _plan_with_states(["DONE (a)", "DONE (b)"])
    assert next_auto.derive_reason(plan) == "completed 2 steps"


def gh_shim(tmp_path, monkeypatch, *, pr_list):
    """A real `gh` on PATH: `repo view` answers, `pr list` does what the test says."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    shim = bin_dir / "gh"
    shim.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *\"repo view\"*) echo '{\"owner\":{\"login\":\"ryanmat\"},\"isFork\":false,"
        "\"defaultBranchRef\":{\"name\":\"main\"},\"isPrivate\":true}';;\n"
        f'  *"pr list"*"--json number,headRefName"*) {pr_list};;\n'
        "  *) exit 0;;\n"
        "esac\n"
    )
    shim.chmod(0o755)
    import os

    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return shim


def test_has_open_next_auto_pr_is_none_when_gh_fails_or_does_not_parse(tmp_path, monkeypatch):
    repo, _ = make_repo(tmp_path)
    gh_shim(tmp_path, monkeypatch, pr_list='echo "HTTP 502: bad gateway" >&2; exit 1')
    assert next_auto.has_open_next_auto_pr(repo) is None
    gh_shim(tmp_path, monkeypatch, pr_list="echo not-json")
    assert next_auto.has_open_next_auto_pr(repo) is None
    gh_shim(tmp_path, monkeypatch, pr_list="echo '[]'")
    assert next_auto.has_open_next_auto_pr(repo) is False
    gh_shim(
        tmp_path, monkeypatch,
        pr_list="echo '[{\"headRefName\": \"next-auto/x-20260101-aaaaaa\", \"number\": 3}]'",
    )  # fmt: skip
    assert next_auto.has_open_next_auto_pr(repo) is True


def test_preflight_checks_refuse_unknown_pr_listing(tmp_path):
    repo, _ = make_repo(tmp_path)
    git(repo, "fetch", "origin")
    kw = base_kwargs()
    kw["open_next_auto_prs"] = None
    reasons = next_auto.preflight_checks(
        repo, repo / "prompt_plan.md", pr_listing_cause="HTTP 502: bad gateway", **kw
    )
    assert reasons == ["could not determine open next-auto PRs (HTTP 502: bad gateway)"]


def test_preflight_refuses_when_pr_listing_fails(tmp_path, monkeypatch, capsys):
    """A gh outage used to read as 'no open PR' and disable the one-open-PR gate."""
    repo, _ = make_repo(tmp_path)
    git(repo, "fetch", "origin")
    gh_shim(tmp_path, monkeypatch, pr_list='echo "HTTP 502: bad gateway" >&2; exit 1')
    monkeypatch.setattr(next_auto, "origin_owner", lambda _root: "ryanmat")
    monkeypatch.setattr(next_auto, "origin_repo", lambda _root: "ryanmat/thing")
    rc = next_auto.main(["--root", str(repo), "preflight", "prompt_plan.md"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.strip().splitlines() == [
        "PREFLIGHT REFUSED: could not determine open next-auto PRs (HTTP 502: bad gateway)"
    ]


def test_preflight_passes_with_a_working_shim(tmp_path, monkeypatch, capsys):
    repo, _ = make_repo(tmp_path)
    git(repo, "fetch", "origin")
    gh_shim(tmp_path, monkeypatch, pr_list="echo '[]'")
    monkeypatch.setattr(next_auto, "origin_owner", lambda _root: "ryanmat")
    monkeypatch.setattr(next_auto, "origin_repo", lambda _root: "ryanmat/thing")
    assert next_auto.main(["--root", str(repo), "preflight", "prompt_plan.md"]) == 0
    assert "PREFLIGHT OK" in capsys.readouterr().out


@pytest.mark.parametrize("slug", sorted(["ryanmat/never-one", "ryanmat/never-two"]))
def test_preflight_refuses_denylisted_repo(tmp_path, monkeypatch, capsys, slug):
    repo, _ = make_repo(tmp_path)
    git(repo, "remote", "set-url", "origin", f"https://github.com/{slug}.git")
    called = []

    def boom(_root):
        called.append(True)
        raise AssertionError("gh must not be called for a repo refused by name")

    monkeypatch.setattr(next_auto, "gh_repo_view", boom)
    rc = next_auto.main(["--root", str(repo), "preflight", "prompt_plan.md"])
    assert rc == 1
    assert called == []
    err = capsys.readouterr().err
    assert err.strip().splitlines() == [f"PREFLIGHT REFUSED: {slug} is never an unattended target"]
    assert frozenset({"ryanmat/never-one", "ryanmat/never-two"}) == next_auto.NEVER_AUTO


def test_preflight_denylist_checked_before_gh_and_before_the_plan(tmp_path, monkeypatch, capsys):
    """The refusal is decided from the origin URL alone: no plan needed, no network."""
    repo, _ = make_repo(tmp_path)
    git(repo, "remote", "set-url", "origin", "git@github.com:ryanmat/never-one.git")
    (repo / "prompt_plan.md").unlink()
    git(repo, "commit", "-q", "-am", "remove the plan")
    monkeypatch.setattr(next_auto, "gh_repo_view", lambda _root: pytest.fail("gh called"))
    rc = next_auto.main(["--root", str(repo), "preflight", "prompt_plan.md"])
    assert rc == 1
    assert capsys.readouterr().err.strip() == (
        "PREFLIGHT REFUSED: ryanmat/never-one is never an unattended target"
    )


def test_start_refuses_denylisted_repo(tmp_path, monkeypatch, capsys):
    """start is its own CLI invocation; a run that skipped preflight is refused the same way."""
    repo, _ = make_repo(tmp_path)
    git(repo, "remote", "set-url", "origin", "https://github.com/ryanmat/never-two.git")
    monkeypatch.setattr(next_auto, "gh_repo_view", lambda _root: pytest.fail("gh called"))
    rc = next_auto.main(["--root", str(repo), "start", "prompt_plan.md", "--no-sync"])
    assert rc == 1
    assert capsys.readouterr().err.strip() == (
        "START FAILED: ryanmat/never-two is never an unattended target"
    )
    assert not (repo / ".next-auto").exists()


# ---- run continuity: step-begin writes context-N.md beside brief-N.md (review, 2026-09-11) ----


def test_step_begin_writes_context_file(tmp_path):
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert ok, reason
    next_auto.commit_step(wt, 1, "first")
    next_auto.set_step(repo, 1, "done", "3 tests green")
    next_auto.append_plan(repo, "decisions", "Ruling: kept X / spec R1 / cost: none")
    next_auto.append_plan(repo, "findings", "Minor: naming")
    state = next_auto.step_begin(repo, 2, test_cmd=TEST_CMD)
    run_dir = Path(state["run_dir"])
    ctx = (run_dir / "context-2.md").read_text()
    brief = (run_dir / "brief-2.md").read_text()
    assert ctx.startswith("# Context for step 2\n")
    assert "- Step 1: first: DONE (3 tests green)" in ctx
    assert "- Ruling: kept X / spec R1 / cost: none [run 20260911-abc123]" in ctx
    assert "- Minor: naming [run 20260911-abc123]" in ctx
    assert "Step 2: second" not in ctx
    # the brief is untouched by continuity: conventions and the one step only
    assert "kept X" not in brief and "naming" not in brief
    assert "### Step 2: second" in brief
    assert (run_dir / "context-1.md").exists()


def test_finding_naming_conftest_does_not_authorize_gate_config(tmp_path):
    """check_gate_config substring-matches the brief. A Finding that names conftest.py
    lives in context-N.md, not the brief, so a staged conftest.py still fails the gate."""
    repo, wt = start_wt(tmp_path)
    next_auto.append_plan(repo, "findings", "Minor: conftest.py needs a shared fixture")
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    run_dir = Path(state["run_dir"])
    assert "conftest.py" in (run_dir / "context-1.md").read_text()
    assert "conftest.py" not in (run_dir / "brief-1.md").read_text()
    good_step(wt)
    (wt / "conftest.py").write_text("collect_ignore_glob = ['tests/*']\n")
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert not ok
    assert "conftest.py" in reason and "not named in the brief" in reason


# ---- attended check: begin ----------------------------------------------------

SH_PLAN = (
    PLAN.replace("- Test: uv run pytest", '- Test: sh -c "exit 0"')
    .replace("- Lint: uv run ruff check .", '- Lint: sh -c "exit 0"')
    .replace("- Run: uv run python -m fixturepkg", "- Run: sh -c true")
)


def attended_record(repo, n=1):
    return repo / ".next-auto" / "attended" / f"step-{n}.json"


def test_check_begin_writes_the_record_and_arms_nothing(tmp_path):
    repo, _ = make_repo(tmp_path)
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert msg == f"CHECK BEGIN step 1 base={head[:8]}"
    data = json.loads(attended_record(repo).read_text())
    assert data["base_head"] == head
    assert data["base_counts"] == [1, 0, 0]
    assert data["brief_path"].endswith("attended/brief-1.md")
    plan = next_plan.parse_plan((repo / "prompt_plan.md").read_text())
    assert Path(data["brief_path"]).read_text() == next_plan.render_brief(plan, 1)
    assert data["test_cmd"] == TEST_CMD
    assert data["lint_cmd"] == LINT_OK
    assert data["failures"] == 0
    assert not list((repo / ".next-auto").rglob("state.json*"))
    assert next_auto.load_state(repo) is None


def test_check_begin_ignores_the_run_dir_through_the_exclude_file(tmp_path):
    repo, _ = make_repo(tmp_path)
    assert git(repo, "check-ignore", "-q", ".next-auto", check=False).returncode != 0
    next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert git(repo, "check-ignore", "-q", ".next-auto", check=False).returncode == 0
    assert not (repo / ".gitignore").exists()
    assert ".next-auto" in (repo / ".git" / "info" / "exclude").read_text()
    # the record and brief are ignored, so the check itself never dirties the tree
    assert ".next-auto" not in git(repo, "status", "--porcelain").stdout


def test_check_begin_leaves_an_existing_ignore_alone(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / ".gitignore").write_text(".next-auto/\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "ignore")
    next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    exclude = repo / ".git" / "info" / "exclude"
    assert not exclude.exists() or ".next-auto" not in exclude.read_text()


def test_check_begin_refuses_a_missing_step(tmp_path, capsys):
    repo, _ = make_repo(tmp_path)
    rc = next_auto.main(["--root", str(repo), "check", "begin", "9"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no step 9 in plan" in err
    assert not attended_record(repo, 9).exists()


def test_check_begin_cli_reads_the_plan_conventions(tmp_path, capsys):
    repo, _ = make_repo(tmp_path)
    (repo / "prompt_plan.md").write_text(SH_PLAN)
    git(repo, "commit", "-qam", "sh plan")
    rc = next_auto.main(["--root", str(repo), "check", "begin", "1"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert out.out.startswith("CHECK BEGIN step 1 base=")
    data = json.loads(attended_record(repo).read_text())
    assert data["test_cmd"] == ["sh", "-c", "exit 0"]
    assert data["base_counts"] == [0, 0, 0]


def test_check_begin_cli_exit_2_when_the_test_program_is_missing(tmp_path, capsys):
    repo, _ = make_repo(tmp_path)
    text = SH_PLAN.replace('- Test: sh -c "exit 0"', "- Test: no-such-program-xyz-123")
    (repo / "prompt_plan.md").write_text(text)
    git(repo, "commit", "-qam", "broken plan")
    rc = next_auto.main(["--root", str(repo), "check", "begin", "1"])
    out = capsys.readouterr()
    assert rc == 2
    assert out.err.startswith("CHECK APPARATUS step 1:")


def test_check_is_not_a_run_command():
    assert "check" not in next_auto.RUN_COMMANDS


# ---- attended check: the gates ------------------------------------------------


def begun(tmp_path, plan_text=None):
    """A repo with an attended record for step 1, ready for the step's edits."""
    repo, _ = make_repo(tmp_path)
    if plan_text is not None:
        (repo / "prompt_plan.md").write_text(plan_text)
        git(repo, "commit", "-qam", "plan variant")
    rc, _msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0
    return repo


def test_check_passes_a_test_first_step(tmp_path):
    repo = begun(tmp_path)
    good_step(repo)
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])


def test_check_fails_prove_red_when_the_new_test_passes_on_base(tmp_path):
    repo = begun(tmp_path)
    (repo / "tests" / "test_feature.py").write_text(
        "from src import X\n\n\ndef test_x():\n    assert X == 1\n"
    )
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert lines == [
        "CHECK FAIL step 1: prove-red failed: the step's new/changed tests PASS against the base "
        "commit, so they do not demonstrate test-first"
    ]


def test_check_fails_when_collected_tests_shrink(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "tests" / "test_base.py").write_text(
        "def test_base():\n    assert True\n\n\ndef test_more():\n    assert True\n"
    )
    git(repo, "commit", "-qam", "two base tests")
    next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    good_step(repo)
    (repo / "tests" / "test_base.py").write_text("# the base tests were removed\n")
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert lines == ["CHECK FAIL step 1: collected tests dropped from 2 to 1"]


def test_check_fails_when_skips_grow(tmp_path):
    repo = begun(tmp_path)
    good_step(repo)
    (repo / "tests" / "test_base.py").write_text(
        "import pytest\n\n\n@pytest.mark.skip\ndef test_base():\n    assert True\n"
    )
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert lines == ["CHECK FAIL step 1: skipped tests grew from 0 to 1"]


def test_check_fails_on_a_staged_env_file(tmp_path):
    repo = begun(tmp_path)
    good_step(repo)
    (repo / ".env").write_text("TOKEN=abc\n")
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert lines == ["CHECK FAIL step 1: staged a secret-shaped file: .env"]


def _begun_with_old_test(tmp_path, body: str):
    """begun(), with a second committed test file tests/test_old.py holding `body`."""
    repo, _ = make_repo(tmp_path)
    (repo / "tests" / "test_old.py").write_text(body)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "old test")
    rc, _msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0
    return repo


def test_check_passes_a_test_first_step_that_also_deletes_a_test_file(tmp_path):
    repo = _begun_with_old_test(tmp_path, "def test_old():\n    assert True\n")
    good_step(repo)
    (repo / "tests" / "test_old.py").unlink()
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])


def test_check_refuses_a_step_whose_only_test_change_is_a_deletion(tmp_path):
    repo = _begun_with_old_test(tmp_path, "def test_old():\n    assert True\n")
    (repo / "src.py").write_text("# Description: fixture\n# Description: two\nX = 2\n")
    (repo / "tests" / "test_old.py").unlink()
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert (
        "CHECK FAIL step 1: no staged test files; a step must add or change a test first"
        " (Mutation/test-first)"
    ) in lines


def test_check_prove_red_removes_a_renamed_test_from_its_old_path(tmp_path):
    # a rename lists only the new path in a default `git diff --cached`; the old path must
    # leave the scratch too, or the base runs the test twice and errors on the duplicate
    repo = _begun_with_old_test(tmp_path, "def test_old():\n    assert True\n")
    (repo / "tests" / "unit").mkdir()
    git(repo, "mv", "tests/test_old.py", "tests/unit/test_old.py")
    (repo / "src.py").write_text("# Description: fixture\n# Description: two\nX = 2\n")
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert any("prove-red failed" in line for line in lines), lines


def test_check_prove_red_frees_a_test_path_the_step_turns_into_a_directory(tmp_path):
    # under a tests/** glob the base file tests/data is a test file; the step deletes it and
    # writes tests/data/test_new.py, so the scratch must remove the file before it writes
    repo, _ = make_repo(tmp_path)
    (repo / "prompt_plan.md").write_text(
        PLAN.replace("- Run: uv run python -m fixturepkg", "- Test-files: tests/**")
    )
    (repo / "tests" / "data").write_text("fixture\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "data file")
    rc, _msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0
    (repo / "tests" / "data").unlink()
    (repo / "tests" / "data").mkdir()
    (repo / "tests" / "data" / "test_new.py").write_text(
        "from src import FEATURE\n\n\ndef test_new():\n    assert FEATURE == 42\n"
    )
    (repo / "src.py").write_text(
        "# Description: fixture\n# Description: two\nX = 1\nFEATURE = 42\n"
    )
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])


def test_check_prove_red_removes_a_deleted_test_from_the_base_scratch(tmp_path):
    # the base's own failing test is the one the step deletes, so a scratch that kept it
    # would read red whatever the step's new test does
    repo = _begun_with_old_test(tmp_path, "def test_old():\n    assert False\n")
    (repo / "tests" / "test_old.py").unlink()
    (repo / "tests" / "test_feature.py").write_text("def test_feature():\n    assert True\n")
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert any("prove-red failed" in line for line in lines), lines


def test_check_reads_a_test_committed_during_the_step_from_the_recorded_base(tmp_path):
    # a red test committed on its own part way through the step (AGENTS.md's red commit) is
    # still the step's test change: the check diffs against check begin's base, not HEAD
    repo = begun(tmp_path)
    (repo / "tests" / "test_feature.py").write_text(
        "from src import FEATURE\n\n\ndef test_feature():\n    assert FEATURE == 42\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "red: the feature test")
    (repo / "src.py").write_text(
        "# Description: fixture\n# Description: two\nX = 1\nFEATURE = 42\n"
    )
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])


def test_check_is_apparatus_when_head_no_longer_descends_from_the_base(tmp_path):
    # a rebase or a reset during the step: a base-relative read would take other commits'
    # files as the step's own, so the check stops instead
    repo = begun(tmp_path)
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "checkout", "-q", "--orphan", "upstream")
    (repo / "upstream.py").write_text("# Description: fixture\n# Description: two\n")
    git(repo, "add", "upstream.py")
    git(repo, "commit", "-qm", "upstream work")
    good_step(repo)
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 2
    assert lines == [
        f"CHECK APPARATUS step 1: HEAD no longer descends from the step's base {base[:8]}; "
        "commit or set aside the step's work and run check begin 1 on the new HEAD"
    ]


def test_check_prove_red_removes_a_test_deleted_in_a_commit_during_the_step(tmp_path):
    # the base's failing test is deleted in a commit part way through the step; a scratch
    # that kept it would read red whatever the step's new test does
    repo = _begun_with_old_test(tmp_path, "def test_old():\n    assert False\n")
    git(repo, "rm", "-q", "tests/test_old.py")
    git(repo, "commit", "-qm", "drop the old test")
    (repo / "tests" / "test_feature.py").write_text("def test_feature():\n    assert True\n")
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert any("prove-red failed" in line for line in lines), lines


def test_check_passes_a_step_editing_a_file_that_binds_a_key_to_a_call(tmp_path):
    repo = begun(tmp_path)
    good_step(repo)
    (repo / "stack.ts").write_text(
        "secrets: {\n  OTLP_INGEST_TOKEN: ecs.Secret.fromSecretsManager(ingestToken),\n},\n"
    )
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])


def test_check_fails_on_a_conftest_edit_the_brief_did_not_name(tmp_path):
    repo = begun(tmp_path)
    good_step(repo)
    (repo / "conftest.py").write_text("import sys\n")
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert lines == [
        "CHECK FAIL step 1: changed gate/config file not named in the brief: conftest.py"
    ]


def test_check_allows_a_conftest_edit_the_brief_names(tmp_path):
    body = next_plan.parse_plan(PLAN).steps[0].body
    assert body in PLAN
    named = PLAN.replace(body, body + "\nThis step also edits conftest.py.", 1)
    repo = begun(tmp_path, plan_text=named)
    good_step(repo)
    (repo / "conftest.py").write_text("import sys\n")
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])


def test_check_fails_when_the_suite_is_red(tmp_path):
    repo = begun(tmp_path)
    good_step(repo)
    (repo / "src.py").write_text(
        "# Description: fixture\n# Description: two\nX = 1\nFEATURE = 41\n"
    )
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert len(lines) == 1
    assert lines[0].startswith("CHECK FAIL step 1: tests failed: ")
    assert "1 failed" in lines[0]


def test_check_reports_every_failed_reason_on_its_own_line(tmp_path):
    repo = begun(tmp_path)
    good_step(repo)
    (repo / ".env").write_text("TOKEN=abc\n")
    (repo / "conftest.py").write_text("import sys\n")
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert lines == [
        "CHECK FAIL step 1: staged a secret-shaped file: .env",
        "CHECK FAIL step 1: changed gate/config file not named in the brief: conftest.py",
    ]


def test_check_exit_2_when_the_test_program_is_missing(tmp_path):
    repo = begun(tmp_path)
    good_step(repo)
    rec = attended_record(repo)
    data = json.loads(rec.read_text())
    data["test_cmd"] = ["no-such-program-xyz-123"]
    rec.write_text(json.dumps(data))
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 2
    assert len(lines) == 1 and lines[0].startswith("CHECK APPARATUS step 1: ")


def test_check_without_begin_refuses(tmp_path):
    repo, _ = make_repo(tmp_path)
    good_step(repo)
    assert next_auto.check_step(repo, 1) == (
        1,
        ["CHECK FAIL step 1: no record; run check begin 1 first"],
    )


def test_check_cli_prints_the_lines_and_exit_code(tmp_path, capsys):
    repo = begun(tmp_path)
    good_step(repo)
    rc = next_auto.main(["--root", str(repo), "check", "1"])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.splitlines() == ["CHECK PASS step 1"]
    (repo / ".env").write_text("TOKEN=abc\n")
    rc = next_auto.main(["--root", str(repo), "check", "1"])
    out = capsys.readouterr()
    assert rc == 1
    assert out.err.splitlines() == ["CHECK FAIL step 1: staged a secret-shaped file: .env"]


# ---- attended check: no trace, failure counter, stale records -----------------


def tree_state(repo):
    return git(repo, "status", "--porcelain").stdout, git(repo, "diff", "--cached").stdout


def worktree_count(repo):
    out = git(repo, "worktree", "list", "--porcelain").stdout
    return sum(1 for ln in out.splitlines() if ln.startswith("worktree "))


def no_scratch(repo):
    return not list((repo / ".next-auto").glob("next-auto-*"))


def test_check_leaves_tree_and_index_identical_on_pass(tmp_path):
    repo = begun(tmp_path)
    good_step(repo)
    before = tree_state(repo)
    assert next_auto.check_step(repo, 1)[0] == 0
    assert tree_state(repo) == before
    assert worktree_count(repo) == 1 and no_scratch(repo)


def test_check_leaves_tree_and_index_identical_on_fail(tmp_path):
    repo = begun(tmp_path)
    good_step(repo)
    (repo / ".env").write_text("TOKEN=abc\n")
    before = tree_state(repo)
    assert next_auto.check_step(repo, 1)[0] == 1
    assert tree_state(repo) == before
    assert worktree_count(repo) == 1 and no_scratch(repo)


def test_check_leaves_tree_and_index_identical_when_the_runner_raises(tmp_path, monkeypatch):
    repo = begun(tmp_path)
    good_step(repo)
    git(repo, "add", "src.py")  # a partially staged index, to prove the restore is exact
    before = tree_state(repo)
    assert "src.py" in before[1]

    def boom(worktree, cmd):
        raise RuntimeError("runner exploded")

    monkeypatch.setattr(next_auto, "run_check", boom)
    with pytest.raises(RuntimeError):
        next_auto.check_step(repo, 1)
    assert tree_state(repo) == before
    assert worktree_count(repo) == 1 and no_scratch(repo)


def test_check_counts_failures_and_removes_the_record_on_the_second(tmp_path):
    """ai-dlc-record R14: the Stop hook reads the second failure off the check record, so the
    record stays with failures at two; the brief goes."""
    repo = begun(tmp_path)
    good_step(repo)
    (repo / ".env").write_text("TOKEN=abc\n")
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert json.loads(attended_record(repo).read_text())["failures"] == 1
    assert attended_record(repo).exists()
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert lines[-1] == "CHECK FAIL step 1: second failure; stop the batch"
    assert json.loads(attended_record(repo).read_text())["failures"] == 2
    assert not (repo / ".next-auto" / "attended" / "brief-1.md").exists()


def test_check_pass_does_not_count_as_a_failure(tmp_path):
    repo = begun(tmp_path)
    good_step(repo)
    assert next_auto.check_step(repo, 1)[0] == 0
    assert json.loads(attended_record(repo).read_text())["failures"] == 0


def test_check_begin_removes_stale_records_of_other_steps(tmp_path):
    repo = begun(tmp_path)
    assert attended_record(repo, 1).exists()
    rc, _ = next_auto.check_begin(repo, 2, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0
    assert not attended_record(repo, 1).exists()
    assert not (repo / ".next-auto" / "attended" / "brief-1.md").exists()
    assert attended_record(repo, 2).exists()


# ---- review package without a run ---------------------------------------------


def test_review_package_with_base_renders_the_chunk_diff_without_state(tmp_path):
    repo, _ = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    good_step(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "feat(step-1): feature")
    assert next_auto.load_state(repo) is None
    path = next_auto.review_package(repo, 1, base=base)
    assert path == repo / ".next-auto" / "attended" / "review-1.md"
    text = path.read_text()
    assert "## Commits" in text and "feat(step-1): feature" in text
    assert "## Diff" in text and "+FEATURE = 42" in text
    assert "## Steps in this chunk" in text
    assert "### Step 1:" in text and "### Step 2:" in text
    assert not list((repo / ".next-auto").rglob("state.json*"))


def test_review_package_cli_runs_with_base_and_refuses_without(tmp_path, capsys):
    repo, _ = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    good_step(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "feat(step-1): feature")
    rc = next_auto.main(["--root", str(repo), "review-package", "1", "--base", base])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.startswith("REVIEW ") and out.out.strip().endswith("attended/review-1.md")
    rc = next_auto.main(["--root", str(repo), "review-package", "1"])
    out = capsys.readouterr()
    assert rc == 1 and out.err.startswith("NO ACTIVE RUN")


# ---- attended check: the fixes from the Chunk B review --------------------------


def test_check_begin_refuses_a_tree_with_changes_beyond_the_plan(tmp_path, capsys):
    repo, _ = make_repo(tmp_path)
    good_step(repo)
    rc = next_auto.main(["--root", str(repo), "check", "begin", "1"])
    err = capsys.readouterr().err
    assert rc == 1
    assert err.startswith("CHECK FAIL step 1: working tree has changes beyond prompt_plan.md")
    assert "src.py" in err and "tests/test_feature.py" in err
    assert not attended_record(repo).exists()


def test_check_begin_allows_only_the_plan_to_be_dirty(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "prompt_plan.md").write_text(PLAN.replace("Status: PENDING", "Status: IN PROGRESS", 1))
    rc, _msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0


def test_repeat_check_begin_after_a_second_failure_needs_a_clean_tree(tmp_path):
    repo = begun(tmp_path)
    good_step(repo)
    (repo / "tests" / "test_base.py").write_text(
        "import pytest\n\n\n@pytest.mark.skip\ndef test_base():\n    assert True\n"
    )
    assert next_auto.check_step(repo, 1)[0] == 1
    assert next_auto.check_step(repo, 1)[0] == 1
    assert json.loads(attended_record(repo).read_text())["failures"] == 2
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 1 and "changes beyond prompt_plan.md" in msg


def test_check_reports_a_git_failure_as_apparatus(tmp_path, monkeypatch):
    repo = begun(tmp_path)
    good_step(repo)

    def locked(worktree, index=None):
        raise subprocess.CalledProcessError(128, ["git", "add", "-A"], stderr="index.lock exists")

    monkeypatch.setattr(next_auto, "stage_all", locked)
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 2
    assert lines[0].startswith("CHECK APPARATUS step 1: ")


def test_check_nothing_staged_counts_as_a_failure(tmp_path):
    repo = begun(tmp_path)
    assert next_auto.check_step(repo, 1) == (
        1,
        ["CHECK FAIL step 1: nothing staged: the step produced no changes"],
    )
    assert json.loads(attended_record(repo).read_text())["failures"] == 1
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert lines[-1] == "CHECK FAIL step 1: second failure; stop the batch"
    assert json.loads(attended_record(repo).read_text())["failures"] == 2


def test_check_begin_reports_a_git_failure_as_apparatus(tmp_path, monkeypatch):
    repo, _ = make_repo(tmp_path)
    real_git = next_auto.git

    def locked(root, *args, **kwargs):
        if args[0] == "status":
            raise subprocess.CalledProcessError(
                128, ["git", "status"], stderr="fatal: index.lock exists"
            )
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(next_auto, "git", locked)
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 2
    assert msg.startswith("CHECK APPARATUS step 1: ")
    assert not attended_record(repo).exists()


def test_ensure_ignored_writes_the_common_exclude_from_a_linked_worktree(tmp_path):
    repo, _ = make_repo(tmp_path)
    wt = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
    assert (wt / ".git").is_file()
    assert next_auto.ensure_ignored(wt, ".next-auto/") is True
    common = repo / ".git" / "info" / "exclude"
    assert ".next-auto/" in common.read_text()
    assert git(wt, "check-ignore", "-q", ".next-auto/probe", check=False).returncode == 0


# ---- Step 12: attended commit through the script, no run state ------------------


def test_commit_with_no_run_commits_at_the_root_and_clears_the_record(tmp_path, capsys):
    """Requirement 18: every git operation of the attended loop goes through a subcommand
    invoked alone. With no run, `commit N` stages the tree, commits at the root, removes
    the step's attended record and brief, and writes no state.json."""
    repo, _ = make_repo(tmp_path)
    rc, _msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0
    (repo / "tests" / "test_new.py").write_text("def test_new():\n    assert True\n")
    (repo / "src.py").write_text("# Description: fixture\n# Description: two\nX = 2\n")
    rc = next_auto.main(["--root", str(repo), "commit", "1", "--title", "feat"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert out.out.strip() == f"COMMIT {sha}"
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == "feat(step-1): feat"
    assert git(repo, "status", "--porcelain").stdout.strip() == ""
    assert "tests/test_new.py" in git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert not attended_record(repo).exists()
    assert not (repo / ".next-auto" / "attended" / "brief-1.md").exists()
    assert not list((repo / ".next-auto").rglob("state.json*"))
    assert next_auto.load_state(repo) is None


def test_commit_with_no_run_takes_the_title_from_the_root_plan(tmp_path, capsys):
    repo, _ = make_repo(tmp_path)
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0] == 0
    (repo / "tests" / "test_new.py").write_text("def test_new():\n    assert True\n")
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    capsys.readouterr()
    assert rc == 0
    plan = next_plan.parse_plan((repo / "prompt_plan.md").read_text())
    title = next(s.title for s in plan.steps if s.number == 1)
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == f"feat(step-1): {title}"


def test_commit_stays_a_run_command_and_refuses_nothing_staged_without_a_run(tmp_path, capsys):
    """`commit` keeps its place in RUN_COMMANDS (the live-run path is unchanged); the
    run-less branch is the no-state case only. A clean tree with no run is one line."""
    assert "commit" in next_auto.RUN_COMMANDS
    repo, _ = make_repo(tmp_path)
    rc = next_auto.main(["--root", str(repo), "commit", "1", "--title", "feat"])
    out = capsys.readouterr()
    assert rc == 1
    assert out.err.strip() == "COMMIT REFUSED step 1: nothing to commit"
    assert "Traceback" not in out.err


# ---- sitting sixteen fix: the run-less commit fails closed, scans, and excludes -----------


def _install_pre_commit(repo, body):
    out = git(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks").stdout
    hooks = Path(out.strip())
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\n" + body)
    hook.chmod(0o755)
    return hook


def test_commit_step_retries_only_when_the_hook_modified_files(tmp_path):
    """A hook that refuses without touching the tree runs once; the retry is for a
    formatter that rewrote files, not a second chance for the same refusal."""
    repo, _ = make_repo(tmp_path)
    counter = tmp_path / "hook-runs"
    _install_pre_commit(
        repo, f'echo run >> "{counter}"\necho "commit refused by policy" >&2\nexit 1\n'
    )
    (repo / "src.py").write_text("# Description: fixture\n# Description: two\nX = 2\n")
    git(repo, "add", "-A")
    with pytest.raises(RuntimeError, match="commit failed for step 1"):
        next_auto.commit_step(repo, 1, "first")
    assert counter.read_text().count("run") == 1


def test_commit_with_no_run_reports_a_hook_refusal_in_one_line(tmp_path, capsys):
    repo, _ = make_repo(tmp_path)
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0] == 0
    good_step(repo)
    _install_pre_commit(repo, 'echo "commit refused by policy" >&2\nexit 1\n')
    before = git(repo, "rev-parse", "HEAD").stdout.strip()
    rc = next_auto.main(["--root", str(repo), "commit", "1", "--title", "feat"])
    out = capsys.readouterr()
    assert rc == 1
    assert out.err.count("\n") == 1
    assert out.err.startswith("COMMIT FAIL step 1: commit failed for step 1:")
    assert "commit refused by policy" in out.err
    assert "Traceback" not in out.err
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before
    assert attended_record(repo).exists(), "a failed commit keeps the step's record"


def test_commit_with_no_run_refuses_secret_shaped_staging(tmp_path, capsys):
    """The item 11 to 13 window (the plan note) is committed by `commit N` alone, so the
    staged-safe scan runs here too, after the add and before the commit."""
    repo, _ = make_repo(tmp_path)
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0] == 0
    good_step(repo)
    (repo / ".env").write_text("TOKEN=abc\n")
    before = git(repo, "rev-parse", "HEAD").stdout.strip()
    rc = next_auto.main(["--root", str(repo), "commit", "1", "--title", "feat"])
    out = capsys.readouterr()
    assert rc == 1
    assert out.err.strip() == "COMMIT REFUSED step 1: staged a secret-shaped file: .env"
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before
    assert attended_record(repo).exists()


def test_commit_with_no_run_excludes_the_run_dir_without_a_prior_check_begin(tmp_path, capsys):
    """A clone without the exclude (no check begin ran here) must not commit the record
    and brief and then unlink them, which would leave staged deletions behind."""
    repo, _ = make_repo(tmp_path)
    att = repo / ".next-auto" / "attended"
    att.mkdir(parents=True)
    (att / "step-1.json").write_text("{}")
    (att / "brief-1.md").write_text("brief")
    (repo / "src.py").write_text("# Description: fixture\n# Description: two\nX = 2\n")
    rc = next_auto.main(["--root", str(repo), "commit", "1", "--title", "feat"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert ".next-auto" not in git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert git(repo, "status", "--porcelain").stdout.strip() == ""
    assert git(repo, "check-ignore", "-q", ".next-auto/probe", check=False).returncode == 0


def test_commit_with_no_run_excludes_an_untracked_plan(tmp_path, capsys):
    """project-a and project-b keep the plan untracked; the attended commit treats it as preflight
    does, excluded and never staged, so the scan's untracked-plan refusal is not reached."""
    repo, _ = make_repo(tmp_path)
    git(repo, "rm", "--cached", "-q", "prompt_plan.md")
    git(repo, "commit", "-qm", "untrack the plan")
    (repo / "src.py").write_text("# Description: fixture\n# Description: two\nX = 2\n")
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert "prompt_plan.md" not in git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert (repo / "prompt_plan.md").is_file()
    assert git(repo, "status", "--porcelain").stdout.strip() == ""


def test_commit_with_no_run_reports_a_git_failure_as_apparatus(tmp_path, capsys, monkeypatch):
    repo, _ = make_repo(tmp_path)
    (repo / "src.py").write_text("# Description: fixture\n# Description: two\nX = 2\n")
    real_git = next_auto.git

    def locked(root, *args, **kwargs):
        if args[:2] == ("add", "-A"):
            raise subprocess.CalledProcessError(
                128, ["git", "add"], stderr="fatal: index.lock exists"
            )
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(next_auto, "git", locked)
    rc = next_auto.main(["--root", str(repo), "commit", "1", "--title", "feat"])
    out = capsys.readouterr()
    assert rc == 2
    assert out.err.startswith("COMMIT APPARATUS step 1: ")
    assert "Traceback" not in out.err


def test_commit_with_a_live_run_commits_in_the_worktree_and_records_state(tmp_path, capsys):
    """The run-less branch is keyed on no state.json; with a run live, `commit N` takes the
    worktree path and writes the step into state, never touching the root."""
    repo, wt = start_wt(tmp_path)
    root_before = git(repo, "rev-parse", "HEAD").stdout.strip()
    wt_before = git(wt, "rev-parse", "HEAD").stdout.strip()
    good_step(wt)
    rc = next_auto.main(["--root", str(repo), "commit", "1", "--title", "feat"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    wt_after = git(wt, "rev-parse", "HEAD").stdout.strip()
    assert wt_after != wt_before
    assert out.out.strip() == f"COMMIT {wt_after}"
    assert git(wt, "log", "-1", "--format=%s").stdout.strip() == "feat(step-1): feat"
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == root_before
    recorded = next_auto.load_state(repo)["steps"]["1"]
    assert recorded == {"state": "DONE", "note": f"commit {wt_after[:8]}"}


# ---- Step 14: `merge <n>` refuses on every failed condition ------------------------------

PR_VIEW_FIELDS = (
    "headRefOid,baseRefName,additions,deletions,files,reviewDecision,mergeStateStatus,isDraft"
)


MERGE_SHA = "e" * 40


def merge_gh(calls, pr, *, owner="ryanmat", missing=False, merge_rc=0, merge_err=""):
    """The gh seam for `merge`: `repo view` names the owner, `pr view` answers from `pr`
    (or the merge commit once asked for it), `pr merge` answers `merge_rc`. Every call is
    recorded so a test can assert on the exact argv the seam received."""

    def _gh(args, root=None):
        calls.append(list(args))
        if missing:
            return subprocess.CompletedProcess(
                ["gh", *args], 127, stdout="", stderr="gh: command not found on PATH"
            )
        if args[:2] == ["repo", "view"]:
            body = {"owner": {"login": owner}, "nameWithOwner": f"{owner}/x"}
            return subprocess.CompletedProcess(["gh", *args], 0, stdout=json.dumps(body), stderr="")
        if args[:2] == ["pr", "view"] and "mergeCommit" in args:
            body = {"mergeCommit": {"oid": MERGE_SHA}}
            return subprocess.CompletedProcess(["gh", *args], 0, stdout=json.dumps(body), stderr="")
        if args[:2] == ["pr", "view"]:
            return subprocess.CompletedProcess(["gh", *args], 0, stdout=json.dumps(pr), stderr="")
        if args[:2] == ["pr", "merge"]:
            return subprocess.CompletedProcess(["gh", *args], merge_rc, stdout="", stderr=merge_err)
        return subprocess.CompletedProcess(["gh", *args], 0, stdout="", stderr="")

    return _gh


def green_pr(sha, **overrides):
    pr = {
        "headRefOid": sha,
        "baseRefName": "main",
        "additions": 10,
        "deletions": 2,
        "files": [{"path": "src.py"}],
        "reviewDecision": "APPROVED",
        "mergeStateStatus": "CLEAN",
        "isDraft": False,
    }
    pr.update(overrides)
    return pr


def write_verdict(repo, sha, **overrides):
    record = {"head": sha, "verdict": "Approved", "reviewer": "adw:next-reviewer"}
    record.update(overrides)
    path = repo / ".next-auto" / "attended" / f"verdict-{sha}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))
    return path


def write_done(repo, sha, **overrides):
    """The done check's record as the session writes it (ai-dlc-record R12)."""
    record = {"head": sha, "verdict": "APPROVED", "reviewer": "done-check", "model": "opus"}
    record.update(overrides)
    path = repo / ".next-auto" / "attended" / f"done-{sha}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))
    return path


def merge_ready(tmp_path):
    """A repo whose HEAD carries every condition: a green suite, an Approved verdict record
    from the chunk reviewer, and a preflight note."""
    repo, _origin = make_repo(tmp_path)
    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    write_verdict(repo, sha)
    git(repo, "notes", "--ref=preflight", "add", "-m", "preflight PASS", sha)
    return repo, sha


def merge_kwargs(**extra):
    kw = {"repo": "ryanmat/x", "test_cmd": TEST_CMD, "lint_cmd": LINT_OK}
    kw.update(extra)
    return kw


MERGE_ARGV = ["pr", "merge", "7", "--repo", "ryanmat/x", "--squash", "--delete-branch"]


def test_merge_merges_with_the_exact_argv_and_prints_the_merge_sha(tmp_path, monkeypatch):
    """Every condition holds: one `gh pr merge` with exactly the squash-and-delete argv,
    never --auto and never --admin, then the merge commit read back (Step 15 replaces
    Step 14's MERGE GATE PASS line with the merge itself)."""
    repo, sha = merge_ready(tmp_path)
    calls = []
    monkeypatch.setattr(next_auto, "gh", merge_gh(calls, green_pr(sha)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert (rc, lines) == (0, [f"MERGED PR #7 {MERGE_SHA}"])
    merges = [c for c in calls if c[:2] == ["pr", "merge"]]
    assert merges == [MERGE_ARGV]
    assert not any("--auto" in c or "--admin" in c for c in calls)
    assert ["pr", "view", "7", "--repo", "ryanmat/x", "--json", PR_VIEW_FIELDS] in calls
    assert calls.index(MERGE_ARGV) < calls.index(
        ["pr", "view", "7", "--repo", "ryanmat/x", "--json", "mergeCommit"]
    )
    assert next_auto.load_state(repo) is None
    assert not list((repo / ".next-auto").rglob("state.json*"))
    assert "wt" not in git(repo, "worktree", "list").stdout


def test_merge_refuses_a_repo_the_owner_does_not_own(tmp_path, monkeypatch):
    """ai-dlc-record R16: the refusal names the allowed owners, sorted and comma-joined."""
    repo, sha = merge_ready(tmp_path)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha), owner="other-org"))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs(repo="other-org/x"))
    assert rc == 1
    assert lines == ["MERGE REFUSED PR #7: repo owner is 'other-org', not one of acme, ryanmat"]


def test_merge_refuses_a_red_suite_at_the_head_sha_and_leaves_no_worktree(tmp_path, monkeypatch):
    repo, _ = merge_ready(tmp_path)
    (repo / "tests" / "test_red.py").write_text("def test_red():\n    assert False\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "red at head")
    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    write_verdict(repo, sha)
    git(repo, "notes", "--ref=preflight", "add", "-m", "preflight PASS", sha)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert rc == 1
    assert len(lines) == 1 and lines[0].startswith(f"MERGE REFUSED PR #7: Test red at {sha}: ")
    assert "1 failed" in lines[0]
    listed = git(repo, "worktree", "list").stdout.strip().splitlines()
    assert len(listed) == 1, listed
    assert not list((repo / ".next-auto").glob("next-auto-*"))


def test_merge_refuses_a_red_lint_at_the_head_sha(tmp_path, monkeypatch):
    repo, sha = merge_ready(tmp_path)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha)))
    lint_red = [sys.executable, "-c", "print('E501 too long'); raise SystemExit(1)"]
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs(lint_cmd=lint_red))
    assert (rc, lines) == (1, [f"MERGE REFUSED PR #7: Lint red at {sha}: E501 too long"])


def test_merge_refuses_a_head_that_is_not_a_local_commit(tmp_path, monkeypatch):
    repo, _sha = merge_ready(tmp_path)
    ghost = "0" * 40
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(ghost)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert rc == 1
    assert f"MERGE REFUSED PR #7: head {ghost} is not a local commit" in lines


@pytest.mark.parametrize(
    "record, reason",
    [
        (
            None,
            "no readable verdict-<sha>.json or done-<sha>.json under .next-auto/attended/ "
            "for {short}..{short}",
        ),
        ({"head": "f" * 40}, "verdict record head {other} is not {sha}"),
        ({"verdict": "Changes requested"}, "verdict is 'Changes requested', not Approved"),
        (
            {"reviewer": "someone-else"},
            "verdict from 'someone-else', not adw:next-reviewer",
        ),
    ],
)
def test_merge_refuses_without_an_accepted_verdict_for_the_head(
    tmp_path, monkeypatch, record, reason
):
    repo, sha = merge_ready(tmp_path)
    verdict_path = repo / ".next-auto" / "attended" / f"verdict-{sha}.json"
    if record is None:
        verdict_path.unlink()
    else:
        write_verdict(repo, sha, **record)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    want = reason.format(sha=sha, other="f" * 40, short=sha[:8])
    assert (rc, lines) == (1, [f"MERGE REFUSED PR #7: {want}"])


def test_merge_accepts_the_done_check_verdict_in_upper_case(tmp_path, monkeypatch):
    """ai-dlc-record R12: the done check's APPROVED lives in its own done-<sha>.json."""
    repo, sha = merge_ready(tmp_path)
    (repo / ".next-auto" / "attended" / f"verdict-{sha}.json").unlink()
    write_done(repo, sha)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha)))
    assert next_auto.merge_pr(repo, 7, **merge_kwargs())[0] == 0


def test_merge_refuses_without_a_preflight_note(tmp_path, monkeypatch):
    repo, sha = merge_ready(tmp_path)
    git(repo, "notes", "--ref=preflight", "remove", sha)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert (rc, lines) == (1, [f"MERGE REFUSED PR #7: no preflight note on {sha}"])


def test_merge_refuses_a_diff_over_the_size_limit(tmp_path, monkeypatch):
    repo, sha = merge_ready(tmp_path)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha, additions=400, deletions=101)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert (rc, lines) == (1, ["MERGE REFUSED PR #7: PR diff 501 lines, over 500"])
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha, additions=400, deletions=100)))
    assert next_auto.merge_pr(repo, 7, **merge_kwargs())[0] == 0


@pytest.mark.parametrize(
    "field", [{"reviewDecision": "REVIEW_REQUIRED"}, {"mergeStateStatus": "BLOCKED"}]
)
def test_merge_refuses_when_the_ruleset_requires_an_approval(tmp_path, monkeypatch, field):
    repo, sha = merge_ready(tmp_path)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha, **field)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert (rc, lines) == (
        1,
        ["MERGE REFUSED PR #7: ruleset requires an approval; the owner merges PR #7 by number"],
    )


def test_merge_refuses_a_draft(tmp_path, monkeypatch):
    repo, sha = merge_ready(tmp_path)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha, isDraft=True)))
    assert next_auto.merge_pr(repo, 7, **merge_kwargs()) == (
        1,
        ["MERGE REFUSED PR #7: PR is a draft"],
    )


def test_merge_prints_one_line_per_failed_condition(tmp_path, monkeypatch):
    repo, sha = merge_ready(tmp_path)
    git(repo, "notes", "--ref=preflight", "remove", sha)
    pr = green_pr(sha, isDraft=True, additions=600, reviewDecision="REVIEW_REQUIRED")
    monkeypatch.setattr(next_auto, "gh", merge_gh([], pr))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert rc == 1
    assert [ln.split(": ", 1)[1] for ln in lines] == [
        f"no preflight note on {sha}",
        "PR diff 602 lines, over 500",
        "ruleset requires an approval; the owner merges PR #7 by number",
        "PR is a draft",
    ]
    assert all(ln.startswith("MERGE REFUSED PR #7: ") for ln in lines)


def test_merge_never_calls_the_merge_on_a_refusal(tmp_path, monkeypatch):
    repo, sha = merge_ready(tmp_path)
    calls = []
    monkeypatch.setattr(next_auto, "gh", merge_gh(calls, green_pr(sha, isDraft=True)))
    assert next_auto.merge_pr(repo, 7, **merge_kwargs())[0] == 1
    assert not any(c[:2] == ["pr", "merge"] for c in calls)


def test_merge_without_gh_on_path_is_apparatus(tmp_path, monkeypatch):
    repo, sha = merge_ready(tmp_path)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha), missing=True))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert (rc, lines) == (2, ["MERGE APPARATUS PR #7: gh: command not found on PATH"])


def test_merge_reports_a_failed_pr_view_as_apparatus(tmp_path, monkeypatch):
    """gh answered `repo view` and then could not answer `pr view` (no such PR, a network
    error): the gate cannot evaluate, so it is apparatus, never a pass or a refusal."""
    repo, sha = merge_ready(tmp_path)
    answering = merge_gh([], green_pr(sha))

    def _gh(args, root=None):
        if args[:2] == ["pr", "view"]:
            return subprocess.CompletedProcess(
                ["gh", *args], 1, stdout="", stderr="no pull requests found for 7"
            )
        return answering(args, root)

    monkeypatch.setattr(next_auto, "gh", _gh)
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert (rc, lines) == (2, ["MERGE APPARATUS PR #7: no pull requests found for 7"])


def test_merge_cli_runs_without_run_state_and_writes_none(tmp_path, monkeypatch, capsys):
    assert "merge" not in next_auto.RUN_COMMANDS
    repo, sha = merge_ready(tmp_path)
    calls = []
    monkeypatch.setattr(next_auto, "gh", merge_gh(calls, green_pr(sha)))
    # the CLI reads Test and Lint from the plan's Conventions; the fixture plan's Test is
    # a plain pytest, which the scratch worktree's interpreter must resolve
    monkeypatch.setattr(
        next_auto, "plan_commands", lambda worktree, plan_name: (TEST_CMD, LINT_OK)
    )
    rc = next_auto.main(["--root", str(repo), "merge", "7", "--repo", "ryanmat/x"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert out.out.strip() == f"MERGED PR #7 {MERGE_SHA}"
    assert next_auto.load_state(repo) is None
    assert not list((repo / ".next-auto").rglob("state.json*"))
    monkeypatch.setattr(next_auto, "gh", merge_gh(calls, green_pr(sha, isDraft=True)))
    rc = next_auto.main(["--root", str(repo), "merge", "7", "--repo", "ryanmat/x"])
    out = capsys.readouterr()
    assert rc == 1
    assert out.err.strip() == "MERGE REFUSED PR #7: PR is a draft"


def test_scratch_worktree_is_removed_on_both_exit_paths(tmp_path):
    repo, _ = make_repo(tmp_path)
    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    with next_auto.scratch_worktree(repo, sha) as wt:
        assert (wt / "src.py").is_file()
        assert git(wt, "rev-parse", "HEAD").stdout.strip() == sha
        assert str(wt).startswith(str(repo / ".next-auto"))
    assert not wt.exists()
    with pytest.raises(RuntimeError, match="boom"), next_auto.scratch_worktree(repo, sha) as wt2:
        raise RuntimeError("boom")
    assert not wt2.exists()
    assert len(git(repo, "worktree", "list").stdout.strip().splitlines()) == 1
    assert not list((repo / ".next-auto").glob("next-auto-*"))


# ---- Step 15: the security surface, the merge call, and the guard during a run -----------


def head_commit(repo, path, content):
    """A commit on top of HEAD adding `path`, carrying a verdict record and a preflight
    note so only the security surface can refuse it. origin/main stays where make_repo
    left it, so `origin/main...<sha>` is exactly this commit."""
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"touch {path}")
    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    write_verdict(repo, sha)
    git(repo, "notes", "--ref=preflight", "add", "-m", "preflight PASS", sha)
    return sha


def test_merge_reports_a_failed_merge_call(tmp_path, monkeypatch):
    repo, sha = merge_ready(tmp_path)
    seam = merge_gh(
        [], green_pr(sha), merge_rc=1, merge_err="GraphQL: Pull request is not mergeable"
    )
    monkeypatch.setattr(next_auto, "gh", seam)
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert (rc, lines) == (1, ["MERGE FAILED gh: GraphQL: Pull request is not mergeable"])


@pytest.mark.parametrize(
    "path",
    [
        "hooks/next-guard.py",
        "rulesets/x.json",
        ".claude/settings.local.json",
        "settings.json",
        ".github/workflows/ci.yml",
        ".pre-commit-config.yaml",
        ".env.local",
        "deploy.pem",
    ],
)
def test_merge_refuses_a_security_surface_path(tmp_path, monkeypatch, path):
    repo, sha = merge_ready(tmp_path)
    calls = []
    files = [{"path": "src.py"}, {"path": path}]
    monkeypatch.setattr(next_auto, "gh", merge_gh(calls, green_pr(sha, files=files)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert (rc, lines) == (1, [f"MERGE REFUSED PR #7: security surface: {path}"])
    assert not any(c[:2] == ["pr", "merge"] for c in calls)


@pytest.mark.parametrize(
    "path, line, symbol",
    [
        ("scripts/extra.py", "NEVER_AUTO = frozenset()", "NEVER_AUTO"),
        ("scripts/extra.py", "for name, rx in SECRET_PATTERNS:", "SECRET_PATTERNS"),
        ("evals/x.py", "hits = scan_secrets(text)", "scan_secrets"),
        ("docs/x.md", "the _scrub_paths helper", "_scrub_paths"),
        ("hooks/probe.py", "r = subprocess.run(cmd, timeout=5)", "timeout="),
        ("scripts/probe.py", "r = subprocess.run(cmd, timeout=30)", "timeout="),
    ],
)
def test_merge_refuses_a_security_surface_hunk(tmp_path, monkeypatch, path, line, symbol):
    repo, _ = merge_ready(tmp_path)
    sha = head_commit(repo, path, f"# Description: fixture\n# Description: two\n{line}\n")
    calls = []
    monkeypatch.setattr(next_auto, "gh", merge_gh(calls, green_pr(sha, files=[{"path": path}])))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert rc == 1
    assert f"MERGE REFUSED PR #7: security surface hunk: {path} {symbol}" in lines
    # a hooks/ path is the surface by path as well, so that case carries two lines
    assert len(lines) == (2 if path.startswith("hooks/") else 1), lines
    assert not any(c[:2] == ["pr", "merge"] for c in calls)


def test_merge_hunk_scan_reads_removed_lines_too(tmp_path, monkeypatch):
    """The range diff is the net change, so the base must hold the line: origin/main
    gains the file with NEVER_AUTO (the bare origin fetches it from the repo), and the PR
    head removes it."""
    repo, _ = merge_ready(tmp_path)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "extra.py").write_text(
        "# Description: f\n# Description: g\nNEVER_AUTO = 1\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add")
    git(tmp_path / "origin.git", "fetch", "-q", str(repo), "main:main")
    git(repo, "fetch", "-q", "origin")
    sha = head_commit(repo, "scripts/extra.py", "# Description: f\n# Description: g\n")
    assert "-NEVER_AUTO" in git(repo, "diff", f"origin/main...{sha}").stdout
    files = [{"path": "scripts/extra.py"}]
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha, files=files)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert rc == 1
    assert "MERGE REFUSED PR #7: security surface hunk: scripts/extra.py NEVER_AUTO" in lines


def test_merge_timer_lines_outside_hooks_and_scripts_are_not_the_surface(tmp_path, monkeypatch):
    repo, _ = merge_ready(tmp_path)
    body = "# Description: f\n# Description: g\nr = get(url, timeout=5)\n"
    sha = head_commit(repo, "src/client.py", body)
    calls = []
    files = [{"path": "src/client.py"}]
    monkeypatch.setattr(next_auto, "gh", merge_gh(calls, green_pr(sha, files=files)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert (rc, lines) == (0, [f"MERGED PR #7 {MERGE_SHA}"])


def test_merge_refuses_when_the_base_cannot_be_diffed(tmp_path, monkeypatch):
    """A base ref the clone does not carry means the hunk scan cannot run; that is a
    refusal with the reason, never a pass on an empty diff."""
    repo, sha = merge_ready(tmp_path)
    calls = []
    monkeypatch.setattr(next_auto, "gh", merge_gh(calls, green_pr(sha, baseRefName="nope")))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert rc == 1
    assert lines[0].startswith(f"MERGE REFUSED PR #7: could not diff origin/nope...{sha}: ")
    assert not any(c[:2] == ["pr", "merge"] for c in calls)


def test_merge_fetches_the_head_from_origin_before_judging_it(tmp_path, monkeypatch):
    """The PR head usually lives only on the remote. merge fetches that sha (one git
    operation) so the suite, lint, and the hunk scan run against it locally."""
    repo, _sha = merge_ready(tmp_path)
    origin = tmp_path / "origin.git"
    git(origin, "config", "uploadpack.allowReachableSHA1InWant", "true")
    scratch = tmp_path / "origin-wt"
    git(origin, "worktree", "add", "-q", str(scratch), "main")
    (scratch / "src.py").write_text("# Description: fixture\n# Description: two\nX = 3\n")
    git(scratch, "add", "-A")
    git(scratch, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "remote only")
    remote_sha = git(scratch, "rev-parse", "HEAD").stdout.strip()
    git(origin, "worktree", "remove", "--force", str(scratch))
    assert git(repo, "cat-file", "-e", remote_sha, check=False).returncode != 0
    write_verdict(repo, remote_sha)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(remote_sha)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert git(repo, "cat-file", "-e", remote_sha, check=False).returncode == 0
    assert (rc, lines) == (1, [f"MERGE REFUSED PR #7: no preflight note on {remote_sha}"])


# ---- ai-dlc-record: result files, verdicts, record commits, approval, owners (R11 to R17) ----
# The plan's Record line names the delivery-record appender and the record's slug. These tests
# name a stub appender committed into the fixture repo: it logs every call (cwd, argv, and the
# evidence and report it is handed), appends one line per append to docs/records/<slug>.md,
# and exits with the code a test writes to `<subcommand>-rc` in its control directory; a
# `<subcommand>-quiet` file there keeps it off stderr when it exits non-zero, `append-silent`
# makes an append write nothing, and `approved-stdout` replaces the plan hash it prints. A
# `sync` appends `- stub sync` unless `sync-silent` is there.

PLUGIN = Path(next_auto.__file__).resolve().parents[1]
RECORD_SLUG = "demo"
RECORD_FILE = f"docs/records/{RECORD_SLUG}.md"
RECORD_HEADER = "# Delivery record: demo\n\n## Events\n\n"
RECORD_PLAN = PLAN.replace(
    "- Run: uv run python -m fixturepkg",
    f"- Run: uv run python -m fixturepkg\n- Record: tools/record-stub {RECORD_SLUG}",
)
# a report in the reviewer's form, each finding starting with file:line and quoting src.py, which
# every fixture repository holds at its head (adw 3.5.0 spec item 12)
REPORT = (
    "### Findings\n#### Critical\n- src.py:3 the guard is inverted\n  > X = 1\n"
    "#### Important\nNone\n"
    "#### Minor\n- src.py:1 a name reads oddly\n  > # Description: fixture\n\n"
    "### Verdict\nStep quality: NeedsFixes\n"
)
STUB = """\
#!@PYTHON@
# Description: stub appender for the engine's record tests: logs each call's cwd, argv and own
# Description: path with the files it names, appends a line to the record, exits as told.
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "@SCRIPTS@")
import next_plan

control = Path("@CONTROL@")
args = sys.argv[1:]
beside = Path(sys.argv[0]).resolve().parent.parent / "node_modules"
call = {"cwd": os.getcwd(), "argv": args, "script": sys.argv[0], "deps": beside.is_dir()}
if "--evidence" in args:
    call["evidence"] = json.loads(Path(args[args.index("--evidence") + 1]).read_text())
if "--report" in args:
    call["report"] = Path(args[args.index("--report") + 1]).read_text()
with (control / "calls.jsonl").open("a") as fh:
    fh.write(json.dumps(call) + "\\n")
rc_file = control / (args[0] + "-rc")
rc = int(rc_file.read_text()) if rc_file.exists() else 0
if args[0] == "approved":
    override = control / "approved-stdout"
    plan = Path(args[args.index("--plan") + 1]).read_bytes().decode("utf-8")
    print(override.read_text() if override.exists() else next_plan.plan_hash(plan))
if rc:
    if not (control / (args[0] + "-quiet")).exists():
        print("stub " + args[0] + " refused", file=sys.stderr)
elif args[0] == "append" and not (control / "append-silent").exists():
    evidence = call.get("evidence", {})
    names = (
        evidence.get("sha") or evidence.get("head") or evidence.get("failure")
        or args[args.index("--head" if "--head" in args else "--by") + 1]
    )
    with open("docs/records/" + args[1] + ".md", "a") as fh:
        fh.write("- stub " + args[2] + " " + names + "\\n")
elif args[0] == "sync" and not (control / "sync-silent").exists():
    with open("docs/records/" + args[1] + ".md", "a") as fh:
        fh.write("- stub sync\\n")
raise SystemExit(rc)
"""


def head_sha(tree):
    return git(tree, "rev-parse", "HEAD").stdout.strip()


def approval_line(plan_text, by="ryanmat"):
    """A plan.approved line for the plan's hash, in the shape of
    docs/records/agent-registration.md in acme/platform at ff5e721."""
    return f"- 2026-10-08T21:28:03Z plan.approved plan={next_plan.plan_hash(plan_text)} by={by}\n"


def record_repo(tmp_path, plan_text=RECORD_PLAN, approved=False):
    """make_repo plus a plan carrying Record, the stub appender it names, and the record,
    committed and pushed so a run's worktree has them too; with `approved` the record holds
    a plan.approved line for the plan, as wrap-up reads it. Returns (repo, control)."""
    repo, _origin = make_repo(tmp_path)
    control = tmp_path / "stub"
    control.mkdir()
    stub = repo / "tools" / "record-stub"
    stub.parent.mkdir()
    body = STUB.replace("@PYTHON@", sys.executable).replace("@CONTROL@", str(control))
    stub.write_text(body.replace("@SCRIPTS@", str(PLUGIN / "scripts")))
    stub.chmod(0o755)
    (repo / RECORD_FILE).parent.mkdir(parents=True)
    (repo / RECORD_FILE).write_text(
        RECORD_HEADER + (approval_line(plan_text) if approved else "")
    )
    (repo / "prompt_plan.md").write_text(plan_text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "record fixture")
    git(repo, "push", "-q", "origin", "main")
    # a clone has origin/HEAD; the engine runs the appender from that branch (R12)
    git(repo, "remote", "set-head", "origin", "main")
    return repo, control


def record_run(tmp_path, approved=False):
    repo, control = record_repo(tmp_path, approved=approved)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260924-rec123", sync=False)
    return repo, Path(state["worktree"]), control


def stub_calls(control):
    path = control / "calls.jsonl"
    return [json.loads(ln) for ln in path.read_text().splitlines()] if path.exists() else []


def appends(control):
    return [c for c in stub_calls(control) if c["argv"][0] == "append"]


def stub_rc(control, subcommand, rc):
    (control / f"{subcommand}-rc").write_text(str(rc))


def attended_pass(repo, n=1, step=good_step):
    """check begin N, the step's edits, and a passing check N at the root."""
    rc, msg = next_auto.check_begin(repo, n, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0, msg
    step(repo)
    assert next_auto.check_step(repo, n) == (0, [f"CHECK PASS step {n}"])


def set_done(tree, n, note):
    plan = tree / "prompt_plan.md"
    plan.write_bytes(next_plan.set_status_bytes(plan.read_bytes(), n, "done", note))


def attended_result(repo, n=1):
    return repo / ".next-auto" / "attended" / f"result-{n}.json"


def plugin_copy(tmp_path, agent, model, name="adwx"):
    """The plugin manifest and agent definitions under tmp_path, with one agent's model line
    and the plugin name changed, so a test proves the identity is read from the files."""
    root = tmp_path / "plugin"
    shutil.copytree(PLUGIN / ".claude-plugin", root / ".claude-plugin")
    shutil.copytree(PLUGIN / "agents", root / "agents")
    manifest = root / ".claude-plugin" / "plugin.json"
    manifest.write_text(json.dumps({**json.loads(manifest.read_text()), "name": name}))
    definition = root / "agents" / f"{agent}.md"
    text = definition.read_text()
    assert "\nmodel: opus\n" in text
    definition.write_text(text.replace("\nmodel: opus\n", f"\nmodel: {model}\n", 1))
    return root


def without_tree(result):
    """A result file less its `tree` hash, after checking the hash's form."""
    assert re.fullmatch(r"[0-9a-f]{64}", result["tree"]), result
    return {k: v for k, v in result.items() if k != "tree"}


def verdict_argv(repo, n, sha, round_, verdict, report):
    return [
        "--root", str(repo), "verdict", str(n), "--head", sha, "--round", str(round_),
        "--verdict", verdict, "--report", str(report),
    ]  # fmt: skip


# -- R11: the result file from check and gate, completed by commit --------------------------


def test_r11_check_writes_the_result_file_on_a_pass(tmp_path):
    """ai-dlc-record R11: a passing check N writes result-N.json with the step, the attended
    mode, the step's base, the suite's passed and collected counts, prove-red and lint."""
    repo = begun(tmp_path)
    base = head_sha(repo)
    good_step(repo)
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    assert without_tree(json.loads(attended_result(repo).read_text())) == {
        "step": 1,
        "mode": "attended",
        "head": base,
        "passed": 2,
        "collected": 2,
        "red": "proved",
        "lint": "ok",
        "size_estimate": 20,
        "size_changed": 6,
    }


# a Lint that prints ruff's rule line and exits 1, and one whose program does not exist
LINT_RED = [sys.executable, "-c", "print('E501 line too long'); raise SystemExit(1)"]
LINT_MISSING = ["no-such-lint-xyz-123"]
LINT_MISSING_LINE = (
    "Lint unavailable: [Errno 2] No such file or directory: 'no-such-lint-xyz-123'"
)


def test_check_fails_a_step_whose_lint_is_red(tmp_path):
    """check N judges Lint as the gate does: a red Lint is a red attempt, counted, and leaves
    no result file."""
    repo, _ = make_repo(tmp_path)
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_RED)[0] == 0
    good_step(repo)
    assert next_auto.check_step(repo, 1) == (
        1, ["CHECK FAIL step 1: Lint failed: E501 line too long"]
    )  # fmt: skip
    assert json.loads(attended_record(repo).read_text())["failures"] == 1
    assert not attended_result(repo).exists()


def test_check_reports_a_lint_that_cannot_start_as_apparatus(tmp_path):
    repo, _ = make_repo(tmp_path)
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_MISSING)[0] == 0
    good_step(repo)
    assert next_auto.check_step(repo, 1) == (2, [f"CHECK APPARATUS step 1: {LINT_MISSING_LINE}"])
    assert json.loads(attended_record(repo).read_text())["failures"] == 0
    assert not attended_result(repo).exists()


def test_r11_a_failing_check_removes_the_steps_result_file(tmp_path):
    """ai-dlc-record R11: the result file is written on a pass only, and a failing check N
    removes an earlier pass so a stale one can never reach the record."""
    repo = begun(tmp_path)
    good_step(repo)
    assert next_auto.check_step(repo, 1)[0] == 0
    assert attended_result(repo).is_file()
    (repo / ".env").write_text("TOKEN=abc\n")
    assert next_auto.check_step(repo, 1)[0] == 1
    assert not attended_result(repo).exists()


def test_r11_check_begin_removes_the_steps_stale_result_file(tmp_path):
    repo, _ = make_repo(tmp_path)
    attended_result(repo).parent.mkdir(parents=True)
    attended_result(repo).write_text('{"step": 1}')
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0] == 0
    assert not attended_result(repo).exists()


def test_r11_gate_writes_the_result_file_with_the_implementer_identity(tmp_path):
    """ai-dlc-record R11: a passing gate K writes <run-dir>/result-K.json in auto mode with the
    implementer definition's `<plugin>:<name>` and model line."""
    repo, wt = start_wt(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK) == (True, "gate passed")
    assert without_tree(json.loads((Path(state["run_dir"]) / "result-1.json").read_text())) == {
        "step": 1,
        "mode": "auto",
        "head": state["base_head"],
        "passed": 2,
        "collected": 2,
        "red": "proved",
        "lint": "ok",
        "size_estimate": 20,
        # the step's six lines plus the worktree plan's status edit, which the gate's size
        # cap counts too
        "size_changed": 8,
        "agent": "adw:next-implementer",
        "model": "opus",
    }


def test_r11_gate_reads_the_implementer_identity_from_its_definition(tmp_path, monkeypatch):
    plugin = plugin_copy(tmp_path, "next-implementer", "sonnet")
    monkeypatch.setattr(next_auto, "PLUGIN_ROOT", plugin)
    repo, wt = start_wt(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    result = json.loads((Path(state["run_dir"]) / "result-1.json").read_text())
    assert (result["agent"], result["model"]) == ("adwx:next-implementer", "sonnet")


def test_r11_a_failing_gate_removes_the_steps_result_file(tmp_path):
    repo, wt = start_wt(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    result = Path(state["run_dir"]) / "result-1.json"
    assert result.is_file()
    (wt / ".env").write_text("TOKEN=abc\n")
    assert not next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    assert not result.exists()


@pytest.mark.parametrize(
    "mutation_line, note, value",
    [
        ("Mutation: required", "2 tests green, mutation killed both ways, CHECK PASS", "reported"),
        ("Mutation: required", "2 tests green, CHECK PASS", "missing"),
        ("Mutation: n/a (no guard to break)", "2 tests green, CHECK PASS", "n/a"),
        ("Mutation: n/a (no guard to break)", "2 tests green, mutation killed, CHECK PASS", "n/a"),
    ],
)
def test_r11_commit_completes_the_result_file_with_the_sha_and_the_mutation_value(
    tmp_path, capsys, mutation_line, note, value
):
    """ai-dlc-record R11, R17: commit N adds its sha and the mutation value read from the plan
    step as it stands, with or without Record; this plan carries none, so nothing is appended."""
    plan_text = PLAN.replace("Mutation: required", mutation_line, 1)
    repo = begun(tmp_path, plan_text=None if plan_text == PLAN else plan_text)
    base = head_sha(repo)
    good_step(repo)
    assert next_auto.check_step(repo, 1)[0] == 0
    set_done(repo, 1, note)
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    sha = head_sha(repo)
    assert capsys.readouterr().out.strip() == f"COMMIT {sha}"
    result = json.loads(attended_result(repo).read_text())
    assert result["sha"] == sha and result["head"] == base
    assert result["mutation"] == value


# -- R11: the reviewer's verdict and report, every round -------------------------------------


def test_r11_verdict_writes_the_round_and_keeps_the_report_on_a_needs_fixes_round(
    tmp_path, capsys
):
    """ai-dlc-record R11, R17: every round, Approved or not, writes verdict-<sha>.json with the
    reviewer definition's identity and model and keeps the report at review-<sha>-<round>.md."""
    repo, _ = make_repo(tmp_path)
    sha = head_sha(repo)
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    rc = next_auto.main(verdict_argv(repo, 2, sha, 1, "NeedsFixes", report))
    out = capsys.readouterr()
    assert rc == 0, out.err
    attended = repo / ".next-auto" / "attended"
    verdict = attended / f"verdict-{sha}.json"
    assert out.out.strip() == f"VERDICT NeedsFixes {verdict}"
    assert json.loads(verdict.read_text()) == {
        "head": sha,
        "verdict": "NeedsFixes",
        "reviewer": "adw:next-reviewer",
        "model": "opus",
        "round": 1,
    }
    assert (attended / f"review-{sha}-1.md").read_text() == REPORT
    assert next_auto.load_state(repo) is None


def test_r11_verdict_reads_the_reviewer_identity_from_its_definition(tmp_path, monkeypatch):
    plugin = plugin_copy(tmp_path, "next-reviewer", "haiku")
    monkeypatch.setattr(next_auto, "PLUGIN_ROOT", plugin)
    repo, _ = make_repo(tmp_path)
    sha = head_sha(repo)
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    assert next_auto.main(verdict_argv(repo, 1, sha, 1, "Approved", report)) == 0
    verdict = json.loads((repo / ".next-auto" / "attended" / f"verdict-{sha}.json").read_text())
    assert (verdict["reviewer"], verdict["model"]) == ("adwx:next-reviewer", "haiku")


def test_r11_verdict_in_a_run_writes_step_and_round_named_files(tmp_path, capsys):
    """ai-dlc-record R11: in a run the files are <run-dir>/verdict-K-<round>.json and
    review-K-<round>.md, and nothing is written under .next-auto/attended/."""
    repo, wt = start_wt(tmp_path)
    sha = head_sha(wt)
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    assert next_auto.main(verdict_argv(repo, 1, sha, 2, "Approved", report)) == 0
    capsys.readouterr()
    run_dir = repo / ".next-auto" / "20260911-abc123"
    verdict = json.loads((run_dir / "verdict-1-2.json").read_text())
    assert verdict == {
        "head": sha,
        "verdict": "Approved",
        "reviewer": "adw:next-reviewer",
        "model": "opus",
        "round": 2,
    }
    assert (run_dir / "review-1-2.md").read_text() == REPORT
    assert not (repo / ".next-auto" / "attended").exists()


def test_r11_verdict_refuses_a_head_that_is_not_a_commit_sha(tmp_path, capsys):
    repo, _ = make_repo(tmp_path)
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    # seven hex characters that name no commit: the repository, not the shape, refuses it
    rc = next_auto.main(verdict_argv(repo, 1, "abc1234", 1, "Approved", report))
    assert rc == 1
    err = capsys.readouterr().err
    assert err.strip() == "VERDICT REFUSED step 1: abc1234 is not a commit of this repository"


def test_r11_merge_still_refuses_a_needs_fixes_round(tmp_path, monkeypatch, capsys):
    """ai-dlc-record R11: the verdict file is now written every round; the merge gate still
    accepts only Approved."""
    repo, sha = merge_ready(tmp_path)
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    assert next_auto.main(verdict_argv(repo, 1, sha, 1, "NeedsFixes", report)) == 0
    capsys.readouterr()
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert (rc, lines) == (1, ["MERGE REFUSED PR #7: verdict is 'NeedsFixes', not Approved"])


# -- R12: the four call points, with and without Record ---------------------------------------


def test_r12_attended_commit_appends_step_done_from_the_completed_result_file(tmp_path, capsys):
    """ai-dlc-record R12, R17: with Record, commit N calls `<command> append <slug> step.done
    --evidence <result file>` from the root after its commit; the line stays uncommitted."""
    repo, control = record_repo(tmp_path)
    base = head_sha(repo)
    attended_pass(repo)
    set_done(repo, 1, "2 tests green, mutation killed both ways, CHECK PASS")
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    sha = head_sha(repo)
    assert out.out.strip() == f"COMMIT {sha}"
    [call] = appends(control)
    assert Path(call["cwd"]) == repo.resolve()
    evidence = str(attended_result(repo))
    assert call["argv"] == ["append", RECORD_SLUG, "step.done", "--evidence", evidence]
    assert without_tree(call["evidence"]) == {
        "step": 1,
        "mode": "attended",
        "head": base,
        "passed": 2,
        "collected": 2,
        "red": "proved",
        "lint": "ok",
        "size_estimate": 20,
        "size_changed": 6,
        "sha": sha,
        "mutation": "reported",
        "plan": next_plan.plan_hash(RECORD_PLAN),
        "runkey": ATTENDED_SESSION_ID,
    }
    assert git(repo, "show", f"HEAD:{RECORD_FILE}").stdout == RECORD_HEADER
    assert git(repo, "status", "--porcelain").stdout == f" M {RECORD_FILE}\n"


def test_r12_the_step_done_line_rides_in_the_next_step_commit(tmp_path, capsys):
    """ai-dlc-record R12, R17: the pending line is not dirt to check begin N+1, and the next
    step's commit carries it; each step's evidence reads its own mutation value."""
    repo, control = record_repo(tmp_path)
    attended_pass(repo, 1)
    set_done(repo, 1, "2 tests green, mutation killed both ways, CHECK PASS")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    first = head_sha(repo)
    attended_pass(repo, 2, second_step)
    set_done(repo, 2, "3 tests green, CHECK PASS")
    assert next_auto.main(["--root", str(repo), "commit", "2"]) == 0
    capsys.readouterr()
    second = head_sha(repo)
    assert f"- stub step.done {first}\n" in git(repo, "show", f"HEAD:{RECORD_FILE}").stdout
    assert f"- stub step.done {first}\n" not in git(repo, "show", f"{first}:{RECORD_FILE}").stdout
    assert (repo / RECORD_FILE).read_text().endswith(f"- stub step.done {second}\n")
    evidence = [c["evidence"] for c in appends(control)]
    assert [(e["step"], e["sha"], e["mutation"]) for e in evidence] == [
        (1, first, "reported"),
        (2, second, "n/a"),
    ]


def test_r12_check_begin_treats_the_record_as_it_treats_the_plan(tmp_path, capsys):
    """ai-dlc-record R12: a pending record line is not dirt; anything else still is."""
    repo, _control = record_repo(tmp_path)
    with (repo / RECORD_FILE).open("a") as fh:
        fh.write("- a pending line\n")
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0] == 0
    (repo / "notes.txt").write_text("stray\n")
    rc = next_auto.main(["--root", str(repo), "check", "begin", "1"])
    err = capsys.readouterr().err
    assert rc == 1
    assert err.startswith(
        f"CHECK FAIL step 1: working tree has changes beyond prompt_plan.md and {RECORD_FILE} "
        "(notes.txt)"
    )


def test_r12_without_record_the_record_directory_is_dirt_to_check_begin(tmp_path):
    """ai-dlc-record R12: a plan with no Record changes nothing."""
    repo, _ = make_repo(tmp_path)
    (repo / "docs" / "records").mkdir(parents=True)
    (repo / RECORD_FILE).write_text(RECORD_HEADER)
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 1 and "beyond prompt_plan.md (docs/" in msg


def test_r12_attended_commit_refuses_before_committing_without_a_result_file(tmp_path, capsys):
    repo, control = record_repo(tmp_path)
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0] == 0
    good_step(repo)
    before = head_sha(repo)
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    err = capsys.readouterr().err
    assert rc == 1
    assert err.strip() == "COMMIT REFUSED step 1: no result file for step 1; run check 1 first"
    assert head_sha(repo) == before
    assert appends(control) == []


def test_r12_commit_reports_a_failed_append_on_one_line_and_keeps_the_commit(tmp_path, capsys):
    """ai-dlc-record R12: the commit stands when the append fails after it; one line, exit 2."""
    repo, control = record_repo(tmp_path)
    attended_pass(repo)
    set_done(repo, 1, "2 tests green, CHECK PASS")
    stub_rc(control, "append", 1)
    before = head_sha(repo)
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    err = capsys.readouterr().err
    sha = head_sha(repo)
    assert rc == 2 and sha != before
    assert err.count("\n") == 1
    assert err.strip() == f"COMMIT {sha} RECORD FAILED step 1: exited 1: stub append refused"
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == "feat(step-1): first"


def test_r12_run_commit_appends_step_done_from_the_worktree(tmp_path, capsys):
    """ai-dlc-record R11, R12: in a run, commit K completes the gate's result file from the
    worktree plan and appends step.done from the worktree with the implementer's identity."""
    repo, wt, control = record_run(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK) == (True, "gate passed")
    next_auto.set_step(repo, 1, "done", "2 tests green, mutation killed both ways")
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    sha = head_sha(wt)
    result = Path(state["run_dir"]) / "result-1.json"
    [call] = appends(control)
    assert Path(call["cwd"]) == wt.resolve()
    assert call["argv"] == ["append", RECORD_SLUG, "step.done", "--evidence", str(result)]
    assert without_tree(call["evidence"]) == {
        "step": 1,
        "mode": "auto",
        "head": state["base_head"],
        "passed": 2,
        "collected": 2,
        "red": "proved",
        "lint": "ok",
        "size_estimate": 20,
        "size_changed": 8,
        "agent": "adw:next-implementer",
        "model": "opus",
        "sha": sha,
        "mutation": "reported",
        "plan": next_plan.plan_hash(RECORD_PLAN),
        "runkey": ATTENDED_SESSION_ID,
    }
    assert (wt / RECORD_FILE).read_text().endswith(f"- stub step.done {sha}\n")
    assert (repo / RECORD_FILE).read_text() == RECORD_HEADER


def test_r12_run_commit_refuses_before_committing_without_the_gates_result_file(tmp_path, capsys):
    repo, wt, control = record_run(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    next_auto.stage_all(wt)
    before = head_sha(wt)
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    err = capsys.readouterr().err
    assert rc == 1
    assert err.strip() == "COMMIT REFUSED step 1: no result file for step 1; run gate 1 first"
    assert head_sha(wt) == before and appends(control) == []


def test_r12_verdict_appends_review_verdict_from_the_verdict_file_and_the_report(tmp_path, capsys):
    """ai-dlc-record R12, R17: the boundary's verdict call appends review.verdict from the root
    with --evidence and --report naming the two files it just wrote."""
    repo, control = record_repo(tmp_path)
    sha = head_sha(repo)
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    assert next_auto.main(verdict_argv(repo, 2, sha, 1, "NeedsFixes", report)) == 0
    capsys.readouterr()
    attended = repo / ".next-auto" / "attended"
    [call] = appends(control)
    assert Path(call["cwd"]) == repo.resolve()
    assert call["argv"] == [
        "append", RECORD_SLUG, "review.verdict",
        "--evidence", str(attended / f"verdict-{sha}.json"),
        "--report", str(attended / f"review-{sha}-1.md"),
    ]  # fmt: skip
    assert call["evidence"]["verdict"] == "NeedsFixes" and call["evidence"]["model"] == "opus"
    assert call["report"] == REPORT
    assert (repo / RECORD_FILE).read_text().endswith(f"- stub review.verdict {sha}\n")


def test_r12_without_record_commit_and_verdict_call_no_appender(tmp_path, capsys):
    """ai-dlc-record R12, R17: the same calls on a plan without Record append nothing."""
    repo, control = record_repo(tmp_path, plan_text=PLAN)
    attended_pass(repo)
    set_done(repo, 1, "2 tests green, CHECK PASS")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    sha = head_sha(repo)
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    assert next_auto.main(verdict_argv(repo, 1, sha, 1, "Approved", report)) == 0
    capsys.readouterr()
    assert stub_calls(control) == []
    assert (repo / RECORD_FILE).read_text() == RECORD_HEADER
    assert git(repo, "status", "--porcelain").stdout == ""


def test_r12_record_commit_commits_the_record_alone(tmp_path, capsys):
    """ai-dlc-record R12: `record-commit` commits only docs/records/<slug>.md as
    `chore(record): <slug>`, leaves other changes where they were, and commits nothing when
    the record has no change."""
    repo, _control = record_repo(tmp_path)
    with (repo / RECORD_FILE).open("a") as fh:
        fh.write("- a pending line\n")
    (repo / "src.py").write_text("# Description: fixture\n# Description: two\nX = 5\n")
    git(repo, "add", "src.py")
    rc = next_auto.main(["--root", str(repo), "record-commit"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    sha = head_sha(repo)
    assert out.out.strip() == f"RECORD COMMIT {sha}"
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == f"chore(record): {RECORD_SLUG}"
    assert git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split() == [RECORD_FILE]
    assert git(repo, "status", "--porcelain").stdout == "M  src.py\n"
    rc = next_auto.main(["--root", str(repo), "record-commit"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == f"RECORD UNCHANGED {RECORD_FILE}"
    assert head_sha(repo) == sha


def test_r12_record_commit_in_a_run_commits_in_the_worktree(tmp_path, capsys):
    repo, wt, _control = record_run(tmp_path)
    root_head = head_sha(repo)
    with (wt / RECORD_FILE).open("a") as fh:
        fh.write("- a pending line\n")
    assert next_auto.main(["--root", str(repo), "record-commit"]) == 0
    assert capsys.readouterr().out.strip() == f"RECORD COMMIT {head_sha(wt)}"
    assert git(wt, "log", "-1", "--format=%s").stdout.strip() == f"chore(record): {RECORD_SLUG}"
    assert head_sha(repo) == root_head


def test_r12_record_commit_refuses_a_plan_without_record(tmp_path, capsys):
    repo, _ = make_repo(tmp_path)
    rc = next_auto.main(["--root", str(repo), "record-commit"])
    assert rc == 1
    assert capsys.readouterr().err.strip() == "RECORD REFUSED: prompt_plan.md carries no Record"


def gh_seeing_the_pushed_head(calls, origin, branch):
    """The wrap-up gh seam, private repo; `pr create` also notes the branch tip on origin at
    the moment the PR opens, so a test can compare it with the head pr.opened names."""

    def _gh(args, root=None):
        calls.append(list(args))
        if args[:2] == ["repo", "view"]:
            body = json.dumps({"isPrivate": True})
            return subprocess.CompletedProcess(["gh", *args], 0, stdout=body, stderr="")
        if args[:2] == ["pr", "create"]:
            tip = git(origin, "rev-parse", f"refs/heads/{branch}").stdout.strip()
            calls.append(["pushed-head", tip])
        url = "https://github.com/ryanmat/fixture/pull/7\n"
        return subprocess.CompletedProcess(["gh", *args], 0, stdout=url, stderr="")

    return _gh


def test_r12_wrap_up_records_pr_opened_then_commits_the_record_and_pushes_again(
    tmp_path, monkeypatch
):
    """ai-dlc-record R12, R17: once the PR exists, wrap-up appends pr.opened with the head the
    PR opened on and the night session's run key, commits the record alone, and pushes that
    commit."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", NIGHT_SESSION_ID)
    repo, wt, control = record_run(tmp_path, approved=True)
    branch = next_auto.load_state(repo)["branch"]
    run_step_one(repo, wt)
    next_auto.set_step(repo, 2, "done", "b")
    origin = tmp_path / "origin.git"
    calls = []
    monkeypatch.setattr(next_auto, "gh", gh_seeing_the_pushed_head(calls, origin, branch))
    result = next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    assert result["record"] is None and result["pr_number"] == 7
    [opened_on] = [c[1] for c in calls if c[0] == "pushed-head"]
    tip = git(origin, "rev-parse", f"refs/heads/{branch}").stdout.strip()
    assert git(wt, "rev-parse", f"{tip}^").stdout.strip() == opened_on
    subject = git(wt, "log", "-1", "--format=%s", tip).stdout.strip()
    assert subject == f"chore(record): {RECORD_SLUG}"
    assert git(wt, "show", "--name-only", "--format=", tip).stdout.split() == [RECORD_FILE]
    [call] = [c for c in appends(control) if c["argv"][2] == "pr.opened"]
    assert Path(call["cwd"]) == wt.resolve()
    assert call["argv"] == [
        "append", RECORD_SLUG, "pr.opened", "--pr", "7", "--head", opened_on, "--mode", "auto",
        "--runkey", NIGHT_SESSION_ID,
    ]  # fmt: skip
    assert f"- stub pr.opened {opened_on}\n" in git(wt, "show", f"{tip}:{RECORD_FILE}").stdout
    assert next_auto.find_state_file(repo) is None


def test_r12_wrap_up_without_record_pushes_once_and_makes_no_record_commit(tmp_path, monkeypatch):
    repo, origin = make_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260924-plain1", sync=False)
    next_auto.set_step(repo, 1, "done", "a")
    monkeypatch.setattr(next_auto, "gh", fake_gh_visibility([], private=True))
    result = next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    assert result["record"] is None
    tip = git(origin, "rev-parse", f"refs/heads/{state['branch']}").stdout.strip()
    subject = git(repo, "log", "-1", "--format=%s", tip).stdout.strip()
    assert subject.startswith("chore(plan): record run")


def test_r12_wrap_up_reports_a_failed_pr_opened_append_and_still_disarms(
    tmp_path, monkeypatch, capsys
):
    """ai-dlc-record R12: the PR exists by then, so a record failure is reported with exit 2
    after RUN STOPPED and the run ends; running wrap-up again would open a second PR."""
    repo, wt, control = record_run(tmp_path, approved=True)
    run_step_one(repo, wt)
    stub_rc(control, "append", 1)
    monkeypatch.setattr(next_auto, "gh", fake_gh([]))
    monkeypatch.setattr(next_auto, "origin_repo", lambda _root: "ryanmat/fixture")
    rc = next_auto.main(["--root", str(repo), "wrap-up"])
    out = capsys.readouterr()
    assert rc == 2
    assert "RUN STOPPED" in out.out and "PR #7" in out.out
    assert out.err.strip() == (
        "RECORD FAILED after PR #7: pr.opened append exited 1: stub append refused"
    )
    assert next_auto.find_state_file(repo) is None


def gh_pr_head(calls, oid, rc=0, name="feat/record"):
    """The gh seam for `pr-opened`: `pr view <n> --json headRefOid,headRefName` answers `oid`
    and `name`, or fails."""

    def _gh(args, root=None):
        calls.append(list(args))
        if rc:
            return subprocess.CompletedProcess(
                ["gh", *args], rc, stdout="", stderr="no pull requests found for 9"
            )
        body = json.dumps({"headRefOid": oid, "headRefName": name})
        return subprocess.CompletedProcess(["gh", *args], 0, stdout=body, stderr="")

    return _gh


def pr_branch(repo):
    """A pushed branch at the root carrying one step commit; returns its head."""
    git(repo, "checkout", "-qb", "feat/record")
    good_step(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "feat(step-1): first")
    git(repo, "push", "-q", "-u", "origin", "feat/record")
    return head_sha(repo)


def test_r12_pr_opened_records_the_head_commits_the_record_and_pushes_again(
    tmp_path, capsys, monkeypatch
):
    """ai-dlc-record R12: the attended session's `pr-opened <n>` after `gh pr create` reads
    the PR's head from GitHub and records pr.opened with it."""
    repo, control = record_repo(tmp_path)
    origin = tmp_path / "origin.git"
    opened_on = pr_branch(repo)
    calls = []
    monkeypatch.setattr(next_auto, "gh", gh_pr_head(calls, opened_on))
    rc = next_auto.main(["--root", str(repo), "pr-opened", "9"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    tip = head_sha(repo)
    assert out.out.strip() == f"PR-OPENED PR #9 head {opened_on} record {tip}"
    assert git(repo, "rev-parse", "HEAD^").stdout.strip() == opened_on
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == f"chore(record): {RECORD_SLUG}"
    assert git(origin, "rev-parse", "refs/heads/feat/record").stdout.strip() == tip
    [call] = appends(control)
    assert Path(call["cwd"]) == repo.resolve()
    assert call["argv"] == [
        "append", RECORD_SLUG, "pr.opened", "--pr", "9", "--head", opened_on, "--mode", "attended",
        "--runkey", ATTENDED_SESSION_ID,
    ]  # fmt: skip
    assert calls == [["pr", "view", "9", "--json", "headRefOid,headRefName"]]


def test_r12_pr_opened_refuses_a_plan_without_record(tmp_path, capsys):
    repo, _ = make_repo(tmp_path)
    rc = next_auto.main(["--root", str(repo), "pr-opened", "9"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.strip() == "PR-OPENED REFUSED PR #9: prompt_plan.md carries no Record"


def test_r12_revert_after_a_critical_puts_the_plan_and_the_record_back(tmp_path, capsys):
    """ai-dlc-record R11, R12: the unattended loop sets step K done before commit K reads its
    note; a Critical that survives reverts K, which drops commit K and restores the plan and the
    record as step K began, so no record line names the dropped commit."""
    repo, wt, _control = record_run(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    next_auto.set_step(repo, 1, "done", "2 tests green, mutation killed both ways")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    first = head_sha(wt)
    state = next_auto.step_begin(repo, 2, test_cmd=TEST_CMD)
    plan_at_begin = (wt / "prompt_plan.md").read_bytes()
    record_at_begin = (wt / RECORD_FILE).read_bytes()
    assert f"- stub step.done {first}\n".encode() in record_at_begin
    second_step(wt)
    assert next_auto.gate(repo, 2, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    next_auto.set_step(repo, 2, "done", "3 tests green")
    assert next_auto.main(["--root", str(repo), "commit", "2"]) == 0
    second = head_sha(wt)
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    assert next_auto.main(verdict_argv(repo, 2, second, 1, "NeedsFixes", report)) == 0
    assert f"- stub review.verdict {second}\n" in (wt / RECORD_FILE).read_text()
    assert next_auto.main(["--root", str(repo), "revert", "2"]) == 0
    capsys.readouterr()
    assert head_sha(wt) == first == state["base_head"]
    assert (wt / "prompt_plan.md").read_bytes() == plan_at_begin
    assert (wt / RECORD_FILE).read_bytes() == record_at_begin
    assert second not in (wt / RECORD_FILE).read_text()
    assert not (Path(state["run_dir"]) / "result-2.json").exists()


def test_r12_revert_refuses_a_tampered_record_snapshot(tmp_path, capsys):
    repo, wt, _control = record_run(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    snap = Path(state["run_dir"]) / "record-snapshot-1.md"
    snap.write_text(snap.read_text() + "- forged line\n")
    assert next_auto.main(["--root", str(repo), "revert", "1"]) == 1
    assert "snapshot" in capsys.readouterr().err
    assert "- forged line" not in (wt / RECORD_FILE).read_text()


def record_only_commit(repo, line):
    path = repo / RECORD_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(line)
    # the verdict under .next-auto/ is untracked here, so stage the record by name
    git(repo, "add", "--", RECORD_FILE)
    git(repo, "commit", "-qm", f"chore(record): {RECORD_SLUG}")
    return head_sha(repo)


def noted(repo, sha):
    git(repo, "notes", "--ref=preflight", "add", "-f", "-m", "preflight PASS", sha)
    return sha


@pytest.mark.parametrize("depth", [1, 2])
def test_r12_merge_accepts_the_verdict_past_record_only_commits(tmp_path, monkeypatch, depth):
    """ai-dlc-record R12, R17: the PR head sits one or two record-only commits past the reviewed
    commit, and the gate follows first parents past them to its verdict. The fixture plan
    carries no Record: a record-only commit is read from git alone."""
    repo, reviewed = merge_ready(tmp_path)
    pr_head = reviewed
    for i in range(depth):
        pr_head = record_only_commit(repo, f"- line {i}\n")
    noted(repo, pr_head)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(pr_head)))
    assert next_auto.merge_pr(repo, 7, **merge_kwargs()) == (0, [f"MERGED PR #7 {MERGE_SHA}"])


def _between(repo, kind):
    """One commit on top of the reviewed one that the walk must not pass."""
    if kind in ("code", "mixed"):
        (repo / "src.py").write_text("# Description: fixture\n# Description: two\nX = 2\n")
        git(repo, "add", "--", "src.py")
    if kind == "mixed":
        (repo / RECORD_FILE).parent.mkdir(parents=True, exist_ok=True)
        (repo / RECORD_FILE).write_text(RECORD_HEADER)
        git(repo, "add", "--", RECORD_FILE)
    if kind == "moved-in":
        (repo / "docs" / "records").mkdir(parents=True, exist_ok=True)
        git(repo, "mv", "conftest.py", "docs/records/conftest.md")
    if kind == "not-markdown":
        (repo / "docs" / "records").mkdir(parents=True, exist_ok=True)
        (repo / "docs" / "records" / "notes.txt").write_text("not a record\n")
        git(repo, "add", "--", "docs/records/notes.txt")
    if kind == "nested":
        (repo / "docs" / "records" / "sub").mkdir(parents=True, exist_ok=True)
        (repo / "docs" / "records" / "sub" / "demo.md").write_text(RECORD_HEADER)
        git(repo, "add", "--", "docs/records/sub/demo.md")
    if kind == "symlink":
        (repo / "docs" / "records").mkdir(parents=True, exist_ok=True)
        os.symlink("demo.md", repo / "docs" / "records" / "link.md")
        git(repo, "add", "--", "docs/records/link.md")
    if kind == "gitlink":
        entry = f"160000,{head_sha(repo)},docs/records/sub.md"
        git(repo, "update-index", "--add", "--cacheinfo", entry)
    if kind == "executable":
        (repo / "docs" / "records").mkdir(parents=True, exist_ok=True)
        (repo / "docs" / "records" / "run.md").write_text(RECORD_HEADER)
        (repo / "docs" / "records" / "run.md").chmod(0o755)
        git(repo, "add", "--", "docs/records/run.md")
    git(repo, "commit", "-qm", f"between: {kind}", "--allow-empty")
    return head_sha(repo)


@pytest.mark.parametrize(
    "kind",
    ["code", "mixed", "moved-in", "empty", "not-markdown", "nested", "symlink", "gitlink",
     "executable"],
)  # fmt: skip
def test_r12_merge_walk_stops_at_a_commit_that_is_not_record_only(tmp_path, monkeypatch, kind):
    """ai-dlc-record R12, R17: a record-only commit changes nothing but direct children of
    docs/records/ ending in .md. Anything outside it, a file moved in from outside, a file of
    another kind or in a subdirectory, or no change at all stops the walk, so the verdict for
    the commit below it does not carry over."""
    repo, _reviewed = merge_ready(tmp_path)
    stopped = _between(repo, kind)
    pr_head = noted(repo, record_only_commit(repo, "- after\n"))
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(pr_head)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    want = (
        "no readable verdict-<sha>.json or done-<sha>.json under .next-auto/attended/ "
        f"for {pr_head[:8]}..{stopped[:8]}"
    )
    assert (rc, lines) == (1, [f"MERGE REFUSED PR #7: {want}"])


def test_r12_merge_walk_keeps_the_head_rule_for_the_commit_it_reaches(tmp_path, monkeypatch):
    repo, reviewed = merge_ready(tmp_path)
    write_verdict(repo, reviewed, head="f" * 40)
    pr_head = noted(repo, record_only_commit(repo, "- after\n"))
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(pr_head)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    want = f"MERGE REFUSED PR #7: verdict record head {'f' * 40} is not {reviewed}"
    assert (rc, lines) == (1, [want])


# -- R13: no step runs before the plan's approval is on the record ---------------------------


def test_r13_check_begin_refuses_when_the_record_holds_no_approval(tmp_path, capsys):
    """ai-dlc-record R13, R17: `<command> approved <slug> --plan <plan>` exiting 1 refuses the
    step with one line naming the missing approval and the hash the engine computed."""
    repo, control = record_repo(tmp_path)
    stub_rc(control, "approved", 1)
    rc = next_auto.main(["--root", str(repo), "check", "begin", "1"])
    err = capsys.readouterr().err
    assert rc == 1
    want = next_plan.plan_hash(RECORD_PLAN)
    assert err.strip() == (
        f"CHECK FAIL step 1: no plan.approved line in {RECORD_FILE} for {want}; "
        "approve the plan on the record first (appender: stub approved refused)"
    )
    assert not attended_record(repo).exists()
    [call] = stub_calls(control)
    assert Path(call["cwd"]) == repo.resolve()
    assert call["argv"] == ["approved", RECORD_SLUG, "--plan", "prompt_plan.md"]


def test_r13_check_begin_proceeds_when_the_plan_is_approved(tmp_path):
    repo, control = record_repo(tmp_path)
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0, msg
    assert [c["argv"][0] for c in stub_calls(control)] == ["approved"]
    assert attended_record(repo).is_file()


@pytest.mark.parametrize("rc_or_missing", [3, "missing"])
def test_r13_check_begin_reports_an_approval_that_cannot_answer_as_apparatus(
    tmp_path, rc_or_missing
):
    plan_text = RECORD_PLAN
    if rc_or_missing == "missing":
        plan_text = RECORD_PLAN.replace("tools/record-stub", "no-such-appender-xyz")
    repo, control = record_repo(tmp_path, plan_text=plan_text)
    if rc_or_missing != "missing":
        stub_rc(control, "approved", rc_or_missing)
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 2
    code = 2 if rc_or_missing == "missing" else 3
    assert msg.startswith(f"CHECK APPARATUS step 1: record approved exited {code}: ")
    assert not attended_record(repo).exists()


def test_r13_start_refuses_an_unapproved_plan_before_gh_and_creates_no_run(
    tmp_path, capsys, monkeypatch
):
    """ai-dlc-record R13, R17: start asks the record before it reaches GitHub; exit 1 is a
    START FAILED line with the engine's hash, and no run directory or branch exists after."""
    repo, control = record_repo(tmp_path)
    stub_rc(control, "approved", 1)

    def boom(_root):
        raise AssertionError("gh must not be called once the plan is refused")

    monkeypatch.setattr(next_auto, "gh_repo_view", boom)
    rc = next_auto.main(["--root", str(repo), "start", "prompt_plan.md", "--no-sync"])
    err = capsys.readouterr().err
    assert rc == 1
    want = next_plan.plan_hash(RECORD_PLAN)
    assert err.strip() == (
        f"START FAILED: no plan.approved line in {RECORD_FILE} for {want}; "
        "approve the plan on the record first (appender: stub approved refused)"
    )
    assert next_auto.find_state_file(repo) is None
    assert "next-auto/" not in git(repo, "branch", "--list").stdout


def test_r13_start_proceeds_when_the_plan_is_approved(tmp_path, capsys, monkeypatch):
    repo, control = record_repo(tmp_path)
    view = {"isFork": False, "defaultBranchRef": {"name": "main"}}
    monkeypatch.setattr(next_auto, "gh_repo_view", lambda _root: view)
    rc = next_auto.main(["--root", str(repo), "start", "prompt_plan.md", "--no-sync"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert out.out.startswith("RUN STARTED ")
    [call] = stub_calls(control)
    assert Path(call["cwd"]) == repo.resolve()
    assert call["argv"] == ["approved", RECORD_SLUG, "--plan", "prompt_plan.md"]


def test_r13_start_reports_an_approval_that_cannot_answer(tmp_path, capsys, monkeypatch):
    repo, control = record_repo(tmp_path)
    stub_rc(control, "approved", 3)
    monkeypatch.setattr(next_auto, "gh_repo_view", lambda _root: {})
    rc = next_auto.main(["--root", str(repo), "start", "prompt_plan.md", "--no-sync"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.strip() == "START FAILED record approved exited 3: stub approved refused"
    assert next_auto.find_state_file(repo) is None


# -- R16: the owner set -----------------------------------------------------------------------


def test_r16_owner_set_is_read_from_adw_owners(monkeypatch):
    assert frozenset({"ryanmat", "acme"}) == next_auto.OWNER
    monkeypatch.setenv(next_auto.OWNERS_VAR, " one , two,,")
    assert next_auto._env_set(next_auto.OWNERS_VAR) == frozenset({"one", "two"})
    monkeypatch.delenv(next_auto.OWNERS_VAR)
    assert next_auto._env_set(next_auto.OWNERS_VAR) == frozenset()


def test_r16_an_unset_owner_list_refuses_every_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(next_auto, "OWNER", frozenset())
    repo, _ = make_repo(tmp_path)
    git(repo, "fetch", "origin")
    kw = {**base_kwargs(), "origin_owner": "ryanmat"}
    assert next_auto.preflight_checks(repo, repo / "prompt_plan.md", **kw) == [
        "ADW_OWNERS is unset; set it to the GitHub owners whose repositories auto runs may"
        " target; auto runs only in repos they own"
    ]
    assert next_auto.owner_reason("ryanmat", noun="repo owner") == (
        "ADW_OWNERS is unset; set it to the GitHub owners whose repositories auto runs may target"
    )


@pytest.mark.parametrize("owner", ["ryanmat", "acme"])
def test_r16_preflight_accepts_either_allowed_owner(tmp_path, owner):
    repo, _ = make_repo(tmp_path)
    git(repo, "fetch", "origin")
    kw = {**base_kwargs(), "origin_owner": owner}
    assert next_auto.preflight_checks(repo, repo / "prompt_plan.md", **kw) == []


def test_r16_preflight_refusal_names_the_allowed_owners(tmp_path):
    repo, _ = make_repo(tmp_path)
    git(repo, "fetch", "origin")
    kw = {**base_kwargs(), "origin_owner": "other-org"}
    assert next_auto.preflight_checks(repo, repo / "prompt_plan.md", **kw) == [
        "origin owner is 'other-org', not one of acme, ryanmat; auto runs only in repos they own"
    ]


def test_r16_preflight_cli_refuses_a_foreign_origin_naming_both_owners(tmp_path, capsys):
    repo, _ = make_repo(tmp_path)
    git(repo, "remote", "set-url", "origin", "https://github.com/other-org/thing.git")
    assert next_auto.main(["--root", str(repo), "preflight", "prompt_plan.md"]) == 1
    assert capsys.readouterr().err.strip() == (
        "PREFLIGHT REFUSED: origin owner is 'other-org', not one of acme, ryanmat; "
        "auto runs only in repos they own"
    )


def test_r16_merge_accepts_a_acme_repo(tmp_path, monkeypatch):
    repo, sha = merge_ready(tmp_path)
    calls = []
    monkeypatch.setattr(next_auto, "gh", merge_gh(calls, green_pr(sha), owner="acme"))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs(repo="acme/x"))
    assert (rc, lines) == (0, [f"MERGED PR #7 {MERGE_SHA}"])
    assert ["pr", "merge", "7", "--repo", "acme/x", "--squash", "--delete-branch"] in calls


def test_r16_the_foreign_owner_scenario_keeps_a_refused_owner():
    path = PLUGIN / "evals" / "next-auto" / "scenarios" / "refuse-foreign-owner.json"
    scenario = json.loads(path.read_text())
    assert scenario["origin_owner_override"] == "other-org"
    assert scenario["origin_owner_override"] not in next_auto.OWNER
    assert "ADW_OWNERS" in scenario["notes"]


# ---- ai-dlc-record: fix rounds, node counts, record integrity, approval, the PR head ---------


def fix_step(tree):
    """A fix round's change: a test that fails at the step's commit, and the fix for it."""
    (tree / "tests" / "test_fix.py").write_text(
        "from src import FIXED\n\n\ndef test_fix():\n    assert FIXED is True\n"
    )
    (tree / "src.py").write_text(
        "# Description: fixture\n# Description: two\nX = 1\nFEATURE = 42\nFIXED = True\n"
    )


def step_done_calls(control):
    return [
        (c["evidence"]["step"], c["evidence"]["sha"], c["evidence"]["head"])
        for c in appends(control)
        if c["argv"][2] == "step.done"
    ]


def run_step_one(repo, wt):
    """Step 1 of a run through step-begin, gate, set done and commit; returns the commit."""
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    next_auto.set_step(repo, 1, "done", "2 tests green, mutation killed both ways")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    return head_sha(wt)


def test_r12_attended_fix_round_re_begins_the_step_and_appends_the_fix_commits_step_done(
    tmp_path, capsys
):
    """ai-dlc-record R11, R12: the boundary's fix round runs check begin N on the DONE step,
    whose base is now the step's commit, then the fix, check N and commit N. The fix commit
    appends its own step.done, so the step has two, and it carries the first round's lines."""
    repo, control = record_repo(tmp_path)
    base = head_sha(repo)
    attended_pass(repo, 1)
    set_done(repo, 1, "2 tests green, mutation killed both ways, CHECK PASS")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    first = head_sha(repo)
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    assert next_auto.main(verdict_argv(repo, 1, first, 1, "NeedsFixes", report)) == 0
    attended_pass(repo, 1, fix_step)
    set_done(repo, 1, "3 tests green, mutation killed both ways, fix round, CHECK PASS")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    capsys.readouterr()
    fix = head_sha(repo)
    assert git(repo, "rev-parse", "HEAD^").stdout.strip() == first
    assert step_done_calls(control) == [(1, first, base), (1, fix, first)]
    carried = git(repo, "show", f"HEAD:{RECORD_FILE}").stdout
    assert f"- stub step.done {first}\n" in carried
    assert f"- stub review.verdict {first}\n" in carried


def test_r12_unattended_fix_round_re_begins_at_the_step_commit(tmp_path, capsys):
    """ai-dlc-record R11, R12: item 7's fix round runs step-begin K again, so the gate judges
    the fix against the step's commit, and commit K appends the fix commit's own step.done."""
    repo, wt, control = record_run(tmp_path)
    first = run_step_one(repo, wt)
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    assert next_auto.main(verdict_argv(repo, 1, first, 1, "NeedsFixes", report)) == 0
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    assert state["base_head"] == first
    fix_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK) == (True, "gate passed")
    next_auto.set_step(repo, 1, "done", "3 tests green, mutation killed both ways, fix round")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    capsys.readouterr()
    fix = head_sha(wt)
    assert [(s, sha) for s, sha, _head in step_done_calls(control)] == [(1, first), (1, fix)]
    carried = git(wt, "show", f"HEAD:{RECORD_FILE}").stdout
    assert f"- stub review.verdict {first}\n" in carried


def test_r12_a_critical_that_survives_the_fix_round_drops_the_whole_step(tmp_path, capsys):
    """ai-dlc-record R12: with the fix round open, revert K drops both of the step's commits
    back to its first base and restores the plan and the record as the step first began, so
    a retry gates as after a fresh step-begin. A repeated fix-round begin keeps that origin."""
    repo, wt, _control = record_run(tmp_path)
    origin = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    plan_at_origin = (wt / "prompt_plan.md").read_bytes()
    record_at_origin = (wt / RECORD_FILE).read_bytes()
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    next_auto.set_step(repo, 1, "done", "2 tests green, mutation killed both ways")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    first = head_sha(wt)
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    assert next_auto.main(verdict_argv(repo, 1, first, 1, "NeedsFixes", report)) == 0
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    fix_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    next_auto.set_step(repo, 1, "done", "3 tests green, fix round")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    fix = head_sha(wt)
    assert next_auto.main(verdict_argv(repo, 1, fix, 2, "NeedsFixes", report)) == 0
    assert next_auto.main(["--root", str(repo), "revert", "1"]) == 0
    capsys.readouterr()
    assert head_sha(wt) == origin["base_head"]
    assert (wt / "prompt_plan.md").read_bytes() == plan_at_origin
    assert (wt / RECORD_FILE).read_bytes() == record_at_origin
    state = next_auto.load_state(repo)
    assert state["base_head"] == origin["base_head"] and "1" not in state["steps"]
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK) == (True, "gate passed")


def test_r12_revert_refuses_a_tampered_first_snapshot_after_a_fix_round(tmp_path, capsys):
    repo, wt, _control = record_run(tmp_path)
    first = run_step_one(repo, wt)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    snap = Path(state["run_dir"]) / "plan-origin-1.md"
    snap.write_text(snap.read_text().replace("Status: IN PROGRESS", "Status: DONE (forged)"))
    assert next_auto.main(["--root", str(repo), "revert", "1"]) == 1
    assert "snapshot" in capsys.readouterr().err
    assert head_sha(wt) == first


# captured from `node --test` (v22) with its output piped, which prints the TAP summary;
# every count differs, so a field read from the wrong line shows
NODE_SUMMARY = (
    "TAP version 13\n# Subtest: passes 1\nok 1 - passes 1\n  ---\n  duration_ms: 0.4\n  ...\n"
    "1..7\n# tests 7\n# suites 0\n# pass 3\n# fail 1\n# cancelled 0\n# skipped 2\n"
    "# todo 1\n# duration_ms 55.970828\n"
)


def test_r11_counts_read_the_node_test_runners_summary(tmp_path):
    """ai-dlc-record R11: collected is `# tests`, passed is `# pass`, skipped is `# skipped`,
    and todo stands in for xfailed; the result file's counts read the same summary."""
    assert next_auto.parse_counts(NODE_SUMMARY) == (7, 2, 1)
    path = tmp_path / "result-1.json"
    next_auto.write_result(path, 1, "attended", "a" * 40, NODE_SUMMARY, "ok", tree="0" * 64)
    result = json.loads(path.read_text())
    assert (result["passed"], result["collected"]) == (3, 7)


def test_r11_pytest_counts_are_read_as_before():
    assert next_auto.parse_counts("3 passed, 1 skipped, 2 xfailed in 0.10s") == (6, 1, 2)
    assert next_auto.parse_counts("no summary a runner prints") == (0, 0, 0)


# captured from `bun test` (Bun 1.4.2) with its output piped: the summary goes to stderr,
# after the failures; Ran counts every test, todo and skipped ones included
BUN_SUMMARY = (
    "bun test v1.4.2 (744846f84)\n"
    "Expected: 2\nReceived: 1\n\n"
    "      at <anonymous> (x.test.ts:6:30)\n(fail) f1 [0.14ms]\n\n"
    " 2 pass\n 1 skip\n 1 todo\n 1 fail\n 3 expect() calls\n"
    "Ran 5 tests across 1 file. [13.00ms]\n"
)
# the same Bun with one file failing to import: the error is counted as a failed test
BUN_IMPORT_ERROR = (
    "bun test v1.4.2 (744846f84)\n\n# Unhandled error between tests\n"
    "-------------------------------\n"
    "error: Cannot find module './missing' from 'y.test.ts'\n"
    "-------------------------------\n\n\n"
    " 1 pass\n 1 fail\n 1 error\n 1 expect() calls\nRan 2 tests across 2 files. [13.00ms]\n"
)
BUN_CLEAN = (
    "bun test v1.4.2 (744846f84)\n\n"
    " 2 pass\n 0 fail\n 2 expect() calls\nRan 2 tests across 1 file. [12.00ms]\n"
)


def test_r11_counts_read_buns_summary(tmp_path):
    """Collected is Bun's `Ran N tests`, passed its `pass`, skipped its `skip`, and todo
    stands in for xfailed, as it does for node; the result file reads the same summary."""
    assert next_auto.parse_counts(BUN_SUMMARY) == (5, 1, 1)
    assert next_auto.parse_counts(BUN_IMPORT_ERROR) == (2, 0, 0)
    path = tmp_path / "result-1.json"
    next_auto.write_result(path, 1, "attended", "a" * 40, BUN_SUMMARY, "ok", tree="0" * 64)
    result = json.loads(path.read_text())
    assert (result["passed"], result["collected"]) == (2, 5)


# the summary of BUN_SUMMARY's run with FORCE_COLOR=1, as Bun 1.4.2 wrote it
BUN_SUMMARY_COLOR = (
    "\n\x1b[0m\x1b[32m 2 pass\x1b[0m\n \x1b[0m\x1b[33m1 skip\x1b[0m\n"
    " \x1b[0m\x1b[35m1 todo\x1b[0m\n\x1b[0m\x1b[31m 1 fail\x1b[0m\n 3 expect() calls\n"
    "Ran 5 tests across 1 file. \x1b[0m\x1b[2m[\x1b[1m12.00ms\x1b[0m\x1b[2m]\x1b[0m\n"
)


def test_r11_counts_read_buns_summary_with_color_on(tmp_path):
    assert next_auto.parse_counts(BUN_SUMMARY_COLOR) == (5, 1, 1)
    assert next_auto.summary_counts(BUN_SUMMARY_COLOR)[0] == 2


# Bun 1.4.2's summary on the run that wrote a new snapshot
BUN_SNAPSHOT = (
    "\n 2 pass\n 0 fail\nsnapshots: +1 added\n 2 expect() calls\n"
    "Ran 2 tests across 1 file. [12.00ms]\n"
)


def test_r11_counts_read_buns_summary_with_a_snapshot_line():
    assert next_auto.parse_counts(BUN_SNAPSHOT) == (2, 0, 0)
    assert next_auto.summary_counts(BUN_SNAPSHOT)[0] == 2


def test_r11_counts_read_only_a_whole_summary_block():
    # a test's own log lines shaped like summary lines, apart from each other, are not a
    # summary; the stdout of a run comes before its stderr, where Bun writes its summary
    logs = " 3 pass\nsome other line\nRan 9 tests across 9 files.\n# tests 4\nmore\n"
    assert next_auto.parse_counts(logs + BUN_SUMMARY) == (5, 1, 1)
    assert next_auto.parse_counts(logs + NODE_SUMMARY) == (7, 2, 1)


def test_r11_counts_sum_bun_and_node_when_one_test_runs_both(tmp_path):
    # acme/platform's Test runs `bun --cwd infra test` then `node --test`, so the output
    # carries both summaries and each one's tests count
    out = BUN_CLEAN + NODE_SUMMARY
    assert next_auto.parse_counts(out) == (9, 2, 1)
    path = tmp_path / "result-1.json"
    next_auto.write_result(path, 1, "attended", "a" * 40, out, "ok", tree="0" * 64)
    result = json.loads(path.read_text())
    assert (result["passed"], result["collected"]) == (5, 9)


def _plan_with(tmp_path, test: str, lint: str) -> Path:
    (tmp_path / "prompt_plan.md").write_text(
        PLAN.replace("- Test: uv run pytest", f"- Test: {test}").replace(
            "- Lint: uv run ruff check .", f"- Lint: {lint}"
        )
    )
    return tmp_path


def test_plan_commands_keeps_a_quoted_and_inside_its_argument(tmp_path):
    root = _plan_with(
        tmp_path, "bun --cwd infra test", "sh -c 'bun run --cwd infra build && npm run lint'"
    )
    assert next_auto.plan_commands(root, "prompt_plan.md") == (
        ["bun", "--cwd", "infra", "test"],
        ["sh", "-c", "bun run --cwd infra build && npm run lint"],
    )


def test_plan_commands_keeps_a_hash_inside_a_word(tmp_path):
    root = _plan_with(tmp_path, "uv run pytest", "npx eslint src#x")
    assert next_auto.plan_commands(root, "prompt_plan.md")[1] == ["npx", "eslint", "src#x"]


@pytest.mark.parametrize(
    "lint",
    ["uv run ruff check . && uv run ruff format .", "uv run ruff check .&&uv run ruff format ."],
)
def test_plan_commands_cuts_lint_at_an_unquoted_and(tmp_path, lint):
    root = _plan_with(tmp_path, "uv run pytest", lint)
    assert next_auto.plan_commands(root, "prompt_plan.md")[1] == ["uv", "run", "ruff", "check", "."]


def test_r12_gate_refuses_a_record_edited_during_the_step(tmp_path):
    """ai-dlc-record R12: during a step the only writer of the record is the engine's append
    after commit, so the gate refuses a record that differs from its step-begin snapshot."""
    repo, wt, _control = record_run(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    with (wt / RECORD_FILE).open("a") as fh:
        fh.write("- a line the step wrote\n")
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert not ok
    snap = Path(state["run_dir"]) / "record-snapshot-1.md"
    assert reason == (
        f"{RECORD_FILE} changed during the step; the implementer may not edit the record "
        f"(snapshot: {snap})"
    )


def test_r12_check_refuses_a_record_edited_since_check_begin(tmp_path):
    """ai-dlc-record R12: check begin N snapshots the record, and check N refuses a record
    that differs from it; a pending line from before check begin is part of the snapshot."""
    repo, _control = record_repo(tmp_path)
    with (repo / RECORD_FILE).open("a") as fh:
        fh.write("- a pending line\n")
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0] == 0
    good_step(repo)
    with (repo / RECORD_FILE).open("a") as fh:
        fh.write("- a line the step wrote\n")
    rc, lines = next_auto.check_step(repo, 1)
    snap = repo / ".next-auto" / "attended" / "record-snapshot-1.md"
    assert (rc, lines) == (
        1,
        [
            f"CHECK FAIL step 1: {RECORD_FILE} changed during the step; the step may not "
            f"edit the record (snapshot: {snap})"
        ],
    )


def test_r13_the_refusal_carries_no_appender_line_when_it_printed_none(tmp_path):
    repo, control = record_repo(tmp_path)
    stub_rc(control, "approved", 1)
    (control / "approved-quiet").write_text("")
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 1
    assert msg.endswith("approve the plan on the record first")


def drop_record(repo):
    git(repo, "rm", "-q", "--", RECORD_FILE)
    git(repo, "commit", "-qm", "no record yet")


def test_r13_check_begin_reports_a_missing_record_as_apparatus(tmp_path):
    """ai-dlc-record R13: no record file is apparatus, named, before `approved` is asked."""
    repo, control = record_repo(tmp_path)
    drop_record(repo)
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert (rc, msg) == (
        2,
        f"CHECK APPARATUS step 1: no record at {RECORD_FILE}; "
        f"run tools/record-stub init {RECORD_SLUG} first",
    )
    assert stub_calls(control) == []


def test_r13_start_reports_a_missing_record(tmp_path, capsys, monkeypatch):
    repo, control = record_repo(tmp_path)
    drop_record(repo)
    monkeypatch.setattr(next_auto, "gh_repo_view", lambda _root: {})
    rc = next_auto.main(["--root", str(repo), "start", "prompt_plan.md", "--no-sync"])
    assert rc == 1
    assert capsys.readouterr().err.strip() == (
        f"START FAILED no record at {RECORD_FILE}; run tools/record-stub init {RECORD_SLUG} first"
    )
    assert stub_calls(control) == [] and next_auto.find_state_file(repo) is None


def test_r12_pr_opened_refuses_a_pr_head_that_is_not_local_head(tmp_path, capsys, monkeypatch):
    """ai-dlc-record R12: pr.opened names the head GitHub holds for the PR, and only when the
    session's HEAD is that head."""
    repo, control = record_repo(tmp_path)
    opened_on = pr_branch(repo)
    other = "f" * 40
    monkeypatch.setattr(next_auto, "gh", gh_pr_head([], other))
    rc = next_auto.main(["--root", str(repo), "pr-opened", "9"])
    assert rc == 1
    assert capsys.readouterr().err.strip() == (
        f"PR-OPENED REFUSED PR #9: PR head {other} is not local HEAD {opened_on}"
    )
    assert appends(control) == [] and head_sha(repo) == opened_on


def test_r12_pr_opened_reports_a_failed_pr_view_as_apparatus(tmp_path, capsys, monkeypatch):
    repo, control = record_repo(tmp_path)
    opened_on = pr_branch(repo)
    monkeypatch.setattr(next_auto, "gh", gh_pr_head([], "", rc=1))
    rc = next_auto.main(["--root", str(repo), "pr-opened", "9"])
    assert rc == 2
    err = capsys.readouterr().err.strip()
    assert err == "PR-OPENED APPARATUS PR #9: no pull requests found for 9"
    assert appends(control) == [] and head_sha(repo) == opened_on


def test_r12_wrap_up_commits_pending_record_lines_before_its_first_push(tmp_path, monkeypatch):
    """ai-dlc-record R12: with Record, wrap-up commits the record's pending lines alone after
    the plan and before the first push, so the PR opens on a head that carries them. The
    stub's sync writes nothing here, so the pending lines' commit is the head; the sync's own
    commit is adw 3.5.0 spec item 6's test."""
    repo, wt, control = record_run(tmp_path, approved=True)
    (control / "sync-silent").write_text("")
    branch = next_auto.load_state(repo)["branch"]
    run_step_one(repo, wt)
    next_auto.set_step(repo, 2, "done", "b")
    with (wt / RECORD_FILE).open("a") as fh:
        fh.write("- a pending line\n")
    origin = tmp_path / "origin.git"
    calls = []
    monkeypatch.setattr(next_auto, "gh", gh_seeing_the_pushed_head(calls, origin, branch))
    assert next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")["record"] is None
    [opened_on] = [c[1] for c in calls if c[0] == "pushed-head"]
    subject = git(wt, "log", "-1", "--format=%s", opened_on).stdout.strip()
    assert subject == f"chore(record): {RECORD_SLUG}"
    below = git(wt, "log", "-1", "--format=%s", f"{opened_on}^").stdout.strip()
    assert below.startswith("chore(plan): record run")
    assert "- a pending line\n" in git(wt, "show", f"{opened_on}:{RECORD_FILE}").stdout
    tip = git(origin, "rev-parse", f"refs/heads/{branch}").stdout.strip()
    assert git(wt, "rev-parse", f"{tip}^").stdout.strip() == opened_on


# ---- ai-dlc-record R11: Test-files and the scratch worktree's node_modules ------------------

PLATFORM_TEST_GLOBS = [
    "**/*_test.go", "**/*.test.ts", "**/*.test.mjs", "**/*.e2e.ts", "**/test_*.py",
]  # fmt: skip


@pytest.mark.parametrize(
    "path, hit",
    [
        ("record.test.mjs", True),
        ("tools/workflows/record.test.mjs", True),
        ("tools/workflows/record.mjs", False),
        ("tools/workflows/record.test.mjs.bak", False),
        ("services/api/handler_test.go", True),
        ("web/app.test.ts", True),
        ("web/app.e2e.ts", True),
        ("test_root.py", True),
        ("tests/test_a.py", True),
        ("tests/helpers.py", False),
    ],
)
def test_r11_test_files_globs_pick_the_staged_test_files(path, hit):
    """ai-dlc-record R11: with Test-files, a staged path is a test file when it matches any
    pattern, `**` spanning zero or more directories; these are acme/platform's patterns."""
    assert next_auto._staged_test_files([path], PLATFORM_TEST_GLOBS) == ([path] if hit else [])


@pytest.mark.parametrize(
    "pattern, path, hit",
    [
        ("*.test.mjs", "record.test.mjs", True),
        ("*.test.mjs", "tools/record.test.mjs", False),
        ("tests/*.py", "tests/a.py", True),
        ("tests/*.py", "tests/sub/a.py", False),
        ("tests/**", "tests/sub/a.py", True),
        ("tests/**", "tests", False),
        ("src/**/x.py", "src/x.py", True),
        ("src/**/x.py", "src/a/b/x.py", True),
        ("t?st_*.py", "tost_1.py", True),
        ("[ab]*.md", "a.md", True),
        ("[!ab]*.md", "a.md", False),
        ("[!ab]*.md", "c.md", True),
        ("**/*.test.mjs", ".hidden.test.mjs", True),
        ("**", "a/b/c", True),
    ],
)
def test_r11_test_files_globs_match_as_pathlib_full_match_does(pattern, path, hit):
    """ai-dlc-record R11: the semantics Python 3.13's PurePath.full_match gives, built from the
    standard library the 3.12 floor has; each row was read from full_match on 3.14."""
    assert next_auto.glob_match(path, pattern) is hit


def test_r11_with_test_files_the_built_in_python_rule_does_not_apply():
    staged = ["tests/test_a.py", "src/feature.test.mjs"]
    assert next_auto._staged_test_files(staged) == ["tests/test_a.py"]
    assert next_auto._staged_test_files(staged, ["**/*.test.mjs"]) == ["src/feature.test.mjs"]


NODE_PLAN = (
    PLAN.replace("- Test: uv run pytest", "- Test: node --test")
    .replace("- Lint: uv run ruff check .", '- Lint: sh -c "exit 0"')
    .replace("- Run: uv run python -m fixturepkg", "- Test-files: **/*.test.mjs")
)
NODE_TEST_HEADER = 'import { test } from "node:test";\nimport assert from "node:assert";\n'
DEP_TEST = NODE_TEST_HEADER + (
    'import { DEP } from "fixture-dep";\n\ntest("dep", () => assert.equal(DEP, "dep"));\n'
)
FEATURE_TEST = NODE_TEST_HEADER + (
    'import { DEP } from "fixture-dep";\nimport { FEATURE } from "./feature.mjs";\n\n'
    'test("feature", () => {\n  assert.equal(DEP, "dep");\n  assert.equal(FEATURE, 42);\n});\n'
)


def node_repo(tmp_path):
    """A repo whose plan runs `node --test` and names its test files with Test-files, with an
    npm package installed under an ignored node_modules at the root, as a developer has it."""
    assert shutil.which("node"), "these tests run the node test runner"
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "t@t.t")
    git(repo, "config", "user.name", "t")
    git(repo, "remote", "add", "origin", str(origin))
    (repo / "prompt_plan.md").write_text(NODE_PLAN)
    (repo / ".gitignore").write_text("node_modules/\n")
    (repo / "feature.mjs").write_text("export const FEATURE = 0;\n")
    (repo / "base.test.mjs").write_text(NODE_TEST_HEADER + 'test("base", () => assert.ok(true));\n')
    dep = repo / "node_modules" / "fixture-dep"
    dep.mkdir(parents=True)
    manifest = {"name": "fixture-dep", "type": "module", "exports": "./index.mjs"}
    (dep / "package.json").write_text(json.dumps(manifest) + "\n")
    (dep / "index.mjs").write_text('export const DEP = "dep";\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def node_run(tree):
    r = subprocess.run(["node", "--test"], cwd=tree, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def test_r11_prove_red_links_node_modules_so_an_import_is_not_counted_as_red(tmp_path):
    """ai-dlc-record R11: prove-red's scratch worktree gets the tree's node_modules. The
    scratch sits under the root, so node already finds packages installed there; in a run the
    tree is the worktree, with its own packages and none at the root. Without the link a test
    that passes on base fails in the scratch on its import and is counted as red; with it,
    the gate sees the test pass on base and refuses it. The worktree's copy survives."""
    repo = node_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260924-deps01", sync=False)
    wt = Path(state["worktree"])
    (repo / "node_modules").rename(wt / "node_modules")
    next_auto.step_begin(repo, 1)
    (wt / "dep.test.mjs").write_text(DEP_TEST)
    where_scratch_sits = repo / ".next-auto" / "next-auto-scratch-probe"
    where_scratch_sits.mkdir()
    (where_scratch_sits / "dep.test.mjs").write_text(DEP_TEST)
    rc, out = node_run(where_scratch_sits)
    shutil.rmtree(where_scratch_sits)
    assert rc != 0 and "ERR_MODULE_NOT_FOUND" in out and "fixture-dep" in out
    ok, reason = next_auto.gate(repo, 1)
    assert not ok and reason.startswith("prove-red failed"), reason
    assert (wt / "node_modules" / "fixture-dep" / "index.mjs").is_file()
    assert worktree_count(repo) == 2 and no_scratch(repo)


def test_r11_check_passes_a_node_step_whose_test_imports_a_package(tmp_path):
    """ai-dlc-record R11: the plan's Test-files names the step's test, prove-red finds it red
    on base for the right reason (the assertion, not the import), and the result file reads
    the node runner's counts."""
    repo = node_repo(tmp_path)
    base = head_sha(repo)
    rc, msg = next_auto.check_begin(repo, 1)
    assert rc == 0, msg
    (repo / "feature.mjs").write_text("export const FEATURE = 42;\n")
    (repo / "feature.test.mjs").write_text(FEATURE_TEST)
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    result = json.loads(attended_result(repo).read_text())
    assert (result["passed"], result["collected"]) == (2, 2)
    on_base = tmp_path / "base-with-deps"
    on_base.mkdir()
    (on_base / "feature.mjs").write_text(git(repo, "show", f"{base}:feature.mjs").stdout)
    (on_base / "feature.test.mjs").write_text(FEATURE_TEST)
    (on_base / "node_modules").symlink_to(repo / "node_modules", target_is_directory=True)
    rc, out = node_run(on_base)
    assert rc != 0 and "ERR_MODULE_NOT_FOUND" not in out and "42" in out


def test_r11_gate_takes_the_test_files_from_the_plan(tmp_path):
    """ai-dlc-record R11: gate K reads Test-files from the worktree plan; without it the same
    step has no staged test file the built-in Python rule recognizes."""
    repo = node_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260924-node01", sync=False)
    wt = Path(state["worktree"])
    next_auto.step_begin(repo, 1)
    (wt / "feature.mjs").write_text("export const FEATURE = 42;\n")
    plain_test = NODE_TEST_HEADER + (
        'import { FEATURE } from "./feature.mjs";\n\ntest("f", () => assert.equal(FEATURE, 42));\n'
    )
    (wt / "feature.test.mjs").write_text(plain_test)
    assert next_auto.gate(repo, 1) == (True, "gate passed")


# ---- ai-dlc-record review round 1: the checked tree, the base appender, verdict chains -----

EVIDENCE = Path(__file__).parent / "fixtures" / "record-evidence"


def evidence_keys(name):
    return list(json.loads((EVIDENCE / name).read_text()))


def test_r11_the_engines_evidence_files_carry_the_shared_fixtures_keys(tmp_path, capsys):
    """ai-dlc-record R11: the result files and the verdict file the engine writes carry exactly
    the keys of tests/fixtures/record-evidence/, which acme/platform's appender test reads."""
    (tmp_path / "a").mkdir()
    repo, _control = record_repo(tmp_path / "a")
    attended_pass(repo)
    set_done(repo, 1, "2 tests green, mutation killed both ways, CHECK PASS")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    assert list(json.loads(attended_result(repo).read_text())) == evidence_keys(
        "result-attended.json"
    )
    report = tmp_path / "reviewer-report.md"
    report.write_text((EVIDENCE / "report.md").read_text())
    sha = head_sha(repo)
    assert next_auto.main(verdict_argv(repo, 1, sha, 1, "NeedsFixes", report)) == 0
    verdict = repo / ".next-auto" / "attended" / f"verdict-{sha}.json"
    assert list(json.loads(verdict.read_text())) == evidence_keys("verdict.json")
    (tmp_path / "b").mkdir()
    repo, wt, _control = record_run(tmp_path / "b")
    run_step_one(repo, wt)
    capsys.readouterr()
    result = repo / ".next-auto" / "20260924-rec123" / "result-1.json"
    assert list(json.loads(result.read_text())) == evidence_keys("result-auto.json")
    text = (EVIDENCE / "report.md").read_text()
    for header in ("#### Critical", "#### Important", "#### Minor"):
        assert header in text


def test_r11_check_records_the_tree_it_checked(tmp_path):
    """ai-dlc-record R11: a pass stores `tree`, a hash of the staged paths and blob ids."""
    repo = begun(tmp_path)
    good_step(repo)
    assert next_auto.check_step(repo, 1)[0] == 0
    tree = json.loads(attended_result(repo).read_text())["tree"]
    assert len(tree) == 64 and set(tree) <= set("0123456789abcdef")


def test_r12_commit_refuses_a_tree_that_changed_since_check_passed(tmp_path, capsys):
    """ai-dlc-record R11, R12: step.done describes the tree check N passed or nothing; the plan
    and the record may change between the two, anything else refuses before committing."""
    repo, control = record_repo(tmp_path)
    attended_pass(repo)
    set_done(repo, 1, "2 tests green, CHECK PASS")
    (repo / "src.py").write_text("# Description: fixture\n# Description: two\nX = 1\nFEATURE = 7\n")
    before = head_sha(repo)
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    assert rc == 1
    assert capsys.readouterr().err.strip() == (
        "COMMIT REFUSED step 1: the tree changed since check 1 passed; run check 1 again"
    )
    assert head_sha(repo) == before and appends(control) == []


def test_r12_commit_refuses_a_result_file_it_already_used(tmp_path, capsys):
    repo, control = record_repo(tmp_path)
    attended_pass(repo)
    set_done(repo, 1, "2 tests green, CHECK PASS")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    first = head_sha(repo)
    (repo / "notes.md").write_text("more\n")
    capsys.readouterr()
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 1
    assert capsys.readouterr().err.strip() == (
        f"COMMIT REFUSED step 1: the result file for step 1 was already used by commit "
        f"{first[:8]}; run check 1 again"
    )
    assert head_sha(repo) == first and len(appends(control)) == 1


def test_r12_run_commit_refuses_a_tree_that_changed_since_the_gate(tmp_path, capsys):
    repo, wt, control = record_run(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    next_auto.set_step(repo, 1, "done", "2 tests green")
    (wt / "extra.py").write_text("# Description: x\n# Description: y\nY = 1\n")
    git(wt, "add", "extra.py")
    before = head_sha(wt)
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    assert rc == 1
    assert capsys.readouterr().err.strip() == (
        "COMMIT REFUSED step 1: the tree changed since gate 1 passed; run gate 1 again"
    )
    assert head_sha(wt) == before and appends(control) == []


FORGED_STUB = (
    "#!@PYTHON@\n# Description: an appender a step rewrote to write its own line.\n"
    "# Description: the engine must never run it.\n"
    "open('docs/records/demo.md', 'a').write('- forged\\n')\n"
)


def test_r12_appends_run_the_appender_committed_on_the_default_branch(tmp_path, capsys):
    """ai-dlc-record R12: a step that rewrites the appender still gets the default branch's
    appender's line, run from a scratch worktree at origin's default branch, with cwd at the
    tree so the tree's record is the one written; the scratch goes afterwards."""
    repo, control = record_repo(tmp_path)

    def forging_step(tree):
        good_step(tree)
        (tree / "tools" / "record-stub").write_text(FORGED_STUB.replace("@PYTHON@", sys.executable))

    attended_pass(repo, 1, forging_step)
    set_done(repo, 1, "2 tests green, CHECK PASS")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    capsys.readouterr()
    sha = head_sha(repo)
    record = (repo / RECORD_FILE).read_text()
    assert f"- stub step.done {sha}\n" in record and "- forged" not in record
    [call] = appends(control)
    assert Path(call["cwd"]) == repo.resolve()
    script = Path(call["script"])
    assert script.name == "record-stub" and "next-auto-scratch-" in str(script)
    assert worktree_count(repo) == 1 and no_scratch(repo)


def test_r12_a_record_command_naming_no_tracked_file_is_apparatus(tmp_path, capsys):
    """ai-dlc-record R12: an appender outside the tree has no copy on the default branch, so
    the engine cannot run it as committed there; it never runs it as given."""
    repo, control = record_repo(tmp_path)
    outside = control / "outside-stub"
    outside.write_text((repo / "tools" / "record-stub").read_text())
    outside.chmod(0o755)
    (repo / "prompt_plan.md").write_text(RECORD_PLAN.replace("tools/record-stub", str(outside)))
    git(repo, "commit", "-qam", "an appender outside the repo")
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert (rc, msg) == (
        2,
        "CHECK APPARATUS step 1: record approved exited 2: the Record command names no tracked "
        "file, so it cannot run as committed on the default branch",
    )
    assert stub_calls(control) == []


def test_r12_the_appenders_scratch_gets_the_trees_node_modules(tmp_path, capsys):
    repo, control = record_repo(tmp_path)
    with (repo / ".git" / "info" / "exclude").open("a") as fh:
        fh.write("node_modules/\n")
    (repo / "node_modules").mkdir()
    attended_pass(repo)
    set_done(repo, 1, "2 tests green, CHECK PASS")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    capsys.readouterr()
    [call] = appends(control)
    assert call["deps"] is True
    assert (repo / "node_modules").is_dir()


def test_r12_an_unset_origin_head_is_apparatus_for_the_appender(tmp_path, capsys):
    repo, control = record_repo(tmp_path)
    git(repo, "remote", "set-head", "origin", "--delete")
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 2
    assert msg.startswith("CHECK APPARATUS step 1: record approved exited 2: ")
    assert "git remote set-head origin -a" in msg
    assert stub_calls(control) == []


@pytest.mark.parametrize("keyed", ["head", "middle"])
def test_r12_merge_accepts_a_verdict_keyed_to_any_commit_on_the_walked_chain(
    tmp_path, monkeypatch, keyed
):
    """ai-dlc-record R12: the verdict may be keyed to the PR head, to a record-only commit in
    between, or to the commit where the walk stops."""
    repo, reviewed = merge_ready(tmp_path)
    (repo / ".next-auto" / "attended" / f"verdict-{reviewed}.json").unlink()
    middle = record_only_commit(repo, "- a\n")
    pr_head = noted(repo, record_only_commit(repo, "- b\n"))
    write_verdict(repo, pr_head if keyed == "head" else middle)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(pr_head)))
    assert next_auto.merge_pr(repo, 7, **merge_kwargs()) == (0, [f"MERGED PR #7 {MERGE_SHA}"])


def test_r12_merge_accepts_the_done_checks_record_past_record_only_commits(tmp_path, monkeypatch):
    repo, reviewed = merge_ready(tmp_path)
    (repo / ".next-auto" / "attended" / f"verdict-{reviewed}.json").unlink()
    write_done(repo, reviewed)
    pr_head = noted(repo, record_only_commit(repo, "- done\n"))
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(pr_head)))
    assert next_auto.merge_pr(repo, 7, **merge_kwargs())[0] == 0


@pytest.mark.parametrize(
    "record, reason",
    [
        ({"verdict": "REJECTED"}, "done check REJECTED at {short}"),
        ({"verdict": "PENDING"}, "done check is 'PENDING', not APPROVED"),
        ({"reviewer": "someone-else"}, "done check from 'someone-else', not done-check"),
        ({"head": "f" * 40}, "done record head {other} is not {sha}"),
    ],
)
def test_r12_merge_refuses_a_done_record_that_does_not_approve(
    tmp_path, monkeypatch, record, reason
):
    repo, sha = merge_ready(tmp_path)
    (repo / ".next-auto" / "attended" / f"verdict-{sha}.json").unlink()
    write_done(repo, sha, **record)
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha)))
    want = reason.format(sha=sha, other="f" * 40, short=sha[:8])
    assert next_auto.merge_pr(repo, 7, **merge_kwargs()) == (1, [f"MERGE REFUSED PR #7: {want}"])


def test_r12_merge_no_longer_reads_the_done_check_from_the_verdict_file(tmp_path, monkeypatch):
    """ai-dlc-record R12: verdict-<sha>.json is the chunk reviewer's alone."""
    repo, sha = merge_ready(tmp_path)
    write_verdict(repo, sha, verdict="APPROVED", reviewer="done-check")
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(sha)))
    assert next_auto.merge_pr(repo, 7, **merge_kwargs()) == (
        1,
        ["MERGE REFUSED PR #7: verdict is 'APPROVED', not Approved"],
    )


def test_r12_wrap_up_skips_pr_opened_when_the_run_committed_no_step(
    tmp_path, monkeypatch, capsys
):
    """ai-dlc-record R12: a run that committed no step records no pr.opened, and wrap-up says
    so on one line."""
    repo, _wt, control = record_run(tmp_path, approved=True)
    next_auto.set_step(repo, 1, "blocked-decision", "which store?")
    monkeypatch.setattr(next_auto, "gh", fake_gh([]))
    monkeypatch.setattr(next_auto, "origin_repo", lambda _root: "ryanmat/fixture")
    rc = next_auto.main(["--root", str(repo), "wrap-up"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert "RECORD SKIPPED pr.opened: the run committed no step" in out.out.splitlines()
    assert appends(control) == []


def test_r13_an_approval_that_prints_another_hash_is_apparatus(tmp_path):
    """ai-dlc-record R13: exit 0 approves only with the engine's hash on stdout."""
    repo, control = record_repo(tmp_path)
    (control / "approved-stdout").write_text("sha256:" + "0" * 64)
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert (rc, msg) == (
        2,
        f"CHECK APPARATUS step 1: record approved exited 0: sha256:{'0' * 64}",
    )


def test_r13_a_refusal_without_the_engines_hash_is_apparatus(tmp_path):
    """ai-dlc-record R13: exit 1 is a missing approval only with the engine's hash on stdout;
    any other exit 1 is apparatus, carrying the appender's last stderr line."""
    repo, control = record_repo(tmp_path)
    stub_rc(control, "approved", 1)
    (control / "approved-stdout").write_text("no record here")
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    want = "CHECK APPARATUS step 1: record approved exited 1: stub approved refused"
    assert (rc, msg) == (2, want)


def test_r12_a_whole_step_revert_keeps_the_first_attempts_patch(tmp_path, capsys):
    """ai-dlc-record R12: still Critical after the fix round, revert K drops the step commit and
    the fix commit into a patch of its own and keeps the patch of the step's red first attempt."""
    repo, wt, _control = record_run(tmp_path)
    origin = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    run_dir = Path(origin["run_dir"])
    (wt / "tests" / "test_vacuous.py").write_text("def test_vacuous():\n    assert True\n")
    assert not next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    assert next_auto.main(["--root", str(repo), "revert", "1"]) == 0
    first_patch = run_dir / "step-1-attempt-1.patch"
    first = first_patch.read_text()
    assert "test_vacuous" in first
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    next_auto.set_step(repo, 1, "done", "2 tests green, mutation killed both ways")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    assert next_auto.main(verdict_argv(repo, 1, head_sha(wt), 1, "NeedsFixes", report)) == 0
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    fix_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    next_auto.set_step(repo, 1, "done", "3 tests green, fix round")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    assert next_auto.main(verdict_argv(repo, 1, head_sha(wt), 2, "NeedsFixes", report)) == 0
    assert next_auto.main(["--root", str(repo), "revert", "1"]) == 0
    capsys.readouterr()
    assert head_sha(wt) == origin["base_head"]
    assert first_patch.read_text() == first
    whole = (run_dir / "step-1-attempt-2.patch").read_text()
    assert "FEATURE = 42" in whole and "FIXED = True" in whole


def test_r12_pr_opened_pushes_to_the_prs_head_branch(tmp_path, capsys, monkeypatch):
    """ai-dlc-record R12: the second push goes to the PR's headRefName, not the local name."""
    repo, _control = record_repo(tmp_path)
    origin = tmp_path / "origin.git"
    git(repo, "checkout", "-qb", "local-name")
    good_step(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "feat(step-1): first")
    git(repo, "push", "-q", "origin", "local-name:pr-head")
    opened_on = head_sha(repo)
    monkeypatch.setattr(next_auto, "gh", gh_pr_head([], opened_on, name="pr-head"))
    assert next_auto.main(["--root", str(repo), "pr-opened", "9"]) == 0
    capsys.readouterr()
    assert git(origin, "rev-parse", "refs/heads/pr-head").stdout.strip() == head_sha(repo)
    missing = git(origin, "rev-parse", "--verify", "--quiet", "refs/heads/local-name", check=False)
    assert missing.returncode != 0


def test_r12_pr_opened_fails_when_the_append_changed_nothing(tmp_path, capsys, monkeypatch):
    repo, control = record_repo(tmp_path)
    opened_on = pr_branch(repo)
    (control / "append-silent").write_text("")
    monkeypatch.setattr(next_auto, "gh", gh_pr_head([], opened_on))
    rc = next_auto.main(["--root", str(repo), "pr-opened", "9"])
    assert rc == 2
    assert capsys.readouterr().err.strip() == (
        "PR-OPENED FAILED PR #9: pr.opened append exited 0 but the record did not change"
    )
    assert head_sha(repo) == opened_on


def test_r11_result_file_counts_passed_apart_from_collected(tmp_path):
    """ai-dlc-record R11: tests=<passed>/<collected>, here with a skipped test on the base."""
    repo, _ = make_repo(tmp_path)
    (repo / "tests" / "test_later.py").write_text(
        "import pytest\n\n\n@pytest.mark.skip\ndef test_later():\n    assert True\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "a skipped test")
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0] == 0
    good_step(repo)
    assert next_auto.check_step(repo, 1)[0] == 0
    result = json.loads(attended_result(repo).read_text())
    assert (result["passed"], result["collected"]) == (2, 3)


def test_r11_a_gate_that_cannot_read_the_implementer_definition_is_apparatus(
    tmp_path, capsys, monkeypatch
):
    """ai-dlc-record R11: one line, exit 2, no traceback, and no result file."""
    monkeypatch.setattr(next_auto, "PLUGIN_ROOT", tmp_path / "no-plugin")
    monkeypatch.setattr(next_auto, "plan_commands", lambda worktree, plan: (TEST_CMD, LINT_OK))
    repo, wt = start_wt(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    rc = next_auto.main(["--root", str(repo), "gate", "1"])
    err = capsys.readouterr().err
    assert rc == 2
    assert err.count("\n") == 1 and "Traceback" not in err
    assert err.startswith("GATE APPARATUS step 1: cannot read the next-implementer definition: ")
    assert not (Path(state["run_dir"]) / "result-1.json").exists()


def test_r11_a_verdict_that_cannot_read_the_reviewer_definition_is_apparatus(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(next_auto, "PLUGIN_ROOT", tmp_path / "no-plugin")
    repo, _ = make_repo(tmp_path)
    sha = head_sha(repo)
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    rc = next_auto.main(verdict_argv(repo, 1, sha, 1, "Approved", report))
    err = capsys.readouterr().err
    assert rc == 2 and err.count("\n") == 1 and "Traceback" not in err
    assert err.startswith("VERDICT APPARATUS step 1: cannot read the next-reviewer definition: ")
    assert not (repo / ".next-auto" / "attended" / f"verdict-{sha}.json").exists()



# ---- ai-dlc-record review round 2: the untouched index, the record in the tree, vetoes --------


def test_r11_check_never_rewrites_the_real_index(tmp_path):
    """ai-dlc-record R11: check N stages into its own index, so the real one keeps its bytes
    and its mtime, the timestamp git's racy-entry check reads."""
    repo = begun(tmp_path)
    good_step(repo)
    index = repo / ".git" / "index"
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    time.sleep(1.1)
    assert next_auto.check_step(repo, 1)[0] == 0
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before
    assert not list((repo / ".next-auto" / "attended").glob("index-*"))


def test_r11_a_same_size_edit_in_the_index_entrys_second_reaches_check_and_commit(
    tmp_path, capsys
):
    """ai-dlc-record R11: the real index holds src.py with a past mtime shared by the index
    file itself, as a fast add leaves them; a same-size edit backdated into that mtime is in
    the tree check N hashes and in the tree commit N commits. Clearing core.trustctime stands
    in for an edit whose ctime lands in the entry's clock tick, which os.utime cannot set."""
    repo, _control = record_repo(tmp_path)
    git(repo, "config", "core.trustctime", "false")
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0] == 0
    good_step(repo)
    src = repo / "src.py"
    past = (int(time.time()) - 30) * 10**9
    os.utime(src, ns=(past, past))
    git(repo, "add", "-A")
    os.utime(repo / ".git" / "index", ns=(past, past))
    text = src.read_text()
    assert "FEATURE = 42\n" in text
    edited = text.replace("FEATURE = 42\n", "FEATURE= 42 \n")
    assert len(edited) == len(text)
    src.write_text(edited)
    os.utime(src, ns=(past, past))
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    set_done(repo, 1, "2 tests green, CHECK PASS")
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert "FEATURE= 42 \n" in git(repo, "show", "HEAD:src.py").stdout


def test_r12_commit_refuses_a_record_line_appended_after_check(tmp_path, capsys):
    """ai-dlc-record R11, R12: the tree hash keeps the record, since nothing but the engine's
    append after commit may write it; only the plan is left out."""
    repo, control = record_repo(tmp_path)
    attended_pass(repo)
    set_done(repo, 1, "2 tests green, CHECK PASS")
    with (repo / RECORD_FILE).open("a") as fh:
        fh.write("- a line written after the check\n")
    before = head_sha(repo)
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 1
    assert capsys.readouterr().err.strip() == (
        "COMMIT REFUSED step 1: the tree changed since check 1 passed; run check 1 again"
    )
    assert head_sha(repo) == before and appends(control) == []


@pytest.mark.parametrize("where", ["same-commit", "record-only-above"])
def test_r12_a_rejected_done_check_on_the_chain_refuses_the_merge(tmp_path, monkeypatch, where):
    """ai-dlc-record R12: a done check that REJECTED the claim vetoes the merge wherever it
    sits on the walked chain, whatever else approves."""
    repo, reviewed = merge_ready(tmp_path)
    pr_head = noted(repo, record_only_commit(repo, "- after\n"))
    rejected = reviewed if where == "same-commit" else pr_head
    write_done(repo, rejected, verdict="REJECTED")
    monkeypatch.setattr(next_auto, "gh", merge_gh([], green_pr(pr_head)))
    rc, lines = next_auto.merge_pr(repo, 7, **merge_kwargs())
    assert (rc, lines) == (1, [f"MERGE REFUSED PR #7: done check REJECTED at {rejected[:8]}"])


def test_r12_run_commit_reports_a_corrupt_result_file_on_one_line(tmp_path, capsys):
    repo, wt, _control = record_run(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    (Path(state["run_dir"]) / "result-1.json").write_text("{not json")
    before = head_sha(wt)
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    err = capsys.readouterr().err
    assert rc == 2 and err.count("\n") == 1 and "Traceback" not in err
    assert err.startswith("COMMIT APPARATUS step 1: ")
    assert head_sha(wt) == before


def test_r12_a_runs_appender_is_origins_default_branch_not_a_local_head(tmp_path, capsys):
    """ai-dlc-record R12: in a run the appender is origin/<default>'s, never the worktree's
    HEAD nor the local default branch, each of which here carries a forged appender."""
    repo, wt, control = record_run(tmp_path)
    forged = FORGED_STUB.replace("@PYTHON@", sys.executable)
    for tree in (wt, repo):
        (tree / "tools" / "record-stub").write_text(forged)
        git(tree, "commit", "-qam", "a forged appender")
    sha = head_sha(wt)
    report = tmp_path / "reviewer-report.md"
    report.write_text(REPORT)
    assert next_auto.main(verdict_argv(repo, 1, sha, 1, "NeedsFixes", report)) == 0
    capsys.readouterr()
    record = (wt / RECORD_FILE).read_text()
    assert f"- stub review.verdict {sha}\n" in record and "- forged" not in record
    assert len(appends(control)) == 1


def test_r12_tracked_file_takes_exactly_one_file_and_never_a_directory(tmp_path):
    """ai-dlc-record R12: only a word naming one tracked file is rewritten to the scratch."""
    repo, _control = record_repo(tmp_path)
    assert next_auto._tracked_file(repo, "tools/record-stub") == "tools/record-stub"
    assert next_auto._tracked_file(repo, "tools") is None
    (repo / "tools" / "second").write_text("x\n")
    git(repo, "add", "tools/second")
    assert next_auto._tracked_file(repo, "tools") is None
    assert next_auto._tracked_file(repo, "node") is None


@pytest.mark.parametrize("files", [1, 2])
def test_r12_tracked_file_refuses_a_directory_the_worktree_deleted(tmp_path, files):
    """ai-dlc-record R12: a tracked directory gone from the working tree is no directory to
    is_dir, so the name git lists must be the word itself: one listed name stops nothing,
    and one file under the word is still not the word."""
    repo, _control = record_repo(tmp_path)
    if files == 2:
        (repo / "tools" / "second").write_text("x\n")
        git(repo, "add", "tools/second")
        git(repo, "commit", "-qm", "a second tool")
    shutil.rmtree(repo / "tools")
    assert next_auto._tracked_file(repo, "tools") is None


def done_verdict_argv(repo, sha, verdict, model, report):
    return [
        "--root", str(repo), "done-verdict", "--head", sha, "--verdict", verdict,
        "--model", model, "--report", str(report),
    ]  # fmt: skip


def test_r12_done_verdict_writes_the_done_file_and_appends_done_verdict(tmp_path, capsys):
    """ai-dlc-record R12: `done-verdict` writes done-<sha>.json with the done check's reviewer
    and the model the session names, keeps the report as done-<sha>.md, and appends
    done.verdict through the default branch's appender; the merge gate then accepts it."""
    repo, control = record_repo(tmp_path)
    sha = head_sha(repo)
    report = tmp_path / "done-report.md"
    report.write_text("APPROVED: the claim holds\n")
    rc = next_auto.main(done_verdict_argv(repo, sha, "APPROVED", "opus", report))
    out = capsys.readouterr()
    assert rc == 0, out.err
    attended = repo / ".next-auto" / "attended"
    done = attended / f"done-{sha}.json"
    assert out.out.strip() == f"DONE-VERDICT APPROVED {done}"
    assert json.loads(done.read_text()) == {
        "head": sha,
        "verdict": "APPROVED",
        "reviewer": "done-check",
        "model": "opus",
    }
    assert list(json.loads(done.read_text())) == evidence_keys("done.json")
    assert (attended / f"done-{sha}.md").read_text() == "APPROVED: the claim holds\n"
    [call] = appends(control)
    assert call["argv"] == [
        "append", RECORD_SLUG, "done.verdict", "--evidence", str(done),
        "--report", str(attended / f"done-{sha}.md"),
    ]  # fmt: skip
    assert "next-auto-scratch-" in call["script"]


def test_r12_done_verdict_refuses_a_model_outside_the_three_names(tmp_path, capsys):
    repo, control = record_repo(tmp_path)
    report = tmp_path / "done-report.md"
    report.write_text("APPROVED\n")
    with pytest.raises(SystemExit) as exc:
        next_auto.main(done_verdict_argv(repo, head_sha(repo), "APPROVED", "gpt", report))
    assert exc.value.code == 2
    assert "invalid choice: 'gpt'" in capsys.readouterr().err
    assert stub_calls(control) == []


def test_r12_done_verdict_without_record_writes_the_file_and_appends_nothing(tmp_path, capsys):
    repo, control = record_repo(tmp_path, plan_text=PLAN)
    sha = head_sha(repo)
    report = tmp_path / "done-report.md"
    report.write_text("REJECTED: two claims fail\n")
    assert next_auto.main(done_verdict_argv(repo, sha, "REJECTED", "sonnet", report)) == 0
    capsys.readouterr()
    done = json.loads((repo / ".next-auto" / "attended" / f"done-{sha}.json").read_text())
    assert done["verdict"] == "REJECTED" and done["model"] == "sonnet"
    assert stub_calls(control) == []


# ---- ai-dlc-record review round 3: the check index seeded from the real one ------------------


def test_r11_a_path_the_step_untracked_is_out_of_both_trees(tmp_path, capsys):
    """ai-dlc-record R11: the step untracks a committed file and ignores it; check N seeds its
    index from the real index's entries, so the checked tree and the committed one both drop
    it and commit N goes through."""
    repo, _control = record_repo(tmp_path)
    (repo / "gen.txt").write_text("generated\n")
    git(repo, "add", "gen.txt")
    git(repo, "commit", "-qm", "a generated file")
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0] == 0
    good_step(repo)
    git(repo, "rm", "-q", "--cached", "gen.txt")
    (repo / ".gitignore").write_text("gen.txt\n")
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    set_done(repo, 1, "2 tests green, CHECK PASS")
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert git(repo, "ls-tree", "--name-only", "HEAD", "gen.txt").stdout == ""
    assert (repo / "gen.txt").is_file()


def test_r11_a_path_the_step_force_added_is_in_both_trees(tmp_path, capsys):
    """ai-dlc-record R11: the step force-adds a file under an ignored path; the real index has
    it, so the checked tree carries it too and commit N goes through with it."""
    repo, _control = record_repo(tmp_path)
    (repo / ".gitignore").write_text("build/\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-qm", "ignore build output")
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0] == 0
    good_step(repo)
    (repo / "build").mkdir()
    (repo / "build" / "keep.txt").write_text("kept on purpose\n")
    git(repo, "add", "-f", "build/keep.txt")
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    set_done(repo, 1, "2 tests green, CHECK PASS")
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert git(repo, "show", "HEAD:build/keep.txt").stdout == "kept on purpose\n"


def test_r12_done_verdict_refuses_a_head_that_is_not_a_commit_sha(tmp_path, capsys):
    repo, control = record_repo(tmp_path)
    report = tmp_path / "done-report.md"
    report.write_text("APPROVED\n")
    # seven hex characters that name no commit: the repository, not the shape, refuses it
    assert next_auto.main(done_verdict_argv(repo, "abc1234", "APPROVED", "opus", report)) == 1
    assert capsys.readouterr().err.strip() == (
        "DONE-VERDICT REFUSED: abc1234 is not a commit of this repository"
    )
    assert not list((repo / ".next-auto" / "attended").glob("done-*"))
    assert stub_calls(control) == []


def test_r12_done_verdict_refuses_a_verdict_outside_approved_and_rejected(tmp_path, capsys):
    repo, control = record_repo(tmp_path)
    report = tmp_path / "done-report.md"
    report.write_text("APPROVED\n")
    with pytest.raises(SystemExit) as exc:
        next_auto.main(done_verdict_argv(repo, head_sha(repo), "Approved", "opus", report))
    assert exc.value.code == 2
    assert "invalid choice: 'Approved'" in capsys.readouterr().err
    assert stub_calls(control) == []


def test_r14_check_on_a_record_at_two_failures_runs_nothing(tmp_path):
    """ai-dlc-record R14: the batch has stopped; a third check prints the stop line again and
    runs nothing, so a missing Test program is not reached."""
    repo = begun(tmp_path)
    good_step(repo)
    (repo / ".env").write_text("TOKEN=abc\n")
    next_auto.check_step(repo, 1)
    next_auto.check_step(repo, 1)
    data = json.loads(attended_record(repo).read_text())
    data["test_cmd"] = ["no-such-program-xyz-123"]
    attended_record(repo).write_text(json.dumps(data))
    assert next_auto.check_step(repo, 1) == (
        1,
        ["CHECK FAIL step 1: second failure; stop the batch"],
    )
    assert json.loads(attended_record(repo).read_text())["failures"] == 2


# ---- ai-dlc-record after PR 2's validation: the engine appends the plan's approval -----------


def plan_approved_argv(repo, by="ryanmat"):
    return ["--root", str(repo), "plan-approved", "--by", by]


def test_r13_plan_approved_appends_through_the_default_branchs_appender(tmp_path, capsys):
    """ai-dlc-record R13: `plan-approved --by <login>` appends plan.approved for the plan
    through the appender committed on the default branch, so an edit to the tree's copy never
    runs, and prints the hash it approved."""
    repo, control = record_repo(tmp_path)
    (repo / "tools" / "record-stub").write_text(FORGED_STUB.replace("@PYTHON@", sys.executable))
    rc = next_auto.main(plan_approved_argv(repo))
    out = capsys.readouterr()
    assert rc == 0, out.err
    expected = next_plan.plan_hash((repo / "prompt_plan.md").read_text())
    assert out.out.strip() == f"PLAN-APPROVED {expected} by ryanmat"
    [call] = appends(control)
    assert call["argv"] == [
        "append", RECORD_SLUG, "plan.approved", "--plan", "prompt_plan.md", "--by", "ryanmat",
    ]  # fmt: skip
    assert Path(call["cwd"]) == repo.resolve() and "next-auto-scratch-" in call["script"]
    record = (repo / RECORD_FILE).read_text()
    assert record.endswith("- stub plan.approved ryanmat\n") and "- forged" not in record


def test_r13_plan_approved_refuses_a_plan_without_record(tmp_path, capsys):
    repo, control = record_repo(tmp_path, plan_text=PLAN)
    assert next_auto.main(plan_approved_argv(repo)) == 1
    err = capsys.readouterr().err
    assert err.strip() == "PLAN-APPROVED REFUSED: prompt_plan.md carries no Record"
    assert stub_calls(control) == []


@pytest.mark.parametrize("by", ["not a login", "user@mat", ""])
def test_r13_plan_approved_refuses_a_by_that_is_not_a_login(tmp_path, capsys, by):
    repo, control = record_repo(tmp_path)
    assert next_auto.main(plan_approved_argv(repo, by)) == 1
    err = capsys.readouterr().err
    assert err.strip() == f"PLAN-APPROVED REFUSED: --by {by!r} is not a GitHub login"
    assert stub_calls(control) == []


def test_r13_plan_approved_without_the_record_file_is_apparatus(tmp_path, capsys):
    repo, control = record_repo(tmp_path)
    (repo / RECORD_FILE).unlink()
    assert next_auto.main(plan_approved_argv(repo)) == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith(f"PLAN-APPROVED APPARATUS: no record at {RECORD_FILE}; run ")
    assert err.endswith(f" init {RECORD_SLUG} first")
    assert stub_calls(control) == []


@pytest.mark.parametrize("failure", ["refused", "silent"])
def test_r13_plan_approved_reports_an_append_that_failed_or_changed_nothing(
    tmp_path, capsys, failure
):
    repo, control = record_repo(tmp_path)
    if failure == "refused":
        stub_rc(control, "append", 1)
    else:
        (control / "append-silent").write_text("")
    assert next_auto.main(plan_approved_argv(repo)) == 2
    err = capsys.readouterr().err.strip()
    detail = (
        "exited 1: stub append refused"
        if failure == "refused"
        else "exited 0 but the record did not change"
    )
    assert err == f"PLAN-APPROVED FAILED: plan.approved append {detail}"


# ---- 3.4.7: prove-red and module resolution (docs/prove-red-resolution-spec.md) ------------
# Each fixture is a live run on devbox (pytest 9 on Python 3.14, Node 22.23.2, Bun 1.4.2) with the
# run's root replaced by <root>: one test importing a module the base lacks per case.

PYTEST_RESOLUTION = """\
==================================== ERRORS ====================================
_____________________ ERROR collecting tests/test_name.py ______________________
ImportError while importing test module '<root>/tests/test_name.py'.
tests/test_name.py:1: in <module>
    from src import FEATURE
E   ImportError: cannot import name 'FEATURE' from 'src' (<root>/src.py)
______________________ ERROR collecting tests/test_own.py ______________________
ImportError while importing test module '<root>/tests/test_own.py'.
tests/test_own.py:1: in <module>
    from newmod import thing
E   ModuleNotFoundError: No module named 'newmod'
_____________________ ERROR collecting tests/test_third.py _____________________
ImportError while importing test module '<root>/tests/test_third.py'.
tests/test_third.py:1: in <module>
    import nonexistent_pkg
E   ModuleNotFoundError: No module named 'nonexistent_pkg'
=========================== short test summary info ============================
ERROR tests/test_name.py
ERROR tests/test_own.py
ERROR tests/test_third.py
!!!!!!!!!!!!!!!!!!! Interrupted: 3 errors during collection !!!!!!!!!!!!!!!!!!!!
3 errors in 0.07s
"""
NODE_RESOLUTION = (
    "TAP version 13\n"
    "# Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'missing-pkg' imported from "
    "<root>/test/a.test.mjs\n"
    """\
#   code: 'ERR_MODULE_NOT_FOUND'
# Subtest: test/a.test.mjs
not ok 1 - test/a.test.mjs
  ---
  failureType: 'testCodeFailure'
  code: 'ERR_TEST_FAILURE'
  ...
"""
    "# Error [ERR_MODULE_NOT_FOUND]: Cannot find module '<root>/test/missing.mjs' imported from "
    "<root>/test/b.test.mjs\n"
    """\
#   code: 'ERR_MODULE_NOT_FOUND',
#   url: 'file://<root>/test/missing.mjs'
# Subtest: test/b.test.mjs
not ok 2 - test/b.test.mjs
  ---
  failureType: 'testCodeFailure'
  code: 'ERR_TEST_FAILURE'
  ...
1..2
# tests 2
# suites 0
# pass 0
# fail 2
# cancelled 0
# skipped 0
# todo 0
# duration_ms 70.1
"""
)
BUN_RESOLUTION = """\
bun test v1.4.2 (744846f84)

a.test.ts:

# Unhandled error between tests
-------------------------------
error: Cannot find package 'zz-acme-no-such-pkg-4471' from '<root>/a.test.ts'
-------------------------------


b.test.ts:

# Unhandled error between tests
-------------------------------
error: Cannot find module './missing' from '<root>/b.test.ts'
-------------------------------


 0 pass
 2 fail
 2 errors
Ran 2 tests across 2 files. [14.00ms]
"""


def _unresolved(text, root, changed):
    """foreign_resolution with the scratch and the step's tree both at root."""
    return next_auto.foreign_resolution(
        text.replace("<root>", str(root)), root, set(changed), root
    )


def _pytest_only(name):
    """PYTEST_RESOLUTION cut to the one collection error of tests/<name>, as its own run."""
    lines = PYTEST_RESOLUTION.splitlines(keepends=True)
    start = next(i for i, ln in enumerate(lines) if f"ERROR collecting tests/{name}" in ln)
    end = next(i for i in range(start + 1, len(lines)) if lines[i][:1] in "_=")
    return "".join(lines[start:end]) + "1 error in 0.05s\n"


def test_r4_pytest_a_third_party_module_is_foreign(tmp_path):
    assert "'nonexistent_pkg'" in (_unresolved(_pytest_only("test_third.py"), tmp_path, []) or "")


@pytest.mark.parametrize(
    ("added", "own"),
    [
        (["newmod.py"], True),
        (["newmod/__init__.py"], True),
        (["src/newmod.py"], True),
        (["lib/newmod.py"], False),
        ([], False),
    ],
    ids=["top-level", "package", "src-layout", "other-layout", "not-added"],
)
def test_r3_pytest_a_dotted_name_is_the_steps_own_at_the_top_level_or_under_src(
    tmp_path, added, own
):
    got = _unresolved(_pytest_only("test_own.py"), tmp_path, added)
    assert (got is None) if own else ("'newmod'" in got)


@pytest.mark.parametrize(
    ("pythonpath", "added", "own"),
    [
        ('pythonpath = ["scripts"]', ["scripts/newmod.py"], True),
        ('pythonpath = ["scripts", "tools"]', ["tools/newmod/__init__.py"], True),
        ('pythonpath = ["scripts"]', ["lib/newmod.py"], False),
        ("", ["scripts/newmod.py"], False),
    ],
    ids=["scripts-root", "second-root-package", "root-not-declared", "no-pythonpath"],
)
def test_r3_pytest_a_dotted_name_is_the_steps_own_under_a_declared_pythonpath_root(
    tmp_path, pythonpath, added, own
):
    # the tree's pyproject declares the roots pytest imports from, as a plugin's layout does
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n{pythonpath}\n"
    )
    got = _unresolved(_pytest_only("test_own.py"), tmp_path, added)
    assert (got is None) if own else ("'newmod'" in got)


@pytest.mark.parametrize(
    ("fixture", "added", "foreign"),
    [
        (NODE_RESOLUTION, ["test/missing.mjs"], "'missing-pkg'"),
        (NODE_RESOLUTION, [], "'missing-pkg'"),
        (BUN_RESOLUTION, ["missing.ts"], "'zz-acme-no-such-pkg-4471'"),
    ],
    ids=["node-package", "node-both", "bun-package"],
)
def test_r4_a_package_the_base_cannot_load_is_foreign(tmp_path, fixture, added, foreign):
    assert foreign in (_unresolved(fixture, tmp_path, added) or "")


@pytest.mark.parametrize(
    ("fixture", "drop", "added"),
    [
        (NODE_RESOLUTION, "a.test.mjs", ["test/missing.mjs"]),
        (BUN_RESOLUTION, "a.test.ts", ["missing.ts"]),
        (BUN_RESOLUTION, "a.test.ts", ["missing/index.ts"]),
    ],
    ids=["node-relative", "bun-relative", "bun-index"],
)
def test_r3_a_relative_module_the_step_adds_keeps_the_red(tmp_path, fixture, drop, added):
    # the same runs with only the relative import left: the step adds the module it names
    if fixture is NODE_RESOLUTION:
        head, _, rest = fixture.partition("# Error [ERR_MODULE_NOT_FOUND]: Cannot find package")
        second = rest[rest.index("# Error [ERR_MODULE_NOT_FOUND]: Cannot find module") :]
        text = head + second.replace("not ok 2", "not ok 1").replace(
            "1..2\n# tests 2", "1..1\n# tests 1"
        ).replace("# fail 2", "# fail 1")
    else:
        text = (
            "b.test.ts:\n\n# Unhandled error between tests\n-------------------------------\n"
            "error: Cannot find module './missing' from '<root>/b.test.ts'\n"
            "-------------------------------\n\n\n"
            " 0 pass\n 1 fail\n 1 error\nRan 1 test across 1 file. [14.00ms]\n"
        )
    assert drop not in text
    assert _unresolved(text, tmp_path, added) is None
    # a module the tree holds and the step does not add, such as a generated one, is foreign
    (tmp_path / added[0]).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / added[0]).write_text("")
    assert "missing" in (_unresolved(text, tmp_path, []) or "")


def test_r4_a_run_with_an_assertion_failure_too_stands_as_red(tmp_path):
    # one more failed test than resolution failures: a real assertion fails on base as well
    text = BUN_RESOLUTION.replace(" 0 pass\n 2 fail\n", " 0 pass\n 3 fail\n").replace(
        "Ran 2 tests", "Ran 3 tests"
    )
    assert _unresolved(text, tmp_path, []) is None
    assert _unresolved(PYTEST_RESOLUTION, tmp_path, []) is None  # the name-level error is red


def _ignored_module_step(tree, exclude):
    """A step whose test imports a module the tree builds and git ignores: Test passes in the
    tree, and the base scratch, which holds only tracked files, cannot load it."""
    with exclude.open("a", encoding="utf-8") as f:
        f.write("genmod.py\n")
    (tree / "genmod.py").write_text("VALUE = 1\n")
    (tree / "tests" / "test_feature.py").write_text(
        "from genmod import VALUE\n\n\ndef test_feature():\n    assert VALUE == 1\n"
    )


def test_r4_check_stops_as_apparatus_on_a_red_made_only_of_a_module_the_scratch_lacks(tmp_path):
    repo = begun(tmp_path)
    _ignored_module_step(repo, repo / ".git" / "info" / "exclude")
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 2
    assert len(lines) == 1 and lines[0].startswith("CHECK APPARATUS step 1: prove-red:")
    assert "'genmod'" in lines[0]
    # an apparatus stop is not a red attempt
    record = json.loads((repo / ".next-auto" / "attended" / "step-1.json").read_text())
    assert record["failures"] == 0


def test_r4_check_counts_a_red_tree_beside_a_prove_red_apparatus(tmp_path):
    # the package is missing from the tree as well, so Test is red there: a dependency the step
    # forgot to install is its own failure and counts as an attempt
    repo = begun(tmp_path)
    (repo / "tests" / "test_feature.py").write_text(
        "import zz_acme_no_such_pkg\n\n\ndef test_feature():\n    assert True\n"
    )
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert any(ln.startswith("CHECK FAIL step 1: tests failed:") for ln in lines), lines
    assert any("prove-red:" in ln and "'zz_acme_no_such_pkg'" in ln for ln in lines), lines
    record = json.loads((repo / ".next-auto" / "attended" / "step-1.json").read_text())
    assert record["failures"] == 1


def test_r4_gate_fails_a_red_tree_beside_a_prove_red_apparatus(tmp_path):
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    (wt / "tests" / "test_feature.py").write_text(
        "import zz_acme_no_such_pkg\n\n\ndef test_feature():\n    assert True\n"
    )
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert not ok
    assert reason.startswith("Test is red after the step:")


@pytest.mark.parametrize("lint_rc", [0, 1], ids=["every-other-check-passes", "lint-fails"])
def test_r4_gate_stops_as_apparatus_only_when_every_other_check_passes(tmp_path, lint_rc):
    repo, wt = start_wt(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    _ignored_module_step(wt, repo / ".git" / "info" / "exclude")
    lint = [sys.executable, "-c", f"raise SystemExit({lint_rc})"]
    if lint_rc:
        ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=lint)
        assert not ok and reason.startswith("Lint failed")
    else:
        with pytest.raises(next_auto.ProveRedApparatusError, match="'genmod'"):
            next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=lint)
    assert not next_auto.run_result_path(next_auto.load_state(repo), 1).exists()


def test_r3_check_passes_a_red_made_of_a_module_the_step_adds(tmp_path):
    repo = begun(tmp_path)
    (repo / "tests" / "test_feature.py").write_text(
        "from newmod import THING\n\n\ndef test_feature():\n    assert THING == 42\n"
    )
    (repo / "newmod.py").write_text("# Description: fixture\n# Description: two\nTHING = 42\n")
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])


def test_r1_the_scratch_links_every_nested_install(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "infra").mkdir()
    (repo / "infra" / "package.json").write_text("{}\n")
    git(repo, "add", "infra/package.json")
    git(repo, "commit", "-qm", "a nested package")
    for rel in ("node_modules", "infra/node_modules", "untracked/node_modules"):
        (repo / rel / "pkg").mkdir(parents=True)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    with next_auto.scratch_worktree(repo, head) as scratch:
        next_auto.link_node_modules(repo, scratch)
        for rel in ("node_modules", "infra/node_modules"):
            assert (scratch / rel).is_symlink(), rel
            assert (scratch / rel).resolve() == (repo / rel).resolve()
        # a directory the base does not track gets no link and is not created
        assert not (scratch / "untracked").exists()


# ---- 3.4.7, review round 1: shared helpers, CommonJS, unread runners, the step's own install --
# live runs on devbox: two test files import one helper whose package the scratch lacks

BUN_SHARED_HELPER = """\
bun test v1.4.2 (744846f84)

test/b.test.ts:

# Unhandled error between tests
-------------------------------
error: Cannot find package 'zz-no-such-pkg-5512' from '<root>/test/helper.ts'
-------------------------------


test/a.test.ts:

# Unhandled error between tests
-------------------------------
-------------------------------


 0 pass
 2 fail
 2 errors
Ran 2 tests across 2 files. [13.00ms]
"""
# the same run with a third file whose assertion fails on base
BUN_SHARED_HELPER_AND_ASSERTION = BUN_SHARED_HELPER.replace(
    "\n 0 pass\n 2 fail\n 2 errors\nRan 2 tests across 2 files.",
    "\ntest/c.test.ts:\nerror: expect(received).toBe(expected)\n\nExpected: 2\nReceived: 1\n\n"
    "(fail) assert [0.13ms]\n\n 0 pass\n 3 fail\n 2 errors\n 1 expect() calls\n"
    "Ran 3 tests across 3 files.",
)
NODE_SHARED_HELPER = "".join(
    "# Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'zz-no-such-pkg-5512' imported from "
    f"<root>/test/helper.mjs\n# Subtest: test/{name}.test.mjs\nnot ok {n} - test/{name}.test.mjs\n"
    "  ---\n  failureType: 'testCodeFailure'\n  code: 'ERR_TEST_FAILURE'\n  ...\n"
    for n, name in ((1, "a"), (2, "b"))
) + "1..2\n# tests 2\n# suites 0\n# pass 0\n# fail 2\n# cancelled 0\n# skipped 0\n# todo 0\n"
NODE_COMMONJS = """\
TAP version 13
# Error: Cannot find module 'zz-no-such-pkg-5512'
# Require stack:
# - <root>/test/c.test.cjs
# Subtest: test/c.test.cjs
not ok 1 - test/c.test.cjs
  ---
  failureType: 'testCodeFailure'
  code: 'ERR_TEST_FAILURE'
  ...
1..1
# tests 1
# suites 0
# pass 0
# fail 1
# cancelled 0
# skipped 0
# todo 0
"""


@pytest.mark.parametrize(
    "fixture", [BUN_SHARED_HELPER, NODE_SHARED_HELPER, NODE_COMMONJS], ids=["bun", "node", "cjs"]
)
def test_r4_a_package_missing_behind_a_shared_helper_or_a_require_is_foreign(tmp_path, fixture):
    assert "'zz-no-such-pkg-5512'" in (_unresolved(fixture, tmp_path, []) or "")


def test_r4_a_shared_helper_beside_an_assertion_failure_stands_as_red(tmp_path):
    assert _unresolved(BUN_SHARED_HELPER_AND_ASSERTION, tmp_path, []) is None


def test_d3_output_from_a_runner_whose_summary_is_not_read_keeps_the_old_rule(tmp_path):
    # a resolution line in another runner's words, with no summary the engine reads
    jest = (
        "FAIL test/a.test.js\n  Cannot find module './x' from 'test/a.test.js'\nTests: 1 failed\n"
    )
    assert _unresolved(jest, tmp_path, []) is None


def _node_repo(tmp_path, test_glob):
    """make_repo with an ignored nested install the plan's node Test resolves from, a tracked
    package beside it, and the plan's Test-files naming node tests; begun at step 1."""
    repo, _ = make_repo(tmp_path)
    (repo / ".gitignore").write_text("node_modules/\n")
    (repo / "infra" / "lib").mkdir(parents=True)
    (repo / "infra" / "package.json").write_text('{"type": "module"}\n')
    (repo / "infra" / "lib" / "value.mjs").write_text("export const VALUE = 1;\n")
    pkg = repo / "infra" / "node_modules" / "fakepkg"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text('{"name": "fakepkg", "type": "module", "main": "index.js"}\n')
    (pkg / "index.js").write_text("export const OFFSET = 1;\n")
    plan = repo / "prompt_plan.md"
    plan.write_text(plan.read_text().replace("- Run:", "- Test-files: **/*.test.mjs\n- Run:"))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "a nested node package")
    cmd = ["node", "--test", test_glob]
    rc, line = next_auto.check_begin(repo, 1, test_cmd=cmd, lint_cmd=LINT_OK)
    assert rc == 0, line
    return repo


def test_r6_a_nested_install_lets_prove_red_read_the_assertion(tmp_path):
    repo = _node_repo(tmp_path, "infra/test/*.test.mjs")
    (repo / "infra" / "test").mkdir()
    (repo / "infra" / "test" / "value.test.mjs").write_text(
        'import test from "node:test";\nimport assert from "node:assert";\n'
        'import { OFFSET } from "fakepkg";\nimport { VALUE } from "../lib/value.mjs";\n'
        'test("value", () => { assert.equal(VALUE + OFFSET, 3); });\n'
    )
    (repo / "infra" / "lib" / "value.mjs").write_text("export const VALUE = 2;\n")
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])


def test_r1_a_package_the_step_adds_gets_its_own_install_linked(tmp_path):
    repo = _node_repo(tmp_path, "pkgs/new/test/*.test.mjs")
    new = repo / "pkgs" / "new"
    dep = new / "node_modules" / "fakedep"
    dep.mkdir(parents=True)
    (dep / "package.json").write_text('{"name": "fakedep", "type": "module", "main": "index.js"}\n')
    (dep / "index.js").write_text("export const ONE = 1;\n")
    (new / "package.json").write_text('{"type": "module"}\n')
    (new / "src").mkdir()
    (new / "src" / "x.mjs").write_text("export const X = 1;\n")
    (new / "test").mkdir()
    (new / "test" / "x.test.mjs").write_text(
        'import test from "node:test";\nimport assert from "node:assert";\n'
        'import { ONE } from "fakedep";\nimport { X } from "../src/x.mjs";\n'
        'test("x", () => { assert.equal(X, ONE); });\n'
    )
    # on base the step's own module is missing, which is red; its package's install is not
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])


def test_check_reports_a_staged_secret_beside_a_red_made_of_a_missing_package(tmp_path):
    repo = begun(tmp_path)
    (repo / "tests" / "test_feature.py").write_text(
        "import zz_acme_no_such_pkg\n\n\ndef test_feature():\n    assert True\n"
    )
    (repo / ".env").write_text("TOKEN=abc\n")
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert "CHECK FAIL step 1: staged a secret-shaped file: .env" in lines
    record = json.loads((repo / ".next-auto" / "attended" / "step-1.json").read_text())
    assert record["failures"] == 1


# a conftest that fails to import ends pytest before any summary (live, pytest 9, exit 4)
PYTEST_CONFTEST = """\
ImportError while loading conftest '<root>/conftest.py'.
conftest.py:1: in <module>
    import zz_no_such_pkg_7731
E   ModuleNotFoundError: No module named 'zz_no_such_pkg_7731'
"""


def test_r4_a_conftest_that_cannot_load_a_package_is_foreign(tmp_path):
    assert "'zz_no_such_pkg_7731'" in (_unresolved(PYTEST_CONFTEST, tmp_path, []) or "")


def test_d3_a_module_error_line_with_no_run_the_engine_reads_keeps_the_old_rule(tmp_path):
    text = PYTEST_CONFTEST.replace("ImportError while loading conftest", "Loading plugin")
    assert _unresolved(text, tmp_path, []) is None


# ---- 3.4.7, review round 2: Bun's failed tests and error count, node's failed entries ---------
# live runs on devbox (Node 22.23.2, Bun 1.4.2), each run's directory replaced by <root>

# a describe that awaits an import: the file's error counts under error, none under fail
BUN_LOAD_ERROR_ONLY = """\
bun test v1.4.2 (744846f84)

test/a.test.ts:

# Unhandled error between tests
-------------------------------
error: Cannot find package 'zz-no-such-pkg-14' from '<root>/test/a.test.ts'
-------------------------------


 0 pass
 0 fail
 1 error
Ran 0 tests across 1 file. [12.00ms]
"""
# an import the file never awaits fails between tests while another file's assertion fails
BUN_LOAD_ERROR_BESIDE_ASSERTION = """\
bun test v1.4.2 (744846f84)

test/a.test.ts:

# Unhandled error between tests
-------------------------------
error: Cannot find package 'zz-no-such-pkg-13' from '<root>/test/a.test.ts'
-------------------------------


test/c.test.ts:
1 | import { test, expect } from "bun:test";
2 | test("c", () => { expect(1).toBe(2); });
                                ^
error: expect(received).toBe(expected)

Expected: 2
Received: 1

      at <anonymous> (<root>/test/c.test.ts:2:29)
(fail) c [0.11ms]

 0 pass
 1 fail
 1 error
 1 expect() calls
Ran 1 test across 2 files. [12.00ms]
"""
# a test that awaits an import of a package the scratch lacks
BUN_IMPORT_IN_TEST = """\
bun test v1.4.2 (744846f84)

test/a.test.ts:
error: Cannot find package 'zz-no-such-pkg-15' from '<root>/test/a.test.ts'
(fail) dyn [0.22ms]

 0 pass
 1 fail
Ran 1 test across 1 file. [12.00ms]
"""
# a beforeAll that awaits the import, beside another file's assertion
BUN_IMPORT_IN_HOOK_BESIDE_ASSERTION = """\
bun test v1.4.2 (744846f84)

test/a.test.ts:
error: Cannot find package 'zz-no-such-pkg-14' from '<root>/test/a.test.ts'
(fail) (unnamed) [0.19ms]

test/c.test.ts:
1 | import { test, expect } from "bun:test";
2 | test("c", () => { expect(1).toBe(2); });
                                ^
error: expect(received).toBe(expected)

Expected: 2
Received: 1

      at <anonymous> (<root>/test/c.test.ts:2:29)
(fail) c [0.10ms]

 0 pass
 2 fail
 1 expect() calls
Ran 2 tests across 2 files. [12.00ms]
"""
# a passing file logs an optional import's failure; another file's assertion fails
NODE_WARNING_BESIDE_ASSERTION = """\
TAP version 13
# optional dep off: Cannot find package 'zz-optional-dep' imported from <root>/test/a.test.mjs
# Subtest: a
ok 1 - a
  ---
  duration_ms: 0.473106
  type: 'test'
  ...
# Subtest: c
not ok 2 - c
  ---
  duration_ms: 0.827929
  type: 'test'
  location: '<root>/test/c.test.mjs:3:1'
  failureType: 'testCodeFailure'
  error: '1 == 2'
  code: 'ERR_ASSERTION'
  name: 'AssertionError'
  expected: 2
  actual: 1
  operator: '=='
  stack: |-
    TestContext.<anonymous> (file://<root>/test/c.test.mjs:3:26)
    Test.runInAsyncScope (node:async_hooks:214:14)
    Test.run (node:internal/test_runner/test:1047:25)
    Test.start (node:internal/test_runner/test:944:17)
    startSubtestAfterBootstrap (node:internal/test_runner/harness:296:17)
  ...
1..2
# tests 2
# suites 0
# pass 1
# fail 1
# cancelled 0
# skipped 0
# todo 0
# duration_ms 49.908296
"""
# the same with the error object logged, which node prints as an Error block
NODE_LOGGED_ERROR_BESIDE_ASSERTION = (
    "TAP version 13\n"
    "# Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'zz-optional-dep' imported from "
    "<root>/test/a.test.mjs\n"
    r"""#     at Object.getPackageJSONURL (node:internal/modules/package_json_reader:314:9)
#     at packageResolve (node:internal/modules/esm/resolve:768:81)
#     at moduleResolve (node:internal/modules/esm/resolve:855:18)
#     at defaultResolve (node:internal/modules/esm/resolve:985:11)
#     at \#cachedDefaultResolve (node:internal/modules/esm/loader:747:20)
#     at ModuleLoader.resolve (node:internal/modules/esm/loader:724:38)
#     at ModuleLoader.getModuleJobForImport (node:internal/modules/esm/loader:320:38)
#     at onImport.tracePromise.__proto__ (node:internal/modules/esm/loader:680:36)
#     at TracingChannel.tracePromise (node:diagnostics_channel:350:14)
#     at ModuleLoader.import (node:internal/modules/esm/loader:679:21) {
#   code: 'ERR_MODULE_NOT_FOUND'
# }
# Subtest: a
ok 1 - a
  ---
  duration_ms: 0.478135
  type: 'test'
  ...
# Subtest: c
not ok 2 - c
  ---
  duration_ms: 0.892862
  type: 'test'
  location: '<root>/test/c.test.mjs:3:1'
  failureType: 'testCodeFailure'
  error: '1 == 2'
  code: 'ERR_ASSERTION'
  name: 'AssertionError'
  expected: 2
  actual: 1
  operator: '=='
  stack: |-
    TestContext.<anonymous> (file://<root>/test/c.test.mjs:3:26)
    Test.runInAsyncScope (node:async_hooks:214:14)
    Test.run (node:internal/test_runner/test:1047:25)
    Test.start (node:internal/test_runner/test:944:17)
    startSubtestAfterBootstrap (node:internal/test_runner/harness:296:17)
  ...
1..2
# tests 2
# suites 0
# pass 1
# fail 1
# cancelled 0
# skipped 0
# todo 0
# duration_ms 48.381999
"""
)
# a failing test's own file logs the error object of an import it tried
NODE_OWN_FILE_LOGGED_ERROR = (
    "TAP version 13\n"
    "# Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'zz-optional-dep' imported from "
    "<root>/test/c.test.mjs\n"
    r"""#     at Object.getPackageJSONURL (node:internal/modules/package_json_reader:314:9)
#     at packageResolve (node:internal/modules/esm/resolve:768:81)
#     at moduleResolve (node:internal/modules/esm/resolve:855:18)
#     at defaultResolve (node:internal/modules/esm/resolve:985:11)
#     at \#cachedDefaultResolve (node:internal/modules/esm/loader:747:20)
#     at ModuleLoader.resolve (node:internal/modules/esm/loader:724:38)
#     at ModuleLoader.getModuleJobForImport (node:internal/modules/esm/loader:320:38)
#     at onImport.tracePromise.__proto__ (node:internal/modules/esm/loader:680:36)
#     at TracingChannel.tracePromise (node:diagnostics_channel:350:14)
#     at ModuleLoader.import (node:internal/modules/esm/loader:679:21) {
#   code: 'ERR_MODULE_NOT_FOUND'
# }
# Subtest: c
not ok 1 - c
  ---
  duration_ms: 0.83866
  type: 'test'
  location: '<root>/test/c.test.mjs:4:1'
  failureType: 'testCodeFailure'
  error: '1 == 2'
  code: 'ERR_ASSERTION'
  name: 'AssertionError'
  expected: 2
  actual: 1
  operator: '=='
  stack: |-
    TestContext.<anonymous> (file://<root>/test/c.test.mjs:4:26)
    Test.runInAsyncScope (node:async_hooks:214:14)
    Test.run (node:internal/test_runner/test:1047:25)
    Test.start (node:internal/test_runner/test:944:17)
    startSubtestAfterBootstrap (node:internal/test_runner/harness:296:17)
  ...
1..1
# tests 1
# suites 0
# pass 0
# fail 1
# cancelled 0
# skipped 0
# todo 0
# duration_ms 46.949056
"""
)
# a test inside a describe awaits an import of a package the scratch lacks
NODE_IMPORT_IN_NESTED_TEST = """\
TAP version 13
# Subtest: d
    # Subtest: x
    not ok 1 - x
      ---
      duration_ms: 0.939749
      type: 'test'
      location: '<root>/test/a.test.mjs:2:23'
      failureType: 'testCodeFailure'
      error: "Cannot find package 'zz-no-such-pkg-15' imported from <root>/test/a.test.mjs"
      code: 'ERR_MODULE_NOT_FOUND'
      stack: |-
        Object.getPackageJSONURL (node:internal/modules/package_json_reader:314:9)
        packageResolve (node:internal/modules/esm/resolve:768:81)
        moduleResolve (node:internal/modules/esm/resolve:855:18)
        defaultResolve (node:internal/modules/esm/resolve:985:11)
        #cachedDefaultResolve (node:internal/modules/esm/loader:747:20)
        ModuleLoader.resolve (node:internal/modules/esm/loader:724:38)
        ModuleLoader.getModuleJobForImport (node:internal/modules/esm/loader:320:38)
        onImport.tracePromise.__proto__ (node:internal/modules/esm/loader:680:36)
        TracingChannel.tracePromise (node:diagnostics_channel:350:14)
        ModuleLoader.import (node:internal/modules/esm/loader:679:21)
      ...
    1..1
not ok 1 - d
  ---
  duration_ms: 1.583765
  type: 'suite'
  location: '<root>/test/a.test.mjs:2:1'
  failureType: 'subtestsFailed'
  error: '1 subtest failed'
  code: 'ERR_TEST_FAILURE'
  ...
1..1
# tests 1
# suites 1
# pass 0
# fail 1
# cancelled 0
# skipped 0
# todo 0
# duration_ms 47.108024
"""
# a CommonJS test requires a package the scratch lacks in its body
NODE_REQUIRE_IN_TEST = """\
TAP version 13
# Subtest: req
not ok 1 - req
  ---
  duration_ms: 0.606856
  type: 'test'
  location: '<root>/test/a.test.cjs:2:1'
  failureType: 'testCodeFailure'
  error: |-
    Cannot find module 'zz-no-such-pkg-16'
    Require stack:
    - <root>/test/a.test.cjs
  code: 'MODULE_NOT_FOUND'
  stack: |-
    Function._resolveFilename (node:internal/modules/cjs/loader:1430:15)
    defaultResolveImpl (node:internal/modules/cjs/loader:1040:19)
    resolveForCJSWithHooks (node:internal/modules/cjs/loader:1045:22)
    Function._load (node:internal/modules/cjs/loader:1216:25)
    wrapModuleLoad (node:internal/modules/cjs/loader:254:19)
    Module.require (node:internal/modules/cjs/loader:1527:12)
    require (node:internal/modules/helpers:147:16)
    TestContext.<anonymous> (<root>/test/a.test.cjs:2:21)
    Test.runInAsyncScope (node:async_hooks:214:14)
    Test.run (node:internal/test_runner/test:1047:25)
  ...
1..1
# tests 1
# suites 0
# pass 0
# fail 1
# cancelled 0
# skipped 0
# todo 0
# duration_ms 43.300977
"""

# a CommonJS helper requires a package the scratch lacks; each file that loads it crashes its
# test process, so node prints the error above the entry and the YAML says only "test failed"
NODE_COMMONJS_HELPER = """\
TAP version 13
# node:internal/modules/cjs/loader:1433
#   throw err;
#   ^
# Error: Cannot find module 'zz-no-such-pkg-4'
# Require stack:
# - <root>/test/helper.cjs
# - <root>/test/a.test.cjs
#     at Function._resolveFilename (node:internal/modules/cjs/loader:1430:15)
#     at defaultResolveImpl (node:internal/modules/cjs/loader:1040:19)
#     at resolveForCJSWithHooks (node:internal/modules/cjs/loader:1045:22)
#     at Function._load (node:internal/modules/cjs/loader:1216:25)
#     at wrapModuleLoad (node:internal/modules/cjs/loader:254:19)
#     at Module.require (node:internal/modules/cjs/loader:1527:12)
#     at require (node:internal/modules/helpers:147:16)
#     at Object.<anonymous> (<root>/test/helper.cjs:1:18)
#     at Module._compile (node:internal/modules/cjs/loader:1781:14)
#     at Object..js (node:internal/modules/cjs/loader:1913:10) {
#   code: 'MODULE_NOT_FOUND',
#   requireStack: [ '<root>/test/helper.cjs', '<root>/test/a.test.cjs' ]
# }
# Node.js v22.23.2
# Subtest: test/a.test.cjs
not ok 1 - test/a.test.cjs
  ---
  duration_ms: 25.320909
  type: 'test'
  location: '<root>/test/a.test.cjs:1:1'
  failureType: 'testCodeFailure'
  exitCode: 1
  signal: ~
  error: 'test failed'
  code: 'ERR_TEST_FAILURE'
  ...
# node:internal/modules/cjs/loader:1433
#   throw err;
#   ^
# Error: Cannot find module 'zz-no-such-pkg-4'
# Require stack:
# - <root>/test/helper.cjs
#     at Function._resolveFilename (node:internal/modules/cjs/loader:1430:15)
#     at defaultResolveImpl (node:internal/modules/cjs/loader:1040:19)
#     at resolveForCJSWithHooks (node:internal/modules/cjs/loader:1045:22)
#     at Function._load (node:internal/modules/cjs/loader:1216:25)
#     at wrapModuleLoad (node:internal/modules/cjs/loader:254:19)
#     at Module.require (node:internal/modules/cjs/loader:1527:12)
#     at require (node:internal/modules/helpers:147:16)
#     at Object.<anonymous> (<root>/test/helper.cjs:1:18)
#     at Module._compile (node:internal/modules/cjs/loader:1781:14)
#     at Object..js (node:internal/modules/cjs/loader:1913:10) {
#   code: 'MODULE_NOT_FOUND',
#   requireStack: [ '<root>/test/helper.cjs' ]
# }
# Node.js v22.23.2
# Subtest: test/helper.cjs
not ok 2 - test/helper.cjs
  ---
  duration_ms: 19.834429
  type: 'test'
  location: '<root>/test/helper.cjs:1:1'
  failureType: 'testCodeFailure'
  exitCode: 1
  signal: ~
  error: 'test failed'
  code: 'ERR_TEST_FAILURE'
  ...
1..2
# tests 2
# suites 0
# pass 0
# fail 2
# cancelled 0
# skipped 0
# todo 0
# duration_ms 34.902907
"""


@pytest.mark.parametrize(
    ("fixture", "pkg"),
    [
        (BUN_LOAD_ERROR_ONLY, "zz-no-such-pkg-14"),
        (BUN_IMPORT_IN_TEST, "zz-no-such-pkg-15"),
        (NODE_IMPORT_IN_NESTED_TEST, "zz-no-such-pkg-15"),
        (NODE_REQUIRE_IN_TEST, "zz-no-such-pkg-16"),
        (NODE_COMMONJS_HELPER, "zz-no-such-pkg-4"),
    ],
    ids=[
        "bun-error-count",
        "bun-in-a-test",
        "node-in-a-nested-test",
        "node-require-in-a-test",
        "node-commonjs-process",
    ],
)
def test_r4_a_package_missing_at_load_or_inside_a_test_is_foreign(tmp_path, fixture, pkg):
    assert f"'{pkg}'" in (_unresolved(fixture, tmp_path, []) or "")


@pytest.mark.parametrize(
    "fixture",
    [
        BUN_LOAD_ERROR_BESIDE_ASSERTION,
        BUN_IMPORT_IN_HOOK_BESIDE_ASSERTION,
        NODE_WARNING_BESIDE_ASSERTION,
        NODE_LOGGED_ERROR_BESIDE_ASSERTION,
        NODE_OWN_FILE_LOGGED_ERROR,
    ],
    ids=[
        "bun-unhandled",
        "bun-hook",
        "node-logged-message",
        "node-logged-error",
        "node-own-file-logged-error",
    ],
)
def test_r4_an_assertion_failure_beside_a_missing_module_stands_as_red(tmp_path, fixture):
    # the only counted failure is the assertion, or one of two is; a passing file's logged
    # resolution message is no failure at all
    assert _unresolved(fixture, tmp_path, []) is None


def test_r4_node_names_the_importer_without_the_yaml_quote(tmp_path):
    got = _unresolved(NODE_IMPORT_IN_NESTED_TEST, tmp_path, []) or ""
    assert "(imported from test/a.test.mjs)" in got


# ---- 3.4.7, OPEN 5 ruled (B): a missing module the step's tree does not hold is the step's -----
# live run on devbox (Node 22.23.2): the base's loader imports a module renamed away from it; the
# step's test exercises the loader, and the step points the loader at the module's new name
NODE_LOADER_RENAMED = """\
TAP version 13
# Subtest: load
not ok 1 - load
  ---
  duration_ms: 0.83302
  type: 'test'
  location: '<root>/test/loader.test.mjs:4:1'
  failureType: 'testCodeFailure'
  error: "Cannot find module '<root>/src/renamed.mjs' imported from <root>/src/loader.mjs"
  code: 'ERR_MODULE_NOT_FOUND'
  stack: |-
    finalizeResolution (node:internal/modules/esm/resolve:275:11)
    moduleResolve (node:internal/modules/esm/resolve:861:10)
    defaultResolve (node:internal/modules/esm/resolve:985:11)
    #cachedDefaultResolve (node:internal/modules/esm/loader:747:20)
    ModuleLoader.resolve (node:internal/modules/esm/loader:724:38)
    ModuleLoader.getModuleJobForImport (node:internal/modules/esm/loader:320:38)
    onImport.tracePromise.__proto__ (node:internal/modules/esm/loader:680:36)
    TracingChannel.tracePromise (node:diagnostics_channel:350:14)
    ModuleLoader.import (node:internal/modules/esm/loader:679:21)
    defaultImportModuleDynamicallyForModule (node:internal/modules/esm/utils:222:31)
  ...
1..1
# tests 1
# suites 0
# pass 0
# fail 1
# cancelled 0
# skipped 0
# todo 0
# duration_ms 47.277764
"""


def test_r3_a_relative_module_the_steps_tree_does_not_hold_keeps_the_red(tmp_path):
    changed = ["src/loader.mjs", "test/loader.test.mjs"]
    assert _unresolved(NODE_LOADER_RENAMED, tmp_path, changed) is None


def test_r3_a_relative_module_the_tree_holds_and_the_step_does_not_add_is_foreign(tmp_path):
    # a generated module, ignored and built in the tree, is one the scratch cannot load
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "renamed.mjs").write_text("export const V = 1;\n")
    got = _unresolved(NODE_LOADER_RENAMED, tmp_path, ["src/loader.mjs"]) or ""
    assert "renamed.mjs" in got


def test_r3_a_module_outside_the_scratch_is_never_the_steps(tmp_path):
    text = NODE_LOADER_RENAMED.replace(
        "'<root>/src/renamed.mjs'", "'/zz-outside-the-repo/renamed.mjs'"
    )
    assert "zz-outside-the-repo" in (_unresolved(text, tmp_path, []) or "")


def test_r3_check_passes_a_step_that_points_a_loader_at_a_renamed_module(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "package.json").write_text('{"type": "module"}\n')
    (repo / "lib").mkdir()
    (repo / "lib" / "real.mjs").write_text("export const V = 1;\n")
    (repo / "lib" / "loader.mjs").write_text(
        'export async function load() { return (await import("./renamed.mjs")).V; }\n'
    )
    plan = repo / "prompt_plan.md"
    plan.write_text(plan.read_text().replace("- Run:", "- Test-files: **/*.test.mjs\n- Run:"))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "a loader that names a module renamed away from it")
    cmd = ["node", "--test", "test/*.test.mjs"]
    rc, line = next_auto.check_begin(repo, 1, test_cmd=cmd, lint_cmd=LINT_OK)
    assert rc == 0, line
    (repo / "test").mkdir()
    (repo / "test" / "loader.test.mjs").write_text(
        'import test from "node:test";\nimport assert from "node:assert";\n'
        'import { load } from "../lib/loader.mjs";\n'
        'test("load", async () => { assert.equal(await load(), 1); });\n'
    )
    (repo / "lib" / "loader.mjs").write_text(
        'export async function load() { return (await import("./real.mjs")).V; }\n'
    )
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])


def test_r3_check_stops_on_a_generated_module_the_tree_holds_and_git_ignores(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "package.json").write_text('{"type": "module"}\n')
    plan = repo / "prompt_plan.md"
    plan.write_text(plan.read_text().replace("- Run:", "- Test-files: **/*.test.mjs\n- Run:"))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "a node package")
    cmd = ["node", "--test", "test/*.test.mjs"]
    rc, line = next_auto.check_begin(repo, 1, test_cmd=cmd, lint_cmd=LINT_OK)
    assert rc == 0, line
    with (repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as f:
        f.write("gen/\n")
    (repo / "gen").mkdir()
    (repo / "gen" / "api.mjs").write_text("export const API = 1;\n")
    (repo / "test").mkdir()
    (repo / "test" / "api.test.mjs").write_text(
        'import test from "node:test";\nimport assert from "node:assert";\n'
        'import { API } from "../gen/api.mjs";\ntest("api", () => { assert.equal(API, 1); });\n'
    )
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 2
    assert len(lines) == 1 and "gen/api.mjs" in lines[0], lines


# ---- 3.4.7, review round 3: todos, subtests, hooks, color, logged errors, the counts ----------
# live runs on devbox (Node 22.23.2, Bun 1.4.2), each run's directory replaced by <root>

# a failing todo beside a file that cannot load a package; node counts the todo apart from fail
NODE_TODO_BESIDE_MISSING = (
    "TAP version 13\n"
    "# Subtest: later\n"
    "not ok 1 - later # TODO\n"
    "  ---\n"
    "  duration_ms: 0.89179\n"
    "  type: 'test'\n"
    "  location: '<root>/test/a.test.mjs:3:6'\n"
    "  failureType: 'testCodeFailure'\n"
    "  error: '1 == 2'\n"
    "  code: 'ERR_ASSERTION'\n"
    "  name: 'AssertionError'\n"
    "  expected: 2\n"
    "  actual: 1\n"
    "  operator: '=='\n"
    "  stack: |-\n"
    "    TestContext.<anonymous> (file://<root>/test/a.test.mjs:3:35)\n"
    "    Test.runInAsyncScope (node:async_hooks:214:14)\n"
    "    Test.run (node:internal/test_runner/test:1047:25)\n"
    "    Test.start (node:internal/test_runner/test:944:17)\n"
    "    startSubtestAfterBootstrap (node:internal/test_runner/harness:296:17)\n"
    "  ...\n"
    "# Subtest: ok\n"
    "ok 2 - ok\n"
    "  ---\n"
    "  duration_ms: 0.129953\n"
    "  type: 'test'\n"
    "  ...\n"
    "# node:internal/modules/package_json_reader:314\n"
    "#   throw new ERR_MODULE_NOT_FOUND(packageName, fileURLToPath(base), null);\n"
    "#         ^\n"
    "# Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'zz-no-such-pkg-30' imported from <root>/t"
    "est/b.test.mjs\n"
    "#     at Object.getPackageJSONURL (node:internal/modules/package_json_reader:314:9)\n"
    "#     at packageResolve (node:internal/modules/esm/resolve:768:81)\n"
    "#     at moduleResolve (node:internal/modules/esm/resolve:855:18)\n"
    "#     at defaultResolve (node:internal/modules/esm/resolve:985:11)\n"
    "#     at \\#cachedDefaultResolve (node:internal/modules/esm/loader:747:20)\n"
    "#     at ModuleLoader.resolve (node:internal/modules/esm/loader:724:38)\n"
    "#     at ModuleLoader.getModuleJobForImport (node:internal/modules/esm/loader:320:38)\n"
    "#     at ModuleJob._link (node:internal/modules/esm/module_job:182:49) {\n"
    "#   code: 'ERR_MODULE_NOT_FOUND'\n"
    "# }\n"
    "# Node.js v22.23.2\n"
    "# Subtest: test/b.test.mjs\n"
    "not ok 2 - test/b.test.mjs\n"
    "  ---\n"
    "  duration_ms: 24.973478\n"
    "  type: 'test'\n"
    "  location: '<root>/test/b.test.mjs:1:1'\n"
    "  failureType: 'testCodeFailure'\n"
    "  exitCode: 1\n"
    "  signal: ~\n"
    "  error: 'test failed'\n"
    "  code: 'ERR_TEST_FAILURE'\n"
    "  ...\n"
    "1..3\n"
    "# tests 3\n"
    "# suites 0\n"
    "# pass 1\n"
    "# fail 1\n"
    "# cancelled 0\n"
    "# skipped 0\n"
    "# todo 1\n"
    "# duration_ms 45.18518\n"
)
# a test whose subtest imports a package the scratch lacks; node counts both under fail
NODE_SUBTEST_PARENT = (
    "TAP version 13\n"
    "# Subtest: p\n"
    "    # Subtest: c\n"
    "    not ok 1 - c\n"
    "      ---\n"
    "      duration_ms: 0.867755\n"
    "      type: 'test'\n"
    "      location: '<root>/test/a.test.mjs:3:11'\n"
    "      failureType: 'testCodeFailure'\n"
    "      error: \"Cannot find package 'zz-no-such-pkg-32' imported from <root>/test/a.test.mjs\""
    "\n"
    "      code: 'ERR_MODULE_NOT_FOUND'\n"
    "      stack: |-\n"
    "        Object.getPackageJSONURL (node:internal/modules/package_json_reader:314:9)\n"
    "        packageResolve (node:internal/modules/esm/resolve:768:81)\n"
    "        moduleResolve (node:internal/modules/esm/resolve:855:18)\n"
    "        defaultResolve (node:internal/modules/esm/resolve:985:11)\n"
    "        #cachedDefaultResolve (node:internal/modules/esm/loader:747:20)\n"
    "        ModuleLoader.resolve (node:internal/modules/esm/loader:724:38)\n"
    "        ModuleLoader.getModuleJobForImport (node:internal/modules/esm/loader:320:38)\n"
    "        onImport.tracePromise.__proto__ (node:internal/modules/esm/loader:680:36)\n"
    "        TracingChannel.tracePromise (node:diagnostics_channel:350:14)\n"
    "        ModuleLoader.import (node:internal/modules/esm/loader:679:21)\n"
    "      ...\n"
    "    1..1\n"
    "not ok 1 - p\n"
    "  ---\n"
    "  duration_ms: 1.383671\n"
    "  type: 'test'\n"
    "  location: '<root>/test/a.test.mjs:2:1'\n"
    "  failureType: 'subtestsFailed'\n"
    "  error: '1 subtest failed'\n"
    "  code: 'ERR_TEST_FAILURE'\n"
    "  ...\n"
    "1..1\n"
    "# tests 2\n"
    "# suites 0\n"
    "# pass 0\n"
    "# fail 2\n"
    "# cancelled 0\n"
    "# skipped 0\n"
    "# todo 0\n"
    "# duration_ms 46.090144\n"
)
# a describe whose before hook imports a missing package; its tests count as cancelled
NODE_BEFORE_HOOK_CANCELS = (
    "TAP version 13\n"
    "# Subtest: d\n"
    "    # Subtest: x\n"
    "    not ok 1 - x\n"
    "      ---\n"
    "      duration_ms: 0\n"
    "      type: 'test'\n"
    "      location: '<root>/test/a.test.mjs:5:3'\n"
    "      failureType: 'cancelledByParent'\n"
    "      error: 'test did not finish before its parent and was cancelled'\n"
    "      code: 'ERR_TEST_FAILURE'\n"
    "      ...\n"
    "    # Subtest: y\n"
    "    not ok 2 - y\n"
    "      ---\n"
    "      duration_ms: 0\n"
    "      type: 'test'\n"
    "      location: '<root>/test/a.test.mjs:6:3'\n"
    "      failureType: 'cancelledByParent'\n"
    "      error: 'test did not finish before its parent and was cancelled'\n"
    "      code: 'ERR_TEST_FAILURE'\n"
    "      ...\n"
    "    1..2\n"
    "not ok 1 - d\n"
    "  ---\n"
    "  duration_ms: 1.116281\n"
    "  type: 'suite'\n"
    "  location: '<root>/test/a.test.mjs:2:1'\n"
    "  failureType: 'hookFailed'\n"
    "  error: \"Cannot find package 'zz-no-such-pkg-33' imported from <root>/test/a.test.mjs\"\n"
    "  code: 'ERR_MODULE_NOT_FOUND'\n"
    "  stack: |-\n"
    "    Object.getPackageJSONURL (node:internal/modules/package_json_reader:314:9)\n"
    "    packageResolve (node:internal/modules/esm/resolve:768:81)\n"
    "    moduleResolve (node:internal/modules/esm/resolve:855:18)\n"
    "    defaultResolve (node:internal/modules/esm/resolve:985:11)\n"
    "    #cachedDefaultResolve (node:internal/modules/esm/loader:747:20)\n"
    "    ModuleLoader.resolve (node:internal/modules/esm/loader:724:38)\n"
    "    ModuleLoader.getModuleJobForImport (node:internal/modules/esm/loader:320:38)\n"
    "    onImport.tracePromise.__proto__ (node:internal/modules/esm/loader:680:36)\n"
    "    TracingChannel.tracePromise (node:diagnostics_channel:350:14)\n"
    "    ModuleLoader.import (node:internal/modules/esm/loader:679:21)\n"
    "  ...\n"
    "1..1\n"
    "# tests 2\n"
    "# suites 1\n"
    "# pass 0\n"
    "# fail 0\n"
    "# cancelled 2\n"
    "# skipped 0\n"
    "# todo 0\n"
    "# duration_ms 47.874896\n"
)
# a test that never settles, cancelled with no failed hook, beside a file missing a package
NODE_HANG_BESIDE_MISSING = (
    "TAP version 13\n"
    "# Subtest: hang\n"
    "not ok 1 - hang\n"
    "  ---\n"
    "  duration_ms: 2.383433\n"
    "  type: 'test'\n"
    "  location: '<root>/test/a.test.mjs:2:1'\n"
    "  failureType: 'cancelledByParent'\n"
    "  error: 'Promise resolution is still pending but the event loop has already resolved'\n"
    "  code: 'ERR_TEST_FAILURE'\n"
    "  stack: |-\n"
    "    process.emit (node:events:519:28)\n"
    "  ...\n"
    "# node:internal/modules/package_json_reader:314\n"
    "#   throw new ERR_MODULE_NOT_FOUND(packageName, fileURLToPath(base), null);\n"
    "#         ^\n"
    "# Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'zz-no-such-pkg-30' imported from <root>/t"
    "est/b.test.mjs\n"
    "#     at Object.getPackageJSONURL (node:internal/modules/package_json_reader:314:9)\n"
    "#     at packageResolve (node:internal/modules/esm/resolve:768:81)\n"
    "#     at moduleResolve (node:internal/modules/esm/resolve:855:18)\n"
    "#     at defaultResolve (node:internal/modules/esm/resolve:985:11)\n"
    "#     at \\#cachedDefaultResolve (node:internal/modules/esm/loader:747:20)\n"
    "#     at ModuleLoader.resolve (node:internal/modules/esm/loader:724:38)\n"
    "#     at ModuleLoader.getModuleJobForImport (node:internal/modules/esm/loader:320:38)\n"
    "#     at ModuleJob._link (node:internal/modules/esm/module_job:182:49) {\n"
    "#   code: 'ERR_MODULE_NOT_FOUND'\n"
    "# }\n"
    "# Node.js v22.23.2\n"
    "# Subtest: test/b.test.mjs\n"
    "not ok 2 - test/b.test.mjs\n"
    "  ---\n"
    "  duration_ms: 28.294548\n"
    "  type: 'test'\n"
    "  location: '<root>/test/b.test.mjs:1:1'\n"
    "  failureType: 'testCodeFailure'\n"
    "  exitCode: 1\n"
    "  signal: ~\n"
    "  error: 'test failed'\n"
    "  code: 'ERR_TEST_FAILURE'\n"
    "  ...\n"
    "1..2\n"
    "# tests 2\n"
    "# suites 0\n"
    "# pass 0\n"
    "# fail 1\n"
    "# cancelled 1\n"
    "# skipped 0\n"
    "# todo 0\n"
    "# duration_ms 46.990351\n"
)
# a file logs an optional import's failure, then crashes at load on a name the step adds
NODE_CRASH_AFTER_LOGGED_MESSAGE = (
    "TAP version 13\n"
    "# optional dep off: Cannot find package 'zz-optional-dep' imported from <root>/test/b.test.mjs"
    "\n"
    "# file://<root>/test/b.test.mjs:5\n"
    "# const thing = lib.makeThing();\n"
    "#                   ^\n"
    "# TypeError: lib.makeThing is not a function\n"
    "#     at file://<root>/test/b.test.mjs:5:19\n"
    "# Node.js v22.23.2\n"
    "# Subtest: test/b.test.mjs\n"
    "not ok 1 - test/b.test.mjs\n"
    "  ---\n"
    "  duration_ms: 29.13391\n"
    "  type: 'test'\n"
    "  location: '<root>/test/b.test.mjs:1:1'\n"
    "  failureType: 'testCodeFailure'\n"
    "  exitCode: 1\n"
    "  signal: ~\n"
    "  error: 'test failed'\n"
    "  code: 'ERR_TEST_FAILURE'\n"
    "  ...\n"
    "1..1\n"
    "# tests 1\n"
    "# suites 0\n"
    "# pass 0\n"
    "# fail 1\n"
    "# cancelled 0\n"
    "# skipped 0\n"
    "# todo 0\n"
    "# duration_ms 39.040482\n"
)
# the same with the error object logged: two error headers, the crash's the last
NODE_CRASH_AFTER_LOGGED_ERROR = (
    "TAP version 13\n"
    "# Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'zz-optional-dep' imported from <root>/tes"
    "t/b.test.mjs\n"
    "#     at Object.getPackageJSONURL (node:internal/modules/package_json_reader:314:9)\n"
    "#     at packageResolve (node:internal/modules/esm/resolve:768:81)\n"
    "#     at moduleResolve (node:internal/modules/esm/resolve:855:18)\n"
    "#     at defaultResolve (node:internal/modules/esm/resolve:985:11)\n"
    "#     at \\#cachedDefaultResolve (node:internal/modules/esm/loader:747:20)\n"
    "#     at ModuleLoader.resolve (node:internal/modules/esm/loader:724:38)\n"
    "#     at ModuleLoader.getModuleJobForImport (node:internal/modules/esm/loader:320:38)\n"
    "#     at onImport.tracePromise.__proto__ (node:internal/modules/esm/loader:680:36)\n"
    "#     at TracingChannel.tracePromise (node:diagnostics_channel:350:14)\n"
    "#     at ModuleLoader.import (node:internal/modules/esm/loader:679:21) {\n"
    "#   code: 'ERR_MODULE_NOT_FOUND'\n"
    "# }\n"
    "# file://<root>/test/b.test.mjs:5\n"
    "# const thing = lib.makeThing();\n"
    "#                   ^\n"
    "# TypeError: lib.makeThing is not a function\n"
    "#     at file://<root>/test/b.test.mjs:5:19\n"
    "# Node.js v22.23.2\n"
    "# Subtest: test/b.test.mjs\n"
    "not ok 1 - test/b.test.mjs\n"
    "  ---\n"
    "  duration_ms: 29.323626\n"
    "  type: 'test'\n"
    "  location: '<root>/test/b.test.mjs:1:1'\n"
    "  failureType: 'testCodeFailure'\n"
    "  exitCode: 1\n"
    "  signal: ~\n"
    "  error: 'test failed'\n"
    "  code: 'ERR_TEST_FAILURE'\n"
    "  ...\n"
    "1..1\n"
    "# tests 1\n"
    "# suites 0\n"
    "# pass 0\n"
    "# fail 1\n"
    "# cancelled 0\n"
    "# skipped 0\n"
    "# todo 0\n"
    "# duration_ms 39.932973\n"
)
# a file logs an optional import's failure, then throws a string at load: no error header
NODE_CRASH_ON_A_THROWN_STRING = (
    "TAP version 13\n"
    "# optional dep off: Cannot find package 'zz-optional-dep' imported from <root>/test/b.test.mjs"
    "\n"
    "# node:internal/modules/run_main:123\n"
    "#     triggerUncaughtException(\n"
    "#     ^\n"
    "# boom\n"
    "# (Use `node --trace-uncaught ...` to show where the exception was thrown)\n"
    "# Node.js v22.23.2\n"
    "# Subtest: test/b.test.mjs\n"
    "not ok 1 - test/b.test.mjs\n"
    "  ---\n"
    "  duration_ms: 27.751521\n"
    "  type: 'test'\n"
    "  location: '<root>/test/b.test.mjs:1:1'\n"
    "  failureType: 'testCodeFailure'\n"
    "  exitCode: 1\n"
    "  signal: ~\n"
    "  error: 'test failed'\n"
    "  code: 'ERR_TEST_FAILURE'\n"
    "  ...\n"
    "1..1\n"
    "# tests 1\n"
    "# suites 0\n"
    "# pass 0\n"
    "# fail 1\n"
    "# cancelled 0\n"
    "# skipped 0\n"
    "# todo 0\n"
    "# duration_ms 37.521317\n"
)
# a passing test logs an import's error; the next test times out with no error of its own
BUN_TIMEOUT_AFTER_LOGGED_ERROR = (
    "bun test v1.4.2 (744846f84)\n"
    "\n"
    "test/m.test.ts:\n"
    "error: Cannot find package 'zz-opt-41' from '<root>/test/m.test.ts'\n"
    "(fail) waits [50.08ms]\n"
    "  ^ this test timed out after 50ms.\n"
    "\n"
    " 1 pass\n"
    " 1 fail\n"
    "Ran 2 tests across 1 file. [62.00ms]\n"
)
# BUN_LOAD_ERROR_BESIDE_ASSERTION with FORCE_COLOR=1
BUN_LOAD_ERROR_BESIDE_ASSERTION_COLOR = (
    "\u001b[0m\u001b[1mbun test \u001b[0m\u001b[2mv1.4.2 (744846f84)\u001b[0m\n"
    "\u001b[0m\n"
    "test/a.test.ts:\n"
    "\u001b[0m\n"
    "\u001b[1m\u001b[2m#\u001b[0m \u001b[31m\u001b[1mUnhandled error\u001b[0m\u001b[2m between test"
    "s\u001b[0m\n"
    "\u001b[2m-------------------------------\u001b[0m\n"
    "\u001b[31merror\u001b[0m\u001b[2m: \u001b[0m\u001b[1mCannot find package 'zz-no-such-pkg-13' f"
    "rom '<root>/test/a.test.ts'\u001b[0m\n"
    "\u001b[0m\u001b[2m-------------------------------\u001b[0m\n"
    "\n"
    "\u001b[0m\n"
    "test/c.test.ts:\n"
    "\u001b[0m\u001b[1m1 |\u001b[0m \u001b[0m\u001b[35mimport\u001b[0m { test, expect } \u001b[0m"
    "\u001b[35mfrom\u001b[0m \u001b[0m\u001b[32m\"bun:test\"\u001b[0m\u001b[0m\u001b[2m;\u001b[0m\n"
    "\u001b[0m\u001b[1m2 |\u001b[0m test(\u001b[0m\u001b[32m\"c\"\u001b[0m, () => { expect(\u001b[0"
    "m\u001b[33m1\u001b[0m)\u001b[0m\u001b[3m\u001b[1m.toBe\u001b[0m(\u001b[0m\u001b[33m2\u001b[0m)"
    "\u001b[0m\u001b[2m;\u001b[0m })\u001b[0m\u001b[2m;\u001b[0m\n"
    "                                \u001b[31m\u001b[1m^\u001b[0m\n"
    "\u001b[0m\u001b[31merror\u001b[0m\u001b[2m:\u001b[0m \u001b[1m\u001b[2mexpect(\u001b[0m\u001b["
    "31mreceived\u001b[0m\u001b[2m).\u001b[0mtoBe\u001b[2m(\u001b[0m\u001b[32mexpected\u001b[0m"
    "\u001b[2m)\u001b[0m\n"
    "\n"
    "Expected: \u001b[32m2\u001b[0m\n"
    "Received: \u001b[31m1\u001b[0m\n"
    "\u001b[0m\n"
    "\u001b[0m      \u001b[2mat \u001b[0m\u001b[0m\u001b[2m<anonymous>\u001b[0m\u001b[2m (\u001b[0m"
    "\u001b[0m\u001b[36m\u001b[2m<root>/\u001b[0m\u001b[36mtest/c.test.ts\u001b[0m\u001b[2m:\u001b["
    "0m\u001b[33m2\u001b[0m\u001b[2m:\u001b[33m29\u001b[0m\u001b[2m)\u001b[0m\n"
    "\u001b[0m\u001b[31m\u2717\u001b[0m\u001b[0m\u001b[1m c\u001b[0m \u001b[0m\u001b[2m[0.13ms"
    "\u001b[0m\u001b[2m]\u001b[0m\n"
    "\n"
    " 0 pass\u001b[0m\n"
    "\u001b[0m\u001b[31m 1 fail\u001b[0m\n"
    " \u001b[0m\u001b[31m1 error\u001b[0m\n"
    " 1 expect() calls\n"
    "Ran 1 test across 2 files. \u001b[0m\u001b[2m[\u001b[1m12.00ms\u001b[0m\u001b[2m]\u001b[0m\n"
)
# BUN_IMPORT_IN_TEST with FORCE_COLOR=1
BUN_IMPORT_IN_TEST_COLOR = (
    "\u001b[0m\u001b[1mbun test \u001b[0m\u001b[2mv1.4.2 (744846f84)\u001b[0m\n"
    "\u001b[0m\n"
    "test/a.test.ts:\n"
    "\u001b[31merror\u001b[0m\u001b[2m: \u001b[0m\u001b[1mCannot find package 'zz-no-such-pkg-15' f"
    "rom '<root>/test/a.test.ts'\u001b[0m\n"
    "\u001b[0m\u001b[31m\u2717\u001b[0m\u001b[0m\u001b[1m dyn\u001b[0m \u001b[0m\u001b[2m[0.21ms"
    "\u001b[0m\u001b[2m]\u001b[0m\n"
    "\n"
    " 0 pass\u001b[0m\n"
    "\u001b[0m\u001b[31m 1 fail\u001b[0m\n"
    "Ran 1 test across 1 file. \u001b[0m\u001b[2m[\u001b[1m13.00ms\u001b[0m\u001b[2m]\u001b[0m\n"
)


@pytest.mark.parametrize(
    ("fixture", "pkg"),
    [
        (NODE_TODO_BESIDE_MISSING, "zz-no-such-pkg-30"),
        (NODE_SUBTEST_PARENT, "zz-no-such-pkg-32"),
        (NODE_BEFORE_HOOK_CANCELS, "zz-no-such-pkg-33"),
        (BUN_IMPORT_IN_TEST_COLOR, "zz-no-such-pkg-15"),
    ],
    ids=["node-failing-todo", "node-subtest-parent", "node-before-hook", "bun-color"],
)
def test_r4_round_3_a_red_made_only_of_a_missing_package_is_foreign(tmp_path, fixture, pkg):
    assert f"'{pkg}'" in (_unresolved(fixture, tmp_path, []) or "")


@pytest.mark.parametrize(
    "fixture",
    [
        NODE_HANG_BESIDE_MISSING,
        NODE_CRASH_AFTER_LOGGED_MESSAGE,
        NODE_CRASH_AFTER_LOGGED_ERROR,
        NODE_CRASH_ON_A_THROWN_STRING,
        BUN_TIMEOUT_AFTER_LOGGED_ERROR,
        BUN_LOAD_ERROR_BESIDE_ASSERTION_COLOR,
    ],
    ids=[
        "node-hang",
        "node-crash-on-a-name",
        "node-crash-after-a-logged-error",
        "node-crash-on-a-thrown-string",
        "bun-timeout",
        "bun-assertion-color",
    ],
)
def test_r4_round_3_another_failure_beside_a_missing_module_stands_as_red(tmp_path, fixture):
    # a cancelled test no failed hook explains, a load crash on a name, a timeout, an assertion
    assert _unresolved(fixture, tmp_path, []) is None


def test_r4_check_stops_on_a_generated_module_beside_a_failing_todo_on_base(tmp_path):
    # a failing todo never fails node's run, so one already on base cannot turn an apparatus
    # stop into a pass; one the step adds grows the xfailed count and fails the check anyway
    repo, _ = make_repo(tmp_path)
    (repo / "package.json").write_text('{"type": "module"}\n')
    (repo / "test").mkdir()
    (repo / "test" / "later.test.mjs").write_text(
        'import test from "node:test";\nimport assert from "node:assert";\n'
        'test.todo("later", () => { assert.equal(1, 2); });\n'
    )
    plan = repo / "prompt_plan.md"
    plan.write_text(plan.read_text().replace("- Run:", "- Test-files: **/*.test.mjs\n- Run:"))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "a node package with a todo")
    cmd = ["node", "--test", "test/*.test.mjs"]
    rc, line = next_auto.check_begin(repo, 1, test_cmd=cmd, lint_cmd=LINT_OK)
    assert rc == 0, line
    with (repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as f:
        f.write("gen/\n")
    (repo / "gen").mkdir()
    (repo / "gen" / "api.mjs").write_text("export const API = 1;\n")
    (repo / "test" / "api.test.mjs").write_text(
        'import test from "node:test";\nimport assert from "node:assert";\n'
        'import { API } from "../gen/api.mjs";\ntest("api", () => { assert.equal(API, 1); });\n'
    )
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 2
    assert len(lines) == 1 and "gen/api.mjs" in lines[0], lines


def test_r4_check_counts_grown_skips_beside_a_prove_red_apparatus(tmp_path):
    # Test passes in the tree, but the step skipped a base test: the counts fail the step,
    # so the apparatus stop waits for them as it waits for Test
    repo = begun(tmp_path)
    _ignored_module_step(repo, repo / ".git" / "info" / "exclude")
    (repo / "tests" / "test_base.py").write_text(
        "import pytest\n\n\n@pytest.mark.skip\ndef test_base():\n    assert True\n"
    )
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert "CHECK FAIL step 1: skipped tests grew from 0 to 1" in lines, lines
    assert any("prove-red:" in ln and "'genmod'" in ln for ln in lines), lines
    record = json.loads((repo / ".next-auto" / "attended" / "step-1.json").read_text())
    assert record["failures"] == 1


def test_r11_test_env_pins_the_node_tap_reporter(monkeypatch):
    """node 23 and later print the spec reporter when their output is captured, and the
    engine's readers are TAP readers, so the environment a Test runs in pins the TAP reporter
    through NODE_OPTIONS, after whatever the variable already holds, unless the command or the
    variable names a reporter itself, since a second reporter without its destination is a
    node error."""
    monkeypatch.delenv("NODE_OPTIONS", raising=False)
    env = next_auto.test_env(["node", "--test"])
    assert env["NODE_OPTIONS"] == "--test-reporter=tap --test-reporter-destination=stdout"
    monkeypatch.setenv("NODE_OPTIONS", "--max-old-space-size=4096")
    env = next_auto.test_env(["npm", "test"])
    assert env["NODE_OPTIONS"] == (
        "--max-old-space-size=4096 --test-reporter=tap --test-reporter-destination=stdout"
    )
    assert next_auto.test_env(["node", "--test", "--test-reporter=spec"])["NODE_OPTIONS"] == (
        "--max-old-space-size=4096"
    )
    monkeypatch.setenv("NODE_OPTIONS", "--test-reporter=dot")
    assert next_auto.test_env(["node", "--test"])["NODE_OPTIONS"] == "--test-reporter=dot"
    monkeypatch.delenv("NODE_OPTIONS", raising=False)
    assert "NODE_OPTIONS" not in next_auto.test_env(["uv", "run", "pytest", "--test-reporter=x"])


# ---- the Test-repo exemption: run_external_test (adw 3.4.8) --------------------------------

STOP_HOOK = Path(__file__).resolve().parents[1] / "hooks" / "stop-gate.py"


def _stub(tmp_path, name, body):
    """An executable /bin/sh stub, the shape _install_pre_commit writes for git hooks."""
    stub = tmp_path / name
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(0o755)
    return stub


def test_run_external_test_returns_exit_zero_and_the_last_line(tmp_path):
    stub = _stub(tmp_path, "ok.sh", "echo first\necho '19 of 19 PASS'\nexit 0\n")
    assert next_auto.run_external_test([str(stub)], tmp_path) == (0, "19 of 19 PASS")


def test_run_external_test_returns_the_failure_exit_and_the_last_line_of_both_streams(tmp_path):
    stub = _stub(tmp_path, "red.sh", "echo start\necho '1 FAIL' >&2\nexit 1\n")
    assert next_auto.run_external_test([str(stub)], tmp_path) == (1, "1 FAIL")


def test_run_external_test_raises_unavailable_for_a_command_that_does_not_exist(tmp_path):
    absent = tmp_path / "absent.sh"
    with pytest.raises(next_auto.ExternalTestUnavailableError, match=r"absent\.sh.*Errno"):
        next_auto.run_external_test([str(absent)], tmp_path)


def test_run_external_test_raises_unavailable_on_ssh_exit_255(tmp_path):
    stub = _stub(
        tmp_path, "ssh.sh",
        "echo 'ssh: connect to host 203.0.113.10 port 22: No route to host' >&2\nexit 255\n",
    )  # fmt: skip
    with pytest.raises(
        next_auto.ExternalTestUnavailableError, match=r"exit 255.*No route to host"
    ):
        next_auto.run_external_test([str(stub)], tmp_path)


def test_run_external_test_raises_timeout_past_the_limit_which_equals_the_hooks(tmp_path):
    stub = _stub(tmp_path, "slow.sh", "sleep 30\necho late\n")
    started = time.monotonic()
    with pytest.raises(next_auto.ExternalTestTimeoutError, match="still running after 1 s"):
        next_auto.run_external_test([str(stub)], tmp_path, limit=1)
    assert time.monotonic() - started < 3
    hook_limit = int(re.search(r"^TEST_LIMIT = (\d+)$", STOP_HOOK.read_text(), re.M).group(1))
    assert next_auto.EXTERNAL_TEST_LIMIT == hook_limit == 540


# ---- the Test-repo exemption: check begin and check N (adw 3.4.8) --------------------------

EXEMPT_LABEL = "ryanmat/acme-engagement, ~/dev/acme on devbox"


def _exempt_plan(stub) -> str:
    """The fixture plan with Step 1 carrying the two header lines (the close-out plan's Step 5
    shape, its ssh command replaced by a local stub)."""
    head = "### Step 1: first\nStatus: PENDING\nEval: no\nMutation: required\n"
    assert PLAN.count(head) == 1
    return PLAN.replace(head, head + f"Test-repo: {EXEMPT_LABEL}\nTest: {stub}\n")


def _exempt_begun(tmp_path, stub_body):
    """begun() on the exempt plan, with the step's own work here: the plan's status edit and
    a note under docs/, nothing else."""
    stub = _stub(tmp_path, "external.sh", stub_body)
    repo = begun(tmp_path, plan_text=_exempt_plan(stub))
    plan = repo / "prompt_plan.md"
    pending = "Status: PENDING\nEval: no\nMutation: required\nTest-repo"
    started = "Status: IN PROGRESS\nEval: no\nMutation: required\nTest-repo"
    plan.write_text(plan.read_text().replace(pending, started, 1))
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text("the step's note\n")
    return repo, stub


def test_check_begin_records_the_test_repo_and_the_external_command(tmp_path, tmp_path_factory):
    stub = _stub(tmp_path, "external.sh", "echo '19 of 19 PASS'\n")
    repo = begun(tmp_path, plan_text=_exempt_plan(stub))
    rec = json.loads(attended_record(repo).read_text())
    assert rec["test_repo"] == EXEMPT_LABEL
    assert rec["external_test_cmd"] == [str(stub)]
    plain = begun(tmp_path_factory.mktemp("plain"))
    rec = json.loads(attended_record(plain).read_text())
    assert (rec["test_repo"], rec["external_test_cmd"]) == (None, None)


def test_check_passes_an_exempt_step_on_the_external_command_and_never_proves_red(
    tmp_path, monkeypatch
):
    repo, stub = _exempt_begun(tmp_path, "echo first\necho '19 of 19 PASS'\n")

    def never(*_args, **_kwargs):
        raise AssertionError("prove-red ran for an exempt step")

    monkeypatch.setattr(next_auto, "prove_red", never)
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    result = json.loads(attended_result(repo).read_text())
    assert result["red"] == "external"
    assert result["test_repo"] == EXEMPT_LABEL
    assert result["external_test"] == {
        "command": [str(stub)], "exit": 0, "last_line": "19 of 19 PASS",
    }  # fmt: skip
    # the Conventions suite still ran here: its counts are the fixture suite's
    assert result["passed"] == result["collected"] >= 1 and result["lint"] == "ok"


def test_check_refuses_an_exempt_step_that_stages_code_here_before_anything_runs(tmp_path):
    marker = tmp_path / "ran"
    repo, _stub_path = _exempt_begun(tmp_path, f"touch {marker}\necho ok\n")
    (repo / "src.py").write_text("# Description: fixture\n# Description: two\nX = 2\n")
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert lines == [
        "CHECK FAIL step 1: a Test-repo step stages only the plan, the record and docs/ here; "
        "src.py need a test in this repository"
    ]
    assert not marker.exists()
    assert json.loads(attended_record(repo).read_text())["failures"] == 1


def test_check_fails_an_exempt_step_whose_external_test_is_red(tmp_path):
    repo, _stub_path = _exempt_begun(tmp_path, "echo running\necho '1 FAIL'\nexit 1\n")
    rc, lines = next_auto.check_step(repo, 1)
    assert (rc, lines) == (1, ["CHECK FAIL step 1: external Test failed: 1 FAIL"])
    assert json.loads(attended_record(repo).read_text())["failures"] == 1
    assert not attended_result(repo).exists()


def test_check_reports_an_unavailable_or_hung_external_test_as_apparatus(tmp_path, monkeypatch):
    repo, stub = _exempt_begun(tmp_path, "echo ok\n")
    rec = attended_record(repo)
    data = json.loads(rec.read_text())
    data["external_test_cmd"] = [str(tmp_path / "absent.sh")]
    rec.write_text(json.dumps(data))
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 2 and len(lines) == 1
    assert lines[0].startswith("CHECK APPARATUS step 1: external Test unavailable: ")
    assert json.loads(rec.read_text())["failures"] == 0
    data["external_test_cmd"] = [str(stub)]
    rec.write_text(json.dumps(data))
    stub.write_text("#!/bin/sh\nsleep 30\n")
    monkeypatch.setattr(next_auto, "EXTERNAL_TEST_LIMIT", 1)
    rc, lines = next_auto.check_step(repo, 1)
    assert (rc, lines) == (2, ["CHECK APPARATUS step 1: external Test still running after 1 s"])
    assert json.loads(rec.read_text())["failures"] == 0


# ---- the Test-repo exemption: commit N, gate K and preflight (adw 3.4.8) -------------------


def test_commit_keeps_the_external_evidence_beside_the_sha_and_mutation(tmp_path):
    repo, stub = _exempt_begun(tmp_path, "echo '19 of 19 PASS'\n")
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    rc, line = next_auto.commit_attended(repo, 1)
    assert rc == 0 and line.startswith("COMMIT "), line
    sha = line.split()[1]
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == sha
    assert "docs/note.md" in git(repo, "show", "--stat", "--format=", "HEAD").stdout
    result = json.loads(attended_result(repo).read_text())
    assert (result["red"], result["test_repo"]) == ("external", EXEMPT_LABEL)
    assert result["external_test"]["command"] == [str(stub)]
    assert (result["sha"], result["mutation"]) == (sha, "missing")


def test_gate_refuses_a_test_repo_step_before_staging_or_running_anything(tmp_path, capsys):
    marker = tmp_path / "ran"
    stub = _stub(tmp_path, "external.sh", f"touch {marker}\necho ok\n")
    repo, _ = make_repo(tmp_path)
    (repo / "prompt_plan.md").write_text(_exempt_plan(stub))
    git(repo, "commit", "-qam", "exempt step")
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = Path(state["worktree"])
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    (wt / "docs").mkdir()
    (wt / "docs" / "note.md").write_text("the implementer's note\n")
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert (ok, reason) == (
        False, "a Test-repo step runs attended; the implementer cannot build in another repository"
    )  # fmt: skip
    assert not marker.exists()
    assert git(wt, "diff", "--cached", "--name-only").stdout == ""
    assert not next_auto.run_result_path(state, 1).exists()
    rc = next_auto.main(["--root", str(repo), "gate", "1"])
    assert rc == 1
    assert capsys.readouterr().out.splitlines() == [
        "GATE FAIL step 1: a Test-repo step runs attended; the implementer cannot build in "
        "another repository"
    ]


def _steps_plan(specs, unsized=()) -> str:
    """The fixture plan's Conventions with steps built from (number, state, exempt) triples;
    every step carries Size: 10 but the numbers in unsized."""
    head = PLAN.split("### Step 1: first", 1)[0]
    body = []
    for number, state, exempt in specs:
        body += [f"### Step {number}: s{number}", f"Status: {state}", "Eval: no",
                 "Mutation: required"]  # fmt: skip
        if number not in unsized:
            body.append("Size: 10")
        if exempt:
            body += [f"Test-repo: {EXEMPT_LABEL}", "Test: true"]
        body += [f"Step {number} body.", ""]
    return head + "\n".join(body) + "\n## Decisions\n(none)\n\n## Findings\n(none)\n"


def _preflight_reasons(tmp_path, specs, unsized=()):
    repo, _ = make_repo(tmp_path)
    (repo / "prompt_plan.md").write_text(_steps_plan(specs, unsized))
    git(repo, "commit", "-qam", "plan variant")
    git(repo, "fetch", "origin")
    return next_auto.preflight_checks(repo, repo / "prompt_plan.md", **base_kwargs())


def test_preflight_refuses_a_test_repo_step_among_the_next_three_pending(tmp_path, capsys):
    specs = [(1, "DONE (x)", False), (2, "PENDING", False), (3, "PENDING", True),
             (4, "PENDING", False)]  # fmt: skip
    reasons = _preflight_reasons(tmp_path, specs)
    assert reasons == [
        "step 3 carries Test-repo and runs attended; auto mode works the next 3 pending steps"
    ]
    assert next_auto.DEFAULT_STEPS == 3


def test_preflight_passes_a_test_repo_step_that_is_done_or_beyond_the_next_three(
    tmp_path, tmp_path_factory
):
    done = [(1, "DONE (x)", True), (2, "PENDING", False), (3, "PENDING", False)]
    assert _preflight_reasons(tmp_path, done) == []
    beyond = [(1, "PENDING", False), (2, "PENDING", False), (3, "PENDING", False),
              (4, "PENDING", True)]  # fmt: skip
    assert _preflight_reasons(tmp_path_factory.mktemp("beyond"), beyond) == []


def test_check_runs_no_external_command_for_an_exempt_step_already_refused(tmp_path):
    """Chunk B review Minor: a staged-safe or gate-config refusal already fails the check, so
    the external command (an ssh round trip of up to 540 s) must not run, and the refusal must
    never be lost behind an apparatus line."""
    marker = tmp_path / "ran"
    repo, _stub_path = _exempt_begun(tmp_path, f"touch {marker}\necho ok\n")
    (repo / "docs" / "Makefile").write_text("all:\n\ttrue\n")
    rc, lines = next_auto.check_step(repo, 1)
    assert (rc, lines) == (
        1, ["CHECK FAIL step 1: changed gate/config file not named in the brief: docs/Makefile"]
    )  # fmt: skip
    assert not marker.exists()
    assert json.loads(attended_record(repo).read_text())["failures"] == 1


# ---- toolchain-files R2 and R4: check begin, check N, commit N and gate K -------------------

TOOLCHAIN_STEP = "Write a failing test then implement."


def _toolchain_plan(globs=True) -> str:
    """The fixture plan with Step 1 naming CLAUDE.md, which check_gate_config requires of a
    staged CLAUDE.md, and with `globs` the Toolchain-files line that admits it."""
    assert PLAN.count(TOOLCHAIN_STEP) == 1
    text = PLAN.replace(TOOLCHAIN_STEP, "Write a failing test then implement, and add CLAUDE.md.")
    if globs:
        run = "- Run: uv run python -m fixturepkg\n"
        assert text.count(run) == 1
        text = text.replace(run, run + "- Toolchain-files: CLAUDE.md\n")
    return text


def test_check_begin_records_the_toolchain_globs(tmp_path, tmp_path_factory):
    """toolchain-files R2: check begin records the plan's globs, or null without the line."""
    repo = begun(tmp_path, plan_text=_toolchain_plan())
    assert json.loads(attended_record(repo).read_text())["toolchain_files"] == ["CLAUDE.md"]
    plain = begun(tmp_path_factory.mktemp("plain"))
    assert json.loads(attended_record(plain).read_text())["toolchain_files"] is None


def test_check_passes_a_step_that_stages_a_named_toolchain_file(tmp_path):
    """toolchain-files R4: check N admits the toolchain paths the record's globs name."""
    repo = begun(tmp_path, plan_text=_toolchain_plan())
    good_step(repo)
    (repo / "CLAUDE.md").write_text("@AGENTS.md\n")
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])


def test_check_refuses_the_toolchain_file_when_the_plan_names_no_globs(tmp_path):
    """toolchain-files R4: without the line the refusal is today's, and it counts an attempt."""
    repo = begun(tmp_path, plan_text=_toolchain_plan(globs=False))
    good_step(repo)
    (repo / "CLAUDE.md").write_text("@AGENTS.md\n")
    assert next_auto.check_step(repo, 1) == (
        1, ["CHECK FAIL step 1: staged toolchain file: CLAUDE.md"]
    )  # fmt: skip
    assert json.loads(attended_record(repo).read_text())["failures"] == 1


def test_attended_commit_commits_a_named_toolchain_file(tmp_path, capsys):
    """toolchain-files R4: commit N admits the toolchain paths the plan's globs name."""
    repo = begun(tmp_path, plan_text=_toolchain_plan())
    good_step(repo)
    (repo / "CLAUDE.md").write_text("@AGENTS.md\n")
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    rc = next_auto.main(["--root", str(repo), "commit", "1", "--title", "the toolchain file"])
    capsys.readouterr()
    assert rc == 0
    committed = git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert "CLAUDE.md" in committed


def test_attended_commit_refuses_a_toolchain_file_the_plan_does_not_name(tmp_path, capsys):
    """toolchain-files R4: without the line commit N refuses as before and commits nothing."""
    repo, _ = make_repo(tmp_path)
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0] == 0
    good_step(repo)
    (repo / "CLAUDE.md").write_text("@AGENTS.md\n")
    before = git(repo, "rev-parse", "HEAD").stdout.strip()
    rc = next_auto.main(["--root", str(repo), "commit", "1", "--title", "the toolchain file"])
    out = capsys.readouterr()
    assert rc == 1
    assert out.err.strip() == "COMMIT REFUSED step 1: staged toolchain file: CLAUDE.md"
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before


def test_gate_refuses_a_toolchain_file_even_when_the_plan_names_it(tmp_path):
    """toolchain-files R4: auto mode ignores the line, so gate K refuses as before."""
    repo, _ = make_repo(tmp_path)
    (repo / "prompt_plan.md").write_text(_toolchain_plan())
    git(repo, "commit", "-qam", "plan variant")
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20261021-tool01", sync=False)
    wt = Path(state["worktree"])
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    (wt / "CLAUDE.md").write_text("@AGENTS.md\n")
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK) == (
        False, "staged toolchain file: CLAUDE.md"
    )  # fmt: skip


# ---- the done note from the record: set N done (adw 3.4.8) ---------------------------------

# The close-out plan's .next-auto/attended/result-4.json on 2026-10-11, the engine's record of
# an attended step as check N writes it (the sha be2cae4b and the mutation value that commit N
# added are left off: set N done runs before the commit and refuses a file that carries sha);
# the step number is the fixture plan's Mutation n/a step, and passed sits one under collected
# (a suite with one skip) so a clause that reads the wrong field cannot match.
RESULT_FIXTURE = {
    "step": 2,
    "mode": "attended",
    "head": "24bc2b746d7d7bbd66566f04361ec2efda66ef2c",
    "passed": 2150,
    "collected": 2151,
    "red": "proved",
    "lint": "ok",
    "tree": "e69d8b00aa3893c8559493d401cd8c11cf6323216c777b5dc05a621dd4109b1c",
}
FIXTURE_CLAUSE = "check: 2150 passed of 2151 collected at base 24bc2b74, lint ok, red proved"


def _with_result(tmp_path, result=RESULT_FIXTURE, step=2):
    repo, _ = make_repo(tmp_path)
    if result is not None:
        path = attended_result(repo, step)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2))
    return repo


def _note_file(tmp_path, text):
    note = tmp_path / "note.txt"
    note.write_text(text)
    return note


def _plan_hash(repo):
    return next_auto.sha256_bytes((repo / "prompt_plan.md").read_bytes())


def _done_line(repo):
    lines = (repo / "prompt_plan.md").read_text().splitlines()
    return next((ln for ln in lines if ln.startswith("Status: DONE")), None)


def _set_done(repo, tmp_path, text, n=2):
    note = _note_file(tmp_path, text)
    return next_auto.main(["--root", str(repo), "set", str(n), "done", "--note-file", str(note)])


def test_set_done_appends_the_evidence_clause_from_the_result_file(tmp_path, capsys):
    repo = _with_result(tmp_path)
    assert _set_done(repo, tmp_path, "the fixture note") == 0
    assert capsys.readouterr().out == "SET step 2 done\n"
    assert _done_line(repo) == f"Status: DONE (the fixture note; {FIXTURE_CLAUSE})"


def test_set_done_is_idempotent_for_a_note_that_already_ends_with_the_clause(tmp_path):
    repo = _with_result(tmp_path)
    assert _set_done(repo, tmp_path, "the fixture note") == 0
    once = _plan_hash(repo)
    # the note as the plan now carries it, clause included, set again
    assert _set_done(repo, tmp_path, f"the fixture note; {FIXTURE_CLAUSE}") == 0
    assert _plan_hash(repo) == once
    assert _done_line(repo).count("check:") == 1


def test_set_done_names_the_external_test_when_red_is_external(tmp_path):
    external = dict(
        RESULT_FIXTURE,
        red="external",
        test_repo=EXEMPT_LABEL,
        external_test={"command": ["ssh", "host", "x.sh"], "exit": 0, "last_line": "19 of 19 PASS"},
    )
    repo = _with_result(tmp_path, external)
    assert _set_done(repo, tmp_path, "the exempt note") == 0
    assert _done_line(repo) == (
        "Status: DONE (the exempt note; check: 2150 passed of 2151 collected at base 24bc2b74, "
        "lint ok, red external, external Test exit 0: 19 of 19 PASS)"
    )


def test_next_plan_set_refuses_done_and_still_sets_the_other_states(tmp_path):
    repo = _with_result(tmp_path)
    plan = repo / "prompt_plan.md"
    before = _plan_hash(repo)
    note = _note_file(tmp_path, "the fixture note")
    script = Path(next_plan.__file__)
    r = subprocess.run(
        [sys.executable, str(script), "set", str(plan), "2", "done", "--note-file", str(note)],
        capture_output=True, text=True,
    )  # fmt: skip
    assert r.returncode == 1
    assert r.stderr.splitlines() == [
        "SET REFUSED step 2: done is set through next_auto.py set N done --note-file <f>, "
        "which reads the check record"
    ]
    assert _plan_hash(repo) == before
    r = subprocess.run(
        [sys.executable, str(script), "set", str(plan), "2", "in-progress"],
        capture_output=True, text=True,
    )  # fmt: skip
    assert r.returncode == 0
    assert "### Step 2: second\nStatus: IN PROGRESS\n" in plan.read_text()


# ---- conflicts and the no-record rule refuse: set N done (adw 3.4.8 Step 7) ----------------

# A refusal is one line on stderr, exit 1, and the plan byte-identical by hash.
def _refused(repo, tmp_path, text, capsys, n=2):
    before = _plan_hash(repo)
    rc = _set_done(repo, tmp_path, text, n=n)
    err = capsys.readouterr().err
    assert rc == 1
    assert _plan_hash(repo) == before
    return err


def test_set_done_refuses_a_passed_count_that_differs_from_the_record(tmp_path, capsys):
    repo = _with_result(tmp_path)
    err = _refused(repo, tmp_path, "the fixture note, 12 passed", capsys)
    assert err == "SET REFUSED step 2: the note says 12 passed, the check record says 2150\n"


@pytest.mark.parametrize(
    "phrase",
    ["suite 12", "12 tests green", "12 test green", "12 green"],
)
def test_set_done_refuses_each_suite_count_form_with_its_own_number(tmp_path, capsys, phrase):
    repo = _with_result(tmp_path)
    err = _refused(repo, tmp_path, f"the fixture note, {phrase}", capsys)
    assert err == f"SET REFUSED step 2: the note says {phrase}, the check record says 2150\n"


@pytest.mark.parametrize(
    "phrase",
    # the record's own count in two forms, and counts with another unit, which the fixed
    # list of patterns does not judge
    ["2150 passed", "suite 2150", "12 mutants killed", "12 PASS", "12 commits"],
)
def test_set_done_accepts_the_record_count_and_counts_with_another_unit(tmp_path, phrase):
    repo = _with_result(tmp_path)
    assert _set_done(repo, tmp_path, f"the fixture note, {phrase}") == 0
    assert _done_line(repo) == f"Status: DONE (the fixture note, {phrase}; {FIXTURE_CLAUSE})"


def test_set_done_refuses_a_check_pass_base_that_is_not_the_record_head(tmp_path, capsys):
    repo = _with_result(tmp_path)
    err = _refused(repo, tmp_path, "the fixture note, CHECK PASS at base deadbeef", capsys)
    assert err == (
        "SET REFUSED step 2: the note says CHECK PASS at base deadbeef, "
        "the check record says 24bc2b74\n"
    )
    assert _set_done(repo, tmp_path, "the fixture note, CHECK PASS at base 24bc2b74") == 0
    assert _done_line(repo) == (
        f"Status: DONE (the fixture note, CHECK PASS at base 24bc2b74; {FIXTURE_CLAUSE})"
    )


def test_set_done_refuses_without_a_result_file_unless_the_note_records_the_gap(
    tmp_path, capsys
):
    repo = _with_result(tmp_path, result=None)
    err = _refused(repo, tmp_path, "the fixture note", capsys)
    assert err == (
        'SET REFUSED step 2: no result file; run check 2, or write "no check 2: <why>" '
        "in the note\n"
    )


@pytest.mark.parametrize(
    "text",
    [
        "no check 2: the tests live in the Acme tree",
        # the close-out plan's live Step 12 form, with this fixture's step number
        "no check 2 pass: the step stages nothing in this repo",
    ],
)
def test_set_done_without_a_result_file_writes_a_note_that_names_the_gap_and_why(
    tmp_path, text
):
    repo = _with_result(tmp_path, result=None)
    assert _set_done(repo, tmp_path, text) == 0
    assert _done_line(repo) == f"Status: DONE ({text})"


def test_set_done_without_a_result_file_refuses_a_gap_phrase_with_no_reason(tmp_path, capsys):
    repo = _with_result(tmp_path, result=None)
    err = _refused(repo, tmp_path, "no check 2. The tests live elsewhere", capsys)
    assert err == (
        'SET REFUSED step 2: no result file; run check 2, or write "no check 2: <why>" '
        "in the note\n"
    )


# ---- mutation claims, the auto-mode path and the unreadable record (adw 3.4.8 Step 8) -----


def test_set_done_refuses_a_mutation_kill_claimed_on_a_mutation_na_step(tmp_path, capsys):
    # the fixture plan's Step 2 header is `Mutation: n/a (docs only)`
    repo = _with_result(tmp_path)
    err = _refused(repo, tmp_path, "the fixture note, mutation killed both ways", capsys)
    assert err == (
        "SET REFUSED step 2: Mutation: n/a step claims a mutation kill; "
        "change the header line before the check\n"
    )
    # the word alone is not the claim
    assert _set_done(repo, tmp_path, "the fixture note, no mutation check on a docs step") == 0
    assert _done_line(repo) == (
        f"Status: DONE (the fixture note, no mutation check on a docs step; {FIXTURE_CLAUSE})"
    )


def test_set_done_refuses_a_mutation_required_step_whose_note_lacks_the_words(tmp_path, capsys):
    # the fixture plan's Step 1 header is `Mutation: required`
    repo = _with_result(tmp_path, dict(RESULT_FIXTURE, step=1, mutation="reported"), step=1)
    err = _refused(repo, tmp_path, "the fixture note, both mutants red", capsys, n=1)
    assert err == (
        "SET REFUSED step 1: Mutation: required step lacks the words mutation killed; "
        "change the header line or run the check\n"
    )
    assert _set_done(repo, tmp_path, "the fixture note, mutation killed both ways", n=1) == 0
    assert _done_line(repo) == (
        f"Status: DONE (the fixture note, mutation killed both ways; {FIXTURE_CLAUSE})"
    )


def test_set_done_in_a_run_appends_the_clause_to_the_worktree_plan_and_refuses_conflicts(
    tmp_path, capsys
):
    repo, _ = make_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = Path(state["worktree"])
    result = next_auto.run_result_path(state, 2)
    result.write_text(json.dumps(dict(RESULT_FIXTURE, mode="auto"), indent=2))
    root_before = _plan_hash(repo)
    wt_before = next_auto.sha256_bytes((wt / "prompt_plan.md").read_bytes())
    # a conflict refuses and leaves both plans as they were
    assert _set_done(repo, tmp_path, "the run note, 12 passed") == 1
    assert capsys.readouterr().err == (
        "SET REFUSED step 2: the note says 12 passed, the check record says 2150\n"
    )
    assert next_auto.sha256_bytes((wt / "prompt_plan.md").read_bytes()) == wt_before
    assert _set_done(repo, tmp_path, "the run note") == 0
    assert capsys.readouterr().out == "SET step 2 done\n"
    wt_lines = (wt / "prompt_plan.md").read_text().splitlines()
    assert f"Status: DONE (the run note; {FIXTURE_CLAUSE})" in wt_lines
    # the root plan is never the run's to change
    assert _plan_hash(repo) == root_before


def test_set_done_is_apparatus_when_the_result_file_does_not_parse(tmp_path, capsys):
    repo = _with_result(tmp_path, result=None)
    path = attended_result(repo, 2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    before = _plan_hash(repo)
    assert _set_done(repo, tmp_path, "the fixture note") == 2
    err = capsys.readouterr().err
    assert err.startswith("SET APPARATUS step 2: result file unreadable: ")
    assert err.count("\n") == 1
    assert _plan_hash(repo) == before


# ---- verdict and done-verdict resolve the head (adw 3.4.8 Step 9) --------------------------

# forty hex characters that are no commit of any fixture repository
GHOST_SHA = "0123456789abcdef0123456789abcdef01234567"


def _two_commit_repo(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "second.md").write_text("the second commit\n")
    git(repo, "add", "docs/second.md")
    git(repo, "commit", "-q", "-m", "docs: second commit")
    return repo


def _report_file(tmp_path, text=REPORT):
    report = tmp_path / "report.md"
    report.write_text(text)
    return report


def test_verdict_refuses_a_well_formed_sha_that_is_no_commit(tmp_path, capsys):
    repo = _two_commit_repo(tmp_path)
    rc = next_auto.main(verdict_argv(repo, 1, GHOST_SHA, 1, "Approved", _report_file(tmp_path)))
    assert rc == 1
    assert capsys.readouterr().err == (
        f"VERDICT REFUSED step 1: {GHOST_SHA} is not a commit of this repository\n"
    )
    assert not list((repo / ".next-auto" / "attended").glob("verdict-*"))


def test_done_verdict_refuses_a_well_formed_sha_that_is_no_commit(tmp_path, capsys):
    repo = _two_commit_repo(tmp_path)
    report = _report_file(tmp_path, "APPROVED\n")
    rc = next_auto.main(done_verdict_argv(repo, GHOST_SHA, "APPROVED", "opus", report))
    assert rc == 1
    assert capsys.readouterr().err == (
        f"DONE-VERDICT REFUSED: {GHOST_SHA} is not a commit of this repository\n"
    )
    assert not list((repo / ".next-auto" / "attended").glob("done-*"))


def test_verdict_and_done_verdict_expand_an_eight_character_prefix(tmp_path, capsys):
    repo = _two_commit_repo(tmp_path)
    sha = head_sha(repo)
    attended = repo / ".next-auto" / "attended"
    rc = next_auto.main(verdict_argv(repo, 1, sha[:8], 1, "Approved", _report_file(tmp_path)))
    assert rc == 0
    assert capsys.readouterr().out == f"VERDICT Approved {attended / f'verdict-{sha}.json'}\n"
    assert json.loads((attended / f"verdict-{sha}.json").read_text())["head"] == sha
    assert (attended / f"review-{sha}-1.md").is_file()
    report = _report_file(tmp_path, "APPROVED\n")
    rc = next_auto.main(done_verdict_argv(repo, sha[:8], "APPROVED", "opus", report))
    assert rc == 0
    assert capsys.readouterr().out == f"DONE-VERDICT APPROVED {attended / f'done-{sha}.json'}\n"
    assert json.loads((attended / f"done-{sha}.json").read_text())["head"] == sha


@pytest.mark.parametrize("head", ["abc123", "e44452", "zzzzzzzz", "e444527g"])
def test_verdict_and_done_verdict_refuse_a_head_that_is_no_sha_or_short_prefix(
    tmp_path, capsys, head
):
    repo = _two_commit_repo(tmp_path)
    why = f"head {head} is not a commit sha or a unique prefix of seven or more hex characters"
    assert next_auto.main(verdict_argv(repo, 1, head, 1, "Approved", _report_file(tmp_path))) == 1
    assert capsys.readouterr().err == f"VERDICT REFUSED step 1: {why}\n"
    report = _report_file(tmp_path, "APPROVED\n")
    assert next_auto.main(done_verdict_argv(repo, head, "APPROVED", "opus", report)) == 1
    assert capsys.readouterr().err == f"DONE-VERDICT REFUSED: {why}\n"
    assert not (repo / ".next-auto" / "attended").exists()


def test_verdict_and_done_verdict_refuse_an_ambiguous_prefix_naming_it(
    tmp_path, capsys, monkeypatch
):
    # two commits sharing seven characters cannot be made on demand, so git's own ambiguity
    # error is returned for the rev-parse call alone; every other git call is real
    repo = _two_commit_repo(tmp_path)
    real_git = next_auto.git

    def fake_git(root, *args, **kwargs):
        if args[:2] == ("rev-parse", "--verify"):
            return subprocess.CompletedProcess(
                ["git", *args], 128, "", "error: short object ID 1234567 is ambiguous\n"
            )
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(next_auto, "git", fake_git)
    why = "head 1234567 is ambiguous in this repository"
    rc = next_auto.main(verdict_argv(repo, 1, "1234567", 1, "Approved", _report_file(tmp_path)))
    assert rc == 1
    assert capsys.readouterr().err == f"VERDICT REFUSED step 1: {why}\n"
    report = _report_file(tmp_path, "APPROVED\n")
    assert next_auto.main(done_verdict_argv(repo, "1234567", "APPROVED", "opus", report)) == 1
    assert capsys.readouterr().err == f"DONE-VERDICT REFUSED: {why}\n"


# ---- the Chunk C fix round on the set core (adw 3.4.8 Step 8, round 2) ---------------------


def test_set_done_is_idempotent_when_the_external_last_line_is_a_pytest_summary(tmp_path):
    # the clause the engine appended carries the external command's own count; the conflict
    # scan judges the typed part of the note only, never the clause (Chunk C Important 1)
    external = dict(
        RESULT_FIXTURE,
        red="external",
        test_repo=EXEMPT_LABEL,
        external_test={
            "command": ["ssh", "host", "x.sh"],
            "exit": 0,
            "last_line": "==== 312 passed in 4.2s ====",
        },
    )
    repo = _with_result(tmp_path, external)
    assert _set_done(repo, tmp_path, "the exempt note") == 0
    once = _plan_hash(repo)
    line = _done_line(repo)
    assert line.endswith(", red external, external Test exit 0: ==== 312 passed in 4.2s ====)")
    carried = line[len("Status: DONE (") : -1]
    assert _set_done(repo, tmp_path, carried) == 0
    assert _plan_hash(repo) == once


def test_set_done_in_a_run_refuses_without_a_result_file_whatever_the_note_says(
    tmp_path, capsys
):
    # gate K writes the file on every pass, so a run has no gap form (Chunk C Important 5)
    repo, _ = make_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = Path(state["worktree"])
    before = next_auto.sha256_bytes((wt / "prompt_plan.md").read_bytes())
    assert _set_done(repo, tmp_path, "no check 2: the gate wrote nothing") == 1
    assert capsys.readouterr().err == (
        "SET REFUSED step 2: no result file in the run; gate 2 writes it on a pass\n"
    )
    assert next_auto.sha256_bytes((wt / "prompt_plan.md").read_bytes()) == before


def test_set_done_refuses_a_result_file_a_commit_already_used(tmp_path, capsys):
    # commit N adds sha to the file; a later set N done with no new check would take another
    # step's counts and base from the leftover (Chunk C Minor 2)
    used = dict(RESULT_FIXTURE, sha="be2cae4bb8a2176eee1edaad2d09340b90d45dbf")
    repo = _with_result(tmp_path, used)
    err = _refused(repo, tmp_path, "the fixture note", capsys)
    assert err == (
        "SET REFUSED step 2: the result file was used by commit be2cae4b; run check 2 again\n"
    )



# ---- the Chunk D fix round on resolve_head (adw 3.4.8 Step 9, round 2) ---------------------


def test_verdict_and_done_verdict_report_a_git_failure_as_apparatus(tmp_path, capsys, monkeypatch):
    # a root that is no repository, or an unreadable object store, is not a wrong sha: git's
    # own last line comes back as an apparatus exit (Chunk D Minor 1); only "Needed a single
    # revision" is the not-a-commit refusal
    repo = _two_commit_repo(tmp_path)
    sha = head_sha(repo)
    real_git = next_auto.git

    def fake_git(root, *args, **kwargs):
        if args[:2] == ("rev-parse", "--verify"):
            return subprocess.CompletedProcess(
                ["git", *args], 128, "",
                "fatal: not a git repository (or any of the parent directories): .git\n",
            )  # fmt: skip
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(next_auto, "git", fake_git)
    why = "git rev-parse: fatal: not a git repository (or any of the parent directories): .git"
    rc = next_auto.main(verdict_argv(repo, 1, sha, 1, "Approved", _report_file(tmp_path)))
    assert rc == 2
    assert capsys.readouterr().err == f"VERDICT APPARATUS step 1: {why}\n"
    report = _report_file(tmp_path, "APPROVED\n")
    assert next_auto.main(done_verdict_argv(repo, sha, "APPROVED", "opus", report)) == 2
    assert capsys.readouterr().err == f"DONE-VERDICT APPARATUS: {why}\n"
    assert not (repo / ".next-auto" / "attended").exists()


# ---- adw 3.5.0: the issue id in the branch name (spec item 1) ----------------------------

# Fixture shape: the Issue value is a Linear id of the Acme team as its sub-issues carry
# them (DOC-800, read 2026-10-12); the slug is PLAN's title, as `_slug` reads it.
def _issue_repo(tmp_path, issue):
    """make_repo with `- Issue: <issue>` after Run in the plan's Conventions, committed."""
    repo, _ = make_repo(tmp_path)
    run = "- Run: uv run python -m fixturepkg\n"
    assert PLAN.count(run) == 1
    (repo / "prompt_plan.md").write_text(PLAN.replace(run, f"{run}- Issue: {issue}\n"))
    git(repo, "commit", "-am", "plan: issue key")
    return repo


def _branches(repo):
    out = git(repo, "branch", "--list", "next-auto/*", "--format=%(refname:short)").stdout
    return out.split()


def test_start_puts_the_issue_id_lowercased_before_the_slug(tmp_path):
    repo = _issue_repo(tmp_path, "DOC-123")
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    branch = "next-auto/doc-123-fixture-implementation-plan-20260911-abc123"
    assert state["branch"] == branch
    assert next_auto.load_state(repo)["branch"] == branch
    assert _branches(repo) == [branch]


def test_start_without_the_issue_key_names_the_branch_as_before(tmp_path):
    repo, _ = make_repo(tmp_path)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    branch = "next-auto/fixture-implementation-plan-20260911-abc123"
    assert state["branch"] == branch
    assert next_auto.load_state(repo)["branch"] == branch
    assert _branches(repo) == [branch]


# ---- adw 3.5.0: commit refuses a title that carries a conventional prefix (spec item 10) --

# Fixture shape: the doubled subjects 3.4.8's c9dd833 and 1491acc carry
# (`feat(step-9): feat(step-9): ...`), from that repo's log.
@pytest.mark.parametrize(
    "title,prefix",
    [("feat(step-3): x", "feat(step-3): "), ("fix: x", "fix: "), ("feat!: x", "feat!: ")],
)
def test_attended_commit_refuses_a_title_with_a_conventional_prefix(
    tmp_path, capsys, title, prefix
):
    repo, _ = make_repo(tmp_path)
    (repo / "tests" / "test_new.py").write_text("def test_new():\n    assert True\n")
    before = git(repo, "rev-parse", "HEAD").stdout.strip()
    rc = next_auto.main(["--root", str(repo), "commit", "3", "--title", title])
    out = capsys.readouterr()
    assert rc == 1
    assert out.err.strip() == (
        f'COMMIT REFUSED step 3: the title already carries a conventional prefix ("{prefix}"); '
        "pass the subject only"
    )
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before


def test_attended_commit_prefixes_a_plain_subject(tmp_path, capsys):
    repo, _ = make_repo(tmp_path)
    (repo / "tests" / "test_new.py").write_text("def test_new():\n    assert True\n")
    rc = next_auto.main(["--root", str(repo), "commit", "3", "--title", "x"])
    capsys.readouterr()
    assert rc == 0
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == "feat(step-3): x"


def test_the_prefix_rule_matches_lowercase_types_only(tmp_path, capsys):
    """`Fix: the parser` is a subject, since the rule reads lowercase types only; `note: see
    R3` is refused, since a lowercase word and a colon at the start read as a type."""
    repo, _ = make_repo(tmp_path)
    (repo / "tests" / "test_new.py").write_text("def test_new():\n    assert True\n")
    rc = next_auto.main(["--root", str(repo), "commit", "3", "--title", "Fix: the parser"])
    capsys.readouterr()
    assert rc == 0
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == "feat(step-3): Fix: the parser"
    (repo / "tests" / "test_other.py").write_text("def test_other():\n    assert True\n")
    before = git(repo, "rev-parse", "HEAD").stdout.strip()
    rc = next_auto.main(["--root", str(repo), "commit", "4", "--title", "note: see R3"])
    out = capsys.readouterr()
    assert rc == 1
    assert out.err.strip() == (
        'COMMIT REFUSED step 4: the title already carries a conventional prefix ("note: "); '
        "pass the subject only"
    )
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before


def test_run_commit_refuses_a_title_with_a_conventional_prefix(tmp_path, capsys):
    repo, wt = start_wt(tmp_path)
    good_step(wt)
    before = git(wt, "rev-parse", "HEAD").stdout.strip()
    rc = next_auto.main(["--root", str(repo), "commit", "1", "--title", "feat(step-1): feat"])
    out = capsys.readouterr()
    assert rc == 1
    assert out.err.strip() == (
        'COMMIT REFUSED step 1: the title already carries a conventional prefix '
        '("feat(step-1): "); pass the subject only'
    )
    assert git(wt, "rev-parse", "HEAD").stdout.strip() == before
    assert "1" not in next_auto.load_state(repo).get("steps", {})


# ---- adw 3.5.0: the Holdout hash at step-begin and gate (spec item 3) ---------------------

# Fixture shapes: `- Holdout: evals/holdout/` and the single-file
# `- Holdout: tools/workflows/check-anchors.mjs`, both as live plans carry them (this repo's
# and acme/platform's, read 2026-10-12).
HOLDOUT_DIR = "evals/holdout/"


HOLDOUT_FILES = ("evals/holdout/a.json", "evals/holdout/b.json")


def holdout_run(tmp_path, holdout=HOLDOUT_DIR, files=HOLDOUT_FILES):
    """A started run whose plan carries `- Holdout: <holdout>` with `files` committed."""
    repo, _ = make_repo(tmp_path)
    run = "- Run: uv run python -m fixturepkg\n"
    (repo / "prompt_plan.md").write_text(PLAN.replace(run, f"{run}- Holdout: {holdout}\n"))
    for rel in files:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(f'{{"scenario": "{rel}"}}\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "holdout")
    git(repo, "push", "origin", "main")
    next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    return repo, repo / ".next-auto" / "20260911-abc123" / "wt"


def _digest(tree, rels):
    """The hash the spec words: every file's relative path and bytes, in sorted path order."""
    h = hashlib.sha256()
    for rel in sorted(rels):
        data = (tree / rel).read_bytes()
        h.update(f"{rel}\0{len(data)}\0".encode())
        h.update(data)
    return h.hexdigest()


def _holdout_refusal(holdout=HOLDOUT_DIR):
    return f"Holdout {holdout} changed during the step; the implementer may not edit the holdout"


def test_step_begin_records_the_holdout_hash(tmp_path):
    repo, wt = holdout_run(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    expected = _digest(wt, HOLDOUT_FILES)
    assert state["holdout_sha256"] == expected
    assert next_auto.load_state(repo)["holdout_sha256"] == expected
    assert next_auto.holdout_hash(wt, HOLDOUT_DIR) == expected


def test_the_holdout_hash_reads_files_in_sorted_order(tmp_path):
    """Twenty files written in reverse name order, so the directory's own listing order is
    not the sorted one on ext4 or tmpfs; the hash must not depend on it."""
    tree = tmp_path / "tree"
    names = [f"evals/holdout/s{i:02d}.json" for i in range(20)]
    for rel in reversed(names):
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(rel)
    assert next_auto.holdout_hash(tree, HOLDOUT_DIR) == _digest(tree, names)


def test_the_holdout_hash_of_a_single_file_and_of_an_absent_path(tmp_path):
    tree = tmp_path / "tree"
    rel = "tools/workflows/check-anchors.mjs"
    (tree / rel).parent.mkdir(parents=True)
    (tree / rel).write_text("// Description: anchors\n")
    assert next_auto.holdout_hash(tree, rel) == _digest(tree, [rel])
    assert next_auto.holdout_hash(tree, "evals/missing/") == "absent"


def test_gate_refuses_a_holdout_file_rewritten_during_the_step_before_staging(tmp_path):
    """A write by Python, which the guard does not see, between step-begin and gate."""
    repo, wt = holdout_run(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    (wt / "evals" / "holdout" / "a.json").write_text('{"scenario": "rewritten"}\n')
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert (ok, reason) == (False, _holdout_refusal())
    assert git(wt, "diff", "--cached", "--name-only").stdout.strip() == ""


@pytest.mark.parametrize("change", ["add", "remove"])
def test_gate_refuses_a_holdout_file_added_or_removed(tmp_path, change):
    repo, wt = holdout_run(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    if change == "add":
        (wt / "evals" / "holdout" / "c.json").write_text("{}\n")
    else:
        (wt / "evals" / "holdout" / "b.json").unlink()
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert (ok, reason) == (False, _holdout_refusal())
    assert git(wt, "diff", "--cached", "--name-only").stdout.strip() == ""


def test_gate_passes_an_untouched_holdout(tmp_path):
    repo, wt = holdout_run(tmp_path)
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert ok, reason


def test_a_plan_without_holdout_records_no_hash_and_skips_the_check(tmp_path):
    repo, wt = start_wt(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    assert "holdout_sha256" not in state
    good_step(wt)
    (wt / "evals").mkdir()
    (wt / "evals" / "x.json").write_text("{}\n")
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert ok, reason


def test_gate_refuses_a_file_created_at_an_absent_holdout(tmp_path):
    repo, wt = holdout_run(tmp_path, holdout="evals/missing/", files=())
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    assert state["holdout_sha256"] == "absent"
    good_step(wt)
    (wt / "evals" / "missing").mkdir(parents=True)
    (wt / "evals" / "missing" / "new.json").write_text("{}\n")
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert (ok, reason) == (False, _holdout_refusal("evals/missing/"))


# ---- adw 3.5.0: a named test deletion lowers the count floor (spec item 5) -----------------

# Fixture shape: the `Deletes: tests/test_old.py` line Step 6's brief in this repo's
# prompt_plan.md names, after `Mutation:` as the 3.5.0 grammar places it.
OLD_TESTS = "def test_old_one():\n    assert True\n\n\ndef test_old_two():\n    assert True\n"


KEEP_TESTS = "def test_keep_one():\n    assert True\n\n\ndef test_keep_two():\n    assert True\n"


def _step1_with(line):
    """PLAN with step 1 carrying `line` after its Mutation and Size lines."""
    step1 = "Mutation: required\nSize: 20\nWrite a failing test then implement."
    assert step1 in PLAN
    return PLAN.replace(step1, step1.replace("\nWrite", f"\n{line}\nWrite"), 1)


def _deletes_plan(paths="tests/test_old.py"):
    return _step1_with(f"Deletes: {paths}")


def _with_old_and_keep(tmp_path, plan_text):
    """make_repo with `plan_text` and two more test files at base, five tests in all."""
    repo, _ = make_repo(tmp_path)
    (repo / "prompt_plan.md").write_text(plan_text)
    (repo / "tests" / "test_old.py").write_text(OLD_TESTS)
    (repo / "tests" / "test_keep.py").write_text(KEEP_TESTS)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "old and kept tests")
    return repo


def _begun_with_deletes(tmp_path, plan_text):
    repo = _with_old_and_keep(tmp_path, plan_text)
    rc, _msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0
    return repo


def test_check_passes_a_named_test_file_deletion_with_the_lower_count(tmp_path):
    repo = _begun_with_deletes(tmp_path, _deletes_plan())
    good_step(repo)
    (repo / "tests" / "test_old.py").unlink()
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    result = json.loads(next_auto.attended_result_path(repo, 1).read_text(encoding="utf-8"))
    assert (result["passed"], result["collected"]) == (4, 4)


def test_check_refuses_the_same_deletion_without_a_deletes_line(tmp_path):
    repo = _begun_with_deletes(tmp_path, PLAN)
    good_step(repo)
    (repo / "tests" / "test_old.py").unlink()
    rc, lines = next_auto.check_step(repo, 1)
    assert (rc, lines) == (1, ["CHECK FAIL step 1: collected tests dropped from 5 to 4"])


def test_check_keeps_the_floor_when_the_named_file_is_not_the_one_deleted(tmp_path):
    # the line names test_old.py, the step deletes test_keep.py: nothing qualifies
    repo = _begun_with_deletes(tmp_path, _deletes_plan())
    good_step(repo)
    (repo / "tests" / "test_keep.py").unlink()
    rc, lines = next_auto.check_step(repo, 1)
    assert (rc, lines) == (1, ["CHECK FAIL step 1: collected tests dropped from 5 to 4"])


def test_check_refuses_a_named_deletion_that_also_drops_a_kept_test(tmp_path):
    # the floor drops by test_old.py's two tests only, so the kept file losing one still counts
    repo = _begun_with_deletes(tmp_path, _deletes_plan())
    (repo / "tests" / "test_old.py").unlink()
    (repo / "tests" / "test_keep.py").write_text(
        "from src import FEATURE\n\n\ndef test_keep_one():\n    assert FEATURE == 42\n"
    )
    (repo / "src.py").write_text(
        "# Description: fixture\n# Description: two\nX = 1\nFEATURE = 42\n"
    )
    rc, lines = next_auto.check_step(repo, 1)
    assert (rc, lines) == (1, ["CHECK FAIL step 1: collected tests dropped from 3 to 2"])


def test_adjusted_base_counts_run_the_base_without_the_named_files_beside_the_tree_deps(tmp_path):
    # a test at base that skips without node_modules shows the scratch got the tree's install
    repo = _with_old_and_keep(tmp_path, PLAN)
    (repo / "tests" / "test_deps.py").write_text(
        "from pathlib import Path\n\nimport pytest\n\n\ndef test_deps():\n"
        "    if not Path('node_modules').is_dir():\n        pytest.skip('no install')\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "deps test")
    (repo / "node_modules").mkdir()
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    counts = next_auto.adjusted_base_counts(
        repo, base, ["tests/test_old.py"], TEST_CMD, tree=repo
    )
    assert counts == (4, 0, 0)


def _deletes_run(tmp_path, plan_text):
    repo = _with_old_and_keep(tmp_path, plan_text)
    git(repo, "push", "origin", "main")
    next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = repo / ".next-auto" / "20260911-abc123" / "wt"
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    (wt / "tests" / "test_old.py").unlink()
    return repo


def test_gate_passes_a_named_test_file_deletion(tmp_path):
    repo = _deletes_run(tmp_path, _deletes_plan())
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert ok, reason


def test_gate_refuses_the_same_deletion_without_a_deletes_line(tmp_path):
    repo = _deletes_run(tmp_path, PLAN)
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert (ok, reason) == (False, "collected tests dropped from 5 to 4")


# ---- adw 3.5.0: a guard test proved by its mutation (spec item 7) --------------------------

# Fixture shape: the `Guard-mutation: src/app.py: return x => return x + 1` line Step 7's brief
# in this repo's prompt_plan.md names. src/ carries an __init__.py so src.app imports beside
# make_repo's src.py.
APP = (
    "# Description: the app under guard\n# Description: one function\n\n\ndef f(x):\n    return x\n"
)


GUARD = ("src/app.py", "return x", "return x + 1")


GUARD_TEST = "from src.app import f\n\n\ndef test_f_returns_its_input():\n    assert f(1) == 1\n"


def _guard_repo(tmp_path, guard="return x => return x + 1"):
    """make_repo with src/app.py at base and step 1 carrying `Guard-mutation: src/app.py:
    <guard>`, or no such line when `guard` is None."""
    repo, _ = make_repo(tmp_path)
    plan = PLAN if guard is None else _step1_with(f"Guard-mutation: src/app.py: {guard}")
    (repo / "prompt_plan.md").write_text(plan)
    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("")
    (repo / "src" / "app.py").write_text(APP)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "the app under guard")
    return repo


def _guard_begun(tmp_path, guard="return x => return x + 1"):
    repo = _guard_repo(tmp_path, guard)
    rc, _msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0
    (repo / "tests" / "test_app.py").write_text(GUARD_TEST)
    return repo


def test_check_proves_a_guard_test_by_its_mutation(tmp_path):
    repo = _guard_begun(tmp_path)
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    result = json.loads(next_auto.attended_result_path(repo, 1).read_text(encoding="utf-8"))
    assert result["red"] == "proved"
    assert result["guard_mutation"] == dict(zip(("path", "old", "new"), GUARD, strict=True))


def test_check_refuses_a_guard_mutation_the_test_does_not_notice(tmp_path):
    repo = _guard_begun(tmp_path, "return x => return x * 1")
    rc, lines = next_auto.check_step(repo, 1)
    assert (rc, lines) == (
        1,
        [
            "CHECK FAIL step 1: prove-red failed: the step's tests still pass with the guard "
            "mutation applied to src/app.py"
        ],
    )


@pytest.mark.parametrize(("old", "k"), [("return y", 0), ("x", 2)])
def test_check_refuses_a_guard_mutation_whose_old_text_is_not_there_once(tmp_path, old, k):
    repo = _guard_begun(tmp_path, f"{old} => z")
    rc, lines = next_auto.check_step(repo, 1)
    assert (rc, lines) == (
        1,
        [f"CHECK FAIL step 1: Guard-mutation: {old} occurs {k} times in src/app.py at base"],
    )


def test_check_without_a_guard_line_keeps_the_green_on_base_refusal(tmp_path):
    repo = _guard_begun(tmp_path, None)
    rc, lines = next_auto.check_step(repo, 1)
    assert (rc, lines) == (
        1,
        [
            "CHECK FAIL step 1: prove-red failed: the step's new/changed tests PASS against the "
            "base commit, so they do not demonstrate test-first"
        ],
    )
    result = next_auto.attended_result_path(repo, 1)
    assert not result.exists()


def test_prove_red_never_runs_the_mutation_when_the_base_is_red(tmp_path):
    # the step's test appends a line each time it runs, so a second line is the mutation's run
    repo = _guard_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    marker = tmp_path / "runs.txt"
    (repo / "tests" / "test_app.py").write_text(
        "from src.app import f\n\n\ndef test_f_adds_one():\n"
        f"    with open({str(marker)!r}, 'a') as fh:\n        fh.write('run\\n')\n"
        "    assert f(1) == 2\n"
    )
    git(repo, "add", "-A")
    proof: dict = {}
    staged = next_auto.staged_files(repo)
    reason = next_auto.prove_red(repo, repo, head, staged, TEST_CMD, guard=GUARD, proof=proof)
    assert reason is None
    assert marker.read_text() == "run\n"
    assert proof == {}


def test_gate_proves_a_guard_test_and_records_the_mutation(tmp_path):
    repo = _guard_repo(tmp_path)
    git(repo, "push", "origin", "main")
    next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = repo / ".next-auto" / "20260911-abc123" / "wt"
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    (wt / "tests" / "test_app.py").write_text(GUARD_TEST)
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert ok, reason
    path = next_auto.run_result_path(next_auto.load_state(repo), 1)
    result = json.loads(path.read_text(encoding="utf-8"))
    assert (result["red"], result["guard_mutation"]) == (
        "proved",
        dict(zip(("path", "old", "new"), GUARD, strict=True)),
    )


# ---- adw 3.5.0: a comment-only fix round passes prove-red (spec item 8) ---------------------

# Fixture shapes: the comment markers and documentation suffixes spec item 8 lists, over the
# src/app.py fixture above.
@pytest.mark.parametrize(
    ("path", "lines", "expected"),
    [
        ("src/app.py", ["# a comment", "", "    # an indented one"], True),
        ("web/app.ts", ["// a note", "/* a block", " * its middle", " */"], True),
        ("docs/notes.md", ["def f(x):", "    return x"], True),
        ("src/app.py", ['"""The docstring."""'], False),
        ("src/app.py", ["x = 1  # a note"], False),
        ("data/app.json", ["# not json"], False),
    ],
)
def test_comment_only_reads_comments_and_documentation(path, lines, expected):
    assert next_auto.comment_only(path, lines) is expected


def _app_first_round(tree):
    """Step 1's first round over src/app.py: a test red at base and the fix for it."""
    (tree / "tests" / "test_app.py").write_text(
        "from src.app import f\n\n\ndef test_f_adds_one():\n    assert f(1) == 2\n"
    )
    (tree / "src" / "app.py").write_text(APP.replace("return x", "return x + 1"))


def _comment_round(tree):
    """A fix round's change: a comment line in src/app.py and a docs/ page."""
    app = tree / "src" / "app.py"
    app.write_text(app.read_text().replace("def f(x):", "# f adds one to its input\ndef f(x):"))
    (tree / "docs").mkdir(exist_ok=True)
    (tree / "docs" / "app.md").write_text("# The app\n\nf adds one to its input.\n")


COMMENT_ROUND = ("n/a", "comment-only fix round")


NO_TEST_LINE = (
    "CHECK FAIL step 1: no staged test files; a step must add or change a test first "
    "(Mutation/test-first)"
)


def _attended_fix_round(tmp_path, first=_app_first_round):
    """Step 1 through its first round (`first`) and commit N, then check begin 1 on the DONE
    step."""
    repo = _guard_repo(tmp_path, None)
    attended_pass(repo, 1, first)
    set_done(repo, 1, "1 test green, mutation killed both ways, CHECK PASS")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0, msg
    return repo


def _record(repo, n=1):
    return json.loads(next_auto.attended_record_path(repo, n).read_text(encoding="utf-8"))


def test_check_passes_a_comment_only_fix_round_with_red_n_a(tmp_path, capsys):
    repo = _attended_fix_round(tmp_path)
    assert _record(repo)["fix_round"] is True
    _comment_round(repo)
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    result = json.loads(attended_result(repo).read_text(encoding="utf-8"))
    assert (result["red"], result["red_reason"]) == COMMENT_ROUND
    assert (result["passed"], result["collected"], result["lint"]) == (2, 2, "ok")


def test_check_refuses_the_same_staging_on_a_first_round(tmp_path):
    repo = _guard_repo(tmp_path, None)
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0, msg
    assert _record(repo)["fix_round"] is False
    _comment_round(repo)
    assert next_auto.check_step(repo, 1) == (1, [NO_TEST_LINE])


def test_check_refuses_a_fix_round_that_changes_a_code_line(tmp_path, capsys):
    repo = _attended_fix_round(tmp_path)
    app = repo / "src" / "app.py"
    app.write_text(app.read_text().replace("return x + 1", "return 1 + x"))
    assert next_auto.check_step(repo, 1) == (1, [NO_TEST_LINE])


def test_gate_passes_a_comment_only_fix_round_in_a_run(tmp_path, capsys):
    repo = _guard_repo(tmp_path, None)
    git(repo, "push", "origin", "main")
    next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = repo / ".next-auto" / "20260911-abc123" / "wt"
    assert next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)["fix_round"] is False
    _app_first_round(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    next_auto.set_step(repo, 1, "done", "1 test green, mutation killed both ways")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    assert next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)["fix_round"] is True
    _comment_round(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK) == (True, "gate passed")
    path = next_auto.run_result_path(next_auto.load_state(repo), 1)
    result = json.loads(path.read_text(encoding="utf-8"))
    assert (result["red"], result["red_reason"]) == COMMENT_ROUND


# ---- adw 3.5.0: a Test that collects nothing is apparatus (spec item 11) --------------------

# Live outputs captured on devbox on 2026-10-12 from 12:20:02 EDT (pytest 9.1.1, node v22.23.3,
# bun 1.4.2), each the whole output of the command above it with the directory cut to <dir>.
# `pytest -q -p no:cacheprovider -k nomatch` beside one test, exit 5
PYTEST_DESELECTED = "\n1 deselected in 0.00s\n"
# `pytest -p no:cacheprovider -k nomatch` beside one test, exit 5
PYTEST_DESELECTED_FULL = (
    "============================= test session starts ==============================\n"
    "platform linux -- Python 3.14.2, pytest-9.1.1, pluggy-1.6.0\n"
    "rootdir: <dir>\n"
    "collected 1 item / 1 deselected / 0 selected\n"
    "\n"
    "============================ 1 deselected in 0.00s =============================\n"
)
# `pytest -q -p no:cacheprovider` in a directory with no tests, exit 5
PYTEST_NONE = "\nno tests ran in 0.00s\n"
# `node --test` (TAP reporter) in a directory with no test files, exit 0: node exits 0 on
# it, so it reaches prove-red with a non-zero exit only from a Test that runs another command
# after it, which the stub stands for by exiting 1
NODE_NONE = (
    "TAP version 13\n1..0\n# tests 0\n# suites 0\n# pass 0\n# fail 0\n# cancelled 0\n"
    "# skipped 0\n# todo 0\n# duration_ms 8.159494\n"
)
# `bun test nomatch` beside one test file, exit 1
BUN_NO_MATCH = (
    "bun test v1.4.2 (744846f84)\n"
    'The following filters did not match any test files in --cwd="<dir>":\n'
    " nomatch\n"
    "1 files were searched [10.00ms]\n"
    "\n"
    'note: Tests need ".test", "_test_", ".spec" or "_spec_" in the filename '
    '(ex: "MyApp.test.ts")\n'
    "\n"
)
# `bun test` in a directory with no test files, exit 1
BUN_NONE = (
    "bun test v1.4.2 (744846f84)\n"
    "error: 0 test files matching **{.test,.spec,_test_,_spec_}.{js,ts,jsx,tsx} "
    'in --cwd="<dir>"\n'
    "\n"
)
# the summary block of `node --test` (TAP reporter) over one failing test, exit 1
NODE_ONE_FAILED = (
    "1..1\n# tests 1\n# suites 0\n# pass 0\n# fail 1\n# cancelled 0\n# skipped 0\n# todo 0\n"
    "# duration_ms 56.01582\n"
)


def _base_prints(out, code):
    """A Test that prints `out` and exits `code` while src.py has no FEATURE (the base), and
    prints a pytest pass once the step adds it."""
    script = (
        "import pathlib, sys\n"
        "if 'FEATURE' in pathlib.Path('src.py').read_text():\n"
        "    print('1 passed in 0.01s')\n"
        "    sys.exit(0)\n"
        f"sys.stdout.write({out!r})\n"
        f"sys.exit({code})\n"
    )
    return [sys.executable, "-c", script]


ZERO_TEST_RUNS = [
    (PYTEST_DESELECTED, 5, "1 deselected in 0.00s"),
    (
        PYTEST_DESELECTED_FULL,
        5,
        "============================ 1 deselected in 0.00s =============================",
    ),
    (PYTEST_NONE, 5, "no tests ran in 0.00s"),
    (NODE_NONE, 1, "# duration_ms 8.159494"),
    (
        BUN_NO_MATCH,
        1,
        'note: Tests need ".test", "_test_", ".spec" or "_spec_" in the filename '
        '(ex: "MyApp.test.ts")',
    ),
    (
        BUN_NONE,
        1,
        "error: 0 test files matching **{.test,.spec,_test_,_spec_}.{js,ts,jsx,tsx} "
        'in --cwd="<dir>"',
    ),
]


def _staged_good_step(tmp_path):
    repo, wt = start_wt(tmp_path)
    head = git(wt, "rev-parse", "HEAD").stdout.strip()
    good_step(wt)
    git(wt, "add", "-A")
    return repo, wt, head, next_auto.staged_files(wt)


@pytest.mark.parametrize(
    ("out", "code", "last"),
    ZERO_TEST_RUNS,
    ids=[
        "pytest-deselected",
        "pytest-deselected-full",
        "pytest-none",
        "node-none",
        "bun-no-match",
        "bun-none",
    ],
)
def test_prove_red_reads_a_base_that_collects_nothing_as_apparatus(tmp_path, out, code, last):
    repo, wt, head, staged = _staged_good_step(tmp_path)
    with pytest.raises(next_auto.ProveRedApparatusError) as exc:
        next_auto.prove_red(repo, wt, head, staged, _base_prints(out, code))
    assert str(exc.value) == f"Test collected no test at base: {last}"


def test_check_reports_a_base_that_collects_nothing_as_apparatus(tmp_path):
    repo, _ = make_repo(tmp_path)
    cmd = _base_prints(PYTEST_DESELECTED, 5)
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=cmd, lint_cmd=LINT_OK)
    assert rc == 0, msg
    good_step(repo)
    assert next_auto.check_step(repo, 1) == (
        2,
        ["CHECK APPARATUS step 1: Test collected no test at base: 1 deselected in 0.00s"],
    )
    rec = json.loads(next_auto.attended_record_path(repo, 1).read_text(encoding="utf-8"))
    assert rec["failures"] == 0


@pytest.mark.parametrize(
    ("out", "code"),
    [(NODE_ONE_FAILED, 1), ("the runner stopped on a signal\n", 1)],
    ids=["node-one-failed", "unread"],
)
def test_prove_red_keeps_a_red_that_ran_a_test_or_is_not_read(tmp_path, out, code):
    repo, wt, head, staged = _staged_good_step(tmp_path)
    assert next_auto.prove_red(repo, wt, head, staged, _base_prints(out, code)) is None


def test_prove_red_still_proves_a_real_pytest_red(tmp_path):
    repo, wt, head, staged = _staged_good_step(tmp_path)
    assert next_auto.prove_red(repo, wt, head, staged, TEST_CMD) is None


# Fix round (Chunk B review, Important): a Test that chains two runners collected nothing only
# when no runner it ran reports a test, each runner's summary read on its own.
# `sh -c "node --test 'web/**/*.test.js' && pytest -q -p no:cacheprovider tests"` with node's TAP
# reporter pinned, over a web/ with no test files and one failing pytest test, run on devbox on
# 2026-10-12 at 14:03:39 EDT (node v22.23.3, pytest 9.1.1), exit 1
NODE_NONE_THEN_PYTEST_FAILED = (
    "TAP version 13\n1..0\n# tests 0\n# suites 0\n# pass 0\n# fail 0\n# cancelled 0\n"
    "# skipped 0\n# todo 0\n# duration_ms 5.332927\n"
    "F                                                                        [100%]\n"
    "=================================== FAILURES ===================================\n"
    "___________________________________ test_new ___________________________________\n"
    "\n"
    "    def test_new():\n"
    ">       assert 1 == 2\n"
    "E       assert 1 == 2\n"
    "\n"
    "tests/test_new.py:2: AssertionError\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_new.py::test_new - assert 1 == 2\n"
    "1 failed in 0.01s\n"
)


# the other chains join the pinned outputs above in the order `sh -c "<first> && <second>"`
# prints them, the second runner's exit the chain's
@pytest.mark.parametrize(
    ("out", "code"),
    [
        (NODE_NONE_THEN_PYTEST_FAILED, 1),
        (BUN_CLEAN + PYTEST_DESELECTED, 5),
        (NODE_ONE_FAILED + PYTEST_NONE, 5),
    ],
    ids=["node-none-then-pytest-failed", "bun-ran-then-pytest-none", "node-ran-then-pytest-none"],
)
def test_prove_red_keeps_the_red_of_a_chain_where_a_runner_ran_a_test(tmp_path, out, code):
    repo, wt, head, staged = _staged_good_step(tmp_path)
    assert next_auto.prove_red(repo, wt, head, staged, _base_prints(out, code)) is None


def test_prove_red_reads_a_chain_where_no_runner_ran_a_test_as_apparatus(tmp_path):
    repo, wt, head, staged = _staged_good_step(tmp_path)
    with pytest.raises(next_auto.ProveRedApparatusError) as exc:
        next_auto.prove_red(repo, wt, head, staged, _base_prints(NODE_NONE + PYTEST_NONE, 5))
    assert str(exc.value) == "Test collected no test at base: no tests ran in 0.00s"


# Fix round (Chunk B review, Important; ruled 2026-10-12 14:00:56 EDT): a line starting `*`
# is a comment only inside a block, when the lines above it lead back to a `/*` opener.
# Fixture shapes: the JSDoc block of apps/web/e2e/console/support/navigation.ts:3 and the Go
# line at apps/dapi/cmd/registryctl/main.go:79 in acme/platform at 1566fad, each cut to the
# lines shown; the Rust and generator lines are rustfmt's and prettier's shapes for a deref
# assignment, a continued product and a generator method.
TS_DOC = (
    "/**\n"
    " * Navigates and waits for React hydration before returning. Next's App Router\n"
    " * appends the <next-route-announcer> element from a client effect, so its\n"
    " */\n"
    "export async function gotoHydrated(page: Page, path: string): Promise<void> {}\n"
)
GO_SET = (
    "package main\n\n"
    "func set(credential *string, decoded string) {\n"
    "\t*credential = manifestCredential(decoded)\n"
    "}\n"
)


@pytest.mark.parametrize(
    ("path", "lines", "expected"),
    [
        ("src/lib.rs", ["*count += 2;"], False),
        ("src/lib.rs", ["    * factor;"], False),
        ("cmd/main.go", ["\t*credential = manifestCredential(decoded)"], False),
        ("web/gen.ts", ["  *[Symbol.iterator]() {"], False),
        ("web/app.ts", ["/* a note */", "*count += 1;"], False),
        ("web/app.ts", ["/**", " * a note", " * ends here */", " * code after the block"], False),
        ("web/app.ts", TS_DOC.splitlines()[:4], True),
        ("src/lib.rs", ["/*", " * a block", "", " * after a blank line", " */"], True),
    ],
)
def test_comment_only_reads_a_star_line_as_a_comment_only_inside_a_block(path, lines, expected):
    assert next_auto.comment_only(path, lines) is expected


def test_comment_only_reads_a_changed_star_line_against_the_lines_above_it():
    doc = TS_DOC.splitlines()
    assert next_auto.comment_only("web/app.ts", doc, [1]) is True
    go = GO_SET.splitlines()
    assert next_auto.comment_only("cmd/main.go", go, [3]) is False


def _first_round_with_star_lines(tree):
    """Step 1's first round over src/app.py, with a TypeScript JSDoc block and a Go deref line
    beside it."""
    _app_first_round(tree)
    (tree / "web").mkdir(exist_ok=True)
    (tree / "web" / "app.ts").write_text(TS_DOC)
    (tree / "cmd").mkdir(exist_ok=True)
    (tree / "cmd" / "main.go").write_text(GO_SET)


def test_check_passes_a_fix_round_that_edits_a_jsdoc_line(tmp_path, capsys):
    repo = _attended_fix_round(tmp_path, first=_first_round_with_star_lines)
    app = repo / "web" / "app.ts"
    app.write_text(app.read_text().replace("so its\n", "so its presence\n"))
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    result = json.loads(attended_result(repo).read_text(encoding="utf-8"))
    assert (result["red"], result["red_reason"]) == COMMENT_ROUND


def test_check_refuses_a_fix_round_that_edits_a_go_deref_line(tmp_path, capsys):
    repo = _attended_fix_round(tmp_path, first=_first_round_with_star_lines)
    main = repo / "cmd" / "main.go"
    main.write_text(main.read_text().replace("manifestCredential(decoded)", "decoded"))
    assert next_auto.check_step(repo, 1) == (1, [NO_TEST_LINE])


# Fix round (Chunk B review round 2, ruled 2026-10-12 15:44:20 EDT, "Check unchanged lines
# too"): a round whose changed lines are all comments still takes prove-red when an unchanged
# line moves into or out of a `/* */` block between the two sides (spec item 8). Fixture shape:
# the commented-out assertion at test/tests.js:10 to 18 of has-symbols 1.1.0, read from
# acme/platform's node_modules on devbox on 2026-10-13.
JS_BLOCKED = (
    "\tt.notEqual(Symbol(), Symbol(), 'two symbols are not equal');\n"
    "\n"
    "\t/*\n"
    "\tt.equal(\n"
    "\t\tSymbol.prototype.toString.call(Symbol('foo')),\n"
    "\t\tSymbol.prototype.toString.call(Symbol('foo')),\n"
    "\t\t'two symbols with the same description stringify the same'\n"
    "\t);\n"
    "\t*/\n"
)


def _first_round_with_a_blocked_assertion(tree):
    """Step 1's first round over src/app.py, with a JavaScript file holding live code and a
    commented-out assertion beside it."""
    _app_first_round(tree)
    (tree / "web").mkdir(exist_ok=True)
    (tree / "web" / "tests.js").write_text(JS_BLOCKED)


def test_check_refuses_a_fix_round_that_uncomments_a_block_of_code(tmp_path, capsys):
    repo = _attended_fix_round(tmp_path, first=_first_round_with_a_blocked_assertion)
    js = repo / "web" / "tests.js"
    js.write_text(js.read_text().replace("\t/*\n", "").replace("\t*/\n", ""))
    assert next_auto.check_step(repo, 1) == (1, [NO_TEST_LINE])


def test_check_refuses_a_fix_round_that_comments_out_live_code(tmp_path, capsys):
    repo = _attended_fix_round(tmp_path, first=_first_round_with_a_blocked_assertion)
    js = repo / "web" / "tests.js"
    js.write_text(
        js.read_text().replace(
            "\tt.notEqual(Symbol(), Symbol(), 'two symbols are not equal');\n",
            "\t/*\n\tt.notEqual(Symbol(), Symbol(), 'two symbols are not equal');\n\t*/\n",
        )
    )
    assert next_auto.check_step(repo, 1) == (1, [NO_TEST_LINE])


def test_check_passes_a_fix_round_that_adds_a_note_beside_a_blocked_assertion(
    tmp_path, capsys
):
    repo = _attended_fix_round(tmp_path, first=_first_round_with_a_blocked_assertion)
    js = repo / "web" / "tests.js"
    note = "\t// kept until Symbol#toString is fixed\n"
    js.write_text(js.read_text().replace("\t/*\n", note + "\t/*\n"))
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    result = json.loads(attended_result(repo).read_text(encoding="utf-8"))
    assert (result["red"], result["red_reason"]) == COMMENT_ROUND


# Fixture shape: the line comment at apps/web/e2e/console/support/registry-stub.ts:2 in
# acme/platform at ff5e721, whose path glob carries a `/*` that opens no block.
GLOB_NOTE = "// (DOC-369). Tests intercept /api/agent-registry/* in the browser with\n"


def test_check_passes_a_fix_round_whose_line_comment_carries_a_block_opener(tmp_path, capsys):
    repo = _attended_fix_round(tmp_path, first=_first_round_with_a_blocked_assertion)
    js = repo / "web" / "tests.js"
    js.write_text(GLOB_NOTE + js.read_text())
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])


# Fix round (Chunk B review, Minor; ruled 2026-10-12 14:00:56 EDT): the prove-red subcommand
# applies the step's Guard-mutation: as check N and gate N do. The subcommand reads Test from
# the worktree plan, so the fixture plan names this interpreter's pytest there.
def test_prove_red_subcommand_proves_a_guard_test_by_its_mutation(tmp_path, capsys):
    repo = _guard_repo(tmp_path)
    plan = repo / "prompt_plan.md"
    plan.write_text(
        plan.read_text().replace("- Test: uv run pytest", f"- Test: {shlex.join(TEST_CMD)}")
    )
    git(repo, "commit", "-qam", "the fixture's Test")
    git(repo, "push", "origin", "main")
    next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = repo / ".next-auto" / "20260911-abc123" / "wt"
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    (wt / "tests" / "test_app.py").write_text(GUARD_TEST)
    capsys.readouterr()
    assert next_auto.main(["--root", str(repo), "prove-red", "1"]) == 0
    assert capsys.readouterr().out.strip().splitlines() == [
        "PROVE-RED PASS: the guard mutation applied to src/app.py turns the step's tests red"
    ]


# ---- adw 3.5.0: check N judges Lint after Test and the counts (spec item 13) -------------

# check N judges Lint after Test and the counts, on both branches of the check
def test_check_runs_lint_after_a_red_test_and_reports_both(tmp_path):
    repo, _ = make_repo(tmp_path)
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_RED)[0] == 0
    good_step(repo)
    (repo / "src.py").write_text(
        "# Description: fixture\n# Description: two\nX = 1\nFEATURE = 41\n"
    )
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert lines[0].startswith("CHECK FAIL step 1: tests failed: ")
    assert lines[1:] == ["CHECK FAIL step 1: Lint failed: E501 line too long"]
    assert json.loads(attended_record(repo).read_text())["failures"] == 1


def test_check_counts_a_red_step_whose_lint_cannot_start(tmp_path):
    """A Lint that cannot start counts no attempt on its own, as a prove-red apparatus does;
    a step that also failed another check counts that failure, the Lint line with it."""
    repo, _ = make_repo(tmp_path)
    assert next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_MISSING)[0] == 0
    good_step(repo)
    (repo / "src.py").write_text(
        "# Description: fixture\n# Description: two\nX = 1\nFEATURE = 41\n"
    )
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert lines[0].startswith("CHECK FAIL step 1: tests failed: ")
    assert lines[1:] == [f"CHECK FAIL step 1: {LINT_MISSING_LINE}"]
    assert json.loads(attended_record(repo).read_text())["failures"] == 1


# Fix round (Chunk C review, Minor; ruled 2026-10-12 15:44:20 EDT): a step that fails no other
# check prints one CHECK APPARATUS line per apparatus, a base Test that collects nothing and a
# Lint that cannot start both shown, and counts no attempt
def test_check_reports_each_apparatus_of_a_step_that_failed_nothing_else(tmp_path):
    repo, _ = make_repo(tmp_path)
    cmd = _base_prints(PYTEST_DESELECTED, 5)
    rc, msg = next_auto.check_begin(repo, 1, test_cmd=cmd, lint_cmd=LINT_MISSING)
    assert rc == 0, msg
    good_step(repo)
    assert next_auto.check_step(repo, 1) == (
        2,
        [
            "CHECK APPARATUS step 1: Test collected no test at base: 1 deselected in 0.00s",
            f"CHECK APPARATUS step 1: {LINT_MISSING_LINE}",
        ],
    )
    assert json.loads(attended_record(repo).read_text())["failures"] == 0


def test_check_fails_an_exempt_step_whose_lint_is_red(tmp_path):
    repo, _stub = _exempt_begun(tmp_path, "echo '19 of 19 PASS'\n")
    rec_path = attended_record(repo)
    rec = json.loads(rec_path.read_text())
    rec["lint_cmd"] = LINT_RED
    rec_path.write_text(json.dumps(rec, indent=2))
    assert next_auto.check_step(repo, 1) == (
        1, ["CHECK FAIL step 1: Lint failed: E501 line too long"]
    )  # fmt: skip
    assert not attended_result(repo).exists()


# ---- adw 3.5.0: verdict N keeps only findings with a quoted span (spec item 12) ----------


# A port of countFindings in tools/workflows/record.mjs of acme/platform at 1566fad: the line
# rules the record counts a kept report's findings by, raising where it throws
def count_findings(report_text):
    headers = {"#### Critical": "critical", "#### Important": "important", "#### Minor": "minor"}
    found = dict.fromkeys(headers.values(), False)
    result = dict.fromkeys(headers.values(), 0)
    current = None
    under_bullet = False
    for raw in report_text.split("\n"):
        trimmed = raw.strip()
        if trimmed in headers:
            current = headers[trimmed]
            found[current] = True
            under_bullet = False
            continue
        if re.match(r"#{1,6}\s", trimmed):
            current = None
            continue
        if current is None:
            continue
        if re.match(r"[-*] ", raw):
            result[current] += 1
            under_bullet = True
            continue
        if trimmed == "" or re.match(r"None(?:$|\.|\s*\()", trimmed):
            continue
        if under_bullet and re.match(r"\s", raw):
            continue
        raise ValueError(f"not a bullet, a line under one, or None: {trimmed[:80]!r}")
    missing = [k for k, seen in found.items() if not seen]
    if missing:
        raise ValueError(f"missing headers: {missing}")
    return result


# the module the findings quote: `total` on lines 4 and 5, constants on 7 to 38, `rounded` on 40
SPAN_APP = (
    "# Description: fixture module for the quoted-span tests\n# Description: two\n\n"
    "def total(items, rate):\n    return sum(items) * rate\n\n"
    + "".join(f"C{k} = {k}\n" for k in range(7, 39))
    + "\ndef rounded(value):\n    return round(value, 2)\n"
)


def _span_repo(tmp_path):
    """make_repo with src/app.py committed at the head the review covers, and line 9 changed
    in the working tree after it, so a span read from the tree instead of the head shows."""
    repo, _ = make_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text(SPAN_APP)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "the reviewed head")
    app = repo / "src" / "app.py"
    app.write_text(SPAN_APP.replace("C9 = 9\n", "C9 = rate_limit(9)\n"))
    return repo, head_sha(repo)


# Fixture shape: the Chunk B round-1 report saved at .next-auto/attended/
# review-6ecad318bdb1fbe3233559c23f8168d63e5309e9-1.md in this repository, its sections kept and
# its findings rewritten over src/app.py; the backticked span and its lost indentation are the
# reviewer's, as that report wrote its spans
SPAN_KEPT = (
    "- `src/app.py:4`. `total` multiplies before it rounds, so a rate of 0.105 loses a cent.\n"
    "  > `def total(items, rate):     return sum(items) * rate`\n"
    "\n"
    "  Fix: round inside `total`.\n"
)
SPAN_ABSENT = "- `src/app.py:9`. The limit wraps a constant.\n  > C9 = rate_limit(9)\n"
SPAN_NONE = "- `src/app.py:40`. `rounded` has no test of a negative value.\n"
SPAN_NO_LOC = "- The fixture names read oddly.\n  > C7 = 7\n"


def _span_report(important, minor):
    return (
        "### Spec compliance\n- Compliant. The step does what its brief asks.\n\n"
        "### Strengths\n- Each test pins the exact line.\n\n"
        "### Scope\nNone\n\n"
        "### Requirement gaps\n- The step names no requirement id.\n\n"
        "### Findings\n#### Critical\nNone\n\n"
        f"#### Important\n{important}\n"
        f"#### Minor\n{minor}\n"
        "### Verdict\nStep quality: Approved\nReasoning: The findings go to the plan.\n"
    )


def test_verdict_drops_findings_whose_span_is_absent_or_missing(tmp_path, capsys):
    repo, sha = _span_repo(tmp_path)
    report = tmp_path / "reviewer-report.md"
    text = _span_report(SPAN_KEPT + SPAN_ABSENT, SPAN_NONE + SPAN_NO_LOC)
    report.write_text(text)
    assert next_auto.main(verdict_argv(repo, 1, sha, 1, "Approved", report)) == 0
    capsys.readouterr()
    kept = repo / ".next-auto" / "attended" / f"review-{sha}-1.md"
    assert kept.read_text() == (
        _span_report(SPAN_KEPT, "None\n")
        + "\n### Dropped findings\n"
        + SPAN_ABSENT
        + f"  Dropped from Important: the quoted span does not occur in src/app.py at {sha[:8]}\n"
        + SPAN_NONE
        + "  Dropped from Minor: no `> ` line quoting the file\n"
        + SPAN_NO_LOC
        + "  Dropped from Minor: no file:line at the start of the finding\n"
    )


def test_verdict_keeps_a_report_the_record_counts_and_the_reviewers_text_beside_it(
    tmp_path, capsys
):
    repo, sha = _span_repo(tmp_path)
    report = tmp_path / "reviewer-report.md"
    text = _span_report(SPAN_KEPT + SPAN_ABSENT, SPAN_NONE + SPAN_NO_LOC)
    report.write_text(text)
    assert next_auto.main(verdict_argv(repo, 1, sha, 1, "Approved", report)) == 0
    capsys.readouterr()
    attended = repo / ".next-auto" / "attended"
    assert count_findings(text) == {"critical": 0, "important": 2, "minor": 2}
    kept = (attended / f"review-{sha}-1.md").read_text()
    assert count_findings(kept) == {"critical": 0, "important": 1, "minor": 0}
    assert (attended / f"review-{sha}-1.orig.md").read_text() == text
    verdict = json.loads((attended / f"verdict-{sha}.json").read_text())
    assert verdict["verdict"] == "Approved"


def test_verdict_names_the_dropped_findings_on_its_output_line(tmp_path, capsys):
    repo, sha = _span_repo(tmp_path)
    report = tmp_path / "reviewer-report.md"
    report.write_text(_span_report(SPAN_KEPT + SPAN_ABSENT, SPAN_NONE))
    assert next_auto.main(verdict_argv(repo, 1, sha, 1, "Approved", report)) == 0
    verdict = repo / ".next-auto" / "attended" / f"verdict-{sha}.json"
    assert capsys.readouterr().out.strip() == (
        f"VERDICT Approved {verdict} (dropped 2: src/app.py:9, src/app.py:40)"
    )


def test_verdict_keeps_a_report_whose_spans_all_occur_byte_identical(tmp_path, capsys):
    repo, sha = _span_repo(tmp_path)
    report = tmp_path / "reviewer-report.md"
    path_span = f"- `{repo / 'src' / 'app.py'}:40` and `:41`. No negative test.\n"
    text = _span_report(SPAN_KEPT, path_span + "  > def rounded(value):\n  > return round(\n")
    report.write_bytes(text.encode())
    assert next_auto.main(verdict_argv(repo, 1, sha, 1, "Approved", report)) == 0
    attended = repo / ".next-auto" / "attended"
    verdict = attended / f"verdict-{sha}.json"
    assert capsys.readouterr().out.strip() == f"VERDICT Approved {verdict}"
    assert (attended / f"review-{sha}-1.md").read_bytes() == text.encode()
    assert not (attended / f"review-{sha}-1.orig.md").exists()


def test_verdict_drops_a_finding_on_a_file_the_head_does_not_hold(tmp_path, capsys):
    repo, sha = _span_repo(tmp_path)
    report = tmp_path / "reviewer-report.md"
    report.write_text(_span_report(SPAN_KEPT, "- `src/gone.py:3`. Unused.\n  > X = 1\n"))
    assert next_auto.main(verdict_argv(repo, 1, sha, 1, "Approved", report)) == 0
    capsys.readouterr()
    kept = (repo / ".next-auto" / "attended" / f"review-{sha}-1.md").read_text()
    assert kept.endswith(
        "### Dropped findings\n- `src/gone.py:3`. Unused.\n  > X = 1\n"
        f"  Dropped from Minor: src/gone.py is not in the tree at {sha[:8]}\n"
    )


# ---- adw 3.5.0: wrap-up writes the three body lines (spec item 2) -----------------------

# Fixture shapes: the plan header of a acme/platform plan (`Spec: <path>. Owner: the owner.`), the
# Closes: line spec item 2 defines after Mutation:, and the plan.approved line approval_line
# copies from a acme/platform record
BODY_PLAN = f"""\
# Fixture Implementation Plan
Spec: docs/specs/x.md. Owner: the owner.

## Conventions binding every step
- Test: uv run pytest
- Lint: uv run ruff check .
- Run: uv run python -m fixturepkg
- Record: tools/record-stub {RECORD_SLUG}

### Step 1: first
Status: PENDING
Eval: no
Mutation: required
Closes: R1
Write a failing test then implement.

### Step 2: second
Status: PENDING
Eval: no
Mutation: n/a (docs only)
Closes: R2, R1
Second step.

### Step 3: third
Status: PENDING
Eval: no
Mutation: n/a (docs only)
Closes: R9
Third step.

## Decisions
(none)

## Findings
(none)
"""
BODY_LINES = "\n\nSpec: docs/specs/x.md\nCloses: R1, R2\nApproved-by: @ryanmat\n"


def _body_run(tmp_path, plan_text=BODY_PLAN, approved=True, done=(1, 2)):
    repo, _control = record_repo(tmp_path, plan_text=plan_text, approved=approved)
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20261013-body12", sync=False)
    for n in done:
        next_auto.set_step(repo, n, "done", "a")
    return repo, state


def test_wrap_up_ends_both_bodies_with_the_three_lines(tmp_path, monkeypatch):
    repo, state = _body_run(tmp_path)
    monkeypatch.setattr(next_auto, "gh", fake_gh_visibility([], private=True))
    next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    run_dir = Path(state["run_dir"])
    for name in ("summary.md", "pr-body.md"):
        assert (run_dir / name).read_text().endswith(BODY_LINES), name
    parsed = subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        input=(run_dir / "pr-body.md").read_text(), capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert parsed.stdout == "Spec: docs/specs/x.md\nCloses: R1, R2\nApproved-by: @ryanmat\n"


def test_wrap_up_omits_approved_by_for_a_plan_with_no_record(tmp_path, monkeypatch):
    repo, _origin = make_repo(tmp_path)
    plain = BODY_PLAN.replace(f"- Record: tools/record-stub {RECORD_SLUG}\n", "")
    (repo / "prompt_plan.md").write_text(plain)
    git(repo, "commit", "-qam", "plan with no Record")
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20261013-body12", sync=False)
    next_auto.set_step(repo, 1, "done", "a")
    monkeypatch.setattr(next_auto, "gh", fake_gh_visibility([], private=False))
    next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    body = (Path(state["run_dir"]) / "pr-body.md").read_text()
    assert body.endswith("\n\nSpec: docs/specs/x.md\nCloses: R1\n")
    assert "Approved-by:" not in body


def test_wrap_up_omits_closes_when_no_done_step_carries_one(tmp_path, monkeypatch):
    repo, state = _body_run(tmp_path, done=())
    monkeypatch.setattr(next_auto, "gh", fake_gh_visibility([], private=False))
    next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    body = (Path(state["run_dir"]) / "pr-body.md").read_text()
    assert body.endswith("\n\nSpec: docs/specs/x.md\nApproved-by: @ryanmat\n")
    assert "Closes:" not in body


def test_wrap_up_stops_before_the_push_when_the_record_has_no_approval(tmp_path, monkeypatch):
    repo, state = _body_run(tmp_path, approved=False)

    def no_gh(*_a, **_k):
        pytest.fail("gh was called before the approval check")

    monkeypatch.setattr(next_auto, "gh", no_gh)
    with pytest.raises(RuntimeError) as exc:
        next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    assert str(exc.value) == (
        f"wrap-up: no plan.approved line for {next_plan.plan_hash(BODY_PLAN)}"
    )
    assert next_auto.find_state_file(repo) is not None
    branch_ref = f"refs/heads/{state['branch']}"
    pushed = git(tmp_path / "origin.git", "rev-parse", "--verify", branch_ref, check=False)
    assert pushed.returncode != 0


# ---- adw 3.5.0: wrap-up runs the record sync before the push (spec item 6) ---------------

# The stub appender above stands in for record.mjs on origin's default branch; its `sync`
# appends one line, as the record_pr_opened tests' append does


def _pushed_subjects(tmp_path, branch):
    """The first-parent subjects of the run branch as origin holds it, oldest first."""
    out = git(tmp_path / "origin.git", "log", "--first-parent", "--reverse", "--format=%s", branch)
    return out.stdout.splitlines()


def test_wrap_up_syncs_the_record_after_its_commit_and_before_the_push(tmp_path, monkeypatch):
    repo, wt, control = record_run(tmp_path, approved=True)
    branch = next_auto.load_state(repo)["branch"]
    run_step_one(repo, wt)
    next_auto.set_step(repo, 2, "done", "b")
    calls = []
    monkeypatch.setattr(
        next_auto, "gh", gh_seeing_the_pushed_head(calls, tmp_path / "origin.git", branch)
    )
    next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    [sync] = [c for c in stub_calls(control) if c["argv"][0] == "sync"]
    assert sync["argv"] == ["sync", RECORD_SLUG] and Path(sync["cwd"]) == wt.resolve()
    subjects = _pushed_subjects(tmp_path, branch)
    first = subjects.index(f"chore(record): {RECORD_SLUG}")
    assert subjects[first + 1] == f"chore(record): sync {RECORD_SLUG}"
    [opened_on] = [c[1] for c in calls if c[0] == "pushed-head"]
    sync_sha = git(wt, "log", "-1", "--format=%H", f"--grep=^chore(record): sync {RECORD_SLUG}$")
    assert sync_sha.stdout.strip() == opened_on
    changed = git(wt, "show", "--name-only", "--format=", opened_on).stdout.split()
    assert changed == [RECORD_FILE]
    assert git(wt, "show", f"{opened_on}:{RECORD_FILE}").stdout.endswith("- stub sync\n")


def test_wrap_up_commits_nothing_when_the_sync_changes_nothing(tmp_path, monkeypatch):
    repo, _wt, control = record_run(tmp_path, approved=True)
    branch = next_auto.load_state(repo)["branch"]
    (control / "sync-silent").write_text("")
    next_auto.set_step(repo, 1, "done", "a")
    monkeypatch.setattr(next_auto, "gh", fake_gh_visibility([], private=True))
    next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    assert [c["argv"] for c in stub_calls(control) if c["argv"][0] == "sync"] == [
        ["sync", RECORD_SLUG]
    ]
    assert not [s for s in _pushed_subjects(tmp_path, branch) if "sync" in s]


def test_wrap_up_stops_before_the_push_when_the_sync_fails(tmp_path, monkeypatch):
    repo, _wt, control = record_run(tmp_path, approved=True)
    branch = next_auto.load_state(repo)["branch"]
    stub_rc(control, "sync", 1)
    next_auto.set_step(repo, 1, "done", "a")

    def no_gh(*_a, **_k):
        pytest.fail("gh was called after a failed sync")

    monkeypatch.setattr(next_auto, "gh", no_gh)
    with pytest.raises(RuntimeError) as exc:
        next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    assert str(exc.value) == "wrap-up: record sync failed: stub sync refused"
    assert next_auto.find_state_file(repo) is not None
    pushed = git(tmp_path / "origin.git", "rev-parse", "--verify", branch, check=False)
    assert pushed.returncode != 0


def test_wrap_up_runs_no_sync_for_a_plan_with_no_record(tmp_path, monkeypatch):
    repo, control = record_repo(tmp_path, plan_text=PLAN)
    next_auto.start(repo, "prompt_plan.md", "main", run_id="20261013-sync13", sync=False)
    next_auto.set_step(repo, 1, "done", "a")
    monkeypatch.setattr(next_auto, "gh", fake_gh_visibility([], private=True))
    next_auto.wrap_up(repo, repo_slug="ryanmat/fixture")
    assert stub_calls(control) == []


# ---- adw 3.5.2: the step cap and the run cap have one source (spec item 2, R2) ------------


def test_the_step_cap_and_the_run_cap_are_read_from_next_plan():
    """validate compares Step-size: against the run cap, so the constant lives in next_plan.py
    and next_auto.py reads it from there; the gate's default limit is the plan module's too."""
    assert next_auto.RUN_SIZE_LIMIT == next_plan.RUN_SIZE_LIMIT == 500
    assert next_plan.STEP_SIZE_DEFAULT == 300
    limit = inspect.signature(next_auto.check_step_size).parameters["limit"].default
    assert limit == next_plan.STEP_SIZE_DEFAULT


# ---- adw 3.5.2: the gate's refusal names estimate and measurement (spec item 2, R2) -------


SIZED_PLAN = PLAN.replace("Size: 20\n", "Size: 120\n")
UNSIZED_PLAN = PLAN.replace("Size: 20\n", "")
assert SIZED_PLAN != PLAN != UNSIZED_PLAN


def _staged_count(wt, base) -> int:
    """The gate's measurement, read from the same git diff --shortstat the engine reads."""
    out = git(wt, "diff", "--cached", "--shortstat", base).stdout
    ins = re.search(r"(\d+) insertion", out)
    dels = re.search(r"(\d+) deletion", out)
    return (int(ins.group(1)) if ins else 0) + (int(dels.group(1)) if dels else 0)


def _over_cap_step(wt):
    """A test-first step with a 400-line file beside it, over the 300 default and under 450."""
    good_step(wt)
    (wt / "huge.py").write_text("\n".join(f"x{i} = {i}" for i in range(400)) + "\n")


def test_check_step_size_names_the_estimate_beside_the_measurement(tmp_path):
    _repo, wt = start_wt(tmp_path)
    base = git(wt, "rev-parse", "HEAD").stdout.strip()
    (wt / "huge.py").write_text("\n".join(f"x{i} = {i}" for i in range(400)) + "\n")
    git(wt, "add", "-A")
    changed = _staged_count(wt, base)
    assert changed == 400
    reason = next_auto.check_step_size(wt, base, limit=300, estimate=120)
    assert reason == f"step changes {changed} lines, over the 300-line per-step cap (estimated 120)"


def test_check_step_size_says_no_estimate_without_one(tmp_path):
    _repo, wt = start_wt(tmp_path)
    base = git(wt, "rev-parse", "HEAD").stdout.strip()
    (wt / "huge.py").write_text("\n".join(f"x{i} = {i}" for i in range(400)) + "\n")
    git(wt, "add", "-A")
    reason = next_auto.check_step_size(wt, base, limit=300)
    assert reason == "step changes 400 lines, over the 300-line per-step cap (no estimate)"


def test_check_step_size_takes_a_measured_count_in_place_of_the_diff(tmp_path):
    """Step 6's callers measure once and pass the count; the diff is read only without it."""
    _repo, wt = start_wt(tmp_path)
    base = git(wt, "rev-parse", "HEAD").stdout.strip()
    reason = next_auto.check_step_size(wt, base, limit=300, estimate=120, changed=301)
    assert reason == "step changes 301 lines, over the 300-line per-step cap (estimated 120)"
    assert next_auto.check_step_size(wt, base, limit=300, changed=300) is None


def _gate_on_plan(tmp_path, plan_text):
    """A run whose plan is plan_text, Step 1 begun and the over-cap step staged by the gate."""
    repo, _ = make_repo(tmp_path)
    if plan_text != PLAN:
        (repo / "prompt_plan.md").write_text(plan_text)
        git(repo, "commit", "-qam", "plan variant")
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = Path(state["worktree"])
    next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    base = git(wt, "rev-parse", "HEAD").stdout.strip()
    _over_cap_step(wt)
    ok, reason = next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    return ok, reason, _staged_count(wt, base)


def test_gate_reads_the_step_cap_from_the_worktree_plan(tmp_path):
    raised = PLAN.replace("- Run: uv run python -m fixturepkg\n",
                          "- Run: uv run python -m fixturepkg\n- Step-size: 450\n")  # fmt: skip
    ok, reason, changed = _gate_on_plan(tmp_path, raised)
    assert ok, reason
    assert 300 < changed <= 450


def test_gate_refuses_the_same_step_under_the_default_cap(tmp_path):
    ok, reason, changed = _gate_on_plan(tmp_path, UNSIZED_PLAN)
    assert (ok, reason) == (
        False, f"step changes {changed} lines, over the 300-line per-step cap (no estimate)"
    )  # fmt: skip


def test_gate_refusal_carries_the_step_size_line_as_the_estimate(tmp_path):
    ok, reason, changed = _gate_on_plan(tmp_path, SIZED_PLAN)
    assert (ok, reason) == (
        False, f"step changes {changed} lines, over the 300-line per-step cap (estimated 120)"
    )  # fmt: skip


# ---- adw 3.5.2: the result file carries size_estimate and size_changed (spec item 5, R5) --


def _all_changes_count(tree, base) -> int:
    """Every change in the tree against the base, new files included, measured as the gate
    measures a staged tree, through a scratch index so the real one is never touched."""
    scratch = tree / ".next-auto" / "scratch-index"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_INDEX_FILE": str(scratch)}
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True, env=env)
    out = subprocess.run(
        ["git", "-C", str(tree), "diff", "--cached", "--shortstat", base],
        capture_output=True, text=True, check=True, env=env,
    ).stdout
    scratch.unlink()
    ins = re.search(r"(\d+) insertion", out)
    dels = re.search(r"(\d+) deletion", out)
    return (int(ins.group(1)) if ins else 0) + (int(dels.group(1)) if dels else 0)


def test_gate_writes_the_estimate_and_the_measured_count(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "prompt_plan.md").write_text(SIZED_PLAN)
    git(repo, "commit", "-qam", "plan variant")
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = Path(state["worktree"])
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK) == (True, "gate passed")
    result = json.loads((Path(state["run_dir"]) / "result-1.json").read_text())
    changed = _staged_count(wt, state["base_head"])
    assert changed > 0
    assert (result["size_estimate"], result["size_changed"]) == (120, changed)


def test_gate_writes_a_null_estimate_for_a_step_without_the_line(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "prompt_plan.md").write_text(UNSIZED_PLAN)
    git(repo, "commit", "-qam", "plan variant")
    state = next_auto.start(repo, "prompt_plan.md", "main", run_id="20260911-abc123", sync=False)
    wt = Path(state["worktree"])
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)[0]
    result = json.loads((Path(state["run_dir"]) / "result-1.json").read_text())
    assert result["size_estimate"] is None
    assert result["size_changed"] == _staged_count(wt, state["base_head"])


def test_check_writes_the_pair_measured_through_the_check_index(tmp_path):
    """The attended check measures the step through its own index with nothing staged in the
    real one, so a measurement read from the real index would be zero."""
    repo = begun(tmp_path, plan_text=SIZED_PLAN)
    base = head_sha(repo)
    good_step(repo)
    assert git(repo, "diff", "--cached", "--shortstat", base).stdout == ""
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    result = json.loads(attended_result(repo).read_text())
    changed = _all_changes_count(repo, base)
    assert changed > 0
    assert (result["size_estimate"], result["size_changed"]) == (120, changed)
    assert git(repo, "diff", "--cached", "--shortstat", base).stdout == ""


def test_the_pair_is_written_after_the_tree_and_counts_deletions(tmp_path):
    repo = begun(tmp_path, plan_text=UNSIZED_PLAN)
    base = head_sha(repo)
    good_step(repo)
    # one deleted line beside the step's insertions, so a count of insertions alone is short
    (repo / "src.py").write_text("# Description: fixture\n# Description: two\nFEATURE = 42\n")
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    text = attended_result(repo).read_text()
    assert text.index('"tree"') < text.index('"size_estimate"') < text.index('"size_changed"')
    result = json.loads(text)
    changed = _all_changes_count(repo, base)
    assert (result["size_estimate"], result["size_changed"]) == (None, changed)


# ---- adw 3.5.2: the pair in the wrap-up summary and over each step report (spec item 5, R5) -


SIZE_LINE = "Size: 187 changed lines against an estimate of 220"


def _result_with_size(run_dir, n, estimate, changed):
    """A step's result file in the shape of tests/fixtures/record-evidence/result-auto.json."""
    result = json.loads((EVIDENCE / "result-auto.json").read_text())
    result.update({"step": n, "size_estimate": estimate, "size_changed": changed})
    (run_dir / f"result-{n}.json").write_text(json.dumps(result, indent=2))


def test_size_line_renders_the_pair_and_the_missing_estimate():
    assert next_auto.size_line({"size_estimate": 220, "size_changed": 187}) == SIZE_LINE
    assert next_auto.size_line({"size_estimate": None, "size_changed": 187}) == (
        "Size: 187 changed lines against no estimate"
    )


def test_summary_step_line_carries_the_pair_from_the_result_file(tmp_path):
    _repo, state, _wt, run_dir = _run_with_report_and_paths(tmp_path)
    _result_with_size(run_dir, 1, 220, 187)
    summary = next_auto.write_summary(state, reason="stopped after 1 of 2 steps").read_text()
    assert f"- Step 1: first: DONE (3 tests green, aaa111); {SIZE_LINE}\n" in summary
    # a step with no result file keeps today's line
    assert "- Step 2: second: PENDING\n" in summary


def test_summary_step_line_says_no_estimate_for_a_null_one(tmp_path):
    _repo, state, _wt, run_dir = _run_with_report_and_paths(tmp_path)
    _result_with_size(run_dir, 1, None, 187)
    summary = next_auto.write_summary(state, reason="stopped after 1 of 2 steps").read_text()
    assert (
        "- Step 1: first: DONE (3 tests green, aaa111); Size: 187 changed lines against no "
        "estimate\n"
    ) in summary


def test_step_report_header_is_followed_by_the_size_line(tmp_path):
    _repo, state, _wt, run_dir = _run_with_report_and_paths(tmp_path)
    _result_with_size(run_dir, 1, 220, 187)
    summary = next_auto.write_summary(state, reason="stopped after 1 of 2 steps").read_text()
    assert f"#### report-1\n{SIZE_LINE}\n\n# Report step 1\n" in summary


def test_summary_names_an_unreadable_result_file_on_stderr_and_keeps_the_line(tmp_path, capsys):
    """A result file that does not parse loses the step its size sentence and nothing else,
    and the loss is named on stderr rather than swallowed."""
    _repo, state, _wt, run_dir = _run_with_report_and_paths(tmp_path)
    (run_dir / "result-1.json").write_text("{not json")
    summary = next_auto.write_summary(state, reason="stopped after 1 of 2 steps").read_text()
    assert "- Step 1: first: DONE (3 tests green, aaa111)\n" in summary
    assert "Size:" not in summary
    err = capsys.readouterr().err
    path = run_dir / "result-1.json"
    assert err.startswith(f"summary: result file {path} unreadable, no size line: ")
    # once per read: the step line and the report header, in each of the two rendered bodies
    assert err.count("\n") == 4, err


def test_step_report_block_is_unchanged_without_a_result_file(tmp_path):
    _repo, state, _wt, _run_dir = _run_with_report_and_paths(tmp_path)
    summary = next_auto.write_summary(state, reason="stopped after 1 of 2 steps").read_text()
    assert "#### report-1\n\n# Report step 1\n" in summary
    assert "Size:" not in summary


def test_pr_body_carries_the_per_step_size_line_whole(tmp_path):
    _repo, state, _wt, run_dir = _run_with_report_and_paths(tmp_path)
    _result_with_size(run_dir, 1, 220, 187)
    next_auto.write_summary(state, reason="stopped after 1 of 2 steps")
    body = (run_dir / "pr-body.md").read_text()
    assert f"- Step 1: first: DONE (3 tests green, aaa111); {SIZE_LINE}\n" in body
    assert "#### report-1" not in body


# ---- adw 3.5.2: auto preflight requires the estimate on the open steps (spec item 4, R4) ----


UNSIZED_LINE = "plan: Step {n} carries no Size: line; auto runs need the estimate"


def test_preflight_refuses_the_first_pending_step_without_a_size_line(tmp_path):
    """A DONE step without the line is never named; the first open one without it is."""
    specs = [(1, "DONE (x)", False), (2, "PENDING", False), (3, "PENDING", False)]
    assert _preflight_reasons(tmp_path, specs, unsized={1, 3}) == [UNSIZED_LINE.format(n=3)]


def test_preflight_names_an_in_progress_step_without_a_size_line(tmp_path):
    specs = [(1, "DONE (x)", False), (2, "IN PROGRESS", False), (3, "PENDING", False)]
    assert _preflight_reasons(tmp_path, specs, unsized={2, 3}) == [UNSIZED_LINE.format(n=2)]


def test_preflight_passes_when_every_open_step_carries_a_size_line(tmp_path):
    specs = [(1, "DONE (x)", False), (2, "PENDING", False), (3, "PENDING", False)]
    assert _preflight_reasons(tmp_path, specs, unsized={1}) == []


def test_preflight_scans_every_open_step_not_only_the_next_batch(tmp_path):
    """Decisions: the scan has no batch bound, unlike the Test-repo scan's DEFAULT_STEPS."""
    specs = [(n, "PENDING", False) for n in range(1, 6)]
    assert _preflight_reasons(tmp_path, specs, unsized={5}) == [UNSIZED_LINE.format(n=5)]


def test_preflight_unsized_reason_follows_the_test_repo_reason_and_is_one_line(tmp_path):
    specs = [(1, "PENDING", True), (2, "PENDING", False), (3, "PENDING", False)]
    assert _preflight_reasons(tmp_path, specs, unsized={2, 3}) == [
        "step 1 carries Test-repo and runs attended; auto mode works the next 3 pending steps",
        UNSIZED_LINE.format(n=2),
    ]


def test_preflight_gives_an_invalid_plan_its_own_reasons_and_no_unsized_line(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "prompt_plan.md").write_text(PLAN.replace("Size: 20\n", "Size: many\n"))
    git(repo, "commit", "-qam", "plan variant")
    git(repo, "fetch", "origin")
    reasons = next_auto.preflight_checks(repo, repo / "prompt_plan.md", **base_kwargs())
    assert reasons == [
        "plan does not validate (run next_plan.py validate for line-numbered reasons)"
    ]


def test_attended_check_begin_and_check_pass_a_step_without_a_size_line(tmp_path):
    repo = begun(tmp_path, plan_text=PLAN.replace("Size: 20\n", ""))
    good_step(repo)
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    assert json.loads(attended_result(repo).read_text())["size_estimate"] is None
# ---- delivery projection Step 1: the run key is the Claude Code session id (D1, D11) -------
# Live values read 2026-10-20: an attended session's CLAUDE_CODE_SESSION_ID, and the session_id
# of the devbox night runner's canary line in /var/lib/acme-night/ledger.jsonl.

ATTENDED_SESSION_ID = "be11367e-be3a-479a-b25f-37faf19c6a0e"
NIGHT_SESSION_ID = "d8bb5538-5352-4dc2-ab8c-4673ba8ff36a"


@pytest.mark.parametrize("key", [ATTENDED_SESSION_ID, NIGHT_SESSION_ID])
def test_run_key_is_the_session_id(key):
    assert next_auto.run_key({"CLAUDE_CODE_SESSION_ID": key}) == (key, None)


def test_run_key_with_the_variable_unset():
    assert next_auto.run_key({}) == (None, "no run key: CLAUDE_CODE_SESSION_ID is unset")


@pytest.mark.parametrize(
    "value", ["20261020-4a828a", ATTENDED_SESSION_ID.upper(), "", ATTENDED_SESSION_ID + "\n"]
)
def test_run_key_refuses_a_value_that_is_not_a_session_id(value):
    assert next_auto.run_key({"CLAUDE_CODE_SESSION_ID": value}) == (
        None,
        f"no run key: CLAUDE_CODE_SESSION_ID is not a session id: {value}",
    )


def test_run_key_form_is_the_lowercase_uuid():
    assert next_auto.RUN_KEY_FORM.pattern == (
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )


# ---- delivery projection Step 2: step results carry the plan hash and the run key (D9, D11) --


def plan_hash_cli(plan):
    """The plan's hash as `python3 scripts/next_plan.py plan-hash <plan>` prints it."""
    script = PLUGIN / "scripts" / "next_plan.py"
    r = subprocess.run(
        [sys.executable, str(script), "plan-hash", str(plan)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return r.stdout.strip()


def test_attended_commit_writes_the_plan_hash_and_the_run_key_into_the_result(tmp_path, capsys):
    repo, control = record_repo(tmp_path)
    attended_pass(repo)
    set_done(repo, 1, "2 tests green, mutation killed both ways, CHECK PASS")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    capsys.readouterr()
    result = json.loads(attended_result(repo).read_text())
    assert (result["sha"], result["mutation"]) == (head_sha(repo), "reported")
    assert result["plan"] == plan_hash_cli(repo / "prompt_plan.md")
    assert result["runkey"] == ATTENDED_SESSION_ID
    [call] = appends(control)
    assert (call["evidence"]["plan"], call["evidence"]["runkey"]) == (
        result["plan"],
        ATTENDED_SESSION_ID,
    )


def test_run_commit_writes_the_plan_hash_and_the_run_key_into_the_result(
    tmp_path, capsys, monkeypatch
):
    """An unattended run reads the same two values from the same sources: the night runner's
    headless session id, and the hash of the worktree's plan."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", NIGHT_SESSION_ID)
    repo, wt, control = record_run(tmp_path)
    state = next_auto.step_begin(repo, 1, test_cmd=TEST_CMD)
    good_step(wt)
    assert next_auto.gate(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK) == (True, "gate passed")
    next_auto.set_step(repo, 1, "done", "2 tests green, mutation killed both ways")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    capsys.readouterr()
    result = json.loads((Path(state["run_dir"]) / "result-1.json").read_text())
    assert result["sha"] == head_sha(wt)
    assert result["plan"] == plan_hash_cli(wt / "prompt_plan.md")
    assert result["runkey"] == NIGHT_SESSION_ID
    [call] = appends(control)
    assert call["evidence"]["runkey"] == NIGHT_SESSION_ID


def test_commit_appends_no_step_done_without_a_run_key(tmp_path, capsys, monkeypatch):
    """With CLAUDE_CODE_SESSION_ID unset the appender never runs: the stub logs every call it
    gets, and it gets no append; the commit stands and the failure is one line, exit 2."""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    repo, control = record_repo(tmp_path)
    attended_pass(repo)
    set_done(repo, 1, "2 tests green, mutation killed both ways, CHECK PASS")
    before = head_sha(repo)
    rc = next_auto.main(["--root", str(repo), "commit", "1"])
    err = capsys.readouterr().err
    sha = head_sha(repo)
    assert rc == 2 and sha != before
    assert err.strip() == (
        f"COMMIT {sha} RECORD FAILED step 1: "
        "step.done needs a run key: CLAUDE_CODE_SESSION_ID is unset"
    )
    assert appends(control) == []
    assert (repo / RECORD_FILE).read_text() == RECORD_HEADER


def test_an_appender_that_ignores_the_two_keys_still_appends(tmp_path, capsys):
    """The stub reads only the evidence keys it knows, as buildStepDone in
    tools/workflows/record.mjs at 763e5a5e does, so this lands ahead of the catalogue version
    that takes plan and runkey (plan Step 4)."""
    repo, control = record_repo(tmp_path)
    attended_pass(repo)
    set_done(repo, 1, "2 tests green, mutation killed both ways, CHECK PASS")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    capsys.readouterr()
    sha = head_sha(repo)
    [call] = appends(control)
    assert {"plan", "runkey"} <= call["evidence"].keys()
    assert (repo / RECORD_FILE).read_text() == RECORD_HEADER + f"- stub step.done {sha}\n"


# ---- delivery projection Step 3: the pull-request-opened line carries the run key (D3, D9) ---


def test_pr_opened_appends_nothing_without_a_run_key(tmp_path, capsys, monkeypatch):
    """With CLAUDE_CODE_SESSION_ID unset, record_pr_opened refuses before the appender runs:
    the stub logs every call it gets and gets no append, the record and the branch stay as
    they were, and the pull request stands."""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    repo, control = record_repo(tmp_path)
    opened_on = pr_branch(repo)
    assert next_auto.record_pr_opened(
        repo, repo, (["tools/record-stub"], RECORD_SLUG), 9, opened_on, "feat/record",
        "attended",
    ) == (None, "pr.opened needs a run key: CLAUDE_CODE_SESSION_ID is unset")  # fmt: skip
    assert appends(control) == []
    assert (repo / RECORD_FILE).read_text() == RECORD_HEADER
    assert head_sha(repo) == opened_on
    monkeypatch.setattr(next_auto, "gh", gh_pr_head([], opened_on))
    assert next_auto.main(["--root", str(repo), "pr-opened", "9"]) == 2
    assert capsys.readouterr().err.strip() == (
        "PR-OPENED FAILED PR #9: pr.opened needs a run key: CLAUDE_CODE_SESSION_ID is unset"
    )
    assert appends(control) == []


# ---- adw 3.5.8: check N's log (ai-dlc-record R14 amendment (3), F-591) ---------------------


def check_log(repo, n=1):
    return repo / ".next-auto" / "attended" / f"check-{n}.log"


def red_step(repo):
    """The step's test with an implementation that does not meet it."""
    (repo / "tests" / "test_feature.py").write_text(
        "from src import FEATURE\n\n\ndef test_feature():\n    assert FEATURE == 42\n"
    )
    (repo / "src.py").write_text(
        "# Description: fixture\n# Description: two\nX = 1\nFEATURE = 41\n"
    )


def test_f591_check_n_writes_the_test_output_on_a_failure_and_again_on_the_pass(tmp_path):
    repo = begun(tmp_path)
    red_step(repo)
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1 and lines[-1].startswith("CHECK FAIL step 1: tests failed: ")
    failed = check_log(repo).read_text()
    assert "test_feature" in failed and re.search(r"\b1 failed\b", failed)
    good_step(repo)
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    passed = check_log(repo).read_text()
    assert re.search(r"\b2 passed\b", passed) and "failed" not in passed


def test_f591_check_n_removes_an_earlier_log_when_its_test_does_not_run(tmp_path):
    repo = begun(tmp_path)
    red_step(repo)
    assert next_auto.check_step(repo, 1)[0] == 1
    git(repo, "checkout", "--", "src.py")
    (repo / "tests" / "test_feature.py").unlink()
    rc, lines = next_auto.check_step(repo, 1)
    assert rc == 1
    assert lines[0] == "CHECK FAIL step 1: nothing staged: the step produced no changes"
    assert not check_log(repo).exists()


def test_f591_commit_n_removes_the_log_with_the_record(tmp_path):
    repo = begun(tmp_path)
    good_step(repo)
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    assert check_log(repo).is_file()
    rc, line = next_auto.commit_attended(repo, 1)
    assert rc == 0, line
    assert not check_log(repo).exists() and not attended_record(repo).exists()


@pytest.mark.parametrize("n", [1, 2], ids=["same-step", "next-step"])
def test_f591_the_next_check_begin_removes_the_log(tmp_path, n):
    repo = begun(tmp_path)
    red_step(repo)
    assert next_auto.check_step(repo, 1)[0] == 1
    assert check_log(repo).is_file()
    git(repo, "checkout", "--", "src.py")
    (repo / "tests" / "test_feature.py").unlink()
    rc, line = next_auto.check_begin(repo, n, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0, line
    assert not check_log(repo).exists()


def test_f591_an_exempt_check_logs_the_external_command_and_exit_first(tmp_path):
    repo, stub = _exempt_begun(tmp_path, "echo first\necho '19 of 19 PASS'\n")
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])
    first, rest = check_log(repo).read_text().split("\n", 1)
    assert first == f"external Test: {shlex.join([str(stub)])} exit 0"
    assert re.search(r"\b\d+ passed\b", rest)


def test_f591_an_exempt_check_logs_a_red_external_test(tmp_path):
    repo, stub = _exempt_begun(tmp_path, "echo running\necho '1 FAIL'\nexit 1\n")
    assert next_auto.check_step(repo, 1)[0] == 1
    first = check_log(repo).read_text().split("\n", 1)[0]
    assert first == f"external Test: {shlex.join([str(stub)])} exit 1"


# ---- adw 3.5.8: the Shares line at check begin and start (R14 amendment (4)) --------------

RUN_LINE = "- Run: uv run python -m fixturepkg"


def shares_text(classes, text=PLAN):
    """The fixture plan with a Shares line naming `classes`, or none when it is None."""
    return text if classes is None else text.replace(RUN_LINE, f"{RUN_LINE}\n- Shares: {classes}")


def set_plan_states(tree, *states):
    plan = tree / "prompt_plan.md"
    for n, state in enumerate(states, start=1):
        note = "a" if state == "done" else ""
        plan.write_bytes(next_plan.set_status_bytes(plan.read_bytes(), n, state, note))


def other_live(tmp_path, repo, theirs, live):
    """A second worktree of the repository at tmp_path/other whose plan shares `theirs`, live
    by `live`: a step IN PROGRESS, a check record left by check begin, a run marker, or a
    finished plan whose check record is left behind."""
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-q", "-b", "other", str(other))
    (other / "prompt_plan.md").write_text(shares_text(theirs))
    attended = other / ".next-auto" / "attended"
    if live == "in-progress":
        set_plan_states(other, "in-progress")
    elif live == "check-record":
        attended.mkdir(parents=True)
        (attended / "step-1.json").write_text("{}")
    elif live == "run-marker":
        marker = other / ".next-auto" / "20261031-run001" / "state.json"
        marker.parent.mkdir(parents=True)
        marker.write_text("{}")
    elif live == "finished":
        set_plan_states(other, "done", "done")
        attended.mkdir(parents=True)
        (attended / "step-2.json").write_text("{}")
    return other


def shares_repo(tmp_path, mine, theirs, live="in-progress"):
    repo, _ = make_repo(tmp_path)
    if mine is not None:
        (repo / "prompt_plan.md").write_text(shares_text(mine))
        git(repo, "commit", "-qam", "this plan's Shares line")
        git(repo, "push", "-q", "origin", "main")
    return repo, other_live(tmp_path, repo, theirs, live)


@pytest.mark.parametrize("live", ["in-progress", "check-record", "run-marker"])
def test_r14_check_begin_refuses_beside_a_live_plan_sharing_a_class(tmp_path, live):
    """rul-3c4db5a3fb: the Shares class check refuses a second builder on one resource."""
    repo, other = shares_repo(tmp_path, "dapi, docs", "migration, dapi", live)
    rc, line = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert (rc, line) == (
        1, f"CHECK FAIL step 1: the work tree {other.resolve()} has a live plan sharing dapi"
    )  # fmt: skip
    assert not attended_record(repo).exists()


def test_r14_check_begin_names_every_class_the_two_plans_share(tmp_path):
    repo, other = shares_repo(tmp_path, "infra, openapi", "openapi, infra")
    rc, line = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert (rc, line) == (
        1,
        f"CHECK FAIL step 1: the work tree {other.resolve()} has a live plan sharing "
        "infra, openapi",
    )


@pytest.mark.parametrize(
    ("mine", "theirs", "live"),
    [
        ("docs", "docs", "in-progress"),
        ("dapi, docs", "docs", "in-progress"),
        (None, "dapi", "in-progress"),
        ("dapi", None, "in-progress"),
        ("dapi", "openapi", "in-progress"),
        ("dapi", "dapi", "finished"),
        ("dapi", "dapi", "none"),
    ],
    ids=[
        "docs-only", "docs-never-counts", "no-line-here", "no-line-there", "other-class",
        "finished-plan", "not-live",
    ],
)
def test_r14_check_begin_passes_where_nothing_is_shared(tmp_path, mine, theirs, live):
    """rul-3c4db5a3fb: documents-only lanes never count, and a plan with no line shares nothing."""
    repo, _other = shares_repo(tmp_path, mine, theirs, live)
    rc, line = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0, line


def test_r14_a_listed_tree_whose_directory_is_gone_counts_for_nothing(tmp_path):
    repo, other = shares_repo(tmp_path, "dapi", "dapi")
    shutil.rmtree(other)
    assert str(other.resolve()) in git(repo, "worktree", "list", "--porcelain").stdout
    rc, line = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0, line


def test_r14_the_live_tree_refuses_a_begin_from_the_checkout_and_not_itself(tmp_path):
    """The tree check begin runs in is never read as another: a begin in the live tree itself
    passes, and only the checkout beside it is refused."""
    repo, other = shares_repo(tmp_path, "dapi", "dapi", live="check-record")
    (other / ".next-auto" / "attended" / "step-1.json").unlink()
    set_plan_states(other, "in-progress")
    git(other, "commit", "-qam", "the other plan, begun")
    rc, line = next_auto.check_begin(other, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0, line
    rc, line = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 1 and str(other.resolve()) in line


def test_r14_start_refuses_beside_a_live_plan_sharing_a_class_before_gh(
    tmp_path, capsys, monkeypatch
):
    repo, other = shares_repo(tmp_path, "migration", "migration")
    monkeypatch.setattr(next_auto, "gh_repo_view", lambda _root: pytest.fail("gh called"))
    rc = next_auto.main(["--root", str(repo), "start", "prompt_plan.md", "--no-sync"])
    assert rc == 1
    assert capsys.readouterr().err.strip() == (
        f"START FAILED: the work tree {other.resolve()} has a live plan sharing migration"
    )
    assert next_auto.find_state_file(repo) is None
    assert "next-auto/" not in git(repo, "branch", "--list").stdout


def test_r14_start_proceeds_beside_a_live_plan_on_another_class(tmp_path, capsys, monkeypatch):
    repo, _other = shares_repo(tmp_path, "migration", "openapi")
    view = {"isFork": False, "defaultBranchRef": {"name": "main"}}
    monkeypatch.setattr(next_auto, "gh_repo_view", lambda _root: view)
    rc = next_auto.main(["--root", str(repo), "start", "prompt_plan.md", "--no-sync"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert out.out.startswith("RUN STARTED ")


# ---- adw 3.5.8: preflight refuses only the same unit's open pull request (amendment (5)) ---

ISSUE_LINE = "- Lint: uv run ruff check ."


def issue_repo(tmp_path, issue):
    """make_repo whose plan carries `Issue: <issue>`, or none when it is None, committed and
    pushed so preflight finds a clean tree level with origin."""
    repo, _ = make_repo(tmp_path)
    if issue is not None:
        plan = repo / "prompt_plan.md"
        plan.write_text(plan.read_text().replace(ISSUE_LINE, f"{ISSUE_LINE}\n- Issue: {issue}"))
        git(repo, "commit", "-qam", "the plan's Issue")
        git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "origin")
    return repo


def open_prs(*branches):
    """`gh pr list --json number,headRefName` as gh prints it (acme/marketplace, read
    2026-10-31), one pull request per head branch."""
    rows = [{"headRefName": b, "number": 30 + i} for i, b in enumerate(branches)]
    return f"echo '{json.dumps(rows)}'"


def preflight(tmp_path, monkeypatch, repo, pr_list):
    gh_shim(tmp_path, monkeypatch, pr_list=pr_list)
    monkeypatch.setattr(next_auto, "origin_owner", lambda _root: "ryanmat")
    monkeypatch.setattr(next_auto, "origin_repo", lambda _root: "ryanmat/thing")
    return next_auto.main(["--root", str(repo), "preflight", "prompt_plan.md"])


@pytest.mark.parametrize(
    "branch",
    [
        "next-auto/doc-1170-the-night-timer-20261030-a1b2c3",
        "next-auto/doc-11710-another-unit-20261031-d4e5f6",
        "next-auto/the-untracked-plan-20261031-0a0b0c",
    ],
    ids=["another-issue", "an-issue-it-prefixes", "no-issue"],
)
def test_r14_preflight_passes_beside_another_units_open_pull_request(
    tmp_path, monkeypatch, capsys, branch
):
    """rul-a609ea8744: another unit's open next-auto PR refuses nothing."""
    repo = issue_repo(tmp_path, "DOC-1171")
    assert preflight(tmp_path, monkeypatch, repo, open_prs(branch)) == 0
    assert "PREFLIGHT OK" in capsys.readouterr().out


def test_r14_preflight_refuses_beside_its_own_units_open_pull_request(
    tmp_path, monkeypatch, capsys
):
    """rul-a609ea8744: preflight refuses only while a next-auto PR of the same unit is open."""
    repo = issue_repo(tmp_path, "DOC-1171")
    pr_list = open_prs(
        "next-auto/doc-1170-the-night-timer-20261030-a1b2c3",
        "next-auto/doc-1171-the-stop-gate-20261031-b2c3d4",
    )
    assert preflight(tmp_path, monkeypatch, repo, pr_list) == 1
    assert capsys.readouterr().err.strip().splitlines() == [
        "PREFLIGHT REFUSED: an open PR labeled next-auto already exists for DOC-1171 (#31)"
    ]


def test_r14_preflight_with_no_issue_refuses_beside_any_open_pull_request(
    tmp_path, monkeypatch, capsys
):
    repo = issue_repo(tmp_path, None)
    pr_list = open_prs("next-auto/doc-1170-the-night-timer-20261030-a1b2c3")
    assert preflight(tmp_path, monkeypatch, repo, pr_list) == 1
    assert capsys.readouterr().err.strip().splitlines() == [
        "PREFLIGHT REFUSED: an open PR labeled next-auto already exists (#30)"
    ]


def test_r14_preflight_with_an_issue_still_refuses_a_listing_gh_cannot_give(
    tmp_path, monkeypatch, capsys
):
    repo = issue_repo(tmp_path, "DOC-1171")
    assert preflight(tmp_path, monkeypatch, repo, 'echo "HTTP 502: bad gateway" >&2; exit 1') == 1
    assert capsys.readouterr().err.strip().splitlines() == [
        "PREFLIGHT REFUSED: could not determine open next-auto PRs (HTTP 502: bad gateway)"
    ]


def test_r14_the_listing_reads_each_pull_requests_head_branch(tmp_path, monkeypatch):
    repo = issue_repo(tmp_path, None)
    gh_shim(tmp_path, monkeypatch, pr_list=open_prs("next-auto/doc-9-x-20261031-aaaaaa"))
    assert next_auto.list_next_auto_prs(repo) == ([(30, "next-auto/doc-9-x-20261031-aaaaaa")], "")
    gh_shim(tmp_path, monkeypatch, pr_list="echo '[{\"number\": 3}]'")
    assert next_auto.list_next_auto_prs(repo) == (None, "gh pr list returned no JSON")


def test_r14_a_refused_begin_set_back_to_pending_frees_the_other_tree(tmp_path):
    """rul-3c4db5a3fb, fix round: item 3 marks the step started before item 4's begin, and an
    IN PROGRESS step counts as live, so a pane refused at its begin refuses the other pane's next
    begin until item 4's instruction sets its step back to pending."""
    repo, other = shares_repo(tmp_path, "dapi", "dapi", live="none")
    set_plan_states(repo, "in-progress")
    rc, line = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0, line
    set_plan_states(other, "in-progress")
    rc, line = next_auto.check_begin(other, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 1 and str(repo.resolve()) in line
    attended_record(repo).unlink()
    set_plan_states(repo, "done", "in-progress")
    rc, line = next_auto.check_begin(repo, 2, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 1 and str(other.resolve()) in line
    set_plan_states(other, "pending")
    rc, line = next_auto.check_begin(repo, 2, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0, line


def test_r14_a_plan_the_shares_check_cannot_read_is_one_line(tmp_path, capsys, monkeypatch):
    """Fix round: another tree's plan that is not UTF-8 is apparatus at check begin and one
    START FAILED line at start, never a traceback."""
    repo, other = shares_repo(tmp_path, "dapi", "dapi")
    (other / "prompt_plan.md").write_bytes(b"# plan\n\xff\xfe\n")
    rc, line = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 2 and line.startswith("CHECK APPARATUS step 1: ")
    assert "utf-8" in line
    monkeypatch.setattr(next_auto, "gh_repo_view", lambda _root: pytest.fail("gh called"))
    rc = next_auto.main(["--root", str(repo), "start", "prompt_plan.md", "--no-sync"])
    [err] = capsys.readouterr().err.strip().splitlines()
    assert rc == 1 and err.startswith("START FAILED: ") and "utf-8" in err
    assert next_auto.find_state_file(repo) is None


def test_r14_a_refused_fix_round_begin_leaves_the_step_done(tmp_path):
    """rul-3c4db5a3fb, round 3: the boundary fix round begins a DONE step with no item 3; a
    refusal writes no record, so the step stays DONE and blocks nobody, and the begin after the
    other tree clears is the fix round's, with fix_round set."""
    repo, other = shares_repo(tmp_path, "dapi", "dapi")
    set_plan_states(repo, "done")
    rc, line = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 1 and str(other.resolve()) in line
    assert not attended_record(repo).exists()
    assert next_plan.parse_plan((repo / "prompt_plan.md").read_text()).steps[0].state == "DONE"
    set_plan_states(other, "pending")
    rc, line = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0, line
    assert json.loads(attended_record(repo).read_text())["fix_round"] is True


def test_r14_start_refuses_on_a_shares_read_error_with_no_message(tmp_path, capsys, monkeypatch):
    """Round 3: an exception whose text is empty still ends start with one START FAILED line."""
    repo, _other = shares_repo(tmp_path, "dapi", "openapi")

    def no_text(_root, _plan):
        raise OSError()

    monkeypatch.setattr(next_auto, "shares_conflict", no_text)
    monkeypatch.setattr(next_auto, "gh_repo_view", lambda _root: pytest.fail("gh called"))
    rc = next_auto.main(["--root", str(repo), "start", "prompt_plan.md", "--no-sync"])
    [err] = capsys.readouterr().err.strip().splitlines()
    assert rc == 1 and err.startswith("START FAILED: ")
    assert next_auto.find_state_file(repo) is None
