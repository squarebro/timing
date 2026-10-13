#!/usr/bin/env python3
"""Merge verified external market series into existing data.json.
Requires: pip install pykrx pandas requests
Run from repository root after the dashboard's existing data.json refresh.
Do not overwrite old, valid history on a transient upstream failure.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
import pandas as pd

ROOT = Path(os.environ.get('DASHBOARD_DATA','data.json'))
TZ = timezone(timedelta(hours=9))
TODAY = datetime.now(TZ).date()
START = (TODAY-timedelta(days=1100)).strftime('%Y%m%d')
END = TODAY.strftime('%Y%m%d')

def real_yield():
    url='https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10'
    response=requests.get(url,timeout=30);response.raise_for_status()
    import io
    df=pd.read_csv(io.StringIO(response.text))
    col='DFII10'
    output=[]
    for _,r in df.iterrows():
        v=pd.to_numeric(r[col],errors='coerce')
        if pd.notna(v): output.append({'date':str(r['observation_date'] if 'observation_date' in df.columns else r.iloc[0])[:10], 'value':float(v)})
    return output[-850:]

def foreign_flow():
    from pykrx import stock
    # API/provider availability and naming may change. Fail closed when columns differ.
    f=stock.get_market_trading_value_by_date(START,END,'000660')
    m=stock.get_market_cap_by_date(START,END,'000660')
    if f is None or m is None or f.empty or m.empty:
        raise ValueError('KRX market data empty')
    fc=next((c for c in f.columns if '외국인합계' in str(c)),None)
    mc=next((c for c in m.columns if str(c)=='시가총액'),None)
    if not fc or not mc:raise ValueError(f'Unexpected KRX columns: {list(f.columns)}, {list(m.columns)}')
    data=f.join(m[[mc]],how='inner').sort_index()
    out=[]
    for index,row in data.iterrows():
        net=float(row[fc])/1e8; cap=float(row[mc])/1e8
        if cap>0 and pd.notna(net):out.append({'date':pd.Timestamp(index).strftime('%Y-%m-%d'),'foreign_net_100m':round(net,5),'market_cap_100m':round(cap,5)})
    return out

def main():
    data=json.loads(ROOT.read_text()) if ROOT.exists() else {}
    old=data.get('hynix_signals',{})
    new={'updated_at':datetime.now(TZ).isoformat(),'flow':old.get('flow',[]),'real_yield':old.get('real_yield',[]),'source':{'flow':'KRX via pykrx / 외국인합계 순매수 거래대금','real_yield':'FRED DFII10 / 10-year TIPS yield'}}
    errors=[]
    for key,fn in [('flow',foreign_flow),('real_yield',real_yield)]:
        try:
            new_data=fn()
            if len(new_data)<60:raise ValueError(f'{key}: insufficient history {len(new_data)}')
            merged={r['date']:r for r in old.get(key,[]) if 'date' in r}
            merged.update({r['date']:r for r in new_data})
            new[key]=[merged[d] for d in sorted(merged)][-850:]
            print(key,len(new[key]),'last',new[key][-1]['date'])
        except Exception as e:
            errors.append(f'{key}: {type(e).__name__}: {e}')
    new['errors']=errors
    data['hynix_signals']=new
    ROOT.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n')
    if errors:
        print('ERROR:',*errors,sep='\n')
        raise SystemExit(1)

if __name__=='__main__':main()
