"""Fetch candidate eval photos (openly licensed) from Wikimedia Commons.
Writes <out>/cand/<concept>__<n>.jpg and <out>/cand/sources.json (title, url, licence, artist)."""
import json, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

OUT = Path(sys.argv[1]); CAND = OUT / "cand"; CAND.mkdir(parents=True, exist_ok=True)
PER = int(sys.argv[2]) if len(sys.argv) > 2 else 8
UA = {"User-Agent": "DataVault-eval/1.0 (local research; matthew@datameister.ai)"}
OK_LIC = re.compile(r"^(CC0|CC BY(-SA)? [0-9.]+|Public domain|PD.*|CC BY [0-9.]+|CC BY-SA [0-9.]+.*)$", re.I)
CONCEPTS = {
    "dog_beach": "dog beach",
    "dog": "dog grass",
    "cat": "domestic cat portrait",
    "beach": "sandy beach summer",
    "snow_mountains": "snow covered mountains alps",
    "birthday_cake": "birthday cake",
    "city_night": "city skyline at night",
    "sunset_sea": "sunset over the sea",
    "food": "plate of pasta food",
    "pizza": "pizza margherita",
    "car": "car parked street",
    "forest": "forest path trees",
    "bicycle": "bicycles Amsterdam",
    "tulips": "tulip field Netherlands",
    "sailboat": "sailing boat",
    "horse": "horse in meadow",
    "christmas_tree": "decorated christmas tree",
    "canal": "Amsterdam canal houses",
    "dogq": "Canis lupus familiaris dog",
    "dogsand": "dog playing on the beach sand",
    "forestq": "beech forest",
    "dogp": "Labrador Retriever",
    "dogc": "Border Collie",
    "dogh": "dog garden lawn playing",
    "cakec": "birthday candles cake party",
    "forestr": "forest trail autumn",
}
def api(params):
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({**params, "format": "json"})
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)
sources = json.loads((CAND / "sources.json").read_text()) if (CAND / "sources.json").exists() else {}
only = sys.argv[3:] or list(CONCEPTS)
for concept in only:
    q = CONCEPTS[concept]
    data = api({"action": "query", "generator": "search", "gsrsearch": f"{q} filetype:bitmap" + ("" if __import__("os").environ.get("NOCAT") else " incategory:Quality_images"),
                "gsrnamespace": 6, "gsrlimit": 30, "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime",
                "iiurlwidth": 640})
    pages = sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    n = 0
    for p in pages:
        if n >= PER: break
        ii = p["imageinfo"][0]
        if ii.get("mime") != "image/jpeg": continue
        meta = ii.get("extmetadata", {})
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        if not OK_LIC.match(lic.strip()): continue
        name = f"{concept}__{n}.jpg"
        try:
            with urllib.request.urlopen(urllib.request.Request(ii["thumburl"], headers=UA), timeout=60) as r:
                (CAND / name).write_bytes(r.read())
        except Exception as e:
            print("fail", p["title"], e); continue
        artist = re.sub("<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")).strip()
        sources[name] = {"title": p["title"], "page": ii.get("descriptionurl"), "licence": lic,
                         "licence_url": (meta.get("LicenseUrl") or {}).get("value"), "artist": artist}
        n += 1
        time.sleep(0.3)
    print(concept, n, flush=True)
(CAND / "sources.json").write_text(json.dumps(sources, indent=1, ensure_ascii=False))
