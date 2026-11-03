#!/usr/bin/env python3
# Description: The delivery projection (spec Requirements 9 to 11 and 13 to 16): a SQLite store of
# Description: nodes and edges, built in a temp file and renamed into place, and its typed queries.

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import next_auto
import next_plan
import rulings

STORE = Path(".next-auto") / "projection.sqlite"
RECORDS = Path("docs") / "records"
# Requirement 10 (b): the plan globs, relative to the project dir, since a workspace's plans
# can live beside the repository and in its worktrees rather than in the tree (the plan's
# Findings)
PLAN_GLOBS = ("prompt_plan.md", "prompt_plan-*.md", "exports/*/prompt_plan.md", "docs/*-plan.md")
# a plan header's spec path, as in `Spec: docs/specs/observability.md. Owner: ...`
SPEC_LINE = re.compile(r"Spec: (\S+?)\.?(?:\s|$)")
TIME_FORM = "%Y-%m-%dT%H:%M:%SZ"
# Requirement 10 (c): every docs/specs/*.md and docs/specs/**/spec.md of the checkout
SPECS = Path("docs") / "specs"
SPEC_GLOBS = ("*.md", "**/spec.md")
# a spec's version line, as in `Version: v3, 2026-10-15 (...)`, its R and D lines; a decision is
# written `D<n>.` or `D<n>,` (most records write `D1, the topic. RULED ...`)
VERSION_LINE = re.compile(r"Version: (\S+?),?(?:\s|$)")
REQUIREMENT_LINE = re.compile(r"R(\d+)\. ")
DECISION_LINE = re.compile(r"D(\d+)[.,] ")
# a record's header is eight lines, the last two `## Events` and a blank one (record.mjs
# headerLines); every line after it is an event line
RECORD_HEADER_LINES = 8
# the event line of the record grammar (R2 of docs/specs/ai-dlc-record.md): a timestamp in
# RFC 3339 UTC to the second, the event, then key=value pairs with no whitespace and no empty value
EVENT_LINE = re.compile(
    r"- (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) ([a-z]+\.[a-z]+)((?: [a-z]+=\S+)*)"
)
# Requirement 10 (d): the pull requests of the target's GitHub repository, bodies and never diffs;
# gh lists 30 unless told, and a year-old repository holds hundreds
REPO_VIEW = ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
PR_FIELDS = "number,title,body,headRefName,baseRefName,state,mergedAt,mergeCommit,createdAt,author"
PR_LIST = ["gh", "pr", "list", "--state", "all", "--limit", "10000", "--json", PR_FIELDS]
PR_PROPS = ("number", "title", "state", "headRefName", "baseRefName", "createdAt", "mergedAt")
# a body's trailer lines (exports/platform/AGENTS.md, Pull requests); a value of `none (<reason>)`
# names nothing
TRAILER_LINE = re.compile(r"(Spec|Closes|Intent|Approved-by): (.+)")
# Linear's copied branch name, <user>/<key>-<n>-<words>, and the engine's next-auto/<issue>-<slug>,
# the key in next_plan.ISSUE_RE's shape lowercased
TRACKER_BRANCH = re.compile(r"[^/]+/([a-z][a-z0-9]*-[0-9]+)-")
# Requirement 10 (e): the ruling store under the project dir (rulings.STORE_SUBDIR) and the
# decisions log beside it, one line per ruling as `<time> <zone> | [<id> | ]<option> | <what>`,
# the time to the second as a handoff gate's ruling-line check requires; a line that opens
# with a date and is not in that form stops the build
DECISIONS_LOG = Path("docs") / "decisions"
DATE_LED = re.compile(r"\d{4}-\d{2}-\d{2}")
DECISION_RULING = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [A-Z]+) \| (?:(rul-[0-9a-f]{10}) \| )?(.+?) \| (.+)"
)
# the store line's fields a ruling node keeps (Requirement 6 names them)
RULING_KEYS = ("id", "time", "header", "option", "notes", "source")
# Requirement 10 (f) and (g): the close-out ledger's JSON lines (closeout_ledger.jsonl_line) and
# the night runner's ledger, one line per headless run; cost_usd is a Decimal string in both, as
# format(Decimal, "f") writes it, and is kept as written
CLOSEOUT_KEYS = ("session_id", "day", "model", "cost_usd")
FACTORY_KEYS = ("ts", "session_id", "cost_usd")
DECIMAL_STRING = re.compile(r"-?\d+(?:\.\d+)?")
# Requirement 13's ruling unblocks <node> (rul-68231e8df9): the tokens of a ruling's text, the words
# a capability slug is matched against, and the fields each ruling source wrote that text into
RULING_TOKEN = re.compile(r"\b(?:F-\d+|DOC-\d+|R\d+|D\d+|S\d+|Step \d+)\b")
SLUG_WORD = re.compile(r"[a-z0-9][a-z0-9-]*")
RULING_TEXT = {"rulings": ("header", "option", "notes"), "decisions": ("option", "what")}
# Requirements 14 and 15: an answer lists at most CAP nodes and stays under 20,000 tokens at four
# characters a token; a source is stale when its watermark is older than STALE_AFTER. find reads a
# node's id and these props, a title, a store header and a ruling's text, and never a body
CAP = 200
MAX_CHARS = 20_000 * 4
STALE_AFTER = timedelta(hours=24)
FIND_PROPS = ("title", "header", "option", "notes", "what")
# Requirement 15's hop caps, which keep every search bounded on a hub node
MAX_HOPS = 4
MAX_PATH = 8
# a node's dated props for query timeline: the UTC time a parser stores as at, gh's createdAt and
# mergedAt, and a close-out cost's UTC day; at keeps the form its source wrote, a ruling's time or
# a night ledger's ts, as its original
DATED_PROPS = ("at", "createdAt", "mergedAt", "day")
ORIGINAL_PROPS = ("time", "ts")
# Requirement 16's query landed, in the shape of a workspace's landed table
# (read 2026-10-24): the deploy workflow's runs it reads (rul-982e177974), read only from a target
# that carries that workflow; the Handoff's Since and Until lines (rul-0723e3dbdc); the Now table
# of the project's docs/setup-plan.md; its Spec: form, failed conclusions and table header
DEPLOY_WORKFLOW = Path(".github") / "workflows" / "deploy.yml"
RUN_FIELDS = "databaseId,displayTitle,status,conclusion,createdAt"
RUN_LIST = [
    *("gh", "run", "list", "--workflow", "deploy.yml", "--limit", "10000"),
    *("--json", RUN_FIELDS),
]
DEPLOY_TITLE = re.compile(r"deploy ([0-9a-f]{40})")
FAILED_RUNS = frozenset({"failure", "timed_out", "startup_failure"})
HANDOFF = Path("docs") / "handoff"
SETUP_PLAN = Path("docs") / "setup-plan.md"
WINDOW_LINE = re.compile(r"(Since|Until): ([0-9a-f]+)")
LANDED_SPEC = re.compile(r"docs/specs/([a-z0-9-]+)(?:\.md|[/]spec\.md)(?:@v\d+)?(?:\s|$)")
LANDED_HEADER = ("| PR | Title | Merged | Run | Conclusion | Intent |", "|---|---|---|---|---|---|")
NEW_YORK = ZoneInfo("America/New_York")

# Requirement 11: the seventeen node types; the node table's check refuses any other
NODE_TYPES = (
    "repo",
    "commit",
    "pull_request",
    "run",
    "plan",
    "plan_step",
    "spec",
    "requirement",
    "decision",
    "ruling",
    "ci_run",
    "deploy",
    "review",
    "done_verdict",
    "intent",
    "tracker_issue",
    "cost",
)
# v1 carries no extracted edges; the edge table's check refuses the method
EDGE_METHODS = ("observed", "declared", "derived")

log = logging.getLogger("next_projection")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


# One node table keyed by (type, key), not a table per type (the plan's Findings, Requirement 11).
# PROV-O: a node is a prov:Entity, a run a prov:Activity; an edge is a qualified relation carrying
# its provenance; a watermark is the newest fact time a source's parser saw. A node's provenance is
# a node_source row per source that wrote it, since a plan's facts come from the records and the
# plan file both.
SCHEMA = f"""
CREATE TABLE node (
    type TEXT NOT NULL CHECK (type IN ({_in(NODE_TYPES)})),
    key TEXT NOT NULL,
    props TEXT NOT NULL,
    PRIMARY KEY (type, key)
);
CREATE TABLE node_source (
    type TEXT NOT NULL,
    key TEXT NOT NULL,
    source TEXT NOT NULL,
    source_version TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (type, key, source)
);
CREATE TABLE edge (
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    type TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ({_in(EDGE_METHODS)})),
    rule TEXT,
    source TEXT NOT NULL,
    source_version TEXT NOT NULL,
    asserted_at TEXT NOT NULL
);
-- one row per fact and source; a NULL rule counts as equal to another, which a plain unique
-- index would not, so two copies of one plan assert each edge once
CREATE UNIQUE INDEX edge_fact ON edge (src, dst, type, method, ifnull(rule, ''), source);
-- a source that could not be reached keeps its last watermark, NULL when it was never read, and
-- is stale (the spec's Error handling)
CREATE TABLE watermarks (
    source TEXT PRIMARY KEY,
    watermark TEXT,
    skipped INTEGER NOT NULL DEFAULT 0,
    stale INTEGER NOT NULL DEFAULT 0
);
"""


def project_dir(given: Path | None) -> Path | None:
    """The project dir given, else CLAUDE_PROJECT_DIR, else None."""
    if given is not None:
        return given
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else None


