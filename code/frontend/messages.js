// DataVault Dashboard - Berichten
//
// Alle gesprekken die DataGatherer inlas (WhatsApp, iMessage, Messenger, Instagram, ...) op één
// plek: links de lijst met zoeken en een filter per bron, rechts het gesprek als chatbubbels met
// datums en bijlagen. De bronnen komen van de server (/api/messages/sources): deze pagina kent
// geen bron bij naam. Alleen lezen; inlezen gebeurt in DataGatherer.
//
// Een foto opent groot (pijltjes bladeren door de foto's van het gesprek), een gedeelde link
// staat als kaartje in de bubbel en de naam bovenaan opent het profiel online als DataVault
// de gebruikersnaam kent (/api/messages/conversations/{id}/profile).
//
// Toetsenbord: pijltjes omhoog/omlaag (en Home/End) in de lijst, Enter opent een gesprek,
// Escape in het zoekveld wist de zoekopdracht. Pasha leest berichten alleen per bron die je
// zelf aanzet (venster 'Pasha en je berichten', standaard uit).

const msgState = {
    built: false,
    sources: [],
    source: null,          // null: alle bronnen
    q: '',
    convs: [],
    hits: [],
    selected: null,        // id van het open gesprek
    thread: null,
    highlight: null,       // id van het bericht waar een zoektreffer naar wees
    searchTimer: null,
    loadToken: 0,
    profiles: new Map(),   // gesprek-id -> profiel (of null) van de server
    lightbox: null,        // {items, index}: de foto's van het gesprek en welke groot staat
};

const MSG_DAY = new Intl.DateTimeFormat('nl-BE', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
const MSG_SHORT = new Intl.DateTimeFormat('nl-BE', { day: 'numeric', month: 'short', year: 'numeric' });
const MSG_TIME = new Intl.DateTimeFormat('nl-BE', { hour: '2-digit', minute: '2-digit' });
const MSG_EDIT_TIME = new Intl.DateTimeFormat('nl-BE', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

// Zoekveld leeg en terug naar alle gesprekken; de focus gaat terug naar het zoekveld
function msgClearSearch() {
    const search = document.getElementById('msg-search');
    clearTimeout(msgState.searchTimer);
    search.value = '';
    msgState.q = '';
    msgLoadList();
    search.focus();
}

function msgInit() {
    if (!msgState.built) {
        msgState.built = true;
        const search = document.getElementById('msg-search');
        search.addEventListener('input', () => {
            clearTimeout(msgState.searchTimer);
            msgState.searchTimer = setTimeout(() => { msgState.q = search.value.trim(); msgLoadList(); }, 250);
        });
        search.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && search.value) {
                e.preventDefault();
                e.stopPropagation();
                msgClearSearch();
            } else if (e.key === 'ArrowDown') {
                const first = document.querySelector('#msg-list .msg-item');
                if (first) { e.preventDefault(); first.focus(); }
            }
        });
        document.getElementById('msg-list').addEventListener('keydown', msgListKey);
    }
    msgLoadSources();
}

// ==================== BRONNEN ====================

async function msgLoadSources() {
    const filters = document.getElementById('msg-filters');
    try {
        const data = await fetchJSON(`${API_BASE}/api/messages/sources`);
        msgState.sources = data.sources;
    } catch (err) {
        document.getElementById('msg-list').replaceChildren(errorState('Berichten laden mislukt', err, msgLoadSources));
        return;
    }
    const total = msgState.sources.reduce((n, s) => n + s.messages, 0);
    document.getElementById('msg-summary').replaceChildren(
        createEl('span', { class: 'mono', text: total.toLocaleString('nl-BE') }),
        ` ${total === 1 ? 'bericht' : 'berichten'} uit `,
        createEl('span', { class: 'mono', text: String(msgState.sources.filter(s => s.messages).length) }),
        ` van ${msgState.sources.length} bronnen`);
    if (msgState.source && !msgState.sources.some(s => s.id === msgState.source && s.messages)) msgState.source = null;
    const pill = (id, label, icon, count) => createEl('button', {
        type: 'button', class: 'pill', 'aria-pressed': String(msgState.source === id),
        dataset: { source: id || '' },
        onClick: () => { msgState.source = id; msgLoadSources(); },
    }, icon ? createEl('i', { class: icon, 'aria-hidden': 'true' }) : null, label,
        count !== null ? createEl('span', { class: 'msg-pill-count mono', text: count.toLocaleString('nl-BE') }) : null);
    const focused = document.activeElement && document.activeElement.closest('#msg-filters')
        ? document.activeElement.dataset.source : undefined;
    filters.replaceChildren(pill(null, 'Alle', null, null),
        ...msgState.sources.filter(s => s.messages).map(s => pill(s.id, s.name, s.icon, s.conversations)));
    if (focused !== undefined) {
        const again = [...filters.querySelectorAll('.pill')].find(b => b.dataset.source === focused);
        if (again) again.focus();
    }
    if (!total) {
        document.getElementById('msg-search').disabled = true;
        msgShowEmptyArchive();
        return;
    }
    document.getElementById('msg-search').disabled = false;
    msgLoadList();
}

function msgSourceInfo(id) {
    return msgState.sources.find(s => s.id === id) || { id, name: id, icon: 'fas fa-comment' };
}

