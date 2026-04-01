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

        hero.innerHTML = `
            <img src="${API_BASE}/api/thumb?path=${encodeURIComponent(cover.path)}" class="shows-hero-img">
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
                thumbUrl = `${API_BASE}/api/thumb?path=${encodeURIComponent(show.preview.path)}`;
            }

            // Full thumbnail with transparent overlay + text shadow for readability
            card.innerHTML = `
                <div class="show-card-image-wrapper" style="position: relative; height: 180px; overflow: hidden; border-radius: 12px;">
                     ${thumbUrl ? `<img src="${thumbUrl}" class="show-card-thumb" style="width: 100%; height: 100%; object-fit: cover;">` : '<div class="no-thumb" style="display:flex;align-items:center;justify-content:center;height:100%;color:#666;background:var(--bg-tertiary);">📁</div>'}
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

        renderFolderTree();
    } catch (error) {
        console.error('Error loading folders:', error);
        showToast('Error', 'Failed to load folders', 'error');
    }
}

function renderFolderTree() {
    const container = document.getElementById('folder-tree');
    container.innerHTML = '';

    folderTree.forEach(folder => {
        const element = createFolderElement(folder, 0);
        container.appendChild(element);
    });
}

function createFolderElement(folder, depth) {
    const div = document.createElement('div');

    // Container for the row (Header)
    const row = document.createElement('div');
    row.className = `folder-row depth-${depth}`;
    row.style.paddingLeft = `${depth * 20}px`; // Indent entire row
    row.style.display = 'flex';
    row.style.alignItems = 'center';
    row.style.cursor = 'pointer';
    row.style.userSelect = 'none';

    // 1. Chevron/Spacer (Left - Fixed Width)
    const chevronContainer = document.createElement('div');
    chevronContainer.className = 'folder-chevron-container';
    chevronContainer.style.width = '30px';
    chevronContainer.style.height = '32px'; // Match row height
    chevronContainer.style.display = 'flex';
    chevronContainer.style.alignItems = 'center';
    chevronContainer.style.justifyContent = 'center';
    chevronContainer.style.flexShrink = '0';

    const hasChildren = folder.children && folder.children.length > 0;
    const isExpanded = expandedFolders.has(folder.path);

    if (hasChildren) {
        const chevron = document.createElement('span');
        chevron.className = 'folder-chevron' + (isExpanded ? ' expanded' : '');
        chevron.innerHTML = '▶';
        chevron.style.pointerEvents = 'none'; // Click is handled by container

        // Chevron Click Handler - SEPARATE from Row
        chevronContainer.onclick = (e) => {
            e.stopPropagation();
            toggleFolder(folder.path);
        };
        chevronContainer.style.cursor = 'pointer';
        chevronContainer.title = "Toggle Folder";
        chevronContainer.appendChild(chevron);
    }

    row.appendChild(chevronContainer);

    // 2. Folder Content (Icon + Name) - The rest of the row
    const contentContainer = document.createElement('div');
    contentContainer.className = 'folder-content';
    contentContainer.style.flex = '1';
    contentContainer.style.display = 'flex';
    contentContainer.style.alignItems = 'center';
    contentContainer.style.gap = '8px';
    contentContainer.style.overflow = 'hidden';
    contentContainer.style.paddingRight = '10px';

    // Icon Container - CLICK TO TOGGLE
    const iconContainer = document.createElement('span');
    iconContainer.className = 'folder-icon-wrapper';
    iconContainer.style.cursor = 'pointer';
    iconContainer.style.display = 'flex';
    iconContainer.style.alignItems = 'center';
    iconContainer.title = "Toggle Folder";

    iconContainer.onclick = (e) => {
        e.stopPropagation();
        toggleFolder(folder.path);
    };

    const icon = document.createElement('span');
    icon.className = 'folder-icon';
    let iconEmoji = '📁';
    if (currentFolder === folder.path) {
        iconEmoji = '🗂️';
    } else if (depth === 0) {
        iconEmoji = hasChildren && isExpanded ? '📂' : '📦';
    } else {
        iconEmoji = hasChildren && isExpanded ? '📂' : '📁';
    }
    icon.innerHTML = iconEmoji;

    iconContainer.appendChild(icon);
    contentContainer.appendChild(iconContainer);

    // Name - CLICK TO SELECT
    const name = document.createElement('span');
    name.className = 'folder-name';
    name.textContent = folder.name;
    name.onclick = (e) => {
        e.stopPropagation();
        selectFolder(folder.path);
    };
    contentContainer.appendChild(name);

    row.appendChild(contentContainer);

    // Highlight if selected
    if (currentFolder === folder.path) {
        row.classList.add('selected');
        // Add visual indicator to content container too
        contentContainer.style.fontWeight = 'bold';
        contentContainer.style.color = 'var(--accent)';
    }

    div.appendChild(row);

    // 3. Children (Recursive)
    if (hasChildren) {
        const childrenDiv = document.createElement('div');
        childrenDiv.className = 'folder-children' + (isExpanded ? '' : ' collapsed');
        // No margin-left here, indentation is handled by padding-left in rows

        folder.children.forEach(child => {
            const childElement = createFolderElement(child, depth + 1);
            childrenDiv.appendChild(childElement);
        });

        div.appendChild(childrenDiv);
    }

    return div;
}

