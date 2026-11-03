# Runbook: AI Development Workflow, Idea to Shipped

Purpose: the step-by-step operating procedure for the Claude Code-first workflow (spec through ship in the terminal). Companion to `ai-dev-workflow.md`, which holds the reasoning; this file holds only the moves.
Updated: 2026-11-02

## Step 0: Pick the weight class

Decide before opening anything. This determines which steps you run.

| Class | Size | Run |
|---|---|---|
| Script | Under about half a day | Skip to step 4 with a hand-written two-paragraph spec.md, or skip the pipeline entirely |
| Feature | Half a day to a few days | Steps 1 (`quick:` mode), 2 through 7 |
| System | Multi-day, a deliverable for someone else, or any model-behavior surface | All steps, full interview |
| Pre-discovery | Work before a resolving event | Steps 1 (`v0:` mode) and 2; the rest waits for the event or drafts against register defaults |

Work whose artifacts are themselves deliverables defaults to System class regardless of size.

## Step 1: Spec (Claude Code, `/adw:spec`)

1. In the target repo: `claude`, then type the mode:
   - System class: `/adw:spec <idea in 2-4 sentences>` (or `/adw:spec new: ...`). Answer the interview one question at a time; it will not advance until each is resolved. Expect coverage of goals, requirements, architecture tradeoffs, data, errors, testing, and (for model-behavior systems) the eval approach and threshold.
   - Feature class: `/adw:spec quick: <idea>`. No interview; it drafts directly and states its assumptions inline. Correct any wrong assumption in your next message.
   - Pre-discovery: `/adw:spec v0: <idea> + <source pointers>`. Expect a confidence-sorted constraint base, 3-6 judgment questions, and a Decision Register with defensible defaults.
2. If you are still deciding between architectures or whether to build at all, say so and stay in conversation. Do not invoke `/adw:spec` until the direction is settled.
3. If the decision needs external synthesis (vendor landscape, compliance, competitive analysis), ask for web research in-session; keep repo-grounded questions for Plan Mode.
4. For model-behavior systems, confirm the emitted spec.md has an Eval plan section with a named threshold before accepting it.

## Step 2: Commit the spec

```bash
git add spec.md && git commit -m "docs: add spec for <feature>"
```

spec.md on disk in the repo is the single durable interface: the session that plans and executes reads the file, not the conversation that produced it. Nothing else crosses.

## Step 3: Ground the plan (brownfield only)

Skip for greenfield. When the repo has code and no spec at all, dispatch the `adw:spec-miner` agent first; it writes the baseline `spec.md` from the code, and the steps below then ground that spec. For any repo with existing code that the spec touches non-trivially:

1. In the terminal, in the repo: `claude`, then Shift+Tab into Plan Mode.
2. Point it at `spec.md` and let it explore the actual implementation.
3. Fold any spec-vs-code conflicts back into `spec.md`, commit the amendment.

For light brownfield changes, `/adw:plan-tdd`'s built-in exploration pass covers this and step 3 collapses into step 4.

## Step 4: Plan (the terminal)

1. In the repo: `claude`, then `/adw:plan-tdd`.
2. Review the emitted `prompt_plan.md`: step sizing sane, no orphaned steps, eval-harness steps present and early if the spec has an Eval plan. Push back in-session if not.
3. Commit it:

```bash
git add prompt_plan.md && git commit -m "docs: implementation plan for <feature>"
```

There is no todo.md. The plan tracks itself; steps get marked completed in place.

## Step 5: Execute the loop (the terminal)

1. Run `/adw:next`.
2. The agent announces the batch in one line (`Running Steps 5 to 8, stopping at the end of Chunk B`), then per step: find the next incomplete prompt, write the failing test first, implement until tests pass and the build runs, meet the eval threshold if the step changes model behavior, run `check`, commit with hooks passing, mark the step complete, and move to the next step of the chunk. It stops at the end of the chunk (the plan, when unchunked), or earlier only on a listed stop: red after the second attempt, a failed `check`, BLOCKED, a genuine decision, a dirty tree, or a Critical reviewer finding that survives one fix round.
3. At the chunk boundary, read the report (steps done with evidence, the reviewer verdict, the stop reason) before saying continue. Demand evidence: pasted test output and eval scores, not "tests pass."
4. If VS Code is open, run `/ide` once at session start for native diff review.
5. If a step goes sideways: double-Esc or `/rewind` to fork or revert. Checkpoints cover the agent's edits only; git remains the record for everything else.
6. If the same fix fails twice, stop iterating. Make the agent re-read the relevant code top-down, state where its model was wrong, and propose a different approach.
7. At natural seams: "Use a subagent to review the diff against prompt_plan.md step N. Flag only gaps that affect correctness or stated requirements."
8. Repeat until no incomplete prompts remain.

## Step 5b: Unattended execution (`/adw:next auto`, optional)

Only for a plan that is already decomposed into mechanical steps, and only in a repository whose owner is in `ADW_OWNERS`. The run is hold-only: it ends at a pull request and never merges.

