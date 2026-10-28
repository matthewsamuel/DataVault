// DataVault - Foto Keurer (Tinder-style foto beoordeling)
// Vereist: folderTree, showToast, API_BASE (uit app.js)

// ==================== STAAT ====================

let keurerFolder = localStorage.getItem('keurer_folder') || '';
let keurerFileQueue = [];
let keurerIndex = 0;
let keurerStats = { total: 0, today: 0, goal: 20, streak: 0, goal_reached_today: false };
let keurerIsAnimating = false;
let keurerSelectedFolderForModal = null;
let keurerMoveSelectedFolder = null;
let keurerPreviewOpen = false;
let keurerMoveHighlightIndex = -1;

// Undo-stapel (nieuwste achteraan): { id, decision, folder, file }.
// De server bewaart waar elk bestand heen ging; undo stuurt alleen het id.
let keurerHistory = [];
const KEURER_UNDO_MAX = 20;
let keurerUndoBusy = false;
let keurerMoveBusy = false;   // één verplaatsverzoek tegelijk (Enter in zoekveld én boom)
let keurerLoadFailed = false; // laatste keer laden mislukt (bijv. schijf los): opnieuw zodra hij terug is

// ==================== INITIALISATIE ====================

function keurerInit() {
    try {
        keurerLoadStats();

        const display = document.getElementById('keurer-folder-display');
        if (display) {
            display.textContent = keurerFolder
                ? keurerShortenPath(keurerFolder)
                : 'Geen map geselecteerd';
        }

        // Undo-stapel eerst van de server halen, zodat hij een herlaad overleeft
        keurerLoadHistory().finally(() => {
            if (keurerFolder) {
                keurerLoadFiles();
            } else {
                keurerShowEmpty();
            }
        });
    } catch (e) {
        console.error('keurerInit fout:', e);
        const cardArea = document.getElementById('keurer-card-area');
        if (cardArea) {
            cardArea.innerHTML = html`<div class="keurer-state-card">
                <span style="font-size:3rem">⚠️</span>
                <h3>Initialisatie mislukt</h3>
                <p style="color:var(--danger)">${e.message}</p>
            </div>`;
        }
    }
}

// De schijf is terug (app.js reloadAfterVaultReturn): statistieken en undo-stapel opnieuw,
// en de foto's alleen als dat laden mislukt was (een lopende reeks blijft staan)
function keurerVaultReturned() {
    keurerLoadStats();
    keurerLoadHistory().finally(() => {
        if (keurerFolder && keurerLoadFailed) keurerLoadFiles();
    });
}

// ==================== STATISTIEKEN ====================

async function keurerLoadStats() {
    try {
        keurerStats = await fetchJSON(`${API_BASE}/api/keurer/stats`);
        keurerRenderStats();
    } catch (e) {
        console.warn('Keurer stats ophalen mislukt:', e);
    }
}

// Statistieken altijd van de server overnemen (na beslissing of undo)
function keurerApplyStats(stats) {
    if (!stats) { keurerLoadStats(); return; }
    const wasReached = keurerStats.goal_reached_today;
    keurerStats = stats;
    keurerRenderStats();
    if (!wasReached && stats.goal_reached_today) {
        showToast('🎉 Dagdoel bereikt!', `Je hebt vandaag ${stats.goal} fotos beoordeeld!`, 'success');
    }
}

// ==================== UNDO-STAPEL ====================

async function keurerLoadHistory() {
    try {
        const data = await fetchJSON(`${API_BASE}/api/keurer/history`);
        // Server geeft nieuwste eerst; de stapel heeft de nieuwste achteraan
        keurerHistory = (data.entries || []).slice(0, KEURER_UNDO_MAX).reverse();
    } catch (e) {
        console.warn('Keurer undo-geschiedenis ophalen mislukt:', e);
    }
}

function keurerDropHistory(id) {
    keurerHistory = keurerHistory.filter(e => e.id !== id);
}

function keurerPushHistory(data) {
    keurerHistory.push({ id: data.id, decision: data.decision, folder: data.folder, file: data.file });
    if (keurerHistory.length > KEURER_UNDO_MAX) keurerHistory.shift();
}

function keurerUpdateRemaining() {
    const remEl = document.getElementById('keurer-remaining');
    if (remEl) {
        const rem = keurerFileQueue.length - keurerIndex;
        remEl.textContent = rem > 0 ? `${rem} nog te beoordelen` : '';
    }
}

function keurerRenderStats() {
    const el = document.getElementById('keurer-stats-bar');
    if (!el) return;

    const streakIcon = keurerStats.streak > 0 ? '🔥' : '❄️';
    const todayPct = Math.min(100, (keurerStats.today / keurerStats.goal) * 100);
    const streakCls = keurerStats.streak > 0 ? 'streak-active' : '';
    const goalCls = keurerStats.goal_reached_today ? 'goal-reached' : '';

    el.innerHTML = html`
        <div class="keurer-stat ${streakCls}" title="${keurerStats.streak} opeenvolgende dag(en) met ${keurerStats.goal}+ beoordelingen">
            <span class="keurer-stat-icon">${streakIcon}</span>
            <span class="keurer-stat-value">${keurerStats.streak}</span>
            <span class="keurer-stat-label">dag streak</span>
        </div>
        <div class="keurer-stat-divider"></div>
        <div class="keurer-stat ${goalCls}" title="Dagdoel: ${keurerStats.goal} beoordelingen">
            <span class="keurer-stat-icon">📅</span>
            <div class="keurer-daily-block">
                <div class="keurer-daily-bar">
                    <div class="keurer-daily-fill" style="width: ${todayPct}%"></div>
                </div>
                <span class="keurer-stat-value">${keurerStats.today}<span class="keurer-stat-dim">/${keurerStats.goal}</span></span>
            </div>
            <span class="keurer-stat-label">vandaag</span>
        </div>
        <div class="keurer-stat-divider"></div>
        <div class="keurer-stat" title="Totaal aantal beoordeelde bestanden ooit">
            <span class="keurer-stat-icon">🏷️</span>
            <span class="keurer-stat-value">${keurerStats.total.toLocaleString('nl-NL')}</span>
            <span class="keurer-stat-label">totaal beoordeeld</span>
        </div>
    `;
}

