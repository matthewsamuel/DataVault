// DataVault Dashboard - Achtergrondtaken (takenpaneel rechtsonder)
// Note: API_BASE is defined in app.js
//
// Zware klussen (duplicaten zoeken, controlegetallen berekenen, miniaturen maken) lopen op de
// server als taak (/api/jobs). Dit paneel toont ze met voortgang, resterende tijd en knoppen om
// te pauzeren, hervatten en annuleren. Alles komt van de server, dus na herladen staat alles er
// gewoon weer. Andere schermen volgen een taak met jobsWatch(id, fn) en starten er een met
// jobsStart(type, params).
//
// Onderaan staat altijd de regel van de bibliotheek (de media-index): hoeveel bestanden erin staan,
// wanneer hij bijgewerkt is, en de knop 'Bibliotheek bijwerken' (een volledige controle). De app
// werkt de bibliotheek ook zelf bij (bij het starten, als de schijf terug is); zo'n automatische
// ronde staat alleen in het paneel zolang hij loopt en geeft geen melding als hij gewoon klaar is.

const JOB_STATUS_LABELS = {
    queued: 'In de wachtrij',
    running: 'Bezig',
    paused: 'Gepauzeerd',
    waiting_for_vault: 'Wacht op archief',
    cancelled: 'Geannuleerd',
    failed: 'Mislukt',
    done: 'Klaar',
    interrupted: 'Onderbroken',
};
const JOB_ACTIVE = new Set(['queued', 'running', 'waiting_for_vault']);
const JOB_POLL_ACTIVE_MS = 1000;
const JOB_POLL_IDLE_MS = 15000;
const JOB_SHOW_FINISHED_MS = 30 * 60 * 1000;  // afgeronde taken blijven een half uur zichtbaar

const jobDock = {
    jobs: [],
    known: new Map(),        // id -> status bij de vorige keer ophalen
    watchers: new Map(),     // id -> Set van functies(job)
    finishedHandlers: [],    // functies(job) als een taak deze sessie klaar, mislukt of gestopt is
    changeHandlers: [],      // functies(job) bij elke opgehaalde stand van elke taak
    timer: null,
    collapsed: false,        // ingeklapt door de gebruiker terwijl er iets loopt (bewaard)
    idleOpen: false,         // zonder iets dat aandacht vraagt is het paneel dicht, tenzij hier open
    showFinished: false,     // de eerder afgeronde taken uitgevouwen
    dismissed: new Set(),
    error: null,
    library: null,           // /api/library/status, of null zolang onbekend
    libraryLoading: false,
};

try {
    jobDock.collapsed = localStorage.getItem('jobDockCollapsed') === '1';
    JSON.parse(localStorage.getItem('jobDockDismissed') || '[]').forEach(id => jobDock.dismissed.add(id));
} catch (e) { /* geen opslag: alles zichtbaar */ }

function saveJobDockPrefs() {
    try {
        localStorage.setItem('jobDockCollapsed', jobDock.collapsed ? '1' : '0');
        localStorage.setItem('jobDockDismissed', JSON.stringify([...jobDock.dismissed].slice(-100)));
    } catch (e) { /* geen opslag */ }
}

// Gooit een ApiError (api.js) met de melding van de server; meld hem met toastFetchError
function jobsRequest(url, options) {
    return fetchJSON(`${API_BASE}${url}`, options);
}

async function jobsStart(type, params) {
    const data = await jobsRequest('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, params }),
    });
    jobDock.dismissed.delete(data.job.id);
    upsertJob(data.job);
    scheduleJobPoll(0);
    return data.job;
}

async function jobsAction(id, action) {
    const job = await jobsRequest(`/api/jobs/${id}/${action}`, { method: 'POST' });
    upsertJob(job);
    scheduleJobPoll(300);
    return job;
}

function jobsResult(id) {
    return jobsRequest(`/api/jobs/${id}/result`);
}

