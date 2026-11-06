#!/usr/bin/env python3
# Description: Plan-grammar tool for /adw:next: validate, migrate, status, next, brief, set, append,
# Description: plan-hash, ruling. Stdlib only; edits are byte-preserving outside the changed span.

from __future__ import annotations

import sys

if sys.version_info < (3, 12):  # noqa: UP036  (the guard runs below the floor by design)
    sys.stderr.write(
        f"adw: python3 is {sys.version_info[0]}.{sys.version_info[1]}; "
        "the plugin needs 3.12 or later on PATH\n"
    )
    sys.exit(2)

import argparse
import hashlib
import json
import re
import shlex
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import rulings

# ---- grammar constants ------------------------------------------------------

STEP_RE = re.compile(r"^### Step (\d+): (.+?)\s*$")
H1_RE = re.compile(r"^# .+")
H2_RE = re.compile(r"^## .+")
CONV_HEADER = "## Conventions binding every step"
DECISIONS_HEADER = "## Decisions"
FINDINGS_HEADER = "## Findings"
CONV_KEY_RE = re.compile(r"^- ([A-Za-z][A-Za-z-]*): (.+?)\s*$")
# optional Conventions key: the delivery-record appender's command followed by the record's
# slug; the runner calls it, and the brief never shows it to the implementer
RECORD_KEY = "Record"
# optional Conventions key: comma-separated globs naming the test files prove-red takes; the
# brief keeps it, since the implementer needs to know what counts as a test
TEST_FILES_KEY = "Test-files"
# optional Conventions key: comma-separated globs naming the CLAUDE.md and .claude/ paths the
# attended check and commit admit; auto mode refuses those paths whatever the plan says
TOOLCHAIN_FILES_KEY = "Toolchain-files"
# optional Conventions key: the tracker id the run's branch name carries; the brief omits it,
# as it omits Record
ISSUE_KEY = "Issue"
ISSUE_RE = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")
# optional Conventions key: the resource classes the plan's steps touch, which check begin and
# start compare with every other live plan of the repository; docs never counts
SHARES_KEY = "Shares"
SHARES_CLASSES = ("openapi", "dapi", "migration", "infra", "docs")
# optional Conventions key: the per-step cap, in changed lines, the auto gate applies to this
# plan's steps and validate holds every Size: line under; absent, the default below. The brief
# keeps it, since the implementer builds to it
STEP_SIZE_KEY = "Step-size"
STEP_SIZE_DEFAULT = 300
# the run cap `/adw:next auto` stops at, which no plan's per-step cap may exceed; next_auto.py
# reads it from here, so the two caps have one source
RUN_SIZE_LIMIT = 500
# a step's optional Closes: line names the requirement ids the step closes
REQUIREMENT_RE = re.compile(r"^R[0-9]+$")
# the files the auto gate lets a step change only when the brief names them (next_auto's
# check_gate_config reads this tuple); one source with the plan-time warning below
GATE_CONFIG_FILES = (
    "conftest.py",
    "pytest.ini",
    "tox.ini",
    "pyproject.toml",
    "ruff.toml",
    ".ruff.toml",
    "setup.cfg",
    ".pre-commit-config.yaml",
    "CLAUDE.md",
    "Makefile",
)
# the stems validate warns about when an open step names one without its basename: the four
# that are not ordinary prose (ruff, setup, pytest, Makefile and CLAUDE are left out, since
# step text names the ruff command, test setup and pytest routinely and Makefile is its own
# basename)
GATE_CONFIG_STEMS = (
    ("pyproject", "pyproject.toml"),
    ("conftest", "conftest.py"),
    ("pre-commit-config", ".pre-commit-config.yaml"),
    ("tox", "tox.ini"),
)

STATES = ("PENDING", "IN PROGRESS", "DONE", "FAILED", "BLOCKED")
# a step's header lines: each at most once, written by the brief in canonical form and
# stripped from its prose
STEP_LINE_PREFIXES = (
    "Status:",
    "Eval:",
    "Mutation:",
    "Size:",
    "Closes:",
    "Deletes:",
    "Guard-mutation:",
    "Test-repo:",
    "Test:",
)
PYTHON_MARKERS = ("uv", "pytest", "ruff", "python")
# the token migrate writes into Conventions for the operator to replace; validate
# refuses a plan that still carries it, so an unfilled default never reaches a run
CHUNK_CAP = 5  # steps per "## Chunk"; the attended loop's batch is bounded by it
PLACEHOLDER = "PACKAGE"
PLACEHOLDER_RE = re.compile(rf"(?<![A-Za-z0-9_]){PLACEHOLDER}(?![A-Za-z0-9_])")

# CLI state token -> (STATE, note-wrapper)
CLI_STATES = {
    "pending": ("PENDING", None),
    "in-progress": ("IN PROGRESS", None),
    "done": ("DONE", "{note}"),
    "failed": ("FAILED", "{note}"),
    "blocked-decision": ("BLOCKED", "decision: {note}"),
    "blocked-apparatus": ("BLOCKED", "apparatus: {note}"),
}


# ---- model ------------------------------------------------------------------


@dataclass
class Step:
    number: int
    title: str
    state: str
    note: str
    eval: bool
    mutation: str
    body: str
    header_line: int  # 1-based line number of the "### Step N:" line
    status_line: int | None  # 1-based line of the "Status:" line, if present
    chunk: str | None = None  # text of the nearest preceding "## Chunk ..." header, or None
    last_in_chunk: bool = False  # final step of its chunk, or of the plan when unchunked
    # the Test-repo exemption: where a step's tests live and the command that runs them from
    # this machine, when the step builds in another repository; None when the line is absent,
    # "" when it is present with no value, so validate can name the empty line
    test_repo: str | None = None
    test_cmd: str | None = None
    # the requirement ids of the step's Closes: line, empty without the line
    closes: list[str] = field(default_factory=list)
    # the test files the step's Deletes: line names, empty without the line
    deletes: list[str] = field(default_factory=list)
    # (path, old, new) of the step's Guard-mutation: line; None without the line or when
    # the line lacks its shape, which validate names
    guard_mutation: tuple[str, str, str] | None = None
    # the step's Size: line, its estimated changed lines (insertions plus deletions, tests
    # included), the count the auto gate measures; None without the line or when the value is
    # not a positive integer, which validate names
    size: int | None = None


@dataclass
class Plan:
    title: str
    conventions: dict[str, str]
    steps: list[Step]
    decisions: str
    findings: str
    has_decisions: bool = False
    has_findings: bool = False
    conv_lines: dict[str, int] = field(default_factory=dict)  # key -> 1-based line


# ---- parsing ----------------------------------------------------------------


def _preceding_h2(lines: list[str], header_idx: int) -> str | None:
    """The raw text of the nearest H2 line above a step header, or None."""
    for j in range(header_idx - 1, -1, -1):
        if H2_RE.match(lines[j]):
            return lines[j].rstrip()
    return None


def _is_chunk_header(h2: str | None) -> bool:
    """"## Chunk" or "## Chunk ..." is a chunk header; "## Chunking rationale" is a bare H2."""
    if h2 is None:
        return False
    text = h2[3:].strip()
    return text == "Chunk" or text.startswith("Chunk ")


def _enclosing_chunk(lines: list[str], header_idx: int) -> str | None:
    """The nearest preceding H2's text when it starts with "Chunk", else None.

    A step under a bare H2 such as "## Notes" has no chunk even when other steps do;
    validate reports that as a coverage gap once the plan uses chunks at all.
    """
    h2 = _preceding_h2(lines, header_idx)
    return h2[3:].strip() if _is_chunk_header(h2) else None



def _parse_status(value: str) -> tuple[str, str]:
    """Return (state, note). First token(s) are the state; note is the parenthetical body."""
    v = value.strip()
    if v.startswith("IN PROGRESS"):
        # a parenthetical is the note, as for the other states (a carried record, 3.5.11 R2);
        # any other remainder stays as it was read
        rest = v[len("IN PROGRESS") :].strip()
        m = re.match(r"^\((.*)\)\s*$", rest, re.S)
        return "IN PROGRESS", m.group(1) if m else rest
    for st in ("PENDING", "DONE", "FAILED", "BLOCKED"):
        if v == st or v.startswith(st + " ") or v.startswith(st + "("):
            rest = v[len(st) :].strip()
            m = re.match(r"^\((.*)\)\s*$", rest, re.S)
            if rest and not m:
                # words after the state outside a parenthetical (`DONE WITH ONE RED CASE`)
                # are not a note; the whole prefix is an unknown state so validate names it
                return " ".join(v.split("(")[0].split()), ""
            return st, m.group(1) if m else ""
    # unknown state: return the first word as the state so validation can flag it
    return v.split()[0] if v.split() else "", ""