// ==================== BESTANDEN LADEN ====================

async function keurerLoadFiles() {
    if (!keurerFolder) { keurerShowEmpty(); return; }

    const cardArea = document.getElementById('keurer-card-area');
    const btnArea  = document.getElementById('keurer-action-buttons');
    if (btnArea) btnArea.style.display = 'none';
    const remEl = document.getElementById('keurer-remaining');
    if (remEl) remEl.textContent = '';

    cardArea.innerHTML = `
        <div class="keurer-state-card">
            <div class="loading-spinner"></div>
            <p style="margin-top: 16px;">Fotos laden...</p>
        </div>`;

    try {
        const data = await fetchJSON(`${API_BASE}/api/keurer/files?folder=${encodeURIComponent(keurerFolder)}`);
        // De server geeft de map in één vaste schrijfwijze terug (symlinks en Unicode-vorm
        // opgelost); die gebruiken we voortaan, zodat undo de beslissing aan deze map koppelt
        if (data.folder && data.folder !== keurerFolder) {
            keurerFolder = data.folder;
            localStorage.setItem('keurer_folder', keurerFolder);
        }
        keurerFileQueue = data.files;
        keurerIndex = 0;
        keurerLoadFailed = false;

        if (remEl) {
            const rem  = data.remaining || 0;
            const done = data.decided  || 0;
            remEl.textContent = `${rem} nog te beoordelen${done > 0 ? ` · ${done} al gedaan` : ''}`;
        }

        if (keurerFileQueue.length === 0) {
            keurerShowDone(data.decided || 0);
        } else {
            keurerShowCard();
        }
    } catch (e) {
        keurerLoadFailed = true;
        cardArea.innerHTML = html`
            <div class="keurer-state-card">
                <span style="font-size: 3rem;">⚠️</span>
                <h3>Laden mislukt</h3>
                <p>${e.message}</p>
                <button class="btn btn-secondary" onclick="keurerLoadFiles()">Opnieuw proberen</button>
            </div>`;
    }
}

function keurerShowEmpty() {
    const cardArea = document.getElementById('keurer-card-area');
    if (!cardArea) return;
    const btnArea = document.getElementById('keurer-action-buttons');
    if (btnArea) btnArea.style.display = 'none';
    const remEl = document.getElementById('keurer-remaining');
    if (remEl) remEl.textContent = '';

    cardArea.innerHTML = `
        <div class="keurer-state-card">
            <span style="font-size: 4rem;">📁</span>
            <h3>Kies een map</h3>
            <p>Selecteer een map om foto's en video's te beoordelen.</p>
            <button class="btn btn-primary" onclick="keurerOpenFolderPicker()">
                <i class="fas fa-folder-open"></i> Map kiezen
            </button>
        </div>`;
}

function keurerShowDone(decidedCount) {
    const cardArea = document.getElementById('keurer-card-area');
    const btnArea  = document.getElementById('keurer-action-buttons');
    if (btnArea) btnArea.style.display = 'none';

    cardArea.innerHTML = html`
        <div class="keurer-state-card">
            <span style="font-size: 4rem;">✅</span>
            <h3>Map volledig beoordeeld!</h3>
            <p>Alle ${decidedCount} bestanden in deze map zijn al beoordeeld.</p>
            <div style="display: flex; gap: 10px; flex-wrap: wrap; justify-content: center;">
                <button class="btn btn-secondary" onclick="keurerOpenFolderPicker()">
                    <i class="fas fa-folder"></i> Andere map
                </button>
            </div>
        </div>`;
}

// ==================== KAART WEERGEVEN ====================

