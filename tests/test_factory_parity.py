# Description: Pins by name what a downstream consumer reads from this repo.
# Description: Stream strings, constants, runner lines, a scenario key, the label, branch, marker.

import json
import re
from pathlib import Path

import next_auto

ROOT = Path(__file__).resolve().parents[1]
AUTO_SRC = (ROOT / "scripts" / "next_auto.py").read_text(encoding="utf-8")
RUNNER_SRC = (ROOT / "evals" / "next-auto" / "run_scenario.py").read_text(encoding="utf-8")
HAPPY_PATH = ROOT / "evals" / "next-auto" / "scenarios" / "happy-path.json"

FACTORY = "factory"


def _printed(literal: str) -> bool:
    # A stream string counts as pinned only when it sits inside a print() call.
    return re.search(r"print\([^)]*" + re.escape(literal), AUTO_SRC) is not None


def test_preflight_refused_stream_string():
    assert _printed("PREFLIGHT REFUSED"), (
        f"{FACTORY} reads PREFLIGHT REFUSED from the preflight stream"
    )


def test_preflight_failed_gh_stream_string():
    assert _printed("PREFLIGHT FAILED gh"), (
        f"{FACTORY} reads PREFLIGHT FAILED gh from the preflight stream"
    )


def test_preflight_ok_stream_string():
    assert _printed("PREFLIGHT OK"), f"{FACTORY} reads PREFLIGHT OK from the preflight stream"


def test_start_failed_stream_string():
    assert _printed("START FAILED"), f"{FACTORY} reads START FAILED from the start stream"


def test_run_started_stream_string():
    assert _printed("RUN STARTED {state['run_id']}"), (
        f"{FACTORY} reads RUN STARTED <id> from the start stream"
    )


def test_wrap_up_failed_stream_string():
    assert _printed("WRAP-UP FAILED"), f"{FACTORY} reads WRAP-UP FAILED from the wrap-up stream"


def test_run_stopped_two_line_shape():
    assert "pr = f\" PR #{result['pr_number']}\"" in AUTO_SRC, (
        f"{FACTORY} parses the PR number from ' PR #<n>' on the RUN STOPPED line"
    )
    assert _printed("RUN STOPPED {result['reason']}{pr}"), (
        f"{FACTORY} reads RUN STOPPED <reason> PR #n as the wrap-up's first line"
    )


def test_run_stopped_no_pr_line_shape():
    """The hand-back's line (carries spec R11, Step 19 of the 3.5.9 engine plan): the public
    README names it among the lines a scheduler reads, so its text and its print are pinned."""
    assert next_auto.NO_PR_SUFFIX == "NO PR: no step committed"
    assert _printed("RUN STOPPED {result['reason']} {NO_PR_SUFFIX}"), (
        f"{FACTORY} reads RUN STOPPED <reason> NO PR: no step committed as the hand-back's line"
    )


def test_never_auto_and_secret_patterns_are_module_attributes():
    assert isinstance(next_auto.NEVER_AUTO, frozenset), (
        f"{FACTORY} imports NEVER_AUTO as the never-run set"
    )
    assert isinstance(next_auto.SECRET_PATTERNS, tuple), (
        f"{FACTORY} imports SECRET_PATTERNS for its own scan"
    )
    assert all(isinstance(p[1], re.Pattern) for p in next_auto.SECRET_PATTERNS), (
        f"{FACTORY} expects (family, compiled pattern) pairs in SECRET_PATTERNS"
    )


def test_reset_fixture_signature():
    assert re.search(r"^def reset_fixture\(clone: Path\) -> str:", RUNNER_SRC, re.M), (
        f"{FACTORY} calls reset_fixture(clone) and reads the baseline sha it returns"
    )


def test_model_line_in_runner():
    assert re.search(r'^MODEL = "', RUNNER_SRC, re.M), (
        f"{FACTORY} rewrites the runner's MODEL line by name"
    )


def test_happy_path_carries_allowed_tools_key():
    data = json.loads(HAPPY_PATH.read_text(encoding="utf-8"))
    assert "allowed_tools" in data, f"{FACTORY} reads allowed_tools from happy-path.json"
    assert isinstance(data["allowed_tools"], list) and data["allowed_tools"]


def test_next_auto_label_on_list_and_create():
    assert '"--label", "next-auto", "--state"' in AUTO_SRC, (
        f"{FACTORY} filters pr list by the label"
    )
    assert '"--label", "next-auto", "--body-file"' in AUTO_SRC, (
        f"{FACTORY} expects the label on the pr create argv"
    )


def test_branch_prefix():
    # the run branch keeps the next-auto/ prefix the runner lists by (next-auto/*), with the
    # plan's Issue id, lowercased, before the slug when the plan carries one
    line = "f\"next-auto/{issue.lower() + '-' if issue else ''}{_slug(plan_text)}-{run_id}\""
    assert line in AUTO_SRC, f"{FACTORY} matches run branches as next-auto/[<issue>-]<slug>-<id>"


def test_state_json_marker_name():
    assert 'glob("*/state.json")' in AUTO_SRC, (
        f"{FACTORY} looks for state.json to detect a live run"
    )
    assert 'run_dir / "state.json"' in AUTO_SRC, f"{FACTORY} expects save_state to write state.json"
