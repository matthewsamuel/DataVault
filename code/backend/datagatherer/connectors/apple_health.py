"""
DataGatherer connector: Apple Gezondheid (export.zip)

The user exports everything in the Health app on the iPhone (profile > 'Exporteer alle
gezondheidsgegevens') and AirDrops or saves export.zip. It holds apple_health_export/
export.xml (the file name follows the phone's language: 'exporteren.xml' in Dutch), often
gigabytes: it is streamed from the zip with iterparse, never unpacked or read whole.

A few useful metrics only:
  steps     per day, the source (iPhone, Watch) with the most steps that day, as Health
            itself avoids counting twice; the export day itself is left out (not complete)
  workout   every workout: kind, duration, distance and energy when present
Every export is a full snapshot, so the keys (day, or workout start + kind) make a second
export add only what is new.
"""
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, IO, Iterator, List, Optional
from xml.etree.ElementTree import iterparse

from backend.datagatherer import activity_store as store, export_kit
from backend.datagatherer.base import Connector, ConnectorError, Coverage, ExportKind, GatherContext, HelpOption

STEPS = "HKQuantityTypeIdentifierStepCount"
MAX_XML_BYTES = 40 * 1024 ** 3
WORKOUTS = {
    "Running": "Hardlopen", "Walking": "Wandelen", "Cycling": "Fietsen", "Swimming": "Zwemmen", "Hiking": "Wandeltocht",
    "Yoga": "Yoga", "TraditionalStrengthTraining": "Krachttraining", "FunctionalStrengthTraining": "Krachttraining",
    "HighIntensityIntervalTraining": "HIIT", "Soccer": "Voetbal", "Tennis": "Tennis", "Rowing": "Roeien",
    "Elliptical": "Crosstrainer", "Dance": "Dansen", "CoreTraining": "Core-training", "Other": "Training",
}


