'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const app=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\nstart\(\);\s*$/,'');
const browserNode=(id='cdn',url='https://edge.example.net/health')=>({id,name:'香港 CDN',basis:'browser',status:'client_pending',test_url:url,latency_ms:999,checked_at:new Date().toISOString()});
function fixture(nodes=[browserNode()]) {
  const dom=new JSDOM('<div id="app"><div id="network-status"></div></div><div id="toast-root"></div>',{url:'https://mg.example/',runScripts:'outside-only',pretendToBeVisual:true});
  const win=dom.window,timers=new Map(),calls=[];let nextTimer=0,visibility='visible';
  Object.defineProperty(win.document,'visibilityState',{get:()=>visibility,configurable:true});
  win.setTimeout=(fn,ms)=>{timers.set(++nextTimer,{fn,ms});return nextTimer;};win.clearTimeout=id=>timers.delete(id);
  let fetchImplementation;
  const standardsFetch=(url,opts={})=>{
    // Fetch main-fetch rejects no-cors + a non-follow redirect mode before any
    // network request. Keep this guard around every per-test response mock.
    if(opts.mode==='no-cors' && opts.redirect && opts.redirect!=='follow') return Promise.reject(new TypeError('Invalid no-cors redirect mode'));
    return fetchImplementation(url,opts);
  };
  Object.defineProperty(win,'fetch',{get:()=>standardsFetch,set:value=>{fetchImplementation=value;},configurable:true});
  win.fetch=async(url,opts)=>{calls.push({url,opts});return {type:'opaque',status:0,body:null};};
  win.eval(app+'\nwindow.fixtureApi={shopNetworkMonitor,networkStatusData,shopNetworkStatusMarkup,shopNetworkSection,renderShopNetworkStatus,refreshBrowserNetworkMeasurements,sampleBrowserNetwork,refreshShopNetworkStatus,stopShopNetworkMonitor,startShopNetworkMonitor,validBrowserMonitorUrl};');
  const api=win.fixtureApi;
  function setNodes(value) {api.shopNetworkMonitor.data=api.networkStatusData({checked_at:new Date().toISOString(),groups:[{id:'cdn',name:'CDN 监测',nodes:value}]});}
  setNodes(nodes);api.shopNetworkMonitor.active=true;
  return {win,api,calls,timers,setNodes,setVisibility:value=>{visibility=value;},close:()=>{api.stopShopNetworkMonitor();win.close();}};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('browser mode ignores server latency and renders no target URL',t=>{
  const f=fixture();t.after(f.close);const node=f.api.shopNetworkMonitor.data.groups[0].nodes[0];
  assert.equal(node.latencyMs,null);assert.equal(node.checkedAt,null);assert.equal(node.status,'client_pending');
  assert.equal(node.testUrl,'https://edge.example.net/health');
  const markup=f.api.shopNetworkStatusMarkup();assert.doesNotMatch(markup,/999|edge\.example|探针 \/ 服务端更新/);assert.match(markup,/暂不可测/);
});
test('warm-up is discarded then three header samples use a median and bypass local cache',async t=>{
  const f=fixture();t.after(f.close);let now=0,cancelled=0,index=0;
  const durations=[2000,300,50,100];f.win.performance.now=()=>now;
  f.win.fetch=async(url,opts)=>{f.calls.push({url,opts});now+=durations[index++];return {type:'basic',status:204,body:{cancel:()=>{cancelled++;return Promise.resolve();}}};};
  await f.api.refreshBrowserNetworkMeasurements();
  assert.equal(f.calls.length,4);assert.equal(cancelled,4);assert.equal(f.api.shopNetworkMonitor.browserResults.get('cdn').latencyMs,100);
  for(const {opts} of f.calls) {
    assert.equal(opts.method,'HEAD');assert.equal(opts.mode,'no-cors');assert.equal(opts.credentials,'omit');assert.equal(opts.redirect,'follow');assert.equal(opts.cache,'no-store');assert.equal(opts.referrerPolicy,'no-referrer');assert.equal(opts.signal.aborted,true);assert.equal(opts.headers,undefined);
  }
  const markup=f.api.shopNetworkStatusMarkup();assert.match(markup,/HTTP 响应 100 ms/);assert.match(markup,/请求成功/);assert.doesNotMatch(markup,/edge\.example|服务端更新/);
  assert.equal(f.api.shopNetworkMonitor.browserControllers.size,0);assert.equal([...f.timers.values()].filter(timer=>timer.ms===5000).length,0);
});
test('opaque responses indicate connectivity without asserting HTTP success',async t=>{
  const f=fixture();t.after(f.close);await f.api.refreshBrowserNetworkMeasurements();
  const markup=f.api.shopNetworkStatusMarkup();assert.match(markup,/连接有响应/);assert.match(markup,/非 Ping/);assert.match(markup,/network-node-status neutral/);assert.doesNotMatch(markup,/请求成功|>正常</);
  assert.match(f.api.shopNetworkSection(),/连接有响应不代表 HTTP 业务成功/);
});
test('readable HTTP error response remains a measured response with error label',async t=>{
  const f=fixture();t.after(f.close);f.win.fetch=async()=>({type:'basic',status:503,body:null});
  await f.api.refreshBrowserNetworkMeasurements();const markup=f.api.shopNetworkStatusMarkup();
  assert.match(markup,/HTTP 响应 \d+ ms/);assert.match(markup,/服务响应异常/);assert.doesNotMatch(markup,/请求成功|>正常</);
});
test('request failures and redirects show generic unavailable without URL or stale server time',async t=>{
  const f=fixture();t.after(f.close);f.win.fetch=async()=>{throw new Error('redirect https://secret.example.net/ IP 203.0.113.9');};
  await f.api.refreshBrowserNetworkMeasurements();const markup=f.api.shopNetworkStatusMarkup();
  assert.match(markup,/暂不可测/);assert.doesNotMatch(markup,/secret\.example|203\.0\.113|999 ms|HTTP 响应/);
});
test('only explicit browser HTTPS URLs are eligible; private source targets never get fetched',async t=>{
  const invalid=['https:edge.example.net','https:///edge.example.net','https://edge.example.net/\u0000','http://edge.example.net','https://user:pass@edge.example.net','https://edge.example.net?q=x','https://edge.example.net#frag','https://edge.example.net?','https://edge.example.net#','https://edge.example.net/ bad','https://edge.example.net\\private','javascript:alert(1)'];
  const nodes=invalid.map((url,index)=>browserNode('n-'+index,url));
  nodes.push({id:'origin',name:'源站',basis:'mmwx',status:'healthy',target:'https://private.example.net',test_url:'https://private.example.net',checked_at:new Date().toISOString(),latency_ms:23});
  const f=fixture(nodes);t.after(f.close);await f.api.refreshBrowserNetworkMeasurements();
  assert.equal(f.calls.length,0);assert.doesNotMatch(JSON.stringify(f.api.shopNetworkMonitor.data),/private\.example/);
});
test('at most three requests run concurrently and every queued node finishes',async t=>{
  const f=fixture(Array.from({length:8},(_,i)=>browserNode('n-'+i)));t.after(f.close);
  let active=0,peak=0;
  f.win.fetch=async(url,opts)=>{active++;peak=Math.max(peak,active);f.calls.push({url,opts});opts.signal.addEventListener('abort',()=>{active--;},{once:true});await tick();return {type:'opaque',status:0,body:null};};
  await f.api.refreshBrowserNetworkMeasurements();
  assert.equal(peak,3);assert.equal(active,0);assert.equal(f.calls.length,32);assert.equal(f.api.shopNetworkMonitor.browserResults.size,8);
  assert.equal([...f.api.shopNetworkMonitor.browserResults.values()].every(result=>result.state==='responded'),true);
});
test('unchanged minute refresh preserves slow queue and avoids starving its tail',async t=>{
  const f=fixture(Array.from({length:4},(_,i)=>browserNode('n-'+i)));t.after(f.close);const pending=[];
  f.win.fetch=(url,opts)=>{f.calls.push({url,opts});return new Promise((resolve,reject)=>{pending.push({resolve,opts});opts.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true});});};
  const run=f.api.refreshBrowserNetworkMeasurements();await tick();const round=f.api.shopNetworkMonitor.browserRound;
  await f.api.refreshBrowserNetworkMeasurements();assert.equal(f.api.shopNetworkMonitor.browserRound,round);assert.equal(f.calls.length,3);assert.equal(f.calls.every(call=>!call.opts.signal.aborted),true);
  while(f.api.shopNetworkMonitor.browserRunning) {for(const item of pending.splice(0)) item.resolve({type:'opaque',status:0,body:null});await tick();}
  await run;assert.equal(f.calls.length,16);assert.equal(f.api.shopNetworkMonitor.browserResults.get('n-3').state,'responded');
});
test('hidden document aborts active requests and prevents queued requests and late updates',async t=>{
  const f=fixture(Array.from({length:4},(_,i)=>browserNode('n-'+i)));t.after(f.close);
  f.win.fetch=(url,opts)=>{f.calls.push({url,opts});return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true}));};
  const run=f.api.refreshBrowserNetworkMeasurements();await tick();f.setVisibility('hidden');f.win.document.dispatchEvent(new f.win.Event('visibilitychange'));
  await run;assert.equal(f.calls.length,3);assert.equal(f.calls.every(call=>call.opts.signal.aborted),true);assert.equal(f.api.shopNetworkMonitor.browserResults.size,0);assert.equal(f.api.shopNetworkMonitor.active,false);
});
test('pagehide aborts pending requests and stale generation cannot replace newer result',async t=>{
  const f=fixture();t.after(f.close);const pending=[];
  f.win.fetch=(url,opts)=>new Promise(resolve=>{f.calls.push({url,opts});pending.push(resolve);});
  const old=f.api.refreshBrowserNetworkMeasurements();await tick();f.win.dispatchEvent(new f.win.Event('pagehide'));
  assert.equal(f.calls[0].opts.signal.aborted,true);
  f.api.shopNetworkMonitor.active=true;f.setNodes([browserNode('new-node','https://new.example.net/health')]);f.win.fetch=async()=>({type:'opaque',status:0,body:null});
  await f.api.refreshBrowserNetworkMeasurements();pending[0]({type:'opaque',status:0,body:null});await old;
  assert.equal(f.api.shopNetworkMonitor.browserResults.has('cdn'),false);assert.equal(f.api.shopNetworkMonitor.browserResults.get('new-node').state,'responded');
});
test('changed test endpoint cancels old round before measuring new configuration',async t=>{
  const f=fixture();t.after(f.close);
  f.win.fetch=(url,opts)=>{f.calls.push({url,opts});return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true}));};
  const old=f.api.refreshBrowserNetworkMeasurements();await tick();f.setNodes([browserNode('cdn','https://changed.example.net/health')]);
  f.win.fetch=async(url,opts)=>{f.calls.push({url,opts});return {type:'opaque',status:0,body:null};};
  await f.api.refreshBrowserNetworkMeasurements();await old;
  assert.equal(f.calls[0].opts.signal.aborted,true);assert.equal(f.calls.slice(1).every(call=>call.url==='https://changed.example.net/health'),true);assert.equal(f.api.shopNetworkMonitor.browserResults.get('cdn').state,'responded');
});
test('five-second deadline aborts each failed sample and reports unavailable',async t=>{
  const f=fixture();t.after(f.close);
  f.win.fetch=(url,opts)=>{f.calls.push({url,opts});return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true}));};
  const run=f.api.refreshBrowserNetworkMeasurements();
  for(let i=0;i<4;i++) {await tick();const timeout=[...f.timers.values()].find(timer=>timer.ms===5000);assert.ok(timeout);timeout.fn();}
  await run;assert.equal(f.calls.length,4);assert.match(f.api.shopNetworkStatusMarkup(),/暂不可测/);
});
test('browser results expire without reusing server timestamps or latency',async t=>{
  const f=fixture();t.after(f.close);await f.api.refreshBrowserNetworkMeasurements();
  f.api.shopNetworkMonitor.browserResults.get('cdn').checkedAt=Date.now()-181000;
  assert.doesNotMatch(f.api.shopNetworkStatusMarkup(),/HTTP 响应/);assert.match(f.api.shopNetworkStatusMarkup(),/暂不可测/);
});
test('server status poll and browser samples remain separate; no client measurements uploaded',async t=>{
  const f=fixture();t.after(f.close);const response={checked_at:new Date().toISOString(),groups:[{id:'cdn',name:'CDN',nodes:[browserNode()]}]};
  f.win.fetch=async(url,opts)=>{f.calls.push({url,opts});return url==='/api/public/network-status'?{ok:true,json:async()=>response}:{type:'opaque',status:0,body:null};};
  await f.api.refreshShopNetworkStatus();await tick();
  assert.equal(f.calls.filter(call=>call.url==='/api/public/network-status').length,1);assert.equal(f.calls.filter(call=>call.url.startsWith('https://')).length,4);
  assert.equal(f.calls.every(call=>call.opts.method===(call.url==='/api/public/network-status'?'GET':'HEAD') && !call.opts.body),true);assert.ok([...f.timers.values()].some(timer=>timer.ms===60000));
});
test('probe response uses heartbeats and omits invalid, stale and offline latency',t=>{
  const f=fixture([]);t.after(f.close);const now=new Date().toISOString();
  for(const value of [0,18.5,60000]) {f.setNodes([{id:'origin',name:'源站甲',basis:'mmwx',status:'healthy',latency_ms:value,checked_at:now}]);assert.match(f.api.shopNetworkStatusMarkup(),new RegExp('响应 '+Math.round(value)+' ms'));assert.match(f.api.shopNetworkStatusMarkup(),/心跳正常/);}
  for(const value of [null,'19',NaN,Infinity,-1,60001]) {f.setNodes([{id:'origin',name:'源站甲',basis:'mmwx',status:'healthy',latency_ms:value,checked_at:now}]);assert.match(f.api.shopNetworkStatusMarkup(),/响应暂无数据/);}
  for(const status of ['down','unknown']) {f.setNodes([{id:'origin',name:'源站甲',basis:'mmwx',status,latency_ms:27,checked_at:now}]);assert.doesNotMatch(f.api.shopNetworkStatusMarkup(),/响应 27/);assert.match(f.api.shopNetworkStatusMarkup(),/心跳离线|心跳未知/);}
  f.setNodes([{id:'origin',name:'源站甲',basis:'mmwx',status:'healthy',latency_ms:27,checked_at:new Date(Date.now()-181000).toISOString()}]);assert.doesNotMatch(f.api.shopNetworkStatusMarkup(),/响应 27/);assert.match(f.api.shopNetworkStatusMarkup(),/心跳未知/);
});

