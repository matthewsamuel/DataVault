// DataVault Dashboard - Zoeken (Slim zoeken en Bestandsnaam)
// Note: API_BASE, openViewer, switchMainView en formatBytes staan in app.js; createEl en
// vaultFileUrl in dom.js; emptyState, errorState, skeletonCards en registerOverlayCloser in
// ui.js; jobsStart en jobsOnChange in jobs.js; registerSettingsSection in settings.js.
//
// Eén zoekvenster voor de hele bibliotheek (backend/semantic.py, /api/search/...):
//   Slim zoeken    op wat er op de foto staat ('hond op het strand'), in het Nederlands of
//                  Engels; het zoekmodel draait op deze Mac
//   Bestandsnaam   op (een deel van) de naam van het bestand
// Openen: Enter in het zoekveld bovenaan, de sneltoets s, of 'Vergelijkbaar' in de viewer
// (foto's die op deze foto lijken). Het venster gebruikt de filters van de bibliotheek
// (soort en periode). Resultaten staan op volgorde van hoe goed ze passen; de pijltjes lopen
// door het raster en Enter opent de viewer met de resultaten als lijst.

const SEARCH_PAGE = 60;
const SEARCH_DEBOUNCE = 350;
const SEARCH_EXAMPLES = ['hond op het strand', 'verjaardagstaart', 'zonsondergang boven zee', 'sneeuw in de bergen'];
const SEARCH_WEAK_PREVIEW = 8;       // past niets echt goed: zoveel van wat er het meest op lijkt meteen tonen
// Slim zoeken begrijpt geen ontkenning ('strand zonder mensen' geeft juist mensen op het strand)
const SEARCH_NEGATION = /(^|\s)(zonder|niet|geen|without|not|no)(\s|$)/i;
const SEARCH_NEGATION_HINT = 'Woorden als ‘zonder’ en ‘niet’ begrijpt Slim zoeken niet: beschrijf wat er wél op staat.';

const searchState = {
    mode: 'semantic',      // 'semantic' | 'filename' | 'similar'
    textMode: 'semantic',  // de laatst gekozen tekst-modus (similar keert hierheen terug)
    query: '',
    similar: null,         // {path, name} van de foto waar 'Vergelijkbaar' mee begon
    kind: '',              // '' | 'photo' | 'video'
    dateFrom: '',
    dateTo: '',
    items: [],
    total: 0,
    ranked: 0,
    nextOffset: null,
    includeWeak: false,
    weakPreview: false,    // niets past echt goed: de eerste foto's die er het meest op lijken staan er al
    loading: false,
    seq: 0,
    timer: null,
    status: null,          // /api/search/status
    focusIndex: 0,
};

function searchRememberedMode() {
    try { return localStorage.getItem('dv-search-mode') || 'semantic'; } catch (e) { return 'semantic'; }
}

function searchRememberMode(mode) {
    try { localStorage.setItem('dv-search-mode', mode); } catch (e) { /* privé-venster: niet onthouden */ }
}

async function searchFetchStatus() {
    try {
        searchState.status = await fetchJSON(`${API_BASE}/api/search/status`, { cache: 'no-store' });
    } catch (e) {
        searchState.status = null;
    }
    return searchState.status;
}

// Het zoekmodel alvast laden (een paar seconden de eerste keer) zodra iemand gaat zoeken:
// als het zoekveld focus krijgt of het venster opent, niet pas bij de eerste zoekopdracht
let searchWarmedAt = 0;

function searchWarm() {
    if (Date.now() - searchWarmedAt < 60000) return;
    searchWarmedAt = Date.now();
    fetch(`${API_BASE}/api/search/warm`, { method: 'POST' }).catch(() => {});
}

document.addEventListener('DOMContentLoaded', () => {
    const field = document.getElementById('global-search');
    if (field) field.addEventListener('focus', searchWarm);
});

// ==================== HET VENSTER ====================

