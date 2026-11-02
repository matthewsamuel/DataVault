// DataVault Dashboard - Back-ups
// Note: API_BASE is defined in app.js; dgModal, dgCloseModal, dgPost and dgField come from datagatherer.js
//
// Hoe lang geleden elke bron veiliggesteld is (/api/datagatherer/backups): de bronnen van
// DataGatherer (laatste keer bijgewerkt of laatste export binnen) en je eigen lijst van wat
// DataGatherer niet kan bereiken, zoals een oude iPad, een gsm in de lade of een usb-stick.
// Per eigen bron: waar de back-up staat, wanneer hij gemaakt is en hoe ver het is (Nog te doen,
// Bezig, Veiliggesteld). Home toont het kort (homeRenderBackups), DataGatherer > Back-ups helemaal.

const BK_STATUSES = [
    { id: 'te_doen', label: 'Nog te doen' },
    { id: 'bezig', label: 'Bezig' },
    { id: 'veilig', label: 'Veiliggesteld' },
];
const BK_KINDS = [
    { id: 'toestel', label: 'Toestel' },
    { id: 'computer', label: 'Computer' },
    { id: 'schijf', label: 'Schijf of stick' },
    { id: 'account', label: 'Account' },
    { id: 'anders', label: 'Anders' },
];
const BK_HEALTH = {
    goed: 'Recent veiliggesteld',
    let_op: 'Een tijd geleden',
    oud: 'Lang geleden',
    nooit: 'Nog geen back-up',
};
const BK_HOME_MAX = 7;

const bkState = { data: null, seq: 0 };

function backupsInit() {
    bkLoad();
}

async function bkFetch() {
    return fetchJSON(`${API_BASE}/api/datagatherer/backups`);
}

// ==================== TIJD ====================

// '3 dagen', '5 weken', '4 maanden', '2 jaar': kort genoeg voor een rij op Home
function bkAge(seconds) {
    if (!seconds) return null;
    const days = Math.max(0, Math.floor((Date.now() / 1000 - seconds) / 86400));
    if (days === 0) return 'Vandaag';
    if (days === 1) return 'Gisteren';
    if (days < 14) return `${days} dagen geleden`;
    if (days < 60) return `${Math.round(days / 7)} weken geleden`;
    if (days < 365) return `${Math.round(days / 30.4)} maanden geleden`;
    const years = Math.floor(days / 365);
    return `${years} jaar geleden`;
}

function bkLongDate(seconds) {
    return new Date(seconds * 1000).toLocaleDateString('nl-NL', { day: 'numeric', month: 'long', year: 'numeric' });
}

// Wat er rechts in een rij staat, en de volledige uitleg voor de tooltip
function bkWhen(item) {
    const age = bkAge(item.last_at);
    if (age) {
        const tip = `${BK_HEALTH[item.health]}: ${bkLongDate(item.last_at)}`
            + (item.requested_at ? ` · nieuwe export aangevraagd ${bkLongDate(item.requested_at)}` : '');
        return { text: age, tip };
    }
    if (item.requested_at) return { text: 'Aangevraagd', tip: `Export aangevraagd ${bkLongDate(item.requested_at)}`, requested: true };
    if (item.status === 'bezig') return { text: 'Bezig', tip: 'Back-up bezig' };
    return { text: 'Nog geen', tip: BK_HEALTH.nooit };
}

// ==================== EEN RIJ ====================

function bkDot(item, when) {
    const health = when && when.requested && !item.last_at ? 'aangevraagd' : item.health;
    return createEl('span', { class: `bk-dot bk-dot-${health}`, 'aria-hidden': 'true' });
}

function bkOpenItem(item) {
    if (item.origin === 'bron' && typeof dgShowSource === 'function') return dgShowSource(item.id);
    switchMainView('backups');
    bkReveal(item.id);
}

