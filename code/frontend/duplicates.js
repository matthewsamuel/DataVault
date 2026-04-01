// DataVault Dashboard - Duplicates Management with Folder Selection
// Note: API_BASE is defined in app.js

// State
let currentDuplicates = [];
let filteredDuplicates = [];
let currentFilter = 'all';
let progressPollInterval = null;
let selectedFolder = null;
let selectedFolderForModal = null;
let currentComparisonData = null;

// ==================== FOLDER SELECTION ====================

function openFolderSelector() {
    const modal = document.getElementById('modal-folder-selector');
    const tree = document.getElementById('folder-selector-tree');

    // Render folder tree in modal
    tree.innerHTML = '';
    if (folderTree && folderTree.length > 0) {
        folderTree.forEach(folder => {
            const element = createFolderSelectorElement(folder, 0);
            tree.appendChild(element);
        });
    } else {
        tree.innerHTML = '<p class="text-center">No folders available</p>';
    }

    modal.classList.add('active');
}

function closeFolderSelector() {
    const modal = document.getElementById('modal-folder-selector');
    modal.classList.remove('active');
    selectedFolderForModal = null;
}

function createFolderSelectorElement(folder, depth) {
    const div = document.createElement('div');

    const item = document.createElement('div');
    item.className = 'folder-item';
    item.style.paddingLeft = `${depth * 20 + 8}px`;

    const hasChildren = folder.children && folder.children.length > 0;

    // Chevron (only if has children)
    if (hasChildren) {
        const chevron = document.createElement('span');
        chevron.className = 'folder-chevron';
        chevron.innerHTML = '▶';
        chevron.onclick = (e) => {
            e.stopPropagation();
            const childrenDiv = div.querySelector('.folder-children');
            if (childrenDiv) {
                childrenDiv.classList.toggle('collapsed');
                chevron.classList.toggle('expanded');
            }
        };
        item.appendChild(chevron);
    } else {
        const spacer = document.createElement('span');
        spacer.style.width = '16px';
        spacer.style.display = 'inline-block';
        item.appendChild(spacer);
    }

    // Folder icon
    const icon = document.createElement('span');
    icon.className = 'folder-icon';
    icon.innerHTML = '📁';
    item.appendChild(icon);

    // Folder name
    const name = document.createElement('span');
    name.className = 'folder-name';
    name.textContent = folder.name;
    item.appendChild(name);

    // Click handler for folder selection
    item.onclick = () => selectFolderInModal(folder.path, item);

    div.appendChild(item);

    // Children (collapsed by default)
    if (hasChildren) {
        const childrenDiv = document.createElement('div');
        childrenDiv.className = 'folder-children collapsed';

        folder.children.forEach(child => {
            const childElement = createFolderSelectorElement(child, depth + 1);
            childrenDiv.appendChild(childElement);
        });

        div.appendChild(childrenDiv);
    }

    return div;
}

function selectFolderInModal(path, itemElement) {
    // Remove previous selection
    document.querySelectorAll('#folder-selector-tree .folder-item').forEach(item => {
        item.classList.remove('selected');
    });

    // Add selection to clicked item
    itemElement.classList.add('selected');

    // Store selected folder
    selectedFolderForModal = path;

    // Enable confirm button
    document.getElementById('btn-confirm-folder').disabled = false;
}

function confirmFolderSelection() {
    if (!selectedFolderForModal) return;

    selectedFolder = selectedFolderForModal;

    // Update UI
    document.getElementById('selected-folder-path').textContent = selectedFolder;
    document.getElementById('btn-scan-folder').disabled = false;

    closeFolderSelector();

    showToast('Folder Selected', 'Ready to scan for duplicates', 'success');
}

// ==================== SCAN FOR DUPLICATES ====================