function buildSearchOverlay() {
    const input = createEl('input', {
        type: 'search', class: 'input search-hero-input', id: 'search-input', autocomplete: 'off', spellcheck: 'false',
        'data-autofocus': '', 'aria-label': 'Zoekopdracht', maxlength: '200',
        onInput: () => searchQueue(), onKeydown: searchInputKeydown,
    });
    const modeButton = (mode, icon, label, hint) => createEl('button', {
        type: 'button', class: 'search-mode', dataset: { mode }, 'aria-pressed': 'false', title: hint,
        onClick: () => searchSetMode(mode),
    }, createEl('i', { class: `fas ${icon}`, 'aria-hidden': 'true' }), ` ${label}`);
    const kindPill = (kind, label) => createEl('button', {
        type: 'button', class: 'pill search-kind', dataset: { kind }, 'aria-pressed': String(kind === ''),
        onClick: () => { searchState.kind = kind; searchRenderFilters(); searchRun(); },
    }, label);
    const dateInput = (id, label, key) => createEl('label', { class: 'search-date' },
        createEl('span', { text: label }),
        createEl('input', {
            type: 'date', class: 'input input-sm is-empty', id, 'aria-label': `${label} (datum)`,
            onChange: (e) => { searchState[key] = e.target.value; searchRenderFilters(); searchRun(); },
        }));
    const overlay = createEl('div', {
        id: 'search-overlay', class: 'dv-overlay search-overlay', role: 'dialog', 'aria-modal': 'true',
        'aria-labelledby': 'search-title', onClick: (e) => { if (e.target === e.currentTarget) closeSearch(); },
    },
        createEl('div', { class: 'dv-overlay-panel search-panel' },
            createEl('div', { class: 'dv-overlay-header' },
                createEl('h2', { class: 'modal-title', id: 'search-title' },
                    createEl('i', { class: 'fas fa-magnifying-glass', 'aria-hidden': 'true' }), createEl('span', { text: 'Zoeken' })),
                createEl('button', { type: 'button', class: 'modal-close', 'aria-label': 'Sluiten', title: 'Sluiten (Esc)', onClick: closeSearch }, '×')),
            createEl('div', { class: 'search-bar' },
                createEl('div', { class: 'search-field search-hero' },
                    createEl('i', { class: 'fas fa-magnifying-glass', 'aria-hidden': 'true' }), input),
                createEl('div', { class: 'segmented search-modes', role: 'group', 'aria-label': 'Zoeken op' },
                    modeButton('semantic', 'fa-wand-magic-sparkles', 'Slim zoeken', 'Op wat er op de foto staat'),
                    modeButton('filename', 'fa-font', 'Bestandsnaam', 'Op (een deel van) de bestandsnaam'))),
            createEl('div', { class: 'search-similar', id: 'search-similar', hidden: true }),
            createEl('div', { class: 'search-filters', role: 'group', 'aria-label': 'Filters' },
                createEl('div', { class: 'search-kinds' },
                    kindPill('', 'Alles'), kindPill('photo', 'Foto’s'), kindPill('video', 'Video’s')),
                createEl('div', { class: 'search-dates' },
                    dateInput('search-date-from', 'Van', 'dateFrom'), dateInput('search-date-to', 'Tot en met', 'dateTo'),
                    createEl('button', {
                        type: 'button', class: 'link-button search-dates-clear', id: 'search-dates-clear', hidden: true,
                        onClick: () => { searchState.dateFrom = searchState.dateTo = ''; searchResetDates(); searchRenderFilters(); searchRun(); },
                    }, 'Periode wissen'))),
            createEl('div', { class: 'search-status', id: 'search-status', role: 'status', 'aria-live': 'polite' }),
            createEl('div', { class: 'search-scroll', id: 'search-scroll' },
                createEl('div', { id: 'search-notice' }),
                createEl('div', { class: 'search-results', id: 'search-results', role: 'list', 'aria-label': 'Resultaten' }),
                createEl('div', { class: 'search-more', id: 'search-more' }),
                createEl('div', { id: 'search-sentinel', 'aria-hidden': 'true' }))));
    document.body.appendChild(overlay);
    const results = overlay.querySelector('#search-results');
    results.addEventListener('keydown', searchResultsKeydown);
    results.addEventListener('focusin', (e) => {
        const card = e.target.closest('[data-index]');
        if (card) searchSetFocus(Number(card.dataset.index), false);
    });
    new IntersectionObserver((entries) => {
        if (entries.some(e => e.isIntersecting)) searchLoadMore();
    }, { root: overlay.querySelector('#search-scroll'), rootMargin: '400px' }).observe(overlay.querySelector('#search-sentinel'));
    return overlay;
}

function searchResetDates() {
    document.getElementById('search-date-from').value = '';
    document.getElementById('search-date-to').value = '';
    document.getElementById('search-date-from').classList.add('is-empty');
    document.getElementById('search-date-to').classList.add('is-empty');
}

// Waar de focus heen gaat als het venster sluit: het element dat het opende (niet het zoekveld
// in het venster zelf, dat dan verborgen is)
let searchOpener = null;

function searchRememberOpener() {
    const active = document.activeElement;
    const overlay = document.getElementById('search-overlay');
    if (searchIsOpen()) return;       // al open: de eerste opener blijft
    searchOpener = active && active !== document.body && !(overlay && overlay.contains(active)) ? active : null;
}

function searchOverlay() {
    return document.getElementById('search-overlay') || buildSearchOverlay();
}

function searchIsOpen() {
    const el = document.getElementById('search-overlay');
    return !!el && el.classList.contains('active');
}

// Openen met een tekst (uit het zoekveld bovenaan) of leeg (sneltoets s)
function openSearch(query = '', { mode = null } = {}) {
    searchRememberOpener();
    const overlay = searchOverlay();
    const input = overlay.querySelector('#search-input');
    searchState.similar = null;
    searchState.textMode = mode || searchState.textMode || searchRememberedMode();
    searchState.mode = searchState.textMode;
    input.value = query;
    overlay.classList.add('active');
    input.focus();
    searchRenderMode();
    searchRenderFilters();
    searchWarm();
    searchFetchStatus().then(() => searchRun());
}