function toggleFolder(path) {
    if (expandedFolders.has(path)) {
        expandedFolders.delete(path);
    } else {
        expandedFolders.add(path);
    }

    // Save state
    localStorage.setItem('expandedFolders', JSON.stringify([...expandedFolders]));

    renderFolderTree();
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
    renderFolderTree();
}

function collapseAllFolders() {
    expandedFolders.clear();
    localStorage.setItem('expandedFolders', JSON.stringify([]));
    renderFolderTree();
}

async function selectFolder(path) {
    currentFolder = path;
    await loadMedia(path);
    renderFolderTree(); // Re-render to update selection
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
    modalMetadata.innerHTML = `
        <div class="metadata-grid">
            <span class="metadata-label">Path:</span>
            <span class="metadata-value">
                ${file.path}
                <button class="copy-btn" onclick="copyToClipboard('${file.path.replace(/'/g, "\\'")}')">Copy</button>
            </span>
            <span class="metadata-label">Size:</span>
            <span class="metadata-value">${formatBytes(file.size)}</span>
            <span class="metadata-label">Modified:</span>
            <span class="metadata-value">${new Date(file.modified).toLocaleString()}</span>
            <span class="metadata-label">Type:</span>
            <span class="metadata-value">${file.type}</span>
            <span class="metadata-label" style="color: var(--primary);">ELO Rating:</span>
            <span class="metadata-value" style="color: var(--primary); font-weight: bold;">
                <i class="fas fa-star" style="margin-right: 5px;"></i>${file.rating || 1000}
            </span>
        </div>
    `;

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

    cover.innerHTML = `<img src="${coverSrc}" style="width: 100%; height: 100%; object-fit: cover; border-radius: 20px;">`;

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
        previewEl.innerHTML = `<img src="${API_BASE}/api/thumb?path=${encodeURIComponent(fileToMove.path)}" alt="Preview" style="width:100%;height:100%;object-fit:cover;border-radius:8px;">`;
    }

    renderMoveFolderTree();
    modal.classList.add('active');
}

function closeMoveFileModal() {
    const modal = document.getElementById('modal-move-file');
    if (modal) modal.classList.remove('active');

    fileToMove = null;
    selectedMoveFolder = null;

    // Resume show if it was playing and not intentionally paused
    if (activeShow && activeShow.isPlaying) {
        activeShow.startLoop();
    }
}

