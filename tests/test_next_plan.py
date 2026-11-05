# Description: Tests for next_plan.py, the plan-grammar validator, migrator, and editor.
# Description: Real files in tmp dirs, no mocks; fixtures vendored from the legacy plan styles.

import json
import re
import subprocess
import sys
from pathlib import Path

import next_plan
import pytest

SCRIPT = Path(next_plan.__file__)
FIXTURES = Path(__file__).parent / "fixtures" / "legacy"
LEGACY_FILES = ["dreams.md", "langsmith.md", "starter.md", "cyber.md"]
# the two shapes the review found migrate collapsing to one step (2026-09-11)
NEW_LEGACY_FILES = ["project-a-marker-style.md", "project-b-list-style.md"]

CANON = """\
# Widget Implementation Plan
Spec: spec.md. Owner: the owner. Written: 2026-09-11.

## Conventions binding every step
- Test: uv run pytest
- Lint: uv run ruff check .
- Run: uv run python -m widget
- Eval: uv run python -m widget.eval
- Eval-codes: apparatus
- Holdout: evals/

## Chunk A: core

### Step 1: parser
Status: DONE (42 tests green, abc1234)
Eval: no
Mutation: required
Write tests for the parser first, then implement it. Done when uv run pytest is green.

### Step 2: engine
Status: PENDING
Eval: no
Mutation: n/a (pure refactor, no new behavior)
Build the engine on top of the parser.

### Step 3: judge
Status: PENDING
Eval: yes
Mutation: required
Add the LLM judge. Meet the eval threshold.

## Decisions
(none)

## Findings
(none)
"""


def run_cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def write(tmp_path, text, name="prompt_plan.md", newline="\n"):
    p = tmp_path / name
    p.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return p


def only_placeholder_reason(text):
    """After migrate, the unfilled PACKAGE placeholder is the one thing validate refuses."""
    reasons = next_plan.validate_text(text)
    return [msg for _ln, msg in reasons] == [
        "Conventions Run still carries the PACKAGE placeholder; fill it in"
    ]


# ---- parsing ----------------------------------------------------------------


def test_parse_canonical():
    plan = next_plan.parse_plan(CANON)
    assert plan.title.startswith("# Widget")
    assert [s.number for s in plan.steps] == [1, 2, 3]
    assert plan.steps[0].state == "DONE"
    assert plan.steps[0].note == "42 tests green, abc1234"
    assert plan.steps[0].eval is False
    assert plan.steps[0].mutation.startswith("required")
    assert plan.steps[1].state == "PENDING"
    assert plan.steps[1].mutation.startswith("n/a")
    assert plan.steps[2].eval is True
    assert plan.conventions["Test"] == "uv run pytest"
    assert plan.conventions["Eval-codes"] == "apparatus"
    assert plan.conventions["Holdout"] == "evals/"


def test_parse_in_progress_and_blocked():
    text = CANON.replace(
        "Status: PENDING\nEval: no\nMutation: n/a",
        "Status: IN PROGRESS\nEval: no\nMutation: n/a",
        1,
    ).replace(
        "Status: PENDING\nEval: yes",
        "Status: BLOCKED (decision: which backend?)\nEval: yes",
        1,
    )
    plan = next_plan.parse_plan(text)
    assert plan.steps[1].state == "IN PROGRESS"
    assert plan.steps[2].state == "BLOCKED"
    assert plan.steps[2].note == "decision: which backend?"


# ---- validation -------------------------------------------------------------


def test_validate_accepts_canonical():
    assert next_plan.validate_text(CANON) == []


def test_cli_validate_exit0(tmp_path):
    p = write(tmp_path, CANON)
    r = run_cli("validate", str(p))
    assert r.returncode == 0, r.stderr


def test_validate_missing_status():
    text = CANON.replace("Status: PENDING\nEval: no\nMutation: n/a", "Eval: no\nMutation: n/a", 1)
    reasons = next_plan.validate_text(text)
    assert any("Status" in msg for _, msg in reasons)


def test_validate_bad_eval_value():
    text = CANON.replace("Eval: no\nMutation: required", "Eval: maybe\nMutation: required", 1)
    reasons = next_plan.validate_text(text)
    assert any("Eval" in msg for _, msg in reasons)


def test_validate_bad_status_state():
    text = CANON.replace("Status: PENDING", "Status: WIP", 1)
    reasons = next_plan.validate_text(text)
    assert any("Status" in msg and ("WIP" in msg or "state" in msg.lower()) for _, msg in reasons)


def test_validate_non_increasing_numbers():
    text = CANON.replace("### Step 3: judge", "### Step 2: judge", 1)
    reasons = next_plan.validate_text(text)
    assert any("increasing" in msg.lower() or "duplicate" in msg.lower() for _, msg in reasons)


def test_validate_requires_uv_run_for_python():
    text = CANON.replace("- Test: uv run pytest", "- Test: pytest", 1)
    reasons = next_plan.validate_text(text)
    assert any("uv run" in msg for _, msg in reasons)


@pytest.mark.parametrize("key", ["Test", "Lint"])
@pytest.mark.parametrize(
    ("value", "op"),
    [
        ("uv run pytest && uv run pytest tests/e2e", "&&"),
        ("uv run pytest&&uv run pytest tests/e2e", "&&"),
        ("uv run pytest | tail -1", "|"),
        ("uv run pytest || true", "||"),
        ("uv run pytest; uv run pytest tests/e2e", ";"),
    ],
)
def test_validate_refuses_an_unquoted_shell_operator_in_test_and_lint(key, value, op):
    """The engine runs Test and Lint without a shell, so an operator outside quotes reaches
    the program as an argument (Test) or ends the command early (Lint)."""
    line = "- Test: uv run pytest" if key == "Test" else "- Lint: uv run ruff check ."
    text = CANON.replace(line, f"- {key}: {value}", 1)
    conv_line = next(i for i, ln in enumerate(text.splitlines(), 1) if ln.startswith(f"- {key}:"))
    assert next_plan.validate_text(text) == [(conv_line, operator_reason(key, op))]


def operator_reason(key, op):
    return (
        f"Conventions {key} carries {op!r}, which reads as a shell operator; the engine runs "
        f"{key} without a shell, so chain commands inside one quoted argument, as in "
        "sh -c 'set -e; a; b', or uv run sh -c '...' in a Python plan"
    )


def test_validate_reads_a_hash_inside_a_word_as_part_of_the_word():
    # shlex.shlex reads # as a comment unless told not to, which hid the && after it
    text = CANON.replace(
        "- Test: uv run pytest", "- Test: uv run pytest -k fix#12 && uv run pytest tests/e2e", 1
    )
    conv_line = next(i for i, ln in enumerate(text.splitlines(), 1) if ln.startswith("- Test:"))
    assert next_plan.validate_text(text) == [(conv_line, operator_reason("Test", "&&"))]


NODE_CANON = (
    CANON.replace("- Run: uv run python -m widget", "- Run: node scripts/run.mjs")
    .replace("- Eval: uv run python -m widget.eval", "- Eval: node scripts/eval.mjs")
)


def test_validate_accepts_acme_platforms_quoted_chains():
    # acme/platform S7 PR 2's lines, which ran under adw 3.4.5, in a plan with no Python
    text = NODE_CANON.replace(
        "- Test: uv run pytest",
        "- Test: sh -c 'set -e; bun --cwd infra test; node --test \"scripts/test/*.test.mjs\"'",
    ).replace(
        "- Lint: uv run ruff check .",
        "- Lint: sh -c 'set -e; bun run --cwd infra build; npm --prefix apps/portal run lint'",
    )
    assert next_plan.validate_text(text) == []


def test_validate_accepts_the_projects_quoted_chains_in_a_python_plan():
    # one project's Test line, and its Lint as rewritten for 3.4.6, behind uv run
    text = CANON.replace(
        "- Test: uv run pytest",
        "- Test: uv run --no-project bash -c 'uv run --directory tools/router pytest -q"
        " && uv run --directory mcp-servers/tracker pytest -q'",
    ).replace(
        "- Lint: uv run ruff check .",
        "- Lint: uv run sh -c 'set -e; uv run ruff check src; grep -E \"TODO|FIXME\" -rL src'",
    )
    assert next_plan.validate_text(text) == []


def test_validate_eval_yes_requires_codes_and_holdout():
    text = (
        CANON.replace("- Eval: uv run python -m widget.eval\n", "")
        .replace("- Eval-codes: apparatus\n", "")
        .replace("- Holdout: evals/\n", "")
    )
    reasons = next_plan.validate_text(text)
    joined = " ".join(msg for _, msg in reasons)
    assert "Eval" in joined and "Holdout" in joined


def test_validate_bad_eval_codes():
    text = CANON.replace("- Eval-codes: apparatus", "- Eval-codes: bananas", 1)
    reasons = next_plan.validate_text(text)
    assert any("Eval-codes" in msg for _, msg in reasons)


def test_validate_requires_trailing_sections():
    text = CANON.replace("## Decisions\n(none)\n\n", "")
    reasons = next_plan.validate_text(text)
    assert any("Decisions" in msg for _, msg in reasons)


def test_validate_blocked_needs_decision_or_apparatus():
    text = CANON.replace("Status: PENDING\nEval: no", "Status: BLOCKED\nEval: no", 1)
    reasons = next_plan.validate_text(text)
    assert any("BLOCKED" in msg for _, msg in reasons)


def test_validate_reasons_have_line_numbers():
    text = CANON.replace("Status: PENDING", "Status: WIP", 1)
    reasons = next_plan.validate_text(text)
    assert reasons
    for ln, _ in reasons:
        assert isinstance(ln, int) and ln > 0


def test_validate_rejects_unfilled_package_placeholder():
    """migrate emits `- Run: uv run python -m PACKAGE` as a default to be edited. A plan
    still carrying it is not finished, so validate refuses it with the line number and
    /next auto refuses the plan until the placeholder is filled in."""
    text = CANON.replace("- Run: uv run python -m widget", "- Run: uv run python -m PACKAGE")
    reasons = next_plan.validate_text(text)
    assert len(reasons) == 1
    ln, msg = reasons[0]
    assert ln == 7
    assert "Run" in msg and "PACKAGE" in msg and "placeholder" in msg


def test_validate_placeholder_is_whole_word():
    """A package whose name merely contains the token is a real name, not a placeholder."""
    text = CANON.replace("- Run: uv run python -m widget", "- Run: uv run python -m PACKAGER")
    assert next_plan.validate_text(text) == []


def test_cli_validate_exit2_on_placeholder(tmp_path):
    text = CANON.replace("- Run: uv run python -m widget", "- Run: uv run python -m PACKAGE")
    p = write(tmp_path, text)
    r = run_cli("validate", str(p))
    assert r.returncode == 2
    assert f"{p}:7:" in r.stderr and "PACKAGE" in r.stderr


def test_migrate_output_validates_once_package_is_filled(tmp_path):
    """The migrated plan is one edit away from valid: the placeholder is the only reason."""
    src = "# X Implementation Plan\n\nrules.\n\n## Step 1: a [done]\nbody\n"
    p = write(tmp_path, src)
    run_cli("migrate", str(p))
    out = p.read_text()
    reasons = next_plan.validate_text(out)
    assert [msg for _ln, msg in reasons if "placeholder" in msg] and len(reasons) == 1
    assert next_plan.validate_text(out.replace("PACKAGE", "xpkg")) == []


# ---- status / next / brief --------------------------------------------------


def test_cli_status_json(tmp_path):
    p = write(tmp_path, CANON)
    r = run_cli("status", str(p))
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert len(data["steps"]) == 3
    assert data["steps"][0]["state"] == "DONE"
    assert data["counts"]["DONE"] == 1
    assert data["counts"]["PENDING"] == 2


def test_next_returns_first_pending():
    plan = next_plan.parse_plan(CANON)
    nxt = next_plan.next_step(plan)
    assert nxt.number == 2


def test_cli_next_null_when_all_done(tmp_path):
    text = CANON.replace("Status: PENDING", "Status: DONE (x)").replace(
        "Status: PENDING", "Status: DONE (y)"
    )
    p = write(tmp_path, text)
    r = run_cli("next", str(p))
    data = json.loads(r.stdout)
    assert data["number"] is None


def test_brief_contains_conventions_and_step():
    plan = next_plan.parse_plan(CANON)
    brief = next_plan.render_brief(plan, 3)
    assert "Conventions binding every step" in brief
    assert "### Step 3: judge" in brief
    assert "LLM judge" in brief
    assert "### Step 1: parser" not in brief


