'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(path.join(__dirname,'../static/media-catalog.js'),'utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const response=body=>({ok:true,status:200,json:async()=>body});
const item=(title,extra={})=>({id:title,title,type:'movie',year:2025,poster_url:null,...extra});
const payload=(items=[],extra={})=>({configured:true,items,page:1,has_more:false,updated_at:'2026-10-11T12:00:00Z',stale:false,...extra});
const library=(id,name=id,extra={})=>({id,name,cover_url:`/api/public/library/cover/${id}`,...extra});
const libraries=(rows=[library('a','精选电影'),library('b','剧集')],extra={})=>({configured:true,libraries:rows,updated_at:'2026-10-11T12:00:00Z',stale:false,...extra});
const searchGroup=(id,items,extra={})=>({library:{id,name:id},items,has_more:false,...extra});
const searchPayload=(groups=[],extra={})=>({configured:true,groups,updated_at:'2026-10-11T12:00:00Z',stale:false,partial:false,...extra});
const search=(f,value)=>{const form=f.root.querySelector('[data-media-form]');form.elements.query.value=value;return f.win.MediaDockMedia.submit(form);};
const selectedHandler=handler=>url=>url==='/api/public/libraries'?response(libraries()):handler(url);
const parameters=url=>new URL(url,'https://shop.example').searchParams;
const openLibrary=(f,id='a')=>f.root.querySelector(`[data-library-id="${id}"]`).click();
const sortBy=(f,value)=>{const select=f.root.querySelector('[data-media-sort]');select.value=value;select.dispatchEvent(new f.win.Event('change',{bubbles:true}));};

function fixture(kind='library',handler=()=>response(libraries()),suffix='') {
  const dom=new JSDOM('<div id="app"></div>',{url:`https://shop.example/${kind==='discovery'?'discover':kind}${suffix}`,runScripts:'outside-only'});
  const win=dom.window,calls=[];
  win.fetch=(url,options)=>{calls.push({url,options});return Promise.resolve(handler(url,options));};
  win.eval(source);
  win.document.getElementById('app').innerHTML=win.MediaDockMedia.markup(kind);
  win.MediaDockMedia.mount(kind);
  return {win,calls,root:win.document.querySelector('main'),close(){win.MediaDockMedia.stop();win.close();}};
}

test('library entry shows ordered library covers and names without requesting mixed films',async()=>{
  const f=fixture('library',()=>response(libraries([library('b','剧集'),library('a','精选电影')])));
  try{
    await flush();
    assert.deepEqual(f.calls.map(call=>call.url),['/api/public/libraries']);
    assert.deepEqual([...f.root.querySelectorAll('.library-cover-card h3')].map(el=>el.textContent),['剧集','精选电影']);
    assert.equal(f.root.querySelector('[data-library-toolbar]').hidden,true);
    assert.equal(f.root.querySelectorAll('.media-card,[data-media-filter],.media-card-meta').length,0);
    assert.equal(f.root.querySelectorAll('[data-media-form]').length,1);
    assert.equal(f.root.querySelectorAll('.library-cover-card img').length,2);
    assert(!f.root.textContent.includes('TMDB'));
  }finally{f.close();}
});

test('opening one library reads only that library and back returns to covers',async()=>{
  const f=fixture('library',selectedHandler(()=>response(payload([item('仅电影库片名',{overview:'不显示简介',genres:['不显示分类']})]))));
  try{
    await flush();openLibrary(f);await flush();
    const params=parameters(f.calls.at(-1).url);
    assert.equal(params.get('library'),'a');assert.equal(params.get('sort'),'newest');assert.equal(params.get('page'),'1');
    assert.equal(params.has('query'),false);assert.equal(params.has('type'),false);
    assert.equal(f.root.querySelector('[data-library-title]').textContent,'精选电影');
    assert.equal(f.root.querySelector('.library-film-title').textContent,'仅电影库片名');
    assert.equal(f.root.querySelectorAll('.media-card-meta,.media-description,.media-type-label,.media-genres').length,0);
    assert(!f.root.textContent.includes('不显示简介'));assert(!f.root.textContent.includes('2025'));
    assert.equal(f.win.location.search,'?library=a&sort=newest&page=1');
    f.root.querySelector('[data-media-action="libraries"]').click();await flush();
    assert.equal(f.win.location.pathname,'/library');assert.equal(f.win.location.search,'');
    assert.equal(f.root.querySelectorAll('.library-cover-card').length,2);assert.equal(f.root.querySelectorAll('.media-card').length,0);
    assert(f.calls.every(call=>!call.options.method&&!call.options.body&&call.options.credentials==='omit'));
  }finally{f.close();}
});

