// ============================================================
// Centralized Folder Tree Component
// Single source of truth for all folder tree rendering + keyboard nav
// ============================================================

/**
 * Render a folder tree into a container element.
 *
 * @param {HTMLElement} container  - The DOM element to render into (will be cleared)
 * @param {Array}       folders   - Array of folder objects { name, path, children, media_count? }
 * @param {Object}      config    - Configuration object (see below)
 *
 * @returns {{ refresh: Function, getSelectedPaths: Function }}
 */
function renderFolderTree(container, folders, config = {}) {
    const {
        itemClass = 'folder-item',
        childrenClass = 'folder-children',
        indent = 20,
        indentBase = 8,
        addDepthClass = false,       // adds depth-N class to rows

        collapseMode = 'collapsed',  // 'collapsed' | 'expanded' (inverted) | 'flat'
        startExpanded = false,
        expandedPaths = null,        // external Set for persistent expand state

        selectionMode = 'single',    // 'single' | 'multi'
        selectedPaths = new Set(),
        selectedPath = null,

        showChevron = true,
        chevronHtml = html`<i class="fas fa-chevron-right" aria-hidden="true"></i>`,        // markup from html`...` (dom.js)
        showCheckbox = (selectionMode === 'multi'),
        showCount = false,

        iconMode = 'fontawesome',    // 'fontawesome' | 'emoji' | 'emoji-state'

        onSelect,                    // (path, element) => void
        onToggle,                    // (path, isExpanded) => void
        onCheckToggle,               // (path, isChecked) => void

        separateClicks = false,      // true: icon=toggle, name=select
    } = config;

    const _expandedPaths = expandedPaths || new Set();
    container.innerHTML = '';

    if (!folders || folders.length === 0) {
        container.replaceChildren(createEl('div', { class: 'folder-tree-empty', text: 'Geen mappen gevonden' }));
        return { refresh: () => renderFolderTree(container, folders, config), getSelectedPaths: () => selectedPaths };
    }

    function isExpanded(path) {
        if (collapseMode === 'flat') return true;
        if (startExpanded) return true;
        return _expandedPaths.has(path);
    }

    function getIcon(folder, depth, expanded, isSelected) {
        if (iconMode === 'fontawesome') {
            const cls = isSelected ? 'fa-folder-open' : 'fa-folder';
            return { html: html`<i class="fas ${cls}"></i>` };
        }
        if (iconMode === 'emoji-state') {
            if (isSelected) return { text: '🗂️' };
            if (depth === 0) return { text: (folder.children?.length > 0 && expanded) ? '📂' : '📦' };
            return { text: (folder.children?.length > 0 && expanded) ? '📂' : '📁' };
        }
        return { text: '📁' };
    }

    function doToggleExpand(path, chevronEl, childrenEl) {
        if (collapseMode === 'flat') return;
        const wasExpanded = _expandedPaths.has(path);
        if (wasExpanded) {
            _expandedPaths.delete(path);
            if (collapseMode === 'collapsed') childrenEl.classList.add('collapsed');
            else childrenEl.classList.remove('expanded');
            if (chevronEl) chevronEl.classList.remove('expanded');
        } else {
            _expandedPaths.add(path);
            if (collapseMode === 'collapsed') childrenEl.classList.remove('collapsed');
            else childrenEl.classList.add('expanded');
            if (chevronEl) chevronEl.classList.add('expanded');
        }
        if (onToggle) onToggle(path, !wasExpanded);
    }

    function doSelect(path, rowEl) {
        container.querySelectorAll('.' + itemClass).forEach(el => el.classList.remove('selected'));
        rowEl.classList.add('selected');
        if (onSelect) onSelect(path, rowEl);
    }

    function doCheckToggle(path, rowEl) {
        const checkbox = rowEl.querySelector('.tl-picker-checkbox');
        if (selectedPaths.has(path)) {
            selectedPaths.delete(path);
            if (checkbox) { checkbox.classList.remove('checked'); checkbox.innerHTML = ''; }
        } else {
            selectedPaths.add(path);
            if (checkbox) { checkbox.classList.add('checked'); checkbox.innerHTML = '<i class="fas fa-check"></i>'; }
        }
        if (onCheckToggle) onCheckToggle(path, selectedPaths.has(path));
    }

    function createNode(folder, depth, parentEl) {
        const hasChildren = folder.children && folder.children.length > 0;
        const expanded = isExpanded(folder.path);
        const isSel = (selectionMode === 'single')
            ? (selectedPath === folder.path)
            : selectedPaths.has(folder.path);

        const wrapper = document.createElement('div');

        // The row
        const row = document.createElement('div');
        row.className = itemClass + (isSel ? ' selected' : '') + (addDepthClass ? ` depth-${depth}` : '');
        row.style.paddingLeft = `${depth * indent + indentBase}px`;
        row.dataset.path = folder.path;

        // Children container (created early so toggleExpand can reference it)
        const childrenContainer = document.createElement('div');

        // Chevron
        if (showChevron) {
            if (hasChildren) {
                const chevron = document.createElement('span');
                chevron.className = 'folder-chevron' + (expanded ? ' expanded' : '');
                setHTML(chevron, chevronHtml);
                chevron.onclick = (e) => {
                    e.stopPropagation();
                    doToggleExpand(folder.path, chevron, childrenContainer);
                };
                row.appendChild(chevron);
            } else {
                const spacer = document.createElement('span');
                spacer.className = 'folder-chevron no-children';
                spacer.style.cssText = 'visibility:hidden;';
                setHTML(spacer, chevronHtml);
                row.appendChild(spacer);
            }
        }

        // Checkbox (multi-select)
        if (showCheckbox) {
            const checkbox = document.createElement('div');
            checkbox.className = 'tl-picker-checkbox' + (isSel ? ' checked' : '');
            checkbox.innerHTML = isSel ? '<i class="fas fa-check"></i>' : '';
            row.appendChild(checkbox);
        }

        // Icon
        const iconEl = document.createElement('span');
        iconEl.className = 'folder-icon';
        const iconData = getIcon(folder, depth, expanded, isSel);
        if (iconData.html) setHTML(iconEl, iconData.html);
        else iconEl.textContent = iconData.text;

        if (separateClicks && hasChildren) {
            iconEl.style.cursor = 'pointer';
            iconEl.title = 'Map open- of dichtklappen';
            iconEl.onclick = (e) => {
                e.stopPropagation();
                doToggleExpand(folder.path, row.querySelector('.folder-chevron'), childrenContainer);
            };
        }
        row.appendChild(iconEl);

        // Name
        const nameEl = document.createElement('span');
        nameEl.className = 'folder-name';
        nameEl.textContent = folder.name;
        nameEl.title = folder.path;

        // Count badge (timeline)
        if (showCount && folder.media_count > 0) {
            const countEl = document.createElement('span');
            countEl.className = 'tl-picker-count';
            countEl.textContent = folder.media_count;
            // Append after name
            nameEl.after_count = countEl; // store reference, append after nameEl
        }
        row.appendChild(nameEl);
        if (nameEl.after_count) row.appendChild(nameEl.after_count);

        // Click handler
        if (separateClicks) {
            nameEl.style.cursor = 'pointer';
            nameEl.onclick = (e) => {
                e.stopPropagation();
                doSelect(folder.path, row);
            };
        } else {
            row.onclick = (e) => {
                e.stopPropagation();
                if (selectionMode === 'multi') {
                    doCheckToggle(folder.path, row);
                } else {
                    doSelect(folder.path, row);
                    // Auto-expand on select if has children
                    if (hasChildren && !isExpanded(folder.path)) {
                        doToggleExpand(folder.path, row.querySelector('.folder-chevron'), childrenContainer);
                    }
                }
            };
        }

        wrapper.appendChild(row);

        // Children
        if (collapseMode === 'flat') {
            // Flat: render children directly, no container needed for collapse
            if (hasChildren) {
                folder.children.forEach(child => createNode(child, depth + 1, parentEl));
            }
        } else if (hasChildren) {
            childrenContainer.className = childrenClass +
                (collapseMode === 'collapsed'
                    ? (expanded ? '' : ' collapsed')
                    : (expanded ? ' expanded' : ''));
            folder.children.forEach(child => createNode(child, depth + 1, childrenContainer));
            wrapper.appendChild(childrenContainer);
        }

        parentEl.appendChild(wrapper);
    }

    folders.forEach(folder => createNode(folder, 0, container));

    return {
        refresh: () => renderFolderTree(container, folders, config),
        getSelectedPaths: () => selectedPaths,
    };
}


