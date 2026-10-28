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
let currentModalIndex = -1;
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

function setTheme(theme, skipSave = false) {
    if (!skipSave) {
        localStorage.setItem('theme', theme);
    }

    const html = document.documentElement;

    if (theme === 'auto') {
        html.removeAttribute('data-theme');
    } else {
        html.setAttribute('data-theme', theme);
    }

    // Update UI
    document.getElementById('theme-label').textContent =
        theme === 'auto' ? 'Auto' : theme.charAt(0).toUpperCase() + theme.slice(1);

    // Update active state
    document.querySelectorAll('.theme-option').forEach(opt => {
        opt.classList.toggle('active', opt.dataset.theme === theme);
    });

    closeThemeDropdown();
}

function toggleThemeDropdown() {
    const dropdown = document.getElementById('theme-dropdown');
    dropdown.classList.toggle('open');
}

function closeThemeDropdown() {
    document.getElementById('theme-dropdown').classList.remove('open');
}

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
    if (!e.target.closest('.theme-toggle')) {
        closeThemeDropdown();
    }
});

// ==================== VIEW SWITCHING ====================

function switchMainView(view) {
    // Update tabs
    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.view === view);
    });

    // Update sections
    document.getElementById('section-home').classList.toggle('active', view === 'home');
    document.getElementById('section-home').classList.toggle('hidden', view !== 'home');

    document.getElementById('section-viewer').classList.toggle('active', view === 'viewer');
    document.getElementById('section-viewer').classList.toggle('hidden', view !== 'viewer');

    document.getElementById('section-duplicates').classList.toggle('active', view === 'duplicates');
    document.getElementById('section-duplicates').classList.toggle('hidden', view !== 'duplicates');

    document.getElementById('section-spotify').classList.toggle('active', view === 'spotify');
    document.getElementById('section-spotify').classList.toggle('hidden', view !== 'spotify');

    document.getElementById('section-shows').classList.toggle('active', view === 'shows');
    document.getElementById('section-shows').classList.toggle('hidden', view !== 'shows');

    document.getElementById('section-keurer').classList.toggle('active', view === 'keurer');
    document.getElementById('section-keurer').classList.toggle('hidden', view !== 'keurer');

    document.getElementById('section-timeline').classList.toggle('active', view === 'timeline');
    document.getElementById('section-timeline').classList.toggle('hidden', view !== 'timeline');

    // Show/hide sidebar
    document.getElementById('sidebar').style.display = view === 'viewer' ? 'flex' : 'none';

    // Clear search
    document.getElementById('global-search').value = '';

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
    } else if (view === 'keurer') {
        if (typeof keurerInit === 'function') keurerInit();
    } else if (view === 'timeline') {
        if (typeof timelineInit === 'function') timelineInit();
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
    container.innerHTML = '<div class="loading-spinner"></div>';
    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error('Failed to load shows');
        const stories = await response.json();
        _prefetchCache[url] = Promise.resolve(stories);
        renderShows(stories);
    } catch (e) {
        console.error('Error loading shows:', e);
        container.innerHTML = `
            <div class="error-state">
                <i class="fas fa-exclamation-triangle"></i>
                <p>Could not load shows. Please check your connection.</p>
                <button class="btn btn-secondary" onclick="loadShows()">Try Again</button>
            </div>
        `;
    }
}

async function reloadShows() {
    // Cache wissen zodat we vers data ophalen
    delete _prefetchCache[`${API_BASE}/api/shows/stories`];
    const btn = document.getElementById('btn-reload-shows');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-sync fa-spin"></i> Reloading...';
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
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-folder-open"></i>
                <p>No folder shows found.</p>
                <button class="btn btn-primary" onclick="openSettingsModal()">Manage Sources</button>
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <div class="shows-header" style="display: flex; justify-content: flex-end; gap: 10px; margin-bottom: 20px;">
            <button class="btn btn-secondary" id="btn-shuffle-shows" onclick="shuffleShows()">
                <i class="fas fa-random"></i> Shuffle Shows
            </button>
            <button class="btn btn-secondary" onclick="openSettingsModal()">
                <i class="fas fa-sliders-h"></i> Manage Sources
            </button>
        </div>
    `;

    // 1. Hero Section (Featured - Random from Vault)
    if (featured && featured.files && featured.files.length > 0) {
        const hero = document.createElement('div');
        hero.className = 'shows-hero';
        const cover = featured.files[0];
        hero.onclick = () => startShow(featured);

        hero.innerHTML = html`
            <img src="${vaultFileUrl('thumb', cover.path)}" class="shows-hero-img">
            <div class="shows-hero-content">
                <span class="shows-hero-tag">Featured</span>
                <h2 class="shows-hero-title">${featured.title}</h2>
                <p class="shows-hero-subtitle">${featured.subtitle}</p>
                <button class="btn btn-primary btn-lg">
                    <i class="fas fa-play" style="margin-right: 10px;"></i> Watch Now
                </button>
            </div>
        `;
        container.appendChild(hero);
    }

    // 2. Folder Shows Grid
    if (shows && shows.length > 0) {
        const gridTitle = document.createElement('h3');
        gridTitle.className = 'section-title';
        gridTitle.textContent = 'Collections';
        gridTitle.style.marginTop = '40px';
        container.appendChild(gridTitle);

        const grid = document.createElement('div');
        grid.className = 'shows-grid-folders';
        grid.style.display = 'grid';
        grid.style.gridTemplateColumns = 'repeat(auto-fill, minmax(200px, 1fr))';
        grid.style.gap = '20px';

        shows.forEach(show => {
            const card = document.createElement('div');
            card.className = 'show-card folder-card';
            card.onclick = () => playFolderShow(show);

            let thumbUrl = '';
            if (show.preview) {
                thumbUrl = vaultFileUrl('thumb', show.preview.path);
            }

            // Full thumbnail with transparent overlay + text shadow for readability
            card.innerHTML = html`
                <div class="show-card-image-wrapper" style="position: relative; height: 180px; overflow: hidden; border-radius: 12px;">
                     ${thumbUrl ? html`<img src="${thumbUrl}" class="show-card-thumb" style="width: 100%; height: 100%; object-fit: cover;">` : html`<div class="no-thumb" style="display:flex;align-items:center;justify-content:center;height:100%;color:#666;background:var(--bg-tertiary);">📁</div>`}
                    <div style="position:absolute;bottom:0;left:0;right:0;padding:10px 12px;background:linear-gradient(to top, rgba(0,0,0,0.45) 0%, transparent 100%);">
                        <h4 style="margin:0;font-size:14px;color:#fff;text-shadow:0 1px 4px rgba(0,0,0,0.8),0 0 8px rgba(0,0,0,0.5);">${show.title}</h4>
                        <span style="font-size:11px;color:rgba(255,255,255,0.75);text-shadow:0 1px 3px rgba(0,0,0,0.8);">Folder</span>
                    </div>
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
        btn.innerHTML = '<i class="fas fa-check"></i> Shuffled!';
        setTimeout(() => {
            btn.innerHTML = '<i class="fas fa-random"></i> Shuffle Shows';
        }, 800);
    }

    renderShows(_lastShowsData);
}

