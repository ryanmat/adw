# Description: Tests for hooks/stop-gate.py, the Stop hook (ai-dlc-record R14): exec it with the
# Description: Stop JSON on stdin against real git repos and assert exit codes, output and files.

import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import next_plan
import pytest
from test_next_auto import (
    ATTENDED_SESSION_ID,
    LINT_OK,
    RECORD_FILE,
    RECORD_PLAN,
    RECORD_SLUG,
    TEST_CMD,
    appends,
    attended_pass,
    fix_step,
    git,
    make_repo,
    record_repo,
    set_done,
    stub_rc,
)


@pytest.fixture(autouse=True)
def session_id(monkeypatch):
    """The run key every commit here records, fixed as test_next_auto.py fixes it."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ATTENDED_SESSION_ID)


HOOK = Path(__file__).resolve().parents[1] / "hooks" / "stop-gate.py"
GATE_PLAN = RECORD_PLAN.replace("- Test: uv run pytest", "- Test: sh suite.sh")
PLAIN_PLAN = "\n".join(ln for ln in GATE_PLAN.splitlines() if not ln.startswith("- Record:")) + "\n"
SUITE = """\
# Description: the stop gate tests' suite: prints the output its test put in the control
# Description: directory and exits with the code held there.
cat "@CONTROL@/suite-out"
exit "$(cat "@CONTROL@/suite-rc")"
"""


def stop(cwd):
    payload = {
        "session_id": "s1",
        "transcript_path": "/dev/null",
        "cwd": str(cwd),
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }
    return subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True,
        cwd=str(cwd),
    )  # fmt: skip


def suite(control, rc, out):
    (control / "suite-rc").write_text(str(rc))
    (control / "suite-out").write_text(out)


def gate_repo(tmp_path, plan_text=GATE_PLAN):
    """The record fixture repo with a plan whose Test is a suite the test controls, green,
    and step 1 marked started, as the attended loop's item 3 leaves it."""
    repo, control = record_repo(tmp_path, plan_text=plan_text)
    (repo / "suite.sh").write_text(SUITE.replace("@CONTROL@", str(control)))
    git(repo, "add", "suite.sh")
    git(repo, "commit", "-qm", "the controlled suite")
    suite(control, 0, "1 passed\n")
    set_states(repo, ("in-progress", ""))
    return repo, control


def dirty(repo):
    (repo / "src.py").write_text("# Description: fixture\n# Description: two\nX = 2\n")


def set_states(repo, *states):
    plan = repo / "prompt_plan.md"
    for n, (state, note) in enumerate(states, start=1):
        plan.write_bytes(next_plan.set_status_bytes(plan.read_bytes(), n, state, note))


def files_of(tree):
    return {
        str(p.relative_to(tree)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in tree.rglob("*")
        if p.is_file()
    }


def gate_file(repo):
    return repo / ".next-auto" / "attended" / "stop-gate.json"


def assert_stood_down(result, before, tree):
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert files_of(tree) == before


# -- stand-down: fast, silent, and writing nothing -----------------------------------------


def test_r14_stands_down_outside_a_git_work_tree(tmp_path):
    """ai-dlc-record R14: the hook runs at every turn end of every session on the machine,
    the Acme workspace root among them, which is no git repository."""
    (tmp_path / "notes.txt").write_text("not a repo\n")
    before = files_of(tmp_path)
    assert_stood_down(stop(tmp_path), before, tmp_path)


def test_r14_stands_down_without_a_plan_at_the_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "work.py").write_text("x = 1\n")
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)


def test_r14_stands_down_while_a_run_is_live(tmp_path):
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "1 failed\n")
    marker = repo / ".next-auto" / "20260924-run001" / "state.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}")
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)


def test_r14_stands_down_when_only_the_plan_and_the_record_changed(tmp_path):
    repo, control = gate_repo(tmp_path)
    suite(control, 1, "1 failed\n")
    set_states(repo, ("in-progress", ""))
    with (repo / RECORD_FILE).open("a") as fh:
        fh.write("- a pending line\n")
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)


@pytest.mark.parametrize(
    "states",
    [
        [("done", "a"), ("done", "b")],
        [("done", "a"), ("blocked-decision", "which store?")],
        [("failed", "red twice"), ("pending", "")],
        [("pending", ""), ("pending", "")],
        [("done", "a"), ("pending", "")],
    ],
    ids=[
        "every-step-done", "first-open-step-blocked", "first-open-step-failed",
        "first-step-not-begun", "next-step-not-begun",
    ],
)  # fmt: skip
def test_r14_stands_down_where_the_loop_itself_stopped(tmp_path, states):
    """ai-dlc-record R14: every step done, or the first step not done is not IN PROGRESS:
    BLOCKED, FAILED, or not yet begun, as at the loop's stop on a dirty tree."""
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "1 failed\n")
    set_states(repo, *states)
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)


@pytest.mark.parametrize("done_first", [False, True], ids=["open-step", "fix-round"])
def test_r14_stands_down_on_a_check_record_at_two_failures(tmp_path, done_first):
    """ai-dlc-record R14: a check record at two failures is the loop's own stop, whether it
    belongs to the first open step or to a committed step in its fix round."""
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "1 failed\n")
    if done_first:
        set_states(repo, ("done", "a"))
    record = repo / ".next-auto" / "attended" / "step-1.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"failures": 2}))
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)


# -- red, green, and the budget ------------------------------------------------------------


def test_r14_a_red_run_blocks_with_the_last_40_lines_on_stderr(tmp_path):
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "".join(f"row-{i:02d} failed\n" for i in range(1, 51)))
    r = stop(repo)
    assert r.returncode == 2
    assert "row-11 failed" in r.stderr and "row-50 failed" in r.stderr
    assert "row-10 failed" not in r.stderr
    data = json.loads(gate_file(repo).read_text())
    assert data["gate"] == "stop" and data["count"] == 1
    assert re.fullmatch(r"[0-9a-f]{8}", data["failure"])
    assert data["hash"].startswith(data["failure"])
    assert git(repo, "check-ignore", "-q", ".next-auto/probe", check=False).returncode == 0


def test_r14_the_failure_hash_follows_the_normalization_rule(tmp_path):
    """ai-dlc-record R14: SHA-256 of the last 40 lines with every UUID, date, time of day, run
    of seven or more hex digits and run of decimal digits not preceded by a letter or an
    underscore removed, in that order, and whitespace collapsed to one space."""
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    out = "FAILED tests/test_a.py::test_x in 0.52s  at 3f9a2b1c7d\n  12 errors\n"
    suite(control, 1, out)
    assert stop(repo).returncode == 2
    text = "\n".join(out.splitlines()[-40:])
    for pattern in (
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}",
        r"[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?(?:[.,][0-9]+)?",
        r"[0-9a-fA-F]{7,}",
        r"(?<![A-Za-z_0-9])[0-9]+",
    ):
        text = re.sub(pattern, "", text)
    want = hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()
    assert json.loads(gate_file(repo).read_text())["hash"] == want


def test_r14_output_differing_only_in_durations_and_hex_ids_is_the_same_failure(tmp_path):
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "FAILED test_x in 0.52s at commit 3f9a2b1c7d\n")
    assert stop(repo).returncode == 2
    first = json.loads(gate_file(repo).read_text())
    suite(control, 1, "FAILED test_x in 11.07s at commit 9e8d7c6b5a4\n")
    assert stop(repo).returncode == 2
    second = json.loads(gate_file(repo).read_text())
    assert second["hash"] == first["hash"] and second["count"] == 2


def test_r14_a_different_failure_resets_the_count(tmp_path):
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "FAILED test_x\n")
    assert stop(repo).returncode == 2
    assert stop(repo).returncode == 2
    suite(control, 1, "FAILED test_y\n")
    assert stop(repo).returncode == 2
    assert json.loads(gate_file(repo).read_text())["count"] == 1


