import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from market_sources import parse_naver_history, parse_naver_quote, parse_fred_csv
import fetch_data as collector


class MarketSourcesTests(unittest.TestCase):
    def test_raw_quote_retains_price_and_observation_time(self):
        quote = parse_naver_quote(json.dumps({'itemCode': '000660', 'closePrice': '250,500', 'localTradedAt': '2025-10-15T15:30:00+09:00', 'fluctuationsRatio': '-1.2', 'marketStatus': 'CLOSE'}), '000660.KS')
        self.assertEqual(quote['price'], 250500)
        self.assertEqual(quote['change_percent'], -1.2)
        self.assertEqual(quote['observed_at'], '2025-10-15T15:30:00+09:00')

    def test_quote_without_source_time_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_naver_quote('{"closePrice":"250,500"}', '000660.KS')

    def test_wrong_stock_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_naver_quote('{"itemCode":"005930","closePrice":"250,500","localTradedAt":"2025-10-15T15:30:00+09:00"}', '000660.KS')

    def test_naver_history_uses_krx_day_and_unscaled_ohlc(self):
        from datetime import date, timedelta, datetime, timezone
        days=[(date(2025, 1, 1)+timedelta(days=i)).strftime('%Y%m%d') for i in range(40)]
        xml='<?xml version="1.0" encoding="EUC-KR"?><chartdata><chart>'+''.join(f'<item data="{d}|250000|252000|249000|250500|100000"/>' for d in days)+'</chart></chartdata>'
        rows=parse_naver_history(xml)
        self.assertEqual(rows[-1]['c'], 250500)
        self.assertEqual(datetime.fromtimestamp(rows[0]['t']/1000, timezone.utc).isoformat(), '2024-12-31T15:00:00+00:00')
        self.assertEqual(rows[0]['h'], 252000)

    def test_missing_fred_values_are_not_zero(self):
        text='observation_date,DGS10\n'+''.join(f'2025-01-{i:02d},4.1\n' for i in range(1, 25))+'2025-01-25,.\n2025-01-26,NaN\n2025-02-31,4.3\n'
        rows=parse_fred_csv(text,'DGS10')
        self.assertEqual(len(rows), 24)
        self.assertEqual(rows[-1]['date'], '2025-01-24')

    def test_refresh_preserves_extended_signals_quotes_and_failed_history(self):
        import os
        with tempfile.TemporaryDirectory() as dirname:
            path=Path(dirname)/'data.json'
            old={'custom_metadata': {'keep': True},'series': {'000660.KS':[{'t':1,'c':200000,'h':210000,'l':190000}]},'quotes': {'000660.KS':{'price':200000}},'hynix_signals': {'flow':[{'date':'2025-10-15','foreign_net_100m':1,'market_cap_100m':1000}],'real_yield':[{'date':'2025-10-15','value':1.4}]}}
            path.write_text(json.dumps(old))
            before=Path.cwd()
            try:
                os.chdir(dirname)
                with patch.object(collector,'ALL_SYMBOLS',['000660.KS']),patch.object(collector,'TICKERS',['000660.KS']),patch.object(collector,'fetch_symbol',return_value={'ok':False,'error':'network blocked'}),patch.object(collector,'naver_quote',side_effect=TimeoutError('blocked')),patch.object(collector,'fred_series',side_effect=TimeoutError('blocked')),patch.object(collector,'fetch_crypto_fg',return_value={'ok':False}),patch.object(collector.time,'sleep'):
                    with self.assertRaises(SystemExit):
                        collector.main()
            finally:
                os.chdir(before)
            got=json.loads(path.read_text())
            self.assertEqual(got['hynix_signals']['flow'],old['hynix_signals']['flow'])
            self.assertEqual(got['hynix_signals']['real_yield'],old['hynix_signals']['real_yield'])
            self.assertEqual(got['series'],old['series'])
            self.assertEqual(got['quotes'],old['quotes'])
            self.assertEqual(got['custom_metadata'],old['custom_metadata'])
            self.assertTrue(got['series_status']['000660.KS']['retained'])
            self.assertEqual(got['failed'],['000660.KS'])

    def test_secondary_collector_preserves_nominal_yield(self):
        spec=importlib.util.spec_from_file_location('signals',Path(__file__).resolve().parents[1]/'update_hynix_signals.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as dirname:
            path=Path(dirname)/'data.json'
            nominal=[{'date':'2025-10-15','value':4.1}]
            path.write_text(json.dumps({'hynix_signals':{'nominal_yield':nominal,'source_status':{'nominal_yield':{'ok':True}}}}))
            with patch.object(module,'ROOT',path),patch.object(module,'foreign_flow',side_effect=TimeoutError()),patch.object(module,'real_yield',side_effect=TimeoutError()):
                module.main()
            got=json.loads(path.read_text())['hynix_signals']
            self.assertEqual(got['nominal_yield'],nominal)
            self.assertTrue(got['source_status']['nominal_yield']['ok'])

if __name__ == '__main__':
    unittest.main()
