# DataVault Dashboard

## A Comprehensive Personal Media Management System

DataVault Dashboard is a full-featured web application for organizing, analyzing, and curating large personal media archives. It combines intelligent file deduplication, chronological browsing, AI-powered assistance, and gamified curation into a unified platform that handles thousands of photos and videos across multiple formats and storage locations.

**Built with:** FastAPI (Python), Vanilla JavaScript, SQLite, Pillow, ffmpeg, Ollama  
**Status:** Production-ready  
**Repository:** https://github.com/matthewsamuel/DataVault

---

## 🎯 Quick Overview

### Core Functionalities

#### Media Organization & Discovery
- **Chronological Timeline Browser** — Browse entire archive by creation date with virtual scrolling, year-based indexing, and efficient multi-folder temporal views
- **Smart Folder Navigation** — Hierarchical folder tree with media counts, folder enable/disable settings, and recursive depth traversal
- **Multi-View Display** — Grid view (thumbnail-based), list view (sortable by name/date/size), and fullscreen modal viewer with zoom and pan controls

#### Duplicate Detection & Management
- **Content-Addressed Deduplication** — Find exact duplicates using xxHash3-128 hashing (size pre-filter first) with a cache that is only trusted while the file is provably unchanged
- **You Choose What Stays** — A fixed, deterministic rule per group (oldest by default; newest, shortest path or a preferred folder), switchable instantly in the UI, plus a per-group pick of the copy to keep
- **Safe Removal** — Move duplicates to RAW_DUPLICATES preserving directory structure, only after a byte-for-byte compare with the kept copy; one undo run per cleanup action, no metadata loss
- **Folder-Level Scanning** — Scan any folder for duplicates within that folder (and its subfolders); read errors are reported, never silently skipped

#### Gamified Photo Curation (Foto Keurer)
- **Swipe-Based Interface** — Four-directional gesture controls (left/right/up/down) + keyboard shortcuts for intuitive rapid rating
- **Streak Motivation System** — Daily 20-photo goal with consecutive day tracking and persistent progress storage
- **Smart Undo History** — Reverse up to 20 actions with automatic file restoration from trash or relocation
- **Keyboard-First Design** — Support for WASD, arrow keys, spacebar preview, and mouse gestures

#### Media Analytics & Insights
- **Spotify Integration** — Parse streaming history (both Extended and Standard formats) with artist/track aggregation, hourly/monthly activity patterns, binge tracking, and skip detection
- **Per-Year Statistics** — Filter and analyze listening habits by year with available year detection
- **Listening Trends** — Total play time, skip ratios, top artists, platform usage distribution

#### Intelligent Slideshows ("Shows")
- **Weighted Random Sampling** — ELO-style rating system (baseline 1000) for intelligent photo presentation
- **Probability-Based Selection** — Higher-rated photos appear more frequently, lower-rated less often
- **Multi-Folder Slideshow** — Create slideshows from any combination of folders with folder visibility controls

#### AI-Powered Chat Assistant
- **Local LLM Integration** — Privacy-first design using Ollama (no cloud data transmission)
- **Rich Context Injection** — Assistant has access to live vault data (folder structure, disk usage, top Spotify artists, listening time)
- **Multi-Modal Interaction** — Text chat + action tags (open folders, show photos by date, switch tabs, scan duplicates)
- **Streaming Responses** — Real-time response streaming for perceived low latency

#### Advanced Media Handling
- **Format Support** — Photos: JPG, PNG, HEIF, TIFF, WebP, GIF, BMP, RAW, CR2, NEF, ARW, DNG | Videos: MP4, MOV, M4V, AVI, MKV, WMV, FLV, WebM, 3GP, MPG, MPEG, MTS, M2TS | Audio: MP3, WAV, FLAC, M4A, AAC, OGG, WMA
- **Thumbnail Generation** — JPEG thumbnails (300x300, quality 85) cached with mtime validation; video frame extraction via ffmpeg
- **Metadata Preservation** — Atomic file operations (os.replace) preserve timestamps, EXIF, IPTC, extended attributes

#### Performance & Reliability
- **Background Prefetch** — Smart multi-stage prefetching (Spotify all-time → shows → per-year data) with promise-based caching
- **Background Jobs** — Duplicate scans, hashing and thumbnail pre-generation run as jobs in a worker thread (`backend/jobs.py`, `/api/jobs`): progress with ETA, cancel, pause and resume, persisted in SQLite so they survive a reload; a job left running by a stopped server comes back as `interrupted`, and one whose vault is unplugged waits for it and continues
- **Never Blocking the Server** — Every route that touches the disk, a subprocess or SQLite is a plain `def` (threadpool); only the chat routes are `async` (they await Ollama). SQLite runs in WAL mode with one connection per thread and a busy timeout
- **Graceful Error Handling** — Corrupt files skipped silently, cross-device moves detected, partial failures reported with details
- **Security Hardening** — No CORS, trusted hosts only (127.0.0.1/localhost), a per-process session token on every state-changing request, one central path check (`resolve_in_vault`) and one exclusion rule for every walk over the archive

