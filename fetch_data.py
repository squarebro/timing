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
# 주의: CNYKRW=X는 야후 파이낸스에 존재하지 않는 심볼이다.
# Invest.html은 이를 KRW=X ÷ CNY=X 로 직접 합성해 계산하므로
# 여기서 수집을 시도할 필요가 없다 (시도해도 매번 실패만 기록됨).
TICKERS = [
    "KRW=X", "GLD", "BTC-USD",
    "000660.KS", "009150.KS",
    "GOOGL", "NVDA", "AMD", "MPWR",
    "QQQ", "SPY", "SCHD", "AIPO",
]

# ── 보조 데이터 (공포탐욕·매크로 계산용) ──
AUX = [
    "^GSPC", "SPY", "^VIX", "TLT", "HYG", "LQD", "RSP",
    "005930.KS", "000660.KS", "042700.KS",
    "^TNX", "KRW=X", "CNY=X", "DX-Y.NYB",
    "^KS11", "069500.KS", "148020.KS",   # 코스피 (원본 → ETF 폴백 2종)
    "^KQ11", "229200.KS", "091180.KS",    # 코스닥 (원본 → KODEX코스닥150 → TIGER코스닥150 폴백)
]

ALL_SYMBOLS = sorted(set(TICKERS + AUX))


def fetch_symbol(symbol, period="3y", retries=3):
    """yfinance로 일봉을 받아 프런트에서 쓰는 {t,c,h,l} 형태로 변환."""
    last_err = None
    for attempt in range(retries):
        try:
            df = yf.Ticker(symbol).history(
                period=period, interval="1d", auto_adjust=True, actions=False
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


# ── 계절성: 10년 자체 계산 대신 공신력 있는 장기 통계를 고정값으로 사용 ──
# 이유: 10년 표본으로 매번 자체 계산하면 (1) 표본이 짧아 통계적으로 약하고
# (2) 계산 방식(상대 순위)이 통상 인용되는 "월평균 수익률 %" 방식과 달라
# Carson Investment Research·YCharts 등 공개 자료와 다른 숫자가 나온다.
# 아래 수치는 1950~2025년 S&P500 실제 데이터(YCharts 집계, Carson Investment
# Research 및 여러 리서치에서 반복 인용되는 표준 수치)를 그대로 옮긴 것이다.
# 자동 계산이 아니므로 매번 똑같이 나오며, 이는 의도된 동작이다 — 76년
# 평균은 어차피 하루이틀 사이에 바뀌지 않는다.
SP500_SEASONALITY_1950 = {
    "years": "1950–2025",
    "years_n": 76,
    "source": "YCharts / Carson Investment Research",
    "months": [
        {"month": 1,  "avg_return": 1.0,  "win_rate": 59.0, "rank": 6},
        {"month": 2,  "avg_return": -0.1, "win_rate": 54.0, "rank": 10},
        {"month": 3,  "avg_return": 1.1,  "win_rate": 64.0, "rank": 5},
        {"month": 4,  "avg_return": 1.5,  "win_rate": 71.0, "rank": 1},
        {"month": 5,  "avg_return": 0.3,  "win_rate": 59.0, "rank": 8},
        {"month": 6,  "avg_return": 0.1,  "win_rate": 54.0, "rank": 9},
        {"month": 7,  "avg_return": 1.2,  "win_rate": 59.0, "rank": 4},
        {"month": 8,  "avg_return": -0.1, "win_rate": 55.0, "rank": 11},
        {"month": 9,  "avg_return": -0.7, "win_rate": 44.0, "rank": 12},
        {"month": 10, "avg_return": 0.9,  "win_rate": 61.0, "rank": 7},
        {"month": 11, "avg_return": 1.5,  "win_rate": 68.0, "rank": 2},
        {"month": 12, "avg_return": 1.4,  "win_rate": 74.0, "rank": 3},
    ],
}

# 나스닥은 QQQ(나스닥100 추종 ETF) 최근 24년 실측 월별 수익률을 사용한다.
# QQQ는 1999년 상장이라 76년 표본은 원천적으로 불가능하며, 24년이 실무에서
# 구할 수 있는 가장 긴 검증된 정량 표본이다(Tradewell 세션낼리티 집계,
# Barchart·Trade That Swing 등 여러 출처의 정성적 패턴과 방향이 일치함:
# 9월 최저·10월 최고).  S&P500과 동일하게 "avg_return %"를 그대로 쓰므로
# 화면에서도 같은 방식(0선 기준 막대)으로 표시된다.
NASDAQ_SEASONALITY_QQQ = {
    "years": "2001–2025 (QQQ 최근 24개년)",
    "years_n": 24,
    "source": "Tradewell QQQ Seasonality (Invesco QQQ Trust 실측 월별 수익률)",
    "months": [
        {"month": 1,  "avg_return": 0.6,  "win_rate": 60.9, "rank": 8},
        {"month": 2,  "avg_return": -0.6, "win_rate": 47.8, "rank": 11},
        {"month": 3,  "avg_return": 1.3,  "win_rate": 66.7, "rank": 5},
        {"month": 4,  "avg_return": 1.9,  "win_rate": 62.5, "rank": 4},
        {"month": 5,  "avg_return": 0.5,  "win_rate": 54.2, "rank": 9},
        {"month": 6,  "avg_return": 0.4,  "win_rate": 58.3, "rank": 10},
        {"month": 7,  "avg_return": 2.1,  "win_rate": 70.8, "rank": 3},
        {"month": 8,  "avg_return": 1.2,  "win_rate": 58.3, "rank": 6},
        {"month": 9,  "avg_return": -2.2, "win_rate": 45.8, "rank": 12},
        {"month": 10, "avg_return": 3.4,  "win_rate": 66.7, "rank": 1},
        {"month": 11, "avg_return": 2.3,  "win_rate": 79.2, "rank": 2},
        {"month": 12, "avg_return": 0.9,  "win_rate": 56.5, "rank": 7},
    ],
}


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

    print("\n계절성 데이터 (고정값 — 1950년 이후 76년 통계, 매번 동일)")
    print(f"  S&P500  OK  (YCharts/Carson 집계, {SP500_SEASONALITY_1950['years']}, "
          f"{SP500_SEASONALITY_1950['years_n']}개년)")
    print(f"  나스닥   OK  (QQQ 실측 {NASDAQ_SEASONALITY_QQQ['years_n']}개년 — {NASDAQ_SEASONALITY_QQQ['years']})")
    seasonality = {
        "sp500": SP500_SEASONALITY_1950,
        "nasdaq": NASDAQ_SEASONALITY_QQQ,
    }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": ALL_SYMBOLS,
        "failed": failed,
        "series": series,
        "crypto_fg": cfg,
        "seasonality": seasonality,
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
