# Intents and traceability in adw

An intent is the outcome a piece of work serves, written down before the spec and carried by one id from the intent file through the spec and plan headers to the pull request trailer the plugin's reporting tools read.

Most work skips it. A feature that fits one spec needs no intent, and the three commands never ask for one: `/adw:spec` writes the id into the spec only when your request names it, `/adw:plan-tdd` copies it only when the spec has it, and no gate or validator reads the line. Write an intent when a piece of work spans more than one spec, or when a reader will ask "what was this for" after the pull requests are merged and the specs have been superseded. The id is free text without spaces; this page uses `30.1`, the same example the README uses.

## When an intent earns its file

The weight classes in `ai-dev-workflow.md` decide how much ceremony a piece of work gets, and the intent sits above all of them. Script and Feature work almost never wants one: the spec is the record, and the pull request's `Spec:` trailer already says what the change served. A System-class build that lands as several specs over weeks is the case for one: each spec answers part of the question, and the intent is the one file that states the whole outcome. The second case is a reader far from the work: an auditor, a new engineer, a person writing the release notes. For them `query intent 30.1` lists what landed against the outcome, and the intent file says what the outcome was.

## The intent file

The file is `docs/intents/<id>.md` in your repository, one page under 8 KB, with seven sections in this order:

- Problem and outcome: what is wrong or missing today, in the terms a user would use, and the state of the world when the work is done. Two paragraphs at most.
- Who it serves: the people and systems that benefit, named by role.
- In and out of scope: two short lists. The out list is the more useful one; it is where a later spec learns that something was left out on purpose.
- Acceptance scenarios: numbered S1 onward, each as Given, When, Then, each ending in a result a person can observe. A spec's requirements cite these by number.
- Rules by reference: the standing rules the work is bound by, cited by name and location, never restated. A rule copied into an intent drifts from its source.
- Dependencies as boundaries: what the work reads and does not change, so a spec knows which interfaces are its own and which belong to someone else.
- Open questions: each one either ruled, with the ruling, or carried with the event that will resolve it.

The file names no repository, no pull request table, no model and no size. Those belong to the spec and the plan, which change as the work proceeds, while the intent does not. A short example:

```markdown
# Intent 30.1: export a workspace to a file

## Problem and outcome

A workspace lives only in the service. A user who leaves, or who needs to
hand the data to another system, has no way to take it with them. When this
is done, a user downloads one archive that holds every document and its
history, and a second system can read it without the service.

## Who it serves

The account owner who is leaving; the integrator who loads the archive
elsewhere; the support engineer who reproduces a report from it.

## In and out of scope

In: the archive format, the download route, the job that builds it.
Out: import into this service; scheduled exports; per-document export.

## Acceptance scenarios

- S1. Given a workspace with documents and history, when the owner requests
  an export, then one archive is produced that holds every document and
  every version, and its manifest lists them.
- S2. Given an archive, when a reader opens it without the service, then
  each document and its history are readable from the files alone.

## Rules by reference

Data leaves the service only through an authenticated route (security
policy, section 4). Exports are logged as access events (audit policy).

## Dependencies as boundaries

Reads the document store and the version history; changes neither.

## Open questions

- Q1. Whether history older than the retention window is included.
  Resolving event: the retention ruling due before the first spec.
```

## The path of the id

The id travels through four carriers and one query. Each carrier below says whether a script reads it or a person does, because the difference decides what breaks when you leave one out.

### The spec header

When your `/adw:spec` request names an intent, by id or by the file's path under `docs/intents/`, the spec's header block, the lines between the title and the first heading, carries one line, `Intent: <id>`:

```text
Intent: 30.1
```

Only a person reads this line. When the request names no intent, the spec carries no such line, and the command does not ask.

### The plan header

`/adw:plan-tdd` copies the line verbatim into the plan header, directly under the `Spec:` line, exactly when the spec has one. The plan validator reads header lines as free text, so the line can never make a plan invalid. From 3.5.9 one script reads it: an unattended run's wrap-up, which copies it into the pull request trailer below. The command file `commands/plan-tdd.md` shows it in the grammar.

### The pull request trailer

A pull request body ends with trailer lines the reporting tools read: `Spec: <slug>`, `Closes: <requirement ids>`, `Intent: <intent ids>` and `Approved-by: @<login>`, several intents separated by commas. On an attended pull request you write the `Intent:` line yourself with the others. From 3.5.9, an unattended run's wrap-up writes it from the plan header, as it already writes `Spec:`, `Closes:` and `Approved-by:`. The projection reads the first line of each trailer, so a second `Intent:` line is ignored.

### The Now table

`docs/setup-plan.md` in your repository carries a `## Intents` section whose `### Now` subsection holds a Markdown table with an `Intent` column and a `Specs` column, one row per live intent, the Specs cell naming the specs that serve it. Those two columns are the only ones the projection requires; add others as you like. The table is the one list of live intents, and it is also the fallback the landed report uses when a pull request names a spec and no intent: the report fills the Intent cell from the row whose Specs cell names that spec. A team that works from a tracker can mirror this table into it, one project per Now intent with its units as the milestones and one issue per pull request, with the table as the source of record.

### The queries

`scripts/next_projection.py` builds a SQLite store from a checkout and answers typed questions from it; no query takes SQL. Outside a Claude Code session, pass `--project <dir>` to each command, since the default comes from `CLAUDE_PROJECT_DIR`:

```bash
python3 <installPath>/scripts/next_projection.py build --target <checkout> --project <dir>
python3 <installPath>/scripts/next_projection.py query intent 30.1 --project <dir>
python3 <installPath>/scripts/next_projection.py query coverage <capability-slug> --project <dir>
```

`query intent` lists the intent's merged pull requests with their deploy and its open ones in base-chain order. `query coverage` lists a capability's requirements and the pull requests that close them. `query entity`, `find`, `neighbors`, `path` and `timeline` walk the same store.

## Two published methods

Two published methods also put a written intent first. AWS's AI-DLC (`awslabs/aidlc-workflows`) runs five phases, Initialization, Ideation, Inception, Construction and Operation, with a gate at each phase boundary; an intent there is one run of the whole lifecycle scoped to a single task, recorded under `aidlc/spaces/<space>/intents/`, and traceability comes from attesting the content of each artifact rather than from commit trailers. Anthropic's Claude Academy course "The AI-native SDLC playbook" puts `intent.md` first, approved by a product owner, then `spec.md`, then `plan.md`, each stage committing an artifact the next stage reads, so the commit history is the audit trail. adw's intent sits where both of those put theirs, before the spec. It differs in three ways: it is optional, it carries one id through the spec and plan headers to a trailer a script reads, and it keeps no registry beyond the Now table.

## Useful links

- [Intents and traceability](../README.md#intents-and-traceability) in the README, the short form of this page.
- [Optional: Write the intent](../workflow-runbook.md#optional-write-the-intent) in the runbook, the steps.
- [Your first feature with adw](first-feature.md), the walkthrough whose pull request body shows the trailer lines.
- `commands/spec.md` and `commands/plan-tdd.md`, the command text that carries the header line: [spec](../commands/spec.md), [plan-tdd](../commands/plan-tdd.md).
- [AI development workflow](../ai-dev-workflow.md), the methodology, Phase 0.