// 'Vergelijkbaar' in de viewer: foto's die op deze foto lijken
function openSimilar(file) {
    if (!file || !file.path) return;
    searchRememberOpener();
    if (typeof isViewerOpen === 'function' && isViewerOpen()) closeModal();
    const overlay = searchOverlay();
    searchState.similar = { path: file.path, name: file.name };
    searchState.mode = 'similar';
    overlay.classList.add('active');
    overlay.querySelector('#search-input').value = '';
    searchRenderMode();
    searchRenderFilters();
    searchFetchStatus().then(() => {
        searchRun();
        const first = overlay.querySelector('#search-similar .btn');
        if (first) first.focus();
    });
}

function closeSearch() {
    const overlay = document.getElementById('search-overlay');
    const wasOpen = searchIsOpen();
    if (overlay) overlay.classList.remove('active');
    if (wasOpen) {
        // na het teruggeven van de focus door het overlay-systeem: naar de opener, of het zoekveld
        const opener = searchOpener && searchOpener.isConnected && searchOpener.offsetParent !== null
            ? searchOpener : document.getElementById('global-search');
        searchOpener = null;
        setTimeout(() => { if (!searchIsOpen() && opener) opener.focus(); }, 0);
    }
    clearTimeout(searchState.timer);
    searchState.seq++;
}

registerOverlayCloser('search-overlay', closeSearch);

function searchSetMode(mode) {
    searchState.mode = mode;
    searchState.textMode = mode;
    searchState.similar = null;
    searchRememberMode(mode);
    searchRenderMode();
    searchRun();
    document.getElementById('search-input').focus();
}

function searchRenderMode() {
    const overlay = searchOverlay();
    overlay.querySelectorAll('.search-mode').forEach(button => {
        const on = button.dataset.mode === (searchState.mode === 'similar' ? '' : searchState.mode);
        button.classList.toggle('active', on);
        button.setAttribute('aria-pressed', String(on));
    });
    const input = overlay.querySelector('#search-input');
    input.placeholder = searchState.mode === 'filename' ? 'Typ (een deel van) een bestandsnaam…'
        : 'Waar zoek je naar? Bijvoorbeeld: hond op het strand';
    const similar = overlay.querySelector('#search-similar');
    similar.hidden = searchState.mode !== 'similar';
    overlay.querySelector('.search-bar').hidden = searchState.mode === 'similar';
    if (searchState.mode === 'similar' && searchState.similar) {
        const file = searchState.similar;
        similar.replaceChildren(
            createEl('div', { class: 'search-similar-thumb thumb-tile' },
                createEl('img', { src: vaultFileUrl('thumb', file.path), alt: '' })),
            createEl('div', { class: 'search-similar-text' },
                createEl('div', { class: 'overline', text: 'Lijkt op' }),
                createEl('div', { class: 'search-similar-title', text: file.name }),
                createEl('div', { class: 'path-label', text: displayPath(file.path), title: file.path })),
            createEl('button', {
                type: 'button', class: 'btn btn-secondary', onClick: () => searchSetMode(searchState.textMode || 'semantic'),
            }, createEl('i', { class: 'fas fa-magnifying-glass', 'aria-hidden': 'true' }), ' Zoeken op tekst'));
    }
    document.getElementById('search-title').querySelector('span').textContent =
        searchState.mode === 'similar' ? 'Vergelijkbare foto’s' : 'Zoeken';
}

function searchRenderFilters() {
    const overlay = searchOverlay();
    overlay.querySelectorAll('.search-kind').forEach(pill => {
        const on = pill.dataset.kind === searchState.kind;
        pill.classList.toggle('active', on);
        pill.setAttribute('aria-pressed', String(on));
    });
    overlay.querySelector('#search-date-from').classList.toggle('is-empty', !searchState.dateFrom);
    overlay.querySelector('#search-date-to').classList.toggle('is-empty', !searchState.dateTo);
    overlay.querySelector('#search-dates-clear').hidden = !(searchState.dateFrom || searchState.dateTo);
}

// ==================== ZOEKEN ====================

function searchQueue() {
    clearTimeout(searchState.timer);
    searchState.timer = setTimeout(searchRun, SEARCH_DEBOUNCE);
}

function searchParams(offset) {
    const params = new URLSearchParams();
    if (searchState.mode === 'similar') params.set('path', searchState.similar.path);
    else params.set('q', searchState.query);
    if (searchState.kind) params.set('kind', searchState.kind);
    if (searchState.dateFrom) params.set('date_from', searchState.dateFrom);
    if (searchState.dateTo) params.set('date_to', searchState.dateTo);
    if (searchState.includeWeak) params.set('include_weak', 'true');
    params.set('offset', String(offset));
    params.set('limit', String(SEARCH_PAGE));
    return params;
}

