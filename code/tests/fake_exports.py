"""
Synthetic exports for the DataGatherer step 3.9c tests: Google Takeout (Dutch and English
names), location history in all four formats, browser history databases, Netflix,
Snapchat, TikTok, Apple and Apple Health. Everything is made up; the shapes follow the
real formats. Nothing here reads real personal data.
"""
import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import zipfile
from pathlib import Path

# 14 July 2019, Brussels summer time (UTC+2)
T0 = "2019-07-14T08:00:00Z"


def my_activity_json():
    return [
        {"header": "YouTube", "title": "Watched Kattenfilmpje", "titleUrl": "https://www.youtube.com/watch?v=abc",
         "subtitles": [{"name": "Kanaal X", "url": "https://www.youtube.com/channel/x"}], "time": "2019-07-14T08:00:00.000Z",
         "products": ["YouTube"]},
        {"header": "YouTube", "title": "Searched for fietsroutes", "titleUrl": "https://www.youtube.com/results?search_query=fietsroutes",
         "time": "2019-07-14T08:05:00.000Z", "products": ["YouTube"]},
    ]


def search_activity_json():
    return [{"header": "Zoeken", "title": "Gezocht naar weer Gent", "titleUrl": "https://www.google.com/search?q=weer+gent",
             "time": "2019-07-14T09:00:00.000Z", "products": ["Zoeken"]}]


MY_ACTIVITY_HTML = """<html><body><div class="mdl-grid">
<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp"><div class="mdl-grid">
<div class="header-cell mdl-cell mdl-cell--12-col"><p class="mdl-typography--title">Maps<br></p></div>
<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">Gezocht naar&nbsp;<a href="https://www.google.com/maps/search/Gravensteen">Gravensteen</a><br>14 jul. 2019, 12:30:00 CEST</div>
<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1 mdl-typography--text-right"></div>
<div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption"><b>Producten:</b><br>&emsp;Maps<br></div>
</div></div>
<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp"><div class="mdl-grid">
<div class="header-cell mdl-cell mdl-cell--12-col"><p class="mdl-typography--title">YouTube<br></p></div>
<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">Watched&nbsp;<a href="https://www.youtube.com/watch?v=def">Fietsen in Gent</a><br><a href="https://www.youtube.com/channel/y">Kanaal Y</a><br>Jul 14, 2019, 3:00:00 PM CEST</div>
</div></div>
</div></body></html>"""


def watch_history_json():
    # The same video as in My Activity (one record), plus one more
    return my_activity_json()[:1] + [
        {"header": "YouTube", "title": "Watched Tweede video", "titleUrl": "https://www.youtube.com/watch?v=ghi",
         "time": "2019-07-15T20:00:00.000Z", "products": ["YouTube"]}]


def chrome_takeout_json():
    return {"Browser History": [
        {"title": "Voorbeeld", "url": "https://voorbeeld.be/", "time_usec": 1563091200000000, "page_transition": "LINK"},
        {"title": "Ander", "url": "https://ander.be/", "time_usec": 1563094800000000, "page_transition": "TYPED"}]}


ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:ev1@google.com\r
DTSTART;TZID=Europe/Brussels:20190714T140000\r
DTEND;TZID=Europe/Brussels:20190714T160000\r
SUMMARY:Verjaardag\\, bij oma\r
LOCATION:Gent\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:ev2@google.com\r
DTSTART;VALUE=DATE:20190715\r
SUMMARY:Vrije dag\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:ev3@google.com\r
DTSTART:20190716T080000Z\r
SUMMARY:Een heel lange titel die over twee regels\r
  gevouwen is\r