def test_r14_the_fourth_stop_on_the_same_failure_is_allowed_and_recorded_once(tmp_path):
    """ai-dlc-record R14, R17: three blocks, then the fourth stop is allowed with one line and
    one gate.overridden append, the slug after the subcommand and the gate file as evidence;
    a fifth stop on the same failure is allowed and appends nothing."""
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "FAILED test_x\n")
    assert [stop(repo).returncode for _ in range(3)] == [2, 2, 2]
    r = stop(repo)
    data = json.loads(gate_file(repo).read_text())
    assert r.returncode == 0
    assert r.stdout.splitlines() == [
        f"stop gate overridden for failure {data['failure']} after three blocks"
    ]
    [call] = appends(control)
    evidence = str(gate_file(repo))
    assert call["argv"] == ["append", RECORD_SLUG, "gate.overridden", "--evidence", evidence]
    assert call["evidence"]["gate"] == "stop" and call["evidence"]["failure"] == data["failure"]
    assert Path(call["cwd"]) == repo.resolve()
    assert stop(repo).returncode == 0
    assert len(appends(control)) == 1


def test_r14_an_appender_failure_never_blocks_the_allowed_stop(tmp_path):
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "FAILED test_x\n")
    for _ in range(3):
        stop(repo)
    stub_rc(control, "append", 1)
    r = stop(repo)
    assert r.returncode == 0
    assert "gate.overridden" in r.stderr and "stub append refused" in r.stderr


def test_r14_without_record_the_allowed_stop_appends_nothing(tmp_path):
    repo, control = gate_repo(tmp_path, plan_text=PLAIN_PLAN)
    dirty(repo)
    suite(control, 1, "FAILED test_x\n")
    assert [stop(repo).returncode for _ in range(4)] == [2, 2, 2, 0]
    assert appends(control) == []
    assert (repo / RECORD_FILE).read_text().count("- stub") == 0


def test_r14_a_green_run_passes_and_deletes_the_gate_file(tmp_path):
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "FAILED test_x\n")
    assert stop(repo).returncode == 2
    assert gate_file(repo).is_file()
    suite(control, 0, "2 passed\n")
    r = stop(repo)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert not gate_file(repo).exists()


def test_r14_outside_a_work_tree_it_stands_down_before_loading_the_engine(tmp_path):
    """ai-dlc-record R14: the hook runs at every turn end on the machine, so outside a work
    tree it stops at its first git call and loads nothing but the standard library. A copy of
    the hook with no scripts/ beside it proves it: reaching for the engine would fail."""
    alone = tmp_path / "plugin" / "hooks" / "stop-gate.py"
    alone.parent.mkdir(parents=True)
    alone.write_bytes(HOOK.read_bytes())
    cwd = tmp_path / "not-a-repo"
    cwd.mkdir()
    (cwd / "prompt_plan.md").write_text(GATE_PLAN)
    before = files_of(cwd)
    payload = json.dumps({"cwd": str(cwd), "hook_event_name": "Stop", "stop_hook_active": False})
    r = subprocess.run(
        [sys.executable, str(alone)], input=payload, capture_output=True, text=True, cwd=str(cwd)
    )
    assert_stood_down(r, before, cwd)


# -- review round 1: the gate during a step, inputs that must not crash it --------------------

LINT_TRUE = [sys.executable, "-c", "raise SystemExit(0)"]


@pytest.mark.parametrize("failures", [0, 1])
def test_r14_a_step_between_check_begin_and_commit_is_gated(tmp_path, failures):
    """ai-dlc-record R14: the state the gate exists for, a check record from check begin N at
    fewer than two failures, blocks a red stop; only two failures stand it down."""
    import next_auto

    repo, control = gate_repo(tmp_path)
    rc, line = next_auto.check_begin(repo, 1, test_cmd=["sh", "suite.sh"], lint_cmd=LINT_TRUE)
    assert rc == 0, line
    record = repo / ".next-auto" / "attended" / "step-1.json"
    data = json.loads(record.read_text())
    assert data["failures"] == 0
    if failures:
        record.write_text(json.dumps({**data, "failures": failures}))
    dirty(repo)
    suite(control, 1, "FAILED test_x\n")
    r = stop(repo)
    assert r.returncode == 2 and "FAILED test_x" in r.stderr


def test_r14_a_later_blocked_step_does_not_stand_the_gate_down(tmp_path):
    """ai-dlc-record R14: only the first step that is not done decides; a BLOCKED step further
    down the plan leaves the open step gated."""
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "FAILED test_x\n")
    set_states(repo, ("in-progress", ""), ("blocked-decision", "which store?"))
    assert stop(repo).returncode == 2


def test_r14_the_hash_reads_the_tail_only(tmp_path):
    """ai-dlc-record R14: output above the last 40 lines is outside the hash, so a random word
    there does not make one failure look like two."""
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    rows = "".join(f"FAILED row {i}\n" for i in range(60))
    suite(control, 1, "seed qrstuv\n" + rows)
    assert stop(repo).returncode == 2
    suite(control, 1, "seed wxyzqp\n" + rows)
    assert stop(repo).returncode == 2
    assert json.loads(gate_file(repo).read_text())["count"] == 2


def test_r14_the_tail_is_the_output_in_the_order_it_was_written(tmp_path):
    """ai-dlc-record R14: stdout and stderr share one stream, so a runner's progress lines on
    stderr do not push the failure summary it prints last out of the 40-line tail."""
    repo, _control = gate_repo(tmp_path)
    (repo / "suite.sh").write_text(
        'i=0; while [ "$i" -lt 50 ]; do echo "progress $i" >&2; i=$((i+1)); done\n'
        'echo "FAILED summary"; exit 1\n'
    )
    r = stop(repo)
    assert r.returncode == 2
    assert r.stderr.splitlines()[-1] == "FAILED summary"


def test_r14_interleaved_streams_keep_their_order_in_the_tail(tmp_path):
    repo, _control = gate_repo(tmp_path)
    (repo / "suite.sh").write_text(
        "for i in 1 2 3; do echo \"out $i\"; echo \"err $i\" >&2; done; exit 1\n"
    )
    r = stop(repo)
    assert r.returncode == 2
    assert r.stderr.splitlines() == ["out 1", "err 1", "out 2", "err 2", "out 3", "err 3"]


def test_r14_a_red_run_with_bytes_that_are_not_utf8_still_blocks(tmp_path):
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "")
    (control / "suite-out").write_bytes(b"FAILED test_x \xff\xfe\n")
    r = stop(repo)
    assert r.returncode == 2 and "FAILED test_x" in r.stderr
    assert json.loads(gate_file(repo).read_text())["count"] == 1


def test_r14_a_plan_with_no_test_stands_down(tmp_path):
    """A prompt_plan.md from another tool, or one with no Test line, has nothing to gate."""
    plan = "\n".join(ln for ln in GATE_PLAN.splitlines() if not ln.startswith("- Test:")) + "\n"
    repo, control = gate_repo(tmp_path, plan_text=plan)
    dirty(repo)
    suite(control, 1, "FAILED test_x\n")
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)


def test_r14_a_test_program_that_cannot_start_lets_the_turn_end_with_one_line(tmp_path):
    plan = GATE_PLAN.replace("- Test: sh suite.sh", "- Test: no-such-program-xyz-123")
    repo, _control = gate_repo(tmp_path, plan_text=plan)
    dirty(repo)
    r = stop(repo)
    assert r.returncode == 1 and r.stderr.count("\n") == 1
    assert r.stderr.startswith("stop-gate: the plan's Test cannot run, so the turn may end: ")


def run_hook(cwd, payload_text):
    return subprocess.run(
        [sys.executable, str(HOOK)], input=payload_text, capture_output=True, text=True,
        cwd=str(cwd),
    )  # fmt: skip


