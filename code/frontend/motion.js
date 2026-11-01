// DataVault Dashboard - Beweging (motion.js)
//
// De bewegingstaal van DataVault staat in docs/MOTION.md; dit bestand is het gereedschap.
// Het laadt als eerste script in <head>, zodat html[data-motion] al klopt vóór de eerste
// weergave (geen flits van een animatie die uit had moeten staan).
//
// Drie standen (Instellingen > Beweging), bewaard in app_settings (/api/ui/settings) en
// voor de eerste weergave ook in localStorage:
//   volledig  alles: veren, choreografie, gedeelde elementen (standaard)
//   rustig    alleen korte vervagingen; niets verschuift, schaalt of veert
//   uit       niets beweegt; elke helper zet meteen de eindtoestand
// Vraagt macOS om minder beweging, dan wordt Volledig vanzelf Rustig.
//
// Alles gaat via één object, window.Motion. De belangrijkste helpers:
//   Motion.mode() / full() / calm() / off()            de stand die nu geldt
//   Motion.animate(el, keyframes, opts)                Web Animations, stand-bewust
//   Motion.spring(el, keyframes, 'snappy'|'smooth'|'bouncy'|'gentle', opts)
//   Motion.enter(el, preset, opts) / exit(el, opts)    binnenkomen en vertrekken
//   Motion.stagger(els, preset, opts)                  in ritme na elkaar
//   Motion.reveal(els, opts)                           pas als het in beeld scrolt
//   Motion.flip(els, mutate, opts) / flipCapture(els)  van oude naar nieuwe plek
//   Motion.sharedElement(thumb, big, {direction})      miniatuur <-> viewer
//   Motion.countUp(el, to, opts)                       getal dat naar zijn waarde telt
//   Motion.viewTransition(update, opts)                document.startViewTransition met terugval
//   Motion.scrollLinked(el, keyframes, opts)           aan de scrollpositie gekoppeld
//   Motion.enterPage(section, {direction})             een onderdeel komt in beeld
//   Motion.glide(fromEl, toEl, opts)                   een indicator glijdt naar zijn nieuwe plek
// Alle helpers geven iets terug met .finished (een Promise) en .cancel(), ook als er
// niets beweegt; ze blokkeren nooit invoer en een nieuwe animatie met dezelfde key
// onderbreekt de vorige vanaf waar die was.