async function scanSelectedFolder() {
    if (!selectedFolder) {
        showToast('No Folder', 'Please select a folder first', 'info');
        return;
    }

    const btnScan = document.getElementById('btn-scan-folder');
    const btnCancel = document.getElementById('btn-cancel-scan');
    const statusPanel = document.getElementById('status-panel');
    const filterBar = document.getElementById('filter-bar');
    const progressContainer = document.getElementById('scan-progress-container');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const gridDiv = document.getElementById('duplicates-grid');

    // Disable scan button, show cancel button
    btnScan.disabled = true;
    btnScan.textContent = 'Scanning...';
    btnScan.style.display = 'none';
    btnCancel.style.display = 'inline-flex';

    // Show progress
    progressContainer.style.display = 'block';
    statusPanel.style.display = 'none';
    filterBar.style.display = 'none';
    gridDiv.innerHTML = '';

    progressBar.style.width = '0%';
    progressBar.textContent = '0%';
    progressText.textContent = 'Initializing scan...';

    // Start polling progress
    progressPollInterval = setInterval(async () => {
        try {
            const progressResp = await fetch(`${API_BASE}/api/raw_duplicates/progress`);
            if (progressResp.ok) {
                const progress = await progressResp.json();

                // Update progress bar
                const percent = progress.progress_percent || 0;
                progressBar.style.width = percent + '%';
                progressBar.textContent = percent + '%';

                // Update status text
                progressText.textContent = `${progress.phase} (${progress.processed_files}/${progress.total_files} files, ${progress.duplicates_found} duplicates found)`;

                // Check if complete or cancelled
                if (progress.status === 'complete' || progress.status === 'error' || progress.status === 'cancelled') {
                    clearInterval(progressPollInterval);
                    progressPollInterval = null;
                }
            }
        } catch (e) {
            console.error('Error polling progress:', e);
        }
    }, 500);

    try {
        const response = await fetch(`${API_BASE}/api/duplicates/scan_folder?folder_path=${encodeURIComponent(selectedFolder)}`, {
            method: 'POST'
        });

        // Stop polling
        if (progressPollInterval) {
            clearInterval(progressPollInterval);
            progressPollInterval = null;
        }

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Scan failed');
        }

        const data = await response.json();

        // Check if scan was cancelled
        const progressResp = await fetch(`${API_BASE}/api/raw_duplicates/progress`);
        if (progressResp.ok) {
            const progress = await progressResp.json();
            if (progress.status === 'cancelled') {
                progressContainer.style.display = 'none';
                showToast('Scan Cancelled', 'Scan was cancelled by user', 'info');
                return;
            }
        }

        currentDuplicates = data.duplicates;
        filteredDuplicates = [...currentDuplicates];

        // Hide progress
        progressContainer.style.display = 'none';

        // Update status panel
        document.getElementById('status-scanned').textContent = data.total_scanned;
        document.getElementById('status-found').textContent = data.duplicates_found;
        document.getElementById('status-timestamp').textContent = new Date().toLocaleTimeString();
        statusPanel.style.display = 'block';

        // Show filter if duplicates found
        if (currentDuplicates.length > 0) {
            filterBar.style.display = 'flex';
            renderDuplicates();
        } else {
            gridDiv.innerHTML = '<p class="text-center">No duplicates found in this folder!</p>';
        }

        showToast('Scan Complete', `Found ${data.duplicates_found} duplicates`, 'success');

    } catch (error) {
        if (progressPollInterval) {
            clearInterval(progressPollInterval);
            progressPollInterval = null;
        }

        console.error('Error scanning folder:', error);
        progressContainer.style.display = 'none';
        showToast('Scan Failed', error.message, 'error');
    } finally {
        btnScan.disabled = false;
        btnScan.textContent = 'Scan for Duplicates';
        btnScan.style.display = 'inline-flex';
        btnCancel.style.display = 'none';
    }
}

async function cancelScan() {
    try {
        const response = await fetch(`${API_BASE}/api/raw_duplicates/cancel`, {
            method: 'POST'
        });

        if (response.ok) {
            showToast('Cancelling', 'Scan cancellation requested...', 'info');
        }
    } catch (error) {
        console.error('Error cancelling scan:', error);
    }
}

// ==================== FILTER DUPLICATES ====================