async function playFolderShow(showObj) {
    try {
        // Fetch files for this folder
        const response = await fetch(`${API_BASE}/api/shows/folder-content?path=${encodeURIComponent(showObj.path)}`);
        if (!response.ok) throw new Error("Failed to fetch folder content");

        const files = await response.json();

        if (files && files.length > 0) {
            startShow({
                title: showObj.title,
                files: files
            });
        } else {
            showToast('Empty', 'No media found in this folder', 'warning');
        }
    } catch (e) {
        console.error("Error playing folder:", e);
        showToast('Error', 'Could not load folder content', 'error');
    }
}

// ==================== FOLDER TREE ====================

async function loadFolders() {
    try {
        const response = await fetch(`${API_BASE}/api/folders`);
        if (!response.ok) throw new Error('Failed to load folders');

        const data = await response.json();
        folderTree = data.folders;

        // Load expanded state from localStorage
        const savedExpanded = localStorage.getItem('expandedFolders');
        if (savedExpanded) {
            expandedFolders = new Set(JSON.parse(savedExpanded));
        }

        renderSidebarFolderTree();
    } catch (error) {
        console.error('Error loading folders:', error);
        showToast('Error', 'Failed to load folders', 'error');
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
        iconMode: 'emoji-state',
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
    renderSidebarFolderTree(); // Re-render to update selection
    updateBreadcrumb(path);
}

// ==================== BREADCRUMB ====================

function updateBreadcrumb(path) {
    const breadcrumb = document.getElementById('breadcrumb');
    breadcrumb.innerHTML = '';

    const parts = path.split('/').filter(p => p);

    if (parts.length === 0) {
        breadcrumb.innerHTML = '<span class="breadcrumb-item active">Home</span>';
        return;
    }

    let currentPath = '';
    parts.forEach((part, index) => {
        currentPath += '/' + part;

        const isLast = index === parts.length - 1;

        if (index > 0) {
            const separator = document.createElement('span');
            separator.className = 'breadcrumb-separator';
            separator.textContent = '/';
            breadcrumb.appendChild(separator);
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

async function loadMedia(folder) {
    try {
        const response = await fetch(`${API_BASE}/api/media?folder=${encodeURIComponent(folder)}`);
        if (!response.ok) throw new Error('Failed to load media');

        const data = await response.json();
        currentMedia = data.files;
        filteredMedia = [...currentMedia];

        applySortAndRender();
    } catch (error) {
        console.error('Error loading media:', error);
        showToast('Error', 'Failed to load media files', 'error');
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
    });

    const grid = document.getElementById('media-grid');
    grid.classList.toggle('list-view', view === 'list');
    grid.classList.toggle('grid-view', view === 'grid');

    renderMediaGrid();
}

// ==================== MEDIA GRID RENDERING ====================

function renderMediaGrid() {
    const grid = document.getElementById('media-grid');
    grid.innerHTML = '';

    if (filteredMedia.length === 0) {
        grid.innerHTML = '<p class="text-center">No media files found.</p>';
        return;
    }

    // Separate folders and media files
    const folders = filteredMedia.filter(item => item.type === 'folder');
    const mediaFiles = filteredMedia.filter(item => item.type !== 'folder');

    // Render folders first
    folders.forEach(folder => {
        const card = createFolderCard(folder);
        grid.appendChild(card);
    });

    // Then render media files
    mediaFiles.forEach((file, index) => {
        const card = createMediaCard(file, index);
        grid.appendChild(card);
    });
}

function createFolderCard(folder) {
    const card = document.createElement('div');
    card.className = 'media-card folder-card';
    card.onclick = () => selectFolder(folder.path);

    // Add filename for list view
    card.setAttribute('data-filename', folder.name);

    // Folder icon with type-based emoji
    const icon = document.createElement('div');
    icon.className = 'folder-card-icon';

    // Get appropriate emoji based on file/folder type
    let iconEmoji = '📁'; // default folder

    if (folder.type === 'folder') {
        iconEmoji = '📁';
    } else {
        // For non-media files, use appropriate emoji
        const name = folder.name.toLowerCase();
        const ext = name.substring(name.lastIndexOf('.'));

        // Document types
        if (['.pdf'].includes(ext)) iconEmoji = '📄';
        else if (['.doc', '.docx'].includes(ext)) iconEmoji = '📝';
        else if (['.xls', '.xlsx', '.csv'].includes(ext)) iconEmoji = '📊';
        else if (['.ppt', '.pptx'].includes(ext)) iconEmoji = '📽️';
        else if (['.txt', '.md', '.log'].includes(ext)) iconEmoji = '📃';

        // Archive types
        else if (['.zip', '.rar', '.7z', '.tar', '.gz'].includes(ext)) iconEmoji = '🗜️';

        // Audio types
        else if (['.mp3', '.wav', '.flac', '.m4a', '.aac'].includes(ext)) iconEmoji = '🎵';

        // Code/config files
        else if (['.js', '.py', '.java', '.cpp', '.c', '.h', '.html', '.css'].includes(ext)) iconEmoji = '👨‍💻';
        else if (['.json', '.xml', '.yaml', '.yml', '.toml', '.ini'].includes(ext)) iconEmoji = '⚙️';

        // Other
        else iconEmoji = '📄';
    }

    icon.textContent = iconEmoji;
    card.appendChild(icon);

    // Folder name
    const name = document.createElement('div');
    name.className = 'folder-card-name';
    name.textContent = folder.name;
    card.appendChild(name);

    return card;
}

function createMediaCard(file, index) {
    const card = document.createElement('div');
    card.className = 'media-card';
    card.onclick = () => openMediaModal(index);

    // Thumbnail
    const img = document.createElement('img');
    img.className = 'media-thumbnail loading';
    img.alt = file.name;

    // Thumbnail URL logic
    let thumbUrl = `${API_BASE}/api/thumb?path=${encodeURIComponent(file.path)}`;

    if (file.type === 'audio') {
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
        img.style.backgroundColor = 'var(--bg-tertiary)';
    });

    // Set src to start loading (async, non-blocking)
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

    // Info icon (bottom-right corner, shown on hover/grid view)
    const infoIcon = document.createElement('div');
    infoIcon.className = 'media-info-icon';
    infoIcon.textContent = 'i';
    infoIcon.title = file.name;
    infoIcon.onclick = (e) => {
        e.stopPropagation();
        openMediaModal(index);
    };

    card.appendChild(infoIcon);

    return card;
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 10) / 10 + ' ' + sizes[i];
}

// ==================== SEARCH/FILTER ====================

function handleGlobalSearch(query) {
    query = query.toLowerCase().trim();

    if (!query) {
        filteredMedia = [...currentMedia];
    } else {
        filteredMedia = currentMedia.filter(file =>
            file.name.toLowerCase().includes(query)
        );
    }

    applySortAndRender();
}

// ==================== MODAL VIEWER ====================

let currentAudioPlayer = null;
let audioKeyHandler = null;

function openMediaModal(index) {
    currentModalIndex = index;
    const file = filteredMedia[index];
    const modal = document.getElementById('modal-viewer');
    const modalBody = document.getElementById('modal-body');
    const modalTitle = document.getElementById('modal-title');
    const modalControls = document.getElementById('modal-controls');
    const modalMetadata = document.getElementById('modal-metadata');

    modalTitle.textContent = file.name;

    // Clear previous content
    modalBody.innerHTML = '';
    modalZoom = 1;
    panOffset = { x: 0, y: 0 };  // Reset pan offset

    if (file.type === 'video') {
        const video = document.createElement('video');
        video.controls = true;
        video.style.maxWidth = '100%';
        video.style.maxHeight = '70vh';
        video.src = `${API_BASE}/api/file?path=${encodeURIComponent(file.path)}`;
        modalBody.appendChild(video);
        modalControls.style.display = 'none';
    } else if (file.type === 'audio') {
        renderAudioPlayer(file, modalBody);
        modalControls.style.display = 'none'; // hide default pan/zoom controls
    } else {
        const img = document.createElement('img');
        img.src = `${API_BASE}/api/file?path=${encodeURIComponent(file.path)}`;
        img.style.transform = `scale(${modalZoom})`;
        img.id = 'modal-image';
        img.style.cursor = 'default';
        img.style.userSelect = 'none';

        // Add pan event listeners
        img.addEventListener('mousedown', startPan);

        modalBody.appendChild(img);
        modalControls.style.display = 'flex';
    }

    // Metadata
    modalMetadata.innerHTML = html`
        <div class="metadata-grid">
            <span class="metadata-label">Path:</span>
            <span class="metadata-value">
                ${file.path}
                <button class="copy-btn">Copy</button>
            </span>
            <span class="metadata-label">Size:</span>
            <span class="metadata-value">${formatBytes(file.size)}</span>
            <span class="metadata-label">Modified:</span>
            <span class="metadata-value">${new Date(file.modified * 1000).toLocaleString()}</span>
            <span class="metadata-label">Type:</span>
            <span class="metadata-value">${file.type}</span>
            <span class="metadata-label" style="color: var(--primary);">ELO Rating:</span>
            <span class="metadata-value" style="color: var(--primary); font-weight: bold;">
                <i class="fas fa-star" style="margin-right: 5px;"></i>${file.rating || 1000}
            </span>
        </div>
    `;
    modalMetadata.querySelector('.copy-btn').addEventListener('click', () => copyToClipboard(file.path));

    modal.classList.add('active');
}

function closeModal() {
    const modal = document.getElementById('modal-viewer');

    // Stop any playing video
    const video = modal.querySelector('video');
    if (video) {
        video.pause();
        video.src = ''; // Clear source to stop buffering
    }

    // Stop audio
    if (currentAudioPlayer) {
        currentAudioPlayer.pause();
        currentAudioPlayer.src = '';
        currentAudioPlayer = null;
    }

    // Cleanup spacebar handler
    if (audioKeyHandler) {
        document.removeEventListener('keydown', audioKeyHandler);
        audioKeyHandler = null;
    }

    modal.classList.remove('active');
    currentModalIndex = -1;
}

async function openInFinder() {
    if (currentModalIndex < 0) return;
    const file = filteredMedia[currentModalIndex];
    if (!file || !file.path) return;

    // Show visual feedback immediately
    const btn = document.getElementById('btn-open-finder');
    const oldText = btn.textContent;
    btn.textContent = 'Opening...';
    btn.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/api/open-in-finder`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: file.path })
        });

        if (!response.ok) {
            console.error('Failed to open in Finder');
        } else {
            // revealed in Finder
        }
    } catch (e) {
        console.error('Error opening in Finder:', e);
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

    cover.innerHTML = html`<img src="${coverSrc}" style="width: 100%; height: 100%; object-fit: cover; border-radius: 20px;">`;

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
    artist.textContent = 'Local File';

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

function openMoveFileModal(fileData = null) {
    // Determine which file to move
    if (fileData) {
        fileToMove = fileData;
    } else if (activeShow) {
        fileToMove = activeShow.files[activeShow.currentIndex];
        activeShow.stopLoop(); // Pause show while moving
    } else if (currentModalIndex >= 0) {
        fileToMove = filteredMedia[currentModalIndex];
    }

    if (!fileToMove) return;

    const modal = document.getElementById('modal-move-file');
    document.getElementById('move-file-name').textContent = fileToMove.name || 'Unknown File';
    document.getElementById('btn-confirm-move').disabled = true;
    selectedMoveFolder = null;

    // Show source folder path
    const sourcePath = fileToMove.path || '';
    const sourceFolder = sourcePath.substring(0, sourcePath.lastIndexOf('/'));
    const sourceFolderName = sourceFolder.split('/').filter(Boolean).slice(-2).join(' / ') || sourceFolder;
    const sourceEl = document.getElementById('move-file-source');
    if (sourceEl) {
        sourceEl.querySelector('span').textContent = sourceFolderName;
        sourceEl.title = sourceFolder;
    }

    // Show thumbnail preview
    const previewEl = document.getElementById('move-file-preview');
    if (previewEl) {
        previewEl.innerHTML = html`<img src="${vaultFileUrl('thumb', fileToMove.path)}" alt="Preview" style="width:100%;height:100%;object-fit:cover;border-radius:8px;">`;
    }

    renderMoveFolderTreeUI();
    modal.classList.add('active');

    // Activate keyboard navigation
    if (window._moveNav) window._moveNav.nav.resetIndex();
    window._moveNav = activateFolderTreeModal({
        modalId: 'modal-move-file',
        containerId: 'move-folder-tree',
        itemSelector: '.move-folder-node',
        childrenSelector: '.move-folder-children',
        invertCollapse: true,
        onSelect: (item) => {
            const path = item.dataset.path;
            if (path) selectMoveDestination(path);
        },
        onConfirm: () => { if (selectedMoveFolder) confirmMoveFile(); },
        onClose: closeMoveFileModal,
    });
}

function closeMoveFileModal() {
    const modal = document.getElementById('modal-move-file');
    if (modal) modal.classList.remove('active');
    if (window._moveNav) { window._moveNav.nav.resetIndex(); }

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
    selectedMoveFolder = path;
    const btn = document.getElementById('btn-confirm-move');

    // Prevent moving to the same directory
    const currentDir = fileToMove.path.substring(0, fileToMove.path.lastIndexOf('/'));

    if (path === currentDir) {
        btn.disabled = true;
        showToast('Info', 'File is already in this folder', 'warning');
    } else {
        btn.disabled = false;
    }

    // Show destination in left panel
    const destDisplay = document.getElementById('move-dest-display');
    const destPath = document.getElementById('move-dest-path');
    if (destDisplay && destPath) {
        const destName = path.split('/').filter(Boolean).slice(-2).join(' / ') || path;
        destPath.querySelector('span').textContent = destName;
        destPath.title = path;
        destDisplay.style.display = '';
    }

    // Re-render to show selection highlight
    renderMoveFolderTreeUI();
}

async function confirmMoveFile() {
    if (!fileToMove || !selectedMoveFolder) return;

    const btn = document.getElementById('btn-confirm-move');
    const originalContent = [...btn.childNodes];
    btn.innerHTML = '<i class="fas fa-spinner fa-spin" style="margin-right: 8px;"></i> Moving...';
    btn.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/api/move-file`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source_path: fileToMove.path,
                destination_folder: selectedMoveFolder
            })
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }

        const moved = await response.json();
        showToast('Moved', moved.message || 'File successfully moved', 'success');

        // Remove from current view
        if (activeShow) {
            // Add a fly-away animation to the current active slide
            const overlay = document.getElementById('cinematic-player');
            const activeSlide = overlay.querySelector('.cinematic-slide.active');
            if (activeSlide) {
                activeSlide.classList.add('fly-away-animation');
            }

            setTimeout(() => {
                activeShow.files.splice(activeShow.currentIndex, 1);
                if (activeShow.files.length === 0) {
                    closeMoveFileModal();
                    activeShow.close();
                } else {
                    if (activeShow.currentIndex >= activeShow.files.length) {
                        activeShow.currentIndex = 0;
                    }
                    activeShow.renderSlide();
                    closeMoveFileModal();
                }
            }, 600); // Wait for fly away animation

            return; // Exit early to avoid closing modal before animation
        } else if (currentModalIndex >= 0) {
            filteredMedia.splice(currentModalIndex, 1);
            renderMediaGrid(); // Re-render grid without file
            closeModal(); // Close viewer
        }

        closeMoveFileModal();

    } catch (e) {
        console.error('Move error:', e);
        showToast('Error', `Failed to move file: ${e.message}`, 'error');
        btn.replaceChildren(...originalContent);
        btn.disabled = false;
    }
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
    if (currentModalIndex > 0) {
        openMediaModal(currentModalIndex - 1);
    }
}

