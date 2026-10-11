'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const app=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\nstart\(\);\s*$/,'');
const sourceId='a'.repeat(64), otherId='b'.repeat(64);
const groups=[{id:'cdn',name:'CDN',nodes:[{id:'cdn',name:'CDN 线路'}]},{id:'origin',name:'源站监测',nodes:[{id:'origin',name:'源站'}]},{id:'relays',name:'线路',nodes:[{id:'relay-a',name:'示例线路 1'},{id:'relay-b',name:'示例线路 2'},{id:'relay-c',name:'示例线路 3'}]}];
const clone=value=>JSON.parse(JSON.stringify(value));
function fixture({legacy=false,probe=false}={}) {
  const dom=new JSDOM('<div id="app"></div><div id="modal-root"></div><div id="toast-root"></div>',{url:'https://mg.example/admin',runScripts:'outside-only'});
  const win=dom.window,calls=[];
  let config={version:2,revision:'c'.repeat(64),nodes:groups.flatMap(group=>group.nodes).map(node=>legacy?{...node,url:node.id==='cdn'?'https://private.example.net/health':''}:{...node,method:probe&&node.id==='origin'?'mmwx':'http',target:probe&&node.id==='origin'?sourceId:node.id==='cdn'?'https://private.example.net/health':''}),status:{checked_at:new Date().toISOString(),groups:groups.map(group=>({...group,nodes:group.nodes.map(node=>({...node,status:'unconfigured',latency_ms:null,checked_at:null}))}))}};
  if(!legacy) config.groups=groups.map(group=>({...group,nodes:config.nodes.filter(node=>group.nodes.some(item=>item.id===node.id))}));
  let source={provider:'mmwx',url:'https://mmw.example.net',configured:true,checked_at:new Date().toISOString(),status:'ready',nodes:[{id:sourceId,name:'PRIVATE 203.0.113.88 <img src=x>',online:true}]};
  let fail=false,conflict=false;
  win.fetch=async(url,opts={})=>{
    const body=opts.body?JSON.parse(opts.body):undefined;calls.push({url,method:opts.method||'GET',body});
    if(conflict && url==='/api/admin/network-monitor' && body) return {ok:false,status:409,json:async()=>({error:{code:'monitor_config_conflict',message:'configuration changed'}})};
    if(fail) return {ok:false,status:503,json:async()=>({error:{message:'sensitive internal error that must not render'}})};
    let result;
    if(url==='/api/admin/network-monitor') {if(body)config={...config,version:2,revision:'d'.repeat(64),groups:clone(body.groups),nodes:clone(body.groups.flatMap(group=>group.nodes))};result=config;}
    else if(url==='/api/admin/network-probe') {if(body)source={...source,url:body.url,configured:Boolean(body.url),status:body.url?'ready':'unconfigured'};result=source;}
    else if(url==='/api/admin/network-probe/refresh') result=source;
    else throw new Error('Unexpected endpoint');
    return {ok:true,status:200,json:async()=>clone(result)};
  };
  win.eval(app+'\nwindow.fixtureApi={state,shopNetworkMonitor,networkStatusData,shopNetworkStatusMarkup,shopNetworkSection,renderShopNetworkStatus,networkMonitorSettingsMarkup,networkProbeSettingsMarkup,networkProbe,loadNetworkMonitorSettings,loadNetworkProbeSettings,updateNetworkProbeResult,changeNetworkMonitorMethod,saveNetworkMonitorSettings,refreshNetworkProbe,saveNetworkProbeSettings,validNetworkDisplayName,networkMonitorEditor,networkMonitorEditorAction,moveNetworkMonitorNode,captureNetworkMonitorDraft};');
  const api=win.fixtureApi;api.state.tab='settings';api.state.auth={authenticated:true};
  win.document.getElementById('app').innerHTML='<div id="network-probe-settings"></div><div id="network-monitor-settings"></div>';
  win.document.getElementById('network-probe-settings').innerHTML=api.networkProbeSettingsMarkup(source);
  api.updateNetworkProbeResult(source);
  win.document.getElementById('network-monitor-settings').innerHTML=api.networkMonitorSettingsMarkup(config);
  return {win,api,calls,config,source,setSource:value=>{source=value;},fail:()=>{fail=true;},conflict:()=>{conflict=true;},close:()=>win.close(),form:()=>win.document.getElementById('network-monitor-form')};
}

