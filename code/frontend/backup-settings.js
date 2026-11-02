// DataVault Dashboard - Instellingen > Back-ups
// Note: API_BASE is defined in app.js
//
// De database (scores, vergelijkingen, Keurer-keuzes, herstelgegevens, instellingen, de index)
// staat op de T7. De server kopieert hem elke dag naar de schijf van deze Mac (backend/backup.py);
// hier zie je de laatste back-up, waar ze staan, maak je er zelf een en lees je hoe je er een
// terugzet. Laadt na settings.js.

let backupBusy = false;

function backupWhen(seconds) {
    if (!seconds) return '—';
    const when = new Date(seconds * 1000);
    const today = new Date();
    const yesterday = new Date(today.getTime() - 86400000);
    const time = when.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
    if (when.toDateString() === today.toDateString()) return `vandaag ${time}`;
    if (when.toDateString() === yesterday.toDateString()) return `gisteren ${time}`;
    return `${when.toLocaleDateString('nl-NL', { day: 'numeric', month: 'short', year: 'numeric' })} ${time}`;
}

function backupClock(seconds) {
    return new Date(seconds * 1000).toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
}

// Wanneer de volgende back-up komt. De app kijkt elk uur (en kort na het starten) of er een
// nodig is; is er nog geen, of is de laatste ouder dan een dag, dan komt hij bij die controle.
function backupNext(status) {
    if (!status.auto) return 'Automatische back-ups staan uit';
    if (status.running) return 'Wordt nu gemaakt';
    const due = status.next_due_at ? status.next_due_at * 1000 : 0;
    if (due <= Date.now()) {
        const check = status.next_check_at ? status.next_check_at * 1000 : 0;
        if (check > Date.now()) return `Bij de volgende controle, rond ${backupClock(status.next_check_at)}`;
        return `Bij de volgende controle (elke ${status.check_interval_minutes === 60 ? 'uur' : `${status.check_interval_minutes} minuten`})`;
    }
    const tomorrow = new Date(Date.now() + 86400000);
    const when = new Date(due);
    const time = when.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
    return when.toDateString() === tomorrow.toDateString() ? `morgen rond ${time}` : `rond ${backupWhen(status.next_due_at)}`;
}

function backupCommandBlock(command, label) {
    // Een optie (--config) blijft heel; lange paden mogen afbreken. De tekst zelf is precies de opdracht.
    const parts = [];
    command.split(' ').forEach((word, i) => {
        if (i) parts.push(' ');
        parts.push(word.startsWith('-') ? createEl('span', { class: 'backup-command-option', text: word }) : word);
    });
    const code = createEl('code', { class: 'backup-command-text' }, ...parts);
    const copy = createEl('button', {
        type: 'button', class: 'btn btn-ghost btn-sm btn-icon backup-copy', 'aria-label': `${label} kopiëren`, title: 'Kopiëren',
        onClick: async () => {
            try {
                await navigator.clipboard.writeText(command);
                showToast('Gekopieerd', 'Plak het in Terminal.', 'success');
            } catch (_) {
                showToast('Kopiëren lukte niet', 'Selecteer de opdracht en kopieer hem zelf.', 'warning');
            }
        },
    }, createEl('i', { class: 'fas fa-copy', 'aria-hidden': 'true' }));
    return createEl('div', { class: 'backup-command' }, code, copy);
}

function backupRestoreHelp(status) {
    const list = `${status.restore_command}`;
    const one = `${status.restore_command} <naam>`;
    return createEl('details', { class: 'backup-restore' },
        createEl('summary', null, createEl('i', { class: 'fas fa-clock-rotate-left', 'aria-hidden': 'true' }), ' Een back-up terugzetten',
            createEl('i', { class: 'fas fa-chevron-right backup-restore-chevron', 'aria-hidden': 'true' })),
        createEl('ol', { class: 'backup-steps' },
            createEl('li', { text: 'Stop DataVault (stop_datavault.sh, of sluit de app). Zolang de app draait, weigert het terugzetten.' }),
            createEl('li', null, 'Bekijk in Terminal welke back-ups er zijn:', backupCommandBlock(list, 'Opdracht om de back-ups te tonen')),
            createEl('li', null, 'Zet er een terug (het vraagt eerst om "ja"):', backupCommandBlock(one, 'Opdracht om terug te zetten')),
            createEl('li', { text: 'Start DataVault weer. Wat niet in de back-up zat (de caches) bouwt de app vanzelf opnieuw op.' })),
        createEl('p', { class: 'backup-note',
            text: 'Vóór het terugzetten bewaart het script de huidige database helemaal, als "datavault-voor-herstel-…" in dezelfde map. '
                + 'Er wordt nooit iets overschreven of weggegooid; spijt je ervan, dan zet je die kopie op dezelfde manier terug.' }));
}

