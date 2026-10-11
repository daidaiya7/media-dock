'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8').replace(/\nstart\(\);\s*$/, '');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject};};
const response = (value, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => value});
const catalog = () => ({products: [], redemption_url: 'https://members.example/', payment_configured: false});
const planCatalog = () => ({...catalog(), products:[
  {id:'own-trial',requires_own_115:true,duration_days:1,listed_price_cents:200},
  {id:'managed-annual',requires_own_115:false,duration_days:365,listed_price_cents:59900}
]});
const movie = (id = 1, overrides = {}) => ({title: `测试电影 ${id}`, poster_url: `/api/public/featured-images/${String(id).padStart(64,'0')}.jpg`, url: `https://www.themoviedb.org/movie/${id}`, ...overrides});

function fixture({route = '/', handler} = {}) {
  const dom = new JSDOM('<div id="app"></div><div id="modal-root"></div><div id="toast-root"></div>', {
    url: `https://mg.example${route}`, runScripts: 'outside-only', pretendToBeVisual: true
  });
  const win = dom.window, calls = [], timers = new Map(), starts = [];
  let sequence = 0, stops = 0;
  Object.defineProperty(win.document, 'visibilityState', {get: () => 'visible', configurable: true});
  win.setTimeout = (fn, ms) => {timers.set(++sequence, {fn, ms}); return sequence;};
  win.clearTimeout = id => timers.delete(id);
  const carousel = {start: root => starts.push(root), stop: () => {stops++;}};
  win.MediaDockCarousel = carousel;
  win.fetch = async (url, options) => {
    calls.push({url, options});
    if (handler) return handler(url, options);
    if (url === '/api/public/catalog') return response(catalog());
    if (url === '/api/public/featured-movies') return response({movies: []});
    throw new Error(`Unexpected mocked endpoint: ${url}`);
  };
  win.eval(source + '\nwindow.routingApi={start,renderShop,renderPublicOrder,renderServiceStatus,loadHomeFeatured,homeHero,featuredSettingsModal,mediaIntegrationModal,loadEmbyLibraries,state,shopNetworkMonitor,stopShopNetworkMonitor};');
  return {
    win, calls, timers, starts, carousel,
    api: win.routingApi,
    stops: () => stops,
    app: win.document.getElementById('app'),
    go(route) {win.history.pushState({}, '', route); return win.routingApi.start();},
    close() {win.routingApi.stopShopNetworkMonitor();carousel.stop();win.close();}
  };
}

test('home renders immediately before catalog or feed and never starts network monitoring', async t => {
  const items = deferred(), featured = deferred();
  const f = fixture({handler: url => url === '/api/public/catalog' ? items.promise : featured.promise}); t.after(f.close);
  const run = f.api.start();
  assert.ok(f.app.querySelector('#home-title'));
  assert.equal(f.app.querySelectorAll('[data-carousel-slide]').length, 0);
  assert.equal(f.starts.length, 1);
  assert.ok(f.app.querySelector('a[href="/status"]'));
  assert.ok(f.app.querySelector('a[href="/claim"]'));
  assert.equal(f.app.querySelector('#plans'),null);
  assert.equal(f.app.querySelectorAll('[data-action="purchase"]').length,0);
  assert.equal(f.app.querySelector('#network-status'), null);
  assert.equal(f.api.shopNetworkMonitor.active, false);
  assert.deepEqual(f.calls.map(call => call.url).sort(), ['/api/public/catalog', '/api/public/featured-movies']);
  items.resolve(response(catalog())); featured.resolve(response({movies: []}));
  await run; await tick();
  f.win.document.dispatchEvent(new f.win.Event('visibilitychange'));
  assert.equal(f.calls.some(call => call.url.includes('network-status') || /^https?:/.test(call.url)), false);
  assert.equal(f.app.querySelector('#network-status'), null);
  assert.ok(f.app.querySelector('.site-nav a[href="https://members.example/"]'));
});

test('catalog failure leaves carousel and all main home entries usable', async t => {
  const f = fixture({handler: url => {
    if (url === '/api/public/catalog') throw new Error('Catalog unavailable');
    return response({movies: []});
  }}); t.after(f.close);
  await f.api.start(); await tick();
  assert.equal(f.app.querySelector('#plans'),null);
  assert.equal(f.app.querySelectorAll('[data-carousel-slide]').length, 0);
  assert.equal(f.starts.length, 1);
  assert.ok(f.app.querySelector('.home-actions a[href="/plans"]'));
  assert.ok(f.app.querySelector('.home-service-link[href="/status"]'));
  assert.ok(f.app.querySelector('a[href="/claim"]'));
  assert.ok(f.app.querySelector('.home-service-link[href="/library"]'));
  assert.ok(f.app.querySelector('.home-service-link[href="/discover"]'));
  assert.ok(f.app.querySelector('a[href^="https://market.example.com/"]'));
  assert.equal(f.calls.some(call => call.url.includes('network-status')), false);
});

