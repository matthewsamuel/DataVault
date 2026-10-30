"""
DataVault Dashboard - Slim zoeken (semantic search)

Find photos by what is on them ('hond op het strand', 'verjaardagstaart'), fully on
this Mac. A vision-language model turns every photo (and one frame of every video)
into a vector, and a search text into a vector in the same space; the photos whose
vectors point the same way as the text are the best matches.

The model: SigLIP 2 base, patch 16, 224 px (Google, Apache-2.0), multilingual: Dutch
and English queries both work without translation. Chosen after measuring five
candidates on a labelled set (code/scripts/eval_semantic_search.py and the README):
it ranked as well as the best other candidate on our set and is clearly better on the
public multilingual benchmark (Crossmodal-3600, Dutch). Runtime is onnxruntime and
tokenizers only (no torch):
- the image encoder is the fp32 ONNX export of immich-app (static batch 1); it runs on
  the GPU through Core ML when that works (about 65-75 images/s on an M3 Pro, against
  about 10 on the CPU), else on the CPU;
- the text encoder is the fp16 ONNX export of onnx-community (half the size, the same
  rankings as fp32 on our set; the int8 one loses most of its Dutch); it runs on the
  CPU in about 35 ms per query.
Images are encoded one at a time (the export has a static batch of 1); the measured
throughput above is enough, so no dynamic-batch re-export was made.
Both exports give the same vectors (cosine 0.99999 against each other).

Files: the model lives in config.MODELS_FOLDER/<model id> (code/models on the T7 by
default, 'models_folder' in config.json). The app never downloads anything by itself:
the 'Zoekmodel installeren' button in Instellingen starts the 'model_download' job
(or run code/scripts/download_models.py), which fetches the pinned files from Hugging
Face, checks every SHA-256 and only then marks the model as installed.

Embeddings (table semantic_embeddings, migration 40): the 'semantic' job embeds every
photo and video of the media index whose row has no embedding for the current model,
or whose size or mtime changed since. It reads the thumbnail (making it first when it
is missing, so the grid gains from it too): measured, a 300 px thumbnail gives the
same rankings as the original and is many times cheaper to decode than a 12 MP JPEG
or HEIC. Videos get their thumbnail frame (ffmpeg, at 1 s). A file that cannot be
decoded is recorded as 'undecodable' and skipped until it changes. The job is
resumable (what was written is not done again), cancellable and pausable, waits for
an unplugged vault, and runs automatically after the media index when the model is
installed (SemanticAuto, held while the user paused or cancelled it).

Search: the normalised vectors of the current model are kept in memory as float16
(VectorIndex; 100k photos x 768 = 150 MB) and loaded incrementally: a query reads
only the rows written since the last one. A query is one matrix product (in float32
chunks) plus the media index filters (media_index._where: kind, dates, folder,
camera, rating, orientation, ...). Results above a relevance cut are 'total'; the
less certain rest can be paged with include_weak.

For the assistant (step 3.8) and other Python callers, use search_photos():

    from backend import semantic
    hits = semantic.search_photos(db, config, "hond op het strand", filters={"kind": "photo"}, limit=20)
    # -> [{"id", "path", "name", "kind", "taken", "score", ...media row}, ...], best first
    # raises semantic.NotReady (with a Dutch message) when the model is not installed

Never moves, changes or deletes an archive file: it reads the archive and writes the
app's own database, thumbnail cache and models folder.
"""
import hashlib
import json
import os
import string
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from backend import media_index

EMBED_JOB = "semantic"
DOWNLOAD_JOB = "model_download"
STATE_KEY = "semantic"            # app_settings: {model, last_run_at, device, per_second, ...}
MARKER = "installed.json"         # in the model folder: what was verified, and when
PART_SUFFIX = ".part"
DOWNLOAD_CHUNK = 1024 * 1024
EMBED_BATCH = 32                  # embeddings written per transaction
FLUSH_SECONDS = 2.0
PROGRESS_SECONDS = 0.5
LOAD_WORKERS = 3                  # thumbnails read (or made) in parallel
CPU_THREADS = 4                   # onnxruntime threads on the CPU: leave room for the page
COREML_MIN_PENDING = 1000         # below this the CPU is done before Core ML has loaded (about 70 s; 4 min the first time)
TEXT_IDLE_SECONDS = 900.0         # the text model (about 1.7 GB in memory) is let go after this
QUERY_CACHE = 128                 # text queries whose vector is kept
RANKING_CACHE = 16                # rankings kept for paging
MAX_RANKED = 5000                 # a query never ranks more than this many photos
# Hub correction: some photos (busy, high-contrast, text-like) score a little higher for
# every text, and a Dutch text scores a little lower on every photo than its English
# twin. Subtracting HUB_ALPHA times the mean vector of REFERENCE_TEXTS (generic captions,
# none of them a concept people search for) from the query removes both: the rankings get
# better (mAP 0.924 -> 0.953 NL, 0.949 -> 0.966 EN on the labelled set) and the scores of
# both languages land on one scale, so one cut serves both. Scores below are on this
# corrected scale (the query is not normalised again).
HUB_ALPHA = 0.8
REFERENCE_TEXTS = (
    "a photo", "een foto", "a picture", "een afbeelding", "a photo of a person", "een foto van een persoon",
    "a photo of an object", "een voorwerp", "a room", "een kamer", "a street", "een straat", "a building",
    "een gebouw", "a screenshot", "een schermafbeelding", "a document", "een document", "a group of people",
    "een groep mensen", "a close-up", "een close-up", "outdoors", "buiten", "indoors", "binnen", "a landscape",
    "een landschap", "an animal", "een dier", "a plant", "een plant", "a vehicle", "een voertuig", "a drawing",
    "een tekening", "a selfie", "een selfie", "something", "iets", "a blurry photo", "een onscherpe foto",
    "the sky", "de lucht", "water", "water", "a table", "een tafel", "text", "tekst",
)
# The relevance cut (measured on the labelled set, scripts/eval_semantic_search.py
# --thumbnails, as the job embeds): a result is 'strong' when its score is at least
# MIN_SCORE, at most SCORE_SPREAD below the best one, and SCORE_Z standard deviations (MAD)
# above the median of the whole index (a query of nonsense lifts nothing out of the crowd).
# On the 179-photo set with 50 Dutch and 50 English queries the strong set holds 86% (NL) /
# 87% (EN) of the right photos at 69% / 75% precision, and no query with right photos
# comes up empty (before the hub correction: 77% / 80% at 62% / 72%). Of 24 queries for
# things that are not in the set, 3 let a photo through (4 photos; before: 4 and 12).
# tests/test_semantic_ranking.py checks these numbers on stored vectors of that set.
# The background (median and MAD) is taken over the whole index, not the filtered selection:
# when a concept is a fifth of a folder, the folder's own median would rise above the matches.
# A best score of at least CLEAR_SCORE is never emptied by the background rule.
MIN_SCORE = 0.045
SCORE_SPREAD = 0.055
SCORE_Z = 1.0
SCORE_Z_MIN_COUNT = 30            # with fewer photos the spread says too little
CLEAR_SCORE = 0.07
WEAK_FALLBACK = 5                 # search_photos: weaker hits returned when nothing is strong
# Image to image cosines sit on another scale (0.6-0.95): a near-copy must not set the cut
NEAR_COPY = 0.93
SIMILAR_SPREAD = 0.12
MAX_QUERY_CHARS = 200


