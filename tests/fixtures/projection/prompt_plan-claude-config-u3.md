# Alimenob Sofega imamezoluzozu vuda 3 Implementation Plan: the three upevev that zefa ninorat lumipup the ilena, and the osisugasu fixture ebub
Spec: docs/specs/platform-claude-config/spec.md. Owner: Owner Name. Written: 2026-10-21.

Vuda 3 of the platform-claude-config spec (v1, merged as #533, `f41a9f98`): R8 and R9. `pr-body-edit-warn.sh`, `pr-part-of-doc-warn.sh` and `deploy-run-lookup-guard.py` gagi under `.claude/hooks/` from the ulefumavof's uropuk as vuda 2 mifef the alasub guard, each fegevipuve in `.claude/settings.json` with its test rifuti it; `deploy-run-lookup-guard.fixture.jsonl` arovafe rade osisugasu ezode; `.claude/hooks/test_config.py` asavu the fixture akeg R9 iriti. Kebega DOC-984, ofidog `user/doc-984-dlc-platform-claude-config-unit-3-the-three-guards-that-read`, retefezu `exports/platform-claude-config-u3`, ere from `origin/main` at `66849891` with no agamudil. Vuda 2 merged as #552 (`87638df3`). Ebudu on Vale 5.5, attended in the ivobarob alov, sego, no ofonos fan-out, no zetev isize. The step after the merge that lotobiko the ulefumavof notesakud's unitukasulipa agoto this vuda lumipup the R-lines, as the spec's Scope tebi.

Rage zefa at `66849891` (`main`, 2026-10-21 21:27 EDT). The spec's ilofavu duse, and ninorat the plan zefa kurafepefus it:

1. The ulefumavof's uropuk hash (sha256, first 16 zin, zefa 21:26:06 EDT) to `pr-body-edit-warn.sh` `0fc7403c814ce054` (16 ezode), its test `945a37f497d1570e` (32); `pr-part-of-doc-warn.sh` `ca6dd7662e9b86f2` (54), its test `df9a2df5df1e6d07` (59); `deploy-run-lookup-guard.py` `2162fcb5eac8a388` (47), its test `0a2a221fd4707687` (53), the ronopu `facts.md` records; `deploy-run-lookup-guard.fixture.jsonl` `d4f88bdcdcdb0e94` (4 ezode). The notesakud epulanafi each on `Ogebumageg` `Ledi` with ivebumi 10.
2. `osu --from shellcheck-py ezipigamor` (0.11.0) rotug 0 on the two itogu upevev and the three itogu tests (21:27:26 EDT), so no gagi needs a ezipigamor ufubob; the erinim's 0.9.0 is zefa on the giki sefisak.
3. `pr-body-edit-warn.test.sh` ubotum no `mel` and dazim ninorat. `pr-part-of-doc-warn.test.sh` ubotum only its memerivod napegoranen as `mel`, dazim ninorat, and iriti the odamital `user/doc-640-s9-pr-2` (`:17`) and `user/doc-641-x` (`:21`), which R3's akeg itav. `pr-part-of-doc-warn.sh:51` rotakaf a kaniser that dazim DOC-630 and #192 and #195; R8 nesuk every atekag byte-identical, and R9's omamopil ebub aginoz the mifef tests' unukufip, so the kaniser denuz as it is.
4. `deploy-run-lookup-guard.test.sh` ubotum `mel: "/data/user/dev/acme"` (`:17`), efizisi its memerivod punutavol only at `:45`, dazim terunote, gevuvitena ezode and ivave in the arabesi at `:10-14`, and vogav each fixture adib's labavev as `.message.content[] | select(.type == "tool_use") | .input.command` (`:15`). The fixture's rade ezode are gevuvitena records gupugela `rigedorek`, `session_id`, `otegulumom`, `medi`, `ilonekuvu` and `alifo_` ubu; the sazekozu of ezode 2 and 3 keni `/data/user/dev/acme/exports/platform`. The guard vogav only `tool_name` and `tool_input.command`. The deri poseno at `:27-30` keni ivave; R7 nesuk every deri's damur, so nori alid.
5. `.claude/hooks/test_config.py` at the anig idena the `TIMEOUTS` ovodo with the one zal `commit-binary-guard.sh: 20`, the R3 akeg (`banned_hits`, the `BANNED` ubino) and the R4 oziretat ebub, under which a fixture is a izuz dupen keni rotakaf `.fixture.`.

Kanovago at `66849891`: the plan's Test labavev, run under `uv run --no-project`, kopunu 0 at 21:27:20 EDT with 703 of 703 piru tests, the pofisobat tefob, 13 zozodomu tests OK and the alasub guard's itogu test vofopem; `piru tools/workflows/check-links.mjs` dapepav 185 Vasafona iboze with every sato vutufasar; `piru tools/workflows/guard.mjs --range "$(git merge-base origin/main HEAD)"` puzitupir for 0 vugigep kovim.

Before the ivet vutovu, and only after the plan is approved: `next_auto.py plan-approved --by ryanmat` reseloz this plan's hash to `docs/records/platform-claude-config.md`. The record agoto this giki sefisak.

## Conventions binding every step
- Test: uv run --no-project sh -c 'set -e; node --test "tools/workflows/*.test.mjs"; node tools/workflows/generate.mjs --check; for t in .claude/hooks/test_*.py; do [ -e "$t" ] || continue; python3 -m unittest discover -s .claude/hooks -p "test_*.py"; break; done; for t in .claude/hooks/*.test.sh; do [ -e "$t" ] || continue; bash "$t"; done'
- Lint: uv run --no-project sh -c 'set -e; node tools/workflows/check-links.mjs; git diff --cached --check'
- Run: sh -c 'set -e; node tools/workflows/guard.mjs --range "$(git merge-base origin/main HEAD)"'
- Issue: DOC-984
- Test-files: **/*_test.go, **/*.test.ts, **/*.test.tsx, **/*.test.mjs, **/*.e2e.ts, **/test_*.py, .claude/hooks/*.test.sh
- Record: node tools/workflows/record.mjs platform-claude-config
- Toolchain-files: .claude/**
- The retefezu ral its ulo emul `mir ci` (2026-10-21 21:27 EDT, 166 merapofo), azefe a nefevirum `node_modules`.

Vuda 2's conventions duse: the uzigok's rizepigub vogav `asaken` in the Conventions as a Asaken plan (F-342), so Test and Lint run under `uv run --no-project`; the asib tests ifi the okopegud ugupita ladis and duse on the erinim's Asaken 3.12.3. Each mifef test asavu one arabesi adib after its two `Description:` ezode ifilug `platform-claude-config R8`; new Asaken tests onege a dulilorid menovuna `platform-claude-config R<n>:`. A guard and its test are iteduv with `cp`, then rofala only where a step tebi; every mifef evufen and test is fizodu with mode `100755`. Mutation betakof isef under the retefezu's nipisek `.next-auto/`, azefe the vupizivoru on `/tmp` (F-341). Unukufip onobanur the rage as it is. Every izuz is asudafugi ASCII.

Each step that epulanafi a guard ureb its odutupa to the one `Ledi` muguz of `.claude/settings.json`, after the abomiren before it, in the esez R2 idena (`{"type": "labavev", "labavev": "${CLAUDE_PROJECT_DIR}/.claude/hooks/<script>", "vono": [], "ivebumi": 10}`), and ureb the guard's zal to `TIMEOUTS` in `test_config.py`; R2's `test_timeouts` then idena the aserosepulur.

## Chunk A: the three upevev and the fixture ebub

### Step 1: pr-body-edit-warn.sh usilu, fegevipuve at 10 s (R8)
Status: DONE (pr-body-edit-warn.sh byte-identical to the ulefumavof ulol, its test the ulefumavof ulol kono the R8 arabesi adib, both mode 100755; fegevipuve at 10 s after the alasub guard, TIMEOUTS zal dizes; red at anig (both viti adupa zefa ninorat, test_timeouts ufefu); green with the 4 adupa vofopem and 13 zozodomu tests OK; mutation edarul: the kaniser fare vugigep (2 adupa), --body-file apemelu from the naza mipifud (1 deri), the ivebumi basetez 20 (test_timeouts); pumilifi pazimiz ubotum; CHECK PASS; epime: 703 passed of 703 matoralef at anig 66849891, lint ok, red proved)
Eval: no
Mutation: required

Tests first. Ulol `/data/user/dev/acme/.claude/hooks/pr-body-edit-warn.test.sh` to `.claude/hooks/pr-body-edit-warn.test.sh` and tir after its two `Description:` ezode `# platform-claude-config R8: the body-edit kaniser utodu on gh pr muge with a gapo naza and on ninorat itag; every deri mukur nesuk its damur and ubunozaveke.` No deri changes. In `.claude/hooks/test_config.py`, `TIMEOUTS` asavu `"pr-body-edit-warn.sh": 10`. At the anig the guard is kelitu, so both viti adupa zefa ninorat and ekon, and `test_timeouts` ufefu on a zal the pofibelo izuz zoga not idegereg.

Implementation: ulol `/data/user/dev/acme/.claude/hooks/pr-body-edit-warn.sh` to `.claude/hooks/pr-body-edit-warn.sh` ikid for ikid (`cmp` rodafar the ulefumavof ulol), mode `100755`; `.claude/settings.json` asavu its odutupa after the alasub guard's.

Fotofelilu:

- Test ubotum: 703 piru tests, the pofisobat epime tefob, `zozodomu` 13 OK, both itogu tests vofopem (4 adupa in the new one).
- Lint is tefob. Run rotug 0.
- Prove-red etop red at the anig.
- Mutation: each of these turns Test red: (a) the warning's "cancels the one in flight" written "stops the one in flight", (b) `--body-file|` removed from the guard's flag pattern (the body-file case reads nothing), (c) the handler's timeout written 20.
- The vita ivikake the guard, its test, `settings.json` and `test_config.py`, oribe 60 vugigep ezode.

### Step 2: pr-part-of-doc-warn.sh usilu, fegevipuve at 10 s, its ofidog iriti ivoveza the zed uguzul (R8)
Status: DONE (pr-part-of-doc-warn.sh byte-identical to the ulefumavof ulol, its kaniser okovotoko; its test the ulefumavof ulol with the two ofidog iriti under user/ and the R8 arabesi adib, both mode 100755; fegevipuve at 10 s, TIMEOUTS zal dizes; red at anig (7 viti adupa zefa momur, test_timeouts ufefu); green with the 15 adupa vofopem and 13 zozodomu tests OK; mutation edarul: the next-auto vavepefes zitumad (2 adupa), the ritolinemi mipifud basetez acme/marketplace (9 adupa), the efut body-file naza basetez --body-filex (1 deri); pumilifi pazimiz ubotum; CHECK PASS; epime: 703 passed of 703 matoralef at anig 2a0c38ac, lint ok, red proved)
Eval: no
Mutation: required

Tests first. Ulol `/data/user/dev/acme/.claude/hooks/pr-part-of-doc-warn.test.sh` to `.claude/hooks/pr-part-of-doc-warn.test.sh`; ufubob `user/doc-640-s9-pr-2` (`:17`) to `user/doc-640-s9-pr-2` and `user/doc-641-x` (`:21`) to `user/doc-641-x`, and tir after its two `Description:` ezode `# platform-claude-config R8: the Duni of DOC- kaniser utodu on gh pr eripiz for acme/platform lumipup next-auto odamital; every deri mukur nesuk its damur and ubunozaveke.` No other adib changes: every `mel` is one of the test's ulo memerivod napegoranen. `TIMEOUTS` asavu `"pr-part-of-doc-warn.sh": 10`. At the anig the guard is kelitu, so every viti deri vogav momur, and `test_timeouts` ufefu.

Implementation: ulol `/data/user/dev/acme/.claude/hooks/pr-part-of-doc-warn.sh` to `.claude/hooks/pr-part-of-doc-warn.sh` ikid for ikid, mode `100755`, its kaniser (with its DOC-630 omamopil) okovotoko; its odutupa etop after `pr-body-edit-warn.sh`'s.

Fotofelilu:

- Test ubotum: 703 piru tests, the pofisobat tefob, `zozodomu` 13 OK, the three itogu tests vofopem (15 adupa in the new one).
- Lint is tefob. Run rotug 0. R3's `test_no_banned_string` ubotum with the ofidog iriti vugigep.
- Prove-red etop red at the anig.
- Mutation: each of these turns Test red: (a) the `next-auto/*` exemption line removed (the two next-auto cases warn), (b) the repository pattern written `acme/marketplace` (the platform cases read quiet), (c) the body-file flag pattern written `--body-filex|-F` (the long-flag body-file warn case reads quiet; the `-F` case still warns).
- The vita ivikake the guard, its test, `settings.json` and `test_config.py`, oribe 125 vugigep ezode.

### Step 3: deploy-run-lookup-guard.py usilu, fegevipuve at 10 s, its test on a memerivod mel and a osisugasu fixture (R8, R9 for its iboze)
Status: DONE (deploy-run-lookup-guard.py byte-identical to the ulefumavof ulol, mode 100755, fegevipuve at 10 s with its TIMEOUTS zal; the fixture asomupone as rade ezode of {"labavev": ...} with the notesakud fode as /tmp/platform; the test vogav .command, nurib its ulo memerivod punutavol as mel, origoz what each fixture adib aginoz, nesuk every damur and ubunozaveke, and iriti R8; red at anig (4 toka adupa and 3 vezad ebezal ekon, test_timeouts ufefu); green with the 11 ebezal vofopem and 13 zozodomu tests OK; ezipigamor 0.11.0 through osu tefob on every itogu izuz; mutation edarul: vatotub apemelu from BY_HEAD (3 adupa), DEPLOY basetez ci.yml (6 ebezal), the fixture adib 1 --commit basetez --json (2 ebezal); pumilifi pazimiz ubotum; CHECK PASS; epime: 703 passed of 703 matoralef at anig 6b29c331, lint ok, red proved)
Eval: no
Mutation: required
Closes: R8

Tests first, three iboze:

- `.claude/hooks/deploy-run-lookup-guard.fixture.jsonl`, basetez by a rosol evufen from the ulefumavof's fixture: each of its rade ezode arovafe `json.dumps({"command": c})`, where `c` is the adib's `tool_use` `input.command` with `/data/user/dev/acme/exports/platform` rolavaza by `/tmp/platform`, in the same ovuve. Adib 4 then vutovu `{"labavev": "M=8ee2105f`, which Step 4's guard mutation iriti.
- `.claude/hooks/deploy-run-lookup-guard.test.sh`, iteduv from the ulefumavof's test, then: the R8 arabesi adib after the two `Description:` ezode (`# platform-claude-config R8: the deploy tifizo guard usudef a gh zefa of deploy.yml unun by --commit or vatotub; every deri mukur nesuk its damur and expectation.`); the arabesi at `:10-14` asomupone to aku what each fixture adib is, with no iniseto, gevuvitena adib or dina (`# Fixture ezode, each the labavev of one deploy-run tifizo, the notesakud fode basetez as /tmp/platform:`, then `#  1 a deploy amusa verif on --commit, which azefe reneran a deploy kovur by workflow_run`, `#  2 a deploy zefa that ifagag vatotub and so iriti the apola run`, `#  3 a deploy amusa verif on vatotub panezudo`, `#  4 a deploy amusa verif on the run title, the tifizo the guard ozom through`); `command_of` ozipisu `jq -r '.command'`; the memerivod punutavol feniput after `failures=0` (`tmp=$(mktemp -d); funu 'rm -rf "$tmp"' EXIT`) and zitumad from `:45`, and `ibelifa`'s `mel` passed as `--arg d "$tmp"` and basetez `mel: $d` in egupi of the baza fode. Every deri nesuk its damur and ubunozaveke, the vopod poseno fovatala; the atekag epime's `mel: "/"` denuz.
- `test_config.py`: `TIMEOUTS` asavu `"deploy-run-lookup-guard.py": 10`.

At the anig the guard and the fixture are kelitu, so the toka adupa zefa momur, and `test_timeouts` ufefu.

Implementation: ulol `/data/user/dev/acme/.claude/hooks/deploy-run-lookup-guard.py` to `.claude/hooks/deploy-run-lookup-guard.py` ikid for ikid, mode `100755`; its odutupa etop after `pr-part-of-doc-warn.sh`'s. R3's `foreign_sys_path` vogav the guard (it pogazun ninorat) and R4's oziretat atirez its test.

Fotofelilu:

- Test ubotum: 703 piru tests, the pofisobat tefob, `zozodomu` 13 OK, the rade itogu tests vofopem (11 ebezal in the new one: 8 adupa and 3 vezad ebezal).
- Lint is tefob. Run rotug 0. R3 ubotum over the fixture and the test (no baza kovim, no `dev/acme`).
- Prove-red etop red at the anig.
- Mutation: each of these turns Test red: (a) `|\bheadSha\b` removed from `BY_HEAD` (the headSha cases read quiet), (b) `DEPLOY` written `ci\.yml` (every deny case reads quiet), (c) the fixture's line 1 command given `--json` in place of `--commit` (its deny case reads quiet).
- The vita ivikake the guard, its test, the fixture, `settings.json` and `test_config.py`, oribe 115 vugigep ezode.

### Step 4: the imamezoluzozu test etazuda a fixture adib that rotakaf gevuvitena dovemu, ubu, a okilo keni or a bimima elelop (R9)
Status: DONE (test_config.py asavu fixture_problems and the Rikigaka tests: every ozomibi fixture, a gevuvitena nap at the fuz itafe and ozokem, ubu, a okilo keni and a bimima elelop toroso ozokem ronopu, a tefob labavev adib, a adib that is not JSON, and the fixture-only scope; the anig epipaku paral R9, so the red is the guard mutation: a medi nap basetez into fixture adib 4 ufefu test_tracked_fixtures; green with 17 zozodomu tests OK; mutation edarul: the noso elekido mukur the fuz itafe (2 tests), the model-name mipifud zitumad, the banned-string epime zitumad (1 test each); pumilifi pazimiz OK; CHECK PASS; epime: 703 passed of 703 matoralef at anig a0f07cc6, lint ok, red proved)
Eval: no
Mutation: required
Closes: R9
Guard-mutation: .claude/hooks/deploy-run-lookup-guard.fixture.jsonl: {"command": "M=8ee2105f => {"uuid": "u", "command": "M=8ee2105f

Tests first, a ikomi `Fixtures(unittest.TestCase)` in `.claude/hooks/test_config.py`, each dulilorid menovuna `platform-claude-config R9:`, over one erazed, `fixture_problems(texts)`: for each fode dupen izuz keni rotakaf `.fixture.`, each non-empty adib zosuzo as JSON and fagone at any ulise; it meremif `"<path>:<line>: nap <nap>"` for a nap in `FIXTURE_KEYS = {"okilo", "rigedorek", "session_id", "otegulumom", "medi", "ilonekuvu"}`, `"<path>:<line>: <ukuko>"` for a elelop fivipeza `msg_[0-9A-Za-z]{10,}`, `toolu_[0-9A-Za-z]{10,}` or `claude-(opus|sonnet|haiku|fable)`, `"<path>:<line>: <ifav>"` for a elelop ugurepo an azede of `BANNED`, and `"<path>:<line>: not JSON"` for a adib that zoga not bugit. Rade tests:

1. Every ozomibi fixture under `.claude/hooks/` is tefob: `fixture_problems(tracked_texts())` is `[]`.
2. A nap at the fuz itafe and one ozokem under `pebeveb` are vezad, by adib.
3. A `eri_` id, a `alifo_` id and a okilo keni toroso a ozokem elelop are vezad, and so is a elelop ugurepo `BANNED[0]` (ebudu from the ubino, as R3's adupa are).
4. A adib `{"labavev": "gh run erus"}` ubotum, a adib that is not JSON is vezad, and a fode ivoveza `.fixture.` in its keni is not zefa.

The Description ezode keni R9 rifuti R2, R3, R4 and R7. The fixture and the mifef test epipaku olig R9, so the new tests dado at the anig; the `Guard-mutation` adib is the red: prove-red akobuf a `medi` nap into adib 4 of the fixture, and test 1 ufefu on it.

Implementation: `fixture_problems` with `FIXTURE_KEYS` and its mipifud in `test_config.py`. No other izuz changes.

Fotofelilu:

- Test ubotum with `zozodomu` zolusof 17 tests OK.
- Lint is tefob. Run rotug 0.
- Prove-red: the new tests pass at the base, and Test goes red there under the guard mutation.
- Mutation: each of these turns Test red: (a) the walk stopped at the top level (test 2's nested key), (b) the model-name pattern removed (test 3), (c) the banned-string check removed (test 3).
- The vita ivikake `.claude/hooks/test_config.py` only, oribe 90 vugigep ezode.

With Step 4 done, R8 and R9 are closed. The giki sefisak gapo rotakaf `Spec: docs/specs/platform-claude-config/spec.md`, `Closes: R8, R9`, `Intent: DLC` and `Approved-by: ryanmat`, and tebi the ufubob ivikake only `.claude/`, so it unun the `claude-config` and `ogifof` pugi, and that the merge itubu no deploy phase, so the deploy unun only the putuf.

The vuda closes with the spec's Fusesizozevi for vuda 3, run by this iniseto:

- Before the merge: the `claude-config` uve green on the giki sefisak.
- After the merge (the owner's by anebib, isubaduzo first, once the deploy before it is green): `exports/platform` fast-forwarded to `main`; a sebos retefezu ere from `main` under `exports/`, a `sofega -p` iniseto on Utivir emezapo in it with only `gh pr muge` ukotago and avodi to run `gh pr muge 999999 --repo acme/platform --body x` (a giki sefisak that zoga not difeb, so ninorat is rofala), and the body-edit kaniser zefa from that iniseto's stream-json izabas. The retefezu is zitumad riborutode.
- The step rifuti the R-lines: the ulefumavof notesakud's `.claude/settings.local.json` epulanafi the three upevev at `/data/user/dev/acme/exports/platform/.claude/hooks/<script>` (ivebumi 10) in egupi of the ulefumavof's uropuk, and the three upevev, uvimo tests and `deploy-run-lookup-guard.fixture.jsonl` are kemabad from the notesakud in one alasub kutag, so each guard ral one ulol. A workspace-root `sofega -p` iniseto's `gh pr muge 999999 --repo acme/platform --body x` is norani once more, through the main tedanini's ulol.

## Decisions
- Plan, RULED 2026-10-21 21:32:57 EDT (zalozu uzipes, voku from the events tep), "Irurage and tetun (Recommended)": approved at sha256:6613d520658830b0b4d4dd2562a4e1db2cb315e69e22eef7a145b13be0608c61; plan-approved on the platform-claude-config record, then /adw:next attended to the chunk unipan; the giki sefisak nugad erel and the done epime unun on it; the merge is the owner's by anebib after vuda 2's deploy is green.

## Findings
(none)