function nextFile() {
    if (currentModalIndex < filteredMedia.length - 1) {
        openMediaModal(currentModalIndex + 1);
    }
}

// Keyboard navigation
document.addEventListener('keydown', (e) => {
    if (document.getElementById('modal-viewer').classList.contains('active')) {
        if (e.key === 'ArrowLeft') prevFile();
        if (e.key === 'ArrowRight') nextFile();
        if (e.key === 'Escape') closeModal();
    }
});

// ==================== TOAST NOTIFICATIONS ====================

function showToast(title, message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    // Titel en melding zijn tekst: ze bevatten vaak bestandsnamen of een foutmelding van de server
    toast.append(
        createEl('div', { class: 'toast-header' },
            createEl('strong', { text: title }),
            createEl('button', { onClick: () => toast.remove() }, '\u00d7')),
        createEl('div', { class: 'toast-body', text: message }));

    container.appendChild(toast);

    // Auto remove after 3s
    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ==================== UTILS ====================

function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showToast('Success', 'Path copied to clipboard', 'success');
    });
}

// ==================== HOME WIDGETS ====================
async function fetchHomeWidgets() {
    // 1. Storage
    try {
        const res = await fetch(`${API_BASE}/api/stats/storage`);
        const data = await res.json();
        if (data.total_gb) {
            document.querySelector('.storage-loading').style.display = 'none';
            document.querySelector('.storage-stats').style.display = 'block';

            document.getElementById('storage-used-text').textContent = `${data.used_gb} GB`;
            document.getElementById('storage-total-text').textContent = `${data.total_gb} GB`;
            document.getElementById('storage-free-text').textContent = `${data.free_gb} GB`;

            const bar = document.getElementById('storage-bar-fill');
            bar.style.width = `${data.percent_used}%`;
            if (data.percent_used > 90) bar.classList.add('critical');
            else if (data.percent_used > 75) bar.classList.add('warning');
        }
    } catch (e) {
        document.querySelector('.storage-loading').textContent = 'Failed to load storage';
    }

    // 2. Photo OTD
    loadPhotoOtdWidget();

    // 3. Spotify OTD
    try {
        const res = await fetch(`${API_BASE}/api/stats/spotify-on-this-day`);
        const data = await res.json();
        const loading = document.getElementById('spotify-otd-loading');
        const content = document.getElementById('spotify-otd-content');

        loading.style.display = 'none';
        content.style.display = 'block';

        if (data.track) {
            const t = data.track;
            content.replaceChildren(
                createEl('div', { class: 'otd-item', onClick: () => openSpotifySearch(`${t.track} ${t.artist}`) },
                    createEl('div', { class: 'otd-spotify-placeholder' }, createEl('i', { class: 'fab fa-spotify' })),
                    createEl('div', { class: 'otd-title', text: t.track }),
                    createEl('div', { class: 'otd-subtitle', text: `${t.artist} • ${t.year}` })));

            // Geen albumcover van buitenaf (zoals vroeger via iTunes): dat zou luistergeschiedenis
            // naar een externe dienst sturen. Het lokale Spotify-icoon blijft staan.
        } else {
            content.innerHTML = '<div class="storage-text">No tracks played on this day.</div>';
        }
    } catch (e) {
        document.getElementById('spotify-otd-loading').textContent = 'Failed to load';
    }
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
    const index = filteredMedia.findIndex(f => f.name === fileName);
    if (index !== -1) {
        openMediaModal(index);
    }
}

