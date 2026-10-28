// DataVault Dashboard - Instellingen > Back-ups
// Note: API_BASE is defined in app.js
//
// De database (scores, vergelijkingen, Keurer-keuzes, herstelgegevens, instellingen, de index)
// staat op de T7. De server kopieert hem elke dag naar de schijf van deze Mac (backend/backup.py);
// hier zie je de laatste back-up, waar ze staan, maak je er zelf een en lees je hoe je er een
// terugzet. Laadt na settings.js.

let backupBusy = false;

function backupWhen(seconds) {
    if (!seconds) return '—';
    const when = new Date(seconds * 1000);
    const today = new Date();
    const yesterday = new Date(today.getTime() - 86400000);
    const time = when.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
    if (when.toDateString() === today.toDateString()) return `vandaag ${time}`;
    if (when.toDateString() === yesterday.toDateString()) return `gisteren ${time}`;
    return `${when.toLocaleDateString('nl-NL', { day: 'numeric', month: 'short', year: 'numeric' })} ${time}`;
}

function backupClock(seconds) {
    return new Date(seconds * 1000).toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
}

// Wanneer de volgende back-up komt. De app kijkt elk uur (en kort na het starten) of er een
// nodig is; is er nog geen, of is de laatste ouder dan een dag, dan komt hij bij die controle.
function backupNext(status) {
    if (!status.auto) return 'Automatische back-ups staan uit';
    if (status.running) return 'Wordt nu gemaakt';
    const due = status.next_due_at ? status.next_due_at * 1000 : 0;
    if (due <= Date.now()) {
        const check = status.next_check_at ? status.next_check_at * 1000 : 0;
        if (check > Date.now()) return `Bij de volgende controle, rond ${backupClock(status.next_check_at)}`;
        return `Bij de volgende controle (elke ${status.check_interval_minutes === 60 ? 'uur' : `${status.check_interval_minutes} minuten`})`;
    }
    const tomorrow = new Date(Date.now() + 86400000);
    const when = new Date(due);
    const time = when.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
    return when.toDateString() === tomorrow.toDateString() ? `morgen rond ${time}` : `rond ${backupWhen(status.next_due_at)}`;
}

function backupCommandBlock(command, label) {
    // Een optie (--config) blijft heel; lange paden mogen afbreken. De tekst zelf is precies de opdracht.
    const parts = [];
    command.split(' ').forEach((word, i) => {
        if (i) parts.push(' ');
        parts.push(word.startsWith('-') ? createEl('span', { class: 'backup-command-option', text: word }) : word);
    });
    const code = createEl('code', { class: 'backup-command-text' }, ...parts);
    const copy = createEl('button', {
        type: 'button', class: 'btn btn-ghost btn-sm btn-icon backup-copy', 'aria-label': `${label} kopiëren`, title: 'Kopiëren',
        onClick: async () => {
            try {
                await navigator.clipboard.writeText(command);
                showToast('Gekopieerd', 'Plak het in Terminal.', 'success');
            } catch (_) {
                showToast('Kopiëren lukte niet', 'Selecteer de opdracht en kopieer hem zelf.', 'warning');
            }
        },
    }, createEl('i', { class: 'fas fa-copy', 'aria-hidden': 'true' }));
    return createEl('div', { class: 'backup-command' }, code, copy);
}

function backupRestoreHelp(status) {
    const list = `${status.restore_command}`;
    const one = `${status.restore_command} <naam>`;
    return createEl('details', { class: 'backup-restore' },
        createEl('summary', null, createEl('i', { class: 'fas fa-clock-rotate-left', 'aria-hidden': 'true' }), ' Een back-up terugzetten'),
        createEl('ol', { class: 'backup-steps' },
            createEl('li', { text: 'Stop DataVault (stop_datavault.sh, of sluit de app). Zolang de app draait, weigert het terugzetten.' }),
            createEl('li', null, 'Bekijk in Terminal welke back-ups er zijn:', backupCommandBlock(list, 'Opdracht om de back-ups te tonen')),
            createEl('li', null, 'Zet er een terug (het vraagt eerst om "ja"):', backupCommandBlock(one, 'Opdracht om terug te zetten')),
            createEl('li', { text: 'Start DataVault weer. Wat niet in de back-up zat (de caches) bouwt de app vanzelf opnieuw op.' })),
        createEl('p', { class: 'backup-note',
            text: 'Vóór het terugzetten bewaart het script de huidige database helemaal, als "datavault-voor-herstel-…" in dezelfde map. '
                + 'Er wordt nooit iets overschreven of weggegooid; spijt je ervan, dan zet je die kopie op dezelfde manier terug.' }));
}

