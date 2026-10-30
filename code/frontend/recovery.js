// DataVault Dashboard - Herstelcentrum
// Note: API_BASE is defined in app.js
//
// Eén lijst, nieuwste eerst, van alles wat de app in het archief verplaatste: Keurer
// (prullenmand, verplaatsen), duplicaten opruimen, Verplaatsen in de viewer, en later
// elke nieuwe soort actie die zich op de server aanmeldt (backend/recovery.py). Per actie:
// wat waarheen ging, wanneer, en een knop Terugzetten. Kan iets niet terug (het bestand is
// weg, of op de oude plek staat al iets), dan staat de reden erbij.
//
// De server zegt per actie welk verzoek hem terugzet (entry.undo = {url, body}); deze pagina
// roept precies dat aan, zodat Keurer en duplicaten hun eigen, bestaande undo houden.
// Een nieuwe soort actie heeft hier dus niets nodig; alleen een eigen icoon kan erbij in
// RECOVERY_ICONS.

const RECOVERY_PAGE = 50;
const RECOVERY_ICONS = {
    keurer: 'fa-images',
    duplicates: 'fa-clone',
    viewer_move: 'fa-right-left',
    near_dup: 'fa-layer-group',
};

const recoveryState = {
    entries: [],
    total: 0,
    nextOffset: null,
    loading: false,
    loaded: false,
    seq: 0,
};

function recoveryInit() {
    recoveryLoad();
}

async function recoveryLoad() {
    const list = document.getElementById('recovery-list');
    if (!list) return;
    const seq = ++recoveryState.seq;
    recoveryState.loading = true;
    if (!recoveryState.loaded) list.replaceChildren(skeletonRows(4));
    try {
        const data = await fetchJSON(`${API_BASE}/api/recovery?limit=${Math.max(RECOVERY_PAGE, recoveryState.entries.length)}`);
        if (seq !== recoveryState.seq) return;
        recoveryState.entries = data.entries || [];
        recoveryState.total = data.total || 0;
        recoveryState.nextOffset = data.next_offset;
        recoveryState.loaded = true;
        recoveryRender();
    } catch (err) {
        if (seq !== recoveryState.seq) return;
        recoveryState.loaded = false;
        document.getElementById('recovery-summary').textContent = '';
        document.getElementById('recovery-more').hidden = true;
        list.replaceChildren(errorState('Het Herstelcentrum kon niet geladen worden', err, recoveryLoad));
    } finally {
        if (seq === recoveryState.seq) recoveryState.loading = false;
    }
}

async function recoveryLoadMore() {
    if (recoveryState.loading || recoveryState.nextOffset === null) return;
    recoveryState.loading = true;
    const button = document.querySelector('#recovery-more button');
    if (button) button.disabled = true;
    try {
        const data = await fetchJSON(`${API_BASE}/api/recovery?offset=${recoveryState.nextOffset}&limit=${RECOVERY_PAGE}`);
        const known = new Set(recoveryState.entries.map(e => e.key));
        recoveryState.entries.push(...(data.entries || []).filter(e => !known.has(e.key)));
        recoveryState.total = data.total || recoveryState.total;
        recoveryState.nextOffset = data.next_offset;
        recoveryRender();
    } catch (err) {
        toastFetchError('Meer laden mislukt', err);
    } finally {
        recoveryState.loading = false;
        if (button) button.disabled = false;
    }
}

function recoveryWhen(seconds) {
    if (!seconds) return 'onbekend moment';
    const date = new Date(seconds * 1000);
    const today = new Date();
    const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    const time = date.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
    if (date.toDateString() === today.toDateString()) return `vandaag ${time}`;
    if (date.toDateString() === yesterday.toDateString()) return `gisteren ${time}`;
    return date.toLocaleString('nl-NL', { dateStyle: 'medium', timeStyle: 'short' });
}

// Een map zoals overal in de app: vanaf het archief, lang ingekort in het midden (displayPath);
// het hele pad staat in de tooltip
function recoveryShortDir(dir) {
    return displayPath(dir || '', 40);
}

// ... en de map waar een bestand in staat
function recoveryShortFolder(path) {
    return recoveryShortDir((path || '').slice(0, (path || '').lastIndexOf('/')));
}

