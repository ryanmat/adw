#!/usr/bin/env python3
# Description: Eval runner for /adw:next auto: resets the shared fixture repo, drives the real
# Description: Claude Code CLI, and grades the resulting git and PR state deterministically.

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import graders
from graders import GraderError, RunState, git

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import record as ledger_record

HERE = Path(__file__).resolve().parent
# the repo root is the adw plugin; loading it puts the sources under test behind /adw:next
REPO_ROOT = HERE.parents[1]
# the plugin the session must report having loaded from the working tree
PLUGIN_NAME = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())["name"]
SCENARIO_DIR = HERE / "scenarios"
PLAN_DIR = HERE / "fixtures" / "plans"
RESULTS_DIR = HERE / "results"
LOCK_PATH = HERE / ".run.lock"
# the fixture clone lives outside this repo: the working tree is loaded as the plugin, and
# Claude Code (2.1.274) denies Write and Edit under a loaded plugin's directory as a
# sensitive file, so a clone under evals/next-auto/ cannot take the loop's edits
DEFAULT_WORKDIR = Path.home() / ".cache" / "adw" / "next-auto"

# the fixture repository is the operator's own: ADW_EVAL_FIXTURE_REPO names it as owner/name,
# and evals/next-auto/fixtures/baseline/ is the tree its `baseline` tag carries
FIXTURE_REPO_VAR = "ADW_EVAL_FIXTURE_REPO"
FIXTURE_REPO = os.environ.get(FIXTURE_REPO_VAR, "")
FIXTURE_URL = f"https://github.com/{FIXTURE_REPO}.git"
BASELINE_TAG = "baseline"
# where next_auto.py puts a run: <root>/.next-auto/<run-id>/
RUN_SUBDIR = ".next-auto"

# Opus, not the ceiling tier: these runs are mechanical orchestration, and the eval
# is graded on git state rather than on prose quality.
MODEL = "claude-opus-4-8"

# A runaway run is a defect, not an expense to absorb. Read from the CLI's own
# total_cost_usd so the cap is measured, not estimated.
DEFAULT_COST_CAP_USD = Decimal("4.00")
DEFAULT_TIMEOUT_S = 1800

REQUIRED_FIELDS = ("name", "prompt", "plan", "allowed_tools", "graders")


@dataclass
class Scenario:
    name: str
    prompt: str
    plan: str
    allowed_tools: list[str]
    graders: list[str]
    grader_args: dict
    extra_files: dict = field(default_factory=dict)
    origin_owner_override: str | None = None
    notes: str = ""
    cost_cap_usd: Decimal | None = None
    # a guard scenario: every trial must pass (pass^k), recorded as pass_k
    consistency: bool = False

    @classmethod
    def load(cls, path: Path) -> Scenario:
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            raise GraderError(f"{path.name} is missing required fields: {missing}")
        unknown = [g for g in data["graders"] if g not in graders.GRADERS]
        if unknown:
            raise GraderError(f"{path.name} names unknown graders: {unknown}")
        return cls(
            name=data["name"],
            prompt=data["prompt"],
            plan=data["plan"],
            allowed_tools=data["allowed_tools"],
            graders=data["graders"],
            grader_args=data.get("grader_args", {}),
            extra_files=data.get("extra_files", {}),
            origin_owner_override=data.get("origin_owner_override"),
            notes=data.get("notes", ""),
            cost_cap_usd=_usd(data.get("cost_cap_usd")),
            consistency=bool(data.get("consistency", False)),
        )


def _usd(value) -> Decimal | None:
    """A USD figure as Decimal, read through str so a float from JSON keeps its printed value."""
    return None if value is None else Decimal(str(value))


def effective_cap(scenario: Scenario, default_cap: Decimal) -> Decimal:
    """A scenario's own cap wins over the CLI default. The cap detects a runaway run,
    and what counts as runaway depends on the path the scenario prescribes: a three-step
    happy path or a retry plus review plus fix round legitimately costs more than a
    preflight refusal."""
    return scenario.cost_cap_usd if scenario.cost_cap_usd is not None else Decimal(str(default_cap))