// ============================================================
// Keyboard Navigation (works with any tree rendered above)
// ============================================================

function initFolderTreeKeyNav(config) {
    const {
        containerId,
        itemSelector = '.folder-item',
        onSelect,
        onHighlight,
        onClose,
        onConfirm,
        chevronSelector = '.folder-chevron',
        childrenSelector = '.folder-children',
        invertCollapse = false,
        expandedClass = 'expanded',
        collapsedClass = 'collapsed',
        typeahead = false,          // letters typen: naar de eerste map die zo heet, ook dichtgeklapt
        enterOpens = false,         // Enter gaat een map met submappen in; ⌘/Shift+Enter bevestigt
        onQuery,                    // (query, found) => void: de getypte zoekterm tonen ('' = weg)
    } = config;

    let highlightIndex = -1;
    let query = '';
    let queryTimer = null;
    let autoOpened = [];            // mappen die het zoeken zelf openklapte (gaan weer dicht)
    const QUERY_MS = 1500;          // zo lang na de laatste letter telt de volgende mee

    function getContainer() {
        return document.getElementById(containerId);
    }

    function isChildrenHidden(childDiv) {
        if (invertCollapse) return !childDiv.classList.contains(expandedClass);
        return childDiv.classList.contains(collapsedClass);
    }

    function showChildren(childDiv, chevron) {
        if (invertCollapse) childDiv.classList.add(expandedClass);
        else childDiv.classList.remove(collapsedClass);
        if (chevron) chevron.classList.add(expandedClass);
    }

    function hideChildren(childDiv, chevron) {
        if (invertCollapse) childDiv.classList.remove(expandedClass);
        else childDiv.classList.add(collapsedClass);
        if (chevron) chevron.classList.remove(expandedClass);
    }

    function getVisibleItems() {
        const container = getContainer();
        if (!container) return [];
        return Array.from(container.querySelectorAll(itemSelector)).filter(el => {
            let parent = el.parentElement;
            while (parent && parent.id !== containerId) {
                if (parent.matches && parent.matches(childrenSelector) && isChildrenHidden(parent)) return false;
                parent = parent.parentElement;
            }
            return true;
        });
    }

    function clearHighlight() {
        const container = getContainer();
        if (!container) return;
        container.querySelectorAll(itemSelector + '.keyboard-focus')
            .forEach(el => el.classList.remove('keyboard-focus'));
    }

    function applyHighlight(items) {
        clearHighlight();
        if (highlightIndex >= 0 && highlightIndex < items.length) {
            items[highlightIndex].classList.add('keyboard-focus');
            items[highlightIndex].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            if (onHighlight) onHighlight(items[highlightIndex]);
        }
    }

    // Elke rij staat in een eigen wrapper (rij + eventueel de submappen). Niet verder omhoog
    // zoeken: dan vond een map zonder submappen de wrapper van zijn ouder, en klapte ← die dicht
    function findWrapper(item) {
        const wrapper = item.parentElement;
        return wrapper && wrapper.id !== containerId ? wrapper : null;
    }

    function getItemChildDiv(item) {
        const w = findWrapper(item);
        return w ? w.querySelector(':scope > ' + childrenSelector) : null;
    }

    function hasChildren(item) {
        const cd = getItemChildDiv(item);
        return cd && cd.children.length > 0;
    }

    function isItemExpanded(item) {
        const cd = getItemChildDiv(item);
        return cd && !isChildrenHidden(cd);
    }

    function expandItem(item) {
        const cd = getItemChildDiv(item);
        if (cd && isChildrenHidden(cd)) showChildren(cd, item.querySelector(chevronSelector));
    }

    function collapseItem(item) {
        const cd = getItemChildDiv(item);
        if (cd && !isChildrenHidden(cd)) hideChildren(cd, item.querySelector(chevronSelector));
    }

    function findParentItem(item) {
        let el = item.parentElement;
        while (el && el.id !== containerId) {
            el = el.parentElement;
            if (!el) return null;
            const pr = el.querySelector(':scope > ' + itemSelector);
            if (pr && pr !== item) return pr;
        }
        return null;
    }

    // ---------- typen om te zoeken ----------

    // 'Reis Italië' vindt ook 'reis italie'
    function fold(text) {
        return text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    }

    function nameEl(item) {
        return item.querySelector('.folder-name');
    }

    function clearMatchMarks() {
        const container = getContainer();
        if (!container) return;
        container.querySelectorAll('.folder-name[data-plain]').forEach(el => {
            el.textContent = el.dataset.plain;
            delete el.dataset.plain;
        });
    }

    // Het getypte stuk van de naam oplichten (zelfde lengte na fold: alleen accenten vallen weg)
    function markMatch(item, at, length) {
        const el = nameEl(item);
        if (!el) return;
        const plain = el.dataset.plain || el.textContent;
        el.dataset.plain = plain;
        el.replaceChildren(plain.slice(0, at),
            createEl('mark', { class: 'tree-match', text: plain.slice(at, at + length) }),
            plain.slice(at + length));
    }

    function setQuery(next, found = true) {
        query = next;
        clearTimeout(queryTimer);
        if (query) queryTimer = setTimeout(() => setQuery(''), QUERY_MS);
        else {
            clearMatchMarks();
            autoOpened = [];        // wat bij de laatste treffer openging, blijft open
        }
        if (onQuery) onQuery(query, found);
    }

    // Eerst de zichtbare mappen vanaf de gemarkeerde, dan de hele boom (ook dichtgeklapt);
    // eerst 'begint met', dan 'bevat'. Dezelfde letter herhalen gaat naar de volgende.
    function findMatch(q, items) {
        const all = Array.from(getContainer().querySelectorAll(itemSelector));
        const repeat = q.length > 1 && [...q].every(ch => ch === q[0]);
        const needle = fold(repeat ? q[0] : q);
        const start = Math.max(0, highlightIndex + (repeat || q.length === 1 ? 1 : 0));
        const rotated = items.slice(start).concat(items.slice(0, start));
        const name = item => fold((nameEl(item) || item).textContent);
        for (const list of [rotated, all]) {
            const hit = list.find(item => name(item).startsWith(needle));
            if (hit) return { item: hit, at: 0, length: needle.length };
        }
        for (const list of [rotated, all]) {
            const hit = list.find(item => name(item).includes(needle));
            if (hit) return { item: hit, at: name(hit).indexOf(needle), length: needle.length };
        }
        return null;
    }

    function revealItem(item, track = false) {
        for (let parent = findParentItem(item); parent; parent = findParentItem(parent)) {
            if (track && !isItemExpanded(parent)) autoOpened.push(parent);
            expandItem(parent);
        }
    }

    function jumpToQuery(items) {
        const match = findMatch(query, items);
        clearMatchMarks();
        if (!match) {
            if (onQuery) onQuery(query, false);
            return;
        }
        // Wat een vorige treffer openklapte en niet boven deze treffer ligt: weer dicht
        const keep = autoOpened.filter(parent => {
            const wrapper = findWrapper(parent);
            return wrapper && wrapper.contains(match.item);
        });
        autoOpened.filter(parent => !keep.includes(parent)).reverse().forEach(collapseItem);
        autoOpened = keep;
        revealItem(match.item, true);
        highlightItem(match.item);
        markMatch(match.item, match.at, match.length);
        if (onQuery) onQuery(query, true);
    }

    // Een map markeren (ook van buitenaf: een muisklik, de huidige map bij openen)
    function highlightItem(item, notify = true) {
        if (!item) return;
        revealItem(item);
        const items = getVisibleItems();
        const index = items.indexOf(item);
        if (index < 0) return;
        highlightIndex = index;
        if (notify) applyHighlight(items);
        else {
            clearHighlight();
            item.classList.add('keyboard-focus');
            item.scrollIntoView({ block: 'nearest' });
        }
    }

    function handleKeydown(e) {
        const container = getContainer();
        if (!container || container.offsetParent === null) return;

        if (typeahead && !e.metaKey && !e.ctrlKey && !e.altKey) {
            const items = getVisibleItems();
            if (e.key.length === 1 && (e.key !== ' ' || query)) {
                e.preventDefault(); e.stopPropagation();
                setQuery(query + e.key);
                jumpToQuery(items);
                return;
            }
            if (e.key === 'Backspace' && query) {
                e.preventDefault(); e.stopPropagation();
                setQuery(query.slice(0, -1));
                if (query) jumpToQuery(items);
                return;
            }
            if (e.key === 'Escape' && query) {
                // Eerst de zoekterm weg, pas daarna sluit Escape het venster
                e.preventDefault(); e.stopPropagation();
                setQuery('');
                return;
            }
        }

        // Enter op een knop of veld in het venster (Annuleren, ×, zoekveld) is voor die knop:
        // niet de gemarkeerde map bevestigen. Anders verplaatst 'Annuleren' het bestand.
        if (e.key === 'Enter' && e.target instanceof Element) {
            const control = e.target.closest('button, a[href], input, select, textarea, summary, [role="button"]');
            if (control && !control.matches(itemSelector)) return;
        }

        const items = getVisibleItems();
        if (items.length === 0 && e.key !== 'Escape') return;
        if (query && e.key.startsWith('Arrow')) setQuery('');

        switch (e.key) {
            case 'ArrowDown':
                e.preventDefault(); e.stopPropagation();
                highlightIndex = Math.min(items.length - 1, highlightIndex + 1);
                applyHighlight(items);
                break;
            case 'ArrowUp':
                e.preventDefault(); e.stopPropagation();
                highlightIndex = Math.max(0, highlightIndex - 1);
                applyHighlight(items);
                break;
            case 'ArrowRight':
                e.preventDefault(); e.stopPropagation();
                if (highlightIndex >= 0 && highlightIndex < items.length) {
                    const item = items[highlightIndex];
                    if (hasChildren(item) && !isItemExpanded(item)) {
                        expandItem(item);
                    } else if (hasChildren(item) && isItemExpanded(item)) {
                        const ni = getVisibleItems();
                        if (highlightIndex + 1 < ni.length) { highlightIndex++; applyHighlight(ni); }
                    }
                }
                break;
            case 'ArrowLeft':
                e.preventDefault(); e.stopPropagation();
                if (highlightIndex >= 0 && highlightIndex < items.length) {
                    const item = items[highlightIndex];
                    if (hasChildren(item) && isItemExpanded(item)) {
                        collapseItem(item);
                        const ni = getVisibleItems();
                        highlightIndex = Math.min(highlightIndex, ni.length - 1);
                        applyHighlight(ni);
                    } else {
                        const pi = findParentItem(item);
                        if (pi) {
                            const ni = getVisibleItems();
                            const idx = ni.indexOf(pi);
                            if (idx >= 0) { highlightIndex = idx; applyHighlight(ni); }
                        }
                    }
                }
                break;
            case 'Enter':
                e.preventDefault(); e.stopPropagation();
                if (highlightIndex >= 0 && highlightIndex < items.length) {
                    const item = items[highlightIndex];
                    if (enterOpens && !e.metaKey && !e.ctrlKey && !e.shiftKey && hasChildren(item)) {
                        // De map in: open hem en markeer de eerste submap
                        setQuery('');
                        expandItem(item);
                        const ni = getVisibleItems();
                        highlightIndex = ni.indexOf(item) + 1;
                        applyHighlight(ni);
                        break;
                    }
                    setQuery('');
                    if (onSelect) onSelect(item);
                    if (onConfirm) onConfirm();
                }
                break;
            case 'Escape':
                e.preventDefault(); e.stopPropagation();
                if (onClose) onClose();
                break;
        }
    }

    function resetIndex() {
        highlightIndex = -1;
        clearHighlight();
        if (query) setQuery('');
    }

    return { resetIndex, handleKeydown, highlightItem };
}


