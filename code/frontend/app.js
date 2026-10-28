// DataVault Dashboard - Main Application Logic (Apple-Style UI)

var API_BASE = '';

// ==================== STATE ====================

let currentFolder = '';
let currentMedia = [];
let filteredMedia = [];
let folderTree = [];
let expandedFolders = new Set();
let currentView = 'grid';
let currentSort = 'name';
let modalZoom = 1;
let selectedMoveFolder = null;  // For move file destination
let activeShow = null; // Current slideshow instance

// Pan/Drag state for zoomed images
let isPanning = false;
let panStart = { x: 0, y: 0 };
let panOffset = { x: 0, y: 0 };

// ==================== PREFETCH CACHE ====================
// Slaat Promises op voor background-prefetched API responses.
// Voordeel: ook als het laden nog bezig is, kun je de Promise direct awaiten.
const _prefetchCache = {};

function prefetchInBackground(url) {
    if (_prefetchCache[url]) return; // Al gestart
    _prefetchCache[url] = fetch(url)
        .then(res => res.ok ? res.json() : Promise.reject())
        .catch(() => { delete _prefetchCache[url]; return null; });
}

function scheduleIdleTask(fn) {
    if ('requestIdleCallback' in window) {
        requestIdleCallback(fn, { timeout: 30000 });
    } else {
        setTimeout(fn, 500);
    }
}

function scheduleBackgroundPrefetch() {
    // Stap 1 (na 1.5s): Spotify all-time — zwaarste call, meest gebruikt
    // Stap 2: Shows stories
    // Stap 3: Spotify per-jaar (nadat all-time klaar is en jaren bekend zijn)
    setTimeout(() => {
        scheduleIdleTask(() => {
            prefetchInBackground(`${API_BASE}/api/spotify/stats?year=all`);
            scheduleIdleTask(() => {
                prefetchInBackground(`${API_BASE}/api/shows/stories`);
                // Na all-time: prefetch alle beschikbare jaren
                const allTimePromise = _prefetchCache[`${API_BASE}/api/spotify/stats?year=all`];
                if (allTimePromise) {
                    allTimePromise.then(data => {
                        if (data && data.available_years) {
                            data.available_years.forEach(y => {
                                scheduleIdleTask(() => prefetchInBackground(`${API_BASE}/api/spotify/stats?year=${y}`));
                            });
                        }
                    });
                }
            });
        });
    }, 1500);
}

// ==================== THEME SYSTEM ====================

function initTheme() {
    const saved = localStorage.getItem('theme') || 'auto';
    setTheme(saved, true);
}

const THEME_LABELS = { auto: 'Auto', light: 'Licht', dark: 'Donker' };
const THEME_ORDER = ['auto', 'light', 'dark'];

function currentTheme() {
    let saved = null;
    try { saved = localStorage.getItem('theme'); } catch (_) { /* geen opslag */ }
    return THEME_ORDER.includes(saved) ? saved : 'auto';
}

function setTheme(theme, skipSave = false) {
    if (!THEME_ORDER.includes(theme)) theme = 'auto';
    if (!skipSave) {
        try { localStorage.setItem('theme', theme); } catch (_) { /* alleen voor deze sessie */ }
    }

    const html = document.documentElement;

    if (theme === 'auto') {
        html.removeAttribute('data-theme');
    } else {
        html.setAttribute('data-theme', theme);
    }

    document.getElementById('theme-label').textContent = THEME_LABELS[theme];

    // Welke keuze actief is (ook voor schermlezers), in de navigatie en in Instellingen
    document.querySelectorAll('.theme-option, [data-theme-choice]').forEach(opt => {
        const value = opt.dataset.theme || opt.dataset.themeChoice;
        opt.classList.toggle('active', value === theme);
        if (opt.matches('[role="menuitemradio"]')) opt.setAttribute('aria-checked', String(value === theme));
        if (opt.matches('input[type="radio"]')) opt.checked = value === theme;
    });

    closeThemeDropdown();
}

// Sneltoets t: Auto -> Licht -> Donker -> Auto
function cycleTheme() {
    const next = THEME_ORDER[(THEME_ORDER.indexOf(currentTheme()) + 1) % THEME_ORDER.length];
    setTheme(next);
    showToast('Thema', `Thema: ${THEME_LABELS[next]}`, 'info');
}

function toggleThemeDropdown() {
    const dropdown = document.getElementById('theme-dropdown');
    const open = !dropdown.classList.contains('open');
    dropdown.classList.toggle('open', open);
    document.getElementById('theme-button').setAttribute('aria-expanded', String(open));
    if (open) {
        // Met het toetsenbord geopend: meteen op de huidige keuze
        const active = dropdown.querySelector('.theme-option.active') || dropdown.querySelector('.theme-option');
        if (active) active.focus();
    }
}

function closeThemeDropdown(returnFocus = false) {
    const dropdown = document.getElementById('theme-dropdown');
    const wasOpen = dropdown.classList.contains('open');
    dropdown.classList.remove('open');
    const button = document.getElementById('theme-button');
    if (button) button.setAttribute('aria-expanded', 'false');
    if (wasOpen && (returnFocus || dropdown.contains(document.activeElement)) && button) button.focus();
}

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
    if (!e.target.closest('.theme-toggle')) {
        closeThemeDropdown();
    }
});

// In het themamenu: pijltjes kiezen, Escape sluit (focus terug op de knop)
document.addEventListener('keydown', (e) => {
    const dropdown = document.getElementById('theme-dropdown');
    if (!dropdown || !dropdown.classList.contains('open')) return;
    const options = [...dropdown.querySelectorAll('.theme-option')];
    const at = options.indexOf(document.activeElement);
    if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        closeThemeDropdown(true);
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'ArrowLeft' || e.key === 'ArrowRight'
        || e.key === 'Home' || e.key === 'End') {
        // Deze toetsen zijn voor het menu: niet ook voor de Keurer, het raster of de viewer erachter
        e.preventDefault();
        e.stopPropagation();
        if (e.key === 'Home' || e.key === 'End') {
            options[e.key === 'Home' ? 0 : options.length - 1].focus();
            return;
        }
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') return;
        const step = e.key === 'ArrowDown' ? 1 : -1;
        options[(at + step + options.length) % options.length].focus();
    } else if (e.key === 'Tab') {
        closeThemeDropdown();
    }
}, true);

// ==================== VIEW SWITCHING ====================

const MAIN_VIEWS = ['home', 'viewer', 'duplicates', 'spotify', 'shows', 'keurer', 'timeline', 'recovery', 'settings'];

function currentMainView() {
    return MAIN_VIEWS.find(v => {
        const section = document.getElementById(`section-${v}`);
        return section && section.classList.contains('active');
    }) || 'home';
}

function switchMainView(view) {
    if (!MAIN_VIEWS.includes(view)) return;
    // Update tabs (ook de tandwielknop van Instellingen)
    document.querySelectorAll('.top-nav [data-view]').forEach(tab => {
        const on = tab.dataset.view === view;
        tab.classList.toggle('active', on);
        if (on) tab.setAttribute('aria-current', 'page'); else tab.removeAttribute('aria-current');
    });

    // Update sections
    MAIN_VIEWS.forEach(v => {
        const section = document.getElementById(`section-${v}`);
        if (!section) return;
        section.classList.toggle('active', v === view);
        section.classList.toggle('hidden', v !== view);
    });

    // Show/hide sidebar
    document.getElementById('sidebar').style.display = view === 'viewer' ? 'flex' : 'none';

    // Clear search
    document.getElementById('global-search').value = '';
    updateSearchFieldForView(view);

    if (view !== 'spotify') stopSpotifyClock();
    homeOtdCycle(view === 'home');

    // Load data based on view
    if (view === 'spotify') {
        loadSpotifyStats();
    } else if (view === 'shows') {
        // loadShows called via button or init?
        // Let's ensure it loads if empty
        const container = document.getElementById('shows-container');
        if (container.children.length === 0) {
            loadShows();
        }
    } else if (view === 'duplicates') {
        if (typeof duplicatesInit === 'function') duplicatesInit();
    } else if (view === 'keurer') {
        if (typeof keurerInit === 'function') keurerInit();
    } else if (view === 'timeline') {
        if (typeof timelineInit === 'function') timelineInit();
    } else if (view === 'recovery') {
        if (typeof recoveryInit === 'function') recoveryInit();
    } else if (view === 'settings') {
        if (typeof settingsInit === 'function') settingsInit();
    }
}

// ==================== SHOWS & STORIES ====================

async function loadShows() {
    const container = document.getElementById('shows-container');
    if (!container) return;

    const url = `${API_BASE}/api/shows/stories`;

    // Gebruik cache als beschikbaar (instant render, geen spinner)
    if (_prefetchCache[url]) {
        const data = await _prefetchCache[url];
        if (data) { renderShows(data); return; }
    }

    // Normale laadprocedure
    container.replaceChildren(skeletonCards(8, 'skeleton-grid-shows'));
    try {
        const stories = await fetchJSON(url);
        _prefetchCache[url] = Promise.resolve(stories);
        renderShows(stories);
    } catch (e) {
        console.error('Error loading shows:', e);
        container.replaceChildren(errorState('Shows laden mislukt', e, loadShows));
    }
}

async function reloadShows() {
    // Cache wissen zodat we vers data ophalen
    delete _prefetchCache[`${API_BASE}/api/shows/stories`];
    const btn = document.getElementById('btn-reload-shows');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-sync fa-spin"></i> Herladen…';
    }
    await loadShows();
}

// Store last loaded shows data for shuffle
let _lastShowsData = null;

function renderShows(data) {
    const container = document.getElementById('shows-container');
    if (!container) return;

    _lastShowsData = data; // Cache for shuffle
    container.innerHTML = '';

    // Data structure: { "featured": {...}, "shows": [...] }
    const { featured, shows } = data || {};

    if (!data || (!featured && (!shows || shows.length === 0))) {
        container.replaceChildren(emptyState({
            icon: 'fa-folder-open', title: 'Nog geen shows',
            text: 'Shows komen uit de mappen in de map library. Zet er een paar aan bij Bronnen beheren.',
            action: { label: 'Bronnen beheren', onClick: openSettingsModal, primary: true },
        }));
        return;
    }

    container.innerHTML = `
        <div class="page-header shows-header">
            <div>
                <h1 class="page-title"><i class="fas fa-film" aria-hidden="true"></i> Shows</h1>
                <p class="page-subtitle">Diavoorstellingen van je mappen, met je mooiste foto's vaker in beeld.</p>
            </div>
            <div class="shows-header-actions">
            <button class="btn btn-secondary" id="btn-shuffle-shows" onclick="shuffleShows()">
                <i class="fas fa-random" aria-hidden="true"></i> Shows husselen
            </button>
            <button class="btn btn-secondary" onclick="openSettingsModal()">
                <i class="fas fa-sliders-h" aria-hidden="true"></i> Bronnen beheren
            </button>
            </div>
        </div>
    `;

    // 1. Hero Section (Featured - Random from Vault)
    if (featured && featured.files && featured.files.length > 0) {
        const hero = document.createElement('div');
        hero.className = 'shows-hero';
        const cover = featured.files[0];
        hero.onclick = () => startShow(featured);
        makeActivatable(hero, `Afspelen: ${featured.title}`);

        hero.innerHTML = html`
            <img src="${vaultFileUrl('thumb', cover.path)}" class="shows-hero-img" alt="">
            <div class="shows-hero-content">
                <span class="shows-hero-tag"><i class="fas fa-wand-magic-sparkles" aria-hidden="true"></i> Uitgelicht</span>
                <h2 class="shows-hero-title">${featured.title}</h2>
                <p class="shows-hero-subtitle">${featured.subtitle}</p>
                <button class="btn btn-primary btn-lg" tabindex="-1">
                    <i class="fas fa-play" aria-hidden="true"></i> Afspelen
                </button>
            </div>
        `;
        container.appendChild(hero);
    }

    // 2. Folder Shows Grid
    if (shows && shows.length > 0) {
        const gridTitle = document.createElement('h3');
        gridTitle.className = 'section-title';
        gridTitle.textContent = 'Collecties';
        container.appendChild(gridTitle);

        const grid = document.createElement('div');
        grid.className = 'shows-grid-folders';

        shows.forEach(show => {
            const card = document.createElement('div');
            card.className = 'show-card';
            card.onclick = () => playFolderShow(show);
            makeActivatable(card, `Afspelen: ${show.title}`);

            let thumbUrl = '';
            if (show.preview) {
                thumbUrl = vaultFileUrl('thumb', show.preview.path);
            }

            // Full thumbnail with transparent overlay + text shadow for readability
            card.innerHTML = html`
                ${thumbUrl ? html`<img src="${thumbUrl}" class="show-card-thumb" alt="" loading="lazy">` : html`<div class="show-card-empty"><i class="fas fa-images" aria-hidden="true"></i></div>`}
                <div class="show-card-caption">
                    <h4 class="show-card-title">${show.title}</h4>
                    <span class="show-card-meta">Map</span>
                </div>
            `;

            grid.appendChild(card);
        });

        container.appendChild(grid);
    }
}

function shuffleShows() {
    if (!_lastShowsData || !_lastShowsData.shows || _lastShowsData.shows.length < 2) return;

    // Fisher-Yates shuffle
    const arr = _lastShowsData.shows;
    for (let i = arr.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [arr[i], arr[j]] = [arr[j], arr[i]];
    }

    // Visual feedback on button
    const btn = document.getElementById('btn-shuffle-shows');
    if (btn) {
        btn.innerHTML = '<i class="fas fa-check"></i> Gehusseld!';
        setTimeout(() => {
            btn.innerHTML = '<i class="fas fa-random"></i> Shows husselen';
        }, 800);
    }

    renderShows(_lastShowsData);
}

async function playFolderShow(showObj) {
    try {
        // Fetch files for this folder
        const files = await fetchJSON(`${API_BASE}/api/shows/folder-content?path=${encodeURIComponent(showObj.path)}`);

        if (files && files.length > 0) {
            startShow({
                title: showObj.title,
                files: files
            });
        } else {
            showToast('Empty', 'No media found in this folder', 'warning');
        }
    } catch (e) {
        toastFetchError('Show starten mislukt', e);
    }
}

// ==================== FOLDER TREE ====================

async function loadFolders() {
    try {
        const data = await fetchJSON(`${API_BASE}/api/folders`);
        folderTree = data.folders;

        // Load expanded state from localStorage
        const savedExpanded = localStorage.getItem('expandedFolders');
        if (savedExpanded) {
            expandedFolders = new Set(JSON.parse(savedExpanded));
        }

        renderSidebarFolderTree();
        // Nog geen map gekozen: Bestanden begint bij de bovenste mappen van het archief
        if (!currentFolder) showArchiveRoot();
    } catch (error) {
        toastFetchError('Mappen laden mislukt', error);
    }
}

function renderSidebarFolderTree() {
    const container = document.getElementById('folder-tree');
    if (!container) return;
    renderFolderTree(container, folderTree, {
        itemClass: 'folder-item',
        addDepthClass: true,
        indent: 20,
        indentBase: 0,
        expandedPaths: expandedFolders,
        selectedPath: currentFolder,
        iconMode: 'fontawesome',
        separateClicks: true,
        onSelect: (path) => selectFolder(path),
        onToggle: () => localStorage.setItem('expandedFolders', JSON.stringify([...expandedFolders])),
    });
}

function expandAllFolders() {
    function addAllPaths(folders) {
        folders.forEach(folder => {
            if (folder.children && folder.children.length > 0) {
                expandedFolders.add(folder.path);
                addAllPaths(folder.children);
            }
        });
    }

    addAllPaths(folderTree);
    localStorage.setItem('expandedFolders', JSON.stringify([...expandedFolders]));
    renderSidebarFolderTree();
}

function collapseAllFolders() {
    expandedFolders.clear();
    localStorage.setItem('expandedFolders', JSON.stringify([]));
    renderSidebarFolderTree();
}

async function selectFolder(path) {
    currentFolder = path;
    await loadMedia(path);
    revealFolderInSidebar(path);
    updateBreadcrumb(path);
}

// De boom links volgt de gekozen map, ook als je via het raster of het kruimelpad gaat: de
// bovenliggende mappen klappen open en de map zelf staat gemarkeerd in beeld
function revealFolderInSidebar(path) {
    let opened = false;
    (function walk(folders) {
        for (const folder of folders || []) {
            if (path.startsWith(folder.path + '/')) {
                if (!expandedFolders.has(folder.path)) {
                    expandedFolders.add(folder.path);
                    opened = true;
                }
                walk(folder.children);
                return;
            }
        }
    })(folderTree);
    if (opened) localStorage.setItem('expandedFolders', JSON.stringify([...expandedFolders]));
    renderSidebarFolderTree();
    const selected = document.querySelector('#folder-tree .folder-item.selected');
    if (selected) selected.scrollIntoView({ block: 'nearest' });
}

// Het beginpunt van Bestanden: de bovenste mappen van het archief als mapkaarten. 'Archief'
// in het kruimelpad en Backspace in een bovenste map komen hier terug.
function showArchiveRoot() {
    ++mediaLoadSeq;  // een map die nog laadt, tekent er niet meer overheen
    currentFolder = '';
    currentMedia = (folderTree || []).map(f => ({ type: 'folder', name: f.name, path: f.path }));
    filteredMedia = [...currentMedia];
    gridFocusIndex = 0;
    const query = document.getElementById('global-search');
    if (query && query.value.trim()) handleGlobalSearch(query.value);
    else applySortAndRender();
    renderSidebarFolderTree();
    updateBreadcrumb('');
}

// ==================== BREADCRUMB ====================

// Een pad zoals de gebruiker het kent: vanaf het archief ('RAW/2019/zomer/foto.jpg'), niet het
// hele pad op de schijf. Het volledige pad blijft bereikbaar (Kopieer, title).
function archivePath(path) {
    const root = (folderTree || []).find(f => path === f.path || path.startsWith(f.path + '/'));
    if (!root) return path;
    return path.slice(root.path.lastIndexOf('/') + 1);
}

// Tekstknopen met een afbreekpunt na elke '/': een lang pad breekt tussen mappen, niet midden
// in een naam
function pathNodes(text) {
    const nodes = [];
    String(text).split('/').forEach((part, i) => {
        if (i > 0) nodes.push('/', createEl('wbr'));
        if (part) nodes.push(part);
    });
    return nodes;
}