# ---- the shared fixture -----------------------------------------------------


@contextlib.contextmanager
def run_lock():
    """Scenarios share one remote fixture, so exactly one may run at a time."""
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise GraderError(
            f"another eval run holds {LOCK_PATH}. Remove it if that run is dead."
        ) from None
    try:
        os.write(fd, f"{os.getpid()} {datetime.now(UTC).isoformat()}\n".encode())
        os.close(fd)
        yield
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def ensure_clone(workdir: Path) -> Path:
    clone = workdir / "next-auto-fixture"
    if not (clone / ".git").is_dir():
        clone.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", FIXTURE_URL, str(clone)], check=True
        )
    return clone


def reset_fixture(clone: Path) -> str:
    """Return the fixture to the baseline tag and clear every artifact of a prior run. The
    remember plugin's hooks write `.remember/` into the clone as the previous scenario's CLI
    session ends, and `git clean -qfdx` raced the last writes on 2026-11-05 22:36 EST
    (docs/adw-3.5.11-engine-plan.md:98), so the reset removes that directory itself and, on a
    clean that still fails, waits two seconds and runs it once more before it raises
    (3.5.12 R8)."""
    # close any PR a prior run opened, and delete its branch
    listing = subprocess.run(
        ["gh", "pr", "list", "--repo", FIXTURE_REPO, "--state", "open", "--json", "number"],
        capture_output=True,
        text=True,
    )
    if listing.returncode == 0 and listing.stdout.strip():
        for pr in json.loads(listing.stdout):
            subprocess.run(
                ["gh", "pr", "close", str(pr["number"]), "--repo", FIXTURE_REPO,
                 "--delete-branch"],
                capture_output=True,
                text=True,
            )

    git(clone, "worktree", "prune")
    for wt in (clone / RUN_SUBDIR).glob("*/wt"):
        # a pruned worktree's directory is no longer registered; rmtree below takes it
        git(clone, "worktree", "remove", "--force", str(wt), check=False)
    shutil.rmtree(clone / RUN_SUBDIR, ignore_errors=True)

    git(clone, "checkout", "--quiet", "main")
    # --force: a moved baseline tag must follow the remote, or the reset lands on the old commit
    git(clone, "fetch", "--quiet", "--prune", "--tags", "--force", "origin")
    git(clone, "reset", "--hard", "--quiet", BASELINE_TAG)
    shutil.rmtree(clone / ".remember", ignore_errors=True)
    cleaned = git(clone, "clean", "-qfdx", check=False)
    if cleaned.returncode != 0:
        time.sleep(2)
        cleaned = git(clone, "clean", "-qfdx", check=False)
        if cleaned.returncode != 0:
            raise GraderError(f"git clean -qfdx failed: {cleaned.stderr.strip()[-400:]}")

    # local and remote run branches from prior scenarios
    for line in git(clone, "branch", "--list", "next-auto/*").stdout.splitlines():
        name = line.strip().lstrip("* ").strip()
        if name:
            git(clone, "branch", "-D", name)
    remote = git(clone, "ls-remote", "--heads", "origin", "next-auto/*").stdout
    for line in remote.splitlines():
        ref = line.split("\t")[-1].strip()
        if ref.startswith("refs/heads/"):
            subprocess.run(
                ["git", "-C", str(clone), "push", "--quiet", "origin", "--delete",
                 ref[len("refs/heads/") :]],
                capture_output=True,
                text=True,
            )

    # main must sit exactly on baseline; a prior run should never have moved it
    baseline = git(clone, "rev-parse", f"{BASELINE_TAG}^{{commit}}").stdout.strip()
    head = git(clone, "rev-parse", "HEAD").stdout.strip()
    if head != baseline:
        raise GraderError(f"fixture main {head[:8]} is not at baseline {baseline[:8]}")
    return baseline


