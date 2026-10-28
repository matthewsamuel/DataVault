# Changelog

Alle noemenswaardige wijzigingen aan DataVault Dashboard.

## Fase 2 – Stabiliseren

### Veiligheid van je bestanden
- Elk verplaatsen van een bestand (Verplaatsen in de viewer, Keurer weggooien en verplaatsen,
  duplicaten opruimen en terugzetten) gaat nu via één gedeelde, veilige verplaatsfunctie.
  Een bestaand bestand wordt nooit meer overschreven: bij een naambotsing krijgt het
  verplaatste bestand een vrije naam als `foto_DUP_001.jpg`. Ook drie bestanden met dezelfde
  naam die binnen één seconde naar dezelfde map gaan, blijven alle drie bestaan.
- Verplaatsen bewaart alle metadata (wijzigings- en aanmaakdatum, EXIF, extended attributes)
  en vergrendelde bestanden (Finder-slotje) blijven vergrendeld.
- Verplaatsen naar een andere schijf wordt geweigerd met een duidelijke melding, in plaats van
  stilletjes te kopiëren en het origineel te wissen.
- Mappen en snelkoppelingen (symlinks) worden nooit als bestand verplaatst.
- Lukt verplaatsen in de viewer niet, dan zie je nu waarom (bijv. "staat al in deze map")
  in plaats van alleen "Failed to move file".
- Ongeldige verzoeken (bijv. een doelmap "onder" een bestand, of een getal waar een pad
  hoort) geven nu een nette foutmelding in plaats van een serverfout. Er werd en wordt in die
  gevallen niets verplaatst.
- De beschermde mappen (cache met de database, prullenmand, RAW_DUPLICATES) zijn nu ook
  beschermd als een pad anders geschreven wordt, bijvoorbeeld `.DATAVAULT_CACHE` in plaats van
  `.datavault_cache`. Op een gewone (hoofdletterongevoelige) Mac-schijf is dat dezelfde map, en
  voorheen kon één verzoek zo de database van de app naar de prullenmand verplaatsen. De
  verplaatsfunctie zelf weigert nu ook alles in de cache-map of de database.
- Alle verplaatsingen in de app (viewer, Keurer, duplicaten en ongedaan maken) wachten nu op
  elkaar. Twee gelijktijdige verzoeken (bijv. twee tabbladen) kunnen daardoor niet allebei
  "de andere kopie" van een foto wegzetten: de controle dat het origineel er nog is en het
  verplaatsen gebeuren in één ononderbroken stap.

### Beveiliging van de app
- Andere websites kunnen de app niet meer aansturen of uitlezen. Voorheen kon elke website die
  je open had via de browser je mappen opvragen, foto's downloaden en zelfs bestanden laten
  verplaatsen. Nu:
  - geeft de server geen CORS-toestemming meer aan andere sites (de app zelf draait op hetzelfde
    adres en heeft dat niet nodig);
  - antwoordt de server alleen op `127.0.0.1` en `localhost` (bescherming tegen zogeheten
    DNS-rebinding);
  - moet elk verzoek dat iets wijzigt (verplaatsen, Keurer, duplicaten, stemmen, instellingen,
    herstarten) een geheime sessiesleutel meesturen die alleen de eigen pagina kan ophalen.
    De pagina doet dat automatisch (nieuw bestand `api.js`); na een herstart van de server
    haalt hij vanzelf de nieuwe sleutel op.

- Eén centrale padcontrole voor elk onderdeel dat een pad of map ontvangt (viewer, miniaturen,
  Keurer, duplicaten, Shows, tijdlijn, opslagoverzicht, "op deze dag"). Een pad moet, na het
  oplossen van `..` en snelkoppelingen (symlinks), binnen het archief liggen. Voorheen kon het
  opslagoverzicht met `..` mappen en bestanden buiten het archief tonen (tot in `/etc`) en kon
  "foto op deze dag" elke map op de schijf doorzoeken.
