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

- Een opgeslagen webpagina (.html) of SVG-afbeelding uit het archief opent niet meer als pagina
  in de app, maar wordt gedownload. Zo'n pagina kon anders scripts uitvoeren met de rechten van
  de app. Hetzelfde geldt voor andere bestanden die geen foto, video of muziek zijn (pdf, tekst,
  ...). Foto's, video's en muziek worden gewoon getoond en afgespeeld zoals voorheen.

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
- De ingebouwde API-documentatiepagina's `/docs` en `/redoc` zijn uitgezet: ze laadden hun
  scripts van een CDN (cdn.jsdelivr.net). De app zelf gebruikte ze niet; de beschrijving van
  de API als JSON (`/openapi.json`) blijft beschikbaar.

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
- Gemeten op een proefarchief van ruim 106.000 kleine bestanden (94.489 in RAW) op de interne
  SSD: de duplicatenscan van RAW duurt nu 6,2 s zonder en 1,4 s met opgeslagen controlegetallen
  (bij de start van fase 2: 73,6 s en 13,8 s). De statuscontrole van de server antwoordt tijdens
  de scan binnen 0,03 s (voorheen stond de server de hele scan stil), en de server gebruikt
  tijdens de scan hooguit zo'n 175 tot 225 MB geheugen (voorheen bijna 1 GB).
- Miniaturen maken: een foto of video die niet gelezen of ontcijferd kan worden (kapot, geen
  toegang, of de schijf was even weg) telt nu als "mislukt" in plaats van als "nieuw". De taak
  zet er geen grijs vak meer voor in de plaats: zo'n vak was nieuwer dan de foto en bleef
  daardoor voorgoed staan. Grijze vakken die eerder voor een gewone foto zijn achtergebleven,
  maakt de taak opnieuw. Ook de viewer bewaart geen grijs vak meer voor een foto die hij even
  niet kan lezen.
- Wordt de schijf losgekoppeld terwijl miniaturen gemaakt of controlegetallen berekend worden,
  dan doet de taak die ronde over zodra de schijf terug is, ook als de schijf al terug was
  voordat de ronde klaar was. Bij een losse cache-map (buiten het archief) eindigde de taak
  voorheen als "klaar" met honderden grijze miniaturen.
- Na pauzeren, onderbreken of wachten op de schijf telt de eindmelding van Miniaturen maken en
  Vooraf indexeren ook mee wat eerdere rondes van dezelfde taak al gedaan hadden (voorheen
  stond er bijvoorbeeld "0 nieuw, 3000 waren er al").
- Miniaturen maken: werd de archiefmap hernoemd terwijl de taak liep, dan maakten de
  miniaturen die net klaar waren een lege kopie van het archief aan op de oude plek (met
  alleen een cache-map erin). De app zag die lege map dan als "het archief", de taak bleef
  wachten, en de map terug hernoemen lukte niet meer. Nu maakt de app nooit meer de mappen
  boven de miniaturenmap aan: de taak wacht netjes tot het archief terug is en gaat dan
  verder. Ook telt de eindmelding nooit meer "nieuw" dan er bestanden zijn.
- Video's korter dan een seconde krijgen nu een echte miniatuur (het eerste beeld). Voorheen
  zocht de app alleen het beeld op 1 seconde, zodat zo'n korte clip een blauwgrijs vak kreeg en
  bij Miniaturen maken elke keer als "mislukt" telde.
- Miniaturen maken: bestanden die wel te lezen zijn maar waar geen beeld uit te halen is (een
  kapotte of lege foto, een onbekend formaat, een video zonder beeld) tellen nu apart als
  "onleesbaar" en niet meer als "mislukt". Zo eindigt een herhaalde ronde over je hele archief
  niet elke keer als onvolledig door dezelfde paar kapotte bestanden. Bestanden die de app echt
  niet kan lezen (geen toegang, leesfout, schijf weg) blijven "mislukt".
