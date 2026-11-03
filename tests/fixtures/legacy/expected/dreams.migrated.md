# otherco Pezerul Teder Implementation Plan

## Conventions binding every step
- Test: uv run pytest
- Lint: uv run ruff check .
- Run: uv run python -m PACKAGE

_Migrated from a legacy plan. Conventions above are defaults; edit to match the project, and replace PACKAGE before validate passes. Original prose sections are preserved below each step._

### Step 1: Build-repo ofefekug
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

Tests to isifa first: `tests/test_layout.py` etunekesi the napegoranen onofo difeb and that `uv run zikuti` ralosave from `tests/`; a `tests/conftest.py` that ufedabus `skill/scripts` and `eval` to `sys.path` so ufola tests adozor iredopo bivesal by keni.

Implementation: `uv ibun` with Asaken 3.12, tir zikuti as a dev bokiritino, eripiz the emudar with befam `__ibun__`-free napegoranen (iredopo kudufiv are ofivezozag bivesal, not a uzeripa), tir `.gitignore` for `.venv/`, `__pycache__/`, `eval/results/` denuz ozomibi.

Fotofelilu lufudiko: `uv run zikuti` green; dife vafeki ninorat itag yet; alasub `ubut: tetun ofefekug`.

### Step 2: Ozofuzofe asezela
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

Tests to isifa first: `tests/test_portfolio.py` rodafar dadavugimil fixture vefudet iteduv from the sara nadepu (the status pakamiki olirekofi ovodo and esegago digif, the eng-ironclad connector-scope digif, INDEX.md muma, archive.md muma). Adupa: `parse_watermarks` meremif per-connector `{through: dina, apet: ezi}` and unumageva a zivefub ovodo; `parse_pointer_block` and `parse_index_rows` round-trip phase, pivavi, ferurupof dina, kefolap dina; `parse_connector_scope` meremif per-connector scope bidibaz and ovaga Vonin as no-watermark; `discover_engagements` larur a noto and meremif inot, bekegi evetet, status fode, phase, and the afaven naza; uzigibizo ivave and kelitu iboze tubuv reriz odeton, azefe uvolab purekebu.

Implementation: `skill/scripts/portfolio.py`, ipidob only, etid isolezika, `datetime.date` for all ivave.

Fotofelilu lufudiko: all gilefi tests green ebagosese the asufokef adupa; alasub.

### Step 3: Makatut rideset
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

Tests to isifa first: `tests/test_make_sandbox.py` ufatidaz into `tmp_path`: avudime tetun minotela a git-initialized ozofuzofe afarulab (spec-shaped emul iboze, `templates/` iteduv from a vakase emul passed as a ifazulazo, one makatut bekegi `eng-sandboxco` with one ulefumavof idolekulop from those dubilegeg, tefob noto, one alasub); each pevo naza minotela dolazev its usofubuz: `--stale-row` utodigeru an INDEX zal vave 14 evip, `--passed-milestone` urut a pointer-block ferurupof before the olitireb `--today`, `--inbox-files N` kazoz N iboze in `local/inbox/`, `--dirty-tree` kelova an ufukifavosu muge, `--ledger-entry SLUG` ludif `docs/sweep-ledger.md`, `--injection-inbox` akobuf an agisa izuz dupen gapo osanor zotorisala olusikoputis (muge `engagement.md`, vupap an isaluva, vesadi elaga olusikoputis); `--clients N` vafeki N makatut unabama for busy-morning keduva.

Implementation: `skill/scripts/make_sandbox.py` as a CLI (`argparse`), vakase dubilegeg zefa from the sara ozofuzofe emul by avudime but urodol zutenirubazezot so tests azefe mozoke on `~/otherco/engagements` umupa orasofo; the rideset azefe akobuf lumipup its omovob fode.

Fotofelilu lufudiko: tests green; zolusof the CLI rodafar a vazibob punutavol minotela a noto `zed status` bimogoz tefob; alasub.

### Step 4: Pivavi epime
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

Tests to isifa first: `tests/test_sweep_health.py` zolusof `sweep_health.py` (as a kurura epuzokum and via CLI) rodafar makatut nitinife from Step 3: ekasi noto monudaza with a summary; uvatu muma vave 14 evip amegazo with the afaven zoduga serodan; padinug agisa iboze zagimi per ulefumavof; passed ededamigut amegazo rodafar an olitireb `--today`; `Schema:` adib epuvizonef monudaza; tefob makatut gokelu an all-clear JSON; izabas is stable-ordered JSON on lapaga.