def test_brief_renders_status_eval_mutation_once():
    """The step body already carries the Status, Eval, and Mutation lines, and the brief
    prepended them again, so every brief showed each line twice."""
    plan = next_plan.parse_plan(CANON)
    brief = next_plan.render_brief(plan, 2)
    lines = brief.splitlines()
    assert lines.count("Status: PENDING") == 1
    assert lines.count("Eval: no") == 1
    assert lines.count("Mutation: n/a (pure refactor, no new behavior)") == 1
    assert sum(1 for ln in lines if ln.startswith(("Status:", "Eval:", "Mutation:"))) == 3
    assert "Build the engine on top of the parser." in brief
    # the three lines sit directly under the step header, then the prose
    i = lines.index("### Step 2: engine")
    assert lines[i + 1 : i + 4] == [
        "Status: PENDING",
        "Eval: no",
        "Mutation: n/a (pure refactor, no new behavior)",
    ]
    assert lines[i + 4] == ""
    assert lines[i + 5] == "Build the engine on top of the parser."


def test_cli_brief_out_file(tmp_path):
    p = write(tmp_path, CANON)
    out = tmp_path / "brief-2.md"
    r = run_cli("brief", str(p), "2", "--out", str(out))
    assert r.returncode == 0, r.stderr
    body = out.read_text()
    assert "### Step 2: engine" in body
    assert "Test: uv run pytest" in body


# ---- byte-preserving set / append ------------------------------------------


def _one_line_differs(before: bytes, after: bytes, needle: str):
    b = before.decode("utf-8").splitlines(keepends=True)
    a = after.decode("utf-8").splitlines(keepends=True)
    assert len(a) == len(b)
    diffs = [i for i in range(len(b)) if a[i] != b[i]]
    assert len(diffs) == 1, f"expected exactly one changed line, got {diffs}"
    assert needle in a[diffs[0]]


def test_set_status_byte_preserving_lf(tmp_path):
    p = write(tmp_path, CANON, newline="\n")
    before = p.read_bytes()
    note = tmp_path / "note.txt"
    note.write_text("87 tests green, def5678")
    # `failed` takes a note through the same path; `done` is the engine's since adw 3.4.8
    r = run_cli("set", str(p), "2", "failed", "--note-file", str(note))
    assert r.returncode == 0, r.stderr
    after = p.read_bytes()
    _one_line_differs(before, after, "Status: FAILED (87 tests green, def5678)")


def test_set_status_byte_preserving_crlf(tmp_path):
    p = write(tmp_path, CANON, newline="\r\n")
    before = p.read_bytes()
    note = tmp_path / "note.txt"
    note.write_text("done on windows")
    r = run_cli("set", str(p), "2", "failed", "--note-file", str(note))
    assert r.returncode == 0, r.stderr
    after = p.read_bytes()
    assert b"\r\n" in after
    assert after.count(b"\r\n") == before.count(b"\r\n")
    _one_line_differs(before, after, "Status: FAILED (done on windows)")
    # the rewritten Status line itself keeps CRLF
    pairs = zip(after.splitlines(True), before.splitlines(True), strict=False)
    changed = next(a for a, b in pairs if a != b)
    assert changed.endswith(b"\r\n")


def test_set_status_blocked_decision(tmp_path):
    p = write(tmp_path, CANON)
    note = tmp_path / "n.txt"
    note.write_text("sqlite or postgres?")
    r = run_cli("set", str(p), "2", "blocked-decision", "--note-file", str(note))
    assert r.returncode == 0, r.stderr
    assert "Status: BLOCKED (decision: sqlite or postgres?)" in p.read_text()


def test_set_status_pending_no_note(tmp_path):
    p = write(tmp_path, CANON)
    r = run_cli("set", str(p), "1", "pending")
    assert r.returncode == 0, r.stderr
    plan = next_plan.parse_plan(p.read_text())
    assert plan.steps[0].state == "PENDING"


def test_append_findings_replaces_none(tmp_path):
    p = write(tmp_path, CANON)
    note = tmp_path / "f.txt"
    note.write_text("Minor: parser drops trailing whitespace (parser.py:88).")
    r = run_cli("append", str(p), "findings", "--note-file", str(note))
    assert r.returncode == 0, r.stderr
    text = p.read_text()
    findings = text.split("## Findings", 1)[1]
    assert "(none)" not in findings
    assert "parser drops trailing whitespace" in findings


def test_append_findings_second_entry_keeps_first(tmp_path):
    p = write(tmp_path, CANON)
    n1 = tmp_path / "1.txt"
    n1.write_text("Minor: first finding.")
    n2 = tmp_path / "2.txt"
    n2.write_text("Important: second finding.")
    run_cli("append", str(p), "findings", "--note-file", str(n1))
    run_cli("append", str(p), "findings", "--note-file", str(n2))
    findings = p.read_text().split("## Findings", 1)[1]
    assert "first finding" in findings and "second finding" in findings


def test_append_decisions(tmp_path):
    p = write(tmp_path, CANON)
    note = tmp_path / "d.txt"
    note.write_text("Ruling: kept float for the score / spec says Decimal / drift over 1e6 rows.")
    r = run_cli("append", str(p), "decisions", "--note-file", str(note))
    assert r.returncode == 0, r.stderr
    dec = p.read_text().split("## Decisions", 1)[1].split("## Findings", 1)[0]
    assert "kept float for the score" in dec


def test_append_preserves_other_bytes(tmp_path):
    p = write(tmp_path, CANON)
    before = p.read_bytes()
    note = tmp_path / "f.txt"
    note.write_text("Minor: something.")
    run_cli("append", str(p), "findings", "--note-file", str(note))
    after = p.read_bytes()
    # everything up to the Findings section body is unchanged
    head_before = before.split(b"## Findings")[0]
    head_after = after.split(b"## Findings")[0]
    assert head_before == head_after


# ---- the appender's newline guard (carries spec R14, Step 11 of the 3.5.9 engine plan) --------


def test_append_puts_the_bullet_on_its_own_line_after_an_unterminated_last_bullet():
    """A section whose last bullet lacks the file's newline (a plan saved without one, or a
    hand edit at the end of the file) gets the new bullet on its own line, the old bullet intact;
    without the guard the two bullets joined on one line."""
    data = CANON.replace("## Findings\n(none)\n", "## Findings\n- first finding").encode("utf-8")
    assert not data.endswith(b"\n")
    out = next_plan.append_section_bytes(data, "findings", "Minor: second finding.")
    assert out.endswith(b"## Findings\n- first finding\n- Minor: second finding.\n")
    assert b"first finding- Minor" not in out


def test_append_after_a_terminated_last_bullet_is_byte_identical_to_the_plain_insertion():
    """A section ending in the file's newline is unchanged from the appender's output before
    the guard: the new bullet follows the last one, no blank line between, nothing else moved."""
    data = CANON.replace("## Findings\n(none)\n", "## Findings\n- first finding\n").encode("utf-8")
    out = next_plan.append_section_bytes(data, "findings", "Minor: second finding.")
    expected = CANON.replace(
        "## Findings\n(none)\n", "## Findings\n- first finding\n- Minor: second finding.\n"
    ).encode("utf-8")
    assert out == expected
    crlf = data.replace(b"\n", b"\r\n")
    out_crlf = next_plan.append_section_bytes(crlf, "findings", "Minor: second finding.")
    assert out_crlf == expected.replace(b"\n", b"\r\n")


# ---- migration --------------------------------------------------------------


def test_migrate_creates_backup(tmp_path):
    p = write(tmp_path, "# X Implementation Plan\n\n## Step 1: a [done]\nbody\n")
    run_cli("migrate", str(p))
    assert (tmp_path / "prompt_plan.md.bak").exists()


def test_migrate_dreams_style_marks_done(tmp_path):
    src = (
        "# Sweep Implementation Plan\n\nStanding rules: uv, pytest.\n\n"
        "## Step 1: Build-repo scaffold [done]\nbody one\n\n"
        "## Step 2: Portfolio parsers [done]\nbody two\n\n"
        "## Step 7: SKILL.md Phase A [done]\nbody\n"
    )
    p = write(tmp_path, src)
    run_cli("migrate", str(p))
    out = p.read_text()
    assert only_placeholder_reason(out), out
    plan = next_plan.parse_plan(out)
    assert all(s.state == "DONE" for s in plan.steps)
    assert [s.number for s in plan.steps] == [1, 2, 3]


def test_migrate_langsmith_emdash_style(tmp_path):
    src = (
        "# LIA Implementation Plan\n\nGlobal conventions: uv run pytest.\n\n"
        "## Step 1 \u2014 Project scaffold [x]\nbody\n\n"
        "## Step 17 \u2014 End-to-end [ ]\nbody\n"
    )
    p = write(tmp_path, src)
    run_cli("migrate", str(p))
    out = p.read_text()
    assert only_placeholder_reason(out), out
    plan = next_plan.parse_plan(out)
    assert plan.steps[0].state == "DONE"
    assert plan.steps[1].state == "PENDING"


def test_migrate_pi_mdm_prefix_style(tmp_path):
    src = (
        "# starter build plan\n\nConstraints: stdlib only.\n\n"
        "### Step 1 - [x] Factory scaffold (done 2026-07-13, 3 tests green)\nbody\n\n"
        "### Step 9 - [ ] Not yet\nbody\n"
    )
    p = write(tmp_path, src)
    run_cli("migrate", str(p))
    out = p.read_text()
    assert only_placeholder_reason(out), out
    plan = next_plan.parse_plan(out)
    assert plan.steps[0].state == "DONE"
    assert "3 tests green" in plan.steps[0].note
    assert plan.steps[1].state == "PENDING"


def test_migrate_cyber_complete_table(tmp_path):
    src = (
        "# Cyber Implementation Plan\n\nInvariants apply.\n\n"
        "## Complete\n\n| Step | Result |\n|---|---|\n"
        "| 11 | done abc123 |\n| 20 | done def456 |\n\n---\n\n"
        "## Step 11: Kali baseline\nbody\n\n"
        "## Step 21: worked use cases\nbody\n\n"
        "## Phase 5: the org repo\nbody\n"
    )
    p = write(tmp_path, src)
    run_cli("migrate", str(p))
    out = p.read_text()
    assert only_placeholder_reason(out), out
    plan = next_plan.parse_plan(out)
    # step 11 is in the Complete table -> DONE; 21 and Phase 5 are open -> PENDING
    by_was = {s.title: s.state for s in plan.steps}
    assert any("was Step 11" in t and st == "DONE" for t, st in by_was.items())
    assert any("was Step 21" in t and st == "PENDING" for t, st in by_was.items())
    assert any("was Phase 5" in t for t in by_was)


def test_migrate_renumbers_contiguously(tmp_path):
    src = (
        "# X Implementation Plan\n\nrules.\n\n"
        "## Step 11: a\nb\n\n## Step 20: c\nd\n\n## Step 18: e\nf\n"
    )
    p = write(tmp_path, src)
    run_cli("migrate", str(p))
    plan = next_plan.parse_plan(p.read_text())
    assert [s.number for s in plan.steps] == [1, 2, 3]


@pytest.mark.parametrize("name", LEGACY_FILES + NEW_LEGACY_FILES)
def test_migrate_real_legacy_then_validate(tmp_path, name):
    src = (FIXTURES / name).read_bytes()
    p = tmp_path / "prompt_plan.md"
    p.write_bytes(src)
    r = run_cli("migrate", str(p))
    assert r.returncode == 0, r.stderr
    out = p.read_text()
    assert only_placeholder_reason(out), f"{name} still invalid after migrate:\n" + "\n".join(
        f"  line {ln}: {msg}" for ln, msg in next_plan.validate_text(out)
    )
    assert next_plan.validate_text(out.replace("PACKAGE", "filled_pkg")) == []


def test_migrate_refuses_a_plan_already_in_the_grammar(tmp_path):
    """A second migrate used to insert a second Status/Eval/Mutation block under every
    step, and the result validated. The interactive flow offers migrate whenever
    validate fails, and after E a freshly migrated plan fails validate on the PACKAGE
    placeholder, so the second migrate is one yes away. Refuse it in code."""
    p = write(tmp_path, CANON)
    before = p.read_bytes()
    r = run_cli("migrate", str(p))
    assert r.returncode == 2
    assert "already in the plan grammar" in r.stderr
    assert "Traceback" not in r.stderr
    assert len(r.stderr.strip().splitlines()) == 1
    assert p.read_bytes() == before
    assert not (tmp_path / "prompt_plan.md.bak").exists()


