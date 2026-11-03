# Take an idea to a shipped pull request

This procedure takes one piece of work from an idea to a merged pull request with the adw plugin, so that every step is specified, planned, tested and reviewed before it ships.

Companion to `ai-dev-workflow.md`, which holds the reasoning; this file holds only the moves. Updated: 2026-11-03.

1. [Pick the weight class](#pick-the-weight-class)
2. [Write the spec](#write-the-spec)
3. [Commit the spec](#commit-the-spec)
4. [Ground the plan in existing code](#ground-the-plan-in-existing-code)
5. [Write the plan](#write-the-plan)
6. [Run the loop](#run-the-loop)
7. [Ship](#ship)
8. [Close the loop](#close-the-loop)

Two optional tasks sit beside these: set up a new repository, once per repository, and run the loop unattended for a plan that is already decomposed into mechanical steps.

## Before you begin

- Python 3.12 or later is on `PATH` as `python3`; the plugin's scripts are standard library only.
- Claude Code has the adw plugin installed at user scope, pinned to a release tag at version 3.5.8 or later, with the install block in the Config placement section of `ai-dev-workflow.md`.
- `gh` is authenticated as a user who can push to the target repository and open pull requests.
- The repository has pre-commit installed; see Optional: Set up a new repository.
- For unattended runs, `ADW_OWNERS` in the `env` block of the user's settings names the GitHub owners a run may target.
- A terminal is open in the target repository.

## Optional: Set up a new repository

Perform this task once, before the first spec in a repository that has no adw conventions yet.

To set up a repository:

1. Add the per-project `CLAUDE.md` from the template in `ai-dev-workflow.md`, Config placement block 2. Swap the three command lines per stack: uv and ruff for Python, biome for TS, clippy and rustfmt for Rust.
2. Add `.pre-commit-config.yaml` from block 3.
3. Install pre-commit:

   ```bash
   uv tool install pre-commit
   ```

4. Install its hooks in the repository:

   ```bash
   pre-commit install
   ```

5. Pin the hook revisions to current:

   ```bash
   pre-commit autoupdate
   ```

6. Confirm `.env` is in `.gitignore` before any secret exists.
7. In a repository with an eval harness, add the eval run command to the `CLAUDE.md` Commands section.

## Pick the weight class

Decide before opening anything; the class determines which tasks you run. Work whose artifacts are themselves deliverables defaults to System class regardless of size.

| Class | Size | Run |
|---|---|---|
| Script | Under about half a day | Skip to Write the plan with a hand-written two-paragraph spec.md, or skip the pipeline entirely |
| Feature | Half a day to a few days | Write the spec in `quick:` mode, then every task from Commit the spec on |
| System | Multi-day, a deliverable for someone else, or any model-behavior surface | Every task, full interview |
| Pre-discovery | Work before a resolving event | Write the spec in `v0:` mode and Commit the spec; the rest waits for the event or drafts against register defaults |

To pick the class:

1. Estimate the size and read the class off the table.

## Write the spec

Invoke `/adw:spec` only after the direction is settled. While you are still deciding between architectures, or whether to build at all, stay in conversation. The mode follows the class:

| Class | Command | What to expect |
|---|---|---|
| System | `/adw:spec <idea in 2-4 sentences>` or `/adw:spec new: ...` | An interview, one question at a time, that does not advance until each is resolved: goals, requirements, architecture tradeoffs, data, errors, testing, and for model-behavior systems the eval approach and threshold |
| Feature | `/adw:spec quick: <idea>` | No interview; a direct draft with its assumptions stated inline, which you correct in your next message |
| Pre-discovery | `/adw:spec v0: <idea> + <source pointers>` | A confidence-sorted constraint base, 3-6 judgment questions, and a Decision Register with defensible defaults |

To write the spec:

1. In the target repository, start `claude` and type the command for the class.
2. For external synthesis (vendor landscape, compliance, competitive analysis), ask for web research in the session. Keep repo-grounded questions for Plan Mode.
3. For a model-behavior system, confirm the emitted `spec.md` has an Eval plan section with a named threshold. `/adw:spec` ends at a hard stop with `spec.md` on disk.

## Commit the spec

`spec.md` on disk in the repository is the single durable interface: the session that plans and executes reads the file, not the conversation that produced it. Nothing else crosses.

To commit the spec:

1. Commit the file:

   ```bash
   git add spec.md && git commit -m "docs: add spec for <feature>"
   ```

## Ground the plan in existing code

Skip this task for a greenfield repository. For light brownfield changes, the built-in exploration pass of `/adw:plan-tdd` covers it and this task collapses into Write the plan. A plan written without reading the code can hallucinate a codebase; a repo-grounded pass cannot.

To ground the plan:

1. In a repository with code and no spec at all, dispatch the `adw:spec-miner` agent first. It writes the baseline `spec.md` from the code, and the steps below then ground that spec.
2. Start `claude` in the repository and press Shift+Tab into Plan Mode.
3. Point it at `spec.md` and let it explore the actual implementation.
4. Fold any conflict between spec and code back into `spec.md` and commit the amendment.

## Write the plan

There is no todo.md. The plan tracks itself; steps are marked completed in place.

To write the plan:

1. In the repository, run `/adw:plan-tdd`.
2. Review the emitted `prompt_plan.md` for step sizing, orphaned steps, and early eval-harness steps when the spec has an Eval plan. Push back in the session when it falls short.
3. Commit the plan:

   ```bash
   git add prompt_plan.md && git commit -m "docs: implementation plan for <feature>"
   ```

## Run the loop

Per step, the agent finds the next incomplete prompt, writes the failing test first, implements until tests pass and the build runs, meets the eval threshold when the step changes model behavior, runs `check`, commits with hooks passing, marks the step complete, and moves to the next step of the chunk. It stops early only on a listed stop: red after the second attempt, a failed `check`, BLOCKED, a genuine decision, a dirty tree, or a Critical reviewer finding that survives one fix round.

To run the loop:

1. Run `/adw:next`.
2. Watch for the batch line (`Running Steps 5 to 8, stopping at the end of Chunk B`); the loop then runs and stops at the end of the chunk.
3. At the chunk boundary, read the report before saying continue. The report carries the steps done with evidence, the reviewer verdict, and the stop reason.
4. Demand evidence: pasted test output and eval scores, not "tests pass."
5. If VS Code is open, run `/ide` once at session start for native diff review.
6. If a step goes sideways, press Esc twice or run `/rewind` to fork or revert. Checkpoints cover the agent's edits only; git remains the record for everything else.
7. If the same fix fails twice, stop and make the agent re-read the code and propose another approach.
8. At natural seams, ask for a subagent review:

   ```text
   Use a subagent to review the diff against prompt_plan.md step N. Flag only gaps that affect correctness or stated requirements.
   ```

9. Repeat until no incomplete prompt remains.

## Optional: Run the loop unattended

Perform this task only for a plan that is already decomposed into mechanical steps, in a repository whose owner is in `ADW_OWNERS`. The run is hold-only: it ends at a pull request and never merges. The rulings in its PR body are the part to read closely, because those are the judgment calls the agent made without you. To stop a live run, delete the run's `state.json`; that disarms the guard hook and the loop's next invocation finds no run.

To run the loop unattended:

1. Check `/usage` before starting. A run that dies mid-step on the usage limit leaves a half-finished worktree and a plan marked IN PROGRESS, which costs more to untangle than doing the step attended.
2. Commit the plan. The run tolerates an unpushed plan commit, but not a dirty tree.
3. Run `/adw:next auto`, or `/adw:next auto N` for N steps this invocation (the default is 3). Preflight refuses, with one line per reason, on an owner outside `ADW_OWNERS`, a fork, a dirty tree, a default branch behind origin, an existing run-state file, an open `next-auto` PR of the same unit, or a plan that does not validate.
4. Enter the `/goal` line it prints. That line re-prompts the loop after each turn ends; without it you get one invocation, not a loop.
5. Leave it alone and come back to the PR.
6. Review the PR body: steps done, failed and blocked, every ruling with its cost-if-wrong, and the findings. On a private repository the body also carries each step's report (the prove-red result, mutation evidence); on a public one those stay in `summary.md` under `.next-auto/<run-id>/`.
7. Prune the outbox: keep, fold, or delete each `[run <id>]` entry in Decisions and Findings before the next run reads them. A tracked plan carries them through the merge; an untracked plan was copied back to the root at wrap-up.
8. Merge through the gate, or push back. The run cannot merge, and the guard hook denies a merge attempt (`gh pr merge` and `next_auto.py merge` alike) for as long as the run-state file exists. Once the run is over and the PR is reviewed, the PR is merged by `python3 <installPath>/scripts/next_auto.py merge <n>` in its own Bash call: it re-runs Test and Lint at the PR head, requires a verdict record for that head and a preflight note on it, refuses a diff over 500 lines, a draft, a ruleset that still requires an approval, and any change on the security surface, then merges with `--squash --delete-branch`, never `--auto` and never `--admin`. The close-out writes the done-check verdict record (`.next-auto/attended/verdict-<sha>.json`, keys `head`, `verdict`, `reviewer`) before `merge` is attempted, so the independent done check's `APPROVED` over the done claim is the record the gate reads. A refusal, a repository the owner does not own, and every `--admin` merge are the owner's act by PR number; `--admin` appears in no command text.

## Allow rules for the attended loop in auto mode

In auto mode an allow rule resolves a call at once only when the whole command matches, and entering auto mode drops the broad rules: blanket `Bash(*)`, interpreter wildcards such as `Bash(python*)`, and package-manager run commands. The attended loop therefore calls each script by its full installed path, one git or gh operation per Bash call, never chained to a test run, a fetch, a checkout, or a pipe, and the rules below name those paths. `<installPath>` is the value `claude plugin list --json` reports for `adw@adw` on that machine, pasted verbatim; it changes at every re-pin, so re-read it after `claude plugin update`. The rules live in the user-scope `permissions.allow` list, never in a project's settings.

One rule per `next_auto.py` subcommand the attended loop uses:

- `Bash(python3 <installPath>/scripts/next_auto.py check:*)`
- `Bash(python3 <installPath>/scripts/next_auto.py commit:*)`
- `Bash(python3 <installPath>/scripts/next_auto.py review-package:*)`
- `Bash(python3 <installPath>/scripts/next_auto.py merge:*)`
- `Bash(python3 <installPath>/scripts/next_auto.py verdict:*)`
- `Bash(python3 <installPath>/scripts/next_auto.py record-commit:*)`
- `Bash(python3 <installPath>/scripts/next_auto.py pr-opened:*)`
- `Bash(python3 <installPath>/scripts/next_auto.py done-verdict:*)`

One per `next_plan.py` subcommand:

- `Bash(python3 <installPath>/scripts/next_plan.py validate:*)`
- `Bash(python3 <installPath>/scripts/next_plan.py next:*)`
- `Bash(python3 <installPath>/scripts/next_plan.py batch:*)`
- `Bash(python3 <installPath>/scripts/next_plan.py set:*)`
- `Bash(python3 <installPath>/scripts/next_plan.py append:*)`

The Conventions commands and the PR form wrap-up uses:

- `Bash(uv run pytest:*)`
- `Bash(uv run ruff check .)`
- `Bash(gh pr create --repo <owner>/:*)`

Memory, handoff, and any write under `~/.claude` go through the Write tool, never a Bash redirect: protected-path writes route to the classifier even when an allow rule matches.

## Failure modes and the rule that catches each

| Symptom | Rule |
|---|---|
| Agent wants --no-verify | Never. Fix the hook failure's cause. |
| Plan or decisions living only in chat | Everything durable goes to disk in the repo. |
| todo.md reappears | Delete it. prompt_plan.md is the single tracking artifact. |
| Plan written without reading the code | Ground it: Plan Mode or /adw:plan-tdd exploration before execution. |
| "Tests pass" with no output shown | Evidence, not assertion. Ask for the command and its output. |
| Agent-shipped model behavior with only pytest green | Eval gate applies. No threshold, no done. |
| Interview short-circuits into assumptions | Wrong mode: bare `/adw:spec` (or `new:`) runs the interview; `quick:` is the no-interview path by design. |
| `/adw:next auto` refuses and you cannot see why | It prints one line per reason. Read it. An owner outside `ADW_OWNERS`, a fork, a dirty tree, local default behind origin, a run already in progress, an open next-auto PR, or an invalid plan. |
| An auto run stops after one invocation | The `/goal` line was never entered. That line is the trigger; without it there is no loop. |
| A step fails the gate on prove-red | The implementation landed before the test, or the test passes against the base commit. Neither is test-first. Re-do the step, test first. |
| A step fails the gate on collected or skip counts | Something shrank the suite to reach green. Find what was deleted, skipped, or xfailed. |
| The installed plugin behaves unlike the source | Drift. Read the installed record's commit SHA (`~/.claude/plugins/installed_plugins.json`) against the tag you meant to be on; two machines report the same SHA. Editing a source without tagging and re-pinning is the usual cause, and it produces a run that contradicts the code you are reading. To run the working tree directly, load it with `claude --plugin-dir <repo>`. |
| A guard denial looks wrong | Read the rule it names. If the command only mentions a forbidden flag rather than using it, that is a guard defect worth fixing, not a reason to work around it. |
| Tempted to force past the guard | Never. The guard is the speed bump; branch protection is the wall. If both are in the way, the action is wrong. |
| The loop stopped mid-chunk | Not a defect: read the stop reason it printed (a failed `check`, a second red, BLOCKED, a decision, a dirty tree). Fix that, then `/adw:next` resumes at the same step. |

## Ship

To ship:

1. For a model-behavior system, run the full eval suite one final time and capture the report against the spec's threshold. The report is an acceptance deliverable.
2. For a web UI, click through the built flow with the Chrome extension: forms, auth paths, error states. Anything assertable belongs in a test instead; add it there.
3. Open the pull request and let CI run as normal. Commit history keyed to plan steps plus captured test and eval output is the audit trail.

## Close the loop

Run this task once the work is validated and pushed or merged, in this order.

To close the loop:

1. If the session produced corrections from you, confirm the pattern was logged to the repository's `docs/lessons.md` as a preventive rule.
2. If the work changed the methodology, update `ai-dev-workflow.md` and the command sources, tag a release, and re-pin each machine. Nothing changes on an installed machine until it re-pins.
3. Run the independent done check over the done claim. Its verdict record is what the merge gate reads.
4. When the session received a correction, run the retrospective into `docs/lessons.md`.
5. Commit the Handoff entry in the plan document, pointer style: HEAD sha, commits landed, step states, open Decisions, next move.
6. Derive the handoff file from that entry. Its final `## Kickoff` section names repo, branch, HEAD sha, and the exact paste line for the next session.
7. Print the paste line as the session's last output. The next session opens by checking `git log <sha>..HEAD` and the plan's `status`; commits past the sha mean the handoff is stale and the plan wins.
8. With nothing open, write "No active work" plus the sha in the handoff file and print no line.

The handoff file is a derived local pointer; state the pipeline or another machine needs lives only in the plan file, `lessons.md`, and the committed Handoff.

## Verify the shipped work

To verify the work shipped:

1. Read the plan's status and confirm every step is done:

   ```bash
   python3 <installPath>/scripts/next_plan.py status prompt_plan.md
   ```

   The JSON lists every step with its state, and no step is still open.

2. Confirm the pull request is merged and CI on the default branch is green.
3. Confirm the repository's `docs/lessons.md` carries any correction from the session and the plan document carries the committed Handoff entry.