function backupRenderStatus(box, status) {
    const last = status.last;
    const lastValue = last
        ? [createEl('span', { text: backupWhen(last.created_at) }),
           createEl('span', { class: 'tabular backup-size', text: ` · ${formatBytes(last.size)}` }),
           createEl('span', { class: `badge ${last.kind === 'handmatig' ? 'badge-info' : 'badge-success'} backup-kind`,
               text: last.kind === 'handmatig' ? 'Handmatig' : 'Automatisch' })]
        : [createEl('span', { class: 'backup-none', text: 'Nog geen back-up' })];
    const kept = status.count === 1 ? '1 back-up' : `${formatNumberNl(status.count)} back-ups`;
    const rule = `de laatste ${status.keep_daily} dagen, ${status.keep_weekly} weken en je ${status.keep_manual} nieuwste handmatige`;
    const rows = [
        settingsRow('Laatste back-up', ...lastValue),
        settingsRow('Volgende', backupNext(status)),
        settingsRow('Locatie', createEl('span', { class: 'path-label', text: status.folder, title: status.folder }),
            status.folder_chosen ? createEl('span', { class: 'badge backup-kind', text: 'Zelf gekozen' }) : null,
            createEl('button', {
                type: 'button', id: 'settings-backup-folder', class: 'btn btn-secondary btn-sm backup-folder-btn',
                onClick: () => backupFolderOpen(status),
            }, createEl('i', { class: 'fas fa-folder-open', 'aria-hidden': 'true' }), ' Andere map…')),
        status.count
            ? settingsRow('Bewaard', createEl('span', { text: `${kept}, samen ` }),
                createEl('span', { class: 'tabular', text: formatBytes(status.total_size) }),
                createEl('span', { class: 'backup-muted', text: ` · ${rule}` }))
            : settingsRow('Bewaard', createEl('span', { class: 'backup-none', text: 'Nog niets bewaard' }),
                createEl('span', { class: 'backup-muted', text: ` · straks ${rule}` })),
    ];
    if (status.folder_chosen && !status.folder_exists) {
        rows.push(createEl('div', { class: 'backup-error', role: 'alert' },
            createEl('i', { class: 'fas fa-plug', 'aria-hidden': 'true' }),
            createEl('span', { text: 'De gekozen back-upmap is nu niet bereikbaar. Sluit de schijf aan, of kies een andere map.' })));
    }
    if (status.folder_note) {
        rows.push(createEl('div', { class: 'backup-error' },
            createEl('i', { class: 'fas fa-circle-info', 'aria-hidden': 'true' }),
            createEl('span', { text: status.folder_note })));
    }
    if (status.last_error) {
        rows.push(createEl('div', { class: 'backup-error', role: 'alert' },
            createEl('i', { class: 'fas fa-triangle-exclamation', 'aria-hidden': 'true' }),
            createEl('span', { text: `Laatste poging mislukt (${backupWhen(status.last_error.at)}): ${status.last_error.message}` })));
    }
    const running = backupBusy || status.running;
    const button = createEl('button', {
        type: 'button', id: 'settings-backup-now', class: `btn btn-secondary${running ? ' is-loading' : ''}`, disabled: running,
        onClick: backupNow,
    'aria-busy': running ? 'true' : null }, createEl('i', { class: 'fas fa-shield-halved', 'aria-hidden': 'true' }), ' Nu back-uppen');
    box.replaceChildren(...rows,
        createEl('p', { class: 'backup-note',
            text: 'Elke back-up wordt eerst op fouten gecontroleerd en pas daarna bewaard. Niet erin: wat de app '
                + 'zelf opnieuw maakt (hashes, datums, de mappenlijst van de index, scanresultaten); zo blijft hij klein.' }),
        createEl('div', { class: 'settings-actions backup-actions' }, button,
            createEl('span', { id: 'settings-backup-step', class: 'backup-muted backup-step', 'aria-live': 'polite',
                text: running ? backupStepText(backupJob()) : '' })),
        backupRestoreHelp(status));
    if (backupRefocus) {
        backupRefocus = false;
        button.focus();
    }
}