function msgShowEmptyArchive() {
    const names = msgState.sources.map(s => s.name);
    const list = names.length > 1 ? `${names.slice(0, -1).join(', ')} of ${names[names.length - 1]}` : names.join('');
    document.getElementById('msg-list').replaceChildren(emptyState({
        icon: 'fa-comments', title: 'Nog geen berichten',
        text: `Lees je chats in via DataGatherer${list ? `: ${list}` : ''}. Ze blijven op deze Mac.`,
        action: { label: 'Naar DataGatherer', primary: true, onClick: () => switchMainView('datagatherer') },
    }));
    msgShowThreadEmpty();
}

// ==================== LIJST ====================

async function msgLoadList() {
    const token = ++msgState.loadToken;
    const list = document.getElementById('msg-list');
    list.replaceChildren(skeletonRows(6));
    const params = new URLSearchParams();
    if (msgState.source) params.set('source', msgState.source);
    try {
        if (msgState.q) {
            params.set('q', msgState.q);
            const [convs, hits] = await Promise.all([
                fetchJSON(`${API_BASE}/api/messages/conversations?${params}&limit=20`),
                fetchJSON(`${API_BASE}/api/messages/search?${params}&limit=60`),
            ]);
            if (token !== msgState.loadToken) return;
            msgState.convs = convs.conversations;
            msgState.hits = hits.results;
            msgState.marks = hits.marks;
        } else {
            const data = await fetchJSON(`${API_BASE}/api/messages/conversations?${params}&limit=300`);
            if (token !== msgState.loadToken) return;
            msgState.convs = data.conversations;
            msgState.hits = [];
        }
    } catch (err) {
        if (token === msgState.loadToken) list.replaceChildren(errorState('Gesprekken laden mislukt', err, msgLoadList));
        return;
    }
    msgRenderList();
    if (!msgState.selected && msgState.convs.length && !msgState.q) msgOpen(msgState.convs[0].id, { focusList: false });
}

function msgRenderList() {
    const list = document.getElementById('msg-list');
    const items = [];
    if (msgState.q) {
        if (msgState.convs.length) {
            items.push(createEl('h3', { class: 'msg-list-heading overline', text: 'Gesprekken' }),
                ...msgState.convs.map(msgConvItem));
        }
        if (msgState.hits.length) {
            items.push(createEl('h3', { class: 'msg-list-heading overline', text: 'Berichten' }),
                ...msgState.hits.map(msgHitItem));
        }
        if (!items.length) {
            items.push(emptyState({ icon: 'fa-magnifying-glass', title: 'Niets gevonden',
                text: `Geen gesprek of bericht met '${msgState.q}'. Probeer een kortere zoekterm.`,
                action: { label: 'Zoekterm wissen', onClick: msgClearSearch } }));
            items[0].classList.add('empty-state-compact');
        }
    } else if (!msgState.convs.length) {
        const empty = emptyState({ icon: 'fa-comment-slash', title: 'Geen gesprekken', text: 'Deze bron heeft nog geen gesprekken.' });
        empty.classList.add('empty-state-compact');
        items.push(empty);
    } else {
        items.push(...msgState.convs.map(msgConvItem));
    }
    list.replaceChildren(...items);
    if (window.MotionData) MotionData.msgList(list);
}

function msgAvatar(title, sourceId, isGroup) {
    const src = msgSourceInfo(sourceId);
    const letter = (title || '').trim().charAt(0).toUpperCase();
    // Een groep, of een nummer/adres zonder naam: een icoon in plaats van een letter
    const icon = isGroup ? 'fa-user-group' : (/\p{L}/u.test(letter) ? null : 'fa-user');
    return createEl('span', { class: 'msg-avatar', 'aria-hidden': 'true' },
        icon ? createEl('i', { class: `fas ${icon}` }) : createEl('span', { text: letter }),
        createEl('i', { class: `${src.icon} msg-avatar-source` }));
}

function msgConvItem(conv) {
    const src = msgSourceInfo(conv.source);
    const active = conv.id === msgState.selected;
    return createEl('button', {
        type: 'button', class: `msg-item${active ? ' active' : ''}`, 'aria-current': active ? 'true' : undefined,
        dataset: { conv: String(conv.id) },
        'aria-label': `${conv.title}, ${src.name}, ${conv.count} ${conv.count === 1 ? 'bericht' : 'berichten'}`,
        onClick: () => { msgState.highlight = null; msgOpen(conv.id); },
    }, msgAvatar(conv.title, conv.source, conv.is_group),
        createEl('span', { class: 'msg-item-main' },
            createEl('span', { class: 'msg-item-title', text: conv.title }),
            createEl('span', { class: 'msg-item-meta' },
                createEl('span', { text: src.name }), ' · ',
                createEl('span', { class: 'mono', text: conv.count.toLocaleString('nl-BE') }),
                conv.count === 1 ? ' bericht' : ' berichten')),
        createEl('span', { class: 'msg-item-date mono', text: conv.last ? MSG_SHORT.format(new Date(conv.last * 1000)) : '' }));
}

// Een fragment met de treffer gemarkeerd: tekst en <mark>, nooit HTML uit het bericht
function msgSnippet(text) {
    const [start, end] = msgState.marks || ['\u0002', '\u0003'];
    const out = createEl('span', { class: 'msg-item-snippet' });
    text.split(start).forEach((part, i) => {
        if (i === 0) { out.append(part); return; }
        const [hit, rest] = part.split(end);
        out.append(createEl('mark', { text: hit }), rest || '');
    });
    return out;
}

