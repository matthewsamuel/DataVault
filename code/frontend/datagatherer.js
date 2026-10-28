// DataVault Dashboard - DataGatherer
// Note: API_BASE is defined in app.js
//
// Eén kaart per gegevensbron (muziek, berichten, sociale media, ...), gegroepeerd per categorie. Alles
// op deze pagina komt uit de beschrijving die elke bron op de server van zichzelf geeft
// (backend/datagatherer/connectors/*.py, via /api/datagatherer/sources): naam, icoon, wat de
// gegevens dekken, de volgende stap in één zin, één hoofdknop, de hulp, de instellingen en het
// netwerklabel. Er staat hier geen code per bron: een nieuwe bron is één Python-bestand.
//
// DataVault gaat alleen online als jij op een knop drukt (of een schema aanzet). Een
// exportpagina of toestemmingspagina opent altijd in je eigen browser (openExternal), nooit
// door de app zelf; wachtwoorden typt alleen jij, op de site van de bron.

const DG_ACTIONS_NEED_DETAIL = new Set(['connect', 'reconnect']);
const DG_EFFORT_TONES = { makkelijk: 'badge-success', gemiddeld: 'badge-info', moeilijk: 'badge-warning' };
const DG_REFRESH_DEBOUNCE_MS = 400;

const dgState = {
    data: null,
    loading: false,
    seq: 0,
    filter: 'all',
    openHelp: new Set(),
    refreshTimer: null,
    connectPoll: null,
};

function datagathererInit() {
    dgLoad();
}

// ==================== LADEN EN TEKENEN ====================

async function dgLoad() {
    const box = document.getElementById('dg-groups');
    if (!box) return;
    const seq = ++dgState.seq;
    if (!dgState.data) box.replaceChildren(skeletonCards(3, 'dg-skeleton'));
    dgState.loading = true;
    try {
        const data = await fetchJSON(`${API_BASE}/api/datagatherer/sources`);
        if (seq !== dgState.seq) return;
        dgState.data = data;
        dgRender();
    } catch (err) {
        if (seq !== dgState.seq) return;
        dgState.data = null;
        document.getElementById('dg-summary').replaceChildren();
        document.getElementById('dg-filters').replaceChildren();
        box.replaceChildren(errorState('De bronnen konden niet geladen worden', err, dgLoad));
    } finally {
        if (seq === dgState.seq) dgState.loading = false;
    }
}

// Na een taak (bijwerken, inlezen) of een wijziging: even wachten en dan alles opnieuw ophalen
function dgRefreshSoon() {
    clearTimeout(dgState.refreshTimer);
    dgState.refreshTimer = setTimeout(() => { if (dgVisible()) dgLoad(); }, DG_REFRESH_DEBOUNCE_MS);
}

function dgVisible() {
    const section = document.getElementById('section-datagatherer');
    return !!section && section.classList.contains('active');
}

function dgSource(id) {
    return dgState.data ? dgState.data.sources.find(s => s.id === id) : null;
}

function dgRender() {
    const data = dgState.data;
    const box = document.getElementById('dg-groups');
    if (!data || !box) return;
    // De focus blijft op dezelfde knop als de kaarten opnieuw getekend worden
    const focused = document.activeElement && box.contains(document.activeElement) ? document.activeElement.dataset.dgFocus : null;
    dgRenderSummary(data.summary);
    dgRenderFilters(data.categories);
    if (!data.sources.length) {
        box.replaceChildren(emptyState({ icon: 'fa-plug', title: 'Nog geen bronnen',
            text: 'Er zijn geen gegevensbronnen geïnstalleerd.' }));
        return;
    }
    const categories = dgState.filter === 'all' ? data.categories : [dgState.filter];
    box.replaceChildren(...categories.map(category => {
        const sources = data.sources.filter(s => s.category === category);
        return createEl('section', { class: 'dg-group', 'aria-labelledby': `dg-group-${dgSlug(category)}` },
            createEl('h2', { class: 'section-title dg-group-title', id: `dg-group-${dgSlug(category)}` },
                createEl('span', { text: category }),
                createEl('span', { class: 'dg-group-count mono', text: String(sources.length) })),
            createEl('div', { class: 'dg-grid' }, ...sources.map(dgCard)));
    }));
    if (focused) {
        const again = box.querySelector(`[data-dg-focus="${CSS.escape(focused)}"]`);
        if (again) again.focus();
    }
}

function dgSlug(text) {
    return String(text).toLowerCase().replace(/[^a-z0-9]+/g, '-');
}

function dgRenderSummary(summary) {
    const el = document.getElementById('dg-summary');
    const part = (n, one, many) => createEl('span', { class: 'dg-summary-item' },
        createEl('span', { class: 'mono', text: formatNumberNl(n) }), ` ${n === 1 ? one : many}`);
    // Altijd het totaal; de rest alleen als het er is
    const parts = [part(summary.total, 'bron', 'bronnen')];
    if (summary.current) parts.push(part(summary.current, 'actueel', 'actueel'));
    if (summary.behind) parts.push(part(summary.behind, 'achter', 'achter'));
    if (summary.to_read) parts.push(part(summary.to_read, 'klaar om in te lezen', 'klaar om in te lezen'));
    if (summary.waiting) parts.push(part(summary.waiting, 'wacht op een export', 'wachten op een export'));
    if (summary.not_set) parts.push(part(summary.not_set, 'nog niet ingesteld', 'nog niet ingesteld'));
    el.replaceChildren(...parts);
}

function dgRenderFilters(categories) {
    const el = document.getElementById('dg-filters');
    if (categories.length < 2) {
        el.replaceChildren();
        dgState.filter = 'all';
        return;
    }
    if (dgState.filter !== 'all' && !categories.includes(dgState.filter)) dgState.filter = 'all';
    const pill = (value, label) => createEl('button', {
        type: 'button', class: 'pill', 'aria-pressed': String(dgState.filter === value), text: label,
        dataset: { dgFocus: `filter-${value}` },
        onClick: () => { dgState.filter = value; dgRender(); },
    });
    el.replaceChildren(pill('all', 'Alles'), ...categories.map(c => pill(c, c)));
}

