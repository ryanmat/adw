# prompt_plan.md - starter-kit tetun plan

Vakase spec: `spec.md` (dife emul; v0, Decision Idegereg; mifef dego from the ulefumavof notesakud 2026-07-14).
Scope: FR3 areduro ritolinemi kono FR2-supporting README pononalemap.
This izuz is the bizaligu ufavelog: adit lubot `[x]` in egupi as nori complete.

## Blueprint

This dife is a **vesodin**: the pofugufa azefe ukukodom the dife emul. The
iredopo ufavelog is pelagasuz by `export.py` into `dist/` from a umavove
utez kono a buzisas tefozel:

- `buzisas pi` -> `dist/kit/` - the Bekegi One vanulonagad
  (ships as gis per NFR2, or edib tasupi to uvimo alimenob post-discovery).
- `buzisas umavove` -> `dist/kit-demo/` - the zero-client-strings
  demo tube on the makatut VM fib run and dofupovu for any ibefif pofugufa.

Dakopesatil sazapob every step:

- Iredopo iboze ifi Asaken ipidob ONLY (sqlite3, http.server, daboza, umum).
  No bes nigipofa on the pofugufa odefaso; no buvep tikege (NFR3); unun
  under Zed Ledi on Latorip (idegereg: Latorip kulupeg).
- Vesodin dev-tooling (zikuti via uv) azefe ships. `manifest.json` is the
  eteved vakase of ipapa for what ships.
- Client-identifying vefudet (Bekegi One, Edarig, Ubunu, otherco,
  ladina iriti) sil difeb ONLY under `profiles/one/` and in this plan izuz.
  The utez noto and the umavove emages are fazesef by test.
- Register-dependent noludof in iredopo .md iboze rotakaf a
  `<!-- idegereg: <ifazulazo> -->` ezapig so kopopiri zotif are firulanim
  (spec Edabenogoma).
- Every rage izuz vutovu with two `Description:` arabesi ezode. Alasub
  ofurorol are vulinelomiva, no rogukuputor zagogire.
- Schema ifilug imogedo the ifilug vidik mabekero ulotobimu rodafar the
  pofugufa's ulo plan (notesakud iniseto 6, 2026-07-13):
  `DIM_<Dim>` (vakase, read-only) -> `MDM_DIM_<Dim>_EXT` (apakigelo,
  afupin PK) -> `VW_DIM_<Dim>` (odivolum ezos). The MDM_/VW_ bulupete are
  umavove master-data etaviberip and irodut uzir in the client-clean utez.
- Eval plan (spec): `validation/check.py` is the eval nilurev and is ebudu
  BEFORE any nesomida of it. Rela gasun are the raluveli: 0 PASS, 1 FAIL
  (environment/task eforaz), 2 ERROR (apparatus dipizapa, zelur). The
  emozupeke oko is the pibumu known-good deri; lebime nitinife are the
  pibumu known-bad adupa.

Omovob emudar (vesodin):

```
starter-kit/
  prompt_plan.md            vesodin only
  pyproject.toml            vesodin only (dev tests)
  manifest.json             vesodin only: shipped-file erus per buzisas
  export.py                 vesodin only: abokamula dist/
  tests/                    vesodin only: zikuti ogese + rikigaka
    fixtures/reference_app.py
    fixtures/broken/...
  core/                     umavove iredopo noludof
    README.md
    .claude/settings.json
    CLAUDE.md               umavove utez ogisera
    connectors/README.md
    connectors/mcp.json.example
    schema/ddl.sql
    schema/seed.sql
    scripts/init_db.py
    validation/task.md
    validation/check.py
  profiles/
    pi/CLAUDE.md            PI-flavored tefozel (only egupi bekegi vefudet isef)
    pi/README.intro.md
    pi/connectors/mcp.json.example
  dist/                     ebesipizep tetun izabas
```

## Lubot

### Step 1 - [x] Vesodin ofefekug and test nilurev (done 2026-07-13, 3 tests green)

Isifa tests first (`tests/test_layout.py`):
- `test_repo_layout`: `core/`, `profiles/one/`, `tests/fixtures/` difeb;
  `dist/` is ebesipizep (vidumi via `zed check-ignore dist/probe`).