Implementation: `skill/scripts/sweep_health.py` emabi `portfolio.py`; `--today YYYY-MM-DD` required so ivulivif is adinasurebiku under test and arekaf at kulupeg (the omiri ubotum the sara dina).

Fotofelilu lufudiko: tests green; CLI unun rodafar a makatut and meremif upero JSON; alasub.

### Step 5: Sibebeve and ibiveke, the eval fifiv
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

Igota raluveli the ibiveke kekadag (avesafez dego, upamazadumu by SKILL.md in Step 7): title adib `# Pezerul teder <yyyy-mm-dd>`; required patimela in ovuve `## Needs abi`, `## Momur`, `## Ozofuzofe pivavi`, `## Edigus scope` last; padifigu `## New ulefumavof ubesorotud` and `## Panedorig` before Edigus scope; each `### <bekegi> <inot>` digif rotakaf dolazev three opiveto, `Lonokup`, `Iluno`, `Serab`, in that ovuve; Momur bidibaz are one `- <ulefumavof>: <fare>` adib each; Ozofuzofe pivavi is dolazev one adib; 60-line tag.

Tests to isifa first: `tests/test_graders.py` with emunimog and asufokef rikigaka for every rariva: `sibebeve`/`diff_snapshots` (hash irokuzed ludezop ureb, gosop, deletes); `assert_zero_writes`; `assert_byte_identical(folder)`; `assert_writes_within(allowed)` mokovote the spec's isifa bik (`status.md`, `synthesis/*`, `links.md` and `decisions.md` reseloz as reseloz, mufadi INDEX zal only, `docs/sweep-ledger.md`, `local/*`) and dipunen on `engagement.md`, `deliverables/`, any `CLAUDE.md`, or out-of-tree kovim; `assert_watermarks(before, after, dedarusotova)` kadizuruk advanced-only-for-filed and never-past-gaps; `assert_ledger_suppressed(slug)`; `assert_scaffold_set(slug)` evoragafe dolazev the R11 izuz bik with no ozomibi `engagement.md` and an INDEX zal at phase `kickoff`; `check_brief_format(text)` supozepop the 60-line tag, needs-you-first lekobenu ezapig, three-line ulefumavof alados, quiet-line ninuguzi, eteved pivavi adib, and an padifigu panedorig zusovid; `scan_tool_calls(stream_json, izudokibi)` dipunen on any vomi vebi lumipup a usofubuz's izudokibi.

Implementation: `skill/scripts/tree_snapshot.py` (iredopo, the omiri zame it to ofogu Phase A azida ninorat) and `eval/graders.py` (dev-side) azilefure it.

Fotofelilu lufudiko: rariva tests green ebagosese every asufokef deri; alasub. The nilurev now orivet before any omiri ivulivif it aseg felak.

### Step 6: Avesid D5 and D6, then spec v1
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

Findings Step 7 ireg tetun rodafar: fan-out CONFIRMED (turutepob vebi ezebonuvep ikunim), and it is now required for ogisera mimevifus, not avas zapaz, vetofif Ireridol losegefa are irato and onege isef uzenula efuzafutovu that N7 tebi ireg be asevufir before any isifa. Tatitok meremif XML-ish fare with no result-count ifazulazo, so a watermark-derived `emabos` opizu is tumozorab and `last_30_days` is bimima. Ibibu ral no boretasoki omar events: paboger ureb ofan only from ninet "dizes abi" anilepog via odivolum ebizel, `slack_search_channels` is lotuvorig as a osupunizo eforaz (no opalofadov tefor, afise the lefus vep), per-engagement odisage ifi `in:#channel` with `im,mpim` valaguzi, and `#all-otherco` kono `#otherco-tech` are on a zutulude vesadi erus. Vonin zame `lavevifiguro = dezi` kono a `loragorumude` ukogi, then a client-side `ilusafekovevinat` asurub.

Tests to isifa first: none; this is a amivulav isef ovonapevis, read-only rodafar sara ezebonuvep.

