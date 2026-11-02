// DataVault Dashboard - 'Ongedaan maken' in een melding
//
// Na een actie die bestanden verplaatst (verplaatsen, prullenmand, Keurer, Reeksen, Duplicaten,
// de selectiebalk) zegt een melding wat er gebeurde, met de knop 'Ongedaan maken'. Die knop
// roept precies het terugzetten aan dat het Herstelcentrum ook gebruikt (per id), dus er is
// maar één weg terug.
//
//   showUndoToast({
//       title, message, type = 'success',
//       key,        // de sleutel van de actie in het Herstelcentrum ('action:12', 'keurer:4',
//                   // 'duplicates:<run>'): wat al terug is, wordt nooit nog eens aangeboden
//       undo,       // {url, body} (POST, zoals entry.undo in het Herstelcentrum) of async () => antwoord
//       onUndone,   // (antwoord) => {title, message, type} | string | null: scherm bijwerken en wat de melding zegt
//       group,      // één melding per groep (Keurer, Welke is mooier?): een nieuwe neemt de plek in
//   })
//   undoToastDone(key)   // elders teruggezet (Herstelcentrum, ↓ in de Keurer): de knop verdwijnt
//
// Toetsen: Tab bereikt de knop (de tijd staat stil zolang de focus of de muis erop staat),
// ⌘Z (of Ctrl+Z) buiten een invoerveld zet de nieuwste actie terug. Na de klik wordt de melding
// zelf de uitkomst ('Teruggezet'), zonder een tweede melding erbij.

const UNDO_TOAST_MS = 10000;

