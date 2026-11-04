# Your first feature with adw

This page takes one feature from an idea to a pull request with the adw plugin, on a real repository, and shows the file each command produced.

adw takes one piece of work from an idea to a pull request in three commands, and every phase leaves a file in your repository. `/adw:spec` writes `spec.md`. `/adw:plan-tdd` writes `prompt_plan.md`. `/adw:next` writes commits, one per step of the plan. A person signs at each boundary: you commit the spec, you commit the plan, and you read a report at the end of each chunk of steps before the next chunk starts. The plugin enforces the rest in code. A hook holds the turn open until the tests pass, and a check between the agent and every commit proves that the step's new tests fail on the commit the step started from, so the test counts in a step's done note are the ones the check measured and not ones the model wrote.

This page walks that path once on a real repository and shows the files it produced, trimmed where they ran long. The repository was a Python package with one empty module and one smoke test. The feature was three string helpers. Everything below was typed into Claude Code with the plugin installed, and nothing was written by hand except the two commit commands, one corrected line in the spec, and the pull request body at the end.

## Before you begin

Install the plugin at user scope, pinned to a release tag:

```bash
claude plugin marketplace add ryanmat/adw@v3.5.8
claude plugin install adw@adw --scope user
```

You also need `python3` at 3.12 or later on your `PATH` (the plugin's scripts and hooks run on it, with no packages to install), `uv` for a Python project (the plan's test and lint commands start with `uv run`), and `gh` logged in for the pull request at the end. The unattended mode and its `ADW_OWNERS` setting are not needed for anything on this page.

## The spec

In the repository, in a Claude Code session, we typed one line:

```text
/adw:spec quick: fixturepkg is an empty Python package with one smoke test. Add three string helpers with no runtime dependencies: slugify(text), truncate(text, limit) that ends the cut text with an ellipsis, and word_count(text).
```

`quick:` is the no-interview mode. The default mode asks one question at a time until the picture is complete; `quick:` drafts the spec at once, lists every default it chose in an Assumptions table, and expects you to correct the wrong ones in your next message. It read `pyproject.toml`, the package file and the smoke test before writing, and it came back in 91 seconds. The file it wrote, trimmed:

```markdown
# fixturepkg String Helpers Specification

Owner: ryanmat. Written: 2026-11-04. Mode: quick.

Add three pure string helpers to `fixturepkg`: `slugify`, `truncate`, and `word_count`.
Each takes a `str`, returns a plain value, uses only the standard library, and holds no
state. This spec defines their exact contracts so that implementation and tests can be
written without further questions.

## Requirements

### Functional: `slugify(text: str) -> str`

Converts arbitrary text into a lowercase ASCII slug suitable for a URL path segment or
a filename stem.

| Input | Output |
| --- | --- |
| `"Hello, World!"` | `"hello-world"` |
| `"  Spaced  Out  "` | `"spaced-out"` |
| `"C++ & Rust"` | `"c-rust"` |
| `"2024_Q3_report"` | `"2024-q3-report"` |
| `""` | `""` |
| `"!!!"` | `""` |

The last two rows are the significant decision: text with no representable ASCII
alphanumerics yields the empty string rather than a fallback token or an error.

(...)

## Error handling

Input that is not a `str` raises `TypeError`. This includes `None`, which is not
silently coerced to `""`, because a `None` reaching a string helper is a caller bug and
surfacing it is more useful than returning a plausible empty result.

(...)

## Assumptions

These are the defaults chosen without asking. Correct any that are wrong and the spec
will be revised.

| Assumption | Chosen default |
| --- | --- |
| Module layout | New `src/fixturepkg/strings.py`, re-exported from `__init__.py` |
| Slug separator | Hyphen, not underscore, and not configurable |
| Unrepresentable slug input | Returns `""` rather than raising or substituting a fallback token |
| Ellipsis and the limit | Counts toward `limit`, so the result never exceeds `limit` |
| Invalid input policy | Raises `TypeError` and `ValueError` rather than coercing or returning a sentinel |
| Smoke test | Updated in the same change, since populating `__all__` necessarily breaks it |
```

The full file ran to about 1,400 words: a table of cases for each function, an Architecture section with the rejected alternatives, and a Testing plan that names the negative cases. The one thing it flagged on its own was that filling `__all__` would break the existing smoke test, and the spec says so rather than leaving the planner to discover it.

This is the first boundary. Read the Assumptions table, correct what is wrong in the next message, and when the file says what you mean, commit it. The one correction here was the `Owner:` line, which the draft had filled with a full name where the plan and the pull request use the GitHub login. Then:

```bash
git add spec.md && git commit -m "docs: add spec for the string helpers"
```

The session that plans and the session that builds read `spec.md` from disk, never the conversation that produced it, so a correction that lives only in the chat is lost.

## The plan

```text
/adw:plan-tdd
```

It read the spec and the repository, and after 102 seconds it had written `prompt_plan.md` and run the validator on it. The plan has six steps in three chunks, one chunk per function, with each function's transform and its input validation as separate steps so no step carries more than five tests. The file, trimmed to the first chunk:

```markdown
# fixturepkg String Helpers Implementation Plan
Spec: spec.md. Owner: ryanmat. Written: 2026-11-04.

## Conventions binding every step
- Test: uv run pytest
- Lint: uv run ruff check .
- Run: uv run python -c "import fixturepkg; print(fixturepkg.__all__)"

Steps 1 through 5 build and test `src/fixturepkg/strings.py` and import from
`fixturepkg.strings` directly. `src/fixturepkg/__init__.py` keeps `__all__ == []`, and
`tests/test_smoke.py` keeps passing, until Step 6 populates the export surface and
updates that assertion in the same step.

## Chunk A: slugify

### Step 1: slugify transform
Status: PENDING
Eval: no
Mutation: required
Size: 160

Write `tests/test_strings.py` first, a new file, importing `from fixturepkg.strings
import slugify`.

Start with `test_slugify_table`, a `pytest.mark.parametrize` case for every row of the
slugify table in spec.md Requirements, all ten pairs verbatim (...)

Then three tests that pin the risks the table alone does not isolate:
`test_slugify_collapses_separator_runs` (...); `test_slugify_strips_edge_hyphens` (...);
and `test_slugify_unrepresentable_returns_empty` (...).

Then create `src/fixturepkg/strings.py`, a new file. Give it a module-level
`_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")` compiled once at import, and
`slugify(text: str) -> str` with a full type annotation and a docstring stating the
contract. (...)

Input validation is Step 2's work. Do not add type checks here.

Completion: `uv run pytest` passes, `uv run ruff check .` is clean at the project's
100-column limit, the Run command still prints `[]`, and the new tests fail against an
empty `slugify` body before the implementation lands.

### Step 2: slugify input validation
Status: PENDING
Eval: no
Mutation: required
Size: 60

Add to `tests/test_strings.py` a `test_slugify_rejects_non_str` using
`pytest.raises(TypeError)` for `None`, for an `int`, and for `bytes` (...)

## Chunk B: truncate

(...)

## Chunk C: word_count and the package export surface

(...)

## Decisions
(none)