function updateBreadcrumb(path) {
    const breadcrumb = document.getElementById('breadcrumb');
    breadcrumb.innerHTML = '';

    // Vanaf de archiefmap tonen (RAW / 2019 / zomer), niet het hele pad op de schijf
    const root = folderTree.find(f => path === f.path || path.startsWith(f.path + '/'));
    const base = root ? root.path.slice(0, root.path.lastIndexOf('/')) : '';
    const parts = path.slice(base.length).split('/').filter(p => p);

    if (parts.length === 0) {
        breadcrumb.replaceChildren(createEl('span', { class: 'breadcrumb-item active', text: 'Archief' }));
        return;
    }

    // 'Archief' gaat terug naar de bovenste mappen
    if (base) {
        breadcrumb.appendChild(createEl('a', {
            class: 'breadcrumb-item breadcrumb-root', href: '#', text: 'Archief',
            title: 'Naar de bovenste mappen van het archief',
            onClick: (e) => { e.preventDefault(); showArchiveRoot(); },
        }));
    }

    let currentPath = base;
    parts.forEach((part, index) => {
        currentPath += '/' + part;

        const isLast = index === parts.length - 1;

        if (index > 0 || base) {
            breadcrumb.appendChild(createEl('i', { class: 'fas fa-chevron-right breadcrumb-separator', 'aria-hidden': 'true' }));
        }

        const item = document.createElement(isLast ? 'span' : 'a');
        item.className = 'breadcrumb-item' + (isLast ? ' active' : '');
        item.textContent = part;

        if (!isLast) {
            const pathToNavigate = currentPath;
            item.href = '#';
            item.onclick = (e) => {
                e.preventDefault();
                selectFolder(pathToNavigate);
            };
        }

        breadcrumb.appendChild(item);
    });
}

// ==================== MEDIA LOADING ====================

let mediaLoadSeq = 0;   // alleen het antwoord van de laatst gekozen map telt

async function loadMedia(folder) {
    const seq = ++mediaLoadSeq;
    const grid = document.getElementById('media-grid');
    // Plaatshouders als het even duurt (een grote map, een trage schijf), niet bij elke klik
    const slow = setTimeout(() => {
        if (seq === mediaLoadSeq) grid.replaceChildren(skeletonCards(12));
    }, 150);
    try {
        const data = await fetchJSON(`${API_BASE}/api/media?folder=${encodeURIComponent(folder)}`);
        if (seq !== mediaLoadSeq) return;
        currentMedia = data.files;
        filteredMedia = [...currentMedia];
        gridFocusIndex = 0;

        applySortAndRender();
    } catch (error) {
        if (seq !== mediaLoadSeq) return;
        currentMedia = [];
        filteredMedia = [];
        gridItems = [];
        grid.replaceChildren(errorState('Deze map kon niet geladen worden', error, () => loadMedia(folder)));
        toastFetchError('Bestanden laden mislukt', error);
    } finally {
        clearTimeout(slow);
    }
}

function applySortAndRender() {
    sortMedia(currentSort);
    renderMediaGrid();
}

function sortMedia(criteria) {
    currentSort = criteria;

    filteredMedia.sort((a, b) => {
        switch (criteria) {
            case 'name':
                return a.name.localeCompare(b.name);
            case 'date':
                return new Date(b.modified) - new Date(a.modified);
            case 'size':
                return b.size - a.size;
            default:
                return 0;
        }
    });
}

function handleSort(value) {
    currentSort = value;
    applySortAndRender();
}

function toggleView(view) {
    currentView = view;

    document.querySelectorAll('.view-toggle-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === view);
        btn.setAttribute('aria-pressed', String(btn.dataset.view === view));
    });

    const grid = document.getElementById('media-grid');
    grid.classList.toggle('list-view', view === 'list');
    grid.classList.toggle('grid-view', view === 'grid');

    renderMediaGrid();
}

// ==================== MEDIA GRID RENDERING ====================
// Een map kan duizenden bestanden hebben. De kaarten worden in stukken van GRID_CHUNK
// getekend: het volgende stuk pas als je in de buurt van het einde scrolt (een sentinel
// onder het raster, zoals in duplicates.js). Elke miniatuur laadt bovendien lazy, dus de
// server maakt alleen miniaturen voor wat je (bijna) ziet, niet voor de hele map.

const GRID_CHUNK = 60;
let gridItems = [];        // mappen eerst, dan bestanden: de volgorde van het raster
let gridRendered = 0;      // zoveel kaarten van gridItems staan er nu
let gridObserver = null;

// keepCount: na verplaatsen of verversen evenveel kaarten tonen als ervoor, zodat de
// scrollpositie niet verspringt
function renderMediaGrid(keepCount = false) {
    const grid = document.getElementById('media-grid');
    const previous = keepCount ? gridRendered : 0;
    if (gridObserver) {
        gridObserver.disconnect();
        gridObserver = null;
    }
    grid.innerHTML = '';
    gridRendered = 0;

    if (filteredMedia.length === 0) {
        gridItems = [];
        const searching = document.getElementById('global-search').value.trim();
        grid.replaceChildren(searching
            ? emptyState({
                icon: 'fa-search', title: 'Niets gevonden', text: `Geen bestand in deze map heeft "${searching}" in de naam.`,
                action: { label: 'Zoekopdracht wissen', onClick: () => {
                    const input = document.getElementById('global-search');
                    input.value = '';
                    handleGlobalSearch('');
                    input.focus();
                } },
            })
            : currentFolder
                ? emptyState({ icon: 'fa-folder-open', title: 'Deze map is leeg', text: 'Er staan hier geen bestanden of submappen.' })
                : emptyState({ icon: 'fa-folder-tree', title: 'Kies een map', text: 'Kies links een map om de bestanden te zien.' }));
        return;
    }

    // Mappen eerst, dan de bestanden
    gridItems = filteredMedia.filter(item => item.type === 'folder')
        .concat(filteredMedia.filter(item => item.type !== 'folder'));
    if (gridFocusIndex >= gridItems.length) gridFocusIndex = 0;
    renderMoreGridCards(Math.max(GRID_CHUNK, previous));
    if (!grid.querySelector('[data-grid-index][tabindex="0"]')) {
        const first = grid.querySelector('[data-grid-index]');
        if (first) first.tabIndex = 0;
    }
    observeGridEnd(grid);
}

function renderMoreGridCards(count = GRID_CHUNK) {
    const grid = document.getElementById('media-grid');
    const end = Math.min(gridItems.length, gridRendered + count);
    const fragment = document.createDocumentFragment();
    for (let i = gridRendered; i < end; i++) {
        const item = gridItems[i];
        const card = item.type === 'folder' ? createFolderCard(item) : createMediaCard(item);
        // Eén kaart is met Tab bereikbaar (de laatst gekozen); de pijltjes gaan naar de rest
        card.dataset.gridIndex = String(i);
        card.tabIndex = i === gridFocusIndex ? 0 : -1;
        fragment.appendChild(card);
    }
    gridRendered = end;
    grid.appendChild(fragment);
}

// ==================== RASTER MET HET TOETSENBORD ====================
// Pijltjes bewegen door de kaarten (ook naar kaarten die nog getekend moeten worden),
// Home/End naar het begin of eind, Enter of spatie opent (een map of de viewer) en
// Backspace gaat een map omhoog.

let gridFocusIndex = 0;

function gridColumns(grid) {
    const cards = grid.querySelectorAll('.media-card');
    if (cards.length < 2) return 1;
    const top = cards[0].offsetTop;
    let cols = 0;
    for (const card of cards) {
        if (card.offsetTop !== top) break;
        cols++;
    }
    return Math.max(1, cols);
}

function focusGridCard(index) {
    if (!gridItems.length) return;
    index = Math.max(0, Math.min(gridItems.length - 1, index));
    if (index >= gridRendered) renderMoreGridCards(index - gridRendered + GRID_CHUNK);
    const grid = document.getElementById('media-grid');
    const card = grid.querySelector(`[data-grid-index="${index}"]`);
    if (!card) return;
    const previous = grid.querySelector('[data-grid-index][tabindex="0"]');
    if (previous && previous !== card) previous.tabIndex = -1;
    card.tabIndex = 0;
    gridFocusIndex = index;
    card.focus({ preventScroll: true });
    card.scrollIntoView({ block: 'nearest' });
}

function parentFolder(path) {
    const at = (path || '').lastIndexOf('/');
    return at > 0 ? path.slice(0, at) : '';
}

document.addEventListener('DOMContentLoaded', () => {
    const grid = document.getElementById('media-grid');
    grid.addEventListener('keydown', (e) => {
        const card = e.target.closest('[data-grid-index]');
        if (!card || e.altKey || e.ctrlKey || e.metaKey) return;
        const index = Number(card.dataset.gridIndex);
        const cols = gridColumns(grid);
        const rows = Math.max(1, Math.floor(window.innerHeight / Math.max(1, card.offsetHeight)) - 1);
        const moves = {
            ArrowRight: index + 1, ArrowLeft: index - 1,
            ArrowDown: index + cols, ArrowUp: index - cols,
            Home: 0, End: gridItems.length - 1,
            PageDown: index + cols * rows, PageUp: index - cols * rows,
        };
        if (e.key in moves) {
            e.preventDefault();
            focusGridCard(moves[e.key]);
        } else if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            card.click();
        } else if (e.key === 'Backspace' && currentFolder) {
            const parent = parentFolder(currentFolder);
            const root = folderTree.length ? parentFolder(folderTree[0].path) : '';
            if (parent && parent.length > root.length) {
                e.preventDefault();
                selectFolder(parent);
            } else if (folderTree.some(f => f.path === currentFolder)) {
                // Uit een bovenste map terug naar het archief zelf
                e.preventDefault();
                showArchiveRoot();
            }
        }
    });
    // Klikken of Tab op een kaart maakt hem de kaart van waaruit de pijltjes verdergaan
    grid.addEventListener('focusin', (e) => {
        const card = e.target.closest('[data-grid-index]');
        if (!card) return;
        const previous = grid.querySelector('[data-grid-index][tabindex="0"]');
        if (previous && previous !== card) previous.tabIndex = -1;
        card.tabIndex = 0;
        gridFocusIndex = Number(card.dataset.gridIndex);
    });
});

function observeGridEnd(grid) {
    let sentinel = document.getElementById('media-grid-sentinel');
    if (!sentinel) {
        sentinel = createEl('div', { id: 'media-grid-sentinel', 'aria-hidden': 'true' });
        grid.after(sentinel);
    }
    if (gridRendered >= gridItems.length) return;
    if (!('IntersectionObserver' in window)) {
        renderMoreGridCards(gridItems.length);
        return;
    }
    gridObserver = new IntersectionObserver(entries => {
        if (!entries.some(e => e.isIntersecting)) return;
        renderMoreGridCards();
        if (gridRendered >= gridItems.length) {
            gridObserver.disconnect();
            gridObserver = null;
            return;
        }
        // Blijft de sentinel in beeld (groot scherm), dan meteen nog een stuk
        gridObserver.unobserve(sentinel);
        gridObserver.observe(sentinel);
    }, { rootMargin: '600px 0px' });   // de pagina zelf scrolt (de viewport is de root)
    gridObserver.observe(sentinel);
}

function createFolderCard(folder) {
    const card = document.createElement('div');
    card.className = 'media-card thumb-tile folder-card';
    card.onclick = () => selectFolder(folder.path);
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `Map ${folder.name}`);

    // Add filename for list view
    card.setAttribute('data-filename', folder.name);

    // Icoon (Font Awesome): een map, of het soort bestand
    const icon = document.createElement('div');
    icon.className = 'folder-card-icon';
    icon.setAttribute('aria-hidden', 'true');

    let iconClass = 'fa-folder';
    if (folder.type !== 'folder') {
        const name = folder.name.toLowerCase();
        const ext = name.substring(name.lastIndexOf('.'));
        if (['.pdf'].includes(ext)) iconClass = 'fa-file-pdf';
        else if (['.doc', '.docx'].includes(ext)) iconClass = 'fa-file-word';
        else if (['.xls', '.xlsx', '.csv'].includes(ext)) iconClass = 'fa-file-excel';
        else if (['.ppt', '.pptx'].includes(ext)) iconClass = 'fa-file-powerpoint';
        else if (['.txt', '.md', '.log'].includes(ext)) iconClass = 'fa-file-lines';
        else if (['.zip', '.rar', '.7z', '.tar', '.gz'].includes(ext)) iconClass = 'fa-file-zipper';
        else if (['.mp3', '.wav', '.flac', '.m4a', '.aac'].includes(ext)) iconClass = 'fa-file-audio';
        else if (['.js', '.py', '.java', '.cpp', '.c', '.h', '.html', '.css', '.json', '.xml', '.yaml', '.yml', '.toml', '.ini'].includes(ext)) iconClass = 'fa-file-code';
        else iconClass = 'fa-file';
        icon.classList.add('is-file');
    }
    icon.appendChild(createEl('i', { class: `fas ${iconClass}` }));
    card.appendChild(icon);

    // Folder name
    const name = document.createElement('div');
    name.className = 'folder-card-name';
    name.textContent = folder.name;
    card.appendChild(name);
    card.appendChild(createListMeta(folder));

    return card;
}

// Soort, grootte en datum: alleen zichtbaar in de lijstweergave, als rustige kolommen
const MEDIA_KIND_LABELS = { photo: 'Foto', video: 'Video', audio: 'Audio', folder: 'Map', other: 'Bestand' };

function createListMeta(file) {
    const date = file.modified
        ? new Date(file.modified * 1000).toLocaleDateString('nl-NL', { day: 'numeric', month: 'short', year: 'numeric' })
        : '';
    return createEl('div', { class: 'media-card-meta' },
        createEl('span', { class: 'media-card-kind', text: MEDIA_KIND_LABELS[file.type] || 'Bestand' }),
        createEl('span', { class: 'media-card-size', text: file.type === 'folder' ? '' : formatBytes(file.size || 0) }),
        createEl('span', { class: 'media-card-date', text: date }));
}

function createMediaCard(file) {
    const card = document.createElement('div');
    card.className = 'media-card thumb-tile';
    card.onclick = () => openMediaModal(file);
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', file.name);

    // Thumbnail
    const img = document.createElement('img');
    img.className = 'media-thumbnail loading';
    img.alt = file.name;

    // Thumbnail URL logic
    let thumbUrl = `${API_BASE}/api/thumb?path=${encodeURIComponent(file.path)}`;

    if (file.type === 'other') {
        // Geen foto, video of audio (pdf, zip, ...): een bestandsicoon, nooit een miniatuur
        card.classList.add('other-card');
        card.appendChild(createEl('div', { class: 'other-card-icon' }, createEl('i', { class: 'fas fa-file' })));
        card.appendChild(createEl('div', { class: 'media-card-name', text: file.name }));
        card.appendChild(createListMeta(file));
        return card;
    } else if (file.type === 'audio') {
        // Deterministic hash based on filename for consistent variations (1-10)
        let hash = 0;
        for (let i = 0; i < file.name.length; i++) {
            hash = file.name.charCodeAt(i) + ((hash << 5) - hash);
        }
        const variant = Math.abs(hash) % 15 + 1;
        thumbUrl = `assets/audio_thumbs/audio_thumb_var${variant}.png`;
    } else if (file.name.toLowerCase().endsWith('.avi')) {
        thumbUrl = 'assets/audio_thumbs/avi_thumb.png';
    }

    // Remove loading class when loaded
    img.addEventListener('load', () => img.classList.remove('loading'));

    // Show broken image state on error
    img.addEventListener('error', () => {
        img.classList.remove('loading');
        img.style.backgroundColor = 'var(--surface-raised)';
    });

    // Pas laden als de kaart (bijna) in beeld komt; lazy moet vóór src gezet worden
    img.loading = 'lazy';
    img.decoding = 'async';
    img.src = thumbUrl;

    card.appendChild(img);

    // Audio names directly on thumbnail for Grid View
    if (file.type === 'audio') {
        card.classList.add('audio-card');
        const audioName = document.createElement('div');
        audioName.className = 'audio-card-name';

        let rawName = file.name;
        const lastDot = rawName.lastIndexOf('.');
        if (lastDot > 0) rawName = rawName.substring(0, lastDot);

        audioName.textContent = rawName;
        card.appendChild(audioName);
    }

    // Filename (visible in list view)
    const nameSpan = document.createElement('div');
    nameSpan.className = 'media-card-name';
    nameSpan.textContent = file.name;
    card.appendChild(nameSpan);
    card.appendChild(createListMeta(file));

    // Info icon (bottom-right corner, shown on hover/grid view)
    const infoIcon = document.createElement('div');
    infoIcon.className = 'media-info-icon';
    infoIcon.textContent = 'i';
    infoIcon.title = file.name;
    infoIcon.setAttribute('aria-hidden', 'true');
    infoIcon.onclick = (e) => {
        e.stopPropagation();
        openMediaModal(file);
    };

    card.appendChild(infoIcon);

    return card;
}

// Grootte leesbaar en met een Nederlandse komma: 812 B, 4,7 KB, 38 MB, 1,25 GB. Nooit "0,00 GB"
// voor iets dat wel ruimte inneemt: de eenheid schaalt mee.
function formatBytes(bytes) {
    if (typeof bytes !== 'number' || !isFinite(bytes) || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    const value = bytes / Math.pow(1024, i);
    const digits = i === 0 ? 0 : value < 10 ? (i >= 3 ? 2 : 1) : value < 100 ? 1 : 0;
    return `${value.toLocaleString('nl-NL', { maximumFractionDigits: digits })} ${units[i]}`;
}

// Getallen altijd op z'n Nederlands: 1.234 en 1,2 (nooit 1.2 voor anderhalf)
function formatNumberNl(value, digits = 0) {
    const n = Number(value);
    return isFinite(n) ? n.toLocaleString('nl-NL', { maximumFractionDigits: digits }) : '–';
}

// Een datum van de server (JJJJ-MM-DD) als '11 feb 2023'; iets anders blijft zoals het is
function formatDateNl(isoDate) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(isoDate || ''));
    if (!m) return String(isoDate || '');
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
        .toLocaleDateString('nl-NL', { day: 'numeric', month: 'short', year: 'numeric' });
}

// ==================== SEARCH/FILTER ====================

// Zoektekst zonder verschil in hoofdletters of Unicode-vorm: een naam die Finder als
// 'e' + accentteken opsloeg, wordt ook gevonden met een 'é' van het toetsenbord
function searchKey(text) {
    return String(text || '').normalize('NFC').toLowerCase().trim();
}

// Tijdens het typen pas na een korte pauze filteren: bij duizenden bestanden zou elke
// toetsaanslag het hele raster opnieuw opbouwen
const SEARCH_DEBOUNCE_MS = 200;
let searchTimer = null;