// De lopende back-up-taak (uit het takenpaneel), en wat er naast de knop staat zolang hij loopt
function backupJob() {
    return (typeof jobDock !== 'undefined' ? jobDock.jobs : []).find(j => j.type === 'backup' && JOB_ACTIVE.has(j.status)) || null;
}

function backupStepText(job) {
    if (!job) return 'Bezig…';
    const step = (job.message || 'Bezig…').replace(/\.\.\.$/, '…');
    return job.total ? `Stap ${Math.min(job.processed + 1, job.total)} van ${job.total}: ${step.charAt(0).toLowerCase()}${step.slice(1)}` : step;
}

jobsOnChange((job) => {
    if (job.type !== 'backup' || !JOB_ACTIVE.has(job.status)) return;
    const step = document.getElementById('settings-backup-step');
    if (step) step.textContent = backupStepText(job);
});

// Na het opnieuw tekenen de focus terug op 'Nu back-uppen' (het stuk wordt pas getekend als
// de stand binnen is)
let backupRefocus = false;

// 'Nu back-uppen' start een achtergrondtaak (een grote database kopiëren, controleren en
// comprimeren duurt minuten): de stappen staan in het takenpaneel, je kunt hem stoppen en de
// uitkomst komt ook na een herlaad als melding (zie jobsOnFinished hieronder)
async function backupNow(e) {
    if (backupBusy) return;
    backupBusy = true;
    const button = e && e.currentTarget;
    if (button) {
        button.classList.add('is-loading');
        button.setAttribute('aria-busy', 'true');
    }
    try {
        await jobsStart('backup', {});
    } catch (err) {
        backupBusy = false;
        toastFetchError('Back-up mislukt', err);
        backupRedraw(button);
    }
}

// Het stuk opnieuw tekenen op dezelfde plek, zonder skelet ertussen: dan springt de pagina niet
async function backupRedraw(button) {
    const box = (button && button.closest('.backup-status')) || document.querySelector('.backup-status');
    const focused = !!button && document.activeElement === button;
    let status = null;
    try {
        status = await fetchJSON(`${API_BASE}/api/backup/status`);
    } catch (_) {
        status = null;
    }
    if (box && box.isConnected && status) {
        backupRefocus = focused;
        // De knop blijft op dezelfde plek in beeld (Chromium verschuift anders de pagina als de
        // inhoud vervangen wordt)
        const old = document.getElementById('settings-backup-now');
        const before = old ? old.getBoundingClientRect().top : null;
        backupRenderStatus(box, status);
        const now = document.getElementById('settings-backup-now');
        if (now && before !== null) window.scrollBy(0, now.getBoundingClientRect().top - before);
    } else {
        settingsRefreshSection('backups');
    }
}

// De uitkomst van een back-up (ook een die liep terwijl je op een ander scherm was)
jobsOnFinished((job) => {
    if (job.type !== 'backup') return;
    backupBusy = false;
    // De test-back-up na het kiezen van een map: het venster zegt hoe het ging (geen melding erbij)
    if (backupFolderTestFinished(job)) {
        backupRedraw(document.getElementById('settings-backup-now'));
        return;
    }
    if (job.status === 'done') {
        const size = job.summary && job.summary.size;
        showToast('Back-up gemaakt', `${size ? `${formatBytes(size)}, ` : ''}gecontroleerd en bewaard.`, 'success');
    } else if (job.status === 'failed') {
        showToast('Back-up mislukt', job.error || 'Onbekende fout', 'error');
    } else if (job.status === 'paused') {
        showToast('Back-up gepauzeerd', 'Hervat hem in het takenpaneel; hij begint dan opnieuw.', 'info');
    } else {
        showToast('Back-up gestopt', 'Er is niets bewaard; je eerdere back-ups blijven staan.', 'info');
    }
    backupRedraw(document.getElementById('settings-backup-now'));
});

// ==================== EEN ANDERE MAP KIEZEN ====================
//
// Het venster 'Map voor de back-ups': een mappenboom (folder-tree.js) die per map pas laadt als
// je hem opent (/api/backup/folders), met bij elke gekozen map meteen het oordeel van de server
// (paths.check_backup_folder: bestaat, schrijfbaar, niet in het archief). 'Map maken' maakt een
// nieuwe map in de gekozen map. 'Gebruiken en testen' bewaart de keuze en maakt meteen een
// back-up daar; het venster toont de stappen en de uitkomst.