// ==================== EEN KAART ====================

function dgDate(seconds) {
    if (!seconds) return null;
    return new Date(seconds * 1000).toLocaleDateString('nl-NL', { day: 'numeric', month: 'short', year: 'numeric' });
}

function dgTime(seconds) {
    if (!seconds) return '';
    const date = new Date(seconds * 1000);
    const today = new Date();
    const time = date.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
    return date.toDateString() === today.toDateString() ? `vandaag ${time}` : `${dgDate(seconds)} ${time}`;
}

function dgDays(n) {
    return `${formatNumberNl(n)} ${n === 1 ? 'dag' : 'dagen'}`;
}

function dgActiveJob(src) {
    if (typeof jobDock === 'undefined') return null;
    const types = Object.values(src.job_types || {}).filter(Boolean);
    return jobDock.jobs.find(j => (types.includes(j.type) || (j.type === 'datagatherer.watch' && j.params && j.params.source === src.id))
        && JOB_ACTIVE.has(j.status)) || null;
}

function dgCard(src) {
    const job = dgActiveJob(src);
    const card = createEl('article', {
        class: `card dg-card dg-tone-${src.tone}${job ? ' is-busy' : ''}`,
        dataset: { source: src.id },
        'aria-labelledby': `dg-name-${src.id}`,
    });
    const status = job
        ? createEl('span', { class: 'badge badge-info dg-status' },
            createEl('i', { class: 'fas fa-circle-notch fa-spin', 'aria-hidden': 'true' }), 'Bezig')
        : createEl('span', { class: `badge ${dgBadgeClass(src.tone)} dg-status`, text: src.status_label });

    appendChildren(card, [
        createEl('header', { class: 'dg-card-head' },
            createEl('span', { class: 'dg-card-icon', 'aria-hidden': 'true' }, createEl('i', { class: src.icon })),
            createEl('div', { class: 'dg-card-titles' },
                createEl('div', { class: 'dg-card-title-row' },
                    createEl('h3', { class: 'dg-card-name', id: `dg-name-${src.id}`, text: src.name }),
                    status),
                createEl('p', { class: 'dg-card-desc', text: src.description }))),
        dgDataBlock(src),
        createEl('p', { class: 'dg-next', text: src.next_step }),
        ...src.notes.map(dgNote),
        job ? dgProgress(job) : null,
        dgActionRow(src, job),
        dgHelpPanel(src),
        dgFooter(src)]);
    return card;
}

function dgBadgeClass(tone) {
    return { success: 'badge-success', warning: 'badge-warning', danger: 'badge-danger', info: 'badge-info' }[tone] || '';
}

function dgDataBlock(src) {
    const until = dgDate(src.data_until);
    const line = createEl('div', { class: 'dg-until' });
    if (until) {
        line.append(createEl('span', { class: 'dg-until-label', text: 'Gegevens t/m ' }),
            createEl('span', { class: 'mono dg-until-date', text: until }));
        if (typeof src.gap_days === 'number') {
            const behind = src.gap_days > src.stale_days;
            line.append(createEl('span', {
                class: `dg-gap${behind ? ' is-behind' : ''}`,
                text: src.gap_days === 0 ? ' · vandaag' : ` · ${dgDays(src.gap_days)} achter`,
            }));
        }
    } else {
        line.append(createEl('span', { class: 'dg-until-label', text: src.raw.count
            ? `${formatNumberNl(src.raw.count)} ${src.raw.count === 1 ? 'export' : 'exports'} in het archief`
            : 'Nog geen gegevens' }));
    }
    return createEl('div', { class: 'dg-data' }, line, dgCoverageBar(src),
        src.records ? createEl('div', { class: 'dg-records' },
            createEl('span', { class: 'mono', text: formatNumberNl(src.records) }),
            ` ${src.records === 1 ? src.record_label[0] : src.record_label[1]}`) : null);
}

// De dekkingsbalk: van de eerste gegevens tot vandaag; gestreept waar de luistertijd een schatting is
function dgCoverageBar(src) {
    const cov = src.coverage || {};
    const track = createEl('div', { class: 'dg-coverage', role: 'img' });
    const segments = cov.segments || [];
    if (!cov.first || !segments.length) {
        track.classList.add('is-empty');
        track.setAttribute('aria-label', 'Nog geen gegevens');
        return createEl('div', { class: 'dg-coverage-wrap' }, track);
    }
    const start = cov.first;
    const end = Math.max(Date.now() / 1000, cov.last || 0);
    const span = Math.max(1, end - start);
    segments.forEach(seg => {
        const left = Math.max(0, (seg.from - start) / span * 100);
        const width = Math.max(0.8, (seg.until - seg.from) / span * 100);
        track.append(createEl('span', {
            class: `dg-coverage-seg${seg.estimated ? ' is-estimated' : ''}`,
            style: `left: ${left.toFixed(2)}%; width: ${Math.min(width, 100 - left).toFixed(2)}%`,
        }));
    });
    const estimated = cov.estimated_since ? `; na ${dgDate(cov.estimated_since)} geschat` : '';
    track.setAttribute('aria-label', `Gegevens van ${dgDate(cov.first)} tot ${dgDate(cov.last)}${estimated}`);
    const legend = createEl('div', { class: 'dg-coverage-legend' },
        createEl('span', { class: 'mono', text: String(new Date(start * 1000).getFullYear()) }),
        cov.estimated_since ? createEl('span', { class: 'dg-legend-estimated' },
            createEl('span', { class: 'dg-legend-swatch', 'aria-hidden': 'true' }),
            `Luistertijd na ${dgDate(cov.estimated_since)} is een schatting`) : null,
        createEl('span', { text: 'vandaag' }));
    return createEl('div', { class: 'dg-coverage-wrap' }, track, legend);
}