const UndoToast = (() => {
    const live = new Map();     // key -> melding met een knop die nog werkt
    const used = new Set();     // keys die al terug zijn (of geprobeerd): nooit nog eens aanbieden
    const groups = new Map();   // group -> melding
    const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
    const HINT = isMac ? '⌘Z' : 'Ctrl+Z';

    function show(opts) {
        const { title, message, type = 'success', key, group } = opts;
        // Al teruggezet (of geen weg terug): een gewone melding
        if (!key || !opts.undo || used.has(key)) return showToast(title, message, type);

        // Dezelfde groep (snel achter elkaar in de Keurer): de melding blijft staan en krijgt de
        // nieuwe inhoud en de nieuwe weg terug, met een knikje; geen stapel meldingen
        const previous = group && groups.get(group);
        if (previous && previous.isConnected && !previous._leaving && previous._undo && !previous._undo.busy) {
            if (previous._undo.key !== key) live.delete(previous._undo.key);
            previous._undo = { ...opts, busy: false };
            live.set(key, previous);
            retitle(previous, title, message, type);
            const button = previous.querySelector('.toast-action');
            if (button) button.disabled = false;
            // Staat de muis of de focus erop, dan blijft hij staan (de tijd begint bij weggaan)
            if (previous.matches(':hover') || previous.contains(document.activeElement)) {
                clearTimeout(previous._hideTimer);
                previous._duration = UNDO_TOAST_MS;
            } else {
                toastSchedule(previous, UNDO_TOAST_MS);
            }
            restartToastTimer(previous);
            Motion.animate(previous, [{ transform: 'none' }, { transform: `translate3d(${-Motion.px('--motion-distance-sm', 6)}px, 0, 0)` }, { transform: 'none' }],
                { spring: 'snappy', key: 'nudge', calm: false });
            return previous;
        }

        const toast = showToast(title, message, type, {
            label: 'Ongedaan maken', icon: 'fa-rotate-left', hint: HINT, keepOpen: true,
            onClick: (button, t) => run(t, button),
        }, { duration: UNDO_TOAST_MS, unique: true, stay: true });
        toast.classList.add('toast-undo');
        toast._undo = { ...opts, busy: false };
        live.set(key, toast);
        if (group) groups.set(group, toast);
        return toast;
    }

    // Titel, tekst en soort van een melding die blijft staan
    function retitle(toast, title, message, type) {
        toast.dataset.title = String(title);
        toast.dataset.message = String(message);
        toast.querySelector('.toast-header strong').textContent = title;
        toast.querySelector('.toast-body').textContent = message;
        ['success', 'error', 'warning', 'info'].forEach(t => toast.classList.remove(t, `toast-${t}`));
        toast.classList.add(type, `toast-${type}`);
        const icon = toast.querySelector('.toast-icon');
        if (icon) {
            icon.className = `fas ${TOAST_ICONS[type] || TOAST_ICONS.info} toast-icon`;
            // Het icoon springt opnieuw (de CSS-animatie van een nieuwe melding)
            icon.style.animation = 'none';
            void icon.offsetWidth;
            icon.style.animation = '';
        }
    }

    async function run(toast, button) {
        const entry = toast._undo;
        if (!entry || entry.busy || used.has(entry.key)) return;
        entry.busy = true;
        used.add(entry.key);
        live.delete(entry.key);
        const hadFocus = toast.contains(document.activeElement);
        button.disabled = true;
        button.classList.add('is-loading');
        button.querySelector('.toast-action-label').textContent = 'Terugzetten…';
        clearTimeout(toast._hideTimer);
        let outcome = null;
        try {
            let data;
            if (typeof entry.undo === 'function') {
                data = await entry.undo();
            } else {
                data = await fetchJSON(`${API_BASE}${entry.undo.url}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(entry.undo.body),
                });
            }
            outcome = entry.onUndone ? await entry.onUndone(data) : defaultOutcome(data);
            refreshRecovery();
        } catch (err) {
            toastFetchError('Ongedaan maken mislukt', err);
            // De schijf even weg of de server niet bereikbaar: de knop blijft, nog eens proberen mag.
            // Kan de stap echt niet (meer) terug (404/409/410 en dergelijke), dan gaat de knop weg.
            if (retryable(err) && toast.isConnected && !toast._leaving && toast._undo === entry) {
                entry.busy = false;
                used.delete(entry.key);
                live.set(entry.key, toast);
                button.disabled = false;
                button.classList.remove('is-loading');
                button.querySelector('.toast-action-label').textContent = 'Ongedaan maken';
                linger(toast, UNDO_TOAST_MS);
                return;
            }
            done(toast);
            return;
        }
        if (!outcome) { done(toast); return; }
        if (typeof outcome === 'string') outcome = { title: 'Teruggezet', message: outcome, type: 'success' };
        // De melding wordt de uitkomst: zelfde plek, nieuw icoon, de knop gaat weg
        const actions = toast.querySelector('.toast-actions');
        if (actions) actions.remove();
        toast._undo = null;
        toast.classList.remove('toast-undo');
        retitle(toast, outcome.title || 'Teruggezet', outcome.message || '', outcome.type || 'success');
        linger(toast, TOAST_MS[outcome.type || 'success'] || TOAST_MS.success);
        // De focus stond op de knop die nu weg is: naar de melding zelf, niet naar <body>
        if (hadFocus) {
            toast.tabIndex = -1;
            toast.focus({ preventScroll: true });
        }
    }

    // Een verzoek dat later wel kan lukken: geen antwoord (netwerk), of de server/schijf even weg
    function retryable(err) {
        const status = err && err.status;
        return !status || status === 502 || status === 503 || status === 504;
    }

    // Nieuwe tijd voor de melding; staat de muis of de focus erop, dan begint die pas bij weggaan
    // (Safari geeft een geklikte knop geen focus, dus 'focusin' alleen is niet genoeg). Krimpt de
    // melding onder de muis weg (de knop verdwijnt), dan komt er soms geen mouseleave: daarom kijkt
    // hij zelf elke halve seconde of de muis en de focus er nog zijn.
    function linger(toast, duration) {
        toast._duration = duration;
        toast.style.setProperty('--toast-ms', `${duration}ms`);
        const held = () => toast.matches(':hover') || toast.contains(document.activeElement);
        clearTimeout(toast._hideTimer);
        if (!held()) {
            toastSchedule(toast, duration);
            restartToastTimer(toast);
            return;
        }
        const watch = () => {
            if (toast._leaving || !toast.isConnected) return;
            if (held()) { toast._hideTimer = setTimeout(watch, 500); return; }
            toastSchedule(toast, duration);
            restartToastTimer(toast);
        };
        toast._hideTimer = setTimeout(watch, 500);
    }

    function done(toast) {
        if (toast._undo) { live.delete(toast._undo.key); toast._undo = null; }
        hideToast(toast);
    }

    // Wat het Herstelcentrum ook zegt na terugzetten
    function defaultOutcome(data) {
        const restored = data && typeof data.restored === 'number' ? data.restored : 1;
        const skipped = ((data && data.skipped) || []).length + ((data && data.errors) || []).length;
        let message = `${restored.toLocaleString('nl-NL')} ${restored === 1 ? 'bestand staat' : 'bestanden staan'} weer op de oude plek.`;
        if (skipped) message += ` ${skipped} kon${skipped === 1 ? '' : 'den'} niet terug; de reden staat in het Herstelcentrum.`;
        if (typeof jobsLibraryRefresh === 'function') jobsLibraryRefresh();
        return { title: 'Teruggezet', message, type: skipped ? 'warning' : 'success' };
    }

    // Staat het Herstelcentrum open, dan klopt zijn lijst meteen weer
    function refreshRecovery() {
        if (typeof currentMainView === 'function' && currentMainView() === 'recovery' && typeof recoveryLoad === 'function') recoveryLoad();
    }

    // Elders teruggezet: de knop hier verdwijnt (de melding zelf mag uitlopen)
    function markDone(key) {
        if (!key) return;
        used.add(key);
        const toast = live.get(key);
        live.delete(key);
        if (toast && toast._undo && toast._undo.key === key && !toast._undo.busy) done(toast);
    }

    // ⌘Z buiten een invoerveld: de nieuwste melding met een werkende knop
    document.addEventListener('keydown', (e) => {
        if (e.key.toLowerCase() !== 'z' || e.shiftKey || e.altKey || !(isMac ? e.metaKey : e.ctrlKey) || e.defaultPrevented) return;
        const t = e.target;
        if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
        const toasts = [...live.values()].filter(x => x.isConnected && !x._leaving && x._undo && !x._undo.busy);
        const newest = toasts[toasts.length - 1];
        if (!newest) return;
        e.preventDefault();
        const button = newest.querySelector('.toast-action');
        if (button) run(newest, button);
    });

    return { show, markDone, get live() { return [...live.keys()]; } };
})();

function showUndoToast(opts) {
    return UndoToast.show(opts);
}

function undoToastDone(key) {
    UndoToast.markDone(key);
}