@pytest.mark.parametrize("case", ["unbalanced-quote", "plan-not-utf8", "stdin-not-json"])
def test_r14_an_input_the_hook_cannot_read_lets_the_turn_end_with_one_line(tmp_path, case):
    """The hook runs at every turn end on the machine: an input it cannot read ends the turn
    with exit 1, which the host shows, and a line naming the problem and the traceback on
    stderr; it never blocks."""
    plan = GATE_PLAN.replace("- Test: sh suite.sh", "- Test: sh 'suite.sh")
    repo, control = gate_repo(tmp_path, plan_text=plan if case == "unbalanced-quote" else GATE_PLAN)
    dirty(repo)
    suite(control, 1, "FAILED test_x\n")
    payload = json.dumps({"cwd": str(repo), "hook_event_name": "Stop"})
    if case == "plan-not-utf8":
        path = repo / "prompt_plan.md"
        path.write_bytes(path.read_bytes() + b"\n\xff\n")
    if case == "stdin-not-json":
        payload = "{not json"
    r = run_hook(repo, payload)
    assert r.returncode == 1, r.stderr
    assert r.stderr.startswith("stop-gate: cannot gate this stop, so the turn may end: ")
    assert "Traceback (most recent call last)" in r.stderr
    assert not gate_file(repo).exists()


def test_r14_an_unreadable_gate_file_starts_the_count_again(tmp_path):
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "FAILED test_x\n")
    gate_file(repo).parent.mkdir(parents=True)
    gate_file(repo).write_text("{not json")
    r = stop(repo)
    assert r.returncode == 2
    assert r.stderr.splitlines()[0].startswith("stop-gate: unreadable gate file, counting again: ")
    assert json.loads(gate_file(repo).read_text())["count"] == 1


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (
            "FAILED test_x id=3f2c1a9e-4b7d-4e2a-9c1f-0a1b2c3d4e5f\n",
            "FAILED test_x id=9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b\n",
        ),
        (
            "2026-09-24T01:30:37Z FAILED test_x\n",
            "2026-09-25T12:31:38.125Z FAILED test_x\n",
        ),
        (
            "FAILED test_x, see snap_2025-12-31.log\n",
            "FAILED test_x, see snap_2026-01-01.log\n",
        ),
    ],
    ids=["uuid", "timestamp", "dated-name"],
)
def test_r14_a_uuid_a_timestamp_or_a_date_does_not_split_one_failure(tmp_path, first, second):
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, first)
    assert stop(repo).returncode == 2
    suite(control, 1, second)
    assert stop(repo).returncode == 2
    assert json.loads(gate_file(repo).read_text())["count"] == 2


@pytest.mark.parametrize(
    ("first", "second"),
    [("test_step_1", "test_step_4"), ("test1", "test4"), ("test_step_12", "test_step_13")],
    ids=["after-underscore", "after-letter", "several-digits"],
)
def test_r14_failures_that_differ_in_an_identifiers_digits_are_two(tmp_path, first, second):
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, f"FAILED tests/test_a.py::{first}\n")
    assert stop(repo).returncode == 2
    suite(control, 1, f"FAILED tests/test_a.py::{second}\n")
    assert stop(repo).returncode == 2
    assert json.loads(gate_file(repo).read_text())["count"] == 1


# -- re-review: the fix round, the host's missing-script rule, the gate file's shape -------


@pytest.mark.parametrize("last", [False, True], ids=["next-step-pending", "every-step-done"])
def test_r14_a_committed_step_in_its_fix_round_is_gated(tmp_path, last):
    """ai-dlc-record R14: the boundary fix round re-begins a DONE step with check begin, so a
    check record below two failures gates it though no step is IN PROGRESS."""
    import next_auto

    repo, control = gate_repo(tmp_path)
    set_states(repo, ("done", "a"), ("done", "b") if last else ("pending", ""))
    rc, line = next_auto.check_begin(repo, 1, test_cmd=["sh", "suite.sh"], lint_cmd=LINT_TRUE)
    assert rc == 0, line
    dirty(repo)
    suite(control, 1, "FAILED test_fix\n")
    r = stop(repo)
    assert r.returncode == 2 and "FAILED test_fix" in r.stderr


def test_r14_a_block_always_prints_its_line_on_stdout(tmp_path):
    """ai-dlc-record R14: the host reads a Stop exit 2 with empty stdout and a stderr naming a
    missing file as a missing hook script and does not block; a red tail can say just that."""
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "FileNotFoundError: [Errno 2] No such file or directory: 'x.json'\n")
    r = stop(repo)
    data = json.loads(gate_file(repo).read_text())
    assert r.returncode == 2 and "No such file or directory" in r.stderr
    assert r.stdout.splitlines() == [
        f"stop gate: the plan's Test is red, block 1 of 3 on failure {data['failure']}"
    ]


@pytest.mark.parametrize(
    "change",
    [{"count": "two"}, {"failure": None}, {"overridden": "no"}],
    ids=["count-not-an-integer", "failure-missing", "overridden-not-a-boolean"],
)
def test_r14_a_gate_file_of_the_wrong_shape_starts_the_count_again(tmp_path, change):
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, "FAILED test_x\n")
    assert stop(repo).returncode == 2
    data = json.loads(gate_file(repo).read_text())
    data["count"] = 3
    data.update({k: v for k, v in change.items() if v is not None})
    for k in [k for k, v in change.items() if v is None]:
        del data[k]
    gate_file(repo).write_text(json.dumps(data))
    r = stop(repo)
    assert r.returncode == 2
    assert r.stderr.splitlines()[0].startswith("stop-gate: unreadable gate file, counting again: ")
    assert json.loads(gate_file(repo).read_text())["count"] == 1
    assert appends(control) == []


# -- 3.4.1: the states table in ai-dlc-record R14, one test per row that changed -------------


def check_begun(repo, n=1):
    import next_auto

    rc, line = next_auto.check_begin(repo, n, test_cmd=["sh", "suite.sh"], lint_cmd=LINT_TRUE)
    assert rc == 0, line
    return repo / ".next-auto" / "attended" / f"step-{n}.json"


def commit_by_hand(repo):
    """A step's work committed outside commit N, as a person does it: HEAD moves past the
    check record's base and the record stays."""
    dirty(repo)
    git(repo, "add", "src.py")
    git(repo, "commit", "-qm", "the step, committed by hand")


def more_work(repo):
    (repo / "later.py").write_text("# Description: fixture\n# Description: two\nY = 1\n")


@pytest.mark.parametrize(
    ("begin_states", "after_states"),
    [
        ([("in-progress", "")], [("done", "a"), ("pending", "")]),
        ([("in-progress", "")], [("done", "a"), ("done", "b")]),
        ([("done", "a"), ("pending", "")], [("done", "a"), ("pending", "")]),
    ],
    ids=["S3-H1-next-step-stop", "H7-finished-plan", "H3-fix-round-committed-by-hand"],
)
def test_r14_a_record_left_by_a_commit_made_by_hand_is_stale(tmp_path, begin_states, after_states):
    """ai-dlc-record R14, states table rows S3, H1, H3 and H7: a check record whose step is DONE
    and whose base is no longer HEAD was left by a commit made outside commit N; it gates
    nothing, so the loop's stop on a dirty tree and every later stop in a finished plan stand
    down."""
    repo, control = gate_repo(tmp_path)
    set_states(repo, *begin_states)
    record = check_begun(repo)
    commit_by_hand(repo)
    set_states(repo, *after_states)
    more_work(repo)
    suite(control, 1, "FAILED test_unrelated\n")
    assert record.is_file()
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)


def test_r14_a_step_committed_by_hand_while_in_progress_is_still_gated(tmp_path):
    """ai-dlc-record R14, row H2: the plan still says the step is open, so its record is live
    though HEAD moved."""
    repo, control = gate_repo(tmp_path)
    check_begun(repo)
    commit_by_hand(repo)
    more_work(repo)
    suite(control, 1, "FAILED test_x\n")
    r = stop(repo)
    assert r.returncode == 2 and "FAILED test_x" in r.stderr