def test_migrate_refuses_its_own_output(tmp_path):
    p = write(tmp_path, "# X Implementation Plan\n\nrules.\n\n## Step 1: a [done]\nbody\n")
    assert run_cli("migrate", str(p)).returncode == 0
    once = p.read_bytes()
    assert run_cli("migrate", str(p)).returncode == 2
    assert p.read_bytes() == once


def test_validate_rejects_duplicated_step_lines():
    """The parser takes the last Status/Eval/Mutation line it sees; a step carrying two
    of any is ambiguous and is refused with the step's line number."""
    dup = CANON.replace(
        "Status: PENDING\nEval: no\nMutation: n/a (pure refactor, no new behavior)\n",
        "Status: PENDING\nEval: no\nMutation: n/a (pure refactor, no new behavior)\n"
        "Status: PENDING\nEval: no\nMutation: n/a (pure refactor, no new behavior)\n",
        1,
    )
    reasons = next_plan.validate_text(dup)
    msgs = [m for _ln, m in reasons]
    assert any("Step 2" in m and "more than one Status" in m for m in msgs), msgs
    assert any("Step 2" in m and "more than one Eval" in m for m in msgs), msgs
    assert any("Step 2" in m and "more than one Mutation" in m for m in msgs), msgs
    assert len(reasons) == 3


@pytest.mark.parametrize(
    "argv",
    [
        ["validate", "PLAN"],
        ["migrate", "PLAN"],
        ["status", "PLAN"],
        ["next", "PLAN"],
        ["brief", "PLAN", "1"],
        ["set", "PLAN", "1", "done", "--note-file", "NOTE"],
        ["append", "PLAN", "findings", "--note-file", "NOTE"],
    ],
    ids=lambda a: a[0],
)
def test_cli_missing_plan_is_one_line(tmp_path, argv):
    """Every subcommand fails with one line on a missing plan, not a FileNotFoundError
    traceback; the interactive flow runs validate directly in repos that may have none."""
    missing = tmp_path / "nope.md"
    note = tmp_path / "note.txt"
    note.write_text("n\n")
    argv = [str(missing) if a == "PLAN" else str(note) if a == "NOTE" else a for a in argv]
    r = run_cli(*argv)
    assert r.returncode == 2
    assert r.stdout == ""
    assert r.stderr.strip().splitlines() == [f"{missing}: no such file"]


# ---- migrate: marker-before-Step headers and list-item steps (review, 2026-09-11) ------------
# one plan writes `### [ ] Step 4a -- title` and `### [x] Step 1: title (2026-08-24)`; another
# `- [x] Step 0.1: title (evidence)`. The header regex wanted `Step <digits>` right after the
# hashes, so both plans collapsed to one migrated step. Labels may carry a letter or a dotted
# minor; steps are renumbered 1..N in document order and one Findings line records the map.

CYBER_MAP = (
    "- Legacy step map: 11 -> 1, 20 -> 2, 21 -> 3, Phase 5 -> 4, 18 -> 5, 19 -> 6, "
    "20 -> 7, Phase 6 -> 8"
)


def test_migrate_header_marker_plan_validates_with_states(tmp_path):
    p = tmp_path / "prompt_plan.md"
    p.write_bytes((FIXTURES / "project-a-marker-style.md").read_bytes())
    r = run_cli("migrate", str(p))
    assert r.returncode == 0, r.stderr
    out = p.read_text()
    assert only_placeholder_reason(out), "\n".join(
        f"  line {ln}: {msg}" for ln, msg in next_plan.validate_text(out)
    )
    plan = next_plan.parse_plan(out)
    assert [s.number for s in plan.steps] == list(range(1, 10))
    assert all(s.state == "PENDING" for s in plan.steps)
    titles = [s.title for s in plan.steps]
    assert titles[0] == "naza, enafil, fudidakit oridukuduk, did ofalule (no kulupeg ivulivif)"
    assert titles[3] == "bakedigeni erinim: tag, vebi, gipuz, zivuviged [was Step 4a]"
    assert titles[7] == "(Q1 = YES only) nizimi fopab atelo off the itoza ivet [was Step 5c]"
    assert titles[8] == "0.9.1, docs, deploy, isef bulonesofo, done-check [was Step 6]"
    assert plan.findings == (
        "- Legacy step map: 1 -> 1, 2 -> 2, 3 -> 3, 4a -> 4, 4b -> 5, 5a -> 6, 5b -> 7, "
        "5c -> 8, 6 -> 9"
    )
    # the step bodies travel with their steps
    assert "Gipodomuzapuripefik" in plan.steps[0].body
    assert "nizimi ogese okovotoko" in plan.steps[7].body


def test_migrate_header_marker_done_with_date_evidence(tmp_path):
    """project-a's other shape: `### [x] Step N: title (date)`. A trailing parenthetical that
    reads as evidence (a date, merged, a PR number, a sha) becomes the note; one that reads
    as title text stays in the title, and an open marker never flips to done."""
    src = (
        "# Fantasy GM plan\n\nrules.\n\n"
        "### [x] Step 1: uzeripa afarulab, did peroniteti, gesat kurura (2026-08-24)\nbody\n\n"
        "### [x] Step 2: utez zefa duzog -- dodave, asuban (2026-08-24; raw mSettings)\nbody\n\n"
        "### [ ] Step 19: Fozifuf rariva isef + data-signal odam\nbody\n\n"
        "### [x] Step 20: eval ibiveke (nilurev before ivulivif)\nbody\n\n"
        "### [ ] Step 21: une the dodave (sakovigu 2026-09-15)\nbody\n"
    )
    plan = next_plan.parse_plan(next_plan.migrate_text(src))
    assert [(s.number, s.state, s.note) for s in plan.steps] == [
        (1, "DONE", "2026-08-24"),
        (2, "DONE", "2026-08-24; raw mSettings"),
        (3, "PENDING", ""),
        (4, "DONE", "migrated from legacy"),
        (5, "PENDING", ""),
    ]
    assert plan.steps[0].title == "uzeripa afarulab, did peroniteti, gesat kurura"
    assert plan.steps[1].title == "utez zefa duzog -- dodave, asuban"
    assert plan.steps[2].title == "Fozifuf rariva isef + data-signal odam [was Step 19]"
    assert plan.steps[3].title == "eval ibiveke (nilurev before ivulivif) [was Step 20]"
    assert plan.steps[4].title == "une the dodave (sakovigu 2026-09-15) [was Step 21]"


def test_migrate_list_style_plan_validates_with_states(tmp_path):
    p = tmp_path / "prompt_plan.md"
    p.write_bytes((FIXTURES / "project-b-list-style.md").read_bytes())
    r = run_cli("migrate", str(p))
    assert r.returncode == 0, r.stderr
    out = p.read_text()
    assert only_placeholder_reason(out), "\n".join(
        f"  line {ln}: {msg}" for ln, msg in next_plan.validate_text(out)
    )
    plan = next_plan.parse_plan(out)
    assert [s.number for s in plan.steps] == list(range(1, 47))
    states = [s.state for s in plan.steps]
    assert states[:33] == ["DONE"] * 33 and states[33:] == ["PENDING"] * 13
    assert plan.steps[0].title == "POST per-resource nuzilatur imenop [was Step 0.1]"
    assert plan.steps[0].note == "2026-07-28, merged to main @ 3d204a8, PR #10"
    assert plan.steps[11].title == "windows_for_keeper utunepise and rured [was Step 4a.1]"
    assert plan.steps[23].title == "usuzun zomapiz, robebe osedebobeno, Bidulonep [was Step 5.4a]"
    assert plan.steps[30].note == "migrated from legacy"  # 7.1 is [x] with no parenthetical
    assert plan.steps[33].title == "compute_baselines and per-week ogidevivav [was Step 8.1]"
    assert plan.steps[45].title == "dagoguvo buzisas and docs [was Step 11.3]"
    assert plan.findings.startswith("- Legacy step map: 0.1 -> 1, 0.2 -> 2, 1.1 -> 3, ")
    assert "4a.1 -> 12, " in plan.findings and plan.findings.endswith("11.2 -> 45, 11.3 -> 46")
    # a checkbox that is not a step stays in the body it belongs to
    assert "agubok tests/test_reconstruction_endpoint.py" in plan.steps[0].body


def test_migrate_writes_renumber_map_to_findings():
    out = next_plan.migrate_text(
        "# X Implementation Plan\n\nrules.\n\n"
        "## Step 11: a\nb\n\n## Step 20: c\nd\n\n## Step 18: e\nf\n"
    )
    assert next_plan.parse_plan(out).findings == "- Legacy step map: 11 -> 1, 20 -> 2, 18 -> 3"
    # a plan already numbered 1..N in order records nothing
    out = next_plan.migrate_text("# X Implementation Plan\n\n## Step 1: a\nb\n\n## Step 2: c\nd\n")
    assert next_plan.parse_plan(out).findings == "(none)"


def test_migrate_renumber_map_lands_in_an_existing_findings_section():
    src = (
        "# X Implementation Plan\n\n## Step 3: a\nb\n\n## Step 4: c\nd\n\n"
        "## Findings\n- kept from before\n"
    )
    plan = next_plan.parse_plan(next_plan.migrate_text(src))
    assert plan.findings == "- kept from before\n- Legacy step map: 3 -> 1, 4 -> 2"


@pytest.mark.parametrize("name", LEGACY_FILES)
def test_migrate_existing_styles_unchanged(name):
    """The four shapes migrate already knew produce the output pinned before this change.
    cyber renumbers (Step 11, 20, 21, Phase 5, ...), so it gains exactly the designed
    Findings line and nothing else."""
    out = next_plan.migrate_text((FIXTURES / name).read_text(encoding="utf-8"))
    pinned = (FIXTURES / "expected" / f"{Path(name).stem}.migrated.md").read_text(encoding="utf-8")
    if name == "cyber.md":
        assert CYBER_MAP in out
        assert out.replace(CYBER_MAP, "(none)") == pinned
    else:
        assert out == pinned


# ---- run continuity: context-N.md (review, 2026-09-11) ------------------------------------
# The brief carries Conventions plus one step and nothing from Decisions or Findings, so
# step K knows nothing of steps 1 to K-1. The context file carries that, as a separate file:
# check_gate_config substring-matches the brief to authorize gate-config edits, and a Finding
# that mentions conftest.py must never widen that allowlist.

BRIEF_STEP_2_PINNED = (
    '## Conventions binding every step\n'
    '- Test: uv run pytest\n'
    '- Lint: uv run ruff check .\n'
    '- Run: uv run python -m widget\n'
    '- Eval: uv run python -m widget.eval\n'
    '- Eval-codes: apparatus\n'
    '- Holdout: evals/\n'
    '\n'
    '### Step 2: engine\n'
    'Status: PENDING\n'
    'Eval: no\n'
    'Mutation: n/a (pure refactor, no new behavior)\n'
    '\n'
    'Build the engine on top of the parser.\n'
)


def test_render_brief_unchanged_byte_for_byte():
    """render_brief's output for the canonical fixture, pinned before context-N.md landed."""
    plan = next_plan.parse_plan(CANON)
    assert next_plan.render_brief(plan, 2) == BRIEF_STEP_2_PINNED


def test_render_context_carries_decisions_findings_and_roster():
    text = CANON.replace(
        "Status: PENDING\nEval: no\nMutation: n/a",
        "Status: FAILED (gate red twice)\nEval: no\nMutation: n/a",
        1,
    )
    text = text.replace(
        "## Decisions\n(none)",
        "## Decisions\n- Ruling: kept float [run r1]\n- Step 3: which judge?",
    ).replace("## Findings\n(none)", "## Findings\n- Minor: parser drops whitespace (parser.py:88)")
    plan = next_plan.parse_plan(text)
    ctx = next_plan.render_context(plan, 3)
    assert ctx == (
        "# Context for step 3\n\n"
        "## Decisions\n- Ruling: kept float [run r1]\n- Step 3: which judge?\n\n"
        "## Findings\n- Minor: parser drops whitespace (parser.py:88)\n\n"
        "## Steps so far\n"
        "- Step 1: parser: DONE (42 tests green, abc1234)\n"
        "- Step 2: engine: FAILED (gate red twice)\n"
    )
    # the step being briefed and later steps are not in the roster
    assert "Step 3: judge" not in ctx
    # nothing of the brief's content is duplicated here
    assert "Conventions binding every step" not in ctx and "Mutation:" not in ctx