function dgNote(note) {
    const icon = note.tone === 'warning' ? 'fa-triangle-exclamation' : 'fa-circle-info';
    return createEl('p', { class: `dg-note dg-note-${note.tone}` },
        createEl('i', { class: `fas ${icon}`, 'aria-hidden': 'true' }),
        createEl('span', { text: note.text, title: note.path || undefined }));
}

function dgProgress(job) {
    const pct = job.total ? Math.min(100, Math.round(job.processed / job.total * 100)) : null;
    return createEl('div', { class: 'dg-progress', role: 'status' },
        createEl('div', { class: `progress${pct === null ? ' is-indeterminate' : ''}` },
            createEl('div', { class: 'progress-fill', style: pct === null ? '' : `width: ${pct}%` })),
        createEl('span', { class: 'dg-progress-text', text: job.message || 'Bezig…' }));
}

function dgActionRow(src, job) {
    const primary = createEl('button', {
        type: 'button', class: 'btn btn-primary dg-primary', disabled: !!job,
        dataset: { dgFocus: `${src.id}-primary`, action: src.primary.action },
        onClick: (e) => dgDoAction(src, src.primary.action, e.currentTarget),
    }, createEl('i', { class: `fas ${src.primary.icon}`, 'aria-hidden': 'true' }), src.primary.label);
    const row = createEl('div', { class: 'dg-actions' }, primary);
    if (src.actions.length) {
        const menu = createEl('div', { class: 'menu dg-more-menu', role: 'menu' },
            ...src.actions.map(a => createEl('button', {
                type: 'button', class: 'menu-item', role: 'menuitem', disabled: !!job && a.action !== 'request_export',
                dataset: { action: a.action },
                onClick: (e) => { e.currentTarget.closest('details').open = false; dgDoAction(src, a.action, primary); },
            }, createEl('i', { class: `fas ${a.icon}`, 'aria-hidden': 'true' }), a.label)));
        const more = createEl('details', { class: 'dg-more' },
            createEl('summary', { class: 'btn btn-secondary', dataset: { dgFocus: `${src.id}-more` },
                'aria-label': `Meer acties voor ${src.name}` },
                'Meer', createEl('i', { class: 'fas fa-chevron-down', 'aria-hidden': 'true' })),
            menu);
        more.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && more.open) {
                e.preventDefault();
                e.stopPropagation();
                more.open = false;
                more.querySelector('summary').focus();
            }
        });
        row.append(more);
    }
    const helpOpen = dgState.openHelp.has(src.id);
    row.append(createEl('span', { class: 'dg-actions-spacer' }),
        createEl('button', {
            type: 'button', class: 'btn btn-ghost btn-icon', 'aria-label': `Hulp bij ${src.name}`, title: 'Hulp',
            'aria-expanded': String(helpOpen), 'aria-controls': `dg-help-${src.id}`,
            dataset: { dgFocus: `${src.id}-help` },
            onClick: () => dgToggleHelp(src.id),
        }, createEl('i', { class: 'fas fa-circle-question', 'aria-hidden': 'true' })),
        createEl('button', {
            type: 'button', class: 'btn btn-ghost btn-icon', 'aria-label': `Instellingen van ${src.name}`,
            title: 'Instellingen', dataset: { dgFocus: `${src.id}-settings` },
            onClick: () => dgOpenSettings(src.id),
        }, createEl('i', { class: 'fas fa-sliders', 'aria-hidden': 'true' })));
    return row;
}

function dgHelpPanel(src) {
    const panel = createEl('div', { class: 'dg-help', id: `dg-help-${src.id}`, hidden: !dgState.openHelp.has(src.id) });
    if (!dgState.openHelp.has(src.id)) return panel;
    panel.append(createEl('p', { class: 'dg-help-loading', text: 'Laden…' }));
    dgDetail(src.id).then(detail => {
        panel.replaceChildren(createEl('h4', { class: 'dg-help-title', text: 'Zo haal je je gegevens binnen' }),
            createEl('ol', { class: 'dg-help-options' }, ...detail.help.map(option => createEl('li', { class: 'dg-help-option' },
                createEl('div', { class: 'dg-help-option-head' },
                    createEl('span', { class: 'dg-help-option-title', text: option.title }),
                    createEl('span', { class: `badge ${DG_EFFORT_TONES[option.effort] || ''}`,
                        text: option.effort.charAt(0).toUpperCase() + option.effort.slice(1) })),
                createEl('ol', { class: 'dg-help-steps' }, ...option.steps.map(step => createEl('li', { text: step }))),
                option.note ? createEl('p', { class: 'dg-help-note', text: option.note }) : null))));
    }).catch(err => panel.replaceChildren(errorState('Hulp laden mislukt', err)));
    return panel;
}

function dgToggleHelp(id) {
    if (dgState.openHelp.has(id)) dgState.openHelp.delete(id); else dgState.openHelp.add(id);
    dgRender();
}

function dgFooter(src) {
    const net = src.network;
    const parts = [createEl('span', { class: 'dg-net' },
        createEl('i', { class: `fas ${net.local ? 'fa-laptop' : 'fa-globe'}`, 'aria-hidden': 'true' }),
        createEl('span', { text: net.label }))];
    if (!net.local && net.last_contact) {
        parts.push(createEl('span', { class: 'dg-net-last', text: `Laatste contact ${dgTime(net.last_contact)}` }));
    }
    if (src.schedule.available) {
        parts.push(createEl('span', { class: 'dg-net-last', text: src.schedule.enabled
            ? `Schema aan: elke ${src.schedule.minutes} minuten` : 'Schema uit' }));
    }
    return createEl('footer', { class: 'dg-card-foot' }, ...parts);
}

// ==================== ACTIES ====================