class NotReady(Exception):
    """Slim zoeken cannot answer: the model is not installed (message is Dutch, for the user)."""


# ---------- the model ----------

@dataclass(frozen=True)
class ModelFile:
    path: str        # inside the model folder
    url: str         # pinned to a revision
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    licence: str
    homepage: str
    files: Tuple[ModelFile, ...]
    image_file: str
    text_file: str
    tokenizer_file: str
    dim: int
    image_size: int
    mean: Tuple[float, float, float]
    std: Tuple[float, float, float]
    context_length: int
    pad_token: str

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def sources(self) -> List[str]:
        return sorted({f.url.split("/resolve/")[0] for f in self.files})


_IMMICH = "https://huggingface.co/immich-app/ViT-B-16-SigLIP2__webli/resolve/5f3fc29104ebcf51e9d893af57604badd51a3ea6"
_ONNX_COMMUNITY = ("https://huggingface.co/onnx-community/siglip2-base-patch16-224-ONNX/resolve/"
                   "ba1f3b0843f24bc5417d38e19c37b287d719b2f4")

SIGLIP2_B16 = ModelSpec(
    id="siglip2-base-patch16-224",
    label="SigLIP 2 (meertalig)",
    licence="Apache-2.0 (Google SigLIP 2)",
    homepage="https://huggingface.co/google/siglip2-base-patch16-224",
    files=(
        ModelFile("visual.onnx", f"{_IMMICH}/visual/model.onnx", 371674292,
                  "fee10c729875dd203d94f396ac3d664e301d39c977ef6d92e7f467a2a716f0b0"),
        ModelFile("textual_fp16.onnx", f"{_ONNX_COMMUNITY}/onnx/text_model_fp16.onnx", 564862230,
                  "711da56ada0a4aa11c7dd3320df741081a3cae4f0ae1b5e5c6d5b294738d0eb0"),
        ModelFile("tokenizer.json", f"{_ONNX_COMMUNITY}/tokenizer.json", 34363039,
                  "cb9140fae3ac5122c972d37adf83e1248471a38147ad76f8215c8872c6fd8322"),
    ),
    image_file="visual.onnx",
    text_file="textual_fp16.onnx",
    tokenizer_file="tokenizer.json",
    dim=768,
    image_size=224,
    mean=(0.5, 0.5, 0.5),
    std=(0.5, 0.5, 0.5),
    context_length=64,
    pad_token="<pad>",
)

MODELS: Dict[str, ModelSpec] = {SIGLIP2_B16.id: SIGLIP2_B16}
DEFAULT_MODEL = SIGLIP2_B16.id


def current_model() -> ModelSpec:
    return MODELS[DEFAULT_MODEL]


def model_dir(config, spec: Optional[ModelSpec] = None) -> Path:
    spec = spec or current_model()
    return Path(config.MODELS_FOLDER) / spec.id


def _marker(folder: Path) -> Dict[str, Any]:
    try:
        value = json.loads((folder / MARKER).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def installed(config, spec: Optional[ModelSpec] = None) -> bool:
    """
    True when every file of the model is there as it was verified: the download wrote
    its size and mtime to installed.json after the SHA-256 check (three stats here).
    """
    spec = spec or current_model()
    folder = model_dir(config, spec)
    verified = _marker(folder).get("files") or {}
    for f in spec.files:
        try:
            st = (folder / f.path).stat()
        except OSError:
            return False
        if verified.get(f.path) != [f.size, st.st_mtime_ns] or st.st_size != f.size:
            return False
    return True


def model_info(config, spec: Optional[ModelSpec] = None) -> Dict[str, Any]:
    spec = spec or current_model()
    return {
        "id": spec.id,
        "label": spec.label,
        "licence": spec.licence,
        "homepage": spec.homepage,
        "sources": spec.sources,
        "size": spec.size,
        "folder": str(model_dir(config, spec)),
        "installed": installed(config, spec),
    }


def not_installed_message(config) -> str:
    return (f"Het zoekmodel is nog niet geïnstalleerd. Installeer het (eenmalig, ongeveer {_mb(current_model().size)} MB) "
            "in Instellingen > Slim zoeken, of met: venv/bin/python scripts/download_models.py")


# ---------- download (only when the user asks: the 'model_download' job) ----------

class DownloadRefused(OSError):
    """A model download that did not go out; the message is Dutch."""


def _open_url(url: str, start: int):
    """The response for url from byte start on (a Range request when start > 0)."""
    import urllib.request
    if os.environ.get("DATAVAULT_TESTING") == "1":
        # Like the DataGatherer guard: a test server never reaches the network (tests fake _open_url)
        raise DownloadRefused("Geen echte netwerkverbinding in testmodus")
    headers = {"User-Agent": "DataVault (model download on request)"}
    if start:
        headers["Range"] = f"bytes={start}-"
    import ssl
    try:   # the python.org Python has no system certificates of its own; certifi is a dependency
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60, context=context)


def _sha256_of(path: Path, stop: Callable[[], None] = lambda: None) -> "hashlib._Hash":
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(DOWNLOAD_CHUNK * 8)
            if not chunk:
                return digest
            digest.update(chunk)
            stop()


def _log_begin(db, url: str) -> Optional[int]:
    """
    The Netwerklog row of one file download, written before it goes out (source 'slim_zoeken',
    trigger 'user': the download is always a click). No row, no download.
    """
    if db is None:
        return None
    from urllib.parse import urlsplit
    from backend.datagatherer import net, state
    parts = urlsplit(url)
    try:
        return state.log_request(db, source_id=state.SEARCH_MODEL_SOURCE, trigger="user", method="GET",
                                 host=(parts.hostname or "?")[:255], path=parts.path[:300], outcome=net.PENDING)
    except Exception:
        raise DownloadRefused(net.LOG_UNAVAILABLE)


def _log_finish(db, row_id: Optional[int], outcome: str, **values) -> None:
    if db is None or row_id is None:
        return
    from backend.datagatherer import state
    try:
        state.finish_request(db, row_id, outcome=outcome, **values)
    except Exception:
        pass  # the download is in the log already (as 'pending'), only its outcome is missing


