// ============================================================
// Shared keyboard navigation for all folder tree popups
// Supports: Arrow Up/Down (navigate), Right (expand), Left (collapse/parent),
//           Enter (select/confirm), Escape (close)
// ============================================================

/**
 * Attach keyboard navigation to a folder tree container.
 *
 * @param {Object} config
 * @param {string}   config.containerId       - ID of the tree container element
 * @param {string}   [config.itemSelector]    - CSS selector for navigable rows
 * @param {Function} [config.onSelect]        - Called with the highlighted element on Enter
 * @param {Function} [config.onClose]         - Called on Escape
 * @param {Function} [config.onConfirm]       - Called on Enter after selecting
 * @param {string}   [config.chevronSelector] - CSS selector for expand chevron inside a row
 * @param {string}   [config.childrenSelector]- CSS selector for children container
 * @param {boolean}  [config.invertCollapse]  - If true, children are hidden by default and shown with expandedClass
 * @param {string}   [config.expandedClass]   - Class on chevron/children when expanded
 * @param {string}   [config.collapsedClass]  - Class on children when collapsed (only if !invertCollapse)
 *
 * @returns {{ destroy: Function, resetIndex: Function, handleKeydown: Function, attachTo: Function }}
 */
function initFolderTreeKeyNav(config) {
    const {
        containerId,
        itemSelector = '.folder-item',
        onSelect,
        onClose,
        onConfirm,
        chevronSelector = '.folder-chevron',
        childrenSelector = '.folder-children',
        invertCollapse = false,
        expandedClass = 'expanded',
        collapsedClass = 'collapsed',
    } = config;

    let highlightIndex = -1;

    function getContainer() {
        return document.getElementById(containerId);
    }

    // Check if a children container is currently hidden
    function isChildrenHidden(childDiv) {
        if (invertCollapse) {
            // Hidden by default, visible when has expandedClass
            return !childDiv.classList.contains(expandedClass);
        } else {
            // Visible by default, hidden when has collapsedClass
            return childDiv.classList.contains(collapsedClass);
        }
    }

    function showChildren(childDiv, chevron) {
        if (invertCollapse) {
            childDiv.classList.add(expandedClass);
        } else {
            childDiv.classList.remove(collapsedClass);
        }
        if (chevron) chevron.classList.add(expandedClass);
    }

    function hideChildren(childDiv, chevron) {
        if (invertCollapse) {
            childDiv.classList.remove(expandedClass);
        } else {
            childDiv.classList.add(collapsedClass);
        }
        if (chevron) chevron.classList.remove(expandedClass);
    }

    function getVisibleItems() {
        const container = getContainer();
        if (!container) return [];
        const all = container.querySelectorAll(itemSelector);
        return Array.from(all).filter(el => {
            let parent = el.parentElement;
            while (parent && parent.id !== containerId) {
                // Check if this parent is a children container that is hidden
                if (parent.matches && parent.matches(childrenSelector) && isChildrenHidden(parent)) {
                    return false;
                }
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
            const target = items[highlightIndex];
            target.classList.add('keyboard-focus');
            target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
    }

    function findWrapper(item) {
        let el = item.parentElement;
        while (el && el.id !== containerId) {
            const childDiv = el.querySelector(':scope > ' + childrenSelector);
            if (childDiv) return el;
            el = el.parentElement;
        }
        return null;
    }

    function getItemChildDiv(item) {
        const wrapper = findWrapper(item);
        if (!wrapper) return null;
        return wrapper.querySelector(':scope > ' + childrenSelector);
    }

    function hasChildren(item) {
        return !!getItemChildDiv(item);
    }

    function isItemExpanded(item) {
        const childDiv = getItemChildDiv(item);
        return childDiv && !isChildrenHidden(childDiv);
    }

    function expandItem(item) {
        const childDiv = getItemChildDiv(item);
        const chevron = item.querySelector(chevronSelector);
        if (childDiv && isChildrenHidden(childDiv)) {
            showChildren(childDiv, chevron);
        }
    }

    function collapseItem(item) {
        const childDiv = getItemChildDiv(item);
        const chevron = item.querySelector(chevronSelector);
        if (childDiv && !isChildrenHidden(childDiv)) {
            hideChildren(childDiv, chevron);
        }
    }

    function findParentItem(item) {
        let el = item.parentElement;
        while (el && el.id !== containerId) {
            el = el.parentElement;
            if (!el) return null;
            const parentRow = el.querySelector(':scope > ' + itemSelector);
            if (parentRow && parentRow !== item) return parentRow;
        }
        return null;
    }

    function handleKeydown(e) {
        const container = getContainer();
        if (!container || container.offsetParent === null) return;

        const items = getVisibleItems();
        if (items.length === 0 && e.key !== 'Escape') return;

        switch (e.key) {
            case 'ArrowDown': {
                e.preventDefault();
                e.stopPropagation();
                highlightIndex = Math.min(items.length - 1, highlightIndex + 1);
                applyHighlight(items);
                break;
            }
            case 'ArrowUp': {
                e.preventDefault();
                e.stopPropagation();
                highlightIndex = Math.max(0, highlightIndex - 1);
                applyHighlight(items);
                break;
            }
            case 'ArrowRight': {
                e.preventDefault();
                e.stopPropagation();
                if (highlightIndex >= 0 && highlightIndex < items.length) {
                    const item = items[highlightIndex];
                    if (hasChildren(item) && !isItemExpanded(item)) {
                        expandItem(item);
                    } else if (hasChildren(item) && isItemExpanded(item)) {
                        const newItems = getVisibleItems();
                        const nextIdx = highlightIndex + 1;
                        if (nextIdx < newItems.length) {
                            highlightIndex = nextIdx;
                            applyHighlight(newItems);
                        }
                    }
                }
                break;
            }
            case 'ArrowLeft': {
                e.preventDefault();
                e.stopPropagation();
                if (highlightIndex >= 0 && highlightIndex < items.length) {
                    const item = items[highlightIndex];
                    if (hasChildren(item) && isItemExpanded(item)) {
                        collapseItem(item);
                        const newItems = getVisibleItems();
                        highlightIndex = Math.min(highlightIndex, newItems.length - 1);
                        applyHighlight(newItems);
                    } else {
                        const parentItem = findParentItem(item);
                        if (parentItem) {
                            const newItems = getVisibleItems();
                            const parentIdx = newItems.indexOf(parentItem);
                            if (parentIdx >= 0) {
                                highlightIndex = parentIdx;
                                applyHighlight(newItems);
                            }
                        }
                    }
                }
                break;
            }
            case 'Enter': {
                e.preventDefault();
                e.stopPropagation();
                if (highlightIndex >= 0 && highlightIndex < items.length) {
                    const item = items[highlightIndex];
                    if (onSelect) onSelect(item);
                    if (onConfirm) onConfirm();
                }
                break;
            }
            case 'Escape': {
                e.preventDefault();
                e.stopPropagation();
                if (onClose) onClose();
                break;
            }
        }
    }

    function attachTo(element) {
        element.addEventListener('keydown', handleKeydown);
    }

    function destroy() {
        // Remove from document-level if we attached there
    }

    function resetIndex() {
        highlightIndex = -1;
        clearHighlight();
    }

    return { destroy, resetIndex, handleKeydown, attachTo };
}
