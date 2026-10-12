const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\nstart\(\);\s*$/,'');

function fixture(){
  const dom=new JSDOM('<div id="app"></div><div id="modal-root"></div><div id="toast-root"></div>',{url:'https://portal.example/admin',runScripts:'outside-only'});
  const w=dom.window,requests=[];
  w.MediaDockGuideEditor={panel:()=>'<form id="guide-form"><input name="title" value="教程标题"></form>'};
  w.fetch=async(url,options={})=>{
    const body=JSON.parse(options.body||'{}');requests.push({url,body});
    return {ok:true,json:async()=>({settings:{...w.settingsTest.state.overview.settings,...body}})};
  };
  w.eval(source+'\nwindow.settingsTest={state,settingsPage,selectSettingsCategory};');
  const api=w.settingsTest;
  api.state.overview={settings:{public_base_url:'https://portal.example',redemption_url:'https://user.example',payment_wechat_url:'',payment_alipay_url:''},connections:{}};
  w.document.querySelector('#app').innerHTML=api.settingsPage();
  return {w,api,requests,doc:w.document,close:()=>w.close(),tab:id=>w.document.querySelector(`[data-category="${id}"]`),panel:id=>w.document.querySelector(`[data-settings-category="${id}"]`)};
}
const settled=()=>new Promise(resolve=>setImmediate(resolve));

test('settings categories expose exactly one linked accessible panel and retain drafts on switching',t=>{
  const f=fixture();t.after(f.close);
  assert.equal(f.doc.querySelectorAll('[role=tab]').length,6);
  assert.equal(f.doc.querySelectorAll('[role=tabpanel]:not([hidden])').length,1);
  for(const tab of f.doc.querySelectorAll('[role=tab]')){
    const panel=f.doc.getElementById(tab.getAttribute('aria-controls'));
    assert.ok(panel);assert.equal(panel.getAttribute('aria-labelledby'),tab.id);
  }
  const input=f.doc.querySelector('[name=redemption_url]');input.value='https://draft.example/';
  f.tab('guide').click();assert.equal(f.panel('guide').hidden,false);assert.equal(f.panel('site').hidden,true);
  f.tab('site').click();assert.equal(input.value,'https://draft.example/');assert.equal(f.doc.querySelector('[name=redemption_url]'),input);
  assert.equal(f.api.state.settingsCategory,'site');
  f.api.selectSettingsCategory('unrecognized');assert.equal(f.api.state.settingsCategory,'site');
});

test('settings category keyboard navigation wraps and supports Home and End',t=>{
  const f=fixture();t.after(f.close);
  function key(name){f.doc.activeElement.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:name,bubbles:true}));}
  f.tab('site').focus();key('ArrowLeft');assert.equal(f.doc.activeElement,f.tab('network'));
  key('ArrowRight');assert.equal(f.doc.activeElement,f.tab('site'));
  key('End');assert.equal(f.doc.activeElement,f.tab('network'));
  key('Home');assert.equal(f.doc.activeElement,f.tab('site'));
  key('ArrowRight');assert.equal(f.doc.activeElement,f.tab('payment'));
  assert.equal(f.doc.querySelectorAll('[role=tab][tabindex="0"]').length,1);
  assert.equal(f.tab('payment').getAttribute('aria-selected'),'true');
});

test('saving payment only sends payment fields and preserves drafts in other categories',async t=>{
  const f=fixture();t.after(f.close);
  const site=f.doc.querySelector('[name=redemption_url]');site.value='https://unsaved.example/';
  const secret=f.doc.querySelector('[name=nextemby_token]');secret.value='draft-secret';
  f.tab('payment').click();const form=f.doc.querySelector('#settings-payment-form');
  form.elements.payment_wechat_url.value='/static/payment.png';
  form.dispatchEvent(new f.w.Event('submit',{bubbles:true,cancelable:true}));await settled();
  assert.equal(f.requests.length,1);assert.equal(f.requests[0].url,'/api/admin/settings');
  assert.deepEqual(f.requests[0].body,{payment_wechat_url:'/static/payment.png',payment_alipay_url:''});
  assert.equal(site.value,'https://unsaved.example/');assert.equal(secret.value,'draft-secret');
  assert.equal(form.isConnected,true);assert.equal(f.api.state.settingsCategory,'payment');
  assert.equal(form.querySelector('.form-error').textContent,'');
});

test('saving service settings leaves an existing secret untouched when empty and clears a submitted draft',async t=>{
  const f=fixture();t.after(f.close);f.tab('services').click();const form=f.doc.querySelector('#settings-services-form');
  form.dispatchEvent(new f.w.Event('submit',{bubbles:true,cancelable:true}));await settled();
  assert.deepEqual(f.requests[0].body,{});
  form.elements.nextemby_token.value='replacement-test-token';
  form.dispatchEvent(new f.w.Event('submit',{bubbles:true,cancelable:true}));await settled();
  assert.deepEqual(f.requests[1].body,{nextemby_token:'replacement-test-token'});
  assert.equal(form.elements.nextemby_token.value,'');assert.equal(f.api.state.settingsCategory,'services');
});
