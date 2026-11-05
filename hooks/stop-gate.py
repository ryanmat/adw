#!/usr/bin/env python3
# Description: Stop hook for /adw:next (ai-dlc-record R14): an attended turn with an open step and
# Description: changes beyond the plan may end only on a green Test, three blocks per failure.

from __future__ import annotations

import sys

if sys.version_info < (3, 12):  # noqa: UP036  (the guard runs below the floor by design)
    sys.stderr.write(
        f"adw: python3 is {sys.version_info[0]}.{sys.version_info[1]}; "
        "the plugin needs 3.12 or later on PATH\n"
    )
    sys.exit(0)

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

PLAN_NAME = "prompt_plan.md"
GATE_FILE = "stop-gate.json"
TAIL_LINES = 40
# blocks on one normalized failure before the next stop on it is allowed (D5)
BUDGET = 3
# seconds a Test may run before its process group is killed, below the 600 s hooks.json gives
# the hook, so the hook and not the host ends a hung suite
TEST_LIMIT = 540
# a node test runner TAP failure line: the reporter prints it where its test ran, so a red
# run's last lines are passing tests and a summary
NOT_OK = re.compile(r"^\s*not ok \d+")
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def work_tree(cwd: str) -> tuple[Path, Path] | None:
    """(the work tree's top level, the main repository root) for cwd, or None when cwd is
    not inside a git work tree. One git call; nothing is written."""
    r = subprocess.run(
        ["git", "-C", cwd, "rev-parse", "--show-toplevel", "--path-format=absolute",
         "--git-common-dir"],
        capture_output=True,
        text=True,
    )  # fmt: skip
    lines = r.stdout.splitlines()
    if r.returncode != 0 or len(lines) != 2:
        return None
    return Path(lines[0]), Path(lines[1]).parent


def run_worktree(top: Path, run_subdir: str) -> bool:
    """True when top is a live run's worktree, <checkout>/.next-auto/<run-id>/wt, the tree
    start adds beside the run's marker."""
    return (
        top.name == "wt"
        and top.parent.parent.name == run_subdir
        and (top.parent / "state.json").is_file()
    )


# R14's normalization, applied after the temporary path segment, in this order: UUIDs, dates,
# times of day, long hex runs, then digit runs not preceded by a letter or an underscore, so an
# identifier keeps its digits
NOISE = [
    re.compile(p)
    for p in (
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}",
        r"[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?(?:[.,][0-9]+)?",
        r"[0-9a-fA-F]{7,}",
        r"(?<![A-Za-z_0-9])[0-9]+",
    )
]


def temp_segment() -> re.Pattern:
    """The first path segment under the temporary directory the Test inherits and under /tmp,
    where a mkdtemp name such as manifest-org-JtWV65 carries random characters after a letter,
    which the digit rule keeps. The longer root goes first, so a TMPDIR under /tmp matches
    whole. A root counts only where it begins a path, after no letter, digit, underscore or
    dot, so a repository's own tmp/ and /var/tmp keep their names and a file:/// URL still
    matches."""
    roots = sorted({tempfile.gettempdir().rstrip("/"), "/tmp"}, key=len, reverse=True)
    alternatives = "|".join(re.escape(r) for r in roots if r)
    return re.compile(r"(?<![\w.])(" + alternatives + r")/[^/\s'\"`]+")


def failure_hash(tail: list[str]) -> str:
    """R14's normalized failure: the tail with the temporary path segment and the NOISE
    patterns removed and whitespace collapsed, so temporary names, durations, timestamps and
    ids do not make one failure look like two, and test_step_1 and test_step_4 stay two
    failures."""
    text = temp_segment().sub(r"\1/", "\n".join(tail))
    for pattern in NOISE:
        text = pattern.sub("", text)
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    found = work_tree(payload.get("cwd") or ".")
    if found is None:
        return 0
    top, repo_root = found
    # the payload's tree alone: a tree with no plan, or no live check record and no IN PROGRESS
    # step, stands the hook down, and no other work tree of the repository is read (F-426)
    verdict, engine, record = judge(top)
    if verdict == GATE:
        return gate(top, repo_root, engine, record)
    return 0


# what judge makes of the work tree: gate its Test, or allow the stop
GATE, HOLD = "gate", "hold"


