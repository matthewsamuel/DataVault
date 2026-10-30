// DataVault Dashboard - Activiteit
// Note: API_BASE is defined in app.js
//
// Wat je deed, per dag, uit de bronnen van DataGatherer (backend/routes/history.py): YouTube,
// zoekopdrachten, websites, agenda, notities, Netflix, TikTok, trainingen, ... Links de dagen
// (nieuwste eerst, per maand), rechts de gebeurtenissen van de gekozen dag. Filters op bron en
// soort, en zoeken in de titels. Net als datagatherer.js staat hier geen code per bron: namen,
// iconen en soorten komen van de server.

const ACT_DAYS_LIMIT = 1500;
const ACT_SEARCH_DEBOUNCE_MS = 300;
const ACT_DETAIL_LABELS = {
    kanaal: 'Kanaal', artiest: 'Artiest', album: 'Album', profiel: 'Profiel', apparaat: 'Apparaat', duur_s: 'Duur',
    duur_min: 'Duur', geluisterd_s: 'Geluisterd', afstand: 'Afstand', kcal: 'Energie', agenda: 'Agenda', locatie: 'Plek',
    browser: 'Browser', gesprek: 'Gesprek', van: 'Van', soort: 'Soort', labels: 'Labels',
};

const actState = {
    facets: null,
    sources: new Set(),
    kind: '',
    q: '',
    days: [],
    day: null,
    seq: 0,
    eventsSeq: 0,
    searchTimer: null,
};

function activityInit() {
    actLoad();
}

function actParams(extra = {}) {
    const params = new URLSearchParams();
    actState.sources.forEach(s => params.append('source', s));
    if (actState.kind) params.append('kind', actState.kind);
    if (actState.q) params.append('q', actState.q);
    Object.entries(extra).forEach(([k, v]) => params.append(k, v));
    return params.toString();
}

async function actLoad() {
    const days = document.getElementById('act-days');
    const events = document.getElementById('act-events');
    if (!days) return;
    const seq = ++actState.seq;
    if (!actState.facets) {
        actShowDays(true);
        days.replaceChildren(skeletonRows(6));
        events.replaceChildren(skeletonRows(5));
    }
    try {
        const facets = await fetchJSON(`${API_BASE}/api/activity/facets`);
        if (seq !== actState.seq) return;
        actState.facets = facets;
        actRenderFilters();
        // Zonder gegevens hebben de filters geen zin: alleen de lege staat
        document.querySelector('.act-toolbar').hidden = !facets.sources.length;
        if (!facets.sources.length) {
            actShowDays(false);
            events.replaceChildren(emptyState({
                icon: 'fa-wave-square', title: 'Nog geen activiteit',
                text: 'Lees in DataGatherer een export of je browsergeschiedenis in; '
                    + 'wat je deed verschijnt hier per dag.',
                action: { label: 'Naar DataGatherer', primary: true, onClick: () => switchMainView('datagatherer') },
            }));
            actSummary('');
            return;
        }
        await actLoadDays(seq);
    } catch (err) {
        if (seq !== actState.seq) return;
        actShowDays(false);
        // Zonder bronnen (de eerste aanvraag mislukte) hebben lege filters geen zin
        if (!actState.facets) document.querySelector('.act-toolbar').hidden = true;
        events.replaceChildren(errorState('De activiteit kon niet geladen worden', err, actLoad));
    }
}

// Zonder dagen (geen treffers, een fout) geen lege, omrande kolom: de melding krijgt de hele breedte
function actShowDays(show) {
    const layout = document.getElementById('act-layout');
    if (layout) layout.classList.toggle('act-empty', !show);
    if (!show) document.getElementById('act-days').replaceChildren();
}

async function actLoadDays(seq = ++actState.seq) {
    const data = await fetchJSON(`${API_BASE}/api/activity/days?${actParams({ limit: ACT_DAYS_LIMIT })}`);
    if (seq !== actState.seq) return;
    actState.days = data.days;
    const total = data.days.reduce((n, d) => n + d.count, 0);
    actSummary(data.days.length
        ? `${total.toLocaleString('nl-BE')} ${total === 1 ? 'gebeurtenis' : 'gebeurtenissen'} op ${data.days.length.toLocaleString('nl-BE')} ${data.days.length === 1 ? 'dag' : 'dagen'}`
        : '');
    if (!data.days.length) {
        actShowDays(false);
        actState.day = null;
        document.getElementById('act-events').replaceChildren(emptyState({
            icon: 'fa-filter', title: 'Niets gevonden', text: 'Geen activiteit met deze filters.',
            action: { label: 'Filters wissen', onClick: actResetFilters },
        }));
        return;
    }
    if (!actState.day || !data.days.some(d => d.day === actState.day)) actState.day = data.days[0].day;
    actShowDays(true);
    actRenderDays();
    actLoadEvents();
}