def test_r14_a_done_step_whose_record_is_at_head_is_still_gated(tmp_path):
    """ai-dlc-record R14, rows S10, S11 and H8: set done and not yet committed, or its commit
    refused, the base is still HEAD and the record is live."""
    repo, control = gate_repo(tmp_path)
    check_begun(repo)
    dirty(repo)
    set_states(repo, ("done", "a"), ("pending", ""))
    suite(control, 1, "FAILED test_x\n")
    assert stop(repo).returncode == 2


def pytest_repo(tmp_path):
    """make_repo's fixture with the plan's Test the real suite, so check N passes for real and
    the hook runs that suite on the turn it judges."""
    repo, _ = make_repo(tmp_path)
    plan = repo / "prompt_plan.md"
    plan.write_text(
        plan.read_text().replace("- Test: uv run pytest", f"- Test: {shlex.join(TEST_CMD)}")
    )
    git(repo, "commit", "-qam", "the plan's Test is the real suite")
    set_states(repo, ("in-progress", ""))
    return repo


def feature(repo, value):
    (repo / "src.py").write_text(
        f"# Description: fixture\n# Description: two\nX = 1\nFEATURE = {value}\n"
    )


def red_commit_then_pass(repo):
    """The step's red test committed alone part way through the step, the fix left in the tree,
    and a real check N pass over both."""
    import next_auto

    rc, line = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0, line
    (repo / "tests" / "test_feature.py").write_text(
        "from src import FEATURE\n\n\ndef test_feature():\n    assert FEATURE == 42\n"
    )
    git(repo, "add", "tests/test_feature.py")
    git(repo, "commit", "-qm", "red: the step's test")
    feature(repo, 42)
    assert next_auto.check_step(repo, 1) == (0, ["CHECK PASS step 1"])


PASSED_STATES = [[("done", "a"), ("pending", "")], [("done", "a"), ("done", "b")]]


@pytest.mark.parametrize("states", PASSED_STATES, ids=["S10-next-step-pending", "S11-finished"])
def test_r14_s10_s11_a_passed_step_with_a_commit_made_during_it_is_still_gated(tmp_path, states):
    """ai-dlc-record R14, rows S10 and S11 with a commit made during the step: the commit moves
    HEAD off the record's base; while HEAD is still the one check N passed at, commit N is
    still to run and the record is live."""
    repo = pytest_repo(tmp_path)
    red_commit_then_pass(repo)
    set_states(repo, *states)
    feature(repo, 41)
    r = stop(repo)
    assert r.returncode == 2 and "test_feature" in r.stderr


@pytest.mark.parametrize("states", PASSED_STATES, ids=["S10-next-step-pending", "S11-finished"])
def test_r14_l6_a_passed_step_whose_commit_did_not_land_stands_down_once_marked(tmp_path, states):
    """ai-dlc-record R14, row L6 with a commit made during the step: an earlier session set the
    step DONE after its pass and commit N did not land, so the record's passed_head is HEAD
    and its base is behind; item 1 reads that as the step's earlier work, marks it stop (c),
    and the hook stands down until the resume sets it in progress."""
    repo = pytest_repo(tmp_path)
    red_commit_then_pass(repo)
    set_states(repo, *states)
    feature(repo, 41)
    assert stop(repo).returncode == 2
    resumed(repo, [], 1, "test_feature")
    assert "has its `base_head` or its `passed_head` at HEAD" in item_1()


@pytest.mark.parametrize("extra", [False, True], ids=["the-rest", "the-rest-and-an-edit"])
@pytest.mark.parametrize("states", PASSED_STATES, ids=["S3-next-step-pending", "H7-finished"])
def test_r14_s3_h7_a_passed_step_then_committed_by_hand_is_stale(tmp_path, extra, states):
    """ai-dlc-record R14, rows S3 and H7 after a pass: the step committed by hand, with or
    without an edit of its own, moves HEAD off the commit the pass saw, so the record was left
    by a commit outside commit N."""
    repo = pytest_repo(tmp_path)
    red_commit_then_pass(repo)
    if extra:
        (repo / "notes.md").write_text("an edit the pass never saw\n")
    git(repo, "add", "-A", "--", ".", ":!prompt_plan.md")
    git(repo, "commit", "-qm", "the rest of the step, committed by hand")
    set_states(repo, *states)
    (repo / "tests" / "test_later.py").write_text("def test_later():\n    assert False\n")
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)


@pytest.mark.parametrize("n", [1, 9], ids=["H5-step-set-back-to-pending", "H10-step-not-in-plan"])
def test_r14_a_record_whose_step_is_pending_or_gone_is_stale(tmp_path, n):
    """ai-dlc-record R14, rows H5 and H10: a revert with no commit that sets the step back to
    PENDING, or an edit that removes the step, leaves a record that gates nothing."""
    repo, control = gate_repo(tmp_path)
    record = check_begun(repo)
    if n != 1:
        record.rename(record.with_name(f"step-{n}.json"))
    set_states(repo, ("pending", ""), ("pending", ""))
    dirty(repo)
    suite(control, 1, "FAILED test_x\n")
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)


@pytest.mark.parametrize(
    "next_state", [("blocked-decision", "which store?"), ("failed", "red twice")],
    ids=["B3-next-step-blocked", "B3-next-step-failed"],
)  # fmt: skip
def test_r14_a_fix_round_is_gated_under_a_blocked_or_failed_next_step(tmp_path, next_state):
    """ai-dlc-record R14, row B3: a live record gates the stop whatever state the plan's first
    open step has; the next chunk's first step marked BLOCKED or FAILED earlier does not stand
    the fix round down."""
    repo, control = gate_repo(tmp_path)
    set_states(repo, ("done", "a"), next_state)
    check_begun(repo)
    dirty(repo)
    suite(control, 1, "FAILED test_fix\n")
    r = stop(repo)
    assert r.returncode == 2 and "FAILED test_fix" in r.stderr


@pytest.mark.parametrize(
    "state", [("blocked-apparatus", "Eval exit 3"), ("blocked-decision", "which store?")],
    ids=["S6-apparatus", "S9-decision"],
)  # fmt: skip
def test_r14_a_record_whose_step_is_blocked_is_the_loops_own_stop(tmp_path, state):
    """ai-dlc-record R14, rows S6 and S9: the loop marks the step BLOCKED when it stops inside
    it, and that record stands the hook down though it is below two failures."""
    repo, control = gate_repo(tmp_path)
    check_begun(repo)
    dirty(repo)
    set_states(repo, state)
    suite(control, 1, "FAILED test_x\n")
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)


HUNG_SUITE = """\
# Description: a suite that never finishes: a child that holds the output pipe, its pid in
# Description: the control directory.
sleep 60 &
echo $! > "@CONTROL@/child-pid"
wait
"""


def run_hook_with_limit(cwd, limit):
    """The hook as the host runs it, with its Test limit set to a test's value."""
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('stop_gate', {str(HOOK)!r})\n"
        "hook = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(hook)\n"
        f"hook.TEST_LIMIT = {limit}\n"
        "raise SystemExit(hook.run())\n"
    )
    payload = json.dumps({"cwd": str(cwd), "hook_event_name": "Stop", "stop_hook_active": False})
    return subprocess.run(
        [sys.executable, "-c", code], input=payload, capture_output=True, text=True, cwd=str(cwd),
        timeout=30,
    )  # fmt: skip