function searchEndpoint() {
    return { semantic: 'semantic', filename: 'filename', similar: 'similar' }[searchState.mode];
}

async function searchRun() {
    clearTimeout(searchState.timer);
    const overlay = searchOverlay();
    searchState.query = overlay.querySelector('#search-input').value.trim();
    searchState.includeWeak = false;
    searchState.weakPreview = false;
    searchState.items = [];
    searchState.nextOffset = null;
    searchState.focusIndex = 0;
    const seq = ++searchState.seq;
    const results = overlay.querySelector('#search-results');
    overlay.querySelector('#search-more').replaceChildren();
    const status = searchState.status;
    const needsModel = searchState.mode !== 'filename';
    if (needsModel && status && !status.ready) {
        searchShowState(searchNotInstalledState(status));
        return;
    }
    if (searchState.mode !== 'similar' && !searchState.query) {
        searchShowState(searchStartState());
        return;
    }
    results.replaceChildren(...skeletonCards(18, 'search-skeleton').children);
    // Het zoekmodel wordt nog ingelezen (na het opstarten, of na een kwartier niet zoeken)
    const modelLoading = searchState.mode === 'semantic' && status && status.text_loaded === false;
    searchSetStatus(modelLoading ? 'Zoekmodel laden… De eerste zoekopdracht duurt even.' : 'Zoeken…');
    searchState.loading = true;
    try {
        let data = await fetchJSON(`${API_BASE}/api/search/${searchEndpoint()}?${searchParams(0)}`);
        if (seq !== searchState.seq) return;
        if (status && searchState.mode === 'semantic') status.text_loaded = true;
        if (!data.items.length && data.ranked > 0 && searchState.mode !== 'filename') {
            // Niets past echt goed: meteen tonen wat er het meest op lijkt, niet achter een knop
            const params = searchParams(0);
            params.set('include_weak', 'true');
            params.set('limit', String(SEARCH_WEAK_PREVIEW));
            const weak = await fetchJSON(`${API_BASE}/api/search/${searchEndpoint()}?${params}`);
            if (seq !== searchState.seq) return;
            if (weak.items.length) {
                searchState.includeWeak = true;
                searchState.weakPreview = true;
                data = { ...weak, total: 0 };
            }
        }
        searchState.items = data.items;
        searchState.total = data.total;
        searchState.ranked = data.ranked;
        searchState.nextOffset = data.next_offset;
        searchRenderResults(data, true);
    } catch (error) {
        if (seq !== searchState.seq) return;
        if (error.status === 409) {
            await searchFetchStatus();
            searchShowState(searchNotInstalledState(searchState.status || {}));
        } else if (error.status === 404 && searchState.mode === 'similar') {
            searchShowState(emptyState({ icon: 'fa-hourglass-half', title: 'Deze foto is nog niet herkend',
                text: 'Slim zoeken is nog bezig met je foto’s. Probeer het straks nog eens.' }));
        } else {
            searchShowState(errorState('Zoeken lukte niet', error, () => searchRun()));
        }
    } finally {
        if (seq === searchState.seq) searchState.loading = false;
    }
}

async function searchLoadMore() {
    if (!searchIsOpen() || searchState.loading || searchState.nextOffset === null || searchState.weakPreview) return;
    const seq = searchState.seq;
    searchState.loading = true;
    try {
        const data = await fetchJSON(`${API_BASE}/api/search/${searchEndpoint()}?${searchParams(searchState.nextOffset)}`);
        if (seq !== searchState.seq) return;
        searchState.items = searchState.items.concat(data.items);
        searchState.nextOffset = data.next_offset;
        searchRenderResults(data, false);
    } catch (error) {
        if (seq === searchState.seq) toastFetchError('Meer resultaten laden lukte niet', error);
    } finally {
        if (seq === searchState.seq) searchState.loading = false;
    }
}

// Na de beste resultaten: de foto's die minder zeker passen, alleen op verzoek
async function searchShowWeak() {
    const first = searchState.items.length;
    searchState.includeWeak = true;
    searchState.weakPreview = false;
    searchState.nextOffset = first;
    searchOverlay().querySelector('#search-more').replaceChildren();
    await searchLoadMore();
    // de knop is weg: de focus naar de eerste nieuwe foto
    const card = searchOverlay().querySelector(`#search-results [data-index="${first}"]`);
    if (card) card.focus();
}

function searchClearFilters() {
    searchState.kind = '';
    searchState.dateFrom = searchState.dateTo = '';
    searchResetDates();
    searchRenderFilters();
    searchRun();
}

function searchShowState(node) {
    const overlay = searchOverlay();
    overlay.querySelector('#search-notice').replaceChildren();
    overlay.querySelector('#search-results').replaceChildren();
    overlay.querySelector('#search-more').replaceChildren(node);
    searchSetStatus('');
}

