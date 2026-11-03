# Description: Tests for next-guard.py, the PreToolUse guard hook for /next auto runs.
# Description: Real temp repos and state-file markers, no mocks; covers always-deny, marker,
# Description: subagent, and exception paths.

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "next-guard.py"

_spec = importlib.util.spec_from_file_location("next_guard", HOOK)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def test_find_work_tree_reports_a_failed_git_call_then_falls_back(tmp_path, monkeypatch, capsys):
    """The git rev-parse path used to swallow every exception behind a bare pass and walk up
    for a .git entry. The fallback stays; the failure is now reported on stderr so a hook that
    is quietly running without git is visible in the transcript."""
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    inner = root / "pkg" / "sub"
    inner.mkdir(parents=True)
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))  # no git on PATH: FileNotFoundError
    assert guard.find_work_tree(str(inner)) == root
    err = capsys.readouterr().err
    assert "next-guard: git rev-parse failed" in err
    assert str(inner) in err


def test_find_work_tree_is_silent_when_git_answers(tmp_path, capsys):
    repo = mk_repo(tmp_path)
    assert guard.find_work_tree(str(repo)) == repo
    assert capsys.readouterr().err == ""


def test_active_state_reports_a_corrupt_marker_and_stays_armed(tmp_path, capsys):
    """A state.json that does not parse used to become an empty dict with no trace. The hook
    stays armed on any marker (fail closed), and now says why the marker read as empty."""
    root = tmp_path / "repo"
    marker = root / guard.RUN_SUBDIR / "r1" / "state.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{not json")
    state = guard.active_state(root)
    assert state is not None and state["_path"] == str(marker)
    err = capsys.readouterr().err
    assert "next-guard: could not parse" in err
    assert str(marker) in err


def test_fail_closed_check_reports_its_own_failure_before_allowing(tmp_path):
    """When main() raises, the hook looks for an active run so it can fail closed. If that
    lookup itself raises (here the cwd was removed under the hook, so os.getcwd() fails), it
    used to exit 0 with no trace at all. The allow stays; the failed lookup is now reported on
    stderr with its traceback, so a hook that quietly stopped guarding is visible."""
    gone = tmp_path / "gone"
    gone.mkdir()
    script = 'cd "$1" && rmdir "$1" && exec "$2" "$3"'
    r = subprocess.run(
        ["sh", "-c", script, "_", str(gone), sys.executable, str(HOOK)],
        input="{not json",
        capture_output=True,
        text=True,
    )
    assert not gone.exists()
    assert r.returncode == 0, r.stderr
    assert "JSONDecodeError" in r.stderr
    assert "next-guard: fail-closed check failed" in r.stderr
    assert "FileNotFoundError" in r.stderr


def mk_repo(tmp_path, with_state=False, default="main", holdout="evals/"):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    if with_state:
        run_dir = repo / ".next-auto" / "20260911-abc123"
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text(
            json.dumps(
                {
                    "run_id": "20260911-abc123",
                    "default": default,
                    "holdout": holdout,
                    "run_dir": str(run_dir),
                    "worktree": str(run_dir / "wt"),
                    "branch": "next-auto/slug-20260911-abc123",
                }
            )
        )
    return repo


def bash(cmd, cwd, agent=False):
    p = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": str(cwd),
        "tool_input": {"command": cmd},
    }
    if agent:
        p["agent_id"] = "sub-1"
    return p


def edit(path, cwd, tool="Edit", agent=False):
    p = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "cwd": str(cwd),
        "tool_input": {"file_path": str(path)},
    }
    if agent:
        p["agent_id"] = "sub-1"
    return p


def decide(payload):
    return guard.evaluate(payload)[0]


# ---- always-deny Bash (regardless of marker) --------------------------------

ALWAYS_DENY = [
    'git commit --no-verify -m "x"',
    'git commit -n -m "x"',
    'git commit -nm "x"',
    'git commit -amn "x"',
    "git merge --no-hooks",
    "git -c core.hooksPath=/dev/null commit -m x",
    'SKIP=ruff git commit -m "x"',
    "PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m x",
    "GIT_CONFIG_PARAMETERS=\"'core.hooksPath=/dev/null'\" git commit -m x",
    "pre-commit uninstall",
    "chmod -x .git/hooks/pre-commit",
]


@pytest.mark.parametrize("cmd", ALWAYS_DENY)
def test_always_deny_without_marker(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=False)
    assert decide(bash(cmd, repo)) == "deny", cmd


@pytest.mark.parametrize("cmd", ALWAYS_DENY)
def test_always_deny_with_marker(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash(cmd, repo)) == "deny", cmd


ALLOWED_NO_MARKER = [
    'git commit -m "normal message with no bad flags"',
    'git commit -am "message"',
    "ls -la",
    "git status",
    "git push origin main",
    "gh pr create --fill",
    "gh pr merge 1",
    "git stash",
    "git tag v1",
    "git reset --hard HEAD~1",
    "python3 /opt/plugins/adw/scripts/next_auto.py merge 7",
    "python3 /opt/plugins/adw/scripts/next_auto.py --root . merge 7",
]


@pytest.mark.parametrize("cmd", ALLOWED_NO_MARKER)
def test_allowed_without_marker(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=False)
    assert decide(bash(cmd, repo)) == "allow", cmd


# ---- always-deny Edit/Write -------------------------------------------------


def test_edit_under_git_denied(tmp_path):
    repo = mk_repo(tmp_path)
    assert decide(edit(repo / ".git" / "config", repo)) == "deny"


