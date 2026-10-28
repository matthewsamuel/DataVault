// DataVault Dashboard - Beste uit reeks (tabblad op de pagina Duplicaten)
// Note: API_BASE is defined in app.js
//
// Foto's die op elkaar lijken: een verkleinde of opnieuw opgeslagen kopie, een bewerking, of
// een burst (een paar opnames binnen een paar seconden). De server vergelijkt de foto's in een
// achtergrondtaak ('phash', jobsStart) en geeft per reeks alle foto's met een kwaliteitsscore;
// de beste is voorgesteld om te houden. Per reeks kies je zelf wat blijft (ook meerdere), en
// bij Bevestigen gaan de andere na een bevestiging naar RAW_DUPLICATES/near-duplicates: één
// actie per reeks in het Herstelcentrum, er wordt nooit iets gewist (/api/series/keep).
//
// Toetsen (op een foto in de lijst): ← → naar de vorige of volgende foto, ↑ ↓ naar de vorige
// of volgende reeks, spatie houden of laten gaan, Enter bevestigen, s overslaan, a alles
// behouden. Overslaan geldt voor deze sessie; 'Alles behouden' onthoudt de server.

const SERIES_PAGE = 20;
const SERIES_STRICTNESS = ['streng', 'normaal', 'ruim'];
const SERIES_STRICTNESS_LABELS = { streng: 'Streng', normaal: 'Normaal', ruim: 'Ruim' };
const SERIES_STRICTNESS_HELP = {
    streng: 'Streng: alleen vrijwel dezelfde foto, zoals een verkleinde of opnieuw opgeslagen kopie.',
    normaal: 'Normaal: ook licht bewerkte foto\'s en bursts met weinig beweging.',
    ruim: 'Ruim: ook iets bijgesneden of lichter gemaakte foto\'s en bursts met meer beweging.',
};
const SERIES_POINTS = [
    ['sharpness', 'Scherpte', 40],
    ['resolution', 'Resolutie', 25],
    ['exposure', 'Belichting', 15],
    ['choice', 'Jouw keuze', 20],
];

const seriesState = {
    strictness: 'normaal',
    sort: 'size',
    items: [],             // reeksen in beeld, in volgorde
    nextOffset: 0,         // null: alles geladen
    total: 0,
    photos: 0,
    keep: new Map(),       // reeks-sleutel -> Set van foto-id's die blijven
    skipped: new Set(),    // reeks-sleutels die in deze sessie overgeslagen zijn
    cursor: { key: null, id: null },
    status: null,
    jobId: null,
    unwatch: null,
    loading: false,
    loaded: false,
    seq: 0,
    confirm: null,         // {series, keep[], move[]} terwijl het venster open is
    busy: false,
};

try {
    const saved = localStorage.getItem('seriesStrictness');
    if (SERIES_STRICTNESS.includes(saved)) seriesState.strictness = saved;
    const sort = localStorage.getItem('seriesSort');
    if (sort === 'size' || sort === 'recent') seriesState.sort = sort;
} catch (e) { /* geen opslag: standaard */ }

// ==================== TABBLADEN OP DE PAGINA DUPLICATEN ====================

const DUP_SUBTITLES = {
    exact: 'Kies een map, zoek byte voor byte identieke bestanden en bepaal per groep welke kopie blijft. '
        + 'De andere kopieën gaan naar RAW_DUPLICATES: er wordt niets gewist en elke actie is terug te zetten.',
    series: 'Foto\'s die op elkaar lijken: verkleinde of bewerkte kopieën en bursts. Kies per reeks wat blijft; '
        + 'de rest gaat naar RAW_DUPLICATES/near-duplicates en is in het Herstelcentrum terug te zetten.',
};

function dupCurrentTab() {
    const tab = document.querySelector('.dup-tabs [aria-selected="true"]');
    return tab ? tab.dataset.tab : 'exact';
}

function dupShowTab(name, focus = false) {
    if (name !== 'exact' && name !== 'series') return;
    document.querySelectorAll('.dup-tabs [role="tab"]').forEach(tab => {
        const on = tab.dataset.tab === name;
        tab.classList.toggle('active', on);
        tab.setAttribute('aria-selected', on ? 'true' : 'false');
        tab.tabIndex = on ? 0 : -1;
        if (on && focus) tab.focus();
    });
    document.getElementById('dup-panel-exact').hidden = name !== 'exact';
    document.getElementById('dup-panel-series').hidden = name !== 'series';
    document.getElementById('dup-subtitle').textContent = DUP_SUBTITLES[name];
    try { localStorage.setItem('dupTab', name); } catch (e) { /* geen opslag */ }
    if (name === 'series') seriesInit();
}