(function () {
    'use strict';

    const MODES = ['volledig', 'rustig', 'uit'];
    const STORE_KEY = 'motion';
    const html = document.documentElement;
    const reduceQuery = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
    const listeners = new Set();

    function readStored() {
        try {
            const value = localStorage.getItem(STORE_KEY);
            return MODES.includes(value) ? value : null;
        } catch (_) {
            return null;
        }
    }

    function writeStored(value) {
        try {
            if (value) localStorage.setItem(STORE_KEY, value);
            else localStorage.removeItem(STORE_KEY);
        } catch (_) { /* privé-venster: alleen voor deze sessie */ }
    }

    let preference = readStored();     // wat de gebruiker koos, of null (nooit gekozen)
    let tokenCache = null;

    function systemReduced() {
        return !!(reduceQuery && reduceQuery.matches);
    }

    // De stand die nu geldt: de keuze van de gebruiker, maar nooit meer dan macOS toestaat
    function mode() {
        const chosen = preference || 'volledig';
        return chosen === 'volledig' && systemReduced() ? 'rustig' : chosen;
    }

    function applyMode() {
        tokenCache = null;
        const current = mode();
        if (html.dataset.motion !== current) html.dataset.motion = current;
        listeners.forEach(fn => {
            try { fn(current); } catch (err) { console.error(err); }
        });
    }

    applyMode();
    if (reduceQuery) {
        const onSystem = () => applyMode();
        if (reduceQuery.addEventListener) reduceQuery.addEventListener('change', onSystem);
        else if (reduceQuery.addListener) reduceQuery.addListener(onSystem);
    }
    // Een ander tabblad koos een andere stand
    window.addEventListener('storage', (e) => {
        if (e.key === STORE_KEY) { preference = readStored(); applyMode(); }
    });

    // ---------- Tokens uit style.css (één bron) ----------

    function token(name) {
        if (!tokenCache) tokenCache = getComputedStyle(html);
        return tokenCache.getPropertyValue(name).trim();
    }

    // '280ms' of '0.28s' als getal in ms
    function ms(name, fallback = 0) {
        const raw = token(name);
        if (!raw) return fallback;
        const n = parseFloat(raw);
        if (Number.isNaN(n)) return fallback;
        return raw.endsWith('ms') ? n : (raw.endsWith('s') ? n * 1000 : n);
    }

    function px(name, fallback = 0) {
        const n = parseFloat(token(name));
        return Number.isNaN(n) ? fallback : n;
    }

    const SPRINGS = ['snappy', 'smooth', 'bouncy', 'gentle'];

    // {easing, duration} van een veer uit de tokens; in Rustig een korte ease-out
    function springTiming(name) {
        const key = SPRINGS.includes(name) ? name : 'smooth';
        return {
            easing: token(`--spring-${key}`) || 'cubic-bezier(0.2, 0.8, 0.2, 1)',
            duration: ms(`--spring-${key}-dur`, 400),
        };
    }

    // Een eigen veer (massa 1) als linear()-curve, voor een geval dat de vier tokens niet dekken
    function springEasing(stiffness = 190, damping = 25, points = 28) {
        let x = 0, v = 0, t = 0;
        const dt = 1 / 600, samples = [[0, 0]];
        let still = null;
        while (t < 3) {
            const a = -stiffness * (x - 1) - damping * v;
            v += a * dt; x += v * dt; t += dt;
            samples.push([t, x]);
            if (Math.abs(x - 1) < 0.002 && Math.abs(v) < 0.02) {
                if (still === null) still = t;
                if (t > still + 0.02) break;
            } else still = null;
        }
        const values = [];
        for (let i = 0; i <= points; i++) {
            const at = Math.min(samples.length - 1, Math.round((i / points) * (samples.length - 1)));
            values.push(i === points ? 1 : Math.round(samples[at][1] * 1000) / 1000);
        }
        return { easing: `linear(${values.join(', ')})`, duration: Math.round(t * 1000) };
    }

    // ---------- Animaties ----------

    // Iets met .finished en .cancel(), ook als er niets beweegt
    function settled() {
        return { finished: Promise.resolve(), cancel() {}, finish() {}, playState: 'finished', stub: true };
    }

    const MOVING = ['transform', 'translate', 'scale', 'rotate', 'filter', 'clipPath', 'offsetDistance',
        'width', 'height', 'top', 'left', 'right', 'bottom', 'backgroundPosition'];

    // Rustig: alleen dekking (en kleur) blijft over; niets verschuift
    function calmFrames(keyframes) {
        const frames = (Array.isArray(keyframes) ? keyframes : [keyframes]).map(frame => {
            const copy = { ...frame };
            MOVING.forEach(prop => { delete copy[prop]; });
            return copy;
        });
        const meaningful = frames.some(f => Object.keys(f).some(k => k !== 'offset' && k !== 'easing' && k !== 'composite'));
        return meaningful ? frames : null;
    }

    const running = new WeakMap();      // el -> Map(key -> Animation)

    function remember(el, key, anim) {
        let map = running.get(el);
        if (!map) { map = new Map(); running.set(el, map); }
        const previous = map.get(key);
        map.set(key, anim);
        anim.finished.then(() => { if (map.get(key) === anim) map.delete(key); }, () => {});
        return previous;
    }

    // Web Animations, stand-bewust.
    //   opts.spring   'snappy' | 'smooth' | 'bouncy' | 'gentle' (curve en duur uit de tokens)
    //   opts.duration ms of een token ('--dur-4'); opts.easing een curve of token ('--ease-exit')
    //   opts.delay    ms; opts.fill (standaard 'backwards': de begintoestand ook tijdens de wachttijd)
    //   opts.key      animaties met dezelfde key op hetzelfde element onderbreken elkaar
    //                 (standaard 'main'); opts.fromCurrent: begin bij de huidige transform
    //   opts.pseudoElement '::after' enz.; opts.calm: false = in Rustig helemaal niets doen
    function animate(el, keyframes, opts = {}) {
        if (!el || typeof el.animate !== 'function') return settled();
        const current = mode();
        if (current === 'uit') { cancelKey(el, opts.key || 'main'); return settled(); }
        let frames = keyframes;
        if (current === 'rustig') {
            if (opts.calm === false) return settled();
            frames = calmFrames(keyframes);
            if (!frames) return settled();
        }
        let duration, easing;
        if (opts.spring) {
            ({ duration, easing } = springTiming(opts.spring));
        }
        if (opts.duration !== undefined) duration = typeof opts.duration === 'string' ? ms(opts.duration, 200) : opts.duration;
        if (opts.easing) easing = opts.easing.startsWith('--') ? token(opts.easing) : opts.easing;
        if (duration === undefined) duration = ms('--dur-4', 280);
        if (!easing) easing = token('--ease-standard') || 'ease-out';
        if (current === 'rustig') duration = Math.min(duration, ms('--dur-5', 200));
        const key = opts.key || 'main';
        const map = running.get(el);
        const previous = map && map.get(key);
        if (previous && opts.fromCurrent && Array.isArray(frames) && frames.length) {
            const cs = getComputedStyle(el);
            frames = [{ ...frames[0], transform: cs.transform === 'none' ? 'none' : cs.transform, opacity: cs.opacity },
                ...frames.slice(1)];
        }
        if (previous) previous.cancel();
        let anim;
        try {
            anim = el.animate(frames, {
                duration,
                easing,
                delay: Math.max(0, opts.delay || 0),
                fill: opts.fill || 'backwards',
                pseudoElement: opts.pseudoElement,
                composite: opts.composite,
            });
        } catch (err) {
            // Een curve die deze browser niet kent (oud Safari zonder linear()): gewoon ease-out
            try {
                anim = el.animate(frames, { duration, easing: 'ease-out', delay: opts.delay || 0, fill: opts.fill || 'backwards', pseudoElement: opts.pseudoElement });
            } catch (_) {
                return settled();
            }
        }
        remember(el, key, anim);
        anim.finished.catch(() => {});   // geannuleerd is geen fout
        return anim;
    }

    function cancelKey(el, key) {
        const map = running.get(el);
        if (map && map.get(key)) map.get(key).cancel();
    }

    function spring(el, keyframes, name = 'smooth', opts = {}) {
        return animate(el, keyframes, { ...opts, spring: name });
    }

    // Vaste vormen van binnenkomen (van -> naar de eigen stijl van het element)
    const PRESETS = {
        fade: () => [{ opacity: 0 }, { opacity: 1 }],
        rise: (d = px('--motion-distance-md', 12)) => [
            { opacity: 0, transform: `translate3d(0, ${d}px, 0)` }, { opacity: 1, transform: 'none' }],
        sink: (d = px('--motion-distance-md', 12)) => [
            { opacity: 0, transform: `translate3d(0, ${-d}px, 0)` }, { opacity: 1, transform: 'none' }],
        slide: (dir = 1, d = px('--motion-distance-lg', 24)) => [
            { opacity: 0, transform: `translate3d(${dir * d}px, 0, 0)` }, { opacity: 1, transform: 'none' }],
        scale: (s = parseFloat(token('--motion-scale-in')) || 0.96) => [
            { opacity: 0, transform: `scale(${s})` }, { opacity: 1, transform: 'none' }],
        pop: () => [
            { opacity: 0, transform: 'scale(0.6)' }, { opacity: 1, transform: 'none' }],
    };

    function frames(preset, ...args) {
        if (Array.isArray(preset)) return preset;
        if (typeof preset === 'function') return preset(...args);
        return (PRESETS[preset] || PRESETS.rise)(...args);
    }

    // Binnenkomen: standaard 'rise' op de smooth-veer
    function enter(el, preset = 'rise', opts = {}) {
        return animate(el, frames(preset, ...(opts.args || [])), { spring: 'smooth', ...opts });
    }

    // Vertrekken: korter dan binnenkomen en versnellend. opts.remove haalt het element daarna weg.
    // opts.to: eigen eindtoestand (standaard vervagen en iets zakken)
    function exit(el, opts = {}) {
        if (!el) return settled();
        const done = () => { if (opts.remove && el.isConnected) el.remove(); };
        if (mode() === 'uit') { done(); return settled(); }
        const d = px('--motion-distance-sm', 6);
        const to = opts.to || { opacity: 0, transform: `translate3d(0, ${d}px, 0) scale(0.98)` };
        const cs = getComputedStyle(el);
        const anim = animate(el, [{ opacity: cs.opacity, transform: cs.transform === 'none' ? 'none' : cs.transform }, to], {
            duration: opts.duration || ms('--dur-3', 200),
            easing: opts.easing || token('--ease-exit') || 'ease-in',
            fill: 'forwards',
            key: opts.key || 'main',
        });
        anim.finished.then(done, () => {});
        if (anim.stub) done();
        return anim;
    }

    // Na elkaar: elk element --stagger-step later, na --stagger-max stappen samen
    function stagger(elements, preset = 'rise', opts = {}) {
        const list = toList(elements);
        if (!list.length || mode() !== 'volledig') {
            if (mode() === 'rustig' && list.length && opts.calm !== false) {
                const anims = list.map(el => animate(el, PRESETS.fade(), { duration: ms('--dur-3', 160), key: opts.key }));
                return group(anims);
            }
            return settled();
        }
        const step = opts.step !== undefined ? opts.step : ms('--stagger-step', 28);
        const max = opts.max || parseInt(token('--stagger-max'), 10) || 10;
        const anims = list.map((el, i) => animate(el, frames(preset, ...(opts.args || [])), {
            spring: opts.spring || 'smooth',
            delay: (opts.delay || 0) + Math.min(i, max) * step,
            key: opts.key,
            duration: opts.duration,
            easing: opts.easing,
        }));
        return group(anims);
    }

    function group(anims) {
        return {
            finished: Promise.all(anims.map(a => a.finished.catch(() => {}))),
            cancel() { anims.forEach(a => a.cancel()); },
            finish() { anims.forEach(a => { try { a.finish(); } catch (_) { /* al klaar */ } }); },
        };
    }

    function toList(elements) {
        if (!elements) return [];
        if (typeof elements === 'string') return [...document.querySelectorAll(elements)];
        if (elements instanceof Element) return [elements];
        return [...elements].filter(Boolean);
    }

    // ---------- In beeld scrollen (IntersectionObserver) ----------

    // Elementen komen pas binnen als ze in beeld scrollen; wat samen binnenkomt, komt in ritme.
    // Alleen in Volledig; anders staat alles er meteen. opts: preset, root, margin, once (true)
    function reveal(elements, opts = {}) {
        const list = toList(elements).filter(el => el.dataset.reveal !== 'done');
        if (!list.length || mode() !== 'volledig' || typeof IntersectionObserver !== 'function') {
            list.forEach(el => { el.dataset.reveal = 'done'; });
            return { disconnect() {} };
        }
        const preset = opts.preset || 'rise';
        const observer = new IntersectionObserver((entries) => {
            const coming = entries.filter(e => e.isIntersecting).map(e => e.target);
            if (!coming.length) return;
            coming.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1);
            coming.forEach(el => {
                el.dataset.reveal = 'done';
                if (opts.once !== false) observer.unobserve(el);
            });
            stagger(coming, preset, { spring: opts.spring || 'smooth', step: opts.step, key: 'reveal' });
        }, { root: opts.root || null, rootMargin: opts.margin || '0px 0px -6% 0px', threshold: 0.01 });
        list.forEach(el => { el.dataset.reveal = 'pending'; observer.observe(el); });
        return observer;
    }

    // ---------- FLIP: van de oude naar de nieuwe plek ----------

    function rectsOf(list) {
        return new Map(list.map(el => [el, el.getBoundingClientRect()]));
    }

    // Meet nu, en speel later (na je eigen wijziging) de verplaatsing af
    function flipCapture(elements, opts = {}) {
        const list = toList(elements);
        const first = mode() === 'volledig' ? rectsOf(list) : null;
        return function play(after) {
            if (!first) return settled();
            const now = toList(after || list);
            const anims = [];
            now.forEach(el => {
                if (!el.isConnected) return;
                const a = first.get(el);
                const b = el.getBoundingClientRect();
                if (!a) {
                    if (opts.enter) anims.push(enter(el, opts.enter, { key: 'flip' }));
                    return;
                }
                const dx = a.left - b.left, dy = a.top - b.top;
                const sx = opts.scale && b.width ? a.width / b.width : 1;
                const sy = opts.scale && b.height ? a.height / b.height : 1;
                if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(sx - 1) < 0.01 && Math.abs(sy - 1) < 0.01) return;
                anims.push(animate(el, [
                    { transform: `translate3d(${dx}px, ${dy}px, 0) scale(${sx}, ${sy})`, transformOrigin: 'top left' },
                    { transform: 'none', transformOrigin: 'top left' },
                ], { spring: opts.spring || 'snappy', key: 'flip' }));
            });
            return group(anims);
        };
    }

    // flip(elementen, () => { ...verander de DOM... }): de elementen glijden naar hun nieuwe plek.
    // mutate mag een Promise teruggeven. Elementen die er pas na mutate zijn: opts.enter (preset).
    function flip(elements, mutate, opts = {}) {
        const play = flipCapture(elements, opts);
        const result = mutate ? mutate() : null;
        const targets = opts.after || elements;
        if (result && typeof result.then === 'function') return result.then(() => play(typeof targets === 'function' ? targets() : targets));
        return play(typeof targets === 'function' ? targets() : targets);
    }

    // ---------- Gedeeld element: miniatuur <-> groot ----------

    // Waar een <img> met object-fit: contain zijn pixels echt tekent
    function contentRect(el, aspect) {
        const box = el.getBoundingClientRect();
        const natural = el.naturalWidth && el.naturalHeight ? el.naturalWidth / el.naturalHeight : aspect;
        if (!natural || !box.width || !box.height) return box;
        const fit = getComputedStyle(el).objectFit;
        if (fit !== 'contain' && fit !== 'scale-down') return box;
        let w = box.width, h = box.width / natural;
        if (h > box.height) { h = box.height; w = h * natural; }
        return { left: box.left + (box.width - w) / 2, top: box.top + (box.height - h) / 2, width: w, height: h };
    }

    function inViewport(r) {
        return r.width > 1 && r.height > 1 && r.bottom > 0 && r.right > 0
            && r.top < window.innerHeight && r.left < window.innerWidth;
    }

    // Een foto groeit vanuit zijn miniatuur naar zijn plek in de viewer (direction 'open'), of
    // krimpt terug (direction 'close'). thumb: de miniatuur (<img> of een element met een
    // <img>); big: de grote <img>, of opts.rect als die nog niet geladen is. De bijsnijding
    // van de miniatuur (object-fit: cover) gaat vloeiend over in het hele beeld.
    function sharedElement(thumb, big, opts = {}) {
        if (mode() !== 'volledig' || !thumb || !thumb.isConnected) return settled();
        const thumbImg = thumb.tagName === 'IMG' ? thumb : thumb.querySelector('img');
        const s = (thumbImg || thumb).getBoundingClientRect();
        const aspect = thumbImg && thumbImg.naturalWidth ? thumbImg.naturalWidth / thumbImg.naturalHeight : null;
        let t = opts.rect || (big ? contentRect(big, aspect) : null);
        if (!t || !inViewport(s) || !t.width || !t.height) return settled();
        const src = opts.src || (thumbImg && (thumbImg.currentSrc || thumbImg.src)) || (big && (big.currentSrc || big.src));
        if (!src) return settled();

        const ghost = document.createElement('img');
        ghost.className = 'dv-shared-ghost';
        ghost.alt = '';
        ghost.setAttribute('aria-hidden', 'true');
        ghost.src = src;
        Object.assign(ghost.style, { left: `${t.left}px`, top: `${t.top}px`, width: `${t.width}px`, height: `${t.height}px` });

        const scale = Math.max(s.width / t.width, s.height / t.height);
        const insetX = Math.max(0, (t.width - s.width / scale) / 2);
        const insetY = Math.max(0, (t.height - s.height / scale) / 2);
        const thumbRadius = parseFloat(getComputedStyle(thumbImg || thumb).borderTopLeftRadius) || px('--radius-thumb', 8);
        const bigRadius = opts.radius !== undefined ? opts.radius : 0;
        const dx = (s.left + s.width / 2) - (t.left + t.width / 2);
        const dy = (s.top + s.height / 2) - (t.top + t.height / 2);
        const small = { transform: `translate3d(${dx}px, ${dy}px, 0) scale(${scale})`, clipPath: `inset(${insetY}px ${insetX}px round ${thumbRadius / scale}px)` };
        const large = { transform: 'none', clipPath: `inset(0px 0px round ${bigRadius}px)` };
        const opening = opts.direction !== 'close';

        document.body.appendChild(ghost);
        const hide = [thumbImg || thumb, big].filter(Boolean);
        const before = hide.map(el => el.style.opacity);
        hide.forEach(el => { el.style.opacity = '0'; });
        const restore = () => hide.forEach((el, i) => { el.style.opacity = before[i]; });

        const anim = animate(ghost, opening ? [small, large] : [large, small], {
            spring: 'gentle', key: 'shared', fill: 'forwards',
        });
        const cleanup = () => {
            restore();
            // Is de grote foto nog niet geladen, dan blijft de miniatuur even staan en vervaagt hij
            if (opening && big && big.tagName === 'IMG' && !big.complete) {
                const fade = () => animate(ghost, [{ opacity: 1 }, { opacity: 0 }], { duration: ms('--dur-3', 200), fill: 'forwards', key: 'ghost-out' })
                    .finished.then(() => ghost.remove(), () => ghost.remove());
                big.addEventListener('load', fade, { once: true });
                big.addEventListener('error', fade, { once: true });
                setTimeout(() => { if (ghost.isConnected) fade(); }, 2500);
                big.style.opacity = '0';
                const show = () => { big.style.opacity = before[1] || ''; };
                big.addEventListener('load', show, { once: true });
                big.addEventListener('error', show, { once: true });
                setTimeout(show, 2500);
            } else {
                ghost.remove();
            }
        };
        anim.finished.then(cleanup, () => { restore(); ghost.remove(); });
        if (anim.stub) { restore(); ghost.remove(); }
        return anim;
    }

    // ---------- Getallen die tellen ----------

    const NUMBER = new Intl.NumberFormat('nl-NL');

    // Een getal telt in --dur-6 naar zijn waarde (ease-out). opts.from (standaard 0, of wat er
    // nu staat), opts.format(n) voor eigen opmaak, opts.decimals. Zonder beweging: meteen.
    function countUp(el, to, opts = {}) {
        if (!el) return settled();
        const decimals = opts.decimals || 0;
        const format = opts.format || (n => decimals ? n.toLocaleString('nl-NL', {
            minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : NUMBER.format(Math.round(n)));
        const from = opts.from !== undefined ? opts.from
            : (el._motionValue !== undefined ? el._motionValue : 0);
        if (el._motionRaf) cancelAnimationFrame(el._motionRaf);
        el._motionValue = to;
        if (mode() !== 'volledig' || from === to || !Number.isFinite(to)) {
            el.textContent = format(to);
            return settled();
        }
        const duration = opts.duration || ms('--dur-6', 640);
        const start = performance.now();
        let resolve;
        const finished = new Promise(r => { resolve = r; });
        const tick = (now) => {
            const p = Math.min(1, (now - start) / duration);
            const eased = 1 - Math.pow(1 - p, 4);   // ease-out quart: snel weg, zacht aankomen
            el.textContent = format(from + (to - from) * eased);
            if (p < 1) el._motionRaf = requestAnimationFrame(tick);
            else { el._motionRaf = null; resolve(); }
        };
        el._motionRaf = requestAnimationFrame(tick);
        return {
            finished,
            cancel() { cancelAnimationFrame(el._motionRaf); el.textContent = format(to); resolve(); },
            finish() { this.cancel(); },
        };
    }

    // ---------- View Transitions ----------

    let activeTransition = null;

    // update() verandert de DOM; met View Transitions vloeit de oude weergave in de nieuwe.
    // Let op: de browser roept update pas een beeld later aan. Gebruik het alleen waar de
    // code erna niet meteen op de nieuwe DOM rekent (thema, raster/lijst, een ander album).
    // opts.kind zet html[data-vt="kind"] tijdens de overgang, voor eigen CSS
    // (::view-transition-old(root) enz.); opts.origin {x, y} zet --vt-x/--vt-y.
    // Uit, Rustig zonder opts.calm, geen ondersteuning of een verborgen tabblad: update meteen.
    function viewTransition(update, opts = {}) {
        const current = mode();
        const canAnimate = typeof document.startViewTransition === 'function' && !document.hidden
            && (current === 'volledig' || (current === 'rustig' && opts.calm));
        if (!canAnimate) {
            update();
            return { finished: Promise.resolve(), updateCallbackDone: Promise.resolve(), ready: Promise.resolve(), skipTransition() {} };
        }
        if (activeTransition) activeTransition.skipTransition();
        if (opts.kind) html.dataset.vt = opts.kind;
        if (opts.origin) {
            html.style.setProperty('--vt-x', `${Math.round(opts.origin.x)}px`);
            html.style.setProperty('--vt-y', `${Math.round(opts.origin.y)}px`);
        }
        const vt = document.startViewTransition(() => {
            try {
                update();
            } catch (err) {
                // Zichtbaar als gewone paginafout, niet ingeslikt door de overgang
                setTimeout(() => { throw err; });
            }
        });
        activeTransition = vt;
        const clear = () => {
            if (activeTransition !== vt) return;
            activeTransition = null;
            delete html.dataset.vt;
        };
        vt.finished.then(clear, clear);
        vt.ready.catch(() => {});
        vt.updateCallbackDone.catch(() => {});
        return vt;
    }

    // ---------- Aan het scrollen gekoppeld ----------

    // keyframes lopen mee met de scrollpositie van opts.source (standaard het document).
    // opts.start / opts.end: scrollafstand in px waarbinnen de animatie loopt. Zonder start
    // en end loopt hij over de hele scrollhoogte, dan met ScrollTimeline door de browser zelf
    // (buiten de hoofdthread); anders via één requestAnimationFrame per scrollbeweging.
    // Voor een blok dat bij het in beeld scrollen verschijnt: de CSS-klasse .motion-scroll-reveal.
    function scrollLinked(el, keyframes, opts = {}) {
        if (!el || mode() !== 'volledig') return { cancel() {} };
        const source = opts.source || document.scrollingElement || document.documentElement;
        if (opts.start === undefined && opts.end === undefined && typeof window.ScrollTimeline === 'function') {
            try {
                const native = el.animate(keyframes, { fill: 'both', easing: 'linear',
                    timeline: new window.ScrollTimeline({ source, axis: 'block' }) });
                return { cancel() { native.cancel(); } };
            } catch (_) { /* geen tijdlijn in Web Animations: hieronder met de hand */ }
        }
        const start = opts.start || 0;
        const end = opts.end || 200;
        const anim = el.animate(keyframes, { duration: 1000, fill: 'both', easing: 'linear' });
        anim.pause();
        const scroller = source === document.scrollingElement || source === document.documentElement ? window : source;
        let queued = false;
        const update = () => {
            queued = false;
            const y = scroller === window ? window.scrollY : source.scrollTop;
            const p = Math.min(1, Math.max(0, (y - start) / Math.max(1, end - start)));
            anim.currentTime = p * 1000;
        };
        const onScroll = () => { if (!queued) { queued = true; requestAnimationFrame(update); } };
        scroller.addEventListener('scroll', onScroll, { passive: true });
        update();
        return { cancel() { scroller.removeEventListener('scroll', onScroll); anim.cancel(); } };
    }

    // ---------- Een indicator die glijdt (navigatie, segmentknoppen, tabs) ----------

    // De indicator (opts.pseudoElement, standaard '::after') van toEl komt aanglijden vanaf de
    // plek van die van fromEl: verschuiven en uitrekken op de snappy-veer. opts.inset(el): hoeveel
    // px de indicator links en rechts binnen zijn element begint (standaard 0).
    function glide(fromEl, toEl, opts = {}) {
        if (!fromEl || !toEl || fromEl === toEl || mode() !== 'volledig') return settled();
        const pseudo = opts.pseudoElement === undefined ? '::after' : opts.pseudoElement;
        const inset = opts.inset || (() => 0);
        const shrink = (r, d) => ({ left: r.left + d, width: Math.max(1, r.width - 2 * d) });
        const a = shrink(fromEl.getBoundingClientRect(), inset(fromEl));
        const b = shrink(toEl.getBoundingClientRect(), inset(toEl));
        if (a.width <= 1 || b.width <= 1) return settled();
        const dx = (a.left + a.width / 2) - (b.left + b.width / 2);
        const sx = a.width / b.width;
        return animate(toEl, [
            { transform: `translateX(${dx}px) scaleX(${sx})` },
            { transform: 'none' },
        ], { spring: 'snappy', pseudoElement: pseudo || undefined, key: 'glide', calm: false });
    }

    // ---------- Een onderdeel komt in beeld ----------

    // Wat in een sectie zelf niet mag verschuiven (vaste lagen erin zouden meeschuiven)
    const PAGE_SKIP = '[data-motion-skip], .modal, .dv-overlay, .leaflet-container';

    // De blokken van een pagina: kop, werkbalk, inhoud. Zakt door zolang er maar één kind is.
    function pageBlocks(section) {
        let root = section;
        for (let depth = 0; depth < 3; depth++) {
            const kids = [...root.children].filter(el => el.getClientRects().length);
            if (kids.length !== 1) break;
            root = kids[0];
        }
        const bottom = window.innerHeight;
        return [...root.children].filter(el => {
            if (!el.getClientRects().length || el.matches(PAGE_SKIP) || el.querySelector(PAGE_SKIP)) return false;
            const r = el.getBoundingClientRect();
            return r.top < bottom && r.bottom > 0;
        }).slice(0, 8);
    }

    // De sectie vervaagt in; zijn blokken schuiven in ritme binnen, vanuit de richting van het
    // menu (direction 1: van rechts, -1: van links, 0: van onder). Alleen dekking op de sectie
    // zelf: een transform daar zou vaste lagen erin (vensters, de kaart) laten verschuiven.
    function enterPage(section, opts = {}) {
        if (!section) return settled();
        const current = mode();
        if (current === 'uit') return settled();
        const fade = animate(section, PRESETS.fade(), { duration: ms('--dur-3', 200), easing: token('--ease-out'), key: 'page' });
        if (current !== 'volledig') return fade;
        const dir = opts.direction || 0;
        const d = px('--motion-distance-md', 12);
        const blocks = pageBlocks(section);
        const preset = () => dir
            ? [{ opacity: 0, transform: `translate3d(${dir * d * 1.5}px, 0, 0)` }, { opacity: 1, transform: 'none' }]
            : PRESETS.rise(d);
        return group([fade, stagger(blocks, preset, { spring: 'smooth', key: 'page', step: ms('--stagger-step', 28) * 1.5 })]);
    }

    // ---------- Vensters: ook netjes weg ----------
    // Een .modal of .dv-overlay die zijn klasse 'active' verliest, verdwijnt niet meteen maar
    // krijgt even 'is-leaving' (CSS: zichtbaar, niet klikbaar, vervaagt en zakt). Komt hij
    // intussen terug, dan stopt het vertrek. Niets in de app hoeft hiervoor iets te doen.

    const LEAVING = '.modal, .dv-overlay';

    // Tijdens het vertrek is het venster inert: Tab en schermlezers slaan het over, zodat de
    // focus nooit in iets belandt dat aan het verdwijnen is.
    function stopLeaving(el) {
        clearTimeout(el._motionLeave);
        el.classList.remove('is-leaving');
        if (el._motionInert) { el.inert = false; el._motionInert = false; }
    }

    function overlayLeaving(el) {
        if (mode() === 'uit') return;
        el.classList.add('is-leaving');
        if (!el.inert) { el.inert = true; el._motionInert = true; }
        clearTimeout(el._motionLeave);
        el._motionLeave = setTimeout(() => stopLeaving(el), ms('--dur-3', 200) + 40);
    }

    function watchOverlays() {
        const observer = new MutationObserver(records => {
            records.forEach(r => {
                const el = r.target;
                if (!el.matches || !el.matches(LEAVING)) return;
                const was = (r.oldValue || '').split(/\s+/).includes('active');
                const is = el.classList.contains('active');
                if (was && !is) overlayLeaving(el);
                if (is && el.classList.contains('is-leaving')) stopLeaving(el);
            });
        });
        observer.observe(document.body, { attributes: true, attributeFilter: ['class'], attributeOldValue: true, subtree: true });
    }

    // ---------- Opslaan en ophalen ----------

    // De keuze van de gebruiker: meteen toepassen, lokaal onthouden en in app_settings bewaren
    async function setPreference(value) {
        if (value !== null && !MODES.includes(value)) throw new Error(`onbekende stand: ${value}`);
        preference = value;
        writeStored(value);
        applyMode();
        if (typeof window.fetchJSON === 'function') {
            await window.fetchJSON('/api/ui/settings', {
                method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ motion: value }),
            });
        }
        return mode();
    }

    // Bij het laden: wat de server bewaarde wint (een andere browser kan het gekozen hebben)
    async function syncFromServer() {
        if (typeof window.fetchJSON !== 'function') return;
        try {
            const data = await window.fetchJSON('/api/ui/settings');
            if (data && MODES.includes(data.motion) && data.motion !== preference) {
                preference = data.motion;
                writeStored(preference);
                applyMode();
            }
        } catch (_) { /* archief los of server weg: de lokale keuze blijft gelden */ }
    }

    function onReady() {
        watchOverlays();
        syncFromServer();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', onReady);
    else onReady();

    window.Motion = {
        MODES,
        mode,
        full: () => mode() === 'volledig',
        calm: () => mode() === 'rustig',
        off: () => mode() === 'uit',
        preference: () => preference,
        systemReduced,
        setPreference,
        syncFromServer,
        onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
        token,
        ms,
        px,
        springTiming,
        springEasing,
        presets: PRESETS,
        animate,
        spring,
        enter,
        exit,
        stagger,
        reveal,
        flip,
        flipCapture,
        sharedElement,
        contentRect,
        countUp,
        viewTransition,
        scrollLinked,
        glide,
        enterPage,
    };
})();
