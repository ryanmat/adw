#!/usr/bin/env python3
# Description: The ruling store and its recall (delivery projection Requirements 6 and 7): one JSON
# Description: line per ruling in <project dir>/.next-auto/rulings/<session id>.jsonl. Stdlib only.

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import traceback
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

STORE_SUBDIR = Path(".next-auto") / "rulings"
# the events log's form, as a session-events hook writes it with time.strftime
TIME_FORM = "%Y-%m-%d %H:%M:%S %Z"
# a word a ruling and a new question share before the ruling is recalled: a run of letters of
# at least MIN_WORD, case-insensitive (Requirement 7)
WORD = re.compile(r"[^\W\d_]+")
MIN_WORD = 4
RECALL_WINDOW = timedelta(hours=48)
RECALL_MAX = 20


def ruling_id(session: str, time: str, header: str) -> str:
    """`rul-` plus ten hex of SHA-256 over session|time|header: the same answer written twice
    gets the same id, and two sessions cannot collide."""
    return "rul-" + hashlib.sha256(f"{session}|{time}|{header}".encode()).hexdigest()[:10]


def make_ruling(
    *,
    session: str,
    time: str,
    header: str,
    question: str,
    option: str | None,
    notes: str | None,
    source: str,
) -> dict:
    """One store line, its keys in the order Requirement 6 names them."""
    return {
        "id": ruling_id(session, time, header),
        "time": time,
        "session": session,
        "header": header,
        "question": question,
        "option": option,
        "notes": notes,
        "source": source,
    }


def store_dir(
    project_dir: str | None = None, env: Mapping[str, str] = os.environ
) -> tuple[Path | None, str | None]:
    """The store under the project dir: the one given, else CLAUDE_PROJECT_DIR, the root the
    session started in (D10). Returns (path, None), or (None, why there is none)."""
    base = project_dir or env.get("CLAUDE_PROJECT_DIR")
    if not base:
        return None, "ruling store: CLAUDE_PROJECT_DIR is unset; pass --dir"
    return Path(base) / STORE_SUBDIR, None


def _read(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    entries = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"{path}:{n}: not a ruling line: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            continue
        if not isinstance(entry, dict):
            print(f"{path}:{n}: not a ruling line: a JSON {type(entry).__name__}", file=sys.stderr)
            continue
        entries.append(entry)
    return entries


def append(store: Path, ruling: dict) -> bool:
    """Append the ruling to its session's file unless a line with its id is there already.
    True when the line was written."""
    path = store / f"{ruling['session']}.jsonl"
    if any(entry.get("id") == ruling["id"] for entry in _read(path)):
        return False
    store.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ruling, ensure_ascii=False) + "\n")
    return True


def load(store: Path) -> list[dict]:
    """Every ruling in every store file, file by file in name order; a line that does not parse
    is reported on stderr with its file and line number and the rest still load."""
    entries: list[dict] = []
    for path in sorted(store.glob("*.jsonl")):
        entries.extend(_read(path))
    return entries


def words(text: str) -> set[str]:
    return {w for w in WORD.findall(text.lower()) if len(w) >= MIN_WORD}


def parse_time(value: str) -> datetime:
    """A time in the events log's form as UTC. The zone abbreviation is read through the local
    zone, the one the events log and the store were written in, so EDT and EST both resolve
    across a DST change; UTC and GMT are UTC wherever the reader runs."""
    stamp, _, zone = value.rpartition(" ")
    parsed = time.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    if zone in ("UTC", "GMT"):
        return datetime(*parsed[:6], tzinfo=UTC)
    if zone not in time.tzname:
        raise ValueError(f"{value}: {zone} is not a name of the local zone {time.tzname}")
    isdst = 1 if zone == time.tzname[1] and time.tzname[0] != time.tzname[1] else 0
    local = time.mktime((*parsed[:8], isdst))
    return datetime.fromtimestamp(local, UTC)


def recall(text: str, now: datetime, entries: list[dict]) -> list[dict]:
    """The rulings of the last 48 hours before now whose header, question or notes share a
    word of four letters or more with text, case-insensitive; at most 20, newest first."""
    wanted = words(text)
    found = []
    for entry in entries:
        try:
            when = parse_time(str(entry.get("time") or ""))
        except ValueError:
            print(f"ruling {entry.get('id')}: unreadable time", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            continue
        if now - when > RECALL_WINDOW:
            continue
        fields = " ".join(str(entry.get(k) or "") for k in ("header", "question", "notes"))
        if wanted & words(fields):
            found.append((when, entry))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in found[:RECALL_MAX]]


def render(entry: dict) -> str:
    """`id | time | header | option | notes`, an absent value as empty."""
    keys = ("id", "time", "header", "option", "notes")
    return " | ".join("" if entry.get(k) is None else str(entry[k]) for k in keys)
