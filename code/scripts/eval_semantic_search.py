"""
Measure how well 'Slim zoeken' finds the right photos, with the installed model and the
app's own encoder (backend/semantic.py).

The labelled set is in scripts/semantic_eval/: labels.txt (photo -> tags, checked by
hand), queries.txt (required tags | Dutch query | English query) and sources.json (where
each photo comes from, with its licence). The photos themselves are not in git: 149
openly licensed photos from Wikimedia Commons (fetch_commons.py downloads them once)
plus 30 macOS wallpaper thumbnails, in .model-build/eval/images/.

    cd code
    venv/bin/python scripts/eval_semantic_search.py --images ../.model-build/eval/images
    venv/bin/python scripts/eval_semantic_search.py --images ... --thumbnails   # as the job sees them

A query counts as found at rank k when a photo with all its tags is at place k. Printed
per language: recall@1, recall@5 (share of queries with a right photo at 1 / in the top
5), MRR, precision@5 and mAP, plus the speed of the image and text encoders.
--thumbnails first makes the 300 px thumbnails the embedding job uses (into a temp dir).
"""
import argparse
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

CODE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE_DIR))

from backend import semantic  # noqa: E402
from backend.config import DEFAULT_MODELS_FOLDER  # noqa: E402

HERE = Path(__file__).resolve().parent / "semantic_eval"


def load_set():
    labels = {}
    for line in (HERE / "labels.txt").read_text().splitlines():
        if line.strip():
            name, *tags = line.split()
            labels[name] = set(tags)
    queries = []
    for line in (HERE / "queries.txt").read_text().splitlines():
        if line.strip():
            tags, nl, en = [p.strip() for p in line.split("|")]
            queries.append((set(tags.split()), nl, en))
    return labels, queries


def metrics(emb, names, labels, queries, encoder, lang_index):
    r1 = r5 = mrr = p5 = ap_sum = 0.0
    times, misses = [], []
    for query in queries:
        need, text = query[0], query[lang_index]
        start = time.perf_counter()
        vector = encoder.encode_text(text)
        times.append(time.perf_counter() - start)
        order = np.argsort(-(emb @ vector))
        relevant = [need <= labels[names[i]] for i in order]
        first = relevant.index(True) + 1
        r1 += first == 1
        r5 += first <= 5
        mrr += 1 / first
        p5 += sum(relevant[:5]) / 5
        hits, ap = 0, 0.0
        for k, rel in enumerate(relevant, 1):
            if rel:
                hits += 1
                ap += hits / k
        ap_sum += ap / sum(relevant)
        if first > 1:
            misses.append(f"{text!r}: first right photo at {first} (top: {names[order[0]]})")
    n = len(queries)
    return {"R@1": r1 / n, "R@5": r5 / n, "MRR": mrr / n, "P@5": p5 / n, "mAP": ap_sum / n,
            "text_ms": float(np.median(times)) * 1000}, misses


def strong_report(emb, names, labels, queries, encoder, index) -> None:
    """Precision and recall of the 'strong' set (the relevance cut of semantic.py, background = this set)."""
    tp = fp = fn = empty = 0
    for q in queries:
        need, text = q[0], q[index]
        scores = emb @ encoder.encode_text(text)
        median = float(np.median(scores))
        mad = float(np.median(np.abs(scores - median))) * 1.4826
        cut = max(semantic.MIN_SCORE, float(scores.max()) - semantic.SCORE_SPREAD, median + semantic.SCORE_Z * mad)
        if float(scores.max()) >= semantic.CLEAR_SCORE and cut > float(scores.max()):
            cut = max(semantic.MIN_SCORE, float(scores.max()) - semantic.SCORE_SPREAD)
        strong = {names[i] for i in np.flatnonzero(scores >= cut)}
        rel = {n for n in names if need <= labels[n]}
        tp += len(strong & rel); fp += len(strong - rel); fn += len(rel - strong)
        empty += (not strong) and bool(rel)
    print(f"    strong set: recall {tp / (tp + fn):.3f} precision {tp / max(1, tp + fp):.3f}; "
          f"queries empty although right photos exist: {empty}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", required=True, help="folder with the eval photos (<name>.jpg)")
    parser.add_argument("--models-folder", default=str(DEFAULT_MODELS_FOLDER))
    parser.add_argument("--device", default="cpu", choices=("cpu", "auto"))
    parser.add_argument("--thumbnails", action="store_true", help="embed the 300 px thumbnails, as the job does")
    args = parser.parse_args()
    from PIL import Image
    spec = semantic.current_model()
    encoder = semantic.Encoder(spec, Path(args.models_folder) / spec.id)
    labels, queries = load_set()
    names = sorted(labels)
    paths = [Path(args.images) / f"{name}.jpg" for name in names]
    if args.thumbnails:
        from backend import thumbnail
        tmp = Path(tempfile.mkdtemp(prefix="dv_eval_thumbs_"))
        start = time.perf_counter()
        thumbs = []
        for path in paths:
            out = tmp / f"{path.stem}.jpg"
            thumbnail.render_photo_thumbnail(path, out)
            thumbs.append(out)
        print(f"thumbnails made in {time.perf_counter() - start:.1f} s")
        paths = thumbs
    device = encoder.load_image(args.device)
    encoder.encode_image(encoder.preprocess(Image.open(paths[0])))   # warm up
    start = time.perf_counter()
    pixels = []
    for path in paths:
        with Image.open(path) as img:
            pixels.append(encoder.preprocess(img))
    decode = time.perf_counter() - start
    start = time.perf_counter()
    emb = np.stack([encoder.encode_image(p) for p in pixels])
    seconds = time.perf_counter() - start
    print(f"model {spec.id}, {len(names)} photos, {len(queries)} queries per language, images on {device}")
    print(f"decode + preprocess {len(names) / decode:.0f} images/s; encoder {len(names) / seconds:.1f} images/s")
    for lang, index in (("nl", 1), ("en", 2)):
        result, misses = metrics(emb, names, labels, queries, encoder, index)
        print(f"{lang}: " + "  ".join(f"{k} {v:.3f}" if k != "text_ms" else f"text {v:.0f} ms" for k, v in result.items()))
        for miss in misses:
            print(f"    {miss}")
        strong_report(emb, names, labels, queries, encoder, index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