def parse_plan(text: str) -> Plan:
    lines = text.splitlines()
    title = ""
    for ln in lines:
        if ln.strip():
            title = ln.rstrip()
            break

    conventions: dict[str, str] = {}
    conv_lines: dict[str, int] = {}
    in_conv = False
    for i, ln in enumerate(lines, start=1):
        if ln.rstrip() == CONV_HEADER:
            in_conv = True
            continue
        if in_conv:
            if H2_RE.match(ln):
                in_conv = False
                continue
            m = CONV_KEY_RE.match(ln)
            if m:
                conventions[m.group(1)] = m.group(2)
                conv_lines[m.group(1)] = i

    steps: list[Step] = []
    # index step header positions
    headers = [(i, m) for i, ln in enumerate(lines) for m in [STEP_RE.match(ln)] if m]
    for idx, (start, m) in enumerate(headers):
        end = headers[idx + 1][0] if idx + 1 < len(headers) else len(lines)
        # a step body also ends at the next H2 section (Decisions/Findings/prose)
        body_end = end
        for j in range(start + 1, end):
            if H2_RE.match(lines[j]):
                body_end = j
                break
        block = lines[start:body_end]
        chunk = _enclosing_chunk(lines, start)
        state, note, eval_flag, mutation = "", "", False, ""
        test_repo = test_cmd = None
        closes: list[str] = []
        deletes: list[str] = []
        guard_mutation = None
        size: int | None = None
        status_line = None
        for k, bl in enumerate(block):
            if bl.startswith("Status:"):
                state, note = _parse_status(bl[len("Status:") :])
                status_line = start + k + 1
            elif bl.startswith("Eval:"):
                eval_flag = bl[len("Eval:") :].strip() == "yes"
            elif bl.startswith("Mutation:"):
                mutation = bl[len("Mutation:") :].strip()
            elif bl.startswith("Test-repo:"):
                test_repo = bl[len("Test-repo:") :].strip()
            elif bl.startswith("Test:"):
                test_cmd = bl[len("Test:") :].strip()
            elif bl.startswith("Closes:"):
                closes = _split_items(bl[len("Closes:") :])
            elif bl.startswith("Deletes:"):
                deletes = _split_items(bl[len("Deletes:") :])
            elif bl.startswith("Guard-mutation:"):
                guard_mutation = _split_guard_mutation(bl[len("Guard-mutation:") :])
            elif bl.startswith("Size:"):
                size = _parse_size(bl[len("Size:") :])
        body = "\n".join(block[1:]).strip("\n")
        steps.append(
            Step(
                number=int(m.group(1)),
                title=m.group(2).strip(),
                state=state,
                note=note,
                eval=eval_flag,
                mutation=mutation,
                body=body,
                header_line=start + 1,
                status_line=status_line,
                chunk=chunk,
                test_repo=test_repo,
                test_cmd=test_cmd,
                closes=closes,
                deletes=deletes,
                guard_mutation=guard_mutation,
                size=size,
            )
        )
    # a step is last in its chunk when the plan ends or the next step's chunk differs;
    # an unchunked plan therefore marks only its final step
    for i, s in enumerate(steps):
        s.last_in_chunk = i == len(steps) - 1 or steps[i + 1].chunk != s.chunk

    def _section(header: str) -> tuple[str, bool]:
        present = False
        collected: list[str] = []
        grabbing = False
        for ln in lines:
            if ln.rstrip() == header:
                present = True
                grabbing = True
                continue
            if grabbing:
                if H2_RE.match(ln):
                    break
                collected.append(ln)
        return "\n".join(collected).strip(), present

    decisions, has_dec = _section(DECISIONS_HEADER)
    findings, has_find = _section(FINDINGS_HEADER)

    return Plan(
        title=title,
        conventions=conventions,
        steps=steps,
        decisions=decisions,
        findings=findings,
        has_decisions=has_dec,
        has_findings=has_find,
        conv_lines=conv_lines,
    )


# ---- validation -------------------------------------------------------------


