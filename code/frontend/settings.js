// DataVault Dashboard - Instellingen
// Note: API_BASE is defined in app.js
//
// Eén plek voor alle keuzes. Elk onderdeel meldt zijn eigen stuk aan; de pagina zet ze op
// volgorde (order) onder elkaar, elk met een kop, een korte uitleg en zijn inhoud:
//
//     registerSettingsSection({
//         id: 'kaart',                       // uniek; wordt id="settings-kaart"
//         title: 'Kaart',
//         icon: 'fa-map',                    // Font Awesome-icoon (lokaal in vendor/)
//         order: 60,                         // 10 Weergave, 20 Taalmodel, 30 Bibliotheek,
//                                            // 40 Shows, 50 Herstel, 90 Over deze app
//         description: 'Kaarttegels van OpenStreetMap (alleen als je dat aanzet).',
//         render(body) { body.append(...); } // bij elk openen van Instellingen opnieuw
//     });
//
// render krijgt een lege <div> en vult die met gewone elementen (createEl uit dom.js, nooit
// innerHTML met gegevens). Een keuze die de server moet onthouden, slaat het onderdeel zelf
// op via zijn eigen API (zoals het taalmodel via /api/chat/model); de server bewaart zulke
// keuzes in app_settings (Database.get_setting / set_setting). settingsRefreshSection(id)
// tekent één stuk opnieuw, bijvoorbeeld als er iets veranderde terwijl de pagina open is.

const settingsSections = [];

function registerSettingsSection(section) {
    const at = settingsSections.findIndex(s => s.id === section.id);
    if (at >= 0) settingsSections.splice(at, 1);
    settingsSections.push({ order: 100, ...section });
    settingsSections.sort((a, b) => a.order - b.order);
    if (settingsVisible()) settingsRender();
}

function settingsVisible() {
    const section = document.getElementById('section-settings');
    return !!section && section.classList.contains('active');
}

function settingsInit() {
    settingsRender();
}

function settingsSectionBody(section) {
    const body = createEl('div', { class: 'settings-section-body' });
    try {
        section.render(body);
    } catch (err) {
        console.error(err);
        body.replaceChildren(errorState(`${section.title}: kon niet getoond worden`, err));
    }
    return body;
}

function settingsRender() {
    const box = document.getElementById('settings-sections');
    if (!box) return;
    box.replaceChildren(...settingsSections.map(section => createEl('section', {
        class: 'settings-section', id: `settings-${section.id}`, 'aria-labelledby': `settings-${section.id}-title`,
    },
        createEl('h2', { class: 'settings-section-title', id: `settings-${section.id}-title` },
            createEl('i', { class: `fas ${section.icon || 'fa-sliders-h'}`, 'aria-hidden': 'true' }), ` ${section.title}`),
        section.description ? createEl('p', { class: 'settings-section-text', text: section.description }) : null,
        settingsSectionBody(section))));
}

function settingsRefreshSection(id) {
    const section = settingsSections.find(s => s.id === id);
    const el = document.getElementById(`settings-${id}`);
    if (!section || !el || !settingsVisible()) return;
    const old = el.querySelector(':scope > .settings-section-body');
    // De focus blijft op dezelfde knop staan als die er na het opnieuw tekenen nog is
    const focusedId = document.activeElement && el.contains(document.activeElement) ? document.activeElement.id : null;
    old.replaceWith(settingsSectionBody(section));
    if (focusedId) {
        const again = document.getElementById(focusedId);
        if (again) again.focus();
    }
}

// Een regel "naam: waarde" in een stuk
function settingsRow(label, ...value) {
    return createEl('div', { class: 'settings-row' },
        createEl('span', { class: 'settings-row-label', text: label }),
        createEl('span', { class: 'settings-row-value' }, ...value));
}

// ==================== DE STUKKEN VAN DE APP ZELF ====================

registerSettingsSection({
    id: 'weergave',
    title: 'Weergave',
    icon: 'fa-circle-half-stroke',
    order: 10,
    description: 'Licht of donker, of automatisch zoals macOS. Sneltoets: t.',
    render(body) {
        const theme = currentTheme();
        const choices = [['auto', 'Automatisch'], ['light', 'Licht'], ['dark', 'Donker']];
        // Dezelfde segmentknop als Raster/Lijst, met echte keuzerondjes eronder (toetsenbord: pijlen)
        const group = createEl('div', { class: 'segmented settings-choice-group', role: 'radiogroup', 'aria-label': 'Thema' },
            ...choices.map(([value, label]) => createEl('label', { class: 'settings-choice' },
                createEl('input', {
                    type: 'radio', class: 'radio', name: 'settings-theme', value, checked: value === theme,
                    dataset: { themeChoice: value }, onChange: () => setTheme(value),
                }),
                createEl('span', { text: label }))));
        body.append(group,
            createEl('div', { class: 'settings-actions' },
                createEl('button', { type: 'button', class: 'btn btn-secondary', onClick: openShortcutHelp },
                    createEl('i', { class: 'fas fa-keyboard', 'aria-hidden': 'true' }), ' Sneltoetsen bekijken (?)')));
    },
});

