# Description: Pins the grammar documented in the command sources and the markdown skill to
# Description: next_plan.py, and the adw plugin layout: manifests, hooks.json, no machine paths.

import inspect
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import next_auto
import next_plan
import pytest

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ROOT / "commands"
SKILL = ROOT / "skills" / "markdown" / "SKILL.md"
PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"


def first_markdown_block(text: str, must_contain: str) -> str:
    blocks = re.findall(r"```markdown\n(.*?)```", text, re.S)
    for b in blocks:
        if must_contain in b:
            return b
    raise AssertionError(f"no ```markdown block containing {must_contain!r}")


def fill(template: str) -> str:
    """Replace <angle> and [square] authoring placeholders with a plausible value."""
    # <n> stands for a positive integer, as Size: <n>; a word there fails validate
    filled = re.sub(r"<n>", "120", template)
    filled = re.sub(r"<[^<>\n]+>", "widget", filled)
    filled = re.sub(r"\[[^\[\]\n]+\]", "widget", filled)
    return filled


def test_plan_tdd_example_validates():
    doc = (COMMANDS / "plan-tdd.md").read_text()
    example = fill(first_markdown_block(doc, "## Conventions binding every step"))
    reasons = next_plan.validate_text(example)
    assert reasons == [], "plan-tdd.md example does not validate:\n" + "\n".join(
        f"  line {ln}: {msg}" for ln, msg in reasons
    )


def test_markdown_skill_example_validates():
    skill = SKILL.read_text()
    example = fill(first_markdown_block(skill, "## Conventions binding every step"))
    # the template elides later steps with a bare "..." line; drop it
    example = "\n".join(ln for ln in example.splitlines() if ln.strip() != "...")
    reasons = next_plan.validate_text(example)
    assert reasons == [], "skills/markdown/SKILL.md example does not validate:\n" + "\n".join(
        f"  line {ln}: {msg}" for ln, msg in reasons
    )


def test_next_md_references_real_subcommands():
    """Every `next_plan.py`/`next_auto.py` subcommand named in next.md must exist."""
    doc = (COMMANDS / "next.md").read_text()
    plan_subs = {
        "validate",
        "migrate",
        "status",
        "next",
        "brief",
        "set",
        "append",
        "batch",
    }
    auto_subs = {
        "preflight",
        "start",
        "step-begin",
        "gate",
        "prove-red",
        "commit",
        "review-package",
        "revert",
        "state",
        "wrap-up",
        "set",
        "append",
        "size",
        "check",
        "merge",
        "verdict",
        "record-commit",
        "pr-opened",
        "done-verdict",
        "plan-approved",
    }
    # a subcommand may end an inline code span, so strip the closing backtick
    for m in re.finditer(r"\$PLAN (\S+)", doc):
        sub = m.group(1).rstrip("`")
        assert sub in plan_subs, f"next.md calls unknown next_plan subcommand {sub}"
    for m in re.finditer(r"\$AUTO (\S+)", doc):
        sub = m.group(1).rstrip("`")
        assert sub in auto_subs, f"next.md calls unknown next_auto subcommand {sub}"


def test_next_md_covers_both_modes():
    doc = (COMMANDS / "next.md").read_text()
    assert "/adw:next auto" in doc
    assert "never merges" in doc or "Never merge" in doc
    assert "--no-verify" in doc


# ---- Step 8, 2026-09-11: continuity contracts, the context file, and the validator sentence ----


def test_plan_tdd_states_validator_requirements():
    """plan-tdd.md said Run was required while the validator required Test and Lint only.
    The doc sentence and the validator must agree, so both are checked here."""
    doc = (COMMANDS / "plan-tdd.md").read_text()
    assert "`Test` and `Lint` are required and `Run` is recommended" in doc
    assert "`Test`, `Lint`, and `Run` are required" not in doc
    example = fill(first_markdown_block(doc, "## Conventions binding every step"))
    without_run = "\n".join(ln for ln in example.splitlines() if not ln.startswith("- Run:"))
    assert next_plan.validate_text(without_run) == []
    without_test = "\n".join(ln for ln in example.splitlines() if not ln.startswith("- Test:"))
    assert any("Test" in msg for _ln, msg in next_plan.validate_text(without_test))


def test_plan_tdd_reads_an_existing_plan_outbox():
    doc = (COMMANDS / "plan-tdd.md").read_text()
    para = doc.split("## When a plan already exists", 1)[1].split("\n## ", 1)[0]
    for needle in ("Decisions", "Findings", "verbatim", "retired step numbers", "never emit"):
        assert needle in para, needle


def test_next_md_hands_the_context_file_to_the_implementer():
    doc = (COMMANDS / "next.md").read_text()
    assert "context-K.md" in doc and "brief-K.md" in doc
    step_begin = next(ln for ln in doc.splitlines() if "step-begin K" in ln)
    assert "context-K.md" in step_begin
    dispatch = next(ln for ln in doc.splitlines() if "Dispatch the `adw:next-implementer`" in ln)
    assert "context path" in dispatch


def test_next_md_states_both_continuity_contracts():
    doc = (COMMANDS / "next.md").read_text()
    for needle in ("tracked plan", "untracked plan", "[run <id>]", "morning read"):
        assert needle in doc, needle


def test_next_md_calls_scripts_through_python3_only():
    """A bare `$AUTO set ...` is an opaque program token, and the guard denies those during a
    run. Every script call in the command text is spelled `python3 $AUTO ...`."""
    doc = (COMMANDS / "next.md").read_text()
    bare = re.findall(r"(?<!python3 )\$(?:AUTO|PLAN) \S+", doc)
    assert bare == [], bare


def test_next_md_describes_the_public_body_and_the_scan():
    doc = (COMMANDS / "next.md").read_text()
    for needle in ("pr-body.md", "secret-shaped", "isPrivate", "never an unattended target"):
        assert needle in doc, needle


# adw 3.5.0 fix round (Chunk C review, Minor; ruled 2026-10-12 15:44:20 EDT): the boundary
# text says verdict N keeps a filtered report with the reviewer's text beside it, and that the
# fix round works from the kept findings (spec item 12)
def test_next_md_says_verdict_n_keeps_a_filtered_report():
    doc = (COMMANDS / "next.md").read_text()
    for needle in (
        "### Dropped findings",
        "review-<sha>-<round>.orig.md",
        "work from the kept findings",
    ):
        assert needle in doc, needle


# ---- Step 9, 2026-09-11: the adw plugin layout -----------------------------------------------


def test_plugin_manifest_fields():
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "adw"
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]), manifest["version"]
    assert manifest["description"].strip()
    assert manifest["author"]["name"].strip()


def test_plugin_layout_dirs_present():
    for rel in (
        "commands/spec.md",
        "commands/plan-tdd.md",
        "commands/next.md",
        "agents/next-implementer.md",
        "agents/next-reviewer.md",
        "agents/spec-miner.md",
        "hooks/hooks.json",
        "hooks/next-guard.py",
        "scripts/next_plan.py",
        "scripts/next_auto.py",
        "skills/markdown/SKILL.md",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
    ):
        assert (ROOT / rel).is_file(), rel
    # the sources moved; nothing is left at the root to drift from its plugin copy
    for stale in ("plan-tdd.md", "next.md", "close.md", "markdown.skill"):
        assert not (ROOT / stale).exists(), stale
    # a root spec.md is the pipeline's own artifact when this repo runs /adw:spec on itself;
    # only a command file (it carries $ARGUMENTS) would be the retired copy drifting back
    root_spec = ROOT / "spec.md"
    if root_spec.exists():
        assert "$ARGUMENTS" not in root_spec.read_text(), "spec.md is a command file"


def test_hooks_json_registers_guard_on_both_matchers_via_plugin_root():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
    by_matcher = {entry["matcher"]: entry["hooks"] for entry in hooks}
    # AskUserQuestion carries ruling-recall.py, checked in tests/test_ruling_recall.py
    assert set(by_matcher) == {"Bash", "Edit|Write|MultiEdit", "AskUserQuestion"}
    for matcher in ("Bash", "Edit|Write|MultiEdit"):
        cmds = by_matcher[matcher]
        assert len(cmds) == 1, matcher
        assert cmds[0]["type"] == "command"
        assert cmds[0]["command"] == f'"{PLUGIN_ROOT_VAR}/hooks/next-guard.py"', matcher
    assert os.access(ROOT / "hooks" / "next-guard.py", os.X_OK)


def test_no_machine_paths_in_commands_or_skill():
    """The plugin installs on any machine; a `~/.claude/` path in command or skill text would
    point at a copy that no longer exists there."""
    hits = []
    for d in ("commands", "agents", "skills"):
        for f in sorted((ROOT / d).rglob("*.md")):
            for i, ln in enumerate(f.read_text().splitlines(), 1):
                if "~/.claude/" in ln or "/Users/" in ln or "/home/" in ln:
                    hits.append(f"{f.relative_to(ROOT)}:{i}: {ln.strip()[:80]}")
    assert hits == [], "\n".join(hits)


