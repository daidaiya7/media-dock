const {test}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/theme.js'),'utf8');
function fixture({saved,blocked=false}={}){
  const dom=new JSDOM('<meta name="theme-color"><div id="controls"></div>',{url:'https://portal.example',runScripts:'outside-only'});
  if(saved)dom.window.localStorage.setItem('mediadock-theme-mode-v2',saved);
  if(blocked)Object.defineProperty(dom.window,'localStorage',{get(){throw new Error('storage disabled');}});
  dom.window.eval(source);return dom;
}
test('automatic theme switches at boundaries and supports overnight daytime',t=>{
  const d=fixture();t.after(()=>d.window.close());const api=d.window.MediaDockTheme;
  const at=(h,m)=>api.resolve('auto',new d.window.Date(2026,9,12,h,m));
  api.configure({theme_mode:'auto',day_start:'07:00',night_start:'19:00'});
  assert.equal(at(6,59),'dark');assert.equal(at(7,0),'light');assert.equal(at(18,59),'light');assert.equal(at(19,0),'dark');
  api.configure({theme_mode:'auto',day_start:'22:00',night_start:'06:00'});
  assert.equal(at(23,30),'light');assert.equal(at(0,0),'light');assert.equal(at(6,0),'dark');assert.equal(at(12,0),'dark');
});
test('manual choice overrides administrator default, updates controls and persists',t=>{
  const d=fixture();t.after(()=>d.window.close());const w=d.window,a=w.MediaDockTheme;
  a.configure({theme_mode:'light'});assert.equal(w.document.documentElement.dataset.theme,'light');
  w.document.querySelector('#controls').innerHTML=a.control()+a.control();
  w.document.querySelector('[data-theme-toggle]').click();
  a.configure({theme_mode:'light'});assert.equal(w.document.documentElement.dataset.theme,'dark');
  assert.equal(w.localStorage.getItem('mediadock-theme-mode-v2'),'dark');
  assert.ok([...w.document.querySelectorAll('[data-theme-toggle]')].every(el=>el.dataset.themeMode==='dark'));
  assert.equal(a.setMode('invalid'),false);assert.equal(w.document.documentElement.dataset.theme,'dark');
});
test('saved choice is applied at script evaluation, before deferred rendering',t=>{
  const d=fixture({saved:'light'});t.after(()=>d.window.close());
  assert.equal(d.window.document.documentElement.dataset.theme,'light');
  assert.equal(d.window.document.querySelector('meta').content,'#f7f5ef');
});
test('theme remains operable with storage blocked and invalid schedules',t=>{
  const d=fixture({blocked:true});t.after(()=>d.window.close());const a=d.window.MediaDockTheme;
  a.configure({theme_mode:'auto',day_start:'bad',night_start:'99:00'});
  assert.equal(a.resolve('auto',new d.window.Date(2026,9,12,12,0)),'light');
  assert.equal(a.setMode('light'),true);assert.equal(d.window.document.documentElement.dataset.theme,'light');
});
test('storage event syncs another tab without discarding configuration',t=>{
  const d=fixture();t.after(()=>d.window.close());const w=d.window;
  w.MediaDockTheme.configure({theme_mode:'dark'});
  w.localStorage.setItem('mediadock-theme-mode-v2','light');
  w.dispatchEvent(new w.StorageEvent('storage',{key:'mediadock-theme-mode-v2',newValue:'light'}));
  assert.equal(w.document.documentElement.dataset.theme,'light');
});

test('new visitors default to automatic and icon clicks cycle all three modes',t=>{
 const d=fixture();t.after(()=>d.window.close());const w=d.window,a=w.MediaDockTheme;
 assert.equal(w.document.documentElement.dataset.themeMode,'auto');
 w.document.querySelector('#controls').innerHTML=a.control();const button=w.document.querySelector('button');
 assert.equal(button.querySelectorAll('svg').length,1);assert.equal(w.document.querySelector('select'),null);
 for(const mode of ['light','dark','auto']){button.click();assert.equal(w.document.documentElement.dataset.themeMode,mode);assert.ok(button.getAttribute('aria-label').includes('点击切换'));}
});