test('plans has its own route, active navigation, filters and purchase form without home content', async t => {
  const f=fixture({route:'/plans',handler:url=>{
    assert.equal(url,'/api/public/catalog');return response(planCatalog());
  }});t.after(f.close);
  await f.api.start();
  assert.equal(f.win.document.title,'观影套餐 · 影视服 MediaDock');
  assert.equal(f.app.querySelectorAll('.product').length,2);
  assert.ok(f.app.querySelector('.site-nav a.active[href="/plans"]'));
  assert.equal(f.app.querySelector('#home-carousel'),null);
  assert.equal(f.app.querySelector('#network-status'),null);
  assert.equal(f.starts.length,0);
  f.app.querySelector('[data-filter="managed"]').click();
  assert.equal(f.app.querySelectorAll('.product').length,1);
  const purchase=f.app.querySelector('[data-action="purchase"]');
  assert.equal(purchase.dataset.product,'managed-annual');purchase.click();
  assert.equal(f.win.document.querySelector('#purchase-form').dataset.product,'managed-annual');
  assert.match(f.win.document.querySelector('.order-summary').textContent,/599/);
  assert.deepEqual(f.calls.map(x=>x.url),['/api/public/catalog']);
  assert.equal(f.calls.every(x=>x.options.method==='GET'),true);
});

