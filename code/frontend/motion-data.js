// DataVault Dashboard - Beweging voor de gegevensschermen (motion-data.js)
//
// Bouwt op de bewegingstaal (docs/MOTION.md) en de helpers in motion.js (window.Motion).
// Dit bestand geeft de schermen met gegevens en karakter hun eigen beweging:
//   Spotify       tellers die tikken, kaarten in ritme, staafjes die groeien, een DNA-helix
//   Kaart         een route die zich langs het pad tekent, bezochte plekken die opduiken
//   DataGatherer  kaarten in ritme, een satelliet bij een bron die bezig is, voortgang die
//                 doorloopt, een klein feest als een bron klaar is
//   Berichten     gesprekken die binnenstromen, een selectie die meeglijdt
//   Activiteit    een tijdlijn die zich naar beneden tekent, een dag die meeglijdt
//   Pasha         een levend kopje: ademen, luisteren als je typt, nadenken, knikken
//
// Elke functie is stand-bewust (Volledig / Rustig / Uit) via de helpers en tokens van
// motion.js; zonder beweging staat alles meteen in zijn eindtoestand. Alles is interrumpeerbaar
// (key per element), gebruikt alleen transform en opacity (plus stroke-dashoffset voor de
// route) en blokkeert nooit invoer. De schermen roepen MotionData.* aan na hun eigen tekenwerk;
// ontbreekt dit bestand, dan werkt alles gewoon zonder deze beweging.

