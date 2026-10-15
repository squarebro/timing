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
    if(target.hostname==='m.stock.naver.com')return route.fulfill({headers,contentType:'application/json',body:JSON.stringify(baseQuote)});
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
 await scenario('real saved nominal series calculates even when every external request fails',d=>d,async p=>{
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
  assert.equal(values.down.score,100);assert.equal(values.up.score,0);assert.equal(values.real.score,61);assert.equal(values.real.coverage,90);assert.equal(values.flow.score,56);assert.equal(values.flow.coverage,80);assert.equal(values.all.score,65);assert.equal(values.missing,null);
 });
 await scenario('latest observed quote is separate from the daily analysis price',d=>{d.quotes={'000660.KS':{price:250500,change_percent:-1.2,observed_at:baseQuote.localTradedAt,source:'NAVER Finance',market_status:'CLOSE'}};return d;},async p=>{
  const row=p.locator('#rows .row').filter({hasText:'SK하이닉스'});assert.match(await row.locator('.px').textContent(),/250,500/);assert.match(await row.textContent(),/최근 거래가격\(장 마감\)/);
  assert.equal(await p.evaluate(()=>RESULTS.find(x=>x.sym==='000660.KS').last),snapshot.series['000660.KS'].at(-1).c);
 });
 await scenario('future quote is rejected instead of labelled as a current price',d=>{d.quotes={'000660.KS':{price:999999,observed_at:'2099-01-01T15:00:00+09:00'}};return d;},async p=>{
  assert.match(await p.locator('#rows .row').filter({hasText:'SK하이닉스'}).textContent(),/현재 시세 미확인/);
 });
 await scenario('source fixture quote is fetched through anonymous fallback',d=>d,async(p,c)=>{
  assert.ok(c.proxyRequests>0);assert.equal(await p.evaluate(()=>quoteFor('000660.KS').price),250500);await hasScore(p);
 },'proxy');
 await scenario('optional stale yields and manual blank inputs do not suppress nominal',d=>{d.hynix_signals={real_yield:yields(100).map(x=>({...x,date:'2025'+x.date.slice(4)}))};return d;},async p=>{
  await p.evaluate(()=>{localStorage.setItem(HY_LS_KEY,JSON.stringify({flow5:'',flow20:'',cap:'1000',date:'2026-10-15'}));hyRender();});
  await hasScore(p);assert.match(await p.locator('#hyBuyDesc').textContent(),/명목금리 100%/);
 });
 await scenario('repeated refresh stays nominal-only and never restores legacy cache',d=>d,async p=>{
  const before=await p.locator('#hyBuy').textContent();await p.locator('#btnRefresh').click();await p.waitForFunction(()=>!RUN_PENDING&&!PUBLIC_STATE.running);assert.equal(await p.locator('#hyBuy').textContent(),before);
  assert.notEqual(await p.evaluate(()=>RESULTS.find(x=>x.sym==='000660.KS').last),99999999);
 });
 console.log('Browser scenarios passed: '+passed);
 await browser.close();
})().catch(e=>{console.error(e);process.exitCode=1;process.exit(1);});
