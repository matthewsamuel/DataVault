// DataVault Dashboard - Berichten
//
// Alle gesprekken die DataGatherer inlas (WhatsApp, iMessage, Messenger, Instagram, ...) op één
// plek: links de lijst met zoeken en een filter per bron, rechts het gesprek als chatbubbels met
// datums en bijlagen. De bronnen komen van de server (/api/messages/sources): deze pagina kent
// geen bron bij naam. Alleen lezen; inlezen gebeurt in DataGatherer.
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
    const header = createEl('div', { class: 'msg-thread-header' },
        msgAvatar(conv.title, conv.source, conv.is_group),
        createEl('div', { class: 'msg-thread-heading' },
            createEl('h2', { class: 'msg-thread-title', text: conv.title }),
            createEl('p', { class: 'msg-thread-meta' },
                createEl('i', { class: src.icon, 'aria-hidden': 'true' }), ` ${src.name} · `,
                createEl('span', { class: 'mono', text: conv.count.toLocaleString('nl-BE') }),
                ` ${conv.count === 1 ? 'bericht' : 'berichten'}`,
                conv.is_group && conv.participants.length ? ` · ${conv.participants.length} deelnemers` : '',
                period ? ' · ' : '', period ? createEl('span', { class: 'mono', text: period }) : '')));
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
        onClick: () => {
            const open = list.hidden;
            list.hidden = !open;
            button.setAttribute('aria-expanded', String(open));
            if (open && window.Motion) Motion.enter(list, 'rise');
        },
    }, createEl('span', { text: label }), createEl('i', { class: 'fas fa-chevron-down msg-edited-chevron', 'aria-hidden': 'true' }));
    return { list, el: createEl('div', { class: 'msg-foot' }, button, time) };
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
            ...previews.map(a => createEl('img', {
                class: 'msg-media-img', loading: 'lazy', alt: a.name || 'Afbeelding',
                src: `${API_BASE}/api/messages/attachments/${m.id}/${a.index}`,
            }))));
    }
    m.attachments.filter(a => !a.preview).forEach(a => bubble.append(createEl('span', { class: 'msg-file' },
        createEl('i', { class: 'fas fa-paperclip', 'aria-hidden': 'true' }),
        createEl('span', { class: 'msg-file-name', text: a.name || 'Bijlage' }),
        a.local ? createEl('span', { class: 'msg-file-note', text: 'op je Mac' }) : null)));
    if (m.body) bubble.append(createEl('p', { class: 'msg-text', text: m.body }));
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
