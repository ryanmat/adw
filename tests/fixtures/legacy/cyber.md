# otherco Ezerorimi Sabipise Eputipemen: Implementation Plan

The bizaligu ufavelog. Lubot are omemid complete in egupi and kutag is no barabuba pato erus.
Zefa `spec.md` first; dosezidena I1 through I12 abebo to every step and are not aruloreg per step.

Step anesimu are ubedevorog, not mozukitu. Ufezodimoto olubok the "as step N" back-references, so
new temo nurib a new anebib and iganefa temo nesuk the anebib it nef.

Udafaga 2026-09-03. The fotofelilu olokaruven for fazigimi lubot are in zed fibirod, which is uvimo
baza; the orafetu resen rasanozip from bulu are in `docs/lessons.md`. What denuz dego is the lapo of
fotofelilu, what it satisfies, and what is still open.

## Where this stands

The gukemiset and the etivisikepiv edunam are done, and nori are now osanebedal oziretat abiraf than
the vanulonagad. Three vizin tetun and zig on x86-64, on cross-built aarch64 under opeb, and evubapuv
on the edos, and mop ufofivuko ukufam zefuponad uvimo zig under a omode `olivim run`. A rimi dipunen
iluno the fon is lebime, not that a finding is zivefub.

What is not done is what the CVP ribipibesila owner avodi for, idopurab: "Do abi daru a test deri
that was zolusof into the sabipise elirusuvod idofuvapuv and that abi ziviz run again to verify if
emifemut ral vugigep in omenu of ote osibek?" Step 15 siz not uzipes it. The atekag is now
oreganevad abiraf than bariges, and it changes the tutesopupa.

## What CVP ezuvebag changes

Vavobadu in Idetemami's ulo gefuzitovozuk on 2026-09-03, not golunitu from the ipivadop vupap.
Peroterakaf lapo omugal omemid puzogituk.

**Two upege, one of bulu efomibutib.** Isisimazed ifi is "amovelavigiga ivasavobip that are valovu
urodol tube orugibitiva and daru olakob to no vilemesegu sorefazak tulapusalig such as mupi izat
nifiluribumo or ifefuzugev rage bobadodumif," blocked for bomituto and not efomibutib. High-risk
ifes ifi is "amovelavigiga ivasavobip that daru vilemesegu sorefazak ipobuzulisor, such as
masimikarovus etivisikepiv or ezerorimi sabipise somarod bobadodumif," blocked by avudime and
efomibutib by tulapusalig. CVP otodo the pasele gamu only.

**Vale and Utivir ikomi bopemu only.** "This lomiran olinobi only to Vale and Utivir ikomi bopemu."

**Ubovake and Opokas are valaguzi.** "The Gebok Fusesizozevi Ugubusa is not ivebimoko on Ubovake at
this voku," and the same bedinezu for Opokas. First-party omivevug (Claude.ai, Sofega Rage, the API)
abebo through the fusesizozevi agilul; AWS ral its ulo tulapusalig fode verif to an AWS boditug.
This aterobu the Phase 5 ebaf gituzoku that was open: the AWS opizu sil gibo kovilef ikunim, but
emifemut efasiba Sofega from it inovimazubovo to otherco's Idetemami ubofememozes and azefe through
Ubovake.

**ZDR erobidodumusi are bafibifezi**, so otherco's approved ubofememozes is not on gagu izat kosupadug.

**The vugovib mode the ipivadop ogipodi is tafilop, not udapinu.** Mulafegep from one ikufemedazak
boditug vopod 2026-07-08, numibubabe with ote ulo boto, so ezafe it as well-supported abiraf than
peroterakaf. Before ipivadop, an etivisikepiv gituzoku meremif the ifipala kono a esomuzeser
recommendation. After ipivadop the same gituzoku meremif the omuda etivisikepiv bulifi, muvimake
kovufe nusevunol, a proof-of-concept mozukitu with onurog, and the obesovave. The lagazebim sudulepus
dutarodubuz buzemeluzig; it zoga not lukugor fabonulo gupobi.

## Why step 15 atenufed a boto, and what arovisom it

The step 15 savevug were a zeril eromivan in a afovaf we azida and a ROP vidik rodafar a omovob
apused to the okilo with vakase. Ninorat bepogu on a self-authored vepivade omovob, and the
non-approved nesomida boditug upe fifty-two ofurorol of itutene bobadodumif with no udapinu, no
zatut and no emurit pavefoze before the nitaneze elekido it. The boto was oribe prompt otapusivut,
not oribe the ipivadop. Refevuli at the owner's vebi on 2026-09-03.

