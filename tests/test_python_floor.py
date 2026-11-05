# Description: The Python floor: every hook and script says one line below its floor and stands
# Description: down, 3.12 for seven files and 3.9 for the guard hook; all eight parse at 3.9.

from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# the three import-bound hooks, floor 3.12; the guard hook keeps its denials from 3.9 (3.5.10
# spec R5, rul-7a3e51f7fc) and has its own floor line below
HOOKS = [
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
GUARD = "hooks/next-guard.py"
LINE = "adw: python3 is {}; the plugin needs 3.12 or later on PATH\n"
GUARD_LINE = "adw: python3 is {}; the guard hook needs 3.9 or later on PATH\n"
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


@pytest.mark.parametrize("rel", HOOKS + SCRIPTS + [GUARD])
def test_preamble_compiles_at_the_3_8_grammar_and_imports_only_sys(rel):
    src = preamble(ROOT / rel)
    floor = (3, 9) if rel == GUARD else (3, 12)
    assert f"sys.version_info < {floor}" in src, f"{rel} has no {floor} guard before its imports"
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


def test_the_guard_hook_below_3_9_says_its_own_line_and_stands_down():
    r = run_below(GUARD, (3, 8, 18), stdin="{}")
    assert (r.returncode, r.stdout, r.stderr) == (0, "", GUARD_LINE.format("3.8"))


@pytest.mark.parametrize("version,shown", VERSIONS)
def test_the_guard_hook_from_3_9_keeps_its_hook_bypass_denial(version, shown, tmp_path):
    """The PreToolUse payload tests/test_next_guard.py builds for `git commit --no-verify -m x`
    (a repository with a `.git` directory and no run marker) is denied with the reason the body
    gave under Apple's /usr/bin/python3 3.9.6 on 2026-11-05 10:07 EST: `permissionDecision`
    deny, `no-verify` in the reason, exit 2."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": str(repo),
        "tool_input": {"command": "git commit --no-verify -m x"},
    }
    r = run_below(GUARD, version, stdin=json.dumps(payload))
    assert r.returncode == 2, (shown, r.stdout, r.stderr)
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert "no-verify" in out["permissionDecisionReason"]


@pytest.mark.parametrize("rel", HOOKS + SCRIPTS + [GUARD])
def test_the_whole_file_parses_at_the_3_9_grammar(rel):
    """An old interpreter compiles the whole module before the guard's first statement runs, so a
    `match` statement or `except*` anywhere in a guarded file would turn the one line into a
    SyntaxError (3.5.10 spec R6; all eight parsed at (3, 9) on 2026-11-05 10:06 EST)."""
    source = (ROOT / rel).read_text(encoding="utf-8")
    ast.parse(source, rel, feature_version=(3, 9))


@pytest.mark.parametrize("rel", HOOKS + SCRIPTS + [GUARD])
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


def real_interpreter(major_minor: tuple[int, int]) -> str | None:
    """A python on this machine whose version is `major_minor`, or None. The Mac's
    /usr/bin/python3 is 3.9.6; a `python3.9` on PATH counts too."""
    candidates = [shutil.which(f"python{major_minor[0]}.{major_minor[1]}"), "/usr/bin/python3"]
    for exe in candidates:
        if not exe or not Path(exe).exists():
            continue
        r = subprocess.run(
            [exe, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0 and tuple(int(x) for x in r.stdout.split()) == major_minor:
            return exe
    return None


def marker_repo(tmp_path: Path) -> Path:
    """The repository tests/test_next_guard.py's mk_repo(with_state=True) builds: a `.git`
    directory and one run marker under `.next-auto/`."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    run_dir = repo / ".next-auto" / "20260911-abc123"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": "20260911-abc123",
                "default": "main",
                "holdout": "evals/",
                "run_dir": str(run_dir),
                "worktree": str(run_dir / "wt"),
                "branch": "next-auto/slug-20260911-abc123",
            }
        )
    )
    return repo


@pytest.mark.parametrize(
    "command,decision,reason",
    [
        ("git clean -fdx", 2, "git clean -x/-X"),
        ("rm -rf .next-auto/20260911-abc123", 2, "recursive rm touching the run directory"),
        ("ls", 0, ""),
    ],
)
def test_the_guard_body_runs_with_a_marker_present_under_a_real_3_9(
    tmp_path, command, decision, reason
):
    """`run_below` patches only `sys.version_info`, so a 3.10+ runtime API in the body passes
    it unseen: `zip(..., strict=True)` raised `TypeError: zip() takes no keyword arguments` on
    every marker-present command under Apple's 3.9.6 (Chunk B review, 2026-11-05), and the
    `__main__` handler then denied everything with a traceback. The marker-present path is
    run here under a real 3.9 when the machine has one: the two denials with their reasons,
    and an ordinary command allowed with nothing on stderr."""
    exe = real_interpreter((3, 9))
    if exe is None:
        pytest.skip("no Python 3.9 interpreter on this machine")
    repo = marker_repo(tmp_path)
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": str(repo),
        "tool_input": {"command": command},
    }
    r = subprocess.run(
        [exe, str(ROOT / GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert "Traceback" not in r.stderr, r.stderr
    assert r.returncode == decision, (r.stdout, r.stderr)
    if decision == 2:
        out = json.loads(r.stdout)["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"
        assert reason in out["permissionDecisionReason"]
    else:
        assert (r.stdout, r.stderr) == ("", "")


def test_the_guard_hook_calls_zip_without_the_3_10_strict_keyword():
    """The static pin for the same gap, for a machine with no 3.9: no `zip(...)` in the guard
    hook carries a `strict` keyword (3.10, PEP 618); the pairing it guarded holds by
    construction, `_effective_dirs` appending one entry per segment."""
    tree = ast.parse((ROOT / GUARD).read_text(encoding="utf-8"), GUARD)
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "zip"
        and any(kw.arg == "strict" for kw in node.keywords)
    ]
    assert offenders == [], f"zip(strict=...) at {GUARD} lines {offenders}"
