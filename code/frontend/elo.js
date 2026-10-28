// DataVault Dashboard - 'Welke is mooier?' (Shows)
// Note: API_BASE is defined in app.js
//
// Twee foto's naast elkaar; jij kiest de mooiste. Beide scores veranderen volgens de Elo-regel
// (backend/elo.py) op dezelfde schaal als de duimpjes in de slideshow: 1000 om te beginnen.
// Een collectie (een show, of de hele bibliotheek) kies je bovenin; de ranglijst staat ernaast.
//
// Toetsen (alleen zolang dit venster open is en je niet in de keuzelijst zit):
//   ← of 1   linker foto        → of 2   rechter foto
//   spatie of s   overslaan     z        laatste vergelijking terugdraaien     Esc   sluiten
// Een ingedrukt gehouden toets telt één keer, en zolang het volgende paar laadt telt geen
// keuze: anders zou je hetzelfde paar twee keer kiezen, of een paar dat je nog niet zag.
//
// Andere collecties dan mappen (albums) melden zich met registerEloScopes(label, fn): fn()
// geeft [{value: 'album:7', text: 'Mooiste'}]; de server kent ze via elo.register_scope.

const ELO_PICK_PAUSE_MS = 420;      // zo lang blijft de keuze zichtbaar voor het volgende paar komt
const ELO_BOARD_LIMIT = 50;

const elo = {
    scope: '',          // pad van de collectie ('' = hele bibliotheek), of 'soort:waarde' (album)
    scopeTitle: 'Hele bibliotheek',
    tab: 'compare',     // 'compare' of 'board'
    pair: null,         // [a, b] zoals de server ze gaf
    stats: null,
    undo: [],           // ids van vergelijkingen in deze ronde, nieuwste laatst
    round: 0,           // vergeleken sinds het venster openging
    busy: false,        // een keuze wordt bewaard of het volgende paar laadt
    seq: 0,             // laat een oud antwoord nooit een nieuwer overschrijven
};

const eloScopeProviders = [];   // [{label, list}] van registerEloScopes

function registerEloScopes(label, list) {
    eloScopeProviders.push({ label, list });
}

function eloIcon(name) {
    return createEl('i', { class: `fas ${name}`, 'aria-hidden': 'true' });
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
                createEl('div', { id: 'elo-progress', class: 'elo-progress', 'aria-live': 'polite' }),
                createEl('div', { class: 'elo-actions' },
                    createEl('button', { type: 'button', id: 'elo-undo', class: 'btn btn-secondary', disabled: true,
                        onClick: eloUndo, title: 'Laatste vergelijking terugdraaien (z)' },
                        eloIcon('fa-rotate-left'), ' Terugdraaien ', createEl('kbd', { text: 'z' })),
                    createEl('button', { type: 'button', id: 'elo-skip', class: 'btn btn-secondary',
                        onClick: eloSkip, title: 'Dit paar overslaan (spatie)' },
                        eloIcon('fa-forward'), ' Overslaan ', createEl('kbd', { text: 'spatie' }))))));
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

// De collecties in de keuzelijst: de hele bibliotheek en elke show
function eloFillScopes() {
    const select = document.getElementById('elo-scope');
    const shows = (typeof _lastShowsData !== 'undefined' && _lastShowsData && _lastShowsData.shows) || [];
    const options = [createEl('option', { value: '', text: 'Hele bibliotheek' })];
    [...shows].sort((a, b) => a.title.localeCompare(b.title, 'nl')).forEach(show => {
        options.push(createEl('option', { value: show.path, text: show.title }));
    });
    const known = new Set(options.map(o => o.value));
    eloScopeProviders.forEach(({ label, list }) => {
        let items = [];
        try { items = list() || []; } catch (_) { items = []; }
        if (!items.length) return;
        items.forEach(item => known.add(item.value));
        options.push(createEl('optgroup', { label },
            ...items.map(item => createEl('option', { value: item.value, text: item.text }))));
    });
    if (elo.scope && !known.has(elo.scope)) {
        options.push(createEl('option', { value: elo.scope, text: elo.scopeTitle }));
    }
    select.replaceChildren(...options);
    select.value = elo.scope;
    eloScopeTitle();
}