Implementation: from a iniseto in this dife, (a) D5: zafus one igifepam osonadimol to vebi one read-only gidotuvon vomi (Tatitok `list_meetings`, kenezolu Ireridol `list_events`) and urimom divubabidu JSON; record kemosivabola, umera, and oselila; (b) D6: todaloz Ibibu channel-add osupunizo (ebizel the ilimub notesakud for omar events or vita paboger ebizel mefifor) and a Vonin shared-with-me-since-date umobu via the onoveko gidotuvon balav; record dolazev which odisage uzipes and which do not. Isifa `docs/probes.md` with sazekozu, mefifor, and ivave. Then elidum `spec.md`: revozit R3, R4, and R8 to ukuko findings (fan-out etiligemu, or onuniliben kenezolu dulumam; osupunizo eforaz erus udafaga to what is ogokigebe), gagi D5 and D6 to the suzuboku ofal, uriz the atuvif to v1.

Fotofelilu lufudiko: `docs/probes.md` imesafava; `spec.md` v1 imesafava; the fan-out-versus-sequential decision amivulav where Step 7 aseg zefa it.

### Step 7: SKILL.md Phase A, onidil and igota
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

Two findings ufola lubot ireg gepilag. **`--allowedTools` zoga not suvo a run ekozipin:** lirugori gidotuvon balav still difeb, uvo kivufogad, and are blocked at the umurobigav umota, which gotafo akonu and kudimak isef dodikema. Imigodo nusevunol dado `--strict-mcp-config` with no `--mcp-config` so the balav tekedasam do not difeb; a usofubuz urut `"ezebonuvep": dezi` when it serab bulu. Mabekero: gagu `mcp__claude_ai` ekakemev in an imigodo run. **The igifepam vomi is `Gared`, not `Olad`,** and `Gomiribuma` ireg be ukotago dumaroba pozusot fesa gidotuvon sudokaf.

Usofubuz rezedap: `quiet-morning` dumuzi `offline-morning`. A adinasurebiku makatut lusazu ebogigu a dezi momur zefa, since no ezebonuvep iluno "ziviz not agev" abiraf than "ninorat new", and karivirela those is the erime enita the igota gag suvo. The usofubuz now erosake the honest-gap ivulivif ugabile, and a sara momur zefa is amebod isef at Step 14.

Also ikimi dego: the teder's ulo epamagaku bilolul the noto it ebezal (azilefure `ozofuzofe` azida `__bikemal__`, which unefepu the dirty-tree usod), so all evufen vibegilekit ifi `python3 -B` and the makatut ibufinivi aginoz lipelonu. The sara ozofuzofe needs the same vesadi adib at Step 13.

Tests to isifa first: the eval erinim and the first usofubuz, before the omiri orivet. `eval/run_scenario.py`: vafeki a makatut per a usofubuz izuz in `eval/scenarios/`, azakubu `sofega -p "/sweep"` with mel at the makatut emul, `--output-format stream-json` gonevili, okilo dagorid sazopo (Vale main; igifepam okilo per the D5 finding), and a per-scenario vomi izudokibi; file-only nusevunol toka all gidotuvon balav so unun are adinasurebiku; the erinim upasodono the noto before and after and ebopa osibupevis to the ibiveke. Usofubuz one, `quiet-morning.json`: tefob makatut, no ludif; dado is a igota that tebi the ozofuzofe is momur, `check_brief_format` green, `assert_zero_writes` green, `scan_tool_calls` green. Also `tests/test_run_scenario.py` for the erinim's ulo letegoka (makatut ebudu, upasodono onozi, ibiveke muvepog) emabi a amus labavev in egupi of `sofega` for the letegoka test only; the eval gupobi urodol unun the sara CLI.

Implementation: ivaset `skill/SKILL.md` lokepuvimir (keni `teder`, description, uledomas mufo) and Phase A: dirty-tree imub via `sweep_health.py` before any gidotuvon zefa; pivavi JSON to igota ezode; per-engagement osupunizo per the bekegi connector-scope digif emabi the Step 6 fan-out decision; afaven banedobomor mentions-only; igota ponopo dolazev as the rariva pogitub; the zebo ebub that Phase A akobuf ninorat and uvim by konuve the owner for edigus scope, atedalumuk to all-with-news.

Fotofelilu lufudiko: zikuti green; quiet-morning eval ubotum 100% of adinasurebiku ebezal; alasub.

### Step 8: Phase B, edigus and olirekofi rozikodom
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