// Pijltjes, Home en End binnen de tabbladen (zoals elke tablist)
function dupTabsKeydown(e) {
    const tabs = [...document.querySelectorAll('.dup-tabs [role="tab"]')];
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    let next = null;
    if (e.key === 'ArrowRight') next = tabs[(i + 1) % tabs.length];
    else if (e.key === 'ArrowLeft') next = tabs[(i - 1 + tabs.length) % tabs.length];
    else if (e.key === 'Home') next = tabs[0];
    else if (e.key === 'End') next = tabs[tabs.length - 1];
    if (!next) return;
    e.preventDefault();
    e.stopPropagation();
    dupShowTab(next.dataset.tab, true);
}

// Bij het openen van de pagina Duplicaten (duplicatesInit): het laatst gekozen tabblad
function seriesOnShow() {
    let saved = 'exact';
    try { saved = localStorage.getItem('dupTab') || 'exact'; } catch (e) { /* geen opslag */ }
    if (saved !== dupCurrentTab()) dupShowTab(saved);
    else if (saved === 'series') seriesInit();
}

// ==================== STAND EN VERGELIJKEN ====================

function seriesInit() {
    seriesSyncControls();
    seriesLoadStatus();
    if (!seriesState.loaded && !seriesState.loading) seriesLoad(true);
}

function seriesSyncControls() {
    const slider = document.getElementById('series-strictness');
    slider.value = String(SERIES_STRICTNESS.indexOf(seriesState.strictness));
    slider.setAttribute('aria-valuetext', SERIES_STRICTNESS_LABELS[seriesState.strictness]);
    document.querySelectorAll('.series-strictness-labels button').forEach((b, i) => {
        b.classList.toggle('active', SERIES_STRICTNESS[i] === seriesState.strictness);
    });
    document.getElementById('series-sort').value = seriesState.sort;
    document.getElementById('series-strictness-help').textContent = SERIES_STRICTNESS_HELP[seriesState.strictness];
}

async function seriesLoadStatus() {
    try {
        const status = await fetchJSON(`${API_BASE}/api/series/status`);
        seriesState.status = status;
        seriesRenderStatus();
        if (status.job && status.job.id !== seriesState.jobId) seriesFollowJob(status.job.id);
    } catch (err) {
        document.getElementById('series-status-text').textContent = err.code === 'vault_unavailable'
            ? 'Archief niet bereikbaar' : `Stand niet op te halen: ${err.message || err}`;
    }
}

function seriesRenderStatus(job) {
    const status = seriesState.status;
    const text = document.getElementById('series-status-text');
    const button = document.getElementById('btn-series-compare');
    const progress = document.getElementById('series-progress');
    const running = !!(job && JOB_ACTIVE.has(job.status));
    if (running) {
        const pct = job.total ? Math.round(job.processed / job.total * 100) : null;
        text.textContent = job.status === 'waiting_for_vault' ? 'Wacht tot het archief terug is…'
            : (job.message || 'Foto\'s vergelijken…');
        progress.hidden = false;
        progress.classList.toggle('is-indeterminate', pct === null);
        document.getElementById('series-progress-fill').style.width = pct === null ? '' : `${pct}%`;
        button.disabled = true;
        button.classList.add('is-loading');
        return;
    }
    progress.hidden = true;
    button.disabled = false;
    button.classList.remove('is-loading');
    if (!status) return;
    if (!status.ready) {
        text.textContent = 'De bibliotheek wordt nog opgebouwd; daarna kun je vergelijken.';
        button.disabled = true;
        return;
    }
    const parts = [`${formatNumberNl(status.analysed)} van ${formatNumberNl(status.photos)} foto's vergeleken`];
    if (status.pending) parts.push(`${formatNumberNl(status.pending)} nog te doen`);
    if (status.undecodable) parts.push(`${formatNumberNl(status.undecodable)} niet te openen`);
    if (status.errors) parts.push(`${formatNumberNl(status.errors)} leesfout${status.errors === 1 ? '' : 'en'}`);
    text.textContent = parts.join(' · ');
    // Is er iets te doen, dan is vergelijken de hoofdactie van dit scherm
    button.classList.toggle('btn-primary', status.pending > 0);
    button.classList.toggle('btn-secondary', !status.pending);
    const reset = document.getElementById('btn-series-reset');
    reset.hidden = !status.reviewed;
    reset.textContent = `Behouden reeksen weer tonen (${formatNumberNl(status.reviewed)})`;
}

