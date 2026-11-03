# Description: Tests for ruling-log.py, the PostToolUse hook on AskUserQuestion that writes one
# Description: ruling store line per answered question. Live payloads on stdin, real tmp stores.

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import rulings

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "ruling-log.py"
FIXTURES = Path(__file__).parent / "fixtures" / "rulings"
# Both fixtures are the PostToolUse envelope of the hooks reference (code.claude.com/docs/en/hooks,
# read 2026-10-21 11:55:54 EDT) around a tool_use input and its toolUseResult, built by script
# from the transcripts on 2026-10-21 11:56:12 EDT. ask-posttooluse.json: session be11367e, one
# question (Areko zitul), answered 2026-10-20 20:52:31 EDT. ask-posttooluse-two.json: session
# 92db1781, two questions, the first answered in free text, answered 2026-10-12 16:33:59 EDT.
ONE = "ask-posttooluse.json"
TWO = "ask-posttooluse-two.json"
# a sentence typed into a dialog (the free-text answer in ask-posttooluse-two.json), used as
# a note in the annotation shape of the AskUserQuestion tool schema read 2026-10-21: no live
# transcript carries an annotation with notes
NOTE = "I only anoz oribe SOC2. And fikilu ekugedat to me. Ninorat itag."

_spec = importlib.util.spec_from_file_location("ruling_log", HOOK)
ruling_log = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ruling_log)


def payload(name=ONE):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def run_hook(data, project_dir, *, opted_in=True):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
        # the store directory is the opt-in; a test of its absence passes opted_in=False
        if opted_in:
            (project_dir / ".next-auto" / "rulings").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        env=env,
    )


def stored(project_dir, session):
    path = project_dir / ".next-auto" / "rulings" / f"{session}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_one_store_line_per_question_with_the_answer_from_the_tool_response(tmp_path):
    data = payload()
    question = data["tool_input"]["questions"][0]
    r = run_hook(data, tmp_path)
    assert r.returncode == 0, r.stderr
    [line] = stored(tmp_path, data["session_id"])
    time.strptime(line["time"], rulings.TIME_FORM)
    assert line == rulings.make_ruling(
        session="be11367e-be3a-479a-b25f-37faf19c6a0e",
        time=line["time"],
        header="Areko zitul",
        question=question["question"],
        option="Ivobem the zitul (Recommended)",
        notes=None,
        source="dialog",
    )


def test_notes_are_the_annotation_entrys_notes_for_that_question(tmp_path):
    data = payload()
    text = data["tool_input"]["questions"][0]["question"]
    data["tool_response"]["annotations"] = {text: {"notes": NOTE}}
    r = run_hook(data, tmp_path)
    assert r.returncode == 0, r.stderr
    [line] = stored(tmp_path, data["session_id"])
    assert line["notes"] == NOTE


def test_a_two_question_dialog_writes_two_lines_with_distinct_ids(tmp_path):
    data = payload(TWO)
    # the time of this dialog's two ruling lines in the Acme events log
    # .claude/reads/92db1781-8a8b-47e2-b7e7-ab2af8a80c8b.events.log, read 2026-10-21
    stamp = "2026-10-12 16:33:59 EDT"
    built = ruling_log.rulings_of(data, stamp)
    headers = ("Lidip arilef", "Ferurupof Map")
    assert [(r["header"], r["id"]) for r in built] == [
        (h, rulings.ruling_id(data["session_id"], stamp, h)) for h in headers
    ]
    assert built[0]["id"] != built[1]["id"]
    r = run_hook(data, tmp_path)
    assert r.returncode == 0, r.stderr
    lines = stored(tmp_path, data["session_id"])
    assert [line["header"] for line in lines] == ["Lidip arilef", "Ferurupof Map"]
    assert lines[0]["id"] != lines[1]["id"]
    assert lines[1]["option"] == "Next azilib mumegiko Kebega (Recommended)"


def test_a_free_text_answer_is_stored_as_the_option(tmp_path):
    data = payload(TWO)
    r = run_hook(data, tmp_path)
    assert r.returncode == 0, r.stderr
    first = stored(tmp_path, data["session_id"])[0]
    assert first["question"].startswith("Lidip's inventory-list-owners test")
    assert first["option"] == NOTE


def test_a_tool_response_with_no_answers_keeps_the_header_and_time(tmp_path):
    data = payload()
    del data["tool_response"]["answers"]
    r = run_hook(data, tmp_path)
    assert r.returncode == 0, r.stderr
    [line] = stored(tmp_path, data["session_id"])
    assert (line["header"], line["option"], line["source"]) == ("Areko zitul", None, "dialog")
    time.strptime(line["time"], rulings.TIME_FORM)


def test_on_success_the_hook_names_each_ruling_as_additional_context(tmp_path):
    data = payload(TWO)
    r = run_hook(data, tmp_path)
    assert r.returncode == 0, r.stderr
    lines = stored(tmp_path, data["session_id"])
    out = json.loads(r.stdout)
    assert out == {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "; ".join(
                f"ruling {line['id']} {line['time']} {line['header']}" for line in lines
            ),
        }
    }


