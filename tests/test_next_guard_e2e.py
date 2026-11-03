# Description: End-to-end tests for next-guard.py: exec the hook as a subprocess with JSON on
# Description: stdin, absolute paths under a real repo with a real .next-auto/<id>/wt worktree,
# Description: and assert the exit code. The unit tests call evaluate() with the author's path
# Description: shapes; six gaps reached the installed hook that way (review, 2026-09-11).

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# NEXT_GUARD_HOOK points the suite at another copy, so the installed hook can be exercised
# with every payload here:
#   NEXT_GUARD_HOOK=~/.claude/hooks/next-guard.py uv run pytest tests/test_next_guard_e2e.py
_DEFAULT_HOOK = Path(__file__).resolve().parents[1] / "hooks" / "next-guard.py"
HOOK = Path(os.environ.get("NEXT_GUARD_HOOK") or _DEFAULT_HOOK).expanduser()
RUN_ID = "20260911-e2e000"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


class Repo:
    """A real git repo with a bare origin and, optionally, a live run: a worktree at
    .next-auto/<id>/wt on a run branch and a state.json marker with absolute paths."""

    def __init__(self, base: Path, *, armed: bool):
        origin = base / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        self.root = base / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@t.t")
        git(self.root, "config", "user.name", "t")
        git(self.root, "remote", "add", "origin", str(origin))
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "evals").mkdir()
        (self.root / "src" / "app.py").write_text("X = 1\n")
        (self.root / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n")
        (self.root / "evals" / "case1.json").write_text("{}\n")
        (self.root / ".pre-commit-config.yaml").write_text("repos: []\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "init")
        git(self.root, "push", "-q", "-u", "origin", "main")
        self.run_dir = self.root / ".next-auto" / RUN_ID
        self.wt = self.run_dir / "wt"
        self.branch = f"next-auto/fixture-{RUN_ID}"
        self.run_dir.mkdir(parents=True)
        git(self.root, "worktree", "add", "-q", "--no-track", "-b", self.branch, str(self.wt))
        self.state = self.run_dir / "state.json"
        (self.run_dir / "brief-1.md").write_text("brief\n")
        self.armed = armed
        if armed:
            write_marker(self.state, RUN_ID, self.root, self.branch, self.wt)


def write_marker(marker, run_id, checkout, branch, wt):
    """The state.json `start` writes, with absolute paths, for a run started from checkout."""
    marker.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "root": str(checkout),
                "default": "main",
                "branch": branch,
                "worktree": str(wt),
                "run_dir": str(marker.parent),
                "plan": "prompt_plan.md",
                "holdout": "evals/",
            }
        )
    )


@pytest.fixture(scope="module")
def armed(tmp_path_factory):
    return Repo(tmp_path_factory.mktemp("armed"), armed=True)


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    return Repo(tmp_path_factory.mktemp("clean"), armed=False)


def run_hook(payload, cwd):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def bash_payload(cmd, cwd, agent=False):
    p = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": str(cwd),
        "tool_input": {"command": cmd},
    }
    if agent:
        p["agent_id"] = "sub-1"
    return p


def edit_payload(path, cwd, tool="Edit", agent=False):
    p = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "cwd": str(cwd),
        "tool_input": {"file_path": str(path)},
    }
    if agent:
        p["agent_id"] = "sub-1"
    return p


def expect_deny(payload, cwd, needle=None):
    r = run_hook(payload, cwd)
    assert r.returncode == 2, f"expected deny (exit 2), got {r.returncode}: {r.stderr}"
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    if needle:
        assert needle in out["permissionDecisionReason"], out["permissionDecisionReason"]
    return out["permissionDecisionReason"]


def expect_allow(payload, cwd):
    r = run_hook(payload, cwd)
    assert r.returncode == 0, f"expected allow (exit 0), got {r.returncode}: {r.stdout} {r.stderr}"
    assert r.stdout.strip() == ""


# Every Bash case runs three ways: as given, behind `cd /tmp && `, and behind a newline
# prefix. Claude Code emits both compound shapes constantly.
PREFIXES = {
    "plain": "",
    "cd-prefix": "cd /tmp && ",
    "newline-prefix": "cd /tmp\necho start\n",
}


