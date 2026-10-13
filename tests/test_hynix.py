import importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
SPEC=importlib.util.spec_from_file_location('collector',str(Path(__file__).resolve().parents[1]/'update_hynix_signals.py'))
m=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
class CollectorTests(unittest.TestCase):
 def test_naver_table_without_css_class(self):
  cells=['2026.10.12','1,500,000','+10,000','+0.7%','1,000,000','-20,000','+11,000','350,000,000','48.0%']
  head=['날짜','종가','전일비','등락률','거래량','기관','외국인 순매매','외국인 보유주수','외국인 보유율']
  html='<table><tr>'+''.join('<th>'+x+'</th>' for x in head)+'</tr><tr>'+''.join('<td>'+x+'</td>' for x in cells)+'</tr></table>'
  result=m.parse_naver_page(html)
  self.assertEqual(1,len(result))
  self.assertAlmostEqual(165,result[0]['foreign_net_100m'])
  self.assertGreater(result[0]['market_cap_100m'],100000)
 def test_blocked_html_rejected(self):
  with self.assertRaises(ValueError):m.parse_naver_page('<html><title>Access denied</title></html>')
 def test_no_foreign_trade_column_rejected(self):
  html='<table><tr><th>날짜</th><th>외국인 보유율</th></tr><tr><td>2026.10.12</td><td>45</td></tr></table>'
  with self.assertRaises(ValueError):m.parse_naver_page(html)
 def test_both_fail_old_data_preserved(self):
  with tempfile.TemporaryDirectory() as dirname:
   p=Path(dirname)/'data.json'; old={'series':{'SPY':[1]},'hynix_signals':{'flow':[{'date':'2026-10-08','foreign_net_100m':10,'market_cap_100m':100000}],'real_yield':[{'date':'2026-10-08','value':1.4}]}}
   p.write_text(json.dumps(old))
   with patch.object(m,'ROOT',p),patch.object(m,'foreign_flow',side_effect=ValueError('mock unavailable')),patch.object(m,'real_yield',side_effect=TimeoutError('mock timeout')):m.main()
   got=json.loads(p.read_text())
   self.assertEqual(got['series'],old['series']);self.assertEqual(got['hynix_signals']['flow'],old['hynix_signals']['flow'])
   self.assertFalse(got['hynix_signals']['source_status']['flow']['ok'])
   self.assertFalse(got['hynix_signals']['source_status']['real_yield']['ok'])
 def test_one_succeeds_other_failed(self):
  with tempfile.TemporaryDirectory() as dirname:
   p=Path(dirname)/'data.json';p.write_text(json.dumps({'hynix_signals':{'flow':[],'real_yield':[]}}))
   rows=[{'date':f'2026-09-{i:02d}','foreign_net_100m':1,'market_cap_100m':1000} for i in range(1,21)]
   with patch.object(m,'ROOT',p),patch.object(m,'foreign_flow',return_value=rows),patch.object(m,'real_yield',side_effect=TimeoutError('mock timeout')):m.main()
   got=json.loads(p.read_text())['hynix_signals']
   self.assertEqual(len(got['flow']),20);self.assertTrue(got['source_status']['flow']['ok']);self.assertFalse(got['source_status']['real_yield']['ok'])
if __name__=='__main__':unittest.main()