// Eerst de bibliotheek bijwerken (een snelle ronde: alleen wat veranderde), zodat ook net
// toegevoegde foto's meedoen; daarna vergelijken
async function seriesStartCompare() {
    const button = document.getElementById('btn-series-compare');
    button.disabled = true;
    try {
        // (auto: een stille ronde, zonder eigen melding; dit scherm toont de voortgang)
        const index = await jobsStart('index', { auto: true });
        seriesRenderStatus({ status: 'running', message: 'Bibliotheek bijwerken…', total: null });
        const done = await seriesWaitForJob(index.id);
        if (done.status !== 'done') {
            seriesRenderStatus();
            showToast('Vergelijken niet gestart', 'De bibliotheek is niet bijgewerkt (gestopt of gepauzeerd).', 'warning');
            return;
        }
        const job = await jobsStart('phash', {});
        seriesFollowJob(job.id);
    } catch (err) {
        seriesRenderStatus();
        toastFetchError('Vergelijken niet gestart', err);
    }
}

function seriesWaitForJob(id) {
    return new Promise(resolve => {
        const stop = jobsWatch(id, job => {
            if (JOB_ACTIVE.has(job.status)) return;
            setTimeout(() => stop(), 0);
            resolve(job);
        });
    });
}

function seriesFollowJob(id) {
    if (seriesState.unwatch) seriesState.unwatch();
    seriesState.jobId = id;
    let wasActive = null;
    seriesState.unwatch = jobsWatch(id, job => {
        const active = JOB_ACTIVE.has(job.status);
        seriesRenderStatus(job);
        if (wasActive && !active) {
            seriesState.unwatch && seriesState.unwatch();
            seriesState.unwatch = null;
            seriesState.jobId = null;
            seriesLoadStatus();
            seriesLoad(true);
        }
        wasActive = active;
    });
}

// ==================== STRENGHEID EN VOLGORDE ====================

function seriesStrictnessInput(value) {
    const name = SERIES_STRICTNESS[Number(value)] || 'normaal';
    const slider = document.getElementById('series-strictness');
    slider.setAttribute('aria-valuetext', SERIES_STRICTNESS_LABELS[name]);
    document.getElementById('series-strictness-help').textContent = SERIES_STRICTNESS_HELP[name];
}

function seriesStrictnessChange(value) {
    seriesSetStrictness(SERIES_STRICTNESS[Number(value)] || 'normaal');
}

function seriesSetStrictness(name) {
    if (!SERIES_STRICTNESS.includes(name)) return;
    const changed = name !== seriesState.strictness;
    seriesState.strictness = name;
    try { localStorage.setItem('seriesStrictness', name); } catch (e) { /* geen opslag */ }
    seriesSyncControls();
    if (changed) seriesLoad(true);
}

function seriesSetSort(sort) {
    if (sort !== 'size' && sort !== 'recent') return;
    seriesState.sort = sort;
    try { localStorage.setItem('seriesSort', sort); } catch (e) { /* geen opslag */ }
    seriesLoad(true);
}

// ==================== LADEN ====================

async function seriesLoad(reset) {
    const seq = ++seriesState.seq;
    const list = document.getElementById('series-list');
    if (reset) {
        seriesState.items = [];
        seriesState.nextOffset = 0;
        seriesState.keep.clear();
        list.replaceChildren(skeletonRows(3));
    }
    if (seriesState.nextOffset === null) return;
    seriesState.loading = true;
    const params = new URLSearchParams({
        strictness: seriesState.strictness, sort: seriesState.sort,
        offset: String(seriesState.nextOffset), limit: String(SERIES_PAGE),
    });
    try {
        const data = await fetchJSON(`${API_BASE}/api/series?${params}`);
        if (seq !== seriesState.seq) return;
        const fresh = data.items.filter(s => !seriesState.skipped.has(s.key)
            && !seriesState.items.some(t => t.key === s.key));
        fresh.forEach(s => seriesState.keep.set(s.key, new Set([s.best])));
        seriesState.items.push(...fresh);
        seriesState.nextOffset = data.next_offset;
        seriesState.total = data.total;
        seriesState.photos = data.photos;
        seriesState.loaded = true;
        seriesRender();
    } catch (err) {
        if (seq !== seriesState.seq) return;
        list.replaceChildren(errorState('Reeksen laden mislukt', err, () => seriesLoad(true)));
        document.getElementById('series-summary').hidden = true;
    } finally {
        if (seq === seriesState.seq) seriesState.loading = false;
    }
}