# ---- always-deny: hook bypass, with and without a marker ---------------------------


def always_deny_cases(repo: Repo) -> dict[str, str]:
    root = repo.root
    return {
        "no-verify": "git commit --no-verify -m x",
        "short-n": "git commit -n -m x",
        "bundled-anm": "git commit -anm x",
        "hooksPath": "git -c core.hooksPath=/dev/null commit -m x",
        "SKIP": "SKIP=ruff git commit -m x",
        "GIT_CONFIG_PARAMETERS": (
            "GIT_CONFIG_PARAMETERS=\"'core.hooksPath=/dev/null'\" git commit -m x"
        ),
        "PRE_COMMIT_ALLOW_NO_CONFIG": "PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m x",
        "pre-commit-uninstall": "pre-commit uninstall",
        "chmod-hooks-abs": f"chmod -x {root}/.git/hooks/pre-commit",
        "chmod-hooks-rel": "chmod +x .git/hooks/pre-commit",
        # program spelled as a path or behind a launcher (review, 2026-09-11)
        "abs-git-no-verify": "/usr/bin/git commit --no-verify -m x",
        "rel-git-short-n": "./git commit -n -m x",
        "command-git": "command git commit --no-verify -m x",
        "env-git": "env git commit --no-verify -m x",
        "env-i-assign-git": "env -i GIT_DIR=.git git commit --no-verify -m x",
        # env -S re-splits its string into the command; bundled clusters (review, 2026-09-12)
        "env-S-no-verify": "env -S 'git commit --no-verify -m x'",
        "env-split-string-eq": "env --split-string='git commit --no-verify -m x'",
        "env-iS-no-verify": "env -iS 'git commit --no-verify -m x'",
        "sudo-Hu-no-verify": "sudo -Hu root git commit --no-verify -m x",
        "sh-c-no-verify": "sh -c 'git commit --no-verify -m x'",
        "eval-no-verify": "eval 'git commit --no-verify -m x'",
        "exec-abs-git": "exec /opt/homebrew/bin/git commit --no-verify -m x",
        "nice-git": "nice -n 10 git commit --no-verify -m x",
        "sudo-u-git": "sudo -u x git commit --no-verify -m x",
        "xargs-git": "xargs git commit --no-verify -m x",
        "subshell-git": "(git commit --no-verify -m x)",
        "group-git": "{ git commit --no-verify -m x; }",
    }


ALWAYS_KEYS = list(always_deny_cases(type("R", (), {"root": Path("/r")})()).keys())


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("key", ALWAYS_KEYS)
def test_always_deny_with_marker(armed, key, prefix):
    cmd = PREFIXES[prefix] + always_deny_cases(armed)[key]
    expect_deny(bash_payload(cmd, armed.wt), armed.wt)


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("key", ALWAYS_KEYS)
def test_always_deny_without_marker(clean, key, prefix):
    cmd = PREFIXES[prefix] + always_deny_cases(clean)[key]
    expect_deny(bash_payload(cmd, clean.root), clean.root)


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_always_deny_edits(armed, clean, tool):
    for repo in (armed, clean):
        expect_deny(edit_payload(repo.root / ".git" / "config", repo.root, tool), repo.root)
        expect_deny(
            edit_payload(repo.root / ".git" / "hooks" / "pre-commit", repo.wt, tool), repo.wt
        )
        # the worktree's .git is a file; editing it is still an edit under .git
        expect_deny(edit_payload(repo.wt / ".git", repo.wt, tool), repo.wt)
        expect_deny(edit_payload(repo.wt / ".pre-commit-config.yaml", repo.wt, tool), repo.wt)
        expect_deny(edit_payload(repo.root / ".pre-commit-config.yaml", repo.root, tool), repo.root)


# ---- marker-present denies: denied while armed, allowed in a clean repo ------------