def test_render_context_for_step_one_is_minimal():
    plan = next_plan.parse_plan(CANON)
    assert next_plan.render_context(plan, 1) == (
        "# Context for step 1\n\n"
        "## Decisions\n(none)\n\n"
        "## Findings\n(none)\n\n"
        "## Steps so far\n(none)\n"
    )


def test_render_context_unknown_step_exits():
    plan = next_plan.parse_plan(CANON)
    with pytest.raises(SystemExit, match="no step 9"):
        next_plan.render_context(plan, 9)


def test_validate_rejects_words_after_a_state_outside_the_note():
    """A Status like `DONE WITH ONE RED CASE (...)` parsed as DONE with the words folded into
    the note, so a red step read as done to every automated reader (review, 2026-09-14). Only
    a parenthetical may follow the state."""
    text = CANON.replace("Status: PENDING", "Status: DONE WITH ONE RED CASE (two of three)", 1)
    reasons = next_plan.validate_text(text)
    assert any("Status" in msg and "DONE WITH" in msg for _, msg in reasons), reasons
    clean = CANON.replace("Status: PENDING", "Status: DONE (two of three, ruled acceptable)", 1)
    assert not any("Status" in msg for _, msg in next_plan.validate_text(clean))


# ---- chunks -----------------------------------------------------------------

CHUNKED = CANON.replace("## Chunk A: core", "## Chunk A: Parser").replace(
    "### Step 3: judge", "## Chunk B: Tool\n\n### Step 3: judge"
)
UNCHUNKED = CANON.replace("## Chunk A: core\n\n", "")
MIXED = CHUNKED.replace("## Chunk B: Tool", "## Notes")


def test_parse_attaches_enclosing_chunk():
    plan = next_plan.parse_plan(CHUNKED)
    assert [s.chunk for s in plan.steps] == ["Chunk A: Parser", "Chunk A: Parser", "Chunk B: Tool"]


def test_unchunked_plan_has_no_chunk():
    plan = next_plan.parse_plan(UNCHUNKED)
    assert [s.chunk for s in plan.steps] == [None, None, None]


def test_step_under_a_non_chunk_h2_has_no_chunk():
    plan = next_plan.parse_plan(MIXED)
    assert [s.chunk for s in plan.steps] == ["Chunk A: Parser", "Chunk A: Parser", None]


def test_last_in_chunk_marks_chunk_ends_and_plan_end():
    chunked = next_plan.parse_plan(CHUNKED)
    assert [s.last_in_chunk for s in chunked.steps] == [False, True, True]
    unchunked = next_plan.parse_plan(UNCHUNKED)
    assert [s.last_in_chunk for s in unchunked.steps] == [False, False, True]


def test_cli_next_prints_chunk_and_last_in_chunk(tmp_path):
    p = write(tmp_path, CHUNKED)
    data = json.loads(run_cli("next", str(p)).stdout)
    assert set(data) == {"number", "title", "chunk", "last_in_chunk"}
    assert data["number"] == 2
    assert data["chunk"] == "Chunk A: Parser"
    assert data["last_in_chunk"] is True


def test_next_never_returns_done_and_takes_in_progress_in_plan_order():
    text = CANON.replace("Status: PENDING\nEval: no", "Status: IN PROGRESS\nEval: no", 1)
    plan = next_plan.parse_plan(text)
    assert plan.steps[1].state == "IN PROGRESS"
    assert next_plan.next_step(plan).number == 2
    later = CANON.replace("Status: PENDING\nEval: yes", "Status: IN PROGRESS\nEval: yes")
    assert next_plan.next_step(next_plan.parse_plan(later)).number == 2


def test_cli_status_carries_chunk_per_step(tmp_path):
    p = write(tmp_path, CHUNKED)
    data = json.loads(run_cli("status", str(p)).stdout)
    assert [s["chunk"] for s in data["steps"]] == [
        "Chunk A: Parser",
        "Chunk A: Parser",
        "Chunk B: Tool",
    ]


# ---- batch ------------------------------------------------------------------


def _plan_with(chunks: list[tuple[str | None, list[tuple[int, str]]]]) -> str:
    """Build a plan text: chunks is [(chunk header text or None, [(number, state), ...])]."""
    out = [
        "# Batch Plan",
        "Spec: spec.md. Owner: the owner. Written: 2026-09-17.",
        "",
        "## Conventions binding every step",
        "- Test: uv run pytest",
        "- Lint: uv run ruff check .",
        "",
    ]
    for header, steps in chunks:
        if header:
            out += [f"## {header}", ""]
        for n, state in steps:
            out += [f"### Step {n}: s{n}", f"Status: {state}", "Eval: no"]
            out += ["Mutation: required", "body", ""]
    out += ["## Decisions", "(none)", "", "## Findings", "(none)", ""]
    return "\n".join(out)


def test_batch_mid_chunk_returns_rest_of_that_chunk_only():
    b = next_plan.batch_steps(next_plan.parse_plan(CHUNKED))
    assert b["steps"] == [2]
    assert (b["first"], b["last"], b["boundary"]) == (2, 2, "chunk")
    assert b["chunk"] == "Chunk A: Parser"
    assert b["chunk_label"] == "A"


def test_batch_moves_to_next_chunk_once_the_first_is_done():
    text = CHUNKED.replace("Status: PENDING\nEval: no", "Status: DONE (x)\nEval: no")
    b = next_plan.batch_steps(next_plan.parse_plan(text))
    assert b["steps"] == [3]
    assert b["chunk"] == "Chunk B: Tool"


def test_batch_unchunked_runs_to_the_plan_end():
    b = next_plan.batch_steps(next_plan.parse_plan(UNCHUNKED))
    assert b["steps"] == [2, 3]
    assert (b["boundary"], b["chunk"]) == ("plan", None)


def test_batch_sentence_forms():
    done = [(n, "DONE (x)") for n in range(1, 5)]
    pending = [(n, "PENDING") for n in range(5, 9)]
    text = _plan_with([("Chunk A: Base", done), ("Chunk B: Tool", pending)])
    plan = next_plan.parse_plan(text)
    assert (
        next_plan.batch_steps(plan)["sentence"]
        == "Running Steps 5 to 8, stopping at the end of Chunk B (the reviewer runs there)."
    )
    assert (
        next_plan.batch_steps(plan, context_pct=41)["sentence"]
        == "Running Steps 5 to 8, stopping at the end of Chunk B or at 60 percent context "
        "(the reviewer runs there)."
    )
    single = next_plan.parse_plan(
        _plan_with([("Chunk A: Base", done), ("Chunk B: Tool", pending[:1])])
    )
    assert (
        next_plan.batch_steps(single)["sentence"]
        == "Running Step 5, stopping at the end of Chunk B (the reviewer runs there)."
    )
    flat = next_plan.parse_plan(_plan_with([(None, [(n, "PENDING") for n in (1, 2, 3)])]))
    assert (
        next_plan.batch_steps(flat)["sentence"]
        == "Running Steps 1 to 3, stopping at the end of the plan."
    )


def test_batch_without_context_pct_carries_null_and_no_clause():
    b = next_plan.batch_steps(next_plan.parse_plan(CHUNKED))
    assert b["context_pct"] is None
    assert "percent" not in b["sentence"]


def test_batch_nothing_pending():
    text = CANON.replace("Status: PENDING", "Status: DONE (y)")
    b = next_plan.batch_steps(next_plan.parse_plan(text))
    assert b["steps"] == []
    assert b["sentence"] == "Nothing to run: every step is DONE, FAILED, or BLOCKED."


def test_cli_batch_prints_json_then_sentence_and_writes_nothing(tmp_path):
    p = write(tmp_path, CHUNKED)
    before = p.read_bytes()
    r = run_cli("batch", str(p))
    assert r.returncode == 0, r.stderr
    line1, line2 = r.stdout.splitlines()
    data = json.loads(line1)
    assert data["steps"] == [2] and data["context_pct"] is None
    assert line2 == "Running Step 2, stopping at the end of Chunk A (the reviewer runs there)."
    assert p.read_bytes() == before
    r = run_cli("batch", str(p), "--context-pct", "41")
    assert json.loads(r.stdout.splitlines()[0])["context_pct"] == 41
    assert "or at 60 percent context" in r.stdout.splitlines()[1]


def test_cli_batch_nothing_pending_json_is_bare(tmp_path):
    p = write(tmp_path, CANON.replace("Status: PENDING", "Status: DONE (y)"))
    r = run_cli("batch", str(p))
    assert r.stdout.splitlines()[0] == '{"steps": []}'


@pytest.mark.parametrize("bad", ["101", "abc", "-1"])
def test_cli_batch_rejects_bad_context_pct(tmp_path, bad):
    p = write(tmp_path, CHUNKED)
    r = run_cli("batch", str(p), "--context-pct", bad)
    assert r.returncode == 2
    assert "usage" in r.stderr


# ---- validate: chunk coverage and the cap -----------------------------------

REPO = Path(__file__).resolve().parents[1]
REAL_PLANS = [
    *sorted((REPO / "evals" / "next-auto" / "fixtures" / "plans").glob("*.md")),
]


def test_validate_step_under_non_chunk_h2_when_chunks_exist():
    text = _plan_with(
        [("Chunk A: One", [(1, "PENDING"), (2, "PENDING")]), ("Later work", [(3, "PENDING")])]
    )
    plan = next_plan.parse_plan(text)
    reasons = next_plan.validate_text(text)
    assert reasons == [
        (
            plan.steps[2].header_line,
            "Step 3: sits under '## Later work', which is not a '## Chunk' header, "
            "but the plan uses chunks",
        )
    ]


def test_validate_six_step_chunk_names_the_chunk():
    text = _plan_with([("Chunk A: Big", [(n, "PENDING") for n in range(1, 7)])])
    reasons = next_plan.validate_text(text)
    header_line = text.splitlines().index("## Chunk A: Big") + 1
    assert reasons == [(header_line, "Chunk A: Big holds 6 steps; the cap is 5")]


def test_validate_five_step_chunk_and_unchunked_nine_pass():
    five = _plan_with([("Chunk A: Ok", [(n, "PENDING") for n in range(1, 6)])])
    assert next_plan.validate_text(five) == []
    nine = _plan_with([(None, [(n, "PENDING") for n in range(1, 10)])])
    assert next_plan.validate_text(nine) == []


def test_cli_validate_chunk_reasons_carry_path_and_line(tmp_path):
    text = _plan_with([("Chunk A: One", [(1, "PENDING")]), ("Later work", [(2, "PENDING")])])
    p = write(tmp_path, text)
    r = run_cli("validate", str(p))
    assert r.returncode == 2
    assert r.stderr.startswith(f"{p}:")
    assert "not a '## Chunk' header" in r.stderr


@pytest.mark.parametrize("plan_path", REAL_PLANS, ids=lambda p: p.name)
def test_real_plans_validate_under_chunk_rules(plan_path):
    assert next_plan.validate_text(plan_path.read_text(encoding="utf-8")) == []


# the pre-migration originals under tests/fixtures/legacy/ cannot validate by design; their
# migrated expectations under expected/ carry the PACKAGE placeholder until a human fills it,
# so they are swept with the placeholder filled, the same way test_migrate_output_validates does
MIGRATED_PLANS = sorted((REPO / "tests" / "fixtures" / "legacy" / "expected").glob("*.md"))


@pytest.mark.parametrize("plan_path", MIGRATED_PLANS, ids=lambda p: p.name)
def test_migrated_fixtures_validate_under_chunk_rules_once_filled(plan_path):
    text = plan_path.read_text(encoding="utf-8")
    assert only_placeholder_reason(text)
    assert next_plan.validate_text(text.replace(next_plan.PLACEHOLDER, "pkg")) == []


def test_set_and_append_byte_preserving_on_chunked_crlf_plan(tmp_path):
    p = write(tmp_path, CHUNKED, newline="\r\n")
    before = p.read_bytes()
    note = tmp_path / "note.txt"
    note.write_text("chunked done")
    assert run_cli("set", str(p), "2", "failed", "--note-file", str(note)).returncode == 0
    after = p.read_bytes()
    assert after.count(b"\r\n") == before.count(b"\r\n")
    _one_line_differs(before, after, "Status: FAILED (chunked done)")
    note.write_text("a finding")
    assert run_cli("append", str(p), "findings", "--note-file", str(note)).returncode == 0
    appended = p.read_bytes()
    assert appended.count(b"\r\n") == after.count(b"\r\n")
    _one_line_differs(after, appended, "- a finding")


