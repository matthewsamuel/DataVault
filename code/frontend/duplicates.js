// DataVault Dashboard - Duplicaten
// Note: API_BASE is defined in app.js
//
// De scan (POST /api/duplicates/scan_folder) geeft groepen byte voor byte identieke
// bestanden terug, met per kopie de gegevens die de regels nodig hebben. Welke kopie
// blijft, rekent deze pagina zelf uit (keepSortKey, dezelfde sleutels als
// scanner.keeper_sort_key op de server), zodat een andere regel meteen zichtbaar is.
// Per groep kun je ook zelf een kopie aanklikken. Opruimen stuurt per groep
// {keep, move[]} naar /api/duplicates/move; de server vergelijkt vlak voor elke
// verplaatsing byte voor byte met de kopie die blijft. Eén klik op opruimen is één
// actie (run) onder Herstellen, ook als die in stukjes verstuurd wordt.

const DUP_RULE_LABELS = {
    oldest: 'Oudste behouden',
    newest: 'Nieuwste behouden',
    shortest: 'Kortste pad',
    folder: 'Voorkeursmap',
};
const DUP_RULE_HELP = {
    oldest: 'de kopie met de oudste datum blijft; bij gelijke datum het kortste pad, dan alfabetisch',
    newest: 'de kopie met de nieuwste datum blijft; bij gelijke datum het kortste pad, dan alfabetisch',
    shortest: 'de kopie met het kortste pad blijft; daarna de oudste, dan alfabetisch',
    folder: 'een kopie in de voorkeursmap blijft; anders de oudste',
};
const DUP_TYPE_LABELS = { photo: 'Foto', video: 'Video', audio: 'Audio' };
const DUP_PAGE_SIZE = 30;      // groepen per keer in beeld
const DUP_CHUNK_FILES = 100;   // bestanden per verzoek (server: max 200)

const dupState = {
    folder: null,          // gescande map
    groups: [],
    rule: 'oldest',
    prefer: null,          // voorkeursmap (absoluut pad) voor rule 'folder'
    overrides: new Map(),  // groep-id -> pad van de zelf gekozen kopie
    failures: new Map(),   // pad -> reden waarom het niet verplaatst is
    filter: 'all',
    visible: [],           // gefilterde groepen, in volgorde
    rendered: 0,
    cards: new Map(),      // groep-id -> kaart-element
    cleanup: null,         // lopende of klaargezette opruimactie
};

let progressPollInterval = null;
let selectedFolder = null;
let selectedFolderForModal = null;
let currentComparisonGroup = null;
let dupObserver = null;

try {
    const saved = localStorage.getItem('dupKeepRule');
    if (saved && DUP_RULE_LABELS[saved] && saved !== 'folder') dupState.rule = saved;
} catch (e) { /* geen opslag: standaardregel */ }

// ==================== FOLDER SELECTION ====================

function openFolderSelector() {
    const modal = document.getElementById('modal-folder-selector');
    const tree = document.getElementById('folder-selector-tree');

    renderFolderTree(tree, folderTree, {
        onSelect: (path) => {
            selectedFolderForModal = path;
            document.getElementById('btn-confirm-folder').disabled = false;
        },
    });

    modal.classList.add('active');

    if (window._dupNav) window._dupNav.nav.resetIndex();
    window._dupNav = activateFolderTreeModal({
        modalId: 'modal-folder-selector',
        containerId: 'folder-selector-tree',
        onSelect: (item) => {
            const path = item.dataset.path;
            if (path) item.click();
        },
        onConfirm: () => { if (selectedFolderForModal) confirmFolderSelection(); },
        onClose: closeFolderSelector,
    });
}

function closeFolderSelector() {
    const modal = document.getElementById('modal-folder-selector');
    modal.classList.remove('active');
    if (window._dupNav) window._dupNav.nav.resetIndex();
    selectedFolderForModal = null;
}

function confirmFolderSelection() {
    if (!selectedFolderForModal) return;

    selectedFolder = selectedFolderForModal;
    document.getElementById('selected-folder-path').textContent = selectedFolder;
    document.getElementById('btn-scan-folder').disabled = false;

    closeFolderSelector();
    showToast('Map gekozen', 'Klaar om te scannen op duplicaten', 'success');
}

// ==================== SCAN ====================