function backupRenderStatus(box, status) {
    const last = status.last;
    const lastValue = last
        ? [createEl('span', { text: backupWhen(last.created_at) }),
           createEl('span', { class: 'tabular backup-size', text: ` · ${formatBytes(last.size)}` }),
           createEl('span', { class: `badge ${last.kind === 'handmatig' ? 'badge-info' : 'badge-success'} backup-kind`,
               text: last.kind === 'handmatig' ? 'Handmatig' : 'Automatisch' })]
        : [createEl('span', { class: 'backup-none', text: 'Nog geen back-up' })];
    const kept = status.count === 1 ? '1 back-up' : `${formatNumberNl(status.count)} back-ups`;
    const rule = `de laatste ${status.keep_daily} dagen, ${status.keep_weekly} weken en je ${status.keep_manual} nieuwste handmatige`;
    const rows = [
        settingsRow('Laatste back-up', ...lastValue),
        settingsRow('Volgende', backupNext(status)),
        settingsRow('Locatie', createEl('span', { class: 'path-label', text: status.folder, title: status.folder })),
        status.count
            ? settingsRow('Bewaard', createEl('span', { text: `${kept}, samen ` }),
                createEl('span', { class: 'tabular', text: formatBytes(status.total_size) }),
                createEl('span', { class: 'backup-muted', text: ` · ${rule}` }))
            : settingsRow('Bewaard', createEl('span', { class: 'backup-none', text: 'Nog niets bewaard' }),
                createEl('span', { class: 'backup-muted', text: ` · straks ${rule}` })),
    ];
    if (status.folder_note) {
        rows.push(createEl('div', { class: 'backup-error' },
            createEl('i', { class: 'fas fa-circle-info', 'aria-hidden': 'true' }),
            createEl('span', { text: status.folder_note })));
    }
    if (status.last_error) {
        rows.push(createEl('div', { class: 'backup-error', role: 'alert' },
            createEl('i', { class: 'fas fa-triangle-exclamation', 'aria-hidden': 'true' }),
            createEl('span', { text: `Laatste poging mislukt (${backupWhen(status.last_error.at)}): ${status.last_error.message}` })));
    }
    const running = backupBusy || status.running;
    const button = createEl('button', {
        type: 'button', id: 'settings-backup-now', class: `btn btn-secondary${running ? ' is-loading' : ''}`, disabled: running,
        onClick: backupNow,
    'aria-busy': running ? 'true' : null }, createEl('i', { class: 'fas fa-shield-halved', 'aria-hidden': 'true' }), ' Nu back-uppen');
    box.replaceChildren(...rows,
        createEl('p', { class: 'backup-note',
            text: 'Elke back-up wordt eerst op fouten gecontroleerd en pas daarna bewaard. Niet erin: wat de app '
                + 'zelf opnieuw maakt (hashes, datums, de mappenlijst van de index, scanresultaten); zo blijft hij klein.' }),
        createEl('div', { class: 'settings-actions' }, button),
        backupRestoreHelp(status));
    if (backupRefocus) {
        backupRefocus = false;
        button.focus();
    }
}

// Na het opnieuw tekenen de focus terug op 'Nu back-uppen' (het stuk wordt pas getekend als
// de stand binnen is)
let backupRefocus = false;

async function backupNow(e) {
    if (backupBusy) return;
    backupBusy = true;
    const button = e && e.currentTarget;
    const box = button && button.closest('.backup-status');
    if (button) {
        button.classList.add('is-loading');
        button.setAttribute('aria-busy', 'true');
    }
    let status = null;
    try {
        const data = await fetchJSON(`${API_BASE}/api/backup/run`, { method: 'POST' });
        status = data.status;
        showToast('Back-up gemaakt', `${formatBytes(data.backup.size)}, gecontroleerd en bewaard.`, 'success');
    } catch (err) {
        toastFetchError('Back-up mislukt', err);
    }
    backupBusy = false;
    backupRefocus = !!button && document.activeElement === button;
    // Op dezelfde plek opnieuw tekenen, zonder skelet ertussen: dan springt de pagina niet
    try {
        if (!status) status = await fetchJSON(`${API_BASE}/api/backup/status`);
    } catch (_) {
        status = null;
    }
    if (box && box.isConnected && status) {
        // De knop blijft op dezelfde plek in beeld (Chromium verschuift anders de pagina als de
        // inhoud vervangen wordt)
        const before = button.getBoundingClientRect().top;
        backupRenderStatus(box, status);
        const now = document.getElementById('settings-backup-now');
        if (now) window.scrollBy(0, now.getBoundingClientRect().top - before);
    } else {
        settingsRefreshSection('backups');
    }
}

registerSettingsSection({
    id: 'backups',
    title: 'Back-ups',
    icon: 'fa-shield-halved',
    order: 80,
    description: 'Je scores, vergelijkingen, Keurer-keuzes en herstelgegevens staan in de database op de T7. '
        + 'De app kopieert die elke dag naar de schijf van deze Mac, los van de T7.',
    render(body) {
        const box = createEl('div', { class: 'backup-status' }, skeletonRows(2));
        body.append(box);
        fetchJSON(`${API_BASE}/api/backup/status`)
            .then(status => backupRenderStatus(box, status))
            .catch(err => box.replaceChildren(errorState('Back-ups niet op te vragen', err, () => settingsRefreshSection('backups'))));
    },
});
