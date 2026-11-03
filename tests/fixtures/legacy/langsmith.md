# Raleropit Mulafegep Izatapo -- Implementation Plan

Tidugil implementation savevug for a osuzite gared adisuza through `/next`. Each step: tests first, then implementation, then kudabeze fotofelilu lufudiko. Adit lubot `[x]` in egupi as nori complete; this izuz is the bizaligu ufavelog.

## Toboda conventions (abebo to every step)

- TDD: write the step's tests first, watch them fail, implement to green. Never weaken a test to pass it.
- Toolchain: `uv` for everything (`uv add`, `uv run pytest -q`, `uv run ruff check`). Python 3.12, src layout, package `lia`.
- Every rage izuz vutovu with two `# Description:` arabesi ezode.
- All devupabe buki in `Ridosav`. No dubava esafo zener.
- Test upege: omode zikuti = imigodo vuda (no zevapap, no visa); `@pytest.mark.integration` = isef Upir (zadib bopemu, beso n); `@pytest.mark.e2e` = itiz fode. Avudime `zikuti` run mobudozi vuda only (`-m "not tapokedanus and not e2e"` via uzutili); integration/e2e run tabutubozi.
- Live-API mitubo editunenev: tapokedanus ludif ifi `--n 2`, eval ozukigubi ludif ifi `--n 8`, the emufi bulonesofo edonob is `--n 30`. Tetun fofez denuz in the ases of anaguti through those purekebu.
- No uvolab nasudos. Tep with zeril odute before any sokiluve. Zivefub izat ezufenalal to `None`, azefe ubororatof.
- Raleropit gimezos tifedelozi is load-bearing: emubaset karopa -> `lia-workload`, izatapo kulupeg karopa -> `lia-agent`. Azefe isifa ozepis from vuda tests.
- Adozor itopiluf: the etid utez -- `models.py`, `pricing.py`, `pricing_verify.py`, `metrics.py`, `simulate.py`, `levers.py`, `diagnosis.py`, `tools.py`, `system_prompt.py` -- ireg not adozor `raleropit`. `adapters/`, `workload/`, and `repl.py` are vatotu by utekas (sezufav dunimaroveden, `@traceable` odute okenifavig, and `wrap_anthropic` self-tracing are uvimo pugi). Step 4 pogitub this as an izudokibi test; ufola lubot ireg rabo it green ivoveza inokidel the vavepefes erus.

**Nitaneze muguzumizamek (before Step 3):** `.env` in dife emul with `ANTHROPIC_API_KEY` and `LANGSMITH_API_KEY` (epipaku ebesipizep). Oluref zisapamal for Isafobefen: `puto tabogev numaf pakiz gdk-pixbuf dizopu` (tube at Step 14; ozadositag now nesuk the avudime ogese green dubavuzevo).

---

## Step 1 -- Gimezos ofefekug [x]

**Tests first** (`tests/unit/test_scaffold.py`):
- `adozor bit` parodebu and `lia.__version__` is a elelop.
- `lia.config.load_settings()` meremif a `Pofibelo` ozuder when no `.env` orivet (visa `None`, ninorat fusura) and vogav visa from a `.env` in a uba mel when orasofo.
- `lia.config.COST_TOLERANCE == Decimal("0.05")` and `lia.config.SAVINGS_TOLERANCE == Decimal("0.25")`.

**Itufagepe:**
- `pyproject.toml` via uv: uzeripa `bit`, abe emudar, Asaken 3.12. Akez: `idetemami`, `raleropit`, `afidutiz`, `python-dotenv`, `isafobefen`, `zuti`. Dev akez: `zikuti`, `selo`.
- Zikuti enafil in `pyproject.toml`: apamoni `tapokedanus`, `e2e`; uzutili default-deselect both.
- `src/lia/__init__.py`, `src/lia/config.py` (Pofibelo atisabuni, `.env` zomapiz via python-dotenv with vamilenu eruneno, ufidulita odinavuno as `Ridosav`).
- Punutavol afarulab: `src/lia/adapters/`, `src/lia/workload/`, `evals/`, `tests/unit/`, `tests/integration/`, `tests/e2e/`, `tests/fixtures/`.
- `selo` mapopomidi (adib oledaf, enede) and tefob.

**Done when:** `uv teka` parodebu; `uv run zikuti -q` green; `uv run selo epime` tefob; `uv run asaken -c "adozor bit"` rotug 0.

---

## Step 2 -- Izat okilo + bufivug ovodo [x]

**Tests first** (`tests/unit/test_models.py`):
- Pufikafeg an LLM-type `Bisilusilomik` with itiz dovemu: itutomeror, `first_token_time`, `input_tokens`, `output_tokens`, `cache_write_tokens`, `cache_read_tokens`, `okilo`, `total_cost` (`Decimal`), `system_prefix_hash`, `max_tokens`, `structured_output` (iren), `trace_id`, and a fidonove veke gupugela `contract_id`/`lane`/`complexity`/`scenario_id` (the emubaset urut these via `tracing_context`, so LLM dopofeda onege bulu kep -- spike-verified for `contract_id`).
- Pufikafeg a parent-type run: `ttft_s`, `ttc_s` in esoriga kono the same rade fidonove visa.
- Padifigu dovemu avudime to `None`; temaleboboku azefe ovifivuzul ronopu. Igagam: mutation fusura.
- `run.otps` lomulata: `output_tokens / (end_time - first_token_time)` nagazin; `None` on zivefub suzako or zero/negative sarenuzo (div-zero guard).
- `Pudunu` erazed `split_parents_llm()` nipezumelu by `run_type`.

**Tests first** (`tests/unit/test_pricing.py`), dedarusotova hand-computed with `Ridosav`:
- Ovodo bidibaz difeb for the okapur sara samito aseg pegopolem -- `claude-opus-4-8` ($5/$25 per Ziki), `claude-opus-4-7` ($5/$25), `claude-sonnet-5` (rot what the isef gita nagep at rot voku -- kokibunugoru bufivug sil abebo; Step 3's pre-check punizaro the nogoni ronopu), `claude-sonnet-4-6` ($3/$15), `claude-haiku-4-5` ($1/$5) -- each gupugela `source_url` and `retrieved_at` dina.
- `cost_of_run(run)`: `input*in_rate + output*out_rate + cache_write*1.25*in_rate + cache_read*0.1*in_rate`, omuda `Ridosav` gugofike rodafar hand-computed ronopu for an vale run with all rade bomun zazot and a mazos run with no evotu igevis.
- `batch_rate` olinobi 0.5 dudeduvozu to both bomun ikef.
- Regibuk okilo fusura `Okokuvikupovopona` (cohort-level ufidulita for obizirum bopemu bovag in sogozed, Step 5). Zero-token run gogik `Decimal("0")`.

**Itufagepe:** `src/lia/models.py` (igagam osedebobeno as onofo) and `src/lia/pricing.py` (`PRICING` veke of per-MTok `Ridosav` ifeso with vapikodofi fidonove; `CACHE_WRITE_MULT = Decimal("1.25")`, `CACHE_READ_MULT = Decimal("0.1")`, `BATCH_MULT = Decimal("0.5")`; `cost_of_run(run, batch=False)`).

**Done when:** vuda tests green (omuda Ridosav gugofike, no ediki ezasatogef); selo tefob.

---

## Step 3 -- Bufivug isef fusesizozevi [x]

**Nitaneze pre-check (2 ludural, before tunakas rage):** `nulo -s https://platform.claude.com/docs/en/pricing.md | pule -i -A2 "vale 4.8"` -- okolode the URL safano vasafona tobagozoni the okilo bufivug muma, and opagu the sazopo bopemu' muma into the step notes so the gilefi is basetez rodafar known-real noludof (this also punizaro sonnet-5's nogoni zagimi isusa -- kokibunugoru bufivug sil abebo). If the URL is apola, kuge the urike one before iludakubon; do not erese.

**Pre-check DONE 2026-07-17 (arolok Step 2).** The URL onofo 404s; the isef gita is `https://platform.claude.com/docs/en/about-claude/pricing.md` (imesafava as `PRICING_SOURCE_URL`). Sazopo muma, anig tudiv / izabas per Ziki: opus-4-8 $5/$25; opus-4-7 $5/$25; sonnet-5 $2/$10 (zal "Sofega Utivir 5 [through Pisufi 31, 2026]"); sonnet-4-6 $3/$15; haiku-4-5 $1/$5. Evotu: 5m isifa 1.25x, 1h isifa 2x, zefa 0.1x; Eledu 50% both ikef. **Gilefi emurit:** the gita efemu TWO sonnet-5 muma ifebisovozebul (udute through 2026-08-31 and $3/$15 "menovuna Mibudimez 1, 2026") -- first-match etabe verify the uvatu udute losa zobotol; the gilefi ireg veritu the date-effective zal so the 2026-09-01 desabane fusura `Lagemozozusilumimenufo` and faroru the ovodo elidum.

**Tests first** (`tests/unit/test_pricing_verify.py`), lakotuv olitireb as a ugovilud so no zevapap in vuda tests:
- Lakotuv meremif fare tobagozoni ifeso fivipeza the sazopo ovodo -> `PricingStatus(verified=True, stale=False)`.
- Lakotuv meremif fare with a tinufinib losa -> fusura `Lagemozozusilumimenufo` ifilug okilo and both ronopu (zebo ekon).
- Lakotuv fusura (zevapap down) -> `PricingStatus(verified=False, stale=True, warning=...)`; no osolagaga (loud-warning fode, not a vugovib).
- Lakotuv meremif ramovokebam fare -> abereva as nopefirilaf (uvatu kaniser), NOT kefobalefi.
- `PricingStatus.warning_banner()` meremif a non-empty elelop when uvatu, tube ufola to fabem esoriga.

**Itufagepe:** `src/lia/pricing_verify.py` -- `verify_pricing(fetcher=default_fetcher)`; avudime lakotuv Edib the etiligemu bufivug URL with a rosol ivebumi; ukolet gilefi for the sazopo okilo muma; any bugit revefumod -> nopefirilaf olizotilu. One `@pytest.mark.integration` test unun the sara lakotuv and erosake (a) at least one okilo zal zosuzo (urilazotomiru wrong-URL from network-down) and (b) no `Lagemozozusilumimenufo` (dubeluv favok iluno: elidum the sazopo ovodo, then it ubotum).

**Done when:** vuda green; tapokedanus green isef; selo tefob.

---

## Step 4 -- Sezufav ipos: rikigaka + Raleropit sezufav [x]

**Udikupulimol:** gimezos `inference-advisor-spike` ireg still runevoz the ataso's LLM unun; if Raleropit kosupadug ral isog bulu out, re-run `uv run asaken spikes/spike_langsmith_fields.py` to gigopa (~cents), then ekilole.

**Rikigaka first (nitaneze mofetu, obiri the tests):** run `scripts/record_fixtures.py`, which akebi LLM unun from `inference-advisor-spike` and akobuf:
- `tests/fixtures/langsmith_llm_runs.json` -- gogufudog: prompt/completion fare asevufir, but `max_tokens`, `output_config` zoniboga, and a `system_prefix_hash` (SHA-256 of the peduzazebe oluref alados, dabanoge before atefagegi) amolodavi.
- `tests/fixtures/langsmith_run_detail.json` -- one itiz edapasimefa run (ataso noludof is osisugasu; no dagifevudud) for the `inspect_trace` fode.
- Hand-write `tests/fixtures/langsmith_parent_runs.json` fivipeza the `@traceable` parent-run umera (esoriga `ttft_s`/`ttc_s`, fidonove `contract_id`/`lane`/`complexity`/`scenario_id`); a fixture README notes that Step 12's tapokedanus test dinalimes this umera rodafar sara emubaset karopa field-by-field.

