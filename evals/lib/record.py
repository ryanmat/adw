#!/usr/bin/env python3
# Description: One evidence record for both eval substrates: converts a plugin-eval
# Description: aggregate-result.json or a next-auto summary into d6-record/1 lines and the ledger.

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

SCHEMA = "d6-record/1"
SUMMARY_SCHEMA = "d6-ledger-summary/1"
SUPPORTED_AGGREGATE_SCHEMA = 1
DEFAULT_INSTALLED = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
DEFAULT_LEDGER = Path(__file__).resolve().parents[1] / "ledger.jsonl"
REPO_ROOT = Path(__file__).resolve().parents[2]


class RecordError(RuntimeError):
    """An input this adapter does not understand. Never guessed around."""


def _money(value) -> str | None:
    """Currency travels as Decimal and is serialised as a string, never a float."""
    if value is None:
        return None
    return str(Decimal(str(value)))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def result_path(path: Path) -> str:
    """The ledger is shared across machines, so a result inside the repo is recorded
    relative to the repo root (posix); a result elsewhere keeps its absolute path."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def resolve_result_path(recorded: str) -> Path:
    """The inverse of result_path: a relative record resolves against the repo root."""
    p = Path(recorded)
    return p if p.is_absolute() else REPO_ROOT / p


def run_cost(path: Path) -> Decimal | None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return None if data.get("costUsd") is None else Decimal(str(data["costUsd"]))


# ---- plugin identity ---------------------------------------------------------


DEFAULT_SKILLS = Path.home() / ".claude" / "skills"


def _git_head(path: Path) -> str | None:
    head = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return head.stdout.strip() if head.returncode == 0 else None


def installed_entry(target: str, installed_plugins_path: Path | None = None) -> dict | None:
    """The install record's entry for `name@marketplace`, or None."""
    installed = Path(installed_plugins_path or DEFAULT_INSTALLED)
    if not installed.exists():
        return None
    plugins = json.loads(installed.read_text(encoding="utf-8")).get("plugins", {})
    entry = plugins.get(target)
    if isinstance(entry, list):
        entry = entry[0] if entry else None
    return entry if isinstance(entry, dict) else None


def resolve_plugin(
    target: str,
    installed_plugins_path: Path | None = None,
    *,
    skills_dir: Path | None = None,
) -> dict:
    """`name@marketplace` resolves through the install record; `skill:<name>` through the
    git HEAD of the skills directory it lives in; a path through its own git HEAD; anything
    else keeps the name and admits it knows nothing more."""
    if target.startswith("skill:"):
        name = target.split(":", 1)[1]
        skill_dir = Path(skills_dir or DEFAULT_SKILLS) / name
        sha = _git_head(skill_dir) if skill_dir.exists() else None
        return {"name": name, "version": None, "gitCommitSha": sha}
    p = Path(target).expanduser()
    if p.exists():
        return {"name": p.resolve().name, "version": None, "gitCommitSha": _git_head(p)}
    name = target.split("@", 1)[0]
    entry = installed_entry(target, installed_plugins_path)
    if entry is not None:
        return {
            "name": name,
            "version": entry.get("version"),
            "gitCommitSha": entry.get("gitCommitSha"),
        }
    return {"name": name, "version": None, "gitCommitSha": None}


# ---- plugin eval -------------------------------------------------------------


def _case_tags(tags, case_name: str) -> list[str]:
    if isinstance(tags, dict):
        return list(tags.get(case_name, []))
    return list(tags or [])


def _run_passed(run: dict) -> bool:
    if run.get("error") or run.get("aborted"):
        return False
    graders = [g for g in run.get("graders", []) if g.get("scored", True)]
    return bool(graders) and all(g.get("passed") for g in graders)


def _run_score(run: dict) -> Decimal:
    graders = [g for g in run.get("graders", []) if g.get("scored", True)]
    if not graders or run.get("error") or run.get("aborted"):
        return Decimal(0)
    weight = sum(Decimal(str(g.get("weight", 1))) for g in graders)
    won = sum(Decimal(str(g.get("weight", 1))) for g in graders if g.get("passed"))
    return won / weight if weight else Decimal(0)