function recoveryFileRow(file) {
    return createEl('li', { class: 'recovery-file' + (file.restorable ? '' : ' blocked') },
        createEl('span', { class: 'recovery-file-name', text: file.name, title: file.from }),
        createEl('span', { class: 'recovery-file-move' },
            createEl('span', { class: 'recovery-path', text: recoveryShortFolder(file.from), title: file.from }),
            createEl('i', { class: 'fas fa-arrow-right', 'aria-hidden': 'true' }),
            createEl('span', { class: 'sr-only', text: ' naar ' }),
            createEl('span', { class: 'recovery-path', text: recoveryShortFolder(file.to), title: file.to })),
        file.reason ? createEl('span', { class: 'recovery-file-reason' + (file.restorable ? ' note' : ''), text: file.reason }) : null);
}

function recoveryEntryCard(entry) {
    const count = entry.file_count;
    const shown = entry.files || [];
    const files = createEl('ul', { class: 'recovery-files' }, ...shown.map(recoveryFileRow));
    let fileBlock = files;
    if (count > 1) {
        const more = count > shown.length ? ` (de eerste ${shown.length} getoond)` : '';
        fileBlock = createEl('details', { class: 'recovery-details' },
            createEl('summary', null, createEl('i', { class: 'fas fa-chevron-right recovery-chevron', 'aria-hidden': 'true' }),
                `${count} bestanden${more}`), files);
    }

    const button = createEl('button', {
        type: 'button',
        class: 'btn btn-secondary btn-small recovery-undo',
        disabled: !entry.undoable,
        text: 'Terugzetten',
        'aria-label': `Terugzetten: ${entry.title}`,
        title: entry.undoable ? 'Naar de oorspronkelijke plek terugzetten' : (entry.reason || 'Kan niet terug'),
        onClick: (e) => recoveryUndo(entry, e.currentTarget),
    });

    // Bij een grote actie controleert de lijst niet elk bestand (dat doet het terugzetten zelf):
    // 'checked' zegt hoeveel bestanden 'restorable' telt
    const checked = typeof entry.checked === 'number' ? entry.checked : count;
    let partial = null;
    if (entry.restorable < checked) {
        partial = checked < count
            ? `${entry.restorable} van de ${checked} gecontroleerde bestanden kunnen terug; de overige ${count - checked} worden bij het terugzetten gecontroleerd.`
            : `${entry.restorable} van ${count} kunnen terug; de rest staat hieronder met de reden.`;
    }
    const status = entry.undoable
        ? (partial ? createEl('div', { class: 'recovery-status partial', text: partial }) : null)
        : createEl('div', { class: 'recovery-status blocked', role: 'note' },
            createEl('i', { class: 'fas fa-circle-exclamation', 'aria-hidden': 'true' }),
            ` Kan niet terug: ${entry.reason || 'onbekende reden'}`);

    return createEl('article', { class: 'recovery-entry' + (entry.undoable ? '' : ' is-blocked'), dataset: { key: entry.key } },
        createEl('div', { class: 'recovery-entry-head' },
            createEl('span', { class: 'recovery-icon', 'aria-hidden': 'true' },
                createEl('i', { class: `fas ${RECOVERY_ICONS[entry.source] || 'fa-folder-open'}` })),
            createEl('div', { class: 'recovery-entry-text' },
                createEl('div', { class: 'recovery-entry-title' },
                    createEl('span', { class: 'recovery-label', text: entry.label }),
                    createEl('span', { text: entry.title })),
                createEl('div', { class: 'recovery-entry-meta' },
                    createEl('time', { text: recoveryWhen(entry.created_at),
                        datetime: entry.created_at ? new Date(entry.created_at * 1000).toISOString() : undefined }),
                    entry.folder ? createEl('span', {}, ' · uit ',
                        createEl('span', { class: 'path-label', text: recoveryShortDir(entry.folder), title: entry.folder })) : null)),
            button),
        status,
        fileBlock,
        entry.note && entry.undoable ? createEl('div', { class: 'recovery-note', text: entry.note }) : null);
}