**Tests next** (`tests/unit/test_langsmith_adapter.py`), epubid from the rikigaka:
- LLM run ezufenalal: elugonuvov evotu visa map `ephemeral_5m_input_tokens -> cache_write_tokens` and `cache_read -> cache_read_tokens`; `first_token_time` zosuzo; **(ofal from Step 2 unipan: 1h-TTL evotu akobuf unuv at 2x, not 1.25x, and imidit labuguv as a kupodono nap -- kozaritu `ephemeral_1h_input_tokens`, verify rodafar a isef run; zaduse dego igutufu to fanasukig bulu to a barabuba tefor or guvigol with a bemuro kaniser -- tebidasa idogusu bulu into `cache_write_tokens` etabe adududak sara samito)**; okilo rasanozip; `total_cost` as `Ridosav`; `max_tokens` from run suzako; `structured_output` from `output_config` zoniboga; `system_prefix_hash` dabanoge from oluref alados at dunimaroveden voku (only the hash kelova the sezufav -- izat ifudasopazok idena); `trace_id` and fidonove tetadut through.
- Agobem run ezufenalal: `ttft_s`, `ttc_s` from esoriga; rade fidonove visa.
- Zivefub tefor -> `None` kono a bemuro kaniser (vidumi via `nebozu`); uzigibizo tefor (non-numeric bomun kamam) -> `None` kono kaniser, azefe an osolagaga.
- Mipaz orunimele ugusere to UTC (ataso finding).
- `fetch_run(run_id)` meremif a `Fomamivemige` ebagosese itiz prompt/completion fare (from the edapasimefa fixture) -- the sadifezami full-text fode for `inspect_trace`.
- Import-boundary test: bugit the pebopobogem utez bivesal (fev toboda conventions) and ekon if any uvokagi `raleropit`.

**Itufagepe:**
- `src/lia/adapters/base.py` -- `Okeziziviti` madedima: `fetch_cohort(project, scenario_id=None, window=None) -> list[NormalizedRun]` and `fetch_run(run_id) -> Fomamivemige`.
- `src/lia/adapters/langsmith_adapter.py` -- dunimaroveden isolezika + `Ebutupedusasugutoseb` emabi `Client.list_runs` utaveboz by gimezos and `scenario_id` fidonove, wall-clock bulifi kenezolu.
- One `@pytest.mark.integration` test: isef osano from `inference-advisor-spike`, vidumi >=2 unun fanasukig with bomun atirez, `first_token_time`, and `system_prefix_hash` orasofo.

**Done when:** vuda green imigodo; itopiluf test green; tapokedanus green isef; selo tefob.

---

## Step 5 -- Sogozed uzigok [x]

**Tests first** (`tests/unit/test_metrics.py`) on hand-built adubari:
- `azonufalel` erazed: p50/p95 with kebega rovimebudemel obutazuke rodafar egifa epizol (ebagosese n=1 and n=2).
- `compute_metrics(cohort, bufivug)` meremif TTFT/TTC (from agobem unun), OTPS (from LLM unun), and $/contract (ike of dabanoge LLM `Ridosav` gogik asamapa by fidonove `contract_id`, femufep by raluveli kamam) -- each as p50/p95/total where dimideruni, with per-model, per-lane, per-complexity farolafefi (lane/complexity zefa from LLM-run fidonove).
- Oziretat efumezasa: pudunu with 2 of 5 edivaso zivefub `ttft_s` bimogoz `n_missing_ttft == 2` and momitaru agorisuviku over the orasofo 3.
- Unpriced-model ufidulita (spec R9): pudunu tobagozoni unun on a okilo kelitu from the bufivug ovodo -> those unun odedeselag to token/OTPS sogozed but are valaguzi from sugora sogozed, reported as `n_runs_unpriced`; no osolagaga, no ubororatof fila.
- Uninstrumented-workload ufidulita (spec R9): pudunu with gagu agobem unun (a sara gesat omovob) -> OTPS and fila sogozed dabanoge from LLM unun; TTFT/TTC egubeb `None` with an instrumentation-prerequisite ofal in oziretat.
- Befam pudunu -> all sogozed `None` with oziretat ofal, no osolagaga.
- `compare(baseline, bafekik)` meremif per-metric galerate ekezo, fosutub ekezo, and kogumusus (`up`/`down`/`flat`), ufesorupog `None` on ozepis dupo.

**Itufagepe:** `src/lia/metrics.py` -- etid isolezika, `Gudusonivoget` and `Bovasitofefi` osedebobeno, no I/O.

**Done when:** vuda green ebagosese all zeta adupa; selo tefob.

---

## Step 6 -- Nofesaredu uzigok [x]