# ---- batch: blockers and holes ----------------------------------------------

DONE_AB = [(1, "DONE (x)"), (2, "DONE (x)")]


def test_batch_stops_before_a_blocked_step_in_the_chunk():
    steps = [(3, "PENDING"), (4, "BLOCKED (decision: which store)"), (5, "PENDING")]
    plan = next_plan.parse_plan(_plan_with([("Chunk A: Base", DONE_AB), ("Chunk B: Tool", steps)]))
    b = next_plan.batch_steps(plan)
    assert b["steps"] == [3]
    assert (b["first"], b["last"], b["boundary"]) == (3, 3, "blocked")
    assert b["sentence"] == "Running Step 3, stopping before Step 4 (BLOCKED)."
    assert (
        next_plan.batch_steps(plan, context_pct=12)["sentence"]
        == "Running Step 3, stopping before Step 4 (BLOCKED) or at 60 percent context."
    )


def test_batch_is_empty_when_a_blocker_precedes_the_next_unfinished_step():
    steps = [(3, "FAILED (red twice)"), (4, "PENDING")]
    plan = next_plan.parse_plan(_plan_with([("Chunk A: Base", DONE_AB), ("Chunk B: Tool", steps)]))
    b = next_plan.batch_steps(plan)
    assert b["steps"] == [] and b["blocker"] == 3
    assert b["sentence"] == "Nothing to run: Step 3 is FAILED; resolve it before Chunk B continues."
    flat = next_plan.parse_plan(
        _plan_with([(None, [(1, "BLOCKED (apparatus: no gh)"), (2, "PENDING")])])
    )
    assert (
        next_plan.batch_steps(flat)["sentence"]
        == "Nothing to run: Step 1 is BLOCKED; resolve it before the plan continues."
    )


def test_batch_is_empty_when_an_earlier_chunk_ends_in_a_blocker():
    """A blocked or failed last step of the previous chunk parks the plan: batch never
    opens the next chunk while that chunk's decision is unmade and its review unrun."""
    chunk_b = [(3, "DONE (x)"), (4, "BLOCKED (decision: which grader)")]
    chunk_c = [(5, "PENDING"), (6, "PENDING")]
    chunks = [("Chunk A: Base", DONE_AB), ("Chunk B: Tool", chunk_b), ("Chunk C: Text", chunk_c)]
    b = next_plan.batch_steps(next_plan.parse_plan(_plan_with(chunks)))
    assert b["steps"] == [] and b["blocker"] == 4
    assert b["sentence"] == (
        "Nothing to run: Step 4 is BLOCKED; resolve it before Chunk C continues."
    )
    failed = next_plan.parse_plan(
        _plan_with(
            [("Chunk A: Base", [(1, "FAILED (red twice)")]), ("Chunk B: Tool", [(2, "PENDING")])]
        )
    )
    assert next_plan.batch_steps(failed)["blocker"] == 1
    # an unchunked plan already stops at the blocker; the earlier-chunk scan changes nothing there
    flat = next_plan.parse_plan(_plan_with([(None, [(1, "DONE (x)"), (2, "PENDING")])]))
    assert next_plan.batch_steps(flat)["steps"] == [2]


def test_cli_batch_blocked_json_carries_the_blocker(tmp_path):
    steps = [(3, "FAILED (red twice)"), (4, "PENDING")]
    p = write(tmp_path, _plan_with([("Chunk A: Base", DONE_AB), ("Chunk B: Tool", steps)]))
    r = run_cli("batch", str(p))
    assert r.returncode == 0
    assert r.stdout.splitlines()[0] == '{"steps": [], "blocker": 3}'


def test_batch_sentence_enumerates_holes():
    steps = [(2, "PENDING"), (3, "DONE (x)"), (4, "PENDING"), (5, "PENDING")]
    plan = next_plan.parse_plan(_plan_with([("Chunk B: Tool", [(1, "DONE (x)"), *steps])]))
    b = next_plan.batch_steps(plan)
    assert b["steps"] == [2, 4, 5]
    assert b["sentence"] == (
        "Running Steps 2, 4, and 5, stopping at the end of Chunk B (the reviewer runs there)."
    )
    two = _plan_with([("Chunk B: Tool", [(2, "PENDING"), (3, "DONE (x)"), (4, "PENDING")])])
    sentence = next_plan.batch_steps(next_plan.parse_plan(two))["sentence"]
    assert sentence.startswith("Running Steps 2 and 4, stopping")


def test_chunk_header_needs_the_word_chunk_not_the_prefix():
    text = _plan_with(
        [("Chunk A: One", [(1, "PENDING")]), ("Chunking rationale", [(2, "PENDING")])]
    )
    plan = next_plan.parse_plan(text)
    assert [s.chunk for s in plan.steps] == ["Chunk A: One", None]
    reasons = next_plan.validate_text(text)
    assert len(reasons) == 1 and "'## Chunking rationale'" in reasons[0][1]
    bare = _plan_with([("Chunk", [(1, "PENDING")])])
    assert next_plan.parse_plan(bare).steps[0].chunk == "Chunk"


# ---- ai-dlc-record: the Record key, the brief, and the plan hash (R11, R17) -----------------

# acme/platform carries the same bytes at tools/workflows/fixtures/plan-hash/prompt_plan.md
# and asserts the same literal in tools/workflows/record.test.mjs (ai-dlc-record R4)
PLAN_HASH_FIXTURE = Path(__file__).parent / "fixtures" / "plan-hash" / "prompt_plan.md"
PLAN_HASH_LITERAL = "sha256:5bcded0ef2895ed5e1a49ee2f34defd49aa8331987bab1c8c21354d7feff557a"
RECORD_LINE = "- Record: node tools/workflows/record.mjs ai-dlc-record"
CANON_WITH_RECORD = CANON.replace("- Holdout: evals/", "- Holdout: evals/\n" + RECORD_LINE)


def _fixture_text() -> str:
    return PLAN_HASH_FIXTURE.read_bytes().decode("utf-8")


def _edited(text: str, old: str, new: str) -> str:
    """One replacement that must land, so an edit test can never pass on an unchanged text."""
    assert old in text, old
    return text.replace(old, new, 1)


def test_r17_plan_hash_of_the_shared_fixture_is_the_platform_literal():
    """ai-dlc-record R11, R17: the engine hashes the shared fixture to the literal the
    appender's own test asserts, so `approved` and the engine agree on which plan was approved."""
    assert next_plan.plan_hash(_fixture_text()) == PLAN_HASH_LITERAL


def test_r17_plan_hash_keeps_status_and_findings_edits_and_changes_with_a_step():
    """ai-dlc-record R11, R17: an approved plan keeps its hash while its Status lines and
    Findings change, and loses it when a step or a Convention changes."""
    text = _fixture_text()
    base = next_plan.plan_hash(text)
    assert next_plan.plan_hash(_edited(text, "Status: pending\n", "")) == base
    finding = "- Nothing surprising found while writing this fixture.\n"
    assert next_plan.plan_hash(_edited(text, finding, "")) == base
    ruling = "Recommendation: yes, they are small."
    assert next_plan.plan_hash(_edited(text, ruling, "Recommendation: no.")) == base
    step = "Add a small pure helper function"
    assert next_plan.plan_hash(_edited(text, step, step + ", renamed")) != base
    assert next_plan.plan_hash(_edited(text, "- Test: npm test", "- Test: npm run t")) != base


def test_r11_plan_hash_normalizes_line_endings_like_the_appender():
    """ai-dlc-record R11: `\\r\\n` and a lone `\\r` read as `\\n`, as record.mjs reads them."""
    text = _fixture_text()
    assert next_plan.plan_hash(text.replace("\n", "\r\n")) == PLAN_HASH_LITERAL
    assert next_plan.plan_hash(text.replace("\n", "\r")) == PLAN_HASH_LITERAL


def test_r11_plan_hash_cli_prints_the_hash():
    r = run_cli("plan-hash", str(PLAN_HASH_FIXTURE))
    assert r.returncode == 0, r.stderr
    assert r.stdout == PLAN_HASH_LITERAL + "\n"


def test_r11_validate_accepts_a_plan_with_and_without_record():
    """ai-dlc-record R11: Record is an optional Conventions key; the parser keeps its value."""
    assert next_plan.validate_text(CANON) == []
    assert next_plan.validate_text(CANON_WITH_RECORD) == []
    conventions = next_plan.parse_plan(CANON_WITH_RECORD).conventions
    assert conventions["Record"] == "node tools/workflows/record.mjs ai-dlc-record"


def test_r11_render_brief_omits_the_record_line():
    """ai-dlc-record R11: the brief never names the appender, so the implementer does not
    learn it exists; the brief of a plan with Record is the brief of the plan without it."""
    brief = next_plan.render_brief(next_plan.parse_plan(CANON_WITH_RECORD), 2)
    for mark in ("Record", "record.mjs", "ai-dlc-record"):
        assert mark not in brief, mark
    assert brief == next_plan.render_brief(next_plan.parse_plan(CANON), 2)


# ---- ai-dlc-record R11: the Test-files key -------------------------------------------------

TEST_FILES_LINE = "- Test-files: **/*.test.mjs, **/*_test.go ,**/test_*.py"
CANON_WITH_TEST_FILES = CANON.replace("- Holdout: evals/", "- Holdout: evals/\n" + TEST_FILES_LINE)


def test_r11_validate_accepts_a_plan_with_and_without_test_files():
    """ai-dlc-record R11: Test-files is an optional Conventions key of comma-separated globs,
    whitespace trimmed."""
    assert next_plan.validate_text(CANON_WITH_TEST_FILES) == []
    plan = next_plan.parse_plan(CANON_WITH_TEST_FILES)
    assert next_plan.plan_test_globs(plan) == ["**/*.test.mjs", "**/*_test.go", "**/test_*.py"]
    assert next_plan.plan_test_globs(next_plan.parse_plan(CANON)) is None


@pytest.mark.parametrize("line", ["- Test-files: , ,", "- Test-files:"])
def test_r11_validate_rejects_a_test_files_line_with_no_pattern(line):
    text = CANON.replace("- Holdout: evals/", "- Holdout: evals/\n" + line)
    lineno = text.splitlines().index(line) + 1
    assert next_plan.validate_text(text) == [
        (lineno, "Conventions Test-files needs at least one comma-separated glob pattern")
    ]


def test_r11_render_brief_keeps_the_test_files_line():
    """ai-dlc-record R11: the implementer needs to know which files count as tests."""
    brief = next_plan.render_brief(next_plan.parse_plan(CANON_WITH_TEST_FILES), 2)
    assert TEST_FILES_LINE in brief


# ---- toolchain-files R1: the Toolchain-files key ------------------------------------------

TOOLCHAIN_FILES_LINE = "- Toolchain-files: CLAUDE.md , .claude/**"
CANON_WITH_TOOLCHAIN_FILES = CANON.replace(
    "- Holdout: evals/", "- Holdout: evals/\n" + TOOLCHAIN_FILES_LINE
)


def test_toolchain_files_validate_accepts_a_plan_with_and_without_the_line():
    """toolchain-files R1: Toolchain-files is an optional Conventions key of comma-separated
    globs, whitespace trimmed."""
    assert next_plan.validate_text(CANON_WITH_TOOLCHAIN_FILES) == []
    plan = next_plan.parse_plan(CANON_WITH_TOOLCHAIN_FILES)
    assert next_plan.plan_toolchain_globs(plan) == ["CLAUDE.md", ".claude/**"]
    assert next_plan.plan_toolchain_globs(next_plan.parse_plan(CANON)) is None


@pytest.mark.parametrize("line", ["- Toolchain-files: , ,", "- Toolchain-files:"])
def test_toolchain_files_validate_rejects_a_line_with_no_pattern(line):
    text = CANON.replace("- Holdout: evals/", "- Holdout: evals/\n" + line)
    lineno = text.splitlines().index(line) + 1
    assert next_plan.validate_text(text) == [
        (lineno, "Conventions Toolchain-files needs at least one comma-separated glob pattern")
    ]


def test_toolchain_files_render_brief_keeps_the_line():
    """toolchain-files R1: the brief keeps the line, as it keeps every key but Record and Issue."""
    brief = next_plan.render_brief(next_plan.parse_plan(CANON_WITH_TOOLCHAIN_FILES), 2)
    assert TOOLCHAIN_FILES_LINE in brief


