# Description: Tests for ruling-recall.py, the PreToolUse hook on AskUserQuestion that denies a
# Description: dialog until each matching ruling was shown to the session. Real stores, no mocks.

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import rulings

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "ruling-recall.py"
FIXTURES = Path(__file__).parent / "fixtures" / "rulings"
# The PreToolUse payload is the envelope of the hooks reference (code.claude.com/docs/en/hooks,
# read 2026-10-21 11:55:54 EDT: the common fields, tool_name, tool_input and tool_use_id) around
# the tool_input of ask-posttooluse.json, the AskUserQuestion call answered 2026-10-20 20:52:31 EDT
# in session be11367e (header Areko zitul). The stored rulings' headers, questions and options
# are live: Zitul and its question and answer from the same session's dialog answered
# 2026-10-20 16:53:44 EDT (its events log, lines 30 and 31), the rest from the two fixtures.
SESSION = "be11367e-be3a-479a-b25f-37faf19c6a0e"
TIMER_QUESTION = "Ivobem acme-night.timer (Mon..Fri 03:30 America/New_York)?"
TIMER_OPTION = "Ivobem once vep is etiligemu (Recommended)"
HEAD = (
    "Rulings of the last 48 hours sharing a word with this question"
    " (id | time | header | option | notes):"
)
CLOSING = "Asked again, this dialog goes ahead; ask only what these rulings leave open."
# a second live session id, the one of ask-posttooluse-two.json (answered 2026-10-12 16:33:59 EDT)
OTHER_SESSION = "92db1781-8a8b-47e2-b7e7-ab2af8a80c8b"

_spec = importlib.util.spec_from_file_location("ruling_recall", HOOK)
ruling_recall = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ruling_recall)


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def pre_tool_use():
    post = fixture("ask-posttooluse.json")
    payload = {k: v for k, v in post.items() if k != "tool_response"}
    payload["hook_event_name"] = "PreToolUse"
    return payload


def store_ruling(project_dir, header, option, question=""):
    entry = rulings.make_ruling(
        session=SESSION,
        time=time.strftime(rulings.TIME_FORM),
        header=header,
        question=question,
        option=option,
        notes=None,
        source="dialog",
    )
    rulings.append(project_dir / ".next-auto" / "rulings", entry)
    return entry