function msgHitItem(hit) {
    const src = msgSourceInfo(hit.source);
    return createEl('button', {
        type: 'button', class: 'msg-item msg-hit', dataset: { conv: String(hit.conversation), hit: String(hit.id) },
        'aria-label': `${hit.title}: ${hit.snippet.replace(/[\u0002\u0003]/g, '')}`,
        onClick: () => { msgState.highlight = hit.id; msgOpen(hit.conversation, { around: hit.id }); },
    }, msgAvatar(hit.title, hit.source, false),
        createEl('span', { class: 'msg-item-main' },
            createEl('span', { class: 'msg-item-title' }, hit.title,
                createEl('span', { class: 'msg-item-sender', text: hit.is_me ? ' · jij' : (hit.sender ? ` · ${hit.sender}` : '') })),
            msgSnippet(hit.snippet)),
        createEl('span', { class: 'msg-item-date mono', text: MSG_SHORT.format(new Date(hit.sent_at * 1000)) }),
        createEl('span', { class: 'sr-only', text: src.name }));
}

function msgListKey(e) {
    const items = [...document.querySelectorAll('#msg-list .msg-item')];
    const i = items.indexOf(document.activeElement);
    if (i < 0) return;
    let next = null;
    if (e.key === 'ArrowDown') next = items[Math.min(items.length - 1, i + 1)];
    else if (e.key === 'ArrowUp') next = i === 0 ? document.getElementById('msg-search') : items[i - 1];
    else if (e.key === 'Home') next = items[0];
    else if (e.key === 'End') next = items[items.length - 1];
    if (next) {
        e.preventDefault();
        next.focus({ preventScroll: true });
        const list = document.getElementById('msg-list');
        if (next.offsetTop < list.scrollTop) list.scrollTop = next.offsetTop - 8;
        else if (next.offsetTop + next.offsetHeight > list.scrollTop + list.clientHeight) {
            list.scrollTop = next.offsetTop + next.offsetHeight - list.clientHeight + 8;
        }
    }
}

// ==================== GESPREK ====================

function msgShowThreadEmpty() {
    msgState.selected = null;
    const pane = document.getElementById('msg-thread');
    pane.replaceChildren(emptyState({ icon: 'fa-comment-dots', title: 'Kies een gesprek',
        text: 'Je berichten verschijnen hier, met datums en bijlagen.' }));
}

async function msgOpen(convId, { around = null, before = null, focusList = true } = {}) {
    const pane = document.getElementById('msg-thread');
    const older = before !== null;
    msgState.selected = convId;
    document.querySelectorAll('#msg-list .msg-item').forEach(el => {
        const on = el.dataset.conv === String(convId) && !el.dataset.hit;
        el.classList.toggle('active', on);
        if (on) el.setAttribute('aria-current', 'true'); else el.removeAttribute('aria-current');
    });
    if (!older) pane.replaceChildren(createEl('div', { class: 'msg-thread-loading' }, skeletonRows(5)));
    const params = new URLSearchParams({ limit: '200' });
    if (around) params.set('around', around);
    if (older) params.set('before', before);
    let data;
    try {
        data = await fetchJSON(`${API_BASE}/api/messages/conversations/${convId}?${params}`);
    } catch (err) {
        if (msgState.selected === convId) pane.replaceChildren(errorState('Gesprek laden mislukt', err, () => msgOpen(convId, { around })));
        return;
    }
    if (msgState.selected !== convId) return;
    if (older && msgState.thread && msgState.thread.conversation.id === data.conversation.id) {
        const scroller = pane.querySelector('.msg-scroll');
        const keep = scroller ? scroller.scrollHeight - scroller.scrollTop : 0;
        data.messages = data.messages.concat(msgState.thread.messages);
        data.has_newer = msgState.thread.has_newer;
        msgState.thread = data;
        msgRenderThread();
        const again = pane.querySelector('.msg-scroll');
        if (again) again.scrollTop = again.scrollHeight - keep;
        if (window.MotionData) MotionData.msgThread(pane, { older: true });
        return;
    }
    msgState.thread = data;
    msgRenderThread();
    const scroller = pane.querySelector('.msg-scroll');
    const target = msgState.highlight && pane.querySelector(`[data-msg="${msgState.highlight}"]`);
    // Alleen het gesprek scrolt (scrollIntoView zou ook de pagina verschuiven)
    if (scroller && target) scroller.scrollTop = target.offsetTop - scroller.clientHeight / 2 + target.offsetHeight / 2;
    else if (scroller) scroller.scrollTop = scroller.scrollHeight;
    if (window.MotionData) MotionData.msgThread(pane, { highlight: !!target });
}

