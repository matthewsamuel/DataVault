/**
 * Resizable Sidebar
 * Allows dragging the sidebar edge to resize
 */

class ResizableSidebar {
    constructor() {
        this.sidebar = document.querySelector('.sidebar');
        this.resizeHandle = null;
        this.isResizing = false;
        this.startX = 0;
        this.startWidth = 0;
        this.minWidth = 180;
        this.maxWidth = 500;

        if (this.sidebar) {
            this.init();
        }
    }

    init() {
        // Create resize handle
        this.createResizeHandle();

        // Bind events
        this.resizeHandle.addEventListener('mousedown', (e) => this.startResize(e));
        document.addEventListener('mousemove', (e) => this.doResize(e));
        document.addEventListener('mouseup', () => this.stopResize());
    }

    createResizeHandle() {
        this.resizeHandle = document.createElement('div');
        this.resizeHandle.className = 'sidebar-resize-handle';
        this.resizeHandle.style.position = 'absolute';
        this.resizeHandle.style.top = '0';
        this.resizeHandle.style.right = '0';
        this.resizeHandle.style.width = '4px';
        this.resizeHandle.style.height = '100%';
        this.resizeHandle.style.cursor = 'ew-resize';
        this.resizeHandle.style.zIndex = '100';
        this.resizeHandle.style.transition = 'background-color 0.2s';

        // Hover effect
        this.resizeHandle.addEventListener('mouseenter', () => {
            this.resizeHandle.style.backgroundColor = 'var(--accent)';
        });

        this.resizeHandle.addEventListener('mouseleave', () => {
            if (!this.isResizing) {
                this.resizeHandle.style.backgroundColor = 'transparent';
            }
        });

        this.sidebar.appendChild(this.resizeHandle);
    }

    startResize(e) {
        this.isResizing = true;
        this.startX = e.clientX;
        this.startWidth = this.sidebar.offsetWidth;

        this.resizeHandle.style.backgroundColor = 'var(--accent)';
        document.body.style.cursor = 'ew-resize';
        document.body.style.userSelect = 'none';

        e.preventDefault();
    }

    doResize(e) {
        if (!this.isResizing) return;

        const delta = e.clientX - this.startX;
        let newWidth = this.startWidth + delta;

        // Clamp to min/max
        newWidth = Math.max(this.minWidth, Math.min(this.maxWidth, newWidth));

        this.sidebar.style.width = `${newWidth}px`;

        // Store in localStorage
        localStorage.setItem('sidebarWidth', newWidth);
    }

    stopResize() {
        if (!this.isResizing) return;

        this.isResizing = false;
        this.resizeHandle.style.backgroundColor = 'transparent';
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
    }

    // Restore saved width
    restoreWidth() {
        const savedWidth = localStorage.getItem('sidebarWidth');
        if (savedWidth) {
            this.sidebar.style.width = `${savedWidth}px`;
        }
    }
}

// Initialize resizable sidebar
document.addEventListener('DOMContentLoaded', () => {
    const resizableSidebar = new ResizableSidebar();
    resizableSidebar.restoreWidth();
});
