# ============================
# app.py
# Weather Arbitrage Terminal
# ============================

import math
import re
import time
from io import StringIO
from datetime import datetime

import pandas as pd
import pytz
import requests
import streamlit as st

# ============================
# CONFIG
# ============================

st.set_page_config(
    page_title="Weather Arbitrage Terminal",
    page_icon="🌦",
    layout="wide"
)

REFRESH_INTERVAL = 10

CSV_URL = "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/latest_1min_temperature.csv"

LATEST_JSON = (
    "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
    "?dataType=latestReadings&lang=en"
)

RHRREAD_JSON = (
    "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
    "?dataType=rhrread&lang=en"
)

POLYMARKET_URL = "https://gamma-api.polymarket.com/markets"

HEADERS = {
    "User-Agent": "Weather-Arbitrage-Terminal/2.0"
}

# ============================
# SESSION
# ============================

if "last_temp" not in st.session_state:
    st.session_state.last_temp = None

if "last_scan" not in st.session_state:
    st.session_state.last_scan = time.time()

if "temperature_history" not in st.session_state:
    st.session_state.temperature_history = []

# ============================
# REQUEST SESSION
# ============================

http = requests.Session()
http.headers.update(HEADERS)

# ============================
# SAFE GET
# ============================

def safe_get(url, retries=3, timeout=15):

    last_error = None

    for _ in range(retries):

        try:

            r = http.get(url, timeout=timeout)

            r.raise_for_status()

            return r

        except Exception as e:

            last_error = e

            time.sleep(1)

    raise last_error

# ============================
# CSV STAGE
# ============================

def fetch_stage_a():

    r = safe_get(CSV_URL)

    df = pd.read_csv(StringIO(r.text))

    station_column = None
    temp_column = None

    for col in df.columns:

        lower = col.lower()

        if "station" in lower:
            station_column = col

        if "temp" in lower:
            temp_column = col

    if station_column is None:
        station_column = df.columns[0]

    if temp_column is None:
        temp_column = df.columns[-1]

    row = None

    mask = (
        df[station_column]
        .astype(str)
        .str.contains("Hong Kong Observatory", case=False)
    )

    if mask.any():
        row = df[mask].iloc[0]
    else:
        row = df.iloc[0]

    temperature = float(row[temp_column])

    return {
        "temperature": temperature,
        "source": "Stage A (1-Minute CSV)"
    }

# ============================
# JSON STAGE
# ============================

def search_temp(obj):

    if isinstance(obj, dict):

        text = str(obj)

        if "Hong Kong Observatory" in text:

            for k, v in obj.items():

                if "temp" in k.lower():

                    try:
                        return float(v)
                    except:
                        pass

        for value in obj.values():

            result = search_temp(value)

            if result is not None:
                return result

    elif isinstance(obj, list):

        for item in obj:

            result = search_temp(item)

            if result is not None:
                return result

    return None

# ============================
# STAGE B
# ============================

def fetch_stage_b():

    r = safe_get(LATEST_JSON)

    data = r.json()

    temp = search_temp(data)

    if temp is None:
        raise Exception("Temperature not found.")

    return {
        "temperature": temp,
        "source": "Stage B (latestReadings)"
    }

# ============================
# STAGE C
# ============================

def fetch_stage_c():

    r = safe_get(RHRREAD_JSON)

    data = r.json()

    if "temperature" not in data:
        raise Exception("temperature section missing")

    rows = data["temperature"]["data"]

    for row in rows:

        place = row.get("place", "")

        if "Hong Kong Observatory" in place:

            return {
                "temperature": float(row["value"]),
                "source": "Stage C (rhrread)"
            }

    if len(rows):

        return {
            "temperature": float(rows[0]["value"]),
            "source": "Stage C (Fallback)"
        }

    raise Exception("No station available.")

# ============================
# MASTER FETCH
# ============================

def get_live_temperature():

    errors = []

    stages = [
        fetch_stage_a,
        fetch_stage_b,
        fetch_stage_c
    ]

    for stage in stages:

        try:

            return stage()

        except Exception as e:

            errors.append(str(e))

    raise Exception("\n".join(errors))

# ============================
# ROUNDING
# ============================

def settlement_bracket(temp):

    return int(math.floor(temp + 0.5))

# ============================
# DELTA ENGINE
# ============================

def delta(temp):

    previous = st.session_state.last_temp

    if previous is None:

        st.session_state.last_temp = temp

        return ""

    diff = temp - previous

    st.session_state.last_temp = temp

    if abs(diff) < 0.1:
        return ""

    if diff > 0:
        return f"📈 +{diff:.1f}°C"

    return f"📉 {diff:.1f}°C"

# ============================
# CLOCKS
# ============================

def ist_now():

    return datetime.now(
        pytz.timezone("Asia/Kolkata")
    )

