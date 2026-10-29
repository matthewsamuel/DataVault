// DataVault Dashboard - Sneltoetsen
// Note: switchMainView, cycleTheme en currentMainView staan in app.js; isTypingTarget,
// anyOverlayOpen en registerOverlayCloser in ui.js.
//
// Eén schema voor de hele app; ? toont het overzicht:
//   1-9 of g + letter   naar een onderdeel (g b = Bestanden, g t = Tijdlijn, ...)
//   /                   naar het zoekveld
//   t                   thema wisselen (Auto, Licht, Donker)
//   ?                   dit overzicht
//   Esc                 het bovenste venster sluiten (ui.js), of uit een invoerveld
// In de rasters (Bestanden, Tijdlijn) bewegen de pijltjes, Home/End en PageUp/PageDown;
// Enter of spatie opent. De viewer, Keurer en de slideshow hebben hun eigen toetsen,
// die ook in het overzicht staan.
//
// Geen sneltoets doet iets terwijl je typt (invoerveld, tekstvak, keuzelijst) of samen
// met Cmd, Ctrl of Alt (dat zijn de toetsen van de browser en macOS). Zolang er een
// venster open is, wisselen 1-9, g, / en t niets op de achtergrond.
//
// Een nieuw onderdeel met eigen toetsen voegt een groep toe met addShortcutGroup(...),
// dan staat het vanzelf in het overzicht.

const SHORTCUT_VIEWS = [
    { view: 'home', key: '1', g: 'h', label: 'Home' },
    { view: 'viewer', key: '2', g: 'b', label: 'Bestanden' },
    { view: 'duplicates', key: '3', g: 'd', label: 'Duplicaten' },
    { view: 'spotify', key: '4', g: 's', label: 'Spotify' },
    { view: 'shows', key: '5', g: 'o', label: 'Shows' },
    { view: 'keurer', key: '6', g: 'k', label: 'Keurer' },
    { view: 'timeline', key: '7', g: 't', label: 'Tijdlijn' },
    { view: 'recovery', key: '8', g: 'r', label: 'Herstelcentrum' },
    { view: 'settings', key: '9', g: 'i', label: 'Instellingen' },
    // Meer dan negen onderdelen: de rest alleen met g + letter
    { view: 'albums', key: null, g: 'a', label: 'Albums' },
    { view: 'datagatherer', key: null, g: 'v', label: 'DataGatherer' },
    { view: 'map', key: null, g: 'm', label: 'Kaart' },
];

const SHORTCUT_GROUPS = [
    {
        title: 'Overal',
        wide: true,     // over de hele breedte, in twee kolommen: de andere groepen staan eronder
        keys: [
            [['1', '…', '9'], 'Naar een onderdeel: ' + SHORTCUT_VIEWS.filter(v => v.key).map(v => `${v.key} ${v.label}`).join(', ')],
            [['g', 'letter'], 'Naar een onderdeel: ' + SHORTCUT_VIEWS.map(v => `${v.g} ${v.label}`).join(', ')],
            [['/'], 'Naar het zoekveld; Enter zoekt in de hele bibliotheek (s opent het zoekvenster)'],
            [['t'], 'Thema wisselen: Auto, Licht, Donker'],
            [['?'], 'Dit overzicht'],
            [['Esc'], 'Bovenste venster sluiten, of uit een invoerveld'],
            [['Tab'], 'Naar het volgende onderdeel; in een venster blijft Tab binnen het venster, langs elke knop'],
            // Safari slaat met Tab buiten vensters knoppen over (instelling van Safari zelf)
            [['⌥', 'Tab'], 'In Safari: ook langs knoppen op de pagina (zie onderaan)'],
        ],
    },
    {
        title: 'Bestanden, Tijdlijn en Albums',
        keys: [
            [['←', '→', '↑', '↓'], 'Naar de volgende kaart'],
            [['Home', 'End'], 'Eerste of laatste kaart'],
            [['PageUp', 'PageDown'], 'Een scherm verder'],
            [['Enter'], 'Openen (map of viewer); ook spatie'],
            [['Backspace'], 'Bestanden: een map omhoog'],
        ],
    },
    {
        title: 'Viewer',
        keys: [
            [['←', '→'], 'Vorige of volgende foto'],
            [['+', '−', '0'], 'Inzoomen, uitzoomen, passend'],
            [['m'], 'Verplaatsen'],
            [['f'], 'Toon in Finder'],
            [['spatie'], 'Audio pauzeren of afspelen'],
        ],
    },
    {
        title: 'Keurer',
        keys: [
            [['←'], 'Houden'],
            [['→'], 'Naar de prullenmand'],
            [['↑'], 'Verplaatsen'],
            [['↓'], 'Laatste stap ongedaan maken'],
            [['spatie'], 'Groot bekijken'],
        ],
    },
    {
        title: 'Slideshow',
        keys: [
            [['←', '→'], 'Vorige of volgende dia'],
            [['spatie'], 'Pauzeren of afspelen'],
        ],
    },
    {
        title: 'Mappen kiezen',
        keys: [
            [['↑', '↓'], 'Vorige of volgende map'],
            [['→', '←'], 'Openklappen of dichtklappen'],
            [['Enter'], 'Deze map kiezen'],
        ],
    },
];