const BF_INDENT = 20;
const BF_BASE = 8;
const BF_LOADING = '\u2026laden';

const backupFolder = {
    status: null, nodes: new Map(), loaded: new Map(), expanded: new Set(), selected: null,
    check: null, seq: 0, phase: 'choose', testJob: null, busy: false,
};

function bfTree() {
    return document.getElementById('backup-folder-tree');
}

function bfRow(path) {
    return bfTree().querySelector(`.folder-item[data-path="${CSS.escape(path)}"]`);
}

function bfNode(entry) {
    backupFolder.nodes.set(entry.path, entry);
    // Een map met (misschien) submappen krijgt een pijltje; de submappen komen bij openen
    const children = entry.has_children === false ? [] : [{ name: 'Laden…', path: `${entry.path}/${BF_LOADING}`, children: [] }];
    return { name: entry.name, path: entry.path, children };
}

function bfConfig(depth) {
    return {
        indent: BF_INDENT, indentBase: BF_BASE + depth * BF_INDENT,
        expandedPaths: backupFolder.expanded, selectedPath: backupFolder.selected,
        onSelect: (path, row) => bfSelect(path, row),
        onToggle: (path, open) => { if (open) bfLoad(path); },
    };
}

// Wat de boom zelf niet weet: welke mappen niet kunnen (gedimd, met de reden), de iconen van
// de beginplekken en de regels 'Laden…'
function bfDecorate(container) {
    container.querySelectorAll('.folder-item').forEach(row => {
        if (row.dataset.path.endsWith(BF_LOADING)) {
            row.classList.add('is-placeholder');
            return;
        }
        const entry = backupFolder.nodes.get(row.dataset.path);
        if (!entry) return;
        if (!entry.allowed) {
            row.classList.add('is-unavailable');
            row.title = entry.reason;
        }
        if (entry.icon) {
            row.querySelector('.folder-icon').replaceChildren(createEl('i', { class: `fas ${entry.icon}`, 'aria-hidden': 'true' }));
        }
    });
}

// Alleen de boom schuift (scrollIntoView zou ook de pagina achter het venster verschuiven)
function bfScrollTo(row) {
    const tree = bfTree();
    const offset = row.getBoundingClientRect().top - tree.getBoundingClientRect().top;
    tree.scrollTop += offset - (tree.clientHeight - row.offsetHeight) / 2;
}

function bfDepth(row) {
    return Math.max(0, Math.round((parseFloat(row.style.paddingLeft) - BF_BASE) / BF_INDENT));
}

// De submappen van één map, één keer per keer dat het venster open is
function bfLoad(path) {
    if (!path || path.endsWith(BF_LOADING)) return Promise.resolve();
    // Zonder submappen (of het archief zelf, waar niets te kiezen valt): niets op te halen
    const entry = backupFolder.nodes.get(path);
    if (entry && entry.has_children === false) return Promise.resolve();
    if (backupFolder.loaded.has(path)) return backupFolder.loaded.get(path);
    const done = (async () => {
        let data;
        try {
            data = await fetchJSON(`${API_BASE}/api/backup/folders?path=${encodeURIComponent(path)}`);
        } catch (err) {
            backupFolder.loaded.delete(path);
            const row = bfRow(path);
            const kids = row && row.parentElement.querySelector(':scope > .folder-children');
            if (kids) kids.replaceChildren(createEl('div', { class: 'picker-message is-error', text: err.message }));
            return;
        }
        const row = bfRow(path);
        if (!row) return;
        const kids = row.parentElement.querySelector(':scope > .folder-children');
        if (!data.folders.length) {
            // Geen submappen: geen pijltje meer (en ← en → doen dan niets)
            const chevron = row.querySelector('.folder-chevron');
            if (chevron) {
                chevron.classList.add('no-children');
                chevron.onclick = null;
            }
            if (kids) kids.replaceChildren();
            return;
        }
        if (!kids) return;
        renderFolderTree(kids, data.folders.map(bfNode), bfConfig(bfDepth(row) + 1));
        bfDecorate(kids);
    })();
    backupFolder.loaded.set(path, done);
    return done;
}