// ==================== PHOTO OTD SETTINGS ====================
let otdCycleInterval = null;

async function loadPhotoOtdWidget() {
    try {
        const loading = document.getElementById('photo-otd-loading');
        const content = document.getElementById('photo-otd-content');

        // Show loading and clear interval
        loading.style.display = 'flex';
        content.style.display = 'none';

        if (otdCycleInterval) {
            clearInterval(otdCycleInterval);
            otdCycleInterval = null;
        }

        let otdAllowedFolders = JSON.parse(localStorage.getItem('otdAllowedFolders') || '[]');
        const query = otdAllowedFolders.length > 0 ? `?allowed_folders=${encodeURIComponent(otdAllowedFolders.join(','))}` : '';
        const res = await fetch(`${API_BASE}/api/stats/photo-on-this-day${query}`);
        const data = await res.json();

        loading.style.display = 'none';
        content.style.display = 'block';

        if (data.photos && data.photos.length > 0) {
            content.replaceChildren();
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
                otdCycleInterval = setInterval(() => {
                    const images = content.querySelectorAll('.otd-large-image');
                    const overlays = content.querySelectorAll('.otd-large-overlay');
                    if (images.length === 0) {
                        clearInterval(otdCycleInterval);
                        return;
                    }
                    images[currentOtd].classList.remove('active');
                    overlays[currentOtd].style.display = 'none';

                    currentOtd = (currentOtd + 1) % images.length;

                    images[currentOtd].classList.add('active');
                    overlays[currentOtd].style.display = 'flex';
                }, 4000); // 4 seconds interval
            }
        } else {
            content.innerHTML = '<div class="storage-text" style="display:flex;height:100%;align-items:center;justify-content:center;">No photos from this day in past years.</div>';
        }
    } catch (e) {
        document.getElementById('photo-otd-loading').textContent = 'Failed to load';
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
        list.innerHTML = '<div style="color:var(--text-secondary);font-size:0.8rem;">No folders available</div>';
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

    // Start background prefetch na 1.5s zodat de initiële pagina eerst volledig laadt
    scheduleBackgroundPrefetch();
});


