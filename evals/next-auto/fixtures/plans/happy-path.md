# fixturepkg Implementation Plan
Spec: spec.md. Owner: the owner. Written: 2026-09-11.

## Conventions binding every step
- Test: uv run pytest
- Lint: uv run ruff check .
- Run: uv run python -c "import fixturepkg"

## Chunk A: string utilities

### Step 1: slugify
Status: PENDING
Eval: no
Mutation: required
Size: 80
Write tests first in `tests/test_slugify.py` covering R1: a normal sentence becomes a
hyphenated slug, runs of punctuation collapse to one hyphen, leading and trailing
separators are stripped, and an all-punctuation input returns the empty string. Then
implement `slugify` in `src/fixturepkg/__init__.py` and add it to `__all__`. Done when
uv run pytest is green and uv run ruff check . is clean.

### Step 2: truncate
Status: PENDING
Eval: no
Mutation: required
Size: 80
Write tests first in `tests/test_truncate.py` covering R2: a short string is returned
unchanged, a long string is cut to exactly `limit` characters ending in an ellipsis, and
a limit below 1 raises ValueError. Then implement `truncate` in
`src/fixturepkg/__init__.py` and add it to `__all__`. Done when uv run pytest is green
and uv run ruff check . is clean.

### Step 3: word_count
Status: PENDING
Eval: no
Mutation: required
Size: 80
Write tests first in `tests/test_word_count.py` covering R3: a normal sentence counts its
words, repeated whitespace does not inflate the count, and empty or whitespace-only input
returns 0. Then implement `word_count` in `src/fixturepkg/__init__.py` and add it to
`__all__`. Done when uv run pytest is green and uv run ruff check . is clean.

## Decisions
(none)

## Findings
(none)
