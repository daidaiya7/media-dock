const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\nstart\(\);\s*$/,'');
function fixture(){
 const dom=new JSDOM('<meta name="description"><link rel="icon"><div id="app"></div><div id="modal-root"></div><div id="toast-root"></div>',{url:'https://portal.example/',runScripts:'outside-only'});
 dom.window.eval(source+';window.brandTest={applyAppearance,brand,homeHero,loadHomeFeatured,state,footer,userCenterLink};');return {w:dom.window,api:dom.window.brandTest,close:()=>dom.window.close()};
}
test('custom brand and homepage copy render safely with a same-origin logo',t=>{
 const f=fixture();t.after(f.close);f.api.applyAppearance({site_name:'我的影厅<script>x</script>',site_subtitle:'Cinema',site_description:'我的站点',logo_url:'/api/public/appearance-assets/logo.png',favicon_url:'https://images.example/icon.png',hero_title:'欢迎来到',hero_highlight:'我的影厅',hero_description:'第一行\n第二行',footer_text:'每晚一部好电影'});
 const app=f.w.document.querySelector('#app');app.innerHTML=f.api.brand()+f.api.homeHero()+f.api.footer();
 assert.equal(app.querySelector('.brand-name').firstChild.textContent,'我的影厅<script>x</script>');assert.equal(app.querySelectorAll('script').length,0);
 assert.match(app.querySelector('.brand-logo').src,/appearance-assets/);assert.match(app.querySelector('#home-title').textContent,/欢迎来到/);
 assert.equal(app.querySelector('.hero-copy').textContent,'第一行\n第二行');assert.match(f.w.document.title,/我的影厅/);
 assert.equal(f.w.document.querySelector('meta[name="description"]').content,'我的站点');
});
test('custom home slides bypass TMDB fetch and retain carousel semantics',async t=>{
 const f=fixture();t.after(f.close);f.api.applyAppearance({hero_images:[{url:'/static/custom.png',title:'自定义图',subtitle:'欢迎'}]});
 f.w.fetch=async()=>{throw Error('TMDB must not be requested');};const app=f.w.document.querySelector('#app');app.innerHTML=f.api.homeHero()+'<aside id="home-carousel-source"></aside>';
 await f.api.loadHomeFeatured(app.querySelector('#home-carousel'));
 assert.equal(app.querySelectorAll('[data-carousel-slide]').length,1);assert.equal(app.querySelector('.hero-slide-image').getAttribute('src'),'/static/custom.png');assert.equal(app.querySelector('#home-carousel-source').hidden,true);
});

test('custom slide alt text escapes the configured site name and title',async t=>{
 const f=fixture();t.after(f.close);
 const siteName='"><svg/onload=alert(1)>',title='" onerror="alert(2)';
 f.api.applyAppearance({site_name:siteName,hero_images:[{url:'/static/custom.png',title,subtitle:'欢迎'}]});
 const app=f.w.document.querySelector('#app');app.innerHTML=f.api.homeHero();
 await f.api.loadHomeFeatured(app.querySelector('#home-carousel'));
 const image=app.querySelector('.hero-slide-image');
 assert.equal(image.getAttribute('alt'),title+' · '+siteName);
 assert.equal(image.hasAttribute('onerror'),false);
 assert.equal(app.querySelector('[onload], [onerror]'),null);
 assert.equal(app.querySelector('[data-carousel-slide] svg'),null);
});

test('user center stays discoverable without configuration and uses configured URL',t=>{
 const f=fixture();t.after(f.close);const root=f.w.document.querySelector('#app');
 root.innerHTML=f.api.userCenterLink();assert.equal(root.querySelector('button').textContent,'用户中心');
 f.api.state.catalog={redemption_url:'https://users.example/'};root.innerHTML=f.api.userCenterLink();
 assert.equal(root.querySelector('a').href,'https://users.example/');assert.equal(root.querySelector('button'),null);
});
