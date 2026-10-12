const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
function fixture(){
  const dom=new JSDOM('<main id="root"></main>',{url:'https://portal.example/admin',runScripts:'outside-only'});
  for(const file of ['guide.js','guide-editor.js'])dom.window.eval(fs.readFileSync(path.join(__dirname,'../static',file),'utf8'));
  return {w:dom.window,root:dom.window.document.querySelector('#root'),close:()=>dom.window.close()};
}
test('editable tutorial preserves diagrams, formatting and section anchors',t=>{
  const f=fixture();t.after(f.close);const groups=f.w.MediaDockGuide.editableGroups();
  assert.equal(groups.length,8);assert.ok(groups.flatMap(x=>x.fields).length<400);
  f.root.innerHTML=f.w.MediaDockGuide.markup();
  assert.equal(f.root.querySelectorAll('.guide-section').length,7);assert.equal(f.root.querySelectorAll('img').length,7);
  assert.ok(f.root.querySelector('#guide-connection'));assert.ok(f.root.querySelector('.guide-purchase-notice strong'));
});
test('plain text overrides cannot inject HTML and section titles update the directory',t=>{
  const f=fixture();t.after(f.close);const title='<img src=x onerror=alert(1)>',heading='购买前先看这里';
  f.root.innerHTML=f.w.MediaDockGuide.markup({guide:{texts:{'heading-t0':title,'library-t0':heading}}});
  assert.equal(f.root.querySelector('h1').textContent,title);assert.equal(f.root.querySelector('h1 img'),null);
  assert.equal(f.root.querySelector('#guide-library-title').textContent,heading);
  assert.equal(f.root.querySelector('.guide-toc a[href="#guide-library"]').textContent,heading);
});
test('claim edits remain aligned whether the user center is configured or absent',t=>{
  const f=fixture();t.after(f.close);const group=f.w.MediaDockGuide.editableGroups().find(x=>x.id==='claim');
  const field=group.fields.find(x=>x.defaultValue.includes('领取凭证与兑换卡密用途不同'));assert.ok(field);
  for(const userCenterUrl of [undefined,'https://users.example.com/']){
    f.root.innerHTML=f.w.MediaDockGuide.markup({userCenterUrl,guide:{texts:{[field.key]:'统一的兑换说明'}}});
    assert.match(f.root.querySelector('#guide-claim').textContent,/统一的兑换说明/);
    assert.ok(f.root.querySelector('#guide-claim').textContent.includes('用户中心'));
  }
});
test('editor saves only changed text, preview is inert and reset waits for saving',t=>{
  const f=fixture();t.after(f.close);f.root.innerHTML=f.w.MediaDockGuideEditor.panel();
  const form=f.root.querySelector('form');assert.equal(JSON.stringify(f.w.MediaDockGuideEditor.value(form)),JSON.stringify({texts:{}}));
  const title=form.querySelector('[data-guide-key="heading-t0"]');title.value='自定义教程';
  assert.equal(f.w.MediaDockGuideEditor.value(form).texts['heading-t0'],'自定义教程');
  form.querySelector('[data-guide-action="preview"]').click();
  assert.equal(form.querySelector('[data-guide-preview] h1').textContent,'自定义教程');
  assert.equal(form.querySelector('[data-guide-preview] #guide-top'),null);
  assert.ok(form.querySelector('[data-guide-preview] #preview-guide-top'));
  form.querySelector('[data-guide-action="reset"]').click();
  assert.equal(JSON.stringify(f.w.MediaDockGuideEditor.value(form)),JSON.stringify({texts:{}}));
  assert.equal(form.querySelector('.guide-editor-preview').hidden,true);
  assert.match(form.querySelector('.guide-editor-feedback').textContent,/保存教程/);
});
test('keyword filtering retains hidden drafts and restores chapters on clear',t=>{
 const f=fixture();t.after(f.close);f.root.innerHTML=f.w.MediaDockGuideEditor.panel();
 const form=f.root.querySelector('form'),search=form.querySelector('[data-guide-search]');
 form.querySelector('[data-guide-key="heading-t0"]').value='未保存标题';
 search.value='Infuse';search.dispatchEvent(new f.w.Event('input',{bubbles:true}));
 assert.ok([...form.querySelectorAll('.guide-editor-chapter')].some(x=>x.hidden));
 assert.equal(f.w.MediaDockGuideEditor.value(form).texts['heading-t0'],'未保存标题');
 search.value='';search.dispatchEvent(new f.w.Event('input',{bubbles:true}));
 assert.equal(form.querySelectorAll('.guide-editor-chapter[hidden]').length,0);
});