RRULE:FREQ=WEEKLY\r
END:VEVENT\r
END:VCALENDAR\r
"""


def keep_note():
    return {"title": "Boodschappen", "textContent": "melk, brood", "createdTimestampUsec": 1563091200000000,
            "userEditedTimestampUsec": 1563091300000000, "isTrashed": False, "labels": [{"name": "thuis"}]}


def records_json():
    # Two points 20 minutes apart around 10:00 Brussels time (08:00 UTC), Gent -> 2 km east
    return {"locations": [
        {"latitudeE7": 510500000, "longitudeE7": 37200000, "accuracy": 20, "timestamp": "2019-07-14T07:50:00Z"},
        {"latitudeE7": 510500000, "longitudeE7": 37500000, "accuracy": 20, "timestampMs": "1563091800000"},
    ]}


def semantic_json():
    return {"timelineObjects": [
        {"placeVisit": {"location": {"latitudeE7": 510570000, "longitudeE7": 37210000, "placeId": "ChIJgravensteen",
                                     "name": "Gravensteen", "address": "Sint-Veerleplein 11, Gent"},
                        "duration": {"startTimestamp": "2019-07-14T10:00:00Z", "endTimestamp": "2019-07-14T11:00:00Z"},
                        "visitConfidence": 90}},
        {"activitySegment": {"startLocation": {"latitudeE7": 510570000, "longitudeE7": 37210000},
                             "endLocation": {"latitudeE7": 510000000, "longitudeE7": 37000000},
                             "duration": {"startTimestampMs": "1563843600000", "endTimestampMs": "1563847200000"},
                             "simplifiedRawPath": {"points": [{"latE7": 510300000, "lngE7": 37100000,
                                                               "timestamp": "2019-07-23T01:30:00Z", "accuracyMeters": 15}]}}},
    ]}


def device_json():
    return {"semanticSegments": [
        {"startTime": "2024-03-30T10:00:00.000+01:00", "endTime": "2024-03-30T12:00:00.000+01:00",
         "visit": {"topCandidate": {"placeId": "ChIJmarkt", "semanticType": "UNKNOWN", "probability": 0.8,
                                    "placeLocation": {"latLng": "51.0547°, 3.7250°"}}}},
        {"startTime": "2024-03-30T12:00:00.000+01:00", "endTime": "2024-03-30T13:00:00.000+01:00",
         "timelinePath": [{"point": "51.0500°, 3.7300°", "time": "2024-03-30T12:15:00.000+01:00"},
                          {"point": "51.0400°, 3.7400°", "time": "2024-03-30T12:45:00.000+01:00"}]}],
        "rawSignals": [{"position": {"LatLng": "51.0300°, 3.7500°", "accuracyMeters": 12,
                                     "timestamp": "2024-03-30T14:00:00.000+01:00"}}],
        "userLocationProfile": {"frequentPlaces": []}}


def ios_json():
    return [
        {"startTime": "2024-03-31T10:00:00.000+02:00", "endTime": "2024-03-31T11:00:00.000+02:00",
         "visit": {"hierarchyLevel": "0", "topCandidate": {"probability": "0.9", "semanticType": "Home",
                                                          "placeID": "ChIJthuis", "placeLocation": "geo:51.100000,3.800000"}}},
        {"startTime": "2024-03-31T11:00:00.000+02:00", "endTime": "2024-03-31T11:30:00.000+02:00",
         "activity": {"start": "geo:51.100000,3.800000", "end": "geo:51.200000,3.900000",
                      "topCandidate": {"type": "walking", "probability": "0.8"}, "distanceMeters": "1500"}},
        {"startTime": "2024-03-31T11:00:00.000+02:00", "endTime": "2024-03-31T13:00:00.000+02:00",
         "timelinePath": [{"point": "geo:51.150000,3.850000", "durationMinutesOffsetFromStartTime": "15"}]},
    ]


def write_zip(path: Path, members: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            if not isinstance(data, (bytes, str)):
                data = json.dumps(data)
            zf.writestr(name, data)
    return path


def dutch_takeout_folder(root: Path) -> Path:
    """An unpacked Dutch Takeout: Takeout/Mijn activiteit/..., Agenda, Keep, Chrome, Locatiegeschiedenis."""
    base = root / "takeout-20190801T000000Z-001" / "Takeout"
    files = {
        "Mijn activiteit/YouTube/MijnActiviteit.json": my_activity_json(),
        "Mijn activiteit/Zoeken/MijnActiviteit.json": search_activity_json(),
        "Mijn activiteit/Maps/MijnActiviteit.html": MY_ACTIVITY_HTML,
        "YouTube en YouTube Music/geschiedenis/kijkgeschiedenis.json": watch_history_json(),
        "Chrome/BrowserHistory.json": chrome_takeout_json(),
        "Agenda/Mijn agenda.ics": ICS,
        "Keep/Boodschappen.json": keep_note(),
        "Locatiegeschiedenis/Records.json": records_json(),
        "Locatiegeschiedenis/Semantic Location History/2019/2019_JULI.json": semantic_json(),
        "Google Foto's/Foto's uit 2019/IMG_1.jpg.json": {"title": "IMG_1.jpg"},
    }
    for rel, data in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return root


def english_takeout_zip(path: Path) -> Path:
    """An English Takeout zip that overlaps the Dutch one (the same video, the same records)."""
    return write_zip(path, {
        "Takeout/My Activity/YouTube/MyActivity.json": my_activity_json(),
        "Takeout/YouTube and YouTube Music/history/watch-history.json": watch_history_json(),
        "Takeout/YouTube and YouTube Music/history/search-history.json": [
            {"header": "YouTube", "title": "Searched for treinreis", "titleUrl": "https://www.youtube.com/results?search_query=treinreis",
             "time": "2019-07-16T10:00:00.000Z"}],
        "Takeout/Calendar/Work.ics": ICS,
        "Takeout/Location History/Records.json": records_json(),
    })


def write_tgz(path: Path, members: dict, mode: str = "w:gz") -> Path:
    """A .tgz/.tar.gz as Takeout makes it: members in archive order, gzip-compressed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, mode) as tf:
        for name, data in members.items():
            if not isinstance(data, (bytes, str)):
                data = json.dumps(data)
            blob = data.encode("utf-8") if isinstance(data, str) else data
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(blob), 1567296000
            tf.addfile(info, io.BytesIO(blob))
    return path