---

## 🏗️ System Architecture

### Backend Structure (FastAPI + Python)

```
backend/
├── main.py                    # FastAPI initialization, middleware, route registration, health/restart
├── security.py                # Trusted hosts, session token (GET /api/session) and its middleware
├── paths.py                   # resolve_in_vault() for request paths; ArchiveFilter/walk_archive() exclusions
├── restart.py                 # Restart of this server process only (helper waits for the PID to exit)
├── config.py                  # Configuration loader, media type definitions
├── database.py                # SQLite abstraction (caching, ratings, decisions, undo tracking)
├── scanner.py                 # File discovery, xxHash3 hashing, per-folder duplicate detection
├── file_ops.py                # Safe atomic file operations with metadata preservation
├── thumbnail.py               # Thumbnail generation (PIL) with cache validation, atomic writes
├── jobs.py                    # JobManager: background jobs with progress, cancel, pause, resume, vault wait
├── job_types.py               # The job types: dup_scan, hashing, thumbnails
│
└── routes/
    ├── folders.py             # Folder tree enumeration, recursive traversal
    ├── media.py               # Individual file operations, thumbnail serving, file downloads
    ├── duplicates.py          # Duplicate scan (as a job), verified batch moves, undo per run
    ├── jobs.py                # /api/jobs: start, list, get, result, cancel, pause, resume
    ├── spotify.py             # Spotify JSON parsing, stats aggregation, year filtering
    ├── shows.py               # Slideshow generation, weighted sampling, ELO ratings
    ├── chat.py                # LLM chat with Ollama, context injection, action tags, streaming
    ├── keurer.py              # Photo curation endpoints, streak calculation, undo support
    ├── stats.py               # Statistics aggregation, general analytics
    └── timeline.py            # Chronological media browsing, date indexing, virtual scrolling
```

**Key Architectural Patterns:**
- **Modular Route-Based Organization:** Each feature is a self-contained router with init/endpoint separation
- **Dependency Injection via init_router():** Routes receive shared config and database instances
- **Stateless API Design:** Routes don't maintain state; all data persists in SQLite or filesystem
- **Streaming Responses:** Chat endpoint uses SSE (Server-Sent Events) for real-time streaming
- **Static Mount Last:** Frontend served via StaticFiles mounted after all API routes to prevent interception

### Frontend Structure (Vanilla JavaScript)

```
frontend/
├── index.html                 # 6 main sections: home, viewer, duplicates, spotify, shows, keurer, timeline
├── api.js                     # Adds the session token to every write (loaded first)
├── dom.js                     # Safe DOM helpers: createEl, html`...`, setHTML (loaded second)
├── app.js                     # Core state management, view switching, prefetch caching
├── style.css                  # Responsive design (Flexbox/Grid), theme system, animations
├── keurer.js                  # Photo curator UI, swipe detection, undo history
├── duplicates.js              # Duplicate groups, keep rules, cleanup dialog, Herstellen (undo)
├── timeline.js                # Timeline UI, virtual scrolling, slider navigation
├── wave-background.js         # Animated background effect
├── resizable-sidebar.js       # Interactive folder tree sidebar
└── vendor/                    # Font Awesome 6.4.0 and JetBrains Mono, with licenses (no CDN)
```

**Frontend Techniques:**
- **No External Frameworks:** Vanilla JS reduces bundle size (<50KB vs. 500KB+ with framework)
- **Virtual Scrolling:** Efficient rendering of large datasets (10,000+ items)
- **Intersection Observer:** Lazy thumbnail loading on scroll
- **Promise-Based Prefetching:** Background data loading with smart prioritization
- **CSS Variables & Media Queries:** Responsive design with automatic dark mode
- **State-Driven Rendering:** Global state → API fetch → DOM update pattern

### Data Flow Architecture

```
User Action (UI)
    ↓
JavaScript Handler (DOM, event listeners)
    ↓
Fetch API Call (HTTP POST/GET)
    ↓
FastAPI Route (validation, authorization)
    ↓
Business Logic (scanner, file_ops, database queries)
    ↓
SQLite or Filesystem Operation
    ↓
Response (JSON or streaming data)
    ↓
UI Update (render, toast notification)
```

---

## 💾 Core Features & Technical Deep Dive

### 1. DUPLICATE DETECTION

**Problem:** Large vaults accumulate identical files across folders. Manual identification is impossible; naive approaches are too slow.

