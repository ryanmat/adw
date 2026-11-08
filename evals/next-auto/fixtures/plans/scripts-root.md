# fixturepkg Implementation Plan
Spec: spec.md. Owner: the owner. Written: 2026-11-07.

## Conventions binding every step
- Test: uv run pytest
- Lint: uv run ruff check .
- Run: uv run python -c "import fixturepkg"

## Chunk A: a tool under scripts/

### Step 1: trace_tool
Status: PENDING
Eval: no
Mutation: required
Size: 80
Write tests first in `tests/test_trace_tool.py` that import the module bare, `import
trace_tool` (the repository's test setup already resolves bare names under `scripts/`;
change nothing there), and cover `trace_tool.pair_sum(a, b)`: two positive
integers return their sum, a negative beside a positive returns their sum, and two zeros
return 0. Then write `scripts/trace_tool.py` with the two-line pure function `pair_sum`
and a docstring stating what it returns. Done when uv run pytest is green and uv run
ruff check . is clean.

## Decisions
(none)

## Findings
(none)