// Op Home: stip, naam en hoe lang geleden; de hele rij opent de bron
function bkHomeRow(item) {
    const when = bkWhen(item);
    const row = createEl('button', {
        type: 'button', class: 'bk-home-row', title: when.tip,
        'aria-label': `${item.name}: ${when.tip}`, onClick: () => bkOpenItem(item),
    },
        bkDot(item, when),
        createEl('i', { class: `${item.icon} bk-home-icon`, 'aria-hidden': 'true' }),
        createEl('span', { class: 'bk-home-name', text: item.name }),
        createEl('span', { class: `bk-home-when${when.requested ? ' is-requested' : ''}`, text: when.text }));
    return row;
}

// ==================== HOME ====================

async function homeLoadBackups() {
    const box = document.getElementById('home-backups-list');
    if (!box) return;
    if (!box.childElementCount) box.replaceChildren(skeletonRows(2));
    try {
        homeRenderBackups(await bkFetch());
    } catch (err) {
        document.getElementById('home-backups-meta').textContent = '';
        box.replaceChildren(errorState('De back-ups konden niet geladen worden', err, homeLoadBackups));
    }
}

function homeRenderBackups(data) {
    const box = document.getElementById('home-backups-list');
    const meta = document.getElementById('home-backups-meta');
    if (!box) return;
    const { items, summary } = data;
    if (!items.length) {
        meta.textContent = '';
        box.replaceChildren(emptyState({
            icon: 'fa-shield-halved', title: 'Nog niets veiliggesteld',
            text: 'Vraag je gegevens op in DataGatherer of zet je oude toestellen in je lijst.',
            action: { label: 'Naar back-ups', onClick: () => switchMainView('backups') },
        }));
        box.firstChild.classList.add('empty-state-compact');
        return;
    }
    meta.textContent = `${summary.good} van ${summary.total} recent`;
    const shown = items.slice(0, BK_HOME_MAX);
    const rows = shown.map(bkHomeRow);
    if (items.length > shown.length) {
        rows.push(createEl('button', { type: 'button', class: 'bk-home-row bk-home-more',
            onClick: () => switchMainView('backups'), text: `+${items.length - shown.length} meer` }));
    }
    const first = !box.querySelector('.bk-home-row');
    box.replaceChildren(createEl('div', { class: 'bk-home-grid' }, ...rows));
    if (first && window.Motion) Motion.stagger(rows, 'rise', { key: 'enter' });
}

// ==================== DE PAGINA ====================

async function bkLoad() {
    const box = document.getElementById('bk-content');
    if (!box) return;
    const seq = ++bkState.seq;
    if (!bkState.data) box.replaceChildren(skeletonRows(4));
    try {
        const [data] = await Promise.all([bkFetch(), bkOwnList()]);
        if (seq !== bkState.seq) return;
        bkState.data = data;
        bkRender();
        homeRenderBackups(data);
    } catch (err) {
        if (seq !== bkState.seq) return;
        bkState.data = null;
        box.replaceChildren(errorState('De back-ups konden niet geladen worden', err, bkLoad));
    }
}