- Takenpaneel: een taak die je hervatte op het moment dat hij net gepauzeerd werd, liep soms
  verder zonder dat de app hem nog volgde (niet meer te annuleren, en een tweede taak van
  dezelfde soort kon tegelijk starten). Annuleren in datzelfde moment liet de taak op
  "Gepauzeerd" staan. Beide kan niet meer.
- Takenpaneel: de knop "Bekijk" staat alleen nog bij een duplicatenscan waarvan de uitkomst nog
  bewaard is (de app bewaart de laatste vijf). Voorheen gaf "Bekijk" bij een oudere scan alleen
  de melding dat het resultaat niet meer bewaard was.
- De oude scanopdracht (`/api/duplicates/scan_folder`, niet meer door het scherm gebruikt) gaf
  een serverfout als je de scan intussen in het takenpaneel pauzeerde. Nu krijg je een
  duidelijke melding met het nummer van de taak, die je in het takenpaneel kunt hervatten.

### Viewer, chat en bediening
- Viewer: in een map met submappen opende een klik op een foto soms een ander bestand (of een
  map), en dan werkten Verplaatsen en Open in Finder ook op dat verkeerde bestand. Nu opent de
  viewer altijd het bestand waarop je klikt, slaan vorige/volgende de mappen over, en werken
  Verplaatsen en Open in Finder altijd op het bestand dat je ziet.
- Viewer: bladeren kan weer met de muis via de zones links en rechts (ze verdwijnen aan het
  begin en eind van de lijst en tijdens inzoomen, en zitten de videobediening niet in de weg).
  De rode knop "Delete" deed niets en is weg; weggooien gaat via de Keurer (omkeerbaar, naar de
  prullenmand). Een video of geluid van het vorige bestand stopt nu als je verder bladert.
- Viewer: de metadata toont alleen nog echte gegevens. De nep-"ELO Rating 1000" is weg; de
  Shows-waardering staat er alleen bij als je in Shows op die foto gestemd hebt (met het aantal
  stemmen). In de tijdlijn staat de opnamedatum.
- Tijdlijn en chat gebruiken dezelfde viewer met hun eigen lijst. Na een foto uit de tijdlijn
  opende "Open in Finder" in de gewone viewer voortaan de laatste tijdlijnfoto, en de
  pijltjestoetsen toonden foto's uit een ander tabblad. Een foto uit de chat openen maakte
  daarna de Files-weergave kapot (klikken gaf een fout). Alle drie opgelost. Verplaats je een
  foto vanuit de tijdlijn, dan verdwijnt hij daar ook uit de lijst.
- Tijdlijn: na het verplaatsen van een foto klopt nu ook de balk erboven (het aantal foto's en
  het jarenbereik), en verdwijnt een jaar waarvan je de laatste foto verplaatst uit de schuif.
- Verplaatsvenster: na een paar keer openen kon één Enter een bestand naar een andere map
  sturen dan de gemarkeerde (en er gingen meerdere verplaatsverzoeken tegelijk). Nu reageert
  elk venster maar één keer op een toets, en verstuurt dubbel Enter of dubbelklikken nooit een
  tweede verzoek (ook in de Keurer). Tijdens een slideshow schuiven de pijltjestoetsen in het
  verplaatsvenster de show niet meer door, en wordt na het verplaatsen precies die dia uit de
  show gehaald.
- Schermvullende chat: er is nu één berichtenlijst die tussen de gewone en de schermvullende
  chat verhuist. Foto's die Pasha toont verschijnen in de chat die je ziet, blijven staan als
  je terugschakelt en blijven aanklikbaar. Een foto uit de chat opent boven de chat, en Escape
  sluit dan eerst alleen de foto.
- Chat: opende Pasha een foto terwijl je cursor nog in het chatvak stond, dan deden Escape en de
  pijltjestoetsen niets tot je eerst in de foto klikte (en in de schermvullende chat sloot
  Escape helemaal niets). Nu werken ze meteen. Vraag je in de schermvullende chat om een map te
  openen, dan gaat de chat dicht zodat je de map ook echt ziet.
