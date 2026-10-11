/* Aggregate numbers only; this module never receives media paths or credentials. */
(() => {
  'use strict';
  let active = null;
  const labels = [['movies','电影','部'],['series','剧集','部'],['episodes','总集数','集']];
  const qualities = [['uhd','4K UHD','超高清'],['hdr','HDR','高动态范围'],['dolby_vision','Dolby Vision','杜比视界'],['remux','REMUX','版本标记']];
  const scopes={all:{label:'全部',unit:'个',noun:'视频条目',description:'电影 + 单集',empty:'暂无视频'},movies:{label:'电影',unit:'部',noun:'电影',description:'电影',empty:'暂无电影'},episodes:{label:'剧集',unit:'集',noun:'单集',description:'剧集按单集统计',empty:'暂无单集'}};
  const integer = value => Number.isSafeInteger(value) && value >= 0 && value <= 100000000 ? value : null;
  const format = value => value === null ? '—' : value.toLocaleString('zh-CN');
  const escape = value => String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

  function markup() {
    return '<section class="media-stats" data-media-stats aria-label="影视库统计" hidden></section>';
  }

  function normalize(raw) {
    if(!raw || typeof raw!=='object' || typeof raw.configured!=='boolean')throw new Error('Invalid statistics');
    if(!raw.configured)return {configured:false};
    if(!['ready','syncing','unavailable'].includes(raw.state))throw new Error('Invalid statistics state');
    const counts=raw.counts&&typeof raw.counts==='object'?raw.counts:{},quality=raw.quality&&typeof raw.quality==='object'?raw.quality:{};
    const normalizeQuality=value=>({...Object.fromEntries(['denominator',...qualities.map(([key])=>key)].map(key=>[key,integer(value[key])])),remux_unknown:Object.hasOwn(value,'remux_unknown')?integer(value.remux_unknown):0});
    const normalizedQuality=normalizeQuality(quality),byType={};
    const typed=raw.quality_by_type;
    if(typed&&typeof typed==='object'&&!Array.isArray(typed))for(const key of Object.keys(scopes))if(Object.hasOwn(typed,key)&&typed[key]&&typeof typed[key]==='object'&&!Array.isArray(typed[key]))byType[key]=normalizeQuality(typed[key]);
    // Old snapshots describe movies only; never present those figures as all media.
    if(!Object.hasOwn(byType,'movies'))byType.movies=normalizedQuality;
    return {configured:true,state:raw.state,counts:Object.fromEntries(labels.map(([key])=>[key,integer(counts[key])])),library_count:integer(raw.library_count),quality:normalizedQuality,quality_by_type:byType,updated_at:typeof raw.updated_at==='string'&&raw.updated_at.length<=40?raw.updated_at:null,stale:raw.stale===true};
  }

  function render(state, data) {
    if(active !== state || !state.element.isConnected) return;
    if(!data || data.configured !== true) {state.element.hidden=true;state.element.innerHTML='';return;}
    state.element.hidden=!state.visible;
    const counts=data.counts && typeof data.counts==='object'?data.counts:{};
    const byType=data.quality_by_type||{movies:data.quality||{}};
    const selected=Object.hasOwn(byType,state.scope)?state.scope:'movies',scope=scopes[selected];
    const quality=byType[selected]||{};
    const denominator=integer(quality.denominator),libraryCount=integer(data.library_count);
    const remuxUnknown=Object.hasOwn(quality,'remux_unknown')?integer(quality.remux_unknown):0;
    const date=typeof data.updated_at==='string'?new Date(data.updated_at):null;
    const time=date && Number.isFinite(date.getTime())?date.toLocaleString('zh-CN',{timeZone:'Asia/Taipei',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'';
    const ready=data.state==='ready';
    const status=ready?(data.stale?'最近一次统计':'已同步 Emby'):data.state==='syncing'?'正在同步统计':'统计暂时不可用';
    const detailsOpen=state.element.querySelector('details')?.open===true;
    const focusedScope=state.element.contains(document.activeElement)?document.activeElement?.dataset.mediaStatsScope:null;
    state.element.innerHTML=`<div class="media-stats-totals">${labels.map(([key,label,unit])=>`<div class="media-stats-total"><span>${label}</span><strong>${format(integer(counts[key]))}<em>${unit}</em></strong></div>`).join('')}</div>
      <div class="media-stats-quality-heading"><div class="media-stats-switch" role="group" aria-label="画质统计范围">${Object.entries(scopes).map(([key,value])=>`<button type="button" data-media-stats-scope="${key}" aria-pressed="${selected===key}"${Object.hasOwn(byType,key)?'':' disabled title="该范围统计暂未提供"'}>${value.label}</button>`).join('')}</div><span class="media-stats-denominator">${scope.description} · 分母 ${denominator===null?'待统计':`${format(denominator)} ${selected==='all'?'个视频条目':scope.unit}`}${!Object.hasOwn(byType,'all')&&!Object.hasOwn(byType,'episodes')?' · 仅有电影统计':''}</span></div>
      <div class="media-stats-qualities">${qualities.map(([key,label,description])=>{
        let amount=integer(quality[key]);
        if(denominator===null || (amount!==null && amount>denominator))amount=null;
        if(key==='remux' && (remuxUnknown===null || (denominator!==null && remuxUnknown>denominator)))amount=null;
        if(key==='remux' && denominator>0 && remuxUnknown===denominator && amount===0)amount=null;
        const lowerBound=key==='remux' && remuxUnknown>0;
        // Round lower bounds down so their displayed percentage never overstates the identified share.
        const ratio=amount!==null&&denominator>0?(lowerBound?Math.floor(amount/denominator*1000):Math.round(amount/denominator*1000))/10:null;
        const ratioLabel=ratio===null?(denominator===0?scope.empty:'待统计'):`${lowerBound?'≥ ':''}${ratio.toFixed(1)}%`;
        const note=lowerBound&&remuxUnknown<=denominator?`<small class="media-stats-quality-note">${amount!==null?'已识别 · ':''}${format(remuxUnknown)} ${scope.unit}文件名待补充</small>`:'';
        return `<div class="media-stats-quality"><div class="media-stats-quality-label"><h4 title="${description}">${label}<small>${description}</small></h4><strong>${ratioLabel}</strong></div>${ratio===null?'<div class="media-stats-track is-pending" aria-hidden="true"></div>':`<progress max="100" value="${ratio}" aria-label="${label} ${lowerBound?'至少':''}占${scope.noun} ${ratio.toFixed(1)}%"></progress>`}<p>${lowerBound&&amount!==null?'≥ ':''}${format(amount)}<span> ${scope.unit}</span></p>${note}</div>`;
      }).join('')}</div>
      <div class="media-stats-foot"><div class="media-stats-notes"><details${detailsOpen?' open':''}><summary>统计口径</summary><p>仅统计已开放媒体库。顶部电影与剧集按部计数，总集数按单集计数。画质范围可切换：电影以电影条目为分母；剧集以每一集为分母；全部以电影与单集之和的视频条目为分母，不将整部剧重复计入。4K 为 Emby 识别宽度至少 3840 的影片；HDR 包含 Emby 识别的 HDR10、HDR10+、HLG 和杜比视界；杜比视界按 Emby 视频类型识别；REMUX 按文件名中的版本标记识别。各标签独立统计；画质与版本可重叠，比例不相加。${remuxUnknown>0?'REMUX 的 ≥ 表示已识别数量与比例的下限，文件名补充后实际数量可能更多。':''}不同版本可能分别计数；未识别的规格不会算入，缺少可用数据的比例显示待统计。</p></details></div><span class="media-stats-source">${libraryCount!==null?`${format(libraryCount)} 个开放媒体库 · `:''}${status}</span><span class="media-stats-updated">${time?`统计快照${data.stale?'（上次成功）':''} · ${escape(time)}（北京时间）`:data.state==='syncing'?'首次统计完成后自动显示':'稍后自动更新'}</span></div>`;
    if(focusedScope&&Object.hasOwn(scopes,focusedScope))state.element.querySelector(`[data-media-stats-scope="${focusedScope}"]`)?.focus({preventScroll:true});
  }

  async function load(state) {
    if(active!==state || !state.element.isConnected)return;
    clearTimeout(state.timer);state.timer=null;
    state.controller?.abort();
    const controller=new AbortController();state.controller=controller;
    const timer=setTimeout(()=>controller.abort(),12000);state.timeout=timer;
    try {
      const response=await fetch('/api/public/library/stats',{credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(!response.ok)throw new Error('Unavailable');
      const data=normalize(await response.json());
      if(active!==state || !state.element.isConnected)return;
      render(state,data);state.data=data;
      state.timer=setTimeout(()=>{if(active===state)load(state);},data.state==='syncing'?15000:300000);
    } catch {
      if(active!==state || !state.element.isConnected)return;
      const fallback=state.data?.state==='ready'?{...state.data,stale:true}:state.data?.configured===false?{configured:false}:{configured:true,state:'unavailable',counts:{},quality:{}};
      state.data=fallback;render(state,fallback);
      state.timer=setTimeout(()=>{if(active===state)load(state);},60000);
    } finally {clearTimeout(timer);if(state.timeout===timer)state.timeout=null;}
  }

  function stop() {if(active){active.controller?.abort();clearTimeout(active.timer);clearTimeout(active.timeout);active.element.removeEventListener('click',active.onClick);active=null;}}
  function mount(root) {
    stop();if(!/^\/library\/?$/.test(location.pathname))return;const element=root?.querySelector('[data-media-stats]');if(!element)return;
    const state={element,visible:!new URLSearchParams(location.search).get('library')&&!new URLSearchParams(location.search).get('query'),scope:'all',data:null,timer:null,timeout:null,controller:null,onClick:null};
    state.onClick=event=>{const button=event.target.closest?.('[data-media-stats-scope]');const key=button?.dataset.mediaStatsScope;if(active!==state||!button||!element.contains(button)||button.disabled||!Object.hasOwn(scopes,key)||!Object.hasOwn(state.data?.quality_by_type||{},key))return;state.scope=key;render(state,state.data);element.querySelector(`[data-media-stats-scope="${key}"]`)?.focus({preventScroll:true});};
    element.addEventListener('click',state.onClick);
    active=state;load(state);
  }
  function setVisible(value) {if(active){active.visible=value===true;active.element.hidden=!active.visible||active.data?.configured!==true;}}
  window.MediaDockMediaStats=Object.freeze({markup,mount,setVisible,stop});
})();