class ProjectionError(Exception):
    """A source line the build cannot read, as <file>:<line>: <text>; it stops the build."""


@dataclass(frozen=True)
class Origin:
    """Where a row came from: the source, its version and the build's ingest time."""

    source: str
    version: str
    ingested_at: str


@dataclass(frozen=True)
class Event:
    """One record line: where it stands, its text, its timestamp, its event and its values."""

    where: str
    text: str
    at: str
    name: str
    values: dict[str, str]

    def get(self, key: str) -> str:
        if key not in self.values:
            raise ProjectionError(f"{self.where}: {self.text}")
        return self.values[key]


@dataclass(frozen=True)
class RecordRead:
    """One record file as derive_produced_in reads it: its origin, each step.done line with the
    plan step it projects to, and its pr.opened lines."""

    origin: Origin
    steps: list[tuple[str, Event]]
    opened: list[Event]


def git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def file_version(path: Path) -> str:
    """The sha of the last commit that touched the file when a git work tree tracks it, else the
    file's SHA-256 (Requirement 10); a file in no work tree, ignored, or staged and never
    committed counts as untracked."""
    listed = subprocess.run(
        ["git", "-C", str(path.parent), "ls-files", "--", path.name],
        capture_output=True,
        text=True,
    )
    if listed.returncode == 0 and listed.stdout.strip():
        sha = git(path.parent, "log", "-1", "--format=%H", "--", path.name)
        if sha:
            return sha
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def put_node(
    conn: sqlite3.Connection, origin: Origin, node_type: str, key: str, props: dict | None = None
) -> str:
    """Upsert a node, its props merged over any it has, and the provenance row of the source
    writing it; return its id, <type>:<key>."""
    row = conn.execute(
        "SELECT props FROM node WHERE type = ? AND key = ?", (node_type, key)
    ).fetchone()
    merged = {**(json.loads(row[0]) if row else {}), **(props or {})}
    conn.execute(
        "INSERT INTO node VALUES (?, ?, ?) ON CONFLICT (type, key) DO UPDATE SET"
        " props = excluded.props",
        (node_type, key, json.dumps(merged, sort_keys=True)),
    )
    conn.execute(
        "INSERT INTO node_source VALUES (?, ?, ?, ?, ?) ON CONFLICT (type, key, source) DO UPDATE"
        " SET source_version = excluded.source_version, ingested_at = excluded.ingested_at",
        (node_type, key, *_stamp(origin)),
    )
    return f"{node_type}:{key}"


def put_edge(
    conn: sqlite3.Connection,
    origin: Origin,
    src: str,
    dst: str,
    edge_type: str,
    asserted_at: str,
    method: str = "observed",
    rule: str | None = None,
) -> None:
    """An edge with its method, and the rule that derived it when the method is derived; an edge
    its source already asserted is kept as first written. ON CONFLICT DO NOTHING, not INSERT OR
    IGNORE, which would also skip a row the method check or a NOT NULL column refuses."""
    conn.execute(
        "INSERT INTO edge VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        (src, dst, edge_type, method, rule, origin.source, origin.version, asserted_at),
    )


def _stamp(origin: Origin) -> tuple[str, str, str]:
    return origin.source, origin.version, origin.ingested_at


def parse_event(where: str, text: str) -> Event:
    m = EVENT_LINE.fullmatch(text)
    if m is None:
        raise ProjectionError(f"{where}: {text}")
    values: dict[str, str] = {}
    for pair in m.group(3).split():
        key, _, value = pair.partition("=")
        if key in values:
            raise ProjectionError(f"{where}: {text}")
        values[key] = value
    return Event(where, text, m.group(1), m.group(2), values)


def project_event(
    conn: sqlite3.Connection, origin: Origin, ev: Event, approved: str | None
) -> str | None:
    """The rows of one record line (sources (a) and (h) of Requirement 10). Returns the plan of
    the nearest plan.approved so far, which a step.done without a plan key belongs to."""
    if ev.name == "plan.approved":
        put_node(conn, origin, "plan", ev.get("plan"), {"at": ev.at, **ev.values})
        return ev.get("plan")
    if ev.name == "step.done":
        plan = ev.values.get("plan") or approved
        if plan is None:
            raise ProjectionError(f"{ev.where}: {ev.text}")
        props = {"at": ev.at, **ev.values}
        step = put_node(conn, origin, "plan_step", f"{plan}/{ev.get('step')}", props)
        part_of = ("observed", None) if "plan" in ev.values else ("derived", "record-order")
        put_edge(
            conn, origin, step, put_node(conn, origin, "plan", plan), "partOf", ev.at, *part_of
        )
        commit = put_node(conn, origin, "commit", ev.get("sha"))
        put_edge(conn, origin, step, commit, "committedAs", ev.at)
        if "runkey" in ev.values:
            run = put_node(conn, origin, "run", ev.values["runkey"])
            put_edge(conn, origin, step, run, "ranIn", ev.at)
    elif ev.name in ("deploy.completed", "deploy.promoted"):
        completed = ev.name == "deploy.completed"
        target = "dev" if completed else ev.get("target")
        sha = ev.get("sha") if completed else ev.get("base")
        props = {"at": ev.at, **ev.values}
        deploy = put_node(conn, origin, "deploy", f"{ev.get('run')}/{target}", props)
        put_edge(conn, origin, deploy, put_node(conn, origin, "commit", sha), "deploys", ev.at)
    return approved


def ingest_records(conn: sqlite3.Connection, root: Path, ingested_at: str) -> list[RecordRead]:
    """Every docs/records/*.md of the checkout; the watermark is the newest timestamp read. Each
    file's step.done and pr.opened lines come back for derive_produced_in."""
    newest: str | None = None
    reads = []
    for path in sorted((root / RECORDS).glob("*.md")):
        rel = path.relative_to(root)
        read = RecordRead(Origin("records", file_version(path), ingested_at), [], [])
        lines = path.read_text(encoding="utf-8").splitlines()
        approved: str | None = None
        for n, text in enumerate(lines[RECORD_HEADER_LINES:], start=RECORD_HEADER_LINES + 1):
            ev = parse_event(f"{rel}:{n}", text)
            approved = project_event(conn, read.origin, ev, approved)
            if ev.name == "step.done":
                plan = ev.values.get("plan") or approved
                read.steps.append((f"plan_step:{plan}/{ev.get('step')}", ev))
            elif ev.name == "pr.opened":
                read.opened.append(ev)
            newest = ev.at if newest is None else max(newest, ev.at)
        reads.append(read)
    if newest is not None:
        conn.execute(
            "INSERT INTO watermarks (source, watermark) VALUES (?, ?)", ("records", newest)
        )
    return reads


def slug_of(spec: str) -> str:
    """The capability slug of a spec path: the folder of a docs/specs/<slug>/spec.md, else the
    file's stem."""
    path = Path(spec)
    return path.parent.name if path.name == "spec.md" else path.stem


def project_plan(conn: sqlite3.Connection, origin: Origin, rel: Path, text: str, at: str) -> None:
    """A plan node keyed by the engine's plan hash, a plan_step per step keyed by hash and number,
    and a declared closes edge per Closes: id to the requirement of the plan's spec."""
    plan = next_plan.parse_plan(text)
    plan_key = next_plan.plan_hash(text)
    spec = next((m.group(1) for ln in text.splitlines() if (m := SPEC_LINE.match(ln))), None)
    props = {"path": str(rel), "title": plan.title, "spec": spec}
    plan_id = put_node(conn, origin, "plan", plan_key, props)
    for s in plan.steps:
        props = {"number": s.number, "title": s.title, "status": s.state}
        step = put_node(conn, origin, "plan_step", f"{plan_key}/{s.number}", props)
        put_edge(conn, origin, step, plan_id, "partOf", at)
        for rid in s.closes:
            if spec is None:
                raise ProjectionError(f"{rel}:{s.header_line}: Closes: {rid} with no Spec: line")
            put_edge(
                conn, origin, step, f"requirement:{slug_of(spec)}/{rid}", "closes", at, "declared"
            )


def ingest_specs(conn: sqlite3.Connection, root: Path, ingested_at: str) -> None:
    """Every spec of the checkout (source (c) of Requirement 10): a requirement per R<n>. line
    keyed <slug>/R<n>/<version> and a decision per D<n>. or D<n>, line keyed <slug>/D<n>. The
    version is the first word of the Version: line, else the file's version, the sha of its last
    commit. The watermark is the newest modification time among the files read."""
    paths = sorted({p for g in SPEC_GLOBS for p in (root / SPECS).glob(g) if p.is_file()})
    newest: str | None = None
    for path in paths:
        rel = path.relative_to(root)
        at = datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime(TIME_FORM)
        newest = at if newest is None else max(newest, at)
        origin = Origin("specs", file_version(path), ingested_at)
        lines = path.read_text(encoding="utf-8").splitlines()
        stated = next((m.group(1) for ln in lines if (m := VERSION_LINE.match(ln))), None)
        version = stated or origin.version
        slug = slug_of(str(rel))
        for ln in lines:
            if m := REQUIREMENT_LINE.match(ln):
                props = {"path": str(rel), "version": version}
                put_node(conn, origin, "requirement", f"{slug}/R{m.group(1)}/{version}", props)
            elif m := DECISION_LINE.match(ln):
                put_node(conn, origin, "decision", f"{slug}/D{m.group(1)}", {"path": str(rel)})
    if newest is not None:
        conn.execute("INSERT INTO watermarks (source, watermark) VALUES (?, ?)", ("specs", newest))