function msgRenderThread() {
    const pane = document.getElementById('msg-thread');
    const { conversation: conv, messages, has_more: hasMore, has_newer: hasNewer } = msgState.thread;
    const src = msgSourceInfo(conv.source);
    const period = conv.first && conv.last
        ? `${MSG_SHORT.format(new Date(conv.first * 1000))} – ${MSG_SHORT.format(new Date(conv.last * 1000))}` : '';
    const header = createEl('div', { class: 'msg-thread-header' }, createEl('div', { class: 'msg-thread-person' },
        msgAvatar(conv.title, conv.source, conv.is_group),
        createEl('div', { class: 'msg-thread-heading' },
            createEl('div', { class: 'msg-thread-name' },
                createEl('h2', { class: 'msg-thread-title', text: conv.title })),
            createEl('p', { class: 'msg-thread-meta' },
                createEl('i', { class: src.icon, 'aria-hidden': 'true' }), ` ${src.name} · `,
                createEl('span', { class: 'mono', text: conv.count.toLocaleString('nl-BE') }),
                ` ${conv.count === 1 ? 'bericht' : 'berichten'}`,
                conv.is_group && conv.participants.length ? ` · ${conv.participants.length} deelnemers` : '',
                period ? ' · ' : '', period ? createEl('span', { class: 'mono', text: period }) : ''))),
        createEl('div', { class: 'msg-thread-actions' }));
    const body = createEl('div', {
        class: 'msg-scroll', tabindex: '0', role: 'log', 'aria-label': `Berichten met ${conv.title}`,
    });
    if (hasMore && messages.length) {
        const first = messages[0];
        body.append(createEl('div', { class: 'msg-more' }, createEl('button', {
            type: 'button', class: 'btn btn-secondary btn-sm',
            onClick: (e) => { e.currentTarget.disabled = true; msgOpen(conv.id, { before: first.id }); },
        }, createEl('i', { class: 'fas fa-arrow-up', 'aria-hidden': 'true' }), 'Oudere berichten laden')));
    }
    let day = null;
    let prev = null;
    for (const m of messages) {
        const when = new Date(m.sent_at * 1000);
        const key = when.toDateString();
        if (key !== day) {
            day = key;
            prev = null;
            body.append(createEl('div', { class: 'msg-day', role: 'separator' }, createEl('span', { text: MSG_DAY.format(when) })));
        }
        body.append(msgBubble(m, conv, prev));
        prev = m;
    }
    if (hasNewer) {
        body.append(createEl('div', { class: 'msg-more' }, createEl('button', {
            type: 'button', class: 'btn btn-secondary btn-sm',
            onClick: () => { msgState.highlight = null; msgOpen(conv.id); },
        }, createEl('i', { class: 'fas fa-arrow-down', 'aria-hidden': 'true' }), 'Naar de nieuwste berichten')));
    }
    if (!messages.length) body.append(emptyState({ icon: 'fa-comment-slash', title: 'Geen berichten', text: 'Dit gesprek is leeg.' }));
    pane.replaceChildren(header, body);
    msgShowProfile(conv);
}

// Waar de ander online te vinden is (backend msg_profiles). Met een gebruikersnaam worden de
// avatar en de naam een link naar het profiel, met de naam erbij en een knop rechts. Zonder
// (Instagram bewaart in de export alleen de weergavenaam) staat er rechts 'Zoek op het web'.
async function msgShowProfile(conv) {
    let profile = msgState.profiles.get(conv.id);
    if (profile === undefined) {
        try {
            profile = (await fetchJSON(`${API_BASE}/api/messages/conversations/${conv.id}/profile`)).profile;
        } catch (_) {
            return;     // zonder link is het gesprek gewoon te lezen
        }
        msgState.profiles.set(conv.id, profile);
    }
    const header = document.querySelector('#msg-thread .msg-thread-header');
    if (!profile || !header || !msgState.thread || msgState.thread.conversation.id !== conv.id) return;
    const actions = header.querySelector('.msg-thread-actions');
    const external = { target: '_blank', rel: 'noopener noreferrer' };
    if (profile.url) {
        const what = profile.site === 'WhatsApp' ? `Chat met ${profile.handle} openen in WhatsApp`
            : `Profiel van @${profile.handle} openen op ${profile.site}`;
        const person = header.querySelector('.msg-thread-person');
        const link = createEl('a', { class: 'msg-thread-person is-link', href: profile.url, title: what, ...external },
            ...person.childNodes);
        link.querySelector('.msg-thread-name').append(
            createEl('i', { class: 'fas fa-arrow-up-right-from-square msg-thread-out', 'aria-hidden': 'true' }));
        // Vlak na de bron, zodat hij ook in een smal venster in beeld blijft (de periode valt eerst weg)
        const meta = link.querySelector('.msg-thread-meta');
        meta.insertBefore(createEl('span', { class: 'mono msg-thread-handle',
            text: profile.site === 'WhatsApp' ? profile.handle : `@${profile.handle}` }), meta.childNodes[2]);
        meta.insertBefore(document.createTextNode(' · '), meta.childNodes[3]);
        person.replaceWith(link);
        actions.append(createEl('a', { class: 'btn btn-secondary btn-sm', href: profile.url, title: what, ...external },
            createEl('i', { class: msgSourceInfo(conv.source).icon, 'aria-hidden': 'true' }),
            profile.site === 'WhatsApp' ? 'Openen in WhatsApp' : 'Profiel openen'));
    } else if (profile.search_url) {
        actions.append(createEl('a', {
            class: 'btn btn-secondary btn-sm', href: profile.search_url, ...external,
            title: `${profile.site} bewaart in de export geen gebruikersnaam. Zoek '${conv.title}' op het web.`,
        }, createEl('i', { class: 'fas fa-magnifying-glass', 'aria-hidden': 'true' }), 'Zoek op het web'));
    }
    if (window.Motion && actions.firstChild) Motion.enter(actions, 'fade', { key: 'profile' });
}