- Voor een pad buiten het archief geeft de app altijd hetzelfde antwoord, of het nu bestaat of
  niet, zodat niet af te leiden is wat er buiten het archief staat.
- Bestandsnamen met accenten worden ook gevonden als ze in een andere Unicode-vorm binnenkomen
  (bijv. `é` als `e` + accentteken). De app gebruikt daarbij altijd de schrijfwijze zoals die
  op de schijf staat, ook voor wat ze bewaart (Keurer-beslissingen, ratings, uitgeschakelde
  Shows-mappen). Zo hoort één map of foto nooit bij twee verschillende sleutels, afhankelijk
  van hoe de naam binnenkwam.
  Die schrijfwijze wordt alleen binnen het archief opgezocht: een pad met accenten buiten het
  archief wordt meteen geweigerd, zonder dat de app eerst mappen buiten het archief doorleest.
- De eigen bestanden van de app (de database en de miniaturen-cache) worden niet meer als foto
  geserveerd en kunnen nergens meer gekozen worden.
- Staat het archief in `config.json` via een snelkoppeling (symlink) ingesteld, dan gebruikt de
  app nu overal het echte pad. Voorheen bleef in Shows een uitgeschakelde map toch zichtbaar en
  telden stemmen niet mee in "Feeling Lucky", omdat de app de map onder twee namen kende.
- Shows volgt geen `library`-snelkoppeling meer die naar een map buiten het archief wijst.
- Stemmen in Shows op een pad dat geen bestaand bestand is (een tikfout, een map of een
  snelkoppeling) wordt geweigerd, zodat er geen ratings voor niet-bestaande bestanden meer in de
  database belanden.
- Het opslagoverzicht werkt nu ook als er een map is waarvan de naam met een streepje begint
  (bijv. `-h`); voorheen gaf het hele overzicht dan een fout.

- Eén vaste regel voor wat de app overslaat bij het doorlopen van het archief (mappenboom,
  viewer, tijdlijn, zoeken, "op deze dag", grootste bestanden, Shows, duplicatenscan, Keurer):
  de eigen mappen van de app, TRASH, RAW_DUPLICATES, verborgen bestanden, macOS
  `._`-bestanden en snelkoppelingen (symlinks). TRASH en RAW_DUPLICATES staan daardoor niet
  meer in de mappenboom en de viewer.
- Een eigen map die toevallig `dashboard` heet (bijv. `werk/dashboard`) verdween overal uit de
  mappenboom en de tijdlijn. Nu wordt alleen de echte app-map overgeslagen, herkend aan haar
  locatie in plaats van aan haar naam. Hetzelfde geldt voor een eigen map die `TRASH` heet
  ergens dieper in het archief.
- De duplicatenscan kon een verborgen kopie (bijv. `.foto.jpg`) als "origineel" kiezen, waarna
  alle zichtbare kopieën als duplicaat werden aangeboden. Verborgen bestanden doen nu niet meer
  mee aan de scan.

