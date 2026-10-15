#!/usr/bin/env python3
"""Public, no-login SK hynix investor flows (NAVER Finance) + FRED DFII10.

Writes dashboard's data.json / hynix_signals. NAVER's foreign daily net BUY is
shares, NOT transaction value: foreign_net_100m is an *estimated* KRW value
(shares * daily close / 100 million). Market cap is estimated contemporaneously
from foreign-held shares / foreign ownership rate, times the unadjusted close.
No KRX credentials, paid API or account needed. Public HTML may change.
"""
import io
import random
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
VERSION = "2026-10-13-r4-schema-tests"
NAVER_URL = 'https://finance.naver.com/item/frgn.naver'


def fetch(url, params=None, retries=3, timeout=(10, 28)):
    """Bounded retries, no credentials; avoid endless Action jobs."""
    for attempt in range(retries):
        try:
            response = SESSION.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            if not response.content:
                raise ValueError('empty HTTP response')
            return response
        except (requests.RequestException, ValueError) as exc:
            if attempt == retries - 1:
                raise
            pause = min(9, 2 ** attempt * 2) + random.uniform(0, .3)
            print(f'RETRY {attempt+1}/{retries} {url.split("?")[0]}: {type(exc).__name__}; wait {pause:.1f}s', flush=True)
            time.sleep(pause)


def decode_page(response):
    content = response.content
    # Korean finance pages can use EUC-KR/CP949 or UTF-8; favor explicit charset.
    import re as _re
    hint = response.headers.get('Content-Type', '') + ' ' + content[:1000].decode('ascii', errors='ignore')
    if _re.search(r'(?:charset\s*=\s*["\']?)utf-8', hint, flags=_re.I):
        return content.decode('utf-8', errors='replace')
    if _re.search(r'(?:charset\s*=\s*["\']?)(?:euc-kr|cp949)', hint, flags=_re.I):
        return content.decode('cp949', errors='replace')
    # Plausibility check, not unconditional apparent_encoding guesses.
    options = [content.decode(enc, errors='replace') for enc in ('cp949', 'utf-8')]
    return max(options, key=lambda txt: sum(txt.count(w) for w in ('외국인', '순매매', '기관', '보유율')))


def number(text):
    text = re.sub(r'[,\s%+]', '', str(text))
    if not re.fullmatch(r'-?\d+(?:\.\d+)?', text):
        raise ValueError('invalid number: ' + repr(text))
    return float(text)


def parse_naver_page(markup):
    """Parse publicly displayed daily investor table, with schema checks.

    Never silently interpret foreign *ownership change* as foreign *net buying*.
    If the source changes its columns, fail closed and keep previous values.
    """
    soup = BeautifulSoup(markup, 'lxml')
    candidates = soup.find_all('table')
    if not candidates:
        title = (soup.title.get_text(' ', strip=True) if soup.title else '')[:90]
        raise ValueError(f'NAVER no tables (blocked/changed); title={title!r}; html_bytes={len(markup)}')
    result = []
    for table in candidates:
        trs = table.find_all('tr')
        heading = ' '.join(' '.join(cell.get_text(' ', strip=True) for cell in tr.find_all(['th'])) for tr in trs[:7])
        heading = re.sub(r'\s+', '', heading)
        # Disambiguation: NAVER foreign activity view, not ordinary price table.
        if '외국인' not in heading or ('기관' not in heading and '순매매' not in heading):
            continue
        for tr in trs:
            cells = [c.get_text(' ', strip=True) for c in tr.find_all('td', recursive=False)]
            if len(cells) < 9 or not re.fullmatch(r'\d{4}\.\d{2}\.\d{2}', cells[0]):
                continue
            try:
                # Original NAVER 9-column schema (date, close, change, %, volume,
                # institution net, foreign net, foreign-held shares, foreign %).
                day = cells[0].replace('.', '-')
                close, net_shares, held, ownership = map(number, (cells[1], cells[6], cells[7], cells[8]))
                if close <= 0 or held <= 0 or not .1 <= ownership <= 100 or not (-1e8 < net_shares < 1e8):
                    continue
                issued = held * 100 / ownership
                if not 1e8 <= issued <= 2e9:
                    continue
                result.append({'date': day,
                    'foreign_net_100m': round(close * net_shares / 1e8, 5),
                    'market_cap_100m': round(close * issued / 1e8, 5)})
            except (ValueError, ZeroDivisionError, IndexError):
                continue
        if result:
            break
    if not result:
        title = (soup.title.get_text(' ', strip=True) if soup.title else '')[:80]
        raise ValueError(f'NAVER foreign trades absent or unexpected schema; title={title!r}; tables={len(candidates)}')
    return result


