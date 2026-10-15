// Run with a local static server on :8000 and Node's playwright module available.
// All upstream responses here are fixtures; these tests do not assert source availability.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const snapshot = JSON.parse(fs.readFileSync(require('node:path').join(__dirname, '../data.json'), 'utf8'));
const clone = () => JSON.parse(JSON.stringify(snapshot));
const dateAt = i => new Date(Date.UTC(2026, 0, i + 1)).toISOString().slice(0, 10);
const yields = n => Array.from({length:n}, (_,i) => ({date:new Date(Date.UTC(2026, 9, 15-n+1+i)).toISOString().slice(0,10), value:4-i*.001}));
const baseQuote = {itemCode:'000660', closePrice:'250,500', localTradedAt:'2026-10-15T15:30:00+09:00', fluctuationsRatio:'-1.2', marketStatus:'CLOSE'};
const investorDays=(()=>{const days=[];for(let d=new Date('2026-10-15T00:00:00Z');days.length<20;d.setUTCDate(d.getUTCDate()-1))if(d.getUTCDay()%6)days.unshift(d.toISOString().slice(0,10));return days;})();
const investorRows=()=>investorDays.map(date=>({date,foreign_net_shares:-1000,institution_net_shares:500,individual_net_shares:500,close:1238000}));
const onlyNominal=d=>{delete d.quotes;d.hynix_signals={};return d;};
const headers = {'access-control-allow-origin':'*'};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 let passed=0;
 async function scenario(name,build,check,network='offline'){
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
  try{
   const data=build(clone()),errors=[];let proxyRequests=0;
   await page.clock.setFixedTime(new Date('2026-10-16T01:00:00Z'));
   await page.addInitScript(()=>localStorage.setItem('timingDesk.public.v1',JSON.stringify({series:{'000660.KS':[{t:1,c:99999999,h:99999999,l:99999999}]}})));
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',async route=>{
    const url=route.request().url();
    if(url.startsWith('http://127.0.0.1:8000/data.json'))return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
    if(url.startsWith('http://127.0.0.1:8000/'))return route.continue();
    let target=new URL(url);const proxy=target.searchParams.get('url')||target.searchParams.get('quest');
    if(proxy){target=new URL(proxy);proxyRequests++;}
    if(network==='offline'||(network==='proxy'&&!proxy))return route.fulfill({status:403,headers,body:'Blocked fixture'});
    if(target.hostname==='m.stock.naver.com'){
     if(target.pathname.endsWith('/integration'))return route.fulfill({headers,contentType:'application/json',body:JSON.stringify(investorRows().map(x=>({bizdate:x.date.replaceAll('-',''),foreignNetBuySell:String(x.foreign_net_shares),organNetBuySell:String(x.institution_net_shares),closePrice:String(x.close)})))});
     return route.fulfill({headers,contentType:'application/json',body:JSON.stringify(baseQuote)});
    }
    if(target.hostname==='fchart.stock.naver.com'){
     const body='<chartdata><chart>'+Array.from({length:220},(_,i)=>`<item data="${dateAt(i).replaceAll('-','')}|250000|252000|249000|250500|1000"/>`).join('')+'</chart></chartdata>';
     return route.fulfill({headers,contentType:'application/xml',body});
    }
    return route.fulfill({status:403,headers,body:'Optional source unavailable'});
   });
   await page.goto('http://127.0.0.1:8000/Invest.html');
   await page.waitForFunction(()=>PUBLIC_STATE.checked&&!RUN_PENDING,{timeout:60000});
   assert.deepEqual(errors,[],'No page errors');
   await check(page,{proxyRequests});
   console.log('PASS '+name);passed++;
  }finally{await context.close();}
 }
 const hasScore=async p=>assert.match(await p.locator('#hyBuy').textContent(),/^\d+ \/ 100$/);
 await scenario('real saved nominal series calculates even when every external request fails',onlyNominal,async p=>{
  await hasScore(p);assert.match(await p.locator('#hyAutoStatus').textContent(),/국채금리 단독 모드/);
  assert.match(await p.locator('#hyBuyDesc').textContent(),/명목금리 100%/);
  assert.match(await p.locator('#rows .row').filter({hasText:'SK하이닉스'}).textContent(),/저장 일봉 종가.*현재 시세 미확인/);
  assert.equal(await p.evaluate(()=>RESULTS.find(x=>x.sym==='000660.KS').last),snapshot.series['000660.KS'].at(-1).c);
  await p.locator('.hynix-wrap').screenshot({path:'/workspace/timing-setup/nominal-only.png'});
 });
 await scenario('missing nominal input never produces an invented score',d=>{delete d.series['^TNX'];delete d.hynix_signals;return d;},async p=>{
  assert.equal(await p.locator('#hyBuy').textContent(),'—');assert.match(await p.locator('#hyAction').textContent(),/名|명목금리/);
 });
 await scenario('21 observations work without a 60-day history',d=>{delete d.series['^TNX'];d.hynix_signals={nominal_yield:yields(21)};return d;},async p=>{
  await hasScore(p);assert.match(await p.locator('#hyBreakdown').textContent(),/60거래일 이력 부족/);
 });
 await scenario('stale nominal blocks score; stale optional inputs are excluded',d=>{d.series['^TNX']=d.series['^TNX'].map(x=>({...x,t:x.t-366*86400000}));delete d.hynix_signals;return d;},async p=>{
  assert.equal(await p.locator('#hyBuy').textContent(),'—');assert.match(await p.locator('#hyAction').textContent(),/오래/);
 });
 await scenario('available weights and rate direction follow the documented formula',d=>d,async p=>{
  const values=await p.evaluate(()=>({down:hyPoints(-.6,-1.2,null,null,null,null,null),up:hyPoints(.6,1.2,null,null,null,null,null),real:hyPoints(0,0,-.6,-1.2,null,null,null),flow:hyPoints(0,0,null,null,.005*10000,.015*10000,10000),all:hyPoints(0,0,-.6,-1.2,50,150,10000),missing:hyPoints(null,null,0,0,0,0,100)}));
  assert.equal(values.down.score,100);assert.equal(values.up.score,0);assert.equal(values.real.score,63);assert.equal(values.real.coverage,80);assert.equal(values.flow.score,57);assert.equal(values.flow.coverage,70);assert.equal(values.all.score,67);assert.equal(values.missing,null);
 });
 await scenario('latest observed quote is separate from the daily analysis price',d=>{d.quotes={'000660.KS':{price:250500,change_percent:-1.2,observed_at:baseQuote.localTradedAt,source:'NAVER Finance',market_status:'CLOSE'}};return d;},async p=>{
  const row=p.locator('#rows .row').filter({hasText:'SK하이닉스'});assert.match(await row.locator('.px').textContent(),/250,500/);assert.match(await row.textContent(),/최근 거래가격\(장 마감\)/);
  assert.equal(await p.evaluate(()=>RESULTS.find(x=>x.sym==='000660.KS').last),snapshot.series['000660.KS'].at(-1).c);
 });
 await scenario('future quote is rejected instead of labelled as a current price',d=>{d.quotes={'000660.KS':{price:999999,observed_at:'2099-01-01T15:00:00+09:00'}};return d;},async p=>{
  assert.match(await p.locator('#rows .row').filter({hasText:'SK하이닉스'}).textContent(),/현재 시세 미확인/);
 });
 await scenario('source fixture quote is fetched through anonymous fallback',onlyNominal,async(p,c)=>{
  assert.ok(c.proxyRequests>0);assert.equal(await p.evaluate(()=>quoteFor('000660.KS').price),250500);await hasScore(p);
 },'proxy');
 await scenario('stale optional yields do not suppress nominal and top opinion has three lines',d=>{d.hynix_signals={real_yield:yields(100).map(x=>({...x,date:'2025'+x.date.slice(4)}))};return d;},async p=>{
  await hasScore(p);assert.match(await p.locator('#hyBuyDesc').textContent(),/명목금리 100%/);
  assert.equal(await p.locator('#hyTopOpinion > div').count(),3);
  assert.equal(await p.locator('#hySave').count(),0);assert.equal(await p.locator('#hyRealCSV').count(),0);
 });
 await scenario('repeated refresh stays nominal-only and never restores legacy cache',d=>d,async p=>{
  const before=await p.locator('#hyBuy').textContent();await p.locator('#btnRefresh').click();await p.waitForFunction(()=>!RUN_PENDING&&!PUBLIC_STATE.running);assert.equal(await p.locator('#hyBuy').textContent(),before);
  assert.notEqual(await p.evaluate(()=>RESULTS.find(x=>x.sym==='000660.KS').last),99999999);
 });
 await scenario('foreign selling and institutional buying have separate opposite scores',d=>{delete d.series['^TNX'];d.hynix_signals={nominal_yield:yields(61).map(x=>({...x,value:4})),flow:investorRows()};return d;},async p=>{
  const v=await p.evaluate(()=>({f:hyInvestorSignal('foreign'),i:hyInvestorSignal('institution'),score:document.querySelector('#hyBuy').textContent}));
  assert.equal(v.f.score,0);assert.equal(v.i.score,100);assert.equal(v.score,'50 / 100');
  assert.match(await p.locator('#hyBuyDesc').textContent(),/외국인 수급 12.5%/);assert.match(await p.locator('#hyBuyDesc').textContent(),/기관 수급 12.5%/);
  assert.equal(await p.locator('#hyInvestorRows tr').count(),20);
 });
 await scenario('missing institution is excluded rather than treated as zero',d=>{d.hynix_signals={flow:investorRows()};delete d.hynix_signals.flow.at(-1).institution_net_shares;return d;},async p=>{
  assert.equal(await p.evaluate(()=>hyInvestorSignal('institution')),null);assert.ok(await p.evaluate(()=>hyInvestorSignal('foreign')));
  assert.doesNotMatch(await p.locator('#hyBuyDesc').textContent(),/기관/);await hasScore(p);
 });
 await scenario('observed zero flows are neutral and remain included',d=>{d.hynix_signals={flow:investorRows().map(x=>({...x,foreign_net_shares:0,institution_net_shares:0}))};return d;},async p=>{
  assert.equal(await p.evaluate(()=>hyInvestorSignal('institution').score),50);assert.equal(await p.evaluate(()=>hyInvestorSignal('foreign').score),50);assert.match(await p.locator('#hyBuyDesc').textContent(),/기관/);
 });
 await scenario('OHLCV data builds volume support/resistance candidates with no CSV input',onlyNominal,async p=>{
  const result=await p.evaluate(()=>{
   DATA.series['000660.KS']=Array.from({length:252},(_,i)=>({t:Date.parse('2026-02-07T00:00:00Z')+i*86400000,c:100000+(Math.floor(i/18)%11)*1000,h:112000,l:99000,v:100000+i%19*5000}));
   hyRenderVolume();
   return {text:document.getElementById('hyVolumeProfile').textContent,p:hyVolumeProfile(DATA.series['000660.KS'],252)};
  });
  assert.ok(result.p.poc);assert.match(result.text,/주요 지지 후보/);assert.match(result.text,/POC/);
  assert.equal(await p.locator('input[type=file]').count(),0);
 });
 await scenario('NAVER JSON refresh supplies usable foreign and institutional scores',onlyNominal,async p=>{
  assert.equal(await p.evaluate(()=>hyInvestorSignal('foreign').score),0);assert.equal(await p.evaluate(()=>hyInvestorSignal('institution').score),100);assert.match(await p.locator('#hyBuyDesc').textContent(),/기관 수급/);
 },'investors');
 await scenario('screenshot column values and ownership rejection match server parsers',onlyNominal,async p=>{
  const v=await p.evaluate(()=>{
   const row=publicInvestorJson(JSON.stringify([{bizdate:'20261015',closePrice:'1,238,000',foreignNetBuySell:'-1,168,078',organNetBuySell:'+206,547',foreignHoldRatio:'48.90'}]))[0];
   let rejected=false;try{publicInvestorJson(JSON.stringify([{bizdate:'20261015',foreignHoldRatio:'48.90'}]));}catch(e){rejected=true;}
   const points=hyPoints(0,0,-.6,-1.2,null,null,null,100,100);
   return {row,rejected,score:points.score,weight:points.coverage};
  });assert.equal(v.row.foreign_net_shares,-1168078);assert.equal(v.row.institution_net_shares,206547);assert.equal(v.rejected,true);assert.equal(v.score,70);assert.equal(v.weight,100);
 });
 await scenario('five genuine days use a half-weight reference and do not invent 20-day totals',d=>{d.hynix_signals={flow:investorRows().slice(-5)};return d;},async p=>{
  const v=await p.evaluate(()=>({f:hyInvestorSignal('foreign'),i:hyInvestorSignal('institution')}));
  assert.equal(v.f.weight,5);assert.equal(v.i.weight,5);assert.equal(v.f.net20,null);assert.equal(v.f.history.length,5);assert.equal(v.i.score,100);
  assert.match(await p.locator('#hyBreakdown').textContent(),/20일 이력 축적 중/);await hasScore(p);
 });
 console.log('Browser scenarios passed: '+passed);
 await browser.close();
})().catch(e=>{console.error(e);process.exitCode=1;process.exit(1);});