// fn(job) bij elke nieuwe stand van deze taak; geeft een functie terug om te stoppen met volgen
function jobsWatch(id, fn) {
    if (!jobDock.watchers.has(id)) jobDock.watchers.set(id, new Set());
    jobDock.watchers.get(id).add(fn);
    const job = jobDock.jobs.find(j => j.id === id);
    if (job) fn(job);
    scheduleJobPoll(0);
    return () => {
        const set = jobDock.watchers.get(id);
        if (set) { set.delete(fn); if (!set.size) jobDock.watchers.delete(id); }
    };
}

function jobsOnFinished(fn) {
    jobDock.finishedHandlers.push(fn);
}

function jobsOnChange(fn) {
    jobDock.changeHandlers.push(fn);
}

function upsertJob(job) {
    const i = jobDock.jobs.findIndex(j => j.id === job.id);
    if (i >= 0) jobDock.jobs[i] = job; else jobDock.jobs.unshift(job);
    noticeJob(job);
    renderJobDock();
}

// Meldt nieuwe standen aan wie meekijkt, en een overgang van bezig naar klaar/mislukt/gestopt
function noticeJob(job) {
    const before = jobDock.known.get(job.id);
    jobDock.known.set(job.id, job.status);
    [...(jobDock.watchers.get(job.id) || []), ...jobDock.changeHandlers].forEach(fn => {
        try { fn(job); } catch (e) { console.error(e); }
    });
    if (before && JOB_ACTIVE.has(before) && !JOB_ACTIVE.has(job.status)) {
        jobDock.finishedHandlers.forEach(fn => {
            try { fn(job); } catch (e) { console.error(e); }
        });
    }
}

// Een automatische bijwerkronde van de bibliotheek (door de app zelf gestart), of een DataGatherer-
// bron die volgens het schema bijwerkt: die blijven alleen staan zolang ze lopen (of als ze mislukken)
function jobIsAuto(job) {
    if (job.type.startsWith('datagatherer.sync.')) return !!(job.params && job.params.trigger === 'schedule');
    return (job.type === 'index' || job.type === 'semantic') && !!(job.params && job.params.auto);
}

async function jobsLibraryRefresh() {
    if (jobDock.libraryLoading) return;
    jobDock.libraryLoading = true;
    const lastPass = jobDock.library ? jobDock.library.last_pass_at : undefined;
    try {
        jobDock.library = await jobsRequest('/api/library/status');
    } catch (error) {
        jobDock.library = null;  // archief weg: de banner meldt het al; de regel verdwijnt even
    } finally {
        jobDock.libraryLoading = false;
    }
    if (typeof homeRenderLibraryStats === 'function') homeRenderLibraryStats(jobDock.library);
    // Een ronde is klaar: Albums telt en laadt opnieuw (alleen als er iets veranderde)
    if (lastPass !== undefined && jobDock.library && jobDock.library.last_pass_at !== lastPass
        && typeof albumsLibraryUpdated === 'function') albumsLibraryUpdated();
    renderJobDock();
}

async function startLibraryUpdate() {
    try {
        const job = await jobsStart('index', { full: true });
        showToast('Bibliotheek bijwerken', `Gestart (taak ${job.id}); de voortgang staat rechtsonder.`, 'info');
    } catch (error) {
        toastFetchError('Bibliotheek bijwerken', error);
    }
}

async function jobsRefresh() {
    const before = new Map(jobDock.jobs.filter(j => j.type === 'index').map(j => [j.id, j.status]));
    try {
        const data = await jobsRequest('/api/jobs?limit=20');
        jobDock.error = null;
        // Taken die al buiten de lijst vallen maar wel gevolgd worden, apart ophalen
        const listed = new Set(data.jobs.map(j => j.id));
        const extra = await Promise.all([...jobDock.watchers.keys()].filter(id => !listed.has(id))
            .map(id => jobsRequest(`/api/jobs/${id}`).catch(() => null)));
        jobDock.jobs = data.jobs.concat(extra.filter(Boolean));
        jobDock.jobs.forEach(noticeJob);
    } catch (error) {
        jobDock.error = error.message;
    }
    // De bibliotheekregel bijwerken als een bijwerkronde begon of klaar is, en anders af en toe
    const indexChanged = jobDock.jobs.some(j => j.type === 'index' && before.get(j.id) !== j.status);
    if (indexChanged || !jobDock.library || !jobDock.jobs.some(j => JOB_ACTIVE.has(j.status))) {
        jobsLibraryRefresh();
    }
    renderJobDock();
    scheduleJobPoll();
}

