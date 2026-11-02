// DataVault Dashboard - Main Application Logic (Apple-Style UI)

var API_BASE = '';

// ==================== STATE ====================

let currentFolder = '';
let currentMedia = [];
let filteredMedia = [];
let folderTree = [];
let foldersLoaded = false;   // /api/folders gaf een antwoord (een leeg archief is dan echt leeg)
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
    // Een keuze van de gebruiker: het nieuwe thema loopt als een cirkel uit vanaf de knop
    // (of het keuzerondje) waarmee hij koos. Bij het laden van de pagina: meteen.
    const before = document.documentElement.getAttribute('data-theme');
    const after = theme === 'auto' ? null : theme;
    if (!skipSave && before !== after && Motion.full()) {
        const from = document.activeElement && document.activeElement !== document.body
            ? document.activeElement : document.getElementById('theme-button');
        const r = from.getBoundingClientRect();
        Motion.viewTransition(() => applyTheme(theme), { kind: 'theme', origin: { x: r.left + r.width / 2, y: r.top + r.height / 2 } });
        if (window.ShellMotion) ShellMotion.themeChanged();
        return;
    }
    applyTheme(theme);
}

function applyTheme(theme) {
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
    dropdown.classList.remove('keyboard');
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
    dropdown.classList.remove('open', 'keyboard');
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
        // Met de muis geopend en dan met pijlen verder: Safari toont dan geen :focus-visible,
        // dus het menu laat zelf zien welke optie de focus heeft
        dropdown.classList.add('keyboard');
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

const MAIN_VIEWS = ['home', 'viewer', 'duplicates', 'spotify', 'shows', 'keurer', 'timeline', 'recovery', 'settings', 'albums', 'datagatherer', 'map', 'berichten', 'activity', 'series'];

function currentMainView() {
    return MAIN_VIEWS.find(v => {
        const section = document.getElementById(`section-${v}`);
        return section && section.classList.contains('active');
    }) || 'home';
}

// De titel van het tabblad per onderdeel ('Kaart · DataVault')
const VIEW_TITLES = {
    home: 'Home', viewer: 'Bestanden', duplicates: 'Duplicaten', spotify: 'Spotify', shows: 'Shows', keurer: 'Keurer',
    timeline: 'Tijdlijn', recovery: 'Herstelcentrum', settings: 'Instellingen', albums: 'Albums', datagatherer: 'DataGatherer',
    map: 'Kaart', berichten: 'Berichten', activity: 'Activiteit', series: 'Reeksen',
};

// Het scherm waar een melding bij hoort: info en succes blijven niet hangen op een ander scherm
let activeMainView = 'home';

// Het menu-element van een onderdeel (Activiteit hoort bij DataGatherer). Staat het onderdeel
// in het menu Meer (smal venster, nav-more.js), dan is dat de knop Meer.
function navEntryFor(view) {
    const tab = [...document.querySelectorAll('.top-nav [data-view]')].find(t =>
        t.dataset.view === view || (t.dataset.also || '').split(' ').includes(view)) || null;
    if (tab && tab.classList.contains('is-overflow')) return document.getElementById('nav-more-btn');
    return tab;
}

// Hoeveel de groene streep binnen een menu-element begint (zie .nav-tab.active::after)
function navIndicatorInset(tab) {
    // Het streepje zelf weet het (smaller venster: kleinere marge); anders de standaardmarge
    const left = parseFloat(getComputedStyle(tab, '::after').left);
    if (Number.isFinite(left)) return left;
    return Motion.px(tab.classList.contains('nav-icon-btn') ? '--space-sm' : '--space-md');
}

// ---------- Terug en Vooruit van de browser ----------
// Elk ander scherm is een stap in de geschiedenis ({view}), zodat ⌘[ of terugvegen in Safari naar
// het vorige scherm gaat in plaats van de app uit. Albums houdt zijn eigen adres (#albums?...).
let navRestoring = false;

function navRecord(view) {
    if (navRestoring) return;
    const keepHash = view === 'albums' && location.hash.startsWith('#albums');
    const url = location.pathname + location.search + (keepHash ? location.hash : '');
    const state = { view };
    // Een nieuwe pagina (nog zonder stap) of hetzelfde scherm: deze stap bijwerken, niet stapelen
    if (history.state && history.state.view && history.state.view !== view) history.pushState(state, '', url);
    else history.replaceState(state, '', url);
}

window.addEventListener('popstate', (e) => {
    const albumsHash = location.hash.startsWith('#albums');
    const view = e.state && MAIN_VIEWS.includes(e.state.view) ? e.state.view : (albumsHash ? 'albums' : 'home');
    // Tot na de hashchange die bij deze stap hoort: Albums leest het adres hier, niet nog eens daar
    navRestoring = true;
    setTimeout(() => { navRestoring = false; }, 0);
    if (view === 'albums' && currentMainView() === 'albums' && typeof albumsRestore === 'function') albumsRestore();
    else switchMainView(view);
});

function navIsRestoring() {
    return navRestoring;
}

function switchMainView(view) {
    if (!MAIN_VIEWS.includes(view)) return;
    const previousView = activeMainView;
    const previousTab = navEntryFor(previousView);
    if (view !== activeMainView) {
        document.querySelectorAll('#toast-container .toast[data-view]').forEach(t => {
            if (t.dataset.view !== view) { clearTimeout(t._hideTimer); t.remove(); }
        });
    }
    activeMainView = view;
    // Sommige onderdelen zetten de meldingen op een eigen plek (de Keurer: naast de kaart)
    const toasts = document.getElementById('toast-container');
    if (toasts) toasts.dataset.view = view;
    navRecord(view);
    document.title = view === 'home' ? 'DataVault' : `${VIEW_TITLES[view]} · DataVault`;
    // Update tabs (ook de tandwielknop van Instellingen)
    document.querySelectorAll('.top-nav [data-view]').forEach(tab => {
        // data-also: een onderdeel zonder eigen knop in het menu (Activiteit hoort bij DataGatherer)
        const on = tab.dataset.view === view || (tab.dataset.also || '').split(' ').includes(view);
        tab.classList.toggle('active', on);
        if (on) tab.setAttribute('aria-current', 'page'); else tab.removeAttribute('aria-current');
        // In een smal venster schuift het menu: het actieve onderdeel altijd in beeld
        const strip = on && tab.closest('.nav-tabs');
        if (strip && strip.scrollWidth > strip.clientWidth) {
            const left = tab.offsetLeft - strip.offsetLeft;
            if (left < strip.scrollLeft || left + tab.offsetWidth > strip.scrollLeft + strip.clientWidth) {
                strip.scrollLeft = left - (strip.clientWidth - tab.offsetWidth) / 2;
            }
        }
    });

    // Staat het onderdeel in Meer, dan krijgt Meer het groene streepje (nav-more.js)
    if (window.NavMore) NavMore.sync(view);

    // Update sections
    MAIN_VIEWS.forEach(v => {
        const section = document.getElementById(`section-${v}`);
        if (!section) return;
        section.classList.toggle('active', v === view);
        section.classList.toggle('hidden', v !== view);
    });

    // Show/hide sidebar
    document.getElementById('sidebar').style.display = view === 'viewer' ? 'flex' : 'none';

    // Beweging (docs/MOTION.md): de groene streep glijdt naar het nieuwe onderdeel en de pagina
    // komt binnen vanuit de richting van het menu. Alles is synchroon klaar; alleen de
    // weergave beweegt, dus code die na switchMainView meteen verder werkt, merkt er niets van.
    if (view !== previousView) {
        const nextTab = navEntryFor(view);
        let direction = 0;
        if (previousTab && nextTab && previousTab !== nextTab) {
            direction = Math.sign(nextTab.getBoundingClientRect().left - previousTab.getBoundingClientRect().left);
        }
        // De schil (motion-shell.js): een elastische streep en een eigen ritme per onderdeel
        const section = document.getElementById(`section-${view}`);
        if (window.ShellMotion) {
            ShellMotion.navGlide(previousTab, nextTab, { inset: navIndicatorInset });
            ShellMotion.enterView(view, section, direction);
        } else {
            Motion.glide(previousTab, nextTab, { inset: navIndicatorInset });
            Motion.enterPage(section, { direction });
        }
    }

    // Clear search
    document.getElementById('global-search').value = '';
    updateSearchFieldForView(view);

    if (view !== 'spotify') stopSpotifyClock();
    homeOtdCycle(view === 'home');
    // Het adres (#albums?...) hoort alleen bij Albums
    if (view !== 'albums' && typeof albumsLeave === 'function') albumsLeave();

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
    } else if (view === 'albums') {
        if (typeof albumsInit === 'function') albumsInit();
    } else if (view === 'datagatherer') {
        if (typeof datagathererInit === 'function') datagathererInit();
    } else if (view === 'map') {
        if (typeof mapInit === 'function') mapInit();
    } else if (view === 'berichten') {
        if (typeof msgInit === 'function') msgInit();
    } else if (view === 'activity') {
        if (typeof activityInit === 'function') activityInit();
    } else if (view === 'series') {
        if (typeof seriesInit === 'function') seriesInit();
    }
}

// ==================== SHOWS & STORIES ====================

// De kop van Shows (titel, uitleg, knoppen) staat er altijd: tijdens het laden, na een fout en met inhoud
const SHOWS_HEADER_HTML = html`
        <div class="page-header shows-header">
            <div>
                <h1 class="page-title"><i class="fas fa-film" aria-hidden="true"></i> Shows</h1>
                <p class="page-subtitle">Diavoorstellingen van je mappen, met je mooiste foto's vaker in beeld.</p>
            </div>
            <div class="shows-header-actions">
            <button class="btn btn-secondary" id="btn-elo" onclick="openElo()">
                <i class="fas fa-scale-balanced" aria-hidden="true"></i> Welke is mooier?
            </button>
            <button class="btn btn-secondary" id="btn-shuffle-shows" onclick="shuffleShows()">
                <i class="fas fa-random" aria-hidden="true"></i> Shows husselen
            </button>
            <button class="btn btn-secondary" onclick="openSettingsModal()">
                <i class="fas fa-sliders-h" aria-hidden="true"></i> Bronnen beheren
            </button>
            </div>
        </div>
    `;

function showsHeader({ busy = false } = {}) {
    const tpl = document.createElement('template');
    setHTML(tpl, SHOWS_HEADER_HTML);
    const header = tpl.content.firstElementChild;
    // Husselen heeft pas iets te doen als de shows er zijn
    if (busy) header.querySelector('#btn-shuffle-shows').disabled = true;
    return header;
}

// Plaatshouders in de vorm van wat komt: een grote uitgelichte show en een rij collecties
function showsSkeleton() {
    return createEl('div', { class: 'shows-skeleton', role: 'status', 'aria-label': 'Shows laden…' },
        createEl('div', { class: 'skeleton shows-skeleton-hero', 'aria-hidden': 'true' }),
        createEl('div', { class: 'skeleton skeleton-line shows-skeleton-title', 'aria-hidden': 'true' }),
        createEl('div', { class: 'shows-grid-folders', 'aria-hidden': 'true' },
            ...[0, 1, 2, 3].map(() => createEl('div', { class: 'skeleton show-card-skeleton' }))));
}

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
    container.replaceChildren(showsHeader({ busy: true }), showsSkeleton());
    try {
        const stories = await fetchJSON(url);
        _prefetchCache[url] = Promise.resolve(stories);
        renderShows(stories);
    } catch (e) {
        console.error('Error loading shows:', e);
        container.replaceChildren(showsHeader({ busy: true }), errorState('Shows laden mislukt', e, loadShows));
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

    const empty = !data || (!featured && (!shows || shows.length === 0));

    // De kop staat er ook zonder shows: 'Welke is mooier?' werkt dan nog op de hele bibliotheek
    container.replaceChildren(showsHeader());

    if (empty) {
        // Husselen en Bronnen beheren staan dan al in de lege staat (of hebben niets om te doen)
        container.querySelectorAll('#btn-shuffle-shows, .shows-header-actions [onclick="openSettingsModal()"]')
            .forEach(btn => btn.remove());
        container.appendChild(emptyState({
            icon: 'fa-folder-open', title: 'Nog geen shows',
            text: 'Shows komen uit de mappen in de map library. Zet er een paar aan bij Bronnen beheren.',
            action: { label: 'Bronnen beheren', onClick: openSettingsModal, primary: true },
        }));
        return;
    }

    // 1. Hero Section (Featured - Random from Vault)
    if (featured && featured.files && featured.files.length > 0) {
        const hero = document.createElement('div');
        hero.className = 'shows-hero';
        const cover = featured.files[0];
        hero.onclick = () => startShow(featured, { from: hero.querySelector('.shows-hero-img'), fromPath: cover.path });
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
        upgradeHeroImage(hero.querySelector('.shows-hero-img'), cover);
        if (!showsFlip) {
            // Eén heldenmoment: de foto zet zich langzaam neer, de tekst rijst er in ritme op
            Motion.animate(hero.querySelector('.shows-hero-img'), [{ transform: 'scale(1.06)' }, { transform: 'none' }],
                { duration: '--dur-6', easing: '--ease-emphasized', key: 'hero' });
            Motion.stagger(hero.querySelector('.shows-hero-content').children, 'rise', { delay: Motion.ms('--dur-2', 140) });
        }
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
            card.onclick = () => playFolderShow(show, card.querySelector('.show-card-thumb'));
            makeActivatable(card, `Afspelen: ${show.title}`);

            let thumbUrl = '';
            if (show.preview) {
                thumbUrl = vaultFileUrl('thumb', show.preview.path);
            }

            // Een stapel foto's: de omslag voorop, nog twee uit de map erachter die bij hover
            // uitwaaieren (alleen als de map ze heeft); tekst met schaduw op de omslag
            const stack = thumbUrl ? (show.stack || []).slice(0, 2) : [];
            card.classList.toggle('has-stack', stack.length > 0);
            setHTML(card, html`
                ${stack.length ? html`<div class="show-card-stack" aria-hidden="true">${stack.map(item => html`<img src="${vaultFileUrl('thumb', item.path)}" alt="" loading="lazy">`)}</div>` : ''}
                <div class="show-card-face">
                    ${thumbUrl ? html`<img src="${thumbUrl}" class="show-card-thumb" alt="" loading="lazy">` : html`<div class="show-card-empty"><i class="fas fa-images" aria-hidden="true"></i></div>`}
                    <div class="show-card-caption">
                        <h4 class="show-card-title">${show.title}</h4>
                        <span class="show-card-meta">Map</span>
                    </div>
                </div>
            `);

            grid.appendChild(card);
        });

        container.appendChild(grid);
        // De collecties komen als een golf binnen; na husselen glijden ze naar hun nieuwe plek
        if (!showsFlip) MediaMotion.cascade(grid.children, { revealRest: true });
    }
}