// De volledige naam als tooltip: de keuzelijst kort lange namen af
function eloScopeTitle() {
    const select = document.getElementById('elo-scope');
    const option = select && select.selectedOptions[0];
    if (select) select.title = option ? option.textContent : '';
}

function openElo(scope = '', title = '') {
    const overlay = document.getElementById('elo-overlay') || eloBuild();
    elo.scope = scope || '';
    elo.scopeTitle = title || 'Hele bibliotheek';
    elo.undo = [];
    elo.round = 0;
    elo.pair = null;
    eloFillScopes();
    // Geopend met het toetsenbord: dan meteen een zichtbare focusring
    const opener = document.activeElement;
    overlay.classList.toggle('is-keyboard', !!(opener && opener.matches && opener.matches(':focus-visible')));
    // Eerst de inhoud (het skelet), dan pas zichtbaar: zo onthoudt ui.js waar de focus vandaan kwam
    // (de knop), voordat dit venster hem ergens neerzet
    eloShowTab('compare');
    overlay.classList.add('active');
    // Eerst het venster; zodra het paar er is, gaat de focus naar de linker foto (eloRestoreFocus)
    setTimeout(() => { if (!overlay.contains(document.activeElement) || document.activeElement === overlay) eloFocusInBody(overlay); }, 0);
}

function closeElo() {
    const overlay = document.getElementById('elo-overlay');
    if (!overlay) return;
    overlay.classList.remove('active');
    elo.seq++;
    // De scores in de slideshow komen vanzelf goed: Shows haalt ze bij het afspelen opnieuw op
}

registerOverlayCloser('elo-overlay', closeElo);

function eloSetScope(value) {
    elo.scope = value;
    const select = document.getElementById('elo-scope');
    const option = select && select.selectedOptions[0];
    elo.scopeTitle = option ? option.textContent : 'Hele bibliotheek';
    elo.undo = [];
    elo.round = 0;      // de teller hoort bij de collectie, net als Terugdraaien
    elo.pair = null;
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
    if (tab === 'board') eloLoadBoard();
    else if (elo.pair) eloRenderPair();
    else eloLoadPair();
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
    const target = (side !== null && document.querySelector(`#elo-body .elo-choice[data-side="${side}"]`))
        || document.querySelector('#elo-body .elo-choice:not(.is-loading), #elo-body button');
    (target || overlay).focus({ preventScroll: true });
}

function eloRestoreFocus(state) {
    const overlay = document.getElementById('elo-overlay');
    if (!state || !overlay || !overlay.classList.contains('active')) return;
    const active = document.activeElement;
    const body = document.getElementById('elo-body');
    // Intussen zelf ergens anders heen gegaan (de keuzelijst, een knop): daar laten
    if (active && active !== document.body && active !== overlay && !body.contains(active)) return;
    eloFocusInBody(overlay, state.side);
}

// De inhoud vervangen zonder dat de focus verloren gaat
function eloSetBody(...nodes) {
    const state = eloFocusState();
    document.getElementById('elo-body').replaceChildren(...nodes);
    eloRestoreFocus(state);
}

// ==================== VERGELIJKEN ====================

function eloSkeleton() {
    const frame = () => createEl('div', { class: 'elo-choice is-loading', 'aria-hidden': 'true' },
        createEl('div', { class: 'elo-frame skeleton' }),
        createEl('div', { class: 'elo-meta' }, createEl('div', { class: 'skeleton skeleton-line' })));
    return createEl('div', { class: 'elo-stage', role: 'status', 'aria-label': 'Laden…' }, frame(), frame());
}