// Onderaan een bubbel: het uur, en 'Bewerkt' of 'Eerdere tekst' (ingetrokken) als dat zo is. De eerdere
// versies die DataVault al had, staan achter die knop (iMessage: messages_store.revise_messages).
function msgFoot(m, when) {
    const time = createEl('time', { class: 'msg-time mono', datetime: when.toISOString(),
        title: `${MSG_DAY.format(when)} ${MSG_TIME.format(when)}${m.tz ? ` (${m.tz})` : ''}`, text: MSG_TIME.format(when) });
    const edits = m.edits || [];
    const changedAt = m.retracted_at || m.edited_at;
    if (!changedAt || (m.retracted_at && !edits.length)) return { el: createEl('div', { class: 'msg-foot' }, time) };
    const stamp = new Date(changedAt * 1000);
    const title = `${m.retracted_at ? 'Ingetrokken' : 'Bewerkt'} op ${MSG_DAY.format(stamp)} om ${MSG_TIME.format(stamp)}`;
    const label = m.retracted_at ? 'Eerdere tekst' : 'Bewerkt';
    if (!edits.length) {
        return { el: createEl('div', { class: 'msg-foot' }, createEl('span', { class: 'msg-edited', title, text: label }), time) };
    }
    const list = createEl('ol', { class: 'msg-edits', id: `msg-edits-${m.id}`, hidden: true,
        'aria-label': edits.length === 1 ? 'Eerdere versie' : 'Eerdere versies' },
        ...edits.map(e => {
            const at = e.at ? new Date(e.at * 1000) : null;
            return createEl('li', { class: 'msg-edit' },
                createEl('span', { class: 'msg-edit-text', text: e.body }),
                at ? createEl('time', { class: 'msg-edit-time mono', datetime: at.toISOString(),
                    title: `${MSG_DAY.format(at)} ${MSG_TIME.format(at)}`, text: MSG_EDIT_TIME.format(at) }) : null);
        }));
    const button = createEl('button', {
        type: 'button', class: 'msg-edited is-toggle', title, 'aria-expanded': 'false', 'aria-controls': list.id,
        onClick: () => msgToggleEdits(button, list),
    }, createEl('span', { text: label }), createEl('i', { class: 'fas fa-chevron-down msg-edited-chevron', 'aria-hidden': 'true' }));
    return { list, el: createEl('div', { class: 'msg-foot' }, button, time) };
}

// De eerdere versies open of dicht. Open: ze komen omhoog en het gesprek eronder glijdt opzij
// (Motion.flip); wat open ging blijft in beeld, met de knop en zijn focusring. Dicht: korter dan
// open en versnellend, daarna sluit het gat; opnieuw openen onderbreekt dat. Uit: meteen.
function msgToggleEdits(button, list) {
    const M = window.Motion;
    const open = list.hidden || list.dataset.closing === '1';
    const foot = button.closest('.msg-foot');
    const scroller = button.closest('.msg-scroll');
    button.setAttribute('aria-expanded', String(open));
    if (open) {
        delete list.dataset.closing;
        const play = M ? M.flipCapture(msgBelow(foot, scroller)) : null;
        list.hidden = false;
        msgKeepInView(scroller, foot);       // gemeten voor de flip iets verschuift
        if (M) {
            M.enter(list, 'rise', { fromCurrent: true });
            play();
        }
        return;
    }
    list.dataset.closing = '1';
    let leaving = null;
    const close = () => {
        if (list.dataset.closing !== '1') return;
        delete list.dataset.closing;
        const play = M ? M.flipCapture(msgBelow(foot, scroller)) : null;
        list.hidden = true;
        if (leaving) leaving.cancel();       // de vervaagde eindstand hoort niet bij de volgende keer open
        if (play) play();
    };
    if (!M || M.off()) { close(); return; }
    leaving = M.exit(list);
    leaving.finished.then(close, () => {});
}

// De voet van de bubbel en wat eronder in beeld staat: dat schuift mee als de versies open- of dichtgaan
function msgBelow(foot, scroller) {
    const out = [foot];
    if (!scroller) return out;
    const bottom = scroller.getBoundingClientRect().bottom;
    let el = foot.closest('.msg-row, .msg-system')?.nextElementSibling;
    for (let n = 0; el && n < 40; el = el.nextElementSibling, n++) {
        if (el.getBoundingClientRect().top > bottom) break;
        out.push(el);
    }
    return out;
}

// Houd de voet (met de knop) binnen het gesprek in beeld: alleen schuiven als hij eronder valt
function msgKeepInView(scroller, foot) {
    if (!scroller) return;
    const view = scroller.getBoundingClientRect();
    const gap = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--space-md')) || 12;
    const over = foot.getBoundingClientRect().bottom + gap - view.bottom;
    if (over > 0) {
        scroller.scrollBy({ top: over, behavior: window.Motion && Motion.full() ? 'smooth' : 'auto' });
    }
}

// ==================== LINKS ====================

const MSG_URL = /https?:\/\/[^\s<>"]+/g;
const MSG_URL_TAIL = /[.,;:!?)\]}'’]+$/;