def judge(top: Path) -> tuple[str, object, tuple[list[str], str] | None]:
    """R14's rules over the work tree, as (verdict, the engine module, the plan's Record): GATE
    when an open step is live with changes beyond the plan and the record and the tree is not
    the one a live record's check N passed; HOLD otherwise, the tree with no plan, or with no
    live check record and no IN PROGRESS step, among them."""
    if not (top / PLAN_NAME).is_file():
        return HOLD, None, None
    # the engine's own helpers, loaded only once there is a plan to read
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import next_auto
    import next_plan

    # a run stands down only its own tree: the checkout it was started from keeps its marker
    # under .next-auto/, and its worktree sits beside the marker; a run live from another
    # worktree of the repository stands nothing down here (R14, rows A1 to A3)
    if next_auto.find_state_file(top) is not None or run_worktree(top, next_auto.RUN_SUBDIR):
        return HOLD, next_auto, None
    plan = next_plan.parse_plan((top / PLAN_NAME).read_text(encoding="utf-8"))
    record = next_auto.plan_record(plan)
    head = subprocess.run(
        ["git", "-C", str(top), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    steps = {s.number: s for s in plan.steps}
    live = False
    # the trees the live records' passes vouch for
    vouched = set()
    # each check record against the step it names: check begin N writes step-N.json and
    # commit N removes it, so what is left says how the step ended (R14's states table)
    for path in next_auto.attended_dir(top).glob("step-*.json"):
        number = path.stem[len("step-") :]
        if not number.isdigit():
            continue
        rec = json.loads(path.read_text(encoding="utf-8"))
        step = steps.get(int(number))
        # stale first, whatever the failure count: the step is gone or set back to PENDING,
        # or DONE and committed outside commit N, which would have removed the record
        if step is None or step.state == "PENDING":
            continue
        # a DONE record off its base whose check N passed at this HEAD is the step's own
        # commit made part way through it, with commit N still to run
        if (
            step.state == "DONE"
            and rec.get("base_head") != head
            and rec.get("passed_head") != head
        ):
            continue
        # at two failures, or at a step the loop marked BLOCKED or FAILED, the loop stopped
        if int(rec.get("failures", 0)) >= 2 or step.state in ("BLOCKED", "FAILED"):
            return HOLD, next_auto, record
        # live: IN PROGRESS, or DONE at its base, as in the boundary fix round and after
        # commit N refused, whatever the plan's first open step is
        live = True
        if rec.get("passed_tree"):
            vouched.add(rec["passed_tree"])
    # with no live record only a step the loop marked started is gated: a step not yet begun,
    # a BLOCKED or FAILED step, a finished plan, and another tool's plan, which never marks
    # one, are not
    open_step = next((s for s in plan.steps if s.state != "DONE"), None)
    if not live and (open_step is None or open_step.state != "IN PROGRESS"):
        return HOLD, next_auto, record
    own = {PLAN_NAME, *([next_auto.record_path(record[1])] if record else [])}
    status = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(top), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if not [ln for ln in status.stdout.splitlines() if ln.strip() and ln[3:].strip() not in own]:
        return HOLD, next_auto, record
    if vouched and checked_tree(top, next_auto) in vouched:
        # the tree a green check N vouched for, the plan aside: its Test already passed here, so
        # the stop clears the gate file as a green Test does
        (next_auto.attended_dir(top) / GATE_FILE).unlink(missing_ok=True)
        return HOLD, next_auto, record
    return GATE, next_auto, record


def checked_tree(top: Path, next_auto) -> str | None:
    """The tree's hash as check N takes it, or None when git cannot stage it, said on stderr,
    and the Test runs as it would with no record to compare."""
    try:
        return next_auto.tree_hash(top, PLAN_NAME)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"stop-gate: cannot hash the tree, so the Test runs: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return None


def gate(top: Path, repo_root: Path, next_auto, record) -> int:
    """Run the plan's Test: green passes and clears the gate file; red blocks with the tail,
    until the same failure has been blocked BUDGET times, and the next stop on it passes."""
    test_cmd = next_auto.plan_commands(top, PLAN_NAME)[0]
    if not test_cmd:
        # a plan with no Test, another tool's prompt_plan.md among them, has nothing to gate
        return 0
    gate_path = next_auto.attended_dir(top) / GATE_FILE
    try:
        # one stream, so the tail is the output in the order the Test wrote it; a process
        # group of its own, so a hung Test and every child holding its pipe end together
        proc = subprocess.Popen(
            test_cmd, cwd=str(top), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True, env=next_auto.test_env(test_cmd),
        )  # fmt: skip
    except OSError as exc:
        # exit 1: the host shows stderr for it, never for exit 0, and it never blocks
        print(f"stop-gate: the plan's Test cannot run, so the turn may end: {exc}", file=sys.stderr)
        return 1
    with proc:
        try:
            out, _ = proc.communicate(timeout=TEST_LIMIT)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            print(
                f"stop-gate: the plan's Test ran past {TEST_LIMIT} s and was stopped, "
                "so the turn may end",
                file=sys.stderr,
            )
            return 1
    if proc.returncode == 0:
        gate_path.unlink(missing_ok=True)
        return 0
    lines = out.decode("utf-8", errors="replace").splitlines()
    # node's TAP failure lines ahead of the last lines, so the tail names what failed
    tail = [ln for ln in lines[:-TAIL_LINES] if NOT_OK.match(ln)] + lines[-TAIL_LINES:]
    digest = failure_hash(tail)
    seen = read_gate(gate_path)
    same = seen.get("hash") == digest
    if same and seen["count"] >= BUDGET:
        return overridden(top, repo_root, next_auto, record, gate_path, seen)
    next_auto.ensure_ignored(top, next_auto.RUN_SUBDIR + "/")
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "gate": "stop",
        "failure": digest[:8],
        "hash": digest,
        "count": seen["count"] + 1 if same else 1,
        "overridden": False,
    }
    gate_path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    print("\n".join(tail), file=sys.stderr)
    # a line on stdout: the host reads a Stop exit 2 with empty stdout and a stderr naming a
    # missing file as a missing hook script and lets the turn end
    count, failure = body["count"], body["failure"]
    print(f"stop gate: the plan's Test is red, block {count} of {BUDGET} on failure {failure}")
    return 2


GATE_SHAPE = {"hash": str, "failure": str, "count": int, "overridden": bool}


def read_gate(gate_path: Path) -> dict:
    """The gate file's hash, failure, count and overridden flag, or nothing when there is none.
    A file that is not a JSON object with those keys and types starts the count again, said on
    stderr."""
    if not gate_path.is_file():
        return {}
    try:
        seen = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"stop-gate: unreadable gate file, counting again: {exc}", file=sys.stderr)
        return {}
    wrong = (
        [k for k, t in GATE_SHAPE.items() if type(seen.get(k)) is not t]
        if isinstance(seen, dict)
        else list(GATE_SHAPE)
    )
    if wrong:
        detail = ", ".join(wrong)
        print(f"stop-gate: unreadable gate file, counting again: {detail}", file=sys.stderr)
        return {}
    return seen