test('direct links restore library, sorting, and page; page changes and sort reset remain scoped',async()=>{
  const f=fixture('library',selectedHandler(url=>response(payload([item('影片')],{page:Number(parameters(url).get('page')),has_more:true}))), '?library=b&sort=year&page=3');
  try{
    await flush();
    assert.equal(f.root.querySelector('[data-library-title]').textContent,'剧集');
    assert.equal(f.root.querySelector('[data-media-sort]').value,'year');
    assert.match(f.root.querySelector('[data-media-pagination]').textContent,/第 3 页/);
    f.root.querySelector('[data-media-action="next"]').click();await flush();
    assert.equal(parameters(f.calls.at(-1).url).get('page'),'4');
    sortBy(f,'newest');await flush();
    const params=parameters(f.calls.at(-1).url);
    assert.equal(params.get('library'),'b');assert.equal(params.get('page'),'1');assert.equal(params.get('sort'),'newest');
    assert.equal(f.win.location.search,'?library=b&sort=newest&page=1');
    // Root's popstate handler remounts the module; it restores the prior URL state.
    f.win.history.replaceState(null,'','/library?library=a&sort=name&page=2');
    f.win.MediaDockMedia.mount('library');await flush();
    assert.equal(parameters(f.calls.at(-1).url).get('library'),'a');assert.equal(parameters(f.calls.at(-1).url).get('page'),'2');
  }finally{f.close();}
});

test('library links and direct entry default to newest while explicit name sort remains available',async()=>{
  const f=fixture('library',selectedHandler(()=>response(payload([item('新入库影片')]))));
  try{
    await flush();
    assert.equal(parameters(f.root.querySelector('.library-cover-card').href).get('sort'),'newest');
    openLibrary(f);await flush();
    assert.equal(f.root.querySelector('[data-media-sort]').value,'newest');
    sortBy(f,'name');await flush();
    assert.equal(parameters(f.calls.at(-1).url).get('sort'),'name');
    assert.equal(f.root.querySelector('[data-media-sort]').value,'name');
    f.win.history.replaceState(null,'','/library?library=b');
    f.win.MediaDockMedia.mount('library');await flush();
    assert.equal(parameters(f.calls.at(-1).url).get('sort'),'newest');
    assert.equal(f.root.querySelector('[data-media-sort]').value,'newest');
  }finally{f.close();}
});

test('invalid direct sort/page defaults to newest/page one and unknown generation returns to index',async()=>{
  const valid=fixture('library',selectedHandler(()=>response(payload([item('影片')]))),'?library=a&sort=anything&page=-9');
  try{await flush();const params=parameters(valid.calls.at(-1).url);assert.equal(params.get('sort'),'newest');assert.equal(params.get('page'),'1');}finally{valid.close();}
  const expired=fixture('library',()=>response(libraries()),'?library=old-generation&sort=year&page=9');
  try{
    await flush();assert.equal(expired.calls.length,1);assert.equal(expired.win.location.search,'');
    assert.match(expired.root.querySelector('[data-library-notice]').textContent,/已更新/);
    assert.equal(expired.root.querySelectorAll('.library-cover-card').length,2);
  }finally{expired.close();}
});

test('expired library HTTP400 reloads the index without displaying raw private diagnostics',async()=>{
  let indices=0;
  const f=fixture('library',url=>url==='/api/public/libraries'?response(++indices===1?libraries():libraries([library('new','新媒体库')])):{ok:false,status:400,json:async()=>({error:'internal.example secret'})},'?library=a');
  try{
    await flush();assert.equal(indices,2);assert.equal(f.win.location.search,'');
    assert.equal(f.root.querySelectorAll('.library-cover-card').length,1);assert.match(f.root.textContent,/新媒体库/);
    assert.match(f.root.textContent,/媒体库已更新/);assert(!f.root.innerHTML.includes('internal.example'));
  }finally{f.close();}
});

test('library pagination respects the server limit of 1000 pages',async()=>{
  const f=fixture('library',selectedHandler(url=>response(payload([item('影片')],{page:Number(parameters(url).get('page')),has_more:true}))),'?library=a&page=1000');
  try{
    await flush();const next=f.root.querySelector('[data-media-action="next"]');assert.equal(next.disabled,true);
    const before=f.calls.length;next.click();await flush();assert.equal(f.calls.length,before);
    f.win.history.replaceState(null,'','/library?library=a&page=1001');f.win.MediaDockMedia.mount('library');await flush();
    assert.equal(parameters(f.calls.at(-1).url).get('page'),'1');
  }finally{f.close();}
});

test('explicit library navigation scrolls to results and respects reduced motion without changing initial position',async()=>{
  const f=fixture('library',selectedHandler(()=>response(payload([item('影片')],{has_more:true}))));
  try{
    const scrolls=[];f.root.querySelector('.media-browser').scrollIntoView=options=>scrolls.push(options);
    f.win.matchMedia=()=>({matches:true});
    await flush();assert.equal(scrolls.length,0);
    openLibrary(f);await flush();assert.equal(scrolls.at(-1).block,'start');assert.equal(scrolls.at(-1).behavior,'auto');
    f.win.matchMedia=()=>({matches:false});sortBy(f,'year');await flush();assert.equal(scrolls.at(-1).behavior,'smooth');
    f.root.querySelector('[data-media-action="libraries"]').click();await flush();assert.equal(scrolls.length,3);
    f.win.MediaDockMedia.mount('library');await flush();assert.equal(scrolls.length,3);
  }finally{f.close();}
});

