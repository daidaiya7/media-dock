(function () {
  'use strict';
  const escape = value => String(value).replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[character]);
  const groups = () => window.MediaDockGuide.editableGroups();

  function panel(config = {}) {
    const texts = config?.texts || {};
    return `<form id="guide-form" class="panel guide-editor"><section class="settings-section"><h2>使用教程</h2><p class="subtitle">按章节编辑现有教程的标题、说明、推荐软件和常见问题。原有目录、重点样式与连接图解会保留。</p><p class="micro">展开章节后修改文字，预览满意后保存。内容公开给所有访客，请勿填写密码、密钥或内部服务地址。</p><label class="field mt-18"><span>查找教程内容</span><input type="search" data-guide-search placeholder="输入软件名称或关键词，例如 Infuse、端口"><small>搜索只筛选编辑项，不会改动已输入的内容。</small></label><div class="guide-editor-chapters">${groups().map((group,index)=>`<details class="guide-editor-chapter" ${index===0?'open':''}><summary>${escape(group.title)}<span>${group.fields.length} 项文字</span></summary><div class="guide-editor-fields">${group.fields.map((field,i)=>{const current=typeof texts[field.key]==='string'?texts[field.key]:field.defaultValue;return `<label class="field"><span>${String(i+1).padStart(2,'0')} · ${escape(field.label)}</span><textarea data-guide-key="${field.key}" maxlength="5000" rows="${field.defaultValue.length>90?4:2}">${escape(current)}</textarea></label>`;}).join('')}</div></details>`).join('')}</div><p class="form-error" role="alert"></p><div class="inline-actions guide-editor-actions"><button type="button" class="btn btn-outline" data-guide-action="preview">预览教程</button><button type="button" class="btn btn-outline" data-guide-action="reset">恢复默认文字</button><button type="submit" class="btn btn-primary">保存教程</button></div><p class="micro guide-editor-feedback" role="status">修改仅在保存后发布；恢复默认也需要保存。</p><div class="guide-editor-preview" hidden><div class="guide-editor-preview-heading"><strong>未保存内容预览</strong><button type="button" class="btn btn-outline btn-small" data-guide-action="close-preview">收起预览</button></div><div class="guide-page" data-guide-preview></div></div></section></form>`;
  }

  function value(form) {
    const defaults = Object.fromEntries(groups().flatMap(group=>group.fields.map(field=>[field.key,field.defaultValue])));
    const texts = {};
    form.querySelectorAll('[data-guide-key]').forEach(input=>{
      const text=input.value.replace(/\r\n?/g,'\n');
      if (text !== defaults[input.dataset.guideKey]) texts[input.dataset.guideKey]=text;
    });
    if (Object.values(texts).reduce((total,text)=>total+text.length,0)>24000) throw new Error('教程自定义文字合计不能超过 24000 字。');
    return {texts};
  }

  document.addEventListener('input',event=>{
    if(!event.target.matches('[data-guide-search]'))return;
    const form=event.target.closest('#guide-form'),query=event.target.value.trim().toLocaleLowerCase();
    form.querySelectorAll('.guide-editor-chapter').forEach(chapter=>{
      if(query && !chapter.hasAttribute('data-search-open'))chapter.dataset.searchOpen=String(chapter.open);
      let found=false;
      chapter.querySelectorAll('.field').forEach(field=>{
        const match=!query||(field.textContent+' '+field.querySelector('textarea').value).toLocaleLowerCase().includes(query);
        field.hidden=!match;found=found||match;
      });
      chapter.hidden=!found;
      if(query && found)chapter.open=true;
      if(!query && chapter.hasAttribute('data-search-open')){chapter.open=chapter.dataset.searchOpen==='true';delete chapter.dataset.searchOpen;}
    });
  });
  document.addEventListener('click',event=>{
    const button=event.target.closest?.('[data-guide-action]');
    const form=button?.closest('#guide-form');
    if (!form) return;
    const feedback=form.querySelector('.guide-editor-feedback');
    const preview=form.querySelector('.guide-editor-preview');
    if(button.dataset.guideAction==='reset'){
      const defaults=Object.fromEntries(groups().flatMap(group=>group.fields.map(field=>[field.key,field.defaultValue])));
      form.querySelectorAll('[data-guide-key]').forEach(input=>{input.value=defaults[input.dataset.guideKey];});
      preview.hidden=true;
      feedback.textContent='已恢复默认文字，点击“保存教程”后发布。';
    } else if(button.dataset.guideAction==='preview'){
      try {
        const target=form.querySelector('[data-guide-preview]');
        target.innerHTML=window.MediaDockGuide.markup({guide:value(form)});
        target.querySelectorAll('[id]').forEach(element=>{element.id='preview-'+element.id;});
        target.querySelectorAll('[aria-labelledby]').forEach(element=>{element.setAttribute('aria-labelledby',element.getAttribute('aria-labelledby').split(/\s+/).map(id=>'preview-'+id).join(' '));});
        target.querySelectorAll('a[href^="#"]').forEach(link=>{link.setAttribute('href','#preview-'+link.getAttribute('href').slice(1));});
        preview.hidden=false;
        form.querySelector('.form-error').textContent='';
        feedback.textContent='下方为未保存内容的预览，保存后前台生效。';
      } catch(error){form.querySelector('.form-error').textContent=error.message;}
    } else if(button.dataset.guideAction==='close-preview') preview.hidden=true;
  });
  document.addEventListener('click',event=>{
    const link=event.target.closest?.('[data-guide-preview] a');
    if(link && !link.getAttribute('href')?.startsWith('#')) event.preventDefault();
  });
  window.MediaDockGuideEditor=Object.freeze({panel,value});
}());