def test_commands_name_scripts_via_plugin_root():
    """The probe showed ${CLAUDE_PLUGIN_ROOT} is substituted in command text before the model
    reads it, so the scripts are named through it and the model types an absolute path."""
    nxt = (COMMANDS / "next.md").read_text()
    assert f'PLAN="{PLUGIN_ROOT_VAR}/scripts/next_plan.py"' in nxt
    assert f'AUTO="{PLUGIN_ROOT_VAR}/scripts/next_auto.py"' in nxt
    assert f"{PLUGIN_ROOT_VAR}/scripts/next_plan.py" in (COMMANDS / "plan-tdd.md").read_text()
    assert f"{PLUGIN_ROOT_VAR}/scripts/next_plan.py" in SKILL.read_text()


def test_claude_plugin_validate_passes():
    """`claude plugin validate --strict` on every target, so warnings fail too. With both
    manifests present the repo root validates only the marketplace, so the plugin manifest
    and each component directory are validated on their own. Skipped only when the CLI is
    absent; the repo has no CI job yet (a recorded follow-on in docs/adw-plugin-plan.md),
    so off this machine the test runs only where the CLI is installed."""
    cli = shutil.which("claude")
    if cli is None:
        import pytest

        pytest.skip("claude CLI not on PATH")
    for target in (".", ".claude-plugin/plugin.json", "commands", "agents", "skills"):
        r = subprocess.run(
            [cli, "plugin", "validate", str(ROOT / target), "--strict"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, f"{target}:\n{r.stdout}{r.stderr}"
        assert "Validation passed" in r.stdout, f"{target}:\n{r.stdout}"


def test_marketplace_lists_adw_at_the_repo_root():
    """One plugin, sourced from the marketplace root itself. A relative source carries no
    ref (the docs are explicit), so the pin is the marketplace add:
    `claude plugin marketplace add ryanmat/adw@<tag>`."""
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert market["name"] == "adw"
    assert market["owner"]["name"].strip()
    assert [p["name"] for p in market["plugins"]] == ["adw"]
    entry = market["plugins"][0]
    assert entry["source"] == "./"
    assert entry["version"] == plugin["version"]


RELEASE_VERSION = "3.5.8"


def test_manifests_read_the_release_version_and_agree():
    """The release step bumps both manifests to the version it tags, so each reads the
    release version and the two agree; the pin moves with each release step."""
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["version"] == RELEASE_VERSION
    assert market["plugins"][0]["version"] == RELEASE_VERSION
    assert market["plugins"][0]["version"] == plugin["version"]


def test_commands_carry_frontmatter_descriptions():
    """`claude plugin validate --strict` requires a frontmatter description per command."""
    for name in ("spec", "plan-tdd", "next"):
        text = (COMMANDS / f"{name}.md").read_text()
        assert text.startswith("---\n"), name
        head = text.split("---", 2)[1]
        assert "description:" in head, name


# ---- Step 10, 2026-09-11: the rename pass and the Python floor -------------------------------

DOCS = ("README.md", "ai-dev-workflow.md", "workflow-runbook.md")
SCENARIOS = ROOT / "evals" / "next-auto" / "scenarios"
# a bare command name: not a path segment (commands/next.md, evals/next-auto/), not namespaced
BARE_COMMAND = re.compile(r"(?<![\w/.\-])/(spec|plan-tdd|next|close)(?![\w./\-])")
RETIRED_COPY_PATHS = (
    "~/.claude/commands",
    "~/.claude/scripts",
    "~/.claude/hooks",
    "~/.claude/agents",
    "~/.claude/skills",
)


def test_goal_line_names_plugin_command():
    """The plugin namespaces its commands, so the goal line `start` prints names
    `/adw:next auto`; a bare `/next auto` is the retired user-scope command."""
    assert "/adw:next auto" in next_auto.GOAL_LINE
    assert BARE_COMMAND.search(next_auto.GOAL_LINE) is None, next_auto.GOAL_LINE


def test_scenario_prompts_name_plugin_command():
    files = sorted(SCENARIOS.glob("*.json"))
    assert len(files) == 8, [f.name for f in files]
    attended = {"attended-chunk-stop", "attended-check-blocks-gaming"}
    for f in files:
        prompt = json.loads(f.read_text())["prompt"]
        if f.stem in attended:
            assert prompt == "/adw:next", f"{f.name}: {prompt!r}"
        else:
            assert re.fullmatch(r"/adw:next auto( \d+)?", prompt), f"{f.name}: {prompt!r}"


def test_readme_names_no_cp_loop():
    """Install is the marketplace add at a tag plus the plugin install; cp and cksum are gone."""
    readme = (ROOT / "README.md").read_text()
    assert "claude plugin marketplace add ryanmat/adw@" in readme
    assert "claude plugin install adw@adw" in readme
    for gone in ("cksum", "live copy", "live copies", *RETIRED_COPY_PATHS):
        assert gone not in readme, gone


def test_methodology_docs_install_the_plugin():
    """ai-dev-workflow.md's Config placement carries the install commands; the runbook points
    at the plugin rather than at copies to cksum."""
    methodology = (ROOT / "ai-dev-workflow.md").read_text()
    assert "claude plugin marketplace add ryanmat/adw@" in methodology
    assert "claude plugin install adw@adw" in methodology
    assert "hooks/hooks.json" in methodology
    runbook = (ROOT / "workflow-runbook.md").read_text()
    assert "plugin" in runbook
    for doc, text in (("ai-dev-workflow.md", methodology), ("workflow-runbook.md", runbook)):
        for gone in ("cksum", "live copy", "live copies", *RETIRED_COPY_PATHS):
            assert gone not in text, f"{doc}: {gone}"


def test_every_surface_names_plugin_commands():
    """No living surface says a bare /spec, /plan-tdd, /next, or /close: command text, agents,
    the skill, the scripts, the eval runner, and the three top-level docs. History under docs/
    and the legacy fixtures keep their text."""
    files = [ROOT / d for d in DOCS]
    for d in ("commands", "agents", "skills"):
        files.extend(sorted((ROOT / d).rglob("*.md")))
    for d in ("scripts", "hooks", "evals/next-auto"):
        files.extend(sorted((ROOT / d).glob("*.py")))
    hits = []
    for f in files:
        for i, ln in enumerate(f.read_text().splitlines(), 1):
            if BARE_COMMAND.search(ln):
                hits.append(f"{f.relative_to(ROOT)}:{i}: {ln.strip()[:80]}")
    assert hits == [], "\n".join(hits)


def test_next_md_dispatches_scoped_agents():
    """Plugin agents are addressed as adw:<name>; a bare name would resolve to a same-named
    user agent first, which is exactly the shadowing Step 12 retires."""
    doc = (COMMANDS / "next.md").read_text()
    assert "`adw:next-implementer`" in doc
    assert "`adw:next-reviewer`" in doc
    bare = re.findall(r"(?<!adw:)next-(?:implementer|reviewer)", doc)
    assert bare == [], bare


def test_pyproject_python_floor_is_312():
    """devbox's PATH python3 is 3.12.3; the scripts are stdlib and the suite passes there."""
    import tomllib

    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert cfg["project"]["requires-python"] == ">=3.12"
    assert cfg["tool"]["ruff"]["target-version"] == "py312"


# ---- v3.2.1: spec-miner joins the plugin ----------------------------------------------------

AGENTS = ROOT / "agents"


def agent_frontmatter(text: str) -> dict[str, str]:
    """The key: value pairs between the first two `---` lines. Stdlib only; no yaml module
    is installed, and agent frontmatter is flat."""
    assert text.startswith("---\n"), "no frontmatter"
    head = text.split("---", 2)[1]
    fields: dict[str, str] = {}
    for ln in head.splitlines():
        if ":" in ln and not ln.startswith(" "):
            key, _, value = ln.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def assert_least_privilege_frontmatter(fields: dict[str, str], where: str) -> None:
    """The estate audit's D4 bar: name, a description, a tools list, a model key."""
    assert fields.get("name"), f"{where}: no name"
    assert fields.get("description"), f"{where}: no description"
    tools = [t.strip() for t in fields.get("tools", "").split(",") if t.strip()]
    assert tools, f"{where}: no tools list"
    assert fields.get("model"), f"{where}: no model"


def test_agents_declare_tools_and_model():
    """Every plugin agent states its tools and model in frontmatter, so least privilege
    is enforced by the plugin system rather than promised in prose."""
    files = sorted(AGENTS.glob("*.md"))
    assert {f.name for f in files} >= {"next-implementer.md", "next-reviewer.md", "spec-miner.md"}
    for f in files:
        assert_least_privilege_frontmatter(agent_frontmatter(f.read_text()), f.name)


def test_agents_frontmatter_check_rejects_a_missing_tools_line(tmp_path):
    """The negative case: the same helper must fail on an agent whose tools line is gone."""
    import pytest

    text = (AGENTS / "next-reviewer.md").read_text()
    stripped = "\n".join(ln for ln in text.splitlines() if not ln.startswith("tools:")) + "\n"
    (tmp_path / "agent.md").write_text(stripped)
    with pytest.raises(AssertionError, match="no tools list"):
        assert_least_privilege_frontmatter(
            agent_frontmatter((tmp_path / "agent.md").read_text()), "agent.md"
        )


def test_spec_miner_frontmatter_matches_body_contract():
    """spec-miner's body limits writes to spec files and forbids mutations, installs, and
    network calls, so its tools are exactly Read, Grep, Glob, Write and never Bash."""
    fields = agent_frontmatter((AGENTS / "spec-miner.md").read_text())
    tools = {t.strip() for t in fields["tools"].split(",")}
    assert tools == {"Read", "Grep", "Glob", "Write"}, tools
    assert fields["model"] == "opus"
    assert fields["name"] == "spec-miner"
    assert "brownfield" in fields["description"]


def test_spec_miner_names_no_bare_commands():
    """The body already says /adw:plan-tdd; a bare /spec, /plan-tdd, /next, or /close
    outside fenced code would name a retired user-scope command."""
    text = (AGENTS / "spec-miner.md").read_text()
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    hits = [ln for ln in prose.splitlines() if BARE_COMMAND.search(ln)]
    assert hits == [], hits


def newest_v_tag(root: Path) -> tuple[int, int, int] | None:
    out = subprocess.run(
        ["git", "tag", "--list", "v*"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    tags = []
    for t in out.split():
        m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", t)
        if m:
            tags.append(tuple(int(x) for x in m.groups()))
    return max(tags) if tags else None


def version_tuple(version: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    assert m, version
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


PLUGIN_PAYLOAD = ("agents", "commands", "hooks", "scripts", "skills", ".claude-plugin")


def payload_unchanged_since_tag(root: Path, tag: tuple[int, int, int]) -> bool:
    """True when nothing the plugin ships differs between the newest tag and the working
    tree, untracked payload files included. Docs, tests, and plan commits after a release
    leave this True; a changed agent, command, hook, script, skill, or manifest, committed or
    not, makes it False and demands a bump."""
    name = "v" + ".".join(map(str, tag))
    # tag against the working tree (no HEAD operand), so an uncommitted edit counts: the
    # pre-commit gate runs this before the commit exists
    diff = subprocess.run(
        ["git", "diff", "--quiet", name, "--", *PLUGIN_PAYLOAD],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert diff.returncode in (0, 1), diff.stderr
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *PLUGIN_PAYLOAD],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return diff.returncode == 0 and untracked == ""


def assert_version_at_or_past(version: str, tag: tuple[int, int, int], unchanged: bool) -> None:
    """Equal to the newest tag is legal only while the shipped payload is unchanged since
    that tag; once anything the plugin ships differs, the manifest must be past the tag, or
    the next release would tag a manifest that still names the old version."""
    tag_text = ".".join(map(str, tag))
    if unchanged:
        assert version_tuple(version) >= tag, f"manifest {version} is behind tag v{tag_text}"
    else:
        assert version_tuple(version) > tag, f"manifest {version} is not past tag v{tag_text}"


def test_manifest_version_is_at_or_past_the_newest_tag():
    """A release bumps the manifest before it tags, so the manifest never trails the newest
    v* tag: equal right after tagging, ahead while the next release is in progress."""
    import pytest

    tag = newest_v_tag(ROOT)
    if tag is None:
        pytest.skip("no v* tag")
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert_version_at_or_past(manifest["version"], tag, payload_unchanged_since_tag(ROOT, tag))


def test_version_check_rejects_a_version_behind_the_tag():
    import pytest

    with pytest.raises(AssertionError, match=r"behind tag v3\.2\.0"):
        assert_version_at_or_past("3.1.9", (3, 2, 0), unchanged=True)


def test_version_check_rejects_an_unbumped_manifest_after_a_payload_change():
    """The review finding on Chunk A, refined in Step 9: equal to the tag is fine while the
    shipped payload matches the tag (docs and plan commits), and fails once it differs,
    otherwise the next release tags a stale version."""
    import pytest

    assert_version_at_or_past("3.2.0", (3, 2, 0), unchanged=True)
    with pytest.raises(AssertionError, match=r"not past tag v3\.2\.0"):
        assert_version_at_or_past("3.2.0", (3, 2, 0), unchanged=False)


def test_payload_probe_sees_a_changed_agent(tmp_path):
    """The probe itself: a tagged repo reports unchanged until a payload file changes."""
    run = lambda *a: subprocess.run(a, cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    commit = lambda msg: run(  # noqa: E731
        "git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", msg
    )
    run("git", "init", "-q")
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "a.md").write_text("---\nname: a\n---\n")
    (tmp_path / "docs.md").write_text("notes\n")
    run("git", "add", "-A")
    commit("release")
    run("git", "tag", "v1.0.0")
    (tmp_path / "docs.md").write_text("more notes\n")
    run("git", "add", "-A")
    commit("docs")
    assert payload_unchanged_since_tag(tmp_path, (1, 0, 0)) is True
    (tmp_path / "agents" / "a.md").write_text("---\nname: a\ntools: Read\n---\n")
    # uncommitted: the pre-commit gate runs pytest before the commit, so the probe must see
    # the working tree, not just HEAD (review, 2026-09-13)
    assert payload_unchanged_since_tag(tmp_path, (1, 0, 0)) is False
    run("git", "add", "-A")
    commit("agent change")
    assert payload_unchanged_since_tag(tmp_path, (1, 0, 0)) is False


def test_payload_probe_sees_an_untracked_payload_file(tmp_path):
    run = lambda *a: subprocess.run(a, cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q")
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "c.md").write_text("---\ndescription: c\n---\n")
    run("git", "add", "-A")
    run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "release")
    run("git", "tag", "v1.0.0")
    assert payload_unchanged_since_tag(tmp_path, (1, 0, 0)) is True
    (tmp_path / "commands" / "d.md").write_text("---\ndescription: d\n---\n")
    assert payload_unchanged_since_tag(tmp_path, (1, 0, 0)) is False


def test_manifest_descriptions_name_all_three_agents():
    """The roster in both manifest descriptions matches the agents directory."""
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    descriptions = (
        ("plugin.json", plugin["description"]),
        ("marketplace.json", market["plugins"][0]["description"]),
    )
    for where, desc in descriptions:
        for agent in ("next-implementer", "next-reviewer", "spec-miner"):
            assert agent in desc, f"{where} description omits {agent}"


def docs_name_spec_miner_as_plugin_agent(texts: dict[str, str]) -> list[str]:
    """Every doc names the agent in its namespaced form and never bare. Returns the
    offending lines, empty when the docs are right."""
    bad = []
    for name, text in texts.items():
        if "adw:spec-miner" not in text:
            bad.append(f"{name}: no adw:spec-miner")
        for i, ln in enumerate(text.splitlines(), 1):
            for m in re.finditer(r"(?<![\w:/-])spec-miner(?!\.md)(?![\w-])", ln):
                bad.append(f"{name}:{i}: bare spec-miner at col {m.start()}")
    return bad


def test_docs_name_spec_miner_as_a_plugin_agent():
    texts = {d: (ROOT / d).read_text() for d in DOCS}
    assert docs_name_spec_miner_as_plugin_agent(texts) == []
    agents_line = next(ln for ln in texts["README.md"].splitlines() if ln.startswith("- `agents/`"))
    for f in ("next-implementer.md", "next-reviewer.md", "spec-miner.md"):
        assert f in agents_line, f


def test_docs_check_rejects_a_bare_or_missing_mention():
    bad = docs_name_spec_miner_as_plugin_agent(
        {"a.md": "run spec-miner first", "b.md": "the `adw:spec-miner` agent, file spec-miner.md"}
    )
    assert bad == ["a.md: no adw:spec-miner", "a.md:1: bare spec-miner at col 4"], bad


# ---- chunk placement: chunks are the attended loop's stop boundaries -------------------

PLACEMENT_MARKS = (
    "stop boundar",
    "always-ask",
    "credentials",
    "security posture",
    "data loss",
    "core business logic",
    "schema migration",
    "later steps depend on",
    "`Eval: yes`",
    "five steps",
    "every step sits under",
)


def _chunk_paragraph(doc: str) -> str:
    """The paragraph that defines chunks, so the pins read the rule and not the example."""
    m = re.search(r"\*\*Chunks\*\*[^\n]*(?:\n(?!\n)[^\n]*)*", doc)
    assert m, "no **Chunks** paragraph"
    return " ".join(m.group(0).split())  # the docs wrap at 90 columns; pins read prose


def test_plan_tdd_states_chunk_placement():
    para = _chunk_paragraph((COMMANDS / "plan-tdd.md").read_text())
    for mark in PLACEMENT_MARKS:
        assert mark in para, f"plan-tdd.md chunk paragraph lacks {mark!r}"
    assert "optional" not in para.lower().split("stop boundar")[0]


def test_markdown_skill_states_chunk_placement():
    skill = SKILL.read_text()
    para = re.search(r"Chunks are[^\n]*(?:\n(?!\n)[^\n]*)*", skill)
    assert para, "no 'Chunks are' sentence in the skill"
    text = " ".join(para.group(0).split())
    for mark in ("stop boundar", "always-ask", "`Eval: yes`", "five", "every step sits under"):
        assert mark in text, f"SKILL.md chunk paragraph lacks {mark!r}"


@pytest.mark.parametrize("path", [COMMANDS / "plan-tdd.md", SKILL], ids=lambda p: p.name)
def test_plan_examples_show_a_second_chunk_with_a_step_under_it(path):
    example = first_markdown_block(path.read_text(), "## Conventions binding every step")
    assert "## Chunk A" in example and "## Chunk B" in example
    after_b = example.split("## Chunk B", 1)[1]
    assert "### Step " in after_b, "Chunk B holds no step"


# ---- Requirement 9 and 17: the loop is chunk-paced everywhere, and sessions close in order ---

CLOSE_OUT_ORDER = (
    "independent done check",
    "retrospective",
    "Handoff",
    "handoff file",
    "## Kickoff",
    "paste line",
)


def _ordered(text: str, marks: tuple[str, ...]) -> list[str]:
    positions = [(text.find(m), m) for m in marks]
    missing = [m for pos, m in positions if pos < 0]
    assert not missing, f"missing {missing}"
    return [m for _pos, m in sorted(positions)]


def _section(text: str, header: str) -> str:
    start = text.index(header)
    rest = text[start + len(header) :]
    m = re.search(r"\n## ", rest)
    return rest[: m.start()] if m else rest


def _close_out_pins(section: str, where: str):
    assert _ordered(section, CLOSE_OUT_ORDER) == list(CLOSE_OUT_ORDER), where
    assert "pointer style" in section, where
    assert "git log <sha>..HEAD" in section, where
    assert "`status`" in section, where
    assert "stale" in section and "plan wins" in section, where
    assert "No active work" in section, where


def test_methodology_attended_loop_is_chunk_paced():
    doc = (ROOT / "ai-dev-workflow.md").read_text()
    phase3 = _section(doc, "## Phase 3: Execute")
    item6 = next(ln for ln in phase3.splitlines() if ln.startswith("6. "))
    assert "chunk" in item6.lower() and "boundary" in item6.lower()
    assert "Pause for user review, then repeat as directed" not in doc
    assert "a human at every step boundary" not in doc
    assert "chunk boundary" in phase3.lower()


def test_methodology_phase_5_carries_the_close_out_order():
    doc = (ROOT / "ai-dev-workflow.md").read_text()
    _close_out_pins(_section(doc, "## Phase 5: Verify and ship"), "ai-dev-workflow.md Phase 5")


def test_runbook_step_5_is_chunk_paced_and_names_the_mid_chunk_stop():
    doc = (ROOT / "workflow-runbook.md").read_text()
    step5 = _section(doc, "## Step 5: Execute the loop")
    item2 = next(ln for ln in step5.splitlines() if ln.startswith("2. "))
    item3 = next(ln for ln in step5.splitlines() if ln.startswith("3. "))
    assert "chunk" in item2.lower() and "stops" in item2.lower()
    assert "boundary" in item3.lower() and "report" in item3.lower()
    row = next(ln for ln in doc.splitlines() if ln.startswith("| The loop stopped mid-chunk"))
    assert "read the stop reason it printed" in row


def test_runbook_step_7_carries_the_close_out_order():
    doc = (ROOT / "workflow-runbook.md").read_text()
    _close_out_pins(_section(doc, "## Step 7: Close the loop"), "workflow-runbook.md Step 7")


def test_readme_names_the_attended_loop_as_chunk_paced():
    lines = (ROOT / "README.md").read_text().splitlines()
    assert "chunk" in lines[2].lower(), lines[2]
    commands_line = next(ln for ln in lines if ln.startswith("- `commands/`"))
    assert "chunk" in commands_line.lower(), commands_line


# ---- Step 11: commands/next.md is the chained attended loop ---------------------------------

AUTO_SECTION_HEADER = "## `/adw:next auto [N]` unattended"
FIXTURES = ROOT / "tests" / "fixtures"


def _attended_section() -> str:
    """Everything before the auto section: the header, the validate step, the attended loop."""
    return (COMMANDS / "next.md").read_text().split(AUTO_SECTION_HEADER, 1)[0]


def _numbered_items(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if re.match(r"^\d+\. \*\*", ln)]


def _per_step_items() -> list[str]:
    """The attended loop's per-step items, without the items before the first step, whose
    item 1 names the fix round's commands for its resume."""
    sec = _attended_section()
    return _numbered_items(sec[sec.index("**Per step**") :])


def test_next_md_description_says_the_loop_runs_to_the_chunk_end():
    line2 = (COMMANDS / "next.md").read_text().splitlines()[1]
    assert line2.startswith("description:"), line2
    assert "end of the current chunk" in line2 and "stops there" in line2, line2
    assert "one attended step" not in line2, line2


def test_next_md_announces_the_batch_through_the_batch_subcommand():
    sec = _attended_section()
    assert "python3 $PLAN batch prompt_plan.md" in sec
    assert "second line" in sec
    assert "Running Steps 5 to 8, stopping at the end of Chunk B (the reviewer runs there)." in sec
    assert "stopping at the end of the plan" in sec


def test_next_md_per_step_procedure_places_check_begin_and_check():
    items = _per_step_items()
    started = next(
        k for k, it in enumerate(items) if "python3 $PLAN set prompt_plan.md N in-progress" in it
    )
    assert "python3 $AUTO check begin N" in items[started + 1], items[started + 1]
    lint = next(k for k, it in enumerate(items) if it.split("**")[1] == "Lint.")
    check = next(k for k, it in enumerate(items) if "python3 $AUTO check N" in it)
    commit = next(k for k, it in enumerate(items) if "Commit on green" in it)
    assert lint < check < commit, (lint, check, commit)


STOP_ITEM_MARKS = {
    "a": ("red after the second attempt", "failed `check`", "red attempt"),
    "b": ("`BLOCKED`", "apparatus", "decision", "eval apparatus"),
    "c": ("genuine decision", "set prompt_plan.md N blocked-decision", "Decisions"),
    "d": ("dirty tree at step start",),
    "e": ("Critical", "one fix round"),
}


def test_next_md_states_the_batch_rule_and_the_closed_stop_list():
    sec = _attended_section()
    assert "Stop and wait** for the owner's review before the next step" not in sec
    assert "stops at the end of the chunk" in sec or "stops at that boundary" in sec
    body = sec[sec.index("(a) ") :]
    parts = re.split(r"\(([a-e])\) ", body)
    assert parts[1::2] == list("abcde"), parts[1::2]
    texts = dict(zip(parts[1::2], parts[2::2], strict=True))
    for letter, marks in STOP_ITEM_MARKS.items():
        for mark in marks:
            assert mark in texts[letter], f"stop ({letter}) lacks {mark!r}"
    assert "Rulings, Important and Minor findings, and a step completing are never stops" in sec
    assert "never runs past" in sec


def test_next_md_boundary_procedure_reviews_the_chunk_diff_and_reports():
    sec = _attended_section()
    ordered = (
        "git rev-parse HEAD",
        "python3 $PLAN batch prompt_plan.md",
        "**At the chunk boundary.**",
        "python3 $AUTO review-package N --base",
        "`adw:next-reviewer`",
    )
    assert _ordered(sec, ordered) == list(ordered)
    boundary = sec[sec.index("**At the chunk boundary.**") :]
    for mark in (
        "Critical",
        "one fix round",
        "Findings",
        "steps done",
        "evidence",
        "reviewer verdict",
        "stop reason",
    ):
        assert mark in boundary, f"boundary paragraph lacks {mark!r}"


def test_next_md_attended_section_carries_the_ruling_versus_decision_rule():
    sec = _attended_section()
    para = sec[sec.index("**Rulings versus decisions.**") :]
    for mark in (
        "Ruling:",
        "cost",
        "a design fork the spec does not answer",
        "a keep-or-delete question",
        "credentials, security posture, or data loss",
    ):
        assert mark in para, f"ruling-versus-decision paragraph lacks {mark!r}"


def test_next_md_auto_section_is_byte_identical_to_the_pin():
    doc = (COMMANDS / "next.md").read_text()
    pin = (FIXTURES / "next_md_auto_section.md").read_text()
    assert doc[doc.index(AUTO_SECTION_HEADER) :] == pin


# ---- Step 12, 2026-09-18: one operation per call, the attended commit, the allow rules ----

AGENTS = ROOT / "agents"
RUNBOOK = ROOT / "workflow-runbook.md"
ALLOW_RULES_HEADER = "## Allow rules for the attended loop in auto mode"


def chained_script_calls(text: str) -> list[str]:
    """Lines inside ```bash fences that call $AUTO or $PLAN and join another program with
    &&, ;, or | outside quotes. Requirement 18: one git or gh operation per Bash call."""
    bad = []
    for fence in re.findall(r"```bash\n(.*?)```", text, re.S):
        for ln in fence.splitlines():
            if "$AUTO" not in ln and "$PLAN" not in ln:
                continue
            unquoted = re.sub(r"'[^']*'|\"[^\"]*\"", "", ln)
            if "&&" in unquoted or ";" in unquoted or "|" in unquoted:
                bad.append(ln.strip())
    return bad


def test_command_texts_issue_one_operation_per_call():
    for path in (COMMANDS / "next.md", AGENTS / "next-implementer.md"):
        assert chained_script_calls(path.read_text()) == [], path.name


def test_one_operation_checker_catches_a_chained_sample():
    sample = (
        "```bash\n"
        "python3 $AUTO check 3 && uv run pytest -q\n"
        "python3 $PLAN set prompt_plan.md 3 done; git status\n"
        "python3 $AUTO gate 1 | tail -1\n"
        "printf 'a && b' > /tmp/note.txt\n"
        "python3 $PLAN append prompt_plan.md decisions --note-file '/tmp/a;b'\n"
        "```\n"
        "python3 $AUTO check 3 && uv run pytest -q outside any fence\n"
    )
    bad = chained_script_calls(sample)
    assert bad == [
        "python3 $AUTO check 3 && uv run pytest -q",
        "python3 $PLAN set prompt_plan.md 3 done; git status",
        "python3 $AUTO gate 1 | tail -1",
    ]


def test_next_md_attended_commit_goes_through_the_script():
    sec = _attended_section()
    assert "python3 $AUTO commit N" in sec
    start = sec.index("**Commit on green.**")
    commit_item = sec[start : sec.index("**Next step.**", start)]
    assert "python3 $AUTO commit N" in commit_item
    assert "git commit" not in commit_item


def test_runbook_lists_the_auto_mode_allow_rules():
    text = RUNBOOK.read_text()
    assert ALLOW_RULES_HEADER in text
    section = _section(text, ALLOW_RULES_HEADER)
    # the rules are the list items; the prose names the broad rules auto mode drops
    rules = re.findall(r"^- `Bash\((.*?)\)`$", section, re.M)
    assert rules, "no Bash(...) rules in the section"
    subs = (
        "check", "commit", "review-package", "merge", "verdict", "record-commit", "pr-opened",
        "done-verdict",
    )  # fmt: skip
    for sub in subs:
        assert any(
            r.startswith("python3 <installPath>/scripts/next_auto.py ") and f" {sub}:*" in r
            for r in rules
        ), sub
    for sub in ("validate", "next", "batch", "set", "append"):
        assert any(
            r.startswith("python3 <installPath>/scripts/next_plan.py ") and f" {sub}:*" in r
            for r in rules
        ), sub
    assert any(r.startswith("uv run pytest") for r in rules)
    assert any(r.startswith("uv run ruff check .") for r in rules)
    assert any(r.startswith("gh pr create ") for r in rules)
    # no interpreter wildcard and no relative script path: every rule is a full-path form
    assert not any(re.match(r"python3?\*|python\*", r) for r in rules), rules
    assert not any("$AUTO" in r or "$PLAN" in r or "${CLAUDE_PLUGIN_ROOT}" in r for r in rules)
    assert "claude plugin list --json" in section and "installPath" in section
    assert "Write tool" in section and "never a Bash redirect" in section
    lowered = section.lower()
    assert "~/.claude" in section and "memory" in lowered and "handoff" in lowered


# ---- Step 13, 2026-09-18: the 60 percent context stop and the assertion-rewrite weight ----

NO_PCT_LINE = "No context percentage in the stamp; the chunk cap alone applies."


def test_next_md_announcement_passes_the_context_percentage_when_the_stamp_has_one():
    sec = _attended_section()
    announce = sec[sec.index("**Announce the batch.**") : sec.index("**Per step**")]
    assert "--context-pct" in announce
    assert "stamp" in announce and "percentage" in announce
    assert NO_PCT_LINE in announce


def test_next_md_stop_list_gains_the_context_stop():
    sec = _attended_section()
    body = sec[sec.index("(a) ") :]
    parts = re.split(r"\(([a-f])\) ", body)
    assert parts[1::2] == list("abcdef"), parts[1::2]
    f_text = dict(zip(parts[1::2], parts[2::2], strict=True))["f"]
    for mark in (
        "step boundary",
        "latest stamp",
        "`context at N percent`",
        "60",
        "remember plugin",
        "`${TMPDIR}/claude-ctx-pct`",
        "statusline",
    ):
        assert mark in f_text, f"stop (f) lacks {mark!r}"
    # the stop reads the percentage at the boundary between steps, never mid-step
    assert "stops at that boundary" in f_text


def test_reviewer_weighs_an_assertion_rewrite_as_at_least_important():
    text = (AGENTS / "next-reviewer.md").read_text()
    # the agent file is hard-wrapped, so compare on collapsed whitespace
    section = " ".join(_section(text, "## Severity vocabulary").split())
    assert "rewrites existing test assertions" in section
    assert "at least Important" in section
    assert "whatever its size" in section
    assert "mutation checks" in section
    assert "edits the assertion to match" in section or "editing the assertion to match" in section


# ---- Step 16: merge text in the loop and the runbook --------------------------------------


def test_next_md_boundary_writes_the_verdict_record_for_merge():
    """Requirement 19: `merge <n>` needs a verdict record for the PR head, and the chunk
    boundary is where the reviewer's Approved is known. The loop writes it with the Write
    tool (a Bash redirect under .next-auto/ would route to the classifier) and the record
    carries the three keys merge reads."""
    sec = _attended_section()
    start = sec.index("**At the chunk boundary.**")
    boundary = sec[start:]
    assert ".next-auto/attended/verdict-<sha>.json" in boundary
    assert "Write tool" in boundary
    assert "`Approved`" in boundary
    for key in ('"head"', '"verdict"', '"reviewer"'):
        assert key in boundary, key
    assert "python3 $AUTO merge <n>" in boundary
    assert "the owner" in boundary and "by number" in boundary


def test_runbook_merge_item_names_the_gate_and_the_owners_acts():
    doc = RUNBOOK.read_text()
    step5b = _section(doc, "## Step 5b: Unattended execution")
    item8 = next(ln for ln in step5b.splitlines() if ln.startswith("8. "))
    assert "python3 <installPath>/scripts/next_auto.py merge <n>" in item8
    assert "its own Bash call" in item8
    assert "refusal" in item8 and "does not own" in item8 and "`--admin`" in item8
    assert "by PR number" in item8
    assert "verdict" in item8 and "done check" in item8 and "before" in item8
    assert "--auto" in item8


def test_no_command_text_carries_an_admin_merge():
    """The classifier's Merge Without Review rule names the --admin arm as the bypass;
    every --admin merge is the owner's act by number, so no command or agent text may type
    it (spec Requirement 19, rejected alternatives)."""
    for folder in (COMMANDS, AGENTS):
        for path in sorted(folder.glob("*.md")):
            assert "--admin" not in path.read_text(), path


# ---- 2026-09-23: a split renumbers only the PENDING steps behind it (the owner's ruling) ----

RENUMBER_SURFACES = [COMMANDS / "plan-tdd.md", SKILL]


@pytest.mark.parametrize("path", RENUMBER_SURFACES, ids=lambda p: p.name)
def test_plan_surfaces_state_the_renumbering_rule(path):
    """The validator requires strictly increasing step numbers and next runs in number order,
    so a mid-plan split must renumber (project-b's store plan did, 2026-09-21 and 2026-09-23), while
    the grammar said never. The owner ruled on 2026-09-23: only the PENDING steps behind the split
    move, every cross-reference follows, the mapping goes to the Findings legacy step map line,
    and a DONE step's number never changes because commit subjects, PR titles, and handoffs cite
    it."""
    text = " ".join(path.read_text().split())  # the docs wrap at 90 columns; pins read prose
    for needle in (
        "renumbers only the PENDING steps behind it",
        "every cross-reference",
        "`- Legacy step map:`",
        "A DONE step's number never changes",
        "commit subjects, PR titles, and handoffs cite it",
        "creating that line on the first renumber",
    ):
        assert needle in text, f"{path.name} lacks {needle!r}"


@pytest.mark.parametrize("path", RENUMBER_SURFACES, ids=lambda p: p.name)
def test_no_plan_surface_says_steps_are_never_renumbered(path):
    text = " ".join(path.read_text().split()).lower()
    for stale in ("never renumber", "nothing is ever renumbered"):
        assert stale not in text, f"{path.name} still says {stale!r}"


# ---- 2026-09-23: the planner sizes steps against the gate's per-step cap ----


def test_plan_tdd_names_the_gate_step_cap():
    """Two project-b steps planned without the number failed next_auto's size gate with every
    other check green (749 and 422 changed lines). The command must state the cap the gate
    enforces, taken from check_step_size's default so the doc and the gate cannot drift."""
    limit = inspect.signature(next_auto.check_step_size).parameters["limit"].default
    doc = (COMMANDS / "plan-tdd.md").read_text()
    sec = doc.split("## Step size", 1)[1].split("\n## ", 1)[0]
    assert f"{limit} changed lines" in sec
    assert "tests included" in sec
    for needle in ("30 changed lines per test", "at most five tests", "Split any step"):
        assert needle in sec, needle
    assert "Never split by deferring the tests" in sec


# ---- ai-dlc-record: the delivery record's call points in the loop text (R11 to R13, R16) ----


def _auto_section() -> str:
    doc = (COMMANDS / "next.md").read_text()
    return doc[doc.index(AUTO_SECTION_HEADER) :]


def test_r11_next_md_unattended_green_sets_the_step_done_before_commit():
    """ai-dlc-record R11: commit K reads the step's note for the record's mutation value, so
    the unattended loop sets the step done before it commits, as the attended loop does."""
    green = next(ln for ln in _auto_section().splitlines() if ln.startswith("4. **Green:**"))
    assert green.index("python3 $AUTO set K done --note-file") < green.index(
        "python3 $AUTO commit K"
    ), green


def test_r12_next_md_attended_boundary_records_every_round_and_commits_the_record():
    """ai-dlc-record R11, R12: the boundary records each reviewer round through `verdict`,
    commits the record alone after the last line and after the done check's line, and records
    the pull request through `pr-opened` after `gh pr create`."""
    sec = _attended_section()
    boundary = sec[sec.index("**At the chunk boundary.**") :]
    ordered = (
        "python3 $AUTO review-package N --base",
        "python3 $AUTO verdict N --head",
        "python3 $AUTO record-commit",
        "python3 $AUTO pr-opened <n>",
    )
    assert _ordered(boundary, ordered) == list(ordered)
    for mark in (
        "every round",
        "`Approved`",
        "`NeedsFixes`",
        "All findings addressed, no new Critical",
        "Findings remain open",
        ".next-auto/attended/review-<sha>-<round>.md",
        '"model"',
        '"round"',
        "`done.verdict`",
        "gh pr create",
        "record-only",
    ):
        assert mark in boundary, f"boundary paragraph lacks {mark!r}"


def test_r12_next_md_attended_commit_names_the_result_file_and_the_record_failure():
    sec = _attended_section()
    check = next(it for it in _per_step_items() if "python3 $AUTO check N" in it)
    assert ".next-auto/attended/result-N.json" in check
    start = sec.index("**Commit on green.**")
    commit_item = sec[start : sec.index("**Next step.**", start)]
    for mark in ("`step.done`", "`COMMIT REFUSED`", "RECORD FAILED", "never re-run"):
        assert mark in commit_item, mark
    done = sec[sec.index("**Mark it done**") : start]
    assert "`mutation killed`" in done


def test_r12_next_md_clean_tree_checks_leave_the_record_file_alone():
    """ai-dlc-record R12: a pending record line is not dirt, at the batch start or per step."""
    sec = _attended_section()
    items = [ln for ln in sec.splitlines() if "**Clean-tree check.**" in ln]
    assert len(items) == 2, items
    for item in items:
        assert "`docs/records/<slug>.md`" in item and "`Record`" in item, item


def test_r12_next_md_unattended_run_records_each_round_and_the_pull_request():
    auto = _auto_section()
    for mark in (
        "python3 $AUTO verdict K --head",
        "<run-dir>/verdict-K-<round>.json",
        "review-K-<round>.md",
        "<run-dir>/result-K.json",
        "`pr.opened`",
        "`chore(record): <slug>`",
        "RECORD FAILED",
    ):
        assert mark in auto, mark
    stopping = auto[auto.index("**Stopping.**") :]
    ordered = ("python3 $AUTO record-commit", "python3 $AUTO wrap-up")
    assert _ordered(stopping, ordered) == list(ordered)


def test_r13_next_md_check_begin_and_start_name_the_plan_approval():
    """ai-dlc-record R13: with Record set, no step runs before the plan's approval is on the
    record; the loop never writes that approval itself."""
    begin = next(
        it for it in _numbered_items(_attended_section()) if "python3 $AUTO check begin N" in it
    )
    for mark in ("`Record`", "`plan.approved`", "hash", "never appends"):
        assert mark in begin, mark
    auto = _auto_section()
    start = auto[auto.index("**Start.**") : auto.index("**Per step**")]
    for mark in ("`Record`", "`plan.approved`", "`START FAILED`"):
        assert mark in start, mark


def test_r16_next_md_preflight_names_both_owners():
    auto = _auto_section()
    assert "the `origin` owner is one of `ADW_OWNERS`" in auto
    assert "`ryanmat/<repo>`" not in auto


def test_r12_next_md_fix_rounds_re_begin_the_step_in_both_modes():
    """ai-dlc-record R11, R12: a fix round re-begins the step at its commit, and its commit
    appends its own step.done; in a run, a failed fix round drops the whole step."""
    sec = _attended_section()
    boundary = sec[sec.index("**At the chunk boundary.**") :]
    fix = boundary[boundary.index("one fix round") :]
    ordered = ("python3 $AUTO check begin N", "python3 $AUTO check N", "python3 $AUTO commit N")
    assert _ordered(fix, ordered) == list(ordered)
    assert "`step.done`" in fix
    item7 = next(ln for ln in _auto_section().splitlines() if ln.startswith("7. "))
    fix = item7[item7.index("one fix round") :]
    ordered = (
        "python3 $AUTO step-begin K",
        "python3 $AUTO gate K",
        "python3 $AUTO set K done",
        "python3 $AUTO commit K",
    )
    assert _ordered(fix, ordered) == list(ordered)
    assert "whole step" in fix


def test_r12_next_md_names_the_record_checks_and_the_counted_runners():
    """ai-dlc-record R11, R12: check and gate refuse an edited record, the counts read pytest,
    node and Bun summaries, check reads the step against its recorded base, and pr-opened reads
    the PR's head from GitHub."""
    sec = _attended_section()
    check = next(it for it in _per_step_items() if "python3 $AUTO check N" in it)
    for mark in (
        "pytest",
        "node test runner",
        "Bun's",
        "any other runner records zeros",
        "against the recorded base, not HEAD",
        "changed since",
    ):
        assert mark in check, mark
    gate = next(ln for ln in _auto_section().splitlines() if ln.startswith("3. "))
    assert "the delivery record byte-identical to its step-begin snapshot" in gate
    boundary = sec[sec.index("**At the chunk boundary.**") :]
    assert "headRefOid" in boundary


def test_r11_next_md_names_the_test_files_key_and_its_platform_source():
    doc = (COMMANDS / "next.md").read_text()
    line = next(ln for ln in doc.splitlines() if "`Test-files`" in ln)
    for mark in ("prove-red", "glob", "`projects.yml`", "`ai_dlc.test_patterns`"):
        assert mark in line, mark


def test_r12_next_md_names_the_done_file_the_checked_tree_and_the_base_appender():
    """ai-dlc-record R11, R12: the done check's own file and its append, the commit refusal for
    a changed tree, the base appender, the verdict chain, and the skipped pr.opened."""
    sec = _attended_section()
    boundary = sec[sec.index("**At the chunk boundary.**") :]
    done = json.loads((FIXTURES / "record-evidence" / "done.json").read_text())
    for mark in (
        ".next-auto/attended/done-<sha>.json",
        "done-<sha>.md",
        "`APPROVED` or `REJECTED`",
        "python3 $AUTO done-verdict --head",
        "`done.verdict`",
        *(f'"{key}"' for key in done),
    ):
        assert mark in boundary, mark
    ordered = ("python3 $AUTO done-verdict --head", "python3 $AUTO record-commit")
    assert _ordered(boundary[boundary.index("done-<sha>.json") :], ordered) == list(ordered)
    assert "any commit from the PR head" in boundary
    start = sec.index("**Commit on green.**")
    commit_item = sec[start : sec.index("**Next step.**", start)]
    assert "the tree changed since check N passed" in commit_item
    doc = (COMMANDS / "next.md").read_text()
    assert "as committed on origin's default branch" in doc
    assert "RECORD SKIPPED" in _auto_section()


def test_r12_next_md_says_how_a_rejected_done_check_is_superseded():
    """ai-dlc-record R12: the veto stands until done-verdict runs again at the same sha."""
    sec = _attended_section()
    boundary = sec[sec.index("**At the chunk boundary.**") :]
    line = next(s for s in boundary.split(". ") if "superseded" in s)
    for mark in ("`REJECTED`", "`done-verdict`", "same sha", "overwrites", "record-only"):
        assert mark in boundary[boundary.index(line) :][: len(line) + 200], mark


# ---- ai-dlc-record lane B: the Stop hook's registration and the reviewer's report (R14, R15) --

REVIEWER = ROOT / "agents" / "next-reviewer.md"


def _fenced_after(text: str, marker: str) -> str:
    """The first ``` block after `marker`."""
    rest = text[text.index(marker) :]
    return re.findall(r"```\n(.*?)```", rest, re.S)[0]


def test_r14_hooks_json_registers_the_stop_gate_as_the_spec_shows():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    assert hooks["Stop"] == [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": f'"{PLUGIN_ROOT_VAR}/hooks/stop-gate.py"',
                    "timeout": 600,
                }
            ]
        }
    ]
    assert os.access(ROOT / "hooks" / "stop-gate.py", os.X_OK)
    readme = (ROOT / "README.md").read_text()
    hooks_line = next(ln for ln in readme.splitlines() if ln.startswith("- `hooks/`"))
    assert "stop-gate.py" in hooks_line and "`Stop`" in hooks_line


def test_r15_the_first_round_report_carries_scope_and_requirement_gaps():
    """ai-dlc-record R15: Scope and Requirement gaps sit between Strengths and Findings, each
    scope entry is also an out-of-scope Important finding and each gap a Critical one."""
    text = REVIEWER.read_text()
    report = _fenced_after(text, "## Output")
    order = ("### Strengths", "### Scope", "### Requirement gaps", "### Findings", "### Verdict")
    assert _ordered(report, order) == list(order)
    assert "Step quality: Approved | NeedsFixes" in report
    flat = " ".join(text.split())
    for mark in ("out of scope", "Important", "`gap`", "Critical", "count nothing twice"):
        assert mark in flat, mark


def test_r15_the_re_review_report_carries_the_three_headers():
    rereview = _fenced_after(REVIEWER.read_text(), "## Re-review rounds")
    breakage = rereview[rereview.index("### New breakage in the fix diff") :]
    breakage = breakage[: breakage.index("### Out-of-scope observations")]
    order = ("#### Critical", "#### Important", "#### Minor")
    assert _ordered(breakage, order) == list(order)
    assert "None" in breakage


def test_r15_the_verdict_word_carries_no_whitespace_anywhere_it_is_quoted():
    for path in (REVIEWER, COMMANDS / "next.md", ROOT / "README.md"):
        assert "Needs fixes" not in path.read_text(), path.name


def test_r13_next_md_approves_the_plan_through_plan_approved():
    """ai-dlc-record R13: the approval goes on the record through `plan-approved`, which runs
    the default branch's appender; the loop never runs it on its own."""
    step = next(ln for ln in _per_step_items() if "check begin N" in ln)
    flat = " ".join(step.split())
    assert "python3 $AUTO plan-approved --by <login>" in flat
    assert "only on the owner's word" in flat and "never appends `plan.approved` itself" in flat
    assert "run-less: check, pr-opened, plan-approved" in (COMMANDS / "next.md").read_text()


def test_r14_next_md_marks_the_step_failed_at_the_stop_on_red():
    """ai-dlc-record R14: the loop's stop on red after the second attempt sets the step FAILED
    with its note, so the Stop hook stands down there instead of blocking it."""
    doc = (COMMANDS / "next.md").read_text()
    on_red = doc[doc.index("**On red.**") :]
    on_red = " ".join(on_red[: on_red.index("\n\n")].split())
    assert "python3 $PLAN set prompt_plan.md N failed --note-file /tmp/note.txt" in on_red
    hook = " ".join(doc[doc.index("The plugin's Stop hook") :].split("\n\n", 1)[0].split())
    assert "a step that is not `IN PROGRESS`" in hook


def test_next_md_marks_an_apparatus_stop_on_the_plan():
    """ai-dlc-record R14, states table row S6: the loop marks the step blocked-apparatus when
    it stops on an apparatus code or CHECK APPARATUS, so the Stop hook stands down there as it
    does at the stop on red and the stop on a decision."""
    doc = (COMMANDS / "next.md").read_text()
    assert "python3 $PLAN set prompt_plan.md N blocked-apparatus --note-file" in doc
    item_13 = doc[doc.index("13. **Commit on green.**") :].split("14. **Next step.**")[0]
    item_13 = " ".join(item_13.split())
    assert "`COMMIT APPARATUS`" in item_13 and "stop (b)" in item_13


def test_r17_every_hook_command_quotes_the_plugin_root():
    """ai-dlc-record R17 and the Interfaces registration: each command in hooks.json quotes
    ${CLAUDE_PLUGIN_ROOT}, so a plugin path with a space stays one word and the validator's
    strict mode passes; checked here too because CI has no CLI to run the validator."""
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    commands = [h["command"] for entries in hooks.values() for e in entries for h in e["hooks"]]
    assert len(commands) == 5
    for command in commands:
        assert command.count(PLUGIN_ROOT_VAR) == 1, command
        assert command.startswith(f'"{PLUGIN_ROOT_VAR}/') and command.endswith('"'), command


def test_next_md_marks_the_item_1_stop_at_an_open_step_blocked_decision():
    """ai-dlc-record R14, states table row L3: before the first step, a dirty tree whose first
    open step is IN PROGRESS is that step's earlier work, and the loop records the stop as stop
    (c), resume or revert, so the Stop hook stands down there."""
    doc = (COMMANDS / "next.md").read_text()
    before = doc[doc.index("**Before the first step.**") :]
    item_1 = " ".join(before.split("2. **Record the batch base.**")[0].split())
    assert "first open step is `IN PROGRESS`" in item_1
    assert "stop (c)" in item_1
    assert "python3 $PLAN set prompt_plan.md N blocked-decision --note-file /tmp/note.txt" in item_1
    assert "`Decision: resume or revert step N's earlier work`" in item_1
    # a resume records the batch base the boundary reads, then continues the step where it is:
    # at item 5 on its check record, at item 4 when check begin never wrote one
    assert "record the batch base as item 2 does" in item_1
    resume = "at per-step item 5 when its check record exists, or at item 4 when it has none"
    assert resume in item_1


def test_plan_tdd_names_the_test_repo_lines_and_when_a_step_carries_them():
    """The Test-repo exemption (adw 3.4.8): the planner must know the two paired lines, when
    a step carries them, and what such a step's Completion names."""
    doc = (COMMANDS / "plan-tdd.md").read_text()
    para = doc.split("Each step carries three metadata lines", 1)[1]
    para = para.split("**Trailing sections.**", 1)[0]
    for needle in (
        "`Test-repo:",
        "`Test:",
        "every file the step touches lives in another repository",
        "names that repository's own test command",
        "refuses either line without the other",
    ):
        assert needle in para, needle


def test_next_md_sets_done_through_the_engine_and_names_the_exemption():
    """adw 3.4.8: item 12 marks a step done through next_auto.py, which reads the check record
    and appends the evidence clause; item 11 names the Test-repo exemption in one sentence."""
    doc = (COMMANDS / "next.md").read_text()
    assert "python3 $AUTO set N done --note-file" in doc
    assert "$PLAN set prompt_plan.md N done" not in doc
    item_11 = doc.split("11. **Check.**", 1)[1].split("12. **Mark it done**", 1)[0]
    assert "Test-repo" in item_11


def test_next_md_item_12_names_the_clause_and_the_three_refusal_forms():
    """adw 3.4.8 Step 8: item 12 names the appended evidence clause and the three ways
    `set N done` refuses (a conflict, an unreadable record, and the gap form a step with no
    result file needs)."""
    doc = (COMMANDS / "next.md").read_text()
    item_12 = doc.split("12. **Mark it done**", 1)[1].split("13. **Commit on green.**", 1)[0]
    assert "check: <passed> passed of <collected> collected at base <head8>" in item_12
    for needle in ("SET REFUSED", "SET APPARATUS", "no check N: <why>"):
        assert needle in item_12, needle


def test_next_md_item_12_names_the_focused_run_note_form():
    """the owner's ruling of 2026-10-11 on Chunk C Important 2: a focused run's count never uses
    the four count forms the scan judges; item 12 gives the form that does not."""
    doc = (COMMANDS / "next.md").read_text()
    item_12 = doc.split("12. **Mark it done**", 1)[1].split("13. **Commit on green.**", 1)[0]
    assert "of the step's tests pass" in item_12


# ---- adw 3.5.0 spec item 12: the reviewer quotes a span under each finding ----------------


def test_reviewer_quotes_the_span_each_finding_is_about():
    """verdict N keeps a finding only when its `> ` span occurs in the named file at the
    reviewed head, so the reviewer's Output asks for one under every finding, first round and
    re-review alike."""
    text = REVIEWER.read_text()
    output = " ".join(_section(text, "## Output").split())
    assert "starts with the repository-relative `file:line`" in output
    assert "an indented `> ` line under it" in output
    assert "copied from that file as it reads at the reviewed head" in output
    assert "### Dropped findings" in output
    report = _fenced_after(text, "## Output")
    assert "  > <the code or text the finding is about, copied from that file>" in report
    rereview = " ".join(_section(text, "## Re-review rounds").split())
    assert "takes the first round's form" in rereview


# ---- adw 3.5.0: the command text names the new lines (Step 16; spec items 1 to 9) ---------


def _flat(name):
    return " ".join((COMMANDS / name).read_text().split())


@pytest.mark.parametrize(
    "needle",
    [
        "`Issue: <id>`, one tracker id such as `DOC-123`, "
        "when the plan's work is tracked by one issue",
        "`Closes: <ids>` when the step closes requirement ids of the spec",
        "`Deletes: <paths>` when the step deletes test files",
        "`Guard-mutation: <path>: <old> => <new>` when the step's tests pass at its base",
    ],
    ids=["Issue", "Closes", "Deletes", "Guard-mutation"],
)
def test_plan_tdd_md_names_each_new_line_and_when_it_is_written(needle):
    assert needle in _flat("plan-tdd.md")


@pytest.mark.parametrize(
    "needle",
    [
        "Lint is judged as the gate judges it",
        "red_reason: comment-only fix round",
        "the step's `Guard-mutation:` line",
        "the Holdout's hash against the one step-begin recorded",
        "`Spec:`, `Closes:` and `Approved-by:`",
        "`<appender> sync <slug>`",
        "reads no other work tree",
    ],
    ids=["lint", "comment-only", "guard-mutation", "holdout", "body-lines", "sync", "own-tree"],
)
def test_next_md_names_the_new_checks_and_body_lines(needle):
    assert needle in _flat("next.md")


# ---- adw 3.5.2: plan-tdd.md writes the Size: line and the rule, next.md names the lines and
# the preflight reason (spec items 6 and 7, R6, R7) -----------------------------------------


def _step_size_section() -> str:
    doc = (COMMANDS / "plan-tdd.md").read_text()
    return " ".join(doc.split("## Step size", 1)[1].split("\n## ", 1)[0].split())


def test_plan_tdd_grammar_block_shows_the_size_line_after_mutation():
    block = first_markdown_block((COMMANDS / "plan-tdd.md").read_text(), "## Conventions binding")
    assert "Mutation: required\nSize: <n>\n" in block
    assert block.count("\nSize: <n>\n") == 2


@pytest.mark.parametrize(
    "needle",
    [
        "every step carries `Size:`",
        "about 2.5 times the function's lines",
        "`Step-size:`",
        "invalid, not merely oversized",
        "with the reason in the Conventions prose",
    ],
    ids=["size", "move-rule", "step-size", "invalid", "reason"],
)
def test_plan_tdd_step_size_section_states_the_rule(needle):
    assert needle in _step_size_section(), needle


@pytest.mark.parametrize("figure", [194, 283, 422, 379, 424, 471, 749])
def test_plan_tdd_step_size_section_keeps_every_measured_figure(figure):
    assert re.search(rf"\b{figure}\b", _step_size_section()), figure


def test_plan_tdd_step_size_section_lists_the_five_factory_failures_together():
    """422 is both a measured step and the Step 36 failure, so the list is asserted whole."""
    assert "at 379, 422, 424, 471 and 749 changed lines" in _step_size_section()


def test_plan_tdd_optional_lines_paragraph_says_size_is_not_one_of_them():
    flat = _flat("plan-tdd.md")
    assert "`Size: <n>` is not optional: every step carries it after `Mutation:`" in flat


def test_next_md_gate_item_names_the_two_size_lines_once_each():
    item = _auto_section().split("3. `python3 $AUTO gate K`", 1)[1].split("\n4. ", 1)[0]
    assert item.count("`Size: <n>`") == 1
    assert item.count("`Step-size: <n>`") == 1


def test_next_md_preflight_paragraph_carries_the_unsized_reason():
    para = _auto_section().split("**Preflight.**", 1)[1].split("**Start.**", 1)[0]
    assert "`plan: Step N carries no Size: line; auto runs need the estimate`" in para


# ---- adw 3.5.8: R16 names the log, the skip, the Shares line and the per-unit preflight ----


def _next_md_item(number: int, title: str) -> str:
    """One numbered item of next.md's per-step list, flattened, from its bold title line."""
    for line in (COMMANDS / "next.md").read_text().splitlines():
        if line.startswith(f"{number}. **{title}**"):
            return " ".join(line.split())
    raise AssertionError(f"no item {number} titled {title}")


def _stop_hook_paragraph() -> str:
    para = next(
        ln for ln in (COMMANDS / "next.md").read_text().splitlines()
        if ln.startswith("The plugin's Stop hook")
    )  # fmt: skip
    return " ".join(para.split())


@pytest.mark.parametrize(
    "needle",
    [
        "It judges the session's own tree alone and reads no other work tree",
        "A stop whose tree hashes as `check N` hashes it to the live record's `passed_tree` is "
        "allowed with no Test run",
        "a live record with no `passed_tree` runs it as before",
    ],
    ids=["own-tree", "skip", "no-passed-tree"],
)
def test_r16_the_stop_hook_paragraph_names_the_tree_rule_and_the_skip(needle):
    assert needle in _stop_hook_paragraph()


def test_r16_the_stop_hook_paragraph_drops_the_worktree_fallback():
    para = _stop_hook_paragraph()
    assert "`git worktree list` order" not in para
    assert "in its block line" not in para


@pytest.mark.parametrize(
    "needle",
    [
        "removes every earlier `check-N.log`",
        "refuses, exit 1 with one line naming the tree and the class, when another work tree of "
        "the repository has a live plan that shares a class with this one",
    ],
    ids=["log", "shares"],
)
def test_r16_item_4_names_the_log_and_the_shares_refusal(needle):
    assert needle in _next_md_item(4, "Record the base.")


@pytest.mark.parametrize(
    "needle",
    [
        "writes the Test command's output, pass or fail, to `.next-auto/attended/check-N.log`",
        "the external command and its exit code on its first line",
        "records `passed_tree`",
    ],
    ids=["log", "exempt-log", "passed-tree"],
)
def test_r16_item_11_names_the_log_and_the_checked_tree(needle):
    assert needle in _next_md_item(11, "Check.")


def test_r16_the_preflight_paragraph_refuses_only_the_same_units_pull_request():
    doc = _flat("next.md")
    assert (
        "no open PR labeled `next-auto` is on a branch of this plan's unit, one beginning "
        "`next-auto/<issue>-` with the plan's `Issue` lowercased, or any open one when the plan "
        "has no `Issue`"
    ) in doc


def test_r16_start_names_the_shares_refusal():
    assert "`start` refuses the same way before it reaches GitHub" in _flat("next.md")


@pytest.mark.parametrize(
    "needle",
    [
        "Add `Shares: <class>[, <class>]` when the plan's steps touch a resource another live "
        "plan could also be building",
        "`openapi`, `dapi`, `migration`, `infra` and `docs`",
        "`docs` never counts",
    ],
    ids=["line", "classes", "docs"],
)
def test_r16_plan_tdd_md_names_the_shares_line(needle):
    assert needle in _flat("plan-tdd.md")


def test_r16_item_4_sets_a_refused_step_back_to_pending():
    """rul-3c4db5a3fb, fix round: an IN PROGRESS step counts as live, so a pane refused at its
    begin sets the step back, or it refuses the other pane's next begin."""
    item = _next_md_item(4, "Record the base.")
    assert (
        "On that refusal a step item 3 marked started goes back with "
        "`python3 $PLAN set prompt_plan.md N pending` and the loop stops, naming the other tree"
    ) in item
    # round 3: the boundary fix round begins a DONE step with no item 3, and a PENDING step
    # would stand the Stop hook down for the whole fix
    assert "A fix round's `DONE` step stays `DONE`" in item


def test_r16_start_names_the_issue_in_the_branch_the_preflight_rule_reads():
    doc = _flat("next.md")
    assert (
        "on `next-auto/<issue>-<slug>-<run-id>`, the `<issue>-` there only when the plan carries "
        "`Issue`"
    ) in doc
