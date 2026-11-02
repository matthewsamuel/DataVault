// Pasha's tools in de chat: resultaten van de server (een strook foto's met een link naar het
// juiste scherm) en bevestigingskaarten voor alles wat bestanden zou verplaatsen. Het model
// kan niets uitvoeren: een kaart doet pas iets na jouw klik op Uitvoeren, en die klik stuurt
// alleen het id van het voorstel naar de server (POST /api/chat/actions/<id>/confirm).
// Alle tekst komt als tekst op het scherm (createEl), nooit als HTML.

function chatScrollDown() {
    const box = document.getElementById('chat-messages');
    if (box) box.scrollTop = box.scrollHeight;
}

function chatCloseFullscreen() {
    const modal = document.getElementById('chat-fullscreen-modal');
    if (modal && modal.classList.contains('active')) closeChatFullscreen();
}

// Opent het scherm dat bij een resultaat hoort, met de juiste filters
function chatOpenLink(link) {
    if (!link || typeof link !== 'object') return;
    chatCloseFullscreen();
    switch (link.view) {
        case 'albums':
            if (link.album != null) albumsShow(link.album);
            else albumsShow(null, link.filters || {});
            break;
        case 'search': openSearch(String(link.query || '')); break;
        case 'otd': albumsOpenOnThisDay(link.month, link.day); break;
        case 'map':
            switchMainView('map');
            if (typeof mapFocus === 'function' && Array.isArray(link.bounds)) mapFocus(link.bounds);
            break;
        case 'series': switchMainView('series'); break;
        case 'recovery': switchMainView('recovery'); break;
        default: break;
    }
}

function chatStrip(items) {
    const grid = createEl('div', { class: 'chat-photo-grid chat-strip' });
    (items || []).forEach(p => {
        const img = createEl('img', {
            class: 'chat-photo', src: vaultFileUrl('thumb', p.path), alt: p.name || '',
            title: p.caption ? `${p.name} (${p.caption})` : (p.name || ''), dataset: { path: p.path },
            loading: 'lazy',
        });
        grid.appendChild(p.caption
            ? createEl('figure', { class: 'chat-strip-item' }, img, createEl('figcaption', { text: p.caption }))
            : img);
    });
    return grid;
}

// play_song: het nummer van 'Spotify op deze dag', meteen gestart in de Spotify-app op deze Mac
// (POST /api/spotify/play). Lukt dat niet, dan een knop naar de webspeler of de zoekpagina.
function chatPlayCard(p) {
    const cover = createEl('div', { class: 'chat-play-cover', 'aria-hidden': 'true' },
        createEl('i', { class: 'fas fa-music' }));
    if (p.cover) {
        const img = createEl('img', { class: 'chat-play-cover-img', src: `${API_BASE}${p.cover}`, alt: '' });
        img.addEventListener('error', () => img.remove(), { once: true });
        cover.appendChild(img);
    }
    const eq = createEl('span', { class: 'chat-play-eq', 'aria-hidden': 'true' },
        createEl('span'), createEl('span'), createEl('span'));
    const statusText = createEl('span');
    const statusIcon = createEl('i', { class: 'fas', 'aria-hidden': 'true' });
    const status = createEl('div', { class: 'chat-play-status', role: 'status' }, eq, statusIcon, statusText);
    const actions = createEl('div', { class: 'chat-confirm-actions' });
    const card = createEl('div', { class: 'chat-play is-starting', role: 'group', 'aria-label': `${p.track} van ${p.artist}` },
        cover,
        createEl('div', { class: 'chat-play-info' },
            createEl('div', { class: 'chat-play-overline' },
                createEl('i', { class: 'fab fa-spotify', 'aria-hidden': 'true' }), ' Spotify op deze dag'),
            createEl('div', { class: 'chat-play-track', text: p.track }),
            createEl('div', { class: 'chat-play-artist', text: p.artist }),
            status, actions));

    const button = (label, icon, onClick) => createEl('button', { type: 'button', class: 'btn btn-secondary btn-sm', onClick },
        createEl('i', { class: `fas ${icon}`, 'aria-hidden': 'true' }), ` ${label}`);
    // starting en playing met de staafjes; note (iets wat jij kunt oplossen) rustig met een i, error in de foutkleur
    const settle = (state, text, link) => {
        card.classList.remove('is-starting', 'is-playing', 'is-note', 'is-error');
        card.classList.add(`is-${state}`);
        statusIcon.className = `fas ${state === 'note' ? 'fa-circle-info' : state === 'error' ? 'fa-triangle-exclamation' : ''}`;
        statusText.textContent = text;
        status.setAttribute('role', state === 'error' ? 'alert' : 'status');
        actions.replaceChildren(...(link ? [link] : []));
        chatScrollDown();
    };
    const search = () => button('Zoeken in Spotify', 'fa-magnifying-glass', () => openSpotifySearch(`${p.track} ${p.artist}`));

    if (!p.uri) {
        settle('note', 'Ik weet de Spotify-link van dit nummer niet, dus ik kan het niet zelf starten.', search());
        return chatBubble(card);
    }
    const start = () => {
        settle('starting', 'Starten in Spotify…');
        fetchJSON(`${API_BASE}/api/spotify/play`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uri: p.uri }),
        }).then(done => {
            if (done.played) settle('playing', 'Speelt nu in Spotify', button('Opnieuw afspelen', 'fa-rotate-right', start));
            else settle(['not_allowed', 'not_installed'].includes(done.code) ? 'note' : 'error',
                done.message || 'Spotify kon het nummer niet starten.',
                done.web ? button('Openen in de webspeler', 'fa-arrow-up-right-from-square', () => openExternal(done.web)) : search());
        }).catch(e => settle('error', e.message || 'Spotify kon het nummer niet starten.', search()));
    };
    start();
    return chatBubble(card);
}