def marker_deny_cases(repo: Repo) -> dict[str, str]:
    rd, wt, root = repo.run_dir, repo.wt, repo.root
    return {
        # merge / release
        "gh-pr-merge": "gh pr merge 1 --squash",
        "next-auto-merge": f"python3 {root}/scripts/next_auto.py merge 7",
        "next-auto-merge-root": f"python3 {root}/scripts/next_auto.py --root . merge 7",
        "gh-api-merge": "gh api -X PUT repos/ryanmat/x/pulls/1/merge",
        "gh-release": "gh release create v1.0.0",
        # push
        "push-force": "git push --force origin main",
        "push-f": "git push -f origin main",
        "push-force-with-lease": "git push --force-with-lease origin main",
        "push-mirror": "git push --mirror origin",
        "push-plus-ref": "git push origin +main",
        "push-local-remote": "git push origin feature:main",
        "push-colon-delete": "git push origin :feature",
        "push-delete": "git push origin --delete feature",
        "push-d": "git push -d origin feature",
        "push-var": "F=--force; git push $F origin main",
        "push-var-braces": "git push ${F} origin main",
        # destructive git
        "stash": "git stash",
        "stash-push": "git stash push -m wip",
        "tag": "git tag v1.0.0",
        "checkout-default": "git checkout main",
        "switch-default": "git switch main",
        "reset-hard": "git reset --hard HEAD~1",
        "reset-hard-abs-wt": f"cd {wt}\ngit reset --hard a5c5b25; echo \"RESET_EXIT: $?\"",
        "worktree-remove": f"git worktree remove {wt}",
        "worktree-remove-force": f"git worktree remove --force {wt}",
        "branch-D": f"git branch -D {repo.branch}",
        # run dir and marker
        "rm-rf-run-dir-abs": f"rm -rf {rd}",
        "rm-rf-worktree-abs": f"rm -rf {wt}",
        "cd-run-dir-rm-rf-wt": f"cd {rd} && rm -rf wt",
        "rm-rf-parent-from-wt": "rm -rf ../../",
        "rm-r-run-dir-rel": f"cd {root} && rm -r .next-auto/{RUN_ID}",
        "rm-marker-abs": f"rm {rd}/state.json",
        "rm-f-marker": f"rm -f {rd}/state.json",
        "mv-marker": f"mv {rd}/state.json /tmp/state.json",
        "cp-over-marker": f"cp /tmp/x.json {rd}/state.json",
        "tee-marker": f"echo '{{}}' | tee {rd}/state.json",
        "truncate-marker": f"truncate -s 0 {rd}/state.json",
        "redirect-marker": f"echo '{{}}' > {rd}/state.json",
        "append-marker": f"echo '{{}}' >> {rd}/state.json",
        "find-delete-marker": f"find {rd} -name state.json -delete",
        "find-exec-marker": f"find {rd} -name state.json -exec rm {{}} +",
        "cd-rm-marker": f"cd {rd} && rm state.json",
        # wrappers
        "sh-c-git": "sh -c 'git push --force origin main'",
        "bash-c-gh": "bash -c 'gh pr merge 1'",
        "zsh-c-git": "zsh -c 'git stash'",
        "eval-git": 'eval "git stash"',
        "eval-subst-gh": 'eval "$(gh auth status)"',
        "heredoc-git": "cat <<'EOF' > /tmp/run.sh\ngit push --force origin main\nEOF",
        "heredoc-marker": f"cat > {rd}/state.json <<'EOF'\n{{}}\nEOF",
        "python-c-git": "python3 -c \"import subprocess; subprocess.run(['git', 'push'])\"",
        "python-c-gh": "python -c \"import os; os.system('gh pr merge 1')\"",
        "python-c-claude": "python3 -c \"import os; os.system('claude -p hi')\"",
        "python-c-marker": f"python3 -c \"import os; os.remove('{rd}/state.json')\"",
        "python-c-next-auto": "python3 -c \"import shutil; shutil.rmtree('.next-auto')\"",
        # nested claude
        "nested-claude": "claude -p 'finish the run'",
        # program spelled as a path, behind a launcher, or opaque (review, 2026-09-11)
        "abs-gh-merge": "/usr/bin/gh pr merge 1",
        "abs-git-push-force": "/usr/bin/git push --force",
        "env-git-stash": "env git stash",
        "env-S-push-force": "env -S 'git push --force'",
        "abs-env-S-gh-merge": "/usr/bin/env -S 'gh pr merge 1'",
        "env-iS-stash": "env -iS 'git stash'",
        "env-S-sh-c-push": "env -S 'sh -c \"git push --force\"'",
        "env-S-rm-run-dir": f"env -S 'rm -rf {rd}'",
        "sudo-Hu-push-force": "sudo -Hu root git push --force",
        "command-git-reset-hard": "command git reset --hard",
        "home-claude": "~/.local/bin/claude -p x",
        "subshell-stash": "(git stash)",
        "group-stash": "{ git stash; }",
        "subst-which-git": "$(which git) push --force",
        "backtick-which-git": "`which git` stash",
        "var-git": "$GIT push --force",
        "brace-var-git": "${GIT} stash",
        "escaped-git": "\\git push --force",
        # git clean reaching the excluded run dir (review, 2026-09-11)
        "clean-fdx": "git clean -fdx",
        "clean-xdf": "git clean -xdf",
        "clean-ffdx": "git clean -ffdx",
        "clean-f-d-x": "git clean -f -d -x",
        "clean-X-f": "git clean -X -f",
        "clean-dash-C-root-x": f"git -C {root} clean -fdx",
        "abs-git-clean-x": "/usr/bin/git clean -fdx",
        "clean-into-run-dir-rel": f"cd {root} && git clean -fd .next-auto",
        "clean-run-dir-abs": f"git clean -fd {rd}",
        "clean-parent-from-wt": "git clean -fd ../",
        "clean-marker": f"git clean -f {rd}/state.json",
        # holdout through the shell
        "redirect-holdout-abs": f"echo x > {wt}/evals/case1.json",
        "append-holdout-rel": "echo x >> evals/case1.json",
        "cp-holdout": f"cp /tmp/x.json {wt}/evals/case2.json",
        "rm-holdout": "rm evals/case1.json",
        "mv-holdout": f"mv {wt}/evals/case1.json /tmp/",
        "tee-holdout": "echo x | tee evals/case1.json",
        "heredoc-holdout": "cat > evals/case1.json <<'EOF'\n{}\nEOF",
        "root-copy-holdout": f"echo x > {root}/evals/case1.json",
    }