@pytest.mark.parametrize(
    "value, reason",
    [
        (
            "ai-dlc-record",
            "Conventions Record must be the appender's command followed by the record's slug",
        ),
        (
            'node "tools/record.mjs ai-dlc-record',
            "Conventions Record does not split into words: No closing quotation",
        ),
    ],
)
def test_r11_validate_rejects_a_record_line_that_is_not_a_command_and_a_slug(value, reason):
    """ai-dlc-record R11: the runner splits Record with shlex, command then slug."""
    line = f"- Record: {value}"
    text = CANON.replace("- Holdout: evals/", "- Holdout: evals/\n" + line)
    lineno = text.splitlines().index(line) + 1
    assert next_plan.validate_text(text) == [(lineno, reason)]


# ---- the Test-repo exemption's two header lines (adw 3.4.8) ----------------------------

# Fixture shape copied from the close-out plan's Step 5 header (docs/close-out-plan.md in
# the engine repository, read 2026-10-11): the step whose tests live in the Acme tree on devbox.
EXEMPT_LINES = (
    "Test-repo: ryanmat/acme-engagement, ~/dev/acme on devbox\n"
    "Test: ssh 203.0.113.10 'bash ~/dev/acme/.claude/hooks/session-events.test.sh'\n"
)


def _exempt_plan(step_lines: str) -> str:
    """A two-step chunked plan whose Step 1 header carries `step_lines` after Mutation."""
    text = _plan_with([("Chunk A: Base", [(1, "PENDING"), (2, "PENDING")])])
    first = "### Step 1: s1\nStatus: PENDING\nEval: no\nMutation: required\n"
    assert text.count(first) == 1
    return text.replace(first, first + step_lines, 1)


def _messages(text: str) -> list[str]:
    return [msg for _, msg in next_plan.validate_text(text)]


def test_parse_exposes_test_repo_and_test_cmd_and_none_without_them():
    plan = next_plan.parse_plan(_exempt_plan(EXEMPT_LINES))
    one, two = plan.steps
    assert one.test_repo == "ryanmat/acme-engagement, ~/dev/acme on devbox"
    assert one.test_cmd == (
        "ssh 203.0.113.10 'bash ~/dev/acme/.claude/hooks/session-events.test.sh'"
    )
    assert (two.test_repo, two.test_cmd) == (None, None)
    assert _messages(_exempt_plan(EXEMPT_LINES)) == []


def test_validate_refuses_test_repo_without_test():
    text = _exempt_plan("Test-repo: ryanmat/acme-engagement, ~/dev/acme on devbox\n")
    assert "Step 1: Test-repo: without Test:" in _messages(text)


def test_validate_refuses_test_without_test_repo():
    text = _exempt_plan("Test: ssh 203.0.113.10 'bash x.test.sh'\n")
    assert "Step 1: Test: without Test-repo:" in _messages(text)


def test_validate_refuses_an_empty_value_on_either_line():
    empty_repo = _exempt_plan("Test-repo:   \nTest: ssh 203.0.113.10 'bash x.test.sh'\n")
    assert "Step 1: Test-repo: is empty" in _messages(empty_repo)
    empty_cmd = _exempt_plan("Test-repo: ryanmat/acme-engagement\nTest: \t\n")
    assert "Step 1: Test: is empty" in _messages(empty_cmd)


def test_validate_refuses_a_second_copy_of_either_line():
    twice = _exempt_plan(EXEMPT_LINES + "Test: echo again\n")
    assert "Step 1: more than one Test: line (2)" in _messages(twice)
    twice_repo = _exempt_plan("Test-repo: a\n" + EXEMPT_LINES)
    assert "Step 1: more than one Test-repo: line (2)" in _messages(twice_repo)


def test_brief_writes_the_test_repo_lines_after_mutation_and_strips_them_from_the_prose():
    plan = next_plan.parse_plan(_exempt_plan(EXEMPT_LINES))
    lines = next_plan.render_brief(plan, 1).splitlines()
    at = lines.index("Mutation: required")
    assert lines[at + 1] == "Test-repo: ryanmat/acme-engagement, ~/dev/acme on devbox"
    assert lines[at + 2] == (
        "Test: ssh 203.0.113.10 'bash ~/dev/acme/.claude/hooks/session-events.test.sh'"
    )
    assert lines[at + 3] == "" and lines[at + 4] == "body"
    for prefix in ("Test-repo:", "Test:"):
        assert sum(1 for ln in lines if ln.startswith(prefix)) == 1, prefix


def test_brief_adds_no_test_repo_lines_to_a_step_without_them():
    plan = next_plan.parse_plan(CANON)
    for n in (1, 2, 3):
        brief = next_plan.render_brief(plan, n)
        assert not any(ln.startswith(("Test-repo:", "Test:")) for ln in brief.splitlines()), n
    assert next_plan.render_brief(plan, 2) == BRIEF_STEP_2_PINNED


def test_cli_status_carries_test_repo_and_test_cmd_null_without_them(tmp_path):
    p = write(tmp_path, _exempt_plan(EXEMPT_LINES))
    data = json.loads(run_cli("status", str(p)).stdout)
    one, two = data["steps"]
    assert one["test_repo"] == "ryanmat/acme-engagement, ~/dev/acme on devbox"
    assert one["test_cmd"] == (
        "ssh 203.0.113.10 'bash ~/dev/acme/.claude/hooks/session-events.test.sh'"
    )
    assert (two["test_repo"], two["test_cmd"]) == (None, None)


# ---- adw 3.5.0: the Issue key and the Closes line (spec items 1 and 2) -------------------

# Fixture shapes: the Issue value is a Linear id of the Acme team as its sub-issues carry
# them (DOC-800, read 2026-10-12); the Closes line is the shape acme/platform's pull
# request bodies carry (`Closes: R5` on #351, read 2026-10-12).
def _with_issue(text: str, value: str) -> str:
    """The plan with an Issue line after Lint in its Conventions."""
    lint = "- Lint: uv run ruff check .\n"
    return _edited(text, lint, f"{lint}- Issue: {value}\n")


def _closes_plan(step_lines: str) -> str:
    """A two-step chunked plan whose Step 1 header carries `step_lines` after Mutation."""
    return _exempt_plan(step_lines)


def test_parse_exposes_the_issue_key_and_closes_as_a_list():
    plan = next_plan.parse_plan(_with_issue(_closes_plan("Closes: R1, R2\n"), "DOC-123"))
    assert plan.conventions["Issue"] == "DOC-123"
    one, two = plan.steps
    assert one.closes == ["R1", "R2"]
    assert two.closes == []


@pytest.mark.parametrize("value", ["doc-123", "DOC", "DOC-", "-123", "DOC-12a", "D OC-1"])
def test_validate_refuses_an_issue_that_is_not_a_tracker_id(value):
    text = _with_issue(_closes_plan(""), value)
    lineno = text.splitlines().index(f"- Issue: {value}") + 1
    assert next_plan.validate_text(text) == [
        (lineno, f"Conventions: Issue: {value} is not a tracker id such as DOC-123")
    ]


@pytest.mark.parametrize("value", ["DOC-123", "DOC-1", "S9-42"])
def test_validate_accepts_a_tracker_id(value):
    assert next_plan.validate_text(_with_issue(_closes_plan(""), value)) == []


def test_validate_refuses_an_empty_closes_line():
    for line in ("Closes:\n", "Closes:   \n", "Closes: , \n"):
        assert _messages(_closes_plan(line)) == ["Step 1: Closes: is empty"], line


def test_validate_refuses_a_closes_id_of_another_shape():
    assert _messages(_closes_plan("Closes: R1, X2\n")) == [
        "Step 1: Closes: X2 is not a requirement id such as R12"
    ]
    assert _messages(_closes_plan("Closes: r3\n")) == [
        "Step 1: Closes: r3 is not a requirement id such as R12"
    ]


def test_validate_refuses_a_second_closes_line():
    text = _closes_plan("Closes: R1\nCloses: R2\n")
    assert _messages(text) == ["Step 1: more than one Closes: line (2)"]


def test_validate_accepts_a_closes_line():
    assert _messages(_closes_plan("Closes: R1, R12\n")) == []


def test_brief_omits_the_issue_key():
    text = _closes_plan("")
    brief = next_plan.render_brief(next_plan.parse_plan(_with_issue(text, "DOC-123")), 1)
    assert "Issue" not in brief and "DOC-123" not in brief
    assert brief == next_plan.render_brief(next_plan.parse_plan(text), 1)


def test_brief_keeps_the_closes_line_after_mutation_and_strips_it_from_the_prose():
    plan = next_plan.parse_plan(_closes_plan("Closes: R1, R2\n" + EXEMPT_LINES))
    lines = next_plan.render_brief(plan, 1).splitlines()
    at = lines.index("Mutation: required")
    assert lines[at + 1] == "Closes: R1, R2"
    assert lines[at + 2].startswith("Test-repo: ")
    assert lines[at + 3].startswith("Test: ")
    assert lines[at + 4] == "" and lines[at + 5] == "body"
    assert sum(1 for ln in lines if ln.startswith("Closes:")) == 1


def test_brief_adds_no_closes_line_to_a_step_without_one():
    plan = next_plan.parse_plan(_closes_plan("Closes: R1\n"))
    assert not any(ln.startswith("Closes:") for ln in next_plan.render_brief(plan, 2).splitlines())
    assert next_plan.render_brief(next_plan.parse_plan(CANON), 2) == BRIEF_STEP_2_PINNED


def test_cli_status_carries_closes(tmp_path):
    p = write(tmp_path, _closes_plan("Closes: R1, R2\n"))
    data = json.loads(run_cli("status", str(p)).stdout)
    one, two = data["steps"]
    assert one["closes"] == ["R1", "R2"]
    assert two["closes"] == []


# ---- adw 3.5.0: the Deletes and Guard-mutation lines (spec items 5 and 7) ----------------

# Fixture shapes: the lines as the spec's items 5 and 7 word them (docs/adw-3.5.0-spec.md),
# the Deletes paths in the shape of this repo's test files.
GUARD_LINE = "Guard-mutation: src/app.py: return x => return x + 1\n"


def test_parse_exposes_deletes_as_a_list_and_the_guard_mutation_as_a_triple():
    plan = next_plan.parse_plan(
        _closes_plan("Deletes: tests/test_old.py, tests/test_b.py\n" + GUARD_LINE)
    )
    one, two = plan.steps
    assert one.deletes == ["tests/test_old.py", "tests/test_b.py"]
    assert one.guard_mutation == ("src/app.py", "return x", "return x + 1")
    assert two.deletes == []
    assert two.guard_mutation is None


def test_parse_splits_the_guard_mutation_on_the_first_arrow_and_the_first_colon():
    plan = next_plan.parse_plan(_closes_plan("Guard-mutation: src/app.py: a: b => c => d\n"))
    assert plan.steps[0].guard_mutation == ("src/app.py", "a: b", "c => d")


def test_parse_keeps_an_empty_replacement():
    plan = next_plan.parse_plan(_closes_plan("Guard-mutation: src/app.py: x + 1 => \n"))
    assert plan.steps[0].guard_mutation == ("src/app.py", "x + 1", "")


def test_validate_accepts_the_deletes_and_guard_mutation_lines():
    text = _closes_plan("Deletes: tests/test_old.py, tests/test_b.py\n" + GUARD_LINE)
    assert _messages(text) == []


def test_validate_refuses_an_empty_deletes_line():
    for line in ("Deletes:\n", "Deletes:   \n", "Deletes: , \n"):
        assert _messages(_closes_plan(line)) == ["Step 1: Deletes: is empty"], line


@pytest.mark.parametrize(
    "path", ["/tmp/test_old.py", "../test_old.py", "tests/../../test_old.py", "tests/.."]
)
def test_validate_refuses_a_deletes_path_outside_the_repository(path):
    assert _messages(_closes_plan(f"Deletes: tests/test_b.py, {path}\n")) == [
        f"Step 1: Deletes: {path} is not a relative path in the repository"
    ]


def test_validate_accepts_a_deletes_path_with_two_dots_inside_a_name():
    assert _messages(_closes_plan("Deletes: tests/test_a..b.py\n")) == []