function searchSetStatus(text, ...extra) {
    const el = document.getElementById('search-status');
    if (el) el.replaceChildren(...(text ? [createEl('span', { text })] : []), ...extra);
}

function searchStartState() {
    if (searchState.mode === 'filename') {
        return emptyState({ icon: 'fa-font', title: 'Zoek op bestandsnaam',
            text: 'Typ een deel van de naam, zoals IMG_2041 of vakantie. Hoofdletters maken niet uit.' });
    }
    const state = emptyState({ icon: 'fa-wand-magic-sparkles', title: 'Zoek op wat er op de foto staat',
        text: 'Beschrijf wat je zoekt, in het Nederlands of Engels. Alles blijft op deze Mac.' });
    state.append(createEl('div', { class: 'search-examples' }, ...SEARCH_EXAMPLES.map(example => createEl('button', {
        type: 'button', class: 'pill', onClick: () => {
            document.getElementById('search-input').value = example;
            searchRun();
        },
    }, example))), createEl('p', { class: 'search-hint' },
        createEl('i', { class: 'fas fa-circle-info', 'aria-hidden': 'true' }), createEl('span', { text: SEARCH_NEGATION_HINT })));
    return state;
}

function searchNotInstalledState(status) {
    const model = status.model || {};
    const downloading = status.download && JOB_ACTIVE.has(status.download.status);
    return emptyState({
        icon: 'fa-wand-magic-sparkles',
        title: downloading ? 'Het zoekmodel wordt geïnstalleerd' : 'Slim zoeken is nog niet geïnstalleerd',
        text: downloading ? 'De voortgang staat rechtsonder. Zoeken op bestandsnaam werkt al.'
            : `Installeer eenmalig het zoekmodel (ongeveer ${formatBytes(model.size || 0)}) in Instellingen. `
              + 'Daarna vind je foto’s op wat erop staat. Zoeken op bestandsnaam werkt nu al.',
        action: downloading ? null : { label: 'Naar Instellingen', primary: true, onClick: () => {
            closeSearch();
            switchMainView('settings');
            setTimeout(() => {
                const section = document.getElementById('settings-zoeken');
                if (section) section.scrollIntoView({ block: 'start' });
                const button = document.getElementById('settings-search-install');
                if (button) button.focus();
            }, 50);
        } },
    });
}

function searchItemFile(item) {
    return {
        path: item.path, name: item.name, type: item.kind, size: item.size,
        timestamp: item.taken, date_source: item.taken_source, rating: item.rating, votes: item.votes,
    };
}

function searchResultCard(item, index) {
    const img = createEl('img', { class: 'loading', alt: item.name, loading: 'lazy', decoding: 'async' });
    img.addEventListener('load', () => img.classList.remove('loading'));
    img.addEventListener('error', () => img.classList.remove('loading'));
    img.src = vaultFileUrl('thumb', item.path);
    // Dezelfde foto op meer plekken (kopieën) is één tegel met een teller
    const copies = item.copies || 0;
    const copiesText = copies ? `nog ${copies} ${copies === 1 ? 'kopie' : 'kopieën'}` : '';
    const where = copies ? `\nOok in: ${(item.copy_paths || []).map(p => displayPath(p.slice(0, p.lastIndexOf('/')))).join(', ')}` : '';
    const card = createEl('div', {
        class: 'search-result thumb-tile', role: 'listitem', tabindex: index === searchState.focusIndex ? '0' : '-1',
        dataset: { index: String(index), motionKey: item.path }, 'aria-label': copies ? `${item.name}, ${copiesText}` : item.name,
        title: item.name + where,
        onClick: () => searchOpen(index),
    }, img, createEl('div', { class: 'thumb-caption', text: item.name }));
    if (item.kind === 'video') {
        card.append(createEl('span', { class: 'search-result-badge', 'aria-hidden': 'true' },
            createEl('i', { class: 'fas fa-play' })));
    }
    if (copies) {
        card.append(createEl('span', { class: 'search-result-copies', 'aria-hidden': 'true' },
            createEl('i', { class: 'fas fa-clone' }), createEl('span', { text: `+${formatNumberNl(copies)}` })));
    }
    return card;
}