function keurerShowCard() {
    const cardArea = document.getElementById('keurer-card-area');
    const btnArea  = document.getElementById('keurer-action-buttons');

    if (keurerIndex >= keurerFileQueue.length) {
        keurerLoadFiles();
        return;
    }

    if (btnArea) btnArea.style.display = 'flex';

    const file     = keurerFileQueue[keurerIndex];
    const thumbUrl = `${API_BASE}/api/thumb?path=${encodeURIComponent(file.path)}`;
    const fileUrl  = `${API_BASE}/api/file?path=${encodeURIComponent(file.path)}`;

    const mediaHtml = file.is_video
        ? html`<video
                class="keurer-card-img"
                id="kc-video"
                src="${fileUrl}"
                autoplay muted loop playsinline
                draggable="false"
                onerror="keurerHandleThumbError(this)"
           ></video>`
        : html`<img
                class="keurer-card-img"
                src="${thumbUrl}"
                alt="${file.name}"
                draggable="false"
                onerror="keurerHandleThumbError(this)"
           >`;

    const undoAvail = keurerHistory.length > 0;

    cardArea.innerHTML = html`
        <div class="keurer-card-wrapper">
            <div class="keurer-card" id="keurer-card">

                <!-- Overlays -->
                <div class="keurer-card-overlay overlay-keep"   id="kc-overlay-keep">
                    <div class="overlay-badge keep-badge"><i class="fas fa-heart"></i> HOUDEN</div>
                </div>
                <div class="keurer-card-overlay overlay-delete" id="kc-overlay-delete">
                    <div class="overlay-badge delete-badge"><i class="fas fa-trash"></i> WEGGOOIEN</div>
                </div>
                <div class="keurer-card-overlay overlay-move"   id="kc-overlay-move">
                    <div class="overlay-badge move-badge"><i class="fas fa-folder-open"></i> VERPLAATSEN</div>
                </div>
                <div class="keurer-card-overlay overlay-undo"   id="kc-overlay-undo">
                    <div class="overlay-badge undo-badge"><i class="fas fa-undo"></i> ONGEDAAN</div>
                </div>

                ${mediaHtml}
                <div class="keurer-card-fallback" id="kc-fallback" style="display:none;">
                    <i class="fas fa-${file.is_video ? 'video' : 'image'}" style="font-size:3rem;color:var(--text-secondary);"></i>
                    <span style="color:var(--text-secondary);margin-top:12px;font-size:0.85rem;text-align:center;padding:0 16px;">${file.name}</span>
                </div>

                <div class="keurer-card-footer">
                    <span class="keurer-card-name" title="${file.name}">${file.name}</span>
                    <div style="display:flex;align-items:center;gap:10px;flex-shrink:0;">
                        ${undoAvail ? html`<span class="keurer-undo-hint" title="Ongedaan maken (↓ pijl)"><i class="fas fa-undo"></i></span>` : ''}
                        <span class="keurer-card-progress">${keurerIndex + 1} / ${keurerFileQueue.length}</span>
                    </div>
                </div>
            </div>
        </div>`;

    keurerSetupDrag(document.getElementById('keurer-card'));
}

function keurerHandleThumbError(el) {
    el.style.display = 'none';
    const fallback = document.getElementById('kc-fallback');
    if (fallback) fallback.style.display = 'flex';
}

// ==================== BESLISSING & ANIMATIE ====================

function keurerAnimateAndDecide(decision) {
    if (keurerIsAnimating) return;
    if (keurerIndex >= keurerFileQueue.length) return;

    keurerIsAnimating = true;
    const card = document.getElementById('keurer-card');
    if (!card) { keurerIsAnimating = false; return; }

    let flyX = 0, flyY = -60, rotate = 0;
    if (decision === 'keep') {
        flyX   = -Math.max(window.innerWidth * 1.1, 600);
        rotate = -22;
        const ol = document.getElementById('kc-overlay-keep');
        if (ol) ol.style.opacity = '1';
    } else {
        flyX   = Math.max(window.innerWidth * 1.1, 600);
        rotate = 22;
        const ol = document.getElementById('kc-overlay-delete');
        if (ol) ol.style.opacity = '1';
    }

    card.style.transition = 'transform 0.38s cubic-bezier(0.5,0,0.75,0), opacity 0.38s ease';
    card.style.transform  = `translateX(${flyX}px) translateY(${flyY}px) rotate(${rotate}deg)`;
    card.style.opacity    = '0';

    const result = keurerSendDecision(decision, keurerFileQueue[keurerIndex]);

    // Pas na het antwoord van de server door naar de volgende kaart;
    // mislukt de beslissing, dan komt dezelfde kaart terug.
    setTimeout(async () => {
        const outcome = await result;
        if (outcome === 'ok') {
            keurerIndex++;
        } else if (outcome === 'gone') {
            keurerFileQueue.splice(keurerIndex, 1);
        }
        keurerIsAnimating = false;
        keurerUpdateRemaining();
        keurerShowCard();
    }, 390);
}

// Animeer kaart omhoog weg en open move-modal
function keurerAnimateUp() {
    if (keurerIsAnimating) return;
    if (keurerIndex >= keurerFileQueue.length) return;

    keurerIsAnimating = true;
    const card = document.getElementById('keurer-card');
    if (!card) { keurerIsAnimating = false; return; }

    const ol = document.getElementById('kc-overlay-move');
    if (ol) ol.style.opacity = '1';

    card.style.transition = 'transform 0.35s cubic-bezier(0.5,0,0.75,0), opacity 0.35s ease';
    card.style.transform  = 'translateY(-120%) scale(0.85)';
    card.style.opacity    = '0';

    setTimeout(() => {
        keurerIsAnimating = false;
        keurerOpenMoveModal();
    }, 350);
}