test('legacy HTTP targets retained and all default public aliases editable',t=>{
  const f=fixture({legacy:true});t.after(f.close);
  assert.equal(f.form().elements.cdn.value,'https://private.example.net/health');
  assert.equal(f.form().elements['method-cdn'].value,'http');
  assert.equal(f.form().elements['name-cdn'].value,'CDN 线路');
  assert.equal(f.form().querySelectorAll('[name^="name-"]').length,3);
  assert.equal(f.calls.length,0);
});
test('method switches preserve direct target and selected probe independently',t=>{
  const f=fixture();t.after(f.close);const form=f.form(),method=form.elements['method-origin'];
  form.elements.origin.value='https://origin.example.net/';
  method.value='mmwx';f.api.changeNetworkMonitorMethod(method);
  assert.equal(form.elements.origin.disabled,true);assert.equal(form.elements['probe-origin'].disabled,false);
  form.elements['probe-origin'].value=sourceId;
  method.value='http';f.api.changeNetworkMonitorMethod(method);
  assert.equal(form.elements.origin.value,'https://origin.example.net/');
  method.value='icmp';f.api.changeNetworkMonitorMethod(method);
  assert.equal(form.elements.origin.value,'https://origin.example.net/');
  method.value='mmwx';f.api.changeNetworkMonitorMethod(method);
  assert.equal(form.elements['probe-origin'].value,sourceId);assert.equal(form.elements['name-origin'].value,'源站');
});
test('refresh list preserves unsaved aliases and targets plus missing selected probe',async t=>{
  const f=fixture({probe:true});t.after(f.close);const form=f.form();
  form.elements['name-origin'].value='家庭源站';form.elements.cdn.value='https://changed.example.net/health';
  f.setSource({...f.source,nodes:[{id:otherId,name:'different private host',online:false}]});
  await f.api.refreshNetworkProbe(f.win.document.querySelector('[data-action="network-probe-refresh"]'));
  assert.equal(f.form(),form);assert.equal(form.elements['name-origin'].value,'家庭源站');assert.equal(form.elements.cdn.value,'https://changed.example.net/health');
  assert.equal(form.elements['probe-origin'].value,sourceId);assert.match(form.elements['probe-origin'].selectedOptions[0].textContent,/不在当前列表/);
  assert.deepEqual(f.calls.map(call=>call.url),['/api/admin/network-probe/refresh']);
  assert.equal(f.calls[0].method,'POST');
});
test('save node settings submits aliases and opaque probe IDs only for probe method',async t=>{
  const f=fixture({probe:true});t.after(f.close);const form=f.form();
  form.elements['name-origin'].value='影视服源站';
  await f.api.saveNetworkMonitorSettings(form);
  const saved=f.calls.find(call=>call.method==='POST').body.groups.flatMap(group=>group.nodes).find(node=>node.id==='origin');
  assert.deepEqual(saved,{id:'origin',name:'影视服源站',method:'mmwx',target:sourceId});
  assert.doesNotMatch(JSON.stringify(f.calls),/PRIVATE|203\.0\.113/);
});
test('probe settings save and disable preserve selections, trim trailing slash',async t=>{
  const f=fixture({probe:true});t.after(f.close);const form=f.win.document.getElementById('network-probe-form');
  form.elements.url.value=' https://mmw.other.example/ ';
  await f.api.saveNetworkProbeSettings(form);
  assert.equal(f.calls[0].body.url,'https://mmw.other.example');assert.equal(f.form().elements['probe-origin'].value,sourceId);
  form.elements.url.value='';await f.api.saveNetworkProbeSettings(form);
  assert.equal(f.calls[1].body.url,'');assert.equal(f.form().elements['probe-origin'].value,sourceId);
  assert.equal(f.win.document.querySelector('[data-action="network-probe-refresh"]').disabled,true);
});
test('public aliases never copy raw probe labels and escape safe alias punctuation',t=>{
  const f=fixture({probe:true});t.after(f.close);
  const status=clone(f.config.status);status.groups[1].nodes[0]={id:'origin',name:'源站 & "甲"',basis:'mmwx',status:'healthy',latency_ms:18,checked_at:new Date().toISOString(),target:sourceId,raw_name:'private.example.net',ip:'203.0.113.4'};
  f.api.shopNetworkMonitor.data=f.api.networkStatusData(status);
  const markup=f.api.shopNetworkStatusMarkup();
  assert.match(markup,/源站 &amp; &quot;甲&quot;/);assert.match(markup,/心跳正常/);assert.match(markup,/响应 18 ms/);
  assert.doesNotMatch(markup,/private\.example|203\.0\.113|aaaaaa/);
  assert.equal(f.api.shopNetworkMonitor.data.groups[1].nodes[0].latencyMs,18);
});
test('unsafe aliases fall back in the public monitoring cards',t=>{
  const f=fixture();t.after(f.close);const status=clone(f.config.status);
  status.groups[1].nodes[0].name='<img src=x onerror=alert(1)>';
  status.groups[2].nodes[0].name='香港线路甲';status.groups[2].nodes[1].name='private.example.net';
  f.api.shopNetworkMonitor.data=f.api.networkStatusData(status);
  f.win.document.getElementById('app').innerHTML=f.api.shopNetworkSection();f.api.renderShopNetworkStatus();
  assert.match(f.win.document.getElementById('network-status').textContent,/香港线路甲/);
  assert.match(f.win.document.getElementById('network-status').textContent,/监测节点/);
  assert.equal(f.win.document.querySelectorAll('img').length,0);
  assert.doesNotMatch(f.win.document.body.innerHTML,/private\.example\.net/);
});
test('probe online expires to unknown after 180 seconds and never shows playback claims',t=>{
  const f=fixture();t.after(f.close);const status=clone(f.config.status),original=f.win.Date.now;
  const now=Date.now();status.groups[1].nodes[0]={id:'origin',name:'源站甲',basis:'mmwx',status:'healthy',checked_at:new Date(now-179000).toISOString()};
  f.win.Date.now=()=>now;f.api.shopNetworkMonitor.data=f.api.networkStatusData(status);
  assert.match(f.api.shopNetworkStatusMarkup(),/心跳正常/);
  f.win.Date.now=()=>now+2000;assert.match(f.api.shopNetworkStatusMarkup(),/心跳未知/);assert.doesNotMatch(f.api.shopNetworkStatusMarkup(),/播放正常|播放可用/);
  f.win.Date.now=original;
});
test('invalid alias and probe URL fail without requests',async t=>{
  const f=fixture();t.after(f.close);
  for(const alias of ['','192.168.1.1','private.example.net','https://example.net','a'.repeat(41),'不可见\u200b字符','example．net','地址：例子','名称[甲]']) {
    f.form().elements['name-origin'].value=alias;await f.api.saveNetworkMonitorSettings(f.form());
    assert.match(f.form().querySelector('.form-error').textContent,/标题/);
  }
  const form=f.win.document.getElementById('network-probe-form');
  for(const url of ['http://example.net','https://example.net/path','https://u:p@example.net','https://example.net?q=x']) {
    form.elements.url.value=url;await f.api.saveNetworkProbeSettings(form);assert.match(form.querySelector('.form-error').textContent,/HTTPS 主地址/);
  }
  assert.equal(f.calls.length,0);
});
test('probe list renders untrusted admin label as text and error keeps selection and draft',async t=>{
  const f=fixture({probe:true});t.after(f.close);
  assert.equal(f.win.document.querySelectorAll('img').length,0);
  assert.match(f.form().elements['probe-origin'].selectedOptions[0].textContent,/<img src=x>/);
  f.form().elements['name-origin'].value='未保存名称';f.fail();
  await f.api.refreshNetworkProbe(f.win.document.querySelector('[data-action="network-probe-refresh"]'));
  assert.equal(f.form().elements['probe-origin'].value,sourceId);assert.equal(f.form().elements['name-origin'].value,'未保存名称');
  assert.doesNotMatch(f.win.document.body.innerHTML,/sensitive internal error/);
  assert.match(f.win.document.querySelector('#network-probe-form .form-error').textContent,/未能刷新/);
});