async function dgPost(url, body) {
    return fetchJSON(`${API_BASE}${url}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    });
}

function dgAfterJob(job) {
    if (typeof upsertJob === 'function' && job) upsertJob(job);
    if (typeof scheduleJobPoll === 'function') scheduleJobPoll(0);
    dgRefreshSoon();
}

async function dgDoAction(src, action, button) {
    if (DG_ACTIONS_NEED_DETAIL.has(action)) return dgOpenConnect(src.id);
    if (action === 'request_export') return dgOpenExport(src.id);
    if (action === 'find_downloads') return dgOpenDownloads(src.id);
    if (action === 'upload') return dgPickFile(src);
    const label = button ? button.textContent : '';
    if (button) { button.disabled = true; button.classList.add('is-loading'); }
    try {
        const data = await dgPost(`/api/datagatherer/sources/${encodeURIComponent(src.id)}/${action === 'sync' ? 'sync' : 'import'}`);
        dgAfterJob(data.job);
        showToast(action === 'sync' ? `${src.name} bijwerken` : `${src.name} inlezen`,
            'Gestart; de voortgang staat op de kaart en rechtsonder.', 'info');
    } catch (err) {
        toastFetchError(`${src.name}: ${label.trim() || 'actie'} mislukt`, err);
    } finally {
        if (button && button.isConnected) { button.disabled = false; button.classList.remove('is-loading'); }
        dgRefreshSoon();
    }
}

// Een exportbestand kiezen (de browser leest het, de server kopieert het naar het archief)
function dgPickFile(src) {
    const input = createEl('input', { type: 'file', accept: '.zip,.json,.tgz', class: 'sr-only', 'aria-hidden': 'true' });
    input.addEventListener('change', async () => {
        const file = input.files && input.files[0];
        input.remove();
        if (!file) return;
        showToast(`${src.name}: bestand inlezen`, `${file.name} wordt naar DataVault gestuurd…`, 'info');
        try {
            const res = await fetch(`${API_BASE}/api/datagatherer/sources/${encodeURIComponent(src.id)}/upload?name=${encodeURIComponent(file.name)}`, {
                method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
            });
            const data = await res.json().catch(() => null);
            if (!res.ok) throw new ApiError(apiErrorMessage(data, `Serverfout (${res.status})`), res.status);
            if (data.job) dgAfterJob(data.job);
            showToast(`${src.name}: bestand inlezen`,
                'Het wordt naar het archief gekopieerd en daarna ingelezen; de voortgang staat op de kaart.', 'info');
        } catch (err) {
            toastFetchError(`${file.name} inlezen mislukt`, err);
        } finally {
            dgRefreshSoon();
        }
    });
    document.body.append(input);
    input.click();
}

// ==================== VENSTERS ====================

function dgModal(id, title, icon, body, footer, wide) {
    document.getElementById(id)?.remove();
    const modal = createEl('div', {
        class: 'modal dg-modal', id, role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': `${id}-title`,
        onClick: (e) => { if (e.target === modal) dgCloseModal(id); },
    },
        createEl('div', { class: `modal-container dg-modal-container${wide ? ' is-wide' : ''}` },
            createEl('div', { class: 'modal-header' },
                createEl('h2', { class: 'modal-title', id: `${id}-title` },
                    createEl('i', { class: `fas ${icon}`, 'aria-hidden': 'true' }), createEl('span', { text: title })),
                createEl('button', { type: 'button', class: 'modal-close', 'aria-label': 'Sluiten', title: 'Sluiten (Esc)',
                    onClick: () => dgCloseModal(id) }, '×')),
            createEl('div', { class: 'modal-body modal-body-list dg-modal-body' }, ...body),
            footer ? createEl('div', { class: 'modal-footer dg-modal-footer' }, ...footer) : null));
    document.body.append(modal);
    registerOverlayCloser(id, () => dgCloseModal(id));
    requestAnimationFrame(() => modal.classList.add('active'));
    return modal;
}

function dgCloseModal(id) {
    const modal = document.getElementById(id);
    if (!modal) return;
    modal.classList.remove('active');
    if (id === 'dg-connect') dgStopConnectPoll();
    setTimeout(() => { if (!modal.classList.contains('active')) modal.remove(); }, 250);
}

async function dgDetail(id) {
    return fetchJSON(`${API_BASE}/api/datagatherer/sources/${encodeURIComponent(id)}`);
}

function dgSteps(steps) {
    return createEl('ol', { class: 'dg-steps' }, ...steps.map(step => createEl('li', { text: step })));
}

// ---------- Export aanvragen ----------

async function dgOpenExport(id) {
    let detail;
    try { detail = await dgDetail(id); } catch (err) { return toastFetchError('Laden mislukt', err); }
    let kind = detail.export_kinds[0];
    const stepsBox = createEl('div', { class: 'dg-export-steps' });
    const showSteps = () => stepsBox.replaceChildren(
        createEl('p', { class: 'dg-modal-text', text: `Meestal binnen ${kind.expected_days[0]} tot ${kind.expected_days[1]} dagen klaar. Zo gaat het:` }),
        dgSteps(kind.steps));
    const body = [createEl('p', { class: 'dg-modal-text',
        text: `De exportpagina van ${detail.name} opent in je eigen browser. Daar log je zelf in; DataVault ziet je wachtwoord nooit.` })];
    if (detail.export_kinds.length > 1) {
        body.push(createEl('div', { class: 'segmented dg-kind-choice', role: 'radiogroup', 'aria-label': 'Soort export' },
            ...detail.export_kinds.map((k, i) => createEl('label', { class: 'settings-choice' },
                createEl('input', { type: 'radio', class: 'radio', name: 'dg-export-kind', value: k.id, checked: i === 0,
                    onChange: () => { kind = k; showSteps(); } }),
                createEl('span', { text: k.label })))));
    }
    showSteps();
    body.push(stepsBox);
    const open = createEl('button', { type: 'button', class: 'btn btn-primary', 'data-autofocus': '' },
        createEl('i', { class: 'fas fa-arrow-up-right-from-square', 'aria-hidden': 'true' }), 'Exportpagina openen');
    open.addEventListener('click', async () => {
        open.disabled = true;
        try {
            const data = await dgPost(`/api/datagatherer/sources/${encodeURIComponent(id)}/request-export`, { kind: kind.id });
            openExternal(data.url);
            dgCloseModal('dg-export');
            const where = data.watching && data.watching.length ? 'DataVault kijkt elke 30 seconden in Downloads en leest hem dan vanzelf in.'
                : 'Kies het bestand daarna met Bestand kiezen.';
            showToast('Export aangevraagd', `Vraag hem aan in je browser. ${where}`, 'success');
        } catch (err) {
            toastFetchError('Export aanvragen mislukt', err);
            open.disabled = false;
        } finally {
            dgRefreshSoon();
        }
    });
    dgModal('dg-export', `Export aanvragen: ${detail.name}`, 'fa-file-export', body,
        [createEl('button', { type: 'button', class: 'btn btn-secondary', text: 'Annuleren', onClick: () => dgCloseModal('dg-export') }), open]);
}

// ---------- In Downloads zoeken ----------

async function dgOpenDownloads(id) {
    const src = dgSource(id);
    const list = createEl('div', { class: 'dg-candidates' }, skeletonRows(2));
    const request = src && src.open_request;
    const footer = [createEl('button', { type: 'button', class: 'btn btn-secondary', text: 'Sluiten', onClick: () => dgCloseModal('dg-downloads') })];
    if (request) {
        footer.unshift(createEl('button', { type: 'button', class: 'btn btn-ghost', text: 'Niet meer wachten',
            title: 'De aanvraag vergeten: DataVault kijkt dan niet meer in Downloads',
            onClick: async () => {
                try { await dgPost(`/api/datagatherer/requests/${request.id}/dismiss`); dgCloseModal('dg-downloads'); }
                catch (err) { toastFetchError('Mislukt', err); }
                dgRefreshSoon();
            } }));
    }
    dgModal('dg-downloads', `In Downloads zoeken: ${src ? src.name : id}`, 'fa-magnifying-glass', [list], footer);
    try {
        const data = await fetchJSON(`${API_BASE}/api/datagatherer/sources/${encodeURIComponent(id)}/candidates`);
        if (!data.folders.length) {
            list.replaceChildren(emptyState({ icon: 'fa-folder-open', title: 'Geen map om in te kijken',
                text: 'DataVault vindt je map Downloads niet. Kies het bestand met Bestand kiezen.', tone: null }));
            return;
        }
        if (!data.candidates.length) {
            list.replaceChildren(emptyState({ icon: 'fa-hourglass-half', title: 'Nog geen export gevonden',
                text: `DataVault keek in ${data.folders.map(f => displayPath(f, 40)).join(', ')}. Een download die nog bezig is, telt pas mee als hij klaar is.` }));
            return;
        }
        list.replaceChildren(createEl('ul', { class: 'dg-candidate-list' }, ...data.candidates.map(c => createEl('li', { class: 'dg-candidate' },
            createEl('i', { class: 'fas fa-file-zipper dg-candidate-icon', 'aria-hidden': 'true' }),
            createEl('div', { class: 'dg-candidate-text' },
                createEl('span', { class: 'dg-candidate-name', text: c.name, title: c.path }),
                createEl('span', { class: 'dg-candidate-meta' },
                    createEl('span', { class: 'mono', text: formatBytes(c.size) }), ` · ${dgTime(c.mtime)}`,
                    c.already ? ' · al in het archief' : '')),
            createEl('button', {
                type: 'button', class: 'btn btn-primary btn-sm', disabled: c.already, text: c.already ? 'Ingelezen' : 'Inlezen',
                onClick: async (e) => {
                    e.currentTarget.disabled = true;
                    try {
                        const data2 = await dgPost(`/api/datagatherer/sources/${encodeURIComponent(id)}/candidates/import`, { path: c.path });
                        dgAfterJob(data2.job);
                        dgCloseModal('dg-downloads');
                        showToast('Export inlezen', `${c.name} wordt naar het archief gekopieerd; het origineel blijft in Downloads staan.`, 'info');
                    } catch (err) {
                        toastFetchError('Inlezen mislukt', err);
                    }
                },
            })))));
    } catch (err) {
        list.replaceChildren(errorState('Zoeken in Downloads mislukt', err));
    }
}

// ---------- Verbinden (de eenmalige instelling van een API-bron) ----------

function dgField(field, idPrefix) {
    const id = `${idPrefix}-${field.key}`;
    const secret = field.kind === 'secret';
    const input = createEl('input', {
        class: `input${secret ? ' mono' : ''}`, id, type: secret ? 'password' : 'text', autocomplete: 'off', spellcheck: 'false',
        placeholder: secret && field.stored ? 'Opgeslagen in de Sleutelhanger (leeg laten om te houden)' : (field.placeholder || ''),
        value: secret ? undefined : (field.value || ''), dataset: { key: field.key, kind: field.kind },
    });
    return createEl('div', { class: 'dg-field' },
        createEl('label', { class: 'dg-field-label', for: id }, field.label,
            secret && field.stored ? createEl('span', { class: 'badge badge-success' },
                createEl('i', { class: 'fas fa-key', 'aria-hidden': 'true' }), 'In de Sleutelhanger') : null),
        input,
        field.help ? createEl('p', { class: 'dg-field-help', text: field.help }) : null);
}

async function dgSaveFields(id, fields) {
    const secrets = {};
    const settings = {};
    fields.forEach(input => {
        const value = input.value.trim();
        if (input.dataset.kind === 'secret') { if (value) secrets[input.dataset.key] = value; }
        else settings[input.dataset.key] = value;
    });
    if (Object.keys(settings).length) {
        await fetchJSON(`${API_BASE}/api/datagatherer/sources/${encodeURIComponent(id)}/settings`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings }),
        });
    }
    if (Object.keys(secrets).length) {
        await fetchJSON(`${API_BASE}/api/datagatherer/sources/${encodeURIComponent(id)}/secrets`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(secrets),
        });
    }
}

async function dgOpenConnect(id) {
    let detail;
    try { detail = await dgDetail(id); } catch (err) { return toastFetchError('Laden mislukt', err); }
    const setup = detail.setup;
    if (!setup) return dgOpenSettings(id);
    const fields = detail.settings.filter(f => setup.fields.includes(f.key));
    const inputs = fields.map(f => dgField(f, 'dg-connect'));
    const steps = createEl('ol', { class: 'dg-steps' }, ...setup.steps.map(step => {
        const li = createEl('li');
        const uri = setup.redirect_uri;
        if (uri && step.includes(uri)) {
            // Het adres dat letterlijk in het dashboard van de bron moet: op een eigen regel, met Kopiëren
            const [before, after] = step.split(uri);
            const copy = createEl('button', { type: 'button', class: 'btn btn-secondary btn-sm dg-copy',
                onClick: async () => {
                    try { await navigator.clipboard.writeText(uri); copyLabel.textContent = 'Gekopieerd'; }
                    catch (_) { copyLabel.textContent = 'Selecteer en kopieer'; }
                } }, createEl('i', { class: 'fas fa-copy', 'aria-hidden': 'true' }));
            const copyLabel = createEl('span', { text: 'Kopiëren' });
            copy.append(copyLabel);
            li.append(before.trim(), createEl('div', { class: 'dg-copy-row' },
                createEl('code', { class: 'dg-copy-value', text: uri }), copy), after.trim());
        } else {
            li.textContent = step;
        }
        return li;
    }));
    const status = createEl('p', { class: 'dg-connect-status', role: 'status' });
    const body = [
        createEl('p', { class: 'dg-modal-text', text: setup.intro }),
        setup.links.length ? createEl('div', { class: 'dg-links' }, ...setup.links.map(link => createEl('button', {
            type: 'button', class: 'btn btn-secondary btn-sm', onClick: () => openExternal(link.url),
        }, createEl('i', { class: 'fas fa-arrow-up-right-from-square', 'aria-hidden': 'true' }), link.label))) : null,
        steps,
        createEl('div', { class: 'dg-fields' }, ...inputs),
        createEl('p', { class: 'dg-modal-note' }, createEl('i', { class: 'fas fa-lock', 'aria-hidden': 'true' }), ` ${setup.note}`),
        status,
    ];
    const save = createEl('button', { type: 'button', class: 'btn btn-primary' },
        createEl('i', { class: `fas ${setup.oauth ? 'fa-plug' : 'fa-floppy-disk'}`, 'aria-hidden': 'true' }),
        setup.oauth ? 'Opslaan en verbinden' : 'Opslaan');
    save.addEventListener('click', async () => {
        const missing = fields.filter(f => f.required && !(f.kind === 'secret' && f.stored)
            && !document.getElementById(`dg-connect-${f.key}`).value.trim());
        if (missing.length) {
            status.textContent = `Vul eerst in: ${missing.map(f => f.label).join(', ')}.`;
            status.className = 'dg-connect-status is-error';
            document.getElementById(`dg-connect-${missing[0].key}`).focus();
            return;
        }
        save.disabled = true;
        status.className = 'dg-connect-status';
        status.textContent = 'Opslaan…';
        try {
            await dgSaveFields(id, [...document.querySelectorAll('#dg-connect .dg-field input')]);
            if (setup.oauth) {
                const data = await dgPost(`/api/datagatherer/sources/${encodeURIComponent(id)}/connect`);
                openExternal(data.authorize_url);
                status.textContent = `Geef in je browser toestemming. Dit venster ziet vanzelf wanneer ${detail.name} verbonden is.`;
                dgStartConnectPoll(id, detail.name);
            } else {
                dgCloseModal('dg-connect');
                showToast(detail.name, 'Opgeslagen. Klik op Bijwerken om je gegevens op te halen.', 'success');
            }
        } catch (err) {
            status.className = 'dg-connect-status is-error';
            status.textContent = err.message || String(err);
            save.disabled = false;
        } finally {
            dgRefreshSoon();
        }
    });
    dgModal('dg-connect', `${detail.name} verbinden`, 'fa-plug', body,
        [createEl('button', { type: 'button', class: 'btn btn-secondary', text: 'Annuleren', onClick: () => dgCloseModal('dg-connect') }), save],
        true);
    const first = document.querySelector('#dg-connect .dg-field input');
    if (first) first.setAttribute('data-autofocus', '');
}

function dgStartConnectPoll(id, name) {
    dgStopConnectPoll();
    const started = Date.now();
    dgState.connectPoll = setInterval(async () => {
        if (Date.now() - started > 10 * 60 * 1000) return dgStopConnectPoll();
        try {
            const detail = await dgDetail(id);
            if (detail.configured && !detail.needs_reconnect) {
                dgStopConnectPoll();
                dgCloseModal('dg-connect');
                showToast(`${name} is verbonden`, 'DataVault haalt nu je nieuwste gegevens op.', 'success');
                if (typeof scheduleJobPoll === 'function') scheduleJobPoll(0);
                dgRefreshSoon();
            }
        } catch (_) { /* de volgende keer opnieuw */ }
    }, 3000);
}

function dgStopConnectPoll() {
    clearInterval(dgState.connectPoll);
    dgState.connectPoll = null;
}

// ---------- Instellingen van een bron ----------

async function dgOpenSettings(id) {
    let detail;
    try { detail = await dgDetail(id); } catch (err) { return toastFetchError('Laden mislukt', err); }
    const body = [];
    const plain = detail.settings.filter(f => f.kind !== 'secret');
    const secrets = detail.settings.filter(f => f.kind === 'secret');
    if (plain.length) {
        body.push(createEl('h3', { class: 'dg-modal-section', text: 'Gegevens' }),
            createEl('div', { class: 'dg-fields' }, ...plain.map(f => dgField(f, 'dg-settings'))));
    }
    if (secrets.length) {
        body.push(createEl('h3', { class: 'dg-modal-section', text: 'Sleutels' }),
            createEl('p', { class: 'dg-modal-text', text: 'Alleen in de Sleutelhanger van macOS; nooit in het archief of de database.' }),
            ...secrets.map(f => createEl('div', { class: 'settings-row' },
                createEl('span', { class: 'settings-row-label', text: f.label }),
                createEl('span', { class: 'settings-row-value' },
                    f.stored ? createEl('span', { class: 'badge badge-success' },
                        createEl('i', { class: 'fas fa-key', 'aria-hidden': 'true' }), 'In de Sleutelhanger')
                        : createEl('span', { class: 'badge', text: 'Niet ingesteld' }),
                    f.stored ? createEl('button', { type: 'button', class: 'btn btn-ghost btn-sm', text: 'Verwijderen',
                        onClick: async () => {
                            try {
                                await fetchJSON(`${API_BASE}/api/datagatherer/sources/${encodeURIComponent(id)}/secrets/${encodeURIComponent(f.key)}`, { method: 'DELETE' });
                                dgOpenSettings(id);
                            } catch (err) { toastFetchError('Verwijderen mislukt', err); }
                            dgRefreshSoon();
                        } }) : null))));
    }
    if (detail.schedule.available) {
        const toggle = createEl('input', { type: 'checkbox', id: 'dg-schedule', checked: detail.schedule.enabled });
        const interval = createEl('select', { class: 'select select-sm', id: 'dg-schedule-minutes', 'aria-label': 'Hoe vaak' },
            ...[15, 30, 60, 120, 360].map(m => createEl('option', { value: String(m), selected: m === detail.schedule.minutes,
                text: m < 60 ? `elke ${m} minuten` : (m === 60 ? 'elk uur' : `elke ${m / 60} uur`) })));
        body.push(createEl('h3', { class: 'dg-modal-section', text: 'Schema' }),
            createEl('div', { class: 'dg-schedule-row' },
                createEl('label', { class: 'switch' }, toggle, createEl('span', { class: 'slider' })),
                createEl('label', { for: 'dg-schedule', class: 'dg-schedule-label', text: 'Automatisch bijwerken' }),
                interval),
            createEl('p', { class: 'dg-field-help', text: `Standaard uit. Aan: DataVault maakt zelf verbinding met ${detail.network.hosts.join(' en ')}, zolang de app draait. `
                + (detail.launchd ? 'Ook als de app dicht is: zet in Instellingen > DataGatherer de achtergrondtaak aan.' : '') }));
    }
    body.push(createEl('h3', { class: 'dg-modal-section', text: 'Netwerk' }),
        createEl('p', { class: 'dg-modal-text' },
            createEl('i', { class: `fas ${detail.network.local ? 'fa-laptop' : 'fa-globe'}`, 'aria-hidden': 'true' }), ` ${detail.network.label}`),
        dgLogList(detail.network_log, false));
    if (detail.raw_imports.length) {
        body.push(createEl('h3', { class: 'dg-modal-section', text: 'Exports in het archief' }),
            createEl('ul', { class: 'dg-raw-list' }, ...detail.raw_imports.map(r => createEl('li', { class: 'dg-raw' },
                createEl('i', { class: `fas ${r.is_folder ? 'fa-folder' : 'fa-file-zipper'}`, 'aria-hidden': 'true' }),
                createEl('span', { class: 'path-label', text: displayPath(r.path, 44), title: r.path }),
                createEl('span', { class: `badge ${r.status === 'imported' ? 'badge-success' : r.status === 'failed' ? 'badge-danger' : ''}`,
                    text: { imported: 'Ingelezen', failed: 'Niet leesbaar', pending: 'Nog inlezen', importing: 'Bezig' }[r.status] || r.status,
                    title: r.error || undefined })))));
    }
    const footer = [];
    if (secrets.some(f => f.stored) || detail.configured) {
        footer.push(createEl('button', { type: 'button', class: 'btn btn-danger dg-forget',
            onClick: async () => {
                if (!confirm(`Alle sleutels van ${detail.name} uit de Sleutelhanger halen? Je gegevens en exports blijven in het archief.`)) return;
                try {
                    await dgPost(`/api/datagatherer/sources/${encodeURIComponent(id)}/forget`);
                    dgCloseModal('dg-settings');
                    showToast(detail.name, 'Vergeten: de sleutels zijn uit de Sleutelhanger. Je gegevens blijven in het archief.', 'success');
                } catch (err) { toastFetchError('Vergeten mislukt', err); }
                dgRefreshSoon();
            } }, createEl('i', { class: 'fas fa-user-slash', 'aria-hidden': 'true' }), 'Vergeet dit account'));
    }
    footer.push(createEl('span', { class: 'dg-actions-spacer' }),
        createEl('button', { type: 'button', class: 'btn btn-secondary', text: 'Annuleren', onClick: () => dgCloseModal('dg-settings') }),
        createEl('button', { type: 'button', class: 'btn btn-primary', text: 'Opslaan', 'data-autofocus': '',
            onClick: async (e) => {
                const button = e.currentTarget;
                button.disabled = true;
                try {
                    await dgSaveFields(id, [...document.querySelectorAll('#dg-settings .dg-field input')]);
                    if (detail.schedule.available) {
                        await fetchJSON(`${API_BASE}/api/datagatherer/sources/${encodeURIComponent(id)}/settings`, {
                            method: 'PUT', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ schedule_enabled: document.getElementById('dg-schedule').checked,
                                schedule_minutes: Number(document.getElementById('dg-schedule-minutes').value) }),
                        });
                    }
                    dgCloseModal('dg-settings');
                    showToast(detail.name, 'Instellingen opgeslagen.', 'success');
                } catch (err) {
                    toastFetchError('Opslaan mislukt', err);
                    button.disabled = false;
                }
                dgRefreshSoon();
            } }));
    dgModal('dg-settings', `Instellingen: ${detail.name}`, 'fa-sliders', body, footer, true);
}

// ---------- Netwerklog ----------

function dgLogList(entries, withSource) {
    if (!entries.length) {
        return createEl('p', { class: 'dg-modal-text dg-log-empty', text: 'Nog geen verbindingen gemaakt.' });
    }
    const outcome = { ok: ['Gelukt', 'badge-success'], refused: ['Geweigerd', 'badge-warning'],
        http_error: ['Fout', 'badge-danger'], error: ['Geen verbinding', 'badge-danger'] };
    const trigger = { user: 'jouw klik', schedule: 'schema' };
    return createEl('ul', { class: 'dg-log' }, ...entries.map(e => createEl('li', { class: 'dg-log-row' },
        createEl('span', { class: 'dg-log-time mono', text: dgTime(e.at) }),
        createEl('span', { class: 'dg-log-what' },
            withSource ? createEl('strong', { text: `${e.source_name} ` }) : null,
            createEl('span', { class: 'mono dg-log-url', text: `${e.method} ${e.host}${e.path || ''}`, title: e.note || '' })),
        createEl('span', { class: 'dg-log-trigger', text: trigger[e.trigger] || e.trigger }),
        createEl('span', { class: `badge ${(outcome[e.outcome] || ['', ''])[1]}`,
            text: `${(outcome[e.outcome] || [e.outcome])[0]}${e.status ? ` ${e.status}` : ''}` }))));
}

async function dgOpenNetworkLog() {
    const box = createEl('div', { class: 'dg-log-box' }, skeletonRows(3));
    dgModal('dg-network', 'Netwerklog', 'fa-globe', [
        createEl('p', { class: 'dg-modal-text', text: 'Elke verbinding die DataGatherer maakte of weigerde, nieuwste eerst. '
            + 'Zonder de rest van het adres: daar kan een sleutel in staan.' }), box],
    [createEl('button', { type: 'button', class: 'btn btn-secondary', text: 'Sluiten', 'data-autofocus': '', onClick: () => dgCloseModal('dg-network') })],
    true);
    try {
        const data = await fetchJSON(`${API_BASE}/api/datagatherer/network-log?limit=200`);
        box.replaceChildren(dgLogList(data.entries, true));
    } catch (err) {
        box.replaceChildren(errorState('Netwerklog laden mislukt', err));
    }
}

// ==================== AANSLUITING OP DE REST VAN DE APP ====================

// Een taak van een bron verandert: de kaart toont de voortgang; klaar = opnieuw ophalen
if (typeof jobsOnChange === 'function') {
    jobsOnChange((job) => {
        if (!job.type.startsWith('datagatherer.') || !dgVisible()) return;
        if (!JOB_ACTIVE.has(job.status)) { dgRender(); dgRefreshSoon(); return; }
        const id = (job.params && job.params.source) || job.type.split('.').pop();
        const card = document.querySelector(`.dg-card[data-source="${CSS.escape(id)}"]`);
        const progress = card && card.querySelector('.dg-progress');
        if (progress) progress.replaceWith(dgProgress(job));
        else dgRefreshSoon();
    });
}

// Nieuwe gegevens van een bron: wie ze toont (bijv. het Spotify-scherm), luistert naar dit event
if (typeof jobsOnFinished === 'function') {
    jobsOnFinished((job) => {
        if (!job.type.startsWith('datagatherer.') || job.status !== 'done') return;
        const source = (job.params && job.params.source) || job.type.split('.').pop();
        window.dispatchEvent(new CustomEvent('datavault:data-changed', { detail: { source } }));
    });
}

// De pagina na 'Verbonden' in de browser: /#datagatherer opent meteen deze tab
document.addEventListener('DOMContentLoaded', () => {
    if (window.location.hash === '#datagatherer') {
        history.replaceState(null, '', window.location.pathname);
        switchMainView('datagatherer');
    }
});

// Instellingen: de achtergrondtaak (launchd) die ook ophaalt als DataVault dicht is
registerSettingsSection({
    id: 'datagatherer',
    title: 'DataGatherer',
    icon: 'fa-cloud-arrow-down',
    order: 70,
    description: 'DataGatherer gaat alleen online als jij op een knop drukt, of voor een bron waarvan je het schema aanzette. '
        + 'Wachtwoorden bewaart DataVault nooit; sleutels staan alleen in de Sleutelhanger.',
    render(body) {
        const status = createEl('span', { text: 'Laden…' });
        const button = createEl('button', { type: 'button', class: 'btn btn-secondary', id: 'settings-dg-launchd', disabled: true, text: '…' });
        const explain = createEl('p', { class: 'dg-field-help dg-settings-note', text: 'Sommige bronnen geven alleen je laatste gegevens terug. '
            + 'Met deze achtergrondtaak (launchd, elke 30 minuten) haalt DataVault ze ook op als de app dicht is, voor bronnen '
            + 'waarvan het schema aan staat. Zonder de T7 doet hij niets.' });
        body.append(
            settingsRow('Ook als DataVault dicht is', status),
            explain,
            createEl('div', { class: 'settings-actions' }, button,
                createEl('button', { type: 'button', class: 'btn btn-secondary', text: 'Naar DataGatherer',
                    onClick: () => switchMainView('datagatherer') })));
        fetchJSON(`${API_BASE}/api/datagatherer/launchd`).then(info => {
            status.textContent = info.installed ? 'Aan (elke 30 minuten)' : 'Uit';
            if (info.source_names && info.source_names.length) {
                explain.textContent = `${info.source_names.join(' en ')} geven alleen je laatste gegevens terug. Met deze achtergrondtaak `
                    + '(launchd, elke 30 minuten) haalt DataVault ze ook op als de app dicht is, als hun schema aan staat. '
                    + 'Zonder de T7 doet hij niets.';
            }
            button.disabled = !info.available;
            button.textContent = info.installed ? 'Achtergrondtaak uitzetten' : 'Achtergrondtaak aanzetten';
            button.addEventListener('click', async () => {
                button.disabled = true;
                try {
                    await dgPost(`/api/datagatherer/launchd/${info.installed ? 'uninstall' : 'install'}`);
                    showToast('DataGatherer', info.installed ? 'De achtergrondtaak staat uit.' : 'De achtergrondtaak staat aan.', 'success');
                } catch (err) {
                    toastFetchError('Achtergrondtaak', err);
                }
                settingsRefreshSection('datagatherer');
            });
        }).catch(() => { status.textContent = 'Onbekend (is het archief bereikbaar?)'; });
    },
});
