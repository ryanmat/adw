---
description: Produce a developer-ready spec.md from the request in interview (default), quick, or v0 mode, then stop; /adw:plan-tdd owns planning.
argument-hint: "[quick: | v0: | new:] <request>"
---

Produce a developer-ready spec from the request below. This is problem-space work: decide what to build and why, before any code. Do not plan implementation steps and do not implement; /adw:plan-tdd owns planning. When the spec is written, stop.

Request: $ARGUMENTS

Pick the mode from the request's prefix. No prefix means interview mode.

## Interview mode (default; also `new:`)

1. If the request names source material (files, Drive docs, Slack threads, emails), pull and extract constraints first; do not re-ask what the sources already answer. Interview only on the gaps.
2. Interview one question at a time (AskUserQuestion). Each question builds on the previous answer; do not advance until the current one is resolved. Cover: the goal in plain language, functional and non-functional requirements, architecture choices and their tradeoffs, data handling, error-handling strategy, and the testing approach. Ask about edge cases and rejected alternatives, not just the happy path.
3. If the deliverable includes model behavior (an agent, a prompt pipeline, retrieval, classification, anything where correctness is probabilistic), also cover the eval approach: what "good output" means, how it is graded (programmatic checks, rubric, LLM-as-judge), and the acceptance threshold. This becomes an "Eval plan" section in the spec, distinct from the testing plan.
4. When the picture is complete, write spec.md to the repo root: Overview, Requirements, Architecture (with reasoning and rejected alternatives noted briefly), Data handling, Error handling, Testing plan, and Eval plan when step 3 applies.

## Quick mode (`quick:`)

No interview, not even a short one. Write spec.md immediately: same structure compressed, plus an Assumptions section listing every default you chose (audience, scope, ordering, formats, dependencies). Pick defensible defaults instead of asking. If you have a standing objection (a simpler existing tool, a reason not to build), state it in one short paragraph before writing, then write the file anyway. After the file, ask at most one question, and only if an assumption is so load-bearing that a wrong guess invalidates the spec; otherwise ask nothing. The user corrects any wrong assumption and you revise.

## v0 mode (`v0:`), the pre-discovery spec

Constraints exist in sources, but key facts stay unresolved until a known future event (discovery call, vendor answer, access grant). Do not use this mode for mere ambiguity without such an event; that belongs in interview or quick mode.

1. Pull the named sources first. Sort extracted constraints by confidence: locked (stated facts and explicit guidance), provisional (proposed but unconfirmed), and unknown (the discovery gaps). Present this constraint base before asking anything.
2. Interview only on judgment calls the sources cannot answer: scope boundaries, defaults for the unknowns, acceptance bars. One question at a time, but expect 3-6 questions, not a full interview.
3. Every unknown becomes a row in a Decision Register: parameter, defensible default, the discovery question that resolves it, and the override impact (which spec sections change if it flips). Deliverables described by the spec are drafted against the defaults, built to roughly 80%, so discovery re-parameterizes rather than restarts.
4. Write spec.md marked v0: the standard structure plus the Decision Register and a Maintenance section that gives the downstream session the update procedure (resolve register rows, apply override impacts, bump to v1). Register-dependent sections must name their parameter so the diff surface is greppable.
5. Label epistemic status on defaults: which are evidence-based and which are inference. Never promote a provisional constraint to locked without a source.

## Shared rules

- Divergent work (comparing multiple architectures, or deciding whether to build at all) stays in conversation. If the request is divergent, say so and discuss; only write spec.md once the direction is settled.
- External synthesis (vendor or API landscape, compliance surface, competitive analysis): run web research in-session (WebSearch, deep-research subagents) before or during the interview. Repo-grounded questions are Plan Mode's job, not research.
- The spec follows the markdown deliverables house style (markdown skill).
- Live configuration is quoted, never recalled. Any requirement that cites a config value (a settings deny or allow rule, a settings key, a ruleset field, a hook registration, an env var name, an installed version) carries an inline anchor naming the file, the machine, and the date it was read in this session, for example `(read settings.local.json on the Mac, 2026-09-14)`. A value that cannot be read in the moment is written as unverified and listed under Decisions, not smoothed into an assertion.
- Hard stop: write spec.md to disk, report what was assumed versus asked, and stop. No prompt_plan.md, no scaffolding, no implementation.
- The intent line is optional. When the request names an intent (an id such as `30.1`, or a path under `docs/intents/`), the spec's header block, the lines between the title and the first H2, carries one line `Intent: <id>`; when the request names none, the spec carries no such line, and the command never asks for one in any mode.