function searchRenderResults(data, fresh) {
    const overlay = searchOverlay();
    const results = overlay.querySelector('#search-results');
    const start = fresh ? 0 : results.children.length;
    const cards = data.items.map((item, i) => searchResultCard(item, start + i));
    // De resultaten stromen binnen: de eerste keer een golf over wat in beeld staat (de rest bij
    // het scrollen); verfijn je de zoekopdracht, dan schuift wat blijft naar zijn nieuwe plek
    const root = results.closest('.search-scroll') || null;
    const refine = fresh && results.querySelector('.search-result');
    if (refine) {
        MediaMotion.flipByKey(results.querySelectorAll('.search-result'), el => el.dataset.motionKey,
            () => results.replaceChildren(...cards), { after: () => cards });
    } else if (fresh) {
        results.replaceChildren(...cards);
        MediaMotion.cascade(cards, { revealRest: true, root });
    } else {
        results.append(...cards);
        MediaMotion.revealTiles(cards, { root });
    }
    const more = overlay.querySelector('#search-more');
    more.replaceChildren();
    const notice = overlay.querySelector('#search-notice');
    if (fresh) notice.replaceChildren();
    if (fresh && searchState.weakPreview) {
        notice.append(createEl('div', { class: 'search-notice', role: 'status' },
            createEl('i', { class: 'fas fa-magnifying-glass search-notice-icon', 'aria-hidden': 'true' }),
            createEl('div', {},
                createEl('div', { class: 'search-notice-title', text: 'Niets past echt goed' }),
                createEl('div', { class: 'search-notice-text', text: SEARCH_NEGATION.test(searchState.query)
                    ? SEARCH_NEGATION_HINT : 'Dit is wat er het meest op lijkt. Andere woorden geven misschien betere resultaten.' }))));
    }
    const count = data.total;
    if (searchState.weakPreview) {
        if (data.next_offset !== null) {
            more.append(createEl('button', { type: 'button', class: 'btn btn-secondary', id: 'search-show-weak', onClick: searchShowWeak },
                createEl('i', { class: 'fas fa-angles-down', 'aria-hidden': 'true' }), ' Meer foto’s die minder goed passen'));
        }
    } else if (fresh && !data.items.length) {
        const weak = searchState.mode !== 'filename' && data.ranked > 0;
        const filtered = !!(searchState.kind || searchState.dateFrom || searchState.dateTo);
        const noun = searchState.kind === 'video' ? 'video' : searchState.kind === 'photo' ? 'foto' : 'foto of video';
        let action = null;
        if (weak) action = { label: 'Toon wat er het meest op lijkt', onClick: searchShowWeak };
        else if (filtered) action = { label: 'Filters wissen', onClick: searchClearFilters };
        more.append(emptyState({
            icon: 'fa-magnifying-glass', title: 'Niets gevonden',
            text: weak ? `Geen ${noun} past echt goed. Probeer andere woorden, of bekijk wat er het meest op lijkt.`
                : 'Probeer andere woorden of minder filters.',
            action,
        }));
    } else if (searchState.nextOffset === null && searchState.mode !== 'filename' && !searchState.includeWeak
        && searchState.ranked > searchState.items.length) {
        more.append(createEl('button', { type: 'button', class: 'btn btn-secondary', id: 'search-show-weak', onClick: searchShowWeak },
            createEl('i', { class: 'fas fa-angles-down', 'aria-hidden': 'true' }), ' Meer foto’s die minder goed passen'));
    }
    const status = searchState.status || {};
    const parts = [];
    const nothing = fresh && !data.items.length;
    if (nothing) {
        // geen telling boven een leeg scherm
    } else if (searchState.weakPreview) {
        parts.push(`${formatNumberNl(searchState.items.length)} die er het meest op lijken`);
    } else if (searchState.mode === 'filename') {
        parts.push(`${formatNumberNl(count)} ${count === 1 ? 'bestand' : 'bestanden'}`);
    } else {
        parts.push(searchState.includeWeak ? `${formatNumberNl(searchState.items.length)} getoond`
            : `${formatNumberNl(count)} ${count === 1 ? 'goed resultaat' : 'goede resultaten'}`);
    }
    if (!nothing && typeof data.took_ms === 'number') {
        parts.push(data.took_ms < 1000 ? `${formatNumberNl(Math.max(1, data.took_ms))} ms`
            : `${formatNumberNl(data.took_ms / 1000, 1)} s`);
    }
    const extra = [];
    if (searchState.mode !== 'filename' && data.pending > 0) {
        extra.push(createEl('span', { class: 'badge badge-info search-pending',
            text: `Nog bezig: ${formatNumberNl(data.indexed)} van ${formatNumberNl(data.indexed + data.pending + (status.undecodable || 0))} herkend` }));
    }
    if (data.library_ready === false) {
        extra.push(createEl('span', { class: 'badge badge-warning', text: 'De bibliotheek wordt nog opgebouwd' }));
    }
    if (fresh && searchState.mode === 'semantic' && !searchState.weakPreview && SEARCH_NEGATION.test(searchState.query)) {
        extra.push(createEl('span', { class: 'badge badge-info', text: 'Tip: ‘zonder’ en ‘niet’ werken niet bij Slim zoeken' }));
    }
    searchSetStatus(parts.join(' · '), ...extra);
}

function searchOpen(index) {
    const files = searchState.items.map(searchItemFile);
    openViewer(files, index, {
        onRemoved: (file) => {
            const at = searchState.items.findIndex(item => item.path === file.path);
            if (at < 0) return;
            searchState.items.splice(at, 1);
            const results = document.getElementById('search-results');
            // Het verplaatste bestand gaat zichtbaar weg, de rest schuift aan
            MediaMotion.flipByKey(results.children, el => el.dataset.motionKey,
                () => results.replaceChildren(...searchState.items.map(searchResultCard)), { after: () => results.children });
        },
    });
}