function scheduleJobPoll(delay) {
    clearTimeout(jobDock.timer);
    if (delay === undefined) {
        const busy = jobDock.jobs.some(j => JOB_ACTIVE.has(j.status) || j.stopping);
        delay = busy || jobDock.watchers.size ? JOB_POLL_ACTIVE_MS : JOB_POLL_IDLE_MS;
    }
    jobDock.timer = setTimeout(jobsRefresh, delay);
}

// ==================== WEERGAVE ====================

function formatEta(seconds) {
    if (seconds === null || seconds === undefined) return '';
    if (seconds < 60) return `nog ~${Math.max(1, Math.round(seconds))} s`;
    if (seconds < 3600) return `nog ~${Math.round(seconds / 60)} min`;
    const hours = Math.floor(seconds / 3600);
    return `nog ~${hours} u ${Math.round((seconds % 3600) / 60)} min`;
}

function jobFolderLabel(job) {
    if (job.target) return job.target;  // van de server: map binnen het archief, of 'hele archief'
    const folder = job.params && job.params.folder;
    if (!folder) return 'hele archief';
    const parts = folder.split('/').filter(Boolean);
    return parts.slice(-2).join('/') || folder;
}

function jobPercent(job) {
    if (job.status === 'done') return 100;
    if (!job.total) return null;
    return Math.min(100, Math.floor(job.processed / job.total * 100));
}

// Klaar, maar niet alles kon gelezen worden (leesfouten, een map die niet open ging). Zo'n
// uitkomst vraagt aandacht: het paneel klapt er niet voor in en hij gaat niet bij 'eerder klaar'.
function jobIncomplete(job) {
    if (job.status !== 'done' || !job.summary) return false;
    return job.summary.complete === false || Number(job.summary.errors) > 0;
}

function jobVisible(job) {
    if (jobDock.dismissed.has(job.id)) return false;
    if (JOB_ACTIVE.has(job.status) || ['paused', 'interrupted'].includes(job.status)) return true;
    // Een automatische ronde die gewoon klaar of geannuleerd is, hoeft niet te blijven staan
    if (jobIsAuto(job) && job.status !== 'failed') return false;
    const finished = (job.finished_at || job.updated_at || 0) * 1000;
    return Date.now() - finished < JOB_SHOW_FINISHED_MS || jobDock.watchers.has(job.id);
}

function jobButton(text, title, onClick, extraClass = '') {
    return createEl('button', {
        class: `btn btn-secondary btn-small ${extraClass}`.trim(),
        title,
        text,
        onClick: async (event) => {
            const button = event.currentTarget;
            button.disabled = true;
            try { await onClick(); } catch (error) { toastFetchError('Taak', error); }
            finally { button.disabled = false; }
        },
    });
}