MARKER_KEYS = list(
    marker_deny_cases(
        type("R", (), {"run_dir": Path("/r/.next-auto/x"), "wt": Path("/r/.next-auto/x/wt"),
                       "root": Path("/r"), "branch": "b"})()
    ).keys()
)


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("key", MARKER_KEYS)
def test_marker_deny_from_the_worktree(armed, key, prefix):
    cmd = PREFIXES[prefix] + marker_deny_cases(armed)[key]
    reason = expect_deny(bash_payload(cmd, armed.wt), armed.wt)
    assert "next-auto run active" in reason and str(armed.state) in reason


@pytest.mark.parametrize("key", MARKER_KEYS)
def test_marker_deny_from_the_root(armed, key):
    cmd = marker_deny_cases(armed)[key]
    expect_deny(bash_payload(cmd, armed.root), armed.root)


@pytest.mark.parametrize("key", MARKER_KEYS)
def test_marker_rules_are_off_without_a_marker(clean, key):
    """The same commands in a repo with no run are allowed: these rules exist only
    while a run is live. A hook that denied them always would block normal work."""
    cmd = marker_deny_cases(clean)[key]
    expect_allow(bash_payload(cmd, clean.wt), clean.wt)


# ---- marker-present denies through Edit/Write/MultiEdit ----------------------------


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_marker_edit_denied(armed, tool):
    expect_deny(edit_payload(armed.state, armed.wt, tool), armed.wt, "state.json")
    expect_deny(edit_payload(armed.run_dir / "state.json.tmp", armed.wt, tool), armed.wt)
    expect_deny(edit_payload(f".next-auto/{RUN_ID}/state.json", armed.root, tool), armed.root)


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_holdout_edit_denied(armed, tool):
    # absolute worktree path, as the harness sends it
    expect_deny(edit_payload(armed.wt / "evals" / "case1.json", armed.wt, tool, agent=True),
                armed.wt, "holdout")
    expect_deny(edit_payload(armed.wt / "evals" / "new" / "case9.json", armed.wt, tool), armed.wt)
    # relative from the worktree
    expect_deny(edit_payload("evals/case1.json", armed.wt, tool, agent=True), armed.wt)
    # the root copy
    expect_deny(edit_payload(armed.root / "evals" / "case1.json", armed.root, tool), armed.root)


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_marker_and_holdout_edits_allowed_without_a_marker(clean, tool):
    expect_allow(edit_payload(clean.wt / "evals" / "case1.json", clean.wt, tool), clean.wt)
    expect_allow(edit_payload(clean.run_dir / "state.json", clean.wt, tool), clean.wt)