// Wat een link is: een gedeeld verhaal, post of profiel op Instagram, of anders gewoon de site.
// Instagram zet de link van wat iemand deelde (of het verhaal waar iemand op reageerde) onder
// de tekst van het bericht (formats/meta_dyi.parse_thread).
function msgLinkInfo(url) {
    let u;
    try { u = new URL(url); } catch (_) { return null; }
    const host = u.hostname.replace(/^www\./, '');
    const [first, second] = u.pathname.split('/').filter(Boolean);
    const short = (host + u.pathname).replace(/\/$/, '');
    if (!/(^|\.)instagram\.com$/.test(host)) return { icon: 'fas fa-link', title: host, meta: short };
    const ig = { icon: 'fab fa-instagram', meta: short };
    if (first === 'stories' && second) {
        return { ...ig, title: `Verhaal van @${second}`, meta: 'Instagram · een verhaal staat 24 uur online' };
    }
    if (first === 'p') return { ...ig, title: 'Post op Instagram' };
    if (first === 'reel' || first === 'reels') return { ...ig, title: 'Reel op Instagram' };
    if (first === 'tv') return { ...ig, title: 'Video op Instagram' };
    const user = first === '_u' ? second : (second ? null : first);
    if (user && /^[A-Za-z0-9._]{1,30}$/.test(user) && !['explore', 'accounts', 'direct'].includes(user)) {
        return { ...ig, title: `Profiel van @${user}`, meta: `instagram.com/${user}` };
    }
    return { ...ig, title: 'Link naar Instagram' };
}

function msgShareCard(url) {
    const info = msgLinkInfo(url);
    if (!info) return null;
    return createEl('a', { class: 'msg-share', href: url, target: '_blank', rel: 'noopener noreferrer', title: url },
        createEl('span', { class: 'msg-share-icon', 'aria-hidden': 'true' }, createEl('i', { class: info.icon })),
        createEl('span', { class: 'msg-share-main' },
            createEl('span', { class: 'msg-share-title', text: info.title }),
            createEl('span', { class: 'msg-share-meta', text: info.meta })),
        createEl('i', { class: 'fas fa-arrow-up-right-from-square msg-share-out', 'aria-hidden': 'true' }));
}

// De tekst van een bericht: een link op een eigen regel wordt een kaartje onder de tekst, een
// link midden in een zin wordt klikbaar. Alleen tekst en <a>, nooit HTML uit het bericht.
function msgBody(body) {
    const cards = [];
    const lines = body.split('\n').filter(line => {
        const bare = line.trim();
        const whole = bare.match(MSG_URL);
        if (!whole || whole.length !== 1 || whole[0] !== bare) return true;
        const card = msgShareCard(bare);
        if (card) cards.push(card);
        return !card;
    });
    const text = lines.join('\n').trim();
    if (!text) return cards;
    const p = createEl('p', { class: 'msg-text' });
    let at = 0;
    for (const match of text.matchAll(MSG_URL)) {
        const url = match[0].replace(MSG_URL_TAIL, '');
        p.append(text.slice(at, match.index),
            createEl('a', { class: 'msg-link', href: url, target: '_blank', rel: 'noopener noreferrer', text: url }));
        at = match.index + url.length;
    }
    p.append(text.slice(at));
    return [p, ...cards];
}

// ==================== FOTO'S ====================
// Een foto uit een gesprek groot: hij groeit uit zijn miniatuur (Motion.sharedElement), eerst
// met het voorbeeld dat er al is en scherp zodra de foto zelf geladen is (?size=full). Pijltjes
// en de zones links en rechts bladeren door de foto's van het geladen gesprek; Escape, het
// kruisje of een klik naast de foto sluit, en de foto krimpt dan terug naar zijn bubbel.

function msgMediaItems() {
    const items = [];
    for (const m of msgState.thread ? msgState.thread.messages : []) {
        m.attachments.filter(a => a.preview).forEach(a => items.push({ m, a, key: `${m.id}:${a.index}` }));
    }
    return items;
}

function msgMediaUrl(item, full) {
    return `${API_BASE}/api/messages/attachments/${item.m.id}/${item.a.index}${full ? '?size=full' : ''}`;
}

function msgBuildLightbox() {
    const nav = (side, dir, label) => createEl('button', {
        type: 'button', class: `modal-nav-zone ${side}`, 'aria-label': label, title: `${label} (${dir < 0 ? '←' : '→'})`,
        onClick: (e) => { e.stopPropagation(); msgLightboxStep(dir); },
    }, createEl('span', { class: 'modal-nav-arrow', 'aria-hidden': 'true' },
        createEl('i', { class: `fas fa-chevron-${side}` })));
    const img = createEl('img', { class: 'msg-lightbox-img', alt: '' });
    const stage = createEl('div', { class: 'msg-lightbox-stage', onClick: (e) => {
        // Naast de foto (ook in de lege band rond een foto die niet het hele vak vult): sluiten
        if (e.target === img) {
            const r = msgContainRect(img);
            if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) return;
        }
        msgCloseLightbox();
    } }, img, nav('left', -1, 'Vorige foto'), nav('right', 1, 'Volgende foto'));
    const button = (icon, label, attrs) => createEl(attrs.href ? 'a' : 'button', {
        class: 'msg-lightbox-btn', 'aria-label': label, title: label, ...attrs,
    }, createEl('i', { class: `fas ${icon}`, 'aria-hidden': 'true' }));
    const modal = createEl('div', {
        class: 'modal msg-lightbox', id: 'msg-lightbox', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'msg-lightbox-who',
        onKeydown: (e) => {
            if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
            e.preventDefault();
            e.stopPropagation();
            msgLightboxStep(e.key === 'ArrowLeft' ? -1 : 1);
        },
    }, createEl('div', { class: 'msg-lightbox-bar' },
        createEl('div', { class: 'msg-lightbox-caption' },
            createEl('span', { class: 'msg-lightbox-who', id: 'msg-lightbox-who' }),
            createEl('time', { class: 'msg-lightbox-when mono' })),
        createEl('span', { class: 'msg-lightbox-count mono' }),
        button('fa-download', 'Foto bewaren', { href: '#', download: '', dataset: { role: 'download' } }),
        button('fa-xmark', 'Sluiten (Esc)', { type: 'button', 'data-autofocus': '', onClick: msgCloseLightbox })),
    stage);
    document.body.append(modal);
    registerOverlayCloser('msg-lightbox', msgCloseLightbox);
    return modal;
}