function createJobItem(job) {
    const percent = jobPercent(job);
    const bar = createEl('div', { class: 'job-bar' });
    if (percent === null) bar.classList.add('indeterminate');
    else bar.style.width = `${percent}%`;

    const details = [];
    if (job.total) details.push(`${job.processed.toLocaleString('nl-NL')} / ${job.total.toLocaleString('nl-NL')}`);
    else if (job.processed && JOB_ACTIVE.has(job.status)) details.push(`${job.processed.toLocaleString('nl-NL')} gevonden`);
    const eta = formatEta(job.eta_seconds);
    if (eta) details.push(eta);

    const actions = [];
    // Alleen de nieuwste uitkomsten worden bewaard: een oudere scan heeft niets meer om te tonen
    if (job.type === 'dup_scan' && job.status === 'done' && job.has_result) {
        actions.push(jobButton('Bekijk', 'Toon dit resultaat in Duplicaten', () => openDuplicateScanJob(job.id)));
    }
    if (job.type === 'export' && job.status === 'done' && job.summary && job.summary.destination) {
        actions.push(jobButton('Toon in Finder', 'Open de exportmap', () => exportRevealFolder(job.summary.destination)));
    }
    if (job.can_pause && !job.stopping) {
        actions.push(jobButton('Pauzeren', 'Pauzeer; hervatten gaat verder waar hij was', () => jobsAction(job.id, 'pause')));
    }
    if (job.can_resume) {
        actions.push(jobButton('Hervatten', 'Ga verder; wat al klaar was wordt overgeslagen', () => jobsAction(job.id, 'resume')));
    }
    if (job.can_cancel && !(job.stopping && job.status !== 'waiting_for_vault')) {
        actions.push(jobButton('Annuleren', 'Stop deze taak', () => jobsAction(job.id, 'cancel'), 'job-cancel'));
    }
    if (!JOB_ACTIVE.has(job.status)) {
        actions.push(createEl('button', {
            class: 'job-dismiss',
            title: 'Verberg uit dit paneel',
            'aria-label': 'Verberg',
            text: '×',
            onClick: () => { jobDock.dismissed.add(job.id); saveJobDockPrefs(); renderJobDock(); },
        }));
    }

    const message = job.status === 'failed' && job.error ? job.error : (job.message || '');
    const incomplete = jobIncomplete(job) ? ' job-incomplete' : '';
    return createEl('div', { class: `job-item job-${job.status}${incomplete}`, dataset: { id: String(job.id) } },
        createEl('div', { class: 'job-item-head' },
            createEl('span', { class: 'job-title', text: job.label, title: (job.params && job.params.folder) || '' }),
            createEl('span', { class: 'job-folder', text: jobFolderLabel(job), title: (job.params && job.params.folder) || '' }),
            createEl('span', { class: 'job-status',
                text: jobIncomplete(job) ? 'Onvolledig' : (JOB_STATUS_LABELS[job.status] || job.status) })),
        createEl('div', { class: 'job-progress' }, bar),
        createEl('div', { class: 'job-message', text: message }),
        createEl('div', { class: 'job-item-foot' },
            createEl('span', { class: 'job-details', text: details.join(' · ') }),
            createEl('span', { class: 'job-actions' }, actions)));
}

function formatLibraryTime(seconds) {
    if (!seconds) return '';
    const when = new Date(seconds * 1000);
    const today = new Date();
    const time = when.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
    if (when.toDateString() === today.toDateString()) return time;
    return `${when.toLocaleDateString('nl-NL', { day: 'numeric', month: 'short' })} ${time}`;
}

// De regel van de bibliotheek: aantal bestanden, laatst bijgewerkt, en de knop om bij te werken
function createLibraryLine() {
    const lib = jobDock.library;
    if (!lib) return null;
    const running = jobDock.jobs.find(j => j.type === 'index' && JOB_ACTIVE.has(j.status));
    let text;
    if (!lib.ready) {
        text = running ? 'Bibliotheek wordt opgebouwd…' : 'Bibliotheek nog niet opgebouwd';
    } else {
        text = `Bibliotheek: ${lib.files.toLocaleString('nl-NL')} bestanden`;
        const when = formatLibraryTime(lib.last_pass_at);
        if (when) text += ` · bijgewerkt ${when}`;
    }
    // De laatste ronde (ook een automatische, die zelf niets meldt) kon niet alles lezen
    const incomplete = lib.ready && !running && lib.last_pass_at && lib.last_pass_at !== lib.last_complete_at;
    const title = lib.ready
        ? `${lib.photos.toLocaleString('nl-NL')} foto's, ${lib.videos.toLocaleString('nl-NL')} video's, ` +
          `${lib.audio.toLocaleString('nl-NL')} audio in ${lib.folders.toLocaleString('nl-NL')} mappen`
        : 'De app leest het archief één keer helemaal in; daarna gaat bijwerken snel';
    const button = createEl('button', {
        class: 'btn btn-secondary btn-small job-library-update',
        id: 'btn-library-update',
        title: 'Controleer alle mappen en bestanden op wijzigingen (ook bestanden die op hun plek zijn bewerkt)',
        text: running ? 'Bezig…' : 'Bibliotheek bijwerken',
        onClick: async (event) => {
            const btn = event.currentTarget;
            btn.disabled = true;
            try { await startLibraryUpdate(); } finally { btn.disabled = false; renderJobDock(); }
        },
    });
    if (running) button.disabled = true;
    const warning = incomplete
        ? createEl('span', {
            class: 'job-library-warning',
            title: 'De laatste ronde kon niet alle mappen of bestanden lezen; de volgende ronde probeert ze opnieuw',
        },
            createEl('i', { class: 'fas fa-triangle-exclamation', 'aria-hidden': 'true' }),
            createEl('span', { text: 'Laatste ronde onvolledig' }))
        : null;
    return createEl('div', { class: `job-library${incomplete ? ' incomplete' : ''}`, id: 'job-library' },
        createEl('span', { class: 'job-library-text', title }, text, warning),
        button);
}