// ==================== DNA / MOUNT EXPLANATION ====================

function showDnaExplanation() {
    const modal = document.getElementById('modal-dna-explanation');

    // Populate DNA type from current data
    const dnaText = document.getElementById('stat-dna');
    const currentDna = dnaText ? dnaText.textContent.trim() : '';

    const dnaTypes = {
        'Early Bird 🐦': { icon: '🐦', title: 'Early Bird', desc: 'You listen most between 5 AM and 12 PM. Early risers unite — you start your day powered by music before the world wakes up.' },
        'Daydreamer ☀️': { icon: '☀️', title: 'Daydreamer', desc: 'Your peak listening is between 12 PM and 6 PM. Afternoon vibes fuel your creative flow and keep you energized throughout the day.' },
        'Evening Chiller 🌇': { icon: '🌇', title: 'Evening Chiller', desc: 'You\u2019re most active between 6 PM and 11 PM. Music is your wind-down ritual — perfect for reflecting on the day.' },
        'Night Owl 🦉': { icon: '🦉', title: 'Night Owl', desc: 'You listen most between 11 PM and 5 AM. The night is your kingdom, and music is your loyal companion in the dark hours.' },
        'Balanced Listener': { icon: '🎧', title: 'Balanced Listener', desc: 'Your listening is spread evenly across the day. You\u2019re a true all-day listener with no single peak time.' }
    };

    const info = dnaTypes[currentDna] || dnaTypes['Balanced Listener'];

    // Fill in modal content
    const iconEl = document.getElementById('dna-modal-icon');
    const titleEl = document.getElementById('dna-modal-title');
    const descEl = document.getElementById('dna-modal-description');
    if (iconEl) iconEl.textContent = info.icon;
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
                newItem.appendChild(createEl('div', { class: 'actual-dna-badge', text: 'Your DNA' }));
            }
        }

        // Initial active state matches the actual DNA
        newItem.classList.toggle('active', info.title.includes(typeName));

        // Click listener to view other explanations
        newItem.addEventListener('click', () => {
            // Update active state
            document.querySelectorAll('.dna-type-item').forEach(el => el.classList.remove('active'));
            newItem.classList.add('active');

            // Find full type name key
            const fullTypeKey = Object.keys(dnaTypes).find(k => k.includes(typeName)) || 'Balanced Listener';
            const clickedInfo = dnaTypes[fullTypeKey];

            // Update Hero
            if (iconEl) iconEl.textContent = clickedInfo.icon;
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
    const colors = ['rgba(29,185,84,0.5)', 'rgba(155,89,182,0.4)', 'rgba(30,144,255,0.3)', 'rgba(255,165,0,0.3)'];
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

let storageNavigationStack = [];

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
    storageNavigationStack = [];
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
        const res = await fetch(`${API_BASE}/api/stats/storage-breakdown-nested${params}`);
        const data = await res.json();

        if (!res.ok || data.error) {
            list.innerHTML = '<li>Fout bij laden van data.</li>';
            loading.style.display = 'none';
            content.style.display = 'block';
            return;
        }

        list.innerHTML = '';
        filesList.innerHTML = '';

        const entries = Object.entries(data.breakdown || {}).sort((a, b) => parseFloat(b[1]) - parseFloat(a[1]));
        const total = entries.reduce((sum, [, gb]) => sum + parseFloat(gb), 0);

        // Reset tab naar subfolders bij elke navigatie
        storageShowTab('subfolders');

        if (folderPath) {
            breadcrumb.style.display = 'flex';
            breadcrumbPath.textContent = `/ ${folderPath}`;
            tabs.style.display = 'flex';
            summaryCard.style.display = 'block';
        } else {
            breadcrumb.style.display = 'none';
            tabs.style.display = 'none';
            summaryCard.style.display = 'block';
        }

        totalDisplay.textContent = `${total.toFixed(2)} GB`;
        if (entries.length > 0) {
            largestDisplay.textContent = `${entries[0][0]} (${entries[0][1]} GB)`;
        }

        entries.forEach(([folder, gb]) => {
            const percent = total > 0 ? (parseFloat(gb) / total * 100).toFixed(1) : 0;
            const li = document.createElement('li');
            li.className = 'storage-folder-item';
            li.style.cursor = 'pointer';

            const icon = getFolderIcon(folder);
            const newPath = folderPath ? `${folderPath}/${folder}` : folder;

            li.innerHTML = html`
                <div class="storage-folder-header">
                    <span class="storage-folder-name"><i class="fas ${icon}"></i>${folder}</span>
                    <span class="storage-folder-size">${gb} GB</span>
                </div>
                <div class="storage-progress-bar">
                    <div class="storage-progress-fill" style="width: ${percent}%"></div>
                </div>
                <div class="storage-folder-percent">${percent}% van totale opslag</div>
            `;

            li.onclick = () => storageNavigateToPath(newPath);
            list.appendChild(li);
        });

        if (folderPath) {
            const filesRes = await fetch(`${API_BASE}/api/stats/largest-files?folder_path=${encodeURIComponent(folderPath)}&limit=20`);
            const filesData = await filesRes.json();

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

                    const displaySize = file.size_gb > 1 ? `${file.size_gb.toFixed(2)} GB` : `${file.size_mb.toFixed(2)} MB`;

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
        list.innerHTML = '<li>Fout bij laden van storage data.</li>';
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
            icon: '🏆',
            title: 'Record Listening Day',
            desc: 'This is the single calendar day where you racked up the absolute highest amount of listening time.'
        },
        'top_binge': {
            icon: '🔁',
            title: 'Top Consecutive Binge',
            desc: 'Your highest streak for playing the exact same song back-to-back repeatedly without interruption.'
        },
        'mainstream_score': {
            icon: '📈',
            title: 'Explorer vs Mainstream',
            desc: 'If 75%+ of your listening goes to just a tiny handful of top artists, you are categorized as a "Mainstream Repeater". If it is widely spread, you are a "Broad Explorer".'
        },
        'seasonal_mood': {
            icon: '🌤️',
            title: 'Seasonal Mood',
            desc: 'Checks whether your listening spikes significantly during Winter months (Hibernator) or Summer months (Summer Vibes) based on your monthly playback.'
        }
    };

    const info = insightDetails[insightKey];
    if (!info) return;

    const iconEl = document.getElementById('insight-modal-icon');
    const titleEl = document.getElementById('insight-modal-title');
    const descEl = document.getElementById('insight-modal-description');

    if (iconEl) iconEl.textContent = info.icon;
    if (titleEl) titleEl.textContent = info.title;
    if (descEl) descEl.textContent = info.desc;

    if (modal) modal.classList.add('active');
}

