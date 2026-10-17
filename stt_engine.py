"""
Speech-to-text engine for StockLlama (no tkinter in here).

FUTO Voice Input uses OpenAI Whisper tiny / base / small fine-tuned with the
"ACFT" method (futo-org/whisper-acft), in English-only (.en) and multilingual
variants.  This module exposes those models plus stock Whisper models and any
model you drop on disk, through three interchangeable backends:

  hf      transformers  - FUTO ACFT checkpoints from the Hugging Face Hub
                          (futo-org/acft-whisper-{tiny,base,small}[.en]) or
                          any local Whisper folder.   pip install transformers torch
  faster  faster-whisper - stock Whisper sizes (fast on CPU, int8) or a local
                          CTranslate2 folder.          pip install faster-whisper
  ggml    pywhispercpp  - whisper.cpp .bin files, e.g. the ones FUTO publishes
                          at keyboard.futo.org/voice-input-models.
                                                       pip install pywhispercpp

Recording needs:  pip install sounddevice numpy

Drop extra models into  ./models/stt/  (a *.bin file or a model folder) and
they appear in the model list automatically.
"""
from __future__ import annotations

import importlib.util
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

SAMPLE_RATE = 16_000
LOCAL_MODEL_DIR = Path(__file__).resolve().parent / "models" / "stt"


@dataclass(frozen=True)
class SttModel:
    key: str
    label: str
    backend: str            # "hf" | "faster" | "ggml"
    source: str             # HF repo id, faster-whisper size, or local path
    multilingual: bool = True
    acft: bool = False      # FUTO audio-context fine-tuned
    approx_mb: Optional[int] = None


_PIP = {
    "hf": "pip install transformers torch",
    "faster": "pip install faster-whisper",
    "ggml": "pip install pywhispercpp",
}
_MODULE = {"hf": "transformers", "faster": "faster_whisper", "ggml": "pywhispercpp"}


def _futo(size: str, english: bool, mb: int) -> SttModel:
    suffix = ".en" if english else ""
    return SttModel(
        key=f"futo-{size}{suffix}",
        label=f"FUTO ACFT  {size}{suffix}" + ("  (English)" if english else "  (multilingual)"),
        backend="hf", source=f"futo-org/acft-whisper-{size}{suffix}",
        multilingual=not english, acft=True, approx_mb=mb)


def _stock(size: str, mb: int) -> SttModel:
    english = size.endswith(".en")
    return SttModel(
        key=f"whisper-{size}", label=f"Whisper  {size}  (faster-whisper)",
        backend="faster", source=size, multilingual=not english, approx_mb=mb)


REGISTRY: list[SttModel] = [
    _futo("tiny", True, 75), _futo("base", True, 142), _futo("small", True, 465),
    _futo("tiny", False, 75), _futo("base", False, 142), _futo("small", False, 465),
    _stock("tiny.en", 75), _stock("base.en", 142), _stock("small.en", 465),
    _stock("medium.en", 1500), _stock("small", 465), _stock("medium", 1500),
    _stock("large-v3", 3000),
]


def local_models() -> list[SttModel]:
    """Models found in ./models/stt (GGML .bin files and model folders)."""
    found: list[SttModel] = []
    if not LOCAL_MODEL_DIR.exists():
        return found
    for p in sorted(LOCAL_MODEL_DIR.iterdir()):
        model = model_from_path(p)
        if model:
            found.append(model)
    return found


def model_from_path(path: Path | str) -> Optional[SttModel]:
    p = Path(path)
    name = p.name
    lower = name.lower()
    if p.is_file() and p.suffix.lower() == ".bin":
        return SttModel(
            key=f"local:{name}", label=f"Local  {name}  (whisper.cpp)",
            backend="ggml", source=str(p),
            multilingual=".en" not in lower and "_en" not in lower,
            acft="acft" in lower, approx_mb=int(p.stat().st_size / 1e6))
    if p.is_dir():
        if (p / "model.bin").exists():
            backend = "faster"
        elif (p / "config.json").exists():
            backend = "hf"
        else:
            return None
        return SttModel(
            key=f"local:{name}", label=f"Local  {name}  ({'CTranslate2' if backend == 'faster' else 'HF'})",
            backend=backend, source=str(p),
            multilingual=".en" not in lower, acft="acft" in lower)
    return None


def available_models() -> list[SttModel]:
    return REGISTRY + local_models()


def find_model(key_or_path: str) -> SttModel:
    for m in available_models():
        if m.key == key_or_path:
            return m
    custom = model_from_path(key_or_path)
    if custom:
        return custom
    if "/" in key_or_path and not Path(key_or_path).exists():   # HF repo id
        return SttModel(key=key_or_path, label=key_or_path, backend="hf",
                        source=key_or_path, acft="acft" in key_or_path.lower())
    raise ValueError(f"Unknown speech model: {key_or_path}")


def backend_installed(backend: str) -> bool:
    return importlib.util.find_spec(_MODULE[backend]) is not None


def install_hint(backend: str) -> str:
    return _PIP[backend]


# ── backends ──────────────────────────────────────────────────────────────────
def _chunks(audio, seconds=30):
    size = seconds * SAMPLE_RATE
    for i in range(0, len(audio), size):
        part = audio[i:i + size]
        if len(part) > SAMPLE_RATE // 10:      # ignore <0.1 s tails
            yield part


