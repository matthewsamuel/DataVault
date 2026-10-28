"""
DataVault Dashboard - DataGatherer

Brings the user's own data (Spotify, Last.fm, later Meta, WhatsApp, Google, ...) up to date
and merges it with the exports already in the archive. One data source is one connector:
a single Python module in backend/datagatherer/connectors/ that describes itself (name,
icon, category, the hosts it may contact, its export page, file patterns, help steps) and
implements what it can do (api_sync, local_import, export_request, export_watch, import).
The DataGatherer tab builds itself from that description; there is no source-specific
frontend code. docs/DATAGATHERER_CONNECTORS.md explains how to add one.

The modules:

    base.py          Connector, its metadata types and the context a connector works with
    registry.py      finds and validates every connector
    keychain.py      tokens and API keys, only in the macOS Keychain (keyring)
    net.py           the network guard: allowed hosts only, only after a click or an
                     enabled schedule, every request logged in dg_network_log
    raw_store.py     raw exports: copied (never moved) into data/<owner>/<Bron>/<datum soort>/,
                     never overwritten, deduplicated by sha256; existing folders registered in place
    state.py         the dg_* tables (connector state, raw imports, requests, cursors, log)
    jobs.py          the background job types: sync, import and watch per connector
    daemon.py        the Downloads watcher and the opt-in schedules, one small thread
    oauth.py         OAuth Authorization Code + PKCE (state, S256) for API connectors
    launchd.py       the optional launchd agent (installed only on a click in Instellingen)
    cli.py           what that agent runs: a scheduled sync without the server
    spotify_store.py the merged Spotify plays (exports, Web API, Last.fm) and supersede rules

Hard rules (see the user's brief): DataVault never stores or types passwords and never
automates a browser; it only opens the right page in the user's own browser. Raw exports
are copied, never moved, modified, overwritten or deleted. Every importer is idempotent.
"""