test('plans purchase keeps existing order credential and retrieval flow with mocked writes only', async t => {
  let posted;
  const f=fixture({route:'/plans',handler:(url,options)=>{
    if(url==='/api/public/catalog')return response({...planCatalog(),payment_configured:true});
    if(url==='/api/public/orders'){
      posted=JSON.parse(options.body);
      return response({order_id:'TEST-ONLY-ORDER',access_token:'TEST-ONLY-TOKEN',product_id:'own-trial'});
    }
    assert.equal(url,'/api/public/orders/TEST-ONLY-ORDER');
    assert.equal(options.headers['X-Order-Token'],'TEST-ONLY-TOKEN');
    return response({product_id:'own-trial',status:'pending_payment'});
  }});t.after(f.close);
  await f.api.start();f.app.querySelector('[data-product="own-trial"]').click();
  const form=f.win.document.querySelector('#purchase-form');form.elements.contact_note.value='测试备注';
  form.dispatchEvent(new f.win.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
  assert.deepEqual(posted,{product_id:'own-trial',contact_note:'测试备注'});
  assert.equal(f.win.location.pathname,'/');assert.equal(f.win.location.search,'?order=TEST-ONLY-ORDER');
  assert.equal(f.win.sessionStorage.getItem('mediadock-order-TEST-ONLY-ORDER'),'TEST-ONLY-TOKEN');
  assert.match(f.app.textContent,/订单已登记/);assert.match(f.app.textContent,/需要管理员核对到账/);
  assert.equal(f.app.querySelector('#plans'),null);
});

test('home remains free of package cards even after successful catalog and featured responses', async t => {
  const f=fixture({handler:url=>response(url==='/api/public/catalog'?planCatalog():{movies:[]})});t.after(f.close);
  await f.api.start();await tick();
  assert.equal(f.app.querySelectorAll('.product,#plans,.how-it-works').length,0);
  assert.ok(f.app.querySelector('.home-actions a[href="/plans"]'));
  assert.ok(f.app.querySelector('.site-footer a[href="/plans"]'));
});

test('legacy home plans link resolves to plans but cannot intercept an existing order link', async t => {
  const f=fixture({route:'/#plans',handler:()=>response(planCatalog())});t.after(f.close);
  await f.api.start();assert.equal(f.win.location.pathname,'/plans');assert.equal(f.win.location.hash,'');
  assert.ok(f.app.querySelector('#plans'));
  await f.go('/?order=TEST-ONLY#plans');
  assert.equal(f.win.location.search,'?order=TEST-ONLY');assert.ok(f.app.querySelector('#order-access-form'));
  assert.equal(f.app.querySelector('#plans'),null);
});

test('late plans catalog cannot overwrite a new home and plans failure preserves navigation', async t => {
  const pending=deferred();let count=0;
  const f=fixture({route:'/plans',handler:url=>url.includes('featured')?response({movies:[]}):++count===1?pending.promise:response(planCatalog())});t.after(f.close);
  const run=f.api.start();await f.go('/');pending.resolve(response(planCatalog()));await run;
  assert.ok(f.app.querySelector('#home-title'));assert.equal(f.app.querySelector('#plans'),null);
  const failed=fixture({route:'/plans/',handler:()=>{throw new Error('Catalog unavailable');}});t.after(failed.close);
  await failed.api.start();assert.match(failed.app.querySelector('#plans').textContent,/套餐暂时无法加载/);
  assert.ok(failed.app.querySelector('.site-nav a[href="/library"]'));
  assert.equal(failed.starts.length,0);
});

test('late order catalog cannot overwrite plans or a newer visit to the same order route', async t => {
  for(const destination of ['/plans','/?order=TEST-ONLY']){
    const pending=deferred();let requests=0;
    const f=fixture({route:'/?order=TEST-ONLY',handler:url=>{
      assert.equal(url,'/api/public/catalog');
      return ++requests===1?pending.promise:response(planCatalog());
    }});t.after(f.close);
    const old=f.api.start();await f.go(destination);
    const before=f.app.innerHTML,title=f.win.document.title;
    pending.resolve(response({...planCatalog(),redemption_url:'https://stale.example/'}));await old;
    assert.equal(f.app.innerHTML,before);assert.equal(f.win.document.title,title);
    assert.equal(f.api.state.catalog.redemption_url,'https://members.example/');
    assert.equal(f.calls.length,2);
    assert.equal(!!f.app.querySelector('.plans-page'),destination==='/plans');
  }
});

test('late order details cannot replace plans or a newer load of the same order, on success or failure', async t => {
  for(const destination of ['/plans','/?order=TEST-ONLY'])for(const failed of [false,true]){
    const pending=deferred();let orderReads=0;
    const f=fixture({route:'/?order=TEST-ONLY',handler:url=>{
      if(url==='/api/public/catalog')return response(planCatalog());
      assert.equal(url,'/api/public/orders/TEST-ONLY');
      return ++orderReads===1?pending.promise:response({product_id:'own-trial',status:'delivered',claim_token:'TEST-NEW-CLAIM'});
    }});t.after(f.close);
    f.win.sessionStorage.setItem('mediadock-order-TEST-ONLY','TEST-ONLY-TOKEN');
    const old=f.api.start();await tick();assert.equal(orderReads,1);
    await f.go(destination);const before=f.app.innerHTML,title=f.win.document.title;
    pending.resolve(failed?response({error:'Old request failed'},404):response({product_id:'own-trial',status:'pending_payment'}));await old;
    assert.equal(f.app.innerHTML,before);assert.equal(f.win.document.title,title);
    if(destination==='/plans')assert.ok(f.app.querySelector('.plans-page'));
    else{assert.match(f.app.textContent,/已发货/);assert.ok(f.app.querySelector('a[href="/claim#TEST-NEW-CLAIM"]'));}
  }
});

test('direct concurrent order refreshes also discard the older response without routing again', async t => {
  const pending=deferred();let reads=0;
  const f=fixture({route:'/?order=TEST-ONLY',handler:url=>{
    assert.equal(url,'/api/public/orders/TEST-ONLY');
    return ++reads===1?pending.promise:response({status:'delivered'});
  }});t.after(f.close);
  f.win.sessionStorage.setItem('mediadock-order-TEST-ONLY','TEST-ONLY-TOKEN');
  const old=f.api.renderPublicOrder('TEST-ONLY');await f.api.renderPublicOrder('TEST-ONLY');
  const before=f.app.innerHTML;
  pending.resolve(response({status:'pending_payment'}));await old;
  assert.equal(f.app.innerHTML,before);assert.match(f.app.textContent,/已发货/);
});

test('both status routes render monitoring independently while catalog is unavailable', async t => {
  for (const route of ['/status', '/status/']) {
    const items = deferred();
    const now = new Date().toISOString();
    const f = fixture({route, handler: url => {
      if (url === '/api/public/catalog') return items.promise;
      if (url === '/api/public/network-status') return response({checked_at: now, groups: [{id: 'origin', name: '源站监测', nodes: [{id: 'origin-one', name: '源站一', basis: 'mmwx', status: 'healthy', checked_at: now, latency_ms: 35}]}]});
      throw new Error('Unexpected mocked endpoint');
    }}); t.after(f.close);
    const run = f.api.start();
    assert.ok(f.app.querySelector('.status-page #network-status'));
    assert.equal(f.app.querySelector('#home-carousel'), null);
    assert.equal(f.starts.length, 0);
    assert.equal(f.api.shopNetworkMonitor.active, true);
    await tick();
    assert.match(f.app.querySelector('#network-status').textContent, /响应 35 ms/);
    assert.match(f.app.querySelector('#network-status').textContent, /心跳正常/);
    items.reject(new Error('Catalog unavailable')); await run;
    assert.equal(f.api.shopNetworkMonitor.active, true);
    assert.ok([...f.timers.values()].some(timer => timer.ms === 60000));
    assert.equal(f.calls.some(call => call.url.includes('featured-movies')), false);
    assert.equal(f.win.document.title, '服务状态 · 影视服 MediaDock');
  }
});

test('status can perform its browser CDN samples after a catalog failure using only mocked fetch', async t => {
  const f = fixture({route: '/status', handler: url => {
    if (url === '/api/public/catalog') throw new Error('Catalog unavailable');
    if (url === '/api/public/network-status') return response({groups: [{id: 'cdn', name: 'CDN', nodes: [{id: 'edge', name: '测试线路', basis: 'browser', status: 'client_pending', test_url: 'https://edge.example/health', latency_ms: null}]}]});
    if (url === 'https://edge.example/health') return {type: 'opaque', status: 0, body: null};
    throw new Error('Unexpected mocked endpoint');
  }}); t.after(f.close);
  await f.api.start(); await tick();
  const samples = f.calls.filter(call => call.url === 'https://edge.example/health');
  assert.equal(samples.length, 4);
  assert.equal(samples.every(call => call.options.method === 'HEAD' && call.options.credentials === 'omit'), true);
  assert.match(f.app.querySelector('#network-status').textContent, /HTTP 响应/);
  assert.equal(f.starts.length, 0);
});

test('claim keeps the original claim form and does not request a claim without submitting', async t => {
  const f = fixture({route: '/claim#test-only-claim', handler: () => {throw new Error('Catalog unavailable');}}); t.after(f.close);
  await f.api.start();
  assert.ok(f.app.querySelector('#claim-form'));
  assert.equal(f.app.querySelector('#claim-form textarea').value, 'test-only-claim');
  assert.equal(f.app.querySelector('#home-carousel'), null);
  assert.equal(f.app.querySelector('#network-status'), null);
  assert.deepEqual(f.calls.map(call => call.url), ['/api/public/catalog']);
  assert.equal(f.calls.every(call => call.options.method === 'GET'), true);
  assert.equal(f.starts.length, 0);
});

test('query order retains its credential form and escapes the synthetic order ID', async t => {
  const f = fixture({route: '/?order=TEST-%3Csample%3E', handler: () => {throw new Error('Catalog unavailable');}}); t.after(f.close);
  await f.api.start();
  assert.ok(f.app.querySelector('#order-access-form'));
  assert.equal(f.app.querySelector('.order-id').textContent, 'TEST-<sample>');
  assert.equal(f.app.querySelector('sample'), null);
  assert.equal(f.app.querySelector('#home-carousel'), null);
  assert.equal(f.app.querySelector('#network-status'), null);
  assert.deepEqual(f.calls.map(call => call.url), ['/api/public/catalog']);
  assert.equal(f.starts.length, 0);
});

test('featured content renders at most six allowed TMDB posters, escaped titles and attribution', async t => {
  const unsafeTitle = '<img src=x onerror="alert(1)"> & "影视服"';
  const f = fixture({handler: url => response(url === '/api/public/catalog' ? catalog() : {movies: Array.from({length: 8}, (_, i) => movie(i + 1, i ? {} : {title: unsafeTitle})), updated_at: new Date().toISOString()})}); t.after(f.close);
  await f.api.start(); await tick();
  const root = f.app.querySelector('#home-carousel');
  assert.equal(root.querySelectorAll('[data-carousel-slide]').length, 6);
  assert.equal(root.querySelector('h2').textContent, unsafeTitle);
  assert.equal(root.querySelector('h2 img'), null);
  assert.equal(root.querySelector('[onerror]'), null);
  assert.equal(root.querySelector('img').alt, `${unsafeTitle} 海报`);
  assert.equal(root.querySelectorAll('h2 a, h2 svg').length, 0);
  assert.match(f.app.querySelector('#home-carousel-source').textContent, /影片资讯，不代表片库已收录/);
  assert.match(f.app.querySelector('#home-carousel-source').textContent, /uses the TMDB API/);
  assert.equal(f.starts.length, 2);
  assert.equal(f.starts[1], root);
});

test('featured content rejects non-allowlisted image and detail URLs', async t => {
  const invalid = [
    {poster_url: 'http://image.tmdb.org/t/p/w500/a.jpg'},
    {poster_url: 'https://image.tmdb.org.evil.example/t/p/w500/a.jpg'},
    {poster_url: 'https://image.tmdb.org/t/p/w780/a.jpg'},
    {poster_url: 'https://image.tmdb.org/t/p/w500/a.svg'},
    {poster_url: 'https://image.tmdb.org/t/p/w500/a.jpg?query=1'},
    {poster_url: 'https://image.tmdb.org/t/p/w500/a.jpg#fragment'},
    {poster_url: 'https://name@image.tmdb.org/t/p/w500/a.jpg'},
    {url: 'https://www.themoviedb.org.evil.example/movie/1'},
    {url: 'https://www.themoviedb.org/movie/1?source=unsafe'},
    {url: 'https://www.themoviedb.org/tv/1'},
    {url: 'javascript:alert(1)'},
    {title: 'x'.repeat(201)}
  ];
  for (const bad of invalid) {
    const f = fixture({handler: url => response(url === '/api/public/catalog' ? catalog() : {movies: [movie(99, bad), movie(1), movie(2), movie(3)]})}); t.after(f.close);
    await f.api.start(); await tick();
    const root = f.app.querySelector('#home-carousel');
    assert.equal(root.querySelectorAll('[data-carousel-slide]').length, 3, JSON.stringify(bad));
    assert.equal(root.querySelectorAll('a').length, 0);
    assert.equal(f.app.querySelectorAll('#home-carousel-source a').length, 0);
    assert.deepEqual([...root.querySelectorAll('h2')].map(heading => heading.textContent), ['测试电影 1', '测试电影 2', '测试电影 3']);
    assert.equal(root.querySelectorAll('img').length, 3);
  }
});

test('small cached batches render and failed feeds never use the three brand images', async t => {
  for (const mode of ['too-few', 'failed']) {
    const f = fixture({handler: url => {
      if (url === '/api/public/catalog') return response(catalog());
      if (mode === 'failed') throw new Error('Feed unavailable');
      return response({movies: [movie(1), movie(2), movie(3, {url: 'https://invalid.example/'})]});
    }}); t.after(f.close);
    await f.api.start(); await tick();
    const root = f.app.querySelector('#home-carousel');
    assert.equal(root.querySelectorAll('[data-carousel-slide]').length, mode==='failed'?0:2);
    assert.equal([...root.querySelectorAll('img')].every(image => image.getAttribute('src').startsWith('/api/public/featured-images/')), true);
    assert.equal(root.getAttribute('aria-label'), mode==='failed'?'影视服光影轮播':'本周热门电影轮播');
    assert.equal(f.starts.length, mode==='failed'?1:2);
  }
});

test('a failed cached poster hides without fetching a brand image without creating a new carousel', async t => {
  const f = fixture({handler: url => response(url === '/api/public/catalog' ? catalog() : {movies: [movie(1), movie(2), movie(3)]})}); t.after(f.close);
  await f.api.start(); await tick();
  const image = f.app.querySelector('#home-carousel img');
  image.dispatchEvent(new f.win.Event('error'));
  assert.equal(image.getAttribute('src'), null);
  assert.equal(image.hidden, true);
  image.dispatchEvent(new f.win.Event('error'));
  assert.equal(f.starts.length, 2);
});

test('late featured and catalog responses cannot replace a completed claim navigation', async t => {
  const items = deferred(), featured = deferred();
  const f = fixture({handler: url => url === '/api/public/catalog' ? items.promise : featured.promise}); t.after(f.close);
  const home = f.api.start();
  f.api.state.catalog = catalog();
  await f.go('/claim');
  const claim = f.app.firstElementChild, starts = f.starts.length;
  featured.resolve(response({movies: [movie(1), movie(2), movie(3)]}));
  items.resolve(response(catalog()));
  await home; await tick();
  assert.equal(f.app.firstElementChild, claim);
  assert.ok(f.app.querySelector('#claim-form'));
  assert.equal(f.starts.length, starts);
});

test('late featured responses cannot restart a carousel after routing away while catalog is pending', async t => {
  for (const route of ['/claim', '/?order=TEST-ONLY']) {
    const items = deferred(), featured = deferred();
    const f = fixture({handler: url => url === '/api/public/catalog' ? items.promise : featured.promise}); t.after(f.close);
    const home = f.api.start();
    const destination = f.go(route), starts = f.starts.length;
    featured.resolve(response({movies: [movie(1), movie(2), movie(3)]}));
    await tick();
    assert.equal(f.starts.length, starts, `late feed must not restart on ${route}`);
    items.resolve(response(catalog()));
    await home; await destination;
  }
});

test('returning from status stops its monitor and renders home without further status requests', async t => {
  const f = fixture({route: '/status', handler: url => {
    if (url === '/api/public/network-status') return response({groups: []});
    if (url === '/api/public/catalog') return response(catalog());
    return response({movies: []});
  }}); t.after(f.close);
  await f.api.start(); await tick();
  const before = f.calls.filter(call => call.url.includes('network-status')).length;
  await f.go('/'); await tick();
  assert.equal(f.api.shopNetworkMonitor.active, false);
  assert.equal(f.app.querySelector('#network-status'), null);
  assert.ok(f.app.querySelector('#home-carousel'));
  f.win.document.dispatchEvent(new f.win.Event('visibilitychange'));
  assert.equal(f.calls.filter(call => call.url.includes('network-status')).length, before);
  assert.equal([...f.timers.values()].some(timer => timer.ms === 60000), false);
});

test('featured settings never echo an existing token and require saved activation before refresh', async t => {
  const f = fixture(); t.after(f.close);
  for (const enabled of [false, true]) {
    for (const tokenConfigured of [false, true]) {
      f.api.featuredSettingsModal({enabled, token_configured: tokenConfigured, token: 'TEST-ONLY-EXISTING-SECRET'});
      const form = f.win.document.getElementById('featured-movies-form');
      assert.equal(form.elements.token.type, 'password');
      assert.equal(form.elements.token.value, '');
      assert.equal(form.elements.token.autocomplete, 'new-password');
      assert.doesNotMatch(form.outerHTML, /TEST-ONLY-EXISTING-SECRET/);
      const refresh = form.querySelector('[data-action="featured-refresh"]');
      assert.equal(refresh.disabled, !enabled || !tokenConfigured);
      if (refresh.disabled) {
        form.elements.enabled.value = 'true';
        form.elements.token.value = 'TEST-ONLY-UNSAVED-TOKEN';
        refresh.click();
        assert.equal(f.calls.length, 0, 'unsaved changes must not enable refresh');
      }
    }
  }
});

test('failed featured settings save clears the typed token without echoing it into an error', async t => {
  const f = fixture({handler: url => {
    assert.equal(url, '/api/admin/featured-movies');
    throw new Error('Mocked save failure');
  }}); t.after(f.close);
  f.api.featuredSettingsModal({enabled: false, token_configured: false});
  const form = f.win.document.getElementById('featured-movies-form');
  form.elements.enabled.value = 'true';
  form.elements.token.value = 'TEST-ONLY-TYPED-TOKEN';
  form.dispatchEvent(new f.win.Event('submit', {bubbles: true, cancelable: true}));
  await tick();
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(f.calls[0].options.body), {enabled: true, token: 'TEST-ONLY-TYPED-TOKEN', clear_token: false});
  assert.equal(form.elements.token.value, '');
  assert.doesNotMatch(f.win.document.body.innerHTML, /TEST-ONLY-TYPED-TOKEN/);
  assert.match(form.querySelector('.form-error').textContent, /Mocked save failure/);
  assert.equal(form.querySelector('[data-action="featured-refresh"]').disabled, true);
});