def dutch_takeout_members() -> dict:
    """The Dutch Takeout's files (dutch_takeout_folder), as archive members under Takeout/."""
    root = Path(tempfile.mkdtemp())
    try:
        dutch_takeout_folder(root)
        base = root / "takeout-20190801T000000Z-001"
        return {str(p.relative_to(base)): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}
    finally:
        shutil.rmtree(root)


def dutch_takeout_tgz(path: Path) -> Path:
    """A Dutch Takeout as .tgz, a photo first (as Takeout often starts) and the sidecars."""
    members = {"Takeout/Google Foto's/Foto's uit 2019/IMG_0.jpg": b"\xff\xd8\xff" + os.urandom(2048)}
    members.update(dutch_takeout_members())
    return write_tgz(path, members)


def english_takeout_tgz(path: Path) -> Path:
    """The English Takeout (english_takeout_zip) as .tar.gz, with a './' prefix like GNU tar writes."""
    zip_path = english_takeout_zip(Path(tempfile.mkdtemp()) / "t.zip")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = {"./" + n: zf.read(n) for n in zf.namelist()}
    finally:
        shutil.rmtree(zip_path.parent)
    return write_tgz(path, members)


def chrome_history_db(path: Path, visits) -> Path:
    """A Chrome History database: urls + visits, visit_time in microseconds since 1601."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY, url LONGVARCHAR, title LONGVARCHAR, visit_count INTEGER DEFAULT 0,
                           typed_count INTEGER DEFAULT 0, last_visit_time INTEGER, hidden INTEGER DEFAULT 0);
        CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER NOT NULL, visit_time INTEGER NOT NULL, from_visit INTEGER,
                             transition INTEGER DEFAULT 0, segment_id INTEGER, visit_duration INTEGER DEFAULT 0);
    """)
    for n, (unix_s, url, title) in enumerate(visits, 1):
        conn.execute("INSERT OR IGNORE INTO urls (url, title) VALUES (?, ?)", (url, title))
        uid = conn.execute("SELECT id FROM urls WHERE url = ?", (url,)).fetchone()[0]
        conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (?, ?, ?)",
                     (uid, int((unix_s + 11644473600) * 1e6), 5000000))
    conn.commit()
    conn.close()
    return path