def download_model(config, spec: Optional[ModelSpec] = None, progress: Callable[[int, int, str], None] = None,
                   check: Callable[[], None] = lambda: None, db=None) -> Dict[str, Any]:
    """
    Fetch the model files that are missing (resuming a .part file with a Range request),
    check each SHA-256, put it in place, and write installed.json once all are there.
    A file whose checksum does not match is removed (only our own .part file) and the
    download fails with a Dutch message. check() raises to stop (cancel, pause): the
    .part file stays, so the next run continues where this one stopped.
    """
    spec = spec or current_model()
    progress = progress or (lambda done, total, message: None)
    folder = model_dir(config, spec)
    folder.mkdir(parents=True, exist_ok=True)
    marker = _marker(folder)
    verified = dict(marker.get("files") or {})
    total = spec.size
    done = 0
    fetched = 0
    for f in spec.files:
        target = folder / f.path
        try:
            st = target.stat()
        except OSError:
            st = None
        if st is not None and st.st_size == f.size and verified.get(f.path) == [f.size, st.st_mtime_ns]:
            done += f.size
            continue
        if st is not None:
            # There, but not verified (an older or interrupted install): check it as it is
            progress(done, total, f"{f.path} controleren...")
            if st.st_size == f.size and _sha256_of(target, check).hexdigest() == f.sha256:
                verified[f.path] = [f.size, target.stat().st_mtime_ns]
                done += f.size
                continue
            target.unlink()  # our own model file, wrong content: fetched again below
        part = target.with_name(target.name + PART_SUFFIX)
        start = part.stat().st_size if part.exists() else 0
        if start > f.size:
            part.unlink()
            start = 0
        digest = _sha256_of(part, check) if start else hashlib.sha256()
        if start < f.size:
            row_id = _log_begin(db, f.url)
            began = time.monotonic()
            received = start
            status = None
            try:
                with _open_url(f.url, start) as response:
                    status = getattr(response, "status", 200)
                    if start and status != 206:     # the server ignored the Range: start over
                        start = 0
                        digest = hashlib.sha256()
                    with open(part, "ab" if start else "wb") as out:
                        received = start
                        last = 0.0
                        while True:
                            check()
                            chunk = response.read(DOWNLOAD_CHUNK)
                            if not chunk:
                                break
                            out.write(chunk)
                            digest.update(chunk)
                            received += len(chunk)
                            fetched += len(chunk)
                            now = time.monotonic()
                            if now - last >= PROGRESS_SECONDS:
                                last = now
                                progress(done + received, total,
                                         f"Downloaden: {_mb(done + received)} van {_mb(total)} MB")
                        if received != f.size:
                            raise OSError(0, f"Download van {f.path} onvolledig ({received} van {f.size} bytes); "
                                             "probeer het opnieuw")
            except DownloadRefused as e:
                _log_finish(db, row_id, "refused", note=str(e))
                raise
            except OSError as e:
                code = getattr(e, "code", None)
                _log_finish(db, row_id, "http_error" if isinstance(code, int) else "error",
                            status=code if isinstance(code, int) else None, bytes=received - start,
                            ms=int((time.monotonic() - began) * 1000), note=type(e).__name__)
                raise
            except BaseException:
                # Stopped on purpose (pause, cancel): the connection itself worked
                _log_finish(db, row_id, "ok", status=status, bytes=received - start,
                            ms=int((time.monotonic() - began) * 1000), note="Gestopt")
                raise
            _log_finish(db, row_id, "ok", status=status, bytes=received - start,
                        ms=int((time.monotonic() - began) * 1000))
        if digest.hexdigest() != f.sha256:
            part.unlink()
            raise ValueError(f"Controlegetal van {f.path} klopt niet: het bestand is verwijderd. "
                             "Probeer het opnieuw.")
        os.replace(part, target)    # our own models folder, never the archive
        verified[f.path] = [f.size, target.stat().st_mtime_ns]
        done += f.size
        (folder / MARKER).write_text(json.dumps({"model": spec.id, "files": verified}, indent=1))
        progress(done, total, f"{f.path} klaar")
    (folder / MARKER).write_text(json.dumps({"model": spec.id, "files": verified,
                                             "verified_at": time.time()}, indent=1))
    forget_encoder()
    return {"model": spec.id, "size": total, "downloaded": fetched, "folder": str(folder),
            "installed": installed(config, spec)}


def _mb(n: int) -> str:
    """Megabytes as the page shows them (formatBytes in app.js: 1 MB = 1024 x 1024 bytes)."""
    return f"{n / 1024 / 1024:,.0f}".replace(",", ".")


# ---------- the encoder ----------

_PUNCTUATION = str.maketrans("", "", string.punctuation)


def clean_query(text: str) -> str:
    """What the model was trained on: one space between words, no punctuation, lower case."""
    return " ".join(str(text).split()).translate(_PUNCTUATION).lower().strip()


class Encoder:
    """
    The two ONNX sessions of a model, each loaded on first use. image_session(device)
    tries Core ML on the GPU (its compiled model is cached next to the model, so only
    the first start takes minutes) and falls back to the CPU. Thread-safe.
    """

    def __init__(self, spec: ModelSpec, folder: Path):
        self.spec = spec
        self.folder = Path(folder)
        self._lock = threading.Lock()
        self._text = None
        self._tokenizer = None
        self._image = None
        self.image_device: Optional[str] = None
        self.text_used = 0.0
        self._reference: Optional[np.ndarray] = None
        self._reference_lock = threading.Lock()

    def _options(self):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = CPU_THREADS
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        return options

    def text_ready(self) -> bool:
        return self._text is not None

    def load_text(self) -> None:
        with self._lock:
            if self._text is not None:
                return
            import onnxruntime as ort
            from tokenizers import Tokenizer
            tokenizer = Tokenizer.from_file(str(self.folder / self.spec.tokenizer_file))
            pad = self.spec.pad_token
            tokenizer.enable_padding(length=self.spec.context_length, pad_token=pad, pad_id=tokenizer.token_to_id(pad))
            tokenizer.enable_truncation(max_length=self.spec.context_length)
            self._text = ort.InferenceSession(str(self.folder / self.spec.text_file), self._options(),
                                              providers=["CPUExecutionProvider"])
            self._tokenizer = tokenizer
            self.text_used = time.monotonic()

    def unload_text(self, idle: float = 0.0) -> bool:
        """Let the text model go when it was not used for idle seconds (it is loaded again on demand)."""
        with self._lock:
            if self._text is None or time.monotonic() - self.text_used < idle:
                return False
            self._text = None
            self._tokenizer = None
            return True

    def unload_image(self) -> None:
        with self._lock:
            self._image = None
            self.image_device = None

    def encode_text(self, text: str) -> np.ndarray:
        self.load_text()
        self.text_used = time.monotonic()
        ids = np.array([self._tokenizer.encode(clean_query(text)).ids], dtype=np.int64)
        vector = self._text.run(["pooler_output"], {"input_ids": ids})[0][0]
        return _normalised(vector)

    def reference_vector(self) -> np.ndarray:
        """The mean vector of REFERENCE_TEXTS (the hub correction), worked out once (about 1 s)."""
        with self._reference_lock:
            if self._reference is None:
                self._reference = np.mean([self.encode_text(t) for t in REFERENCE_TEXTS], axis=0).astype(np.float32)
            return self._reference

    def reference_ready(self) -> bool:
        return self._reference is not None

    def load_image(self, device: str = "auto") -> str:
        """Load the image session; device 'auto' (Core ML, else CPU) or 'cpu'. Returns the device used."""
        with self._lock:
            if self._image is not None and (device == "auto" or self.image_device == device):
                return self.image_device
            import onnxruntime as ort
            path = str(self.folder / self.spec.image_file)
            session = None
            if device == "auto" and "CoreMLExecutionProvider" in ort.get_available_providers():
                cache = self.folder / "coreml-cache"
                try:
                    cache.mkdir(exist_ok=True)
                    session = ort.InferenceSession(path, self._options(), providers=[
                        ("CoreMLExecutionProvider", {"ModelFormat": "MLProgram", "MLComputeUnits": "CPUAndGPU",
                                                     "ModelCacheDirectory": str(cache)}),
                        "CPUExecutionProvider"])
                    self.image_device = "gpu"
                except Exception:
                    session = None
            if session is None:
                session = ort.InferenceSession(path, self._options(), providers=["CPUExecutionProvider"])
                self.image_device = "cpu"
            self._image = session
            return self.image_device

    def preprocess(self, img) -> np.ndarray:
        """PIL image -> (3, size, size) float32: squashed to the model's square, as in training."""
        from PIL import Image
        size = self.spec.image_size
        img = img.convert("RGB").resize((size, size), Image.Resampling.BICUBIC)
        array = np.asarray(img, dtype=np.float32) / 255.0
        array = (array - np.array(self.spec.mean, np.float32)) / np.array(self.spec.std, np.float32)
        return array.transpose(2, 0, 1)

    def encode_image(self, pixels: np.ndarray) -> np.ndarray:
        if self._image is None:
            self.load_image()
        vector = self._image.run(None, {"image": pixels[None]})[0][0]
        return _normalised(vector)