async function eloLoadPair(prev) {
    const seq = ++elo.seq;
    if (!elo.pair) eloSetBody(eloSkeleton());
    const params = [eloScopeParam(), ...(prev || []).map(p => `prev=${encodeURIComponent(p.path)}`)].filter(Boolean);
    try {
        const data = await fetchJSON(`${API_BASE}/api/shows/compare/pair?${params.join('&')}`);
        if (seq !== elo.seq) return;
        elo.pair = data.pair;
        elo.stats = data.stats;
        elo.scopeTitle = data.scope.title;
        if (!data.pair) {
            eloRenderEmpty(data.reason);
        } else {
            eloRenderPair();
        }
    } catch (err) {
        if (seq !== elo.seq) return;
        elo.pair = null;
        eloSetBody(errorState('Geen foto\'s geladen', err, () => eloLoadPair()));
    }
    eloUpdateProgress();
    eloUpdateActions();
}

function eloRenderEmpty(reason) {
    const few = reason === 'too_few';
    eloSetBody(emptyState({
        icon: few ? 'fa-images' : 'fa-check',
        title: few ? 'Te weinig foto\'s in deze collectie' : 'Geen nieuw paar',
        text: few ? 'Er zijn minstens twee foto\'s nodig. Kies een andere collectie of de hele bibliotheek.'
            : 'Alle foto\'s hier zijn net met elkaar vergeleken. Kies een andere collectie, of bekijk de ranglijst.',
        action: elo.scope ? { label: 'Hele bibliotheek', onClick: () => { document.getElementById('elo-scope').value = ''; eloSetScope(''); } }
            : { label: 'Ranglijst bekijken', onClick: () => eloShowTab('board') },
    }));
}

function eloChoice(photo, side) {
    const keys = side === 0 ? ['←', '1'] : ['→', '2'];
    const img = createEl('img', { class: 'elo-img', alt: photo.name, src: vaultFileUrl('file', photo.path), draggable: 'false' });
    // Een formaat dat de browser niet kan tonen: dan de miniatuur
    img.addEventListener('error', () => {
        if (!img.dataset.fallback) { img.dataset.fallback = '1'; img.src = vaultFileUrl('thumb', photo.path); }
    }, { once: false });
    img.addEventListener('load', () => img.classList.add('is-loaded'));
    const games = photo.games === 1 ? '1 vergelijking' : `${formatNumberNl(photo.games)} vergelijkingen`;
    return createEl('button', {
        type: 'button', class: 'elo-choice', dataset: { side: String(side) },
        'aria-label': `${side === 0 ? 'Links' : 'Rechts'} is mooier: ${photo.name}`,
        title: `${photo.name} (${keys.join(' of ')})`,
        onClick: () => eloPick(side),
    },
        createEl('div', { class: 'elo-frame' }, img,
            createEl('span', { class: 'elo-delta', 'aria-hidden': 'true' }),
            createEl('span', { class: 'elo-check', 'aria-hidden': 'true' }, eloIcon('fa-check'))),
        createEl('div', { class: 'elo-meta' },
            createEl('span', { class: 'elo-keys', 'aria-hidden': 'true' }, ...keys.map(k => createEl('kbd', { text: k }))),
            createEl('span', { class: 'elo-name', text: photo.name, title: photo.path }),
            createEl('span', { class: 'elo-score', title: 'Score: stijgt als je een foto kiest, daalt als de ander wint' },
                'Score ', createEl('span', { class: 'tabular', text: formatNumberNl(photo.rating) }),
                createEl('span', { class: 'elo-games', text: ` · ${games}` }))));
}

function eloRenderPair() {
    if (!elo.pair) return;
    eloSetBody(createEl('div', { class: 'elo-stage' },
        eloChoice(elo.pair[0], 0),
        createEl('span', { class: 'elo-vs', 'aria-hidden': 'true', text: 'of' }),
        eloChoice(elo.pair[1], 1)));
    eloUpdateProgress();
    eloUpdateActions();
}