let showsFlip = false;

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

    // Elke collectie glijdt naar zijn nieuwe plek (FLIP op de titel)
    const grid = document.querySelector('#shows-container .shows-grid-folders');
    const key = el => (el.querySelector('.show-card-title') || {}).textContent;
    showsFlip = true;
    try {
        MediaMotion.flipByKey(grid ? grid.children : [], key, () => renderShows(_lastShowsData),
            { after: () => document.querySelectorAll('#shows-container .shows-grid-folders > .show-card'), spring: 'smooth' });
    } finally {
        showsFlip = false;
    }
}

async function playFolderShow(showObj, coverImg = null) {
    try {
        // Fetch files for this folder
        const files = await fetchJSON(`${API_BASE}/api/shows/folder-content?path=${encodeURIComponent(showObj.path)}`);

        if (files && files.length > 0) {
            startShow({
                title: showObj.title,
                files: files
            }, { from: coverImg, fromPath: showObj.preview && showObj.preview.path });
        } else {
            showToast('Empty', 'No media found in this folder', 'warning');
        }
    } catch (e) {
        toastFetchError('Show starten mislukt', e);
    }
}

// Het scorelabel in de slideshow: een ster met het getal, en voluit 'Score 1000' als naam
// en tooltip, zodat duidelijk is wat het getal is
function setScoreBadge(badge, score) {
    if (!badge) return;
    const label = `Score ${score}`;
    badge.title = `${label}: stijgt als je een foto mooi vindt, daalt bij minder mooi`;
    badge.setAttribute('aria-label', label);
    badge.setAttribute('role', 'img');
    badge.replaceChildren(createEl('i', { class: 'fas fa-star', 'aria-hidden': 'true' }), ` ${score}`);
}

// ==================== FOLDER TREE ====================

async function loadFolders() {
    try {
        const data = await fetchJSON(`${API_BASE}/api/folders`);
        folderTree = data.folders;
        foldersLoaded = true;

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
        showFoldersFailed(error);
    }
}