def safari_history_db(path: Path, visits) -> Path:
    """A Safari History.db: history_items + history_visits, visit_time in seconds since 2001."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE history_items (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL UNIQUE,
                                    domain_expansion TEXT NULL, visit_count INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE history_visits (id INTEGER PRIMARY KEY AUTOINCREMENT, history_item INTEGER NOT NULL,
                                     visit_time REAL NOT NULL, title TEXT NULL, load_successful BOOLEAN NOT NULL DEFAULT 1,
                                     redirect_source INTEGER NULL, redirect_destination INTEGER NULL);
    """)
    for unix_s, url, title, redirect in visits:
        conn.execute("INSERT OR IGNORE INTO history_items (url) VALUES (?)", (url,))
        iid = conn.execute("SELECT id FROM history_items WHERE url = ?", (url,)).fetchone()[0]
        conn.execute("INSERT INTO history_visits (history_item, visit_time, title, redirect_source) VALUES (?, ?, ?, ?)",
                     (iid, unix_s - 978307200, title, redirect))
    conn.commit()
    conn.close()
    return path


NETFLIX_FULL = ("Profile Name,Start Time,Duration,Attributes,Title,Supplemental Video Type,Device Type,Bookmark,"
                "Latest Bookmark,Country\n"
                "Matthew,2019-07-14 19:00:00,00:45:00,,Serie: Seizoen 1: Aflevering 1,,Apple TV,00:45:00,00:45:00,BE (Belgium)\n"
                "Matthew,2019-07-14 20:00:00,00:01:30,,Trailer X,TRAILER,Apple TV,00:01:30,00:01:30,BE (Belgium)\n"
                "Gast,2019-07-15 18:00:00,01:40:00,,Een film,,iPhone,01:40:00,01:40:00,BE (Belgium)\n")
NETFLIX_SIMPLE = "Title,Date\n\"Serie: Seizoen 1: Aflevering 2\",15-07-2019\nDocumentaire,16-07-2019\n"


def snapchat_zip(path: Path) -> Path:
    return write_zip(path, {
        "json/memories_history.json": {"Saved Media": [
            {"Date": "2019-07-14 10:00:00 UTC", "Media Type": "Image", "Location": "Latitude, Longitude: 51.05, 3.72",
             "Download Link": "https://app.snapchat.com/dmd/memories?uid=x"},
            {"Date": "2019-07-14 11:00:00 UTC", "Media Type": "Video", "Location": "Latitude, Longitude: 0.0, 0.0",
             "Download Link": "https://app.snapchat.com/dmd/memories?uid=y"}]},
        "json/chat_history.json": {"vriend1": [
            {"From": "vriend1", "Media Type": "TEXT", "Created": "2019-07-14 12:00:00 UTC", "Content": "Hoi!"},
            {"From": "matthew", "Media Type": "TEXT", "Created": "2019-07-14 12:01:00 UTC", "Content": "Hey"}]},
        "json/account.json": {"Basic Information": {}},
    })


def tiktok_old():
    return {"Profile": {"Profile Information": {}}, "Activity": {
        "Video Browsing History": {"VideoList": [{"Date": "2023-01-01 12:00:00", "Link": "https://www.tiktokv.com/share/video/1/"}]},
        "Like List": {"ItemFavoriteList": [{"Date": "2023-01-01 12:05:00", "Link": "https://www.tiktokv.com/share/video/2/"}]},
        "Search History": {"SearchList": [{"Date": "2023-01-01 12:10:00", "SearchTerm": "koken"}]}}}


def tiktok_new():
    return {"Profile": {}, "Your Activity": {
        "Watch History": {"VideoList": [{"Date": "2023-01-01 12:00:00", "Link": "https://www.tiktokv.com/share/video/1/"},
                                        {"Date": "2023-01-02 09:00:00", "Link": "https://www.tiktokv.com/share/video/3/"}]},
        "Searches": {"SearchList": [{"Date": "2023-01-02 09:10:00", "SearchTerm": "fietsen"}]}}}