class _HFBackend:
    def __init__(self, model: SttModel):
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor
        self.torch = torch
        self.multilingual = model.multilingual
        try:
            self.processor = WhisperProcessor.from_pretrained(model.source)
        except Exception:
            # Fine-tuned repos sometimes ship weights only; borrow the
            # tokenizer / feature extractor from the matching OpenAI model.
            base = "openai/whisper-" + model.source.rsplit("acft-whisper-", 1)[-1]
            self.processor = WhisperProcessor.from_pretrained(base)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = WhisperForConditionalGeneration.from_pretrained(
            model.source).to(self.device).eval()
        gen = getattr(self.model, "generation_config", None)
        if gen is not None and hasattr(gen, "is_multilingual"):
            self.multilingual = bool(gen.is_multilingual)

    def transcribe(self, audio, language):
        texts = []
        for part in _chunks(audio):
            feats = self.processor(part, sampling_rate=SAMPLE_RATE,
                                   return_tensors="pt").input_features.to(self.device)
            kwargs = {"max_new_tokens": 200}
            if self.multilingual:
                kwargs["task"] = "transcribe"
                if language:
                    kwargs["language"] = language
            with self.torch.no_grad():
                ids = self.model.generate(feats, **kwargs)
            texts.append(self.processor.batch_decode(ids, skip_special_tokens=True)[0].strip())
        return " ".join(t for t in texts if t)


class _FasterBackend:
    def __init__(self, model: SttModel):
        from faster_whisper import WhisperModel
        try:
            import ctranslate2
            cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            cuda = False
        self.model = WhisperModel(model.source,
                                  device="cuda" if cuda else "cpu",
                                  compute_type="float16" if cuda else "int8")
        self.multilingual = model.multilingual

    def transcribe(self, audio, language):
        segments, _info = self.model.transcribe(
            audio, language=language if self.multilingual else "en",
            beam_size=5, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


class _GGMLBackend:
    """whisper.cpp via pywhispercpp.

    FUTO's ACFT models are meant to run with a *dynamic audio context* (only
    encode the real audio length).  whisper.cpp's audio_ctx counts 50 frames
    per second (1500 = 30 s), so we pass seconds*50 plus a safety margin.
    """
    CTX_MARGIN = 128

    def __init__(self, model: SttModel):
        from pywhispercpp.model import Model
        self.model = Model(model.source)
        self.acft = model.acft
        self.multilingual = model.multilingual

    def transcribe(self, audio, language):
        params = {}
        if self.multilingual and language:
            params["language"] = language
        elif not self.multilingual:
            params["language"] = "en"
        if self.acft:
            seconds = len(audio) / SAMPLE_RATE
            params["audio_ctx"] = min(1500, math.ceil(seconds * 50) + self.CTX_MARGIN)
        segments = self.model.transcribe(audio, **params)
        return " ".join(s.text.strip() for s in segments).strip()


_BACKENDS = {"hf": _HFBackend, "faster": _FasterBackend, "ggml": _GGMLBackend}


# ── public API ────────────────────────────────────────────────────────────────
@dataclass
class Transcription:
    text: str
    model_label: str
    audio_seconds: float
    elapsed_seconds: float
    language: Optional[str]


class SpeechRecognizer:
    """Loads one model at a time and transcribes float32 16 kHz mono audio."""

    def __init__(self):
        self._lock = threading.Lock()
        self._backend = None
        self.model: Optional[SttModel] = None

    @property
    def loaded_key(self) -> Optional[str]:
        return self.model.key if self.model else None

    def load(self, model_or_key) -> SttModel:
        """Load a model given an SttModel, a registry key, a path or an HF id."""
        model = (model_or_key if isinstance(model_or_key, SttModel)
                 else find_model(model_or_key))
        if not backend_installed(model.backend):
            raise RuntimeError(
                f"'{model.label}' needs an extra package. Run:  {install_hint(model.backend)}")
        with self._lock:
            if self.model and self.model.key == model.key and self._backend:
                return model
            self._backend = _BACKENDS[model.backend](model)
            self.model = model
        return model

    def transcribe(self, audio, language: Optional[str] = None) -> Transcription:
        if self._backend is None:
            raise RuntimeError("No speech model loaded.")
        lang = None if language in (None, "", "auto") else language
        start = time.time()
        with self._lock:
            text = self._backend.transcribe(audio, lang)
        return Transcription(
            text=text, model_label=self.model.label,
            audio_seconds=len(audio) / SAMPLE_RATE,
            elapsed_seconds=time.time() - start, language=lang)


class MicRecorder:
    """Push-to-toggle microphone capture at 16 kHz mono."""

    def __init__(self):
        self._frames = []
        self._stream = None

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self):
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("Recording needs:  pip install sounddevice numpy") from exc
        self._frames = []

        def _cb(indata, _frames, _time, _status):
            self._frames.append(indata.copy())

        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                      dtype="float32", callback=_cb)
        self._stream.start()

    def stop(self):
        """Stop and return the audio as a 1-D float32 numpy array."""
        import numpy as np
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._frames:
            return np.zeros(0, dtype="float32")
        return np.concatenate(self._frames).reshape(-1).astype("float32")
