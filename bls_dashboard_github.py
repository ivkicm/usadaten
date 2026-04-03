import json
import urllib.request
from datetime import datetime
from pathlib import Path

API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# BLS Serien
CPI_SERIES = "CUUR0000SA0"          # CPI-U, all items
UNRATE_SERIES = "LNS14000000"       # Arbeitslosenquote in %
UNEMP_LEVEL_SERIES = "LNS13000000"  # Arbeitslose in Tausend
NFP_LEVEL_SERIES = "CES0000000001"  # Total Nonfarm Payrolls Level in Tausend


def post_bls(series_ids, start_year, end_year):
    payload = json.dumps({
        "seriesid": series_ids,
        "startyear": str(start_year),
        "endyear": str(end_year)
    }).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={"Content-Type": "application/json"}
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    if data.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS API Fehler: {data}")

    return data["Results"]["series"]


def parse_monthly_points(series_data):
    points = []

    for row in series_data:
        period = row.get("period", "")

        if not period.startswith("M"):
            continue
        if period == "M13":
            continue

        value_str = row.get("value")
        if value_str in ("-", "", None):
            continue

        try:
            value = float(value_str)
            year = int(row["year"])
            month = int(period[1:])
        except (ValueError, TypeError, KeyError):
            continue

        label = datetime(year, month, 1).strftime("%b %Y")

        points.append({
            "year": year,
            "month": month,
            "label": label,
            "value": value
        })

    points.sort(key=lambda x: (x["year"], x["month"]))
    return points


def compute_last_3_yoy_inflation(cpi_points):
    lookup = {(p["year"], p["month"]): p["value"] for p in cpi_points}
    results = []

    for p in cpi_points:
        prev_key = (p["year"] - 1, p["month"])
        if prev_key in lookup:
            yoy = ((p["value"] / lookup[prev_key]) - 1) * 100
            results.append({
                "year": p["year"],
                "month": p["month"],
                "label": p["label"],
                "value": round(yoy, 1)
            })

    return results[-3:]


def compute_nfp_changes(nfp_level_points):
    """
    CES0000000001 ist der Beschäftigungsstand in Tausend.
    Für das Dashboard wollen wir die Monatsveränderung:
    aktueller Monat - Vormonat = Jobs-Zuwachs in Tausend.
    """
    out = []

    for i in range(1, len(nfp_level_points)):
        prev_item = nfp_level_points[i - 1]
        curr_item = nfp_level_points[i]

        if (curr_item["year"], curr_item["month"]) <= (prev_item["year"], prev_item["month"]):
            continue

        change_k = int(round(curr_item["value"] - prev_item["value"]))

        out.append({
            "year": curr_item["year"],
            "month": curr_item["month"],
            "label": curr_item["label"],
            "jobs_k": change_k
        })

    return out


def merge_unemployment_data(rate_points, level_points, nfp_change_points):
    level_lookup = {(p["year"], p["month"]): p["value"] for p in level_points}
    nfp_lookup = {(p["year"], p["month"]): p["jobs_k"] for p in nfp_change_points}

    merged = []

    for p in rate_points:
        key = (p["year"], p["month"])
        if key in level_lookup and key in nfp_lookup:
            unemployed_millions = round(level_lookup[key] / 1000, 1)

            merged.append({
                "year": p["year"],
                "month": p["month"],
                "label": p["label"],
                "value": round(p["value"], 1),
                "unemployed_millions": unemployed_millions,
                "jobs_k": nfp_lookup[key]
            })

    return merged[-3:]


def month_to_month_changes(values):
    changes = [None]
    for i in range(1, len(values)):
        changes.append(round(values[i]["value"] - values[i - 1]["value"], 1))
    return changes


def jobs_changes(values):
    changes = [None]
    for i in range(1, len(values)):
        changes.append(values[i]["jobs_k"] - values[i - 1]["jobs_k"])
    return changes


def fmt_pp_change(change):
    if change is None:
        return ""
    if change > 0:
        return f' <span class="change up">▲ +{change:.1f} PP</span>'
    if change < 0:
        return f' <span class="change down">▼ {change:.1f} PP</span>'
    return ' <span class="change neutral">■ 0.0 PP</span>'


def fmt_jobs(value_k):
    sign = "+" if value_k > 0 else ""
    return f"{sign}{value_k}k Jobs"

