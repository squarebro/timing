"""Free, anonymous market sources used by the scheduled server-side collector.

Browser CORS permissions are irrelevant here. Keep raw quotes separate from
historical bars, and never infer a missing observation date from fetch time.
"""
import csv
import io
import json
import math
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def public_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content = response.read()
        charset = response.headers.get_content_charset()
        if not charset:
            declaration = re.search(br'<\?xml[^>]*encoding=["\']([^"\']+)', content[:200], re.I)
            charset = declaration.group(1).decode('ascii') if declaration else 'utf-8'
        return content.decode(charset)


def valid_day(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat() == value and value <= datetime.now(KST).date().isoformat()
    except (ValueError, TypeError):
        return False


def parse_naver_history(text):
    result = {}
    root = ET.fromstring(re.sub(r'^\s*<\?xml[^>]*\?>', '', text, count=1))
    for item in root.iter("item"):
        parts = item.get("data", "").split("|")
        if len(parts) < 6:
            continue
        try:
            day = datetime.strptime(parts[0], "%Y%m%d").date().isoformat()
            high, low, close = map(float, (parts[2], parts[3], parts[4]))
            if not valid_day(day) or not all(math.isfinite(v) and v > 0 for v in (high, low, close)) or not low <= close <= high:
                continue
            t = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=KST).timestamp() * 1000)
            result[day] = {"t": t, "c": close, "h": high, "l": low}
        except (ValueError, OverflowError):
            continue
    rows = [result[d] for d in sorted(result)]
    if len(rows) < 30:
        raise ValueError(f"NAVER daily history has fewer than 30 valid observations: {len(rows)}")
    return rows


def naver_history(symbol):
    code = symbol.split(".")[0]
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("A six-digit Korean stock code is required")
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count=850&requestType=0"
    return parse_naver_history(public_text(url))


def parse_naver_quote(text, symbol):
    value = json.loads(text)
    expected = symbol.split(".")[0]
    if str(value.get("itemCode", value.get("stockId", expected))) != expected:
        raise ValueError("NAVER quote identity mismatch")
    price = float(str(value.get("closePrice", "")).replace(",", ""))
    traded_at = value.get("localTradedAt") or ""
    # Date and price must both come from the source, not our fetch clock.
    observed = datetime.fromisoformat(traded_at.replace("Z", "+00:00"))
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=KST)
    if observed > datetime.now(timezone.utc) + timedelta(minutes=5) or not valid_day(observed.astimezone(KST).date().isoformat()) or not math.isfinite(price) or price <= 0:
        raise ValueError("NAVER quote has an invalid price or observation date")
    ratio = value.get("fluctuationsRatio")
    try:
        ratio = float(str(ratio).replace(",", "").replace("%", ""))
        if not math.isfinite(ratio):
            ratio = None
    except (ValueError, TypeError):
        ratio = None
    return {"price": price, "change_percent": ratio, "observed_at": observed.isoformat(),
            "fetched_at": datetime.now(timezone.utc).isoformat(), "currency": "KRW",
            "source": "NAVER Finance", "market_status": value.get("marketStatus", "UNKNOWN")}


def naver_quote(symbol):
    code = symbol.split(".")[0]
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("A six-digit Korean stock code is required")
    return parse_naver_quote(public_text(f"https://m.stock.naver.com/api/stock/{code}/basic"), symbol)


def parse_fred_csv(text, code):
    observations = {}
    for row in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        day = row.get("observation_date", row.get("DATE", ""))
        try:
            value = float(row.get(code, ""))
        except (ValueError, TypeError):
            continue
        if valid_day(day) and math.isfinite(value) and -10 <= value <= 30:
            observations[day] = {"date": day, "value": value}
    rows = [observations[d] for d in sorted(observations)][-850:]
    if len(rows) < 21:
        raise ValueError(f"FRED {code}: fewer than 21 valid observations")
    return rows


def fred_series(code):
    if code not in ("DGS10", "DFII10"):
        raise ValueError("Unsupported Treasury series")
    since = (datetime.now(timezone.utc) - timedelta(days=5 * 366)).date().isoformat()
    return parse_fred_csv(public_text(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}&cosd={since}"), code)
