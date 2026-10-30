// ==================== KAART ====================
// Foto's en video's met een GPS-positie (uit EXIF, door de index gelezen) op een kaart.
// Alles komt uit /api/map/...; er wordt nooit iets gekopieerd of verplaatst.
//
// De basiskaart werkt zonder internet: landsgrenzen en grote steden van Natural Earth
// (publiek domein, vendor/naturalearth/), getekend als GeoJSON-laag in de kleuren van het
// thema (CSS-variabelen --map-*). Alleen als de gebruiker 'Gedetailleerde kaart
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
    const CHUNK = 10000;
    const NEIGHBOUR_PX = 28;            // foto's binnen zoveel pixels van de aangeklikte horen bij 'deze plek'
    const NEIGHBOUR_MAX = 200;          // zoveel bladert de viewer er hoogstens doorheen
    const MAX_LABELS = 120;
    const HINT_ZOOM = 9;                // vanaf hier is de basiskaart zonder internet te grof
    const KIND_ICON = ['fa-image', 'fa-film', 'fa-music'];
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
        osm: false, osmLoaded: false,
        filters: { year: '', date_from: '', date_to: '', camera: '', album: '' },
        points: [], markers: new Map(), total: 0, withoutGps: 0, version: '', bounds: null,
        loadToken: 0, loading: false, popup: null, popupToken: 0, facetsLoaded: false,
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
        map.createPane('places').style.zIndex = 450;
        map.getPane('places').style.pointerEvents = 'none';
        state.map = map;

        const pin = L_.divIcon({ className: 'map-pin-wrap', html: '<span class="map-pin"></span>', iconSize: [18, 18], iconAnchor: [9, 9] });
        state.pinIcon = pin;
        state.estimatedIcon = L_.divIcon({ className: 'map-pin-wrap', html: '<span class="map-pin map-pin-estimated"></span>', iconSize: [18, 18], iconAnchor: [9, 9] });
        state.cluster = L_.markerClusterGroup({
            chunkedLoading: true, chunkInterval: 120, chunkDelay: 30,
            maxClusterRadius: 56, showCoverageOnHover: false, spiderfyOnMaxZoom: true, zoomToBoundsOnClick: true,
            iconCreateFunction: cluster => {
                const n = cluster.getChildCount();
                const size = n < 10 ? 'sm' : n < 100 ? 'md' : n < 1000 ? 'lg' : 'xl';
                const dim = { sm: 34, md: 40, lg: 48, xl: 56 }[size];
                return L_.divIcon({
                    className: 'map-cluster-wrap',
                    html: `<span class="map-cluster map-cluster-${size}">${n < 1000 ? n : Math.round(n / 100) / 10 + 'k'}</span>`,
                    iconSize: [dim, dim],
                });
            },
        });
        state.cluster.on('click', e => markerClicked(e.layer));
        map.addLayer(state.cluster);
        map.getContainer().setAttribute('aria-label', "Kaart met foto's op locatie. Pijltjestoetsen verschuiven, plus en min zoomen, Enter opent een groep of foto.");

        map.on('zoomend moveend', () => { updateLabels(); updateHint(); });
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
                style: () => ({ className: 'map-country', weight: 1 }),
                onEachFeature: (feature, layer) => {
                    if (feature.properties && feature.properties.n) layer.bindTooltip(feature.properties.n, { sticky: true, className: 'map-tip', direction: 'top' });
                },
                attribution: 'Basiskaart: Natural Earth',
            });
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
            if (state.places) { map.removeLayer(state.places); state.places = null; }
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
            updateLabels();
        }
        map.getContainer().classList.toggle('map-online', wantOsm);
        updateHint();
    }

    function updateLabels() {
        const map = state.map;
        if (!map || state.osm || !state.placeData) return;
        if (!state.places) state.places = L.layerGroup([], { pane: 'places' }).addTo(map);
        state.places.clearLayers();
        const zoom = map.getZoom();
        const view = map.getBounds().pad(0.1);
        const wrap = view.getWest() < -180 || view.getEast() > 180;
        const shown = [];
        for (const [name, lon, lat, minZoom, capital] of state.placeData) {
            if (zoom + 1.5 < minZoom || (!capital && zoom < 3)) continue;
            if (!wrap && !view.contains([lat, lon])) continue;
            shown.push([name, lon, lat, minZoom, capital]);
        }
        shown.sort((a, b) => (b[4] - a[4]) || (a[3] - b[3]));
        // Eerst de belangrijkste; een naam die over een eerdere heen valt, blijft weg
        const boxes = [];
        let count = 0;
        for (const [name, lon, lat, , capital] of shown) {
            if (count >= MAX_LABELS) break;
            const at = map.latLngToContainerPoint([lat, lon]);
            const box = [at.x - 8, at.y - 8, at.x + 14 + name.length * 6.2, at.y + 8];
            if (boxes.some(b => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) continue;
            boxes.push(box);
            count++;
            L.marker([lat, lon], {
                interactive: false, keyboard: false, pane: 'places',
                icon: L.divIcon({
                    className: 'map-place-wrap', iconSize: [0, 0],
                    html: createEl('span', { class: 'map-place' + (capital ? ' map-place-capital' : ''), text: name }),
                }),
            }).addTo(state.places);
        }
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

    function onFilter(key, value) {
        const f = state.filters;
        f[key] = value;
        if (key === 'year' && value) { f.date_from = ''; f.date_to = ''; }
        if ((key === 'date_from' || key === 'date_to') && value) f.year = '';
        if (key === 'album' && value) Object.assign(f, { year: '', date_from: '', date_to: '', camera: '' });
        if (f.date_from && f.date_to && f.date_from > f.date_to) {
            showToast('Datums omgedraaid', 'De begindatum lag na de einddatum; de kaart toont niets.', 'warning');
        }
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
            syncFilterControls();
            loadPoints({ fit: true });
        });
        $('map-osm-toggle').addEventListener('change', e => setOsm(e.target.checked));
        $('map-nogps').addEventListener('click', () => {
            // Dezelfde filters in Albums, maar dan alleen de foto's zonder locatie
            const q = params();
            q.delete('album');
            q.set('has_gps', 'false');
            location.hash = `#albums?${q.toString()}`;
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
            ` Foto’s zonder locatie: ${fmt(state.withoutGps)}`);
        none.setAttribute('aria-label', `Foto\u2019s zonder locatie: ${fmt(state.withoutGps)}`);
        none.disabled = !!state.filters.album || !state.withoutGps;
        none.title = state.filters.album ? 'Kies “Hele archief” om ze in Albums te tonen' : 'Toon deze foto’s in Albums';
    }

    function makeMarker(p) {
        const estimated = p.length > 5;
        const marker = L.marker([p[1], p[2]], { icon: estimated ? state.estimatedIcon : state.pinIcon, pid: p[0], keyboard: true,
            title: markerTitle(p) + (estimated ? ' · locatie geschat uit Tijdlijn' : '') });
        state.markers.set(p[0], marker);
        return marker;
    }

    function markerTitle(p) {
        const kind = ['Foto', 'Video', 'Audio'][p[4]] || 'Foto';
        return p[3] ? `${kind} van ${new Date(p[3] * 1000).toLocaleDateString('nl-NL', { day: 'numeric', month: 'long', year: 'numeric' })}` : kind;
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
        meta.append(
            createEl('span', { class: 'metadata-label metadata-estimated', text: 'Locatie (geschat)' }),
            createEl('span', { class: 'metadata-value', title: `Afgeleid uit ${how}; ${tz}. Alleen in DataVault, niet in het bestand.` },
                createEl('span', { class: 'mono', text: `${est.lat.toFixed(4)}, ${est.lon.toFixed(4)}` }),
                ` · ${Math.round(est.confidence * 100)}% zeker · ±${fmt(Math.round(est.accuracy_m || 0))} m`));
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
            if (line.length > 1) L.polyline(line, { className: 'map-track-line', interactive: false, smoothFactor: 1.5 }).addTo(layer);
            data.visits.forEach(v => {
                const from = new Date(v.start).toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
                const until = new Date(v.end).toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
                L.circleMarker([v.lat, v.lon], { radius: 7, className: 'map-track-visit' })
                    .bindTooltip(`${v.name || v.address || 'Bezochte plek'} · ${from}–${until}`, { className: 'map-track-tip', direction: 'top' })
                    .addTo(layer);
            });
            if (line.length === 1) L.circleMarker(line[0], { radius: 5, className: 'map-track-visit' }).addTo(layer);
            layer.addTo(state.map);
            state.track = layer;
            const all = line.concat(data.visits.map(v => [v.lat, v.lon]));
            $('map-track-info').textContent = all.length
                ? `${fmt(data.points.length)} ${data.points.length === 1 ? 'punt' : 'punten'}, ${fmt(data.visits.length)} ${data.visits.length === 1 ? 'plek' : 'plekken'}`
                : 'Geen locatie op deze dag';
            if (all.length) state.map.fitBounds(L.latLngBounds(all), { padding: [64, 64], maxZoom: 15 });
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

    // Voor de browsertest (tests/ui/smoke_webkit.py): wat de kaart nu toont
    window.mapView = () => (state.map ? {
        zoom: state.map.getZoom(), markers: state.markers.size, osm: state.osm, popup: !!(state.popup && state.map.hasLayer(state.popup)),
        tiles: !!(state.tiles && state.map.hasLayer(state.tiles)),
        estimated: state.inferredIds.size, track: !!state.track, trackDay: state.trackDay,
    } : null);

    window.mapVaultReturned = function () {
        if (state.inited) loadPoints({ fit: true });
    };
})();
