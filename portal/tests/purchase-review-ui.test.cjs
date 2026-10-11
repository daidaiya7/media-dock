'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const appSource = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8').replace(/\nstart\(\);\s*$/, '');
const deliverySource = fs.readFileSync(path.join(__dirname, '../static/delivery.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => {await tick(); await tick();};
const response = (value, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => value});
const deferred = () => {let resolve; const promise = new Promise(done => {resolve = done;}); return {promise, resolve};};
const product = {id:'free_monthly',name:'测试月卡',duration_days:30,requires_own_115:true,listed_price_cents:4000};
const catalog = (overrides = {}) => ({products:[product],redemption_url:'https://members.example/',payment_configured:true,payment_qr:{wechat:'/static/fixture-wechat.png',alipay:'/static/fixture-alipay.png'},...overrides});
const requestId = 'XR-test-private-request';
const requestToken = 'private-review-status-token';
const review = (overrides = {}) => ({request_id:requestId,xianyu_order_id:'123456789012345678',product_id:product.id,product,buyer_note:'测试买家',status:'pending_review',created_at:'2026-10-11T12:00:00Z',...overrides});

function fixture({route='/plans',handler} = {}) {
  const dom = new JSDOM('<div id="app"></div><div id="modal-root"></div><div id="toast-root"></div>', {url:`https://mg.example${route}`,runScripts:'outside-only',pretendToBeVisual:true});
  const win = dom.window, calls = [], timers = new Map(); let sequence = 0;
  win.setTimeout = (fn, ms) => {timers.set(++sequence,{fn,ms}); return sequence;};
  win.clearTimeout = id => timers.delete(id);
  win.MediaDockCarousel = {start(){},stop(){}};
  win.fetch = async (url, options) => {
    calls.push({url,options});
    if(handler) return handler(url,options);
    if(url === '/api/public/catalog') return response(catalog());
    if(url === '/api/public/featured-movies') return response({movies:[]});
    throw new Error(`Unexpected mocked endpoint: ${url}`);
  };
  win.eval(deliverySource+'\n'+appSource+'\nwindow.purchaseTest={start,state,purchaseModal,renderPurchaseReview,adminOrderEntries,orderSourceCounts,filteredOrders,renderAdminShell,selectOrderSource,deliveryOrders,selectDelivery,stopShopNetworkMonitor};');
  const api = win.purchaseTest;
  api.state.catalog = catalog();
  return {
    win,api,calls,app:win.document.getElementById('app'),
    async go(route) {win.history.pushState({},'',route); return api.start();},
    submit(form) {form.dispatchEvent(new win.Event('submit',{bubbles:true,cancelable:true}));},
    admin(data = {}) {api.state.overview={products:[product],orders:[],purchase_reviews:[],settings:{xianyu_account_id:'test-seller'},connections:{},...data};api.state.tab='orders';api.state.auth={authenticated:true};api.renderAdminShell();},
    close() {api.stopShopNetworkMonitor();dom.window.close();}
  };
}
function noPurchaseOrCardDisclosure(f) {
  assert.equal(f.app.querySelector('.payment-qr'),null);
  assert.equal(f.app.querySelector('#paid-notice-form'),null);
  assert.equal(f.app.querySelector('a[href^="/claim#"]'),null);
  assert.equal(f.app.querySelector('a[href*="forged-claim.example"]'),null);
  assert.equal(f.app.querySelector('[data-action="issue"]'),null);
  assert.doesNotMatch(f.app.textContent,/FORGED-CARD|FORGED-CLAIM|FORGED-BUYER|待付款|应付金额|付款说明|登记订单不代表已经付款/);
}

test('purchase modal links to Xianyu in both modes while retaining the selected package',t=>{
  const f=fixture();t.after(f.close);
  f.api.purchaseModal(product.id);
  const doc=f.win.document;
  assert.ok(doc.querySelector('.purchase-modal #purchase-form'));
  const link=doc.querySelector('.purchase-modal a[href^="https://market.example.com/"]');
  assert.ok(link);assert.equal(link.target,'_blank');assert.match(link.rel,/noopener/);
  assert.ok(doc.querySelector('[data-action="purchase-mode"][data-mode="web"]'));
  doc.querySelector('[data-action="purchase-mode"][data-mode="xianyu"]').click();
  const form=doc.querySelector('#xianyu-purchase-form');
  assert.ok(form);assert.equal(form.dataset.product,product.id);
  assert.equal(doc.querySelector('#purchase-form'),null);
  assert.ok(form.elements.xianyu_order_id.required);
  assert.ok(doc.querySelector('.purchase-modal a[href^="https://market.example.com/"]'));
  assert.match(doc.querySelector('.order-summary').textContent,/月卡/);
  assert.match(doc.querySelector('.order-summary').textContent,/40/);
  assert.equal(f.calls.length,0);
});

test('web purchase still posts only a web registration and retains its own private status token',async t=>{
  let posted;
  const f=fixture({handler:(url,options)=>{
    if(url==='/api/public/orders') {posted=JSON.parse(options.body);return response({order_id:'WEB-TEST',access_token:'WEB-PRIVATE',product_id:product.id});}
    assert.equal(url,'/api/public/orders/WEB-TEST');
    assert.equal(options.headers['X-Order-Token'],'WEB-PRIVATE');
    return response({product_id:product.id,status:'pending_payment'});
  }});t.after(f.close);
  f.api.purchaseModal(product.id);
  const form=f.win.document.querySelector('#purchase-form');form.elements.contact_note.value='网页测试备注';
  f.submit(form);await settle();
  assert.deepEqual(posted,{product_id:product.id,contact_note:'网页测试备注'});
  assert.equal(f.win.location.search,'?order=WEB-TEST');
  assert.equal(f.win.sessionStorage.getItem('mediadock-order-WEB-TEST'),'WEB-PRIVATE');
  assert.match(f.app.textContent,/订单已登记/);assert.ok(f.app.querySelector('.payment-qr'));
  assert.equal(f.calls.some(call=>call.url.includes('purchase-reviews')),false);
});

test('Xianyu submission creates only a review and shows no web payment or card delivery controls',async t=>{
  let posted;
  const f=fixture({handler:(url,options)=>{
    if(url==='/api/public/purchase-reviews') {
      posted=JSON.parse(options.body);
      return response({...review(),access_token:requestToken,claim_token:'FORGED-CLAIM',card:'FORGED-CARD'});
    }
    assert.equal(url,`/api/public/purchase-reviews/${requestId}`);
    assert.equal(options.headers['X-Order-Token'],requestToken);
    return response({...review(),claim_token:'FORGED-CLAIM',card:'FORGED-CARD',claim_url:'https://forged-claim.example/secret',buyer_id:'FORGED-BUYER'});
  }});t.after(f.close);
  f.api.purchaseModal(product.id,'xianyu');
  const form=f.win.document.querySelector('#xianyu-purchase-form');
  form.elements.xianyu_order_id.value='123456789012345678';form.elements.buyer_note.value='测试买家';
  f.submit(form);await settle();
  assert.deepEqual(posted,{product_id:product.id,xianyu_order_id:'123456789012345678',buyer_note:'测试买家'});
  assert.equal(f.win.location.pathname,'/');assert.equal(f.win.location.search,`?review=${requestId}`);
  assert.match(f.app.textContent,/审核/);assert.match(f.app.textContent,/闲鱼/);
  noPurchaseOrCardDisclosure(f);
  assert.deepEqual(f.calls.map(call=>call.url),['/api/public/purchase-reviews',`/api/public/purchase-reviews/${requestId}`]);
  assert.equal(f.calls.some(call=>/\/api\/(?:admin|public\/orders)|\/issue/.test(call.url)),false);
});

test('review token persists across direct query navigation without changing web credentials',async t=>{
  let reads=0;
  const f=fixture({route:`/?review=${requestId}`,handler:(url,options)=>{
    if(url==='/api/public/catalog')return response(catalog());
    assert.equal(url,`/api/public/purchase-reviews/${requestId}`);reads++;
    assert.equal(options.headers['X-Order-Token'],requestToken);
    return response(review());
  }});t.after(f.close);
  f.win.sessionStorage.setItem('mediadock-order-WEB-existing','WEB-existing-secret');
  await f.api.renderPurchaseReview(requestId,{...review(),access_token:requestToken});
  assert.equal(f.win.sessionStorage.getItem('mediadock-order-WEB-existing'),'WEB-existing-secret');
  await f.go(`/plans`);await f.go(`/?review=${requestId}`);
  assert.equal(reads,2);assert.match(f.app.textContent,/审核/);noPurchaseOrCardDisclosure(f);
});

test('approved review directs delivery back to Xianyu and ignores forged card, buyer and claim fields',async t=>{
  const f=fixture({route:`/?review=${requestId}`,handler:()=>response({...review({status:'approved'}),order_id:'XY-private-order',card:'FORGED-CARD',claim_token:'FORGED-CLAIM',claim_url:'https://forged-claim.example/secret',buyer_id:'FORGED-BUYER'})});t.after(f.close);
  await f.api.renderPurchaseReview(requestId,{access_token:requestToken});
  assert.match(f.app.textContent,/闲鱼/);assert.match(f.app.textContent,/通过|已核对/);
  noPurchaseOrCardDisclosure(f);
  assert.doesNotMatch(f.app.textContent,/XY-private-order/);
});

test('failed public review authentication does not reveal another order or offer issuance',async t=>{
  const f=fixture({route:`/?review=${requestId}`,handler:()=>response({error:{message:'申请不存在或查询凭证不正确'}},404)});t.after(f.close);
  await f.api.renderPurchaseReview(requestId,{access_token:requestToken});
  await f.api.renderPurchaseReview(requestId);
  assert.match(f.app.textContent,/凭证|查询/);noPurchaseOrCardDisclosure(f);
  assert.equal(f.calls.some(call=>call.url.includes('/api/public/orders')),false);
});

test('a late review result cannot overwrite a newer plans route',async t=>{
  const pending=deferred();
  const f=fixture({route:`/?review=${requestId}`,handler:url=>url==='/api/public/catalog'?response(catalog()):pending.promise});t.after(f.close);
  const old=f.api.renderPurchaseReview(requestId,{access_token:requestToken});
  await f.go('/plans');const current=f.app.innerHTML;
  pending.resolve(response(review({status:'approved'})));await old;
  assert.equal(f.app.innerHTML,current);assert.ok(f.app.querySelector('#plans'));
  assert.equal(f.win.document.title,'观影套餐 · 影视服 MediaDock');
});

test('review rows join the Xianyu order group, linked approvals deduplicate and never enter delivery queue',t=>{
  const f=fixture({route:'/admin'});t.after(f.close);
  f.admin({orders:[{order_id:'WEB-1',source:'web',product_id:product.id,status:'pending_review',delivery_mode:'claim'},{order_id:'XY-linked',source:'xianyu',product_id:product.id,status:'eligible',delivery_mode:'direct'}],purchase_reviews:[review(),review({request_id:'XR-approved',status:'approved',order_id:'XY-linked'}),review({request_id:'XR-rejected',status:'rejected'})]});
  assert.equal(f.api.adminOrderEntries().length,4);
  assert.equal(f.api.orderSourceCounts().xianyu,3);
  f.api.selectOrderSource('xianyu');assert.equal(f.api.filteredOrders().length,3);
  assert.ok(f.win.document.querySelector(`[data-action="purchase-review-detail"][data-id="${requestId}"]`));
  assert.equal(f.win.document.querySelector('[data-action="purchase-review-detail"][data-id="XR-approved"]'),null);
  assert.equal(f.api.deliveryOrders().length,2);
  f.api.selectDelivery('delivery-channel','xianyu');
  assert.equal(f.api.deliveryOrders().length,1);assert.equal(f.api.deliveryOrders()[0].order_id,'XY-linked');
  assert.equal(f.win.document.querySelector('[data-action="purchase-review-detail"]'),null);
  assert.equal(f.calls.length,0);
});

test('review notes remain escaped in order rows and admin approval requires explicit verification fields',t=>{
  const f=fixture({route:'/admin'});t.after(f.close);
  const malicious='<img src=x onerror=alert(1)>';
  f.admin({purchase_reviews:[review({buyer_note:malicious})]});
  assert.equal(f.win.document.querySelector('#order-table-content img'),null);
  const button=f.win.document.querySelector(`[data-action="purchase-review-detail"][data-id="${requestId}"]`);assert.ok(button);button.click();
  const doc=f.win.document,dialog=doc.querySelector('[role="dialog"]');
  assert.equal(dialog.querySelector('img'),null);assert.match(dialog.textContent,/<img src=x onerror=alert\(1\)>/);
  const approve=doc.querySelector('#purchase-review-approve-form'),reject=doc.querySelector('#purchase-review-reject-form');
  assert.ok(approve);assert.ok(approve.elements.buyer_id.required);assert.ok(approve.elements.evidence_note.required);
  assert.ok(approve.querySelector('input[type="checkbox"][required]'));
  assert.ok(reject);assert.ok(reject.elements.reason.required);
  assert.equal(dialog.querySelector('[data-action="issue"]'),null);
  assert.equal(dialog.querySelector('[data-action="make-claim"]'),null);
  assert.equal(f.calls.length,0);
});

test('review query requires its private credential and refresh reuses it without writing an order',async t=>{
  const f=fixture({route:`/?review=${requestId}`,handler:(url,options)=>{
    assert.equal(url,`/api/public/purchase-reviews/${requestId}`);
    assert.equal(options.method,'GET');assert.equal(options.headers['X-Order-Token'],requestToken);
    return response(review());
  }});t.after(f.close);
  await f.api.renderPurchaseReview(requestId);
  assert.equal(f.calls.length,0);
  const form=f.win.document.querySelector('#purchase-review-access-form');assert.ok(form);
  form.elements.token.value=`  ${requestToken}  `;f.submit(form);await settle();
  assert.equal(f.win.document.querySelector('#purchase-review-access-form'),null);
  const refresh=f.app.querySelector('[data-action="refresh-purchase-review"]');assert.ok(refresh);refresh.click();await settle();
  assert.equal(f.calls.length,2);assert.match(f.app.textContent,/待审核/);noPurchaseOrCardDisclosure(f);
});

test('admin verification checkbox blocks approval and approved review opens an existing order without issuing',async t=>{
  let posted;
  const original={order_id:'XY-existing',source:'xianyu',product_id:product.id,status:'eligible',delivery_mode:'direct',product};
  const overview={products:[product],orders:[original],purchase_reviews:[review({status:'approved',order_id:original.order_id})],settings:{},connections:{}};
  const f=fixture({route:'/admin',handler:(url,options)=>{
    if(url===`/api/admin/purchase-reviews/${requestId}/approve`) {posted=JSON.parse(options.body);return response({order_id:original.order_id});}
    assert.equal(url,'/api/admin/overview');return response(overview);
  }});t.after(f.close);
  f.admin({purchase_reviews:[review()]});f.win.document.querySelector('[data-action="purchase-review-detail"]').click();
  const form=f.win.document.querySelector('#purchase-review-approve-form');
  form.elements.buyer_id.value='  verified-buyer-123  ';form.elements.evidence_note.value='已核对  月卡付款 40 元';
  f.submit(form);await settle();
  assert.equal(f.calls.length,0);assert.match(form.querySelector('.form-error').textContent,/勾选确认/);
  form.elements.verified.checked=true;f.submit(form);await settle();
  assert.deepEqual(posted,{buyer_id:'verified-buyer-123',evidence_note:'已核对 月卡付款 40 元'});
  assert.equal(f.win.document.querySelector('#purchase-review-approve-form'),null);
  const next=f.win.document.querySelector('[data-action="issue"]');assert.ok(next);assert.equal(next.dataset.id,original.order_id);
  assert.deepEqual(f.calls.map(call=>call.url),[`/api/admin/purchase-reviews/${requestId}/approve`,'/api/admin/overview']);
});

test('rejecting a review records only a review decision and removes further approval controls',async t=>{
  let posted;
  const reason='套餐不符，请在闲鱼核对';
  const overview={products:[product],orders:[],purchase_reviews:[review({status:'rejected',review_note:reason})],settings:{},connections:{}};
  const f=fixture({route:'/admin',handler:(url,options)=>{
    if(url===`/api/admin/purchase-reviews/${requestId}/reject`) {posted=JSON.parse(options.body);return response({status:'rejected'});}
    assert.equal(url,'/api/admin/overview');return response(overview);
  }});t.after(f.close);
  f.admin({purchase_reviews:[review()]});f.win.document.querySelector('[data-action="purchase-review-detail"]').click();
  const form=f.win.document.querySelector('#purchase-review-reject-form');form.elements.reason.value=reason;f.submit(form);await settle();
  assert.deepEqual(posted,{reason});
  assert.equal(f.win.document.querySelector('#purchase-review-approve-form'),null);
  assert.equal(f.win.document.querySelector('[data-action="issue"]'),null);
  assert.equal(f.api.state.overview.orders.length,0);
  assert.match(f.win.document.querySelector('[role="dialog"]').textContent,/未通过核对/);
  assert.deepEqual(f.calls.map(call=>call.url),[`/api/admin/purchase-reviews/${requestId}/reject`,'/api/admin/overview']);
});