test('public names are escaped and only the exact cover proxy for each opaque ID is accepted',async()=>{
  const f=fixture('library',()=>response(libraries([
    library('valid','<img src=x onerror=alert(1)>'),
    library('private','Private',{cover_url:'https://internal.example/cover.jpg'}),
    library('cross','Cross',{cover_url:'/api/public/library/cover/valid'}),
    library('query','Query',{cover_url:'/api/public/library/cover/query?token=secret'}),
    library('invalid/path','Forbidden'),library('valid','duplicate'),
  ])));
  try{
    await flush();assert.equal(f.root.querySelectorAll('script,[onerror]').length,0);
    assert.equal(f.root.querySelectorAll('.library-cover-card').length,4);
    assert.equal(f.root.querySelector('h3').textContent,'<img src=x onerror=alert(1)>');
    assert.deepEqual([...f.root.querySelectorAll('img')].map(img=>img.getAttribute('src')),['/api/public/library/cover/valid']);
    assert(!f.root.innerHTML.includes('internal.example'));assert(!f.root.innerHTML.includes('token=secret'));
    const img=f.root.querySelector('img');img.dispatchEvent(new f.win.Event('error'));
    assert.equal(img.hidden,true);assert.equal(img.getAttribute('src'),null);
    assert.match(img.closest('.library-cover').textContent,/暂无媒体库封面/);
  }finally{f.close();}
});

test('film cards use escaped names and approved posters, with no additional film detail',async()=>{
  const f=fixture('library',selectedHandler(()=>response(payload([
    item('<img src=x onerror=alert(1)>',{overview:'<script>bad()</script>',poster_url:'https://private.example/secret.jpg'}),
    item('Bad host',{poster_url:'https://image.tmdb.org.evil.example/t/p/w500/x.jpg'}),
    item('Bad URL',{poster_url:'javascript:alert(1)'}),
    item('Provider poster must be rejected',{poster_url:'https://image.tmdb.org/t/p/w500/poster.jpg'}),
    item('Local poster',{poster_url:'/api/public/library/poster/abc123'}),
    item('Forbidden local',{poster_url:'/api/admin/secrets'}),
    item('Unsupported local alias',{poster_url:'/library/poster/abc123'}),
  ]))), '?library=a');
  try{
    await flush();assert.equal(f.root.querySelectorAll('script,[onerror],details,.media-card-meta,.media-type-label').length,0);
  assert.deepEqual([...f.root.querySelectorAll('img')].map(img=>img.getAttribute('src')),['/api/public/library/poster/abc123']);
    assert(!f.root.innerHTML.includes('private.example'));assert.equal(f.root.querySelector('.library-film-title').textContent,'<img src=x onerror=alert(1)>');
    const img=f.root.querySelector('img');img.dispatchEvent(new f.win.Event('error'));
    assert.equal(img.hidden,true);assert.equal(img.getAttribute('src'),null);assert(f.root.querySelector('.media-poster-placeholder'));
  }finally{f.close();}
});

test('a late result from an earlier sort cannot replace current library results',async()=>{
  const pending=[];
  const f=fixture('library',selectedHandler(()=>new Promise(resolve=>pending.push(resolve))));
  try{
    await flush();openLibrary(f);await flush();sortBy(f,'year');await flush();
    assert.equal(f.calls[1].options.signal.aborted,true);
    pending[1](response(payload([item('new result')])));await flush();
    pending[0](response(payload([item('old result')])));await flush();
    assert.match(f.root.textContent,/new result/);assert(!f.root.textContent.includes('old result'));
    assert.equal(f.root.querySelector('[data-media-sort]').value,'year');
  }finally{f.close();}
});

test('returning to index aborts a pending library and ignores its eventual response',async()=>{
  let resolve;
  const f=fixture('library',selectedHandler(()=>new Promise(value=>{resolve=value;})));
  try{
    await flush();openLibrary(f);await flush();
    f.root.querySelector('[data-media-action="libraries"]').click();await flush();
    assert.equal(f.calls[1].options.signal.aborted,true);
    resolve(response(payload([item('late film')])));await flush();
    assert.equal(f.root.querySelectorAll('.library-cover-card').length,2);assert(!f.root.textContent.includes('late film'));
    assert.equal(f.win.location.search,'');
  }finally{f.close();}
});