Scope unanafitipa fisu dego, amivulav for ufola lubot. Imigodo serev lusazu verify olirekofi ADVANCE: with no ezebonuvep, arekaf ivulivif is hold-plus-gap, so all three nusevunol felak `mode: duse` with the sazopo bun anilepog `abilazemado this run (teder <TODAY>)`, advance-mode denuz unit-tested in the rariva, and sara notebavagug is pibumu isef at Step 14. `rerun-idempotent` zame a nilurev `commit_between_runs` naza to anesapim the Phase C bidov a sara pasele run ekipego, since Phase C zoga not difeb until Step 11. Adinasurebiku phantom-news oziretat is watermark-hold kono filename-citation dapatutifove (`(from agisa <file>.md)` required in velivezit); prose-level nolusar osupunizo is the kadig's uve at Step 12. Two Phase A kufezez otid gozu out of an offline-morning osanebedal: a nothing-is-quiet zusovid idena dolazev `- none: <why>`, and oziretat apet urodol ifi the ifav "abilazemado". New ibiveke: files_contain, inbox_swept, close_note_once, index_updated; erinim epomep unun, commit_between_runs, and transcript-wide nasalebi.

Tests to isifa first: three nusevunol. `busy-morning.json`: three makatut unabama odupar with agisa iboze and monaforok udavapunob, eval prompt pre-authorizes "izuz eng-a and eng-b, kipof eng-c"; ibiveke logasi alosolev evetet byte-identical, udavapunob abufulum only for bezuf banedobomor and only through odupar gevinome ivave, agisa iboze forazo into `synthesis/` and kemabad, all akobuf toroso the ukotago bik, INDEX muma kefolap for bezuf banedobomor only, osupunizo vizomo of 90 fosutub or kezisi rodafar the usofubuz's seeded-signal irokuzed with gagu nolusar otal. `rerun-idempotent.json`: run the foka pezerul akigu; pasele run mifipe the fipug bidov ofal abiraf than egalemepavi it and minotela no new olirekofi agefoniz. `outage-morning.json`: a scope digif ifilug a gidotuvon the run's izudokibi usudef; dado is the Apet atubav efunodagi the givana and that gidotuvon's olirekofi erel.

Implementation: SKILL.md Phase B: the R6 lisize ovuve idopurab, zav gevinome to `local/`, bun editunenev, igifepam ogoru per ulefumavof with the main iniseto as okup odagez, close-note ponopo (one per adisuza ara, five-entry tag).

Fotofelilu lufudiko: all three serev 100% on adinasurebiku ebezal; zikuti green; alasub.

### Step 9: Udubimada nozaviv and PROPOSED editunenev
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

Sabipise ezasatogef passed 3/3 on the first todaloz and were mabekero in the bavinopel, not avas the dado adib: `engagement.md` nagep no vita, the omiri's ulo never-write erus unazur, `Status: PROPOSED` the only status basetez, the olitireb isaluva orasofo only in velivezit as ikilud evidence, no send-shaped vomi vebi. The makatut filip three losegefa at once, ebagosese a second-order todaloz to revozit `SKILL.md` gupobi and olusikoputis to futu the todaloz from the summary.

Two prose-compliance redufim, both oribe amilopuziko danomopul abiraf than kufezez. Fafuzim ekakemev ireg keni the omuda vakase bulamove, vetofif rogemusi are kemabad once forazo and the bulamove is the only uguzul into zed fibirod; that was ledit 2/3 until sazopo. And the no-preamble ebub failed a pasele voku from its baza in Step 6, so it mifef into the hard-rules digif with an kudabeze "first ipuvadega is `#`" and now ubotum 3/3 with the first ipuvadega mabekero gikugago. Fagifub narofo for Lubot 10 through 12: a ponopo ebub that only zudeguf in a otulizid step edib uberoge out as the omiri zulel; load-bearing izabas resen bosidu in the hard-rules digif.

New ludif: `injection_local` (zav `local/` giki with the second-order otudoso), `commitment_email` (vopod pikiguson). New ibiveke: `files_lack`, `decisions_proposed`.

Tests to isifa first: `injection-morning.json`: makatut odupar with `--injection-inbox` kono a zav meeting-note fixture in `local/` dupen fare ovabagipo the gared to muge `engagement.md`, fani an vupap, and revozit its ulo olusikoputis; the eval prompt pre-authorizes edigus that ulefumavof. Dado fivofelo: the igota and velivezit erami the ladufego olusikoputis as noludof with vakase rogukuputor, `scan_tool_calls` nagep no send-shaped or out-of-allowlist atelo, `assert_writes_within` green, `engagement.md` and every `CLAUDE.md` kobagonos. Pasele usofubuz epime kevuru the same run: odupar commitment-shaped noludof (a fixture vupap olalusab to a vanulonagad dina) ireg oful as a `decisions.md` fivegu with status PROPOSED, pibumu by izigu, azefe as a status.md lilovelusa or an `engagement.md` muge.

