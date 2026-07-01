import html
import os
import math
import shutil
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import pandas as pd
import requests
import streamlit as st

try:
    from PIL import Image, ImageEnhance, ImageOps
except ImportError:  # pragma: no cover - handled in UI
    Image = None
    ImageEnhance = None
    ImageOps = None

try:
    from pydub import AudioSegment
except ImportError:  # pragma: no cover - handled in UI
    AudioSegment = None

try:
    import plotly.express as px
    import plotly.graph_objects as go
except ImportError:  # pragma: no cover - handled in UI
    px = None
    go = None

def configured_default_api_url() -> str:
    """Return the API URL from environment variables or Streamlit secrets.

    Deployment note: keep the public API hostname out of the source code.
    Set SONGCATCHER_API_URL in Streamlit Community Cloud secrets, or export it
    locally before running Streamlit.
    """
    env_url = os.getenv("SONGCATCHER_API_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")

    try:
        secrets_url = str(st.secrets.get("SONGCATCHER_API_URL", "")).strip()
        if not secrets_url and "api" in st.secrets:
            api_section = st.secrets.get("api", {})
            if hasattr(api_section, "get"):
                secrets_url = str(api_section.get("url", "")).strip()
    except Exception:
        secrets_url = ""

    return secrets_url.rstrip("/")


DEFAULT_API_URL = configured_default_api_url()
PERMANENTLY_EXCLUDED_SPECIES = {"common redstart"}
DEFAULT_HIDDEN_LARGEST_LINK_SETS = 1
RECENT_DETECTION_SORT_PARAMS = {
    "sort_by": "detected_at",
    "sort_order": "desc",
    "order_by": "detected_at",
    "order": "desc",
    "newest_first": "true",
}
RECENT_DETECTION_SORT_PARAM_KEYS = set(RECENT_DETECTION_SORT_PARAMS)
REQUEST_TIMEOUT = 20
WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
DISPLAY_LOCATION_NAME = "BirdNET-Pi - Arise Initiative - Dungeness"