function filterDuplicates(filter) {
    currentFilter = filter;

    // Update filter chips
    document.querySelectorAll('.filter-chip').forEach(chip => {
        chip.classList.toggle('active', chip.dataset.filter === filter);
    });

    // Filter by type
    if (filter === 'all') {
        filteredDuplicates = [...currentDuplicates];
    } else if (filter === 'photos') {
        filteredDuplicates = currentDuplicates.filter(dup =>
            isImageFile(dup.path)
        );
    } else if (filter === 'videos') {
        filteredDuplicates = currentDuplicates.filter(dup =>
            isVideoFile(dup.path)
        );
    }

    renderDuplicates();
}

function isImageFile(path) {
    const ext = path.split('.').pop().toLowerCase();
    return ['jpg', 'jpeg', 'png', 'gif', 'heic', 'heif', 'webp', 'bmp'].includes(ext);
}

function isVideoFile(path) {
    const ext = path.split('.').pop().toLowerCase();
    return ['mp4', 'mov', 'avi', 'mkv', 'webm', 'm4v'].includes(ext);
}

// ==================== RENDER DUPLICATES ====================

function renderDuplicates() {
    const gridDiv = document.getElementById('duplicates-grid');
    gridDiv.innerHTML = '';

    if (filteredDuplicates.length === 0) {
        gridDiv.innerHTML = `<p class="text-center">No ${currentFilter === 'all' ? '' : currentFilter} duplicates found.</p>`;
        return;
    }

    filteredDuplicates.forEach(dup => {
        const card = createDuplicateCard(dup);
        gridDiv.appendChild(card);
    });
}

function createDuplicateCard(dup) {
    const card = document.createElement('div');
    card.className = 'media-card duplicate-card';

    // Click handler to open comparison
    card.onclick = () => openComparisonModal(dup);

    // Thumbnail
    const img = document.createElement('img');
    img.className = 'media-thumbnail loading';
    img.alt = getFilename(dup.path);

    const thumbUrl = `${API_BASE}/api/thumb?path=${encodeURIComponent(dup.path)}`;
    img.onload = () => img.classList.remove('loading');
    img.onerror = () => {
        // Fallback to file icon if thumbnail fails
        img.src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y="50" font-size="40">📄</text></svg>';
        img.classList.remove('loading');
    };
    img.src = thumbUrl;

    card.appendChild(img);

    // Info overlay
    const info = document.createElement('div');
    info.className = 'duplicate-info';

    const filename = document.createElement('div');
    filename.className = 'duplicate-filename';
    filename.textContent = getFilename(dup.path);
    filename.title = getFilename(dup.path);
    info.appendChild(filename);

    const badge = document.createElement('div');
    badge.className = 'duplicate-badge';
    badge.textContent = 'DUPLICATE';
    info.appendChild(badge);

    const size = document.createElement('div');
    size.className = 'duplicate-size';
    size.textContent = formatBytes(dup.size);
    info.appendChild(size);

    card.appendChild(info);

    return card;
}

function getFilename(path) {
    return path.split('/').pop();
}

// ==================== SIDE-BY-SIDE COMPARISON ====================

function openComparisonModal(dup) {
    currentComparisonData = dup;

    const modal = document.getElementById('modal-comparison');

    // Set paths
    document.getElementById('comparison-original-path').textContent = dup.duplicate_of;
    document.getElementById('comparison-duplicate-path').textContent = dup.path;

    // Set sizes
    document.getElementById('comparison-original-size').textContent = formatBytes(dup.size);
    document.getElementById('comparison-duplicate-size').textContent = formatBytes(dup.size);

    // Check file types
    const isVideo = isVideoFile(dup.path);

    // Original file
    const originalImg = document.getElementById('comparison-original-img');
    const originalVideo = document.getElementById('comparison-original-video');

    if (isVideo) {
        originalImg.style.display = 'none';
        originalVideo.style.display = 'block';
        originalVideo.src = `${API_BASE}/api/file?path=${encodeURIComponent(dup.duplicate_of)}`;
    } else {
        originalImg.style.display = 'block';
        originalVideo.style.display = 'none';
        originalImg.src = `${API_BASE}/api/file?path=${encodeURIComponent(dup.duplicate_of)}`;
    }

    // Duplicate file
    const duplicateImg = document.getElementById('comparison-duplicate-img');
    const duplicateVideo = document.getElementById('comparison-duplicate-video');

    if (isVideo) {
        duplicateImg.style.display = 'none';
        duplicateVideo.style.display = 'block';
        duplicateVideo.src = `${API_BASE}/api/file?path=${encodeURIComponent(dup.path)}`;
    } else {
        duplicateImg.style.display = 'block';
        duplicateVideo.style.display = 'none';
        duplicateImg.src = `${API_BASE}/api/file?path=${encodeURIComponent(dup.path)}`;
    }

    modal.classList.add('active');
}