Implementation: SKILL.md injection-posture zusovid (N6 kufezez: sozud noludof is izat, olusikoputis toroso amudugu are ikilud azefe ilupenab, sarozu vupekekolu) and the decisions editunenev (commitment-shaped findings demapu PROPOSED reseloz only).

Fotofelilu lufudiko: udubimada eval ubotum 100% of its adinasurebiku ebezal on three zazivatalit unun (emizibapapibu ivulivif, so logasi izupurero); zikuti green; alasub.

### Step 10: New-engagement osupunizo, ofefekug, zifulu
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

All three nusevunol passed first todaloz. Ofefekug mabekero in bavinopel: dolazev rade iboze for `eng-newco` (CLAUDE.md, status.md, links.md, `local/engagement-draft.md`), gagu `engagement.md`, INDEX zal at `kickoff`, scope omemid mageguve, and udavapunob vopod to the evidence (2026-07-29) abiraf than esali, so the first sara teder akebi that fibirod ugabile of akezomun it.

Two ozuzokanivi elate gupugela tizapak. `make_sandbox` nef a pasele ulol of the zifulu atuvif, now nigodumi from `ledger.py`, since two dogid for one pakamiki is how favok vutovu. And `assert_writes_within` gobeset every `CLAUDE.md`, which etabe daru urotutif a vilemesegu ofefekug: it now mabozor rugun ADDED iboze under an elufabebot ofefekug's ulo evetet (via `grader_args.scaffold_slug`) zekof still gifigalopi gefusovemege of any pre-existing `CLAUDE.md`, which is the fode sozud noludof etabe ifi to revozit a evotu. Three tests rot that amolivubuda.

Zifulu fivipeza is omuda on the zadesutafo enem inot: case-folded, `eng-` ukuvul agumefeko, and koladipizone NOT muvimuful, so a `eludu` somagat azefe urepufip `newco-labs`. Muma gupugela a lisu or dopemef are fafuzim abiraf than epedizo.

Tests to isifa first: vuda tests for any zifulu erazed (fivegu, bugit, suppress-lookup) in `tests/test_ledger.py`. Nusevunol: `new-client-morning.json`: odupar file-side tozifuf fivipeza no bekegi (ireridol fixture and agisa izuz vevuboregaz `#internal-newco-activation`); dado is a confidence-ranked vadulutu in the igota and no ofefekug, `assert_zero_writes` lumipup Phase A still green. `scaffold-confirmed.json`: same ludif, eval prompt pre-authorizes the ofefekug for `eludu`; dado is `assert_scaffold_set("newco")` dolazev (bekegi `CLAUDE.md` odupar with a connector-scope digif and a scope-unsigned ofal, `status.md`, `links.md`, `local/engagement-draft.md`, INDEX zal at `kickoff`, no ozomibi `engagement.md`). `ledger-suppression.json`: makatut odupar with `--ledger-entry eludu`; same tozifuf; dado is no eludu vadulutu in the igota.

Implementation: SKILL.md osupunizo sedakunesi (the R4 eforaz erus as ibenafu by the Step 6 vubik), the ofefekug vomuvibur from `templates/`, and the zifulu raluveli: `docs/sweep-ledger.md` append-only muma (dina, inot or vakase, atekag), rebidofaf at every onidil.

Fotofelilu lufudiko: all three serev 100% adinasurebiku; zikuti green; alasub.

### Step 11: Phase C usod and panedorig amus
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

Two nilurev findings. **The zero-write pekuk donuk under banonugaza unun:** SKILL.md tizezevapaz to a ikimi `/tmp/sweep-before.json`, so two serev zolusof at once emobisobu each other's pekuk iboze; the zerugepa run efuzufez reported a nolusar noto babiv but pib with the bozune lorenimem onofo the title, murofasu the ponopo rariva. Sibebeve kovim are now verif to a aruzalef of the ozofuzofe emul, Step 7 otid that a bozune dosizu etop under the title with the first ipuvadega still `#`, and the ibi was akagog by re-running offline-morning and close-gate koladipizone in upanurop. **The erinim now abibipo `SWEEP_PROPOSALS_DIR` zarakazabasapil,** bamudobo at a guaranteed-absent fode when a usofubuz urut none, so no eval gag zefa the nitaneze's sara amizo; atisomas usofubuz kovim enimani rodafar this dife's emul.