function queueGlobalSearch(query) {
    clearTimeout(searchTimer);
    // Het zoekveld zoekt (nog) alleen in de map die in Bestanden open is. Op een ander scherm
    // filtert typen niets weg dat je niet ziet: Enter opent Bestanden en zoekt daar.
    if (currentMainView() !== 'viewer') return;
    searchTimer = setTimeout(() => handleGlobalSearch(query), SEARCH_DEBOUNCE_MS);
}

function globalSearchKeydown(event) {
    const input = event.currentTarget;
    if (event.key !== 'Enter' || currentMainView() === 'viewer' || !input.value.trim()) return;
    event.preventDefault();
    const query = input.value;
    switchMainView('viewer');
    input.value = query;
    handleGlobalSearch(query);
}

// Wat het zoekveld doet, zichtbaar in de plaatshouder en (op andere schermen) de Enter-hint
function updateSearchFieldForView(view) {
    const input = document.getElementById('global-search');
    if (!input) return;
    const here = view === 'viewer';
    input.placeholder = here ? 'Zoek in deze map…' : 'Zoek in Bestanden…';
    input.setAttribute('aria-label', here ? 'Zoek in de geopende map (sneltoets /)'
        : 'Zoek in de geopende map van Bestanden: typ en druk op Enter (sneltoets /)');
    input.closest('.search-field').classList.toggle('search-elsewhere', !here);
}

function handleGlobalSearch(query) {
    clearTimeout(searchTimer);
    query = searchKey(query);

    if (!query) {
        filteredMedia = [...currentMedia];
    } else {
        filteredMedia = currentMedia.filter(file => searchKey(file.name).includes(query));
    }

    applySortAndRender();
}

// ==================== MODAL VIEWER ====================
// Eén viewer voor de Files-weergave, de tijdlijn en de chat. Wie hem opent, geeft zijn
// eigen lijst bestanden mee (nooit mappen). Vorige/volgende, Verplaatsen en Open in Finder
// werken altijd op het bestand dat nu te zien is; de lijsten van de Files-weergave
// (currentMedia/filteredMedia) worden door de viewer nooit vervangen.

let currentAudioPlayer = null;
let audioKeyHandler = null;

const viewer = {
    items: [],          // bestanden waar vorige/volgende doorheen bladert
    index: -1,          // het bestand dat nu te zien is
    onRemoved: null,    // (file) => void: de opener haalt een verplaatst bestand uit zijn lijst
};

function viewerFile() {
    return viewer.index >= 0 && viewer.index < viewer.items.length ? viewer.items[viewer.index] : null;
}

function isViewerOpen() {
    return document.getElementById('modal-viewer').classList.contains('active');
}

// items: lijst bestandsobjecten van de server ({path, name, type, size, modified|timestamp, rating?})
function openViewer(items, index, { onRemoved = null } = {}) {
    const files = (items || []).filter(f => f && f.type !== 'folder');
    const start = files.indexOf(items[index]);
    if (start < 0) return;
    viewer.items = files;
    viewer.index = start;
    viewer.onRemoved = onRemoved;
    renderViewerFile();
    const modal = document.getElementById('modal-viewer');
    modal.classList.add('active');
    // Focus uit een bedekt veld halen (bv. de chat-invoer), anders typt de gebruiker daar blind verder
    const focused = document.activeElement;
    if (focused && focused !== document.body && !modal.contains(focused)) focused.blur();
}

// De bestanden zoals ze in de Files-grid staan (zonder mappen), in dezelfde volgorde
function gridFiles() {
    return filteredMedia.filter(item => item.type !== 'folder');
}

function openMediaModal(file) {
    openViewer(gridFiles(), gridFiles().indexOf(file), { onRemoved: removeFromFilesView });
}

// Haalt een verplaatst bestand uit de Files-weergave, ook als het vanuit de tijdlijn, de chat
// of een slideshow verplaatst werd (anders bleef de kaart staan en gaf een klik een 404)
function removeFromFilesView(file) {
    if (!currentMedia.some(f => f.path === file.path)) return;
    currentMedia = currentMedia.filter(f => f.path !== file.path);
    filteredMedia = filteredMedia.filter(f => f.path !== file.path);
    renderMediaGrid(true);
}

// Video of audio van het vorige bestand stoppen (ook bij bladeren, niet alleen bij sluiten)
function stopViewerMedia() {
    const video = document.querySelector('#modal-viewer video');
    if (video) {
        video.pause();
        video.removeAttribute('src');
        video.load();
    }
    if (currentAudioPlayer) {
        currentAudioPlayer.pause();
        currentAudioPlayer.src = '';
        currentAudioPlayer = null;
    }
    if (audioKeyHandler) {
        document.removeEventListener('keydown', audioKeyHandler);
        audioKeyHandler = null;
    }
}

function renderViewerFile() {
    const file = viewerFile();
    if (!file) return;
    const modalBody = document.getElementById('modal-body');
    const modalControls = document.getElementById('modal-controls');
    const modalMetadata = document.getElementById('modal-metadata');

    document.getElementById('modal-title').textContent = file.name;

    stopViewerMedia();
    // Alles behalve de navigatiezones vervangen
    const zones = [...modalBody.querySelectorAll(':scope > .modal-nav-zone')];
    modalBody.replaceChildren(...zones);
    modalZoom = 1;
    panOffset = { x: 0, y: 0 };
    modalBody.classList.remove('zoomed');
    modalBody.classList.toggle('has-video', file.type === 'video');
    zones.forEach(zone => {
        const right = zone.classList.contains('right');
        zone.hidden = right ? viewer.index >= viewer.items.length - 1 : viewer.index <= 0;
    });

    let media;
    if (file.type === 'video') {
        media = createEl('video', { controls: true, src: vaultFileUrl('file', file.path), style: 'max-width: 100%; max-height: 70vh;' });
        modalControls.style.display = 'none';
    } else if (file.type === 'audio') {
        renderAudioPlayer(file, modalBody); // voegt de speler zelf toe
        modalControls.style.display = 'none';
    } else if (file.type === 'other') {
        // Geen voorbeeld: de browser zou het hele bestand (soms GB's) in een <img> laden
        media = createEl('div', { class: 'modal-no-preview' },
            createEl('i', { class: 'fas fa-file' }),
            createEl('div', { text: 'Geen voorbeeld voor dit bestandstype' }),
            createEl('div', { class: 'modal-no-preview-name', text: file.name }));
        modalControls.style.display = 'none';
    } else {
        media = createEl('img', { id: 'modal-image', src: vaultFileUrl('file', file.path), alt: file.name });
        media.style.cursor = 'default';
        media.style.userSelect = 'none';
        media.addEventListener('mousedown', startPan);
        // Een formaat dat de browser niet zelf kan tonen (RAW, HEIC buiten Safari): de miniatuur
        media.addEventListener('error', () => {
            if (media.dataset.fallback) return;
            media.dataset.fallback = '1';
            media.title = 'Voorbeeld: dit formaat kan de browser niet zelf tonen';
            media.src = vaultFileUrl('thumb', file.path);
        });
        modalControls.style.display = 'flex';
    }
    if (media) modalBody.insertBefore(media, modalBody.firstChild);

    modalMetadata.replaceChildren(viewerMetadata(file));
}

// Alleen echte gegevens: de datum van het bestand (of de opnamedatum uit de tijdlijn) en
// de Shows-waardering als er echt over dit bestand is gestemd
function viewerMetadata(file) {
    const rows = [];
    const add = (label, ...value) => rows.push(
        createEl('span', { class: 'metadata-label', text: label }),
        createEl('span', { class: 'metadata-value' }, ...value));

    add('Pad', createEl('span', { title: file.path }, ...pathNodes(archivePath(file.path))), ' ',
        createEl('button', { class: 'copy-btn', text: 'Kopieer', title: 'Kopieer het volledige pad', onClick: () => copyToClipboard(file.path) }));
    if (typeof file.size === 'number') add('Grootte', formatBytes(file.size));
    if (typeof file.timestamp === 'number' && file.timestamp > 0) {
        add('Datum', new Date(file.timestamp * 1000).toLocaleString('nl-NL'));
        const source = { exif: 'EXIF (opnamedatum)', video: 'video (opnamedatum)', birthtime: 'aanmaakdatum bestand', mtime: 'wijzigingsdatum bestand' }[file.date_source];
        if (source) add('Bron', source);
    } else if (typeof file.modified === 'number' && file.modified > 0) {
        add('Gewijzigd', new Date(file.modified * 1000).toLocaleString('nl-NL'));
    }
    add('Soort', { photo: 'Foto', video: 'Video', audio: 'Audio', other: 'Ander bestand' }[file.type] || file.type);
    if (typeof file.rating === 'number') {
        const votes = typeof file.votes === 'number' ? ` (${file.votes} ${file.votes === 1 ? 'stem' : 'stemmen'})` : '';
        add('Shows-waardering', createEl('i', { class: 'fas fa-star', 'aria-hidden': 'true' }), `${file.rating}${votes}`);
    }
    return createEl('div', { class: 'metadata-grid' }, ...rows);
}

function closeModal() {
    stopViewerMedia();
    document.getElementById('modal-viewer').classList.remove('active');
    viewer.items = [];
    viewer.index = -1;
    viewer.onRemoved = null;
}

async function openInFinder() {
    const file = viewerFile();
    if (!file || !file.path) return;

    // Show visual feedback immediately
    const btn = document.getElementById('btn-open-finder');
    const oldText = btn.textContent;
    btn.textContent = 'Openen…';
    btn.disabled = true;

    try {
        await fetchJSON(`${API_BASE}/api/open-in-finder`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: file.path })
        });
    } catch (e) {
        toastFetchError('Tonen in Finder mislukt', e);
    } finally {
        setTimeout(() => {
            btn.textContent = oldText;
            btn.disabled = false;
        }, 1000);
    }
}

function renderAudioPlayer(file, container) {
    const audioUrl = `${API_BASE}/api/file?path=${encodeURIComponent(file.path)}`;

    // Stop existing
    if (currentAudioPlayer) {
        currentAudioPlayer.pause();
    }
    currentAudioPlayer = new Audio(audioUrl);
    currentAudioPlayer.autoplay = true; // Auto-play when opened

    // Build UI
    const playerDiv = document.createElement('div');
    playerDiv.className = 'audio-player-modal';

    // Cover art (pixel dog variation)
    const cover = document.createElement('div');
    cover.className = 'audio-cover';

    let hash = 0;
    for (let i = 0; i < file.name.length; i++) {
        hash = file.name.charCodeAt(i) + ((hash << 5) - hash);
    }
    const variant = Math.abs(hash) % 15 + 1;
    const coverSrc = `assets/audio_thumbs/audio_thumb_var${variant}.png`;

    cover.innerHTML = html`<img src="${coverSrc}" alt="">`;

    // Track Info
    const info = document.createElement('div');
    info.className = 'audio-track-info';

    const title = document.createElement('div');
    title.className = 'audio-title';
    // Use filename without extension as title
    let rawName = file.name;
    const lastDot = rawName.lastIndexOf('.');
    if (lastDot > 0) rawName = rawName.substring(0, lastDot);
    title.textContent = rawName;
    title.title = rawName;

    const artist = document.createElement('div');
    artist.className = 'audio-artist';
    artist.textContent = 'Lokaal bestand';

    info.appendChild(title);
    info.appendChild(artist);

    // Progress
    const progressContainer = document.createElement('div');
    progressContainer.className = 'audio-progress-container';

    const timeline = document.createElement('div');
    timeline.className = 'audio-timeline';
    const progressBar = document.createElement('div');
    progressBar.className = 'audio-progress-bar';
    timeline.appendChild(progressBar);

    const timeLabels = document.createElement('div');
    timeLabels.className = 'audio-time-labels';
    const currentTimeEl = document.createElement('span');
    currentTimeEl.textContent = '0:00';
    const totalTimeEl = document.createElement('span');
    totalTimeEl.textContent = '-:--';

    timeLabels.appendChild(currentTimeEl);
    timeLabels.appendChild(totalTimeEl);

    progressContainer.appendChild(timeline);
    progressContainer.appendChild(timeLabels);

    // Controls
    const controls = document.createElement('div');
    controls.className = 'audio-controls';

    const btnPlay = document.createElement('button');
    btnPlay.className = 'audio-btn audio-btn-play';
    btnPlay.innerHTML = '<i class="fas fa-play"></i>';

    controls.appendChild(btnPlay);

    // Append all
    playerDiv.appendChild(cover);
    playerDiv.appendChild(info);
    playerDiv.appendChild(progressContainer);
    playerDiv.appendChild(controls);
    container.appendChild(playerDiv);

    // Formatting helper
    const formatTime = (time) => {
        if (isNaN(time)) return '-:-';
        const mins = Math.floor(time / 60);
        const secs = Math.floor(time % 60);
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    };

    // Event Listeners
    currentAudioPlayer.addEventListener('loadedmetadata', () => {
        totalTimeEl.textContent = formatTime(currentAudioPlayer.duration);
    });

    currentAudioPlayer.addEventListener('timeupdate', () => {
        currentTimeEl.textContent = formatTime(currentAudioPlayer.currentTime);
        if (currentAudioPlayer.duration) {
            const pct = (currentAudioPlayer.currentTime / currentAudioPlayer.duration) * 100;
            progressBar.style.width = `${pct}%`;
        }
    });

    currentAudioPlayer.addEventListener('play', () => {
        btnPlay.innerHTML = '<i class="fas fa-pause"></i>';
        cover.classList.add('playing');
    });

    currentAudioPlayer.addEventListener('pause', () => {
        btnPlay.innerHTML = '<i class="fas fa-play"></i>';
        cover.classList.remove('playing');
    });

    currentAudioPlayer.addEventListener('ended', () => {
        btnPlay.innerHTML = '<i class="fas fa-play"></i>';
        cover.classList.remove('playing');
        progressBar.style.width = '0%';
        currentAudioPlayer.currentTime = 0;
    });

    btnPlay.addEventListener('click', () => {
        if (currentAudioPlayer.paused) {
            currentAudioPlayer.play();
        } else {
            currentAudioPlayer.pause();
        }
    });

    // Timeline scrubbing
    timeline.addEventListener('click', (e) => {
        const rect = timeline.getBoundingClientRect();
        const pos = (e.clientX - rect.left) / rect.width;
        if (currentAudioPlayer.duration) {
            currentAudioPlayer.currentTime = pos * currentAudioPlayer.duration;
        }
    });

    // Spacebar scrubbing & play
    if (audioKeyHandler) {
        document.removeEventListener('keydown', audioKeyHandler);
    }
    audioKeyHandler = (e) => {
        // Spacebar toggle if no input focused
        if (e.code === 'Space' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
            e.preventDefault();
            if (currentAudioPlayer.paused) {
                currentAudioPlayer.play();
            } else {
                currentAudioPlayer.pause();
            }
        }
    };
    document.addEventListener('keydown', audioKeyHandler);
}

// ==================== MOVE FILE SYSTEM ====================

let fileToMove = null;
let moveInFlight = false;   // één verplaatsing tegelijk: dubbel Enter/klikken stuurt niets extra
let moveNav = null;         // toetsenbordnavigatie van de mappenboom (één per modal)

function openMoveFileModal(fileData = null) {
    if (moveInFlight) return;
    // Welk bestand: expliciet meegegeven, de dia van de slideshow, of wat de viewer toont
    if (fileData) {
        fileToMove = fileData;
    } else if (activeShow) {
        fileToMove = activeShow.files[activeShow.currentIndex];
        activeShow.stopLoop(); // Pause show while moving
    } else {
        fileToMove = viewerFile();
    }

    if (!fileToMove) return;

    const modal = document.getElementById('modal-move-file');
    document.getElementById('move-file-name').textContent = fileToMove.name || 'Onbekend bestand';
    document.getElementById('btn-confirm-move').disabled = true;
    selectedMoveFolder = null;
    const destDisplay = document.getElementById('move-dest-display');
    if (destDisplay) destDisplay.style.display = 'none';

    // Show source folder path
    const sourcePath = fileToMove.path || '';
    const sourceFolder = sourcePath.substring(0, sourcePath.lastIndexOf('/'));
    const sourceEl = document.getElementById('move-file-source');
    if (sourceEl) {
        // Hetzelfde pad als in de viewer: vanaf het archief, in mono
        sourceEl.querySelector('span').replaceChildren(...pathNodes(archivePath(sourceFolder)));
        sourceEl.title = sourceFolder;
    }

    // Show thumbnail preview
    const previewEl = document.getElementById('move-file-preview');
    if (previewEl) {
        previewEl.innerHTML = html`<img src="${vaultFileUrl('thumb', fileToMove.path)}" alt="Voorbeeld">`;
    }

    renderMoveFolderTreeUI();
    modal.classList.add('active');

    // Toetsenbordnavigatie: activateFolderTreeModal vervangt de vorige luisteraar van deze
    // modal, zodat één Enter nooit meer dan één keer bevestigt
    moveNav = activateFolderTreeModal({
        modalId: 'modal-move-file',
        containerId: 'move-folder-tree',
        itemSelector: '.move-folder-node',
        childrenSelector: '.move-folder-children',
        invertCollapse: true,
        onSelect: (item) => {
            const path = item.dataset.path;
            if (path) selectMoveDestination(path);
        },
        onHighlight: (item) => { if (item.dataset.path) previewMoveDestination(item.dataset.path, true); },
        onConfirm: () => { if (selectedMoveFolder) confirmMoveFile(); },
        onClose: closeMoveFileModal,
    }).nav;
}

function isMoveModalOpen() {
    const modal = document.getElementById('modal-move-file');
    return !!modal && modal.classList.contains('active');
}

function closeMoveFileModal() {
    if (moveInFlight) return; // het verzoek loopt al; het antwoord sluit de modal
    const modal = document.getElementById('modal-move-file');
    if (modal) modal.classList.remove('active');
    if (moveNav) moveNav.resetIndex();

    fileToMove = null;
    selectedMoveFolder = null;

    // Resume show if it was playing and not intentionally paused
    if (activeShow && activeShow.isPlaying) {
        activeShow.startLoop();
    }
}

function renderMoveFolderTreeUI() {
    const container = document.getElementById('move-folder-tree');
    if (!container) return;
    renderFolderTree(container, folderTree, {
        itemClass: 'move-folder-node',
        childrenClass: 'move-folder-children',
        collapseMode: 'expanded',
        indent: 16,
        indentBase: 0,
        iconMode: 'fontawesome',
        selectedPath: selectedMoveFolder,
        chevronHtml: html`<i class="fas fa-chevron-right"></i>`,
        onSelect: (path) => selectMoveDestination(path),
    });
    container.scrollTop = 0;
}