def ingest_plans(
    conn: sqlite3.Connection, project: Path, globs: tuple[str, ...], ingested_at: str
) -> None:
    """Every plan under the globs (source (b) of Requirement 10). A file without the Conventions
    heading is skipped and counted; one the grammar rejects stops the build naming the file. The
    files are read oldest first by modification time, then by path, so of several copies of one
    plan the newest writes its path and its steps' status last. The watermark is the newest
    modification time among the files read."""
    found = {p for g in globs for p in project.glob(g) if p.is_file()}
    paths = sorted(found, key=lambda p: (p.stat().st_mtime, p))
    newest: str | None = None
    skipped = 0
    for path in paths:
        rel = path.relative_to(project)
        at = datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime(TIME_FORM)
        newest = at if newest is None else max(newest, at)
        text = path.read_text(encoding="utf-8")
        if not any(ln.rstrip() == next_plan.CONV_HEADER for ln in text.splitlines()):
            skipped += 1
            continue
        errors = next_plan.validate_text(text)
        if errors:
            line, reason = errors[0]
            raise ProjectionError(f"{rel}:{line}: {reason}")
        project_plan(conn, Origin("plans", file_version(path), ingested_at), rel, text, at)
    if newest is not None:
        conn.execute(
            "INSERT INTO watermarks (source, watermark, skipped) VALUES (?, ?, ?)",
            ("plans", newest, skipped),
        )


def run_gh(argv: list[str], cwd: Path) -> str:
    """One command in the target checkout and its stdout; CalledProcessError on a non-zero exit, an
    OSError when the program is missing. The pull request source reads GitHub through it alone."""
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=True).stdout


def last_watermark(store: Path, source: str) -> str | None:
    """The source's watermark in the previous store, or None when it has none."""
    if not store.is_file():
        return None
    conn = sqlite3.connect(store)
    try:
        row = conn.execute("SELECT watermark FROM watermarks WHERE source = ?", (source,))
        found = row.fetchone()
    finally:
        conn.close()
    return found[0] if found else None


def keep_stale(conn: sqlite3.Connection, source: str, previous: Path) -> None:
    """A source the build could not reach: stale at the previous store's watermark, with the
    rows that store holds from it carried over, each node merged as put_node merges and each row
    keeping the version and ingest time of the build that read it, so the store answers as of the
    watermark it keeps (rul-fade447316). Its derived edges are not carried, since every derivation
    runs again over the carried facts (rul-538e89a0b5)."""
    conn.execute(
        "INSERT INTO watermarks (source, watermark, stale) VALUES (?, ?, 1)",
        (source, last_watermark(previous, source)),
    )
    if not previous.is_file():
        return
    old = sqlite3.connect(previous)
    try:
        nodes = old.execute(
            "SELECT n.type, n.key, n.props, s.source_version, s.ingested_at FROM node n"
            " JOIN node_source s USING (type, key) WHERE s.source = ?",
            (source,),
        ).fetchall()
        edges = old.execute(
            "SELECT src, dst, type, method, rule, source, source_version, asserted_at FROM edge"
            " WHERE source = ? AND method != 'derived'",
            (source,),
        ).fetchall()
    finally:
        old.close()
    for node_type, key, props, version, ingested_at in nodes:
        put_node(conn, Origin(source, version, ingested_at), node_type, key, json.loads(props))
    conn.executemany(
        "INSERT INTO edge VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", edges
    )


def trailers(body: str) -> dict[str, str]:
    """The first Spec:, Closes:, Intent: and Approved-by: line of a pull request body."""
    found: dict[str, str] = {}
    for ln in body.splitlines():
        if (m := TRAILER_LINE.fullmatch(ln)) and m.group(1) not in found:
            found[m.group(1)] = m.group(2).strip()
    return found


def named(value: str | None) -> list[str]:
    """A trailer's comma-separated values; none for a missing trailer or `none (<reason>)`."""
    if value is None or value.startswith("none"):
        return []
    return [v.strip() for v in value.split(",")]


def tracker_of(branch: str) -> str | None:
    """The tracker issue a branch name leads with, as DOC-1015, or None."""
    m = TRACKER_BRANCH.match(branch)
    return m.group(1).upper() if m else None


def project_pull(conn: sqlite3.Connection, origin: Origin, repo: str, pr: dict) -> None:
    """A pull_request keyed <repo>#<number> with its gh fields, author and Approved-by as props;
    its trailers as declared edges to its spec, the requirements it closes and the intents it
    serves; its branch's tracker issue; once merged, its merge commit as an observed edge. A
    Closes: with no spec path stops the build, as a plan's does."""
    key = f"{repo}#{pr['number']}"
    found = trailers(pr["body"] or "")
    props = {k: pr[k] for k in PR_PROPS} | {"author": pr["author"]["login"]}
    if "Approved-by" in found:
        props["approvedBy"] = found["Approved-by"]
    # the Intent: and Spec: values as written, for query landed's Intent cell (rul-0723e3dbdc)
    for trailer, name in (("Intent", "intent"), ("Spec", "spec")):
        if trailer in found:
            props[name] = found[trailer]
    node = put_node(conn, origin, "pull_request", key, props)
    at = pr["createdAt"]
    specs = named(found.get("Spec"))
    for path in specs:
        spec = put_node(conn, origin, "spec", slug_of(path), {"path": path})
        put_edge(conn, origin, node, spec, "implements", at, "declared")
    closes = named(found.get("Closes"))
    if closes and not specs:
        raise ProjectionError(f"{key}: Closes: {found['Closes']} with no Spec: path")
    for rid in closes:
        target = f"requirement:{slug_of(specs[0])}/{rid}"
        put_edge(conn, origin, node, target, "closes", at, "declared")
    for intent in named(found.get("Intent")):
        served = put_node(conn, origin, "intent", intent)
        put_edge(conn, origin, node, served, "serves", at, "declared")
    if issue := tracker_of(pr["headRefName"]):
        tracked = put_node(conn, origin, "tracker_issue", issue)
        put_edge(conn, origin, node, tracked, "tracks", at, "declared")
    if pr["mergeCommit"]:
        commit = put_node(conn, origin, "commit", pr["mergeCommit"]["oid"])
        put_edge(conn, origin, node, commit, "mergedAs", pr["mergedAt"])


def ingest_pulls(
    conn: sqlite3.Connection,
    root: Path,
    ingested_at: str,
    gh: Callable[[list[str], Path], str],
    previous: Path,
) -> None:
    """The pull requests of the target's repository (source (d) of Requirement 10), read through
    gh alone; a target with no git remote has none to read. A gh call that fails, gh offline or
    missing, is logged and leaves the source stale at the previous store's watermark, and the build
    goes on. The version is the read's SHA-256, the watermark the newest createdAt or mergedAt."""
    if not git(root, "remote"):
        return
    try:
        repo = gh(REPO_VIEW, root).strip()
        text = gh(PR_LIST, root)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else exc
        log.error("next_projection: pulls unreachable, kept stale: %s", detail, exc_info=True)
        keep_stale(conn, "pulls", previous)
        return
    version = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    origin = Origin("pulls", version, ingested_at)
    newest: str | None = None
    for pr in json.loads(text):
        project_pull(conn, origin, repo, pr)
        for at in (pr["createdAt"], pr["mergedAt"]):
            if at is not None:
                newest = at if newest is None else max(newest, at)
    if newest is not None:
        conn.execute("INSERT INTO watermarks (source, watermark) VALUES (?, ?)", ("pulls", newest))


def ingest_runs(
    conn: sqlite3.Connection,
    root: Path,
    ingested_at: str,
    gh: Callable[[list[str], Path], str],
    previous: Path,
) -> None:
    """The deploy workflow's runs, the read landed.py makes (rul-982e177974, beside source (h) of
    Requirement 10, which stays the source of deploy ranges): a ci_run per run keyed by its id with
    its gh fields, and an observed deploys edge to the commit its title names. Read only from a
    target with a remote that carries .github/workflows/deploy.yml; a gh call that fails leaves the
    source stale, as the pulls source does. The watermark is the newest createdAt."""
    if not (root / DEPLOY_WORKFLOW).is_file() or not git(root, "remote"):
        return
    try:
        text = gh(RUN_LIST, root)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else exc
        log.error("next_projection: runs unreachable, kept stale: %s", detail, exc_info=True)
        keep_stale(conn, "runs", previous)
        return
    version = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    origin = Origin("runs", version, ingested_at)
    newest: str | None = None
    for run in json.loads(text):
        node = put_node(conn, origin, "ci_run", str(run["databaseId"]), run)
        if m := DEPLOY_TITLE.fullmatch(run["displayTitle"]):
            commit = put_node(conn, origin, "commit", m.group(1))
            put_edge(conn, origin, node, commit, "deploys", run["createdAt"])
        newest = run["createdAt"] if newest is None else max(newest, run["createdAt"])
    if newest is not None:
        conn.execute("INSERT INTO watermarks (source, watermark) VALUES (?, ?)", ("runs", newest))


def utc_of(where: str, text: str, value: str) -> str:
    """A ruling time in the events log's form as UTC, read as rulings.parse_time reads it; a
    time it cannot read, such as a zone the local zone does not name, stops the build."""
    try:
        return rulings.parse_time(value).strftime(TIME_FORM)
    except ValueError:
        log.error("next_projection: %s: unreadable ruling time %s", where, value, exc_info=True)
        raise ProjectionError(f"{where}: {text}") from None