// Het deel van een <img> met object-fit: contain waar de foto echt staat
function msgContainRect(img) {
    const box = img.getBoundingClientRect();
    const aspect = img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : box.width / box.height;
    let w = box.width, h = w / aspect;
    if (h > box.height) { h = box.height; w = h * aspect; }
    const left = box.left + (box.width - w) / 2, top = box.top + (box.height - h) / 2;
    return { left, top, right: left + w, bottom: top + h };
}

function msgOpenLightbox(key, thumb) {
    const items = msgMediaItems();
    const index = items.findIndex(item => item.key === key);
    if (index < 0) return;
    msgState.lightbox = { items, index };
    const modal = document.getElementById('msg-lightbox') || msgBuildLightbox();
    msgShowLightboxItem(0);
    modal.classList.add('active');
    if (window.Motion && thumb && thumb.naturalWidth) {
        Motion.sharedElement(thumb, modal.querySelector('.msg-lightbox-img'), { direction: 'open' });
    }
}

// De foto op msgState.lightbox.index; dir (-1, 1) schuift hem van die kant binnen
function msgShowLightboxItem(dir) {
    const { items, index } = msgState.lightbox;
    const item = items[index];
    const modal = document.getElementById('msg-lightbox');
    const img = modal.querySelector('.msg-lightbox-img');
    const conv = msgState.thread.conversation;
    const when = new Date(item.m.sent_at * 1000);
    img.src = msgMediaUrl(item, false);
    img.alt = item.a.name || 'Foto';
    const full = new Image();
    full.src = msgMediaUrl(item, true);
    full.decode().then(() => {
        if (msgState.lightbox && msgState.lightbox.items[msgState.lightbox.index] === item) img.src = full.src;
    }, () => {});
    modal.querySelector('.msg-lightbox-who').textContent = item.m.is_me ? 'Jij' : (item.m.sender || conv.title);
    const time = modal.querySelector('.msg-lightbox-when');
    time.dateTime = when.toISOString();
    time.textContent = `${MSG_DAY.format(when)} · ${MSG_TIME.format(when)}`;
    modal.querySelector('.msg-lightbox-count').textContent = items.length > 1 ? `${index + 1} / ${items.length}` : '';
    const download = modal.querySelector('[data-role="download"]');
    download.href = full.src;
    download.download = item.a.name || `foto-${item.key.replace(':', '-')}.jpg`;
    modal.querySelector('.modal-nav-zone.left').hidden = index === 0;
    modal.querySelector('.modal-nav-zone.right').hidden = index === items.length - 1;
    if (dir && window.Motion) {
        const d = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--motion-distance-lg')) || 24;
        Motion.animate(img, [{ opacity: 0, transform: `translate3d(${dir * d}px, 0, 0) scale(0.985)` }, { opacity: 1, transform: 'none' }],
            { spring: 'snappy', key: 'msg-lightbox', fromCurrent: true });
    }
}

function msgLightboxStep(dir) {
    const box = msgState.lightbox;
    if (!box) return;
    const next = box.index + dir;
    if (next < 0 || next >= box.items.length) return;
    box.index = next;
    msgShowLightboxItem(dir);
}

function msgCloseLightbox() {
    const modal = document.getElementById('msg-lightbox');
    if (!modal || !modal.classList.contains('active')) return;
    const box = msgState.lightbox;
    const thumb = box && document.querySelector(`#msg-thread img[data-media="${box.items[box.index].key}"]`);
    if (window.Motion && thumb && thumb.naturalWidth) {
        Motion.sharedElement(thumb, modal.querySelector('.msg-lightbox-img'), { direction: 'close' });
    }
    modal.classList.remove('active');
    msgState.lightbox = null;
}