st.set_page_config(
    page_title="Song Catcher Dashboard",
    page_icon="🎧",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        img,
        [data-testid="stImage"] img,
        [data-testid="stDataFrame"] img,
        [data-testid="stDataFrame"] div[role="gridcell"] img,
        [data-testid="stDataFrame"] div[style*="background-image"],
        .songcatcher-wiki-img {
            border-radius: 0 !important;
            box-shadow: none !important;
            filter: none !important;
            clip-path: none !important;
            overflow: hidden !important;
            max-width: 100% !important;
            box-sizing: border-box !important;
        }
        .songcatcher-table-wrap {
            max-height: 420px;
            overflow-y: auto;
            overflow-x: auto;
            border: 1px solid rgba(128,128,128,0.25);
            margin-bottom: 1rem;
        }
        .songcatcher-table {
            width: 100%;
            max-width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
            font-size: 0.92rem;
            overflow: hidden;
        }
        .songcatcher-table th,
        .songcatcher-table td {
            border-bottom: 1px solid rgba(128,128,128,0.25);
            padding: 0.45rem 0.5rem;
            vertical-align: middle;
            text-align: left;
            overflow: hidden;
            overflow-wrap: anywhere;
        }
        .songcatcher-table th {
            font-weight: 650;
            position: sticky;
            top: 0;
            z-index: 2;
            background: var(--background-color, #ffffff);
        }
        .songcatcher-table td a {
            display: inline-block;
            text-align: center;
        }
        .songcatcher-table td:has(a) {
            text-align: center;
        }
        .songcatcher-table img {
            border-radius: 0 !important;
            box-shadow: none !important;
            filter: none !important;
            clip-path: none !important;
            max-width: 100% !important;
            object-fit: cover;
            display: block;
            margin-left: auto;
            margin-right: auto;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=60)
def api_get(base_url: str, path: str, params: dict | None = None) -> dict:
    base_url = base_url.rstrip("/") + "/"
    url = urljoin(base_url, path.lstrip("/"))
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=60)
def api_get_optional(base_url: str, path: str, params: dict | None = None) -> dict | None:
    try:
        return api_get(base_url, path, params=params)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise


def detection_sort_key(detection: dict) -> str:
    """Return a stable ISO-ish timestamp key for newest-first detection ordering."""
    return str(detection.get("detected_at") or "")


def sort_detections_newest_first(detections: list[dict], limit: int | None = None) -> list[dict]:
    """Sort detections by detected_at descending and apply the requested limit after sorting."""
    sorted_detections = sorted(detections, key=detection_sort_key, reverse=True)
    if limit is None:
        return sorted_detections

    try:
        limit = int(limit)
    except Exception:
        return sorted_detections

    if limit <= 0:
        return []

    return sorted_detections[:limit]


def api_get_detection_search(base_url: str, path: str, params: dict | None = None) -> dict:
    """Call a detection search endpoint, retrying without sort hints if the API rejects unknown params."""
    try:
        return api_get(base_url, path, params=params)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        has_sort_hints = bool(params and RECENT_DETECTION_SORT_PARAM_KEYS.intersection(params))
        if status_code in {400, 422} and has_sort_hints:
            clean_params = {
                key: value
                for key, value in dict(params).items()
                if key not in RECENT_DETECTION_SORT_PARAM_KEYS
            }
            return api_get(base_url, path, params=clean_params)
        raise


def api_get_optional_detection_search(base_url: str, path: str, params: dict | None = None) -> dict | None:
    try:
        return api_get_detection_search(base_url, path, params=params)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise


def get_detection_search_payload(base_url: str, params: dict, detection_source: str) -> dict:
    # These are harmless hints for APIs that support explicit newest-first ordering.
    # If the current API ignores them, the dashboard still sorts the returned rows locally.
    params = dict(params)
    params.update(RECENT_DETECTION_SORT_PARAMS)
    requested_limit = params.get("limit", 200)

    if detection_source == "Unfiltered metadata":
        payload = api_get_optional_detection_search(base_url, "/api/v1/detections/unfiltered/search", params=params)
        if payload is None:
            return {"detections": [], "metadata_source": "unfiltered_endpoint_unavailable"}
        if payload.get("metadata_source") == "promoted_fallback":
            return {"detections": [], "metadata_source": "unfiltered_empty"}
        payload["detections"] = sort_detections_newest_first(payload.get("detections", []), requested_limit)
        return payload

    if detection_source in {"Combined promoted + unfiltered", "Combined promoted + unfiltered metadata"}:
        promoted_payload = api_get_detection_search(base_url, "/api/v1/detections/search", params=params)
        unfiltered_payload = api_get_optional_detection_search(base_url, "/api/v1/detections/unfiltered/search", params=params)

        promoted = promoted_payload.get("detections", [])
        unfiltered = []
        if unfiltered_payload is not None and unfiltered_payload.get("metadata_source") != "promoted_fallback":
            unfiltered = unfiltered_payload.get("detections", [])

        def mark_detection_group(items, group_name):
            marked = []
            for item in items:
                detection = dict(item)
                detection["_dashboard_source_group"] = group_name
                marked.append(detection)
            return marked

        promoted_marked = mark_detection_group(promoted, "promoted")
        unfiltered_marked = mark_detection_group(unfiltered, "unfiltered")

        promoted_marked = sort_detections_newest_first(promoted_marked, requested_limit)
        unfiltered_marked = sort_detections_newest_first(unfiltered_marked, requested_limit)

        # For the combined card view, promoted detections should remain visually distinct
        # and appear before unfiltered metadata. Tables/metrics still use the combined set.
        combined = (promoted_marked + unfiltered_marked)[:requested_limit]

        return {
            "detections": combined,
            "promoted_detections": promoted_marked,
            "unfiltered_detections": unfiltered_marked,
            "metadata_source": "combined",
        }

    payload = api_get_detection_search(base_url, "/api/v1/detections/search", params=params)
    payload["metadata_source"] = payload.get("metadata_source", "promoted")
    payload["detections"] = sort_detections_newest_first(payload.get("detections", []), requested_limit)
    return payload

@st.cache_data(ttl=300)
def fetch_binary(url: str) -> bytes:
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.content


@st.cache_data(ttl=86400)
def wikipedia_summary(common_name: str | None, scientific_name: str | None = None) -> dict:
    candidates = []
    if common_name:
        candidates.append(str(common_name))
    if scientific_name:
        candidates.append(str(scientific_name))

    for candidate in candidates:
        try:
            title = quote(candidate.replace(" ", "_"))
            response = requests.get(
                WIKIPEDIA_SUMMARY_URL.format(title=title),
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "SongCatcherDashboard/0.1"},
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("content_urls", {}).get("desktop", {}).get("page"):
                    return data
        except Exception:
            continue

    return {}


def wikipedia_page_url(common_name: str | None, scientific_name: str | None = None) -> str:
    data = wikipedia_summary(common_name, scientific_name)
    return data.get("content_urls", {}).get("desktop", {}).get("page") or ""


def wikipedia_thumbnail_url(common_name: str | None, scientific_name: str | None = None) -> str:
    data = wikipedia_summary(common_name, scientific_name)
    return data.get("thumbnail", {}).get("source") or ""


def wikipedia_image_link_html(common_name: str | None, scientific_name: str | None = None, size_px: int = 104) -> str:
    page_url = wikipedia_page_url(common_name, scientific_name)
    thumb_url = wikipedia_thumbnail_url(common_name, scientific_name)

    if not page_url:
        return ""

    safe_page_url = html.escape(page_url, quote=True)
    safe_name = html.escape(str(common_name or scientific_name or "Wikipedia species image"), quote=True)

    if thumb_url:
        safe_thumb_url = html.escape(thumb_url, quote=True)
        return (
            f'<a href="{safe_page_url}" target="_blank" title="Open Wikipedia page">'
            f'<img class="songcatcher-wiki-img" src="{safe_thumb_url}" alt="{safe_name}" '
            f'style="display:block; width:min({size_px}px, 100%); max-width:100%; aspect-ratio:1 / 1; height:auto; object-fit:cover; '
            f'border-radius:0 !important; border:none !important; box-shadow:none !important; filter:none !important; clip-path:none !important; overflow:hidden;" />'
            f'</a>'
        )

    return f'<a href="{safe_page_url}" target="_blank">Wikipedia</a>'


def render_wikipedia_icon(common_name: str | None, scientific_name: str | None = None, size_px: int = 104):
    icon_html = wikipedia_image_link_html(common_name, scientific_name, size_px=size_px)
    if icon_html:
        st.markdown(icon_html, unsafe_allow_html=True)


def clean_display_value(value, fallback=""):
    try:
        if pd.isna(value):
            return fallback
    except Exception:
        pass

    value_text = str(value).strip()
    if value_text.lower() in {"", "nan", "none", "null"}:
        return fallback

    return value_text


def format_count(value) -> str:
    try:
        return f"{int(round(float(value))):,}"
    except Exception:
        return "—"


def format_float(value, places: int = 2) -> str:
    try:
        return f"{float(value):.{places}f}"
    except Exception:
        return "—"


def _split_dashboard_date_range(value_text: str) -> list[str]:
    """Return date-range parts without confusing the slashes inside UK dates."""
    value_text = str(value_text or "").strip()
    if not value_text:
        return []

    normalised = value_text.replace("—", "–").replace(" - ", "–").replace(" to ", "–")
    if "–" in normalised:
        return [part.strip() for part in normalised.split("–", 1) if part.strip()]

    # Some API weekly summaries may use ISO slash ranges, e.g. 2026-05-18/2026-05-24.
    if normalised.count("/") == 1 and "-" in normalised:
        return [part.strip() for part in normalised.split("/", 1) if part.strip()]

    return [normalised]


def dashboard_date_sort_key(value):
    """Parse dashboard daily/weekly labels into a sortable date.

    The dashboard displays dates as DD/MM/YY, but sorting those strings breaks
    when the month changes. This helper always sorts by real dates and treats
    range labels by their start date.
    """
    if value is None:
        return pd.NaT

    try:
        if pd.isna(value):
            return pd.NaT
    except Exception:
        pass

    value_text = str(value).strip()
    if not value_text:
        return pd.NaT

    if "-W" in value_text:
        try:
            year_text, week_text = value_text.split("-W", 1)
            week_number = "".join(ch for ch in week_text if ch.isdigit())[:2]
            return pd.Timestamp(datetime.fromisocalendar(int(year_text), int(week_number), 1))
        except Exception:
            pass

    first_part = _split_dashboard_date_range(value_text)[0]

    try:
        parsed = pd.to_datetime(first_part, errors="coerce", dayfirst=True)
        if pd.notna(parsed):
            return parsed
    except Exception:
        pass

    try:
        parsed = pd.to_datetime(value_text, errors="coerce", dayfirst=True)
        if pd.notna(parsed):
            return parsed
    except Exception:
        pass

    return pd.NaT


def format_dashboard_date(value) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    value_text = str(value).strip()
    if not value_text:
        return ""

    parts = _split_dashboard_date_range(value_text)
    if len(parts) == 2:
        left = format_dashboard_date(parts[0])
        right = format_dashboard_date(parts[1])
        if left and right:
            return f"{left}–{right}"

    if "-W" in value_text:
        try:
            year_text, week_text = value_text.split("-W", 1)
            week_number = "".join(ch for ch in week_text if ch.isdigit())[:2]
            start = datetime.fromisocalendar(int(year_text), int(week_number), 1)
            end = start + timedelta(days=6)
            return f"{start.strftime('%d/%m/%y')}–{end.strftime('%d/%m/%y')}"
        except Exception:
            pass

    try:
        parsed = pd.to_datetime(value_text, errors="coerce", dayfirst=True)
        if pd.notna(parsed):
            return parsed.strftime("%d/%m/%y")
    except Exception:
        pass

    return value_text


def format_dashboard_datetime(value) -> str:
    if value is None:
        return "—"

    try:
        if pd.isna(value):
            return "—"
    except Exception:
        pass

    value_text = str(value).strip()
    if not value_text:
        return "—"

    try:
        return datetime.fromisoformat(value_text.replace("Z", "+00:00")).strftime("%d/%m/%y %H:%M")
    except Exception:
        pass

    try:
        parsed = pd.to_datetime(value_text, errors="coerce")
        if pd.notna(parsed):
            return parsed.strftime("%d/%m/%y %H:%M")
    except Exception:
        pass

    return value_text


def uk_date_input(label: str, value: date, key: str | None = None):
    """Render a date input using UK day/month/year ordering where Streamlit supports it.

    Older Streamlit releases do not accept the date_input(format=...) keyword, so the
    fallback keeps the dashboard running and shows the selected date beneath the field.
    """
    try:
        return st.date_input(label, value=value, key=key, format="DD/MM/YYYY")
    except TypeError:
        selected = st.date_input(label, value=value, key=key)
        try:
            st.caption(f"Selected: {selected.strftime('%d/%m/%y')}")
        except Exception:
            pass
        return selected


def format_gmt_datetime_seconds(value) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return str(value)

    return parsed.strftime("%d/%m/%y %H:%M:%S GMT")


def display_column_heading(value) -> str:
    return str(value).replace("_", " ").replace("avg", "average")


def display_dataframe_with_clean_headings(df: pd.DataFrame, **kwargs):
    display_df = df.copy()
    display_df.columns = [display_column_heading(col) for col in display_df.columns]

    # Streamlit versions before the newer width API do not accept width="stretch".
    # Translate it to the long-supported container-width flag.
    if kwargs.get("width") == "stretch":
        kwargs.pop("width", None)
        kwargs["use_container_width"] = True

    st.dataframe(display_df, **kwargs)


def render_html_table(df: pd.DataFrame, columns: list[str], column_labels: dict[str, str] | None = None, max_rows: int | None = None):
    if df.empty:
        st.info("No rows to display.")
        return

    column_labels = column_labels or {}
    rows = df[columns].head(max_rows).to_dict(orient="records") if max_rows else df[columns].to_dict(orient="records")

    html_rows = []
    header_cells = "".join(f"<th>{html.escape(display_column_heading(column_labels.get(col, col)))}</th>" for col in columns)
    html_rows.append(f"<tr>{header_cells}</tr>")

    for row in rows:
        cells = []
        for col in columns:
            value = row.get(col, "")
            if col.endswith("_html"):
                cells.append(f"<td>{value or ''}</td>")
            else:
                cells.append(f"<td>{html.escape('' if pd.isna(value) else str(value))}</td>")
        html_rows.append(f"<tr>{''.join(cells)}</tr>")

    st.markdown(f"<table class='songcatcher-table'>{''.join(html_rows)}</table>", unsafe_allow_html=True)


def format_link(url: str | None, label: str) -> str:
    if not url:
        return ""
    return (
        '<div style="text-align:center;">'
        f'<a href="{html.escape(str(url), quote=True)}" target="_blank">{html.escape(label)}</a>'
        '</div>'
    )


def render_latest_detections_html_table(df: pd.DataFrame):
    rows = []
    for _, row in df.iterrows():
        common = row.get("common_name", "")
        scientific = row.get("scientific_name", "")
        wiki_url = wikipedia_page_url(common, scientific)
        image_html = wikipedia_image_link_html(common, scientific, size_px=64)
        confidence = row.get("confidence", "")
        if pd.notna(confidence) and confidence != "":
            confidence = f"{float(confidence):.2f}"

        rows.append(
            "<tr>"
            f"<td>{image_html}</td>"
            f"<td>{html.escape(str(row.get('detected', '')))}</td>"
            f"<td>{html.escape(str(common))}</td>"
            f"<td>{html.escape(str(scientific))}</td>"
            f"<td>{confidence}</td>"
            f"<td>{html.escape(str(row.get('event_type', '')).replace('_', ' '))}</td>"
            f"<td>{format_link(wiki_url, 'Wikipedia')}</td>"
            f"<td>{format_link(row.get('audio_url'), 'Audio')}</td>"
            f"<td>{format_link(row.get('sonogram_url'), 'Sonogram')}</td>"
            "</tr>"
        )

    table_html = (
        '<div class="songcatcher-table-wrap">'
        '<table class="songcatcher-table">'
        "<thead><tr>"
        "<th>Image</th><th>Detected</th><th>Common name</th><th>Scientific name</th>"
        "<th>Confidence</th><th>Event</th><th>Wikipedia</th><th>Audio</th><th>Sonogram</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


def render_species_html_table(species_df: pd.DataFrame):
    rows = []
    for _, row in species_df.iterrows():
        common = row.get("common_name", "")
        scientific = row.get("scientific_name", "")
        wiki_url = wikipedia_page_url(common, scientific)
        image_html = wikipedia_image_link_html(common, scientific, size_px=64)
        best_confidence = row.get("best_confidence", "")
        if pd.notna(best_confidence) and best_confidence != "":
            best_confidence = f"{float(best_confidence):.2f}"

        rows.append(
            "<tr>"
            f"<td>{image_html}</td>"
            f"<td>{html.escape(str(common))}</td>"
            f"<td>{html.escape(str(scientific))}</td>"
            f"<td>{int(row.get('detections', 0))}</td>"
            f"<td>{best_confidence}</td>"
            f"<td>{html.escape(str(row.get('first_detected', '')))}</td>"
            f"<td>{html.escape(str(row.get('latest_detected', '')))}</td>"
            f"<td>{format_link(wiki_url, 'Wikipedia')}</td>"
            "</tr>"
        )

    table_html = (
        '<div class="songcatcher-table-wrap">'
        '<table class="songcatcher-table">'
        "<thead><tr>"
        "<th>Image</th><th>Common name</th><th>Scientific name</th><th>Detections</th>"
        "<th>Best confidence</th><th>First detected</th><th>Latest detected</th><th>Wikipedia</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


def infer_audio_format(url: str) -> str | None:
    suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
    if suffix in {"flac", "wav", "mp3", "ogg", "m4a", "aac"}:
        return suffix
    return None


@st.cache_data(ttl=300)
def processed_audio_bytes(audio_url: str, audio_mode: str) -> bytes:
    if AudioSegment is None:
        raise RuntimeError("pydub is not installed")

    if shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "Enhanced audio playback needs FFmpeg/ffprobe. "
            "Install it with: conda install -c conda-forge ffmpeg, or brew install ffmpeg."
        )

    audio_bytes = fetch_binary(audio_url)
    audio_format = infer_audio_format(audio_url)
    segment = AudioSegment.from_file(BytesIO(audio_bytes), format=audio_format)

    gain_map = {
        "Gain +6 dB": 6,
        "Gain +12 dB": 12,
        "Gain +18 dB": 18,
        "Gain +24 dB": 24,
    }

    segment = segment + gain_map.get(audio_mode, 0)

    # Peak limiter: after gain is applied, reduce the whole clip if it would exceed the ceiling.
    # This avoids clipping/overloading the dashboard playback output.
    ceiling_dbfs = -1.0
    if segment.max_dBFS > ceiling_dbfs:
        segment = segment.apply_gain(ceiling_dbfs - segment.max_dBFS)

    output = BytesIO()
    segment.export(output, format="wav")
    return output.getvalue()


def render_audio_player(audio_url: str, audio_mode: str):
    if audio_mode == "Original":
        st.audio(audio_url)
        return

    if AudioSegment is None:
        st.warning("Enhanced audio playback needs pydub. Run: pip install pydub")
        st.audio(audio_url)
        return

    try:
        audio_bytes = processed_audio_bytes(audio_url, audio_mode)
        st.audio(audio_bytes, format="audio/wav")
    except Exception as exc:
        st.warning(f"Could not create enhanced audio playback, showing original instead: {exc}")
        st.audio(audio_url)


def full_url(base_url: str, path_or_url) -> str:
    if path_or_url is None:
        return ""

    try:
        if pd.isna(path_or_url):
            return ""
    except Exception:
        pass

    path_or_url = str(path_or_url).strip()
    if not path_or_url:
        return ""

    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url

    return urljoin(base_url.rstrip("/") + "/", path_or_url.lstrip("/"))


def crop_sonogram_frequency(image, low_hz: int, high_hz: int, max_frequency_hz: int = 24000):
    if low_hz <= 0 and high_hz >= max_frequency_hz:
        return image

    if high_hz <= low_hz:
        return image

    original_size = image.size
    width, height = image.size
    low_hz = max(0, min(low_hz, max_frequency_hz))
    high_hz = max(0, min(high_hz, max_frequency_hz))

    # Existing PNG sonograms do not include frequency metadata, so this is a display crop.
    # The mapping assumes low frequencies are at the bottom and high frequencies are at the top.
    y_top = int(height * (1 - (high_hz / max_frequency_hz)))
    y_bottom = int(height * (1 - (low_hz / max_frequency_hz)))
    y_top = max(0, min(y_top, height - 1))
    y_bottom = max(y_top + 1, min(y_bottom, height))

    cropped = image.crop((0, y_top, width, y_bottom))
    return cropped.resize(original_size)


def st_image_stretch(image, caption: str | None = None):
    """Display an image at container width across old and new Streamlit versions.

    Newer Streamlit releases prefer width="stretch". Older releases may raise
    TypeError because they only accept an integer width, so we fall back to the
    older container-width parameter only when needed.
    """
    try:
        st.image(image, caption=caption, width="stretch")
    except TypeError:
        st.image(image, caption=caption, use_column_width=True)


def render_sonogram(
    sonogram_url: str,
    sonogram_mode: str,
    frequency_range_hz: tuple[int, int] = (0, 24000),
    max_frequency_hz: int = 24000,
    contrast_factor: float = 1.0,
):
    full_range = frequency_range_hz[0] <= 0 and frequency_range_hz[1] >= max_frequency_hz

    if Image is None or ImageOps is None or ImageEnhance is None:
        st.warning("Sonogram processing needs Pillow. Run: pip install pillow")
        st_image_stretch(sonogram_url, caption="Sonogram")
        return

    try:
        image_bytes = fetch_binary(sonogram_url)
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        image = crop_sonogram_frequency(
            image,
            low_hz=frequency_range_hz[0],
            high_hz=frequency_range_hz[1],
            max_frequency_hz=max_frequency_hz,
        )

        frequency_label = ""
        if not full_range:
            frequency_label = f" · {frequency_range_hz[0]:,}–{frequency_range_hz[1]:,} Hz display crop"

        if sonogram_mode == "Greyscale":
            processed = ImageOps.grayscale(image)
            caption = f"Sonogram, greyscale{frequency_label}"
        elif sonogram_mode == "Inverted":
            processed = ImageOps.invert(image)
            processed = ImageEnhance.Brightness(processed).enhance(1.05)
            caption = f"Sonogram, inverted{frequency_label}"
        elif sonogram_mode == "Inverted greyscale":
            processed = ImageOps.grayscale(image)
            processed = ImageOps.invert(processed)
            caption = f"Sonogram, inverted greyscale{frequency_label}"
        else:
            processed = image
            caption = f"Sonogram{frequency_label}"

        processed = ImageEnhance.Contrast(processed).enhance(contrast_factor)
        st_image_stretch(processed, caption=caption)
    except Exception as exc:
        st.warning(f"Could not process sonogram, showing original instead: {exc}")
        st_image_stretch(sonogram_url, caption="Sonogram")


def format_datetime(value: str | None) -> str:
    return format_dashboard_datetime(value)


def bool_status(value) -> str:
    if value in (True, 1, "1", "True", "true"):
        return "OK"
    if value in (False, 0, "0", "False", "false"):
        return "Check"
    return "Unknown"


def parse_status_notes(notes: str | None) -> tuple[dict, list[str]]:
    parsed = {}
    remaining = []

    if not notes:
        return parsed, remaining

    for part in str(notes).split(";"):
        item = part.strip()
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            parsed[key.strip()] = value.strip()
        else:
            remaining.append(item)

    return parsed, remaining


def render_small_status_metric(column, label: str, value: str):
    column.markdown(
        f"""
        <div style="border:1px solid rgba(128,128,128,0.25); padding:0.65rem; min-height:4.25rem;">
            <div style="font-size:0.72rem; opacity:0.72; line-height:1.1;">{label}</div>
            <div style="font-size:0.95rem; font-weight:600; line-height:1.2; overflow-wrap:anywhere; margin-top:0.25rem;">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def detections_to_df(detections: list[dict]) -> pd.DataFrame:
    if not detections:
        return pd.DataFrame()

    df = pd.DataFrame(detections)

    if "detected_at" in df.columns:
        df["detected_at_dt"] = pd.to_datetime(df["detected_at"], errors="coerce", utc=True)
        df["detected"] = df["detected_at_dt"].dt.strftime("%d/%m/%y %H:%M")
        df["date"] = df["detected_at_dt"].dt.strftime("%d/%m/%y")
        df["date_sort"] = df["detected_at_dt"].dt.date
        df["hour"] = df["detected_at_dt"].dt.hour.astype("Int64")
        df["weekday"] = df["detected_at_dt"].dt.day_name()
        df["day_hour"] = df["detected_at_dt"].dt.strftime("%d/%m/%y %H:00")

    if "scientific_name" in df.columns:
        df["scientific_name"] = df["scientific_name"].apply(lambda value: clean_display_value(value))

    if "common_name" in df.columns:
        df["common_name"] = df["common_name"].apply(lambda value: clean_display_value(value, fallback="Unknown species"))

    if "confidence" in df.columns:
        df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce")
        df["confidence_percent"] = (df["confidence"] * 100).round(1)

    return df


def require_plotly() -> bool:
    if px is None or go is None:
        st.error("Plotly is not installed. Run: pip install plotly")
        return False
    return True



def render_metric_row(detections: list[dict], status: dict | None):
    df = detections_to_df(detections)

    total = len(detections)
    species = df["common_name"].nunique() if not df.empty and "common_name" in df.columns else 0
    max_conf = df["confidence"].max() if not df.empty and "confidence" in df.columns else None
    cpu = status.get("cpu_temp_c") if status else None

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Detections shown", total)
    col2.metric("Species shown", species)
    col3.metric("Highest confidence", "—" if pd.isna(max_conf) or max_conf is None else f"{max_conf:.2f}")
    col4.metric("Station CPU", "—" if cpu is None else f"{float(cpu):.1f}°C")

def render_station_status(status: dict | None):
    if not status:
        st.info("No station status has been reported yet.")
        return

    cols = st.columns(4)
    render_small_status_metric(cols[0], "Last report", format_datetime(status.get("reported_at")))
    render_small_status_metric(
        cols[1],
        "CPU temperature",
        "—" if status.get("cpu_temp_c") is None else f"{float(status['cpu_temp_c']):.1f}°C",
    )
    render_small_status_metric(
        cols[2],
        "Disk used",
        "—" if status.get("disk_used_percent") is None else f"{float(status['disk_used_percent']):.1f}%",
    )
    render_small_status_metric(
        cols[3],
        "Disk available",
        "—" if status.get("disk_available_gb") is None else f"{float(status['disk_available_gb']):.1f} GB",
    )

    parsed_notes, remaining_notes = parse_status_notes(status.get("notes"))

    health_rows = [
        {"Check": "All monitored services", "Status": bool_status(status.get("services_ok"))},
        {"Check": "Recording service", "Status": bool_status(status.get("recording_ok"))},
        {"Check": "Analysis service", "Status": bool_status(status.get("analysis_ok"))},
        {"Check": "Promotion guard", "Status": bool_status(status.get("promotion_guard_ok"))},
        {"Check": "Network", "Status": bool_status(status.get("network_ok"))},
    ]

    if "human_audio_guard_ok" in parsed_notes:
        health_rows.append({"Check": "Human audio guard", "Status": bool_status(parsed_notes["human_audio_guard_ok"])})
    if "storage_guard_timer_ok" in parsed_notes:
        health_rows.append({"Check": "Storage guard timer", "Status": bool_status(parsed_notes["storage_guard_timer_ok"])})

    health = pd.DataFrame(health_rows)
    display_dataframe_with_clean_headings(health, width="stretch", hide_index=True)

    display_notes = [note for note in remaining_notes if not note.startswith("hostname=")]
    if display_notes:
        st.caption("; ".join(display_notes))


def render_detection_card(
    base_url: str,
    detection: dict,
    sonogram_mode: str = "Inverted",
    audio_mode: str = "Original",
    sonogram_frequency_range: tuple[int, int] = (0, 24000),
    sonogram_contrast: float = 1.35,
):
    species = detection.get("common_name", "Unknown species")
    scientific = detection.get("scientific_name", "")
    confidence = detection.get("confidence")
    event_type = detection.get("event_type") or "detection"
    detected = format_datetime(detection.get("detected_at"))
    station = detection.get("station_name") or detection.get("station_id") or "Unknown station"

    title = f"{species}"
    if confidence is not None:
        title += f" · {float(confidence):.3f}"

    with st.container(border=True):
        st.subheader(title)
        st.caption(f"{scientific} · {detected} · {station} · {event_type}")

        sonogram_url = full_url(base_url, detection.get("sonogram_url"))
        audio_url = full_url(base_url, detection.get("audio_url"))

        left, right = st.columns([2, 1])
        with left:
            if sonogram_url:
                render_sonogram(
                    sonogram_url,
                    sonogram_mode=sonogram_mode,
                    frequency_range_hz=sonogram_frequency_range,
                    contrast_factor=sonogram_contrast,
                )
            else:
                st.info("No sonogram available for this detection.")

        with right:
            if audio_url:
                render_audio_player(audio_url, audio_mode=audio_mode)
                st.link_button("Open original audio file", audio_url)
            else:
                st.info("No audio file available for this detection.")

            if sonogram_url:
                st.link_button("Open original sonogram", sonogram_url)

            render_wikipedia_icon(species, scientific, size_px=104)


def build_summary_rows_from_df(df: pd.DataFrame, period: str) -> list[dict]:
    if df.empty or "detected_at_dt" not in df.columns:
        return []

    summary_df = df.dropna(subset=["detected_at_dt"]).copy()

    if period == "daily":
        summary_df["_period_sort"] = summary_df["detected_at_dt"].dt.date
        summary_df["date"] = summary_df["detected_at_dt"].dt.strftime("%d/%m/%y")
        period_col = "date"
    else:
        week_period = summary_df["detected_at_dt"].dt.to_period("W")
        summary_df["_period_sort"] = week_period.apply(lambda value: value.start_time)
        summary_df["week"] = week_period.apply(
            lambda value: f"{value.start_time.strftime('%d/%m/%y')}–{value.end_time.strftime('%d/%m/%y')}"
        )
        period_col = "week"

    rows = (
        summary_df.groupby(["_period_sort", period_col], as_index=False)
        .agg(
            detections=("id", "count"),
            distinct_species=("common_name", "nunique"),
            average_confidence=("confidence", "mean"),
            max_confidence=("confidence", "max"),
        )
        .sort_values("_period_sort", ascending=True)
        .drop(columns=["_period_sort"])
    )

    rows["average_confidence"] = rows["average_confidence"].round(2)
    rows["max_confidence"] = rows["max_confidence"].round(2)
    return rows.to_dict(orient="records")


def render_summary_chart(title: str, rows: list[dict], date_col: str):
    st.subheader(title)
    if not rows:
        st.info("No summary data available yet.")
        return

    df = pd.DataFrame(rows)

    if date_col in df.columns:
        df["_date_sort"] = df[date_col].apply(dashboard_date_sort_key)
        df[date_col] = df[date_col].apply(format_dashboard_date)

    count_cols = ["detections", "distinct_species", "species_count"]
    confidence_cols = ["avg_confidence", "average_confidence", "max_confidence", "best_confidence"]

    for col in count_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(0).astype("Int64")

    for col in confidence_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

    if "_date_sort" in df.columns:
        df = df.sort_values(["_date_sort", date_col], ascending=[True, True], na_position="last").reset_index(drop=True)

    display_dataframe_with_clean_headings(df.drop(columns=["_date_sort"], errors="ignore"), width="stretch", hide_index=True)

    if date_col in df.columns and "detections" in df.columns:
        chart_columns = [date_col, "detections"] + (["_date_sort"] if "_date_sort" in df.columns else [])
        chart_df = df[chart_columns].copy()
        chart_df = chart_df.sort_values("_date_sort" if "_date_sort" in chart_df.columns else date_col, na_position="last")

        if require_plotly():
            fig = px.bar(
                chart_df,
                x=date_col,
                y="detections",
                color="detections",
                color_continuous_scale="Viridis",
                labels={date_col: date_col.capitalize(), "detections": "Detections"},
                title=title,
            )
            fig.update_layout(
                height=460,
                coloraxis_colorbar=dict(tickformat=",d"),
            )
            fig.update_traces(hovertemplate=f"{date_col.capitalize()}: %{{x}}<br>Detections: %{{y:.0f}}<extra></extra>")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.bar_chart(chart_df, x=date_col, y="detections", use_container_width=True)


def render_top_species_visual(df: pd.DataFrame, top_n: int, interval: str):
    st.subheader("Top species over time")

    if not require_plotly() or df.empty:
        st.info("No detection data available for this visualisation.")
        return

    if interval == "Hour":
        df["period_sort"] = df["detected_at_dt"].dt.floor("h")
        df["period"] = df["period_sort"].dt.strftime("%d/%m/%y %H:00")
    elif interval == "Day":
        df["period_sort"] = df["detected_at_dt"].dt.date
        df["period"] = df["detected_at_dt"].dt.strftime("%d/%m/%y")
    elif interval == "Week":
        week_period = df["detected_at_dt"].dt.to_period("W")
        df["period_sort"] = week_period.apply(lambda period: period.start_time)
        df["period"] = week_period.apply(
            lambda period: f"{period.start_time.strftime('%d/%m/%y')}–{period.end_time.strftime('%d/%m/%y')}"
        )
    else:
        month_period = df["detected_at_dt"].dt.to_period("M")
        df["period_sort"] = month_period.apply(lambda period: period.start_time)
        df["period"] = month_period.apply(lambda period: period.start_time.strftime("%d/%m/%y"))

    top_species = df["common_name"].value_counts().head(top_n).index.tolist()
    plot_df = (
        df[df["common_name"].isin(top_species)]
        .groupby(["period_sort", "period", "common_name"], as_index=False)
        .size()
        .rename(columns={"size": "detections"})
        .sort_values("period_sort")
    )

    if plot_df.empty:
        st.info("No top-species data available for the selected filters.")
        return

    fig = px.bar(
        plot_df,
        x="period",
        y="detections",
        color="common_name",
        title=f"Top {top_n} species by {interval.lower()}",
        labels={"period": interval, "detections": "Detections", "common_name": "Species"},
    )
    period_order = plot_df["period"].drop_duplicates().tolist()
    fig.update_xaxes(categoryorder="array", categoryarray=period_order)
    fig.update_layout(legend_title_text="Species", height=520)
    st.plotly_chart(fig, use_container_width=True)


def render_single_hourly_circular_plot(day_df: pd.DataFrame, metric: str, title: str, chart_key: str):
    value_col = "detections" if metric == "Detections" else "species_count"

    hourly = (
        day_df.groupby("hour", as_index=False)
        .agg(detections=("id", "count"), species_count=("common_name", "nunique"))
        .sort_values("hour")
    )

    all_hours = pd.DataFrame({"hour": list(range(24))})
    hourly = all_hours.merge(hourly, on="hour", how="left").fillna({"detections": 0, "species_count": 0})
    hourly["theta"] = hourly["hour"] * 15
    hourly["label"] = hourly["hour"].map(lambda h: f"{h:02d}:00")

    fig = go.Figure(
        data=[
            go.Barpolar(
                r=hourly[value_col],
                theta=hourly["theta"],
                width=[14] * len(hourly),
                text=hourly["label"],
                marker=dict(
                    color=hourly[value_col],
                    colorscale="Viridis",
                    showscale=True,
                    colorbar=dict(title=metric, tickformat="d"),
                ),
                hovertemplate="Hour: %{text}<br>Count: %{r}<extra></extra>",
            )
        ]
    )
    fig.update_layout(
        title=title,
        polar=dict(
            angularaxis=dict(
                tickmode="array",
                tickvals=[h * 15 for h in range(0, 24, 3)],
                ticktext=[f"{h:02d}:00" for h in range(0, 24, 3)],
                direction="clockwise",
            ),
            radialaxis=dict(
                showticklabels=False,
                ticks="",
                showline=False,
            )
        ),
        height=420,
        margin=dict(l=20, r=20, t=60, b=20),
    )
    st.plotly_chart(fig, use_container_width=True, key=chart_key)


def render_hourly_circular_plot(df: pd.DataFrame):
    st.subheader("Circular detections by hour — last 7 days")

    if not require_plotly() or df.empty:
        st.info("No detection data available for this visualisation.")
        return

    if "detected_at_dt" not in df.columns:
        st.info("No timestamped detection data available for this visualisation.")
        return

    plot_base = df.dropna(subset=["detected_at_dt", "hour"]).copy()
    if plot_base.empty:
        st.info("No timestamped detection data available for this visualisation.")
        return

    plot_base["detected_date"] = plot_base["detected_at_dt"].dt.date
    latest_date = plot_base["detected_date"].max()
    start_date = latest_date - timedelta(days=6)
    last_week_dates = [start_date + timedelta(days=i) for i in range(7)]
    plot_base = plot_base[plot_base["detected_date"].isin(last_week_dates)].copy()

    metric = st.radio(
        "Circular plot metric",
        ["Detections", "Distinct species"],
        horizontal=True,
        key="circular_metric",
    )

    st.caption(
        "Seven daily circular charts are shown for the latest seven dates available in the filtered detection set."
    )

    columns = st.columns(2)
    for index, day in enumerate(last_week_dates):
        day_df = plot_base[plot_base["detected_date"] == day].copy()
        title = f"{metric} by hour — {day.strftime('%d/%m/%y')}"
        with columns[index % 2]:
            render_single_hourly_circular_plot(day_df, metric, title, f"circular_hourly_{day.isoformat()}_{metric}")


def render_hour_day_heatmap(df: pd.DataFrame):
    st.subheader("Hour-by-day heatmap")

    if not require_plotly() or df.empty:
        st.info("No detection data available for this visualisation.")
        return

    metric = st.radio(
        "Heatmap metric",
        ["Detections", "Distinct species", "Average confidence"],
        horizontal=True,
        key="heatmap_metric",
    )

    if metric == "Detections":
        grouped = df.groupby(["date", "hour"], as_index=False).size().rename(columns={"size": "value"})
    elif metric == "Distinct species":
        grouped = df.groupby(["date", "hour"], as_index=False).agg(value=("common_name", "nunique"))
    else:
        grouped = df.groupby(["date", "hour"], as_index=False).agg(value=("confidence", "mean"))

    pivot = grouped.pivot(index="date", columns="hour", values="value").fillna(0)
    if "date_sort" in df.columns:
        date_order = (
            df[["date", "date_sort"]]
            .drop_duplicates()
            .sort_values("date_sort")["date"]
            .tolist()
        )
        pivot = pivot.reindex(index=date_order)
    else:
        pivot = pivot.sort_index()
    pivot = pivot.reindex(columns=list(range(24)), fill_value=0)

    y_dates = pivot.index.tolist()
    y_indices = list(range(len(y_dates)))

    fig = px.imshow(
        pivot.to_numpy(dtype=float),
        x=list(range(24)),
        y=y_indices,
        aspect="auto",
        labels={"x": "Hour", "y": "Date", "color": metric},
        title=f"{metric} per hour per day",
        color_continuous_scale="Viridis",
    )
    fig.update_layout(
        height=560,
        xaxis=dict(
            tickmode="array",
            tickvals=list(range(0, 24, 3)),
            ticktext=[f"{h:02d}:00" for h in range(0, 24, 3)],
        ),
        yaxis=dict(
            tickmode="array",
            tickvals=y_indices,
            ticktext=y_dates,
        ),
    )
    fig.update_traces(
        hovertemplate="Hour: %{x}:00<br>Date: %{customdata}<br>Value: %{z}<extra></extra>",
        customdata=[[day for _ in range(24)] for day in y_dates],
    )
    if metric in {"Detections", "Distinct species"}:
        fig.update_coloraxes(colorbar_tickformat="d")
    st.plotly_chart(fig, use_container_width=True)



def normalise_radius_values(values, min_radius: float = 0.012, max_radius: float = 0.045) -> list[float]:
    numeric = pd.to_numeric(values, errors="coerce").fillna(0)
    min_value = float(numeric.min()) if len(numeric) else 0.0
    max_value = float(numeric.max()) if len(numeric) else 0.0

    if max_value <= min_value:
        return [((min_radius + max_radius) / 2)] * len(numeric)

    normalised = (numeric - min_value) / (max_value - min_value)
    return (min_radius + (normalised ** 1.7) * (max_radius - min_radius)).tolist()


def sample_viridis_colour(value, min_value, max_value) -> str:
    if px is None:
        return "#1f77b4"

    try:
        value = float(value)
        min_value = float(min_value)
        max_value = float(max_value)
    except Exception:
        return px.colors.sample_colorscale("Viridis", [0.5])[0]

    if max_value <= min_value:
        position = 0.75
    else:
        position = max(0.0, min(1.0, (value - min_value) / (max_value - min_value)))

    return px.colors.sample_colorscale("Viridis", [position])[0]


def ellipsoid_mesh_points(
    center_x: float,
    center_y: float,
    center_z: float,
    radius_x: float,
    radius_y: float,
    radius_z: float,
    lat_steps: int = 8,
    lon_steps: int = 14,
):
    x_values = []
    y_values = []
    z_values = []

    for lat_index in range(lat_steps + 1):
        phi = -math.pi / 2 + math.pi * lat_index / lat_steps
        cos_phi = math.cos(phi)
        sin_phi = math.sin(phi)

        for lon_index in range(lon_steps):
            theta = 2 * math.pi * lon_index / lon_steps
            x_values.append(center_x + radius_x * cos_phi * math.cos(theta))
            y_values.append(center_y + radius_y * cos_phi * math.sin(theta))
            z_values.append(center_z + radius_z * sin_phi)

    i_values = []
    j_values = []
    k_values = []

    for lat_index in range(lat_steps):
        for lon_index in range(lon_steps):
            p0 = lat_index * lon_steps + lon_index
            p1 = lat_index * lon_steps + ((lon_index + 1) % lon_steps)
            p2 = (lat_index + 1) * lon_steps + lon_index
            p3 = (lat_index + 1) * lon_steps + ((lon_index + 1) % lon_steps)

            i_values.extend([p0, p1])
            j_values.extend([p2, p2])
            k_values.extend([p1, p3])

    return x_values, y_values, z_values, i_values, j_values, k_values


def add_zoom_scaling_node_mesh(
    fig,
    center_x: float,
    center_y: float,
    center_z: float,
    radius_x: float,
    radius_y: float,
    radius_z: float,
    colour: str,
    hover_text: str,
    name: str = "",
    legendgroup: str | None = None,
    showlegend: bool = False,
):
    x_values, y_values, z_values, i_values, j_values, k_values = ellipsoid_mesh_points(
        center_x=center_x,
        center_y=center_y,
        center_z=center_z,
        radius_x=radius_x,
        radius_y=radius_y,
        radius_z=radius_z,
    )

    fig.add_trace(
        go.Mesh3d(
            x=x_values,
            y=y_values,
            z=z_values,
            i=i_values,
            j=j_values,
            k=k_values,
            color=colour,
            opacity=0.9,
            name=name,
            legendgroup=legendgroup,
            showlegend=showlegend,
            hovertext=hover_text,
            hoverinfo="text",
            flatshading=False,
        )
    )




def species_combo_parts(combo_key: str) -> list[str]:
    value = str(combo_key or "").strip()
    for separator in ["<br>", "<br/>", "<br />", "\n", ";", " | "]:
        value = value.replace(separator, ",")
    return [part.strip() for part in value.split(",") if part.strip()]


def matching_species_combo_link_palette() -> list[str]:
    return [
        "rgba(230, 25, 75, 0.78)",
        "rgba(60, 180, 75, 0.78)",
        "rgba(0, 130, 200, 0.78)",
        "rgba(245, 130, 48, 0.78)",
        "rgba(145, 30, 180, 0.78)",
        "rgba(70, 240, 240, 0.78)",
        "rgba(240, 50, 230, 0.78)",
        "rgba(210, 245, 60, 0.78)",
        "rgba(250, 190, 190, 0.78)",
        "rgba(0, 128, 128, 0.78)",
        "rgba(220, 190, 255, 0.78)",
        "rgba(170, 110, 40, 0.78)",
        "rgba(255, 250, 200, 0.78)",
        "rgba(128, 0, 0, 0.78)",
        "rgba(170, 255, 195, 0.78)",
        "rgba(0, 0, 128, 0.78)",
    ]


def build_matching_species_combo_colour_map(plot_df: pd.DataFrame) -> dict[str, dict]:
    """Assign stable link labels and colours to repeated species-set paths."""
    if plot_df.empty or "species_combo_key" not in plot_df.columns:
        return {}

    groups = []
    for combo_key, combo_df in plot_df.groupby("species_combo_key", dropna=True):
        combo_key = str(combo_key).strip()
        if not combo_key:
            continue
        combo_df = combo_df.sort_values(["date_sort", "hour"]).copy()
        if len(combo_df) < 2:
            continue
        total_detections = int(combo_df["detections"].sum()) if "detections" in combo_df.columns else 0
        groups.append((combo_key, len(combo_df), total_detections))

    groups.sort(key=lambda item: (item[1], item[2]), reverse=True)
    palette = matching_species_combo_link_palette()

    return {
        combo_key: {
            "label": f"Species set {index + 1}",
            "colour": palette[index % len(palette)],
            "rank": index + 1,
        }
        for index, (combo_key, _node_count, _total_detections) in enumerate(groups)
    }


def colour_swatch_html(colour: str, label: str | None = None) -> str:
    if not colour:
        return ""

    safe_colour = html.escape(str(colour), quote=True)
    safe_label = html.escape(str(label or colour))
    return (
        '<span style="display:inline-flex; align-items:center; gap:0.4rem; white-space:nowrap;">'
        f'<span style="display:inline-block; width:1rem; height:1rem; border:1px solid rgba(128,128,128,0.55); background:{safe_colour};"></span>'
        f'<span>{safe_label}</span>'
        '</span>'
    )



def build_matching_species_combo_summary(plot_df: pd.DataFrame, colour_map: dict | None = None) -> pd.DataFrame:
    if plot_df.empty or "species_combo_key" not in plot_df.columns:
        return pd.DataFrame()

    colour_map = colour_map or build_matching_species_combo_colour_map(plot_df)
    rows = []

    for combo_key, combo_df in plot_df.groupby("species_combo_key", dropna=True):
        combo_key = str(combo_key).strip()
        if not combo_key:
            continue

        node_count = int(len(combo_df))
        if node_count < 2:
            continue

        species = species_combo_parts(combo_key)
        total_detections = int(combo_df["detections"].sum()) if "detections" in combo_df.columns else 0
        max_bin_detections = int(combo_df["detections"].max()) if "detections" in combo_df.columns else 0
        average_confidence = (
            float(combo_df["average_confidence"].mean())
            if "average_confidence" in combo_df.columns
            else None
        )

        first_date = ""
        last_date = ""
        if "date_sort" in combo_df.columns:
            sorted_dates = combo_df["date_sort"].dropna().sort_values()
            if not sorted_dates.empty:
                first_date = sorted_dates.iloc[0].strftime("%d/%m/%y")
                last_date = sorted_dates.iloc[-1].strftime("%d/%m/%y")

        link_info = colour_map.get(combo_key, {})
        link_label = link_info.get("label", "")
        rows.append(
            {
                "link label": link_label,
                "species set": combo_key,
                "nodes": node_count,
                "links": max(node_count - 1, 0),
                "species": len(species),
                "total detections": total_detections,
                "max bin detections": max_bin_detections,
                "average confidence": None if average_confidence is None else round(average_confidence, 2),
                "first date": first_date,
                "last date": last_date,
            }
        )

    return pd.DataFrame(rows)


def render_matching_species_combo_cluster_figures(plot_df: pd.DataFrame, colour_map: dict | None = None):
    colour_map = colour_map or build_matching_species_combo_colour_map(plot_df)
    summary_df = build_matching_species_combo_summary(plot_df, colour_map=colour_map)

    if summary_df.empty:
        st.info("No repeated species-set clusters were found in the current filtered data.")
        return

    most_interconnected = summary_df.sort_values(
        ["nodes", "links", "total detections"],
        ascending=False,
    ).iloc[0]

    most_species = summary_df.sort_values(
        ["species", "nodes", "total detections"],
        ascending=False,
    ).iloc[0]

    most_detections = summary_df.sort_values(
        ["total detections", "nodes", "species"],
        ascending=False,
    ).iloc[0]

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Largest linked cluster",
            f"{int(most_interconnected['nodes'])} nodes",
            f"{int(most_interconnected['links'])} links",
        )
        st.caption(str(most_interconnected["species set"]))

    with col2:
        st.metric(
            "Most species in a cluster",
            f"{int(most_species['species'])} species",
            f"{int(most_species['nodes'])} nodes",
        )
        st.caption(str(most_species["species set"]))

    with col3:
        st.metric(
            "Most detections in a cluster",
            f"{int(most_detections['total detections'])} detections",
            f"{int(most_detections['species'])} species",
        )
        st.caption(str(most_detections["species set"]))

    with st.expander("Top linked species-set clusters", expanded=False):
        st.caption("Use the chart legend to show/hide individual species-set link paths.")
        display_df = summary_df.sort_values(
            ["nodes", "total detections", "species"],
            ascending=False,
        ).head(20)
        display_dataframe_with_clean_headings(display_df, use_container_width=True, hide_index=True)


def add_matching_species_combo_links(
    fig,
    plot_df: pd.DataFrame,
    colour_map: dict | None = None,
    hidden_largest_count: int = DEFAULT_HIDDEN_LARGEST_LINK_SETS,
):
    """Draw coloured 3D line paths between hour/date bins with exactly the same species combination."""
    if plot_df.empty or "species_combo_key" not in plot_df.columns:
        return

    colour_map = colour_map or build_matching_species_combo_colour_map(plot_df)

    groups = []
    for combo_key, combo_df in plot_df.groupby("species_combo_key", dropna=True):
        combo_key = str(combo_key).strip()
        if not combo_key:
            continue

        combo_df = combo_df.sort_values(["date_sort", "hour"]).copy()
        if len(combo_df) < 2:
            continue

        groups.append((combo_key, combo_df))

    groups.sort(key=lambda item: (len(item[1]), item[1]["detections"].sum()), reverse=True)

    for group_index, (combo_key, combo_df) in enumerate(groups):
        link_info = colour_map.get(combo_key, {})
        colour = link_info.get("colour", "rgba(128, 128, 128, 0.78)")
        link_label = link_info.get("label", "Species set")
        species_count = len(species_combo_parts(combo_key))
        total_detections = int(combo_df["detections"].sum()) if "detections" in combo_df.columns else 0

        hidden_by_default = group_index < max(0, int(hidden_largest_count))

        fig.add_trace(
            go.Scatter3d(
                x=combo_df["hour"],
                y=combo_df["date_position"],
                z=combo_df["detections"],
                mode="lines",
                line=dict(color=colour, width=4),
                hovertext=[
                    f"{html.escape(link_label)}<br>"
                    "Matching species set<br>"
                    f"Linked nodes: {len(combo_df)}<br>"
                    f"Species in set: {species_count}<br>"
                    f"Total detections in linked nodes: {total_detections}<br>"
                    f"Species: {html.escape(combo_key)}"
                ] * len(combo_df),
                hoverinfo="text",
                name=link_label,
                legendgroup=f"species_set_link::{link_label}",
                showlegend=True,
                visible="legendonly" if hidden_by_default else True,
            )
        )

def add_invisible_colourbar_trace(fig, df: pd.DataFrame, x_col: str, y_col: str, z_col: str, color_col: str, title: str):
    fig.add_trace(
        go.Scatter3d(
            x=df[x_col],
            y=df[y_col],
            z=df[z_col],
            mode="markers",
            marker=dict(
                size=1,
                opacity=0,
                color=df[color_col],
                colorscale="Viridis",
                showscale=True,
                colorbar=dict(title=title, tickformat="d", x=1.02, y=0.48, len=0.6),
            ),
            hoverinfo="skip",
            showlegend=False,
        )
    )


def add_confidence_size_column(df: pd.DataFrame, source_col: str = "average_confidence") -> pd.DataFrame:
    df = df.copy()
    values = pd.to_numeric(df[source_col], errors="coerce").fillna(0)
    min_value = float(values.min())
    max_value = float(values.max())

    if max_value <= min_value:
        df["confidence_size"] = 30
    else:
        normalised = (values - min_value) / (max_value - min_value)
        df["confidence_size"] = 8 + (normalised ** 1.7) * 92

    return df


def selected_plotly_points(event):
    if event is None:
        return []

    try:
        return list(event.selection.points)
    except Exception:
        pass

    try:
        return list(event.get("selection", {}).get("points", []))
    except Exception:
        return []


def render_selectable_plotly_chart(fig, key: str):
    try:
        return st.plotly_chart(
            fig,
            use_container_width=True,
            key=key,
            on_select="rerun",
            selection_mode="points",
        )
    except TypeError:
        st.plotly_chart(fig, use_container_width=True)
        return None


def render_3d_hour_date_detection_scatter(df: pd.DataFrame, zoom_scaling_nodes: bool = False, link_matching_species_combinations: bool = False):
    st.subheader("3D hour × date × detections scatter")
    st.caption(
        "Each node is an hour/date bin. The Z axis shows detection count, colour shows the number of distinct species, "
        "and node size shows average confidence. Hover over a node to read the distinct species detected in that bin."
    )

    if not require_plotly() or df.empty:
        st.info("No detection data available for this visualisation.")
        return

    plot_base = df.dropna(subset=["detected_at_dt", "hour"]).copy()
    if plot_base.empty:
        st.info("No timestamped detection data available for this visualisation.")
        return

    plot_base["hour"] = plot_base["hour"].astype(int)
    plot_base["hour_label"] = plot_base["hour"].map(lambda h: f"{h:02d}:00")
    plot_base["date"] = plot_base["detected_at_dt"].dt.strftime("%d/%m/%y")
    plot_base["date_sort"] = plot_base["detected_at_dt"].dt.date

    grouped = plot_base.groupby(["date", "date_sort", "hour", "hour_label"], as_index=False).agg(
        detections=("id", "count"),
        distinct_species=("common_name", "nunique"),
        species_list=("common_name", lambda values: ", ".join(sorted(set(v for v in values.dropna())))),
        average_confidence=("confidence", "mean"),
        best_confidence=("confidence", "max"),
    )

    grouped = grouped[grouped["detections"].fillna(0) > 0].copy()
    if grouped.empty:
        st.info("No non-zero hour × date values available for the selected filters.")
        return

    date_key = grouped[["date", "date_sort"]].drop_duplicates().sort_values("date_sort").reset_index(drop=True)
    date_key["date_index"] = date_key.index
    date_key["date_position"] = date_key["date_index"] + 0.5
    plot_df = grouped.merge(date_key, on=["date", "date_sort"], how="left")
    plot_df = add_confidence_size_column(plot_df, source_col="average_confidence")
    plot_df["species_combo_key"] = plot_df["species_list"].fillna("").astype(str)

    max_detections = float(plot_df["detections"].max()) if not plot_df.empty else 1.0
    z_range_max = max(1.0, max_detections * 1.15)
    date_axis_max = max(float(len(date_key)), 1.0)

    if zoom_scaling_nodes:
        fig = go.Figure()
        radius_values = normalise_radius_values(plot_df["average_confidence"])
        x_span = 23
        y_span = date_axis_max
        z_span = z_range_max
        colour_min = float(plot_df["distinct_species"].min())
        colour_max = float(plot_df["distinct_species"].max())

        for row, radius_scale in zip(plot_df.to_dict(orient="records"), radius_values):
            hover_text = (
                f"Date: {html.escape(str(row.get('date', '')))}<br>"
                f"Hour: {html.escape(str(row.get('hour_label', '')))}<br>"
                f"Detections: {int(row.get('detections', 0))}<br>"
                f"Distinct species: {int(row.get('distinct_species', 0))}<br>"
                f"Distinct species listed: {html.escape(str(row.get('species_list', '')))}<br>"
                f"Average confidence: {float(row.get('average_confidence', 0)):.2f}<br>"
                f"Best confidence: {float(row.get('best_confidence', 0)):.2f}"
            )
            add_zoom_scaling_node_mesh(
                fig=fig,
                center_x=float(row["hour"]),
                center_y=float(row["date_position"]),
                center_z=float(row["detections"]),
                radius_x=x_span * radius_scale,
                radius_y=y_span * radius_scale,
                radius_z=z_span * radius_scale,
                colour=sample_viridis_colour(row.get("distinct_species", 0), colour_min, colour_max),
                hover_text=hover_text,
                name="Hour/date bin",
            )

        add_invisible_colourbar_trace(
            fig,
            plot_df,
            x_col="hour",
            y_col="date_position",
            z_col="detections",
            color_col="distinct_species",
            title="Distinct species",
        )
        fig.update_layout(title="3D scatter: hour, date and detection count")
    else:
        fig = px.scatter_3d(
            plot_df,
            x="hour",
            y="date_position",
            z="detections",
            size="confidence_size",
            color="distinct_species",
            color_continuous_scale="Viridis",
            size_max=34,
            hover_data={
                "date": True,
                "hour_label": True,
                "species_list": True,
                "species_combo_key": False,
                "detections": True,
                "distinct_species": True,
                "average_confidence": ":.2f",
                "best_confidence": ":.2f",
                "confidence_size": False,
                "hour": False,
                "date_index": False,
                "date_position": False,
            },
            title="3D scatter: hour, date and detection count",
            labels={
                "hour": "Hour",
                "date_position": "Date",
                "detections": "Detections",
                "distinct_species": "Distinct species",
                "average_confidence": "Average confidence",
                "species_list": "Distinct species listed",
            },
        )

    if link_matching_species_combinations:
        st.caption(
            "Species-combination linking is enabled: bins with exactly the same distinct-species list are connected by coloured 3D line paths. "
            "Click species-set entries in the chart legend to show or hide those cluster paths. "
            "The largest link sets start hidden so smaller patterns are easier to see first."
        )
        species_set_colour_map = build_matching_species_combo_colour_map(plot_df)
        render_matching_species_combo_cluster_figures(plot_df, colour_map=species_set_colour_map)
        add_matching_species_combo_links(fig, plot_df, colour_map=species_set_colour_map)

    layout_margin = dict(l=0, r=280 if link_matching_species_combinations else 0, b=0, t=60)
    legend_layout = dict(
        title_text="Species-set links",
        x=1.12,
        xanchor="left",
        y=1.0,
        yanchor="top",
        bgcolor="rgba(255,255,255,0.72)",
        bordercolor="rgba(128,128,128,0.35)",
        borderwidth=1,
        itemsizing="constant",
        itemclick="toggle",
        itemdoubleclick=False,
    ) if link_matching_species_combinations else None

    fig.update_layout(
        height=760,
        legend=legend_layout,
        scene=dict(
            xaxis=dict(
                title=dict(text="Hour"),
                tickmode="array",
                tickvals=list(range(0, 24, 3)),
                ticktext=[f"{h:02d}:00" for h in range(0, 24, 3)],
                range=[0, 23],
                showspikes=False,
            ),
            yaxis=dict(
                title=dict(text="Date"),
                tickmode="array",
                tickvals=date_key["date_position"].tolist(),
                ticktext=date_key["date"].tolist(),
                range=[0, date_axis_max],
                showspikes=False,
            ),
            zaxis=dict(title=dict(text="Detections"), range=[0, z_range_max], showspikes=False),
            aspectmode="cube",
        ),
        margin=layout_margin,
    )
    if not zoom_scaling_nodes:
        fig.update_coloraxes(colorbar_tickformat="d", colorbar=dict(x=1.02, y=0.48, len=0.6))

    st.plotly_chart(fig, use_container_width=True)


def add_same_species_activity_links(
    fig,
    plot_df: pd.DataFrame,
    species_colours: dict[str, str],
    hidden_largest_count: int = DEFAULT_HIDDEN_LARGEST_LINK_SETS,
):
    """Draw independently toggleable lines between nodes for the same species.

    Link traces deliberately use a separate legendgroup from the node traces. This means
    clicking a link legend item hides/shows only the path, not the actual species nodes.
    """
    if plot_df.empty or "common_name" not in plot_df.columns:
        return

    groups = []
    for species_name, species_df in plot_df.groupby("common_name", dropna=True):
        species_name = str(species_name).strip()
        if not species_name:
            continue

        species_df = species_df.sort_values(["date_sort", "hour"]).copy()
        if len(species_df) < 2:
            continue

        total_detections = int(species_df["detection_count"].sum()) if "detection_count" in species_df.columns else 0
        groups.append((species_name, species_df, total_detections))

    groups.sort(key=lambda item: (len(item[1]), item[2]), reverse=True)

    for group_index, (species_name, species_df, total_detections) in enumerate(groups):
        first_date = species_df["date"].iloc[0] if "date" in species_df.columns else ""
        last_date = species_df["date"].iloc[-1] if "date" in species_df.columns else ""
        hidden_by_default = group_index < max(0, int(hidden_largest_count))

        fig.add_trace(
            go.Scatter3d(
                x=species_df["date_position"],
                y=species_df["hour"],
                z=species_df["average_confidence"],
                mode="lines",
                line=dict(color=species_colours.get(species_name, "#1f77b4"), width=3),
                hovertext=[
                    "Same-species activity path<br>"
                    f"Species: {html.escape(species_name)}<br>"
                    f"Linked nodes: {len(species_df)}<br>"
                    f"Total detections in linked nodes: {total_detections}<br>"
                    f"Date span: {html.escape(str(first_date))}–{html.escape(str(last_date))}"
                ] * len(species_df),
                hoverinfo="text",
                name=f"{species_name} links",
                legendgroup=f"species_activity_link::{species_name}",
                showlegend=True,
                visible="legendonly" if hidden_by_default else True,
            )
        )


def render_3d_species_scatter(
    df: pd.DataFrame,
    top_n: int,
    zoom_scaling_nodes: bool = False,
    link_same_species_nodes: bool = False,
):
    st.subheader("3D species activity scatter")

    if not require_plotly() or df.empty:
        st.info("No detection data available for this visualisation.")
        return

    top_species = df["common_name"].value_counts().head(top_n).index.tolist()
    filtered = df[df["common_name"].isin(top_species)].copy()

    if filtered.empty:
        st.info("No species activity data available for the selected filters.")
        return

    grouped = (
        filtered.groupby(["date", "date_sort", "hour", "common_name", "scientific_name"], as_index=False)
        .agg(
            detection_count=("id", "count"),
            best_confidence=("confidence", "max"),
            average_confidence=("confidence", "mean"),
            first_detected=("detected", "min"),
            latest_detected=("detected", "max"),
        )
    )

    date_key = grouped[["date", "date_sort"]].drop_duplicates().sort_values("date_sort").reset_index(drop=True)
    date_key["date_index"] = date_key.index
    date_key["date_position"] = date_key["date_index"] + 0.5
    plot_df = grouped.merge(date_key, on=["date", "date_sort"], how="left")
    plot_df["hour_label"] = plot_df["hour"].map(lambda h: f"{int(h):02d}:00")

    species_names = [species for species in top_species if species in set(plot_df["common_name"].astype(str))]
    palette = px.colors.qualitative.Plotly if px is not None else ["#1f77b4"]
    species_colours = {
        species: palette[index % len(palette)]
        for index, species in enumerate(species_names)
    }

    confidence_values = pd.to_numeric(plot_df["average_confidence"], errors="coerce").dropna()
    if confidence_values.empty:
        z_min, z_max = 0.0, 1.0
    else:
        z_min = float(confidence_values.min())
        z_max = float(confidence_values.max())
        padding = max((z_max - z_min) * 0.12, 0.01)
        z_min = max(0.0, z_min - padding)
        z_max = min(1.0, z_max + padding)
        if z_max <= z_min:
            z_min = max(0.0, z_min - 0.01)
            z_max = min(1.0, z_max + 0.01)

    date_axis_max = max(float(len(date_key)), 1.0)

    if zoom_scaling_nodes:
        fig = go.Figure()
        radius_values = normalise_radius_values(plot_df["detection_count"])
        x_span = date_axis_max
        y_span = 23
        z_span = max(z_max - z_min, 0.01)

        legend_seen = set()

        for row, radius_scale in zip(plot_df.to_dict(orient="records"), radius_values):
            species_name = str(row.get("common_name") or "Unknown species")
            hover_text = (
                f"Date: {html.escape(str(row.get('date', '')))}<br>"
                f"Hour: {html.escape(str(row.get('hour_label', '')))}<br>"
                f"Species: {html.escape(species_name)}<br>"
                f"Scientific name: {html.escape(str(row.get('scientific_name') or ''))}<br>"
                f"Detections: {int(row.get('detection_count', 0))}<br>"
                f"Average confidence: {float(row.get('average_confidence', 0)):.2f}<br>"
                f"Best confidence: {float(row.get('best_confidence', 0)):.2f}<br>"
                f"First detected: {html.escape(str(row.get('first_detected', '')))}<br>"
                f"Latest detected: {html.escape(str(row.get('latest_detected', '')))}"
            )
            add_zoom_scaling_node_mesh(
                fig=fig,
                center_x=float(row["date_position"]),
                center_y=float(row["hour"]),
                center_z=float(row["average_confidence"]),
                radius_x=x_span * radius_scale,
                radius_y=y_span * radius_scale,
                radius_z=z_span * radius_scale,
                colour=species_colours.get(species_name, "#1f77b4"),
                hover_text=hover_text,
                name=species_name,
                legendgroup=species_name,
                showlegend=species_name not in legend_seen,
            )
            legend_seen.add(species_name)

        fig.update_layout(title=f"3D species activity scatter — top {top_n} species")
    else:
        fig = px.scatter_3d(
            plot_df,
            x="date_position",
            y="hour",
            z="average_confidence",
            color="common_name",
            color_discrete_map=species_colours,
            size="detection_count",
            size_max=32,
            hover_data={
                "date": True,
                "hour_label": True,
                "common_name": True,
                "scientific_name": True,
                "detection_count": True,
                "best_confidence": ":.2f",
                "average_confidence": ":.2f",
                "first_detected": True,
                "latest_detected": True,
                "date_index": False,
                "date_position": False,
                "hour": False,
            },
            title=f"3D species activity scatter — top {top_n} species",
            labels={
                "date_position": "Date",
                "hour": "Hour",
                "detection_count": "Detections",
                "common_name": "Species",
                "average_confidence": "Average confidence",
            },
        )

    if link_same_species_nodes:
        st.caption(
            "Same-species linking is enabled: nodes for each species are connected in chronological order using the same colour as that species' nodes. "
            "The link legend controls only the paths, not the species nodes. The largest link sets start hidden by default."
        )
        add_same_species_activity_links(fig, plot_df, species_colours)

    fig.update_layout(
        height=720,
        scene=dict(
            xaxis=dict(
                title=dict(text="Date"),
                tickmode="array",
                tickvals=date_key["date_position"].tolist(),
                ticktext=date_key["date"].tolist(),
                range=[0, date_axis_max],
                showspikes=False,
            ),
            yaxis=dict(
                title=dict(text="Hour"),
                tickmode="array",
                tickvals=list(range(0, 24, 3)),
                ticktext=[f"{h:02d}:00" for h in range(0, 24, 3)],
                range=[0, 23],
                showspikes=False,
            ),
            zaxis=dict(title=dict(text="Average confidence"), range=[z_min, z_max], showspikes=False),
            aspectmode="cube",
        ),
        margin=dict(l=0, r=280 if link_same_species_nodes else 0, b=0, t=60),
        legend=dict(
            title_text="Species / activity links",
            x=1.12,
            xanchor="left",
            y=1.0,
            yanchor="top",
            bgcolor="rgba(255,255,255,0.72)",
            bordercolor="rgba(128,128,128,0.35)",
            borderwidth=1,
            itemsizing="constant",
            itemclick="toggle",
            itemdoubleclick=False,
        ) if link_same_species_nodes else None,
    )
    st.plotly_chart(fig, use_container_width=True)


def show_api_url_dialog():
    if hasattr(st, "dialog"):
        @st.dialog("Connect to Song Catcher API")
        def _api_dialog():
            st.write("Enter the current API base URL for this dashboard session.")
            current = st.session_state.get("api_url_input", DEFAULT_API_URL)
            submitted = st.text_input("API base URL", value=current, key="api_url_dialog_input")
            if st.button("Connect", type="primary"):
                st.session_state["api_url"] = submitted.strip().rstrip("/")
                st.session_state["api_url_input"] = st.session_state["api_url"]
                st.cache_data.clear()
                st.rerun()

        _api_dialog()
    else:
        st.warning("Enter the current API URL below to connect the dashboard.")
        submitted = st.text_input("API base URL", value=st.session_state.get("api_url_input", DEFAULT_API_URL))
        if st.button("Connect", type="primary"):
            st.session_state["api_url"] = submitted.strip().rstrip("/")
            st.session_state["api_url_input"] = st.session_state["api_url"]
            st.cache_data.clear()
            st.rerun()


def get_api_url() -> str | None:
    if "api_url" not in st.session_state or not st.session_state["api_url"]:
        st.session_state["api_url"] = DEFAULT_API_URL.strip().rstrip("/")
        st.session_state["api_url_input"] = st.session_state["api_url"]

    return st.session_state["api_url"]


def main():
    st.title("Song Catcher Public Dashboard")
    st.caption("Open-access view of promoted BirdNET detections, station health, sonograms and audio clips.")

    api_url = get_api_url()
    if not api_url:
        st.info("Waiting for an API URL before loading dashboard data.")
        st.stop()

    with st.sidebar:
        st.header("API")
        st.caption(f"Connected to: {api_url}")
        if st.button("Change API URL"):
            st.session_state["api_url"] = ""
            show_api_url_dialog()
            st.stop()

        if st.button("Clear cache / refresh"):
            st.cache_data.clear()
            st.rerun()

    try:
        stations_payload = api_get(api_url, "/api/v1/stations")
        stations = stations_payload.get("stations", [])
        for station in stations:
            if station.get("id") == "songcatcher-01":
                station["location_name"] = DISPLAY_LOCATION_NAME
    except Exception as exc:
        st.error(f"Could not reach API: {exc}")
        st.stop()

    if not stations:
        st.warning("No stations have been registered yet.")
        st.stop()

    station_options = {f"{s.get('name', s.get('id'))} ({s.get('id')})": s.get("id") for s in stations}

    with st.sidebar:
        st.header("Filters")
        selected_station_labels = st.multiselect(
            "Station",
            options=list(station_options.keys()),
            default=list(station_options.keys())[:1],
        )
        selected_station_ids = [station_options[label] for label in selected_station_labels]
        detection_source = st.selectbox(
            "Detection source",
            options=["Promoted detections", "Unfiltered metadata", "Combined promoted + unfiltered metadata"],
            index=2,
        )
        species_filter_placeholder = st.empty()
        if not selected_station_ids:
            st.warning("Select at least one station.")
            st.stop()
        min_confidence = st.slider("Minimum confidence", 0.0, 1.0, 0.50, 0.01)
        max_results = st.slider("Maximum results", 10, 20000, 10000, 100)

        default_to = date.today()
        default_from = default_to - timedelta(days=6)
        date_from = uk_date_input("From date", value=default_from, key="date_from")
        date_to = uk_date_input("To date", value=default_to, key="date_to")

        show_cards = st.toggle("Show detection cards", value=True)
        show_table = st.toggle("Show table", value=True)
        show_3d = st.toggle("Enable 3D visualisations", value=False)

        st.header("Media display")
        sonogram_mode = st.selectbox(
            "Sonogram display mode",
            ["Original", "Greyscale", "Inverted", "Inverted greyscale"],
            index=0,
        )
        audio_mode = st.selectbox(
            "Audio playback mode",
            ["Original", "Gain +6 dB", "Gain +12 dB", "Gain +18 dB", "Gain +24 dB"],
            index=2,
        )
        sonogram_frequency_range = st.slider(
            "Sonogram frequency range (Hz)",
            min_value=0,
            max_value=24000,
            value=(0, 12000),
            step=500,
        )
        sonogram_contrast = st.slider(
            "Sonogram contrast",
            min_value=1.0,
            max_value=3.0,
            value=3.0,
            step=0.05,
        )
        st.caption("Frequency range is applied as a display crop to the stored sonogram PNGs. Contrast is applied only to dashboard display.")

    status_station_id = selected_station_ids[0]
    status_payload = api_get(api_url, f"/api/v1/stations/{status_station_id}/status")
    status = status_payload.get("status")

    base_params = {
        "limit": max_results,
    }
    if min_confidence > 0:
        base_params["min_confidence"] = min_confidence
    if date_from:
        base_params["date_from"] = f"{date_from.isoformat()}T00:00:00"
    if date_to:
        base_params["date_to"] = f"{date_to.isoformat()}T23:59:59"

    detections = []
    for selected_station_id in selected_station_ids:
        params = dict(base_params)
        params["station_id"] = selected_station_id
        detections_payload = get_detection_search_payload(api_url, params, detection_source)
        detections.extend(detections_payload.get("detections", []))

    for detection in detections:
        if detection.get("station_id") == "songcatcher-01":
            detection["location_name"] = DISPLAY_LOCATION_NAME
    df = detections_to_df(detections)
    if not df.empty and "detected_at_dt" in df.columns:
        df = df[df["detected_at_dt"].notna()].copy()

    if not df.empty and "common_name" in df.columns:
        species_name_normalised = df["common_name"].astype(str).str.strip().str.casefold()
        df = df[~species_name_normalised.isin(PERMANENTLY_EXCLUDED_SPECIES)].copy()

    if not df.empty and "common_name" in df.columns:
        species_options = sorted(df["common_name"].dropna().astype(str).unique().tolist())
        with species_filter_placeholder.container():
            selected_species = st.multiselect(
                "Include species",
                options=species_options,
                default=[],
                help="Leave blank to include all species before applying the exclusion filter.",
            )
            excluded_species = st.multiselect(
                "Exclude species",
                options=species_options,
                default=[],
                help="Species selected here are removed from the dashboard even if they also appear in the include filter.",
            )
            st.caption("Permanently excluded from all views: Common Redstart")
        if selected_species:
            df = df[df["common_name"].astype(str).isin(selected_species)].copy()
        if excluded_species:
            df = df[~df["common_name"].astype(str).isin(excluded_species)].copy()

    if not df.empty and "detected_at_dt" in df.columns:
        df = df.sort_values("detected_at_dt", ascending=False).reset_index(drop=True)

    detections = df.to_dict(orient="records") if not df.empty else []

    if not df.empty and "detected_at_dt" in df.columns:
        latest_detection = df["detected_at_dt"].max()
        oldest_detection = df["detected_at_dt"].min()
        st.caption(
            "Loaded newest-first detections from "
            f"{oldest_detection.strftime('%d/%m/%y %H:%M')} to {latest_detection.strftime('%d/%m/%y %H:%M')} "
            f"after filters ({len(df):,} records)."
        )

    render_metric_row(detections, status)
    st.caption(f"Detection source: {detection_source}")

    tab_latest, tab_visuals, tab_summary, tab_species, tab_status, tab_about = st.tabs(
        ["Latest detections", "Visualisations", "Summaries", "Species", "Station status", "About"]
    )

    with tab_latest:
        st.header("Latest detections")
        if not detections:
            st.info("No detections match the current filters.")
        else:
            if show_table:
                table_df = df.copy()
                if "audio_url" in table_df.columns:
                    table_df["audio_url"] = table_df["audio_url"].apply(lambda value: full_url(api_url, value))
                if "sonogram_url" in table_df.columns:
                    table_df["sonogram_url"] = table_df["sonogram_url"].apply(lambda value: full_url(api_url, value))
                render_latest_detections_html_table(table_df)

            if show_cards:
                def render_detection_cards_section(title: str, section_detections: list[dict]):
                    st.subheader(title)
                    if not section_detections:
                        st.info(f"No {title.lower()} match the current filters.")
                        return
                    for detection in section_detections:
                        render_detection_card(
                            api_url,
                            detection,
                            sonogram_mode=sonogram_mode,
                            audio_mode=audio_mode,
                            sonogram_frequency_range=sonogram_frequency_range,
                            sonogram_contrast=sonogram_contrast,
                        )

                if detection_source in {"Combined promoted + unfiltered", "Combined promoted + unfiltered metadata"}:
                    promoted_cards = [
                        detection for detection in detections
                        if detection.get("_dashboard_source_group") == "promoted"
                    ]
                    unfiltered_cards = [
                        detection for detection in detections
                        if detection.get("_dashboard_source_group") == "unfiltered"
                    ]

                    render_detection_cards_section("Promoted detections", promoted_cards)
                    st.divider()
                    render_detection_cards_section("Unfiltered metadata detections", unfiltered_cards)
                else:
                    for detection in detections:
                        render_detection_card(
                            api_url,
                            detection,
                            sonogram_mode=sonogram_mode,
                            audio_mode=audio_mode,
                            sonogram_frequency_range=sonogram_frequency_range,
                            sonogram_contrast=sonogram_contrast,
                        )

    with tab_visuals:
        st.header("Detection visualisations")
        if df.empty:
            st.info("No detections match the current filters.")
        else:
            viz_col1, viz_col2, viz_col3 = st.columns(3)
            with viz_col1:
                top_n = st.slider("Top species", 3, 30, 30, 1)
            with viz_col2:
                interval = st.selectbox("Time interval", ["Hour", "Day", "Week", "Month"], index=1)
            with viz_col3:
                st.metric("Visualised records", len(df))

            render_top_species_visual(df.copy(), top_n=top_n, interval=interval)
            render_hourly_circular_plot(df.copy())
            render_hour_day_heatmap(df.copy())

            if show_3d:
                st.divider()
                zoom_scaling_nodes = st.toggle(
                    "Use zoom-scaling 3D nodes",
                    value=True,
                    help="Render nodes as 3D mesh geometry so they scale with the scene when zooming. This is slower than standard Plotly markers.",
                )
                link_matching_species_combinations = st.toggle(
                    "Link matching species-combination bins",
                    value=True,
                    help="Connect hour/date bins where the exact same set of distinct species was detected. This can become visually busy with large result sets.",
                )
                link_same_species_nodes = st.toggle(
                    "Link same-species activity nodes",
                    value=True,
                    help="Connect nodes belonging to the same species in the 3D species activity scatter. Lines use the same colour as the species nodes.",
                )
                render_3d_hour_date_detection_scatter(
                    df.copy(),
                    zoom_scaling_nodes=zoom_scaling_nodes,
                    link_matching_species_combinations=link_matching_species_combinations,
                )
                render_3d_species_scatter(
                    df.copy(),
                    top_n=top_n,
                    zoom_scaling_nodes=zoom_scaling_nodes,
                    link_same_species_nodes=link_same_species_nodes,
                )
            else:
                st.info("Turn on 'Enable 3D visualisations' in the sidebar to view hour × date detection scatter plots and 3D species activity.")

    with tab_status:
        st.header("Station status")
        render_station_status(status)

        with st.expander("Registered stations"):
            stations_df = pd.DataFrame(stations)
            for date_column in ["created_at", "latest_reported_at"]:
                if date_column in stations_df.columns:
                    stations_df[date_column] = stations_df[date_column].apply(format_gmt_datetime_seconds)
            display_dataframe_with_clean_headings(stations_df, width="stretch", hide_index=True)

    with tab_summary:
        if detection_source == "Promoted detections":
            daily = api_get(api_url, "/api/v1/summary/daily", params={"station_id": status_station_id, "days": 30})
            weekly = api_get(api_url, "/api/v1/summary/weekly", params={"station_id": status_station_id, "weeks": 12})

            render_summary_chart("Daily summary", daily.get("daily_summary", []), "date")
            render_summary_chart("Weekly summary", weekly.get("weekly_summary", []), "week")
        else:
            render_summary_chart("Daily summary", build_summary_rows_from_df(df, "daily"), "date")
            render_summary_chart("Weekly summary", build_summary_rows_from_df(df, "weekly"), "week")

    with tab_species:
        st.header("Species overview")
        if df.empty:
            st.info("No species to show for the current filters.")
        else:
            species_df = (
                df.groupby(["common_name", "scientific_name"], dropna=False)
                .agg(
                    detections=("id", "count"),
                    best_confidence=("confidence", "max"),
                    first_detected=("detected_at_dt", "min"),
                    latest_detected=("detected_at_dt", "max"),
                )
                .reset_index()
                .sort_values(["detections", "best_confidence"], ascending=[False, False])
            )
            species_df["best_confidence"] = species_df["best_confidence"].round(2)
            species_df["first_detected"] = species_df["first_detected"].dt.strftime("%d/%m/%y %H:%M")
            species_df["latest_detected"] = species_df["latest_detected"].dt.strftime("%d/%m/%y %H:%M")
            render_species_html_table(species_df)

            with st.expander("Species Wikipedia links", expanded=True):
                icon_cols = st.columns(4)
                for index, row in species_df.head(30).reset_index(drop=True).iterrows():
                    with icon_cols[index % 4]:
                        st.write(row["common_name"])
                        render_wikipedia_icon(row.get("common_name"), row.get("scientific_name"), size_px=104)

    with tab_about:
        st.header("About this dashboard")
        st.write(
            "This dashboard displays promoted and/or unfiltered Song Catcher detections. "
            "The station analyses audio locally, deletes recordings containing human audio, "
            "and uploads only approved detection metadata, sonograms and promoted audio clips."
        )
        st.write(
            "Current public endpoints used by this dashboard include station status, detection search, "
            "daily summaries, weekly summaries and file access."
        )
        st.write(
            "The visualisation layer includes 2D species-over-time plots, circular hourly activity plots, "
            "hour-by-day heatmaps, and optional 3D scatter plots for exploring temporal patterns."
        )


if __name__ == "__main__":
    main()