// ==================== TOETSENBORD ====================

function searchInputKeydown(e) {
    if (e.key === 'Enter') {
        e.preventDefault();
        searchRun();
    } else if (e.key === 'ArrowDown' && searchState.items.length) {
        e.preventDefault();
        searchSetFocus(0, true);
    }
}

function searchColumns(results) {
    const cards = results.querySelectorAll('.search-result');
    if (cards.length < 2) return 1;
    const top = cards[0].offsetTop;
    let cols = 0;
    for (const card of cards) {
        if (card.offsetTop !== top) break;
        cols++;
    }
    return Math.max(1, cols);
}

function searchSetFocus(index, focus) {
    const results = document.getElementById('search-results');
    const count = results.children.length;
    if (!count) return;
    index = Math.max(0, Math.min(count - 1, index));
    const card = results.querySelector(`[data-index="${index}"]`);
    const previous = results.querySelector('[data-index][tabindex="0"]');
    if (previous && previous !== card) previous.tabIndex = -1;
    card.tabIndex = 0;
    searchState.focusIndex = index;
    if (focus) {
        card.focus({ preventScroll: true });
        card.scrollIntoView({ block: 'nearest' });
        if (index >= count - 6) searchLoadMore();
    }
}

function searchResultsKeydown(e) {
    const card = e.target.closest('[data-index]');
    if (!card || e.altKey || e.ctrlKey || e.metaKey) return;
    const results = e.currentTarget;
    const index = Number(card.dataset.index);
    const cols = searchColumns(results);
    const rows = Math.max(1, Math.floor(results.parentElement.clientHeight / Math.max(1, card.offsetHeight)) - 1);
    const moves = {
        ArrowRight: index + 1, ArrowLeft: index - 1, ArrowDown: index + cols, ArrowUp: index - cols,
        Home: 0, End: results.children.length - 1, PageDown: index + cols * rows, PageUp: index - cols * rows,
    };
    if (e.key === 'ArrowUp' && index < cols) {
        e.preventDefault();
        document.getElementById(searchState.mode === 'similar' ? 'search-results' : 'search-input').focus();
        return;
    }
    if (e.key in moves) {
        e.preventDefault();
        searchSetFocus(moves[e.key], true);
    } else if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        searchOpen(index);
    }
}

// s: het zoekvenster openen; v in de viewer: vergelijkbare foto's
document.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || isTypingTarget(e.target)) return;
    if (e.key === 'v' && typeof isViewerOpen === 'function' && isViewerOpen() && topOverlay()
        && topOverlay().id === 'modal-viewer') {
        const file = viewerFile();
        if (file && (file.type === 'photo' || file.type === 'video')) {
            e.preventDefault();
            openSimilar(file);
        }
        return;
    }
    if (e.key === 's' && !anyOverlayOpen() && typeof shortcutGPending !== 'undefined' && !shortcutGPending) {
        const themeMenu = document.getElementById('theme-dropdown');
        if (themeMenu && themeMenu.classList.contains('open')) return;
        e.preventDefault();
        openSearch('');
    }
});

// De knop 'Vergelijkbaar' in de viewer: alleen voor foto's en video's
function searchViewerChanged(file) {
    const button = document.getElementById('btn-similar');
    if (button) button.hidden = !file || !(file.type === 'photo' || file.type === 'video');
}

function similarFromViewer() {
    openSimilar(viewerFile());
}

if (typeof addShortcutGroup === 'function') {
    addShortcutGroup('Zoeken', [
        [['s'], 'Zoekvenster openen (Slim zoeken of Bestandsnaam)'],
        [['Enter'], 'In het zoekveld bovenaan: zoeken in de hele bibliotheek'],
        [['↓'], 'Van het zoekveld naar de resultaten; pijltjes lopen door het raster'],
        [['v'], "In de viewer: foto's die op deze foto lijken"],
    ]);
}

// ==================== INSTELLINGEN ====================

let searchSettingsAsked = false;