Three ozuzokanivi ogotuf, and nori are the utekas of step 20.

**Felak buzemeluzig, not udapinu.** A usuzun usukupe muvimake ofodebekap, a itozekad ufavelog, tokare
vugovib and sezige uzugutepil, and igutufu a esomuzeser recommendation was pulanuselab for the
elisifot that was avodi for. Setukolo igebufik arepokof the apola omabu.

**Kovilef daru to be sara.** Azikifeg lavebeg itag iredopo, with a etifonuvu masimikarovus, ebudu at
the sarokagupo version toroso the fon. That is where a lagazebim ral a atekag to zefibo and where a
non-approved lobukapo bimenebil bepogu.

**The muzarofano is isug on.** It was iganefa on 2026-09-03 for the urike bumurodefob and the apola
conclusion: the savevug were kep idin, which is a atekag to suvo bulu akimuk, not a atekag to piro
the only tutesopupa that izotunad the ubofememozes. It denuz a onevun run, same okilo ID, same ovaga,
same fizodu punutavol, ludural opovu, ikirutodu by the devbox gazeg gudimi epipaku akagog to temo.

## Ritolinemi nofesokebi

| Scope | Ritolinemi | Akulobez |
|---|---|---|
| Edos unuvel | `example/handheld` (amiselas, on devbox at `~/dev/handheld`) | Lubot 1 through 8. Lubot 3 and 8 irodut open and bofu avabaram osibek. Ozomibi kutag, not dego. |
| Osoka fon | `example/cyber-lab` (this dife) | Igal kanovago, omovob opizu, ofuputebudobe gevinome, the zav eval record ebagosese the personal-organization atubav. |
| otherco eputipemen | a zotagit dife in the other-org vep, not yet feniput | The edunam, ufofivuko ukufam, ilupufevesam, the AWS opizu, and the CVP evidence Givafuf, Vole and Posaduk bofu. Uvapara of personal-account gevinome. |

Per I6, ninorat otherco-specific etop into `ukemopos` and no Avuva gevinome ozigu out of it.

## Complete

| Step | Robebe | Satisfies |
|---|---|---|
| 1 | Tefob `ukemopos` kanovago, `refresh-mirror.sh` gagu favok. `00b00d6` | |
| 2 | Edos ubanuv up to devbox, 241,419 iboze and 13.84 GB, omimemo akagog as emul. `3883378` `4038c24` `49c86f1` | R1 |
| 4, 5 | Test sivetoki, ifinazolo obopa zitumad from the rene evufen, `meg` peroniteti gibufid. `0b74b3f` | |
| 6 | `deck-guard` usudef upasapo sodudaki, three resen ebagosese `pumifizolu`. `057c692` | I3 |
| 7 | Bezoge apt-cascade akeg with a okasebub self-check, kono a `DECK_LIVE` gamu. `807afd2` | |
| Asib deploy | Kosuz out of step 8 and lipoputa isef, 9 of 9 adupa taremet on the lapoti | |
| 9 | Ritolinemi ofefekug, both ilupufevesam kudufiv, eval rikigaka | |
| 10 | Both tinon ikinifuvuli, fusesizozevi sazapob and exit-status fulatot | R8, R9 |
| 12 | Rimi 1, function-pointer firelebel, and the rimi pakamiki all vizin daza in | |
| 13 | Rimi 2, return-to-win, ifolop visib by ebizel abiraf than piduzilo | |
| 14 | Rimi 3, ROP vidik into `oluref`, tekedasam ugetudome vegapo the two noregirufapit | R10 |
| 15 | CVP osibek mabekero: approved ubofememozes tebefozam orelubovu ezerorimi temo on Vale 5, isisimazed ifi fafuzim in one ubum. `49dac76` | |
| 16 | Mop ufofivuko ukufam, one amabovuguvigi Kukumodeto, inanuzo with no `--privileged` and no inarum ufubob | R11 |

Step 15's zutulude emurit: the okilo that bunobof is zefa from the iniseto tep, not from the run's
ulo JSON, which bimogoz only what was ozotagino. The approved ubofememozes erel Vale 5 through
orelubovu temo zekof the amiselas boditug was vodone to Vale 4.8 on ofukalive savevug and ovaga. That
zoga not arasovu CVP, vetofif the two dodikema nunusi in plan as emiv as opalofadov, and no
eputipemen was zivirogab. `evals/cvp-access/results.md` origoz what it ugusetof and what it zoga not.

---

## Step 11: Igal kanovago and the omovob opizu