function recoveryRender() {
    const list = document.getElementById('recovery-list');
    const summary = document.getElementById('recovery-summary');
    const more = document.getElementById('recovery-more');
    const entries = recoveryState.entries;
    more.hidden = recoveryState.nextOffset === null;
    if (!entries.length) {
        summary.textContent = '';
        list.replaceChildren(emptyState({
            icon: 'fa-clock-rotate-left',
            title: 'Nog niets om terug te zetten',
            text: 'Zodra de app iets verplaatst (Keurer, duplicaten opruimen, Verplaatsen in de viewer), staat het hier, met een knop om het terug te zetten.',
            action: { label: 'Naar de Keurer', onClick: () => switchMainView('keurer') },
        }));
        return;
    }
    const open = entries.filter(e => e.undoable).length;
    summary.textContent = `${recoveryState.total} actie${recoveryState.total === 1 ? '' : 's'}`
        + (entries.length < recoveryState.total ? ` (${entries.length} getoond)` : '')
        + ` · ${open} terug te zetten`;
    list.replaceChildren(...entries.map(recoveryEntryCard));
}

async function recoveryUndo(entry, button) {
    const n = entry.restorable;
    const unchecked = typeof entry.checked === 'number' && entry.checked < entry.file_count;
    const what = unchecked ? `De ${entry.file_count} bestanden van deze actie (voor zover ze nog terug kunnen)`
        : (n === 1 ? 'Dit bestand' : `${n} bestanden`);
    const question = `${what} terugzetten naar de oorspronkelijke plek?\n\n`
        + (entry.note || 'Er wordt nooit iets overschreven.');
    if (!confirm(question)) return;
    // De lijst wordt zo opnieuw opgebouwd; onthoud waar de focus stond om hem terug te zetten
    const list = document.getElementById('recovery-list');
    const hadFocus = list.contains(document.activeElement);
    const index = recoveryState.entries.findIndex(e => e.key === entry.key);
    button.disabled = true;
    button.textContent = 'Bezig…';
    try {
        const data = await fetchJSON(`${API_BASE}${entry.undo.url}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(entry.undo.body),
        });
        const restored = typeof data.restored === 'number' ? data.restored : 1;
        const skipped = (data.skipped || []).length + ((data.errors || []).length);
        let message = `${restored} bestand${restored === 1 ? '' : 'en'} teruggezet.`;
        if (skipped) message += ` ${skipped} kon${skipped === 1 ? '' : 'den'} niet terug; de reden staat in het Herstelcentrum.`;
        showToast('Teruggezet', message, skipped ? 'warning' : 'success');
        recoveryAfterUndo(entry);
    } catch (err) {
        toastFetchError('Terugzetten mislukt', err);
    } finally {
        await recoveryLoad();
        const active = document.activeElement;
        if (hadFocus || !active || active === document.body) recoveryRefocus(entry.key, index);
    }
}

// Na terugzetten de focus niet laten wegvallen (naar het begin van de pagina): dezelfde actie
// als die nog iets terug kan zetten, anders de volgende (of vorige), anders de paginatitel
function recoveryRefocus(key, index) {
    const cards = [...document.querySelectorAll('#recovery-list .recovery-entry')];
    const at = Math.max(0, index);
    const order = [cards.find(c => c.dataset.key === key), ...cards.slice(at), ...cards.slice(0, at).reverse()];
    for (const card of order) {
        const button = card && card.querySelector('.recovery-undo');
        if (button && !button.disabled) { button.focus(); return; }
    }
    const title = document.getElementById('recovery-title');
    if (title) title.focus();
}

// Andere schermen die dezelfde bestanden tonen, bijwerken
function recoveryAfterUndo(entry) {
    if (entry.source === 'keurer' && typeof keurerLoadHistory === 'function') keurerLoadHistory();
    if (entry.source === 'duplicates' && typeof loadDuplicateRuns === 'function') loadDuplicateRuns();
    if (typeof jobsLibraryRefresh === 'function') jobsLibraryRefresh();
    const folders = new Set((entry.files || []).flatMap(f => [f.from, f.to]).map(p => p.slice(0, p.lastIndexOf('/'))));
    if (typeof currentFolder !== 'undefined' && currentFolder && folders.has(currentFolder)) loadMedia(currentFolder);
}