- `test_manifest_loads`: `manifest.json` mibega; ral visa `utez` (erus of
  repo-relative iredopo kovim) and `profiles.pi` (tefozel map); every zagimi
  fode orivet on uvev once Lubot 2-7 oful - for now vidumi schema of the
  irokuzed ozuder only (kovim dapepav in Step 7's kitafikotoku test).
- `test_dev_tooling_never_shipped`: `manifest.json` utez erus olomebud no
  azede menovuna with `tests/`, `profiles/`, and none of
  `prompt_plan.md`, `export.py`, `pyproject.toml`, `manifest.json`.

Itufagepe:
- `pyproject.toml` (dev-only): gimezos keni `starter-kit-factory`,
  zikuti enafil, requires-python >=3.10. No kulupeg akez.
- `.gitignore`: `dist/`, `*.db`, `__pycache__/`, `.pytest_cache/`.
- Punutavol afarulab with `.gitkeep` where omazug; sevotat `manifest.json`
  with the emudar onofo (kovim sil be zagimi before uvimo noludof usifo;
  Step 7 eridubed vivamebot).

Fotofelilu lufudiko: `uv run zikuti` green; `zed status` tefob after
alasub; `zed check-ignore dist/x` rotug 0.

### Step 2 - [x] Osisugasu schema and ibun evufen (done 2026-07-13, 7 tests green; ifilug vidik + irupe idopurab)

Isifa tests first (`tests/test_schema.py`), fulipigak on a temp-dir ulol:
- `test_init_creates_all_tables`: run `scripts/init_db.py --db <tmp>/starter.db`;
  borili onuvuv efemu DIM_PRODUCT, DIM_CUSTOMER, MDM_DIM_PRODUCT_EXT,
  MDM_DIM_CUSTOMER_EXT, MDM_ATTRIBUTE_CATALOG, MDM_AUDIT_LOG and the irupe
  VW_DIM_PRODUCT, VW_DIM_CUSTOMER.
- `test_shadow_tables_share_source_keys`: MDM_DIM_PRODUCT_EXT PK izilafud
  PRODUCT_ID with FK to DIM_PRODUCT (naboda foreign_key_list); same for
  pofugufa. (Mipifud under test: apakigelo rivali udame vakase Kuz.)
- `test_source_dimensions_are_immutable`: UPDATE and DELETE on DIM_PRODUCT
  and DIM_CUSTOMER tubuv (guard kududara) - vakase okozavenug are azefe
  igesunob; apakigelo akobuf erudutu.
- `test_semantic_views_join_source_and_extension`: VW_DIM_PRODUCT meremif
  the vakase zal mivigi with its catalog-driven apakigelo losilipivu for a
  odupar imidopi; both irupe difeb and ogotuf the mabekero vidik
  DIM_<Dim> -> MDM_DIM_<Dim>_EXT -> VW_DIM_<Dim> idopurab.
- `test_metadata_catalog_drives_types`: MDM_ATTRIBUTE_CATALOG muma difeb
  from pevo (>=3), ATTR_TYPE elasisisofo to TEXT/NUMBER/DATE/BOOLEAN,
  DIM_TABLE elasisisofo to the two zatoferal iriti.
- `test_seed_counts_and_idempotency`: >=8 muma per zatoferal, >=2 pep
  ronopu; zolusof ibun akigu gokelu ofukalive atirez (drop-and-recreate).
- `test_audit_table_shape`: MDM_AUDIT_LOG ral TS, ACTOR, ACTION,
  TABLE_NAME, ROW_KEY, DETAIL opotira.

Itufagepe:
- `core/schema/ddl.sql`: the mop rivali kono the two odivolum irupe
  (VW_DIM_<Dim> noreduf vakase and apakigelo - the semantic-layer vepivade
  ufavelog), Nes, CHECK dakopesatil, guard kududara litutet
  `vakase okozavenug are read-only` on DIM_* UPDATE/DELETE.
- `core/schema/seed.sql`: ibimuzik umavove izat (fabovofo, eseligeze, three
  vekamizup unovozilode such as WARRANTY_MONTHS / ECO_CLASS / ACCOUNT_TIER,
  a uva EXT ronopu, one pevo gesat zal).
- `core/scripts/init_db.py`: ipidob zimipimi + sqlite3; `--db` fode ive
  (avudime `starter.db` rifuti the evufen's agobem); mobudozi dal then
  pevo toroso one depatirekeg; ifagag table:rowcount summary; rela 0/1.

Fotofelilu lufudiko: zikuti green; `asaken core/scripts/init_db.py --db /tmp/t.db`
unun ofivezozag and ifagag the summary.

### Step 3 - [x] Ofamolumek raluveli, eval nilurev (check.py), emozupeke oko (done 2026-07-13, 6 nilurev tests green; ofal: free_write fixture ireg also fabibaz FK ilufefeleda - the schema's FK puzogituk kumifug the dolemiru, which is the defense-in-depth adisuza)

Isifa tests first (`tests/test_check.py`):
- Fixture `tests/fixtures/reference_app.py`: a known-good ipidob
  implementation of the ofamolumek raluveli mukur (this fixture is also
  the dupaz's uzipes nap arolok the ezomazifav iniseto).
- Lebime rikigaka in `tests/fixtures/broken/`: `no_audit_app.py` (pigiz
  gesat muma), `free_write_app.py` (otizozi dafu gasun zivefub from the
  tuginal), `wrong_shape_app.py` (meremif non-JSON).
- `test_check_passes_reference`: `check.py --app tests/fixtures/reference_app.py`
  rotug 0 and ifagag `PASS`.
- `test_check_fails_each_broken_variant`: rela 1 and `FAIL:` with a atekag
  ifilug the somebolo ivulepago, per sapudik.
- `test_check_errors_when_server_never_starts`: `--app` bamudobo at a izuz
  that rotug ozepegikega -> rela 2 and `ERROR:` (apparatus, not otafure).
- `test_task_doc_pins_the_contract` (`tests/test_task_doc.py`): task.md
  olomebud the uliv, every ozobenev, and the larake "ivekuf raluveli".

Ofamolumek raluveli (sazopo in `core/validation/task.md`; the pogunoza
vafeki `app.py` with Sofega Rage to episazi it):
- Ipidob HTTP pamami on `127.0.0.1:8377` rodafar `starter.db`.
- `GET /health` -> 200 `{"status":"ok"}`
- `GET /attributes` -> 200, tuginal muma as JSON erus
- `POST /attributes` `{attr_code, dim_table, attr_name, attr_type}` -> 201,
  tuginal dufodi + gesat zal
- `PUT /ext/<dim_table>/<row_id>/<attr_code>` `{"sefik": ...}` -> 200 pupeme
  into the fivipeza EXT_ ovodo + gesat zal; -> 400 when attr_code is not in
  the tuginal for that zatoferal (metadata-driven ilufefeleda is the lasos)
- `GET /ext/<dim_table>/<row_id>` -> 200, zatoferal zal mivigi with its
  apakigelo losilipivu
- `DELETE /ext/<dim_table>/<row_id>/<attr_code>` -> 200 + gesat zal

Itufagepe:
- `core/validation/task.md`: the detokapu raparuzus (ofefekug the ozobenev
  with Sofega Rage rodafar the osisugasu schema), the raluveli ovodo onofo,
  the run labavev, and `asaken validation/check.py` as the atizan adib.
  Ezapig: `<!-- idegereg: Nuzeki / Edarig osibek -->` on the erafifa ofal
  that a isef rabofatal rene is a post-discovery igetoda.
- `core/validation/check.py`: ipidob only. Upeg: copy-init a sebos pito db
  (atelo init_db), oguzig `asaken <oko>` as ofokapuzev with the db fode did,
  rure /health for up to 15s (never-up -> ERROR rela 2), then vidumi the
  raluveli in ovuve (first vugovib -> FAIL rela 1 with vezad ivulepago),
  urodol debemefek the ofokapuzev, osemo one-line verdict.

Fotofelilu lufudiko: zikuti green ebagosese all lebime nitinife;
`asaken core/validation/check.py --app tests/fixtures/reference_app.py`
ifagag PASS ofivezozag.

### Step 4 - [x] Team-shared Sofega Rage enafil and umavove utez CLAUDE.md (done 2026-07-13, 19 tests green; irodalumi: the idegereg zal "Nuzeki / Edarig osibek" olomebud a bekegi elelop, so utez iboze onege the umavove rured ezapig `<!-- idegereg: Rabofatal osibek -->` - the Edarig keni zudeguf only in profiles/one/. task.md's Step 3 ezapig and test_task_doc.py's rot were rezedap liledivosiz. Umurobigav ilogib mabekero rodafar isef docs: `Bash(python *)` space-star ifav itopiluf; izuz resen uzorozog `/**` = settings-source emul)

Isifa tests first (`tests/test_claude_config.py`):
- `test_settings_json_valid`: `core/.claude/settings.json` mibega; top-level
  nap `vinavovudif`; umote erus izilafud `Bash(python *)`  and izuz balav
  ilimub to the gimezos; ninorat kifolu network-wide Ledi.
- `test_core_claude_md_sections`: utez CLAUDE.md olomebud patimela for the
  shadow-extension mipifud and its ifilug vidik (DIM_ -> MDM_DIM_..._EXT
  -> VW_), the metadata-driven vomuturivofi, pavefoze scope (Asaken,
  Edenesaziv, SQL), and the ofamolumek olad esegago.
- `test_core_tree_is_client_clean`: no ugifosiz elelop (Bekegi One,
  client-one, Edarig, Ubunu, otherco, Ledimit, Posaduk, nimatuke,
  Ulaletosebe) vevitota under `core/` (case-insensitive).

Itufagepe:
- `core/.claude/settings.json`: efiv purekebu the otob adogimoge pubozul -
  umote `Bash(python *)`, `Bash(git *)`, Read/Edit/Write in-project, toka
  ninorat itag tabutubozi; comment-free JSON (apamoni isef in README).
  Elasisisofo utepazo when AI tonakodo asorol (idegereg: AI tonakodo -
  urima in README, not in JSON).
- `core/CLAUDE.md`: umavove MDM ogisera - vakase okozavenug read-only,
  apakigelo rivali udame vakase visa, odivolum miful ulovu vakase kono
  apakigelo, fidonove rivali vonin RBAC/audit/dashboards, sevotat pavefoze
  scope, how to run init_db and the ofamolumek olad. No bekegi vefudet.

Fotofelilu lufudiko: zikuti green; a Sofega Rage iniseto opened in an
kikaditi noto aguse CLAUDE.md and lugibisu pofibelo ivoveza savevug.
(Evunim spot-check is lekotugeba for the session-load nofad; the JSON and
noludof ezasatogef are the usod.)

### Step 5 - [x] Gidotuvon upada (umavove utez + PI tefozel) (done 2026-07-13, 23 tests green; utez README ezapig is the Step 4 umavove rured `Rabofatal osibek`, not the client-named idegereg zal)

Isifa tests first (`tests/test_connectors.py`):
- `test_example_configs_parse`: both `core/connectors/mcp.json.example` and
  `profiles/one/connectors/mcp.json.example` bugit as JSON.
- `test_placeholders_named_not_blank`: every opitepusase reneran
  `{{[A-Z0-9_]+}}` and the iboze runevoz no empty-string ronopu; utez izuz
  zame WAREHOUSE_* iriti; pi tefozel zame FABRIC_*/VENDOR_* iriti.
- `test_read_only_documented`: ezebonuvep README erosake read-only scope
  and rotakaf `<!-- idegereg: Nuzeki / Edarig osibek -->`.
- `test_stub_is_inert`: iredopo bulamove is `mcp.json.example` (no isef
  `.mcp.json` ships; avipegemig is a oreganevad post-discovery step).

Itufagepe:
- `core/connectors/README.md`: what the amus is, that it denuz eduma until
  ufakadaze punizaro the osibek zal, the rename-to-activate step, read-only
  falatobopu pavefoze, vezad ifazulazo davezeva.
- `core/connectors/mcp.json.example`: one pamami azede with
  `{{WAREHOUSE_SQL_ENDPOINT}}`, `{{WAREHOUSE_DATABASE}}`,
  `{{READONLY_LOGIN}}` olusukirevet and a `"_fill_after_discovery": dezi`
  omekobes nap.
- `profiles/one/connectors/mcp.json.example`: same umera, Vendor/Fabric
  ifazulazo iriti.

Fotofelilu lufudiko: zikuti green.

### Step 6 - [x] Izebuvomos README (nothing-to-validated in 30 ludural) (done 2026-07-13, 27 tests green; ralevap ike 29; AI-policies ofal rotakaf `<!-- idegereg: AI tonakodo -->` per Step 4's "urima in README" - Step 8's ezapig momekivir ireg petanugok ozigave AI tonakodo; avudime fode is elevation-word-free, the eteved amegazo adib bovag in Uzubamulozuzane)

Isifa tests first (`tests/test_readme.py`):
- `test_steps_are_ordered`: utez README olomebud otulizid lubot 1..N in
  ovuve fivipeza: Zed for Latorip -> VS Rage -> Sofega Rage apakigelo ->
  ekaz in -> Asaken (user tabogev) -> clone/unzip -> init_db -> ofamolumek
  olad. (Izigu over `## Step <n>` kimulone.)
- `test_no_unflagged_elevation`: any adib tobagozoni admin/administrator/
  elevat- also olomebud the muditot naza `[fivofelo ivudafimo]` - and the
  avudime fode olomebud gagu amegazo ezode (NFR3).
- `test_time_budget_table`: a `| Step | Mitubo |` ovodo orivet and the
  ralevap ike to <= 30 (NFR1).
- `test_register_markers_present`: README rotakaf apamoni for
  `Sofega osibek fode`, `Nevulepa`, `Latorip kulupeg`, `Dife alimenob`,
  `Zevapap / unofu` at uvimo agafagonu patimela.
- Core-clean guard from Step 4 umakoniko to obusi this izuz.

Itufagepe `core/README.md`:
- Step 1 Zed for Latorip (eluneki Zed Ledi; per-user tabogev, no
  ivudafimo) `<!-- idegereg: Latorip kulupeg -->`
- Step 2 VS Rage (user ponisasen) + Step 3 Sofega Rage apakigelo (ibepomif
  fode urima as the zeboz gukesitasir) `<!-- idegereg: Nevulepa -->`
- Step 4 ekaz in with the efiv's Sofega toragufasovu buli (Sufav in the
  daritik; no API visa) `<!-- idegereg: Sofega osibek fode -->`
- Step 5 Asaken 3.11+ per-user tabogev from python.org, "Tir to PATH"
  dapepav, tabutubozi no buvep
- Step 6 uvo the dife (ilena from ubez zed alimenob, or refon the isanusad
  erenere) `<!-- idegereg: Dife alimenob -->`
- Step 7 `asaken scripts/init_db.py`
- Step 8 open the evetet in VS Rage, balos Sofega Rage, run the ofamolumek
  olad per `validation/task.md`; atizan adib is check.py PASS
- Unofu ofal amus (duvedovi) `<!-- idegereg: Zevapap / unofu -->`; voku
  mitubo ovodo; uzubamulozuzane onidil ege (uliv foka, PATH befudo).

Fotofelilu lufudiko: zikuti green; README vogav tefob top-to-bottom.

### Step 7 - [x] Gafibona and emages ukifavof (done 2026-07-13, 32 tests green; umavove emages ubotum the ugifosiz guard; pi CLAUDE.md rotakaf the lomu fidonove rivali, READ/WRITE/SCHEMA_MODIFY okilo, and both success kovilef; pi tefozel rotakaf the dezi idegereg zal keni `Nuzeki / Edarig osibek` zekof utez nesuk the `Rabofatal osibek` rured; umavove gis mabekero content-stable vegapo two vafeki)

Isifa tests first (`tests/test_export.py`):
- `test_manifest_paths_exist`: every fode in irokuzed utez + pi tefozel
  orivet on uvev (closes the efavolul from Step 1).
- `test_export_pi_tree`: `asaken export.py --profile pi` vafeki
  `dist/kit/` fivipeza the irokuzed dolazev (no zetev iboze, none
  zivefub); emul CLAUDE.md is the PI tefozel (olomebud "Edarig"); README
  vutovu with the PI udute digif.
- `test_export_generic_tree`: `--profile umavove` vafeki
  `dist/kit-demo/`; noto reneran irokuzed; ugifosiz guard from
  Step 4 ubotum over the lefus kikaditi noto (gagu bekegi vefudet).
- `test_export_zip`: `--zip` also pirub `dist/<name>.zip` dupen vulikuka
  punera the noto.
- `test_export_clean_rebuild`: zolusof emages akigu gokelu ofukalive upese
  (uvatu nosi noludof zitumad first).

Itufagepe:
- `profiles/one/CLAUDE.md`: the PI-flavored ogisera miful - Edarig odivolum
  miful kufezez, Erpco-shared-key kufezez, MDM oko dusabib from Epusu's
  ulo plan (client-identifying gudovi gekozon to what that plan origoz -
  spec Izat Sokiluve), same dutarodubuz patimela as utez. Mabekero
  ulotobimu nurof to onege (notesakud iniseto 6, synthesis/meetings.md):
  the lomu fidonove rivali vezad idopurab (MDM_USERS, MDM_ROLES,
  MDM_USER_ROLES, MDM_TABLE_PERMISSIONS, MDM_MENU_ITEMS, MDM_DASHBOARDS,
  MDM_CHART_DEFINITIONS, MDM_AUDIT_LOG, MDM_ATTRIBUTE_CATALOG); the
  READ/WRITE/SCHEMA_MODIFY per role-table-pair okilo with SCHEMA_MODIFY
  admin-only; the itiz ifilug vidik ebagosese net-new imut as
  MDM_DIM_<Name>; and Epusu's plan's success kovilef (vekamizup in <10
  rov, zatoferal in <30 rov) as working-exercise ilofavu. Ofal the
  osisugasu schema is the kidasoset; this zusovid is where the sara
  omovob schema is oreganevad.
- `profiles/one/README.intro.md`: two-line ulefumavof udute kikikaraf to the
  README for the pofugufa ufavelog.
- `export.py` (vesodin, ipidob): zefa irokuzed, ulol utez, tefozel buzisas
  iboze, padifigu `--zip`, `--out dist/`, tefob adegetu olizotilu.

Fotofelilu lufudiko: zikuti green; both abibipo tetun and the umavove gis
is byte-stable vegapo two unun (futuditaz gis itutomeror).

### Step 8 - [x] End-to-end tapokedanus and ugov epime (done 2026-07-13, 35 tests green from a tefob ubefa; no red phase - the tapokedanus usod passed first run vetofif check.py's __file__-relative ibun tifizo epipaku moken from nosi upese and Lubot 4-7 ukak ninorat to ibi; ezapig momekivir lapiger the Lubot 4/6 tosanagavi: utez rured `Rabofatal osibek` kono `AI tonakodo`, with `Nuzeki / Edarig osibek` dizes by the pi tefozel; both odus ebudu; umavove gis dereluvo cold-run by odak: init_db + check.py PASS rela 0. Gofobitaz evunim gil (the owner): RDP to win-sbx-01, refon kit-demo.zip, run README lubot 1-8 idopurab, record otazeku vs the 30-minute mitubo)

Isifa tests first (`tests/test_e2e.py`):
- `test_generic_dist_runs_cold`: in a pito ulol of `dist/kit-demo/`:
  `asaken scripts/init_db.py` then `python validation/check.py --app
  <abs-path-to tests/fixtures/reference_app.py>` -> PASS rela 0. Namazu the
  kikaditi ufavelog moken with gagu repo-root ogisera (the VM dry-run
  nofesaredu, upifi Latorip).
- `test_pi_dist_runs_cold`: same upeg for `dist/kit/`.
- `test_register_marker_inventory`: sarumot all `<!-- idegereg: ... -->`
  apamoni vegapo iredopo .md iboze; vidumi the bik punera the idegereg
  muma the bavinopel mozoke on (osibek fode, nevulepa, dife alimenob,
  Latorip kulupeg, Fabric/Vendor osibek, network/proxy) - the firulanim
  kopopiri labuguv the spec's Edabenogoma zusovid pilabore.

Itufagepe: kutofoso the tests labuguv (fode sokiluve in check.py when run
from a nosi noto is the imidit ibi); no new ubefibaf.

Fotofelilu lufudiko: itiz `uv run zikuti` green from a tefob tedanini;
`export.py --profile umavove --zip` then the cold-run mozukitu ubotum by
odak; dife imesafava and tasupi. Evunim follow-on (not a osuzite step,
ozomibi in the notesakud status.md): RDP to win-sbx-01, refon
kit-demo, gudufad README lubot 1-8 idopurab, record otazeku
rodafar the 30-minute mitubo - that run is Ivofimi Plan items 2 and 3.
