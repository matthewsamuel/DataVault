// DataVault Dashboard - 'Welke is mooier?' (Shows)
// Note: API_BASE is defined in app.js
//
// Twee foto's naast elkaar; jij kiest de mooiste, zegt 'gelijk' of slaat het paar over. Elke foto
// heeft een score en een onzekerheid (Glicko-2, backend/elo.py): een nieuwe foto beweegt veel, een
// zekere weinig. De score is dezelfde schaal als de duimpjes in de slideshow (1000 om te beginnen).
// Een collectie (de hele bibliotheek, een show of een album) kies je bovenin; de ranglijst staat ernaast.
//
// Snel: er staan altijd een paar paren klaar (afbeeldingen al geladen en gedecodeerd). Een keuze
// wisselt meteen naar het volgende paar; het opslaan loopt erachteraan, in vaste volgorde, en telt
// dankzij een client_token nooit dubbel. Wie tijdens het opslaan z drukt, draait terug wat er net
// gekozen is (de keten wacht op elkaar).
//
// Toetsen (alleen zolang dit venster open is en je niet in een keuzelijst zit):
//   ← of 1   linker foto        → of 2   rechter foto       spatie   gelijk
//   s        overslaan          z        terugdraaien (vaker drukken: verder terug)   Esc   sluiten
// Een ingedrukt gehouden toets telt één keer, en een keuze binnen ELO_MIN_DWELL_MS nadat een paar
// verscheen telt niet (een dubbelklik of een toets die stuitert kiest zo nooit een paar dat je niet zag).
//
// Beweging: de winnaar licht op (groene ring, vinkje, score omhoog), het paar zweeft weg en het
// volgende komt omhoog, met de Web Animations API. Animaties blokkeren nooit: het nieuwe paar is
// meteen te kiezen, en een volgende keuze breekt een lopende animatie af. Bij 'beweging verminderen'
// (systeem of html[data-motion="reduce"]) wisselt alles zonder animatie.
//
// Andere collecties dan mappen melden zich met registerEloScopes(label, fn): fn() geeft
// [{value: 'album:7', text: 'Mooiste'}]; de server kent ze via elo.register_scope. Slimme albums
// doet dit bestand zelf.

const ELO_MIN_DWELL_MS = 250;       // zo lang na het verschijnen van een paar telt een keuze nog niet
const ELO_QUEUE_TARGET = 2;         // zoveel paren staan klaar achter het huidige
const ELO_FETCH_COUNT = 3;
const ELO_BOARD_LIMIT = 50;
const ELO_UNDO_MAX = 200;
const ELO_TOP_CHOICES = [10, 25, 50];
const ELO_GOALS = [10, 20, 50, 100];
const ELO_SCOPE_CHARS = 30;         // langere namen in de keuzelijst eindigen op '…'

const elo = {
    scope: '',          // pad van de collectie ('' = hele bibliotheek), of 'soort:waarde' (album)
    scopeTitle: 'Hele bibliotheek',
    tab: 'compare',     // 'compare' of 'board'
    pair: null,         // het paar in beeld ([a, b] zoals de server ze gaf); null zonder paar
    queue: [],          // klaarstaande paren (met hun geladen afbeeldingen)
    fetching: null,     // de lopende aanvraag voor nieuwe paren
    last: null,         // het laatst getoonde paar, zodat het niet meteen terugkomt
    notice: '',         // korte melding vooraan in de voortgangsregel
    noticeIcon: 'fa-rotate-left',
    stats: null,        // {photos, compared, comparisons, settled}
    progress: null,     // {session, today, streak, best_streak, goal, total}
    undo: [],           // ids van vergelijkingen in deze ronde, nieuwste laatst
    round: 0,           // vergeleken sinds het venster openging (in deze collectie)
    busy: false,        // terugdraaien loopt
    seq: 0,             // laat een oud antwoord nooit een nieuwer overschrijven
    chain: Promise.resolve(),   // opslaan en terugdraaien, één voor één
    pending: 0,         // keuzes die nog opgeslagen worden
    shownAt: 0,         // wanneer het paar in beeld verscheen (performance.now)
    albums: [],         // slimme albums voor de keuzelijst
    board: null,        // de laatst getoonde ranglijst
    boardYear: '',
    topN: 25,
};

const eloScopeProviders = [];   // [{label, list}] van registerEloScopes

function registerEloScopes(label, list) {
    eloScopeProviders.push({ label, list });
}

function eloIcon(name) {
    return createEl('i', { class: `fas ${name}`, 'aria-hidden': 'true' });
}