def test_write_git_hook_denied(tmp_path):
    repo = mk_repo(tmp_path)
    assert decide(edit(repo / ".git" / "hooks" / "pre-commit", repo, tool="Write")) == "deny"


def test_edit_precommit_config_denied(tmp_path):
    repo = mk_repo(tmp_path)
    assert decide(edit(repo / ".pre-commit-config.yaml", repo)) == "deny"


def test_edit_normal_file_allowed(tmp_path):
    repo = mk_repo(tmp_path)
    assert decide(edit(repo / "src" / "app.py", repo)) == "allow"


# ---- marker-present denies ---------------------------------------------------

MARKER_DENY = [
    "gh pr merge 1",
    "gh api -X PUT repos/o/r/pulls/1/merge",
    "gh release create v1",
    "git push --force origin main",
    "git push -f origin main",
    "git push --force-with-lease origin main",
    "git push --mirror",
    "git push origin +main",
    "git push origin local:remote",
    "git stash",
    "git stash push -m wip",
    "git tag v1.0.0",
    "git reset --hard HEAD~2",
    "git worktree remove wt",
    "git branch -D feature",
    "bash -c 'git push --force'",
    "sh -c 'gh pr merge 1'",
    "python -c \"import os; os.system('git push')\"",
    'eval "$(gh auth status)"',
    "claude -p 'do something'",
    "python3 /opt/plugins/adw/scripts/next_auto.py merge 7",
    "python3 /opt/plugins/adw/scripts/next_auto.py --root . merge 7",
    "python scripts/next_auto.py merge 7 --repo ryanmat/x",
    "./scripts/next_auto.py merge 7",
]


@pytest.mark.parametrize("cmd", MARKER_DENY)
def test_marker_present_denies(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash(cmd, repo)) == "deny", cmd


def test_next_auto_merge_denied_by_name_during_a_run(tmp_path):
    """Requirement 19: `/adw:next auto` never merges; the guard names the subcommand so a
    live run cannot reach the merge gate through the script either. Its sibling
    subcommands stay allowed while armed."""
    repo = mk_repo(tmp_path, with_state=True)
    for cmd in (
        "python3 /opt/plugins/adw/scripts/next_auto.py merge 7",
        "python3 /opt/plugins/adw/scripts/next_auto.py --root . merge 7",
    ):
        decision, reason = guard.evaluate(bash(cmd, repo))
        assert decision == "deny", cmd
        assert "next_auto.py merge during a run" in reason
    for cmd in (
        "python3 /opt/plugins/adw/scripts/next_auto.py commit 7",
        "python3 /opt/plugins/adw/scripts/next_auto.py --root . check 7",
        "python3 /opt/plugins/adw/scripts/next_plan.py set prompt_plan.md 7 done",
    ):
        assert decide(bash(cmd, repo)) == "allow", cmd


@pytest.mark.parametrize(
    "cmd", ["gh pr merge 1", "git stash", "git tag v1", "git reset --hard HEAD~1"]
)
def test_marker_absent_allows_those(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=False)
    assert decide(bash(cmd, repo)) == "allow", cmd


def test_checkout_default_branch_denied_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True, default="main")
    assert decide(bash("git checkout main", repo)) == "deny"
    assert decide(bash("git switch main", repo)) == "deny"


def test_checkout_feature_branch_allowed_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True, default="main")
    assert decide(bash("git checkout -b feature", repo)) == "allow"


def test_rm_rf_run_dir_denied_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash("rm -rf .next-auto/20260911-abc123", repo)) == "deny"


def test_wrapper_without_sensitive_word_allowed_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash("bash -c 'ls -la && echo hi'", repo)) == "allow"


def test_normal_push_allowed_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash("git push origin next-auto/slug-20260911-abc123", repo)) == "allow"


def test_edit_holdout_denied_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True, holdout="evals/")
    assert decide(edit(repo / "evals" / "scenarios" / "s1.json", repo)) == "deny"


def test_edit_holdout_allowed_without_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=False, holdout="evals/")
    assert decide(edit(repo / "evals" / "scenarios" / "s1.json", repo)) == "allow"


# ---- subagent (agent_id) restrictions, marker present -----------------------


def test_subagent_git_commit_denied(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash("git commit -m x", repo, agent=True)) == "deny"


def test_subagent_git_readonly_allowed(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    for c in ("git status", "git diff", "git log --oneline", "git show HEAD"):
        assert decide(bash(c, repo, agent=True)) == "allow", c


def test_subagent_all_gh_denied(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash("gh pr list", repo, agent=True)) == "deny"


def test_orchestrator_git_commit_allowed_with_marker(tmp_path):
    # no agent_id: the orchestrator may commit
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash("git commit -m x", repo, agent=False)) == "allow"


# ---- envelope + exit-code contract (subprocess) -----------------------------


