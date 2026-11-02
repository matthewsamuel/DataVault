// ==================== KAART ====================
// Foto's en video's met een GPS-positie (uit EXIF, door de index gelezen) op een kaart.
// Alles komt uit /api/map/...; er wordt nooit iets gekopieerd of verplaatst.
//
// De basiskaart werkt zonder internet: landsgrenzen en grote steden van Natural Earth
// (publiek domein, vendor/naturalearth/), getekend als GeoJSON-laag in de kleuren van het
// thema (CSS-variabelen --map-*). Een aangeklikt land, en bij ver inzoomen elk land in beeld,
// krijgt zijn provincies (gestippelde grenzen en namen) en grote steden erbij, uit een klein
// bestand per land (vendor/naturalearth/regions/, gemaakt door scripts/build_map_regions.py). Vanaf zoom 8
// komen daar de kleinere plaatsen bij (regions/towns/); een plaats waar foto's genomen zijn, krijgt eerder en
// met voorrang zijn naam, en ligt er een groep op, dan staat de naam ernaast. Alleen als de gebruiker 'Gedetailleerde kaart
// (OpenStreetMap, online)' aanzet (standaard uit, bewaard op de server), komen er tegels van
// OpenStreetMap bij. Zolang dat uit staat verlaat er geen enkel verzoek deze Mac; het
// tegel-adres staat daarom op precies één plek (OSM_TILES) en wordt alleen dan gebruikt.
//
// De punten komen als compacte rijen [id, lat, lon, taken, soort] in stukken van 10.000
// binnen; Leaflet.markercluster groepeert ze (chunkedLoading), zodat 100.000 foto's de
// pagina niet vastzetten.

