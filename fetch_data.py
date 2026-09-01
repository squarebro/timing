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

# ── 계절성 분석 전용 (S&P500·나스닥 10년치) ──
# 이 두 개는 위 ALL_SYMBOLS와 별도로 훨씬 긴 기간을 받는다.
# 목적이 "월별 상대 순위"뿐이라 종가만 있으면 되고, 3년치 일봉과는
# 별도의 국(dict)에 저장한다.
SEASONALITY_SYMS = {"sp500": "^GSPC", "nasdaq": "^IXIC"}
SEASONALITY_PERIOD = "10y"


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


def compute_seasonality(rows):
    """
    10년 일봉에서 "각 연도의 각 월이 그해 12개월 중 수익률 몇 등이었는가"를 계산.
    반환값의 month_rank[i] (i=0~11, 1월~12월)는 1~12 사이 실수로,
    1에 가까울수록 그 달이 그해 최고 성과월이었던 경우가 많았다는 뜻이다.

    표본이 10개 연도뿐이라 통계적으로 약하다는 점을 이 함수를 쓰는 쪽에서
    반드시 함께 표시해야 한다. 이건 계절성의 "경향"이지 예측이 아니다.
    """
    from collections import defaultdict

    by_year_month = defaultdict(list)  # {(year,month): [rows...]}
    for r in rows:
        d = datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc)
        by_year_month[(d.year, d.month)].append(r)

    monthly_return = {}  # (year,month) -> 그 달의 수익률(%)
    for (y, m), rs in by_year_month.items():
        rs_sorted = sorted(rs, key=lambda x: x["t"])
        if len(rs_sorted) < 5:  # 표본이 너무 적은 달(수집 경계)은 제외
            continue
        start, end = rs_sorted[0]["c"], rs_sorted[-1]["c"]
        if start:
            monthly_return[(y, m)] = (end / start - 1) * 100

    years = sorted(set(y for y, m in monthly_return))
    rank_sum = [0.0] * 12
    rank_n = [0] * 12
    return_sum = [0.0] * 12
    return_n = [0] * 12
    win_n = [0] * 12   # 그 달이 플러스로 마감한 횟수

    for y in years:
        year_rows = [(m, monthly_return[(y, m)]) for m in range(1, 13) if (y, m) in monthly_return]
        if len(year_rows) < 10:  # 그해 데이터가 너무 부족하면 순위 계산에서 제외
            continue
        # 수익률 내림차순 정렬 → 1등이 그해 최고 성과월
        ranked = sorted(year_rows, key=lambda x: -x[1])
        rank_of = {m: i + 1 for i, (m, _) in enumerate(ranked)}
        for m, ret in year_rows:
            rank_sum[m - 1] += rank_of[m]
            rank_n[m - 1] += 1
            return_sum[m - 1] += ret
            return_n[m - 1] += 1
            if ret > 0:
                win_n[m - 1] += 1

    months = []
    for i in range(12):
        n = rank_n[i]
        months.append({
            "month": i + 1,
            "avg_rank": round(rank_sum[i] / n, 2) if n else None,
            "avg_return": round(return_sum[i] / n, 2) if return_n[i] else None,
            "win_rate": round(win_n[i] / return_n[i] * 100, 1) if return_n[i] else None,
            "n_years": n,
        })

    return {"years_used": len(years), "months": months}


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

    print(f"\n계절성 분석용 10년 데이터 ({SEASONALITY_PERIOD})")
    seasonality = {}
    for key, sym in SEASONALITY_SYMS.items():
        print(f"  {sym:<10}", end=" ", flush=True)
        r = fetch_symbol(sym, period=SEASONALITY_PERIOD)
        if r["ok"]:
            seasonality[key] = compute_seasonality(r["rows"])
            print(f"OK  ({r['count']}행 → {seasonality[key]['years_used']}개 연도 사용)")
        else:
            print(f"실패 — {r['error']}")
        time.sleep(0.6)

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
