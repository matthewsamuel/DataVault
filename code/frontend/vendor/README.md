# Meegeleverde bestanden van derden

De dashboard-app laadt niets van internet: alles wat de pagina nodig heeft staat hier.
`code/tests/test_frontend_offline.py` faalt zodra de frontend een externe host noemt.

| Map | Wat | Versie | Bron | Licentie |
|---|---|---|---|---|
| `fontawesome-6.4.0/` | Font Awesome Free: `css/all.min.css` en `webfonts/` | 6.4.0 (dezelfde bestanden als cdnjs 6.4.0) | npm `@fortawesome/fontawesome-free@6.4.0` | `LICENSE.txt` (iconen CC BY 4.0, fonts SIL OFL 1.1, code MIT) |
| `jetbrains-mono/` | JetBrains Mono, variabel lettertype (gewicht 300–600), woff2 per tekenset | Google Fonts v24 | fonts.gstatic.com (zoals voorheen via fonts.googleapis.com geladen) | `OFL.txt` (SIL OFL 1.1) |
| `leaflet-1.9.4/` | Leaflet, de kaartbibliotheek (`leaflet.js`, `leaflet.css`, `images/`) | 1.9.4 | npm `leaflet@1.9.4` | `LICENSE` (BSD 2-Clause) |
| `leaflet.markercluster-1.5.3/` | Leaflet.markercluster: groepeert markers met aantallen | 1.5.3 | npm `leaflet.markercluster@1.5.3` | `LICENSE` (MIT) |
| `naturalearth/` | Natural Earth 1:50m landen en steden: de basiskaart zonder internet | 5.1 (vereenvoudigd) | naturalearthdata.com via GitHub nvkelso/natural-earth-vector | `README.txt` (publiek domein) |

Bijwerken: vervang de bestanden in de map (nieuwe versie in de mapnaam), pas de `<link>` in
`index.html` aan en draai de tests.