test('successful featured settings save refreshes metadata without retaining either token', async t => {
  const f = fixture({handler: () => response({enabled: true, token_configured: true, token: 'TEST-ONLY-SERVER-TOKEN', movie_count: 0})}); t.after(f.close);
  f.api.featuredSettingsModal({enabled: false, token_configured: false});
  const oldForm = f.win.document.getElementById('featured-movies-form');
  oldForm.elements.enabled.value = 'true';
  oldForm.elements.token.value = 'TEST-ONLY-TYPED-TOKEN';
  oldForm.dispatchEvent(new f.win.Event('submit', {bubbles: true, cancelable: true}));
  await tick();
  const form = f.win.document.getElementById('featured-movies-form');
  assert.notEqual(form, oldForm);
  assert.equal(oldForm.elements.token.value, '');
  assert.equal(form.elements.token.value, '');
  assert.equal(form.querySelector('[data-action="featured-refresh"]').disabled, false);
  assert.doesNotMatch(f.win.document.body.innerHTML, /TEST-ONLY-(?:TYPED|SERVER)-TOKEN/);
  assert.equal(f.calls.length, 1);
});


test('media selection submits more than eight explicit libraries and retains their names', async t => {
  const ids=Array.from({length:12},(_,i)=>`root-${i}`);
  const f=fixture({handler:async(url,options)=>{
    assert.equal(url,'/api/admin/emby-library');
    const body=JSON.parse(options.body);
    assert.deepEqual(body.library_ids,ids);
    return response({base_url:'https://emby.example',token_configured:true,enabled:true,library_ids:ids});
  }});t.after(f.close);
  f.api.mediaIntegrationModal('emby',{base_url:'https://emby.example',token_configured:true,enabled:true,library_ids:ids},ids.map((id,i)=>({id,name:`Library ${i}`})));
  assert.equal(f.calls.length,0);
  const form=f.win.document.querySelector('#media-integration-form');
  form.dispatchEvent(new f.win.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
  assert.equal(f.calls.length,1);
  assert.equal(f.win.document.querySelectorAll('[name="library_id"]:checked').length,12);
  assert.match(f.win.document.querySelector('#emby-library-options').textContent,/Library 11/);
});

test('refreshing library choices preserves edits made while the read is pending', async t => {
  const pending=deferred();const f=fixture({handler:()=>pending.promise});t.after(f.close);
  f.api.mediaIntegrationModal('emby',{base_url:'https://emby.example',token_configured:true,enabled:false,library_ids:['root-a']},[{id:'root-a',name:'Alpha'},{id:'root-b',name:'Beta'}]);
  const run=f.api.loadEmbyLibraries(f.win.document.querySelector('[data-action="emby-libraries"]'));
  f.win.document.querySelector('[value="root-b"]').checked=true;
  pending.resolve(response({libraries:[{id:'root-b',name:'Beta'}]}));await run;
  assert.deepEqual([...f.win.document.querySelectorAll('[name="library_id"]:checked')].map(x=>x.value).sort(),['root-a','root-b']);
  assert.match(f.win.document.querySelector('#emby-library-count').textContent,/2/);
  assert.equal(f.calls.every(call=>call.options.method==='GET'),true);
});

test('media library order follows saved selection instead of Emby inventory order and persists moves', async t => {
  let saved;
  const f=fixture({handler:async(url,options)=>{
    assert.equal(url,'/api/admin/emby-library');
    saved=JSON.parse(options.body);
    return response({...saved,token_configured:true});
  }});t.after(f.close);
  const libraries=[{id:'a',name:'Alpha'},{id:'b',name:'Beta'},{id:'private',name:'Private'}];
  f.api.mediaIntegrationModal('emby',{base_url:'https://emby.example',token_configured:true,enabled:true,library_ids:['b','a']},libraries);
  const names=()=>[...f.win.document.querySelectorAll('.media-library-order-name')].map(x=>x.textContent);
  assert.deepEqual(names(),['Beta','Alpha']);
  assert.equal(f.win.document.querySelector('[data-library-id="b"][data-direction="up"]').disabled,true);
  assert.equal(f.win.document.querySelector('[data-library-id="a"][data-direction="down"]').disabled,true);
  f.win.document.querySelector('[data-library-id="a"][data-direction="up"]').click();
  assert.deepEqual(names(),['Alpha','Beta']);
  assert.equal(f.win.document.querySelector('[name="library_id"][value="private"]').checked,false);
  assert.equal(f.calls.length,0);
  const form=f.win.document.querySelector('#media-integration-form');
  form.dispatchEvent(new f.win.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
  assert.deepEqual(saved.library_ids,['a','b']);
  assert.deepEqual(names(),['Alpha','Beta']);
});

test('deselecting removes a library from public order and reselecting appends without changing other selections', t => {
  const f=fixture();t.after(f.close);
  f.api.mediaIntegrationModal('emby',{base_url:'https://emby.example',token_configured:true,enabled:true,library_ids:['c','a']},['a','b','c'].map(id=>({id,name:id.toUpperCase()})));
  const selected=f.win.document.querySelector('[name="library_id"][value="c"]');
  selected.checked=false;selected.dispatchEvent(new f.win.Event('change',{bubbles:true}));
  assert.deepEqual([...f.win.document.querySelectorAll('.media-library-order-name')].map(x=>x.textContent),['A']);
  selected.checked=true;selected.dispatchEvent(new f.win.Event('change',{bubbles:true}));
  assert.deepEqual([...f.win.document.querySelectorAll('.media-library-order-name')].map(x=>x.textContent),['A','C']);
  assert.equal(f.win.document.querySelector('[name="library_id"][value="b"]').checked,false);
  assert.match(f.win.document.querySelector('#emby-library-count').textContent,/2/);
  assert.equal(f.calls.length,0);
});

test('refreshing the inventory preserves moves and checkbox edits made during its request', async t => {
  const pending=deferred();const f=fixture({handler:()=>pending.promise});t.after(f.close);
  f.api.mediaIntegrationModal('emby',{base_url:'https://emby.example',token_configured:true,enabled:true,library_ids:['a','b']},['a','b','c'].map(id=>({id,name:id.toUpperCase()})));
  const run=f.api.loadEmbyLibraries(f.win.document.querySelector('[data-action="emby-libraries"]'));
  f.win.document.querySelector('[data-library-id="b"][data-direction="up"]').click();
  const extra=f.win.document.querySelector('[name="library_id"][value="c"]');
  extra.checked=true;extra.dispatchEvent(new f.win.Event('change',{bubbles:true}));
  pending.resolve(response({libraries:[{id:'c',name:'New C'},{id:'a',name:'New A'},{id:'b',name:'New B'},{id:'private',name:'Private'}]}));await run;
  assert.deepEqual([...f.win.document.querySelectorAll('.media-library-order-name')].map(x=>x.textContent),['New B','New A','New C']);
  assert.equal(f.win.document.querySelector('[name="library_id"][value="private"]').checked,false);
  assert.equal(f.calls.every(call=>call.options.method==='GET'),true);
});

test('custom admin entry submits username and existing password together', async t => {
  const f = fixture({route:'/admin', handler:(url, options) => {
    if(url === '/api/auth/status') return response({initialized:true,authenticated:false});
    assert.equal(url, '/api/auth/login');
    assert.deepEqual(JSON.parse(options.body), {username:'admin',password:'existing-test-password'});
    return response({error:{message:'用户名或密码不正确'}},401);
  }}); t.after(f.close);
  await f.api.start();
  const form = f.app.querySelector('#auth-form');
  assert.ok(form);
  assert.equal(form.elements.username.autocomplete,'username');
  assert.equal(form.elements.password.autocomplete,'current-password');
  form.elements.username.value='admin';
  form.elements.password.value='existing-test-password';
  form.dispatchEvent(new f.win.Event('submit',{bubbles:true,cancelable:true}));
  await tick(); await tick();
  assert.equal(f.calls.filter(c=>c.url==='/api/auth/login').length,1);
});

test('assistant logout on custom entry revokes session then shows login', async t => {
  const f=fixture({route:'/admin?logout=1',handler:(url,options)=>{
    if(url==='/api/auth/logout'){assert.equal(options.method,'POST');return response({ok:true});}
    assert.equal(url,'/api/auth/status');return response({initialized:true,authenticated:false});
  }});t.after(f.close);
  await f.api.start();
  assert.deepEqual(f.calls.map(c=>c.url),['/api/auth/logout','/api/auth/status']);
  assert.equal(f.win.location.pathname,'/admin');
  assert.equal(f.win.location.search,'');
  assert.ok(f.app.querySelector('#auth-form'));
});


test('unconfigured payments disable web checkout and block synthetic submissions', async t => {
  const f=fixture({route:'/plans',handler:url=>{assert.equal(url,'/api/public/catalog');return response(planCatalog());}});t.after(f.close);
  await f.api.start();f.app.querySelector('[data-product="own-trial"]').click();
  const form=f.win.document.querySelector('#purchase-form'),button=form.querySelector('[type="submit"]');
  assert.equal(button.disabled,true);
  assert.ok(form.textContent.includes('网页收款尚未配置，现在仅登记待付款订单。可以通过其它入口完成购买。'));
  button.click();form.dispatchEvent(new f.win.Event('submit',{bubbles:true,cancelable:true}));await tick();
  assert.equal(button.disabled,true);
  assert.deepEqual(f.calls.map(x=>x.url),['/api/public/catalog']);
});