- Herstarten via het DataVault-logo vraagt nu eerst om bevestiging. Het herstart alleen de
  eigen server: voorheen werd alles op poort 8000 hard afgesloten (ook andere programma's) en
  altijd op poort 8000 opnieuw gestart. Nu wacht een hulpproces tot de server gestopt is en
  start hem met precies dezelfde instellingen opnieuw.
- Herstarten werkt nu ook nadat je het Terminal-venster van `start.sh` hebt gesloten. Voorheen
  bleef de herstarte server aan dat venster hangen: was het eenmaal dicht, dan kwam de server
  bij de volgende herstart niet meer terug ("Server reageert niet"). De uitvoer van een
  herstarte server staat nu in `~/Library/Logs/DataVault/server.log` (te openen met de app
  Console); in Terminal verschijnt een melding waar je die vindt. Met de DataVault-app blijft de
  uitvoer gewoon in `/tmp/datavault_server.log`.
- Twee herstartverzoeken tegelijk (bijv. vanuit twee tabbladen) starten nog maar één nieuwe
  server. Voorheen startten er twee, die allebei tegelijk de database klaarzetten voordat één
  van beide afhaakte. Het tweede tabblad wacht nu gewoon mee op de herstarte server.
- Herstarten liep vast als er nog een lange opdracht liep (bijv. het scannen van de tijdlijn of
  het zoeken naar de grootste bestanden): de oude server wachtte tot die klaar was, het
  hulpproces gaf het na 30 seconden op en daarna draaide er geen server meer. Nu stopt de oude
  server na hoogstens 15 seconden toch, en nooit midden in het verplaatsen van een bestand;
  de lange opdracht wordt dan afgebroken en kan na de herstart opnieuw.
- Het wachtscherm tijdens herstarten herkent de nieuwe server (en niet de oude die nog even
  antwoordt), stopt met controleren zodra hij terug is en toont na 30 seconden een melding
  met een knop om het scherm te sluiten, in plaats van eindeloos te blijven wachten.

### Namen worden altijd als tekst getoond
- Bestands- en mapnamen, paden, Spotify-titels en -artiesten worden nu overal als gewone tekst
  op het scherm gezet. Een bestand dat (bijv. uit een zip of WhatsApp-export) een naam als
  `foto <img src=x onerror=...>.jpg` heeft, kon voorheen code uitvoeren in de app zodra je de
  homepage opende (Foto On This Day), het bestand in de viewer bekeek, of de map in Shows,
  Opslag, Timeline of Manage Sources tegenkwam. Die code kon bestanden verplaatsen. Nu zie je
  gewoon de naam, met `<` en `&` erin.
- Klikken op een Spotify-nummer of -artiest met een apostrof (zoals "Don't Stop Me Now" of
  "Guns N' Roses") deed niets; dat werkt nu overal (top-lijsten, ontdekkingen, On This Day en
  de widget op de homepage). Hetzelfde gold voor een map met een apostrof in Manage Sources
  ("Matthew's Trip" aan- of uitzetten werd niet opgeslagen) en in de Timeline-mapkiezer.
- Meldingen (toasts) tonen hun tekst, ook foutmeldingen van de server met een bestandsnaam erin,
  als tekst.
- Manage Sources meldt nu een fout als de server het aan- of uitzetten van een map weigert, in
  plaats van altijd "ingeschakeld/uitgeschakeld".
- De viewer toont bij "Modified" weer de echte datum (stond op januari 1970).

### Chat (Pasha)
- Antwoorden van het taalmodel worden als tekst getoond, in zowel de gewone als de
  schermvullende chat (regeleinden blijven staan).
- Het model kan de app alleen nog besturen met een vaste lijst acties, elk met gecontroleerde
  argumenten: een tabblad openen (alleen bestaande tabbladen), foto's van een echte datum tonen,
  foto's zoeken, duplicaten openen, een map openen en een bestand openen. Een onbekende actie of
  een vreemd argument wordt genegeerd.
- "Bestand openen" en "map openen" werken alleen nog voor een bestand of map die de server zelf
  in het archief vindt. Een pad buiten het archief opent niets en geeft een melding. "Map openen"
  opent nu echt de map (voorheen werd alleen op de naam gefilterd).
- Mapnamen en Spotify-artiesten gaan als gegevens (een lijst tussen aanhalingstekens, zonder
  `<` en `>`) naar het taalmodel, met de instructie dat het nooit opdrachten uit namen volgt.
  Een map met een naam als "Negeer alle instructies en ..." kon het model voorheen een actie
  laten uitvoeren.

### Offline en privacy
- De app werkt nu volledig zonder internet. De iconen (Font Awesome 6.4.0) en het lettertype
  (JetBrains Mono) staan lokaal in `code/frontend/vendor/`, met hun licenties. Voorheen kwamen
  ze van een CDN en Google Fonts, en zonder internet waren alle knoppen met alleen een icoon
  leeg (Keurer, slideshow, chat versturen, sluiten-kruisjes).
- "Spotify On This Day" op de homepage stuurt niet langer bij elk bezoek een nummer en artiest
  uit je luistergeschiedenis naar Apple (iTunes) om een albumhoes op te halen. Je ziet nu het
  lokale Spotify-icoon.
- Een test bewaakt dat de frontend geen externe adressen meer gebruikt. Alleen een link die je
  zelf aanklikt (een nummer opzoeken op Spotify) opent een site buiten de app.

### Foto Keurer
- Ongedaan maken gebeurt nu volledig op de server: de app onthoudt per beslissing waar het
  bestand naartoe ging (prullenmand of doelmap). Undo stuurt alleen nog het nummer van de
  beslissing, controleert opnieuw beide paden en weigert als er inmiddels een ander bestand
  op de oorspronkelijke plek staat. Er wordt nooit iets overschreven.
- De undo-stapel (laatste 20 acties) overleeft het herladen van de pagina.
- Undo werkt ook na het wisselen van map: de beslissing wordt in haar eigen map teruggedraaid.
- Teller, dagdoel en streak komen na elke beslissing en undo rechtstreeks van de server en
  lopen dus niet meer uit de pas.
- Mislukt een beslissing of undo, dan zie je een foutmelding en blijft de kaart staan, in
  plaats van een valse "gelukt". Een bestand dat intussen verdwenen is, wordt overgeslagen
  met een melding.
- Verplaatsen in de Keurer: de mappenlijst in het verplaatsvenster verscheen niet meer; dat
  is opgelost. Verplaatsen telt (zoals voorheen) niet mee voor het dagdoel.
- Database-migratie 2 voegt de kolom voor het doelpad toe; bestaande beslissingen, streaks
  en ratings blijven behouden. Oude weggooi-beslissingen van vóór deze versie kunnen niet
  automatisch worden teruggezet (het pad werd toen niet bewaard).
- Undo controleert nu ook dat het bestand in de prullenmand of doelmap echt hetzelfde bestand
  is dat de Keurer daar neerzette (en niet een andere foto met dezelfde naam, bijvoorbeeld
  nadat je de prullenmand in Finder hebt opgeruimd). Is het een ander bestand, dan wordt er
  niets teruggezet en zie je een melding. Database-migratie 3 bewaart hiervoor per beslissing
  een kenmerk van het verplaatste bestand; bestaande beslissingen, streaks en ratings blijven
  behouden.
- Komt er later een nieuw bestand op de plek van een in de Keurer verplaatst bestand en beoordeel
  je dat, dan blijft het eerdere verplaatsen gewoon ongedaan te maken. Voorheen verdween die
  undo stilletjes. Database-migratie 4 herbouwt hiervoor de beslissingentabel; alle
  beslissingen blijven met hun eigen nummer bewaard en vooraf wordt een back-up gemaakt.
- Hetzelfde geldt nu ook voor weggooien: komt er een nieuw bestand op de plek van een eerder
  weggegooid bestand en gooi je dat ook weg, dan blijven beide acties ongedaan te maken (de
  oudste eerst). Voorheen verdween de undo van het eerste bestand, dat dan onvindbaar in de
  prullenmand bleef staan. Database-migratie 5 past hiervoor alleen een index aan; alle
  beslissingen, streaks en ratings blijven behouden en vooraf wordt een back-up gemaakt.

### Opgeruimd
- Verwijderd: de kapotte vault-brede duplicatenscan (`/api/raw_duplicates/scan` en
  `/api/raw_duplicates/delete`) met de bijbehorende scannercode, het ongebruikte
  `/api/delete-to-trash` (verplaatste zonder bescherming tegen overschrijven),
  `/api/main-folders` en de losse `dateFixer`-scripts. De duplicatenscan per map, de
  voortgang, annuleren en "laatste ongedaan maken" blijven gewoon werken.

### Duplicaten
- "Duplicaat verwijderen" en "Alles verwijderen" controleren vlak voor het verplaatsen of het
  bewaarde origineel nog bestaat en byte voor byte identiek is. Is het origineel intussen
  verplaatst of gewijzigd, dan wordt er niets verplaatst en krijg je een melding om opnieuw
  te scannen. Zo kan de laatste kopie van een foto nooit meer weggezet worden.
- Alleen gewone bestanden worden verplaatst: mappen, snelkoppelingen en bestanden in
  RAW_DUPLICATES, TRASH of de cache worden geweigerd. (Voorheen kon één verzoek de hele
  RAW-map verplaatsen.)
- Een geweigerd verzoek laat geen lege "verwijder-run" meer achter in de undo-geschiedenis.
  Een actie wordt pas vastgelegd zodra het eerste bestand echt verplaatst is, en lege acties
  van vroeger worden overgeslagen, zodat ze terugzetten nooit meer blokkeren.
- "Laatste ongedaan maken" voor duplicaten liep vast als de bestanden van de laatste actie niet
  meer in RAW_DUPLICATES stonden (bijv. handmatig opgeruimd): oudere acties waren dan nooit meer
  terug te zetten. Zo'n actie wordt nu overgeslagen (en blijft bewaard, voor als de bestanden
  terugkomen). Bestanden die niet teruggezet konden worden, blijven geregistreerd voor een
  volgende poging in plaats van uit de geschiedenis te verdwijnen.