test('stop aborts reads, removes local controls, and prevents subsequent fetches after late index response',async()=>{
  let resolve;
  const f=fixture('library',()=>new Promise(value=>{resolve=value;}),'?library=a');
  try{
    const before=f.root.innerHTML;
    f.win.MediaDockMedia.stop();assert.equal(f.calls[0].options.signal.aborted,true);
    f.root.querySelector('[data-media-action="libraries"]').click();sortBy(f,'year');assert.equal(f.calls.length,1);
    resolve(response(libraries()));await flush();
    assert.equal(f.root.innerHTML,before);assert.equal(f.calls.length,1);
  }finally{f.close();}
});

test('empty index, empty selected library and unconfigured states have distinct safe guidance',async()=>{
  const emptyIndex=fixture('library',()=>response(libraries([])));
  try{await flush();assert.match(emptyIndex.root.textContent,/暂时没有可浏览的媒体库/);assert.equal(emptyIndex.calls.length,1);}finally{emptyIndex.close();}
  const emptyLibrary=fixture('library',selectedHandler(()=>response(payload([]))),'?library=a');
  try{await flush();assert.match(emptyLibrary.root.textContent,/这个媒体库暂时没有影片/);assert.equal(emptyLibrary.root.querySelector('[data-media-pagination]').hidden,true);}finally{emptyLibrary.close();}
  const unconfigured=fixture('library',()=>response(libraries([],{configured:false,token:'secret',origin:'https://internal.example'})));
  try{await flush();assert.match(unconfigured.root.textContent,/片库正在准备中/);assert(!unconfigured.root.innerHTML.includes('secret'));assert(!unconfigured.root.innerHTML.includes('internal.example'));}finally{unconfigured.close();}
});

test('a failing library request retains a retry and usable return control without leaking diagnostics',async()=>{
  const f=fixture('library',selectedHandler(()=>({ok:false,status:503,json:async()=>({error:'internal.example secret'})})),'?library=a');
  try{await flush();assert.match(f.root.textContent,/暂时没能读取影片/);assert.equal(f.root.querySelector('[data-library-toolbar]').hidden,false);assert(f.root.querySelector('[data-media-action="retry"]'));assert(!f.root.innerHTML.includes('internal.example'));}finally{f.close();}
});

test('discovery starts with search guidance and never loads the current subscription list',async()=>{
  const f=fixture('discovery',()=>response(payload([item('Private subscription')],{mode:'subscriptions'})));
  try{
    await flush();assert.equal(f.calls.length,0);assert.equal(f.root.querySelectorAll('.media-card').length,0);
    assert.match(f.root.querySelector('[data-media-results]').textContent,/搜索你想看的影片/);
    assert.equal(f.root.querySelector('[data-media-results]').getAttribute('aria-busy'),'false');
    assert.equal(f.root.querySelector('[data-media-attribution]').hidden,true);
    assert(!f.root.textContent.includes('正在追更'));assert(!f.root.textContent.includes('Private subscription'));
    for(const type of ['tv','movie','all'])f.root.querySelector(`[data-media-filter="${type}"]`).click();
    await flush();assert.equal(f.calls.length,0);assert.equal(f.root.querySelectorAll('.media-card').length,0);
  }finally{f.close();}
});

test('discovery search renders unknown counts without inventing a percentage',async()=>{
  const f=fixture('discovery',()=>response(payload([
    item('Unknown total',{type:'tv',subscribed:true,local_episodes:5,total_episodes:null}),
    item('Known total',{type:'tv',subscribed:true,local_episodes:3,total_episodes:10}),
    item('Inconsistent total',{type:'tv',in_library:true,local_episodes:12,total_episodes:10}),
    item('Movie',{in_library:true,local_episodes:1,total_episodes:1}),
  ],{mode:'search'})));
  try{
    search(f,'影片');await flush();const cards=f.root.querySelectorAll('.media-card');
    assert.match(cards[0].textContent,/已入库 5 集 · 总集数待更新/);assert.equal(cards[0].querySelectorAll('progress').length,0);
    assert.equal(cards[1].querySelector('progress').value,3);assert.equal(cards[1].querySelector('progress').max,10);
    assert.equal(cards[2].querySelectorAll('progress').length,0);assert.equal(cards[3].querySelectorAll('progress').length,0);
    assert.equal(f.root.querySelectorAll('[data-media-action="subscribe"]').length,0);
    assert(f.calls.every(call=>call.url.startsWith('/api/public/discovery?')&&!call.options.body&&parameters(call.url).get('query')==='影片'));
  }finally{f.close();}
});