**Solution:**
- **Per-Folder Scan as a background job** (`POST /api/jobs {type: "dup_scan", params: {folder, rule, prefer_folder}}`,
  `scanner.find_duplicates_in_folder`); `POST /api/duplicates/scan_folder?folder_path=&rule=&prefer_folder=` runs
  the same job and waits for it (for scripts):
  1. Walk the chosen folder in sorted order with the archive's one exclusion rule (no cache, TRASH,
     RAW_DUPLICATES, hidden or `._` files, symlinks) and group the non-empty media files by size; per file only
     the path string and a small fingerprint tuple are kept
  2. Hash only the files that share a size with xxHash3-128 on 4 threads, submitted in a bounded window
     (`jobs.bounded_map`: never tens of thousands of futures at once); new hashes go to the cache in batches
     of 2000 (or every 10 s), so a stopped or interrupted scan resumes from the cache
  3. Group by hash and pick the copy that stays with `scanner.choose_keeper(rule)`
  4. Answer with groups: `{id, hash, size, type, copies: [...], keep}`; each copy carries `capture_ts`
     (EXIF DateTimeOriginal, else birthtime, else mtime), `file_ts`, `hidden`, `path_len` and `name_rank`
- **Which copy stays:** every rule first prefers a copy that is not hidden (dot name or the Finder
  'hidden' flag) and ends with the path in alphabetical order, so the result is fully deterministic.
  `oldest` (default): oldest capture date, then oldest file date, then shortest path. `newest`: the reverse
  dates. `shortest`: shortest path, then oldest. `folder`: a copy anywhere below `prefer_folder`, then oldest
  (the UI offers every folder holding a copy plus its parent folders up to the scanned folder).
  Identical bytes mean identical EXIF, so within a group the file date tells the copies apart.
  The UI (`keepSortKey` in duplicates.js) uses the same keys, so switching rules is instant, and the user can
  pick another copy per group.
- **Honest results:** unreadable folders and files are counted (`errors`, `error_samples`, `complete: false`);
  a folder that disappears mid-scan fails the job, and an unplugged vault makes it wait and run again, so a
  scan that missed files because of the disk is never `done`. The result is stored (`job_results`) and served
  again after a reload, leaving out copies that were moved since. One scan runs at a time; a second one waits
  in the queue (`scan_folder` answers 409 instead).
- **Smart Caching:** `file_hashes` rows carry their algorithm (`algo`) and are only used while the file's
  inode, size, mtime_ns and ctime_ns are unchanged (ctime cannot be set back, so an in-place edit that
  restores the mtime is still noticed). Rows from before migration 6 are simply rehashed. The cache is read in
  one query and written in one transaction per scan, and rows of files that are gone are pruned.
- **Verified Removal** (`POST /api/duplicates/move`, body `{groups: [{keep, move: [...]}], run_id?, folder?}`):
  for every file, right before moving and under `ARCHIVE_LOCK`, the server checks that it is a regular file
  outside RAW_DUPLICATES/TRASH/cache, that `keep` still exists inside the vault, and that both are
  **byte-for-byte identical** (`file_ops.files_identical`, no cached hash), and that `keep` is not hidden
  (dot name below the vault root or UF_HIDDEN) while the file to move is visible. Only then is the file moved to
  RAW_DUPLICATES with its folder structure, via `move_no_overwrite`. Refusals are listed per file in `failed`.
  So the last copy of a photo can never be moved away, even if the folder changed since the scan.
  `POST /api/duplicates/delete_single` does the same for one file.
- **Undo per action:** a run is created in the same transaction as its first moved file (so there are no empty
  runs); the UI sends one action in chunks of about 100 files with the returned `run_id`. Runs are ordered by an
  AUTOINCREMENT `seq`, never by clock time. `GET /api/duplicates/runs` lists recent runs (with how many files are
  still in RAW_DUPLICATES), `POST /api/duplicates/undo {run_id}` restores one run, and
  `POST /api/raw_duplicates/undo_last` restores the newest run that still has a file in RAW_DUPLICATES.
  A file that took the original place is never overwritten: the restored file then gets
  `name_RESTORED_001.ext`. Only restored files are forgotten; the rest stays recorded for a later attempt.
  The database lives on the vault disk: while it is unplugged, every database error answers 503
  "Archief niet bereikbaar (schijf losgekoppeld?)" (app-wide handler in `main.py`) instead of a 500.

**Code Example:**
```python
# duplicates._move_verified, shortened (the caller holds ARCHIVE_LOCK and records the move)
if not files_identical(src_path, keeper):
    raise HTTPException(409, "File is no longer identical to the kept copy; scan again")
dst_path, was_conflict = safe_move_with_structure(real_src, base_folder, config.DUPLICATES_FOLDER.resolve())
```

**Algorithm:** xxHash3-128 to find candidates quickly; a full byte compare decides before anything moves.

---

### 2. CHRONOLOGICAL TIMELINE

**Problem:** Thousands of photos spanning decades; users need intuitive temporal exploration without loading everything.

**Solution:**
- **Date Extraction:** Uses macOS st_birthtime (file creation date) with mtime fallback
- **Recursive Folder Scanning:** Efficient traversal excluding system folders
- **Date-Based Indexing:** Year → Month → Count structure for O(1) year jumping
- **Year Boundaries:** Precomputed indices of first photo per year
- **Virtual Scrolling:** Render only visible items (20-50 thumbnails at a time)
- **Multi-Folder Support:** Users select 1+ folders; results merged chronologically
- **Slider Navigation:** Year labels + real-time tooltip showing date range