@pytest.mark.parametrize(
    "line",
    [
        "Guard-mutation:\n",
        "Guard-mutation: src/app.py return x => return x + 1\n",
        "Guard-mutation: src/app.py: return x -> return x + 1\n",
        "Guard-mutation: src/app.py: return x=>return x + 1\n",
        "Guard-mutation: : return x => return x + 1\n",
        "Guard-mutation: src/app.py:  => return x + 1\n",
    ],
)
def test_validate_refuses_a_guard_mutation_without_its_shape(line):
    assert _messages(_closes_plan(line)) == [
        "Step 1: Guard-mutation: needs <path>: <old> => <new>"
    ], line


def test_validate_refuses_a_second_deletes_or_guard_mutation_line():
    text = _closes_plan("Deletes: tests/test_a.py\nDeletes: tests/test_b.py\n")
    assert _messages(text) == ["Step 1: more than one Deletes: line (2)"]
    text = _closes_plan(GUARD_LINE + GUARD_LINE)
    assert _messages(text) == ["Step 1: more than one Guard-mutation: line (2)"]


def test_brief_keeps_deletes_and_guard_mutation_after_mutation_and_strips_them_from_the_prose():
    plan = next_plan.parse_plan(
        _closes_plan("Closes: R1\nDeletes: tests/test_old.py,tests/test_b.py\n" + GUARD_LINE)
    )
    lines = next_plan.render_brief(plan, 1).splitlines()
    at = lines.index("Mutation: required")
    assert lines[at + 1 : at + 4] == [
        "Closes: R1",
        "Deletes: tests/test_old.py, tests/test_b.py",
        "Guard-mutation: src/app.py: return x => return x + 1",
    ]
    assert lines[at + 4] == "" and lines[at + 5] == "body"
    assert sum(1 for ln in lines if ln.startswith("Deletes:")) == 1
    assert sum(1 for ln in lines if ln.startswith("Guard-mutation:")) == 1


def test_brief_adds_neither_line_to_a_step_without_them():
    plan = next_plan.parse_plan(_closes_plan("Deletes: tests/test_old.py\n" + GUARD_LINE))
    brief = next_plan.render_brief(plan, 2).splitlines()
    assert not any(ln.startswith(("Deletes:", "Guard-mutation:")) for ln in brief)


def test_cli_status_carries_deletes_and_the_guard_mutation(tmp_path):
    p = write(tmp_path, _closes_plan("Deletes: tests/test_old.py\n" + GUARD_LINE))
    data = json.loads(run_cli("status", str(p)).stdout)
    one, two = data["steps"]
    assert one["deletes"] == ["tests/test_old.py"]
    assert one["guard_mutation"] == {"path": "src/app.py", "old": "return x", "new": "return x + 1"}
    assert (two["deletes"], two["guard_mutation"]) == ([], None)


# ---- adw 3.5.2: the Size: step line (spec item 1, R1) -----------------------------------

# Fixture shapes: the line as docs/adw-3.5.2-size-spec.md item 1 words it, the value in the
# shape of this repository's own plan lines (`Size: 210`, prompt_plan.md at 438080c).


def test_parse_exposes_the_size_as_an_int_and_none_without_the_line():
    one, two = next_plan.parse_plan(_closes_plan("Size: 120\n")).steps
    assert one.size == 120
    assert two.size is None


def test_validate_accepts_a_size_line():
    assert _messages(_closes_plan("Size: 120\n")) == []


def test_validate_refuses_an_empty_size_line():
    for line in ("Size:\n", "Size:   \n"):
        assert _messages(_closes_plan(line)) == ["Step 1: Size: is empty"], line


@pytest.mark.parametrize("value", ["0", "-3", "many", "1.5", "120 lines", "+7"])
def test_validate_refuses_a_size_that_is_not_a_positive_line_count(value):
    assert _messages(_closes_plan(f"Size: {value}\n")) == [
        f"Step 1: Size: {value} is not a positive line count"
    ]
    assert next_plan.parse_plan(_closes_plan(f"Size: {value}\n")).steps[0].size is None


def test_validate_refuses_a_second_size_line():
    assert _messages(_closes_plan("Size: 120\nSize: 130\n")) == [
        "Step 1: more than one Size: line (2)"
    ]


def test_brief_keeps_the_size_line_after_mutation_and_strips_it_from_the_prose():
    plan = next_plan.parse_plan(_closes_plan("Size: 120\nCloses: R1\n" + EXEMPT_LINES))
    lines = next_plan.render_brief(plan, 1).splitlines()
    at = lines.index("Mutation: required")
    assert lines[at + 1 : at + 3] == ["Size: 120", "Closes: R1"]
    assert lines[at + 3].startswith("Test-repo: ")
    assert lines[at + 5] == "" and lines[at + 6] == "body"
    assert sum(1 for ln in lines if ln.startswith("Size:")) == 1


def test_brief_adds_no_size_line_to_a_step_without_one():
    plan = next_plan.parse_plan(_closes_plan("Size: 120\n"))
    assert not any(ln.startswith("Size:") for ln in next_plan.render_brief(plan, 2).splitlines())
    assert next_plan.render_brief(next_plan.parse_plan(CANON), 2) == BRIEF_STEP_2_PINNED


def test_cli_status_carries_the_size(tmp_path):
    p = write(tmp_path, _closes_plan("Size: 120\n"))
    data = json.loads(run_cli("status", str(p)).stdout)
    one, two = data["steps"]
    assert one["size"] == 120
    assert two["size"] is None


# ---- adw 3.5.2: the Step-size: Conventions key and the plan's limit (spec item 2, R2) ----

# Fixture shapes: the key as docs/adw-3.5.2-size-spec.md item 2 words it; the values the spec's
# Testing section names (450 accepted, 600 refused) and the run cap's edges.


def _with_step_size(text: str, value: str) -> str:
    """The plan with a Step-size line after Lint in its Conventions."""
    lint = "- Lint: uv run ruff check .\n"
    return _edited(text, lint, f"{lint}- Step-size: {value}\n")


def test_step_size_limit_is_the_default_without_the_key_and_the_key_with_it():
    assert next_plan.step_size_limit(next_plan.parse_plan(_closes_plan(""))) == 300
    plan = next_plan.parse_plan(_with_step_size(_closes_plan(""), "450"))
    assert next_plan.step_size_limit(plan) == 450
    assert "- Step-size: 450" in next_plan.render_brief(plan, 1)


@pytest.mark.parametrize("value", ["450", "300", "500"])
def test_validate_accepts_a_step_size_up_to_the_run_cap(value):
    assert _messages(_with_step_size(_closes_plan(""), value)) == []


@pytest.mark.parametrize("value", ["many", "0", "1.5"])
def test_validate_refuses_a_step_size_that_is_not_a_positive_line_count(value):
    text = _with_step_size(_closes_plan(""), value)
    lineno = text.splitlines().index(f"- Step-size: {value}") + 1
    assert next_plan.validate_text(text) == [
        (lineno, f"Conventions: Step-size: {value} is not a positive line count")
    ]


def test_validate_refuses_an_empty_step_size_as_it_refuses_an_empty_size():
    """Chunk A review (round 1, Important): the two empty values take the one `is empty`
    shape of the Closes: and Deletes: refusals (Decisions)."""
    for value in ("", "  "):
        text = _with_step_size(_closes_plan(""), value)
        lines = text.splitlines()
        lineno = next(i for i, ln in enumerate(lines, 1) if ln.startswith("- Step-size:"))
        assert next_plan.validate_text(text) == [
            (lineno, "Conventions: Step-size: is empty")
        ], repr(value)


@pytest.mark.parametrize("value", ["600", "501"])
def test_validate_refuses_a_step_size_over_the_run_cap(value):
    text = _with_step_size(_closes_plan(""), value)
    lineno = text.splitlines().index(f"- Step-size: {value}") + 1
    assert next_plan.validate_text(text) == [
        (lineno, f"Conventions: Step-size: {value} is over the 500-line run cap")
    ]


# ---- adw 3.5.2: validate refuses an estimate over the plan's gate (spec item 3, R3) -------


def test_validate_refuses_a_size_over_the_default_gate_and_accepts_one_at_it():
    assert _messages(_closes_plan("Size: 320\n")) == [
        "Step 1: Size: 320 is over the 300-line per-step cap; split the step"
    ]
    assert _messages(_closes_plan("Size: 301\n")) == [
        "Step 1: Size: 301 is over the 300-line per-step cap; split the step"
    ]
    assert _messages(_closes_plan("Size: 300\n")) == []


def test_validate_reads_the_gate_from_the_step_size_key():
    assert _messages(_with_step_size(_closes_plan("Size: 320\n"), "450")) == []
    assert _messages(_with_step_size(_closes_plan("Size: 451\n"), "450")) == [
        "Step 1: Size: 451 is over the 450-line per-step cap; split the step"
    ]


def test_validate_passes_a_step_with_no_size_line_whatever_the_gate():
    assert _messages(_with_step_size(_closes_plan(""), "1")) == []
    # a real plan validates through the CLI whatever its Size: lines say (Chunk A review, round 1,
    # Important)
    live = REPO / "evals" / "next-auto" / "fixtures" / "plans" / "happy-path.md"
    assert run_cli("validate", str(live)).returncode == 0


def test_validate_names_a_malformed_size_once_and_never_as_over_the_gate():
    assert _messages(_closes_plan("Size: many\n")) == [
        "Step 1: Size: many is not a positive line count"
    ]


# ---- adw 3.5.8: the Shares Conventions line (ai-dlc-record R14 amendment (4)) -------------

# Fixture shape: the classes are the five the amendment of 2026-10-30 names, rul-3c4db5a3fb.
def _with_shares(text: str, value: str) -> str:
    """The plan with a Shares line after Lint in its Conventions."""
    lint = "- Lint: uv run ruff check .\n"
    return _edited(text, lint, f"{lint}- Shares: {value}\n")


@pytest.mark.parametrize(
    "value", ["openapi", "dapi", "migration", "infra", "docs", "dapi, migration", "docs,infra"]
)
def test_validate_accepts_each_resource_class(value):
    text = _with_shares(_closes_plan(""), value)
    assert next_plan.validate_text(text) == []
    assert next_plan.plan_shares(next_plan.parse_plan(text)) == {
        c.strip() for c in value.split(",")
    }


@pytest.mark.parametrize(
    ("value", "word"), [("api", "api"), ("dapi, schema", "schema"), ("DAPI", "DAPI")]
)
def test_validate_refuses_an_unknown_resource_class(value, word):
    text = _with_shares(_closes_plan(""), value)
    lineno = text.splitlines().index(f"- Shares: {value}") + 1
    assert next_plan.validate_text(text) == [
        (
            lineno,
            f"Conventions: Shares: {word} is not a resource class "
            "(openapi, dapi, migration, infra, docs)",
        )
    ]


def test_validate_refuses_a_shares_line_with_no_class():
    text = _with_shares(_closes_plan(""), ", ")
    lineno = text.splitlines().index("- Shares: , ") + 1
    assert next_plan.validate_text(text) == [(lineno, "Conventions: Shares: names no class")]


def test_a_plan_with_no_shares_line_shares_nothing():
    assert next_plan.plan_shares(next_plan.parse_plan(_closes_plan(""))) == set()


# ---- the gate-config warning (carries spec R13, its 3(b); Step 12 of the 3.5.9 engine plan) --
# A step that says "the pyproject's dependency list" and never `pyproject.toml` passes
# validate today and fails at the gate, which authorizes a config edit only when the step text
# contains the basename. validate warns at plan time, never refuses.

PYPROJECT_WARNING = (
    "Step 2: names pyproject without its basename pyproject.toml; the gate authorizes a "
    "config edit only when the step text contains the basename"
)


def _plan_with_step_2_text(text):
    return CANON.replace("Build the engine on top of the parser.", text)


def test_validate_warnings_names_a_stem_without_its_basename():
    text = _plan_with_step_2_text("Add httpx to the pyproject's dependency list.")
    assert next_plan.validate_warnings(text) == [(20, PYPROJECT_WARNING)]


def test_validate_warnings_is_silent_when_the_basename_is_present_too():
    text = _plan_with_step_2_text(
        "Add httpx to the pyproject's dependency list in pyproject.toml."
    )
    assert next_plan.validate_warnings(text) == []


@pytest.mark.parametrize(
    "step_2_text",
    [
        "Run ruff check over the module.",
        "Add a conftest fixture to conftest.py for the fake origin.",
        "Clean up the toxic data path.",
        "Build the engine on top of the parser.",
    ],
)
def test_validate_warnings_ignores_prose_stems_and_whole_words(step_2_text):
    assert next_plan.validate_warnings(_plan_with_step_2_text(step_2_text)) == []