function closeInsightModal() {
    const modal = document.getElementById('modal-insight-explanation');
    if (modal) modal.classList.remove('active');
}

// ==================== SHOWS SETTINGS ====================

let settingsData = {
    folders: [],
    loading: false
};

async function openSettingsModal() {
    // create modal if not exists
    let modal = document.getElementById('modal-show-settings');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'modal-show-settings';
        modal.className = 'modal'; // Centered by CSS
        modal.innerHTML = `
        <div class="modal-content settings-modal-content">
            <span class="close-modal" onclick="closeSettingsModal()">&times;</span>
            <h2>Manage Sources</h2>
            <p class="settings-subtitle">Choose which folders to include in your shows.</p>
            <div id="settings-folders-list" class="settings-list">
                <div class="loading-spinner"></div>
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
        const [foldersRes, settingsRes] = await Promise.all([
            fetch(`${API_BASE}/api/folders`),
            fetch(`${API_BASE}/api/shows/settings`)
        ]);

        if (!foldersRes.ok || !settingsRes.ok) {
            throw new Error("Failed to load data");
        }

        const foldersData = await foldersRes.json();
        const settingsData = await settingsRes.json();

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
            list.innerHTML = '<div class="error-message">Invalid folder data.</div>';
        }

    } catch (e) {
        console.error("Error loading settings:", e);
        list.innerHTML = '<div class="error-message">Failed to load settings.</div>';
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
        <i class="fas fa-chevron-right"></i>
    </div>
    <div class="settings-label">
        <i class="fas fa-folder${item.enabled ? '-open' : ''}" style="opacity: ${item.enabled ? 1 : 0.5}"></i>
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
        const res = await fetch(`${API_BASE}/api/shows/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, enabled })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showToast('Fout', err.detail || `Instelling opslaan mislukt (${res.status})`, 'error');
            return;
        }
        showToast('Instellingen', `Folder ${enabled ? 'ingeschakeld' : 'uitgeschakeld'}`, 'success');
        reloadShows();
    } catch (e) {
        console.error("Error toggling folder:", e);
        showToast('Fout', 'Instelling opslaan mislukt', 'error');
    }
}


// ==================== CINEMATIC PLAYER ====================

