# Changelog

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