(function () {
    'use strict';

    // Het ENIGE internetadres van de frontend: pas na een keuze van de gebruiker gebruikt.
    // tests/test_frontend_offline.py laat precies deze sjabloon toe.
    const OSM_TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
    const OSM_COPYRIGHT = 'https://www.openstreetmap.org/copyright';
    const COUNTRIES_URL = 'vendor/naturalearth/countries-50m.geojson';
    const PLACES_URL = 'vendor/naturalearth/places-50m.json';
    const REGIONS_URL = 'vendor/naturalearth/regions/';
    const REGIONS_V = 2;                // ophogen als scripts/build_map_regions.py de bestanden opnieuw maakt
    const DETAIL_ZOOM = 7;              // vanaf hier krijgt elk land in beeld zijn provincies en steden
    const DETAIL_MAX = 8;               // hoogstens zoveel landen tegelijk
    const TOWN_ZOOM = 8;                // vanaf hier ook de kleinere plaatsen (vanaf 1.000 inwoners)
    const PHOTO_CELL = 100;             // rooster van de foto's per plaats: 1/100 graad (~1 km)
    const CHUNK = 10000;
    const CLUSTER_ANIMATE_MAX = 5000;  // meer foto's: groepen zonder zoomanimatie (zie useCluster)
    const NEIGHBOUR_PX = 28;            // foto's binnen zoveel pixels van de aangeklikte horen bij 'deze plek'
    const NEIGHBOUR_MAX = 200;          // zoveel bladert de viewer er hoogstens doorheen
    const MAX_LABELS = 120;
    const LABEL_GAP = 4;                // px lucht tussen twee namen
    const COUNTRY_WRAP = 16;            // langere landnamen breken over twee regels
    const EDGE_GAP = 4;                 // px: een naam staat helemaal binnen de kaart, niet half erbuiten
    const COUNTRY_NUDGE_MAX = 64;       // px: zo ver mag een landnaam opschuiven voor een groep foto's
    // Korter op de kaart dan in Natural Earth (de tooltip houdt de volledige naam)
    const COUNTRY_SHORT = { USA: 'Verenigde Staten', CHN: 'China' };
    const HINT_ZOOM = 9;                // vanaf hier is de basiskaart zonder internet te grof
    const KIND_ICON = ['fa-image', 'fa-film', 'fa-music'];
    const HOVER_DELAY = 90;             // ms stilstaan op een pin of groep voor de voorvertoning (niet bij langs bewegen)
    const HOVER_GRACE = 240;            // ms om van de pin naar het kaartje te bewegen voor het verdwijnt
    const HOVER_THUMBS = 4;             // zoveel miniaturen toont een groep
    const HOVER_CACHE_MAX = 600;        // bekende bestanden (pad en soort) voor de voorvertoning
    // Plaatsnamen op de kaart in het Nederlands waar dat de gewone naam is (Natural Earth is Engels)
    const PLACE_NL = {
        'Moscow': 'Moskou', 'Vienna': 'Wenen', 'Prague': 'Praag', 'Brussels': 'Brussel', 'Cologne': 'Keulen',
        'Athens': 'Athene', 'Lisbon': 'Lissabon', 'Copenhagen': 'Kopenhagen', 'Warsaw': 'Warschau',
        'Budapest': 'Boedapest', 'Milan': 'Milaan', 'Naples': 'Napels', 'Venice': 'Venetië', 'Berlin': 'Berlijn',
        'Munich': 'München', 'Geneva': 'Genève', 'Cape Town': 'Kaapstad', 'Tokyo': 'Tokio', 'Beijing': 'Peking',
        'The Hague': 'Den Haag', 'Liege': 'Luik', 'Ghent': 'Gent', 'Antwerp': 'Antwerpen', 'Seville': 'Sevilla',
        'London': 'Londen', 'Paris': 'Parijs', 'Bucharest': 'Boekarest', 'Kiev': 'Kyiv', 'Cairo': 'Caïro',
        'Baghdad': 'Bagdad', 'Tehran': 'Teheran', 'Belgrade': 'Belgrado', 'Saint Petersburg': 'Sint-Petersburg',
        'Makkah': 'Mekka', 'Mecca': 'Mekka', 'Mexico City': 'Mexico-Stad', 'Washington, D.C.': 'Washington',
        'Kuwait City': 'Koeweit', 'Archangel': 'Archangelsk', 'Florence': 'Florence', 'Algiers': 'Algiers',
        'Singapore': 'Singapore', 'Rome': 'Rome', 'Turin': 'Turijn', 'Strasbourg': 'Straatsburg', 'Aachen': 'Aken',
        'Frankfurt': 'Frankfurt', 'Zurich': 'Zürich', 'Hanoi': 'Hanoi', 'Seoul': 'Seoel', 'Damascus': 'Damascus',
        'Jerusalem': 'Jeruzalem', 'Tripoli': 'Tripoli', 'Havana': 'Havana', 'Bern': 'Bern', 'Vatican City': 'Vaticaanstad',
    };

    const $ = id => document.getElementById(id);
    const fmt = n => Number(n).toLocaleString('nl-NL');

    const state = {
        map: null, cluster: null, countries: null, places: null, tiles: null, hint: null,
        placeData: null, inited: null,
        // Provincies en steden: het aangeklikte land, de landen die nu detail tonen en hun data
        selected: '', countryLayers: new Map(), countryBoxes: [], regionData: new Map(),
        regionLayers: new Map(), detailCodes: [], detailRegions: [], detailToken: 0, chip: null,
        // Namen op de kaart: landen (punt, voorrang, grootste deel) en de namen die nu staan, per sleutel
        countryLabels: [], labelPool: new Map(), textWidths: new Map(), fonts: null,
        // Kleinere plaatsen per land, en hoeveel foto's er per rondje van ~1 km liggen (voor 'hier was je')
        townData: new Map(), detailTowns: [], photoGrid: null, photoGridOf: null, photoGridLen: 0,
        osm: false, osmLoaded: false,
        filters: { year: '', date_from: '', date_to: '', camera: '', album: '' },
        points: [], markers: new Map(), total: 0, withoutGps: 0, version: '', bounds: null,
        loadToken: 0, loading: false, popup: null, popupToken: 0, facetsLoaded: false,
        hover: null, hoverTimer: 0, hoverHideTimer: 0, hoverToken: 0, hoverLayer: null, hoverItems: new Map(),
        // Locatiegeschiedenis (DataGatherer): geschatte plekken van foto's zonder GPS, en de route van één dag
        history: null, showInferred: false, inferredIds: new Set(), inferredTotal: 0,
        track: null, trackDay: '', trackDays: [], trackOn: false, trackToken: 0,
    };

    // ---------- instellingen: tegels van OpenStreetMap ----------

    async function loadOsmSetting() {
        if (state.osmLoaded) return state.osm;
        try {
            const data = await fetchJSON(`${API_BASE}/api/map/settings`);
            state.osm = data.osm_tiles === true;
            state.osmLoaded = true;
        } catch (err) {
            state.osm = false;      // bij twijfel offline
        }
        return state.osm;
    }

    async function setOsm(on) {
        const previous = state.osm;
        state.osm = on;
        syncOsmControls();
        applyBasemap();
        try {
            await fetchJSON(`${API_BASE}/api/map/settings`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ osm_tiles: on }),
            });
            state.osmLoaded = true;
        } catch (err) {
            state.osm = previous;
            syncOsmControls();
            applyBasemap();
            toastFetchError('De kaartkeuze is niet bewaard', err);
        }
    }

    function syncOsmControls() {
        document.querySelectorAll('input.map-osm-input').forEach(input => { input.checked = state.osm; });
        const box = $('map-osm-toggle');
        if (box) box.checked = state.osm;
    }

    function osmSwitch(id) {
        return createEl('label', { class: 'switch' },
            createEl('input', {
                type: 'checkbox', role: 'switch', class: 'map-osm-input', id,
                onChange: e => setOsm(e.target.checked),
            }),
            createEl('span', { class: 'slider' }));
    }

    registerSettingsSection({
        id: 'kaart',
        title: 'Kaart',
        icon: 'fa-map',
        order: 60,
        description: "De kaart met je foto's werkt zonder internet. Wil je meer detail, dan kun je kaarttegels van OpenStreetMap aanzetten.",
        render(body) {
            const toggle = osmSwitch('settings-map-osm');
            toggle.querySelector('input').checked = state.osm;
            loadOsmSetting().then(() => { toggle.querySelector('input').checked = state.osm; });
            body.append(
                createEl('div', { class: 'settings-row' },
                    createEl('label', { class: 'settings-row-label', for: 'settings-map-osm', text: 'Gedetailleerde kaart (OpenStreetMap, online)' }),
                    createEl('span', { class: 'settings-row-value' }, toggle)),
                createEl('p', { class: 'settings-hint' },
                    'Uit (standaard): een eenvoudige wereldkaart die op deze Mac staat; er verlaat niets je computer. ',
                    'Aan: je browser haalt kaarttegels van OpenStreetMap via internet. OpenStreetMap ziet dan welk deel van de wereld je bekijkt, ',
                    'maar niet welke foto’s je hebt: de plekken van je foto’s blijven op deze Mac.'));
        },
    });

    // ---------- de kaart ----------

    function build() {
        const L_ = window.L;
        const map = L_.map('map-canvas', {
            minZoom: 2, maxZoom: 18, zoomSnap: 0.5, worldCopyJump: true, zoomControl: false,
            attributionControl: true, keyboard: true, keyboardPanDelta: 120,
            maxBounds: [[-85, -540], [85, 540]], maxBoundsViscosity: 0.8,
        });
        map.attributionControl.setPrefix(false);       // geen link naar leafletjs.com
        L_.control.zoom({ zoomInTitle: 'Inzoomen (+)', zoomOutTitle: 'Uitzoomen (−)' }).addTo(map);
        map.setView([30, 10], 2);
        map.createPane('regions').style.zIndex = 420;
        map.getPane('regions').style.pointerEvents = 'none';
        map.createPane('places').style.zIndex = 450;
        map.getPane('places').style.pointerEvents = 'none';
        state.map = map;

        const pin = L_.divIcon({ className: 'map-pin-wrap', html: '<span class="map-pin"></span>', iconSize: [18, 18], iconAnchor: [9, 9] });
        state.pinIcon = pin;
        state.estimatedIcon = L_.divIcon({ className: 'map-pin-wrap', html: '<span class="map-pin map-pin-estimated"></span>', iconSize: [18, 18], iconAnchor: [9, 9] });
        useCluster(true);
        map.getContainer().setAttribute('aria-label', "Kaart met foto's op locatie. Pijltjestoetsen verschuiven, plus en min zoomen, Enter opent een groep of foto.");

        map.on('zoomend moveend', () => { updateDetail(); updateHint(); });
        // Tijdens het zoomen schaalt Leaflet de vectorlaag mee (ook de randen): style.css laat de
        // groene rand dan even terugwijken, tot de kaart stilligt
        map.on('zoomstart', () => map.getContainer().classList.add('is-zooming'));
        map.on('zoomend', () => map.getContainer().classList.remove('is-zooming'));
        map.on('click', () => { if (state.selected) selectCountry(''); });
        map.getContainer().addEventListener('keydown', e => {
            if (e.key === 'Escape' && state.selected) { e.stopPropagation(); selectCountry(''); }
        });
        map.on('movestart zoomstart popupopen', hideHover);
        map.getContainer().addEventListener('mouseleave', hideHover);
        // De kaart wordt hoger of lager zonder dat het venster verandert (de takenbalk onderaan komt of gaat):
        // Leaflet meet dan opnieuw, en de namen volgen (moveend)
        if (typeof ResizeObserver === 'function') {
            let pending = 0;
            new ResizeObserver(() => {
                cancelAnimationFrame(pending);
                pending = requestAnimationFrame(() => map.invalidateSize({ pan: false, debounceMoveend: true }));
            }).observe(map.getContainer());
        }
        syncOsmControls();
        applyBasemap();
    }

    // ---------- basiskaart: offline landen en steden, of de tegels van OpenStreetMap ----------

    async function loadCountries() {
        if (state.countries) return state.countries;
        try {
            const res = await fetch(COUNTRIES_URL);
            const data = await res.json();
            state.countries = L.geoJSON(data, {
                // bubblingMouseEvents uit: een klik op een land is geen klik op de zee (die heft de keuze op)
                style: () => ({ className: 'map-country', weight: 1, bubblingMouseEvents: false }),
                onEachFeature: (feature, layer) => {
                    const { n, a, l, z, k } = feature.properties || {};
                    if (n) layer.bindTooltip(n, { sticky: true, className: 'map-tip', direction: 'top' });
                    if (!a) return;
                    if (!state.countryLayers.has(a)) state.countryLayers.set(a, []);
                    state.countryLayers.get(a).push(layer);
                    const polys = feature.geometry.type === 'Polygon' ? [feature.geometry.coordinates] : feature.geometry.coordinates;
                    const boxes = polys.map(poly => L.latLngBounds(poly[0].map(([lon, lat]) => [lat, lon])));
                    for (const box of boxes) state.countryBoxes.push([a, box]);
                    // De naam staat op het punt l (build_map_regions.py: in het grootste deel); dat deel
                    // bepaalt ook vanaf welke zoom de naam erin past
                    if (n && l) {
                        const main = boxes.filter(b => b.contains([l[1], l[0]])).sort((x, y) => boxArea(y) - boxArea(x))[0]
                            || boxes.sort((x, y) => boxArea(y) - boxArea(x))[0];
                        state.countryLabels.push({ code: a, name: COUNTRY_SHORT[a] || n, lon: l[0], lat: l[1],
                            minZoom: z || 5, rank: k || 9, box: main, area: boxArea(main) * Math.cos(l[1] * Math.PI / 180) });
                    }
                    layer.on('click', () => selectCountry(state.selected === a ? '' : a));
                },
                attribution: 'Basiskaart: Natural Earth, steden: GeoNames',
            });
            // Belangrijkste eerst: Natural Earth's LABELRANK, dan het grootste land
            state.countryLabels.sort((x, y) => (x.rank - y.rank) || (y.area - x.area));
        } catch (err) {
            console.error(err);
            state.countries = null;
        }
        return state.countries;
    }

    async function loadPlaces() {
        if (state.placeData) return state.placeData;
        try {
            state.placeData = (await (await fetch(PLACES_URL)).json()).map(([name, ...rest]) => [PLACE_NL[name] || name, ...rest]);
        } catch (err) {
            state.placeData = [];
        }
        return state.placeData;
    }

    async function applyBasemap() {
        const map = state.map;
        if (!map) return;
        const wantOsm = state.osm;
        if (wantOsm) {
            if (state.countries) map.removeLayer(state.countries);
            if (state.places) { map.removeLayer(state.places); state.places = null; state.labelPool.clear(); }
            selectCountry('');
            updateDetail();
            if (!state.tiles) {
                state.tiles = L.tileLayer(OSM_TILES, {
                    maxZoom: 19, className: 'map-tiles',
                    attribution: `© <a href="${OSM_COPYRIGHT}" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>-bijdragers`,
                });
            }
            state.tiles.addTo(map);
            state.tiles.bringToBack();
        } else {
            if (state.tiles) map.removeLayer(state.tiles);
            const countries = await loadCountries();
            if (state.osm !== wantOsm) return;       // intussen omgezet
            if (countries && !map.hasLayer(countries)) { countries.addTo(map); countries.bringToBack(); }
            await loadPlaces();
            updateDetail();
        }
        map.getContainer().classList.toggle('map-online', wantOsm);
        updateHint();
    }

    // ---------- provincies en steden van een aangeduid land ----------

    // Het land onder de klik aanduiden ('' heft de keuze op). Is het land niet (goed) in beeld,
    // dan schuift en zoomt de kaart ernaartoe; wie al dichterbij zit, blijft waar hij is.
    function selectCountry(a3) {
        const map = state.map;
        if (!map || a3 === state.selected) return;
        for (const layer of state.countryLayers.get(state.selected) || []) layer.getElement()?.classList.remove('map-country-selected');
        state.selected = a3;
        for (const layer of state.countryLayers.get(a3) || []) {
            layer.getElement()?.classList.add('map-country-selected');
            layer.bringToFront();
        }
        renderChip();
        if (a3) {
            const main = state.countryBoxes.filter(([code]) => code === a3)
                .map(([, box]) => box)
                .sort((x, y) => boxArea(y) - boxArea(x))[0];
            const target = main && Math.min(map.getBoundsZoom(main, false, L.point(96, 96)), 9);
            if (main && (map.getZoom() < target - 1 || !map.getBounds().intersects(main))) {
                map.flyToBounds(main, { padding: [48, 48], maxZoom: 9, animate: !reducedMotion(), duration: 0.8 });
                return;         // updateDetail volgt op moveend
            }
        }
        updateDetail();
    }

    function boxArea(box) {
        return (box.getEast() - box.getWest()) * (box.getNorth() - box.getSouth());
    }

    function reducedMotion() {
        if (window.Motion) return !Motion.full();   // Beweging in Instellingen (volgt ook macOS)
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    }

    // Het label bovenaan de kaart met het aangeduide land en een knop om het los te laten
    function renderChip() {
        const name = state.selected && state.countryLayers.get(state.selected)?.[0]?.feature?.properties?.n;
        if (!state.chip) {
            state.chip = createEl('div', { class: 'map-country-chip', role: 'status', hidden: true });
            $('map-canvas').parentElement.appendChild(state.chip);
        }
        state.chip.hidden = !name;
        if (!name) { state.chip.replaceChildren(); return; }
        state.chip.replaceChildren(
            createEl('i', { class: 'fas fa-location-dot', 'aria-hidden': 'true' }),
            createEl('span', { class: 'map-country-chip-name', text: name }),
            createEl('span', { class: 'map-country-chip-meta', text: 'provincies en steden' }),
            createEl('button', {
                type: 'button', class: 'map-country-chip-close', 'aria-label': `${name} niet meer aanduiden (Esc)`, title: 'Loslaten (Esc)',
                onClick: () => { selectCountry(''); state.map.getContainer().focus(); },
            }, createEl('i', { class: 'fas fa-xmark', 'aria-hidden': 'true' })));
    }

    async function loadRegion(a3) {
        if (!state.regionData.has(a3)) {
            state.regionData.set(a3, fetch(`${REGIONS_URL}${a3}.json?v=${REGIONS_V}`)
                .then(res => (res.ok ? res.json() : null))
                .then(data => data && { ...data, cities: (data.cities || []).map(([name, ...rest]) => [PLACE_NL[name] || name, ...rest]) })
                .catch(() => null));
        }
        return state.regionData.get(a3);
    }

    async function loadTowns(a3) {
        if (!state.townData.has(a3)) {
            state.townData.set(a3, fetch(`${REGIONS_URL}towns/${a3}.json?v=${REGIONS_V}`)
                .then(res => (res.ok ? res.json() : null))
                .catch(() => null));
        }
        return state.townData.get(a3);
    }

    // Welke landen nu detail tonen: het aangeduide, en bij ver inzoomen elk land in beeld
    function detailWanted() {
        const map = state.map;
        const codes = state.selected ? [state.selected] : [];
        if (map.getZoom() >= DETAIL_ZOOM) {
            const view = map.getBounds();
            for (const [code, box] of state.countryBoxes) {
                if (codes.length >= DETAIL_MAX) break;
                if (!codes.includes(code) && view.intersects(box)) codes.push(code);
            }
        }
        return codes;
    }

    async function updateDetail() {
        const map = state.map;
        if (!map) return;
        const token = ++state.detailToken;
        const codes = state.osm ? [] : detailWanted();
        const towns = map.getZoom() >= TOWN_ZOOM;
        const [data, townLists] = await Promise.all([
            Promise.all(codes.map(loadRegion)),
            Promise.all(codes.map(code => (towns ? loadTowns(code) : null))),
        ]);
        if (token !== state.detailToken) return;        // intussen verder geschoven
        state.detailCodes = codes.filter((_, i) => data[i]);
        state.detailRegions = data.filter(Boolean);
        state.detailTowns = townLists.filter(Boolean);
        for (const [code, layer] of state.regionLayers) {
            if (!state.detailCodes.includes(code)) { map.removeLayer(layer); state.regionLayers.delete(code); }
        }
        state.detailCodes.forEach(code => {
            const region = data[codes.indexOf(code)];
            if (state.regionLayers.has(code) || !region.borders) return;
            const layer = L.geoJSON(region.borders, {
                pane: 'regions', interactive: false,
                style: () => ({ className: 'map-region-border', weight: 1.6, dashArray: '0 4.5', lineCap: 'round', fill: false }),
            }).addTo(map);
            state.regionLayers.set(code, layer);
        });
        updateLabels();
    }

    // Breedte van een naam in px, gemeten met het lettertype van zijn soort (één keer per naam)
    function textWidth(kind, text) {
        const key = `${kind}:${text}`;
        let w = state.textWidths.get(key);
        if (w !== undefined) return w;
        if (!state.fonts) {
            const probe = createEl('span', { style: 'position:absolute;visibility:hidden;pointer-events:none' });
            state.map.getPane('places').appendChild(probe);
            state.fonts = {};
            for (const [k, cls] of [['country', 'map-country-label'], ['region', 'map-region'], ['city', 'map-place'], ['capital', 'map-place map-place-capital']]) {
                probe.className = cls;
                const cs = getComputedStyle(probe);
                state.fonts[k] = { font: `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`, size: parseFloat(cs.fontSize) || 12,
                    spacing: parseFloat(cs.letterSpacing) || 0 };
            }
            probe.remove();
            state.measure = document.createElement('canvas').getContext('2d');
        }
        const f = state.fonts[kind];
        state.measure.font = f.font;
        w = state.measure.measureText(text).width + f.spacing * text.length;
        state.textWidths.set(key, w);
        return w;
    }

    // Een lange landnaam over twee regels, gebroken bij de spatie die het dichtst bij het midden ligt
    function countryLines(name) {
        if (name.length <= COUNTRY_WRAP || !name.includes(' ')) return [name];
        const mid = name.length / 2;
        let at = -1;
        for (let i = 0; i < name.length; i++) if (name[i] === ' ' && (at < 0 || Math.abs(i - mid) < Math.abs(at - mid))) at = i;
        return [name.slice(0, at), name.slice(at + 1)];
    }

    // Hoeveel van een naam in het grootste deel van zijn land past (1 = helemaal)
    function countryFit(c, zoom, width) {
        const nw = state.map.project(c.box.getNorthWest(), zoom);
        const se = state.map.project(c.box.getSouthEast(), zoom);
        return Math.min((se.x - nw.x) / width, (se.y - nw.y) / 14);
    }

    function labelBox(kind, name, at) {
        if (kind === 'country') {
            const lines = countryLines(name);
            const w = Math.max(...lines.map(line => textWidth('country', line)));
            const h = lines.length * state.fonts.country.size * 1.2;
            return [at.x - w / 2 - LABEL_GAP, at.y - h / 2 - LABEL_GAP / 2, at.x + w / 2 + LABEL_GAP, at.y + h / 2 + LABEL_GAP / 2];
        }
        if (kind === 'region') {
            const w = textWidth('region', name);
            return [at.x - w / 2 - LABEL_GAP, at.y - 9, at.x + w / 2 + LABEL_GAP, at.y + 9];
        }
        // een stad: het puntje links, de naam rechts ervan (zie .map-place)
        return [at.x - 8, at.y - 8, at.x + 6 + textWidth(kind, name) + LABEL_GAP, at.y + 8];
    }

    // Een plaatsnaam naast de groep of speld die op (of vlak naast) de plaats ligt: eronder, rechts, erboven of
    // links ervan. Het puntje valt dan weg (het zou naast de echte plek staan). Ligt er niets op het puntje,
    // dan de naam links ervan. Geeft null als er nergens plek is.
    function besideMarker(style, name, anchor, blocked, free) {
        const w = textWidth(style, name);
        const reach = 10;
        const cover = blocked.find(b => anchor.x >= b[0] - reach && anchor.x <= b[2] + reach && anchor.y >= b[1] - reach && anchor.y <= b[3] + reach);
        if (!cover) {
            const box = [anchor.x - 6 - w - LABEL_GAP, anchor.y - 8, anchor.x + 8, anchor.y + 8];
            return free(box) ? { side: 'dot-left', at: [anchor.x, anchor.y], box } : null;
        }
        const cx = (cover[0] + cover[2]) / 2;
        const cy = (cover[1] + cover[3]) / 2;
        const spots = [
            ['below', cx, cover[3], [cx - w / 2 - LABEL_GAP, cover[3], cx + w / 2 + LABEL_GAP, cover[3] + 16]],
            ['right', cover[2], cy, [cover[2], cy - 8, cover[2] + w + LABEL_GAP, cy + 8]],
            ['above', cx, cover[1], [cx - w / 2 - LABEL_GAP, cover[1] - 16, cx + w / 2 + LABEL_GAP, cover[1]]],
            ['left', cover[0], cy, [cover[0] - w - LABEL_GAP, cy - 8, cover[0], cy + 8]],
        ];
        const spot = spots.find(([, , , box]) => free(box));
        return spot && { side: spot[0], at: [spot[1], spot[2]], box: spot[3] };
    }

    // Hoeveel foto's er rond een plek liggen: het rooster van state.points (opnieuw als die veranderen),
    // r cellen van ~1 km in elke richting
    function photosNear(lat, lon, r) {
        if (state.photoGridOf !== state.points || state.photoGridLen !== state.points.length) {
            state.photoGrid = new Map();
            for (const p of state.points) {
                const key = Math.floor(p[1] * PHOTO_CELL) * 100000 + Math.floor(p[2] * PHOTO_CELL);
                state.photoGrid.set(key, (state.photoGrid.get(key) || 0) + 1);
            }
            state.photoGridOf = state.points;
            state.photoGridLen = state.points.length;
        }
        if (!state.photoGrid.size) return 0;
        const y = Math.floor(lat * PHOTO_CELL);
        const x = Math.floor(lon * PHOTO_CELL);
        let n = 0;
        for (let dy = -r; dy <= r; dy++) {
            for (let dx = -r; dx <= r; dx++) n += state.photoGrid.get((y + dy) * 100000 + x + dx) || 0;
        }
        return n;
    }

    function labelElement(kind, name, capital, photos, side) {
        if (kind === 'country') {
            const lines = countryLines(name);
            return createEl('span', { class: 'map-country-label' }, ...lines.map(line => createEl('span', { class: 'map-country-label-line', text: line })));
        }
        if (kind === 'region') return createEl('span', { class: 'map-region', text: name });
        return createEl('span', {
            class: 'map-place' + (capital ? ' map-place-capital' : '') + (photos ? ' map-place-visited' : '') + (side ? ` map-place-beside is-${side}` : ''),
            text: name,
        });
    }

    // Een naam die moet wijken, vervaagt eerst (--dur-3, versnellend) en gaat dan pas van de kaart
    function retireLabel(marker) {
        const group = state.places;
        const el = marker.getElement()?.firstElementChild;
        const ms = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--dur-3')) || 0;
        if (!el || ms <= 0) { group?.removeLayer(marker); return; }
        el.classList.add('map-label-leaving');
        setTimeout(() => group?.removeLayer(marker), ms + 20);
    }

    // Wat boven de namen ligt, in px binnen de kaart: de groepen en spelden (die van markercluster op dit
    // zoomniveau; tijdens hun animatie hun eindplek), de knoppen van Leaflet en het label van een aangeduid land
    function labelObstacles(map) {
        const out = [];
        const group = state.cluster?._featureGroup;
        if (group && map.hasLayer(state.cluster)) {
            const size = { x: map.getContainer().clientWidth, y: map.getContainer().clientHeight };
            group.eachLayer(layer => {
                const ll = layer._backupLatlng || layer.getLatLng?.();
                if (!ll) return;
                const pt = map.latLngToContainerPoint(ll);
                if (pt.x < -60 || pt.y < -60 || pt.x > size.x + 60 || pt.y > size.y + 60) return;
                const n = layer.getChildCount ? layer.getChildCount() : 0;
                const r = (n ? (n < 10 ? 34 : n < 100 ? 40 : n < 1000 ? 48 : 56) : 18) / 2 + 2;
                out.push([pt.x - r, pt.y - r, pt.x + r, pt.y + r]);
            });
        }
        const origin = map.getContainer().getBoundingClientRect();
        const overlays = [...map.getContainer().querySelectorAll('.leaflet-control'), state.chip, state.hint];
        for (const el of overlays) {
            if (!el || el.hidden || !el.offsetParent) continue;
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) continue;
            out.push([r.left - origin.left - LABEL_GAP, r.top - origin.top - LABEL_GAP, r.right - origin.left + LABEL_GAP, r.bottom - origin.top + LABEL_GAP]);
        }
        return out;
    }

    function updateLabels() {
        const map = state.map;
        if (!map || state.osm || !state.placeData) return;
        const started = performance.now();
        if (!state.places) state.places = L.layerGroup([], { pane: 'places' }).addTo(map);
        const zoom = map.getZoom();
        const view = map.getBounds().pad(0.1);
        // Alleen wat echt in beeld is. Over de datumgrens (Japan, de Stille Oceaan) tekent de kaart het land maar
        // één keer, op zijn eigen lengte: een naam op een kopie (lengte ±360) zou in lege zee staan, en alle namen
        // van de wereld meetellen vulde MAX_LABELS met namen buiten beeld (geen Tokio en Peking meer)
        const visible = (lat, lon) => view.contains([lat, lon]);
        // Steden van de landen met detail; een wereldplaats op (bijna) dezelfde plek valt dan weg. Een stad
        // waar foto's genomen zijn, komt twee zoomstappen eerder; een kleinere plaats ook (anders vanaf zijn zoom).
        const shown = [];
        const near = [];
        for (const region of state.detailRegions) {
            for (const [name, lon, lat, minZoom, capital] of region.cities) {
                near.push([lon, lat]);
                if (zoom + 3.5 < minZoom || !visible(lat, lon)) continue;
                const photos = photosNear(lat, lon, 3);
                if (zoom + (photos ? 3.5 : 1.5) < minZoom) continue;
                shown.push(['city', name, lon, lat, minZoom, capital, photos]);
            }
        }
        for (const towns of state.detailTowns) {
            for (const [name, lon, lat, minZoom] of towns) {
                if (zoom + 2 < minZoom || !visible(lat, lon)) continue;
                const photos = photosNear(lat, lon, 1);
                if (zoom + (photos ? 2 : 0) < minZoom) continue;
                shown.push(['city', name, lon, lat, minZoom, 0, photos]);
            }
        }
        for (const [name, lon, lat, minZoom, capital] of state.placeData) {
            if (zoom + 1.5 < minZoom || (!capital && zoom < 3)) continue;
            if (!visible(lat, lon)) continue;
            if (near.some(([x, y]) => Math.abs(x - lon) < 0.12 && Math.abs(y - lat) < 0.08)) continue;
            shown.push(['city', name, lon, lat, minZoom, capital, photosNear(lat, lon, 3)]);
        }
        shown.sort((a, b) => (b[5] - a[5]) || (a[4] - b[4]));
        const capitals = shown.filter(p => p[5]);
        // Plaatsen met foto's: die met de meeste eerst, vóór de provincies en de andere plaatsen
        const visited = shown.filter(p => !p[5] && p[6]).sort((a, b) => b[6] - a[6]);
        const provinces = [];
        for (const region of state.detailRegions) {
            for (const [name, lon, lat] of region.labels || []) if (visible(lat, lon)) provinces.push(['region', name, lon, lat, 0, 0]);
        }
        // Landnamen: wie provincies toont, zet zijn eigen naam achter de provincies (anders verdringt
        // 'Nederland' midden in het land Gelderland en Utrecht)
        // Een land krijgt zijn naam als Natural Earth hem vanaf deze zoom toont (MIN_LABEL) en hij er
        // ongeveer in past; een dwergstaat (Monaco, Vaticaanstad) pas veel later en na de steden eromheen.
        const countries = [];
        const small = [];
        const later = [];
        for (const c of state.countryLabels) {
            if (!visible(c.lat, c.lon)) continue;
            const width = Math.max(...countryLines(c.name).map(line => textWidth('country', line)));
            const fit = countryFit(c, zoom, width);
            if (fit < 1 && zoom < c.minZoom - 0.5) continue;
            if (fit < 0.35 && zoom < c.minZoom + 2) continue;
            const row = ['country', c.name, c.lon, c.lat, 0, 0, c.code, c.box];
            (state.detailCodes.includes(c.code) ? later : fit < 0.35 ? small : countries).push(row);
        }
        // Volgorde = voorrang: landen, hoofdsteden, plaatsen met foto's, provincies, landen met detail en
        // dwergstaten, dan de andere steden en plaatsen. Een naam die over een eerdere heen zou vallen, blijft weg. Hetzelfde voor een naam
        // onder een groep of speld (die liggen erbovenop: 'Fr(134)k'), onder de knoppen en het label van een
        // aangeduid land, of half buiten de kaart. Een landnaam schuift dan eerst nog naar boven, onder of
        // binnen de rand, zolang hij in zijn land blijft.
        const canvas = map.getContainer();
        const size = { x: canvas.clientWidth, y: canvas.clientHeight };
        const boxes = [];
        const blocked = labelObstacles(map);
        const hits = (box, list) => list.some(b => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]);
        const inside = box => box[0] >= EDGE_GAP && box[1] >= EDGE_GAP && box[2] <= size.x - EDGE_GAP && box[3] <= size.y - EDGE_GAP;
        const free = box => inside(box) && !hits(box, boxes) && !hits(box, blocked);
        const ordered = [...countries, ...capitals, ...visited, ...provinces, ...later, ...small, ...shown.filter(p => !p[5] && !p[6])];
        const wanted = new Map();
        for (const row of ordered) {
            const [kind, name, lon, lat, , capital] = row;
            const [code, countryBox] = kind === 'country' ? row.slice(6) : [];
            const photos = kind === 'city' ? row[6] : 0;
            if (wanted.size >= MAX_LABELS) break;
            const anchor = map.latLngToContainerPoint([lat, lon]);
            const style = kind === 'city' && capital ? 'capital' : kind;
            let box = labelBox(style, name, anchor);
            if (!free(box) && photos) {
                // een plaats met foto's waar de groep bovenop ligt: de naam ernaast
                const spot = besideMarker(style, name, anchor, blocked, free);
                if (!spot) continue;
                boxes.push(spot.box);
                const ll = map.containerPointToLatLng(spot.at);
                const key = `${kind}:${name}:${lon}:${lat}:${spot.side}`;
                if (!wanted.has(key)) wanted.set(key, { kind, name, lon: ll.lng, lat: ll.lat, capital, photos, side: spot.side });
                continue;
            }
            if (!free(box)) {
                if (kind !== 'country' || hits(box, boxes)) continue;
                // een landnaam: binnen de rand, of net boven of onder de groep die erop ligt
                const dx = Math.max(EDGE_GAP - box[0], 0) + Math.min(size.x - EDGE_GAP - box[2], 0);
                const edgeDy = Math.max(EDGE_GAP - box[1], 0) + Math.min(size.y - EDGE_GAP - box[3], 0);
                const shifted = [box[0] + dx, box[1] + edgeDy, box[2] + dx, box[3] + edgeDy];
                const under = blocked.filter(b => hits(shifted, [b]));
                const down = Math.max(0, ...under.map(b => b[3] - box[1])) + LABEL_GAP;
                const up = Math.min(0, ...under.map(b => b[1] - box[3])) - LABEL_GAP;
                const moves = [[dx, edgeDy], [dx, down], [dx, up]].filter(([, my]) => Math.abs(my) <= COUNTRY_NUDGE_MAX);
                const fits = moves.find(([mx, my]) => {
                    const moved = [box[0] + mx, box[1] + my, box[2] + mx, box[3] + my];
                    const ll = map.containerPointToLatLng([anchor.x + mx, anchor.y + my]);
                    return (mx || my) && free(moved) && countryBox.contains(ll);
                });
                if (!fits) continue;
                box = [box[0] + fits[0], box[1] + fits[1], box[2] + fits[0], box[3] + fits[1]];
                const ll = map.containerPointToLatLng([anchor.x + fits[0], anchor.y + fits[1]]);
                boxes.push(box);
                const key = `${kind}:${name}:${lon}:${lat}:${Math.round(fits[0])}:${Math.round(fits[1])}`;
                if (!wanted.has(key)) wanted.set(key, { kind, name, lon: ll.lng, lat: ll.lat, capital, code });
                continue;
            }
            boxes.push(box);
            const key = `${kind}:${name}:${lon}:${lat}`;
            if (!wanted.has(key)) wanted.set(key, { kind, name, lon, lat, capital, code, photos });
        }
        // Wat blijft, blijft staan; wat weg moet, vervaagt; wat erbij komt, vloeit in (op volgorde)
        for (const [key, marker] of state.labelPool) {
            if (!wanted.has(key)) { state.labelPool.delete(key); retireLabel(marker); }
        }
        let fresh = 0;
        for (const [key, w] of wanted) {
            let marker = state.labelPool.get(key);
            if (!marker) {
                const el = labelElement(w.kind, w.name, w.capital, w.photos, w.side);
                el.classList.add('map-label-new');
                el.style.setProperty('--label-i', String(fresh++));
                if (w.code) el.dataset.code = w.code;
                marker = L.marker([w.lat, w.lon], {
                    interactive: false, keyboard: false, pane: 'places',
                    icon: L.divIcon({ className: 'map-place-wrap', iconSize: [0, 0], html: el }),
                }).addTo(state.places);
                state.labelPool.set(key, marker);
            } else {
                const ll = marker.getLatLng();
                if (ll.lat !== w.lat || ll.lng !== w.lon) marker.setLatLng([w.lat, w.lon]);
            }
            if (w.kind === 'country') marker.getElement()?.firstElementChild?.classList.toggle('is-selected', w.code === state.selected);
            if (w.kind === 'city') marker.getElement()?.firstElementChild?.classList.toggle('map-place-visited', !!w.photos);
        }
        state.labelMs = performance.now() - started;
    }

    function updateHint() {
        if (!state.map) return;
        const show = !state.osm && state.map.getZoom() >= HINT_ZOOM;
        if (!state.hint) {
            state.hint = createEl('div', { class: 'map-hint', role: 'note', hidden: true },
                createEl('i', { class: 'fas fa-circle-info', 'aria-hidden': 'true' }),
                ' Meer detail nodig? Zet “Gedetailleerde kaart” aan.');
            $('map-canvas').parentElement.appendChild(state.hint);
        }
        state.hint.hidden = !show;
    }

    // ---------- filters ----------

    function params(extra = {}) {
        const f = state.filters;
        const q = new URLSearchParams();
        if (f.album) q.set('album', f.album);
        else {
            if (f.year) q.set('year', f.year);
            if (f.date_from) q.set('date_from', f.date_from);
            if (f.date_to) q.set('date_to', f.date_to);
            if (f.camera) q.set('camera', f.camera);
        }
        Object.entries(extra).forEach(([k, v]) => q.set(k, v));
        return q;
    }

    function filtersActive() {
        return Object.values(state.filters).some(Boolean);
    }

    function option(value, label) {
        return createEl('option', { value, text: label });
    }

    async function loadFilterChoices() {
        if (state.facetsLoaded) return;
        state.facetsLoaded = true;
        const [facets, albums] = await Promise.all([
            fetchJSON(`${API_BASE}/api/library/facets`).catch(() => null),
            fetchJSON(`${API_BASE}/api/library/albums?with_hidden=false`).catch(() => null),
        ]);
        const year = $('map-year');
        year.replaceChildren(option('', 'Alle jaren'), ...((facets && facets.years) || []).slice().sort((a, b) => b[0] - a[0]).map(([y]) => option(String(y), String(y))));
        const camera = $('map-camera');
        camera.replaceChildren(option('', 'Alle camera’s'), ...((facets && facets.cameras) || []).map(c => option(c.value, c.label)));
        const album = $('map-album');
        album.replaceChildren(option('', 'Hele archief'), ...((albums && albums.albums) || []).filter(a => !a.hidden).map(a => option(String(a.id), a.name)));
        syncFilterControls();
    }

    function syncFilterControls() {
        const f = state.filters;
        $('map-year').value = f.year;
        $('map-date-from').value = f.date_from;
        $('map-date-to').value = f.date_to;
        // Een lege datum toont in WebKit de datum van vandaag: gedempt, zodat het geen filter lijkt
        $('map-date-from').classList.toggle('is-empty', !f.date_from);
        $('map-date-to').classList.toggle('is-empty', !f.date_to);
        $('map-camera').value = f.camera;
        $('map-album').value = f.album;
        const albumOn = !!f.album;
        ['map-year', 'map-date-from', 'map-date-to', 'map-camera'].forEach(id => { $(id).disabled = albumOn; });
        $('map-reset').hidden = !filtersActive();
    }

    // Van… tot…: een begindatum na de einddatum zegt de regel naast de datums meteen (zoals in
    // Albums); de kaart en de aantallen blijven bij het laatste geldige filter staan
    function showRangeError(key) {
        const error = $('map-range-error');
        if (!key) {
            error.hidden = true;
            error.textContent = '';
            $('map-date-from').removeAttribute('aria-invalid');
            $('map-date-to').removeAttribute('aria-invalid');
            return;
        }
        error.textContent = key === 'date_from' ? 'De begindatum ligt na de einddatum.' : 'De einddatum ligt voor de begindatum.';
        error.hidden = false;
        $(key === 'date_from' ? 'map-date-from' : 'map-date-to').setAttribute('aria-invalid', 'true');
    }

    function onFilter(key, value) {
        const f = state.filters;
        if (key === 'date_from' || key === 'date_to') {
            const from = $('map-date-from').value;
            const to = $('map-date-to').value;
            if (from && to && from > to) {
                showRangeError(key);
                return;
            }
            Object.assign(f, { date_from: from, date_to: to });
            if (from || to) f.year = '';
        } else {
            f[key] = value;
            if (key === 'year' && value) { f.date_from = ''; f.date_to = ''; }
            if (key === 'album' && value) Object.assign(f, { year: '', date_from: '', date_to: '', camera: '' });
        }
        showRangeError(null);
        syncFilterControls();
        loadPoints({ fit: true });
    }

    function bindControls() {
        $('map-year').addEventListener('change', e => onFilter('year', e.target.value));
        $('map-date-from').addEventListener('change', e => onFilter('date_from', e.target.value));
        $('map-date-to').addEventListener('change', e => onFilter('date_to', e.target.value));
        $('map-camera').addEventListener('change', e => onFilter('camera', e.target.value));
        $('map-album').addEventListener('change', e => onFilter('album', e.target.value));
        $('map-reset').addEventListener('click', () => {
            state.filters = { year: '', date_from: '', date_to: '', camera: '', album: '' };
            showRangeError(null);
            syncFilterControls();
            loadPoints({ fit: true });
        });
        $('map-osm-toggle').addEventListener('change', e => setOsm(e.target.checked));
        $('map-nogps').addEventListener('click', () => {
            // Dezelfde filters in Albums, maar dan alleen de foto's zonder locatie
            const q = params();
            q.delete('album');
            q.set('has_gps', 'false');
            history.pushState({ view: 'albums' }, '', `${location.pathname}${location.search}#albums?${q.toString()}`);
            switchMainView('albums');
        });
    }

    // ---------- punten laden ----------

    function setOverlay(node) {
        const box = $('map-overlay');
        if (!node) { box.hidden = true; box.replaceChildren(); return; }
        box.replaceChildren(node);
        box.hidden = false;
    }

    function setProgress(text) {
        const box = $('map-progress');
        box.hidden = !text;
        box.textContent = text || '';
    }

    function renderCounts() {
        const total = state.total;
        // Het aantal geschatte plekken staat op de knop 'Locatie geschat uit Tijdlijn'
        $('map-count').textContent = total
            ? `${fmt(total)} ${total === 1 ? 'foto' : 'foto’s'} op de kaart`
            : '';
        const none = $('map-nogps');
        none.hidden = !(state.withoutGps > 0 || total > 0);
        none.replaceChildren(createEl('i', { class: 'fas fa-location-crosshairs', 'aria-hidden': 'true' }),
            ` Zonder locatie: ${fmt(state.withoutGps)}`);
        // Het aantal telt alle bestanden zonder locatie (ook video's en audio), net als Albums
        none.setAttribute('aria-label', `Bestanden zonder locatie: ${fmt(state.withoutGps)}`);
        none.disabled = !!state.filters.album || !state.withoutGps;
        none.title = state.filters.album ? 'Kies “Hele archief” om ze in Albums te tonen' : 'Toon deze bestanden in Albums';
    }

    function makeMarker(p) {
        const estimated = p.length > 5;
        const marker = L.marker([p[1], p[2]], { icon: estimated ? state.estimatedIcon : state.pinIcon, pid: p[0], ts: p[3], kind: p[4], keyboard: true,
            title: markerTitle(p) + (estimated ? ' · locatie geschat uit Tijdlijn' : '') });
        state.markers.set(p[0], marker);
        return marker;
    }

    function markerTitle(p) {
        const kind = ['Foto', 'Video', 'Audio'][p[4]] || 'Foto';
        return p[3] ? `${kind} van ${new Date(p[3] * 1000).toLocaleDateString('nl-NL', { day: 'numeric', month: 'long', year: 'numeric' })}` : kind;
    }

    // De groepen (Leaflet.markercluster). Bij veel punten (meer dan CLUSTER_ANIMATE_MAX) of met
    // minder beweging (van de Mac, of Beweging in Instellingen: html[data-motion] is niet "volledig")
    // zonder de zoomanimatie van de groepen: die verschuift elke zichtbare groep apart en gaf
    // haperingen bij tienduizenden foto's. animate wordt alleen bij het maken gelezen, dus
    // dan komt er een nieuwe (lege) groep voor in de plaats.
    function useCluster(animate) {
        animate = animate && !reducedMotion();
        if (state.cluster && state.clusterAnimated === animate) return;
        if (state.cluster) state.map.removeLayer(state.cluster);
        state.clusterAnimated = animate;
        state.cluster = L.markerClusterGroup({
            chunkedLoading: true, chunkInterval: 120, chunkDelay: 30, animate,
            maxClusterRadius: 56, showCoverageOnHover: false, spiderfyOnMaxZoom: true, zoomToBoundsOnClick: true,
            iconCreateFunction: cluster => {
                const n = cluster.getChildCount();
                const size = n < 10 ? 'sm' : n < 100 ? 'md' : n < 1000 ? 'lg' : 'xl';
                const dim = { sm: 34, md: 40, lg: 48, xl: 56 }[size];
                return L.divIcon({
                    className: 'map-cluster-wrap',
                    html: `<span class="map-cluster map-cluster-${size}">${n < 1000 ? n : Math.round(n / 100) / 10 + 'k'}</span>`,
                    iconSize: [dim, dim],
                });
            },
        });
        state.cluster.on('click', e => markerClicked(e.layer));
        state.cluster.on('mouseover clustermouseover', e => hoverStart(e.layer));
        state.cluster.on('mouseout clustermouseout', e => { if (e.layer === state.hoverLayer) hideHoverSoon(); });
        state.cluster.on('clusterclick', hideHover);
        state.cluster.on('animationend', () => updateLabels());   // de groepen liggen op hun nieuwe plek
        state.map.addLayer(state.cluster);
    }

    async function loadPoints({ fit = false } = {}) {
        const token = ++state.loadToken;
        state.loading = true;
        state.cluster.clearLayers();
        state.markers.clear();
        state.points = [];
        state.inferredIds.clear();
        closePopup();
        setOverlay(null);
        setProgress('Kaart laden…');
        let after = 0;
        let first = true;
        try {
            for (;;) {
                const data = await fetchJSON(`${API_BASE}/api/map/points?${params({ after, limit: CHUNK })}`);
                if (token !== state.loadToken) return;
                if (first) {
                    first = false;
                    state.total = data.total;
                    state.withoutGps = data.without_gps;
                    state.version = data.version;
                    state.bounds = data.bounds;
                    useCluster(data.total <= CLUSTER_ANIMATE_MAX);
                    renderCounts();
                    if (!data.total && !state.showInferred) {
                        setProgress('');
                        setOverlay(noPointsState(data));
                        if (fit) state.map.setView([30, 10], 2);
                        break;
                    }
                    if (fit) fitToData();
                }
                for (const p of data.points) state.points.push(p);
                state.cluster.addLayers(data.points.map(makeMarker));
                if (data.next === null) break;
                after = data.next;
                setProgress(`Kaart laden… ${fmt(state.points.length)} van ${fmt(state.total)}`);
            }
            if (state.showInferred && token === state.loadToken) await loadInferred(token);
            setProgress('');
            if (token === state.loadToken) updateLabels();         // namen wijken voor de groepen
        } catch (err) {
            if (token !== state.loadToken) return;
            setProgress('');
            setOverlay(errorState('De kaart laden mislukte', err, () => loadPoints({ fit })));
        } finally {
            if (token === state.loadToken) state.loading = false;
        }
    }

    function noPointsState(data) {
        if (filtersActive()) {
            return emptyState({
                icon: 'fa-map-location-dot', title: 'Geen foto’s met locatie bij deze filters',
                text: 'Kies een ander jaar of wis de filters.',
                action: { label: 'Filters wissen', onClick: () => $('map-reset').click() },
            });
        }
        if (!data.ready) {
            return emptyState({
                icon: 'fa-hourglass-half', title: 'De bibliotheek wordt nog ingelezen',
                text: 'Zodra de index klaar is, verschijnen je foto’s hier op de kaart.',
            });
        }
        return emptyState({
            icon: 'fa-location-dot', title: 'Nog geen foto’s met locatie',
            text: 'Foto’s krijgen een plek op de kaart als er een GPS-positie in staat. Die lees je in via Albums of de bibliotheek.',
        });
    }

    function fitToData() {
        const b = state.bounds;
        if (!b) return;
        state.map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: [48, 48], maxZoom: 12, animate: false });
    }

    // ---------- een marker aanklikken: popup met miniatuur, viewer met buren ----------

    function closePopup() {
        hideHover();
        state.popupToken++;
        if (state.popup) { state.map.closePopup(state.popup); state.popup = null; }
    }

    // De foto's 'op deze plek': binnen een paar pixels van de aangeklikte, op datum, hoogstens NEIGHBOUR_MAX
    function neighboursOf(id) {
        const marker = state.markers.get(id);
        if (!marker) return [id];
        const { lat, lng } = marker.getLatLng();
        const dLon = NEIGHBOUR_PX * 360 / (256 * Math.pow(2, state.map.getZoom()));
        const dLat = dLon * Math.cos(lat * Math.PI / 180);
        const near = state.points.filter(p => Math.abs(p[1] - lat) <= dLat && Math.abs(p[2] - lng) <= dLon);
        near.sort((a, b) => (a[3] || 0) - (b[3] || 0) || a[0] - b[0]);
        let at = near.findIndex(p => p[0] === id);
        if (at < 0) return [id];
        const from = Math.max(0, Math.min(at - Math.floor(NEIGHBOUR_MAX / 2), near.length - NEIGHBOUR_MAX));
        return near.slice(from, from + NEIGHBOUR_MAX).map(p => p[0]);
    }

    async function markerClicked(layer) {
        const id = layer && layer.options && layer.options.pid;
        if (id === undefined) return;
        closePopup();
        const token = state.popupToken;
        const popup = L.popup({ className: 'map-popup', minWidth: 236, maxWidth: 260, autoPanPadding: [24, 24], closeButton: true })
            .setLatLng(layer.getLatLng())
            .setContent(createEl('div', { class: 'map-popup-body' }, createEl('div', { class: 'skeleton map-popup-img', 'aria-hidden': 'true' }), createEl('div', { class: 'map-popup-meta', text: 'Laden…' })))
            .openOn(state.map);
        state.popup = popup;
        try {
            const ids = neighboursOf(id);
            const data = await fetchJSON(`${API_BASE}/api/map/items?ids=${ids.join(',')}`);
            if (token !== state.popupToken) return;
            const items = data.items;
            const index = items.findIndex(i => i.id === id);
            if (index < 0) throw new Error('Dit bestand is niet meer op de kaart');
            popup.setContent(popupContent(items, index));
            const open = popup.getElement() && popup.getElement().querySelector('.map-popup-open');
            if (open) open.focus({ preventScroll: true });
        } catch (err) {
            if (token !== state.popupToken) return;
            popup.setContent(createEl('div', { class: 'map-popup-body' }, createEl('div', { class: 'map-popup-meta', text: err.message || 'Laden mislukt' })));
        }
    }

    function popupContent(items, index) {
        const file = items[index];
        const date = file.timestamp ? new Date(file.timestamp * 1000).toLocaleDateString('nl-NL', { day: 'numeric', month: 'long', year: 'numeric' }) : 'Datum onbekend';
        const open = () => { closePopup(); openViewer(items, index); };
        const thumb = file.type === 'audio'
            ? createEl('div', { class: 'map-popup-img map-popup-noimg' }, createEl('i', { class: 'fas fa-music', 'aria-hidden': 'true' }))
            : createEl('img', { class: 'map-popup-img', src: vaultFileUrl('thumb', file.path), alt: file.name, loading: 'lazy', draggable: 'false' });
        thumb.addEventListener('error', () => thumb.classList.add('map-popup-broken'));
        return createEl('div', { class: 'map-popup-body' },
            createEl('button', { type: 'button', class: 'map-popup-thumb', 'aria-label': `Open ${file.name}`, onClick: open }, thumb,
                file.type === 'video' ? createEl('i', { class: 'fas fa-play map-popup-badge', 'aria-hidden': 'true' }) : null),
            createEl('div', { class: 'map-popup-meta' },
                createEl('div', { class: 'map-popup-name', title: file.name, text: file.name }),
                createEl('div', { class: 'map-popup-date', text: date + (items.length > 1 ? ` · ${index + 1} van ${items.length} op deze plek` : '') }),
                state.inferredIds.has(file.id) ? createEl('div', { class: 'map-popup-estimated' },
                    createEl('i', { class: 'fas fa-route', 'aria-hidden': 'true' }), ' Locatie geschat uit Tijdlijn') : null),
            createEl('button', { type: 'button', class: 'btn btn-primary btn-sm map-popup-open', onClick: open },
                createEl('i', { class: `fas ${KIND_ICON[['photo', 'video', 'audio'].indexOf(file.type)] || 'fa-image'}`, 'aria-hidden': 'true' }),
                items.length > 1 ? ` Openen (${items.length})` : ' Openen'));
    }

    // ---------- over een pin of groep bewegen: voorvertoning met miniatuur ----------
    // Een kaartje boven de pin (of de groep) met de miniatuur, of bij een groep een raster van
    // hoogstens HOVER_THUMBS miniaturen over de hele periode. Het blijft HOVER_GRACE staan als
    // de muis de pin verlaat, zodat je naar het kaartje kunt: een miniatuur aanklikken opent de
    // viewer. Het verdwijnt bij verschuiven, zoomen en de popup.

    function hoverStart(layer) {
        if (!layer) return;
        clearTimeout(state.hoverHideTimer);
        if (layer === state.hoverLayer) return;
        hideHover();
        state.hoverLayer = layer;
        // De browser-tooltip (title) zou ernaast verschijnen: voor schermlezers blijft hij als aria-label
        const el = layer.getElement && layer.getElement();
        if (el && el.title) { el.setAttribute('aria-label', el.title); el.removeAttribute('title'); }
        state.hoverTimer = setTimeout(() => showHover(layer), HOVER_DELAY);
    }

    // De muis verlaat de pin of het kaartje: nog even laten staan, want misschien gaat hij erheen
    function hideHoverSoon() {
        clearTimeout(state.hoverHideTimer);
        if (!state.hover || !state.hover.classList.contains('is-visible')) { hideHover(); return; }
        state.hoverHideTimer = setTimeout(hideHover, HOVER_GRACE);
    }

    function hideHover() {
        clearTimeout(state.hoverTimer);
        clearTimeout(state.hoverHideTimer);
        state.hoverToken++;
        state.hoverLayer = null;
        const card = state.hover;
        if (card) card.classList.remove('is-visible');
    }

    function hoverDate(ts, opts) {
        return new Date(ts * 1000).toLocaleDateString('nl-NL', opts || { day: 'numeric', month: 'long', year: 'numeric' });
    }

    // Wat een groep toont: HOVER_THUMBS markers gelijk over de groep verdeeld (zonder te sorteren,
    // ook bij 100.000 foto's één keer erdoorheen), en de vroegste en laatste datum
    function clusterSample(cluster) {
        const all = cluster.getAllChildMarkers();
        let first = Infinity, last = -Infinity;
        const kinds = [0, 0, 0];
        for (const m of all) {
            const ts = m.options.ts;
            if (ts) { if (ts < first) first = ts; if (ts > last) last = ts; }
            kinds[m.options.kind || 0]++;
        }
        const pick = [];
        const step = all.length / Math.min(HOVER_THUMBS, all.length);
        for (let i = 0; i < Math.min(HOVER_THUMBS, all.length); i++) pick.push(all[Math.floor(i * step)]);
        pick.sort((a, b) => (a.options.ts || 0) - (b.options.ts || 0));
        return { markers: pick, count: all.length, kinds, first: isFinite(first) ? first : 0, last: isFinite(last) ? last : 0 };
    }

    // '9 foto's', '3 video's', of bij een mengsel '12 bestanden'
    function countText(count, kinds) {
        const only = kinds.findIndex(k => k === count);
        const [one, many] = [['foto', 'foto’s'], ['video', 'video’s'], ['audiobestand', 'audiobestanden']][only] || ['bestand', 'bestanden'];
        return `${fmt(count)} ${count === 1 ? one : many}`;
    }

    function periodText(first, last) {
        if (!first) return '';
        const month = { month: 'short', year: 'numeric' };
        const a = hoverDate(first, month), b = hoverDate(last, month);
        if (a === b) return new Date(first * 1000).toDateString() === new Date(last * 1000).toDateString() ? hoverDate(first) : a;
        return `${a} – ${b}`;
    }

    function hoverThumb(marker, i, cluster) {
        const kind = marker.options.kind || 0;
        // Alleen met de muis: het kaartje is aria-hidden, met het toetsenbord opent Enter de popup
        const tile = createEl('button', { type: 'button', tabindex: '-1', class: 'map-hover-thumb skeleton', 'data-pid': String(marker.options.pid),
            onClick: () => openFromHover(marker, cluster) });
        tile.style.setProperty('--hover-i', String(i));  // kleine vertraging per miniatuur bij het infaden
        if (kind === 2) {
            tile.classList.remove('skeleton');
            tile.classList.add('map-hover-noimg');
            tile.append(createEl('i', { class: 'fas fa-music', 'aria-hidden': 'true' }));
        } else if (kind === 1) {
            tile.append(createEl('i', { class: 'fas fa-play map-hover-badge', 'aria-hidden': 'true' }));
        }
        return tile;
    }

    function hoverCard() {
        if (state.hover) return state.hover;
        state.hover = createEl('div', { class: 'map-hover', 'aria-hidden': 'true' });
        // Klikken en scrollen op het kaartje verschuiven of zoomen de kaart niet
        L.DomEvent.disableClickPropagation(state.hover);
        L.DomEvent.disableScrollPropagation(state.hover);
        state.hover.addEventListener('mouseenter', () => clearTimeout(state.hoverHideTimer));
        state.hover.addEventListener('mouseleave', hideHoverSoon);
        state.map.getContainer().append(state.hover);
        return state.hover;
    }

    async function showHover(layer) {
        if (layer !== state.hoverLayer || !state.map.hasLayer(layer)) return;
        if (state.popup && state.map.hasLayer(state.popup)) return;
        const token = ++state.hoverToken;
        const isCluster = typeof layer.getAllChildMarkers === 'function';
        let markers, title, meta;
        if (isCluster) {
            const s = clusterSample(layer);
            markers = s.markers;
            title = countText(s.count, s.kinds);
            meta = periodText(s.first, s.last);
        } else {
            markers = [layer];
            const ts = layer.options.ts;
            title = ts ? hoverDate(ts) : 'Datum onbekend';
            const kind = ['Foto', 'Video', 'Audio'][layer.options.kind || 0];
            meta = state.inferredIds.has(layer.options.pid) ? `${kind} · locatie geschat uit Tijdlijn`
                : ts ? `${kind} · ${new Date(ts * 1000).toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' })}` : kind;
        }
        const card = hoverCard();
        card.classList.remove('is-visible');
        card.classList.toggle('is-group', markers.length > 1);
        card.replaceChildren(
            createEl('div', { class: `map-hover-grid map-hover-grid-${markers.length}` },
                ...markers.map((m, i) => hoverThumb(m, i, isCluster ? layer : null))),
            createEl('div', { class: 'map-hover-meta' },
                createEl('div', { class: 'map-hover-text' },
                    createEl('div', { class: 'map-hover-title', text: title }),
                    meta ? createEl('div', { class: 'map-hover-sub', text: meta }) : null),
                isCluster ? createEl('button', { type: 'button', tabindex: '-1', class: 'btn btn-ghost btn-sm map-hover-zoom',
                    onClick: () => { hideHover(); layer.zoomToBounds({ padding: [48, 48] }); } },
                    createEl('i', { class: 'fas fa-magnifying-glass-plus', 'aria-hidden': 'true' }), ' Inzoomen') : null));
        placeHover(card, layer);
        requestAnimationFrame(() => { if (token === state.hoverToken) card.classList.add('is-visible'); });

        const missing = markers.map(m => m.options.pid).filter(id => !state.hoverItems.has(id));
        if (missing.length) {
            try {
                const data = await fetchJSON(`${API_BASE}/api/map/items?ids=${missing.join(',')}`);
                for (const item of data.items) state.hoverItems.set(item.id, item);
                while (state.hoverItems.size > HOVER_CACHE_MAX) state.hoverItems.delete(state.hoverItems.keys().next().value);
            } catch (err) {
                // Zonder miniatuur blijft het kaartje met datum staan
            }
            if (token !== state.hoverToken) return;
        }
        for (const tile of card.querySelectorAll('.map-hover-thumb[data-pid]')) {
            const item = state.hoverItems.get(Number(tile.dataset.pid));
            if (!item || !item.path || item.type === 'audio') {
                tile.classList.remove('skeleton');
                if (!item || !item.path) tile.classList.add('map-hover-broken');
                continue;
            }
            const img = createEl('img', { src: vaultFileUrl('thumb', item.path), alt: '', draggable: 'false', decoding: 'async' });
            img.addEventListener('load', () => { tile.classList.remove('skeleton'); tile.classList.add('is-loaded'); });
            img.addEventListener('error', () => { tile.classList.remove('skeleton'); tile.classList.add('map-hover-broken'); });
            tile.prepend(img);
        }
    }

    // Een miniatuur in het kaartje aangeklikt: de viewer, met de foto's op deze plek (een pin) of
    // de foto's van de groep op datum rond de aangeklikte, hoogstens NEIGHBOUR_MAX
    async function openFromHover(marker, cluster) {
        const id = marker.options.pid;
        hideHover();
        let ids;
        if (cluster) {
            const all = cluster.getAllChildMarkers()
                .sort((a, b) => (a.options.ts || 0) - (b.options.ts || 0) || a.options.pid - b.options.pid);
            const at = all.indexOf(marker);
            const from = Math.max(0, Math.min(at - Math.floor(NEIGHBOUR_MAX / 2), all.length - NEIGHBOUR_MAX));
            ids = all.slice(from, from + NEIGHBOUR_MAX).map(m => m.options.pid);
        } else {
            ids = neighboursOf(id);
        }
        try {
            const data = await fetchJSON(`${API_BASE}/api/map/items?ids=${ids.join(',')}`);
            const index = data.items.findIndex(i => i.id === id);
            if (index < 0) throw new Error('Dit bestand is niet meer op de kaart');
            openViewer(data.items, index);
        } catch (err) {
            toastFetchError('Openen mislukt', err);
        }
    }

    // Boven de pin, anders eronder, en past het daar ook niet: ernaast; altijd binnen de kaart
    function placeHover(card, layer) {
        const map = state.map;
        const size = map.getSize();
        const at = map.latLngToContainerPoint(layer.getLatLng());
        const icon = layer.getElement && layer.getElement();
        const half = icon ? icon.offsetHeight / 2 : 9;
        const w = card.offsetWidth, h = card.offsetHeight;
        const gap = 10, edge = 8;
        const clamp = (v, max) => Math.max(edge, Math.min(max - edge, v));
        let side = 'above', left = at.x - w / 2, top = at.y - half - gap - h;
        if (top < edge) {
            if (at.y + half + gap + h <= size.y - edge) { side = 'below'; top = at.y + half + gap; }
            else {
                side = at.x + half + gap + w <= size.x - edge || at.x < size.x / 2 ? 'right' : 'left';
                left = side === 'right' ? at.x + half + gap : at.x - half - gap - w;
                top = at.y - h / 2;
            }
        }
        left = clamp(left, size.x - w);
        top = clamp(top, size.y - h);
        card.dataset.side = side;
        card.style.left = `${Math.round(left)}px`;
        card.style.top = `${Math.round(top)}px`;
        card.style.setProperty('--origin-x', `${Math.round(at.x - left)}px`);
        card.style.setProperty('--origin-y', `${Math.round(at.y - top)}px`);
    }

    // ---------- vanuit de viewer: 'Op kaart tonen' ----------

    window.mapViewerHook = function (file) {
        const btn = $('btn-show-on-map');
        if (!btn) return;
        const known = file.has_gps === true || typeof file.lat === 'number';
        btn.hidden = !known;
        if (!['photo', 'video'].includes(file.type) || !file.path) return;
        // Plaats, camera en afmetingen voor de gegevens, en zonder GPS misschien een plek
        // geschat uit de Tijdlijn (één keer per bestand gevraagd)
        const answer = file.map_locate || fetchJSON(`${API_BASE}/api/map/locate?path=${encodeURIComponent(file.path)}`);
        file.map_locate = answer;
        Promise.resolve(answer).then(found => {
            file.has_gps = found.has_gps;
            if (found.has_gps) { file.lat = found.lat; file.lon = found.lon; file.map_id = found.id; }
            if (viewerFile() !== file) return;
            btn.hidden = !found.has_gps && !found.inferred;
            if (typeof viewerAddDetails === 'function') viewerAddDetails(file, found);
            if (found.inferred) { file.map_estimate = found; showEstimate(found); }
        }).catch(() => { file.map_locate = null; });
    };

    // 'Locatie (geschat)' in de gegevens van de viewer: alleen in DataVault, nooit in het bestand
    function showEstimate(found) {
        const meta = $('modal-metadata') && $('modal-metadata').querySelector('.metadata-grid');
        if (!meta || meta.querySelector('.metadata-estimated')) return;
        const est = found.inferred;
        const how = { bezoek: 'een bezochte plek', interpolatie: 'twee punten ervoor en erna', dichtstbij: 'het dichtstbijzijnde punt' }[est.method] || 'je Tijdlijn';
        const tz = { exif: 'tijdzone uit de foto', locatie: 'tijdzone van de plek', aangenomen: 'tijdzone aangenomen: Brussel', utc: 'tijd van de video' }[est.tz_source] || '';
        // Zoals de Plaats van een foto met GPS: de plaats als waarde, de provincie en de kust stil eronder,
        // en daaronder de technische waarden (de positie in mono, hoe zeker en hoe nauwkeurig)
        const coords = createEl('span', { class: 'mono', text: `${est.lat.toFixed(4)}, ${est.lon.toFixed(4)}` });
        const sure = `${Math.round(est.confidence * 100)}% zeker · ±${fmt(Math.round(est.accuracy_m || 0))} m`;
        const note = est.place && typeof viewerPlaceNote === 'function' ? viewerPlaceNote(est.place, est.place_parts) : null;
        meta.append(
            createEl('span', { class: 'metadata-label metadata-estimated', text: 'Locatie (geschat)' }),
            createEl('span', { class: 'metadata-value', title: `Afgeleid uit ${how}; ${tz}. Alleen in DataVault, niet in het bestand.` },
                ...(est.place
                    ? [est.place, note, createEl('span', { class: 'metadata-note' }, coords, ` · ${sure}`)]
                    : [coords, createEl('span', { class: 'metadata-note', text: sure })])));
    }

    window.mapShowViewerFile = async function () {
        const file = viewerFile();
        if (!file) return;
        if (file.map_estimate && !file.has_gps) {
            // Geen GPS, wel een plek geschat uit de Tijdlijn: die laag aan en daarheen
            const found = file.map_estimate;
            closeModal();
            switchMainView('map');
            await state.inited;
            if (!state.showInferred) await setInferred(true);
            focusPoint(found.id, found.inferred.lat, found.inferred.lon);
            return;
        }
        let id = file.map_id !== undefined ? file.map_id : file.id;
        let lat = file.lat, lon = file.lon;
        if (typeof lat !== 'number' || id === undefined) {
            try {
                const found = await fetchJSON(`${API_BASE}/api/map/locate?path=${encodeURIComponent(file.path)}`);
                if (!found.has_gps) { showToast('Geen locatie', 'Deze foto heeft geen GPS-positie.', 'info'); return; }
                ({ lat, lon } = found);
                id = found.id;
            } catch (err) { toastFetchError('De plek opzoeken mislukte', err); return; }
        }
        closeModal();
        switchMainView('map');
        await state.inited;
        focusPoint(id, lat, lon);
    };

    // ---------- locatiegeschiedenis: geschatte plekken en de route van een dag ----------

    async function loadHistorySummary() {
        try {
            state.history = await fetchJSON(`${API_BASE}/api/location/summary`);
        } catch (err) {
            state.history = null;
        }
        renderHistoryControls();
    }

    function renderHistoryControls() {
        const bar = $('map-layers');
        const h = state.history;
        const any = h && (h.points > 0 || h.inferred > 0);
        bar.hidden = !any;
        if (!any) return;
        const inferred = $('map-inferred');
        inferred.hidden = !h.inferred;
        inferred.setAttribute('aria-pressed', state.showInferred ? 'true' : 'false');
        $('map-inferred-count').textContent = fmt(h.inferred);
        const trackBtn = $('map-track');
        trackBtn.hidden = !h.points;
        trackBtn.setAttribute('aria-pressed', state.trackOn ? 'true' : 'false');
        $('map-track-controls').hidden = !state.trackOn;
        $('map-track-day').value = state.trackDay;
        const i = state.trackDays.indexOf(state.trackDay);
        $('map-track-prev').disabled = i <= 0;
        $('map-track-next').disabled = i < 0 || i >= state.trackDays.length - 1;
    }

    async function loadInferred(token) {
        let after = 0;
        for (;;) {
            const data = await fetchJSON(`${API_BASE}/api/map/inferred?${params({ after, limit: CHUNK })}`);
            if (token !== state.loadToken) return;
            state.inferredTotal = data.total;
            data.points.forEach(p => { state.inferredIds.add(p[0]); state.points.push(p); });
            state.cluster.addLayers(data.points.map(makeMarker));
            if (data.next === null) break;
            after = data.next;
        }
        if (!state.total && state.inferredTotal && state.points.length) {
            const lats = state.points.map(p => p[1]);
            const lons = state.points.map(p => p[2]);
            state.bounds = [Math.min(...lats), Math.min(...lons), Math.max(...lats), Math.max(...lons)];
            fitToData();
        }
        renderCounts();
    }

    async function setInferred(on) {
        state.showInferred = on;
        renderHistoryControls();
        await loadPoints({ fit: false });
    }

    async function setTrack(on) {
        state.trackOn = on;
        if (on && !state.trackDays.length) {
            try {
                state.trackDays = (await fetchJSON(`${API_BASE}/api/location/days`)).days.map(d => d.day);
            } catch (err) { toastFetchError('De Tijdlijn laden mislukte', err); }
        }
        if (on && !state.trackDay && state.trackDays.length) state.trackDay = state.trackDays[state.trackDays.length - 1];
        renderHistoryControls();
        if (on) drawTrack(); else clearTrack();
        if (on) $('map-track-day').focus();
    }

    function clearTrack() {
        state.trackToken++;
        if (state.track) { state.map.removeLayer(state.track); state.track = null; }
        $('map-track-info').textContent = '';
    }

    async function drawTrack() {
        clearTrack();
        const day = state.trackDay;
        if (!day) return;
        const token = state.trackToken;
        $('map-track-info').textContent = 'Laden…';
        try {
            const data = await fetchJSON(`${API_BASE}/api/location/track?day=${encodeURIComponent(day)}`);
            if (token !== state.trackToken) return;
            const layer = L.layerGroup();
            const line = data.points.map(p => [p[1], p[2]]);
            const route = line.length > 1 ? L.polyline(line, { className: 'map-track-line', interactive: false, smoothFactor: 1.5 }).addTo(layer) : null;
            const visits = [];
            data.visits.forEach(v => {
                const from = new Date(v.start).toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
                const until = new Date(v.end).toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
                visits.push(L.circleMarker([v.lat, v.lon], { radius: 7, className: 'map-track-visit' })
                    .bindTooltip(`${v.name || v.address || 'Bezochte plek'} · ${from}–${until}`, { className: 'map-track-tip', direction: 'top' })
                    .addTo(layer));
            });
            if (line.length === 1) L.circleMarker(line[0], { radius: 5, className: 'map-track-visit' }).addTo(layer);
            layer.addTo(state.map);
            state.track = layer;
            const all = line.concat(data.visits.map(v => [v.lat, v.lon]));
            $('map-track-info').textContent = all.length
                ? `${fmt(data.points.length)} ${data.points.length === 1 ? 'punt' : 'punten'}, ${fmt(data.visits.length)} ${data.visits.length === 1 ? 'plek' : 'plekken'}`
                : 'Geen locatie op deze dag';
            if (all.length) state.map.fitBounds(L.latLngBounds(all), { padding: [64, 64], maxZoom: 15 });
            if (window.MotionData) MotionData.mapRoute(state.map, route, visits);
        } catch (err) {
            if (token !== state.trackToken) return;
            $('map-track-info').textContent = '';
            toastFetchError('De route laden mislukte', err);
        }
    }

    function stepTrack(delta) {
        const i = state.trackDays.indexOf(state.trackDay);
        const next = state.trackDays[i + delta];
        if (!next) return;
        state.trackDay = next;
        renderHistoryControls();
        drawTrack();
    }

    function bindHistoryControls() {
        $('map-inferred').addEventListener('click', () => setInferred(!state.showInferred));
        $('map-track').addEventListener('click', () => setTrack(!state.trackOn));
        $('map-track-prev').addEventListener('click', () => stepTrack(-1));
        $('map-track-next').addEventListener('click', () => stepTrack(1));
        $('map-track-day').addEventListener('change', e => {
            state.trackDay = e.target.value;
            renderHistoryControls();
            drawTrack();
        });
    }

    async function focusPoint(id, lat, lon) {
        if (state.loading) await new Promise(done => { const t = setInterval(() => { if (!state.loading) { clearInterval(t); done(); } }, 80); });
        if (!state.markers.has(id) && filtersActive()) {
            // De foto valt buiten de filters: eerst die weg
            state.filters = { year: '', date_from: '', date_to: '', camera: '', album: '' };
            showRangeError(null);
            syncFilterControls();
            await loadPoints({ fit: false });
        }
        const marker = state.markers.get(id);
        state.map.invalidateSize();
        if (!marker) { state.map.setView([lat, lon], 15); return; }
        state.cluster.zoomToShowLayer(marker, () => markerClicked(marker));
    }

    // ---------- openen ----------

    window.mapInit = function () {
        if (!state.inited) {
            state.inited = (async () => {
                await loadOsmSetting();
                build();
                bindControls();
                bindHistoryControls();
                loadHistorySummary();
                loadFilterChoices();
                await loadPoints({ fit: true });
            })();
            return state.inited;
        }
        // Teruggekomen: de kaart houdt zijn plek, tenzij de bibliotheek intussen veranderde
        state.map.invalidateSize();
        loadHistorySummary();
        state.inited.then(async () => {
            if (state.loading) return;
            try {
                const data = await fetchJSON(`${API_BASE}/api/map/points?${params({ limit: 1 })}`);
                if (data.version !== state.version) loadPoints({ fit: !state.total });
            } catch (err) { /* de vorige kaart blijft staan */ }
        });
        return state.inited;
    };

    // Vanuit de chat (Pasha): de kaart op een gebied zetten, [zuid, west, noord, oost]
    window.mapFocus = async function (bounds) {
        await mapInit();
        state.map.invalidateSize();
        state.map.fitBounds([[bounds[0], bounds[1]], [bounds[2], bounds[3]]], { padding: [48, 48], maxZoom: 12, animate: false });
    };

    // Voor de browsertest: een land aanduiden zoals een klik erop doet
    window.mapSelectCountry = a3 => selectCountry(a3);

    // Voor de browsertest (tests/ui/smoke_webkit.py): wat de kaart nu toont
    window.mapView = () => (state.map ? {
        zoom: state.map.getZoom(), markers: state.markers.size, osm: state.osm, popup: !!(state.popup && state.map.hasLayer(state.popup)),
        tiles: !!(state.tiles && state.map.hasLayer(state.tiles)),
        estimated: state.inferredIds.size, track: !!state.track, trackDay: state.trackDay,
        selected: state.selected, detail: state.detailCodes.slice(),
        labelMs: state.labelMs || 0,
        countryLabels: [...state.labelPool.keys()].filter(k => k.startsWith('country:')).map(k => k.split(':')[1]),
        labels: [...state.labelPool.keys()].map(k => { const [kind, name, lon, lat] = k.split(':'); return { kind, name, lon: +lon, lat: +lat }; }),
    } : null);

    window.mapVaultReturned = function () {
        if (state.inited) loadPoints({ fit: true });
    };
})();