## Findings
(none)
```

The loop parses this shape, so a few words on it. The Conventions are the three commands the loop runs for every step; `Test` and `Lint` are required and have to be single commands with no shell operators. A chunk is an H2 group of at most five steps, and the loop stops at the end of each one. Every step opens with the tests to write, carries a `Size:` estimate of changed lines, and ends with its completion criteria. `Decisions` and `Findings` are the plan's outbox: a ruling the loop made, or a question only you can answer, is appended there during the run, so the plan file is also the record of the run. The grammar is in `commands/plan-tdd.md`, and `scripts/next_plan.py validate` is what accepts or refuses a plan.

This is the second boundary. Read the step sizes and the order, push back in the session if a step is too large or depends on something later, and commit:

```bash
git add prompt_plan.md && git commit -m "docs: implementation plan for the string helpers"
```

## The loop

```text
/adw:next
```

The first thing it printed was the batch line, which comes from the plan tool and not from the model:

```text
Running Steps 1 to 2, stopping at the end of Chunk A (the reviewer runs there).
```

Then it worked step 1. The sequence is the same for every step, and each line below is what a script printed:

```text
CHECK BEGIN step 1 base=6e71297c
```

`check begin` records the commit the step starts from and the test counts at that commit. The model then wrote `tests/test_strings.py` and an empty `slugify`, and ran the Test command:

```text
FAILED tests/test_strings.py::test_slugify_table[Hello, World!-hello-world]
FAILED tests/test_strings.py::test_slugify_table[  Spaced  Out  -spaced-out]
FAILED tests/test_strings.py::test_slugify_table[C++ & Rust-c-rust] - Asserti...
FAILED tests/test_strings.py::test_slugify_table[2024_Q3_report-2024-q3-report]
FAILED tests/test_strings.py::test_slugify_collapses_separator_runs - Asserti...
FAILED tests/test_strings.py::test_slugify_strips_edge_hyphens - AssertionErr...
9 failed, 5 passed in 0.02s
```

It said in one line why that red was the expected one (the transform is missing, not the import; the three rows whose expected output is the empty string pass trivially), implemented the function, and ran the tests again:

```text
14 passed in 0.01s
```

Then the mutation check: it broke the implementation one way (removed the edge-hyphen strip), saw five tests fail, restored it, broke it another way (removed the `+` quantifier so separator runs no longer collapse), saw five fail again, and restored it. A test that survives both mutations is not testing anything. Then lint, and then the check:

```text
CHECK PASS step 1
```

`check` reads the tree and the test runner and never the model's report. It stages the tree and runs the gate's rules against it: no staged secret, no staged toolchain file, no edit to a test-configuration file the step did not name, then prove-red (the step's new test file is applied to the base commit in a scratch worktree and the suite has to fail there), then the Test command, then the collected and skipped counts against the baseline so a step cannot reach green by removing tests, then Lint. A pass writes a result file with the numbers it measured. The step is then marked done and committed through the script:

```text
SET step 1 done
COMMIT 91ce56db5fb80464e52d731ec8b0920e5993dd59
```

`set 1 done` takes the model's note and appends the measured evidence after it, so the status line in the plan reads:

```text
Status: DONE (slugify table plus the three risk tests written first and red against a placeholder body; mutation killed both ways (strip removed, regex quantifier removed); CHECK PASS; check: 14 passed of 14 collected at base 6e71297c, lint ok, red proved)
```

A note whose own numbers disagree with the result file is refused, so the suite numbers in a plan are never typed. The commit is `feat(step-1): slugify transform`, and it carries the test file, the implementation, and the one changed line of the plan.

Step 2 ran the same way. Its red is worth a look: `unicodedata.normalize` already raises `TypeError` for a non-string, so a test that only checked the exception class would have passed before the guard existed. The tests assert the message names the parameter and the received type, and those three assertions were the red. Green at 17 passed, mutation killed both ways, `CHECK PASS step 2`, `COMMIT d4657acc`.

Step 2 was the last step of Chunk A, so the loop rendered a review package (the two commits, their stat and their diff from the batch base) and dispatched the reviewer agent against it with fresh context. The reviewer's verdict is recorded as a file:

```json
{
  "head": "d4657acc52c6bc42012b515fdbe5b4962eb2e001",
  "verdict": "Approved",
  "reviewer": "adw:next-reviewer",
  "model": "opus",
  "round": 1
}
```

Its report is kept beside it. It traced each table row through the implementation by hand, checked that no existing assertion in the repository had been rewritten, and raised nothing. Then the loop stopped and printed its report, trimmed:

```text
Chunk A is done and the tree is clean. Stopping at the boundary as the loop requires.

## Steps completed

Step 1 - slugify transform (91ce56d)

Wrote tests/test_strings.py first: test_slugify_table parametrized over all ten spec
rows verbatim, plus three risk tests. Red against a placeholder body: 9 failures, each
an assert '' == <expected slug>. Then src/fixturepkg/strings.py with a module-level
_NON_SLUG_RE and the NFKD-fold/lowercase/substitute/strip body. Green at 14 passed.
Mutation killed both ways: removing .strip("-") -> 5 failed; removing the regex +
quantifier -> 5 failed. CHECK PASS.

Step 2 - slugify input validation (d4657ac)

(...) Green at 17 passed. Mutation killed both ways (...). CHECK PASS.

## Reviewer

Step quality: Approved, no Critical, Important, or Minor findings. Verdict recorded at
verdict-d4657acc(...).json, round 1.

## Stop reason