def ingest_decisions(conn: sqlite3.Connection, project: Path, ingested_at: str) -> None:
    """Every docs/decisions/*.md of the project dir (source (e) of Requirement 10, read in place
    of the Handoff's Rulings sections, which the handoff gate holds equal to these lines): a ruling
    per line with its time, the time in UTC, its option and what it rules, keyed by the id it
    names, else by rulings.ruling_id over decisions|<time>|<option>. The watermark is the newest
    ruling time in UTC."""
    newest: str | None = None
    for path in sorted((project / DECISIONS_LOG).glob("*.md")):
        rel = path.relative_to(project)
        origin = Origin("decisions", file_version(path), ingested_at)
        for n, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not DATE_LED.match(text):
                continue
            m = DECISION_RULING.fullmatch(text)
            if m is None:
                raise ProjectionError(f"{rel}:{n}: {text}")
            stamp, rid, option, what = m.groups()
            at = utc_of(f"{rel}:{n}", text, stamp)
            key = rid or rulings.ruling_id("decisions", stamp, option)
            props = {"time": stamp, "at": at, "option": option, "what": what, "source": "decisions"}
            put_node(conn, origin, "ruling", key, props)
            newest = at if newest is None else max(newest, at)
    if newest is not None:
        conn.execute(
            "INSERT INTO watermarks (source, watermark) VALUES (?, ?)", ("decisions", newest)
        )


def ingest_rulings(conn: sqlite3.Connection, project: Path, ingested_at: str) -> None:
    """Every store file of the project dir (source (e) of Requirement 10): a ruling per line keyed
    by its id with its time, the time in UTC, header, option, notes and source. It runs after the
    decisions log, so a ruling both hold keeps the store's fields beside the log's what. A line
    that is not a JSON object with those fields stops the build. The watermark is the newest
    ruling time in UTC."""
    newest: str | None = None
    for path in sorted((project / rulings.STORE_SUBDIR).glob("*.jsonl")):
        rel = path.relative_to(project)
        origin = Origin("rulings", file_version(path), ingested_at)
        for n, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not text.strip():
                continue
            where = f"{rel}:{n}"
            try:
                entry = json.loads(text)
            except json.JSONDecodeError:
                log.error("next_projection: %s: not a ruling line", where, exc_info=True)
                raise ProjectionError(f"{where}: {text}") from None
            if not isinstance(entry, dict) or any(k not in entry for k in RULING_KEYS):
                raise ProjectionError(f"{where}: {text}")
            at = utc_of(where, text, entry["time"])
            props = {k: entry[k] for k in RULING_KEYS if k != "id"} | {"at": at}
            put_node(conn, origin, "ruling", entry["id"], props)
            newest = at if newest is None else max(newest, at)
    if newest is not None:
        conn.execute(
            "INSERT INTO watermarks (source, watermark) VALUES (?, ?)", ("rulings", newest)
        )


def read_ledgers(
    conn: sqlite3.Connection, source: str, paths: tuple[Path, ...], previous: Path
) -> list[tuple[Path, str]] | None:
    """The text of each ledger path of a source, or None when one cannot be read, a path that
    does not exist among them: the exception is logged and the source left stale at the previous
    store's watermark, and the build goes on (the spec's Error handling)."""
    texts = []
    for path in paths:
        try:
            texts.append((path, path.read_text(encoding="utf-8")))
        except OSError:
            log.error(
                "next_projection: %s ledger unreadable, kept stale: %s", source, path, exc_info=True
            )
            keep_stale(conn, source, previous)
            return None
    return texts


def ledger_rows(path: Path, text: str, keys: tuple[str, ...]) -> list[tuple[str, dict]]:
    """Each line of a ledger as (where, row); a line that is not a JSON object with the keys, a
    cost_usd that is not a Decimal string, or a run key not in next_auto.RUN_KEY_FORM stops the
    build naming the path and line."""
    rows = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{path}:{n}: {line}"
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            log.error("next_projection: %s:%d: not a ledger line", path, n, exc_info=True)
            raise ProjectionError(where) from None
        if not isinstance(row, dict) or any(k not in row for k in keys):
            raise ProjectionError(where)
        cost = row["cost_usd"]
        run = row.get("parent_session_id") or row["session_id"]
        if not isinstance(cost, str) or not DECIMAL_STRING.fullmatch(cost):
            raise ProjectionError(where)
        if not isinstance(run, str) or not next_auto.RUN_KEY_FORM.fullmatch(run):
            raise ProjectionError(where)
        rows.append((where, row))
    return rows


def ingest_closeout(
    conn: sqlite3.Connection, paths: tuple[Path, ...], ingested_at: str, previous: Path
) -> None:
    """The close-out ledger's JSON lines (source (f) of Requirement 10): a cost per line keyed
    <session>/<day>/<model>, one per session, UTC day and model as closeout_ledger.rows_from writes
    them, spent in the run of its session, a subagent run's in its parent session's. The watermark
    is the newest modification time among the files, a line carrying a day and no time."""
    texts = read_ledgers(conn, "closeout", paths, previous) if paths else None
    if texts is None:
        return
    newest: str | None = None
    for path, text in texts:
        at = datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime(TIME_FORM)
        newest = at if newest is None else max(newest, at)
        origin = Origin("closeout", file_version(path), ingested_at)
        for _, row in ledger_rows(path, text, CLOSEOUT_KEYS):
            key = f"{row['session_id']}/{row['day']}/{row['model']}"
            cost = put_node(conn, origin, "cost", key, row)
            run = put_node(conn, origin, "run", row.get("parent_session_id") or row["session_id"])
            put_edge(conn, origin, cost, run, "spentIn", at)
    if newest is not None:
        conn.execute(
            "INSERT INTO watermarks (source, watermark) VALUES (?, ?)", ("closeout", newest)
        )


def ingest_factory(
    conn: sqlite3.Connection, paths: tuple[Path, ...], ingested_at: str, previous: Path
) -> None:
    """The night runner's ledger (source (g) of Requirement 10): a cost per line keyed by its
    session id, its ts in UTC as at, spent in the run of that session id; the factory ledger,
    which carries no session id, is not read (D11). The watermark is the newest ts in UTC."""
    texts = read_ledgers(conn, "factory", paths, previous) if paths else None
    if texts is None:
        return
    newest: str | None = None
    for path, text in texts:
        origin = Origin("factory", file_version(path), ingested_at)
        for where, row in ledger_rows(path, text, FACTORY_KEYS):
            try:
                at = datetime.fromisoformat(row["ts"]).astimezone(UTC).strftime(TIME_FORM)
            except (TypeError, ValueError):
                log.error("next_projection: %s: unreadable ts", where, exc_info=True)
                raise ProjectionError(where) from None
            cost = put_node(conn, origin, "cost", row["session_id"], row | {"at": at})
            run = put_node(conn, origin, "run", row["session_id"])
            put_edge(conn, origin, cost, run, "spentIn", at)
            newest = at if newest is None else max(newest, at)
    if newest is not None:
        conn.execute(
            "INSERT INTO watermarks (source, watermark) VALUES (?, ?)", ("factory", newest)
        )


def stored_requirements(conn: sqlite3.Connection, closes: str) -> list[str]:
    """The requirement ids in the store for a declared closes target, requirement:<slug>/R<n>:
    the requirement of that slug and number in the spec version the checkout holds."""
    prefix = closes.removeprefix("requirement:") + "/"
    keys = conn.execute(
        "SELECT key FROM node WHERE type = 'requirement' AND substr(key, 1, ?) = ?",
        (len(prefix), prefix),
    ).fetchall()
    return [f"requirement:{key}" for (key,) in keys]


def derive_closes(conn: sqlite3.Connection, ingested_at: str) -> None:
    """Requirement 13's plan_step closes requirement (rule closes-current-version): each declared
    closes edge of a plan step, its requirement:<slug>/R<n> joined to the requirement of that slug
    and number in the store, the spec version the checkout holds. A Closes: id with no such
    requirement derives nothing, and a pull request's Closes: is not this rule's. The derived edge
    carries the declared edge's source, version and time."""
    declared = conn.execute(
        "SELECT src, dst, source, source_version, asserted_at FROM edge"
        " WHERE type = 'closes' AND method = 'declared' AND src LIKE 'plan_step:%'"
    ).fetchall()
    for src, dst, source, version, at in declared:
        origin = Origin(source, version, ingested_at)
        for target in stored_requirements(conn, dst):
            put_edge(conn, origin, src, target, "closes", at, "derived", "closes-current-version")


def derive_deploy_ranges(conn: sqlite3.Connection, root: Path, ingested_at: str) -> None:
    """Requirement 13's deploy includes commit (rule deploy-range): a target's deploys in time
    order, each that succeeded including its own commit and every commit git rev-list gives from
    the previous successful deploy's commit on that target to its own. A deploy that failed or was
    skipped shipped nothing, includes nothing and is no target's previous one. Each commit gets a
    node, and each edge the deploy's records source, version and time."""
    rows = conn.execute(
        "SELECT n.key, n.props, s.source_version FROM node n JOIN node_source s USING (type, key)"
        " WHERE n.type = 'deploy' AND s.source = 'records'"
    ).fetchall()
    deploys = []
    for key, props, version in rows:
        p = json.loads(props)
        if p.get("conclusion") == "success":
            target = key.split("/", 1)[1]
            deploys.append((target, p["at"], key, p.get("sha") or p["base"], version))
    previous: dict[str, str] = {}
    for target, at, key, sha, version in sorted(deploys):
        origin = Origin("records", version, ingested_at)
        since = previous.get(target)
        shipped = git(root, "rev-list", f"{since}..{sha}").split() if since else []
        for commit in sorted({sha, *shipped}):
            dst = put_node(conn, origin, "commit", commit)
            put_edge(conn, origin, f"deploy:{key}", dst, "includes", at, "derived", "deploy-range")
        previous[target] = sha