def health_time(text: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.strptime((text or "").strip(), "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def _xml_member(names: List[str]) -> Optional[str]:
    for n in names:
        low = n.lower()
        if low.endswith(".xml") and "apple_health_export/" in low and "cda" not in low.rsplit("/", 1)[-1]:
            return n
    return None


def parse(stream: IO[bytes], check=None) -> Iterator[Dict[str, Any]]:
    steps: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    export_day = None
    count = 0
    root = None
    depth = 0
    for event, el in iterparse(stream, events=("start", "end")):
        if event == "start":
            if root is None:
                root = el
            depth += 1
            continue
        depth -= 1
        tag = el.tag
        if tag == "Record":
            if el.get("type") == STEPS:
                start = health_time(el.get("startDate"))
                try:
                    value = float(el.get("value") or 0)
                except ValueError:
                    value = 0
                if start is not None and value > 0:
                    steps[start.strftime("%Y-%m-%d")][el.get("sourceName") or "?"] += value
            el.clear()
        elif tag == "Workout":
            ev = _workout(el)
            if ev is not None:
                yield ev
            el.clear()
        elif tag == "ExportDate":
            moment = health_time(el.get("value"))
            export_day = moment.strftime("%Y-%m-%d") if moment else None
        if depth == 1:
            # A child of the root is done: drop it from the root, or millions of emptied
            # Records stay attached and memory grows with the export
            root.clear()
        count += 1
        if check is not None and count % 50000 == 0:
            check()
    for day, by_source in sorted(steps.items()):
        if day == export_day:
            continue
        source, total = max(by_source.items(), key=lambda kv: kv[1])
        noon = datetime.strptime(day + " 12:00:00", "%Y-%m-%d %H:%M:%S").astimezone()
        n = int(round(total))
        yield store.event("steps", int(noon.timestamp() * 1000), store.key("steps", day),
                          title=f"{n:,} stappen".replace(",", "."), details={"stappen": n, "dag": day, "bron": source[:80]})


def _workout(el) -> Optional[Dict[str, Any]]:
    start, end = health_time(el.get("startDate")), health_time(el.get("endDate"))
    if start is None:
        return None
    raw_type = (el.get("workoutActivityType") or "").replace("HKWorkoutActivityType", "")
    label = WORKOUTS.get(raw_type, raw_type or "Training")
    details: Dict[str, Any] = {"soort": label}
    try:
        minutes = float(el.get("duration") or 0)
        if (el.get("durationUnit") or "min") == "min" and minutes:
            details["duur_min"] = round(minutes, 1)
    except ValueError:
        pass
    distance, unit = el.get("totalDistance"), el.get("totalDistanceUnit")
    energy = el.get("totalEnergyBurned")
    for stat in el.findall("WorkoutStatistics"):       # newer exports keep these in child elements
        kind = stat.get("type") or ""
        if "Distance" in kind and not distance:
            distance, unit = stat.get("sum"), stat.get("unit")
        if kind.endswith("ActiveEnergyBurned") and not energy:
            energy = stat.get("sum")
    try:
        if distance and float(distance) > 0:
            details["afstand"] = f"{float(distance):.2f} {unit or 'km'}"
        if energy and float(energy) > 0:
            details["kcal"] = round(float(energy))
    except ValueError:
        pass
    ts = int(start.timestamp() * 1000)
    return store.event("workout", ts, store.key("workout", ts, raw_type), title=label, details=details,
                       end_ms=int(end.timestamp() * 1000) if end else None)


class AppleHealth(Connector):
    id = "apple_health"
    name = "Apple Gezondheid"
    icon = "fas fa-heart-pulse"
    category = "Activiteit"
    order = 60
    description = "Je stappen per dag en je trainingen, uit de export van de app Gezondheid op je iPhone."
    capabilities = frozenset({"export_request", "export_watch", "import"})
    raw_folder = "Apple Gezondheid"
    record_label = ("meting", "metingen")
    stale_days = 60
    settings = (export_kit.PASHA,)
    exports = (
        ExportKind(
            id="export", label="Gezondheid-export", request_url="https://support.apple.com/guide/iphone/iph5ede58c3d/ios",
            patterns=("export*.zip", "*.zip"), folder_label="Export", expected_days=(0, 0),
            steps=("Open op je iPhone de app Gezondheid en tik rechtsboven op je profielfoto.",
                   "Tik op 'Exporteer alle gezondheidsgegevens' en wacht tot het klaar is.",
                   "Deel export.zip met AirDrop naar deze Mac (of bewaar hem in iCloud Drive).",
                   "DataVault vindt hem in Downloads en leest stappen en trainingen in.")),
    )
    help = (
        HelpOption(title="Exporteren op je iPhone", effort="makkelijk",
                   steps=("Gezondheid > profielfoto > 'Exporteer alle gezondheidsgegevens'.",
                          "AirDrop export.zip naar deze Mac; klik hier op In Downloads zoeken."),
                   note="Een volledige export kan enkele minuten duren en groot zijn."),
    )

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        return "export" if names is not None and _xml_member(names) else None

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        path = Path(raw["path"])
        try:
            with zipfile.ZipFile(path) as zf:
                member = _xml_member([i.filename for i in zf.infolist()])
                if member is None:
                    raise ConnectorError("Geen export.xml gevonden in deze Gezondheid-export")
                if zf.getinfo(member).file_size > MAX_XML_BYTES:
                    raise ConnectorError("Deze Gezondheid-export is te groot om in te lezen")
                ctx.progress(message="Gezondheid-export lezen (dat kan even duren)")
                with zf.open(member) as stream:
                    events: List[Dict[str, Any]] = list(parse(stream, check=ctx.check))
        except zipfile.BadZipFile:
            raise ConnectorError("Dit is geen zip van de app Gezondheid")
        except SyntaxError as e:          # xml.etree.ElementTree.ParseError
            raise ConnectorError(f"export.xml is niet leesbaar: {e}")
        return store.insert(ctx.db, self.id, raw["id"], events, check=ctx.check)

    def coverage(self, ctx: GatherContext) -> Coverage:
        return store.coverage(ctx.db, self.id)


CONNECTOR = AppleHealth()
