#!/usr/bin/env python3
# Description: PostToolUse hook on AskUserQuestion (delivery projection Requirement 6): one ruling
# Description: store line per answered question, each id handed back to the session as context.

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import rulings
from next_auto import RUN_KEY_FORM


def labels_only(answer: str, labels: list[str], multi: bool) -> bool:
    """True when the answer is one of the question's labels or, on a multi-select question,
    labels joined by ", " as Claude Code joins them (hooks reference, AskUserQuestion input).
    Labels are matched whole, since a label can hold a comma ("Carry it, read only")."""
    if answer in labels:
        return True
    return multi and any(
        answer.startswith(label + ", ") and labels_only(answer[len(label) + 2 :], labels, multi)
        for label in labels
    )


def rulings_of(payload: dict, stamp: str) -> list[dict]:
    """One ruling per question of the dialog. The option is the tool_response's `answers` value
    for the question's text (D2): a label, labels joined by commas, or the free text typed in
    their place. Notes are that question's `annotations` entry's notes and a free-text answer,
    which recall searches and the option it does not. A response with no `answers` keeps the
    header and time with a null option, and the session writes the answer with ruling add."""
    session = str(payload.get("session_id") or "")
    # the session id names the store file and enters the ruling id
    if not RUN_KEY_FORM.fullmatch(session):
        raise ValueError(f"ruling-log: session_id is not a session id: {session!r}")
    response = payload.get("tool_response") or {}
    answers = response.get("answers") or {}
    annotations = response.get("annotations") or {}
    entries = []
    for question in (payload.get("tool_input") or {}).get("questions") or []:
        text = str(question.get("question") or "")
        option = answers.get(text)
        labels = [str(o.get("label") or "") for o in question.get("options") or []]
        multi = bool(question.get("multiSelect"))
        typed = None
        if option is not None and not labels_only(option, labels, multi):
            typed = option
        note = (annotations.get(text) or {}).get("notes") or None
        entries.append(
            rulings.make_ruling(
                session=session,
                time=stamp,
                header=str(question.get("header") or ""),
                question=text,
                option=option,
                notes="; ".join(part for part in (note, typed) if part) or None,
                source="dialog",
            )
        )
    return entries


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    entries = rulings_of(payload, time.strftime(rulings.TIME_FORM))
    if not entries:
        return 0
    store, why = rulings.store_dir()
    if store is None:
        raise RuntimeError(why)
    # the store directory is the opt-in: a project without .next-auto/rulings/ is outside the
    # loop, so its dialogs are not recorded and recall has nothing to deny on
    if not store.is_dir():
        return 0
    # two questions of one dialog under one header share an id (session, time and header), so
    # the second answer cannot be stored; it is reported and left out of the context
    seen: set[str] = set()
    stored = []
    for n, entry in enumerate(entries, start=1):
        if entry["id"] in seen:
            print(
                f"ruling-log: question {n} repeats the header {entry['header']!r} of an earlier"
                f" question in this dialog, so its answer shares the id {entry['id']} and is not"
                f" stored: {entry['option']!r}",
                file=sys.stderr,
            )
            continue
        seen.add(entry["id"])
        rulings.append(store, entry)
        stored.append(entry)
    context = "; ".join(f"ruling {e['id']} {e['time']} {e['header']}" for e in stored)
    out = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8") + b"\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # a hook that cannot write its line lets the session go on; the traceback names why,
        # and the handoff gate catches the missing id at close
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)
