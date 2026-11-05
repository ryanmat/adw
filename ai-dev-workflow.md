# AI development workflow

Claude Code owns spec through ship in the terminal, with deterministic gates (tests, lint, pre-commit) holding the line for code and graded evals holding it for model behavior.

Version: 3.5.10
Last revised: 2026-11-05

The IDE reviews and the browser verifies. This document is for the human. Do not load it into agent context as standing instructions. The only machine-facing pieces are the install blocks in the "Config placement" section.

History in brief. The workflow began as a spec, plan, execute loop run by hand across chat and terminal surfaces. Version 2 removed the separate todo list and added the eval phase and the weight-class floor. Then v3 moved every phase into Claude Code, gave each slash command exactly one source file, and added the unattended mode. The repository then shipped as the `adw` plugin, so the commands are namespaced (`/adw:spec`, `/adw:plan-tdd`, `/adw:next`), and the plan grammar, the guard, the Stop gate and the deterministic step gate moved from prompt text into code.

---

## Surfaces and their jobs

| Surface | Job | Do not use it for |
|---|---|---|
| Claude Code CLI in a terminal | `/adw:spec` interviews, Plan Mode against the repo, execution loop, commits, subagent review | Nothing categorical; this is the primary surface. Keep long divergent dialectic out of a session that will execute (start fresh from the spec file) |
| Claude Code desktop app | Session orchestration, parallel worktree sessions, visual diff review, checkpoint and rewind | Nothing categorical; same engine as the CLI |
| Chat (claude.ai or the desktop chat) | Ideation capture, notes from a phone, divergent conversation away from the terminal | Spec development (`/adw:spec` owns it), implementation, repo-grounded planning |
| VS Code with `/ide` from the terminal | Native diff review and diagnostics sharing while keeping the full CLI feature set | n/a, this is the default when VS Code is open |
| VS Code Claude Code extension | Selection-scoped @-mentions during IDE-native pair-programming loops | Anything `/ide` already covers |
| Chrome extension | Click-through verification of built web UIs, form flows, browser-side repro | Anything a test can assert instead |

The split keeps design dialectic (dead ends, rejected alternatives) out of the context the agent executes against. An agent then starts from a distilled spec, not a transcript. Enforcement is by artifact, not by surface: `/adw:spec` ends at a hard stop and emits spec.md, and execution reads the file, in a fresh session when the dialectic ran long.

## Weight classes

The full pipeline earns its cost only above a size floor. Applied uniformly, it gold-plates scripts or gets skipped inconsistently; a documented floor prevents both.

| Class | Size | Process |
|---|---|---|
| Script | Under about half a day | No ceremony. Hand-written two-paragraph spec.md if the repo warrants a record, or none at all. Execute directly. |
| Feature | Half a day to a few days | `/adw:spec quick: <idea>` in the repo (compressed spec, assumptions stated, no interview), then `/adw:plan-tdd` and `/adw:next`. |
| System | Multi-day, a deliverable for someone else, or anything with an eval surface | Full pipeline: `/adw:spec` interview, plan, gated loop, eval gate where model behavior is the product. |
| Pre-discovery | Work before a resolving event (a discovery call, a vendor answer, an access grant) | `/adw:spec v0: <idea + source pointers>`: constraint base from sources, Decision Register with defensible defaults, deliverables drafted to about 80 percent; bump v0 to v1 when the event resolves the rows. |

Work whose artifacts are themselves deliverables defaults to System class regardless of size.

---

## Phase 0: Intent (optional)

Above the spec sits the intent: the outcome a piece of work serves, written before any spec and carried by one id. Most work skips this phase, and Script and Feature class work almost always does. A change that fits one spec needs no intent, the three commands never ask for one, and no gate or validator reads it; write one when the work spans more than one spec, or when a reader will ask what the work was for after the pull requests are merged and the specs have been superseded.