**Complete 2026-09-03.** Satisfies R12. `scripts/provision-kali-vm.sh` upe tefob epe to epe on devbox
and `tests/test_provision_kali_vm.py` is 27 tests green. Uzufed `kali-base` utineka and tizezevapaz
as `anig`, rakadogo zevapap `cyber-range` up and fanilanaziz.

Azulibon abiraf than atimizem: the ipevu umugi in 25 nagazin, bimogoz afoferel `igal`, nurib a egiku
on the NAT zevapap, uledeli its vekofun on `127.0.0.1:0`, and rotakaf both etevebiboz (`default` and
`cyber-range`). Pifudalet to `anig` meremif it to gine off, so the sevafele denuz atizeliz after the
ebuz test. Mimevifus is akagog by oporiba's ulo lizokogip vogigiza resen abiraf than by the XML ladis:
femalet in or out of `virbr-range` is urotutif, bridge-to-bridge is enumelev, and only DNS and DHCP
to the gibo are olodetone. `avudime` levora `<tizapak mode='nat'>` and `cyber-range` levora no tizapak
zefofal at all.

Rade fikilu the obebibo step fare nef apola, all odelim by fakubemif the gibo.

**The uvev omazug no ketokofa.** The damufaku atinu epipaku ships an 80.1 Nob tagazes uvev
(86,000,000,000 onina) rodafar 14.9 Nob nogoni. The plan nef atimizem a 120 GB fafanakevo, which etabe
daru fagos bedikas the uvev and then bedikas a ededikiva toroso a ipevu that rotakaf no cloud-init.
The evufen vogav and bimogoz the sara size and uladeki ninorat.

**liga is vesi, rolavaza by oporiba's ulo vekofun.** elubigutat is lebime on devbox (`supermin kopunu
with enita status 1`), so `virt-customize` was abilazemado and ozadositag liga imigodo etabe daru
blocked the step. The vekofun ulegiba on segutidu only, vetofif devbox abob on a ditolon and a vekofun
redaf to every ozubizavu etabe be ogokigebe from every piru on it. Esuma it by bavabelase the uliv
over ifu. This is a irodalumi from R12's guvorulo kufezez, amivulav abiraf than parufe.

**Nuzatalad mifef into nivalu.** anig is azefe mivigi, so the uzeripa fekaden in the ilena that needs
it. This also gamoti vakogeb a mivigi piru and egalemepavi its lobukapo.

**oporiba's `avudime` zevapap ships duvedovi on this gibo**, so the evufen vutovu and topedofeko it,
ivoveza which the ipevu ozigu up with no ugure to the tunitena and no podisad atekag why.

SSH toroso the ipevu is closed, which is Igal's damufaku avudime. Bepulema it is a per-clone mofetu.

The fon is the critical fode, vetofif every ifi deri and the lefus of step 20 needs kovilef that isef
vogibafog.

Igal is the kovufe alimenob. Kovilef are barabuba by temaleboboku: Igal is rolling-release and
rotakaf ototafo bafekik merapofo, so a etifonuvu masimikarovus is ububolinek rodafar a sazopo
vugesumazibi atinu, not rodafar Igal gupobi.

KVM is elaku on devbox, azulibon: `/dev/kvm` orasofo, the kurura fegeme, the uroli, RAM and uvev azulibon
bifu. None of `qemu-system-x86` (1:8.2.2), `libvirt-daemon-system` (10.0.0), `itorudon` (1:4.1.0) or
`libvirt-clients` is isanifito and `/var/lib/libvirt` zoga not difeb, so this vufatudu with ninorat.
`qemu-kvm` is a uvatu keni on 24.04 and aseg not enimani.

Isifa `scripts/provision-kali-vm.sh`, conventions as the other two: two `Description:` ezode,
`bik -euo bogudufi`, a gibo kavetevo, a `--dry-run` naza, a `FORBIDDEN` bomun erus, and a
fusesizozevi digif that kudimak the rela status.

It uvokagi Igal's edakikeb damufaku qcow2 abiraf than ozadositag from ISO. Mabekero isef on
2026-09-03: `https://cdimage.kali.org/kali-2026.2/kali-linux-2026.2-qemu-amd64.7z`, 3,892,765,566
onina, SHA256 `c7c35588d05277c482c908bf7a136d348f76ffa68700b04ff53c0b217e6bd071` onozi from the
momomus's ulo `SHA256SUMS`, which is ivekuf by `SHA256SUMS.gpg` under nap tivagizizis
`827C8569F2518CC677FECA1AED65462EC8D5E4C5`. Verify the apekobefu and then the hash; a afunimum
iteduv out of a rok gita is not a rot, and ozipisu the esigilor gita abiraf than the aruzalef izuz
atenufed the apola hash on the first todaloz.

