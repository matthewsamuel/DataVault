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
// behouden, v groot vergelijken. Overslaan geldt voor deze sessie; 'Alles behouden' onthoudt
// de server (en is meteen ongedaan te maken).
//
// Paging: een bevestigde of behouden reeks verdwijnt ook op de server, dus schuift de rest daar
// een plaats op. nextOffset gaat dan één omlaag (seriesState.gone telt mee met een verzoek dat
// al onderweg is); een overgeslagen reeks blijft op de server en telt gewoon mee.

const SERIES_PAGE = 20;
const SERIES_STRICTNESS = ['streng', 'normaal', 'ruim'];
const SERIES_STRICTNESS_LABELS = { streng: 'Streng', normaal: 'Normaal', ruim: 'Ruim' };
const SERIES_STRICTNESS_HELP = {
    streng: 'Streng: alleen vrijwel dezelfde foto (een verkleinde of opnieuw opgeslagen kopie) en bursts met bijna geen beweging.',
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
    total: 0,              // reeksen die de server nog heeft (ook de overgeslagen)
    photos: 0,
    gone: 0,               // reeksen die in deze sessie op de server verdwenen (bevestigd of behouden)
    handled: 0,            // daarvan sinds het openen van het tabblad: voor 'Alles bekeken'
    moved: 0,              // en daarvan met verplaatste foto's
    lastKept: null,        // {key, title}: de laatste 'Alles behouden', ongedaan te maken
    job: null,             // de vergelijking die nu loopt (voor het lege scherm)
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

// Bij elk bezoek opnieuw (iets kan in het Herstelcentrum teruggezet zijn); wat je per reeks
// koos, blijft staan zolang de reeks dezelfde is
function seriesInit() {
    seriesSyncControls();
    seriesState.handled = seriesState.moved = 0;
    seriesLoadStatus();
    seriesLoad(true);
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
        // Het lege scherm hangt af van de stand (nog niet vergeleken, of echt niets gevonden)
        if (seriesState.loaded && !seriesState.items.length && !seriesState.loading) seriesRender();
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
    const label = document.getElementById('series-compare-label');
    const wasRunning = !!seriesState.job;
    seriesState.job = running ? job : null;
    if (running) {
        const pct = job.total ? Math.round(job.processed / job.total * 100) : null;
        text.textContent = seriesJobText(job);
        progress.hidden = false;
        progress.classList.toggle('is-indeterminate', pct === null);
        document.getElementById('series-progress-fill').style.width = pct === null ? '' : `${pct}%`;
        // Bezig: de knop zegt het (de balk ernaast laat de voortgang zien)
        button.disabled = true;
        label.textContent = 'Bezig met vergelijken…';
        seriesUpdateEmptyProgress();
        return;
    }
    progress.hidden = true;
    button.disabled = false;
    label.textContent = 'Foto\'s vergelijken';
    if (wasRunning && seriesState.loaded && !seriesState.items.length) seriesRender();
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
    if (button.disabled && seriesState.job) return;
    button.disabled = true;
    try {
        // (auto: een stille ronde, zonder eigen melding; dit scherm toont de voortgang)
        const index = await jobsStart('index', { auto: true });
        seriesRenderStatus({ id: index.id, status: 'running', message: 'Bibliotheek bijwerken…', total: null });
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

function seriesJobText(job) {
    return job.status === 'waiting_for_vault' ? 'Wacht tot het archief terug is…'
        : (job.message || 'Foto\'s vergelijken…');
}

// Terwijl er vergeleken wordt en er nog niets te tonen is: het lege scherm zegt dat, zonder
// tweede knop (de voortgang staat erboven)
function seriesUpdateEmptyProgress() {
    if (!seriesState.loaded || seriesState.items.length) return;
    if (!document.getElementById('series-empty-progress')) seriesRender();
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
    if (changed) {
        seriesState.handled = seriesState.moved = 0;
        seriesLoad(true);
    }
}

function seriesSetSort(sort) {
    if (sort !== 'size' && sort !== 'recent') return;
    seriesState.sort = sort;
    seriesState.handled = seriesState.moved = 0;
    try { localStorage.setItem('seriesSort', sort); } catch (e) { /* geen opslag */ }
    seriesLoad(true);
}

// ==================== LADEN ====================

// Geeft true terug als er reeksen bij kwamen (of de lijst opnieuw begon)
async function seriesLoad(reset) {
    const seq = ++seriesState.seq;
    const list = document.getElementById('series-list');
    if (reset) {
        seriesState.items = [];
        seriesState.nextOffset = 0;
        if (!seriesState.loaded) list.replaceChildren(skeletonRows(3));
    }
    if (seriesState.nextOffset === null) return false;
    seriesState.loading = true;
    seriesMoreBusy(true);
    const goneBefore = seriesState.gone;
    const params = new URLSearchParams({
        strictness: seriesState.strictness, sort: seriesState.sort,
        offset: String(seriesState.nextOffset), limit: String(SERIES_PAGE),
    });
    try {
        const data = await fetchJSON(`${API_BASE}/api/series?${params}`);
        if (seq !== seriesState.seq) return false;
        const before = seriesState.items.length;
        const fresh = data.items.filter(s => !seriesState.skipped.has(s.key)
            && !seriesState.items.some(t => t.key === s.key));
        fresh.forEach(s => {
            const chosen = seriesState.keep.get(s.key);
            const still = chosen && [...chosen].every(id => s.members.some(m => m.id === id));
            if (!still) seriesState.keep.set(s.key, new Set([s.best]));
        });
        seriesState.items.push(...fresh);
        // Wat tijdens dit verzoek op de server verdween, schoof de rest daar al op
        const goneSince = seriesState.gone - goneBefore;
        seriesState.nextOffset = data.next_offset === null ? null : Math.max(0, data.next_offset - goneSince);
        seriesState.total = Math.max(0, data.total - goneSince);
        seriesState.photos = data.photos;
        seriesState.loaded = true;
        seriesState.loading = false;
        // (bijna) alles op deze pagina was overgeslagen: meteen de volgende
        const more = seriesState.items.length < 5 && seriesState.nextOffset !== null;
        if (!more || seriesState.items.length) seriesRender();
        if (more) await seriesLoad(false);
        return seriesState.items.length > before || reset;
    } catch (err) {
        if (seq !== seriesState.seq) return;
        list.replaceChildren(errorState('Reeksen laden mislukt', err, () => seriesLoad(true)));
        document.getElementById('series-summary').hidden = true;
        return false;
    } finally {
        if (seq === seriesState.seq) {
            seriesState.loading = false;
            seriesMoreBusy(false);
        }
    }
}

function seriesMoreBusy(busy) {
    const more = document.getElementById('btn-series-more');
    if (!more) return;
    more.disabled = busy;
    more.classList.toggle('is-loading', busy);
}

// Komt het einde van de lijst in beeld, dan laadt de volgende pagina vanzelf
let seriesMoreObserver = null;

function seriesWatchMore(button) {
    if (!('IntersectionObserver' in window)) return;
    if (!seriesMoreObserver) {
        seriesMoreObserver = new IntersectionObserver(entries => {
            if (entries.some(e => e.isIntersecting) && !seriesState.loading && seriesState.nextOffset !== null
                && !document.getElementById('dup-panel-series').hidden) seriesLoad(false);
        }, { rootMargin: '0px 0px 400px 0px' });
    }
    seriesMoreObserver.disconnect();
    seriesMoreObserver.observe(button);
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

function seriesNotCompared(status) {
    return !!(status && status.ready && !status.analysed && status.pending);
}

function seriesSummaryText(status) {
    if (seriesNotCompared(status)) return '';
    const { total, photos } = seriesState;
    const skipped = seriesState.skipped.size;
    if (!total) return 'Geen reeksen';
    const parts = [`${formatNumberNl(total)} ${total === 1 ? 'reeks' : 'reeksen'}`, `${formatNumberNl(photos)} foto's`];
    if (skipped) parts.push(`${formatNumberNl(skipped)} overgeslagen`);
    return parts.join(' · ');
}

function seriesRender() {
    const list = document.getElementById('series-list');
    const summary = document.getElementById('series-summary');
    const status = seriesState.status;
    const shown = seriesState.items.length;
    // Stond de focus in de lijst, dan komt hij na het opnieuw tekenen terug op de cursor
    const hadFocus = list.contains(document.activeElement);
    summary.hidden = false;
    document.getElementById('series-summary-text').textContent = seriesSummaryText(status);
    seriesRenderLastKept();
    // Eén hoofdknop: vóór de eerste vergelijking staat hij in het lege scherm, niet ook bovenin
    document.getElementById('btn-series-compare').hidden = !shown && seriesNotCompared(status) && !seriesState.job;
    if (!shown) {
        list.replaceChildren(seriesEmptyState(status));
        if (hadFocus) seriesFocusCursor();
        return;
    }
    const cards = seriesState.items.map(series => seriesCard(series));
    list.replaceChildren(...cards);
    if (seriesState.nextOffset !== null) {
        const more = createEl('button', {
            class: 'btn btn-secondary', id: 'btn-series-more', text: 'Meer reeksen laden',
            onClick: () => seriesLoad(false),
        });
        list.appendChild(createEl('div', { class: 'series-more' }, more));
        seriesWatchMore(more);
    }
    seriesEnsureCursor();
    if (hadFocus) seriesFocusCursor(false);
}

function seriesEmptyState(status) {
    if (status && !status.ready) {
        return emptyState({
            icon: 'fa-hourglass-half', title: 'De bibliotheek wordt nog opgebouwd',
            text: 'Zodra alle foto\'s bekend zijn, kun je ze hier vergelijken.',
        });
    }
    if (seriesState.job && (!status || !status.analysed || !seriesState.total)) {
        // Er wordt vergeleken: geen knop; de voortgang staat erboven
        const state = emptyState({
            icon: 'fa-hourglass-half', title: 'Foto\'s worden vergeleken…',
            text: 'Dat gebeurt op de achtergrond; je kunt intussen verder. De reeksen verschijnen hier zodra het klaar is.',
        });
        state.id = 'series-empty-progress';
        return state;
    }
    if (seriesNotCompared(status)) {
        return emptyState({
            icon: 'fa-layer-group', title: 'Nog niet vergeleken',
            text: 'Vergelijk je foto\'s één keer; daarna gaat het alleen nog om nieuwe of veranderde foto\'s.',
            action: { label: 'Foto\'s vergelijken', onClick: seriesStartCompare, primary: true },
        });
    }
    if (seriesState.skipped.size && seriesState.total) {
        return emptyState({
            icon: 'fa-forward', title: 'Alle reeksen overgeslagen',
            text: seriesState.handled
                ? `${formatNumberNl(seriesState.handled)} ${seriesState.handled === 1 ? 'reeks' : 'reeksen'} afgehandeld; de overgeslagen reeksen wachten nog.`
                : 'Overgeslagen reeksen blijven voor later staan.',
            action: { label: 'Overgeslagen tonen', primary: true, onClick: seriesShowSkipped },
        });
    }
    if (seriesState.handled) {
        const n = `${formatNumberNl(seriesState.handled)} ${seriesState.handled === 1 ? 'reeks' : 'reeksen'}`;
        return emptyState({
            icon: 'fa-circle-check', title: 'Alles bekeken',
            text: seriesState.moved
                ? `Je hebt ${n} afgehandeld. Wat je verplaatste, staat in het Herstelcentrum en kan daar terug.`
                : `Je hebt ${n} afgehandeld en alles behouden. Met \u2018Behouden reeksen weer tonen\u2019 zie je ze opnieuw.`,
            action: seriesState.moved ? { label: 'Naar het Herstelcentrum', onClick: () => goToView('recovery') } : null,
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

function seriesShowSkipped() {
    seriesState.skipped.clear();
    seriesLoad(true).then(() => seriesFocusCursor());
}

// 'Alles behouden' is meteen ongedaan te maken, tot de volgende keer
function seriesRenderLastKept() {
    const slot = document.getElementById('series-last-kept');
    const last = seriesState.lastKept;
    slot.hidden = !last;
    if (!last) {
        slot.replaceChildren();
        return;
    }
    slot.replaceChildren(
        createEl('i', { class: 'fas fa-check', 'aria-hidden': 'true' }),
        createEl('span', { text: `Behouden: ${last.title}` }),
        createEl('button', { type: 'button', class: 'link-button', id: 'btn-series-undo-keep', text: 'Ongedaan maken', onClick: seriesUndoKeepAll }));
}

// Kleine reeksen krijgen grotere foto's: twee foto's van 156 px vergelijk je niet
const SERIES_TILE = { 2: 340, 3: 300, 4: 260 };

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
                    class: 'btn btn-ghost', title: 'De foto\'s groot naast elkaar (v)', onClick: () => seriesOpenCompare(series),
                }, createEl('i', { class: 'fas fa-magnifying-glass-plus', 'aria-hidden': 'true' }), 'Groot bekijken'),
                createEl('button', {
                    class: 'btn btn-ghost', title: 'Deze reeks nu overslaan (s)', onClick: () => seriesSkip(series),
                }, 'Overslaan'),
                createEl('button', {
                    class: 'btn btn-secondary series-keepall-btn', title: 'Alle foto\'s blijven; de reeks komt niet terug (a)',
                    onClick: () => seriesKeepAll(series),
                }, 'Alles behouden'),
                createEl('button', {
                    class: 'btn btn-primary series-confirm-btn', title: 'Bevestigen (Enter)',
                    onClick: () => seriesConfirm(series),
                }, `Verplaats ${moving.length}`))),
        createEl('div', { class: 'series-photos', role: 'group', 'aria-label': `Foto's: ${seriesTitle(series)}` },
            ...series.members.map(member => seriesPhoto(series, member, keep.has(member.id)))),
        createEl('div', { class: 'series-detail', 'aria-live': 'polite' }));
    const tile = SERIES_TILE[series.members.length];
    if (tile) {
        card.style.setProperty('--series-tile', `${tile}px`);
        card.style.setProperty('--series-count', String(series.members.length));
    }
    card.classList.toggle('is-small', !!tile);
    seriesSyncActions(card, moving.length);
    return card;
}

// Niets te verplaatsen: dan is 'Alles behouden' de hoofdactie, en er staat geen tweede knop
// met dezelfde naam naast
function seriesSyncActions(card, moving) {
    const confirm = card.querySelector('.series-confirm-btn');
    const keepAll = card.querySelector('.series-keepall-btn');
    confirm.hidden = !moving;
    confirm.textContent = `Verplaats ${moving}`;
    keepAll.classList.toggle('btn-primary', !moving);
    keepAll.classList.toggle('btn-secondary', !!moving);
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
    seriesSyncActions(card, series.members.length - keep.size);
    seriesRenderDetail(series);
    if (seriesCompareState.series === series) seriesRenderCompare();
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

function seriesFocusCursor(scroll = true) {
    const card = seriesCardEl(seriesState.cursor.key);
    const tile = card && [...card.querySelectorAll('.series-photo')].find(t => t.dataset.id === String(seriesState.cursor.id));
    if (tile) {
        tile.focus({ preventScroll: !scroll });
        if (scroll) tile.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    } else {
        const list = document.getElementById('series-list');
        const fallback = list.querySelector('.empty-state-action, #btn-series-more');
        (fallback || document.getElementById('btn-series-compare')).focus();
    }
}

function seriesKeydown(e) {
    if (e.metaKey || e.ctrlKey || e.altKey || anyOverlayOpen()) return;
    // 'g' en dan een letter wisselt van pagina (shortcuts.js): die letter is niet voor de reeks
    if (typeof shortcutGPending !== 'undefined' && shortcutGPending) return;
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
        else if (seriesState.nextOffset !== null) seriesDownIntoNextPage(series.key);
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
    case 'v':
        seriesOpenCompare(series, series.members[pos].id);
        break;
    default:
        return;
    }
    e.preventDefault();
    e.stopPropagation();
}

// ↓ op de laatste geladen reeks: de volgende pagina laden en daar verder
async function seriesDownIntoNextPage(key) {
    if (!seriesState.loading) await seriesLoad(false);
    const index = seriesState.items.findIndex(s => s.key === key);
    const next = seriesState.items[index + 1];
    if (!next) return;
    seriesMoveCursor(next.key, (next.members.find(m => m.best) || next.members[0]).id, false);
    seriesFocusCursor();
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

// Een reeks is afgehandeld: weg uit de lijst, de cursor naar de volgende (of de vorige).
// gone: de server heeft hem niet meer (verplaatst of behouden), dus schuift de rest daar op;
// een overgeslagen reeks staat er nog wel.
function seriesDone(series, gone) {
    const index = seriesState.items.indexOf(series);
    if (index < 0) return;
    const hadFocus = !!(document.activeElement && document.activeElement.closest('#series-list'));
    seriesState.items.splice(index, 1);
    seriesState.keep.delete(series.key);
    if (gone) {
        seriesState.gone += 1;
        seriesState.handled += 1;
        if (seriesState.nextOffset !== null) seriesState.nextOffset = Math.max(0, seriesState.nextOffset - 1);
        seriesState.total = Math.max(0, seriesState.total - 1);
        seriesState.photos = Math.max(0, seriesState.photos - series.count);
    }
    const next = seriesState.items[index] || seriesState.items[index - 1];
    if (next) seriesState.cursor = { key: next.key, id: next.best };
    seriesRender();
    if (seriesState.items.length < 5 && seriesState.nextOffset !== null && !seriesState.loading) seriesLoad(false);
    if (hadFocus) seriesFocusCursor();
}

function seriesSkip(series) {
    seriesState.skipped.add(series.key);
    seriesDone(series, false);
}

async function seriesKeepAll(series) {
    if (seriesState.busy) return;
    seriesState.busy = true;
    try {
        const data = await fetchJSON(`${API_BASE}/api/series/keep-all`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ members: series.members.map(m => m.id) }),
        });
        seriesState.lastKept = { key: data.key, title: seriesTitle(series) };
        seriesDone(series, true);
        if (seriesState.status) seriesState.status.reviewed += 1;
        seriesRenderStatus();
        showToast('Alles behouden', `${series.count} foto's blijven staan. Deze reeks komt niet meer terug, tenzij hij verandert; `
            + 'met \u2018Ongedaan maken\u2019 boven de lijst staat hij er meteen weer.', 'success');
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
            seriesState.moved += 1;
            seriesDone(pending.series, true);
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

// ==================== GROOT VERGELIJKEN ====================
//
// De foto's van één reeks groot naast elkaar (v, of 'Groot bekijken'). 'Passend' toont elke foto
// heel; '100%' toont dezelfde uitsnede van elke foto op de pixelgrootte van de grootste, zodat
// een verkleinde of wazige kopie echt zachter oogt. Slepen verschuift de uitsnede van alle foto's
// tegelijk. Houden of laten gaan kan hier ook; de kaart in de lijst loopt mee.

const seriesCompareState = { series: null, current: null, detail: false, focus: { x: 50, y: 50 }, drag: null };

function seriesOpenCompare(series, id) {
    seriesCompareState.series = series;
    seriesCompareState.current = id || (seriesState.cursor.key === series.key ? seriesState.cursor.id : series.best);
    seriesCompareState.focus = { x: 50, y: 50 };
    document.getElementById('series-compare-title').textContent = seriesTitle(series);
    const grid = document.getElementById('series-compare-grid');
    grid.style.setProperty('--compare-columns', String(Math.min(series.members.length, 3)));
    grid.replaceChildren(...series.members.map(member => seriesComparePane(series, member)));
    seriesCompareZoom(seriesCompareState.detail);
    seriesRenderCompare();
    document.getElementById('modal-series-compare').classList.add('active');
    // De focus op de knop van de foto onder de cursor (niet op de eerste knop van het venster)
    setTimeout(() => seriesCompareFocus(seriesCompareState.current), 0);
}

function seriesCloseCompare() {
    document.getElementById('modal-series-compare').classList.remove('active');
    document.getElementById('series-compare-grid').replaceChildren();
    seriesCompareState.series = null;
}

function seriesComparePane(series, member) {
    const img = createEl('img', { class: 'series-compare-img loading', alt: member.name, draggable: 'false' });
    img.addEventListener('load', () => img.classList.remove('loading'), { once: true });
    // Kan de browser het origineel niet tonen (HEIC buiten Safari), dan de miniatuur
    img.addEventListener('error', () => {
        if (img.dataset.fallback) { img.classList.remove('loading'); return; }
        img.dataset.fallback = '1';
        img.src = vaultFileUrl('thumb', member.path);
    });
    img.src = vaultFileUrl('file', member.path);
    const frame = createEl('div', { class: 'series-compare-frame' },
        img,
        member.best ? createEl('span', { class: 'series-best-badge' },
            createEl('i', { class: 'fas fa-star', 'aria-hidden': 'true' }), 'Beste') : null);
    frame.addEventListener('pointerdown', e => seriesCompareDragStart(e, frame));
    const facts = [
        member.width && member.height ? `${member.width} × ${member.height}` : null,
        formatBytes(member.size),
        member.camera,
    ].filter(Boolean).join(' · ');
    return createEl('div', { class: 'series-compare-pane', dataset: { id: String(member.id) } },
        frame,
        createEl('div', { class: 'series-compare-info' },
            createEl('div', { class: 'series-compare-head' },
                createEl('span', { class: 'series-compare-name', title: member.path, text: member.name }),
                createEl('span', { class: 'badge series-compare-state' })),
            createEl('div', { class: 'series-compare-facts', text: facts }),
            createEl('div', { class: 'series-compare-score' },
                createEl('span', { class: 'series-compare-score-total', text: `Score ${member.score}` }),
                ...SERIES_POINTS.map(([key, label, max], i) => createEl('span', { class: 'series-detail-part' },
                    createEl('span', { class: `series-score-dot series-score-part-${i + 1}`, 'aria-hidden': 'true' }),
                    `${label} ${member.breakdown[key]}/${max}`)))),
        createEl('button', {
            type: 'button', class: 'btn series-compare-keep',
            onClick: () => { seriesCompareState.current = member.id; seriesToggleKeep(series, member.id); },
            onFocus: () => seriesCompareSetCurrent(member.id),
        }));
}

// Na elke wijziging: wat blijft, wat gaat, en de knop onderin
function seriesRenderCompare() {
    const series = seriesCompareState.series;
    if (!series) return;
    const keep = seriesState.keep.get(series.key);
    if (!keep) return;
    document.querySelectorAll('#series-compare-grid .series-compare-pane').forEach(pane => {
        const member = series.members.find(m => m.id === Number(pane.dataset.id));
        const kept = keep.has(member.id);
        pane.classList.toggle('is-kept', kept);
        pane.classList.toggle('is-current', member.id === seriesCompareState.current);
        const state = pane.querySelector('.series-compare-state');
        state.className = `badge series-compare-state ${kept ? 'badge-accent' : 'badge-warning'}`;
        state.textContent = kept ? 'Blijft' : 'Gaat weg';
        const button = pane.querySelector('.series-compare-keep');
        button.classList.toggle('btn-primary', kept);
        button.classList.toggle('btn-secondary', !kept);
        button.setAttribute('aria-pressed', kept ? 'true' : 'false');
        button.setAttribute('aria-label', `${member.name} houden`);
        button.replaceChildren(createEl('i', { class: `fas ${kept ? 'fa-check' : 'fa-plus'}`, 'aria-hidden': 'true' }),
            kept ? 'Blijft' : 'Houden');
    });
    const moving = series.members.length - keep.size;
    const go = document.getElementById('btn-series-compare-go');
    go.textContent = moving ? `Verplaats ${moving}` : 'Alles behouden';
}

function seriesCompareSetCurrent(id) {
    seriesCompareState.current = id;
    document.querySelectorAll('#series-compare-grid .series-compare-pane').forEach(pane => {
        pane.classList.toggle('is-current', pane.dataset.id === String(id));
    });
}

function seriesCompareFocus(id) {
    const pane = [...document.querySelectorAll('#series-compare-grid .series-compare-pane')].find(p => p.dataset.id === String(id));
    if (!pane) return;
    pane.querySelector('.series-compare-keep').focus({ preventScroll: true });
    pane.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

// Passend of 100%: op 100% krijgt elke foto de breedte (in beeldpunten) van de grootste
function seriesCompareZoom(detail) {
    seriesCompareState.detail = !!detail;
    const series = seriesCompareState.series;
    document.getElementById('btn-series-fit').setAttribute('aria-pressed', detail ? 'false' : 'true');
    document.getElementById('btn-series-detail').setAttribute('aria-pressed', detail ? 'true' : 'false');
    const grid = document.getElementById('series-compare-grid');
    grid.classList.toggle('is-detail', !!detail);
    if (!series) return;
    const widest = Math.max(...series.members.map(m => m.width || 0)) || 1600;
    grid.style.setProperty('--compare-detail-width', `${Math.round(widest / (window.devicePixelRatio || 1))}px`);
    seriesComparePan();
}

function seriesComparePan() {
    const { x, y } = seriesCompareState.focus;
    const grid = document.getElementById('series-compare-grid');
    grid.style.setProperty('--compare-x', `${x}%`);
    grid.style.setProperty('--compare-y', `${y}%`);
}

// Slepen op 100%: de uitsnede van alle foto's schuift mee
function seriesCompareDragStart(e, frame) {
    if (!seriesCompareState.detail || e.button !== 0) return;
    const img = frame.querySelector('img');
    const rect = img.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    e.preventDefault();
    const start = { px: e.clientX, py: e.clientY, ...seriesCompareState.focus };
    frame.setPointerCapture(e.pointerId);
    frame.classList.add('is-dragging');
    const move = ev => {
        const clamp = v => Math.max(0, Math.min(100, v));
        seriesCompareState.focus = {
            x: clamp(start.x - (ev.clientX - start.px) / rect.width * 100),
            y: clamp(start.y - (ev.clientY - start.py) / rect.height * 100),
        };
        seriesComparePan();
    };
    const end = () => {
        frame.classList.remove('is-dragging');
        frame.removeEventListener('pointermove', move);
        frame.removeEventListener('pointerup', end);
        frame.removeEventListener('pointercancel', end);
    };
    frame.addEventListener('pointermove', move);
    frame.addEventListener('pointerup', end);
    frame.addEventListener('pointercancel', end);
}

function seriesCompareKeydown(e) {
    if (e.metaKey || e.ctrlKey || e.altKey || !seriesCompareState.series) return;
    const series = seriesCompareState.series;
    const pos = series.members.findIndex(m => m.id === seriesCompareState.current);
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        const next = series.members[pos + (e.key === 'ArrowRight' ? 1 : -1)];
        if (next) seriesCompareFocus(next.id);
    } else if (e.key === 'Home' || e.key === 'End') {
        seriesCompareFocus(series.members[e.key === 'Home' ? 0 : series.members.length - 1].id);
    } else if (e.key === 'd') {
        seriesCompareZoom(!seriesCompareState.detail);
    } else {
        return;
    }
    e.preventDefault();
    e.stopPropagation();
}

// Verplaatsen vanuit het venster: eerst dicht, dan dezelfde vraag als in de lijst
function seriesCompareConfirm() {
    const series = seriesCompareState.series;
    if (!series) return;
    seriesCloseCompare();
    seriesConfirm(series);
}

async function seriesUndoKeepAll() {
    const last = seriesState.lastKept;
    if (!last) return;
    try {
        await fetchJSON(`${API_BASE}/api/series/reviews/undo`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: last.key }),
        });
        seriesState.lastKept = null;
        seriesState.handled = Math.max(0, seriesState.handled - 1);
        showToast('Weer in de lijst', `${last.title} staat weer tussen de reeksen.`, 'success');
        seriesLoadStatus();
        await seriesLoad(true);
        const back = seriesState.items.find(s => s.key === last.key);
        if (back) {
            seriesMoveCursor(back.key, back.best, false);
            seriesFocusCursor();
        }
    } catch (err) {
        toastFetchError('Ongedaan maken mislukt', err);
    }
}

async function seriesResetReviews() {
    try {
        const data = await fetchJSON(`${API_BASE}/api/series/reviews/reset`, { method: 'POST' });
        showToast('Reeksen weer zichtbaar', `${data.shown_again} ${data.shown_again === 1 ? 'reeks staat' : 'reeksen staan'} weer in de lijst.`, 'success');
        seriesState.lastKept = null;
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
    registerOverlayCloser('modal-series-compare', seriesCloseCompare);
    document.getElementById('modal-series-compare').addEventListener('keydown', seriesCompareKeydown);
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
            [['Home', 'End'], 'Eerste of laatste foto in de reeks'],
            [['spatie'], 'Foto houden of laten gaan'],
            [['v'], 'Groot vergelijken (ook op 100%)'],
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
