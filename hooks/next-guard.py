#!/usr/bin/env python3
# Description: PreToolUse guard hook for /adw:next auto: denies hook-bypass everywhere, plus
# Description: merge, destructive, holdout and subagent actions while a run is live for its tree.

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import subprocess
import sys
import traceback
from pathlib import Path, PurePath

READONLY_GIT = {"status", "diff", "log", "show"}
GIT_HOOK_BYPASS = (
    "--no-verify",
    "--no-hooks",
    "core.hooksPath",
    "PRE_COMMIT_ALLOW_NO_CONFIG",
    "GIT_CONFIG_PARAMETERS",
)
SEPARATORS = {"|", "||", "&&", ";", "&"}
SENSITIVE_WORD_RE = re.compile(r"\b(?:git|gh|claude)\b")
# Run layout, mirrored from next_auto.RUN_SUBDIR. The run dir sits outside .claude/ so
# tool writes into the worktree are permitted; the marker inside it therefore has no
# protection from Claude Code itself, and this hook supplies it while a run is live, in the
# trees the run arms: the checkout it was started from and its worktree, not the
# repository's other worktrees.
RUN_SUBDIR = ".next-auto"
MARKER_NAMES = {"state.json", "state.json.tmp"}
MARKER_RE = re.compile(r"state\.json|\.next-auto")
TAMPER_PROGRAMS = {"rm", "mv", "cp", "tee", "truncate", "install", "ln", "unlink", "shred", "dd"}
REDIRECTS = {">", ">>"}
# Programs that run the program named by their arguments, mapped to the flags that consume
# the token after them: `nice -n 10 git ...` and `sudo -u x git ...` both run git. A path
# to a launcher (`/usr/bin/env`) unwraps the same way, so identification never depends on
# how the caller spelled the launcher.
LAUNCHERS: dict[str, frozenset[str]] = {
    "env": frozenset({"-u", "-C", "-S", "--unset", "--chdir", "--split-string"}),
    "command": frozenset(),
    "exec": frozenset({"-a"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "nohup": frozenset(),
    "sudo": frozenset({"-u", "-g", "-C", "-D", "-p", "-r", "-t", "-T", "-U"}),
    "doas": frozenset({"-u", "-C"}),
    "xargs": frozenset({"-a", "-d", "-E", "-e", "-I", "-i", "-L", "-l", "-n", "-P", "-s"}),
    "time": frozenset({"-f", "-o", "--format", "--output"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
    "builtin": frozenset(),
    # npx and bunx run the package binary named after their flags; `npm exec` and its alias
    # `npm x` are npx under npm's name, read as a two-word launcher (_launcher_at)
    "npx": frozenset({"-p", "--package", "-c", "--call"}),
    "bunx": frozenset({"-p", "--package"}),
}
NPM_EXEC_WORDS = frozenset({"exec", "x"})
# `env -S STRING` re-splits STRING into env's own argument list before it execs, so the
# words of the string are the command and are spliced into the token list at lex time.
ENV_SPLIT_FLAGS = frozenset({"-S", "--split-string"})
# `npx -c STRING` runs STRING as the command, so its words are spliced in the same way
CALL_SPLIT_FLAGS = frozenset({"-c", "--call"})
GROUP_OPENERS = {"(", "{"}
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


# ---- repo / state discovery -------------------------------------------------


def find_work_tree(cwd: str | None) -> Path | None:
    """The top level of the work tree cwd is in, as stop-gate.py reads it: a linked worktree's
    own, not the main checkout. Where git cannot answer, the nearest directory at or above cwd
    with a .git entry."""
    if not cwd:
        return None
    start = Path(cwd)
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception as exc:  # the hook must not die on a missing or hung git
        print(f"next-guard: git rev-parse failed in {start}: {exc!r}", file=sys.stderr)
    # fallback: walk up for a .git entry (dir or file)
    for p in [start, *start.parents]:
        if (p / ".git").exists():
            return p
    return None


def run_worktree(top: Path) -> bool:
    """True when top is a live run's worktree, <checkout>/.next-auto/<run-id>/wt, the tree
    start adds beside the run's marker."""
    return (
        top.name == "wt"
        and top.parent.parent.name == RUN_SUBDIR
        and (top.parent / "state.json").is_file()
    )


def active_state(top: Path | None) -> dict | None:
    """The marker of the run live for the work tree at top, read as stop-gate.py reads it: the
    newest state.json under the tree's own .next-auto/, the checkout the run was started from,
    or the marker beside the tree when it is a live run's worktree. A run live from another
    worktree of the repository arms nothing here."""
    if top is None:
        return None
    base = top / RUN_SUBDIR
    states = (
        sorted(base.glob("*/state.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if base.is_dir()
        else []
    )
    if not states and run_worktree(top):
        states = [top.parent / "state.json"]
    if not states:
        return None
    try:
        data = json.loads(states[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # fail closed: an unreadable marker still arms the hook, and says why it reads as empty
        print(f"next-guard: could not parse {states[0]}: {exc}", file=sys.stderr)
        data = {}
    data["_path"] = str(states[0])
    return data


# ---- helpers ----------------------------------------------------------------


def _tokens(cmd: str) -> list[str]:
    """Shell-aware tokens. punctuation_chars keeps operators (&&, ;, |) as their own
    tokens, so a segment splitter can see `cd x && git stash` as two commands.

    A newline separates commands exactly as `;` does, so it is rewritten to one before
    lexing; shlex would otherwise treat it as whitespace and glue `cd x\ngit stash`
    into a single segment whose program is cd. A backslash-newline continuation is the
    opposite case, one command across lines, and becomes a space. Heredoc bodies are
    matched separately on the raw command text, so rewriting them here only ever adds
    segments to inspect."""
    cmd = cmd.replace("\\\n", " ").replace("\n", " ; ")
    try:
        lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = cmd.split()
    return _unwrap_env_split(tokens)


def _split_words(text: str) -> list[str]:
    """The words env -S makes of its string. One that will not lex stays one token."""
    try:
        return shlex.split(text)
    except ValueError:
        return [text]


def _unwrap_env_split(tokens: list[str]) -> list[str]:
    """Splice the words of every `env -S STRING` in place of the flag and its string.

    env re-splits STRING and processes the words as the rest of its own argument list
    (more flags, assignments, then the program), so `env -S 'git push --force'` runs
    git and every rule has to see `git`, `push`, `--force` as tokens rather than one
    string. The attached (`-S'...'`, `--split-string=...`) and bundled (`-iS`) spellings
    unwrap the same way. The walk follows launchers only and stops at the first program,
    so git's own `-S` (the signing flag) is never touched.
    """
    out: list[str] = []
    seg: list[str] = []
    for t in [*tokens, None]:
        if t is not None and t not in SEPARATORS:
            seg.append(t)
            continue
        out.extend(_unwrap_env_split_segment(seg))
        seg = []
        if t is not None:
            out.append(t)
    return out


def _unwrap_env_split_segment(seg: list[str]) -> list[str]:
    seg = list(seg)
    i = 0
    while i < len(seg):
        t = seg[i]
        if t in GROUP_OPENERS or ASSIGNMENT_RE.match(t):
            i += 1
            continue
        name, width = _launcher_at(seg, i)
        if name is None:
            break
        takes_arg = LAUNCHERS[name]
        i += width
        while i < len(seg) and seg[i].startswith("-"):
            start = i
            i, flag, value = _flag_step(seg, i, takes_arg)
            split = (name == "env" and flag in ENV_SPLIT_FLAGS) or (
                name == "npx" and flag in CALL_SPLIT_FLAGS
            )
            if split and value is not None:
                seg[start:i] = _split_words(value)
                i = start  # the words continue the launcher's own argument list
    return seg


def _launcher_at(seg: list[str], i: int) -> tuple[str | None, int]:
    """The launcher seg[i] names, as its LAUNCHERS key, and how many words its name spans:
    `npm exec` and `npm x` span two and read as npx. (None, 0) when seg[i] names none."""
    name = _basename(seg[i])
    if name == "npm" and i + 1 < len(seg) and seg[i + 1] in NPM_EXEC_WORDS:
        return "npx", 2
    return (name, 1) if name in LAUNCHERS else (None, 0)


def _segments(tokens: list[str]) -> list[list[str]]:
    """Split a token list into command segments on shell separators.

    `cd x && git stash` is two segments. Inspecting only the first program would
    let any rule be bypassed by prefixing an unrelated command.
    """
    segs: list[list[str]] = []
    cur: list[str] = []
    for t in tokens:
        if t in SEPARATORS:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def _program_index(seg: list[str]) -> int | None:
    """Index of the token that names the program a segment runs.

    Skips `VAR=val` assignments, the `(` and `{` that open a subshell or group, and any
    launcher (with the flags it consumes), so `env -i X=1 nice -n 10 /usr/bin/git ...`
    resolves to the git token. None when nothing is left.
    """
    i = 0
    while i < len(seg):
        t = seg[i]
        if t in GROUP_OPENERS or ASSIGNMENT_RE.match(t):
            i += 1
            continue
        name, width = _launcher_at(seg, i)
        if name is None:
            return i
        i += width
        while i < len(seg) and seg[i].startswith("-"):
            i, _flag, _value = _flag_step(seg, i, LAUNCHERS[name])
    return None


def _flag_step(
    seg: list[str], i: int, takes_arg: frozenset[str]
) -> tuple[int, str | None, str | None]:
    """Step over the launcher flag at seg[i] the way getopt reads it.

    Returns the index after the flag and its argument, the flag that took an argument
    (or None), and that argument (or None). A long flag takes `=value` or the next
    token. In a short cluster, a letter with trailing characters carries them as its
    argument (`-n10`, `-uroot`) and a letter in last position takes the next token
    (`-Hu root`); skipping the whole cluster as one flag left `root` as the program.
    """
    t = seg[i]
    i += 1
    if t.startswith("--"):
        name, eq, value = t.partition("=")
        if name not in takes_arg:
            return i, None, None
        if eq:
            return i, name, value
        if i < len(seg):
            return i + 1, name, seg[i]
        return i, name, None
    for j, ch in enumerate(t[1:], start=1):
        flag = f"-{ch}"
        if flag not in takes_arg:
            continue
        rest = t[j + 1 :]
        if rest:
            return i, flag, rest
        if i < len(seg):
            return i + 1, flag, seg[i]
        return i, flag, None
    return i, None, None


def _basename(token: str) -> str:
    """`/usr/bin/git`, `./git`, and `~/.local/bin/claude` compare as `git` and `claude`."""
    return PurePath(token).name if "/" in token else token


def _is_opaque(token: str) -> bool:
    """A program token the hook cannot identify: a variable, a command substitution, or a
    backtick. The lexer already resolves `\\git` to `git`, so a leading backslash never
    reaches here; a quoted one would, and is treated as opaque."""
    return token.startswith("$") or "`" in token or token.startswith("\\")


def _program(seg: list[str]) -> str | None:
    """The normalized program a segment runs, or None."""
    i = _program_index(seg)
    return None if i is None else _basename(seg[i])


def _args(seg: list[str]) -> list[str]:
    """The tokens after the program: its own flags and operands."""
    i = _program_index(seg)
    return [] if i is None else seg[i + 1 :]


def _git_subcommand_index(seg: list[str]) -> int | None:
    """Index of the git subcommand in one segment, skipping `-c k=v` and `-C dir`."""
    if _program(seg) != "git":
        return None
    i = _program_index(seg) + 1
    while i < len(seg):
        t = seg[i]
        if t in ("-C", "-c", "--git-dir", "--work-tree"):
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return i
    return None


def _git_subcommand(seg: list[str]) -> str | None:
    """Return the git subcommand of one segment, or None."""
    i = _git_subcommand_index(seg)
    return None if i is None else seg[i]


def _git_dash_c_base(seg: list[str], base: Path | None) -> Path | None:
    """The directory a git call's operands resolve against: `-C dir` applied to the
    segment's effective directory, or that directory alone."""
    for i, t in enumerate(seg):
        if t == "-C" and i + 1 < len(seg):
            target = Path(seg[i + 1])
            if target.is_absolute():
                return target
            return None if base is None else base / target
    return base


def _commit_has_short_n(tokens: list[str]) -> bool:
    return any(t.startswith("-") and not t.startswith("--") and "n" in t[1:] for t in tokens)


def _push_is_dangerous(cmd: str, tokens: list[str]) -> bool:
    if any(f in cmd for f in ("--force-with-lease", "--force", "--mirror", "--delete")):
        return True
    for t in tokens:
        if t.startswith("-") and not t.startswith("--") and ("f" in t[1:] or "d" in t[1:]):
            return True
        if t.startswith("+"):
            return True
        if t.startswith(":"):
            return True  # `:branch` is a delete refspec
        if t.startswith("$"):
            return True  # an unexpanded variable can carry any flag
        # plain refspec local:remote (no URL, no scp-form, no path)
        if ":" in t and "//" not in t and "@" not in t and "/" not in t and "." not in t:
            left, _, right = t.partition(":")
            if left and right:
                return True
    return False


def _path_parts(file_path: str, root: Path | None) -> list[str]:
    p = PurePath(file_path)
    if p.is_absolute() and root is not None:
        with contextlib.suppress(ValueError):
            p = PurePath(str(Path(file_path))).relative_to(root)
    return list(p.parts)


def _holdout_parts(state: dict) -> list[str]:
    holdout = (state.get("holdout") or "").strip("/")
    return holdout.split("/") if holdout else []


def _under_holdout(path: str, state: dict, root: Path | None) -> bool:
    """True if `path` names something inside the run's holdout.

    The implementer works in the worktree and the harness hands the hook absolute
    paths there, so the path is tried relative to the worktree, relative to the repo
    root, and as given (cwd-relative from inside either copy). Matching only the
    root-relative form protected the root copy and nothing else.
    """
    hp = _holdout_parts(state)
    if not hp or not path:
        return False
    bases = [root, Path(state["worktree"]) if state.get("worktree") else None]
    candidates = [_path_parts(path, b) for b in bases if b is not None]
    candidates.append(list(PurePath(path).parts))
    return any(c[: len(hp)] == hp for c in candidates)


def _effective_dirs(segments: list[list[str]], cwd: str | None) -> list[Path | None]:
    """The directory each segment runs in, following `cd` through the command. None
    when it cannot be known (a `cd` to a variable)."""
    cur: Path | None = Path(cwd) if cwd else None
    out: list[Path | None] = []
    for seg in segments:
        out.append(cur)
        if _program(seg) != "cd":
            continue
        target = next((t for t in _args(seg) if not t.startswith("-")), None)
        if target is None or target == "~":
            cur = Path.home()
        elif target.startswith("~/"):
            cur = Path.home() / target[2:]
        elif target.startswith("$"):
            cur = None
        elif Path(target).is_absolute() or cur is None:
            cur = Path(target) if Path(target).is_absolute() else None
        else:
            cur = cur / target
    return out


def _touches_run_dir(token: str, base: Path | None, state: dict) -> bool:
    """True if a recursive rm of `token` would remove the run dir, an ancestor of it, or
    anything in it outside the worktree. Resolved as a path, not matched as a substring:
    the worktree lives inside the run dir, so every absolute worktree path contains
    `.next-auto`, and the implementer's own cleanup (`rm -rf <wt>/src/__pycache__`)
    must stay allowed."""
    run_dir = state.get("run_dir")
    if not run_dir:
        return RUN_SUBDIR in token
    if Path(token).is_absolute():
        target = Path(os.path.realpath(token))
    elif base is not None:
        target = Path(os.path.realpath(base / token))
    else:
        return RUN_SUBDIR in token
    rd = Path(os.path.realpath(run_dir))
    if target == rd or rd.is_relative_to(target):
        return True
    if not target.is_relative_to(rd):
        return False
    wt = Path(os.path.realpath(state["worktree"])) if state.get("worktree") else None
    return wt is None or target == wt or not target.is_relative_to(wt)


def _redirect_targets(segments: list[list[str]]) -> list[str]:
    """Directories the command itself points at: `cd <dir>` and `git -C <dir>`.

    A command can reach a guarded tree without the session being inside it, so the
    marker is looked for at those targets as well as at cwd.
    """
    out: list[str] = []
    for seg in segments:
        prog = _program(seg)
        if prog == "cd":
            for t in _args(seg):
                if not t.startswith("-"):
                    out.append(t)
                    break
        elif prog == "git":
            for i, t in enumerate(seg):
                if t == "-C" and i + 1 < len(seg):
                    out.append(seg[i + 1])
    return out


# ---- decision ---------------------------------------------------------------


def _always_deny(segments: list[list[str]]) -> str | None:
    """The rule an always-tier denial names, or None. Scoped to the segment that actually
    invokes the tool: a command that merely mentions these strings (a patch, a doc, a
    test) is not an attempt to use them. A wrapper payload (`sh -c '...'`, `eval ...`)
    is a command of its own and is judged the same way, so `sh -c "git commit
    --no-verify -m x"` reads as the commit it runs."""
    for seg in segments:
        if _program(seg) != "git":
            continue
        sub = _git_subcommand(seg)
        if sub == "commit" and ("--no-verify" in seg or _commit_has_short_n(seg)):
            return "git commit hook-bypass flag (--no-verify / -n)"
        if any(any(tok in t for tok in GIT_HOOK_BYPASS) for t in seg):
            return (
                "git hook-bypass flag or env (--no-hooks, hooksPath, "
                "PRE_COMMIT_ALLOW_NO_CONFIG, GIT_CONFIG_PARAMETERS)"
            )
        if sub == "commit" and any(t.startswith("SKIP=") for t in seg):
            return "SKIP= in front of git commit bypasses pre-commit"
    for seg in segments:
        prog = _program(seg)
        if prog == "pre-commit" and "uninstall" in seg:
            return "pre-commit uninstall"
        if prog == "chmod" and any(".git/hooks" in t for t in seg):
            return "chmod on .git/hooks"
    for payload in _wrapper_payloads(segments):
        rule = _always_deny(_segments(_tokens(payload)))
        if rule:
            return rule
    return None


def evaluate(payload: dict) -> tuple[str, str]:
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input", {}) or {}
    is_sub = bool(payload.get("agent_id"))

    cmd = (ti.get("command", "") or "") if tool == "Bash" else ""
    tokens = _tokens(cmd) if cmd else []
    segments = _segments(tokens)

    root = find_work_tree(payload.get("cwd"))
    state = active_state(root)
    if state is None:
        for target in _redirect_targets(segments):
            candidate = find_work_tree(target)
            found = active_state(candidate)
            if found is not None:
                state = found
                break
    if state is not None:
        # paths resolve against the checkout the run was started from, <checkout>/.next-auto/
        # <run-id>/state.json, so its holdout copy is covered from the run's worktree too
        root = Path(state["_path"]).parents[2]
    marker = f" next-auto run active: {state['_path']}" if state else ""

    def deny(rule: str) -> tuple[str, str]:
        return "deny", f"blocked: {rule}.{marker}"

    if tool in ("Edit", "Write", "MultiEdit"):
        fp = ti.get("file_path", "") or ""
        parts = _path_parts(fp, root)
        if ".git" in parts:
            return deny("edit under .git/ is not permitted")
        if PurePath(fp).name == ".pre-commit-config.yaml":
            return deny("editing .pre-commit-config.yaml is not permitted")
        if state:
            if PurePath(fp).name in MARKER_NAMES and RUN_SUBDIR in parts:
                return deny("editing the run marker (state.json) during a run")
            if _under_holdout(fp, state, root):
                return deny(
                    f"edit under the holdout path ({state.get('holdout')}) during a run"
                )
        return "allow", ""

    if tool != "Bash":
        return "allow", ""

    git_calls = [(_git_subcommand(s), s) for s in segments if _program(s) == "git"]

    # ---- always-deny (regardless of marker) ----
    rule = _always_deny(segments)
    if rule:
        return deny(rule)

    if not state:
        return "allow", ""

    # ---- marker present: a program the hook cannot name ----
    # `$(which git) push --force` and `$GIT stash` run whatever the shell resolves; every
    # rule below keys on the program, so an unidentifiable one is refused while armed.
    for seg in segments:
        i = _program_index(seg)
        if i is not None and _is_opaque(seg[i]):
            return deny("opaque program token during a run")

    # ---- marker present: subagent restrictions ----
    if is_sub:
        for sub, _seg in git_calls:
            if sub is not None and sub not in READONLY_GIT:
                return deny(
                    f"subagents may run git {', '.join(sorted(READONLY_GIT))} only, not '{sub}'"
                )
        if _tokens_has_program(tokens, "gh"):
            return deny("subagents may not run gh during a run")

    # ---- marker present: merge / release ----
    # the merge gate is the attended loop's; `/adw:next auto` never merges, through gh or
    # through the script that wraps it (Requirement 19)
    for seg in segments:
        if _runs_next_auto_merge(seg):
            return deny("next_auto.py merge during a run")
    if _tokens_has_program(tokens, "gh"):
        if "pr" in tokens and "merge" in tokens:
            return deny("gh pr merge during a run")
        if "release" in tokens:
            return deny("gh release during a run")
        if "api" in tokens and "/merge" in cmd:
            return deny("gh api to a /merge endpoint during a run")

    # ---- marker present: git destructive ----
    if any(sub == "push" for sub, _seg in git_calls) and any(
        f in cmd for f in ("--force", "--mirror", "--delete")
    ):
        # `F=--force; git push $F` puts the flag outside the push segment
        return deny("dangerous git push (force / mirror / delete) during a run")
    for sub, seg in git_calls:
        if sub == "push" and _push_is_dangerous(" ".join(seg), seg):
            return deny("dangerous git push (force / mirror / refspec) during a run")
        if sub == "stash":
            return deny("git stash during a run")
        if sub == "tag":
            return deny("git tag during a run")
        if sub in ("checkout", "switch"):
            default = state.get("default", "")
            if default and default in seg:
                return deny(f"git {sub} of the default branch ({default}) during a run")
        if sub == "reset" and "--hard" in seg:
            return deny("git reset --hard during a run")
        if sub == "worktree" and "remove" in seg:
            return deny("git worktree remove during a run")
        if sub == "branch" and any(
            t.startswith("-") and not t.startswith("--") and "D" in t[1:] for t in seg
        ):
            return deny("git branch -D during a run")

    # ---- marker present: git clean reaching the run dir ----
    # .next-auto/ is excluded, not tracked, so -x and -X reach it and remove the marker.
    # An operand resolving into the run dir does the same with -fd. `git clean -fd`
    # inside the worktree is the shape `revert` uses and stays allowed.
    for seg, base in zip(segments, _effective_dirs(segments, payload.get("cwd")), strict=True):
        if _git_subcommand(seg) != "clean":
            continue
        args = seg[_git_subcommand_index(seg) + 1 :]
        if any(
            t.startswith("-") and not t.startswith("--") and ("x" in t[1:] or "X" in t[1:])
            for t in args
        ):
            return deny("git clean -x/-X (reaches the excluded run directory) during a run")
        operands = [t for t in args if not t.startswith("-")]
        gbase = _git_dash_c_base(seg, base)
        if any(_touches_run_dir(t, gbase, state) for t in operands):
            return deny("git clean into the run directory during a run")

    # ---- marker present: recursive rm touching the run dir ----
    for seg, base in zip(segments, _effective_dirs(segments, payload.get("cwd")), strict=True):
        args = _args(seg)
        if _program(seg) != "rm" or not _is_recursive(args):
            continue
        operands = [t for t in args if not t.startswith("-")]
        if any(_touches_run_dir(t, base, state) for t in operands):
            return deny("recursive rm touching the run directory during a run")

    # ---- marker present: tampering with the run marker ----
    # The marker is what arms this hook. Reads are fine; anything that removes, moves,
    # or rewrites it through a program or a redirect is not.
    for seg in segments:
        prog = _program(seg)
        if prog in TAMPER_PROGRAMS and any(_names_marker(t) for t in seg):
            return deny("removing or rewriting the run marker (state.json) during a run")
        if prog == "find" and ("-delete" in seg or "-exec" in seg) and any(
            _names_marker(t) for t in seg
        ):
            return deny("find -delete/-exec on the run marker (state.json) during a run")
        for i, t in enumerate(seg):
            if t in REDIRECTS and i + 1 < len(seg) and _names_marker(seg[i + 1]):
                return deny("redirect into the run marker (state.json) during a run")

    # ---- marker present: writes into the holdout through the shell ----
    if _holdout_parts(state):
        for seg in segments:
            prog = _program(seg)
            if prog in TAMPER_PROGRAMS and any(_under_holdout(t, state, root) for t in seg[1:]):
                return deny(f"shell write under the holdout path ({state.get('holdout')})")
            for i, t in enumerate(seg):
                if t in REDIRECTS and i + 1 < len(seg) and _under_holdout(seg[i + 1], state, root):
                    return deny(f"redirect into the holdout path ({state.get('holdout')})")

    # ---- marker present: shell wrappers carrying git/gh/claude ----
    # Matched on the wrapper's own payload, not on the whole command: a path such as
    # evals/ or .claude/ contains these words without being a wrapper or a target.
    # A payload is opaque code (shell, Python, a substitution), so it is matched on the
    # whole word rather than by program position. Erring toward denial inside something
    # about to be executed is the safe direction.
    for payload in _wrapper_payloads(segments) + _heredoc_bodies(cmd):
        if SENSITIVE_WORD_RE.search(payload):
            return deny(
                "shell wrapper (sh -c / eval / heredoc / python -c) carrying git/gh/claude"
            )
        if MARKER_RE.search(payload):
            return deny(
                "shell wrapper (sh -c / eval / heredoc / python -c) naming the run marker"
            )

    # ---- marker present: nested claude ----
    if _tokens_has_program(tokens, "claude"):
        return deny("nested claude invocation during a run")

    return "allow", ""


WRAPPER_PROGRAMS = {"sh", "bash", "zsh", "python", "python3"}


def _wrapper_payloads(segments: list[list[str]]) -> list[str]:
    """The command strings passed to a wrapper: `bash -c <payload>`, `eval <payload>`."""
    out: list[str] = []
    for seg in segments:
        prog = _program(seg)
        if prog == "eval":
            out.append(" ".join(_args(seg)))
        elif prog in WRAPPER_PROGRAMS:
            for i, t in enumerate(seg):
                if t == "-c" and i + 1 < len(seg):
                    out.append(seg[i + 1])
    return out


def _heredoc_bodies(cmd: str) -> list[str]:
    """Everything after a heredoc marker. The body is opaque to tokenizing, so it is
    treated as one payload."""
    if "<<" not in cmd:
        return []
    return [cmd.split("<<", 1)[1]]


def _runs_next_auto_merge(seg: list[str]) -> bool:
    """True when the segment runs next_auto.py's `merge` subcommand: the script as the
    program (`./scripts/next_auto.py merge 7`) or as the first operand of a python
    interpreter (`python3 <path>/next_auto.py --root . merge 7`)."""
    prog = _program(seg)
    if prog is None:
        return False
    args = _args(seg)
    if prog == "next_auto.py":
        return "merge" in args
    if prog.startswith("python"):
        for i, t in enumerate(args):
            if _basename(t) == "next_auto.py":
                return "merge" in args[i + 1 :]
    return False


def _tokens_has_program(tokens: list[str], name: str) -> bool:
    """True if any segment of the command runs `name` as its program, however spelled."""
    return any(_program(seg) == name for seg in _segments(tokens))


def _names_marker(token: str) -> bool:
    return PurePath(token).name in MARKER_NAMES


def _is_recursive(tokens: list[str]) -> bool:
    return "--recursive" in tokens or any(
        t.startswith("-") and not t.startswith("--") and "r" in t[1:].lower() for t in tokens
    )


# ---- main -------------------------------------------------------------------


def main() -> int:
    payload = json.load(sys.stdin)
    decision, reason = evaluate(payload)
    if decision == "deny":
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc(file=sys.stderr)
        # Fail closed if a run is active for the best-effort cwd; fail open otherwise.
        try:
            root = find_work_tree(os.getcwd())
            sys.exit(2 if active_state(root) else 0)
        except Exception as inner:
            # The run lookup itself failed, so there is nothing to fail closed on; say so.
            print(f"next-guard: fail-closed check failed, allowing: {inner!r}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            sys.exit(0)
