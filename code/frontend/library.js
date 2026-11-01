// ==================== ALBUMS ====================
// De bibliotheek (de media-index van de server) als één scherm: alles in het archief met
// filters, de slimme albums en 'Op deze dag'. Alles komt uit /api/library/...; een album is
// een opgeslagen filter en dus altijd actueel: er wordt nooit iets gekopieerd of verplaatst.
//
// Het adres houdt bij wat je ziet, zodat je een weergave kunt bewaren of delen met jezelf:
//   #albums                          alles
//   #albums?kind=photo&year=2019     met filters (dezelfde namen als de server: library_filters.py)
//   #albums/album/3                  een slim album (met ?... erachter: aangepast, nog niet opgeslagen)
//   #albums/op-deze-dag[/07-14]      op deze dag (vandaag, of een andere dag)
//
// Het raster is gevirtualiseerd: alleen de rijen in beeld bestaan, en per 200 bestanden
// wordt een pagina opgehaald als die in beeld komt. 100.000 foto's zijn dus nooit tegelijk
// op de pagina.

(function () {
    'use strict';

    const PAGE = 200;                   // bestanden per verzoek
    const MIN_TILE = 168;               // smalste tegel; daaruit volgt het aantal kolommen
    const GAP = 12;
    const PRELOAD_ROWS = 3;
    const OTD_PER_YEAR = 60;
    const FILTER_KEYS = ['kind', 'ext', 'date_from', 'date_to', 'year', 'month', 'period', 'folder', 'folders',
        'recursive', 'camera', 'camera_contains', 'has_gps', 'min_rating', 'keurer', 'orientation', 'capture_date'];
    const LIST_KEYS = ['kind', 'ext', 'folders'];
    const DEFAULT_SORT = '-taken';
    const SORTS = [
        ['-taken', 'Nieuwste eerst'], ['taken', 'Oudste eerst'], ['name', 'Naam (A-Z)'], ['-name', 'Naam (Z-A)'],
        ['-size', 'Grootste eerst'], ['size', 'Kleinste eerst'], ['-rating', 'Hoogste waardering'],
        ['-added', 'Laatst toegevoegd'],
    ];
    const KINDS = [['photo', "Foto's", 'fa-image'], ['video', "Video's", 'fa-film'], ['audio', 'Audio', 'fa-music']];
    const MONTHS = ['januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september',
        'oktober', 'november', 'december'];
    const MONTHS_SHORT = ['jan', 'feb', 'mrt', 'apr', 'mei', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'];
    const PERIODS = { this_year: 'Dit jaar', last_year: 'Vorig jaar', this_month: 'Deze maand', last_30_days: 'Laatste 30 dagen' };
    // De Shows-score: elk hartje ('Mooi') is +50 vanaf 1000, elk duimpje omlaag -50 (shows.py).
    // Dezelfde woorden als in Shows en de viewer ('Score 1150'); [waarde, keuze, label]
    const RATINGS = [
        ['100', 'Beoordeeld in Shows', 'Beoordeeld in Shows'],
        ['1050', 'Vaker mooi dan niet (score 1050+)', 'Vaker mooi dan niet'],
        ['1150', 'Netto 3× mooi (score 1150+)', 'Netto 3× mooi'],
        ['1300', 'Netto 6× mooi (score 1300+)', 'Netto 6× mooi'],
    ];
    const CAMERA_CONTAINS = '__contains';    // de keuze 'Bevat …' in de cameralijst (geen echte camera)
    const SELECTS = {
        has_gps: [['true', 'Met locatie'], ['false', 'Zonder locatie']],
        keurer: [['keep', 'Gehouden in de Keurer'], ['undecided', 'Nog niet gekeurd']],
        orientation: [['landscape', 'Liggend'], ['portrait', 'Staand'], ['square', 'Vierkant']],
        capture_date: [['known', 'Met opnamedatum'], ['missing', 'Zonder opnamedatum']],
    };
    // Wat een waarde in het adres mag zijn; de rest wordt weggelaten (een oude of getypte bladwijzer)
    const VALID = {
        kind: (v) => KINDS.some(k => k[0] === v),
        ext: (v) => /^\.?[a-z0-9]{1,10}$/i.test(v),
        date_from: (v) => /^\d{4}-\d{2}-\d{2}$/.test(v) && !isNaN(Date.parse(v)),
        date_to: (v) => /^\d{4}-\d{2}-\d{2}$/.test(v) && !isNaN(Date.parse(v)),
        year: (v) => /^\d{4}$/.test(v),
        month: (v) => /^\d{1,2}$/.test(v) && Number(v) >= 1 && Number(v) <= 12,
        period: (v) => v in PERIODS,
        min_rating: (v) => /^-?\d{1,10}$/.test(v),
        has_gps: (v) => v === 'true' || v === 'false',
        recursive: (v) => v === 'true' || v === 'false',
        keurer: (v) => SELECTS.keurer.some(([x]) => x === v),
        orientation: (v) => SELECTS.orientation.some(([x]) => x === v),
        capture_date: (v) => SELECTS.capture_date.some(([x]) => x === v),
    };
    const ICON_LABELS = {
        'fa-images': 'Foto\'s', 'fa-star': 'Ster', 'fa-video': 'Video', 'fa-calendar-xmark': 'Geen datum',
        'fa-location-dot': 'Locatie', 'fa-calendar-day': 'Kalender', 'fa-mobile-screen': 'Telefoon',
        'fa-heart': 'Hart', 'fa-camera': 'Camera', 'fa-mountain-sun': 'Bergen', 'fa-umbrella-beach': 'Strand',
        'fa-users': 'Mensen', 'fa-paw': 'Dieren', 'fa-plane': 'Reizen', 'fa-music': 'Muziek', 'fa-folder': 'Map',
    };

    const state = {
        inited: false,
        mode: 'browse',             // 'browse' | 'otd'
        albumId: null,              // het slimme album dat open is (null: alles)
        spec: {},                   // het filter, in de vorm van de server
        sort: DEFAULT_SORT,
        albums: [],
        allCount: null,
        icons: [],
        showHidden: false,
        facets: null,
        moreOpen: false,
        // raster
        seq: 0,
        total: 0,
        version: null,
        ready: true,
        pages: new Map(),           // paginanummer -> [bestanden] of een Promise
        cols: 4,
        tile: MIN_TILE,
        rowH: MIN_TILE + GAP,
        rendered: new Map(),        // rij -> element
        focusIndex: 0,
        // op deze dag
        otdMonth: 0,
        otdDay: 0,
        otdItems: [],
        otdSeq: 0,
    };

    const $ = (id) => document.getElementById(id);

    // ── Filter <-> adres ──────────────────────────────────

    function specToParams(spec) {
        const params = new URLSearchParams();
        FILTER_KEYS.forEach(key => {
            const value = spec[key];
            if (value === undefined || value === null || value === '') return;
            if (Array.isArray(value)) value.forEach(v => params.append(key, v));
            else params.append(key, String(value));
        });
        return params;
    }

    // Het filter uit een adres. Waarden die niet kunnen (year=abc) vallen weg, zodat een oude of
    // verkeerd getypte bladwijzer toch iets laat zien; state.dropped telt ze voor een melding.
    function paramsToSpec(params) {
        const spec = {};
        state.dropped = 0;
        for (const [key, value] of params) {
            if (!FILTER_KEYS.includes(key)) continue;
            const valid = VALID[key] || ((v) => typeof v === 'string' && v.trim() !== '' && v.length <= 200);
            if (!valid(value)) { state.dropped++; continue; }
            if (LIST_KEYS.includes(key)) (spec[key] = spec[key] || []).push(value);
            else if (['year', 'month', 'min_rating'].includes(key)) spec[key] = Number(value);
            else if (['has_gps', 'recursive'].includes(key)) spec[key] = value === 'true';
            else spec[key] = value;
        }
        return spec;
    }

    // Twee filters gelijk? (de server geeft lijsten gesorteerd en zonder lege waarden terug)
    function sameSpec(a, b) {
        const norm = (s) => JSON.stringify(FILTER_KEYS.filter(k => s[k] !== undefined && s[k] !== null && s[k] !== '')
            .map(k => [k, Array.isArray(s[k]) ? [...s[k]].sort() : s[k]]));
        return norm(a || {}) === norm(b || {});
    }

    function currentAlbum() {
        return state.albums.find(a => a.id === state.albumId) || null;
    }

    function albumChanged() {
        const album = currentAlbum();
        return !!album && (!sameSpec(album.filters, state.spec) || album.sort !== state.sort);
    }

    function hashFor() {
        if (state.mode === 'otd') {
            const now = new Date();
            const today = state.otdMonth === now.getMonth() + 1 && state.otdDay === now.getDate();
            return '#albums/op-deze-dag' + (today ? '' : `/${pad2(state.otdMonth)}-${pad2(state.otdDay)}`);
        }
        let hash = '#albums';
        let params;
        const album = currentAlbum();
        if (album) {
            hash += `/album/${album.id}`;
            params = albumChanged() ? specToParams(state.spec) : new URLSearchParams();
            if (albumChanged() && state.sort !== album.sort) params.append('sort', state.sort);
        } else {
            params = specToParams(state.spec);
            if (state.sort !== DEFAULT_SORT) params.append('sort', state.sort);
        }
        const query = params.toString();
        return query ? `${hash}?${query}` : hash;
    }

    function writeHash() {
        const hash = hashFor();
        if (location.hash !== hash) history.replaceState(history.state, '', location.pathname + location.search + hash);
    }

    // Het adres lezen: true als het over Albums ging
    function readHash() {
        const m = /^#albums(?:\/album\/(\d+)|\/op-deze-dag(?:\/(\d{2})-(\d{2}))?)?(?:\?(.*))?$/.exec(location.hash);
        if (!m) return false;
        const params = new URLSearchParams(m[4] || '');
        if (location.hash.startsWith('#albums/op-deze-dag')) {
            state.mode = 'otd';
            const now = new Date();
            state.otdMonth = m[2] ? Number(m[2]) : now.getMonth() + 1;
            state.otdDay = m[3] ? Number(m[3]) : now.getDate();
            if (!validDay(state.otdMonth, state.otdDay)) {
                state.otdMonth = now.getMonth() + 1;
                state.otdDay = now.getDate();
            }
            return true;
        }
        state.mode = 'browse';
        state.customRange = false;
        state.albumId = m[1] ? Number(m[1]) : null;
        // Een bladwijzer naar een album dat er niet meer is: alles, met een melding (zodra de albums er zijn)
        state.missingAlbum = state.albumId !== null && state.albumsLoaded && !currentAlbum() ? state.albumId : null;
        if (state.missingAlbum !== null) state.albumId = null;
        const album = currentAlbum();
        const edited = [...params.keys()].some(k => FILTER_KEYS.includes(k) || k === 'sort');
        if (album && !edited) {
            state.spec = JSON.parse(JSON.stringify(album.filters || {}));
            state.sort = album.sort || DEFAULT_SORT;
        } else {
            state.spec = paramsToSpec(params);
            state.sort = SORTS.some(([v]) => v === params.get('sort')) ? params.get('sort')
                : (album ? album.sort : DEFAULT_SORT);
        }
        return true;
    }

    window.addEventListener('hashchange', () => {
        if (!location.hash.startsWith('#albums')) return;
        // Terug of Vooruit: app.js (popstate) heeft deze stap al getoond
        if (typeof navIsRestoring === 'function' && navIsRestoring()) return;
        if (currentMainView() !== 'albums') {
            switchMainView('albums');
            return;
        }
        if (readHash()) showMode();
    });

    // Albums verlaten: het adres weer leeg (het hoort niet bij de andere schermen)
    window.albumsLeave = function () {
        if (location.hash.startsWith('#albums')) history.replaceState(history.state, '', location.pathname + location.search);
    };

    // Terug of Vooruit naar een andere stap binnen Albums (een ander album, Op deze dag)
    window.albumsRestore = function () {
        if (readHash()) showMode();
    };

    // Naar een ander album of Op deze dag is een eigen stap in de geschiedenis
    function pushHash() {
        const hash = hashFor();
        if (location.hash === hash && history.state && history.state.view === 'albums') return;
        const state = { view: 'albums' };
        const url = location.pathname + location.search + hash;
        if (history.state && history.state.view) history.pushState(state, '', url);
        else history.replaceState(state, '', url);
    }

    // ── Openen ───────────────────────────────────────────

    window.albumsInit = async function () {
        const firstTime = !state.inited;
        if (firstTime) {
            state.inited = true;
            const scroll = $('albums-scroll');
            scroll.addEventListener('scroll', onScroll, { passive: true });
            scroll.addEventListener('keydown', onGridKeydown);
            scroll.addEventListener('focusin', onGridFocus);
            $('albums-otd-groups').addEventListener('keydown', onOtdKeydown);
            window.addEventListener('resize', debounce(() => {
                if (currentMainView() === 'albums' && state.mode === 'browse' && state.total) layout(true);
            }, 150));
            buildStaticControls();
            // De keuzelijsten (Altijd, Elke maand, ...) staan er meteen, ook terwijl de rest nog laadt
            renderFilters();
        }
        // De eerste keer: plaatshouders in zijbalk en raster tot de albums er zijn (geen valse 'Nog geen albums')
        if (!state.albumsLoaded && state.mode === 'browse') {
            $('albums-state').replaceChildren();
            $('albums-loading').hidden = false;
        }
        renderSidebar();
        await loadAlbums();
        // Zonder adres (terug via het menu): verder waar je was, op dezelfde plek
        const fromHash = readHash();
        showMode(!firstTime && !fromHash);
        loadFacets();
    };

    function showMode(keepScroll = false) {
        const otd = state.mode === 'otd';
        $('albums-browse').hidden = otd;
        $('albums-otd').hidden = !otd;
        if (otd) state.newHint = false;
        renderSidebar();
        if (otd) loadOnThisDay();
        else {
            renderHeader();
            renderFilters();
            reload(keepScroll);
        }
        writeHash();
        hashNotices();
    }

    // Wat er in een bladwijzer niet meer klopte, één keer zeggen (het adres is dan al hersteld)
    function hashNotices() {
        if (state.missingAlbum !== null && state.missingAlbum !== undefined) {
            showToast('Album niet gevonden', 'Dit album bestaat niet meer. Je ziet nu alles in het archief.', 'warning');
            state.missingAlbum = null;
        }
        if (state.dropped) {
            showToast('Adres aangepast', `${state.dropped === 1 ? 'Eén filter' : `${state.dropped} filters`} uit het adres kon${state.dropped === 1 ? '' : 'den'} niet gebruikt worden en ${state.dropped === 1 ? 'is' : 'zijn'} weggelaten.`, 'warning');
            state.dropped = 0;
        }
    }

    // Vanuit Home ('Alles bekijken' bij Foto op deze dag) of het menu
    window.albumsOpenOnThisDay = function (month, day) {
        const now = new Date();
        state.mode = 'otd';
        state.otdMonth = month || now.getMonth() + 1;
        state.otdDay = day || now.getDate();
        pushHash();
        if (currentMainView() !== 'albums') switchMainView('albums');
        else showMode();
    };

    window.albumsShow = function (albumId, spec, sort) {
        state.mode = 'browse';
        state.customRange = false;
        state.newHint = false;
        state.albumId = albumId ?? null;
        const album = currentAlbum();
        state.spec = spec ? { ...spec } : album ? JSON.parse(JSON.stringify(album.filters || {})) : {};
        state.sort = sort || (album ? album.sort : DEFAULT_SORT);
        pushHash();
        if (currentMainView() !== 'albums') switchMainView('albums');
        else showMode();
    };

    // Nieuw slim album: vanaf alles, met een uitleg onder de filters (geen melding over de knoppen
    // heen); Bewaar als album maakt het. Staan er al filters (en geen album), dan blijven die staan.
    window.albumsNew = function () {
        const keep = state.mode === 'browse' && state.albumId === null;
        if (!keep) albumsShow(null, {}, DEFAULT_SORT);
        state.newHint = true;
        renderNewHint();
        setTimeout(() => {
            const first = document.querySelector('#albums-kinds .pill');
            if (first) first.focus();
        }, keep ? 0 : 100);
    };

    function renderNewHint() {
        const hint = $('albums-new-hint');
        if (!hint) return;
        const show = !!state.newHint && state.mode === 'browse' && state.albumId === null;
        hint.hidden = !show;
        if (!show) return;
        hint.replaceChildren(
            createEl('i', { class: 'fas fa-wand-magic-sparkles', 'aria-hidden': 'true' }),
            createEl('span', { class: 'albums-new-hint-text', text: 'Nieuw slim album: kies hierboven de filters en klik dan rechtsboven op Bewaar als album.' }),
            createEl('button', {
                type: 'button', class: 'btn btn-ghost btn-icon btn-sm', 'aria-label': 'Uitleg sluiten', title: 'Sluiten',
                onClick: () => { state.newHint = false; renderNewHint(); $('albums-title').focus(); },
            }, createEl('i', { class: 'fas fa-xmark', 'aria-hidden': 'true' })));
    }

    // ── Zijbalk ──────────────────────────────────────────

    async function loadAlbums() {
        try {
            const data = await fetchJSON(`${API_BASE}/api/library/albums`);
            state.albums = data.albums || [];
            state.allCount = data.all;
            state.icons = data.icons || [];
            state.albumsError = null;
            state.albumsLoaded = true;
        } catch (e) {
            state.albumsError = e;
        }
        renderSidebar();
    }

    function countText(n) {
        return typeof n === 'number' ? formatNumberNl(n) : '';
    }

    function sideItem({ icon, label, count, active, onClick, hidden, title }) {
        return createEl('button', {
            type: 'button',
            class: 'albums-side-item' + (active ? ' active' : '') + (hidden ? ' is-hidden' : ''),
            'aria-current': active ? 'page' : null,
            title: title || null,
            onClick,
        },
            createEl('i', { class: `fas ${icon} albums-side-icon`, 'aria-hidden': 'true' }),
            createEl('span', { class: 'albums-side-name', text: label }),
            hidden ? createEl('i', { class: 'fas fa-eye-slash albums-side-flag', 'aria-hidden': 'true' }) : null,
            createEl('span', { class: 'albums-side-count tabular', text: countText(count) }));
    }

    // De zijbalk wordt opnieuw getekend: wie er met het toetsenbord was, blijft op het gekozen item
    function renderSidebar() {
        const library = $('albums-side-library');
        const list = $('albums-side-albums');
        if (!library || !list) return;
        const active = document.activeElement;
        const hadFocus = !!active && !!active.closest && !!active.closest('.albums-sidebar');
        const focusLink = hadFocus && active.classList.contains('albums-side-link') ? active.dataset.link : null;
        drawSidebar(library, list);
        if (!hadFocus || document.activeElement.closest('.albums-sidebar')) return;
        const target = (focusLink && document.querySelector(`.albums-sidebar [data-link="${focusLink}"]`))
            || document.querySelector('.albums-sidebar .albums-side-item.active')
            || document.querySelector('.albums-sidebar .albums-side-item');
        if (target) target.focus();
    }

    // Regels in de vorm van een album (icoon, naam, aantal) zolang de lijst laadt
    function sideSkeleton() {
        return createEl('div', { class: 'albums-side-skeleton', role: 'status', 'aria-label': 'Albums laden…' },
            ...[72, 56, 64, 48, 60].map(width => createEl('div', { class: 'albums-side-skeleton-row', 'aria-hidden': 'true' },
                createEl('span', { class: 'skeleton albums-side-skeleton-icon' }),
                createEl('span', { class: 'skeleton skeleton-line', style: `width: ${width}%` }))));
    }

    function drawSidebar(library, list) {
        const browsing = state.mode === 'browse';
        library.replaceChildren(
            sideItem({
                icon: 'fa-photo-film', label: 'Alles', count: state.allCount,
                active: browsing && state.albumId === null,
                onClick: () => albumsShow(null, {}, DEFAULT_SORT),
                title: 'Alle foto\'s, video\'s en audio in het archief',
            }),
            sideItem({
                icon: 'fa-calendar-day', label: 'Op deze dag', active: !browsing,
                onClick: () => albumsOpenOnThisDay(),
                title: 'Foto\'s en video\'s van vandaag in eerdere jaren',
            }));
        if (!state.albumsLoaded && !state.albumsError) {
            list.replaceChildren(sideSkeleton());
            return;
        }
        if (state.albumsError) {
            const gone = state.albumsError.code === 'vault_unavailable';
            list.replaceChildren(createEl('p', { class: 'albums-side-note', text: gone ? 'Archief niet bereikbaar.' : 'Albums laden mislukt.' }),
                createEl('button', { type: 'button', class: 'link-button albums-side-link', text: 'Opnieuw proberen', onClick: loadAlbums }));
            return;
        }
        const shown = state.albums.filter(a => state.showHidden || !a.hidden);
        const hiddenCount = state.albums.filter(a => a.hidden).length;
        const items = shown.map(album => sideItem({
            icon: album.icon || 'fa-images', label: album.name, count: album.count, hidden: album.hidden,
            active: browsing && state.albumId === album.id,
            onClick: () => albumsShow(album.id),
            title: album.error ? `${album.name}: ${album.error}` : album.name,
        }));
        if (!state.albums.length) {
            items.push(createEl('p', { class: 'albums-side-note', text: 'Nog geen albums. Kies filters en bewaar ze als album.' }));
        }
        const extra = [];
        if (hiddenCount) {
            extra.push(createEl('button', {
                type: 'button', class: 'link-button albums-side-link', 'aria-expanded': String(state.showHidden),
                dataset: { link: 'hidden' },
                text: state.showHidden ? 'Verborgen albums niet tonen' : `Verborgen albums tonen (${hiddenCount})`,
                onClick: () => { state.showHidden = !state.showHidden; renderSidebar(); },
            }));
        }
        const builtins = new Set(state.albums.filter(a => a.builtin && !a.hidden).map(a => a.builtin));
        if (builtins.size < 6) {
            extra.push(createEl('button', {
                type: 'button', class: 'link-button albums-side-link', text: 'Standaardalbums terugzetten',
                dataset: { link: 'defaults' },
                onClick: restoreDefaults,
            }));
        }
        list.replaceChildren(...items, ...(extra.length ? [createEl('div', { class: 'albums-side-extra' }, ...extra)] : []));
    }

    async function restoreDefaults() {
        try {
            const data = await fetchJSON(`${API_BASE}/api/library/albums/defaults`, { method: 'POST' });
            state.albums = data.albums || [];
            state.allCount = data.all;
            renderSidebar();
            showToast('Standaardalbums terug', data.added ? `${data.added} album${data.added === 1 ? '' : 's'} weer toegevoegd.`
                : 'De standaardalbums staan weer in de lijst.', 'success');
        } catch (e) {
            toastFetchError('Terugzetten mislukt', e);
        }
    }

    // ── Kop ──────────────────────────────────────────────

    function renderHeader() {
        const album = currentAlbum();
        const icon = $('albums-title-icon');
        icon.className = `fas ${album ? album.icon || 'fa-images' : 'fa-photo-film'}`;
        $('albums-title-text').textContent = album ? album.name : 'Alles';
        const changed = albumChanged();
        $('albums-subtitle').textContent = album
            ? (changed ? 'Aangepast: bewaar de nieuwe filters of zet ze terug.'
                : 'Slim album: altijd actueel, de bestanden blijven waar ze staan.')
            : 'Alles in je archief. Kies filters en bewaar ze als slim album.';
        // Hoogstens drie knoppen. Een aangepast album: terugzetten, bewaren, of als nieuw album
        // bewaren (naam en icoon wijzigen kan daarna weer met Album bewerken)
        const actions = $('albums-header-actions');
        const focused = document.activeElement && actions.contains(document.activeElement) ? document.activeElement.id : null;
        const buttons = [];
        if (album && changed) {
            buttons.push(createEl('button', { type: 'button', class: 'btn btn-ghost', id: 'albums-revert', onClick: () => { albumsShow(album.id); $('albums-title').focus(); } },
                createEl('i', { class: 'fas fa-rotate-left', 'aria-hidden': 'true' }), 'Terugzetten'));
            buttons.push(createEl('button', {
                type: 'button', class: 'btn btn-secondary', id: 'albums-save-new', title: 'Deze filters als een nieuw album bewaren',
                onClick: () => openAlbumDialog(null),
            }, createEl('i', { class: 'fas fa-plus', 'aria-hidden': 'true' }), 'Als nieuw album'));
            buttons.push(createEl('button', { type: 'button', class: 'btn btn-primary', id: 'albums-save-changes', onClick: saveAlbumChanges },
                createEl('i', { class: 'fas fa-check', 'aria-hidden': 'true' }), 'Wijzigingen bewaren'));
        } else if (album) {
            buttons.push(createEl('button', { type: 'button', class: 'btn btn-secondary', id: 'albums-edit', onClick: () => openAlbumDialog(album) },
                createEl('i', { class: 'fas fa-pen', 'aria-hidden': 'true' }), 'Album bewerken'));
        } else {
            const any = Object.keys(state.spec).length > 0;
            buttons.push(createEl('button', {
                type: 'button', class: `btn ${any ? 'btn-primary' : 'btn-secondary'}`, id: 'albums-save-new',
                onClick: () => openAlbumDialog(null),
            }, createEl('i', { class: 'fas fa-plus', 'aria-hidden': 'true' }), 'Bewaar als album'));
        }
        // Bij een aangepast album zijn er al drie knoppen (zie boven): dan past deze niet meer naast elkaar
        if (!(album && changed)) buttons.push(createEl('button', { type: 'button', class: 'btn btn-secondary', id: 'albums-best',
            title: "De beste foto's van een periode kiezen en exporteren", onClick: () => window.DVExport && DVExport.openBest() },
            createEl('i', { class: 'fas fa-trophy', 'aria-hidden': 'true' }), 'Beste van een periode'));
        actions.replaceChildren(...buttons);
        renderNewHint();
        // Weg was de knop met de focus (bijv. na Wijzigingen bewaren): dan de titel, niet de pagina
        if (focused) ($(focused) || $('albums-title')).focus();
    }

    // ── Filterbalk ───────────────────────────────────────

    function option(value, label, selected) {
        return createEl('option', { value, selected: !!selected }, label);
    }

    function buildStaticControls() {
        const sort = $('albums-sort');
        sort.replaceChildren(...SORTS.map(([v, l]) => option(v, l)));
        sort.addEventListener('change', () => { state.sort = sort.value; changed(); });
        const kinds = $('albums-kinds');
        kinds.replaceChildren(...KINDS.map(([kind, label, icon]) => createEl('button', {
            type: 'button', class: 'pill', dataset: { kind }, 'aria-pressed': 'false',
            onClick: () => toggleKind(kind),
        }, createEl('i', { class: `fas ${icon}`, 'aria-hidden': 'true' }), label)));
        $('albums-when').addEventListener('change', onWhenChange);
        $('albums-month').addEventListener('change', (e) => setKey('month', e.target.value ? Number(e.target.value) : null));
        $('albums-from').addEventListener('change', (e) => setRangeDay('date_from', e.target.value));
        $('albums-to').addEventListener('change', (e) => setRangeDay('date_to', e.target.value));
        $('albums-folder-btn').addEventListener('click', openFolderPicker);
        $('albums-more-btn').addEventListener('click', () => {
            state.moreOpen = !state.moreOpen;
            renderFilters();
        });
        $('albums-camera').addEventListener('change', (e) => setCamera(e.target.value));
        $('albums-ext').addEventListener('change', (e) => setKey('ext', e.target.value ? [e.target.value] : null));
        $('albums-rating').addEventListener('change', (e) => setKey('min_rating', e.target.value ? Number(e.target.value) : null));
        Object.keys(SELECTS).forEach(key => {
            const el = $(`albums-${key.replace('_', '-')}`);
            el.replaceChildren(option('', { has_gps: 'Locatie', keurer: 'Keurer', orientation: 'Oriëntatie', capture_date: 'Opnamedatum' }[key]),
                ...SELECTS[key].map(([v, l]) => option(v, l)));
            el.addEventListener('change', () => setKey(key, el.value === '' ? null
                : key === 'has_gps' ? el.value === 'true' : el.value));
        });
        $('albums-clear').addEventListener('click', () => {
            state.spec = {};
            state.customRange = false;
            changed();
            $('albums-title').focus();
        });
        $('albums-loading').replaceChildren(skeletonCards(12, 'albums-skeleton'));
        registerOverlayCloser('modal-album', albumsCloseDialog);
        registerOverlayCloser('modal-albums-folder', albumsCloseFolderPicker);
    }

    function toggleKind(kind) {
        const kinds = new Set(state.spec.kind || []);
        if (kinds.has(kind)) kinds.delete(kind); else kinds.add(kind);
        setKey('kind', kinds.size && kinds.size < KINDS.length ? KINDS.map(k => k[0]).filter(k => kinds.has(k)) : null);
    }

    function setKey(key, value) {
        const spec = { ...state.spec };
        if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) delete spec[key];
        else spec[key] = value;
        if (key === 'folder') {
            delete spec.folders;        // één map of de mappen van 'op deze dag', nooit allebei
            if (!spec.folder) delete spec.recursive;
        }
        state.spec = spec;
        changed();
    }

    function setCamera(value) {
        if (value === CAMERA_CONTAINS) return;     // dat is het filter dat er al staat
        const spec = { ...state.spec };
        delete spec.camera;
        delete spec.camera_contains;
        if (value) spec.camera = value;
        state.spec = spec;
        changed();
    }

    // Van… tot…: een einddatum voor de begindatum kan niet; dat zegt de regel eronder meteen,
    // in plaats van dat het hele raster een foutmelding wordt
    function setRangeDay(key, value) {
        const from = key === 'date_from' ? value : state.spec.date_from;
        const to = key === 'date_to' ? value : state.spec.date_to;
        const error = $('albums-range-error');
        if (from && to && from > to) {
            error.textContent = key === 'date_from' ? 'De begindatum ligt na de einddatum.' : 'De einddatum ligt voor de begindatum.';
            error.hidden = false;
            $(key === 'date_from' ? 'albums-from' : 'albums-to').setAttribute('aria-invalid', 'true');
            return;
        }
        error.hidden = true;
        error.textContent = '';
        $('albums-from').removeAttribute('aria-invalid');
        $('albums-to').removeAttribute('aria-invalid');
        setKey(key, value || null);
    }

    function onWhenChange(e) {
        const value = e.target.value;
        const spec = { ...state.spec };
        ['period', 'year', 'date_from', 'date_to'].forEach(k => delete spec[k]);
        if (value.startsWith('y:')) spec.year = Number(value.slice(2));
        else if (PERIODS[value]) spec.period = value;
        state.spec = spec;
        state.customRange = value === 'range';
        changed();
        if (value === 'range') setTimeout(() => $('albums-from').focus(), 0);
    }

    function changed() {
        renderHeader();
        renderFilters();
        renderSidebar();
        writeHash();
        reload();
    }

    function whenValue(spec) {
        if (spec.date_from || spec.date_to) return 'range';
        if (spec.period) return spec.period;
        if (spec.year) return `y:${spec.year}`;
        return state.customRange ? 'range' : '';
    }

    // De aantallen in de keuzelijsten tellen het hele archief. Staat er nog een ander filter aan,
    // dan kloppen ze niet meer voor deze selectie: dan alleen de namen, geen (misleidend) getal
    const FACET_KEYS = {
        when: ['year', 'month', 'period', 'date_from', 'date_to'],
        camera: ['camera', 'camera_contains'],
        ext: ['ext'],
    };
    function facetCount(facet, n) {
        const own = FACET_KEYS[facet];
        const other = Object.keys(state.spec).some(k => !own.includes(k) && state.spec[k] !== undefined
            && state.spec[k] !== null && !(Array.isArray(state.spec[k]) && !state.spec[k].length));
        return other ? '' : ` (${formatNumberNl(n)})`;
    }

    function renderFilters() {
        const spec = state.spec;
        document.querySelectorAll('#albums-kinds .pill').forEach(pill => {
            pill.setAttribute('aria-pressed', String((spec.kind || []).includes(pill.dataset.kind)));
        });
        const years = (state.facets && state.facets.years) || [];
        const when = $('albums-when');
        const yearOptions = years.map(([y, n]) => option(`y:${y}`, `${y}${facetCount('when', n)}`));
        if (spec.year && !years.some(([y]) => y === spec.year)) yearOptions.unshift(option(`y:${spec.year}`, String(spec.year)));
        when.replaceChildren(option('', 'Altijd'),
            ...Object.entries(PERIODS).map(([v, l]) => option(v, l)),
            yearOptions.length ? createEl('optgroup', { label: 'Jaar' }, ...yearOptions) : null,
            option('range', 'Van… tot…'));
        when.value = whenValue(spec);
        const month = $('albums-month');
        month.replaceChildren(option('', spec.year ? 'Hele jaar' : 'Elke maand'), ...MONTHS.map((m, i) => option(String(i + 1), m)));
        month.value = spec.month ? String(spec.month) : '';
        const range = when.value === 'range';
        $('albums-range').hidden = !range;
        $('albums-from').value = spec.date_from || '';
        $('albums-to').value = spec.date_to || '';
        // De ene datum begrenst de andere in de datumkiezer
        $('albums-from').max = spec.date_to || '';
        $('albums-to').min = spec.date_from || '';
        if (!range) $('albums-range-error').hidden = true;
        const folderBtn = $('albums-folder-btn');
        const folderLabel = $('albums-folder-label');
        const folders = spec.folders || [];
        folderLabel.textContent = spec.folder ? displayPath(folderAbs(spec.folder), 28)
            : folders.length === 1 ? displayPath(folderAbs(folders[0]), 28)
                : folders.length ? `${folders.length} mappen` : 'Hele archief';
        folderBtn.title = spec.folder ? `Map: ${folderAbs(spec.folder)}`
            : folders.length ? `Mappen: ${folders.map(folderAbs).join(', ')}` : 'Een map kiezen';
        folderBtn.classList.toggle('active', !!spec.folder || folders.length > 0);
        $('albums-sort').value = state.sort;

        // Meer filters
        const cameras = (state.facets && state.facets.cameras) || [];
        const camera = $('albums-camera');
        const camOptions = cameras.map(c => option(c.value, `${c.label}${facetCount('camera', c.count)}`));
        if (spec.camera && !cameras.some(c => c.value === spec.camera)) camOptions.unshift(option(spec.camera, spec.camera));
        if (spec.camera_contains) camOptions.unshift(option(CAMERA_CONTAINS, `Bevat '${spec.camera_contains}'`));
        camera.replaceChildren(option('', 'Camera'), ...camOptions);
        camera.value = spec.camera || (spec.camera_contains ? CAMERA_CONTAINS : '');
        const exts = (state.facets && state.facets.exts) || [];
        const ext = $('albums-ext');
        const extOptions = exts.map(x => option(x.ext, `${x.ext}${facetCount('ext', x.count)}`));
        (spec.ext || []).forEach(x => { if (!exts.some(e => e.ext === x)) extOptions.unshift(option(x, x)); });
        ext.replaceChildren(option('', 'Bestandstype'), ...extOptions);
        ext.value = spec.ext && spec.ext.length === 1 ? spec.ext[0] : '';
        const rating = $('albums-rating');
        rating.replaceChildren(option('', 'Shows-score'), ...RATINGS.map(([v, l]) => option(v, l)));
        if (spec.min_rating && !RATINGS.some(([v]) => Number(v) === spec.min_rating)) {
            rating.appendChild(option(String(spec.min_rating), `Score ${spec.min_rating} of hoger`));
        }
        rating.value = spec.min_rating ? String(spec.min_rating) : '';
        Object.keys(SELECTS).forEach(key => {
            $(`albums-${key.replace('_', '-')}`).value = spec[key] === undefined ? '' : String(spec[key]);
        });
        const advanced = ['camera', 'camera_contains', 'ext', 'min_rating', 'has_gps', 'keurer', 'orientation', 'capture_date']
            .filter(k => spec[k] !== undefined).length;
        const moreBtn = $('albums-more-btn');
        moreBtn.setAttribute('aria-expanded', String(state.moreOpen));
        moreBtn.classList.toggle('active', advanced > 0);
        $('albums-more-count').textContent = advanced ? String(advanced) : '';
        $('albums-more-count').hidden = !advanced;
        $('albums-more').hidden = !state.moreOpen;
        renderChips();
    }

    // De gekozen filters als labels met een kruisje: ook die van een album (dat wordt dan 'aangepast')
    function chipLabels(spec) {
        const out = [];
        const fmt = (d) => formatDateNl(d);
        if (spec.kind) out.push(['kind', spec.kind.map(k => (KINDS.find(x => x[0] === k) || [k, k])[1]).join(', ')]);
        if (spec.period) out.push(['period', PERIODS[spec.period] || spec.period]);
        if (spec.year && spec.month) out.push(['year', `${MONTHS[spec.month - 1]} ${spec.year}`]);
        else if (spec.year) out.push(['year', String(spec.year)]);
        if (spec.month && !spec.year) out.push(['month', `Elke ${MONTHS[spec.month - 1]}`]);
        if (spec.date_from && spec.date_to) out.push(['date_from', spec.date_from === spec.date_to ? fmt(spec.date_from) : `${fmt(spec.date_from)} – ${fmt(spec.date_to)}`]);
        else if (spec.date_from) out.push(['date_from', `Vanaf ${fmt(spec.date_from)}`]);
        else if (spec.date_to) out.push(['date_to', `Tot en met ${fmt(spec.date_to)}`]);
        const flat = spec.recursive === false ? ' (zonder submappen)' : '';
        if (spec.folder) out.push(['folder', displayPath(folderAbs(spec.folder), 36) + flat]);
        if (spec.folders && spec.folders.length === 1) out.push(['folders', displayPath(folderAbs(spec.folders[0]), 36) + flat]);
        else if (spec.folders) out.push(['folders', `${spec.folders.length} mappen${flat}`]);
        if (spec.camera) out.push(['camera', spec.camera]);
        if (spec.camera_contains) out.push(['camera_contains', `Camera met '${spec.camera_contains}'`]);
        if (spec.ext) out.push(['ext', spec.ext.join(', ')]);
        if (spec.min_rating) out.push(['min_rating', (RATINGS.find(([v]) => Number(v) === spec.min_rating) || [0, 0, `Score ${spec.min_rating}+`])[2]]);
        Object.keys(SELECTS).forEach(key => {
            if (spec[key] === undefined) return;
            const found = SELECTS[key].find(([v]) => v === String(spec[key]));
            out.push([key, found ? found[1] : String(spec[key])]);
        });
        return out;
    }

    function renderChips() {
        const chips = $('albums-chips');
        const labels = chipLabels(state.spec);
        chips.replaceChildren(...labels.map(([key, label], at) => createEl('span', { class: 'chip albums-chip' },
            createEl('span', { class: 'albums-chip-text', text: label }),
            createEl('button', {
                type: 'button', class: 'chip-remove', 'aria-label': `Filter ${label} weghalen`, title: 'Weghalen',
                onClick: () => {
                    const spec = { ...state.spec };
                    delete spec[key];
                    if (key === 'year') delete spec.month;
                    if (key === 'date_from' || key === 'date_to') { delete spec.date_from; delete spec.date_to; state.customRange = false; }
                    if (key === 'folder' || key === 'folders') delete spec.recursive;
                    state.spec = spec;
                    changed();
                    // Verder bij het volgende label (of het vorige), anders bij het aantal
                    const left = chips.querySelectorAll('.chip-remove');
                    const next = left[Math.min(at, left.length - 1)];
                    if (next) next.focus();
                    else $('albums-title').focus();
                },
            }, createEl('i', { class: 'fas fa-xmark', 'aria-hidden': 'true' })))));
        $('albums-clear').hidden = labels.length === 0;
    }

    function renderCount() {
        const el = $('albums-count');
        if (state.total === null) { el.replaceChildren(); return; }
        el.replaceChildren(createEl('span', { class: 'albums-count-number tabular', text: formatNumberNl(state.total) }),
            ` ${state.total === 1 ? 'bestand' : 'bestanden'}`);
        updateSelectAll();
    }

    async function loadFacets() {
        try {
            state.facets = await fetchJSON(`${API_BASE}/api/library/facets`);
            if (state.mode === 'browse') renderFilters();
        } catch (_) { /* de keuzelijsten blijven dan zonder jaren en camera's */ }
    }

    // ── Map kiezen ───────────────────────────────────────

    function vaultRoot() {
        const first = (typeof folderTree !== 'undefined' && folderTree[0]) ? folderTree[0].path : '';
        return first ? first.slice(0, first.lastIndexOf('/')) : '';
    }

    function folderAbs(rel) {
        if (!rel || rel.startsWith('/')) return rel;
        const root = vaultRoot();
        return root ? `${root}/${rel}` : rel;
    }

    function folderRel(abs) {
        const root = vaultRoot();
        return root && abs.startsWith(root + '/') ? abs.slice(root.length + 1) : abs;
    }

    let pickedFolder = null;

    async function openFolderPicker() {
        const modal = $('modal-albums-folder');
        modal.classList.add('active');
        pickedFolder = null;
        $('btn-albums-folder-confirm').disabled = true;
        $('albums-folder-sub').checked = state.spec.recursive !== false;
        const tree = $('albums-folder-tree');
        tree.replaceChildren(skeletonRows(4));
        let folders = (typeof folderTree !== 'undefined' && folderTree.length) ? folderTree : null;
        if (!folders) {
            try { folders = (await fetchJSON(`${API_BASE}/api/folders`)).folders || []; } catch (_) { folders = []; }
        }
        tree.replaceChildren();
        if (!folders.length) {
            tree.appendChild(createEl('p', { class: 'picker-message is-error', text: 'Mappen konden niet geladen worden.' }));
            return;
        }
        renderFolderTree(tree, folders, {
            selectedPath: state.spec.folder ? folderAbs(state.spec.folder) : null,
            onSelect: (path) => {
                pickedFolder = path;
                $('btn-albums-folder-confirm').disabled = false;
            },
        });
        window._albumsFolderNav = activateFolderTreeModal({
            modalId: 'modal-albums-folder',
            containerId: 'albums-folder-tree',
            onSelect: (item) => { if (item.dataset.path) item.click(); },
            onHighlight: (item) => { if (item.dataset.path) { pickedFolder = item.dataset.path; $('btn-albums-folder-confirm').disabled = false; } },
            onConfirm: () => { if (pickedFolder) albumsConfirmFolder(); },
            onClose: albumsCloseFolderPicker,
        });
    }

    window.albumsCloseFolderPicker = function () {
        $('modal-albums-folder').classList.remove('active');
        if (window._albumsFolderNav) window._albumsFolderNav.nav.resetIndex();
        pickedFolder = null;
    };

    window.albumsConfirmFolder = function () {
        if (!pickedFolder) return;
        const spec = { ...state.spec, folder: folderRel(pickedFolder) };
        delete spec.folders;
        if ($('albums-folder-sub').checked) delete spec.recursive; else spec.recursive = false;
        albumsCloseFolderPicker();
        state.spec = spec;
        changed();
    };

    window.albumsWholeArchive = function () {
        albumsCloseFolderPicker();
        setKey('folder', null);
    };

    // ── Raster: laden ────────────────────────────────────

    function apiParams(offset, limit) {
        const params = specToParams(state.spec);
        params.set('sort', state.sort);
        params.set('offset', String(offset));
        params.set('limit', String(limit));
        return params;
    }

    function reload(keepScroll) {
        state.seq++;
        state.pages = new Map();
        state.version = null;
        state.total = null;
        clearRows();
        const scroll = $('albums-scroll');
        if (!keepScroll) {
            scroll.scrollTop = 0;
            state.focusIndex = 0;
        }
        $('albums-state').replaceChildren();
        $('albums-content').style.height = '0px';
        $('albums-loading').hidden = false;
        renderCount();
        loadPage(0).then(() => {
            $('albums-loading').hidden = true;
        });
    }

    // Een pagina ophalen (één keer, ook als meerdere rijen erom vragen). Geeft de bestanden.
    function loadPage(page) {
        const known = state.pages.get(page);
        if (known) return Array.isArray(known) ? Promise.resolve(known) : known;
        const seq = state.seq;
        const promise = (async () => {
            try {
                const data = await fetchJSON(`${API_BASE}/api/library/browse?${apiParams(page * PAGE, PAGE)}`);
                if (seq !== state.seq) return null;
                if (state.version !== null && data.version !== state.version) {
                    // Het archief veranderde terwijl je bladerde: opnieuw, op dezelfde plek
                    reload(true);
                    return null;
                }
                const first = state.version === null;
                state.version = data.version;
                state.ready = data.ready;
                if (first) {
                    state.total = data.total;
                    renderCount();
                    if (!sameSpec(data.filters, state.spec)) {
                        state.spec = data.filters;
                        renderFilters();
                        writeHash();
                    }
                }
                state.pages.set(page, data.items);
                if (first) {
                    layout(false);
                    showEmptyOrNotReady();
                } else {
                    refreshRows(page);
                }
                return data.items;
            } catch (e) {
                if (seq !== state.seq) return null;
                state.pages.delete(page);
                if (page === 0) {
                    $('albums-loading').hidden = true;
                    $('albums-state').replaceChildren(badFilter(e) ? badFilterState(e)
                        : errorState(failTitle(e, 'Bibliotheek laden mislukt'), e, () => reload()));
                } else {
                    toastFetchError('Laden mislukt', e);
                }
                return null;
            }
        })();
        state.pages.set(page, promise);
        return promise;
    }

    // De server weigerde het filter zelf (400, of 403 voor een map buiten het archief): opnieuw
    // proberen helpt dan niet, de filters wissen of het album terugzetten wel
    function badFilter(e) {
        return !!e && (e.status === 400 || e.status === 403 || e.status === 422);
    }

    function badFilterState(e) {
        const album = currentAlbum();
        const back = album && albumChanged();
        return emptyState({
            icon: 'fa-filter-circle-xmark', title: 'Filter klopt niet',
            text: (e && e.message) || 'Dit filter kan niet gebruikt worden.',
            action: back ? { label: 'Album terugzetten', onClick: () => albumsShow(album.id) }
                : { label: 'Filters wissen', primary: true, onClick: () => { state.spec = {}; state.customRange = false; changed(); $('albums-title').focus(); } },
        });
    }

    // Wat er (behalve soort en sortering) nog meer filtert
    function narrowingKeys(spec) {
        return Object.keys(spec).filter(k => k !== 'kind' && k !== 'recursive');
    }

    function kindWords(spec) {
        const kinds = spec.kind || [];
        if (kinds.length === 1) return { photo: 'foto\'s', video: 'video\'s', audio: 'audiobestanden' }[kinds[0]] || 'bestanden';
        if (kinds.length === 2 && !kinds.includes('audio')) return 'foto\'s of video\'s';
        return 'bestanden';
    }

    function showEmptyOrNotReady() {
        const box = $('albums-state');
        if (state.total > 0) {
            box.replaceChildren();
            return;
        }
        if (!state.ready) {
            box.replaceChildren(emptyState({
                icon: 'fa-hourglass-half', title: 'De bibliotheek wordt nog opgebouwd',
                text: 'De app leest het archief één keer helemaal in. Dit vult zich vanzelf zodra dat klaar is.',
                action: { label: 'Opnieuw kijken', onClick: () => reload() },
            }));
        } else if (currentAlbum() && !albumChanged()) {
            const album = currentAlbum();
            // Het hartjes-advies alleen als de score het enige is dat het album smaller maakt
            const onlyRating = state.spec.min_rating !== undefined && narrowingKeys(state.spec).length === 1;
            box.replaceChildren(emptyState({
                icon: album.icon || 'fa-images', title: 'Dit album is nog leeg',
                text: onlyRating ? 'Geef in een Shows-diavoorstelling een hartje aan je mooiste foto\'s: wat hoog scoort, komt hier vanzelf bij.'
                    : `Nog geen ${kindWords(state.spec)} in het archief passen bij dit album. Het vult zich vanzelf zodra dat wel zo is.`,
                action: onlyRating ? { label: 'Naar Shows', onClick: () => switchMainView('shows') }
                    : { label: 'Album bewerken', onClick: () => openAlbumDialog(album) },
            }));
        } else if (Object.keys(state.spec).length) {
            box.replaceChildren(emptyState({
                icon: 'fa-filter-circle-xmark', title: 'Niets gevonden',
                text: `Geen ${kindWords(state.spec)} die bij al deze filters passen.`,
                action: { label: 'Filters wissen', onClick: () => { state.spec = {}; state.customRange = false; changed(); $('albums-title').focus(); } },
            }));
        } else {
            box.replaceChildren(emptyState({
                icon: 'fa-images', title: 'Het archief is nog leeg',
                text: 'Zodra er foto\'s, video\'s of audio in het archief staan, zie je ze hier.',
                action: { label: 'Naar Bestanden', primary: true, onClick: () => switchMainView('viewer') },
            }));
        }
    }

    // ── Raster: tekenen ──────────────────────────────────

    function gutter() {
        return parseFloat(getComputedStyle($('albums-scroll')).getPropertyValue('--page-gutter')) || 32;
    }

    function layout(keepPosition) {
        const scroll = $('albums-scroll');
        const width = Math.min(scroll.clientWidth, parseFloat(getComputedStyle($('albums-content')).maxWidth) || Infinity)
            - 2 * gutter();
        if (width <= 0) return;
        const anchor = keepPosition && state.rowH ? Math.floor(scroll.scrollTop / state.rowH) * state.cols : null;
        state.cols = Math.max(2, Math.floor((width + GAP) / (MIN_TILE + GAP)));
        state.tile = (width - GAP * (state.cols - 1)) / state.cols;
        state.rowH = state.tile + GAP;
        const rows = Math.ceil((state.total || 0) / state.cols);
        $('albums-content').style.height = `${rows * state.rowH}px`;
        clearRows();
        if (anchor !== null) scroll.scrollTop = Math.floor(anchor / state.cols) * state.rowH;
        renderRows();
    }

    function clearRows() {
        state.rendered.forEach(el => el.remove());
        state.rendered.clear();
    }

    function visibleRows() {
        const scroll = $('albums-scroll');
        const top = scroll.scrollTop - $('albums-content').offsetTop;
        const first = Math.max(0, Math.floor(top / state.rowH) - PRELOAD_ROWS);
        const last = Math.min(Math.ceil((state.total || 0) / state.cols) - 1,
            Math.ceil((top + scroll.clientHeight) / state.rowH) + PRELOAD_ROWS);
        return [first, last];
    }

    function renderRows() {
        if (!state.total) return;
        const [first, last] = visibleRows();
        state.rendered.forEach((el, row) => {
            if (row < first || row > last) { el.remove(); state.rendered.delete(row); }
        });
        const content = $('albums-content');
        const fragment = document.createDocumentFragment();
        for (let row = first; row <= last; row++) {
            if (state.rendered.has(row)) continue;
            const el = rowNode(row);
            state.rendered.set(row, el);
            fragment.appendChild(el);
        }
        content.appendChild(fragment);
        // Roving tabindex: staat de tegel met de focus buiten beeld, dan krijgt de eerste zichtbare de tab-stop
        if (!content.querySelector('.albums-tile[tabindex="0"]')) {
            const firstTile = content.querySelector('.albums-tile');
            if (firstTile) firstTile.tabIndex = 0;
        }
        // Ontbrekende pagina's ophalen
        const pages = new Set();
        for (let i = first * state.cols; i <= Math.min(state.total - 1, (last + 1) * state.cols - 1); i++) pages.add(Math.floor(i / PAGE));
        pages.forEach(p => { if (!state.pages.has(p)) loadPage(p); });
    }

    function itemAt(index) {
        const page = state.pages.get(Math.floor(index / PAGE));
        return Array.isArray(page) ? page[index % PAGE] || null : null;
    }

    function rowNode(row) {
        const el = createEl('div', { class: 'albums-row', dataset: { row: String(row) } });
        el.style.top = `${row * state.rowH}px`;
        el.style.height = `${state.tile}px`;
        el.style.gridTemplateColumns = `repeat(${state.cols}, 1fr)`;
        el.style.gap = `${GAP}px`;
        for (let i = row * state.cols; i < Math.min(state.total, (row + 1) * state.cols); i++) {
            el.appendChild(tileNode(itemAt(i), i));
        }
        return el;
    }

    // Een pagina is binnen: zijn rijen opnieuw tekenen (de plaatshouders worden foto's)
    function refreshRows(page) {
        const from = Math.floor(page * PAGE / state.cols);
        const to = Math.floor(((page + 1) * PAGE - 1) / state.cols);
        const hadFocus = document.activeElement && document.activeElement.closest && document.activeElement.closest('#albums-content');
        for (let row = from; row <= to; row++) {
            const old = state.rendered.get(row);
            if (!old) continue;
            const el = rowNode(row);
            old.replaceWith(el);
            state.rendered.set(row, el);
        }
        if (hadFocus && !document.activeElement.closest('#albums-content')) focusTile(state.focusIndex, false);
    }

    function dateLabel(ts) {
        if (typeof ts !== 'number' || ts <= 0) return '';
        const d = new Date(ts * 1000);
        return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]} ${d.getFullYear()}`;
    }

    // Eén tegel: de foto (of een plaatshouder zolang zijn pagina nog laadt)
    function tileNode(file, index, onOpen) {
        const card = createEl('div', {
            class: 'albums-tile thumb-tile' + (file ? '' : ' is-placeholder'),
            role: 'button',
            dataset: { index: String(index) },
            tabindex: index === state.focusIndex && !onOpen ? '0' : '-1',
        });
        if (!file) {
            card.setAttribute('aria-label', 'Laden…');
            card.appendChild(createEl('img', { class: 'loading', alt: '' }));
            return card;
        }
        const img = createEl('img', { class: 'loading', alt: file.name, loading: 'lazy', decoding: 'async' });
        img.addEventListener('load', () => img.classList.remove('loading'), { once: true });
        img.addEventListener('error', () => img.classList.remove('loading'), { once: true });
        img.src = file.type === 'audio' ? audioThumbUrl(file.name) : vaultFileUrl('thumb', file.path);
        card.appendChild(img);
        if (file.type === 'video' || file.type === 'audio') {
            card.appendChild(createEl('span', { class: 'albums-tile-badge', 'aria-hidden': 'true' },
                createEl('i', { class: `fas ${file.type === 'video' ? 'fa-play' : 'fa-music'}` })));
        }
        const date = dateLabel(file.timestamp);
        card.appendChild(createEl('div', { class: 'thumb-caption albums-tile-caption' },
            createEl('span', { class: 'albums-tile-name', text: file.name }),
            date ? createEl('span', { class: 'albums-tile-date', text: date }) : null));
        const kind = file.type === 'video' ? ', video' : file.type === 'audio' ? ', audio' : '';
        card.setAttribute('aria-label', `${file.name}${date ? ', ' + date : ''}${kind}`);
        if (!onOpen) {
            if (window.DVExport && DVExport.has(file.path)) card.classList.add('selected');
            card.setAttribute('aria-pressed', card.classList.contains('selected') ? 'true' : 'false');
        }
        card.addEventListener('click', (e) => {
            if (onOpen) return onOpen(index);
            if (window.DVExport && DVExport.wantsSelect(e)) return selectTile(file, index, e.shiftKey);
            openAt(index);
        });
        return card;
    }

    // ── Selecteren (export) ──────────────────────────────

    let anchorIndex = null;
    function selectTile(file, index, range) {
        if (range && anchorIndex !== null) {
            const files = [];
            for (let i = Math.min(anchorIndex, index); i <= Math.max(anchorIndex, index); i++) {
                const f = itemAt(i);
                if (f) files.push(f);
            }
            DVExport.selectRange(files);
        } else {
            DVExport.toggle(file);
        }
        anchorIndex = index;
    }

    function paintSelection() {
        $('albums-content').querySelectorAll('.albums-tile[data-index]').forEach(tile => {
            const file = itemAt(Number(tile.dataset.index));
            if (!file) return;
            const on = DVExport.has(file.path);
            tile.classList.toggle('selected', on);
            tile.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }

    // Alles van de huidige filter, pagina voor pagina (hoogstens 20.000)
    async function allOfFilter() {
        const files = [];
        for (let offset = 0; offset < Math.min(state.total, 20000); offset += 1000) {
            const data = await fetchJSON(`${API_BASE}/api/library/browse?${apiParams(offset, 1000)}`);
            files.push(...data.items);
        }
        return files;
    }

    function updateSelectAll() {
        if (window.DVExport) DVExport.setSelectAll(state.mode === 'browse' ? allOfFilter : null, state.total);
    }
    if (window.DVExport) DVExport.onChange(paintSelection);

    let scrollTicking = false;
    function onScroll() {
        if (scrollTicking) return;
        scrollTicking = true;
        requestAnimationFrame(() => {
            scrollTicking = false;
            renderRows();
        });
    }

    // ── Raster: viewer ───────────────────────────────────

    // De geladen pagina's rond deze tegel gaan mee naar de viewer; aan de rand haalt hij er meer bij
    function openAt(index) {
        let lo = Math.floor(index / PAGE);
        let hi = lo;
        if (!Array.isArray(state.pages.get(lo))) return;
        while (lo > 0 && Array.isArray(state.pages.get(lo - 1))) lo--;
        while (Array.isArray(state.pages.get(hi + 1))) hi++;
        const items = [];
        for (let p = lo; p <= hi; p++) items.push(...state.pages.get(p));
        const seq = state.seq;
        openViewer(items, index - lo * PAGE, {
            onRemoved: () => libraryChanged(),
            more: async (step) => {
                const page = step > 0 ? hi + 1 : lo - 1;
                if (seq !== state.seq || page < 0 || page * PAGE >= state.total) return [];
                const got = await loadPage(page);
                if (!got) return [];
                if (step > 0) hi = page; else lo = page;
                return got;
            },
        });
        state.focusIndex = index;
    }

    // Een bestand ging weg (verplaatst in de viewer): het raster laadt opnieuw, op dezelfde plek
    const libraryChanged = debounce(() => {
        if (state.mode === 'browse') reload(true);
        loadAlbums();
    }, 400);

    // ── Raster: toetsenbord ──────────────────────────────

    function focusTile(index, scrollIntoView = true) {
        if (!state.total) return;
        index = Math.max(0, Math.min(state.total - 1, index));
        const scroll = $('albums-scroll');
        const row = Math.floor(index / state.cols);
        const top = row * state.rowH + $('albums-content').offsetTop;
        // De zwevende taakbalk onderaan mag de tegel niet bedekken
        const dock = parseFloat(getComputedStyle(scroll).getPropertyValue('--job-dock-space')) || 0;
        if (scrollIntoView) {
            if (top < scroll.scrollTop) scroll.scrollTop = top - GAP;
            else if (top + state.tile > scroll.scrollTop + scroll.clientHeight - dock) scroll.scrollTop = top + state.tile - scroll.clientHeight + GAP + dock;
            renderRows();
        }
        const old = $('albums-content').querySelector('.albums-tile[tabindex="0"]');
        if (old) old.tabIndex = -1;
        state.focusIndex = index;
        const tile = $('albums-content').querySelector(`.albums-tile[data-index="${index}"]`);
        if (tile) {
            tile.tabIndex = 0;
            tile.focus({ preventScroll: true });
        }
    }

    function onGridFocus(e) {
        const tile = e.target.closest && e.target.closest('.albums-tile');
        if (!tile) return;
        const old = $('albums-content').querySelector('.albums-tile[tabindex="0"]');
        if (old && old !== tile) old.tabIndex = -1;
        tile.tabIndex = 0;
        state.focusIndex = Number(tile.dataset.index);
    }

    function onGridKeydown(e) {
        const tile = e.target.closest && e.target.closest('.albums-tile');
        if (!tile || e.altKey || e.ctrlKey || e.metaKey) return;
        const index = Number(tile.dataset.index);
        const perPage = state.cols * Math.max(1, Math.floor($('albums-scroll').clientHeight / state.rowH) - 1);
        const moves = {
            ArrowRight: index + 1, ArrowLeft: index - 1, ArrowDown: index + state.cols, ArrowUp: index - state.cols,
            Home: 0, End: state.total - 1, PageDown: index + perPage, PageUp: index - perPage,
        };
        if (e.key in moves) {
            e.preventDefault();
            if ((e.key === 'ArrowDown' && moves.ArrowDown >= state.total) || (e.key === 'ArrowUp' && moves.ArrowUp < 0)) return;
            focusTile(moves[e.key]);
        } else if (e.key === ' ' && window.DVExport) {
            e.preventDefault();
            const file = itemAt(index);
            if (file) selectTile(file, index, e.shiftKey);
        } else if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            tile.click();
        }
    }

    // ── Slimme albums: bewaren en bewerken ───────────────

    let dialogAlbum = null;     // null: nieuw album van de huidige filters

    function openAlbumDialog(album) {
        dialogAlbum = album;
        const modal = $('modal-album');
        $('album-dialog-title').textContent = album ? 'Album bewerken' : 'Bewaar als slim album';
        const name = $('album-name');
        name.value = album ? album.name : suggestName();
        $('album-error').textContent = '';
        const current = album ? album.icon || 'fa-images' : 'fa-images';
        const icons = state.icons.length ? state.icons : Object.keys(ICON_LABELS);
        $('album-icons').replaceChildren(...icons.map(icon => createEl('label', { class: 'album-icon-choice', title: ICON_LABELS[icon] || icon },
            createEl('input', { type: 'radio', name: 'album-icon', value: icon, checked: icon === current, 'aria-label': ICON_LABELS[icon] || icon }),
            createEl('i', { class: `fas ${icon}`, 'aria-hidden': 'true' }))));
        // Wat er in het album komt: de huidige filters (nieuw), of die van het album
        const spec = album ? album.filters : state.spec;
        const sort = album ? album.sort : state.sort;
        const labels = chipLabels(spec || {});
        $('album-filters').replaceChildren(...(labels.length ? labels.map(([, l]) => createEl('span', { class: 'chip', text: l }))
            : [createEl('span', { class: 'album-filters-none', text: 'Geen filters: alles in het archief' })]),
            createEl('span', { class: 'album-filters-sort', text: `· ${(SORTS.find(([v]) => v === sort) || [0, sort])[1]}` }));
        $('album-hidden-row').hidden = !album;
        $('album-hidden').checked = !!(album && album.hidden);
        $('album-delete').hidden = !album;
        $('album-delete').dataset.confirm = '';
        $('album-delete-label').textContent = 'Verwijderen';
        $('album-save').textContent = album ? 'Bewaren' : 'Album bewaren';
        modal.classList.add('active');
        setTimeout(() => { name.focus(); name.select(); }, 60);
    }

    function suggestName() {
        const labels = chipLabels(state.spec).map(([, l]) => l);
        const name = labels.slice(0, 3).join(' · ');
        return name.length > 60 ? name.slice(0, 59) + '…' : name;
    }

    window.albumsCloseDialog = function () {
        $('modal-album').classList.remove('active');
        dialogAlbum = null;
    };

    window.albumsSaveDialog = async function (event) {
        if (event) event.preventDefault();
        const save = $('album-save');
        const icon = (document.querySelector('#album-icons input:checked') || {}).value;
        const body = { name: $('album-name').value, icon };
        let url = `${API_BASE}/api/library/albums`;
        let method = 'POST';
        if (dialogAlbum) {
            url += `/${dialogAlbum.id}`;
            method = 'PATCH';
            body.hidden = $('album-hidden').checked;
        } else {
            body.filters = state.spec;
            body.sort = state.sort;
        }
        save.disabled = true;
        save.classList.add('is-loading');
        try {
            const before = dialogAlbum;
            const album = await fetchJSON(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
            albumsCloseDialog();
            await loadAlbums();
            if (method === 'POST') {
                showToast('Album bewaard', `'${album.name}' staat bij je slimme albums en blijft vanzelf bij.`, 'success');
                albumsShow(album.id);
            } else {
                renderSidebar();
                renderHeader();
                writeHash();
                if (album.hidden && !before.hidden && !state.showHidden) {
                    // Verborgen: weg uit de lijst (het album zelf blijft open), met de weg terug
                    showToast('Album verborgen', `'${album.name}' staat niet meer in de lijst. Je vindt het onder 'Verborgen albums tonen'.`, 'success', {
                        label: 'Ongedaan maken',
                        onClick: () => unhideAlbum(album),
                    });
                }
            }
            $('albums-title').focus();
        } catch (e) {
            $('album-error').textContent = (e && e.message) || 'Bewaren mislukt';
        } finally {
            save.disabled = false;
            save.classList.remove('is-loading');
        }
    };

    async function unhideAlbum(album) {
        try {
            await fetchJSON(`${API_BASE}/api/library/albums/${album.id}`, {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ hidden: false }),
            });
            await loadAlbums();
            showToast('Album weer zichtbaar', `'${album.name}' staat weer in de lijst.`, 'success');
        } catch (e) {
            toastFetchError('Terugzetten mislukt', e);
        }
    }

    window.albumsDeleteFromDialog = async function () {
        const button = $('album-delete');
        const album = dialogAlbum;
        if (!album) return;
        if (!button.dataset.confirm) {
            // Eerst bevestigen: één klik extra, in dezelfde knop
            button.dataset.confirm = '1';
            $('album-delete-label').textContent = 'Echt verwijderen?';
            return;
        }
        try {
            await fetchJSON(`${API_BASE}/api/library/albums/${album.id}`, { method: 'DELETE' });
            albumsCloseDialog();
            showToast('Album verwijderd', `'${album.name}' is weg. Je foto's en video's blijven waar ze staan.`, 'success');
            await loadAlbums();
            albumsShow(null, {}, DEFAULT_SORT);
            $('albums-title').focus();
        } catch (e) {
            $('album-error').textContent = (e && e.message) || 'Verwijderen mislukt';
        }
    };

    async function saveAlbumChanges() {
        const album = currentAlbum();
        if (!album) return;
        try {
            await fetchJSON(`${API_BASE}/api/library/albums/${album.id}`, {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filters: state.spec, sort: state.sort }),
            });
            await loadAlbums();
            showToast('Album bijgewerkt', `'${album.name}' gebruikt nu deze filters.`, 'success');
            renderHeader();
            writeHash();
        } catch (e) {
            toastFetchError('Bewaren mislukt', e);
        }
    }

    // ── Op deze dag ──────────────────────────────────────

    function pad2(n) { return String(n).padStart(2, '0'); }

    // 2000 is een schrikkeljaar: 29 februari is altijd te kiezen
    function validDay(month, day) {
        if (!(month >= 1 && month <= 12 && day >= 1)) return false;
        return day <= new Date(2000, month, 0).getDate();
    }

    function shiftDay(step) {
        const d = new Date(2000, state.otdMonth - 1, state.otdDay + step);
        state.otdMonth = d.getMonth() + 1;
        state.otdDay = d.getDate();
        writeHash();
        loadOnThisDay();
    }
    window.albumsOtdShift = shiftDay;

    window.albumsOtdToday = function () {
        const now = new Date();
        state.otdMonth = now.getMonth() + 1;
        state.otdDay = now.getDate();
        writeHash();
        loadOnThisDay();
        // De knop schakelt zichzelf uit: de focus naar de titel, niet naar de pagina
        $('albums-otd-title').focus();
    };

    function renderOtdControls() {
        const month = $('albums-otd-month');
        const day = $('albums-otd-day');
        if (!month.options.length) {
            month.replaceChildren(...MONTHS.map((m, i) => option(String(i + 1), m)));
            month.addEventListener('change', () => {
                state.otdMonth = Number(month.value);
                state.otdDay = Math.min(state.otdDay, new Date(2000, state.otdMonth, 0).getDate());
                writeHash();
                loadOnThisDay();
            });
            day.addEventListener('change', () => {
                state.otdDay = Number(day.value);
                writeHash();
                loadOnThisDay();
            });
        }
        const days = new Date(2000, state.otdMonth, 0).getDate();
        day.replaceChildren(...Array.from({ length: days }, (_, i) => option(String(i + 1), String(i + 1))));
        month.value = String(state.otdMonth);
        day.value = String(state.otdDay);
        const now = new Date();
        const today = state.otdMonth === now.getMonth() + 1 && state.otdDay === now.getDate();
        $('albums-otd-today').disabled = today;
        $('albums-otd-title-text').textContent = today ? 'Op deze dag' : `Op ${state.otdDay} ${MONTHS[state.otdMonth - 1]}`;
        const dayName = `${state.otdDay} ${MONTHS[state.otdMonth - 1]}`;
        $('albums-otd-subtitle').textContent = today ? `Foto's en video's van vandaag, ${dayName}, in eerdere jaren.`
            : `Foto's en video's van ${dayName} in eerdere jaren.`;
        const allowed = otdFolders();
        const note = $('albums-otd-folders');
        note.replaceChildren(...(allowed.length ? [
            createEl('i', { class: 'fas fa-folder', 'aria-hidden': 'true' }),
            createEl('span', { text: 'Alleen uit ' }),
            ...allowed.slice(0, 3).flatMap((f, i) => [i ? ', ' : '', createEl('span', { class: 'path-label', title: f, text: displayPath(folderAbs(f), 28) })]),
            allowed.length > 3 ? ` en nog ${allowed.length - 3}` : '',
            createEl('span', { text: ' (zoals gekozen bij Foto op deze dag op Home)' }),
        ] : []));
        note.hidden = !allowed.length;
    }

    function otdFolders() {
        try {
            const value = JSON.parse(localStorage.getItem('otdAllowedFolders') || '[]');
            return Array.isArray(value) ? value.filter(v => typeof v === 'string') : [];
        } catch (_) {
            return [];
        }
    }

    async function loadOnThisDay() {
        renderOtdControls();
        const seq = ++state.otdSeq;
        const groups = $('albums-otd-groups');
        groups.replaceChildren(skeletonCards(8, 'albums-otd-skeleton'));
        const params = new URLSearchParams({ date: `${pad2(state.otdMonth)}-${pad2(state.otdDay)}`, per_year: String(OTD_PER_YEAR) });
        otdFolders().forEach(f => params.append('allowed_folders', f));
        try {
            const data = await fetchJSON(`${API_BASE}/api/library/on-this-day?${params}`);
            if (seq !== state.otdSeq) return;
            renderOnThisDay(data);
        } catch (e) {
            if (seq !== state.otdSeq) return;
            groups.replaceChildren(errorState(failTitle(e, 'Op deze dag laden mislukt'), e, loadOnThisDay));
        }
    }

    function yearsAgo(n) {
        return n === 1 ? '1 jaar geleden' : `${n} jaar geleden`;
    }

    function renderOnThisDay(data) {
        const groups = $('albums-otd-groups');
        state.otdItems = [];
        if (!data.years.length) {
            groups.replaceChildren(emptyState({
                icon: 'fa-camera-retro',
                title: data.ready ? 'Geen herinneringen op deze dag' : 'De bibliotheek wordt nog opgebouwd',
                text: data.ready ? 'Geen foto\'s of video\'s van deze dag in eerdere jaren. Probeer een dag eerder of later.'
                    : 'De app leest het archief één keer helemaal in. Dit vult zich vanzelf zodra dat klaar is.',
                action: data.ready ? { label: 'Volgende dag', onClick: () => shiftDay(1) } : { label: 'Opnieuw kijken', onClick: loadOnThisDay },
            }));
            return;
        }
        const sections = data.years.map(group => {
            const start = state.otdItems.length;
            state.otdItems.push(...group.items);
            const photos = group.items.filter(i => i.type === 'photo').length;
            const videos = group.items.length - photos;
            const parts = [];
            if (group.total > group.items.length) parts.push(`${formatNumberNl(group.total)} bestanden`);
            else {
                if (photos) parts.push(`${photos} ${photos === 1 ? 'foto' : 'foto\'s'}`);
                if (videos) parts.push(`${videos} ${videos === 1 ? 'video' : 'video\'s'}`);
            }
            const grid = createEl('div', { class: 'albums-otd-grid' },
                ...group.items.map((file, i) => tileNode(file, start + i, openOtd)));
            const realDay = `${group.year}-${pad2(data.month)}-${pad2(data.day)}`;
            return createEl('section', { class: 'albums-otd-year', 'aria-label': `${yearsAgo(group.years_ago)}, ${group.year}` },
                createEl('header', { class: 'albums-otd-year-head' },
                    createEl('h2', { class: 'albums-otd-year-title' },
                        createEl('span', { text: yearsAgo(group.years_ago) }),
                        createEl('span', { class: 'albums-otd-year-number tabular', text: String(group.year) })),
                    createEl('span', { class: 'albums-otd-year-meta', text: parts.join(' · ') }),
                    // Precies deze N: de server geeft per jaar het filter mee (met de mappen van Home,
                    // alleen echte opnamedatums, en 29 februari bij 28 februari in een gewoon jaar)
                    group.total > group.items.length ? createEl('button', {
                        type: 'button', class: 'btn btn-ghost btn-sm',
                        onClick: () => { albumsShow(null, group.filters || { kind: ['photo', 'video'], capture_date: 'known', date_from: realDay, date_to: realDay }, 'taken'); $('albums-title').focus(); },
                    }, `Alle ${formatNumberNl(group.total)} bekijken`, createEl('i', { class: 'fas fa-arrow-right', 'aria-hidden': 'true' })) : null),
                grid);
        });
        groups.replaceChildren(...sections);
        const first = groups.querySelector('.albums-tile');
        if (first) first.tabIndex = 0;
    }

    function openOtd(index) {
        openViewer(state.otdItems, index, { onRemoved: () => loadOnThisDay() });
    }

    // Pijltjes door alle jaren heen: links/rechts op volgorde, op/neer naar de foto erboven of eronder
    function onOtdKeydown(e) {
        const tile = e.target.closest && e.target.closest('.albums-tile');
        if (!tile || e.altKey || e.ctrlKey || e.metaKey) return;
        const tiles = [...$('albums-otd-groups').querySelectorAll('.albums-tile')];
        const at = tiles.indexOf(tile);
        let next = null;
        if (e.key === 'ArrowRight') next = tiles[at + 1];
        else if (e.key === 'ArrowLeft') next = tiles[at - 1];
        else if (e.key === 'Home') next = tiles[0];
        else if (e.key === 'End') next = tiles[tiles.length - 1];
        else if (['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp'].includes(e.key)) {
            const box = tile.getBoundingClientRect();
            const down = e.key === 'ArrowDown' || e.key === 'PageDown';
            // PageUp/PageDown: ongeveer een scherm verder (minstens één rij), op dezelfde plek in de rij
            const page = e.key.startsWith('Page') ? Math.max(box.height, window.innerHeight * 0.8) : 0;
            let candidates = tiles.map(t => [t, t.getBoundingClientRect()])
                .filter(([, b]) => down ? b.top > box.top + 2 + Math.max(0, page - box.height) : b.top < box.top - 2 - Math.max(0, page - box.height));
            if (!candidates.length && page) {
                // Voorbij het eind of het begin: de laatste of eerste rij
                candidates = tiles.map(t => [t, t.getBoundingClientRect()])
                    .filter(([, b]) => down ? b.top > box.top + 2 : b.top < box.top - 2);
                if (candidates.length) {
                    const edge = down ? Math.max(...candidates.map(([, b]) => b.top)) : Math.min(...candidates.map(([, b]) => b.top));
                    candidates = candidates.filter(([, b]) => Math.abs(b.top - edge) < 2);
                }
            }
            if (candidates.length) {
                const rowTop = down ? Math.min(...candidates.map(([, b]) => b.top)) : Math.max(...candidates.map(([, b]) => b.top));
                const row = candidates.filter(([, b]) => Math.abs(b.top - rowTop) < 2);
                next = row.reduce((best, cur) => Math.abs(cur[1].left - box.left) < Math.abs(best[1].left - box.left) ? cur : best)[0];
            } else if (page) {
                next = down ? tiles[tiles.length - 1] : tiles[0];    // al op de bovenste of onderste rij, zoals in het raster
            }
        } else if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            tile.click();
            return;
        } else return;
        e.preventDefault();
        if (!next) return;
        tile.tabIndex = -1;
        next.tabIndex = 0;
        next.focus();
        next.scrollIntoView({ block: 'nearest' });
    }

    // ── Hulpjes ──────────────────────────────────────────

    function failTitle(error, title) {
        return error && error.code === 'vault_unavailable' ? 'Archief niet bereikbaar' : title;
    }

    function debounce(fn, ms) {
        let timer = null;
        return (...args) => {
            clearTimeout(timer);
            timer = setTimeout(() => fn(...args), ms);
        };
    }

    // De bibliotheek is bijgewerkt (jobs.js) of de schijf is terug: tellingen en raster opnieuw
    window.albumsLibraryUpdated = async function () {
        if (!state.inited) return;
        loadAlbums();
        loadFacets();
        if (currentMainView() !== 'albums') return;
        if (state.mode === 'otd') {
            loadOnThisDay();
            return;
        }
        // Alleen opnieuw laden als er in deze selectie echt iets veranderde
        const seq = state.seq;
        try {
            const data = await fetchJSON(`${API_BASE}/api/library/browse?${apiParams(0, 1)}`);
            if (seq === state.seq && data.version !== state.version) reload(true);
        } catch (_) { /* de volgende keer weer */ }
    };

    // Een bladwijzer naar #albums...: meteen naar Albums. app.js opent bij het starten eerst Home
    // (en dat haalt het adres weg), dus het adres van het laden zelf onthouden en terugzetten.
    const bootHash = location.hash;
    document.addEventListener('DOMContentLoaded', () => {
        if (!bootHash.startsWith('#albums')) return;
        history.replaceState(history.state, '', location.pathname + location.search + bootHash);
        switchMainView('albums');
    });

    addShortcutGroupSafe();
    function addShortcutGroupSafe() {
        if (typeof addShortcutGroup !== 'function') return;
        addShortcutGroup('Albums', [
            [['←', '→', '↑', '↓'], 'Door de foto\'s'],
            [['Enter'], 'Foto openen in de viewer'],
            [['spatie'], 'Foto selecteren voor export'],
            [['Home', 'End'], 'Naar de eerste of laatste foto'],
            [['PageUp', 'PageDown'], 'Een scherm terug of verder (ook in Op deze dag)'],
        ]);
    }
})();