def test_r14_a_test_past_its_limit_ends_the_turn_with_exit_1_and_is_killed(tmp_path):
    """ai-dlc-record R14, row T1: a Test still running at the limit has its process group
    killed, a child holding the pipe included, and the turn ends with exit 1 and one stderr
    line; nothing blocks and the gate file is left as it was."""
    import os
    import time

    repo, control = gate_repo(tmp_path)
    (repo / "suite.sh").write_text(HUNG_SUITE.replace("@CONTROL@", str(control)))
    seeded = json.dumps({"gate": "stop", "failure": "abcd1234", "hash": "abcd1234" * 8,
                         "count": 2, "overridden": False}).encode()  # fmt: skip
    gate_file(repo).parent.mkdir(parents=True)
    gate_file(repo).write_bytes(seeded)
    started = time.monotonic()
    r = run_hook_with_limit(repo, 1)
    assert time.monotonic() - started < 20
    assert (r.returncode, r.stdout) == (1, "")
    assert r.stderr.splitlines() == [
        "stop-gate: the plan's Test ran past 1 s and was stopped, so the turn may end"
    ]
    assert gate_file(repo).read_bytes() == seeded
    child = int((control / "child-pid").read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(child, 9)
        pytest.fail(f"the Test's child {child} outlived the hook")


def test_r14_the_test_limit_is_540_s_below_the_registered_timeout():
    """ai-dlc-record R14, row T1: the limit sits under the host's timeout for the hook, so the
    hook, not the host, ends a hung Test."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("stop_gate", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    registered = json.loads((HOOK.parent / "hooks.json").read_text())["hooks"]["Stop"]
    timeout = registered[0]["hooks"][0]["timeout"]
    assert (hook.TEST_LIMIT, timeout) == (540, 600)


def stop_with_env(cwd, **env):
    import os

    payload = json.dumps({"cwd": str(cwd), "hook_event_name": "Stop", "stop_hook_active": False})
    return subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, cwd=str(cwd),
        env={**os.environ, **env},
    )  # fmt: skip


@pytest.mark.parametrize("root", ["tmp", "tmpdir"])
def test_r14_a_mkdtemp_name_under_the_temp_dir_does_not_split_one_failure(tmp_path, root):
    """ai-dlc-record R14, from the captures: bun's red-first failure printed its mkdtemp
    directory, whose random suffix follows a letter, and split one failure into two. The first
    segment under /tmp and under the temporary directory the Test inherits is removed; the
    rest of the path still counts."""
    repo, control = gate_repo(tmp_path)
    base = tmp_path / "own-tmp"
    base.mkdir()
    # TMPDIR always names a directory of the test's own, so the /tmp case holds apart from it
    env = {"TMPDIR": str(base)}
    under = str(base) if root == "tmpdir" else "/tmp"
    dirty(repo)
    for n, suffix in enumerate(["JtWV65", "IDkn9g"], start=1):
        suite(control, 1, f'Expected: "1" at {under}/manifest-org-{suffix}/credentials.json\n')
        assert stop_with_env(repo, **env).returncode == 2
        assert json.loads(gate_file(repo).read_text())["count"] == n
    suite(control, 1, f'Expected: "1" at {under}/manifest-org-IDkn9g/other.json\n')
    assert stop_with_env(repo, **env).returncode == 2
    assert json.loads(gate_file(repo).read_text())["count"] == 1


def tap(failing, first=327):
    """A node test runner TAP report as the captures show it: the failure where its test ran,
    then 45 passing tests and the summary, so the last 40 lines hold no failure."""
    lines = [f"# Subtest: {failing}", f"not ok {first} - {failing}", "  ---", "  duration_ms: 38.2"]
    lines += ["  error: 'the input did not match'", "  ..."]
    for k in range(45):
        lines += [f"ok {first + 1 + k} - R24: passing test {chr(65 + k % 26)}"]
    lines += [f"1..{first + 45}", f"# tests {first + 45}", f"# pass {first + 44}", "# fail 1"]
    return "\n".join(lines) + "\n"


def test_r14_node_tap_failure_lines_lead_the_tail(tmp_path):
    """ai-dlc-record R14, from the captures: every red run of the node workflows suite hashed
    the same and its tail showed no failure; each earlier `not ok` line now leads the tail, so
    two failing tests are two failures and the same one under another number is one."""
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    suite(control, 1, tap("R1: init refuses a slug that names a directory"))
    r = stop(repo)
    assert r.returncode == 2
    assert r.stderr.splitlines()[0] == "not ok 327 - R1: init refuses a slug that names a directory"
    suite(control, 1, tap("R1: init refuses a slug that names a directory", first=330))
    assert stop(repo).returncode == 2
    assert json.loads(gate_file(repo).read_text())["count"] == 2
    suite(control, 1, tap("R2: a line without a timestamp is refused"))
    assert stop(repo).returncode == 2
    assert json.loads(gate_file(repo).read_text())["count"] == 1


# -- 3.4.1 review: states the table lacked, the tail's details, the temp root's anchor --------


@pytest.mark.parametrize(
    ("first_states", "failures", "stop_state"),
    [
        ([("in-progress", "")], 0, ("failed", "red Test twice")),
        ([("in-progress", "")], 1, ("failed", "one failed check, then a red Test")),
        ([("done", "a"), ("pending", "")], 0, ("failed", "the fix is red twice")),
        ([("done", "a"), ("pending", "")], 0, ("blocked-apparatus", "COMMIT APPARATUS")),
        ([("done", "a"), ("pending", "")], 1, ("blocked-decision", "which store?")),
    ],
    ids=[
        "S8b-red-test-twice", "S8b-failed-check-then-red-test", "B4b-fix-round-red",
        "B4b-fix-round-apparatus", "B4b-fix-round-decision",
    ],
)  # fmt: skip
def test_r14_a_record_whose_step_the_loop_marked_below_two_failures_stands_down(
    tmp_path, first_states, failures, stop_state
):
    """ai-dlc-record R14, rows S8b and B4b: the loop's stop on red through a red Test rather
    than a second failed check, and each stop inside the fix round, leave a record below two
    failures whose step the loop marked FAILED or BLOCKED; that record is the loop's stop."""
    repo, control = gate_repo(tmp_path)
    set_states(repo, *first_states)
    record = check_begun(repo)
    if failures:
        record.write_text(json.dumps({**json.loads(record.read_text()), "failures": failures}))
    dirty(repo)
    set_states(repo, stop_state)
    suite(control, 1, "FAILED test_x\n")
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)


def test_r14_a_two_failure_record_whose_step_was_finished_by_hand_is_stale(tmp_path):
    """ai-dlc-record R14, row H13: after the loop's stop at two failures, a person fixes the
    step, commits it by hand and sets it done; the record is stale before it is a stop, so a
    later step set IN PROGRESS by hand is gated as the plan says it is open."""
    repo, control = gate_repo(tmp_path)
    record = check_begun(repo)
    record.write_text(json.dumps({**json.loads(record.read_text()), "failures": 2}))
    commit_by_hand(repo)
    set_states(repo, ("done", "fixed by hand"), ("in-progress", ""))
    more_work(repo)
    suite(control, 1, "FAILED test_step_2\n")
    r = stop(repo)
    assert r.returncode == 2 and "FAILED test_step_2" in r.stderr


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ("/repo/test/tmp/case-alpha/x.json", "/repo/test/tmp/case-beta/x.json", False),
        ("/var/tmp/run-alpha/x.json", "/var/tmp/run-beta/x.json", False),
        ("./tmp/run-alpha/x.json", "./tmp/run-beta/x.json", False),
        ("file:///tmp/record-test-JtWV65/x.mjs", "file:///tmp/record-test-IDkn9g/x.mjs", True),
        ("'/tmp/record-test-JtWV65/x.mjs'", "'/tmp/record-test-IDkn9g/x.mjs'", True),
    ],
    ids=["repo-tmp-dir", "var-tmp", "relative-tmp", "file-url", "quoted"],
)
def test_r14_the_temp_root_counts_only_where_it_begins_a_path(tmp_path, first, second, same):
    """ai-dlc-record R14: the segment removed is the one under /tmp or the temporary directory
    where that root begins a path; a repository's own tmp/ directory and /var/tmp keep their
    names, so two cases there stay two failures."""
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    for text in (first, second):
        suite(control, 1, f"FAILED test_x at {text}\n")
        assert stop_with_env(repo, TMPDIR=str(tmp_path)).returncode == 2
    assert json.loads(gate_file(repo).read_text())["count"] == (2 if same else 1)


