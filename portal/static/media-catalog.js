(function () {
  'use strict';

  let current = null;
  let generation = 0;
  const escape = value => String(value).replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[character]);
  const text = (value, limit = 160) => typeof value === 'string' ? value.slice(0, limit).trim() : '';
  const kindOf = kind => kind === 'discovery' || kind === 'discover' ? 'discovery' : 'library';
  const count = value => Number.isInteger(value) && value >= 0 && value <= 1000000 ? value : null;
  const validLibraryId = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,160}$/.test(value);
  const validSort = value => ['name','newest','year'].includes(value);
  const defaultLibrarySort = 'newest';
  const posterPlaceholder = '<span class="media-poster-placeholder" aria-hidden="true"><svg viewBox="0 0 48 56" width="40" height="48" fill="none"><rect x="8" y="4" width="32" height="46" rx="3" stroke="currentColor"/><path d="M17 4v46M31 4v46M8 14h9m14 0h9M8 24h9m14 0h9M8 34h9m14 0h9M8 44h9m14 0h9" stroke="currentColor"/><path d="m22 22 6 5-6 5z" fill="currentColor"/></svg><span>MediaDock</span><small>暂无海报</small></span>';

  function safePoster(value) {
    if (typeof value !== 'string' || value.length > 2048 || /[\s\u0000-\u001f\u007f\\]/.test(value)) return '';
    if (value.startsWith('/') && !value.startsWith('//')) {
      try {
        const url = new URL(value, location.origin);
        if (url.origin !== location.origin || url.search || url.hash || !/^\/api\/public\/(?:library\/(?:poster|cover)|discovery\/poster)\/[A-Za-z0-9_-]{1,160}(?:\.(?:jpg|png|webp))?$/.test(url.pathname)) return '';
        return url.pathname;
      } catch { return ''; }
    }
    // Discovery posters must always use the same-origin server cache. Never
    // fall back to a provider URL here, otherwise visitors would connect to
    // TMDB directly and the China-region image failure would return.
    return '';
  }

  function normalizeItem(raw, kind) {
    if (!raw || typeof raw !== 'object' || !text(raw.title)) return null;
    const item = {title:text(raw.title,160),type:raw.type === 'tv' ? 'tv' : raw.type === 'movie' ? 'movie' : '',poster:safePoster(raw.poster_url),year:''};
    if ((typeof raw.year === 'string' || typeof raw.year === 'number') && /^\d{4}$/.test(String(raw.year)) && Number(raw.year) >= 1800 && Number(raw.year) <= 2200) item.year = String(raw.year);
    if (kind === 'library') {
      item.id = validLibraryId(raw.id) ? raw.id : '';
      item.overview = text(raw.overview,4000);
      item.genres = Array.isArray(raw.genres) ? raw.genres.filter(value=>typeof value==='string').slice(0,4).map(value=>text(value,30)).filter(Boolean) : [];
    } else {
      item.id = typeof raw.id === 'string' && /^[1-9][0-9]{0,11}$/.test(raw.id) ? raw.id : '';
      item.requestToken = typeof raw.request_token === 'string' && raw.request_token.length <= 4096 ? raw.request_token : '';
      item.inLibrary = raw.in_library === true;
      item.subscribed = raw.subscribed === true;
      item.localEpisodes = count(raw.local_episodes);
      item.totalEpisodes = count(raw.total_episodes);
    }
    return item;
  }

  function updatedLabel(value) {
    if (typeof value !== 'string' || value.length > 40) return '';
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return '';
    return date.toLocaleString('zh-CN',{timeZone:'Asia/Taipei',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  }

  function markup(kind) {
    const discovery = kindOf(kind) === 'discovery';
    if (!discovery) return libraryMarkup();
    return `<main class="media-content" data-media-kind="${discovery?'discovery':'library'}">
      <header class="media-heading"><div class="eyebrow">MediaDock / ${discovery?'DISCOVER':'LIBRARY'}</div><h1>${discovery?'下一部，想看什么？':'走进影视服片库。'}</h1><p>${discovery?'搜索感兴趣的影片，提交想看申请；用户中心客户可按原有规则订阅。':'浏览片库，寻找今晚想看的故事。实际可观看内容以账号权限与用户中心为准。'}</p></header>
      ${window.MediaDockSubscriptions?.publicMarkup()||''}
      <section class="media-browser" aria-label="${discovery?'搜索影片与剧集':'片库浏览'}">
        <div class="media-toolbar"><form class="media-search" data-media-form role="search"><label class="visually-hidden" for="media-query">搜索影片或剧集</label><svg aria-hidden="true" width="17" height="17" viewBox="0 0 24 24" fill="none"><circle cx="10.5" cy="10.5" r="6.5" stroke="currentColor" stroke-width="1.5"/><path d="m16 16 5 5" stroke="currentColor" stroke-width="1.5"/></svg><input type="search" id="media-query" name="query" maxlength="80" ${discovery?'minlength="2"':''} autocomplete="off" placeholder="搜索影片或剧集名称" aria-describedby="media-search-feedback"><button type="submit">搜索</button></form>
        <div class="media-filters" role="group" aria-label="影片类型"><button type="button" data-media-filter="all" class="is-active" aria-pressed="true">全部</button><button type="button" data-media-filter="movie" aria-pressed="false">电影</button><button type="button" data-media-filter="tv" aria-pressed="false">剧集</button></div></div><p class="media-search-feedback" id="media-search-feedback" data-media-search-feedback role="status"></p>
        <div class="media-results-heading"><h2 data-media-results-title>搜索影片</h2><div class="discovery-results-tools"><span data-media-results-count></span><button type="button" class="discovery-search-clear" data-media-action="reset" data-discovery-clear hidden>清除搜索</button></div></div>
        <div data-media-results aria-busy="false">${discoveryIdleMarkup()}</div>
        <div class="media-pagination" data-media-pagination hidden></div><p class="media-update-note" data-media-updated role="status"></p><div class="media-attribution" data-media-attribution hidden></div>
      </section><div class="media-page-foot"><a href="${discovery?'/library':'/discover'}">${discovery?'浏览片库':'搜索可订阅影片'} <span aria-hidden="true">→</span></a><a href="/guide">使用指南</a></div>
    </main>`;
  }

  function libraryMarkup() {
    return `<main class="media-content media-library-content" data-media-kind="library">
      <header class="media-heading"><div class="eyebrow">MediaDock / LIBRARY</div><h1 data-library-title>走进影视服片库。</h1><p data-library-description>选择一个媒体库，浏览其中的影片海报。</p></header>
      ${window.MediaDockMediaStats?.markup()||''}
      <section class="media-browser" aria-label="媒体库浏览"><div class="library-view-toolbar" data-library-toolbar hidden><button type="button" class="library-back" data-media-action="libraries"><span aria-hidden="true">←</span> 返回媒体库</button><div data-library-order></div></div>
      <div class="library-search-toolbar"><form class="media-search" data-media-form role="search"><label class="visually-hidden" for="library-query" data-library-search-label>搜索所有媒体库</label><svg aria-hidden="true" width="17" height="17" viewBox="0 0 24 24" fill="none"><circle cx="10.5" cy="10.5" r="6.5" stroke="currentColor" stroke-width="1.5"/><path d="m16 16 5 5" stroke="currentColor" stroke-width="1.5"/></svg><input type="search" id="library-query" name="query" minlength="2" maxlength="80" autocomplete="off" placeholder="搜索所有媒体库中的影片" aria-describedby="library-search-feedback"><button type="submit">搜索</button></form><button type="button" class="library-search-clear" data-media-action="reset" hidden>清除搜索</button></div><p class="media-search-feedback" id="library-search-feedback" data-media-search-feedback role="status"></p>
      <p class="library-view-notice" data-library-notice role="status" hidden></p><div class="media-results-heading"><h2 data-media-results-title>媒体库</h2><span data-media-results-count></span></div>
      <div data-media-results aria-busy="true">${skeleton(true)}</div><div class="media-pagination" data-media-pagination hidden></div><p class="media-update-note" data-media-updated role="status"></p></section>
      <div class="media-page-foot"><a href="/discover">搜索可订阅影片 <span aria-hidden="true">→</span></a><a href="/guide">使用指南</a></div></main>`;
  }

  function skeleton(libraries=false) {
    return `<div class="media-skeleton-grid${libraries?' library-cover-skeleton':''}" aria-hidden="true">${Array.from({length:6},()=>'<div class="media-skeleton"><div></div><span></span><small></small></div>').join('')}</div><span class="visually-hidden" role="status">${libraries?'正在读取媒体库…':'正在读取影片…'}</span>`;
  }

  function episodeMarkup(item) {
    if (item.type !== 'tv') return '';
    const local=item.localEpisodes,total=item.totalEpisodes;
    const label = local !== null ? `已入库 ${local} 集${total>0?` · 共 ${total} 集`:' · 总集数待更新'}` : total>0?`共 ${total} 集 · 进度待更新`:'集数进度待更新';
    const bar = local !== null && total > 0 && local <= total ? `<progress max="${total}" value="${local}" aria-label="已入库 ${local} 集，共 ${total} 集"></progress>` : '';
    return `<div class="media-episode-progress">${bar}<span>${label}</span></div>`;
  }

  function cardMarkup(item, kind, mode) {
    if(kind==='library')return `<article class="media-card library-film-card"><button type="button" class="library-film-open" data-media-action="film-detail" data-film-id="${escape(item.id)}" aria-label="查看 ${escape(item.title)} 的详情"><span class="media-poster">${posterPlaceholder}${item.poster?`<img src="${escape(item.poster)}" alt="${escape(item.title)}海报" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-media-poster>`:''}</span><span class="media-card-copy"><span class="library-film-title">${escape(item.title)}</span><span class="library-film-hint">查看详情 →</span></span></button></article>`;
    const badges = kind === 'discovery' ? `${item.inLibrary?'<span class="media-badge is-library">已入库</span>':''}${item.subscribed?'<span class="media-badge is-tracking">追更中</span>':''}${!item.inLibrary&&!item.subscribed&&mode==='search'?'<span class="media-badge is-available">可订阅</span>':''}` : '';
    const description = kind === 'library' && item.overview ? `<details class="media-description"><summary>查看简介</summary><p>${escape(item.overview)}</p></details>` : '';
    return `<article class="media-card"><div class="media-poster">${posterPlaceholder}${item.poster?`<img src="${escape(item.poster)}" alt="${escape(item.title)}海报" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-media-poster>`:''}${item.type?`<span class="media-type-label">${item.type==='tv'?'剧集':'电影'}</span>`:''}</div><div class="media-card-copy"><h3>${escape(item.title)}</h3><div class="media-card-meta">${item.year?`<span>${item.year}</span>`:'<span>年份待补充</span>'}${item.genres?.length?`<span class="media-genres">${escape(item.genres.join(' / '))}</span>`:''}</div>${badges?`<div class="media-badges">${badges}</div>`:''}${kind==='discovery'?episodeMarkup(item):''}${description}${kind==='discovery'?(window.MediaDockSubscriptions?.cardAction(item)||''):''}</div></article>`;
  }

  function emptyMarkup(title, description, action = '') {
    return `<div class="media-empty"><span class="media-empty-mark" aria-hidden="true">✦</span><h3>${escape(title)}</h3><p>${escape(description)}</p>${action}</div>`;
  }

  function discoveryIdleMarkup() {
    return emptyMarkup('搜索你想看的影片','输入至少 2 个字的片名，找到影片后可提交想看申请或按客户规则订阅。');
  }

  function render(state) {
    if (current !== state || !state.root.isConnected) return;
    if(state.kind==='library'){renderLibrary(state);return;}
    const resultRoot=state.root.querySelector('[data-media-results]'),pagination=state.root.querySelector('[data-media-pagination]'),summary=state.root.querySelector('[data-media-results-count]'),updated=state.root.querySelector('[data-media-updated]'),attribution=state.root.querySelector('[data-media-attribution]');
    state.root.querySelector('[data-media-results-title]').textContent=state.query?'搜索结果':'搜索影片';
    for(const button of state.root.querySelectorAll('[data-media-filter]')) {
      const selected=button.dataset.mediaFilter===state.type;
      button.classList.toggle('is-active',selected);button.setAttribute('aria-pressed',String(selected));
    }
    resultRoot.setAttribute('aria-busy',String(state.loading));
    summary.textContent='';updated.textContent='';pagination.hidden=true;pagination.innerHTML='';attribution.hidden=true;attribution.innerHTML='';
    const clear=state.root.querySelector('[data-discovery-clear]');if(clear)clear.hidden=!state.query;
    if(!state.query){resultRoot.innerHTML=discoveryIdleMarkup();return;}
    if(state.loading){resultRoot.innerHTML=skeleton();return;}
    if(state.error){resultRoot.innerHTML=emptyMarkup('暂时没能读取影片','请稍后再试，或先看看网站的其他内容。','<button type="button" class="btn btn-outline" data-media-action="retry">重新读取</button>');return;}
    const data=state.data;
    if(!data.configured){resultRoot.innerHTML=emptyMarkup(state.kind==='library'?'片库正在准备中':'搜片服务正在准备中','入口开放后，你可以在这里查看内容。请稍后再来。','<a class="btn btn-outline" href="/">返回首页</a>');return;}
    const items=data.items;
    summary.textContent=items.length?`${state.kind==='library'?'本页 ':''}${items.length} 部${state.query?'匹配影片':''}`:'';
    if(!items.length){resultRoot.innerHTML=emptyMarkup(state.query?'没有找到匹配的影片':state.kind==='discovery'?'暂时没有追更内容':'暂时没有可展示的影片',state.query?'试试更短的片名，或切换电影、剧集筛选。':state.kind==='discovery'?'可以搜索感兴趣的影片，提交想看申请或连接用户中心订阅。':'可以稍后再来，或尝试搜索其他片名。',state.query?'<button type="button" class="btn btn-outline" data-media-action="reset">清除搜索</button>':'');}
    else{resultRoot.innerHTML=`<div class="media-grid">${items.map(item=>cardMarkup(item,state.kind,data.mode)).join('')}</div>`;}
    if(state.kind==='library' && (items.length || state.page>1)){
      pagination.hidden=false;pagination.innerHTML=`<button type="button" class="btn btn-outline" data-media-action="prev" ${state.page<=1?'disabled':''}>上一页</button><span>第 ${state.page} 页</span><button type="button" class="btn btn-outline" data-media-action="next" ${!data.hasMore?'disabled':''}>下一页</button>`;
    }
    const time=updatedLabel(data.updatedAt),parts=[];
    if(data.stale)parts.push('当前展示最近一次同步的内容');
    if(time)parts.push(`搜索结果读取于 ${time}（北京时间）`);
    if(state.kind==='discovery' && data.mode==='search')parts.push('访客申请需审核，客户订阅沿用用户中心规则');
    updated.textContent=parts.join(' · ');
    if(state.kind==='discovery' && items.length){
      attribution.hidden=false;attribution.innerHTML='<img src="/static/tmdb-logo.svg" width="70" alt="TMDB"><p>影片海报与资料来源：TMDB。<span>This product uses the TMDB API but is not endorsed or certified by TMDB.</span></p>';
    }
    // Cached images may fail before the delegated load event is observed.
    for(const img of resultRoot.querySelectorAll('[data-media-poster]')) if(img.complete && !img.naturalWidth && img.currentSrc) posterFailed(img);
  }

  function openFilmDetail(state, id) {
    const items=state.libraryId?state.data?.items:state.searchData?.groups.flatMap(group=>group.items);
    const item=items?.find(row=>row.id===id);
    if(!item || typeof window.showModal!=='function')return;
    const meta=[item.type==='tv'?'剧集':'电影',item.year,...(item.genres||[])].filter(Boolean);
    window.showModal(`<div class="film-detail"><div class="film-detail-art">${item.poster?`<img src="${escape(item.poster)}" alt="${escape(item.title)}海报" referrerpolicy="no-referrer">`:posterPlaceholder}</div><div class="film-detail-copy"><div class="eyebrow">MediaDock / LIBRARY</div><h2 id="modal-title">${escape(item.title)}</h2><p class="film-detail-meta">${escape(meta.join(' · '))}</p><p class="film-detail-overview">${escape(item.overview||'这部影片暂时没有简介。')}</p><p class="micro">实际可观看内容以你的账号权限为准。</p><div class="film-detail-actions"><a class="btn btn-primary" href="/plans">购卡套餐</a></div></div></div>`, 'film-detail-modal');
  }

  function libraryCards(libraries) {
    return `<div class="library-cover-grid">${libraries.map(library=>`<a class="library-cover-card" href="/library?${escape(new URLSearchParams({library:library.id,sort:defaultLibrarySort,page:'1'}).toString())}" data-media-action="open-library" data-library-id="${escape(library.id)}"><div class="library-cover">${posterPlaceholder.replace('暂无海报','暂无媒体库封面')}${library.cover?`<img src="${escape(library.cover)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-media-poster>`:''}<span class="library-cover-open" aria-hidden="true">↗</span></div><h3>${escape(library.name)}</h3></a>`).join('')}</div>`;
  }

  function librarySearchGroups(groups, query) {
    return groups.map(group=>`<section class="library-search-group" aria-label="${escape(group.library.name)}搜索结果"><div class="library-search-group-heading"><h3>${escape(group.library.name)}</h3><a href="/library?${escape(new URLSearchParams({library:group.library.id,query,sort:defaultLibrarySort,page:'1'}).toString())}" data-media-action="open-library" data-library-id="${escape(group.library.id)}">查看本库全部结果 <span aria-hidden="true">→</span></a></div><div class="media-grid">${group.items.map(item=>cardMarkup(item,'library')).join('')}</div></section>`).join('');
  }

  function renderLibrary(state) {
    const selected=state.libraryId,library=state.libraries?.libraries.find(item=>item.id===selected);
    window.MediaDockMediaStats?.setVisible(!selected&&!state.query);
    const root=state.root,result=root.querySelector('[data-media-results]'),summary=root.querySelector('[data-media-results-count]'),pagination=root.querySelector('[data-media-pagination]'),updated=root.querySelector('[data-media-updated]');
    root.querySelector('[data-library-title]').textContent=selected?(library?.name||'媒体库'):'走进影视服片库。';
    root.querySelector('[data-library-description]').textContent=selected?'浏览或搜索此媒体库中的影片。':'选择一个媒体库，或搜索所有已开放媒体库中的影片。';
    root.querySelector('[data-media-results-title]').textContent=state.query?(selected?'本库搜索结果':'跨库搜索结果'):selected?'影片海报':'媒体库';
    root.querySelector('[data-library-toolbar]').hidden=!selected;
    const order=root.querySelector('[data-library-order]');
    if(state.query)order.innerHTML='<p class="library-sort" data-library-search-order>搜索结果按匹配顺序排列</p>';
    else{
      if(!order.querySelector('[data-media-sort]'))order.innerHTML='<label class="library-sort"><span>排序</span><select data-media-sort aria-label="影片排序"><option value="newest">最新入库</option><option value="name">按名称</option><option value="year">上映年份</option></select></label>';
      order.querySelector('[data-media-sort]').value=state.sort;
    }
    root.querySelector('[data-library-search-label]').textContent=selected?'搜索当前媒体库':'搜索所有媒体库';
    root.querySelector('[name="query"]').placeholder=selected?'搜索当前媒体库中的影片':'搜索所有媒体库中的影片';
    root.querySelector('.library-search-clear').hidden=!state.query;
    const notice=root.querySelector('[data-library-notice]');notice.textContent=state.notice;notice.hidden=!state.notice;
    result.setAttribute('aria-busy',String(state.loading));summary.textContent='';updated.textContent='';pagination.hidden=true;pagination.innerHTML='';
    if(state.loading){result.innerHTML=skeleton(!selected&&!state.query);return;}
    if(state.error){result.innerHTML=emptyMarkup(state.query?'暂时没能完成搜索':selected?'暂时没能读取影片':'暂时没能读取媒体库','请稍后再试，或先看看网站的其他内容。','<button type="button" class="btn btn-outline" data-media-action="retry">重新读取</button>');return;}
    const data=selected?state.data:state.query?state.searchData:state.libraries;
    if(!data?.configured){result.innerHTML=emptyMarkup('片库正在准备中','入口开放后，你可以在这里查看内容。请稍后再来。','<a class="btn btn-outline" href="/">返回首页</a>');return;}
    if(!selected&&state.query){
      const groups=data.groups,partial=data.partial;
      summary.textContent=groups.length?`${groups.length} 个媒体库 · 已展示 ${groups.reduce((total,group)=>total+group.items.length,0)} 部`:'';
      const warning=partial?'<div class="library-search-partial" role="status"><span>部分媒体库暂时未能搜索，可重试。</span><button type="button" data-media-action="retry">重试搜索</button></div>':'';
      result.innerHTML=warning+(groups.length?librarySearchGroups(groups,state.query):partial?emptyMarkup('暂未取得匹配结果','还有媒体库未完成搜索，请重试后查看完整结果。'):emptyMarkup('没有找到匹配的影片','试试其他片名，或清除搜索返回媒体库。','<button type="button" class="btn btn-outline" data-media-action="reset">清除搜索</button>'));
    }else if(!selected){
      summary.textContent=data.libraries.length?`${data.libraries.length} 个媒体库`:'';
      result.innerHTML=data.libraries.length?libraryCards(data.libraries):emptyMarkup('暂时没有可浏览的媒体库','媒体库开放后会显示在这里，请稍后再来。');
    }else{
      summary.textContent=data.items.length?`本页 ${data.items.length} 部`:'';
      result.innerHTML=data.items.length?`<div class="media-grid">${data.items.map(item=>cardMarkup(item,'library')).join('')}</div>`:emptyMarkup(state.query?'本库没有找到匹配的影片':'这个媒体库暂时没有影片',state.query?'试试其他片名，或返回搜索所有媒体库。':'可以返回媒体库，看看其他内容。',state.query?'<button type="button" class="btn btn-outline" data-media-action="reset">清除搜索</button>':'<button type="button" class="btn btn-outline" data-media-action="libraries">返回媒体库</button>');
      if(data.items.length || state.page>1){pagination.hidden=false;pagination.innerHTML=`<button type="button" class="btn btn-outline" data-media-action="prev" ${state.page<=1?'disabled':''}>上一页</button><span>第 ${state.page} 页</span><button type="button" class="btn btn-outline" data-media-action="next" ${!data.hasMore||state.page>=1000?'disabled':''}>下一页</button>`;}
    }
    const parts=[],time=updatedLabel(data.updatedAt);if(data.stale)parts.push('当前展示最近一次同步的内容');if(time)parts.push(`${selected?'影片列表':state.query?'搜索结果':'媒体库目录'}读取于 ${time}（北京时间）`);updated.textContent=parts.join(' · ');
    for(const img of result.querySelectorAll('[data-media-poster]'))if(img.complete&&!img.naturalWidth&&img.currentSrc)posterFailed(img);
  }

  function normalizeLibraries(raw) {
    if(!raw || typeof raw!=='object' || typeof raw.configured!=='boolean' || !Array.isArray(raw.libraries))throw new Error('invalid');
    const seen=new Set(),libraries=[];
    for(const row of raw.libraries.slice(0,128)){
      if(!row || !validLibraryId(row.id) || seen.has(row.id) || !text(row.name))continue;
      seen.add(row.id);
      const path=`/api/public/library/cover/${row.id}`;
      libraries.push({id:row.id,name:text(row.name,160),cover:row.cover_url===path?path:''});
    }
    return {configured:raw.configured,libraries,updatedAt:raw.updated_at,stale:raw.stale===true};
  }

  function normalizeLibrarySearch(raw, libraries) {
    if(!raw || typeof raw!=='object' || typeof raw.configured!=='boolean' || !Array.isArray(raw.groups))throw new Error('invalid');
    const allowed=new Map(libraries.libraries.map(library=>[library.id,library])),seen=new Map();
    let partial=raw.partial===true;
    for(const group of raw.groups.slice(0,128)){
      const id=group?.library?.id;
      if(!allowed.has(id) || seen.has(id) || !Array.isArray(group.items)){partial=true;continue;}
      const items=group.items.slice(0,6).map(item=>normalizeItem(item,'library')).filter(Boolean);
      if(items.length)seen.set(id,{library:allowed.get(id),items,hasMore:group.has_more===true});
    }
    return {configured:raw.configured,groups:libraries.libraries.flatMap(library=>seen.has(library.id)?[seen.get(library.id)]:[]),partial,updatedAt:raw.updated_at,stale:raw.stale===true};
  }

  function libraryUrl(state, replace=false) {
    const params=new URLSearchParams();
    if(state.libraryId){params.set('library',state.libraryId);params.set('sort',state.sort);params.set('page',String(state.page));}
    if(state.query)params.set('query',state.query);
    history[replace?'replaceState':'pushState'](null,'',`/library${params.size?'?'+params:''}`);
  }

  function expireLibrary(state) {
    state.libraryId='';state.page=1;state.data=null;state.notice='媒体库已更新，请重新选择。';libraryUrl(state,true);
  }

  function posterFailed(img) {
    if(!(img instanceof HTMLImageElement) || !img.matches('[data-media-poster]'))return;
    img.removeAttribute('src');img.hidden=true;img.closest('.media-poster,.library-cover')?.classList.add('has-no-poster');
  }

  async function loadLibrary(state, scroll=false) {
    clearTimeout(state.timer);state.controller?.abort();
    const controller=new AbortController(),request=++state.request;
    state.controller=controller;state.loading=true;state.error=false;render(state);
    const active=()=>current===state && state.generation===generation && request===state.request;
    const timeout=setTimeout(()=>controller.abort(),25000);state.timer=timeout;
    const get=async url=>{
      const response=await fetch(url,{signal:controller.signal,credentials:'omit',cache:'no-store',headers:{Accept:'application/json'}});
      if(!response.ok){const error=new Error('unavailable');error.status=response.status;throw error;}
      const body=await response.json();
      if(controller.signal.aborted)throw new Error('aborted');
      return body;
    };
    try{
      if(!state.libraryId || !state.libraries){
        const raw=await get('/api/public/libraries');if(!active())return;
        state.libraries=normalizeLibraries(raw);
      }
      if(state.libraryId && (!state.libraries.configured || !state.libraries.libraries.some(item=>item.id===state.libraryId))){expireLibrary(state);}
      if(state.libraryId){
        const params=new URLSearchParams({library:state.libraryId,sort:state.sort,page:String(state.page)});
        if(state.query)params.set('query',state.query);
        let raw;
        try{raw=await get(`/api/public/library?${params}`);}catch(error){
          if(!active())return;
          if(![400,404,409,410].includes(error.status))throw error;
          expireLibrary(state);state.libraries=null;
          const latest=await get('/api/public/libraries');if(!active())return;
          state.libraries=normalizeLibraries(latest);
        }
        if(!active())return;
        if(state.libraryId){
          if(!raw || typeof raw!=='object' || typeof raw.configured!=='boolean' || !Array.isArray(raw.items))throw new Error('invalid');
          if(!raw.configured){expireLibrary(state);state.libraries={configured:false,libraries:[],updatedAt:null,stale:false};}
          else{
            state.data={configured:true,items:raw.items.slice(0,60).map(item=>normalizeItem(item,'library')).filter(Boolean),hasMore:raw.has_more===true,updatedAt:raw.updated_at,stale:raw.stale===true};
            if(Number.isInteger(raw.page)&&raw.page>=1&&raw.page<=1000){state.page=raw.page;libraryUrl(state,true);}
          }
        }
      }
      if(!state.libraryId&&state.query){
        if(!state.libraries.configured){state.searchData={configured:false,groups:[],partial:false};return;}
        const raw=await get(`/api/public/library/search?${new URLSearchParams({query:state.query})}`);if(!active())return;
        state.searchData=normalizeLibrarySearch(raw,state.libraries);
      }
    }catch{
      if(active())state.error=true;
    }finally{
      clearTimeout(timeout);if(state.timer===timeout)state.timer=null;
      if(active()){
        state.loading=false;render(state);
        if(scroll)state.root.querySelector('.media-browser')?.scrollIntoView?.({block:'start',behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
      }
    }
  }

  async function load(state, scroll=false) {
    if(current!==state)return;
    if(state.kind==='library')return loadLibrary(state,scroll);
    clearTimeout(state.timer);
    state.controller?.abort();
    const request=++state.request;
    state.controller=null;state.timer=null;state.error=false;
    // Clearing a search invalidates earlier results without ever loading a subscription list.
    if(!state.query){state.data=null;state.loading=false;render(state);return;}
    const controller=new AbortController();
    state.controller=controller;state.loading=true;render(state);
    const params=new URLSearchParams({query:state.query,type:state.type});
    const timeout=setTimeout(()=>controller.abort(),15000);
    state.timer=timeout;
    try{
      const response=await fetch(`/api/public/discovery?${params}`,{signal:controller.signal,credentials:'omit',cache:'no-store',headers:{Accept:'application/json'}});
      if(!response.ok)throw new Error('unavailable');
      const raw=await response.json();
      if(!raw || typeof raw!=='object' || typeof raw.configured!=='boolean' || !Array.isArray(raw.items) || (raw.configured && raw.mode!=='search'))throw new Error('invalid');
      if(current!==state || state.generation!==generation || request!==state.request)return;
      state.data={configured:raw.configured,items:raw.items.slice(0,60).map(item=>normalizeItem(item,state.kind)).filter(Boolean),hasMore:raw.has_more===true,updatedAt:raw.updated_at,stale:raw.stale===true,mode:'search'};
      if(state.kind==='library' && Number.isInteger(raw.page) && raw.page>=1 && raw.page<=1000)state.page=raw.page;
    }catch{
      if(current!==state || state.generation!==generation || request!==state.request)return;
      state.error=true;
    }finally{
      clearTimeout(timeout);
      if(state.timer===timeout)state.timer=null;
      if(current===state && state.generation===generation && request===state.request){state.loading=false;render(state);}
    }
  }

  function submit(form) {
    const state=current;
    if(!state || !(form instanceof HTMLFormElement) || !form.matches('[data-media-form]') || !state.root.contains(form))return false;
    const query=form.elements.namedItem('query');
    const value=typeof query?.value==='string'?query.value.trim():'',feedback=state.root.querySelector('[data-media-search-feedback]');
    feedback.textContent='';
    if(value.length>80 || value.length===1 || /[\u0000-\u001f\u007f]/.test(value)){
      feedback.textContent=value.length>80?'片名最多填写 80 个字。':state.kind==='library'?'请输入至少 2 个字的片名，或清空返回浏览。':'请输入至少 2 个字的片名；清空可重置搜索。';query?.focus();return false;
    }
    state.query=value;
    state.page=1;
    if(state.kind==='library'){state.notice='';libraryUrl(state);}
    void load(state,state.kind==='library');return true;
  }

  function mount(kind) {
    stop();
    kind=kindOf(kind);
    const root=document.querySelector(`main.media-content[data-media-kind="${kind}"]`);
    if(!root)return;
    const params=new URLSearchParams(location.search),requestedId=params.get('library'),requestedPage=Number(params.get('page'));
    const state={root,kind,generation,request:0,query:'',type:'all',page:1,data:null,loading:false,error:false,controller:null,timer:null,cleanups:[],libraries:null,searchData:null,libraryId:'',sort:defaultLibrarySort,notice:''};
    if(kind==='library' && validLibraryId(requestedId)){state.libraryId=requestedId;state.sort=validSort(params.get('sort'))?params.get('sort'):defaultLibrarySort;state.page=Number.isInteger(requestedPage)&&requestedPage>=1&&requestedPage<=1000?requestedPage:1;}
    if(kind==='library'){
      const requestedQuery=(params.get('query')||'').trim();
      if(requestedQuery.length>=2&&requestedQuery.length<=80&&!/[\u0000-\u001f\u007f]/.test(requestedQuery))state.query=requestedQuery;
      root.querySelector('[name="query"]').value=state.query;
    }
    current=state;
    const click=event=>{
      if(!(event.target instanceof Element))return;
      const button=event.target.closest('[data-media-action],[data-media-filter]');
      if(!button || !root.contains(button) || button.disabled)return;
      if(state.kind==='library'){
        if(button.tagName==='A' && (event.ctrlKey||event.metaKey||event.shiftKey||event.altKey))return;
        event.preventDefault();
        const action=button.dataset.mediaAction;
        if(action==='film-detail'){openFilmDetail(state,button.dataset.filmId);return;}
        if(action==='open-library'){
          if(!state.libraries?.libraries.some(item=>item.id===button.dataset.libraryId))return;
          state.libraryId=button.dataset.libraryId;state.sort=defaultLibrarySort;state.page=1;state.notice='';
        }else if(action==='libraries'){state.libraryId='';state.page=1;state.notice='';}
        else if(action==='reset'){state.query='';state.page=1;state.notice='';root.querySelector('[name="query"]').value='';root.querySelector('[data-media-search-feedback]').textContent='';}
        else if(action==='prev'){if(state.loading||state.page<=1)return;state.page--;}
        else if(action==='next'){if(state.loading||!state.data?.hasMore||state.page>=1000)return;state.page++;}
        else if(action!=='retry')return;
        if(action!=='retry')libraryUrl(state);
        void load(state,action!=='retry');return;
      }
      event.preventDefault();
      if(button.dataset.mediaFilter){
        if(!['all','movie','tv'].includes(button.dataset.mediaFilter) || state.type===button.dataset.mediaFilter)return;
        state.type=button.dataset.mediaFilter;state.page=1;
      }else{
        const action=button.dataset.mediaAction;
        if(action==='prev'){if(state.loading||state.page<=1)return;state.page--;}
        else if(action==='next'){if(state.loading||!state.data?.hasMore)return;state.page++;}
        else if(action==='reset'){state.query='';state.page=1;root.querySelector('[name="query"]').value='';root.querySelector('[data-media-search-feedback]').textContent='';}
        else if(action!=='retry')return;
      }
      void load(state);
    };
    const error=event=>posterFailed(event.target);
    const change=event=>{
      if(state.kind!=='library' || state.query || !(event.target instanceof HTMLSelectElement) || !event.target.matches('[data-media-sort]') || !validSort(event.target.value) || !state.libraryId)return;
      state.sort=event.target.value;state.page=1;state.notice='';libraryUrl(state);void load(state,true);
    };
    root.addEventListener('click',click);root.addEventListener('error',error,true);
    root.addEventListener('change',change);
    state.cleanups.push(()=>root.removeEventListener('click',click),()=>root.removeEventListener('error',error,true),()=>root.removeEventListener('change',change));
    if(kind==='discovery')window.MediaDockSubscriptions?.mountPublic(root);
    if(kind==='library')window.MediaDockMediaStats?.mount(root);
    void load(state);
  }

  function stop() {
    window.MediaDockMediaStats?.stop();
    window.MediaDockSubscriptions?.stopPublic();
    generation++;
    if(!current)return;
    clearTimeout(current.timer);
    current.controller?.abort();
    for(const cleanup of current.cleanups)cleanup();
    current=null;
  }

  window.MediaDockMedia=Object.freeze({markup,mount,submit,stop});
}());