def foreign_flow(old_rows=None):
    known = {x['date'] for x in (old_rows or []) if 'date' in x}
    max_pages = min(120, max(1, int(os.environ.get('NAVER_MAX_PAGES', '40' if len(known)<160 else '5'))))
    variants = [
        ('https://finance.naver.com/item/frgn.naver', {'code':'000660', 'trader_day':'1'}),
        ('https://finance.naver.com/item/frgn.naver', {'code':'000660'}),
    ]
    all_rows, diagnostics = {}, []
    for url, base_params in variants:
        try:
            for page in range(1, max_pages+1):
                response = fetch(url, params={**base_params, 'page':page}, retries=2)
                content = decode_page(response)
                page_rows = parse_naver_page(content)
                if page == 1:
                    print(f'NAVER table variant={"trader_day" if "trader_day" in base_params else "default"} HTTP={response.status_code} observations={len(page_rows)}', flush=True)
                for x in page_rows:
                    all_rows[x['date']] = x
                if known and any(x['date'] in known for x in page_rows) and len(all_rows)>=20:
                    break
                # Avoid hitting a public service too hard.
                if page < max_pages: time.sleep(.25)
            if len(all_rows)>=20:
                break
        except Exception as exc:
            diagnostics.append(f'{type(exc).__name__}: {exc}')
            print('WARN NAVER variant:', diagnostics[-1], flush=True)
            if all_rows and len(all_rows)>=20: break
    if len(all_rows)<20:
        raise ValueError(f'NAVER publicly accessible foreign-flow unavailable: {len(all_rows)} observations; ' + ' / '.join(diagnostics))
    items=[all_rows[d] for d in sorted(all_rows)]
    print('OK NAVER',len(items),'latest',items[-1]['date'],flush=True)
    return items


def real_yield():
    """Public FRED CSV with a second no-key mirror host as fallback."""
    urls = [
        'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10',
        'https://research.stlouisfed.org/fred2/graph/fredgraph.csv?id=DFII10',
    ]
    errors=[]
    for url in urls:
        try:
            response=fetch(url,retries=2,timeout=(8,16))
            frame=pd.read_csv(io.StringIO(response.text))
            if 'DFII10' not in frame.columns: raise ValueError('FRED CSV column DFII10 missing')
            dc='observation_date' if 'observation_date' in frame.columns else frame.columns[0]
            vals=pd.to_numeric(frame.DFII10, errors='coerce')
            result=[{'date':str(day)[:10], 'value':float(v)} for day,v in zip(frame[dc],vals) if pd.notna(v)]
            if len(result)<61: raise ValueError('FRED valid rows fewer than 61')
            print('OK FRED',len(result),'latest',result[-1]['date'],flush=True)
            return result[-850:]
        except Exception as exc:
            errors.append(f'{type(exc).__name__}: {exc}')
            print('WARN FRED source:', errors[-1],flush=True)
    raise RuntimeError('FRED CSV not reachable: '+' / '.join(errors))


def main():
    print('HYNIX_COLLECTOR_VERSION:', VERSION, flush=True)
    if not ROOT.exists():
        raise SystemExit(f'Missing {ROOT}: run existing dashboard data collection first')
    data = json.loads(ROOT.read_text(encoding='utf-8'))
    old = data.get('hynix_signals') or {}
    merged = {**old,
        'updated_at': NOW.isoformat(),
        'flow': old.get('flow', []),
        'real_yield': old.get('real_yield', []),
        'source': {
            'flow': 'NAVER Finance public page (not a supported API); estimated KRW amount = foreign net shares × daily close; estimated cap = foreign holding shares / holding ratio × close; NO LOGIN',
            'real_yield': 'FRED DFII10; US 10-year TIPS real yield',
        },
        'errors': [],
        'source_status': {k: v for k, v in (old.get('source_status') or {}).items() if k not in ('flow', 'real_yield')},
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
            merged['source_status'][key]={'ok':True,'latest_date':merged[key][-1]['date'],'fetched_at':NOW.isoformat(), 'observations':len(merged[key])}
            print(f'OK {key}: {len(merged[key])} dates; last {merged[key][-1]["date"]}', flush=True)
        except Exception as exc:
            error = f'{key}: {type(exc).__name__}: {exc}'
            merged['errors'].append(error)
            previous = old.get(key) or []
            merged['source_status'][key]={'ok':False,'latest_date':previous[-1].get('date') if previous else None,'error':error,'checked_at':NOW.isoformat()}
            print('WARN ' + error, flush=True)

    data['hynix_signals'] = merged
    tmp=ROOT.with_suffix(ROOT.suffix+'.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    tmp.replace(ROOT)
    if successful == 0:
        print('WARNING: both upstream sources failed; committed failure status, old observations retained; scores must show unavailable when stale.',flush=True)
    if merged['errors'] and successful:
        print('PARTIAL SUCCESS: successful source committed; missing input produces no fabricated score.', flush=True)


if __name__ == '__main__':
    main()
