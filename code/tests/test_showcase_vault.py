"""The showcase vault (tests/fixtures/showcase_vault.py) for screenshots and design reviews."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import showcase_vault  # noqa: E402


def test_every_scene_renders_a_distinct_photo():
    seen = set()
    for i, scene in enumerate(showcase_vault.PALETTES):
        img = showcase_vault.render_scene(scene, showcase_vault.SEED + i, (160, 120))
        assert img.size == (160, 120) and img.mode == "RGB"
        seen.add(img.tobytes())
    assert len(seen) == len(showcase_vault.PALETTES)


def test_trips_are_spread_over_years_and_countries():
    years = {folder.split("/")[0] for folder, *_ in showcase_vault.TRIPS}
    places = {gps for *_, gps, _cam, _n in showcase_vault.TRIPS}
    assert len(years) >= 8 and len(places) == len(showcase_vault.TRIPS)
    assert sum(t[-1] for t in showcase_vault.TRIPS) >= 150
    assert set(showcase_vault.LOCATION_TRIPS) <= {t[0] for t in showcase_vault.TRIPS}


def test_cli_refuses_a_relative_or_outside_directory(tmp_path):
    import subprocess
    script = Path(showcase_vault.__file__)
    for bad in ("relative-dir", "--help-not-a-flag", "/Users/nobody/vault"):
        r = subprocess.run([sys.executable, str(script), bad], cwd=tmp_path, capture_output=True, text=True)
        assert r.returncode != 0
    assert list(tmp_path.iterdir()) == []
    r = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0 and "directory" in r.stdout
