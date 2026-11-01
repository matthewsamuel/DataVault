// DataVault Dashboard - Gedeelde UI-bouwstenen
// Note: API_BASE is defined in app.js
//
// 1. Lege, mislukte en ladende stukken scherm zien er overal hetzelfde uit:
//    emptyState({icon, title, text, action}), errorState(title, err, retry) en
//    skeletonCards(n) / skeletonRows(n). Gebruik ze in plaats van een lege <div> of een
//    losse "Loading..."-tekst.
//
// 2. Vensters boven de pagina (elke .modal, de schermvullende chat, de Keurer-preview, de
//    slideshow en alles met de klasse .dv-overlay) gedragen zich hier op één manier:
//    - bij openen gaat de focus het venster in (het eerste element met data-autofocus,
//      anders het venster zelf); Tab en Shift+Tab blijven binnen het bovenste venster;
//    - Escape sluit alleen het bovenste venster (als zijn eigen code dat nog niet deed);
//    - bij sluiten gaat de focus terug naar waar hij stond, bijvoorbeeld de kaart in het
//      raster of het chatvak waaruit een foto geopend werd.
//    Een nieuw venster hoeft hiervoor niets te doen behalve .modal.active (of
//    .dv-overlay.active) te zetten. Heeft het bij Escape meer nodig dan het weghalen van
//    'active', registreer dan zijn sluitfunctie: registerOverlayCloser('modal-id', fn).
//
// Laadt na dom.js en vóór app.js.

// ==================== LEEG, FOUT, LADEN ====================

// Eén vorm voor "hier is (nog) niets": icoon, titel, uitleg en zo nodig één knop
function emptyState({ icon = 'fa-inbox', title, text, action, tone } = {}) {
    return createEl('div', {
        class: 'empty-state' + (tone ? ` empty-state-${tone}` : ''),
        role: tone === 'error' ? 'alert' : 'status',
    },
        createEl('i', { class: `fas ${icon} empty-state-icon`, 'aria-hidden': 'true' }),
        title ? createEl('div', { class: 'empty-state-title', text: title }) : null,
        text ? createEl('div', { class: 'empty-state-text', text }) : null,
        action ? createEl('button', {
            class: `btn ${action.primary ? 'btn-primary' : 'btn-secondary'} btn-small empty-state-action`,
            onClick: action.onClick,
            text: action.label,
        }) : null);
}

// Iets laden mislukte: de melding van de server en (als dat kan) 'Opnieuw proberen'.
// Is het archief weg, dan zegt de banner bovenin dat al; hier dan een rustige tekst.
function errorState(title, err, retry) {
    const gone = err && err.code === 'vault_unavailable';
    return emptyState({
        icon: gone ? 'fa-plug' : 'fa-exclamation-triangle',
        tone: gone ? null : 'error',
        title,
        text: gone ? 'Het archief is niet bereikbaar. Sluit de schijf aan; dit laadt dan vanzelf opnieuw.'
            : ((err && err.message) || String(err || 'Er ging iets mis')),
        action: retry ? { label: 'Opnieuw proberen', onClick: retry } : null,
    });
}

// Grijze plaatshouders in de vorm van wat er komt (kaarten in een raster)
function skeletonCards(count = 12, extraClass = '') {
    const box = createEl('div', { class: `skeleton-grid ${extraClass}`.trim(), role: 'status', 'aria-label': 'Laden…' });
    for (let i = 0; i < count; i++) box.appendChild(createEl('div', { class: 'skeleton skeleton-card', 'aria-hidden': 'true' }));
    return box;
}

// ... of regels in een lijst
function skeletonRows(count = 4) {
    const box = createEl('div', { class: 'skeleton-list', role: 'status', 'aria-label': 'Laden…' });
    for (let i = 0; i < count; i++) {
        box.appendChild(createEl('div', { class: 'skeleton-row', 'aria-hidden': 'true' },
            createEl('div', { class: 'skeleton skeleton-line skeleton-line-wide' }),
            createEl('div', { class: 'skeleton skeleton-line' })));
    }
    return box;
}

// Een klikbare kaart (div) ook met het toetsenbord bruikbaar maken: Tab erheen, Enter of spatie
function makeActivatable(el, label) {
    el.tabIndex = 0;
    el.setAttribute('role', 'button');
    if (label) el.setAttribute('aria-label', label);
    el.addEventListener('keydown', (e) => {
        if (e.target !== el || (e.key !== 'Enter' && e.key !== ' ')) return;
        e.preventDefault();
        el.click();
    });
    return el;
}

