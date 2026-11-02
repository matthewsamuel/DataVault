// ==================== EXPORTEREN ====================
// Een selectie (het rondje op een tegel, klikken, shift-klikken, cmd-klikken of spatie in
// Albums en de tijdlijn) of de beste foto's van een periode kopieer je naar een map buiten het
// archief. Het archief zelf verandert nooit: er wordt alleen gelezen en gekopieerd. Het
// kopiëren is een achtergrondtaak (type 'export', zie backend/export.py); de voortgang staat
// in het takenpaneel.
// Dezelfde selectie kun je ook in één keer verplaatsen of naar de prullenmand doen (⌫); dat is
// één actie in het Herstelcentrum, en de melding heeft 'Ongedaan maken'.
//
//   DVExport.toggle(file) / has(path) / clear() / selectRange(files) / selectMany(files)
//   DVExport.setSelectAll(fn)   fn() geeft alle bestanden van de huidige filter (Albums)
//   DVExport.openExport(files)  het exportvenster; DVExport.openBest() 'Beste van een periode'
//   DVExport.onMoved(fn)        fn(moved, {trashed}) na verplaatsen: [{path, new_path}]
//   DVExport.onRestored(fn)     fn() na 'Ongedaan maken': de bestanden staan weer op hun plek
//   DVExport.trashFiles(files)  losse bestanden naar de prullenmand (met melding); geeft de verplaatste
//   DVExport.selectButton(fn)   het rondje voor op een tegel; fn(event) bij een klik