test('readable endpoints rejecting HEAD are unavailable without GET fallback',async t=>{
  const f=fixture();t.after(f.close);
  for(const status of [405,501]) {
    f.win.fetch=async(url,opts)=>{f.calls.push({url,opts});return {type:'basic',status,body:null};};
    await f.api.refreshBrowserNetworkMeasurements();assert.match(f.api.shopNetworkStatusMarkup(),/暂不可测/);assert.doesNotMatch(f.api.shopNetworkStatusMarkup(),/HTTP 响应/);
  }
  assert.equal(f.calls.length,8);assert.equal(f.calls.every(call=>call.opts.method==='HEAD'),true);
});

test('fetch fixture enforces the no-cors redirect rule from the browser standard',async t=>{
  const f=fixture();t.after(f.close);let called=0;
  f.win.fetch=async()=>{called++;return {type:'opaque',status:0,body:null};};
  for(const redirect of ['error','manual']) await assert.rejects(f.win.fetch('https://edge.example.net',{mode:'no-cors',redirect}),/Invalid no-cors redirect mode/);
  assert.equal(called,0);await f.win.fetch('https://edge.example.net',{mode:'no-cors',redirect:'follow'});assert.equal(called,1);
});

// Count only actual measurements: connection warm-up is never a data sample.
test('successful warm-up cannot rescue an otherwise failed measurement round',async t=>{
  const f=fixture();t.after(f.close);let index=0;
  f.win.fetch=async()=>{if(index++===0)return {type:'opaque',status:0,body:null};throw new Error('network');};
  await f.api.refreshBrowserNetworkMeasurements();
  const result=f.api.shopNetworkMonitor.browserResults.get('cdn');
  assert.equal(index,4);assert.equal(result.sampleCount,0);assert.equal(result.latencyMs,null);
  assert.match(f.api.shopNetworkStatusMarkup(),/暂不可测/);assert.doesNotMatch(f.api.shopNetworkStatusMarkup(),/HTTP 响应/);
});
test('a failed warm-up still permits three successful measured samples',async t=>{
  const f=fixture();t.after(f.close);let index=0,now=0;f.win.performance.now=()=>now;
  f.win.fetch=async()=>{if(index++===0)throw new Error('cold connection');now+=80;return {type:'opaque',status:0,body:null};};
  await f.api.refreshBrowserNetworkMeasurements();
  const result=f.api.shopNetworkMonitor.browserResults.get('cdn');
  assert.equal(result.sampleCount,3);assert.equal(result.latencyMs,80);assert.match(f.api.shopNetworkStatusMarkup(),/连接有响应/);
});
test('one response is insufficient; two responses show a warning and their median',async t=>{
  const f=fixture();t.after(f.close);
  for(const durations of [[10,80,null,null],[10,80,null,120]]) {
    let index=0,now=0;f.win.performance.now=()=>now;
    f.win.fetch=async()=>{const duration=durations[index++];if(duration===null)throw new Error('timeout');now+=duration;return {type:'opaque',status:0,body:null};};
    await f.api.refreshBrowserNetworkMeasurements();
    const result=f.api.shopNetworkMonitor.browserResults.get('cdn'),markup=f.api.shopNetworkStatusMarkup();
    if(durations[3]===null) {assert.equal(result.latencyMs,null);assert.match(markup,/有效响应不足/);assert.doesNotMatch(markup,/HTTP 响应/);}
    else {assert.equal(result.latencyMs,100);assert.equal(result.sampleCount,2);assert.match(markup,/HTTP 响应 100 ms/);assert.match(markup,/响应不稳定/);assert.match(markup,/network-node-status degraded/);}
  }
});