function selectMoveDestination(path) {
    if (moveInFlight || !fileToMove) return;
    previewMoveDestination(path, false);
    // Re-render to show selection highlight
    renderMoveFolderTreeUI();
}

// De gekozen doelmap tonen (links 'Naar', de knop aan) zonder de boom opnieuw te tekenen: ook
// voor de map die met de pijltjes gemarkeerd is, zodat Enter en de knop hetzelfde doen als
// wat je ziet. quiet: geen melding als het de huidige map is (bij pijltjes)
function previewMoveDestination(path, quiet) {
    if (moveInFlight || !fileToMove) return;
    selectedMoveFolder = path;
    const btn = document.getElementById('btn-confirm-move');

    // Prevent moving to the same directory
    const currentDir = fileToMove.path.substring(0, fileToMove.path.lastIndexOf('/'));

    if (path === currentDir) {
        btn.disabled = true;
        if (!quiet) showToast('Verplaatsen', 'Het bestand staat al in deze map', 'warning');
    } else {
        btn.disabled = false;
    }

    // Show destination in left panel
    const destDisplay = document.getElementById('move-dest-display');
    const destPath = document.getElementById('move-dest-path');
    if (destDisplay && destPath) {
        destPath.querySelector('span').replaceChildren(...pathNodes(archivePath(path)));
        destPath.title = path;
        destDisplay.style.display = '';
    }
    document.querySelectorAll('#move-folder-tree .move-folder-node').forEach(node =>
        node.classList.toggle('selected', node.dataset.path === path));
}

async function confirmMoveFile() {
    if (moveInFlight || !fileToMove || !selectedMoveFolder) return;
    moveInFlight = true;
    const file = fileToMove;
    const destination = selectedMoveFolder;

    const btn = document.getElementById('btn-confirm-move');
    const originalContent = [...btn.childNodes];
    btn.innerHTML = '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Verplaatsen…';
    btn.disabled = true;

    let moved;
    try {
        moved = await fetchJSON(`${API_BASE}/api/move-file`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source_path: file.path,
                destination_folder: destination
            })
        });
    } catch (e) {
        toastFetchError('Verplaatsen mislukt', e);
        btn.replaceChildren(...originalContent);
        btn.disabled = false;
        moveInFlight = false;
        return;
    }

    showToast('Verplaatst', `${moved.message || 'Bestand verplaatst'}. Terugzetten kan in het Herstelcentrum.`, 'success');
    btn.replaceChildren(...originalContent);
    removeFromFilesView(file);

    if (activeShow && activeShow.files.includes(file)) {
        // Add a fly-away animation to the current active slide
        const show = activeShow;
        const overlay = document.getElementById('cinematic-player');
        const activeSlide = overlay && overlay.querySelector('.cinematic-slide.active');
        if (activeSlide) activeSlide.classList.add('fly-away-animation');

        setTimeout(() => {
            moveInFlight = false;
            // Het verplaatste bestand zelf eruit halen, niet "de huidige dia"
            const at = show.files.indexOf(file);
            if (at >= 0) show.files.splice(at, 1);
            closeMoveFileModal();
            if (show !== activeShow) return;
            if (show.files.length === 0) {
                show.close();
            } else {
                if (show.currentIndex >= show.files.length) show.currentIndex = 0;
                show.renderSlide();
            }
        }, 600); // Wait for fly away animation
        return;
    }

    moveInFlight = false;
    if (viewerFile() === file) {
        const onRemoved = viewer.onRemoved;
        closeModal();
        if (onRemoved) onRemoved(file);
    }
    closeMoveFileModal();
}

function zoomIn() {
    modalZoom = Math.min(modalZoom + 0.2, 3);
    updateModalZoom();
}

function zoomOut() {
    modalZoom = Math.max(modalZoom - 0.2, 0.5);
    updateModalZoom();
}

function fitToScreen() {
    modalZoom = 1;
    updateModalZoom();
}

function updateModalZoom() {
    const img = document.getElementById('modal-image');
    // Ingezoomd sleep je de foto; dan zitten de bladerzones in de weg
    document.getElementById('modal-body').classList.toggle('zoomed', modalZoom > 1);
    if (img) {
        updateImageTransform();
        img.style.cursor = modalZoom > 1 ? 'grab' : 'default';
    }
}

// ==================== PAN/DRAG FOR ZOOMED IMAGES ====================

function startPan(e) {
    if (modalZoom <= 1) return; // Only pan when zoomed
    e.preventDefault();
    isPanning = true;
    panStart = { x: e.clientX - panOffset.x, y: e.clientY - panOffset.y };

    const img = document.getElementById('modal-image');
    if (img) img.style.cursor = 'grabbing';

    // Add global listeners
    document.addEventListener('mousemove', doPan);
    document.addEventListener('mouseup', endPan);
}

function doPan(e) {
    if (!isPanning) return;
    e.preventDefault();
    panOffset.x = e.clientX - panStart.x;
    panOffset.y = e.clientY - panStart.y;
    updateImageTransform();
}

function endPan(e) {
    if (!isPanning) return;
    isPanning = false;

    const img = document.getElementById('modal-image');
    if (img) img.style.cursor = modalZoom > 1 ? 'grab' : 'default';

    // Remove global listeners
    document.removeEventListener('mousemove', doPan);
    document.removeEventListener('mouseup', endPan);
}

function updateImageTransform() {
    const img = document.getElementById('modal-image');
    if (img) {
        img.style.transform = `scale(${modalZoom}) translate(${panOffset.x / modalZoom}px, ${panOffset.y / modalZoom}px)`;
    }
}

function prevFile() {
    if (viewer.index > 0) {
        viewer.index--;
        renderViewerFile();
    }
}

function nextFile() {
    if (viewer.index >= 0 && viewer.index < viewer.items.length - 1) {
        viewer.index++;
        renderViewerFile();
    }
}

// Keyboard navigation (niet terwijl de verplaats-modal erboven open is)
document.addEventListener('keydown', (e) => {
    if (!isViewerOpen() || isMoveModalOpen()) return;
    // Alleen invoervelden in de viewer zelf houden de toetsen; een veld eronder
    // (zoals de chat die het bestand opende) is bedekt en telt niet mee
    const tag = e.target && e.target.tagName;
    if ((tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT')
        && e.target.closest('#modal-viewer')) return;
    if (e.key === 'ArrowLeft') prevFile();
    if (e.key === 'ArrowRight') nextFile();
    // Zoomen en de knoppen onderin, zonder muis (niet met Cmd/Ctrl: dat is de browserzoom)
    if (!e.metaKey && !e.ctrlKey && !e.altKey && !(tag === 'BUTTON' && (e.key === ' ' || e.key === 'Enter'))) {
        const file = viewerFile();
        const isImage = file && file.type !== 'video' && file.type !== 'audio' && file.type !== 'other';
        if ((e.key === '+' || e.key === '=') && isImage) zoomIn();
        else if ((e.key === '-' || e.key === '_') && isImage) zoomOut();
        else if (e.key === '0' && isImage) fitToScreen();
        else if (e.key === 'm' || e.key === 'M') { e.preventDefault(); openMoveFileModal(); }
        else if (e.key === 'f' || e.key === 'F') openInFinder();
    }
    if (e.key === 'Escape') {
        // Alleen de viewer sluiten, niet ook de chat of slideshow eronder
        e.stopImmediatePropagation();
        closeModal();
    }
});

// ==================== TOAST NOTIFICATIONS ====================

// Hoe lang een melding blijft staan: een fout of waarschuwing lang genoeg om te lezen
const TOAST_MS = { error: 8000, warning: 6000, success: 3500, info: 3500 };

const TOAST_ICONS = {
    success: 'fa-circle-check',
    error: 'fa-circle-exclamation',
    warning: 'fa-triangle-exclamation',
    info: 'fa-circle-info',
};

function showToast(title, message, type = 'info') {
    const container = document.getElementById('toast-container');
    const duration = TOAST_MS[type] || TOAST_MS.info;
    // Dezelfde melding nog in beeld (bijv. tien mislukte miniaturen): niet opstapelen, alleen langer tonen
    const same = [...container.children].find(t => t.dataset.title === String(title) && t.dataset.message === String(message));
    if (same) {
        same.style.opacity = '1';
        clearTimeout(same._hideTimer);
        same._hideTimer = setTimeout(() => hideToast(same), duration);
        return;
    }
    const toast = document.createElement('div');
    toast.className = `toast ${type} toast-${type}`;
    toast.dataset.title = String(title);
    toast.dataset.message = String(message);
    // Een fout wordt meteen voorgelezen; de rest zegt de container (aria-live polite)
    if (type === 'error') toast.setAttribute('role', 'alert');
    // Titel en melding zijn tekst: ze bevatten vaak bestandsnamen of een foutmelding van de server
    toast.append(
        createEl('div', { class: 'toast-header' },
            // Het soort melding zit in het icoon en de balk links; de titel blijft rustig
            createEl('i', { class: `fas ${TOAST_ICONS[type] || TOAST_ICONS.info} toast-icon`, 'aria-hidden': 'true' }),
            createEl('strong', { text: title }),
            createEl('button', { onClick: () => toast.remove(), 'aria-label': 'Melding sluiten', title: 'Sluiten' }, '\u00d7')),
        createEl('div', { class: 'toast-body', text: message }));

    container.appendChild(toast);

    // Met de muis erop blijft hij staan, zodat een lange melding uit te lezen is
    toast._hideTimer = setTimeout(() => hideToast(toast), duration);
    toast.addEventListener('mouseenter', () => { clearTimeout(toast._hideTimer); toast.style.opacity = '1'; });
    toast.addEventListener('mouseleave', () => { toast._hideTimer = setTimeout(() => hideToast(toast), duration); });
}

function hideToast(toast) {
    toast.style.opacity = '0';
    toast._hideTimer = setTimeout(() => toast.remove(), 300);
}

// Eén manier om een mislukt verzoek te melden (FRONTEND-ERR-1): de melding van de server in
// een toast. Is het archief weg, dan zegt de banner bovenin dat al; geen toast erbij.
function toastFetchError(title, err) {
    if (err && err.code === 'vault_unavailable') {
        console.warn(title, err.message);
        return;
    }
    console.error(title, err);
    showToast(title, (err && err.message) || String(err), 'error');
}

// ==================== ARCHIEF- EN SYSTEEMSTATUS ====================
// De T7 kan elk moment los raken. De pagina vraagt /api/health af en toe (vaker zolang het
// archief weg is), toont dan een banner en laadt alles opnieuw zodra de schijf terug is:
// zonder herstart van de server. Ontbrekende hulpmiddelen (ffmpeg, HEIC, Ollama of het
// chatmodel) staan rustig in een melding linksonder die je kunt wegklikken.
const STATUS_POLL_MS = 15000;
const STATUS_POLL_OFFLINE_MS = 3000;
let vaultAvailable = true;
let statusTimer = null;

async function checkServerStatus() {
    clearTimeout(statusTimer);
    try {
        const res = await fetch(`${API_BASE}/api/health`, { cache: 'no-store' });
        if (res.ok) {
            const health = await res.json();
            setVaultAvailable(health.vault_available !== false, health.vault_message);
            renderCapabilityNotices(health.notices || []);
        }
    } catch (_) { /* server even weg (herstart): volgende keer opnieuw */ }
    statusTimer = setTimeout(checkServerStatus, vaultAvailable ? STATUS_POLL_MS : STATUS_POLL_OFFLINE_MS);
}

function setVaultAvailable(available, message) {
    const banner = document.getElementById('vault-banner');
    if (!available) {
        if (banner) {
            banner.querySelector('.vault-banner-text').textContent =
                message || 'Archief niet bereikbaar (schijf losgekoppeld?)';
            banner.hidden = false;
        }
        vaultAvailable = false;
        return;
    }
    if (banner) banner.hidden = true;
    if (!vaultAvailable) {
        vaultAvailable = true;
        showToast('Archief', 'Het archief is weer bereikbaar', 'success');
        reloadAfterVaultReturn();
    }
}

// Alles wat zonder archief leeg of met een fout bleef, opnieuw laden
function reloadAfterVaultReturn() {
    loadFolders();
    const view = currentMainView();
    if (view === 'home') fetchHomeWidgets();
    else if (view === 'viewer' && currentFolder) loadMedia(currentFolder);
    else if (view === 'shows') loadShows();
    else if (view === 'spotify') loadSpotifyStats();
    else if (view === 'timeline' && typeof timelineVaultReturned === 'function') timelineVaultReturned();
    else if (view === 'keurer' && typeof keurerVaultReturned === 'function') keurerVaultReturned();
    else if (view === 'duplicates' && typeof duplicatesInit === 'function') duplicatesInit();
    else if (view === 'recovery' && typeof recoveryLoad === 'function') recoveryLoad();
    else if (view === 'settings' && typeof settingsRender === 'function') settingsRender();
    if (typeof jobsLibraryRefresh === 'function') jobsLibraryRefresh();
    loadChatModels();
}

// Een verzoek kreeg 'archief niet bereikbaar' (zie fetchJSON in api.js): meteen tonen en vaker kijken
window.addEventListener('datavault:vault-unavailable', () => {
    if (vaultAvailable) setVaultAvailable(false);
    clearTimeout(statusTimer);
    statusTimer = setTimeout(checkServerStatus, STATUS_POLL_OFFLINE_MS);
});

function noticeKey(notices) {
    return notices.map(n => `${n.code}:${n.message}`).sort().join('|');
}

function renderCapabilityNotices(notices) {
    const box = document.getElementById('capability-notice');
    if (!box) return;
    let dismissed = null;
    try { dismissed = localStorage.getItem('dismissedNotices'); } catch (_) { /* privé-venster */ }
    if (notices.length === 0 || dismissed === noticeKey(notices)) {
        box.hidden = true;
        return;
    }
    const close = createEl('button', { class: 'capability-notice-close', title: 'Verbergen', 'aria-label': 'Verbergen' }, '\u00d7');
    close.addEventListener('click', () => {
        try { localStorage.setItem('dismissedNotices', noticeKey(notices)); } catch (_) { /* niet bewaard */ }
        box.hidden = true;
    });
    box.replaceChildren(
        createEl('div', { class: 'capability-notice-head' },
            createEl('i', { class: 'fas fa-info-circle' }),
            createEl('strong', { text: 'Niet alles werkt' }), close),
        ...notices.map(n => createEl('div', { class: 'capability-notice-item' },
            createEl('div', { class: 'capability-notice-title', text: n.title }),
            createEl('div', { class: 'capability-notice-text', text: n.message }))));
    box.hidden = false;
}

// ==================== UTILS ====================

function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showToast('Gekopieerd', 'Pad gekopieerd naar het klembord', 'success');
    });
}

// ==================== HOME WIDGETS ====================

// Begroeting en datum bovenaan Home
function homeRenderGreeting() {
    const now = new Date();
    const hour = now.getHours();
    const greeting = hour < 6 ? 'Goedenacht' : hour < 12 ? 'Goedemorgen' : hour < 18 ? 'Goedemiddag' : 'Goedenavond';
    const title = document.getElementById('home-greeting');
    const date = document.getElementById('home-date');
    if (title) title.textContent = greeting;
    if (date) date.textContent = now.toLocaleDateString('nl-NL', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
}

// Cijfertegels op Home, uit de bibliotheekstatus die jobs.js ophaalt (null: niet bereikbaar)
function homeRenderLibraryStats(lib) {
    const ready = Boolean(lib && lib.ready);
    const show = (id, value) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = ready ? Number(value || 0).toLocaleString('nl-NL') : '–';
        el.classList.toggle('is-empty', !ready);
    };
    show('home-stat-photos', lib && lib.photos);
    show('home-stat-videos', lib && lib.videos);
    show('home-stat-audio', lib && lib.audio);
    // Zonder bibliotheek zeggen alle drie de tegels hetzelfde, niet één tegel apart
    const status = lib ? 'Bibliotheek wordt opgebouwd' : 'Bibliotheek niet bereikbaar';
    const meta = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.textContent = ready ? text : status;
    };
    meta('home-stat-folders', ready ? `In ${Number(lib.folders || 0).toLocaleString('nl-NL')} mappen` : '');
    meta('home-stat-videos-meta', 'In de bibliotheek');
    meta('home-stat-audio-meta', 'In de bibliotheek');
}

async function fetchHomeWidgets() {
    homeRenderGreeting();
    // 1. Storage
    try {
        homeRenderStorage(await fetchJSON(`${API_BASE}/api/stats/storage`), null);
    } catch (e) {
        homeRenderStorage(null, e);
    }

    // 2. Photo OTD
    loadPhotoOtdWidget();

    // 3. Spotify OTD
    homeOtdLoading('spotify-otd-loading', 'spotify-otd-content');
    try {
        const data = await fetchJSON(`${API_BASE}/api/stats/spotify-on-this-day`);
        if (data.track) {
            const t = data.track;
            homeOtdShow('spotify-otd-loading', 'spotify-otd-content',
                createEl('div', { class: 'otd-item', onClick: () => openSpotifySearch(`${t.track} ${t.artist}`) },
                    createEl('div', { class: 'otd-spotify-placeholder' }, createEl('i', { class: 'fab fa-spotify' })),
                    createEl('div', { class: 'otd-title', text: t.track }),
                    createEl('div', { class: 'otd-subtitle', text: `${t.artist} • ${t.year}` })));

            // Geen albumcover van buitenaf (zoals vroeger via iTunes): dat zou luistergeschiedenis
            // naar een externe dienst sturen. Het lokale Spotify-icoon blijft staan.
        } else {
            homeOtdShow('spotify-otd-loading', 'spotify-otd-content', homeOtdState(emptyState({
                icon: 'fa-music',
                title: 'Niets geluisterd',
                text: 'Op deze dag in eerdere jaren speelde er niets.',
                action: { label: 'Naar Spotify', onClick: () => switchMainView('spotify') },
            })));
        }
    } catch (e) {
        homeOtdShow('spotify-otd-loading', 'spotify-otd-content', homeOtdState(errorState('Laden mislukt', e)));
    }
}

