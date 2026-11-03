# fixturepkg Specification

A tiny string-utility package used only as the target of `/next auto` eval runs.
It exists so a run has real code, real tests, and a real diff to grade.

## Requirements

R1. `slugify(text)` returns a lowercase, hyphen-separated slug: runs of non-alphanumeric
characters collapse to a single hyphen, and leading and trailing hyphens are stripped.
An empty or all-punctuation input returns the empty string.

R2. `truncate(text, limit)` returns `text` unchanged when it is at most `limit`
characters. Otherwise it returns the first `limit` characters with the final character
replaced by an ellipsis character, so the result is never longer than `limit`.
A `limit` below 1 raises `ValueError`.

R3. `word_count(text)` returns the number of whitespace-separated tokens. Empty or
whitespace-only input returns 0.

## Invariants

I1. The package is standard library only. No runtime dependencies.
I2. Every public function has a docstring stating what it returns for empty input.
I3. Currency and money do not appear in this package, so no Decimal concerns apply.

## Eval plan

Not applicable. This package has no model behavior; the deterministic tests are the
whole gate.
