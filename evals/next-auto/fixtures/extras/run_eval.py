# Description: Holdout eval for the next-auto fixture: runs slugify over evals/cases.json.
# Description: Exit 0 when every case matches, 1 otherwise. Under Holdout, never edited in a run.

import json
import sys
from pathlib import Path

import fixturepkg


def main() -> int:
    cases = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))
    failures = []
    for case in cases:
        got = fixturepkg.slugify(case["input"])
        if got != case["expected"]:
            failures.append(f"FAIL {case['input']!r}: got {got!r}, want {case['expected']!r}")
    for line in failures:
        print(line)
    print(f"fixturepkg eval: {len(cases) - len(failures)}/{len(cases)} cases pass")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