The file is `docs/intents/<id>.md`, one page under 8 KB in seven sections (problem and outcome, who it serves, in and out of scope, acceptance scenarios, rules by reference, dependencies as boundaries, open questions), and it names no repository, model or size. Its id travels through the `Intent:` line of the spec and plan headers to the `Intent:` trailer on the pull request and the `## Intents` Now table of `docs/setup-plan.md`, where the projection reads it and `query intent` lists what has landed against the outcome. The shape, the carriers and the queries are in `docs/intent.md`; the moves are the runbook's optional task.

## Phase 1: Spec (Claude Code, `/adw:spec`)

Problem-space work. Decide what to build and why, in the repo, with connectors and research tools already mounted.

`/adw:spec` carries the standing contract in one versioned file (source: `commands/spec.md` in this repo). The prefix picks the mode. No prefix or `new:` runs the one-question-at-a-time interview (System class). `quick:` runs the compressed path, assumptions stated, no interview (Feature class). `v0:` runs the pre-discovery mode: a constraint base from named sources, a short judgment-call interview, and a Decision Register with defensible defaults. The interview covers goals, requirements, architecture tradeoffs, data handling, error handling and testing. When the deliverable includes model behavior it also covers the eval approach: what good output means, how it is graded, and the acceptance threshold.

Research policy: external synthesis (vendor and API landscape, compliance surface, competitive analysis, documents that need citations) runs as web research inside the session. Repo-grounded questions are Plan Mode's job, and quick factual checks need no research pass at all.

Divergent work (comparing architectures, deciding whether to build at all) stays conversational; `/adw:spec` is invoked only once direction settles.

Output: `spec.md` on disk in the target repo, committed. For model-behavior systems it contains an Eval plan section distinct from the testing plan. `/adw:spec` ends at a hard stop: no planning, no scaffolding, no implementation.

## Phase 2: Plan (the terminal)

`/adw:plan-tdd` in the repo turns `spec.md` into `prompt_plan.md`: ordered TDD implementation prompts in the plan grammar `scripts/next_plan.py` validates, each beginning with the tests to write and ending with explicit completion criteria. The plan is its own tracking artifact; steps are marked completed in place. There is no separate todo.md: duplicated state goes stale by step two and becomes a misleading status artifact.

Brownfield, or any repo with existing code: ground the plan against reality first. When the repo has code and no spec at all, the `adw:spec-miner` agent mines the baseline `spec.md` (Requirement and Invariant blocks anchored to the code that enforces them) before `/adw:plan-tdd` runs. The built-in exploration pass of `/adw:plan-tdd` reads the actual implementation for light changes; gnarly existing code gets an explicit Plan Mode pass pointed at `spec.md` first. A plan written without reading the code can hallucinate a codebase; a repo-grounded pass cannot. Surface conflicts between spec and code before the plan is written, not during execution.

If the spec has an Eval plan, the implementation plan includes eval-harness steps early: harness and graded case set before the behavior they grade, mirroring test-first.

Output: `prompt_plan.md`, committed.

## Phase 3: Execute (the terminal)

Invoke the loop with `/adw:next`. Per step:

1. Open prompt_plan.md, find the next prompt not marked completed.
2. Confirm it is truly unfinished (ask if uncertain; skip if already done).
3. Write the failing test first. Implement until tests pass and the program builds and runs. For model-behavior steps, run the eval set and meet the threshold before calling the step done.
4. Commit with a clear message. Pre-commit hooks must pass; never bypass with --no-verify.
5. Mark the prompt completed in prompt_plan.md.
6. Move to the next step of the same `## Chunk`. The loop runs to the chunk boundary (the end of the plan when unchunked) and stops there with a report: the steps done with their evidence, the reviewer's verdict, the stop reason. Review at the boundary, then say continue for the next chunk.

Supporting practices during the loop:

- TDD is the primary counter to hallucination and scope drift. Test and mock in one step, make the mock real in the next.
- Checkpoints: double-Esc or `/rewind` to fork or revert when a step goes sideways. Checkpoints cover Claude's edits, not yours or bash side effects; git remains the record.
- Subagent review at natural seams: "Use a subagent to review the diff against prompt_plan.md step N. Flag only gaps that affect correctness or stated requirements."
- Demand evidence, not assertions: test output, eval scores, the command run and what it returned.
- Diff review: if VS Code is open, run `/ide` once per session to get native side-by-side diffs and diagnostics sharing without leaving the CLI.

### Unattended mode: `/adw:next auto`

The interactive loop has a human at every chunk boundary and a deterministic `check` between each step and its commit. That is the right default, and it is also what keeps the loop from running while nobody is watching. `/adw:next auto` removes that trigger for work that is already decomposed, and pays for it with restrictions rather than trust.

It is hold-only. A run branches from local default into a worktree, commits per step, and ends at a labeled pull request; a run that commits no step ends without one, handing the plan back with its stop recorded. It never merges, and nothing reaches the default branch without a human. A run targets only a repository whose GitHub owner is in `ADW_OWNERS` and that is not a fork, which keeps an unattended agent away from every repository you did not name.

The enforcement is code, not instruction. A guard hook denies hook bypasses everywhere and, while a run is live, denies merges, force pushes, and the destructive git verbs. Between the implementer and the commit, a deterministic gate runs. It re-applies the step's new tests to the commit the step started from and requires them to fail there, so test-first is proven rather than claimed. The same gate refuses a staged secret, a toolchain file, or a change to the gate's own config the step never named, and compares collected, skipped, and xfailed counts against the baseline, so a step cannot reach green by shrinking the suite. Nor does the orchestrator fix review findings itself; a second failure on the same step stops the run.

The cost of a run is bounded by a step budget per invocation, a per-step diff cap, and a run-level diff cap. Before starting one, check remaining usage; an unattended loop that stops halfway through a step because the budget ran out leaves more mess than it saves.

Two contracts carry a run's rulings and findings forward. Each step's implementer receives a context file beside its brief: the plan's Decisions and Findings as they stand, plus a roster of the earlier steps with their states and notes. Step K therefore knows what steps 1 to K-1 decided and found. At wrap-up, a tracked plan's Status notes, Decisions, and Findings ride the branch, and the morning merge carries them into the next run. A plan kept untracked at the root is copied into the run's worktree at start and copied back after the PR exists. Every entry appended during a run carries a `[run <id>]` tag, so an agent's unreviewed ruling reads differently from one the owner wrote by hand; the morning read is where that outbox gets pruned. The PR body is the full run summary on a private repo and a terse one, with no step reports and no absolute paths, on a public one. Either body is scanned for secret-shaped content before anything is pushed.

Use it when the plan is genuinely decomposed and the steps are mechanical. Keep the interactive loop for anything where the shape of the answer is still in question. A step that needs judgment comes back as a blocked decision anyway, and that round trip costs more than doing it attended.

## Phase 4: Eval gate (model-behavior systems only)

Deterministic tests gate deterministic code. For systems whose paid-for correctness is probabilistic (Claude API applications, agents, retrieval, prompt pipelines), pytest green says nothing about it. The eval gate closes that hole.

What it consists of, at methodology level:

- A graded case set derived from the spec's Eval plan: representative inputs, a definition of good output, and a grader (programmatic assertion where possible, rubric or LLM-as-judge where not).
- A threshold agreed in the spec, not invented at ship time. The threshold is the acceptance criterion the work is signed against.
- The harness runs in the loop (per behavior-changing step) and in CI (per merge), the same way tests do. A regression in eval score blocks exactly like a failing test.
- The eval report is a deliverable, not an internal artifact: it is the evidence that the behavior exists and holds under change.

Tooling is deliberately unspecified here; the invariant is the gate, not the tool. Skip this phase entirely for systems with no model-behavior surface; do not skip it for anything where an LLM's output is the product.

## Phase 5: Verify and ship