def _normalised(vector) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


_encoders: Dict[Tuple[str, str], Encoder] = {}
_encoders_lock = threading.Lock()
_encoder_factory: Optional[Callable[[Any, ModelSpec], Encoder]] = None


def set_encoder_factory(factory: Optional[Callable[[Any, ModelSpec], Any]]) -> None:
    """Tests replace the real model with a fake encoder (same methods as Encoder)."""
    global _encoder_factory
    _encoder_factory = factory
    forget_encoder()


def get_encoder(config, spec: Optional[ModelSpec] = None):
    """The encoder of the installed model; raises NotReady when it is not installed."""
    spec = spec or current_model()
    if _encoder_factory is not None:
        key = ("fake", spec.id)
    else:
        if not installed(config, spec):
            raise NotReady(not_installed_message(config))
        key = (str(model_dir(config, spec)), spec.id)
    with _encoders_lock:
        encoder = _encoders.get(key)
        if encoder is None:
            encoder = _encoder_factory(config, spec) if _encoder_factory else Encoder(spec, model_dir(config, spec))
            _encoders[key] = encoder
        return encoder


def forget_encoder() -> None:
    with _encoders_lock:
        _encoders.clear()
    _text_cache.clear()


def unload_idle(idle: float = TEXT_IDLE_SECONDS) -> None:
    """Free the text model of every encoder that was not used for idle seconds."""
    with _encoders_lock:
        encoders = list(_encoders.values())
    for encoder in encoders:
        if hasattr(encoder, "unload_text"):
            encoder.unload_text(idle)


def model_ready(config) -> bool:
    return _encoder_factory is not None or installed(config)


# ---------- embedding the library (the 'semantic' job) ----------

_generation = 0
_generation_lock = threading.Lock()


def _bump() -> None:
    global _generation
    with _generation_lock:
        _generation += 1


def generation() -> int:
    return _generation


def state(db) -> Dict[str, Any]:
    try:
        value = db.get_setting(STATE_KEY, {})
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


_PENDING_SQL = """
    FROM media m LEFT JOIN semantic_embeddings e ON e.media_id = m.id AND e.model = ?
    WHERE m.missing = 0 AND m.kind IN ('photo', 'video')
      AND (e.media_id IS NULL OR e.size != m.size OR e.mtime_ns != m.mtime_ns)
"""


_counts_cache: Dict[str, Any] = {}
COUNTS_SECONDS = 2.0


def counts(db, spec: Optional[ModelSpec] = None, fresh: bool = False) -> Dict[str, int]:
    """
    How far the library is: photos and videos, how many have an embedding, how many wait.
    (A scan of the index: cached for COUNTS_SECONDS while nothing changed, for the
    answers of the search routes; fresh=True always counts.)
    """
    spec = spec or current_model()
    key = (spec.id, _generation, media_index.generation(), id(db))
    cached = _counts_cache.get("value")
    if not fresh and cached and _counts_cache.get("key") == key and time.monotonic() - _counts_cache["at"] < COUNTS_SECONDS:
        return dict(cached)
    value = _count(db, spec)
    _counts_cache.update(key=key, value=value, at=time.monotonic())
    return dict(value)


def _count(db, spec: ModelSpec) -> Dict[str, int]:
    with db._connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM media WHERE missing = 0 AND kind IN ('photo', 'video')").fetchone()[0]
        pending = conn.execute(f"SELECT COUNT(*) {_PENDING_SQL}", (spec.id,)).fetchone()[0]
        undecodable = conn.execute(
            "SELECT COUNT(*) FROM semantic_embeddings e JOIN media m ON m.id = e.media_id "
            "WHERE e.model = ? AND e.status != 'ok' AND m.missing = 0 AND e.size = m.size AND e.mtime_ns = m.mtime_ns",
            (spec.id,)).fetchone()[0]
    return {"total": total, "pending": pending, "undecodable": undecodable,
            "embedded": total - pending - undecodable}


def purge_orphans(db) -> int:
    """Remove the embeddings whose media row is gone (the index removed it)."""
    with db._connect() as conn:
        cursor = conn.execute("DELETE FROM semantic_embeddings WHERE media_id NOT IN (SELECT id FROM media)")
        removed = cursor.rowcount or 0
    if removed:
        _index.reset()      # SQLite hands out the freed rowids again: reload from scratch
        _bump()
    return removed


def _pending_rows(db, spec: ModelSpec) -> List[Tuple]:
    """(id, path, kind, size, mtime_ns) of what still needs an embedding, newest photos first."""
    with db._connect() as conn:
        return conn.execute(
            f"SELECT m.id, m.path, m.kind, m.size, m.mtime_ns {_PENDING_SQL} "
            "ORDER BY m.taken IS NULL, m.taken DESC, m.id", (spec.id,)).fetchall()


