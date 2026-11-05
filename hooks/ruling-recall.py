#!/usr/bin/env python3
# Description: PreToolUse hook on AskUserQuestion (delivery projection Requirement 7): denies
# Description: a dialog, the matching rulings of 48 hours as its reason, until each was shown.

from __future__ import annotations

import sys

if sys.version_info < (3, 12):  # noqa: UP036  (the guard runs below the floor by design)
    sys.stderr.write(
        f"adw: python3 is {sys.version_info[0]}.{sys.version_info[1]}; "
        "the plugin needs 3.12 or later on PATH\n"
    )
    sys.exit(0)

import json
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import rulings
from next_auto import RUN_KEY_FORM

HEAD = (
    "Rulings of the last 48 hours sharing a word with this question"
    " (id | time | header | option | notes):"
)
CLOSING = "Asked again, this dialog goes ahead; ask only what these rulings leave open."


def found_for(payload: dict, now: datetime) -> tuple[Path, list[dict]]:
    """The store and the rulings recall finds for the dialog. The text searched is every
    question's header and question."""
    questions = (payload.get("tool_input") or {}).get("questions") or []
    text = " ".join(f"{q.get('header') or ''} {q.get('question') or ''}" for q in questions)
    store, why = rulings.store_dir()
    if store is None:
        raise RuntimeError(why)
    return store, rulings.recall(text, now, rulings.load(store))


def reason_of(payload: dict, now: datetime) -> str | None:
    """The deny reason when a matching ruling has not been shown to the session, after its id is
    appended to <store>/<session>.shown; None when nothing matches or every match was shown."""
    session = str(payload.get("session_id") or "")
    if not RUN_KEY_FORM.fullmatch(session):
        raise ValueError(f"ruling-recall: session_id is not a session id: {session!r}")
    store, found = found_for(payload, now)
    shown_path = store / f"{session}.shown"
    shown = set(shown_path.read_text(encoding="utf-8").split()) if shown_path.is_file() else set()
    fresh = [entry["id"] for entry in found if entry["id"] not in shown]
    if not fresh:
        return None
    # written before the deny prints, so a store that cannot take it lets the question through
    with shown_path.open("a", encoding="utf-8") as f:
        f.write("".join(f"{rid}\n" for rid in fresh))
    return "\n".join([HEAD, *(rulings.render(entry) for entry in found), CLOSING])


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    reason = reason_of(payload, datetime.now(UTC))
    if reason is None:
        return 0
    # deny only: the hooks reference shows a deny's reason to the model, not to the human;
    # never allow
    # and never updatedInput, which with allow would answer the question in the owner's place
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8") + b"\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # a failure never holds a question: the traceback names why, and the question goes ahead
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)