test('discovery filters search results and reset makes no unfiltered request',async()=>{
  const f=fixture('discovery',url=>response(payload(parameters(url).get('query')==='empty'?[]:[item('Movie',{subscribed:false,in_library:false})],{mode:'search'})));
  try{
    await flush();f.root.querySelector('[data-media-filter="tv"]').click();assert.equal(f.calls.length,0);
    search(f,'movie');await flush();assert.equal(parameters(f.calls.at(-1).url).get('type'),'tv');
    assert.match(f.root.textContent,/可订阅/);assert.match(f.root.textContent,/访客申请需审核，客户订阅沿用用户中心规则/);
    assert.equal(f.root.querySelector('[data-discovery-clear]').hidden,false);
    f.root.querySelector('[data-media-filter="movie"]').click();await flush();assert.equal(parameters(f.calls.at(-1).url).get('query'),'movie');assert.equal(parameters(f.calls.at(-1).url).get('type'),'movie');
    search(f,'empty');await flush();assert.match(f.root.textContent,/没有找到匹配/);
    const count=f.calls.length;f.root.querySelector('[data-discovery-clear]').click();await flush();assert.equal(f.calls.length,count);
    assert.match(f.root.querySelector('[data-media-results]').textContent,/搜索你想看的影片/);assert.equal(f.root.querySelector('[data-discovery-clear]').hidden,true);
    assert.equal(f.root.querySelector('[name="query"]').value,'');assert.equal(f.root.querySelector('[data-media-results-count]').textContent,'');assert.equal(f.root.querySelector('[data-media-updated]').textContent,'');
    assert(f.calls.every(call=>parameters(call.url).get('query').length>=2&&!parameters(call.url).has('library')&&!parameters(call.url).has('page')));
  }finally{f.close();}
});

test('discovery enforces 2–80 clean characters and credits TMDB only after search',async()=>{
  const f=fixture('discovery',()=>response(payload([item('Valid movie')],{mode:'search'})));
  try{
    await flush();const form=f.root.querySelector('[data-media-form]');
    assert.equal(form.elements.query.minLength,2);assert.equal(form.elements.query.maxLength,80);assert.equal(f.root.querySelector('[data-media-attribution]').hidden,true);
    form.elements.query.value='一';assert.equal(f.win.MediaDockMedia.submit(form),false);assert.equal(f.calls.length,0);assert.match(f.root.querySelector('[data-media-search-feedback]').textContent,/至少 2/);
    for(const invalid of ['x'.repeat(81),'bad\u0000query'.replace('\\u0000','\u0000')]){form.elements.query.value=invalid;assert.equal(f.win.MediaDockMedia.submit(form),false);assert.equal(f.calls.length,0);}
    assert.equal(search(f,'影片'),true);await flush();assert.equal(f.root.querySelector('[data-media-attribution]').hidden,false);assert.match(f.root.querySelector('[data-media-attribution]').textContent,/not endorsed or certified by TMDB/);
    form.elements.query.value='';assert.equal(f.win.MediaDockMedia.submit(form),true);await flush();assert.equal(f.calls.length,1);assert.equal(f.root.querySelectorAll('.media-card').length,0);assert.equal(f.root.querySelector('[data-media-attribution]').hidden,true);
  }finally{f.close();}
  const failure=fixture('discovery',()=>({ok:false,json:async()=>({error:'secret internal.example'})}));
  try{search(failure,'影片');await flush();assert.match(failure.root.textContent,/暂时没能读取影片/);assert(!failure.root.innerHTML.includes('internal.example'));search(failure,'');await flush();assert.match(failure.root.textContent,/搜索你想看的影片/);assert.equal(failure.calls.length,1);}finally{failure.close();}
});

test('discovery ignores aborted earlier searches and escapes query/result content',async()=>{
  const pending=[];
  const f=fixture('discovery',()=>new Promise(resolve=>pending.push(resolve)));
  try{
    assert.equal(f.calls.length,0);search(f,'first');search(f,'a&<b>');
    assert.equal(f.calls[0].options.signal.aborted,true);assert.equal(parameters(f.calls[1].url).get('query'),'a&<b>');
    pending[1](response(payload([item('<img src=x onerror=bad()>')],{mode:'search'})));await flush();
    pending[0](response(payload([item('stale')],{mode:'search'})));await flush();
    assert.equal(f.root.querySelector('h3').textContent,'<img src=x onerror=bad()>');assert.equal(f.root.querySelectorAll('[onerror]').length,0);assert(!f.root.textContent.includes('stale'));
  }finally{f.close();}
});

test('clearing an in-flight discovery search invalidates success and failure responses',async()=>{
  let resolve,reject;const f=fixture('discovery',()=>new Promise((yes,no)=>{resolve=yes;reject=no;}));
  try{
    search(f,'影片');assert.equal(f.root.querySelector('[data-discovery-clear]').hidden,false);f.root.querySelector('[data-discovery-clear]').click();assert.equal(f.calls[0].options.signal.aborted,true);
    resolve(response(payload([item('Late private result')],{mode:'search'})));await flush();assert.equal(f.root.querySelectorAll('.media-card').length,0);assert(!f.root.textContent.includes('Late private result'));assert.match(f.root.textContent,/搜索你想看的影片/);
    search(f,'电影');search(f,'');reject(new Error('late failed'));await flush();assert.equal(f.root.querySelector('[data-media-action="retry"]'),null);assert.match(f.root.textContent,/搜索你想看的影片/);assert.equal(f.calls.length,2);
  }finally{f.close();}
});