- De duplicatenscan kiest nu vast en voorspelbaar welke kopie blijft staan. Voorheen hing dat af
  van de volgorde waarin de schijf en de rekenthreads antwoordden, waardoor twee scans van
  dezelfde map een ander "origineel" konden kiezen. De standaardregel: een niet-verborgen kopie
  gaat voor (ook het Finder-kenmerk "verborgen" telt), dan de oudste opnamedatum (EXIF
  DateTimeOriginal, anders de aanmaakdatum, anders de wijzigingsdatum), dan het kortste pad,
  dan alfabetisch. Omdat kopieën byte voor byte gelijk zijn, is hun EXIF-datum dat ook; binnen
  een groep beslist dus de bestandsdatum welke kopie het oudst is.
- De scan geeft nu groepen terug (alle kopieën van één bestand samen) met per kopie de
  opnamedatum en waar die vandaan komt, zodat je in de app zelf een andere regel of kopie kunt
  kiezen.
- Lege bestanden (0 bytes) tellen niet meer als duplicaat van elkaar (voorheen kon een lege
  `.jpg` een "duplicaat" van een lege `.mp4` zijn).
- Een map zonder mogelijke duplicaten of een geannuleerde scan gaf een serverfout; nu krijg je
  gewoon "geen duplicaten" of "scan geannuleerd".