def derive_implemented_by(conn: sqlite3.Connection, ingested_at: str) -> None:
    """Requirement 13's requirement implementedBy pull_request (rule merged-closes): each declared
    closes edge of a merged pull request, joined to the stored requirement as a plan step's is,
    asserted at the merge. An open or closed pull request implements nothing. The edge carries the
    declared edge's source and version."""
    declared = conn.execute(
        "SELECT e.src, e.dst, e.source, e.source_version, n.props FROM edge e JOIN node n"
        " ON n.type = 'pull_request' AND e.src = 'pull_request:' || n.key"
        " WHERE e.type = 'closes' AND e.method = 'declared'"
    ).fetchall()
    for src, dst, source, version, props in declared:
        pr = json.loads(props)
        if pr["state"] != "MERGED":
            continue
        origin = Origin(source, version, ingested_at)
        at = pr["mergedAt"]
        for requirement in stored_requirements(conn, dst):
            put_edge(
                conn, origin, requirement, src, "implementedBy", at, "derived", "merged-closes"
            )


def derive_stacked_on(conn: sqlite3.Connection, ingested_at: str) -> None:
    """Requirement 13's pull_request stackedOn pull_request (rule base-chain): a pull request
    whose base branch is the head branch of another open pull request of its repository, asserted
    at its creation, with its pulls source and version."""
    rows = conn.execute(
        "SELECT n.key, n.props, s.source_version FROM node n JOIN node_source s USING (type, key)"
        " WHERE n.type = 'pull_request' AND s.source = 'pulls'"
    ).fetchall()
    pulls = [(key, json.loads(props), version) for key, props, version in rows]
    open_heads: dict[tuple[str, str], list[str]] = {}
    for key, pr, _ in pulls:
        if pr["state"] == "OPEN":
            open_heads.setdefault((key.rpartition("#")[0], pr["headRefName"]), []).append(key)
    for key, pr, version in pulls:
        origin = Origin("pulls", version, ingested_at)
        src, at = f"pull_request:{key}", pr["createdAt"]
        for base in open_heads.get((key.rpartition("#")[0], pr["baseRefName"]), []):
            if base != key:
                dst = f"pull_request:{base}"
                put_edge(conn, origin, src, dst, "stackedOn", at, "derived", "base-chain")


def contains(root: Path, head: str, commit: str) -> bool | None:
    """Whether head is the commit or one of its descendants in the checkout, by git merge-base
    --is-ancestor; None when git cannot answer, as when the checkout does not hold one of the two,
    which a fresh clone does not for a deleted branch's commits."""
    done = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", commit, head],
        capture_output=True,
        text=True,
    )
    return {0: True, 1: False}.get(done.returncode)


def derive_produced_in(
    conn: sqlite3.Connection, root: Path, reads: list[RecordRead], ingested_at: str
) -> None:
    """Requirement 13's plan_step producedIn pull_request, as ruled (rul-b08d06de25): a step.done
    line with a run key joins the first pull request its run opened after it in its record (rule
    runkey); a line with no run key, or whose run opened none after it, joins the first one in its
    record opened after it whose head contains its commit (rule ancestry). A head git cannot judge
    against the line's commit ends that line's search, since it may be the first to hold the
    commit, and the lines that met one are counted in one warning. A pull request the store holds
    no node for derives nothing. Each edge carries its record's source and version and the time
    the pull request opened."""
    numbers = {
        json.loads(props)["number"]: f"pull_request:{key}"
        for key, props in conn.execute("SELECT key, props FROM node WHERE type = 'pull_request'")
    }
    unjudged = 0
    for read in reads:
        opened = sorted(read.opened, key=lambda pr: pr.at)
        for step, ev in read.steps:
            later = [pr for pr in opened if pr.at >= ev.at]
            run = ev.values.get("runkey")
            found = next((pr for pr in later if run and pr.values.get("runkey") == run), None)
            rule = "runkey"
            if found is None:
                rule = "ancestry"
                for pr in later:
                    held = contains(root, pr.get("head"), ev.get("sha"))
                    if held is None:
                        unjudged += 1
                        break
                    if held:
                        found = pr
                        break
            if found is not None and int(found.get("pr")) in numbers:
                dst = numbers[int(found.get("pr"))]
                put_edge(conn, read.origin, step, dst, "producedIn", found.at, "derived", rule)
    if unjudged:
        log.warning(
            "next_projection: %d step lines met a commit the checkout does not hold; they derive"
            " no producedIn edge by ancestry",
            unjudged,
        )


def derive_unblocks(conn: sqlite3.Connection, ingested_at: str) -> None:
    """Requirement 13's ruling unblocks <node> (rule ruling-token), as ruled (rul-68231e8df9): each
    F-<n>, DOC-<n>, R<n>, D<n>, S<n> and Step <n> token in the text a ruling source wrote, resolved
    to a node the store holds and never creating one. R<n> and D<n> resolve to the requirement or
    decision of the one capability slug the text names that has that number; any other token only
    when one node alone carries it, as its key or, for a plan step, its number. Each edge carries
    the source that wrote the text, its version and the ruling's time."""
    scoped: dict[str, dict[str, list[str]]] = {}
    carried: dict[str, list[str]] = {}
    for node_type, key in conn.execute("SELECT type, key FROM node"):
        if node_type in ("requirement", "decision"):
            slug, number = key.split("/")[:2]
            scoped.setdefault(number, {}).setdefault(slug, []).append(f"{node_type}:{key}")
        else:
            token = f"Step {key.rsplit('/', 1)[1]}" if node_type == "plan_step" else key
            carried.setdefault(token, []).append(f"{node_type}:{key}")
    rows = conn.execute(
        "SELECT n.key, n.props, s.source, s.source_version FROM node n JOIN node_source s"
        " USING (type, key) WHERE n.type = 'ruling'"
    ).fetchall()
    for key, props, source, version in rows:
        ruling = json.loads(props)
        text = " ".join(ruling[f] for f in RULING_TEXT[source] if ruling.get(f))
        words = set(SLUG_WORD.findall(text.lower()))
        origin = Origin(source, version, ingested_at)
        for token in sorted(set(RULING_TOKEN.findall(text))):
            if token[0] in "RD" and token[1:].isdigit():
                slugs = [slug for slug in scoped.get(token, {}) if slug in words]
                targets = scoped[token][slugs[0]] if len(slugs) == 1 else []
            else:
                found = carried.get(token, [])
                targets = found if len(found) == 1 else []
            for dst in targets:
                src = f"ruling:{key}"
                put_edge(
                    conn, origin, src, dst, "unblocks", ruling["at"], "derived", "ruling-token"
                )


def ingest(
    conn: sqlite3.Connection,
    target: Path,
    project: Path,
    plan_globs: tuple[str, ...],
    gh: Callable[[list[str], Path], str],
    closeout: tuple[Path, ...],
    factory: tuple[Path, ...],
) -> None:
    """Every source into the store: the target checkout's, which must be a git work tree since
    every tracked source is versioned by its commit sha, its repository's pull requests, the
    plans and rulings under the project dir, and the ledgers given; then the derived edges that
    join them."""
    root = Path(git(target, "rev-parse", "--show-toplevel"))
    ingested_at = datetime.now(UTC).strftime(TIME_FORM)
    previous = project / STORE
    reads = ingest_records(conn, root, ingested_at)
    ingest_specs(conn, root, ingested_at)
    ingest_pulls(conn, root, ingested_at, gh, previous)
    ingest_runs(conn, root, ingested_at, gh, previous)
    ingest_plans(conn, project, plan_globs, ingested_at)
    ingest_decisions(conn, project, ingested_at)
    ingest_rulings(conn, project, ingested_at)
    ingest_closeout(conn, closeout, ingested_at, previous)
    ingest_factory(conn, factory, ingested_at, previous)
    derive_closes(conn, ingested_at)
    derive_deploy_ranges(conn, root, ingested_at)
    derive_implemented_by(conn, ingested_at)
    derive_stacked_on(conn, ingested_at)
    derive_produced_in(conn, root, reads, ingested_at)
    derive_unblocks(conn, ingested_at)


def build(
    target: Path,
    project: Path,
    plan_globs: tuple[str, ...] = PLAN_GLOBS,
    gh: Callable[[list[str], Path], str] = run_gh,
    closeout: tuple[Path, ...] = (),
    factory: tuple[Path, ...] = (),
) -> Path:
    """Write the store to projection.sqlite.tmp and rename it over projection.sqlite once every
    source parsed (Requirement 9); a build that fails leaves the previous store and no tmp file."""
    store = project / STORE
    tmp = store.with_name(store.name + ".tmp")
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp.unlink(missing_ok=True)
    try:
        conn = sqlite3.connect(tmp)
        try:
            conn.executescript(SCHEMA)
            ingest(conn, target, project, plan_globs, gh, closeout, factory)
            conn.commit()
        finally:
            conn.close()
        tmp.replace(store)
    finally:
        # after the rename there is no tmp file; after a failure this removes the partial one
        tmp.unlink(missing_ok=True)
    return store