def apply_plan(clone: Path, plan_name: str, extra_files: dict | None = None) -> None:
    src = PLAN_DIR / f"{plan_name}.md"
    if not src.is_file():
        raise GraderError(f"no plan fixture at {src}")
    (clone / "prompt_plan.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    for repo_path, fixture_name in (extra_files or {}).items():
        source = HERE / "fixtures" / "extras" / fixture_name
        if not source.is_file():
            raise GraderError(f"no extra fixture at {source}")
        dest = clone / repo_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        git(clone, "add", repo_path)
    git(clone, "add", "prompt_plan.md")
    committed = git(
        clone, "commit", "--quiet", "-m", f"chore: {plan_name} plan for this eval run", check=False
    )
    if committed.returncode != 0 and "nothing to commit" not in committed.stdout:
        raise GraderError(f"could not commit the scenario plan: {committed.stdout}")
    # deliberately not pushed: a run must tolerate an unpushed plan commit (C1)


# ---- driving the CLI --------------------------------------------------------


def build_cli_argv(scenario: Scenario, cli: str, cost_cap: Decimal) -> list[str]:
    """The working tree is loaded as the plugin, so `/adw:next` resolves to the sources
    under test and not to an installed copy. The scenario's cap is also the CLI's hard
    budget: a runaway run is stopped by the CLI, not only flagged after the fact."""
    return [
        cli,
        "-p",
        scenario.prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        MODEL,
        "--strict-mcp-config",
        "--plugin-dir",
        str(REPO_ROOT),
        "--max-budget-usd",
        f"{cost_cap:.2f}",
        "--allowedTools",
        *scenario.allowed_tools,
    ]


def checkout_sha(root: Path) -> str | None:
    """HEAD of the working tree loaded as the plugin, or None when it is not a git checkout;
    the shared record keys the plugin under test by this sha."""
    r = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def stream_events(raw: str) -> Iterator[dict]:
    """The JSON events of a stream-json capture, one per non-blank line. A line that
    does not parse is reported on stderr with its traceback and skipped, never
    swallowed: a truncated capture must leave a trace, not a quietly empty grade."""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            print(
                f"run_scenario: skipping a malformed stream line: {line[:200]!r}",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)


def session_evidence(raw: str, root: Path = REPO_ROOT) -> dict:
    """What the session's init event says about the plugin under test, so a recorded
    result proves on its own that the run loaded the working tree as the plugin: the
    CLI version, the plugin entry (name, path, source, version), and the plugin's
    commands and agents. Explicit Nones and empty lists when no init event was seen."""
    out: dict = {"claude_code_version": None, "plugin": None, "commands": [], "agents": []}
    prefix = f"{PLUGIN_NAME}:"
    for event in stream_events(raw):
        if event.get("type") != "system" or event.get("subtype") != "init":
            continue
        out["claude_code_version"] = event.get("claude_code_version")
        plugin = next(
            (p for p in event.get("plugins", []) if p.get("name") == PLUGIN_NAME), None
        )
        out["plugin"] = {**plugin, "gitCommitSha": checkout_sha(root)} if plugin else None
        out["commands"] = [c for c in event.get("slash_commands", []) if c.startswith(prefix)]
        out["agents"] = [a for a in event.get("agents", []) if a.startswith(prefix)]
        break
    return out


def parse_stream(raw: str) -> tuple[str, Decimal | None, bool]:
    """(all text, total_cost_usd, is_error) from a stream-json capture."""
    texts: list[str] = []
    cost: Decimal | None = None
    is_error = False
    for event in stream_events(raw):
        if event.get("type") == "result":
            if isinstance(event.get("result"), str):
                texts.append(event["result"])
            if isinstance(event.get("total_cost_usd"), (int, float)):
                cost = _usd(event["total_cost_usd"])
            is_error = bool(event.get("is_error", False))
        elif event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        texts.append(json.dumps(block.get("input", ""))[:4000])
        elif event.get("type") == "user":
            for block in event.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, str):
                        texts.append(content[:4000])
                    elif isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                texts.append(c.get("text", "")[:4000])
    return "\n".join(texts), cost, is_error


# ---- gathering state for the graders ----------------------------------------