// De mappen kwamen niet (archief los, serverfout): geen lege pagina maar zeggen wat er is,
// met 'Opnieuw proberen'. Komt het archief terug, dan laadt reloadAfterVaultReturn ze vanzelf.
function showFoldersFailed(error) {
    const gone = error && error.code === 'vault_unavailable';
    if (!folderTree || folderTree.length === 0) {
        const tree = document.getElementById('folder-tree');
        if (tree) tree.replaceChildren(createEl('div', {
            class: 'folder-tree-empty', text: gone ? 'Archief niet bereikbaar' : 'Mappen niet geladen',
        }));
    }
    if (currentFolder) return;
    ++mediaLoadSeq;
    currentMedia = [];
    filteredMedia = [];
    gridItems = [];
    document.getElementById('media-grid').replaceChildren(
        errorState(gone ? 'Archief niet bereikbaar' : 'Mappen konden niet geladen worden', error, () => loadFolders()));
    updateBreadcrumb('');
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
    // Koos de gebruiker intussen iets anders (een andere map, 'Archief'), dan hoort het
    // kruimelpad en de boom bij die keuze, niet bij deze
    if (!(await loadMedia(path))) return;
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
    gridMotion = 'enter';
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

// Omgekeerd: een pad vanaf het archief (zoals /api/stats ze geeft) naar het volledige pad,
// waarmee Bestanden en de mappenboom werken
function fullVaultPath(path) {
    const first = (folderTree || [])[0];
    if (!first || !path || path.startsWith('/')) return path;
    return `${first.path.slice(0, first.path.lastIndexOf('/'))}/${path}`;
}

// Hét padlabel van de app: vanaf het archief ('RAW/2019/zomer', 'TRASH/…', 'Archief' voor de
// archiefmap zelf), en een lang pad ingekort in het midden ('RAW/…/zomer/dag 2'), zodat begin
// en eind herkenbaar blijven. Toon het in mono (class path-label); het hele pad hoort in title.
function displayPath(path, max = 48) {
    const text = String(path || '');
    const first = (folderTree || [])[0];
    const vault = first ? first.path.slice(0, first.path.lastIndexOf('/')) : '';
    let rel;
    if (vault && text === vault) return 'Archief';
    if (vault && text.startsWith(vault + '/')) rel = text.slice(vault.length + 1);
    else if (!vault) {
        // De mappen zijn nog niet geladen: dan in elk geval het eind van het pad
        const parts = text.split('/').filter(Boolean);
        rel = parts.length > 2 ? `…/${parts.slice(-2).join('/')}` : parts.join('/');
    } else rel = text;
    return middleEllipsis(rel, max);
}

// Midden eruit: de bovenste map, '…' en zoveel mogelijk mappen van het eind
function middleEllipsis(rel, max) {
    if (rel.length <= max) return rel;
    const parts = rel.split('/');
    const head = parts[0];
    let tail = parts[parts.length - 1];
    for (let i = parts.length - 2; i > 0; i--) {
        const next = `${parts[i]}/${tail}`;
        if (head.length + 3 + next.length > max) break;
        tail = next;
    }
    const out = parts.length > 1 ? `${head}/…/${tail}` : tail;
    return out.length <= max ? out : `${out.slice(0, Math.ceil(max / 2) - 1)}…${out.slice(-Math.floor(max / 2))}`;
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

// Geeft true als dit antwoord (of de foutmelding) getoond is, false als intussen een andere
// map of het archiefbegin gekozen is
async function loadMedia(folder) {
    const seq = ++mediaLoadSeq;
    const grid = document.getElementById('media-grid');
    // Plaatshouders als het even duurt (een grote map, een trage schijf), niet bij elke klik
    const slow = setTimeout(() => {
        if (seq === mediaLoadSeq) grid.replaceChildren(skeletonCards(12));
    }, 150);
    try {
        const data = await fetchJSON(`${API_BASE}/api/media?folder=${encodeURIComponent(folder)}`);
        if (seq !== mediaLoadSeq) return false;
        currentMedia = data.files;
        filteredMedia = [...currentMedia];
        gridFocusIndex = 0;
        gridMotion = 'enter';   // een andere map: de tegels komen als een golf binnen

        applySortAndRender();
        return true;
    } catch (error) {
        if (seq !== mediaLoadSeq) return false;
        currentMedia = [];
        filteredMedia = [];
        gridItems = [];
        grid.replaceChildren(errorState('Deze map kon niet geladen worden', error, () => loadMedia(folder)));
        toastFetchError('Bestanden laden mislukt', error);
        return true;
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
    gridMotion = 'flip';    // dezelfde tegels, een andere volgorde: ze glijden naar hun plek
    applySortAndRender();
}

function toggleView(view) {
    currentView = view;

    document.querySelectorAll('.view-toggle-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === view);
        btn.setAttribute('aria-pressed', String(btn.dataset.view === view));
    });

    // Raster en lijst vloeien in elkaar over (View Transition; zonder beweging meteen)
    Motion.viewTransition(() => {
        const grid = document.getElementById('media-grid');
        grid.classList.toggle('list-view', view === 'list');
        grid.classList.toggle('grid-view', view === 'grid');
        renderMediaGrid();
    }, { kind: 'swap' });
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
// Hoe de volgende keer tekenen beweegt (media-motion.js): 'enter' (een andere map: een golf),
// 'flip' (sorteren, filteren, een bestand weg: de tegels glijden naar hun nieuwe plek) of
// niets. Eén keer: renderMediaGrid zet hem terug.
let gridMotion = null;

// keepCount: na verplaatsen of verversen evenveel kaarten tonen als ervoor, zodat de
// scrollpositie niet verspringt
function renderMediaGrid(keepCount = false) {
    const motion = gridMotion;
    gridMotion = null;
    if (motion === 'flip' && gridItems.length) {
        const grid = document.getElementById('media-grid');
        MediaMotion.flipByKey(grid.querySelectorAll('[data-grid-index]'), el => el.dataset.motionKey || el.dataset.filename,
            () => drawMediaGrid(keepCount), { after: () => grid.querySelectorAll('[data-grid-index]') });
        return;
    }
    drawMediaGrid(keepCount);
    if (motion === 'enter') {
        const grid = document.getElementById('media-grid');
        MediaMotion.cascade(grid.querySelectorAll('[data-grid-index]'), { revealRest: true, owner: grid });
    }
}

function drawMediaGrid(keepCount) {
    const grid = document.getElementById('media-grid');
    MediaMotion.release(grid);   // wachtende 'in beeld scrollen' van de vorige tegels
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
                icon: 'fa-search', title: 'Niets gevonden',
                text: `Geen bestand in deze map heeft "${searching}" in de naam. Slim zoeken kijkt in de hele bibliotheek, ook naar wat er op de foto staat.`,
                action: typeof openSearch === 'function'
                    ? { label: 'Zoek in de hele bibliotheek', primary: true, onClick: () => openSearch(searching) }
                    : { label: 'Zoekopdracht wissen', onClick: () => {
                        const input = document.getElementById('global-search');
                        input.value = '';
                        handleGlobalSearch('');
                        input.focus();
                    } },
            })
            : currentFolder
                ? emptyState({ icon: 'fa-folder-open', title: 'Deze map is leeg', text: 'Er staan hier geen bestanden of submappen.' })
                : (foldersLoaded && folderTree.length === 0)
                    ? emptyState({ icon: 'fa-box-open', title: 'Het archief is leeg', text: 'Zet mappen met foto\'s, video\'s of muziek in het archief; ze verschijnen hier.',
                        action: { label: 'Opnieuw kijken', onClick: () => loadFolders() } })
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
    const cards = [];
    for (let i = gridRendered; i < end; i++) {
        const item = gridItems[i];
        const card = item.type === 'folder' ? createFolderCard(item) : createMediaCard(item);
        // Eén kaart is met Tab bereikbaar (de laatst gekozen); de pijltjes gaan naar de rest
        card.dataset.gridIndex = String(i);
        card.tabIndex = i === gridFocusIndex ? 0 : -1;
        fragment.appendChild(card);
        cards.push(card);
    }
    gridRendered = end;
    grid.appendChild(fragment);
    return cards;
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
        // Het volgende stuk komt rij voor rij binnen zodra het in beeld scrolt
        MediaMotion.revealTiles(renderMoreGridCards(), { owner: document.getElementById('media-grid') });
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
    card.dataset.motionKey = file.path;   // de viewer groeit hieruit en krimpt hierheen terug
    card.onclick = () => openMediaModal(file);
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', file.name);

    // Thumbnail
    const img = document.createElement('img');
    img.className = 'media-thumbnail loading';
    img.alt = file.name;

    // Thumbnail URL logic
    let thumbUrl = `${API_BASE}/api/thumb?path=${encodeURIComponent(file.path)}`;

    if (file.type === 'other' || file.type === 'audio') {
        // Geen foto of video (pdf, zip, audio, ...): een bestandsicoon op een rustig vlak, nooit een miniatuur
        card.classList.add('other-card');
        const icon = file.type === 'audio' ? 'fa-music' : 'fa-file';
        card.appendChild(createEl('div', { class: 'other-card-icon' }, createEl('i', { class: `fas ${icon}` })));
        card.appendChild(createEl('div', { class: 'media-card-name', text: file.name }));
        card.appendChild(createListMeta(file));
        return card;
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

// Een vaste hoes per audiobestand (een van 15 varianten, gekozen op de naam)
function audioThumbUrl(name) {
    let hash = 0;
    for (let i = 0; i < name.length; i++) {
        hash = name.charCodeAt(i) + ((hash << 5) - hash);
    }
    return `assets/audio_thumbs/audio_thumb_var${Math.abs(hash) % 15 + 1}.png`;
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
    // In Bestanden filtert typen de geopende map. Op een ander scherm filtert typen niets weg
    // dat je niet ziet: Enter opent het zoekvenster (search.js) voor de hele bibliotheek.
    if (currentMainView() !== 'viewer') return;
    searchTimer = setTimeout(() => handleGlobalSearch(query), SEARCH_DEBOUNCE_MS);
}

// Enter: zoeken in de hele bibliotheek (Slim zoeken of op bestandsnaam), ook vanuit Bestanden
function globalSearchKeydown(event) {
    const input = event.currentTarget;
    if (event.key !== 'Enter' || !input.value.trim()) return;
    event.preventDefault();
    if (typeof openSearch === 'function') {
        openSearch(input.value);
        if (currentMainView() === 'viewer') handleGlobalSearch('');    // de mapfilter blijft niet achter het venster hangen
        return;
    }
    switchMainView('viewer');
    handleGlobalSearch(input.value);
}

// Wat het zoekveld doet, zichtbaar in de plaatshouder en (op andere schermen) de Enter-hint
// In een smaller venster is het zoekveld korter (er staan meer knoppen rechts): dan een korte tekst
const SEARCH_COMPACT = window.matchMedia('(max-width: 1240px)');
SEARCH_COMPACT.addEventListener('change', () => updateSearchFieldForView(currentMainView()));
document.addEventListener('DOMContentLoaded', () => updateSearchFieldForView(currentMainView()));

function updateSearchFieldForView(view) {
    const input = document.getElementById('global-search');
    if (!input) return;
    const here = view === 'viewer';
    input.placeholder = SEARCH_COMPACT.matches ? (here ? 'Zoek in map…' : 'Zoeken…')
        : (here ? 'Zoek in deze map…' : 'Zoek in je foto\u2019s…');
    input.setAttribute('aria-label', here ? 'Zoek in de geopende map; Enter zoekt in de hele bibliotheek (sneltoets /)'
        : 'Zoek in de hele bibliotheek: typ en druk op Enter (sneltoets /)');
    input.closest('.search-field').classList.toggle('search-elsewhere', !here);
}

function handleGlobalSearch(query) {
    clearTimeout(searchTimer);
    query = searchKey(query);
    if (gridMotion !== 'enter') gridMotion = 'flip';   // typen: wat blijft schuift aan, niets wacht

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
    more: null,         // async (richting -1|1) => [bestanden]: een lijst die per pagina laadt (Albums)
    step: 0,            // de richting van het laatste bladeren (1, -1, 0): de foto schuift die kant op
    carry: 0,           // zoveel px was de foto al weggeveegd (MediaMotion.viewerSwipe)
    moreDone: {},       // richting -> true als er aan die kant niets meer komt
};

function viewerFile() {
    return viewer.index >= 0 && viewer.index < viewer.items.length ? viewer.items[viewer.index] : null;
}

function isViewerOpen() {
    return document.getElementById('modal-viewer').classList.contains('active');
}

// items: lijst bestandsobjecten van de server ({path, name, type, size, modified|timestamp, rating?}).
// more: voor een lijst die nog niet helemaal geladen is; aan het eind (of begin) haalt de viewer
// daarmee de volgende (of vorige) bestanden op en bladert hij gewoon verder.
function openViewer(items, index, { onRemoved = null, more = null } = {}) {
    const files = (items || []).filter(f => f && f.type !== 'folder');
    const start = files.indexOf(items[index]);
    if (start < 0) return;
    viewer.items = files;
    viewer.index = start;
    viewer.onRemoved = onRemoved;
    viewer.more = more;
    viewer.moreDone = {};
    renderViewerFile();
    const modal = document.getElementById('modal-viewer');
    const wasOpen = modal.classList.contains('active');
    modal.classList.add('active');
    if (!wasOpen) viewerMorph('open');
    // Focus uit een bedekt veld halen (bv. de chat-invoer), anders typt de gebruiker daar blind verder
    const focused = document.activeElement;
    if (focused && focused !== document.body && !modal.contains(focused)) focused.blur();
}

// ---------- Beweging: de foto groeit uit zijn miniatuur (en krimpt terug) ----------
// De miniatuur is wat de gebruiker net aanklikte (of met Enter opende), of een tegel met
// data-motion-key gelijk aan het pad van het bestand. Zonder miniatuur in beeld: gewoon het
// venster dat invaagt. Zie Motion.sharedElement en docs/MOTION.md.
let viewerSourceEl = null;
document.addEventListener('pointerdown', (e) => { viewerSourceEl = e.target.closest ? e.target.closest('img, [data-motion-key]') : null; }, true);
document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') viewerSourceEl = document.activeElement;
}, true);

let viewerMorphFrom = null;     // {file, thumb}: waar de geopende foto vandaan kwam

function viewerThumbFor(file, opening) {
    if (!file) return null;
    const keyed = [...document.querySelectorAll('[data-motion-key]')].find(el => el.dataset.motionKey === file.path
        && !el.closest('#modal-viewer'));
    const usable = img => (img && img.tagName === 'IMG' && img.naturalWidth > 0 && img.isConnected ? img : null);
    if (keyed) {
        const img = usable(keyed.tagName === 'IMG' ? keyed : keyed.querySelector('img'));
        if (img) return img;
    }
    if (!opening) return viewerMorphFrom && viewerMorphFrom.file === file ? usable(viewerMorphFrom.thumb) : null;
    const src = viewerSourceEl && viewerSourceEl.isConnected && !viewerSourceEl.closest('#modal-viewer') ? viewerSourceEl : null;
    if (!src) return null;
    return usable(src.tagName === 'IMG' ? src : src.querySelector('img'));
}

function viewerMorph(direction) {
    if (!Motion.full()) return;
    const file = viewerFile();
    if (!file || file.type === 'audio' || file.type === 'other') return;
    const big = document.getElementById('modal-image') || document.querySelector('#modal-body video');
    const thumb = viewerThumbFor(file, direction === 'open');
    if (direction === 'open') viewerMorphFrom = thumb ? { file, thumb } : null;
    if (!big || !thumb) return;
    const radius = parseFloat(getComputedStyle(big).borderTopLeftRadius) || 0;
    if (direction === 'open') {
        // Nog niet geladen: de plek uit de verhouding van de miniatuur en het inhoudsvak van de
        // viewer (zonder zijn opvulling; de foto is daar hooguit 100% hoog, zoals in de CSS)
        let rect = null;
        if (!(big.naturalWidth > 0)) {
            const bodyEl = document.getElementById('modal-body');
            const b = bodyEl.getBoundingClientRect();
            const cs = getComputedStyle(bodyEl);
            const pad = side => parseFloat(cs[`padding${side}`]) || 0;
            const box = { left: b.left + pad('Left'), top: b.top + pad('Top'),
                width: b.width - pad('Left') - pad('Right'), height: b.height - pad('Top') - pad('Bottom') };
            const aspect = thumb.naturalWidth / thumb.naturalHeight;
            let w = box.width, h = w / aspect;
            if (h > box.height) { h = box.height; w = h * aspect; }
            rect = { left: box.left + (box.width - w) / 2, top: box.top + (box.height - h) / 2, width: w, height: h };
        }
        Motion.sharedElement(thumb, big, { direction: 'open', rect, radius, src: thumb.currentSrc || thumb.src });
    } else {
        Motion.sharedElement(thumb, big, { direction: 'close', radius, src: big.tagName === 'IMG' ? (big.currentSrc || big.src) : (thumb.currentSrc || thumb.src) });
    }
}

// De bestanden zoals ze in de Files-grid staan (zonder mappen), in dezelfde volgorde
function gridFiles() {
    return filteredMedia.filter(item => item.type !== 'folder');
}

// Een grote omslagfoto begint met de miniatuur (meteen in beeld) en wisselt naar het
// originele bestand zodra dat geladen is, maar alleen voor JPEG/PNG/WebP tot 20 MB: geen
// RAW of HEIC (de browser toont die niet overal) en geen enorme bestanden
const HERO_FULL_EXTS = ['.jpg', '.jpeg', '.png', '.webp'];
function loadFullImage(file, onReady) {
    if (!file || !file.path) return;
    const ext = (file.path.match(/\.[^./]+$/) || [''])[0].toLowerCase();
    if (!HERO_FULL_EXTS.includes(ext) || (typeof file.size === 'number' && file.size > 20 * 1024 * 1024)) return;
    const full = new Image();
    full.onload = () => onReady(full.src);
    full.src = vaultFileUrl('file', file.path);
}

function upgradeHeroImage(img, file) {
    if (img) loadFullImage(file, src => { if (img.isConnected) img.src = src; });
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
    gridMotion = 'flip';    // de tegel gaat zichtbaar weg, de rest schuift aan
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
    // Bij bladeren gaat de vorige foto als spook opzij (MediaMotion.viewerStep); meten vóór het vervangen
    const step = viewer.step;
    const carry = viewer.carry;
    viewer.step = 0;
    viewer.carry = 0;
    const oldMedia = step ? modalBody.querySelector(':scope > #modal-image') : null;
    const oldRect = oldMedia ? oldMedia.getBoundingClientRect() : null;
    // Alles behalve de navigatiezones vervangen
    const zones = [...modalBody.querySelectorAll(':scope > .modal-nav-zone')];
    modalBody.replaceChildren(...zones);
    modalZoom = 1;
    panOffset = { x: 0, y: 0 };
    modalBody.classList.remove('zoomed');
    modalBody.classList.toggle('has-video', file.type === 'video');
    zones.forEach(zone => {
        const right = zone.classList.contains('right');
        const step = right ? 1 : -1;
        const edge = right ? viewer.index >= viewer.items.length - 1 : viewer.index <= 0;
        zone.hidden = edge && !(viewer.more && !viewer.moreDone[step]);
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
    if (step) MediaMotion.viewerStep(modalBody, oldMedia, oldRect, media, step, carry);
    else MediaMotion.clearViewerGhost();

    modalMetadata.replaceChildren(viewerMetadata(file));
    if (typeof searchViewerChanged === 'function') searchViewerChanged(file);
    if (typeof mapViewerHook === 'function') mapViewerHook(file);   // 'Op kaart tonen' (map.js)
}

// Alleen echte gegevens: de datum van het bestand (of de opnamedatum uit de tijdlijn) en
// de Shows-waardering als er echt over dit bestand is gestemd. Plaats, camera en afmetingen
// komen uit de bibliotheek zodra /api/map/locate antwoordt (viewerAddDetails, via map.js).
function viewerMetadata(file) {
    const rows = [];
    const add = (label, ...value) => rows.push(
        createEl('span', { class: 'metadata-label', text: label }),
        createEl('span', { class: 'metadata-value' }, ...value));

    if (typeof file.timestamp === 'number' && file.timestamp > 0) {
        const source = { exif: 'opnamedatum', video: 'opnamedatum', birthtime: 'aanmaakdatum van het bestand', mtime: 'wijzigingsdatum van het bestand' }[file.date_source];
        add('Datum', formatTimestampNl(file.timestamp), source ? createEl('span', { class: 'metadata-note', text: source }) : null);
    } else if (typeof file.modified === 'number' && file.modified > 0) {
        add('Gewijzigd', formatTimestampNl(file.modified));
    }
    add('Soort', { photo: 'Foto', video: 'Video', audio: 'Audio', other: 'Ander bestand' }[file.type] || file.type,
        typeof file.size === 'number' ? createEl('span', { class: 'metadata-note mono', text: formatBytes(file.size) }) : null);
    if (typeof file.rating === 'number') {
        const votes = typeof file.votes === 'number' ? ` (${file.votes} ${file.votes === 1 ? 'stem' : 'stemmen'})` : '';
        add('Shows-waardering', createEl('i', { class: 'fas fa-star', 'aria-hidden': 'true' }), `${file.rating}${votes}`);
    }
    rows.push(createEl('span', { class: 'metadata-label', text: 'Pad' }),
        createEl('span', { class: 'metadata-value metadata-path' },
            createEl('span', { title: file.path }, ...pathNodes(archivePath(file.path))),
            createEl('button', { class: 'copy-btn', text: 'Kopieer', title: 'Kopieer het volledige pad', onClick: () => copyToClipboard(file.path) })));
    return createEl('div', { class: 'metadata-grid' }, ...rows);
}

// Een tijdstip (seconden) als '15 juni 2021, 11:20'
function formatTimestampNl(seconds) {
    return new Date(seconds * 1000).toLocaleString('nl-NL', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// Plaats, camera en afmetingen uit de bibliotheek (antwoord van /api/map/locate) boven het
// pad in de gegevens van de viewer zetten; alleen wat bekend is
// Wat de plaatsnaam zelf niet zegt, stil erachter: de provincie en of het aan de kust is
// ('4 km ten oosten van Olhão, Portugal' + 'district Faro · aan de kust')
function viewerPlaceNote(label, parts) {
    if (!parts) return null;
    const bits = [];
    if (parts.region && !label.includes(parts.region)) bits.push(parts.region_kind ? `${parts.region_kind} ${parts.region}` : parts.region);
    if (parts.coast && !parts.sea && !/kust/i.test(label)) bits.push('aan de kust');
    return bits.length ? createEl('span', { class: 'metadata-note', text: bits.join(' · ') }) : null;
}

function viewerAddDetails(file, found) {
    const grid = document.querySelector('#modal-metadata .metadata-grid');
    if (!grid || viewerFile() !== file || grid.querySelector('.metadata-details')) return;
    const d = found.details || {};
    // Alleen een wijzigingsdatum bekend in de map? Dan de opnamedatum uit de bibliotheek
    const changed = [...grid.querySelectorAll('.metadata-label')].find(el => el.textContent === 'Gewijzigd');
    if (changed && typeof d.taken === 'number' && d.taken > 0) {
        const source = { exif: 'opnamedatum', video: 'opnamedatum' }[d.taken_source];
        changed.textContent = 'Datum';
        changed.nextElementSibling.replaceChildren(formatTimestampNl(d.taken),
            source ? createEl('span', { class: 'metadata-note', text: source }) : '');
    }
    const rows = [];
    const add = (label, ...value) => rows.push(
        createEl('span', { class: 'metadata-label metadata-details', text: label }),
        createEl('span', { class: 'metadata-value' }, ...value));
    if (found.place) add('Plaats', found.place, viewerPlaceNote(found.place, found.place_parts));
    if (d.camera) add('Camera', d.camera);
    if (d.width && d.height) {
        const mp = d.width * d.height / 1e6;
        add('Afmetingen', createEl('span', { class: 'mono', text: `${d.width} × ${d.height}` }),
            mp >= 0.1 ? createEl('span', { class: 'metadata-note mono', text: `${formatNumberNl(mp, 1)} MP` }) : null);
    }
    const before = grid.querySelector('.metadata-path');
    rows.forEach(el => grid.insertBefore(el, before ? before.previousElementSibling : null));
}

function closeModal() {
    stopViewerMedia();
    viewerMorph('close');
    document.getElementById('modal-viewer').classList.remove('active');
    viewer.items = [];
    viewer.index = -1;
    viewer.onRemoved = null;
    viewer.more = null;
    viewer.moreDone = {};
}

async function openInFinder() {
    const file = viewerFile();
    if (!file || !file.path) return;

    // Meteen zichtbaar dat er iets gebeurt; daarna komen icoon en tekst terug
    const btn = document.getElementById('btn-open-finder');
    const content = [...btn.childNodes];
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
            btn.replaceChildren(...content);
            btn.disabled = false;
        }, 1000);
    }
}

// 'In Bestanden': de map van het bestand openen in de app zelf, met de tegel in beeld en
// even aangewezen. Wat erboven openstond (zoeken, chat, een slideshow) gaat ook dicht.
async function showViewerFileInFiles() {
    showFileInFiles(viewerFile());
}

// file: { path, name }, het pad vanaf het archief
async function showFileInFiles(file) {
    if (!file || !file.path) return;
    const folder = parentFolder(file.path);
    for (let top = topOverlay(), guard = 0; top && guard < 10; top = topOverlay(), guard++) closeOverlay(top);
    const here = currentMainView() === 'viewer' && currentFolder === folder
        && gridItems.some(item => item.path === file.path);
    if (currentMainView() !== 'viewer') switchMainView('viewer');
    if (!here) {
        if (document.getElementById('global-search').value) handleGlobalSearch('');
        await selectFolder(folder);
        if (currentFolder !== folder) return;   // intussen iets anders gekozen
    }
    const index = gridItems.findIndex(item => item.path === file.path);
    if (index < 0) {
        showToast('Niet in deze map', `${file.name} staat niet (meer) in ${displayPath(folder)}.`, 'info');
        return;
    }
    focusGridCard(index);
    const card = document.querySelector(`#media-grid [data-grid-index="${index}"]`);
    if (card) locateGridCard(card);
}

// Een tegel aanwijzen: hij komt naar het midden, veert even op en een groene ring licht op en
// dooft weer. Met minder beweging (Mac of Beweging in Instellingen) alleen de ring, even.
function locateGridCard(card) {
    const calm = document.documentElement.dataset.motion !== 'volledig'
        || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    card.scrollIntoView({ block: 'center', behavior: calm ? 'auto' : 'smooth' });
    card.querySelectorAll(':scope > .locate-ring').forEach(el => el.remove());
    const ring = createEl('span', { class: 'locate-ring', 'aria-hidden': 'true' });
    card.appendChild(ring);
    if (calm || typeof card.animate !== 'function') {
        setTimeout(() => ring.remove(), 1600);
        return;
    }
    card.animate([
        { transform: 'scale(0.94)' },
        { transform: 'scale(1.04)', offset: 0.45 },
        { transform: 'scale(0.99)', offset: 0.75 },
        { transform: 'scale(1)' },
    ], { duration: 620, delay: 180, easing: 'cubic-bezier(0.34, 1.56, 0.64, 1)', fill: 'backwards' });
    ring.animate([
        { opacity: 0, transform: 'scale(1.12)' },
        { opacity: 1, transform: 'scale(1)', offset: 0.2 },
        { opacity: 1, transform: 'scale(1)', offset: 0.7 },
        { opacity: 0, transform: 'scale(1)' },
    ], { duration: 1800, delay: 180, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: 'both' })
        .finished.then(() => ring.remove(), () => ring.remove());
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
let moveBatch = null;       // de selectiebalk (export.js): { files, folder (hun gezamenlijke map of null), onMove(folder) }
let moveInFlight = false;   // één verplaatsing tegelijk: dubbel Enter/klikken stuurt niets extra
let moveNav = null;         // toetsenbordnavigatie van de mappenboom (één per modal)
let moveTwin = null;        // { path, name }: precies dit bestand (dezelfde bytes) staat al in de doelmap

// fileData: één bestand, of een lijst (de selectie) met onMove(folder) -> Promise<bool> die het
// verplaatsen zelf doet; bij true sluit het venster
// Wat het verplaatsvenster als miniatuur toont: voor geluid dezelfde illustratie als in het raster
// (de server geeft daar alleen een grijs vlak)
function moveThumbSrc(file) {
    const name = file.name || file.path.split('/').pop();
    const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')).toLowerCase() : '';
    return ['.mp3', '.wav', '.flac', '.m4a', '.aac'].includes(ext) ? audioThumbUrl(name) : vaultFileUrl('thumb', file.path);
}

function openMoveFileModal(fileData = null, { onMove = null } = {}) {
    if (moveInFlight) return;
    moveBatch = null;
    // Welk bestand: expliciet meegegeven, de dia van de slideshow, of wat de viewer toont
    if (Array.isArray(fileData)) {
        if (!fileData.length || !onMove) return;
        const folders = new Set(fileData.map(f => f.path.substring(0, f.path.lastIndexOf('/'))));
        moveBatch = { files: fileData, folder: folders.size === 1 ? [...folders][0] : null, onMove };
        fileToMove = fileData[0];
    } else if (fileData) {
        fileToMove = fileData;
    } else if (activeShow) {
        fileToMove = activeShow.files[activeShow.currentIndex];
        activeShow.stopLoop(); // Pause show while moving
    } else {
        fileToMove = viewerFile();
    }

    if (!fileToMove) return;

    const modal = document.getElementById('modal-move-file');
    const count = moveBatch ? moveBatch.files.length : 1;
    const countText = `${count.toLocaleString('nl-NL')} bestanden`;
    document.getElementById('move-modal-title').textContent = moveBatch ? `${countText} verplaatsen` : 'Bestand verplaatsen';
    document.getElementById('move-file-label').textContent = moveBatch ? 'Selectie' : 'Bestand';
    document.getElementById('move-file-name').textContent = moveBatch
        ? (count === 1 ? fileToMove.name : countText) : (fileToMove.name || 'Onbekend bestand');
    document.getElementById('btn-confirm-move').disabled = true;
    selectedMoveFolder = null;
    showMoveTwin(null);
    const destDisplay = document.getElementById('move-dest-display');
    if (destDisplay) destDisplay.style.display = 'none';

    // Show source folder path (een selectie uit meer mappen heeft er geen)
    const sourcePath = fileToMove.path || '';
    const sourceFolder = moveBatch ? moveBatch.folder : sourcePath.substring(0, sourcePath.lastIndexOf('/'));
    const sourceEl = document.getElementById('move-file-source');
    if (sourceEl) {
        // Hetzelfde pad als in de viewer: vanaf het archief, in mono
        sourceEl.classList.toggle('is-mixed', !sourceFolder);
        sourceEl.querySelector('span').replaceChildren(...(sourceFolder ? pathNodes(archivePath(sourceFolder)) : ['Verschillende mappen']));
        sourceEl.title = sourceFolder || '';
    }

    // Show thumbnail preview: voor een selectie een waaiertje van de eerste drie
    const previewEl = document.getElementById('move-file-preview');
    if (previewEl) {
        previewEl.classList.toggle('is-stack', !!moveBatch && count > 1);
        if (moveBatch && count > 1) {
            previewEl.replaceChildren(
                ...moveBatch.files.slice(0, 3).reverse().map(f => createEl('img', { src: moveThumbSrc(f), alt: '' })),
                createEl('span', { class: 'move-stack-count tabular', text: count.toLocaleString('nl-NL') }));
        } else {
            previewEl.innerHTML = html`<img src="${moveThumbSrc(fileToMove)}" alt="Voorbeeld">`;
        }
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
        // Niet naar de map waar het bestand al staat (de knop staat dan uit)
        onConfirm: () => { if (selectedMoveFolder && !document.getElementById('btn-confirm-move').disabled) confirmMoveFile(); },
        onClose: closeMoveFileModal,
        typeahead: true,
        enterOpens: true,
        onQuery: showMoveQuery,
    }).nav;
    showMoveQuery('');
    // Beginnen bij de map waar het bestand nu staat: de buurmappen zijn meestal het doel
    const here = sourceFolder && [...document.querySelectorAll('#move-folder-tree .move-folder-node')]
        .find(node => node.dataset.path === sourceFolder);
    if (here) moveNav.highlightItem(here, false);
}

// De getypte zoekterm boven de boom; rood omrand als geen map zo heet
function showMoveQuery(query, found = true) {
    const pill = document.getElementById('move-tree-query');
    if (!pill) return;
    pill.hidden = !query;
    pill.classList.toggle('no-match', !!query && !found);
    pill.querySelector('span').textContent = query;
    pill.title = found ? '' : 'Geen map met deze naam';
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
    moveBatch = null;
    selectedMoveFolder = null;
    showMoveTwin(null);

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
        onSelect: (path, row) => selectMoveDestination(path, row),
    });
    container.scrollTop = 0;
}

