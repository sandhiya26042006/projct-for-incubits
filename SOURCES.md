# Sources and licensing

- **Music provider/model:** none. The application uses an original procedural synthesis routine in `app.py`; no generative model or external music provider is called.
- **Audio library:** NumPy, installed from PyPI via `requirements.txt`. WAV encoding uses Python's standard-library `wave`; MP3 encoding optionally uses an installed `ffmpeg` executable and its `libmp3lame` encoder.
- **Licenses:** consult the license files/distribution terms for the exact installed NumPy and FFmpeg builds before redistribution or commercial deployment. This project does not claim rights to a third-party music model, samples, recordings, or commercial usage permissions.
- **Network services / API keys:** none required by the demo. The UI imports fonts from Google Fonts when network access is available; system sans-serif fallbacks remain usable.