def command_tokens(value: str) -> list[str]:
    """A Conventions command split as shlex splits it, quotes parsed first, with each run of
    shell punctuation outside quotes as a token of its own: `a&&b` is three tokens, and
    `sh -c 'a && b'` is three with the chain inside the last. A `#` is part of its word, as
    `shlex.split` reads it. A quoted or escaped argument made only of punctuation, as in
    `grep ';'` or `-exec ... \\;`, reads as an operator too."""
    lexer = shlex.shlex(value, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def shell_operator(value: str) -> str | None:
    """The first token in a command that chains or pipes (`&&`, `|`, `;` or a run holding
    one), which a command run without a shell passes to its program as an argument."""
    for tok in command_tokens(value):
        if not tok.strip("();<>|&") and ("&&" in tok or "|" in tok or ";" in tok):
            return tok
    return None


def validate_warnings(text: str) -> list[tuple[int, str]]:
    """The plan-time warnings, never refusals: an open step (PENDING or IN PROGRESS) whose text
    names a gate-config stem as a whole word not followed by its extension, with the basename
    nowhere in the step, would fail the gate's config check at its edit."""
    warnings: list[tuple[int, str]] = []
    for step in parse_plan(text).steps:
        if step.state not in ("PENDING", "IN PROGRESS"):
            continue
        step_text = f"{step.title}\n{step.body}"
        for stem, basename in GATE_CONFIG_STEMS:
            if basename in step_text:
                continue
            ext = basename[basename.index(".", 1) :]
            pattern = rf"(?<![A-Za-z0-9_-]){re.escape(stem)}(?![A-Za-z0-9_-])(?!{re.escape(ext)})"
            if re.search(pattern, step_text):
                msg = (
                    f"Step {step.number}: names {stem} without its basename {basename}; the "
                    "gate authorizes a config edit only when the step text contains the basename"
                )
                warnings.append((step.header_line, msg))
    return warnings


# an H2 whose first word is Decisions or Findings: the header, or a near miss of it
_NEAR_MISS_RE = re.compile(r"^## (Decisions|Findings)(?![A-Za-z0-9_-])")


def near_miss_headers(lines: list[str]) -> list[tuple[int, str, str]]:
    """Each H2 whose first word after `## ` is Decisions or Findings and whose text is not the
    header it stands for (3.5.11 spec R3, ryanmat/adw#8): a hand-written `## Decisions open`,
    or the other tree's form of the header. Returns (1-based line, the text found, the header)
    in document order."""
    out: list[tuple[int, str, str]] = []
    for i, ln in enumerate(lines, 1):
        text = ln.rstrip()
        m = _NEAR_MISS_RE.match(text)
        if m is None:
            continue
        header = DECISIONS_HEADER if m.group(1) == "Decisions" else FINDINGS_HEADER
        if text != header:
            out.append((i, text, header))
    return out


def missing_near_misses(text: str) -> list[tuple[int, str, str]]:
    """The near miss validate names and migrate renames: the first near miss of each trailing
    section the plan lacks, in document order. A section whose header is present keeps every
    near miss of it as the prose it is, so migrate never writes a second header, and a second
    near miss of one absent header is left for the operator."""
    plan = parse_plan(text)
    absent = {
        header
        for present, header in (
            (plan.has_decisions, DECISIONS_HEADER),
            (plan.has_findings, FINDINGS_HEADER),
        )
        if not present
    }
    out: list[tuple[int, str, str]] = []
    for ln, found, header in near_miss_headers(text.splitlines()):
        if header in absent and all(h != header for _l, _f, h in out):
            out.append((ln, found, header))
    return out


def validate_text(text: str) -> list[tuple[int, str]]:
    reasons: list[tuple[int, str]] = []
    lines = text.splitlines()
    if not lines or not H1_RE.match(next((ln for ln in lines if ln.strip()), "")):
        reasons.append((1, "first non-empty line must be an H1 title ('# ...')"))

    plan = parse_plan(text)

    # conventions
    conv_line = next((i for i, ln in enumerate(lines, 1) if ln.rstrip() == CONV_HEADER), None)
    if conv_line is None:
        reasons.append((1, f"missing section: {CONV_HEADER!r}"))
    else:
        for key in ("Test", "Lint"):
            if key not in plan.conventions:
                reasons.append((conv_line, f"Conventions missing required key: {key}"))
                continue
            ln = plan.conv_lines.get(key, conv_line)
            try:
                op = shell_operator(plan.conventions[key])
            except ValueError as exc:
                reasons.append((ln, f"Conventions {key} does not split as a command: {exc}"))
                continue
            if op:
                msg = (
                    f"Conventions {key} carries {op!r}, which reads as a shell operator; the "
                    f"engine runs {key} without a shell, so chain commands inside one quoted "
                    "argument, as in sh -c 'set -e; a; b', or uv run sh -c '...' in a Python plan"
                )
                reasons.append((ln, msg))
        for key, val in plan.conventions.items():
            if PLACEHOLDER_RE.search(val):
                ln = plan.conv_lines.get(key, conv_line)
                msg = f"Conventions {key} still carries the {PLACEHOLDER} placeholder; fill it in"
                reasons.append((ln, msg))
        for i in range(conv_line + 1, len(lines) + 1):
            ln = lines[i - 1]
            if H2_RE.match(ln):
                break
            if ln.startswith(f"- {TEST_FILES_KEY}:") and not _split_globs(ln.split(":", 1)[1]):
                msg = f"Conventions {TEST_FILES_KEY} needs at least one comma-separated glob"
                reasons.append((i, msg + " pattern"))
            if ln.startswith(f"- {TOOLCHAIN_FILES_KEY}:") and not _split_globs(
                ln.split(":", 1)[1]
            ):
                msg = f"Conventions {TOOLCHAIN_FILES_KEY} needs at least one comma-separated glob"
                reasons.append((i, msg + " pattern"))
            if ln.startswith(f"- {ISSUE_KEY}:"):
                value = ln.split(":", 1)[1].strip()
                if not ISSUE_RE.match(value):
                    msg = f"Conventions: {ISSUE_KEY}: {value} is not a tracker id such as DOC-123"
                    reasons.append((i, msg))
            if ln.startswith(f"- {SHARES_KEY}:"):
                classes = _split_globs(ln.split(":", 1)[1])
                unknown = [c for c in classes if c not in SHARES_CLASSES]
                if not classes:
                    reasons.append((i, f"Conventions: {SHARES_KEY}: names no class"))
                elif unknown:
                    known = ", ".join(SHARES_CLASSES)
                    msg = f"Conventions: {SHARES_KEY}: {unknown[0]} is not a resource class"
                    reasons.append((i, f"{msg} ({known})"))
            if ln.startswith(f"- {RECORD_KEY}:"):
                reason = _record_reason(ln.split(":", 1)[1])
                if reason:
                    reasons.append((i, reason))
            if ln.startswith(f"- {STEP_SIZE_KEY}:"):
                value = ln.split(":", 1)[1].strip()
                size = _parse_size(value)
                if not value:
                    reasons.append((i, f"Conventions: {STEP_SIZE_KEY}: is empty"))
                elif size is None:
                    msg = f"Conventions: {STEP_SIZE_KEY}: {value} is not a positive line count"
                    reasons.append((i, msg))
                elif size > RUN_SIZE_LIMIT:
                    msg = f"Conventions: {STEP_SIZE_KEY}: {size} is over the "
                    reasons.append((i, msg + f"{RUN_SIZE_LIMIT}-line run cap"))
        conv_blob = " ".join(plan.conventions.values())
        is_python = any(mk in conv_blob for mk in PYTHON_MARKERS)
        if is_python:
            for key in ("Test", "Lint"):
                val = plan.conventions.get(key, "")
                if val and not val.startswith("uv run"):
                    ln = plan.conv_lines.get(key, conv_line)
                    reasons.append((ln, f"{key} must start with 'uv run' for a Python plan"))

    # steps
    if not plan.steps:
        reasons.append((1, "plan has no '### Step N:' steps"))
    # the per-step cap every Size: line is held under, read once for the plan
    limit = step_size_limit(plan)
    prev = None
    for s in plan.steps:
        if prev is not None and s.number <= prev:
            msg = f"step numbers must be strictly increasing (saw {s.number} after {prev})"
            reasons.append((s.header_line, msg))
        prev = s.number
        if s.status_line is None:
            reasons.append((s.header_line, f"Step {s.number}: missing Status: line"))
        else:
            if s.state not in STATES:
                msg = f"Step {s.number}: unknown Status state {s.state!r}"
                reasons.append((s.status_line, msg))
            elif s.state in ("DONE", "FAILED") and not s.note:
                msg = f"Step {s.number}: {s.state} requires a parenthetical note"
                reasons.append((s.status_line, msg))
            elif s.state == "BLOCKED" and not (
                s.note.startswith("decision:") or s.note.startswith("apparatus:")
            ):
                msg = f"Step {s.number}: BLOCKED requires '(decision: ...)' or '(apparatus: ...)'"
                reasons.append((s.status_line, msg))
        # each metadata line exactly once: the parser keeps the last one it sees, so a
        # second copy is ambiguous rather than harmless
        body_end = _step_body_end(lines, s.header_line)
        counts: dict[str, int] = {}
        for prefix in STEP_LINE_PREFIXES:
            n_lines = sum(
                1 for i in range(s.header_line, body_end + 1) if lines[i - 1].startswith(prefix)
            )
            counts[prefix] = n_lines
            if n_lines > 1:
                msg = f"Step {s.number}: more than one {prefix} line ({n_lines})"
                reasons.append((s.header_line, msg))
        # Eval line
        eval_present = any(
            lines[i - 1].startswith("Eval:")
            for i in range(s.header_line, _step_body_end(lines, s.header_line) + 1)
        )
        if not eval_present:
            reasons.append((s.header_line, f"Step {s.number}: missing Eval: line"))
        else:
            eval_val = _find_line_value(lines, s.header_line, "Eval:")
            if eval_val not in ("yes", "no"):
                reasons.append((s.header_line, f"Step {s.number}: Eval must be 'yes' or 'no'"))
        # Mutation line
        if not s.mutation:
            reasons.append((s.header_line, f"Step {s.number}: missing Mutation: line"))
        elif not (s.mutation == "required" or s.mutation.startswith("n/a")):
            msg = f"Step {s.number}: Mutation must be 'required' or 'n/a (<reason>)'"
            reasons.append((s.header_line, msg))
        elif s.mutation.startswith("n/a") and not re.search(r"n/a\s*\(.+\)", s.mutation):
            msg = f"Step {s.number}: Mutation n/a requires a reason in parens"
            reasons.append((s.header_line, msg))
        # the Test-repo exemption's lines come as a pair, each with a value: a step may not
        # claim the exemption without saying what runs in place of a test here
        has_repo, has_cmd = s.test_repo is not None, s.test_cmd is not None
        if has_repo and not has_cmd:
            reasons.append((s.header_line, f"Step {s.number}: Test-repo: without Test:"))
        elif has_cmd and not has_repo:
            reasons.append((s.header_line, f"Step {s.number}: Test: without Test-repo:"))
        if has_repo and not s.test_repo:
            reasons.append((s.header_line, f"Step {s.number}: Test-repo: is empty"))
        if has_cmd and not s.test_cmd:
            reasons.append((s.header_line, f"Step {s.number}: Test: is empty"))
        # the Closes: line names at least one requirement id, each of the R12 shape
        closes_value = _find_line_value(lines, s.header_line, "Closes:")
        if counts["Closes:"] and not _split_items(closes_value):
            reasons.append((s.header_line, f"Step {s.number}: Closes: is empty"))
        for rid in _split_items(closes_value) if counts["Closes:"] else []:
            if not REQUIREMENT_RE.match(rid):
                msg = f"Step {s.number}: Closes: {rid} is not a requirement id such as R12"
                reasons.append((s.header_line, msg))
        # the Deletes: line names at least one path, each relative and inside the repository
        deletes_value = _find_line_value(lines, s.header_line, "Deletes:")
        if counts["Deletes:"] and not _split_items(deletes_value):
            reasons.append((s.header_line, f"Step {s.number}: Deletes: is empty"))
        for dpath in _split_items(deletes_value) if counts["Deletes:"] else []:
            pure = PurePosixPath(dpath)
            if pure.is_absolute() or ".." in pure.parts:
                msg = f"Step {s.number}: Deletes: {dpath} is not a relative path in the repository"
                reasons.append((s.header_line, msg))
        # the Guard-mutation: line splits into a path, the old text and its replacement
        guard_value = _find_line_value(lines, s.header_line, "Guard-mutation:")
        if counts["Guard-mutation:"] and _split_guard_mutation(guard_value) is None:
            msg = f"Step {s.number}: Guard-mutation: needs <path>: <old> => <new>"
            reasons.append((s.header_line, msg))
        # the Size: line is the step's estimated changed lines, a positive integer
        size_value = _find_line_value(lines, s.header_line, "Size:")
        if counts["Size:"] and not size_value:
            reasons.append((s.header_line, f"Step {s.number}: Size: is empty"))
        elif counts["Size:"] and _parse_size(size_value) is None:
            msg = f"Step {s.number}: Size: {size_value} is not a positive line count"
            reasons.append((s.header_line, msg))
        elif counts["Size:"] and _parse_size(size_value) > limit:
            # the plan-time refusal: a step the plan already knows is over the gate never
            # reaches a run
            msg = f"Step {s.number}: Size: {size_value} is over the {limit}-line per-step cap"
            reasons.append((s.header_line, msg + "; split the step"))

    # eval-dependent conventions
    if any(s.eval for s in plan.steps):
        for key in ("Eval", "Eval-codes", "Holdout"):
            if key not in plan.conventions:
                msg = f"a step is Eval: yes but Conventions missing {key}"
                reasons.append((conv_line or 1, msg))
        codes = plan.conventions.get("Eval-codes", "")
        if codes and codes not in ("apparatus", "exit0"):
            ln = plan.conv_lines.get("Eval-codes", conv_line or 1)
            reasons.append((ln, "Eval-codes must be 'apparatus' or 'exit0'"))

    # chunks: once any "## Chunk" header exists every step sits under one, and no chunk
    # holds more than CHUNK_CAP steps; both keep the attended batch bounded
    chunk_lines = {
        ln[3:].strip(): i
        for i, ln in enumerate(lines, 1)
        if ln.startswith("## ") and _is_chunk_header(ln)
    }
    if chunk_lines:
        for s in plan.steps:
            if s.chunk is None:
                h2 = _preceding_h2(lines, s.header_line - 1) or "no H2"
                msg = (
                    f"Step {s.number}: sits under '{h2}', which is not a '## Chunk' header, "
                    "but the plan uses chunks"
                )
                reasons.append((s.header_line, msg))
        for chunk, ln in chunk_lines.items():
            n = sum(1 for s in plan.steps if s.chunk == chunk)
            if n > CHUNK_CAP:
                reasons.append((ln, f"{chunk} holds {n} steps; the cap is {CHUNK_CAP}"))

    # trailing sections; a near miss of the header is named at its own line, so the one-line
    # fix is in the reason (3.5.11 spec R3), else the section is missing at the end
    misses = {header: (ln, found) for ln, found, header in missing_near_misses(text)}
    sections = ((plan.has_decisions, DECISIONS_HEADER), (plan.has_findings, FINDINGS_HEADER))
    for present, header in sections:
        if present:
            continue
        if header in misses:
            ln, found = misses[header]
            reasons.append(
                (ln, f"missing section: {header!r} (found {found!r} at line {ln}; rename it)")
            )
        else:
            reasons.append((len(lines), f"missing section: {header!r}"))

    reasons.sort(key=lambda r: r[0])
    return reasons


def _step_body_end(lines: list[str], header_line: int) -> int:
    """1-based line of the last line belonging to the step whose header is at header_line."""
    for j in range(header_line, len(lines)):
        if STEP_RE.match(lines[j]) or H2_RE.match(lines[j]):
            return j  # 1-based line before this (j is 0-based index == 1-based of previous)
    return len(lines)


def _find_line_value(lines: list[str], header_line: int, prefix: str) -> str:
    end = _step_body_end(lines, header_line)
    for i in range(header_line, end + 1):
        if i - 1 < len(lines) and lines[i - 1].startswith(prefix):
            return lines[i - 1][len(prefix) :].strip()
    return ""


# ---- status / next / brief --------------------------------------------------


def to_json(plan: Plan) -> dict:
    counts: dict[str, int] = {}
    for s in plan.steps:
        counts[s.state] = counts.get(s.state, 0) + 1
    return {
        "title": plan.title,
        "conventions": plan.conventions,
        "steps": [
            {
                "number": s.number,
                "title": s.title,
                "state": s.state,
                "note": s.note,
                "eval": s.eval,
                "mutation": s.mutation,
                "size": s.size,
                "chunk": s.chunk,
                "test_repo": s.test_repo,
                "test_cmd": s.test_cmd,
                "closes": s.closes,
                "deletes": s.deletes,
                "guard_mutation": (
                    dict(zip(("path", "old", "new"), s.guard_mutation, strict=True))
                    if s.guard_mutation
                    else None
                ),
            }
            for s in plan.steps
        ],
        "counts": counts,
    }


def next_step(plan: Plan) -> Step | None:
    for s in plan.steps:
        if s.state in ("PENDING", "IN PROGRESS"):
            return s
    return None


NOTHING_TO_RUN = "Nothing to run: every step is DONE, FAILED, or BLOCKED."
CONTEXT_STOP_PCT = 60


def chunk_label(chunk: str) -> str:
    """The label the announcement uses: "Chunk B: Tool" -> "B"."""
    return chunk[len("Chunk") :].split(":", 1)[0].strip()


def _span(members: list[int]) -> str:
    """"Step 5", "Steps 5 to 8" when contiguous, else "Steps 2 and 4" or "Steps 2, 4, and 5"."""
    if len(members) == 1:
        return f"Step {members[0]}"
    if members == list(range(members[0], members[-1] + 1)):
        return f"Steps {members[0]} to {members[-1]}"
    if len(members) == 2:
        return f"Steps {members[0]} and {members[1]}"
    return "Steps " + ", ".join(str(n) for n in members[:-1]) + f", and {members[-1]}"


def batch_sentence(
    members: list[int],
    boundary: str,
    label: str | None,
    context_pct: int | None,
    blocker: Step | None = None,
) -> str:
    ctx = f" or at {CONTEXT_STOP_PCT} percent context" if context_pct is not None else ""
    if boundary == "blocked":
        where = f"before Step {blocker.number} ({blocker.state})"
        return f"Running {_span(members)}, stopping {where}{ctx}."
    where = f"the end of Chunk {label}" if boundary == "chunk" else "the end of the plan"
    tail = " (the reviewer runs there)" if boundary == "chunk" else ""
    return f"Running {_span(members)}, stopping at {where}{ctx}{tail}."


def batch_steps(plan: Plan, context_pct: int | None = None) -> dict:
    """The unfinished steps from the next one through the end of its chunk (or the plan).

    Reads the plan and writes nothing. The loop quotes the sentence as its announcement,
    so the numbers are computed here, not judged there.
    """
    first = next_step(plan)
    if first is None:
        return {"steps": [], "sentence": NOTHING_TO_RUN}
    label = chunk_label(first.chunk) if first.chunk else None
    # a FAILED or BLOCKED step anywhere before the next unfinished one parks the plan, so a
    # chunk that ends in an unmade decision never lets the next chunk open (Requirement 4)
    parked = next(
        (s for s in plan.steps if s.number < first.number and s.state in ("FAILED", "BLOCKED")),
        None,
    )
    # walk the whole chunk (or the whole unchunked plan) in order: a FAILED or BLOCKED step
    # anywhere in it ends the batch there, so the loop never builds past an unresolved stop
    scope = plan.steps if first.chunk is None else [s for s in plan.steps if s.chunk == first.chunk]
    members: list[int] = []
    blocker: Step | None = parked
    for s in scope if parked is None else []:
        if s.state in ("FAILED", "BLOCKED"):
            blocker = s
            break
        if s.state in ("PENDING", "IN PROGRESS"):
            members.append(s.number)
    if blocker is not None and not members:
        unit = f"Chunk {label}" if label else "the plan"
        return {
            "steps": [],
            "blocker": blocker.number,
            "sentence": (
                f"Nothing to run: Step {blocker.number} is {blocker.state}; "
                f"resolve it before {unit} continues."
            ),
        }
    boundary = "blocked" if blocker else ("chunk" if first.chunk is not None else "plan")
    return {
        "steps": members,
        "first": members[0],
        "last": members[-1],
        "boundary": boundary,
        "chunk": first.chunk,
        "chunk_label": label,
        "context_pct": context_pct,
        "sentence": batch_sentence(members, boundary, label, context_pct, blocker),
    }


def render_brief(plan: Plan, n: int, text: str | None = None) -> str:
    step = next((s for s in plan.steps if s.number == n), None)
    if step is None:
        raise SystemExit(f"no step {n} in plan")
    conv = [CONV_HEADER]
    for k, v in plan.conventions.items():
        if k not in (RECORD_KEY, ISSUE_KEY):
            conv.append(f"- {k}: {v}")
    header = f"### Step {step.number}: {step.title}"
    block = [header]
    block.append(f"Status: {step.state}" + (f" ({step.note})" if step.note else ""))
    block.append(f"Eval: {'yes' if step.eval else 'no'}")
    block.append(f"Mutation: {step.mutation}")
    if step.size is not None:
        block.append(f"Size: {step.size}")
    if step.closes:
        block.append(f"Closes: {', '.join(step.closes)}")
    if step.deletes:
        block.append(f"Deletes: {', '.join(step.deletes)}")
    if step.guard_mutation:
        gpath, old, new = step.guard_mutation
        block.append(f"Guard-mutation: {gpath}: {old} => {new}")
    if step.test_repo is not None:
        block.append(f"Test-repo: {step.test_repo}")
    if step.test_cmd is not None:
        block.append(f"Test: {step.test_cmd}")
    # The body holds the step's own header lines; the brief has just written them in
    # canonical form, so only the prose follows.
    prose = "\n".join(
        ln for ln in step.body.splitlines() if not ln.startswith(STEP_LINE_PREFIXES)
    ).strip("\n")
    if prose:
        block.append("")
        block.append(prose)
    return "\n".join(conv) + "\n\n" + "\n".join(block) + "\n"


def render_context(plan: Plan, n: int) -> str:
    """What step n inherits from the run so far: the Decisions and Findings sections as
    they stand and a one-line roster of steps 1..n-1 with state and note. Kept apart from
    the brief on purpose: the gate authorizes config edits by substring-matching the
    brief, so a Finding that names conftest.py must never reach it."""
    if not any(s.number == n for s in plan.steps):
        raise SystemExit(f"no step {n} in plan")
    roster = []
    for s in plan.steps:
        if s.number >= n:
            break
        note = f" ({s.note})" if s.note else ""
        roster.append(f"- Step {s.number}: {s.title}: {s.state}{note}")
    parts = [
        f"# Context for step {n}",
        f"{DECISIONS_HEADER}\n{plan.decisions.strip() or '(none)'}",
        f"{FINDINGS_HEADER}\n{plan.findings.strip() or '(none)'}",
        "## Steps so far\n" + ("\n".join(roster) or "(none)"),
    ]
    return "\n\n".join(parts) + "\n"


def _record_reason(value: str) -> str | None:
    """Why a Record value is not the appender's command followed by the record's slug, split
    as the runner splits it."""
    try:
        words = shlex.split(value)
    except ValueError as exc:
        return f"Conventions {RECORD_KEY} does not split into words: {exc}"
    if len(words) < 2:
        return f"Conventions {RECORD_KEY} must be the appender's command followed by the " + (
            "record's slug"
        )
    return None


def _split_items(value: str) -> list[str]:
    """The comma-separated items of a Closes: or Deletes: value, whitespace trimmed, empty
    items dropped."""
    return [item.strip() for item in value.split(",") if item.strip()]


def step_size_limit(plan: Plan) -> int:
    """The per-step cap for a plan, in changed lines: its Step-size: key, or the default. A
    value that is not a positive integer reads as the default; a value over the run cap is
    returned as written, validate having refused the plan that carries it."""
    value = plan.conventions.get(STEP_SIZE_KEY)
    size = _parse_size(value) if value is not None else None
    return STEP_SIZE_DEFAULT if size is None else size


def _parse_size(value: str) -> int | None:
    """The positive integer of a Size: value, or None for anything else (empty, zero, a sign,
    a fraction, trailing words), which validate names."""
    v = value.strip()
    return int(v) if re.fullmatch(r"[0-9]+", v) and int(v) > 0 else None


def _split_guard_mutation(value: str) -> tuple[str, str, str] | None:
    """(path, old, new) of a Guard-mutation: value, split on the first `: ` and then the
    first ` => `; None when either is missing or the path or old text is empty. A value
    ending in ` =>` has an empty replacement, since the line's trailing space is stripped."""
    gpath, colon, rest = value.strip().partition(": ")
    # the appended space lets a trailing ` =>` match; the slice below takes it back
    old, arrow, new = (rest + " ").partition(" => ")
    if not colon or not arrow or not gpath or not old:
        return None
    return gpath, old, new[:-1]


def _split_globs(value: str) -> list[str]:
    return [g.strip() for g in value.split(",") if g.strip()]


def plan_test_globs(plan: Plan) -> list[str] | None:
    """The plan's Test-files globs, whitespace trimmed, or None when it has no such key."""
    value = plan.conventions.get(TEST_FILES_KEY)
    return None if value is None else _split_globs(value)


def plan_toolchain_globs(plan: Plan) -> list[str] | None:
    """The plan's Toolchain-files globs, whitespace trimmed, or None when it has no such key."""
    value = plan.conventions.get(TOOLCHAIN_FILES_KEY)
    return None if value is None else _split_globs(value)


def plan_shares(plan: Plan) -> set[str]:
    """The resource classes the plan's Shares line names, empty when it has none."""
    return set(_split_globs(plan.conventions.get(SHARES_KEY, "")))


def plan_hash(text: str) -> str:
    """The delivery record's plan hash (ai-dlc-record R4): SHA-256 of the plan with line
    endings read as `\n`, every `Status:` line dropped, and the Decisions and Findings
    sections dropped up to the next `## ` heading, so an approved plan keeps its hash while
    its states, rulings, and findings change. record.mjs computes the same value."""
    kept: list[str] = []
    skipping = False
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if skipping:
            if not line.startswith("## "):
                continue
            skipping = False
        if line.startswith("Status:"):
            continue
        if line.rstrip() in (DECISIONS_HEADER, FINDINGS_HEADER):
            skipping = True
            continue
        kept.append(line)
    return "sha256:" + hashlib.sha256("\n".join(kept).encode("utf-8")).hexdigest()


# ---- byte-preserving edits --------------------------------------------------


def _split_keepends(data: bytes) -> list[bytes]:
    return data.splitlines(keepends=True)


def _newline_of(line: bytes) -> bytes:
    if line.endswith(b"\r\n"):
        return b"\r\n"
    if line.endswith(b"\n"):
        return b"\n"
    return b""


# the clause `evidence_clause` writes, as a DONE step's Status carries it (3.5.10 spec R11): a
# step whose note holds one is a committed step, and a second `set N done` on it is a fix round
EVIDENCE_CLAUSE_RE = re.compile(
    r"check: \d+ passed of \d+ collected at base [0-9a-fA-F]{8}, lint \S+, red \S+"
)
RECORD_JOINT = "record: "


def carried_record(step: Step) -> str:
    """The build record a step's Status carries (3.5.11 spec R2), else empty: the whole note of
    a DONE step whose note holds an evidence clause, or, on any other state, the text after the
    first joint when that text holds one: `record: ` opening the note (the unwrapped states) or
    `; record: ` after the state's own note. The record's own text may say `record: ` (a DONE
    note is free prose), so the first joint is the wrapper's, and set_status_bytes refuses a
    state note of its own that holds the joint while a record is carried."""
    if step.state == "DONE":
        return step.note if EVIDENCE_CLAUSE_RE.search(step.note) else ""
    if step.note.startswith(RECORD_JOINT):
        record = step.note[len(RECORD_JOINT) :]
    else:
        cut = step.note.find(f"; {RECORD_JOINT}")
        if cut == -1:
            return ""
        record = step.note[cut + len(f"; {RECORD_JOINT}") :]
    return record if EVIDENCE_CLAUSE_RE.search(record) else ""


def set_status_bytes(data: bytes, n: int, cli_state: str, note: str) -> bytes:
    if cli_state not in CLI_STATES:
        raise SystemExit(f"unknown state {cli_state!r}; one of {', '.join(CLI_STATES)}")
    state, wrapper = CLI_STATES[cli_state]

    text = data.decode("utf-8")
    plan = parse_plan(text)
    step = next((s for s in plan.steps if s.number == n), None)
    if step is None:
        raise SystemExit(f"no step {n} in plan")

    # a set to any state but done keeps the step's build record after the state's own note
    # (3.5.11 spec R2); `done` is written whole by set N done, record and fix rounds included
    record = carried_record(step) if state != "DONE" else ""
    body = wrapper.format(note=note) if wrapper is not None else ""
    if record and RECORD_JOINT in note:
        # the joint is the wrapper's alone while a record rides, so the first one is always it
        raise SystemExit(
            f'SET REFUSED step {n}: the note carries "{RECORD_JOINT.strip()} ", the joint the '
            "Status keeps the build record behind; reword it"
        )
    if record:
        body = f"{body}; {RECORD_JOINT}{record}" if body else f"{RECORD_JOINT}{record}"
    status = f"Status: {state} ({body})" if body or wrapper is not None else f"Status: {state}"

    lines = _split_keepends(data)
    if step.status_line is not None:
        target = step.status_line - 1
        nl = _newline_of(lines[target])
        lines[target] = status.encode("utf-8") + nl
    else:
        # insert a Status line right after the header, matching the header's newline
        hdr = step.header_line - 1
        nl = _newline_of(lines[hdr]) or b"\n"
        lines.insert(hdr + 1, status.encode("utf-8") + nl)
    return b"".join(lines)


def append_section_bytes(data: bytes, section: str, entry: str) -> bytes:
    header = DECISIONS_HEADER if section == "decisions" else FINDINGS_HEADER
    lines = _split_keepends(data)
    # find the header line
    hidx = None
    for i, raw in enumerate(lines):
        if raw.decode("utf-8").rstrip("\r\n") == header:
            hidx = i
            break
    if hidx is None:
        raise SystemExit(f"section not found: {header}")
    nl = _newline_of(lines[hidx]) or b"\n"
    # find end of section (next H2 or EOF) and locate a lone "(none)" line
    end = len(lines)
    none_idx = None
    for i in range(hidx + 1, len(lines)):
        s = lines[i].decode("utf-8").rstrip("\r\n")
        if s.startswith("## "):
            end = i
            break
        if s.strip() == "(none)":
            none_idx = i
    entry_line = ("- " + entry.strip().replace("\n", " ")).encode("utf-8") + nl
    if none_idx is not None:
        lines[none_idx] = entry_line
    else:
        # insert before the trailing blank lines of the section
        insert_at = end
        while insert_at - 1 > hidx and lines[insert_at - 1].decode("utf-8").strip() == "":
            insert_at -= 1
        # the line before the insertion point ends with the file's newline, so the bullet
        # never lands mid-line when the section's last line was saved without one
        if not _newline_of(lines[insert_at - 1]):
            lines[insert_at - 1] += nl
        lines.insert(insert_at, entry_line)
    return b"".join(lines)


# ---- migration --------------------------------------------------------------

# A step label: `1`, `4a`, `0.1`, `5.4b`. A header may carry a checkbox before the word
# Step (`### [ ] Step 4a -- title`); a list item must (`- [x] Step 0.1: title (evidence)`),
# so an ordinary bullet that mentions a step stays body text.
_STEP_LABEL = r"\d+[a-z]?(?:\.\d+[a-z]?)?"
_LEGACY_HEADER_RE = re.compile(
    rf"^(#{{2,3}})\s+(?:(\[[^\]]*\])\s+)?(Step|Phase)\s+({_STEP_LABEL})\b(.*)$"
)
_LEGACY_ITEM_RE = re.compile(rf"^-\s+(\[[^\]]*\])\s+Step\s+({_STEP_LABEL})\b(.*)$")
_DONE_MARKERS = ("[x]", "[X]", "[done]", "[DONE]", "[\u2713]")
_OPEN_MARKERS = ("[ ]", "[]", "[todo]", "[TODO]")
# what a trailing parenthetical must carry to count as completion evidence rather than
# part of the title: a done word, a date, a merge or PR reference, or a commit sha
_EVIDENCE_RE = re.compile(
    r"\bdone\b|tests? green|\u2705|\d{4}-\d{2}-\d{2}|\bmerged\b|\bPR #\d+"
    r"|\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b",
    re.I,
)


def _marker_state(marker: str | None) -> bool | None:
    if marker in _DONE_MARKERS:
        return True
    if marker in _OPEN_MARKERS:
        return False
    return None


def _clean_legacy_title(rest: str, done: bool | None = None) -> tuple[str, bool | None, str]:
    """From the text after 'Step N', return (title, done_or_none, evidence). `done` is
    what a marker before the word Step already said, if any."""
    r = rest.strip()
    # leading separators: ':', '-', '--', em dash
    r = re.sub(r"^\s*(?:-{1,2}|[:\u2014])\s*", "", r)
    # a leading marker like '[x]' (starter 'Step N - [x] Title')
    for mk in _DONE_MARKERS:
        if r.startswith(mk):
            done = True
            r = r[len(mk) :].strip()
    for mk in _OPEN_MARKERS:
        if r.startswith(mk):
            done = False
            r = r[len(mk) :].strip()
    # trailing markers
    for mk in _DONE_MARKERS:
        if r.endswith(mk):
            done = True
            r = r[: -len(mk)].strip()
    for mk in _OPEN_MARKERS:
        if r.endswith(mk):
            done = False if done is None else done
            r = r[: -len(mk)].strip()
    # trailing parenthetical evidence e.g. "(done 2026-07-13, 3 tests green)" or
    # "(2026-07-28, merged to main @ 3d204a8, PR #10)"; a marker that says open wins,
    # and a parenthetical that reads as title text stays in the title
    evidence = ""
    m = re.search(r"\(([^()]*)\)\s*$", r)
    if m and done is not False and _EVIDENCE_RE.search(m.group(1)):
        evidence = m.group(1).strip()
        done = True
        r = r[: m.start()].strip()
    return r.strip(" :-\u2014"), done, evidence


def _parse_complete_table(lines: list[str]) -> set[int]:
    done: set[int] = set()
    in_complete = False
    for ln in lines:
        s = ln.rstrip()
        if s == "## Complete":
            in_complete = True
            continue
        if in_complete and s.startswith("## "):
            break
        if in_complete and s.startswith("|"):
            first = s.strip("|").split("|", 1)[0].strip()
            for tok in re.split(r"[,\s]+", first):
                if tok.isdigit():
                    done.add(int(tok))
    return done


def in_grammar(text: str) -> bool:
    """A plan that already carries the Conventions header is in the grammar; migrating it
    again would insert a second metadata block under every step."""
    return any(ln.rstrip() == CONV_HEADER for ln in text.splitlines())


def migrate_text(text: str) -> str:
    lines = text.splitlines()
    completed = _parse_complete_table(lines)

    # collect legacy step headers and list-item steps, in document order
    hdrs = []
    for i, ln in enumerate(lines):
        m = _LEGACY_HEADER_RE.match(ln)
        if m:
            marker, label, num, rest = m.group(2), m.group(3), m.group(4), m.group(5)
        else:
            m = _LEGACY_ITEM_RE.match(ln)
            if not m:
                continue
            marker, label, num, rest = m.group(1), "Step", m.group(2), m.group(3)
        title, done_flag, evidence = _clean_legacy_title(rest, _marker_state(marker))
        if done_flag is None:
            done_flag = num.isdigit() and int(num) in completed
        hdrs.append(
            {
                "idx": i,
                "label": label,
                "num": num,
                "title": title,
                "done": done_flag,
                "ev": evidence,
            }
        )

    title_line = next(
        (ln for ln in lines if ln.strip().startswith("# ")), "# Migrated Implementation Plan"
    )

    out: list[str] = []
    out.append(title_line.rstrip())
    out.append("")
    out.append(CONV_HEADER)
    out.append("- Test: uv run pytest")
    out.append("- Lint: uv run ruff check .")
    out.append(f"- Run: uv run python -m {PLACEHOLDER}")
    out.append("")
    out.append(
        "_Migrated from a legacy plan. Conventions above are defaults; edit to match the "
        f"project, and replace {PLACEHOLDER} before validate passes. Original prose "
        "sections are preserved below each step._"
    )
    out.append("")

    if not hdrs:
        # no recognizable steps: still emit a minimal valid skeleton, preserving the body
        out.append("### Step 1: migrated content")
        out.append("Status: PENDING")
        out.append("Eval: no")
        out.append("Mutation: n/a (migrated from a legacy plan without step headers)")
        out.append("")
        body = "\n".join(lines[1:]).strip("\n") if lines else ""
        if body:
            out.append(body)
        out.append("")
        out.append(DECISIONS_HEADER)
        out.append("(none)")
        out.append("")
        out.append(FINDINGS_HEADER)
        out.append("(none)")
        out.append("")
        return "\n".join(out) + "\n"

    for new_num, h in enumerate(hdrs, start=1):
        end = hdrs[new_num]["idx"] if new_num < len(hdrs) else len(lines)
        # trailing prose after the last step up to a required section is kept in the body too
        body_lines = lines[h["idx"] + 1 : end]
        was = ""
        if not (h["label"] == "Step" and h["num"] == str(new_num)):
            was = f" [was {h['label']} {h['num']}]"
        out.append(f"### Step {new_num}: {h['title']}{was}".rstrip())
        if h["done"]:
            ev = h["ev"] if h["ev"] else "migrated from legacy"
            out.append(f"Status: DONE ({ev})")
        else:
            out.append("Status: PENDING")
        out.append("Eval: no")
        out.append("Mutation: n/a (migrated from a legacy plan)")
        # keep the original body, but drop any stray H2 prose sections into the flow verbatim
        body = "\n".join(body_lines).strip("\n")
        if body:
            out.append("")
            out.append(body)
        out.append("")

    # append required trailing sections if the legacy body did not already carry them
    joined = "\n".join(out)
    if DECISIONS_HEADER not in joined:
        out.append(DECISIONS_HEADER)
        out.append("(none)")
        out.append("")
    if FINDINGS_HEADER not in joined:
        out.append(FINDINGS_HEADER)
        out.append("(none)")
        out.append("")
    text = "\n".join(out) + "\n"
    # every renumbering is recorded once, in Findings, so the old labels stay traceable
    # after the per-step `[was ...]` suffixes are edited away
    if any(h["label"] != "Step" or h["num"] != str(i) for i, h in enumerate(hdrs, start=1)):
        entries = ", ".join(
            f"{h['label'] + ' ' if h['label'] != 'Step' else ''}{h['num']} -> {i}"
            for i, h in enumerate(hdrs, start=1)
        )
        text = append_section_bytes(
            text.encode("utf-8"), "findings", f"{LEGACY_MAP_KEY}: {entries}"
        ).decode("utf-8")
    return text


# ---- renumber ---------------------------------------------------------------

# a whole-word `Step <n>` or `Steps <n>` reference: not inside a longer word, not the prefix of
# a longer label (`Step 70`, `Step 7a`, `Step 7.1`), so a map over 7 never touches them; after
# a plural, a list of `, <n>` entries closed by exactly one `and <n>` or `to <n>` is read too,
# nothing after the closing entry, and a comma run with no closing entry contributes nothing
# beyond the first number; _step_ref_spans holds the rule
STEP_REF_RE = re.compile(r"(?<![A-Za-z0-9_-])(Steps?) (\d+)(?![0-9A-Za-z])(?!\.\d)")
_STEP_LIST_RE = re.compile(r"(,\s*|\s+and\s+|\s+to\s+)(\d+)(?![0-9A-Za-z])(?!\.\d)")
RENUMBER_SECTIONS = ("preamble", "headers", "status", "bodies", "decisions", "findings")
LEGACY_MAP_KEY = "Legacy step map"


class RenumberError(ValueError):
    """A renumber the plan grammar refuses: a shift that is not positive, a start above every
    step, or a step in range that is not PENDING."""


def _renumber_map(plan: Plan, start: int, by: int) -> dict[int, int]:
    # a positive shift over every step from `start` up: an insertion or a split, the only
    # cases the numbering rule allows; nothing it moves can land on a step it keeps
    if by <= 0:
        raise RenumberError(
            f"by is {by}; a renumber shifts the steps by a positive number, an insertion or a split"
        )
    moved = [s for s in plan.steps if s.number >= start]
    if not moved:
        raise RenumberError(f"no step is numbered {start} or above; nothing to renumber")
    for s in moved:
        if s.state != "PENDING":
            raise RenumberError(
                f"Step {s.number} is {s.state}; a renumber moves PENDING steps only, and a "
                "DONE step's number never changes"
            )
    return {s.number: s.number + by for s in moved}


def renumber_text(text: str, start: int, by: int) -> tuple[str, dict[int, int], dict[str, int]]:
    """Relabel every PENDING step numbered `start` and above by `by`, and every whole-word
    `Step <n>` reference to one of them anywhere in the plan: the preamble and Conventions
    before the first step header, the step headers, the `Status:` notes, the step bodies, and
    the Decisions and Findings sections. Returns the text, the old-to-new map and the count of
    references rewritten per section. Bytes outside the rewritten spans are unchanged; a
    reference to a label outside the map is left alone, and the `Legacy step map` line carries
    bare numbers no reference matches."""
    plan = parse_plan(text)
    mapping = _renumber_map(plan, start, by)
    lines = text.splitlines(keepends=True)
    counts = dict.fromkeys(RENUMBER_SECTIONS, 0)
    if not plan.steps:
        return text, mapping, counts
    header_lines = {s.header_line for s in plan.steps}
    status_lines = {s.status_line for s in plan.steps if s.status_line is not None}
    section = "preamble"
    for i in range(1, len(lines) + 1):
        raw = lines[i - 1]
        bare = raw.rstrip("\r\n")
        if i == plan.steps[0].header_line:
            section = "bodies"
        if bare == DECISIONS_HEADER:
            section = "decisions"
        elif bare == FINDINGS_HEADER:
            section = "findings"
        if i in header_lines:
            where = "headers"
        elif i in status_lines:
            where = "status"
        else:
            where = section
        lines[i - 1], n = _swap_step_refs(raw, mapping)
        counts[where] += n
    return "".join(lines), mapping, counts


def _step_ref_spans(line: str) -> list[tuple[int, int, int]]:
    """(start, end, number) of every step number a reference on the line names: the one after
    `Step`, and after `Steps` the list that follows it: comma entries closed by exactly one
    `and` or `to` entry, which ends the list. A comma run with no closing word is a count, not
    a list (`Steps 7, 8` names 7 alone), and nothing after the closing entry is read
    (`Steps 7 to 9, 12 tests red` names 7 and 9)."""
    spans: list[tuple[int, int, int]] = []
    pos = 0
    while (m := STEP_REF_RE.search(line, pos)) is not None:
        spans.append((m.start(2), m.end(2), int(m.group(2))))
        pos = m.end()
        if m.group(1) == "Steps":
            pending: list[tuple[int, int, int]] = []
            while (n := _STEP_LIST_RE.match(line, pos)) is not None:
                entry = (n.start(2), n.end(2), int(n.group(2)))
                pos = n.end()
                if n.group(1).startswith(","):
                    pending.append(entry)
                    continue
                spans.extend(pending)
                spans.append(entry)
                break
    return spans


def _swap_step_refs(line: str, mapping: dict[int, int]) -> tuple[str, int]:
    """One line with every mapped step reference rewritten, and how many numbers were."""
    out: list[str] = []
    last = swapped = 0
    for start, end, n in _step_ref_spans(line):
        if n in mapping:
            out.append(line[last:start])
            out.append(str(mapping[n]))
            last = end
            swapped += 1
    out.append(line[last:])
    return "".join(out), swapped


def _extend_legacy_map(data: bytes, entries: str) -> bytes:
    """The old-to-new mapping on the `- Legacy step map:` line under Findings, after the
    entries already there; the line is created on the first renumber, as migrate creates it."""
    lines = _split_keepends(data)
    in_findings = False
    for i, raw in enumerate(lines):
        bare = raw.decode("utf-8").rstrip("\r\n")
        if bare.startswith("## "):
            in_findings = bare == FINDINGS_HEADER
        elif in_findings and bare.startswith(f"- {LEGACY_MAP_KEY}:"):
            lines[i] = (bare + ", " + entries).encode("utf-8") + _newline_of(raw)
            return b"".join(lines)
    return append_section_bytes(data, "findings", f"{LEGACY_MAP_KEY}: {entries}")


def unmapped_step_references(text: str, mapping: dict[int, int]) -> list[int]:
    """The distinct `Step <n>` labels the text references outside the map, in order: the
    steps a renumber left alone, and any label that names no step at all."""
    found = {n for ln in text.splitlines() for _start, _end, n in _step_ref_spans(ln)}
    return sorted(n for n in found if n not in mapping and n not in mapping.values())


# ---- CLI --------------------------------------------------------------------


def _read_note(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8").strip()


def _percent(value: str) -> int:
    n = int(value)  # a non-integer raises ValueError, which argparse reports as usage
    if not 0 <= n <= 100:
        raise argparse.ArgumentTypeError(f"{value} is not a percentage between 0 and 100")
    return n


def _ruling_add(args: argparse.Namespace) -> int:
    """A prose ruling, typed with no dialog, into the store the ruling-log hook writes."""
    # next_auto imports this module at its top, so its run key is read here
    import next_auto

    store, why = rulings.store_dir(args.dir)
    if store is None:
        print(why, file=sys.stderr)
        return 2
    session, why = next_auto.run_key()
    if session is None:
        print(f"ruling store: {why}", file=sys.stderr)
        return 2
    entry = rulings.make_ruling(
        session=session,
        time=time.strftime(rulings.TIME_FORM),
        header=args.header,
        question="",
        option=args.text,
        notes=None,
        source=args.source,
    )
    rulings.append(store, entry)
    print(f"ruling {entry['id']} {entry['time']} {entry['header']}")
    return 0


def _ruling_recall(args: argparse.Namespace) -> int:
    """The rulings the ruling-recall hook would put in front of a question with this text, for a
    session about to ask one in prose; a store with nothing in it prints nothing."""
    store, why = rulings.store_dir(args.dir)
    if store is None:
        print(why, file=sys.stderr)
        return 2
    for entry in rulings.recall(args.text, datetime.now(UTC), rulings.load(store)):
        print(rulings.render(entry))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="next_plan.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("validate", "migrate", "status", "next", "plan-hash"):
        s = sub.add_parser(name)
        s.add_argument("plan")

    bt = sub.add_parser("batch")
    bt.add_argument("plan")
    bt.add_argument("--context-pct", type=_percent, default=None)

    b = sub.add_parser("brief")
    b.add_argument("plan")
    b.add_argument("n", type=int)
    b.add_argument("--out")

    st = sub.add_parser("set")
    st.add_argument("plan")
    st.add_argument("n", type=int)
    st.add_argument("state")
    st.add_argument("--note-file")

    ap_ = sub.add_parser("append")
    ap_.add_argument("plan")
    ap_.add_argument("section", choices=["decisions", "findings"])
    ap_.add_argument("--note-file", required=True)

    rn = sub.add_parser("renumber")
    rn.add_argument("plan")
    rn.add_argument("--from", dest="start", type=int, required=True, help="the first step moved")
    rn.add_argument("--by", type=int, required=True, help="the shift, positive for an insertion")

    rl = sub.add_parser("ruling")
    rl_sub = rl.add_subparsers(dest="ruling_cmd", required=True)
    rl_add = rl_sub.add_parser("add")
    rl_add.add_argument("--header", required=True)
    rl_add.add_argument("--text", required=True)
    rl_add.add_argument("--source", required=True)
    rl_add.add_argument("--dir", help="the project dir; defaults to CLAUDE_PROJECT_DIR")
    rl_recall = rl_sub.add_parser("recall")
    rl_recall.add_argument("text")
    rl_recall.add_argument("--dir", help="the project dir; defaults to CLAUDE_PROJECT_DIR")

    args = ap.parse_args(argv)
    if args.cmd == "ruling":
        return _ruling_add(args) if args.ruling_cmd == "add" else _ruling_recall(args)
    plan_path = Path(args.plan)
    if not plan_path.is_file():
        print(f"{plan_path}: no such file", file=sys.stderr)
        return 2

    if args.cmd == "validate":
        text = plan_path.read_text(encoding="utf-8")
        reasons = validate_text(text)
        if reasons:
            for ln, msg in reasons:
                print(f"{plan_path}:{ln}: {msg}", file=sys.stderr)
            return 2
        for ln, msg in validate_warnings(text):
            print(f"{plan_path}:{ln}: WARN {msg}", file=sys.stderr)
        return 0

    if args.cmd == "migrate":
        data = plan_path.read_bytes()
        text = data.decode("utf-8")
        if in_grammar(text):
            # a plan in the grammar is rewritten only at the near miss of a section it lacks,
            # the one validate names, renamed to its header with the backup a legacy migrate
            # leaves (3.5.11 spec R3); a present header's near misses stay as the prose they are
            misses = missing_near_misses(text)
            if not misses:
                print(
                    f"{plan_path}: already in the plan grammar; run validate, not migrate",
                    file=sys.stderr,
                )
                return 2
            bak = plan_path.with_suffix(plan_path.suffix + ".bak")
            bak.write_bytes(data)
            lines = _split_keepends(data)
            for ln, _found, header in misses:
                lines[ln - 1] = header.encode("utf-8") + _newline_of(lines[ln - 1])
            plan_path.write_bytes(b"".join(lines))
            for _ln, found, header in misses:
                print(f"migrated {plan_path}: renamed {found!r} to {header!r} (backup at {bak})")
            return 0
        bak = plan_path.with_suffix(plan_path.suffix + ".bak")
        bak.write_bytes(data)
        migrated = migrate_text(text)
        # preserve the file's dominant newline style
        lf_only = data.count(b"\n") - data.count(b"\r\n")
        if data.count(b"\r\n") >= lf_only:
            migrated = migrated.replace("\n", "\r\n")
        plan_path.write_text(migrated, encoding="utf-8", newline="")
        print(f"migrated {plan_path} (backup at {bak})")
        return 0

    if args.cmd == "status":
        plan = parse_plan(plan_path.read_text(encoding="utf-8"))
        print(json.dumps(to_json(plan), indent=2))
        return 0

    if args.cmd == "renumber":
        data = plan_path.read_bytes()
        try:
            text, mapping, counts = renumber_text(data.decode("utf-8"), args.start, args.by)
        except RenumberError as exc:
            print(f"{plan_path}: {exc}", file=sys.stderr)
            return 2
        entries = ", ".join(f"{old} -> {new}" for old, new in mapping.items())
        plan_path.write_bytes(_extend_legacy_map(text.encode("utf-8"), entries))
        print(f"renumbered {plan_path}: {entries}")
        for name in RENUMBER_SECTIONS:
            print(f"{name}: {counts[name]}")
        alone = unmapped_step_references(text, mapping)
        print("left alone: " + (", ".join(f"Step {n}" for n in alone) if alone else "none"))
        return 0

    if args.cmd == "plan-hash":
        print(plan_hash(plan_path.read_bytes().decode("utf-8")))
        return 0

    if args.cmd == "batch":
        plan = parse_plan(plan_path.read_text(encoding="utf-8"))
        b = batch_steps(plan, args.context_pct)
        print(json.dumps({k: v for k, v in b.items() if k != "sentence"}))
        print(b["sentence"])
        return 0

    if args.cmd == "next":
        plan = parse_plan(plan_path.read_text(encoding="utf-8"))
        s = next_step(plan)
        print(
            json.dumps(
                {
                    "number": s.number,
                    "title": s.title,
                    "chunk": s.chunk,
                    "last_in_chunk": s.last_in_chunk,
                }
                if s
                else {"number": None}
            )
        )
        return 0

    if args.cmd == "brief":
        plan = parse_plan(plan_path.read_text(encoding="utf-8"))
        out = render_brief(plan, args.n)
        if args.out:
            Path(args.out).write_text(out, encoding="utf-8")
        else:
            sys.stdout.write(out)
        return 0

    if args.cmd == "set":
        if args.state == "done":
            # `done` is the engine's: next_auto.py set N done reads the step's check record and
            # appends its evidence clause, so this grammar tool never writes a DONE note
            print(
                f"SET REFUSED step {args.n}: done is set through next_auto.py set N done "
                "--note-file <f>, which reads the check record",
                file=sys.stderr,
            )
            return 1
        note = _read_note(args.note_file)
        new = set_status_bytes(plan_path.read_bytes(), args.n, args.state, note)
        plan_path.write_bytes(new)
        return 0

    if args.cmd == "append":
        note = _read_note(args.note_file)
        new = append_section_bytes(plan_path.read_bytes(), args.section, note)
        plan_path.write_bytes(new)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