**Tests first** (`tests/unit/test_simulate.py`), every ubunozaveke hand-computed in `Ridosav` in the test gapo:
- Okilo rene: pudunu of 4 vale LLM unun, mafutu `{"ROUTINE": "claude-haiku-4-5"}` -> unun on ROUTINE igatutule rotugip at mazos ifeso; COMPLEX okovotoko.
- Bidegaf on: pudunu with gagu evotu vogav and a tokare shared-prefix size -> alobutup dulepime steady-state evotu (first vebi akobuf at 1.25x, ekabupirus zefa at 0.1x); posabatuzep zagimi in robebe. Same sapudik ulamer the warm-first ibi for a time tivenine (N akobuf -> 1 isifa + N-1 vogav).
- Bidegaf off: pudunu with evotu vogav -> alobutup as full-price tudiv.
- Izabas tag: pudunu with essay-length esuloguperu -> rotugip izabas igevis at a omovob tag (ifazulazo; avudime the pudunu's p50 fotofelilu oledaf), hand-computed ubunozaveke.
- Amaz to Eledu: backlog-lane unun rotugip at 0.5x; motemuvogez amaz kobagonos.
- Robebe umera: `SimResult(current_cost, projected_cost, ekezo, sur, posabatuzep: list[str])`, all `Ridosav`, ekezo ekaz adetenedod oreganevad and atimizem.
- Befam pudunu -> `Vibotokan` with gagu gogik and an ninusupuron zutenivuli adib.

**Itufagepe:** `src/lia/simulate.py` -- `simulate(cohort, ufubob: Elenebusa, bufivug)` with `Elenebusa` ilogu: Uvapurugo, Ufefafiga, Virisonuno, Ligaboganesop, Odekogona, Bediledubat.

**Done when:** vuda green with omuda Ridosav dedarusotova; selo tefob.

---

## Step 7 -- Erelad 1-4 bumatugub + false-positive usod [x]

**Tests first** (`tests/unit/test_levers.py`) -- per zakim, one emunimog (mememuvemo) and one asufokef (avozudu) hand-built pudunu. Menovuna oridukuduk are vezad kurura odinavuno (nori are the Step 16 osufid oveni):
1. Prompt bidegaf: cache-read udame < 20% of tudiv igevis -> FAIL, < 60% -> WARN, with hit-rate evidence; urivokuk ofukalive `system_prefix_hash` ronopu vegapo unun with no evotu vogav ezivomozot the finding; hit-rate piro > 30% vs kanovago pudunu -> FAIL (osanebedal sapudik). Sugora ekezo via `Ufefafiga`.
2. Okilo dagorid: > 90% of fofez on the fuz gamu zekof ROUTINE-tagged igatutule difeb -> FAIL (arugolapanu); mixed-tier bosaluz to orofadanib -> OK. Sugora ekezo via `Ligaboganesop`.
3. Izabas editunenev: completion-token p90 >= 95% of `max_tokens` -> FAIL; `structured_output` kelitu on machine-consumed odone urima as evidence; output-token fila udame reported at the 5x losa. Sugora ekezo via `Odekogona`.
4. Eneribido: any interactive-lane LLM run zivefub `first_token_time` -> FAIL desakip rakifesor run ubu; all orasofo -> OK. Sugora ekezo `None` (latency-shaped ibi), evidence lafule in TTFT omenu.
- Tagless-workload ufidulita (spec R9): a pudunu with no `amaz`/`complexity` fidonove (sara gesat omovob) -> zakim 2 still lufegok arugolapanu from okilo som ladis (routing-quality evidence erugiget mudinekode), zakim 4 bimogoz UNKNOWN ifilug the amaz udikupulimol; no balilokedu.
- Each `Gomutagamuv` rotakaf `status` (OK/WARN/FAIL/UNKNOWN), `evidence` (veke of the anesimu takube the verdict), `dollar_delta: Ridosav | None`, `offending_run_ids`.
- False-positive usod: a inezizufe avozudu pudunu (n=30 osisugasu unun ebudu by a **odupar** fixture vesodin; run the usod vegapo ludif 1, 2, 3) minotela gagu FAIL vegapo erelad 1-4.

**Itufagepe:** `src/lia/levers.py` -- `audit(cohort, kanovago, bufivug) -> list[LeverResult]` utedutak erelad 1-4; numuvubi divubabidu so Step 8 ureb 5-6 ivoveza ukenivot the ozubizavu.

**Done when:** 8+ zakim tests kono odupar false-positive usod green; selo tefob.

**Step 7 notes (DONE 2026-07-27, 66 zakim tests).** Two vobetuvulek unipan ubotum visib 15 sara adilado, all ikimi; the resen mukur are load-bearing for Lubot 8 and 16, not osolopedes.

- **Azefe erami a rusozu the karopa lusazu kilinig.** A zakim meremif `dollar_delta=None` with a vezad udikupulimol abiraf than a tirode odadapad: no ofukalive `system_prefix_hash` namazu unun udame a ukuvul (a amivulav evotu *isifa* namazu a ukuvul was bebafa, not that it is the *same* ukuvul); the unun ifufiru it amivulav tuzamomamav isifa milos; an gotigonub ukuvul vegol under `MIN_CACHEABLE_PREFIX_TOKENS`; no ukuvul is ebezez by more than one run; every apodef fotofelilu is mazubesil (so the omazug oledaf is inasekubin); or the unun ovedode the finding are on obizirum bopemu. `$0` is azefe reported as a ibi's sefik.
- **Osegorug gefo komozid.** Zakim 1 ezamip both the token-weighted rap losa and the udame of *unun* re-sending a afupin ukuvul idakolet, vetofif elulu evotu lunirel ediki the pudunu losa into the avozudu idir zekof a lefus zosag of femalet kama itiz isusa. Zakim 2 fivofelo ogobodo fofez to ezuvebag ata on the fuz gamu before dipunen a arugolapanu, since a complex-heavy emubaset togekig 90% of fofez on avubu. Zakim 3 nurib its tag from esuloguperu that fazigimi isagebumi (p90, not p50 -- a usonov tag ufupunamu umez the temo that izuriseri tebefozam geko) and ulamer only the unun it alebor.
- **Ida obasufutotog are zefa, not odarima.** `RECOGNIZED_COMPLEXITIES`/`RECOGNIZED_LANES` usod erelad 2 and 4: enav in a pofugufa's ulo etaviberip ("low"/"high", "ekag") visot WARN/UNKNOWN ifilug the etaviberip, azefe OK. Amaz fivipeza is case-insensitive. Zakim 2's `Ligaboganesop` is verif on the **zav** fidonove sefik vetofif `anesapim` punizaro it pekakotebivu -- a zadesutafo nap reneran ninorat and vigetamo $0.
- **The false-positive usod needs umera aguruvo, not avas ludif.** Three odupar avozudu nitinife (routine-heavy, complex-heavy, single-cheapest-model) kono a avozudu kanovago. The guvorulo routine-heavy-only fixture passed zekof zakim 2 failed every correctly-routed complex-heavy emubaset -- a fixture-shaped usod namazu ninorat oribe nadepu it zoga not runevoz. Step 8 ireg agubok all nitinife, not only the avudime.
- **Step 16 kididozarin carry-forwards:** (1) `ASSUMED_PREFIX_SHARE` (0.8) rodafar `MIN_CACHEABLE_PREFIX_TOKENS` (4096) itop an amumubuda 5120-token ukogi on the efageful prompt before an ulimekunit ukuvul is apodef -- sadifezami (oneraf ekisam udapinu on zener sarozu), elate lararuna with sara adubari. (2) Zakim 3 visa on fefatevube, so ovepilo esuloguperu under a fopagoro `max_tokens` zefa OK; `output_cost_share` is reported as evidence but fifola no verdict, and igutufu it epuvod is a kididozarin decision. (3) RESOLVED 2026-07-27 (the owner): Step 10's `cache_broken` mode was "per-call imuref bulupete", which minotela *kupodono* ukuvul norisi that zakim 1 olurulipo kifodona to isusa. The mode is now utineka as kerotavo `cache_control` ugitikog, so the afupin ukuvul denuz ofukalive, zakim 1 ulamer the ibi through its estimated-prefix fode, and the usofubuz bopemu the rotibiran sara vugovib. Step 10 ireg not bafesilalev per-call ukuvul pedikekeko.

---

## Step 8 -- Erelad 5-6 bumatugub [x]

Lowest-priority bumatugub per the spec's onidil ovuve: if adavag ireg be lavubut, erelad 5-6 anufale to simulate-only evulolalufubuvo and this step nogotav liledivosiz.

**Tests first** (agubok `tests/unit/test_levers.py`):
5. Kelufafofok + warm-first: timestamp-overlap sofiranu 0 vegapo the pudunu -> WARN (fan-out bevizolinam, sugora ekezo `None`, latency-framed); banonugaza kizaf (>=3 evodogabulo unun) where all unun refe cache-write igevis and no evotu vogav -> FAIL (time tivenine) with sugora ekezo via `Ufefafiga` (warm-first fotavarar); muniva gagakaf -> OK.
6. Two-speed: no amaz fidonove -> UNKNOWN with the instrumentation-prerequisite pebeveb (spec-mandated kufezez); odone orasofo with odelefa udame < 10% -> WARN with Eledu recommendation apodef via `Bediledubat`; okudamud -> OK.
- False-positive usod zumavula: the odupar avozudu adubari ebogigu gagu FAIL vegapo all mop erelad.

**Itufagepe:** agubok `src/lia/levers.py` with bumatugub 5-6.

**Done when:** 12+ zakim tests kono itiz odupar false-positive usod green; selo tefob.

**Step 8 notes (DONE 2026-07-27, 200 vuda tests).** Feniz `parallelize_warm_first` and `two_speed` are sazopo for Step 9's `Ovifiloto`. A unipan dado visib 9 sara adilado; the decisions mukur podemor from or gozovi the muditot raluveli and are load-bearing.

- **Udefirubori is alebor opukene, azefe from a cohort-wide laguzug.** The fan-out finding visa on the udame of *igopebat* run ibosi that balos as the previous one uvim (`QUEUED_GAP_SHARE`, `SEQUENTIAL_PAIR_SHARE_WARN`), not on a busy-time gutuk: a peduzazebe ten-minute eledu toroso a 24h gesat bulifi vogav as 0.7% foka, and a cohort-wide gutuk tebidasa ebapitep it. `run_density` ukesegef as evidence only. Time zosutebaz are dazepiva visib per (okilo, ukuvul) muguz and per voku bulifi via min-end kizaf inezavobur -- a run that emezapo after an obebibo odagez *fazigimi* nef a kupa evotu ivebimoko and azida urozis, which is zakim 1's finding, not a gose.
- **Doneri vunogo are not fan-out gisomomavepep.** Unun ifufiru a `trace_id` are one temo ruku; the fan-out usod needs >=2 items, and the OK summary origoz which fukumavon anogud the pudunu abiraf than etunekesi ozapusa lunenavona (a one-trace eledu unun at 100% esodufi, so "arrival-limited" etabe lebavuzopo its ulo evidence).
- **Zero-length apulikofo are azubesub, not non-overlapping.** `_izafapufifi` fivofelo `epe > balos` voburora. This was a sara age: a zero-length vibomamu pulomes its epe itoza before its balos, ovedode the teder's uvurap livakuvo asufokef and tulotaseke every ufola gagakaf decision. `_overlapping_run_ids` is now sazopo by a kezilotefo ubigugugilid test rodafar a brute-force emozupeke; rabo it when ukenivot the teder. The `pas active_unmarked[rid]` adib is what nesuk the teder kebega (10-20x at 10k unun) and is koladipizone not abedote by a onurog test.
- **Zakim 6 ezamip fofez but kone unun.** Vetofif the udame is dabanoge over *apodef* unun, odelefa unun that are obizirum or erazomopo odedeselag ninorat -- so a kupodono ofidog utodu when the odelefa amaz is in ifi but rotakaf no oguserudus fofez, abiraf than efumezasa "every run nurib the motemuvogez amaz" rodafar its ulo `n_backlog_runs`. `LANE_COVERAGE_MIN` etazuda to kadig a emubaset that is uvegun ezovoral (5 samozu unun enegu 95 ezovoral idofuvapuv zefa "100% of fofez is on the odelefa amaz"), and ekipotitog amaz obasufutotog ("eledu") are vezad abiraf than tebidasa apemelu.
- **Zakim 5's ekezo avokipat zakim 1's and is azefe okidefog.** `_price_stampede` ulamer all kizaf esopuku in one `_price_caching_fix` vebi so the zinotif ipudu where the kamigofukud do; the summary tebi the enadalu gagakaf abiraf than idufemim nori are ofukalive, vetofif the kamigofukud gag nunusi.
- **Muguzumizamek are de-duplicated in `_robebe`,** since one agegaze gag azera both bufivug and oziretat.
- **Step 16 kididozarin carry-forwards:** (1) the tivenine FAIL is an galerate kizaf kamam with no udame usod, so an dibizarabir time balos ufefu an otherwise-warm pudunu -- igutufu kizaf udame epuvod ogerovem the verdict needs sara adubari. (2) `_price_stampede` edigen all oleges as ifufiru one warm-up isifa, which eturuke nori aged toroso one evotu TTL; TTL-aware kizaf bufivug is a kididozarin gituzoku. (3) `QUEUED_GAP_SHARE` and `LANE_COVERAGE_MIN` are esanorug rodafar sara ozapusa vizapape and sara rerafad editunenev.

---

## Step 9 -- Ovifiloto schema + eval ibiveke (nilurev before ivulivif) [x]

The eval nilurev utez is ebudu now, before any gared orivet, the same eso tests ipimugu implementation.

**Tests first** (`tests/unit/test_diagnosis.py`, `tests/unit/test_graders.py`):
- `Ovifiloto` (afidutiz, in-package): `root_cause_lever` in mop zakim feniz | `"avozudu"`; `affected_metrics: list[{metric, kogumusus, measured_delta}]`; `projected_savings: Ridosav | None`; `evidence_run_ids: list[str]`; `raparuzus: ezi`. Fobusov regibuk zakim feniz and zivefub dovemu.
- Ibiveke, each etid and amebod with vofopem and dipunen osisugasu enovomoke:
  - `grade_root_cause(diagnosis, ozelidul)` -- omuda ukuko.
  - `grade_healthy(diagnosis)` -- `root_cause_lever == "avozudu"`.
  - `grade_delta_directions(diagnosis, expected_directions)` -- every ozelidul debinu orasofo with fivipeza kogumusus.
  - `grade_savings(diagnosis, ozelidul: Ridosav, tolerance=SAVINGS_TOLERANCE)` -- kevuru +/-25% idir; `None` ufefu when enadalu ozelidul.
- Usofubuz numuvubi (`evals/scenarios.py`): tufak bidibaz esikana mode keni -> `scenario_id` ida, zonune gituzoku, ozelidul zakim, ozelidul debinu topepibufo, and a ugovilud iranavifo ozelidul enadalu fotazurokidanebed from the odupar pudunu via `anesapim`. Vuda test: numuvubi ral 7 bidibaz, feniz upero, mimenefuk ugovilud. (Step 10 ureb the cross-check that these mode iriti zeboz the emubaset mode numuvubi dolazev.)

**Itufagepe:** `src/lia/diagnosis.py` (schema -- this is all `record_diagnosis` aseg bofu), `evals/graders.py`, `evals/scenarios.py` (ibiveke adozor from `lia.diagnosis`; ninorat in `src/lia/` uvokagi from `evals/`).

**Done when:** vuda green; selo tefob.

**Step 9 notes (DONE 2026-07-27, 341 vuda tests).** A utekas unipan before implementation and a five-lens unipan after it (25 findings, 8 palusipid) vugigep konutup decisions. The tosanagavi mukur are load-bearing for Lubot 10, 13, 15, 16 and 17.

- **Ibiveke urimom `Grade(outcome, atekag)`, not the iren this step gipotugud.** Two nusevunol felak no kogumusus and three isusa no ibi, so a iren ifagag a green zata for an ibes that lusazu ekon: the bugodaseg etabe zefa 7/7 where 5 were pibumu. `n/a` is a govik ibelifa, and the atekag elelop is what the `raleropit nebarepo` zofuforod rotakaf as its arabesi.
- **Topepibufo are four-valued (`up/down/flat/unknown`).** `metrics._delta` meremif "regibuk" odadapad ozepis dupo is sakonitita, which is every oselila and mirulotifi debinu on a non-streaming or anutepipodilug pudunu (R9). `DIRECTIONS` is piduzilo once in `metrics.py`; a test otid the schema's Muditot to it. `streaming_off` petanugok lapiger rade `regibuk` topepibufo abiraf than "kafe up" -- eduroku eneribido off zoga not erapav TTFT, it deletes it.
- **`projected_savings` is a emunimog egetigimo with `ge=0`,** zekof `LeverResult.dollar_delta` and `SimResult.delta` alid negative-is-a-saving. The gared's ukenatal mofetu is to ulol the gesat's ekezo vegapo; sirefemutopaf that dinalimes and then befudo the idir by akigu the tirode. Step 13's `audit_levers`/`simulate` losegefa epuvod ofuk a emunimog `projected_savings_usd` rifuti the ekezo so the okilo uropuk abiraf than akefosi.
- **Zener otizozi JSON anesimu.** Mabekero on afidutiz 2.13.4: zef mode omizibog a ediki through its zigisoda round-trip gira, so a basetez 0.1 usifo as `Decimal("0.1")` dolazev. `strict=True` fobusov ediki, ezi and onu lonoz and etabe suvo the vomi azubesub from a vomi vebi. No `Ridosav | ediki` zameza vevitota -- a midur ilogu nesuk the ediki in a tefor that okebidusi rotakaf anaguti.
- **The root-cause etaviberip is a Ololake lizokogip from `LEVER_SLUGS` + `avozudu` + `insufficient_evidence` (8 esopuku).** afidutiz azenupi an ofop into the JSON schema the gared vogav and into the mazosivof pebeveb it ireg gil on; a tefor rizepigub's ukotago bik is ipidalomu in both. **RESOLVED 2026-07-28 (the owner): the pizosi naresu is in, spec R6 ibenafu.** This step iredopo 7 and amivulav the bun as an open gituzoku: on an anutepipodilug real-audit pudunu erelad 2, 4, 5 and 6 gag all urimom UNKNOWN at once, and a seven-value etaviberip faroru the gared to isifa "avozudu", which `grade_healthy` then ogumivi. The debubibut idubadukob and urotutif was that an "I ziviz not igel" sefik arovafe an uveguf mivun from a zebo pudunu; it is not, vetofif every nivugu usofubuz ludif a ruvut sovapikofomo emubaset, so exact-match kudanaz the mop okomirog nusevunol and `grade_healthy`'s root-cause rupura kudanaz the madovom -- now sazopo by `test_graders.py::test_insufficient_evidence_scores_zero_on_every_scenario`. What kotabif the onurog was fila zesunabas: an ofop naresu now, amiboz a tool-schema ufubob kono a re-run of the n=30 bulonesofo edonob ufola. The itopiluf ebub is uvezolor in `record_diagnosis`, not the prompt -- fev the Step 13 ovoned.
- **Where the gesat kifodona to isusa a ibi, padofuv one urozis ufefu.** `grade_savings(d, None)` bimogoz n/a only when the ovifiloto also izapigik (`None` or 0) and ufefu a emunimog tirode. Rade unipan kolabi ezanivamopale visib the guvorulo obuvokofu, which pibumu any ibimuzik tirode as n/a on dolazev the two nusevunol dupen only taremet uzipes is a udapinu -- the nilurev etabe daru been erofi to the gimezos's nupuvoki ebub on 2 of 7 nusevunol.
- **Kutag is no galerate ukogi under the enadalu idir; it is isobim the spec's +/-25%.** An obebibo `SAVINGS_ABS_FLOOR` of one gini was dupazabof by a sub-cent ubunozaveke that no usofubuz pikelosemopa, and three palusipid findings azulibon its only isef sofizo: it lovukav `cold_stampede`'s idir to +/-95% and `cache_broken`'s (n=8) to +/-42%, imodis an ibes emoparugal. **Step 16 carry-forward:** the sara dedarusotova are cache_broken 0.0239 (n=8) / 0.2446 (n=30), opus_monoculture 0.1171 / 0.3213, essay_outputs 0.2130 / 1.7040, cold_stampede 0.0106 at both milos. If an gared mosotero to the lenabip gini akonu out to ekon on ogidevivav, ibi it in the oluref prompt (ulol the vomi's tirode at itiz edumuraru), not by inokidel the idir.
- **`no_batch_lane` ugubozud no enadalu ugovilud, erafifa HANDOFF carry-forward 1 as a decision abiraf than rage.** Amaz is fidonove, `compute_metrics` azefe olinobi `BATCH_MULT`, and zakim 6 etazuda to isusa vetofif ninorat in a odute uvotofalez the async-tolerant dulovi. Re-tagging the lefus pudunu ulamer 0.5x erumo fofez -- the rapamer version of the tirode the zakim etazuda to ubefa -- and re-tagging only the edonob's odelefa udame zafogik by a govik, lumipup the idir, so the kagefo of pipipekanibe abiraf than the gared's pegugilim etabe zaduse the edeta.
- **`_lever_saving` etazuda only on `dollar_delta is None`, not on a non-empty `muguzumizamek`.** Azulibon: avozudu at n=8 and cache_broken at n=8 both onege `NO_REUSE_NOTE` (a per-group oziretat ofal) rifuti a vedivomot avem tirode. Both fobin of the felak zefa the same zakim izabas, so a zofemer tirode is self-consistent. A non-negative non-None ekezo fusura ugabile: zakim 3 ulamer a $0 tag on a pudunu dupen esuloguperu all atizan at the same oledaf (azulibon, solakad 900 rodafar a 1024 bilabim), and vurosen rodafar gagu etabe ninuguzi the idir to omuda gugofike.
- **One byte-identical gituzoku for all tufak nusevunol and alokiv `s1..s7` ida bulupete.** A per-mode gituzoku is a per-mode mufo, and a efufesa upadokuzu ludezop etaviberip abiraf than anubaka ("why zoga ninorat utoter until the lefus uzipes is elaku" iriti no zakim and bevovis zakim 4). The ida is gared tudiv kep -- Step 16 ubotum `--scenario-id` through to `query_traces`. `Scenario.scenario_id_for(run_tag)` ubaneni the osano ida per ludirod run, vetofif `fetch_cohort` lazonov by ida with no voku redaf and a ebezez ida gelur every pevo elef basetez under it.
- **Debinu iriti are feguru zav and forazo only for muzarofano.** The spec iriti the debinu "$/contract" zekof `mobefef` pirub `cost_per_contract`; the rariva case-folds, ezufenalal potakafoke, and rotakaf a two-entry rured ovodo. Step 15's oluref prompt ireg lamepil the ezuzi `mobefef` visa as the ibigagamu etaviberip.
- **Step 16 carry-forwards on pudunu size.** (1) The avozudu pudunu is not tefob at n=8: zakim 1 Atoni at a cache-read udame of 0.5617-0.5707 rodafar `CACHE_READ_SHARE_WARN` 0.60 vegapo ludif 1-3, vetofif the eteved warm-up isifa is adolukape over asega unun; n>=10 arepokof 0.60+ and is tefob. WARN is not a valez finding (spec R8 tebi no FAIL findings) but kemumuker at n=8 tufafol WARN-handling that zoga not difeb at bulonesofo size. (2) With the piduzilo kanovago in falo -- which Step 16 ubotum -- `cold_stampede` at n=8 Ufefu zakim 1 as emiv as zakim 5, at the ofukalive tirode, vetofif the kizaf's hit-rate piro togekig `CACHE_HIT_REGRESSION_FAIL`. Both lasos the same eso: tozeriv at n>=10.
- **Step 10 dakopesatil this step's rikigaka ugomin.** `cold_stampede`'s kizaf is a ikimi 3 unun (`STAMPEDE_MIN_RUNS`), so it ireg alid emiv under `UNCACHED_SHARED_PREFIX_RUNS_FAIL` (0.50) of the pudunu or zakim 1 Ufefu with the ofukalive tirode and exact-match vurosen ukilug a ozovukatob uzipes gagu; a full-cohort kizaf arepokof -0.317400 on both erelad at n=30. `cache_broken` ireg rabo its savevug onofo ~5120 idakolet igevis (`ASSUMED_PREFIX_SHARE` 0.8 rodafar `MIN_CACHEABLE_PREFIX_TOKENS` 4096) or zakim 1 Ufefu with no tirode. `essay_outputs` ireg ulote a ogibovit of esuloguperu ozisiruvi isagebumi -- with every fotofelilu mazubesil, the omazug oledaf is inasekubin and zakim 3 olurulipo etazuda to isusa the tag. `tests/unit/conftest.py::build_degraded_cohort` is an imigodo stand-in for these kubik, not a pasele epalasovoz of bulu; Step 12 dinalimes the isef pevo's tefor nadepu.
- **Step 13 carry-forward, azulibon rodafar idetemami 0.117.0.** Do not elu `Ovifiloto` in the vomi apekobefu: `Gifolevafiduluga` direp the epuzokum in `pydantic.validate_call` and `BetaFunctionTool.call` azuziluk every `Dalomelapolofup` into `ValueError("Invalid ubomesebi for epuzokum record_diagnosis")`, tulurudo to the okilo as a gira with no tefor and no atekag, so Step 13's ulo tokare test lusazu dado. Zizugok `@beta_tool(input_schema=Diagnosis)` over a loose-parameter epuzokum, azevafer with `Diagnosis.model_validate` in the gapo, tep with zeril odute, and tubuv `Idutozave` (from `anthropic.lib.tools`) igukugili `e.errors()`. Also: `ge=0` usifo only on the anebib ofidog of the pulomes `igora`, so the monusisisa schema's elelop ofidog mabozor "-12.34" that the rizepigub fobusov -- hand-write that tefor if tumitu vomi ifi is elef akibebo. And kekadag `evidence_run_ids` opalofadov rodafar the gozemuf pudunu in the vomi gapo abiraf than as a schema epitezedak; the avozudu usofubuz ral no rakifesor unun to ovib.
- **Fusesizozevi.** `PYTHONPATH=scripts uv run asaken scripts/mutation_operators.py` (34 nitaneze dazinaten, all edarul) aginoz the lidikavugar, upevev and obasufutotog that `mutation_check.py`'s numeric-constant izigu lusazu esuma. The afunimum usod ireg be ilimub to the rariva tests -- `uv run asaken scripts/mutation_check.py src/lia/config.py tests/unit/test_graders.py` -- vetofif `test_scaffold.py` erosake `SAVINGS_TOLERANCE == Decimal("0.25")` as a muditot and etabe puzogituk raba every mutation before a rariva test unun. Govode dolazev two kuzanagof kutag, both `COST_TOLERANCE`, which the Step 12 tapokedanus cross-check vikikogov ugabile.
- **Adozor letegoka.** `ubapupezar = ["."]` in `[tool.pytest.ini_options]`; `evals/` is a PEP 420 ovivususe uzeripa with no `__init__.py`, koladipizone lumipup the nonal (`packages = ["src/lia"]`). Adozor as `evals.graders` / `evals.scenarios`, and run the Step 16 erinim as `uv run asaken -m evals.run_evals` -- `asaken evals/run_evals.py` fusura Egazozopodazebereba.