**Performance:** 10,000+ photo timelines scroll smoothly

**Code Example:**
```python
# Build date index for quick navigation
date_index = defaultdict(lambda: defaultdict(int))
for f in all_files:
    dt = datetime.fromtimestamp(f["timestamp"])
    date_index[dt.year][dt.month] += 1

# Compute year boundaries for slider jumping
year_boundaries = {}
for i, f in enumerate(all_files):
    year = datetime.fromtimestamp(f["timestamp"]).year
    if year not in year_boundaries:
        year_boundaries[year] = i  # Index of first photo in year
```

---

### 3. GAMIFIED CURATION (Foto Keurer)

**Problem:** Manually rating large photo libraries is tedious; users need motivation.

**Solution:**
- **Four-Directional Gestures:** 
  - LEFT (A-key) = Keep
  - RIGHT (D-key) = Delete
  - UP (W-key) = Move to folder
  - DOWN (S-key) = Undo
- **Daily Streak System:** Requires 20+ ratings per day; tracks consecutive days
- **Progress Tracking:** Real-time counter (today's count / 20) + lifetime total
- **Undo History Stack:** Stores last 20 actions with trash_path and moved_to locations
- **Database Recording:** Timestamps decisions for streak recalculation

**Streak Algorithm:**
```python
streak = 0
check_date = today

# If today's goal not met, start counting from yesterday
if daily_count[today] < 20:
    check_date = today - 1

# Count consecutive days with 20+ decisions
while daily_count[check_date] >= 20:
    streak += 1
    check_date -= 1
```

**Gesture Detection:**
```javascript
// Calculate swipe distance and angle
const deltaX = endX - startX;
const deltaY = endY - startY;
const distance = Math.sqrt(deltaX*deltaX + deltaY*deltaY);
const angle = Math.atan2(deltaY, deltaX);

// Threshold: minimum 50px swipe distance
if (distance > 50) {
    if (angle > -0.75 && angle < 0.75) {
        decide('delete');      // →
    } else if (angle > 0.75 && angle < 2.4) {
        decide('move');        // ↑
    } // ... more angles
}
```

---

### 4. SPOTIFY ANALYTICS

**Problem:** Spotify provides raw history but limited insights. Understanding personal habits requires custom aggregation.

**Solution:**
- **Multi-Format Parser:** Handles both Extended (Streaming_History_Audio_*.json) and Standard (StreamingHistory*.json) formats
- **Robust Error Handling:** Graceful fallback for missing fields; skips glitches (<1s plays)
- **Rich Aggregation:**
  - Artists: cumulative time, play count, tracked first occurrence
  - Tracks: ms_played, artist, name
  - Hourly distribution (0-23)
  - Monthly distribution (1-12)
  - Platform usage
  - Binge tracking (max consecutive plays)
  - Skip/completion detection (< or > 30s as threshold)
  - "On This Day" feature (same month/day in previous years)
- **Year Filtering:** Detect available years; filter stats by year

**Performance:** Millions of play records aggregated in <1 second

---

### 5. SLIDESHOW SYSTEM (Shows)

**Problem:** Static browsing is passive. Randomized presentation with weighted recommendations improves engagement.

**Solution:**
- **ELO-Style Ratings:** Each file has rating (baseline 1000); updated via user votes
- **Weighted Random Sampling:** Probability proportional to rating
- **Probability Calculation:**
  ```python
  weights = [rating - min_rating + 1 for rating in all_ratings]
  probabilities = [w / sum(weights) for w in weights]
  sampled = random.choices(files, weights=probabilities, k=20)
  ```
- **Slideshow Controls:** Auto-advance (configurable interval), manual prev/next, pause/resume
- **Folder Filtering:** Exclude folders via enable/disable settings stored in database

---

### 6. LLM-POWERED CHAT

**Problem:** Users want conversational help, but cloud AI raises privacy concerns.

**Solution:**
- **Local Ollama Integration:** Privacy-first; no cloud data transmission
- **Dynamic Context Injection:**
  ```
  Current date/time
  Available folders (top 25)
  Disk usage (used, free, total)
  Top Spotify artists + total listening time
  ```
- **Multi-Modal Interaction:** Chat text + action tags
  - `open_folder`, `show_photos`, `search_photos`, `open_file`, `open_tab`, `scan_duplicates`
  - Model output is untrusted: it is shown with `textContent`, and an action runs only if it is
    on an allowlist with typed arguments (`open_tab` only for a known view, `show_photos` only
    for a real `YYYY-MM-DD` date). `open_file` opens a file only if the server lists it in its
    folder (`/api/media`, so inside the vault); `open_folder` only opens a folder from the
    server's folder tree.
  - Folder names and Spotify artists go into the system prompt as a JSON list without `<`, `>`
    or control characters (`chat.as_prompt_data`), and the prompt says they are data, not
    instructions.
- **Streaming Responses:** SSE (Server-Sent Events) for real-time chat
- **Think Block Filtering:** Removes `<think>` tags from reasoning models

**System Prompt Example:**
```
You are Pasha, DataVault's personal AI assistant.
Answer briefly in Dutch. Be helpful.

=== LIVE VAULT DATA ===
Today: April 1, 2026
Folders: RAW, Archive, 2020, 2021, ...
Disk: 850GB used, 150GB free (1TB total)
Spotify: Top artists: Taylor Swift, The Weeknd, ...
Listening time: 45,000 minutes
```

---

### 7. MEDIA VIEWER & THUMBNAIL SYSTEM

**Problem:** Browsing 1000s of images requires fast, efficient thumbnail loading and rendering.

**Solution:**
- **Lazy Thumbnail Generation:** JPEG thumbnails (300x300, quality 85) generated on first access
- **Cache Validation:** Thumbnails valid if mtime > original file mtime; auto-regenerated if file changes
- **Format Conversion:** Pillow handles HEIF (with pillow-heif), RAW, JPEG, PNG, etc.; converts to RGB as needed
- **Video Frame Extraction:** ffmpeg extracts frame at 1s mark (or first frame if shorter)
- **Concurrent Processing:** ThreadPoolExecutor for multi-core hash/thumbnail operations

**Performance:**
- Thumbnail generation: 50-100ms per image
- Cache hit: <10ms per thumbnail
- Video frame extraction: 100-300ms per video

---

## 🛠️ Technical Achievements & Design Patterns

### 1. Safe File Operations (Metadata Preservation)

**Key Insight:** Standard approaches (copy → delete) lose metadata, and a plain `os.rename()` silently overwrites an existing file. Solution: every move goes through one helper, `file_ops.move_no_overwrite()`.

**Benefits:**
- Single atomic, *exclusive* rename (`renamex_np(RENAME_EXCL)` on macOS, `os.link` + unlink elsewhere): an existing file is never replaced, a clash becomes `name_DUP_001.ext`
- Same inode, so mtime, birthtime, EXIF, extended attributes and Finder locks are preserved
- Refuses cross-volume moves, directories and symlinks instead of falling back to copy + delete

```python
final_path = move_no_overwrite(src, dst)                  # picks name_DUP_001.ext on a clash
move_no_overwrite(trash_file, original, rename_on_conflict=False)  # undo: 409 if taken
```

### 2. Smart Caching with Validity Tracking

**Problem:** Re-hashing on every scan is slow; naive caching ignores changes.

**Solution:** Cache with stat-based invalidation (size + mtime).

**Impact:** 90-95% cache hit rate on typical vaults

### 3. Virtual Scrolling for Large Datasets

**Pattern:** Render only visible items; update range on scroll.

**Enables:** Smooth browsing of 10,000+ photo timelines without DOM bloat

### 4. Background Prefetch with Promise Coordination

**Pattern:** Start expensive API calls in background; `await` when needed.

**Benefits:**
- Reduces perceived latency
- Prioritizes based on usage patterns (Spotify all-time → shows → per-year)
- Progressive enhancement: app responds immediately; data loads later

### 5. Transactional Undo System

**Pattern:** Record all operations in database; rollback by reversing each operation.

**Enables:** Undo for duplicates, keurer decisions, file moves

### 6. HTTP Security, Path Validation & Exclusions

**HTTP layer** (`backend/security.py`): the frontend is served same-origin, so there is no CORS
middleware at all; no other website can read an API response. `TrustedHostMiddleware` only
answers `Host: 127.0.0.1` or `localhost` (DNS rebinding gets 400). Every request with a method
other than GET/HEAD/OPTIONS must carry `X-DataVault-Token`, a secret generated per server
process and served by `GET /api/session` (cross-site `Sec-Fetch-Site` is refused).
`frontend/api.js`, loaded before every other script, wraps `fetch`: it adds the token to
same-origin writes and, after a server restart (403 with `X-DataVault-Auth: token`), fetches the
new token and retries once. GET endpoints have no side effects except `/api/thumb`, which fills
the app's own thumbnail cache.

**Paths** (`backend/paths.py`): every route that takes a path or folder calls
`resolve_in_vault(user_path, config)`. Relative input is taken from the vault root; the on-disk
spelling wins, else the NFC/NFD form; after resolving `..` and symlinks both the entry and its
target must lie inside `VAULT_ROOT`; the cache, the database and (if it lives in the vault) the
app folder are refused. Outside paths always get the same 403, checked before any existence
test, so the answer reveals nothing about the disk outside the vault. A file keeps its last
component unresolved (so a route can still refuse a symlink); a folder (`folder=True`) is fully
resolved, so stored paths use one spelling.

```python
folder_path = resolve_in_vault(folder, config, folder=True)   # 400 non-string, 403 outside/internal
if not folder_path.is_dir():
    raise HTTPException(status_code=404, detail="Folder not found")
```

**Rendering** (`frontend/dom.js`): file and folder names, paths, EXIF values, Spotify titles and
chat output are data, never HTML (`xss <img src=x onerror=...>.jpg` is a valid file name). The
frontend builds HTML only with `createEl` (text nodes, `addEventListener`), the ``html`...` ``
template tag (escapes every interpolated value) or `setHTML` (accepts only that markup), and no inline
handler carries data. `tests/test_frontend_safety.py` fails on any other `innerHTML` use.

**Offline**: Font Awesome and the JetBrains Mono font are served from `frontend/vendor/`; the
frontend contacts no other host. `tests/test_frontend_offline.py` fails on any external URL in
the frontend (the allowlist is at the top of that file; only click-to-open Spotify search links
are listed separately).

**Exclusions**: every walk over the archive (folder tree, viewer, timeline, stats, Shows,
duplicates scan, Keurer, chat context, Spotify files) uses `ArchiveFilter` / `walk_archive()`,
a drop-in for `os.walk`. It skips the cache, TRASH, RAW_DUPLICATES and the app folder by device +
inode (not by name, so a user folder called `dashboard` stays visible), hidden files, `._`
AppleDouble files and symlinks.

**Restart** (`backend/restart.py`): `POST /api/restart` restarts only the process that received
it. A helper in its own session waits for this PID to exit, then execs the same interpreter with
the same arguments, working directory and environment. Nothing is killed by port. A second
restart request while one is pending gets 409 (`Herstart is al bezig`), so only one new server
ever starts. The restarted server outlives the Terminal window that started it, so it never
writes to that Terminal: output that went to a regular file (the DataVault app's
`/tmp/datavault_server.log`) stays there, anything else (a tty, a pipe) goes to
`~/Library/Logs/DataVault/server.log` (override with `DATAVAULT_LOG_FILE`; above 10 MB the old
log moves to `server.log.1`).
The old process stops with SIGTERM (uvicorn finishes open requests). If a request keeps it
alive, a SIGINT after 10 s makes uvicorn stop waiting (uvicorn ignores a second SIGTERM), and
after 15 s the process ends itself with `os._exit`, because a synchronous request (timeline scan,
largest-files walk, `du`) runs in a non-daemon worker thread the interpreter would wait for. That
last step first takes `ARCHIVE_LOCK`, so it never interrupts an archive move. The helper waits
up to 5 minutes for the old PID.

## 📊 Database Design

### SQLite Schema

```sql
-- Keurer decisions: track rated files per folder
CREATE TABLE keurer_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    folder TEXT NOT NULL,
    decision TEXT NOT NULL,  -- 'keep', 'delete', 'move'
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    dest_path TEXT,          -- where delete/move put the file (for server-side undo)
    dest_ino INTEGER,        -- inode + size of that file, checked before undo
    dest_size INTEGER
);
-- Only 'keep' is unique per path: delete/move rows always stay, so their undo is never lost
CREATE UNIQUE INDEX idx_keurer_keep_path_folder ON keurer_decisions(path, folder) WHERE decision = 'keep';
CREATE INDEX idx_keurer_folder ON keurer_decisions(folder);
CREATE INDEX idx_keurer_date ON keurer_decisions(decided_at);

-- Hash cache: avoid re-hashing unchanged files (migration 6)
CREATE TABLE file_hashes (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    hash TEXT NOT NULL,      -- was 'sha256'; holds the algo's hex digest
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    algo TEXT,               -- 'xxh3_128'; NULL = unknown, never used
    ino INTEGER,             -- the hash is only trusted while ino, size,
    mtime_ns INTEGER,        -- mtime_ns and ctime_ns are unchanged
    ctime_ns INTEGER
);

-- File ratings: ELO scores for weighted sampling
CREATE TABLE file_ratings (
    path TEXT PRIMARY KEY,
    rating INTEGER DEFAULT 1000,
    votes INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Undo tracking: one run per duplicates cleanup action (migration 7)
CREATE TABLE delete_runs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,  -- order of runs; never clock time
    run_id TEXT NOT NULL UNIQUE,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_files INTEGER DEFAULT 0,
    created_at REAL,                        -- UTC epoch seconds, for display
    folder TEXT                             -- the folder that was cleaned up
);

CREATE TABLE delete_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    src_path TEXT NOT NULL,
    dst_path TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES delete_runs(run_id)
);
CREATE INDEX idx_delops_run ON delete_operations(run_id);

-- Folder visibility settings
CREATE TABLE show_settings (
    path TEXT PRIMARY KEY,
    enabled BOOLEAN DEFAULT 1
);

-- Background jobs (migration 8): progress, checkpoint and a small summary per job
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,                 -- 'dup_scan', 'hashing', 'thumbnails'
    params TEXT NOT NULL DEFAULT '{}',  -- JSON
    status TEXT NOT NULL,               -- queued, running, paused, waiting_for_vault,
                                        -- cancelled, failed, done, interrupted
    processed INTEGER NOT NULL DEFAULT 0,
    total INTEGER,                      -- NULL while still counting
    message TEXT, error TEXT,
    checkpoint TEXT, summary TEXT,      -- JSON
    created_at REAL NOT NULL, updated_at REAL NOT NULL, started_at REAL, finished_at REAL
);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_type ON jobs(type, id);
-- The full result of a finished job, zlib-compressed JSON (kept for the newest few per type)
CREATE TABLE job_results (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    encoding TEXT NOT NULL,             -- 'json+zlib'
    data BLOB NOT NULL
);
```

The database runs in WAL mode (readers never wait for the writer) with `busy_timeout` 10 s and one
connection per thread; every method runs inside `with self._connect()`, which commits or rolls back, so a
failed call never leaves a transaction open. Before each use the database file is stat'ed: gone means
`OperationalError` (503 "Archief niet bereikbaar"), and a different file (the disk was mounted again) gets
a fresh connection.

---

## 🚀 Performance Optimizations

| Operation | Time | Notes |
|-----------|------|-------|
| Duplicate scan, ~94k files (synthetic, SSD) | 6.2 s cold, 1.4 s warm | Phase 1: 73.6 s / 13.8 s. Background job; `/api/health` stays under 30 ms meanwhile; peak RSS ~175-225 MB (was ~960 MB) |
| Thumbnail generation | 50-100ms | Per image with PIL |
| Thumbnail cache hit | <10ms | Validated by mtime |
| Video frame extraction | 100-300ms | Via ffmpeg |
| Timeline rendering (10k photos) | <100ms | Virtual scrolling |
| Spotify aggregation (millions) | <1s | defaultdict aggregation |
| Initial page load | <1s | Lazy loading, deferred data |

**Optimization Strategies:**
1. **Hash Caching:** Trusted only while inode, size, mtime_ns and ctime_ns are unchanged; read in one query, written in batches
2. **Background Prefetch:** Multi-stage prioritization, requestIdleCallback
3. **Concurrent Processing:** Bounded ThreadPoolExecutor windows inside background jobs; blocking routes run in the threadpool
4. **Lazy Rendering:** IntersectionObserver for thumbnail loading
5. **Virtual Scrolling:** Only render visible items
6. **Streaming Responses:** SSE for real-time chat
7. **Static Asset Caching:** Browser cache validation via mtime

---

## 🔧 Technologies & Skills Demonstrated

### Languages & Frameworks
- **Python 3.8+** — FastAPI, pathlib, asyncio, concurrent.futures
- **JavaScript (ES6)** — Vanilla JS (no frameworks), async/await, Fetch API
- **SQL** — SQLite3, schema design, indexing, transactions
- **HTML/CSS3** — Semantic markup, Flexbox/Grid, responsive design, CSS variables

### Libraries & Tools
- **FastAPI** — Modern async web framework with type hints
- **Pillow (PIL)** — Image processing, format conversion, thumbnail generation
- **ffmpeg** — Video processing, frame extraction
- **SQLite3** — Local database, schema management
- **xxHash3** — Fast hashing (10x vs. SHA-256)
- **Ollama** — Local LLM integration
- **TrustedHostMiddleware + session token** — Local-only HTTP layer (no CORS)
- **StaticFiles** — Frontend serving via FastAPI

### Design Patterns & Concepts
- **RESTful API Design** — Stateless routes, proper HTTP methods, streaming responses
- **Dependency Injection** — Modular route initialization without heavy frameworks
- **Transactional Operations** — ACID guarantees for file operations and undo
- **Caching Strategies** — Stat-based cache invalidation, prefetch prioritization
- **Virtual Scrolling** — Efficient rendering of large datasets
- **Gesture Detection** — Touch/mouse event handling with angle/distance thresholds
- **State Machine Pattern** — View switching, phase-based progress tracking
- **Content-Addressed Hashing** — Deduplication via xxHash3-128, confirmed by a byte compare
- **ELO Rating System** — Probability-weighted random sampling
- **Security Hardening** — Path validation, vault boundary enforcement
- **Error Handling** — Graceful degradation, cross-device detection, comprehensive logging

### System Design Aspects
- **Single-Page Application (SPA)** — Client-side routing, state management
- **Asynchronous Architecture** — Async FastAPI routes, streaming responses
- **Database Optimization** — Strategic indexing, denormalized aggregations
- **File System Operations** — Atomic moves, metadata preservation, safe deletion
- **Multi-Format Support** — Handling 40+ media file types
- **Privacy-First Design** — Local LLM (no cloud transmission)

---

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- SQLite3 (included with Python)
- Modern web browser
- ffmpeg (for video thumbnails)
- Ollama (optional, for chat feature)

### Installation

```bash
# Clone repository
git clone https://github.com/matthewsamuel/DataVault.git
cd DataVault

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create config.json with vault paths
cat > config.json << EOF
{
  "vault_root": "/path/to/vault",
  "raw_folder": "/path/to/vault/RAW",
  "cache_folder": "/path/to/cache",
  "duplicates_folder": "/path/to/vault/RAW_DUPLICATES"
}
EOF

# Start backend server
python -m uvicorn code.backend.main:app --reload --host 127.0.0.1 --port 8000

# Open browser
# http://localhost:8000
```

### Configuration

Create `config.json` in project root:
```json
{
  "vault_root": "/Volumes/T7",
  "raw_folder": "/Volumes/T7/RAW",
  "cache_folder": "/Volumes/T7/.datavault_cache",
  "duplicates_folder": "/Volumes/T7/RAW_DUPLICATES",
  "trash_folder": "/Volumes/T7/TRASH"
}
```

---

## 📁 Project Structure

```
DataVault/
├── code/
│   ├── backend/
│   │   ├── main.py                    # FastAPI app, route registration
│   │   ├── config.py                  # Configuration, path validation
│   │   ├── database.py                # SQLite abstraction
│   │   ├── scanner.py                 # File discovery, hashing
│   │   ├── file_ops.py                # Safe file operations
│   │   ├── thumbnail.py               # Thumbnail generation
│   │   ├── jobs.py                    # Background jobs (JobManager)
│   │   ├── job_types.py               # dup_scan, hashing, thumbnails
│   │   └── routes/                    # API endpoints
│   │       ├── folders.py
│   │       ├── media.py
│   │       ├── duplicates.py
│   │       ├── spotify.py
│   │       ├── shows.py
│   │       ├── chat.py
│   │       ├── keurer.py
│   │       ├── stats.py
│   │       └── timeline.py
│   │
│   └── frontend/
│       ├── index.html                 # Main template
│       ├── api.js                     # Session token for writes
│       ├── dom.js                     # Safe DOM helpers
│       ├── app.js                     # Core logic
│       ├── style.css                  # Styling
│       ├── keurer.js                  # Photo curator UI
│       ├── duplicates.js              # Duplicate browser
│       ├── timeline.js                # Timeline UI
│       ├── wave-background.js         # Background animation
│       ├── resizable-sidebar.js       # Sidebar interaction
│       └── vendor/                    # Font Awesome, JetBrains Mono (local copies)
│
├── README.md                          # This file
├── PORTFOLIO.md                       # Detailed technical documentation
├── requirements.txt                   # Python dependencies
├── config.json                        # Configuration (user-created)
└── .gitignore                         # Git configuration
```

---

## 📈 What This Project Demonstrates

### Software Engineering
- **Full-Stack Development:** Backend (Python, async), frontend (vanilla JS), database (SQLite)
- **API Design:** RESTful endpoints with proper HTTP semantics, streaming responses
- **Database Design:** Schema normalization, strategic indexing, transactional integrity
- **File System Operations:** Atomic moves, metadata preservation, cross-device detection
- **Security:** Path validation, vault boundary enforcement, graceful error handling

### Data Processing & Algorithms
- **Content-Addressed Hashing:** xxHash3-128 deduplication with smart caching and a byte compare before any move
- **Date Extraction & Indexing:** Temporal organization with year-based jumping
- **Probability-Based Sampling:** ELO rating system for weighted randomness
- **Recursive Traversal:** Efficient folder scanning with exclusion filters
- **Background Jobs:** Multi-phase scanning as resumable jobs with progress, ETA, cancel and pause

### Performance & Optimization
- **Cache Strategy:** Stat-based invalidation achieving 90%+ hit rates
- **Virtual Scrolling:** Smooth rendering of 10,000+ item lists
- **Background Prefetch:** Smart prioritization reducing perceived latency
- **Concurrent Processing:** Multi-core utilization via ThreadPoolExecutor
- **Format Support:** Handling 40+ media types with intelligent conversions

### UI/UX & Frontend Engineering
- **Gesture Detection:** Multi-directional swipe recognition with angle/distance thresholds
- **Responsive Design:** CSS Flexbox/Grid adapting to all screen sizes
- **State Management:** Unidirectional data flow without external frameworks
- **Lazy Loading:** IntersectionObserver for efficient image rendering
- **Accessibility:** Keyboard shortcuts, ARIA labels, semantic HTML

### AI/ML Integration
- **Local LLM:** Privacy-preserving Ollama integration
- **Context Injection:** Dynamic system prompts with live vault data
- **Multi-Modal Interaction:** Text + structured action tags
- **Streaming:** Real-time response handling via SSE

### Reliability & Maintenance
- **Error Handling:** Graceful degradation for missing external drives
- **Logging:** Comprehensive operation tracking
- **Testing:** Type hints throughout; error validation
- **Documentation:** Code comments, READMEs, portfolio documentation

---

## 📝 License

This project is available under the MIT License. See LICENSE file for details.

---

## 👤 Author

**Matthew Samuel**  
Full-stack developer passionate about building elegant, efficient tools for personal productivity.

- GitHub: https://github.com/matthewsamuel
- Portfolio: https://github.com/matthewsamuel/DataVault

---

**Last Updated:** April 2026  
**Status:** Production-Ready
