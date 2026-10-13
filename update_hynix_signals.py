#!/usr/bin/env python3
"""Public, no-login SK hynix investor flows (NAVER Finance) + FRED DFII10.

Writes dashboard's data.json / hynix_signals. NAVER's foreign daily net BUY is
shares, NOT transaction value: foreign_net_100m is an *estimated* KRW value
(shares * daily close / 100 million). Market cap is estimated contemporaneously
from foreign-held shares / foreign ownership rate, times the unadjusted close.
No KRX credentials, paid API or account needed. Public HTML may change.
"""
import io
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(os.environ.get('DASHBOARD_DATA', 'data.json'))
TZ = timezone(timedelta(hours=9))
NOW = datetime.now(TZ)
SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36',
    'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.7',
})
NAVER_URL = 'https://finance.naver.com/item/frgn.naver'


def fetch(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            response = SESSION.get(url, params=params, timeout=22)
            response.raise_for_status()
            if not response.content:
                raise ValueError('empty HTTP response')
            return response
        except (requests.RequestException, ValueError):
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def number(text):
    text = re.sub(r'[,\s%+]', '', str(text))
    if not re.fullmatch(r'-?\d+(?:\.\d+)?', text):
        raise ValueError('invalid number: ' + repr(text))
    return float(text)


def parse_naver_page(markup):
    """Return daily rows; reject unexpected schemas instead of inventing flows."""
    soup = BeautifulSoup(markup, 'lxml')
    tables = soup.select('table.type2')
    if not tables:
        raise ValueError('NAVER investor-flow table.type2 not found; page may have changed or access blocked')
    result = []
    for table in tables:
        headers = ' '.join(table.stripped_strings)[:700]
        if not ('기관' in headers and '외국인' in headers and '순매매' in headers):
            continue
        for tr in table.select('tr'):
            cells = [c.get_text(' ', strip=True) for c in tr.find_all('td', recursive=False)]
            if len(cells) < 9:
                continue
            if not re.fullmatch(r'\d{4}\.\d{2}\.\d{2}', cells[0]):
                continue
            try:
                day = cells[0].replace('.', '-')
                # NAVER table: date, close, chg, change%, volume,
                # institution net shares, foreign net shares, foreign holdings, foreign ownership%.
                close = number(cells[1])
                net_shares = number(cells[6])
                held_shares = number(cells[7])
                foreign_pct = number(cells[8])
                if close <= 0 or held_shares <= 0 or not 0.1 <= foreign_pct <= 100:
                    continue
                shares_out = held_shares * 100 / foreign_pct
                # SK hynix has hundreds of millions of issued shares.
                if not 100_000_000 <= shares_out <= 2_000_000_000:
                    continue
                est_cap = close * shares_out / 100_000_000
                est_flow = close * net_shares / 100_000_000
                result.append({
                    'date': day,
                    'foreign_net_100m': round(est_flow, 5),
                    'market_cap_100m': round(est_cap, 5),
                })
            except (ValueError, IndexError, ZeroDivisionError):
                continue
        if result:
            break
    if not result:
        raise ValueError('NAVER investor-flow rows not found or columns changed')
    return result


def foreign_flow(old_rows=None):
    # First run: deeper history. Subsequent runs stop as soon as dates overlap.
    known = {row['date'] for row in (old_rows or []) if 'date' in row}
    max_pages = int(os.environ.get('NAVER_MAX_PAGES', '110' if len(known) < 160 else '12'))
    all_rows = {}
    SESSION.get('https://finance.naver.com/', timeout=12)  # public session cookie only; no login
    for page in range(1, min(max_pages, 130) + 1):
        response = fetch(NAVER_URL, params={'code': '000660', 'page': page})
        # NAVER sometimes serves EUC-KR instead of UTF-8.
        response.encoding = response.apparent_encoding if response.apparent_encoding in ('UTF-8', 'EUC-KR', 'CP949') else 'euc-kr'
        rows = parse_naver_page(response.text)
        for row in rows:
            all_rows[row['date']] = row
        if known and any(row['date'] in known for row in rows) and len(all_rows) >= 20:
            break
        if page > 1 and page % 10 == 0:
            print(f'NAVER pages={page}, unique trading dates={len(all_rows)}', flush=True)
        time.sleep(0.15)
    items = [all_rows[d] for d in sorted(all_rows)]
    if len(items) < 20:
        raise ValueError(f'NAVER public flow returned only {len(items)} observations (need >=20)')
    print('foreign_flow NAVER', len(items), 'latest', items[-1]['date'], flush=True)
    return items


def real_yield():
    url = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10'
    response = fetch(url)
    frame = pd.read_csv(io.StringIO(response.text))
    if 'DFII10' not in frame.columns:
        raise ValueError('FRED CSV DFII10 column missing')
    date_col = 'observation_date' if 'observation_date' in frame.columns else frame.columns[0]
    result = []
    for _, row in frame.iterrows():
        val = pd.to_numeric(row['DFII10'], errors='coerce')
        if pd.notna(val):
            result.append({'date': str(row[date_col])[:10], 'value': float(val)})
    return result[-850:]


def main():
    if not ROOT.exists():
        raise SystemExit(f'Missing {ROOT}: run existing dashboard data collection first')
    data = json.loads(ROOT.read_text(encoding='utf-8'))
    old = data.get('hynix_signals') or {}
    merged = {
        'updated_at': NOW.isoformat(),
        'flow': old.get('flow', []),
        'real_yield': old.get('real_yield', []),
        'source': {
            'flow': 'NAVER Finance frgn.naver; estimated KRW amount = foreign net shares × daily close; estimated cap = foreign holding shares / holding ratio × close; NO LOGIN',
            'real_yield': 'FRED DFII10; US 10-year TIPS real yield',
        },
        'errors': [],
    }
    successful = 0
    for key, fn in [('flow', lambda: foreign_flow(old.get('flow', []))), ('real_yield', real_yield)]:
        try:
            rows = fn()
            if len(rows) < (20 if key == 'flow' else 61):
                raise ValueError(f'{key}: insufficient observations {len(rows)}')
            by_day = {x['date']: x for x in old.get(key, []) if 'date' in x}
            by_day.update({x['date']: x for x in rows})
            merged[key] = [by_day[d] for d in sorted(by_day)][-850:]
            successful += 1
            print(f'OK {key}: {len(merged[key])} dates; last {merged[key][-1]["date"]}', flush=True)
        except Exception as exc:
            error = f'{key}: {type(exc).__name__}: {exc}'
            merged['errors'].append(error)
            print('WARN ' + error, flush=True)

    data['hynix_signals'] = merged
    ROOT.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    if successful == 0:
        raise SystemExit('Both NAVER and FRED failed. Preserved old values; see errors in data.json')
    if merged['errors']:
        print('PARTIAL SUCCESS: successful source committed; missing input produces no fabricated score.', flush=True)


if __name__ == '__main__':
    main()