// Is dit een plek waar de gebruiker typt? Dan doet geen enkele sneltoets iets.
function isTypingTarget(el) {
    if (!el || el === document.body) return false;
    if (el.isContentEditable) return true;
    if (el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') return true;
    if (el.tagName !== 'INPUT') return false;
    return !['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'color', 'file'].includes((el.type || '').toLowerCase());
}

// ==================== VENSTERS: FOCUS EN ESCAPE ====================

const OVERLAY_SELECTOR = '.modal.active, .chat-fullscreen-modal.active, #keurer-preview-overlay.active, '
    + '#cinematic-player.active, .dv-overlay.active';
const OVERLAY_CANDIDATES = '.modal, .chat-fullscreen-modal, #keurer-preview-overlay, #cinematic-player, .dv-overlay';
const TABBABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), '
    + 'textarea:not([disabled]), video[controls], audio[controls], [tabindex]:not([tabindex="-1"]), [contenteditable="true"]';

// Sluitfuncties per id; zonder sluitfunctie haalt Escape alleen de klasse 'active' weg
const overlayClosers = {
    'modal-viewer': () => closeModal(),
    'modal-move-file': () => closeMoveFileModal(),
    'modal-folder-selector': () => closeFolderSelector(),
    'modal-comparison': () => closeComparisonModal(),
    'modal-dup-confirm': () => closeCleanupDialog(),
    'modal-storage-details': () => closeStorageModal(),
    'modal-dna-explanation': () => closeDnaModal(),
    'modal-insight-explanation': () => closeInsightModal(),
    'modal-keurer-move': () => keurerCloseMoveModal(),
    'modal-keurer-folder': () => keurerCloseFolderPicker(),
    'modal-timeline-folders': () => timelineCloseFolderPicker(),
    'modal-show-settings': () => closeSettingsModal(),
    'chat-fullscreen-modal': () => closeChatFullscreen(),
    'keurer-preview-overlay': () => keurerClosePreview(),
    'cinematic-player': () => { if (activeShow) activeShow.close(); },
};

function registerOverlayCloser(id, close) {
    overlayClosers[id] = close;
}

const overlayStack = [];        // [{el, returnTo}] in de volgorde waarin ze opengingen
let lastFocused = null;         // het laatste element met focus (ook als het daarna 'blur' kreeg)

function overlayShown(el) {
    return el.isConnected && el.getClientRects().length > 0;
}

function openOverlays() {
    return [...document.querySelectorAll(OVERLAY_SELECTOR)].filter(overlayShown);
}

// Het bovenste open venster: hoogste z-index, bij gelijke z-index het laatst geopende
function topOverlay() {
    const open = openOverlays();
    if (!open.length) return null;
    const order = el => {
        const at = overlayStack.findIndex(entry => entry.el === el);
        return at < 0 ? overlayStack.length : at;
    };
    const z = el => parseInt(getComputedStyle(el).zIndex, 10) || 0;
    return open.sort((a, b) => (z(a) - z(b)) || (order(a) - order(b))).pop();
}

function anyOverlayOpen() {
    return openOverlays().length > 0;
}

function closeOverlay(el) {
    const close = overlayClosers[el.id];
    if (close) close();
    else el.classList.remove('active');
}

// Wat Tab in een venster langsgaat, in de volgorde van de pagina
function tabbables(root) {
    const items = [...root.querySelectorAll(TABBABLE)].filter(el => el.getClientRects().length > 0
        && !(el.hasAttribute('tabindex') && el.tabIndex < 0)
        && !el.closest('[hidden], [inert]') && getComputedStyle(el).visibility !== 'hidden');
    // Een groep keuzerondjes is één stop, zoals in de browser: het gekozen rondje (of het eerste)
    const isRadio = el => el.tagName === 'INPUT' && el.type === 'radio' && el.name;
    const stops = new Map();
    return items.filter(el => {
        if (!isRadio(el)) return true;
        if (!stops.has(el.name)) {
            const group = items.filter(r => isRadio(r) && r.name === el.name);
            stops.set(el.name, group.find(r => r.checked) || group[0]);
        }
        return stops.get(el.name) === el;
    });
}

// Het element waar Tab (of Shift+Tab) vanaf 'active' heen gaat, binnen top, rondom aan de randen
function nextTabStop(top, active, back) {
    const items = tabbables(top);
    if (!items.length) return null;
    if (!active || !top.contains(active) || active === top) return back ? items[items.length - 1] : items[0];
    const at = items.indexOf(active);
    if (at >= 0) return items[(at + (back ? -1 : 1) + items.length) % items.length];
    // De focus staat op iets dat zelf geen Tab-stop is (een kaart met tabindex -1, het venster):
    // de eerstvolgende stop erna (of ervoor) in de pagina
    if (back) {
        return [...items].reverse().find(el => el.compareDocumentPosition(active) & Node.DOCUMENT_POSITION_FOLLOWING)
            || items[items.length - 1];
    }
    return items.find(el => active.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) || items[0];
}

// Waar de focus na sluiten heen gaat. Een foto die vanuit de chat geopend werd, geeft de
// focus terug aan het chatvak (niet aan het fotootje in de berichtenlijst).
function focusReturnTarget(el) {
    if (el && el.closest && el.closest('#chat-messages')) {
        const full = document.getElementById('chat-fullscreen-modal');
        return document.getElementById(full && full.classList.contains('active') ? 'chat-input-fullscreen' : 'chat-input');
    }
    return el;
}

function canTakeFocus(el) {
    return el && el !== document.body && el.isConnected && typeof el.focus === 'function'
        && el.getClientRects().length > 0 && !el.disabled;
}

function overlayLabel(el) {
    const title = el.querySelector('.modal-title, h2, h3');
    return title ? title.textContent.trim() : '';
}

function overlayOpened(el) {
    const active = document.activeElement;
    const returnTo = focusReturnTarget(active && active !== document.body ? active : lastFocused);
    overlayStack.push({ el, returnTo });
    if (!el.hasAttribute('role')) el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    if (!el.hasAttribute('aria-label') && !el.hasAttribute('aria-labelledby')) {
        const label = overlayLabel(el);
        if (label) el.setAttribute('aria-label', label);
    }
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
    // Eigen code kan de focus al gezet hebben (chatvak, zoekveld); anders het venster zelf
    // of zijn data-autofocus-element. Een tel later, zodat de inhoud er staat.
    setTimeout(() => {
        if (!overlayShown(el) || el.contains(document.activeElement)) return;
        if (topOverlay() !== el) return;
        const first = el.querySelector('[data-autofocus]');
        (canTakeFocus(first) ? first : el).focus({ preventScroll: true });
    }, 30);
}

function overlayClosed(entry) {
    const el = entry.el;
    el.removeAttribute('aria-modal');
    // Stond de focus in het gesloten venster (of nergens), dan terug naar waar hij was
    const active = document.activeElement;
    const lost = !active || active === document.body || el.contains(active) || !active.isConnected;
    if (!lost) return;
    const top = topOverlay();
    const target = entry.returnTo;
    if (canTakeFocus(target) && (!top || top.contains(target))) {
        target.focus({ preventScroll: true });
    } else if (top) {
        top.focus({ preventScroll: true });
    }
}

let overlaySyncQueued = false;
function syncOverlays() {
    overlaySyncQueued = false;
    const open = new Set(openOverlays());
    // Gesloten: in omgekeerde volgorde, zodat de focus bij het onderste nog open venster eindigt
    for (let i = overlayStack.length - 1; i >= 0; i--) {
        if (!open.has(overlayStack[i].el)) overlayClosed(overlayStack.splice(i, 1)[0]);
    }
    open.forEach(el => {
        if (!overlayStack.some(entry => entry.el === el)) overlayOpened(el);
    });
}

function queueOverlaySync() {
    if (overlaySyncQueued) return;
    overlaySyncQueued = true;
    // Na de huidige klik of toets: dan staat de klasse (en eventueel eigen focus) goed
    Promise.resolve().then(syncOverlays);
}

document.addEventListener('focusin', (e) => { lastFocused = e.target; }, true);

// Klik op iets dat zelf geen focus kan krijgen (een kaart, een fotootje): onthoud het
// dichtstbijzijnde element dat het wel kan, zodat de focus daar na sluiten terugkomt
document.addEventListener('mousedown', (e) => {
    const target = e.target.closest && e.target.closest('[tabindex], a[href], button, input, select, textarea, #chat-messages');
    if (target) lastFocused = target;
}, true);

// Tab blijft in het bovenste venster. De focus gaat hier altijd met de hand naar de volgende
// stop: Safari slaat bij Tab (met de standaardinstelling) knoppen over en zou zo uit het venster
// springen, naar de pagina erachter.
document.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab' || e.ctrlKey || e.metaKey || e.altKey) return;
    const top = topOverlay();
    if (!top) return;
    e.preventDefault();
    const next = nextTabStop(top, document.activeElement, e.shiftKey);
    (next || top).focus({ preventScroll: !next });
}, true);