// Een klik (of Enter) kiest de map; de boom blijft staan zoals hij is (niet opnieuw tekenen,
// anders klapt alles dicht) en de pijltjes gaan verder vanaf de aangeklikte map
function selectMoveDestination(path, row = null) {
    if (moveInFlight || !fileToMove) return;
    if (row && moveNav) moveNav.highlightItem(row, false);
    previewMoveDestination(path, false);
}

// De gekozen doelmap tonen (links 'Naar', de knop aan) zonder de boom opnieuw te tekenen: ook
// voor de map die met de pijltjes gemarkeerd is, zodat Enter en de knop hetzelfde doen als
// wat je ziet. quiet: geen melding als het de huidige map is (bij pijltjes)
function previewMoveDestination(path, quiet) {
    if (moveInFlight || !fileToMove) return;
    if (path !== selectedMoveFolder) showMoveTwin(null);
    selectedMoveFolder = path;
    const btn = document.getElementById('btn-confirm-move');

    // Prevent moving to the same directory
    const currentDir = moveBatch ? moveBatch.folder : fileToMove.path.substring(0, fileToMove.path.lastIndexOf('/'));

    if (path === currentDir) {
        btn.disabled = true;
        // Dit is de map van het bestand zelf: er is geen tweede kopie, dus ook niets om weg te gooien
        if (!quiet) showToast('Verplaatsen', moveBatch ? 'Dit is de map waar alles nu staat' : 'Dit is de map waar het bestand nu staat', 'warning');
    } else {
        btn.disabled = false;
    }

    // Links 'Naar' tonen, behalve voor de map waar het bestand al staat
    const destDisplay = document.getElementById('move-dest-display');
    const destPath = document.getElementById('move-dest-path');
    if (destDisplay && destPath) {
        destPath.querySelector('span').replaceChildren(...pathNodes(archivePath(path)));
        destPath.title = path;
        destDisplay.style.display = path === currentDir ? 'none' : '';
    }
    document.querySelectorAll('#move-folder-tree .move-folder-node').forEach(node => {
        const on = node.dataset.path === path;
        node.classList.toggle('selected', on);
        const icon = node.querySelector('.folder-icon i');
        if (icon) {
            icon.classList.toggle('fa-folder-open', on);
            icon.classList.toggle('fa-folder', !on);
        }
    });
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

    // De selectie: export.js verplaatst en meldt; dicht als het gelukt is
    if (moveBatch) {
        const done = await moveBatch.onMove(destination);
        btn.replaceChildren(...originalContent);
        btn.disabled = false;
        moveInFlight = false;
        if (done) closeMoveFileModal();
        return;
    }

    // Staan dezelfde bytes al in de doelmap? Dan eerst kiezen: deze kopie naar de prullenmand
    // of toch verplaatsen. Nog een keer bevestigen verplaatst (er wordt nooit iets overschreven).
    if (!moveTwin) {
        let twin = null;
        try {
            twin = (await fetchJSON(`${API_BASE}/api/move-check`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_path: file.path, destination_folder: destination }),
            })).twin;
        } catch (e) {
            console.warn('Controle op een kopie in de doelmap mislukt', e);
        }
        if (twin && fileToMove === file && selectedMoveFolder === destination) {
            btn.replaceChildren(...originalContent);
            btn.disabled = false;
            moveInFlight = false;
            showMoveTwin(twin);
            return;
        }
    }

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

    const from = file.path.slice(0, file.path.lastIndexOf('/'));
    showUndoToast({
        title: 'Verplaatst', message: `${moved.message || 'Bestand verplaatst'}.`,
        key: moved.action_id ? `action:${moved.action_id}` : null,
        undo: { url: '/api/recovery/undo', body: { action_id: moved.action_id } },
        onUndone: (data) => undoneInFolder(data, from, file.name),
    });
    btn.replaceChildren(...originalContent);
    finishMoveOut(file);
}

// Na 'Ongedaan maken' (undo-toast.js) van één bestand uit een map: staat die map open in
// Bestanden, dan komt het er weer in; de melding zegt waar het weer staat
function undoneInFolder(data, folder, name) {
    if (typeof jobsLibraryRefresh === 'function') jobsLibraryRefresh();
    if (typeof currentFolder !== 'undefined' && currentFolder === folder && typeof loadMedia === 'function') loadMedia(currentFolder);
    if (data && data.restored === 0) {
        const reason = (data.skipped && data.skipped[0] && data.skipped[0].reason) || 'Het bestand kon niet terug.';
        return { title: 'Niet teruggezet', message: reason, type: 'warning' };
    }
    return { title: 'Teruggezet', message: `${name} staat weer in ${folder.split('/').pop() || 'het archief'}.`, type: 'success' };
}