function eloMotionOK() {
    if (document.documentElement.dataset.motion === 'reduce') return false;
    return !(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

// Element.animate met de juiste voorzorg: geen animatie bij verminderde beweging of zonder API
function eloAnimate(el, keyframes, options) {
    if (!el || !eloMotionOK() || typeof el.animate !== 'function') return null;
    try { return el.animate(keyframes, { fill: 'both', ...options }); } catch (_) { return null; }
}

const ELO_SPRING = 'cubic-bezier(0.22, 1.2, 0.36, 1)';     // licht doorverend
const ELO_GLIDE = 'cubic-bezier(0.2, 0.8, 0.2, 1)';        // = --ease-out

function eloToken() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function eloBuild() {
    const scopeSelect = createEl('select', {
        id: 'elo-scope', class: 'select select-sm elo-scope', 'aria-label': 'Collectie',
        onChange: (e) => eloSetScope(e.target.value),
    });
    const tabs = createEl('div', { class: 'segmented elo-tabs', role: 'group', 'aria-label': 'Weergave' },
        createEl('button', { type: 'button', id: 'elo-tab-compare', dataset: { tab: 'compare' }, 'aria-pressed': 'true',
            onClick: () => eloShowTab('compare') }, eloIcon('fa-scale-balanced'), ' Vergelijken'),
        createEl('button', { type: 'button', id: 'elo-tab-board', dataset: { tab: 'board' }, 'aria-pressed': 'false',
            onClick: () => eloShowTab('board') }, eloIcon('fa-ranking-star'), ' Ranglijst'));
    const close = createEl('button', {
        type: 'button', class: 'modal-close', 'aria-label': 'Sluiten', title: 'Sluiten (Esc)', onClick: closeElo,
    }, '×');

    const goal = createEl('button', {
        type: 'button', id: 'elo-goal', class: 'elo-goal', onClick: eloCycleGoal,
        title: 'Sessiedoel (klik om te wijzigen)', 'aria-label': 'Sessiedoel',
    },
        eloRing(),
        createEl('span', { class: 'elo-goal-text' },
            createEl('span', { id: 'elo-goal-count', class: 'elo-goal-count tabular', text: '0' }),
            createEl('span', { id: 'elo-goal-of', class: 'elo-goal-of', text: 'van 20' })));

    const overlay = createEl('div', {
        id: 'elo-overlay', class: 'dv-overlay elo-overlay', role: 'dialog', 'aria-modal': 'true',
        'aria-labelledby': 'elo-title', tabindex: '-1',
        onClick: (e) => { if (e.target === overlay) closeElo(); },
    },
        createEl('div', { class: 'dv-overlay-panel elo-panel' },
            createEl('div', { class: 'elo-header' },
                createEl('div', { class: 'elo-heading' },
                    createEl('h2', { id: 'elo-title', class: 'modal-title' }, eloIcon('fa-scale-balanced'), ' Welke is mooier?'),
                    createEl('p', { class: 'elo-subtitle',
                        text: 'Kies steeds de mooiste van twee. Hoe hoger de score, hoe vaker een foto in Shows komt.' })),
                createEl('div', { class: 'elo-controls' }, scopeSelect, tabs, close)),
            createEl('div', { id: 'elo-body', class: 'elo-body' }),
            createEl('div', { id: 'elo-footer', class: 'elo-footer' },
                createEl('div', { class: 'elo-meters' },
                    goal,
                    createEl('div', { id: 'elo-streak', class: 'elo-streak', hidden: true },
                        eloIcon('fa-fire'), createEl('span', { id: 'elo-streak-text' })),
                    createEl('div', { id: 'elo-settled', class: 'elo-settled', title: 'Hoe zeker de ranglijst van deze collectie is' },
                        createEl('div', { class: 'elo-settled-track', 'aria-hidden': 'true' },
                            createEl('div', { id: 'elo-settled-fill', class: 'elo-settled-fill' })),
                        createEl('span', { id: 'elo-settled-text', class: 'elo-settled-text', text: '0% zeker' })),
                    createEl('div', { id: 'elo-progress', class: 'elo-progress', 'aria-live': 'polite' })),
                createEl('div', { class: 'elo-actions' },
                    createEl('button', { type: 'button', id: 'elo-undo', class: 'btn btn-secondary btn-sm', disabled: true,
                        onClick: eloUndo, title: 'Laatste keuze terugdraaien (z)' },
                        eloIcon('fa-rotate-left'), ' Terugdraaien ', createEl('kbd', { text: 'z' })),
                    createEl('button', { type: 'button', id: 'elo-draw', class: 'btn btn-secondary btn-sm',
                        onClick: () => eloPick('draw', 'pointer'), title: 'Even mooi (spatie)' },
                        eloIcon('fa-equals'), ' Gelijk ', createEl('kbd', { text: 'spatie' })),
                    createEl('button', { type: 'button', id: 'elo-skip', class: 'btn btn-secondary btn-sm',
                        onClick: eloSkip, title: 'Dit paar overslaan (s)' },
                        eloIcon('fa-forward'), ' Overslaan ', createEl('kbd', { text: 's' }))))));
    overlay.addEventListener('keydown', eloKeydown);
    // De focusring alleen na Tab of Enter, niet na een klik (WebKit toont :focus-visible hier niet
    // betrouwbaar, omdat de focus met de hand verplaatst wordt)
    overlay.addEventListener('keydown', (e) => {
        if (e.key === 'Tab' || e.key === 'Enter') overlay.classList.add('is-keyboard');
    }, true);
    overlay.addEventListener('pointerdown', () => overlay.classList.remove('is-keyboard'), true);
    document.body.appendChild(overlay);
    return overlay;
}

// Een ring die volloopt naar het sessiedoel (SVG, de omtrek via pathLength 100)
function eloRing() {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 36 36');
    svg.setAttribute('class', 'elo-ring');
    svg.setAttribute('aria-hidden', 'true');
    for (const cls of ['elo-ring-track', 'elo-ring-fill']) {
        const c = document.createElementNS(ns, 'circle');
        c.setAttribute('cx', '18');
        c.setAttribute('cy', '18');
        c.setAttribute('r', '15');
        c.setAttribute('pathLength', '100');
        c.setAttribute('class', cls);
        if (cls === 'elo-ring-fill') c.id = 'elo-ring-fill';
        svg.appendChild(c);
    }
    return svg;
}

// ==================== COLLECTIES ====================

// De collecties in de keuzelijst: de hele bibliotheek, elke show en elk slim album
function eloFillScopes() {
    const select = document.getElementById('elo-scope');
    const shows = (typeof _lastShowsData !== 'undefined' && _lastShowsData && _lastShowsData.shows) || [];
    const options = [eloOption('', 'Hele bibliotheek')];
    const showOptions = [...shows].sort((a, b) => a.title.localeCompare(b.title, 'nl')).map(show => eloOption(show.path, show.title));
    if (showOptions.length) options.push(createEl('optgroup', { label: 'Shows' }, ...showOptions));
    const known = new Set(['', ...shows.map(s => s.path)]);
    const groups = [];
    if (elo.albums.length) {
        groups.push({ label: 'Albums', items: elo.albums.map(a => ({ value: `album:${a.id}`, text: a.name })) });
    }
    eloScopeProviders.forEach(({ label, list }) => {
        let items = [];
        try { items = list() || []; } catch (_) { items = []; }
        if (items.length) groups.push({ label, items });
    });
    groups.forEach(({ label, items }) => {
        items.forEach(item => known.add(item.value));
        options.push(createEl('optgroup', { label }, ...items.map(item => eloOption(item.value, item.text))));
    });
    if (elo.scope && !known.has(elo.scope)) {
        options.push(eloOption(elo.scope, elo.scopeTitle));
    }
    select.replaceChildren(...options);
    select.value = elo.scope;
    eloScopeTitle();
}

async function eloLoadAlbums() {
    try {
        const data = await fetchJSON(`${API_BASE}/api/library/albums?with_hidden=false`);
        elo.albums = (data.albums || []).filter(a => !a.hidden);
        const select = document.getElementById('elo-scope');
        if (select && document.activeElement !== select) eloFillScopes();
    } catch (_) {
        // Zonder albums blijft de lijst bij de bibliotheek en de shows
    }
}

function eloOption(value, title) {
    const short = title.length > ELO_SCOPE_CHARS ? `${title.slice(0, ELO_SCOPE_CHARS - 1).trimEnd()}…` : title;
    return createEl('option', { value, text: short, title, dataset: { full: title } });
}

function eloOptionTitle(option) {
    return option ? (option.dataset.full || option.textContent) : '';
}

function eloScopeTitle() {
    const select = document.getElementById('elo-scope');
    if (select) select.title = eloOptionTitle(select.selectedOptions[0]);
}

function openElo(scope = '', title = '') {
    const overlay = document.getElementById('elo-overlay') || eloBuild();
    elo.scope = scope || '';
    elo.scopeTitle = title || 'Hele bibliotheek';
    eloResetRound();
    elo.last = null;
    eloFillScopes();
    eloLoadAlbums();
    // Geopend met het toetsenbord: dan meteen een zichtbare focusring
    const opener = document.activeElement;
    overlay.classList.toggle('is-keyboard', !!(opener && opener.matches && opener.matches(':focus-visible')));
    // Eerst de inhoud (het skelet), dan pas zichtbaar: zo onthoudt ui.js waar de focus vandaan kwam
    eloShowTab('compare');
    overlay.classList.add('active');
    eloAnimate(overlay.querySelector('.elo-panel'), [
        { opacity: 0, transform: 'translateY(12px) scale(0.985)' },
        { opacity: 1, transform: 'none' }], { duration: 280, easing: ELO_GLIDE, fill: 'none' });
    setTimeout(() => { if (!overlay.contains(document.activeElement) || document.activeElement === overlay) eloFocusInBody(overlay); }, 0);
}

function closeElo() {
    const overlay = document.getElementById('elo-overlay');
    if (!overlay) return;
    overlay.classList.remove('active');
    elo.seq++;
    eloDropQueue();
    // De scores in de slideshow komen vanzelf goed: Shows haalt ze bij het afspelen opnieuw op
}

registerOverlayCloser('elo-overlay', closeElo);

function eloResetRound() {
    elo.undo = [];
    elo.round = 0;
    elo.pair = null;
    elo.notice = '';
    eloDropQueue();
}

function eloDropQueue() {
    elo.queue = [];
    elo.fetching = null;
}

function eloSetScope(value) {
    elo.scope = value;
    const select = document.getElementById('elo-scope');
    const option = select && select.selectedOptions[0];
    elo.scopeTitle = option ? eloOptionTitle(option) : 'Hele bibliotheek';
    eloResetRound();
    elo.seq++;
    eloScopeTitle();
    eloUpdateActions();
    if (elo.tab === 'board') eloLoadBoard(); else eloLoadPair();
}

function eloShowTab(tab) {
    elo.tab = tab;
    document.querySelectorAll('#elo-overlay .elo-tabs button').forEach(btn => {
        const on = btn.dataset.tab === tab;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-pressed', String(on));
    });
    document.getElementById('elo-footer').hidden = tab !== 'compare';
    if (tab === 'board') {
        eloLoadBoard();
    } else if (elo.pair) {
        eloShowPair(elo.pair, 'none');
        eloFillQueue();
    } else {
        eloLoadPair();
    }
}

function eloIsOpen() {
    const overlay = document.getElementById('elo-overlay');
    return !!overlay && overlay.classList.contains('active');
}

// Een map gaat als folder=, een andere collectie ('album:7') als scope=
function eloScopeIsFolder() {
    return elo.scope.startsWith('/');
}

function eloScopeParam() {
    if (!elo.scope) return '';
    return `${eloScopeIsFolder() ? 'folder' : 'scope'}=${encodeURIComponent(elo.scope)}`;
}

function eloScopeBody() {
    if (!elo.scope) return {};
    return eloScopeIsFolder() ? { folder: elo.scope } : { scope: elo.scope };
}

function eloPost(url, body, method = 'POST') {
    return fetchJSON(`${API_BASE}${url}`, {
        method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
}

// ==================== FOCUS ====================
// Het venster tekent zijn inhoud steeds opnieuw. Stond de focus op een foto (of was hij
// weggevallen naar de pagina), dan komt hij terug op dezelfde kant, anders op het eerste wat
// je kunt bedienen; zo blijven de toetsen werken na Tab + Enter. Staat de focus op de
// keuzelijst of een knop bovenin, dan blijft hij daar.

function eloFocusState() {
    const overlay = document.getElementById('elo-overlay');
    if (!overlay || !overlay.classList.contains('active')) return null;
    const active = document.activeElement;
    const body = document.getElementById('elo-body');
    if (!active || active === document.body || active === overlay) return { side: null };
    if (body.contains(active)) {
        const choice = active.closest('.elo-choice');
        return { side: choice ? choice.dataset.side : null };
    }
    return null;
}

function eloFocusInBody(overlay, side = null) {
    const live = '#elo-body .elo-stage:not(.is-leaving)';
    // Op de ranglijst de lijst zelf (scrollen met de pijltjes), niet een keuzelijst die dan oplicht
    // In volgorde van voorkeur (één selectorlijst zou de eerste in de pagina nemen: een vertrekkende foto)
    const wanted = [side !== null ? `${live} .elo-choice[data-side="${side}"]` : null, `${live} .elo-choice:not(.is-loading)`,
        '#elo-body .elo-board-list', '#elo-body .empty-state button', '#elo-body :not(.is-leaving) > button'];
    const target = wanted.filter(Boolean).map(sel => document.querySelector(sel)).find(Boolean);
    (target || overlay).focus({ preventScroll: true });
}

function eloRestoreFocus(state) {
    const overlay = document.getElementById('elo-overlay');
    if (!state || !overlay || !overlay.classList.contains('active')) return;
    const active = document.activeElement;
    const body = document.getElementById('elo-body');
    // Intussen zelf ergens anders heen gegaan (de keuzelijst, een knop): daar laten
    if (active && active !== document.body && active !== overlay && !body.contains(active)) return;
    // Nog op iets levends in de inhoud (niet op een vertrekkende foto): ook laten
    if (active && body.contains(active) && active.isConnected && !active.closest('.is-leaving')
        && !active.closest('.elo-choice')) return;
    eloFocusInBody(overlay, state.side);
}

// De inhoud vervangen zonder dat de focus verloren gaat
function eloSetBody(...nodes) {
    const state = eloFocusState();
    document.getElementById('elo-body').replaceChildren(...nodes);
    eloRestoreFocus(state);
}

// ==================== PAREN LADEN ====================

function eloSkeleton() {
    const frame = () => createEl('div', { class: 'elo-choice is-loading', 'aria-hidden': 'true' },
        createEl('div', { class: 'elo-frame skeleton' }),
        createEl('div', { class: 'elo-meta' }, createEl('div', { class: 'skeleton skeleton-line' })));
    return createEl('div', { class: 'elo-stage', role: 'status', 'aria-label': 'Laden…' }, frame(), frame());
}

// De afbeeldingen van een paar laden en decoderen voordat het in beeld komt; een formaat dat de
// browser niet kan tonen valt terug op de miniatuur
function eloPreload(pair) {
    pair._imgs = pair.map(photo => {
        const img = new Image();
        img.className = 'elo-img';
        img.alt = photo.name;
        img.draggable = false;
        img.decoding = 'async';
        const loaded = () => img.classList.add('is-loaded');
        img.addEventListener('load', loaded);
        img.addEventListener('error', () => {
            if (!img.dataset.fallback) { img.dataset.fallback = '1'; img.src = vaultFileUrl('thumb', photo.path); }
        });
        img.src = vaultFileUrl('file', photo.path);
        if (img.decode) img.decode().then(loaded, () => {});
        return img;
    });
    return pair;
}

function eloPaths(pair) {
    return pair ? pair.map(p => p.path) : [];
}

// Paren bijhalen tot er ELO_QUEUE_TARGET klaarstaan (één aanvraag tegelijk)
function eloFillQueue() {
    if (elo.fetching || elo.queue.length >= ELO_QUEUE_TARGET || elo.tab !== 'compare') return elo.fetching;
    const seq = elo.seq;
    const exclude = [...eloPaths(elo.pair), ...elo.queue.flatMap(eloPaths)];
    const params = [eloScopeParam(), `count=${ELO_FETCH_COUNT - Math.min(1, elo.queue.length)}`,
        ...eloPaths(elo.pair || elo.last).map(p => `prev=${encodeURIComponent(p)}`),
        ...exclude.map(p => `exclude=${encodeURIComponent(p)}`)].filter(Boolean);
    const request = fetchJSON(`${API_BASE}/api/shows/compare/pair?${params.join('&')}`).then(data => {
        if (seq !== elo.seq) return null;
        elo.stats = data.stats;
        elo.progress = data.progress;
        elo.scopeTitle = data.scope.title;
        const busy = new Set([...eloPaths(elo.pair), ...elo.queue.flatMap(eloPaths)]);
        (data.pairs || []).forEach(pair => {
            if (pair.some(p => busy.has(p.path))) return;
            pair.forEach(p => busy.add(p.path));
            elo.queue.push(eloPreload(pair));
        });
        eloUpdateProgress();
        return data;
    }).finally(() => { if (elo.fetching === request) elo.fetching = null; });
    elo.fetching = request;
    return request;
}

// Het eerste paar van een ronde (of na een fout): skelet, ophalen, tonen
async function eloLoadPair() {
    const seq = ++elo.seq;
    elo.pair = null;
    eloDropQueue();
    eloSetBody(eloSkeleton());
    eloUpdateActions();
    try {
        const data = await eloFillQueue();
        if (seq !== elo.seq || elo.tab !== 'compare' || !data) return;
        if (elo.queue.length) {
            eloShowPair(elo.queue.shift(), 'none');
            eloFillQueue();
        } else {
            eloRenderEmpty(data.reason);
        }
    } catch (err) {
        if (seq !== elo.seq || elo.tab !== 'compare') return;
        elo.pair = null;
        eloSetBody(errorState('Geen foto\'s geladen', err, () => eloLoadPair()));
    }
    eloUpdateProgress();
    eloUpdateActions();
}

// Het volgende paar uit de wachtrij, of (is die leeg) zodra het binnen is
async function eloAdvance(direction) {
    if (elo.queue.length) {
        eloShowPair(elo.queue.shift(), direction);
        eloFillQueue();
        return;
    }
    const seq = elo.seq;
    elo.pair = null;
    eloUpdateActions();
    try {
        let data = await (elo.fetching || eloFillQueue());
        if (seq !== elo.seq || elo.tab !== 'compare') return;
        if (!elo.queue.length && data) data = await eloFillQueue();
        if (seq !== elo.seq || elo.tab !== 'compare') return;
        if (elo.queue.length) {
            eloShowPair(elo.queue.shift(), direction);
            eloFillQueue();
        } else {
            eloRenderEmpty(data ? data.reason : 'no_pair');
        }
    } catch (err) {
        if (seq !== elo.seq || elo.tab !== 'compare') return;
        eloSetBody(errorState('Geen foto\'s geladen', err, () => eloLoadPair()));
    }
    eloUpdateActions();
}

function eloRenderEmpty(reason) {
    const few = reason === 'too_few';
    const toLibrary = { label: 'Hele bibliotheek', onClick: () => { document.getElementById('elo-scope').value = ''; eloSetScope(''); } };
    const toBoard = { label: 'Ranglijst bekijken', onClick: () => eloShowTab('board') };
    const state = emptyState({
        icon: few ? 'fa-images' : 'fa-check',
        title: few ? 'Te weinig foto\'s in deze collectie' : 'Geen nieuw paar',
        text: few ? 'Er zijn minstens twee foto\'s nodig. Kies een andere collectie of de hele bibliotheek.'
            : `Alle foto's hier zijn net met elkaar vergeleken. ${elo.scope ? 'Kies de hele bibliotheek' : 'Kies een andere collectie'}, of bekijk de ranglijst.`,
        action: elo.scope ? toLibrary : toBoard,
    });
    if (!few && elo.scope) {
        const first = state.querySelector('.empty-state-action');
        const second = createEl('button', { type: 'button', class: 'btn btn-ghost btn-small empty-state-action',
            onClick: toBoard.onClick, text: toBoard.label });
        const actions = createEl('div', { class: 'elo-empty-actions' });
        first.replaceWith(actions);
        actions.append(first, second);
    }
    elo.pair = null;
    eloSetBody(state);
    eloUpdateActions();
}

// ==================== HET PAAR IN BEELD ====================

function eloChoice(photo, side, img) {
    const keys = side === 0 ? ['←', '1'] : ['→', '2'];
    const games = photo.games === 1 ? '1 vergelijking' : `${formatNumberNl(photo.games)} vergelijkingen`;
    const sure = photo.games && photo.rd ? `± ${formatNumberNl(Math.round(2 * photo.rd))}` : '';
    return createEl('button', {
        type: 'button', class: 'elo-choice', dataset: { side: String(side) },
        'aria-label': `${side === 0 ? 'Links' : 'Rechts'} is mooier: ${photo.name}`,
        title: `${photo.name} (${keys.join(' of ')})`,
        onClick: (e) => eloPick(side, e.detail === 0 ? 'key' : 'pointer'),
    },
        createEl('div', { class: 'elo-frame' }, img,
            createEl('span', { class: 'elo-delta', 'aria-hidden': 'true' }),
            createEl('span', { class: 'elo-check', 'aria-hidden': 'true' }, eloIcon('fa-check'))),
        createEl('div', { class: 'elo-meta' },
            createEl('span', { class: 'elo-keys', 'aria-hidden': 'true' }, ...keys.map(k => createEl('kbd', { text: k }))),
            createEl('span', { class: 'elo-name', text: photo.name, title: photo.path }),
            photo.games
                ? createEl('span', { class: 'elo-score', title: `Score, met de marge waarbinnen hij voor 95% zeker ligt · ${games}` },
                    createEl('span', { class: 'tabular elo-score-value', text: formatNumberNl(photo.rating) }),
                    createEl('span', { class: 'tabular elo-score-band', text: sure }))
                : createEl('span', { class: 'elo-new-chip', title: 'Nog nooit vergeleken: deze foto beweegt nog veel', text: 'Nieuw' })));
}

function eloStage(pair) {
    const imgs = pair._imgs || eloPreload(pair)._imgs;
    return createEl('div', { class: 'elo-stage' },
        eloChoice(pair[0], 0, imgs[0]),
        createEl('span', { class: 'elo-vs', 'aria-hidden': 'true', text: 'of' }),
        eloChoice(pair[1], 1, imgs[1]));
}

// Een paar in beeld zetten. direction: 'next' (na een keuze), 'skip', 'back' (terugdraaien) of
// 'none'. Het oude paar blijft even als vertrekkend plaatje staan (niet klikbaar, niet voor de
// schermlezer) terwijl het nieuwe binnenkomt; dat nieuwe is meteen te kiezen.
function eloShowPair(pair, direction = 'next') {
    const body = document.getElementById('elo-body');
    const state = eloFocusState();
    const old = body.querySelector('.elo-stage:not(.is-leaving)');
    body.querySelectorAll('.elo-stage.is-leaving').forEach(el => el.remove());
    const stage = eloStage(pair);
    elo.pair = pair;
    elo.last = pair;
    elo.shownAt = performance.now();
    const leaving = old && !old.querySelector('.is-loading') && direction !== 'none' && eloMotionOK();
    if (leaving) {
        old.classList.add('is-leaving');
        old.setAttribute('aria-hidden', 'true');
        old.querySelectorAll('button').forEach(b => { b.tabIndex = -1; });
        body.querySelectorAll(':scope > :not(.elo-stage)').forEach(el => el.remove());
        body.appendChild(stage);
        eloLeave(old, direction);
    } else {
        body.replaceChildren(stage);
    }
    eloEnter(stage, direction);
    eloRestoreFocus(state);
    eloUpdateProgress();
    eloUpdateActions();
    return old;
}

// Het nieuwe paar komt binnen: van onderen (na een keuze), van opzij (overslaan, terugdraaien)
function eloEnter(stage, direction) {
    if (direction === 'none') {
        eloAnimate(stage, [{ opacity: 0 }, { opacity: 1 }], { duration: 220, easing: ELO_GLIDE, fill: 'none' });
        return;
    }
    const from = { next: 'translateY(28px) scale(0.97)', skip: 'translateX(48px)', back: 'translateX(-48px)' }[direction]
        || 'translateY(16px)';
    stage.querySelectorAll('.elo-choice').forEach((choice, i) => {
        eloAnimate(choice, [
            { opacity: 0, transform: from },
            { opacity: 1, transform: 'none' }],
            { duration: 420, delay: 110 + i * 60, easing: ELO_SPRING, fill: 'backwards' });
    });
    eloAnimate(stage.querySelector('.elo-vs'), [
        { opacity: 0, transform: 'translate(-50%, -50%) scale(0.4) rotate(-90deg)' },
        { opacity: 1, transform: 'translate(-50%, -50%) scale(1) rotate(0deg)' }],
        { duration: 420, delay: 150, easing: ELO_SPRING, fill: 'backwards' });
}

// Het oude paar gaat weg: de winnaar zweeft omhoog, de ander zakt weg (bij gelijk allebei omhoog)
function eloLeave(old, direction) {
    const animations = [];
    old.querySelectorAll('.elo-choice').forEach((choice, i) => {
        let frames;
        if (direction === 'skip') {
            frames = [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: `translateX(${i ? 40 : -40}px) rotate(${i ? 2 : -2}deg)` }];
        } else if (direction === 'back') {
            frames = [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateX(56px)' }];
        } else if (choice.classList.contains('is-chosen') || choice.classList.contains('is-draw')) {
            frames = [
                { opacity: 1, transform: 'none', offset: 0 },
                { opacity: 1, transform: 'translateY(-6px) scale(1.03)', offset: 0.4 },
                { opacity: 0, transform: 'translateY(-32px) scale(0.96)', offset: 1 }];
        } else {
            frames = [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(20px) scale(0.94)' }];
        }
        const a = eloAnimate(choice, frames, { duration: direction === 'next' ? 300 : 240, easing: ELO_GLIDE });
        if (a) animations.push(a);
    });
    const vs = eloAnimate(old.querySelector('.elo-vs'), [{ opacity: 1 }, { opacity: 0 }], { duration: 160 });
    if (vs) animations.push(vs);
    if (!animations.length) { old.remove(); return; }
    Promise.all(animations.map(a => a.finished.catch(() => {}))).then(() => old.remove());
}

// Na een keuze: de groene ring, het vinkje (of bij gelijk een '=' op allebei)
function eloMarkChoice(stage, outcome) {
    if (!stage) return;
    stage.querySelectorAll('.elo-choice').forEach((el, i) => {
        if (outcome === 'draw') el.classList.add('is-draw');
        else el.classList.add(i === outcome ? 'is-chosen' : 'is-passed');
    });
    const marks = outcome === 'draw' ? [...stage.querySelectorAll('.elo-check')]
        : [stage.querySelector(`.elo-choice[data-side="${outcome}"] .elo-check`)];
    if (outcome === 'draw') marks.forEach(m => m.replaceChildren(eloIcon('fa-equals')));
    marks.forEach(mark => eloAnimate(mark, [
        { transform: 'scale(0.2) rotate(-30deg)', opacity: 0 },
        { transform: 'scale(1.15) rotate(0deg)', opacity: 1, offset: 0.6 },
        { transform: 'scale(1)', opacity: 1 }], { duration: 320, easing: ELO_SPRING }));
}

// +N/−N op de foto's zodra de server antwoordt (ook op het vertrekkende plaatje)
function eloShowDeltas(stage, result) {
    if (!stage || !stage.isConnected) return;
    const choices = stage.querySelectorAll('.elo-choice');
    [result.a, result.b].forEach((change, i) => {
        const delta = choices[i] && choices[i].querySelector('.elo-delta');
        if (!delta) return;
        const n = change.rating - change.before;
        delta.textContent = eloSigned(n);
        delta.classList.add(n > 0 ? 'is-up' : n < 0 ? 'is-down' : 'is-even');
        eloAnimate(delta, [
            { opacity: 0, transform: 'translateY(10px) scale(0.8)' },
            { opacity: 1, transform: 'translateY(0) scale(1)' }], { duration: 260, easing: ELO_SPRING });
    });
}

function eloSigned(n) {
    return n > 0 ? `+${formatNumberNl(n)}` : n < 0 ? `−${formatNumberNl(-n)}` : '±0';
}

// ==================== KIEZEN, GELIJK, OVERSLAAN, TERUGDRAAIEN ====================

// side: 0 (links), 1 (rechts) of 'draw'; how: 'key' of 'pointer'
function eloPick(side, how = 'key') {
    if (!elo.pair || elo.busy || elo.tab !== 'compare') return;
    // Te snel na het verschijnen van dit paar: een dubbelklik of een stuiterende toets
    if (performance.now() - elo.shownAt < ELO_MIN_DWELL_MS) return;
    const pair = elo.pair;
    const [a, b] = pair;
    const winner = side === 'draw' ? 'draw' : side === 0 ? 'a' : 'b';
    const stage = document.querySelector('#elo-body .elo-stage:not(.is-leaving)');
    eloMarkChoice(stage, side);
    if (elo.notice) elo.notice = '';
    elo.round++;
    elo.pending++;
    const token = eloToken();
    const scopeBody = eloScopeBody();
    const seq = elo.seq;
    eloAdvance('next');
    elo.chain = elo.chain.then(async () => {
        try {
            const result = await eloPost('/api/shows/compare', { a: a.path, b: b.path, winner, client_token: token, ...scopeBody });
            if (seq !== elo.seq && !eloIsOpen()) return;
            if (seq === elo.seq) {
                if (elo.undo[elo.undo.length - 1] !== result.id) elo.undo.push(result.id);
                if (elo.undo.length > ELO_UNDO_MAX) elo.undo.shift();
                if (result.stats) elo.stats = result.stats;
            }
            const before = elo.progress ? elo.progress.session : 0;
            if (result.progress) elo.progress = result.progress;
            eloShowDeltas(stage, result);
            eloCelebrateGoal(before);
        } catch (err) {
            // Niet bewaard: dit paar komt meteen terug, met een melding
            elo.round = Math.max(0, elo.round - 1);
            toastFetchError('Keuze niet opgeslagen', err);
            if (seq === elo.seq && eloIsOpen() && elo.tab === 'compare') {
                if (elo.pair) elo.queue.unshift(elo.pair);
                eloShowPair(pair, 'back');
            }
        } finally {
            elo.pending--;
            eloUpdateProgress();
            eloUpdateActions();
        }
    });
    eloUpdateProgress();
}

function eloSkip() {
    if (!elo.pair || elo.busy || elo.tab !== 'compare') return;
    const [a, b] = elo.pair;
    elo.notice = '';
    eloPost('/api/shows/compare/skip', { a: a.path, b: b.path, ...eloScopeBody() }).catch(() => {});
    eloAdvance('skip');
}

async function eloUndo() {
    if (elo.busy || elo.tab !== 'compare' || (!elo.undo.length && !elo.pending)) return;
    elo.busy = true;
    eloUpdateActions();
    let id = null;
    try {
        await elo.chain;            // eerst wat nog opgeslagen wordt
        if (!elo.undo.length) return;
        id = elo.undo[elo.undo.length - 1];
        const result = await eloPost('/api/shows/compare/undo', { id });
        elo.undo.pop();
        elo.round = Math.max(0, elo.round - 1);
        if (result.stats) elo.stats = result.stats;
        if (result.progress) elo.progress = result.progress;
        // Hetzelfde paar weer in beeld, met de scores van vóór die keuze; het paar dat in beeld
        // stond gaat vooraan in de wachtrij
        const name = p => p.path.slice(p.path.lastIndexOf('/') + 1);
        const pair = [result.a, result.b].map(p => ({ ...p, name: name(p), votes: 0 }));
        const back = new Set(eloPaths(pair));
        elo.queue = elo.queue.filter(q => !q.some(p => back.has(p.path)));
        if (elo.pair && !elo.pair.some(p => back.has(p.path))) elo.queue.unshift(elo.pair);
        elo.notice = 'Teruggedraaid, kies opnieuw';
        elo.noticeIcon = 'fa-rotate-left';
        eloShowPair(eloPreload(pair), 'back');
        elo.shownAt = 0;            // een teruggedraaid paar ken je al: meteen te kiezen
    } catch (err) {
        // Kan deze vergelijking echt niet meer terug (al teruggedraaid, of een foto veranderde
        // sindsdien), dan gaat hij van de stapel; bij een netwerk- of serverfout blijft hij,
        // zodat z het straks nog eens probeert
        if (id !== null && err && [400, 404, 409].includes(err.status)) elo.undo.pop();
        toastFetchError('Terugdraaien lukte niet', err);
    } finally {
        elo.busy = false;
        eloUpdateActions();
        eloUpdateProgress();
    }
}

function eloKeydown(e) {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    if (isTypingTarget(e.target) || elo.tab !== 'compare') return;
    // Spatie en Enter op een knop (Sluiten, Terugdraaien, de tabbladen) doen wat die knop doet
    const onControl = e.target.closest && e.target.closest('button, a[href], summary') && !e.target.closest('.elo-choice');
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    const onChoice = e.target.closest && e.target.closest('.elo-choice');
    // Een ingedrukt gehouden toets herhaalt niet: anders kies je een paar dat je nog niet zag
    if (e.repeat && (['ArrowLeft', 'ArrowRight', '1', '2', ' ', 's', 'z'].includes(key) || (key === 'Enter' && onChoice))) {
        if (!(onControl && key === ' ')) e.preventDefault();
        return;
    }
    if (key === 'ArrowLeft' || key === '1') { e.preventDefault(); eloPick(0, 'key'); }
    else if (key === 'ArrowRight' || key === '2') { e.preventDefault(); eloPick(1, 'key'); }
    else if (key === ' ' && !onControl) { e.preventDefault(); eloPick('draw', 'key'); }
    else if (key === 's') { e.preventDefault(); eloSkip(); }
    else if (key === 'z') { e.preventDefault(); eloUndo(); }
}

// ==================== VOORTGANG ====================

function eloUpdateProgress() {
    const box = document.getElementById('elo-progress');
    if (!box) return;
    const parts = [];
    if (elo.stats) {
        const photos = elo.stats.photos === 1 ? 'foto' : 'foto\'s';
        parts.push(`${formatNumberNl(elo.stats.compared)} van ${formatNumberNl(elo.stats.photos)} ${photos} beoordeeld`);
    }
    if (elo.round) parts.push(`${formatNumberNl(elo.round)} in deze ronde`);
    const notice = elo.notice ? createEl('span', { class: 'elo-notice' },
        eloIcon(elo.noticeIcon), ` ${elo.notice}`) : null;
    box.replaceChildren(...[notice, notice ? ' · ' : null,
        createEl('span', { class: 'elo-scope-name', text: elo.scopeTitle }),
        parts.length ? createEl('span', { text: ` · ${parts.join(' · ')}` }) : null].filter(Boolean));
    box.title = box.textContent;
    eloUpdateMeters();
}

function eloUpdateMeters() {
    const p = elo.progress || { session: 0, goal: 20, streak: 0, best_streak: 0 };
    const goal = p.goal || 20;
    const session = Math.max(p.session || 0, 0);
    const fill = document.getElementById('elo-ring-fill');
    if (fill) fill.style.strokeDashoffset = String(100 - Math.min(1, session / goal) * 100);
    const count = document.getElementById('elo-goal-count');
    if (count) count.textContent = formatNumberNl(session);
    const of = document.getElementById('elo-goal-of');
    if (of) of.textContent = `van ${formatNumberNl(goal)}`;
    const button = document.getElementById('elo-goal');
    if (button) {
        button.classList.toggle('is-done', session >= goal);
        const label = `Sessiedoel: ${session} van ${goal} vergelijkingen. Klik om het doel te wijzigen.`;
        button.setAttribute('aria-label', label);
        button.title = label;
    }
    const streak = document.getElementById('elo-streak');
    if (streak) {
        streak.hidden = !p.streak;
        document.getElementById('elo-streak-text').textContent = p.streak === 1 ? '1 dag' : `${formatNumberNl(p.streak || 0)} dagen op rij`;
        streak.title = `Dagen op rij met minstens één vergelijking. Langste reeks: ${formatNumberNl(p.best_streak || 0)}.`;
    }
    const settled = elo.stats ? Math.round((elo.stats.settled || 0) * 100) : 0;
    const settledFill = document.getElementById('elo-settled-fill');
    if (settledFill) settledFill.style.transform = `scaleX(${settled / 100})`;
    const settledText = document.getElementById('elo-settled-text');
    if (settledText) settledText.textContent = `${settled}% zeker`;
}

async function eloCycleGoal() {
    const current = (elo.progress && elo.progress.goal) || 20;
    const next = ELO_GOALS[(ELO_GOALS.indexOf(current) + 1) % ELO_GOALS.length];
    try {
        elo.progress = await eloPost('/api/shows/compare/goal', { goal: next }, 'PUT');
        eloUpdateMeters();
    } catch (err) {
        toastFetchError('Doel niet gewijzigd', err);
    }
}

// Het sessiedoel gehaald: de ring licht op en er spatten een paar groene vonkjes af
function eloCelebrateGoal(before) {
    const p = elo.progress;
    if (!p || before >= p.goal || p.session < p.goal) return;
    elo.notice = `Sessiedoel gehaald: ${formatNumberNl(p.goal)} vergelijkingen`;
    elo.noticeIcon = 'fa-trophy';
    const ring = document.getElementById('elo-goal');
    if (!ring) return;
    eloAnimate(ring, [{ transform: 'scale(1)' }, { transform: 'scale(1.18)' }, { transform: 'scale(1)' }],
        { duration: 520, easing: ELO_SPRING, fill: 'none' });
    if (!eloMotionOK()) return;
    for (let i = 0; i < 14; i++) {
        const spark = createEl('span', { class: 'elo-spark', 'aria-hidden': 'true' });
        ring.appendChild(spark);
        const angle = (i / 14) * Math.PI * 2;
        const dist = 26 + (i % 3) * 10;
        const a = eloAnimate(spark, [
            { transform: 'translate(-50%, -50%) scale(1)', opacity: 1 },
            { transform: `translate(calc(-50% + ${Math.round(Math.cos(angle) * dist)}px), calc(-50% + ${Math.round(Math.sin(angle) * dist)}px)) scale(0.2)`, opacity: 0 }],
            { duration: 640 + (i % 4) * 60, easing: ELO_GLIDE });
        if (a) a.finished.then(() => spark.remove(), () => spark.remove()); else spark.remove();
    }
}

// Terugdraaien kan alleen met iets om terug te draaien, Gelijk en Overslaan alleen met een paar
function eloUpdateActions() {
    const undo = document.getElementById('elo-undo');
    if (undo) undo.disabled = (elo.undo.length === 0 && elo.pending === 0) || elo.busy;
    for (const id of ['elo-skip', 'elo-draw']) {
        const btn = document.getElementById(id);
        if (btn) btn.disabled = !elo.pair || elo.busy;
    }
}

// ==================== RANGLIJST ====================

function eloBoardParams() {
    const params = [`limit=${ELO_BOARD_LIMIT}`];
    if (elo.scope) params.push(eloScopeParam());
    if (elo.boardYear) params.push(`year=${encodeURIComponent(elo.boardYear)}`);
    return params.join('&');
}

async function eloLoadBoard() {
    const seq = ++elo.seq;
    eloSetBody(skeletonRows(6));
    try {
        const data = await fetchJSON(`${API_BASE}/api/shows/leaderboard?${eloBoardParams()}`);
        if (seq !== elo.seq || elo.tab !== 'board') return;
        elo.board = data;
        eloRenderBoard(data);
    } catch (err) {
        if (seq !== elo.seq || elo.tab !== 'board') return;
        eloSetBody(errorState('Ranglijst laden mislukt', err, eloLoadBoard));
    }
}

function eloMovement(item) {
    if (item.new) return createEl('span', { class: 'elo-move is-new', title: 'Voor het eerst vergeleken in de laatste sessie', text: 'Nieuw' });
    if (!item.movement) return createEl('span', { class: 'elo-move is-same', title: 'Zelfde plek als voor de laatste sessie', 'aria-label': 'Geen verandering' }, '–');
    const up = item.movement > 0;
    const n = Math.abs(item.movement);
    return createEl('span', {
        class: `elo-move ${up ? 'is-up' : 'is-down'}`,
        title: `${n} ${n === 1 ? 'plek' : 'plekken'} ${up ? 'gestegen' : 'gedaald'} sinds de laatste sessie`,
        'aria-label': `${n} ${n === 1 ? 'plek' : 'plekken'} ${up ? 'omhoog' : 'omlaag'}`,
    }, eloIcon(up ? 'fa-arrow-up' : 'fa-arrow-down'), createEl('span', { class: 'tabular', text: String(n) }));
}

// De band: waar de score met 95% zekerheid ligt, op één schaal voor de hele lijst
function eloBand(item, min, max) {
    const track = createEl('span', { class: 'elo-band', 'aria-hidden': 'true' });
    if (item.low === null || item.low === undefined) {
        track.classList.add('is-unknown');
        return track;
    }
    const span = Math.max(1, max - min);
    const range = createEl('span', { class: 'elo-band-range' });
    range.style.left = `${((item.low - min) / span) * 100}%`;
    range.style.width = `${Math.max(2, ((item.high - item.low) / span) * 100)}%`;
    const dot = createEl('span', { class: 'elo-band-dot' });
    dot.style.left = `${((item.rating - min) / span) * 100}%`;
    track.append(range, dot);
    return track;
}

function eloSettledMeter(share, id) {
    const settled = Math.round((share || 0) * 100);
    const fill = createEl('div', { class: 'elo-settled-fill' });
    fill.style.transform = `scaleX(${settled / 100})`;
    return createEl('div', { class: 'elo-settled', id, title: 'Hoe zeker de ranglijst van deze collectie is' },
        createEl('div', { class: 'elo-settled-track', 'aria-hidden': 'true' }, fill),
        createEl('span', { class: 'elo-settled-text', text: `${settled}% zeker` }));
}

function eloBoardTools(data) {
    const years = createEl('select', {
        id: 'elo-board-year', class: 'select select-sm', 'aria-label': 'Jaar',
        onChange: (e) => { elo.boardYear = e.target.value; eloLoadBoard(); },
    }, createEl('option', { value: '', text: 'Alle jaren' }),
        ...(data.years || []).map(y => createEl('option', { value: String(y.year), text: `${y.year} (${formatNumberNl(y.count)})` })));
    years.value = elo.boardYear;
    if (years.value !== elo.boardYear) years.value = '';
    const top = createEl('select', {
        id: 'elo-board-top', class: 'select select-sm', 'aria-label': 'Hoeveel foto\'s',
        onChange: (e) => { elo.topN = Number(e.target.value); },
    }, ...ELO_TOP_CHOICES.map(n => createEl('option', { value: String(n), text: `Top ${n}` })));
    top.value = String(elo.topN);
    return createEl('div', { class: 'elo-board-tools' },
        createEl('div', { class: 'elo-board-heading' },
            createEl('h3', { class: 'elo-board-title', text: data.scope.title }),
            createEl('p', { class: 'elo-board-caption', text: eloBoardCaption(data) })),
        eloSettledMeter(data.settled, 'elo-board-settled'),
        createEl('div', { class: 'elo-board-actions' }, years, top,
            createEl('button', { type: 'button', id: 'elo-top-album', class: 'btn btn-secondary btn-sm', onClick: eloTopAlbum,
                disabled: !data.items.length, title: 'Een slim album met de beste foto\'s van deze lijst' },
                eloIcon('fa-images'), ' Als album'),
            createEl('button', { type: 'button', id: 'elo-top-export', class: 'btn btn-secondary btn-sm', onClick: eloTopExport,
                disabled: !data.items.length || !window.DVExport, title: 'De beste foto\'s kopiëren naar een map' },
                eloIcon('fa-file-export'), ' Exporteren')));
}

function eloBoardCaption(data) {
    const shown = data.items.length < data.total ? `De beste ${data.items.length} van ${formatNumberNl(data.total)}` : `${formatNumberNl(data.total)} beoordeeld`;
    const year = data.year ? ` uit ${data.year}` : '';
    const since = data.session_start
        ? ` · pijltjes: sinds de sessie van ${new Date(data.session_start * 1000).toLocaleDateString('nl-NL', { day: 'numeric', month: 'long' })}` : '';
    return `${shown}${year}${since}`;
}

function eloRenderBoard(data) {
    const tools = eloBoardTools(data);
    if (!data.items.length) {
        eloSetBody(createEl('div', { class: 'elo-board' }, tools, emptyState({
            icon: 'fa-ranking-star', title: data.year ? `Nog niets uit ${data.year}` : 'Nog geen ranglijst',
            text: data.year ? 'Kies een ander jaar of alle jaren.' : 'Vergelijk een paar foto\'s (of stem in de slideshow); de best beoordeelde verschijnen hier.',
            action: { label: 'Begin met vergelijken', onClick: () => eloShowTab('compare'), primary: true },
        })));
        return;
    }
    const banded = data.items.filter(i => i.low !== null && i.low !== undefined);
    const min = banded.length ? Math.min(...banded.map(i => i.low)) : 0;
    const max = banded.length ? Math.max(...banded.map(i => i.high)) : 1;
    const rows = data.items.map(item => {
        const thumb = createEl('img', { class: 'elo-board-img', src: vaultFileUrl('thumb', item.path), alt: '', loading: 'lazy' });
        const record = item.games
            ? [`${formatNumberNl(item.wins)} gewonnen`, item.draws ? `${formatNumberNl(item.draws)} gelijk` : null,
                `${formatNumberNl(item.losses)} verloren`].filter(Boolean).join(' · ')
            : 'Nog niet vergeleken';
        const votes = item.votes ? ` · ${item.votes === 1 ? '1 stem' : `${formatNumberNl(item.votes)} stemmen`}` : '';
        const sure = item.rd ? `Ligt voor 95% zeker tussen ${formatNumberNl(item.low)} en ${formatNumberNl(item.high)}` : 'Nog niet vergeleken: nog geen marge';
        return createEl('li', { class: `elo-board-row${item.rank <= 3 ? ' is-top' : ''}`, dataset: { rank: String(item.rank) } },
            createEl('span', { class: 'elo-rank tabular', text: String(item.rank) }),
            createEl('span', { class: 'elo-board-thumb' }, thumb),
            createEl('span', { class: 'elo-board-text' },
                createEl('span', { class: 'elo-board-name', text: item.name, title: item.path }),
                createEl('span', { class: 'elo-board-sub', text: `${item.folder} · ${record}${votes}` })),
            createEl('span', { class: 'elo-board-band', title: sure }, eloBand(item, min, max)),
            eloMovement(item),
            createEl('span', { class: 'elo-board-score tabular', 'aria-label': `Score ${item.rating}`, text: formatNumberNl(item.rating) }));
    });
    const list = createEl('ol', { class: 'elo-board-list', tabindex: '-1', 'aria-label': `Ranglijst ${data.scope.title}` }, ...rows);
    eloSetBody(createEl('div', { class: 'elo-board' }, tools, list));
    rows.slice(0, 14).forEach((row, i) => eloAnimate(row, [
        { opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }],
        { duration: 320, delay: i * 28, easing: ELO_GLIDE, fill: 'backwards' }));
}

async function eloTopAlbum() {
    if (!elo.board) return;
    const btn = document.getElementById('elo-top-album');
    if (btn) btn.disabled = true;
    try {
        const album = await eloPost('/api/shows/leaderboard/album', {
            n: elo.topN, ...eloScopeBody(), ...(elo.boardYear ? { year: Number(elo.boardYear) } : {}),
        });
        showToast('Album gemaakt', `'${album.name}' staat bij Albums. Het volgt de scores: wat later stijgt, komt erbij.`, 'success',
            window.albumsShow ? { label: 'Bekijken', onClick: () => { closeElo(); window.albumsShow(album.id, album.filters, album.sort); } } : null);
    } catch (err) {
        toastFetchError('Album niet gemaakt', err);
    } finally {
        if (btn && btn.isConnected) btn.disabled = false;
    }
}

function eloTopExport() {
    const items = elo.board ? elo.board.items.slice(0, elo.topN) : [];
    if (!items.length || !window.DVExport) return;
    closeElo();
    DVExport.openExport(items.map(i => ({ path: i.path, name: i.name, size: i.size || 0 })));
}

addShortcutGroup('Welke is mooier? (Shows)', [
    [['←', '1'], 'De linker foto is mooier'],
    [['→', '2'], 'De rechter foto is mooier'],
    [['spatie'], 'Even mooi (gelijk)'],
    [['s'], 'Dit paar overslaan'],
    [['z'], 'Terugdraaien (vaker: verder terug)'],
]);