function eloUpdateProgress() {
    const box = document.getElementById('elo-progress');
    if (!box) return;
    const parts = [];
    if (elo.round) parts.push(`${formatNumberNl(elo.round)} vergeleken in deze ronde`);
    if (elo.stats) {
        parts.push(`${formatNumberNl(elo.stats.compared)} van ${formatNumberNl(elo.stats.photos)} foto's beoordeeld`);
    }
    box.replaceChildren(createEl('span', { class: 'elo-scope-name', text: elo.scopeTitle }),
        parts.length ? createEl('span', { text: ` · ${parts.join(' · ')}` }) : null);
    box.title = box.textContent;
}

// Terugdraaien kan alleen met iets om terug te draaien, Overslaan alleen met een paar in beeld
function eloUpdateActions() {
    const undo = document.getElementById('elo-undo');
    if (undo) undo.disabled = elo.undo.length === 0 || elo.busy;
    const skip = document.getElementById('elo-skip');
    if (skip) skip.disabled = !elo.pair || elo.busy;
}

function eloSigned(n) {
    return n > 0 ? `+${n}` : n < 0 ? `−${-n}` : '±0';
}

async function eloPick(side) {
    if (elo.busy || !elo.pair || elo.tab !== 'compare') return;
    // Bezig tot het volgende paar er staat: een tweede toets in die tijd kiest niets
    elo.busy = true;
    eloUpdateActions();
    const [a, b] = elo.pair;
    const choices = document.querySelectorAll('#elo-body .elo-choice');
    choices.forEach((el, i) => el.classList.add(i === side ? 'is-chosen' : 'is-passed'));
    let saved = false;
    try {
        const result = await fetchJSON(`${API_BASE}/api/shows/compare`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ a: a.path, b: b.path, winner: side === 0 ? 'a' : 'b', ...eloScopeBody() }),
        });
        saved = true;
        elo.undo.push(result.id);
        elo.round++;
        [result.a, result.b].forEach((change, i) => {
            const delta = choices[i] && choices[i].querySelector('.elo-delta');
            if (delta) {
                delta.textContent = eloSigned(change.rating - change.before);
                delta.classList.add(change.rating >= change.before ? 'is-up' : 'is-down');
            }
        });
        await new Promise(resolve => setTimeout(resolve, ELO_PICK_PAUSE_MS));
        await eloLoadPair([a, b]);
    } catch (err) {
        if (!saved) {
            choices.forEach(el => el.classList.remove('is-chosen', 'is-passed'));
            toastFetchError('Keuze niet opgeslagen', err);
        }
    }
    elo.busy = false;
    eloUpdateActions();
}

async function eloSkip() {
    if (elo.busy || !elo.pair || elo.tab !== 'compare') return;
    elo.busy = true;
    eloUpdateActions();
    try {
        await eloLoadPair(elo.pair);
    } finally {
        elo.busy = false;
        eloUpdateActions();
    }
}

async function eloUndo() {
    if (elo.busy || !elo.undo.length || elo.tab !== 'compare') return;
    elo.busy = true;
    eloUpdateActions();
    const id = elo.undo.pop();
    try {
        const result = await fetchJSON(`${API_BASE}/api/shows/compare/undo`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id }),
        });
        elo.round = Math.max(0, elo.round - 1);
        if (result.stats) elo.stats = result.stats;
        // Hetzelfde paar weer in beeld, met de scores van vóór die keuze
        const name = p => p.path.slice(p.path.lastIndexOf('/') + 1);
        elo.pair = [result.a, result.b].map(p => ({ ...p, name: name(p), votes: 0 }));
        elo.seq++;
        eloRenderPair();
        showToast('Teruggedraaid', 'Beide scores staan weer zoals ze waren. Kies opnieuw.', 'info');
    } catch (err) {
        toastFetchError('Terugdraaien lukte niet', err);
    }
    elo.busy = false;
    eloUpdateActions();
    eloUpdateProgress();
}

