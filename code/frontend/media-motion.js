// DataVault Dashboard - Beweging voor foto's en media (media-motion.js)
//
// De bewegingstaal staat in docs/MOTION.md, het gereedschap in motion.js (window.Motion).
// Dit bestand voegt daar de stukken aan toe die alleen de fotoschermen gebruiken:
// Bestanden, Tijdlijn, Albums, Reeksen, Duplicaten, de viewer, de Keurer, Shows, Slim
// zoeken en Exporteren. Alles loopt via de helpers van Motion, dus elke stand
// (Volledig, Rustig, Uit) en prefers-reduced-motion doen vanzelf het goede.
//
//   MediaMotion.cascade(tiles)            de tegels in beeld komen als een golf binnen
//   MediaMotion.revealTiles(tiles, opts)  de rest pas als hij in beeld scrolt (per rij)
//   MediaMotion.flipByKey(els, key, fn)   een opnieuw getekende lijst: elk ding glijdt naar zijn
//                                         nieuwe plek, nieuwe dingen komen binnen
//   MediaMotion.ghost(el)                 een vaste kopie van el op zijn plek, om weg te laten gaan
//   MediaMotion.leave(el, opts)           een element gaat weg als spook (de lijst kan meteen verder)
//   MediaMotion.viewerStep(...)           volgende of vorige foto in de viewer
//   MediaMotion.keurerFly(card, decision) de beoordeelde kaart vliegt weg, de volgende staat er al
//   MediaMotion.rollText(el, text, dir)   een label rolt naar zijn nieuwe tekst (Tijdlijn)
//   MediaMotion.press(el)                 een knop geeft even mee (toetsenbord-feedback)
//   MediaMotion.pop(el)                   een kleine beloning (vinkje, winnaar)
//   MediaMotion.seriesSettle(card)        een reeks waaiert uit en komt tot rust op de beste
//   MediaMotion.slideMarker(rect, el)     de markering van een lijst glijdt naar el (Albums-zijbalk)
//
// Regels: alleen transform en opacity, niets blokkeert invoer, een nieuwe beweging met
// dezelfde key onderbreekt de vorige. Hover-licht en selectie staan in style.css
// (sectie BEWEGING: MEDIA).