function bkRender() {
    const box = document.getElementById('bk-content');
    const data = bkState.data;
    if (!box || !data) return;
    // Je eigen lijst in je eigen volgorde: een rij springt niet weg als je de stand aanklikt
    const byId = new Map(data.items.filter(i => i.origin === 'eigen').map(i => [i.id, i]));
    const own = (bkState.own || []).map(r => byId.get(r.id)).filter(Boolean);
    const sources = data.items.filter(i => i.origin === 'bron');
    const first = !box.querySelector('.bk-section');
    box.replaceChildren(
        bkProgress(data.summary),
        createEl('section', { class: 'bk-section', 'aria-labelledby': 'bk-own-title' },
            createEl('div', { class: 'bk-section-head' },
                createEl('h2', { class: 'section-title bk-section-title', id: 'bk-own-title' },
                    createEl('span', { text: 'Je eigen bronnen' }),
                    createEl('span', { class: 'dg-group-count mono', text: String(own.length) })),
                own.length ? createEl('button', { type: 'button', class: 'btn btn-primary btn-sm', onClick: () => bkOpenEditor(null) },
                    createEl('i', { class: 'fas fa-plus', 'aria-hidden': 'true' }), 'Bron toevoegen') : null),
            createEl('p', { class: 'bk-section-text',
                text: 'Oude toestellen, schijven en accounts die DataGatherer niet zelf kan bijwerken. Noteer waar de back-up staat, dan vind je hem later terug.' }),
            own.length ? createEl('ul', { class: 'bk-list', role: 'list' }, ...own.map(bkOwnRow))
                : emptyState({ icon: 'fa-tablet-screen-button', title: 'Nog geen eigen bronnen',
                    text: 'Zet hier bijvoorbeeld je oude iPad, met waar zijn back-up staat.',
                    action: { label: 'Bron toevoegen', primary: true, onClick: () => bkOpenEditor(null) } })),
        createEl('section', { class: 'bk-section', 'aria-labelledby': 'bk-dg-title' },
            createEl('div', { class: 'bk-section-head' },
                createEl('h2', { class: 'section-title bk-section-title', id: 'bk-dg-title' },
                    createEl('span', { text: 'Bronnen in DataGatherer' }),
                    createEl('span', { class: 'dg-group-count mono', text: String(sources.length) }))),
            createEl('p', { class: 'bk-section-text',
                text: 'De laatste keer dat ze bijgewerkt zijn of dat er een export binnenkwam.' }),
            sources.length ? createEl('ul', { class: 'bk-list', role: 'list' }, ...sources.map(bkSourceRow))
                : emptyState({ icon: 'fa-cloud-arrow-down', title: 'Nog geen gegevens',
                    text: 'Vraag in DataGatherer een export aan of werk een bron bij.',
                    action: { label: 'Naar bronnen', onClick: () => switchMainView('datagatherer') } })));
    if (first && window.Motion) Motion.stagger(box.querySelectorAll('.bk-progress, .bk-row'), 'rise', { key: 'enter' });
    bkReveal();
}

// De voortgang van je eigen lijst: hoeveel er veiliggesteld is
function bkProgress(summary) {
    const total = summary.own_total;
    const safe = summary.own_safe;
    const busy = summary.own_busy;
    const pct = total ? Math.round((safe / total) * 100) : 0;
    const legend = (cls, n, label) => createEl('span', { class: 'bk-legend-item' },
        createEl('span', { class: `bk-dot ${cls}`, 'aria-hidden': 'true' }),
        createEl('span', { class: 'mono', text: formatNumberNl(n) }), ` ${label}`);
    return createEl('div', { class: 'card bk-progress' },
        createEl('div', { class: 'bk-progress-head' },
            createEl('div', {},
                createEl('div', { class: 'stat-tile-label' },
                    createEl('i', { class: 'fas fa-list-check', 'aria-hidden': 'true' }), 'Je eigen lijst'),
                createEl('div', { class: 'bk-progress-value' },
                    createEl('span', { class: 'mono', text: formatNumberNl(safe) }),
                    createEl('span', { class: 'bk-progress-of', text: ` van ${formatNumberNl(total)} veiliggesteld` }))),
            createEl('div', { class: 'bk-legend' },
                legend('bk-dot-goed', safe, 'veiliggesteld'),
                legend('bk-dot-let_op', busy, 'bezig'),
                legend('bk-dot-nooit', total - safe - busy, 'nog te doen'))),
        createEl('div', { class: 'progress bk-progress-bar', role: 'progressbar', 'aria-label': 'Veiliggesteld',
            'aria-valuemin': '0', 'aria-valuemax': String(total || 0), 'aria-valuenow': String(safe) },
            createEl('div', { class: 'progress-fill', style: `width: ${pct}%` })),
        createEl('p', { class: 'stat-tile-meta',
            text: `${formatNumberNl(summary.good)} van ${formatNumberNl(summary.total)} bronnen samen zijn veiliggesteld in de laatste ${bkState.data.good_days} dagen.` }));
}

