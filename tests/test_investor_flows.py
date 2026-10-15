import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec=importlib.util.spec_from_file_location('investor_collector', Path(__file__).resolve().parents[1]/'update_hynix_signals.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class InvestorFlowTests(unittest.TestCase):
    def test_json_keeps_foreign_sell_and_institution_buy_separate(self):
        # Values transcribed from the user's Npay screenshot; parser fixture only.
        payload=[{'bizdate':'20261015','foreignNetBuySell':'-1,168,078','organNetBuySell':'+206,547','individualNetBuySell':'+494,555','foreignHoldRatio':'48.90','closePrice':'1,238,000'}]
        row=m.parse_naver_json(payload)[0]
        self.assertEqual(row['date'],'2026-10-15')
        self.assertEqual(row['foreign_net_shares'],-1168078)
        self.assertEqual(row['institution_net_shares'],206547)
        self.assertEqual(row['individual_net_shares'],494555)
        self.assertEqual(row['close'],1238000)
        self.assertNotIn('market_cap_100m',row)

    def test_missing_institution_does_not_become_zero(self):
        row=m.parse_naver_json([{'bizdate':'20261015','foreignNetBuySell':'100','organNetBuySell':'-','closePrice':'1,238,000'}])[0]
        self.assertNotIn('institution_net_shares',row)
        observed=m.parse_naver_json([{'bizdate':'20261015','foreignNetBuySell':'100','organNetBuySell':'0'}])[0]
        self.assertEqual(observed['institution_net_shares'],0)

    def test_ownership_is_never_net_buying(self):
        with self.assertRaises(ValueError):
            m.parse_naver_json([{'bizdate':'20261015','foreignHoldRatio':'48.90','foreignHoldShares':'350000000'}])
        with self.assertRaises(ValueError):
            m.parse_naver_json([{'bizdate':'20261015','foreignNetBuySell':'48.90%'}])

    def test_npay_visible_column_order_and_share_units(self):
        headers=['날짜','외국인','보유율','기관','개인','종가','전일대비']
        cells=['26. 10. 15.','-1,168,078주','48.90%','+206,547주','+494,555주','1,238,000','▼181,000(-12.76%)']
        html='<table><tr>'+''.join('<th>'+x+'</th>' for x in headers)+'</tr><tr>'+''.join('<td>'+x+'</td>' for x in cells)+'</tr></table>'
        row=m.parse_naver_page(html)[0]
        self.assertEqual(row['foreign_net_shares'],-1168078)
        self.assertEqual(row['institution_net_shares'],206547)
        self.assertEqual(row['individual_net_shares'],494555)

    def test_legacy_table_keeps_institution_column_and_sign(self):
        headers=['날짜','종가','전일비','등락률','거래량','기관','외국인 순매매','외국인 보유주수','외국인 보유율']
        cells=['2026.10.15','1,238,000','0','0','1000000','+206547','-1168078','350000000','48.90%']
        html='<table><tr>'+''.join('<th>'+x+'</th>' for x in headers)+'</tr><tr>'+''.join('<td>'+x+'</td>' for x in cells)+'</tr></table>'
        row=m.parse_naver_page(html)[0]
        self.assertEqual(row['institution_net_shares'],206547)
        self.assertEqual(row['foreign_net_shares'],-1168078)

    def test_json_endpoint_precedes_spa_html(self):
        response=Mock()
        response.json.return_value=[{'bizdate':f'202609{i:02d}','foreignNetBuySell':'-100','organNetBuySell':'+50'} for i in range(1,21)]
        with patch.object(m,'fetch',return_value=response) as fetch:
            rows=m.foreign_flow([])
        self.assertEqual(len(rows),20)
        self.assertEqual(fetch.call_count,1)
        self.assertIn('/api/stock/000660/integration',fetch.call_args.args[0])
        self.assertEqual(rows[-1]['institution_net_shares'],50)

    def test_rows_are_deduplicated_and_sorted(self):
        payload=[{'bizdate':'20261015','foreignNetBuySell':'-100','organNetBuySell':'50'}, {'bizdate':'20261014','foreignNetBuySell':'100','organNetBuySell':'-50'}, {'bizdate':'20261015','foreignNetBuySell':'-200','organNetBuySell':'20'}]
        rows=m.parse_naver_json(json.dumps(payload))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[-1]['foreign_net_shares'],-200)

    def test_partial_refresh_preserves_previously_observed_institution(self):
        import tempfile
        with tempfile.TemporaryDirectory() as dirname:
            path=Path(dirname)/'data.json'
            old=[{'date':f'2026-09-{i:02d}','foreign_net_shares':-10,'institution_net_shares':20} for i in range(1,21)]
            path.write_text(json.dumps({'hynix_signals':{'flow':old}}))
            rows=[{'date':x['date'],'foreign_net_shares':-30} for x in old]
            with patch.object(m,'ROOT',path),patch.object(m,'foreign_flow',return_value=rows),patch.object(m,'real_yield',side_effect=TimeoutError('unavailable')):
                m.main()
            got=json.loads(path.read_text())['hynix_signals']
            self.assertEqual(got['flow'][-1]['institution_net_shares'],20)
            self.assertEqual(got['flow'][-1]['foreign_net_shares'],-30)
            self.assertEqual(got['source_status']['flow']['participants']['institution']['observations'],20)

    def test_verified_five_row_integration_is_accepted_without_fabrication(self):
        response=Mock()
        response.json.return_value={'dealTrendInfos':[{'bizdate':f'202610{i:02d}','foreignerPureBuyQuant':'-100','organPureBuyQuant':'+20'} for i in range(11,16)]}
        with patch.object(m,'fetch',return_value=response):
            rows=m.foreign_flow([])
        self.assertEqual(len(rows),5)
        self.assertEqual(rows[-1]['institution_net_shares'],20)

    def test_impossible_date_is_rejected(self):
        with self.assertRaises(ValueError):
            m.parse_naver_json([{'bizdate':'20260231','foreignNetBuySell':'100','organNetBuySell':'20'}])

if __name__=='__main__': unittest.main()