class _Skip(Exception):
    """A file that gets no embedding this time and is not an error (a video without ffmpeg)."""


def load_pixels(encoder, config, path: str, kind: str) -> Tuple[np.ndarray, str]:
    """
    The model input for one file, from its thumbnail: the cached one when it is there,
    else made now (and kept, so the grid has it too). Raises thumbnail.Undecodable for a
    file that is not an image or video it can read, OSError for a read error, _Skip for
    a video when ffmpeg is missing. Returns (pixels, 'thumb' | 'new_thumb').
    """
    from PIL import Image
    from backend import thumbnail
    file_path = Path(path)
    cache_path = thumbnail.get_thumbnail_cache_path(file_path, config.THUMB_CACHE_PATH)
    source = "thumb"
    if not thumbnail.is_thumbnail_valid(cache_path, file_path) or thumbnail.is_placeholder(cache_path):
        source = "new_thumb"
        if kind == "video":
            if not thumbnail.have_ffmpeg():
                raise _Skip("ffmpeg ontbreekt")
            thumbnail.render_video_thumbnail(file_path, cache_path)
        else:
            thumbnail.render_photo_thumbnail(file_path, cache_path)
    with Image.open(cache_path) as img:
        img.load()
        return encoder.preprocess(img), source


def _write(db, spec: ModelSpec, rows: List[Tuple]) -> None:
    """rows: (media_id, vector or None, status, size, mtime_ns, source)."""
    if not rows:
        return
    now = time.time()
    values = [(mid, spec.id, spec.dim, None if vec is None else vec.astype(np.float16).tobytes(),
               status, size, mtime_ns, source, now) for mid, vec, status, size, mtime_ns, source in rows]
    with db._connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO semantic_embeddings "
            "(media_id, model, dim, vector, status, size, mtime_ns, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
    _bump()


def run_embedding(ctx, config, db, errors, vault_check: Callable[[], None]) -> Dict[str, Any]:
    """
    Embed every photo and video that has no (current) embedding. Resume needs nothing of
    its own: what was written is not pending any more. errors is a job error log
    (job_types._JobErrors); vault_check() raises VaultUnavailable when the vault is gone.
    """
    from backend import scanner, thumbnail
    from backend.jobs import bounded_map
    spec = current_model()
    started = time.monotonic()
    purge_orphans(db)
    pending = _pending_rows(db, spec)
    total = len(pending)
    counts_ = {"embedded": 0, "undecodable": 0, "skipped": 0, "made_thumbs": 0}
    undecodable = scanner._ErrorLog()
    device = None
    if total:
        encoder = get_encoder(config, spec)
        ctx.progress(0, total, "Zoekmodel laden..." if total < COREML_MIN_PENDING
                     else "Zoekmodel voorbereiden (de eerste keer duurt dit een paar minuten)...")
        device = encoder.load_image("auto" if total >= COREML_MIN_PENDING else "cpu")
        ctx.check()
        loaded = time.monotonic()      # the speed is counted from here: loading is not recognising
        config.ensure_cache_dirs()
        buffer: List[Tuple] = []
        last_flush = last_progress = time.monotonic()
        processed = 0
        stop = lambda: ctx.stopping or errors.vault_lost  # noqa: E731
        load = lambda row: load_pixels(encoder, config, row[1], row[2])  # noqa: E731
        try:
            for row, result, error in bounded_map(load, pending, workers=LOAD_WORKERS, stop=stop):
                processed += 1
                media_id, path, kind, size, mtime_ns = row
                if error is None:
                    pixels, source = result
                    buffer.append((media_id, encoder.encode_image(pixels), "ok", size, mtime_ns, source))
                    counts_["embedded"] += 1
                    counts_["made_thumbs"] += source == "new_thumb"
                elif isinstance(error, _Skip):
                    counts_["skipped"] += 1
                elif isinstance(error, thumbnail.Undecodable) and not errors.vault_lost and config.VAULT_ROOT.is_dir():
                    undecodable(path, error)
                    buffer.append((media_id, None, "undecodable", size, mtime_ns, None))
                    counts_["undecodable"] += 1
                else:
                    errors(path, error)
                now = time.monotonic()
                if len(buffer) >= EMBED_BATCH or now - last_flush >= FLUSH_SECONDS:
                    _write(db, spec, buffer)
                    buffer = []
                    last_flush = now
                if now - last_progress >= PROGRESS_SECONDS or processed == total:
                    last_progress = now
                    rate = counts_["embedded"] / max(0.001, now - loaded)
                    ctx.progress(processed, total, f"Foto's herkennen... {_n(processed)}/{_n(total)} "
                                                   f"({rate:.0f} per seconde)")
                ctx.check()
        finally:
            _write(db, spec, buffer)
            if hasattr(encoder, "unload_image"):
                encoder.unload_image()   # the image model is only needed by this job
        vault_check()
    seconds = round(time.monotonic() - started, 2)
    working = time.monotonic() - loaded if total else 0.0
    result = {
        "model": spec.id,
        "files": total,
        "embedded": counts_["embedded"],
        "made_thumbs": counts_["made_thumbs"],
        "undecodable": counts_["undecodable"],
        "undecodable_samples": undecodable.samples,
        "skipped": counts_["skipped"],
        "errors": errors.count,
        "error_samples": errors.samples,
        "complete": errors.count == 0,
        "device": device,
        "seconds": seconds,
        "load_seconds": round(loaded - started, 2) if total else 0.0,
        "per_second": round(counts_["embedded"] / working, 1) if working and counts_["embedded"] else None,
    }
    info = {**state(db), "model": spec.id, "last_run_at": time.time(), "device": device,
            "per_second": result["per_second"], "last_seconds": seconds}
    if result["complete"]:
        info["last_complete_at"] = info["last_run_at"]
    db.set_setting(STATE_KEY, info)
    return result


def run_message(result: Dict[str, Any]) -> str:
    parts = [f"Klaar: {_n(result['embedded'])} foto's en video's herkend"]
    if result.get("undecodable"):
        parts.append(f"{_n(result['undecodable'])} onleesbaar")
    if result.get("skipped"):
        parts.append(f"{_n(result['skipped'])} video('s) overgeslagen (ffmpeg ontbreekt)")
    if result.get("errors"):
        parts.append(f"{_n(result['errors'])} mislukt (volgende keer opnieuw)")
    if result.get("per_second"):
        parts.append(f"{result['per_second']:.0f} per seconde")
    return "; ".join(parts)


def _n(value: int) -> str:
    return f"{value:,}".replace(",", ".")


# ---------- the search index (in memory) ----------