function closeComparisonModal() {
    const modal = document.getElementById('modal-comparison');
    modal.classList.remove('active');

    // Stop videos if playing
    document.getElementById('comparison-original-video').pause();
    document.getElementById('comparison-duplicate-video').pause();

    currentComparisonData = null;
}

async function confirmDelete() {
    if (!currentComparisonData) return;

    const btnDelete = document.getElementById('btn-confirm-delete');
    btnDelete.disabled = true;
    btnDelete.textContent = 'Deleting...';

    try {
        const response = await fetch(`${API_BASE}/api/duplicates/delete_single?file_path=${encodeURIComponent(currentComparisonData.path)}`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Delete failed');
        }

        const data = await response.json();

        showToast('Deleted', 'Duplicate moved to RAW_DUPLICATES', 'success');

        // Remove from current duplicates
        currentDuplicates = currentDuplicates.filter(d => d.path !== currentComparisonData.path);
        filteredDuplicates = filteredDuplicates.filter(d => d.path !== currentComparisonData.path);

        // Update status
        document.getElementById('status-found').textContent = currentDuplicates.length;

        // Re-render grid
        renderDuplicates();

        // Close modal
        closeComparisonModal();

    } catch (error) {
        console.error('Error deleting duplicate:', error);
        showToast('Delete Failed', error.message, 'error');
    } finally {
        btnDelete.disabled = false;
        btnDelete.textContent = 'Delete Duplicate';
    }
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 10) / 10 + ' ' + sizes[i];
}

// ==================== REMOVE ALL DUPLICATES ====================

async function removeAllDuplicates() {
    if (filteredDuplicates.length === 0) {
        showToast('No Duplicates', 'No duplicates to remove', 'info');
        return;
    }

    const count = filteredDuplicates.length;
    const filterText = currentFilter === 'all' ? 'all' : currentFilter;

    if (!confirm(`Are you sure you want to remove ${count} ${filterText} duplicate(s)?\n\nThey will be moved to RAW_DUPLICATES, not permanently deleted.`)) {
        return;
    }

    const btnRemoveAll = document.getElementById('btn-remove-all');
    const originalText = btnRemoveAll.textContent;
    btnRemoveAll.disabled = true;
    btnRemoveAll.textContent = 'Removing...';

    let successCount = 0;
    let failCount = 0;

    try {
        // Process all duplicates
        for (let i = 0; i < filteredDuplicates.length; i++) {
            const dup = filteredDuplicates[i];
            btnRemoveAll.textContent = `Removing... ${i + 1}/${count}`;

            try {
                const response = await fetch(`${API_BASE}/api/duplicates/delete_single?file_path=${encodeURIComponent(dup.path)}`, {
                    method: 'POST'
                });

                if (response.ok) {
                    successCount++;
                    // Remove from current duplicates
                    currentDuplicates = currentDuplicates.filter(d => d.path !== dup.path);
                } else {
                    failCount++;
                    console.error(`Failed to delete ${dup.path}`);
                }
            } catch (error) {
                failCount++;
                console.error(`Error deleting ${dup.path}:`, error);
            }
        }

        // Update status
        document.getElementById('status-found').textContent = currentDuplicates.length;

        // Re-filter and render
        filterDuplicates(currentFilter);

        // Show result
        if (failCount === 0) {
            showToast('Success', `Removed ${successCount} duplicate(s)`, 'success');
        } else {
            showToast('Partially Complete', `Removed ${successCount}, failed ${failCount}`, 'warning');
        }

    } catch (error) {
        console.error('Error in remove all:', error);
        showToast('Error', 'Failed to remove duplicates', 'error');
    } finally {
        btnRemoveAll.disabled = false;
        btnRemoveAll.textContent = originalText;
    }
}