End of Chunk A, the batch boundary. Next up is Step 3 (truncate transform) under
Chunk B, which begins only on a new /adw:next or your word. No decisions were recorded
and the Findings section is still empty.
```

Chunk A took 345 seconds. This is the third boundary, and the one you will see most often. Read the report, and if a number looks wrong, the files are there to check: the commits, `.next-auto/attended/result-1.json`, the review package and the reviewer's report. To go on, type `/adw:next` again (or tell the session to continue). The next batch line names Steps 3 to 4 and Chunk B.

Chunk B (truncate, steps 3 and 4) ran the same way in 363 seconds and ended Approved with one Minor finding: a parametrized test took an `expected` value it never used. The loop appended the finding to the plan's Findings section, which is the designed place for it, and then told us that nothing in the scripts commits the plan on its own at a boundary, so the plan was left modified and the next run's clean-tree check would have stopped there. We committed the plan by hand (`docs(plan): record the Chunk B review finding`) and typed `/adw:next` again. Chunk C (word_count and the package export, steps 5 and 6) took 373 seconds, ended Approved with one Important finding that the reviewer itself classed as plan-mandated (the smoke test's assertion was rewritten, as the spec said it would be), and this time the loop committed the plan itself. Either way the finding is in the plan file, where the next reader finds it; the engine needs one rule for that commit, and that is queued for the next release.

At the end of Chunk C every step was DONE, the tree was clean, and the suite stood at 48 tests. The three runs of the loop cost $2.45, $2.66 and $2.90 on Opus; with the spec ($0.63) and the plan ($0.53) the whole feature came to $9.17 and about 21 minutes of agent time, with a person reading at the five boundaries.

The loop can also stop before a boundary. It stops on a second red at the same step, on a failed check, on a question only you can answer (a design fork the spec does not settle, a keep-or-delete question, anything touching credentials or data loss), on a dirty tree at step start, and on a Critical reviewer finding that survives one fix round. Each stop is recorded in the plan with its reason, and `/adw:next` resumes at the same step once you have dealt with it. The whole procedure, including every stop, is `commands/next.md`.

## The pull request

Attended work ends as commits on your branch. Push it and open the pull request the way you would for any branch; the engine does not do this part for you in attended mode. The body ends with trailer lines that the plugin's reporting tools read later: the spec, the requirement ids the steps closed, who approved it, and, when you track one, the intent the work serves (this body carries the first three; `Intent:` is the fourth).

```bash
git push -u origin example/first-feature
gh pr create --title "feat(strings): add slugify, truncate and word_count" --body-file pr-body.md
```

The body we wrote, trimmed:

```markdown
## Summary
Adds three standard-library string helpers to `fixturepkg` (`slugify`, `truncate`,
`word_count`) and exports them from the package root. Built with the adw attended loop
from `spec.md` through `prompt_plan.md`: six steps in three chunks, one commit per step,
the failing tests written first at every step.

## Changes
- `src/fixturepkg/strings.py`: the three helpers, with `TypeError` on non-string input
  and `ValueError` on a `truncate` limit below 1.
- `src/fixturepkg/__init__.py`: re-exports the three names and populates `__all__`.
- `tests/test_strings.py`: 47 tests, the spec's tables plus the negative cases.
(...)

## Test plan
- `uv run pytest`: 48 passed at a9b9eac (1 smoke test plus 47 in `tests/test_strings.py`).
- `uv run ruff check .`: clean.
- Each step's `check` passed: prove-red against the step's base commit, test counts
  against the baseline, lint. Result files under `.next-auto/attended/`.
- Chunk reviews: three rounds, all `Approved`; one Minor and one Important finding, both
  recorded in the plan's Findings and judged plan-mandated or cosmetic.

Spec: spec.md
Closes: slugify, truncate, word_count contracts (the spec carries no requirement ids)
Approved-by: @ryanmat
```

The trailers are the part the tools read: `scripts/next_projection.py` builds a view of pull requests, specs and the requirement ids they close from these four lines, and an unattended run writes three of them itself at wrap-up. A spec with numbered requirements (`R1`, `R2`) gives the `Closes:` line ids to carry; this one had none, which is why that line is prose.

Merge it the way you merge any pull request; nothing in attended mode merges for you.

## What the plugin was doing while you watched

Four things ran in code during that session, and none of them was prompt text:

- A guard hook on every Bash, Edit and Write call denied any hook bypass (`--no-verify` and its relatives) and any edit under `.git/` or to `.pre-commit-config.yaml`. It arms further denials only during an unattended run.
- A Stop hook held each turn open while a step was in progress and the tree had changes beyond the plan, until the plan's Test command passed. A red Test blocks the turn with its tail; the fourth stop on one failure is allowed.
- The check between each step and its commit, described above, with prove-red against the base commit and the count comparison.
- The plan tool computed the batch, the next step and the status lines, so the numbers in the plan were measured rather than written.

## Summary

You have a `spec.md` and a `prompt_plan.md` committed in the repository, six commits named for the plan's steps, a plan whose status lines carry the measured test counts and whose Findings section carries the two review findings, and an open pull request with the four trailer lines. The three string helpers are implemented, exported and covered by 48 tests, and the whole run cost $9.17.

## Next steps

The command files are the behavior. `commands/spec.md`, `commands/plan-tdd.md` and `commands/next.md` are what the model reads when you type the commands, and when the loop does something this page did not prepare you for, the answer is in `commands/next.md`. The methodology behind the shape is `ai-dev-workflow.md`, and the moves without the reasoning are `workflow-runbook.md`. The unattended mode, which ends at a pull request a run cannot merge, is the `Optional: Run the loop unattended` task of the runbook together with the allow rules it needs. The README's Intents and traceability section covers the `Intent:` trailer and the `query intent` report that lists what has landed against an intent.