// Het paneel zweeft rechtsonder over de pagina. Zoveel ruimte (paneel + marge) houdt de pagina
// onderaan vrij (--job-dock-space, zie .main-content), zodat het laatste stuk inhoud onder het
// paneel vandaan kan scrollen.
let jobDockObserver = null;

function jobDockReserveSpace() {
    const dock = document.getElementById('job-dock');
    if (!dock) return;
    const update = () => {
        const height = dock.hidden ? 0 : dock.getBoundingClientRect().height;
        // paneel + zijn afstand tot de onderrand + nog eens die afstand als lucht erboven
        const gap = parseFloat(getComputedStyle(dock).bottom) || 0;
        document.documentElement.style.setProperty('--job-dock-space', height ? `${Math.ceil(height + 2 * gap)}px` : '0px');
    };
    if (!jobDockObserver && typeof ResizeObserver === 'function') {
        jobDockObserver = new ResizeObserver(update);
        jobDockObserver.observe(dock);
    }
    update();
}

function renderJobDock() {
    const dock = document.getElementById('job-dock');
    if (!dock) return;
    try { renderJobDockContent(dock); } finally { jobDockReserveSpace(); }
}

function renderJobDockContent(dock) {
    const visible = jobDock.jobs.filter(jobVisible);
    const active = visible.filter(j => JOB_ACTIVE.has(j.status)).length;
    const library = createLibraryLine();
    dock.hidden = !visible.length && !jobDock.error && !library;
    // Zonder taken alleen de regel van de bibliotheek, als smalle balk
    const compact = !visible.length && !jobDock.error;
    dock.classList.toggle('compact', compact);
    if (compact) {
        if (library) dock.replaceChildren(library);
        return;
    }

    // Afgerond (klaar of geannuleerd) vraagt geen aandacht meer: is er verder niets, dan klapt
    // het paneel vanzelf in, zodat het niets van de pagina bedekt. Een onvolledige uitkomst
    // (leesfouten) telt niet als afgerond: die blijft open in beeld.
    const finished = visible.filter(j => (j.status === 'done' && !jobIncomplete(j)) || j.status === 'cancelled');
    const open = visible.filter(j => !finished.includes(j));
    const incomplete = visible.filter(jobIncomplete).length;
    const idle = !open.length && !jobDock.error;
    const collapsed = idle ? !jobDock.idleOpen : jobDock.collapsed;
    dock.classList.toggle('collapsed', collapsed);
    dock.classList.toggle('attention', incomplete > 0);

    let title = active ? `Taken (${active} bezig)`
        : idle ? `${finished.length} ${finished.length === 1 ? 'taak' : 'taken'} klaar` : `Taken (${visible.length})`;
    if (incomplete) title += ` · ${incomplete} onvolledig`;
    // Alleen afgeronde taken en dicht: een klein icoon met het aantal, geen balk over de pagina
    const tiny = idle && collapsed;
    dock.classList.toggle('tiny', tiny);
    const header = createEl('button', {
        class: 'job-dock-header',
        'aria-label': tiny ? title : null,
        title: tiny ? title : null,
        'aria-expanded': collapsed ? 'false' : 'true',
        onClick: () => {
            if (idle) jobDock.idleOpen = !jobDock.idleOpen;
            else { jobDock.collapsed = !jobDock.collapsed; saveJobDockPrefs(); }
            renderJobDock();
        },
    },
        createEl('span', { class: 'job-dock-title' },
            tiny ? createEl('i', { class: 'fas fa-circle-check', 'aria-hidden': 'true' })
                : (incomplete ? createEl('i', { class: 'fas fa-triangle-exclamation job-dock-warning', 'aria-hidden': 'true' }) : null),
            tiny ? String(finished.length) : title),
        createEl('i', { class: `fas ${collapsed ? 'fa-chevron-up' : 'fa-chevron-down'} job-dock-toggle`, 'aria-hidden': 'true' }));

    // Van de afgeronde taken staat de nieuwste er helemaal; de rest samen op één regel
    const older = finished.slice(1);
    const items = open.concat(finished.slice(0, 1)).map(createJobItem);
    if (older.length) {
        items.push(createEl('div', { class: 'job-folded' },
            createEl('button', {
                class: 'job-folded-toggle',
                'aria-expanded': jobDock.showFinished ? 'true' : 'false',
                onClick: () => { jobDock.showFinished = !jobDock.showFinished; renderJobDock(); },
            },
                createEl('i', { class: `fas ${jobDock.showFinished ? 'fa-chevron-down' : 'fa-chevron-right'}`, 'aria-hidden': 'true' }),
                createEl('span', { text: `${older.length} eerder klaar` })),
            createEl('button', {
                class: 'btn btn-ghost btn-small',
                text: 'Alles verbergen',
                title: 'Verberg alle afgeronde taken uit dit paneel',
                onClick: () => { finished.forEach(j => jobDock.dismissed.add(j.id)); saveJobDockPrefs(); renderJobDock(); },
            })));
        if (jobDock.showFinished) items.push(...older.map(createJobItem));
    }

    const body = createEl('div', { class: 'job-dock-body' },
        jobDock.error ? createEl('div', { class: 'job-dock-error', text: `Taken niet op te halen: ${jobDock.error}` }) : null,
        items);
    dock.replaceChildren(header, body);
    if (library) dock.appendChild(library);
}