// ==================== TEKENEN ====================

function seriesTitle(series) {
    const title = series.kind === 'burst'
        ? `Burst van ${series.count} foto's`
        : `${series.count} foto's die op elkaar lijken`;
    // Een heel grote groep komt in delen (de server knipt hem op tijdsvolgorde)
    return series.part ? `${title} (deel ${series.part[0]} van ${series.part[1]})` : title;
}

function seriesDate(ts) {
    if (!ts) return null;
    return new Date(ts * 1000).toLocaleString('nl-NL', {
        day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
}

function seriesRender() {
    const list = document.getElementById('series-list');
    const summary = document.getElementById('series-summary');
    const status = seriesState.status;
    const shown = seriesState.items.length;
    summary.hidden = false;
    document.getElementById('series-summary-text').textContent = seriesState.total
        ? `${formatNumberNl(seriesState.total)} ${seriesState.total === 1 ? 'reeks' : 'reeksen'} · ${formatNumberNl(seriesState.photos)} foto's`
        : 'Geen reeksen';
    if (!shown) {
        list.replaceChildren(seriesEmptyState(status));
        return;
    }
    const cards = seriesState.items.map(series => seriesCard(series));
    list.replaceChildren(...cards);
    if (seriesState.nextOffset !== null) {
        list.appendChild(createEl('div', { class: 'series-more' },
            createEl('button', {
                class: 'btn btn-secondary', id: 'btn-series-more', text: 'Meer reeksen laden',
                onClick: () => seriesLoad(false),
            })));
    }
    seriesEnsureCursor();
}

function seriesEmptyState(status) {
    if (status && !status.ready) {
        return emptyState({
            icon: 'fa-hourglass-half', title: 'De bibliotheek wordt nog opgebouwd',
            text: 'Zodra alle foto\'s bekend zijn, kun je ze hier vergelijken.',
        });
    }
    if (status && !status.analysed && status.pending) {
        return emptyState({
            icon: 'fa-layer-group', title: 'Nog niet vergeleken',
            text: 'Vergelijk je foto\'s één keer; daarna gaat het alleen nog om nieuwe of veranderde foto\'s.',
            action: { label: 'Foto\'s vergelijken', onClick: seriesStartCompare, primary: true },
        });
    }
    if (seriesState.skipped.size && seriesState.total) {
        return emptyState({
            icon: 'fa-forward', title: 'Alle reeksen overgeslagen',
            text: 'Overgeslagen reeksen komen terug als je de pagina opnieuw laadt.',
            action: { label: 'Overgeslagen tonen', onClick: () => { seriesState.skipped.clear(); seriesLoad(true); } },
        });
    }
    const looser = SERIES_STRICTNESS[SERIES_STRICTNESS.indexOf(seriesState.strictness) + 1];
    return emptyState({
        icon: 'fa-circle-check', title: 'Geen reeksen gevonden',
        text: looser ? `Bij deze strengheid lijken geen foto's op elkaar. Bij ${SERIES_STRICTNESS_LABELS[looser].toLowerCase()} kijkt de app ruimer.`
            : 'Er zijn geen foto\'s die op elkaar lijken.',
        action: looser ? { label: `${SERIES_STRICTNESS_LABELS[looser]} proberen`, onClick: () => seriesSetStrictness(looser) } : null,
    });
}

function seriesCard(series) {
    const keep = seriesState.keep.get(series.key);
    const moving = series.members.filter(m => !keep.has(m.id));
    const meta = [];
    // De datum van de voorgestelde foto (de nieuwste kan een kopie van vandaag zijn)
    const best = series.members.find(m => m.best) || series.members[0];
    const when = seriesDate(best.taken || series.taken);
    if (when) meta.push(createEl('span', { text: when }));
    if (series.folder) {
        meta.push(createEl('span', { class: 'path-label', title: series.folder, text: displayPath(series.folder) }));
    } else {
        meta.push(createEl('span', { text: 'in meerdere mappen' }));
    }
    const card = createEl('section', {
        class: 'series-card', dataset: { key: series.key }, 'aria-labelledby': `series-title-${series.key}`,
    },
        createEl('div', { class: 'series-card-header' },
            createEl('div', { class: 'series-card-heading' },
                createEl('span', { class: 'series-kind-icon', 'aria-hidden': 'true' },
                    createEl('i', { class: `fas ${series.kind === 'burst' ? 'fa-bolt' : 'fa-clone'}` })),
                createEl('div', { class: 'series-card-titles' },
                    createEl('h2', { class: 'series-card-title', id: `series-title-${series.key}`, text: seriesTitle(series) }),
                    createEl('div', { class: 'series-card-meta' }, ...seriesJoin(meta)))),
            createEl('div', { class: 'series-card-actions' },
                createEl('button', {
                    class: 'btn btn-ghost', title: 'Deze reeks nu overslaan (s)', onClick: () => seriesSkip(series),
                }, 'Overslaan'),
                createEl('button', {
                    class: 'btn btn-secondary', title: 'Alle foto\'s blijven; de reeks komt niet terug (a)',
                    onClick: () => seriesKeepAll(series),
                }, 'Alles behouden'),
                createEl('button', {
                    class: 'btn btn-primary series-confirm-btn', title: 'Bevestigen (Enter)',
                    onClick: () => seriesConfirm(series),
                }, moving.length ? `Verplaats ${moving.length}` : 'Alles behouden'))),
        createEl('div', { class: 'series-photos', role: 'group', 'aria-label': `Foto's: ${seriesTitle(series)}` },
            ...series.members.map(member => seriesPhoto(series, member, keep.has(member.id)))),
        createEl('div', { class: 'series-detail', 'aria-live': 'polite' }));
    return card;
}

function seriesJoin(nodes) {
    const out = [];
    nodes.forEach((node, i) => {
        if (i) out.push(createEl('span', { class: 'series-dot', 'aria-hidden': 'true', text: '·' }));
        out.push(node);
    });
    return out;
}

function seriesPhotoLabel(member, kept) {
    return `${member.name}: ${kept ? 'blijft' : 'gaat naar RAW_DUPLICATES'}${member.best ? ', voorgesteld als beste' : ''}, score ${member.score}`;
}

function seriesPhoto(series, member, kept) {
    const img = createEl('img', { class: 'loading', alt: '', loading: 'lazy', decoding: 'async' });
    img.addEventListener('load', () => img.classList.remove('loading'), { once: true });
    img.addEventListener('error', () => { img.classList.remove('loading'); img.classList.add('is-missing'); }, { once: true });
    img.src = vaultFileUrl('thumb', member.path);
    const tile = createEl('button', {
        type: 'button',
        class: `series-photo thumb-tile${kept ? ' selected' : ' is-leaving'}${member.best ? ' is-best' : ''}`,
        dataset: { id: String(member.id) },
        tabindex: '-1',
        'aria-pressed': kept ? 'true' : 'false',
        'aria-label': seriesPhotoLabel(member, kept),
        title: member.path,
        onClick: () => { seriesMoveCursor(series.key, member.id, true); seriesToggleKeep(series, member.id); },
        onFocus: () => seriesMoveCursor(series.key, member.id, false),
    },
        img,
        member.best ? createEl('span', { class: 'series-best-badge' },
            createEl('i', { class: 'fas fa-star', 'aria-hidden': 'true' }), 'Beste') : null,
        createEl('span', { class: 'series-photo-state', 'aria-hidden': 'true', text: kept ? '' : 'Gaat weg' }));
    const size = member.width && member.height ? `${member.width} × ${member.height}` : formatBytes(member.size);
    return createEl('div', { class: 'series-photo-cell' },
        tile,
        createEl('div', { class: 'series-photo-name', title: member.path, text: member.name }),
        createEl('div', { class: 'series-photo-meta' },
            createEl('span', { class: 'series-score', title: seriesBreakdownText(member) },
                createEl('span', { class: 'series-score-value', text: String(member.score) }),
                seriesScoreBar(member)),
            createEl('span', { class: 'series-photo-facts', text: size })),
        member.copies && member.copies.length ? createEl('span', {
            class: 'chip series-copies',
            title: 'Byte voor byte gelijk; gaan mee als deze foto weggaat:\n' + member.copies.map(c => c.path).join('\n'),
        }, `+${member.copies.length} exacte ${member.copies.length === 1 ? 'kopie' : 'kopieën'}`) : null);
}

function seriesBreakdownText(member) {
    return SERIES_POINTS.map(([key, label, max]) => `${label} ${member.breakdown[key]}/${max}`).join(' · ');
}

// Vier dunne stukjes in één balk: hoeveel elk onderdeel bijdraagt aan de score (van 100)
function seriesScoreBar(member) {
    return createEl('span', { class: 'series-score-bar', 'aria-hidden': 'true' },
        ...SERIES_POINTS.map(([key], i) => {
            const part = createEl('span', { class: `series-score-part series-score-part-${i + 1}` });
            part.style.width = `${member.breakdown[key]}%`;
            return part;
        }));
}

function seriesUpdateCard(series) {
    const card = seriesCardEl(series.key);
    if (!card) return;
    const keep = seriesState.keep.get(series.key);
    card.querySelectorAll('.series-photo').forEach(tile => {
        const member = series.members.find(m => m.id === Number(tile.dataset.id));
        const kept = keep.has(member.id);
        tile.classList.toggle('selected', kept);
        tile.classList.toggle('is-leaving', !kept);
        tile.setAttribute('aria-pressed', kept ? 'true' : 'false');
        tile.setAttribute('aria-label', seriesPhotoLabel(member, kept));
        tile.querySelector('.series-photo-state').textContent = kept ? '' : 'Gaat weg';
    });
    const moving = series.members.length - keep.size;
    card.querySelector('.series-confirm-btn').textContent = moving ? `Verplaats ${moving}` : 'Alles behouden';
    seriesRenderDetail(series);
}

function seriesCardEl(key) {
    return [...document.querySelectorAll('#series-list .series-card')].find(c => c.dataset.key === key) || null;
}

function seriesRenderDetail(series) {
    const card = seriesCardEl(series.key);
    if (!card) return;
    const detail = card.querySelector('.series-detail');
    const id = seriesState.cursor.key === series.key ? seriesState.cursor.id : series.best;
    const member = series.members.find(m => m.id === id) || series.members[0];
    const keep = seriesState.keep.get(series.key);
    const facts = [
        member.width && member.height ? `${member.width} × ${member.height}` : null,
        formatBytes(member.size),
        member.camera,
        seriesDate(member.taken),
    ].filter(Boolean).join(' · ');
    detail.replaceChildren(
        createEl('span', { class: 'series-detail-name', title: member.path, text: member.name }),
        createEl('span', { class: 'series-detail-facts', text: facts }),
        createEl('span', { class: 'series-detail-score' },
            createEl('span', { text: `Score ${member.score}` }),
            ...SERIES_POINTS.map(([key, label, max], i) => createEl('span', { class: 'series-detail-part' },
                createEl('span', { class: `series-score-dot series-score-part-${i + 1}`, 'aria-hidden': 'true' }),
                `${label} ${member.breakdown[key]}/${max}`))),
        createEl('span', { class: `badge ${keep.has(member.id) ? 'badge-accent' : 'badge-warning'}`,
            text: keep.has(member.id) ? 'Blijft' : 'Gaat weg' }));
}

// ==================== CURSOR EN TOETSEN ====================

function seriesEnsureCursor() {
    const { key } = seriesState.cursor;
    const series = seriesState.items.find(s => s.key === key) || seriesState.items[0];
    if (!series) return;
    const id = series.members.some(m => m.id === seriesState.cursor.id) ? seriesState.cursor.id : series.best;
    seriesMoveCursor(series.key, id, false);
    seriesState.items.forEach(s => seriesRenderDetail(s));
}

// Roving tabindex: alleen de foto onder de cursor is een tabstop
function seriesMoveCursor(key, id, focus) {
    seriesState.cursor = { key, id };
    document.querySelectorAll('#series-list .series-photo').forEach(tile => {
        const here = tile.dataset.id === String(id) && tile.closest('.series-card').dataset.key === key;
        tile.tabIndex = here ? 0 : -1;
        if (here && focus && document.activeElement !== tile) tile.focus();
    });
    const series = seriesState.items.find(s => s.key === key);
    if (series) seriesRenderDetail(series);
}

function seriesFocusCursor() {
    const card = seriesCardEl(seriesState.cursor.key);
    const tile = card && [...card.querySelectorAll('.series-photo')].find(t => t.dataset.id === String(seriesState.cursor.id));
    if (tile) {
        tile.focus();
        tile.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    } else {
        const list = document.getElementById('series-list');
        const fallback = list.querySelector('.empty-state-action, #btn-series-more');
        (fallback || document.getElementById('btn-series-compare')).focus();
    }
}

function seriesKeydown(e) {
    if (e.metaKey || e.ctrlKey || e.altKey || anyOverlayOpen()) return;
    const tile = e.target.closest && e.target.closest('.series-photo');
    if (!tile) return;
    const key = tile.closest('.series-card').dataset.key;
    const index = seriesState.items.findIndex(s => s.key === key);
    const series = seriesState.items[index];
    if (!series) return;
    const pos = series.members.findIndex(m => m.id === Number(tile.dataset.id));
    const go = (s, member) => { seriesMoveCursor(s.key, member.id, true); seriesFocusCursor(); };
    switch (e.key) {
    case 'ArrowRight':
        if (pos < series.members.length - 1) go(series, series.members[pos + 1]);
        break;
    case 'ArrowLeft':
        if (pos > 0) go(series, series.members[pos - 1]);
        break;
    case 'ArrowDown': {
        const next = seriesState.items[index + 1];
        if (next) go(next, next.members.find(m => m.best) || next.members[0]);
        break;
    }
    case 'ArrowUp': {
        const prev = seriesState.items[index - 1];
        if (prev) go(prev, prev.members.find(m => m.best) || prev.members[0]);
        break;
    }
    case 'Home':
        go(series, series.members[0]);
        break;
    case 'End':
        go(series, series.members[series.members.length - 1]);
        break;
    case ' ':
        seriesToggleKeep(series, series.members[pos].id);
        break;
    case 'Enter':
        seriesConfirm(series);
        break;
    case 's':
        seriesSkip(series);
        break;
    case 'a':
        seriesKeepAll(series);
        break;
    default:
        return;
    }
    e.preventDefault();
    e.stopPropagation();
}

// ==================== BESLISSEN ====================

function seriesToggleKeep(series, id) {
    const keep = seriesState.keep.get(series.key);
    if (keep.has(id)) {
        if (keep.size === 1) {
            showToast('Minstens één foto blijft', 'Kies eerst een andere foto om te houden, of sla de reeks over.', 'info');
            return;
        }
        keep.delete(id);
    } else {
        keep.add(id);
    }
    seriesUpdateCard(series);
}

// Een reeks is afgehandeld: weg uit de lijst, de cursor naar de volgende (of de vorige)
function seriesDone(series) {
    const index = seriesState.items.indexOf(series);
    const hadFocus = !!(document.activeElement && document.activeElement.closest('#series-list'));
    seriesState.items.splice(index, 1);
    seriesState.keep.delete(series.key);
    seriesState.total = Math.max(0, seriesState.total - 1);
    seriesState.photos = Math.max(0, seriesState.photos - series.count);
    const next = seriesState.items[index] || seriesState.items[index - 1];
    if (next) seriesState.cursor = { key: next.key, id: next.best };
    seriesRender();
    if (seriesState.items.length < 5 && seriesState.nextOffset !== null && !seriesState.loading) seriesLoad(false);
    if (hadFocus) seriesFocusCursor();
}

function seriesSkip(series) {
    seriesState.skipped.add(series.key);
    seriesDone(series);
}

async function seriesKeepAll(series) {
    if (seriesState.busy) return;
    seriesState.busy = true;
    try {
        await fetchJSON(`${API_BASE}/api/series/keep-all`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ members: series.members.map(m => m.id) }),
        });
        seriesDone(series);
        if (seriesState.status) seriesState.status.reviewed += 1;
        seriesRenderStatus();
        showToast('Alles behouden', `${series.count} foto's blijven staan. Deze reeks komt niet meer terug, tenzij hij verandert.`, 'success');
    } catch (err) {
        toastFetchError('Behouden mislukt', err);
    } finally {
        seriesState.busy = false;
    }
}