// Een map open zetten (voor het tonen van de huidige back-upmap bij het openen)
function bfExpand(path) {
    const row = bfRow(path);
    if (!row) return;
    backupFolder.expanded.add(path);
    const kids = row.parentElement.querySelector(':scope > .folder-children');
    if (kids) kids.classList.remove('collapsed');
    const chevron = row.querySelector('.folder-chevron');
    if (chevron) chevron.classList.add('expanded');
}

function bfSelect(path, row) {
    if (!path || path.endsWith(BF_LOADING) || backupFolder.phase !== 'choose') return;
    bfTree().querySelectorAll('.folder-item.selected').forEach(el => { if (el !== row) el.classList.remove('selected'); });
    if (row) row.classList.add('selected');
    backupFolder.selected = path;
    document.getElementById('backup-folder-create').disabled = false;
    bfLoad(path);
    bfCheck(path);
}

// Het oordeel van de server over de gekozen map, onder de boom
async function bfCheck(path) {
    const seq = ++backupFolder.seq;
    backupFolder.check = null;
    bfRenderCheck({ state: 'busy', text: 'Even kijken of deze map kan…' });
    let result;
    try {
        result = await fetchJSON(`${API_BASE}/api/backup/folder/check`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }),
        });
    } catch (err) {
        result = { ok: false, error: err.message };
    }
    if (seq !== backupFolder.seq) return;
    backupFolder.check = result;
    if (!result.ok) bfRenderCheck({ state: 'error', title: 'Deze map kan niet', text: result.error });
    else if (result.current) bfRenderCheck({ state: 'ok', title: 'Dit is al de back-upmap', path: result.path, warning: result.warning });
    else bfRenderCheck({ state: 'ok', title: 'Deze map kan', path: result.path, warning: result.warning });
}

const BF_ICONS = {
    busy: 'fa-circle-notch fa-spin', ok: 'fa-circle-check', warn: 'fa-triangle-exclamation',
    error: 'fa-circle-exclamation', done: 'fa-circle-check', idle: 'fa-circle-info',
};

function bfRenderCheck({ state, title = null, text = null, path = null, warning = null }) {
    const box = document.getElementById('backup-folder-check');
    // Kan, met een kanttekening (dezelfde schijf als de database): oranje in plaats van groen
    if (state === 'ok' && warning) state = 'warn';
    box.className = `backup-folder-check is-${state}`;
    box.replaceChildren(
        createEl('i', { class: `fas ${BF_ICONS[state] || BF_ICONS.idle} backup-folder-check-icon`, 'aria-hidden': 'true' }),
        createEl('div', { class: 'backup-folder-check-text' },
            title ? createEl('strong', { text: title }) : null,
            path ? createEl('span', { class: 'path-label backup-folder-check-path', text: path, title: path }) : null,
            text ? createEl('span', { text }) : null,
            warning ? createEl('span', { class: 'backup-folder-check-warning', text: warning }) : null));
    const use = document.getElementById('backup-folder-use');
    if (backupFolder.phase === 'choose') {
        const check = backupFolder.check;
        use.disabled = !(check && check.ok && !check.current);
    }
}