def list_run_branches(clone: Path) -> list[str]:
    """Run branches by short ref name, pushed refs first.

    Read through for-each-ref rather than `git branch -a`, whose listing decorates a
    branch checked out in a worktree with `+` and the current one with `*`; a decorated
    name handed to `git show` is not a ref.
    """
    out = git(
        clone,
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/heads/next-auto/",
        "refs/remotes/origin/next-auto/",
    ).stdout
    branches = [ln.strip() for ln in out.splitlines() if ln.strip()]
    branches.sort(key=lambda b: (not b.startswith("origin/"), b))
    return branches


def gather(
    clone: Path,
    baseline: str,
    stream_text: str,
    start_sha: str = "",
    since: datetime | None = None,
) -> RunState:
    """`since` scopes the PR list to PRs created at or after the run started; anything
    older (a closed PR from a prior scenario, a merged probe PR) is fixture history, not
    something this run opened or merged."""
    git(clone, "fetch", "--quiet", "--prune", "origin")
    branches = list_run_branches(clone)

    prs = []
    listing = subprocess.run(
        ["gh", "pr", "list", "--repo", FIXTURE_REPO, "--state", "all", "--json",
         "number,state,mergedAt,labels,headRefName,createdAt"],
        capture_output=True,
        text=True,
    )
    if listing.returncode == 0 and listing.stdout.strip():
        prs = [p for p in json.loads(listing.stdout) if p.get("state") != "CLOSED"
               or p.get("mergedAt")]
        if since is not None:
            prs = [p for p in prs if datetime.fromisoformat(p["createdAt"]) >= since]

    plan_text = ""
    diff_files: list[str] = []
    commits = 0
    head_commits = 0
    if not branches:
        # attended: the plan and the commits live on the checked-out branch, and the plan
        # is read from disk because a decision stop sets blocked-decision and appends to
        # Decisions without a commit (commands/next.md, stop (c))
        plan_path = clone / "prompt_plan.md"
        plan_text = plan_path.read_text(encoding="utf-8") if plan_path.is_file() else ""
        if start_sha:
            log = git(clone, "log", "--oneline", f"{start_sha}..HEAD").stdout
            head_commits = len([c for c in log.splitlines() if c])
            # the gate_config grader reads this list in both modes; attended commits
            # land on the checked-out branch, so the diff is the start sha to HEAD
            diff_files = [
                f
                for f in git(clone, "diff", "--name-only", f"{start_sha}..HEAD").stdout.splitlines()
                if f.strip()
            ]
            # a decision stop leaves the step's edits uncommitted, so the dirty paths count
            # too; the gate_config grader must see a conftest.py edit made before the stop
            for ln in git(clone, "status", "--porcelain").stdout.splitlines():
                path = ln[3:].strip()
                if path and path not in diff_files:
                    diff_files.append(path)
    if branches:
        ref = branches[0]
        plan_text = git(clone, "show", f"{ref}:prompt_plan.md").stdout
        diff_files = [
            f for f in git(clone, "diff", "--name-only", f"{baseline}..{ref}").stdout.splitlines()
            if f.strip()
        ]
        commits = len(
            [
                c
                for c in git(clone, "log", "--oneline", f"{baseline}..{ref}").stdout.splitlines()
                if c
            ]
        )

    run_dirs = sorted((clone / RUN_SUBDIR).glob("*")) if (clone / RUN_SUBDIR).is_dir() else []
    summary_text = ""
    for d in run_dirs:
        s = d / "summary.md"
        if s.is_file():
            summary_text += s.read_text(encoding="utf-8")
    state_files = [str(p) for d in run_dirs for p in d.glob("state.json")]

    return RunState(
        clone=clone,
        baseline_sha=baseline,
        main_sha=git(clone, "rev-parse", "origin/main").stdout.strip(),
        run_branches=branches,
        prs=prs,
        plan_text=plan_text,
        summary_text=summary_text,
        state_files=state_files,
        stream_text=stream_text,
        branch_diff_files=diff_files,
        branch_commits=commits,
        start_sha=start_sha,
        head_commits=head_commits,
    )


