# Song Catcher Dashboard

Streamlit dashboard for the Song Catcher / BirdNET-Pi public detection API.

## Files

- `dashboard.py` — main Streamlit app
- `requirements.txt` — Python dependencies
- `packages.txt` — system packages for Streamlit Community Cloud; includes `ffmpeg` for enhanced audio playback
- `.streamlit/secrets.toml.example` — example Streamlit secrets configuration
- `docs/cloudflare_named_tunnel_pi.md` — named Cloudflare Tunnel runbook

## Configuration

The dashboard reads the API URL from configuration, not from a hard-coded tunnel URL.

Preferred Streamlit secret:

```toml
SONGCATCHER_API_URL = "https://api.yourdomain.com"
```

It also supports this nested form:

```toml
[api]
url = "https://api.yourdomain.com"
```

For local development, you can export the environment variable:

```bash
export SONGCATCHER_API_URL="https://api.yourdomain.com"
streamlit run dashboard.py
```

For temporary testing only, you can point it at the current Quick Tunnel:

```bash
export SONGCATCHER_API_URL="https://lancaster-efforts-thumb-craig.trycloudflare.com"
streamlit run dashboard.py
```

## Deploy to Streamlit Community Cloud

1. Push this repository to GitHub.
2. Create a new Streamlit Community Cloud app.
3. Select this repository and set the main file path to `dashboard.py`.
4. Add `SONGCATCHER_API_URL` in the app secrets.
5. Deploy.

## Local run

```bash
python -m pip install -r requirements.txt
export SONGCATCHER_API_URL="https://lancaster-efforts-thumb-craig.trycloudflare.com"
streamlit run dashboard.py
```

If enhanced audio modes are needed locally, install FFmpeg/ffprobe on the host system.