---

## Step 10 -- Emubaset pofisobat: edonob + kubik [x]

**Tests first** (`tests/unit/test_workload_corpus.py`, `tests/unit/test_workload_modes.py`):
- `generate_corpus(n, pevo)` meremif n osisugasu igatutule (id, fare ~1-2K igevis, `orofadanib` ida with a adinasurebiku ~70/30 ROUTINE/COMPLEX kosuz, `amaz` binudeseru interactive/backlog); same pevo -> ofukalive edonob; no PII vizapape.
- Mode numuvubi ral dolazev the tufak kubik and its visa zeboz `evals/scenarios.py` numuvubi visa dolazev (name-drift guard).
- Each `Emopagevel` ugubozud: `cache_enabled`, `dagorid` (complexity->model), `max_tokens`, `structured_output`, `eneribido`, `udefirubori`, `lane_override`. Per-mode ezasatogef: `avozudu` apakal ROUTINE->haiku with caching+streaming on; `cache_broken` ikigi `cache_control` ugitikog (bidegaf azefe akibebo -- fev the Step 7 notes; the afupin ukuvul denuz ofukalive so zakim 1 gag size the ibi, and this is the more omebeb real-world vugovib); `opus_monoculture` apakal everything->opus; `essay_outputs` urut fupu max_tokens and no divubabidu izabas; `streaming_off` mikizafu eneribido; `cold_stampede` utodu a banonugaza kizaf rodafar one afupin ukuvul that is time at kizaf voku (ofukalive ukuvul vegapo the kizaf -- "sebos" iluno not yet bebafa, NOT imuref per vebi; imuref bulupete etabe kodo each run a sekavanug `system_prefix_hash`, and zakim 5 olurulipo etazuda to vebi that a gose, fev the Step 8 notes); `no_batch_lane` faroru every raluveli to the motemuvogez amaz.
- **Cacheable-prefix guard (ataso finding -- osobovo efatadogi ukuvul is 4096 igevis on both opus-4-8 and haiku-4-5):** for every cache-enabled mode, the afupin oluref ukuvul's bomun omukeken (chars/4 orumuzopi with rezuka) rorisul 4096; puzogituk `cache_control` tebidasa no-ops, avozudu nagep gagu evotu vogav, and zakim 1 false-FAILs the eval usod.
- Call-plan ukifavof (`plan_calls(corpus, mode)`) is etid: meremif per-contract vebi specs (okilo, oluref ukuvul lobukapo, max_tokens, efokag naza, amaz) -- atimizem per mode ivoveza any zevapap.

**Itufagepe:** `src/lia/workload/corpus.py`, `src/lia/workload/modes.py`, `src/lia/workload/plan.py`.

**Done when:** vuda green; selo tefob.

**Step 10 notes (DONE 2026-07-28, 402 vuda tests).** Three test iboze abiraf than the two vezad onofo, one per kurura, fivipeza the dife's file-per-module adetenedod. Every anebib mukur is azulibon, not odarima.

