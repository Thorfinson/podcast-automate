"""Standalone worker: runs in the user's separate PyTorch/Qwen environment."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def environment_report() -> dict:
    report = {"python": platform.python_version(), "system": platform.system(),
              "os_version": platform.version(), "packages": {}, "gpu_available": False,
              "cuda_available": False, "mps_available": False}
    for name in ("torch", "qwen-tts", "soundfile", "transformers"):
        try:
            report["packages"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report["packages"][name] = None
    if importlib.util.find_spec("torch"):
        import torch
        report["hip_version"] = getattr(torch.version, "hip", None)
        report["cuda_available"] = torch.cuda.is_available()
        report["mps_available"] = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
        report["gpu_available"] = report["cuda_available"] or report["mps_available"]
        if report["cuda_available"]:
            report["gpu_name"] = torch.cuda.get_device_name(0)
            report["gpu_memory_bytes"] = torch.cuda.get_device_properties(0).total_memory
        elif report["mps_available"]:
            report["gpu_name"] = "Apple Metal (MPS)"
    return report


def select_device(torch, requested: str) -> str:
    cuda = torch.cuda.is_available()
    mps = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    if requested == "auto":
        return "cuda:0" if cuda else "mps" if mps else "cpu"
    if requested == "cuda:0" and not cuda:
        raise RuntimeError("CUDA_UNAVAILABLE")
    if requested == "mps" and not mps:
        raise RuntimeError("MPS_UNAVAILABLE")
    if requested not in {"cuda:0", "mps", "cpu"}:
        raise RuntimeError("INVALID_DEVICE")
    return requested


def model_dtype(torch, device: str):
    # MPS uses float32 for broader operator compatibility; do not call CUDA
    # memory or precision APIs on Apple GPUs or the CPU.
    if device != "cuda:0":
        return torch.float32
    return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16


def spoken_settings(text: str, spoken) -> dict:
    """The spoken form belongs to a segment's settings only when it differs from the script text.

    Both engines, both verifiers and both cache keys use this one rule: a segment without a
    spoken form is stored and keyed exactly as it was before spoken forms existed, so every
    earlier cache entry stays valid. ``text`` is always the reviewed script.
    """
    return {} if spoken is None or spoken == text else {"spoken_text": spoken}


def segment_settings(segment: dict, *, voice: str, language: str, config: dict, revision: str,
                     device: str, dtype: str, packages: dict, hip_version) -> dict:
    """Everything that decides what a rendered segment sounds like; the cache key hashes it."""
    return {
        "worker_version": 2, "model": config["tts_model"], "revision": revision,
        "voice": voice, "text": segment["text"], "language": language,
        "device": config["tts_device"], "resolved_device": device, "dtype": dtype,
        "attention": config["tts_attention"], "seed": config["seed"],
        "packages": packages, "hip_version": hip_version,
        **spoken_settings(segment["text"], segment.get("spoken_text")),
    }


def cache_key(settings: dict) -> str:
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()


def cached_segment(wav: Path, meta: Path) -> dict | None:
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        if wav.is_file() and data["sha256"] == hash_file(wav):
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def render(request: dict, report: dict) -> dict:
    import numpy as np
    import soundfile as sf
    import torch
    from huggingface_hub import snapshot_download
    from qwen_tts import Qwen3TTSModel

    config = request["runtime"]
    progress_file = request.get("progress_file")

    def progress(state, results, current=None):
        if progress_file:
            save(Path(progress_file), {"status": state, "completed": len(results),
                "total": len(request["segments"]), "current_segment": current,
                "segments": results})

    progress("loading_model", [])
    language = request.get("language", "German")
    device = select_device(torch, config["tts_device"])
    dtype = model_dtype(torch, device)
    if Path(config["tts_model"]).is_dir():
        raise RuntimeError("USE_PINNED_HUGGINGFACE_MODEL")
    # Load processor and model from the same immutable downloaded snapshot.
    snapshot = snapshot_download(config["tts_model"], revision=config["tts_revision"])
    revision = Path(snapshot).name
    start = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        snapshot, device_map=device,
        dtype=dtype,
        attn_implementation=config["tts_attention"],
    )
    load_seconds = time.perf_counter() - start
    if device == "cuda:0":
        torch.cuda.reset_peak_memory_stats()
    cache = Path(request["cache_dir"])
    cache.mkdir(parents=True, exist_ok=True)
    results = []
    for segment in request["segments"]:
        progress("rendering", results, segment["segment_id"])
        voice = request["voices"][segment["speaker_id"]]
        spoken = segment.get("spoken_text") or segment["text"]
        settings = segment_settings(segment, voice=voice, language=language, config=config,
                                    revision=revision, device=device, dtype=str(dtype),
                                    packages=report["packages"], hip_version=report.get("hip_version"))
        key = cache_key(settings)
        wav, meta = cache / f"{key}.wav", cache / f"{key}.json"
        data = cached_segment(wav, meta)
        hit = data is not None
        if not hit:
            torch.manual_seed(config["seed"])
            started = time.perf_counter()
            with torch.inference_mode():
                waves, rate = model.generate_custom_voice(
                    text=spoken, language=language, speaker=voice)
            samples = np.asarray(waves[0], dtype=np.float32)
            if samples.ndim != 1 or not samples.size or not np.isfinite(samples).all():
                raise RuntimeError("INVALID_AUDIO")
            peak = float(np.abs(samples).max())
            if peak < 1e-5:
                raise RuntimeError("EMPTY_AUDIO")
            if peak > 0.99:
                samples = samples * (0.99 / peak)
            fd, temporary = tempfile.mkstemp(dir=cache, suffix=".wav")
            os.close(fd)
            try:
                sf.write(temporary, samples, rate, subtype="PCM_16")
                os.replace(temporary, wav)
            finally:
                Path(temporary).unlink(missing_ok=True)
            data = {
                "sha256": hash_file(wav), "duration_seconds": len(samples) / rate,
                "render_seconds": time.perf_counter() - started, "settings": settings,
            }
            save(meta, data)
        results.append({
            "segment_id": segment["segment_id"], "path": str(wav),
            "cache_hit": hit, **data,
        })
        progress("rendering", results)
    progress("completed", results)
    return {
        **report, "model_revision": revision, "model_load_seconds": load_seconds,
        "selected_device": device,
        "peak_gpu_memory_bytes": torch.cuda.max_memory_allocated() if device == "cuda:0" else None,
        "segments": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = environment_report()
        if not args.doctor:
            if args.request is None:
                parser.error("--request is required for rendering")
            report = render(json.loads(args.request.read_text(encoding="utf-8")), report)
        save(args.output, report)
        return 0
    except Exception as exc:
        messages = {
            "CUDA_UNAVAILABLE": "PyTorch erkennt keine CUDA-/ROCm-GPU. Passenden PyTorch-Build und Treiber prüfen.",
            "MPS_UNAVAILABLE": "PyTorch erkennt kein MPS-Gerät. Apple-Silicon-Mac und passenden PyTorch-Build prüfen.",
            "INVALID_DEVICE": "Als Qwen-Gerät auto, cuda:0, mps oder cpu einstellen.",
            "USE_PINNED_HUGGINGFACE_MODEL": "Für die Probe einen Hugging-Face-Modellnamen verwenden.",
            "INVALID_AUDIO": "Das Modell lieferte ungültige Audiodaten.",
            "EMPTY_AUDIO": "Das Modell lieferte nur Stille.",
        }
        message = messages.get(str(exc), f"TTS-Umgebung fehlgeschlagen ({type(exc).__name__}).")
        save(args.output, {"error": message, "error_type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
