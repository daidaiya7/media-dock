const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),path=require('path');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\nstart\(\);\s*$/,'');
function fixture(route='/',options={}){
 const dom=new JSDOM('<div id="app"></div><div id="modal-root"></div><div id="toast-root"></div>',{url:'https://mg.example'+route,runScripts:'outside-only'}),w=dom.window,timers=new Map();let seq=0;
 w.setTimeout=(fn,ms)=>{timers.set(++seq,{fn,ms});return seq};w.clearTimeout=id=>timers.delete(id);
 if(options.seen)w.localStorage.setItem('mediadock-introduction-seen','1');
 if(options.blocked)for(const key of ['localStorage','sessionStorage'])Object.defineProperty(w,key,{get(){throw Error('unavailable')}});
 w.fetch=async url=>({ok:true,json:async()=>url==='/api/public/catalog'?{products:[],payment_configured:false}:{movies:[]}});
 w.eval(source+'\nwindow.introTest={start,showIntroduction,showModal,closeModal,footer};');
 return {w,api:w.introTest,root:w.document.querySelector('#modal-root'),timers,close:()=>w.close(),unlock(){const t=[...timers.values()].find(t=>t.ms===1000);assert.ok(t);t.fn();}};
}
test('first home visit displays immediately and all dismissal paths wait for one second',async t=>{
 const f=fixture();t.after(f.close);const run=f.api.start();await run;
 assert.ok(f.root.querySelector('.intro-modal'));assert.ok(f.root.querySelector('[data-intro-ack]').disabled);
 f.root.querySelector('[data-intro-ack]').click();f.api.closeModal();f.w.document.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.ok(f.root.firstChild);
 assert.equal(f.w.localStorage.getItem('mediadock-introduction-seen'),'1');f.unlock();
 assert.equal(f.root.querySelector('[data-intro-ack]').textContent,'了解');f.root.querySelector('[data-intro-ack]').click();assert.equal(f.root.childElementCount,0);
 await run;await f.api.start();assert.equal(f.root.childElementCount,0);
});
test('return visit suppresses auto dialog and footer reopens same introduction before plans',async t=>{
 const f=fixture('/',{seen:true});t.after(f.close);await f.api.start();assert.equal(f.root.childElementCount,0);
 const links=f.w.document.querySelector('.footer-links');assert.equal(links.children[0].textContent,'影视服简介');assert.equal(links.children[1].getAttribute('href'),'/plans');
 links.children[0].focus();links.children[0].click();await new Promise(resolve=>setImmediate(resolve));assert.ok(f.root.querySelector('.intro-modal'));assert.equal(f.root.querySelector('[data-intro-ack]').disabled,false);
 assert.match(f.root.textContent,/统一观影入口/);assert.match(f.root.textContent,/领取链接/);assert.match(f.root.textContent,/购买必看/);
 f.root.querySelector('[data-intro-ack]').click();assert.equal(f.w.document.activeElement,links.children[0]);
});
test('auto announcement never opens on checkout retrieval, review, plans or admin routes',t=>{
 for(const route of ['/?order=ORDER','/?review=REVIEW','/plans','/guide','/admin']){
  const f=fixture(route);f.api.showIntroduction(true);assert.equal(f.root.childElementCount,0);assert.equal(f.w.localStorage.getItem('mediadock-introduction-seen'),null);f.close();
 }
});
test('blocked browser storage does not break reading or repeat within the page',t=>{
 const f=fixture('/',{blocked:true});t.after(f.close);f.api.showIntroduction(true);f.unlock();f.api.closeModal();f.api.showIntroduction(true);assert.equal(f.root.childElementCount,0);
});
test('pending timer is cleared and cannot mutate a replacement modal',t=>{
 const f=fixture();t.after(f.close);f.api.showIntroduction(true);const timer=[...f.timers.values()].find(x=>x.ms===1000);f.api.showModal('<h2 id="modal-title">Other</h2>');assert.equal(f.timers.size,0);timer.fn();assert.equal(f.root.textContent,'Other');f.api.closeModal();assert.equal(f.root.childElementCount,0);
});

test('configured announcement controls automatic popup and safely escapes all content',async t=>{
 const f=fixture();t.after(f.close);
 const intro={enabled:false,title:'<img src=x onerror=alert(1)>',subtitle:'<script>bad</script>',sections:[{title:'购买必看 <b>x</b>',body:'</textarea><script>bad</script>\n第二行'}]};
 f.w.fetch=async()=>({ok:true,json:async()=>({products:[],introduction:intro})});
 await f.api.start();assert.equal(f.root.childElementCount,0);assert.equal(f.w.localStorage.getItem('mediadock-introduction-seen'),null);
 f.w.document.querySelector('.footer-intro-link').click();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(f.root.querySelector('#modal-title').textContent,intro.title);assert.equal(f.root.querySelectorAll('script,img,b,textarea').length,0);
 assert.match(f.root.textContent,/第二行/);assert.ok(f.root.querySelector('.intro-purchase'));
});
test('catalog failure does not bypass disabled state with default popup',async t=>{
 const f=fixture();t.after(f.close);f.w.fetch=async()=>{throw new Error('offline')};await f.api.start();assert.equal(f.root.childElementCount,0);
});
test('preview never records the introduction as seen',t=>{
 const f=fixture();t.after(f.close);f.api.showIntroduction(false,{enabled:true,title:'预览',subtitle:'',sections:[{title:'测试',body:'未保存'}]},true);
 assert.match(f.root.textContent,/未保存内容的预览/);assert.equal(f.w.localStorage.getItem('mediadock-introduction-seen'),null);
 f.api.closeModal();f.api.showIntroduction(true);assert.ok(f.root.querySelector('[data-intro-ack]').disabled);
});


test('custom entry label appears before plans and in the current dialog hint as plain text',async t=>{
 const f=fixture('/',{seen:true});t.after(f.close);
 const intro={entry_label:'购买必看 <b>公告</b>',enabled:true,title:'标题',subtitle:'',sections:[{title:'说明',body:'正文'}]};
 f.w.fetch=async()=>({ok:true,json:async()=>({products:[],introduction:intro})});await f.api.start();
 const link=f.w.document.querySelector('.footer-intro-link');assert.equal(link.textContent,intro.entry_label);assert.equal(link.querySelector('b'),null);
 link.click();await new Promise(resolve=>setImmediate(resolve));assert.match(f.root.textContent,/购买必看 <b>公告<\/b>/);assert.equal(f.root.querySelector('b'),null);
});