def hkt_now():

    return datetime.now(
        pytz.timezone("Asia/Hong_Kong")
    )

# ============================
# HISTORY
# ============================

def update_history(temp):

    history = st.session_state.temperature_history

    history.append({
        "time": datetime.now(),
        "temp": temp
    })

    if len(history) > 100:
        history.pop(0)

    st.session_state.temperature_history = history

# ===========================================
# POLYMARKET ENGINE
# ===========================================

def _safe_float(value):

    try:
        if value is None:
            return None

        if value == "":
            return None

        return float(value)

    except Exception:
        return None


def parse_yes_price(market):

    candidates = []

    if isinstance(market.get("outcomePrices"), list):

        prices = market.get("outcomePrices")

        if len(prices):

            value = _safe_float(prices[0])

            if value is not None:

                if value <= 1:
                    value *= 100

                candidates.append(value)

    if "lastTradePrice" in market:

        value = _safe_float(market["lastTradePrice"])

        if value is not None:

            if value <= 1:
                value *= 100

            candidates.append(value)

    if "bestBid" in market:

        value = _safe_float(market["bestBid"])

        if value is not None:

            if value <= 1:
                value *= 100

            candidates.append(value)

    if len(candidates):

        return round(max(candidates), 2)

    return None


def parse_bracket(question):

    matches = re.findall(r"\d+", question)

    if len(matches):

        return int(matches[0])

    return None


# ===========================================
# DOWNLOAD MARKETS
# ===========================================

def fetch_polymarket():

    response = safe_get(POLYMARKET_URL)

    data = response.json()

    if not isinstance(data, list):

        return pd.DataFrame()

    rows = []

    for market in data:

        question = (
            market.get("question")
            or market.get("title")
            or ""
        )

        if "hong kong" not in question.lower():
            continue

        if "temp" not in question.lower():
            continue

        price = parse_yes_price(market)

        bracket = parse_bracket(question)

        rows.append({

            "Question": question,

            "Bracket": bracket,

            "YES": price,

            "Active": market.get("active", True)

        })

    if not rows:

        return pd.DataFrame()

    df = pd.DataFrame(rows)

    df = df[df["YES"].notna()]

    df = df.sort_values(

        by="YES",

        ascending=False

    )

    return df.reset_index(drop=True)


# ===========================================
# TOP 3 MARKETS
# ===========================================

def top_three(df):

    if df.empty:

        return df

    return df.head(3)


# ===========================================
# BUY SIGNAL
# ===========================================

def quant_signal(df, target):

    if df.empty:

        return False, None

    target_rows = df[

        df["Bracket"] == target

    ]

    if target_rows.empty:

        return False, None

    row = target_rows.iloc[0]

    yes = row["YES"]

    if yes < 50:

        return True, row

    return False, row


# ===========================================
# MARKET STATUS
# ===========================================

def market_status(df):

    if df.empty:

        return "NO MARKETS"

    avg = df["YES"].mean()

    if avg < 35:

        return "LOW CONFIDENCE"

    elif avg < 60:

        return "BALANCED"

    else:

        return "HIGH CONFIDENCE"


# ===========================================
# MARKET SUMMARY
# ===========================================

def market_summary(df):

    if df.empty:

        return {

            "count": 0,

            "highest": None,

            "lowest": None

        }

    return {

        "count": len(df),

        "highest": df["YES"].max(),

        "lowest": df["YES"].min()

    }

# ==========================================================
# CUSTOM CSS
# ==========================================================

st.markdown("""
<style>

.stApp{
    background:#0d1117;
    color:white;
}

div[data-testid="metric-container"]{
    background:#161b22;
    border-radius:12px;
    padding:15px;
    border:1px solid #30363d;
}

.alert-buy{
    background:#8B0000;
    padding:18px;
    border-radius:12px;
    color:white;
    font-size:28px;
    font-weight:bold;
    text-align:center;
    animation:blink 1s infinite;
}

.alert-green{
    background:#006400;
    padding:15px;
    border-radius:10px;
    color:white;
    font-size:20px;
    text-align:center;
}

.delta-box{
    background:#1f2937;
    padding:12px;
    border-radius:10px;
    color:white;
    font-size:18px;
}

@keyframes blink{
0%{opacity:1;}
50%{opacity:.45;}
100%{opacity:1;}
}

</style>
""", unsafe_allow_html=True)

# ==========================================================
# REFRESH LOGIC
# ==========================================================

if time.time() - st.session_state.last_scan >= REFRESH_INTERVAL:
    st.session_state.last_scan = time.time()

remaining = max(
    0,
    REFRESH_INTERVAL - int(time.time() - st.session_state.last_scan)
)

# ==========================================================
# HEADER
# ==========================================================

st.title("🌦 Weather Arbitrage Terminal")