test('discovery refuses a legacy current-subscriptions response even during a search',async()=>{
  const f=fixture('discovery',()=>response(payload([item('Never expose subscribed list')],{mode:'subscriptions'})));
  try{search(f,'电影');await flush();assert.match(f.root.textContent,/暂时没能读取影片/);assert(!f.root.textContent.includes('Never expose'));assert.equal(f.root.querySelectorAll('.media-card').length,0);}finally{f.close();}
});

test('entry searches public libraries in configured order with at most six poster-only cards per group',async()=>{
  const f=fixture('library',url=>url==='/api/public/libraries'?response(libraries()):response(searchPayload([
    searchGroup('b',[item('剧集结果')]),searchGroup('a',Array.from({length:8},(_,i)=>item(`电影结果${i}`,{overview:'隐藏简介'})),{has_more:true}),
  ])));
  try{
    await flush();assert.equal(search(f,'电影'),true);await flush();
    assert.equal(f.calls.at(-1).url,'/api/public/library/search?query=%E7%94%B5%E5%BD%B1');
    assert.equal(f.win.location.search,'?query=%E7%94%B5%E5%BD%B1');
    const groups=f.root.querySelectorAll('.library-search-group');assert.equal(groups.length,2);
    assert.equal(groups[0].querySelector('.library-search-group-heading h3').textContent,'精选电影');
    assert.equal(groups[0].querySelectorAll('.media-card').length,6);assert.equal(groups[1].querySelectorAll('.media-card').length,1);
    assert.equal(f.root.querySelector('[data-media-results-count]').textContent,'2 个媒体库 · 已展示 7 部');
    assert.equal(f.root.querySelectorAll('.media-card-meta,.media-description,.media-type-label').length,0);
    assert.equal(f.root.querySelector('[data-media-pagination]').hidden,true);assert(!f.root.textContent.includes('隐藏简介'));
    assert.equal(parameters(groups[0].querySelector('a').getAttribute('href')).get('query'),'电影');
    assert(f.calls.every(call=>!call.options.body&&!call.options.method));
  }finally{f.close();}
});

test('search drill-down, pagination, return and URL remount retain the query without offering ineffective sorting',async()=>{
  const f=fixture('library',url=>{
    if(url==='/api/public/libraries')return response(libraries());
    if(url.startsWith('/api/public/library/search?'))return response(searchPayload([searchGroup('a',[item('匹配电影')],{has_more:true})]));
    return response(payload([item('匹配电影')],{page:Number(parameters(url).get('page')),has_more:true}));
  },'?query=%E5%8C%B9%E9%85%8D');
  try{
    await flush();assert.equal(f.root.querySelector('[name="query"]').value,'匹配');openLibrary(f);await flush();
    let params=parameters(f.calls.at(-1).url);assert.equal(params.get('library'),'a');assert.equal(params.get('query'),'匹配');assert.equal(params.get('sort'),'newest');
    f.root.querySelector('[data-media-action="next"]').click();await flush();params=parameters(f.calls.at(-1).url);assert.equal(params.get('page'),'2');assert.equal(params.get('query'),'匹配');
    assert.equal(f.root.querySelector('[data-media-sort]'),null);assert.match(f.root.querySelector('[data-library-search-order]').textContent,/搜索结果按匹配顺序排列/);
    f.root.querySelector('[data-media-action="libraries"]').click();await flush();assert.equal(f.win.location.search,'?query=%E5%8C%B9%E9%85%8D');assert.equal(f.root.querySelectorAll('.library-search-group').length,1);
    f.win.history.replaceState(null,'','/library?library=a&query=matching&sort=newest&page=3');f.win.MediaDockMedia.mount('library');await flush();
    assert.equal(f.root.querySelector('[name="query"]').value,'matching');params=parameters(f.calls.at(-1).url);assert.equal(params.get('query'),'matching');assert.equal(params.get('page'),'3');assert.equal(params.get('sort'),'newest');
    assert.equal(f.root.querySelector('[data-media-sort]'),null);
    f.root.querySelector('.library-search-clear').click();await flush();assert.equal(f.root.querySelector('[data-media-sort]').value,'newest');
  }finally{f.close();}
});