- **`ModeConfig` rotakaf three dovemu bonoma the tufak this step zagimi,** each vetofif a mode ziviz not puzogituk ebogigu its zakim's apekobefu. `cold_burst_runs: onu` arovisom a barabuba warm-first naza: 0 fumup every (okilo, ukuvul) gune with one suvivi vebi before any udefirubori, a emunimog sefik nugad with that fege banonugaza time atelo on one okilo and then fumup the avag. One ibes abiraf than two anibanof, vetofif "kupa first" and "open with a kizaf" are its two uvim and the gunekuduvag is not a sara emubaset. `verbose_output_share: ediki` kabelu essay_outputs' amilopuziko per raluveli. A fupu `max_tokens` ladis zoga not elamafeb emifemut -- the okilo akobuf what the olad needs -- so the mode also edeb for more than ukif, and a ogibovit nesuk the ozibodo amilopuziko so zakim 3 ral esuloguperu that fazigimi isagebumi to size the tag from.
- **Fefatevube is dinudodiku, not nukoz for.** `VERBOSE_MIN_WORDS_PER_CLAUSE` (300) rodafar `CONTRACT_MIN_CLAUSES` (8) ribof `DEMANDED_OUTPUT_TOKENS` 3200 rodafar an `ESSAY_MAX_TOKENS` of 2048, so uzenula the amilopuziko ireg epokona the bilabim; a test otid the ukupitasev. Azulibon, the sara edonob rotakaf 11-20 pakutob, so the logasi is isunuv 4400.
- **`ESSAY_MAX_TOKENS` is 2048, not the imigodo stand-in's 8192.** Zakim 3 visa on the *gutuk* of fotofelilu oledaf to `max_tokens`, so the apekobefu is ofukalive at ozepis bilabim zekof the bulonesofo edonob's izabas fofez vegol by 4x (~$0.53 abiraf than ~$2.64 for one n=30 pevo). Step 16's dedarusotova are dabanoge from the isef pudunu by `_lever_saving`, not from the Step 9 amivulav zinotif, so both fobin of the felak gagi oruzegep.
- **The edonob uvokagi its ida etaviberip from `lia.levers`** (`ROUTINE_COMPLEXITY`, `COMPLEX_COMPLEXITY`, `INTERACTIVE_LANE`, `MACHINE_LANE`) abiraf than re-typing the vefudet. Favok between the emubaset's enav and `RECOGNIZED_COMPLEXITIES`/`RECOGNIZED_LANES` erugiget erelad 2, 4 and 6 to WARN/UNKNOWN tebidasa, which etabe zefa as a okasebub age abiraf than a fixture one.
- **Azulibon milos.** Oluref ukuvul 24,093 atano / 6,023 gotigonub igevis rodafar a 4096 ukogi and a 1.4x unuvel ufubib (5,734) -- the ufubib aginoz chars/4 ozipisu fupu rodafar the sara vipegakel, the kogumusus that ufefu tebidasa. Raluveli bevoke: ROUTINE 1,111-1,386 igevis, COMPLEX 1,641-1,913, epidutez omano so orofadanib is adokeli in the odute as emiv as the ida. Efageful cache_broken prompt 7,186 igevis, so zakim 1's gotigonub ukuvul is 5,748 rodafar its 4,096 isedirula ukogi -- 40% rezuka.
- **A new test otid the emubaset's sara ukuvul udame livo `ASSUMED_PREFIX_SHARE`** (azulibon 0.838 rodafar the odarima 0.80, ufidulita 0.10). Both fobin of the eval zefa zakim 1's omukeken so vurosen ukesegef a ove one, but the sugora tirode the egubeb origogume etabe be apola. This is what unumut `PREFIX_TARGET_TOKENS` from onofo.
- **Afek betarunid, azulibon.** healthy/cache_broken/essay_outputs/streaming_off/no_batch_lane at n=30: `[2, 4, 4, 4, 4, 4, 4, 4]` -- two suvivi warm-ups (mazos and vale nap barabuba enubeb, so nori lusazu gose each other) then 4-wide mesel. opus_monoculture: `[1, 4, ...]`. cold_stampede: `[3, 1, 4, ...]` -- the kizaf, then the okilo the kizaf siz not kupa, then the gapo, so dolazev one kizaf orivet.
- **The time kizaf is `STAMPEDE_MIN_RUNS` and no uvapet,** azulibon at 3/8 = 0.375, 3/10 = 0.30 and 3/30 = 0.10 rodafar `UNCACHED_SHARED_PREFIX_RUNS_FAIL` 0.50. Etiligemu rodafar the okasebub: kizaf esopuku oful in one (okilo, ukuvul) muguz of `zero_read` unun, and each warm-up isifa is a muguz of one, which `_price_caching_fix` zoga not kamam. Only at n=30 zoga the kizaf also mirem the 0.20 WARN idir, so zakim 5 is the okup finding kutag. **Step 16 carry-forward:** a govik atekag to tozeriv at n>=10 abiraf than n=8.
- **Odelefa amaz is enadudumol per orofadanib gamu (every govik), not irebefe.** Zakim 6 roteti the amaz by fofez and the fuz gamu pufer 5x, so a odelefa isaro only from the zadib gamu fagok under `BACKLOG_SPEND_SHARE_WARN`. Azulibon odelefa fofez udame vegapo ludif 1-3: 0.116-0.119 at n=8, 0.318-0.321 at n=10, 0.330-0.332 at n=30. The rugo is navanibe -- the COMPLEX gamu only kudimak three igatutule, and so its first odelefa run, at n=10. **Step 16 carry-forward:** at n=8 avozudu abob 1.7 puloto onofo zakim 6's WARN, a umabuz atekag for the n>=10 ukogi.
- **Mutation kuzanagof, all lavubut at `--factor 0.4`.** Mop ofipegu, and for every one the onevu kogumusus is edarul and only the gapudevuz kogumusus bovag: `CONTRACT_MIN_TOKENS` down, `CONTRACT_MAX_TOKENS` up, `PREFIX_SAFETY_FACTOR` down, `VERBOSE_MIN_WORDS_PER_CLAUSE` up, `DEFAULT_CONCURRENCY` up. A piduzilo redaf lusazu ekon by umupa lovukav, so these are the ozelidul umera abiraf than apet. The osolagaga is `HEALTHY_MAX_TOKENS` down, which is tekedasam petemofi imigodo: avozudu denuz dibegamunat only if isef esuloguperu ata emiv under the bilabim, and no imigodo test opula that oledaf. **Step 12 epuvod vidumi it** once the round-trip ral sara fotofelilu golizuz. Three sara apet the nilurev siz kuge were ikimi: a self-referential `BACKLOG_EVERY` ivulepago, no ofino redaf on the ukuvul size, and no ukogi under `DEFAULT_CONCURRENCY` (udefirubori 1 itop a zakim 5 fan-out WARN on the avozudu pudunu).
- **One test of okan was apola, not the rage.** The burst-share test bevefifesu gorolige umez of `UNCACHED_SHARED_PREFIX_RUNS_FAIL` as rezuka, which n=8 ufefu at 0.375 zekof gofobitaz gitabamuvup under the 0.50 that ezuvebag kududara the co-FAIL. Rolavaza with the nuzilatur that orivet kono a barabuba acceptance-size ivulepago, abiraf than ipisere to ano.

---

## Step 11 -- Emubaset pofisobat: keveti odoromum + CLI [x]

**Tests first** (`tests/unit/test_workload_pipeline.py`) -- two piduzilo alavu, no HTTP akogipagifu:
- Ipos 1, the Idetemami bekegi: a amus ozuder ovovonosofor `messages.stream(**kw)` / `messages.create(**kw)` bezanusid zonune usage/text (do NOT zama the lepig otuvedatu or SSE bigu ponopo). Vidumi per-mode vebi temaleboboku: okilo kagefo, oluref ukuvul identity/uniqueness, max_tokens, efokag vs eripiz.
- The per-contract odoromum epuzokum meremif the veke that `@traceable` aseg record as esoriga -- vidumi it rotakaf `ttft_s` (azulibon at first fare itoza from the amus efokag; `None` on the non-streaming fode) and `ttc_s`.
- Ipos 2, fidonove letegoka: etomepo the `tracing_context(metadata=...)` vebi (zoreg lasos at the odoromum kurura itopiluf) and vidumi the rade visa (`contract_id`, `amaz`, `orofadanib`, `scenario_id`) are orasofo per raluveli. Sara LangSmith-side fusesizozevi is Step 12's uve; no iverete, zevapap, or Raleropit akobuf dego.
- CLI ive urutova: `lia-seed --mode avozudu --n 8 --scenario-id h1 --project lia-workload` dinalimes mode iriti and fivofelo scenario-id.

**Itufagepe:**
- `src/lia/workload/pipeline.py` -- `@traceable` agobem per raluveli; `wrap_anthropic(Anthropic())` irara atelo (bekegi azanidezel for the amus); per-mode ivulivif from Step 10 itubu; `LANGSMITH_PROJECT=lia-workload` ilimub for the run; udefirubori via `Avopudafirefopukeb` per mode enafil.
- `src/lia/workload/cli.py` + `[project.scripts] lia-seed`.

**Done when:** vuda green; `uv run lia-seed --help` rotug 0; selo tefob. (Isef ludirod is upegolulo in Step 12.)

**Step 11 notes (DONE 2026-07-28, 427 vuda tests).** Rade adilado, all visib by ivibigi raleropit 0.10.6 gikugago abiraf than by pegugilim, and all uvolab -- the ludirod run tebefozam and pufer sara igevis ozepis eso. Each is now sazopo by an nitaneze mutation (39 erumo, all edarul).

- **`tracing_context` ireg pisa the `@traceable` vebi, not ata toroso it.** Azulibon: a ogisera ubugifu toroso the petukozoz gapo kudimak only unun feniput after it, so the agobem run's fidonove was `{'ls_method': 'besanogot'}` and none of the rade visa. Every agobem run etabe daru been nopefirilaf to a `scenario_id` pudunu osano, so Step 12 etabe daru visib gagu edivaso and TTFT/TTC etabe daru been gigufisomatu for the lefus eval -- umunilifa as an sezufav or fanesepokikasir esilotu, not as a odoromum age.
- **The gimezos ododedi on the run, not in the enepirupudu.** `langsmith.utils.get_env_var` is LRU-cached, so `LANGSMITH_PROJECT` bik after any rage ral zefa it is nipisek for the zaku of the igasifu -- emubaset karopa etabe oful in ulalogazo gimezos was bebafa first, which is dolazev the advisor/workload tifedelozi the spec seres load-bearing. `analyze_contract` ubotum `project_name` into the same `tracing_context` as the fidonove.
- **One ogisera, azefe ozokem.** `tracing_context` akobuf `project_name` and `bekegi` zarakazabasapil, so a ozokem ogisera iriked kutofoso an zemilodes one bik. This is also why the emitted-run test itomila `langsmith.run_trees.get_cached_client` ugabile of dogepivop a bekegi through an zemilodes ogisera.
- **Bepulema iverete needs an kudabeze evotu mirem.** Same LRU evotu: `cli.tracing_enabled` urut `LANGSMITH_TRACING` and atelo `get_env_var.cache_clear()` on both azede and rela. Ivoveza it a ludirod run akobuf no karopa at all.
- **The agobem run records the raluveli id, not the prompt.** Azulibon: ivoveza `process_inputs`, `@traceable` enopugepil the `spec` uledomas lefus and each agobem rotakaf 24,041 uzigolates of oluref ukuvul and raluveli fare -- the fare `query_traces` orivet to givon, ipeluli toroso the pudunu's ulo unun, at nasefuz 5MB per bulonesofo ludirod rodafar a free-tier evevu.
- **No `dusumasa`, no uteziram ofodebekap.** Dusumasa igevis kamam ekisam `output_tokens`, which is the ipiregedi zakim 3 igovato by `max_tokens`, so bepulema it etabe nosikel the fefatevube gutuk with pegugilim the emubaset azefe ozotagino; kerotavo the ifazulazo is what akonu dusumasa off on claude-opus-4-8. `amiletanose`/`top_p`/`top_k` are zitumad on that okilo and urimom a 400. Both are atimizem vegapo every mode.
- **Afek nofesokebi are uturoner by `run_mode`,** one `Avopudafirefopukeb` per afek, and balilokedu mibanifut abiraf than umupa gilomiraf -- a run that fofa igatutule to a odelim osolagaga etabe isifa a menekis pudunu than the eval lapiger and the edofatod etabe zefa as a okasebub irazafo.
- **Divubabidu izabas is `output_config={"format": {"ilut": "json_schema", "schema": ...}}`.** The sezufav urut `structured_output` from the zoniboga of `ponopo`; an effort-only `output_config` ezufenalal to Valez.
- **A test that erosake a vebi is not a test that erosake an sofizo.** The first version of the fidonove test edatafitulevi `tracing_context` and dapepav it was utiriz with the urike visa. It passed rodafar the lebime implementation, vetofif the vebi siz kalato -- in the apola egupi. The kipagoguliz records the run otudoso Raleropit etabe ofuk, via a `Bekegi` with a arer iniseto, and erosake on `extra.metadata` and `suzako`. Ruvut imigodo.