function renderMoveFolderTree() {
    const container = document.getElementById('move-folder-tree');
    if (!container) return;
    container.innerHTML = '';

    const createFolderNode = (folder, depth, parentEl) => {
        const hasChildren = folder.children && folder.children.length > 0;

        // Wrapper for this folder + its children
        const wrapper = document.createElement('div');

        // The clickable row
        const row = document.createElement('div');
        row.className = 'move-folder-node' + (selectedMoveFolder === folder.path ? ' selected' : '');
        row.style.paddingLeft = `${depth * 16}px`;

        // Chevron
        const chevron = document.createElement('span');
        chevron.className = 'folder-chevron' + (hasChildren ? '' : ' no-children');
        chevron.innerHTML = '<i class="fas fa-chevron-right"></i>';

        // Folder icon
        const icon = document.createElement('i');
        icon.className = `fas fa-folder${selectedMoveFolder === folder.path ? '-open' : ''} folder-icon`;

        // Folder name
        const name = document.createElement('span');
        name.className = 'folder-name';
        name.textContent = folder.name;
        name.title = folder.path;

        row.appendChild(chevron);
        row.appendChild(icon);
        row.appendChild(name);

        // Children container
        const childrenContainer = document.createElement('div');
        childrenContainer.className = 'move-folder-children';

        // Click on chevron toggles expand/collapse
        chevron.onclick = (e) => {
            e.stopPropagation();
            if (!hasChildren) return;
            const isExpanded = chevron.classList.contains('expanded');
            chevron.classList.toggle('expanded');
            childrenContainer.classList.toggle('expanded');
        };

        // Click on the row selects the folder
        row.onclick = (e) => {
            e.stopPropagation();
            selectMoveDestination(folder.path);
            // Also expand if has children
            if (hasChildren && !chevron.classList.contains('expanded')) {
                chevron.classList.add('expanded');
                childrenContainer.classList.add('expanded');
            }
        };

        wrapper.appendChild(row);

        // Build children recursively
        if (hasChildren) {
            folder.children.forEach(child => createFolderNode(child, depth + 1, childrenContainer));
        }

        wrapper.appendChild(childrenContainer);
        parentEl.appendChild(wrapper);
    };

    if (folderTree && folderTree.length > 0) {
        folderTree.forEach(folder => createFolderNode(folder, 0, container));
    } else {
        container.innerHTML = '<div style="padding:20px;color:var(--text-secondary);text-align:center;">No folders available</div>';
    }

    // Scroll to top
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
    renderMoveFolderTree();
}

async function confirmMoveFile() {
    if (!fileToMove || !selectedMoveFolder) return;

    const btn = document.getElementById('btn-confirm-move');
    const originalText = btn.innerHTML;
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

        if (!response.ok) throw new Error('Failed to move file');

        showToast('Moved', 'File successfully moved', 'success');

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
        showToast('Error', 'Failed to move file', 'error');
        btn.innerHTML = originalText;
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
    toast.innerHTML = `
        <div class="toast-header">
            <strong>${title}</strong>
            <button onclick="this.parentElement.parentElement.remove()">&times;</button>
        </div>
        <div class="toast-body">${message}</div>
    `;

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
    try {
        const res = await fetch(`${API_BASE}/api/stats/photo-on-this-day`);
        const data = await res.json();
        const loading = document.getElementById('photo-otd-loading');
        const content = document.getElementById('photo-otd-content');

        loading.style.display = 'none';
        content.style.display = 'block';

        if (data.photos && data.photos.length > 0) {
            let itemsHtml = '';
            data.photos.forEach(p => {
                itemsHtml += `
                    <div class="otd-item" onclick="openPhotoOTD('${p.path.replace(/'/g, "\\'")}')">
                        <img src="${API_BASE}/api/thumb?path=${encodeURIComponent(p.path)}" class="otd-photo" alt="Photo">
                        <div class="otd-title">${p.name}</div>
                        <div class="otd-subtitle">${p.year}</div>
                    </div>
                `;
            });
            content.innerHTML = `<div class="otd-carousel">${itemsHtml}</div>`;
        } else {
            content.innerHTML = '<div class="storage-text">No photos from this day in past years.</div>';
        }
    } catch (e) {
        document.getElementById('photo-otd-loading').textContent = 'Failed to load';
    }

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
            const searchUrl = `https://open.spotify.com/search/${encodeURIComponent(t.track + ' ' + t.artist)}`;
            const placeholder = `<div class="otd-spotify-placeholder"><i class="fab fa-spotify"></i></div>`;

            content.innerHTML = `
                <div class="otd-item" onclick="window.open('${searchUrl}', '_blank')">
                    ${placeholder}
                    <div class="otd-title">${t.track}</div>
                    <div class="otd-subtitle">${t.artist} • ${t.year}</div>
                </div>
            `;

            // Probeer albumcover op te halen via iTunes Search API (gratis, geen credentials nodig)
            const query = encodeURIComponent(`${t.track} ${t.artist}`);
            fetch(`https://itunes.apple.com/search?term=${query}&media=music&limit=1`)
                .then(r => r.json())
                .then(itunesData => {
                    const result = itunesData.results && itunesData.results[0];
                    if (result && result.artworkUrl100) {
                        const artUrl = result.artworkUrl100.replace('100x100', '300x300');
                        const img = document.createElement('img');
                        img.src = artUrl;
                        img.className = 'otd-photo';
                        img.alt = t.track;
                        const placeholder = content.querySelector('.otd-spotify-placeholder');
                        if (placeholder) placeholder.replaceWith(img);
                    }
                })
                .catch(() => {}); // blijf placeholder tonen bij fout
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
    renderFolderTree();
    updateBreadcrumb(folder);

    // Zoek de foto in de geladen bestanden en open de modal
    const index = filteredMedia.findIndex(f => f.name === fileName);
    if (index !== -1) {
        openMediaModal(index);
    }
}