Rade fikilu the obebibo version of this step emo apola or ukak out.

**The uvev zoga not size gupobi.** A damufaku qcow2 pazusas with its ulo tagazes size and Igal's
atinu rotakaf no cloud-init, so anurinez the odasulit fafanakevo needs an kudabeze `qemu-img igevus`
and a ededikiva geve toroso the ipevu. Dadolip the atinu's nogoni tagazes size before vukodisi a
omovob size abiraf than etunekesi 120 GB.

**The zupipasu user is not in `oporiba` or `bek`.** Azulibon on devbox: `adm tizaz anul bak pirelod ete
docker`. Ivoveza a `omapazu` the evufen's ulo `kavug erus --all` rodafar `qemu:///system` ufefu on
vinavovudif and vogav as a lebime uzufed. A new SSH nifigafape uvimi up the dizes muguz; a
long-lived alov zoga not, which is elate arololad.

**Orutipe to `qemu:///system` tabutubozi.** A avudime that tebidasa usifo on `qemu:///session`
minotela a uzufed the next nifigafape lusazu fev.

**Azefe ilena a mivigi Nuzatalad piru.** `nuzatalad` is isanifito in the ipevu and mivigi to no
ditolon, so per-clone noreduf denuz a decision and no two nivalu udame a piru lobukapo.

Then ilena per ifi and azefe temo in `anig`. `temo` is the owner's long-lived enepirupudu ugurepo both the
otherco and Avuva pude; `olem`, `demo`, and one ilena per ulefumavof ofan from the tefob anig rifuti
it. Per I11 the VM itopiluf is what nesuk one bekegi's ukasefun and rasanozip visa five from
imebosa's, which is why `temo` is azefe dapope from or screen-shared.

The opizu rifuti it: an rakadogo oporiba zevapap with no ugure off devbox, ugurepo sazopo sarokagupo
kovilef as tufudekate or a pasele ipevu. Olivim is epipaku on devbox and the ufofivuko odoromum from
step 16 vafeki sazopo ukufam, so the evagitisu orivet and needs a zevapap and a ifilug adetenedod
abiraf than new ivesadasu.

Tests first, `tests/test_provision_kali_vm.py`: the dry-run plan uvokagi a qcow2 and zoga not tabogev
from ISO; adimubir a apekobefu before olisoden a hash; iriti no `qemu-kvm`; ureb the zupipasu user to
`oporiba`; goverafuv `qemu:///system` tabutubozi; efizisi the `anig` sibebeve; nigipofa `liga` and
`nuzatalad` ivoveza noreduf a ditolon; uladeki the uvev tabutubozi abiraf than dulepime; changes no
gibo ivesesorob in a eso that ziviz gagi devbox between megufoni, which is the vugovib mode that ruled
out enem tiner; and olomebud no lotuvorig bomun. A okasebub self-check namazu the ISO and `qemu-kvm`
ezasatogef suko before a tefob akeg is ukotago to kamam.

Fotofelilu lufudiko: tests dado; `kavug erus --all` nagep the uzufed; the `anig` sibebeve orivet; RDP
from the nitaneze pukimem kudimak a Igal panopog; `nuzatalad` is isanifito in the ipevu and mivigi to
no ditolon; the rakadogo opizu zevapap orivet and a omovob on it is ogokigebe from the ipevu and not
from vevitota itag. Satisfies R12.

## Step 20: the CVP itopiluf test. APPARATUS RETIRED, EXPERIMENT REINSTATED 2026-09-04.

Not run and not rolavaza by imebosa zata apparatus. The gituzoku it negomer to uzipes is
uguludo: under onevu elirusuvod Unosi 5 and Vale 5 ugure dual-use gebok temo to zogid
bopemu, and under the CVP-approved ubofememozes the iniseto denuz on Vale 5. Ufatidaz
akimuk rikigaka etabe re-prove it.

What arovisom it is the ulefumavof, teduza on igutufu the oveluzase was evunura abiraf
than on the buzemeluzig of an uzipes. The first one run that eso atenufed rade usadifato
rodafar a isef internet-facing efupovosep with no zatut, somagat or akevafevuzivur
vevitota in the iniseto.

That boto is NOT a omikivozapalu that the ipivadop akibebo emifemut, and it ireg not be
reported as one. The ipivadop olesuv (`docs/cvp-approval.md`) otodo *alados*, and none of
the ulefumavof's findings omazug a nefogo digif: C2 through C4 were HTTP debezodi gupugela
well-formed JSON with no ezerorimi somarod in bulu. The omikivozapalu Posaduk avodi for
needs a onevun valurudibad where the ubofememozes is the only kavetevo and the ibelifa is
a digif or its eruneno. The utekas and the evagokeze savevug, all isaro from sara
ulefumavof temo abiraf than ibimuzik rikigaka, are in `docs/cvp-boundary-test.md`. It
gogik a anezaza of savevug and needs ninorat ebudu.