# ---- one scenario -----------------------------------------------------------


def run_one(
    scenario: Scenario,
    clone: Path,
    *,
    cli: str,
    cost_cap: Decimal,
    timeout: int,
    raw_path: Path | None = None,
) -> dict:
    baseline = reset_fixture(clone)
    apply_plan(clone, scenario.plan, scenario.extra_files)
    # the run starts from baseline plus the plan commit; holdout graders compare to this
    start_sha = git(clone, "rev-parse", "HEAD").stdout.strip()

    original_url = git(clone, "remote", "get-url", "origin").stdout.strip()
    if scenario.origin_owner_override:
        git(
            clone,
            "remote",
            "set-url",
            "origin",
            "https://github.com/"
            f"{scenario.origin_owner_override}/{FIXTURE_REPO.rsplit('/', 1)[-1]}.git",
        )

    cost_cap = effective_cap(scenario, cost_cap)
    argv = build_cli_argv(scenario, cli, cost_cap)
    started = time.time()
    started_at = datetime.now(UTC)
    timed_out = False
    try:
        proc = subprocess.run(
            argv, cwd=str(clone), capture_output=True, text=True, timeout=timeout
        )
        raw = proc.stdout + proc.stderr
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        raw = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        exit_code = -1
        timed_out = True
    finally:
        if scenario.origin_owner_override:
            git(clone, "remote", "set-url", "origin", original_url)

    elapsed = round(time.time() - started, 1)
    if raw_path is not None:
        raw_path.write_text(raw, encoding="utf-8")
    stream_text, cost, is_error = parse_stream(raw)
    state = gather(clone, baseline, stream_text, start_sha, since=started_at)

    results = []
    for name in scenario.graders:
        fn = graders.GRADERS[name]
        args = scenario.grader_args.get(name, {})
        try:
            ok, detail = fn(state, args)
        except GraderError as exc:
            ok, detail = False, f"grader error: {exc}"
        results.append({"grader": name, "pass": ok, "detail": detail})

    over_cap = cost is not None and cost > cost_cap
    passed = all(r["pass"] for r in results) and not timed_out and not over_cap

    return {
        "scenario": scenario.name,
        "pass": passed,
        "total_cost_usd": None if cost is None else str(cost),
        "cost_cap_usd": str(cost_cap),
        "over_cost_cap": over_cap,
        "elapsed_s": elapsed,
        "timed_out": timed_out,
        "cli_exit": exit_code,
        "cli_is_error": is_error,
        "model": MODEL,
        "cli_argv": argv,
        "session": session_evidence(raw),
        "graders": results,
        "notes": scenario.notes,
        "ran_at": datetime.now(UTC).isoformat(),
        "raw_chars": len(raw),
    }


def run_trials(scenario: Scenario, clone: Path, runs: int, **kw) -> dict:
    """One scenario, `runs` independent trials: run_one resets the fixture at the top of
    every trial, so no trial sees another's branches or PRs. The scenario passes only when
    every trial passes; pass_k records that the scenario declared itself a guard."""
    trials = [run_one(scenario, clone, **kw) for _ in range(runs)]
    costs = [Decimal(t["total_cost_usd"]) for t in trials if t["total_cost_usd"] is not None]
    return {
        "scenario": scenario.name,
        "pass": all(t["pass"] for t in trials),
        "pass_k": scenario.consistency,
        "runs": runs,
        "total_cost_usd": str(sum(costs)) if costs else None,
        "cost_cap_usd": trials[0]["cost_cap_usd"],
        "model": MODEL,
        "session": trials[0]["session"],
        "notes": scenario.notes,
        "ran_at": datetime.now(UTC).isoformat(),
        "trials": trials,
    }


# ---- re-grading recorded results ---------------------------------------------


_MISMATCH_RE = re.compile(r"step (\d+): want (\S+), got (\S+)")