registerSettingsSection({
    id: 'zoeken',
    title: 'Slim zoeken',
    icon: 'fa-wand-magic-sparkles',
    order: 70,
    description: 'Zoek foto’s op wat erop staat, zoals “hond op het strand”. Het zoekmodel draait op deze Mac: '
        + 'je foto’s en zoekopdrachten gaan nergens heen.',
    render(body) {
        const status = searchState.status;
        if (!searchSettingsAsked) {
            searchSettingsAsked = true;
            searchFetchStatus().then(() => settingsRefreshSection('zoeken')).finally(() => { searchSettingsAsked = false; });
        }
        if (!status) {
            body.append(settingsRow('Status', 'Laden…'));
            return;
        }
        const model = status.model;
        const download = status.download && JOB_ACTIVE.has(status.download.status) ? status.download : null;
        const job = status.job && JOB_ACTIVE.has(status.job.status) ? status.job : null;
        body.append(settingsRow('Model', `${model.label} · ${model.licence}`));
        if (!model.installed) {
            body.append(
                settingsRow('Download', 'Het model wordt alleen gedownload als je hieronder op installeren klikt.'),
                settingsRow('Grootte', `ongeveer ${formatBytes(model.size)} (eenmalig)`),
                settingsRow('Bron', createEl('span', { class: 'settings-sources' },
                    ...model.sources.map(url => createEl('span', { class: 'path-label', text: url })))),
                settingsRow('Map', createEl('span', { class: 'path-label', text: model.folder, title: model.folder })),
                settingsRow('Status', download ? (download.message || 'Bezig met downloaden…') : 'Niet geïnstalleerd'),
                createEl('div', { class: 'settings-actions' },
                    createEl('button', {
                        type: 'button', class: 'btn btn-primary', id: 'settings-search-install', disabled: !!download,
                        onClick: () => searchInstallModel(model),
                    }, createEl('i', { class: 'fas fa-download', 'aria-hidden': 'true' }),
                    download ? ' Bezig…' : ` Zoekmodel installeren (~${formatBytes(model.size)})`)));
            return;
        }
        const recognised = `${formatNumberNl(status.embedded)} van ${formatNumberNl(status.total)} foto’s en video’s`
            + (status.undecodable ? ` (${formatNumberNl(status.undecodable)} onleesbaar)` : '');
        body.append(
            settingsRow('Status', 'Geïnstalleerd'),
            settingsRow('Herkend', recognised),
            settingsRow('Nog te doen', job ? (job.message || 'Bezig…')
                : status.pending ? `${formatNumberNl(status.pending)} foto\u2019s en video\u2019s` : 'Niets: alles is bij'));
        if (status.per_second) {
            body.append(settingsRow('Snelheid', `${formatNumberNl(status.per_second)} per seconde`
                + (status.device === 'gpu' ? ' (grafische chip)' : status.device === 'cpu' ? ' (processor)' : '')));
        }
        body.append(createEl('div', { class: 'settings-actions' },
            createEl('button', {
                type: 'button', class: 'btn btn-secondary', id: 'settings-search-update', disabled: !!job || !status.pending,
                title: 'Herkent nieuwe en gewijzigde foto’s; dat gebeurt ook vanzelf na het bijwerken van de bibliotheek',
                onClick: async (e) => {
                    e.currentTarget.disabled = true;
                    try { await jobsStart('semantic', {}); } catch (error) { toastFetchError('Foto’s herkennen', error); }
                    settingsRefreshSection('zoeken');
                },
            }, job ? 'Bezig…' : 'Nu bijwerken'),
            createEl('button', { type: 'button', class: 'btn btn-secondary', onClick: () => openSearch('') },
                createEl('i', { class: 'fas fa-magnifying-glass', 'aria-hidden': 'true' }), ' Zoeken (s)')));
    },
});

async function searchInstallModel(model) {
    const ok = await dvConfirm({
        title: 'Zoekmodel downloaden?', icon: 'fa-cloud-arrow-down', confirmLabel: 'Downloaden',
        body: [`Het zoekmodel (ongeveer ${formatBytes(model.size)}) wordt eenmalig gedownload van:`,
               createEl('ul', { class: 'dv-confirm-list' }, ...model.sources.map(url => createEl('li', { class: 'mono', text: url }))),
               createEl('p', {}, 'naar ', createEl('span', { class: 'mono', text: model.folder }), '.'), 'Daarna werkt Slim zoeken helemaal op deze Mac.'],
    });
    if (!ok) return;
    try {
        await jobsStart('model_download', {});
        showToast('Zoekmodel installeren', 'Gestart; de voortgang staat rechtsonder.', 'info');
    } catch (error) {
        toastFetchError('Zoekmodel installeren', error);
    }
    searchFetchStatus().then(() => settingsRefreshSection('zoeken'));
}

// Een taak van Slim zoeken veranderde: de stand opnieuw ophalen, hooguit eens per 2 seconden
// (de taakpaneel-poll meldt elke taak bij elke ronde)
let searchJobSeen = '';
let searchJobRefreshAt = 0;
if (typeof jobsOnChange === 'function') {
    jobsOnChange((job) => {
        if (job.type !== 'semantic' && job.type !== 'model_download') return;
        const seen = `${job.id}:${job.status}`;
        const now = Date.now();
        if (seen === searchJobSeen && now - searchJobRefreshAt < 2000) return;
        searchJobSeen = seen;
        searchJobRefreshAt = now;
        if (!settingsVisible() && !searchIsOpen()) return;
        searchFetchStatus().then(() => settingsRefreshSection('zoeken'));
    });
}