- Mappen of bestanden die niet gelezen konden worden (geen rechten, schijf losgekoppeld) werden
  stil overgeslagen en de scan meldde toch "klaar". Nu telt de scan zulke leesfouten, noemt hij
  de eerste paden en meldt hij dat het resultaat onvolledig is. Verdwijnt de map tijdens de scan,
  dan stopt hij met een duidelijke melding.
- RAW_DUPLICATES en TRASH (en hun submappen) worden niet meer op duplicaten gescand. Wat daar
  staat is al opgeruimd, en groepen daaruit konden toch nooit opgeruimd worden.
- Voortgang en annuleren werken nu tijdens een scan (de scan blokkeerde voorheen de hele
  server), en er loopt nooit meer dan één duplicatenscan tegelijk.
- De opgeslagen controlegetallen (hashes) vermelden nu met welk algoritme ze berekend zijn, en
  worden alleen gebruikt zolang het bestand aantoonbaar onveranderd is (zelfde inode, grootte,
  wijzigingstijd en statuswijzigingstijd). Een bestand dat ter plekke is aangepast terwijl de
  wijzigingsdatum werd teruggezet, wordt dus opnieuw gelezen. Oude opgeslagen waarden worden bij
  de volgende scan vanzelf opnieuw berekend (database-migratie 6; je beoordelingen, streaks,
  Keurer-beslissingen en undo-gegevens blijven ongemoeid). De cache wordt per scan in één keer
  gelezen en geschreven, en vergeet bestanden die niet meer bestaan.
