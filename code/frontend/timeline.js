// ==================== TIMELINE MODULE ====================
// Photo timeline browser with time scrubber, folder selection,
// virtualized rendering, and chronological grouping.

(function () {
    'use strict';

    // ── State ──────────────────────────────────────────────
    let tlFiles = [];                   // all scanned files sorted by timestamp
    let tlDateIndex = {};               // year → { month → count }
    let tlYearBoundaries = {};          // year → first index in tlFiles
    let tlSelectedFolders = new Set();  // selected folder paths
    let tlFolderTree = [];              // full folder tree from API
    let tlLoading = false;
    let tlScanning = false;
    let tlInitialized = false;
    let tlFoldersFailed = false;        // last folder list or scan failed (e.g. the disk was gone):
    let tlScanFailed = false;           // done again on return to the tab or when the disk is back

    // Paging: the first page shows at once, the rest loads behind it (oldest first, so it
    // only ever grows at the bottom). tlTotal is the whole selection, also before it is loaded.
    const TL_FIRST_PAGE = 3000;
    const TL_PAGE = 20000;
    let tlTotal = 0;
    let tlScanSeq = 0;                  // a newer scan stops the pages of an older one
    let tlPagesLoading = false;
    let tlPendingYear = null;           // a year asked for before its photos were loaded

    // Virtualization
    const ITEM_HEIGHT = 220;            // photo card height (px) until the first layout
    const COLS = 5;                     // cards per row
    const GAP = 12;                     // between the cards, in a row and between rows
    let tlTile = ITEM_HEIGHT;           // square tiles: as high as a column is wide
    let tlRowHeight = ITEM_HEIGHT + GAP; // row height + gap
    const HEADER_HEIGHT = 48;           // date group header height
    const PRELOAD_ROWS = 4;            // extra rows to render above/below

    let tlGroups = [];                  // [{label, date, startIdx, endIdx, files}]
    let tlLayoutRows = [];              // [{type:'header'|'row', top, height, groupIdx, rowFiles?}]
    let tlTotalHeight = 0;
    let tlRenderedRange = { start: -1, end: -1 };
    let tlScrollContainer = null;
    let tlContentEl = null;
    let tlLayoutCols = 0;               // columns the current layout was built with

    let tlSliderDragging = false;
    let tlScrollTicking = false;
    let tlFocusIndex = 0;               // the card the keyboard is on (Tab lands here)

    // ── Persistence ───────────────────────────────────────
    function loadSavedFolders() {
        try {
            const saved = localStorage.getItem('timeline_folders');
            if (saved) {
                const arr = JSON.parse(saved);
                tlSelectedFolders = new Set(arr);
            }
        } catch (e) { /* ignore */ }
    }

    function saveFolders() {
        localStorage.setItem('timeline_folders', JSON.stringify([...tlSelectedFolders]));
    }

    // ── Init ──────────────────────────────────────────────
    window.timelineInit = async function () {
        loadSavedFolders();

        const container = document.getElementById('timeline-scroll');
        if (!container) return;

        // Prevent re-initialization when switching back to timeline tab
        if (tlInitialized) {
            // What failed last time (the disk was gone) is tried again
            if (tlFoldersFailed || tlScanFailed) {
                await timelineVaultReturned();
                return;
            }
            // Window resized while the tab was hidden: rebuild with the real width
            if (tlFiles.length > 0 && layoutStale()) recomputeLayout();
            // Just re-render in case of resize
            if (tlFiles.length > 0) {
                tlRenderedRange = { start: -1, end: -1 };
                renderVisibleRows();
            }
            return;
        }
        tlInitialized = true;

        tlScrollContainer = container;
        tlContentEl = document.getElementById('timeline-virtual-content');

        // Attach scroll listener
        container.addEventListener('scroll', onTimelineScroll, { passive: true });
        container.addEventListener('keydown', onTimelineKeydown);

        // Recompute cols on resize
        // (not while the tab is hidden: its width is 0 then, which would mean 2 columns)
        window.addEventListener('resize', debounce(() => {
            if (tlScrollContainer && tlScrollContainer.clientWidth > 0 && tlFiles.length > 0) recomputeLayout();
        }, 200));

        // Load folder tree for picker
        await loadTimelineFolders();

        // If we have saved folders, auto-scan
        if (tlSelectedFolders.size > 0) {
            updateFolderChips();
            await scanTimeline();
        } else {
            renderEmptyState();
        }
    };

    // ── Folder Loading ────────────────────────────────────
    async function loadTimelineFolders() {
        try {
            const data = await fetchJSON(`${API_BASE}/api/timeline/folders`);
            tlFolderTree = data.folders || [];
            tlFoldersFailed = false;
        } catch (e) {
            toastFetchError('Tijdlijn: mappen laden mislukt', e);
            tlFolderTree = [];
            tlFoldersFailed = true;
        }
    }

    // The disk is back (app.js reloadAfterVaultReturn) or the tab is opened again:
    // load again what failed, without the user picking the folders again
    window.timelineVaultReturned = async function () {
        if (!tlInitialized) return;
        if (tlFoldersFailed) await loadTimelineFolders();
        if (tlScanFailed && tlSelectedFolders.size > 0) {
            updateFolderChips();
            await scanTimeline();
        }
    };

    // ── Folder Picker Modal ───────────────────────────────
    window.timelineOpenFolderPicker = function () {
        const modal = document.getElementById('modal-timeline-folders');
        const tree = document.getElementById('timeline-folder-tree');
        if (!modal || !tree) return;

        tree.innerHTML = '';
        if (tlFolderTree.length === 0) {
            tree.appendChild(emptyState({ icon: 'fa-folder-open', title: 'Geen mappen gevonden',
                text: tlFoldersFailed ? 'De mappen konden niet geladen worden. Is de schijf aangesloten?' : 'Het archief heeft nog geen mappen met foto\'s.' }));
        } else {
            renderPickerTree(tree, tlFolderTree, 0);
        }
        modal.classList.add('active');
        // De eerste map krijgt de focus: pijltjes en spatie werken meteen
        const first = tree.querySelector('.tl-picker-row');
        if (first) setTimeout(() => first.focus(), 0);
    };

    window.timelineCloseFolderPicker = function () {
        const modal = document.getElementById('modal-timeline-folders');
        if (modal) modal.classList.remove('active');
    };

    window.timelineConfirmFolders = async function () {
        timelineCloseFolderPicker();
        saveFolders();
        updateFolderChips();

        if (tlSelectedFolders.size > 0) {
            await scanTimeline();
        } else {
            tlScanSeq++;   // pages still on their way are for the old selection
            tlFiles = [];
            tlTotal = 0;
            renderEmptyState();
        }
    };

    function renderPickerTree(container, folders, depth) {
        folders.forEach(folder => {
            const row = document.createElement('div');
            row.className = 'tl-picker-row';
            row.style.paddingLeft = `${depth * 20 + 12}px`;

            const isSelected = tlSelectedFolders.has(folder.path);

            const checkbox = document.createElement('div');
            checkbox.className = 'tl-picker-checkbox' + (isSelected ? ' checked' : '');
            checkbox.innerHTML = isSelected ? '<i class="fas fa-check"></i>' : '';

            const icon = document.createElement('span');
            icon.className = 'tl-picker-icon';
            setHTML(icon, html`<i class="fas fa-folder" aria-hidden="true"></i>`);

            const name = document.createElement('span');
            name.className = 'tl-picker-name';
            name.textContent = folder.name;

            const count = document.createElement('span');
            count.className = 'tl-picker-count';
            count.textContent = folder.media_count > 0 ? `${folder.media_count}` : '';

            row.appendChild(checkbox);
            row.appendChild(icon);
            row.appendChild(name);
            row.appendChild(count);

            // Een aanvinkvakje voor het toetsenbord en schermlezers: Tab erheen, spatie of Enter
            row.tabIndex = 0;
            row.setAttribute('role', 'checkbox');
            row.setAttribute('aria-checked', String(isSelected));
            row.setAttribute('aria-label', folder.name);
            row.onclick = () => {
                if (tlSelectedFolders.has(folder.path)) {
                    tlSelectedFolders.delete(folder.path);
                    checkbox.classList.remove('checked');
                    checkbox.innerHTML = '';
                } else {
                    tlSelectedFolders.add(folder.path);
                    checkbox.classList.add('checked');
                    checkbox.innerHTML = '<i class="fas fa-check"></i>';
                }
                row.setAttribute('aria-checked', String(tlSelectedFolders.has(folder.path)));
            };
            row.onkeydown = (e) => {
                if (e.key === ' ' || e.key === 'Enter') {
                    e.preventDefault();
                    row.click();
                } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                    e.preventDefault();
                    const rows = [...container.querySelectorAll('.tl-picker-row')];
                    const next = rows[rows.indexOf(row) + (e.key === 'ArrowDown' ? 1 : -1)];
                    if (next) next.focus();
                }
            };

            container.appendChild(row);

            // Render children recursively
            if (folder.children && folder.children.length > 0) {
                renderPickerTree(container, folder.children, depth + 1);
            }
        });
    }

    // ── Folder Chips ──────────────────────────────────────
    function updateFolderChips() {
        const container = document.getElementById('timeline-folder-chips');
        if (!container) return;

        container.innerHTML = '';

        if (tlSelectedFolders.size === 0) {
            container.innerHTML = '<span class="tl-no-folders">Geen mappen gekozen</span>';
            return;
        }

        tlSelectedFolders.forEach(path => {
            const name = path.split('/').pop();
            const chip = createEl('div', { class: 'tl-folder-chip' },
                createEl('span', { class: 'tl-chip-name', text: name }),
                createEl('button', {
                    class: 'tl-chip-remove',
                    'aria-label': `${name} uit de tijdlijn halen`,
                    title: 'Uit de tijdlijn halen',
                    onClick: (e) => { e.stopPropagation(); timelineRemoveFolder(path); },
                }, '×'));
            container.appendChild(chip);
        });
    }

    window.timelineRemoveFolder = async function (path) {
        tlSelectedFolders.delete(path);
        saveFolders();
        updateFolderChips();

        if (tlSelectedFolders.size > 0) {
            await scanTimeline();
        } else {
            tlScanSeq++;   // pages still on their way are for the old selection
            tlFiles = [];
            tlTotal = 0;
            renderEmptyState();
        }
    };

    // ── Scanning ──────────────────────────────────────────
    function timelineScanUrl(offset, limit) {
        // Eén parameter per map: een mapnaam mag een komma bevatten
        const params = new URLSearchParams();
        [...tlSelectedFolders].forEach(folder => params.append('folders', folder));
        params.set('offset', String(offset));
        params.set('limit', String(limit));
        return `${API_BASE}/api/timeline/scan?${params}`;
    }

    async function scanTimeline() {
        if (tlScanning) return;
        tlScanning = true;
        const seq = ++tlScanSeq;
        tlPendingYear = null;
        tlFocusIndex = 0;

        const scrollEl = tlScrollContainer;
        const contentEl = tlContentEl;
        const sliderWrap = document.getElementById('timeline-slider-wrap');

        // Show loading
        if (contentEl) {
            contentEl.style.height = 'auto';
            contentEl.replaceChildren(skeletonCards(15, 'tl-skeleton'));
        }

        try {
            const data = await fetchJSON(timelineScanUrl(0, TL_FIRST_PAGE));
            tlFiles = timelineFilesFrom(data);
            tlTotal = data.total || tlFiles.length;
            tlScanFailed = false;
            if (data.skipped && data.skipped.length) {
                showToast('Tijdlijn', `${data.skipped.length} gekozen map(pen) niet gevonden en overgeslagen`, 'warning');
            }
            if (data.complete === false) {
                showToast('Tijdlijn', `${data.errors || 'Sommige'} map(pen) of bestand(en) konden niet gelezen worden; de tijdlijn is mogelijk onvolledig`, 'warning');
            }
            tlDateIndex = data.date_index || {};
            tlYearBoundaries = data.year_boundaries || {};

            if (tlFiles.length === 0) {
                renderEmptyState('Geen foto\'s of video\'s in de gekozen mappen');
                tlScanning = false;
                return;
            }

            // Build groups
            buildGroups();

            // Build layout
            recomputeLayout();

            // Setup slider
            setupSlider();

            // Show info bar
            renderInfoBar();

            // Show slider
            if (sliderWrap) sliderWrap.style.display = 'flex';

            // Scroll to top and render
            if (scrollEl) scrollEl.scrollTop = 0;
            tlRenderedRange = { start: -1, end: -1 };
            renderVisibleRows();
            // De eerste foto's komen als een golf binnen (alleen wat in beeld staat)
            if (tlContentEl) MediaMotion.cascade(tlContentEl.querySelectorAll('.tl-photo-card'), { root: scrollEl });

            if (data.next_offset !== null && data.next_offset !== undefined) {
                // Not awaited: the timeline can be used while the rest arrives
                loadRemainingPages(seq, data.next_offset, {
                    version: data.version, source: data.source, total: data.total, date_index: data.date_index,
                });
            }
        } catch (e) {
            console.error('Timeline scan error:', e);
            tlScanFailed = true;
            renderEmptyState(`Tijdlijn laden mislukt: ${e.message}`);
        }

        tlScanning = false;
    }

    // Zelfde selectie, alleen uit een andere bron (de schijf of de bibliotheek): zelfde
    // aantal en zelfde verdeling over de maanden, dan zijn het dezelfde rijen
    function sameSelection(page, first) {
        return page.source !== first.source && page.total === first.total &&
            JSON.stringify(page.date_index || {}) === JSON.stringify(first.date_index || {});
    }

    // De rest van de tijdlijn, een pagina tegelijk, achter de eerste pagina aan
    async function loadRemainingPages(seq, offset, first) {
        let version = first.version;
        let source = first.source;
        tlPagesLoading = true;
        renderInfoBar();
        try {
            while (offset !== null && offset !== undefined && seq === tlScanSeq) {
                const page = await fetchJSON(timelineScanUrl(offset, TL_PAGE));
                if (seq !== tlScanSeq) return;
                if (page.version !== version && sameSelection(page, { ...first, source })) {
                    // Bijv. de bibliotheek is net bijgewerkt: verder met de nieuwe bron
                    version = page.version;
                    source = page.source;
                } else if (page.version !== version) {
                    // De selectie is intussen veranderd (bijv. bijgewerkt): opnieuw vanaf het begin
                    tlPagesLoading = false;
                    scanTimeline();
                    return;
                }
                tlFiles = tlFiles.concat(timelineFilesFrom(page));
                offset = page.next_offset;
                buildGroups();
                recomputeLayout();   // groeit alleen onderaan: de scrollpositie blijft staan
                updateSliderFromScroll();
                if (tlPendingYear !== null && tlYearBoundaries[tlPendingYear] < tlFiles.length) {
                    const year = tlPendingYear;
                    tlPendingYear = null;
                    jumpToYear(year);
                }
                renderInfoBar();
            }
        } catch (e) {
            if (seq === tlScanSeq) {
                toastFetchError('Tijdlijn: verder laden mislukt', e);
                tlScanFailed = true;   // bij terugkomen of als de schijf terug is: opnieuw
            }
        } finally {
            if (seq === tlScanSeq) {
                tlPagesLoading = false;
                renderInfoBar();
            }
        }
    }

    // De server stuurt per bestand een korte rij [tijd, pad t.o.v. het archief, grootte,
    // 'p'|'v', datumbron]; hier worden het weer bestandsobjecten voor de viewer
    const TL_DATE_SOURCES = { e: 'exif', q: 'video', b: 'birthtime', m: 'mtime' };

    function timelineFilesFrom(data) {
        const root = String(data.root || '').replace(/\/+$/, '');
        const columns = data.columns || [];
        const at = name => columns.indexOf(name);
        const [iTs, iPath, iSize, iType, iSource] = ['timestamp', 'path', 'size', 'type', 'date_source'].map(at);
        return (data.rows || []).map(row => {
            const rel = row[iPath];
            return {
                path: `${root}/${rel}`,
                name: rel.slice(rel.lastIndexOf('/') + 1),
                timestamp: row[iTs],
                size: row[iSize],
                type: row[iType] === 'v' ? 'video' : 'photo',
                date_source: TL_DATE_SOURCES[row[iSource]] || null,
            };
        });
    }

    // Aantal foto's en jaarbereik; ook na een verplaatsing opnieuw getekend
    function renderInfoBar() {
        const infoBar = document.getElementById('timeline-info-bar');
        if (!infoBar) return;
        const years = Object.keys(tlDateIndex).sort();
        const range = years.length > 1
            ? `${years[0]} – ${years[years.length - 1]}`
            : years[0] || '';
        const loading = tlPagesLoading && tlFiles.length < tlTotal
            ? html`<span class="tl-info-loading" role="status">${tlFiles.length.toLocaleString('nl-NL')} van ${tlTotal.toLocaleString('nl-NL')} geladen…</span>`
            : html``;
        infoBar.innerHTML = html`
            <span class="tl-info-count"><i class="fas fa-images" aria-hidden="true"></i> ${tlTotal.toLocaleString('nl-NL')} foto's en video's</span>
            <span class="tl-info-range"><i class="fas fa-calendar-alt"></i> ${range}</span>
            ${loading}
        `;
        infoBar.style.display = 'flex';
    }

    // ── Grouping ──────────────────────────────────────────
    function buildGroups() {
        tlGroups = [];
        if (tlFiles.length === 0) return;

        let currentLabel = '';
        let currentGroup = null;

        for (let i = 0; i < tlFiles.length; i++) {
            const dt = new Date(tlFiles[i].timestamp * 1000);
            const year = dt.getFullYear();
            const month = dt.getMonth();
            const day = dt.getDate();

            const monthNames = ['januari', 'februari', 'maart', 'april', 'mei', 'juni',
                'juli', 'augustus', 'september', 'oktober', 'november', 'december'];
            const label = `${monthNames[month]} ${year}`;

            if (label !== currentLabel) {
                if (currentGroup) {
                    currentGroup.endIdx = i - 1;
                    tlGroups.push(currentGroup);
                }
                currentGroup = {
                    label: label,
                    date: new Date(year, month, 1),
                    startIdx: i,
                    endIdx: i
                };
                currentLabel = label;
            }
        }

        if (currentGroup) {
            currentGroup.endIdx = tlFiles.length - 1;
            tlGroups.push(currentGroup);
        }
    }

    // ── Layout Computation ────────────────────────────────
    // Page margin left and right of the rows (--page-gutter, 20 px on narrow windows)
    function gutter() {
        const value = tlContentEl && parseFloat(getComputedStyle(tlContentEl).getPropertyValue('--page-gutter'));
        return value > 0 ? value : 32;
    }

    // The content is at most --page-max wide (centred, like every page); the rows sit inside it
    function rowWidth() {
        return (tlContentEl || tlScrollContainer).clientWidth - 2 * gutter();
    }

    function getColumns() {
        if (!tlScrollContainer) return COLS;
        const w = rowWidth();
        if (w < 500) return 2;
        if (w < 750) return 3;
        if (w < 1050) return 4;
        if (w < 1400) return 5;
        return 6;
    }

    // Built for another width (columns or tile size)?
    function layoutStale() {
        const cols = getColumns();
        return cols !== tlLayoutCols
            || Math.max(80, Math.floor((rowWidth() - GAP * (cols - 1)) / cols)) !== tlTile;
    }

    function recomputeLayout() {
        const cols = getColumns();
        tlLayoutCols = cols;
        // Square tiles, the same shape as in Bestanden, at every window width
        tlTile = Math.max(80, Math.floor((rowWidth() - GAP * (cols - 1)) / cols));
        tlRowHeight = tlTile + GAP;
        tlLayoutRows = [];
        let top = 0;

        for (let gi = 0; gi < tlGroups.length; gi++) {
            const group = tlGroups[gi];

            // Header row
            tlLayoutRows.push({
                type: 'header',
                top: top,
                height: HEADER_HEIGHT,
                groupIdx: gi,
                label: group.label
            });
            top += HEADER_HEIGHT;

            // Photo rows
            const count = group.endIdx - group.startIdx + 1;
            const numRows = Math.ceil(count / cols);

            for (let r = 0; r < numRows; r++) {
                const startFile = group.startIdx + r * cols;
                const endFile = Math.min(startFile + cols - 1, group.endIdx);
                const rowFiles = [];
                for (let fi = startFile; fi <= endFile; fi++) {
                    rowFiles.push(fi);
                }

                tlLayoutRows.push({
                    type: 'row',
                    top: top,
                    height: tlRowHeight,
                    groupIdx: gi,
                    rowFiles: rowFiles
                });
                top += tlRowHeight;
            }

            // Group spacing
            top += 16;
        }

        // Ruimte onderaan, zoals elke pagina (--space-4xl): het takenpaneel bedekt de laatste rij niet
        tlTotalHeight = top + 64;

        if (tlContentEl) {
            tlContentEl.style.height = `${tlTotalHeight}px`;
            tlContentEl.innerHTML = ''; // Clear for re-render
        }

        tlRenderedRange = { start: -1, end: -1 };
        renderVisibleRows();
    }

    // ── Virtualized Rendering ─────────────────────────────
    function renderVisibleRows() {
        if (!tlScrollContainer || !tlContentEl || tlLayoutRows.length === 0) return;

        const scrollTop = tlScrollContainer.scrollTop;
        const viewHeight = tlScrollContainer.clientHeight;
        const preloadPx = PRELOAD_ROWS * tlRowHeight;

        const viewStart = scrollTop - preloadPx;
        const viewEnd = scrollTop + viewHeight + preloadPx;

        // Binary search for first row traversing into view
        let left = 0;
        let right = tlLayoutRows.length - 1;
        let startIdx = 0;

        while (left <= right) {
            const mid = Math.floor((left + right) / 2);
            if (tlLayoutRows[mid].top + tlLayoutRows[mid].height < viewStart) {
                left = mid + 1;
            } else {
                startIdx = mid;
                right = mid - 1;
            }
        }

        let endIdx = startIdx;
        while (endIdx < tlLayoutRows.length && tlLayoutRows[endIdx].top < viewEnd) {
            endIdx++;
        }
        endIdx = Math.min(endIdx, tlLayoutRows.length - 1);

        // Skip if range hasn't changed
        if (startIdx === tlRenderedRange.start && endIdx === tlRenderedRange.end) return;
        tlRenderedRange = { start: startIdx, end: endIdx };

        // Smart DOM Update (Dom Recycling)
        const existingNodes = Array.from(tlContentEl.children);
        const existingIndices = new Set();
        
        // Remove nodes that are out of bounds
        existingNodes.forEach(node => {
            const idx = parseInt(node.getAttribute('data-row-idx'), 10);
            if (isNaN(idx) || idx < startIdx || idx > endIdx) {
                tlContentEl.removeChild(node);
            } else {
                existingIndices.add(idx);
            }
        });

        // Add nodes that are missing
        const fragment = document.createDocumentFragment();
        for (let i = startIdx; i <= endIdx; i++) {
            if (!existingIndices.has(i)) {
                fragment.appendChild(renderRowNode(i));
            }
        }
        
        if (fragment.childElementCount > 0) {
            tlContentEl.appendChild(fragment);
        }
        // Eén kaart in beeld is met Tab bereikbaar: die van het toetsenbord, anders de eerste
        if (!tlContentEl.querySelector('.tl-photo-card[tabindex="0"]')) {
            const first = tlContentEl.querySelector('.tl-photo-card');
            if (first) first.tabIndex = 0;
        }
    }

    function renderRowNode(rowIndex) {
        const layoutRow = tlLayoutRows[rowIndex];

        if (layoutRow.type === 'header') {
            const header = document.createElement('div');
            header.className = 'tl-group-header';
            header.setAttribute('data-row-idx', rowIndex);
            header.style.position = 'absolute';
            header.style.top = `${layoutRow.top}px`;
            header.style.left = '0';
            header.style.right = '0';
            header.style.height = `${layoutRow.height}px`;
            header.textContent = layoutRow.label;
            return header;
        } else {
            const row = document.createElement('div');
            row.className = 'tl-photo-row';
            row.setAttribute('data-row-idx', rowIndex);
            row.style.position = 'absolute';
            row.style.top = `${layoutRow.top}px`;
            row.style.left = `${gutter()}px`;
            row.style.right = `${gutter()}px`;
            row.style.height = `${tlTile}px`;
            row.style.display = 'grid';
            row.style.gridTemplateColumns = `repeat(${tlLayoutCols}, 1fr)`;
            row.style.gap = `${GAP}px`;

            layoutRow.rowFiles.forEach(fileIdx => {
                const file = tlFiles[fileIdx];
                const card = createPhotoCard(file, fileIdx);
                row.appendChild(card);
            });

            return row;
        }
    }

    function createPhotoCard(file, index) {
        const card = document.createElement('div');
        card.className = 'tl-photo-card thumb-tile';
        card.setAttribute('data-index', index);
        card.dataset.motionKey = file.path;   // de viewer groeit hieruit en krimpt hierheen terug
        card.setAttribute('role', 'button');
        card.tabIndex = index === tlFocusIndex ? 0 : -1;

        // Lazy image
        const img = document.createElement('img');
        img.className = 'tl-photo-img loading';
        img.alt = file.name;
        img.loading = 'lazy';

        let thumbUrl = `${API_BASE}/api/thumb?path=${encodeURIComponent(file.path)}`;

        if (file.type === 'video') {
            // Video badge
            const badge = document.createElement('div');
            badge.className = 'tl-video-badge';
            badge.innerHTML = '<i class="fas fa-play"></i>';
            card.appendChild(badge);
        }

        img.addEventListener('load', () => img.classList.remove('loading'));
        img.addEventListener('error', () => {
            img.classList.remove('loading');
            img.style.backgroundColor = 'var(--surface-raised)';
        });

        img.src = thumbUrl;
        card.appendChild(img);

        // Date label
        const dt = new Date(file.timestamp * 1000);
        const dateStr = `${dt.getDate()} ${['jan', 'feb', 'mrt', 'apr', 'mei', 'jun',
            'jul', 'aug', 'sep', 'okt', 'nov', 'dec'][dt.getMonth()]} ${dt.getFullYear()}`;
        const timeStr = `${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}`;

        const dateLabel = document.createElement('div');
        dateLabel.className = 'tl-photo-date';
        dateLabel.textContent = dateStr;
        card.appendChild(dateLabel);
        card.setAttribute('aria-label', `${file.name}, ${dateStr} ${timeStr}${file.type === 'video' ? ', video' : ''}`);

        // Click handler - open in viewer modal
        if (window.DVExport && DVExport.has(file.path)) card.classList.add('selected');
        card.onclick = (e) => {
            if (window.DVExport && DVExport.wantsSelect(e)) return selectTimelineFile(file, index, e.shiftKey);
            openTimelinePhoto(file, index);
        };

        return card;
    }

    // Selecteren voor export: klik met shift of cmd, of spatie
    let tlAnchor = null;
    function selectTimelineFile(file, index, range) {
        if (range && tlAnchor !== null) {
            DVExport.selectRange(tlFiles.slice(Math.min(tlAnchor, index), Math.max(tlAnchor, index) + 1));
        } else {
            DVExport.toggle(file);
        }
        tlAnchor = index;
    }
    if (window.DVExport) {
        DVExport.onChange(() => {
            if (!tlContentEl) return;
            tlContentEl.querySelectorAll('.tl-photo-card').forEach(card => {
                const f = tlFiles[Number(card.getAttribute('data-index'))];
                if (f) card.classList.toggle('selected', DVExport.has(f.path));
            });
        });
    }

    // De gedeelde viewer van app.js, met de tijdlijn als eigen lijst: bladeren gaat door de
    // tijdlijn, en Open in Finder en Verplaatsen werken op de foto die te zien is
    function openTimelinePhoto(file, index) {
        openViewer(tlFiles, index, { onRemoved: removeTimelineFile });
    }

    // Een verplaatst bestand uit de tijdlijn halen en de indeling opnieuw opbouwen
    function removeTimelineFile(file) {
        const at = tlFiles.findIndex(f => f.path === file.path);
        if (at < 0) return;
        tlFiles.splice(at, 1);
        const dt = new Date(file.timestamp * 1000);
        const year = String(dt.getFullYear());
        const month = String(dt.getMonth() + 1);
        if (tlDateIndex[year] && tlDateIndex[year][month]) {
            tlDateIndex[year][month]--;
            // Een lege maand of een leeg jaar telt niet meer mee in het bereik
            if (tlDateIndex[year][month] <= 0) delete tlDateIndex[year][month];
            if (Object.keys(tlDateIndex[year]).length === 0) delete tlDateIndex[year];
        }
        // Jaren na dit bestand beginnen één plek eerder; een leeg jaar verdwijnt (ook als nog
        // niet alles geladen is, dus niet opnieuw tellen uit tlFiles)
        tlTotal = Math.max(0, tlTotal - 1);
        Object.keys(tlYearBoundaries).forEach(y => {
            if (!tlDateIndex[y]) delete tlYearBoundaries[y];
            else if (tlYearBoundaries[y] > at) tlYearBoundaries[y]--;
        });
        if (tlFiles.length === 0 && !tlPagesLoading) {
            renderEmptyState('Geen foto\'s of video\'s in de gekozen mappen');
            return;
        }
        buildGroups();
        recomputeLayout();
        renderInfoBar();
        // Jaarlabels opnieuw opbouwen (een jaar zonder foto's verdwijnt) en de schuif bij de scrollpositie houden
        setupSlider();
        updateSliderFromScroll();
    }

    // ── Keyboard ──────────────────────────────────────────
    // Arrows move between cards (up and down keep the column, across month headers),
    // Home/End and PageUp/PageDown jump, Enter or space opens the viewer. The list is
    // virtualized, so the target row is scrolled into view and drawn before it gets focus.

    function layoutRowOf(index) {
        let lo = 0, hi = tlLayoutRows.length - 1, found = -1;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            const row = tlLayoutRows[mid];
            const first = row.type === 'row' ? row.rowFiles[0] : (tlGroups[row.groupIdx] || {}).startIdx;
            if (first <= index) { found = mid; lo = mid + 1; } else hi = mid - 1;
        }
        // A header shares its first index with the row below it
        while (found >= 0 && found < tlLayoutRows.length && tlLayoutRows[found].type !== 'row') found++;
        return found;
    }

    function photoRowFrom(ri, step) {
        for (let i = ri + step; i >= 0 && i < tlLayoutRows.length; i += step) {
            if (tlLayoutRows[i].type === 'row') return i;
        }
        return -1;
    }

    function focusTimelineCard(index) {
        if (!tlFiles.length || !tlScrollContainer) return;
        index = Math.max(0, Math.min(tlFiles.length - 1, index));
        const ri = layoutRowOf(index);
        if (ri < 0) return;
        const row = tlLayoutRows[ri];
        const before = tlLayoutRows[ri - 1];
        const top = before && before.type === 'header' ? before.top : row.top;
        const view = tlScrollContainer.clientHeight;
        const scroll = tlScrollContainer.scrollTop;
        if (top < scroll) tlScrollContainer.scrollTop = top;
        else if (row.top + row.height > scroll + view) tlScrollContainer.scrollTop = row.top + row.height - view;
        renderVisibleRows();
        updateSliderFromScroll();
        updateCurrentDateLabel();
        const old = tlContentEl.querySelector('.tl-photo-card[tabindex="0"]');
        if (old) old.tabIndex = -1;
        tlFocusIndex = index;
        const card = tlContentEl.querySelector(`.tl-photo-card[data-index="${index}"]`);
        if (card) {
            card.tabIndex = 0;
            card.focus({ preventScroll: true });
        }
    }

    function onTimelineKeydown(e) {
        const card = e.target.closest && e.target.closest('.tl-photo-card');
        if (!card || e.altKey || e.ctrlKey || e.metaKey) return;
        const index = Number(card.getAttribute('data-index'));
        const ri = layoutRowOf(index);
        const row = tlLayoutRows[ri];
        const col = row ? index - row.rowFiles[0] : 0;
        const rowsPerPage = Math.max(1, Math.floor(tlScrollContainer.clientHeight / tlRowHeight) - 1);
        const inRow = (target) => {
            if (target < 0) return index;
            const r = tlLayoutRows[target];
            return Math.min(r.rowFiles[0] + col, r.rowFiles[r.rowFiles.length - 1]);
        };
        const rowsAway = (n) => {
            let target = ri;
            for (let i = 0; i < Math.abs(n); i++) {
                const next = photoRowFrom(target, Math.sign(n));
                if (next < 0) break;
                target = next;
            }
            return inRow(target);
        };
        let next = null;
        switch (e.key) {
            case 'ArrowRight': next = index + 1; break;
            case 'ArrowLeft': next = index - 1; break;
            case 'ArrowDown': next = inRow(photoRowFrom(ri, 1)); break;
            case 'ArrowUp': next = inRow(photoRowFrom(ri, -1)); break;
            case 'PageDown': next = rowsAway(rowsPerPage); break;
            case 'PageUp': next = rowsAway(-rowsPerPage); break;
            case 'Home': next = 0; break;
            case 'End': next = tlFiles.length - 1; break;
            case ' ':
                e.preventDefault();
                if (window.DVExport && tlFiles[index]) selectTimelineFile(tlFiles[index], index, e.shiftKey);
                return;
            case 'Enter':
                e.preventDefault();
                card.click();
                return;
            default:
                return;
        }
        e.preventDefault();
        focusTimelineCard(next);
    }

    // Clicking a card (or Tab onto it) makes it the card the arrows go on from
    document.addEventListener('focusin', (e) => {
        const card = e.target.closest && e.target.closest('.tl-photo-card');
        if (!card || !tlContentEl || !tlContentEl.contains(card)) return;
        const old = tlContentEl.querySelector('.tl-photo-card[tabindex="0"]');
        if (old && old !== card) old.tabIndex = -1;
        card.tabIndex = 0;
        tlFocusIndex = Number(card.getAttribute('data-index'));
    });

    // ── Scroll Handler ────────────────────────────────────
    function onTimelineScroll() {
        if (!tlScrollTicking) {
            window.requestAnimationFrame(() => {
                renderVisibleRows();
                updateSliderFromScroll();
                updateCurrentDateLabel();
                tlScrollTicking = false;
            });
            tlScrollTicking = true;
        }
    }

    function updateCurrentDateLabel() {
        if (!tlScrollContainer || tlLayoutRows.length === 0) return;

        const scrollTop = tlScrollContainer.scrollTop;
        const label = document.getElementById('timeline-current-date');
        if (!label) return;

        // Find the header row closest to current scroll position using the rendered range
        let searchStart = Math.min(tlRenderedRange.end >= 0 ? tlRenderedRange.end : tlLayoutRows.length - 1, tlLayoutRows.length - 1);
        for (let i = searchStart; i >= 0; i--) {
            if (tlLayoutRows[i] && tlLayoutRows[i].type === 'header' && tlLayoutRows[i].top <= scrollTop + 60) {
                if (label.textContent !== tlLayoutRows[i].label) {
                    // De maand rolt: omhoog als je verder scrolt, omlaag als je teruggaat
                    MediaMotion.timelineLabel(label, tlLayoutRows[i].label, scrollTop);
                }
                return;
            }
        }

        if (tlLayoutRows[0]) {
            label.textContent = tlLayoutRows[0].label || '';
        }
    }

    // ── Slider ────────────────────────────────────────────
    function setupSlider() {
        const slider = document.getElementById('timeline-slider');
        const yearLabels = document.getElementById('timeline-year-labels');
        if (!slider) return;

        // Set range
        slider.min = 0;
        slider.max = 1000;
        slider.value = 0;

        // Build year labels
        if (yearLabels) {
            yearLabels.innerHTML = '';
            const years = Object.keys(tlYearBoundaries).map(Number).sort((a, b) => a - b);
            years.forEach(year => {
                const lbl = document.createElement('span');
                lbl.className = 'tl-year-label';
                lbl.textContent = year;
                lbl.onclick = () => jumpToYear(year);
                yearLabels.appendChild(lbl);
            });
        }

        // Slider input
        slider.oninput = () => {
            tlSliderDragging = true;
            const pct = slider.value / 1000;
            const scrollTarget = pct * (tlTotalHeight - (tlScrollContainer ? tlScrollContainer.clientHeight : 0));
            if (tlScrollContainer) {
                tlScrollContainer.scrollTop = scrollTarget;
            }
            updateSliderTooltip(pct);
        };

        slider.onchange = () => {
            tlSliderDragging = false;
            hideSliderTooltip();
        };

        slider.onmousedown = () => { tlSliderDragging = true; };
        slider.onmouseup = () => {
            tlSliderDragging = false;
            hideSliderTooltip();
        };
    }

    function updateSliderFromScroll() {
        if (tlSliderDragging) return;

        const slider = document.getElementById('timeline-slider');
        if (!slider || !tlScrollContainer) return;

        const maxScroll = tlTotalHeight - tlScrollContainer.clientHeight;
        if (maxScroll <= 0) {
            slider.value = 0;
            return;
        }

        const pct = tlScrollContainer.scrollTop / maxScroll;
        slider.value = Math.round(pct * 1000);
    }

    function updateSliderTooltip(pct) {
        const tooltip = document.getElementById('timeline-slider-tooltip');
        if (!tooltip) return;

        // Find the date at this position
        const targetTop = pct * tlTotalHeight;
        let dateLabel = '';

        for (let i = tlLayoutRows.length - 1; i >= 0; i--) {
            if (tlLayoutRows[i].type === 'header' && tlLayoutRows[i].top <= targetTop) {
                dateLabel = tlLayoutRows[i].label;
                break;
            }
        }

        if (dateLabel) {
            tooltip.textContent = dateLabel;
            tooltip.style.opacity = '1';

            // Position tooltip along slider
            const slider = document.getElementById('timeline-slider');
            if (slider) {
                const rect = slider.getBoundingClientRect();
                const thumbPos = pct * rect.width;
                tooltip.style.left = `${thumbPos}px`;
            }
        }
    }

    function hideSliderTooltip() {
        const tooltip = document.getElementById('timeline-slider-tooltip');
        if (tooltip) tooltip.style.opacity = '0';
    }

    // ── Year Jump ─────────────────────────────────────────
    window.jumpToYear = function (year) {
        const idx = tlYearBoundaries[year];
        if (idx === undefined) return;
        if (idx >= tlFiles.length) {
            // Nog niet geladen: springen zodra die pagina er is
            tlPendingYear = year;
            showToast('Tijdlijn', `${year} wordt nog geladen…`, 'info');
            return;
        }

        // Find the layout row that contains this file index
        for (let i = 0; i < tlLayoutRows.length; i++) {
            const lr = tlLayoutRows[i];
            if (lr.type === 'header') {
                // Check if the next rows belong to a group starting at or before idx
                const group = tlGroups[lr.groupIdx];
                if (group && group.startIdx <= idx && group.endIdx >= idx) {
                    if (tlScrollContainer) {
                        tlScrollContainer.scrollTo({ top: lr.top, behavior: Motion.full() ? 'smooth' : 'auto' });
                    }
                    return;
                }
            }
        }

        // Fallback: use header for the group containing this index
        for (let i = 0; i < tlGroups.length; i++) {
            if (tlGroups[i].startIdx <= idx && tlGroups[i].endIdx >= idx) {
                const headerRow = tlLayoutRows.find(r => r.type === 'header' && r.groupIdx === i);
                if (headerRow && tlScrollContainer) {
                    tlScrollContainer.scrollTo({ top: headerRow.top, behavior: Motion.full() ? 'smooth' : 'auto' });
                }
                return;
            }
        }
    };

    // ── Empty State ───────────────────────────────────────
    function renderEmptyState(message) {
        const contentEl = tlContentEl;
        const infoBar = document.getElementById('timeline-info-bar');
        const sliderWrap = document.getElementById('timeline-slider-wrap');

        if (infoBar) infoBar.style.display = 'none';
        if (sliderWrap) sliderWrap.style.display = 'none';

        if (contentEl) {
            contentEl.style.height = 'auto';
            const failed = message && tlScanFailed;
            contentEl.replaceChildren(emptyState({
                icon: failed ? 'fa-exclamation-triangle' : 'fa-images',
                tone: failed ? 'error' : null,
                title: message || 'Kies mappen voor je tijdlijn',
                text: failed ? 'Probeer het opnieuw, of kies andere mappen.'
                    : 'Kies een of meer mappen uit het archief; alle foto\'s en video\'s komen dan op datum onder elkaar.',
                action: { label: 'Mappen kiezen', onClick: timelineOpenFolderPicker, primary: true },
            }));
        }
    }

    // ── Utilities ─────────────────────────────────────────
    function debounce(fn, ms) {
        let timer;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), ms);
        };
    }

})();