def test_r14_an_indented_tap_failure_line_leads_the_tail_once(tmp_path):
    """ai-dlc-record R14: a nested subtest's failure line is indented and names the failing
    test; it leads the tail, and a failure line already inside the last 40 lines is not
    repeated ahead of them."""
    repo, control = gate_repo(tmp_path)
    dirty(repo)
    report = tap("R1: the parent").replace(
        "not ok 327 - R1: the parent", "    not ok 1 - the nested case\nnot ok 327 - R1: the parent"
    )
    report = report.replace("# fail 1", "not ok 400 - R9: a late failure\n# fail 2")
    suite(control, 1, report)
    r = stop(repo)
    assert r.returncode == 2
    lines = r.stderr.splitlines()
    assert lines[:2] == ["    not ok 1 - the nested case", "not ok 327 - R1: the parent"]
    assert lines.count("not ok 400 - R9: a late failure") == 1


# -- 3.4.2: rows A2, A3 and L3 of ai-dlc-record R14's states table -----------------------------


def linked_worktree(repo, name):
    """A linked worktree of the fixture repo at its HEAD, with the plan's step 1 started as
    the attended loop's item 3 leaves it."""
    tree = repo.parent / name
    git(repo, "worktree", "add", "-q", "-b", name, str(tree))
    set_states(tree, ("in-progress", ""))
    return tree


def live_marker(checkout, run_id="20260928-run001"):
    marker = checkout / ".next-auto" / run_id / "state.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}")
    return marker


@pytest.mark.parametrize("run_from", ["main", "other-linked"])
def test_r14_a2_a_run_live_from_another_checkout_leaves_a_linked_worktrees_gate(
    tmp_path, run_from
):
    """ai-dlc-record R14, row A2: a run stands down only its own tree, so an attended session
    in a linked worktree is gated while a run is live from the main checkout or from another
    linked worktree."""
    repo, control = gate_repo(tmp_path)
    linked = linked_worktree(repo, "linked")
    live_marker(repo if run_from == "main" else linked_worktree(repo, "other"))
    dirty(linked)
    suite(control, 1, "FAILED test_linked\n")
    r = stop(linked)
    assert r.returncode == 2 and "FAILED test_linked" in r.stderr


@pytest.mark.parametrize("start_from", ["main", "linked"])
def test_r14_a3_a_session_in_a_live_runs_worktree_stands_down(tmp_path, start_from):
    """ai-dlc-record R14, row A3: the run's own worktree, .next-auto/<run-id>/wt beside its
    marker, stands the hook down wherever the run was started from; a run started from a
    linked worktree keeps its marker under that worktree, which 3.4.1 never read."""
    import next_auto

    repo, control = gate_repo(tmp_path)
    root = repo if start_from == "main" else linked_worktree(repo, "linked")
    state = next_auto.start(root, "prompt_plan.md", "main", run_id="20260928-a3run", sync=False)
    wt = Path(state["worktree"])
    assert wt == root / ".next-auto" / "20260928-a3run" / "wt"
    set_states(wt, ("in-progress", ""))
    dirty(wt)
    suite(control, 1, "FAILED test_in_the_run\n")
    before = files_of(wt)
    assert_stood_down(stop(wt), before, wt)


def test_r14_l3_the_item_1_stop_at_an_open_step_stands_down_once_marked(tmp_path):
    """ai-dlc-record R14, row L3: item 1 stops on a dirty tree whose first open step is IN
    PROGRESS with its check record from an earlier session; the loop marks the step
    blocked-decision, stop (c), and that plan and record stand the hook down."""
    repo, control = gate_repo(tmp_path)
    record = check_begun(repo)
    dirty(repo)
    set_states(repo, ("blocked-decision", "resume or revert step 1's earlier work"))
    suite(control, 1, "FAILED test_x\n")
    assert record.is_file()
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)


@pytest.mark.parametrize("start_from", ["main", "linked"])
def test_r14_a5_a_finished_runs_worktree_is_gated_as_any_tree(tmp_path, start_from):
    """ai-dlc-record R14, row A5: wrap-up removes the marker and leaves the run's worktree, so
    a session there is no longer inside a live run and the other rows apply."""
    import next_auto

    repo, control = gate_repo(tmp_path)
    root = repo if start_from == "main" else linked_worktree(repo, "linked")
    state = next_auto.start(root, "prompt_plan.md", "main", run_id="20260928-a3done", sync=False)
    wt = Path(state["worktree"])
    (Path(state["run_dir"]) / "state.json").unlink()
    set_states(wt, ("in-progress", ""))
    dirty(wt)
    suite(control, 1, "FAILED test_after_the_run\n")
    r = stop(wt)
    assert r.returncode == 2 and "FAILED test_after_the_run" in r.stderr


def test_r14_a1_a_run_started_from_a_linked_worktree_stands_that_worktree_down(tmp_path):
    """ai-dlc-record R14, row A1: the checkout a run was started from holds its marker under its
    own .next-auto/, a linked worktree as much as the main checkout, and a session there stands
    down; 3.4.1 read the marker from the main checkout only and gated it."""
    import next_auto

    repo, control = gate_repo(tmp_path)
    linked = linked_worktree(repo, "linked")
    next_auto.start(linked, "prompt_plan.md", "main", run_id="20260928-a1run", sync=False)
    dirty(linked)
    suite(control, 1, "FAILED test_linked\n")
    before = files_of(linked)
    assert_stood_down(stop(linked), before, linked)


def test_r14_a4_a_run_live_from_a_linked_worktree_leaves_the_main_checkouts_gate(tmp_path):
    """ai-dlc-record R14, row A4: a run live from another worktree of the repository stands
    nothing down, the main checkout included."""
    import next_auto

    repo, control = gate_repo(tmp_path)
    linked = linked_worktree(repo, "linked")
    next_auto.start(linked, "prompt_plan.md", "main", run_id="20260928-mainrun", sync=False)
    dirty(repo)
    suite(control, 1, "FAILED test_main\n")
    r = stop(repo)
    assert r.returncode == 2 and "FAILED test_main" in r.stderr


# -- 3.4.3: row L3's resume and rows L4 to L7 of ai-dlc-record R14's states table -------------

NEXT_MD = Path(__file__).resolve().parents[1] / "commands" / "next.md"


def item_1():
    """commands/next.md's item 1 before the first step, its whitespace collapsed."""
    doc = NEXT_MD.read_text()
    before = doc[doc.index("**Before the first step.**") :]
    return " ".join(before.split("2. **Record the batch base.**")[0].split())


def resumed(repo, before, n, failure):
    """Item 1's stop (c) at step N and a resume: marked blocked-decision, the stop stands down
    and writes nothing; set back in progress, it is gated again."""
    set_states(repo, *before, ("blocked-decision", f"resume or revert step {n}'s earlier work"))
    snapshot = files_of(repo)
    assert_stood_down(stop(repo), snapshot, repo)
    set_states(repo, *before, ("in-progress", ""))
    r = stop(repo)
    assert r.returncode == 2 and failure in r.stderr


def done_at_head(repo, control, n, states, committed):
    """Step N DONE with its check record at HEAD and a dirty tree, gated before item 1 marks
    it: begun at the step's commit, as commit N writes it, for the boundary fix round, or
    begun before a commit N that refused or failed."""
    if committed:
        more_work(repo)
        git(repo, "add", "later.py")
        git(repo, "commit", "-qm", f"feat(step-{n}): the step")
    set_states(repo, *states)
    record = check_begun(repo, n)
    dirty(repo)
    suite(control, 1, "FAILED test_fix\n")
    r = stop(repo)
    assert r.returncode == 2 and "FAILED test_fix" in r.stderr
    return record


DONE_STATES = [(1, [("done", "a"), ("pending", "")]), (2, [("done", "a"), ("done", "b")])]


