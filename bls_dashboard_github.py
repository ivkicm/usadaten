import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# BLS Serien
CPI_SERIES = "CUUR0000SA0"          # CPI-U, all items
UNRATE_SERIES = "LNS14000000"       # Arbeitslosenquote in %
UNEMP_LEVEL_SERIES = "LNS13000000"  # Arbeitslose in Tausend
NFP_LEVEL_SERIES = "CES0000000001"  # Total Nonfarm Payrolls Level in Tausend

# GitHub / Repo Konfiguration per Env
OUTPUT_HTML = Path(os.getenv("OUTPUT_HTML", "macro-dashboard/index.html"))
STATE_JSON = Path(os.getenv("STATE_JSON", "macro-dashboard/bls_state.json"))
DATA_JSON = Path(os.getenv("DATA_JSON", "macro-dashboard/bls_data.json"))


def post_bls(series_ids, start_year, end_year):
    payload = json.dumps(
        {
            "seriesid": series_ids,
            "startyear": str(start_year),
            "endyear": str(end_year),
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
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
        if not period.startswith("M") or period == "M13":
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
        points.append(
            {
                "year": year,
                "month": month,
                "label": label,
                "value": value,
            }
        )

    points.sort(key=lambda x: (x["year"], x["month"]))
    return points


def compute_last_3_yoy_inflation(cpi_points):
    lookup = {(p["year"], p["month"]): p["value"] for p in cpi_points}
    results = []

    for p in cpi_points:
        prev_key = (p["year"] - 1, p["month"])
        if prev_key in lookup:
            yoy = ((p["value"] / lookup[prev_key]) - 1) * 100
            results.append(
                {
                    "year": p["year"],
                    "month": p["month"],
                    "label": p["label"],
                    "value": round(yoy, 1),
                }
            )

    return results[-3:]


def compute_nfp_changes(nfp_level_points):
    out = []
    for i in range(1, len(nfp_level_points)):
        prev_item = nfp_level_points[i - 1]
        curr_item = nfp_level_points[i]

        if (curr_item["year"], curr_item["month"]) <= (prev_item["year"], prev_item["month"]):
            continue

        change_k = int(round(curr_item["value"] - prev_item["value"]))
        out.append(
            {
                "year": curr_item["year"],
                "month": curr_item["month"],
                "label": curr_item["label"],
                "jobs_k": change_k,
            }
        )

    return out


def merge_unemployment_data(rate_points, level_points, nfp_change_points):
    level_lookup = {(p["year"], p["month"]): p["value"] for p in level_points}
    nfp_lookup = {(p["year"], p["month"]): p["jobs_k"] for p in nfp_change_points}
    merged = []

    for p in rate_points:
        key = (p["year"], p["month"])
        if key in level_lookup and key in nfp_lookup:
            merged.append(
                {
                    "year": p["year"],
                    "month": p["month"],
                    "label": p["label"],
                    "value": round(p["value"], 1),
                    "unemployed_millions": round(level_lookup[key] / 1000, 1),
                    "jobs_k": nfp_lookup[key],
                }
            )

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


def fmt_jobs_change(change_k):
    if change_k is None:
        return ""
    if change_k > 0:
        return f' <span class="jobs-change up">▲ +{change_k}k</span>'
    if change_k < 0:
        return f' <span class="jobs-change down">▼ {change_k}k</span>'
    return ' <span class="jobs-change neutral">■ 0k</span>'


def build_inflation_cards(items):
    changes = month_to_month_changes(items)
    cards_html = []
    for item, change in zip(items, changes):
        cards_html.append(
            f"""
        <div class="value-card">
            <div class="month">{item["label"]}</div>
            <div class="value-line">
                <span class="value">{item["value"]:.1f}%</span>{fmt_pp_change(change)}
            </div>
        </div>
        """.strip()
        )
    return "\n".join(cards_html)


def build_unemployment_cards(items):
    rate_changes = month_to_month_changes(items)
    jobs_delta_changes = jobs_changes(items)
    cards_html = []

    for item, rate_change, jobs_delta_change in zip(items, rate_changes, jobs_delta_changes):
        cards_html.append(
            f"""
        <div class="value-card">
            <div class="month">{item["label"]}</div>
            <div class="value-line">
                <span class="value">{item["value"]:.1f}%</span>{fmt_pp_change(rate_change)}
            </div>
            <div class="subvalue">{item["unemployed_millions"]:.1f} Mio. Arbeitslose</div>
            <div class="jobs-line">
                <span class="jobs-value">{fmt_jobs(item["jobs_k"])}</span>{fmt_jobs_change(jobs_delta_change)}
            </div>
        </div>
        """.strip()
        )

    return "\n".join(cards_html)


def build_html(inflation_items, unemployment_items, published_at_utc):
    inflation_cards = build_inflation_cards(inflation_items)
    unemployment_cards = build_unemployment_cards(unemployment_items)
    inflation_latest = inflation_items[-1]
    unemployment_latest = unemployment_items[-1]

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>US Inflation & Arbeitslosenquote Dashboard</title>
<style>
    :root {{
        --bg: #0f172a;
        --panel: #111827;
        --panel-2: #1f2937;
        --text: #f8fafc;
        --muted: #94a3b8;
        --accent: #38bdf8;
        --good: #22c55e;
        --bad: #ef4444;
        --neutral: #f59e0b;
        --cyan-soft: #93c5fd;
        --shadow: 0 16px 40px rgba(0,0,0,0.35);
        --radius: 24px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
        margin: 0;
        min-height: 100vh;
        font-family: Arial, Helvetica, sans-serif;
        background:
            radial-gradient(circle at top left, rgba(56,189,248,0.10), transparent 30%),
            radial-gradient(circle at bottom right, rgba(99,102,241,0.10), transparent 30%),
            var(--bg);
        color: var(--text);
        padding: 24px;
    }}
    .wrap {{
        width: 100%;
        max-width: none;
        margin: 0;
        min-height: calc(100vh - 48px);
        display: grid;
        grid-template-rows: 1fr 1fr;
        gap: 24px;
    }}
    .panel {{
        background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
        border: 1px solid rgba(255,255,255,0.08);
        box-shadow: var(--shadow);
        border-radius: var(--radius);
        padding: 24px;
        margin-bottom: 0;
        min-height: 0;
        display: flex;
        flex-direction: column;
    }}
    .panel-head {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        flex-wrap: wrap;
        margin-bottom: 22px;
    }}
    .title {{ font-size: 30px; font-weight: 700; margin: 0; }}
    .latest {{
        background: rgba(56,189,248,0.12);
        border: 1px solid rgba(56,189,248,0.28);
        color: #bae6fd;
        padding: 10px 14px;
        border-radius: 999px;
        font-weight: 700;
    }}
    .cards {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 18px;
        flex: 1;
        min-height: 0;
    }}
    .value-card {{
        background: linear-gradient(180deg, rgba(31,41,55,1), rgba(17,24,39,0.95));
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 20px;
        padding: 26px;
        min-height: 0;
        height: 100%;
        display: flex;
        flex-direction: column;
        justify-content: center;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
    }}
    .month {{ color: #dbeafe; font-size: 30px; font-weight: 800; margin-bottom: 18px; letter-spacing: 0.3px; }}
    .value-line {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }}
    .value {{ font-size: 58px; font-weight: 900; line-height: 1; }}
    .change, .jobs-change {{
        display: inline-flex;
        align-items: center;
        padding: 8px 12px;
        border-radius: 999px;
        font-weight: 800;
        line-height: 1;
        white-space: nowrap;
    }}
    .change {{ font-size: 22px; }}
    .jobs-change {{ font-size: 18px; }}
    .change.up, .jobs-change.down {{ background: rgba(239,68,68,0.16); color: #fecaca; border: 1px solid rgba(239,68,68,0.30); }}
    .change.down, .jobs-change.up {{ background: rgba(34,197,94,0.16); color: #bbf7d0; border: 1px solid rgba(34,197,94,0.30); }}
    .change.neutral, .jobs-change.neutral {{ background: rgba(245,158,11,0.16); color: #fde68a; border: 1px solid rgba(245,158,11,0.30); }}
    .subvalue {{ margin-top: 18px; font-size: 28px; font-weight: 700; color: #cbd5e1; }}
    .jobs-line {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 14px; }}
    .jobs-value {{ font-size: 28px; font-weight: 800; color: var(--cyan-soft); }}
    .wrap > .panel:first-of-type .value {{ font-size: 99px; }}
    .wrap > .panel:first-of-type .change {{ font-size: 37px; }}
    .wrap > .panel:last-of-type .value {{ font-size: 81px; }}
    .wrap > .panel:last-of-type .change {{ font-size: 31px; }}
    .wrap > .panel:last-of-type .subvalue {{ font-size: 39px; }}
    .wrap > .panel:last-of-type .jobs-value {{ font-size: 39px; }}
    .wrap > .panel:last-of-type .jobs-change {{ font-size: 25px; }}
    @media (max-width: 980px) {{
        body {{ padding: 16px; }}
        .wrap {{
            min-height: calc(100vh - 32px);
            grid-template-rows: auto auto;
            gap: 16px;
        }}
        .panel {{ padding: 18px; }}
        .cards {{ grid-template-columns: 1fr; flex: none; }}
        .month {{ font-size: 26px; }}
        .value {{ font-size: 46px; }}
        .change {{ font-size: 18px; }}
        .subvalue {{ font-size: 24px; }}
        .jobs-value {{ font-size: 24px; }}
        .jobs-change {{ font-size: 16px; }}
    }}
</style>
</head>
<body>
    <div class="wrap">
        <section class="panel">
            <div class="panel-head">
                <h2 class="title">Inflation</h2>
                <div class="latest">Aktuell: {inflation_latest["value"]:.1f}% ({inflation_latest["label"]})</div>
            </div>
            <div class="cards">
                {inflation_cards}
            </div>
        </section>

        <section class="panel">
            <div class="panel-head">
                <h2 class="title">Arbeitslosenquote</h2>
                <div class="latest">
                    Aktuell: {unemployment_latest["value"]:.1f}% · {unemployment_latest["unemployed_millions"]:.1f} Mio. · {fmt_jobs(unemployment_latest["jobs_k"])} ({unemployment_latest["label"]})
                </div>
            </div>
            <div class="cards">
                {unemployment_cards}
            </div>
        </section>
    </div>
</body>
</html>"""


def load_previous_state():
    if not STATE_JSON.exists():
        return None
    try:
        return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None


def ensure_parent_dirs():
    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    STATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    DATA_JSON.parent.mkdir(parents=True, exist_ok=True)


def build_payload():
    current_year = datetime.now(timezone.utc).year
    start_year = current_year - 2
    end_year = current_year

    series = post_bls(
        [CPI_SERIES, UNRATE_SERIES, UNEMP_LEVEL_SERIES, NFP_LEVEL_SERIES],
        start_year,
        end_year,
    )

    cpi_raw = None
    unrate_raw = None
    unemp_level_raw = None
    nfp_level_raw = None

    for s in series:
        if s["seriesID"] == CPI_SERIES:
            cpi_raw = s["data"]
        elif s["seriesID"] == UNRATE_SERIES:
            unrate_raw = s["data"]
        elif s["seriesID"] == UNEMP_LEVEL_SERIES:
            unemp_level_raw = s["data"]
        elif s["seriesID"] == NFP_LEVEL_SERIES:
            nfp_level_raw = s["data"]

    if not cpi_raw or not unrate_raw or not unemp_level_raw or not nfp_level_raw:
        raise RuntimeError("Benötigte Serien wurden nicht vollständig von der API zurückgegeben.")

    cpi_points = parse_monthly_points(cpi_raw)
    unrate_points = parse_monthly_points(unrate_raw)
    unemp_level_points = parse_monthly_points(unemp_level_raw)
    nfp_level_points = parse_monthly_points(nfp_level_raw)

    inflation_items = compute_last_3_yoy_inflation(cpi_points)
    nfp_change_points = compute_nfp_changes(nfp_level_points)
    unemployment_items = merge_unemployment_data(unrate_points, unemp_level_points, nfp_change_points)

    if len(inflation_items) < 3:
        raise RuntimeError("Zu wenige Inflationsdaten nach Berechnung.")
    if len(unemployment_items) < 3:
        raise RuntimeError("Zu wenige Arbeitslosendaten nach Berechnung.")

    latest_fingerprint = {
        "inflation_latest": inflation_items[-1],
        "unemployment_latest": unemployment_items[-1],
    }

    return {
        "published_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "inflation_items": inflation_items,
        "unemployment_items": unemployment_items,
        "latest_fingerprint": latest_fingerprint,
    }


def has_material_change(old_state, new_payload):
    if not old_state:
        return True
    old_fp = old_state.get("latest_fingerprint")
    new_fp = new_payload.get("latest_fingerprint")
    return old_fp != new_fp


def main():
    ensure_parent_dirs()
    old_state = load_previous_state()
    payload = build_payload()
    material_change = has_material_change(old_state, payload)

    html = build_html(
        payload["inflation_items"],
        payload["unemployment_items"],
        payload["published_at_utc"],
    )

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"HTML gebaut: {OUTPUT_HTML}")

    if material_change or not STATE_JSON.exists() or not DATA_JSON.exists():
        STATE_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        DATA_JSON.write_text(
            json.dumps(
                {
                    "inflation_items": payload["inflation_items"],
                    "unemployment_items": payload["unemployment_items"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Neue BLS-Daten erkannt: {STATE_JSON}")
        print(f"Data aktualisiert: {DATA_JSON}")
    else:
        print("Keine neuen BLS-Daten. Nur HTML/Template wurde neu gebaut.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