function msgBubble(m, conv, prev) {
    if (m.kind === 'system') {
        return createEl('div', { class: 'msg-system', dataset: { msg: String(m.id) } },
            createEl('span', { text: m.body || '' }));
    }
    const mine = m.is_me === true;
    const sameSender = prev && prev.kind !== 'system' && prev.is_me === m.is_me && prev.sender === m.sender
        && m.sent_at - prev.sent_at < 600;
    const when = new Date(m.sent_at * 1000);
    const bubble = createEl('div', { class: 'msg-bubble' });
    if (!mine && conv.is_group && !sameSender && m.sender) {
        bubble.append(createEl('span', { class: 'msg-sender', text: m.sender }));
    }
    const previews = m.attachments.filter(a => a.preview);
    if (previews.length) {
        bubble.append(createEl('div', { class: `msg-media${previews.length > 1 ? ' is-grid' : ''}` },
            ...previews.map(a => {
                const key = `${m.id}:${a.index}`;
                const img = createEl('img', {
                    class: 'msg-media-img', loading: 'lazy', alt: a.name || 'Afbeelding', dataset: { media: key },
                    src: `${API_BASE}/api/messages/attachments/${m.id}/${a.index}`,
                });
                return createEl('button', { type: 'button', class: 'msg-media-open', title: 'Foto groot bekijken',
                    'aria-label': 'Foto groot bekijken', onClick: () => msgOpenLightbox(key, img) }, img);
            })));
    }
    m.attachments.filter(a => !a.preview).forEach(a => bubble.append(createEl('span', { class: 'msg-file' },
        createEl('i', { class: 'fas fa-paperclip', 'aria-hidden': 'true' }),
        createEl('span', { class: 'msg-file-name', text: a.name || 'Bijlage' }),
        a.local ? createEl('span', { class: 'msg-file-note', text: 'op je Mac' }) : null)));
    if (m.body) bubble.append(...msgBody(m.body));
    else if (m.kind === 'unsent') bubble.append(createEl('p', { class: 'msg-text msg-text-muted', text: 'Bericht ingetrokken' }));
    else if (!m.attachments.length) bubble.append(createEl('p', { class: 'msg-text msg-text-muted', text: 'Media (niet in de export)' }));
    const foot = msgFoot(m, when);
    if (foot.list) bubble.append(foot.list);
    bubble.append(foot.el);
    const row = createEl('div', {
        class: `msg-row${mine ? ' is-me' : ''}${sameSender ? ' is-continued' : ''}${msgState.highlight === m.id ? ' is-hit' : ''}`,
        dataset: { msg: String(m.id) },
    }, bubble);
    if (m.reactions && m.reactions.length) {
        row.append(createEl('div', { class: 'msg-reactions', title: m.reactions.map(r => `${r.actor}: ${r.reaction}`).join(', ') },
            ...m.reactions.slice(0, 6).map(r => createEl('span', { class: 'msg-reaction', text: r.reaction }))));
    }
    return row;
}

// ==================== PASHA ====================

function msgOpenPasha() {
    document.getElementById('msg-pasha')?.remove();
    const rows = msgState.sources.map(s => {
        const input = createEl('input', { type: 'checkbox', id: `msg-pasha-${s.id}`, checked: s.pasha, dataset: { source: s.id } });
        return createEl('div', { class: 'msg-pasha-row' },
            createEl('i', { class: `${s.icon} msg-pasha-icon`, 'aria-hidden': 'true' }),
            createEl('label', { class: 'msg-pasha-label', for: input.id }, s.name,
                createEl('span', { class: 'msg-pasha-count', text: `${s.messages.toLocaleString('nl-BE')} ${s.messages === 1 ? 'bericht' : 'berichten'}` })),
            createEl('label', { class: 'switch' }, input, createEl('span', { class: 'slider' })));
    });
    const close = () => {
        const modal = document.getElementById('msg-pasha');
        if (!modal) return;
        modal.classList.remove('active');
        setTimeout(() => modal.remove(), 250);
    };
    const save = createEl('button', { type: 'button', class: 'btn btn-primary', text: 'Opslaan', 'data-autofocus': '' });
    save.addEventListener('click', async () => {
        save.disabled = true;
        try {
            for (const input of document.querySelectorAll('#msg-pasha input[type=checkbox]')) {
                const src = msgSourceInfo(input.dataset.source);
                if (src.pasha === input.checked) continue;
                await fetchJSON(`${API_BASE}/api/datagatherer/sources/${encodeURIComponent(src.id)}/settings`, {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ settings: { pasha: input.checked } }),
                });
            }
            close();
            showToast('Pasha en je berichten', 'Opgeslagen.', 'success');
            msgLoadSources();
        } catch (err) {
            toastFetchError('Opslaan mislukt', err);
            save.disabled = false;
        }
    });
    const modal = createEl('div', {
        class: 'modal', id: 'msg-pasha', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'msg-pasha-title',
        onClick: (e) => { if (e.target === modal) close(); },
    }, createEl('div', { class: 'modal-container dg-modal-container msg-pasha-container' },
        createEl('div', { class: 'modal-header' },
            createEl('h2', { class: 'modal-title', id: 'msg-pasha-title' },
                createEl('i', { class: 'fas fa-shield-halved', 'aria-hidden': 'true' }), createEl('span', { text: 'Pasha en je berichten' })),
            createEl('button', { type: 'button', class: 'modal-close', 'aria-label': 'Sluiten', title: 'Sluiten (Esc)', onClick: close }, '×')),
        createEl('div', { class: 'modal-body modal-body-list dg-modal-body' },
            createEl('p', { class: 'dg-modal-text', text: 'Pasha leest je berichten alleen van de bronnen die je hier aanzet, '
                + 'en alleen de paar berichten die bij je vraag passen. Alles blijft op deze Mac: Pasha draait lokaal.' }),
            createEl('div', { class: 'msg-pasha-list' }, ...rows)),
        createEl('div', { class: 'modal-footer dg-modal-footer' },
            createEl('span', { class: 'dg-actions-spacer' }),
            createEl('button', { type: 'button', class: 'btn btn-secondary', text: 'Annuleren', onClick: close }), save)));
    document.body.append(modal);
    registerOverlayCloser('msg-pasha', close);
    requestAnimationFrame(() => modal.classList.add('active'));
}
