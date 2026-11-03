# Description: Tests for rulings.py, the ruling store under .next-auto/rulings/, and for the
# Description: ruling add subcommand of next_plan.py. Real files in tmp dirs, no mocks.

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import next_plan
import pytest
import rulings

SCRIPT = Path(next_plan.__file__)
# one live ruling, joined from two Acme files read 2026-10-21: the line
# `2026-10-21 08:41:03 EDT ruling #16 merge` of the events log
# .claude/reads/33f24739-25f8-400d-a4be-52e68d0a7929.events.log (session, time, header), and
# the option of the same time's line in docs/decisions/2026-10.md
SESSION = "33f24739-25f8-400d-a4be-52e68d0a7929"
TIME = "2026-10-21 08:41:03 EDT"
HEADER = "#16 merge"
OPTION = "Merge #16 now (Recommended)"
# the attended session id Step 1's tests use, a second session for the collision check
OTHER_SESSION = "be11367e-be3a-479a-b25f-37faf19c6a0e"


def dialog_ruling(**overrides):
    fields = dict(
        session=SESSION,
        time=TIME,
        header=HEADER,
        question="",
        option=OPTION,
        notes=None,
        source="dialog",
    )
    fields.update(overrides)
    return rulings.make_ruling(**fields)


def run_ruling(cwd, *args, project_dir=None, session=SESSION):
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID")
    }
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    if session is not None:
        env["CLAUDE_CODE_SESSION_ID"] = session
    return subprocess.run(
        [sys.executable, str(SCRIPT), "ruling", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


def run_add(cwd, *args, project_dir=None, session=SESSION):
    return run_ruling(cwd, "add", *args, project_dir=project_dir, session=session)


def test_ruling_id_is_rul_and_ten_hex_of_sha256_over_session_time_and_header():
    expected = "rul-" + hashlib.sha256(f"{SESSION}|{TIME}|{HEADER}".encode()).hexdigest()[:10]
    assert rulings.ruling_id(SESSION, TIME, HEADER) == expected
    # two sessions answering the same header in the same second cannot collide
    assert rulings.ruling_id(OTHER_SESSION, TIME, HEADER) != expected


def test_appending_the_same_ruling_twice_leaves_one_line(tmp_path):
    store = tmp_path / ".next-auto" / "rulings"
    entry = dialog_ruling()
    assert rulings.append(store, entry) is True
    assert rulings.append(store, entry) is False
    lines = (store / f"{SESSION}.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [entry]


def test_ruling_add_writes_one_line_under_the_project_dir(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    r = run_add(
        tmp_path,
        "--header", HEADER, "--text", OPTION, "--source", "typed 20:41",
        project_dir=project,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    path = project / ".next-auto" / "rulings" / f"{SESSION}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    line = json.loads(lines[0])
    assert list(line) == [
        "id", "time", "session", "header", "question", "option", "notes", "source",
    ]  # fmt: skip
    # the events log's form, as session-events.py writes it with time.strftime
    time.strptime(line["time"], "%Y-%m-%d %H:%M:%S %Z")
    assert line["id"] == rulings.ruling_id(SESSION, line["time"], HEADER)
    assert line["session"] == SESSION
    assert line["header"] == HEADER
    assert line["question"] == ""
    assert line["option"] == OPTION
    assert line["notes"] is None
    assert line["source"] == "typed 20:41"
    # the id goes back to the session in the line the ruling-log hook prints
    assert r.stdout == f"ruling {line['id']} {line['time']} {HEADER}\n"


def test_a_malformed_store_line_is_reported_and_the_others_load(tmp_path, capsys):
    store = tmp_path / ".next-auto" / "rulings"
    first = dialog_ruling()
    second = dialog_ruling(header="#534 merge", time="2026-10-21 00:14:18 EDT")
    path = store / f"{SESSION}.jsonl"
    store.mkdir(parents=True)
    path.write_text(
        json.dumps(first) + "\n" + '{"id": "rul-' + "\n" + json.dumps(second) + "\n",
        encoding="utf-8",
    )
    assert rulings.load(store) == [first, second]
    err = capsys.readouterr().err
    assert f"{path}:2: " in err


def test_ruling_add_with_no_project_dir_and_no_dir_flag_exits_2(tmp_path):
    r = run_add(tmp_path, "--header", HEADER, "--text", OPTION, "--source", "typed 20:41")
    assert r.returncode == 2
    assert r.stderr == "ruling store: CLAUDE_PROJECT_DIR is unset; pass --dir\n"
    assert not (tmp_path / ".next-auto").exists()
    # --dir names the project dir in its place
    project = tmp_path / "project"
    project.mkdir()
    r = run_add(
        tmp_path,
        "--header", HEADER, "--text", OPTION, "--source", "typed 20:41", "--dir", str(project),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert (project / ".next-auto" / "rulings" / f"{SESSION}.jsonl").is_file()


def test_ruling_add_with_no_session_id_exits_2_and_writes_nothing(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    r = run_add(
        tmp_path,
        "--header", HEADER, "--text", OPTION, "--source", "typed 20:41",
        project_dir=project, session=None,
    )  # fmt: skip
    assert r.returncode == 2
    assert r.stderr == "ruling store: no run key: CLAUDE_CODE_SESSION_ID is unset\n"
    assert not (project / ".next-auto").exists()


# ---- recall (Step 8) ---------------------------------------------------------

# a sentence the owner typed into a dialog, the free-text answer in
# tests/fixtures/rulings/ask-posttooluse-two.json, used here as a ruling's notes
NOTE_SOC2 = "I only anoz oribe SOC2. And fikilu ekugedat to me. Ninorat itag."
# the zone the events log's abbreviations are read through; the tests pin it, since CI runs in
# UTC and EDT is not one of that zone's names
NEW_YORK = "America/New_York"
# the 08:41:03 EDT ruling above, as UTC
NOW = datetime(2026, 10, 21, 12, 41, 3, tzinfo=UTC)


@pytest.fixture
def new_york(monkeypatch):
    monkeypatch.setenv("TZ", NEW_YORK)
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def stamp(hours_before):
    """The events log's form for the time hours_before NOW, in the pinned local zone."""
    moment = (NOW - timedelta(hours=hours_before)).timestamp()
    return time.strftime(rulings.TIME_FORM, time.localtime(moment))


def test_recall_matches_a_shared_word_of_four_letters_in_header_question_or_notes(new_york):
    # headers and the question are live: "Zitul" and its question from the dialog answered
    # 2026-10-20 16:53:44 EDT in session be11367e, "Lidip arilef" and "Vep fofez" from the
    # transcripts read 2026-10-21 11:56:12 EDT
    by_header = dialog_ruling(time=stamp(1))
    by_question = dialog_ruling(
        header="Zitul",
        time=stamp(2),
        question="Ivobem acme-night.timer (Mon..Fri 03:30 America/New_York)?",
    )
    by_notes = dialog_ruling(header="Lidip arilef", time=stamp(3), notes=NOTE_SOC2)
    # the option is not searched: every ruling here carries "Merge #16 now (Recommended)"
    by_option = dialog_ruling(header="Vep fofez", time=stamp(4))
    entries = [by_header, by_question, by_notes, by_option]
    assert rulings.recall("When should the MERGE run?", NOW, entries) == [by_header]
    assert rulings.recall("the night runner", NOW, entries) == [by_question]
    assert rulings.recall("SOC2 items ekugedat", NOW, entries) == [by_notes]


def test_recall_does_not_match_a_shared_word_of_three_letters(new_york):
    entry = dialog_ruling(header="Vep fofez", time=stamp(1))
    assert rulings.recall("Which org?", NOW, [entry]) == []


def test_recall_leaves_out_a_ruling_older_than_48_hours(new_york):
    inside = dialog_ruling(time=stamp(47))
    outside = dialog_ruling(time=stamp(49))
    assert rulings.recall("merge", NOW, [outside, inside]) == [inside]


def test_recall_returns_the_20_newest_of_25_newest_first_rendered(new_york):
    entries = [dialog_ruling(header=f"#{n} merge", time=stamp(n)) for n in range(25)]
    found = rulings.recall("merge", NOW, list(reversed(entries)))
    assert found == entries[:20]
    first = found[0]
    assert rulings.render(first) == (
        f"{first['id']} | {first['time']} | #0 merge | Merge #16 now (Recommended) | "
    )


def test_ruling_recall_with_no_store_directory_prints_nothing(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    r = run_ruling(tmp_path, "recall", "merge", "--dir", str(project))
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert not (project / ".next-auto").exists()


def test_ruling_recall_prints_a_ruling_just_added(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    added = run_add(
        tmp_path,
        "--header", HEADER, "--text", OPTION, "--source", "typed 20:41", "--dir", str(project),
    )  # fmt: skip
    assert added.returncode == 0, added.stderr
    [line] = rulings.load(project / ".next-auto" / "rulings")
    r = run_ruling(tmp_path, "recall", "Merge it?", "--dir", str(project))
    assert (r.returncode, r.stderr) == (0, "")
    assert r.stdout == rulings.render(line) + "\n"


def test_recall_reports_a_time_in_no_zone_it_can_read_and_leaves_that_ruling_out(
    new_york, capsys
):
    # CET is not a name of the pinned zone, so its offset is unknown
    unreadable = dialog_ruling(time="2026-10-21 08:41:03 CET")
    readable = dialog_ruling(time=stamp(1))
    assert rulings.recall("merge", NOW, [unreadable, readable]) == [readable]
    err = capsys.readouterr().err
    assert f"ruling {unreadable['id']}: unreadable time" in err
    assert "CET is not a name of the local zone" in err