def overridden(top: Path, repo_root: Path, next_auto, record, gate_path: Path, seen: dict) -> int:
    """The stop after BUDGET blocks on one failure is allowed. With Record, gate.overridden is
    appended once per failure, through the appender committed on the default branch; its
    failure is reported and never blocks the stop, and a later stop tries again. The line is
    the hook's own, as commit N's step.done is, so a check record that matched the record before
    the append takes the record as it now stands, hash and snapshot, and the step's next check N
    does not refuse it (R12); a record the step itself edited keeps its refusal."""
    print(f"stop gate overridden for failure {seen['failure']} after three blocks")
    if record is None or seen.get("overridden"):
        return 0
    record_file = next_auto.record_path(record[1])
    current = top / record_file
    old = next_auto.sha256_bytes(current.read_bytes()) if current.is_file() else None
    flags = ("--evidence", str(gate_path))
    r = next_auto.run_record(repo_root, top, record, "append", "gate.overridden", *flags)
    if r.returncode != 0:
        detail = next_auto._record_detail(r)
        print(f"stop-gate: gate.overridden append {detail}", file=sys.stderr)
        return 0
    gate_path.write_text(json.dumps({**seen, "overridden": True}, indent=2), encoding="utf-8")
    after = current.read_bytes()
    for path in next_auto.attended_dir(top).glob("step-*.json"):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("record_file") != record_file or rec.get("record_sha256") != old:
            continue
        rec["record_sha256"] = next_auto.sha256_bytes(after)
        path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        (path.parent / f"record-snapshot-{path.stem[len('step-') :]}.md").write_bytes(after)
    return 0


def run() -> int:
    """The hook's exit code: main's, or 1 for any error, since the hook runs at every turn end
    on the machine and must never block on its own failure."""
    try:
        return main()
    except Exception as exc:
        print(f"stop-gate: cannot gate this stop, so the turn may end: {exc!r}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        # exit 1: the host shows stderr for it, never for exit 0, and it never blocks
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