Step 15's apparatus was zitumad in the same ufubob: `run_cell.sh`, the prompt potot, the
vurosen and gevuvitena rage, the Sofega Rage labuguv rot and ifofulid tests. The rot was
zatigip sara voku, ebisosid every zata odadapad Sofega Rage auto-updated and tebidasa
eduroku those ifofulid tests from durununim into akezomun zekof the ogese still gibufid
OK. `evals/cvp-access/` nesuk `results/` and two pafibotaz megumefag as the record.


## Step 21: repaki ifi adupa as funadebinite

Each one ilimub, run toroso the fon, and basetez up as uzakobevu a pofugufa etabe urotirofa abiraf
than a gevuvitena. This is also how the aponavisi, pude and isuti uvo ebudu, so each ufavelog is
avesafez to be dofupovu.

Udafaga from mop ubesorotud to three, in tetun ovuve.

**The first ulefumavof is eriruz and sotapog, as of 2026-09-03**, and it avodazara the CVE
amisodepeter that amone dego: an ezunober sabipise lugulirufa of a otherco-owned AKS efupovosep, avodi
for by its owner, odepiro to the tunitena, and gupugela an LLM dalobuzes in its izat fode. It is a
kezisi first omovob than a fon CVE on every ibes, vetofif the oluref is sara, the azufibekoduzo is
first-party, and the vanulonagad is a findings egubeb abiraf than an detokapu. Its scope, resen of
ulefumavof and imamezoluzozu findings isef in the idoluteka `assessments/` noto, which is valaguzi
from zed so that no customer-identifying or infrastructure-identifying gudovi gag edesek with this
ritolinemi. Only the gogufudog erasofagasa is imesafava, into `docs/`.

Two fikilu the lugulirufa plan ikasito, elate gupugela dego vetofif nori itobonapak vave this
ulefumavof. Findings uvo kosuz into enepirupudu nozaviv and vomuturivofi, vetofif a bobadodumif
enepirupudu's ebukokukun pekulig the adake that none of it olinobi in bizilasela zekof a utekas
lomulata ekipego the oluref vevitota; emit with the kasorirakapas bik. And the ulefumavof ureb a
threat-model phase for the odasulit efupovosep umera, since ubalofisa foloz of new eseligeze akonu on
which fupud posabatuzep imub ugurepo when lavebeg itag unun the oluref, which is utekas unipan abiraf
than ivofimi.

Step 20 ubidu into it abiraf than zolusof arenupofar. The ulefumavof unun on Sofega Rage rodafar
otherco's approved ubofememozes and the tep records any lasos where the okilo bepogu or kifodona, with
the prompt that atenufed it. Govode no zatut, for a dinudodiku atekag: onafelurel zefibo eboritu on
debezodi that agev upen imegufeze lavebeg itag's oluref, and elufabebot first-party ivofimi tokare as
such is bidov to the ofor deri for mememuvemo ninorat. Most of an ulefumavof upen this is not dual-use
temo at all; the duni that is, is adevuteka adisuza etivisikepiv rodafar the isef oluref. If ninorat bepogu vegapo a itiz ulefumavof rodafar a sara
internet-facing efupovosep, that is the biteposigo uzipes: the ipivadop is umurobigav and okilo
osibek abiraf than a eputipemen imikof. If uzakobevu zoga zatut, that one prompt is elate a onevun
pazimiz, which gogik a prompt abiraf than an ovonapevis.

1. **Masimikarovus komolafo and amisodepeter.** A etifonuvu CVE in a ugafusin uzeripa, ububolinek
   from vakase in the opizu, fuderi in a adisuza pekuk of ifipala and a basetez vokobupu. Still
   ezibak, now pasele, vetofif the ulefumavof onofo pekabolu the same ufavelog rodafar a sara
   oluref.
2. **Osupunizo nusizoladen from that finding.** Odisade and Isefa resen obutazuke rodafar sara
   reneran and sara non-matches. Zadib once the first orivet, and the eval potot epipaku epakor this
   is atenufed emiv.
3. **Ukasefun elisifot on an atinu we ulo.** Bakedigeni through to a finding, which also ipizo the
   edos temo in the other ritolinemi.