// Het bestand is weg uit deze map (verplaatst of naar de prullenmand): uit de lijst, de show
// en de viewer halen en het venster sluiten
function finishMoveOut(file) {
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

// 'Verwijderen' in de viewer: naar de prullenmand (er wordt nooit iets gewist); de melding
// heeft 'Ongedaan maken'. De viewer blijft open en toont het volgende bestand.
let viewerTrashBusy = false;
async function trashViewerFile() {
    const file = viewerFile();
    if (!file || viewerTrashBusy || !window.DVExport) return;
    viewerTrashBusy = true;
    const btn = document.getElementById('btn-trash-file');
    btn.disabled = true;
    btn.classList.add('is-loading');
    const moved = await DVExport.trashFiles([file]);
    btn.classList.remove('is-loading');
    btn.disabled = false;
    viewerTrashBusy = false;
    if (!moved.length) return;
    const onRemoved = viewer.onRemoved;
    viewerDropFile(file);
    if (onRemoved) onRemoved(file);
}

// Na 'Ongedaan maken' van een verwijdering (export.js): de open map in Bestanden opnieuw
// inlezen, zodat het bestand er weer in staat
document.addEventListener('DOMContentLoaded', () => {
    if (window.DVExport) DVExport.onRestored(() => { if (currentFolder) loadMedia(currentFolder); });
});

// Een bestand uit de lijst van de viewer halen. Was het het getoonde, dan schuift het volgende
// op zijn plek (aan het eind het vorige); was het het laatste, dan gaat de viewer dicht.
function viewerDropFile(file) {
    const at = viewer.items.indexOf(file);
    if (at < 0 || !isViewerOpen()) return;
    viewer.items.splice(at, 1);
    if (!viewer.items.length) { closeModal(); return; }
    if (at < viewer.index) { viewer.index--; return; }
    if (at > viewer.index) return;
    if (viewer.index >= viewer.items.length) {
        viewer.index = viewer.items.length - 1;
        viewer.step = -1;
    } else {
        viewer.step = 1;
    }
    renderViewerFile();
}

// De melding als dezelfde bytes al in de doelmap staan (null: weg). De hoofdknop heet dan
// 'Toch verplaatsen'; naar de prullenmand gaat alleen met de eigen knop, nooit met ⌘Enter
function showMoveTwin(twin) {
    moveTwin = twin;
    const label = document.querySelector('#btn-confirm-move .move-confirm-label');
    if (label) label.textContent = twin ? 'Toch verplaatsen' : 'Hierheen verplaatsen';
    const box = document.getElementById('move-twin');
    if (!box) return;
    box.hidden = !twin;
    if (!twin) return;
    document.getElementById('move-twin-thumb').replaceChildren(
        createEl('img', { src: vaultFileUrl('thumb', twin.path), alt: '' }));
    document.getElementById('move-twin-detail').textContent =
        `${twin.name} heeft precies dezelfde inhoud. Deze kopie kan dus weg; terugzetten kan in het Herstelcentrum.`;
}

// Deze kopie naar de prullenmand; de server vergelijkt eerst opnieuw byte voor byte
async function trashMoveTwin() {
    if (moveInFlight || !fileToMove || !moveTwin || moveBatch) return;
    moveInFlight = true;
    const file = fileToMove;
    const twin = moveTwin;
    const btn = document.getElementById('btn-move-twin-trash');
    const confirmBtn = document.getElementById('btn-confirm-move');
    const originalContent = [...btn.childNodes];
    btn.innerHTML = '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Naar de prullenmand…';
    btn.disabled = true;
    confirmBtn.disabled = true;

    let trashed;
    try {
        trashed = await fetchJSON(`${API_BASE}/api/move-twin-trash`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_path: file.path, twin_path: twin.path }),
        });
    } catch (e) {
        // Niets verplaatst (bijv. de andere kopie is veranderd): de melding gaat weg
        toastFetchError('Niet naar de prullenmand', e);
        btn.replaceChildren(...originalContent);
        btn.disabled = false;
        confirmBtn.disabled = false;
        moveInFlight = false;
        showMoveTwin(null);
        return;
    }

    const folder = twin.path.split('/').slice(-2, -1)[0] || 'het archief';
    const from = file.path.slice(0, file.path.lastIndexOf('/'));
    showUndoToast({
        title: 'Naar de prullenmand', message: `${file.name} is een kopie; ${twin.name} blijft staan in ${folder}.`,
        key: trashed.action_id ? `action:${trashed.action_id}` : null,
        undo: { url: '/api/recovery/undo', body: { action_id: trashed.action_id } },
        onUndone: (data) => undoneInFolder(data, from, file.name),
    });
    btn.replaceChildren(...originalContent);
    btn.disabled = false;
    finishMoveOut(file);
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

function prevFile(carry = 0) {
    if (viewer.index > 0) {
        viewer.index--;
        viewer.step = -1;
        viewer.carry = carry;
        renderViewerFile();
    } else if (viewer.index === 0) {
        viewerLoadMore(-1);
    }
}

function nextFile(carry = 0) {
    if (viewer.index >= 0 && viewer.index < viewer.items.length - 1) {
        viewer.index++;
        viewer.step = 1;
        viewer.carry = carry;
        renderViewerFile();
    } else if (viewer.index >= 0) {
        viewerLoadMore(1);
    }
}

// Aan de rand van een lijst die per pagina laadt: de volgende (of vorige) pagina erbij en verder
let viewerMoreBusy = false;
async function viewerLoadMore(step) {
    const more = viewer.more;
    if (!more || viewer.moreDone[step] || viewerMoreBusy) return;
    viewerMoreBusy = true;
    try {
        const files = ((await more(step)) || []).filter(f => f && f.type !== 'folder');
        if (viewer.more !== more) return;      // de viewer is intussen dicht of opnieuw geopend
        if (!files.length) {
            viewer.moreDone[step] = true;
        } else if (step > 0) {
            viewer.items = viewer.items.concat(files);
            viewer.index++;
        } else {
            viewer.items = files.concat(viewer.items);
            viewer.index += files.length - 1;
        }
        viewer.step = files.length ? step : 0;
        renderViewerFile();
    } catch (e) {
        toastFetchError('Volgende bestanden laden mislukt', e);
    } finally {
        viewerMoreBusy = false;
    }
}

// Vegen met de muis of twee vingers op het trackpad (alleen zonder zoom, niet bij video)
document.addEventListener('DOMContentLoaded', () => {
    MediaMotion.viewerSwipe(document.getElementById('modal-body'), {
        canSwipe: () => isViewerOpen() && !isMoveModalOpen() && modalZoom <= 1,
        hasNext: () => viewer.index < viewer.items.length - 1 || !!(viewer.more && !viewer.moreDone[1]),
        hasPrev: () => viewer.index > 0 || !!(viewer.more && !viewer.moreDone[-1]),
        go: (step, carry) => (step > 0 ? nextFile(carry) : prevFile(carry)),
    });
});

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
        else if (e.key === 'b' || e.key === 'B') { e.preventDefault(); showViewerFileInFiles(); }
        else if ((e.key === 'Backspace' || e.key === 'Delete') && tag !== 'BUTTON') { e.preventDefault(); trashViewerFile(); }
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

// action (optioneel): {label, onClick, icon, hint, keepOpen}, een knop in de melding (bijv.
// 'Ongedaan maken'; zie undo-toast.js). Zonder keepOpen sluit de melding na de klik; met keepOpen
// krijgt onClick(button, toast) en beslist zelf.
// options (optioneel): {duration} in ms, {unique: true} stapelt nooit samen met een gelijke
// melding, {stay: true} blijft staan bij een wissel van onderdeel. Geeft de melding terug.
function showToast(title, message, type = 'info', action = null, options = {}) {
    const container = document.getElementById('toast-container');
    const duration = options.duration || TOAST_MS[type] || TOAST_MS.info;
    // Dezelfde melding nog in beeld (bijv. tien mislukte miniaturen): niet opstapelen, alleen langer tonen
    const same = options.unique || action ? null : [...container.children].find(t => !t._leaving && !t._unique
        && t.dataset.title === String(title) && t.dataset.message === String(message));
    if (same) {
        same.style.opacity = '1';
        toastSchedule(same);
        restartToastTimer(same);
        // Dezelfde melding nog eens: een kort knikje in plaats van een tweede melding
        Motion.animate(same, [{ transform: 'none' }, { transform: `translate3d(${-Motion.px('--motion-distance-sm', 6)}px, 0, 0)` }, { transform: 'none' }],
            { spring: 'snappy', key: 'nudge', calm: false });
        return same;
    }
    const toast = document.createElement('div');
    toast.className = `toast ${type} toast-${type}`;
    toast.dataset.title = String(title);
    toast.dataset.message = String(message);
    toast._duration = duration;
    toast._unique = !!(options.unique || action);
    if ((type === 'info' || type === 'success') && !options.stay) toast.dataset.view = activeMainView;
    // Een fout wordt meteen voorgelezen; de rest zegt de container (aria-live polite)
    if (type === 'error') toast.setAttribute('role', 'alert');
    // Titel en melding zijn tekst: ze bevatten vaak bestandsnamen of een foutmelding van de server
    toast.append(
        createEl('div', { class: 'toast-header' },
            // Het soort melding zit in het icoon en de balk links; de titel blijft rustig
            createEl('i', { class: `fas ${TOAST_ICONS[type] || TOAST_ICONS.info} toast-icon`, 'aria-hidden': 'true' }),
            createEl('strong', { text: title }),
            createEl('button', { onClick: () => hideToast(toast), 'aria-label': 'Melding sluiten', title: 'Sluiten' }, '\u00d7')),
        createEl('div', { class: 'toast-body', text: message }),
        createEl('div', { class: 'toast-timer', 'aria-hidden': 'true' }));
    toast.style.setProperty('--toast-ms', `${duration}ms`);
    if (action) {
        const button = createEl('button', {
            type: 'button', class: 'btn btn-secondary btn-sm toast-action',
            title: action.hint ? `${action.label} (${action.hint})` : null,
            onClick: () => {
                if (action.keepOpen) { action.onClick(button, toast); return; }
                hideToast(toast);
                action.onClick();
            },
        }, action.icon ? createEl('i', { class: `fas ${action.icon}`, 'aria-hidden': 'true' }) : null,
        createEl('span', { class: 'toast-action-label', text: action.label }));
        toast.append(createEl('div', { class: 'toast-actions' },
            action.hint ? createEl('kbd', { class: 'toast-hint', 'aria-hidden': 'true', text: action.hint }) : null,
            button));
    }

    container.appendChild(toast);

    // Met de muis erop of de focus erin blijft hij staan, zodat een lange melding uit te lezen is
    // (en 'Ongedaan maken' met Tab te bereiken); daarna begint de tijd opnieuw
    toastSchedule(toast);
    const hold = () => { if (!toast._leaving) { clearTimeout(toast._hideTimer); toast.style.opacity = '1'; } };
    const release = () => {
        if (toast._leaving || toast.matches(':hover') || toast.contains(document.activeElement)) return;
        toastSchedule(toast);
        restartToastTimer(toast);
    };
    toast.addEventListener('mouseenter', hold);
    toast.addEventListener('focusin', hold);
    toast.addEventListener('mouseleave', release);
    toast.addEventListener('focusout', () => setTimeout(release, 0));
    return toast;
}

// De melding gaat na zijn eigen tijd weg (opnieuw vanaf nu)
function toastSchedule(toast, duration = toast._duration || TOAST_MS.info) {
    clearTimeout(toast._hideTimer);
    toast._duration = duration;
    toast.style.setProperty('--toast-ms', `${duration}ms`);
    toast._hideTimer = setTimeout(() => hideToast(toast), duration);
}

// De leeglopende lijn opnieuw vol: de melding blijft weer de hele tijd staan
function restartToastTimer(toast) {
    const timer = toast.querySelector('.toast-timer');
    if (!timer) return;
    timer.style.animation = 'none';
    void timer.offsetWidth;   // de animatie echt opnieuw laten beginnen
    timer.style.animation = '';
}

// Weg naar rechts, versnellend; de meldingen eronder schuiven daarna op hun veer omhoog
function hideToast(toast) {
    if (!toast || toast._leaving) return;
    toast._leaving = true;
    clearTimeout(toast._hideTimer);
    const container = toast.parentElement;
    const leave = Motion.exit(toast, {
        to: { opacity: 0, transform: `translate3d(${Motion.px('--motion-distance-lg', 24) * 1.5}px, 0, 0) scale(0.96)` },
    });
    leave.finished.then(() => {
        const others = container ? [...container.children].filter(t => t !== toast) : [];
        Motion.flip(others, () => toast.remove());
    }, () => toast.remove());
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
    else if (view === 'map' && typeof mapVaultReturned === 'function') mapVaultReturned();
    else if (view === 'keurer' && typeof keurerVaultReturned === 'function') keurerVaultReturned();
    else if (view === 'duplicates' && typeof duplicatesInit === 'function') duplicatesInit();
    else if (view === 'recovery' && typeof recoveryLoad === 'function') recoveryLoad();
    else if (view === 'settings' && typeof settingsRender === 'function') settingsRender();
    else if (view === 'albums' && typeof albumsInit === 'function') albumsInit();
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
    // Alleen als het anders is: zo blijft de letter-voor-letter-begroeting (motion-shell.js) staan
    if (title && title.textContent !== greeting) title.textContent = greeting;
    if (date) date.textContent = now.toLocaleDateString('nl-NL', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
}

// Cijfertegels op Home, uit de bibliotheekstatus die jobs.js ophaalt (null: niet bereikbaar)
function homeRenderLibraryStats(lib) {
    const ready = Boolean(lib && lib.ready);
    const show = (id, value) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.toggle('is-empty', !ready);
        // De eerste keer dat Home in beeld is, telt het getal op (motion-shell.js)
        if (ready && window.ShellMotion) ShellMotion.statValue(el, value);
        else el.textContent = ready ? Number(value || 0).toLocaleString('nl-NL') : '–';
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
            // Zonder hoes een eigen 'hoes': een verloop dat per artiest altijd hetzelfde is
            const hue = [...String(t.artist || '')].reduce((n, c) => (n * 31 + c.charCodeAt(0)) % 5, 0) + 1;
            const cover = createEl('div', { class: `otd-cover otd-cover-${hue}`, 'aria-hidden': 'true' },
                createEl('i', { class: 'fas fa-music' }), createEl('i', { class: 'fab fa-spotify otd-cover-brand' }));
            const item = createEl('div', { class: 'otd-item', onClick: () => openSpotifySearch(`${t.track} ${t.artist}`) },
                cover,
                createEl('div', { class: 'otd-title', text: t.track }),
                createEl('div', { class: 'otd-subtitle', text: t.artist }),
                createEl('div', { class: 'otd-when' }, createEl('i', { class: 'fas fa-clock', 'aria-hidden': 'true' }),
                    createEl('span', { class: 'mono', text: `${t.time || ''}` }), ` · ${t.year}`));
            makeActivatable(item, `${t.track} van ${t.artist} zoeken in Spotify`);
            homeOtdShow('spotify-otd-loading', 'spotify-otd-content', item);
            spotifyOtdCover(item, cover, t.cover ? `${API_BASE}${t.cover}` : null);
        } else {
            spotifyOtdCover(null, null, null);
            homeOtdShow('spotify-otd-loading', 'spotify-otd-content', homeOtdState(emptyState({
                icon: 'fa-music',
                title: 'Niets geluisterd',
                text: 'Op deze dag in eerdere jaren speelde er niets.',
                action: { label: 'Naar Spotify', onClick: () => switchMainView('spotify') },
            })));
        }
    } catch (e) {
        spotifyOtdCover(null, null, null);
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
    if (window.ShellMotion) ShellMotion.storageValue(used, Number(data.used_gb), gb);
    else used.textContent = gb(data.used_gb);
    meta.replaceChildren('van ', createEl('span', { text: gb(data.total_gb) }), ' · ',
        createEl('span', { text: gb(data.free_gb) }), ' vrij');
    bar.style.width = `${data.percent_used}%`;
    bar.classList.toggle('critical', data.percent_used > 90);
    bar.classList.toggle('warning', data.percent_used > 75 && data.percent_used <= 90);
    if (window.ShellMotion) ShellMotion.storageFill(bar);
}

