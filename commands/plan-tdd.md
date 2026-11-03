---
description: Read spec.md and write prompt_plan.md as a test-driven implementation plan in the plan grammar, validate it, and stop.
---

Read @spec.md and produce a test-driven implementation plan.

First, draft a blueprint: break the work into small iterative chunks that each build on the last. Then decompose each chunk into right-sized steps, small enough to implement safely with strong tests, large enough to move the project forward. Review the sizing once and adjust before writing output.

Then write `prompt_plan.md` to the project root, in the grammar below. `/adw:next` parses this file, and `/adw:next auto` refuses to run against a plan that does not validate.

## The grammar

```markdown
# <Project> Implementation Plan
Spec: spec.md. Owner: <name>. Written: <date>.

## Conventions binding every step
- Test: uv run pytest
- Lint: uv run ruff check .
- Run: uv run python -m <package>
- Eval: <command>
- Eval-codes: apparatus
- Holdout: evals/

## Chunk A: <title>

### Step 1: <title>
Status: PENDING
Eval: no
Mutation: required
Size: <n>
<the prompt: tests to write first, the implementation, explicit completion criteria>

## Chunk B: <title>

### Step 2: <title>
Status: PENDING
Eval: no
Mutation: n/a (pure config change, no behavior to mutate)
Size: <n>
<prompt>

## Decisions
(none)

## Findings
(none)
```

**Conventions.** `Test` and `Lint` are required and `Run` is recommended, matching the validator. For a Python project `Test` and `Lint` must start with `uv run`, so that steps run against the project's own environment rather than whatever interpreter is ambient. The engine runs `Test` and `Lint` without a shell, so each is one command: validate refuses `&&`, `|`, or `;` standing as an operator in either (outside quotes, or alone as a quoted or escaped argument), and a chain goes inside one quoted argument, as in `sh -c 'set -e; <cmd>; <cmd>'`, or `uv run sh -c '...'` in a Python plan. Add `Eval`, `Eval-codes`, and `Holdout` only when at least one step is `Eval: yes`; if any step is, all three are required. `Eval-codes` is either `apparatus` (0 pass, 1 fail, 2 or 3 apparatus breakage) or `exit0` (nonzero is a fail). `Holdout` names the directory of graded cases, which is never edited during a run. Add `Issue: <id>`, one tracker id such as `DOC-123`, when the plan's work is tracked by one issue: an auto run's branch then begins with the id, lowercased, and validate refuses any other shape. Add `Shares: <class>[, <class>]` when the plan's steps touch a resource another live plan could also be building, naming each class from `openapi`, `dapi`, `migration`, `infra` and `docs`: the agent registry OpenAPI document and its generated types, a `dapi` image rebuild, a database migration, `projects.yml` or `infra/`, and documents only. `check begin` and `start` refuse while another work tree's live plan shares a class with this one, `docs` never counts, and validate refuses any other word. Add `Toolchain-files: <globs>` only in a repository that keeps `CLAUDE.md` or files under `.claude/` under version control on purpose, naming the paths the plan's steps commit (`CLAUDE.md, .claude/**`): attended `check N` and `commit N` admit those paths, and auto mode refuses them, so such a plan runs attended.

**Chunks** are H2 groupings and the stop boundaries of the attended loop: `/adw:next` runs every step of a chunk back to back, dispatches a fresh-context reviewer against the chunk's diff at its end, and stops there for review. Place a boundary before any step in an always-ask class (credentials, security posture, data loss, core business logic, a schema migration), before a step whose result fixes an interface later steps depend on, and after every `Eval: yes` step. Never put more than five steps in a chunk; `validate` refuses a sixth. When chunks are used, every step sits under one, and `validate` refuses a step under a bare H2. A plan with no chunks runs to its last step in one batch.

**Steps** are H3, numbered from 1, strictly increasing. A DONE step's number never changes, because commit subjects, PR titles, and handoffs cite it. Retiring a step leaves its number behind, and new work at the end takes the next unused number. A split or an insertion ahead of PENDING work renumbers only the PENDING steps behind it, updates every cross-reference to them, and appends the old-to-new mapping to the `- Legacy step map:` line under Findings, creating that line on the first renumber.

Each step carries three metadata lines before its prompt, five under the Test-repo exemption below:

- `Status:` one of `PENDING`, `IN PROGRESS`, `DONE (<evidence>)`, `FAILED (<reason>)`, `BLOCKED (decision: <question>)`, `BLOCKED (apparatus: <reason>)`. Write every step as `PENDING`; execution rewrites this line in place.
- `Eval:` `yes` or `no`. `yes` means the step changes model behavior and must meet the eval threshold as well as the deterministic tests.
- `Mutation:` `required`, or `n/a (<reason>)`. Default to `required`. Use `n/a` only where there is no behavioral guard to mutate, such as a scaffold, a config file, or a docs-only step, and state which of those it is. The reason is read by both the executing agent and the reviewer, so a bare `n/a` is not acceptable.