# ---- subagent restriction ----------------------------------------------------------

SUBAGENT_DENY = [
    "git commit -m x",
    "/usr/bin/git commit -m x",
    "env git add -A",
    "command gh pr list",
    "git add -A",
    "git checkout -b feature",
    "git rev-parse HEAD",
    "git push -u origin next-auto/x",
    "git fetch origin",
    "gh pr list",
    "gh pr view 1",
    "gh repo view --json name",
]

SUBAGENT_ALLOW = [
    "git status --short",
    "/usr/bin/git status --short",
    "git diff --stat",
    "git log --oneline -3",
    "git show HEAD --stat",
]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("cmd", SUBAGENT_DENY)
def test_subagent_denied_while_armed(armed, cmd, prefix):
    expect_deny(bash_payload(PREFIXES[prefix] + cmd, armed.wt, agent=True), armed.wt, "subagents")


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("cmd", SUBAGENT_ALLOW)
def test_subagent_readonly_git_allowed_while_armed(armed, cmd, prefix):
    expect_allow(bash_payload(PREFIXES[prefix] + cmd, armed.wt, agent=True), armed.wt)


@pytest.mark.parametrize("cmd", SUBAGENT_DENY)
def test_subagent_restriction_is_off_without_a_marker(clean, cmd):
    expect_allow(bash_payload(cmd, clean.wt, agent=True), clean.wt)


@pytest.mark.parametrize("cmd", ["git commit -m x", "git add -A", "gh pr list"])
def test_orchestrator_is_not_a_subagent(armed, cmd):
    expect_allow(bash_payload(cmd, armed.wt), armed.wt)


# ---- malformed stdin ---------------------------------------------------------------


def test_malformed_stdin_fails_closed_while_armed(armed):
    r = run_hook("not json", armed.wt)
    assert r.returncode == 2
    assert "Traceback" in r.stderr
    r = run_hook("", armed.root)
    assert r.returncode == 2


def test_malformed_stdin_fails_open_without_a_marker(clean):
    r = run_hook("not json", clean.wt)
    assert r.returncode == 0
    assert "Traceback" in r.stderr


# ---- allows while armed: the work a run actually does ------------------------------


def allow_cases(repo: Repo) -> dict[str, str]:
    rd, wt = repo.run_dir, repo.wt
    return {
        "plain-commit": 'git commit -m "feat(step-1): first"',
        "commit-a": 'git commit -am "message"',
        "push-u-run-branch": f"git push -u origin {repo.branch}",
        "checkout-b-feature": "git checkout -b feature",
        "switch-c-feature": "git switch -c feature",
        "log-main": "git log main --oneline -3",
        "diff-main": "git diff main...HEAD --stat",
        "ls-dot-claude": "ls .claude/",
        "cat-evals": "cat evals/case1.json",
        "ls-evals": "ls -la evals/",
        "grep-no-verify": "grep -rn -- --no-verify docs/",
        "rg-hooks-path": "rg core.hooksPath ~/.claude/hooks",
        "echo-no-verify-doc": "echo 'never use --no-verify' >> CONTRIBUTING.md",
        "cat-marker": f"cat {rd}/state.json",
        "cat-brief": f"cat {rd}/brief-1.md",
        "ls-run-dir": f"ls -la {rd}",
        "find-marker-no-delete": f"find {rd} -name state.json",
        "find-pyc-delete": "find . -name '*.pyc' -delete",
        "uv-run-pytest-evals": "uv run pytest evals/",
        "cd-wt-pytest": f"cd {wt} && uv run pytest -q",
        "multiline-pytest-ruff": f"cd {wt}\nuv run pytest\nuv run ruff check .",
        "next-auto-gate": "python3 ~/.claude/scripts/next_auto.py gate 1",
        "next-auto-set-note": (
            'printf "3 tests green" > /tmp/note.txt\n'
            "python3 ~/.claude/scripts/next_auto.py set 1 done --note-file /tmp/note.txt"
        ),
        "next-auto-state": "python3 ~/.claude/scripts/next_auto.py state",
        "python-c-benign": "python3 -c 'print(1 + 1)'",
        "bash-c-benign": "bash -c 'ls -la && echo hi'",
        "heredoc-benign": "cat > /tmp/note.txt <<'EOF'\nRuling: kept X / spec R1 / cost none\nEOF",
        "redirect-tests": f"echo 'def test_y(): pass' > {wt}/tests/test_y.py",
        "redirect-src": "echo 'Y = 2' >> src/app.py",
        "rm-pycache": f"rm -rf {wt}/src/__pycache__",
        "mv-src": f"mv {wt}/src/app.py {wt}/src/app_old.py",
        # normalized programs doing read-only work
        "abs-git-status": "/usr/bin/git status",
        "env-git-log": "env git log -1",
        "env-S-git-status": "env -S 'git status'",
        "sudo-Hu-git-diff": "sudo -Hu root git diff",
        "command-git-diff": "command git diff",
        "time-pytest": "time uv run pytest -q",
        "subshell-ls": "(cd /tmp && ls)",
        "var-as-argument": "echo $HOME && ls $(pwd)",
        # the clean shape revert uses, inside the worktree
        "clean-fd-wt": "git clean -fd",
        "clean-fd-src": "git clean -fd src/",
        "clean-dash-C-wt-fd": f"git -C {wt} clean -fd",
        "clean-dry-run": "git clean -n -d",
    }


