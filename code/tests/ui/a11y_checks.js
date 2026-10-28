// Browser checks for tests/ui/smoke_webkit.py (loaded with page.add_script_tag, never by the app).
//
// __contrastFailures(): every visible text in the viewport whose colour against its background
//   is below WCAG AA (4.5:1, or 3:1 for large text). Text on a picture or gradient is skipped
//   (its background is unknown), as is text in disabled or dimmed (opacity < 0.5) controls.
// __unnamedControls(): visible buttons, links, fields and focusable cards without an
//   accessible name (aria-label, label, text or title).

// Returns contrast failures for visible text in the page (WCAG AA)
window.__contrastFailures = () => {
  function parse(c) {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  }
  function blend(top, bottom) {
    const a = top.a + bottom.a * (1 - top.a);
    if (a === 0) return { r: 0, g: 0, b: 0, a: 0 };
    return { r: (top.r * top.a + bottom.r * bottom.a * (1 - top.a)) / a,
             g: (top.g * top.a + bottom.g * bottom.a * (1 - top.a)) / a,
             b: (top.b * top.a + bottom.b * bottom.a * (1 - top.a)) / a, a };
  }
  function lum(c) {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  }
  function ratio(a, b) { const l1 = lum(a), l2 = lum(b); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); }
  function background(el) {
    // collect layers from el up to root
    const layers = [];
    let node = el;
    while (node && node.nodeType === 1) {
      const cs = getComputedStyle(node);
      if (cs.backgroundImage && cs.backgroundImage !== 'none' && !cs.backgroundImage.startsWith('linear-gradient(rgba(0, 0, 0, 0)')) return null; // image/gradient: unknown
      const bg = parse(cs.backgroundColor);
      if (bg && bg.a > 0) { layers.push(bg); if (bg.a >= 1) break; }
      node = node.parentElement;
    }
    let base = { r: 255, g: 255, b: 255, a: 1 };
    if (!layers.length || layers[layers.length - 1].a < 1) {
      const bodyBg = parse(getComputedStyle(document.body).backgroundColor);
      if (bodyBg && bodyBg.a > 0) base = bodyBg;
    }
    let out = base;
    for (let i = layers.length - 1; i >= 0; i--) out = blend(layers[i], out);
    return out;
  }
  function opacity(el) { let o = 1; for (let n = el; n && n.nodeType === 1; n = n.parentElement) o *= parseFloat(getComputedStyle(n).opacity); return o; }
  function name(el) {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    if (el.classList.length) s += '.' + [...el.classList].slice(0, 2).join('.');
    const p = el.parentElement;
    if (p) { let ps = p.tagName.toLowerCase(); if (p.id) ps += '#' + p.id; else if (p.classList.length) ps += '.' + p.classList[0]; s = ps + ' > ' + s; }
    return s;
  }
  const fails = new Map();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let t;
  while ((t = walker.nextNode())) {
    if (!t.textContent.trim()) continue;
    const el = t.parentElement;
    if (!el || el.closest('[aria-hidden="true"], script, style, option, .sr-only, [disabled], .skeleton')) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    if (rect.bottom < 0 || rect.top > innerHeight || rect.right < 0 || rect.left > innerWidth) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden') continue;
    const op = opacity(el);
    if (op < 0.5) continue; // inactive/disabled look
    const fg = parse(cs.color); if (!fg) continue;
    const bg = background(el); if (!bg) continue;
    const fgb = blend({ ...fg, a: fg.a * op }, bg);
    const r = ratio(fgb, bg);
    const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
    const need = (size >= 24 || (bold && size >= 18.66)) ? 3 : 4.5;
    if (r < need) {
      const key = name(el) + ' ' + cs.color + ' on ' + `rgb(${bg.r|0},${bg.g|0},${bg.b|0})`;
      if (!fails.has(key)) fails.set(key, { key, ratio: Math.round(r * 100) / 100, text: t.textContent.trim().slice(0, 40) });
    }
  }
  return [...fails.values()];
};

window.__unnamedControls = () => {
  const out = [];
  for (const el of document.querySelectorAll('button, [role="button"], a[href], input:not([type=hidden]), select, textarea, [tabindex="0"]')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || el.closest('[aria-hidden="true"]')) continue;
    if (r.bottom < 0 || r.top > innerHeight) continue;
    let name = el.getAttribute('aria-label') || '';
    if (!name && el.getAttribute('aria-labelledby')) name = (document.getElementById(el.getAttribute('aria-labelledby')) || {}).textContent || '';
    if (!name && el.id) { const l = document.querySelector(`label[for="${el.id}"]`); if (l) name = l.textContent; }
    if (!name && el.closest('label')) name = el.closest('label').textContent;
    if (!name && !['INPUT','SELECT','TEXTAREA'].includes(el.tagName)) name = el.textContent;
    if (!name) name = el.getAttribute('title') || el.getAttribute('placeholder') || '';
    if (!name.trim()) out.push(el.outerHTML.slice(0, 140));
  }
  return [...new Set(out)];
};
