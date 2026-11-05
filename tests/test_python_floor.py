# Description: The Python floor: every hook and script says one line below 3.12 and stands down.
# Description: The preamble under ast at the 3.8 grammar, then each file run at 3.9 and 3.11.

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = [
    "hooks/next-guard.py",
    "hooks/ruling-log.py",
    "hooks/ruling-recall.py",
    "hooks/stop-gate.py",
]
SCRIPTS = [
    "scripts/next_plan.py",
    "scripts/next_auto.py",
    "scripts/next_projection.py",
    "scripts/rulings.py",
]
LINE = "adw: python3 is {}; the plugin needs 3.12 or later on PATH\n"
# the interpreter the install sweep found first on a Mac with no newer python3: the system
# 3.9.6, whose tracebacks the sweep spec quotes (R4; read 2026-11-03); 3.11 is the last
# version below the floor, so a guard drawn at (3, 11) lets it through
VERSIONS = [((3, 9, 6), "3.9"), ((3, 11, 9), "3.11")]
FUTURE = "from __future__ import annotations\n"


def preamble(path: Path) -> str:
    """The statements between the __future__ import and the first import that is not
    `import sys`: the part of the file an old interpreter must get through to print the line."""
    after = path.read_text(encoding="utf-8").split(FUTURE, 1)[1]
    kept = []
    for line in after.splitlines(keepends=True):
        plain = line.startswith("import ") and line.strip() != "import sys"
        if plain or line.startswith("from "):
            break
        kept.append(line)
    return "".join(kept)


@pytest.mark.parametrize("rel", HOOKS + SCRIPTS)
def test_preamble_compiles_at_the_3_8_grammar_and_imports_only_sys(rel):
    src = preamble(ROOT / rel)
    assert "sys.version_info < (3, 12)" in src, f"{rel} has no version guard before its imports"
    tree = ast.parse(src, rel, feature_version=(3, 8))
    names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    froms = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert (set(names), froms) == ({"sys"}, [])


def run_below(rel: str, version: tuple, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    """Run the file's source under this interpreter with sys.version_info reading `version`, the
    one thing the guard looks at; the file's own `import sys` returns the patched module."""
    path = str(ROOT / rel)
    patch = f"import sys; sys.version_info = {(*version, 'final', 0)!r}; "
    code = patch + f"exec(compile(open({path!r}).read(), {path!r}, 'exec'))"
    return subprocess.run(
        [sys.executable, "-c", code, *args], input=stdin, capture_output=True, text=True, cwd=ROOT
    )


@pytest.mark.parametrize("version,shown", VERSIONS)
@pytest.mark.parametrize("rel", HOOKS)
def test_a_hook_below_3_12_says_one_line_and_stands_down(rel, version, shown):
    r = run_below(rel, version, stdin="{}")
    assert (r.returncode, r.stdout, r.stderr) == (0, "", LINE.format(shown))


@pytest.mark.parametrize("version,shown", VERSIONS)
@pytest.mark.parametrize("rel", SCRIPTS)
def test_a_script_below_3_12_says_one_line_and_exits_2(rel, version, shown):
    r = run_below(rel, version, "--help")
    assert (r.returncode, r.stdout, r.stderr) == (2, "", LINE.format(shown))


@pytest.mark.parametrize("rel", HOOKS + SCRIPTS)
def test_each_file_still_imports_under_the_real_interpreter(rel, monkeypatch):
    path = ROOT / rel
    name = "floor_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # a dataclass built under `from __future__ import annotations` reads its module from
    # sys.modules while the class is created, so the module is registered before it runs
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    assert module.sys is sys