registerSettingsSection({
    id: 'taalmodel',
    title: 'Taalmodel van Pasha',
    icon: 'fa-robot',
    order: 20,
    description: 'Pasha draait met Ollama op deze Mac; er gaat niets naar internet. Kies welk geïnstalleerd model hij gebruikt. '
        + 'Een cloudmodel van Ollama (gemarkeerd met cloud) draait bij ollama.com en vraagt eerst je toestemming.',
    render(body) {
        const select = createEl('select', {
            class: 'toolbar-select chat-model-select', id: 'settings-chat-model', 'aria-label': 'Taalmodel van Pasha',
            onChange: (e) => chooseChatModel(e.target.value),
        });
        select.setAttribute('data-chat-model', '');
        const status = createEl('span', { class: 'chat-status', 'data-chat-status': '', 'data-state': 'pending', text: 'Verbinden…' });
        body.append(
            settingsRow('Model', select),
            settingsRow('Status', status),
            createEl('div', { class: 'settings-actions' },
                createEl('button', { type: 'button', class: 'btn btn-secondary', id: 'settings-chat-reload',
                    text: 'Opnieuw controleren', onClick: loadChatModels })));
        // Pas als het stuk op de pagina staat: renderChatModelSelects vult alle [data-chat-model]
        queueMicrotask(renderChatModelSelects);
    },
});

function settingsLibraryText(lib) {
    if (!lib) return 'Onbekend (is het archief bereikbaar?)';
    if (!lib.ready) return 'Nog niet opgebouwd';
    return `${lib.files.toLocaleString('nl-NL')} bestanden: ${lib.photos.toLocaleString('nl-NL')} foto's, `
        + `${lib.videos.toLocaleString('nl-NL')} video's, ${lib.audio.toLocaleString('nl-NL')} audio, `
        + `in ${lib.folders.toLocaleString('nl-NL')} mappen`;
}

let settingsLibraryAsked = false;

registerSettingsSection({
    id: 'bibliotheek',
    title: 'Bibliotheek',
    icon: 'fa-database',
    order: 30,
    description: 'De app kent elk bestand in het archief (datum, camera, plek) en houdt dat zelf bij: bij het starten, '
        + 'als de schijf terugkomt en elk half uur. Hij leest alleen; hij verplaatst of wist nooit iets.',
    render(body) {
        const lib = typeof jobDock !== 'undefined' ? jobDock.library : null;
        const running = typeof jobDock !== 'undefined'
            && jobDock.jobs.some(j => j.type === 'index' && JOB_ACTIVE.has(j.status));
        body.append(
            settingsRow('Inhoud', settingsLibraryText(lib)),
            settingsRow('Laatst bijgewerkt', lib && lib.last_pass_at ? formatLibraryTime(lib.last_pass_at) : '—'),
            settingsRow('Laatst helemaal gecontroleerd', lib && lib.last_full_at ? formatLibraryTime(lib.last_full_at) : '—'),
            createEl('div', { class: 'settings-actions' },
                createEl('button', {
                    type: 'button', class: 'btn btn-secondary', id: 'settings-library-full', disabled: running,
                    text: running ? 'Bezig…' : 'Alles controleren',
                    title: 'Controleer alle mappen en bestanden, ook bestanden die op hun plek zijn bewerkt',
                    onClick: async (e) => { e.currentTarget.disabled = true; await startLibraryUpdate(); settingsRefreshSection('bibliotheek'); },
                })));
        // Eén keer per keer openen de stand ophalen; daarna tekent een taakwijziging opnieuw
        if (!settingsLibraryAsked && typeof jobsLibraryRefresh === 'function') {
            settingsLibraryAsked = true;
            jobsLibraryRefresh().then(() => settingsRefreshSection('bibliotheek')).finally(() => { settingsLibraryAsked = false; });
        }
    },
});

if (typeof jobsOnChange === 'function') {
    jobsOnChange((job) => { if (job.type === 'index') settingsRefreshSection('bibliotheek'); });
}

registerSettingsSection({
    id: 'shows',
    title: 'Shows',
    icon: 'fa-film',
    order: 40,
    description: 'Welke mappen in de map library meedoen aan Shows.',
    render(body) {
        body.append(createEl('div', { class: 'settings-actions' },
            createEl('button', { type: 'button', class: 'btn btn-secondary', text: 'Bronnen beheren', onClick: openSettingsModal })));
    },
});

registerSettingsSection({
    id: 'herstel',
    title: 'Herstel',
    icon: 'fa-clock-rotate-left',
    order: 50,
    description: 'De app wist nooit iets. Alles wat hij verplaatste, zet je terug in het Herstelcentrum.',
    render(body) {
        body.append(createEl('div', { class: 'settings-actions' },
            createEl('button', { type: 'button', class: 'btn btn-secondary', text: 'Naar het Herstelcentrum',
                onClick: () => switchMainView('recovery') })));
    },
});

registerSettingsSection({
    id: 'over',
    title: 'Over deze app',
    icon: 'fa-circle-info',
    order: 90,
    description: 'DataVault draait helemaal op deze Mac: geen cloud, geen tracking.',
    render(body) {
        const archive = createEl('span', { text: 'Laden…' });
        const archivePathEl = createEl('span', { class: 'path-label' });
        const database = createEl('span', { text: '…' });
        body.append(settingsRow('Archief', archivePathEl, archive), settingsRow('Database', database));
        fetch(`${API_BASE}/api/health`, { cache: 'no-store' })
            .then(res => res.json())
            .then(health => {
                // Hier het volledige pad op de schijf: dit is wáár het archief staat
                archivePathEl.textContent = health.vault_path || '';
                archive.textContent = health.vault_available ? '' : ' (niet bereikbaar)';
                database.textContent = health.database_ready ? 'Klaar' : 'Nog niet geopend';
            })
            .catch(() => { archive.textContent = 'Server niet bereikbaar'; database.textContent = '—'; });
    },
});
