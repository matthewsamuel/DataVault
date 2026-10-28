// DataVault Dashboard - Veilige DOM-hulpjes
//
// Namen van bestanden en mappen, paden, EXIF-waarden, Spotify-titels en tekst van
// het taalmodel zijn gegevens, nooit HTML. Een bestand mag op macOS gewoon
// 'foto <img src=x onerror=...>.jpg' heten; dat moet als tekst op het scherm komen
// en nooit als code draaien. Daarom bouwt de frontend zijn HTML alleen op deze
// manieren op (tests/test_frontend_safety.py bewaakt dat):
//
// - createEl(tag, props, ...kinderen) maakt een element. Tekst gaat altijd via een
//   tekstknoop, klikken via addEventListener (props.onClick), nooit via een
//   onclick-tekst met data erin.
// - html`...` is een template waarin elke ${waarde} ge-escaped wordt. Alleen een
//   ander html`...`-stuk wordt ongewijzigd ingevoegd.
// - setHTML(element, markup) zet alleen markup uit html`...`; al het andere wordt
//   gewone tekst.
//
// Laadt direct na api.js, vóór alle andere scripts.

class SafeHTML {
    constructor(markup) { this.markup = markup; }
    toString() { return this.markup; }
}

const HTML_ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => HTML_ESCAPES[c]);
}

function htmlValue(value) {
    if (value instanceof SafeHTML) return value.markup;
    if (Array.isArray(value)) return value.map(htmlValue).join('');
    if (value === null || value === undefined || value === false) return '';
    return escapeHTML(value);
}

// Tagged template: html`<b>${naam}</b>` escapet naam; html`` in html`` blijft markup
function html(strings, ...values) {
    let out = strings[0];
    values.forEach((value, i) => { out += htmlValue(value) + strings[i + 1]; });
    return new SafeHTML(out);
}

// Zet markup uit html`...`; iets anders (een gewone string van buiten) wordt tekst
function setHTML(element, markup) {
    if (markup instanceof SafeHTML) element.innerHTML = markup.markup;
    else element.textContent = markup == null ? '' : String(markup);
}

function appendChildren(node, children) {
    children.forEach(child => {
        if (child === null || child === undefined || child === false) return;
        if (Array.isArray(child)) appendChildren(node, child);
        else if (child instanceof Node) node.appendChild(child);
        else if (child instanceof SafeHTML) {
            const tpl = document.createElement('template');
            tpl.innerHTML = child.markup;
            node.appendChild(tpl.content);
        } else node.appendChild(document.createTextNode(String(child)));
    });
    return node;
}

// createEl('div', { class: 'naam', title: pad, dataset: { path }, onClick: fn }, 'tekst', kindElement)
function createEl(tag, props, ...children) {
    const node = document.createElement(tag);
    Object.entries(props || {}).forEach(([key, value]) => {
        if (value === undefined || value === null) return;
        if (key === 'class' || key === 'className') node.className = value;
        else if (key === 'text') node.textContent = value;
        else if (key === 'style') node.style.cssText = value;
        else if (key === 'dataset') Object.assign(node.dataset, value);
        else if (/^on[A-Z]/.test(key)) {
            if (typeof value !== 'function') throw new TypeError(`${key} moet een functie zijn`);
            node.addEventListener(key.slice(2).toLowerCase(), value);
        } else if (/^on/i.test(key) || key === 'innerHTML' || key === 'outerHTML') {
            throw new TypeError(`${key} is niet toegestaan; gebruik onClick met een functie`);
        } else if (typeof value === 'boolean') node.toggleAttribute(key, value);
        else node.setAttribute(key, value);
    });
    return appendChildren(node, children);
}

// URL voor een bestand van de eigen server (/api/file, /api/thumb)
function vaultFileUrl(endpoint, path) {
    return `${API_BASE}/api/${endpoint}?path=${encodeURIComponent(path)}`;
}

// CSS url("...") voor style.backgroundImage; een pad kan ' ( ) bevatten
function cssUrl(url) {
    return `url("${String(url).replace(/[\\"]/g, '\\$&').replace(/[\r\n\f]/g, '')}")`;
}

// Link naar buiten alleen na een klik, in een nieuw tabblad zonder toegang tot deze pagina
function openExternal(url) {
    window.open(url, '_blank', 'noopener,noreferrer');
}