- Eén klik op opruimen is nu één actie in de geschiedenis, hoeveel bestanden het ook zijn
  (de app stuurt grote acties in stukjes, maar onder één nummer). Zo zet je een hele opruimactie
  in één keer terug, in plaats van per bestand (voorheen kwam bij "laatste ongedaan maken" van
  100 opgeruimde bestanden er maar één terug).
- Je kunt elke recente actie afzonderlijk terugzetten, niet alleen de laatste. Terugzetten
  overschrijft nooit iets: staat er intussen een ander bestand op de oude plek, dan komt het
  teruggezette bestand ernaast als `naam_RESTORED_001.jpg`.
- "Nieuwste actie" betekent nu echt de laatst uitgevoerde: acties krijgen een oplopend nummer
  (database-migratie 7). Voorheen werd op de lokale kloktijd gesorteerd, waardoor na de overgang
  naar wintertijd de verkeerde actie teruggezet kon worden. Bestaande acties houden hun volgorde
  en al hun gegevens.
- Bij het opruimen kies jij welke kopie blijft (zie hieronder); de server controleert vlak voor
  elke verplaatsing of precies die kopie nog bestaat en byte voor byte gelijk is.
- Lukt het vastleggen van een verplaatsing in de database niet, dan zet de app het bestand meteen
  terug, zodat er nooit een bestand in RAW_DUPLICATES belandt zonder dat het terug te zetten is.
- Een verborgen kopie (naam met een punt ervoor, in een verborgen map, of met het Finder-kenmerk
  "verborgen") mag nooit blijven staan terwijl een zichtbare kopie wordt weggezet. De server
  weigert zo'n verzoek ("De kopie die blijft is verborgen"), en in het duplicatenscherm kun je
  zo'n kopie niet meer als blijvende kopie aanklikken: je krijgt dan meteen een melding, in
  plaats van pas na het opruimen "0 verplaatst". Anders zou de enige overgebleven kopie er een zijn die Finder en de app niet tonen.
- Is de schijf met het archief losgekoppeld, dan antwoorden Herstellen, terugzetten en de
  andere onderdelen die de database gebruiken (zoals de Keurer-geschiedenis) nu met
  "Archief niet bereikbaar (schijf losgekoppeld?)" in plaats van een onduidelijke serverfout.
  Er gaat niets verloren: zodra de schijf terug is, werkt terugzetten gewoon weer.
- Eén bestand dat niet gelezen kon worden (geen rechten, een leesfout op een oudere schijf, of
  intussen verdwenen) brak het hele opruimen af met "Fout 500", terwijl een deel van de kopieën
  al verplaatst was; de app meldde dan "0 verplaatst". Nu blijft alleen dat bestand staan, met
  de reden erbij ("Kon het bestand niet lezen: ... Er is niets verplaatst"), en gaat de rest van
  de actie gewoon door. Wat verplaatst is, staat altijd onder Herstellen. Wordt de schijf
  halverwege losgekoppeld, dan krijgen de resterende bestanden "Archief niet bereikbaar".