left, middle, right = st.columns(3)

with left:
    st.metric(
        "🇮🇳 IST",
        ist_now().strftime("%H:%M:%S")
    )

with middle:
    st.metric(
        "🇭🇰 HKT",
        hkt_now().strftime("%H:%M:%S")
    )

with right:
    st.metric(
        "⏳ Next Scan",
        f"{remaining}s"
    )

st.divider()

# ==========================================================
# LIVE HKO DATA
# ==========================================================

try:

    live = get_live_temperature()

    temperature = live["temperature"]

    source = live["source"]

except Exception as e:

    st.error("Unable to fetch HKO data")

    st.exception(e)

    st.stop()

update_history(temperature)

direction = delta(temperature)

target = settlement_bracket(temperature)

# ==========================================================
# METRIC CARDS
# ==========================================================

a, b, c = st.columns(3)

with a:

    st.metric(

        "🌡 Live Temperature",

        f"{temperature:.1f}°C",

        direction

    )

with b:

    st.metric(

        "📍 Settlement",

        f"{target}°C"

    )

with c:

    st.metric(

        "📡 Data Source",

        source

    )

st.divider()

# ==========================================================
# TEMPERATURE HISTORY
# ==========================================================

st.subheader("Temperature History")

history = pd.DataFrame(

    st.session_state.temperature_history

)

if not history.empty:

    history = history.rename(

        columns={

            "time": "Time",

            "temp": "Temperature"

        }

    )

    history = history.set_index("Time")

    st.line_chart(history)

else:

    st.info("Collecting temperature history...")

# ==========================================================
# POLYMARKET
# ==========================================================

st.divider()

st.subheader("Polymarket Markets")

try:

    markets = fetch_polymarket()

except Exception as e:

    st.warning("Unable to download Polymarket markets")

    st.exception(e)

    markets = pd.DataFrame()

if markets.empty:

    st.info("No Hong Kong weather contracts found.")

else:

    top = top_three(markets)

    st.dataframe(

        top,

        use_container_width=True,

        hide_index=True

    )

# ==========================================================
# MARKET SUMMARY
# ==========================================================

summary = market_summary(markets)

x, y, z = st.columns(3)

with x:

    st.metric(

        "Contracts",

        summary["count"]

    )

with y:

    highest = summary["highest"]

    if highest is None:
        highest = "-"

    st.metric(

        "Highest YES",

        highest

    )

with z:

    lowest = summary["lowest"]

    if lowest is None:
        lowest = "-"

    st.metric(

        "Lowest YES",

        lowest

    )

# ==========================================================
# STATUS
# ==========================================================

status = market_status(markets)

if status == "HIGH CONFIDENCE":

    st.success("🟢 High Confidence Market")

elif status == "BALANCED":

    st.info("🟦 Balanced Market")

elif status == "LOW CONFIDENCE":

    st.warning("🟡 Low Confidence Market")

else:

    st.error("🔴 No Active Market")

# ==========================================================
# TRADING SIGNAL
# ==========================================================

buy, row = quant_signal(markets, target)

st.divider()

st.subheader("Trading Signal")

if buy:

    st.markdown(f"""
<div class="alert-buy">

🚨 QUANT BUY SIGNAL 🚨

Current Settlement Target

<b>{target}°C</b>

YES Price :
<b>{row['YES']:.2f}¢</b>

Possible Underpricing Detected

</div>
""", unsafe_allow_html=True)

else:

    st.markdown("""
<div class="alert-green">

✅ No obvious mispricing detected.

</div>
""", unsafe_allow_html=True)

# ==========================================================
# DELTA ALERT
# ==========================================================

if direction != "":

    st.markdown(f"""
<div class="delta-box">

Temperature Movement

<h2>{direction}</h2>

</div>
""", unsafe_allow_html=True)

# ==========================================================
# CURRENT SNAPSHOT
# ==========================================================

st.divider()

st.subheader("Current Snapshot")

snapshot = pd.DataFrame([{

    "Temperature": f"{temperature:.1f}°C",

    "Settlement": target,

    "Source": source,

    "Status": status,

    "Updated(HKT)": hkt_now().strftime("%H:%M:%S"),

    "Updated(IST)": ist_now().strftime("%H:%M:%S")

}])

st.dataframe(

    snapshot,

    use_container_width=True,

    hide_index=True

)

# ==========================================================
# FOOTER
# ==========================================================

st.caption(

    "Weather Arbitrage Terminal | "

    "Live HKO | "

    "Polymarket | "

    "Auto Refresh"

)

# ==========================================================
# AUTO REFRESH
# ==========================================================

time.sleep(1)

if time.time() - st.session_state.last_scan >= REFRESH_INTERVAL:

    st.session_state.last_scan = time.time()

    st.rerun()

else:

    st.rerun()