function actSummary(text) {
    const el = document.getElementById('act-summary');
    if (el) el.textContent = text;
}

// ---------- filters ----------

function actRenderFilters() {
    const bar = document.getElementById('act-sources');
    const kindSelect = document.getElementById('act-kind');
    const pill = (label, icon, pressed, onClick, count) => createEl('button', {
        type: 'button', class: 'pill act-source', 'aria-pressed': pressed ? 'true' : 'false', onClick,
    }, icon ? createEl('i', { class: icon, 'aria-hidden': 'true' }) : null, label,
        count !== undefined ? createEl('span', { class: 'act-pill-count', text: count.toLocaleString('nl-BE') }) : null);
    bar.replaceChildren(
        pill('Alle bronnen', null, actState.sources.size === 0, () => { actState.sources.clear(); actFiltersChanged(); }),
        ...actState.facets.sources.map(s => pill(s.name, s.icon, actState.sources.has(s.id), () => {
            if (actState.sources.has(s.id)) actState.sources.delete(s.id); else actState.sources.add(s.id);
            actFiltersChanged();
        }, s.count)));
    kindSelect.replaceChildren(createEl('option', { value: '', text: 'Alle soorten' }),
        ...actState.facets.kinds.map(k => createEl('option', { value: k.id, selected: k.id === actState.kind, text: `${k.label} (${k.count.toLocaleString('nl-BE')})` })));
    document.getElementById('act-reset').hidden = !(actState.sources.size || actState.kind || actState.q);
}

function actFiltersChanged() {
    const focusKey = document.activeElement && document.activeElement.closest('.act-source')
        ? [...document.querySelectorAll('.act-source')].indexOf(document.activeElement.closest('.act-source')) : -1;
    actRenderFilters();
    if (focusKey >= 0) {
        const again = document.querySelectorAll('.act-source')[focusKey];
        if (again) again.focus();
    }
    actLoadDays().catch(err => {
        actShowDays(false);
        document.getElementById('act-events').replaceChildren(errorState('De activiteit kon niet geladen worden', err, actLoad));
    });
}

function actResetFilters() {
    actState.sources.clear();
    actState.kind = '';
    actState.q = '';
    document.getElementById('act-search').value = '';
    actFiltersChanged();
}

// ---------- dagen ----------

function actDayLabel(day, withYear = false) {
    const d = new Date(`${day}T12:00:00`);
    return d.toLocaleDateString('nl-BE', { weekday: 'short', day: 'numeric', month: 'short', ...(withYear ? { year: 'numeric' } : {}) });
}

function actRenderDays() {
    const box = document.getElementById('act-days');
    const kinds = new Map((actState.facets ? actState.facets.kinds : []).map(k => [k.id, k]));
    const children = [];
    let month = null;
    actState.days.forEach(d => {
        const m = d.day.slice(0, 7);
        if (m !== month) {
            month = m;
            children.push(createEl('li', { class: 'act-month overline', role: 'presentation',
                text: new Date(`${m}-15T12:00:00`).toLocaleDateString('nl-BE', { month: 'long', year: 'numeric' }) }));
        }
        const top = Object.entries(d.kinds).sort((a, b) => b[1] - a[1]).slice(0, 3);
        children.push(createEl('li', { role: 'presentation' },
            createEl('button', {
                type: 'button', class: 'act-day', 'aria-pressed': d.day === actState.day ? 'true' : 'false',
                dataset: { day: d.day }, onClick: () => actSelectDay(d.day),
                'aria-label': `${actDayLabel(d.day, true)}: ${d.count} ${d.count === 1 ? 'gebeurtenis' : 'gebeurtenissen'}`,
            },
                createEl('span', { class: 'act-day-label', text: actDayLabel(d.day) }),
                createEl('span', { class: 'act-day-icons', 'aria-hidden': 'true' },
                    ...top.map(([k]) => createEl('i', { class: `fas ${(kinds.get(k) || {}).icon || 'fa-wave-square'}` }))),
                createEl('span', { class: 'act-day-count mono', text: d.count.toLocaleString('nl-BE') }))));
    });
    box.replaceChildren(...children);
}

function actSelectDay(day, focus = false) {
    actState.day = day;
    document.querySelectorAll('.act-day').forEach(b => b.setAttribute('aria-pressed', b.dataset.day === day ? 'true' : 'false'));
    const btn = document.querySelector(`.act-day[data-day="${day}"]`);
    if (btn && focus) { btn.focus(); btn.scrollIntoView({ block: 'nearest' }); }
    actLoadEvents();
}

// Pijltjes in de dagenlijst: naar de vorige of volgende dag
function actDaysKey(e) {
    const btn = e.target.closest('.act-day');
    if (!btn || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    const days = actState.days.map(d => d.day);
    const i = days.indexOf(btn.dataset.day);
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? days.length - 1 : Math.max(0, Math.min(days.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)));
    actSelectDay(days[next], true);
}