@pytest.mark.parametrize(
    ("n", "states"), DONE_STATES, ids=["L4-next-step-pending", "L5-finished-plan"]
)
def test_r14_l4_l5_the_item_1_stop_at_a_done_steps_fix_round_stands_down_once_marked(
    tmp_path, n, states
):
    """ai-dlc-record R14, rows L4 and L5: item 1 stops on a dirty tree left by a fix round an
    earlier session began, step N DONE with its check record at HEAD, its commit, under a
    PENDING next step or on the last step of a finished plan. The record is live, so the stop
    is gated until the loop marks N blocked-decision, stop (c), and the resume's set N
    in-progress gates the fix again. commands/next.md's item 1 reads no state of the steps
    after N, tells the fix round by HEAD's subject, and resumes it at its fix with no second
    check begin, through its re-review and record commit, from a batch base at the step's
    commit."""
    repo, control = gate_repo(tmp_path)
    record = done_at_head(repo, control, n, states, committed=True)
    resumed(repo, states[: n - 1], n, "FAILED test_fix")
    assert record.is_file()
    item = item_1()
    for phrase in (
        "a step is `DONE` and its check record `.next-auto/attended/step-N.json` has its "
        "`base_head` or its `passed_head` at HEAD, whatever state the steps after it have",
        "a fix round an earlier session left when HEAD's subject begins `feat(step-N):`",
        "python3 $PLAN set prompt_plan.md N in-progress",
        "record the batch base, which is step N's commit, and continue the boundary's fix round "
        "at its fix",
        "with no second `check begin N`, since its record exists",
        "`verdict N --round 2`",
        "`record-commit`",
    ):
        assert phrase in item, phrase


@pytest.mark.parametrize(("n", "states"), DONE_STATES, ids=["next-step-pending", "last-step"])
def test_r14_l6_the_item_1_stop_at_a_commit_that_did_not_land_stands_down_once_marked(
    tmp_path, n, states
):
    """ai-dlc-record R14, row L6, rows S10 and S11 carried over: an earlier session set step N
    DONE and its commit N refused or failed, so the record is at HEAD and HEAD is not the
    step's commit. It is gated as a fix round is until item 1 marks it with the same mark,
    stop (c); the resume checks again if the tree moved, sets the step done and commits it,
    and goes on at item 14."""
    repo, control = gate_repo(tmp_path)
    done_at_head(repo, control, n, states, committed=False)
    resumed(repo, states[: n - 1], n, "FAILED test_fix")
    item = item_1()
    for phrase in (
        "and otherwise a step whose `commit N` refused or failed",
        "On a resume of a commit that did not land, record the batch base as item 2 does",
        "run `check N` again if the tree moved since it passed",
        "`set N done` with its evidence and `commit N`, and go on at per-step item 14",
    ):
        assert phrase in item, phrase


@pytest.mark.parametrize("state", [("in-progress", ""), ("done", "a")], ids=["in-progress", "done"])
def test_r14_l7_a_record_at_two_failures_at_item_1_is_stop_a(tmp_path, state):
    """ai-dlc-record R14, row L7: an earlier session ended between the second failed check N
    and set N failed, so check N would refuse at once. Item 1 makes it stop (a), marked failed
    as on red, and the record at two failures stands the hook down before the mark and
    after it."""
    repo, control = gate_repo(tmp_path)
    set_states(repo, state)
    record = check_begun(repo)
    record.write_text(json.dumps({**json.loads(record.read_text()), "failures": 2}))
    dirty(repo)
    suite(control, 1, "FAILED test_x\n")
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)
    set_states(repo, ("failed", "two failed checks"))
    before = files_of(repo)
    assert_stood_down(stop(repo), before, repo)
    item = item_1()
    assert "A check record at two failures makes the stop stop (a), not stop (c)" in item
    assert "python3 $PLAN set prompt_plan.md N failed --note-file /tmp/note.txt" in item
    assert "a resume of it records the batch base as item 2 does" in item
    # the resume of an open step below two failures is the other rule, so a two-failure record
    # takes check begin N and never item 5
    assert "On a resume of a step that was `IN PROGRESS` below two failures" in item


@pytest.mark.parametrize("begun", [True, False], ids=["with-its-record", "with-none"])
def test_r14_l3_a_resume_sets_the_step_in_progress_and_is_gated_again(tmp_path, begun):
    """ai-dlc-record R14, row L3: the item 1 stop at an IN PROGRESS step marks it
    blocked-decision, which stands the hook down, so a resume that left it there would run the
    resumed step ungated; every resume in item 1 begins with set N in-progress, the plan says
    the step is open again, and the stop is gated with its check record from the earlier
    session or with none."""
    repo, control = gate_repo(tmp_path)
    if begun:
        check_begun(repo)
    dirty(repo)
    suite(control, 1, "FAILED test_resumed\n")
    resumed(repo, [], 1, "FAILED test_resumed")
    assert "Every resume begins with `python3 $PLAN set prompt_plan.md N in-progress`" in item_1()


@pytest.mark.parametrize("edited", [False, True], ids=["resumed-check-passes", "step-edit-refused"])
def test_r14_an_override_in_a_fix_round_refreshes_the_check_record_for_the_resumed_check(
    tmp_path, edited
):
    """ai-dlc-record R14 and R12: the fourth red stop in a fix round appends gate.overridden to
    the record, the hook's own line as commit N's step.done is, so a check record that matched
    the record before the append takes it as it now stands and the resumed check N passes; a
    record the step itself edited before the override is still refused."""
    import next_auto

    repo, control = gate_repo(tmp_path)
    attended_pass(repo, 1)
    set_done(repo, 1, "2 tests green, mutation killed both ways, CHECK PASS")
    assert next_auto.main(["--root", str(repo), "commit", "1"]) == 0
    rc, line = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
    assert rc == 0, line
    fix_step(repo)
    if edited:
        with (repo / RECORD_FILE).open("a") as fh:
            fh.write("- a line the step wrote\n")
    suite(control, 1, "FAILED test_fix\n")
    assert [stop(repo).returncode for _ in range(4)] == [2, 2, 2, 0]
    record = (repo / RECORD_FILE).read_bytes()
    assert b"gate.overridden" in record
    snapshot = repo / ".next-auto" / "attended" / "record-snapshot-1.md"
    assert (snapshot.read_bytes() == record) is not edited
    set_states(repo, ("in-progress", ""))
    rc, lines = next_auto.check_step(repo, 1)
    if edited:
        assert rc == 1 and "changed during the step" in " ".join(lines)
    else:
        assert (rc, lines) == (0, ["CHECK PASS step 1"])
    doc = " ".join(NEXT_MD.read_text().split())
    refresh = "a check record that matched the record before that append takes the new hash"
    assert refresh in doc
    assert "only the engine's own appends write it" in doc


def test_r14_the_test_runs_with_the_node_tap_reporter_pinned(tmp_path):
    """ai-dlc-record R14: the hook reads node's TAP `not ok` lines, so its Test runs in the
    engine's test environment, NODE_OPTIONS pinning the TAP reporter; the suite here prints
    the variable and fails, so the block's tail shows it."""
    repo, _control = gate_repo(tmp_path)
    dirty(repo)
    (repo / "suite.sh").write_text(
        "# Description: prints the node options the hook set and fails.\n"
        "# Description: fixture for the stop gate tests.\n"
        'echo "NODE_OPTIONS=$NODE_OPTIONS"\nexit 1\n'
    )
    r = stop(repo)
    assert r.returncode == 2
    assert "NODE_OPTIONS=--test-reporter=tap --test-reporter-destination=stdout" in r.stderr


# ---- adw 3.5.8: the Stop hook judges the payload's tree alone (R14 amendment (1)) ---------

# Fixture shape: the main checkout with nothing begun and a second worktree of the same
# repository added by `git worktree add`, where the step runs, as a session started in the
# checkout drives a step in `<repo>-<name>`


def side_tree(tmp_path, repo):
    """A second worktree of the repository at tmp_path/side on branch side, the main
    checkout's plan set back to nothing begun."""
    set_states(repo, ("pending", ""))
    side = tmp_path / "side"
    git(repo, "worktree", "add", "-q", "-b", "side", str(side))
    return side


def live_red_step(side, control):
    """Step 1 begun and live in the worktree, with work in it and a red Test."""
    set_states(side, ("in-progress", ""))
    check_begun(side)
    dirty(side)
    suite(control, 1, "FAILED test_x in the side tree\n")