// Spotify op deze dag: de albumhoes die de Spotify-sync op deze Mac bewaarde (Home zelf gaat
// nooit online). Achter de kaart drijft een zelfgemaakte, vervaagde gloed uit dezelfde hoes, en
// de hoes kantelt mee met de muis. Alleen transform en opacity; met minder beweging (Mac of
// Beweging in Instellingen) staat de gloed stil en kantelt er niets.
function spotifyOtdCover(item, cover, src) {
    const widget = document.getElementById('widget-spotify-otd');
    widget.querySelectorAll(':scope > .otd-ambient').forEach(el => el.remove());
    widget.classList.remove('has-ambient');
    if (!item || !src) return;
    const img = new Image();
    img.decoding = 'async';
    img.alt = '';
    img.onload = () => {
        if (!item.isConnected) return;
        img.className = 'otd-cover-img';
        cover.replaceChildren(img, createEl('i', { class: 'fab fa-spotify otd-cover-brand' }));
        cover.classList.add('has-art');
        item.classList.add('has-cover');
        const ambient = createEl('div', { class: 'otd-ambient', 'aria-hidden': 'true' },
            createEl('img', { class: 'otd-ambient-a', src, alt: '' }),
            createEl('img', { class: 'otd-ambient-b', src, alt: '' }),
            createEl('div', { class: 'otd-ambient-veil' }));
        widget.prepend(ambient);
        // Eén frame later: dan vloeit de gloed in in plaats van te verschijnen
        requestAnimationFrame(() => requestAnimationFrame(() => widget.classList.add('has-ambient')));
    };
    img.src = src;   // mislukt het laden, dan blijft het eigen verloop staan

    // Beweging (docs/MOTION.md): alleen in Volledig kantelt de hoes; motion.js volgt ook macOS
    const calm = () => (window.Motion ? !Motion.full()
        : window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    let frame = 0;
    item.addEventListener('pointermove', e => {
        if (calm() || !cover.classList.contains('has-art')) return;
        const r = cover.getBoundingClientRect();
        const x = Math.max(-1, Math.min(1, ((e.clientX - r.left) / r.width) * 2 - 1));
        const y = Math.max(-1, Math.min(1, ((e.clientY - r.top) / r.height) * 2 - 1));
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
            cover.classList.add('is-tilting');
            cover.style.setProperty('--tilt-x', `${(-y * 7).toFixed(2)}deg`);
            cover.style.setProperty('--tilt-y', `${(x * 7).toFixed(2)}deg`);
            cover.style.setProperty('--glare-x', `${((x + 1) * 50).toFixed(1)}%`);
            cover.style.setProperty('--glare-y', `${((y + 1) * 50).toFixed(1)}%`);
        });
    });
    item.addEventListener('pointerleave', () => {
        cancelAnimationFrame(frame);
        cover.classList.remove('is-tilting');
        cover.style.removeProperty('--tilt-x');
        cover.style.removeProperty('--tilt-y');
    });
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
    if (window.ShellMotion) ShellMotion.otdShow(content);
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
                // (scherp voor de eerste paar; de rest van de rotatie blijft de miniatuur)
                if (index < 4) loadFullImage(p, src => { if (image.isConnected) image.style.backgroundImage = cssUrl(src); });
                // Ook met het toetsenbord te openen; alleen de foto die je ziet is een tabstop
                makeActivatable(image, `${p.name} openen`);
                image.tabIndex = index === 0 ? 0 : -1;
                const overlay = createEl('div', { class: 'otd-large-overlay', style: `display: ${index === 0 ? 'flex' : 'none'};` },
                    createEl('div', { class: 'otd-large-title', text: p.name }),
                    createEl('div', { class: 'otd-large-subtitle', text: p.year }));
                content.append(image, overlay);
            });
            if (window.ShellMotion) ShellMotion.otdShow(content);

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
                    // Staat de focus op de foto, dan blijft die staan (anders wijst de focus
                    // naar een foto die je niet meer ziet)
                    if (content.contains(document.activeElement)) return;
                    images[currentOtd].classList.remove('active');
                    images[currentOtd].tabIndex = -1;
                    overlays[currentOtd].style.display = 'none';

                    currentOtd = (currentOtd + 1) % images.length;

                    images[currentOtd].classList.add('active');
                    images[currentOtd].tabIndex = 0;
                    overlays[currentOtd].style.display = 'flex';
                    if (window.ShellMotion) ShellMotion.otdText(overlays[currentOtd]);
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
    if (!await dvConfirm({
        title: 'DataVault-server herstarten?', icon: 'fa-rotate-right', confirmLabel: 'Herstarten',
        body: 'De app is dan een paar seconden niet bereikbaar.',
    })) return;
    if (brand && brand.dataset.restarting) return;

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
    // De eerste indruk: het menu valt op zijn plek, de begroeting rijst op, dan de kaarten
    // (motion-shell.js; docs/MOTION.md)
    if (window.ShellMotion) ShellMotion.homeIntro();
    else Motion.enterPage(document.getElementById('section-home'));
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
        'Balanced Listener': { icon: 'fa-headphones', title: 'Evenwichtige luisteraar', key: 'Balanced Listener', desc: 'Er springt (nog) geen dagdeel uit: in deze periode zijn te weinig nummers afgespeeld om een type te kiezen.' }
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
                newItem.appendChild(createEl('span', { class: 'actual-dna-badge', text: 'Jij' }));
            }
        }

        // Initial active state matches the actual DNA
        newItem.classList.toggle('active', info.key === typeName);
        newItem.setAttribute('aria-pressed', String(info.key === typeName));

        // Click listener to view other explanations
        newItem.addEventListener('click', () => {
            // Update active state
            document.querySelectorAll('.dna-type-item').forEach(el => {
                el.classList.remove('active');
                el.setAttribute('aria-pressed', 'false');
            });
            newItem.classList.add('active');
            newItem.setAttribute('aria-pressed', 'true');

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
        // Bestanden die los in de map zelf staan tellen mee; een map met alleen foto's was 0 B
        const filesSize = data.files_size || 0;
        const filesCount = data.files_count || 0;
        const total = entries.reduce((sum, [, size]) => sum + size, 0) + filesSize;

        // Elke navigatie begint bij de submappen; zonder submappen meteen bij de bestanden
        storageShowTab(folderPath && !entries.length && filesCount ? 'largest-files' : 'subfolders');

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
        largestDisplay.textContent = entries.length > 0 ? `${entries[0][0]} (${formatBytes(entries[0][1])})` : '';
        largestDisplay.parentElement.style.display = entries.length ? '' : 'none';

        if (entries.length === 0) {
            list.replaceChildren(emptyState({ icon: 'fa-folder-open', title: 'Geen submappen',
                text: !folderPath ? 'Het archief heeft nog geen mappen.'
                    : filesCount ? 'Alles staat los in deze map; zie Grootste bestanden.' : 'Deze map is leeg.' }));
        }

        // Bovenaan is het aandeel in het hele archief; in een map het aandeel in die map
        const shareOf = folderPath ? 'van deze map' : 'van het totaal';
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
                <div class="storage-folder-percent"><span class="storage-folder-percent-value">${percent}%</span> ${shareOf}</div>
            `;
            list.appendChild(li);
        });

        // Naast submappen ook losse bestanden: een eigen rij die naar Grootste bestanden gaat
        if (folderPath && entries.length && filesSize > 0) {
            const percent = (filesSize / total * 100).toLocaleString('nl-NL', { maximumFractionDigits: 1 });
            const label = filesCount === 1 ? '1 los bestand' : `${filesCount.toLocaleString('nl-NL')} losse bestanden`;
            const li = createEl('li', { class: 'storage-folder-item', tabindex: '0', role: 'button',
                'aria-label': `${label}, ${formatBytes(filesSize)}: toon de grootste`,
                onClick: () => storageShowTab('largest-files'),
                onKeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); storageShowTab('largest-files'); } } });
            li.innerHTML = html`
                <div class="storage-folder-header">
                    <span class="storage-folder-name"><i class="fas fa-file"></i>${label}</span>
                    <span class="storage-folder-size">${formatBytes(filesSize)}</span>
                </div>
                <div class="storage-progress-bar">
                    <div class="storage-progress-fill" style="width: ${filesSize / total * 100}%"></div>
                </div>
                <div class="storage-folder-percent"><span class="storage-folder-percent-value">${percent}%</span> ${shareOf}</div>
            `;
            list.appendChild(li);
        }

        if (folderPath) {
            const filesData = await fetchJSON(`${API_BASE}/api/stats/largest-files?folder_path=${encodeURIComponent(folderPath)}&limit=20`)
                .catch(err => { toastFetchError('Grootste bestanden laden mislukt', err); return {}; });

            if (filesData.files) filesList.replaceChildren(...filesData.files.map(storageFileRow));
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

const STORAGE_FILE_ICONS = [
    ['fa-image', ['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'heif', 'tif', 'tiff', 'bmp']],
    ['fa-video', ['mp4', 'mov', 'm4v', 'avi', 'mkv', 'webm', '3gp']],
    ['fa-music', ['mp3', 'wav', 'flac', 'aac', 'm4a']],
    ['fa-file-lines', ['pdf', 'doc', 'docx', 'txt']],
    ['fa-file-zipper', ['zip', 'rar', '7z']],
];

// Een rij in 'Grootste bestanden': een mini-voorbeeld (opent de viewer), naam, grootte en pad,
// en rechts 'In Bestanden' en de prullenmand
function storageFileRow(file) {
    const ext = file.name.split('.').pop().toLowerCase();
    const fileIcon = (STORAGE_FILE_ICONS.find(([, exts]) => exts.includes(ext)) || ['fa-file'])[0];
    const visual = fileIcon === 'fa-image' || fileIcon === 'fa-video';
    const playable = visual || fileIcon === 'fa-music';
    const size = formatBytes(typeof file.size === 'number' ? file.size : file.size_mb * 2 ** 20);
    const faIcon = (name) => createEl('i', { class: `fas ${name}`, 'aria-hidden': 'true' });

    const preview = createEl(playable ? 'button' : 'span', playable
        ? { type: 'button', class: 'storage-file-thumb', title: 'Bekijken', 'aria-label': `${file.name} bekijken`,
            onClick: () => openFileInViewer(file.path) }
        : { class: 'storage-file-thumb', 'aria-hidden': 'true' },
        visual ? createEl('img', { src: vaultFileUrl('thumb', file.path), alt: '', loading: 'lazy', decoding: 'async' }) : faIcon(fileIcon),
        fileIcon === 'fa-video' ? createEl('span', { class: 'storage-file-play', 'aria-hidden': 'true' }, faIcon('fa-play')) : null);

    const li = createEl('li', { class: 'storage-file-item' });
    li.append(
        preview,
        createEl('div', { class: 'storage-file-main' },
            createEl('div', { class: 'storage-file-header' },
                createEl('span', { class: 'storage-file-name', text: file.name, title: file.name }),
                createEl('span', { class: 'storage-file-size', text: size })),
            createEl('div', { class: 'storage-file-path', text: file.path, title: file.path })),
        createEl('div', { class: 'storage-file-actions' },
            createEl('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-sm',
                title: 'Toon in Bestanden', 'aria-label': `${file.name} tonen in Bestanden`,
                onClick: () => showFileInFiles({ ...file, path: fullVaultPath(file.path) }) }, faIcon('fa-folder-open')),
            createEl('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-sm storage-file-trash',
                title: 'Naar de prullenmand (terug te zetten in het Herstelcentrum)', 'aria-label': `${file.name} naar de prullenmand`,
                onClick: (e) => storageTrashFile(file, li, e.currentTarget) }, faIcon('fa-trash-can'))));
    return li;
}

// Naar de prullenmand (er wordt nooit iets gewist); de melding biedt 'Ongedaan maken'. De rij
// schuift weg en de rijen eronder schuiven soepel omhoog.
async function storageTrashFile(file, li, button) {
    button.disabled = true;
    button.classList.add('is-loading');
    const moved = await DVExport.trashFiles([file]);
    button.classList.remove('is-loading');
    if (!moved.length) { button.disabled = false; return; }
    const list = li.parentElement;
    const calm = document.documentElement.dataset.motion !== 'volledig'
        || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    const finish = () => {
        if (!li.isConnected) return;
        const below = [...list.children].filter(el => el !== li && li.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
        const before = below.map(el => el.getBoundingClientRect().top);
        li.remove();
        if (!list.children.length) {
            list.replaceChildren(emptyState({ icon: 'fa-folder-open', title: 'Geen bestanden meer', text: 'Alles uit deze map staat in de prullenmand.' }));
            return;
        }
        if (calm) return;
        below.forEach((el, i) => {
            const dy = before[i] - el.getBoundingClientRect().top;
            if (dy) el.animate([{ transform: `translateY(${dy}px)` }, { transform: 'none' }],
                { duration: 420, delay: i * 18, easing: 'cubic-bezier(0.22, 1.2, 0.36, 1)', fill: 'backwards' });
        });
    };
    if (calm || typeof li.animate !== 'function') { finish(); return; }
    li.style.pointerEvents = 'none';
    li.animate([
        { opacity: 1, transform: 'none' },
        { opacity: 0, transform: 'translateX(24px) scale(0.98)' },
    ], { duration: 220, easing: 'cubic-bezier(0.4, 0, 1, 1)', fill: 'forwards' }).finished.then(finish, finish);
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

// De lange uitleg bij een inzicht: wat het is, hoe DataVault het uitrekent en wat het voor jou is
// (uit de laatst getoonde cijfers; spotifyLastStats zet renderSpotifyStats)
let spotifyLastStats = null;

function spotifyInsightResult(key) {
    const insights = spotifyLastStats && spotifyLastStats.new_insights;
    if (!insights) return null;
    if (key === 'record_day') {
        const day = insights.record_day;
        return day ? `Op ${formatDateNl(day.date)} luisterde je ${formatNumberNl(day.hours, 1)} uur.` : null;
    }
    if (key === 'top_binge') {
        const binge = insights.top_binge;
        return binge ? `${binge.track} van ${binge.artist}: ${binge.count} keer achter elkaar.`
            : 'In deze periode draaide je geen nummer twee keer direct na elkaar.';
    }
    if (key === 'mainstream_score' && insights.mainstream_score) {
        return `${spotifyLabel(insights.mainstream_score)}: de top 5% van je artiesten is goed voor ${insights.mainstream_percent}% van je luistertijd.`;
    }
    if (key === 'seasonal_mood' && insights.seasonal_mood) {
        const mood = String(insights.seasonal_mood);
        if (mood.startsWith('Winter')) return 'Jij bent een winterluisteraar: in de winter luister je duidelijk meer.';
        if (mood.startsWith('Summer')) return 'Jij bent een zomerluisteraar: in de zomer luister je duidelijk meer.';
        return 'Jij luistert het hele jaar door ongeveer even veel.';
    }
    return null;
}

function showInsightExplanation(insightKey) {
    const modal = document.getElementById('modal-insight-explanation');

    const insightDetails = {
        'record_day': {
            icon: 'fa-trophy',
            title: 'Recorddag',
            desc: 'De dag waarop je het langst naar muziek luisterde, in de periode die bovenaan gekozen is.',
            how: 'DataVault telt per kalenderdag de speeltijd op van alles wat je afspeelde, ook van nummers die je oversloeg, en kiest de dag met de meeste uren.',
        },
        'top_binge': {
            icon: 'fa-repeat',
            title: 'Langste reeks',
            desc: 'Het nummer dat je het vaakst direct na elkaar draaide, zonder een ander nummer ertussen.',
            how: 'Een reeks begint opnieuw zodra er een ander nummer speelt. Eén keer afspelen is nog geen reeks; het nummer moet minstens twee keer achter elkaar komen.',
        },
        'mainstream_score': {
            icon: 'fa-chart-line',
            title: 'Vaste favorieten of brede ontdekker',
            desc: 'Hoe je luistertijd over je artiesten verdeeld is: vooral steeds dezelfde, of juist veel verschillende.',
            how: 'DataVault neemt de 5% artiesten naar wie je het langst luisterde en kijkt welk deel van je luistertijd naar hen ging. '
                + 'Meer dan 75%: vaste favorieten. Minder dan 40%: brede ontdekker. Daartussen: evenwichtige luisteraar.',
        },
        'seasonal_mood': {
            icon: 'fa-cloud-sun',
            title: 'Seizoensstemming',
            desc: 'Of je in de winter of juist in de zomer duidelijk meer muziek luistert.',
            how: 'DataVault vergelijkt het aantal afgespeelde nummers in december, januari en februari met dat in juni, juli en augustus. '
                + 'Is het ene seizoen minstens anderhalf keer zo groot, dan ben je een winter- of zomerluisteraar; anders luister je het hele jaar door.',
        }
    };

    const info = insightDetails[insightKey];
    if (!info) return;

    const iconEl = document.getElementById('insight-modal-icon');
    const titleEl = document.getElementById('insight-modal-title');
    const descEl = document.getElementById('insight-modal-description');
    const howEl = document.getElementById('insight-modal-how');
    const resultEl = document.getElementById('insight-modal-result');

    if (iconEl) iconEl.replaceChildren(createEl('i', { class: `fas ${info.icon}` }));
    if (titleEl) titleEl.textContent = info.title;
    if (descEl) descEl.textContent = info.desc;
    if (howEl) howEl.textContent = info.how;
    if (resultEl) {
        const result = spotifyInsightResult(insightKey);
        resultEl.hidden = !result;
        resultEl.textContent = result || '';
    }

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
            <div class="modal-footer">
                <span class="modal-footer-note">Een wijziging wordt meteen bewaard.</span>
                <button type="button" class="btn btn-secondary" onclick="closeSettingsModal()">Sluiten</button>
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
    // Geen enkele map met submappen: dan ook geen lege ruimte voor de uitklappijltjes, zodat
    // de rijen op de lijn van de tekst erboven staan
    container.classList.toggle('is-flat', !tree.some(item => item.children && item.children.length));
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
    // opts.from: de omslag waarop geklikt werd (<img>), opts.fromPath: welk bestand die toont.
    // De show begint dan met die foto, en de omslag groeit uit tot de eerste dia (één object).
    constructor(story, opts = {}) {
        this.story = story;
        this.files = (story.files || []).filter(f => f && (f.type === 'photo' || f.type === 'video'));
        const first = opts.fromPath ? this.files.findIndex(f => f.path === opts.fromPath) : -1;
        if (first > 0) this.files = [...this.files.slice(first), ...this.files.slice(0, first)];
        this.from = first >= 0 && this.files[0].type === 'photo' ? opts.from : null;
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
        // Zonder 'active' erin, en die in het volgende beeld erbij: het zwart vaagt op (--dur-4)
        // in plaats van in één beeld te verschijnen
        overlay.className = 'cinematic-overlay';
        requestAnimationFrame(() => { if (overlay.isConnected) overlay.classList.add('active'); });
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
        // Weg in --dur-3 (sneller dan het openen); niet meer klikbaar en niet meer in de Tab-volgorde
        this.playerEl.classList.remove('active');
        this.playerEl.inert = true;
        setTimeout(() => this.playerEl.remove(), Motion.ms('--dur-3', 200) + 40);
        activeShow = null;
    }

    // De eerste dia: de omslag groeit uit tot de foto (gedeeld element). De dia staat meteen
    // helemaal zichtbaar en de Ken Burns begint pas als de omslag geland is.
    morphIn(slide, img) {
        const cover = this.from;
        this.from = null;
        if (!cover || !cover.isConnected || !cover.naturalWidth || !Motion.full()) return false;
        const box = document.getElementById('slide-container').getBoundingClientRect();
        const aspect = cover.naturalWidth / cover.naturalHeight;
        let w = box.width, h = w / aspect;
        if (h > box.height) { h = box.height; w = h * aspect; }
        const rect = { left: box.left + (box.width - w) / 2, top: box.top + (box.height - h) / 2, width: w, height: h };
        slide.classList.add('active', 'is-first');
        img.classList.add('kb-hold');
        const anim = Motion.sharedElement(cover, img, { direction: 'open', rect, className: 'mm-show-ghost' });
        const go = () => { img.classList.remove('kb-hold'); slide.classList.remove('is-first'); };
        anim.finished.then(go, go);
        return true;
    }

    startLoop() {
        if (!this.isPlaying) return;
        this.stopLoop();

        // De groene lijn loopt vol met een transform (geen breedte: die zou elk beeld de layout
        // raken); zonder beweging staat hij gewoon vol
        const bar = document.getElementById('player-progress');
        bar.classList.add('mm-progress');
        bar.style.opacity = '1';
        if (this.progressAnim) this.progressAnim.cancel();
        this.progressAnim = Motion.off() ? null
            : bar.animate([{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration: this.duration, easing: 'linear', fill: 'forwards' });
        bar.style.transform = Motion.off() ? 'none' : '';

        this.timer = setTimeout(() => this.next(), this.duration);
    }

    stopLoop() {
        clearTimeout(this.timer);
        if (this.progressAnim) { this.progressAnim.cancel(); this.progressAnim = null; }
        const bar = document.getElementById('player-progress');
        if (bar) bar.style.transform = '';
    }

    resetProgress() {
        if (this.isPlaying) this.startLoop();
        else this.stopLoop();   // gepauzeerd naar een andere dia: de lijn begint weer leeg
    }

    togglePlay() {
        this.isPlaying = !this.isPlaying;
        const icon = document.getElementById('btn-play-pause').querySelector('i');
        icon.className = this.isPlaying ? 'fas fa-pause' : 'fas fa-play';
        // Pauze houdt alles stil waar het is (de lijn en de Ken Burns); afspelen gaat daar verder
        this.playerEl.classList.toggle('is-paused', !this.isPlaying);
        const anim = this.progressAnim;
        if (this.isPlaying && anim && anim.playState === 'paused') {
            const left = Math.max(0, this.duration - (Number(anim.currentTime) || 0));
            anim.play();
            clearTimeout(this.timer);
            this.timer = setTimeout(() => this.next(), left);
        } else if (this.isPlaying) {
            this.startLoop();
        } else {
            clearTimeout(this.timer);
            if (anim) anim.pause();
        }
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
                setScoreBadge(badge, newRating);
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
            if (badge) setScoreBadge(badge, oldRating);
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

    // Titel en gegevens van de dia
    showMeta(file) {
        const titleEl = document.getElementById('player-slide-title');
        const metaEl = document.getElementById('player-slide-meta');
        // Show folder name (from API or extracted from path)
        const folderName = file.folder || file.path.split(/[/\\\\]/).slice(-2, -1)[0] || 'Onbekende map';

        MediaMotion.rollText(titleEl, folderName, 1);

        metaEl.innerHTML = html`
<span class="player-meta-item"><i class="fas fa-folder" aria-hidden="true"></i> ${folderName}</span>
<span class="player-meta-item"><i class="fas fa-camera" aria-hidden="true"></i> ${file.name}</span>
<span class="player-meta-item"><i class="fas fa-calendar" aria-hidden="true"></i> ${new Date(file.modified * 1000).toLocaleDateString('nl-NL')}</span>
${file.size ? html`<span class="player-meta-item"><i class="fas fa-hard-drive" aria-hidden="true"></i> ${formatBytes(file.size)}</span>` : ''}
<div class="elo-rating-badge"></div>
`;
        setScoreBadge(metaEl.querySelector('.elo-rating-badge'), file.rating || 1000);
    }

    renderSlide() {
        const file = this.files[this.currentIndex];
        const container = document.getElementById('slide-container');

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
            // Ken Burns vanuit een willekeurig punt (nooit de rand), zo lang als de dia plus de overgang
            img.style.setProperty('--kb-x', `${Math.round(20 + Math.random() * 60)}%`);
            img.style.setProperty('--kb-y', `${Math.round(25 + Math.random() * 50)}%`);
            img.style.setProperty('--kb-dur', `${this.duration + 2000}ms`);
            // Een kleine drift (hooguit 2% opzij en 1,5% op of neer): een camera die meebeweegt
            img.style.setProperty('--kb-tx', `${(Math.random() * 4 - 2).toFixed(2)}%`);
            img.style.setProperty('--kb-ty', `${(Math.random() * 3 - 1.5).toFixed(2)}%`);
            img.addEventListener('error', () => {
                const thumb = vaultFileUrl('thumb', file.path);
                if (!img.src.endsWith(thumb)) img.src = thumb;
            }, { once: true });
            slide.appendChild(img);
        }

        container.appendChild(slide);
        if (!oldSlide && !isVideo && this.morphIn(slide, slide.querySelector('img'))) {
            this.showMeta(file);
            this.resetProgress();
            return;
        }
        // De eerste dia zonder omslag: hij vaagt op met het zwart mee, niet in de trage overgang
        if (!oldSlide) slide.classList.add('is-opening');

        // Kruisvervagen: de nieuwe dia komt erover, de oude gaat pas weg als de nieuwe er staat.
        // Snel bladeren: dia's die nog aan het vertrekken waren, gaan meteen weg.
        container.querySelectorAll('.cinematic-slide.is-leaving').forEach(el => el.remove());
        if (oldSlide) oldSlide.classList.add('is-leaving');
        requestAnimationFrame(() => requestAnimationFrame(() => {
            slide.classList.add('active');
            if (oldSlide) {
                oldSlide.classList.remove('active', 'is-opening', 'is-first');
                setTimeout(() => oldSlide.remove(), Motion.ms('--dur-6', 640) * 1.5 + 100);
            }
        }));

        this.showMeta(file);

        // For videos: don't start the timer (video 'ended' event handles advancement)
        // For photos: use normal timer
        if (isVideo) {
            this.stopLoop(); // Don't auto-advance, wait for video to finish
            // Show indeterminate progress for video
            const bar = document.getElementById('player-progress');
            if (bar) {
                bar.classList.add('mm-progress');
                bar.style.transform = 'none';
                bar.style.opacity = '0.3';
            }
        } else {
            this.resetProgress();
        }
    }
}

function startShow(story, opts = {}) {
    if (activeShow) activeShow.close();
    const playable = (story.files || []).some(f => f && (f.type === 'photo' || f.type === 'video'));
    if (!playable) {
        showToast('Leeg', "Geen foto's of video's in deze show", 'warning');
        return;
    }
    activeShow = new CinematicPlayer(story, opts);
}



// ==================== SPOTIFY WRAPPED ====================

let spotifyLoadSeq = 0; // alleen het antwoord op de laatste aanvraag telt (snel van jaar wisselen)

// De regel onder de titel: tot wanneer de gegevens gaan, en sinds wanneer de luistertijd een
// schatting is (plays van de Spotify-API en Last.fm na de laatste export); met een knop naar DataGatherer
async function loadSpotifyCoverage() {
    const chip = document.getElementById('spotify-coverage');
    if (!chip) return;
    try {
        const cov = await fetchJSON(`${API_BASE}/api/spotify/coverage`);
        if (!cov.last) { chip.hidden = true; return; }
        const day = (s) => new Date(s * 1000).toLocaleDateString('nl-NL', { day: 'numeric', month: 'short', year: 'numeric' });
        chip.replaceChildren();
        // Het grootste gat (bijv. tussen een oude export en de laatste nummers van de API) noemen we altijd
        const hole = (cov.holes || []).reduce((big, h) => (!big || h.until - h.from > big.until - big.from ? h : big), null);
        appendChildren(chip, [
            createEl('i', { class: `fas ${hole ? 'fa-calendar-xmark' : 'fa-calendar-check'}`, 'aria-hidden': 'true' }),
            createEl('span', {}, 'Bijgewerkt t/m ', createEl('span', { class: 'mono', text: day(cov.last) })),
            cov.estimated_since ? createEl('span', { class: 'spotify-coverage-estimate' },
                ' · schatting vanaf ', createEl('span', { class: 'mono', text: day(cov.estimated_since) })) : null,
            hole ? createEl('span', { class: 'spotify-coverage-hole' },
                ' · geen gegevens van ', createEl('span', { class: 'mono', text: day(hole.from) }),
                ' tot ', createEl('span', { class: 'mono', text: day(hole.until) })) : null,
            cov.waiting_for_import ? createEl('span', { class: 'spotify-coverage-estimate' },
                ' · je nieuwste gegevens zijn nog niet ingelezen') : null,
            createEl('button', { type: 'button', class: 'link-button spotify-coverage-link',
                text: cov.waiting_for_import ? 'Inlezen' : 'Bijwerken', onClick: () => switchMainView('datagatherer') })]);
        chip.hidden = false;
    } catch (_) {
        chip.hidden = true;   // de statistieken zelf melden een fout al
    }
}

// Na nieuwe gegevens uit DataGatherer (Spotify, Last.fm): de eerder opgehaalde statistieken gelden niet meer
window.addEventListener('datavault:data-changed', (e) => {
    if (!['spotify', 'lastfm'].includes(e.detail && e.detail.source)) return;
    Object.keys(_prefetchCache).filter(url => url.includes('/api/spotify/')).forEach(url => delete _prefetchCache[url]);
});

async function loadSpotifyStats() {
    const seq = ++spotifyLoadSeq;
    loadSpotifyCoverage();
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
        // Zonder één afgespeeld nummer geen nullen, lege lijsten en een verzonnen luister-DNA:
        // de lege staat met de weg naar de Spotify-kaart in DataGatherer
        const empty = !data.total_plays;
        showSpotifyEmpty(empty && year === 'all');
        if (content) content.style.display = empty && year === 'all' ? 'none' : 'block';
        if (empty && year === 'all') return;
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
    showSpotifyEmpty(false);
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

// Nog geen luistergeschiedenis: icoon, één zin en de knop naar de Spotify-kaart in DataGatherer
function showSpotifyEmpty(show) {
    let box = document.getElementById('spotify-empty');
    const years = document.querySelector('#section-spotify .year-selector');
    if (years) years.hidden = show;
    if (!show) {
        if (box) box.hidden = true;
        return;
    }
    if (!box) {
        box = createEl('div', { id: 'spotify-empty', class: 'spotify-empty' });
        box.appendChild(emptyState({
            icon: 'fa-headphones', title: 'Nog geen luistergeschiedenis',
            text: 'Lees je Spotify-export in via DataGatherer; daarna zie je hier je topartiesten, je nummers en wanneer je luistert.',
            action: { label: 'Spotify-export inlezen', primary: true, onClick: () => dgShowSource('spotify') },
        }));
        document.getElementById('spotify-content').before(box);
    }
    box.hidden = false;
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
    spotifyLastStats = data;
    // 1. Stats Cards
    updateText('stat-minutes', data.total_minutes.toLocaleString('nl-NL'));
    updateText('stat-top-artist', data.top_artists.length > 0 ? data.top_artists[0].name : '-');
    updateText('stat-plays', data.total_plays.toLocaleString('nl-NL'));
    updateText('stat-dna', spotifyLabel(data.listening_dna));
    const dnaEl = document.getElementById('stat-dna');
    if (dnaEl) dnaEl.dataset.dna = data.listening_dna || '';

    // 2. Lists: altijd alles wat er is. De kaarten zijn even hoog (CSS); is de ene lijst
    // korter, dan sluit een rustige slotregel hem af in plaats van een leeg vlak.
    const longest = Math.max(data.top_artists.length, data.top_tracks.length);
    renderSpotifyList('top-artists-list', data.top_artists, (item, i) => spotifyListItem({
        rank: `#${i + 1}`, name: item.name, meta: `${Math.round(item.ms / 60000).toLocaleString('nl-NL')} min`,
        icon: 'fab fa-spotify', query: item.name,
    }), spotifyListEnd(data.top_artists.length, longest, 'artiest', 'artiesten'));

    renderSpotifyList('top-tracks-list', data.top_tracks, (item, i) => spotifyListItem({
        rank: `#${i + 1}`, name: item.name, meta: `${item.artist} • ${item.count}× gespeeld`,
        icon: 'fas fa-play', query: `${item.name} ${item.artist}`,
    }), spotifyListEnd(data.top_tracks.length, longest, 'nummer', 'nummers'));

    // 2.5 New Insights
    if (data.new_insights) {
        const insights = data.new_insights;

        // Record Day
        if (insights.record_day) {
            const hoursEl = document.getElementById('stat-record-day-hours');
            if (hoursEl) hoursEl.replaceChildren(createEl('span', { class: 'stat-number', text: formatNumberNl(insights.record_day.hours, 1) }),
                createEl('span', { class: 'stat-unit', text: 'uur' }));
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
        <div class="otd-play-time"><i class="fas fa-clock" aria-hidden="true"></i> ${play.time}</div>
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
    // Beweging (motion-data.js): tellers, kaarten in ritme, groeiende staafjes, de DNA-helix
    if (window.MotionData) MotionData.spotifyRendered(data);
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

function renderSpotifyList(id, items, buildItem, end = null) {
    const list = document.getElementById(id);
    if (!list) return;
    list.replaceChildren(...items.map((item, i) => buildItem(item, i)), ...(end ? [end] : []));
}

// Slotregel onder een lijst die korter is dan de lijst ernaast: zo is te zien dat dit alles is
function spotifyListEnd(count, longest, one, many) {
    if (count >= longest) return null;
    const text = count === 0 ? `Geen ${many} in deze periode`
        : count === 1 ? `Dat is de enige ${one} in deze periode`
        : `Dat zijn alle ${count} ${many} in deze periode`;
    return createEl('div', { class: 'spotify-list-end', text });
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
            // Een cloudmodel van Ollama draait bij ollama.com: dat staat er altijd bij
            text: m.cloud ? `${m.name} · cloud, niet lokaal` : (m.parameter_size ? `${m.name} (${m.parameter_size})` : m.name),
        }));
        if (current && !installed) options.unshift(createEl('option', { value: current, text: `${current} (niet geïnstalleerd)` }));
        select.replaceChildren(...options);
        if (current) select.value = current;
        select.disabled = !reachable || names.length === 0;
    });
    if (!reachable) setChatStatus('Ollama offline', 'error', error || 'Start de Ollama-app');
    else if (!installed) setChatStatus('Model ontbreekt', 'warning', `Kies een ander model, of installeer het met: ollama pull ${current}`);
    else if ((models || []).some(m => m.name === current && m.cloud)) {
        setChatStatus('In de cloud (ollama.com)', 'warning', `Taalmodel: ${current}. Het draait bij ollama.com: je vragen en wat Pasha uit je archief meestuurt gaan naar internet.`);
    } else setChatStatus('Online', 'ok', `Taalmodel: ${current}`);
    if (typeof renderChatToolFormat === 'function') renderChatToolFormat();   // settings.js
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
    // Een cloudmodel (Ollama '-cloud') stuurt de vraag en de archiefcontext naar ollama.com: alleen na een ja
    const cloud = (chatModelState.models || []).some(m => m.name === model && m.cloud);
    if (cloud && !await dvConfirm({
        title: 'Cloudmodel gebruiken?',
        icon: 'fa-cloud',
        body: [`${model} draait niet op deze Mac maar bij ollama.com.`,
            'Alles wat je Pasha vraagt gaat dan naar internet, met wat hij uit je archief meestuurt '
            + '(mappen, plekken, Spotify en, als je dat aanzette, berichten).'],
        confirmLabel: 'Toch gebruiken',
    })) {
        renderChatModelSelects();
        return;
    }
    try {
        const data = await fetchJSON(`${API_BASE}/api/chat/model`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cloud ? { model, allow_cloud: true } : { model }),
        });
        chatModelState.current = data.current;
        if (data.tool_format_used) chatModelState.tool_format_used = data.tool_format_used;
        showToast('Pasha', data.cloud ? `Gebruikt nu ${data.current}, in de cloud bij ollama.com` : `Gebruikt nu het model ${data.current}`,
            data.cloud ? 'warning' : 'success');
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
// Wat tijdens het streamen verborgen blijft tot het antwoord af is (de server haalt het dan weg):
// een ACTION-tag, en een aanroep die het model als tekst schrijft ({"name": ...}, <tool_call>, find_photos(...))
const CHAT_TOOL_NAMES = ['find_photos', 'library_filter', 'open_smart_album', 'on_this_day', 'photos_at_place',
    'series_overview', 'job_status', 'recovery_summary', 'search_photos', 'open_folder', 'open_tab', 'open_file',
    'show_photos', 'scan_duplicates', 'propose_move', 'propose_keep_best', 'propose_export', 'play_song'];