def status_lines(project: Path) -> list[str]:
    """One line per source with its watermark, "never read" when it has none, the count of files
    it skipped, if any, and "stale" when the build could not reach it; or "never built" when the
    store is missing."""
    store = project / STORE
    if not store.is_file():
        return ["never built"]
    conn = sqlite3.connect(store)
    try:
        rows = conn.execute(
            "SELECT source, watermark, skipped, stale FROM watermarks ORDER BY source"
        ).fetchall()
    finally:
        conn.close()
    return [
        f"{src} {mark or 'never read'}"
        + (f" skipped {skipped}" if skipped else "")
        + (" stale" if stale else "")
        for src, mark, skipped, stale in rows
    ]


def open_store(project: Path) -> sqlite3.Connection:
    """The store opened read-only, so no query writes it; a missing store raises
    sqlite3.OperationalError rather than leaving an empty file behind."""
    return sqlite3.connect(f"{(project / STORE).resolve().as_uri()}?mode=ro", uri=True)


def freshness(
    conn: sqlite3.Connection, touched: set[str] | None, now: datetime
) -> tuple[dict[str, str | None], list[str]]:
    """as_of, the watermark of each source an answer touched, every source when touched is None,
    and stale, those of them with no watermark, one older than STALE_AFTER at now, or one the last
    build could not reach (Requirement 14)."""
    rows = conn.execute("SELECT source, watermark, stale FROM watermarks").fetchall()
    known = {source: (mark, flag) for source, mark, flag in rows}
    as_of: dict[str, str | None] = {}
    stale: list[str] = []
    for source in sorted(known if touched is None else touched):
        mark, flag = known.get(source, (None, 0))
        as_of[source] = mark
        old = mark is None or datetime.strptime(mark, TIME_FORM).replace(tzinfo=UTC) < (
            now - STALE_AFTER
        )
        if old or flag:
            stale.append(source)
    return as_of, stale


def bounded(items: list, answer_of: Callable[[list], dict], hint: str) -> dict:
    """The answer over the first CAP items, then over fewer while its JSON is longer than
    MAX_CHARS; "more" counts the items left out and the hint names the filter to narrow by
    (Requirement 15). A node's own props are never cut."""
    kept = items[:CAP]
    while True:
        answer = answer_of(kept)
        if len(kept) < len(items):
            answer |= {"more": len(items) - len(kept), "hint": hint}
        if not kept or len(json.dumps(answer)) <= MAX_CHARS:
            return answer
        kept = kept[:-1]


# an edge seen from one of its ends: (edge type, direction from this end, the other end, method,
# rule, source, asserted_at)
NodeEdge = tuple[str, str, str, str, str | None, str, str]


def node_read(
    project: Path, node_id: str, now: datetime
) -> tuple[dict | None, list[NodeEdge], dict]:
    """A node's props, None when the store holds no such node, its edges both ways in sorted order,
    and the envelope with no match yet: the node touched its own sources and its edges', and a
    node the store does not hold touched every source."""
    node_type, _, key = node_id.partition(":")
    conn = open_store(project)
    try:
        row = conn.execute(
            "SELECT props FROM node WHERE type = ? AND key = ?", (node_type, key)
        ).fetchone()
        sources = conn.execute(
            "SELECT source FROM node_source WHERE type = ? AND key = ?", (node_type, key)
        ).fetchall()
        edges = conn.execute(
            "SELECT type, 'out', dst, method, rule, source, asserted_at FROM edge WHERE src = ?"
            " UNION ALL"
            " SELECT type, 'in', src, method, rule, source, asserted_at FROM edge WHERE dst = ?",
            (node_id, node_id),
        ).fetchall()
        touched = {s for (s,) in sources} | {e[5] for e in edges} if row else None
        as_of, stale = freshness(conn, touched, now)
    finally:
        conn.close()
    edges.sort(key=lambda e: (*e[:4], e[4] or "", e[5]))
    props = json.loads(row[0]) if row else None
    return props, edges, {"matches": [], "as_of": as_of, "stale": stale}


def edge_entry(edge: NodeEdge) -> dict:
    _, direction, other, method, rule, source, asserted_at = edge
    found = {"direction": direction, "node": other, "method": method, "rule": rule}
    return found | {"source": source, "at": asserted_at}


def query_entity(project: Path, node_id: str, now: datetime | None = None) -> dict:
    """`query entity <type>:<key>`: the node, its props and its edges both ways grouped by type,
    each group with its full count (Requirement 15)."""
    props, edges, envelope = node_read(project, node_id, now or datetime.now(UTC))
    if props is None:
        return envelope
    counts = Counter(e[0] for e in edges)

    def answer_of(kept: list) -> dict:
        groups: dict[str, dict] = {t: {"count": n, "edges": []} for t, n in sorted(counts.items())}
        for edge in kept:
            groups[edge[0]]["edges"].append(edge_entry(edge))
        match = {"id": node_id, "type": node_id.partition(":")[0], "props": props, "edges": groups}
        return envelope | {"matches": [match]}

    hint = f"narrow by edge type: query neighbors {node_id} --type <edge type>"
    return bounded(edges, answer_of, hint)


def query_timeline(
    project: Path, node_id: str, since: str | None = None, now: datetime | None = None
) -> dict:
    """`query timeline <id>`: every dated fact on the node in time order, its DATED_PROPS and each
    edge's asserted time, all in UTC, with the form its source wrote kept as original (Requirement
    15, the spec's Assumptions); only those at or after since, a UTC time, when given, so a capped
    timeline reads on from the last fact it listed (rul-1ca905ea05)."""
    props, edges, envelope = node_read(project, node_id, now or datetime.now(UTC))
    if props is None:
        return envelope
    written = next((props[k] for k in ORIGINAL_PROPS if isinstance(props.get(k), str)), None)
    facts = [
        {"at": props[name], "original": written if name == "at" and written else props[name]}
        | {"fact": name}
        for name in DATED_PROPS
        if isinstance(props.get(name), str)
    ]
    facts += [{"original": e[6], "fact": e[0]} | edge_entry(e) for e in edges]
    facts = [f for f in facts if since is None or f["at"] >= since]
    facts.sort(key=lambda f: (f["at"], f["fact"], f.get("node", "")))

    def answer_of(kept: list) -> dict:
        match = {"id": node_id, "type": node_id.partition(":")[0], "facts": kept}
        return envelope | {"matches": [match]}

    hint = f"narrow by time: query timeline {node_id} --since <time>"
    return bounded(facts, answer_of, hint)


def query_find(
    project: Path, text: str, node_type: str | None = None, now: datetime | None = None
) -> dict:
    """`query find <text>`: the id and type of each node, of node_type when given, whose id or
    FIND_PROPS value holds the text, case folded (Requirement 15). It reads every node, so it
    touches every source."""
    needle = text.casefold()
    now = now or datetime.now(UTC)
    conn = open_store(project)
    try:
        rows = conn.execute("SELECT type, key, props FROM node ORDER BY type, key").fetchall()
        as_of, stale = freshness(conn, None, now)
    finally:
        conn.close()
    found = []
    for row_type, key, props in rows:
        if node_type is not None and row_type != node_type:
            continue
        values = json.loads(props)
        fields = (f"{row_type}:{key}", *(values.get(p) for p in FIND_PROPS))
        if any(isinstance(v, str) and needle in v.casefold() for v in fields):
            found.append({"id": f"{row_type}:{key}", "type": row_type})
    hint = "narrow by node type: query find <text> --type <node type>"
    return bounded(found, lambda kept: {"matches": kept, "as_of": as_of, "stale": stale}, hint)


# a link is one edge seen from one of its ends: (the other end, edge type, direction from this end,
# method, rule, source)
Link = tuple[str, str, str, str, str | None, str]


def adjacency(conn: sqlite3.Connection, edge_type: str | None) -> dict[str, list[Link]]:
    """Every edge, or every edge of edge_type, as a link under each of its ends, each node's links
    sorted so a search walks them in one order."""
    sql = "SELECT src, dst, type, method, rule, source FROM edge"
    rows = conn.execute(sql + " WHERE type = ?", (edge_type,)) if edge_type else conn.execute(sql)
    links: dict[str, list[Link]] = defaultdict(list)
    for src, dst, kind, method, rule, source in rows:
        links[src].append((dst, kind, "out", method, rule, source))
        links[dst].append((src, kind, "in", method, rule, source))
    for found in links.values():
        found.sort(key=lambda link: (*link[:4], link[4] or "", link[5]))
    return links


def reach(
    links: dict[str, list[Link]], start: str, depth: int
) -> dict[str, tuple[int, str, Link]]:
    """Breadth first from start to depth hops: each node reached, start aside, with its hop, the
    node it was reached from and the link it was reached by, the first in sorted order at its
    shortest hop."""
    reached: dict[str, tuple[int, str, Link]] = {}
    frontier = [start]
    for number in range(1, depth + 1):
        found = []
        for node in frontier:
            for link in links.get(node, ()):
                if link[0] != start and link[0] not in reached:
                    reached[link[0]] = (number, node, link)
                    found.append(link[0])
        frontier = sorted(found)
    return reached


def hop_match(node: str, number: int, came_from: str, link: Link) -> dict:
    _, kind, direction, method, rule, source = link
    edge = {"type": kind, "direction": direction, "from": came_from, "method": method}
    edge |= {"rule": rule, "source": source}
    return {"id": node, "type": node.partition(":")[0], "hop": number, "edge": edge}


def hops_refusal(hops: int) -> str | None:
    """Why a neighbors search of hops is refused, or None (Requirement 15)."""
    if hops < 1:
        return "--hops is at least 1"
    if hops > MAX_HOPS:
        return f"--hops is at most {MAX_HOPS}"
    return None