def test_a_stop_in_the_live_worktree_is_gated_and_its_line_names_no_tree(tmp_path):
    repo, control = gate_repo(tmp_path)
    side = side_tree(tmp_path, repo)
    live_red_step(side, control)
    r = stop(side)
    assert r.returncode == 2 and "FAILED test_x in the side tree" in r.stderr
    assert json.loads(gate_file(side).read_text())["count"] == 1
    assert not gate_file(repo).exists()
    [line] = r.stdout.splitlines()
    assert line.startswith("stop gate: the plan's Test is red, block 1 of 3 on failure ")
    assert "work tree" not in line


def payload_tree(tmp_path, repo, kind):
    """The tree a stop's payload names beside the live side tree: the main checkout with
    nothing begun, a worktree whose branch has no plan, a detached scratch checkout at HEAD as
    prove-red adds one, or the main checkout with every step done; each carries work."""
    if kind == "no-plan":
        tree = tmp_path / "noplan"
        git(repo, "worktree", "add", "-q", "-b", "noplan", str(tree))
        git(tree, "rm", "-q", "prompt_plan.md")
        git(tree, "commit", "-qm", "no plan on this branch")
    elif kind == "scratch-checkout":
        tree = tmp_path / "scratch"
        git(repo, "worktree", "add", "-q", "--detach", str(tree), "HEAD")
    else:
        tree = repo
        if kind == "finished-plan":
            set_states(repo, ("done", "a"), ("done", "b"))
    dirty(tree)
    return tree


@pytest.mark.parametrize(
    "kind", ["main-checkout", "no-plan", "scratch-checkout", "finished-plan"]
)
def test_f426_f590_a_tree_that_is_not_live_stands_down_beside_a_live_tree(tmp_path, kind):
    """ai-dlc-record R14 amendment (1), F-426 and F-590: a tree with no plan, or no live check
    record and no IN PROGRESS step, stands the hook down and reads no other tree, so another
    pane's red Test never runs from this stop and nothing is written in its tree."""
    repo, control = gate_repo(tmp_path)
    side = side_tree(tmp_path, repo)
    live_red_step(side, control)
    tree = payload_tree(tmp_path, repo, kind)
    before, side_before = files_of(tree), files_of(side)
    assert_stood_down(stop(tree), before, tree)
    assert files_of(side) == side_before
    assert not gate_file(side).exists()


def test_a_clean_checkout_with_nothing_live_anywhere_passes(tmp_path):
    repo, control = gate_repo(tmp_path)
    side = side_tree(tmp_path, repo)
    dirty(side)
    suite(control, 1, "FAILED test_x in the side tree\n")
    before, side_before = files_of(repo), files_of(side)
    assert_stood_down(stop(repo), before, repo)
    assert files_of(side) == side_before


# ---- adw 3.5.8: the hash-skip (R14 amendment (2)) -----------------------------------------


def passed_then_red_test(tmp_path):
    """A real check N pass at step 1, then the plan's Test changed to one that always fails:
    the plan is left out of the hash, so the tree is still the one the check vouched for, and
    a Test the hook runs blocks."""
    repo = pytest_repo(tmp_path)
    attended_pass(repo, 1)
    plan = repo / "prompt_plan.md"
    plan.write_text(
        plan.read_text().replace(f"- Test: {shlex.join(TEST_CMD)}", "- Test: false")
    )
    return repo


def step_record(repo, n=1):
    return repo / ".next-auto" / "attended" / f"step-{n}.json"


def test_r14_check_n_records_the_tree_it_passed(tmp_path):
    """rul-86cd241e32, the release's hash-skip: check N records the tree its pass vouches
    for."""
    import next_auto

    repo = passed_then_red_test(tmp_path)
    rec = json.loads(step_record(repo).read_text())
    result = json.loads((repo / ".next-auto" / "attended" / "result-1.json").read_text())
    assert rec["passed_tree"] == result["tree"]
    assert re.fullmatch(r"[0-9a-f]{64}", rec["passed_tree"])
    assert rec["passed_head"] == next_auto.git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.mark.parametrize("edited", [False, True], ids=["at-the-checked-tree", "after-an-edit"])
def test_r14_a_stop_at_the_checked_tree_runs_no_test(tmp_path, edited):
    """ai-dlc-record R14 amendment (2), rul-86cd241e32: a stop whose tree hashes to the live
    record's passed_tree exits 0 with no Test run; an edit after the check moves the hash, and
    the Test runs and blocks."""
    repo = passed_then_red_test(tmp_path)
    if edited:
        feature(repo, 41)
    r = stop(repo)
    if edited:
        assert r.returncode == 2
        assert r.stdout.startswith("stop gate: the plan's Test is red, block 1 of 3")
        assert gate_file(repo).is_file()
    else:
        assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
        assert not gate_file(repo).exists()


def test_r14_a_stop_at_the_checked_tree_clears_the_gate_count_as_a_green_test_did(tmp_path):
    """ai-dlc-record R14 amendment (2), rul-86cd241e32: a skip stands where a green Test stood, so a
    block count from a red stop before the check passed does not carry into a later failure."""
    repo = passed_then_red_test(tmp_path)
    gate = gate_file(repo)
    seen = {"gate": "stop", "failure": "abcd1234", "hash": "a" * 64, "count": 1}
    seen["overridden"] = False
    gate.write_text(json.dumps(seen))
    r = stop(repo)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert not gate.exists()


def test_r14_an_untracked_file_after_the_check_moves_the_tree(tmp_path):
    """rul-86cd241e32: the hash covers files git would add, so a new file runs the Test."""
    repo = passed_then_red_test(tmp_path)
    (repo / "notes.txt").write_text("written after the check\n")
    assert stop(repo).returncode == 2


@pytest.mark.parametrize("why", ["earlier-engine", "never-passed"])
def test_r14_a_live_record_without_passed_tree_is_still_gated(tmp_path, why):
    """ai-dlc-record R14 amendment (2), rul-86cd241e32: a record from an engine before
    3.5.8, or from a check that has not passed, carries no passed_tree, and the Test
    runs as before."""
    import next_auto

    if why == "earlier-engine":
        repo = passed_then_red_test(tmp_path)
        rec = json.loads(step_record(repo).read_text())
        del rec["passed_tree"]
        step_record(repo).write_text(json.dumps(rec, indent=2))
    else:
        repo = pytest_repo(tmp_path)
        rc, line = next_auto.check_begin(repo, 1, test_cmd=TEST_CMD, lint_cmd=LINT_OK)
        assert rc == 0, line
        feature(repo, 42)
        plan = repo / "prompt_plan.md"
        plan.write_text(
            plan.read_text().replace(f"- Test: {shlex.join(TEST_CMD)}", "- Test: false")
        )
    assert "passed_tree" not in json.loads(step_record(repo).read_text())
    r = stop(repo)
    assert r.returncode == 2 and r.stdout.startswith("stop gate: the plan's Test is red")


def test_r14_a_tree_git_cannot_stage_runs_the_test_and_says_why(tmp_path):
    """ai-dlc-record R14 amendment (2), rul-86cd241e32: with src.py unmerged in the index,
    write-tree refuses, so the hook cannot say the tree is the checked one and runs the Test
    as before."""
    repo = passed_then_red_test(tmp_path)
    blob = git(repo, "hash-object", "-w", "src.py").stdout.strip()
    zero = "0" * 40
    info = f"0 {zero}\tsrc.py\n100644 {blob} 1\tsrc.py\n100644 {blob} 2\tsrc.py\n"
    subprocess.run(
        ["git", "-C", str(repo), "update-index", "--index-info"], input=info, text=True, check=True
    )
    assert "U" in git(repo, "status", "--porcelain").stdout.split("src.py")[0]
    r = stop(repo)
    assert r.returncode == 2 and r.stdout.startswith("stop gate: the plan's Test is red")
    assert "stop-gate: cannot hash the tree, so the Test runs: " in r.stderr