- Meldingen (toasts) staan nu altijd bovenop, ook boven de viewer en de slideshow.
- Spotify: lukte het laden één keer niet (bijv. tijdens een herstart), dan bleef de pagina leeg
  tot je de hele app herlaadde. Nu zie je een melding met de knop "Opnieuw proberen", en de
  pagina werkt gewoon weer zodra het lukt.

### Shows
- Een mapshow toont nu elke foto en video uit de map precies één keer, in een willekeurige
  volgorde waarin foto's met een hogere waardering eerder komen. Voorheen werd er getrokken
  "met teruglegging": ongeveer een derde van de foto's kwam nooit langs en andere twee of drie
  keer.
- Mapshows en "Feeling Lucky" kijken nu naar álle bestanden, niet alleen naar de eerste 500.
  Feeling Lucky houdt nu ook rekening met je stemmen.
- Geluidsbestanden komen niet meer in een slideshow. Video's die de browser kan afspelen
  (mp4, m4v, mov, webm) spelen af; andere videoformaten tonen hun miniatuur, en een video die
  toch niet afspeelt blijft niet hangen maar gaat door naar de volgende dia. Lukt een foto niet
  (bijv. RAW), dan toont de dia de miniatuur.
- De lijst met shows: de map `library` zelf is geen show meer, een map met alleen submappen
  (en bijv. een `.DS_Store`) wordt wel een show met een voorbeeld uit een submap, en één map die
  niet te lezen is laat niet meer alle volgende shows stilletjes verdwijnen.

### Thema en zuinigheid
- Het thema "Auto" volgt nu echt de systeeminstelling van macOS (licht of donker), ook als die
  wisselt terwijl de app open is. Voorheen was Auto altijd donker. Een paar kleuren, randen en
  het lettertype die naar niet-bestaande instellingen verwezen, zijn gerepareerd.
- De bewegende achtergrond (zijbalk en Spotify) draait alleen nog als hij in beeld is, staat
  stil als "Beperk beweging" in de systeeminstellingen aan staat, en tekent hoogstens zo'n 30
  beelden per seconde met veel minder lijnen. Voorheen liep hij altijd door, ook in een ander
  tabblad, met tot ~35.000 punten per beeld.
- De Keurer liet per beoordeelde kaart twee luisteraars achter (na honderden kaarten
  honderden); de klok bij Spotify "Op deze dag" en de wisselende foto's op de homepage lopen
  alleen nog als je dat tabblad open hebt. De tijdlijn toont na het verkleinen of vergroten van
  het venster in een ander tabblad weer het juiste aantal kolommen.

### Opgeruimd (frontend)
- Verwijderd: het ongebruikte `folder-nav.js` (een oude kopie van de mappenboom-navigatie), de
  elf ongebruikte `... Background Removed.png`-afbeeldingen (ca. 2,6 MB) en ongeveer 14 kB
  CSS voor onderdelen die niet meer bestaan (de drie verschillende versies van de schakelaar
  zijn er nu één).
- Nieuw: `code/tests/ui/smoke_webkit.py`, een browsertest die in WebKit (Safari) door alle
  tabbladen klikt en faalt bij elke JavaScript-fout (zie `code/README.md`), en een test die
  controleert dat elke knop een bestaande functie aanroept en elke CSS-kleur bestaat.

### Bestandstypen
- Eén gedeelde lijst met bestandstypen voor de hele app. Daardoor worden `.tif`, `.heif`/`.hif`
  en de RAW-formaten `.cr3` (Canon), `.raf` (Fuji), `.orf` (Olympus) en `.rw2` (Panasonic) nu
  overal herkend: in de tijdlijn, de Keurer, Shows, "op deze dag", zoeken en de duplicaatscan.
  Voorheen toonde de viewer ze wel, maar sloegen de andere onderdelen ze stil over.
