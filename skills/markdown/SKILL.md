---
name: markdown
description: Produce polished, standalone Markdown deliverables (.md files) in a clear-writing house style, lead with the answer, prose by default, tables and lists only when the content is genuinely list-shaped, no emojis, no em dashes, epistemic status labeled, and copy-paste ready with no placeholders. Use this skill whenever creating any Markdown document meant to be saved, shared, or handed off, including technical references, specifications (spec.md), implementation plans (prompt_plan.md), architecture decision records, comparison and analysis writeups, runbooks, and design docs, even when the user only says "write it up", "make a doc", "draft a spec", or "create a markdown file" without naming this skill. Do not use it for README or pull request bodies (the github skill owns those) or for Word, PDF, PowerPoint, or Excel deliverables (those skills own their formats).
---

# Markdown Deliverables

A Markdown file is a deliverable someone will act on, not a chat reply. Optimize every choice for one thing: the reader finding the answer and the load-bearing detail fast, then being able to copy the artifact and use it without editing.

This skill governs standalone `.md` files. It does not cover conversational replies (those follow normal response style), README or PR bodies (defer to the `github` skill), or Word, PDF, PowerPoint, or Excel outputs (defer to `docx`, `pdf`, `pptx`, `xlsx`).

## Workflow

1. Identify the document type (reference, spec, plan, decision record, analysis, runbook, or general). Pick the matching template below; if none fits, apply the house style to a plain structure.
2. Draft in the working directory.
3. Reread with fresh eyes against the house style and the anti-patterns list. Cut what is not pulling weight.
4. Write the final file to the destination the task calls for: the repo working directory in Claude Code, or `/mnt/user-data/outputs/` when running in a chat sandbox. Never paste a long document into chat and a file both; the file is the deliverable.

## House style

These are the load-bearing rules. They exist to serve the reader, not to satisfy a checklist, so apply the reasoning, not just the letter.

Lead with the answer. The first sentence resolves the question or states the recommendation. Context, caveats, and derivation follow. A reader who stops after one paragraph should already have the takeaway. Never restate the prompt back as an opening; the reader knows what they asked.

Prose by default. Write in paragraphs. Reach for a bulleted list or a table only when the content is genuinely list-shaped: discrete parallel items, or rows and columns that a reader will scan or compare. Prose that has been chopped into bullets to look organized is harder to read, not easier. When a list is right, each item earns its place and is a full thought, not a fragment.

No emojis. Anywhere. They read as unserious in a technical deliverable.

No em dashes. Use a comma, a colon, parentheses, or two sentences instead. This is a hard constraint; there is always a cleaner construction.

Label epistemic status when it matters. Distinguish established fact from widely held view from your own inference from genuine uncertainty, and say which is which in the text. A reader making a decision needs to know whether a claim is settled or is your read. Do not launder inference as fact, and do not hedge settled facts into mush.

Copy-paste ready, no placeholders. Commands, config, and code run as written. No `TODO`, no `<your-value-here>`, no "fill this in." If a value genuinely depends on the reader's environment, name the exact variable and show a concrete working default, then note what to swap.

Decimal, never float, for money. Any currency amount, financial calculation, or code snippet touching money uses a decimal type (for example Python `Decimal`), never a binary float. Floating-point rounding is a correctness bug in financial math, not a style preference.

Skip filler. No "I hope this helps", no throat-clearing preamble, no padded caveats. Say the thing, support it, stop.

## Structure conventions

Front matter for standing documents. A doc that will be revisited (reference, spec, plan, runbook, ADR) opens with a compact header: title as `# H1`, then owner, date, and a one-line summary of what the doc is and who it is for. Ephemeral one-off writeups do not need it.

Heading hierarchy, used shallowly. One `# H1` (the title). `## H2` for major sections, `### H3` sparingly for subsections. If you reach for `####`, the document probably wants to be split or flattened. Headings are navigation; a reader should be able to grasp the shape of the doc from the headings alone.

One idea per section. Short sections beat long ones. A section that runs many paragraphs is usually two sections.

Order by what the reader needs first. Recommendation or answer up top, supporting detail below, sources and appendices last.

## Formatting mechanics

Tables for genuinely tabular data: comparisons across a fixed set of criteria, option-by-attribute matrices, parameter specifications. Keep cells terse; a table cell holding three sentences wants to be prose. Do not use a table for a single column of items; that is a list.

Fenced code blocks with a language tag for every command, config file, and code sample, so it renders and highlights correctly and the reader can copy it cleanly. Use inline code for identifiers, file paths, flags, commands, and config keys named mid-sentence.

Links: use real, resolvable URLs with descriptive anchor text. No bare "click here". Put a source list at the end of research-derived or reference documents so claims are traceable.

Emphasis is rare. Bold is for the occasional genuinely critical term or a table's row label, not for decorating every other noun. Bold-heavy text reads as anxious and is harder to scan than clean prose. Italics for light emphasis or first-use of a term. Horizontal rules only to separate major top-level sections when it genuinely aids scanning, not between every heading.

## Document-type templates

Use these as starting structure, not a cage. Drop sections that do not apply; add ones the specific document needs.

### Technical reference

For durable, lookup-oriented domain material the reader will return to.