// ==================== SERVER RESTART ====================

async function restartServer() {
    const brand = document.querySelector('.nav-brand');

    // Dubbele klik beveiliging
    if (brand && brand.dataset.restarting) return;
    if (brand) {
        brand.dataset.restarting = '1';
        brand.style.opacity = '0.5';
        brand.style.cursor = 'wait';
        brand.title = 'Server herstart...';
    }

    try {
        await fetch(`${API_BASE}/api/restart`, { method: 'POST' });
    } catch (_) {
        // Verwacht — server valt weg vóór response
    }

    // Toon overlay en poll tot server terug is
    showRestartOverlay();
}

function showRestartOverlay() {
    let overlay = document.getElementById('restart-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'restart-overlay';
        overlay.innerHTML = `
            <div class="restart-box">
                <div class="loading-spinner" style="width:32px;height:32px;margin:0 auto 14px;"></div>
                <div class="restart-title">Server herstart...</div>
                <div class="restart-sub" id="restart-sub">Even geduld</div>
            </div>`;
        document.body.appendChild(overlay);
    }
    overlay.style.display = 'flex';

    let dots = 0;
    const subEl = document.getElementById('restart-sub');
    const dotTimer = setInterval(() => {
        dots = (dots + 1) % 4;
        if (subEl) subEl.textContent = 'Even geduld' + '.'.repeat(dots);
    }, 400);

    // Poll elke 600ms tot server antwoordt
    const poll = setInterval(async () => {
        try {
            const res = await fetch(`${API_BASE}/api/health`, { cache: 'no-store' });
            if (res.ok) {
                clearInterval(poll);
                clearInterval(dotTimer);
                overlay.style.display = 'none';
                const brand = document.querySelector('.nav-brand');
                if (brand) {
                    delete brand.dataset.restarting;
                    brand.style.opacity = '';
                    brand.style.cursor = 'pointer';
                    brand.title = 'Server herstarten';
                }
                showToast('DataVault', 'Server succesvol herstart', 'success');
            }
        } catch (_) { /* nog niet klaar */ }
    }, 600);
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
                newItem.innerHTML += '<div class="actual-dna-badge">Your DNA</div>';
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

async function openStorageModal() {
    const modal = document.getElementById('modal-storage-details');
    const loading = document.getElementById('storage-breakdown-loading');
    const content = document.getElementById('storage-breakdown-content');
    const list = document.getElementById('storage-folder-list');

    modal.classList.add('active');
    loading.style.display = 'flex';
    content.style.display = 'none';

    try {
        const res = await fetch(`${API_BASE}/api/stats/storage-breakdown`);
        const data = await res.json();

        list.innerHTML = '';
        if (data.breakdown) {
            for (const [folder, gb] of Object.entries(data.breakdown)) {
                const li = document.createElement('li');
                li.className = 'storage-folder-item';
                li.innerHTML = `<span class="storage-folder-name">${folder}</span><span class="storage-folder-size">${gb} GB</span>`;
                list.appendChild(li);
            }
        } else {
            list.innerHTML = '<li>Geen data beschikbaar.</li>';
        }

        loading.style.display = 'none';
        content.style.display = 'block';
    } catch (e) {
        loading.style.display = 'none';
        list.innerHTML = '<li>Fout bij laden van storage data.</li>';
        content.style.display = 'block';
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

    header.innerHTML = `
    <div class="settings-chevron ${hasChildren ? '' : 'hidden'}">
        <i class="fas fa-chevron-right"></i>
    </div>
    <div class="settings-label">
        <i class="fas fa-folder${item.enabled ? '-open' : ''}" style="opacity: ${item.enabled ? 1 : 0.5}"></i>
        <span>${item.name}</span>
    </div>
    <label class="switch">
        <input type="checkbox" ${item.enabled ? 'checked' : ''} 
               onchange="toggleSettingsFolder('${item.path.replace(/\\/g, '\\\\')}', this.checked)">
        <span class="slider round"></span>
    </label>
`;

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
        await fetch(`${API_BASE}/api/shows/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, enabled })
        });
        showToast('Instellingen', `Folder ${enabled ? 'ingeschakeld' : 'uitgeschakeld'}`, 'success');
        reloadShows();
    } catch (e) {
        console.error("Error toggling folder:", e);
        showToast('Failed to update settings', 'error');
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
                badge.innerHTML = `<i class="fas fa-star"></i> ELO: ${newRating}`;
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
            slide.innerHTML = `<img src="${API_BASE}/api/file?path=${encodeURIComponent(file.path)}" class="cinematic-media ${kbEffect}">`;
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

        metaEl.innerHTML = `
<i class="fas fa-folder"></i> ${folderName} <span style="opacity:0.5; margin:0 5px;">|</span>
<i class="fas fa-camera"></i> ${file.name} <span style="opacity:0.5; margin:0 5px;">|</span>
<i class="fas fa-calendar"></i> ${new Date(file.modified * 1000).toLocaleDateString()}
${file.size ? `<span style="margin-left: 15px;"><i class="fas fa-hdd"></i> ${(file.size / (1024 * 1024)).toFixed(1)} MB</span>` : ''}
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
    renderSpotifyList('top-artists-list', data.top_artists, (item, i) => `
<div class="spotify-list-item clickable" role="button" onclick="window.open('https://open.spotify.com/search/${encodeURIComponent(item.name)}', '_blank'); event.stopPropagation();">
    <div class="item-rank">#${i + 1}</div>
    <div class="item-info">
        <div class="item-name">${item.name}</div>
        <div class="item-meta">${Math.round(item.ms / 60000).toLocaleString()} mins</div>
    </div>
    <div class="action-icon"><i class="fab fa-spotify"></i></div>
</div>
`);

    renderSpotifyList('top-tracks-list', data.top_tracks, (item, i) => `
<div class="spotify-list-item clickable" role="button" onclick="window.open('https://open.spotify.com/search/${encodeURIComponent(item.name + ' ' + item.artist)}', '_blank'); event.stopPropagation();">
    <div class="item-rank">#${i + 1}</div>
    <div class="item-info">
        <div class="item-name">${item.name}</div>
        <div class="item-meta">${item.artist} • ${item.count} plays</div>
    </div>
    <div class="action-icon"><i class="fas fa-play"></i></div>
</div>
`);

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
            bingeEl.innerHTML = `<a href="https://open.spotify.com/search/${encodeURIComponent(insights.top_binge.track + ' ' + insights.top_binge.artist)}" target="_blank" onclick="event.stopPropagation();" style="color: inherit; text-decoration: underline;">${insights.top_binge.track}</a>`;
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
            renderSpotifyList('discoveries-list', insights.discoveries, (item, i) => `
            <div class="spotify-list-item clickable" role="button" onclick="window.open('https://open.spotify.com/search/${encodeURIComponent(item.name)}', '_blank'); event.stopPropagation();">
                <div class="item-rank">#${i + 1}</div>
                <div class="item-info">
                    <div class="item-name">${item.name}</div>
                    <div class="item-meta">First played this year • ${Math.round(item.ms / 60000).toLocaleString()} mins</div>
                </div>
                <div class="action-icon"><i class="fab fa-spotify"></i></div>
            </div>
            `);
        } else {
            discoveriesContainer.style.display = 'none';
        }
    }
    const otdContainer = document.getElementById('on-this-day-container');
    const otdSubtitle = otdContainer ? otdContainer.querySelector('.section-subtitle') : null;
    const otdList = document.getElementById('on-this-day-list');
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
                window.open(`https://open.spotify.com/search/${encodeURIComponent(play.track + ' ' + play.artist)}`, '_blank');
            };
            el.innerHTML = `
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

function renderSpotifyList(id, items, templateFn) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = '';
    items.forEach((item, i) => {
        el.innerHTML += templateFn(item, i);
    });
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

// Open een enkel bestand direct in de modal viewer
function openFileInViewer(path) {
    const ext = path.split('.').pop().toLowerCase();
    const videoExts = ['mp4', 'mov', 'm4v', 'avi', 'mkv', 'wmv', 'flv', 'webm', '3gp', 'mpg', 'mpeg', 'mts', 'm2ts'];
    const audioExts = ['mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg', 'wma'];
    let type = 'photo';
    if (videoExts.includes(ext)) type = 'video';
    else if (audioExts.includes(ext)) type = 'audio';

    filteredMedia = [{ path, name: path.split('/').pop(), type, size: 0, modified: 0, rating: 0 }];
    const modal = document.getElementById('modal-viewer');
    if (modal) {
        openMediaModal(0);
        modal.style.display = 'flex';
    }
}

window.chatHistory = [];

function handleChatKeyPress(e) {
    if (e.key === 'Enter') {
        sendChatMessage();
    }
}

async function sendChatMessage() {
    const inputEl = document.getElementById('chat-input');
    const message = inputEl.value.trim();

    if (!message) return;

    inputEl.value = '';
    inputEl.disabled = true;
    const btn = document.querySelector('.chat-send-btn');
    if (btn) btn.disabled = true;

    const messagesContainer = document.getElementById('chat-messages');

    // Gebruikersbericht toevoegen
    const userMsg = document.createElement('div');
    userMsg.className = 'chat-message user-message';
    userMsg.innerHTML = `<div class="message-bubble">${escapeHTML(message)}</div>`;
    messagesContainer.appendChild(userMsg);
    window.chatHistory.push({ role: 'user', content: message });
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    // AI bubble met loading dots — wordt live gevuld via streaming
    const aiMsg = document.createElement('div');
    aiMsg.className = 'chat-message ai-message';
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.innerHTML = `<div class="message-loading"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>`;
    aiMsg.appendChild(bubble);
    messagesContainer.appendChild(aiMsg);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    let fullText = '';
    let firstToken = true;

    try {
        const response = await fetch(`${API_BASE}/api/chat/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages: window.chatHistory, model: 'phi3.5:latest' })
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
                try {
                    const event = JSON.parse(line.slice(6));

                    if (event.error) {
                        bubble.innerHTML = `<span style="color: var(--danger)">${escapeHTML(event.error)}</span>`;
                        return;
                    }

                    if (event.token) {
                        if (firstToken) {
                            bubble.innerHTML = '';
                            firstToken = false;
                        }
                        fullText += event.token;
                        bubble.innerHTML = escapeHTML(fullText).replace(/\n/g, '<br>');
                        messagesContainer.scrollTop = messagesContainer.scrollHeight;
                    }

                    if (event.done) {
                        // ACTION tags detecteren en uitvoeren
                        const actionMatch = fullText.match(/<<ACTION:\s*({.*?})\s*>>/s);
                        if (actionMatch) {
                            try {
                                const actionData = JSON.parse(actionMatch[1]);
                                setTimeout(() => executeAIAction(actionData), 1000);
                            } catch (parseErr) {
                                console.error('Action parse error:', parseErr);
                            }
                            fullText = fullText.replace(/<<ACTION:[\s\S]*?>>/, '').trim();
                            bubble.innerHTML = escapeHTML(fullText).replace(/\n/g, '<br>');
                        }
                        window.chatHistory.push({ role: 'assistant', content: fullText });
                    }
                } catch (_) { /* ongeldige JSON negeren */ }
            }
        }

    } catch (e) {
        console.error('Chat error:', e);
        bubble.innerHTML = `<span style="color: var(--danger)">Fout: Kan Ollama niet bereiken. Draait de server en Ollama?</span>`;
    } finally {
        inputEl.disabled = false;
        if (btn) btn.disabled = false;
        inputEl.focus();
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
}

