<p align="center"><img src="https://github.com/ryanmat/adw/releases/download/v3.5.8/adw-logo.png" alt="ADW" width="720"></p>

The intent, spec, plan, run pipeline for Claude Code: a person writes the intent by hand, `/adw:spec` writes the spec, `/adw:plan-tdd` writes the test-driven plan, and `/adw:next` executes it in chunk-paced attended batches or unattended to a pull request, with deterministic checks on the code and graded evals on model behavior.

[![ci](https://github.com/ryanmat/adw/actions/workflows/ci.yml/badge.svg)](https://github.com/ryanmat/adw/actions/workflows/ci.yml) [![release](https://img.shields.io/github/v/release/ryanmat/adw)](https://github.com/ryanmat/adw/releases/latest) [![license](https://img.shields.io/github/license/ryanmat/adw)](LICENSE)

## Overview

adw is a Claude Code plugin and the methodology it implements. The first file is the intent, `docs/intents/<id>.md`, a page a person writes by hand that states the outcome the work serves; work that fits in one spec skips it, as [Intents and traceability in adw](docs/intent.md) says. Three slash commands then take the work from that outcome to a pull request, and every phase leaves a file in the repository: `/adw:spec` writes `spec.md`, `/adw:plan-tdd` writes `prompt_plan.md`, and `/adw:next` writes commits, one per step of the plan. A person signs at each boundary (the intent and spec commits, the plan commit, and the report at the end of each chunk of steps), and the plugin enforces the rest in code. A guard hook denies hook bypasses. A Stop hook holds a turn open until the plan's tests pass. A check between the agent and every commit proves that the step's new tests fail on the commit the step started from, refuses staged secrets and toolchain files, and compares the suite's collected and skipped counts against the baseline, so a step cannot reach green by shrinking the suite.

Start with [Your first feature with adw](docs/first-feature.md), which walks the three commands once on a real repository and shows the files they produced. The methodology is `ai-dev-workflow.md`; the step-by-step procedure is `workflow-runbook.md`. The command files under `commands/` are the source of truth for what each command does: they are the text the model reads when you type the command, so when the loop does something these documents did not prepare you for, the answer is in `commands/next.md`.

Nothing in the plugin is standing agent context; every piece is loaded by the plugin system on demand or read by a person. The repository root is the plugin and its own single-plugin marketplace, so git tags carry the versions and the installed record's commit SHA is the drift check.

## Setup

Python 3.12 or later on `PATH` as `python3` (the scripts and hooks are standard library and run on whichever `python3` Claude Code finds first; a lower version prints one line and the hooks stand down, except the guard hook, which keeps its denials from 3.9), `uv` for a Python project, and `gh` logged in for pull requests. Install at user scope, pinned to a release tag:

```bash
claude plugin marketplace add ryanmat/adw@v3.5.10
claude plugin install adw@adw --scope user
```

The attended loop needs no configuration. Installing the plugin turns four hooks on in every session, listed under Layout, `hooks/`. Two of the guard's denials apply everywhere, with or without a plan: a hook bypass on any git command, and an edit under `.git/` or to `.pre-commit-config.yaml`. The Stop hook runs only in a repository whose `prompt_plan.md` has a step in progress, and the rest arms only during an unattended run.

Unattended runs need an owner allow-list before they do anything. Set it in the `env` block of `~/.claude/settings.json`, as the GitHub owners whose repositories a run may target:

```json
{
  "env": {
    "ADW_OWNERS": "your-github-login,your-org"
  }
}
```

With no `ADW_OWNERS`, every preflight refuses.

Two machines agree when their installed records carry the same commit SHA. The record carries the tag's commit (v3.5.8 is 508911b), not the head of `main`, which moves with documentation commits between releases:

```bash
python3 -c 'import json,os;p=os.path.join(os.environ.get("CLAUDE_CONFIG_DIR",os.path.expanduser("~/.claude")),"plugins","installed_plugins.json");d=json.load(open(p))["plugins"];print(json.dumps({k:v for k,v in d.items() if k.startswith("adw@")},indent=1))'
```

Permission deny rules cannot ship in a plugin; they stay in user settings.

## Usage

In a Claude Code session in the target repository, one file you write and three commands, with a commit in your shell after the file and after each of the first two commands. The intent is optional: when the work fits in one spec, start at `/adw:spec`.

```text
docs/intents/<id>.md                   # by hand: the outcome the work serves, in the seven sections docs/intent.md lists
                                       # name the id in the spec request and the spec's header carries Intent: <id>
```

```bash
git add docs/intents/<id>.md && git commit -m "docs: add intent <id>"
```

```text
/adw:spec <idea in 2-4 sentences>      # interview mode; quick: and v0: are the other two
```

```bash
git add spec.md && git commit -m "docs: add spec for <feature>"
```

```text
/adw:plan-tdd                          # spec.md -> prompt_plan.md, validated
```

```bash
git add prompt_plan.md && git commit -m "docs: implementation plan for <feature>"
```

```text
/adw:next                              # runs to the end of the current chunk, then reports
```

`/adw:next` writes the failing test first, implements to green, runs the deterministic `check`, commits with hooks passing, marks the step done, and stops at the chunk boundary (a chunk is an H2 group of at most five plan steps) with the evidence and the reviewer's verdict. Type `/adw:next` again for the next chunk. Attended work ends as commits on your branch; you push and open the pull request. [Your first feature with adw](docs/first-feature.md) shows each of these steps with the files it produced.

For a plan that is already decomposed into mechanical steps, `/adw:next auto [N]` runs up to N steps unattended in a worktree and ends at a labeled pull request, and a run that commits no step ends without a pull request: the plan comes back with its stop recorded and nothing is pushed. It is hold-only: the run cannot merge, and a person reviews the pull request and merges it. The gated form, `python3 <installPath>/scripts/next_auto.py merge <n>`, re-runs Test and Lint at the head and requires a reviewer verdict record on it; with `ADW_PREFLIGHT_NOTE=required` in your settings it also requires a `refs/notes/preflight` note, which a pre-push check of your own writes, and without that setting it reads no notes. See the runbook's unattended task.

The plugin ships no scheduler. `/adw:next auto N` is one invocation, so a scheduler of your own (cron, a CI job, a runner on a machine you own) starts it as one `claude -p "/adw:next auto N"` under the CLI's `--max-budget-usd` cap and reads its stream. The lines a scheduler reads (`PREFLIGHT REFUSED`, `PREFLIGHT OK`, `RUN STARTED`, `RUN STOPPED <reason> PR #n`, `RUN STOPPED <reason> NO PR: no step committed`, `WRAP-UP FAILED`) are pinned by name in `tests/test_factory_parity.py`, so a release that changes one fails its own suite. Every run is hold-only, so a scheduler lands pull requests and never merges them.

The methodology is `ai-dev-workflow.md`; the moves without the reasoning are `workflow-runbook.md`.

## Intents and traceability

An intent sits above a spec: the outcome that several pieces of work serve together, carried by an id such as `30.1`. The engine never writes one. The id travels through four places, the intent file at `docs/intents/<id>.md`, the `Intent:` line in the spec and plan headers, the `Intent:` trailer on a pull request body, and the `## Intents` Now table of `docs/setup-plan.md`, and the projection reads the last two, so that `query intent` lists what has landed against each outcome. The file's shape, each carrier and the queries are in [Intents and traceability in adw](docs/intent.md).

## Layout

- `docs/first-feature.md` - the walkthrough: the three commands once on a real repository, with the spec, the plan, the loop's output, the reviewer verdict, the boundary report and the pull request body it produced.
- `docs/intent.md` - the optional intent above a spec: the seven-section file, the path of its id through the spec and plan headers to the pull request trailer and the Now table, and the `query intent` report.
- `ai-dev-workflow.md` - the methodology: surfaces and their jobs, weight classes, the phases from the optional intent through unattended execution and shipping, config placement.
- `workflow-runbook.md` - the step-by-step operating procedure; the moves without the reasoning.
- `.claude-plugin/` - `plugin.json` (name `adw`, version) and `marketplace.json` (one plugin, sourced from this root).
- `commands/` - `spec.md` (`/adw:spec`: interview, quick, and v0 pre-discovery modes), `plan-tdd.md` (`/adw:plan-tdd`), and `next.md` (`/adw:next`, the chunk-paced attended loop and `/adw:next auto`).
- `agents/` - `next-implementer.md` and `next-reviewer.md`, dispatched as `adw:next-implementer` and `adw:next-reviewer` by an unattended run; `spec-miner.md`, dispatched as `adw:spec-miner` by the operator to mine a brownfield codebase into the baseline `spec.md` that `/adw:plan-tdd` plans against.
- `hooks/` - `hooks.json` registers `next-guard.py` on `PreToolUse` for `Bash` and `Edit|Write|MultiEdit`: it denies hook bypasses everywhere, and merges, force pushes, and destructive git while a run is live; and `stop-gate.py` on `Stop`: while `prompt_plan.md` has a step in progress (or a live check record) and the tree has changes beyond the plan and the record, a turn ends only on a green Test run, and the fourth stop on one failure is allowed; and `ruling-log.py` on `PostToolUse` for `AskUserQuestion`: one line per answered question in the ruling store, `.next-auto/rulings/<session id>.jsonl` under the project dir, written only in a project where that directory already exists; and `ruling-recall.py` on `PreToolUse` for `AskUserQuestion`: the rulings of the last 48 hours that share a word with the new question, as the reason of a deny while any of them has not been shown to the session, so the model reads them before the person sees the question; the dialog asked again goes ahead.
- `scripts/next_plan.py` - the plan grammar: validate, migrate, status, next, brief, plan-hash, byte-preserving status and section edits, and the `ruling add` and `ruling recall` commands over `scripts/rulings.py`.
- `scripts/next_auto.py` - every git and gh operation of an unattended run, plus the deterministic gate, the attended `check` and `commit`, the merge gate, and the delivery record's call points for a plan whose Conventions carry `Record`.
- `scripts/next_projection.py` - the delivery projection: a SQLite view over records, specs, pull requests and their trailers, intents, rulings and ledgers, with its typed queries.
- `skills/markdown/SKILL.md` - the house-style Markdown deliverables skill; also uploadable to Claude Desktop via Settings > Features > Skills.
- `tests/` - the suite for the scripts, the hooks, the command grammar, and the plugin layout. Real git repos with a local bare origin, no mocks. Fixtures are synthetic: the plans and records under `tests/fixtures/` keep the structure of real ones with their free text regenerated.
- `evals/next-auto/` - scenario evals for unattended runs, graded on git and PR state rather than by a judge. They drive the real CLI against a fixture repository you own and record cost per scenario.
- `evals/lib/` - the git fixture builder and the eval ledger record the tests and the harness share.

## Configuration

Read from the environment, which for Claude Code means the `env` block of the user's settings.

| Variable | Default | Effect |
|---|---|---|
| `ADW_OWNERS` | unset | Comma-separated GitHub owners whose repositories `/adw:next auto` may target. Unset, preflight, start and merge refuse. |
| `ADW_NEVER_AUTO` | empty | Comma-separated `owner/repo` slugs that are never an unattended target, refused before any network call. |
| `ADW_PREFLIGHT_NOTE` | unset | Set to `required` to make `merge` refuse a head without a `refs/notes/preflight` note; unset, the gate does not read notes. |
| `ADW_DONE_REVIEWER` | `done-check` | The reviewer name a done-check verdict record carries; `done-verdict` writes it and `merge` accepts it in place of the chunk reviewer's `Approved`. |
| `ADW_EVAL_FIXTURE_REPO` | unset | The `owner/repo` the eval harness resets and drives; its `baseline` tag holds `evals/next-auto/fixtures/baseline/`. Evals only. |

## Working on the code

```bash
uv sync --locked                            # .python-version names 3.14; uv fetches it if absent, and 3.12 is the floor
uv run pytest                               # scripts, hooks, the docs-grammar guards, and the plugin layout; about seven minutes
uv run ruff check .
uv run --isolated --python 3.12 pytest -q   # the floor the scripts run on
claude plugin validate --strict .
claude --plugin-dir .                       # load the working tree as the plugin without installing it
```

Editing a source changes nothing on an installed machine until a new tag is cut and the machine re-pins to it; a working tree loaded with `--plugin-dir` runs the source directly.

Eval runs need `gh` authenticated as a user who can push to the fixture repository, and they cost real money:

```bash
export ADW_EVAL_FIXTURE_REPO=<owner>/<repo>
python3 evals/next-auto/run_scenario.py --list
python3 evals/next-auto/run_scenario.py refuse-foreign-owner    # the cheap one
python3 evals/next-auto/run_scenario.py                         # all scenarios
```

They reset the fixture repository to its `baseline` tag, drive the real Claude Code CLI against it with the working tree loaded as the plugin and the scenario's cost cap as the CLI's hard budget, and write per-scenario results with `total_cost_usd` to `evals/next-auto/results/`. A lock file serializes them, because they share one remote.

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