function chatToolResult(result) {
    if (result.play) return chatPlayCard(result.play);
    const body = createEl('div', { class: 'chat-tool' },
        createEl('div', { class: 'chat-tool-title', text: result.title || '' }));
    if (result.items && result.items.length) body.appendChild(chatStrip(result.items));
    if (result.lines && result.lines.length) {
        body.appendChild(createEl('ul', { class: 'chat-tool-lines' },
            ...result.lines.map(l => createEl('li', { text: l }))));
    }
    if (result.link) {
        body.appendChild(createEl('button', {
            type: 'button', class: 'btn btn-ghost btn-sm chat-tool-link',
            onClick: () => chatOpenLink(result.link),
        }, createEl('i', { class: 'fas fa-arrow-up-right-from-square', 'aria-hidden': 'true' }), ` ${result.link.label || 'Openen'}`));
    }
    return chatBubble(body);
}

function chatConfirmCard(p) {
    const status = createEl('div', { class: 'chat-confirm-status', role: 'status' });
    const list = createEl('ul', { class: 'chat-confirm-list' },
        ...(p.items || []).map(i => createEl('li', {},
            createEl('span', { class: 'mono', text: i.name }),
            createEl('span', { class: 'chat-confirm-to', text: ` → ${i.to}` }))));
    if (p.more) list.appendChild(createEl('li', { class: 'chat-confirm-more', text: `en nog ${p.more} andere` }));
    const run = createEl('button', { type: 'button', class: 'btn btn-primary btn-sm', text: 'Uitvoeren' });
    const cancel = createEl('button', { type: 'button', class: 'btn btn-secondary btn-sm', text: 'Annuleren' });
    // Zoals elke bevestiging in de app: Annuleren (secundair) links van de hoofdknop
    const actions = createEl('div', { class: 'chat-confirm-actions' }, cancel, run);
    const card = createEl('div', { class: 'chat-confirm', role: 'group', 'aria-label': 'Bevestig de actie' },
        createEl('div', { class: 'chat-tool-title' },
            createEl('i', { class: 'fas fa-shield-halved', 'aria-hidden': 'true' }), ` ${p.title}`),
        list,
        createEl('div', { class: 'chat-confirm-note', text: 'Er gebeurt niets voordat je op Uitvoeren klikt. Verplaatsen kun je terugzetten in het Herstelcentrum.' }),
        actions, status);
    // De uitkomst als inline melding: een fout met waarschuwingsicoon in de foutkleur, anders rustig met een vinkje
    const settle = (text, isError) => {
        actions.remove();
        status.replaceChildren(
            createEl('i', { class: `fas ${isError ? 'fa-triangle-exclamation' : 'fa-circle-check'}`, 'aria-hidden': 'true' }),
            createEl('span', { text }));
        status.classList.toggle('is-error', !!isError);
        if (isError) status.setAttribute('role', 'alert');
        chatScrollDown();
    };
    const call = (verb) => fetchJSON(`${API_BASE}/api/chat/actions/${encodeURIComponent(p.id)}/${verb}`, { method: 'POST' });
    run.addEventListener('click', async () => {
        run.disabled = cancel.disabled = true;
        try {
            const done = await call('confirm');
            if (done.open_export) {
                settle(done.message || 'Het exportvenster opent.');
                chatCloseFullscreen();
                if (window.DVExport && done.open_export.length) DVExport.openExport(done.open_export);
                return;
            }
            settle(done.message || 'Klaar.', done.moved === 0);
            if (done.moved) {
                showToast('Pasha', done.message, 'success');
                if (typeof loadFolders === 'function') try { loadFolders(); } catch (_) { /* scherm ververst later */ }
            }
        } catch (e) {
            settle(e.message || 'Uitvoeren mislukt.', true);
        }
    }, { once: true });
    cancel.addEventListener('click', async () => {
        run.disabled = cancel.disabled = true;
        try { await call('cancel'); } catch (_) { /* verlopen: ook goed */ }
        settle('Geannuleerd; er is niets verplaatst.', false);
    }, { once: true });
    return chatBubble(card);
}

// Eén 'tool'-event uit de stroom van /api/chat/stream
function chatRenderToolEvent(event) {
    const box = document.getElementById('chat-messages');
    if (!box) return;
    if (event.error) box.appendChild(chatBubble(createEl('span', { class: 'chat-tool-error', text: event.error })));
    else if (event.pending) box.appendChild(chatConfirmCard(event.pending));
    else if (event.result) box.appendChild(chatToolResult(event.result));
    chatScrollDown();
}
