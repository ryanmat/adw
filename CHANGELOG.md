# Changelog

Each entry describes one release of the plugin; a release entry lists what it removes under a bullet that begins "Removed:", one item per surface.

## 3.5.11 (2026-11-06)

Curated from the maintainer's development repository after the engine merge b4b84f6, plus this release's version and documentation edits.

The items the 3.5.10 release left open, two of them filed as issues here:

- `set N done` exempts a comment-only fix round of a `Mutation: required` step from the mutation-killed claim when the check record carries `red_reason: comment-only fix round`, and `commit N` records `mutation: n/a` for that round; the refusal on a required step names the step's mutation controls and their re-run on a fix round (#7).
- `validate` names a near-miss trailing header in the section's place (`missing section: '## Decisions' (found '## Decisions open' at line N; rename it)`), and `migrate` renames the first near miss of each absent section with a `.bak` beside the plan, refusing one that stands beside the section's own header (#8).
- `set N <state>` on a step with a build record keeps it in every state but done: `; record: <note>` after a wrapped state's own note, or `(record: <note>)` alone on in-progress and pending; a note that itself holds `record: ` is refused.
- `check N` ends `check-N.log` with a stamp line naming HEAD, the base, the Test exit, the counts and the time.
- `commands/next.md`: the mutation-check item says to delete `__pycache__` after a same-size mutant, the boundary paragraph says the `.orig.md` copy is written when the verdict filter dropped a finding, and the stop and on-red sentences say the record is kept.
- The changelog pin in the maintainer's tests anchors at the first heading, and the step-reference grammar states its closing-word rule in its comment.

## 3.5.10 (2026-11-05)

Curated from the maintainer's development repository after the engine merge da4a370, plus this release's version and documentation edits.

The hardening items the 3.5.9 release left in its plan's Findings:

- `renumber` reads a plural list as comma entries closed by one `and` or `to` entry, so a count after the list (`Steps 7 to 9, 12 tests red`) is no longer a step number; it rewrites references in the plan's preamble too, counted under a `preamble` key.
- `boundary-commit` skips a plan kept out of git by design with an exit-0 line instead of refusing with a false reason, and reads a corrupt run marker as the error it is.
- `next-guard` keeps its denials from Python 3.9 and its body runs there; the three import-bound hooks and the four scripts still stand down below 3.12 with one line.
- `set N done` on a step already done appends a fix round after the step's record (`; fix round: <note>; <clause>`) instead of replacing it; a corrected note against the same record replaces that round's note, and the plan's own note passed back changes nothing.
- The eval graders share one Decisions condition, and the projection's `--text` test reads a token per query shape from the answer itself.
- Removed: the unread leftovers of `evals/lib/record.py` in the public cut (the shebang, `SUPPORTED_AGGREGATE_SCHEMA`, `DEFAULT_LEDGER`, the plugin-eval divider, `CORRECTION_SCHEMA`).

Documentation: the README leads with the intent (the four phases intent, spec, plan, run; the intent file and its commit first in Usage), and this changelog marks removals under a `Removed:` bullet, one item per surface, starting with the 3.5.9 entry.

## 3.5.9 (2026-11-05)

Curated from the maintainer's development repository after the engine merge de46e60, plus this release's version and documentation edits.

The install sweep's engine fixes:

- `merge` requires a `refs/notes/preflight` note on the head only under `ADW_PREFLIGHT_NOTE=required`; unset, the gate reads no notes (the README's Configuration table has the row).
- `start` checks the owner allow-list before it reaches GitHub, as preflight and merge do.
- `boundary-commit` commits the plan alone (with the delivery record when the plan carries one) at a chunk boundary and refuses anything else staged.
- Every hook and script prints one line and stands down below Python 3.12 instead of raising.
- Removed: the classifier probe (`evals/next-auto/probe_classifier.py`, its fixture, test and result files); the projection's two ledger sources (the close-out and factory parsers of `next_projection.py`); the plugin-eval functions of `evals/lib/record.py`, stripped by name in the public cut; `start`'s unused open-pull-request probe.
- Wrap-up writes the `Intent:` trailer from the plan header, third of the four trailer lines.

Carried from unattended use of the plugin under a scheduler:

- A run that commits no step hands back: no push, no pull request, `RUN STOPPED <reason> NO PR: no step committed`, the plan brought home and the local run branch kept for the morning read.
- One `[run <id>]` tag per entry, and the appender puts a bullet on its own line after an unterminated last line.
- `validate` warns, never refuses, when an open step names `pyproject`, `conftest`, `pre-commit-config` or `tox` without the file's basename; the `preflight` command prints each warning once as `PREFLIGHT WARN`.
- `next_plan.py renumber <plan> --from <n> --by <k>` relabels the PENDING steps from `<n>` up and every `Step <n>` or `Steps <n>, <n> and <n>` reference, extends the `Legacy step map` line, and refuses a non-positive shift, a start above every step and a step in range that is not PENDING.
- The ninth eval scenario, `decision-stop-empty`, grades the hand-back live.

Documentation: `docs/intent.md` is new (the intent artifact, the `Intent:` header line in `/adw:spec` and `/adw:plan-tdd`, the trailer, a run's hand-back in the methodology), and the command text, README and runbook moved with each change above. The first-feature walkthrough and the README overview shipped in the 3.5.8 documentation releases.

## 3.5.8 (2026-11-02)

First public release, curated from the maintainer's development repository at commit ab2b2d1.

The engine as it runs in production: the three commands, the run and spec-mining agents, the guard, Stop, ruling-log and ruling-recall hooks, the plan grammar and auto-run scripts, the delivery projection, the Markdown skill, the suite, and the next-auto eval harness.

Changes against the development tree, made for a public audience:

- The owner allow-list and the never-run set are read from `ADW_OWNERS` and `ADW_NEVER_AUTO`, with no default owner. Preflight, start and merge refuse until `ADW_OWNERS` is set.
- The done-check reviewer a verdict record names is `ADW_DONE_REVIEWER`, default `done-check`.
- The ruling hooks are opt-in: `ruling-log.py` records a dialog only in a project that already has `.next-auto/rulings/`.
- The plan grammar's trailing section is `## Decisions`; the `Eval-codes` value for the 0 pass, 1 fail, 2 and 3 apparatus convention is `apparatus`.
- The eval harness names its fixture repository through `ADW_EVAL_FIXTURE_REPO`.
- Test fixtures are synthetic: the structure of real plans, records and pull requests with their free text regenerated.
- The source is ASCII throughout.