ALLOW_KEYS = list(
    allow_cases(type("R", (), {"run_dir": Path("/r/.next-auto/x"),
                               "wt": Path("/r/.next-auto/x/wt"), "branch": "b"})()).keys()
)


@pytest.mark.parametrize("key", ALLOW_KEYS)
def test_allowed_while_armed(armed, key):
    expect_allow(bash_payload(allow_cases(armed)[key], armed.wt), armed.wt)


@pytest.mark.parametrize("key", ALLOW_KEYS)
def test_allowed_without_a_marker(clean, key):
    expect_allow(bash_payload(allow_cases(clean)[key], clean.wt), clean.wt)


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_worktree_writes_allowed_while_armed(armed, tool):
    for rel in ("src/app.py", "src/new_module.py", "tests/test_new.py", "src/evals_helper.py"):
        expect_allow(edit_payload(armed.wt / rel, armed.wt, tool, agent=True), armed.wt)
        expect_allow(edit_payload(rel, armed.wt, tool, agent=True), armed.wt)
    # other run-dir files (the orchestrator saves report-K.md there)
    expect_allow(edit_payload(armed.run_dir / "report-1.md", armed.root, tool), armed.root)


# ---- attended record: no marker, nothing armed --------------------------------


@pytest.fixture(scope="module")
def attended(tmp_path_factory):
    repo = Repo(tmp_path_factory.mktemp("attended"), armed=False)
    rec = repo.root / ".next-auto" / "attended"
    rec.mkdir(parents=True)
    (rec / "step-1.json").write_text(json.dumps({"base_head": "abc", "failures": 0}))
    (rec / "brief-1.md").write_text("brief\n")
    return repo


@pytest.mark.parametrize("cmd", ["git push origin main", "git commit -m x", "gh pr merge 7"])
def test_attended_record_alone_arms_nothing(attended, cmd):
    expect_allow(bash_payload(cmd, attended.root), attended.root)


# ---- 3.4.3: the marker read as the Stop hook reads it, guard paths G1 to G6 ------------------

LINKED_RUN_ID = "20260928-e2e001"


def linked_worktree(repo, name):
    tree = repo.root.parent / name
    git(repo.root, "worktree", "add", "-q", "-b", name, str(tree))
    return tree


