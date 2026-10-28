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

    window.DataVaultSession = {
        refresh: () => loadToken(true),
    };

    loadToken().catch(() => { /* server nog niet bereikbaar; volgende verzoek probeert opnieuw */ });
})();
