import json
import io
import tempfile
import time
import unittest
import wave
from pathlib import Path

import app


class ValidationTests(unittest.TestCase):
    def test_valid_request(self):
        self.assertIsNone(app.validate({"prompt": "calm", "duration_s": 5}))

    def test_duration_bounds(self):
        self.assertEqual(app.validate({"prompt": "x", "duration_s": 4})[1], "duration_s")
        self.assertEqual(app.validate({"prompt": "x", "duration_s": 301})[1], "duration_s")

    def test_required_prompt(self):
        self.assertEqual(app.validate({"duration_s": 5})[1], "prompt")


class RenderTests(unittest.TestCase):
    def test_rendered_audio_and_manifest(self):
        with tempfile.TemporaryDirectory(dir=app.ROOT) as td:
            old = app.OUTPUT_DIR
            app.OUTPUT_DIR = Path(td)
            try:
                jid = "test-job"
                app.JOBS[jid] = {"progress": 0}
                result = app.render(jid, {"prompt": "demo", "duration_s": 5, "seed": 42})
                folder = Path(td) / jid
                self.assertEqual(set(result["files"]["stems"]), {"drums.wav", "bass.wav", "melody_harmony.wav"})
                with wave.open(str(folder / "mix.wav")) as mix:
                    self.assertEqual(mix.getframerate(), 48000)
                    self.assertEqual(mix.getnframes(), 240000)
                    expected = mix.readframes(mix.getnframes())
                stems = []
                for name in result["files"]["stems"]:
                    with wave.open(str(folder / name)) as wav:
                        stems.append(wav.readframes(wav.getnframes()))
                import numpy as np
                mix_arr = np.frombuffer(expected, dtype="<i2").astype("int32")
                summed = sum(np.frombuffer(s, dtype="<i2").astype("int32") for s in stems)
                self.assertTrue(np.array_equal(mix_arr, summed))
                manifest = json.loads((folder / "music_manifest.json").read_text())
                self.assertFalse(manifest["generated_by_ai"])
                self.assertEqual(manifest["duration_s"], 5.0)
            finally:
                app.OUTPUT_DIR = old
                app.JOBS.pop("test-job", None)


class HandlerHarness:
    """Call the real request handler methods in-process without binding a socket."""
    def __init__(self, path, body=b"", headers=None):
        self.path = path
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.headers = headers or {}
        self.status = None
        self.response_headers = {}
    def send_response(self, status): self.status = status
    def send_header(self, key, value): self.response_headers[key] = value
    def end_headers(self): pass
    def send_json(self, status, obj):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.wfile.write(json.dumps(obj).encode())
    def json(self): return json.loads(self.wfile.getvalue())


class ApiFlowTests(unittest.TestCase):
    def call_handler(self, method, path, body=b"", headers=None):
        handler = HandlerHarness(path, body, headers)
        getattr(app.Handler, method)(handler)
        return handler

    def test_health_and_readiness(self):
        self.assertEqual(self.call_handler("do_GET", "/health").json()["status"], "ok")
        self.assertEqual(self.call_handler("do_GET", "/ready").status, 200)

    def test_post_poll_download_end_to_end(self):
        with tempfile.TemporaryDirectory(dir=app.ROOT) as td:
            old_output = app.OUTPUT_DIR
            app.OUTPUT_DIR = Path(td)
            try:
                body = json.dumps({"prompt": "demo", "duration_s": 5, "seed": 7}).encode()
                accepted = self.call_handler("do_POST", "/api/v1/music/jobs", body, {"Content-Length": str(len(body))})
                self.assertEqual(accepted.status, 202)
                job = accepted.json()
                for _ in range(100):
                    status = self.call_handler("do_GET", job["status_url"])
                    if status.json()["status"] in ("succeeded", "failed"):
                        break
                    time.sleep(.05)
                self.assertEqual(status.json()["status"], "succeeded")
                file_url = status.json()["result"]["links"]["mix_wav"]
                download = self.call_handler("do_GET", file_url)
                self.assertEqual(download.status, 200)
                self.assertTrue(download.wfile.getvalue().startswith(b"RIFF"))
            finally:
                app.OUTPUT_DIR = old_output


if __name__ == "__main__":
    unittest.main()
