Natural Earth 1:50m data (https://www.naturalearthdata.com), public domain ("No permission is needed to use Natural Earth").
countries-50m.geojson: ne_50m_admin_0_countries, reduced to the Dutch country name (n), the English
  Natural Earth name (e, only where it differs; the backend's place search and time zones use it), the
  ADM0_A3 code (a), the point for the country name (l: Natural Earth's own label point when it lies well
  inside the largest part, else that part's pole of inaccessibility in Mercator), MIN_LABEL (z) and
  LABELRANK (k), and coordinates rounded to 2 decimals.
places-50m.json: ne_50m_populated_places_simple, capitals and places of 300,000+ inhabitants, as
  [name, lon, lat, min_zoom, is_capital]. Used as the offline basemap of the Kaart view.
regions/<ADM0_A3>.json: per country, loaded only when the country is selected on the map or in view at zoom 7+:
  borders  - the lines between provinces (ne_10m_admin_1_states_provinces, simplified; FRA, ITA, GBR, SVN,
             LVA, MLT and PHL merged into their regions), without the outer border
  labels   - [name, lon, lat] per province, Dutch names where Natural Earth has them
  cities   - [name, lon, lat, min_zoom, is_capital]: places of 50,000+ inhabitants and province capitals from
             GeoNames cities15000 (https://www.geonames.org, CC BY 4.0, credited in the map attribution),
             Dutch names from ne_10m_populated_places where it has them
regions/towns/<ADM0_A3>.json: per country, the smaller places for the map from zoom 8: every place of 1,000+
  inhabitants from GeoNames cities1000 (CC BY 4.0) that is not a city above or within 1.5 km of a larger
  place, as [name, lon, lat, min_zoom], largest first. A place where photos were taken shows earlier.
../../../backend/data/places_detail.json (server only, for the place name of a photo): the provinces as
  shapes, the coast line and every place of 15,000+ inhabitants (GeoNames), coordinates in thousandths
  of a degree, each ring as its first point followed by differences.
Rebuild everything with code/scripts/build_map_regions.py (see its docstring for the source files).