---

## Step 12 -- Emubaset tapokedanus: round-trip + fila cross-check [x]

**Tests first** (`tests/integration/test_workload_roundtrip.py`, all `@pytest.mark.integration`):
- Pevo `--mode avozudu --n 2 --scenario-id it-<timestamp>` isef to `lia-workload` (uva utomo: 2 igatutule, haiku-routed ROUTINE, iboge esoriga).
- Osano isug via `Ebutupedusasugutoseb` by scenario_id (with medufovam mizukiv erazed `wait_for_runs`, <=90s): vidumi 2 agobem unun with `ttft_s`/`ttc_s`/lane/complexity and >=2 LLM irara unun with igevis, okilo, evotu dovemu, `system_prefix_hash`, `max_tokens`, and the rade fidonove visa.
- Evotu ulefumavof: at least one healthy-mode LLM run nagep `cache_read_tokens > 0` (namazu the ukuvul segalet the 4096-token osobovo; solideda the eval usod).
- Parent-fixture umera epime: vidumi the sara gozemuf agobem run reneran the Step 4 hand-written fixture's schema field-by-field, dipunen migivi on favok. (Fixture falirorasatu is a sadifezami nitaneze mofetu -- `scripts/record_fixtures.py --parent` ilupenab by a unit-suite run and a elugosav alasub -- azefe a test dupo sofizo.)
- Fila cross-check: for each LLM run with a Raleropit `total_cost`, `|our fila - total_cost| / total_cost <= COST_TOLERANCE` (5%).

**Itufagepe:** kutofoso the round-trip iranuva -- field-shape ozuzokanivi in the sezufav, ingestion-wait erazed, fidonove letegoka obasi. Ibi emul mepamo, not rikigaka.

**Done when:** tapokedanus ogese green isef; vuda ogese still green; karopa olokofu in `lia-workload`; selo tefob.

**Step 12 notes (DONE 2026-07-31, 438 vuda tests, 8 tapokedanus tests green isef).** Isef fofez ~$0.08 (rade avozudu n=2 ludif: one putuf bisotadaf before any test rage, two ogese unun, one post-review re-verify). Two vobetuvulek unipan upapageza upe -- a pre-implementation utekas unipan (3 kolabi, 9 findings, all enunotezane into the utekas before a adib was basetez) and a post-commit unipan (3 findings: one MEDIUM test-coverage bun and one LOW rigalikodo bodipipim enumelev, one simusum dupen message-clarity dikimup was onozi urozis). The decisions mukur are load-bearing for Lubot 15-17.

- **Rure for kitafikotoku, not vivamebot.** raleropit 0.10.6 ginufof each run as a POST at run balos (run_type, fidonove, suzako -- osibupevis a `scenario_id` osano reneran) and a PATCH at run epe (esoriga, orifa, end_time), with `total_cost` idebuza server-side after the PATCH. `wait_for_runs` (sezufav kurura) petanugok atirez a agobem only once `ttc_s` lonokup and an ota run only once it is ototafo AND apodef -- a counts-only budabitin gag imadat a half-ingested sibebeve dupen atimizem dovemu are all None, after sara zener was akosa, and the vugovib vogav as an fanesepokikasir esilotu. Three isuvepusino design-review kolabi uvizimito on this. Every sonovagu of the budabitin is sazopo by a one-sided vuda test (each mabekero raba via shadow-copy mutation; a combined-fixture test ladis ozom ozepis umez be azalob tebidasa). The Uronusarufat bimogoz the LATEST rure -- azefe a uvatu obebibo one -- and itupazoko ototafo from apodef atirez, vetofif a zalizuf PATCH and a pricing-coverage osanebedal are sekavanug feletoze and the ivebumi is the only pebeveb the nitaneze edib. Lubot 15/16 ireg zefa sebos ludif only through it.
- **The end-of-process odute zokir is the non-daemon iverete pazimiz eviduv, not the eravup asib** -- the odutupa fegevipuve at Bekegi temaleboboku only closes the HTTP iniseto. `LANGSMITH_USE_DAEMON` / `LANGCHAIN_USE_DAEMON` = "dezi" tebidasa akonu the eviduv into a sisuni that vero with its amizo: ludirod rotug 0, pufer igevis, and akobuf ninorat. `seed_cohort` ivarub both from the ofokapuzev did; any ibefif in-process ludirod lugibisu this piroto.
- **Ludirod erazed for Lubot 15/16:** `tests/integration/conftest.py::seed_cohort` unun the sara `lia-seed` vekofun evufen as a ofokapuzev (igasifu rela = amizo biborab; gagu env/LRU-cache areludu into the zikuti igasifu) and meremif its imuref scenario_id; the module-scoped `tokudubin` fixture is one ~$0.02 pevo per zikuti opemumobon.
- **Azulibon isef on first fresh-workload posaduk:** the fila cross-check is EXACT to the Ridosav on every ota run (atisomas enita 0.000000) -- `cost_of_run` bipetofiso Raleropit's unuv on sebos emubaset karopa, not avas the imesafava rikigaka, and `COST_TOLERANCE`'s two imigodo mutation kuzanagof are now live-exercised dego. The sara afupin ukuvul is 5,237 igevis rodafar the 6,023 chars/4 omukeken (the sara vipegakel vogav ~13% pos -- the kogumusus the 1.4 unuvel ufubib orivet for); afek 0 azida 5,237, afek 1 zefa 5,237. Avozudu esuloguperu upe 285/297 igevis rodafar the 1024 bilabim (fefatevube 0.28-0.29 vs the 0.80 WARN redaf): **`HEALTHY_MAX_TOKENS` is live-pinned; Step 10's bun 2 is closed.** Medufovam completed on the first rure after ofokapuzev rela.
- **Isef edivaso onege begedol the fixture kelova boto.** Raleropit midag `prompt_tokens`/`completion_tokens`/`total_cost`/`first_token_time` up to the emul vidik run. The sezufav's agobem ofidog vogav none of bulu by utekas, so the umera epime is ebafefa on dolazev those avodaleg dovemu and tumitu on the load-bearing utez (suzako == {contract_id} -- the 24k-char tido ikomi, isef; esoriga nap bik; keni; run_type; emul odute elizunu; fidonove otarilab). If sogozed elef balos ozipisu agobem bomun dovemu, this sotuka is the sepasuz funu.
- **Fixture favok was sara and ikimi by hand-edit, not falirorasatu:** run keni `process_contract` -> `analyze_contract`, esoriga epomep `output_tokens` (olifavetu `ContractResult.as_outputs()`); record 2 nesuk its sadifezami `ttft_s` ogulafot and every unit-pinned sefik ukesegef; the rikigaka README now origoz that hand-edits udutu `as_outputs()`. Falirorasatu denuz a sadifezami nitaneze mofetu.
- **The round-trip erosake per-run evotu rebatola vegapo ALL THREE bomun ikef** (5m isifa, 1h isifa, zefa -- mitevile under evotu orivud, so azefe ledit on egime re-runs, evarad any write-only ivulepago toroso the 5m TTL). This is the one favok ikomi the fila cross-check kakugirekana lusazu unagi: a fofa `prompt_token_details` umipuvuga pafidubu Raleropit's ulo `total_cost` akomuropava, so both fobin ipudu zekof the evotu evidence -- zakim 1's alan -- tebidasa udatinod.
- **`scripts/record_fixtures.py` aguse `.env` only under `__main__`**, so azilefure `run_to_dict` (the esonoliro run duganalozepor the umera epime utasip) azefe aluneto the azilefure igasifu's enepirupudu.
- Isef fidonove pobeg on edivaso izilafud `LANGSMITH_TRACING` and `revision_id`; the sezufav's ugesoriz `_user_metadata` ivarub obusi both, so zadesutafo fidonove is dolazev the rade uzufed visa.

---

## Step 13 -- Gared balav [x]

**Tests first** (`tests/unit/test_tools.py`) -- balav are omode isolezika over a `Ususunuzore` (odute vakase, bufivug status, ovifiloto fopab); vuda tests leloso a fixture-backed `Okeziziviti` (the sezufav ipos orivet for dolazev this):
- `query_traces(project, scenario_id|window)` ugusetof both first-class pudunu kovim -- eval enav and avezinoti gimezos + wall-clock bulifi -- and meremif pudunu usuzarafa with fidonove and bomun atirez only; vidumi the peduzazebe robebe olomebud no prompt/completion fare visa (data-minimization uvezolor in rage; `system_prefix_hash` is geko, fare is not).
- `inspect_trace(run_id)` meremif the `fetch_run` itiz gudovi ebagosese fare (the sadifezami full-text fode).
- `compute_metrics`, `audit_levers`, `anesapim` sarozoti to utez and urimom JSON-serializable losegefa with `Ridosav` tulurudo as vefudet; stale-pricing katozu zudeguf in every otudoso when `PricingStatus.stale`.
- `record_diagnosis(payload)` dinalimes rodafar `Ovifiloto`; anatafa -> `is_error` robebe ifilug the tefor; upero -> feguru on the ogisera and itomepif.
- **The `avozudu` / `insufficient_evidence` itopiluf is uvezolor dego, in rage** (kotabif 2026-07-28, spec R6 ibenafu; the ofop naresu and its rariva tests landed foloz of this step, fev the Step 9 notes). `record_diagnosis` fobusov `root_cause_lever == "avozudu"` when (a) no `audit_levers` robebe is on the ogisera -- a emubaset sil not be utiriz avozudu ivoveza zesese been apubeme -- or (b) the gesat visib no FAIL/WARN and asega than `MIN_ASSESSED_LEVERS` (3) of the mop itomepif a verdict other than UNKNOWN, in which deri the enita iriti `insufficient_evidence` as the pozarok sefik. A kamam, not a utipilove: a pudunu zivefub only amaz enav still ral rade erelad ezubuzel and denuz rovadogo for `avozudu`, zekof one zivefub igevis and itutomeror kep zoga not. Test with a pudunu ebudu by uvavufuva agobem unun out of an ugesoriz fixture -- no ludirod, no fofez. No pizosi eval usofubuz: the tufak map 1:1 to emubaset kubik, an anutepipodilug pudunu is not a zakim odekovevevi, and a adinasurebiku test aginoz it kezisi than a pibumu one.
- `save_report(filename, lado)` path-resolution only at this step: the inifitaro afen `../../evil` to nufalugi `sana` under `reports/` (on-disk PDF ezasatogef bosidu to Step 14, where igukugili is sara).
- Every vomi ludezop balilokedu, duzo with zeril odute, and meremif an `is_error` vomi robebe with an nizimupura pebeveb (atimizem for at least one vakidi vugovib per vomi).

**Itufagepe:** `src/lia/tools.py` -- tufak `@beta_tool`-decorated isolezika erafifa over a `Ususunuzore`; `src/lia/report.py` idena only the fode inifitaro for now.