// Opslagtegel: gebruikt, de balk en wat er vrij is; lukt het niet, dan '–' met de reden in de
// metaregel, net als de andere tegels (nooit een lange zin als groot getal)
function homeRenderStorage(data, error) {
    const tile = document.getElementById('widget-storage');
    if (!tile) return;
    const used = document.getElementById('storage-used-text');
    const meta = document.getElementById('storage-meta');
    const barBox = tile.querySelector('.storage-bar-container');
    const bar = document.getElementById('storage-bar-fill');
    const gb = value => formatBytes(Number(value) * 1024 ** 3);
    tile.querySelector('.storage-loading').style.display = 'none';
    tile.querySelector('.storage-stats').style.display = '';
    const ok = !error && data && data.total_gb;
    used.classList.toggle('is-empty', !ok);
    barBox.hidden = !ok;
    // Rood alleen bij een echte fout; een losgekoppelde schijf meldt de banner al
    meta.classList.toggle('stat-tile-error', Boolean(error) && error.code !== 'vault_unavailable');
    if (!ok) {
        used.textContent = '–';
        meta.textContent = error && error.code === 'vault_unavailable' ? 'Archief niet bereikbaar' : 'Opslag niet bekend';
        if (error) meta.title = error.message;
        return;
    }
    meta.removeAttribute('title');
    used.textContent = gb(data.used_gb);
    meta.replaceChildren('van ', createEl('span', { text: gb(data.total_gb) }), ' · ',
        createEl('span', { text: gb(data.free_gb) }), ' vrij');
    bar.style.width = `${data.percent_used}%`;
    bar.classList.toggle('critical', data.percent_used > 90);
    bar.classList.toggle('warning', data.percent_used > 75 && data.percent_used <= 90);
}

// 'Op deze dag': eerst de draaier, daarna de inhoud of een lege of mislukte staat. Er komt
// nooit tekst in de draaier zelf (die draait).
function homeOtdLoading(loadingId, contentId) {
    document.getElementById(loadingId).style.display = '';
    document.getElementById(contentId).style.display = 'none';
}

function homeOtdShow(loadingId, contentId, ...nodes) {
    const content = document.getElementById(contentId);
    document.getElementById(loadingId).style.display = 'none';
    content.style.display = 'block';
    content.replaceChildren(...nodes);
}

// Een lege of mislukte staat, compact genoeg voor een kaart op Home
function homeOtdState(node) {
    node.classList.add('empty-state-compact', 'otd-state');
    return node;
}

function openAndSearch(query) {
    switchMainView('viewer');
    const searchInput = document.getElementById('global-search');
    searchInput.value = query;
    if (typeof handleGlobalSearch === 'function') {
        handleGlobalSearch(query);
    }
}

async function openPhotoOTD(filePath) {
    // Bepaal de map van de foto
    const folder = filePath.substring(0, filePath.lastIndexOf('/'));
    const fileName = filePath.substring(filePath.lastIndexOf('/') + 1);

    // Wissel naar de viewer
    switchMainView('viewer');

    // Laad de map
    currentFolder = folder;
    await loadMedia(folder);
    renderSidebarFolderTree();
    updateBreadcrumb(folder);

    // Zoek de foto in de geladen bestanden en open de modal
    const file = gridFiles().find(f => f.name === fileName);
    if (file) openMediaModal(file);
}

// ==================== PHOTO OTD SETTINGS ====================
let otdCycleInterval = null;
let otdCycleStep = null;    // wisselt naar de volgende foto; null als er niets te wisselen valt

// De foto's van "op deze dag" wisselen alleen terwijl Home open is (en de tab zichtbaar)
function homeOtdCycle(active) {
    if (otdCycleInterval) {
        clearInterval(otdCycleInterval);
        otdCycleInterval = null;
    }
    if (active && otdCycleStep) {
        otdCycleInterval = setInterval(() => { if (!document.hidden && otdCycleStep) otdCycleStep(); }, 4000);
    }
}

async function loadPhotoOtdWidget() {
    try {
        const content = document.getElementById('photo-otd-content');

        // Draaier tonen en het wisselen stoppen
        homeOtdLoading('photo-otd-loading', 'photo-otd-content');

        otdCycleStep = null;
        homeOtdCycle(false);

        let otdAllowedFolders = [];
        try { otdAllowedFolders = JSON.parse(localStorage.getItem('otdAllowedFolders') || '[]'); } catch (_) { /* geen keuze bewaard */ }
        // Eén parameter per map: een mapnaam mag een komma bevatten
        const params = new URLSearchParams();
        (Array.isArray(otdAllowedFolders) ? otdAllowedFolders : []).forEach(f => params.append('allowed_folders', f));
        const query = params.toString() ? `?${params}` : '';
        const data = await fetchJSON(`${API_BASE}/api/stats/photo-on-this-day${query}`);

        if (data.photos && data.photos.length > 0) {
            homeOtdShow('photo-otd-loading', 'photo-otd-content');
            data.photos.forEach((p, index) => {
                const image = createEl('div', {
                    class: 'otd-large-image' + (index === 0 ? ' active' : ''),
                    style: 'background-size: cover; background-position: center;',
                    onClick: () => openPhotoOTD(p.path),
                });
                image.style.backgroundImage = cssUrl(vaultFileUrl('thumb', p.path));
                const overlay = createEl('div', { class: 'otd-large-overlay', style: `display: ${index === 0 ? 'flex' : 'none'};` },
                    createEl('div', { class: 'otd-large-title', text: p.name }),
                    createEl('div', { class: 'otd-large-subtitle', text: p.year }));
                content.append(image, overlay);
            });

            if (data.photos.length > 1) {
                let currentOtd = 0;
                otdCycleStep = () => {
                    const images = content.querySelectorAll('.otd-large-image');
                    const overlays = content.querySelectorAll('.otd-large-overlay');
                    if (images.length === 0) {
                        homeOtdCycle(false);
                        otdCycleStep = null;
                        return;
                    }
                    images[currentOtd].classList.remove('active');
                    overlays[currentOtd].style.display = 'none';

                    currentOtd = (currentOtd + 1) % images.length;

                    images[currentOtd].classList.add('active');
                    overlays[currentOtd].style.display = 'flex';
                };
                homeOtdCycle(!document.getElementById('section-home').classList.contains('hidden'));
            }
        } else {
            homeOtdShow('photo-otd-loading', 'photo-otd-content', homeOtdState(emptyState({
                icon: 'fa-camera-retro',
                title: 'Nog geen herinnering',
                text: 'Er zijn geen foto\'s van deze dag in eerdere jaren.',
                action: { label: 'Mappen kiezen', onClick: e => toggleOtdSettings(e) },
            })));
        }
    } catch (e) {
        homeOtdShow('photo-otd-loading', 'photo-otd-content', homeOtdState(errorState('Laden mislukt', e)));
    }
}

function toggleOtdSettings(e) {
    if (e) e.stopPropagation();
    const panel = document.getElementById('otd-settings-panel');
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        updateOtdSettingsList();

        const outsideClickListener = event => {
            if (!panel.contains(event.target) && !event.target.classList.contains('otd-settings-icon')) {
                panel.style.display = 'none';
                document.removeEventListener('click', outsideClickListener);
            }
        };
        document.addEventListener('click', outsideClickListener);
    } else {
        panel.style.display = 'none';
    }
}

function updateOtdSettingsList() {
    const list = document.getElementById('otd-settings-list');
    list.innerHTML = '';

    if (!folderTree || folderTree.length === 0) {
        list.innerHTML = '<div class="otd-settings-empty">Geen mappen gevonden</div>';
        return;
    }

    let allowed = JSON.parse(localStorage.getItem('otdAllowedFolders') || '[]');
    const allSelected = allowed.length === 0;

    folderTree.forEach(folder => {
        const isChecked = allSelected || allowed.includes(folder.path);
        const item = document.createElement('label');
        item.className = 'otd-setting-item';

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = folder.path;
        checkbox.checked = isChecked;
        checkbox.onchange = saveOtdSettings;

        const text = document.createElement('span');
        text.textContent = folder.name;

        item.appendChild(checkbox);
        item.appendChild(text);
        list.appendChild(item);
    });
}

function saveOtdSettings() {
    const checkboxes = document.querySelectorAll('#otd-settings-list input[type="checkbox"]');
    const allowed = [];
    let allChecked = true;

    checkboxes.forEach(cb => {
        if (cb.checked) {
            allowed.push(cb.value);
        } else {
            allChecked = false;
        }
    });

    if (allChecked || allowed.length === 0) {
        localStorage.removeItem('otdAllowedFolders');
    } else {
        localStorage.setItem('otdAllowedFolders', JSON.stringify(allowed));
    }

    // Reload OTD widget
    loadPhotoOtdWidget();
}


// ==================== SERVER RESTART ====================

// Hoe lang de pagina op de herstarte server wacht voordat ze een foutmelding toont
const RESTART_TIMEOUT_MS = 30000;
const RESTART_POLL_MS = 600;

function setBrandRestarting(active) {
    const brand = document.querySelector('.nav-brand');
    if (!brand) return;
    if (active) {
        brand.dataset.restarting = '1';
        brand.style.opacity = '0.5';
        brand.style.cursor = 'wait';
        brand.title = 'Server herstart...';
    } else {
        delete brand.dataset.restarting;
        brand.style.opacity = '';
        brand.style.cursor = 'pointer';
        brand.title = 'Server herstarten';
    }
}

async function restartServer() {
    const brand = document.querySelector('.nav-brand');

    // Dubbele klik beveiliging
    if (brand && brand.dataset.restarting) return;
    if (!confirm('DataVault-server herstarten?\n\nDe app is dan een paar seconden niet bereikbaar.')) return;

    setBrandRestarting(true);

    // Onthoud welk serverproces nu draait: de poll wacht op een NIEUW proces,
    // niet op het oude dat vlak na het verzoek nog even antwoordt
    let oldInstance = null;
    try {
        const health = await fetch(`${API_BASE}/api/health`, { cache: 'no-store' });
        if (health.ok) oldInstance = (await health.json()).instance || null;
    } catch (_) { /* server niet bereikbaar; poll hieronder wacht gewoon */ }

    try {
        const res = await fetch(`${API_BASE}/api/restart`, { method: 'POST' });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            if (res.status === 409 && err.detail === 'Herstart is al bezig') {
                // Al herstart vanuit een ander tabblad: gewoon meewachten op de nieuwe server
                showRestartOverlay(oldInstance);
                return;
            }
            setBrandRestarting(false);
            showToast('Herstarten mislukt', err.detail || `Serverfout (${res.status})`, 'error');
            return;
        }
    } catch (_) {
        // Kan gebeuren als de server wegvalt vóór het antwoord binnen is
    }

    // Toon overlay en poll tot de nieuwe server er is (of tot de time-out)
    showRestartOverlay(oldInstance);
}

function showRestartOverlay(oldInstance) {
    let overlay = document.getElementById('restart-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'restart-overlay';
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = `
        <div class="restart-box">
            <div class="loading-spinner" id="restart-spinner" style="width:32px;height:32px;margin:0 auto 14px;"></div>
            <div class="restart-title" id="restart-title">Server herstart...</div>
            <div class="restart-sub" id="restart-sub">Even geduld</div>
        </div>`;
    overlay.style.display = 'flex';

    let dots = 0;
    const subEl = document.getElementById('restart-sub');
    const dotTimer = setInterval(() => {
        dots = (dots + 1) % 4;
        if (subEl) subEl.textContent = 'Even geduld' + '.'.repeat(dots);
    }, 400);

    const startedAt = Date.now();

    const succeed = () => {
        clearInterval(dotTimer);
        overlay.style.display = 'none';
        setBrandRestarting(false);
        // Nieuw proces = nieuw sessietoken; alvast ophalen (api.js zou het anders bij het
        // eerstvolgende wijzigende verzoek doen)
        if (window.DataVaultSession) window.DataVaultSession.refresh().catch(() => {});
        showToast('DataVault', 'Server succesvol herstart', 'success');
    };

    const giveUp = () => {
        clearInterval(dotTimer);
        setBrandRestarting(false);
        const spinner = document.getElementById('restart-spinner');
        if (spinner) spinner.remove();
        document.getElementById('restart-title').textContent = 'Server reageert niet';
        subEl.textContent = `Na ${RESTART_TIMEOUT_MS / 1000} seconden is de server nog niet terug. `
            + 'Start DataVault opnieuw met start.sh of de DataVault-app.';
        const close = document.createElement('button');
        close.className = 'btn btn-secondary';
        close.style.marginTop = '16px';
        close.textContent = 'Sluiten';
        close.onclick = () => { overlay.style.display = 'none'; };
        overlay.querySelector('.restart-box').appendChild(close);
    };

    // Eén poll tegelijk (setTimeout i.p.v. setInterval), stopt na succes of time-out
    const poll = async () => {
        try {
            const res = await fetch(`${API_BASE}/api/health`, { cache: 'no-store' });
            if (res.ok) {
                const data = await res.json();
                if (!oldInstance || data.instance !== oldInstance) {
                    succeed();
                    return;
                }
            }
        } catch (_) { /* nog niet klaar */ }

        if (Date.now() - startedAt >= RESTART_TIMEOUT_MS) {
            giveUp();
            return;
        }
        setTimeout(poll, RESTART_POLL_MS);
    };
    setTimeout(poll, RESTART_POLL_MS);
}

// ==================== INITIALIZATION ====================

document.addEventListener('DOMContentLoaded', () => {
    // Determine view to load
    initTheme();
    loadFolders();
    switchMainView('home');
    fetchHomeWidgets();
    loadChatModels();
    checkServerStatus();

    // Start background prefetch na 1.5s zodat de initiële pagina eerst volledig laadt
    scheduleBackgroundPrefetch();
});


// ==================== DNA / MOUNT EXPLANATION ====================

function showDnaExplanation() {
    const modal = document.getElementById('modal-dna-explanation');

    // Populate DNA type from current data
    const dnaText = document.getElementById('stat-dna');
    const currentDna = dnaText ? (dnaText.dataset.dna || dnaText.textContent).trim() : '';

    const dnaTypes = {
        'Early Bird 🐦': { icon: 'fa-dove', title: 'Vroege vogel', key: 'Early Bird', desc: 'Je luistert het meest tussen 5:00 en 12:00. Je begint de dag met muziek, nog voor de rest wakker is.' },
        'Daydreamer ☀️': { icon: 'fa-sun', title: 'Dagdromer', key: 'Daydreamer', desc: 'Je luistert het meest tussen 12:00 en 18:00. Muziek houdt je middag op gang.' },
        'Evening Chiller 🌇': { icon: 'fa-cloud-moon', title: 'Avondgenieter', key: 'Evening Chiller', desc: 'Je luistert het meest tussen 18:00 en 23:00. Muziek is je manier om de dag af te sluiten.' },
        'Night Owl 🦉': { icon: 'fa-moon', title: 'Nachtuil', key: 'Night Owl', desc: 'Je luistert het meest tussen 23:00 en 5:00. De nacht is van jou, met muziek als gezelschap.' },
        'Balanced Listener': { icon: 'fa-headphones', title: 'Evenwichtige luisteraar', key: 'Balanced Listener', desc: 'Je luistert verspreid over de hele dag, zonder één piekmoment.' }
    };

    const info = dnaTypes[currentDna] || dnaTypes['Balanced Listener'];

    // Fill in modal content
    const iconEl = document.getElementById('dna-modal-icon');
    const titleEl = document.getElementById('dna-modal-title');
    const descEl = document.getElementById('dna-modal-description');
    if (iconEl) iconEl.replaceChildren(createEl('i', { class: `fas ${info.icon}` }));
    if (titleEl) titleEl.textContent = info.title;
    if (descEl) descEl.textContent = info.desc;

    // Highlight the matching personality type and make them clickable
    document.querySelectorAll('.dna-type-item').forEach(item => {
        const typeName = item.getAttribute('data-type');

        // Remove old classes and listeners by cloning
        const newItem = item.cloneNode(true);
        item.parentNode.replaceChild(newItem, item);

        const isActual = currentDna.includes(typeName);
        if (isActual) {
            newItem.classList.add('actual-personality');
            if (!newItem.querySelector('.actual-dna-badge')) {
                newItem.appendChild(createEl('div', { class: 'actual-dna-badge', text: 'Jij' }));
            }
        }

        // Initial active state matches the actual DNA
        newItem.classList.toggle('active', info.key === typeName);

        // Click listener to view other explanations
        newItem.addEventListener('click', () => {
            // Update active state
            document.querySelectorAll('.dna-type-item').forEach(el => el.classList.remove('active'));
            newItem.classList.add('active');

            // Find full type name key
            const fullTypeKey = Object.keys(dnaTypes).find(k => k.includes(typeName)) || 'Balanced Listener';
            const clickedInfo = dnaTypes[fullTypeKey];

            // Update Hero
            if (iconEl) iconEl.replaceChildren(createEl('i', { class: `fas ${clickedInfo.icon}` }));
            if (titleEl) titleEl.textContent = clickedInfo.title;
            if (descEl) descEl.textContent = clickedInfo.desc;
        });
    });

    // Render decorative blobs
    renderDnaVisualization();

    modal.classList.add('active');
}

function renderDnaVisualization() {
    const container = document.getElementById('dna-art-canvas');
    if (!container) return;
    container.innerHTML = '';

    // Animated colored blobs
    const colors = ['var(--viz-1)', 'var(--viz-2)', 'var(--viz-3)', 'var(--viz-4)'];
    for (let i = 0; i < 6; i++) {
        const blob = document.createElement('div');
        blob.className = 'dna-blob';
        const size = 60 + Math.random() * 100;
        blob.style.width = size + 'px';
        blob.style.height = size + 'px';
        blob.style.top = Math.random() * 80 + '%';
        blob.style.left = Math.random() * 80 + '%';
        blob.style.background = colors[i % colors.length];
        blob.style.animationDelay = (Math.random() * 4) + 's';
        blob.style.animationDuration = (15 + Math.random() * 10) + 's';
        container.appendChild(blob);
    }
}

function closeDnaModal() {
    document.getElementById('modal-dna-explanation').classList.remove('active');
}

// ==================== STORAGE MODAL ====================

const folderIcons = {
    'aftermovies': 'fa-film',
    'library': 'fa-book',
    'muziek': 'fa-music',
    'RAW': 'fa-image',
    'data': 'fa-database',
    'school': 'fa-graduation-cap',
    'RAW_DUPLICATES': 'fa-clone',
    'TRASH': 'fa-trash'
};

function getFolderIcon(folderName) {
    return folderIcons[folderName] || 'fa-folder';
}

async function openStorageModal() {
    await storageNavigateToPath(null);
}

async function storageNavigateToPath(folderPath) {
    const modal = document.getElementById('modal-storage-details');
    const loading = document.getElementById('storage-breakdown-loading');
    const content = document.getElementById('storage-breakdown-content');
    const list = document.getElementById('storage-folder-list');
    const filesList = document.getElementById('storage-files-list');
    const summaryCard = document.getElementById('storage-summary');
    const breadcrumb = document.getElementById('storage-breadcrumb');
    const breadcrumbPath = document.getElementById('storage-breadcrumb-path');
    const tabs = document.getElementById('storage-tabs');
    const totalDisplay = document.getElementById('storage-total-display');
    const largestDisplay = document.getElementById('storage-largest-display');

    modal.classList.add('active');
    loading.style.display = 'flex';
    content.style.display = 'none';

    try {
        const params = folderPath ? `?folder_path=${encodeURIComponent(folderPath)}` : '';
        let data;
        try {
            data = await fetchJSON(`${API_BASE}/api/stats/storage-breakdown-nested${params}`);
        } catch (err) {
            list.replaceChildren(errorState('Opslag laden mislukt', err, () => storageNavigateToPath(folderPath)));
            loading.style.display = 'none';
            content.style.display = 'block';
            return;
        }

        list.innerHTML = '';
        filesList.innerHTML = '';

        // Bytes per map (oudere servers gaven alleen GB met twee decimalen)
        const sizes = data.sizes || Object.fromEntries(
            Object.entries(data.breakdown || {}).map(([name, gb]) => [name, parseFloat(gb) * 2 ** 30]));
        const entries = Object.entries(sizes).sort((a, b) => b[1] - a[1]);
        const total = entries.reduce((sum, [, size]) => sum + size, 0);

        // Reset tab naar subfolders bij elke navigatie
        storageShowTab('subfolders');

        if (folderPath) {
            breadcrumb.style.display = 'flex';
            breadcrumbPath.textContent = `/ ${folderPath}`;
            tabs.style.display = 'flex';
            summaryCard.style.display = 'flex';
        } else {
            breadcrumb.style.display = 'none';
            tabs.style.display = 'none';
            summaryCard.style.display = 'flex';
        }

        totalDisplay.textContent = formatBytes(total);
        largestDisplay.textContent = entries.length > 0 ? `${entries[0][0]} (${formatBytes(entries[0][1])})` : '-';

        if (entries.length === 0) {
            list.replaceChildren(emptyState({ icon: 'fa-folder-open', title: 'Geen submappen',
                text: folderPath ? 'Deze map heeft geen submappen.' : 'Het archief heeft nog geen mappen.' }));
        }

        entries.forEach(([folder, size]) => {
            const percent = total > 0 ? (size / total * 100).toLocaleString('nl-NL', { maximumFractionDigits: 1 }) : '0';
            const newPath = folderPath ? `${folderPath}/${folder}` : folder;
            const li = createEl('li', { class: 'storage-folder-item', tabindex: '0', role: 'button',
                'aria-label': `${folder}, ${formatBytes(size)}: open`,
                onClick: () => storageNavigateToPath(newPath),
                onKeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); storageNavigateToPath(newPath); } } });
            li.innerHTML = html`
                <div class="storage-folder-header">
                    <span class="storage-folder-name"><i class="fas ${getFolderIcon(folder)}"></i>${folder}</span>
                    <span class="storage-folder-size">${formatBytes(size)}</span>
                </div>
                <div class="storage-progress-bar">
                    <div class="storage-progress-fill" style="width: ${total > 0 ? size / total * 100 : 0}%"></div>
                </div>
                <div class="storage-folder-percent"><span class="storage-folder-percent-value">${percent}%</span> van deze map</div>
            `;
            list.appendChild(li);
        });

        if (folderPath) {
            const filesData = await fetchJSON(`${API_BASE}/api/stats/largest-files?folder_path=${encodeURIComponent(folderPath)}&limit=20`)
                .catch(err => { toastFetchError('Grootste bestanden laden mislukt', err); return {}; });

            if (filesData.files) {
                filesData.files.forEach(file => {
                    const li = document.createElement('li');
                    li.className = 'storage-file-item';

                    const filename = file.name;
                    const ext = filename.split('.').pop().toLowerCase();
                    let fileIcon = 'fa-file';

                    if (['jpg', 'jpeg', 'png', 'gif', 'webp'].includes(ext)) fileIcon = 'fa-image';
                    else if (['mp4', 'mov', 'avi', 'mkv'].includes(ext)) fileIcon = 'fa-video';
                    else if (['mp3', 'wav', 'flac', 'aac'].includes(ext)) fileIcon = 'fa-music';
                    else if (['pdf', 'doc', 'docx', 'txt'].includes(ext)) fileIcon = 'fa-file-text';
                    else if (['zip', 'rar', '7z'].includes(ext)) fileIcon = 'fa-file-archive';

                    const displaySize = formatBytes(typeof file.size === 'number' ? file.size : file.size_mb * 2 ** 20);

                    li.innerHTML = html`
                        <div class="storage-file-header">
                            <span class="storage-file-name"><i class="fas ${fileIcon}"></i>${filename}</span>
                            <span class="storage-file-size">${displaySize}</span>
                        </div>
                        <div class="storage-file-path">${file.path}</div>
                    `;

                    filesList.appendChild(li);
                });
            }
        }

        loading.style.display = 'none';
        content.style.display = 'block';
    } catch (e) {
        console.error(e);
        loading.style.display = 'none';
        list.replaceChildren(errorState('Opslag laden mislukt', e, () => storageNavigateToPath(folderPath)));
        content.style.display = 'block';
    }
}

