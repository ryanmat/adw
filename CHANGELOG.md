# Changelog

## 3.5.9 (2026-11-05)

Curated from the maintainer's development repository after the engine merge de46e60, plus this release's version and documentation edits.

The install sweep's engine fixes:

- `merge` requires a `refs/notes/preflight` note on the head only under `ADW_PREFLIGHT_NOTE=required`; unset, the gate reads no notes (the README's Configuration table has the row).
- `start` checks the owner allow-list before it reaches GitHub, as preflight and merge do; the unused open-pull-request probe is gone.
- `boundary-commit` commits the plan alone (with the delivery record when the plan carries one) at a chunk boundary and refuses anything else staged.
- Every hook and script prints one line and stands down below Python 3.12 instead of raising.
- The classifier probe and the projection's two ledger sources are gone; the public tree is cut with the plugin-eval functions stripped by name.
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