function bkSourceRow(item) {
    const when = bkWhen(item);
    return createEl('li', { class: 'bk-row', dataset: { key: item.key } },
        bkDot(item, when),
        createEl('span', { class: 'bk-row-icon', 'aria-hidden': 'true' }, createEl('i', { class: item.icon })),
        createEl('div', { class: 'bk-row-main' },
            createEl('div', { class: 'bk-row-name', text: item.name }),
            createEl('div', { class: 'bk-row-meta', text: item.requested_at
                ? `Nieuwe export aangevraagd ${bkLongDate(item.requested_at)}` : BK_HEALTH[item.health] })),
        createEl('span', { class: 'bk-row-when', title: when.tip, text: item.last_at ? bkLongDate(item.last_at) : when.text }),
        createEl('button', { type: 'button', class: 'btn btn-ghost btn-sm', onClick: () => dgShowSource(item.id) },
            'Openen', createEl('i', { class: 'fas fa-arrow-right', 'aria-hidden': 'true' })));
}

function bkOwnRow(item) {
    const when = bkWhen(item);
    const statusGroup = createEl('div', { class: 'segmented bk-status', role: 'group',
        'aria-label': `Stand van ${item.name}` },
        ...BK_STATUSES.map(s => createEl('button', {
            type: 'button', class: item.status === s.id ? 'active' : '', 'aria-pressed': String(item.status === s.id),
            dataset: { bkFocus: `status-${item.id}-${s.id}` },
            onClick: () => { if (item.status !== s.id) bkSave(item.id, bkStatusChange(item, s.id)); },
        }, s.label)));
    return createEl('li', { class: 'bk-row bk-row-own', dataset: { key: item.key, own: String(item.id) } },
        bkDot(item, when),
        createEl('span', { class: 'bk-row-icon', 'aria-hidden': 'true' }, createEl('i', { class: item.icon })),
        createEl('div', { class: 'bk-row-main' },
            createEl('div', { class: 'bk-row-name' }, item.name,
                item.in_use ? createEl('span', { class: 'badge', text: 'In gebruik' }) : null),
            createEl('div', { class: 'bk-row-meta' }, bkOwnLocation(item.id))),
        createEl('span', { class: 'bk-row-when', title: when.tip, text: item.last_at ? bkLongDate(item.last_at) : 'Geen datum' }),
        statusGroup,
        createEl('div', { class: 'bk-row-actions' },
            createEl('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-sm', title: 'Bewerken',
                'aria-label': `${item.name} bewerken`, dataset: { bkFocus: `edit-${item.id}` },
                onClick: () => bkOpenEditor(item.id) }, createEl('i', { class: 'fas fa-pen', 'aria-hidden': 'true' })),
            createEl('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-sm bk-remove', title: 'Uit de lijst halen',
                'aria-label': `${item.name} uit de lijst halen`, onClick: () => bkRemove(item.id) },
                createEl('i', { class: 'fas fa-trash-can', 'aria-hidden': 'true' }))));
}

// Waar de back-up staat (technisch: mono), of de soort als er niets genoteerd is
function bkOwnLocation(id) {
    const row = (bkState.own || []).find(r => r.id === id);
    if (!row) return '';
    const parts = [createEl('span', { text: row.kind_label })];
    if (row.location) parts.push(' · ', createEl('code', { class: 'bk-path', text: row.location }));
    if (row.note) parts.push(' · ', createEl('span', { text: row.note }));
    return createEl('span', {}, ...parts);
}

// 'Veiliggesteld' zonder datum: dan is dat vandaag
function bkStatusChange(item, status) {
    const body = { status };
    if (status === 'veilig' && !item.last_at) body.backup_at = Date.now() / 1000;
    return body;
}

// Na opnieuw tekenen: de rij in beeld (vanaf Home) of de focus terug op dezelfde knop
function bkReveal(id) {
    if (id !== undefined) bkState.reveal = id;
    const target = bkState.reveal;
    if (target === undefined || target === null) return;
    const row = document.querySelector(`.bk-row[data-own="${CSS.escape(String(target))}"]`);
    if (!row) return;
    bkState.reveal = null;
    const reduce = window.Motion ? !Motion.full() : false;
    row.scrollIntoView({ block: 'center', behavior: reduce ? 'auto' : 'smooth' });
    row.classList.add('is-flash');
    setTimeout(() => row.classList.remove('is-flash'), 1600);
}

// ==================== OPSLAAN ====================