function seriesConfirm(series) {
    if (seriesState.busy) return;
    const keep = seriesState.keep.get(series.key);
    const move = series.members.filter(m => !keep.has(m.id));
    if (!move.length) {
        seriesKeepAll(series);
        return;
    }
    const kept = series.members.filter(m => keep.has(m.id));
    const copies = move.reduce((n, m) => n + (m.copies ? m.copies.length : 0), 0);
    const files = move.length + copies;
    const bytes = move.reduce((n, m) => n + m.size * (1 + (m.copies ? m.copies.length : 0)), 0);
    seriesState.confirm = { series, keep: kept.map(m => m.id) };
    const destination = seriesState.status ? seriesState.status.holding_folder : null;
    document.getElementById('series-confirm-title').textContent =
        `${files} ${files === 1 ? 'foto' : 'foto\'s'} uit deze reeks verplaatsen?`;
    const list = document.getElementById('series-confirm-list');
    list.replaceChildren(...[
        createEl('li', {},
            `${files} ${files === 1 ? 'bestand gaat' : 'bestanden gaan'} (${formatBytes(bytes)}) naar `,
            createEl('span', { class: 'path-label', title: destination || '', text: 'RAW_DUPLICATES/near-duplicates' }),
            ', in dezelfde mappen als nu.'),
        copies ? createEl('li', { text: `Daarvan ${copies} exacte ${copies === 1 ? 'kopie' : 'kopieën'} van een foto die weggaat.` }) : null,
        createEl('li', { class: 'dup-confirm-keep' },
            `${kept.length === 1 ? 'Blijft' : 'Blijven'}: `,
            ...kept.flatMap((m, i) => [i ? ', ' : null,
                createEl('span', { class: 'path-label', title: m.path, text: m.name })])),
        createEl('li', { text: 'Er wordt niets gewist. In het Herstelcentrum zet je deze reeks in één keer terug.' }),
    ].filter(Boolean));
    const go = document.getElementById('btn-series-go');
    go.textContent = `Verplaats ${files} ${files === 1 ? 'foto' : 'foto\'s'}`;
    go.disabled = false;
    document.getElementById('modal-series-confirm').classList.add('active');
}