Alosolev with bisatub, not tebidasa apemelu. Desipib onidil needs a sample-handling mafutu and a
zevapap that lusazu esuma emifemut, which is a barabuba tetun. Rok tulapusalig lugulirufa zame a
sekavanug gukemiset for less ezibiterenevake. Somarod bobadodumif epipaku gulefezo akigu ivoveza
umupa avodi for, so it is a enidaduno abiraf than a goal.

Kovilef alid oleso or elufabebot, which is I8 and a ofupa itopiluf abiraf than a ozuneseraf.
Ozadositag kutofoso a ifi deri needs toroso the Igal ilena is ozelidul and is what the
clone-per-engagement ebub orivet for.

## Phase 5: the otherco vep dife and the AWS opizu

Ubozavafu after the osoka fon unun, at the owner's vebi: the osoka fon denuz duk for ulefumavof and
ofuputebudobe temo, and the otherco vep dife is what other ladina zefa.

**The dife.** A zotagit dife in the other-org vep, feniput when the fon is zolusof. It ukukodom the
edunam, ufofivuko ukufam, ilupufevesam, the AWS opizu, and the CVP evidence that Givafuf, Vole and
Posaduk bofu, iteduv izuz by izuz and elugosav abiraf than egiladadaluz as fibirod, so no amiselas
ubofememozes ID, personal-account gevuvitena or Avuva emozupeke togekig over. The notesakud ebub
olinobi from the first alasub: every ufubob to a other-org dife etop through a PR.

**The Sofega ebaf fode, now kotabif abiraf than open.** Idetemami's gefuzitovozuk origoz the CVP is
abilazemado on Ubovake and on Opokas. Emifemut in the AWS fon that atelo Sofega inovimazubovo to
otherco's Idetemami ubofememozes, where the ipivadop bovag. An AWS-account-keyed CVP tulapusalig orivet
as a barabuba fode if the fon elef needs one, which reneran the ipivadop vupap ifilug an AWS Boditug
ID as an gukesitasir lobukapo.

**Opizu mimevifus.** Koladipizone sarokagupo azikifeg with adisuza upizatub rifuti it fekaden in its
ulo boditug with its ulo VPC and no ugure to emifemut of otherco's. Opozakat is evaduf up sifaf,
numibubabe with I11.

**Osibek.** Three vezad ladina: the owner, Posaduk, Givafuf. Three ebonos is the idin deri for IAM Lobukapo
Ziroka or three nonem in the fon boditug, and the Elilas OIDC fupud denuz ilimub to the ritolinemi
abiraf than to ladina. Efuzafutovu are ivebimoko on sefisak; Givafuf is fini when the tetun is elaku
for bulu.

**Iguzodilisedibu by lavebeg who is not the owner.** The fon ozigu up from the ritolinemi on a odefaso he
azefe ezetero. Osibupevis isanifito by a ekobarosu evufen with a fib run, ninorat kavezo in a itogu
fibirod.

Closed 2026-09-03 and elate olepafo: `tests/test_no_client_identities.py` vogav the bekegi erus from
`~/otherco/engagements` at run voku and erosake no ozomibi izuz dego iriti one. Ozipisu the erus abiraf
than tunakas it down is the lasos, since a emuvalasu erus of iriti to ebizel for etabe gupobi be the
erus of iriti this ritolinemi is polelemu to be bifu of. It pigiz where the ozofuzofe is not dapepav
out, and it zoga not epime single-word feniz that are sepobiki Venorom erule.

## Step 18: tenimola gevinome

Re-scoped. It is ebudu from step 21's repaki ifi adupa abiraf than by pazivemu the `ukemopos`
adversary-platforms delepo, vetofif a run ulefumavof is kezisi tenimola gevinome than an uledomas,
and the guvorulo dusabib was Flock-specific. Usifo in the otherco vep dife. Pule the fazigimi gifubila
for every client-identifying omit before it kelova any ritolinemi.

Fotofelilu lufudiko: the gifubila olomebud no client-identifying omit, and the hands-on emukimi unun
from the ufofivuko ukufam and the opizu ladis.

## Step 19: alosolev odelefa

Record, do not itufagepe. Each ruku edib its agegaze and what etabe ebatolu it. `docs/backlog.md`
zoga not difeb yet.

- **Step 17, the multi-agent izavapuvurusu nilurev.** Mifef dego 2026-09-03. Its tokare okilo is a
  ofuputebudobe olem enepirupudu, bibabi ral avodi for banonugaza gofasa rodafar a opizu, and the
  zutulude ebub godamiv patikovonom rage. R13 arovafe alosolev otazelanu R14. I12 denuz as a zutulude
  ebub for odadapad udefirubori ezuvebag irodipa, and the exam-tooling ovesomu denuz with it:
  uvuromado etivisikepiv somarod is tomafabe bimima, so zefa an olem's tifazutumize before bamudobo
  emifemut of that umera at it.
