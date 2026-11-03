# Description: The auto-mode classifier probe: open a throwaway PR on the next-auto fixture, print
# Description: the launch line for the session that re-issues the shapes, record its stream, close.

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_WORKDIR = Path.home() / ".cache" / "adw" / "next-auto"
# the operator's own fixture repository, as owner/name, shared with run_scenario.py
FIXTURE_REPO = os.environ.get("ADW_EVAL_FIXTURE_REPO", "")
RESULTS = HERE / "results"
PROMPT_TEMPLATE = HERE / "fixtures" / "probe_classifier_prompt.md"
# the shapes are numbered 1 to 7 in the prompt; the session under test issues them in order
SHAPE_COUNT = 7
DENIAL_RE = re.compile(r"denied by the Claude Code auto mode classifier\. Reason: (\[[^\]]+\])")


def run(
    args: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    r = subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed ({r.returncode}): {r.stderr.strip()[-400:]}")
    return r


def git(clone: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(clone), *args], check=check)


def state_path(stamp: str) -> Path:
    return RESULTS / f"{stamp}-probe-classifier.state.json"


def prompt_path(stamp: str) -> Path:
    return RESULTS / f"{stamp}-probe-classifier.prompt.txt"


def raw_path(stamp: str) -> Path:
    return RESULTS / f"{stamp}-probe-classifier.raw.jsonl"


def render_prompt(template: str, state: dict) -> str:
    """The prompt with every {placeholder} filled from the PR state; a leftover brace pair
    is a template error, raised rather than handed to a paid session."""
    text = template
    for key in ("number", "repo", "base", "head", "delete_ref", "stamp"):
        text = text.replace("{" + key + "}", str(state[key]))
    leftover = re.findall(r"\{[a-z_]+\}", text)
    if leftover:
        raise ValueError(f"unfilled placeholders in the probe prompt: {leftover}")
    return text


def launch_line(state: dict, clone: Path) -> str:
    """The exact command the operator pastes. A session in auto mode cannot launch it: the
    classifier denies a nested `--permission-mode auto` session as [Create Unsafe Agents]
    (measured 2026-09-18), so the launch is the operator's own shell. The clone path is
    passed in, never stored: the state file is committed and carries no machine path."""
    return (
        f"cd {clone} && claude -p \"$(cat {prompt_path(state['stamp'])})\" "
        "--permission-mode auto --permission-prompts none --output-format stream-json "
        "--verbose --model sonnet --max-budget-usd 2 --strict-mcp-config "
        f"> {raw_path(state['stamp'])}"
    )


