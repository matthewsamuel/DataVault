# Changelog

Alle noemenswaardige wijzigingen aan DataVault Dashboard.

## Fase 3 – Nieuwe functies

Fase 3 voegt de bibliotheek (één index van het hele archief), Albums, de Kaart, Slim zoeken, Beste uit
reeks, Exporteren, het Herstelcentrum, DataGatherer, Pasha met voorstellen, Welke is mooier? en
database-back-ups toe, in een nieuw uiterlijk met licht en donker thema. Wat bewust niet gedaan is,
staat in [docs/NIET_GEDAAN.md](docs/NIET_GEDAAN.md).

### Verificatieronde
- Facebook en Instagram: een nieuwere export van een groep waaruit iemand vertrok of die een nieuwe naam kreeg, komt in hetzelfde gesprek, zonder dubbele berichten.
- Facebook en Instagram: verschillende chats met dezelfde mensen (twee groepen, of chats met 'Facebook-gebruiker') blijven aparte gesprekken; chats die eerder samengevoegd waren, worden bij de volgende import weer gesplitst.
- Bestand kiezen laat in Safari ook NetflixViewingHistory.csv en Safari's History.db kiezen.
- Een Facebook-export wordt herkend, ook als er 'instagram' in een chatnaam staat.
- Google Mijn activiteit als HTML én als JSON geeft geen dubbele items meer; dubbels die er al waren, worden opgeruimd.
- WhatsApp: een nieuwere chat-export met dezelfde bestandsnaam op dezelfde dag wordt geen tweede gesprek ('Anna 2') meer.
- WhatsApp-exports van een Franse Android ('01/06/2024 à 21:34') worden herkend.
- Een grote locatiegeschiedenis (Records.json van 1 GB of meer) wordt stukje voor stukje ingelezen in plaats van overgeslagen; een bestand dat niet gelezen kon worden, staat als waarschuwing op de kaart van de bron.
- Een grote Gezondheid-export gebruikt veel minder geheugen.
- iMessage: reacties met een emoji of sticker (iOS 18) staan bij het bericht, en een ingetrokken bericht toont 'Bericht ingetrokken'.
- Activiteit toont 'Hele dag' bij items zonder tijdstip (Netflix, agendapunten voor de hele dag, stappen) in plaats van een verzonnen uur.
- Kaart: de rij 'Uit je Tijdlijn' staat in dezelfde kolom als de kop en de filters, en is weg als er geen locatiegeschiedenis is.

### Laatste reparaties
- Meldingen van een scherm (bijvoorbeeld 'Bijgewerkt' bij Duplicaten) verdwijnen als je naar een ander scherm gaat.
- Audiobestanden hebben in het raster een rustig vlak met een muziekicoon in plaats van een tekening.
- Kaart: kop en filters staan op dezelfde plek als op de andere pagina's; een lege datum ziet er gedempt uit.
- Het takenpaneel is een klein icoon met het aantal zodra alles klaar is.
- Het indexeren meldt hoeveel mappen zijn bekeken en hoeveel ongewijzigd zijn overgeslagen.

### Pasha: zoeken, tonen en voorstellen met bevestiging

- Pasha kan nu ook zoeken op inhoud (Slim zoeken), je bibliotheek filteren op jaar, maand, camera of map, een slim album openen, foto's van deze dag tonen, foto's bij een stad of land vinden (volledig offline, met de kaartgegevens van de Kaart), je reeksen van bijna-dezelfde foto's laten zien en vertellen hoe het met de achtergrondtaken en het Herstelcentrum staat.
- Het antwoord komt als een strook miniaturen met een knop die het juiste scherm opent met de juiste filters. Ontbreekt er iets (het zoekmodel, de index, de reeksen), dan zegt Pasha dat duidelijk.
- Bestanden verplaatsen, de beste foto van een reeks houden en exporteren stelt Pasha alleen voor. Er verschijnt een kaart met wat er waarheen gaat; pas na jouw klik op Uitvoeren gebeurt er iets, op dezelfde veilige manier als in de schermen zelf (niets wordt overschreven of verwijderd). Verplaatsen staat daarna als een actie in het Herstelcentrum, met terugzetten. Een voorstel geldt tien minuten en één keer.
- Pasha kent geen verwijderen en kan geen mappen buiten het archief gebruiken.

### Bibliotheek: één index van het hele archief
- De app houdt nu een bibliotheek bij: van elke foto, video en elk audiobestand weet hij de
  opnamedatum, de afmetingen, de camera en lens, de GPS-positie en (bij video en m4a) de duur. Dat
  wordt gelezen uit de kop van het bestand en de EXIF-gegevens, zonder de foto zelf te openen
  (ook HEIC; RAW waar dat kan, anders alleen grootte en datum). Volgende functies (filters, slimme
  albums, kaart, exporteren, de assistent) bouwen hierop.
- De eerste keer leest de app het archief één keer helemaal in, op de achtergrond (in de test met
  100.000 kleine bestanden: 14 seconden; de app bleef gewoon reageren). Daarna gaat bijwerken
  razendsnel: alleen mappen die veranderd zijn worden opnieuw gelezen, en daarin alleen nieuwe of
  gewijzigde bestanden. Een ongewijzigd archief van 100.000 bestanden is in 0,03 seconde
  gecontroleerd.
- Bijwerken gebeurt vanzelf: bij het starten van de app, als de schijf weer aangesloten wordt, elk
  half uur, en als de tijdlijn ziet dat er iets veranderd is. Zo'n automatische ronde staat alleen in
  het takenpaneel zolang hij loopt en geeft geen melding als alles goed ging.
- Het takenpaneel rechtsonder laat altijd zien hoeveel bestanden de bibliotheek kent en wanneer hij
  bijgewerkt is, met de knop **Bibliotheek bijwerken**. Die controleert alle mappen en bestanden,
  ook een foto die op zijn plek bewerkt is. Pauzeren, hervatten en annuleren werken zoals bij elke
  taak; wat al gelezen was, wordt bij hervatten overgeslagen. Pauzeer of annuleer je een ronde, dan
  begint de app er zelf geen nieuwe tot je hem opnieuw start of de schijf opnieuw aansluit (of tot
  je zelf op **Bibliotheek bijwerken** klikt). Wordt de schijf losgekoppeld, dan wacht de ronde en
  gaat hij vanzelf verder als de schijf terug is.
- Wordt een ronde onderbroken (app afgesloten, geannuleerd, gepauzeerd of schijf losgekoppeld), dan
  maakt de volgende ronde hem af: een map telt pas als bijgewerkt als alles eronder gelezen is. Een
  map die even niet te lezen was, wordt de volgende keer opnieuw geprobeerd, en tot dan gebruikt de
  tijdlijn voor die plek de schijf zelf.
- Verplaatst de app een bestand (Verplaatsen in de viewer, Keurer, duplicaten, en het terugzetten
  daarvan), dan gaan de bibliotheek, de Shows-waardering en een Keurer-"houden" meteen mee naar de
  nieuwe plek. Een waardering raakt dus niet meer kwijt door een verplaatsing.
- Verplaats je een bestand buiten de app (bijv. in Finder), dan herkent de volgende ronde het aan
  zijn kenmerken (hetzelfde bestand op schijf, of dezelfde inhoud als die al bekend was) en gaat de
  waardering ook mee, ook als je het verplaatst terwijl er net een ronde loopt. Bij twijfel wordt
  niets gekoppeld.
- Een bestand dat van de schijf verdwenen is, wordt eerst alleen verborgen en pas uit de bibliotheek
  gehaald als ook de ronde daarna het nergens terugvindt, en nooit als een map niet gelezen kon worden. De
  bibliotheek leest alleen: hij verplaatst of wist nooit een bestand. Waarderingen en
  Keurer-beslissingen blijven altijd bewaard.

### Tijdlijn, "op deze dag" en foto's per datum
- De tijdlijn haalt zijn foto's nu uit de bibliotheek als die bij is met de schijf: geen wachten meer
  op het doorlopen van alle mappen (100.000 foto's: de eerste foto's staan er binnen een seconde, in plaats
  van na 12 s bij de eerste keer). Is er nog geen bibliotheek, of is er iets veranderd, dan werkt hij zoals voorheen en
  wordt de bibliotheek op de achtergrond bijgewerkt.
- De tijdlijn laadt per pagina: de eerste 3000 foto's staan er meteen, de rest komt erachteraan
  ("… van … geladen"). Het aantal en de jaren kloppen meteen voor de hele selectie; klik je op een
  jaar dat nog niet geladen is, dan springt hij erheen zodra het er is. Alleen een verandering in de
  gekozen mappen laat hem opnieuw beginnen; wordt de bibliotheek elders bijgewerkt, of is hij net
  klaar terwijl de tijdlijn laadt, dan laadt hij gewoon verder. Kon een map niet gelezen worden, dan
  wordt voor het verder laden niet elke keer het hele archief opnieuw doorlopen.
