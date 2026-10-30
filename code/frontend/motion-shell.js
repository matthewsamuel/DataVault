// DataVault Dashboard - Beweging van de schil (motion-shell.js)
//
// De bewegingstaal staat in docs/MOTION.md en het gereedschap in motion.js (window.Motion).
// Dit bestand geeft de schil van de app zijn beweging: alles wat op elk scherm terugkomt.
//
//   Menu         een zachte vlek glijdt onder de muis van onderdeel naar onderdeel; de groene
//                streep rekt elastisch mee naar het nieuwe onderdeel (navGlide)
//   Pagina's     elk onderdeel komt binnen vanuit de richting van het menu; het icoon van de
//                paginatitel zet zich neer, lange lijsten komen in ritme (enterView)
//   Home         de begroeting rijst letter voor letter op, de tegels tellen hun getal op, de
//                opslagbalk vult zich en 'Op deze dag' opent als een gordijn (homeIntro)
//   Takenpaneel  de balk groeit vloeiend tussen twee peilingen, een glans loopt mee zolang een
//                taak bezig is, en klaar is een klein feestje (jobDockBefore / jobDockAfter)
//   Meldingen    wegvegen naar rechts met de muis of een vinger
//   Vensters     groeien uit de knop of de plek waar je klikte, en krimpen daar weer heen
//   Menu's       het themamenu en .menu veren open en vervagen weg
//   Thema        het pictogram draait een halve slag mee met de cirkel van het nieuwe thema
//   Segmenten    het gekozen segment van elke .segmented en .view-toggle glijdt naar zijn plek
//   Sneltoetsen  in het overzicht licht de toets op die je indrukt
//   Pasha        berichten rijzen op, Pasha knikt als zijn antwoord er is
//   Leeg, fout   lege en mislukte staten komen vriendelijk binnen (één keer, niet bij elke
//                peiling opnieuw); de skeletglans loopt via transform
//
// Alles is stand-bewust (Volledig, Rustig, Uit) doordat het via Motion of de tokens loopt, en
// niets houdt invoer tegen: animaties zijn alleen weergave, de DOM is meteen in zijn eindstand.
// Laadt als laatste script, na app.js en de onderdelen die het aanroept.

