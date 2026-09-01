#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
투자 타이밍 모니터 — 데이터 수집기
GitHub Actions가 매일 이 스크립트를 실행해 data.json을 만든다.
브라우저는 이 JSON만 읽으므로 CORS 프록시가 필요 없다.

야후는 크롤링 방지를 위해 쿠키·크럼 세션을 요구하는 경우가 있어
직접 urllib으로 치면 403이 나기 쉽다. yfinance가 이 과정을
대신 처리해주므로 이를 사용한다.
"""
import json
import time
import sys
from datetime import datetime, timezone

import yfinance as yf

# ── 종목 정의 (Invest.html의 TICKERS와 동일하게 유지할 것) ──
TICKERS = [
    "KRW=X", "CNYKRW=X", "GLD", "BTC-USD",
    "000660.KS", "009150.KS",
    "GOOGL", "NVDA", "AMD", "MPWR",
    "QQQ", "SPY", "SCHD", "AIPO",
]

# ── 보조 데이터 (공포탐욕·매크로 계산용) ──
AUX = [
    "^GSPC", "SPY", "^VIX", "TLT", "HYG", "LQD", "RSP",
    "005930.KS", "000660.KS", "042700.KS",
    "^TNX", "KRW=X", "CNY=X", "DX-Y.NYB",
]

ALL_SYMBOLS = sorted(set(TICKERS + AUX))


def fetch_symbol(symbol, retries=3):
    """yfinance로 3년 일봉을 받아 프런트에서 쓰는 {t,c,h,l} 형태로 변환."""
    last_err = None
    for attempt in range(retries):
        try:
            df = yf.Ticker(symbol).history(
                period="3y", interval="1d", auto_adjust=True, actions=False
            )
            if df is None or df.empty:
                raise ValueError("빈 데이터프레임")

            rows = []
            for idx, row in df.iterrows():
                c, h, l = row["Close"], row["High"], row["Low"]
                if any(v is None or v != v for v in (c, h, l)):  # NaN 체크
                    continue
                t_ms = int(idx.timestamp() * 1000)
                rows.append({
                    "t": t_ms,
                    "c": round(float(c), 6),
                    "h": round(float(h), 6),
                    "l": round(float(l), 6),
                })

            if len(rows) < 30:
                raise ValueError(f"데이터 부족 ({len(rows)}행)")

            return {"ok": True, "rows": rows, "count": len(rows)}
        except Exception as e:
            last_err = str(e)
            time.sleep(2 * (attempt + 1))
    return {"ok": False, "error": last_err, "rows": []}


def fetch_crypto_fg():
    """alternative.me 크립토 공포탐욕지수 원본값."""
    import urllib.request
    url = "https://api.alternative.me/fng/?limit=1"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))
        d = data["data"][0]
        return {"ok": True, "score": int(d["value"]),
                "label": d["value_classification"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    print(f"yfinance {yf.__version__} · 수집 대상 {len(ALL_SYMBOLS)}개 심볼\n")
    series = {}
    failed = []

    for sym in ALL_SYMBOLS:
        print(f"  {sym:<12}", end=" ", flush=True)
        r = fetch_symbol(sym)
        if r["ok"]:
            series[sym] = r["rows"]
            print(f"OK  ({r['count']}행)")
        else:
            failed.append(sym)
            print(f"실패 — {r['error']}")
        time.sleep(0.6)

    print("\n크립토 공포탐욕지수 ...", end=" ", flush=True)
    cfg = fetch_crypto_fg()
    print("OK" if cfg["ok"] else f"실패 — {cfg.get('error')}")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": ALL_SYMBOLS,
        "failed": failed,
        "series": series,
        "crypto_fg": cfg,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    ok_n = len(series)
    print(f"\n완료: {ok_n}/{len(ALL_SYMBOLS)}개 성공")
    if failed:
        print("실패 목록:", ", ".join(failed))

    # 절반 이상 실패하면 워크플로를 실패 처리해 Actions 탭에서 바로 보이게 한다.
    # (data.json은 이미 저장했으므로 사이트는 마지막 성공본으로 계속 작동한다)
    if ok_n < len(ALL_SYMBOLS) * 0.5:
        print("\n경고: 절반 이상 실패. data.json은 저장했지만 커밋 여부는 워크플로에서 판단.")
        sys.exit(1)


if __name__ == "__main__":
    main()