- De tijdlijn tekende bij veel foto's toch elke foto tegelijk (een lijst van 100.000 foto's maakte
  alle 100.000 kaartjes aan, waardoor de pagina minutenlang vastliep). Nu tekent hij alleen wat in
  beeld is.
- "Op deze dag" en "foto's van deze datum" (ook in de chat) zoeken nu in de bibliotheek in plaats van
  een deel van het archief af te lopen, en vinden dus altijd alles. Elke treffer wordt eerst nog op
  de schijf gecontroleerd.

### Albums: filters, slimme albums en op deze dag
- Nieuw tabblad **Albums** (sneltoets `g` `a`). Links staan **Alles**, **Op deze dag** en je slimme
  albums, elk met het aantal bestanden. Rechts zie je alles uit de bibliotheek in één raster.
- Filteren kan op soort (foto's, video's, audio), wanneer (dit jaar, vorig jaar, deze maand, de laatste
  30 dagen, een jaar, of van… tot…), maand, map (met of zonder submappen) en, onder **Meer filters**, op
  camera, bestandstype, locatie, Shows-score (in de woorden van Shows: "Vaker mooi dan niet", "Netto
  3× mooi"), Keurer (gehouden of nog niet gekeurd), liggend of staand en wel of geen opnamedatum.
  Filters zijn te combineren; wat je koos staat als labels boven het raster en gaat met één klik weer
  weg. Sorteren kan op datum, naam, grootte of waardering.
- Ook met 100.000 foto's blijft het vlot: het raster tekent alleen wat in beeld is en haalt de foto's
  per 200 op als je erheen scrolt. Gemeten op 100.000 bestanden: een album tellen 4 tot 25
  milliseconden; een pagina met het aantal erbij 6 tot 100 milliseconden vooraan, en hoogstens zo'n
  140 milliseconden diep in de lijst, ook bij sorteren op waardering of grootte.
- Het adres in de browser volgt wat je ziet, dus een gefilterde weergave kun je als bladwijzer bewaren
  en later precies zo terugkrijgen. Klopt er iets niet in een bladwijzer (een jaar dat geen getal is,
  een album dat er niet meer is), dan laat de app dat stukje weg en zegt dat in een melding.
- Kies je bij **Van… tot…** een einddatum vóór de begindatum, dan staat dat meteen onder de datums;
  het raster blijft gewoon staan. Weigert de app een filter, dan zie je **Filter klopt niet** met
  **Filters wissen**.
- **Bewaar als album** maakt van de gekozen filters een slim album, met een naam en een icoon. Een slim
  album is een bewaarde zoekopdracht: het blijft vanzelf bij en verplaatst of kopieert nooit een
  bestand. Pas je de filters van een album aan, dan kun je de wijziging bewaren of terugzetten; via
  **Album bewerken** kun je het hernoemen, verbergen of verwijderen (je foto's blijven waar ze staan).
  Een verborgen album gaat uit de lijst, met **Ongedaan maken** in de melding. Twee albums hebben
  nooit dezelfde naam, ook niet als je een standaardalbum terugzet ("Video's (2)").
- Er staan zes albums klaar: **Beste foto's** (de hoogst gewaardeerde in Shows), **Video's**, **Zonder
  datum** (alleen de datum van het bestand, geen opnamedatum), **Met locatie**, **Dit jaar** (alleen
  met een echte opnamedatum, dus geen bestanden die dit jaar alleen gekopieerd zijn) en **iPhone**. Je kunt ze verbergen of verwijderen; **Standaardalbums terugzetten** haalt ze terug.
- **Op deze dag** is nu een eigen scherm: alle foto's en video's van vandaag in eerdere jaren, per jaar
  ("7 jaar geleden · 2019"), met knoppen voor de vorige en volgende dag en een keuze voor een andere
  dag. Alleen foto's met een echte opnamedatum tellen mee: een bestand dat alleen de datum heeft
  waarop het gekopieerd is, is geen herinnering. 29 februari is altijd te kiezen, en in een jaar zonder 29 februari laat 28 februari die foto's
  ook zien. Het houdt rekening met de mappen die je op Home koos, en op Home staat bij **Foto op deze
  dag** nu **Alles bekijken**. Heeft een jaar veel foto's, dan opent **Alle … bekijken** precies
  die foto's in het raster: dezelfde mappen en hetzelfde aantal.
- Een foto opent in de gewone viewer; vorige en volgende gaan door alle jaren of door het hele raster
  (de viewer haalt zelf de volgende foto's op). Pijltjes, Home/End, PageUp/PageDown en Enter werken in
  beide schermen.
- Datums zijn overal de tijd waarop de foto genomen is, zoals de camera hem schreef: een foto van
  23:30 op oudejaarsavond hoort bij dat jaar, net als in de tijdlijn.
- Met tien onderdelen past het menu bovenin nog steeds van 1024 px breed af: het zoekveld is wat
  smaller (met de korte tekst "Zoeken…" onder 1100 px) en het woord DataVault verdwijnt eerder.

### Exporteren: een selectie of de beste foto's van een periode
- In Albums en de Tijdlijn selecteer je foto's met een klik met shift of cmd, of met de spatiebalk
  (Esc wist de selectie). Een balk onderaan toont het aantal en de totale grootte; in Albums
  kies je met "Alles selecteren" alles van de huidige filter.
- "Beste van een periode" (knop in Albums) kiest de beste foto's van een jaar, maand of eigen
  periode, eventueel binnen een map of slim album: op waardering in Shows, "gehouden" in de Keurer
  en (als die er is) de kwaliteitsscore, hoogstens één foto per burst of reeks en verspreid over de
  periode. Je ziet eerst een voorbeeld en kunt foto's weglaten.
- Exporteren kopieert naar een map buiten het archief (standaard Afbeeldingen/DataVault Export), als
  achtergrondtaak met voortgang, annuleren en hervatten. Het archief verandert nooit en er wordt niets
  overschreven (een bestaande naam krijgt " (2)"). Vooraf wordt de totale grootte berekend en de export
  geweigerd als er te weinig ruimte is. Mappen binnen het archief en systeemmappen worden geweigerd.
- Opties: originelen (exact dezelfde bestanden, datum behouden) of verkleind als JPEG (langste zijde
  1024, 2048 of 4096 px, EXIF en staande stand behouden, HEIC wordt JPEG); alles in één map, per maand
  of zoals in het archief; een overzicht (CSV of JSON) met de bronpaden. "Toon in Finder" opent de map.

### Herstelcentrum: alles terugzetten op één plek
- Nieuw tabblad **Herstel**: één lijst, nieuwste eerst, van alles wat de app in je archief verplaatste:
  Keurer (prullenmand en verplaatsen), duplicaten opruimen en Verplaatsen in de viewer. Per actie zie
  je wat waarheen ging en wanneer, met een knop **Terugzetten**. De app wist nooit iets; elk bestand
  kan terug naar zijn oude plek.
- Kan iets niet terug, dan staat erbij waarom: het bestand staat niet meer in de prullenmand of de
  doelmap, er staat nu een ander bestand met dezelfde naam, of op de oude plek staat al een bestand.
  Er wordt nooit iets overschreven en nooit een ander bestand "teruggezet".
- Verplaatsen in de viewer kon tot nu toe niet ongedaan worden; dat kan nu ook, vanuit het
  Herstelcentrum. Na het verplaatsen zegt de melding waar je het terugvindt.
- Bij Duplicaten staat naast Herstellen een knop naar het Herstelcentrum.
- Ook met grote opruimacties (duizenden duplicaten per keer) opent het Herstelcentrum meteen: de
  lijst controleert de getoonde bestanden en verder alleen tot er één terug kan; de rest controleert
  het terugzetten zelf. De lijst zegt dan hoeveel bestanden al gecontroleerd zijn.
- Volgende functies die bestanden verplaatsen (zoals "beste bewaren" bij bijna-duplicaten en de
  assistent) komen vanzelf in dezelfde lijst.

### Toetsenbord
- Eén vaste set sneltoetsen in de hele app; **?** toont ze allemaal:
  - **1** tot **9** of **g** + letter (g b Bestanden, g t Tijdlijn, g r Herstel, ...) gaat naar een tabblad;
  - **/** gaat naar het zoekveld en **t** wisselt het thema (Auto, Licht, Donker);
  - in de viewer: **←** **→** bladeren, **+** **−** **0** zoomen, **m** verplaatsen en **f** toon in Finder.
- Het raster van Bestanden en de tijdlijn werken met de pijltjes, Home/End en PageUp/PageDown; Enter
  of spatie opent een map of foto, Backspace gaat in Bestanden een map omhoog. In de tijdlijn blijft
  de kolom gelijk als je met ↑ ↓ over een maand heen gaat.
- Geen sneltoets doet iets terwijl je in een invoerveld typt, of samen met Cmd, Ctrl of Alt. Ook de
  Keurer-toetsen niet: een spatie in het zoekveld opent geen voorbeeld meer.
- De pijltjes in het themamenu kiezen alleen een thema. Op het Keurer-tabblad beslisten ze ook over
  de foto erachter (naar de prullenmand, terugdraaien, verplaatsen); dat kan niet meer. De Keurer
  reageert op pijltjes en spatie alleen als de focus op de Keurer zelf staat (of nergens, of op het
  Keurer-tabblad). Een pijltje op een ander tabblad (bijvoorbeeld Tijdlijn) beslist niets, en spatie
  op een Keurer-knop (Houden, Kies map, ...) drukt die knop in in plaats van het voorbeeld te openen.
- In een venster om een map te kiezen doet Enter op **Annuleren** of **×** wat die knop zegt: het
  venster sluit en er wordt niets verplaatst. Alleen Enter in de mappenlijst of het zoekveld kiest de
  gemarkeerde map.
- Na **Terugzetten** in het Herstelcentrum blijft de focus in de lijst (op dezelfde of de volgende
  actie) in plaats van naar het begin van de pagina te springen.
- In Safari gaat Tab op de pagina niet langs knoppen; het overzicht (**?**) noemt nu **⌥ Tab** (of de
  instelling in Safari > Instellingen > Geavanceerd).
- Elk venster houdt de focus binnen zich (Tab gaat rond), **Esc** sluit alleen het bovenste venster,
  en daarna staat de focus weer waar hij was: op de foto in het raster of in het chatvak. Na het
  sluiten van een foto die vanuit de chat geopend werd, kun je dus meteen verder typen.
  Ook in Safari: daar sloeg Tab knoppen over en kwam de focus zo op de pagina achter het venster.
- Overal een duidelijke focusrand als je met het toetsenbord werkt. Het themamenu, de mapkeuze van
  de tijdlijn, de opslagwidget, Shows-kaarten en de Spotify-tegels werken ook met het toetsenbord.

### Instellingen
- Nieuw: **Instellingen** (het tandwiel rechtsboven, of 9). Eén plek voor het thema, het taalmodel van
  Pasha, de bibliotheek (stand en "Alles controleren"), de Shows-bronnen en een link naar het
  Herstelcentrum. Volgende onderdelen (kaart, zoeken op beeld, back-ups) krijgen hier hun eigen stuk.

### Duidelijker scherm
- Lege schermen zeggen wat er aan de hand is en wat je kunt doen ("Deze map is leeg", "Niets
  gevonden", "Kies mappen voor je tijdlijn"); iets dat niet laadde toont de reden en **Opnieuw
  proberen**.
- Tijdens het laden staan er grijze plaatshouders in de vorm van wat er komt, in plaats van een leeg
  vlak of een draaiend rondje.
- Meldingen tonen de titel met een sluitknop en daaronder de tekst. Fouten en waarschuwingen blijven
  langer staan, met de muis erop blijft een melding staan, en een fout wordt meteen voorgelezen.
- De rest van de app is nu ook Nederlands (tabbladen, knoppen, vensters, maanden in de tijdlijn,
  Spotify-koppen). Knoppen met alleen een icoon hebben een uitleg bij de muis en voor schermlezers.
- Het opslagoverzicht toont groottes als KB, MB of GB: een map van een paar MB staat er nu gewoon in
  (was 0,00 GB of ontbrak), net als kleine bestanden bij de grootste bestanden. Het venster "Opslag
  per map" begint weer bovenaan (het totaal viel half buiten beeld) en gebruikt de hele breedte.

### Licht en donker thema
- Elk scherm, venster en het takenpaneel zijn in beide thema's nagelopen. Alle tekst haalt nu het
  WCAG AA-contrast (minstens 4,5:1). Het groen in het lichte thema is donkerder (het oude groen was als
  tekst slecht leesbaar), grijze en rode tekst zijn bijgesteld, en de kop "Collecties" bij Shows was in
  het lichte thema wit op wit.
- Het thema kies je ook met het toetsenbord (menu met pijltjes, of **t**) en in Instellingen.

### Nieuw uiterlijk: modern, met een groen accent
- De hele app heeft een rustiger, moderner uiterlijk. De gewone systeemletter van de Mac voor alle
  tekst; de mono-letter alleen nog voor het DATAVAULT-logo, getallen en paden. Geen hoofdletters met
  extra spatiëring meer, maar gewone zinnen.
- Het felle neongroen is een rustiger groen dat in beide thema's goed leesbaar is. Het groen staat
  alleen nog op de belangrijkste knop, het actieve menu-onderdeel, selecties, focus en voortgang.
- Licht is bijna wit met witte kaarten en zachte schaduwen; donker is diep grijs (niet zwart), met
  dunne randen en iets lichtere kaarten.
- Afgeronde hoeken, meer ruimte en overal dezelfde knoppen, invoervelden, keuzelijsten (zonder de
  systeemknop), schakelaars, filterpillen, kaarten, vensters, menu's en meldingen.
- Bovenbalk: onderdelen met een groen streepje onder het actieve (ook "Herstelcentrum" voluit), een
  duidelijk zoekveld met de sneltoets **/**, en de themakeuze als een klein menu. Op een smaller
  scherm past alles nog. Het zoekveld zoekt in de map die bij Bestanden open is; op een ander
  scherm zie je dat aan "Zoek in Bestanden…" en brengt **Enter** je daarheen met je zoekopdracht.
  Vind je niets, dan wist "Zoekopdracht wissen" hem weer.
- Elke pagina heeft dezelfde breedte en dezelfde kop (icoon, titel, één regel uitleg): de titel
  springt niet meer heen en weer als je van tabblad wisselt. Ook de Keurer en de Tijdlijn hebben
  zo'n kop.
- Home begint met een begroeting en de datum, daarna "op deze dag" (foto en Spotify naast elkaar),
  tegels met het aantal foto's, video's en audio en de opslag, en Pasha in chatballonnen met zijn
  hondenkopje naast elk antwoord. Pasha's kaart is zo hoog als de berichten, met tot het eerste
  bericht drie voorbeeldvragen om op te klikken.
- Is de schijf losgekoppeld, dan tonen de "op deze dag"-kaarten een rustige melding (geen tekst
  meer die in het laadrondje meedraait) en de opslagtegel een streepje met de reden eronder. Is er
  niets van deze dag, dan zegt de kaart dat met een knop ("Naar Spotify", "Mappen kiezen").
- Getallen en datums overal op z'n Nederlands: "448 GB", "12,1 GB vrij", "1,2 uur",
  "Recorddag: 11 feb 2023".
- Bestanden: het pad begint bij het archief ("Archief › RAW › 2019") in plaats van bij de schijf.
  Foto's zijn even grote, afgeronde tegels die licht omhoog komen onder de muis en zacht verschijnen
  als ze geladen zijn. Mappen en bestanden hebben iconen in plaats van emoji. Bestanden en Tijdlijn
  gebruiken nu precies dezelfde fototegel.
- Ook de viewer, Duplicaten, het vergelijkvenster en de opruimbevestiging tonen paden vanaf het
  archief ("RAW/misc/strand.jpg"); het volledige pad staat in de tooltip en achter Kopieer.
- De viewer toont de foto groot op een donkere achtergrond (ook in het lichte thema), met de gegevens
  rustig in een paneel rechts en ronde knoppen om te zoomen en te bladeren.
- Keurer: de foto staat centraal en de pagina is precies zo hoog als het venster, dus de knoppen
  en de sneltoetsen (als toetsjes) staan ook op een laptopscherm (1280×720, 1024×768) zonder
  scrollen in beeld. De map en het aantal dat nog te doen is staan rechts in de kop.
- Tijdlijn: de maandkoppen vallen niet meer over de foto's heen, en de foto's zijn vierkant bij
  elke vensterbreedte, net als in Bestanden.
- Spotify houdt zijn eigen groen, met iconen in plaats van emoji (ook in de vensters "Jouw
  luister-DNA" en "Uitleg"), en de luistertypes en uitleg in het Nederlands ("Vroege vogel", "Brede
  ontdekker"). De grafieken "Per maand" en "Per uur" hebben maanden en uren eronder, en één
  tooltip per staaf. De uitgelichte show heet nu "Verras me".
- Vensters hebben in het donkere thema weer zichtbare scheidingslijnen, en elke venstertitel heeft
  één rustig icoon op dezelfde plek.
- In het venster Bestand verplaatsen zie je met de pijltjes welke map gemarkeerd is (groene ring),
  en die map staat dan ook bij "Naar" met de knop aan: Enter en de knop doen wat je ziet. Ook de
  keuzes in het themamenu krijgen met het toetsenbord de groene ring.
- Het takenpaneel rechtsonder klapt vanzelf in als er niets meer loopt, is nooit hoger dan 40% van
  het venster, en toont van de afgeronde taken alleen de nieuwste; de rest staat op één regel
  ("3 eerder klaar") die je kunt openklappen of in één keer verbergen. Zo bedekt het geen knoppen
  meer. "1 groepen" heet nu "1 groep".
- Een taak die klaar is maar niet alles kon lezen (bijvoorbeeld een bestand zonder leesrechten),
  klapt het takenpaneel niet meer weg en verdwijnt niet onder "eerder klaar": hij staat er als
  "Onvolledig" in de waarschuwingskleur, en de kop zegt "1 onvolledig". Kon de laatste ronde van de
  bibliotheek (ook een automatische) niet alles lezen, dan staat dat onder de bibliotheekregel.
- Bestanden begint niet meer met een lege pagina, maar met de bovenste mappen van het archief
  (data, library, RAW). "Archief" in het pad bovenaan brengt je daar terug, en Backspace in een
  bovenste map ook. Open je een map via het raster of het pad, dan klapt de mappenkolom mee open en
  staat die map daar gemarkeerd.
- De lijstweergave van Bestanden heeft rijen over de hele breedte met een dunne lijn ertussen, en
  rustige kolommen voor soort, grootte en datum.
- Het venster Bestand verplaatsen toont "Huidige map" en "Naar" als pad vanaf het archief
  ("RAW/2019/zomer vakantie"), net als de viewer ernaast.
- Pasha op volledig scherm: de invoer staat weer onderaan het venster in plaats van midden in een
  leeg vlak, en de voorbeeldvragen staan er ook.
- Is de schijf losgekoppeld, dan zeggen de tegels Foto's, Video's en Audio alle drie "Bibliotheek
  niet bereikbaar".
- Knoppen rechts in een paginakop staan op de hoogte van de titel en zijn overal even groot. De
  Keurer zegt "Map kiezen" zolang er nog geen map gekozen is.
- Meldingen hebben een neutrale titel met een icoon voor het soort melding (gelukt, fout,
  waarschuwing, info) in de kleur van de balk links.
- Het sneltoetsenoverzicht is evenwichtiger: "Overal" staat over de hele breedte in twee kolommen,
  en een toetscombinatie (zoals de vier pijltjes) breekt nooit meer over twee regels.
- Spotify: topartiesten en topnummers tonen weer alles wat er is (een eerdere versie kortte de
  langste lijst in tot de lengte van de kortste, waardoor nummers wegvielen). De twee kaarten zijn
  even hoog; de kortste lijst eindigt met een rustige regel als "Dat zijn alle 6 artiesten in deze
  periode". De labels onder de tegels staan op één lijn, en in "1,2 uur" staat "uur" in de gewone
  letter. De voortgangsbalk van de diavoorstelling is groen.
- Mono alleen nog voor getallen en paden: "68,1% van deze map" en "Geen" (geen map gekozen) staan
  in de gewone letter.
- In een smal venster (onder ca. 900 px, bijvoorbeeld Split View) schuift het menu bovenin opzij in
  plaats van onder het zoekveld te verdwijnen, en blijven de foto's in de Tijdlijn vierkant en op
  één lijn met de maandkoppen.
- De achtergrondgolven in de mappenkolom zijn weg; alleen Spotify heeft ze nog.
- Beweging is kort en subtiel, en staat helemaal stil als je in macOS "Beperk beweging" aan hebt.
- Bestanden: klik je een map aan en meteen daarna op "Archief" (of Backspace), dan blijven het pad
  bovenaan en de mappenkolom op "Archief" staan, ook als die map pas later klaar is met laden.
- Bestanden toont geen lege pagina meer als het archief bij het openen niet bereikbaar is, maar
  "Archief niet bereikbaar" met "Opnieuw proberen" (ook in de mappenkolom); komt de schijf terug,
  dan verschijnen de mappen vanzelf. Een archief zonder mappen zegt "Het archief is leeg".
- Bestanden: in de lijst staan mappen en bestanden op dezelfde lijn (icoon, naam, soort, grootte,
  datum). In het raster zie je de naam van een foto of video onderaan de tegel als je erop wijst of
  er met het toetsenbord op staat, zodat bijna gelijke burstfoto's uit elkaar te houden zijn.
- Keurer: een map zonder foto's of video's (bijvoorbeeld alleen submappen) zegt "Geen foto's of
  video's in deze map" met "Andere map", in plaats van een groen "Map volledig beoordeeld". De
  labels ("Dagen op rij", "Vandaag", "Totaal beoordeeld") en de ongedaan-melding beginnen met een
  hoofdletter.
- Home: de foto van "op deze dag" open je ook met het toetsenbord (Enter of spatie), met de groene
  focusring; zolang de foto de focus heeft, wisselt hij niet.
- Diavoorstelling: titel, gegevens en voortgangsbalk staan boven en onder de foto in plaats van
  eroverheen, op elke schermhoogte. Het getal met de ster heet nu "Score" (tooltip en schermlezer).
- Paden zien er overal hetzelfde uit: vanaf het archief ("RAW/2022/bursts"), lange paden in het
  midden ingekort ("RAW/…/zomer/dag 2"), in de mono-letter, met het volledige pad in de tooltip. Zo
  ook in de Keurer en het Herstelcentrum. Instellingen > Over deze app toont het volledige pad op
  de schijf, ook in mono.
- Instellingen: de themakeuze is dezelfde schakelaar als Raster/Lijst, de knoppen zijn even groot
  als elders, en het tandwiel krijgt als het actief is hetzelfde groene streepje als de andere
  menu-onderdelen. Tussen het menu en het zoekveld blijft bij elke breedte vanaf 1024 px ruimte.
- Duplicaten: de uitleg boven de groepen is opgesplitst in een regel met de twee labels (en rechts
  wat er zichtbaar opgeruimd wordt) en één rustige regel met de gekozen regel.
- Het takenpaneel rechtsonder bedekt geen inhoud meer: de pagina houdt onderaan zoveel ruimte vrij
  als het paneel hoog is, dus ook de laatste tegel of het laatste chatbericht scrolt eronder vandaan.
- Bronnen beheren: de mappen staan op één lijn met de tekst erboven. Het sneltoetsenoverzicht
  scrolt in een laag venster binnen het venster, met de voetnoot altijd heel in beeld. In het
  themamenu zie je ook na openen met de muis welke keuze de pijltjes gemarkeerd hebben.

### Kaart: foto's op de plek waar ze genomen zijn

- Nieuw tabblad **Kaart** (sneltoets `g m`): al je foto's en video's met een GPS-positie in EXIF,
  gegroepeerd met aantallen. Klik op een groep om in te zoomen, klik op een foto voor een
  voorbeeld met datum en open hem in de gewone viewer; vorige en volgende bladeren door de
  foto's op die plek. De kaart past zich bij het openen aan je foto's aan.
- Filterbalk voor jaar, van-tot-datum, camera en slim album, met "Foto's zonder locatie: N"
  (een klik toont ze in Albums). Werkt met het toetsenbord: Tab naar de kaart, pijltjes om te
  schuiven, plus en min om te zoomen, Enter op een groep of foto.
- In de viewer staat bij een foto met locatie een knop **Op kaart tonen**.
- De basiskaart werkt zonder internet: landsgrenzen en grote steden van Natural Earth
  (publiek domein), in de kleuren van het lichte of donkere thema.
- **Gedetailleerde kaart (OpenStreetMap, online)**: standaard uit, aan te zetten op de Kaart of
  in Instellingen. Alleen dan haalt de browser kaarttegels van OpenStreetMap via internet
  (met bronvermelding); zolang het uit staat verlaat er geen enkel verzoek je Mac.
  De keuze wordt bewaard in de app-instellingen.
- Een GPS-positie van precies 0,0 (geen fix), buiten bereik of met alleen een breedte of
  lengte telt als "geen locatie"; ook breuken als (teller, noemer) worden nu gelezen.
- Techniek: `GET /api/map/points` (compacte rijen in stukken, filters van Albums, `bbox`),
  `/api/map/items`, `/api/map/locate`, `/api/map/settings`; Leaflet 1.9.4 en
  Leaflet.markercluster 1.5.3 staan met licentie in `frontend/vendor/`. Geen databasewijziging.

### Beste uit reeks: bijna-duplicaten en bursts
- Nieuw tabblad **Beste uit reeks** op de pagina Duplicaten (naast **Exacte kopieën**). Het vindt foto's
  die op elkaar *lijken*: een verkleinde of opnieuw opgeslagen kopie (zoals een foto uit WhatsApp), een
  licht bewerkte versie, en bursts (een paar opnames met dezelfde camera binnen een paar seconden, in
  dezelfde map).
- **Foto's vergelijken** werkt eerst de bibliotheek bij en bekijkt dan op de achtergrond elke foto één
  keer, in een kleine versie (ook HEIC). Daarna gaat het alleen nog om nieuwe of veranderde foto's.
  Pauzeren, hervatten en annuleren werken zoals bij elke taak, en wordt de schijf losgekoppeld, dan
  wacht de taak tot hij terug is. Video's worden niet vergeleken, en RAW-bestanden die de app niet kan
  openen worden overgeslagen (het scherm zegt hoeveel).
- Per reeks staan de foto's naast elkaar, met de beste voorgesteld (**Beste**). Die keuze komt uit een
  score van 0 tot 100: scherpte, resolutie, belichting (geen weggevallen schaduwen of hoge lichten) en
  jouw eigen keuze (Keurer "houden" en de Shows-waardering); bij gelijke stand het grootste bestand.
  Onder de foto's staat waar de score vandaan komt. Een wazige kopie verliest het altijd van de scherpe,
  en een verkleinde kopie telt nooit als scherper dan het origineel (scherpte wordt bij elke foto op
  dezelfde grootte gemeten; daarom worden alle foto's één keer opnieuw vergeleken).
- **Groot bekijken** (of de toets **v**) zet de foto's van een reeks groot naast elkaar. Met **100%** (of
  **d**) zie je van elke foto dezelfde uitsnede op ware grootte, zodat een verkleinde of wazige kopie echt
  zachter oogt; slepen verschuift de uitsnede van alle foto's tegelijk. Ook daar kies je wat blijft.
  Reeksen van twee tot vier foto's krijgen in de lijst grotere foto's, en een foto die weggaat is nog goed
  te zien.
- Je kiest zelf wat blijft, ook meerdere foto's. **Bevestigen** vraagt eerst hoeveel bestanden waarheen
  gaan, en verplaatst de rest dan naar `RAW_DUPLICATES/near-duplicates`, in dezelfde mappen als nu. Er
  wordt niets gewist: elke reeks is één actie in het Herstelcentrum en gaat in één keer terug, met
  dezelfde inhoud en datums. De vraag vooraf begint op **Annuleren**: twee keer Enter verplaatst niets.
  **Overslaan** laat een reeks voor nu liggen; **Alles behouden** laat alles staan en de reeks komt niet
  meer terug (tot er een foto bij komt; in het tabblad kun je ze weer tonen). Een **Alles behouden** maak
  je meteen ongedaan met **Ongedaan maken** boven de lijst.
- Na het laatste besluit zegt de lijst wat er is: **Alles bekeken**, of **Alle reeksen overgeslagen** met
  **Overgeslagen tonen**. Er worden geen reeksen meer overgeslagen bij het verder laden na een besluit,
  en het laden gaat vanzelf als je naar het einde scrolt of met ↓ voorbij de laatste reeks gaat.
- Met de schuif **Strengheid** kies je hoe ver foto's mogen afwijken: **Streng** (vrijwel dezelfde foto, en
  bursts met bijna geen beweging),
  **Normaal** of **Ruim** (ook iets bijgesneden of lichter gemaakt). Volgorde: grootste reeksen eerst of
  nieuwste eerst; beide worden onthouden.
- Exacte kopieën (byte voor byte gelijk) blijven bij **Exacte kopieën** horen: hier tellen ze als één foto
  met "+2 exacte kopieën" erbij, en ze gaan alleen mee als die foto weggaat. Zolang van een mogelijke kopie
  nog niet vaststaat of hij byte voor byte gelijk is (een vergelijking die gepauzeerd of gestopt is), staat
  hij niet in een reeks. Dat herkennen ("Exacte kopieën herkennen") toont voortgang, bewaart wat klaar is
  en gebruikt wat **Exacte kopieën** al eerder las.
- Een map met honderden foto's die er precies hetzelfde uitzien (bijvoorbeeld kopieën van één opname)
  wordt één groep, in delen van hoogstens 48 foto's, zonder dat het vergelijken daar traag van wordt.
- Volledig met het toetsenbord: ← → naar de vorige of volgende foto, ↑ ↓ naar de vorige of volgende
  reeks, Home/End eerste of laatste foto, spatie houden of laten gaan, v groot bekijken, Enter
  bevestigen, s overslaan, a alles behouden (ook in het overzicht onder **?**). **g** en een letter
  wisselt ook vanaf een foto van pagina. De tabbladen werken met ← →.
- Vóór de eerste vergelijking staat er één knop **Foto's vergelijken**; terwijl er vergeleken wordt, zegt
  de lijst dat zonder tweede knop.
- Snelheid op deze Mac: een gewone 12 MP JPEG kost ongeveer 8 ms (130 foto's per seconde); HEIC is
  trager omdat de hele foto gelezen wordt. Het zoeken van reeksen in 100.000 foto's duurt daarna een
  paar seconden.

### DataGatherer: je eigen gegevens bijwerken
- Nieuwe tab **DataGatherer** (het wolkje naast Instellingen, sneltoets g a): één kaart per bron,
  gegroepeerd per categorie. Per kaart zie je tot welke datum je gegevens gaan en hoeveel dagen je
  achter zit, een balk met wat gedekt is (gestreept waar de luistertijd een schatting is), de
  volgende stap in één zin, één hoofdknop (Verbinden, Bijwerken, Export aanvragen of Inlezen),
  hulp van makkelijk naar moeilijk, en met welke servers de bron verbinding maakt.
- **Geen wachtwoorden, geen bots.** Een export vraag je zelf aan in je eigen browser; DataVault
  opent alleen de juiste pagina en leest het bestand daarna in. Sleutels (tokens, API-sleutels)
  staan alleen in de Sleutelhanger van macOS. Per bron is er **Vergeet dit account**.
- **Alleen online als jij het wilt.** DataVault maakt alleen verbinding als jij op een knop drukt,
  of voor een bron waarvan je zelf het schema aanzette (standaard uit). Het mag dan alleen naar de
  officiële servers van die bron. Elke verbinding, ook een geweigerde, staat in het **Netwerklog**.
- **Exports blijven onaangeroerd.** Een export wordt gekopieerd naar
  `data/Matthew/<Bron>/<datum> <soort>/`, nooit verplaatst of overschreven, en dezelfde export
  nooit twee keer. Je bestaande mappen ("Spotify Extended Streaming History", "RAW_google") worden
  ter plekke herkend en blijven waar ze staan.
- **Downloads in de gaten houden.** Zolang je een export aangevraagd hebt, kijkt DataVault elke
  30 seconden in Downloads. Het wacht tot de download klaar is en herkent de export aan de inhoud.
  Daarna kopieert het hem naar de T7 en leest hem in. Het origineel blijft in Downloads staan; de
  kaart zegt dat je het mag weggooien.
- **Spotify**: je Extended Streaming History en Accountgegevens worden ingelezen, met de
  Spotify-ID van elk nummer. Een nummer dat tussen twee exports een andere titel kreeg, telt zo
  niet meer dubbel. Twee keer dezelfde export inlezen voegt niets toe.
- **Spotify tot gisteren**: via de Spotify-API (Verbinden, met een stappenplan voor je eigen gratis
  ontwikkelaarsapp; Premium nodig) haalt Bijwerken je laatste 50 nummers op. Zet je het schema aan,
  dan gebeurt dat elk half uur. In Instellingen > DataGatherer kun je een achtergrondtaak (launchd)
  aanzetten, die ook ophaalt als DataVault dicht is. Elke 6 maanden vraagt Spotify opnieuw om
  toestemming: de kaart waarschuwt 14 dagen vooraf en toont daarna "Opnieuw verbinden".
- **Last.fm** vult aan wat de Spotify-API niet meer teruggeeft, met je gebruikersnaam en een
  API-sleutel. De eerste keer haalt het je hele geschiedenis op, daarna alleen wat nieuw is.
- **Samengevoegd, nooit dubbel**: komt een nieuwe export binnen, dan gelden de plays van de API en
  Last.fm uit die periode als vervangen. Ze worden gemarkeerd, nooit gewist. Het Spotify-scherm leest
  de samengevoegde gegevens en toont "Bijgewerkt t/m … · luistertijd na … is een schatting". Pasha
  krijgt geen plays van de Spotify-API: dat staan de voorwaarden van Spotify niet toe.
- **Google Takeout**: de knop opent Takeout. Een Takeout-zip in Downloads wordt herkend en
  gekopieerd, en je bestaande Takeouts in RAW_google worden geregistreerd. De inhoud wordt in een
  volgende versie ingelezen.
- Een nieuwe bron is één Python-bestand; zie `docs/DATAGATHERER_CONNECTORS.md`.
- **Je statistieken krimpen nooit meer.** Het Spotify-scherm las de samengevoegde gegevens al zodra
  er iets in stond: na één upload, de eerste keer Bijwerken of een afgebroken eerste inleesbeurt
  zag je maar een deel van je geschiedenis. Nu blijft het je exportbestanden lezen tot elk
  exportbestand ook echt ingelezen is; het label zegt dan "je nieuwste gegevens zijn nog niet
  ingelezen", met een knop Inlezen.
- **Gaten zie je.** Ligt er tijd tussen je laatste export en je nieuwste nummers van de API, dan
  zegt de kaart "Gat van … dagen", noemt de periode en raadt een nieuwe export aan. De balk laat die
  periode leeg, en het Spotify-scherm zegt "schatting vanaf …" en "geen gegevens van … tot …".
- **Bestand kiezen** werkt nu ook met een losse `StreamingHistory*.json` of
  `Streaming_History_Audio_*.json`. Kopiëren naar het archief loopt als taak, met voortgang en
  Annuleren. Hetzelfde bestand twee keer tegelijk kiezen geeft nooit meer een extra kopie.
- **In Downloads zoeken** vindt elke export bij de juiste bron, ook als er een Spotify-zip en een
  Takeout-zip tegelijk in Downloads staan.
- Een vergrendelde Sleutelhanger (of "Weigeren" bij de vraag van macOS) laat de tab niet meer
  vastlopen: de kaart zegt wat er is. Een export met een vreemd getal erin slaat die ene regel over;
  een export die echt niet te lezen is, wordt als mislukt gemarkeerd en de rest gaat door.
- **Duidelijkere kaarten**: lukte Bijwerken niet, dan staat de reden in rood op de kaart (en in het
  Netwerklog waarom een verbinding geweigerd werd). "Meer" werkt als een echt menu: pijltjes,
  Home/End, Escape, klikken ernaast sluit het, en na een venster komt de focus terug op "Meer". De
  knoppen van een kaart blijven op één regel, een filter kiezen met het toetsenbord houdt de focus,
  lege schermen hebben een knop om verder te gaan, en de teksten zijn overal Nederlands
  ("luisterbeurten", "API-sleutel", "19 sep 2023", "meestal binnen 2 dagen").
- Een export die je in een al ingelezen map uitpakt wordt herkend: de kaart biedt dan Inlezen aan
  (ook altijd in het menu Meer) en de Spotify-pagina blijft zolang op de bestanden.
- Een paar dagen niet luisteren geldt niet meer als gat; alleen het stuk tussen een export en je
  eerste latere nummer kan echt ontbreken.
- Een geannuleerde upload laat geen tijdelijke kopie meer achter.
### DataGatherer: Google, locatie, browsers en meer bronnen
- **Google Takeout wordt nu echt ingelezen**, ook je bestaande Nederlandstalige Takeouts in
  `RAW_google` (ter plekke, niets wordt verplaatst of uitgepakt): Mijn activiteit (YouTube,
  Zoeken, Maps, ook de HTML-versie met Nederlandse datums), je YouTube-kijk- en zoekgeschiedenis,
  Chrome, Agenda en Keep. Nederlandse en Engelse mapnamen werken allebei, en hetzelfde filmpje uit
  twee Takeouts telt maar één keer.
- **Takeout volgens schema**: stel bij Google Takeout een map in om op te volgen (bijvoorbeeld je
  Google Drive-map met Takeout-exports). Elke nieuwe export daarin kopieert DataVault vanzelf naar
  het archief, ook zonder dat je eerst op Export aanvragen klikt.
- **Nieuwe bron: Google Maps Tijdlijn.** Exporteer je Tijdlijn op je iPhone naar een iCloud
  Drive-map en vul die map in bij de instellingen; DataVault leest elke nieuwe export in. De oude
  Tijdlijn uit Takeout (Records.json en Semantic Location History) en de nieuwe export van de
  telefoon (iPhone en Android) worden herkend aan de inhoud en samengevoegd zonder dubbels.
- **Locatie geschat uit Tijdlijn**: foto's zonder GPS krijgen een geschatte plek uit je
  locatiegeschiedenis rond het moment van de foto (hoogstens 30 minuten ernaast, met hoe zeker
  dat is). Dat staat alleen in DataVault, **nooit in je fotobestanden**. Op de Kaart zet je de laag
  "Locatie geschat uit Tijdlijn" aan (een open groene ring), en in de viewer zie je
  "Locatie (geschat)". Tijdzones: de tijdzone uit de foto als die er is, anders die van de plek
  (offline bepaald), anders Brussel met zomertijd.
- **Route per dag op de Kaart**: kies een dag en zie je route en de plekken waar je was, met
  knoppen naar de vorige en volgende dag met locatie.
- **Nieuw scherm: Activiteit** (in DataGatherer, of g c): per dag wat je deed, met filters op bron
  en soort en zoeken in de titels. Websites, YouTube, zoekopdrachten, agenda, notities, Netflix,
  TikTok, trainingen en meer staan samen op één tijdlijn.
- **Chrome- en Safari-geschiedenis** van deze Mac: Bijwerken leest een kopie van de database (de
  browser mag open blijven) en voegt alleen nieuwe bezoeken toe. Safari vraagt Volledige
  schijftoegang; de kaart zegt hoe, of je kiest een kopie van History.db.
- **Meer bronnen**, elk met hulp en een knop naar de juiste pagina: **Netflix** (kijkactiviteit,
  zonder trailers), **Snapchat** (bewaarde Memories met plek, en je chats), **TikTok** (bekeken,
  leuk gevonden, gezocht), **Apple** (je Apple Music-luistergeschiedenis van privacy.apple.com) en
  **Apple Gezondheid** (stappen per dag en je trainingen uit export.zip). Twee keer dezelfde
  export inlezen voegt niets toe. Apple Foto's via het programma osxphotos staat als optionele
  route in de hulp.
- **Pasha weet waar je was**, als jij dat wilt: zet per bron "Pasha mag deze gegevens gebruiken"
  aan (standaard uit). Vraag dan bijvoorbeeld "Waar was ik op 14 juli 2019?".

### Back-ups van de database
- Je scores, vergelijkingen, Keurer-keuzes, herstelgegevens en instellingen staan in de database
  op de T7. Die wordt nu elke dag gekopieerd naar de schijf van deze Mac
  (`~/Library/Application Support/DataVault/backups`), los van de T7: bij het starten als de
  laatste back-up ouder is dan een dag, en daarna dagelijks zolang de app draait. Vóór elke
  database-update blijft er ook een volledige kopie naast de database, zoals voorheen.
- Een back-up is een momentopname terwijl de app gewoon doorwerkt (ook wat nog niet in het
  hoofdbestand stond), wordt gecontroleerd (PRAGMA integrity_check) en pas daarna bewaard, en is
  klein: wat de app zelf opnieuw kan opbouwen (hashes, datums, de mappenlijst van de index,
  scanresultaten, later beeldvectoren) zit er niet in, en hij is gecomprimeerd. Een index van
  100.000 foto's: ongeveer 7 MB in plaats van 109 MB, in een seconde.
- Bewaard blijven de automatische back-up van elk van de laatste 7 dagen en van de laatste 4 weken,
  en je 5 nieuwste handmatige: maak je op één dag twee keer een back-up (bijvoorbeeld vóór iets
  spannends en daarna nog eens), dan blijven ze allebei. Een handmatige back-up haalt de
  automatische van die ochtend nooit weg, want dat is vaak de enige van vóór wat er misging. Oudere
  gaan weg. Alleen de eigen back-upbestanden worden ooit opgeruimd.
- Een eigen back-upmap in config.json mag met `~/` beginnen (je thuismap). Een pad dat niet
  helemaal is uitgeschreven gebruikt de app niet: dan blijft de standaardmap in gebruik en zegt
  Instellingen waarom.
- Een back-upmap op een USB-stick of schijf met exFAT werkt ook (die kent geen harde links; de
  back-up wordt dan als nieuw bestand gemaakt dat nooit iets overschrijft, en opnieuw vergeleken).
- **Instellingen > Back-ups** toont de laatste back-up, wanneer de volgende komt (de app kijkt elk
  uur; staat er nog geen, dan zie je hoe laat de volgende controle is), de map, hoeveel er bewaard
  zijn en een mislukte poging, in gewone taal: een map waarin de app niet mag schrijven wordt
  meteen zo genoemd, met wat je eraan doet. Met **Nu back-uppen** maak je er meteen een; het scherm
  springt daarbij niet meer.
- Terugzetten gaat met `restore_backup.py` in Terminal, met de app gestopt (draait hij nog, dan
  weigert het script). Het bewaart eerst de huidige database helemaal als
  "datavault-voor-herstel-…" en zet pas dan de back-up terug; er wordt nooit iets overschreven. De
  stappen staan ook in Instellingen, met de opdrachten om te kopiëren. Draait de app met een eigen
  instellingenbestand, dan noemt die opdracht het ook (`--config`), zodat hij altijd déze database
  terugzet.

### Shows: "Welke is mooier?"
- Nieuw bij Shows: **Welke is mooier?** Twee foto's naast elkaar, jij kiest de mooiste. Kies met
  **←** of **1** (links) en **→** of **2** (rechts), sla over met **spatie** of **s**, en draai de
  laatste keuze terug met **z** (dan staat datzelfde paar weer in beeld). Je kiest bovenin uit welke
  collectie de foto's komen: de hele bibliotheek of één show.
- De score is dezelfde als die van de duimpjes in de slideshow (1000 om te beginnen; een duim blijft
  +50 of −50), dus hoe vaker je een foto kiest, hoe vaker hij in Shows en "Verras me" komt. Na een
  keuze zie je kort hoeveel beide scores veranderden.
- Hoe de score verandert (Elo, zoals bij schaken): wint een foto van een hoger gewaardeerde, dan
  stijgt hij veel; wint hij van een lagere, dan weinig. Een foto met minder dan 30 vergelijkingen
  beweegt maximaal 32 punten per keuze, daarna 16, zodat één verrassende keuze niet alles omgooit.
  Geen score zakt onder de 100.
- De paren zijn gekozen om snel een goede volgorde te krijgen: eerst foto's die nog weinig
  vergeleken zijn, tegen foto's met een vergelijkbare score, uit de hele collectie (ook in een
  nieuwe bibliotheek, waar alle scores nog gelijk zijn). Nooit twee keer achter elkaar hetzelfde
  paar, en nooit twee foto's die (bijna) hetzelfde zijn: een kopie, of twee foto's uit dezelfde
  reeks, hoe lang die ook is (in dezelfde map, elke foto binnen 2 seconden na de vorige).
- Een ingedrukt gehouden toets, of een tweede toets terwijl het volgende paar laadt, telt niet: je
  kiest nooit hetzelfde paar twee keer of een paar dat je nog niet zag. Kies je met Tab en Enter,
  dan blijven de toetsen daarna werken, en alles wat je met Tab bereikt heeft een zichtbare
  focusring. Na terugdraaien klopt de voortgang onderin weer; bij een andere collectie begint de
  teller opnieuw, en Overslaan staat uit als er geen paar is.
- Een map die net is aangemaakt (nog niet in de bibliotheek-index) doet meteen mee.
- **Ranglijst** (tabblad in hetzelfde venster): de best gewaardeerde foto's van de collectie, met
  hoeveel vergelijkingen ze wonnen.
- Elke vergelijking wordt bewaard met beide scores ervoor en erna, dus terugdraaien zet beide scores
  precies terug. Verplaats je een foto, dan gaan zijn score en vergelijkingen mee. Was een van de
  twee foto's daarna nog veranderd (een duim, een latere keuze), dan zegt de app dat in plaats van
  te gokken.
- Ga je meteen na een keuze naar de Ranglijst, dan blijft de ranglijst staan (er kwam soms een paar
  overheen dat je niet kon kiezen), en bij Vergelijken krijg je daarna een nieuw paar. Lukt
  terugdraaien even niet (geen verbinding, de app herstart), dan kun je het met **z** gewoon nog eens
  proberen. Na terugdraaien staat "Teruggedraaid, kies opnieuw" onderin, in plaats van een melding
  over de rechter foto.
- De foto die je niet koos dooft rustig uit zonder dat het donkere vlak eromheen grijs wordt, en de
  winnaar krijgt zijn +N in het groen. Lange shownamen in de keuzelijst eindigen netjes op "…".
- Ook zonder shows staat **Welke is mooier?** bovenaan bij Shows, voor de hele bibliotheek.

### Slim zoeken: foto's vinden op wat erop staat

- Slim zoeken laat bij korte zoekopdrachten zoals 'food' of 'een auto' weer de juiste foto's zien (de
  drempel was te streng), en lege datumvelden zien er niet meer uit als een actief filter.
- Verbeterd na de review: nieuwe foto's blijven niet meer onzichtbaar nadat verwijderde foto's zijn
  opgeruimd, een onderwerp dat een groot deel van de map beslaat (bijvoorbeeld "hond") wordt gewoon
  gevonden, onzin als zoekopdracht geeft "Niets gevonden", "Vergelijkbaar" verbergt gelijkende foto's niet
  meer achter een bijna-kopie, de pagina's van de resultaten schuiven niet meer terwijl foto's nog
  herkend worden, bestandsnamen met é of ü worden ook op hoofdletters gevonden en het zoekmodel wordt
  bij het starten alvast geladen. Het venster geeft de focus netjes terug, 'Periode wissen' verschijnt
  direct en een leeg resultaat heeft een knop om de filters te wissen.
- Zoek in je foto's en video's op wat erop staat, gewoon in het Nederlands (of Engels): "hond op het
  strand", "verjaardagstaart", "zonsondergang boven zee", "sneeuw in de bergen". Typ in het zoekveld
  bovenaan en druk op Enter, of druk op **s**: het zoekvenster zoekt in de hele bibliotheek, met de
  keuze **Slim zoeken** of **Bestandsnaam**, filters op foto's of video's en een periode, en de beste
  resultaten eerst. Met de pijltjes loop je door de resultaten, Enter opent ze in de viewer en Esc
  brengt je terug naar de resultaten.
- In de viewer zoekt **Vergelijkbaar** (toets **v**) foto's die op de getoonde foto lijken.
- Alles gebeurt op deze Mac: je foto's en zoekopdrachten gaan nergens heen. Het zoekmodel (ongeveer
  926 MB) wordt alleen gedownload als je in **Instellingen > Slim zoeken** op **Zoekmodel installeren**
  klikt; de app vraagt eerst en laat zien waar het vandaan komt. Daarna herkent de app je foto's op de
  achtergrond (op de grafische chip zo'n 70 foto's per seconde, en daarna alleen nieuwe of gewijzigde
  foto's); de voortgang staat in het takenpaneel, en pauzeren, hervatten en annuleren werken zoals bij
  elke taak. Zoeken in 100.000 foto's duurt ongeveer een tiende seconde.
- Zonder zoekmodel werkt alles zoals eerst, en zegt het zoekvenster hoe je het installeert. Zoeken op
  bestandsnaam in de hele bibliotheek werkt altijd.
- Een verplaatste foto blijft gewoon vindbaar, en een foto die naar de prullenmand ging, verschijnt niet
  meer in de resultaten.
- Gekozen na een eerlijke vergelijking van vijf zoekmodellen op 179 echte foto's met 50 Nederlandse en
  Engelse zoekopdrachten: het gekozen model (SigLIP 2) zet bij 49 van de 50 Nederlandse zoekopdrachten
  een goede foto bovenaan en bij alle 50 in de top 5. Een model dat alleen Engels kent, deed dat bij 33.

### DataGatherer: berichten en sociaal
- **Nieuwe bronnen in DataGatherer**: WhatsApp, iMessage en sms, Messenger, Facebook en Instagram.
  Je vraagt exports zelf aan in je eigen browser of op je telefoon; DataVault opent de juiste pagina,
  ziet het bestand in Downloads en leest het in. Er gaat geen wachtwoord door DataVault en er is geen
  bot.
- **Berichten**: een nieuw scherm met al je gesprekken op één plek. Zoek in alle bronnen tegelijk
  (ook zonder accenten: 'cafe' vindt 'café'), filter per bron en lees een gesprek als chatbubbels met
  datums, reacties en voorbeelden van foto's uit je export. Met de pijltjes door de lijst, Enter
  opent. Te openen met de knop Berichten op de DataGatherer-pagina of met `g e`.
- **WhatsApp zonder dubbels**: WhatsApp voor Mac, een ChatStorage.sqlite uit een iPhone-backup en
  losse chat-exports (iPhone en Android, Nederlands en Engels) komen samen in één gesprek. Wat in
  meerdere bronnen staat, zie je één keer; niets wordt gewist.
- **iMessage en sms** uit Berichten op je Mac, ook de tekst die nieuwere macOS-versies verpakt
  opslaat, met reacties (tapbacks) en de namen van bijlagen.
- **Facebook, Instagram en Messenger**: berichten, reacties, likes en de gegevens van je
  Instagram-foto's. Vreemde tekens zoals 'CafÃ©' worden weer 'Café'. Een export in het Nederlands
  werkt net zo goed. Je versleutelde Messenger-chats (beveiligde opslag) en de chats uit je
  Facebook-export komen samen in één gesprek.
- **Alleen wat nieuw is**: bij Facebook en Instagram toont het venster Export aanvragen de datum
  'sinds' en zet hem op je klembord, zodat je alleen een korte periode aanvraagt. Laat je Meta
  automatisch exporteren naar Dropbox, Google Drive of Koofr, vul dan die map in bij Instellingen:
  DataVault leest nieuwe exports daar vanzelf in.
- **Toegang nodig**: macOS beschermt je berichten. De kaart zegt precies waar je toegang geeft
  (Systeeminstellingen > Privacy en beveiliging > Volledige schijftoegang) en geeft een opdracht om te
  kopiëren voor een kleine hulp die je zelf start. De DataVault-server zelf heeft die toegang niet
  nodig.
- **Pasha en je berichten**: Pasha leest je berichten alleen van de bronnen die je zelf aanzet
  (standaard alles uit), en dan alleen de paar berichten die bij je vraag passen.

### Afwerking: één rustig geheel
- **Viewer met meer gegevens**: naast de foto staan nu ook de plaats (offline opgezocht, bijvoorbeeld
  'Lissabon, Portugal'), de camera en de afmetingen in pixels en megapixels. De datum is de
  opnamedatum als die bekend is, met de bron er klein onder; het pad staat onderaan.
- **Home**: 'Spotify op deze dag' heeft een eigen hoes (een kleurverloop per artiest, zonder iets
  van internet), met het tijdstip en het jaar eronder. De grote foto's op Home en in Shows worden
  scherp: eerst de miniatuur, dan het originele bestand zodra dat geladen is (alleen JPEG, PNG en
  WebP tot 20 MB). De naam op een lichte foto blijft leesbaar.
- **Netjes op elk scherm**: Spotify past nu ook in een venster van 1024 pixels breed (de cijferkaarten
  en de datum liepen buiten beeld), de knoppen bovenaan DataGatherer staan op één rij, lange paden in
  een DataGatherer-melding breken netjes af, en de foto in de viewer houdt ruimte tot de kop. De strook
  'op deze dag' bij Spotify loopt zacht uit aan de randen, en de laadvlakken glanzen zonder harde streep.
- **Overal dezelfde bediening**: in het Herstelcentrum klap je de bestanden open met hetzelfde draaiende
  pijltje als elders, en bij Duplicaten > Herstellen staat de map vanaf het archief ('RAW/...') in
  plaats van het volledige pad op de schijf.
- **Tabblad en beweging**: DataVault heeft een eigen icoon in het tabblad, en de titel van het tabblad
  zegt in welk onderdeel je bent ('Kaart · DataVault'). Een ander onderdeel komt rustig in beeld
  (niet als je in macOS minder beweging hebt gevraagd).
- **Activiteit** heeft een eigen icoon (een golflijn) in plaats van dat van het Herstelcentrum, en onder
  de gebeurtenissen van een dag staat niet langer het woord 'null'.
- **Kaart in het Nederlands**: bekende steden heten op de kaart zoals in het Nederlands (Moskou,
  Wenen, Parijs, Lissabon, Tokio, ...). In het sneltoetsenoverzicht heet de spatiebalk overal 'spatie'.
- **Schermafbeeldingen**: de README toont acht schermen in licht en donker (docs/screenshots), gemaakt
  van een gegenereerd voorbeeldarchief zonder echte foto's of persoonlijke gegevens.

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
  alle zichtbare kopieën als duplicaat werden aangeboden. Bestanden met een punt voor hun naam
  doen nu niet meer mee aan de scan. (Bestanden die alleen met het Finder-kenmerk "verborgen"
  verborgen zijn, doen wel mee, maar blijven nooit staan in plaats van een zichtbare kopie; zie
  Duplicaten.)

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
  plaats van pas na het opruimen "0 verplaatst". Anders zou de enige overgebleven kopie er een
  zijn die Finder en de app niet tonen.
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
- Files: een map met duizenden bestanden opent nu meteen. De kaarten verschijnen in stukken van
  60 terwijl je scrolt, en een miniatuur wordt pas opgehaald als hij (bijna) in beeld komt.
  Voorheen vroeg één map van 1500 foto's direct 1500 miniaturen op, die de server allemaal op
  de schijf maakte en bewaarde, ook als je alleen de eerste rij bekeek. Het zoekveld filtert pas
  als je even stopt met typen, in plaats van bij elke letter het hele raster opnieuw te bouwen.
- Files: verplaats je een foto vanuit de tijdlijn, de chat of een slideshow, dan verdwijnt hij nu
  ook uit de Files-weergave (voorheen bleef de kaart staan en gaf een klik een foutmelding). Na
  verplaatsen springt een lange lijst niet meer terug naar boven.
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
- Stemmen die tegelijk binnenkomen (bijv. vanuit twee tabbladen) tellen nu allemaal mee. Voorheen
  kon de ene stem de andere overschrijven: na 40 gelijktijdige duimpjes omhoog stond een foto op
  1350 in plaats van 3000, terwijl de teller wel 40 stemmen zei. De regel zelf is ongewijzigd
  (start 1000, +50 of −50 per stem, nooit onder de 100) en wordt nu ook door tests bewaakt.

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
- Foto's zoeken op naam (chat) doorzocht bij een naam die nergens voorkwam het hele archief, wat
  op een grote schijf minuten kon duren. Nu stopt het zoeken na 5000 mappen of 4 seconden, net
  als "op deze dag", en zegt Pasha erbij dat niet het hele archief doorzocht is.
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

### Installeren en starten
- De installatie-instructies in `README.md` en `code/README.md` werken weer zoals ze er staan:
  alles gebeurt vanuit de map `code` (daar staan `requirements.txt` en `config.json`), de venv
  wordt met Python 3.12 gemaakt (de vastgezette versies werken niet op de Python 3.9 van macOS),
  en de server start met `uvicorn --factory backend.main:create_app` of gewoon `./start.sh`.
  Het oude commando `uvicorn backend.main:app` gaf de fout "Attribute app not found".
- `start.sh` en `stop_datavault.sh` stoppen alleen nog een DataVault-server op poort 8000. Draait
  daar een ander programma, dan laten ze het met rust en zeggen ze welk programma het is.
  `start.sh` geeft een oude server nu tot 10 seconden om open verzoeken af te ronden voordat hij
  hem hard stopt (was 1 seconde). De handleidingen raden niet meer `lsof -ti:8000 | xargs kill -9`
  aan, dat ook het Safari-tabblad met de app afsloot; ze verwijzen naar `./stop_datavault.sh`.

### Nog open (voor fase 3)
Bekend, maar bewust nog niet in fase 2 opgelost:
- De tijdlijn stuurt alle foto's van de gekozen mappen in één antwoord (compact en bewaard, maar
  zonder bladeren), en het scannen ervan is nog geen achtergrondtaak. *(Opgelost in fase 3: de
  tijdlijn leest uit de bibliotheek en laadt per pagina.)*
- Het opslagoverzicht laat mappen kleiner dan ongeveer 5 MB weg, omdat het in hele GB's met twee
  decimalen rekent (zo'n map wordt 0,00 GB). *(Opgelost in fase 3: het
  overzicht rekent in bytes en toont KB, MB of GB.)*
- Na het sluiten van een foto die vanuit de chat geopend werd, staat de cursor niet meteen weer
  in het chatvak. *(Opgelost in fase 3: de focus gaat terug naar het chatvak.)*
- Start je een tweede server op hetzelfde archief (met de hand, op een andere poort), dan ziet
  die bij het opstarten de lopende taken van de eerste als "onderbroken".
- Twee oudere opdrachten (`/api/duplicates/delete_single` en `/api/duplicates/scan_folder`, niet
  meer door het scherm gebruikt) nemen hun pad nog als URL-parameter in plaats van in de body.

## Fase 1 – Analyse

Fase 1 was een analyse zonder codewijzigingen. Het rapport bevat ongeveer 180 bevindingen, bijna
allemaal gecontroleerd door ze na te bootsen: 2 kritiek, 34 hoog, 81 middel en 61 laag. De grootste
onderwerpen waren bugs (52), gegevensverlies en -integriteit (28), snelheid bij 100.000 bestanden
(27) en veiligheid (23). De ernstigste zaken waren Keurer-undo die bestanden buiten het archief kon
verplaatsen of overschrijven, een app die elke website liet aanspreken (open CORS, geen
authenticatie), verplaatsen dat bestaande bestanden kon overschrijven, en taken die de hele server
tientallen minuten blokkeerden. Fase 2 loste die op; fase 3 bouwde daarna de nieuwe functies.