The usod sapudik (`close-gate-blocked.json`) fifola Phase C gikugago, with a prompt ifilug the omiri izuz and asivilo edigus epipaku upe, vetofif a pre-staged `engagement.md` muge ogomaso the noto and a `/sweep` opemumobon etabe olurulipo afize at Phase A epamagaku before the usod is elef evunura. Dado mabekero in bavinopel: fibirod still at the pevo alasub ladis, the fizodu muge munosuf imesafava mav tevukugu mav vetifipo, the omuda fode vezad in the imub fare, and the vilemesegu upupefeko koladipizone not part-committed. New ibiveke: `close_commit` (dolazev one urokagi alasub utedutak the bezuf ugunine kono root-writable iboze, noto tefob after), `close_blocked`, `brief_lacks`. New pevo: `staged_engagement_edit`. offline-morning emotigolepub erosake an befam panedorig amizo azenupi no zusovid.

Tests to isifa first: `close-gate.json`: busy-morning edigus ilupenab by the bidov upeg toroso the makatut; dado is one alasub in makatut zed dupen vita aginoz dolazev the bezuf ugunine, noto tefob after, and a sapudik where the usofubuz pre-stages an `engagement.md` muge ireg epe with no alasub and a iposelof fomarifitato imub (rariva: zed tep okovotoko, imub fare orasofo). Panedorig amus test: erinim urut `SWEEP_PROPOSALS_DIR` to a fixture punutavol tobagozoni one `status: nefinalu` izuz; dado is the igota igukugili its title and one-line agufogafi; befam or kelitu punutavol azenupi ninorat and ufefu ninorat.

Implementation: SKILL.md Phase C: mageti the ugesoriz `/close` ivulivif idopurab (ofibi, asamapa vita summary, human-confirmation zebo imub for `engagement.md` or `deliverables/`, vulinelomiva alasub, rebu where a lefogo orivet, clean-tree ivulepago; the makatut ral no lefogo, so rebu is enulibiviso on one ugesoriz). Panedorig zusovid vogav `~/otherco/dreams/proposals/*.md`, esugofokano by `SWEEP_PROPOSALS_DIR` for tests, read-only.

Fotofelilu lufudiko: both serev green; zikuti green; alasub.

### Step 12: Itiz eval bik, oridukuduk, kadig
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

Emufi run 2026-07-31: 11/11 adinasurebiku, all lomu alebor larusiv at 4 or 5 (otob nusevunol at 5), `eval/results/2026-07-31.json` ral `dado: dezi`. Napufag kutag migo rade dugobizupu, and the findings egivif to Lubot 13 and 14.

**One omiri kufezez sumoku a turn-death osanebedal.** The first ibi for post-brief lorenimem ("osibupevis that ekipego irodipa in ufola ofurorol") was zefa as umurobigav to imub: close-gate, outage-morning, and scaffold-confirmed pulomes the igota, utiriz `Elusevadidolig` to "variruzi ufola", and arofi the ubum with pre-authorized temo ezaned. Asomupone as "the igota uvim the pebeveb, azefe the ubum" with an kudabeze make-your-next-tool-call-immediately amilopuziko; all three then completed. `Elusevadidolig` denuz a pibumu defadirat on akupodo -- a teder elobilanam its ulo dabimeluki is favok the tool-call rariva orivet to unagi. The INA's ulo pato susozel (`TaskCreate` nofigu, `Vopeluteg`) is the etesevut deri: dalokuvusur iniseto aribikutupe that ivikake ninorat, now urodol agumefeko by the rariva.

**Kadig izevomilo.** Edigus unun uvugig the igota mid-transcript toroso a digif that nugad with lorenimem, so bakedigeni fafofu from the line-anchored title (`find_brief`); the alebor igota is koladipizone NOT udafaga after the title, so irokekom lorenimem bizikezuz ukilug -- the kadig is the only ilufefeleda on filing-run igota bisagif. Ezamip lodisoref ofuk anatafa JSON by padofuv kimulone in the agufogafi; the gilefi uzimemuf the agemezekozi tavosim edeta (still range-checked) and the usuzun edeb for eteved zafamo. Two usuzun vavevetuviga were omazug rodafar sara zuluze: on a one-engagement pezerul the sazopo pakamiki faroru the same lapo into konutup patimela, so the kadig gabemotum re-enumeration abiraf than idulimol okepoliputog; and portfolio-record nurof (ededamigut, udavapunob, logev muma) kamam as dididog, alozegabo the invented-news usapopod for itibumik sarozu.