test('shared public JavaScript contains no actual probe service address',()=>{
  assert.doesNotMatch(app,/monitor\.example\.com/i);
  assert.match(app,/https:\/\/probe\.example\.com/);
});


test('headings themselves are editable without duplicate fixed node names',t=>{
  const f=fixture();t.after(f.close);
  assert.equal(f.form().querySelectorAll('.network-settings-node-heading h3').length,0);
  assert.equal(f.form().querySelectorAll('.network-settings-node-heading input[aria-label="监测项目标题"]').length,5);
  assert.equal(f.form().querySelectorAll('.network-settings-group-heading input[aria-label="分组标题"]').length,3);
  assert.doesNotMatch(f.form().textContent,/公开显示名称/);
});
test('add and remove are draft-only, undo retains later edits and private targets',t=>{
  const f=fixture({probe:true});t.after(f.close);let form=f.form();
  form.elements['name-origin'].value='自己的源站';form.elements['group-name-origin'].value='主要源站';
  const savedProbe=form.elements['probe-origin'].value;
  f.api.networkMonitorEditorAction('network-node-remove',form.querySelector('[data-node="origin"]'));
  assert.equal(f.form().elements['name-origin'],undefined);
  f.form().elements.cdn.value='https://draft.example.net/health';
  f.api.networkMonitorEditorAction('network-monitor-undo',f.form().querySelector('[data-action="network-monitor-undo"]'));
  assert.equal(f.form().elements['name-origin'].value,'自己的源站');assert.equal(f.form().elements['probe-origin'].value,savedProbe);
  assert.equal(f.form().elements['group-name-origin'].value,'主要源站');assert.equal(f.form().elements.cdn.value,'https://draft.example.net/health');
  f.api.networkMonitorEditorAction('network-group-add',f.form().querySelector('[data-action="network-group-add"]'));
  const group=f.api.networkMonitorEditor.groups.at(-1);assert.match(group.id,/^g-[a-f0-9]{32}$/);
  f.form().elements[`group-name-${group.id}`].value='自定义分组';
  f.api.networkMonitorEditorAction('network-node-add',f.form().querySelector(`[data-action="network-node-add"][data-group="${group.id}"]`));
  const node=group.nodes[0];assert.match(node.id,/^n-[a-f0-9]{32}$/);
  assert.equal(f.form().elements[`group-name-${group.id}`].value,'自定义分组');assert.equal(f.form().elements['probe-origin'].value,savedProbe);
  assert.equal(f.calls.length,0);
});
test('deleting a configured group and undo restore all selections and headings',t=>{
  const f=fixture({probe:true});t.after(f.close);
  f.form().elements['name-origin'].value='专属源站';
  f.api.networkMonitorEditorAction('network-group-remove',f.form().querySelector('[data-action="network-group-remove"][data-group="origin"]'));
  assert.equal(f.form().elements['probe-origin'],undefined);assert.match(f.form().textContent,/不影响探针或服务器/);
  f.api.networkMonitorEditorAction('network-monitor-undo',f.form().querySelector('[data-action="network-monitor-undo"]'));
  assert.equal(f.form().elements['probe-origin'].value,sourceId);assert.equal(f.form().elements['name-origin'].value,'专属源站');assert.equal(f.calls.length,0);
});
test('switching protocol, moving node and adding preserve both target drafts',t=>{
  const f=fixture();t.after(f.close);let form=f.form();
  form.elements.origin.value='https://origin-draft.example.net/';form.elements['name-origin'].value='可移动源站';
  form.elements['method-origin'].value='mmwx';f.api.changeNetworkMonitorMethod(form.elements['method-origin']);form.elements['probe-origin'].value=sourceId;
  const move=form.querySelector('[data-network-move="origin"]');move.value='relays';f.api.moveNetworkMonitorNode(move);
  assert.equal(f.api.networkMonitorEditor.groups.find(group=>group.id==='origin').nodes.length,0);
  assert.equal(f.form().elements['probe-origin'].value,sourceId);assert.equal(f.form().elements.origin.value,'https://origin-draft.example.net/');
  f.api.networkMonitorEditorAction('network-node-add',f.form().querySelector('[data-action="network-node-add"][data-group="relays"]'));
  form=f.form();assert.equal(form.elements['name-origin'].value,'可移动源站');assert.equal(form.elements['probe-origin'].value,sourceId);
  form.elements['method-origin'].value='icmp';f.api.changeNetworkMonitorMethod(form.elements['method-origin']);assert.equal(form.elements.origin.value,'https://origin-draft.example.net/');
});
test('save dynamic titles and groups with revision and newly-added nodes',async t=>{
  const f=fixture();t.after(f.close);
  f.form().elements['group-name-relays'].value='我的线路';f.form().elements['name-relay-a'].value='线路甲';
  f.api.networkMonitorEditorAction('network-node-add',f.form().querySelector('[data-action="network-node-add"][data-group="relays"]'));
  const newNode=f.api.networkMonitorEditor.groups[2].nodes.at(-1);
  f.form().elements[`name-${newNode.id}`].value='线路乙';f.form().elements[`method-${newNode.id}`].value='icmp';f.api.changeNetworkMonitorMethod(f.form().elements[`method-${newNode.id}`]);f.form().elements[newNode.id].value='ping.example.net';
  await f.api.saveNetworkMonitorSettings(f.form());
  const body=f.calls[0].body;assert.equal(body.version,2);assert.equal(body.revision,'c'.repeat(64));assert.equal(body.groups[2].name,'我的线路');assert.equal(body.groups[2].nodes[0].name,'线路甲');
  assert.deepEqual(body.groups[2].nodes.at(-1),{id:newNode.id,name:'线路乙',method:'icmp',target:'ping.example.net'});
  assert.equal(f.api.networkMonitorEditor.revision,'d'.repeat(64));assert.equal(f.api.networkMonitorEditor.undo.length,0);
});
test('empty configuration saves as empty and public page does not resurrect defaults',async t=>{
  const f=fixture();t.after(f.close);
  while(f.api.networkMonitorEditor.groups.length) f.api.networkMonitorEditorAction('network-group-remove',f.form().querySelector('[data-action="network-group-remove"]'));
  await f.api.saveNetworkMonitorSettings(f.form());assert.deepEqual(f.calls[0].body.groups,[]);assert.equal(f.form().querySelectorAll('.network-settings-group').length,0);
  f.api.shopNetworkMonitor.data=f.api.networkStatusData({groups:[],checked_at:null});f.win.document.getElementById('app').innerHTML=f.api.shopNetworkSection();f.api.renderShopNetworkStatus();
  assert.match(f.win.document.getElementById('network-status').textContent,/尚未配置监测/);assert.equal(f.win.document.getElementById('network-routes'),null);
  assert.doesNotMatch(f.win.document.getElementById('network-status').textContent,/示例线路 1|示例线路 2|示例线路 3|源站|CDN 监测/);
});
test('public custom groups escape titles, reject malicious IDs, and use no private fields',t=>{
  const f=fixture();t.after(f.close);
  const status={checked_at:null,groups:[{id:'g-custom',name:'我的线路 & "甲"',nodes:[{id:'n-custom',name:'香港节点',basis:'icmp',status:'unconfigured',target:'hidden.example.net',ip:'203.0.113.44'}]},{id:'g-evil" onmouseover="alert(1)',name:'恶意',nodes:[]},{id:'g-invalid',name:'private.example.net',nodes:[]}]};
  f.api.shopNetworkMonitor.data=f.api.networkStatusData(status);const markup=f.api.shopNetworkStatusMarkup();
  assert.match(markup,/我的线路 &amp; &quot;甲&quot;/);assert.match(markup,/香港节点/);assert.match(markup,/监测分组/);assert.doesNotMatch(markup,/onmouseover|alert|hidden.example|private.example|203.0.113|network-monitor-g-custom/);
  assert.equal(f.api.shopNetworkMonitor.data.groups.length,2);
});
test('conflict keeps complete draft and revision without automatic reload',async t=>{
  const f=fixture({probe:true});t.after(f.close);const form=f.form();
  form.elements['name-origin'].value='冲突草稿';form.elements['group-name-origin'].value='冲突分组';f.conflict();await f.api.saveNetworkMonitorSettings(form);
  assert.equal(f.form(),form);assert.equal(form.elements['name-origin'].value,'冲突草稿');assert.equal(form.elements['group-name-origin'].value,'冲突分组');assert.equal(form.elements['probe-origin'].value,sourceId);
  assert.match(form.querySelector('.form-error').textContent,/草稿仍已保留/);assert.equal(f.calls.length,1);assert.equal(f.api.networkMonitorEditor.revision,'c'.repeat(64));
});
test('new groups and nodes stop at configured limits',t=>{
  const f=fixture();t.after(f.close);
  while(f.api.networkMonitorEditor.groups.length<12) f.api.networkMonitorEditorAction('network-group-add',f.form().querySelector('[data-action="network-group-add"]'));
  f.api.networkMonitorEditorAction('network-group-add',f.form().querySelector('[data-action="network-group-add"]'));assert.equal(f.api.networkMonitorEditor.groups.length,12);assert.equal(f.form().querySelector('[data-action="network-group-add"]').disabled,true);
  while(f.api.networkMonitorEditor.groups.flatMap(group=>group.nodes).length<40) f.api.networkMonitorEditorAction('network-node-add',f.form().querySelector('[data-action="network-node-add"]'));
  f.api.networkMonitorEditorAction('network-node-add',f.form().querySelector('[data-action="network-node-add"]'));assert.equal(f.api.networkMonitorEditor.groups.flatMap(group=>group.nodes).length,40);assert.equal([...f.form().querySelectorAll('[data-action="network-node-add"]')].every(button=>button.disabled),true);
});