def walked(conn: sqlite3.Connection, matches: list[dict], now: datetime) -> dict:
    """The envelope over hop matches: they touched the sources of the edges they were reached by,
    and an answer with none touched every source."""
    touched = {m["edge"]["source"] for m in matches} or None
    as_of, stale = freshness(conn, touched, now)
    return {"matches": matches, "as_of": as_of, "stale": stale}


def query_neighbors(
    project: Path,
    node_id: str,
    edge_type: str | None = None,
    hops: int = 1,
    now: datetime | None = None,
) -> dict:
    """`query neighbors <id> --type <edge type> --hops <n>`: the nodes within hops of the node over
    edges of edge_type, every type when None, either way along each edge, in hop order with the
    edge each was reached by (Requirement 15)."""
    if why := hops_refusal(hops):
        raise ValueError(why)
    now = now or datetime.now(UTC)
    conn = open_store(project)
    try:
        reached = reach(adjacency(conn, edge_type), node_id, hops)
        order = sorted(reached, key=lambda node: (reached[node][0], node))
        envelope = walked(conn, [hop_match(n, *reached[n]) for n in order], now)
    finally:
        conn.close()
    hint = f"narrow by edge type or fewer hops: query neighbors {node_id}"
    hint += " --type <edge type> --hops <n>"
    return bounded(envelope["matches"], lambda kept: envelope | {"matches": kept}, hint)


def query_path(project: Path, start: str, end: str, now: datetime | None = None) -> dict:
    """`query path <id> <id>`: the shortest path over edges of any type, either way along each,
    as ordered hops; none when it would take more than MAX_PATH hops (Requirement 15)."""
    now = now or datetime.now(UTC)
    conn = open_store(project)
    try:
        reached = reach(adjacency(conn, None), start, MAX_PATH)
        path: list[dict] = []
        node = end
        while node in reached:
            number, came_from, link = reached[node]
            path.insert(0, hop_match(node, number, came_from, link))
            node = came_from
        return walked(conn, path, now)
    finally:
        conn.close()


def base_first(opened: list[dict], numbers: dict[str, int]) -> list[dict]:
    """Open pull requests in base-chain order: each one stacked on none of them, by number, then
    every one stacked on it, depth first; one left in a cycle follows by number."""
    ids = {pr["id"] for pr in opened}
    stacked: dict[str | None, list[dict]] = defaultdict(list)
    for pr in sorted(opened, key=lambda pr: numbers[pr["id"]]):
        stacked[pr["stackedOn"] if pr["stackedOn"] in ids else None].append(pr)
    order: list[dict] = []
    todo = list(reversed(stacked[None]))
    while todo:
        pr = todo.pop()
        order.append(pr)
        todo.extend(reversed(stacked[pr["id"]]))
    seen = {pr["id"] for pr in order}
    left = [pr for pr in opened if pr["id"] not in seen]
    return order + sorted(left, key=lambda pr: numbers[pr["id"]])


def query_intent(
    project: Path, intent: str, state: str | None = None, now: datetime | None = None
) -> dict:
    """`query intent <id>`: the pull requests that serve the intent, the merged ones by merge time
    with the deploys that include their merge commit, then the open ones in base-chain order, only
    those of state (merged or open) when given (Requirement 16, scenario S4); a closed one never
    merged is left out. It touches the sources of the edges it read, or every source when no pull
    request serves the intent."""
    now = now or datetime.now(UTC)
    conn = open_store(project)
    try:
        serving = conn.execute(
            "SELECT e.src, e.source, n.props FROM edge e JOIN node n"
            " ON n.type = 'pull_request' AND 'pull_request:' || n.key = e.src"
            " WHERE e.type = 'serves' AND e.dst = ? ORDER BY e.src",
            (f"intent:{intent}",),
        ).fetchall()
        touched = {source for _, source, _ in serving}
        merged: list[dict] = []
        opened: list[dict] = []
        numbers: dict[str, int] = {}
        for pr, _, text in serving:
            props = json.loads(text)
            if state is not None and props["state"] != state.upper():
                continue
            found = {"id": pr, "type": "pull_request", "state": props["state"]}
            found["title"] = props["title"]
            if props["state"] == "MERGED":
                deploys = conn.execute(
                    "SELECT DISTINCT d.src, d.source FROM edge m JOIN edge d"
                    " ON d.dst = m.dst AND d.type = 'includes'"
                    " WHERE m.src = ? AND m.type = 'mergedAs' ORDER BY d.src",
                    (pr,),
                ).fetchall()
                touched |= {source for _, source in deploys}
                found |= {"mergedAt": props["mergedAt"], "deploys": [d for d, _ in deploys]}
                merged.append(found)
            elif props["state"] == "OPEN":
                base = conn.execute(
                    "SELECT dst, source FROM edge WHERE src = ? AND type = 'stackedOn'", (pr,)
                ).fetchone()
                touched |= {base[1]} if base else set()
                opened.append(found | {"stackedOn": base[0] if base else None})
                numbers[pr] = props["number"]
        as_of, stale = freshness(conn, touched or None, now)
    finally:
        conn.close()
    merged.sort(key=lambda pr: (pr["mergedAt"], pr["id"]))
    matches = merged + base_first(opened, numbers)
    hint = f"narrow by state: query intent {intent} --state <merged or open>"
    return bounded(matches, lambda kept: {"matches": kept, "as_of": as_of, "stale": stale}, hint)


def query_coverage(project: Path, slug: str, now: datetime | None = None) -> dict:
    """`query coverage <slug>`: each requirement of the capability in the spec version the checkout
    holds, by number, with the merged pull requests whose Closes: names it (its implementedBy
    edges); an empty closedBy names a requirement none closes (Requirement 16, scenario S6)."""
    prefix = f"{slug}/R"
    now = now or datetime.now(UTC)
    conn = open_store(project)
    try:
        keys = conn.execute(
            "SELECT key FROM node WHERE type = 'requirement' AND substr(key, 1, ?) = ?",
            (len(prefix), prefix),
        ).fetchall()
        matches: list[dict] = []
        touched: set[str] = set()
        for (key,) in keys:
            node = f"requirement:{key}"
            closers = conn.execute(
                "SELECT dst, source FROM edge WHERE src = ? AND type = 'implementedBy'", (node,)
            ).fetchall()
            sources = conn.execute(
                "SELECT source FROM node_source WHERE type = 'requirement' AND key = ?", (key,)
            ).fetchall()
            touched |= {s for _, s in closers} | {s for (s,) in sources}
            closed = sorted({pr for pr, _ in closers})
            matches.append({"id": node, "type": "requirement", "closedBy": closed})
        as_of, stale = freshness(conn, touched or None, now)
    finally:
        conn.close()
    matches.sort(key=lambda m: int(m["id"].removeprefix(f"requirement:{prefix}").split("/")[0]))
    hint = f"narrow to one requirement: query entity requirement:{slug}/R<n>/<version>"
    return bounded(matches, lambda kept: {"matches": kept, "as_of": as_of, "stale": stale}, hint)


@dataclass(frozen=True)
class NowTable:
    """The Now table under ## Intents: its Intent column in table order, and each spec slug to
    the intents whose Specs cell names it."""

    intents: tuple[str, ...]
    by_spec: dict[str, list[str]]


def now_table(path: Path) -> NowTable:
    """The Now table of the workspace's setup plan; a missing file, table or column stops the
    query."""
    if not path.is_file():
        raise ProjectionError(f"no setup plan at {path}")
    by_spec: dict[str, list[str]] = {}
    intents: list[str] = []
    section = sub = None
    columns: tuple[int, int] | None = None
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.startswith("## "):
            section, sub = line[3:].strip(), None
        elif line.startswith("### "):
            sub = line[4:].strip()
        elif section == "Intents" and sub == "Now" and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if columns is None:
                if "Intent" not in cells or "Specs" not in cells:
                    raise ProjectionError(f"{path}: the Now table names no Intent or Specs column")
                columns = (cells.index("Intent"), cells.index("Specs"))
            elif not set(line) <= set("|-: "):
                intents.append(cells[columns[0]])
                for named in re.split(r"[;,]", cells[columns[1]]):
                    if SLUG_WORD.fullmatch(named.strip()) and named.strip() != "none":
                        by_spec.setdefault(named.strip(), []).append(cells[columns[0]])
    if columns is None:
        raise ProjectionError(f"{path}: no Now table under ## Intents")
    return NowTable(tuple(intents), by_spec)


def landed_intent(props: dict, table: NowTable) -> str:
    """landed.py's Intent cell: the pull request's Intent: ids, each one the Now table holds, else
    the intents its Spec: serves through the table's Specs cells, else none."""
    if "intent" in props:
        named = [item.strip() for item in re.split(r"[;,]", props["intent"]) if item.strip()]
        if not named:
            return "none (Intent: names nothing)"
        for item in named:
            if item not in table.intents:
                return f"none (Intent: {item} is not a Now intent)"
        return ", ".join(named)
    m = LANDED_SPEC.match(props.get("spec", ""))
    return ", ".join(table.by_spec.get(m.group(1), []) if m else []) or "none"


