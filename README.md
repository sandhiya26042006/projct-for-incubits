# PS1 — AI Background Music Engine for Video

Runnable assessment demo with a browser UI and an asynchronous JSON API. The included provider creates deterministic synthetic tones and rhythms; it is a **procedural mock, not AI-generated music**. No paid key is needed. No commercial usage rights are asserted for the synthesized output.

## Run on Windows

```powershell
cd 'D:\ai problem'
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). API health: `/health`; readiness: `/ready`. Outputs are saved under `outputs/<job_id>/`. The app reads `.env`; set `PORT` or `OUTPUT_DIR` there to change defaults. `ffmpeg` on PATH adds `mix.mp3` when MP3 is requested; without it, WAV and stems still work.

## API

`POST /api/v1/music/jobs` accepts JSON and optionally `Idempotency-Key`. It returns `202` with a `job_id`; poll `GET /api/v1/jobs/{job_id}`. Successful jobs include result links. `prompt` is required; `duration_s` must be 5–300. Optional fields are sections, tempo_bpm, key, loopable, narration_url, seed, and output. The mock currently uses prompt, duration, tempo, seed, formats, and narration_url; key, loopable, sections, and target_lufs are accepted but do not change the composition. Narration URLs are not fetched: the optional ducked WAV is only an attenuated music-only preview.

Example:

```powershell
$body = @'
{
  "prompt": "Warm, optimistic acoustic pop for a product launch, with a calm intro, gradual build-up and uplifting finish. Instrumental only, no vocals.",
  "duration_s": 15,
  "sections": [{"start_s":0,"mood":"calm intro"},{"start_s":5,"mood":"gentle build-up"},{"start_s":10,"mood":"uplifting finish"}],
  "tempo_bpm": 100,
  "loopable": false,
  "seed": 42,
  "output": {"sample_rate":48000,"target_lufs":-16,"formats":["wav","mp3"]}
}
'@
$job = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/music/jobs -ContentType 'application/json' -Body $body
do { Start-Sleep -Milliseconds 500; $status = Invoke-RestMethod "http://127.0.0.1:8000$($job.status_url)" } while ($status.status -in @('queued','running'))
$status | ConvertTo-Json -Depth 8
```

## Audio claims

Three separately generated procedural parts (`drums`, `bass`, `melody_harmony`) are saved as mono 48 kHz PCM WAV. The mix is their sample-wise integer sum with unit manifest gains. The manifest reports exact sample duration, RMS dBFS proxy, and sample peak. It does not claim LUFS or oversampled true-peak compliance. It is not an instrument recording or an AI generation. A true AI provider, stem quality, acoustic realism, loudness targeting, narration sidechain ducking, and musicality require future provider integration and listening/measurement validation.

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Project layout

- `app.py`: stdlib HTTP API, background worker, procedural renderer
- `static/`: simple browser UI
- `outputs/`: generated audio and manifests (created at runtime)
- `openapi.yaml`: API contract
- `REPORT.md`, `SOURCES.md`, `AI_USE.md`: implementation record and limitations