PLAY_ACTIVITY_CSV = ("Album Name,Artist Name,Content Name,Event End Timestamp,Event Start Timestamp,"
                     "Play Duration Milliseconds,Song Name\n"
                     "Album A,Artiest A,Nummer A,2019-07-14T10:03:00.000Z,2019-07-14T10:00:00.000Z,180000,Nummer A\n"
                     "Album B,Artiest B,Nummer B,2019-07-14T10:04:00.000Z,2019-07-14T10:03:30.000Z,0,Nummer B\n")


def apple_privacy_zip(path: Path) -> Path:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("Apple_Media_Services/Apple Music Activity/Apple Music Play Activity.csv", PLAY_ACTIVITY_CSV)
    return write_zip(path, {"Apple Media Services information.zip": inner.getvalue(), "Overig/info.txt": "x"})


HEALTH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!ELEMENT HealthData (ExportDate,Record*,Workout*)>
]>
<HealthData locale="nl_BE">
 <ExportDate value="2019-07-16 20:00:00 +0200"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" startDate="2019-07-14 09:00:00 +0200" endDate="2019-07-14 09:10:00 +0200" value="1000"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" startDate="2019-07-14 15:00:00 +0200" endDate="2019-07-14 15:10:00 +0200" value="500"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Apple Watch" unit="count" startDate="2019-07-14 09:00:00 +0200" endDate="2019-07-14 09:10:00 +0200" value="1200"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" startDate="2019-07-16 09:00:00 +0200" endDate="2019-07-16 09:10:00 +0200" value="300"/>
 <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Apple Watch" unit="count/min" startDate="2019-07-14 09:00:00 +0200" endDate="2019-07-14 09:00:00 +0200" value="70"/>
 <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30" durationUnit="min" sourceName="Apple Watch" startDate="2019-07-14 18:00:00 +0200" endDate="2019-07-14 18:30:00 +0200">
  <WorkoutStatistics type="HKQuantityTypeIdentifierDistanceWalkingRunning" sum="5.2" unit="km"/>
  <WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned" sum="350" unit="kcal"/>
 </Workout>
</HealthData>
"""


def health_zip(path: Path, xml_name="apple_health_export/exporteren.xml") -> Path:
    return write_zip(path, {xml_name: HEALTH_XML, "apple_health_export/export_cda.xml": "<ClinicalDocument/>"})


def swarm_checkins():
    """checkins1.json of a Foursquare data download: UTC 'createdAt', a venueless check-in, one without a place."""
    return {"count": 4, "items": [
        {"id": "5d2b0c1a", "createdAt": "2019-07-14 10:00:00.000000", "type": "checkin", "visibility": "public",
         "timeZoneOffset": 120, "venue": {"id": "4b5c", "name": "Café Het Spijker",
                                          "url": "https://foursquare.com/v/4b5c"},
         "lat": 51.0543, "lng": 3.7174, "hacc": 12.0, "shout": "Eindelijk terras"},
        {"id": "5d2b0c1b", "createdAt": "2019-07-14 18:30:00.000000", "type": "checkin",
         "timeZoneOffset": 120, "venue": {"id": "4b5d", "name": "Sint-Pietersstation"}, "lat": 51.0359, "lng": 3.7108},
        {"id": "5d2b0c1c", "createdAt": "2019-07-15 08:00:00", "type": "venueless", "shout": "Op weg naar zee",
         "lat": 51.2, "lng": 3.2},
        {"id": "5d2b0c1d", "createdAt": "2019-07-15 09:00:00.000000", "type": "checkin"}]}


def swarm_zip(path: Path) -> Path:
    return write_zip(path, {
        "checkins1.json": swarm_checkins(),
        "checkins1.csv": "id,createdAt\n5d2b0c1a,2019-07-14 10:00:00\n",
        "visits.json": {"count": 1, "items": [{"id": "v1", "createdAt": "2019-07-14 12:00:00.000000",
                                               "lat": 51.05, "lng": 3.72}]},
        "photos.json": {"count": 0, "items": []},
        "readme.html": "<html></html>",
    })