def test_validate_warnings_ignores_a_done_step():
    text = CANON.replace(
        "Write tests for the parser first, then implement it.",
        "Pin the version in the pyproject's dependency list.",
    )
    assert next_plan.validate_warnings(text) == []


def test_validate_cli_prints_the_warning_on_stderr_and_still_exits_0(tmp_path):
    text = _plan_with_step_2_text("Add httpx to the pyproject's dependency list.")
    p = write(tmp_path, text)
    r = run_cli("validate", str(p))
    assert r.returncode == 0, r.stderr
    assert r.stderr.splitlines() == [f"{p}:20: WARN {PYPROJECT_WARNING}"]
    assert next_plan.validate_text(text) == []


def test_validate_warnings_names_each_of_the_other_three_stems():
    """Chunk E review Minor, carried into Step 14: a positive case per remaining stem, so
    dropping one from GATE_CONFIG_STEMS or slicing its extension wrong fails a test."""
    cases = {
        "Add a conftest fixture for the fake origin.": "conftest without its basename conftest.py",
        "Add the ruff hook to the pre-commit-config.": (
            "pre-commit-config without its basename .pre-commit-config.yaml"
        ),
        "Run tox -e py312 before the commit.": "tox without its basename tox.ini",
    }
    for step_2_text, expected in cases.items():
        warnings = next_plan.validate_warnings(_plan_with_step_2_text(step_2_text))
        assert len(warnings) == 1, step_2_text
        assert warnings[0][1].startswith(f"Step 2: names {expected};"), warnings[0][1]


# ---- renumber (carries spec R15, Step 14 of the 3.5.9 engine plan) -------------------------------
# The project-b store was renumbered by ten hand scripts between 2026-09-21 and 2026-10-23, two
# of which swept the step bodies and not Decisions and Findings. renumber_text relabels the
# PENDING steps from a number up and every whole-word `Step <n>` reference to them, wherever
# it sits.

RENUMBER_PLAN = """\
# Widget Implementation Plan
Spec: spec.md. Owner: the owner. Written: 2026-09-11.

## Conventions binding every step
- Test: uv run pytest
- Lint: uv run ruff check .
- Run: uv run python -m widget

### Step 5: parser
Status: DONE (42 tests green; the split moved its tail to Step 7)
Eval: no
Mutation: required
Write tests for the parser first. Step 7 takes the tail.

### Step 6: lexer
Status: DONE (done)
Eval: no
Mutation: required
The lexer.

### Step 7: engine
Status: PENDING
Eval: no
Mutation: required
Build the engine on Step 6; Step 12 reads it. Step 70 is a typo kept on purpose.

### Step 8: judge
Status: PENDING
Eval: no
Mutation: required
Add the LLM judge after Step 7.

### Step 12: report
Status: PENDING
Eval: no
Mutation: required
Render the report from Step 7 and Step 8; Step 5 is untouched.

## Decisions
- Step 7: sqlite or json? Step 12 depends on it; Step 5 does not.

## Findings
- Minor: Step 12 and Step 70 share a word; Step 5 is done.
- Legacy step map: 3 -> 5, 4 -> 6
"""


def test_renumber_text_relabels_the_pending_steps_and_every_reference():
    out, _mapping, _counts = next_plan.renumber_text(RENUMBER_PLAN, start=7, by=1)
    plan = next_plan.parse_plan(out)
    assert [s.number for s in plan.steps] == [5, 6, 8, 9, 13]
    assert next_plan.validate_text(out) == []
    # a DONE step keeps its number, its Status note follows the moved step
    assert (
        "### Step 5: parser\nStatus: DONE (42 tests green; the split moved its tail to Step 8)"
    ) in out
    assert "Write tests for the parser first. Step 8 takes the tail." in out
    # bodies, Decisions and Findings
    assert "Build the engine on Step 6; Step 13 reads it. Step 70 is a typo kept on purpose." in out
    assert "Add the LLM judge after Step 8." in out
    assert "Render the report from Step 8 and Step 9; Step 5 is untouched." in out
    assert "- Step 8: sqlite or json? Step 13 depends on it; Step 5 does not." in out
    assert "- Minor: Step 13 and Step 70 share a word; Step 5 is done." in out
    assert "- Legacy step map: 3 -> 5, 4 -> 6" in out
    assert not re.search(r"Step (7|12)(?![0-9])", out)


def test_renumber_text_returns_the_map_and_the_per_section_counts():
    out, mapping, counts = next_plan.renumber_text(RENUMBER_PLAN, start=7, by=1)
    assert mapping == {7: 8, 8: 9, 12: 13}
    assert counts == {"headers": 3, "status": 1, "bodies": 5, "decisions": 2, "findings": 1}
    assert next_plan.unmapped_step_references(out, mapping) == [5, 6, 70]


@pytest.mark.parametrize("state", ["DONE (x)", "IN PROGRESS", "BLOCKED (y)", "FAILED (z)"])
def test_renumber_text_refuses_a_step_in_range_that_is_not_pending(state):
    text = RENUMBER_PLAN.replace(
        "### Step 8: judge\nStatus: PENDING", f"### Step 8: judge\nStatus: {state}"
    )
    with pytest.raises(next_plan.RenumberError) as exc:
        next_plan.renumber_text(text, start=7, by=1)
    assert "Step 8" in str(exc.value) and state.split(" (")[0] in str(exc.value)
    assert next_plan.unmapped_step_references(text, {}) == [5, 6, 7, 8, 12, 70]


@pytest.mark.parametrize(
    "by, words",
    [(0, ["by", "0"]), (-1, ["by", "-1", "positive"])],
)
def test_renumber_text_refuses_a_zero_and_a_negative_shift(by, words):
    """Step 19 (Chunk F review): the carries spec names positive shifts alone, an insertion or a
    split, so a negative one is refused before any collision could arise."""
    with pytest.raises(next_plan.RenumberError) as exc:
        next_plan.renumber_text(RENUMBER_PLAN, start=7, by=by)
    for w in words:
        assert w in str(exc.value), (w, str(exc.value))


def test_renumber_text_refuses_a_start_above_every_step(tmp_path):
    """Step 19 (Chunk F review): a typo in --from is a refusal, never an empty map written as a
    success with a dangling separator on the map line."""
    with pytest.raises(next_plan.RenumberError) as exc:
        next_plan.renumber_text(RENUMBER_PLAN, start=99, by=1)
    assert "99" in str(exc.value) and "no step" in str(exc.value)
    p = write(tmp_path, RENUMBER_PLAN)
    before = p.read_bytes()
    r = run_cli("renumber", str(p), "--from", "99", "--by", "1")
    assert r.returncode == 2 and r.stdout == "" and "no step" in r.stderr
    assert p.read_bytes() == before


PLURAL_PLAN = RENUMBER_PLAN.replace(
    "Render the report from Step 7 and Step 8; Step 5 is untouched.",
    "Render the report from Steps 7 and 8; Steps 7, 8 and 12 share the fixture; Steps 7 to 8"
    " run first; Steps 70 and 71 are typos kept on purpose; Step 5 is untouched.",
)


def test_renumber_text_rewrites_plural_step_references_number_by_number():
    """Step 19 (Chunk F review): `Steps 7 and 8`, `Steps 7, 8 and 12` and `Steps 7 to 8` are
    references too; a sweep that skipped them left the stale pointers the spec was written to
    end."""
    out, mapping, counts = next_plan.renumber_text(PLURAL_PLAN, start=7, by=1)
    assert (
        "Render the report from Steps 8 and 9; Steps 8, 9 and 13 share the fixture; Steps 8 to 9"
        " run first; Steps 70 and 71 are typos kept on purpose; Step 5 is untouched."
    ) in out
    assert counts["bodies"] == 5 - 2 + 7
    assert next_plan.unmapped_step_references(out, mapping) == [5, 6, 70, 71]
    assert next_plan.unmapped_step_references("Steps 70 and 71", {}) == [70, 71]


# ---- the renumber command (carries spec R15, Step 15 of the 3.5.9 engine plan) -----------------

SECTION_LINES = ["headers: 3", "status: 1", "bodies: 5", "decisions: 2", "findings: 1"]


def test_renumber_cli_rewrites_the_file_and_reports_per_section(tmp_path):
    """The file changes only where the map applies, in the file's own newline style; stdout
    carries one line per section with its count and one line naming the labels left alone."""
    out_text, _mapping, _counts = next_plan.renumber_text(RENUMBER_PLAN, start=7, by=1)
    expected = out_text.replace(
        "- Legacy step map: 3 -> 5, 4 -> 6\n",
        "- Legacy step map: 3 -> 5, 4 -> 6, 7 -> 8, 8 -> 9, 12 -> 13\n",
    )
    assert expected != out_text
    for newline in ("\n", "\r\n"):
        d = tmp_path / newline.encode().hex()
        d.mkdir()
        p = write(d, RENUMBER_PLAN, newline=newline)
        r = run_cli("renumber", str(p), "--from", "7", "--by", "1")
        assert r.returncode == 0, r.stderr
        assert p.read_bytes() == expected.replace("\n", newline).encode("utf-8")
        assert r.stdout.splitlines() == [
            f"renumbered {p}: 7 -> 8, 8 -> 9, 12 -> 13",
            *SECTION_LINES,
            "left alone: Step 5, Step 6, Step 70",
        ]
    assert not (Path(__file__).parent / ".tmp-renumber").exists()


def test_renumber_cli_creates_the_map_line_once_and_extends_it(tmp_path):
    p = write(tmp_path, RENUMBER_PLAN.replace("- Legacy step map: 3 -> 5, 4 -> 6\n", ""))
    assert "Legacy step map" not in p.read_text()
    r = run_cli("renumber", str(p), "--from", "7", "--by", "1")
    assert r.returncode == 0, r.stderr
    findings = p.read_text().split("## Findings\n", 1)[1]
    assert findings == (
        "- Minor: Step 13 and Step 70 share a word; Step 5 is done.\n"
        "- Legacy step map: 7 -> 8, 8 -> 9, 12 -> 13\n"
    )
    r = run_cli("renumber", str(p), "--from", "9", "--by", "1")
    assert r.returncode == 0, r.stderr
    findings = p.read_text().split("## Findings\n", 1)[1]
    assert findings == (
        "- Minor: Step 14 and Step 70 share a word; Step 5 is done.\n"
        "- Legacy step map: 7 -> 8, 8 -> 9, 12 -> 13, 9 -> 10, 13 -> 14\n"
    )
    assert p.read_text().count("Legacy step map") == 1
    assert [s.number for s in next_plan.parse_plan(p.read_text()).steps] == [5, 6, 8, 10, 14]


def test_renumber_cli_refusal_leaves_the_file_unchanged(tmp_path):
    p = write(tmp_path, RENUMBER_PLAN)
    before = p.read_bytes()
    r = run_cli("renumber", str(p), "--from", "6", "--by", "1")
    assert r.returncode == 2
    assert r.stdout == ""
    assert r.stderr.strip() == (
        f"{p}: Step 6 is DONE; a renumber moves PENDING steps only, and a DONE step's number "
        "never changes"
    )
    assert p.read_bytes() == before
    r = run_cli("renumber", str(p), "--from", "7", "--by", "0")
    assert r.returncode == 2 and "by is 0" in r.stderr
    assert p.read_bytes() == before


def test_renumber_cli_keeps_a_migrated_plans_map_entries_first(tmp_path):
    """A plan migrate_text relabelled carries the migration's map; a renumber extends that
    line after the existing entries."""
    migrated = next_plan.migrate_text(
        "# X Implementation Plan\n\nrules.\n\n"
        "## Step 11: a\nb mentions Step 20.\n\n## Step 20: c\nd\n\n## Step 18: e\nf\n"
    )
    assert next_plan.parse_plan(migrated).findings == "- Legacy step map: 11 -> 1, 20 -> 2, 18 -> 3"
    p = write(tmp_path, migrated)
    r = run_cli("renumber", str(p), "--from", "2", "--by", "1")
    assert r.returncode == 0, r.stderr
    plan = next_plan.parse_plan(p.read_text())
    assert plan.findings == "- Legacy step map: 11 -> 1, 20 -> 2, 18 -> 3, 2 -> 3, 3 -> 4"
    assert [s.number for s in plan.steps] == [1, 3, 4]
    assert "b mentions Step 20." in p.read_text()