def _case_cost(case: dict, per_run: list[dict]) -> str | None:
    """The CLI reports costUsd per run; the case cost is the sum over every arm the pass ran
    (a with-without ablation bills both), unless the aggregate carries a case-level figure."""
    explicit = case.get("costUsd", case.get("aggregates", {}).get("costUsd"))
    if explicit is not None:
        return _money(explicit)
    arms = case.get("arms") or {}
    if arms:
        costs = [
            Decimal(str(run["costUsd"]))
            for arm in arms.values()
            for run in arm
            if run.get("costUsd") is not None
        ]
    else:
        costs = [Decimal(r["cost_usd"]) for r in per_run if r.get("cost_usd") is not None]
    return str(sum(costs)) if costs else None


def _without_arm(case: dict) -> tuple[str | None, str | None]:
    """(score_without, ablation_delta) for a with-without pass, else (None, None); the
    delta is the with score minus the without score, the skill's measured contribution."""
    if "without" not in (case.get("arms") or {}):
        return None, None
    agg = case.get("aggregates", {})
    score, without = _money(agg.get("score")), _money(agg.get("scoreWithout"))
    if score is None or without is None:
        return without, None
    return without, str(Decimal(score) - Decimal(without))


def from_plugin_eval(
    path: Path,
    target: str,
    tags=None,
    *,
    model: str | None = None,
    judge_model: str | None = None,
    ablation: str = "none",
    suite: str | None = None,
    machine: str | None = None,
    installed_plugins_path: Path | None = None,
    skills_dir: Path | None = None,
    plugin: dict | None = None,
) -> list[dict]:
    """One record per case from a plugin-eval aggregate. `plugin` is the target identity
    resolved when the pass was staged; a caller that has it passes it, because the target
    can change on disk while a pass runs and the record must name what actually ran."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("schemaVersion")
    if version != SUPPORTED_AGGREGATE_SCHEMA:
        raise RecordError(f"{path.name}: schemaVersion {version} is not supported (want 1)")
    if plugin is None:
        plugin = resolve_plugin(target, installed_plugins_path, skills_dir=skills_dir)
    partial = bool(data.get("partial"))
    ran_at = _now()
    records = []
    for case in data.get("cases", []):
        name = case["name"]
        runs = case.get("arms", {}).get("with", [])
        per_run = []
        for run in runs:
            per_run.append(
                {
                    "pass": _run_passed(run),
                    "score": str(_run_score(run)),
                    "cost_usd": _money(run.get("costUsd")),
                    "judge_cost_usd": _money(run.get("judgeCostUsd")),
                    "turns": run.get("turns"),
                    "duration_s": run.get("durationSeconds"),
                    "error": run.get("error"),
                    "aborted": run.get("aborted"),
                    "skipped_paid_graders": bool(run.get("skippedPaidGraders")),
                    "graders": [
                        {
                            "name": g.get("name"),
                            "type": g.get("type"),
                            "passed": g.get("passed"),
                            "scored": g.get("scored", True),
                            "weight": g.get("weight", 1),
                            "detail": g.get("explanation"),
                        }
                        for g in run.get("graders", [])
                    ],
                }
            )
        case_tags = _case_tags(tags, name)
        comparable = not any(r["skipped_paid_graders"] for r in per_run)
        all_passed = bool(per_run) and all(r["pass"] for r in per_run)
        score_without, ablation_delta = _without_arm(case)
        records.append(
            {
                "schema": SCHEMA,
                "ran_at": ran_at,
                "machine": machine or socket.gethostname(),
                "substrate": "plugin-eval",
                "suite": suite or (case_tags[0] if case_tags else name.split("--")[0]),
                "case": name,
                "tags": case_tags,
                "runs": len(per_run),
                "per_run": per_run,
                "score": _money(case.get("aggregates", {}).get("score")),
                "pass_k": all_passed and comparable and "consistency" in case_tags,
                "cost_usd": _case_cost(case, per_run),
                "score_without": score_without,
                "ablation_delta": ablation_delta,
                "claude_version": data.get("claudeVersion"),
                "plugin": plugin,
                "model": model,
                "judge_model": judge_model,
                "ablation": ablation,
                "result_path": result_path(path),
                "partial": partial,
                "partial_reason": data.get("partialReason"),
                "comparable": comparable,
            }
        )
    return records


# ---- next-auto ---------------------------------------------------------------


def from_next_auto(path: Path, *, machine: str | None = None) -> list[dict]:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    ran_at = _now()
    records = []
    for result in data.get("results", []):
        session = result.get("session") or {}
        plugin_info = session.get("plugin") or {}
        trials = result.get("trials") or [result]
        per_run = [
            {
                "pass": bool(t.get("pass")),
                "score": "1.0" if t.get("pass") else "0.0",
                "cost_usd": _money(t.get("total_cost_usd")),
                "graders": [
                    {"name": g.get("grader"), "passed": g.get("pass"), "detail": g.get("detail")}
                    for g in t.get("graders", [])
                ],
            }
            for t in trials
        ]
        records.append(
            {
                "schema": SCHEMA,
                "ran_at": ran_at,
                "machine": machine or socket.gethostname(),
                "substrate": "next-auto",
                "suite": "next-auto",
                "case": result.get("scenario"),
                "tags": ["adw", "regression"] + (["consistency"] if result.get("pass_k") else []),
                "runs": len(per_run),
                "per_run": per_run,
                "score": "1.0" if result.get("pass") else "0.0",
                "pass_k": bool(result.get("pass_k")),
                "cost_usd": _money(result.get("total_cost_usd")),
                "claude_version": session.get("claude_code_version"),
                "plugin": {
                    "name": plugin_info.get("name") or "adw",
                    "version": plugin_info.get("version"),
                    "gitCommitSha": plugin_info.get("gitCommitSha"),
                },
                "model": result.get("model") or data.get("model"),
                "judge_model": None,
                "ablation": "none",
                "result_path": result_path(path),
                "partial": bool(result.get("timed_out") or result.get("over_cost_cap")),
                "partial_reason": (
                    "timed_out"
                    if result.get("timed_out")
                    else "over_cost_cap"
                    if result.get("over_cost_cap")
                    else None
                ),
                "comparable": True,
            }
        )
    return records


# ---- ledger and rendering ----------------------------------------------------


def append(ledger_path: Path, records: list[dict]) -> None:
    """Append-only: one JSON line per record, then one summary line for the batch."""
    ledger_path = Path(ledger_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    costs = [Decimal(r["cost_usd"]) for r in records if r.get("cost_usd") is not None]
    summary = {
        "schema": SUMMARY_SCHEMA,
        "ran_at": _now(),
        "records": len(records),
        "passed": sum(1 for r in records if record_passed(r)),
        "cost_usd": str(sum(costs)) if costs else None,
        "substrate": sorted({r["substrate"] for r in records}),
        "result_paths": sorted({r["result_path"] for r in records}),
    }
    if not costs:
        # a plugin-eval aggregate carries one cost for the run, not one per case; a batch
        # that spans several aggregates sums them
        totals = [
            run_cost(resolve_result_path(p))
            for p in summary["result_paths"]
            if resolve_result_path(p).exists()
        ]
        totals = [t for t in totals if t is not None]
        if totals:
            summary["cost_usd"] = str(sum(totals))
    with ledger_path.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
        fh.write(json.dumps(summary, sort_keys=True) + "\n")


CORRECTION_SCHEMA = "d6-ledger-correction/1"


def append_correction(
    ledger_path: Path, *, supersedes: str, superseded_by: str, reason: str
) -> None:
    """Records are never edited. A correction is one more line naming the stale batch by the
    `ran_at` prefix its lines share and the batch that replaces it; readers drop the former."""
    line = {
        "schema": CORRECTION_SCHEMA,
        "ran_at": _now(),
        "supersedes": supersedes,
        "superseded_by": superseded_by,
        "reason": reason,
    }
    with Path(ledger_path).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, sort_keys=True) + "\n")


def effective_records(ledger_path: Path) -> list[dict]:
    """The d6-record/1 lines that still stand: every record whose `ran_at` starts with a
    superseded batch prefix is dropped, summaries and corrections are not records."""
    lines = [
        json.loads(ln)
        for ln in Path(ledger_path).read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    superseded = [d["supersedes"] for d in lines if d.get("schema") == CORRECTION_SCHEMA]
    return [
        d
        for d in lines
        if d.get("schema") == SCHEMA
        and not any(d.get("ran_at", "").startswith(prefix) for prefix in superseded)
    ]


def record_passed(r: dict) -> bool:
    """A case passed only if it ran, every run passed, nothing was skipped, and the run
    was not cut short."""
    return (
        bool(r["per_run"])
        and all(x["pass"] for x in r["per_run"])
        and r.get("comparable", True)
        and not r.get("partial", False)
    )


def render_markdown(records: list[dict]) -> str:
    lines = [
        "| case | substrate | runs | score | pass_k | comparable | claude | plugin |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        p = r["plugin"]
        sha = (p.get("gitCommitSha") or "")[:8]
        plugin = " ".join(x for x in (p.get("name"), p.get("version"), sha) if x)
        lines.append(
            f"| {r['case']} | {r['substrate']} | {r['runs']} | {r['score']} | "
            f"{'yes' if r['pass_k'] else 'no'} | {'yes' if r['comparable'] else 'no'} | "
            f"{r['claude_version'] or ''} | {plugin} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="convert eval results into d6-record lines")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--plugin-eval", help="an aggregate-result.json from claude plugin eval")
    src.add_argument("--next-auto", help="a *-summary.json from evals/next-auto")
    src.add_argument(
        "--correct",
        nargs=2,
        metavar=("SUPERSEDES", "SUPERSEDED_BY"),
        help="append a correction line: the stale batch's ran_at prefix, then its replacement",
    )
    src.add_argument(
        "--effective", action="store_true", help="print the count and cost of records that stand"
    )
    ap.add_argument("--reason", default="", help="why the batch is superseded (with --correct)")
    ap.add_argument("--target", help="plugin name@marketplace or path (plugin-eval only)")
    ap.add_argument("--tags", default="", help="comma-separated tags applied to every case")
    ap.add_argument("--model")
    ap.add_argument("--judge-model")
    ap.add_argument("--ablation", default="none")
    ap.add_argument("--ledger", help="append records here (default evals/ledger.jsonl)")
    ap.add_argument("--markdown", action="store_true", help="print the records as a table")
    ap.add_argument("--no-ledger", action="store_true", help="convert only; append nothing")
    args = ap.parse_args(argv)
    ledger_path = Path(args.ledger) if args.ledger else DEFAULT_LEDGER
    if args.correct:
        if not args.reason:
            ap.error("--reason is required with --correct")
        append_correction(
            ledger_path,
            supersedes=args.correct[0],
            superseded_by=args.correct[1],
            reason=args.reason,
        )
        return 0
    if args.effective:
        recs = effective_records(ledger_path)
        total = sum(Decimal(r["cost_usd"]) for r in recs if r.get("cost_usd") is not None)
        print(f"{len(recs)} records stand; cost USD {total}")
        return 0
    try:
        if args.plugin_eval:
            if not args.target:
                ap.error("--target is required with --plugin-eval")
            records = from_plugin_eval(
                Path(args.plugin_eval),
                args.target,
                [t for t in args.tags.split(",") if t],
                model=args.model,
                judge_model=args.judge_model,
                ablation=args.ablation,
            )
        else:
            records = from_next_auto(Path(args.next_auto))
    except (RecordError, OSError, json.JSONDecodeError) as exc:
        print(f"record: {exc}", file=sys.stderr)
        return 2
    if not args.no_ledger:
        append(ledger_path, records)
    if args.markdown:
        sys.stdout.write(render_markdown(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