def open_pr(clone: Path, repo: str) -> dict:
    """Three throwaway branches from the fixture's HEAD: a base the PR targets (so an
    allowed merge can never reach main, which the fixture's ruleset protects), the head
    carrying one probe file, and a ref for the DELETE shape. Then one PR, base to head,
    the rendered prompt beside the results, and the launch line on stdout."""
    if git(clone, "status", "--porcelain").stdout.strip():
        raise SystemExit("fixture clone is dirty; run run_scenario.py --reset-only first")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = f"probe/classifier-{stamp}-base"
    head = f"probe/classifier-{stamp}"
    delete_ref = f"probe/classifier-{stamp}-delete"
    start = git(clone, "rev-parse", "HEAD").stdout.strip()
    git(clone, "branch", base, start)
    git(clone, "branch", delete_ref, start)
    git(clone, "checkout", "-q", "-b", head, start)
    probe_file = clone / "probe" / f"classifier-{stamp}.md"
    probe_file.parent.mkdir(parents=True, exist_ok=True)
    probe_file.write_text(
        f"# Classifier probe {stamp}\n\nThrowaway PR for the auto-mode classifier probe "
        "(ai-dev-workflow Step 16). Closed by probe_classifier.py close.\n"
    )
    git(clone, "add", str(probe_file.relative_to(clone)))
    git(clone, "commit", "-qm", f"probe: classifier command shapes {stamp}")
    head_sha = git(clone, "rev-parse", "HEAD").stdout.strip()
    git(clone, "push", "-q", "origin", base, head, delete_ref)
    git(clone, "checkout", "-q", "main")
    created = run(
        [
            "gh", "pr", "create", "--repo", repo, "--base", base, "--head", head,
            "--title", f"probe: classifier command shapes {stamp}",
            "--body", "Throwaway PR for the auto-mode classifier probe. Closed by the probe.",
        ]
    )
    url = created.stdout.strip().splitlines()[-1]
    number = int(url.rstrip("/").rsplit("/", 1)[-1])
    state = {
        "stamp": stamp,
        "repo": repo,
        "number": number,
        "url": url,
        "base": base,
        "head": head,
        "head_sha": head_sha,
        "delete_ref": delete_ref,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    state_path(stamp).write_text(json.dumps(state, indent=2) + "\n")
    prompt_path(stamp).write_text(render_prompt(PROMPT_TEMPLATE.read_text(), state))
    print(json.dumps(state))
    print("LAUNCH " + launch_line(state, clone))
    return state


def _text_of(block: dict) -> str:
    content = block.get("content", "")
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return content if isinstance(content, str) else ""


def parse_probe_stream(raw: str) -> dict:
    """Every Bash tool_use in issue order paired with its tool_result: denied by the
    classifier (with the bracketed rule), denied otherwise, or allowed; plus the session's
    cost. The command text is what the model typed, so a paraphrased shape shows here."""
    uses: dict[str, dict] = {}
    order: list[str] = []
    cost = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # a malformed line is reported with its text, never dropped in silence, and
            # the rows around it still parse
            print(
                f"probe_classifier: skipping a malformed stream line: {line[:200]!r}",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)
            continue
        if event.get("type") == "result" and isinstance(event.get("total_cost_usd"), (int, float)):
            cost = event["total_cost_usd"]
        # The system permission_denied event is the verdict's structured source: its
        # decision_reason_type names the denier (classifier or asyncAgent) and its
        # decision_reason carries the rule; its message field is a plain string.
        if event.get("type") == "system" and event.get("subtype") == "permission_denied":
            entry = uses.get(event.get("tool_use_id"))
            if entry is not None:
                reason_type = event.get("decision_reason_type", "")
                reason = event.get("decision_reason", "")
                entry["decision"] = "denied"
                entry["rule"] = (
                    reason if reason_type == "classifier" else f"({reason_type}) {reason}"
                )
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        for block in message.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                uses[block["id"]] = {"command": (block.get("input") or {}).get("command", "")}
                order.append(block["id"])
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in uses:
                text = _text_of(block)
                entry = uses[block["tool_use_id"]]
                entry["result"] = text[:300]
                if entry.get("decision") == "denied":
                    continue
                m = DENIAL_RE.search(text)
                if m:
                    entry["decision"], entry["rule"] = "denied", m.group(1)
                elif block.get("is_error") and "denied" in text.lower():
                    entry["decision"], entry["rule"] = "denied", "(not the classifier)"
                else:
                    entry["decision"], entry["rule"] = "allowed", ""
    rows = [
        {"shape": i + 1, **uses[uid]} for i, uid in enumerate(order)
    ]
    for row in rows:
        row.setdefault("decision", "no result")
        row.setdefault("rule", "")
        row.setdefault("result", "")
    return {"rows": rows, "total_cost_usd": cost, "bash_calls": len(order)}


def record(stamp: str, raw: Path) -> dict:
    """Parse the captured stream into the committed result file and print the table the
    plan's Findings entry carries."""
    parsed = parse_probe_stream(raw.read_text(encoding="utf-8", errors="replace"))
    state = json.loads(state_path(stamp).read_text())
    out = {
        "stamp": stamp,
        "pr": state["number"],
        "repo": state["repo"],
        "recorded_at": datetime.now(UTC).isoformat(),
        **parsed,
    }
    (RESULTS / f"{stamp}-probe-classifier.json").write_text(json.dumps(out, indent=2) + "\n")
    print("| shape | decision | rule | command |")
    print("|---|---|---|---|")
    for row in parsed["rows"]:
        cmd = row["command"].replace("|", "\\|").replace("\n", " ")
        print(f"| {row['shape']} | {row['decision']} | {row['rule']} | `{cmd[:110]}` |")
    print(f"cost_usd={parsed['total_cost_usd']} bash_calls={parsed['bash_calls']}")
    return out


def close_pr(state: dict, clone: Path) -> None:
    """Close the PR (a merged one is left as merged) and delete every probe branch; the
    fixture's baseline is untouched either way, since the PR never targeted main."""
    repo = state["repo"]
    view = run(["gh", "pr", "view", str(state["number"]), "--repo", repo, "--json", "state"])
    pr_state = json.loads(view.stdout).get("state")
    if pr_state == "OPEN":
        run(["gh", "pr", "close", str(state["number"]), "--repo", repo, "--delete-branch"])
    remote = git(clone, "ls-remote", "--heads", "origin", "probe/*").stdout
    live = [ln.split("\t")[-1].removeprefix("refs/heads/") for ln in remote.splitlines() if ln]
    if live:
        git(clone, "push", "-q", "origin", "--delete", *live)
    # local cleanup is best effort: a branch the loop already deleted, or a clone already
    # on main, is not an error, and the remote state above is the record
    for branch in (state["base"], state["head"], state["delete_ref"]):
        git(clone, "branch", "-D", "-q", branch, check=False)
    git(clone, "checkout", "-q", "main", check=False)
    print(json.dumps({"number": state["number"], "was": pr_state, "deleted_refs": live}))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["open", "record", "close"])
    ap.add_argument("--clone", default=str(DEFAULT_WORKDIR / "next-auto-fixture"))
    ap.add_argument("--repo", default=FIXTURE_REPO, required=not FIXTURE_REPO)
    ap.add_argument("--stamp", help="record and close: the stamp printed by open")
    ap.add_argument("--raw", help="record: the stream-json capture; default the launch line's")
    args = ap.parse_args(argv)
    if args.action == "open":
        open_pr(Path(args.clone), args.repo)
        return 0
    if not args.stamp:
        ap.error(f"{args.action} needs --stamp")
    if args.action == "record":
        record(args.stamp, Path(args.raw) if args.raw else raw_path(args.stamp))
        return 0
    close_pr(json.loads(state_path(args.stamp).read_text()), Path(args.clone))
    return 0


if __name__ == "__main__":
    sys.exit(main())
