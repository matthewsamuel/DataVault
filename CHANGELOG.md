# Changelog

Alle noemenswaardige wijzigingen aan DataVault Dashboard.

## Fase 2 – Stabiliseren

### Veiligheid van je bestanden
- Elk verplaatsen van een bestand (Verplaatsen in de viewer, Keurer weggooien en verplaatsen,
  duplicaten opruimen en terugzetten) gaat nu via één gedeelde, veilige verplaatsfunctie.
  Een bestaand bestand wordt nooit meer overschreven: bij een naambotsing krijgt het
  verplaatste bestand een vrije naam als `foto_DUP_001.jpg`. Ook drie bestanden met dezelfde
  naam die binnen één seconde naar dezelfde map gaan, blijven alle drie bestaan.
- Verplaatsen bewaart alle metadata (wijzigings- en aanmaakdatum, EXIF, extended attributes)
  en vergrendelde bestanden (Finder-slotje) blijven vergrendeld.
- Verplaatsen naar een andere schijf wordt geweigerd met een duidelijke melding, in plaats van
  stilletjes te kopiëren en het origineel te wissen.
- Mappen en snelkoppelingen (symlinks) worden nooit als bestand verplaatst.