class LinkedRun:
    """A real repo whose live run was started from a linked worktree: the main checkout with
    no run of its own, the linked worktree holding the marker under its own .next-auto/ with
    the run's worktree beside it, as `start` lays them out there, and a directory outside
    any repository."""

    def __init__(self, base: Path):
        self.repo = Repo(base, armed=False)
        self.main = self.repo.root
        self.linked = linked_worktree(self.repo, "linked")
        run_dir = self.linked / ".next-auto" / LINKED_RUN_ID
        run_dir.mkdir(parents=True)
        self.wt = run_dir / "wt"
        branch = f"next-auto/fixture-{LINKED_RUN_ID}"
        git(self.linked, "worktree", "add", "-q", "--no-track", "-b", branch, str(self.wt))
        self.marker = run_dir / "state.json"
        write_marker(self.marker, LINKED_RUN_ID, self.linked, branch, self.wt)
        self.outside = base / "outside"
        self.outside.mkdir()


@pytest.fixture(scope="module")
def linked_run(tmp_path_factory):
    return LinkedRun(tmp_path_factory.mktemp("linkedrun"))


@pytest.fixture(scope="module")
def main_run(tmp_path_factory):
    """The armed repo, its run live from the main checkout, plus a linked worktree with no
    run of its own."""
    repo = Repo(tmp_path_factory.mktemp("mainrun"), armed=True)
    repo.other = linked_worktree(repo, "other")
    return repo


def test_g1_a_run_started_from_a_linked_worktree_arms_that_worktree(linked_run):
    cwd = linked_run.linked
    reason = expect_deny(bash_payload("gh pr merge 1", cwd), cwd)
    assert str(linked_run.marker) in reason


def test_g2_a_run_started_from_a_linked_worktree_arms_its_own_run_worktree(linked_run):
    cwd = linked_run.wt
    reason = expect_deny(bash_payload("gh pr merge 1", cwd), cwd)
    assert str(linked_run.marker) in reason


def test_g3_a_run_live_from_the_main_checkout_leaves_a_linked_worktree_unarmed(main_run):
    """The guard mirrors the Stop hook's run scope (the owner, 2026-09-28): a run arms the checkout
    it was started from and its own worktree, not every worktree of the repository."""
    expect_allow(bash_payload("gh pr merge 1", main_run.other), main_run.other)


def test_g4_a_run_live_from_a_linked_worktree_leaves_the_main_checkout_unarmed(linked_run):
    expect_allow(bash_payload("gh pr merge 1", linked_run.main), linked_run.main)


@pytest.mark.parametrize("shape", ["cd", "git-C"])
def test_g5_a_linked_worktrees_marker_is_found_through_cd_and_git_dash_c(linked_run, shape):
    cmd = {
        "cd": f"cd {linked_run.linked} && gh pr merge 1",
        "git-C": f"git -C {linked_run.linked} stash",
    }[shape]
    reason = expect_deny(bash_payload(cmd, linked_run.outside), linked_run.outside)
    assert str(linked_run.marker) in reason


@pytest.mark.parametrize("where", ["main-checkout", "run-worktree"])
def test_g6_a_run_live_from_the_main_checkout_arms_it_and_its_run_worktree(armed, where):
    cwd = armed.root if where == "main-checkout" else armed.wt
    reason = expect_deny(bash_payload("gh pr merge 1", cwd), cwd)
    assert str(armed.state) in reason


@pytest.mark.parametrize("where", ["linked-worktree", "run-worktree"])
def test_g7_malformed_stdin_fails_closed_in_a_linked_worktrees_live_run(linked_run, where):
    cwd = linked_run.linked if where == "linked-worktree" else linked_run.wt
    r = run_hook("not json", cwd)
    assert r.returncode == 2
    assert "Traceback" in r.stderr


@pytest.mark.parametrize("case", ["rm-run-dir", "write-marker", "write-holdout"])
def test_g8_a_linked_worktree_reaches_a_main_checkout_runs_files_unarmed(main_run, case):
    """The run scope the owner ruled for the guard (2026-09-28) covers the marker, run directory and
    holdout rules as well as merge and push: a session in a linked worktree with no run of its
    own is not armed by a run live from the main checkout, even for that run's own files named
    by absolute path."""
    cwd = main_run.other
    payload = {
        "rm-run-dir": bash_payload(f"rm -rf {main_run.run_dir}", cwd),
        "write-marker": edit_payload(main_run.state, cwd, "Write"),
        "write-holdout": edit_payload(main_run.root / "evals" / "case1.json", cwd, "Write"),
    }[case]
    expect_allow(payload, cwd)