async function backupFolderOpen(status) {
    Object.assign(backupFolder, {
        status, nodes: new Map(), loaded: new Map(), expanded: new Set(), selected: null,
        check: null, seq: 0, phase: 'choose', testJob: null, busy: false,
    });
    const modal = document.getElementById('modal-backup-folder');
    const use = document.getElementById('backup-folder-use');
    use.disabled = true;
    use.classList.remove('is-loading');
    use.textContent = 'Gebruiken en testen';
    document.getElementById('backup-folder-create').disabled = true;
    document.getElementById('backup-folder-cancel').hidden = false;
    document.getElementById('backup-folder-default').hidden = !status.folder_chosen;
    document.querySelector('#modal-backup-folder .backup-folder-new').hidden = false;
    bfRenderCheck({ state: 'idle', text: 'Kies een map in de lijst, of maak er een in de gekozen map.' });
    const tree = bfTree();
    tree.replaceChildren(skeletonRows(3));
    modal.classList.add('active');
    activateFolderTreeModal({
        modalId: 'modal-backup-folder',
        containerId: 'backup-folder-tree',
        onSelect: (item) => item.click(),
        onHighlight: (item) => bfLoad(item.dataset.path),
        onClose: backupFolderClose,
    });
    let roots;
    try {
        roots = (await fetchJSON(`${API_BASE}/api/backup/folders`)).folders;
    } catch (err) {
        tree.replaceChildren(createEl('div', { class: 'picker-message is-error', text: `Mappen laden mislukt: ${err.message}` }));
        return;
    }
    renderFolderTree(tree, roots.map(bfNode), bfConfig(0));
    bfDecorate(tree);
    // De huidige back-upmap staat open en gekozen, als hij onder een beginplek ligt
    const current = status.folder;
    const root = roots.find(r => current === r.path || current.startsWith(r.path.replace(/\/$/, '') + '/'));
    if (!root) return;
    const parts = current.slice(root.path.length).split('/').filter(Boolean);
    let at = root.path;
    for (const part of parts) {
        await bfLoad(at);
        bfExpand(at);
        at = `${at.replace(/\/$/, '')}/${part}`;
        if (!bfRow(at)) return;
    }
    const row = bfRow(at);
    if (row && backupFolder.phase === 'choose' && !backupFolder.selected) {
        bfSelect(at, row);
        bfScrollTo(row);
        if (modal._treeNav) modal._treeNav.highlightItem(row, false);
    }
}

function backupFolderClose() {
    document.getElementById('modal-backup-folder').classList.remove('active');
    const modal = document.getElementById('modal-backup-folder');
    if (modal._treeNav) modal._treeNav.resetIndex();
    // Een test die nog loopt, gaat gewoon door; de uitkomst komt dan als melding
    backupFolder.phase = backupFolder.phase === 'testing' ? 'closed-testing' : 'closed';
}

// 'Map maken': een nieuwe map in de gekozen map, daarna staat hij gekozen in de boom
async function backupFolderCreate() {
    const parent = backupFolder.selected;
    const input = document.getElementById('backup-folder-new-name');
    const name = input.value.trim();
    if (!parent || backupFolder.busy) return;
    const button = document.getElementById('backup-folder-create');
    backupFolder.busy = true;
    button.classList.add('is-loading');
    try {
        const made = await fetchJSON(`${API_BASE}/api/backup/folder/create`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: parent, name }),
        });
        backupFolder.loaded.delete(parent);
        // Hij heeft nu (minstens) één submap: opnieuw ophalen
        const parentEntry = backupFolder.nodes.get(parent);
        if (parentEntry) parentEntry.has_children = true;
        const parentRow = bfRow(parent);
        // Had de map nog geen pijltje (geen submappen), dan krijgt hij er nu een
        if (parentRow) {
            const kids = parentRow.parentElement.querySelector(':scope > .folder-children')
                || parentRow.parentElement.appendChild(createEl('div', { class: 'folder-children' }));
            const chevron = parentRow.querySelector('.folder-chevron');
            if (chevron) {
                chevron.classList.remove('no-children');
                chevron.style.visibility = '';
                chevron.onclick = (e) => { e.stopPropagation(); bfExpand(parent); };
            }
            kids.replaceChildren();
        }
        await bfLoad(parent);
        bfExpand(parent);
        const row = bfRow(made.path);
        if (row) {
            bfSelect(made.path, row);
            bfScrollTo(row);
        }
    } catch (err) {
        bfRenderCheck({ state: 'error', title: 'Map maken lukte niet', text: err.message });
    } finally {
        backupFolder.busy = false;
        button.classList.remove('is-loading');
    }
}

// 'Gebruiken en testen': de keuze bewaren en meteen een back-up daar maken
async function backupFolderUse() {
    const check = backupFolder.check;
    if (!check || !check.ok || check.current || backupFolder.phase !== 'choose' || backupFolder.busy) return;
    const use = document.getElementById('backup-folder-use');
    backupFolder.busy = true;
    use.classList.add('is-loading');
    let saved;
    try {
        saved = await fetchJSON(`${API_BASE}/api/backup/folder`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: check.path }),
        });
    } catch (err) {
        backupFolder.busy = false;
        use.classList.remove('is-loading');
        bfRenderCheck({ state: 'error', title: 'Niet bewaard', text: err.message });
        return;
    }
    backupFolder.phase = 'testing';
    backupFolder.status = saved.status;
    document.querySelector('#modal-backup-folder .backup-folder-new').hidden = true;
    document.getElementById('backup-folder-default').hidden = true;
    bfRenderCheck({ state: 'busy', title: 'Test-back-up maken', path: saved.status.folder, text: 'Bezig…' });
    try {
        const job = await jobsStart('backup', {});
        backupFolder.testJob = job.id;
    } catch (err) {
        backupFolder.phase = 'choose';
        bfRenderCheck({ state: 'error', title: 'De map is bewaard, maar de test-back-up startte niet', text: err.message });
    } finally {
        backupFolder.busy = false;
        use.classList.remove('is-loading');
        backupRedraw(document.getElementById('settings-backup-now'));
    }
}