(function () {
    'use strict';

    const M = () => window.Motion;
    const full = () => !!(window.Motion && Motion.full());
    const off = () => !window.Motion || Motion.off();
    const reduceList = (els) => [...(els || [])].filter(Boolean);

    // ---------- Kleine bouwstenen ----------

    // Hoeveel elementen onder de vouw nog los binnenkomen als ze in beeld scrollen; daarna
    // staan ze er gewoon (een lijst van duizenden houdt zo geen duizenden waarnemers bij)
    const REVEAL_CAP = 48;

    // Elementen die nu (deels) in hun scrollvlak of in het venster staan. De lijst staat in
    // leesvolgorde: na het laatste zichtbare element stopt het meten.
    function visibleIn(els, root) {
        const box = root ? root.getBoundingClientRect() : { top: 0, bottom: window.innerHeight };
        const seen = [];
        const list = reduceList(els);
        // Lange lijsten (een gesprek van duizenden berichten, onderaan geopend): zoek binair het
        // eerste element dat tot in beeld reikt, in plaats van alles erboven te meten
        let from = 0;
        if (list.length > 64) {
            let lo = 0, hi = list.length - 1;
            while (lo < hi) {
                const mid = (lo + hi) >> 1;
                if (list[mid].getBoundingClientRect().bottom <= box.top) lo = mid + 1; else hi = mid;
            }
            from = Math.max(0, lo - 1);
        }
        for (const el of list.slice(from)) {
            const r = el.getBoundingClientRect();
            if (r.height <= 0 || r.bottom <= box.top) continue;
            if (r.top >= box.bottom) break;
            seen.push(el);
        }
        return seen;
    }

    // Wat nu in beeld staat komt in ritme binnen; de volgende pas als ze in beeld scrollen
    function arrive(els, opts = {}) {
        const list = reduceList(els);
        if (!list.length || off()) return;
        const seen = visibleIn(list, opts.root);
        M().stagger(seen, opts.preset || 'rise', { spring: opts.spring || 'smooth', step: opts.step, delay: opts.delay, key: 'md-arrive' });
        const after = seen.length ? list.indexOf(seen[seen.length - 1]) + 1 : 0;
        const rest = list.slice(after, after + REVEAL_CAP);
        if (rest.length) M().reveal(rest, { root: opts.root, preset: opts.preset || 'rise' });
    }

    // Eén keer iets doen zodra el in beeld komt (meteen als er geen beweging is)
    function whenVisible(el, fn, root) {
        if (!el) return;
        if (!full() || typeof IntersectionObserver !== 'function') { fn(); return; }
        if (el._mdWatch) el._mdWatch.disconnect();
        const io = new IntersectionObserver((entries) => {
            if (!entries.some(e => e.isIntersecting)) return;
            io.disconnect();
            el._mdWatch = null;
            fn();
        }, { root: root || null, rootMargin: '0px 0px -8% 0px', threshold: 0.01 });
        el._mdWatch = io;
        io.observe(el);
    }

    // Een getal tikt naar zijn waarde: de eerste keer vanaf nul, daarna alleen bij een
    // verandering (vanaf de oude waarde). Een gelijk getal blijft gewoon staan.
    function tick(el, value, opts = {}) {
        if (!el || !Number.isFinite(value)) return;
        if (el._motionValue === undefined && opts.first === false) el._motionValue = value;
        M().countUp(el, value, opts);
    }

    // Nieuwe tekst op een vaste plek: hij komt zacht omhoog in plaats van te verspringen
    function swapText(el) {
        if (!el) return;
        const now = el.textContent;
        const before = el._mdText;
        el._mdText = now;
        if (before === undefined || before === now) return;
        M().animate(el, [
            { opacity: 0, transform: `translate3d(0, ${M().px('--motion-distance-sm', 6)}px, 0)` },
            { opacity: 1, transform: 'none' },
        ], { spring: 'smooth', key: 'md-swap' });
    }

    // Een selectie (achtergrond in ::before) glijdt verticaal van het oude naar het nieuwe item
    function glideY(fromEl, toEl, pseudo = '::before') {
        if (!fromEl || !toEl || fromEl === toEl || !full() || !fromEl.isConnected) return;
        const a = fromEl.getBoundingClientRect();
        const b = toEl.getBoundingClientRect();
        if (!a.height || !b.height) return;
        const dy = a.top - b.top;
        const sy = a.height / b.height;
        const sx = a.width / b.width;
        if (Math.abs(dy) < 1) return;
        M().animate(toEl, [
            { transform: `translate3d(${a.left - b.left}px, ${dy}px, 0) scale(${sx}, ${sy})`, transformOrigin: 'top left' },
            { transform: 'none', transformOrigin: 'top left' },
        ], { spring: 'snappy', pseudoElement: pseudo, key: 'md-glide', calm: false });
    }

    // Kijkt naar een lijst: verliest het ene item zijn selectie en krijgt een ander hem in
    // dezelfde beurt, dan glijdt de selectie mee
    function watchSelection(list, attr, isOn) {
        if (!list || list._mdSelect) return;
        list._mdSelect = new MutationObserver(records => {
            let lost = null, gained = null;
            records.forEach(r => {
                const el = r.target;
                const was = r.oldValue;
                const on = isOn(el);
                const wasOn = attr === 'class' ? (was || '').split(/\s+/).includes('active') : was === 'true';
                if (wasOn && !on) lost = el;
                if (!wasOn && on) gained = el;
            });
            if (lost && gained) glideY(lost, gained);
        });
        list._mdSelect.observe(list, { subtree: true, attributes: true, attributeOldValue: true, attributeFilter: [attr] });
    }

    // Kubische curve uit een token ('cubic-bezier(a, b, c, d)') -> [a, b, c, d]
    function curvePoints(name, fallback) {
        const m = /cubic-bezier\(([^)]+)\)/.exec(M().token(name) || '');
        const pts = m ? m[1].split(',').map(Number) : fallback;
        return pts.length === 4 && pts.every(Number.isFinite) ? pts : fallback;
    }

    // Bij een curve: op welk moment (0..1) is voortgang p bereikt?
    function timeAtProgress([x1, y1, x2, y2], p) {
        const bez = (a, b, s) => 3 * a * s * (1 - s) * (1 - s) + 3 * b * s * s * (1 - s) + s * s * s;
        let lo = 0, hi = 1;
        for (let i = 0; i < 24; i++) {
            const mid = (lo + hi) / 2;
            if (bez(y1, y2, mid) < p) lo = mid; else hi = mid;
        }
        return bez(x1, x2, (lo + hi) / 2);
    }

    // Een klein feest rond el (een icoon): een ring die uitdijt en een krans van stipjes.
    // Alleen voor een echte beloning (docs/MOTION.md: 'generous at rewards').
    // De laag staat los boven de pagina (position: fixed op de plek van el): schermen die zich
    // intussen opnieuw tekenen (DataGatherer bij elke taakstap) breken het feestje niet af.
    function overlayAt(el, className) {
        const r = el.getBoundingClientRect();
        const layer = document.createElement('div');
        layer.className = className;
        layer.setAttribute('aria-hidden', 'true');
        Object.assign(layer.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px`,
            borderRadius: getComputedStyle(el).borderTopLeftRadius });
        document.body.appendChild(layer);
        return layer;
    }

    function celebrate(el) {
        if (!el || !full() || !el.isConnected) return;
        const host = overlayAt(el, 'md-burst-layer');
        setTimeout(() => host.remove(), M().ms('--dur-6', 640) * 2.5);
        const ring = document.createElement('span');
        ring.className = 'md-burst-ring';
        ring.setAttribute('aria-hidden', 'true');
        host.appendChild(ring);
        const dur = M().ms('--dur-6', 640);
        M().animate(ring, [
            { opacity: 0.9, transform: 'scale(0.6)' },
            { opacity: 0, transform: 'scale(2.1)' },
        ], { duration: dur, easing: '--ease-out', fill: 'forwards', key: 'md-burst' }).finished.then(() => ring.remove(), () => ring.remove());
        const count = 10;
        for (let i = 0; i < count; i++) {
            const dot = document.createElement('span');
            dot.className = `md-burst-dot${i % 3 === 0 ? ' is-soft' : ''}`;
            dot.setAttribute('aria-hidden', 'true');
            host.appendChild(dot);
            const angle = (i / count) * Math.PI * 2 + 0.3;
            const dist = 26 + (i % 2) * 10;
            const x = Math.cos(angle) * dist, y = Math.sin(angle) * dist;
            M().animate(dot, [
                { opacity: 0, transform: 'translate3d(0, 0, 0) scale(0.4)' },
                { opacity: 1, transform: `translate3d(${x * 0.6}px, ${y * 0.6}px, 0) scale(1)`, offset: 0.35 },
                { opacity: 0, transform: `translate3d(${x}px, ${y}px, 0) scale(0.6)` },
            ], { duration: dur * 1.2, easing: '--ease-emphasized', delay: (i % 3) * M().ms('--stagger-step', 28), fill: 'forwards', key: 'md-burst' })
                .finished.then(() => dot.remove(), () => dot.remove());
        }
    }

    // Eén keer kort oplichten van een rand (::after met de accentrand)
    function glow(el) {
        if (!el || off() || !el.isConnected) return;
        const layer = overlayAt(el, 'md-glow-layer');
        M().animate(layer, [{ opacity: 0 }, { opacity: 1, offset: 0.25 }, { opacity: 0 }], {
            duration: M().ms('--dur-6', 640) * 1.6, easing: '--ease-out', fill: 'forwards', key: 'md-glow',
        }).finished.then(() => layer.remove(), () => layer.remove());
        if (Motion.off()) layer.remove();
    }

    // ==================== SPOTIFY ====================

    const HELIX_RUNGS = 24;

    // Het luister-DNA als een dubbele helix: 24 sporten, één per uur van de dag. Hoe vaker je
    // op dat uur luistert, hoe voller de stip en hoe steviger de sport. In Volledig draait hij
    // even rond als hij in beeld komt en opnieuw zolang je over de kaart beweegt.
    function dnaHelix(card, hourly) {
        if (!card || !Array.isArray(hourly) || hourly.length !== HELIX_RUNGS) return null;
        let svg = card.querySelector('.md-helix');
        const max = Math.max(...hourly, 1);
        const W = 184, H = 44, A = 15, cy = H / 2, pad = 6;
        const step = (W - 2 * pad) / (HELIX_RUNGS - 1);
        const turns = 2;    // twee volle slagen over de dag
        const NS = 'http://www.w3.org/2000/svg';
        if (!svg) {
            svg = document.createElementNS(NS, 'svg');
            svg.setAttribute('class', 'md-helix');
            svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
            svg.setAttribute('aria-hidden', 'true');
            svg.setAttribute('focusable', 'false');
            for (let i = 0; i < HELIX_RUNGS; i++) {
                const phase = (i / HELIX_RUNGS) * turns;               // in slagen (0..turns)
                const x = pad + i * step;
                const g = document.createElementNS(NS, 'g');
                g.setAttribute('class', 'md-helix-rung');
                g.style.setProperty('--i', i);
                // Een negatieve vertraging zet elke sport op zijn eigen plek in de draai
                g.style.setProperty('--phase', phase.toFixed(4));
                const line = document.createElementNS(NS, 'line');
                line.setAttribute('class', 'md-helix-bar');
                line.setAttribute('x1', x); line.setAttribute('x2', x);
                line.setAttribute('y1', cy - A); line.setAttribute('y2', cy + A);
                const top = document.createElementNS(NS, 'circle');
                top.setAttribute('class', 'md-helix-dot is-a');
                const bottom = document.createElementNS(NS, 'circle');
                bottom.setAttribute('class', 'md-helix-dot is-b');
                [top, bottom].forEach(c => { c.setAttribute('cx', x); c.setAttribute('cy', cy); });
                // Stilstaande houding (Rustig, Uit, voor de eerste draai): de projectie van de draai
                const y = -A * Math.cos(phase * Math.PI * 2);
                top.style.transform = `translateY(${y.toFixed(2)}px)`;
                bottom.style.transform = `translateY(${(-y).toFixed(2)}px)`;
                line.style.transform = `scaleY(${(y / A).toFixed(3)})`;
                g.append(line, top, bottom);
                svg.appendChild(g);
            }
            svg.style.setProperty('--amp', `${A}px`);
            card.appendChild(svg);
            card.addEventListener('pointerenter', () => spinHelix(svg, true));
            card.addEventListener('pointerleave', () => spinHelix(svg, false));
            card.addEventListener('focus', () => spinHelix(svg, true));
            card.addEventListener('blur', () => spinHelix(svg, false));
        }
        [...svg.querySelectorAll('.md-helix-rung')].forEach((g, i) => {
            const v = (hourly[i] || 0) / max;
            g.style.setProperty('--v', v.toFixed(3));
            g.querySelectorAll('circle').forEach(c => c.setAttribute('r', (1.3 + v * 2.4).toFixed(2)));
        });
        return svg;
    }

    // Draaien: zolang de muis erop staat, of één keer kort als de helix in beeld komt
    function spinHelix(svg, on, once = false) {
        if (!svg) return;
        clearTimeout(svg._mdStop);
        if (on && full()) {
            svg.classList.add('is-spinning');
            if (once) svg._mdStop = setTimeout(() => svg.classList.remove('is-spinning'), M().ms('--dur-6', 640) * 6);
        } else if (!on) {
            svg.classList.remove('is-spinning');
        }
    }

    // Groeien vanaf de basislijn (de eerste keer) of van de oude naar de nieuwe hoogte
    function chartBars(chart, values) {
        if (!chart) return;
        const bars = [...chart.querySelectorAll('.chart-bar')];
        const max = Math.max(...values, 1);
        const before = chart._mdValues;
        const beforeMax = chart._mdMax;
        chart._mdValues = values.slice();
        chart._mdMax = max;
        if (off() || !bars.length) return;
        // Een golf over alle staafjes: samen niet langer dan --stagger-max stappen
        const spread = M().ms('--stagger-step', 28) * (parseInt(M().token('--stagger-max'), 10) || 10);
        const step = spread / bars.length;
        if (!before) {
            chart.dataset.mdPending = '';
            whenVisible(chart, () => {
                delete chart.dataset.mdPending;
                bars.forEach((bar, i) => M().animate(bar, [
                    { transform: 'scaleY(0)', opacity: 0 }, { transform: 'scaleY(1)', opacity: 1 },
                ], { spring: 'smooth', delay: i * step, key: 'md-bar' }));
                M().stagger(chart.nextElementSibling ? [chart.nextElementSibling] : [], 'fade', { delay: spread });
            });
            return;
        }
        if (before.join() === values.join()) return;
        bars.forEach((bar, i) => {
            const h0 = (before[i] || 0) / (beforeMax || 1);
            const h1 = (values[i] || 0) / max;
            if (h1 <= 0.001) {
                M().animate(bar, [{ opacity: 0 }, { opacity: 1 }], { duration: '--dur-3', key: 'md-bar' });
                return;
            }
            M().animate(bar, [
                { transform: `scaleY(${Math.max(0.001, h0 / h1).toFixed(4)})` }, { transform: 'scaleY(1)' },
            ], { spring: 'smooth', delay: i * step * 0.5, key: 'md-bar' });
        });
    }

    // Een dun staafje onder elke regel van een toplijst: zijn aandeel ten opzichte van #1
    function shareBars(list, values) {
        if (!list) return;
        const items = [...list.querySelectorAll('.spotify-list-item')];
        const max = Math.max(...values, 1);
        items.forEach((item, i) => {
            let bar = item.querySelector('.md-share');
            if (!bar) {
                bar = document.createElement('span');
                bar.className = 'md-share';
                bar.setAttribute('aria-hidden', 'true');
                item.appendChild(bar);
            }
            bar.style.setProperty('--share', ((values[i] || 0) / max).toFixed(4));
        });
        return items;
    }

    function listKey(list) {
        return list ? [...list.querySelectorAll('.item-name')].map(n => n.textContent).join('|') : '';
    }

    // Een lijst die nieuw is (of andere inhoud kreeg) komt in ritme binnen; de staafjes groeien mee
    function spotifyList(list, values) {
        if (!list) return;
        const items = shareBars(list, values) || [];
        const key = listKey(list);
        if (list._mdKey === key) return;
        list._mdKey = key;
        if (off()) return;
        whenVisible(list, () => {
            M().stagger(items.slice(0, 10), 'rise', { key: 'md-arrive' });
            items.forEach((item, i) => {
                const bar = item.querySelector('.md-share');
                const share = parseFloat(bar.style.getPropertyValue('--share')) || 0;
                M().animate(bar, [{ transform: 'scaleX(0)' }, { transform: `scaleX(${share})` }], {
                    spring: 'gentle', delay: Math.min(i, 10) * M().ms('--stagger-step', 28) + M().ms('--dur-2', 140), key: 'md-share',
                });
            });
        });
    }

    function spotifyRendered(data) {
        const content = document.getElementById('spotify-content');
        if (!content || !data) return;
        // Dezelfde gegevens nog eens (terug naar Spotify, uit de cache): niets te bewegen
        if (content._mdData === data) return;
        content._mdData = data;
        const first = !content._mdSeen;
        content._mdSeen = true;

        // Getallen: tellen de eerste keer op, daarna alleen bij een ander jaar
        tick(document.getElementById('stat-minutes'), data.total_minutes);
        tick(document.getElementById('stat-plays'), data.total_plays);
        const record = data.new_insights && data.new_insights.record_day;
        const hours = document.querySelector('#stat-record-day-hours .stat-number');
        if (record && hours) tick(hours, record.hours, { decimals: 1 });
        ['stat-top-artist', 'stat-dna', 'stat-top-binge', 'stat-mainstream-score', 'stat-seasonal-mood']
            .forEach(id => swapText(document.getElementById(id)));

        // De DNA-helix in de kaart Luister-DNA
        const helix = dnaHelix(content.querySelector('.listening-personality'), data.hourly_activity);
        if (first && helix && full()) {
            helix.classList.add('is-drawing');
            whenVisible(helix, () => {
                const rungs = [...helix.querySelectorAll('.md-helix-rung')];
                const step = M().ms('--stagger-step', 28) * 0.6;
                rungs.forEach((g, i) => M().animate(g, [{ opacity: 0 }, { opacity: 1 }], {
                    duration: '--dur-4', easing: '--ease-out', delay: M().ms('--dur-3', 200) + i * step, key: 'md-helix',
                }));
                helix.classList.remove('is-drawing');
                spinHelix(helix, true, true);
            });
        }

        // De kaarten bovenaan: rij na rij, de eerste keer
        // De eerste keer komt de hele pagina in ritme binnen: de kaarten één voor één, daarna
        // de blokken eronder (wat onder de vouw staat pas als het in beeld scrolt)
        if (first) {
            const parts = [];
            [...content.children].forEach(block => {
                if (block.matches('.spotify-stats-grid')) parts.push(...block.children);
                else if (block.getClientRects().length) parts.push(block);
            });
            arrive(parts);
        }

        spotifyList(document.getElementById('top-artists-list'), (data.top_artists || []).map(a => a.ms));
        spotifyList(document.getElementById('top-tracks-list'), (data.top_tracks || []).map(t => t.count));
        const discoveries = data.new_insights && data.new_insights.discoveries;
        if (discoveries) spotifyList(document.getElementById('discoveries-list'), discoveries.map(d => d.ms));

        const otd = document.getElementById('on-this-day-list');
        if (otd && otd._mdKey !== otd.textContent) {
            otd._mdKey = otd.textContent;
            whenVisible(otd, () => M().stagger(otd.querySelectorAll('.on-this-day-item'), 'rise', { key: 'md-arrive' }));
        }

        chartBars(document.getElementById('monthly-chart'), data.monthly_activity || []);
        chartBars(document.getElementById('hourly-chart'), data.hourly_activity || []);
    }

    // ==================== KAART: de route van een dag ====================

    // De lijn tekent zich langs het pad (stroke-dashoffset), met een lichtpuntje voorop
    // (SVG animateMotion op hetzelfde pad), en elke bezochte plek duikt op zodra de lijn er is.
    // Zoom of verschuif je tijdens het tekenen, dan staat de route er meteen helemaal.
    function mapRoute(map, line, visits) {
        if (!map) return;
        const path = line && line.getElement ? line.getElement() : null;
        const dots = (visits || []).map(v => (v.getElement ? v.getElement() : null)).filter(Boolean);
        if (!path && !dots.length) return;
        if (!full()) {
            M().animate(path, [{ opacity: 0 }, { opacity: 1 }], { duration: '--dur-4', key: 'md-route' });
            dots.forEach(d => M().animate(d, [{ opacity: 0 }, { opacity: 1 }], { duration: '--dur-4', key: 'md-route' }));
            return;
        }
        if (map._mdRoute) map._mdRoute();
        const hide = [path, ...dots].filter(Boolean);
        hide.forEach(el => { el.style.opacity = '0'; });

        let anims = [];
        let head = null;
        let started = false;
        let done = false;
        const finish = () => {
            if (done) return;
            done = true;
            map.off('zoomstart movestart', interrupt);
            if (map._mdRoute === stop) map._mdRoute = null;
            hide.forEach(el => { el.style.opacity = ''; });
            if (path) path.style.strokeDasharray = '';
            if (head && head.isConnected) head.remove();
        };
        const stop = () => { anims.forEach(a => { try { a.cancel(); } catch (_) { /* al weg */ } }); finish(); };
        const interrupt = () => { if (started) stop(); };
        map._mdRoute = stop;

        const start = () => {
            if (started || done) return;
            started = true;
            map.on('zoomstart movestart', interrupt);
            // --ease-standard: op gang komen en zacht aankomen, zodat je de lijn ziet reizen
            const curve = curvePoints('--ease-standard', [0.2, 0, 0, 1]);
            const easing = M().token('--ease-standard') || 'ease-out';
            let len = 0;
            if (path && path.isConnected && typeof path.getTotalLength === 'function') len = path.getTotalLength();
            // Langer pad, iets langer onderweg: tussen --dur-6 en drie keer zo lang
            const base = M().ms('--dur-6', 640);
            const dur = Math.round(Math.min(base * 3, Math.max(base * 1.5, len * 1.6)));
            if (path && len > 0) {
                path.style.strokeDasharray = `${len} ${len}`;
                path.style.opacity = '';
                anims.push(path.animate([{ strokeDashoffset: len }, { strokeDashoffset: 0 }], { duration: dur, easing, fill: 'both' }));
                // Het lichtpuntje voorop
                const d = path.getAttribute('d');
                const svgNS = 'http://www.w3.org/2000/svg';
                if (d && path.parentNode) {
                    head = document.createElementNS(svgNS, 'circle');
                    head.setAttribute('r', '6');
                    head.setAttribute('class', 'md-route-head');
                    const motion = document.createElementNS(svgNS, 'animateMotion');
                    motion.setAttribute('dur', `${dur}ms`);
                    motion.setAttribute('fill', 'freeze');
                    motion.setAttribute('begin', 'indefinite');
                    motion.setAttribute('calcMode', 'spline');
                    motion.setAttribute('keyPoints', '0;1');
                    motion.setAttribute('keyTimes', '0;1');
                    motion.setAttribute('keySplines', curve.join(' '));
                    motion.setAttribute('path', d);
                    head.appendChild(motion);
                    path.parentNode.appendChild(head);
                    try { motion.beginElement(); } catch (_) { head.remove(); head = null; }
                    if (head) {
                        anims.push(head.animate([{ opacity: 1 }, { opacity: 1, offset: 0.9 }, { opacity: 0 }], { duration: dur + M().ms('--dur-3', 200), fill: 'both' }));
                    }
                }
            }
            // De plekken: op het moment dat de lijn ze bereikt
            const pts = path && line.getLatLngs ? line.getLatLngs().map(ll => map.latLngToLayerPoint(ll)) : [];
            const cum = [0];
            for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + pts[i].distanceTo(pts[i - 1]));
            const total = cum[cum.length - 1] || 0;
            dots.forEach((dot, i) => {
                let at = dots.length > 1 ? i / (dots.length - 1) : 1;
                const v = visits[i];
                if (total > 0 && v && v.getLatLng) {
                    const p = map.latLngToLayerPoint(v.getLatLng());
                    let best = 0, bestD = Infinity;
                    pts.forEach((q, j) => { const dd = q.distanceTo(p); if (dd < bestD) { bestD = dd; best = j; } });
                    at = cum[best] / total;
                }
                const delay = total > 0 ? timeAtProgress(curve, Math.min(1, at)) * dur : i * M().ms('--stagger-step', 28) * 3;
                dot.style.opacity = '';
                anims.push(M().animate(dot, [{ opacity: 0, transform: 'scale(0.2)' }, { opacity: 1, transform: 'none' }],
                    { spring: 'bouncy', delay, key: 'md-route' }));
            });
            Promise.all(anims.map(a => a.finished.catch(() => {}))).then(() => {
                anims.forEach(a => { try { if (a.effect && a.effect.target === path) a.cancel(); } catch (_) { /* weg */ } });
                finish();
            });
        };
        // Eerst de kaart op zijn plek (fitBounds kan nog zoomen of schuiven), dan tekenen
        const moving = map._animatingZoom || (map._panAnim && map._panAnim._inProgress);
        if (moving) {
            map.once('moveend', () => requestAnimationFrame(start));
            setTimeout(() => { if (!started) start(); }, M().ms('--dur-5', 420) + M().ms('--dur-3', 200));
        } else {
            requestAnimationFrame(start);
        }
    }

    // ==================== DATAGATHERER ====================

    const dgMemory = new Map();      // bron -> {busy, label, records, pct}
    const dgPendingParty = new Set();   // bronnen waarvan een taak net klaar is

    function dgCardState(card) {
        const fill = card.querySelector('.dg-progress .progress-fill');
        const pct = fill && fill.style.width ? parseFloat(fill.style.width) : null;
        const status = card.querySelector('.dg-status');
        return { busy: card.classList.contains('is-busy'), label: status ? status.textContent.trim() : '', pct };
    }

    // Voortgang loopt door van waar hij was (de kaart wordt bij elke stap opnieuw getekend)
    function progressFrom(oldPct, progress) {
        if (!progress || oldPct === null || oldPct === undefined || off()) return;
        const fill = progress.querySelector('.progress-fill');
        const now = fill && fill.style.width ? parseFloat(fill.style.width) : null;
        if (!fill || now === null || !now || Math.abs(now - oldPct) < 0.2) return;
        M().animate(fill, [
            { transform: `scaleX(${Math.max(0, oldPct / now).toFixed(4)})`, transformOrigin: 'left center' },
            { transform: 'none', transformOrigin: 'left center' },
        ], { duration: '--dur-5', easing: '--ease-standard', key: 'md-progress' });
    }

    function dgProgress(oldEl, newEl) {
        if (!oldEl || !newEl) return;
        const fill = oldEl.querySelector('.progress-fill');
        const pct = fill && fill.style.width ? parseFloat(fill.style.width) : null;
        progressFrom(pct, newEl);
        const a = oldEl.querySelector('.dg-progress-text');
        const b = newEl.querySelector('.dg-progress-text');
        if (a && b && a.textContent !== b.textContent && full()) {
            M().animate(b, [{ opacity: 0.35 }, { opacity: 1 }], { duration: '--dur-3', key: 'md-swap' });
        }
    }

    function dgRendered(box, opts = {}) {
        if (!box) return;
        const cards = [...box.querySelectorAll('.dg-card')];
        const fresh = !box._mdSeen || box._mdFilter !== opts.filter;
        box._mdSeen = true;
        box._mdFilter = opts.filter;
        const records = new Map((opts.sources || []).map(s => [s.id, s.records || 0]));

        if (fresh) {
            const parts = [];
            box.querySelectorAll('.dg-group').forEach(group => {
                const title = group.querySelector('.dg-group-title');
                if (title) parts.push(title);
                parts.push(...group.querySelectorAll('.dg-card'));
            });
            arrive(parts);
            // De dekking van elke bron groeit van links naar rechts, als hij in beeld komt
            cards.forEach(card => {
                const segs = [...card.querySelectorAll('.dg-coverage-seg')];
                if (!segs.length || !full()) return;
                whenVisible(card, () => segs.forEach((seg, i) => M().animate(seg, [
                    { transform: 'scaleX(0)', transformOrigin: 'left center' }, { transform: 'none', transformOrigin: 'left center' },
                ], { duration: '--dur-6', easing: '--ease-emphasized', delay: M().ms('--dur-3', 200) + i * M().ms('--stagger-step', 28), key: 'md-cov' })));
            });
        }

        cards.forEach(card => {
            const id = card.dataset.source;
            const now = dgCardState(card);
            const before = dgMemory.get(id);
            const count = records.get(id) || 0;
            const recordEl = card.querySelector('.dg-records .mono');
            if (before && !fresh) {
                if (now.busy && before.pct !== null) progressFrom(before.pct, card.querySelector('.dg-progress'));
                if (before.label !== now.label) {
                    M().spring(card.querySelector('.dg-status'), [{ opacity: 0, transform: 'scale(0.7)' }, { opacity: 1, transform: 'none' }],
                        dgPendingParty.has(id) ? 'bouncy' : 'snappy', { key: 'md-pop' });
                }
                if (recordEl && before.records !== count) tick(recordEl, count, { from: before.records });
            }
            if (recordEl) recordEl._motionValue = count;
            if (dgPendingParty.has(id) && !now.busy) {
                dgPendingParty.delete(id);
                celebrate(card.querySelector('.dg-card-icon'));
                glow(card);
            }
            dgMemory.set(id, { ...now, records: count });
        });

        // Getallen bovenaan: de eerste keer optellen, daarna alleen bij een verandering
        document.querySelectorAll('#dg-summary .dg-summary-item .mono').forEach((el, i) => {
            const value = parseInt(el.textContent.replace(/\D/g, ''), 10);
            const key = `summary-${i}`;
            const prev = dgMemory.get(key);
            if (Number.isFinite(value)) tick(el, value, prev === undefined ? {} : { from: prev });
            dgMemory.set(key, value);
        });
    }

    // Een taak van een bron is klaar: na het volgende tekenen krijgt die kaart zijn feestje
    function hookJobs() {
        if (typeof window.jobsOnFinished !== 'function') return;
        window.jobsOnFinished((job) => {
            if (!job || !job.type || !job.type.startsWith('datagatherer.') || job.status !== 'done') return;
            const source = (job.params && job.params.source) || job.type.split('.').pop();
            dgPendingParty.add(source);
            setTimeout(() => dgPendingParty.delete(source), 15000);
        });
    }

    // ==================== BERICHTEN ====================

    function msgList(list) {
        if (!list) return;
        const items = [...list.querySelectorAll('.msg-item, .msg-list-heading, .empty-state')];
        const key = items.map(el => el.dataset.conv || el.textContent.slice(0, 24)).join('|');
        if (list._mdKey === key) return;
        list._mdKey = key;
        // Zoeken (je typt in het zoekveld): de resultaten staan er meteen, met een korte fade
        // en zonder ritme, zodat typen nooit op beweging wacht (docs/MOTION.md, principe 5)
        if (document.activeElement && document.activeElement.id === 'msg-search') {
            M().animate(list, [{ opacity: 0.4 }, { opacity: 1 }], { duration: '--dur-2', easing: '--ease-out', key: 'md-arrive' });
        } else {
            arrive(items, { root: list });
        }
        watchSelection(list, 'class', el => el.classList && el.classList.contains('msg-item') && !el.dataset.hit && el.classList.contains('active'));
    }

    // Een geopend gesprek: de berichten die je ziet stromen in ritme binnen, van boven naar
    // onder; nieuwe oudere berichten (bovenaan geladen) pas als je er naartoe scrolt
    function msgThread(pane, opts = {}) {
        if (!pane || off()) return;
        const scroller = pane.querySelector('.msg-scroll');
        const header = pane.querySelector('.msg-thread-header');
        const rows = scroller ? [...scroller.querySelectorAll('.msg-row, .msg-day')] : [];
        if (opts.older) {
            const known = scroller && scroller._mdKnown;
            const fresh = known ? rows.filter(r => !known.has(r.dataset.msg || r)) : [];
            if (scroller) scroller._mdKnown = new Set(rows.map(r => r.dataset.msg || r));
            const above = fresh.filter(r => r.getBoundingClientRect().bottom < scroller.getBoundingClientRect().top);
            if (above.length) M().reveal(above.slice(-REVEAL_CAP), { root: scroller, preset: 'fade' });
            return;
        }
        if (scroller) scroller._mdKnown = new Set(rows.map(r => r.dataset.msg || r));
        if (header) M().enter(header, 'slide', { args: [-1, M().px('--motion-distance-sm', 6)], key: 'md-arrive' });
        const seen = visibleIn(rows, scroller);
        const bubble = (el) => {
            if (el.classList.contains('msg-day')) return [{ opacity: 0 }, { opacity: 1 }];
            const d = M().px('--motion-distance-md', 12);
            return [{ opacity: 0, transform: `translate3d(0, ${d}px, 0) scale(0.94)` }, { opacity: 1, transform: 'none' }];
        };
        const step = M().ms('--stagger-step', 28);
        seen.forEach((row, i) => M().animate(row, bubble(row), { spring: 'smooth', delay: Math.min(i, 10) * step, key: 'md-arrive' }));
        const hit = opts.highlight ? scroller.querySelector('.msg-row.is-hit .msg-bubble') : null;
        if (hit && full()) {
            hit.classList.add('md-spotlight');
            M().animate(hit, [{ opacity: 0 }, { opacity: 1, offset: 0.3 }, { opacity: 0 }], {
                duration: M().ms('--dur-6', 640) * 2, easing: '--ease-out', delay: Math.min(seen.length, 10) * step + M().ms('--dur-4', 280),
                pseudoElement: '::after', key: 'md-glow',
            }).finished.then(() => hit.classList.remove('md-spotlight'), () => hit.classList.remove('md-spotlight'));
        }
    }

    // ==================== ACTIVITEIT ====================

    function actDays(box) {
        if (!box) return;
        const panel = box.closest('.act-days-panel') || null;
        const items = [...box.children];
        const key = items.length + '|' + (items[1] ? items[1].textContent : '');
        if (box._mdKey === key) return;
        box._mdKey = key;
        arrive(items, { root: panel });
        watchSelection(box, 'aria-pressed', el => el.getAttribute && el.getAttribute('aria-pressed') === 'true');
    }

    // De dag tekent zich als een tijdlijn: de lijn loopt naar beneden en elke gebeurtenis
    // komt binnen als de lijn er langs komt; het icoontje springt op zijn plek
    function actEvents(box) {
        if (!box || off()) return;
        const list = box.querySelector('.act-list');
        const head = box.querySelector('.act-events-head');
        if (head) M().enter(head, 'fade', { duration: '--dur-3', easing: '--ease-out', key: 'md-arrive' });
        if (!list) return;
        const events = [...list.children];
        const seen = visibleIn(events);
        const step = M().ms('--stagger-step', 28) * 1.5;
        if (full()) {
            M().animate(list, [{ transform: 'scaleY(0)' }, { transform: 'scaleY(1)' }], {
                duration: M().ms('--dur-6', 640) + Math.min(seen.length, 10) * step, easing: '--ease-emphasized',
                pseudoElement: '::before', key: 'md-spine',
            });
        }
        seen.forEach((ev, i) => {
            const delay = Math.min(i, 10) * step;
            M().animate(ev, M().presets.rise(M().px('--motion-distance-sm', 6)), { spring: 'smooth', delay, key: 'md-arrive' });
            const icon = ev.querySelector('.act-icon');
            if (icon && full()) M().animate(icon, [{ transform: 'scale(0.4)', opacity: 0 }, { transform: 'none', opacity: 1 }], { spring: 'snappy', delay: delay + M().ms('--dur-1', 90), key: 'md-pop' });
        });
        const after = seen.length ? events.indexOf(seen[seen.length - 1]) + 1 : 0;
        const rest = events.slice(after, after + REVEAL_CAP);
        if (rest.length) M().reveal(rest, {});
    }

    // ==================== PASHA ====================

    const pasha = { listenTimer: null };

    function mascots() {
        return [...document.querySelectorAll('.chat-mascot')];
    }

    function setMood(mood, on) {
        mascots().forEach(m => m.classList.toggle(`is-${mood}`, on));
    }

    // Ademen: rustig op en neer na elk contact (tien keer, een halve minuut), daarna ligt hij
    // stil op zijn rustpunt tot het volgende contact. Nooit eindeloos.
    function wake() {
        if (!full()) return;
        mascots().forEach(m => {
            if (!m._mdBreath) {
                m._mdBreath = true;
                m.addEventListener('animationend', (e) => { if (e.animationName === 'md-breathe') m.classList.remove('is-awake'); });
            }
            m.classList.add('is-awake');
        });
    }

    // Een kort knikje (een antwoord komt binnen) of het kopje laten hangen (een fout)
    function nod(kind = 'nod') {
        if (!full()) return;
        const frames = kind === 'nod'
            ? [{ transform: 'none' }, { transform: 'translate3d(0, 3px, 0) rotate(-4deg)', offset: 0.35 }, { transform: 'none' }]
            : [{ transform: 'none' }, { transform: 'translate3d(0, 3px, 0) rotate(8deg) scale(0.96)', offset: 0.4 }, { transform: 'translate3d(0, 1px, 0) rotate(5deg)', offset: 0.7 }, { transform: 'none' }];
        mascots().forEach(m => M().animate(m, frames, { spring: kind === 'nod' ? 'bouncy' : 'gentle', key: 'nod',
            duration: kind === 'nod' ? undefined : M().ms('--dur-6', 640) * 1.6 }));
    }

    function bubbleIn(msg) {
        if (!msg || off()) return;
        const mine = msg.classList.contains('user-message');
        const d = M().px('--motion-distance-sm', 6);
        msg.style.transformOrigin = mine ? 'bottom right' : 'bottom left';
        M().animate(msg, [
            { opacity: 0, transform: `translate3d(${mine ? d * 2 : -d}px, ${d * 1.5}px, 0) scale(0.92)` },
            { opacity: 1, transform: 'none' },
        ], { spring: mine ? 'snappy' : 'smooth', key: 'chat-in' });
        // Foto's in een antwoord: in ritme, hooguit acht
        const photos = msg.querySelectorAll('.chat-photo, .chat-strip-item');
        if (photos.length) M().stagger([...photos].slice(0, 8), 'scale', { delay: M().ms('--dur-2', 140), key: 'md-arrive' });
        actionCard(msg);
    }

    // Een actie- of bevestigingskaart van Pasha vouwt zich open: titel, regels, de uitleg en de
    // knoppen in ritme; het schildje van een bevestiging klikt op zijn plek
    function actionCard(msg) {
        const card = msg.querySelector('.chat-confirm, .chat-tool');
        if (!card || !full()) return;
        const parts = [card.querySelector('.chat-tool-title'),
            ...[...card.querySelectorAll('.chat-confirm-list > li, .chat-tool-lines > li')].slice(0, 6),
            card.querySelector('.chat-confirm-note'), card.querySelector('.chat-confirm-actions'), card.querySelector('.chat-tool-link')];
        M().stagger(parts.filter(Boolean), 'rise', { delay: M().ms('--dur-2', 140), key: 'md-arrive' });
        const shield = card.querySelector('.chat-tool-title > i');
        if (shield) M().spring(shield, M().presets.pop(), 'snappy', { delay: M().ms('--dur-4', 280), key: 'md-pop' });
    }

    // Uitvoeren of annuleren in een bevestigingskaart: een vinkje bij succes, een schudje bij een fout
    function confirmSettled(status) {
        const card = status.closest('.chat-confirm');
        if (!card || card._mdSettled) return;
        card._mdSettled = true;
        const failed = status.classList.contains('chat-error-text');
        const cancelled = /^Geannuleerd/.test(status.textContent);
        if (!failed && !cancelled) {
            const check = document.createElement('i');
            check.className = 'fas fa-circle-check md-confirm-check';
            check.setAttribute('aria-hidden', 'true');
            status.prepend(check, ' ');
            M().spring(check, M().presets.pop(), 'bouncy', { key: 'md-pop' });
            glow(card.closest('.message-bubble') || card);
            nod('nod');
        } else if (failed && full()) {
            const d = M().px('--motion-distance-xs', 2) * 2;
            M().animate(card, [
                { transform: 'none' }, { transform: `translate3d(${-d}px, 0, 0)`, offset: 0.2 }, { transform: `translate3d(${d}px, 0, 0)`, offset: 0.45 },
                { transform: `translate3d(${-d / 2}px, 0, 0)`, offset: 0.7 }, { transform: 'none' },
            ], { duration: '--dur-5', easing: '--ease-out', key: 'md-shake' });
            nod('sad');
        }
        M().animate(status, [{ opacity: 0 }, { opacity: 1 }], { duration: '--dur-3', key: 'md-swap' });
    }

    function watchChat() {
        const box = document.getElementById('chat-messages');
        if (!box || box._mdWatch) return;
        let thinking = false;
        const sync = () => {
            const loading = !!box.querySelector('.message-loading');
            if (loading !== thinking) {
                thinking = loading;
                setMood('thinking', loading);
                if (!loading) {
                    const last = box.lastElementChild;
                    const bubble = last && last.querySelector('.message-bubble');
                    if (bubble && bubble.querySelector('.chat-error-text')) nod('sad');
                    else nod('nod');
                    if (bubble && bubble.classList.contains('message-text')) bubble.classList.add('md-streaming');
                }
            }
        };
        box._mdWatch = new MutationObserver(records => {
            records.forEach(r => {
                r.addedNodes.forEach(node => {
                    if (node.nodeType !== 1) return;
                    if (node.parentElement === box && node.classList.contains('chat-message')) { bubbleIn(node); wake(); }
                    if (node.parentElement && node.parentElement.classList.contains('chat-confirm-status')) confirmSettled(node.parentElement);
                });
                if (r.type === 'childList' && r.target.classList && r.target.classList.contains('chat-confirm-status') && r.target.textContent) {
                    confirmSettled(r.target);
                }
                if (r.target.classList && r.target.classList.contains('message-bubble') && r.target.querySelector('.chat-error-text')) {
                    r.target.classList.remove('md-streaming');
                }
            });
            sync();
        });
        box._mdWatch.observe(box, { childList: true, subtree: true });

        // Typen: Pasha spitst zijn oren (kopje schuin); klaar met streamen: de cursor gaat weg
        const inputs = ['chat-input', 'chat-input-fullscreen'].map(id => document.getElementById(id)).filter(Boolean);
        inputs.forEach(input => {
            input.addEventListener('input', () => {
                if (!full()) return;
                wake();
                setMood('listening', !!input.value.trim());
                clearTimeout(pasha.listenTimer);
                pasha.listenTimer = setTimeout(() => setMood('listening', false), 1200);
            });
            input.addEventListener('blur', () => setMood('listening', false));
            new MutationObserver(() => {
                if (!input.disabled) {
                    box.querySelectorAll('.md-streaming').forEach(b => b.classList.remove('md-streaming'));
                } else {
                    setMood('listening', false);
                }
            }).observe(input, { attributes: true, attributeFilter: ['disabled'] });
        });
        wake();
    }

    // ==================== Opstarten ====================

    // De kaart komt in beeld: even .md-map-arriving, zodat de groepen dan opploffen (CSS)
    function watchMapEntry() {
        const section = document.getElementById('section-map');
        const canvas = document.getElementById('map-canvas');
        if (!section || !canvas) return;
        new MutationObserver(() => {
            const on = section.classList.contains('active');
            if (on === section._mdOn) return;
            section._mdOn = on;
            clearTimeout(canvas._mdArrive);
            canvas.classList.toggle('md-map-arriving', on && full());
            if (on) canvas._mdArrive = setTimeout(() => canvas.classList.remove('md-map-arriving'), M().ms('--dur-6', 640) * 2.5);
        }).observe(section, { attributes: true, attributeFilter: ['class'] });
    }

    function ready() {
        hookJobs();
        watchChat();
        watchMapEntry();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready);
    else ready();

    window.MotionData = {
        spotifyRendered,
        mapRoute,
        dgRendered,
        dgProgress,
        msgList,
        msgThread,
        actDays,
        actEvents,
        celebrate,
        // Kleine helpers voor andere schermen: tick (getal tikt alleen bij verandering), swapText (nieuwe
        // tekst komt zacht omhoog), glideY (selectie in ::before glijdt mee), arrive (zichtbaar in
        // ritme, de rest bij scrollen, met plafond), whenVisible (één keer zodra iets in beeld komt)
        tick,
        swapText,
        glideY,
        arrive,
        whenVisible,
    };
})();