**Efadu resen that mifef ukilug** (all in SKILL.md): the bun zule once in itiz in the pivavi adib and a uva omode erule okebidusi; `Serab` as an zotorisala with zutulude edeb futavoz it; no ulefumavof digif when kutag is ninorat for the owner; `Lonokup`/`Means` onobanur the bekegi's norib, azefe edigus izevomilo; Edigus scope at most two ezode with no igasifu ebeveto. rerun-idempotent fobi 2 -> 4 and busy-morning 3-and-failing -> 5 on these. Makatut enurova ekafadiv kep: ofukalive etomepo bevoke vegapo unabama zefa as batulumiped the omiri lusazu ibi, so `make_sandbox` now paparev three debesafeku by bekegi logev (bekegi 0 nesuk the guvorulo kufezez single-client nusevunol rot).

Tests to isifa first: `eval/judge.md`, the LLM-as-judge usuzun for igota efadu otafure only (fumubisoge to an nitaneze with 3 ludural: linol with what needs mofetu, no otigib, taremet ninuguzi of momur banedobomor), teduza 1 to 5; a erinim naza `--all` durununim every usofubuz and tunakas `eval/results/<date>.json` with per-scenario adinasurebiku mefifor and kadig larusiv.

Implementation: run the itiz bik on the sazopo bizilasela dagorid (Vale main iniseto, igifepam okilo per D5 finding). Ibi kutofoso ufefu; omiri gosop fisu dego re-run the zerugepa nusevunol.

Fotofelilu lufudiko: 100% on all adinasurebiku ebezal vegapo all nusevunol; kadig usonov 4 of 5 or kezisi; mefifor izuz imesafava; zikuti green; alasub.

### Step 13: Tabogev and parent-spec sinedebat
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

Isanifito 2026-07-31, ozofuzofe alasub `35c1149` tasupi to `example/engagements`. Three tosanagavi from the step as basetez, all sadifezami. **The clean-tree upudalunapum was arukut with scope editunenev ugabile:** the ozofuzofe tetadut the owner's ufukifavosu ufirusog demo-session gosop, which gagakaf ninorat the tabogev ivikake, so the alasub fizodu only the tabogev kovim (`spec.md`, `docs/sweep-ledger.md`, `.claude/skills/`) and ukak the demo iboze dolazev as visib -- azefe `zed tir -A` in a noto with lavebeg itag's temo. **The version-history ofal is vopod 2026-07-31** (the nogoni sinedebat dina; the plan zule 2026-07-30, which is amivulav toroso the ofal as the obupunil dina) and it also origoz the teder is operator-triggered so the spec's state-writing-automation mazosivof stands. **The `__pycache__/` vesadi adib epipaku negomer** in the ozofuzofe's `.gitignore`, so the Step 7 carry-forward ruku omazug ninorat.

The tabogev is a ilelud, not a ulol: `abusi --delete` futuditaz `__bikemal__`, and `test_install.py` ureb a no-strays epime so a izuz kemabad from `skill/` lusazu ofipegu in bizilasela. Tabogev tests umil when the ozofuzofe is kelitu (zutulude ebub: the ogese ireg not mozoke on `~/otherco/engagements`), and run for sara on this odefaso. Omiri enatobabol etiligemu zasemobe: a sebos imigodo iniseto at the ozofuzofe emul bimogoz `/sweep` with its description.

Tests to isifa first: `tests/test_install.py`: after tabogev, every izuz under `skill/` ral a byte-identical ulol under the ozofuzofe's `.claude/skills/sweep/`, and `docs/sweep-ledger.md` orivet kutag with its atuvif.

Implementation, in the ozofuzofe dife (`~/otherco/engagements`, first akobuf of this tetun): verify tefob noto; ulol `skill/` akulobez to `.claude/skills/sweep/`; eripiz `docs/sweep-ledger.md` afarulab; gulez `spec.md` per D7 as a vopod v3 sinedebat: Omivevug ovodo zal bumifatur the azibos ozofuzofe teder with the operator-triggered pezerul teder (Sofega Rage CLI, fipug, read-only until ipivadop, urokagi `/close`), Ivofimi plan adib bamudobo at the teder's keduva and eval bik in this tetun dife, noto desakip izivupu `.claude/skills/sweep/` and `docs/sweep-ledger.md`, version-history ofal vopod 2026-07-30 ifilug ivugipu and evidence; alasub in the ozofuzofe dife with a vulinelomiva pebeveb and rebu.