def shipped_by(
    position: int, runs: list[dict], order: dict[str, int]
) -> tuple[str, dict | None, str]:
    """landed.py's state of the merge at position, from the runs whose title names it or a later
    merge: the first that succeeded, else one still running, else the last that failed."""
    covering = sorted(
        (run for run in runs if order.get(run["sha"], -1) >= position),
        key=lambda run: (run["createdAt"], run["databaseId"]),
    )
    passed = [r for r in covering if r["status"] == "completed" and r["conclusion"] == "success"]
    if passed:
        return "PASS", passed[0], "success"
    active = [run for run in covering if run["status"] != "completed"]
    if active:
        return "PENDING", active[0], active[0]["status"]
    failed = [run for run in covering if run["conclusion"] in FAILED_RUNS]
    if failed:
        return "FAIL", failed[-1], failed[-1]["conclusion"]
    if covering:
        return "PENDING", covering[-1], f"{covering[-1]['conclusion']}, no later deploy succeeded"
    return "PENDING", None, "no deploy run titled with this merge or a later one"


def query_landed(project: Path, date: str) -> list[str]:
    """`query landed --date <d>`: the Landed table of the window docs/handoff/<d>.md names in its
    Since and Until lines (rul-0723e3dbdc), in the shape landed.py --format md prints: each pull
    request merged after Since through Until in merge order, with its New York merge time, the
    deploy run that shipped it and its intent (Requirement 16, scenario S5)."""
    handoff = project / HANDOFF / f"{date}.md"
    if not handoff.is_file():
        raise ProjectionError(f"no Handoff at {HANDOFF / handoff.name}")
    window: dict[str, str] = {}
    for line in handoff.read_text(encoding="utf-8").splitlines():
        if (m := WINDOW_LINE.fullmatch(line)) and m.group(1) not in window:
            window[m.group(1)] = m.group(2)
    if set(window) != {"Since", "Until"}:
        raise ProjectionError(f"{HANDOFF / handoff.name}: no Since and Until lines")
    table = now_table(project / SETUP_PLAN)
    conn = open_store(project)
    try:
        prs = conn.execute(
            "SELECT n.props, e.dst FROM node n JOIN edge e"
            " ON e.src = 'pull_request:' || n.key AND e.type = 'mergedAs'"
            " WHERE n.type = 'pull_request'"
        ).fetchall()
        runs = conn.execute(
            "SELECT n.props, e.dst FROM node n JOIN edge e"
            " ON e.src = 'ci_run:' || n.key AND e.type = 'deploys' WHERE n.type = 'ci_run'"
        ).fetchall()
    finally:
        conn.close()
    merged = sorted(
        ((json.loads(props), dst.removeprefix("commit:")) for props, dst in prs),
        key=lambda pr: (pr[0]["mergedAt"], pr[0]["number"]),
    )
    order = {oid: index for index, (_, oid) in enumerate(merged)}
    deploys = [json.loads(props) | {"sha": dst.removeprefix("commit:")} for props, dst in runs]

    def position(sha: str) -> int:
        found = [index for oid, index in order.items() if oid.startswith(sha)]
        if len(sha) < 7 or len(found) != 1:
            raise ProjectionError(f"{sha} is not the merge commit of one merged pull request")
        return found[0]

    start, end = position(window["Since"]), position(window["Until"])
    if end < start:
        raise ProjectionError(f"Until {window['Until']} was merged before Since {window['Since']}")
    lines = list(LANDED_HEADER)
    for index in range(start + 1, end + 1):
        props, _ = merged[index]
        state, run, detail = shipped_by(index, deploys, order)
        if run:
            detail = f"{detail}, deploy {run['sha'][:7]}"
        moment = datetime.fromisoformat(props["mergedAt"]).astimezone(NEW_YORK)
        cells = (
            f"#{props['number']}",
            props["title"].replace("|", "\\|"),
            moment.strftime("%Y-%m-%d %H:%M:%S %Z"),
            str(run["databaseId"]) if run else "none",
            f"{state} ({detail})",
            landed_intent(props, table),
        )
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def answer_lines(answer: dict) -> list[str]:
    """An answer's text form: each match's id, the count left out with its hint, and the stale
    sources; an answer with no match reads no match as of each source's watermark (Requirement
    14, scenario S7)."""
    stale = ", ".join(answer["stale"])
    if not answer["matches"]:
        marks = ", ".join(f"{s} {m or 'never read'}" for s, m in answer["as_of"].items())
        flag = f" (stale: {stale})" if stale else ""
        return [f"no match as of {marks or 'no source read'}{flag}"]
    lines = [m["id"] for m in answer["matches"]]
    if "more" in answer:
        lines.append(f"more {answer['more']}: {answer['hint']}")
    if stale:
        lines.append(f"stale: {stale}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="next_projection.py",
        description="The delivery projection: build the store from a checkout, print its status,"
        " answer typed queries from it.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="rebuild the store from the target checkout")
    b.add_argument("--target", type=Path, required=True, help="the checkout the sources live in")
    b.add_argument(
        "--plans",
        action="append",
        help="a plan glob relative to the project dir, repeatable; defaults to "
        + ", ".join(PLAN_GLOBS),
    )
    b.add_argument(
        "--closeout-jsonl",
        action="append",
        type=Path,
        default=[],
        help="a close-out ledger's JSON lines (closeout_ledger.py --jsonl), repeatable",
    )
    b.add_argument(
        "--factory-ledger",
        action="append",
        type=Path,
        default=[],
        help="the night runner's ledger.jsonl, repeatable",
    )
    s = sub.add_parser("status", help="print each source's watermark")
    # Requirement 15: typed tools only; no subcommand takes SQL
    q = sub.add_parser("query", help="answer a typed question from the store, as JSON")
    tools = q.add_subparsers(dest="tool", required=True)
    e = tools.add_parser("entity", help="a node, its props and its edges grouped by type")
    e.add_argument("id", help="the node id, <type>:<key>")
    f = tools.add_parser("find", help="nodes whose id, title, header or ruling text holds the text")
    f.add_argument("text")
    f.add_argument("--type", dest="node_type", choices=NODE_TYPES, help="only nodes of this type")
    n = tools.add_parser("neighbors", help="the nodes within --hops of a node, by hop")
    n.add_argument("id", help="the node id, <type>:<key>")
    n.add_argument("--type", dest="edge_type", help="follow only edges of this type")
    n.add_argument("--hops", type=int, default=1, help=f"at most {MAX_HOPS}; defaults to 1")
    w = tools.add_parser("path", help=f"the shortest path between two nodes, up to {MAX_PATH} hops")
    w.add_argument("start", help="the node id the path starts at")
    w.add_argument("end", help="the node id the path ends at")
    t = tools.add_parser("timeline", help="every dated fact on a node, in time order")
    t.add_argument("id", help="the node id, <type>:<key>")
    t.add_argument("--since", help="only facts at or after this UTC time, as 2026-10-23T01:47:57Z")
    i = tools.add_parser("intent", help="an intent's merged pull requests and open ones")
    i.add_argument("intent", help="the intent id, as 30.1")
    i.add_argument("--state", choices=("merged", "open"), help="only pull requests in this state")
    c = tools.add_parser("coverage", help="a capability's requirements and the PRs that close them")
    c.add_argument("slug", help="the capability slug, as shadow-agents")
    d = tools.add_parser("landed", help="a Handoff day's Landed table, as landed.py prints it")
    d.add_argument("--date", required=True, help="the day of docs/handoff/<date>.md")
    tool_parsers = (e, f, n, w, t, i, c)
    for p in tool_parsers:
        p.add_argument("--text", dest="as_text", action="store_true", help="print lines, not JSON")
    for p in (b, s, *tool_parsers, d):
        p.add_argument(
            "--project", type=Path, help="the project dir; defaults to CLAUDE_PROJECT_DIR"
        )
    args = ap.parse_args(argv)
    project = project_dir(args.project)
    if project is None:
        print("next_projection: CLAUDE_PROJECT_DIR is unset; pass --project", file=sys.stderr)
        return 2
    if args.cmd == "status":
        for line in status_lines(project):
            print(line)
        return 0
    if args.cmd == "query":
        if args.tool == "neighbors" and (why := hops_refusal(args.hops)):
            print(f"next_projection: {why}", file=sys.stderr)
            return 2
        if args.tool == "landed":
            try:
                lines = query_landed(project, args.date)
            except (OSError, sqlite3.Error, ProjectionError) as exc:
                log.error("next_projection: query landed failed: %s", exc, exc_info=True)
                return 1
            print("\n".join(lines))
            return 0
        try:
            if args.tool == "entity":
                answer = query_entity(project, args.id)
            elif args.tool == "find":
                answer = query_find(project, args.text, args.node_type)
            elif args.tool == "neighbors":
                answer = query_neighbors(project, args.id, args.edge_type, args.hops)
            elif args.tool == "path":
                answer = query_path(project, args.start, args.end)
            elif args.tool == "timeline":
                answer = query_timeline(project, args.id, args.since)
            elif args.tool == "intent":
                answer = query_intent(project, args.intent, args.state)
            else:
                answer = query_coverage(project, args.slug)
        except sqlite3.Error as exc:
            log.error("next_projection: cannot read %s: %s", project / STORE, exc, exc_info=True)
            return 1
        print("\n".join(answer_lines(answer)) if args.as_text else json.dumps(answer))
        return 0
    try:
        store = build(
            args.target,
            project,
            tuple(args.plans or PLAN_GLOBS),
            closeout=tuple(args.closeout_jsonl),
            factory=tuple(args.factory_ledger),
        )
    except (OSError, sqlite3.Error, subprocess.CalledProcessError, ProjectionError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else exc
        log.error("next_projection: build failed, previous store kept: %s", detail, exc_info=True)
        return 1
    print(f"BUILT {store}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