async function bkOwnList() {
    const data = await fetchJSON(`${API_BASE}/api/datagatherer/own-sources`);
    bkState.own = data.sources;
    return data.sources;
}

async function bkReload(focusKey) {
    await bkLoad();
    if (focusKey) {
        const again = document.querySelector(`#bk-content [data-bk-focus="${CSS.escape(focusKey)}"]`);
        if (again) again.focus({ preventScroll: true });
    }
}

async function bkSave(id, body) {
    const focus = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.bkFocus : null;
    try {
        await fetchJSON(`${API_BASE}/api/datagatherer/own-sources/${id}`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
    } catch (err) {
        toastFetchError('Bewaren mislukt', err);
    }
    // Is de stand veranderd, dan staat de focus op de knop van de nieuwe stand
    await bkReload(body.status ? `status-${id}-${body.status}` : focus);
}

async function bkRemove(id) {
    const row = (bkState.own || []).find(r => r.id === id);
    if (!row) return;
    let removed;
    try {
        removed = (await fetchJSON(`${API_BASE}/api/datagatherer/own-sources/${id}`, { method: 'DELETE' })).removed;
    } catch (err) {
        toastFetchError(`${row.name} uit de lijst halen mislukt`, err);
        return;
    }
    const el = document.querySelector(`.bk-row[data-own="${id}"]`);
    if (el && window.Motion) await Motion.exit(el).finished;
    bkReload();
    const fields = ['id', 'name', 'kind', 'location', 'note', 'status', 'in_use', 'backup_at', 'position', 'created_at', 'updated_at'];
    showUndoToast({
        title: 'Uit de lijst gehaald',
        message: `${row.name} staat niet meer in je lijst. Je bestanden zijn niet aangeraakt.`,
        key: `eigen-bron:${id}:${removed.updated_at}`,
        undo: async () => dgPost('/api/datagatherer/own-sources/restore',
            Object.fromEntries(fields.map(f => [f, removed[f]]))),
        onUndone: () => { bkReload(); return `${row.name} staat terug in je lijst.`; },
    });
}

// ==================== HET VENSTER ====================

function bkToDateInput(seconds) {
    if (!seconds) return '';
    const d = new Date(seconds * 1000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function bkFromDateInput(value) {
    if (!value) return null;
    const [y, m, d] = value.split('-').map(Number);
    return new Date(y, m - 1, d, 12).getTime() / 1000;
}

async function bkOpenEditor(id) {
    if (!bkState.own) {
        try { await bkOwnList(); } catch (err) { toastFetchError('Je lijst kon niet geladen worden', err); return; }
    }
    const row = id === null ? null : bkState.own.find(r => r.id === id);
    const v = row || { name: '', kind: 'toestel', location: '', note: '', status: 'te_doen', in_use: false, backup_at: null };
    const pid = 'bk-edit';
    const name = createEl('input', { class: 'input', id: `${pid}-name`, type: 'text', value: v.name, maxlength: '80',
        placeholder: 'Bijvoorbeeld Oude iPad (2014)', autocomplete: 'off' });
    const kind = createEl('select', { class: 'select', id: `${pid}-kind` },
        ...BK_KINDS.map(k => createEl('option', { value: k.id, selected: v.kind === k.id, text: k.label })));
    const location = createEl('input', { class: 'input mono', id: `${pid}-location`, type: 'text', value: v.location || '',
        maxlength: '300', placeholder: 'Bijvoorbeeld HDD back-up / ipad_2014', autocomplete: 'off', spellcheck: 'false' });
    const date = createEl('input', { class: 'input bk-date', id: `${pid}-date`, type: 'date', value: bkToDateInput(v.backup_at),
        max: bkToDateInput(Date.now() / 1000) });
    let status = v.status;
    const statusGroup = createEl('div', { class: 'segmented bk-status', role: 'group', 'aria-labelledby': `${pid}-status-label` },
        ...BK_STATUSES.map(s => createEl('button', {
            type: 'button', class: status === s.id ? 'active' : '', 'aria-pressed': String(status === s.id),
            onClick: (e) => {
                status = s.id;
                statusGroup.querySelectorAll('button').forEach(b => {
                    const on = b === e.currentTarget;
                    b.classList.toggle('active', on);
                    b.setAttribute('aria-pressed', String(on));
                });
            },
        }, s.label)));
    const inUse = createEl('input', { type: 'checkbox', id: `${pid}-in-use`, role: 'switch', checked: !!v.in_use });
    const note = createEl('textarea', { class: 'input bk-note', id: `${pid}-note`, rows: '2', maxlength: '1000',
        placeholder: 'Wat staat erop, wat ontbreekt nog' }, v.note || '');
    const error = createEl('p', { class: 'dg-field-error', id: `${pid}-name-error`, hidden: true });
    const field = (label, control, help, extra) => createEl('div', { class: 'dg-field' },
        createEl('label', { class: 'dg-field-label', for: control.id, id: extra }, label), control,
        help ? createEl('p', { class: 'dg-field-help', text: help }) : null);

    const save = createEl('button', { type: 'button', class: 'btn btn-primary' },
        createEl('i', { class: 'fas fa-check', 'aria-hidden': 'true' }), row ? 'Bewaren' : 'Toevoegen');
    const submit = async () => {
        if (!name.value.trim()) {
            dgFieldError(name, 'Geef de bron een naam.');
            name.focus();
            return;
        }
        const body = { name: name.value, kind: kind.value, location: location.value || null, note: note.value || null,
            status, in_use: inUse.checked, backup_at: bkFromDateInput(date.value) };
        if (body.status === 'veilig' && !body.backup_at && !row) body.backup_at = Date.now() / 1000;
        save.disabled = true;
        save.classList.add('is-loading');
        try {
            const res = row
                ? await fetchJSON(`${API_BASE}/api/datagatherer/own-sources/${row.id}`, {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
                : await dgPost('/api/datagatherer/own-sources', body);
            dgCloseModal(pid);
            showToast(row ? 'Bewaard' : 'Toegevoegd', `${res.source.name} staat in je lijst.`, 'success');
            bkState.reveal = res.source.id;
            bkReload();
        } catch (err) {
            dgFieldError(name, (err && err.message) || 'Bewaren mislukt.');
        } finally {
            if (save.isConnected) { save.disabled = false; save.classList.remove('is-loading'); }
        }
    };
    save.addEventListener('click', submit);
    name.addEventListener('input', () => dgFieldError(name, null));
    const modal = dgModal(pid, row ? `${row.name} bewerken` : 'Bron toevoegen', row ? 'fa-pen' : 'fa-plus', [
        createEl('div', { class: 'dg-fields' },
            createEl('div', { class: 'dg-field' },
                createEl('label', { class: 'dg-field-label', for: name.id }, 'Naam'), name, error),
            createEl('div', { class: 'bk-field-pair' },
                field('Soort', kind),
                field('Datum van de back-up', date, 'Leeg als er nog geen is.')),
            field('Waar staat de back-up', location, 'Een schijf, een map of een plek, zodat je hem later terugvindt.'),
            createEl('div', { class: 'dg-field' },
                createEl('span', { class: 'dg-field-label', id: `${pid}-status-label` }, 'Stand'), statusGroup),
            createEl('div', { class: 'dg-field dg-field-toggle' },
                createEl('div', { class: 'dg-schedule-row' },
                    createEl('label', { class: 'switch' }, inUse, createEl('span', { class: 'slider' })),
                    createEl('label', { class: 'dg-schedule-label', for: inUse.id, text: 'Krijgt nog nieuwe gegevens' })),
                createEl('p', { class: 'dg-field-help',
                    text: 'Aan voor een toestel dat je nog gebruikt: dan wordt een back-up na een maand oud. Uit voor een oud toestel: veiliggesteld blijft veiliggesteld.' })),
            field('Notitie', note)),
    ], [
        createEl('button', { type: 'button', class: 'btn btn-secondary', onClick: () => dgCloseModal(pid) }, 'Annuleren'),
        save,
    ]);
    modal.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.target.tagName === 'INPUT') { e.preventDefault(); submit(); }
    });
    setTimeout(() => name.focus(), 60);
}