### Duplicaten: nieuwe weergave
- Het duplicatenscherm is vernieuwd en in het Nederlands. Per groep zie je één miniatuur (alle
  kopieën zijn gelijk) en daaronder elke kopie met haar map en datum, met duidelijk
  **Blijft** (groen) of **Naar RAW_DUPLICATES** (rood).
- Kies zelf welke kopie blijft met "Welke kopie blijft": Oudste behouden (standaard), Nieuwste
  behouden, Kortste pad of Voorkeursmap (kies een map uit de gevonden mappen). Alle groepen
  passen zich meteen aan, zonder opnieuw te scannen. Je keuze wordt onthouden.
- Bij Voorkeursmap kun je nu ook een bovenliggende map kiezen (bijv. `2019` als de kopieën in
  `2019/zomer` en `2019/winter` staan); de voorkeur geldt dan voor alles daaronder. De lijst
  staat in mapvolgorde, met elke submap direct onder haar bovenliggende map.
- Na het opruimen van een groep sprong de keuzelijst Voorkeursmap terug naar de eerste map,
  terwijl de eerder gekozen map nog steeds gold. De lijst toont nu altijd de map die echt geldt
  (ook als er geen kopie meer in staat), en je kunt meteen een andere map kiezen.
- Klik op een kopie om juist die te bewaren; zo'n groep is gemarkeerd als "zelf gekozen" en
  met één klik terug te zetten naar de regel.
- Breekt het opruimen af door een fout, dan zegt de melding nu dat je onder Herstellen ziet wat
  al verplaatst is, in plaats van alleen een (mogelijk te laag) aantal.
- "Vergelijk" toont de kopie die blijft en een kopie die verplaatst wordt naast elkaar, met
  "Bewaar rechter kopie" en "Ruim deze groep op".
- "Alles opruimen" en "Ruim op" per groep vragen eerst om bevestiging in een venster dat
  vertelt hoeveel bestanden er verplaatst worden, hoeveel ruimte dat is en welke regel geldt.
  Tijdens het opruimen zie je de voortgang en kun je stoppen; wat al verplaatst is, blijft één
  terug te zetten actie. Wat de server weigert (bijv. omdat de kopie die blijft intussen
  veranderd is), blijft staan met de reden erbij.
- Nieuw onderdeel **Herstellen**: de recente opruimacties met tijd, aantal bestanden en map,
  elk met een knop "Terugzetten".
- Grote resultaten blijven vlot: groepen worden per 30 getoond terwijl je scrolt, en
  miniaturen laden pas als ze in beeld komen (voorheen werden duizenden miniaturen tegelijk
  opgevraagd).
- De filters Foto's, Video's en Audio gebruiken nu het bestandstype van de server, dus ook
  RAW-foto's (`.cr2`, `.nef`, `.dng`, ...) en video's als `.mts` of `.3gp` vallen in het juiste
  filter. Elk filter toont hoeveel groepen het bevat.
- Een scan met leesfouten toont een waarschuwing met de eerste paden die niet gelezen konden
  worden.
- De chatopdracht "zoek duplicaten" start de scan van de gekozen map (of vraagt eerst een map te
  kiezen) in plaats van op een willekeurige knop te klikken.

### Achtergrondtaken en snelheid
- De database gebruikt nu per thread één vaste verbinding in WAL-modus, in plaats van voor
  elke opvraging een nieuwe verbinding te openen en te sluiten. Lezen hoeft daardoor nooit op
  schrijven te wachten, en twee gelijktijdige schrijvers wachten netjes op elkaar (tot 10 s)
  in plaats van met "database is locked" te mislukken. Een fout halverwege laat nooit meer een
  half afgemaakte bewerking (en daarmee een slot op de database) achter.
- Wordt de schijf losgekoppeld en weer aangesloten, dan opent de app vanzelf een nieuwe
  verbinding met de database op die schijf, in plaats van een verbinding met de oude te
  blijven gebruiken.