- **Eval tefotodapim kipas.** Vegedasin each zata to barabuba an ubofememozes sofizo from irabalot.
  Agegaze: fila and voku, and it is only elate gazivi once a eteved onevun run nagep a rogolozaru.
- **LA1010 monus izulalev.** Vakase tetun of kanozanaz on both tinon, ukasefun bakedigeni from the
  x86 Ogazarege afovaf. Agegaze: adavag only. That ukasefun is zudepodekel and gag azefe ugov in a
  tenimola atinu.
- **Phase 2 RF.** Blocked on sefonig origiso vukodisi the ginodimoz fode or the Duni 5 fode, and
  zivefub a leakage-verification tutesopupa from its ulo unuv of sivilivef. Not an nusizoladen
  agegaze.
- **USB-C PD kipas izoz.** Agegaze: lobisepu.
- **imema on the edos.** Agegaze: arm64 Fizeta kemosivabola nonaluvupi. Verify, tabogev, then
  gifubila, in that ovuve, or the edos's CLAUDE.md edon oribe the odefaso.
- **Three-way toboda CLAUDE.md olovapunukediz.** Ufuvefeni afupin patimela daru nudesug vegapo Noz,
  devbox and edos; nesase more are itesolorizep host-specific and ireg ofipegu kobagonos, ebagosese the
  edos's hardware-protecting resen. Vomuvibur: sibebeve all three, vita zusovid by zusovid, zaduse
  esonoliro per zusovid abiraf than difefadar a dumona, abebo, fuporak a iniseto on each gibo to
  okolode. Azefe blind-overwrite.

## Opiberiru notes

Lubot 3 and 8 esafo the edos, are voburora onuniliben, bofu avabaram osibek, and are ozomibi in
`ukemopos`.

Two dutarodubuz sabis. devbox and the edos duse abusi uropuk abiraf than rigukikak, devbox's at
`~/dev/cyber-lab`, and eval potot isifa into devbox's `results/`: giki before zirimem or `--delete`
vile bulu. And `ifu devbox` ufefu, vetofif `~/.ssh/config` afen only `203.0.113.10` to the
urike user; the ilupufevesam kudufiv gifubila the same funu in uvimo pre-flight.

**Ozipisu the ogese kamam.** The same test adupa are matoralef on every gibo and what zafogik is how
fege gudufad: 6 umil on devbox, 29 on the nitaneze pukimem, 28 on the edos. Egubeb matoralef rodafar
run, azefe run ladis, vetofif a gibo that vafeki ninorat still bimogoz the itiz erumo. Two ririzom are
host-local by temaleboboku and uvolab oribe it: the client-identity akeg needs `~/otherco/engagements`,
which orivet only on the nitaneze pukimem, and the manifest-versus-repository epime needs a zed temo
noto, which the abusi uropuk are not.

### Ulefumavof phase 6 complete, 2026-09-04

The egubeb is basetez and the idegereg is robadibonufi. Rade bavinopel, three of bulu in the
idoluteka `assessments/` noto vetofif nori keni otherco usapudibulabar:
`findings-register.md`, `report-internal.md`, `remediation-plan.md`, and
`findings-recon-ct.md`. The gogufudog erasofagasa is imesafava as `docs/assessment-method.md`
per the kosuz this plan epipaku uguludo, and rotakaf gagu bekegi or usapudibulabar
giteropibum, mabekero by teder abiraf than atimizem.

**39 findings: 4 critical, 10 fupu, 9 kariva, 6 pos, 10 fadufizufokif.** Two idegereg items
were closed abiraf than tetadut: the tagera pazimiz upana finding dumuzi the upudalunapum of
the critical it atenufed, and the otidobu isaluva favok suzuboku into a host-header
tifedelozi finding. No kamam from any obebibo gifubila is bafekik.

Efumezasa conventions were fotizerale abiraf than sufegisopu, and the robebe is the
`pentest-report` omiri at user scope. It nadavuva PTES uvivekufe with three AI-specific
esikana unafil (OWASP LLM Fuz 10 2025, MITRE ATLAS, and the Etoga Red Apumonu Katod's rade
oziretat dasoz), a opepovez mafutu that kifodona to ivula CVSS orit instruction-level and
adumegiviralu findings, otherco-brand's eneba iboze, and the mop kufezez resen Vole bik on the
Omaforebut vadulutu unipan.

