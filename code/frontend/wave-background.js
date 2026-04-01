/**
 * Vanilla JS Wave Background for Sidebar
 * Animated SVG waves with simplex noise and mouse interaction
 */

class WaveBackground {
    constructor(container) {
        this.container = container;
        this.svg = null;
        this.paths = [];
        this.lines = [];
        this.mouse = {
            x: -10,
            y: 0,
            lx: 0,
            ly: 0,
            sx: 0,
            sy: 0,
            v: 0,
            vs: 0,
            a: 0,
            set: false
        };
        this.noiseGen = null;
        this.rafId = null;
        this.bounding = null;

        this.init();
    }

    init() {
        // Create simplex noise generator (simple implementation)
        this.noiseGen = this.createSimplexNoise();

        // Create SVG element
        this.createSVG();

        // Set initial size
        this.setSize();

        // Create wave lines
        this.setLines();

        // Bind events
        // Use ResizeObserver for more robust size tracking (works for sidebar resize and visibility changes)
        this.resizeObserver = new ResizeObserver(() => this.onResize());
        this.resizeObserver.observe(this.container);

        this.container.addEventListener('mousemove', (e) => this.onMouseMove(e));

        // Start animation
        this.tick(0);
    }

    createSVG() {
        this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        this.svg.classList.add('wave-background-svg');
        this.svg.style.position = 'absolute';
        this.svg.style.top = '0';
        this.svg.style.left = '0';
        this.svg.style.width = '100%';
        this.svg.style.height = '100%';
        this.svg.style.pointerEvents = 'none';
        this.svg.style.zIndex = '0';
        // Opacity will be controlled by CSS or set here, stick to user preference
        this.svg.style.opacity = '0.4';

        // Ensure it's the first child so it's behind content
        if (this.container.firstChild) {
            this.container.insertBefore(this.svg, this.container.firstChild);
        } else {
            this.container.appendChild(this.svg);
        }
    }

    setSize() {
        this.bounding = this.container.getBoundingClientRect();
    }

    setLines() {
        if (!this.svg || !this.bounding) return;

        const { width, height } = this.bounding;
        this.lines = [];

        // Clear existing paths
        this.paths.forEach(path => path.remove());
        this.paths = [];

        // Spacing for wave lines - tighter for more coverage
        const xGap = 10;
        const yGap = 10;

        // Extend to full width with padding
        const totalLines = Math.ceil(width / xGap) + 4;  // Extra lines for full coverage
        const totalPoints = Math.ceil(height / yGap) + 2;

        const xStart = -20;  // Start before left edge
        const yStart = -10;  // Start before top edge

        // Create vertical wave lines with varying styles
        for (let i = 0; i < totalLines; i++) {
            const points = [];

            for (let j = 0; j < totalPoints; j++) {
                points.push({
                    x: xStart + xGap * i,
                    y: yStart + yGap * j,
                    wave: { x: 0, y: 0 },
                    cursor: { x: 0, y: 0, vx: 0, vy: 0 }
                });
            }

            // Create path with artistic variation
            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('fill', 'none');

            // Vary stroke width and opacity for artistic effect
            const variation = Math.sin(i * 0.3) * 0.5 + 0.5;  // 0 to 1
            const strokeWidth = 0.3 + variation * 0.6;  // 0.3 to 0.9
            const opacity = 0.1 + variation * 0.15;  // 0.1 to 0.25

            path.setAttribute('stroke', 'var(--accent)');
            path.setAttribute('stroke-width', strokeWidth.toString());
            path.setAttribute('opacity', opacity.toString());

            // Add subtle glow effect to some lines
            if (i % 5 === 0) {
                path.style.filter = 'drop-shadow(0 0 2px var(--accent))';
            }

            this.svg.appendChild(path);
            this.paths.push(path);
            this.lines.push(points);
        }
    }

    onResize() {
        this.setSize();
        this.setLines();
    }

    onMouseMove(e) {
        if (!this.bounding) return;

        this.mouse.x = e.clientX - this.bounding.left;
        this.mouse.y = e.clientY - this.bounding.top;

        if (!this.mouse.set) {
            this.mouse.sx = this.mouse.x;
            this.mouse.sy = this.mouse.y;
            this.mouse.lx = this.mouse.x;
            this.mouse.ly = this.mouse.y;
            this.mouse.set = true;
        }
    }

