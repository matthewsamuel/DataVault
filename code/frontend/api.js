// DataVault Dashboard - API-sessie
//
// De server weigert elk verzoek dat iets kan wijzigen (POST, PUT, PATCH, DELETE)
// zonder het sessietoken van het huidige serverproces (zie backend/security.py).
// Dit script laadt vóór alle andere scripts, haalt het token één keer op en zet het
// automatisch op elk wijzigend verzoek naar de eigen server. Na een herstart van de
// server is het token anders: dan wordt het één keer opnieuw opgehaald en het
// verzoek opnieuw verstuurd.

(function () {
    const nativeFetch = window.fetch.bind(window);
    const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
    const TOKEN_HEADER = 'X-DataVault-Token';
    let tokenPromise = null;

    function loadToken(force) {
        if (force || !tokenPromise) {
            tokenPromise = nativeFetch('/api/session', { cache: 'no-store', credentials: 'same-origin' })
                .then(res => {
                    if (!res.ok) throw new Error(`Sessie ophalen mislukt (${res.status})`);
                    return res.json();
                })
                .then(data => data.token)
                .catch(err => {
                    tokenPromise = null;  // volgende poging opnieuw proberen
                    throw err;
                });
        }
        return tokenPromise;
    }

    function isSameOrigin(url) {
        try {
            return new URL(url, window.location.href).origin === window.location.origin;
        } catch (_) {
            return false;
        }
    }

    window.fetch = async function (input, init) {
        const isRequest = input instanceof Request;
        const method = ((init && init.method) || (isRequest ? input.method : 'GET')).toUpperCase();
        const url = isRequest ? input.url : String(input);

        // Lezen en verzoeken naar andere sites gaan ongewijzigd (nooit het token meesturen)
        if (SAFE_METHODS.has(method) || !isSameOrigin(url)) {
            return nativeFetch(input, init);
        }

        const spare = isRequest ? input.clone() : null;  // body is maar één keer leesbaar
        const send = async (target, force) => {
            const token = await loadToken(force);
            const headers = new Headers((init && init.headers) || (isRequest ? target.headers : undefined));
            headers.set(TOKEN_HEADER, token);
            return nativeFetch(target, { ...(init || {}), headers });
        };

        const res = await send(input, false);
        if (res.status === 403 && res.headers.get('X-DataVault-Auth') === 'token') {
            // Server is herstart (nieuw token): één keer opnieuw proberen
            return send(spare || input, true);
        }
        return res;
    };

    // Fouten van de server hebben één vorm: {"error": {"code", "message"}} (plus het
    // oudere "detail" met dezelfde tekst). Dit haalt de leesbare melding eruit.
    window.apiErrorMessage = function (data, fallback) {
        if (data && data.error && typeof data.error.message === 'string') return data.error.message;
        if (data && typeof data.detail === 'string') return data.detail;
        return fallback || 'Er ging iets mis';
    };

    class ApiError extends Error {
        constructor(message, status, code) {
            super(message);
            this.name = 'ApiError';
            this.status = status;
            this.code = code;
        }
    }
    window.ApiError = ApiError;

    // fetch + JSON, met één foutafhandeling voor de hele app: een antwoord dat niet ok is,
    // wordt een ApiError met de melding van de server (status en code erbij). Is het
    // archief niet bereikbaar, dan hoort de rest van de pagina dat via het event
    // 'datavault:vault-unavailable' (de banner bovenin), zodat niet elk onderdeel een
    // eigen melding hoeft te tonen.
    window.fetchJSON = async function (url, options) {
        let res;
        try {
            res = await window.fetch(url, options);
        } catch (_) {
            throw new ApiError('De DataVault-server is niet bereikbaar', 0, 'network');
        }
        const data = await res.json().catch(() => null);
        if (!res.ok) {
            const code = (data && data.error && data.error.code) || `http_${res.status}`;
            const err = new ApiError(window.apiErrorMessage(data, `Serverfout (${res.status})`), res.status, code);
            if (code === 'vault_unavailable') {
                window.dispatchEvent(new CustomEvent('datavault:vault-unavailable'));
            }
            throw err;
        }
        return data;
    };

    window.DataVaultSession = {
        refresh: () => loadToken(true),
    };

    loadToken().catch(() => { /* server nog niet bereikbaar; volgende verzoek probeert opnieuw */ });
})();
