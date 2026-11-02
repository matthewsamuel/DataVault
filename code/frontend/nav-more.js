// DataVault Dashboard - het menu Meer in de bovenbalk
//
// Elk onderdeel heeft een eigen knop in het menu. Past de rij niet (1024 px, Split View), dan
// schuiven de laatste onderdelen naar het menu Meer, zodat er altijd lucht blijft voor het
// zoekveld en niets onder iets anders verdwijnt. Staat het open onderdeel in Meer, dan krijgt
// Meer het groene streepje (switchMainView roept NavMore.sync aan).
//
// Toetsen op Meer: Enter, spatie of ↓ opent en zet de focus op het eerste onderdeel, ↑ op het
// laatste. In het menu: ↑ ↓ (rondgaand), Home, End, een beginletter, Enter kiest, Esc sluit en
// zet de focus terug op Meer, Tab sluit en gaat verder. Een klik ernaast sluit het ook.
//
// Beweging (docs/MOTION.md): openen veert (CSS shell-menu-in, de onderdelen volgen op de
// stagger-stap), sluiten vervaagt in --dur-3. In Uit staat het menu meteen open of dicht.

const NavMore = (() => {
    let strip = null;
    let button = null;
    let menu = null;
    let tabs = [];
    let overflow = [];      // de onderdelen die nu in Meer staan (in volgorde van het menu)
    let frame = 0;
    let leaveTimer = 0;

    function init() {
        strip = document.querySelector('.top-nav .nav-tabs');
        button = document.getElementById('nav-more-btn');
        menu = document.getElementById('nav-more-menu');
        if (!strip || !button || !menu) return;
        tabs = [...strip.querySelectorAll('.nav-tab[data-view]')];

        button.addEventListener('click', (e) => {
            if (isOpen()) close(false);
            // Met de muis of het toetsenbord: het eerste onderdeel krijgt de focus (zoals elk menu)
            else open('first', e.detail === 0);
        });
        button.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                e.stopPropagation();
                open(e.key === 'ArrowUp' ? 'last' : 'first', true);
            }
        });
        menu.addEventListener('keydown', menuKey);
        menu.addEventListener('click', (e) => {
            const item = e.target.closest('.menu-item[data-more-view]');
            if (!item) return;
            close(true);
            switchMainView(item.dataset.moreView);
        });
        // Een klik ernaast sluit het menu (ook een klik op een ander onderdeel in de balk)
        document.addEventListener('pointerdown', (e) => {
            if (isOpen() && !menu.contains(e.target) && !button.contains(e.target)) close(false);
        }, true);
        window.addEventListener('blur', () => { if (isOpen()) close(false); });

        // Opnieuw passen bij elke maatwijziging van de balk (venster, thema-label, lettertype)
        if ('ResizeObserver' in window) new ResizeObserver(schedule).observe(strip);
        window.addEventListener('resize', schedule, { passive: true });
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule, () => {});
        layout();
    }

    function schedule() {
        if (frame) return;
        frame = requestAnimationFrame(() => { frame = 0; layout(); });
    }

    // Hoeveel onderdelen passen er, met minstens --space-xl lucht voor het zoekveld? Eerst alles
    // tonen en in één keer meten, dan in één keer schrijven (geen heen-en-weer van lezen en schrijven).
    function layout() {
        if (!strip) return;
        const wasOverflow = overflow.map(t => t.dataset.view).join(' ');
        tabs.forEach(t => t.classList.remove('is-overflow'));
        button.hidden = false;
        const style = getComputedStyle(strip);
        const gap = parseFloat(style.columnGap) || 0;
        const room = strip.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0)
            - Motion.px('--space-xl', 24);
        const widths = tabs.map(t => t.getBoundingClientRect().width);
        const moreWidth = button.getBoundingClientRect().width;

        const all = widths.reduce((sum, w) => sum + w, 0) + gap * (tabs.length - 1);
        let keep = tabs.length;
        if (all > room) {
            let used = moreWidth;
            keep = 0;
            while (keep < tabs.length && used + gap + widths[keep] <= room) used += gap + widths[keep++];
        }

        overflow = tabs.slice(keep);
        tabs.forEach((t, i) => t.classList.toggle('is-overflow', i >= keep));
        button.hidden = overflow.length === 0;
        if (overflow.map(t => t.dataset.view).join(' ') !== wasOverflow) {
            if (isOpen()) close(false);
            build();
        }
        sync(typeof currentMainView === 'function' ? currentMainView() : null);
    }

    // De onderdelen in Meer: icoon, naam en de sneltoets (uit shortcuts.js), het open onderdeel met een vinkje
    function build() {
        const keys = typeof SHORTCUT_VIEWS !== 'undefined' ? SHORTCUT_VIEWS : [];
        const items = overflow.map((tab, i) => {
            const view = tab.dataset.view;
            const shortcut = keys.find(k => k.view === view);
            const hint = shortcut ? (shortcut.key || `g ${shortcut.g}`) : '';
            const item = createEl('button', {
                type: 'button', class: 'menu-item nav-more-item', role: 'menuitem', tabindex: '-1',
                'data-more-view': view, title: tab.title, style: `--i: ${Math.min(i, 9)}`,
            },
            createEl('i', { class: `${tab.dataset.icon || 'fas fa-circle'} nav-more-icon`, 'aria-hidden': 'true' }),
            createEl('span', { class: 'nav-more-label', text: tab.textContent.trim() }),
            hint ? createEl('kbd', { class: 'nav-more-key', 'aria-hidden': 'true', text: hint }) : null,
            createEl('i', { class: 'fas fa-check menu-check', 'aria-hidden': 'true' }));
            if (shortcut) item.setAttribute('aria-keyshortcuts', shortcut.key || `g ${shortcut.g}`);
            return item;
        });
        menu.replaceChildren(...items);
    }

    // Meer is actief als het open onderdeel erin staat; het onderdeel zelf krijgt aria-current
    function sync(view) {
        if (!button) return;
        const inMore = overflow.find(t => t.dataset.view === view || (t.dataset.also || '').split(' ').includes(view));
        button.classList.toggle('active', !!inMore);
        button.setAttribute('aria-label', inMore ? `Meer onderdelen, nu open: ${inMore.textContent.trim()}` : 'Meer onderdelen');
        menu.querySelectorAll('.menu-item').forEach(item => {
            const on = !!inMore && item.dataset.moreView === inMore.dataset.view;
            item.classList.toggle('active', on);
            if (on) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
        });
        // Een wissel van onderdeel (sneltoets, Pasha) sluit een open menu
        if (isOpen() && view !== sync.lastView && sync.lastView !== undefined) close(false);
        sync.lastView = view;
    }

    function items() {
        return [...menu.querySelectorAll('.menu-item')];
    }

    function isOpen() {
        return !!menu && !menu.hidden;
    }

    function open(where = 'first', keyboard = false) {
        if (!overflow.length) return;
        clearTimeout(leaveTimer);
        menu.classList.remove('is-leaving');
        menu.inert = false;
        // Onder Meer, met de linkerrand gelijk aan de knop (binnen het venster)
        const nav = menu.parentElement;
        const navBox = nav.getBoundingClientRect();
        const box = button.getBoundingClientRect();
        menu.hidden = false;
        const width = menu.getBoundingClientRect().width;
        const gutter = Motion.px('--space-sm', 8);
        const left = Math.min(box.left, window.innerWidth - width - gutter) - navBox.left;
        menu.style.left = `${Math.max(gutter, Math.round(left))}px`;
        menu.classList.toggle('keyboard', keyboard);
        button.setAttribute('aria-expanded', 'true');
        const list = items();
        const current = list.find(i => i.classList.contains('active'));
        const target = where === 'last' ? list[list.length - 1] : (where === 'first' && current && !keyboard ? current : list[0]);
        if (target) target.focus();
    }

    function close(returnFocus) {
        if (!isOpen()) return;
        button.setAttribute('aria-expanded', 'false');
        menu.hidden = true;
        // Vervagen (niet klikbaar, niet in de tabvolgorde) in --dur-3; in Uit meteen weg
        const ms = Motion.ms('--dur-3', 200);
        if (ms > 0) {
            menu.classList.add('is-leaving');
            menu.inert = true;
            clearTimeout(leaveTimer);
            leaveTimer = setTimeout(() => { menu.classList.remove('is-leaving'); menu.inert = false; }, ms + 40);
        }
        if (returnFocus) button.focus();
    }

    function menuKey(e) {
        const list = items();
        const at = list.indexOf(document.activeElement);
        const go = (i) => { menu.classList.add('keyboard'); list[(i + list.length) % list.length].focus(); };
        if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();
            close(true);
        } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Home' || e.key === 'End') {
            // Deze toetsen zijn voor het menu, niet voor sneltoetsen of de pagina erachter
            e.preventDefault();
            e.stopPropagation();
            if (!list.length) return;
            if (e.key === 'Home') go(0);
            else if (e.key === 'End') go(list.length - 1);
            else go(at + (e.key === 'ArrowDown' ? 1 : -1));
        } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            e.preventDefault();
            e.stopPropagation();
        } else if (e.key === 'Tab') {
            close(false);
        } else if (e.key.length === 1 && /\S/.test(e.key) && !e.metaKey && !e.ctrlKey && !e.altKey) {
            // Een beginletter springt naar het volgende onderdeel met die letter (geen sneltoets hier)
            e.preventDefault();
            e.stopPropagation();
            const letter = e.key.toLowerCase();
            for (let step = 1; step <= list.length; step++) {
                const item = list[(at + step) % list.length];
                if (item.textContent.trim().toLowerCase().startsWith(letter)) { go((at + step) % list.length); break; }
            }
        }
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();

    return { layout, sync, open, close, isOpen, get overflow() { return overflow.map(t => t.dataset.view); } };
})();
window.NavMore = NavMore;
