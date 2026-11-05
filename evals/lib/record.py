# Description: One evidence record for the next-auto eval: converts a run summary into
# Description: d6-record/1 lines and appends the shared ledger (evals/ledger.jsonl).

from __future__ import annotations

import json
import socket
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

SCHEMA = "d6-record/1"
SUMMARY_SCHEMA = "d6-ledger-summary/1"
DEFAULT_INSTALLED = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
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