// ============================================================
// Helper: activate keyboard nav on a modal with a folder tree
// ============================================================

function activateFolderTreeModal(opts) {
    const {
        modalId, containerId,
        itemSelector = '.folder-item',
        childrenSelector = '.folder-children',
        invertCollapse = false,
        onSelect, onHighlight, onConfirm, onClose,
        typeahead, enterOpens, onQuery,
        focusSelector,
    } = opts;

    const nav = initFolderTreeKeyNav({
        containerId, itemSelector, childrenSelector, invertCollapse,
        onSelect, onHighlight, onConfirm, onClose,
        typeahead, enterOpens, onQuery,
    });

    const modal = document.getElementById(modalId);
    if (modal) {
        // Eén luisteraar per modal: die van een vorige keer openen eerst weghalen. Anders
        // reageren alle oude navigaties (elk met een eigen markering) ook op Enter en kan
        // een bestand in een andere map belanden dan de gemarkeerde.
        if (modal._treeNav) {
            modal.removeEventListener('keydown', modal._treeNav.handleKeydown);
            modal._treeNav.resetIndex();
        }
        modal._treeNav = nav;
        modal.setAttribute('tabindex', '-1');
        modal.addEventListener('keydown', nav.handleKeydown);
        const focusEl = focusSelector ? modal.querySelector(focusSelector) : null;
        setTimeout(() => (focusEl || modal).focus(), 80);
    }

    return { nav };
}