def run_hook(stdin, project_dir):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    return subprocess.run(
        [sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, env=env
    )


def store_dir(project_dir):
    return project_dir / ".next-auto" / "rulings"


def decision(r):
    return json.loads(r.stdout)["hookSpecificOutput"]


def test_a_matching_ruling_denies_the_dialog_with_the_rulings_as_its_reason(tmp_path):
    entry = store_ruling(tmp_path, "Zitul", TIMER_OPTION, question=TIMER_QUESTION)
    r = run_hook(json.dumps(pre_tool_use()), tmp_path)
    assert (r.returncode, r.stderr) == (0, "")
    out = decision(r)
    assert (out["hookEventName"], out["permissionDecision"]) == ("PreToolUse", "deny")
    assert out["permissionDecisionReason"].splitlines() == [HEAD, rulings.render(entry), CLOSING]


def test_the_same_dialog_asked_again_goes_ahead(tmp_path):
    store_ruling(tmp_path, "Zitul", TIMER_OPTION, question=TIMER_QUESTION)
    first = run_hook(json.dumps(pre_tool_use()), tmp_path)
    assert decision(first)["permissionDecision"] == "deny"
    again = run_hook(json.dumps(pre_tool_use()), tmp_path)
    assert (again.returncode, again.stdout, again.stderr) == (0, "", "")


def test_a_dialog_with_one_unshown_match_is_denied_and_lists_every_match(tmp_path):
    shown = store_ruling(tmp_path, "Zitul", TIMER_OPTION, question=TIMER_QUESTION)
    assert decision(run_hook(json.dumps(pre_tool_use()), tmp_path))["permissionDecision"] == "deny"
    newer = store_ruling(tmp_path, "Areko zitul", "Ivobem the zitul (Recommended)")
    r = run_hook(json.dumps(pre_tool_use()), tmp_path)
    assert (r.returncode, r.stderr) == (0, "")
    lines = decision(r)["permissionDecisionReason"].splitlines()
    assert (lines[0], lines[-1]) == (HEAD, CLOSING)
    assert sorted(lines[1:-1]) == sorted([rulings.render(shown), rulings.render(newer)])


def test_a_ruling_shown_to_another_session_still_denies_in_this_one(tmp_path):
    entry = store_ruling(tmp_path, "Zitul", TIMER_OPTION, question=TIMER_QUESTION)
    other = pre_tool_use()
    other["session_id"] = OTHER_SESSION
    assert decision(run_hook(json.dumps(other), tmp_path))["permissionDecision"] == "deny"
    shown = store_dir(tmp_path) / f"{OTHER_SESSION}.shown"
    assert shown.read_text(encoding="utf-8") == f"{entry['id']}\n"
    r = run_hook(json.dumps(pre_tool_use()), tmp_path)
    assert decision(r)["permissionDecision"] == "deny"


def test_the_output_denies_and_never_allows_answers_or_adds_context(tmp_path):
    store_ruling(tmp_path, "Areko zitul", "Ivobem the zitul (Recommended)")
    r = run_hook(json.dumps(pre_tool_use()), tmp_path)
    out = json.loads(r.stdout)
    assert list(out) == ["hookSpecificOutput"]
    assert set(out["hookSpecificOutput"]) == {
        "hookEventName",
        "permissionDecision",
        "permissionDecisionReason",
    }
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "updatedInput" not in r.stdout and "additionalContext" not in r.stdout


def test_a_bad_session_id_or_an_unwritable_store_lets_the_question_through(tmp_path):
    store_ruling(tmp_path, "Zitul", TIMER_OPTION, question=TIMER_QUESTION)
    payload = pre_tool_use()
    payload["session_id"] = "not-a-session"
    r = run_hook(json.dumps(payload), tmp_path)
    assert (r.returncode, r.stdout) == (0, "")
    assert "Traceback (most recent call last)" in r.stderr
    assert "ruling-recall: session_id is not a session id: 'not-a-session'" in r.stderr
    store = store_dir(tmp_path)
    store.chmod(0o555)
    try:
        r = run_hook(json.dumps(pre_tool_use()), tmp_path)
    finally:
        store.chmod(0o755)
    assert (r.returncode, r.stdout) == (0, "")
    assert "Traceback (most recent call last)" in r.stderr
    assert "PermissionError" in r.stderr
    assert not (store / f"{SESSION}.shown").exists()


def test_the_store_loader_reads_no_shown_file(tmp_path):
    entry = store_ruling(tmp_path, "Zitul", TIMER_OPTION, question=TIMER_QUESTION)
    run_hook(json.dumps(pre_tool_use()), tmp_path)
    store = store_dir(tmp_path)
    assert (store / f"{SESSION}.shown").is_file()
    assert rulings.load(store) == [entry]


def test_no_match_prints_nothing(tmp_path):
    # Vep fofez shares no word of four letters with the Areko zitul header and question
    store_ruling(tmp_path, "Vep fofez", "Duse the zitul")
    r = run_hook(json.dumps(pre_tool_use()), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    empty = tmp_path / "empty"
    empty.mkdir()
    r = run_hook(json.dumps(pre_tool_use()), empty)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_an_exception_prints_the_traceback_nothing_on_stdout_and_exits_0(tmp_path):
    r = run_hook("{not json", tmp_path)
    assert (r.returncode, r.stdout) == (0, "")
    assert "Traceback (most recent call last)" in r.stderr
    assert "JSONDecodeError" in r.stderr
    r = run_hook(json.dumps(pre_tool_use()), None)
    assert (r.returncode, r.stdout) == (0, "")
    assert "ruling store: CLAUDE_PROJECT_DIR is unset; pass --dir" in r.stderr


def test_found_for_searches_every_question_of_the_dialog(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    entry = store_ruling(tmp_path, "Ferurupof Map", "Next azilib mumegiko Kebega (Recommended)")
    payload = pre_tool_use()
    # the second question of ask-posttooluse-two.json, after the Areko zitul question
    payload["tool_input"]["questions"].append(
        fixture("ask-posttooluse-two.json")["tool_input"]["questions"][1]
    )
    store, found = ruling_recall.found_for(payload, rulings.parse_time(entry["time"]))
    assert store == tmp_path / ".next-auto" / "rulings"
    assert [f["id"] for f in found] == [entry["id"]]
    payload["tool_input"]["questions"].pop()
    assert ruling_recall.found_for(payload, rulings.parse_time(entry["time"]))[1] == []


def test_hooks_json_registers_the_hook_on_ask_user_question_before_it_runs():
    registered = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
    commands = [
        h["command"]
        for entry in registered
        if entry["matcher"] == "AskUserQuestion"
        for h in entry["hooks"]
    ]
    assert commands == ['"${CLAUDE_PLUGIN_ROOT}/hooks/ruling-recall.py"']
    assert os.access(HOOK, os.X_OK)