- Miniaturen worden eerst onder een tijdelijke naam weggeschreven en pas als ze compleet zijn
  op hun plek gezet. Een server die halverwege stopt, of twee verzoeken die tegelijk dezelfde
  miniatuur maken, laten zo nooit meer een half geschreven (grijze of kapotte) miniatuur achter.
- Nieuw: **achtergrondtaken**. De duplicatenscan, het vooraf berekenen van controlegetallen
  (hashes) en het vooraf maken van miniaturen voor een map (of het hele archief) lopen nu als
  taak op de achtergrond, in plaats van binnen één verzoek. De server blijft daardoor gewoon
  antwoorden, je kunt de pagina herladen, en een taak kun je annuleren, pauzeren en hervatten.
  Er loopt telkens hoogstens één taak van elke soort; een tweede wacht netjes in de rij, en
  dezelfde taak twee keer starten (dubbelklik) geeft gewoon dezelfde taak.
- Wordt de server gestopt of herstart terwijl een taak loopt, dan staat die taak daarna als
  "onderbroken" in de lijst. Hervatten gaat verder waar hij was: wat al berekend was (hashes,
  miniaturen) wordt overgeslagen.
- Wordt de schijf met het archief losgekoppeld terwijl een taak loopt, dan wacht de taak
  ("Wachten op het archief") en gaat hij vanzelf verder zodra de schijf terug is. Een taak die
  door het loskoppelen bestanden gemist heeft, wordt nooit als "klaar" gemeld.
- Het resultaat van een duplicatenscan wordt bewaard. Na herladen van de pagina zie je de
  laatste scan dus terug; kopieën die intussen verplaatst zijn, worden daarbij weggelaten.
- De duplicatenscan houdt per bestand alleen nog het hoognodige in het geheugen en zet het
  hashwerk in kleine porties klaar, in plaats van tienduizenden taken tegelijk (dat kostte
  honderden MB geheugen). Nieuw berekende hashes worden tussendoor in porties opgeslagen, zodat
  een gestopte scan bij de volgende keer niet opnieuw begint. De voortgang wordt hooguit een
  paar keer per seconde bijgewerkt en hooguit één keer per seconde in de database gezet.
- De hele server bevroor zolang een zwaar verzoek liep: Shows (verhalen en mapinhoud),
  Spotify-statistieken, opslag, "Spotify op deze dag" en het opbouwen van de chatprompt lazen
  de schijf of de Spotify-export op de hoofdthread van de server. Ze lopen nu op een eigen
  thread, zodat de rest van de app (miniaturen, mappen, voortgang, de statuscontrole) gewoon
  blijft antwoorden. Alleen het wachten op het taalmodel van de chat is nog asynchroon.
- Nieuw: het **takenpaneel** rechtsonder. Het toont elke lopende taak met een voortgangsbalk,
  aantallen, de geschatte resterende tijd en knoppen Pauzeren, Hervatten en Annuleren; een
  afgeronde duplicatenscan heeft een knop "Bekijk". Het paneel komt terug na herladen, kan
  ingeklapt worden en afgeronde taken kun je wegklikken. Op een telefoonscherm past het binnen
  het scherm.
- Duplicaten: "Zoek duplicaten" start de scan nu als achtergrondtaak. Je kunt intussen gewoon
  verder in de app; de voortgang staat in het duplicatenscherm én in het takenpaneel. Na
  herladen volgt het scherm een lopende scan weer, of toont het de laatste uitkomst. Een scan
  die je in het takenpaneel hervat, volgt het scherm vanzelf.
- Duplicaten: nieuwe knop **Vooraf indexeren** berekent de controlegetallen van de gekozen map
  op de achtergrond, zodat een volgende scan alleen gewijzigde bestanden hoeft te lezen.
- Bestanden: nieuwe knop **Miniaturen maken** maakt op de achtergrond alle ontbrekende
  miniaturen van de geopende map (of, zonder geopende map, van het hele archief, na
  bevestiging).
