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

    // Virtualization
    const ITEM_HEIGHT = 220;            // photo card height (px)
    const COLS = 5;                     // cards per row
    const ROW_HEIGHT = ITEM_HEIGHT + 12; // row height + gap
    const HEADER_HEIGHT = 48;           // date group header height
    const PRELOAD_ROWS = 4;            // extra rows to render above/below

    let tlGroups = [];                  // [{label, date, startIdx, endIdx, files}]
    let tlLayoutRows = [];              // [{type:'header'|'row', top, height, groupIdx, rowFiles?}]
    let tlTotalHeight = 0;
    let tlRenderedRange = { start: -1, end: -1 };
    let tlScrollContainer = null;
    let tlContentEl = null;

    // Slider
    let tlSliderDragging = false;

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

        // Recompute cols on resize
        window.addEventListener('resize', debounce(recomputeLayout, 200));

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
            const res = await fetch(`${API_BASE}/api/timeline/folders`);
            if (!res.ok) throw new Error('Failed to load folders');
            const data = await res.json();
            tlFolderTree = data.folders || [];
        } catch (e) {
            console.error('Timeline: error loading folders', e);
            tlFolderTree = [];
        }
    }

    // ── Folder Picker Modal ───────────────────────────────
    window.timelineOpenFolderPicker = function () {
        const modal = document.getElementById('modal-timeline-folders');
        const tree = document.getElementById('timeline-folder-tree');
        if (!modal || !tree) return;

        tree.innerHTML = '';
        renderPickerTree(tree, tlFolderTree, 0);
        modal.classList.add('active');
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
            tlFiles = [];
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
            icon.textContent = '📁';

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
            container.innerHTML = '<span class="tl-no-folders">No folders selected</span>';
            return;
        }

        tlSelectedFolders.forEach(path => {
            const name = path.split('/').pop();
            const chip = document.createElement('div');
            chip.className = 'tl-folder-chip';
            chip.innerHTML = `
                <span class="tl-chip-name">${name}</span>
                <button class="tl-chip-remove" onclick="event.stopPropagation(); timelineRemoveFolder('${path.replace(/'/g, "\\'")}')">×</button>
            `;
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
            tlFiles = [];
            renderEmptyState();
        }
    };

    // ── Scanning ──────────────────────────────────────────
    async function scanTimeline() {
        if (tlScanning) return;
        tlScanning = true;

        const scrollEl = tlScrollContainer;
        const contentEl = tlContentEl;
        const infoBar = document.getElementById('timeline-info-bar');
        const sliderWrap = document.getElementById('timeline-slider-wrap');

        // Show loading
        if (contentEl) {
            contentEl.style.height = 'auto';
            contentEl.innerHTML = `
                <div class="tl-loading-state">
                    <div class="tl-loading-spinner"></div>
                    <p>Scanning folders…</p>
                </div>
            `;
        }

        try {
            const foldersParam = [...tlSelectedFolders].join(',');
            const res = await fetch(`${API_BASE}/api/timeline/scan?folders=${encodeURIComponent(foldersParam)}`);
            if (!res.ok) throw new Error('Scan failed');

            const data = await res.json();
            tlFiles = data.files || [];
            tlDateIndex = data.date_index || {};
            tlYearBoundaries = data.year_boundaries || {};

            if (tlFiles.length === 0) {
                renderEmptyState('No photos found in selected folders');
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
            if (infoBar) {
                const years = Object.keys(tlDateIndex).sort();
                const range = years.length > 1
                    ? `${years[0]} – ${years[years.length - 1]}`
                    : years[0] || '';
                infoBar.innerHTML = `
                    <span class="tl-info-count"><i class="fas fa-images"></i> ${tlFiles.length.toLocaleString()} photos</span>
                    <span class="tl-info-range"><i class="fas fa-calendar-alt"></i> ${range}</span>
                `;
                infoBar.style.display = 'flex';
            }

            // Show slider
            if (sliderWrap) sliderWrap.style.display = 'flex';

            // Scroll to top and render
            if (scrollEl) scrollEl.scrollTop = 0;
            tlRenderedRange = { start: -1, end: -1 };
            renderVisibleRows();

        } catch (e) {
            console.error('Timeline scan error:', e);
            renderEmptyState('Failed to scan folders. Check console for details.');
        }

        tlScanning = false;
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

            const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                'July', 'August', 'September', 'October', 'November', 'December'];
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
    function getColumns() {
        if (!tlScrollContainer) return COLS;
        const w = tlScrollContainer.clientWidth - 48; // padding
        if (w < 500) return 2;
        if (w < 750) return 3;
        if (w < 1050) return 4;
        if (w < 1400) return 5;
        return 6;
    }

    function recomputeLayout() {
        const cols = getColumns();
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
                    height: ROW_HEIGHT,
                    groupIdx: gi,
                    rowFiles: rowFiles
                });
                top += ROW_HEIGHT;
            }

            // Group spacing
            top += 16;
        }

        tlTotalHeight = top;

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
        const preloadPx = PRELOAD_ROWS * ROW_HEIGHT;

        const viewStart = scrollTop - preloadPx;
        const viewEnd = scrollTop + viewHeight + preloadPx;

        // Binary search for first visible row
        let startIdx = 0;
        let endIdx = tlLayoutRows.length - 1;

        // Find first row that is in view
        while (startIdx < tlLayoutRows.length && tlLayoutRows[startIdx].top + tlLayoutRows[startIdx].height < viewStart) {
            startIdx++;
        }
        // Find last row that is in view
        endIdx = startIdx;
        while (endIdx < tlLayoutRows.length && tlLayoutRows[endIdx].top < viewEnd) {
            endIdx++;
        }
        endIdx = Math.min(endIdx, tlLayoutRows.length - 1);

        // Skip if range hasn't changed
        if (startIdx === tlRenderedRange.start && endIdx === tlRenderedRange.end) return;

        tlRenderedRange = { start: startIdx, end: endIdx };

        // Build fragment
        const fragment = document.createDocumentFragment();

        for (let i = startIdx; i <= endIdx; i++) {
            const layoutRow = tlLayoutRows[i];

            if (layoutRow.type === 'header') {
                const header = document.createElement('div');
                header.className = 'tl-group-header';
                header.style.position = 'absolute';
                header.style.top = `${layoutRow.top}px`;
                header.style.left = '0';
                header.style.right = '0';
                header.style.height = `${layoutRow.height}px`;
                header.textContent = layoutRow.label;
                fragment.appendChild(header);
            } else {
                const row = document.createElement('div');
                row.className = 'tl-photo-row';
                row.style.position = 'absolute';
                row.style.top = `${layoutRow.top}px`;
                row.style.left = '24px';
                row.style.right = '24px';
                row.style.height = `${ROW_HEIGHT}px`;
                row.style.display = 'grid';
                row.style.gridTemplateColumns = `repeat(${getColumns()}, 1fr)`;
                row.style.gap = '10px';

                layoutRow.rowFiles.forEach(fileIdx => {
                    const file = tlFiles[fileIdx];
                    const card = createPhotoCard(file, fileIdx);
                    row.appendChild(card);
                });

                fragment.appendChild(row);
            }
        }

        tlContentEl.innerHTML = '';
        tlContentEl.appendChild(fragment);
    }

    function createPhotoCard(file, index) {
        const card = document.createElement('div');
        card.className = 'tl-photo-card';
        card.setAttribute('data-index', index);

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
            img.style.backgroundColor = 'var(--bg-tertiary)';
        });

        img.src = thumbUrl;
        card.appendChild(img);

        // Date label
        const dt = new Date(file.timestamp * 1000);
        const dateStr = `${dt.getDate()} ${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][dt.getMonth()]} ${dt.getFullYear()}`;
        const timeStr = `${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}`;

        const dateLabel = document.createElement('div');
        dateLabel.className = 'tl-photo-date';
        dateLabel.textContent = dateStr;
        card.appendChild(dateLabel);

        // Click handler - open in viewer modal
        card.onclick = () => {
            openTimelinePhoto(file, index);
        };

        return card;
    }

    function openTimelinePhoto(file, index) {
        // Reuse the existing media modal from app.js
        // We need to set up the modal data for timeline context
        const modal = document.getElementById('modal-viewer');
        if (!modal) return;

        const title = document.getElementById('modal-title');
        const body = document.getElementById('modal-body');
        const metadata = document.getElementById('modal-metadata');

        if (title) title.textContent = file.name;

        // Create appropriate media element
        let mediaHTML = '';
        if (file.type === 'video') {
            mediaHTML = `<video src="${API_BASE}/api/file?path=${encodeURIComponent(file.path)}" controls autoplay class="modal-media" style="max-width:100%;max-height:70vh;"></video>`;
        } else {
            mediaHTML = `<img src="${API_BASE}/api/file?path=${encodeURIComponent(file.path)}" class="modal-media" style="max-width:100%;max-height:70vh;object-fit:contain;">`;
        }

        // Keep nav zones
        const navZones = body.querySelectorAll('.modal-nav-zone');
        body.innerHTML = mediaHTML;
        navZones.forEach(z => body.appendChild(z));

        // Setup timeline-specific navigation
        const leftZone = body.querySelector('.modal-nav-zone.left');
        const rightZone = body.querySelector('.modal-nav-zone.right');

        if (leftZone) {
            leftZone.onclick = () => {
                if (index > 0) openTimelinePhoto(tlFiles[index - 1], index - 1);
            };
        }
        if (rightZone) {
            rightZone.onclick = () => {
                if (index < tlFiles.length - 1) openTimelinePhoto(tlFiles[index + 1], index + 1);
            };
        }

        // Metadata
        if (metadata) {
            const dt = new Date(file.timestamp * 1000);
            const sizeStr = formatSize(file.size);
            metadata.innerHTML = `
                <div class="modal-metadata-grid">
                    <div class="metadata-item"><strong>Date</strong><span>${dt.toLocaleString()}</span></div>
                    <div class="metadata-item"><strong>Size</strong><span>${sizeStr}</span></div>
                    <div class="metadata-item"><strong>Type</strong><span>${file.type}</span></div>
                    <div class="metadata-item"><strong>Path</strong><span style="font-size:0.8em;word-break:break-all;">${file.path}</span></div>
                </div>
            `;
        }

        // Wire up Finder / Delete buttons
        const finderBtn = document.getElementById('btn-open-finder');
        if (finderBtn) {
            finderBtn.onclick = () => {
                fetch(`${API_BASE}/api/open-in-finder`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: file.path })
                });
            };
        }

        modal.classList.add('active');
    }

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
        if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
        return (bytes / 1073741824).toFixed(2) + ' GB';
    }

    // ── Scroll Handler ────────────────────────────────────
    function onTimelineScroll() {
        renderVisibleRows();
        updateSliderFromScroll();
        updateCurrentDateLabel();
    }

    function updateCurrentDateLabel() {
        if (!tlScrollContainer || tlLayoutRows.length === 0) return;

        const scrollTop = tlScrollContainer.scrollTop;
        const label = document.getElementById('timeline-current-date');
        if (!label) return;

        // Find the header row closest to current scroll position
        for (let i = tlLayoutRows.length - 1; i >= 0; i--) {
            if (tlLayoutRows[i].type === 'header' && tlLayoutRows[i].top <= scrollTop + 60) {
                label.textContent = tlLayoutRows[i].label;
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

        // Find the layout row that contains this file index
        for (let i = 0; i < tlLayoutRows.length; i++) {
            const lr = tlLayoutRows[i];
            if (lr.type === 'header') {
                // Check if the next rows belong to a group starting at or before idx
                const group = tlGroups[lr.groupIdx];
                if (group && group.startIdx <= idx && group.endIdx >= idx) {
                    if (tlScrollContainer) {
                        tlScrollContainer.scrollTo({ top: lr.top, behavior: 'smooth' });
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
                    tlScrollContainer.scrollTo({ top: headerRow.top, behavior: 'smooth' });
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
            contentEl.innerHTML = `
                <div class="tl-empty-state">
                    <div class="tl-empty-icon">📷</div>
                    <h3>${message || 'Select folders to explore your photo timeline'}</h3>
                    <p>Choose one or more folders from your vault to see all photos organized chronologically.</p>
                    <button class="btn btn-primary" onclick="timelineOpenFolderPicker()">
                        <i class="fas fa-folder-plus"></i> Select Folders
                    </button>
                </div>
            `;
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
