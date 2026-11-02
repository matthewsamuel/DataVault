"""
Measure how well 'Slim zoeken' finds the right photos, with the installed model and the
app's own encoder (backend/semantic.py).

The labelled set is in scripts/semantic_eval/: labels.txt (photo -> tags, checked by
hand), queries.txt (required tags | Dutch query | English query), free_queries.txt (20
Dutch and 10 English free-form queries written after the cut was set: tags | language |
query), absent_queries.txt (things in none of the photos: Dutch | English) and
sources.json (where each photo comes from, with its licence). The photos themselves are not in git: 149
openly licensed photos from Wikimedia Commons (fetch_commons.py downloads them once)
plus 30 macOS wallpaper thumbnails, in .model-build/eval/images/.

    cd code
    venv/bin/python scripts/eval_semantic_search.py --images ../.model-build/eval/images
    venv/bin/python scripts/eval_semantic_search.py --images ... --thumbnails   # as the job sees them
    venv/bin/python scripts/eval_semantic_search.py --images ... --thumbnails --save-fixture
        # also stores the vectors for tests/test_semantic_ranking.py (after a model or cut change)

A query counts as found at rank k when a photo with all its tags is at place k. Printed
per language: recall@1, recall@5 (share of queries with a right photo at 1 / in the top
5), MRR, precision@5 and mAP (ranked with the app's query vector: hub correction included),
the recall and precision of the 'strong' set (semantic.relevance_cut) for the labelled and
the free-form queries, how many photos absent things let through, the recall when a concept
is a fifth or a half of the searched folder (the background stays the whole set), plus the
speed of the image and text encoders.
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
FIXTURE = CODE_DIR / "tests" / "fixtures" / "semantic_eval_vectors.npz"


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


def load_extra():
    """([(tags, language, query)], [(dutch, english)]): the free-form and the absent queries."""
    free, absent = [], []
    for line in (HERE / "free_queries.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            tags, lang, text = [p.strip() for p in line.split("|")]
            free.append((set(tags.split()), lang, text))
    for line in (HERE / "absent_queries.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            absent.append(tuple(p.strip() for p in line.split("|")))
    return free, absent


def absent_let_through(emb, vectors):
    """(queries that let a photo through, photos let through) for queries of absent things."""
    counts = []
    for vector in vectors:
        scores = emb @ vector
        counts.append(int((scores >= semantic.relevance_cut(scores, scores)).sum()))
    return sum(c > 0 for c in counts), sum(counts)


def folder_recall(emb, names, labels, queries, vectors, share, seed=1):
    """
    Recall of the strong set when the right photos are `share` of the searched selection
    (a folder or filter) and the background is the whole set, as in semantic.rank; and the
    number of those searches that come up empty. Queries with fewer than 4 right photos skip.
    """
    rng = np.random.default_rng(seed)
    total = found = empty = 0
    for q, vector in zip(queries, vectors):
        right = np.array([q[0] <= labels[n] for n in names])
        if right.sum() < 4:
            continue
        pos, neg = np.flatnonzero(right), np.flatnonzero(~right)
        extra = rng.choice(neg, size=min(len(neg), int(round(len(pos) * (1 - share) / share))), replace=False)
        every = emb @ vector
        selection = every[np.concatenate([pos, extra])]
        hits = int((selection[:len(pos)] >= semantic.relevance_cut(selection, every)).sum())
        total += len(pos); found += hits; empty += hits == 0
    return found / max(1, total), empty


def metrics(emb, names, labels, queries, encoder, lang_index):
    r1 = r5 = mrr = p5 = ap_sum = 0.0
    times, misses = [], []
    for query in queries:
        need, text = query[0], query[lang_index]
        start = time.perf_counter()
        vector = semantic.corrected(encoder.encode_text(text), encoder.reference_vector())
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


def strong_set(emb, names, labels, queries, vectors):
    """(recall, precision, queries empty although right photos exist) of the 'strong' sets (background = this set)."""
    tp = fp = fn = empty = 0
    for q, vector in zip(queries, vectors):
        scores = emb @ vector
        cut = semantic.relevance_cut(scores, scores)
        strong = {names[i] for i in np.flatnonzero(scores >= cut)}
        rel = {n for n in names if q[0] <= labels[n]}
        tp += len(strong & rel); fp += len(strong - rel); fn += len(rel - strong)
        empty += (not strong) and bool(rel)
    return tp / (tp + fn), tp / max(1, tp + fp), empty


def strong_report(emb, names, labels, queries, encoder, index) -> None:
    """Precision and recall of the 'strong' set (the relevance cut of semantic.py)."""
    reference = encoder.reference_vector()
    vectors = [semantic.corrected(encoder.encode_text(q[index]), reference) for q in queries]
    recall, precision, empty = strong_set(emb, names, labels, queries, vectors)
    print(f"    strong set: recall {recall:.3f} precision {precision:.3f}; "
          f"queries empty although right photos exist: {empty}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", required=True, help="folder with the eval photos (<name>.jpg)")
    parser.add_argument("--models-folder", default=str(DEFAULT_MODELS_FOLDER))
    parser.add_argument("--device", default="cpu", choices=("cpu", "auto"))
    parser.add_argument("--thumbnails", action="store_true", help="embed the 300 px thumbnails, as the job does")
    parser.add_argument("--save-fixture", action="store_true",
                        help=f"store the photo and query vectors in {FIXTURE.relative_to(CODE_DIR)}")
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
    free, absent = load_extra()
    reference = encoder.reference_vector()
    for lang in ("nl", "en"):
        mine = [(need, None, text) for need, language, text in free if language == lang]
        vectors = [semantic.corrected(encoder.encode_text(q[2]), reference) for q in mine]
        recall, precision, empty = strong_set(emb, names, labels, mine, vectors)
        print(f"{lang} free-form ({len(mine)}): strong set recall {recall:.3f} precision {precision:.3f}; "
              f"empty although right photos exist: {empty}")
        gone = [semantic.corrected(encoder.encode_text(pair[0 if lang == "nl" else 1]), reference) for pair in absent]
        hit, photos = absent_let_through(emb, gone)
        print(f"{lang} absent ({len(absent)}): {hit} let something through ({photos} photos)")
        index = 1 if lang == "nl" else 2
        labelled = [semantic.corrected(encoder.encode_text(q[index]), reference) for q in queries]
        for share in (0.2, 0.5):
            recall, empty = folder_recall(emb, names, labels, queries, labelled, share)
            print(f"{lang} concept is {share:.0%} of the folder: recall {recall:.3f}, empty {empty}")
    if args.save_fixture:
        np.savez_compressed(
            FIXTURE, model=spec.id, names=np.array(names), photos=emb.astype(np.float16),
            nl=np.stack([encoder.encode_text(q[1]) for q in queries]).astype(np.float16),
            en=np.stack([encoder.encode_text(q[2]) for q in queries]).astype(np.float16),
            free=np.stack([encoder.encode_text(q[2]) for q in free]).astype(np.float16),
            absent_nl=np.stack([encoder.encode_text(a[0]) for a in absent]).astype(np.float16),
            absent_en=np.stack([encoder.encode_text(a[1]) for a in absent]).astype(np.float16),
            reference_texts=np.array(semantic.REFERENCE_TEXTS),
            reference=encoder.reference_vector().astype(np.float32))
        print(f"vectors stored in {FIXTURE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
