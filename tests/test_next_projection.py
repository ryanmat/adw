# Description: Tests for next_projection.py (Requirements 9 to 11 and 13 to 16): the build through
# Description: a temp file, tables, status, parsers, derivations, queries. Real git; saved gh reads.

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import next_plan
import next_projection
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "next_projection.py"


def git_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def run_script(*args, project_env=None):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if project_env is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env
    )


def store_of(project):
    return project / ".next-auto" / "projection.sqlite"


def tables(store):
    with sqlite3.connect(store) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {name for (name,) in rows}


def test_build_over_an_empty_repository_writes_the_store_with_its_tables(tmp_path):
    target = git_repo(tmp_path / "target")
    project = tmp_path / "project"
    r = run_script("build", "--target", str(target), "--project", str(project))
    assert (r.returncode, r.stderr) == (0, "")
    assert r.stdout == f"BUILT {store_of(project)}\n"
    assert tables(store_of(project)) == {"node", "node_source", "edge", "watermarks"}


def test_an_edge_with_the_extracted_method_is_refused(tmp_path):
    project = tmp_path / "project"
    next_projection.build(git_repo(tmp_path / "target"), project)
    row = "INSERT INTO edge VALUES ('commit:a', 'commit:b', 'parent', ?, NULL, 'records', 'x', 't')"
    with sqlite3.connect(store_of(project)) as conn:
        for method in ("observed", "declared", "derived"):
            conn.execute(row, (method,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(row, ("extracted",))


def test_put_edge_keeps_a_repeated_edge_once_and_refuses_a_method_outside_the_three(tmp_path):
    conn = sqlite3.connect(tmp_path / "store.sqlite")
    conn.executescript(next_projection.SCHEMA)
    origin = next_projection.Origin("records", "x", "t")
    for _ in range(2):
        next_projection.put_edge(conn, origin, "commit:a", "commit:b", "parent", "t")
    assert conn.execute("SELECT count(*) FROM edge").fetchone() == (1,)
    with pytest.raises(sqlite3.IntegrityError):
        next_projection.put_edge(conn, origin, "commit:a", "commit:b", "parent", "t", "extracted")


def test_a_node_outside_the_seventeen_types_is_refused(tmp_path):
    project = tmp_path / "project"
    next_projection.build(git_repo(tmp_path / "target"), project)
    row = "INSERT INTO node VALUES (?, 'k', '{}')"
    assert len(next_projection.NODE_TYPES) == 17
    with sqlite3.connect(store_of(project)) as conn:
        for node_type in next_projection.NODE_TYPES:
            conn.execute(row, (node_type,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(row, ("branch",))


def test_a_failed_build_leaves_the_previous_store_byte_identical_and_no_tmp(tmp_path):
    project = tmp_path / "project"
    next_projection.build(git_repo(tmp_path / "target"), project)
    store = store_of(project)
    with sqlite3.connect(store) as conn:
        conn.execute("INSERT INTO node VALUES ('repo', 'target', '{}')")
    before = store.read_bytes()
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    r = run_script("build", "--target", str(not_a_repo), "--project", str(project))
    assert (r.returncode, r.stdout) == (1, "")
    assert "Traceback (most recent call last)" in r.stderr
    assert "not a git repository" in r.stderr
    assert store.read_bytes() == before
    assert sorted(p.name for p in store.parent.iterdir()) == ["projection.sqlite"]


def test_status_prints_each_source_watermark_or_never_built(tmp_path):
    project = tmp_path / "project"
    r = run_script("status", "--project", str(project))
    assert (r.returncode, r.stdout, r.stderr) == (0, "never built\n", "")
    next_projection.build(git_repo(tmp_path / "target"), project)
    with sqlite3.connect(store_of(project)) as conn:
        conn.execute(
            "INSERT INTO watermarks (source, watermark) VALUES ('specs', '2026-10-20T19:02:11Z')"
        )
        conn.execute(
            "INSERT INTO watermarks (source, watermark) VALUES ('records', '2026-10-21T01:47:30Z')"
        )
    r = run_script("status", project_env=project)
    assert (r.returncode, r.stderr) == (0, "")
    assert r.stdout == "records 2026-10-21T01:47:30Z\nspecs 2026-10-20T19:02:11Z\n"


def test_no_project_dir_is_refused(tmp_path):
    target = git_repo(tmp_path / "target")
    for args in (("build", "--target", str(target)), ("status",)):
        r = run_script(*args)
        assert (r.returncode, r.stdout) == (2, "")
        assert r.stderr == "next_projection: CLAUDE_PROJECT_DIR is unset; pass --project\n"


# Record fixtures, copied line for line. DEMO_RECORD: the header and lines 10 to 12 and 274 to 279
# of docs/records/acme-demo-account.md in acme/platform at 763e5a5e (a plan approval and two
# step.done lines without a plan key; then #472's pr.opened, a later plan approval, ci.passed,
# pr.merged, deploy.completed and deploy.promoted), read 2026-10-22. CONFIG_RECORD: lines 1 to 12
# of docs/records/platform-claude-config.md at acme/platform 10f1502a, whose step.done carries
# plan and runkey in Step 4's form, read 2026-10-22.
RECORD_HEADER = (
    "# Delivery record: {slug}\n\nSpec: docs/specs/{spec}\nSchema: record/1\n"
    "Appended by tools/workflows/record.mjs. Do not edit by hand; `record verify` fails on any"
    " changed line.\n\n## Events\n\n"
)
P1 = "sha256:fdb4d1b417e98639acd6383922cebb1f53f8cff76885f4a00612d229195e8d09"
P2 = "sha256:fd7c88ffef3f6e35f1b86bd45e74a528801fe80fe473c89b7cc28a79a385bcc4"
P3 = "sha256:32dc69d46ad43a43b459d39a651a15ad19d586f0e307397a9edb3cb5203aeb33"
DEMO_RECORD = RECORD_HEADER.format(
    slug="acme-demo-account", spec="acme-demo-account.md"
) + "".join(
    line + "\n"
    for line in (
        f"- 2026-10-02T18:08:02Z plan.approved plan={P1} by=ryanmat",
        "- 2026-10-02T18:13:58Z step.done step=1 sha=1bb183d0307a8c4cb79dd0a7e1b53d9d68cd0c48"
        " mode=attended tests=905/905 red=proved mutation=reported by=ryanmat",
        "- 2026-10-02T18:18:21Z step.done step=2 sha=a078d8ed89e7d63d7698378f50c61afa0694251e"
        " mode=attended tests=906/906 red=proved mutation=reported by=ryanmat",
        "- 2026-10-17T02:42:07Z pr.opened pr=472 head=dff27006fbf2b615b80931ede42533175502b503"
        " mode=attended",
        f"- 2026-10-20T20:17:26Z plan.approved plan={P2} by=ryanmat",
        "- 2026-10-17T03:57:07Z ci.passed pr=472 head=b9d516153e70fa6b187899cc4e123900da21d700"
        " run=39000394687",
        "- 2026-10-17T04:54:34Z pr.merged pr=472 sha=c708ead369d301d4b61d96e5c6ea90bc818ffe5b"
        " by=ryanmat review=none",
        "- 2026-10-17T05:30:28Z deploy.completed sha=c708ead369d301d4b61d96e5c6ea90bc818ffe5b"
        " run=39004573395 conclusion=success",
        "- 2026-10-17T07:08:58Z deploy.promoted target=demo"
        " base=690072b615a60a3c649bc297ab29d19a2b3a78aa run=39010149386 conclusion=success",
    )
)
CONFIG_RECORD = RECORD_HEADER.format(
    slug="platform-claude-config", spec="platform-claude-config/spec.md"
) + "".join(
    line + "\n"
    for line in (
        "- 2026-10-21T03:05:01Z spec.approved decision=keep pr=533"
        " sha=f41a9f98fe4e8a637d668f49d1815ca9bb4ac72d by=ryanmat",
        "- 2026-10-21T15:51:20Z plan.approved"
        " plan=sha256:f017db7b73259ab80b5e69395abef6376db6f379bf357412f1eab667bb522099 by=ryanmat",
        f"- 2026-10-21T18:03:23Z plan.approved plan={P3} by=ryanmat",
        "- 2026-10-21T18:05:30Z step.done step=1 sha=ae668a8d81bb787d0ac95fd39f5a4284e9390ed2"
        " mode=attended tests=700/700 red=proved mutation=reported by=ryanmat"
        f" plan={P3} runkey=b84b6d8a-ca89-4c8f-ab2b-b013afd7f64a",
    )
)


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def record_repo(path, records, commit=True):
    repo = git_repo(path)
    (repo / "docs" / "records").mkdir(parents=True)
    for name, text in records.items():
        (repo / "docs" / "records" / name).write_text(text, encoding="utf-8")
    if commit:
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "records")
    return repo


def built(tmp_path, records, commit=True):
    project = tmp_path / "project"
    next_projection.build(record_repo(tmp_path / "target", records, commit), project)
    return sqlite3.connect(store_of(project))


def nodes(conn, node_type):
    rows = conn.execute("SELECT key FROM node WHERE type = ?", (node_type,)).fetchall()
    return {key for (key,) in rows}


def edges_from(conn, src):
    rows = conn.execute("SELECT dst, type, method, rule FROM edge WHERE src = ?", (src,)).fetchall()
    return set(rows)


def test_a_step_done_with_plan_and_runkey_gives_a_plan_step_with_observed_edges(tmp_path):
    conn = built(tmp_path, {"platform-claude-config.md": CONFIG_RECORD})
    assert f"{P3}/1" in nodes(conn, "plan_step")
    assert edges_from(conn, f"plan_step:{P3}/1") == {
        (f"plan:{P3}", "partOf", "observed", None),
        ("commit:ae668a8d81bb787d0ac95fd39f5a4284e9390ed2", "committedAs", "observed", None),
        ("run:b84b6d8a-ca89-4c8f-ab2b-b013afd7f64a", "ranIn", "observed", None),
    }
    assert {"ae668a8d81bb787d0ac95fd39f5a4284e9390ed2"} <= nodes(conn, "commit")
    assert nodes(conn, "run") == {"b84b6d8a-ca89-4c8f-ab2b-b013afd7f64a"}


def test_a_step_done_without_plan_takes_the_preceding_approval_by_record_order(tmp_path):
    conn = built(tmp_path, {"acme-demo-account.md": DEMO_RECORD})
    assert nodes(conn, "plan_step") == {f"{P1}/1", f"{P1}/2"}
    assert edges_from(conn, f"plan_step:{P1}/1") == {
        (f"plan:{P1}", "partOf", "derived", "record-order"),
        ("commit:1bb183d0307a8c4cb79dd0a7e1b53d9d68cd0c48", "committedAs", "observed", None),
    }
    assert nodes(conn, "plan") == {P1, P2}


def test_deploy_lines_give_deploy_nodes_by_run_and_target_with_their_commits(tmp_path):
    conn = built(tmp_path, {"acme-demo-account.md": DEMO_RECORD})
    assert nodes(conn, "deploy") == {"39004573395/dev", "39010149386/demo"}
    # each the first deploy on its target, so it includes its own commit alone (Step 25)
    assert edges_from(conn, "deploy:39004573395/dev") == {
        ("commit:c708ead369d301d4b61d96e5c6ea90bc818ffe5b", "deploys", "observed", None),
        ("commit:c708ead369d301d4b61d96e5c6ea90bc818ffe5b", "includes", "derived", "deploy-range"),
    }
    assert edges_from(conn, "deploy:39010149386/demo") == {
        ("commit:690072b615a60a3c649bc297ab29d19a2b3a78aa", "deploys", "observed", None),
        ("commit:690072b615a60a3c649bc297ab29d19a2b3a78aa", "includes", "derived", "deploy-range"),
    }


def test_a_line_breaking_the_grammar_stops_the_build_naming_file_line_and_text(tmp_path):
    bad = "- 2026-10-17T05:30:28Z deploy.completed sha=c708ead3 run= conclusion=success"
    target = record_repo(tmp_path / "target", {"acme-demo-account.md": DEMO_RECORD + bad + "\n"})
    project = tmp_path / "project"
    r = run_script("build", "--target", str(target), "--project", str(project))
    assert (r.returncode, r.stdout) == (1, "")
    assert f"docs/records/acme-demo-account.md:18: {bad}" in r.stderr
    assert not store_of(project).exists()


def test_the_watermark_is_the_newest_timestamp_and_rows_carry_the_file_sha(tmp_path):
    conn = built(tmp_path, {"acme-demo-account.md": DEMO_RECORD})
    assert conn.execute("SELECT source, watermark FROM watermarks").fetchall() == [
        ("records", "2026-10-20T20:17:26Z")
    ]
    sha = git(tmp_path / "target", "log", "-1", "--format=%H", "--", "docs/records")
    versions = conn.execute("SELECT DISTINCT source, source_version FROM node_source").fetchall()
    assert versions == [("records", sha)]
    edge_versions = conn.execute("SELECT DISTINCT source, source_version FROM edge").fetchall()
    assert edge_versions == [("records", sha)]


def test_an_untracked_record_is_versioned_by_its_sha256(tmp_path):
    conn = built(tmp_path, {"acme-demo-account.md": DEMO_RECORD}, commit=False)
    digest = hashlib.sha256(DEMO_RECORD.encode("utf-8")).hexdigest()
    versions = conn.execute("SELECT DISTINCT source_version FROM node_source").fetchall()
    assert versions == [(f"sha256:{digest}",)]


def test_a_node_one_source_wrote_twice_keeps_one_provenance_row(tmp_path):
    conn = built(tmp_path, {"acme-demo-account.md": DEMO_RECORD})
    rows = conn.execute(
        "SELECT source FROM node_source WHERE type = 'plan' AND key = ?", (P1,)
    ).fetchall()
    assert rows == [("records",)]


# PLAN_TEXT: exports/platform-claude-config-u3/prompt_plan.md in the Acme workspace on devbox, a
# grammar plan with four DONE steps, Closes: R8 on Step 3 and R9 on Step 4, read 2026-10-22 00:21
# EDT. NO_HEADING: this repository's plan-hash fixture, which carries "## Conventions" and not the
# grammar's heading.
FIXTURES = Path(__file__).parent / "fixtures"
PLAN_TEXT = (FIXTURES / "projection" / "prompt_plan-claude-config-u3.md").read_text(
    encoding="utf-8"
)
NO_HEADING = (FIXTURES / "plan-hash" / "prompt_plan.md").read_text(encoding="utf-8")
PLAN_SLUG = "platform-claude-config"
# 2026-10-21 22:52:55 EDT, the amended delivery projection plan's approval
PLAN_MTIME = 1792637575


def plan_project(tmp_path, plans):
    project = tmp_path / "project"
    for rel, text in plans.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (PLAN_MTIME, PLAN_MTIME))
    return project


def built_plans(tmp_path, plans):
    project = plan_project(tmp_path, plans)
    next_projection.build(git_repo(tmp_path / "target"), project)
    return sqlite3.connect(store_of(project))


def test_a_plan_is_keyed_by_the_engine_plan_hash(tmp_path):
    conn = built_plans(tmp_path, {"prompt_plan.md": PLAN_TEXT})
    assert nodes(conn, "plan") == {next_plan.plan_hash(PLAN_TEXT)}


def test_each_step_gives_a_plan_step_with_its_status_and_declared_closes_edges(tmp_path):
    conn = built_plans(tmp_path, {"prompt_plan.md": PLAN_TEXT})
    h = next_plan.plan_hash(PLAN_TEXT)
    rows = conn.execute("SELECT key, props FROM node WHERE type = 'plan_step' ORDER BY key")
    steps = [(key, json.loads(props)["number"], json.loads(props)["status"]) for key, props in rows]
    assert steps == [(f"{h}/{n}", n, "DONE") for n in (1, 2, 3, 4)]
    assert edges_from(conn, f"plan_step:{h}/3") == {
        (f"plan:{h}", "partOf", "observed", None),
        (f"requirement:{PLAN_SLUG}/R8", "closes", "declared", None),
    }
    assert edges_from(conn, f"plan_step:{h}/4") == {
        (f"plan:{h}", "partOf", "observed", None),
        (f"requirement:{PLAN_SLUG}/R9", "closes", "declared", None),
    }
    assert edges_from(conn, f"plan_step:{h}/1") == {(f"plan:{h}", "partOf", "observed", None)}


def test_two_plans_of_one_slug_each_with_a_step_3_give_two_plan_steps(tmp_path):
    title = "# Alimenob Sofega imamezoluzozu vuda 3 Implementation Plan"
    second = PLAN_TEXT.replace(title, title.replace("vuda 3", "unit 3b"), 1)
    assert second != PLAN_TEXT
    conn = built_plans(tmp_path, {"prompt_plan-u3.md": PLAN_TEXT, "prompt_plan-u3b.md": second})
    steps_3 = {key for key in nodes(conn, "plan_step") if key.endswith("/3")}
    assert steps_3 == {f"{next_plan.plan_hash(PLAN_TEXT)}/3", f"{next_plan.plan_hash(second)}/3"}


def test_a_plan_without_the_heading_is_skipped_and_counted_and_a_rejected_one_stops_the_build(
    tmp_path,
):
    project = plan_project(
        tmp_path, {"prompt_plan.md": PLAN_TEXT, "docs/hash-fixture-plan.md": NO_HEADING}
    )
    target = git_repo(tmp_path / "target")
    r = run_script("build", "--target", str(target), "--project", str(project))
    assert (r.returncode, r.stderr) == (0, "")
    r = run_script("status", "--project", str(project))
    assert r.stdout == "plans 2026-10-22T02:52:55Z skipped 1\n"
    rejected = PLAN_TEXT.replace("### Step 4:", "### Step 3:", 1)
    (project / "docs" / "u3-plan.md").write_text(rejected, encoding="utf-8")
    before = store_of(project).read_bytes()
    r = run_script("build", "--target", str(target), "--project", str(project))
    assert (r.returncode, r.stdout) == (1, "")
    assert "docs/u3-plan.md:92: step numbers must be strictly increasing" in r.stderr
    assert store_of(project).read_bytes() == before


def test_two_identical_copies_of_a_plan_give_one_edge_per_fact(tmp_path):
    conn = built_plans(
        tmp_path, {"prompt_plan.md": PLAN_TEXT, "exports/u3/prompt_plan.md": PLAN_TEXT}
    )
    h = next_plan.plan_hash(PLAN_TEXT)
    rows = conn.execute(
        "SELECT src, dst, type FROM edge WHERE src LIKE ? ORDER BY src, type", (f"plan_step:{h}/%",)
    ).fetchall()
    assert rows == [
        (f"plan_step:{h}/1", f"plan:{h}", "partOf"),
        (f"plan_step:{h}/2", f"plan:{h}", "partOf"),
        (f"plan_step:{h}/3", f"requirement:{PLAN_SLUG}/R8", "closes"),
        (f"plan_step:{h}/3", f"plan:{h}", "partOf"),
        (f"plan_step:{h}/4", f"requirement:{PLAN_SLUG}/R9", "closes"),
        (f"plan_step:{h}/4", f"plan:{h}", "partOf"),
    ]


def test_the_newest_copy_of_a_plan_gives_its_path_and_step_status(tmp_path):
    lines = PLAN_TEXT.split("\n")
    header = lines.index(next(ln for ln in lines if ln.startswith("### Step 4:")))
    status = next(i for i in range(header, len(lines)) if lines[i].startswith("Status:"))
    lines[status] = "Status: PENDING"
    older = "\n".join(lines)
    assert next_plan.plan_hash(older) == next_plan.plan_hash(PLAN_TEXT)
    project = plan_project(tmp_path, {"docs/u3-plan.md": PLAN_TEXT, "prompt_plan.md": older})
    os.utime(project / "docs" / "u3-plan.md", (PLAN_MTIME + 60, PLAN_MTIME + 60))
    next_projection.build(git_repo(tmp_path / "target"), project)
    conn = sqlite3.connect(store_of(project))
    h = next_plan.plan_hash(PLAN_TEXT)
    query = "SELECT props FROM node WHERE type = ? AND key = ?"
    assert json.loads(conn.execute(query, ("plan", h)).fetchone()[0])["path"] == "docs/u3-plan.md"
    step_4 = json.loads(conn.execute(query, ("plan_step", f"{h}/4")).fetchone()[0])
    assert step_4["status"] == "DONE"


# F081_PLAN: exports/platform-f081/prompt_plan.md in the Acme workspace on devbox, the plan whose
# hash is P2, approved by DEMO_RECORD's 2026-10-20T20:17:26Z plan.approved, read 2026-10-22.
F081_PLAN = (FIXTURES / "projection" / "prompt_plan-f081.md").read_text(encoding="utf-8")


def test_a_plan_the_records_and_the_plans_both_wrote_keeps_a_provenance_row_per_source(tmp_path):
    assert next_plan.plan_hash(F081_PLAN) == P2
    target = record_repo(tmp_path / "target", {"acme-demo-account.md": DEMO_RECORD})
    project = plan_project(tmp_path, {"prompt_plan.md": F081_PLAN})
    next_projection.build(target, project)
    conn = sqlite3.connect(store_of(project))
    query = "SELECT props FROM node WHERE type = 'plan' AND key = ?"
    (props,) = conn.execute(query, (P2,)).fetchone()
    assert {"by", "path", "title"} <= json.loads(props).keys()
    sha = git(target, "log", "-1", "--format=%H", "--", "docs/records")
    digest = hashlib.sha256(F081_PLAN.encode("utf-8")).hexdigest()
    rows = conn.execute(
        "SELECT source, source_version FROM node_source WHERE type = 'plan' AND key = ?"
        " ORDER BY source",
        (P2,),
    ).fetchall()
    assert rows == [("plans", f"sha256:{digest}"), ("records", sha)]


# SPECS: excerpts of four specs in acme/platform at 763e5a5e, each line kept whole and in
# order with a blank line after it, read 2026-10-22: docs/specs/acme-demo-account.md lines 1, 3,
# 39, 41, 194, 262 and 264 (a flat spec at Version v7, line 194 a decision written D<n>,);
# docs/specs/agent-cost/spec.md lines 1, 3, 25, 27, 78 and 80 (a folder spec at v3);
# docs/specs/ai-dlc-record.md lines 1, 41 and 43 (a spec with no Version line);
# docs/specs/customer-tenancy.md lines 1 and 236 (a line naming decisions that is not one);
# docs/specs/platform-claude-config/spec.md at 10f1502a lines 1, 3 and 57 (PLAN_TEXT's spec at v1,
# R9 and no R8).
SPECS = FIXTURES / "projection" / "specs"


def spec_repo(path, names):
    repo = git_repo(path)
    for name in names:
        dst = repo / "docs" / "specs" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((SPECS / name).read_bytes())
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "specs")
    return repo


def built_specs(tmp_path, names):
    project = tmp_path / "project"
    next_projection.build(spec_repo(tmp_path / "target", names), project)
    return sqlite3.connect(store_of(project))


def test_each_r_line_of_a_flat_spec_gives_a_requirement_by_slug_number_and_version(tmp_path):
    conn = built_specs(tmp_path, ["acme-demo-account.md"])
    assert nodes(conn, "requirement") == {
        "acme-demo-account/R1/v7",
        "acme-demo-account/R2/v7",
    }


def test_a_folder_spec_takes_its_slug_from_the_folder(tmp_path):
    conn = built_specs(tmp_path, ["agent-cost/spec.md"])
    assert nodes(conn, "requirement") == {"agent-cost/R1/v3", "agent-cost/R2/v3"}


def test_each_d_line_gives_a_decision_node(tmp_path):
    conn = built_specs(tmp_path, ["agent-cost/spec.md", "acme-demo-account.md"])
    assert nodes(conn, "decision") == {
        "agent-cost/D1",
        "agent-cost/D2",
        "acme-demo-account/D1",
        "acme-demo-account/D35",
        "acme-demo-account/D36",
    }


def test_a_d_line_written_with_a_comma_gives_a_decision_node(tmp_path):
    conn = built_specs(tmp_path, ["acme-demo-account.md"])
    assert "acme-demo-account/D1" in nodes(conn, "decision")


def test_a_line_naming_a_range_of_decisions_gives_no_decision_node(tmp_path):
    conn = built_specs(tmp_path, ["customer-tenancy.md"])
    assert nodes(conn, "decision") == set()


def test_a_spec_with_no_version_line_is_versioned_by_its_last_commit(tmp_path):
    conn = built_specs(tmp_path, ["ai-dlc-record.md"])
    sha = git(tmp_path / "target", "log", "-1", "--format=%H", "--", "docs/specs/ai-dlc-record.md")
    assert len(sha) == 40
    assert nodes(conn, "requirement") == {f"ai-dlc-record/R1/{sha}", f"ai-dlc-record/R2/{sha}"}


def built_plan_and_spec(tmp_path):
    target = spec_repo(tmp_path / "target", ["platform-claude-config/spec.md"])
    project = plan_project(tmp_path, {"prompt_plan.md": PLAN_TEXT})
    next_projection.build(target, project)
    return sqlite3.connect(store_of(project)), next_plan.plan_hash(PLAN_TEXT)


def test_a_closes_id_derives_an_edge_to_the_requirement_of_the_stored_spec_version(tmp_path):
    conn, h = built_plan_and_spec(tmp_path)
    derived = {e for e in edges_from(conn, f"plan_step:{h}/4") if e[2] == "derived"}
    assert derived == {
        (f"requirement:{PLAN_SLUG}/R9/v1", "closes", "derived", "closes-current-version")
    }


def test_a_closes_id_with_no_requirement_in_the_store_derives_nothing(tmp_path):
    conn, h = built_plan_and_spec(tmp_path)
    assert {e for e in edges_from(conn, f"plan_step:{h}/3") if e[2] == "derived"} == set()


def test_the_declared_closes_edges_stay_as_the_plan_parser_wrote_them(tmp_path):
    conn, h = built_plan_and_spec(tmp_path)
    declared = conn.execute(
        "SELECT src, dst FROM edge WHERE type = 'closes' AND method = 'declared' ORDER BY src"
    ).fetchall()
    assert declared == [
        (f"plan_step:{h}/3", f"requirement:{PLAN_SLUG}/R8"),
        (f"plan_step:{h}/4", f"requirement:{PLAN_SLUG}/R9"),
    ]


def test_a_spec_staged_and_never_committed_is_versioned_by_its_sha256(tmp_path):
    repo = git_repo(tmp_path / "target")
    (repo / "README.md").write_text("target\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "readme")
    spec = repo / "docs" / "specs" / "ai-dlc-record.md"
    spec.parent.mkdir(parents=True)
    spec.write_bytes((SPECS / "ai-dlc-record.md").read_bytes())
    git(repo, "add", "-A")
    project = tmp_path / "project"
    next_projection.build(repo, project)
    conn = sqlite3.connect(store_of(project))
    digest = hashlib.sha256(spec.read_bytes()).hexdigest()
    assert nodes(conn, "requirement") == {
        f"ai-dlc-record/R1/sha256:{digest}",
        f"ai-dlc-record/R2/sha256:{digest}",
    }


# Pull request fixtures. gh-pr-list.json: four pull requests of a live `gh pr list --repo
# acme/platform --state all --limit 400 --json number,title,body,headRefName,baseRefName,state,
# mergedAt,mergeCommit,createdAt,author` read 2026-10-22 22:06:39 EDT, in the order and the compact
# form gh wrote them: #591 merged with the four trailers at the top of its body, #580 closed
# unmerged on a stacked base with them at the bottom, #569 merged under Spec: none and Intent: none,
# #529 open on the engine's next-auto branch. No pull request was open on a stacked base in that
# read. GH_REPO: `gh repo view --json nameWithOwner --jq .nameWithOwner` in the acme/platform
# checkout, read 22:19:21 EDT; ORIGIN_URL: its `git remote get-url origin`. GH_OFFLINE: the stderr
# and exit 1 of that gh pr list with HTTPS_PROXY at a closed local port, read 22:22:36 EDT. The
# excerpt specs/slack-demo-surface.md: lines 1, 3 and 242 (R54) at acme/platform 018fcd05, read
# 22:21:03 EDT.
GH_PR_LIST = (FIXTURES / "projection" / "gh-pr-list.json").read_text(encoding="utf-8")
GH_REPO = "acme/platform\n"
ORIGIN_URL = "https://github.com/acme/platform.git"
GH_OFFLINE = (
    'Post "https://api.github.com/graphql": proxyconnect tcp: dial tcp 127.0.0.1:9: connect:'
    " connection refused\n"
)
PR_FIELDS = "number,title,body,headRefName,baseRefName,state,mergedAt,mergeCommit,createdAt,author"
PR591 = "pull_request:acme/platform#591"
PR580 = "pull_request:acme/platform#580"
PR569 = "pull_request:acme/platform#569"
PR529 = "pull_request:acme/platform#529"
MERGE569 = "ff128568492bc8450881106fd9a707e99b8c4160"
MERGE591 = "6d01ea38d5a71168119825de95390c2815760d4b"


def saved_gh(calls=None, pr_list=GH_PR_LIST, runs="[]"):
    def gh(argv, cwd):
        if calls is not None:
            calls.append((argv, cwd))
        if argv[:3] == ["gh", "repo", "view"]:
            return GH_REPO
        if argv[:3] == ["gh", "pr", "list"]:
            return pr_list
        if argv[:3] == ["gh", "run", "list"]:
            return runs
        raise AssertionError(argv)

    return gh


def offline_gh(argv, cwd):
    raise subprocess.CalledProcessError(1, argv, output="", stderr=GH_OFFLINE)


def with_origin(repo):
    git(repo, "remote", "add", "origin", ORIGIN_URL)
    return repo


def built_pulls(tmp_path, gh=None, specs=()):
    target = spec_repo(tmp_path / "target", specs) if specs else git_repo(tmp_path / "target")
    project = tmp_path / "project"
    next_projection.build(with_origin(target), project, gh=gh or saved_gh())
    return sqlite3.connect(store_of(project))


def node_props(conn, node_type, key):
    row = conn.execute("SELECT props FROM node WHERE type = ? AND key = ?", (node_type, key))
    return json.loads(row.fetchone()[0])


def test_the_trailers_become_declared_edges_and_the_approver_a_property(tmp_path):
    conn = built_pulls(tmp_path)
    declared = {"implements", "closes", "serves"}
    assert {e for e in edges_from(conn, PR591) if e[1] in declared} == {
        ("spec:slack-demo-surface", "implements", "declared", None),
        ("requirement:slack-demo-surface/R54", "closes", "declared", None),
        ("intent:30.10", "serves", "declared", None),
    }
    assert {e for e in edges_from(conn, PR580) if e[1] in declared} == {
        ("spec:shadow-agents", "implements", "declared", None),
        ("requirement:shadow-agents/R2", "closes", "declared", None),
        ("requirement:shadow-agents/R3", "closes", "declared", None),
        ("requirement:shadow-agents/R29", "closes", "declared", None),
        ("intent:30.4", "serves", "declared", None),
    }
    assert {e for e in edges_from(conn, PR569) if e[1] in declared} == set()
    props = node_props(conn, "pull_request", "acme/platform#591")
    assert props["approvedBy"] == "@ryanmat"
    assert (props["number"], props["state"], props["author"]) == (591, "MERGED", "ryanmat")
    assert props["title"] == "feat(dapi): the fofez and bomun sogozed with uvimo osozabez umipuvuga"
    assert node_props(conn, "spec", "shadow-agents") == {"path": "docs/specs/shadow-agents/spec.md"}


def test_the_branch_name_gives_the_tracker_issue(tmp_path):
    conn = built_pulls(tmp_path)
    # F-081 opens the plan slug of #529's next-auto branch; the engine leads such a branch with the
    # plan's Issue key in the same shape, so the two read alike (the plan's Findings)
    assert nodes(conn, "tracker_issue") == {"DOC-1015", "DOC-1018", "DOC-1003", "F-081"}
    assert ("tracker_issue:DOC-1015", "tracks", "declared", None) in edges_from(conn, PR591)


@pytest.mark.parametrize(
    "branch",
    [
        "dependabot/npm_and_yarn/apps/portal/minor-and-patch-3b869c52",
        "dependabot/bun/infra/minor-and-patch-6e5c770472",
        "feat/s9-factory-oidc-roles",
        "docs/change-management-exception",
    ],
)
def test_a_branch_outside_the_tracker_shape_names_no_issue(branch):
    assert next_projection.tracker_of(branch) is None


def test_a_merged_pull_request_carries_its_merge_commit_as_an_observed_edge(tmp_path):
    conn = built_pulls(tmp_path)
    rows = conn.execute(
        "SELECT src, dst, method, asserted_at FROM edge WHERE type = 'mergedAs' ORDER BY src"
    ).fetchall()
    assert rows == [
        (PR569, f"commit:{MERGE569}", "observed", "2026-10-22T15:12:53Z"),
        (PR591, f"commit:{MERGE591}", "observed", "2026-10-23T01:47:57Z"),
    ]


def test_a_failing_gh_call_marks_pulls_stale_at_its_last_watermark_and_the_build_goes_on(
    tmp_path, caplog
):
    target = with_origin(record_repo(tmp_path / "target", {"acme-demo-account.md": DEMO_RECORD}))
    project = tmp_path / "project"
    next_projection.build(target, project, gh=offline_gh)
    assert next_projection.status_lines(project) == [
        "pulls never read stale",
        "records 2026-10-20T20:17:26Z",
    ]
    next_projection.build(target, project, gh=saved_gh())
    assert next_projection.status_lines(project)[0] == "pulls 2026-10-23T01:47:57Z"
    next_projection.build(target, project, gh=offline_gh)
    assert next_projection.status_lines(project) == [
        "pulls 2026-10-23T01:47:57Z stale",
        "records 2026-10-20T20:17:26Z",
    ]
    conn = sqlite3.connect(store_of(project))
    # the rows of the build that read the pull requests are carried (rul-fade447316)
    assert nodes(conn, "pull_request") == {
        "acme/platform#591",
        "acme/platform#580",
        "acme/platform#569",
        "acme/platform#529",
    }
    assert ("commit:" + MERGE591, "mergedAs", "observed", None) in edges_from(conn, PR591)
    assert nodes(conn, "deploy") == {"39004573395/dev", "39010149386/demo"}
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 2
    assert all(r.exc_info is not None and GH_OFFLINE.strip() in r.getMessage() for r in errors)


def test_gh_missing_marks_pulls_stale(tmp_path, caplog):
    def missing_gh(argv, cwd):
        return next_projection.run_gh(["gh-not-installed", *argv[1:]], cwd)

    project = tmp_path / "project"
    next_projection.build(with_origin(git_repo(tmp_path / "target")), project, gh=missing_gh)
    assert next_projection.status_lines(project) == ["pulls never read stale"]
    assert [r.exc_info[0] for r in caplog.records if r.levelname == "ERROR"] == [FileNotFoundError]


def test_the_pull_request_read_is_one_function_given_the_command(tmp_path):
    calls = []
    conn = built_pulls(tmp_path, saved_gh(calls))
    root = Path(git(tmp_path / "target", "rev-parse", "--show-toplevel"))
    assert calls == [
        (["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], root),
        (["gh", "pr", "list", "--state", "all", "--limit", "10000", "--json", PR_FIELDS], root),
    ]
    digest = "sha256:" + hashlib.sha256(GH_PR_LIST.encode("utf-8")).hexdigest()
    rows = conn.execute(
        "SELECT DISTINCT source, source_version FROM node_source WHERE type = 'pull_request'"
    ).fetchall()
    assert rows == [("pulls", digest)]
    assert nodes(conn, "pull_request") == {
        "acme/platform#591",
        "acme/platform#580",
        "acme/platform#569",
        "acme/platform#529",
    }


def test_run_gh_returns_the_output_and_raises_on_a_failing_command(tmp_path):
    repo = git_repo(tmp_path / "target")
    out = next_projection.run_gh(["git", "rev-parse", "--is-inside-work-tree"], repo)
    assert out == "true\n"
    with pytest.raises(subprocess.CalledProcessError):
        next_projection.run_gh(["git", "log", "-1"], repo)


def test_a_target_with_no_remote_reads_no_pull_requests(tmp_path):
    def no_call(argv, cwd):
        raise AssertionError(argv)

    project = tmp_path / "project"
    next_projection.build(git_repo(tmp_path / "target"), project, gh=no_call)
    assert next_projection.status_lines(project) == []


def test_closes_ids_under_spec_none_stop_the_build_naming_the_pull_request(tmp_path):
    # #569's body with #591's Closes: line added ahead of its Intent: none line
    pr = next(p for p in json.loads(GH_PR_LIST) if p["number"] == 569)
    pr["body"] = pr["body"].replace("Intent: none", "Closes: R54\nIntent: none", 1)
    gh = saved_gh(pr_list=json.dumps([pr]))
    with pytest.raises(next_projection.ProjectionError) as exc:
        built_pulls(tmp_path, gh)
    assert str(exc.value) == "acme/platform#569: Closes: R54 with no Spec: path"


def test_a_pull_requests_closes_id_derives_no_closes_current_version_edge(tmp_path):
    conn = built_pulls(tmp_path, specs=["slack-demo-surface.md"])
    assert nodes(conn, "requirement") == {"slack-demo-surface/R54/v6"}
    assert {e for e in edges_from(conn, PR591) if e[1] == "closes"} == {
        ("requirement:slack-demo-surface/R54", "closes", "declared", None)
    }


# Ruling fixtures. rulings/6859d9d4-aec0-4dd5-8bb4-fd43fffb4112.jsonl: the lines of that store file
# in ~/dev/acme/.next-auto/rulings for rul-45a3de5046 (a typed answer, kept as notes) and
# rul-97b833e6b1 (notes null), read 2026-10-22 22:44:36 EDT. decisions-2026-10.md: lines 1 to 5,
# 11, 923 and 934 of the Acme workspace's docs/decisions/2026-10.md, read then: the header, a
# line from before the ids and the two lines naming those ids. decisions-minute-line.md: line 570
# of that file, a time with no seconds.
RULINGS_DIR = FIXTURES / "projection" / "rulings"
STORE_FILE = "6859d9d4-aec0-4dd5-8bb4-fd43fffb4112.jsonl"
DECISIONS_TEXT = (FIXTURES / "projection" / "decisions-2026-10.md").read_text(encoding="utf-8")
MINUTE_LINE = (FIXTURES / "projection" / "decisions-minute-line.md").read_text(encoding="utf-8")
FOLD_ALL_FOUR = (
    "rul-" + hashlib.sha256(b"decisions|2026-10-13 10:22:08 EDT|Fiti all rade").hexdigest()[:10]
)


@pytest.fixture
def new_york(monkeypatch):
    # the store and the log name EDT, a name time.tzname holds only in the zone they were written
    # in; CI runs in UTC
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def ruling_project(tmp_path, store_text=None, decisions_text=DECISIONS_TEXT):
    project = tmp_path / "project"
    store = project / ".next-auto" / "rulings" / STORE_FILE
    store.parent.mkdir(parents=True)
    if store_text is None:
        store.write_bytes((RULINGS_DIR / STORE_FILE).read_bytes())
    else:
        store.write_text(store_text, encoding="utf-8")
    if decisions_text is not None:
        log_file = project / "docs" / "decisions" / "2026-10.md"
        log_file.parent.mkdir(parents=True)
        log_file.write_text(decisions_text, encoding="utf-8")
    return project


def built_rulings(tmp_path, **kw):
    project = ruling_project(tmp_path, **kw)
    next_projection.build(git_repo(tmp_path / "target"), project)
    return sqlite3.connect(store_of(project)), project


def test_each_store_line_gives_a_ruling_by_its_id_with_its_fields(tmp_path, new_york):
    conn, _ = built_rulings(tmp_path, decisions_text=None)
    assert nodes(conn, "ruling") == {"rul-45a3de5046", "rul-97b833e6b1"}
    assert node_props(conn, "ruling", "rul-97b833e6b1") == {
        "time": "2026-10-22 20:25:21 EDT",
        "at": "2026-10-23T00:25:21Z",
        "header": "Bidov",
        "option": "Bidov now (Recommended)",
        "notes": None,
        "source": "dialog",
    }
    typed = node_props(conn, "ruling", "rul-45a3de5046")
    assert typed["notes"] == typed["option"]
    assert typed["notes"].startswith("Zoga osoka refe what is in the demo vep esali?")


def test_a_decisions_line_with_an_id_joins_the_stores_ruling(tmp_path, new_york):
    conn, _ = built_rulings(tmp_path)
    assert nodes(conn, "ruling") == {"rul-45a3de5046", "rul-97b833e6b1", FOLD_ALL_FOUR}
    props = node_props(conn, "ruling", "rul-97b833e6b1")
    assert (props["header"], props["source"]) == ("Bidov", "dialog")
    assert props["what"].startswith("Bidov the utekas alov: amaz egubeb, bunarunotalon")
    rows = conn.execute(
        "SELECT source FROM node_source WHERE type = 'ruling' AND key = 'rul-97b833e6b1'"
        " ORDER BY source"
    ).fetchall()
    assert rows == [("decisions",), ("rulings",)]


def test_a_decisions_line_without_an_id_is_keyed_by_the_hash_of_its_time_and_option(
    tmp_path, new_york
):
    conn, _ = built_rulings(tmp_path)
    assert node_props(conn, "ruling", FOLD_ALL_FOUR) == {
        "time": "2026-10-13 10:22:08 EDT",
        "at": "2026-10-13T14:22:08Z",
        "option": "Fiti all rade",
        "what": "the katekob edos and Ibibu findings fiti into the babiv spec (R44 to R46)",
        "source": "decisions",
    }


def test_the_ruling_watermarks_are_the_newest_times_in_utc(tmp_path, new_york):
    _, project = built_rulings(tmp_path)
    assert next_projection.status_lines(project) == [
        "decisions 2026-10-23T00:25:21Z",
        "rulings 2026-10-23T00:25:21Z",
    ]


def test_a_ruling_line_the_parser_cannot_read_stops_the_build_naming_file_and_line(
    tmp_path, new_york, monkeypatch
):
    target = git_repo(tmp_path / "target")
    # a decisions line with no seconds, as the handoff gate's RULING_LINE refuses
    project = ruling_project(tmp_path / "minute", decisions_text=DECISIONS_TEXT + MINUTE_LINE)
    with pytest.raises(next_projection.ProjectionError) as exc:
        next_projection.build(target, project)
    assert str(exc.value) == f"docs/decisions/2026-10.md:9: {MINUTE_LINE.rstrip()}"
    # a store line cut short, as a write that stopped part way leaves it
    cut = (RULINGS_DIR / STORE_FILE).read_text(encoding="utf-8").splitlines()[1][:60]
    project = ruling_project(tmp_path / "cut", store_text=cut + "\n", decisions_text=None)
    with pytest.raises(next_projection.ProjectionError) as exc:
        next_projection.build(target, project)
    assert str(exc.value) == f".next-auto/rulings/{STORE_FILE}:1: {cut}"
    # a zone the local zone does not name
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    project = ruling_project(tmp_path / "zone", decisions_text=None)
    with pytest.raises(next_projection.ProjectionError) as exc:
        next_projection.build(target, project)
    first = (RULINGS_DIR / STORE_FILE).read_text(encoding="utf-8").splitlines()[0]
    assert str(exc.value) == f".next-auto/rulings/{STORE_FILE}:1: {first}"


# The close-out and factory ledgers, sources (f) and (g) of Requirement 10, were retired on
# 2026-11-04 by rul-8a9c3bd6ee (sweep spec R5): build takes no ledger argument, the store carries
# no closeout or factory watermark, and no cost node or spentIn edge, which those two sources
# alone produced; every query still answers over the pulls store.
LEDGER_FLAGS = ("--closeout-jsonl", "--factory-ledger")


@pytest.mark.parametrize("flag", LEDGER_FLAGS)
def test_build_refuses_the_retired_ledger_arguments(tmp_path, flag):
    target = git_repo(tmp_path / "target")
    project = tmp_path / "project"
    ledger = tmp_path / "ledger.jsonl"
    r = run_script("build", "--target", str(target), "--project", str(project), flag, str(ledger))
    assert r.returncode == 2
    assert f"unrecognized arguments: {flag}" in r.stderr
    assert not store_of(project).exists()


def test_the_store_holds_no_ledger_watermark_and_no_cost_node(tmp_path):
    conn = built_pulls(tmp_path, specs=("slack-demo-surface.md",))
    sources = {s for (s,) in conn.execute("SELECT source FROM watermarks")}
    assert sources and not sources & {"closeout", "factory"}
    assert conn.execute("SELECT count(*) FROM node WHERE type = 'cost'").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM edge WHERE type = 'spentIn'").fetchone() == (0,)
    for name in ("ingest_closeout", "ingest_factory", "read_ledgers", "ledger_rows"):
        assert not hasattr(next_projection, name), name


# one argument shape per query subcommand, each answerable over the pulls store: #591 serves
# intent 30.10, implements slack-demo-surface and merged as MERGE591. The third element is the
# token the text answer must carry (3.5.10 spec R8), each copied from the per-subcommand test
# that already expects it: the node itself for entity, find and timeline; the intent #591
# serves for neighbors and #591 itself for intent (test_query_entity_gives_the_node_...); the
# merge commit for path (the mergedAs edge); the R54/v6 requirement for coverage
QUERY_SHAPES = [
    ("entity", (PR591,), PR591),
    ("find", ("591",), PR591),
    ("neighbors", (PR591,), "intent:30.10"),
    ("path", (PR591, f"commit:{MERGE591}"), f"commit:{MERGE591}"),
    ("timeline", (PR591,), PR591),
    ("intent", ("30.10",), PR591),
    ("coverage", ("slack-demo-surface",), "requirement:slack-demo-surface/R54/v6"),
]


@pytest.mark.parametrize("shape", QUERY_SHAPES, ids=[s[0] for s in QUERY_SHAPES])
def test_every_query_still_answers_over_the_pulls_store_as_text(tmp_path, shape):
    name, args, token = shape
    built_pulls(tmp_path, specs=("slack-demo-surface.md",))
    r = run_script("query", name, *args, "--text", "--project", str(tmp_path / "project"))
    assert (r.returncode, r.stderr) == (0, ""), r.stderr
    assert token in r.stdout, (name, r.stdout)


def test_query_landed_still_answers(tmp_path):
    """The eighth subcommand needs a Handoff day, so it answers over the landed project; it
    prints the table as lines and takes no --text."""
    project = landed_project(tmp_path)
    same = "c138ffc1150f7604c9a126c071226b4927579874"
    handoff = project / "docs" / "handoff" / "2026-10-24.md"
    handoff.write_text(f"## Landed\nSince: {same}\nUntil: {same}\n", encoding="utf-8")
    r = run_script("query", "landed", "--date", "2026-10-24", "--project", str(project))
    assert (r.returncode, r.stderr) == (0, ""), r.stderr
    assert r.stdout.startswith("| PR | Title | Merged | Run | Conclusion | Intent |")


def source_rows(store, source):
    with sqlite3.connect(store) as conn:
        provenance = conn.execute(
            "SELECT type, key, source_version, ingested_at FROM node_source WHERE source = ?"
            " ORDER BY type, key",
            (source,),
        ).fetchall()
        props = conn.execute(
            "SELECT n.type, n.key, n.props FROM node n JOIN node_source s USING (type, key)"
            " WHERE s.source = ? ORDER BY n.type, n.key",
            (source,),
        ).fetchall()
        edges = conn.execute(
            "SELECT src, dst, type, method, rule, source, source_version, asserted_at FROM edge"
            " WHERE source = ? ORDER BY src, dst, type",
            (source,),
        ).fetchall()
    return provenance, props, edges


def test_a_stale_sources_rows_keep_the_provenance_of_the_build_that_read_them(tmp_path):
    target = with_origin(git_repo(tmp_path / "target"))
    project = tmp_path / "project"
    next_projection.build(target, project, gh=saved_gh())
    read = source_rows(store_of(project), "pulls")
    assert all(read)
    next_projection.build(target, project, gh=offline_gh)
    assert source_rows(store_of(project), "pulls") == read


# Derived edge fixtures, over repositories built with evals/lib/fixturelib.py. The deploy lines
# are live ones with their shas replaced by the fixture repository's commits c1 to c5: DEMO_RECORD's
# deploy.completed (run 39004573395) and deploy.promoted (run 39010149386), and from docs/records
# at acme/platform df1122b2, read 2026-10-23, the skipped deploy.completed of run 39789026106
# and the deploy.completed of run 39864016611, which deployed #577's merge. gh-pr-577.json: #577
# from a live `gh pr list --repo acme/platform --state all --limit 1000 --json <PR_FIELDS>`
# read 2026-10-23 09:59:08 EDT, merged at 22:07:14Z; #580 was opened on its head branch at
# 22:07:00Z. specs/shadow-agents/spec.md: lines 1, 3, 27 and 29 (R1, R2) of that spec at
# acme/platform df1122b2, read then.
FIXTURELIB = ROOT / "evals" / "lib" / "fixturelib.py"
_spec = importlib.util.spec_from_file_location("fixturelib", FIXTURELIB)
fixturelib = importlib.util.module_from_spec(_spec)
sys.modules["fixturelib"] = fixturelib
_spec.loader.exec_module(fixturelib)

DEPLOY_DEV = "- 2026-10-17T05:30:28Z deploy.completed sha={} run=39004573395 conclusion=success"
DEPLOY_DEMO = (
    "- 2026-10-17T07:08:58Z deploy.promoted target=demo base={} run=39010149386 conclusion=success"
)
DEPLOY_SKIPPED = "- 2026-10-22T13:41:10Z deploy.completed sha={} run=39789026106 conclusion=skipped"
DEPLOY_LATER = "- 2026-10-22T22:39:38Z deploy.completed sha={} run=39864016611 conclusion=success"
PR577_TEXT = (FIXTURES / "projection" / "gh-pr-577.json").read_text(encoding="utf-8")
PR577 = "pull_request:acme/platform#577"
STACK_SPECS = ("slack-demo-surface.md", "shadow-agents/spec.md")


def fixture_repo(root, files, commits):
    fixture = fixturelib.Fixture(name="target", files=files, commits=commits)
    return fixturelib.build(fixture, root=root, dirname="target").path


def built_deploys(tmp_path, lines):
    names = [f"c{n}" for n in range(1, 6)]
    files = {f"{name}.txt": f"{name}\n" for name in names}
    repo = fixture_repo(tmp_path, files, tuple((name, (f"{name}.txt",)) for name in names))
    shas = dict(zip(names, git(repo, "rev-list", "--reverse", "HEAD").split(), strict=True))
    record = repo / "docs" / "records" / "deploys.md"
    record.parent.mkdir(parents=True)
    events = "".join(line.format(shas[name]) + "\n" for line, name in lines)
    record.write_text(RECORD_HEADER.format(slug="deploys", spec="deploys.md") + events, "utf-8")
    project = tmp_path / "project"
    next_projection.build(repo, project, gh=saved_gh(pr_list="[]"))
    return sqlite3.connect(store_of(project)), shas


def includes(conn, deploy):
    return {e for e in edges_from(conn, f"deploy:{deploy}") if e[1] == "includes"}


def range_of(shas, *names):
    return {(f"commit:{shas[name]}", "includes", "derived", "deploy-range") for name in names}


def test_a_deploy_includes_every_commit_since_the_previous_deploy_on_its_target(tmp_path):
    conn, c = built_deploys(
        tmp_path, [(DEPLOY_DEV, "c2"), (DEPLOY_DEMO, "c3"), (DEPLOY_LATER, "c5")]
    )
    assert includes(conn, "39864016611/dev") == range_of(c, "c3", "c4", "c5")
    assert {c["c3"], c["c4"], c["c5"]} <= nodes(conn, "commit")
    assert c["c1"] not in nodes(conn, "commit")


def test_the_first_deploy_on_a_target_includes_only_its_own_commit(tmp_path):
    conn, c = built_deploys(
        tmp_path, [(DEPLOY_DEV, "c2"), (DEPLOY_DEMO, "c3"), (DEPLOY_LATER, "c5")]
    )
    assert includes(conn, "39004573395/dev") == range_of(c, "c2")
    assert includes(conn, "39010149386/demo") == range_of(c, "c3")


def test_a_deploy_that_did_not_succeed_includes_nothing_and_is_not_the_previous_one(tmp_path):
    conn, c = built_deploys(
        tmp_path, [(DEPLOY_DEV, "c2"), (DEPLOY_SKIPPED, "c4"), (DEPLOY_LATER, "c5")]
    )
    assert includes(conn, "39789026106/dev") == set()
    assert includes(conn, "39864016611/dev") == range_of(c, "c3", "c4", "c5")


def built_stack(root, pr_list):
    files = {
        f"docs/specs/{name}": (SPECS / name).read_text(encoding="utf-8") for name in STACK_SPECS
    }
    repo = fixture_repo(root, files, (("specs", tuple(files)),))
    project = root / "project"
    next_projection.build(repo, project, gh=saved_gh(pr_list=pr_list))
    return sqlite3.connect(store_of(project))


def derived_of(conn, edge_type):
    return conn.execute(
        "SELECT src, dst, method, rule FROM edge WHERE type = ? ORDER BY src, dst", (edge_type,)
    ).fetchall()


def test_a_closes_id_on_a_merged_pull_request_derives_implemented_by_never_on_an_unmerged_one(
    tmp_path,
):
    # #577 merged closes R1 of shadow-agents; #580 closed unmerged closes its R2, both in the store
    conn = built_stack(tmp_path, json.dumps(json.loads(GH_PR_LIST) + json.loads(PR577_TEXT)))
    assert {"shadow-agents/R1/v4", "shadow-agents/R2/v4"} <= nodes(conn, "requirement")
    assert derived_of(conn, "implementedBy") == [
        ("requirement:shadow-agents/R1/v4", PR577, "derived", "merged-closes"),
        ("requirement:slack-demo-surface/R54/v6", PR591, "derived", "merged-closes"),
    ]


def test_a_pull_request_on_another_open_pull_requests_head_branch_is_stacked_on_it(tmp_path):
    pr580 = next(p for p in json.loads(GH_PR_LIST) if p["number"] == 580)
    (pr577,) = json.loads(PR577_TEXT)
    assert pr580["baseRefName"] == pr577["headRefName"]
    # the two as they stood from #580's creation at 22:07:00Z to #577's merge at 22:07:14Z
    unmerged = {"state": "OPEN", "mergedAt": None, "mergeCommit": None}
    conn = built_stack(tmp_path / "open", json.dumps([pr580 | unmerged, pr577 | unmerged]))
    assert derived_of(conn, "stackedOn") == [(PR580, PR577, "derived", "base-chain")]
    conn = built_stack(tmp_path / "merged", json.dumps([pr580 | unmerged, pr577]))
    assert derived_of(conn, "stackedOn") == []


# producedIn and unblocks fixtures. shadow-agents-record.md: the header and lines 11 to 13, 16 to 19
# and 22 of docs/records/shadow-agents.md at acme/platform df1122b2, read 2026-10-23 10:37:57
# EDT: run b32085ff did Steps 1 to 3, opened #577, did Steps 4 to 6 and opened #580; each live sha
# is written as a commit of a fixturelib repository in the same order.
# unblocks/decisions-2026-10.md: lines 1 to 5, 286, 354, 358, 818, 897 and 960 of the Acme
# workspace's docs/decisions/2026-10.md, read 10:44:25 EDT. unblocks/specs/slack-demo-surface.md:
# lines 1, 3, 174 (R25) and 242 (R54) of that spec at acme/platform df1122b2, version v7, read
# 2026-10-23.
SHADOW_RECORD = (FIXTURES / "projection" / "shadow-agents-record.md").read_text(encoding="utf-8")
SHADOW_RUN = "runkey=b32085ff-e6ba-429a-9839-fdb175fee5e9"
OTHER_RUN = "runkey=2a6347ef-6be3-4b9e-96bd-1c18636e0e77"
# the record's shas in history order: Steps 1 to 3, #577's head, Steps 4 to 6, #580's head
SHADOW_SHAS = (
    "472e085bcf91c812f8a478d218cfad0b3171caa0",
    "4e9c8192560fe1663f9aeea5285825ae890fc2ae",
    "352804a74bc80567b7b138312ebb137be72561b1",
    "0e3590bb42be9c00f4aa5b1d868fb7af0c411300",
    "b2c5f9a523aa2c984cb934d043f76a544f55742d",
    "33045cca7317eef29ddf05e20d325fa5a5810837",
    "9ee8994c8450c02a01e32a9975cd176d7efba3cb",
    "121e102cbe5e03b1425a03dae11393b959f84ed4",
)
OPENED_577 = "2026-10-22T21:43:58Z"
OPENED_580 = "2026-10-22T22:07:05Z"
UNBLOCKS = FIXTURES / "projection" / "unblocks"
UNBLOCKS_LOG = (UNBLOCKS / "decisions-2026-10.md").read_text(encoding="utf-8")


def derived_build(root, record=SHADOW_RECORD, decisions=None):
    """A fixturelib target holding the slack-demo-surface excerpt and eight commits standing in for
    the record's shas, the record when given, two plans and the decisions log when given."""
    spec = "docs/specs/slack-demo-surface.md"
    names = [f"c{n}" for n in range(1, 9)]
    files = {spec: (UNBLOCKS / "specs" / "slack-demo-surface.md").read_text(encoding="utf-8")}
    files |= {f"{name}.txt": f"{name}\n" for name in names}
    commits = (("specs", (spec,)), *((name, (f"{name}.txt",)) for name in names))
    repo = fixture_repo(root, files, commits)
    history = git(repo, "rev-list", "--reverse", "HEAD").split()[1:]
    if record is not None:
        for live, made in zip(SHADOW_SHAS, history, strict=True):
            record = record.replace(live, made)
        path = repo / "docs" / "records" / "shadow-agents.md"
        path.parent.mkdir(parents=True)
        path.write_text(record, encoding="utf-8")
    plans = {"prompt_plan.md": PLAN_TEXT, "exports/f081/prompt_plan.md": F081_PLAN}
    project = plan_project(root, plans)
    if decisions is not None:
        log_file = project / "docs" / "decisions" / "2026-10.md"
        log_file.parent.mkdir(parents=True)
        log_file.write_text(decisions, encoding="utf-8")
    pr_list = json.dumps(json.loads(GH_PR_LIST) + json.loads(PR577_TEXT))
    next_projection.build(repo, project, gh=saved_gh(pr_list=pr_list))
    return sqlite3.connect(store_of(project)), repo, project


def produced_in(conn):
    rows = conn.execute(
        "SELECT src, dst, method, rule, asserted_at FROM edge WHERE type = 'producedIn'"
        " ORDER BY src, dst"
    ).fetchall()
    return [(src.rsplit("/", 1)[1], dst, method, rule, at) for src, dst, method, rule, at in rows]


def joined(rule, first=("1", "2", "3"), second=("4", "5", "6")):
    return [(s, PR577, "derived", rule, OPENED_577) for s in first] + [
        (s, PR580, "derived", rule, OPENED_580) for s in second
    ]


def test_a_step_line_joins_the_first_pull_request_its_run_opened_after_it(tmp_path):
    # run b32085ff opened two pull requests; each step joins the next one, not both
    conn, _, _ = derived_build(tmp_path)
    assert produced_in(conn) == joined("runkey")


def test_a_step_line_with_no_run_key_or_whose_run_opened_none_joins_by_ancestry(tmp_path):
    unkeyed = SHADOW_RECORD.replace(" " + SHADOW_RUN, "")
    assert SHADOW_RUN not in unkeyed
    conn, _, _ = derived_build(tmp_path / "unkeyed", unkeyed)
    assert produced_in(conn) == joined("ancestry")
    # the two pull requests opened from another session, so the steps' run opened none
    lines = SHADOW_RECORD.splitlines(keepends=True)
    other = "".join(
        ln.replace(SHADOW_RUN, OTHER_RUN) if " pr.opened " in ln else ln for ln in lines
    )
    conn, _, _ = derived_build(tmp_path / "other", other)
    assert produced_in(conn) == joined("ancestry")


def test_a_step_line_with_no_run_key_and_no_descendant_pull_request_head_derives_nothing(
    tmp_path, caplog
):
    # #580 opened on #577's head, which holds none of Steps 4 to 6
    unkeyed = SHADOW_RECORD.replace(" " + SHADOW_RUN, "").replace(SHADOW_SHAS[7], SHADOW_SHAS[3])
    conn, _, _ = derived_build(tmp_path, unkeyed)
    assert produced_in(conn) == joined("ancestry", second=())
    # git judged every head, so no line is counted as unjudged
    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


def log_id(stamp, option):
    return "rul-" + hashlib.sha256(f"decisions|{stamp}|{option}".encode()).hexdigest()[:10]


def test_a_ruling_unblocks_each_token_that_resolves_to_a_node_and_skips_the_rest(
    tmp_path, new_york
):
    conn, _, _ = derived_build(tmp_path, record=None, decisions=UNBLOCKS_LOG)
    h = next_plan.plan_hash(PLAN_TEXT)
    deck = log_id("2026-10-15 13:56:21 EDT", "Go (Recommended)")
    f223 = log_id("2026-10-21 21:26:14 EDT", "One PR, spec + TDD (Recommended)")
    # Step 3 is the claude-config plan's alone; Step 1 is in both plans; R25 is named with its
    # slug, R54 is not; F-155, F-223, DOC-877, DOC-890 and S33 have no node, nor has R14 its slug
    assert derived_of(conn, "unblocks") == sorted(
        [
            (f"ruling:{deck}", f"plan_step:{h}/3", "derived", "ruling-token"),
            (f"ruling:{f223}", "requirement:slack-demo-surface/R25/v7", "derived", "ruling-token"),
            ("ruling:rul-b94f971710", "tracker_issue:DOC-1003", "derived", "ruling-token"),
        ]
    )
    keys = {key for (key,) in conn.execute("SELECT key FROM node")}
    assert not {"F-155", "F-223", "DOC-877", "DOC-890", "S33"} & keys
    assert nodes(conn, "tracker_issue") == {"DOC-1015", "DOC-1018", "DOC-1003", "DOC-1010", "F-081"}


def test_every_derived_edge_carries_its_rule_and_the_version_of_the_facts_it_came_from(
    tmp_path, new_york
):
    conn, repo, project = derived_build(tmp_path, decisions=UNBLOCKS_LOG)
    rows = conn.execute(
        "SELECT type, rule, source, source_version FROM edge WHERE method = 'derived'"
    ).fetchall()
    assert {row[0] for row in rows} == {"implementedBy", "producedIn", "unblocks"}
    versions = set(conn.execute("SELECT source, source_version FROM node_source").fetchall())
    assert all(rule and (source, version) in versions for _, rule, source, version in rows)
    record = repo / "docs" / "records" / "shadow-agents.md"
    log_file = project / "docs" / "decisions" / "2026-10.md"
    of = {kind: {(s, v) for t, _, s, v in rows if t == kind} for kind in ("producedIn", "unblocks")}
    assert of["producedIn"] == {
        ("records", f"sha256:{hashlib.sha256(record.read_bytes()).hexdigest()}")
    }
    digest = hashlib.sha256(log_file.read_bytes()).hexdigest()
    assert of["unblocks"] == {("decisions", f"sha256:{digest}")}


def test_a_step_line_whose_commit_the_checkout_does_not_hold_derives_nothing_and_is_counted(
    tmp_path, caplog
):
    # the live shas in a repository holding none of them, as a fresh clone holds no deleted
    # branch's commits
    conn = built(tmp_path, {"shadow-agents.md": SHADOW_RECORD.replace(" " + SHADOW_RUN, "")})
    assert produced_in(conn) == []
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == [
        "next_projection: 6 step lines met a commit the checkout does not hold; they derive no"
        " producedIn edge by ancestry"
    ]


def test_a_stale_pulls_source_carries_no_derived_edge_past_a_spec_version_bump(tmp_path):
    # rul-538e89a0b5: an offline build re-derives from the carried declared edges, so a merged
    # pull request implements only the spec version the checkout now holds
    files = {
        f"docs/specs/{name}": (SPECS / name).read_text(encoding="utf-8") for name in STACK_SPECS
    }
    repo = fixture_repo(tmp_path, files, (("specs", tuple(files)),))
    project = tmp_path / "project"
    pr_list = json.dumps(json.loads(GH_PR_LIST) + json.loads(PR577_TEXT))
    next_projection.build(repo, project, gh=saved_gh(pr_list=pr_list))
    spec = repo / "docs" / "specs" / "shadow-agents" / "spec.md"
    text = spec.read_text(encoding="utf-8")
    spec.write_text(text.replace("Version: v4,", "Version: v5,", 1), encoding="utf-8")
    git(repo, "commit", "-qam", "shadow-agents v5")
    next_projection.build(repo, project, gh=offline_gh)
    conn = sqlite3.connect(store_of(project))
    assert [e for e in derived_of(conn, "implementedBy") if e[1] == PR577] == [
        ("requirement:shadow-agents/R1/v5", PR577, "derived", "merged-closes")
    ]


# Query fixtures: the pull request read and the ruling store above. The pulls watermark is #591's
# mergedAt, 2026-10-23T01:47:57Z; the clock each answer is read at is passed as now.
PULLS_MARK = "2026-10-23T01:47:57Z"


def at(text):
    return datetime.strptime(text, next_projection.TIME_FORM).replace(tzinfo=UTC)


def query_project(tmp_path):
    """The pull request read and the ruling store with its decisions log, built into one store."""
    project = ruling_project(tmp_path)
    next_projection.build(with_origin(git_repo(tmp_path / "target")), project, gh=saved_gh())
    return project


def many_pulls(n):
    # n copies of #591 as the live read gives it, numbered from 1000, each naming the one spec
    pr = next(p for p in json.loads(GH_PR_LIST) if p["number"] == 591)
    return json.dumps([pr | {"number": 1000 + i} for i in range(n)])


def test_query_entity_gives_the_node_its_props_and_its_edges_grouped_by_type_with_counts(
    tmp_path,
):
    conn = built_pulls(tmp_path, specs=("slack-demo-surface.md",))
    project = tmp_path / "project"
    answer = next_projection.query_entity(project, PR591, now=at("2026-10-23T02:00:00Z"))

    def edge(direction, node, method, edge_at, rule=None):
        found = {"direction": direction, "node": node, "method": method, "rule": rule}
        return {"count": 1, "edges": [found | {"source": "pulls", "at": edge_at}]}

    created = "2026-10-23T01:42:13Z"
    r54 = "requirement:slack-demo-surface/R54"
    assert answer == {
        "matches": [
            {
                "id": PR591,
                "type": "pull_request",
                "props": node_props(conn, "pull_request", "acme/platform#591"),
                "edges": {
                    "closes": edge("out", r54, "declared", created),
                    "implementedBy": edge(
                        "in", f"{r54}/v6", "derived", PULLS_MARK, "merged-closes"
                    ),
                    "implements": edge("out", "spec:slack-demo-surface", "declared", created),
                    "mergedAs": edge("out", f"commit:{MERGE591}", "observed", PULLS_MARK),
                    "serves": edge("out", "intent:30.10", "declared", created),
                    "tracks": edge("out", "tracker_issue:DOC-1015", "declared", created),
                },
            }
        ],
        "as_of": {"pulls": PULLS_MARK},
        "stale": [],
    }
    # the command prints the same answer as JSON, read at the real clock
    r = run_script("query", "entity", PR591, "--project", str(project))
    assert (r.returncode, r.stderr) == (0, "")
    assert json.loads(r.stdout)["matches"] == answer["matches"]


def test_query_find_matches_ids_titles_headers_and_ruling_text_never_bodies(tmp_path, new_york):
    project = query_project(tmp_path)
    now = at("2026-10-23T02:00:00Z")
    expected = {
        # a title, case folded
        "FOFEZ AND BOMUN": [PR591],
        # an id; the head branch also names doc-1015 and is not read
        "doc-1015": ["tracker_issue:DOC-1015"],
        # a store header, the store's typed notes and the decisions log's what
        "Tinolupikuk": ["ruling:rul-45a3de5046"],
        "demo vep esali": ["ruling:rul-45a3de5046"],
        "katekob edos": [f"ruling:{FOLD_ALL_FOUR}"],
        # #591's body, never read
        "Steps 82 and 83": [],
    }
    for text, ids in expected.items():
        answer = next_projection.query_find(project, text, now=now)
        types = [i.split(":", 1)[0] for i in ids]
        assert answer["matches"] == [{"id": i, "type": t} for i, t in zip(ids, types, strict=True)]
    assert next_projection.query_find(project, "doc-1015", "pull_request", now=now)["matches"] == []
    # a find reads every node, so it touches every source
    assert set(answer["as_of"]) == {"pulls", "decisions", "rulings"}


def test_stale_names_a_source_over_24_hours_old_or_left_stale_by_the_last_build(tmp_path):
    built_pulls(tmp_path)
    project = tmp_path / "project"
    for now, stale in (("2026-10-24T01:47:57Z", []), ("2026-10-24T01:47:58Z", ["pulls"])):
        answer = next_projection.query_entity(project, PR591, now=at(now))
        assert (answer["as_of"], answer["stale"]) == ({"pulls": PULLS_MARK}, stale)
    # an offline build keeps the watermark and the rows and marks the source stale
    next_projection.build(tmp_path / "target", project, gh=offline_gh)
    answer = next_projection.query_entity(project, PR591, now=at("2026-10-23T02:00:00Z"))
    assert answer["matches"][0]["id"] == PR591
    assert (answer["as_of"], answer["stale"]) == ({"pulls": PULLS_MARK}, ["pulls"])


def test_an_empty_answer_is_the_closed_world_envelope_and_reads_no_match_as_of(tmp_path):
    # scenario S7: nothing found says no match as of the watermark and flags a stale source
    built_pulls(tmp_path)
    project = tmp_path / "project"
    missing = "pull_request:acme/platform#9999"
    fresh = next_projection.query_entity(project, missing, now=at("2026-10-23T02:00:00Z"))
    assert fresh == {"matches": [], "as_of": {"pulls": PULLS_MARK}, "stale": []}
    assert next_projection.answer_lines(fresh) == [f"no match as of pulls {PULLS_MARK}"]
    old = next_projection.query_find(project, "nothing holds this", now=at("2026-10-25T00:00:00Z"))
    assert old == {"matches": [], "as_of": {"pulls": PULLS_MARK}, "stale": ["pulls"]}
    stale_line = f"no match as of pulls {PULLS_MARK} (stale: pulls)"
    assert next_projection.answer_lines(old) == [stale_line]
    # the command's text form, at the real clock, more than 24 hours after the watermark
    r = run_script("query", "entity", missing, "--text", "--project", str(project))
    assert (r.returncode, r.stdout, r.stderr) == (0, stale_line + "\n", "")


def test_an_answer_lists_at_most_200_nodes_and_truncates_past_the_token_budget(
    tmp_path, monkeypatch
):
    built_pulls(tmp_path, gh=saved_gh(pr_list=many_pulls(205)))
    project = tmp_path / "project"
    now = at("2026-10-23T02:00:00Z")
    ids = sorted(f"pull_request:acme/platform#{1000 + i}" for i in range(205))
    found = next_projection.query_find(project, "fofez and bomun", now=now)
    assert [m["id"] for m in found["matches"]] == ids[:200]
    assert (found["more"], found["hint"]) == (
        5,
        "narrow by node type: query find <text> --type <node type>",
    )
    hub = next_projection.query_entity(project, "spec:slack-demo-surface", now=now)
    implements = hub["matches"][0]["edges"]["implements"]
    assert (implements["count"], len(implements["edges"]), hub["more"]) == (205, 200, 5)
    assert hub["hint"] == (
        "narrow by edge type: query neighbors spec:slack-demo-surface --type <edge type>"
    )
    # 20,000 tokens at four characters a token; a smaller budget shows the truncation
    assert next_projection.MAX_CHARS == 80_000
    monkeypatch.setattr(next_projection, "MAX_CHARS", 2_000)
    cut = next_projection.query_find(project, "fofez and bomun", now=now)
    assert 0 < len(cut["matches"]) < 200
    assert cut["more"] == 205 - len(cut["matches"])
    assert len(json.dumps(cut)) <= 2_000


# The hop-bound fixture: a store holding one chain of deploys and the commits they include,
# c0 - d0 - c1 - d1 - ... - c4 - d4, keyed as live deploys and commits are, under the records
# watermark of the Acme store read 2026-10-24 10:59:33 EDT.
RECORDS_MARK = "2026-10-23T14:36:15Z"


def chain_store(project, length):
    store = store_of(project)
    store.parent.mkdir(parents=True)
    conn = sqlite3.connect(store)
    conn.executescript(next_projection.SCHEMA)
    conn.execute("INSERT INTO watermarks VALUES ('records', ?, 0, 0)", (RECORDS_MARK,))
    origin = next_projection.Origin("records", "sha256:" + "0" * 64, RECORDS_MARK)
    commits = [f"commit:{hashlib.sha1(str(i).encode()).hexdigest()}" for i in range(length + 1)]
    deploys = [f"deploy:{39978659174 + i}/dev" for i in range(length + 1)]
    for i, deploy in enumerate(deploys):
        for commit in commits[i : i + 2]:
            next_projection.put_edge(
                conn, origin, deploy, commit, "includes", RECORDS_MARK, "derived", "deploy-range"
            )
    conn.commit()
    conn.close()
    return commits, deploys


def hop(node, number, edge_type, direction, came_from, method, rule=None, source="pulls"):
    found = {"type": edge_type, "direction": direction, "from": came_from, "method": method}
    edge = found | {"rule": rule, "source": source}
    return {"id": node, "type": node.split(":", 1)[0], "hop": number, "edge": edge}


def test_query_neighbors_gives_the_nodes_by_hop_over_one_edge_type_with_method_and_rule(
    tmp_path,
):
    pr_list = json.dumps(json.loads(GH_PR_LIST) + json.loads(PR577_TEXT))
    built_pulls(tmp_path, gh=saved_gh(pr_list=pr_list))
    project = tmp_path / "project"
    now = at("2026-10-23T02:00:00Z")
    answer = next_projection.query_neighbors(project, PR580, "serves", hops=2, now=now)
    # #580 and #577 both serve 30.4; #580's other edges are not followed
    assert answer == {
        "matches": [
            hop("intent:30.4", 1, "serves", "out", PR580, "declared"),
            hop(PR577, 2, "serves", "in", "intent:30.4", "declared"),
        ],
        "as_of": {"pulls": PULLS_MARK},
        "stale": [],
    }
    one = next_projection.query_neighbors(project, PR580, "serves", hops=1, now=now)
    assert [m["id"] for m in one["matches"]] == ["intent:30.4"]


def test_neighbors_past_four_hops_is_refused_and_four_hops_reach_four(tmp_path):
    project = tmp_path / "project"
    commits, deploys = chain_store(project, 4)
    now = at("2026-10-23T15:00:00Z")
    with pytest.raises(ValueError, match=r"^--hops is at most 4$"):
        next_projection.query_neighbors(project, commits[0], hops=5, now=now)
    r = run_script("query", "neighbors", commits[0], "--hops", "5", "--project", str(project))
    assert (r.returncode, r.stdout, r.stderr) == (2, "", "next_projection: --hops is at most 4\n")
    four = next_projection.query_neighbors(project, commits[0], hops=4, now=now)
    reached = [(m["id"], m["hop"]) for m in four["matches"]]
    assert reached == [(deploys[0], 1), (commits[1], 2), (deploys[1], 3), (commits[2], 4)]


def test_query_path_gives_the_shortest_path_over_typed_edges_as_ordered_hops(tmp_path):
    built_pulls(tmp_path, specs=("slack-demo-surface.md",))
    project = tmp_path / "project"
    r54 = "requirement:slack-demo-surface/R54/v6"
    issue = "tracker_issue:DOC-1015"
    answer = next_projection.query_path(project, r54, issue, now=at("2026-10-23T02:00:00Z"))
    assert answer == {
        "matches": [
            hop(PR591, 1, "implementedBy", "out", r54, "derived", "merged-closes"),
            hop(issue, 2, "tracks", "out", PR591, "declared"),
        ],
        "as_of": {"pulls": PULLS_MARK},
        "stale": [],
    }


def test_a_path_longer_than_8_hops_is_no_path_with_the_no_match_text(tmp_path):
    project = tmp_path / "project"
    commits, deploys = chain_store(project, 4)
    now = at("2026-10-23T15:00:00Z")
    eight = next_projection.query_path(project, commits[0], commits[4], now=now)
    assert [m["hop"] for m in eight["matches"]] == list(range(1, 9))
    assert eight["matches"][-1]["id"] == commits[4]
    nine = next_projection.query_path(project, commits[0], deploys[4], now=now)
    assert nine == {"matches": [], "as_of": {"records": RECORDS_MARK}, "stale": []}
    assert next_projection.answer_lines(nine) == [f"no match as of records {RECORDS_MARK}"]


def test_a_hub_with_more_than_200_neighbours_returns_the_cap_and_more(tmp_path):
    built_pulls(tmp_path, gh=saved_gh(pr_list=many_pulls(205)))
    project = tmp_path / "project"
    hub = "spec:slack-demo-surface"
    answer = next_projection.query_neighbors(
        project, hub, "implements", hops=1, now=at("2026-10-23T02:00:00Z")
    )
    ids = sorted(f"pull_request:acme/platform#{1000 + i}" for i in range(205))
    assert [m["id"] for m in answer["matches"]] == ids[:200]
    assert (answer["more"], answer["hint"]) == (
        5,
        f"narrow by edge type or fewer hops: query neighbors {hub} --type <edge type> --hops <n>",
    )


def test_query_timeline_lists_every_dated_fact_in_time_order_with_the_original_form(
    tmp_path, new_york
):
    (tmp_path / "pulls").mkdir()
    built_pulls(tmp_path / "pulls", specs=("slack-demo-surface.md",))
    now = at("2026-10-23T02:00:00Z")
    answer = next_projection.query_timeline(tmp_path / "pulls" / "project", PR591, now=now)
    created = "2026-10-23T01:42:13Z"
    r54 = "requirement:slack-demo-surface/R54"

    def fact(when, name):
        return {"at": when, "original": when, "fact": name}

    def edge(when, name, direction, node, method, rule=None):
        found = {"direction": direction, "node": node, "method": method, "rule": rule}
        return fact(when, name) | found | {"source": "pulls"}

    (match,) = answer["matches"]
    assert (match["id"], match["type"]) == (PR591, "pull_request")
    assert match["facts"] == [
        edge(created, "closes", "out", r54, "declared"),
        fact(created, "createdAt"),
        edge(created, "implements", "out", "spec:slack-demo-surface", "declared"),
        edge(created, "serves", "out", "intent:30.10", "declared"),
        edge(created, "tracks", "out", "tracker_issue:DOC-1015", "declared"),
        edge(PULLS_MARK, "implementedBy", "in", f"{r54}/v6", "derived", "merged-closes"),
        edge(PULLS_MARK, "mergedAs", "out", f"commit:{MERGE591}", "observed"),
        fact(PULLS_MARK, "mergedAt"),
    ]
    # a ruling's time is kept as the store wrote it beside its UTC form
    project = query_project(tmp_path / "rulings")
    ruled = next_projection.query_timeline(project, "ruling:rul-97b833e6b1", now=now)
    assert ruled["matches"][0]["facts"] == [
        {"at": "2026-10-23T00:25:21Z", "original": "2026-10-22 20:25:21 EDT", "fact": "at"}
    ]


# Intent fixtures. gh-pr-530.json: `gh pr view 530 --repo acme/platform --json <PR_FIELDS>`,
# read 2026-10-24 11:59:18 EDT, merged into main under Intent: 30.1 as 763e5a5e. Its deploy: the
# customer-docs record's dev deploy of 2026-10-21T01:42:18Z (run 39527024128, sha 707e7562), whose
# range from the observability record's deploy of 2026-10-20T19:49:35Z (run 39482122587, sha
# 7df15c5f), the previous successful dev deploy, holds 763e5a5e (git rev-list in the
# acme/platform checkout, read 11:58:36 EDT); the three shas are written as fixturelib commits
# in that order.
PR530_TEXT = (FIXTURES / "projection" / "gh-pr-530.json").read_text(encoding="utf-8")
PR530 = "pull_request:acme/platform#530"
DEPLOY_BEFORE_530 = (
    "- 2026-10-20T19:49:35Z deploy.completed sha={} run=39482122587 conclusion=success"
)
DEPLOY_OF_530 = "- 2026-10-21T01:42:18Z deploy.completed sha={} run=39527024128 conclusion=success"


def intent_build(tmp_path):
    names = ("before", "merge", "deploy")
    files = {f"{name}.txt": f"{name}\n" for name in names}
    repo = fixture_repo(tmp_path, files, tuple((name, (f"{name}.txt",)) for name in names))
    shas = dict(zip(names, git(repo, "rev-list", "--reverse", "HEAD").split(), strict=True))
    record = repo / "docs" / "records" / "deploys.md"
    record.parent.mkdir(parents=True)
    events = f"{DEPLOY_BEFORE_530.format(shas['before'])}\n{DEPLOY_OF_530.format(shas['deploy'])}\n"
    record.write_text(RECORD_HEADER.format(slug="deploys", spec="deploys.md") + events, "utf-8")
    (pr530,) = json.loads(PR530_TEXT)
    pr529 = next(p for p in json.loads(GH_PR_LIST) if p["number"] == 529)
    pr_list = json.dumps([pr530 | {"mergeCommit": {"oid": shas["merge"]}}, pr529])
    project = tmp_path / "project"
    next_projection.build(repo, project, gh=saved_gh(pr_list=pr_list))
    return project, pr530, pr529


def test_query_intent_lists_merged_pull_requests_with_their_deploy_and_open_ones(tmp_path):
    # Requirement 16's intent report, scenario S4: merged with their deploy, then open ones
    project, pr530, pr529 = intent_build(tmp_path)
    answer = next_projection.query_intent(project, "30.1", now=at("2026-10-23T02:00:00Z"))
    assert answer["matches"] == [
        {
            "id": PR530,
            "type": "pull_request",
            "state": "MERGED",
            "title": pr530["title"],
            "mergedAt": "2026-10-20T21:59:42Z",
            "deploys": ["deploy:39527024128/dev"],
        },
        {
            "id": PR529,
            "type": "pull_request",
            "state": "OPEN",
            "title": pr529["title"],
            "stackedOn": None,
        },
    ]
    assert set(answer["as_of"]) == {"pulls", "records"}
    # --state, the filter a capped answer's hint names
    for state, ids in (("merged", [PR530]), ("open", [PR529])):
        only = next_projection.query_intent(project, "30.1", state, now=at("2026-10-23T02:00:00Z"))
        assert [m["id"] for m in only["matches"]] == ids


def test_an_intent_with_no_pull_requests_is_the_closed_world_empty_answer(tmp_path):
    project, _, _ = intent_build(tmp_path)
    answer = next_projection.query_intent(project, "30.99", now=at("2026-10-23T02:00:00Z"))
    assert answer["matches"] == []
    # nothing matched, so every source was touched
    assert set(answer["as_of"]) == {"pulls", "records"}
    assert next_projection.answer_lines(answer)[0].startswith("no match as of pulls ")


def test_a_stacked_chain_of_three_open_pull_requests_lists_base_first(tmp_path):
    # scenario S4's base-chain order (Requirement 16)
    # #580 on #577's head as they stood open, and a copy of #580 numbered 576 on #580's head
    pr580 = next(p for p in json.loads(GH_PR_LIST) if p["number"] == 580)
    (pr577,) = json.loads(PR577_TEXT)
    unmerged = {"state": "OPEN", "mergedAt": None, "mergeCommit": None}
    pr576 = pr580 | unmerged | {"number": 576, "headRefName": pr580["headRefName"] + "-next"}
    pr576["baseRefName"] = pr580["headRefName"]
    built_stack(tmp_path, json.dumps([pr576, pr580 | unmerged, pr577 | unmerged]))
    answer = next_projection.query_intent(tmp_path / "project", "30.4")
    pr576_id = "pull_request:acme/platform#576"
    chain = [(m["id"], m["stackedOn"]) for m in answer["matches"]]
    assert chain == [(PR577, None), (PR580, PR577), (pr576_id, PR580)]


def test_query_timeline_since_lists_the_facts_from_that_time_and_a_capped_one_names_it(tmp_path):
    for name in ("one", "hub"):
        (tmp_path / name).mkdir()
    built_pulls(tmp_path / "one", specs=("slack-demo-surface.md",))
    project = tmp_path / "one" / "project"
    now = at("2026-10-23T02:00:00Z")
    later = next_projection.query_timeline(project, PR591, since=PULLS_MARK, now=now)
    facts = [f["fact"] for f in later["matches"][0]["facts"]]
    assert facts == ["implementedBy", "mergedAs", "mergedAt"]
    r = run_script("query", "timeline", PR591, "--since", PULLS_MARK, "--project", str(project))
    assert (r.returncode, r.stderr) == (0, "")
    assert json.loads(r.stdout)["matches"] == later["matches"]
    # a timeline over the cap names --since, which reads on from the last fact it listed
    built_pulls(tmp_path / "hub", gh=saved_gh(pr_list=many_pulls(205)))
    hub = "spec:slack-demo-surface"
    capped = next_projection.query_timeline(tmp_path / "hub" / "project", hub, now=now)
    assert (len(capped["matches"][0]["facts"]), capped["more"]) == (200, 5)
    assert capped["hint"] == f"narrow by time: query timeline {hub} --since <time>"


def test_open_pull_requests_left_in_a_stacked_cycle_follow_by_number(tmp_path):
    # two copies of #580 numbered 999 and 1000, each open on the other's head branch, so neither
    # has a base among them
    pr580 = next(p for p in json.loads(GH_PR_LIST) if p["number"] == 580)
    unmerged = {"state": "OPEN", "mergedAt": None, "mergeCommit": None}
    first = pr580 | unmerged | {"number": 999, "headRefName": "cycle-a", "baseRefName": "cycle-b"}
    second = pr580 | unmerged | {"number": 1000, "headRefName": "cycle-b", "baseRefName": "cycle-a"}
    built_stack(tmp_path, json.dumps([first, second]))
    answer = next_projection.query_intent(tmp_path / "project", "30.4")
    assert [m["id"] for m in answer["matches"]] == [
        "pull_request:acme/platform#999",
        "pull_request:acme/platform#1000",
    ]


def test_query_coverage_lists_each_requirement_with_its_closing_pull_requests(tmp_path):
    # scenario S6 (Requirement 16): #577 merged closes R1; #580, closed unmerged, names R2
    built_stack(tmp_path, json.dumps(json.loads(GH_PR_LIST) + json.loads(PR577_TEXT)))
    project = tmp_path / "project"
    now = at("2026-10-23T02:00:00Z")
    answer = next_projection.query_coverage(project, "shadow-agents", now=now)
    assert answer["matches"] == [
        {"id": "requirement:shadow-agents/R1/v4", "type": "requirement", "closedBy": [PR577]},
        {"id": "requirement:shadow-agents/R2/v4", "type": "requirement", "closedBy": []},
    ]
    assert next_projection.query_coverage(project, "no-such-capability")["matches"] == []


# Landed fixtures (scenario S5, Requirement 16, rul-0723e3dbdc and rul-982e177974), read
# 2026-10-24: landed/gh-pr-list.json, `gh pr view <n> --repo acme/platform --json <PR_FIELDS>`
# for #602 to #613 merged on 2026-10-23 (#609, open, left out) at 13:18:12 EDT;
# landed/gh-run-list.json, the runs created 2026-10-23T05:00:00Z to 16:30:00Z of `gh run list
# --repo acme/platform --workflow deploy.yml --limit 10000 --json
# databaseId,displayTitle,status,conclusion,createdAt` at 13:04:35 EDT;
# landed/handoff-2026-10-23.md, lines 121 to 136 of the Acme workspace's
# docs/handoff/2026-10-23.md, the Landed section landed.py --format md printed at that close;
# landed/setup-plan-now.md, lines 1223, 1233 and 1241 to 1262 of docs/setup-plan.md at engagement
# commit 4110ea2 (2026-10-23 11:26:19 EDT), the Now table landed.py read then.
LANDED = FIXTURES / "projection" / "landed"
LANDED_TABLE = (LANDED / "handoff-2026-10-23.md").read_text(encoding="utf-8").splitlines()[4:]
LANDED_RUNS = (LANDED / "gh-run-list.json").read_text(encoding="utf-8")


def landed_project(tmp_path, runs=LANDED_RUNS):
    target = git_repo(tmp_path / "target")
    workflow = target / ".github" / "workflows" / "deploy.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: deploy\n", encoding="utf-8")
    project = tmp_path / "project"
    for rel, name in (
        ("docs/handoff/2026-10-23.md", "handoff-2026-10-23.md"),
        ("docs/setup-plan.md", "setup-plan-now.md"),
    ):
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_bytes((LANDED / name).read_bytes())
    pr_list = (LANDED / "gh-pr-list.json").read_text(encoding="utf-8")
    next_projection.build(with_origin(target), project, gh=saved_gh(pr_list=pr_list, runs=runs))
    return project


def test_query_landed_prints_the_landed_table_in_the_landed_py_shape(tmp_path):
    project = landed_project(tmp_path)
    # a window whose Until is its Since holds no merge: the two header lines alone
    same = "c138ffc1150f7604c9a126c071226b4927579874"
    handoff = project / "docs" / "handoff" / "2026-10-24.md"
    handoff.write_text(f"## Landed\nSince: {same}\nUntil: {same}\n", encoding="utf-8")
    assert next_projection.query_landed(project, "2026-10-24") == [
        "| PR | Title | Merged | Run | Conclusion | Intent |",
        "|---|---|---|---|---|---|",
    ]
    r = run_script("query", "landed", "--date", "2026-10-25", "--project", str(project))
    assert r.returncode == 1
    assert "no Handoff at docs/handoff/2026-10-25.md" in r.stderr


def test_query_landed_for_a_live_handoff_day_is_byte_identical_to_its_table(tmp_path):
    project = landed_project(tmp_path)
    assert next_projection.query_landed(project, "2026-10-23") == LANDED_TABLE
    r = run_script("query", "landed", "--date", "2026-10-23", "--project", str(project))
    assert (r.returncode, r.stderr) == (0, "")
    assert r.stdout == "\n".join(LANDED_TABLE) + "\n"


def test_a_merge_no_deploy_run_names_prints_the_no_run_form(tmp_path):
    # without run 39984688948, the one titled with #613's merge, no run covers #613
    runs = [r for r in json.loads(LANDED_RUNS) if r["databaseId"] != 39984688948]
    project = landed_project(tmp_path, runs=json.dumps(runs))
    table = next_projection.query_landed(project, "2026-10-23")
    assert table[:-1] == LANDED_TABLE[:-1]
    assert table[-1] == (
        "| #613 | feat(doccheck): the Napufag emezapo vuvupome, mir run epime and the azilib in the"
        " docs uve (DOC-1036) | 2026-10-23 12:05:11 EDT | none | PENDING (no deploy run titled with"
        " this merge or a later one) | 30.12 |"
    )


def test_an_unreachable_runs_read_keeps_the_runs_stale_and_the_table_whole(tmp_path):
    # rul-982e177974: the runs source fails as the pulls source does, its rows carried forward
    project = landed_project(tmp_path)
    next_projection.build(tmp_path / "target", project, gh=offline_gh)
    newest = max(run["createdAt"] for run in json.loads(LANDED_RUNS))
    assert f"runs {newest} stale" in next_projection.status_lines(project)
    assert next_projection.query_landed(project, "2026-10-23") == LANDED_TABLE