(function () {
    'use strict';

    const settled = () => ({ finished: Promise.resolve(), cancel() {}, finish() {}, stub: true });
    const list = (els) => (!els ? [] : els instanceof Element ? [els] : [...els].filter(Boolean));
    const full = () => window.Motion && Motion.full();
    const off = () => !window.Motion || Motion.off();

    function group(anims) {
        return {
            finished: Promise.all(anims.map(a => a.finished.catch(() => {}))),
            cancel() { anims.forEach(a => a.cancel()); },
            finish() { anims.forEach(a => { try { a.finish(); } catch (_) { /* al klaar */ } }); },
        };
    }

    // Een tegel komt binnen: helder in het eerste derde, de veer zet hem daarna neer
    function tileFrames(distance, scale) {
        const d = distance === undefined ? Motion.px('--motion-distance-md', 12) : distance;
        const s = scale === undefined ? (parseFloat(Motion.token('--motion-scale-in')) || 0.96) : scale;
        return [
            { opacity: 0, transform: `translate3d(0, ${d}px, 0) scale(${s})` },
            { opacity: 1, offset: 0.3 },
            { opacity: 1, transform: 'none' },
        ];
    }

    // De elementen (in documentvolgorde, van boven naar onder) die rond het scherm staan: zoekt
    // binair naar de eerste, zodat een raster van duizenden tegels maar een paar keer gemeten wordt
    function nearView(els, margin = 0) {
        const all = list(els);
        const vh = window.innerHeight;
        let lo = 0, hi = all.length;
        while (lo < hi) {
            const mid = (lo + hi) >> 1;
            if (all[mid].getBoundingClientRect().bottom < -margin) lo = mid + 1; else hi = mid;
        }
        const out = [];
        for (let i = lo; i < all.length; i++) {
            const r = all[i].getBoundingClientRect();
            if (r.top > vh + margin) break;
            out.push(all[i]);
        }
        return out;
    }

    // Waarnemers van revealTiles per eigenaar (een raster): weg zodra het raster opnieuw tekent
    const owned = new WeakMap();
    function own(owner, observer) {
        if (!owner || !observer) return;
        if (!owned.has(owner)) owned.set(owner, []);
        owned.get(owner).push(observer);
    }
    function release(owner) {
        const observers = owner && owned.get(owner);
        if (!observers) return;
        observers.forEach(o => o.disconnect());
        owned.delete(owner);
    }

    // ---------- 1. Een raster komt binnen: een golf vanaf linksboven ----------

    // Alleen wat in beeld staat beweegt; de vertraging volgt rij + kolom (een diagonale golf),
    // na --stagger-max stappen komt de rest samen. opts.root: de scrollende ouder (standaard
    // het venster); opts.max: hoeveel tegels hoogstens; opts.step: ms per stap.
    function cascade(tiles, opts = {}) {
        const els = list(tiles);
        if (!els.length || off()) return settled();
        const rootRect = opts.root ? opts.root.getBoundingClientRect() : { top: 0, bottom: window.innerHeight };
        const seen = [];
        let rest = els.length;
        for (let i = 0; i < els.length; i++) {
            const r = els[i].getBoundingClientRect();
            if (!r.width || r.bottom < rootRect.top) continue;
            if (r.top > rootRect.bottom || seen.length >= (opts.max || 72)) { rest = i; break; }
            seen.push([els[i], r]);
        }
        // Wat onder de rand staat, komt pas binnen als het in beeld scrolt
        if (opts.revealRest && rest < els.length) revealTiles(els.slice(rest), { root: opts.root, owner: opts.owner });
        if (!seen.length) return settled();
        if (!full()) {
            return Motion.stagger(seen.map(([el]) => el), 'fade', { key: 'enter' });
        }
        // Rij en kolom uit de plek op het scherm (werkt voor elk raster, ook met gaten)
        const tops = [...new Set(seen.map(([, r]) => Math.round(r.top)))].sort((a, b) => a - b);
        const lefts = [...new Set(seen.map(([, r]) => Math.round(r.left)))].sort((a, b) => a - b);
        const near = (arr, v) => {
            let best = 0;
            arr.forEach((x, i) => { if (Math.abs(x - v) < Math.abs(arr[best] - v)) best = i; });
            return best;
        };
        const step = opts.step !== undefined ? opts.step : Motion.ms('--stagger-step', 28);
        const max = parseInt(Motion.token('--stagger-max'), 10) || 10;
        const frames = tileFrames(opts.distance, opts.scale);
        const anims = seen.map(([el, r]) => {
            const wave = near(tops, Math.round(r.top)) + near(lefts, Math.round(r.left)) * 0.75;
            return Motion.animate(el, frames, {
                spring: opts.spring || 'smooth',
                delay: (opts.delay || 0) + Math.min(wave, max) * step,
                key: 'enter',
            });
        });
        return group(anims);
    }

    // ---------- 2. De rest: pas als hij in beeld scrolt ----------

    // Per keer dat er tegels in beeld komen een korte golf van links naar rechts (Motion.reveal
    // sorteert ze op volgorde in het document). Alleen in Volledig; anders staan ze er meteen.
    function revealTiles(tiles, opts = {}) {
        const els = list(tiles);
        if (!els.length || !full()) return { disconnect() {} };
        const observer = Motion.reveal(els, {
            preset: () => tileFrames(Motion.px('--motion-distance-md', 12), 0.97),
            root: opts.root || null,
            margin: opts.margin || '0px 0px 6% 0px',   // net vóór de rand: bij snel scrollen geen leeg vlak
            step: opts.step !== undefined ? opts.step : Motion.ms('--stagger-step', 28) * 0.75,
        });
        own(opts.owner, observer);
        return observer;
    }

    // ---------- 3. Een lijst die opnieuw getekend wordt (sorteren, filteren, weghalen) ----------

    // flipByKey(oud, keyFn, mutate, {after, enter, exit, root})
    //   oud:    de elementen nu (worden gemeten voor mutate)
    //   keyFn:  el -> sleutel (hetzelfde ding in de nieuwe lijst heeft dezelfde sleutel)
    //   mutate: tekent de lijst opnieuw (mag nieuwe elementen maken)
    //   after:  () -> de elementen na mutate
    // Wat blijft glijdt naar zijn nieuwe plek (snappy), wat nieuw is komt binnen, en wat
    // verdween gaat als spook weg (opts.exit !== false). Alleen wat in beeld is beweegt.
    function flipByKey(before, keyFn, mutate, opts = {}) {
        if (!full()) { mutate(); return settled(); }
        const vh = window.innerHeight;
        const old = new Map();
        const ghosts = [];
        nearView(before, vh).forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.bottom < -vh || r.top > vh * 2 || !r.width) return;
            const key = keyFn(el);
            if (key === undefined || key === null) return;
            old.set(String(key), { el, r });
        });
        mutate();
        const now = nearView(typeof opts.after === 'function' ? opts.after() : opts.after, vh * 0.5);
        const anims = [];
        const kept = new Set();
        const moves = [];
        now.forEach(el => {
            const key = keyFn(el);
            const was = key === undefined || key === null ? null : old.get(String(key));
            if (was) kept.add(String(key));
            moves.push([el, was]);
        });
        // Eerst alles meten, dan pas animeren (geen lezen en schrijven door elkaar)
        const measured = moves.map(([el, was]) => [el, was, el.getBoundingClientRect()]);
        measured.forEach(([el, was, b]) => {
            if (b.bottom < 0 || b.top > vh) return;
            if (!was) {
                if (opts.enter !== false) anims.push(Motion.animate(el, tileFrames(undefined, 0.94), { spring: 'smooth', key: 'flip', delay: Motion.ms('--dur-1', 90) }));
                return;
            }
            const dx = was.r.left - b.left, dy = was.r.top - b.top;
            if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;
            anims.push(Motion.animate(el, [
                { transform: `translate3d(${dx}px, ${dy}px, 0)` },
                { transform: 'none' },
            ], { spring: opts.spring || 'snappy', key: 'flip' }));
        });
        if (opts.exit !== false) {
            old.forEach(({ el, r }, key) => {
                if (kept.has(key) || r.bottom < 0 || r.top > vh) return;
                ghosts.push(leaveFrom(el, r, opts.exitTo));
            });
        }
        return group(anims.concat(ghosts));
    }

    // ---------- 4. Spoken: iets gaat weg terwijl de lijst al verder is ----------

    let ghostLayer = null;
    function layer() {
        if (ghostLayer && ghostLayer.isConnected) return ghostLayer;
        ghostLayer = document.createElement('div');
        ghostLayer.className = 'mm-ghost-layer';
        ghostLayer.setAttribute('aria-hidden', 'true');
        document.body.appendChild(ghostLayer);
        return ghostLayer;
    }

    // Een kopie van el, vast op zijn plek op het scherm (rect: de plek, standaard die van nu).
    // Een kopie zonder id's, niet klikbaar en onzichtbaar voor schermlezers.
    function ghost(el, rect, opts = {}) {
        const r = rect || el.getBoundingClientRect();
        // In een venster of overlay blijft het spook in die laag (anders zou het eronder vallen)
        const host = opts.layer || el.closest('.dv-overlay, .modal');
        const copy = opts.move ? el : el.cloneNode(true);
        if (copy.id) copy.removeAttribute('id');
        copy.querySelectorAll('[id]').forEach(n => n.removeAttribute('id'));
        copy.querySelectorAll('[tabindex], button, a').forEach(n => n.setAttribute('tabindex', '-1'));
        copy.classList.add('mm-ghost');
        const holder = document.createElement('div');
        holder.className = 'mm-ghost-holder';
        Object.assign(holder.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
        holder.appendChild(copy);
        (host || layer()).appendChild(holder);
        return { holder, copy };
    }

    function leaveFrom(el, r, to) {
        if (!full() || !r.width) return settled();
        const { holder } = ghost(el, r);
        const d = Motion.px('--motion-distance-sm', 6);
        const anim = Motion.animate(holder, [
            { opacity: 1, transform: 'none' },
            to || { opacity: 0, transform: `translate3d(0, ${d}px, 0) scale(0.94)` },
        ], { duration: Motion.ms('--dur-3', 200), easing: '--ease-exit', fill: 'forwards', key: 'ghost' });
        const done = () => holder.remove();
        anim.finished.then(done, done);
        if (anim.stub) done();
        return anim;
    }

    // Een element gaat weg als spook; het echte element mag meteen weg of opnieuw getekend worden
    function leave(el, opts = {}) {
        if (!el || !el.isConnected || !full()) return settled();
        return leaveFrom(el, el.getBoundingClientRect(), opts.to);
    }

    // ---------- 5. Kleine beloningen en feedback ----------

    function pop(el, opts = {}) {
        if (!el) return settled();
        return Motion.animate(el, [
            { transform: `scale(${opts.from || 0.6})`, opacity: opts.fade === false ? 1 : 0 },
            { opacity: 1, offset: 0.3 },
            { transform: 'none', opacity: 1 },
        ], { spring: 'bouncy', key: opts.key || 'pop', delay: opts.delay || 0 });
    }

    // Een knop die met het toetsenbord 'ingedrukt' werd: even mee, en terug op de snappy-veer
    function press(el) {
        if (!el) return settled();
        const s = parseFloat(Motion.token('--motion-scale-press')) || 0.97;
        return Motion.animate(el, [
            { transform: `scale(${Math.min(s, 0.94)})` },
            { transform: 'none' },
        ], { spring: 'snappy', key: 'press', calm: false });
    }

    // Een label rolt naar zijn nieuwe tekst (omhoog bij verder, omlaag bij terug)
    function rollText(el, text, dir = 1) {
        if (!el || el.textContent === text) return settled();
        el.textContent = text;
        if (!full()) return settled();
        const d = Motion.px('--motion-distance-md', 12) * 0.75;
        return Motion.animate(el, [
            { opacity: 0, transform: `translate3d(0, ${dir * d}px, 0)` },
            { opacity: 1, transform: 'none' },
        ], { spring: 'snappy', key: 'roll' });
    }

    // Een getal (bijv. de selectie) tikt naar zijn nieuwe waarde met een klein duwtje
    function nudge(el, dir = 1) {
        if (!el || !full()) return settled();
        return Motion.animate(el, [
            { transform: `translate3d(0, ${dir * Motion.px('--motion-distance-sm', 6)}px, 0)`, opacity: 0.4 },
            { transform: 'none', opacity: 1 },
        ], { spring: 'snappy', key: 'nudge' });
    }

    // ---------- 6. Hover: een zacht licht dat de muis volgt ----------
    // Eén element voor de hele app: het schuift mee naar de tegel onder de muis en volgt de
    // aanwijzer met een transform (één schrijfactie per beeld). Geen kanteling, geen
    // kleurverschuiving: alleen licht boven de foto (docs/MOTION.md: rustig rond foto's).

    const LIGHT_TILES = '.thumb-tile:not(.folder-card):not(.other-card):not(.is-placeholder)';
    let light = null;
    let lightTile = null;
    let lightRect = null;
    let lightPos = null;
    let lightQueued = false;

    function lightEl() {
        if (light) return light;
        light = document.createElement('span');
        light.className = 'mm-light';
        light.setAttribute('aria-hidden', 'true');
        return light;
    }

    function placeLight() {
        lightQueued = false;
        if (!lightTile || !lightPos || !lightRect) return;
        const x = lightPos.x - lightRect.left - lightRect.width;
        const y = lightPos.y - lightRect.top - lightRect.height;
        light.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0)`;
    }

    function lightOn(tile, e) {
        if (!full() || !tile.querySelector('img')) return;
        const el = lightEl();
        lightTile = tile;
        lightRect = tile.getBoundingClientRect();
        el.classList.remove('is-on');
        tile.appendChild(el);
        lightPos = { x: e.clientX, y: e.clientY };
        placeLight();
        // Een beeld later aan: zo vaagt het licht in op zijn plek in plaats van te schuiven
        requestAnimationFrame(() => { if (lightTile === tile) el.classList.add('is-on'); });
    }

    function lightOff() {
        if (!light) return;
        light.classList.remove('is-on');
        lightTile = null;
        lightRect = null;
        const el = light;
        // Na het vervagen pas weghalen (anders flitst hij uit)
        setTimeout(() => { if (!lightTile && el.isConnected) el.remove(); }, Motion.ms('--dur-3', 200) + 20);
    }

    document.addEventListener('pointerover', (e) => {
        if (e.pointerType && e.pointerType !== 'mouse') return;
        const tile = e.target.closest && e.target.closest(LIGHT_TILES);
        if (tile === lightTile) return;
        if (lightTile) lightOff();
        if (tile && !tile.closest('.mm-ghost')) lightOn(tile, e);
    }, { passive: true });

    let lightSleeping = null;   // de tegel onder de muis na scrollen: aan bij de volgende beweging
    document.addEventListener('pointermove', (e) => {
        if (!lightTile) {
            if (!lightSleeping) return;
            const tile = e.target.closest && e.target.closest(LIGHT_TILES);
            lightSleeping = null;
            if (tile && !tile.closest('.mm-ghost')) lightOn(tile, e);
            return;
        }
        lightPos = { x: e.clientX, y: e.clientY };
        if (!lightQueued) { lightQueued = true; requestAnimationFrame(placeLight); }
    }, { passive: true });

    // Scrollen verschuift de tegel onder de muis: opnieuw meten bij de volgende beweging
    document.addEventListener('scroll', () => {
        if (lightTile) { lightOff(); lightSleeping = true; }
    }, { passive: true, capture: true });

    // ---------- 7. Viewer: volgende en vorige ----------
    // De oude foto blijft staan tot de nieuwe gedecodeerd is (nooit een leeg vlak), schuift dan
    // een stukje weg terwijl de nieuwe vanaf de andere kant binnenkomt. Alles in --dur-2:
    // pijltjes voelen meteen. Een nieuwe stap ruimt een lopende overgang meteen op.

    let viewerGhost = null;
    let viewerSeq = 0;

    function clearViewerGhost() {
        if (viewerGhost) { viewerGhost.remove(); viewerGhost = null; }
    }

    // body: #modal-body; oldMedia: het vorige beeld (al uit de DOM) met zijn plek oldRect;
    // media: het nieuwe beeld; step: 1 (volgende) of -1 (vorige)
    function viewerStep(body, oldMedia, oldRect, media, step, carry = 0) {
        clearViewerGhost();
        const seq = ++viewerSeq;
        if (!media || !step || off()) return settled();
        const d = Motion.px('--motion-distance-lg', 24) * 1.5;
        const dur = Motion.ms('--dur-2', 140);
        const isImg = media.tagName === 'IMG';
        // De oude foto als spook op precies zijn plek in de viewer
        if (oldMedia && oldRect && oldRect.width && full()) {
            const box = body.getBoundingClientRect();
            const g = oldMedia;
            g.removeAttribute('id');
            g.classList.add('mm-viewer-ghost');
            Object.assign(g.style, {
                left: `${oldRect.left - box.left - carry}px`, top: `${oldRect.top - box.top}px`,
                width: `${oldRect.width}px`, height: `${oldRect.height}px`, transform: carry ? `translate3d(${carry}px, 0, 0)` : '',
            });
            body.appendChild(g);
            viewerGhost = g;
        }
        const run = () => {
            if (seq !== viewerSeq) return;
            const g = viewerGhost;
            if (g) {
                const out = Motion.animate(g, [
                    { opacity: 1, transform: `translate3d(${carry}px, 0, 0)` },
                    { opacity: 0, transform: `translate3d(${-step * d + carry}px, 0, 0) scale(0.98)` },
                ], { duration: dur, easing: '--ease-exit', fill: 'forwards', key: 'step' });
                out.finished.then(() => { if (viewerGhost === g) clearViewerGhost(); }, () => {});
                if (out.stub) clearViewerGhost();
            }
            Motion.animate(media, [
                { opacity: 0, transform: `translate3d(${step * d}px, 0, 0)` },
                { opacity: 1, transform: 'none' },
            ], { duration: dur + Motion.ms('--dur-1', 90), easing: '--ease-standard', key: 'step' });
        };
        if (isImg && !media.complete && typeof media.decode === 'function') {
            // Hooguit een korte tel wachten: daarna gewoon door (een trage schijf)
            let done = false;
            const go = () => { if (!done) { done = true; run(); } };
            media.decode().then(go, go);
            setTimeout(go, 260);
        } else {
            run();
        }
        return settled();
    }

    // Vegen in de viewer (muis of trackpad): de foto volgt de hand; ver genoeg of snel genoeg
    // is de volgende of vorige, anders veert hij terug. Alleen zonder zoom.
    function viewerSwipe(body, hooks) {
        if (!body || body._mmSwipe) return;
        body._mmSwipe = true;
        let drag = null;
        const media = () => body.querySelector('#modal-image');
        // Geen 'afbeelding slepen' van de browser zelf: slepen is hier vegen
        body.addEventListener('dragstart', (e) => { if (e.target && e.target.id === 'modal-image') e.preventDefault(); });
        body.addEventListener('pointerdown', (e) => {
            const img = media();
            if (!img || e.button !== 0 || e.target !== img || !hooks.canSwipe()) return;
            drag = { x: e.clientX, y: e.clientY, t: performance.now(), dx: 0, img, id: e.pointerId, moved: false };
        });
        window.addEventListener('pointermove', (e) => {
            if (!drag || e.pointerId !== drag.id) return;
            const dx = e.clientX - drag.x;
            if (!drag.moved && Math.abs(dx) < 6) return;
            drag.moved = true;
            // Aan de rand van de lijst: weerstand (elastiek)
            const edge = (dx < 0 && !hooks.hasNext()) || (dx > 0 && !hooks.hasPrev());
            drag.dx = edge ? dx * 0.25 : dx;
            drag.img.style.transform = `translate3d(${drag.dx}px, 0, 0)`;
            drag.img.style.cursor = 'grabbing';
        }, { passive: true });
        const end = (e) => {
            if (!drag || (e && e.pointerId !== drag.id)) return;
            const { dx, img, t, moved } = drag;
            drag = null;
            img.style.cursor = '';
            if (!moved) return;
            const v = Math.abs(dx) / Math.max(1, performance.now() - t);
            const far = Math.abs(dx) > 90 || (Math.abs(dx) > 30 && v > 0.6);
            if (far && dx < 0 && hooks.hasNext()) { img.style.transform = ''; hooks.go(1, dx); return; }
            if (far && dx > 0 && hooks.hasPrev()) { img.style.transform = ''; hooks.go(-1, dx); return; }
            img.style.transform = '';
            Motion.animate(img, [{ transform: `translate3d(${dx}px, 0, 0)` }, { transform: 'none' }], { spring: 'snappy', key: 'swipe' });
        };
        window.addEventListener('pointerup', end);
        window.addEventListener('pointercancel', end);
        // Twee vingers op het trackpad, zijwaarts: één stap per veeg
        let wheel = 0, wheelLock = 0, wheelIdle = null;
        body.addEventListener('wheel', (e) => {
            if (Math.abs(e.deltaX) <= Math.abs(e.deltaY) || !hooks.canSwipe()) return;
            e.preventDefault();
            clearTimeout(wheelIdle);
            wheelIdle = setTimeout(() => { wheel = 0; wheelLock = 0; }, 180);
            if (wheelLock) return;
            wheel += e.deltaX;
            if (Math.abs(wheel) > 70) {
                const step = wheel > 0 ? 1 : -1;
                wheel = 0;
                wheelLock = 1;
                if (step > 0 ? hooks.hasNext() : hooks.hasPrev()) hooks.go(step, 0);
            }
        }, { passive: false });
    }

    // ---------- 8. Keurer: de kaart vliegt, de volgende staat er al ----------

    const flying = [];

    // Het spook vliegt ONDER de nieuwe kaart: een eigen laag vooraan in de Keurer-indeling, dus
    // vóór het kaartvak getekend. De volgende foto wordt nooit bedekt; alleen wat buiten de
    // nieuwe kaart uitsteekt is te zien.
    function keurerLayer(card) {
        const layout = card.closest('.keurer-layout');
        if (!layout) return null;
        let under = layout.querySelector(':scope > .mm-ghost-under');
        if (!under) {
            under = document.createElement('div');
            under.className = 'mm-ghost-layer mm-ghost-under';
            under.setAttribute('aria-hidden', 'true');
            layout.prepend(under);
        }
        return under;
    }

    // De kaart verlaat het vak als spook en vliegt in de richting van de beslissing (houden
    // links, prullenmand rechts, verplaatsen omhoog), onder de volgende kaart door. Het vak is
    // meteen vrij. Een nieuwe beslissing maakt het vorige spook meteen af: er vliegt er één.
    function keurerFly(card, decision) {
        if (!card || !card.isConnected) return settled();
        const stamp = { keep: '.overlay-keep', delete: '.overlay-delete', move: '.overlay-move', undo: '.overlay-undo' }[decision];
        if (off()) return settled();
        const current = card.style.transform || '';
        card.style.transform = 'none';
        const r = card.getBoundingClientRect();
        card.style.transform = current;
        const { holder, copy } = ghost(card, r, { move: true, layer: keurerLayer(card) });
        copy.classList.add('mm-keurer-flying');
        const overlay = stamp && copy.querySelector(stamp);
        if (overlay) overlay.style.opacity = '1';
        const from = getComputedStyle(copy).transform;
        copy.style.transform = '';
        const start = { transform: from === 'none' ? 'none' : from, opacity: 1 };
        const vw = window.innerWidth;
        let to;
        if (decision === 'keep') to = { transform: `translate3d(${-Math.max(vw * 0.55, 420)}px, -40px, 0) rotate(-16deg)`, opacity: 0 };
        else if (decision === 'delete') to = { transform: `translate3d(${Math.max(vw * 0.55, 420)}px, -40px, 0) rotate(16deg)`, opacity: 0 };
        else if (decision === 'move') to = { transform: `translate3d(0, ${-Math.max(window.innerHeight * 0.5, 360)}px, 0) scale(0.86)`, opacity: 0 };
        else to = { transform: `translate3d(0, ${Motion.px('--motion-distance-lg', 24) * 3}px, 0) scale(0.9)`, opacity: 0 };
        // Vertrekt meteen (--ease-out) en is na --dur-4 weg; dekkend tot een vijfde van de vlucht
        const anim = Motion.animate(copy, Motion.calm() ? [{ opacity: 1 }, { opacity: 0 }] : [start, { opacity: 1, offset: 0.2 }, to], {
            duration: Motion.calm() ? Motion.ms('--dur-2', 140) : Motion.ms('--dur-4', 280),
            easing: '--ease-out', fill: 'forwards', key: 'fly',
        });
        if (overlay && full()) {
            const badge = overlay.querySelector('.overlay-badge');
            if (badge) pop(badge, { from: 0.7, fade: false });
        }
        const entry = { holder, anim };
        flying.push(entry);
        while (flying.length > 1) {
            const old = flying.shift();
            try { old.anim.finish(); } catch (_) { old.holder.remove(); }
        }
        const done = () => {
            holder.remove();
            const at = flying.indexOf(entry);
            if (at >= 0) flying.splice(at, 1);
        };
        anim.finished.then(done, done);
        if (anim.stub) done();
        return anim;
    }

    // De volgende kaart: hij lag er al onder en komt naar voren (geen wachttijd, meteen te bedienen)
    function keurerArrive(card, from) {
        if (!card || off()) return settled();
        if (from) {
            // Terug van een ongedaan gemaakte beslissing: uit de richting waar hij heen ging
            const x = from === 'keep' ? -1 : from === 'delete' ? 1 : 0;
            const y = from === 'move' ? -1 : 0;
            const d = Motion.px('--motion-distance-lg', 24) * 4;
            return Motion.animate(card, [
                { opacity: 0, transform: `translate3d(${x * d}px, ${y * d}px, 0) rotate(${x * 6}deg) scale(0.96)` },
                { opacity: 1, offset: 0.35 },
                { opacity: 1, transform: 'none' },
            ], { spring: 'smooth', key: 'arrive' });
        }
        // Meteen dekkend (het spook eronder schijnt nooit door), alleen een klein zetje naar voren
        return Motion.animate(card, [
            { transform: `translate3d(0, ${Motion.px('--motion-distance-sm', 6)}px, 0) scale(${parseFloat(Motion.token('--motion-scale-in')) || 0.96})` },
            { transform: 'none' },
        ], { spring: 'snappy', key: 'arrive' });
    }

    // ---------- 9. Reeksen: de foto's waaieren uit en komen tot rust op de beste ----------

    // De foto's van een reeks beginnen als een stapel op de plek van de beste foto, waaieren
    // naar hun eigen plek (de dichtstbijzijnde eerst) en daarna zet de beste zich neer: zijn
    // ster springt en de ring verschijnt. Eén keer per reeks (opnieuw tekenen speelt niet nog eens).
    const settledSeries = new Set();

    function seriesSettle(card, opts = {}) {
        if (!card || !full()) return settled();
        const key = card.dataset.key;
        if (key && settledSeries.has(key) && !opts.again) return settled();
        if (key) settledSeries.add(key);
        const tiles = [...card.querySelectorAll('.series-photo')];
        if (tiles.length < 2) return settled();
        const best = card.querySelector('.series-photo.is-best') || tiles[0];
        const rects = tiles.map(t => t.getBoundingClientRect());
        if (!opts.force && (rects[0].bottom < 0 || rects[0].top > window.innerHeight)) return settled();
        const b = rects[tiles.indexOf(best)];
        const step = Motion.ms('--stagger-step', 28);
        const anims = [];
        const order = tiles.map((t, i) => [t, rects[i], Math.abs(rects[i].left - b.left)]).sort((x, y) => x[2] - y[2]);
        order.forEach(([tile, r], rank) => {
            const dx = b.left + b.width / 2 - (r.left + r.width / 2);
            const dy = b.top + b.height / 2 - (r.top + r.height / 2);
            const sign = dx > 0 ? -1 : 1;
            const isBest = tile === best;
            anims.push(Motion.animate(tile, isBest ? [
                { opacity: 0, transform: 'scale(0.9)' },
                { opacity: 1, offset: 0.3 },
                { opacity: 1, transform: 'none' },
            ] : [
                { opacity: 0, transform: `translate3d(${dx}px, ${dy}px, 0) rotate(${sign * (3 + rank)}deg) scale(0.9)` },
                { opacity: 1, offset: 0.25 },
                { opacity: 1, transform: 'none' },
            ], { spring: 'smooth', delay: (opts.delay || 0) + rank * step * 1.5, key: 'enter' }));
        });
        // De winnaar: een korte lift en de ster springt erop
        const after = (opts.delay || 0) + Motion.ms('--spring-smooth-dur', 520) * 0.55 + order.length * step;
        anims.push(Motion.animate(best, [
            { transform: 'none' },
            { transform: `translate3d(0, ${-Motion.px('--motion-distance-sm', 6)}px, 0) scale(1.03)`, offset: 0.4 },
            { transform: 'none' },
        ], { spring: 'gentle', delay: after, key: 'win', fill: 'none' }));
        const badge = best.querySelector('.series-best-badge');
        if (badge) anims.push(pop(badge, { delay: after + Motion.ms('--dur-1', 90), key: 'badge' }));
        best.classList.add('mm-winner');
        setTimeout(() => best.classList.remove('mm-winner'), after + Motion.ms('--spring-bouncy-dur', 620) + 300);
        return group(anims);
    }

    // Reeksen die later in beeld scrollen, waaieren dan pas uit (één keer per reeks)
    let seriesObserver = null;
    function seriesWatch(cards) {
        if (!full() || typeof IntersectionObserver !== 'function') return;
        if (!seriesObserver) {
            seriesObserver = new IntersectionObserver(entries => {
                entries.forEach(e => {
                    if (!e.isIntersecting) return;
                    seriesObserver.unobserve(e.target);
                    const card = e.target.closest('.series-card');
                    if (card && card.isConnected) seriesSettle(card, { force: true });
                });
            }, { threshold: 0 });
        }
        list(cards).forEach(card => {
            const photos = card.querySelector('.series-photos');
            if (!photos || settledSeries.has(card.dataset.key)) return;
            const r = photos.getBoundingClientRect();
            if (r.top < window.innerHeight && r.bottom > 0) seriesSettle(card, { force: true });
            else seriesObserver.observe(photos);
        });
    }

    // ---------- 10. Tijdlijn: rijen en koppen die zich neerzetten ----------
    // Het in beeld scrollen zelf doet style.css (animation-timeline: view()); hier alleen het
    // label met de huidige maand, dat bij verder scrollen omhoog en terug omlaag rolt.

    let lastRollTop = 0;
    function timelineLabel(el, text, scrollTop) {
        const dir = scrollTop >= lastRollTop ? 1 : -1;
        lastRollTop = scrollTop;
        return rollText(el, text, dir);
    }

    // ---------- 11. Een markering die naar een ander item in een lijst glijdt ----------
    // Zoals Motion.glide, maar in elke richting (een zijbalk is verticaal): de markering
    // (pseudo, standaard '::before') van toEl komt aanglijden vanaf fromRect, de plek van de
    // vorige. Alleen in Volledig; anders staat hij meteen op zijn plek.
    function slideMarker(fromRect, toEl, pseudo = '::before') {
        if (!fromRect || !toEl || !full()) return settled();
        const r = toEl.getBoundingClientRect();
        if (!r.width || !fromRect.width) return settled();
        const dx = fromRect.left - r.left, dy = fromRect.top - r.top;
        if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return settled();
        return Motion.animate(toEl, [
            { transform: `translate3d(${dx}px, ${dy}px, 0) scale(${fromRect.width / r.width}, ${fromRect.height / r.height})` },
            { transform: 'none' },
        ], { spring: 'snappy', pseudoElement: pseudo, key: 'marker' });
    }

    window.MediaMotion = {
        slideMarker,
        tileFrames,
        nearView,
        release,
        cascade,
        revealTiles,
        flipByKey,
        ghost,
        leave,
        pop,
        press,
        rollText,
        nudge,
        viewerStep,
        viewerSwipe,
        clearViewerGhost,
        keurerFly,
        keurerArrive,
        seriesSettle,
        seriesWatch,
        timelineLabel,
    };
})();