- Documenten en andere bestanden (pdf, txt, zip, ...) worden niet meer als "foto" getoond. Ze
  krijgen een bestandsicoon, en de viewer meldt "Geen voorbeeld voor dit bestandstype" in plaats
  van het hele bestand als kapot plaatje te laden.
- RAW-foto's en onbekende bestanden worden met het juiste bestandstype verstuurd (niet meer als
  tekst). Kan de browser een formaat niet zelf tonen (RAW, of HEIC buiten Safari), dan toont de
  viewer de miniatuur.
- Zoeken op bestandsnaam (zoekbalk en chat) vindt nu ook namen met accenten die Finder in een
  andere Unicode-vorm opsloeg, bijvoorbeeld "Café" of "Zürich".

### Miniaturen
- Miniaturen van grote foto's kosten veel minder geheugen en tijd: de foto wordt eerst klein
  ingelezen en pas daarna rechtop gedraaid. Een foto van 48 megapixel kostte ~400 MB geheugen,
  nu ~25 MB. Er worden hoogstens 4 miniaturen tegelijk gemaakt.
- Staande foto's (EXIF-oriëntatie) krijgen nu altijd een rechtopstaande miniatuur, ook als er
  al een scheve in de cache stond: oude miniaturen worden opnieuw gemaakt en de oude cache-
  bestanden worden bij het opstarten opgeruimd.
- Een ander bestand op dezelfde plek (na terugzetten, ongedaan maken of een tweede
  `IMG_0001.jpg`) toont nooit meer de miniatuur van het vorige bestand. Een verplaatst bestand
  houdt zijn miniatuur.
- Een miniatuur die niet gemaakt kon worden (kapot bestand, ffmpeg ontbreekt, schijf even weg)
  wordt niet meer voorgoed als grijs vlak bewaard. De app probeert het later opnieuw; een video
  krijgt zijn miniatuur zodra ffmpeg geïnstalleerd is.
- HEIC-foto's van de iPhone krijgen een echte miniatuur. RAW-foto's die de app zelf niet kan
  lezen, gaan via het ingebouwde macOS-programma `sips`.
- Panorama's en scans boven 179 megapixel krijgen een echte miniatuur in plaats van een grijs vlak.
  Dat geldt nu ook voor JPEG-panorama's boven 600 megapixel: die worden op 1/8 van hun grootte
  ingelezen (een panorama van 624 megapixel kost zo ~35 MB geheugen). Een TIFF- of PNG-scan boven
  600 megapixel gaat via het macOS-programma `sips`, één tegelijk en in een eigen proces, en krijgt
  zo ook een echte miniatuur (tot 1000 megapixel). Voorheen bleef die voorgoed een grijs vlak.
- De map met miniaturen groeit niet meer eindeloos. Wordt hij groter dan 4 GB (ongeveer 200.000
  miniaturen), dan ruimt de app bij het opstarten de miniaturen op die het langst niet getoond zijn,
  zoals die van bewerkte of verwijderde foto's. Alleen de eigen cache van de app; foto's zelf
  worden nooit aangeraakt.
- Video's: ffmpeg zoekt direct naar seconde 1 in plaats van vanaf het begin te decoderen, en kan
  de Terminal waarin de server draait niet meer uitlezen. Clips korter dan een seconde krijgen
  hun eerste beeld.
- Meerdere gelijktijdige verzoeken om dezelfde nieuwe miniatuur maken hem maar één keer en
  krijgen nooit een half geschreven plaatje.

### Datums, tijdlijn en "op deze dag"
- De tijdlijn, "op deze dag" en foto's op datum (chat) gebruiken nu de echte opnamedatum: de
  EXIF-datum van een foto of de opnamedatum die in een video staat. Pas als die er niet is,
  telt de aanmaakdatum van het bestand, en daarna de wijzigingsdatum. Voorheen stonden
  gekopieerde of teruggezette foto's op de kopieerdatum, en gebruikte "op deze dag" een andere
  datum dan de tijdlijn. De viewer toont bij een foto uit de tijdlijn waar de datum vandaan komt.