// Geeft 'ok', 'gone' (bestand bestaat niet meer) of 'failed' terug
async function keurerSendDecision(decision, file) {
    try {
        const data = await fetchJSON(`${API_BASE}/api/keurer/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: file.path, folder: keurerFolder, decision }),
        });
        keurerPushHistory({ id: data.id, decision, folder: keurerFolder, file });
        keurerApplyStats(data.stats);
        return 'ok';
    } catch (e) {
        if (e.status === 404) {
            showToast('Overgeslagen', `${file.name}: ${e.message}`, 'warning');
            return 'gone';
        }
        toastFetchError(`Beslissing mislukt voor ${file.name}`, e);
        return 'failed';
    }
}

function keurerDecideKeep()   { keurerAnimateAndDecide('keep'); }
function keurerDecideDelete() { keurerAnimateAndDecide('delete'); }
function keurerDecideMove()   { keurerAnimateUp(); }

async function keurerUndo() {
    if (keurerIsAnimating || keurerUndoBusy) return;
    if (keurerHistory.length === 0) {
        showToast('Undo', 'Geen actie om ongedaan te maken', 'info');
        return;
    }

    const card = document.getElementById('keurer-card');
    if (card) {
        const ol = document.getElementById('kc-overlay-undo');
        if (ol) ol.style.opacity = '1';
        card.style.transition = 'transform 0.3s ease, opacity 0.3s ease';
        card.style.transform  = 'translateY(80px) scale(0.92)';
        card.style.opacity    = '0';
    }

    const entry = keurerHistory[keurerHistory.length - 1];
    keurerUndoBusy = true;

    try {
        const data = await fetchJSON(`${API_BASE}/api/keurer/undo`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: entry.id }),
        });
        keurerDropHistory(entry.id);
        keurerApplyStats(data.stats);

        // Alleen terug in de wachtrij als de beslissing bij de huidige map hoort
        const file = data.file || entry.file;
        if (data.folder === keurerFolder && !keurerFileQueue.some(f => f.path === file.path)) {
            keurerFileQueue.splice(keurerIndex, 0, file);
        }
        keurerUpdateRemaining();

        const label = entry.decision === 'delete' ? 'prullenmand' : entry.decision === 'move' ? 'verplaatsing' : 'bewaren';
        const where = data.folder === keurerFolder ? '' : ` (in ${keurerShortenPath(data.folder)})`;
        showToast('Ongedaan gemaakt', `${label} teruggedraaid voor ${file.name}${where}`, 'success');

    } catch (e) {
        // 404/409/410: deze stap kan niet (meer) terug; haal hem van de stapel
        if ([404, 409, 410].includes(e.status)) keurerDropHistory(entry.id);
        toastFetchError('Undo mislukt', e);
    } finally {
        keurerUndoBusy = false;
    }

    setTimeout(() => { keurerShowCard(); }, 320);
}

// ==================== VERPLAATS MODAL ====================

// Gecachte platte lijst van alle mappen voor zoeken
let _keurerFlatFolders = null;

function keurerFlattenTree(folders, result = []) {
    for (const f of folders) {
        result.push({ name: f.name, path: f.path });
        if (f.children && f.children.length > 0) {
            keurerFlattenTree(f.children, result);
        }
    }
    return result;
}

async function keurerOpenMoveModal() {
    if (keurerIndex >= keurerFileQueue.length) { keurerShowCard(); return; }

    const modal = document.getElementById('modal-keurer-move');
    modal.classList.add('active');
    keurerMoveSelectedFolder = null;
    keurerMoveHighlightIndex = -1;
    document.getElementById('btn-keurer-confirm-move').disabled = true;

    // Zoekbalk resetten en direct focussen
    const searchEl = document.getElementById('keurer-move-search');
    const clearBtn = document.getElementById('keurer-move-search-clear');
    if (searchEl) { searchEl.value = ''; }
    if (clearBtn) clearBtn.style.display = 'none';
    requestAnimationFrame(() => { if (searchEl) searchEl.focus(); });

    const nameEl = document.getElementById('keurer-move-filename');
    if (nameEl) nameEl.textContent = keurerFileQueue[keurerIndex].name;

    const treeContainer = document.getElementById('keurer-move-folder-tree');
    treeContainer.innerHTML = '<div class="loading-spinner" style="margin:2rem auto;display:block;"></div>';

    const tree = (typeof folderTree !== 'undefined' && folderTree.length > 0)
        ? folderTree
        : await (async () => {
            try {
                const data = await fetchJSON(`${API_BASE}/api/folders`);
                return data.folders || [];
            } catch (_) { return []; }
        })();

    // Bouw platte lijst voor zoekfunctie
    _keurerFlatFolders = keurerFlattenTree(tree);

    treeContainer.innerHTML = '';

    if (tree.length === 0) {
        treeContainer.innerHTML = '<p style="color:var(--danger);padding:1rem;">Mappen konden niet geladen worden.</p>';
        return;
    }

    tree.forEach(f => keurerRenderMoveFolderNode(treeContainer, f, 0));

    // Keyboard nav via centraal systeem
    window._keurerMoveNav = activateFolderTreeModal({
        modalId: 'modal-keurer-move',
        containerId: 'keurer-move-folder-tree',
        onSelect: (item) => { if (item.dataset.path) item.click(); },
        onConfirm: () => { if (keurerMoveSelectedFolder) keurerConfirmMove(); },
        onClose: keurerCloseMoveModal,
        focusSelector: '#keurer-move-search',
    });
}

function keurerFilterMoveTree(query) {
    const treeContainer = document.getElementById('keurer-move-folder-tree');
    const clearBtn      = document.getElementById('keurer-move-search-clear');
    if (clearBtn) clearBtn.style.display = query.length > 0 ? 'flex' : 'none';

    // Reset selectie en highlight bij nieuwe zoekopdracht
    keurerMoveSelectedFolder = null;
    keurerMoveHighlightIndex = -1;
    document.getElementById('btn-keurer-confirm-move').disabled = true;

    if (!query.trim()) {
        // Herstel de volledige boom
        treeContainer.innerHTML = '';
        const tree = (typeof folderTree !== 'undefined' && folderTree.length > 0) ? folderTree : [];
        tree.forEach(f => keurerRenderMoveFolderNode(treeContainer, f, 0));
        return;
    }

    const q = query.toLowerCase();

    // Splits in "begint met" en "bevat"
    const starts   = [];
    const contains = [];

    for (const f of (_keurerFlatFolders || [])) {
        const nameLower = f.name.toLowerCase();
        if (nameLower.startsWith(q))      starts.push(f);
        else if (nameLower.includes(q))   contains.push(f);
    }

    treeContainer.innerHTML = '';

    if (starts.length === 0 && contains.length === 0) {
        treeContainer.innerHTML = '<p style="color:var(--text-secondary);padding:1rem 12px;font-size:0.85rem;">Geen mappen gevonden</p>';
        return;
    }

    const render = (list, groupLabel) => {
        if (list.length === 0) return;
        const header = document.createElement('div');
        header.className = 'keurer-move-group-label';
        header.textContent = groupLabel;
        treeContainer.appendChild(header);

        for (const f of list) {
            const item = document.createElement('div');
            item.className = 'folder-item keurer-move-flat-item';
            item.dataset.path = f.path;

            const icon = document.createElement('span');
            icon.className = 'folder-icon';
            icon.textContent = '📁';
            item.appendChild(icon);

            const nameEl = document.createElement('span');
            nameEl.className = 'folder-name';
            setHTML(nameEl, keurerHighlight(f.name, q));
            item.appendChild(nameEl);

            // Volledig pad als subtekst
            const pathEl = document.createElement('span');
            pathEl.className = 'keurer-move-flat-path';
            pathEl.textContent = keurerShortenPath(f.path);
            item.appendChild(pathEl);

            item.onclick = () => {
                treeContainer.querySelectorAll('.folder-item').forEach(el => el.classList.remove('selected'));
                item.classList.add('selected');
                keurerMoveSelectedFolder = f.path;
                document.getElementById('btn-keurer-confirm-move').disabled = false;
            };

            treeContainer.appendChild(item);
        }
    };

    render(starts,   'Begint met "' + query + '"');
    render(contains, 'Bevat "' + query + '"');
}

function keurerClearMoveSearch() {
    const searchEl = document.getElementById('keurer-move-search');
    if (searchEl) { searchEl.value = ''; searchEl.focus(); }
    keurerFilterMoveTree('');
}

function keurerHighlight(name, query) {
    // Markeer de eerste match (case-insensitief) vet
    const idx = name.toLowerCase().indexOf(query.toLowerCase());
    if (idx === -1) return html`${name}`;
    return html`${name.slice(0, idx)}<mark class="keurer-hl">${name.slice(idx, idx + query.length)}</mark>${name.slice(idx + query.length)}`;
}

// ==================== KEYBOARD NAV IN MODAL ====================

// Haal alleen zichtbare folder-items op (niet in collapsed containers)
function keurerGetVisibleMoveItems() {
    const all = document.querySelectorAll('#keurer-move-folder-tree .folder-item');
    return Array.from(all).filter(el => {
        // Controleer of het item of een parent verborgen is (collapsed)
        let parent = el.parentElement;
        while (parent && parent.id !== 'keurer-move-folder-tree') {
            if (parent.classList.contains('collapsed')) return false;
            parent = parent.parentElement;
        }
        return true;
    });
}

function keurerHandleMoveSearchKeydown(e) {
    const items = keurerGetVisibleMoveItems();

    if (e.key === 'ArrowDown') {
        e.preventDefault();
        e.stopPropagation();
        if (items.length === 0) return;
        keurerMoveHighlightIndex = Math.min(items.length - 1, keurerMoveHighlightIndex + 1);
        keurerUpdateMoveHighlight(items);
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        e.stopPropagation();
        if (items.length === 0) return;
        keurerMoveHighlightIndex = Math.max(0, keurerMoveHighlightIndex - 1);
        keurerUpdateMoveHighlight(items);
    } else if (e.key === 'Enter') {
        e.preventDefault();
        e.stopPropagation();
        if (keurerMoveHighlightIndex >= 0 && keurerMoveHighlightIndex < items.length) {
            // Selecteer de map en bevestig verplaatsen
            items[keurerMoveHighlightIndex].click();
            keurerConfirmMove();
        } else if (keurerMoveSelectedFolder) {
            // Als er geen highlight is maar wel een geselecteerde map (via klik)
            keurerConfirmMove();
        }
    } else if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        keurerCloseMoveModal();
    }
}

function keurerUpdateMoveHighlight(items) {
    // Verwijder keyboard-focus van alle items in de tree
    document.querySelectorAll('#keurer-move-folder-tree .folder-item.keyboard-focus')
        .forEach(el => el.classList.remove('keyboard-focus'));

    if (keurerMoveHighlightIndex >= 0 && keurerMoveHighlightIndex < items.length) {
        const target = items[keurerMoveHighlightIndex];
        target.classList.add('keyboard-focus');
        target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });

        // Selecteer de map meteen zodat de knop actief wordt
        const path = target.dataset.path;
        if (path) {
            document.querySelectorAll('#keurer-move-folder-tree .folder-item')
                .forEach(el => el.classList.remove('selected'));
            target.classList.add('selected');
            keurerMoveSelectedFolder = path;
            document.getElementById('btn-keurer-confirm-move').disabled = false;
        }
    }
}

function keurerRenderMoveFolderNode(container, folder, depth) {
    const wrapper = document.createElement('div');

    const item = document.createElement('div');
    item.className = 'folder-item';
    item.style.paddingLeft = `${depth * 20 + 8}px`;
    item.dataset.path = folder.path;

    const hasChildren = folder.children && folder.children.length > 0;

    if (hasChildren) {
        const chevron = document.createElement('span');
        chevron.className = 'folder-chevron';
        chevron.innerHTML = '▶';
        chevron.onclick = e => {
            e.stopPropagation();
            const childDiv = wrapper.querySelector(':scope > .folder-children');
            if (childDiv) {
                const isOpen = !childDiv.classList.contains('collapsed');
                childDiv.classList.toggle('collapsed', isOpen);
                chevron.classList.toggle('expanded', !isOpen);
                // Reset keyboard highlight — zichtbare items zijn veranderd
                keurerMoveHighlightIndex = -1;
                document.querySelectorAll('#keurer-move-folder-tree .folder-item.keyboard-focus')
                    .forEach(el => el.classList.remove('keyboard-focus'));
            }
        };
        item.appendChild(chevron);
    } else {
        const spacer = document.createElement('span');
        spacer.style.cssText = 'width:16px;display:inline-block;flex-shrink:0;';
        item.appendChild(spacer);
    }

    const icon = document.createElement('span');
    icon.className = 'folder-icon';
    icon.textContent = '📁';
    item.appendChild(icon);

    const name = document.createElement('span');
    name.className = 'folder-name';
    name.textContent = folder.name;
    item.appendChild(name);

    item.onclick = () => {
        document.querySelectorAll('#keurer-move-folder-tree .folder-item').forEach(el => el.classList.remove('selected'));
        item.classList.add('selected');
        keurerMoveSelectedFolder = folder.path;
        document.getElementById('btn-keurer-confirm-move').disabled = false;
    };

    wrapper.appendChild(item);

    if (hasChildren) {
        const childDiv = document.createElement('div');
        childDiv.className = 'folder-children collapsed';
        folder.children.forEach(child => keurerRenderMoveFolderNode(childDiv, child, depth + 1));
        wrapper.appendChild(childDiv);
    }

    container.appendChild(wrapper);
}

function keurerCloseMoveModal() {
    if (keurerMoveBusy) return; // het antwoord van de server sluit de modal
    document.getElementById('modal-keurer-move').classList.remove('active');
    keurerMoveSelectedFolder = null;
    // Zoekbalk wissen
    const searchEl = document.getElementById('keurer-move-search');
    if (searchEl) searchEl.value = '';
    const clearBtn = document.getElementById('keurer-move-search-clear');
    if (clearBtn) clearBtn.style.display = 'none';
    // Kaart terugzetten als annuleren
    keurerShowCard();
}

async function keurerConfirmMove() {
    if (keurerMoveBusy || !keurerMoveSelectedFolder) return;
    if (keurerIndex >= keurerFileQueue.length) return;
    keurerMoveBusy = true;

    const file = keurerFileQueue[keurerIndex];
    const btn  = document.getElementById('btn-keurer-confirm-move');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Bezig...';

    try {
        const movedData = await fetchJSON(`${API_BASE}/api/keurer/decide`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                path: file.path,
                folder: keurerFolder,
                decision: 'move',
                destination_folder: keurerMoveSelectedFolder,
            }),
        });

        document.getElementById('modal-keurer-move').classList.remove('active');
        const destFolder = keurerMoveSelectedFolder;
        keurerMoveSelectedFolder = null;
        btn.innerHTML = '<i class="fas fa-check"></i> Verplaatsen';

        // Server bewaart de verplaatsing; undo gaat via het id
        keurerPushHistory({ id: movedData.id, decision: 'move', folder: keurerFolder, file });
        keurerApplyStats(movedData.stats);

        // Verwijder bestand uit queue
        keurerFileQueue.splice(keurerIndex, 1);
        keurerUpdateRemaining();

        const newName = (movedData.dest_path || '').split('/').pop();
        const renamed = newName && newName !== file.name ? ` als ${newName}` : '';
        showToast('Verplaatst', `Bestand verplaatst naar ${destFolder.split('/').pop()}${renamed}`, 'success');
        keurerShowCard();

    } catch (e) {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-check"></i> Verplaatsen';
        toastFetchError('Verplaatsen mislukt', e);
    } finally {
        keurerMoveBusy = false;
    }
}

// ==================== PREVIEW FULLSCREEN ====================

function keurerTogglePreview() {
    if (keurerPreviewOpen) {
        keurerClosePreview();
    } else {
        keurerOpenPreview();
    }
}

function keurerOpenPreview() {
    if (keurerIndex >= keurerFileQueue.length) return;

    const file    = keurerFileQueue[keurerIndex];
    const fileUrl = `${API_BASE}/api/file?path=${encodeURIComponent(file.path)}`;

    let mediaHtml;
    if (file.is_video) {
        mediaHtml = html`<video
            src="${fileUrl}"
            class="keurer-preview-media"
            controls
            autoplay
            playsinline
        ></video>`;
    } else {
        mediaHtml = html`<img
            src="${fileUrl}"
            class="keurer-preview-media"
            alt="${file.name}"
            draggable="false"
        >`;
    }

    const overlay = document.getElementById('keurer-preview-overlay');
    setHTML(document.getElementById('keurer-preview-content'), mediaHtml);
    document.getElementById('keurer-preview-filename').textContent = file.name;
    overlay.classList.add('active');
    keurerPreviewOpen = true;
}

function keurerClosePreview() {
    const overlay = document.getElementById('keurer-preview-overlay');
    overlay.classList.remove('active');

    // Stop video als die speelt
    const video = overlay.querySelector('video');
    if (video) { video.pause(); video.src = ''; }

    document.getElementById('keurer-preview-content').innerHTML = '';
    keurerPreviewOpen = false;
}

// ==================== SWIPE / SLEEP ====================

function keurerSetupDrag(card) {
    if (!card) return;

    let startX = 0, startY = 0;
    let currentX = 0, currentY = 0;
    let dragging = false;

    function onStart(x, y) {
        if (keurerIsAnimating) return;
        dragging = true;
        startX   = x;  startY   = y;
        currentX = 0;  currentY = 0;
        card.style.transition = 'none';
    }

    function onMove(x, y) {
        if (!dragging) return;
        currentX = x - startX;
        currentY = y - startY;

        // Bepaal dominante richting
        const absX = Math.abs(currentX);
        const absY = Math.abs(currentY);

        if (absY > absX && currentY < -40) {
            // Omhoog-swipe dominant → verplaatsen
            const upProgress = Math.min(1, (-currentY - 40) / 100);
            card.style.transform = `translateY(${currentY * 0.6}px) scale(${1 - upProgress * 0.05})`;

            const olMove   = document.getElementById('kc-overlay-move');
            const olKeep   = document.getElementById('kc-overlay-keep');
            const olDelete = document.getElementById('kc-overlay-delete');
            const olUndo   = document.getElementById('kc-overlay-undo');
            if (olMove)   olMove.style.opacity   = Math.min(0.92, upProgress).toString();
            if (olKeep)   olKeep.style.opacity   = '0';
            if (olDelete) olDelete.style.opacity = '0';
            if (olUndo)   olUndo.style.opacity   = '0';
        } else if (absY > absX && currentY > 40 && keurerHistory.length > 0) {
            // Omlaag-swipe dominant → undo
            const downProgress = Math.min(1, (currentY - 40) / 100);
            card.style.transform = `translateY(${currentY * 0.6}px) scale(${1 - downProgress * 0.05})`;

            const olUndo   = document.getElementById('kc-overlay-undo');
            const olKeep   = document.getElementById('kc-overlay-keep');
            const olDelete = document.getElementById('kc-overlay-delete');
            const olMove   = document.getElementById('kc-overlay-move');
            if (olUndo)   olUndo.style.opacity   = Math.min(0.92, downProgress).toString();
            if (olKeep)   olKeep.style.opacity   = '0';
            if (olDelete) olDelete.style.opacity = '0';
            if (olMove)   olMove.style.opacity   = '0';
        } else {
            // Links/rechts dominant
            const rotate = currentX * 0.07;
            card.style.transform = `translateX(${currentX}px) rotate(${rotate}deg)`;

            const threshold = 60;
            const olKeep   = document.getElementById('kc-overlay-keep');
            const olDelete = document.getElementById('kc-overlay-delete');
            const olMove   = document.getElementById('kc-overlay-move');
            if (olMove) olMove.style.opacity = '0';

            if (currentX < -threshold) {
                const opacity = Math.min(0.92, (-currentX - threshold) / 80);
                if (olKeep)   olKeep.style.opacity   = opacity.toString();
                if (olDelete) olDelete.style.opacity = '0';
            } else if (currentX > threshold) {
                const opacity = Math.min(0.92, (currentX - threshold) / 80);
                if (olDelete) olDelete.style.opacity = opacity.toString();
                if (olKeep)   olKeep.style.opacity   = '0';
            } else {
                if (olKeep)   olKeep.style.opacity   = '0';
                if (olDelete) olDelete.style.opacity = '0';
            }
        }
    }

    function onEnd() {
        if (!dragging) return;
        dragging = false;

        const absX = Math.abs(currentX);
        const absY = Math.abs(currentY);

        if (absY > absX && currentY < -100) {
            // Omhoog-swipe → verplaatsen
            keurerAnimateUp();
        } else if (absY > absX && currentY > 100 && keurerHistory.length > 0) {
            // Omlaag-swipe → undo
            keurerUndo();
        } else if (currentX < -100) {
            keurerAnimateAndDecide('keep');
        } else if (currentX > 100) {
            keurerAnimateAndDecide('delete');
        } else {
            // Terugveren
            card.style.transition = 'transform 0.32s cubic-bezier(0.34,1.56,0.64,1)';
            card.style.transform  = 'translateX(0) translateY(0) rotate(0) scale(1)';
            ['kc-overlay-keep', 'kc-overlay-delete', 'kc-overlay-move', 'kc-overlay-undo'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.style.opacity = '0';
            });
        }
    }

    // Muis: de window-luisteraars bestaan alleen tijdens het slepen. Zo blijft er per
    // beoordeelde kaart niets achter (geen stapel luisteraars, geen vastgehouden kaarten).
    function onMouseMove(e) { onMove(e.clientX, e.clientY); }
    function onMouseUp() {
        window.removeEventListener('mousemove', onMouseMove);
        window.removeEventListener('mouseup', onMouseUp);
        onEnd();
    }
    card.addEventListener('mousedown', e => {
        e.preventDefault();
        onStart(e.clientX, e.clientY);
        if (!dragging) return;
        window.addEventListener('mousemove', onMouseMove);
        window.addEventListener('mouseup', onMouseUp);
    });

    // Touch
    card.addEventListener('touchstart', e => onStart(e.touches[0].clientX, e.touches[0].clientY), { passive: true });
    card.addEventListener('touchmove',  e => { if (dragging) onMove(e.touches[0].clientX, e.touches[0].clientY); }, { passive: true });
    card.addEventListener('touchend',   onEnd);
}

// ==================== TOETSENBORD ====================

// Mogen de Keurer-toetsen (pijltjes, spatie) nu iets doen? Alleen als de toets voor de Keurer
// bedoeld is: niet tijdens typen, niet in het themamenu of een ander menu of venster, en niet als
// een ander deel van het scherm de toets al afhandelde. Een pijltje beslist over een bestand en
// verplaatst het, dus bij twijfel: niets doen.
function keurerKeyIsForKeurer(e, key) {
    if (e.defaultPrevented) return false;
    const themeMenu = document.getElementById('theme-dropdown');
    if (themeMenu && themeMenu.classList.contains('open')) return false;
    const el = document.activeElement;
    if (isTypingTarget(e.target) || isTypingTarget(el)) return false;
    if (!el || el === document.body || el === document.documentElement) return true;
    if (el.closest('#section-keurer, #keurer-preview-overlay')) {
        // Spatie op een knop (Houden, Kies map, ...) drukt die knop in; geen voorbeeld openen
        if (key === 'Space' && el !== document.getElementById('keurer-card')
            && el.closest('button, a[href], summary, [role="button"]')) return false;
        return true;
    }
    // Net op het Keurer-tabblad geklikt: de focus staat in het hoofdmenu. Alleen dat tabblad
    // telt; een pijltje op een ander tabblad (Tijdlijn, ...) is geen beslissing over een foto.
    const tab = el.closest('.nav-tabs [data-view]');
    if (tab) return tab.dataset.view === 'keurer';
    return false;
}

document.addEventListener('keydown', function (e) {
    const section = document.getElementById('section-keurer');
    if (!section || !section.classList.contains('active')) return;
    // Het sneltoetsenoverzicht (?) ligt erboven: geen beslissing achter het venster
    if (document.querySelector('.dv-overlay.active')) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    const moveModalActive = document.getElementById('modal-keurer-move').classList.contains('active');

    // Enter in move-modal: bevestig verplaatsen, maar alleen vanuit het zoekveld, de mappenboom
    // of het venster zelf. Enter op een knop (Annuleren, ×) doet wat die knop doet.
    if (e.key === 'Enter' && moveModalActive) {
        const modal = document.getElementById('modal-keurer-move');
        const t = e.target instanceof Element ? e.target : null;
        const fromTree = t && (t === modal || t.id === 'keurer-move-search'
            || t.closest('#keurer-move-folder-tree'));
        if (!fromTree) return;
        e.preventDefault();
        if (keurerMoveSelectedFolder) keurerConfirmMove();
        return;
    }

    // Escape: sluit preview of move-modal
    if (e.key === 'Escape') {
        if (keurerPreviewOpen) { keurerClosePreview(); return; }
        if (moveModalActive)   { keurerCloseMoveModal(); return; }
    }

    // Andere toetsen: niet in vensters, niet tijdens typen, niet in menu's
    if (moveModalActive || document.querySelector('.modal.active')) return;
    const space = e.code === 'Space' || e.key === ' ';
    const arrow = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key);
    if (!space && !arrow) return;
    if (!keurerKeyIsForKeurer(e, space ? 'Space' : e.key)) return;

    // Spatie: preview open of dicht
    if (space) {
        e.preventDefault();
        keurerTogglePreview();
        return;
    }

    if (keurerPreviewOpen) return;
    if (e.key === 'ArrowLeft')        { e.preventDefault(); keurerDecideKeep(); }
    else if (e.key === 'ArrowRight')  { e.preventDefault(); keurerDecideDelete(); }
    else if (e.key === 'ArrowUp')     { e.preventDefault(); keurerDecideMove(); }
    else if (e.key === 'ArrowDown')   { e.preventDefault(); keurerUndo(); }
});

// ==================== BRON-MAP KIEZER ====================

async function keurerOpenFolderPicker() {
    const modal = document.getElementById('modal-keurer-folder');
    modal.classList.add('active');
    keurerSelectedFolderForModal = null;
    document.getElementById('btn-keurer-confirm-folder').disabled = true;

    const treeContainer = document.getElementById('keurer-folder-tree');
    treeContainer.innerHTML = '<div class="loading-spinner" style="margin:2rem auto;display:block;"></div>';

    const tree = (typeof folderTree !== 'undefined' && folderTree.length > 0)
        ? folderTree
        : await (async () => {
            try {
                const data = await fetchJSON(`${API_BASE}/api/folders`);
                return data.folders || [];
            } catch (_) { return []; }
        })();

    treeContainer.innerHTML = '';

    if (tree.length === 0) {
        treeContainer.innerHTML = '<p style="color:var(--danger);padding:1rem;">Mappen konden niet geladen worden.</p>';
        return;
    }

    renderFolderTree(treeContainer, tree, {
        onSelect: (path) => {
            keurerSelectedFolderForModal = path;
            document.getElementById('btn-keurer-confirm-folder').disabled = false;
        },
    });

    // Keyboard nav
    window._keurerFolderNav = activateFolderTreeModal({
        modalId: 'modal-keurer-folder',
        containerId: 'keurer-folder-tree',
        onSelect: (item) => { if (item.dataset.path) item.click(); },
        onConfirm: () => { if (keurerSelectedFolderForModal) keurerConfirmFolderSelection(); },
        onClose: keurerCloseFolderPicker,
    });
}

function keurerCloseFolderPicker() {
    document.getElementById('modal-keurer-folder').classList.remove('active');
    if (window._keurerFolderNav) window._keurerFolderNav.nav.resetIndex();
    keurerSelectedFolderForModal = null;
}

function keurerConfirmFolderSelection() {
    if (!keurerSelectedFolderForModal) return;
    keurerFolder = keurerSelectedFolderForModal;
    localStorage.setItem('keurer_folder', keurerFolder);

    const display = document.getElementById('keurer-folder-display');
    if (display) display.textContent = keurerShortenPath(keurerFolder);

    keurerCloseFolderPicker();
    keurerLoadFiles();
}

// ==================== HELPERS ====================

function keurerShortenPath(p) {
    const parts = p.split('/').filter(Boolean);
    if (parts.length <= 2) return '/' + parts.join('/');
    return `.../${parts.slice(-2).join('/')}`;
}