class VectorIndex:
    """
    Every 'ok' embedding of one model, as float16 rows with their media ids. refresh()
    reads only the rows written since the last call (INSERT OR REPLACE gives a changed
    row a new rowid), so after the first load a query costs almost nothing extra. A
    media id that comes again overwrites its row; one that turned undecodable is blanked.
    """

    CHUNK = 32768

    def __init__(self):
        self._lock = threading.Lock()
        self.model: Optional[str] = None
        self.dim = 0
        self.ids = np.zeros(0, dtype=np.int64)
        self.vectors = np.zeros((0, 0), dtype=np.float16)
        self.count = 0
        self._row_of: Dict[int, int] = {}
        self._last_rowid = 0
        self._generation = -1

    def reset(self) -> None:
        with self._lock:
            self.model = None
            self.count = 0
            self._row_of = {}
            self._last_rowid = 0
            self._generation = -1

    def refresh(self, db, spec: ModelSpec) -> None:
        with self._lock:
            if self.model != spec.id or self.dim != spec.dim:
                self.model, self.dim = spec.id, spec.dim
                self.ids = np.zeros(1024, dtype=np.int64)
                self.vectors = np.zeros((1024, spec.dim), dtype=np.float16)
                self.count, self._row_of, self._last_rowid, self._generation = 0, {}, 0, -1
            if self._generation == _generation:
                return
            seen = _generation
            with db._connect() as conn:
                while True:
                    rows = conn.execute(
                        "SELECT rowid, media_id, vector FROM semantic_embeddings "
                        "WHERE model = ? AND rowid > ? ORDER BY rowid LIMIT ?",
                        (spec.id, self._last_rowid, self.CHUNK)).fetchall()
                    if not rows:
                        break
                    self._add(rows, spec.dim)
                    self._last_rowid = rows[-1][0]
            self._generation = seen

    def _add(self, rows, dim: int) -> None:
        width = dim * 2
        good = [(media_id, blob) for _, media_id, blob in rows if blob is not None and len(blob) == width]
        for _, media_id, blob in rows:      # turned undecodable: blank its old row
            if (blob is None or len(blob) != width) and media_id in self._row_of:
                row = self._row_of[media_id]
                self.ids[row] = -1
                self.vectors[row] = 0
        if not good:
            return
        need = self.count + len(good)
        if need > len(self.ids):
            size = max(need, len(self.ids) * 2)
            ids = np.zeros(size, dtype=np.int64)
            ids[:self.count] = self.ids[:self.count]
            vectors = np.zeros((size, dim), dtype=np.float16)
            vectors[:self.count] = self.vectors[:self.count]
            self.ids, self.vectors = ids, vectors
        # One copy for the whole chunk: a row per media id (a known id keeps its row)
        block = np.frombuffer(b"".join(blob for _, blob in good), dtype=np.float16).reshape(len(good), dim)
        rows_at = np.empty(len(good), dtype=np.int64)
        for k, (media_id, _) in enumerate(good):
            row = self._row_of.get(media_id)
            if row is None:
                row = self._row_of[media_id] = self.count
                self.count += 1
            rows_at[k] = row
        self.ids[rows_at] = [media_id for media_id, _ in good]
        self.vectors[rows_at] = block

    def vector_of(self, media_id: int) -> Optional[np.ndarray]:
        with self._lock:
            row = self._row_of.get(int(media_id))
            if row is None or self.ids[row] != media_id:
                return None
            return self.vectors[row].astype(np.float32)

    def scores(self, query: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """(ids, cosine scores) of every row, in float32 chunks (float16 matmul is slow in numpy)."""
        with self._lock:
            n = self.count
            ids = self.ids[:n].copy()
            out = np.empty(n, dtype=np.float32)
            q = query.astype(np.float32)
            for start in range(0, n, self.CHUNK):
                end = min(n, start + self.CHUNK)
                out[start:end] = self.vectors[start:end].astype(np.float32) @ q
        return ids, out


_index = VectorIndex()
_text_cache: "OrderedDict[str, np.ndarray]" = OrderedDict()
_text_lock = threading.Lock()
_rankings: "OrderedDict[Tuple, Dict[str, Any]]" = OrderedDict()
_rankings_lock = threading.Lock()


def vector_index() -> VectorIndex:
    return _index


def text_vector(config, text: str) -> np.ndarray:
    """The (cached) vector of a search text."""
    key = clean_query(text)
    with _text_lock:
        if key in _text_cache:
            _text_cache.move_to_end(key)
            return _text_cache[key]
    vector = get_encoder(config).encode_text(text)
    with _text_lock:
        _text_cache[key] = vector
        while len(_text_cache) > QUERY_CACHE:
            _text_cache.popitem(last=False)
    return vector


def _reference(encoder) -> Optional[np.ndarray]:
    """The hub correction of an encoder (None for a test encoder without one)."""
    fn = getattr(encoder, "reference_vector", None)
    return fn() if fn is not None else None


def corrected(vector: np.ndarray, reference: Optional[np.ndarray]) -> np.ndarray:
    """A text vector minus the hub correction (see HUB_ALPHA): the vector that is ranked with."""
    if reference is None:
        return vector
    return (vector - HUB_ALPHA * reference).astype(np.float32)


def query_vector(config, text: str) -> np.ndarray:
    """The vector a search text is ranked with."""
    vector = text_vector(config, text)
    return corrected(vector, _reference(get_encoder(config)))


def warm_up(config) -> bool:
    """Load the text model (and its hub correction) ahead of the first query (the search window opens)."""
    if not model_ready(config):
        return False
    encoder = get_encoder(config)
    if hasattr(encoder, "load_text"):
        encoder.load_text()
    _reference(encoder)
    return True


def text_loaded(config) -> bool:
    """True when a text query will not wait for the model to load (for 'Zoekmodel laden...')."""
    if _encoder_factory is not None:
        return True
    spec = current_model()
    with _encoders_lock:
        encoder = _encoders.get((str(model_dir(config, spec)), spec.id))
    return encoder is not None and encoder.text_ready() and encoder.reference_ready()


_allowed_cache: "OrderedDict[Tuple, np.ndarray]" = OrderedDict()
_allowed_lock = threading.Lock()


def _allowed_ids(db, filters: Dict[str, Any]) -> np.ndarray:
    """The media ids the filters allow (sorted), cached until the index changes."""
    key = (_filters_key(filters), media_index.generation())
    with _allowed_lock:
        if key in _allowed_cache:
            _allowed_cache.move_to_end(key)
            return _allowed_cache[key]
    where, args = media_index._where({"kind": ["photo", "video"], **filters})
    with db._connect() as conn:
        rows = conn.execute(f"SELECT m.id FROM media m WHERE {where}", args).fetchall()
    ids = np.sort(np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows)))
    with _allowed_lock:
        _allowed_cache[key] = ids
        while len(_allowed_cache) > RANKING_CACHE:
            _allowed_cache.popitem(last=False)
    return ids


def _filters_key(filters: Dict[str, Any]) -> str:
    return json.dumps(filters, sort_keys=True, default=str)