// Escape: wat het bovenste venster vóór de toets was. Sloot zijn eigen code het al (de viewer,
// de mappenboom, de slideshow doen dat zelf), dan gebeurt hier niets meer; anders sluit dit het.
let escapeTop = null;
window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') escapeTop = topOverlay();
}, true);
window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const top = escapeTop;
    escapeTop = null;
    if (top) {
        if (overlayShown(top) && top.matches(OVERLAY_SELECTOR) && topOverlay() === top) closeOverlay(top);
        return;
    }
    // Geen venster open: Escape haalt de focus uit een invoerveld (bv. het zoekveld)
    const active = document.activeElement;
    if (active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) active.blur();
});

function initOverlayWatch() {
    const onClass = (records) => {
        if (records.some(r => r.target.matches && r.target.matches(OVERLAY_CANDIDATES))) queueOverlaySync();
    };
    new MutationObserver(onClass).observe(document.body, { attributes: true, attributeFilter: ['class'], subtree: true });
    // De slideshow en Manage Sources worden als nieuw element aan <body> toegevoegd
    new MutationObserver(queueOverlaySync).observe(document.body, { childList: true });
    syncOverlays();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initOverlayWatch);
else initOverlayWatch();

// ==================== BEVESTIGEN ====================

// Eén vraag in het gewone venster van de app (in plaats van de kale bevestigvraag van de browser).
// Geeft een Promise<boolean>. De focus staat eerst op Annuleren (Enter doet dus niets onherroepelijks),
// Escape, het kruisje en een klik naast het venster annuleren, en de focus gaat daarna terug.
//   title:        de vraag, kort ('8 bestanden terugzetten?')
//   body:         één tekst of een lijst regels (elke tekst een alinea; een element blijft zoals het is)
//   confirmLabel: de knop die het doet ('Terugzetten')
//   danger:       rode knop voor iets dat je niet zomaar terugdraait
//   icon:         Font Awesome-icoon in de titel
function dvConfirm({ title, body = '', confirmLabel = 'Doorgaan', danger = false, icon = 'fa-circle-question' } = {}) {
    document.getElementById('modal-dv-confirm')?.remove();
    const returnTo = focusReturnTarget(document.activeElement !== document.body ? document.activeElement : lastFocused);
    return new Promise((resolve) => {
        let done = false;
        const finish = (answer) => {
            if (done) return;
            done = true;
            modal.classList.remove('active');
            // Meteen terug naar waar de focus was, zodat de code na 'await dvConfirm' daar verder kan
            if (canTakeFocus(returnTo)) returnTo.focus({ preventScroll: true });
            // Na de sluitanimatie weghalen; de focus is dan al terug (ui.js, overlayClosed)
            setTimeout(() => modal.remove(), 250);
            resolve(answer);
        };
        const lines = (Array.isArray(body) ? body : [body]).filter(Boolean);
        const cancel = createEl('button', { type: 'button', class: 'btn btn-secondary', 'data-autofocus': '',
            text: 'Annuleren', onClick: () => finish(false) });
        const ok = createEl('button', { type: 'button', class: `btn ${danger ? 'btn-danger' : 'btn-primary'}`,
            text: confirmLabel, onClick: () => finish(true) });
        const modal = createEl('div', {
            class: 'modal dv-confirm', id: 'modal-dv-confirm', role: 'alertdialog',
            'aria-labelledby': 'dv-confirm-title', 'aria-describedby': lines.length ? 'dv-confirm-body' : null,
            onClick: (e) => { if (e.target === modal) finish(false); },
        },
            createEl('div', { class: 'modal-container dv-confirm-container' },
                createEl('div', { class: 'modal-header' },
                    createEl('h2', { class: 'modal-title', id: 'dv-confirm-title' },
                        createEl('i', { class: `fas ${icon}`, 'aria-hidden': 'true' }), ' ', createEl('span', { text: title })),
                    createEl('button', { type: 'button', class: 'modal-close', 'aria-label': 'Annuleren', title: 'Annuleren (Esc)',
                        onClick: () => finish(false) }, '×')),
                lines.length ? createEl('div', { class: 'modal-body dv-confirm-body', id: 'dv-confirm-body' },
                    ...lines.map(line => (typeof line === 'string' ? createEl('p', { text: line }) : line))) : null,
                createEl('div', { class: 'modal-footer' }, cancel, ok)));
        document.body.append(modal);
        registerOverlayCloser('modal-dv-confirm', () => finish(false));
        modal.classList.add('active');
    });
}