- Pre-commit is the deterministic gate. The agent wants to commit; the hook forces lint, format, and hygiene to pass locally instead of clogging CI.
- Web UIs: use the Chrome extension to click through the built flow (forms, auth paths, error states) before calling a step done. Anything assertable belongs in a test instead.
- PR and CI as normal. Commit history keyed to prompt_plan steps is the audit trail; captured test output and eval reports are the evidence attached to it.
- Session close runs in a fixed order once the work is validated and pushed or merged. First the independent done check over the done claim, whose verdict record is what the merge gate reads. Then a retrospective into `docs/lessons.md` when the session received a correction. Next the committed Handoff entry in the plan document, pointer style: HEAD sha, commits landed, step states, open Decisions, the next move. Then the handoff file derived from that entry, with a final `## Kickoff` section naming repo, branch, HEAD sha, and the exact paste line for the next session. Last, the paste line printed as the session's last output. The kickoff line opens by checking `git log <sha>..HEAD` and the plan's `status`; commits past the sha mean the handoff is stale and the plan wins. With nothing open, the handoff file says "No active work" plus the sha and no line is printed. The handoff file is a derived local pointer; state the pipeline or another machine needs lives only in the plan file, `lessons.md`, and the committed Handoff.

---

## Config placement (what lives where, and the install blocks)

Principle: CLAUDE.md text is advisory; the model can ignore it under context pressure. Anything that must happen goes in a hook or pre-commit. Keep every agent-facing file short.

### 1. The `adw` plugin: slash commands

One home per contract: each command's source of truth is a standalone file under `commands/` in this repo, and the repo is itself the `adw` Claude Code plugin. The plugin system namespaces its commands, so every surface says `/adw:<name>`. No command text is embedded here.

| Command | Source (this repo) |
|---|---|
| `/adw:spec` | `commands/spec.md` |
| `/adw:plan-tdd` | `commands/plan-tdd.md` |
| `/adw:next` | `commands/next.md` |

Install at user scope, pinned to a release tag. The marketplace add carries the pin, because a plugin sourced from the marketplace's own root follows the marketplace's commit:

```bash
claude plugin marketplace add ryanmat/adw@v3.5.10
claude plugin install adw@adw --scope user
```

Verify no drift between machines by the installed record, which carries the git commit SHA of the tag (not the head of `main`); two machines agree when the SHAs match:

```bash
python3 -c 'import json,os;p=os.path.join(os.environ.get("CLAUDE_CONFIG_DIR",os.path.expanduser("~/.claude")),"plugins","installed_plugins.json");d=json.load(open(p))["plugins"];print(json.dumps({k:v for k,v in d.items() if k.startswith("adw@")},indent=1))'
```

Unattended runs read their owner allow-list from `ADW_OWNERS` in the `env` block of the user's settings; the README lists every variable.

### 1a. Scripts, hooks, agents, and skill for `/adw:next`

The commands are prose the model reads; these are the parts that execute. They ship in the same plugin, so one install carries all of them and nothing is copied by hand.

| Artifact | Path in the plugin | How it is reached |
|---|---|---|
| Plan grammar tool | `scripts/next_plan.py` | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/next_plan.py`, named in the command text |
| Auto-run orchestrator | `scripts/next_auto.py` | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/next_auto.py`, named in the command text |
| Guard hook | `hooks/next-guard.py` | registered by `hooks/hooks.json` |
| Stop gate | `hooks/stop-gate.py` | registered by `hooks/hooks.json` |
| Ruling hooks | `hooks/ruling-log.py`, `hooks/ruling-recall.py` | registered by `hooks/hooks.json`; active in a project with `.next-auto/rulings/` |
| Implementer agent | `agents/next-implementer.md` | dispatched as `adw:next-implementer` |
| Reviewer agent | `agents/next-reviewer.md` | dispatched as `adw:next-reviewer` |
| Spec-mining agent | `agents/spec-miner.md` | dispatched as `adw:spec-miner` |
| Markdown deliverables skill | `skills/markdown/SKILL.md` | loaded on task match |