```markdown
# [Topic]

Scope: [what this covers and, if useful, what it deliberately excludes]
Updated: [date]

[One or two sentences orienting the reader: what this is and when to use it.]

## [Subtopic]
[Self-contained explanation. Procedures as numbered steps. Parameters, thresholds,
and specs in a table. Each subtopic readable without the others.]

## [Subtopic]
...

## Sources
- [Descriptive title](url)
```

### Specification (spec.md)

Problem-space definition, handed off to planning or implementation.

```markdown
# [Project] Specification

## Overview
[Goal, in plain language. What we are building and why.]

## Requirements
[Functional and non-functional. What it must do.]

## Architecture
[Chosen approach and the key decisions, with the reasoning. Note rejected alternatives
briefly so the choice is legible.]

## Data handling
[Shapes, storage, validation, lifecycle.]

## Error handling
[Failure modes and the intended behavior for each.]

## Testing plan
[How correctness is verified.]
```

### Implementation plan (prompt_plan.md)

Ordered prompts for a codegen agent to execute one at a time. The grammar is parsed by
`/adw:next`, so it is fixed: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/next_plan.py" validate prompt_plan.md` must exit 0.

```markdown
# [Project] Implementation Plan
Spec: spec.md. Owner: [name]. Written: [date].

## Conventions binding every step
- Test: uv run pytest
- Lint: uv run ruff check .
- Run: uv run python -m [package]
- Eval: [command]
- Eval-codes: apparatus
- Holdout: evals/

## Chunk A: [title]

### Step 1: [name]
Status: PENDING
Eval: no
Mutation: required
[Prompt for the agent. Begin with the tests to write first, named concretely, negative
cases included. Describe the implementation. Name the files the step may touch. End with
explicit completion criteria: tests pass, builds, runs.]

## Chunk B: [title]

### Step 2: [name]
Status: PENDING
Eval: no
Mutation: n/a (reason there is no behavioral guard to mutate)
...

## Decisions
(none)

## Findings
(none)
```

`Test`, `Lint`, and `Run` are required; Python projects prefix Test and Lint with `uv run`.
Add `Eval`, `Eval-codes`, and `Holdout` only when a step is `Eval: yes`, and then all three.
Chunks are H2 groupings and the stop boundaries of the attended loop: `/adw:next` runs a
chunk's steps back to back, reviews at its end, and stops there. Put a boundary before any
always-ask step (credentials, security posture, data loss, core business logic, a schema
migration), before a step that fixes an interface later steps depend on, and after every
`Eval: yes` step; never more than five steps in a chunk, and when chunks are used every step
sits under one. Steps are H3, numbered from 1 and strictly increasing. A DONE step's number
never changes, because commit subjects, PR titles, and handoffs cite it, and retiring a step
leaves its number behind. A split or an insertion ahead of PENDING work renumbers only the
PENDING steps behind it, updates every cross-reference to them, and appends the old-to-new
mapping to the `- Legacy step map:` line under Findings, creating that line on the first
renumber. The two trailing sections are the plan's outbox: execution appends human questions
to Decisions and non-blocking review findings to Findings.

The plan itself is the tracking artifact: the executing agent rewrites each step's `Status:`
line in place. Do not emit a parallel `todo.md`; duplicated state goes stale.

### Architecture decision record (ADR)

One decision, captured so a reader in six months understands why.

```markdown
# ADR NNN: [Decision]

Status: [proposed | accepted | superseded]
Date: [date]

## Context
[The forces at play, the constraint, what made this a decision.]

## Decision
[What was chosen, stated plainly.]

## Alternatives considered
[Each option and why it lost. Brief.]

## Consequences
[What this makes easier, what it makes harder, what it commits us to.]
```

### Analysis or comparison

A recommendation backed by evidence.

```markdown
# [Question or subject]

[Answer or recommendation, first sentence. The rest of this paragraph frames it.]

## Criteria
[What matters for this decision and why.]

[Comparison table if the options vary across a fixed set of attributes. Tradeoffs and
the reasoning in prose, labeling fact vs view vs inference.]

## Recommendation
[Restate the call and the conditions under which it would change.]

## Sources
- [Descriptive title](url)
```

### Runbook

An operational procedure someone runs under pressure.

```markdown
# Runbook: [Task]

Purpose: [what this accomplishes and when to run it]

## Prerequisites
[Access, tools, preconditions.]

## Procedure
1. [Step, with the exact command in a fenced block.]
2. ...

## Verification
[How to confirm it worked.]

## Rollback
[How to undo, if it did not.]

## Escalation
[Who or what to escalate to, and when.]
```

## Anti-patterns

Watch for these on the fresh-eyes reread and cut them:

- Restating the question as the opening line instead of answering it.
- Prose sliced into bullets that would read better as paragraphs.
- Header stacking: a heading immediately followed by another heading, or four-level nesting.
- Bold sprinkled across ordinary nouns until nothing stands out.
- Emojis, and em dashes.
- Placeholder tokens, `TODO`s, or "fill this in" left in a supposedly finished artifact.
- Hedging walls: stacked caveats that bury the actual claim. State the claim, note the one caveat that matters.
- A single-column "table" that is really a list, or a table cell holding a paragraph.

## File handling

Create the file at the destination the task calls for (the repo working directory in Claude Code; `/mnt/user-data/outputs/` in a chat sandbox), then present it. If the document is long, build it section by section and finalize in place. The user gets the file, not a wall of text in chat; a one or two sentence summary of what changed or what the doc contains is enough accompanying prose.