(function () {
    'use strict';

    const M = window.Motion;
    if (!M) return;

    const settled = () => ({ finished: Promise.resolve(), cancel() {}, finish() {} });
    const reduceAll = list => ({
        finished: Promise.all(list.map(a => (a && a.finished ? a.finished.catch(() => {}) : null))),
        cancel() { list.forEach(a => a && a.cancel && a.cancel()); },
        finish() { list.forEach(a => { try { a && a.finish && a.finish(); } catch (_) { /* klaar */ } }); },
    });
    const inView = (el) => {
        const r = el.getBoundingClientRect();
        return r.bottom > 0 && r.top < window.innerHeight && r.width > 0;
    };

    // Waar de gebruiker het laatst klikte en wat buiten een venster het laatst focus had: daar
    // groeit een venster uit (en krimpt het naartoe terug)
    const lastPointer = { x: 0, y: 0, t: 0, target: null };
    let lastFocusOutside = null;
    document.addEventListener('pointerdown', (e) => {
        lastPointer.x = e.clientX;
        lastPointer.y = e.clientY;
        lastPointer.t = performance.now();
        lastPointer.target = e.target;
    }, true);
    document.addEventListener('focusin', (e) => {
        if (e.target && e.target.closest && !e.target.closest('.modal, .dv-overlay')) lastFocusOutside = e.target;
    }, true);

    // ==================== 1. MENU: ZWEVENDE VLEK EN ELASTISCHE STREEP ====================

    // De groene streep rekt eerst uit naar het nieuwe onderdeel (de voorkant loopt voor, de
    // achterkant volgt) en trekt zich daar samen. Een eigen versie van Motion.glide met dezelfde
    // geometrie; in Rustig en Uit staat hij meteen op zijn plek.
    function navGlide(fromEl, toEl, opts = {}) {
        if (!fromEl || !toEl || fromEl === toEl || !M.full()) return settled();
        const inset = opts.inset || (() => 0);
        const box = (el) => {
            const r = el.getBoundingClientRect();
            const d = inset(el);
            return { left: r.left + d, right: r.right - d };
        };
        const a = box(fromEl), b = box(toEl);
        const bw = b.right - b.left;
        if (bw <= 1 || a.right - a.left <= 1) return settled();
        const bc = (b.left + b.right) / 2;
        const lead = t => 1 - Math.pow(1 - t, 3.2);                       // de voorkant: snel weg
        const trail = t => lead(Math.max(0, (t - 0.16) / 0.84));          // de achterkant: volgt
        const right = b.left > a.left;
        const frames = [];
        const N = 14;
        for (let i = 0; i <= N; i++) {
            const t = i / N;
            const front = right ? a.right + (b.right - a.right) * lead(t) : a.left + (b.left - a.left) * lead(t);
            const back = right ? a.left + (b.left - a.left) * trail(t) : a.right + (b.right - a.right) * trail(t);
            const l = Math.min(front, back), r = Math.max(front, back);
            const c = (l + r) / 2;
            frames.push({ transform: `translateX(${(c - bc).toFixed(2)}px) scaleX(${Math.max(0.01, (r - l) / bw).toFixed(4)})`, offset: t });
        }
        frames[N].transform = 'none';
        return M.animate(toEl, frames, {
            duration: M.ms('--spring-smooth-dur', 520),
            easing: 'linear',
            pseudoElement: '::after',
            key: 'glide',
            calm: false,
        });
    }

    // Een zachte vlek onder de muis die van onderdeel naar onderdeel glijdt (clip-path: de ronde
    // hoeken blijven kloppen, wat de breedte ook is). In Rustig verschijnt hij op zijn plek.
    function initNavHover() {
        const strip = document.querySelector('.top-nav .nav-tabs');
        if (!strip) return;
        const pill = document.createElement('span');
        pill.className = 'nav-hover-pill';
        pill.setAttribute('aria-hidden', 'true');
        strip.prepend(pill);
        strip.classList.add('has-hover-pill');
        let shown = false;
        let current = null;
        const clipFor = (tab) => {
            const width = strip.scrollWidth;
            pill.style.width = `${width}px`;
            const left = tab.offsetLeft;
            const right = width - (tab.offsetLeft + tab.offsetWidth);
            return `inset(0px ${right}px 0px ${left}px round ${M.px('--radius-sm', 6)}px)`;
        };
        const show = (tab) => {
            if (tab === current) return;
            const to = clipFor(tab);
            const from = current && shown ? getComputedStyle(pill).clipPath : null;
            current = tab;
            pill.style.clipPath = to;
            if (!shown) {
                shown = true;
                pill.classList.add('is-shown');
                M.animate(pill, [{ opacity: 0, transform: 'scale(0.96)' }, { opacity: 1, transform: 'none' }],
                    { duration: '--dur-2', easing: '--ease-out', key: 'pill-fade' });
                return;
            }
            if (M.full() && from && from !== 'none') {
                M.animate(pill, [{ clipPath: from }, { clipPath: to }], { spring: 'snappy', key: 'pill-move' });
            }
        };
        const hide = () => {
            if (!shown) return;
            shown = false;
            current = null;
            pill.classList.remove('is-shown');
            M.animate(pill, [{ opacity: 1 }, { opacity: 0 }], { duration: '--dur-3', easing: '--ease-exit', key: 'pill-fade' });
        };
        strip.addEventListener('pointerover', (e) => {
            if (e.pointerType === 'touch') return;
            const tab = e.target.closest && e.target.closest('.nav-tab');
            if (tab && strip.contains(tab)) show(tab);
        });
        strip.addEventListener('pointerleave', hide);
        strip.addEventListener('scroll', hide, { passive: true });
        window.addEventListener('resize', hide, { passive: true });
    }

    // ==================== 2. PAGINA'S ====================

    // Blokken die de schil zelf in ritme laat binnenkomen (Motion.enterPage slaat ze over)
    const OWN_RHYTHM = ['#settings-sections', '#recovery-list', '.home-stats-grid', '.home-dynamic-widgets'];

    // Een onderdeel komt binnen. direction: 1 van rechts, -1 van links, 0 van onder.
    function enterView(view, section, direction = 0) {
        if (!section) return settled();
        const anims = [M.enterPage(section, { direction })];
        if (!M.full()) {
            if (view === 'home') homeArrive(0);
            return reduceAll(anims);
        }
        // Het icoon van de paginatitel zet zich neer: een kleine draai en schaal, op de veer
        const icon = section.querySelector('.page-header .page-title > i, .page-title > i');
        if (icon && inView(icon)) {
            anims.push(M.animate(icon, [
                { opacity: 0, transform: `scale(0.6) rotate(${direction >= 0 ? -14 : 14}deg)` },
                { opacity: 1, transform: 'none' },
            ], { spring: 'smooth', delay: M.ms('--stagger-step', 28) * 2, key: 'title-icon' }));
        }
        if (view === 'home') anims.push(homeArrive(direction));
        else if (view === 'settings') anims.push(settingsArrive(direction));
        else if (view === 'recovery') anims.push(listArrive(document.getElementById('recovery-list'), '.recovery-entry', direction));
        return reduceAll(anims);
    }

    // Een lijst die binnenkomt: wat in beeld staat in ritme vanuit de richting, de rest pas als
    // het in beeld scrolt
    function listArrive(list, selector, direction = 0, baseDelay = 2) {
        if (!list) return settled();
        const items = [...list.querySelectorAll(`:scope > ${selector}`)];
        if (!items.length) return settled();
        const visible = items.filter(inView);
        const rest = items.filter(el => !visible.includes(el));
        const d = M.px('--motion-distance-md', 12);
        const preset = () => direction
            ? [{ opacity: 0, transform: `translate3d(${direction * d * 1.5}px, ${d / 2}px, 0)` }, { opacity: 1, transform: 'none' }]
            : M.presets.rise(d);
        const anim = M.stagger(visible, preset, {
            spring: 'smooth', key: 'page', delay: M.ms('--stagger-step', 28) * baseDelay,
        });
        if (rest.length) M.reveal(rest);
        return anim;
    }

    function settingsArrive(direction) {
        return listArrive(document.getElementById('settings-sections'), '.settings-section', direction);
    }

    // ==================== 3. HOME ====================

    const counted = new WeakSet();     // tegels die deze sessie al hebben opgeteld

    function homeVisible() {
        const home = document.getElementById('section-home');
        return !!home && home.classList.contains('active') && !document.hidden;
    }

    // De begroeting rijst letter voor letter uit een masker op. Schermlezers lezen het woord
    // (aria-label); na de animatie staat er weer gewoon tekst.
    function greetingReveal(delay = 0) {
        const title = document.getElementById('home-greeting');
        if (!title || !M.full()) return settled();
        const text = title.textContent;
        if (!text || text.length > 40) return M.enter(title, 'rise', { delay });
        title.setAttribute('aria-label', text);
        const letters = [...text].map(ch => {
            const span = document.createElement('span');
            span.className = 'hero-letter';
            span.setAttribute('aria-hidden', 'true');
            span.textContent = ch === ' ' ? ' ' : ch;
            return span;
        });
        const mask = document.createElement('span');
        mask.className = 'hero-mask';
        mask.setAttribute('aria-hidden', 'true');
        mask.append(...letters);
        title.replaceChildren(mask);
        const step = Math.max(14, Math.min(26, 260 / letters.length));
        const anims = letters.map((span, i) => M.animate(span, [
            { transform: 'translate3d(0, 105%, 0) rotate(6deg)', opacity: 0 },
            { opacity: 1, offset: 0.25 },
            { transform: 'none', opacity: 1 },
        ], { spring: 'smooth', delay: delay + i * step, key: 'hero' }));
        const done = reduceAll(anims);
        done.finished.then(() => {
            // Alleen terugzetten als niemand de tekst intussen veranderde
            if (title.firstChild === mask) { title.textContent = text; title.removeAttribute('aria-label'); }
        });
        return done;
    }

    // Een getal op een tegel: de eerste keer dat Home in beeld is telt het op vanaf nul, daarna
    // alleen als het verandert (vanaf het oude getal). format: eigen opmaak van het eindgetal.
    function statValue(el, value, opts = {}) {
        if (!el) return;
        const n = Number(value || 0);
        const fmt = opts.format || (v => Math.round(v).toLocaleString('nl-NL'));
        el._shellTarget = n;
        el._shellFormat = fmt;
        el._shellCountFormat = opts.countFormat || fmt;
        if (!counted.has(el)) {
            if (homeVisible() && M.full()) { counted.add(el); countFrom(el, 0, n); return; }
            el.textContent = fmt(n);        // nog niet gezien: tellen zodra Home in beeld komt
            el._motionValue = n;
            return;
        }
        if (el._motionValue !== undefined && el._motionValue !== n && homeVisible()) countFrom(el, el._motionValue, n);
        else { el.textContent = fmt(n); el._motionValue = n; }
    }

    function countFrom(el, from, to) {
        const anim = M.countUp(el, to, { from, format: el._shellCountFormat });
        anim.finished.then(() => { if (el._motionValue === to) el.textContent = el._shellFormat(to); });
        return anim;
    }

    // Tegels die nog niet geteld hebben (de cijfers kwamen binnen terwijl Home verborgen was)
    function countPendingStats(delay) {
        document.querySelectorAll('#section-home .stat-tile-value').forEach(el => {
            if (el._shellTarget === undefined || counted.has(el) || el.classList.contains('is-empty')) return;
            counted.add(el);
            if (!M.full()) return;
            el.textContent = el._shellCountFormat(0);
            setTimeout(() => countFrom(el, 0, el._shellTarget), delay);
        });
    }

    // De opslagbalk vult zich vanaf links (clip-path: de ronde kop blijft rond), één keer per
    // sessie: zodra hij er is en Home in beeld is
    function storageFill(bar, delay = 0) {
        if (!bar || bar._shellFilled || !bar.getClientRects().length || !homeVisible()) return settled();
        bar._shellFilled = true;
        if (!M.full()) return settled();
        return M.animate(bar, [{ clipPath: 'inset(0 100% 0 0 round 999px)' }, { clipPath: 'inset(0 0% 0 0 round 999px)' }],
            { spring: 'gentle', delay, key: 'fill' });
    }

    // Het opslaggetal telt in GB (of TB) op en eindigt in de gewone opmaak
    function storageValue(el, usedGb, format) {
        const tb = usedGb >= 1024;
        statValue(el, usedGb, {
            format: () => format(usedGb),
            countFormat: n => (tb ? `${(n / 1024).toLocaleString('nl-NL', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} TB`
                : `${Math.round(n).toLocaleString('nl-NL')} GB`),
        });
    }

    function homeArrive(direction) {
        const step = M.ms('--stagger-step', 28);
        const anims = [];
        // De vier tegels in ritme, vanuit de richting van het menu
        const tiles = [...document.querySelectorAll('#section-home .home-stats-grid > .stat-tile')].filter(inView);
        const widgets = [...document.querySelectorAll('#section-home .home-dynamic-widgets > .dynamic-widget')].filter(inView);
        if (M.full()) {
            const d = M.px('--motion-distance-md', 12);
            const preset = () => direction
                ? [{ opacity: 0, transform: `translate3d(${direction * d * 1.5}px, 0, 0)` }, { opacity: 1, transform: 'none' }]
                : [{ opacity: 0, transform: `translate3d(0, ${d * 1.5}px, 0) scale(0.985)` }, { opacity: 1, transform: 'none' }];
            anims.push(M.stagger(widgets, preset, { spring: 'smooth', key: 'page', delay: step * 3 }));
            anims.push(M.stagger(tiles, preset, { spring: 'smooth', key: 'page', delay: step * 5 }));
            anims.push(storageFill(document.getElementById('storage-bar-fill'), step * 9));
        } else {
            anims.push(M.stagger([...widgets, ...tiles], 'fade'));
        }
        countPendingStats(step * 6);
        return reduceAll(anims);
    }

    // De eerste indruk bij het openen van de app: het menu valt zacht op zijn plek, de datum en
    // de begroeting rijzen op, daarna de kaarten en de tegels
    function homeIntro() {
        const home = document.getElementById('section-home');
        if (!home || M.off()) return settled();
        if (!M.full()) return reduceAll([M.enterPage(home), homeArrive(0)]);
        const step = M.ms('--stagger-step', 28);
        const navItems = [
            document.querySelector('.top-nav .nav-brand'),
            ...document.querySelectorAll('.top-nav .nav-tab'),
            ...document.querySelectorAll('.top-nav .nav-actions > *'),
        ].filter(Boolean);
        const anims = [M.stagger(navItems, () => M.presets.sink(M.px('--motion-distance-sm', 6)), { spring: 'smooth', key: 'intro', step: step * 0.6, max: 16 })];
        // De groene streep tekent zich vanuit het midden
        const activeTab = document.querySelector('.top-nav .nav-tab.active');
        if (activeTab) {
            anims.push(M.animate(activeTab, [{ transform: 'scaleX(0)' }, { transform: 'none' }],
                { spring: 'smooth', pseudoElement: '::after', delay: step * 8, key: 'glide' }));
        }
        // De hero: datum, begroeting letter voor letter, ondertitel
        anims.push(M.enter(document.getElementById('home-date'), 'rise', { delay: step * 2, key: 'intro' }));
        anims.push(greetingReveal(step * 4));
        anims.push(M.enter(document.querySelector('.home-subtitle'), 'rise', { delay: step * 10, key: 'intro' }));
        anims.push(homeArrive(0));
        const chat = document.querySelector('#section-home .chat-widget');
        if (chat && inView(chat)) anims.push(M.enter(chat, 'rise', { delay: step * 12, key: 'intro' }));
        return reduceAll(anims);
    }

    // 'Op deze dag' verschijnt: een foto opent als een gordijn van onder naar boven en zet zich
    // neer, zijn titel rijst op; een nummer springt met zijn hoes naar voren
    function otdShow(content) {
        if (!content || M.off()) return;
        const image = content.querySelector('.otd-large-image.active');
        const step = M.ms('--stagger-step', 28);
        if (image) {
            if (M.full()) {
                M.animate(image, [
                    { clipPath: 'inset(100% 0 0 0)', transform: 'scale(1.06)' },
                    { clipPath: 'inset(0% 0 0 0)', transform: 'none' },
                ], { spring: 'gentle', key: 'otd-reveal', duration: M.ms('--dur-6', 640) * 1.4 });
            } else {
                M.animate(image, M.presets.fade(), { duration: '--dur-3', key: 'otd-reveal' });
            }
            const overlay = image.nextElementSibling;
            if (overlay && overlay.classList.contains('otd-large-overlay')) otdText(overlay, step * 8);
            return;
        }
        const item = content.querySelector('.otd-item');
        if (item) {
            const cover = item.querySelector('.otd-cover');
            if (cover) M.animate(cover, [{ opacity: 0, transform: 'scale(0.82) rotate(-4deg)' }, { opacity: 1, transform: 'none' }], { spring: 'smooth', key: 'otd-reveal' });
            M.stagger([...item.children].filter(el => el !== cover), 'rise', { spring: 'smooth', delay: step * 4, key: 'otd-reveal' });
        }
    }

    // De titel en het jaar van een foto rijzen op (ook bij het wisselen naar de volgende foto)
    function otdText(overlay, delay = 0) {
        if (!overlay) return;
        M.stagger([...overlay.children], () => M.presets.rise(M.px('--motion-distance-sm', 6) * 1.5),
            { spring: 'smooth', delay, key: 'otd-text' });
    }

    // ==================== 4. TAKENPANEEL ====================

    const JOB_ACTIVE_CLASSES = ['job-running', 'job-queued', 'job-waiting_for_vault'];
    const SHEEN_MS = 2200;      // één glans over een lopende balk (een voortgangslus mag doorlopen)
    const INDET_MS = 1200;      // .job-bar.indeterminate (style.css)
    let dockSeen = false;

    // Vóór het opnieuw tekenen: hoe ver elke balk nu zichtbaar is, en wat er stond
    function jobDockBefore(dock) {
        const state = { hidden: !dock || dock.hidden, bars: new Map(), status: new Map(), tiny: null };
        if (!dock || dock.hidden) return state;
        dock.querySelectorAll('.job-item[data-id]').forEach(item => {
            const id = item.dataset.id;
            const track = item.querySelector('.job-progress');
            const bar = item.querySelector('.job-bar');
            state.status.set(id, item.className);
            if (track && bar && !bar.classList.contains('indeterminate')) {
                const tw = track.getBoundingClientRect().width;
                if (tw > 0) state.bars.set(id, bar.getBoundingClientRect().width / tw);
            }
        });
        const tiny = dock.classList.contains('tiny') && dock.querySelector('.job-dock-title');
        state.tiny = tiny ? tiny.textContent : null;
        return state;
    }

    function jobDockAfter(dock, before) {
        if (!dock) return;
        const first = !dockSeen;
        dockSeen = true;
        if (dock.hidden) return;
        // Het paneel verschijnt: het rijst rechtsonder op
        if (before.hidden && !first) {
            M.animate(dock, [{ opacity: 0, transform: `translate3d(0, ${M.px('--motion-distance-lg', 24)}px, 0) scale(0.97)` }, { opacity: 1, transform: 'none' }],
                { spring: 'smooth', key: 'dock' });
        }
        const now = performance.now();
        dock.querySelectorAll('.job-item[data-id]').forEach(item => {
            const id = item.dataset.id;
            const bar = item.querySelector('.job-bar');
            // De lussen (glans, onbepaald) lopen door alsof er niets opnieuw getekend is
            if (bar) {
                bar.style.setProperty('--job-sheen-delay', `${-(now % SHEEN_MS)}ms`);
                if (bar.classList.contains('indeterminate')) bar.style.animationDelay = `${-(now % INDET_MS)}ms`;
            }
            const was = before.status.get(id);
            if (!was) {
                if (!before.hidden && !first) M.enter(item, 'rise', { args: [M.px('--motion-distance-sm', 6)], key: 'job-in' });
            } else if (JOB_ACTIVE_CLASSES.some(c => was.split(' ').includes(c)) && item.classList.contains('job-done')) {
                jobCelebrate(item);
            }
            // De balk groeit van waar hij zichtbaar was naar de nieuwe stand
            const from = before.bars.get(id);
            if (bar && from !== undefined && !bar.classList.contains('indeterminate')) {
                const track = item.querySelector('.job-progress');
                const tw = track ? track.getBoundingClientRect().width : 0;
                const to = tw ? bar.getBoundingClientRect().width / tw : 0;
                if (to > 0 && Math.abs(to - from) > 0.002) {
                    M.animate(bar, [{ transform: `scaleX(${Math.max(0, from / to).toFixed(4)})` }, { transform: 'none' }],
                        { duration: '--dur-6', easing: '--ease-standard', key: 'progress', calm: false });
                }
            }
        });
        // Het getal op het kleine icoon (afgeronde taken) springt als het verandert
        const tiny = dock.classList.contains('tiny') && dock.querySelector('.job-dock-title');
        if (tiny && before.tiny !== null && before.tiny !== tiny.textContent) {
            M.animate(tiny, M.presets.pop(), { spring: 'bouncy', key: 'tiny' });
        }
    }

    // Klaar: het bolletje wordt een groen vinkje dat erin springt, met een kring die uitdijt en
    // een handvol vonkjes; een beloning, dus de bouncy-veer (één keer per echte afronding)
    function jobCelebrate(item) {
        item.classList.add('is-celebrating');
        if (!M.full()) return;
        const status = item.querySelector('.job-status');
        if (status) M.animate(status, M.presets.pop(), { spring: 'bouncy', key: 'celebrate' });
        const r = (status || item).getBoundingClientRect();
        const burst = document.createElement('div');
        burst.className = 'job-burst';
        burst.setAttribute('aria-hidden', 'true');
        burst.style.left = `${r.left + 3}px`;
        burst.style.top = `${r.top + r.height / 2}px`;
        const ring = document.createElement('span');
        ring.className = 'job-burst-ring';
        burst.appendChild(ring);
        const sparks = [];
        for (let i = 0; i < 8; i++) {
            const spark = document.createElement('span');
            spark.className = 'job-burst-spark';
            burst.appendChild(spark);
            sparks.push(spark);
        }
        document.body.appendChild(burst);
        const dist = M.px('--motion-distance-lg', 24);
        const anims = [M.animate(ring, [{ opacity: 0.7, transform: 'scale(0.4)' }, { opacity: 0, transform: 'scale(3.2)' }],
            { duration: '--dur-5', easing: '--ease-emphasized', fill: 'forwards', key: 'burst' })];
        sparks.forEach((spark, i) => {
            const angle = (i / sparks.length) * Math.PI * 2 + 0.3;
            const reach = dist * (0.75 + (i % 3) * 0.2);
            anims.push(M.animate(spark, [
                { opacity: 1, transform: 'translate3d(0, 0, 0) scale(1)' },
                { opacity: 0, transform: `translate3d(${Math.cos(angle) * reach}px, ${Math.sin(angle) * reach}px, 0) scale(0.3)` },
            ], { duration: '--dur-5', easing: '--ease-emphasized', fill: 'forwards', delay: (i % 2) * 30, key: 'burst' }));
        });
        reduceAll(anims).finished.then(() => burst.remove());
        setTimeout(() => burst.remove(), 2000);
    }

    // ==================== 5. MELDINGEN: WEGVEGEN ====================

    function initToastSwipe() {
        const container = document.getElementById('toast-container');
        if (!container) return;
        container.addEventListener('pointerdown', (e) => {
            const toast = e.target.closest('.toast');
            if (!toast || toast._leaving || e.button !== 0 || e.target.closest('button, a, input')) return;
            const startX = e.clientX;
            let dx = 0, lastX = startX, lastT = performance.now(), velocity = 0, dragging = false;
            const move = (ev) => {
                const x = ev.clientX;
                const t = performance.now();
                velocity = (x - lastX) / Math.max(1, t - lastT);
                lastX = x; lastT = t;
                dx = x - startX;
                if (!dragging && Math.abs(dx) < 4) return;
                if (!dragging) {
                    dragging = true;
                    toast.classList.add('is-dragging');
                    try { toast.setPointerCapture(ev.pointerId); } catch (_) { /* al los */ }
                }
                // Naar links geeft hij nauwelijks mee (rubberband), naar rechts volgt hij de vinger
                const shown = dx > 0 ? dx : dx / 6;
                toast.style.transform = `translate3d(${shown}px, 0, 0)`;
                toast.style.opacity = String(Math.max(0.35, 1 - Math.max(0, dx) / 260));
            };
            const up = () => {
                document.removeEventListener('pointermove', move);
                document.removeEventListener('pointerup', up);
                document.removeEventListener('pointercancel', up);
                if (!dragging) return;
                toast.classList.remove('is-dragging');
                const current = toast.style.transform;
                toast.style.transform = '';
                toast.style.opacity = '';
                if (dx > 90 || (dx > 24 && velocity > 0.5)) {
                    toastFling(toast, current);
                } else {
                    M.animate(toast, [{ transform: current, opacity: 1 }, { transform: 'none', opacity: 1 }], { spring: 'snappy', key: 'swipe' });
                }
            };
            document.addEventListener('pointermove', move);
            document.addEventListener('pointerup', up);
            document.addEventListener('pointercancel', up);
        });
    }

    // Weggeveegd: hij vliegt door in de richting van de veeg, de rest schuift op (zoals hideToast)
    function toastFling(toast, from) {
        toast._leaving = true;
        clearTimeout(toast._hideTimer);
        const container = toast.parentElement;
        const away = window.innerWidth;
        const done = () => {
            const others = container ? [...container.children].filter(t => t !== toast) : [];
            M.flip(others, () => toast.remove());
        };
        const anim = M.animate(toast, [{ transform: from || 'none', opacity: 0.6 }, { transform: `translate3d(${away * 0.4}px, 0, 0)`, opacity: 0 }],
            { duration: '--dur-3', easing: '--ease-exit', fill: 'forwards', key: 'swipe' });
        if (anim.stub) { toast.remove(); return; }
        anim.finished.then(done, () => toast.remove());
    }

    // ==================== 6. VENSTERS: UIT DE KNOP GEGROEID ====================

    const PANEL = ':scope > .modal-container, :scope > .move-modal-container, :scope > .dv-overlay-panel';

    // Waar het venster vandaan komt: de plek van de klik (als die net was), anders de knop met de
    // focus. Het venster schaalt vanaf daar (transform-origin), dus het groeit uit de knop en
    // krimpt er bij sluiten naartoe terug.
    function overlayOrigin(overlay) {
        // De viewer heeft zijn eigen held: de foto die uit zijn miniatuur groeit
        if (overlay.id === 'modal-viewer') return;
        const panel = overlay.querySelector(PANEL);
        if (!panel) return;
        let x, y;
        const recent = performance.now() - lastPointer.t < 900;
        if (recent && !(lastPointer.target && lastPointer.target.closest && lastPointer.target.closest('[data-motion-key]'))) {
            x = lastPointer.x; y = lastPointer.y;
        } else if (lastFocusOutside && lastFocusOutside.isConnected && lastFocusOutside !== document.body) {
            const r = lastFocusOutside.getBoundingClientRect();
            if (!r.width) return;
            x = r.left + r.width / 2; y = r.top + r.height / 2;
        } else {
            panel.style.transformOrigin = '';
            return;
        }
        const p = panel.getBoundingClientRect();
        if (!p.width) return;
        // Niet verder dan een halve paneelmaat buiten het paneel: dan blijft het een duw, geen vlucht
        const cx = Math.max(-p.width / 2, Math.min(p.width * 1.5, x - p.left));
        const cy = Math.max(-p.height / 2, Math.min(p.height * 1.5, y - p.top));
        panel.style.transformOrigin = `${Math.round(cx)}px ${Math.round(cy)}px`;
    }

    // Het themamenu: openen veert (CSS), sluiten vervaagt even in plaats van te verdwijnen
    function menuLeaving(menu) {
        if (M.off()) return;
        menu.classList.add('is-leaving');
        clearTimeout(menu._shellLeave);
        menu._shellLeave = setTimeout(() => menu.classList.remove('is-leaving'), M.ms('--dur-3', 200) + 40);
    }

    function watchOverlaysAndMenus() {
        const observer = new MutationObserver(records => {
            for (const r of records) {
                const el = r.target;
                if (!el.classList) continue;
                const was = (r.oldValue || '').split(/\s+/);
                if (el.matches('.modal, .dv-overlay')) {
                    if (el.classList.contains('active') && !was.includes('active')) overlayOrigin(el);
                } else if (el.id === 'theme-dropdown') {
                    if (was.includes('open') && !el.classList.contains('open')) menuLeaving(el);
                    // (alleen weghalen als hij er is: elke classList-wijziging is weer een melding)
                    if (el.classList.contains('open') && el.classList.contains('is-leaving')) {
                        clearTimeout(el._shellLeave);
                        el.classList.remove('is-leaving');
                    }
                }
            }
        });
        observer.observe(document.body, { attributes: true, attributeFilter: ['class'], attributeOldValue: true, subtree: true });
    }

    // ==================== 7. THEMA ====================

    // Het pictogram (half licht, half donker) draait een halve slag terwijl het nieuwe thema
    // als cirkel uitloopt
    function themeChanged() {
        // De cirkel eindigt net voorbij de verste hoek, zodat hij de hele duur zichtbaar loopt
        const root = document.documentElement;
        const x = parseFloat(root.style.getPropertyValue('--vt-x'));
        const y = parseFloat(root.style.getPropertyValue('--vt-y'));
        if (Number.isFinite(x) && Number.isFinite(y)) {
            const far = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
            root.style.setProperty('--shell-reveal-end', `${Math.ceil(far)}px`);
        }
        const icon = document.querySelector('#theme-button > i:first-child');
        if (!icon || !M.full()) return;
        icon._shellTurn = (icon._shellTurn || 0) + 180;
        M.animate(icon, [{ transform: `rotate(${icon._shellTurn - 180}deg)` }, { transform: `rotate(${icon._shellTurn}deg)` }],
            { spring: 'smooth', fill: 'forwards', key: 'theme-turn' });
    }

    // ==================== 8. SEGMENTEN ====================

    const SEGMENTED = '.segmented, .view-toggle';
    const segActive = (group) => [...group.children].find(el =>
        el.classList.contains('active') || el.getAttribute('aria-pressed') === 'true'
        || (el.matches('label') && el.querySelector('input:checked')));

    // De gekozen achtergrond glijdt van het oude naar het nieuwe segment. Een los vlak (thumb)
    // doet de reis; het nieuwe segment toont zijn eigen achtergrond pas als het vlak er is.
    function segGlide(group, fromEl) {
        const toEl = segActive(group);
        if (!toEl || !fromEl || toEl === fromEl || !fromEl.isConnected || !M.full()) return;
        const g = group.getBoundingClientRect();
        const a = fromEl.getBoundingClientRect();
        const b = toEl.getBoundingClientRect();
        if (!a.width || !b.width) return;
        let thumb = group.querySelector(':scope > .seg-thumb');
        if (!thumb) {
            thumb = document.createElement('span');
            thumb.className = 'seg-thumb';
            thumb.setAttribute('aria-hidden', 'true');
            group.prepend(thumb);
        }
        const left = b.left - g.left - group.clientLeft;
        const top = b.top - g.top - group.clientTop;
        Object.assign(thumb.style, { left: `${left}px`, top: `${top}px`, width: `${b.width}px`, height: `${b.height}px` });
        group.classList.add('is-gliding');
        const anim = M.animate(thumb, [
            { transform: `translate3d(${a.left - b.left}px, ${a.top - b.top}px, 0) scale(${a.width / b.width}, ${a.height / b.height})`, opacity: 1 },
            { transform: 'none', opacity: 1 },
        ], { spring: 'snappy', key: 'seg', fill: 'both' });
        const end = () => { if (group._shellSeg === anim) { group.classList.remove('is-gliding'); thumb.remove(); } };
        group._shellSeg = anim;
        anim.finished.then(end, end);
    }

    function initSegmented() {
        const remember = (e) => {
            const group = e.target.closest && e.target.closest(SEGMENTED);
            if (group) group._shellFrom = segActive(group);
        };
        document.addEventListener('pointerdown', remember, true);
        document.addEventListener('keydown', (e) => {
            if (e.key.startsWith('Arrow') || e.key === ' ' || e.key === 'Enter') remember(e);
        }, true);
        const after = (e) => {
            const group = e.target.closest && e.target.closest(SEGMENTED);
            if (!group || !group._shellFrom) return;
            const from = group._shellFrom;
            group._shellFrom = null;
            // Na de eigen klik-afhandeling van de app (die zet .active of het keuzerondje)
            requestAnimationFrame(() => segGlide(group, from));
        };
        document.addEventListener('click', after);
        document.addEventListener('change', after);
    }

    // ==================== 9. SNELTOETSEN: DE TOETS LICHT OP ====================

    const KEY_LABELS = { ArrowLeft: '←', ArrowRight: '→', ArrowUp: '↑', ArrowDown: '↓', Escape: 'Esc', ' ': 'Spatie', Enter: 'Enter', Delete: 'Delete', Backspace: '⌫' };

    function initShortcutEcho() {
        document.addEventListener('keydown', (e) => {
            const help = document.getElementById('shortcut-help');
            if (!help || !help.classList.contains('active') || e.metaKey || e.ctrlKey || e.altKey) return;
            const label = KEY_LABELS[e.key] || e.key;
            const wanted = label.toLowerCase();
            help.querySelectorAll('kbd').forEach(kbd => {
                const text = kbd.textContent.trim().toLowerCase();
                if (text !== wanted) return;
                kbd.classList.remove('is-pressed');
                void kbd.offsetWidth;   // opnieuw afspelen bij een herhaalde toets
                kbd.classList.add('is-pressed');
                clearTimeout(kbd._shellPress);
                kbd._shellPress = setTimeout(() => kbd.classList.remove('is-pressed'), M.ms('--dur-5', 420) + 80);
                M.animate(kbd, [{ transform: 'translate3d(0, 1px, 0) scale(0.9)' }, { transform: 'none' }], { spring: 'bouncy', key: 'press' });
            });
        });
    }

    // ==================== 10. PASHA ====================

    function initChat() {
        const list = document.getElementById('chat-messages');
        if (!list) return;
        const mascot = document.querySelector('#section-home .chat-mascot');
        const observer = new MutationObserver(records => {
            if (M.off()) return;
            const added = [];
            records.forEach(r => r.addedNodes.forEach(n => {
                if (n.nodeType === 1 && n.classList.contains('chat-message')) added.push(n);
            }));
            added.forEach(msg => {
                const user = msg.classList.contains('user-message');
                const d = M.px('--motion-distance-sm', 6);
                msg.style.transformOrigin = user ? 'bottom right' : 'bottom left';
                M.animate(msg, [
                    { opacity: 0, transform: `translate3d(${user ? d : 0}px, ${d * 1.5}px, 0) scale(0.97)` },
                    { opacity: 1, transform: 'none' },
                ], { spring: 'smooth', key: 'chat-in' });
                // Een echt antwoord (geen puntjes): Pasha knikt één keer
                if (!user && !msg.querySelector('.message-loading') && mascot && inView(mascot)) {
                    M.animate(mascot, [
                        { transform: 'none' }, { transform: 'rotate(-8deg) translate3d(0, 2px, 0)', offset: 0.35 }, { transform: 'none' },
                    ], { spring: 'bouncy', key: 'nod' });
                }
            });
        });
        observer.observe(list, { childList: true });
    }

    // ==================== 11. LEEG EN FOUT ====================

    // Een lege of mislukte staat komt vriendelijk binnen: het icoon zet zich neer met een kring
    // die eenmaal uitdijt, titel en uitleg rijzen op; een fout schudt één keer zacht nee.
    // Dezelfde staat op dezelfde plek binnen korte tijd (een peiling die opnieuw tekent):
    // niet nog eens.
    const recentStates = new Map();

    function stateSignature(node) {
        const host = node.parentElement && (node.parentElement.closest('[id]') || node.parentElement);
        const title = node.querySelector('.empty-state-title');
        return `${host ? host.id || host.className : ''}|${title ? title.textContent : ''}`;
    }

    function animateState(node) {
        const sig = stateSignature(node);
        const now = performance.now();
        const seen = recentStates.get(sig);
        recentStates.set(sig, now);
        if (recentStates.size > 80) recentStates.delete(recentStates.keys().next().value);
        if (seen && now - seen < 20000) return;
        if (!inView(node)) return;
        const icon = node.querySelector('.empty-state-icon');
        const rest = [...node.children].filter(el => el !== icon);
        const step = M.ms('--stagger-step', 28);
        if (icon) {
            M.animate(icon, [{ opacity: 0, transform: 'scale(0.6) rotate(-10deg)' }, { opacity: 1, transform: 'none' }], { spring: 'smooth', key: 'state' });
            icon.classList.add('is-arriving');
            setTimeout(() => icon.classList.remove('is-arriving'), M.ms('--dur-6', 640) + 200);
            if (node.classList.contains('empty-state-error') && M.full()) {
                const d = M.px('--motion-distance-xs', 2) * 2;
                M.animate(icon, [
                    { transform: 'none' }, { transform: `translateX(${-d}px)` }, { transform: `translateX(${d}px)` },
                    { transform: `translateX(${-d / 2}px)` }, { transform: 'none' },
                ], { duration: '--dur-5', easing: '--ease-standard', delay: M.ms('--spring-smooth-dur', 520) * 0.6, key: 'shake' });
            }
        }
        M.stagger(rest, () => M.presets.rise(M.px('--motion-distance-sm', 6)), { spring: 'smooth', delay: step * 2, key: 'state' });
    }

    function watchStates() {
        const observer = new MutationObserver(records => {
            if (M.off()) return;
            for (const r of records) {
                for (const n of r.addedNodes) {
                    if (n.nodeType !== 1) continue;
                    if (n.classList.contains('empty-state')) animateState(n);
                    else if (n.firstElementChild && !n.classList.contains('media-card')) {
                        const inner = n.querySelector('.empty-state');
                        if (inner) animateState(inner);
                    }
                }
            }
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }

    // ==================== 12. HERSTELCENTRUM ====================

    // Vóór het opnieuw tekenen van de lijst: waar staat elke actie en kan hij nog terug.
    // Daarna glijden de acties naar hun nieuwe plek (FLIP op sleutel) en wat veranderde, licht op.
    function keyedFlip(list, selector) {
        const before = new Map();
        if (list && M.full()) {
            list.querySelectorAll(`:scope > ${selector}`).forEach(el => {
                // Was hij nog aan het binnenkomen, dan neemt zijn opvolger het over waar hij was
                const flying = el.getAnimations().some(a => a.playState === 'running' || a.playState === 'pending');
                const cs = flying ? getComputedStyle(el) : null;
                before.set(el.dataset.key, {
                    rect: el.getBoundingClientRect(), cls: el.className,
                    flight: cs ? { opacity: cs.opacity, transform: cs.transform === 'none' ? 'none' : cs.transform } : null,
                });
            });
        }
        const hadItems = before.size > 0;
        return function play() {
            if (!list) return;
            const items = [...list.querySelectorAll(`:scope > ${selector}`)];
            if (!hadItems) {
                // Eerste keer (na het skelet): wat in beeld staat in ritme, de rest bij het scrollen
                const section = list.closest('.section');
                if (section && section.classList.contains('active')) listArrive(list, selector, 0, 0);
                return;
            }
            items.forEach(el => {
                const old = before.get(el.dataset.key);
                if (!old) { M.enter(el, 'rise', { key: 'flip' }); return; }
                if (old.flight) {
                    M.animate(el, [old.flight, { opacity: 1, transform: 'none' }], { spring: 'smooth', key: 'page' });
                    return;
                }
                const now = el.getBoundingClientRect();
                const dy = old.rect.top - now.top;
                if (Math.abs(dy) > 0.5) {
                    M.animate(el, [{ transform: `translate3d(0, ${dy}px, 0)` }, { transform: 'none' }], { spring: 'snappy', key: 'flip' });
                }
                if (old.cls !== el.className) {
                    el.classList.add('is-changed');
                    setTimeout(() => el.classList.remove('is-changed'), M.ms('--dur-6', 640) * 2);
                }
            });
        };
    }

    // <details> in het Herstelcentrum: de bestandenlijst rijst op bij openklappen
    function initDetails() {
        document.addEventListener('toggle', (e) => {
            const details = e.target;
            if (!details.matches || !details.matches('.recovery-details') || !details.open) return;
            const files = details.querySelector('.recovery-files');
            if (files) M.stagger([...files.children].slice(0, 10), () => M.presets.rise(M.px('--motion-distance-sm', 6)), { spring: 'smooth', key: 'details' });
        }, true);
    }

    // ==================== OPSTARTEN ====================

    function init() {
        OWN_RHYTHM.forEach(sel => document.querySelectorAll(sel).forEach(el => { el.dataset.motionSkip = ''; }));
        initNavHover();
        initToastSwipe();
        watchOverlaysAndMenus();
        initSegmented();
        initShortcutEcho();
        initChat();
        watchStates();
        initDetails();
    }

    // Dit script staat onderaan de pagina: de schil bestaat al. Meteen beginnen, dus vóór de
    // DOMContentLoaded van app.js (die Home voor het eerst laat binnenkomen).
    init();

    window.ShellMotion = {
        navGlide,
        enterView,
        homeIntro,
        greetingReveal,
        statValue,
        storageValue,
        storageFill,
        otdShow,
        otdText,
        jobDockBefore,
        jobDockAfter,
        jobCelebrate,
        themeChanged,
        keyedFlip,
        listArrive,
    };
})();
