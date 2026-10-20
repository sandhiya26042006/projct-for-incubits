"""PS1 background music engine demo. The default provider is procedural mock audio."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import struct
import threading
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import numpy as np

ROOT = Path(__file__).resolve().parent

def load_env_file():
    env_file = ROOT / ".env"
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))

load_env_file()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", ROOT / "outputs")).resolve()
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
SAMPLE_RATE = 48000
JOBS: dict[str, dict] = {}
IDEMPOTENCY: dict[str, str] = {}
LOCK = threading.RLock()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ps1")


def error(message, status=400, field=None):
    result = {"error": {"message": message}}
    if field:
        result["error"]["field"] = field
    return status, result


def validate(payload):
    if not isinstance(payload, dict):
        return "Request body must be a JSON object.", "body"
    if not isinstance(payload.get("prompt"), str) or not payload["prompt"].strip():
        return "prompt is required and must be a non-empty string.", "prompt"
    d = payload.get("duration_s")
    if isinstance(d, bool) or not isinstance(d, (int, float)) or not 5 <= d <= 300:
        return "duration_s must be between 5 and 300 seconds.", "duration_s"
    sections = payload.get("sections", [])
    if not isinstance(sections, list) or any(not isinstance(s, dict) or not isinstance(s.get("start_s"), (int, float)) or not 0 <= s["start_s"] < d or not isinstance(s.get("mood"), str) for s in sections):
        return "sections must contain objects with start_s within the duration and a mood string.", "sections"
    tempo = payload.get("tempo_bpm")
    if tempo is not None and (isinstance(tempo, bool) or not isinstance(tempo, (int, float)) or not 30 <= tempo <= 300):
        return "tempo_bpm must be between 30 and 300.", "tempo_bpm"
    output = payload.get("output", {})
    if not isinstance(output, dict):
        return "output must be an object.", "output"
    sr = output.get("sample_rate", SAMPLE_RATE)
    if sr != SAMPLE_RATE:
        return "This demo currently supports sample_rate=48000 only.", "output.sample_rate"
    formats = output.get("formats", ["wav"])
    if not isinstance(formats, list) or any(x not in ("wav", "mp3") for x in formats):
        return "formats may contain wav and/or mp3.", "output.formats"
    return None


def write_mono(path: Path, chunks, rate=SAMPLE_RATE):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate)
        for chunk in chunks:
            f.writeframesraw(np.asarray(chunk, dtype="<i2").tobytes())


def render(job_id, req):
    """Create three distinct synthetic parts, and a mix that is their exact integer sum."""
    duration = float(req["duration_s"])
    n_total = round(duration * SAMPLE_RATE)
    bpm = float(req.get("tempo_bpm", 100))
    seed = int(req.get("seed") or 0)
    rng = np.random.default_rng(seed)
    folder = OUTPUT_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)
    names = ("drums", "bass", "melody_harmony")
    paths = {name: folder / f"{name}.wav" for name in names}
    mix_path = folder / "mix.wav"
    n_chunks = (n_total + SAMPLE_RATE - 1) // SAMPLE_RATE
    peak = 0.60
    gains = {"drums": 0.40, "bass": 0.32, "melody_harmony": 0.28}
    # Compose per-second to bound memory. Each stem is generated independently.
    with wave.open(str(paths["drums"]), "wb") as drums, wave.open(str(paths["bass"]), "wb") as bass, wave.open(str(paths["melody_harmony"]), "wb") as melody, wave.open(str(mix_path), "wb") as mix:
        for wf in (drums, bass, melody, mix):
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SAMPLE_RATE)
        for k in range(n_chunks):
            start = k * SAMPLE_RATE
            count = min(SAMPLE_RATE, n_total - start)
            t = (start + np.arange(count, dtype=np.float64)) / SAMPLE_RATE
            beat = 60.0 / bpm
            # Quiet procedural drum hits, low bass notes, and a major-key melodic line.
            beat_idx = np.floor(t / beat).astype(int)
            beat_age = t - beat_idx * beat
            env = np.exp(-beat_age * 18)
            kick = np.sin(2 * np.pi * (55 - 20 * np.minimum(beat_age / .12, 1)) * t) * env * (beat_idx % 4 == 0)
            snare_noise = rng.uniform(-1, 1, count) * np.exp(-beat_age * 28) * ((beat_idx % 4 == 2) | (beat_idx % 4 == 3))
            hat = np.sin(2 * np.pi * 7800 * t) * np.exp(-np.mod(t, beat / 2) * 65) * .15
            drum = kick * .40 + snare_noise * .22 + hat
            chord_idx = np.floor(t / (beat * 4)).astype(int)
            roots = np.array([110.0, 146.83, 164.81, 130.81])
            root = roots[chord_idx % 4]
            bass_raw = np.sin(2 * np.pi * root * t) * .65 + np.sin(2 * np.pi * root * 2 * t) * .15
            # A separate plucked melodic/harmony part, arranged on an eight-note scale.
            scale = np.array([261.63, 293.66, 329.63, 392.0, 440.0, 392.0, 329.63, 293.66])
            note_len = beat / 2
            note_idx = np.floor(t / note_len).astype(int)
            note_age = np.mod(t, note_len)
            note_env = np.exp(-note_age * 5.0)
            melody_raw = (np.sin(2 * np.pi * scale[note_idx % 8] * t) + .3 * np.sin(2 * np.pi * scale[note_idx % 8] * 2 * t)) * note_env * .5
            # Soft fade-in/out keeps the result from beginning or ending abruptly.
            fade = np.minimum(1.0, np.minimum(t / .35, np.maximum(0, (duration - t) / .8)))
            vals = [drum * gains["drums"], bass_raw * gains["bass"], melody_raw * gains["melody_harmony"]]
            vals = [np.clip(v * fade * peak, -0.94, 0.94) for v in vals]
            ints = [np.rint(v * 32767).astype("<i2") for v in vals]
            mix_i = (ints[0].astype(np.int32) + ints[1].astype(np.int32) + ints[2].astype(np.int32)).astype("<i2")
            drums.writeframesraw(ints[0].tobytes()); bass.writeframesraw(ints[1].tobytes()); melody.writeframesraw(ints[2].tobytes()); mix.writeframesraw(mix_i.tobytes())
            with LOCK:
                JOBS[job_id]["progress"] = min(95, int((k + 1) * 95 / n_chunks))
    # Narration is not fetched: create a clearly documented attenuated variant on request.
    ducked = None
    if req.get("narration_url"):
        ducked_path = folder / "mix_ducked.wav"
        with wave.open(str(mix_path), "rb") as src, wave.open(str(ducked_path), "wb") as dst:
            dst.setparams(src.getparams())
            while raw := src.readframes(SAMPLE_RATE):
                audio = np.frombuffer(raw, dtype="<i2").astype(np.float32)
                dst.writeframesraw(np.rint(audio * 0.62).astype("<i2").tobytes())
        ducked = "mix_ducked.wav"
    mp3 = None
    if "mp3" in req.get("output", {}).get("formats", []):
        import shutil, subprocess
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            mp3_path = folder / "mix.mp3"
            subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(mix_path), "-codec:a", "libmp3lame", "-b:a", "192k", str(mp3_path)], check=True, timeout=max(30, duration * 2))
            mp3 = "mix.mp3"
    # RMS dBFS proxy; not a standards-compliant LUFS/true-peak meter.
    with wave.open(str(mix_path), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768
    rms_dbfs = float(20 * np.log10(max(1e-12, np.sqrt(np.mean(samples * samples)))))
    peak_dbfs = float(20 * np.log10(max(1e-12, np.max(np.abs(samples)))))
    manifest = {
        "job_id": job_id, "provider": "procedural_mock", "generated_by_ai": False,
        "sample_rate_hz": SAMPLE_RATE, "channels": 1, "duration_s": n_total / SAMPLE_RATE,
        "stems": {name: {"file": f"{name}.wav", "gain": 1.0} for name in names},
        "mix": "mix.wav", "mix_reconstruction": "mix.wav is the integer sample sum of the listed stem files at gain 1.0",
        "mix_ducked": ducked, "mp3": mp3,
        "measurements": {"rms_dbfs": rms_dbfs, "sample_peak_dbfs": peak_dbfs, "lufs": None, "true_peak_dbtp": None},
        "notes": ["Procedural mock audio; not AI-generated or a licensed instrument recording.", "Loudness is reported as RMS dBFS proxy; LUFS and oversampled true peak were not measured.", "Narration URL is not downloaded or mixed; mix_ducked.wav is a 4.2 dB attenuated music-only preview."],
    }
    (folder / "music_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"files": {"mix_wav": "mix.wav", "stems": [f"{n}.wav" for n in names], "manifest": "music_manifest.json", **({"mix_mp3": mp3} if mp3 else {}), **({"mix_ducked_wav": ducked} if ducked else {})}, "measurements": manifest["measurements"], "provider": "procedural_mock", "generated_by_ai": False}


def run_job(job_id, req):
    try:
        with LOCK:
            JOBS[job_id].update(status="running", started_at=time.time())
        result = render(job_id, req)
        with LOCK:
            JOBS[job_id].update(status="succeeded", progress=100, result=result, completed_at=time.time())
        log.info("job %s succeeded (procedural mock)", job_id)
    except Exception as exc:
        log.exception("job %s failed", job_id)
        with LOCK:
            JOBS[job_id].update(status="failed", error={"message": str(exc)}, completed_at=time.time())


class Handler(BaseHTTPRequestHandler):
    server_version = "PS1Demo/1.0"
    def send_json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(data)
    def do_OPTIONS(self):
        self.send_response(204); self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS"); self.send_header("Access-Control-Allow-Headers", "Content-Type,Idempotency-Key"); self.end_headers()
    def do_GET(self):
        path = unquote(self.path.split("?", 1)[0])
        if path == "/health": return self.send_json(200, {"status": "ok"})
        if path == "/ready": return self.send_json(200, {"status": "ready", "provider": "procedural_mock", "output_dir_writable": os.access(OUTPUT_DIR.parent if not OUTPUT_DIR.exists() else OUTPUT_DIR, os.W_OK)})
        if path == "/openapi.yaml":
            data = (ROOT / "openapi.yaml").read_bytes(); self.send_response(200); self.send_header("Content-Type", "application/yaml; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if path == "/":
            html = (ROOT / "static" / "index.html").read_bytes(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(html))); self.end_headers(); self.wfile.write(html); return
        if path.startswith("/static/"):
            f = (ROOT / path.lstrip("/")).resolve()
            if ROOT / "static" not in f.parents or not f.is_file(): return self.send_json(404, {"error": {"message": "Not found"}})
            data = f.read_bytes(); self.send_response(200); self.send_header("Content-Type", "text/css" if f.suffix == ".css" else "text/javascript"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if path.startswith("/api/v1/jobs/") and "/files/" in path:
            bits = path.split("/files/", 1)
            jid = bits[0].rsplit("/", 1)[-1]; filename = Path(bits[1]).name
            fp = (OUTPUT_DIR / jid / filename).resolve()
            if fp.parent != (OUTPUT_DIR / jid).resolve() or not fp.is_file(): return self.send_json(404, {"error": {"message": "File not found"}})
            content_type = "audio/wav" if fp.suffix == ".wav" else "audio/mpeg" if fp.suffix == ".mp3" else "application/json"
            data = fp.read_bytes(); self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Content-Disposition", f'attachment; filename="{filename}"'); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if path.startswith("/api/v1/jobs/"):
            jid = path.rsplit("/", 1)[-1]
            with LOCK: job = JOBS.get(jid)
            if not job: return self.send_json(404, {"error": {"message": "Job not found"}})
            result = dict(job)
            if job.get("status") == "succeeded":
                result["result"] = dict(job["result"])
                result["result"]["links"] = {key: f"/api/v1/jobs/{jid}/files/{value}" for key, value in job["result"]["files"].items() if isinstance(value, str)}
                result["result"]["links"]["stems"] = [f"/api/v1/jobs/{jid}/files/{x}" for x in job["result"]["files"]["stems"]]
            return self.send_json(200, result)
        return self.send_json(404, {"error": {"message": "Not found"}})
    def do_POST(self):
        if self.path != "/api/v1/music/jobs": return self.send_json(404, {"error": {"message": "Not found"}})
        try: req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        except Exception: return self.send_json(*error("Request body must be valid JSON."))
        invalid = validate(req)
        if invalid: return self.send_json(*error(*invalid))
        key = self.headers.get("Idempotency-Key")
        if key:
            with LOCK:
                if key in IDEMPOTENCY:
                    jid = IDEMPOTENCY[key]
                    return self.send_json(200, {"job_id": jid, "status": JOBS[jid]["status"], "idempotent_replay": True, "status_url": f"/api/v1/jobs/{jid}"})
        jid = str(uuid.uuid4())
        job = {"job_id": jid, "status": "queued", "progress": 0, "created_at": time.time(), "provider": "procedural_mock", "generated_by_ai": False}
        with LOCK:
            JOBS[jid] = job
            if key: IDEMPOTENCY[key] = jid
        threading.Thread(target=run_job, args=(jid, req), daemon=True).start()
        log.info("accepted job %s", jid)
        return self.send_json(202, {"job_id": jid, "status": "queued", "provider": "procedural_mock", "generated_by_ai": False, "status_url": f"/api/v1/jobs/{jid}"})
    def log_message(self, fmt, *args): log.info("%s - %s", self.address_string(), fmt % args)


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log.info("PS1 demo listening on http://%s:%s; provider=procedural_mock", HOST, PORT)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