function eloKeydown(e) {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    if (isTypingTarget(e.target) || elo.tab !== 'compare') return;
    // Spatie en Enter op een knop (Sluiten, Terugdraaien, de tabbladen) doen wat die knop doet
    const onControl = e.target.closest && e.target.closest('button, a[href], summary') && !e.target.closest('.elo-choice');
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    // Een ingedrukt gehouden toets herhaalt niet: anders kies je een paar dat je nog niet zag
    const onChoice = e.target.closest && e.target.closest('.elo-choice');
    if (e.repeat && (['ArrowLeft', 'ArrowRight', '1', '2', ' ', 's', 'z'].includes(key) || (key === 'Enter' && onChoice))) {
        if (!(onControl && key === ' ')) e.preventDefault();
        return;
    }
    if (key === 'ArrowLeft' || key === '1') { e.preventDefault(); eloPick(0); }
    else if (key === 'ArrowRight' || key === '2') { e.preventDefault(); eloPick(1); }
    else if ((key === ' ' || key === 's') && !(onControl && key === ' ')) { e.preventDefault(); eloSkip(); }
    else if (key === 'z') { e.preventDefault(); eloUndo(); }
}

// ==================== RANGLIJST ====================

async function eloLoadBoard() {
    const seq = ++elo.seq;
    eloSetBody(skeletonRows(6));
    try {
        const data = await fetchJSON(`${API_BASE}/api/shows/leaderboard?limit=${ELO_BOARD_LIMIT}${elo.scope ? '&' + eloScopeParam() : ''}`);
        if (seq !== elo.seq) return;
        eloRenderBoard(data);
    } catch (err) {
        if (seq !== elo.seq) return;
        eloSetBody(errorState('Ranglijst laden mislukt', err, eloLoadBoard));
    }
}

function eloRenderBoard(data) {
    if (!data.items.length) {
        eloSetBody(emptyState({
            icon: 'fa-ranking-star', title: 'Nog geen ranglijst',
            text: 'Vergelijk een paar foto\'s (of stem in de slideshow); de best beoordeelde verschijnen hier.',
            action: { label: 'Begin met vergelijken', onClick: () => eloShowTab('compare'), primary: true },
        }));
        return;
    }
    const rows = data.items.map(item => {
        const thumb = createEl('img', { class: 'elo-board-img', src: vaultFileUrl('thumb', item.path), alt: '', loading: 'lazy' });
        const won = item.games ? `${formatNumberNl(item.wins)} van ${formatNumberNl(item.games)} gewonnen` : 'Nog niet vergeleken';
        const votes = item.votes ? ` · ${item.votes === 1 ? '1 stem' : `${formatNumberNl(item.votes)} stemmen`}` : '';
        return createEl('li', { class: `elo-board-row${item.rank <= 3 ? ' is-top' : ''}` },
            createEl('span', { class: 'elo-rank tabular', text: String(item.rank) }),
            createEl('span', { class: 'elo-board-thumb' }, thumb),
            createEl('span', { class: 'elo-board-text' },
                createEl('span', { class: 'elo-board-name', text: item.name, title: item.path }),
                createEl('span', { class: 'elo-board-sub', text: `${item.folder} · ${won}${votes}` })),
            createEl('span', { class: 'elo-board-score tabular', 'aria-label': `Score ${item.rating}`, text: formatNumberNl(item.rating) }));
    });
    const shown = data.items.length < data.total ? `De beste ${data.items.length} van ${formatNumberNl(data.total)}` : `${formatNumberNl(data.total)} beoordeeld`;
    eloSetBody(createEl('div', { class: 'elo-board' },
        createEl('p', { class: 'elo-board-caption' }, createEl('strong', { text: data.scope.title }), ` · ${shown}`),
        createEl('ol', { class: 'elo-board-list', 'aria-label': `Ranglijst ${data.scope.title}` }, ...rows)));
}

addShortcutGroup('Welke is mooier? (Shows)', [
    [['←', '1'], 'De linker foto is mooier'],
    [['→', '2'], 'De rechter foto is mooier'],
    [['spatie', 's'], 'Dit paar overslaan'],
    [['z'], 'Laatste vergelijking terugdraaien'],
]);