async function scanSelectedFolder() {
    if (!selectedFolder) {
        showToast('Geen map', 'Kies eerst een map', 'info');
        return;
    }

    const btnScan = document.getElementById('btn-scan-folder');
    const btnCancel = document.getElementById('btn-cancel-scan');
    const progressContainer = document.getElementById('scan-progress-container');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');

    btnScan.disabled = true;
    btnScan.style.display = 'none';
    btnCancel.style.display = 'inline-flex';

    progressContainer.style.display = 'block';
    progressBar.style.width = '0%';
    progressBar.textContent = '0%';
    progressText.textContent = 'Scan starten...';

    progressPollInterval = setInterval(async () => {
        try {
            const resp = await fetch(`${API_BASE}/api/raw_duplicates/progress`);
            if (!resp.ok) return;
            const progress = await resp.json();
            const percent = progress.progress_percent || 0;
            progressBar.style.width = percent + '%';
            progressBar.textContent = percent + '%';
            progressText.textContent = `${progress.phase} (${progress.processed_files}/${progress.total_files} bestanden)`;
        } catch (e) {
            console.error('Error polling progress:', e);
        }
    }, 500);

    try {
        const params = new URLSearchParams({ folder_path: selectedFolder });
        const response = await fetch(`${API_BASE}/api/duplicates/scan_folder?${params}`, { method: 'POST' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Scan mislukt');

        if (data.cancelled) {
            showToast('Scan gestopt', 'De scan is geannuleerd', 'info');
            return;
        }
        showScanResult(data);
        const found = countMoves(dupState.groups);
        showToast(data.complete ? 'Scan klaar' : 'Scan onvolledig',
            `${dupState.groups.length} groepen, ${found} te verplaatsen` +
            (data.complete ? '' : ` (${data.errors} leesfout(en))`),
            data.complete ? 'success' : 'warning');
    } catch (error) {
        console.error('Error scanning folder:', error);
        showToast('Scan mislukt', error.message, 'error');
    } finally {
        clearInterval(progressPollInterval);
        progressPollInterval = null;
        progressContainer.style.display = 'none';
        btnScan.disabled = false;
        btnScan.style.display = 'inline-flex';
        btnCancel.style.display = 'none';
    }
}

async function cancelScan() {
    try {
        const response = await fetch(`${API_BASE}/api/raw_duplicates/cancel`, { method: 'POST' });
        if (response.ok) showToast('Stoppen', 'De scan wordt gestopt...', 'info');
    } catch (error) {
        console.error('Error cancelling scan:', error);
    }
}

function showScanResult(data) {
    dupState.folder = data.folder;
    dupState.groups = data.groups || [];
    dupState.overrides = new Map();
    dupState.failures = new Map();
    if (dupState.rule === 'folder') dupState.rule = 'oldest';
    dupState.prefer = null;

    document.getElementById('status-scanned').textContent = data.total_scanned;
    document.getElementById('status-timestamp').textContent = new Date().toLocaleTimeString('nl-NL');
    document.getElementById('status-panel').style.display = 'block';

    const warning = document.getElementById('dup-scan-warning');
    if (data.complete) {
        warning.hidden = true;
        warning.replaceChildren();
    } else {
        // Nooit 'klaar' zeggen terwijl er bestanden gemist zijn
        warning.replaceChildren(
            createEl('strong', { text: `Let op: ${data.errors} map(pen) of bestand(en) konden niet gelezen worden. ` }),
            'De resultaten zijn onvolledig; controleer de rechten en of de schijf nog aangesloten is.',
            createEl('ul', {}, (data.error_samples || []).map(e => createEl('li', { text: `${e.path}: ${e.error}` }))));
        warning.hidden = false;
    }

    document.getElementById('dup-rule').value = dupState.rule;
    fillPreferFolderOptions();
    document.getElementById('filter-bar').style.display = dupState.groups.length ? 'flex' : 'none';
    document.getElementById('dup-legend').style.display = dupState.groups.length ? 'block' : 'none';
    filterDuplicates('all');
}

// ==================== WHICH COPY STAYS ====================

// Zelfde sleutels als scanner.keeper_sort_key; de kleinste sleutel blijft
function keepSortKey(copy, rule, prefer) {
    const hidden = copy.hidden ? 1 : 0;
    if (rule === 'newest') return [hidden, -copy.capture_ts, -copy.file_ts, copy.path_len, copy.name_rank];
    if (rule === 'shortest') return [hidden, copy.path_len, copy.capture_ts, copy.file_ts, copy.name_rank];
    if (rule === 'folder') {
        const outside = prefer && (copy.path === prefer || copy.path.startsWith(prefer + '/')) ? 0 : 1;
        return [hidden, outside, copy.capture_ts, copy.file_ts, copy.path_len, copy.name_rank];
    }
    return [hidden, copy.capture_ts, copy.file_ts, copy.path_len, copy.name_rank];
}

function compareKeys(a, b) {
    for (let i = 0; i < a.length; i++) {
        if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
    }
    return 0;
}

function ruleKeeper(group) {
    let best = 0;
    let bestKey = keepSortKey(group.copies[0], dupState.rule, dupState.prefer);
    for (let i = 1; i < group.copies.length; i++) {
        const key = keepSortKey(group.copies[i], dupState.rule, dupState.prefer);
        if (compareKeys(key, bestKey) < 0) { best = i; bestKey = key; }
    }
    return best;
}

function keeperIndex(group) {
    const chosen = dupState.overrides.get(group.id);
    if (chosen) {
        const i = group.copies.findIndex(c => c.path === chosen);
        if (i >= 0) return i;
    }
    return ruleKeeper(group);
}

function countMoves(groups) {
    return groups.reduce((n, g) => n + g.copies.length - 1, 0);
}

function setKeepRule(rule) {
    if (!DUP_RULE_LABELS[rule]) return;
    dupState.rule = rule;
    const preferSelect = document.getElementById('dup-prefer-folder');
    preferSelect.hidden = rule !== 'folder';
    if (rule === 'folder' && !dupState.prefer && preferSelect.options.length) {
        dupState.prefer = preferSelect.value;
    }
    if (rule !== 'folder') {
        try { localStorage.setItem('dupKeepRule', rule); } catch (e) { /* geen opslag */ }
    }
    refreshAllGroups();
}

function setPreferFolder(folder) {
    dupState.prefer = folder || null;
    refreshAllGroups();
}

// Elke map met een kopie plus de bovenliggende mappen tot de gescande map:
// de voorkeur geldt voor de hele submap, dus 'RAW/2019' dekt ook 'RAW/2019/zomer'
function preferFolderChoices() {
    const base = dupState.folder || '';
    const folders = new Set();
    dupState.groups.forEach(g => g.copies.forEach(c => {
        folders.add(c.folder);
        let folder = c.folder;
        while (folder.startsWith(base + '/')) {
            folder = folder.slice(0, folder.lastIndexOf('/'));
            if (folder === base) break;
            folders.add(folder);
        }
    }));
    // De actieve voorkeur blijft zichtbaar, ook als er geen kopie meer in staat
    if (dupState.prefer) folders.add(dupState.prefer);
    // Per mapniveau sorteren, zodat een submap direct onder haar bovenliggende map staat
    return [...folders].sort((a, b) => compareKeys(a.split('/'), b.split('/')) || a.length - b.length);
}

function fillPreferFolderOptions() {
    const select = document.getElementById('dup-prefer-folder');
    const folders = preferFolderChoices();
    select.replaceChildren(...folders.map(f => createEl('option', { value: f, text: relativeToScan(f) || '(gescande map zelf)' })));
    // replaceChildren zet de keuze terug op de eerste optie; toon wat echt geldt
    if (dupState.prefer) select.value = dupState.prefer;
    select.hidden = dupState.rule !== 'folder';
}

function relativeToScan(path) {
    const base = dupState.folder || '';
    if (path === base) return '';
    return path.startsWith(base + '/') ? path.slice(base.length + 1) : path;
}

function chooseCopy(group, index) {
    if (index === ruleKeeper(group)) dupState.overrides.delete(group.id);
    else dupState.overrides.set(group.id, group.copies[index].path);
    refreshAllGroups();
}

function resetOverrides() {
    dupState.overrides.clear();
    refreshAllGroups();
}

// ==================== FILTER AND PROGRESSIVE RENDERING ====================

function filterDuplicates(filter) {
    dupState.filter = filter;
    document.querySelectorAll('#dup-filters .filter-chip').forEach(chip => {
        chip.classList.toggle('active', chip.dataset.filter === filter);
    });
    dupState.visible = filter === 'all' ? dupState.groups : dupState.groups.filter(g => g.type === filter);
    renderDuplicates();
}

function renderDuplicates() {
    const grid = document.getElementById('duplicates-grid');
    grid.replaceChildren();
    dupState.cards = new Map();
    dupState.rendered = 0;

    if (!dupState.groups.length && dupState.folder) {
        grid.appendChild(createEl('p', { class: 'text-center', text: 'Geen duplicaten gevonden in deze map.' }));
    } else if (!dupState.visible.length && dupState.folder) {
        grid.appendChild(createEl('p', { class: 'text-center', text: 'Geen duplicaten van dit type.' }));
    }
    renderMoreGroups();
    observeSentinel();
    updateSummary();
}

// Alleen wat bijna in beeld is wordt gebouwd: duizenden groepen geven geen duizenden miniaturen tegelijk
function renderMoreGroups() {
    const grid = document.getElementById('duplicates-grid');
    const end = Math.min(dupState.rendered + DUP_PAGE_SIZE, dupState.visible.length);
    for (let i = dupState.rendered; i < end; i++) {
        const group = dupState.visible[i];
        const card = createGroupCard(group);
        dupState.cards.set(group.id, card);
        grid.appendChild(card);
    }
    dupState.rendered = end;
}

function observeSentinel() {
    const sentinel = document.getElementById('dup-sentinel');
    if (dupObserver) dupObserver.disconnect();
    if (!('IntersectionObserver' in window)) {
        while (dupState.rendered < dupState.visible.length) renderMoreGroups();
        return;
    }
    dupObserver = new IntersectionObserver(entries => {
        if (entries.some(e => e.isIntersecting) && dupState.rendered < dupState.visible.length) {
            renderMoreGroups();
            // Blijft de sentinel in beeld (groot scherm), dan meteen nog een stuk
            dupObserver.unobserve(sentinel);
            dupObserver.observe(sentinel);
        }
    }, { root: document.getElementById('duplicates-container'), rootMargin: '600px 0px' });
    dupObserver.observe(sentinel);
}

function refreshAllGroups() {
    dupState.cards.forEach((card, id) => {
        const group = dupState.groups.find(g => g.id === id);
        if (group) updateGroupCard(card, group);
    });
    updateSummary();
}

function updateSummary() {
    const groups = dupState.visible;
    const moves = countMoves(groups);
    const bytes = groups.reduce((n, g) => n + g.size * (g.copies.length - 1), 0);
    document.getElementById('status-groups').textContent = dupState.groups.length;
    document.getElementById('status-found').textContent = countMoves(dupState.groups);
    document.getElementById('status-bytes').textContent = formatBytes(
        dupState.groups.reduce((n, g) => n + g.size * (g.copies.length - 1), 0));

    const removeAll = document.getElementById('btn-remove-all');
    removeAll.textContent = moves ? `Alles opruimen (${moves})` : 'Alles opruimen';
    removeAll.disabled = moves === 0;

    const counts = { all: dupState.groups.length, photo: 0, video: 0, audio: 0 };
    dupState.groups.forEach(g => { counts[g.type] = (counts[g.type] || 0) + 1; });
    document.querySelectorAll('#dup-filters .filter-chip').forEach(chip => {
        const label = { all: 'Alles', photo: "Foto's", video: "Video's", audio: 'Audio' }[chip.dataset.filter];
        chip.textContent = `${label} (${counts[chip.dataset.filter] || 0})`;
    });

    const manual = [...dupState.overrides.keys()].filter(id => dupState.groups.some(g => g.id === id)).length;
    const info = document.getElementById('dup-override-info');
    if (manual) {
        info.replaceChildren(
            `${manual} groep${manual === 1 ? '' : 'en'} zelf gekozen · `,
            createEl('button', { class: 'link-button', onClick: resetOverrides, text: 'terug naar de regel' }));
        info.hidden = false;
    } else {
        info.hidden = true;
    }

    const ruleText = dupState.rule === 'folder' && dupState.prefer
        ? `${DUP_RULE_LABELS.folder} "${relativeToScan(dupState.prefer) || dupState.prefer}"`
        : DUP_RULE_LABELS[dupState.rule];
    document.getElementById('dup-legend').replaceChildren(
        createEl('span', { class: 'dup-legend-keep', text: 'Blijft staan' }), ' ',
        createEl('span', { class: 'dup-legend-move', text: 'Gaat naar RAW_DUPLICATES' }),
        ` · Regel: ${ruleText} (${DUP_RULE_HELP[dupState.rule]}). Klik op een kopie om die te bewaren.` +
        ` Verborgen kopieën gaan altijd als laatste.` +
        (bytes ? ` Zichtbaar: ${moves} bestanden, ${formatBytes(bytes)}.` : ''));
}

// ==================== GROUP CARDS ====================

function formatDupDate(ts) {
    if (!ts) return '?';
    return new Date(ts * 1000).toLocaleDateString('nl-NL', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function fileDateLabel(copy) {
    return `${copy.file_ts_source === 'birthtime' ? 'aangemaakt' : 'gewijzigd'} ${formatDupDate(copy.file_ts)}`;
}

function createGroupCard(group) {
    const first = group.copies[0];
    const thumb = createEl('img', {
        class: 'dup-group-thumb',
        alt: first.name,
        loading: 'lazy',
        decoding: 'async',
    });
    thumb.addEventListener('error', () => thumb.classList.add('dup-thumb-missing'), { once: true });
    // Alle kopieën zijn byte voor byte gelijk: één miniatuur per groep is genoeg
    thumb.src = group.type === 'audio' ? 'audio_thumb.png' : vaultFileUrl('thumb', first.path);

    const exif = first.capture_source === 'exif' ? ` · opname ${formatDupDate(first.capture_ts)}` : '';
    const card = createEl('div', { class: 'dup-group', dataset: { id: String(group.id) } },
        createEl('button', { class: 'dup-group-thumb-wrap', title: 'Vergelijk', onClick: () => openComparisonModal(group) }, thumb),
        createEl('div', { class: 'dup-group-main' },
            createEl('div', { class: 'dup-group-header' },
                createEl('span', { class: 'dup-group-title', text: first.name, title: first.name }),
                createEl('span', {
                    class: 'dup-group-meta',
                    text: `${group.copies.length} kopieën · ${formatBytes(group.size)} per kopie · ${DUP_TYPE_LABELS[group.type] || group.type}${exif}`,
                }),
                createEl('span', { class: 'dup-group-override' }),
                createEl('span', { class: 'dup-group-actions' },
                    createEl('button', { class: 'btn btn-secondary btn-small', onClick: () => openComparisonModal(group), text: 'Vergelijk' }),
                    createEl('button', { class: 'btn btn-danger btn-small dup-group-clean', onClick: () => openCleanupDialog([group]) }))),
            createEl('div', { class: 'dup-copies' },
                group.copies.map((copy, index) => createEl('button', {
                    class: 'dup-copy',
                    dataset: { index: String(index) },
                    title: `${copy.path}\nKlik om deze kopie te bewaren`,
                    onClick: () => chooseCopy(group, index),
                },
                    createEl('span', { class: 'dup-copy-badge' }),
                    createEl('span', { class: 'dup-copy-path', text: relativeToScan(copy.path) }),
                    createEl('span', { class: 'dup-copy-date', text: fileDateLabel(copy) + (copy.hidden ? ' · verborgen' : '') }),
                    createEl('span', { class: 'dup-copy-error' }))))));
    updateGroupCard(card, group);
    return card;
}

function updateGroupCard(card, group) {
    const keep = keeperIndex(group);
    card.querySelectorAll('.dup-copy').forEach(row => {
        const index = Number(row.dataset.index);
        const copy = group.copies[index];
        const keeps = index === keep;
        row.classList.toggle('keep', keeps);
        row.classList.toggle('move', !keeps);
        row.setAttribute('aria-pressed', keeps ? 'true' : 'false');
        row.querySelector('.dup-copy-badge').textContent = keeps ? 'Blijft' : 'Naar RAW_DUPLICATES';
        const failure = dupState.failures.get(copy.path);
        row.querySelector('.dup-copy-error').textContent = failure ? `Niet verplaatst: ${failure}` : '';
    });
    const manual = dupState.overrides.has(group.id) && dupState.overrides.get(group.id) === group.copies[keep].path;
    card.classList.toggle('manual', manual);
    const override = card.querySelector('.dup-group-override');
    override.replaceChildren(...(manual ? [
        'Zelf gekozen · ',
        createEl('button', { class: 'link-button', text: 'regel', onClick: () => { dupState.overrides.delete(group.id); refreshAllGroups(); } }),
    ] : []));
    const moves = group.copies.length - 1;
    card.querySelector('.dup-group-clean').textContent = `Ruim op (${moves})`;
}

// ==================== SIDE-BY-SIDE COMPARISON ====================

function setComparisonMedia(prefix, copy, isVideo) {
    const img = document.getElementById(`comparison-${prefix}-img`);
    const video = document.getElementById(`comparison-${prefix}-video`);
    document.getElementById(`comparison-${prefix}-path`).textContent = copy.path;
    document.getElementById(`comparison-${prefix}-size`).textContent =
        `${formatBytes(copy.size)} · ${fileDateLabel(copy)}`;
    img.style.display = isVideo ? 'none' : 'block';
    video.style.display = isVideo ? 'block' : 'none';
    if (isVideo) { video.src = vaultFileUrl('file', copy.path); img.removeAttribute('src'); }
    else { img.src = vaultFileUrl('file', copy.path); video.removeAttribute('src'); }
}

function openComparisonModal(group) {
    currentComparisonGroup = group;
    const keep = keeperIndex(group);
    const other = group.copies.findIndex((_, i) => i !== keep);
    const isVideo = group.type === 'video';
    const isAudio = group.type === 'audio';

    setComparisonMedia('original', group.copies[keep], isVideo || isAudio);
    setComparisonMedia('duplicate', group.copies[other], isVideo || isAudio);
    currentComparisonGroup.compared = other;

    document.getElementById('btn-confirm-delete').textContent =
        `Ruim deze groep op (${group.copies.length - 1})`;
    document.getElementById('modal-comparison').classList.add('active');
}

function closeComparisonModal() {
    document.getElementById('modal-comparison').classList.remove('active');
    document.getElementById('comparison-original-video').pause();
    document.getElementById('comparison-duplicate-video').pause();
    currentComparisonGroup = null;
}

function keepComparedCopy() {
    const group = currentComparisonGroup;
    if (!group) return;
    chooseCopy(group, group.compared);
    openComparisonModal(group);
}

function confirmDelete() {
    const group = currentComparisonGroup;
    if (!group) return;
    closeComparisonModal();
    openCleanupDialog([group]);
}

// ==================== CLEAN UP (one run per action) ====================

function removeAllDuplicates() {
    if (!dupState.visible.length) {
        showToast('Geen duplicaten', 'Er is niets op te ruimen', 'info');
        return;
    }
    openCleanupDialog(dupState.visible);
}

function openCleanupDialog(groups) {
    if (dupState.cleanup && dupState.cleanup.running) return;
    const plan = groups.map(group => {
        const keep = keeperIndex(group);
        return {
            group,
            keep: group.copies[keep].path,
            move: group.copies.filter((_, i) => i !== keep).map(c => c.path),
        };
    });
    const files = plan.reduce((n, p) => n + p.move.length, 0);
    const bytes = plan.reduce((n, p) => n + p.group.size * p.move.length, 0);
    const manual = plan.filter(p => dupState.overrides.has(p.group.id)).length;
    dupState.cleanup = { plan, files, running: false, stop: false };

    const single = groups.length === 1;
    document.getElementById('dup-confirm-title').textContent = single
        ? `"${groups[0].copies[0].name}" opruimen?`
        : `${files} duplicaten opruimen?`;
    const ruleText = dupState.rule === 'folder' && dupState.prefer
        ? `${DUP_RULE_LABELS.folder} "${relativeToScan(dupState.prefer) || dupState.prefer}"`
        : DUP_RULE_LABELS[dupState.rule];
    const items = [
        `${files} bestand${files === 1 ? '' : 'en'} uit ${plan.length} groep${plan.length === 1 ? '' : 'en'} ` +
            `gaan naar RAW_DUPLICATES (${formatBytes(bytes)}).`,
        `Welke kopie blijft: ${ruleText}` + (manual ? `, ${manual} groep${manual === 1 ? '' : 'en'} zelf gekozen.` : '.'),
        'Er wordt niets gewist. Onder Herstellen zet je deze hele actie in één keer terug.',
        'Vlak voor elke verplaatsing controleert de server of de kopie die blijft er nog is en byte voor byte gelijk is; zo niet, dan blijft het bestand staan.',
    ];
    const list = document.getElementById('dup-confirm-list');
    list.replaceChildren(...items.map(text => createEl('li', { text })));
    if (single) {
        list.appendChild(createEl('li', { class: 'dup-confirm-keep', text: `Blijft: ${plan[0].keep}` }));
    }

    document.getElementById('dup-confirm-progress').hidden = true;
    const go = document.getElementById('btn-dup-go');
    go.disabled = files === 0;
    go.style.display = '';
    go.textContent = `Verplaats ${files} bestand${files === 1 ? '' : 'en'}`;
    document.getElementById('btn-dup-cancel').textContent = 'Annuleren';
    document.getElementById('modal-dup-confirm').classList.add('active');
}

function closeCleanupDialog() {
    const cleanup = dupState.cleanup;
    if (cleanup && cleanup.running) {
        cleanup.stop = true;  // stopt na het lopende stukje; wat al verplaatst is blijft in dezelfde actie
        document.getElementById('dup-confirm-text').textContent = 'Stoppen na dit stukje...';
        return;
    }
    document.getElementById('modal-dup-confirm').classList.remove('active');
    dupState.cleanup = null;
}

// Groepen in stukjes van ongeveer DUP_CHUNK_FILES bestanden (een groep wordt nooit gesplitst, tenzij hij zelf te groot is)
function cleanupChunks(plan) {
    const chunks = [];
    let current = [];
    let count = 0;
    plan.forEach(p => {
        for (let i = 0; i < p.move.length; i += DUP_CHUNK_FILES) {
            const part = { keep: p.keep, move: p.move.slice(i, i + DUP_CHUNK_FILES) };
            if (count + part.move.length > DUP_CHUNK_FILES && current.length) {
                chunks.push(current);
                current = [];
                count = 0;
            }
            current.push(part);
            count += part.move.length;
        }
    });
    if (current.length) chunks.push(current);
    return chunks;
}

async function runCleanup() {
    const cleanup = dupState.cleanup;
    if (!cleanup || cleanup.running) return;
    cleanup.running = true;

    const go = document.getElementById('btn-dup-go');
    const cancel = document.getElementById('btn-dup-cancel');
    const bar = document.getElementById('dup-confirm-bar');
    const text = document.getElementById('dup-confirm-text');
    go.style.display = 'none';
    cancel.textContent = 'Stoppen';
    document.getElementById('dup-confirm-progress').hidden = false;

    let runId = null;
    let done = 0;
    const moved = new Set();
    const failed = new Map();
    let requestError = null;

    for (const chunk of cleanupChunks(cleanup.plan)) {
        if (cleanup.stop) break;
        const percent = Math.round(done / cleanup.files * 100);
        bar.style.width = percent + '%';
        bar.textContent = percent + '%';
        text.textContent = `Verplaatsen... ${done}/${cleanup.files}`;
        try {
            const response = await fetch(`${API_BASE}/api/duplicates/move`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ groups: chunk, run_id: runId, folder: dupState.folder }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || `Fout ${response.status}`);
            if (data.run_id) runId = data.run_id;
            data.moved.forEach(m => moved.add(m.path));
            data.failed.forEach(f => failed.set(f.path, typeof f.detail === 'string' ? f.detail : `fout ${f.status}`));
        } catch (error) {
            requestError = error.message;
            break;
        }
        done += chunk.reduce((n, part) => n + part.move.length, 0);
    }
    bar.style.width = '100%';
    bar.textContent = '100%';

    applyCleanupResult(moved, failed);
    cleanup.running = false;
    closeCleanupDialog();

    const skipped = cleanup.files - moved.size - failed.size;
    if (requestError) {
        showToast('Opruimen afgebroken', `${moved.size} verplaatst; daarna: ${requestError}`, 'error');
    } else if (failed.size || skipped) {
        showToast('Deels opgeruimd',
            `${moved.size} verplaatst, ${failed.size} niet verplaatst` + (skipped > 0 ? `, ${skipped} niet geprobeerd (gestopt)` : '') +
            '. De redenen staan bij de bestanden.', 'warning');
    } else {
        showToast('Opgeruimd', `${moved.size} bestand${moved.size === 1 ? '' : 'en'} naar RAW_DUPLICATES. Terugzetten kan onder Herstellen.`, 'success');
    }
    loadDuplicateRuns();
}

function applyCleanupResult(moved, failed) {
    failed.forEach((detail, path) => dupState.failures.set(path, detail));
    moved.forEach(path => dupState.failures.delete(path));
    // Verplaatste kopieën verdwijnen uit hun groep; een groep met nog één kopie is klaar
    dupState.groups = dupState.groups
        .map(g => moved.size && g.copies.some(c => moved.has(c.path))
            ? { ...g, copies: g.copies.filter(c => !moved.has(c.path)) }
            : g)
        .filter(g => g.copies.length > 1);
    // name_rank blijft geldig: de volgorde van de overgebleven kopieën verandert niet
    fillPreferFolderOptions();
    filterDuplicates(dupState.filter);
}

// ==================== HERSTELLEN ====================

async function loadDuplicateRuns() {
    const container = document.getElementById('dup-runs');
    if (!container) return;
    try {
        const response = await fetch(`${API_BASE}/api/duplicates/runs`);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Fout ${response.status}`);
        renderDuplicateRuns(data.runs || []);
    } catch (error) {
        container.replaceChildren(createEl('div', { class: 'dup-runs-empty', text: `Kon de geschiedenis niet laden: ${error.message}` }));
    }
}

function renderDuplicateRuns(runs) {
    const container = document.getElementById('dup-runs');
    if (!runs.length) {
        container.replaceChildren(createEl('div', { class: 'dup-runs-empty', text: 'Nog geen opruimacties om terug te zetten.' }));
        return;
    }
    container.replaceChildren(...runs.map(run => {
        const when = run.created_at
            ? new Date(run.created_at * 1000).toLocaleString('nl-NL', { dateStyle: 'medium', timeStyle: 'short' })
            : (run.timestamp || '?');
        let count = `${run.remaining} bestand${run.remaining === 1 ? '' : 'en'}`;
        if (run.remaining !== run.total_files) count += ` (van ${run.total_files})`;
        const gone = run.remaining - run.restorable;
        const note = run.restorable === 0
            ? 'staat niet meer in RAW_DUPLICATES'
            : gone > 0 ? `${gone} staan niet meer in RAW_DUPLICATES` : '';
        return createEl('div', { class: 'dup-run' },
            createEl('span', { class: 'dup-run-time', text: when }),
            createEl('span', { class: 'dup-run-count', text: count }),
            createEl('span', { class: 'dup-run-folder', text: run.folder || '', title: run.folder || '' }),
            createEl('span', { class: 'dup-run-note', text: note }),
            createEl('button', {
                class: 'btn btn-secondary btn-small',
                disabled: run.restorable === 0,
                onClick: (event) => undoDuplicateRun(run, event.currentTarget),
                text: 'Terugzetten',
            }));
    }));
}

async function undoDuplicateRun(run, button) {
    const n = run.restorable;
    if (!confirm(`${n} bestand${n === 1 ? '' : 'en'} terugzetten naar de oorspronkelijke plek?\n\n` +
                 'Staat daar intussen een ander bestand, dan komt het teruggezette bestand ernaast ' +
                 '(naam_RESTORED_001); er wordt niets overschreven.')) {
        return;
    }
    button.disabled = true;
    try {
        const response = await fetch(`${API_BASE}/api/duplicates/undo`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ run_id: run.run_id }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `Fout ${response.status}`);
        const beside = (data.files || []).filter(f => f.restored_path !== f.src_path).length;
        let message = `${data.restored} bestand${data.restored === 1 ? '' : 'en'} teruggezet.`;
        if (beside) message += ` ${beside} kwam${beside === 1 ? '' : 'en'} naast een bestaand bestand (_RESTORED_).`;
        if (data.errors && data.errors.length) message += ` ${data.errors.length} fout(en): ${data.errors[0]}`;
        if (dupState.folder) message += ' Scan opnieuw om ze weer als duplicaat te zien.';
        showToast('Teruggezet', message, data.errors && data.errors.length ? 'warning' : 'success');
    } catch (error) {
        showToast('Terugzetten mislukt', error.message, 'error');
    } finally {
        loadDuplicateRuns();
    }
}

function duplicatesInit() {
    loadDuplicateRuns();
    document.getElementById('dup-rule').value = dupState.rule;
}