1. Pre-flight the budget. Check `/usage` first. A run that dies mid-step because the usage limit hit leaves a half-finished worktree and a plan marked IN PROGRESS, which costs more to untangle than doing the step attended.
2. Commit the plan. The run tolerates an unpushed plan commit, but not a dirty tree.
3. Run `/adw:next auto` (optionally `/adw:next auto N` for N steps this invocation; the default is 3). Preflight refuses, with one line, on an owner outside `ADW_OWNERS`, a fork, a dirty tree, a default branch behind origin, an existing run-state file, an open `next-auto` PR of the same unit, or a plan that does not validate.
4. Enter the `/goal` line it prints. That is the trigger that re-prompts the loop after each turn ends. Skip it and you get one invocation, not a loop.
5. Leave it alone. Come back to the PR.
6. Review the PR body: steps done, failed, and blocked; every ruling with its cost-if-wrong; and the findings. On a private repo the body also carries each step's report (the prove-red result, mutation evidence); on a public repo those stay in `summary.md` under `.next-auto/<run-id>/`. The rulings are the part to read closely, because those are the judgment calls the agent made without you.
7. Prune the outbox. Every Decisions and Findings entry appended during the run carries a `[run <id>]` tag; keep, fold, or delete each one before the next run reads them. A tracked plan carries them through the merge; an untracked plan was copied back to the root at wrap-up.
8. Merge through the gate, or push back. The run cannot merge, and the guard hook denies a merge attempt (`gh pr merge` and `next_auto.py merge` alike) for as long as the run-state file exists. Once the run is over and the PR is reviewed, the PR is merged by `python3 <installPath>/scripts/next_auto.py merge <n>` in its own Bash call: it re-runs Test and Lint at the PR head, requires a verdict record for that head and a preflight note on it, refuses a diff over 500 lines, a draft, a ruleset that still requires an approval, and any change on the security surface, then merges with `--squash --delete-branch`, never `--auto` and never `--admin`. The close-out writes the done-check verdict record (`.next-auto/attended/verdict-<sha>.json`, keys `head`, `verdict`, `reviewer`) before `merge` is attempted, so the independent done check's `APPROVED` over the done claim is the record the gate reads. A refusal, a repository the owner does not own, and every `--admin` merge are the owner's act by PR number; `--admin` appears in no command text.

To stop a live run: delete the run's `state.json`. That disarms the guard hook and the loop's next invocation finds no run.

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

## Step 6: Verify and ship

1. Model-behavior systems: run the full eval suite one final time; the report against the spec's threshold is an acceptance deliverable, capture it.
2. Web UIs: click through the built flow with the Chrome extension (forms, auth, error states). Anything assertable belongs in a test instead; add it there.
3. PR and CI as normal. Commit history keyed to plan steps plus captured test and eval output is the audit trail.

## Step 7: Close the loop

1. If the session produced corrections from you, confirm the pattern was logged to the repo's `docs/lessons.md` as a preventive rule.
2. If the work changed the methodology itself, update `ai-dev-workflow.md` in this repository and, if command behavior changed, the command sources under `commands/`; commit, tag a release, and re-pin each machine to the new tag with the install block in the methodology's Config placement section. Nothing changes on an installed machine until it re-pins.
3. Session close, in order, once the work is validated and pushed or merged: an independent done check over the done claim, whose verdict record is what the merge gate reads; a retrospective into `docs/lessons.md` when the session received a correction; the committed Handoff entry in the plan document, pointer style (HEAD sha, commits landed, step states, open Decisions, the next move); then the handoff file derived from that entry, with a final `## Kickoff` section naming repo, branch, HEAD sha, and the exact paste line for the next session; then the paste line printed as the session's last output. The kickoff line opens by checking `git log <sha>..HEAD` and the plan's `status`; commits past the sha mean the handoff is stale and the plan wins. With nothing open, the handoff file says "No active work" plus the sha and no line is printed. The handoff file is a derived local pointer; state the pipeline or another machine needs lives only in the plan file, `lessons.md`, and the committed Handoff.

## New repo setup (once per repo)

1. Drop the per-project `CLAUDE.md` (template in ai-dev-workflow.md, Config placement block 2; swap the three command lines per stack: uv/ruff for Python, biome for TS, clippy + rustfmt for Rust).
2. Add `.pre-commit-config.yaml` (block 3), then:

```bash
uv tool install pre-commit
pre-commit install
pre-commit autoupdate
```

3. Ensure `.env` is gitignored before any secret exists.
4. Repos with an eval harness: add the eval run command to the per-project CLAUDE.md Commands section.

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
| The installed plugin behaves unlike the source | Drift. Read the installed record's commit SHA (`~/.claude/plugins/installed_plugins.json`) against the tag you meant to be on; two machines should report the same SHA. Editing a source without tagging and re-pinning is the usual cause, and it produces a run that contradicts the code you are reading. To run the working tree directly, load it with `claude --plugin-dir <repo>`. |
| A guard denial looks wrong | Read the rule it names. If the command only mentions a forbidden flag rather than using it, that is a guard defect worth fixing, not a reason to work around it. |
| Tempted to force past the guard | Never. The guard is the speed bump; branch protection is the wall. If both are in the way, the action is wrong. |
| The loop stopped mid-chunk | Not a defect: read the stop reason it printed (a failed `check`, a second red, BLOCKED, a decision, a dirty tree). Fix that, then `/adw:next` resumes at the same step. |