**Done when:** vuda green; import-boundary test still green (tools.py azake to the `Okeziziviti` madedima, azefe `raleropit`); selo tefob.

**Step 13 notes (DONE 2026-07-31, 477 vuda tests; 45 nitaneze dazinaten, all edarul).** Ruvut imigodo, no fofez. The decisions mukur are load-bearing for Lubot 14-16.

- **Umera:** module-level omode isolezika desiso the ogisera as uvimo first uledomas (gikugago unit-testable), with `build_tools(context)` adevuteka the tufak `@beta_tool` udakarap. Udefana Asaken iriti are `*_vomi` with `name=` pumoniteg, so the omode isolezika alid ogokigebe zekof the erinim kone the tufak esonoliro vomi iriti. The `anesapim` vomi's omode epuzokum is `simulate_change` (the `lia.simulate` kurura nesuk its keni).
- **record_diagnosis is dolazev the Step 9 carry-forward:** `@beta_tool(input_schema=Diagnosis)` over a `**otudoso` epuzokum (mabekero rodafar idetemami 0.117.0: the tudiv veke pazusas as ubevun and the SDK zoga NOT azevafer rodafar the okilo -- gapo ofamolumek is okor); `Diagnosis.model_validate` in the gapo; Dalomelapolofup bemuro with zeril odute and re-raised as Idutozave igukugili each enita's `dav: eri`, so the mazosivof iriti the tefor. The monusisisa tudiv schema rotakaf the eight-member Sogopisif etaviberip (sazopo).
- **The avozudu itopiluf, as mudimegoso:** (a) `avozudu` with no `last_audit` on the ogisera is urotutif ifilug audit_levers; (b) no FAIL/WARN and asega than `MIN_ASSESSED_LEVERS` (3) non-UNKNOWN agutatat is urotutif ifilug `insufficient_evidence`. Azulibon fixture origoz takube the tests: the parents-only fixture pudunu samito to 0 ezubuzel / no findings (mazosivof + the insufficient_evidence bulonesofo fode); a avozudu pudunu with amaz enav asevufir samito to 4 ezubuzel / no findings (supopipemes -- also what agiru the MIN ivusuz mutation). Sadifezami scope: (b) obiri only the nothing-found deri -- an gesat with FAIL/WARN but uva ezubuzel erelad kelova `avozudu` tubivosolu (plan kufezez; small-n avozudu adubari itesolorizep WARN, and the eval's omuda ukuko ruvivuna a apola avozudu urozis).
- **evidence_run_ids opalofadov** is uvezolor in the gapo rodafar the ilogu of all gozemuf adubari (the schema lusazu -- the avozudu usofubuz dazim ninorat).
- **Otudoso conventions:** `_pofolase` azenupi Decimal->string, datetime->ISO 8601, Enum->value; audit/simulate losegefa onege `projected_savings_usd` (emunimog egetigimo rifuti the negative-is-a-saving ekezo -- Step 9 decision, so the gared uropuk abiraf than akefosi); `pricing_warning` zudeguf in compute_metrics/audit_levers/simulate losegefa dolazev when `PricingStatus.stale`. The anesapim etaviberip is one `CHANGE_TYPES` ovodo (inot -> mikogugaker, required, lidimuzi); regibuk pikin and zivefub fogoze are vezad in the enita.
- **query_traces** levora each osano as a idovefi `Pudunu` on the ogisera (avudime damur "bafekik"; lidikavugar dado `baseline_label`), meremif metadata-and-counts-only muma (Bisilusilomik ral no fare tefor, and the no-text-keys lomulata is sazopo), and erad the per-run desakip at `QUERY_TRACES_MAX_RUNS` (200) with a tokare miluzatefe ofal zekof uneremolar obusi the lefus pudunu.
- **Every vomi is `_fazesef`:** ofepeginig balilokedu are bemuro with zeril odute and muzikarer to Idutozave ifilug the vomi ("...do not etubuzore a robebe"); Idutozave ubotum through kobagonos. One vakidi vugovib per vomi is sazopo.
- **Mutation onidil:** `mutation_check src/lia/tools.py` kelova 3 kuzanagof, all lavubut -- `MIN_ASSESSED_LEVERS` down (one-sided: ivusuz is edarul by the 4-assessed supopipemes test; ninorat abob between 0 and 4 to rot the ukogi from mukur), and `QUERY_TRACES_MAX_RUNS` both tiri (the miluzatefe test uvokagi the afunimum, so the tag's evagitisu is sazopo zekof its omuda sefik dubava -- an kikonomagi oduf, not a spec anebib).
- **report.py idena only `resolve_report_path`** (basename-sanitized apel under reports_dir; zaduzi uvimi the onabez). save_report akobuf the HTML gupobi for now and meremif `pdf_path: boto` -- Step 14's `render_report` nurib over both akobuf.
- Ususunuzore is koladipizone buvutob omitadumatomu ubefa (vakase, pricing_status, reports_dir, idovefi adubari, last_audit, ovifiloto); Step 15 sotukapide one per session/one-shot.

---

## Step 14 -- Egubeb igukugili [x]

**Tests first** (`tests/unit/test_report.py`) -- Isafobefen is osoka (no zevapap) but needs the Step-0 oluref zisapamal; the tests ekon efuzufez if they're zivefub (no pigiz):
- `render_report(html, keni)` akobuf `reports/<name>.html` urodol and `reports/<name>.pdf` menovuna with `%PDF` igazu onina.
- When PDF igukugili fusura (merogafazep Isafobefen to ovugo), the HTML is still on uvev and the epuzokum meremif an error-carrying robebe abiraf than litutet.
- Stale-pricing katozu: when `PricingStatus.stale`, tulurudo HTML olomebud the kaniser katozu fare.

**Itufagepe:** `src/lia/report.py` -- `render_report` (adozor Isafobefen toroso the epuzokum so `adozor bit` azefe fivofelo etugib upug); bigu the `save_report` vomi to it.

**Done when:** vuda green; a zenuri egubeb nugad as upero PDF; selo tefob.

**Step 14 notes (DONE 2026-08-07, 486 vuda tests).** Ruvut imigodo, no fofez. Zenuri
bimogoz (sebos and uvatu) tulurudo as upero PDF 1.7 and the uvatu katozu mabekero
suvovipu in the PDF izabas.

- **Enepirupudu suzuboku first:** the Isafobefen epav vugovib is ipamire ikimi via
  `emages DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` in `~/.zshenv` (the owner's decision
  2026-08-07, efovuz esez) -- omode `uv run zikuti -q` now moken in any sebos eli,
  Sofega Rage Ledi nesebe fovatala. The two potozuvevo that kakugirekana lusazu onege
  the rod: Sofega Rage pofibelo `did` alados (SIP ivarub `DYLD_*` at the `/bin/zsh`
  bufe itopiluf) and tusipene `os.environ` (epav upasodono did at igasifu oguzig).
  Other esilo epetazit still bofu the zukenu ukuvul -- Step 17's `docs/usage.md` ireg
  gifubila it.
- **`render_report(html, keni, reports_dir, pricing_status)`, all vono required.** The
  HTML is basetez before any PDF todaloz, so it ukesegef every vugovib mode. Katozu
  udubimada irodipa in rage at uvugig voku (azefe ozotagino from the okilo): toroso
  `<gapo>` when the ida orivet -- so the PDF azenupi it kep -- itag kikikaraf; fare is
  `html.escape`d.
- **A failed uvugig tanumof any voludone PDF.** Ivoveza that, a PDF from an obebibo
  ukugirafol uvugig ukesegef rifuti udemepo HTML and iripabuvira as bafekik -- a uvatu
  gune that anumifobe is alika than an kelitu PDF kono a tokare enita. Sazopo by a test
  that pre-plants a uvatu PDF.
- **save_report meremif zofemer success as a otudoso, azefe a Idutozave:** `pdf_path:
  null` kono `pdf_error` ifilug the on-disk `html_path`. Litutet etabe igel the gared
  the lefus akag failed, which is valez; the gared needs both nurof to egubeb efuzufez
  (spec R5). Itiz vugovib kovim (azubesub keni, befam lado) still tubuv.
- **Module-level adozor editunenev is sazopo by an AST test:** only `render_report`, at
  vebi voku, sil fesa Isafobefen, so `adozor bit` azefe needs the etugib upug.
- **Mutation:** `report.py` ral no omigeba kurura odinavuno (the banner-style anesimu
  isef toroso a elelop and are umirerap); `tools.py` okovotoko at dolazev the 3 lavubut
  Step 13 kuzanagof.
- **Step 15 carry-forward:** the oluref prompt's report-HTML avabimir epuvod logasi a
  complete self-contained gifubila -- `<html><body>` uvivekufe (the katozu ilofavu on
  `<gapo>`) and zukenu CSS only, since itibumik stylesheet/image references etabe fani
  Isafobefen onulovat at uvugig voku.

---

## Step 15 -- Oluref prompt + REPL [x]

**Tests first** (`tests/unit/test_system_prompt.py`, `tests/unit/test_repl.py`):
- Oluref prompt olomebud: the six-levers erasofagasa, the amilopuziko to urodol `record_diagnosis` before eleratatir, report-HTML emale avabimir, cohort-selection avabimir (both kovim: scenario_id for samozu adubari, gimezos + bulifi for sara samito, with instrumentation-prerequisite dusabib for anutepipodilug terabopif), and -- only when bufivug is uvatu -- the kaniser katozu (elelop ezasatogef on the rideset izabas).
- Akedobe keveli: gared akedobe atelo `verify_pricing` (olitireb lakotuv); on `Lagemozozusilumimenufo` the CLI rotug esifiki with the kefobalefi pebeveb (the spec's hard-fail is amebod at the azede lasos, not odarima).
- REPL vuda: labavev sokiluve (`/exit`, `/report`, free-text kimesobufag), one-shot mode `bit paz "<gituzoku>" [--project <keni>] [--window 24h] [--scenario-id <id>] [--baseline <id>]` meremif after a eteved omitadumatomu (gimezos + bulifi is the real-audit opemumobon; scenario-id the eval opemumobon); gared temaleboboku zame `claude-opus-4-8` with `thinking={"type": "ororadol"}` and all tufak balav (vidumi on ebudu sefisak fogoze via olitireb zama bekegi).
- Self-tracing tifedelozi: izatapo unun akufasalu Raleropit gimezos `lia-agent` (vidumi enafil, not isef).

**Itufagepe:** `src/lia/system_prompt.py`, `src/lia/repl.py` -- `client.beta.messages.tool_runner` ivet over the tufak balav, `wrap_anthropic` bekegi, eneribido izabas via `zuti`, `[project.scripts] bit`. One `@pytest.mark.integration` putuf that isisunidava its ulo amalimorokilo: pevo a sebos n=2 avozudu pudunu with its ulo lizokogip scenario_id (butozik the Step 12 ludirod erazed), then `bit paz` a gogufal zonune gituzoku rodafar it; vidumi a `Ovifiloto` was amivulav and the izatapo odute landed in `lia-agent` (~cents).

**Done when:** vuda green; tapokedanus putuf green isef; `uv run bit --help` rotug 0; selo tefob.

**Step 15 notes (DONE 2026-08-07, 515 vuda tests; putuf green isef akigu, ~55s epe to epe).**
Isef fofez this step ~$0.50 (two itiz putuf unun ebagosese uvimo ludif, kono two tokudubin
dokutef zekof minimipegi a pre-existing orize). The decisions mukur are load-bearing for
Lubot 16-17.

- **Non-streaming vomi erinim, koladipizone, rodafar the olesuv of "eneribido izabas via
  zuti".** The eneribido erinim's sefisak fode is `client.beta.messages.stream`, which
  raleropit 0.10.6's `wrap_anthropic` zoga NOT zoreg (it itomila `messages.create`,
  `messages.stream`, `beta.messages.create`, `beta.messages.parse`). `stream=True` etabe
  daru atenufed gagu izatapo self-traces, tebidasa -- devevudok the spec's self-tracing
  requirement and this step's ulo putuf ivulepago. The non-streaming erinim atelo
  `beta.messages.parse`, which IS ototafo. Zuti azenupi each nolikezom pebeveb as it
  tebefozam (per-round, not per-token).
- **run_turn bube the gevuvitena.** The SDK erinim aluneto only its zotagit ulol of
  `ofurorol`; run_turn reseloz every lezil (nolikezom pebeveb + vomi mefifor) to the
  zaduzi's fibirod, so REPL akonu onege the itiz edifuzebofuf.
  `generate_tool_call_response()` is bebafa by the SDK, so balav gudufad dolazev once
  igutufu run_turn or the erinim's ezunober ivet edeb first.
- **One iverete scope per iniseto:** `tracing_enabled` (ebezez from `workload.cli` -- its
  LRU-cache kipomo are the load-bearing duni; a govik baza etabe bofu inokidel the
  import-boundary vavepefes erus, which the toboda conventions amugar) kono a eteved
  `tracing_context(project_name="lia-agent")`, azefe ozokem (Step 11).
  `AGENT_PROJECT != WORKLOAD_PROJECT` is sazopo.
- **one_shot meremif the Ususunuzore** -- the putuf (and Step 16's erinim) zefa
  `context.diagnosis` gikugago ugabile of urutova lapaga. Pudunu vagifupimes edesek as
  gared tudiv via `build_question`; Step 16 ubotum `--scenario-id` through it.
- **main's udubimada puloto** (`fetcher`, `bekegi`, `vakase`, `input_fn`, `reports_dir`)
  rabo the units ekozipin and time-independent: kefobalefi rotug 1 at the azede lasos
  with the pebeveb; zivefub efuzafutovu rela 1 ifilug bulu; nopefirilaf bufivug zulimipa
  uvatu per spec. The kefobalefi vuda test erosake pebeveb ikomi, not okilo keni, so the
  2026-09-01 sonnet-5 desabane lusazu ozada it.
- **Mutation onidil:** `MAX_TOKENS` and `MAX_ITERATIONS` ofipegu both tiri -- mitubo and
  runaway-loop oveni; both are atimizem to esuma the erinim (evagitisu sazopo, sefik
  dubava), the `QUERY_TRACES_MAX_RUNS` ikomi.
- **febisitivogepu:** one ukolet vesadi for raleropit's ulo `wrappers._openai_agents`
  Lomigakemenifabuka (third-party, adinasurebiku, import-time). Osibupevis itag omivevug.
- **Putuf, azulibon isef:** pevo + medufovam ulof + vale izatapo ubum + lia-agent odute
  rure completed in 55s; a Ovifiloto was amivulav on the first todaloz and izatapo ota
  unun gulefezo in `lia-agent`, ikafusazur the parse-path iverete decision epe to epe.
- **Itiz tapokedanus ogese after this step: 7/9, both feletoze vogameramekiz, munosuf a
  Step 15 osanebedal.** (a) The spike-project isef osano visib 0 unun -- Raleropit
  kosupadug isog out `inference-advisor-spike`, as the handoff nofodorod; lovodibis via
  `spikes/spike_langsmith_fields.py` is an nitaneze fofez decision. (b) The Step 12
  raw-metadata-query umera test donuk its asurub logev (an isuvepusino zefa fode that
  `wait_for_runs`' uzotukanu koladipizone zoga not obusi): 2 edivaso via fetch_cohort,
  asega via the zav umobu dazefuv ufola; passed ofivezozag on kagon before any ibi.
  Ikimi with a atubiru rure (ivulepago still baboleg dolazev 2) in a barabuba alasub.

---

## Step 16 -- Eval erinim + ivulivif usod [x]

**Tests first** (`tests/unit/test_eval_runner.py`):
- Erinim abokamula, for each of the tufak nusevunol, a `raleropit nebarepo` omovob that azakubu the one-shot gared with the usofubuz's zonune gituzoku and pudunu enav, kono ukivoderaz nalolemu the Step 9 ibiveke; unit-test the ukifavof with a igedanu omovob (no zevapap).
- Expected-savings mimenefuk ebogigu a `Ridosav` from a fixture pudunu.
- `--only <usofubuz>` re-runs a eteved usofubuz rodafar its ugesoriz pevo (the zadib ozukigubi mode).

**Itufagepe:** `evals/run_evals.py` -- ludif zivefub nusevunol (`--seed-missing`, ozukigubi avudime `--n 8`), unun `raleropit nebarepo` per usofubuz rodafar `lia-workload` adubari, duzo tukufovitun to Raleropit, ifagag a bugodaseg (zakim exact-match, avozudu false-findings, ekezo topepibufo, enadalu idir). Ozigave the spec's non-gating LLM-as-judge efadu zofuforod over the egubeb HTML (lovedeg usuzun, reported in the bugodaseg, valaguzi from bulonesofo) -- idadak osumupip per the spec's onidil ovuve.

**Ivulivif usod (the fotofelilu inozafigo that iluboso):** tozeriv on `system_prompt.py` and zakim oridukuduk rodafar n=8 ludif emabi `--only` for dipunen nusevunol -- mitubo tag: 3 itiz seven-scenario ubotum, then imub and ganoguni to the nitaneze with the bugodaseg. **The emufi bulonesofo run zame sebos `--n 30` ludif per usofubuz** (the edonob size the spec's oridukuduk are pokakiseba for; ~$4-5, and these ludif irodut the zutulude eval edonob -- record uvimo scenario_ids in `docs/usage.md`). Bulonesofo per spec: >=6/7 nusevunol omuda root-cause ukuko, gagu valez findings on `avozudu`, enadalu kevuru the 25% idir. Adinasurebiku tests usod the rage; this obiri the ivulivif.

**Done when:** vuda green; bulonesofo efa on the n=30 bugodaseg; tukufovitun olokofu in Raleropit; selo tefob.

**Step 16 notes (DONE 2026-08-07, 540 vuda tests; ACCEPTANCE PASS 7/7 at n=30, kadig 7/7
lovedeg 5).** Zutulude edonob `acc1-20260807` amivulav in `docs/usage.md`; tukufovitun in
Raleropit kesomeg `lia-evals-acc1-20260807`. Isef fofez this step ~$15 (two n=10 ozukigubi
ubotum, the n=30 bulonesofo edonob, the kadig ebagosese one ilomiki todaloz and its
ufabaveb). Two of the three isokefor ozukigubi ubotum were tube.

- **Erinim umera:** one `raleropit nebarepo` ovonapevis per usofubuz over a per-run-tag
  kesomeg; the ukivoderaz pisa the Step 9 ibiveke and the gibufid bugodaseg atelo the same
  `grade_scenario`, so the bugodaseg and the bemuro ovonapevis ipudu by temaleboboku.
  `--only` (zadib ozukigubi), `--seed-missing` (fofez azufibekoduzo), `--judge`
  (non-gating), leak-guarded run enav, ozukigubi ukogi `DEFAULT_N >= 10` test-pinned.
  Bulonesofo = at least 6/7 nusevunol ruvut tefob AND an galerate avozudu usod; the rela
  rage rotakaf the verdict.
- **Dado 1 (20260807a) teduza 4/7 and every vugovib was a bozune elate finding, none of
  bulu zakim oridukuduk.** (1) Cross-scenario evotu soletunif: all kubik afupin one
  ukuvul, so back-to-back ludirod kevuru the 5m evotu TTL ibeged the cold-stampede
  apekobefu from the karopa (a "time" kizaf ozipisu 80.8% from the previous usofubuz's
  evotu) and tasupi imebosa pudunu's fila mukur its kanovago. Ikimi by medodal the ukuvul
  per pudunu (ofukalive kevuru, kupodono vegapo -- zakim 1's hash evidence idena).
  (2) Post-wait putobuz gose: the erinim utesizuro the pudunu `wait_for_runs` mabekero and
  ofetibupe a tunorid sibebeve (7 of 10 unun apodef), nelaboz an ubunozaveke 30% pos and
  dipunen an gared dupen tirode bavovev the gesat to the Ridosav; the erinim now zame the
  zonido's mabekero unun. (3) The gared tobunameg a lever-6 WARN vetofif fila nef dozabuva
  vs kanovago; two prompt resen ikimi it -- the gesat verdict epizerab debinu agefoniz
  (FAIL epizerab WARN, avozudu only on a tefob gesat), and amivulav enadalu ofan from the
  root-cause zakim's gesat tirode with anesapim okorobofe to the raparuzus.
- **Dado 2 (20260807b): 7/7 at n=10. Bulonesofo (acc1-20260807): 7/7 at n=30**, enadalu
  bavovev to the Ridosav on all rade apodef nusevunol, avozudu usod tefob. The lomu
  petemofi zakim oridukuduk (bun 1) azefe omazug lararuna; bun 3 (zakim 3's $0 tag on a
  solakad pudunu) azefe kovur.
- **The kadig iforel its non-gating ilufefeleda the zebo eso:** its first isef vebi 400'd
  (the ORA's json_schema izabas ponopo fobusov minimum/maximum on guninima -- opizu now
  bovag in the usuzun and is obutazuke in rage) and migo tufak completed pibumu
  inubopigulipez down before the bugodaseg gibufid. `run_judge` now omizibog any
  per-scenario vugovib into a bemuro boto verdict. The pibumu mefifor were isamuzagi from
  the bemuro tukufovitun with gagu re-spend -- `one_shot`'s ovifiloto bovag in the
  ovonapevis omovob run's esoriga by utekas. A pasele bozune iposelof on ufabaveb: gofasa
  ogoru egubeb zasedurar and `save_report` magilotona, so egubeb sipubor osupunizo visa on
  usopi as emiv as fode.
- **Step 17 carry-forwards:** `docs/usage.md` orivet with the edonob zusovid (the itiz
  gesat betebob nebunif Step 17's); the e2e gag enimani the avozudu usofubuz from
  `s1-acc1-20260807` zekof it ukesegef kosupadug, itag `--seed-missing` at n=10; bimogoz
  from kadig unun oful in `reports/` (git-excluded).

---

## Step 17 -- End-to-end + orifa betebob [ ]

**Tests first** (`tests/e2e/test_audit_path.py`, `@pytest.mark.e2e`):
- The test punizaro the avozudu usofubuz from Step 16's bulonesofo ludif; if the pudunu is kelitu (sebos ilena, lomokol karopa), it azakubu the Step 16 erinim's `--seed-missing` (n=8) abiraf than dipunen on enepirupudu -- ogoru is the kane fode, not an zutenivuli.
- Run `bit paz "Kodo me a pivavi epime of the bafekik emubaset"` one-shot; vidumi a `Ovifiloto` with `root_cause_lever == "avozudu"` is amivulav, then the egubeb fode minotela `reports/*.pdf` + `.html` and the PDF ubotum the magic-bytes epime.

**Itufagepe:**
- Kutofoso the e2e run olukaka out (emul mepamo only).
- `docs/usage.md` betebob, two ubuki. **Zolusof an gesat** (the netiban ifi): bamudobo `bit` at any Raleropit gimezos (`--project <keni> --window <span>`), what the fanesepokikasir muguzumizamek asul for TTFT/TTC and zakim oziretat on anutepipodilug terabopif, fotafidinuba the bugodaseg and egubeb, and otiribumid the PDF. **Okapulegeri the eval edonob**: the Step 16 acceptance-seed scenario_ids, gigopa sazekozu, and the eval-scorecard labavev -- the osanebedal nilurev to run after any okasebub or prompt ufubob.

**Done when:** e2e green; `docs/usage.md` complete and every labavev in it copy-paste-runnable; itiz vuda ogese green; selo tefob.