test('search replaces the sorting control and clearing restores the existing browsing preference',async()=>{
  const f=fixture('library',selectedHandler(url=>response(payload([item('Second match'),item('First match')],{page:Number(parameters(url).get('page')),has_more:true}))),'?library=a&sort=year&page=2');
  try{
    await flush();assert.equal(f.root.querySelector('[data-media-sort]').value,'year');
    search(f,'match');await flush();assert.equal(f.root.querySelector('[data-media-sort]'),null);
    assert.equal(f.root.querySelector('[data-library-search-order]').textContent,'搜索结果按匹配顺序排列');
    assert.equal(parameters(f.win.location.href).get('sort'),'year');
    assert.deepEqual([...f.root.querySelectorAll('.library-film-title')].map(el=>el.textContent),['Second match','First match']);
    assert.equal(f.root.querySelectorAll('.media-card-meta,.media-description,.media-type-label').length,0);
    f.root.querySelector('[data-media-action="next"]').click();await flush();
    assert.equal(parameters(f.calls.at(-1).url).get('query'),'match');assert.equal(parameters(f.calls.at(-1).url).get('page'),'2');
    f.root.querySelector('.library-search-clear').click();await flush();
    assert.equal(f.root.querySelector('[data-library-search-order]'),null);assert.equal(f.root.querySelector('[data-media-sort]').value,'year');
    assert.equal(parameters(f.calls.at(-1).url).has('query'),false);assert.equal(parameters(f.calls.at(-1).url).get('sort'),'year');
    sortBy(f,'newest');await flush();assert.equal(parameters(f.calls.at(-1).url).get('sort'),'newest');
  }finally{f.close();}
});

test('empty search returns index covers or the current library browsing scope',async()=>{
  const f=fixture('library',url=>url==='/api/public/libraries'?response(libraries()):url.startsWith('/api/public/library/search?')?response(searchPayload([searchGroup('a',[item('匹配')])])):response(payload([item('影片')])));
  try{
    await flush();search(f,'匹配');await flush();f.root.querySelector('.library-search-clear').click();await flush();
    assert.equal(f.win.location.search,'');assert.equal(f.root.querySelectorAll('.library-cover-card').length,2);assert.equal(f.root.querySelector('[name="query"]').value,'');
    openLibrary(f);await flush();search(f,'影片');await flush();assert.equal(parameters(f.calls.at(-1).url).get('query'),'影片');
    search(f,'');await flush();assert.equal(parameters(f.calls.at(-1).url).get('library'),'a');assert.equal(parameters(f.calls.at(-1).url).has('query'),false);assert.equal(f.root.querySelectorAll('.media-card').length,1);
  }finally{f.close();}
});

test('library queries require 2–80 characters and do not make requests for invalid or control input',async()=>{
  const f=fixture();
  try{
    await flush();const count=f.calls.length,form=f.root.querySelector('[data-media-form]');
    assert.equal(form.elements.query.minLength,2);assert.equal(form.elements.query.maxLength,80);
    for(const query of ['一','x'.repeat(81),'ab\u0001cd']){assert.equal(search(f,query),false);assert.equal(f.calls.length,count);}
    assert.match(f.root.querySelector('[data-media-search-feedback]').textContent,/至少 2/);
  }finally{f.close();}
  const invalid=fixture('library',()=>response(libraries()),'?query=x');
  try{await flush();assert.equal(invalid.calls.length,1);assert.equal(invalid.root.querySelector('[name="query"]').value,'');}finally{invalid.close();}
});

test('partial search reports missing libraries and never claims a definitive no-result state',async()=>{
  const f=fixture('library',url=>url==='/api/public/libraries'?response(libraries()):response(searchPayload([],{partial:true})),'?query=missing');
  try{
    await flush();assert.match(f.root.textContent,/部分媒体库暂时未能搜索，可重试/);assert.match(f.root.textContent,/还有媒体库未完成搜索/);assert(!f.root.textContent.includes('没有找到匹配的影片'));
    const before=f.calls.length;f.root.querySelector('.library-search-partial button').click();await flush();assert.equal(f.calls.length,before+2);
  }finally{f.close();}
  const empty=fixture('library',url=>url==='/api/public/libraries'?response(libraries()):response(searchPayload([])),'?query=missing');
  try{await flush();assert.match(empty.root.textContent,/没有找到匹配的影片/);assert.equal(empty.root.querySelectorAll('.library-search-partial').length,0);}finally{empty.close();}
});

test('search ignores unselected libraries, escapes names, and exposes no unknown DTO fields',async()=>{
  const f=fixture('library',url=>url==='/api/public/libraries'?response(libraries([library('a','<img src=x onerror=bad()>')])):response(searchPayload([
    searchGroup('a',[item('<script>title</script>',{poster_url:'https://internal.example/poster'})],{library:{id:'a',name:'https://internal.example/name'}}),
    searchGroup('private',[item('Private title')]),
  ],{secret:'token'})),'?query=search');
  try{
    await flush();assert.equal(f.root.querySelectorAll('.library-search-group').length,1);assert.equal(f.root.querySelectorAll('script,[onerror]').length,0);assert(!f.root.innerHTML.includes('internal.example'));assert(!f.root.textContent.includes('Private title'));assert(!f.root.innerHTML.includes('token'));
    assert.match(f.root.textContent,/部分媒体库暂时未能搜索/);assert.equal(f.root.querySelector('.library-film-title').textContent,'<script>title</script>');
  }finally{f.close();}
});