function seriesCloseConfirm() {
    if (seriesState.busy) return;
    document.getElementById('modal-series-confirm').classList.remove('active');
    seriesState.confirm = null;
}

async function seriesRunConfirm() {
    const pending = seriesState.confirm;
    if (!pending || seriesState.busy) return;
    const go = document.getElementById('btn-series-go');
    seriesState.busy = true;
    go.disabled = true;
    go.classList.add('is-loading');
    try {
        const result = await fetchJSON(`${API_BASE}/api/series/keep`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ members: pending.series.members.map(m => m.id), keep: pending.keep }),
        });
        seriesState.busy = false;
        seriesCloseConfirm();
        const moved = result.moved.length;
        if (result.failed.length) {
            showToast(`${moved} verplaatst, ${result.failed.length} niet`,
                result.failed.map(f => `${f.path ? f.path.split('/').pop() : 'foto'}: ${f.reason}`).join('\n'), 'warning');
            seriesLoad(true);
        } else {
            showToast(`${moved} ${moved === 1 ? 'foto' : 'foto\'s'} verplaatst`,
                'Ze staan in RAW_DUPLICATES/near-duplicates. Terugzetten kan in het Herstelcentrum.', 'success');
            seriesDone(pending.series);
        }
        seriesLoadStatus();
    } catch (err) {
        seriesState.busy = false;
        go.disabled = false;
        toastFetchError('Verplaatsen mislukt', err);
        if (err && err.status === 409) {
            seriesCloseConfirm();
            seriesLoad(true);
        }
    } finally {
        go.classList.remove('is-loading');
    }
}