The scripts are standard library only and run under any Python 3.12 or later, so plain `python3` works with no environment to sync. Under `PreToolUse`, the guard hook is registered on two matchers, `Bash` and `Edit|Write|MultiEdit`. It denies hook bypasses in every repo, all the time, and adds the merge and destructive-git denials only while a run-state file exists. Installing the plugin is the whole install; there is no per-repo step. A plugin cannot ship `permissions.deny` rules, so those stay in user settings, and in managed settings for a sandbox image.

### 2. Project CLAUDE.md block (per repo, keep the whole file short)

Commands shown for a Python/uv stack; swap the command lines per stack (biome for TS, clippy + rustfmt for Rust). Add an Eval line only when the repo has an eval harness.

```markdown
## Commands
- Test: uv run pytest
- Lint and format: uv run ruff check --fix . && uv run ruff format .
- Run: uv run python -m <package>

## Working agreement
- TDD: failing test before implementation. Mock first, make the mock real next step.
- Never commit with --no-verify. Pre-commit must pass.
- Definition of done: tests pass, lint clean, program runs, prompt_plan.md updated.
- Do not touch files outside the scope of the current prompt_plan step.
```

### 3. Pre-commit: `.pre-commit-config.yaml` (per repo)

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-merge-conflict
      - id: check-added-large-files
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.4
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

Install and pin to current (autoupdate supersedes the revs above, which drift):

```bash
uv tool install pre-commit
pre-commit install
pre-commit autoupdate
```

Keep tests and evals out of pre-commit (too slow per commit); the loop and CI own them. Add mypy or pyright here only if the project already type-checks clean.

### What does NOT get installed

- This document. It lives in this repo, never in a project tree. Never referenced from CLAUDE.md, never in standing agent context.
- The global CLAUDE.md carries exactly one line from this workflow, the weight-class routing rule: Feature-class and above requires a spec.md born from `/adw:spec` and goes through `/adw:plan-tdd` and `/adw:next`, with no ad-hoc implementation. Nothing else from this document goes there. It stays lean. The loop lives in the slash commands, conventions live per project, and enforcement lives in hooks and pre-commit.

---

## The workflow as delivery structure

The artifacts map one-to-one onto the stages a reviewer or a client wants to see:

| Stage | Artifact | What the reader sees |
|---|---|---|
| Inception | The intent each unit of work serves: the optional `docs/intents/<id>.md` file, the `Intent:` trailer on its pull request, and the `## Intents` Now table that maps live intents to their specs | Every change traces to a stated outcome, and `query intent` lists what has landed against it |
| Discovery | spec.md (interview-derived, rejected alternatives recorded) | The problem was understood before code existed |
| Architecture | prompt_plan.md, plus ADRs for load-bearing decisions | The build order and its reasoning are inspectable |
| Implementation | Commit history keyed to plan steps, captured test output | Every change is scoped, gated, and evidenced |
| Acceptance (AI behavior) | Eval report against the spec's agreed threshold | The probabilistic part of the system is measured, not vibed |

Evidence discipline (output over assertion) is what makes agent-built work defensible in review. It is baked into the loop, not the writeup.

Cost the phases separately when modeling a build. Spec and plan cycles are cheap and high-leverage; execution and eval loops dominate token spend, so phase separation is a cost-control lever as well as a quality one. Claude Code exposes session cost reporting and telemetry hooks. Verify the mechanisms against its docs before wiring per-phase capture or quoting numbers, because that surface changes and this document does not track it.

## References

- Harper Reed, "My LLM codegen workflow atm": https://harper.blog/2025/02/16/my-llm-codegen-workflow-atm/
- Harper Reed, "Basic Claude Code" (source of the loop, TDD, linting, pre-commit points): https://harper.blog/2025/05/08/basic-claude-code/
- Anthropic, Claude Code best practices: https://code.claude.com/docs/en/best-practices
- Anthropic, Effective context engineering for AI agents: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