A step whose every file lives in another repository (a hook in another repository's tree, a script in a config repo) carries two more lines after `Mutation:`, as a pair: `Test-repo: <label>` naming where its tests live, and `Test: <command>` naming the command that runs that repository's suite from this machine, for example `Test: ssh 203.0.113.10 'bash ~/dev/acme/.claude/hooks/session-events.test.sh'`. Write them only when every file the step touches lives in another repository; a step that builds code here uses the ordinary rule and no exemption. Under the Test-repo exemption `check N` runs that command as the step's test evidence in place of prove-red and the counts, the step may stage nothing here but the plan, the record and files under `docs/`, and its Completion names that repository's own test command. Validate refuses either line without the other and an empty value, and `/adw:next auto` refuses such a step, which runs attended.

`Size: <n>` is not optional: every step carries it after `Mutation:`, the estimate the Step size section below defines; `/adw:next auto` preflight refuses a plan with a `PENDING` or `IN PROGRESS` step that lacks it, naming the first, and the attended loop does not. Three more step lines are optional, each written once after `Mutation:` and after `Size:`: `Closes: <ids>` when the step closes requirement ids of the spec (`R1, R2`; an auto run's PR body lists the done steps' ids), `Deletes: <paths>` when the step deletes test files (relative paths, comma separated; the count check then measures the base without the named files, and a deletion the line does not name keeps the refusal), and `Guard-mutation: <path>: <old> => <new>` when the step's tests pass at its base because the base already has the behavior they guard (prove-red applies the replacement, which must occur once in that non-test file at the base, and requires the tests to fail then).

**Trailing sections.** End every plan with `## Decisions` and `## Findings`, each holding `(none)`. Execution appends to them: questions that need a human ruling go to Decisions, review findings that do not block go to Findings. They are the plan's outbox.

**The plan is the tracking artifact.** Steps are marked in place during execution. Do not emit a separate `todo.md`; duplicated state goes stale.

## Writing the step prompts

Each step's prompt:

- Begins with the tests to write first, named concretely, including the negative cases.
- Describes the implementation needed to make those tests pass.
- Ends with explicit completion criteria: tests pass, the program builds, the program runs.
- Builds on prior steps with no orphaned or unintegrated code.

Name the files a step is expected to touch. Gate and config files (`conftest.py`, `pytest.ini`, `.pre-commit-config.yaml`, `Makefile`, `CLAUDE.md`) are refused during a run unless the step's prompt names the file, so a step that legitimately needs to change one must say so.

## Step size

`/adw:next auto` refuses any step whose staged diff exceeds 300 changed lines, insertions plus deletions across every file, tests included. The gate runs after tests, mutation, and lint have passed, so an oversized step spends its whole run and lands nothing. That is why every step carries `Size:`, its estimated changed lines on that same count (`Size: 120`), written after `Mutation:`, and `validate` refuses a plan whose step estimate is over the gate: the plan is invalid, not merely oversized, so split before writing `Status: PENDING`. Size every step against the cap as you write it:

- Estimate about 30 changed lines per test the step mandates, plus 40 to 60 per named code surface (a function, a constant together with the code path that reads it, a CLI). Count every test the prose implies: an error case named in the implementation paragraph is a test. A new test module or a CLI costs more.
- A step that moves a function verbatim between modules costs about 2.5 times the function's lines: the removal, the addition, the retargeted imports and patch sites (six measured steps, 2026-10). A function above about 120 lines therefore does not move in one step under a 300 gate. Split the move at a seam, or raise the gate for that plan with `Step-size:` in Conventions (`- Step-size: 450`, in either direction up to the 500-line run cap) with the reason in the Conventions prose.
- Hold each step to at most five tests and two code surfaces. Split any step whose estimate passes 250 lines; the margin absorbs fixtures and formatting.
- Split along a seam that leaves each half testable on its own: a primitive, then its composition; a default mode, then its error paths. Never split by deferring the tests.

Measured on real steps: five tests landed at 194 changed lines, six at 283, seven at 422. Five factory nights failed the gate with every other check green, at 379, 422, 424, 471 and 749 changed lines, each landed by hand the next sitting. Every passing step's result file records its estimate beside its measured count; that pair is the data this rule is corrected from.

## Eval steps

If spec.md contains an Eval plan section, meaning the deliverable includes model behavior (agents, prompt pipelines, retrieval, classification), the plan must include eval-harness steps, and early: build the harness and a small graded case set before the behavior it grades, the same way tests precede implementation. Mark those behavior steps `Eval: yes` and set the eval threshold as a completion criterion alongside the deterministic tests. Deterministic tests gate the code; evals gate the behavior. A plan that ships an agent with only pytest green is incomplete.

## Brownfield

If spec.md is a change against existing code, first dispatch exploration to read the actual implementation so the plan is grounded in what the code really does, not assumptions. Surface any conflicts between spec.md and the current codebase before writing the plan.

## When a plan already exists

If `prompt_plan.md` already exists beside `spec.md`, read its Decisions and Findings sections before writing anything. They are the outbox of earlier runs: rulings the executing agent made, questions the owner answered, and review findings that were parked. Fold each entry into a step of the new plan or restate it verbatim in the new plan's Findings, name the retired step numbers so the old labels stay traceable, and never emit a plan that drops an entry. A migrated plan also carries a `Legacy step map` line in Findings; keep it.

## Before you finish

Validate the file you just wrote:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/next_plan.py" validate prompt_plan.md
```

Exit 0 means the grammar is right. Exit 2 prints line-numbered reasons; fix them and re-run. Do not hand over a plan that does not validate.

Do not begin implementing. Output the plan and stop.