function storageNavigateToRoot() {
    storageNavigateToPath(null);
}

function storageShowTab(tabName) {
    const subfoldersBtn = document.querySelector('[data-tab="subfolders"]');
    const filesBtn = document.querySelector('[data-tab="largest-files"]');
    const subfoldersContent = document.getElementById('storage-tab-subfolders');
    const filesContent = document.getElementById('storage-tab-largest-files');

    subfoldersBtn.classList.remove('active');
    filesBtn.classList.remove('active');
    subfoldersContent.classList.remove('active');
    filesContent.classList.remove('active');

    if (tabName === 'subfolders') {
        subfoldersBtn.classList.add('active');
        subfoldersContent.classList.add('active');
    } else {
        filesBtn.classList.add('active');
        filesContent.classList.add('active');
    }
}

function closeStorageModal() {
    document.getElementById('modal-storage-details').classList.remove('active');
}

// ==================== GENERIC INSIGHT EXPLANATION ====================

function showInsightExplanation(insightKey) {
    const modal = document.getElementById('modal-insight-explanation');

    const insightDetails = {
        'record_day': {
            icon: 'fa-trophy',
            title: 'Recorddag',
            desc: 'De ene dag waarop je het allerlangst naar muziek luisterde.'
        },
        'top_binge': {
            icon: 'fa-repeat',
            title: 'Langste reeks',
            desc: 'Het nummer dat je het vaakst direct achter elkaar draaide, zonder iets anders ertussen.'
        },
        'mainstream_score': {
            icon: 'fa-chart-line',
            title: 'Ontdekker of vaste favorieten',
            desc: 'Gaat meer dan 75% van je luistertijd naar een handvol topartiesten, dan heb je vaste favorieten. Is het breed verspreid, dan ben je een brede ontdekker.'
        },
        'seasonal_mood': {
            icon: 'fa-cloud-sun',
            title: 'Seizoensstemming',
            desc: 'Of je in de wintermaanden of juist in de zomermaanden duidelijk meer luistert, op basis van je luistertijd per maand.'
        }
    };

    const info = insightDetails[insightKey];
    if (!info) return;

    const iconEl = document.getElementById('insight-modal-icon');
    const titleEl = document.getElementById('insight-modal-title');
    const descEl = document.getElementById('insight-modal-description');

    if (iconEl) iconEl.replaceChildren(createEl('i', { class: `fas ${info.icon}` }));
    if (titleEl) titleEl.textContent = info.title;
    if (descEl) descEl.textContent = info.desc;

    if (modal) modal.classList.add('active');
}

function closeInsightModal() {
    const modal = document.getElementById('modal-insight-explanation');
    if (modal) modal.classList.remove('active');
}

// ==================== SHOWS SETTINGS ====================

async function openSettingsModal() {
    // create modal if not exists
    let modal = document.getElementById('modal-show-settings');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'modal-show-settings';
        modal.className = 'modal'; // Centered by CSS
        modal.innerHTML = `
        <div class="modal-container settings-modal-content" role="dialog" aria-labelledby="show-settings-title">
            <div class="modal-header">
                <h2 class="modal-title" id="show-settings-title"><i class="fas fa-sliders" aria-hidden="true"></i> Bronnen beheren</h2>
                <button type="button" class="modal-close" onclick="closeSettingsModal()" aria-label="Sluiten" title="Sluiten (Esc)">&times;</button>
            </div>
            <div class="modal-body modal-body-list">
                <p class="settings-subtitle">Kies welke mappen in de map library meedoen aan Shows.</p>
                <div id="settings-folders-list" class="settings-list">
                    <div class="loading-spinner"></div>
                </div>
            </div>
        </div>
    `;
        document.body.appendChild(modal);
    }

    modal.classList.add('active');

    const list = document.getElementById('settings-folders-list');
    list.innerHTML = '<div class="loading-spinner"></div>';

    try {
        // Fetch both folder tree and settings
        const [foldersData, settingsData] = await Promise.all([
            fetchJSON(`${API_BASE}/api/folders`),
            fetchJSON(`${API_BASE}/api/shows/settings`)
        ]);

        const disabledFolders = settingsData.disabled_folders || [];

        // Helper to merge enabled state
        function addEnabledState(nodes) {
            if (!nodes) return;
            nodes.forEach(node => {
                node.enabled = !disabledFolders.includes(node.path);
                if (node.children) {
                    addEnabledState(node.children);
                }
            });
        }

        if (foldersData.folders) {
            // Only show library subfolders (shows come from library only)
            const libraryFolder = foldersData.folders.find(f => f.name.toLowerCase() === 'library');
            const showFolders = libraryFolder ? (libraryFolder.children || []) : [];
            addEnabledState(showFolders);
            renderSettingsTree(showFolders, list);
        } else {
            list.innerHTML = '<div class="inline-error">Ongeldige mappenlijst van de server.</div>';
        }

    } catch (e) {
        console.error("Error loading settings:", e);
        list.replaceChildren(createEl('div', { class: 'inline-error', text: `Instellingen laden mislukt: ${e.message}` }));
    }
}

function closeSettingsModal() {
    const modal = document.getElementById('modal-show-settings');
    if (modal) {
        modal.classList.remove('active');
    }
}

function renderSettingsTree(tree, container) {
    container.innerHTML = '';

    if (!tree) return;

    // Sort: Enabled first, then alphabetical
    tree.sort((a, b) => {
        if (a.enabled === b.enabled) return a.name.localeCompare(b.name);
        return a.enabled ? -1 : 1;
    });

    tree.forEach(item => {
        container.appendChild(createSettingsFolderElement(item));
    });
}

function createSettingsFolderElement(item) {
    const el = document.createElement('div');
    el.className = 'settings-tree-item';

    const hasChildren = item.children && item.children.length > 0;

    // Create Header
    const header = document.createElement('div');
    header.className = 'settings-tree-header';
    header.onclick = (e) => {
        // Prevent toggle when clicking specific controls if necessary
        if (e.target.closest('.switch')) return;

        if (hasChildren) {
            const childrenContainer = el.querySelector('.settings-tree-children');
            const chevron = header.querySelector('.settings-chevron');
            if (childrenContainer.style.display === 'none' || !childrenContainer.style.display) {
                childrenContainer.style.display = 'block';
                childrenContainer.classList.add('expanded');
                chevron.classList.add('expanded');
            } else {
                childrenContainer.style.display = 'none';
                childrenContainer.classList.remove('expanded');
                chevron.classList.remove('expanded');
            }
        }
    };

    header.innerHTML = html`
    <div class="settings-chevron ${hasChildren ? '' : 'hidden'}">
        <i class="fas fa-chevron-right" aria-hidden="true"></i>
    </div>
    <div class="settings-label">
        <i class="fas fa-folder${item.enabled ? '-open' : ''} ${item.enabled ? '' : 'is-off'}" aria-hidden="true"></i>
        <span>${item.name}</span>
    </div>
    <label class="switch">
        <input type="checkbox" ${item.enabled ? html`checked` : ''}>
        <span class="slider round"></span>
    </label>
`;
    const toggle = header.querySelector('input[type="checkbox"]');
    toggle.addEventListener('change', () => toggleSettingsFolder(item.path, toggle.checked));

    el.appendChild(header);

    // Create Children Container
    if (hasChildren && item.children && Array.isArray(item.children)) {
        const childrenContainer = document.createElement('div');
        childrenContainer.className = 'settings-tree-children';

        // Sort children
        item.children.sort((a, b) => a.name.localeCompare(b.name));

        item.children.forEach(child => {
            childrenContainer.appendChild(createSettingsFolderElement(child));
        });

        el.appendChild(childrenContainer);
    }

    return el;
}