def relevance_cut(scores: np.ndarray, background: np.ndarray, spread: float = SCORE_SPREAD,
                  best: Optional[float] = None) -> float:
    """
    The score a result needs to be 'strong' (see MIN_SCORE above): scores are those of the
    selection, background those of the whole index; best (default: the best score) is
    where the spread is counted from.
    """
    cut = MIN_SCORE
    if not len(scores):
        return cut
    top = float(scores.max())
    cut = max(cut, (top if best is None else best) - spread)
    if len(background) >= SCORE_Z_MIN_COUNT:
        median = float(np.median(background))
        mad = float(np.median(np.abs(background - median))) * 1.4826
        z_cut = max(cut, median + SCORE_Z * mad)
        if top >= CLEAR_SCORE and z_cut > top:
            z_cut = cut      # a clear match is never hidden by a crowded background
        cut = z_cut
    return cut


def rank(db, config, key: Tuple, vector_fn: Callable[[], np.ndarray], filters: Dict[str, Any],
         exclude: Optional[int] = None, keep_stale: bool = False) -> Dict[str, Any]:
    """
    {ids, scores, strong}: the best MAX_RANKED media ids for a query vector within the
    filters, best first; strong is how many pass the relevance cut. Cached per query,
    filters and index state, so paging does not rank again (keep_stale: a later page keeps
    the ranking of the first page while the index grows, so pages do not shift).
    """
    spec = current_model()
    _index.refresh(db, spec)
    cache_key = (spec.id, key, _filters_key(filters), exclude)
    state_key = (_generation, media_index.generation())
    with _rankings_lock:
        cached = _rankings.get(cache_key)
        if cached is not None and (keep_stale or cached["state"] == state_key):
            _rankings.move_to_end(cache_key)
            return cached
    query = vector_fn()
    ids, scores = _index.scores(query)
    keep = ids >= 0
    background = scores[keep]
    allowed = _allowed_ids(db, filters)
    keep &= np.isin(ids, allowed)
    if exclude is not None:
        keep &= ids != exclude
    ids, scores = ids[keep], scores[keep]
    if key[0] == "similar":
        apart = scores[scores < NEAR_COPY]
        cut = relevance_cut(scores, background, SIMILAR_SPREAD, float(apart.max()) if len(apart) else None)
    else:
        cut = relevance_cut(scores, background)
    if len(ids) > MAX_RANKED:
        top = np.argpartition(-scores, MAX_RANKED - 1)[:MAX_RANKED]
        ids, scores = ids[top], scores[top]
    order = np.argsort(-scores, kind="stable")
    ids, scores = ids[order], scores[order]
    strong = int(np.searchsorted(-scores, -cut, side="right")) if len(scores) else 0
    ranking = {"ids": ids, "scores": scores, "strong": strong, "state": state_key}
    with _rankings_lock:
        _rankings[cache_key] = ranking
        while len(_rankings) > RANKING_CACHE:
            _rankings.popitem(last=False)
    return ranking


