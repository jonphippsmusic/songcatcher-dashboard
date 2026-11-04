
"""Public HabitatLens sampling-effort dashboard.

Contains no authentication credentials and does not access individual
recording events, audio files or station filesystem paths.
"""


def format_effort_duration(value):
    """Format an aggregate duration without implying station uptime."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "Unknown"

    if seconds < 0:
        return "Unknown"

    if seconds >= 3600:
        return f"{seconds / 3600:,.2f} h"

    return f"{seconds / 60:,.1f} min"


def format_completion(value):
    """Preserve unavailable completion measurements as N/A."""
    if value is None:
        return "N/A"

    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def render_habitatlens_tab(
    api_url,
    selected_station_ids,
    date_from,
    date_to,
    api_get,
):
    """Render public, aggregate-only HabitatLens coverage."""

    import pandas as pd
    import streamlit as st

    st.header("HabitatLens")

    st.caption(
        "Explore how much audio each Songcatcher has recorded and "
        "analysed. Sampling effort helps establish the evidence "
        "available for interpreting bird detections."
    )

    if not selected_station_ids:
        st.info("Select a station in the sidebar.")
        return

    station_id = st.selectbox(
        "Station",
        options=list(selected_station_ids),
        key="habitatlens_station",
    )

    scope_label = st.radio(
        "Monitoring context",
        options=[
            "Verified field monitoring",
            "Technical monitoring",
        ],
        horizontal=True,
        key="habitatlens_scope",
    )

    scope = (
        "field"
        if scope_label == "Verified field monitoring"
        else "technical"
    )

    if date_from is None or date_to is None:
        st.info("Select a date range to view sampling effort.")
        return

    if date_from > date_to:
        st.error("The start date must not be after the end date.")
        return

    selected_days = (date_to - date_from).days + 1

    if selected_days > 366:
        st.warning(
            "HabitatLens supports date ranges of up to 366 days. "
            "Please shorten the date range in the sidebar."
        )
        return

    params = {
        "station_id": station_id,
        "scope": scope,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    }

    try:
        payload = api_get(
            api_url,
            "/api/v1/habitatlens/coverage",
            params=params,
        )
    except Exception:
        st.error(
            "HabitatLens coverage is temporarily unavailable. "
            "Other dashboard features are unaffected."
        )
        return

    if not isinstance(payload, dict):
        st.error("Unexpected HabitatLens API response.")
        return

    if (
        payload.get("station_id") != station_id
        or payload.get("scope") != scope
    ):
        st.error("HabitatLens response does not match the selected context.")
        return

    totals = payload.get("totals")

    if not isinstance(totals, dict):
        st.error("HabitatLens coverage totals are unavailable.")
        return

    windows = int(totals.get("windows") or 0)

    if scope == "technical":
        st.warning(
            "Technical monitoring includes workbench recordings. "
            "These measurements describe recording and analysis activity, "
            "not verified ecological field observations."
        )

    if scope == "field" and windows == 0:
        st.info(
            "No verified field sampling effort is available for this "
            "station and date range. This does not mean that no birds "
            "were present or detected."
        )
        return

    columns = st.columns(4)

    columns[0].metric(
        "Recording windows",
        f"{windows:,}",
    )

    columns[1].metric(
        "Recorded audio",
        format_effort_duration(
            totals.get("recorded_seconds")
        ),
    )

    columns[2].metric(
        "Analysed audio",
        format_effort_duration(
            totals.get("analysed_seconds")
        ),
    )

    columns[3].metric(
        "Analysis completion",
        format_completion(
            totals.get("analysis_completion_percent")
        ),
    )

    st.caption(
        "Analysis completion is the proportion of recorded audio "
        "successfully analysed. It is not a measure of continuous "
        "station uptime."
    )

    unknown_windows = int(
        totals.get("unknown_duration_windows") or 0
    )

    if unknown_windows:
        st.warning(
            f"{unknown_windows:,} recording windows have incomplete "
            "duration information."
        )

    status_counts = totals.get("status_counts") or {}

    with st.expander("Recording outcomes", expanded=False):
        st.write({
            "Analysed": status_counts.get("analysed", 0),
            "Recorded only": status_counts.get("recorded_only", 0),
            "Failed": status_counts.get("failed", 0),
            "Unknown": status_counts.get("unknown", 0),
        })

    daily = payload.get("daily") or []

    if not daily:
        st.info("No daily coverage records are available.")
        return

    records = []

    for item in daily:
        if not isinstance(item, dict):
            continue

        records.append({
            "Date": item.get("date"),
            "Recording windows": int(item.get("windows") or 0),
            "Recorded (min)": round(
                float(item.get("recorded_seconds") or 0) / 60,
                2,
            ),
            "Analysed (min)": round(
                float(item.get("analysed_seconds") or 0) / 60,
                2,
            ),
        })

    if not records:
        st.info("No valid daily coverage entries were returned.")
        return

    daily_df = (
        pd.DataFrame(records)
        .sort_values("Date")
        .reset_index(drop=True)
    )

    st.subheader("Daily sampling effort")

    chart_df = daily_df.set_index("Date")[
        ["Recorded (min)", "Analysed (min)"]
    ]

    st.bar_chart(chart_df)

    with st.expander("Daily coverage table"):
        st.dataframe(
            daily_df,
            width="stretch",
            hide_index=True,
        )

    st.caption(
        "Days without reported recording windows are not evidence "
        "of zero bird activity. Missing monitoring evidence must "
        "be interpreted separately from species observations."
    )

    notice = payload.get("notice")

    if notice:
        st.caption(str(notice))
