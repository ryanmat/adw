# fixturepkg Implementation Plan
Spec: spec.md. Owner: the owner. Written: 2026-09-11.

## Conventions binding every step
- Test: uv run pytest
- Lint: uv run ruff check .
- Run: uv run python -c "import fixturepkg"
- Eval: uv run python evals/run_eval.py
- Eval-codes: exit0
- Holdout: evals/

## Chunk A: string utilities

### Step 1: slugify
Status: PENDING
Eval: yes
Mutation: required
Size: 80
Write tests first in `tests/test_slugify.py` covering R1: a normal sentence becomes a
hyphenated slug, runs of punctuation collapse to one hyphen, leading and trailing
separators are stripped, and an all-punctuation input returns the empty string. Then
implement `slugify` in `src/fixturepkg/__init__.py` and add it to `__all__`. The holdout
under `evals/` checks slugify against cases the tests do not see; the orchestrator runs it
after green, and nothing under `evals/` is edited during the run. Done when uv run pytest
is green and uv run ruff check . is clean.

## Decisions
(none)

## Findings
(none)