function jobsInit() {
    jobsOnFinished(job => {
        // Een automatische ronde meldt zich alleen als er iets misging
        if (jobIsAuto(job) && job.status !== 'failed') return;
        if (job.type === 'export') return;   // export.js meldt dit zelf (met 'Toon in Finder')
        if (job.type === 'backup') return;   // backup-settings.js meldt dit zelf (met de grootte)
        const kind = job.status === 'done' ? 'success' : job.status === 'failed' ? 'error' : 'info';
        const text = job.status === 'failed' ? (job.error || 'Mislukt') : (job.message || JOB_STATUS_LABELS[job.status]);
        showToast(`${job.label}: ${JOB_STATUS_LABELS[job.status] || job.status}`, text, kind);
    });
    jobsRefresh();
}

// ==================== TAKEN STARTEN VANUIT DE APP ====================

async function startThumbnailJob() {
    const folder = typeof currentFolder === 'string' && currentFolder ? currentFolder : null;
    if (!folder && !confirm('Miniaturen maken voor het hele archief?\n\nDit kan bij een groot archief uren duren. ' +
                            'Het loopt op de achtergrond; je kunt het pauzeren en later hervatten.')) {
        return;
    }
    try {
        const job = await jobsStart('thumbnails', folder ? { folder } : {});
        showToast('Miniaturen maken', `Gestart voor ${jobFolderLabel(job)}; de voortgang staat rechtsonder.`, 'info');
    } catch (error) {
        toastFetchError('Miniaturen maken', error);
    }
}

document.addEventListener('DOMContentLoaded', jobsInit);