const CHAT_LIVE_HIDDEN = new RegExp('(?:<<|<\\|)ACTION[\\s\\S]*$|\\{\\s*"action"[^}]*$|<\\/?tool_call>[\\s\\S]*$'
    + '|\\{\\s*"name"[\\s\\S]*$|\\b(?:' + CHAT_TOOL_NAMES.join('|') + ')\\s*\\([\\s\\S]*$');
const CHAT_NO_ANSWER = 'Dat lukte me niet, wil je het anders vragen?';

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
    // While streaming: nothing visible yet (whitespace, a hidden call) keeps the loading dots
    const showLive = (text) => {
        if (text || bubble.classList.contains('message-text')) showText(text);
    };
    let noAnswer = false;   // the server found nothing to say ({notice: 'no_answer'})
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
    let rawText = '';      // het antwoord van het model zoals het kwam, met de ACTION-tag
    const toolEvents = [];

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

                if (event.tool) { toolEvents.push(event); continue; }

                if (event.error) {
                    showError(String(event.error));
                    // Ollama weg of model ontbreekt: de status bovenin bijwerken
                    if (event.code === 'ollama_down' || event.code === 'model_missing') loadChatModels();
                    return;
                }

                // Ollama's eigen gereedschapsaanroep: de geschiedenis bewaart de actie als tag (zie bij 'done')
                if (typeof event.raw === 'string') { rawText = event.raw; continue; }

                if (event.notice === 'no_answer') { noAnswer = true; continue; }

                if (typeof event.replace === 'string') { fullText = event.replace; showLive(fullText.replace(/<<ACTION[\s\S]*$/, '').trimEnd()); continue; }

                if (typeof event.token === 'string' && event.token) {
                    fullText += event.token;
                    rawText += event.token;
                    // Een half binnengekomen ACTION-tag of een als tekst geschreven aanroep niet laten zien
                    showLive(fullText.replace(CHAT_LIVE_HIDDEN, '').trimEnd());
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                }

                if (event.done) {
                    finished = true;
                    const reply = parseChatReply(fullText);
                    fullText = reply.text;
                    // Niets om te laten zien (een oudere server stuurt geen notice): nooit een lege ballon
                    if (!fullText && !toolEvents.length && !reply.action) {
                        noAnswer = true;
                        fullText = CHAT_NO_ANSWER;
                    }
                    showText(fullText);
                    // Alleen een actie en geen tekst: de lege ballon weglaten, de resultaten komen eronder
                    if (!fullText && toolEvents.length) {
                        aiMsg.remove();
                        const allFailed = toolEvents.every(t => t.error);
                        fullText = allFailed ? toolEvents[0].error : 'Ik heb het opgezocht.';
                    }
                    toolEvents.forEach(chatRenderToolEvent);
                    if (reply.action) setTimeout(() => executeAIAction(reply.action), 1000);
                    // Lukte een actie, dan onthoudt de geschiedenis het antwoord mét de tag: zo blijft het
                    // model ook bij de volgende vraag een actie schrijven in plaats van alleen 'Dit vond ik.'
                    const acted = reply.action || toolEvents.some(t => !t.error);
                    if (noAnswer) {
                        // Geen echt antwoord: de vraag telt niet mee, zodat de geschiedenis om en om blijft
                        const last = window.chatHistory[window.chatHistory.length - 1];
                        if (last && last.role === 'user' && last.content === message) window.chatHistory.pop();
                    } else {
                        window.chatHistory.push({ role: 'assistant', content: acted && rawText.trim() ? rawText.trim() : fullText });
                    }
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