    movePoints(time) {
        this.lines.forEach(points => {
            points.forEach(p => {
                // Wave movement with noise - faster waves
                const move = this.noiseGen(
                    (p.x + time * 0.012) * 0.004,
                    (p.y + time * 0.005) * 0.003
                ) * 8;

                p.wave.x = Math.cos(move) * 10;
                p.wave.y = Math.sin(move) * 5;

                // Mouse effect - MORE REACTIVE
                const dx = p.x - this.mouse.sx;
                const dy = p.y - this.mouse.sy;
                const d = Math.hypot(dx, dy);
                const l = Math.max(250, this.mouse.vs);

                if (d < l) {
                    const s = 1 - d / l;
                    const f = Math.cos(d * 0.01) * s;

                    p.cursor.vx += Math.cos(this.mouse.a) * f * l * this.mouse.vs * 0.0004;
                    p.cursor.vy += Math.sin(this.mouse.a) * f * l * this.mouse.vs * 0.0004;
                }

                p.cursor.vx += (0 - p.cursor.x) * 0.015;
                p.cursor.vy += (0 - p.cursor.y) * 0.015;

                p.cursor.vx *= 0.88;
                p.cursor.vy *= 0.88;

                p.cursor.x += p.cursor.vx;
                p.cursor.y += p.cursor.vy;

                p.cursor.x = Math.min(60, Math.max(-60, p.cursor.x));
                p.cursor.y = Math.min(60, Math.max(-60, p.cursor.y));
            });
        });
    }

    moved(point, withCursor = true) {
        return {
            x: point.x + point.wave.x + (withCursor ? point.cursor.x : 0),
            y: point.y + point.wave.y + (withCursor ? point.cursor.y : 0)
        };
    }

    drawLines() {
        this.lines.forEach((points, lIndex) => {
            if (points.length < 2 || !this.paths[lIndex]) return;

            const firstPoint = this.moved(points[0], false);
            let d = `M ${firstPoint.x} ${firstPoint.y}`;

            for (let i = 1; i < points.length; i++) {
                const current = this.moved(points[i]);
                d += ` L ${current.x} ${current.y}`;
            }

            this.paths[lIndex].setAttribute('d', d);
        });
    }

    tick(time) {
        // Smooth mouse movement
        this.mouse.sx += (this.mouse.x - this.mouse.sx) * 0.1;
        this.mouse.sy += (this.mouse.y - this.mouse.sy) * 0.1;

        // Mouse velocity
        const dx = this.mouse.x - this.mouse.lx;
        const dy = this.mouse.y - this.mouse.ly;
        const d = Math.hypot(dx, dy);

        this.mouse.v = d;
        this.mouse.vs += (d - this.mouse.vs) * 0.1;
        this.mouse.vs = Math.min(100, this.mouse.vs);

        this.mouse.lx = this.mouse.x;
        this.mouse.ly = this.mouse.y;
        this.mouse.a = Math.atan2(dy, dx);

        // Animate
        this.movePoints(time);
        this.drawLines();

        this.rafId = requestAnimationFrame((t) => this.tick(t));
    }

    // Simple 2D noise generator (Perlin-like)
    createSimplexNoise() {
        const p = [];
        for (let i = 0; i < 256; i++) p[i] = i;

        // Shuffle
        for (let i = 255; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [p[i], p[j]] = [p[j], p[i]];
        }

        // Duplicate
        for (let i = 0; i < 256; i++) p[256 + i] = p[i];

        const fade = t => t * t * t * (t * (t * 6 - 15) + 10);
        const lerp = (t, a, b) => a + t * (b - a);

        const grad = (hash, x, y) => {
            const h = hash & 15;
            const u = h < 8 ? x : y;
            const v = h < 4 ? y : h === 12 || h === 14 ? x : 0;
            return ((h & 1) === 0 ? u : -u) + ((h & 2) === 0 ? v : -v);
        };

        return (x, y) => {
            const X = Math.floor(x) & 255;
            const Y = Math.floor(y) & 255;

            x -= Math.floor(x);
            y -= Math.floor(y);

            const u = fade(x);
            const v = fade(y);

            const aa = p[p[X] + Y];
            const ab = p[p[X] + Y + 1];
            const ba = p[p[X + 1] + Y];
            const bb = p[p[X + 1] + Y + 1];

            return lerp(v,
                lerp(u, grad(aa, x, y), grad(ba, x - 1, y)),
                lerp(u, grad(ab, x, y - 1), grad(bb, x - 1, y - 1))
            );
        };
    }

    destroy() {
        if (this.rafId) cancelAnimationFrame(this.rafId);
        if (this.svg) this.svg.remove();
        if (this.resizeObserver) this.resizeObserver.disconnect();
        this.container.removeEventListener('mousemove', this.onMouseMove);
    }
}

// Initialize wave background on sidebar and other elements
document.addEventListener('DOMContentLoaded', () => {
    // Sidebar background
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        sidebar.style.position = 'relative';
        window.sidebarWave = new WaveBackground(sidebar);
    }

    // Spotify background (will be initialized when view is shown, or now if we want it always running)
    // Better to have it running because the view is just hidden via display:none
    const spotifyView = document.getElementById('section-spotify');
    if (spotifyView) {
        spotifyView.style.position = 'relative';
        spotifyView.style.overflow = 'hidden'; // Ensure waves don't spill out
        window.spotifyWave = new WaveBackground(spotifyView);
    }
});
