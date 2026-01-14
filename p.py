import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List

import requests
import pandas as pd
import matplotlib.pyplot as plt


WAQI_BASE = "https://api.waqi.info"


@dataclass
class CityQuery:
    label: str
    waqi_feed_path: str  # what we put after /feed/


def aqi_category_us(aqi: float) -> str:
    """
    US AQI category labels (commonly used). WAQI uses AQI conventions aligned with these ranges.
    Ref: AirNow AQI basics. :contentReference[oaicite:2]{index=2}
    """
    if aqi <= 50:
        return "Good"
    if aqi <= 100:
        return "Moderate"
    if aqi <= 150:
        return "Unhealthy (Sensitive)"
    if aqi <= 200:
        return "Unhealthy"
    if aqi <= 300:
        return "Very Unhealthy"
    return "Hazardous"


def safe_get(d: Dict[str, Any], *keys: str) -> Optional[Any]:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def fetch_waqi_city(token: str, feed_path: str, timeout: int = 25) -> Dict[str, Any]:
    url = f"{WAQI_BASE}/feed/{feed_path}/?token={token}"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    if payload.get("status") != "ok":
        raise RuntimeError(f"WAQI returned status={payload.get('status')}, data={payload.get('data')}")
    return payload["data"]


def parse_city_data(label: str, data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Returns:
      - summary row: AQI, time, station, city, lat/lon, category
      - pollutants row: iaqi components if present (pm25, pm10, o3, no2, so2, co)
    """
    aqi = safe_get(data, "aqi")
    # Sometimes AQI can be "-" or None
    try:
        aqi_num = float(aqi)
    except Exception:
        aqi_num = float("nan")

    time_s = safe_get(data, "time", "s")
    station = safe_get(data, "attributions", 0, "name")  # may not exist; we’ll fallback below
    city_name = safe_get(data, "city", "name")
    geo = safe_get(data, "city", "geo")  # [lat, lon]
    lat = geo[0] if isinstance(geo, list) and len(geo) >= 2 else None
    lon = geo[1] if isinstance(geo, list) and len(geo) >= 2 else None

    # Better station fallback
    if not station:
        station = safe_get(data, "city", "name")

    summary = {
        "City": label,
        "AQI": aqi_num,
        "Category": aqi_category_us(aqi_num) if pd.notna(aqi_num) else "Unknown",
        "ObservedTime": time_s,
        "Station/Area": station,
        "ReportedCity": city_name,
        "Lat": lat,
        "Lon": lon,
    }

    iaqi = safe_get(data, "iaqi") or {}
    def iaqi_val(key: str) -> Optional[float]:
        v = safe_get(iaqi, key, "v")
        try:
            return float(v)
        except Exception:
            return None

    pollutants = {
        "City": label,
        "pm25": iaqi_val("pm25"),
        "pm10": iaqi_val("pm10"),
        "o3": iaqi_val("o3"),
        "no2": iaqi_val("no2"),
        "so2": iaqi_val("so2"),
        "co": iaqi_val("co"),
    }

    return summary, pollutants


def plot_aqi_bar(df: pd.DataFrame, out_png: str = "aqi_bar.png") -> None:
    df2 = df.copy()
    df2 = df2.sort_values("AQI", ascending=False)

    fig = plt.figure(figsize=(10, 5.5))
    ax = plt.gca()

    ax.bar(df2["City"], df2["AQI"])
    ax.set_title("Current Air Quality Index (AQI)\nSão Paulo vs Montreal vs Seattle")
    ax.set_ylabel("AQI (higher = worse)")
    ax.set_ylim(0, max(350, (df2["AQI"].max(skipna=True) if len(df2) else 350) + 25))

    # annotate values + category
    for i, row in enumerate(df2.itertuples(index=False)):
        aqi = row.AQI
        cat = row.Category
        if pd.isna(aqi):
            label = "n/a"
            y = 5
        else:
            label = f"{int(round(aqi))} ({cat})"
            y = aqi + 5
        ax.text(i, y, label, ha="center", va="bottom", fontsize=10)

    # footnote
    ax.text(
        0.01,
        -0.18,
        "Source: WAQI API (aqicn.org). Categories follow common US AQI ranges (AirNow).",
        transform=ax.transAxes,
        fontsize=9,
        va="top",
    )

    plt.tight_layout()
    plt.savefig(out_png, dpi=180)
    plt.close(fig)


def main() -> int:
    token = os.getenv("WAQI_TOKEN")
    if not token:
        print("ERROR: WAQI_TOKEN environment variable not set.\n"
              "Get a free token from WAQI and set it, e.g.:\n"
              "  setx WAQI_TOKEN \"YOUR_TOKEN\"\n")
        return 2

    # These are WAQI “feed” paths. If one ever fails, WAQI usually still works via a nearby station name.
    cities: List[CityQuery] = [
        CityQuery("São Paulo", "sao%20paulo"),
        CityQuery("Montreal", "montreal"),
        CityQuery("Seattle", "seattle"),
    ]

    summaries = []
    pollutants = []

    for c in cities:
        try:
            data = fetch_waqi_city(token, c.waqi_feed_path)
            s, p = parse_city_data(c.label, data)
            summaries.append(s)
            pollutants.append(p)
        except Exception as e:
            summaries.append({
                "City": c.label,
                "AQI": float("nan"),
                "Category": "Unknown",
                "ObservedTime": None,
                "Station/Area": None,
                "ReportedCity": None,
                "Lat": None,
                "Lon": None,
                "Error": str(e),
            })
            pollutants.append({"City": c.label})
        time.sleep(0.25)  # be polite

    df = pd.DataFrame(summaries)
    df_poll = pd.DataFrame(pollutants)

    print("\n=== Current AQI Summary ===")
    cols = ["City", "AQI", "Category", "ObservedTime", "Station/Area"]
    print(df[cols].to_string(index=False))

    print("\n=== Pollutants (IAQI components, if available) ===")
    print(df_poll.fillna("").to_string(index=False))

    out_png = "aqi_bar.png"
    plot_aqi_bar(df, out_png=out_png)
    print(f"\nSaved chart: {out_png}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