def _regrade_step_states(detail: str, want_args: dict) -> tuple[bool, str]:
    """Re-derive a plan_step_states verdict from its recorded mismatch detail against the
    scenario's current accepted states. The detail lists only the steps that mismatched
    at run time, so every other step already matched."""
    mismatches = _MISMATCH_RE.findall(detail)
    if not mismatches:
        return False, detail
    still_bad, accepted_now = [], []
    for num, _want, got in mismatches:
        want = want_args.get(num)
        accepted = [want] if isinstance(want, str) else list(want or [])
        if got in accepted:
            accepted_now.append(f"step {num} got {got}, accepted {'|'.join(accepted)}")
        else:
            still_bad.append(f"step {num}: want {'|'.join(accepted)}, got {got}")
    if still_bad:
        return False, "; ".join(still_bad)
    return True, "re-graded: " + "; ".join(accepted_now)


def regrade(
    summary_path: Path,
    *,
    scenario_dir: Path = SCENARIO_DIR,
    default_cap: Decimal = DEFAULT_COST_CAP_USD,
) -> Path:
    """Recompute a run summary's verdicts from its recorded grader outcomes and costs
    against the scenario files as they are now. Writes <stamp>-regrade.json beside the
    original and never edits it; each result carries its original verdict."""
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    scenarios = {}
    for p in sorted(scenario_dir.glob("*.json")):
        sc = Scenario.load(p)
        scenarios[sc.name] = sc
    results = []
    for r in summary["results"]:
        sc = scenarios[r["scenario"]]
        cap = effective_cap(sc, default_cap)
        trials_out = []
        for t in r.get("trials") or [r]:
            graders_out = []
            for g in t["graders"]:
                g2 = dict(g)
                if g["grader"] == "plan_step_states" and not g["pass"]:
                    g2["pass"], g2["detail"] = _regrade_step_states(
                        g["detail"], sc.grader_args.get("plan_step_states", {})
                    )
                graders_out.append(g2)
            cost = _usd(t.get("total_cost_usd"))
            over = cost is not None and cost > cap
            passed = all(g["pass"] for g in graders_out) and not t.get("timed_out") and not over
            trials_out.append(
                {**t, "pass": passed, "cost_cap_usd": str(cap), "over_cost_cap": over,
                 "graders": graders_out}
            )
        # a summary written before trials existed is one trial; its fields stay flat
        regraded = {**r, **({"trials": trials_out} if "trials" in r else trials_out[0])}
        regraded["original_pass"] = r["pass"]
        regraded["pass"] = all(t["pass"] for t in trials_out)
        regraded["cost_cap_usd"] = str(cap)
        results.append(regraded)
    out = summary_path.with_name(summary_path.name.replace("-summary.json", "-regrade.json"))
    data = {
        "regraded_from": summary_path.name,
        "regraded_at": datetime.now(UTC).isoformat(),
        "rules": "current scenarios/*.json caps and accepted step states",
        "model": summary.get("model"),
        "scenarios": len(results),
        "passed": sum(1 for r in results if r["pass"]),
        "total_cost_usd": summary.get("total_cost_usd"),
        "results": results,
    }
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_scenario.py")
    ap.add_argument("scenarios", nargs="*", help="scenario names; default is all")
    ap.add_argument("--cli", default="claude")
    ap.add_argument("--cost-cap", type=Decimal, default=DEFAULT_COST_CAP_USD)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--workdir", default=str(DEFAULT_WORKDIR))
    ap.add_argument("--list", action="store_true", help="list scenarios and exit")
    ap.add_argument("--reset-only", action="store_true", help="reset the fixture and exit")
    ap.add_argument(
        "--regrade", metavar="SUMMARY_JSON",
        help="recompute a recorded summary against the current scenario rules and exit",
    )
    ap.add_argument("--runs", type=int, default=1, help="independent trials per scenario")
    ap.add_argument("--record", action="store_true", help="append the shared d6-record ledger")
    ap.add_argument("--ledger", default=None, help="ledger path (default evals/ledger.jsonl)")
    ap.add_argument(
        "--canary", action="store_true", help="alias for refuse-foreign-owner --runs 1"
    )
    args = ap.parse_args(argv)
    if args.runs < 1:
        ap.error("--runs must be at least 1")
    if not FIXTURE_REPO and not (args.list or args.regrade):
        print(
            f"{FIXTURE_REPO_VAR} is unset; name your fixture repository as owner/name",
            file=sys.stderr,
        )
        return 2
    if args.canary:
        args.scenarios, args.runs = ["refuse-foreign-owner"], 1

    if args.regrade:
        out = regrade(Path(args.regrade), default_cap=args.cost_cap)
        data = json.loads(out.read_text(encoding="utf-8"))
        for r in data["results"]:
            flip = ""
            if r["pass"] != r["original_pass"]:
                flip = "  (was FAIL)" if r["pass"] else "  (was PASS)"
            verdict = "PASS" if r["pass"] else "FAIL"
            print(
                f"{verdict}  {r['scenario']:20} cost=${_usd(r['total_cost_usd']):.4f} "
                f"cap=${_usd(r['cost_cap_usd']):.2f}{flip}"
            )
        print(f"\n{data['passed']}/{data['scenarios']} scenarios pass after re-grade -> {out.name}")
        return 0 if data["passed"] == data["scenarios"] else 1

    paths = sorted(SCENARIO_DIR.glob("*.json"))
    if args.list:
        for p in paths:
            s = Scenario.load(p)
            print(f"{s.name:24} plan={s.plan:20} graders={len(s.graders)}")
        return 0

    wanted = set(args.scenarios)
    chosen = [p for p in paths if not wanted or Scenario.load(p).name in wanted]
    if wanted and len(chosen) != len(wanted):
        found = {Scenario.load(p).name for p in chosen}
        print(f"unknown scenarios: {sorted(wanted - found)}", file=sys.stderr)
        return 2

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    workdir = Path(args.workdir)

    with run_lock():
        clone = ensure_clone(workdir)
        if args.reset_only:
            reset_fixture(clone)
            print("fixture reset to baseline")
            return 0

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        all_results = []
        for path in chosen:
            scenario = Scenario.load(path)
            print(f"=== {scenario.name} ===", flush=True)
            raw_path = RESULTS_DIR / f"{stamp}-{scenario.name}.raw.jsonl"
            result = run_trials(
                scenario,
                clone,
                args.runs,
                cli=args.cli,
                cost_cap=args.cost_cap,
                timeout=args.timeout,
                raw_path=raw_path,
            )
            result["raw_path"] = raw_path.name
            all_results.append(result)
            verdict = "PASS" if result["pass"] else "FAIL"
            cost = _usd(result["total_cost_usd"])
            cost_s = f"${cost:.4f}" if cost is not None else "n/a"
            print(f"{verdict}  {scenario.name}  runs={args.runs}  cost={cost_s}", flush=True)
            for i, trial in enumerate(result["trials"]):
                t_verdict = "PASS" if trial["pass"] else "FAIL"
                print(f"  trial {i}: {t_verdict}  {trial['elapsed_s']}s", flush=True)
                for r in trial["graders"]:
                    mark = "ok  " if r["pass"] else "FAIL"
                    print(f"    {mark} {r['grader']}: {r['detail']}", flush=True)
            (RESULTS_DIR / f"{stamp}-{scenario.name}.json").write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )

        total = sum((_usd(r["total_cost_usd"]) or Decimal(0) for r in all_results), Decimal(0))
        passed = sum(1 for r in all_results if r["pass"])
        summary = {
            "ran_at": stamp,
            "model": MODEL,
            "scenarios": len(all_results),
            "passed": passed,
            "total_cost_usd": str(total),
            "results": all_results,
        }
        summary_path = RESULTS_DIR / f"{stamp}-summary.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"\n{passed}/{len(all_results)} scenarios pass, total ${total:.4f}")
        if args.record:
            ledger = Path(args.ledger) if args.ledger else REPO_ROOT / "evals" / "ledger.jsonl"
            records = ledger_record.from_next_auto(summary_path)
            ledger_record.append(ledger, records)
            sys.stdout.write(ledger_record.render_markdown(records))
        return 0 if passed == len(all_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