// ---------- gebeurtenissen van een dag ----------

function actDetailText(key, value) {
    if (key === 'duur_s' || key === 'geluisterd_s') {
        const m = Math.round(value / 60);
        return m >= 60 ? `${Math.floor(m / 60)} u ${m % 60} min` : (m ? `${m} min` : `${value} s`);
    }
    if (key === 'duur_min') return `${Math.round(value)} min`;
    if (key === 'kcal') return `${value} kcal`;
    if (Array.isArray(value)) return value.join(', ');
    return String(value);
}

function actEvent(ev) {
    const time = new Date(ev.ts * 1000).toLocaleTimeString('nl-BE', { hour: '2-digit', minute: '2-digit' });
    const meta = [ev.kind_label, ev.source_name];
    Object.entries(ev.details || {}).forEach(([k, v]) => {
        if (ACT_DETAIL_LABELS[k] && v !== null && v !== '' && meta.length < 5) meta.push(`${ACT_DETAIL_LABELS[k]}: ${actDetailText(k, v)}`);
    });
    const kind = (actState.facets.kinds.find(k => k.id === ev.kind) || {}).icon || 'fa-wave-square';
    const title = ev.url && /^https?:\/\//.test(ev.url)
        ? createEl('a', { class: 'act-title', href: ev.url, rel: 'noopener noreferrer', title: ev.url, text: ev.title || ev.url,
            onClick: (e) => { e.preventDefault(); openExternal(ev.url); } })
        : createEl('span', { class: 'act-title', text: ev.title || ev.kind_label });
    const text = ev.details && typeof ev.details.tekst === 'string' && ev.details.tekst.trim()
        ? createEl('p', { class: 'act-text', text: ev.details.tekst }) : null;
    return createEl('li', { class: 'act-event' },
        createEl('time', { class: 'act-time mono', datetime: new Date(ev.ts * 1000).toISOString(), text: time }),
        createEl('span', { class: 'act-icon', 'aria-hidden': 'true' }, createEl('i', { class: `fas ${kind}` })),
        createEl('div', { class: 'act-body' }, title, text,
            createEl('div', { class: 'act-meta' }, createEl('i', { class: ev.source_icon, 'aria-hidden': 'true' }), ` ${meta.join(' · ')}`)));
}

async function actLoadEvents() {
    const box = document.getElementById('act-events');
    const day = actState.day;
    if (!day) return;
    const seq = ++actState.eventsSeq;
    box.replaceChildren(skeletonRows(4));
    try {
        const data = await fetchJSON(`${API_BASE}/api/activity/events?${actParams({ day })}`);
        if (seq !== actState.eventsSeq) return;
        const head = createEl('div', { class: 'act-events-head' },
            createEl('h2', { class: 'section-title', id: 'act-day-title', text: new Date(`${day}T12:00:00`).toLocaleDateString('nl-BE',
                { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }) }),
            createEl('span', { class: 'act-events-count', text: `${data.events.length}${data.truncated ? '+' : ''} ${data.events.length === 1 ? 'gebeurtenis' : 'gebeurtenissen'}` }));
        // (replaceChildren zou null als de tekst 'null' tonen)
        box.replaceChildren(...[head,
            createEl('ol', { class: 'act-list', 'aria-labelledby': 'act-day-title' }, ...data.events.map(actEvent)),
            data.truncated ? createEl('p', { class: 'act-more', text: 'Alleen de eerste 500 van deze dag: verfijn met de filters.' }) : null]
            .filter(Boolean));
    } catch (err) {
        if (seq !== actState.eventsSeq) return;
        box.replaceChildren(errorState('Deze dag kon niet geladen worden', err, actLoadEvents));
    }
}

// ---------- vastknopen ----------

document.addEventListener('DOMContentLoaded', () => {
    const search = document.getElementById('act-search');
    if (!search) return;
    search.addEventListener('input', () => {
        clearTimeout(actState.searchTimer);
        actState.searchTimer = setTimeout(() => {
            actState.q = search.value.trim();
            actFiltersChanged();
        }, ACT_SEARCH_DEBOUNCE_MS);
    });
    document.getElementById('act-kind').addEventListener('change', (e) => { actState.kind = e.target.value; actFiltersChanged(); });
    document.getElementById('act-reset').addEventListener('click', actResetFilters);
    document.getElementById('act-days').addEventListener('keydown', actDaysKey);
    if (window.location.hash === '#activiteit') {
        history.replaceState(null, '', window.location.pathname);
        switchMainView('activity');
    }
});

// Nieuwe gegevens ingelezen: de volgende keer opnieuw laden
window.addEventListener('datavault:data-changed', () => {
    actState.facets = null;
    if (typeof currentMainView === 'function' && currentMainView() === 'activity') actLoad();
});
