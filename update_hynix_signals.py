#!/usr/bin/env python3
"""Public, no-login SK hynix foreign/institution flows (NAVER/Npay) + FRED DFII10.

Writes data.json / hynix_signals. Foreign and institution net trades are
SHARES, not ownership changes or gross trading amounts. Optional *_net_100m
values estimate KRW value from net shares times close. Scores use signed
net-share history; market-cap estimates are retained only for legacy clients.
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
VERSION = "2026-10-16-r5-foreign-institution"
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


def share_number(value):
    """Signed net shares; ownership percentages are never share quantities."""
    text = str(value).strip().replace('−', '-')
    if '%' in text:
        raise ValueError('ownership ratio is not net shares')
    if text.endswith('주'):
        text = text[:-1]
    return number(text)


def investor_day(value):
    text = re.sub(r'\s', '', str(value))
    if re.fullmatch(r'\d{8}', text):
        text = f'{text[:4]}-{text[4:6]}-{text[6:]}'
    elif re.fullmatch(r'\d{2}\.\d{2}\.\d{2}\.?', text):
        text = '20' + text.rstrip('.').replace('.', '-')
    else:
        text = text.rstrip('.').replace('.', '-')
    from market_sources import valid_day
    if not valid_day(text):
        raise ValueError('invalid investor observation date')
    return text


def investor_row(day, close, foreign=None, institution=None, individual=None, volume=None):
    row = {'date': investor_day(day)}
    if close is not None:
        value = number(close)
        if value <= 0:
            raise ValueError('invalid daily close')
        row['close'] = value
    for name, value in [('foreign', foreign), ('institution', institution), ('individual', individual)]:
        if value is None or str(value).strip() in ('', '-', '--'):
            continue  # Missing is not an observed zero.
        shares = share_number(value)
        if not shares.is_integer() or not -1e8 < shares < 1e8:
            raise ValueError('net shares outside supported range')
        row[name + '_net_shares'] = shares
        if 'close' in row:
            row[name + '_net_100m'] = round(row['close'] * shares / 1e8, 5)
    if not any(k in row for k in ('foreign_net_shares', 'institution_net_shares')):
        raise ValueError('no foreign or institutional net trading')
    if volume is not None:
        try:
            row['volume'] = share_number(volume)
        except ValueError:
            pass
    return row


def parse_naver_json(payload):
    """NAVER's anonymous integration dealTrendInfos investor rows.

    Accept explicitly named net-trading fields only; held shares/ownership are
    distinct and must never be guessed as daily net buying.
    """
    value = json.loads(payload) if isinstance(payload, str) else payload
    root_keys = sorted(value) if isinstance(value, dict) else []
    if isinstance(value, dict):
        value = next((value[k] for k in ('dealTrendInfos', 'data', 'items') if isinstance(value.get(k), list)), None)
    if not isinstance(value, list):
        raise ValueError('NAVER investor JSON list missing; root keys=' + repr(root_keys))
    rows = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        def field(*names):
            return next((item[k] for k in names if k in item), None)
        try:
            row = investor_row(field('bizdate', 'businessDate', 'date'), field('closePrice'),
                field('foreignNetBuySell', 'foreignerPureBuyQuant'),
                field('organNetBuySell', 'organPureBuyQuant', 'institutionNetBuySell'),
                field('individualNetBuySell', 'individualPureBuyQuant'),
                field('accumulatedTradingVolume', 'tradingVolume'))
            rows[row['date']] = row
        except (ValueError, TypeError):
            continue
    if not rows:
        sample = value[0] if value else {}
        raise ValueError('NAVER investor JSON has no valid net trades; first row=' + repr(sample)[:900])
    return [rows[d] for d in sorted(rows)]


def parse_naver_page(markup):
    """Recognize legacy 9-column and Npay 7-column investor tables."""
    soup = BeautifulSoup(markup, 'lxml')
    candidates = soup.find_all('table')
    for table in candidates:
        headers = [re.sub(r'\s+', '', c.get_text(' ', strip=True)) for c in table.find_all('th')]
        heading = ''.join(headers)
        if '외국인' not in heading or '기관' not in heading:
            continue
        npay = all(x in headers for x in ('날짜', '외국인', '기관', '종가'))
        result = {}
        for tr in table.find_all('tr'):
            cells = [c.get_text(' ', strip=True) for c in tr.find_all('td', recursive=False)]
            try:
                if npay and len(cells) == len(headers):
                    get = lambda label: cells[headers.index(label)] if label in headers else None
                    row = investor_row(get('날짜'), get('종가'), get('외국인'), get('기관'), get('개인'), get('거래량'))
                elif len(cells) == 9 and '순매매' in heading and '보유' in heading:
                    row = investor_row(cells[0], cells[1], cells[6], cells[5], volume=cells[4])
                    held, ownership = number(cells[7]), number(cells[8])
                    if held > 0 and .1 <= ownership <= 100:
                        issued = held * 100 / ownership
                        if 1e8 <= issued <= 2e9:
                            row['market_cap_100m'] = round(row['close'] * issued / 1e8, 5)
                else:
                    continue
                result[row['date']] = row
            except (ValueError, TypeError, IndexError):
                continue
        if result:
            return [result[d] for d in sorted(result)]
    # SPA shells have no table. Fail clearly so the JSON endpoint can be used.
    raise ValueError(f'NAVER investor HTML has no supported net-trading table; tables={len(candidates)}')


def foreign_flow(old_rows=None):
    # Verified Npay integration response currently supplies five daily rows.
    # Retain daily observations across runs instead of inventing a 20-day series.
    diagnostics = []
    try:
        response = fetch('https://m.stock.naver.com/api/stock/000660/integration', retries=2)
        rows = parse_naver_json(response.json())
        if len(rows) >= 5:
            print('OK NAVER integration investor rows', len(rows), 'latest', rows[-1]['date'], flush=True)
            return rows
        diagnostics.append(f'integration: only {len(rows)} observations')
    except Exception as exc:
        diagnostics.append(f'integration: {type(exc).__name__}: {exc}')
    known = {x['date'] for x in (old_rows or []) if 'date' in x}
    max_pages = min(120, max(1, int(os.environ.get('NAVER_MAX_PAGES', '40' if len(known)<160 else '5'))))
    variants = [
        ('https://finance.naver.com/item/frgn.naver', {'code':'000660', 'trader_day':'1'}),
        ('https://finance.naver.com/item/frgn.naver', {'code':'000660'}),
    ]
    all_rows = {}
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
            'flow': 'NAVER/Npay investor trend; foreign and institution daily NET shares (buy minus sell), not gross buying/selling; anonymous JSON with recognized HTML fallback. Amounts, if present, are shares times close estimates.',
            'real_yield': 'FRED DFII10; US 10-year TIPS real yield',
        },
        'errors': [],
        'source_status': {k: v for k, v in (old.get('source_status') or {}).items() if k not in ('flow', 'real_yield')},
    }
    successful = 0
    for key, fn in [('flow', lambda: foreign_flow(old.get('flow', []))), ('real_yield', real_yield)]:
        try:
            rows = fn()
            if len(rows) < (5 if key == 'flow' else 61):
                raise ValueError(f'{key}: insufficient observations {len(rows)}')
            by_day = {x['date']: x for x in old.get(key, []) if 'date' in x}
            for row in rows:
                by_day[row['date']] = {**by_day.get(row['date'], {}), **row}
            merged[key] = [by_day[d] for d in sorted(by_day)][-850:]
            successful += 1
            merged['source_status'][key]={'ok':True,'latest_date':merged[key][-1]['date'],'fetched_at':NOW.isoformat(), 'observations':len(merged[key])}
            if key == 'flow':
                merged['source_status'][key]['participants'] = {
                    who: {'observations': sum(isinstance(x.get(who + '_net_shares'), (int, float)) for x in merged[key])}
                    for who in ('foreign', 'institution')
                }
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