async function seriesResetReviews() {
    try {
        const data = await fetchJSON(`${API_BASE}/api/series/reviews/reset`, { method: 'POST' });
        showToast('Reeksen weer zichtbaar', `${data.shown_again} ${data.shown_again === 1 ? 'reeks staat' : 'reeksen staan'} weer in de lijst.`, 'success');
        seriesLoadStatus();
        seriesLoad(true);
    } catch (err) {
        toastFetchError('Terugzetten mislukt', err);
    }
}

function seriesInitPage() {
    document.querySelector('.dup-tabs').addEventListener('keydown', dupTabsKeydown);
    document.getElementById('series-list').addEventListener('keydown', seriesKeydown);
    registerOverlayCloser('modal-series-confirm', seriesCloseConfirm);
    addShortcutGroupLater();
    // Na het terugzetten in het Herstelcentrum of een nieuwe vergelijking: opnieuw laden bij terugkomst
    if (typeof jobsOnFinished === 'function') {
        jobsOnFinished(job => {
            if (job.type === 'index' && seriesState.loaded) seriesLoadStatus();
        });
    }
}

// shortcuts.js laadt na dit script: de groep pas toevoegen als de pagina klaar is
function addShortcutGroupLater() {
    const add = () => {
        if (typeof addShortcutGroup !== 'function') return;
        addShortcutGroup('Beste uit reeks (Duplicaten)', [
            [['←', '→'], 'Vorige of volgende foto in de reeks'],
            [['↑', '↓'], 'Vorige of volgende reeks'],
            [['Spatie'], 'Foto houden of laten gaan'],
            [['Enter'], 'Reeks bevestigen (met een vraag vooraf)'],
            [['s'], 'Reeks overslaan'],
            [['a'], 'Alles behouden'],
        ]);
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add, { once: true });
    else add();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', seriesInitPage, { once: true });
else seriesInitPage();