- Opnamedatums worden één keer per bestand gelezen en bewaard; een gewijzigd bestand wordt
  opnieuw gelezen. Daardoor wordt elke volgende keer snel.
- Kon een bestand even niet gelezen worden (geen rechten, een leesfout, de schijf viel weg), dan
  bewaart de app voor dat bestand geen datum. Voorheen bleef zo'n foto voorgoed op de
  aanmaakdatum staan in plaats van de EXIF-datum, tot het bestand zelf veranderde.
- Mappen met een komma in de naam (bijv. "Parijs, 2019") werken nu in de tijdlijn en in de
  instelling van "op deze dag". Voorheen gaf zo'n map stil 0 foto's.
- Kies je in de tijdlijn een map én een map daarbinnen, dan staat elke foto er nog maar één keer
  in. Mappen die niet meer bestaan worden gemeld in plaats van stil overgeslagen.
- De tijdlijn laadt sneller: het antwoord van de server is ongeveer drie keer zo klein en wordt
  bewaard tot er in een van de mappen iets verandert.
- Kon de tijdlijn een map of bestand niet lezen (geen rechten, een leesfout, de schijf viel
  halverwege weg), dan meldt hij dat de tijdlijn mogelijk onvolledig is en bewaart hij dat
  onvolledige antwoord niet meer. Voorheen bleef zo'n lege of halve tijdlijn staan, ook nadat de
  map weer leesbaar was, tot een herstart.
- Panorama's en scans boven 600 megapixel staan nu op hun EXIF-datum in de tijdlijn en bij "op
  deze dag". Voorheen kregen ze voorgoed hun bestandsdatum, omdat de app hun EXIF niet las.
- "Op deze dag" doet per bezoek een begrensde hoeveelheid werk (eerst wat de app al weet, daarna
  hooguit 300 mappen of 2 seconden) in plaats van soms het hele archief door te lopen. Het toont
  alleen foto's en video's, nooit meer een muziekbestand als grijs vlak.

### Spotify
- De Spotify-geschiedenis wordt nog maar één keer ingelezen en bewaard tot er een exportbestand
  bijkomt of verandert. Voorheen werd bij elke jaarwissel, elk chatbericht en elk bezoek aan Home
  de hele export opnieuw gelezen (seconden werk bij een grote export).
- Staan zowel de gewone export (Account Data) als de uitgebreide export (Extended Streaming
  History) in de map, dan wordt een nummer niet meer dubbel geteld. Ook een tweede kopie van
  hetzelfde exportbestand telt niet dubbel.
- Die vergelijking kijkt nu naar het nummer en de tijd, niet meer naar de artiest: de twee
  exports noemen bij verzamelalbums en samenwerkingen een andere artiest ("Various Artists"
  tegenover de echte artiest), waardoor zulke nummers toch dubbel telden. Speel je een nummer
  twee keer kort na elkaar, dan blijven het twee keer.
- De langste "binge" en "op deze dag" hangen niet meer af van de volgorde van de bestanden.
  Twee verschillende nummers in dezelfde minuut blijven allebei in "op deze dag" staan.
- Een jaar met weinig muziek toont geen artiesten met 0 keer afgespeeld meer, en een ongeldig
  jaar geeft een foutmelding in plaats van stil alle jaren.
- De chat kent nu je totale luistertijd (die kwam door een fout nooit in de context).

### Chat: modelkeuze en duidelijke meldingen
- Je kiest het taalmodel van Pasha nu zelf in een keuzelijst bovenin de chat. De lijst toont de
  modellen die in Ollama geïnstalleerd zijn, en je keuze wordt bewaard. Standaard gebruikt Pasha
  `qwen2.5:3b`: een licht model (1,9 GB) dat snel antwoordt. Het oude, vaste `phi3.5` was op deze
  Mac niet eens geïnstalleerd.