test('late cross-library search cannot overwrite a newer search or clearing the query',async()=>{
  const pending=[];
  const f=fixture('library',url=>url==='/api/public/libraries'?response(libraries()):new Promise(resolve=>pending.push(resolve)));
  try{
    await flush();search(f,'old');await flush();const oldCall=f.calls.at(-1);search(f,'new');await flush();assert.equal(oldCall.options.signal.aborted,true);
    pending[1](response(searchPayload([searchGroup('a',[item('New search')])])));await flush();pending[0](response(searchPayload([searchGroup('a',[item('Old search')])])));await flush();
    assert.match(f.root.textContent,/New search/);assert(!f.root.textContent.includes('Old search'));
    search(f,'waiting');await flush();search(f,'');await flush();pending[2](response(searchPayload([searchGroup('a',[item('Late search')])])));await flush();
    assert.equal(f.root.querySelectorAll('.library-cover-card').length,2);assert(!f.root.textContent.includes('Late search'));assert.equal(f.win.location.search,'');
  }finally{f.close();}
});

test('search failure remains an error and generation expiry can safely resume a global search',async()=>{
  const failure=fixture('library',url=>url==='/api/public/libraries'?response(libraries()):{ok:false,status:503,json:async()=>({error:'secret'})},'?query=search');
  try{await flush();assert.match(failure.root.textContent,/暂时没能完成搜索/);assert(!failure.root.textContent.includes('没有找到匹配'));assert(!failure.root.innerHTML.includes('secret'));}finally{failure.close();}
  const f=fixture('library',url=>url==='/api/public/libraries'?response(libraries()):url.startsWith('/api/public/library/search?')?response(searchPayload([searchGroup('a',[item('Global match')])])):{ok:false,status:400},'?library=a&query=search');
  try{await flush();assert.equal(f.win.location.search,'?query=search');assert.match(f.root.textContent,/媒体库已更新/);assert.match(f.root.textContent,/Global match/);}finally{f.close();}
});

test('library statistics hook mounts only on library and hides for search and selected libraries',async()=>{
  const dom=new JSDOM('<div id="app"></div>',{url:'https://shop.example/library',runScripts:'outside-only'}),win=dom.window,events=[];
  win.MediaDockMediaStats={markup:()=>'<section data-fixture-stats>片库统计</section>',mount:root=>events.push(['mount',Boolean(root.querySelector('[data-fixture-stats]'))]),setVisible:value=>events.push(['visible',value]),stop:()=>events.push(['stop'])};
  win.fetch=async url=>response(url==='/api/public/libraries'?libraries():url.startsWith('/api/public/library/search?')?searchPayload([]):payload([item('影片')]));
  try{
    win.eval(source);const app=win.document.querySelector('#app');app.innerHTML=win.MediaDockMedia.markup('library');win.MediaDockMedia.mount('library');await flush();assert(events.some(event=>event[0]==='mount'&&event[1]));assert.deepEqual(events.at(-1),['visible',true]);
    win.document.querySelector('[data-library-id="a"]').click();await flush();assert.deepEqual(events.at(-1),['visible',false]);
    win.document.querySelector('[data-media-action="libraries"]').click();await flush();assert.deepEqual(events.at(-1),['visible',true]);
    const form=win.document.querySelector('[data-media-form]');form.elements.query.value='影片';win.MediaDockMedia.submit(form);await flush();assert.deepEqual(events.at(-1),['visible',false]);
    win.MediaDockMedia.stop();assert.deepEqual(events.at(-1),['stop']);events.length=0;
    app.innerHTML=win.MediaDockMedia.markup('discovery');win.MediaDockMedia.mount('discovery');await flush();assert.equal(app.querySelector('[data-fixture-stats]'),null);assert(!events.some(event=>event[0]==='mount'));
  }finally{win.MediaDockMedia?.stop();win.close();}
});

test('film details open from the selected card without another request and escape metadata',async()=>{
  const f=fixture('library',selectedHandler(()=>response(payload([item('A film',{id:'film-a',year:2024,overview:'<script>unsafe()</script>',genres:['剧情'],poster_url:'/api/public/library/poster/film-a'})]))),'?library=a');
  try{
    await flush();let content='';f.win.showModal=html=>{content=html;};
    const before=f.calls.length;
    const button=f.root.querySelector('[data-media-action="film-detail"]');
    assert.equal(button.tagName,'BUTTON');button.click();
    assert.match(content,/A film/);assert.match(content,/2024/);assert.match(content,/剧情/);
    assert.match(content,/&lt;script&gt;/);assert(!content.includes('<script>'));
    const detail=f.win.document.createElement('div');detail.innerHTML=content;
    const actions=detail.querySelectorAll('.film-detail-actions a');
    assert.equal(actions.length,1);assert.equal(actions[0].textContent,'购卡套餐');
    assert.equal(actions[0].getAttribute('href'),'/plans');assert.equal(f.calls.length,before);
  }finally{f.close();}
});