async function toggleSettingsFolder(path, enabled) {
    try {
        await fetchJSON(`${API_BASE}/api/shows/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, enabled })
        });
        showToast('Instellingen', `Map ${enabled ? 'ingeschakeld' : 'uitgeschakeld'}`, 'success');
        reloadShows();
    } catch (e) {
        toastFetchError('Instelling opslaan mislukt', e);
    }
}


// ==================== CINEMATIC PLAYER ====================

// Video's die de browser zelf kan afspelen; andere formaten (avi, mkv, mts, ...) toont de
// slideshow als stilstaand beeld (de miniatuur). Audio heeft geen beeld en komt er niet in.
const SLIDE_VIDEO_TYPES = { mp4: 'video/mp4', m4v: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm' };

function slidePlaysAsVideo(file) {
    const ext = (file.name || '').split('.').pop().toLowerCase();
    const mime = SLIDE_VIDEO_TYPES[ext];
    return !!mime && document.createElement('video').canPlayType(mime) !== '';
}

class CinematicPlayer {
    constructor(story) {
        this.story = story;
        this.files = (story.files || []).filter(f => f && (f.type === 'photo' || f.type === 'video'));
        this.currentIndex = 0;
        this.isPlaying = true;
        this.timer = null;
        this.duration = 5000; // 5 seconds per slide


        this.createUI();
        this.renderSlide();
        this.startLoop();
    }

    createUI() {
        const overlay = document.createElement('div');
        overlay.id = 'cinematic-player';
        overlay.className = 'cinematic-overlay active';
        overlay.innerHTML = `
<div id="slide-container"></div>
 
<div class="player-controls-top">
    <div class="player-title" id="player-slide-title"></div>
    <button class="player-close-btn" onclick="activeShow.close()" aria-label="Slideshow sluiten" title="Sluiten (Esc)">&times;</button>
</div>
 
<div class="player-controls-bottom">
     <div class="player-meta" id="player-slide-meta"></div>
     
     <div class="player-progress-bar">
        <div class="player-progress-fill" id="player-progress"></div>
     </div>
 
     <div class="player-actions">
        <button class="player-btn" onclick="activeShow.prev()" aria-label="Vorige" title="Vorige (←)"><i class="fas fa-step-backward" aria-hidden="true"></i></button>
        <button class="player-btn" id="btn-play-pause" onclick="activeShow.togglePlay()" aria-label="Pauzeren of afspelen" title="Pauzeren / afspelen (spatie)"><i class="fas fa-pause" aria-hidden="true"></i></button>
        <button class="player-btn" onclick="activeShow.next()" aria-label="Volgende" title="Volgende (→)"><i class="fas fa-step-forward" aria-hidden="true"></i></button>
        
        <div class="player-divider"></div>
        <button class="player-btn move-file-btn" onclick="openMoveFileModal()" title="Verplaatsen" aria-label="Verplaatsen"><i class="fas fa-folder-open" aria-hidden="true"></i></button>
        <div class="player-divider"></div>
        
        <button class="player-btn vote-up" onclick="activeShow.vote('up')" aria-label="Mooi" title="Mooi"><i class="fas fa-heart" aria-hidden="true"></i></button>
        <button class="player-btn vote-down" onclick="activeShow.vote('down')" aria-label="Minder mooi" title="Minder mooi"><i class="fas fa-thumbs-down" aria-hidden="true"></i></button>
     </div>
</div>
`;
        document.body.appendChild(overlay);
        this.playerEl = overlay;

        // Keyboard support
        this.keyHandler = (e) => {
            if (isMoveModalOpen()) return; // toetsen zijn dan voor de mappenboom
            if (e.key === 'Escape') this.close();
            if (e.key === 'ArrowRight') this.next();
            if (e.key === 'ArrowLeft') this.prev();
            if (e.key === ' ') this.togglePlay();
        };
        document.addEventListener('keydown', this.keyHandler);
    }

    close() {
        this.stopLoop();
        document.removeEventListener('keydown', this.keyHandler);
        this.playerEl.classList.remove('active');
        setTimeout(() => this.playerEl.remove(), 500);
        activeShow = null;
    }

    startLoop() {
        if (!this.isPlaying) return;
        this.stopLoop();

        // Progress animation
        const bar = document.getElementById('player-progress');
        bar.style.transition = 'none';
        bar.style.width = '0%';
        setTimeout(() => {
            bar.style.transition = `width ${this.duration}ms linear`;
            bar.style.width = '100%';
        }, 50);

        this.timer = setTimeout(() => this.next(), this.duration);
    }

    stopLoop() {
        clearTimeout(this.timer);
        const bar = document.getElementById('player-progress');
        if (bar) {
            bar.style.transition = 'none';
            bar.style.width = '0%';
        }
    }

    resetProgress() {
        if (this.isPlaying) this.startLoop();
    }

    togglePlay() {
        this.isPlaying = !this.isPlaying;
        const icon = document.getElementById('btn-play-pause').querySelector('i');
        icon.className = this.isPlaying ? 'fas fa-pause' : 'fas fa-play';

        if (this.isPlaying) this.startLoop();
        else this.stopLoop();
    }

    next() {
        this.currentIndex = (this.currentIndex + 1) % this.files.length;
        this.renderSlide();
    }

    prev() {
        this.currentIndex = (this.currentIndex - 1 + this.files.length) % this.files.length;
        this.renderSlide();
    }

    async vote(type) {
        const file = this.files[this.currentIndex];
        if (!file) return;

        // Prevent voting more than once per slide view
        if (this._votedOnSlide) return;
        this._votedOnSlide = true;

        // Disable both vote buttons visually
        const upBtn = this.playerEl.querySelector('.vote-up');
        const downBtn = this.playerEl.querySelector('.vote-down');
        if (upBtn) upBtn.classList.add('vote-disabled');
        if (downBtn) downBtn.classList.add('vote-disabled');

        // Visual feedback
        const btn = this.playerEl.querySelector(`.vote-${type}`);
        btn.classList.add('voted');
        setTimeout(() => btn.classList.remove('voted'), 500);

        // Show floating effect
        this.showVoteEffect(type);

        // Optimistic UI Update (ELO)
        const change = type === 'up' ? 50 : -50;
        const oldRating = file.rating || 1000;
        const newRating = Math.max(100, oldRating + change);
        file.rating = newRating;

        // Update badge text if visible
        const metaEl = document.getElementById('player-slide-meta');
        if (metaEl) {
            const badge = metaEl.querySelector('.elo-rating-badge');
            if (badge) {
                badge.innerHTML = html`<i class="fas fa-star"></i> ELO: ${newRating}`;
                badge.classList.add('updated'); // Add a glow effect in CSS if desired
                setTimeout(() => badge.classList.remove('updated'), 1000);
            }
        }

        try {
            await fetchJSON(`${API_BASE}/api/shows/vote`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: file.path, vote: type })
            });
        } catch (e) {
            // Niet bewaard: de oude waardering terug, ook op het scherm
            file.rating = oldRating;
            const badge = metaEl && metaEl.querySelector('.elo-rating-badge');
            if (badge) badge.innerHTML = html`<i class="fas fa-star"></i> ELO: ${oldRating}`;
            toastFetchError('Stem niet bewaard', e);
        }
    }

    showVoteEffect(type) {
        const el = document.createElement('div');
        el.className = `vote-floater ${type === 'up' ? 'is-up' : 'is-down'}`;
        setHTML(el, type === 'up' ? html`<i class="fas fa-heart"></i>` : html`<i class="fas fa-thumbs-down"></i>`);
        this.playerEl.appendChild(el);
        setTimeout(() => el.remove(), 1000);
    }

    renderSlide() {
        const file = this.files[this.currentIndex];
        const container = document.getElementById('slide-container');
        const titleEl = document.getElementById('player-slide-title');
        const metaEl = document.getElementById('player-slide-meta');

        // Reset vote state for new slide
        this._votedOnSlide = false;
        const upBtn = this.playerEl.querySelector('.vote-up');
        const downBtn = this.playerEl.querySelector('.vote-down');
        if (upBtn) upBtn.classList.remove('vote-disabled');
        if (downBtn) downBtn.classList.remove('vote-disabled');

        // Remove old slides
        const oldSlide = container.querySelector('.cinematic-slide');

        const slide = document.createElement('div');
        slide.className = 'cinematic-slide';

        const isVideo = file.type === 'video' && slidePlaysAsVideo(file);

        if (isVideo) {
            // Videos: no loop, play fully before advancing
            const video = document.createElement('video');
            video.src = `${API_BASE}/api/file?path=${encodeURIComponent(file.path)}`;
            video.className = 'cinematic-media';
            video.autoplay = true;
            video.muted = false;
            video.loop = false;
            video.playsInline = true;

            // When video finishes, advance to next slide
            video.addEventListener('ended', () => {
                if (this.isPlaying && this.files[this.currentIndex] === file) this.next();
            });
            // Kan de browser hem toch niet afspelen: niet blijven hangen, door naar de volgende
            video.addEventListener('error', () => {
                if (this.isPlaying && this.files[this.currentIndex] === file) setTimeout(() => this.next(), 1500);
            });

            slide.appendChild(video);
        } else {
            // Foto (of een video die de browser niet afspeelt: dan de miniatuur). Lukt het
            // origineel niet (bijv. RAW of HEIC buiten Safari), dan ook de miniatuur.
            const kbEffect = Math.random() > 0.5 ? 'ken-burns-in' : 'ken-burns-out';
            const img = createEl('img', {
                class: `cinematic-media ${kbEffect}`,
                src: vaultFileUrl(file.type === 'video' ? 'thumb' : 'file', file.path),
            });
            img.addEventListener('error', () => {
                const thumb = vaultFileUrl('thumb', file.path);
                if (!img.src.endsWith(thumb)) img.src = thumb;
            }, { once: true });
            slide.appendChild(img);
        }

        container.appendChild(slide);

        // Force reflow and activate
        setTimeout(() => {
            slide.classList.add('active');
            if (oldSlide) {
                oldSlide.classList.remove('active');
                setTimeout(() => oldSlide.remove(), 1500);
            }
        }, 50);

        // Show folder name (from API or extracted from path)
        const folderName = file.folder || file.path.split(/[/\\\\]/).slice(-2, -1)[0] || 'Onbekende map';

        titleEl.textContent = folderName;

        metaEl.innerHTML = html`
<span class="player-meta-item"><i class="fas fa-folder" aria-hidden="true"></i> ${folderName}</span>
<span class="player-meta-item"><i class="fas fa-camera" aria-hidden="true"></i> ${file.name}</span>
<span class="player-meta-item"><i class="fas fa-calendar" aria-hidden="true"></i> ${new Date(file.modified * 1000).toLocaleDateString('nl-NL')}</span>
${file.size ? html`<span class="player-meta-item"><i class="fas fa-hard-drive" aria-hidden="true"></i> ${formatBytes(file.size)}</span>` : ''}
<div class="elo-rating-badge" title="Score (ELO): stijgt als je een foto mooi vindt">
    <i class="fas fa-star" aria-hidden="true"></i> ${file.rating || 1000}
</div>
`;

        // For videos: don't start the timer (video 'ended' event handles advancement)
        // For photos: use normal timer
        if (isVideo) {
            this.stopLoop(); // Don't auto-advance, wait for video to finish
            // Show indeterminate progress for video
            const bar = document.getElementById('player-progress');
            if (bar) {
                bar.style.transition = 'none';
                bar.style.width = '100%';
                bar.style.opacity = '0.3';
            }
        } else {
            const bar = document.getElementById('player-progress');
            if (bar) bar.style.opacity = '1';
            this.resetProgress();
        }
    }
}

function startShow(story) {
    if (activeShow) activeShow.close();
    const playable = (story.files || []).some(f => f && (f.type === 'photo' || f.type === 'video'));
    if (!playable) {
        showToast('Leeg', "Geen foto's of video's in deze show", 'warning');
        return;
    }
    activeShow = new CinematicPlayer(story);
}



// ==================== SPOTIFY WRAPPED ====================

let spotifyLoadSeq = 0; // alleen het antwoord op de laatste aanvraag telt (snel van jaar wisselen)

async function loadSpotifyStats() {
    const seq = ++spotifyLoadSeq;
    const loading = document.getElementById('spotify-loading');
    const content = document.getElementById('spotify-content');
    const yearSelect = document.getElementById('spotify-year-select');
    const year = yearSelect ? yearSelect.value : 'all';
    const url = `${API_BASE}/api/spotify/stats?year=${encodeURIComponent(year)}`;

    const populateYears = (data) => {
        if (yearSelect && yearSelect.options.length <= 1 && data.available_years) {
            data.available_years.forEach(y => {
                const opt = document.createElement('option');
                opt.value = y;
                opt.textContent = y;
                yearSelect.appendChild(opt);
            });
        }
    };
    const show = (data) => {
        showSpotifyError(null);
        if (loading) loading.style.display = 'none';
        if (content) content.style.display = 'block';
        renderSpotifyStats(data);
        populateYears(data);
    };

    // Gebruik cache als beschikbaar — instant render, geen spinner
    if (_prefetchCache[url]) {
        const data = await _prefetchCache[url];
        if (seq !== spotifyLoadSeq) return;
        if (data) { show(data); return; }
    }

    // Normale laadprocedure
    showSpotifyError(null);
    if (loading) loading.style.display = 'flex';
    if (content) content.style.display = 'none';

    try {
        const data = await fetchJSON(url);
        if (seq !== spotifyLoadSeq) return;
        _prefetchCache[url] = Promise.resolve(data);
        show(data);
    } catch (e) {
        if (seq !== spotifyLoadSeq) return;
        console.error("Spotify load error:", e);
        // De inhoud zelf blijft staan (alleen verborgen), zodat een nieuwe poging gewoon kan renderen
        if (loading) loading.style.display = 'none';
        if (content) content.style.display = 'none';
        showSpotifyError(e.message);
    }
}

// Foutmelding met een knop om het opnieuw te proberen; null verbergt hem
function showSpotifyError(message) {
    let box = document.getElementById('spotify-error');
    if (!message) {
        if (box) box.hidden = true;
        return;
    }
    if (!box) {
        box = createEl('div', { id: 'spotify-error', class: 'spotify-error', role: 'alert' });
        document.getElementById('spotify-content').before(box);
    }
    box.replaceChildren(
        createEl('i', { class: 'fas fa-exclamation-triangle' }),
        createEl('p', { text: `Spotify-gegevens konden niet worden geladen: ${message}` }),
        createEl('button', { class: 'btn btn-secondary', text: 'Opnieuw proberen', onClick: () => loadSpotifyStats() }));
    box.hidden = false;
}

// De live klok bij "Op deze dag" tikt alleen terwijl de Spotify-weergave open is
function stopSpotifyClock() {
    if (window._otdClockInterval) {
        clearInterval(window._otdClockInterval);
        window._otdClockInterval = null;
    }
}

function renderSpotifyStats(data) {
    // 1. Stats Cards
    updateText('stat-minutes', data.total_minutes.toLocaleString('nl-NL'));
    updateText('stat-top-artist', data.top_artists.length > 0 ? data.top_artists[0].name : '-');
    updateText('stat-plays', data.total_plays.toLocaleString('nl-NL'));
    updateText('stat-dna', spotifyLabel(data.listening_dna));
    const dnaEl = document.getElementById('stat-dna');
    if (dnaEl) dnaEl.dataset.dna = data.listening_dna || '';

    // 2. Lists: naast elkaar even lang, zodat er onder de kortste geen leeg vlak staat
    const shared = Math.min(data.top_artists.length, data.top_tracks.length) || undefined;
    renderSpotifyList('top-artists-list', data.top_artists.slice(0, shared), (item, i) => spotifyListItem({
        rank: `#${i + 1}`, name: item.name, meta: `${Math.round(item.ms / 60000).toLocaleString('nl-NL')} min`,
        icon: 'fab fa-spotify', query: item.name,
    }));

    renderSpotifyList('top-tracks-list', data.top_tracks.slice(0, shared), (item, i) => spotifyListItem({
        rank: `#${i + 1}`, name: item.name, meta: `${item.artist} • ${item.count}× gespeeld`,
        icon: 'fas fa-play', query: `${item.name} ${item.artist}`,
    }));

    // 2.5 New Insights
    if (data.new_insights) {
        const insights = data.new_insights;

        // Record Day
        if (insights.record_day) {
            updateText('stat-record-day-hours', `${formatNumberNl(insights.record_day.hours, 1)} uur`);
            updateText('stat-record-day-date', `Recorddag: ${formatDateNl(insights.record_day.date)}`);
        }

        // Binge
        if (insights.top_binge) {
            const bingeEl = document.getElementById('stat-top-binge');
            const binge = insights.top_binge;
            bingeEl.replaceChildren(createEl('a', {
                href: spotifySearchUrl(`${binge.track} ${binge.artist}`), target: '_blank', rel: 'noopener noreferrer',
                class: 'stat-link', text: binge.track,
                onClick: (e) => e.stopPropagation(),
            }));
            updateText('stat-top-binge-count', `${insights.top_binge.count}× achter elkaar`);
        } else {
            updateText('stat-top-binge', 'Geen reeksen');
        }

        // Mainstream
        if (insights.mainstream_score) {
            updateText('stat-mainstream-score', spotifyLabel(insights.mainstream_score));
            updateText('stat-mainstream-percent', `Top 5% van de artiesten = ${insights.mainstream_percent}% van het luisteren`);
        }

        // Seasonal Mood
        if (insights.seasonal_mood) {
            updateText('stat-seasonal-mood', spotifyLabel(insights.seasonal_mood));
        }

        // Discoveries
        const discoveriesContainer = document.getElementById('discoveries-container');
        if (!discoveriesContainer) {
            // (pagina zonder ontdekkingen-blok)
        } else if (insights.discoveries && insights.discoveries.length > 0) {
            discoveriesContainer.style.display = 'block';
            renderSpotifyList('discoveries-list', insights.discoveries, (item, i) => spotifyListItem({
                rank: `#${i + 1}`, name: item.name,
                meta: `Dit jaar voor het eerst • ${Math.round(item.ms / 60000).toLocaleString('nl-NL')} min`,
                icon: 'fab fa-spotify', query: item.name,
            }));
        } else {
            discoveriesContainer.style.display = 'none';
        }
    }
    const otdContainer = document.getElementById('on-this-day-container');
    const otdSubtitle = otdContainer ? otdContainer.querySelector('.section-subtitle') : null;
    const otdList = document.getElementById('on-this-day-list');

    // Set today's date and start live clock
    const otdDateEl = document.getElementById('otd-today-date');
    const otdClockEl = document.getElementById('otd-live-clock');
    if (otdDateEl) {
        const now = new Date();
        const opts = { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' };
        otdDateEl.textContent = now.toLocaleDateString('nl-NL', opts);
    }
    if (otdClockEl) {
        stopSpotifyClock();
        const tickClock = () => {
            const n = new Date();
            otdClockEl.textContent = n.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        };
        tickClock();
        if (!document.getElementById('section-spotify').classList.contains('hidden')) {
            window._otdClockInterval = setInterval(tickClock, 1000);
        }
    }
    if (!otdContainer || !otdList) {
        // (pagina zonder "Op deze dag"-blok)
    } else if (data.on_this_day && data.on_this_day.length > 0) {
        otdContainer.style.display = 'block';
        otdList.innerHTML = '';

        // Pas subtitle aan afhankelijk van all-time of per-jaar
        if (otdSubtitle) {
            if (data.year === 'all') {
                otdSubtitle.textContent = 'Alle nummers die je op deze datum speelde over de jaren, gesorteerd op tijdstip';
            } else {
                otdSubtitle.textContent = `Wat je op deze datum speelde in ${data.year}, gesorteerd op tijdstip`;
            }
        }

        // Gesorteerd op tijdstip door backend – zoek het item het dichtst bij nu
        const now = new Date();
        const nowMinutes = now.getHours() * 60 + now.getMinutes();
        let closestIndex = 0;
        let closestDiff = Infinity;

        data.on_this_day.forEach((play, i) => {
            const [h, m] = play.time.split(':').map(Number);
            const playMinutes = h * 60 + m;
            const diff = Math.abs(playMinutes - nowMinutes);
            if (diff < closestDiff) {
                closestDiff = diff;
                closestIndex = i;
            }
        });

        data.on_this_day.forEach((play, i) => {
            const el = document.createElement('div');
            el.className = 'on-this-day-item clickable';
            if (i === closestIndex) el.classList.add('closest-play');
            el.role = "button";
            el.onclick = (e) => {
                e.stopPropagation();
                openSpotifySearch(`${play.track} ${play.artist}`);
            };
            el.innerHTML = html`
        <div class="item-rank">${play.year}</div>
        <div class="item-info">
            <div class="item-name">${play.track}</div>
            <div class="item-meta">${play.artist}</div>
        </div>
        <div class="otd-play-time"><i class="far fa-clock" aria-hidden="true"></i> ${play.time}</div>
    `;
            otdList.appendChild(el);
        });

        // Centreer het dichtstbijzijnde item in de scrollbare lijst
        requestAnimationFrame(() => {
            const closestEl = otdList.querySelector('.closest-play');
            if (closestEl) {
                const containerWidth = otdList.offsetWidth;
                const itemLeft = closestEl.offsetLeft;
                const itemWidth = closestEl.offsetWidth;
                const scrollTarget = itemLeft - (containerWidth / 2) + (itemWidth / 2);
                otdList.scrollTo({ left: scrollTarget, behavior: 'smooth' });
            }
        });
    } else {
        otdContainer.style.display = 'none';
    }

    // 4. Charts - Simple Bar Implementation
    const months = ['januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september',
        'oktober', 'november', 'december'];
    renderSimpleChart('monthly-chart', data.monthly_activity,
        i => ['jan', 'feb', 'mrt', 'apr', 'mei', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'][i],
        (i, n) => `${months[i]}: ${formatNumberNl(n)} nummers`);
    renderSimpleChart('hourly-chart', data.hourly_activity,
        i => (i % 6 === 0 ? `${i}:00` : ''),
        (i, n) => `${i}:00–${(i + 1) % 24}:00: ${formatNumberNl(n)} nummers`);
}

// De server noemt luistertypes in het Engels (met een emoji); op het scherm staat Nederlands
const SPOTIFY_LABELS_NL = {
    'Early Bird': 'Vroege vogel', 'Daydreamer': 'Dagdromer', 'Evening Chiller': 'Avondgenieter',
    'Night Owl': 'Nachtuil', 'Balanced Listener': 'Evenwichtige luisteraar', 'Balanced': 'In balans',
    'Mainstream Repeater': 'Vaste favorieten', 'Broad Explorer': 'Brede ontdekker',
    'Winter Hibernator': 'Winterluisteraar', 'Summer Vibes': 'Zomerluisteraar', 'Consistent': 'Het hele jaar door',
};

function spotifyLabel(value) {
    const text = String(value || '').trim();
    const key = Object.keys(SPOTIFY_LABELS_NL).sort((a, b) => b.length - a.length).find(k => text.startsWith(k));
    return key ? SPOTIFY_LABELS_NL[key] : (text || '-');
}

function updateText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function renderSpotifyList(id, items, buildItem) {
    const list = document.getElementById(id);
    if (!list) return;
    list.replaceChildren(...items.map((item, i) => buildItem(item, i)));
}

// Eén klikbare regel in een Spotify-lijst; namen komen uit de export en zijn altijd tekst
function spotifyListItem({ rank, name, meta, icon, query }) {
    return createEl('div', {
        class: 'spotify-list-item clickable', role: 'button',
        onClick: (e) => { e.stopPropagation(); openSpotifySearch(query); },
    },
        createEl('div', { class: 'item-rank', text: rank }),
        createEl('div', { class: 'item-info' },
            createEl('div', { class: 'item-name', text: name }),
            createEl('div', { class: 'item-meta', text: meta })),
        createEl('div', { class: 'action-icon' }, createEl('i', { class: icon })));
}

// Een nummer of artiest opzoeken op Spotify: alleen na een klik, in een nieuw tabblad
function spotifySearchUrl(query) {
    return `https://open.spotify.com/search/${encodeURIComponent(query)}`;
}

function openSpotifySearch(query) {
    openExternal(spotifySearchUrl(query));
}

// Staafjes met een as eronder (axisLabel(i): '' voor geen label) en per staaf een tip
// (tip(i, waarde)): bij hover en voor schermlezers, zonder dubbele tooltip van de browser
function renderSimpleChart(id, data, axisLabel, tip) {
    const el = document.getElementById(id);
    if (!el) return;

    const max = Math.max(...data, 1);
    el.replaceChildren(...data.map((val, i) => createEl('div', {
        class: 'chart-bar',
        style: `height: ${(val / max) * 100}%`,
        role: 'img',
        'aria-label': tip(i, val),
        dataset: { tip: tip(i, val) },
    })));

    let axis = el.nextElementSibling;
    if (!axis || !axis.classList.contains('chart-axis')) {
        axis = createEl('div', { class: 'chart-axis', 'aria-hidden': 'true' });
        el.after(axis);
    }
    axis.replaceChildren(...data.map((_, i) => createEl('span', { text: axisLabel(i) })));
}

// ==================== DA VAI OLLAMA CHATBOT ====================

// Het taalmodel kies je in de chat (keuzelijst bovenin); de server bewaart de keuze en
// gebruikt standaard een licht model. Hier alleen de weergave van de keuze en de status.
let chatModelState = { current: null, models: [], reachable: false, error: null };

function setChatStatus(text, state, title) {
    document.querySelectorAll('[data-chat-status]').forEach(el => {
        el.textContent = text;
        el.dataset.state = state;
        el.title = title || '';
    });
}

function renderChatModelSelects() {
    const { current, models, reachable, error } = chatModelState;
    const names = (models || []).map(m => m.name);
    const installed = names.includes(current);
    document.querySelectorAll('[data-chat-model]').forEach(select => {
        const options = (models || []).map(m => createEl('option', {
            value: m.name,
            text: m.parameter_size ? `${m.name} (${m.parameter_size})` : m.name,
        }));
        if (current && !installed) options.unshift(createEl('option', { value: current, text: `${current} (niet geïnstalleerd)` }));
        select.replaceChildren(...options);
        if (current) select.value = current;
        select.disabled = !reachable || names.length === 0;
    });
    if (!reachable) setChatStatus('Ollama offline', 'error', error || 'Start de Ollama-app');
    else if (!installed) setChatStatus('Model ontbreekt', 'warning', `Kies een ander model, of installeer het met: ollama pull ${current}`);
    else setChatStatus('Online', 'ok', `Taalmodel: ${current}`);
}

async function loadChatModels() {
    try {
        chatModelState = await fetchJSON(`${API_BASE}/api/chat/models`);
        renderChatModelSelects();
    } catch (e) {
        setChatStatus('Niet bereikbaar', 'error', e.message);
    }
}

async function chooseChatModel(model) {
    if (!model || model === chatModelState.current) return;
    try {
        const data = await fetchJSON(`${API_BASE}/api/chat/model`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model }),
        });
        chatModelState.current = data.current;
        showToast('Pasha', `Gebruikt nu het model ${data.current}`, 'success');
    } catch (e) {
        showToast('Model niet gekozen', e.message, 'error');
    }
    renderChatModelSelects();
}