function escapeHTML(str) {
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function executeAIAction(actionData) {
    if (!actionData || !actionData.action) return;

    switch (actionData.action) {
        case 'open_tab':
            if (actionData.target) {
                switchMainView(actionData.target);
            }
            break;

        case 'scan_duplicates':
            switchMainView('duplicates');
            // Check if the scan function exists and call it
            if (typeof findDuplicates === 'function') {
                findDuplicates();
            } else {
                const scanBtn = document.querySelector('#section-duplicates .btn-primary');
                if (scanBtn) scanBtn.click();
            }
            break;

        case 'open_folder':
            switchMainView('viewer');
            if (actionData.target) {
                const searchInput = document.getElementById('global-search');
                if (searchInput) {
                    searchInput.value = actionData.target;
                    // Trigger the existing search function
                    if (typeof handleGlobalSearch === 'function') {
                        handleGlobalSearch(actionData.target);
                    }
                }
            }
            break;

        case 'show_photos':
            if (actionData.date) {
                // Fetch photos
                const chatMessages = document.getElementById('chat-messages');

                // Add loading bubble
                const loadingBubble = document.createElement('div');
                loadingBubble.className = 'chat-message ai-message';
                loadingBubble.innerHTML = `<div class="message-bubble"><div class="message-loading"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div></div>`;
                chatMessages.appendChild(loadingBubble);
                chatMessages.scrollTop = chatMessages.scrollHeight;

                fetch(`${API_BASE}/api/stats/photos-by-date?date=${actionData.date}`)
                    .then(res => res.json())
                    .then(data => {
                        loadingBubble.remove();
                        if (data.photos && data.photos.length > 0) {
                            const container = document.createElement('div');
                            container.className = 'chat-message ai-message';

                            let imagesHtml = '';
                            data.photos.slice(0, 10).forEach(p => {
                                const escapedPath = p.path.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
                                imagesHtml += `<img src="${API_BASE}/api/thumb?path=${encodeURIComponent(p.path)}" style="width: 100px; height: 100px; object-fit: cover; border-radius: 8px; cursor: pointer; border: 1px solid rgba(255, 255, 255, 0.1);" title="${p.path.split('/').pop()}" onclick="openFileInViewer('${escapedPath}')">`;
                            });

                            container.innerHTML = `
                                <div class="message-bubble" style="background: transparent; border: none; padding: 0;">
                                    <div style="display: flex; flex-wrap: wrap; gap: 8px;">
                                        ${imagesHtml}
                                    </div>
                                </div>
                            `;
                            chatMessages.appendChild(container);
                            chatMessages.scrollTop = chatMessages.scrollHeight;
                        } else {
                            const errorBubble = document.createElement('div');
                            errorBubble.className = 'chat-message ai-message';
                            errorBubble.innerHTML = `<div class="message-bubble">Ik kon helaas geen foto's vinden uit mijn archief voor deze exacte datum.</div>`;
                            chatMessages.appendChild(errorBubble);
                            chatMessages.scrollTop = chatMessages.scrollHeight;
                        }
                    })
                    .catch(err => {
                        loadingBubble.remove();
                        console.error('Error fetching photos:', err);
                    });
            }
            break;

        case 'open_file':
            if (actionData.path) {
                openFileInViewer(actionData.path);
            }
            break;

        default:
            console.warn("Unknown AI action:", actionData.action);
    }
}