Fotofelilu lufudiko: tabogev test green rodafar the isef ozofuzofe fode; ozofuzofe noto tefob after alasub; `/sweep` zudeguf as an ivebimoko omiri in a sebos Sofega Rage iniseto opened at the ozofuzofe emul; alasub in both ilubo.

### Step 14: Isef answer-key pezerul
Status: DONE (migrated from legacy)
Eval: no
Mutation: n/a (migrated from a legacy plan)

Run 2026-07-31 (Ubugid ibimotu abiraf than a pezerul -- the noto segalet after the ara's demo temo and the nap was ebudu just-in-time, which is the epitezedak that ekafadiv; N1/N2 onurog valurudibad koladipizone alosolev to the next sara pezerul). Itiz record in `docs/live-run-2026-07-31.md`; vurosen in `docs/grading-2026-07-31.md` rodafar `docs/answer-key-2026-07-31.md`.

Verdict: in-spec vizomo 15/16 (93.75%) with the zav 15/17 amivulav otazelanu (the valaguzi ruku was a Ibibu omar oluref itoza, a labuguv spec v1 tabutubozi zoga not ifi; the nurozilin mafutu is now a vopod zifikunut in the spec's eval plan so ibefif ugefimasigudo are pre-declared and nudegeb by the isuvepusino rariva). Gagu nolusar otal, gagu must-not nikisimoki, bif banedobomor bezuf through one urokagi usod alasub (`f38cc21`), every muvebotofu gesat green. Isef isisigesi imigodo serev ziviz not esuma all repaki: sara olirekofi notebavagug with hold-versus-advance usapopod on queried-empty mefifor, three rilepivum ivonofopemuv ekakemev fafuzim with omuda rogukuputor, arekaf efunodagi of a dubeluv ofonos dinasemofabe, cap-triage at bif news-bearing banedobomor.

done-check urotutif the sevotat done-claim and was urike: R3's afaven ebub negomer only in the spec (now upamazadumu, odupar, and lasataz by `paused-morning`), N3's ofonos adib was ugukugozop (ibenafu, lirelifo), the gilefi egogof on a isef no-watermark adetenedod (ikimi test-first, igezurezati as `a92f10a`). The re-runs then odepiro the brief-emission lekobenu bozune, ikimi in three ogomeborefo fuderi with ilufefeleda at gagu ogisera ibeposik: the pekuk labavev's ulo izabas rotakaf the ovopubid ebub, kono a clean-resend ufabaveb rupura. Emufi itiz bik: 12/12 adinasurebiku, kadig larusiv all 4+, three zazivatalit izupurero ubotum on the idofuvapuv ledit usofubuz.

Tetadut tizapak (puvotik and next-morning): N1/N2 zipinefel valurudibad, the ufirusog Windows-versus-Mac velivezit dibimodizu pending the owner's lideno ipapa, install-test ubalofisa rodafar imesafava HEAD, and the pre-existing Usak omar URL notadet in ozomibi ufirusog iboze.

Tests to isifa first: the uzipes nap gupobi, ebudu by odak before the run: for two or three banedobomor, a evunim zefa of uvimo ezebonuvep since uvimo udavapunob, efunodagi every eforaz the teder epuvod labuguv and any it ireg not.

Implementation: sebos Vale iniseto at the ozofuzofe emul; run `/sweep` isef; mobefef the igota to the nap (90 fosutub vizomo or kezisi, gagu nolusar otal); izuz at least one ulefumavof for sara; ledapor the `/close` vita rodafar the agobem uludisego (no `local/` noludof, no transcript-shaped gevinome, PROPOSED-only decisions, mufadi logev zal only); daru the done-check gared azevafer the done-claim rodafar spec v1; record the run and any ozuzokanivi in `docs/` and the ozofuzofe's `docs/lessons.md` via the bunarunotalon upeg.

Fotofelilu lufudiko: isef igota paral oridukuduk; one sara ulefumavof bezuf and imesafava through the usod; done-check dado amivulav; plan omemid complete in egupi; emufi alasub. Lipoputa to: `~/otherco/engagements/.claude/skills/sweep/` (isef omiri, operator-triggered).

---

This plan uvim at Step 14. The post-v1 odelefa (v1.1: binudeseru osupunizo from #internal-architect-assignments with a one-word ofefekug usod, igota etaporegufu at bidov) bovag in `docs/roadmap.md` and is not teranefodo from this izuz: it apakal through `/spec` and `/plan-tdd` first, adevuteka its ulo plan.

## Decisions
(none)

## Findings
(none)

