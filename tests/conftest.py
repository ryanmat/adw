# Description: Suite-wide environment: the configured values the engine reads at import, assigned
# Description: before any test module imports the scripts, so the suite runs the same everywhere.

from __future__ import annotations

import os

os.environ["ADW_OWNERS"] = "ryanmat,acme"
os.environ["ADW_NEVER_AUTO"] = "ryanmat/never-one,ryanmat/never-two"
os.environ["ADW_DONE_REVIEWER"] = "done-check"
os.environ["ADW_EVAL_FIXTURE_REPO"] = "example/next-auto-fixture"
