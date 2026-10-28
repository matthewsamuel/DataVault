"""
Install the 'Slim zoeken' model by hand (the same as the 'Zoekmodel installeren' button
in Instellingen). DataVault never downloads anything by itself: this is the one, explicit
step that fetches the model files from Hugging Face. Every file is pinned to a revision
and its SHA-256 is checked before it is used; an interrupted download continues.

    cd code
    venv/bin/python scripts/download_models.py            # into code/models (or models_folder)
    venv/bin/python scripts/download_models.py --list     # what would be downloaded, from where
    venv/bin/python scripts/download_models.py --models-folder /Volumes/T7/models

The models folder is 'models_folder' in config.json when set, else code/models.
"""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

CODE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE_DIR))

from backend import semantic  # noqa: E402
from backend.config import DEFAULT_MODELS_FOLDER, resolve_config_path  # noqa: E402


def models_folder(explicit):
    if explicit:
        return Path(explicit).expanduser().resolve()
    try:
        data = json.loads(resolve_config_path().read_text())
        if data.get("models_folder"):
            return Path(data["models_folder"]).expanduser().resolve()
    except (OSError, ValueError):
        pass
    return DEFAULT_MODELS_FOLDER


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the Slim zoeken model (one-time download).")
    parser.add_argument("--models-folder", help="where the models go (default: models_folder or code/models)")
    parser.add_argument("--model", default=semantic.DEFAULT_MODEL, choices=sorted(semantic.MODELS))
    parser.add_argument("--list", action="store_true", help="only show the files, sizes and sources")
    args = parser.parse_args()
    spec = semantic.MODELS[args.model]
    config = SimpleNamespace(MODELS_FOLDER=models_folder(args.models_folder))
    print(f"Model:   {spec.label} ({spec.id}), licence {spec.licence}")
    print(f"Folder:  {semantic.model_dir(config, spec)}")
    for f in spec.files:
        print(f"  {f.path:20s} {f.size / 1e6:8.1f} MB  {f.url}")
    print(f"Total:   {spec.size / 1e6:.0f} MB")
    if args.list:
        return 0
    if semantic.installed(config, spec):
        print("Already installed and verified.")
        return 0

    def progress(done, total, message):
        print(f"\r{done / 1e6:8.1f} / {total / 1e6:.1f} MB  {message[:60]:60s}", end="", flush=True)

    result = semantic.download_model(config, spec, progress=progress)
    print(f"\nInstalled: {result['installed']} (downloaded {result['downloaded'] / 1e6:.0f} MB, checksums verified)")
    return 0 if result["installed"] else 1


if __name__ == "__main__":
    sys.exit(main())