test('browser method discloses public address and preserves draft through probe and HTTP switches',async t=>{
  const f=fixture();t.after(f.close);let form=f.form(),method=form.elements['method-cdn'];
  method.value='browser';f.api.changeNetworkMonitorMethod(method);form.elements.cdn.value='https://edge.example.net/ping';
  assert.match(method.selectedOptions[0].textContent,/买家浏览器测速/);assert.match(method.closest('.network-settings-node').textContent,/将在买家浏览器中可见/);
  method.value='mmwx';f.api.changeNetworkMonitorMethod(method);form.elements['probe-cdn'].value=sourceId;
  method.value='browser';f.api.changeNetworkMonitorMethod(method);assert.equal(form.elements.cdn.value,'https://edge.example.net/ping');assert.equal(form.elements['probe-cdn'].value,sourceId);
  await f.api.saveNetworkMonitorSettings(form);
  assert.deepEqual(f.calls[0].body.groups[0].nodes[0],{id:'cdn',name:'CDN 线路',method:'browser',target:'https://edge.example.net/ping'});
  assert.equal(f.form().elements['method-cdn'].value,'browser');assert.match(f.form().querySelector('.network-settings-target-help').textContent,/将在买家浏览器中可见/);
});
test('browser invalid or credentialed URL fails before saving',async t=>{
  const f=fixture();t.after(f.close);const form=f.form(),method=form.elements['method-cdn'];method.value='browser';f.api.changeNetworkMonitorMethod(method);
  for(const url of ['http://edge.example.net','https://user:pass@edge.example.net','https://edge.example.net?q=x','https://edge.example.net#fragment','https://edge.example.net?']) {
    form.elements.cdn.value=url;await f.api.saveNetworkMonitorSettings(form);assert.match(form.querySelector('.form-error').textContent,/HTTPS 地址/);
  }
  assert.equal(f.calls.length,0);
});