(function () {
    'use strict';

    const MONTHS = ['januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september',
        'oktober', 'november', 'december'];
    const EDGES = [[1024, '1024 px (klein, om te mailen)'], [2048, '2048 px (om te delen)'], [4096, '4096 px (groot)']];
    const MAX_ALL = 20000;

    const selection = new Map();        // pad -> {path, name, size}
    const listeners = [];
    const movedListeners = [];
    const restoredListeners = [];
    let moving = false;                 // één verplaatsing of verwijdering tegelijk
    let selectAllFn = null;
    let selectAllCount = null;
    let bar = null;

    const $$ = (id) => document.getElementById(id);
    const icon = (name) => createEl('i', { class: `fas ${name}`, 'aria-hidden': 'true' });
    const pickFile = (f) => ({ path: f.path, name: f.name, size: f.size || 0 });
    const post = (url, body) => fetchJSON(`${API_BASE}${url}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
    });

    // ── Selectie ─────────────────────────────────────────

    function changed() {
        renderBar();
        listeners.forEach(fn => { try { fn(); } catch (e) { console.error(e); } });
    }

    function totalBytes() {
        let n = 0;
        selection.forEach(f => { n += f.size || 0; });
        return n;
    }

    const DVExport = {
        get count() { return selection.size; },
        has: (path) => selection.has(path),
        files: () => [...selection.values()],
        onChange: (fn) => listeners.push(fn),
        toggle(file) {
            if (selection.has(file.path)) selection.delete(file.path); else selection.set(file.path, pickFile(file));
            changed();
        },
        selectMany(files) {
            files.forEach(f => selection.set(f.path, pickFile(f)));
            changed();
        },
        // Shift-klik: alles tussen het laatst gekozen bestand en dit bestand (de lijst is in beeldvolgorde)
        selectRange(files) {
            DVExport.selectMany(files);
        },
        remove(paths) {
            paths.forEach(p => selection.delete(p));
            changed();
        },
        clear() {
            if (!selection.size) return;
            selection.clear();
            changed();
        },
        setSelectAll(fn, count) {
            selectAllFn = fn;
            selectAllCount = count == null ? null : count;
            renderBar();
        },
        openExport,
        openBest,
        onMoved: (fn) => movedListeners.push(fn),
        onRestored: (fn) => restoredListeners.push(fn),
        selectButton(onClick) {
            return createEl('button', {
                type: 'button', class: 'thumb-select', tabindex: '-1', 'aria-label': 'Selecteren', title: 'Selecteren',
                onClick: (e) => { e.stopPropagation(); onClick(e); },
            });
        },
        moveSelection,
        trashSelection,
        trashFiles,
    };
    window.DVExport = DVExport;

    // ── Verplaatsen en verwijderen ───────────────────────

    function notify(list, ...args) {
        list.forEach(fn => { try { fn(...args); } catch (e) { console.error(e); } });
    }

    function plural(n) {
        return `${n.toLocaleString('nl-NL')} ${n === 1 ? 'bestand' : 'bestanden'}`;
    }

    // Wat er gebeurd is: de views halen de bestanden weg, de selectie laat ze los, een melding
    // met 'Ongedaan maken' (de hele actie terug via het Herstelcentrum)
    function finishMove(result, trashed, where) {
        const moved = result.moved || [];
        const skipped = result.skipped || [];
        if (moved.length) {
            notify(movedListeners, moved, { trashed });
            moved.forEach(m => selection.delete(m.path));
            changed();
        }
        const undo = result.action_id && moved.length ? { label: 'Ongedaan maken', onClick: () => undoAction(result.action_id) } : null;
        const skippedText = skipped.length ? ` ${plural(skipped.length)} overgeslagen: ${skipped[0].reason.toLowerCase()}${skipped.length > 1 ? ' …' : ''}.` : '';
        if (result.error) {
            showToast(trashed ? 'Verwijderen gestopt' : 'Verplaatsen gestopt', `${result.error}.${skippedText}`, 'error', undo);
        } else if (!moved.length) {
            showToast(trashed ? 'Niets verwijderd' : 'Niets verplaatst', skippedText.trim() || 'Er was niets om te verplaatsen.', 'warning');
        } else {
            showToast(trashed ? 'Naar de prullenmand' : 'Verplaatst',
                `${plural(moved.length)} ${trashed ? 'in de prullenmand' : `naar ${where}`}.${skippedText} Terugzetten kan ook in het Herstelcentrum.`,
                skipped.length ? 'warning' : 'success', undo);
        }
    }

    async function undoAction(actionId) {
        try {
            const r = await post('/api/recovery/undo', { action_id: actionId });
            const back = r.restored || 0;
            showToast('Teruggezet', `${plural(back)} weer op de oude plek${r.skipped && r.skipped.length ? `, ${r.skipped.length} niet (zie het Herstelcentrum)` : ''}.`,
                r.skipped && r.skipped.length ? 'warning' : 'success');
            notify(restoredListeners);
        } catch (e) { toastFetchError('Terugzetten mislukt', e); }
    }

    // Het verplaatsvenster van de viewer, met de hele selectie
    function moveSelection() {
        if (moving || !selection.size || typeof openMoveFileModal !== 'function') return;
        openMoveFileModal(DVExport.files(), {
            onMove: async (folder) => {
                moving = true;
                try {
                    const result = await post('/api/move-files', { paths: DVExport.files().map(f => f.path), destination_folder: folder });
                    finishMove(result, false, folder.split('/').filter(Boolean).pop() || 'het archief');
                    return true;
                } catch (e) {
                    toastFetchError('Verplaatsen mislukt', e);
                    return false;
                } finally { moving = false; }
            },
        });
    }

    // 'Verwijderen' is naar de prullenmand (TRASH) verplaatsen: er wordt nooit iets gewist
    async function trashSelection() {
        if (moving || !selection.size) return;
        moving = true;
        renderBar();
        try {
            finishMove(await post('/api/trash-files', { paths: DVExport.files().map(f => f.path) }), true);
        } catch (e) {
            toastFetchError('Verwijderen mislukt', e);
        } finally {
            moving = false;
            renderBar();
        }
    }

    // Losse bestanden naar de prullenmand, buiten de selectie om (Opslag per map). Geeft de
    // verplaatste terug ([{path, new_path}]), leeg als er niets weg is.
    async function trashFiles(files) {
        try {
            const result = await post('/api/trash-files', { paths: files.map(f => f.path) });
            finishMove(result, true);
            return result.moved || [];
        } catch (e) {
            toastFetchError('Verwijderen mislukt', e);
            return [];
        }
    }

    // ── Selectiebalk ─────────────────────────────────────

    function ensureBar() {
        if (bar) return bar;
        bar = createEl('div', { class: 'export-bar', id: 'export-bar', role: 'region', 'aria-label': 'Selectie', hidden: true });
        document.body.appendChild(bar);
        return bar;
    }

    let shownCount = 0;
    let barLeaving = null;
    function renderBar() {
        const el = ensureBar();
        const n = selection.size;
        const before = shownCount;
        shownCount = n;
        if (!n) {
            // De balk zakt weg (korter dan hij kwam) en is meteen niet meer klikbaar
            if (el.hidden || !before) { el.hidden = true; el.replaceChildren(); return; }
            el.style.pointerEvents = 'none';
            const leaving = barLeaving = Motion.animate(el, [
                { opacity: 1, transform: 'translate3d(-50%, 0, 0)' },
                { opacity: 0, transform: `translate3d(-50%, ${Motion.px('--motion-distance-lg', 24)}px, 0) scale(0.96)` },
            ], { duration: '--dur-3', easing: '--ease-exit', fill: 'forwards', key: 'bar' });
            leaving.finished.then(() => {
                if (barLeaving !== leaving) return;
                barLeaving = null;
                el.hidden = true;
                el.replaceChildren();
                el.style.pointerEvents = '';
                leaving.cancel();
            }, () => {});
            return;
        }
        // Weer iets gekozen terwijl de balk wegzakte: hij blijft gewoon staan
        if (barLeaving) { const leaving = barLeaving; barLeaving = null; leaving.cancel(); el.style.pointerEvents = ''; }
        el.hidden = false;
        const buttons = [];
        if (selectAllFn && selectAllCount && selectAllCount > n) {
            buttons.push(createEl('button', {
                type: 'button', class: 'btn btn-ghost btn-sm', id: 'export-select-all',
                onClick: async (e) => {
                    const button = e.currentTarget;
                    button.disabled = true;
                    try {
                        const files = await selectAllFn();
                        if (files && files.length) DVExport.selectMany(files);
                    } catch (err) { toastFetchError('Alles selecteren mislukt', err); } finally { button.disabled = false; }
                },
            }, `Alles selecteren (${selectAllCount.toLocaleString('nl-NL')})`));
        }
        buttons.push(createEl('button', { type: 'button', class: 'btn btn-ghost btn-sm', id: 'export-clear', title: 'Selectie wissen (Esc)', onClick: () => DVExport.clear() }, 'Wissen'));
        buttons.push(createEl('span', { class: 'export-bar-divider', 'aria-hidden': 'true' }));
        buttons.push(createEl('button', { type: 'button', class: 'btn btn-secondary btn-sm', id: 'selection-move', disabled: moving,
            title: 'Naar een andere map in het archief', onClick: moveSelection }, icon('fa-folder-open'), 'Verplaatsen…'));
        buttons.push(createEl('button', { type: 'button', class: `btn btn-danger btn-sm${moving ? ' is-loading' : ''}`, id: 'selection-trash', disabled: moving,
            title: 'Naar de prullenmand (⌫); terugzetten kan in het Herstelcentrum', onClick: trashSelection }, icon('fa-trash-can'), 'Verwijderen'));
        buttons.push(createEl('button', { type: 'button', class: 'btn btn-primary btn-sm', id: 'export-open', onClick: () => openExport(DVExport.files()) },
            icon('fa-file-export'), 'Exporteren…'));
        el.replaceChildren(
            createEl('span', { class: 'export-bar-count tabular', 'aria-live': 'polite' },
                `${n.toLocaleString('nl-NL')} geselecteerd`,
                createEl('span', { class: 'export-bar-size', text: ` · ${formatBytes(totalBytes())}` })),
            createEl('span', { class: 'export-bar-spacer' }), ...buttons);
        // Het aantal tikt mee: omhoog bij erbij, omlaag bij eraf
        if (before && before !== n) MediaMotion.nudge(el.querySelector('.export-bar-count'), n > before ? 1 : -1);
    }

    // Een tegel met modifiers selecteert; zolang er iets geselecteerd is, selecteert een gewone klik ook
    DVExport.wantsSelect = (event) => event.shiftKey || event.metaKey || event.ctrlKey || selection.size > 0;

    document.addEventListener('keydown', (e) => {
        if (!selection.size || anyOverlayOpen() || isTypingTarget(e.target)) return;
        if (e.key === 'Escape') DVExport.clear();
        // ⌫ of Delete: de selectie naar de prullenmand (de melding kan het terugdraaien)
        else if ((e.key === 'Backspace' || e.key === 'Delete') && !e.altKey && !e.ctrlKey && bar && !bar.hidden) {
            e.preventDefault();
            trashSelection();
        }
    });

    // ── Modal ────────────────────────────────────────────

    function modal(id, title, iconName, body, footer, wide) {
        let el = $$(id);
        if (el) el.remove();
        el = createEl('div', { class: 'modal', id, role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': `${id}-title` },
            createEl('div', { class: `modal-container export-modal${wide ? ' export-modal-wide' : ''}` },
                createEl('div', { class: 'modal-header' },
                    createEl('h2', { class: 'modal-title', id: `${id}-title` }, icon(iconName), ' ', title),
                    createEl('button', { type: 'button', class: 'modal-close', 'aria-label': 'Sluiten', title: 'Sluiten (Esc)', text: '×', onClick: () => closeModal(id) })),
                createEl('div', { class: 'modal-body export-body' }, body),
                createEl('div', { class: 'modal-footer' }, footer)));
        el.addEventListener('mousedown', (e) => { if (e.target === el) closeModal(id); });
        document.body.appendChild(el);
        registerOverlayCloser(id, () => closeModal(id));
        requestAnimationFrame(() => el.classList.add('active'));
        return el;
    }

    function closeModal(id) {
        const el = $$(id);
        if (el) el.remove();
    }

    function field(label, control, hint, id) {
        return createEl('div', { class: 'export-field' },
            createEl('label', { class: 'export-label', for: id, text: label }), control,
            hint ? createEl('p', { class: 'export-hint', text: hint }) : null);
    }

    function radio(name, value, label, checked, onChange) {
        const input = createEl('input', { type: 'radio', class: 'radio', name, value });
        input.checked = checked;
        input.addEventListener('change', () => onChange(value));
        return createEl('label', { class: 'export-choice' }, input, createEl('span', { text: label }));
    }

    function select(id, options, value, onChange) {
        const el = createEl('select', { class: 'select', id });
        options.forEach(([v, label]) => el.appendChild(createEl('option', { value: String(v), text: label })));
        el.value = String(value);
        el.addEventListener('change', () => onChange(el.value));
        return el;
    }

    // ── Exportvenster ────────────────────────────────────

    async function openExport(files) {
        if (!files || !files.length) return;
        const state = { mode: 'originals', edge: 2048, structure: 'flat', manifest: 'csv', base: '', name: '', plan: null, busy: false };
        const stamp = new Date();
        state.name = `Export ${stamp.getFullYear()}-${String(stamp.getMonth() + 1).padStart(2, '0')}-${String(stamp.getDate()).padStart(2, '0')}`;
        try {
            state.base = (await fetchJSON(`${API_BASE}/api/export/defaults`)).base;
        } catch (e) { state.base = ''; }
        const paths = files.map(f => f.path);

        const baseInput = createEl('input', { type: 'text', class: 'input mono', id: 'export-base', value: state.base, spellcheck: 'false', autocomplete: 'off' });
        const nameInput = createEl('input', { type: 'text', class: 'input', id: 'export-name', value: state.name, autocomplete: 'off' });
        const chooseBtn = createEl('button', { type: 'button', class: 'btn btn-secondary', id: 'export-choose', onClick: async () => {
            chooseBtn.disabled = true;
            try {
                const r = await post('/api/export/choose-folder');
                if (r.path) { baseInput.value = r.path; state.base = r.path; refresh(); }
            } catch (e) { toastFetchError('Map kiezen mislukt', e); } finally { chooseBtn.disabled = false; }
        } }, icon('fa-folder-open'), 'Kies map…');
        const status = createEl('div', { class: 'export-status', id: 'export-status', role: 'status', 'aria-live': 'polite' });
        const edgeSelect = select('export-edge', EDGES, state.edge, (v) => { state.edge = Number(v); refresh(); });
        const edgeField = field('Langste zijde', edgeSelect, 'HEIC wordt JPEG. Video en audio worden ongewijzigd gekopieerd.', 'export-edge');
        edgeField.hidden = true;

        const startBtn = createEl('button', { type: 'button', class: 'btn btn-primary', id: 'export-start', disabled: true }, icon('fa-file-export'), 'Exporteren');
        const body = [
            createEl('p', { class: 'export-summary', id: 'export-summary' }),
            field('Opslaan in', createEl('div', { class: 'export-row' }, baseInput, chooseBtn),
                'Buiten het archief: standaard in Afbeeldingen, of kies een map op de T7 of een andere schijf.', 'export-base'),
            field('Naam van de map', nameInput, null, 'export-name'),
            createEl('div', { class: 'export-field' },
                createEl('span', { class: 'export-label', text: 'Bestanden' }),
                createEl('div', { class: 'export-choices', role: 'radiogroup', 'aria-label': 'Bestanden' },
                    radio('export-mode', 'originals', 'Originelen (exact dezelfde bestanden)', true, (v) => { state.mode = v; edgeField.hidden = true; refresh(); }),
                    radio('export-mode', 'resized', 'Verkleind als JPEG, om te delen', false, (v) => { state.mode = v; edgeField.hidden = false; refresh(); }))),
            edgeField,
            createEl('div', { class: 'export-grid2' },
                field('Mappen', select('export-structure', [['flat', 'Alles in één map'], ['month', 'Per maand'], ['folders', 'Zoals in het archief']], 'flat', (v) => { state.structure = v; }), null, 'export-structure'),
                field('Overzicht', select('export-manifest', [['csv', 'Lijst als CSV'], ['json', 'Lijst als JSON'], ['none', 'Geen lijst']], 'csv', (v) => { state.manifest = v; }), 'Met de bronpaden van elk bestand.', 'export-manifest')),
            status,
        ];
        const el = modal('modal-export', 'Exporteren', 'fa-file-export', body, [
            createEl('button', { type: 'button', class: 'btn btn-secondary', onClick: () => closeModal('modal-export') }, 'Annuleren'), startBtn,
        ]);

        const destination = () => {
            const base = baseInput.value.trim().replace(/\/+$/, '');
            const name = nameInput.value.trim().replace(/[\/\0]/g, '-');
            return name ? `${base}/${name}` : base;
        };
        let seq = 0;
        let timer = null;
        async function refresh() {
            clearTimeout(timer);
            timer = setTimeout(async () => {
                const mine = ++seq;
                try {
                    const plan = await post('/api/export/plan', { paths, mode: state.mode, max_edge: state.edge, destination: destination() });
                    if (mine !== seq || !el.isConnected) return;
                    state.plan = plan;
                    const sizeText = state.mode === 'resized' ? `ongeveer ${formatBytes(plan.estimated_bytes)}` : formatBytes(plan.total_bytes);
                    $$('export-summary').textContent = `${plan.count.toLocaleString('nl-NL')} bestand${plan.count === 1 ? '' : 'en'}, ${sizeText}` +
                        (plan.skipped ? ` (${plan.skipped} niet meer beschikbaar, overgeslagen)` : '');
                    const ok = !plan.destination_error && plan.count > 0;
                    status.className = `export-status ${ok ? 'is-ok' : 'is-error'}`;
                    status.replaceChildren(icon(ok ? 'fa-circle-check' : 'fa-triangle-exclamation'),
                        createEl('span', { text: ok ? `Past: ${formatBytes(plan.free_bytes)} vrij op de bestemming. Er wordt niets overschreven.` : (plan.destination_error || 'Geen bestanden om te exporteren') }));
                    startBtn.disabled = !ok || state.busy;
                } catch (e) {
                    if (mine !== seq) return;
                    status.className = 'export-status is-error';
                    status.replaceChildren(icon('fa-triangle-exclamation'), createEl('span', { text: (e && e.message) || 'Controleren mislukt' }));
                    startBtn.disabled = true;
                }
            }, 200);
        }
        [baseInput, nameInput].forEach(i => i.addEventListener('input', refresh));
        startBtn.addEventListener('click', async () => {
            state.busy = true;
            startBtn.disabled = true;
            try {
                const job = await jobsStart('export', { paths, destination: destination(), mode: state.mode, max_edge: state.edge,
                    structure: state.structure, manifest: state.manifest });
                closeModal('modal-export');
                showToast('Export gestart', `${job.params.count} bestanden worden gekopieerd; de voortgang staat rechtsonder.`, 'success');
            } catch (e) {
                state.busy = false;
                status.className = 'export-status is-error';
                status.replaceChildren(icon('fa-triangle-exclamation'), createEl('span', { text: (e && e.message) || 'Starten mislukt' }));
            }
        });
        refresh();
        setTimeout(() => nameInput.focus(), 50);
    }

    // ── Beste van een periode ────────────────────────────

    async function openBest() {
        const now = new Date();
        const state = { kind: 'year', year: now.getFullYear() - (now.getMonth() < 2 ? 1 : 0), month: now.getMonth() + 1,
            from: '', to: '', unit: 'count', amount: 50, folder: '', album: '', items: [], busy: false };
        let albums = [];
        try { albums = (await fetchJSON(`${API_BASE}/api/library/albums`)).albums.filter(a => !a.hidden); } catch (e) { albums = []; }

        const yearInput = createEl('input', { type: 'number', class: 'input', id: 'best-year', value: state.year, min: '1900', max: '2200' });
        yearInput.addEventListener('input', () => { state.year = Number(yearInput.value); });
        const monthSelect = select('best-month', MONTHS.map((m, i) => [i + 1, m]), state.month, (v) => { state.month = Number(v); });
        const fromInput = createEl('input', { type: 'date', class: 'input', id: 'best-from', 'aria-label': 'Van' });
        const toInput = createEl('input', { type: 'date', class: 'input', id: 'best-to', 'aria-label': 'Tot en met' });
        fromInput.addEventListener('change', () => { state.from = fromInput.value; });
        toInput.addEventListener('change', () => { state.to = toInput.value; });
        const periodBox = createEl('div', { class: 'export-row', id: 'best-period' });
        const drawPeriod = () => {
            const parts = state.kind === 'year' ? [yearInput] : state.kind === 'month' ? [monthSelect, yearInput] : [fromInput, createEl('span', { text: 'tot en met' }), toInput];
            periodBox.replaceChildren(...parts);
        };
        const kindSelect = select('best-kind', [['year', 'Een jaar'], ['month', 'Een maand'], ['range', 'Een eigen periode']], state.kind, (v) => { state.kind = v; drawPeriod(); });
        drawPeriod();
        const amountInput = createEl('input', { type: 'number', class: 'input', id: 'best-amount', value: state.amount, min: '1', max: '5000' });
        amountInput.addEventListener('input', () => { state.amount = Number(amountInput.value); });
        const unitSelect = select('best-unit', [['count', "foto's"], ['percent', '% van de foto\'s']], state.unit, (v) => { state.unit = v; });
        const folderInput = createEl('input', { type: 'text', class: 'input mono', id: 'best-folder', placeholder: 'Hele archief', spellcheck: 'false', autocomplete: 'off' });
        folderInput.addEventListener('input', () => { state.folder = folderInput.value.trim(); });
        const albumSelect = select('best-album', [['', 'Geen album'], ...albums.map(a => [a.id, a.name])], '', (v) => { state.album = v; });
        const result = createEl('div', { class: 'best-result', id: 'best-result' });
        const exportBtn = createEl('button', { type: 'button', class: 'btn btn-primary', id: 'best-export', disabled: true },
            icon('fa-file-export'), 'Exporteren…');
        const previewBtn = createEl('button', { type: 'button', class: 'btn btn-secondary', id: 'best-preview' }, icon('fa-wand-magic-sparkles'), 'Voorbeeld maken');

        const range = () => {
            const pad = (n) => String(n).padStart(2, '0');
            if (state.kind === 'year') return { date_from: `${state.year}-01-01`, date_to: `${state.year}-12-31` };
            if (state.kind === 'month') {
                const last = new Date(state.year, state.month, 0).getDate();
                return { date_from: `${state.year}-${pad(state.month)}-01`, date_to: `${state.year}-${pad(state.month)}-${pad(last)}` };
            }
            return { date_from: state.from || null, date_to: state.to || null };
        };

        // motion: 'enter' (een nieuw voorbeeld: de foto's komen als een golf binnen) of 'flip'
        // (een foto weggelaten: hij gaat zichtbaar weg, de rest schuift aan)
        const drawResult = (data, motion) => {
            if (motion === 'flip') {
                const tiles = () => result.querySelectorAll('.best-tile');
                MediaMotion.flipByKey(tiles(), el => el.dataset.path, () => drawResult(data), { after: tiles });
                return;
            }
            if (!data) { result.replaceChildren(); return; }
            if (!state.items.length) {
                result.replaceChildren(emptyState({ icon: 'fa-image', title: "Geen foto's gevonden", text: 'Probeer een andere periode of een ander filter.' }));
                exportBtn.disabled = true;
                return;
            }
            const bytes = state.items.reduce((n, i) => n + (i.size || 0), 0);
            const tiles = state.items.map(item => {
                const tile = createEl('div', { class: 'best-tile thumb-tile', dataset: { path: item.path }, title: `${item.name}${item.reasons.length ? ' · ' + item.reasons.join(', ') : ''}` },
                    createEl('img', { src: vaultFileUrl('thumb', item.path), alt: item.name, loading: 'lazy' }),
                    createEl('button', { type: 'button', class: 'best-remove', 'aria-label': `${item.name} weglaten`, title: 'Weglaten',
                        onClick: () => { state.items = state.items.filter(i => i.path !== item.path); drawResult(data, 'flip'); } }, icon('fa-xmark')));
                return tile;
            });
            result.replaceChildren(
                createEl('p', { class: 'export-summary', id: 'best-summary' },
                    createEl('strong', { text: `${state.items.length.toLocaleString('nl-NL')} van ${data.pool.toLocaleString('nl-NL')} foto's, ${formatBytes(bytes)}.` }),
                    ' Hoogstens één per reeks of burst, verspreid over de periode. Haal weg wat je niet wilt.'),
                createEl('div', { class: 'best-grid' }, tiles));
            exportBtn.disabled = false;
        };

        previewBtn.addEventListener('click', async () => {
            previewBtn.disabled = true;
            exportBtn.disabled = true;
            result.replaceChildren(createEl('div', { class: 'export-status', role: 'status' }, 'Beste foto\'s kiezen…'));
            try {
                const body = { ...range(), folder: state.folder || null, album_id: state.album ? Number(state.album) : null };
                body[state.unit === 'percent' ? 'percent' : 'count'] = state.amount;
                const data = await post('/api/export/best', body);
                state.items = data.items;
                drawResult(data);
                MediaMotion.cascade(result.querySelectorAll('.best-tile'), { revealRest: true, root: result.closest('.modal-body') });
            } catch (e) {
                result.replaceChildren(errorState('Kiezen mislukt', e, null));
            } finally { previewBtn.disabled = false; }
        });
        exportBtn.addEventListener('click', () => {
            if (!state.items.length) return;
            closeModal('modal-best');
            openExport(state.items);
        });

        modal('modal-best', 'Beste van een periode', 'fa-trophy', [
            createEl('p', { class: 'export-hint', text: "Kiest de beste foto's op waardering in Shows, gehouden in de Keurer en kwaliteit, zonder twintig keer dezelfde burst." }),
            createEl('div', { class: 'export-grid2' }, field('Periode', kindSelect, null, 'best-kind'), field('Wanneer', periodBox, null, 'best-year')),
            createEl('div', { class: 'export-grid2' },
                field('Hoeveel', createEl('div', { class: 'export-row' }, amountInput, unitSelect), null, 'best-amount'),
                field('Album (optioneel)', albumSelect, null, 'best-album')),
            field('Map (optioneel)', folderInput, 'Een map in het archief, bijvoorbeeld RAW/2019.', 'best-folder'),
            createEl('div', { class: 'export-row' }, previewBtn),
            result,
        ], [createEl('button', { type: 'button', class: 'btn btn-secondary', onClick: () => closeModal('modal-best') }, 'Sluiten'), exportBtn], true);
        setTimeout(() => previewBtn.focus(), 50);
    }

    // ── Klaar: de map in Finder tonen ────────────────────

    window.exportRevealFolder = async function (path) {
        try { await post('/api/export/reveal', { path }); } catch (e) { toastFetchError('Openen in Finder mislukt', e); }
    };

    document.addEventListener('DOMContentLoaded', () => {
        if (typeof jobsOnFinished !== 'function') return;
        jobsOnFinished((job) => {
            if (job.type !== 'export') return;
            const s = job.summary || {};
            if (job.status === 'done' && s.destination) {
                showToast('Export klaar', `${s.copied} bestanden (${formatBytes(s.bytes)})${s.errors ? `, ${s.errors} mislukt` : ''}.`,
                    s.errors ? 'warning' : 'success', { label: 'Toon in Finder', onClick: () => exportRevealFolder(s.destination) });
            } else if (job.status === 'failed') {
                showToast('Export mislukt', job.error || 'Er ging iets mis; wat al gekopieerd is, blijft staan.', 'error');
            }
        });
    });

    if (typeof addShortcutGroup === 'function') {
        addShortcutGroup('Selecteren, verplaatsen en exporteren', [
            [['spatie'], 'Foto selecteren of deselecteren (Albums en Tijdlijn)'],
            [['⇧ klik'], 'Alles tussen twee foto\'s selecteren'],
            [['⌘ klik'], 'Eén foto toevoegen aan de selectie'],
            [['⌫'], 'Selectie naar de prullenmand'],
            [['Esc'], 'Selectie wissen'],
        ]);
    }
})();
