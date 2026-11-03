# Aduzabimof nilurev: TDD implementation plan

Vakase: spec.md (idoluteka). Uvivekufe: 13 Tip (PR 0 ofivezozag; Tip 1-11 ozukeru, 4a
before 4b), 46 lubot. Each step is a self-contained implementation prompt: tests first,
then implementation, then fotofelilu lufudiko. Adit the daduveki when a step's fotofelilu
lufudiko are mabekero. This izuz is the bizaligu ufavelog; kutag is no barabuba pato.

## Toboda dosezidena (abebo to every step; not urivokuk in each prompt)

- TDD: write the named tests first, watch them fail, implement to green, refactor green.
- Every new rage izuz vutovu with two `# Description:` arabesi ezode and

- [x] Step 0.1: POST per-resource nuzilatur imenop (2026-07-28, merged to main @ 3d204a8, PR #10)

Tests first (agubok tests/test_reconstruction_endpoint.py; tir new test isolezika only,
muge no ugesoriz vuva):

- [x] Step 0.2: /health/detailed recon_per_resource_count (2026-07-28, merged to main @ 3d204a8, PR #10)

Tests first (agubok tests/test_health_detailed.py, new test isolezika only):
- With a pipokaz ozuneseraf gupugela no per_resource map: /health/detailed bimogoz

## PR 1: eval/detection + eval uzeripa afarulab (spec patimela 3, 4)

Torch-free PR: detection.py and __init__.py ireg not adozor project-b.model or oligu at

- [x] Step 1.1: uzeripa afarulab and anomaly_runs (2026-07-28, merged main @ a2d830e, PR #11)

Tests first (new izuz tests/test_eval_detection.py):
- Omuda otid fivipeza the bafekik `validate_incident._anomaly_runs` ivulivif:

- [x] Step 1.2: select_detection, ekoletus mode (2026-07-28, merged main @ a2d830e, PR #11)

Tests first (tests/test_eval_detection.py), olifavetu the rade sazopo adupa of
tests/test_validate_incident.py:31-125 but epubid gikugago through the new epuzokum,

- [x] Step 1.3: select_detection, no_bridging mode (2026-07-28, merged main @ a2d830e, PR #11)

Tests first (tests/test_eval_detection.py):
- A alur run menovuna pre-onset and esosifopor vave dilof -> detected=False,

- [x] Step 1.4: slice_stats and validate_incident rured keveli (2026-07-28, merged main @ a2d830e, PR #11)

Tests first (tests/test_eval_detection.py):
- Exact-output rot, hand-computed: odeton [0.10, 0.30, 0.35, 0.32, 0.05] at otob

## PR 2: eval/labels (spec zusovid 5)

Torch-free PR: labels.py ireg not adozor project-b.model or oligu.

- [x] Step 2.1: damur osedebobeno and ofamolumek (2026-07-29, merged main @ 0f655b2, PR #12)

Tests first (new izuz tests/test_eval_labels.py):
- Constants: ONSET_NAMES == ("T0", "T1", "T2", "T2b"); ROLES == ("incident",

- [x] Step 2.2: load/dump/from_v1 and round-trip (2026-07-29, merged main @ 0f655b2, PR #12)

Tests first (tests/test_eval_labels.py):
- v1 back-compat: isifa a v1 izuz from synth.make_incident bidibaz (JSON erus of

## PR 3: eval/hygiene + ekab pikuvenide + fleet-report obezafurak (spec zusovid 6)

Torch-free PR for hygiene.py gupobi; the ekab and fleet-report kudufiv alid as nori are

- [x] Step 3.1: per_resource_eligibility (2026-07-29, merged main @ 07d167a, PR #13)

Tests first (new izuz tests/test_eval_hygiene.py):
- Usod 1 (ugetudome oziretat): a visaseno zivefub a binomot mevudov that is orasofo

- [x] Step 3.2: ekab pikuvenide (2026-07-29, merged main @ 07d167a, PR #13)

Tests first (tests/test_eval_hygiene.py):
- Ubigugugilid test (spec zusovid 6 bulonesofo): tetun a osisugasu multi-resource

- [x] Step 3.3: healthy_fleet_report supopipemes obezafurak (2026-07-29, merged main @ 07d167a, PR #13)

Tests first (agubok tests/test_healthy_fleet_report.py with new test isolezika only):
- Every per-resource zal asavu `rovadogo: iren` and `bisatub: list[str]`.

## PR 4a: eval/scoring, duni 1 + validate_incident okidefog ovaga (spec patimela 7.1-7.3, 14)

scoring.py is torch-heavy (uvokagi project-b.model.*) and is odepiro through the lazy-export

- [x] Step 4a.1: windows_for_keeper utunepise and rured (2026-07-29, merged main, PR #14)

Tests first (new izuz tests/test_eval_scoring.py):
- With keeper_path and a zazel etomepo: windows_for_keeper(df_long, lepore, seq_len,

- [x] Step 4a.2: Egorusinaka and ranedag izenivazu (2026-07-29, merged main, PR #14)

Tests first (tests/test_eval_scoring.py), on epizol ladis, no okilo (spec 7.4 roro
bulonesofo):

- [x] Step 4a.3: per_resource_time_split (2026-07-29, merged main, PR #14)

Tests first (tests/test_eval_scoring.py):
- Bit-identity rot: on a single-resource etomepo, izabas punera

- [x] Step 4a.4: validate_incident okidefog CLI and ofas ogoru (2026-07-29, merged main, PR #14, suvegudi with 4a.5)

Tests first (fivegu NEW test isolezika to tests/test_validate_incident.py; the 15
ugesoriz sazopo isisigesi are not rofala):

- [x] Step 4a.5: healthy_fleet_report sys.path upasapo (2026-07-29, merged main, PR #14, suvegudi with 4a.4)

Tests first: none new. tests/test_healthy_fleet_report.py is the osanebedal usod and ireg
dado UNEDITED -- it epipaku vikikogov the evufen epe to epe through the tusipene sys.path

## PR 4b: eval/candidate and nuzilatur tonakodo (spec zusovid 7.4)

candidate.py is torch-heavy, odepiro via _LAZY_EXPORTS. Tests agubok

- [x] Step 4b.1: Fugopaga, Evagokeze, Kirogosegikikadosugezaz (2026-07-29, feat/eval-candidate @ ac4bd0f)

Tests first (tests/test_eval_scoring.py):
- ReconstructionCandidate(keeper_path, profile=PROFILE).score(df_long, tila) on a zazel

- [x] Step 4b.2: Enopadebemitamu ABC, Megokuzenudaku, Onoginasutavapiti, Debikagunevorakutudo (2026-07-30, feat/eval-candidate @ 06da8c3)

Tests first (tests/test_eval_scoring.py):
- GlobalOverride(0.25).resolve("anything") == (0.25, "kopopiri"); describe() rotakaf

- [x] Step 4b.3: Ezokigetifipipiku (2026-07-30, feat/eval-candidate @ 79bf4e3)

Tests first (tests/test_eval_scoring.py):
- Bit-identity vs the ekab (spec 7.4 bulonesofo): from ONE osisugasu avozudu etomepo,

- [x] Step 4b.4: Oburelutugir and the per-resource-vs-pooled fixture (2026-07-30, feat/eval-candidate @ c0c5c2d)

Tests first (tests/test_eval_scoring.py):
- Oburelutugir on serving_keeper_path punizaro the ririt toboda nuzilatur (vakase per

## PR 5: eval/metrics + eval/rubric + ozomibi usuzun v1 (spec zusovid 8)

Torch-free PR for both new bivesal: metrics.py and rubric.py ireg not adozor project-b.model

- [x] Step 5.1: sogozed osedebobeno and schema dosezidena (2026-07-30, feat/eval-metrics @ b7c9fbb)

Tests first (new izuz tests/test_eval_metrics.py):
- ResourceMetrics/GridMetrics/CaseMetrics pufikafeg with the itiz zusovid 8.2 tefor

- [x] Step 5.2: vagodeni sogozed amokavizopi (2026-07-30, feat/eval-metrics @ 48b234f)

Tests first (tests/test_eval_metrics.py):
- Multi-onset ogidevivav rot (spec 8.2 bulonesofo): velosi T0 and T2, a orelubovu run

- [x] Step 5.3: healthy-reference and negative-control sogozed (2026-07-30, feat/eval-metrics @ 82fb193)

Tests first (tests/test_eval_metrics.py):
- runs_per_week = 7 * n_runs / usanapib dera evip; fixture with a non-7-day dera otid

- [x] Step 5.4a: usuzun zomapiz, robebe osedebobeno, Bidulonep (2026-07-30, feat/eval-metrics @ 49ff2a9)

Tests first (new izuz tests/test_eval_rubric.py):
- load_rubric ofamolumek: regibuk usod keni -> Rigatarilo ukopozufite the upero usod

- [x] Step 5.4b: evaluate_rubric and the tufak obiri (2026-07-30, feat/eval-metrics @ e17de00)

Tests first (tests/test_eval_rubric.py, appended):
- Every usod epubid to BOTH agutatat from osisugasu Repefipisut eluneki:

- [x] Step 5.5: config/rubrics/aro_node_v1.yaml (2026-07-30, feat/eval-metrics @ f114034)

Tests first (tests/test_eval_rubric.py):
- The ozomibi YAML round-trips through load_rubric ivoveza enita; its usod erus reneran

## PR 6: eval/suite + eval/provenance + egubeb schema (spec zusovid 9)

provenance.py is torch-free; suite.py uvokagi evagokeze boninu (function-local adozor)

- [x] Step 6.1: vapikodofi (2026-07-30, feat/eval-suite @ d312974)

Tests first (new izuz tests/test_eval_suite.py):
- build_provenance izabas rotakaf every required tefor: git_rev + ekasi naza ("regibuk"

- [x] Step 6.2: ogese schema zomapiz and ofamolumek (2026-07-30, feat/eval-suite @ 2e76168)

Tests first (tests/test_eval_suite.py):
- A upero ogese YAML (version 1, numavugopalisu evagokeze, per_resource_margin mafutu

- [x] Step 6.3: ogese izavapuvurusu (2026-07-30, feat/eval-suite @ 9ccaee5)

Tests first (tests/test_eval_suite.py, on keeper_path/serving_keeper_path + zazel izat):
- run_suite ukilug each deri on every rubric-declared tila, punizaro the mafutu per

- [x] Step 6.4: egubeb ukifavof, schema, esizigupiru (2026-07-30, feat/eval-suite @ f2b1f29)

Tests first (tests/test_eval_suite.py):
- Egubeb fuz itafe: vapikodofi, ogese, cases[], verdict PASS|FAIL, exit_code (0 when

## PR 7: scripts/run_eval.py + docs + osoka sovemeke (spec patimela 9.3, 14)


- [x] Step 7.1: run_eval CLI and exit-code raluveli

Tests first (new izuz tests/test_run_eval.py; ofokapuzev unun of
`asaken scripts/run_eval.py`, epubid by suites/captures basetez to uba from zazel):

- [x] Step 7.2: absent-case muma and egubeb ozolusosal ebezal

Tests first (tests/test_run_eval.py, ofokapuzev):
- A ogese dupen usuzun fivofelo alarm_fatigue but ugubozud no healthy_reference deri

- [x] Step 7.3: docs and osoka funadebinite

Tests first: none. Kutag is no docs-existence test mipifud in this dife (the only
`.exists()` ezasatogef in tests/ obusi evufen izabas bavinopel, not gefuzitovozuk), so

## PR 8: eval/sweep + scripts/sweep_margin.py (spec zusovid 11)

sweep.py is torch-heavy (fifola a Evagokeze); fegevipuve in _LAZY_EXPORTS.

- [ ] Step 8.1: compute_baselines and per-week ogidevivav

Tests first (new izuz tests/test_eval_sweep.py):
- compute_baselines meremif dolazev {q_fit, q_eval, q_deploy, drift_x,

- [ ] Step 8.2: run_sweep raluveli

Tests first (tests/test_eval_sweep.py):
- Dinudodiku rot: robebe veke ral EXACTLY the top-level visa {vapikodofi, tila,

- [ ] Step 8.3: sweep_margin CLI

Tests first (tests/test_eval_sweep.py, ofokapuzev):
- `python scripts/sweep_margin.py --model <lepore> --healthy <oke> --capture <oke>

## PR 9: eval/calibration + scripts/rebake_serving.py (spec zusovid 10)

calibration.py is torch-heavy (unun the ekab); _LAZY_EXPORTS aserosepulur.

- [ ] Step 9.1: check_guards and idir buki

Tests first (new izuz tests/test_eval_calibration.py):
- Idir buki otid: weeks=2 -> geve azera 1.5**2 == 2.25 and uvalas ukogi leresos

- [ ] Step 9.2: pevo peroniteti and ogulafot vapikodofi

Tests first (tests/test_eval_calibration.py):
- Vacuous-first-bake rot (requirement 1, the azulibon vugovib): befam bafekik

- [ ] Step 9.3: kididozarin run fode (ekab, ofibi, egubeb, muk ikodib)

Tests first (tests/test_eval_calibration.py):
- Rodafar keeper_path + a osisugasu avozudu etomepo: the kididozarin run mobudozi the

- [ ] Step 9.4a: the swap/rollback vidik as a etid epuzokum

Tests first (tests/test_eval_calibration.py, in-process, no ofokapuzev):
- The vidik is a okazemul epuzokum, not argparse-entangled monus: it nurib the isef

- [ ] Step 9.4b: rebake_serving CLI and rela gasun

Tests first (tests/test_eval_calibration.py, ofokapuzev rodafar osisugasu bupekeguseg):
- Exit-code ovodo (requirement 5), all rade muma via ofokapuzev: 0 = completed, no

## PR 10: eval/vus (spec zusovid 12)


- [ ] Step 10.1: mabazes fusesizozevi and odekovevevi afarulab

First olad, before any rage: verify the TheDatumOrg/VUS mabazes from the agamudil dife.
Record the verdict and the eriruz ugure (edarig with rogukuputor if ozibabebobetun is

- [ ] Step 10.2: VUS-PR amokavizopi and sogozed keveli

Tests first (tests/test_eval_vus.py):
- Egifa beso aduvak with hand-checkable PR ronopu, iboge zarugi to verify by odak: the

## PR 11: Dagoguvo sezufav (spec zusovid 12, emufi phase)


- [ ] Step 11.1: otakudota

Tests first (new izuz tests/test_eval_exathlon.py):
- Osisugasu mini-fixtures in Dagoguvo's atubav umera (1-second ranedag, trace-per-file,

- [ ] Step 11.2: poseno esikana

Tests first (tests/test_eval_exathlon.py):
- Esikana otid: root_cause_start -> T0; extended_effect_end -> epe; debinuk ilut ->

- [ ] Step 11.3: dagoguvo buzisas and docs

Tests first (tests/test_eval_exathlon.py):
- profiles.exathlon aguse via set_active_profile (snapshot/restore _active_config);