class CinematicPlayer {
    constructor(story) {
        this.story = story;
        this.files = story.files || [];
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
    <button class="player-close-btn" onclick="activeShow.close()">&times;</button>
</div>
 
<div class="player-controls-bottom">
     <div class="player-meta" id="player-slide-meta"></div>
     
     <div class="player-progress-bar">
        <div class="player-progress-fill" id="player-progress"></div>
     </div>
 
     <div class="player-actions">
        <button class="player-btn" onclick="activeShow.prev()"><i class="fas fa-step-backward"></i></button>
        <button class="player-btn" id="btn-play-pause" onclick="activeShow.togglePlay()"><i class="fas fa-pause"></i></button>
        <button class="player-btn" onclick="activeShow.next()"><i class="fas fa-step-forward"></i></button>
        
        <div class="player-divider"></div>
        <button class="player-btn move-file-btn" onclick="openMoveFileModal()" title="Move File"><i class="fas fa-folder-open"></i></button>
        <div class="player-divider"></div>
        
        <button class="player-btn vote-up" onclick="activeShow.vote('up')"><i class="fas fa-heart"></i></button>
        <button class="player-btn vote-down" onclick="activeShow.vote('down')"><i class="fas fa-thumbs-down"></i></button>
     </div>
</div>
`;
        document.body.appendChild(overlay);
        this.playerEl = overlay;

        // Keyboard support
        this.keyHandler = (e) => {
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
            await fetch(`${API_BASE}/api/shows/vote`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: file.path, vote: type })
            });
        } catch (e) {
            console.error("Vote failed:", e);
            // Revert on failure
            file.rating = oldRating;
        }
    }

    showVoteEffect(type) {
        const icon = type === 'up' ? '❤️' : '👎';
        const el = document.createElement('div');
        el.className = 'vote-floater';
        el.textContent = icon;
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

        const fileName = file.name || '';
        const isVideo = fileName.toLowerCase().match(/\.(mp4|mov|webm)$/);

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
                if (this.isPlaying) this.next();
            });

            slide.appendChild(video);
        } else {
            const kbEffect = Math.random() > 0.5 ? 'ken-burns-in' : 'ken-burns-out';
            slide.innerHTML = html`<img src="${vaultFileUrl('file', file.path)}" class="cinematic-media ${kbEffect}">`;
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
        const folderName = file.folder || file.path.split(/[/\\\\]/).slice(-2, -1)[0] || 'Unknown Folder';

        titleEl.textContent = folderName;

        metaEl.innerHTML = html`
<i class="fas fa-folder"></i> ${folderName} <span style="opacity:0.5; margin:0 5px;">|</span>
<i class="fas fa-camera"></i> ${file.name} <span style="opacity:0.5; margin:0 5px;">|</span>
<i class="fas fa-calendar"></i> ${new Date(file.modified * 1000).toLocaleDateString()}
${file.size ? html`<span style="margin-left: 15px;"><i class="fas fa-hdd"></i> ${(file.size / (1024 * 1024)).toFixed(1)} MB</span>` : ''}
<div class="elo-rating-badge">
    <i class="fas fa-star"></i> ELO: ${file.rating || 1000}
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
    activeShow = new CinematicPlayer(story);
}



// ==================== SPOTIFY WRAPPED ====================

async function loadSpotifyStats() {
    const loading = document.getElementById('spotify-loading');
    const content = document.getElementById('spotify-content');
    const yearSelect = document.getElementById('spotify-year-select');
    const year = yearSelect ? yearSelect.value : 'all';
    const url = `${API_BASE}/api/spotify/stats?year=${year}`;

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

    // Gebruik cache als beschikbaar — instant render, geen spinner
    if (_prefetchCache[url]) {
        const data = await _prefetchCache[url];
        if (data) {
            if (loading) loading.style.display = 'none';
            if (content) content.style.display = 'block';
            renderSpotifyStats(data);
            populateYears(data);
            return;
        }
    }

    // Normale laadprocedure
    if (loading) loading.style.display = 'flex';
    if (content) content.style.display = 'none';

    try {
        const res = await fetch(url);
        if (!res.ok) throw new Error('Failed to load Spotify stats');
        const data = await res.json();
        _prefetchCache[url] = Promise.resolve(data);
        renderSpotifyStats(data);
        populateYears(data);
    } catch (e) {
        console.error("Spotify load error:", e);
        showToast('Error', 'Failed to load Spotify data', 'error');
        if (content) content.innerHTML = '<div class="error-state">Failed to load data. Is the backend running?</div>';
    } finally {
        if (loading) loading.style.display = 'none';
        if (content) content.style.display = 'block';
    }
}

function renderSpotifyStats(data) {
    // 1. Stats Cards
    updateText('stat-minutes', data.total_minutes.toLocaleString());
    updateText('stat-top-artist', data.top_artists.length > 0 ? data.top_artists[0].name : '-');
    updateText('stat-plays', data.total_plays.toLocaleString());
    updateText('stat-dna', data.listening_dna);

    // 2. Lists
    renderSpotifyList('top-artists-list', data.top_artists, (item, i) => spotifyListItem({
        rank: `#${i + 1}`, name: item.name, meta: `${Math.round(item.ms / 60000).toLocaleString()} mins`,
        icon: 'fab fa-spotify', query: item.name,
    }));

    renderSpotifyList('top-tracks-list', data.top_tracks, (item, i) => spotifyListItem({
        rank: `#${i + 1}`, name: item.name, meta: `${item.artist} • ${item.count} plays`,
        icon: 'fas fa-play', query: `${item.name} ${item.artist}`,
    }));

    // 2.5 New Insights
    if (data.new_insights) {
        const insights = data.new_insights;

        // Record Day
        if (insights.record_day) {
            updateText('stat-record-day-hours', `${insights.record_day.hours} hrs`);
            updateText('stat-record-day-date', `Record Day: ${insights.record_day.date}`);
        }

        // Binge
        if (insights.top_binge) {
            const bingeEl = document.getElementById('stat-top-binge');
            const binge = insights.top_binge;
            bingeEl.replaceChildren(createEl('a', {
                href: spotifySearchUrl(`${binge.track} ${binge.artist}`), target: '_blank', rel: 'noopener noreferrer',
                style: 'color: inherit; text-decoration: underline;', text: binge.track,
                onClick: (e) => e.stopPropagation(),
            }));
            updateText('stat-top-binge-count', `Binge: ${insights.top_binge.count}x in a row`);
        } else {
            updateText('stat-top-binge', 'No binges');
        }

        // Mainstream
        if (insights.mainstream_score) {
            updateText('stat-mainstream-score', insights.mainstream_score);
            updateText('stat-mainstream-percent', `Top 5% artists = ${insights.mainstream_percent}% of listening`);
        }

        // Seasonal Mood
        if (insights.seasonal_mood) {
            updateText('stat-seasonal-mood', insights.seasonal_mood);
        }

        // Discoveries
        const discoveriesContainer = document.getElementById('discoveries-container');
        if (insights.discoveries && insights.discoveries.length > 0) {
            discoveriesContainer.style.display = 'block';
            renderSpotifyList('discoveries-list', insights.discoveries, (item, i) => spotifyListItem({
                rank: `#${i + 1}`, name: item.name,
                meta: `First played this year • ${Math.round(item.ms / 60000).toLocaleString()} mins`,
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
        // Clear any existing interval
        if (window._otdClockInterval) clearInterval(window._otdClockInterval);
        const tickClock = () => {
            const n = new Date();
            otdClockEl.textContent = n.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        };
        tickClock();
        window._otdClockInterval = setInterval(tickClock, 1000);
    }
    if (data.on_this_day && data.on_this_day.length > 0) {
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
            <div style="font-size:0.85rem;color:var(--text-secondary);">${play.artist}</div>
        </div>
        <div style="font-size:0.85rem;color:var(--text-tertiary);white-space:nowrap;"><i class="far fa-clock"></i> ${play.time}</div>
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
    renderSimpleChart('monthly-chart', data.monthly_activity, ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D']);
    renderSimpleChart('hourly-chart', data.hourly_activity,
        ['12a', '', '', '', '4a', '', '', '', '8a', '', '', '', '12p', '', '', '', '4p', '', '', '', '8p', '', '', '']
    );
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

function renderSimpleChart(id, data, labels) {
    const el = document.getElementById(id);
    if (!el) return;

    const max = Math.max(...data, 1);
    el.innerHTML = '';

    data.forEach((val, i) => {
        const height = (val / max) * 100;
        const bar = document.createElement('div');
        bar.className = 'chart-bar';
        bar.style.height = `${height}%`;
        bar.title = `${labels[i] || i}: ${val}`;

        if (labels && labels[i]) {
            bar.setAttribute('data-label', labels[i]);
        }

        el.appendChild(bar);
    });
}

// ==================== DA VAI OLLAMA CHATBOT ====================

const CHAT_MODEL = 'phi3.5:latest';

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
        const res = await fetch(`${API_BASE}/api/media?folder=${encodeURIComponent(folder)}`);
        if (!res.ok) { notFound(); return; }
        const data = await res.json();
        const file = (data.files || []).find(f =>
            ['photo', 'video', 'audio'].includes(f.type) && f.name.normalize('NFC') === name);
        if (!file) { notFound(); return; }
        filteredMedia = [file];
        openMediaModal(0);
    } catch (e) {
        console.error('Bestand openen mislukt:', e);
        notFound();
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
    const grid = createEl('div', { style: 'display: flex; flex-wrap: wrap; gap: 8px;' });
    photos.forEach(p => grid.appendChild(createEl('img', {
        src: vaultFileUrl('thumb', p.path),
        style: `width: ${size}px; height: ${size}px; object-fit: cover; border-radius: 8px; cursor: pointer; border: 1px solid rgba(255, 255, 255, 0.1);`,
        title: titleOf(p),
        dataset: { path: p.path },
    })));
    const message = chatBubble(grid);
    message.firstChild.style.cssText = 'background: transparent; border: none; padding: 0;';
    return message;
}

// Toont foto's van de server in de chat (show_photos en search_photos)
function chatShowPhotos(url, size, titleOf, emptyText, limit) {
    const chatMessages = document.getElementById('chat-messages');
    const loading = chatLoadingBubble();
    chatMessages.appendChild(loading);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    fetch(url)
        .then(res => res.json())
        .then(data => {
            loading.remove();
            const photos = (data.photos || []).slice(0, limit);
            chatMessages.appendChild(photos.length > 0
                ? chatPhotoGrid(photos, size, titleOf)
                : chatBubble(emptyText));
            chatMessages.scrollTop = chatMessages.scrollHeight;
        })
        .catch(err => {
            loading.remove();
            console.error('Error fetching photos:', err);
        });
}

function executeAIAction(actionData) {
    if (!actionData || !actionData.action) return;

    switch (actionData.action) {
        case 'open_tab':
            switchMainView(actionData.target);
            break;

        case 'scan_duplicates':
            switchMainView('duplicates');
            {
                const scanBtn = document.querySelector('#section-duplicates .btn-primary');
                if (scanBtn) scanBtn.click();
            }
            break;

        case 'open_folder': {
            const folder = findFolderInTree(actionData.target);
            if (!folder) {
                showToast('Map niet gevonden', actionData.target, 'warning');
                break;
            }
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
        btn: document.querySelector('.chat-send-btn'),
        messagesContainer: document.getElementById('chat-messages'),
        mirrorContainer: null,
    });
}

function sendChatMessageFullscreen() {
    return sendChat({
        inputEl: document.getElementById('chat-input-fullscreen'),
        btn: document.querySelector('.chat-fullscreen-modal .chat-send-btn'),
        messagesContainer: document.getElementById('chat-messages-fullscreen'),
        mirrorContainer: document.getElementById('chat-messages'),
    });
}

// Stuurt het bericht en toont het antwoord terwijl het binnenkomt. Alles wat het model
// schrijft komt als tekst (textContent) op het scherm, nooit als HTML. Met een
// mirrorContainer (schermvullende chat) wordt het gesprek ook in de gewone chat bijgewerkt.
async function sendChat({ inputEl, btn, messagesContainer, mirrorContainer }) {
    const message = inputEl.value.trim();
    if (!message) return;

    inputEl.value = '';
    inputEl.disabled = true;
    if (btn) btn.disabled = true;

    const userMsg = createEl('div', { class: 'chat-message user-message' },
        createEl('div', { class: 'message-bubble message-text', text: message }));
    messagesContainer.appendChild(userMsg);
    if (mirrorContainer) mirrorContainer.appendChild(userMsg.cloneNode(true));

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
    const mirrorMsg = mirrorContainer ? aiMsg.cloneNode(true) : null;
    if (mirrorMsg) mirrorContainer.appendChild(mirrorMsg);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    const showText = (text) => {
        for (const b of [bubble, mirrorMsg && mirrorMsg.firstChild]) {
            if (!b) continue;
            b.classList.add('message-text');
            b.textContent = text;
        }
    };
    const showError = (text) => {
        for (const b of [bubble, mirrorMsg && mirrorMsg.firstChild]) {
            if (b) b.replaceChildren(createEl('span', { style: 'color: var(--danger)', text }));
        }
    };

    let fullText = '';

    try {
        const response = await fetch(`${API_BASE}/api/chat/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages: window.chatHistory, model: CHAT_MODEL })
        });

        if (!response.ok) throw new Error(`Server returned ${response.status}`);

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
                    return;
                }

                if (typeof event.token === 'string' && event.token) {
                    fullText += event.token;
                    // Een half binnengekomen ACTION-tag niet laten zien
                    showText(fullText.replace(/<<ACTION[\s\S]*$/, '').trimEnd());
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                }

                if (event.done) {
                    const reply = parseChatReply(fullText);
                    fullText = reply.text;
                    showText(fullText);
                    if (reply.action) setTimeout(() => executeAIAction(reply.action), 1000);
                    window.chatHistory.push({ role: 'assistant', content: fullText });
                }
            }
        }

    } catch (e) {
        console.error('Chat error:', e);
        showError('Fout: Kan Ollama niet bereiken. Draait de server en Ollama?');
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
        syncChatMessages('to-fullscreen');
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
        syncChatMessages('to-home');
        modal.classList.remove('active');
        document.removeEventListener('keydown', handleFullscreenEscape);
    }
}

function handleFullscreenEscape(e) {
    if (e.key === 'Escape') {
        closeChatFullscreen();
    }
}

// Kopieert de berichten als DOM-knopen (niet via innerHTML, dat tekst opnieuw als HTML zou lezen)
function syncChatMessages(direction) {
    const homeContainer = document.getElementById('chat-messages');
    const fullscreenContainer = document.getElementById('chat-messages-fullscreen');
    if (!homeContainer || !fullscreenContainer) return;

    const [from, to] = direction === 'to-fullscreen'
        ? [homeContainer, fullscreenContainer]
        : [fullscreenContainer, homeContainer];
    to.replaceChildren(...[...from.childNodes].map(node => node.cloneNode(true)));
}