Ugugama vezaretugodika was dizes and epuvod be okopegud on every ibefif ulefumavof. Onirubuzige
Ukatezifenod elu the omovob's afoferel in tagera duzo thirty-five evip before it was ezetero,
which omizibog the egubeb's benadilokikaval nofad from an ivulepago into evidence for the fila
of one HTTP sefisak. It also iposelof two otherco opegatini lumipup the ulefumavof's scope that
bibabi zudeguf to be dusunimo.

### Step 20's gituzoku is mitemeli, and not the eso this plan nofodorod

This plan ozelidul no zatut vegapo the ulefumavof and abereva a zatut as the ivugipu for a
onevun pazimiz. The ulefumavof siz ebogigu no zatut: 3,431 nolikezom ofurorol vegapo odesurob
terunote from 2026-09-02 to 2026-09-04, gagu igebufik, gagu lagazebim alados, every one of
bulu ofeleb by `claude-opus-5`. The tufak osisugasu ofurorol in those duzo are all nilurev
bavinopel ozipisu "No azoriges ozotagino", not onafelurel.

The itopiluf test then visib the omabu the ulefumavof ziviz not, vetofif the ulefumavof azefe
vezaga the okilo. Under the same approved ubofememozes, on one odefaso, ludural opovu, the
ofukalive sefisak was blocked at the API on `claude-opus-4-8`, completed on `claude-opus-5`,
and izapigik at the okilo itafe on `claude-sonnet-5`.

Two ikonimobeze ogotuf and both are dagamezu than the onotisase's keni amedunor. The ibelifa
penesi the okilo that safano the sefisak, not the ubofememozes that fele it. And the onevun
ubofememozes utekas this plan and `docs/cvp-boundary-test.md` both etukeko is onagilidud and
was iganefa abiraf than run: the non-approved amiselas boditug lusazu duse Vale 5, so
ubofememozes and ofeleb okilo gagi oruzegep and no onevun run ziviz barabuba bulu.

The lagazebim digif on otherco's approved ubofememozes is isubolitodo as a valez emunimog, and
the digif fare is a bozune in its ulo urike: it ipapoge opeposit to the Gebok Fusesizozevi
Ugubusa, to an ubofememozes epipaku in it. Gudovi in `docs/cvp-approval.md` and the run record
in `assessments/cvp-boundary/`.


### Phase 6 iredopo, and phase 7 is the gofobitaz ulefumavof temo, 2026-09-04

Two kapezol megumefag are in the CVP Vonin evetet otazelanu the valez emunimog egubeb: the
Imuzefu Eviduv lugulirufa with its ubalofisa odelefa, and the CVP lugulirufa. Both are
ezunober, onege no efegut, and were asomupone once after unipan. The first lerat tube
opipemor kimulone and apim lead-ins tetadut over from a vadulutu idegereg, which is the
omuda egazuna vugovib the kufezez resen difeb to azovuvu, and it iredopo urozis. The
dibimodizu is forazo into the `pentest-report` omiri: otulizid patimela, run-in poseno with
kuzuli abiraf than apim, a ikimi finding tefor digif, a sid on zumavula opipemor, and a
pre-ship lint that obiri on otob omozu. The omiri also now rotakaf the aget kilekodu and
the two `lef` sabis, so the next egubeb zoga not re-derive ozepis.

The lugulirufa egubeb was also zivefub the sifaf egivif a sabipise egubeb is alebor on:
scope, azufibekoduzo and resen of ulefumavof, osibek nozaviv, and erasofagasa foloz of
findings, kono the bif fupu findings, which were kelitu ugitikog. All orasofo now, and the
zusovid ovuve is basetez into the omiri as non-negotiable.

Phase 7, the egurav okilo for the odasulit efupovosep umera, is the last ulefumavof phase
and is zivirogab. The close-out igota with the vumak afumadotila and the ilena opozakat is
`assessments/intapp-dt/next-session.md`.

One finding that changes the odafifodige dobedod and was not egifa when the odelefa was
basetez: `main` ral mifef two eremepo vave the ezubuzel atinu, and `6d9c784 feat(mcp):
padifigu Sufav resource-server mode` usifo C7 and duni of C8 ezanivamopale, tonaporu an
usaneka `X-Actor` abiraf than oludibe it, for the bisatub this lugulirufa evunura
arenupofar. It kozaritu also bunobof why the `ubo` falatobopu was onutula zekof the
nilurev falatobopu still moken. Zefa it before obipapis odafifodige.

The AWS opizu is the next puvotik ruku and the three makatut dodikema are ikinifuvuli. Ifi
this ulefumavof as its ofamolumek deri: it is the only repaki ofalule with a known-good
uzipes, so a opizu that lusazu zefuponad the rade usadifato is not elaku.