// De stappen van de test-back-up in het venster
jobsOnChange((job) => {
    if (job.type !== 'backup' || job.id !== backupFolder.testJob || backupFolder.phase !== 'testing') return;
    const text = document.querySelector('#backup-folder-check .backup-folder-check-text > span:last-child');
    if (text) text.textContent = backupStepText(job);
});

// true: dit was de test-back-up en het venster (of een melding, als het al dicht is) zegt hoe het ging
function backupFolderTestFinished(job) {
    if (job.id !== backupFolder.testJob) return false;
    backupFolder.testJob = null;
    const folder = backupFolder.status ? backupFolder.status.folder : '';
    const size = job.summary && job.summary.size;
    const ok = job.status === 'done';
    const message = ok ? `${size ? `${formatBytes(size)}, ` : ''}gecontroleerd en bewaard. Vanaf nu gaan de back-ups hierheen.`
        : (job.error || 'De back-up is gestopt; er is niets bewaard.');
    if (backupFolder.phase !== 'testing') {
        showToast(ok ? 'Test-back-up gelukt' : 'Test-back-up mislukt', `${folder}: ${message}`, ok ? 'success' : 'error');
        return true;
    }
    backupFolder.phase = ok ? 'done' : 'choose';
    bfRenderCheck(ok ? { state: 'done', title: 'Test-back-up gelukt', path: folder, text: message }
        : { state: 'error', title: 'Test-back-up mislukt', path: folder, text: `${message} Kies een andere map, of probeer het nog eens.` });
    const use = document.getElementById('backup-folder-use');
    if (ok) {
        use.textContent = 'Klaar';
        use.disabled = false;
        use.onclick = () => { use.onclick = null; backupFolderClose(); };
        document.getElementById('backup-folder-cancel').hidden = true;
        use.focus();
    } else {
        document.querySelector('#modal-backup-folder .backup-folder-new').hidden = false;
        use.disabled = true;
    }
    return true;
}

async function backupFolderUseDefault() {
    if (backupFolder.busy) return;
    backupFolder.busy = true;
    try {
        const saved = await fetchJSON(`${API_BASE}/api/backup/folder`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: null }),
        });
        backupFolderClose();
        showToast('Standaardmap', `De back-ups gaan weer naar ${saved.status.folder}.`, 'success');
        backupRedraw(document.getElementById('settings-backup-now'));
    } catch (err) {
        bfRenderCheck({ state: 'error', title: 'Niet bewaard', text: err.message });
    } finally {
        backupFolder.busy = false;
    }
}

// In het naamveld zijn letters en pijltjes voor het veld, niet voor de boom; Enter maakt de map
document.addEventListener('DOMContentLoaded', () => {
    const input = document.getElementById('backup-folder-new-name');
    if (input) {
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' || e.key === 'Tab') return;
            e.stopPropagation();
            if (e.key === 'Enter') { e.preventDefault(); backupFolderCreate(); }
        });
    }
    if (typeof registerOverlayCloser === 'function') registerOverlayCloser('modal-backup-folder', backupFolderClose);
});

registerSettingsSection({
    id: 'backups',
    title: 'Back-ups',
    icon: 'fa-shield-halved',
    order: 80,
    description: 'Je scores, vergelijkingen, Keurer-keuzes en herstelgegevens staan in de database op de T7. '
        + 'De app kopieert die elke dag naar de schijf van deze Mac, los van de T7.',
    render(body) {
        const box = createEl('div', { class: 'backup-status' }, skeletonRows(2));
        body.append(box);
        fetchJSON(`${API_BASE}/api/backup/status`)
            .then(status => backupRenderStatus(box, status))
            .catch(err => box.replaceChildren(errorState('Back-ups niet op te vragen', err, () => settingsRefreshSection('backups'))));
    },
});
