const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\nstart\(\);\s*$/,'');
const delivery=fs.readFileSync(path.join(__dirname,'../static/delivery.js'),'utf8');
function fixture(){
 const dom=new JSDOM('<div id="app"></div><div id="modal-root"></div><div id="toast-root"></div>',{url:'https://portal.example/',runScripts:'outside-only'});
 dom.window.eval(delivery+source+';window.manualTest={state,renderPublicOrder,orderDetail,deliveryOrders,deliveryActionLabel};');
 const api=dom.window.manualTest;api.state.catalog={products:[],payment_configured:true};
 return {w:dom.window,api,close:()=>dom.window.close()};
}
test('manual public statuses explain external delivery and never expose stale claim links',async t=>{
 const f=fixture();t.after(f.close);
 for(const status of ['manual_issued','manual_delivered']){
  f.w.fetch=async()=>({ok:true,json:async()=>({status,claim_url:'/claim#stale'})});
  await f.api.renderPublicOrder('ORDER-TEST',{access_token:'TEST-TOKEN'});
  const body=f.w.document.querySelector('#app');
  assert.match(body.textContent,/其他渠道生成/);
  assert.doesNotMatch(body.textContent,/登记订单不代表已经付款/);
  assert.equal(body.querySelector('a[href^="/claim#"]'),null);
  assert.equal(body.querySelector('#paid-notice-form'),null);
 }
});
test('eligible admin order exposes manual actions and marked orders cannot issue',t=>{
 const f=fixture();t.after(f.close);
 const order={order_id:'ORDER-TEST',status:'eligible',delivery_mode:'direct',source:'manual'};
 f.api.state.overview={orders:[order],products:[]};
 f.api.orderDetail('ORDER-TEST');
 assert.equal(f.w.document.querySelectorAll('[data-action="manual-mark"]').length,2);
 order.status='manual_delivered';order.manual_note='<img src=x onerror=alert(1)>';
 f.api.orderDetail('ORDER-TEST');
 assert.equal(f.w.document.querySelector('[data-action="issue"]'),null);
 assert.ok(f.w.document.querySelector('[data-action="manual-unmark"]'));
 assert.equal(f.w.document.querySelector('#modal-root img'),null);
});
test('delivery issued filter includes external manual handling',t=>{
 const f=fixture();t.after(f.close);
 f.api.state.overview={orders:[{order_id:'1',status:'manual_issued'},{order_id:'2',status:'manual_delivered'},{order_id:'3',status:'eligible'}],products:[]};
 assert.deepEqual(Array.from(f.api.deliveryOrders({stage:'issued',search:''}),o=>o.order_id),['1','2']);
 assert.equal(f.api.deliveryActionLabel({status:'manual_delivered'}),'查看手动处理');
});