def test_an_unwritable_store_prints_the_traceback_and_exits_0(tmp_path):
    # the store exists (the opt-in) but a directory sits where the session's file belongs,
    # so the write fails whoever runs the test
    store = tmp_path / ".next-auto" / "rulings"
    store.mkdir(parents=True)
    (store / f"{payload()['session_id']}.jsonl").mkdir()
    r = run_hook(payload(), tmp_path)
    assert r.returncode == 0
    assert r.stdout == ""
    assert "Traceback (most recent call last)" in r.stderr
    assert "IsADirectoryError" in r.stderr


def test_no_project_dir_or_no_session_id_prints_the_traceback_and_writes_nothing(tmp_path):
    r = run_hook(payload(), None)
    assert (r.returncode, r.stdout) == (0, "")
    assert "ruling store: CLAUDE_PROJECT_DIR is unset; pass --dir" in r.stderr
    data = payload()
    # the session id names the store file, so a value that is not one is refused
    data["session_id"] = "../be11367e"
    r = run_hook(data, tmp_path)
    assert (r.returncode, r.stdout) == (0, "")
    assert "session_id is not a session id: '../be11367e'" in r.stderr
    assert list((tmp_path / ".next-auto" / "rulings").iterdir()) == []


def test_hooks_json_registers_the_hook_on_ask_user_question_after_it_runs():
    registered = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PostToolUse"]
    commands = [
        h["command"]
        for entry in registered
        if entry["matcher"] == "AskUserQuestion"
        for h in entry["hooks"]
    ]
    assert commands == ['"${CLAUDE_PLUGIN_ROOT}/hooks/ruling-log.py"']
    assert os.access(HOOK, os.X_OK)


# ---- the Chunk C review fixes (2026-10-21 12:55:25 EDT) ---------------------------------------

# ask-posttooluse-multi.json: the same envelope around the call answered 2026-10-08 16:54:51 EDT
# in session c7949bb3, built by script on 2026-10-21 12:56:16 EDT; its second question (Sensors)
# is multi-select and was answered with three of its four labels joined by ", "
MULTI = "ask-posttooluse-multi.json"


def test_a_free_text_answer_also_goes_into_notes_so_recall_finds_it(tmp_path):
    data = payload(TWO)
    r = run_hook(data, tmp_path)
    assert r.returncode == 0, r.stderr
    typed, label = stored(tmp_path, data["session_id"])
    assert (typed["option"], typed["notes"]) == (NOTE, NOTE)
    assert (label["option"], label["notes"]) == ("Next azilib mumegiko Kebega (Recommended)", None)
    now = rulings.parse_time(typed["time"])
    assert rulings.recall("fikilu ekugedat", now, [typed, label]) == [typed]


def test_annotation_notes_and_a_free_text_answer_are_both_kept(tmp_path):
    data = payload(TWO)
    text = data["tool_input"]["questions"][0]["question"]
    data["tool_response"]["annotations"] = {text: {"notes": "Ferurupof Map"}}
    r = run_hook(data, tmp_path)
    assert r.returncode == 0, r.stderr
    typed = stored(tmp_path, data["session_id"])[0]
    assert typed["notes"] == f"Ferurupof Map; {NOTE}"


def test_labels_joined_on_a_multi_select_question_are_not_free_text(tmp_path):
    data = payload(MULTI)
    r = run_hook(data, tmp_path)
    assert r.returncode == 0, r.stderr
    merge, sensors = stored(tmp_path, data["session_id"])
    assert sensors["option"] == (
        "Defer-option viti (Recommended), Merge guard iriti its failed epime (Recommended), "
        "Rosol sha toka on --match-head-commit (Recommended)"
    )
    assert (merge["notes"], sensors["notes"]) == (None, None)


def test_a_label_holding_a_comma_is_not_free_text(tmp_path):
    # "Onege it, zefa only" is an option label of the first question in ask-posttooluse-two.json
    data = payload(TWO)
    text = data["tool_input"]["questions"][0]["question"]
    data["tool_response"]["answers"][text] = "Onege it, zefa only"
    r = run_hook(data, tmp_path)
    assert r.returncode == 0, r.stderr
    first = stored(tmp_path, data["session_id"])[0]
    assert (first["option"], first["notes"]) == ("Onege it, zefa only", None)


def test_joined_labels_on_a_single_select_question_are_free_text(tmp_path):
    data = payload(MULTI)
    text = data["tool_input"]["questions"][0]["question"]
    data["tool_response"]["answers"][text] = "Merge 300 (Recommended), Duse"
    r = run_hook(data, tmp_path)
    assert r.returncode == 0, r.stderr
    merge = stored(tmp_path, data["session_id"])[0]
    assert merge["notes"] == "Merge 300 (Recommended), Duse"


def test_a_header_repeated_in_one_dialog_is_reported_and_left_out_of_the_context(tmp_path):
    data = payload(TWO)
    data["tool_input"]["questions"][1]["header"] = "Lidip arilef"
    r = run_hook(data, tmp_path)
    assert r.returncode == 0
    [line] = stored(tmp_path, data["session_id"])
    assert line["option"] == NOTE
    assert r.stderr == (
        "ruling-log: question 2 repeats the header 'Lidip arilef' of an earlier question in this"
        f" dialog, so its answer shares the id {line['id']} and is not stored:"
        " 'Next azilib mumegiko Kebega (Recommended)'\n"
    )
    context = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert context == f"ruling {line['id']} {line['time']} Lidip arilef"


def test_a_project_without_the_store_directory_records_nothing(tmp_path):
    r = run_hook(payload(), tmp_path, opted_in=False)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert not (tmp_path / ".next-auto").exists()