- Naast de keuzelijst staat of de chat werkt: Online, Model ontbreekt of Ollama offline.
- Gaat er iets mis, dan staat er een duidelijke melding in de chat in plaats van eindeloos
  ladende puntjes: Ollama draait niet, het model is niet geïnstalleerd (met het commando om het
  te installeren), Ollama antwoordt niet op tijd, of het antwoord stopte halverwege. Een vraag
  zonder antwoord telt niet meer mee in het gesprek.
- "Duplicaten scannen" vanuit de chat start niet meer zomaar een scan: Pasha toont een knop
  "Scan starten" voor de gekozen map, en pas na je klik start de scan als achtergrondtaak.
- Foto's zoeken of tonen vanuit de chat geeft bij een fout een melding in de chat, in plaats van
  stil te verdwijnen.

### Schijf los, foutmeldingen en wat er op deze Mac ontbreekt
- Is de archiefschijf (T7) niet aangesloten of raakt hij los, dan toont de app bovenin een rode
  balk "Archief niet bereikbaar". Zodra de schijf terug is, verdwijnt de balk en laadt de app
  vanzelf opnieuw wat je zag, zonder de server te herstarten. Dat geldt ook voor de tijdlijn (je
  hoeft de mappen niet opnieuw te kiezen), de Foto Keurer en "Herstellen" bij Duplicaten. Was de
  tijdlijn mislukt terwijl je op een ander tabblad zat, dan scant hij opnieuw zodra je hem opent.
  Een Keurer-reeks die gewoon geladen was, blijft staan waar je was.
- Elk onderdeel dat het archief nodig heeft, geeft dan dezelfde duidelijke melding. Voorheen kreeg
  je afhankelijk van de plek "niet gevonden", een lege lijst, "0 foto's" of een serverfout. Ook
  als de schijf midden in een verzoek wegvalt, komt die melding.
- De app start nu ook als de schijf nog niet aangesloten is. Voorheen bleef dan alles kapot tot
  een herstart, ook nadat de schijf er weer was. De database wordt geopend zodra de schijf er is,
  en de app maakt nooit zelf mappen aan op de plek van de schijf.
- Alle foutmeldingen van de server hebben nu dezelfde vorm, en de pagina toont ze op één manier:
  in gewone taal, met de reden. Dezelfde melding stapelt zich niet meer op. Opslag, "op deze dag",
  Shows, stemmen in een slideshow en het opslagoverzicht melden nu een fout in plaats van stil
  leeg te blijven of te doen alsof het gelukt is.
- Ook de Foto Keurer, het opruimen en terugzetten van duplicaten, de taakbalk rechtsonder,
  verplaatsen, Open in Finder en de Shows-instellingen melden een fout nu op dezelfde manier.
  Valt de schijf weg, dan verschijnt de rode balk meteen (niet pas bij de volgende controle,
  tot 15 seconden later) en krijg je geen losse foutmelding meer.
- Een scan of andere achtergrondtaak starten terwijl de schijf los is, zegt nu "Archief niet
  bereikbaar" in plaats van "Map niet gevonden".
- Ontbreekt er iets op deze Mac (ffmpeg voor video-miniaturen, HEIC-ondersteuning, Ollama of het
  gekozen chatmodel), dan staat er linksonder een rustige melding met hoe je het oplost. Die kun
  je wegklikken.
- Naast `qwen2.5:3b` is ook `qwen2.5:1.5b` (1 GB) geïnstalleerd, zodat je in de keuzelijst kunt
  kiezen tussen een snel en een nog sneller model. Gemeten op deze Mac: een kort antwoord duurt
  0,1 tot 0,3 seconde zodra het model geladen is; het eerste antwoord na een modelwissel duurt
  enkele seconden langer (laden).