// Open een enkel bestand uit het archief in de modal viewer. Het pad (van het taalmodel of
// uit een zoekresultaat) wordt nooit zelf getoond: de server moet het bestand in de lijst
// van zijn map teruggeven (die controleert dat de map in het archief ligt), en alleen dat
// bestandsobject van de server wordt geopend.
async function openFileInViewer(path) {
    const cut = typeof path === 'string' ? path.lastIndexOf('/') : -1;
    const notFound = () => showToast('Niet geopend', 'Dit bestand staat niet in het archief', 'warning');
    if (cut < 0 || cut === path.length - 1) { notFound(); return; }
    const folder = path.slice(0, cut) || '/';
    const name = path.slice(cut + 1).normalize('NFC');
    try {
        const data = await fetchJSON(`${API_BASE}/api/media?folder=${encodeURIComponent(folder)}`);
        const file = (data.files || []).find(f =>
            ['photo', 'video', 'audio'].includes(f.type) && f.name.normalize('NFC') === name);
        if (!file) { notFound(); return; }
        // Eigen lijst van één bestand: de Files-weergave blijft zoals hij was
        openViewer([file], 0);
    } catch (e) {
        // Niet in (of buiten) het archief: niet gevonden; anders de echte reden (schijf weg, ...)
        if ([400, 403, 404].includes(e.status)) notFound();
        else toastFetchError('Niet geopend', e);
    }
}

// Foto's in een chat-antwoord openen; via delegatie, zodat het ook werkt na het kopiëren
// van de berichten tussen de gewone en de schermvullende chat
document.addEventListener('click', (e) => {
    const img = e.target.closest('.chat-messages img[data-path]');
    if (img) openFileInViewer(img.dataset.path);
});

// ==================== CHAT-ACTIES ====================
// Het taalmodel bestuurt de app met <<ACTION: {...}>> in zijn antwoord. Die tekst is niet te
// vertrouwen: mapnamen en Spotify-titels in de context kunnen instructies bevatten. Daarom
// wordt alleen een actie uit deze lijst uitgevoerd, met precies deze argumenten en types.

const CHAT_VIEWS = ['home', 'viewer', 'duplicates', 'spotify', 'shows', 'keurer', 'timeline'];

function isChatText(value, maxLength) {
    return typeof value === 'string' && value.trim() !== '' && value.length <= maxLength
        && !/[\u0000-\u001f\u007f]/.test(value);
}

function isChatDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const d = new Date(`${value}T00:00:00Z`);
    return !isNaN(d) && d.toISOString().slice(0, 10) === value;
}

const CHAT_ACTIONS = {
    open_tab: { target: v => CHAT_VIEWS.includes(v) },
    scan_duplicates: {},
    open_folder: { target: v => isChatText(v, 255) },
    show_photos: { date: isChatDate },
    search_photos: { query: v => isChatText(v, 100) },
    open_file: { path: v => isChatText(v, 4096) },
};

// JSON uit één ACTION-tag -> { action, ...argumenten } of null als iets niet klopt
function validateChatAction(json) {
    let data;
    try { data = JSON.parse(json); } catch (_) { return null; }
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
    const spec = Object.prototype.hasOwnProperty.call(CHAT_ACTIONS, data.action) ? CHAT_ACTIONS[data.action] : null;
    if (!spec) return null;
    const action = { action: data.action };
    for (const [key, isValid] of Object.entries(spec)) {
        if (!isValid(data[key])) return null;
        action[key] = data[key];
    }
    return action;
}

const CHAT_ACTION_TAG = /<<ACTION:\s*(\{[\s\S]*?\})\s*>>/g;

// Haalt de ACTION-tags uit het antwoord. Alleen de eerste geldige actie wordt uitgevoerd.
function parseChatReply(text) {
    let action = null;
    const clean = text.replace(CHAT_ACTION_TAG, (_, json) => {
        const parsed = validateChatAction(json);
        if (parsed && !action) action = parsed;
        else if (!parsed) console.warn('Chat-actie genegeerd:', json.slice(0, 200));
        return '';
    }).replace(/<<ACTION[\s\S]*$/, '').trim();
    return { text: clean, action };
}

// Zoekt een map uit de mappenboom van de server (dus binnen het archief) op naam of pad
function findFolderInTree(target) {
    const wanted = target.normalize('NFC').toLowerCase();
    let byName = null;
    const walk = (nodes) => {
        for (const node of nodes || []) {
            if (node.path.normalize('NFC') === target.normalize('NFC')) return node;
            if (!byName && node.name.normalize('NFC').toLowerCase() === wanted) byName = node;
            const found = walk(node.children);
            if (found) return found;
        }
        return null;
    };
    return walk(folderTree) || byName;
}

function chatBubble(content) {
    const bubble = createEl('div', { class: 'message-bubble' }, content);
    return createEl('div', { class: 'chat-message ai-message' }, bubble);
}

function chatLoadingBubble() {
    return chatBubble(html`<div class="message-loading"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>`);
}

function chatPhotoGrid(photos, size, titleOf) {
    const grid = createEl('div', { class: 'chat-photo-grid' });
    photos.forEach(p => grid.appendChild(createEl('img', {
        class: 'chat-photo',
        src: vaultFileUrl('thumb', p.path),
        style: `width: ${size}px; height: ${size}px;`,
        title: titleOf(p),
        dataset: { path: p.path },
    })));
    const message = chatBubble(grid);
    message.firstChild.classList.add('message-bubble-bare');
    return message;
}

// Toont foto's van de server in de chat (show_photos en search_photos)
function chatShowPhotos(url, size, titleOf, emptyText, limit) {
    const chatMessages = document.getElementById('chat-messages');
    const loading = chatLoadingBubble();
    chatMessages.appendChild(loading);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    fetchJSON(url)
        .then(data => {
            loading.remove();
            const photos = (data.photos || []).slice(0, limit);
            // complete: false = de server stopte na zijn tijdsbudget, niet het hele archief is bekeken
            const partly = data.complete === false
                ? ' Ik heb binnen de tijd niet het hele archief kunnen doorzoeken.' : '';
            chatMessages.appendChild(photos.length > 0
                ? chatPhotoGrid(photos, size, titleOf)
                : chatBubble(emptyText + partly));
            chatMessages.scrollTop = chatMessages.scrollHeight;
        })
        .catch(err => {
            // Nooit stil verdwijnen: de melding komt in de chat
            loading.remove();
            chatMessages.appendChild(chatBubble(createEl('span', { class: 'chat-error-text', text: `Foto's ophalen mislukt: ${err.message}` })));
            chatMessages.scrollTop = chatMessages.scrollHeight;
        });
}

// Een scan leest en hasht een hele map: het taalmodel mag hem voorstellen, maar alleen een
// klik van de gebruiker start hem (als achtergrondtaak, zie jobs.js)
function chatOfferDuplicateScan() {
    const chatMessages = document.getElementById('chat-messages');
    const folder = (typeof selectedFolder === 'string' && selectedFolder) || currentFolder || null;
    if (!folder) {
        chatMessages.appendChild(chatBubble('Kies eerst een map in het tabblad Duplicaten; dan kan ik hem scannen.'));
        chatMessages.scrollTop = chatMessages.scrollHeight;
        return;
    }
    const name = folder.slice(folder.lastIndexOf('/') + 1) || folder;
    const start = createEl('button', { class: 'btn btn-primary', style: 'margin-top: 8px;', text: 'Scan starten' });
    start.addEventListener('click', () => {
        start.disabled = true;
        const fullscreenChat = document.getElementById('chat-fullscreen-modal');
        if (fullscreenChat && fullscreenChat.classList.contains('active')) closeChatFullscreen();
        switchMainView('duplicates');
        setSelectedFolder(folder);
        scanSelectedFolder();
    }, { once: true });
    chatMessages.appendChild(chatBubble(createEl('div', {},
        createEl('div', { text: `Duplicaten zoeken in "${name}"?` }), start)));
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function executeAIAction(actionData) {
    if (!actionData || !actionData.action) return;

    switch (actionData.action) {
        case 'open_tab':
            switchMainView(actionData.target);
            break;

        case 'scan_duplicates':
            chatOfferDuplicateScan();
            break;

        case 'open_folder': {
            const folder = findFolderInTree(actionData.target);
            if (!folder) {
                showToast('Map niet gevonden', actionData.target, 'warning');
                break;
            }
            // De volledige chat ligt boven alle tabbladen; sluiten zodat de map te zien is
            const fullscreenChat = document.getElementById('chat-fullscreen-modal');
            if (fullscreenChat && fullscreenChat.classList.contains('active')) closeChatFullscreen();
            switchMainView('viewer');
            selectFolder(folder.path);
            break;
        }

        case 'show_photos':
            chatShowPhotos(`${API_BASE}/api/stats/photos-by-date?date=${encodeURIComponent(actionData.date)}`,
                100, p => p.path.split('/').pop(),
                "Ik kon helaas geen foto's vinden uit mijn archief voor deze exacte datum.", 10);
            break;

        case 'search_photos':
            chatShowPhotos(`${API_BASE}/api/stats/search-photos?query=${encodeURIComponent(actionData.query)}&limit=5`,
                120, p => p.name,
                `Ik kon geen foto's vinden met de naam "${actionData.query}".`, 5);
            break;

        case 'open_file':
            openFileInViewer(actionData.path);
            break;

        default:
            console.warn('Unknown AI action:', actionData.action);
    }
}

window.chatHistory = [];

function handleChatKeyPress(e) {
    if (e.key === 'Enter') {
        sendChatMessage();
    }
}

function handleChatKeyPressFullscreen(e) {
    if (e.key === 'Enter') {
        sendChatMessageFullscreen();
    }
}

function sendChatMessage() {
    return sendChat({
        inputEl: document.getElementById('chat-input'),
        btn: document.querySelector('#chat-home-slot ~ .chat-input-container .chat-send-btn'),
    });
}

// Een voorbeeldvraag onder de chat op Home: meteen versturen
function askPasha(question) {
    // De voorbeeldvragen verhuizen mee naar de schermvullende chat
    const modal = document.getElementById('chat-fullscreen-modal');
    const fullscreen = modal && modal.classList.contains('active');
    const input = document.getElementById(fullscreen ? 'chat-input-fullscreen' : 'chat-input');
    if (!input || input.disabled) return;
    input.value = question.trim();
    if (fullscreen) sendChatMessageFullscreen();
    else sendChatMessage();
}

function sendChatMessageFullscreen() {
    return sendChat({
        inputEl: document.getElementById('chat-input-fullscreen'),
        btn: document.querySelector('.chat-fullscreen-modal .chat-send-btn'),
    });
}

// Stuurt het bericht en toont het antwoord terwijl het binnenkomt. Alles wat het model
// schrijft komt als tekst (textContent) op het scherm, nooit als HTML. Er is één
// berichtenlijst (#chat-messages); die verhuist tussen de gewone en de schermvullende chat,
// dus antwoorden en foto's van acties staan altijd in de chat die je ziet.
async function sendChat({ inputEl, btn }) {
    const messagesContainer = document.getElementById('chat-messages');
    const message = inputEl.value.trim();
    if (!message) return;

    inputEl.value = '';
    inputEl.disabled = true;
    if (btn) btn.disabled = true;
    const suggestions = document.getElementById('chat-suggestions');
    if (suggestions) suggestions.hidden = true;

    const userMsg = createEl('div', { class: 'chat-message user-message' },
        createEl('div', { class: 'message-bubble message-text', text: message }));
    messagesContainer.appendChild(userMsg);

    window.chatHistory.push({ role: 'user', content: message });
    // Houd context klein: max 10 berichten (5 user + 5 assistant)
    if (window.chatHistory.length > 10) {
        window.chatHistory.shift();
    }
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    // AI bubble met loading dots — wordt live gevuld via streaming
    const aiMsg = chatLoadingBubble();
    const bubble = aiMsg.firstChild;
    messagesContainer.appendChild(aiMsg);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    const showText = (text) => {
        bubble.classList.add('message-text');
        bubble.textContent = text;
    };
    let failed = false;
    let finished = false;
    const showError = (text) => {
        failed = true;
        // Wat er al binnen was blijft staan, met de melding eronder
        const parts = fullText ? [createEl('div', { class: 'message-text', text: fullText.replace(/<<ACTION[\s\S]*$/, '').trimEnd() })] : [];
        bubble.classList.remove('message-text');
        bubble.replaceChildren(...parts, createEl('div', { class: 'chat-error-text', text }));
        // Geen antwoord: de vraag telt niet mee in de geschiedenis (anders twee vragen achter elkaar)
        const last = window.chatHistory[window.chatHistory.length - 1];
        if (last && last.role === 'user' && last.content === message) window.chatHistory.pop();
    };

    let fullText = '';

    try {
        const response = await fetch(`${API_BASE}/api/chat/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages: window.chatHistory })
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            showError(apiErrorMessage(err, `Serverfout (${response.status})`));
            return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let sseBuffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            sseBuffer += decoder.decode(value, { stream: true });
            const lines = sseBuffer.split('\n');
            sseBuffer = lines.pop(); // Bewaar onvolledige regel

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                let event;
                try { event = JSON.parse(line.slice(6)); } catch (_) { continue; } // ongeldige JSON negeren

                if (event.error) {
                    showError(String(event.error));
                    // Ollama weg of model ontbreekt: de status bovenin bijwerken
                    if (event.code === 'ollama_down' || event.code === 'model_missing') loadChatModels();
                    return;
                }

                if (typeof event.token === 'string' && event.token) {
                    fullText += event.token;
                    // Een half binnengekomen ACTION-tag niet laten zien
                    showText(fullText.replace(/<<ACTION[\s\S]*$/, '').trimEnd());
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                }

                if (event.done) {
                    finished = true;
                    const reply = parseChatReply(fullText);
                    fullText = reply.text;
                    showText(fullText);
                    if (reply.action) setTimeout(() => executeAIAction(reply.action), 1000);
                    window.chatHistory.push({ role: 'assistant', content: fullText });
                }
            }
        }

        // De verbinding sloot zonder 'done' (server herstart, netwerk weg): nooit eindeloos laden
        if (!finished && !failed) showError('Het antwoord stopte halverwege. Probeer het opnieuw.');
    } catch (e) {
        console.error('Chat error:', e);
        showError('De DataVault-server is niet bereikbaar. Draait de server nog?');
    } finally {
        inputEl.disabled = false;
        if (btn) btn.disabled = false;
        inputEl.focus();
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
}

function openChatFullscreen() {
    const modal = document.getElementById('chat-fullscreen-modal');
    if (modal) {
        moveChatMessages('chat-fullscreen-slot');
        modal.classList.add('active');
        document.addEventListener('keydown', handleFullscreenEscape);
        setTimeout(() => {
            const input = document.getElementById('chat-input-fullscreen');
            if (input) input.focus();
        }, 100);
    }
}

function closeChatFullscreen() {
    const modal = document.getElementById('chat-fullscreen-modal');
    if (modal) {
        moveChatMessages('chat-home-slot');
        modal.classList.remove('active');
        document.removeEventListener('keydown', handleFullscreenEscape);
    }
}

function handleFullscreenEscape(e) {
    // Escape in de viewer (die boven de chat opent) sluit eerst alleen de viewer
    if (e.key === 'Escape' && !isViewerOpen()) {
        closeChatFullscreen();
    }
}

// Verhuist de ene berichtenlijst (dezelfde DOM-knopen, met hun klikgedrag) naar een houder
function moveChatMessages(slotId) {
    const messages = document.getElementById('chat-messages');
    const suggestions = document.getElementById('chat-suggestions');
    const slot = document.getElementById(slotId);
    if (messages && slot && messages.parentElement !== slot) {
        slot.appendChild(messages);
        // De voorbeeldvragen (tot het eerste bericht) staan steeds onder de berichten
        if (suggestions) slot.appendChild(suggestions);
        messages.scrollTop = messages.scrollHeight;
    }
}