function addShortcutGroup(title, keys) {
    SHORTCUT_GROUPS.push({ title, keys });
    const help = document.getElementById('shortcut-help');
    if (help) help.remove();   // de volgende keer opnieuw opgebouwd, met deze groep erbij
}

// ==================== OVERZICHT (?) ====================

function buildShortcutHelp() {
    const close = createEl('button', {
        type: 'button', class: 'modal-close', 'aria-label': 'Sluiten', title: 'Sluiten (Esc)', onClick: closeShortcutHelp,
    }, '×');
    const groups = SHORTCUT_GROUPS.map(group => createEl('section', { class: `shortcut-group${group.wide ? ' is-wide' : ''}` },
        createEl('h3', { text: group.title }),
        createEl('dl', null, ...group.keys.flatMap(([keys, what]) => [
            createEl('dt', null, ...keys.map(k => createEl('kbd', { text: k }))),
            createEl('dd', { text: what }),
        ]))));
    const overlay = createEl('div', {
        id: 'shortcut-help', class: 'dv-overlay shortcut-help', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'shortcut-help-title',
        onClick: (e) => { if (e.target === overlay) closeShortcutHelp(); },
    },
        createEl('div', { class: 'dv-overlay-panel' },
            createEl('div', { class: 'dv-overlay-header' },
                createEl('h2', { id: 'shortcut-help-title', class: 'modal-title' },
                    createEl('i', { class: 'fas fa-keyboard', 'aria-hidden': 'true' }), ' Sneltoetsen'),
                close),
            // Scrolt in een laag venster; met tabindex ook met de pijltjes te scrollen
            createEl('div', { class: 'shortcut-groups', tabindex: '0', role: 'region', 'aria-label': 'Alle sneltoetsen' }, ...groups),
            createEl('p', { class: 'shortcut-note',
                text: 'Sneltoetsen doen niets terwijl je in een invoerveld typt, en nooit samen met Cmd, Ctrl of Alt. ' +
                      'Wil je in Safari met Tab alleen ook langs knoppen: Safari > Instellingen > Geavanceerd, de Tab-optie aan.' })));
    document.body.appendChild(overlay);
    return overlay;
}

function openShortcutHelp() {
    const help = document.getElementById('shortcut-help') || buildShortcutHelp();
    help.classList.add('active');
    setTimeout(() => help.querySelector('.modal-close').focus(), 0);
}

function closeShortcutHelp() {
    const help = document.getElementById('shortcut-help');
    if (help) help.classList.remove('active');
}

function toggleShortcutHelp() {
    const help = document.getElementById('shortcut-help');
    if (help && help.classList.contains('active')) closeShortcutHelp();
    else openShortcutHelp();
}

registerOverlayCloser('shortcut-help', closeShortcutHelp);

// ==================== DE TOETSEN ====================

let shortcutGPending = null;   // timer: na 'g' wacht de app even op de letter

function goToView(view) {
    switchMainView(view);
    // Focus op het gekozen menu-item: zichtbaar waar je bent, en Tab gaat van daar verder
    const tab = document.querySelector(`.top-nav [data-view="${view}"]`);
    if (tab) tab.focus({ preventScroll: true });
}

document.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    if (isTypingTarget(e.target)) return;

    if (e.key === '?') {
        // Niet boven de viewer, een dialoog of de slideshow: hun toetsen zouden eronder doorwerken
        const help = document.getElementById('shortcut-help');
        const helpOpen = !!help && help.classList.contains('active');
        if (anyOverlayOpen() && !helpOpen) return;
        e.preventDefault();
        toggleShortcutHelp();
        return;
    }
    if (anyOverlayOpen()) return;
    const themeMenu = document.getElementById('theme-dropdown');
    if (themeMenu && themeMenu.classList.contains('open')) return;

    if (shortcutGPending) {
        clearTimeout(shortcutGPending);
        shortcutGPending = null;
        const target = SHORTCUT_VIEWS.find(v => v.g === e.key.toLowerCase());
        if (target) {
            e.preventDefault();
            goToView(target.view);
        }
        return;
    }

    const byNumber = SHORTCUT_VIEWS.find(v => v.key === e.key);
    if (byNumber) {
        e.preventDefault();
        goToView(byNumber.view);
    } else if (e.key === 'g') {
        shortcutGPending = setTimeout(() => { shortcutGPending = null; }, 1500);
    } else if (e.key === '/') {
        e.preventDefault();
        const search = document.getElementById('global-search');
        search.focus();
        search.select();
    } else if (e.key === 't') {
        e.preventDefault();
        cycleTheme();
    }
});