def _rows_by_id(db, ids: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    if not len(ids):
        return {}
    columns = ", ".join(f"m.{c}" for c in media_index.ROW_COLUMNS)
    with db._connect() as conn:
        rows = conn.execute(
            f"SELECT {columns}, r.rating, r.votes FROM media m LEFT JOIN file_ratings r ON r.path = m.path "
            f"WHERE m.missing = 0 AND m.id IN ({', '.join('?' * len(ids))})", [int(i) for i in ids]).fetchall()
    return {row[0]: media_index._item(row) for row in rows}


def page(db, ranking: Dict[str, Any], offset: int, limit: int, include_weak: bool) -> Dict[str, Any]:
    """One page of a ranking, as media rows with their score."""
    end_all = len(ranking["ids"]) if include_weak else ranking["strong"]
    ids = ranking["ids"][offset:min(offset + limit, end_all)]
    scores = ranking["scores"][offset:offset + len(ids)]
    rows = _rows_by_id(db, ids)
    items = []
    for media_id, score in zip(ids.tolist(), scores.tolist()):
        row = rows.get(media_id)
        if row is not None:   # hidden since (moved to TRASH): left out
            items.append({**row, "score": round(score, 4)})
    next_offset = offset + len(ids)
    return {
        "total": ranking["strong"],
        "ranked": len(ranking["ids"]),
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset if next_offset < end_all else None,
        "items": items,
    }


def search_text(db, config, text: str, filters: Optional[Dict[str, Any]] = None, offset: int = 0,
                limit: int = 60, include_weak: bool = False) -> Dict[str, Any]:
    """Photos and videos that match a text (Dutch, English, ...), best first. Raises NotReady."""
    text = str(text or "").strip()[:MAX_QUERY_CHARS]
    filters = dict(filters or {})
    if not model_ready(config):
        raise NotReady(not_installed_message(config))
    ranking = rank(db, config, ("text", clean_query(text)), lambda: query_vector(config, text), filters,
                   keep_stale=offset > 0)
    return page(db, ranking, offset, limit, include_weak)


def search_similar(db, config, media_id: int, filters: Optional[Dict[str, Any]] = None, offset: int = 0,
                   limit: int = 60, include_weak: bool = False) -> Dict[str, Any]:
    """
    Photos and videos that look like this one (image to image), best first. Raises
    NotReady without a model, LookupError when the photo has no embedding (yet).
    """
    filters = dict(filters or {})
    if not model_ready(config):
        raise NotReady(not_installed_message(config))
    _index.refresh(db, current_model())
    vector = _index.vector_of(media_id)
    if vector is None:
        raise LookupError("Deze foto is nog niet herkend")
    ranking = rank(db, config, ("similar", int(media_id)), lambda: vector, filters, exclude=int(media_id),
                   keep_stale=offset > 0)
    return page(db, ranking, offset, limit, include_weak)


def search_photos(db, config, text: str, filters: Optional[Dict[str, Any]] = None,
                  limit: int = 20) -> List[Dict[str, Any]]:
    """
    For the assistant and other Python callers: the best matching photos and videos for a
    text as media rows (media_index.ROW_COLUMNS plus 'score'), best first, at most limit,
    only those above the relevance cut (when nothing is above it, the best few weaker hits,
    so a short query like 'food' never comes back empty). filters are media_index filters (kind, date_from,
    date_to, folder, camera, has_gps, min_rating, orientation, ...). Raises NotReady.
    """
    result = search_text(db, config, text, filters, offset=0, limit=limit)
    if not result["items"] and result["total"] == 0 and result["ranked"]:
        result = search_text(db, config, text, filters, offset=0, limit=min(limit, WEAK_FALLBACK),
                             include_weak=True)
    return result["items"]


def media_id_for_path(db, path: str) -> Optional[int]:
    with db._connect() as conn:
        row = conn.execute("SELECT id FROM media WHERE path = ? AND missing = 0", (str(path),)).fetchone()
    return row[0] if row else None


def search_filename(db, text: str, filters: Optional[Dict[str, Any]] = None, offset: int = 0,
                    limit: int = 60) -> Dict[str, Any]:
    """Files whose name contains text (case-insensitive), newest first: 'Bestandsnaam' mode."""
    import unicodedata
    needle = unicodedata.normalize("NFC", str(text or "").strip())[:MAX_QUERY_CHARS]
    where, args = media_index._where({"kind": ["photo", "video"], **(filters or {})})
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    where += " AND casefold(m.name) LIKE ? ESCAPE '\\'"
    args = [*args, f"%{escaped.casefold()}%"]
    columns = ", ".join(f"m.{c}" for c in media_index.ROW_COLUMNS)
    with db._connect() as conn:
        conn.create_function("casefold", 1, lambda v: v.casefold() if isinstance(v, str) else v)
        total = conn.execute(f"SELECT COUNT(*) FROM media m WHERE {where}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT {columns}, r.rating, r.votes FROM media m LEFT JOIN file_ratings r ON r.path = m.path "
            f"WHERE {where} ORDER BY m.taken IS NULL, m.taken DESC, m.path LIMIT ? OFFSET ?",
            [*args, int(limit), int(offset)]).fetchall()
    items = [media_index._item(row) for row in rows]
    next_offset = offset + len(items)
    return {"total": total, "ranked": total, "offset": offset, "limit": limit,
            "next_offset": next_offset if next_offset < total else None, "items": items}


# ---------- automatic runs ----------

AUTO_POLL_SECONDS = 30.0


class SemanticAuto:
    """
    Keeps the embeddings up to date while the app runs: when the model is installed,
    no index pass or embedding job is active, and photos are waiting, it starts an
    automatic 'semantic' job. Like the AutoIndexer it is held while the newest such job
    was paused or cancelled by the user since the app started (a job the user starts
    ends the hold).
    """

    def __init__(self, manager, config, db, poll: float = AUTO_POLL_SECONDS):
        self.manager = manager
        self.config = config
        self.db = db
        self.poll = poll
        self._since = time.time()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="semantic-auto", daemon=True)
        self._thread.start()

    def stop(self, wait: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(wait)

    def request(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        self._stop.wait(5.0)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            unload_idle()
            self._wake.wait(self.poll)
            self._wake.clear()

    def held(self, jobs: List[Dict[str, Any]]) -> bool:
        """True when the user paused or cancelled the newest embedding job since the app started."""
        from backend.jobs import CANCELLED, PAUSED
        latest = jobs[0] if jobs else None
        if latest is None:
            return False
        if latest["status"] == PAUSED:
            return (latest.get("updated_at") or 0) >= self._since
        if latest["status"] == CANCELLED:
            return (latest.get("finished_at") or 0) >= self._since
        return False

    def tick(self) -> bool:
        from backend.jobs import ACTIVE
        if not self.db.ready or not self.config.VAULT_ROOT.is_dir() or not model_ready(self.config):
            return False
        if not media_index.usable(self.db):
            return False
        for job in self.manager.list(limit=5, job_type=media_index.INDEX_JOB):
            if job["status"] in ACTIVE:
                return False
        jobs = self.manager.list(limit=10, job_type=EMBED_JOB)
        if any(job["status"] in ACTIVE for job in jobs) or self.held(jobs):
            return False
        if counts(self.db, fresh=True)["pending"] == 0:
            return False
        from backend.jobs import INTERRUPTED, PAUSED, JobError
        try:
            self.manager.start(EMBED_JOB, {"auto": True}, exclusive=True)
        except Exception:
            return False
        # An automatic job that a restart interrupted (or that was paused before the app
        # started) is replaced by this one, which does what it left
        for job in jobs:
            if (job.get("params") or {}).get("auto") and job["status"] in (INTERRUPTED, PAUSED):
                try:
                    self.manager.cancel(job["id"])
                except JobError:
                    pass
        return True


# ---------- job types ----------

def build_job_types(config, db) -> List[Any]:
    """The 'semantic' (embed the library) and 'model_download' (install the model) jobs."""
    from fastapi import HTTPException
    from backend.job_types import _JobErrors
    from backend.jobs import JobType, VaultUnavailable

    def prepare_embed(params: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(params.get("auto", False), bool):
            raise HTTPException(status_code=400, detail="auto moet true of false zijn")
        if not model_ready(config):
            raise HTTPException(status_code=409, detail=not_installed_message(config))
        return {"auto": params.get("auto", False)}

    def run_embed(ctx) -> Dict[str, Any]:
        if not config.VAULT_ROOT.is_dir():
            raise VaultUnavailable()
        errors = _JobErrors(config)

        def vault_check() -> None:
            if errors.vault_lost or not config.VAULT_ROOT.is_dir():
                raise VaultUnavailable()

        result = run_embedding(ctx, config, db, errors, vault_check)
        ctx.progress(result["files"], result["files"], run_message(result))
        return result

    def prepare_download(params: Dict[str, Any]) -> Dict[str, Any]:
        model = params.get("model", DEFAULT_MODEL)
        if model not in MODELS:
            raise HTTPException(status_code=400, detail="Onbekend zoekmodel")
        return {"model": model}

    def run_download(ctx) -> Dict[str, Any]:
        spec = MODELS[ctx.params["model"]]
        ctx.progress(0, spec.size, "Downloaden...")
        try:
            result = download_model(config, spec, check=ctx.check, db=db,
                                    progress=lambda done, total, message: ctx.progress(done, total, message))
        except OSError as e:
            import urllib.error
            if isinstance(e, DownloadRefused):
                raise RuntimeError(f"Downloaden geweigerd: {e}")
            if isinstance(e, urllib.error.HTTPError):
                raise RuntimeError(f"Downloaden mislukt: de server antwoordde {e.code}. Probeer het later opnieuw.")
            if isinstance(e, urllib.error.URLError):
                raise RuntimeError("Downloaden mislukt: geen verbinding met huggingface.co. "
                                   "Controleer de internetverbinding en hervat de taak.")
            raise
        ctx.progress(spec.size, spec.size, f"Klaar: zoekmodel geïnstalleerd ({_mb(spec.size)} MB)")
        return {**result, "complete": True, "errors": 0}

    return [
        JobType(EMBED_JOB, "Foto's herkennen voor Slim zoeken", prepare_embed, run_embed,
                summarize=lambda r: {k: r.get(k) for k in ("files", "embedded", "undecodable", "skipped", "errors",
                                                            "complete", "device", "per_second", "seconds")},
                results_to_keep=3, jobs_to_keep=5, describe=lambda params: "hele archief"),
        JobType(DOWNLOAD_JOB, "Zoekmodel installeren", prepare_download, run_download,
                summarize=lambda r: {k: r.get(k) for k in ("model", "size", "downloaded", "complete", "errors")},
                results_to_keep=2, jobs_to_keep=5,
                describe=lambda params: MODELS[params.get("model", DEFAULT_MODEL)].label),
    ]