def run_hook(payload, cwd):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def test_deny_emits_json_and_exit2(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    r = run_hook(bash("git commit --no-verify -m x", repo), cwd=repo)
    assert r.returncode == 2, r.stderr
    out = json.loads(r.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "no-verify" in hso["permissionDecisionReason"]


def test_deny_reason_names_state_path_when_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    r = run_hook(bash("gh pr merge 1", repo), cwd=repo)
    assert r.returncode == 2
    reason = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "state.json" in reason


def test_allow_is_silent_exit0(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    r = run_hook(bash("git status", repo), cwd=repo)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_exception_exit2_when_state_present(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    r = subprocess.run(
        [sys.executable, str(HOOK)], input="not json", capture_output=True, text=True, cwd=str(repo)
    )
    assert r.returncode == 2
    assert "Traceback" in r.stderr


def test_exception_exit0_when_no_state(tmp_path):
    repo = mk_repo(tmp_path, with_state=False)
    r = subprocess.run(
        [sys.executable, str(HOOK)], input="not json", capture_output=True, text=True, cwd=str(repo)
    )
    assert r.returncode == 0
    assert "Traceback" in r.stderr


# ---- compound commands: `cd X && git ...` must not bypass any rule ----------
# Claude Code emits compound commands constantly, so a rule that only inspects
# the first program is not a guard at all.

PREFIX = "cd /tmp/somewhere && "


@pytest.mark.parametrize("cmd", ALWAYS_DENY)
def test_always_deny_survives_cd_prefix(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=False)
    assert decide(bash(PREFIX + cmd, repo)) == "deny", cmd


@pytest.mark.parametrize("cmd", MARKER_DENY)
def test_marker_deny_survives_cd_prefix(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash(PREFIX + cmd, repo)) == "deny", cmd


def test_second_git_in_a_chain_is_inspected(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash("git status && git stash", repo)) == "deny"
    assert decide(bash("git log --oneline; git reset --hard HEAD~1", repo)) == "deny"


def test_checkout_default_denied_after_prefix(tmp_path):
    repo = mk_repo(tmp_path, with_state=True, default="main")
    assert decide(bash("cd /tmp/x && git checkout main", repo)) == "deny"


def test_subagent_restrictions_survive_cd_prefix(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash("cd /tmp/x && git commit -m x", repo, agent=True)) == "deny"
    assert decide(bash("cd /tmp/x && gh pr list", repo, agent=True)) == "deny"


def test_benign_compounds_still_allowed(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    for c in (
        "cd /tmp/x && git status",
        "cd /tmp/x && git push origin next-auto/slug",
        "cd /tmp/x && ls -la",
        "git log --oneline -5 && git diff --stat",
    ):
        assert decide(bash(c, repo)) == "allow", c


def test_log_of_default_branch_is_not_a_checkout(tmp_path):
    # `default in tokens` anywhere would false-positive here
    repo = mk_repo(tmp_path, with_state=True, default="main")
    assert decide(bash("git log main --oneline", repo)) == "allow"


def test_mentioning_bypass_strings_is_not_using_them(tmp_path):
    """A command that merely contains the bypass words (a patch, a doc, a grep)
    is not an attempt to bypass. Denying those made the guard unusable in practice."""
    repo = mk_repo(tmp_path, with_state=True)
    for c in (
        "python3 apply_patch.py  # mentions core.hooksPath in a message string",
        "rg --files-with-matches PRE_COMMIT_ALLOW_NO_CONFIG docs/",
        "echo 'never use --no-verify' >> CONTRIBUTING.md",
    ):
        assert decide(bash(c, repo)) == "allow", c


def test_real_bypass_uses_are_still_denied(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    for c in (
        "git -c core.hooksPath=/dev/null commit -m x",
        "cd /tmp/x && git -c core.hooksPath=/dev/null commit -m x",
        "GIT_CONFIG_PARAMETERS='core.hooksPath=x' git commit -m y",
        "SKIP=ruff git commit -m x",
        "cd /tmp/x && SKIP=ruff git commit -m x",
    ):
        assert decide(bash(c, repo)) == "deny", c


# ---- the guarded repo may be reached via `cd` or `git -C`, not just cwd -----


def test_marker_found_via_cd_target(tmp_path):
    """Session rooted elsewhere, command cds into the guarded repo."""
    repo = mk_repo(tmp_path, with_state=True)
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    p = bash(f"cd {repo} && gh pr merge 1", outside)
    assert guard.evaluate(p)[0] == "deny"


def test_marker_found_via_git_dash_c(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    p = bash(f"git -C {repo} stash", outside)
    assert guard.evaluate(p)[0] == "deny"


def test_unguarded_repo_still_allowed_from_outside(tmp_path):
    """cd-ing into a repo with no run marker stays allowed."""
    repo = mk_repo(tmp_path, with_state=False)
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    p = bash(f"cd {repo} && gh pr merge 1", outside)
    assert guard.evaluate(p)[0] == "allow"


# ---- wrapper detection must match programs, not substrings ------------------


def test_paths_containing_eval_or_claude_are_not_wrappers(tmp_path):
    """`evals/` contains "eval" and `.claude/` contains "claude". Substring matching
    denied plain `ls` and `cat` on those paths, which made the guard unusable in the
    very repo that hosts the eval harness."""
    repo = mk_repo(tmp_path, with_state=True)
    for c in (
        "cd evals/next-auto/.work/repo/.claude/next-auto/r1 && ls -la && cat state.json",
        "cat evals/next-auto/results/summary.json",
        "ls .claude/next-auto",
        "ls .next-auto",
        "cat .next-auto/20260911-abc123/state.json",
        "rg pattern evals/",
    ):
        assert decide(bash(c, repo)) == "allow", c


def test_real_wrappers_touching_git_are_still_denied(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    for c in (
        "bash -c 'git push --force'",
        "sh -c 'gh pr merge 1'",
        "zsh -c 'git stash'",
        'python3 -c "import os; os.system(\'git push\')"',
        "eval \"$(gh auth status)\"",
        "cd /tmp && eval \"git stash\"",
    ):
        assert decide(bash(c, repo)) == "deny", c


# ---- the run marker must not be tampered with through tool calls --------------
# The run dir lives outside .claude/, so Claude Code's own sensitive-file protection
# no longer covers state.json. The hook carries that protection deliberately.


def test_edit_state_json_denied_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    run_dir = repo / ".next-auto" / "20260911-abc123"
    for tool in ("Edit", "Write", "MultiEdit"):
        assert decide(edit(run_dir / "state.json", repo, tool=tool)) == "deny", tool
        assert decide(edit(run_dir / "state.json.tmp", repo, tool=tool)) == "deny", tool
    assert decide(edit(".next-auto/20260911-abc123/state.json", repo)) == "deny"


def test_edit_other_run_dir_files_allowed_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    run_dir = repo / ".next-auto" / "20260911-abc123"
    assert decide(edit(run_dir / "report-1.md", repo, tool="Write", agent=True)) == "allow"
    assert decide(edit(run_dir / "wt" / "src" / "x.py", repo, tool="Write", agent=True)) == "allow"
    assert decide(edit(run_dir / "wt" / "tests" / "test_x.py", repo, tool="Edit")) == "allow"


def test_edit_state_json_allowed_without_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=False)
    assert decide(edit(repo / ".next-auto" / "old" / "state.json", repo, tool="Write")) == "allow"


def test_bash_tamper_with_state_json_denied_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    rd = ".next-auto/20260911-abc123"
    for c in (
        f"rm {rd}/state.json",
        f"rm -f {rd}/state.json",
        f"mv {rd}/state.json /tmp/x",
        f"cp /tmp/x {rd}/state.json",
        f"echo '{{}}' > {rd}/state.json",
        f"echo '{{}}' >> {rd}/state.json",
        f"echo '{{}}' | tee {rd}/state.json",
        f"truncate -s 0 {rd}/state.json",
        f"cd {rd} && rm state.json",
        f"cd {rd} && echo x > state.json",
        f"python3 -c \"import os; os.remove('{rd}/state.json')\"",
        f"bash -c 'rm {rd}/state.json'",
        f"cat > {rd}/state.json <<'EOF'\n{{}}\nEOF",
        f"rm -rf {rd}",
        f"rm -r {rd}",
    ):
        assert decide(bash(c, repo)) == "deny", c


def test_bash_reads_of_run_dir_allowed_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    rd = ".next-auto/20260911-abc123"
    for c in (
        f"cat {rd}/state.json",
        f"ls -la {rd}",
        f"cat {rd}/brief-1.md",
        f"cd {rd}/wt && uv run pytest",
        "python3 ~/.claude/scripts/next_auto.py state",
        f"python3 ~/.claude/scripts/next_auto.py gate 1 && cat {rd}/report-1.md",
    ):
        assert decide(bash(c, repo)) == "allow", c


# ---- newlines separate commands too ------------------------------------------
# `cd <wt>\ngit reset --hard <sha>` was allowed during a live eval run (double-fail-stop,
# 2026-09-11): the splitter knew &&, ||, ;, | and & but not the newline, so the git
# call sat inside the cd segment and was never inspected. Claude Code emits multi-line
# commands constantly.

NL_PREFIX = "cd /tmp\necho start\n"


@pytest.mark.parametrize("cmd", ALWAYS_DENY)
def test_always_deny_survives_newline_prefix(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=False)
    assert decide(bash(NL_PREFIX + cmd, repo)) == "deny", cmd


@pytest.mark.parametrize("cmd", MARKER_DENY)
def test_marker_deny_survives_newline_prefix(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash(NL_PREFIX + cmd, repo)) == "deny", cmd


def test_eval_reset_hard_shape_denied_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    cmd = (
        f"cd {repo}/.next-auto/20260911-abc123/wt\n"
        'git reset --hard a5c5b25; echo "RESET_EXIT: $?"\n'
        'echo "===LOG===" && git log --oneline -3\n'
        'echo "===STATUS===" && git status --short\n'
    )
    decision, reason = guard.evaluate(bash(cmd, repo))
    assert decision == "deny"
    assert "reset --hard" in reason


def test_backslash_continuation_does_not_hide_flags(tmp_path):
    repo = mk_repo(tmp_path, with_state=False)
    assert decide(bash("git commit \\\n  --no-verify -m x", repo)) == "deny"
    assert decide(bash("git commit -m x \\\n  --no-verify", repo)) == "deny"


def test_multiline_benign_commands_allowed_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    for c in (
        "cd .next-auto/20260911-abc123/wt\nuv run pytest\nuv run ruff check .",
        "ls\ncat .next-auto/20260911-abc123/state.json",
        "git status --short\ngit log --oneline -3",
        'printf "a\\nb\\n" > /tmp/note.txt\n'
        "python3 ~/.claude/scripts/next_auto.py set 1 done --note-file /tmp/note.txt",
    ):
        assert decide(bash(c, repo)) == "allow", c


def test_subagent_git_restriction_survives_newline(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash("cd wt\ngit rev-parse HEAD", repo, agent=True)) == "deny"
    assert decide(bash("cd wt\ngit status --short", repo, agent=True)) == "allow"


# ---- review gaps, 2026-09-11: holdout by the paths the harness really sends -----


def test_holdout_denied_for_absolute_worktree_paths(tmp_path):
    """The implementer writes absolute paths under the worktree. Matching the holdout
    only against a root-relative path protected the root copy and nothing else."""
    repo = mk_repo(tmp_path, with_state=True, holdout="evals/")
    wt = repo / ".next-auto" / "20260911-abc123" / "wt"
    for tool in ("Write", "Edit", "MultiEdit"):
        p = edit(wt / "evals" / "case1.json", wt, tool=tool, agent=True)
        assert decide(p) == "deny", tool
    assert decide(edit(repo / "evals" / "case1.json", repo, tool="Write")) == "deny"
    # a cwd-relative path from inside the worktree
    assert decide(edit("evals/case1.json", wt, tool="Write", agent=True)) == "deny"
    # neighbours stay writable
    assert decide(edit(wt / "tests" / "test_x.py", wt, tool="Write", agent=True)) == "allow"
    assert decide(edit(wt / "src" / "evals_helper.py", wt, tool="Write", agent=True)) == "allow"


def test_holdout_denied_for_bash_writes(tmp_path):
    repo = mk_repo(tmp_path, with_state=True, holdout="evals/")
    wt = repo / ".next-auto" / "20260911-abc123" / "wt"
    for c in (
        "echo x > evals/case1.json",
        "echo x >> evals/case1.json",
        f"echo x > {wt}/evals/case1.json",
        "cp /tmp/x evals/case1.json",
        "rm evals/case1.json",
        "mv evals/case1.json /tmp/",
        "cat > evals/case1.json <<'EOF'\n{}\nEOF",
    ):
        assert decide(bash(c, wt, agent=True)) == "deny", c
    for c in ("cat evals/case1.json", "ls evals/", "uv run pytest evals/"):
        assert decide(bash(c, wt, agent=True)) == "allow", c


def test_holdout_empty_means_no_holdout_rule(tmp_path):
    repo = mk_repo(tmp_path, with_state=True, holdout="")
    wt = repo / ".next-auto" / "20260911-abc123" / "wt"
    assert decide(edit(wt / "evals" / "x.json", wt, tool="Write")) == "allow"


# ---- review gaps: push deletes, variable-carried flags, find -delete -------------


def test_push_delete_forms_denied_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    for c in (
        "git push origin :x",
        "git push origin :refs/heads/x",
        "git push origin --delete x",
        "git push -d origin x",
        "git push --delete origin x",
    ):
        assert decide(bash(c, repo)) == "deny", c
    assert decide(bash("git push -u origin next-auto/x", repo)) == "allow"


def test_push_flag_carried_by_variable_denied_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    for c in (
        "F=--force; git push $F origin main",
        "F=--force\ngit push $F origin main",
        "git push ${F} origin main",
    ):
        assert decide(bash(c, repo)) == "deny", c


def test_find_delete_on_marker_denied_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash("find .next-auto -name state.json -delete", repo)) == "deny"
    assert decide(bash("find .next-auto -name state.json -exec rm {} +", repo)) == "deny"
    assert decide(bash("find .next-auto -name state.json", repo)) == "allow"
    assert decide(bash("find . -name '*.pyc' -delete", repo)) == "allow"


# ---- recursive rm: the run dir is protected by path, not by substring ------------------
# The worktree lives inside the run dir, so its absolute path contains `.next-auto`. A
# substring test denied `rm -rf <wt>/src/__pycache__`, ordinary cleanup the implementer
# does with the absolute paths the harness hands it (e2e suite, 2026-09-11). The rule
# resolves each operand against the command's effective directory and denies only the run
# dir itself, its ancestors, and anything in it that is not strictly inside the worktree.


def test_recursive_rm_inside_the_worktree_is_allowed(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    rd = repo / ".next-auto" / "20260911-abc123"
    wt = rd / "wt"
    for c in (
        f"rm -rf {wt}/src/__pycache__",
        f"rm -rf {wt}/.pytest_cache {wt}/.ruff_cache",
        "rm -rf .pytest_cache",
        f"cd {wt} && rm -rf build/",
        f"cd {wt}\nrm -r dist",
    ):
        assert decide(bash(c, wt)) == "allow", c


def test_recursive_rm_of_the_run_dir_or_worktree_is_denied(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    rd = repo / ".next-auto" / "20260911-abc123"
    wt = rd / "wt"
    for cwd, c in (
        (repo, f"rm -rf {rd}"),
        (repo, f"rm -rf {wt}"),
        (repo, f"rm -rf {rd}/wt/"),
        (repo, "rm -rf .next-auto"),
        (repo, "rm -rf .next-auto/20260911-abc123/wt"),
        (repo, f"cd {rd} && rm -rf wt"),
        (repo, f"cd {rd}\nrm -r wt"),
        (wt, "rm -rf .."),
        (wt, "rm -rf ../../"),
        (wt, f"rm -rf {rd}/brief-1.md"),
        (repo, f"rm -rf /tmp/x {rd}"),
    ):
        assert decide(bash(c, cwd)) == "deny", c


# ---- program identification: paths, launchers, opaque tokens (review, 2026-09-11) --------
# The hook compared the raw first token to `git`, `gh`, or `claude`, so `/usr/bin/git`,
# `command git`, and `env git` passed every rule including the always tier. The program is
# now the basename of the token left after skipping env assignments and launcher programs.

PATH_QUALIFIED_ALWAYS = [
    "/usr/bin/git commit --no-verify -m x",
    "./git commit -n -m x",
    "command git commit --no-verify -m x",
    "env git commit --no-verify -m x",
    "env -i GIT_DIR=.git git commit --no-verify -m x",
    "exec /opt/homebrew/bin/git commit --no-verify -m x",
    "nice -n 10 git commit --no-verify -m x",
    "sudo -u x git commit --no-verify -m x",
    "xargs git commit --no-verify -m x",
    "nohup git commit --no-verify -m x",
    "time git commit --no-verify -m x",
    "stdbuf -oL git commit --no-verify -m x",
    "doas -u x git commit --no-verify -m x",
    "builtin command git commit --no-verify -m x",
    "/usr/bin/env -u X git commit --no-verify -m x",
    "(git commit --no-verify -m x)",
    "{ git commit --no-verify -m x; }",
]


@pytest.mark.parametrize("cmd", PATH_QUALIFIED_ALWAYS)
def test_path_qualified_program_denied_always_tier(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=False)
    decision, reason = guard.evaluate(bash(cmd, repo))
    assert decision == "deny", cmd
    assert "hook-bypass" in reason, reason


@pytest.mark.parametrize("cmd", PATH_QUALIFIED_ALWAYS)
def test_path_qualified_program_denied_always_tier_with_marker(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash(cmd, repo)) == "deny", cmd


PATH_QUALIFIED_RUN = [
    "/usr/bin/gh pr merge 1",
    "/usr/bin/git push --force",
    "env git stash",
    "command git reset --hard",
    "~/.local/bin/claude -p x",
    "exec gh release create v1",
    "sudo -E git tag v1",
    "(git stash)",
    "{ git stash; }",
    "nice -n 5 git worktree remove wt",
]


@pytest.mark.parametrize("cmd", PATH_QUALIFIED_RUN)
def test_path_qualified_program_denied_run_tier(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash(cmd, repo)) == "deny", cmd


@pytest.mark.parametrize("cmd", PATH_QUALIFIED_RUN)
def test_path_qualified_run_tier_allowed_without_marker(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=False)
    assert decide(bash(cmd, repo)) == "allow", cmd


def test_subagent_path_qualified_git_and_gh_denied(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    for c in ("/usr/bin/git commit -m x", "env git add -A", "command gh pr list"):
        assert decide(bash(c, repo, agent=True)) == "deny", c
    assert decide(bash("/usr/bin/git status", repo, agent=True)) == "allow"


# An opaque program token cannot be identified, so during a run it is denied outright. With
# no marker the always tier names specific flags only, and these carry none it can see.
# `\git` is not opaque to the lexer: POSIX quoting resolves it to `git`, and the push rule
# takes it from there.
OPAQUE = [
    "$(which git) push --force",
    "`which git` stash",
    "$GIT push --force",
    "\\git push --force",
    "${GIT} stash",
    "$(command -v gh) pr merge 1",
]


@pytest.mark.parametrize("cmd", OPAQUE)
def test_opaque_program_token_denied_while_armed(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=True)
    decision, reason = guard.evaluate(bash(cmd, repo))
    assert decision == "deny", cmd
    assert "opaque program token" in reason or "push" in reason, reason


@pytest.mark.parametrize("cmd", OPAQUE)
def test_opaque_program_token_allowed_without_marker(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=False)
    assert decide(bash(cmd, repo)) == "allow", cmd


def test_opaque_program_token_reason_names_the_rule(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    decision, reason = guard.evaluate(bash("$(which git) push --force", repo))
    assert decision == "deny"
    assert "opaque program token during a run" in reason


# `env -S STRING` re-splits STRING into env's own argument list before it execs, so the
# words of the string are the command; the hook used to skip the string as the flag's
# argument and see no program at all (review, 2026-09-12). A bundled short cluster whose
# last letter takes an argument (`sudo -Hu root`) used to be skipped as one flag, leaving
# `root` as the program. Both spellings now resolve to the program they run.
ENV_SPLIT_ALWAYS = [
    "env -S 'git commit --no-verify -m x'",
    "/usr/bin/env -S 'git commit --no-verify -m x'",
    "env --split-string='git commit --no-verify -m x'",
    "env --split-string 'git commit --no-verify -m x'",
    "env -iS 'git commit --no-verify -m x'",
    "env -S'git commit --no-verify -m x'",
    "env -S 'git' commit --no-verify -m x",
    "env -S '-i git' commit --no-verify -m x",
    "env -S 'sh -c \"git commit --no-verify -m x\"'",
    "sudo -Hu root git commit --no-verify -m x",
    "sudo -uroot git commit --no-verify -m x",
    "nice -n10 git commit --no-verify -m x",
    "xargs -rn1 git commit --no-verify -m x",
]


@pytest.mark.parametrize("cmd", ENV_SPLIT_ALWAYS)
def test_env_split_and_bundled_flags_denied_always_tier(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=False)
    decision, reason = guard.evaluate(bash(cmd, repo))
    assert decision == "deny", cmd
    assert "hook-bypass" in reason, reason


ENV_SPLIT_RUN = [
    "env -S 'git push --force'",
    "/usr/bin/env -S 'gh pr merge 1'",
    "env -i -S 'git stash'",
    "env -iS 'git stash'",
    "env --split-string='git push --force'",
    "env -S 'sh -c \"git push --force\"'",
    "env -S 'nice -n 10 git push --force'",
    "sudo -Hu root git push --force",
    "sudo -Eu root gh release create v1",
]


@pytest.mark.parametrize("cmd", ENV_SPLIT_RUN)
def test_env_split_and_bundled_flags_denied_run_tier(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash(cmd, repo)) == "deny", cmd


@pytest.mark.parametrize("cmd", ENV_SPLIT_RUN)
def test_env_split_run_tier_allowed_without_marker(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=False)
    assert decide(bash(cmd, repo)) == "allow", cmd


def test_env_split_rm_of_run_dir_denied_while_armed(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    decision, reason = guard.evaluate(bash(f"env -S 'rm -rf {repo}/.next-auto'", repo))
    assert decision == "deny"
    assert "run" in reason, reason


def test_env_split_reason_names_the_rule_not_the_launcher(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    decision, reason = guard.evaluate(bash("env -S 'git push --force'", repo))
    assert decision == "deny"
    assert "dangerous git push" in reason, reason


@pytest.mark.parametrize(
    "cmd",
    ["env -S 'git status'", "env -S 'git log -1'", "sudo -Hu root git diff", "env -S 'ls -la'"],
)
def test_env_split_reads_still_allowed_while_armed(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash(cmd, repo)) == "allow", cmd


@pytest.mark.parametrize(
    "cmd, expected",
    [
        ("env -S 'git push --force'", ["env", "git", "push", "--force"]),
        ("env -S'git push --force'", ["env", "git", "push", "--force"]),
        ("env --split-string='git push --force'", ["env", "git", "push", "--force"]),
        ("env -iS 'git stash'", ["env", "git", "stash"]),
        ("env -i -S 'git stash'", ["env", "-i", "git", "stash"]),
        ("env -S 'sh -c \"git push\"'", ["env", "sh", "-c", "git push"]),
        ("cd x && env -S 'git push --force'", ["cd", "x", "&&", "env", "git", "push", "--force"]),
        ("X=1 /usr/bin/env -S 'git push'", ["X=1", "/usr/bin/env", "git", "push"]),
        # git's own -S is the signing flag and is never a split string
        ("git commit -S -m x", ["git", "commit", "-S", "-m", "x"]),
        ("git log -S needle", ["git", "log", "-S", "needle"]),
        # a payload that will not lex stays one token rather than dying
        ("env -S \"git push 'oops\"", ["env", "git push 'oops"]),
    ],
)
def test_tokens_splice_env_split_payload(cmd, expected):
    assert guard._tokens(cmd) == expected


# A wrapper payload was judged only while a marker was present, so `sh -c "git commit
# --no-verify -m x"` ran the same commit past the always tier (found writing the env -S
# cases, 2026-09-12). The payload is now judged as a command of its own at that tier.
WRAPPED_ALWAYS = [
    "sh -c 'git commit --no-verify -m x'",
    "bash -c 'git commit -n -m x'",
    "zsh -c 'SKIP=ruff git commit -m x'",
    "eval 'git commit --no-verify -m x'",
    "eval git commit --no-verify -m x",
    "sh -c 'sh -c \"git commit --no-verify -m x\"'",
    "sh -c 'pre-commit uninstall'",
    "bash -c 'cd /tmp && /usr/bin/git commit --no-verify -m x'",
]


@pytest.mark.parametrize("cmd", WRAPPED_ALWAYS)
def test_wrapper_payload_judged_at_always_tier(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=False)
    decision, reason = guard.evaluate(bash(cmd, repo))
    assert decision == "deny", cmd
    assert any(w in reason for w in ("hook-bypass", "SKIP=", "pre-commit uninstall")), reason


@pytest.mark.parametrize(
    "cmd",
    [
        "sh -c 'git status'",
        "bash -c 'git commit -m x'",
        "sh -c 'echo \"git commit --no-verify\"'",
        "python3 -c 'print(\"--no-verify\")'",
    ],
)
def test_wrapper_payload_without_bypass_allowed_without_marker(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=False)
    assert decide(bash(cmd, repo)) == "allow", cmd


def test_opaque_token_in_argument_position_is_not_a_program(tmp_path):
    """Variables and substitutions as arguments are ordinary; only the program slot counts."""
    repo = mk_repo(tmp_path, with_state=True)
    for c in (
        "echo $HOME",
        "ls $(pwd)",
        "cat `ls | head -1`",
        "uv run pytest tests/test_${MOD}.py",
        "cd /tmp && echo $?",
    ):
        assert decide(bash(c, repo)) == "allow", c


@pytest.mark.parametrize(
    "cmd",
    [
        "/usr/bin/git status",
        "env git log -1",
        "command git diff",
        "time uv run pytest",
        "nice -n 10 uv run ruff check .",
        "/opt/homebrew/bin/gh pr view 1",
        "sudo -u x ls",
        "(cd /tmp && ls)",
    ],
)
def test_normalized_reads_still_allowed(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash(cmd, repo)) == "allow", cmd


def test_launcher_unwrap_keeps_cd_and_rm_operands_straight(tmp_path):
    """Operand extraction must skip the launcher too: `builtin cd <dir>` targets <dir>,
    and `sudo rm -rf <run dir>` names the run dir, not `rm`."""
    repo = mk_repo(tmp_path, with_state=True)
    rd = repo / ".next-auto" / "20260911-abc123"
    assert decide(bash(f"sudo rm -rf {rd}", repo)) == "deny"
    assert decide(bash(f"env rm -rf {rd}/wt", repo)) == "deny"
    assert decide(bash("command rm -rf .next-auto", repo)) == "deny"
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    assert decide(bash(f"builtin cd {repo} && gh pr merge 1", outside)) == "deny"
    assert decide(bash("sudo rm -rf /tmp/scratch", repo)) == "allow"


# ---- git clean during a run (review, 2026-09-11) --------------------------------------
# `.next-auto/` is only excluded, so `git clean -x` reaches it and removes the marker. Any
# clean carrying -x or -X is denied while armed, as is a clean whose operands resolve into
# the run dir. `git clean -fd` inside the worktree is the shape `revert` uses and stays.

GIT_CLEAN_X = [
    "git clean -fdx",
    "git clean -xdf",
    "git clean -ffdx",
    "git clean -f -d -x",
    "git clean -X -f",
    "git clean -fdX",
    "git clean -fd --exclude=foo -x",
    "/usr/bin/git clean -fdx",
    "env git clean -fdx",
]


@pytest.mark.parametrize("cmd", GIT_CLEAN_X)
def test_git_clean_x_denied_with_marker(tmp_path, cmd):
    repo = mk_repo(tmp_path, with_state=True)
    decision, reason = guard.evaluate(bash(cmd, repo))
    assert decision == "deny", cmd
    assert "git clean" in reason, reason


def test_git_clean_x_via_dash_c_denied_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    assert decide(bash(f"git -C {repo} clean -fdx", repo)) == "deny"
    assert decide(bash(f"git -C {repo} clean -fdx", outside)) == "deny"
    assert decide(bash(f"cd {repo} && git clean -fdx", outside)) == "deny"


def test_git_clean_into_run_dir_denied(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    rd = repo / ".next-auto" / "20260911-abc123"
    wt = rd / "wt"
    for cwd, c in (
        (repo, "git clean -fd .next-auto"),
        (repo, "git clean -fd .next-auto/20260911-abc123"),
        (repo, f"git clean -fd {rd}"),
        (repo, "git clean -fd -- .next-auto"),
        (wt, "git clean -fd ../"),
        (wt, f"git clean -fd {rd}/brief-1.md"),
        (repo, "git clean -f .next-auto/20260911-abc123/state.json"),
    ):
        assert decide(bash(c, cwd)) == "deny", c


def test_git_clean_fd_in_worktree_allowed(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    wt = repo / ".next-auto" / "20260911-abc123" / "wt"
    for cwd, c in (
        (wt, "git clean -fd"),
        (wt, "git clean -fdq"),
        (wt, "git clean -fd src/"),
        (wt, f"git clean -fd {wt}/build"),
        (repo, f"git -C {wt} clean -fd"),
        (repo, f"cd {wt} && git clean -fd"),
        (wt, "git clean -n"),
        (wt, "git clean --dry-run -d"),
    ):
        assert decide(bash(c, cwd)) == "allow", c


def test_git_clean_allowed_without_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=False)
    for c in ("git clean -fdx", "git clean -ffdx", "git clean -X -f", "git clean -fd .next-auto"):
        assert decide(bash(c, repo)) == "allow", c


def test_subagent_git_clean_denied_with_marker(tmp_path):
    repo = mk_repo(tmp_path, with_state=True)
    assert decide(bash("git clean -fd", repo, agent=True)) == "deny"


# ---- adw 3.5.0: npx, bunx and npm exec are launchers (spec item 4, D2) --------------------

# Fixture shapes: the flags `npx --help` and `npm exec --help` print with npm 10.9.9 on devbox
# (`--package`, `-c|--call`, alias `x`; `-p` is npx's short form, spec item 4) and the ones
# `bunx --help` prints with bun 1.4.2 (`-p, --package <package>`, flag-only `--bun`), read
# 2026-10-13. Plain `sed -i` into the holdout is allowed today, since sed is no tamper
# program, so the forms run `rm`, which the holdout rule denies when it runs plain.
HOLDOUT_RM = "rm evals/x.txt"
LAUNCHED_RM = (
    "npx rm evals/x.txt",
    "npx -y rm evals/x.txt",
    "npx --yes rm evals/x.txt",
    "npx -p pkg rm evals/x.txt",
    "npx --package pkg rm evals/x.txt",
    "npx --package=pkg rm evals/x.txt",
    'npx -c "rm evals/x.txt"',
    "npx --call 'rm evals/x.txt'",
    "npx --package=pkg -c 'rm evals/x.txt'",
    "bunx rm evals/x.txt",
    "bunx -p pkg rm evals/x.txt",
    "bunx --bun rm evals/x.txt",
    "npm exec -- rm evals/x.txt",
    "npm exec --package=pkg -- rm evals/x.txt",
    "npm exec -c 'rm evals/x.txt'",
    "npm x -- rm evals/x.txt",
)


def _armed_wt(tmp_path):
    repo = mk_repo(tmp_path, with_state=True, holdout="evals/")
    return repo / ".next-auto" / "20260911-abc123" / "wt"


@pytest.mark.parametrize("cmd", LAUNCHED_RM)
def test_a_launched_holdout_write_is_denied_as_the_plain_one_is(tmp_path, cmd):
    wt = _armed_wt(tmp_path)
    plain = guard.evaluate(bash(HOLDOUT_RM, wt, agent=True))
    assert plain[0] == "deny" and "shell write under the holdout path (evals/)" in plain[1]
    assert guard.evaluate(bash(cmd, wt, agent=True)) == plain


@pytest.mark.parametrize("cmd", ["npx claude -p hi", "bunx claude -p hi", "npm x -- claude -p hi"])
def test_a_launched_claude_is_a_nested_claude(tmp_path, cmd):
    wt = _armed_wt(tmp_path)
    decision, reason = guard.evaluate(bash(cmd, wt, agent=True))
    assert decision == "deny" and "nested claude invocation during a run" in reason


@pytest.mark.parametrize(
    "cmd",
    ["npx vitest run", "npx -y prettier --check .", "bunx vitest run", "npm exec -- vitest run"],
)
def test_a_launched_test_runner_is_allowed(tmp_path, cmd):
    assert decide(bash(cmd, _armed_wt(tmp_path), agent=True)) == "allow"
