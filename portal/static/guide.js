(function () {
  'use strict';

  const escape = value => String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);

  function userCenterLink(value) {
    if (typeof value !== 'string' || value.length > 2048 || /[\s\u0000-\u001f\u007f]/.test(value)) return '用户中心';
    try {
      const url = new URL(value);
      if (url.protocol !== 'https:' || !url.hostname || url.username || url.password) return '用户中心';
      return `<a href="${escape(url.href)}" target="_blank" rel="noopener noreferrer">用户中心<span aria-hidden="true"> ↗</span></a>`;
    } catch {
      return '用户中心';
    }
  }

  function markup({userCenterUrl} = {}) {
    const center = userCenterLink(userCenterUrl);
    return `<main class="guide-content" id="guide-top">
      <header class="guide-heading">
        <div class="eyebrow">MediaDock / 使用指南</div>
        <h1>先查片库，再选套餐。</h1>
        <p>购买前先搜索想看的影片，再按需选择套餐。查片无需购买天卡；想体验实际播放速度与流畅度时，再选择试用卡。</p>
        <div class="guide-heading-actions"><a class="btn btn-primary" href="/library">先查片库</a><a class="btn btn-outline" href="/plans">选择套餐</a></div>
      </header>
      <div class="guide-layout">
        <nav class="guide-toc" aria-label="使用指南目录"><span>阅读目录</span><ol>
          <li><a href="#guide-library">片库预览与求片订阅</a></li>
          <li><a href="#guide-plans">选择合适的套餐</a></li>
          <li><a href="#guide-purchase">下单与付款</a></li>
          <li><a href="#guide-claim">领取与兑换卡密</a></li>
          <li><a href="#guide-client">推荐软件与连接图解</a></li>
          <li><a href="#guide-status">看懂服务状态</a></li>
          <li><a href="#guide-faq">常见问题</a></li>
        </ol><a class="guide-toc-back" href="/">返回首页 <span aria-hidden="true">↗</span></a></nav>
        <div class="guide-articles">
          <section class="guide-section" id="guide-library" aria-labelledby="guide-library-title"><div class="guide-section-label">01 / DISCOVER</div><h2 id="guide-library-title">片库预览与求片订阅</h2><div class="guide-callout guide-purchase-notice"><strong>购买必看！</strong><p><strong>购买前，先到<a href="/library">片库预览</a>搜索想看的电影或剧集，查看已开放片库的收录情况。</strong></p><p><strong>只想确认库里有没有片，直接搜索即可，无需购买试用卡或天卡。</strong></p><p><strong>试用卡推荐用于测试实际播放速度、流畅度和客户端使用体验。</strong>需要亲自试播时，再按需选择。</p></div><p>除了直接搜索，也可以先选择媒体库封面，再浏览或搜索该库的影片海报。库内默认按最新入库排序，也可切换名称或上映年份，并翻页查看。若页面显示正在准备中，请稍后再来；可观看内容以片库和账号实际可见内容为准。</p><p><a href="/discover">求片订阅</a>通过输入片名搜索可订阅影片，并查看搜索结果中的入库和集数进度。总集数未知时，仅显示已知集数。访客可以点击“想看 / 订阅”提交想看申请，管理员通过后加入追更；在“我的想看申请”查看审核进度，记录仅在提交时的浏览器中可见。已有客户如需按用户中心的额度与审核规则订阅，请前往部署方提供的用户中心；站内直接连接功能需要另行集成。申请或订阅成功不代表已经入库，请以影片实际进度为准。取消或调整已有订阅，请前往用户中心或联系管理员。</p><p>首页电影海报是内容展示，不代表对应影片一定已入库。</p></section>
          <section class="guide-section" id="guide-plans" aria-labelledby="guide-plans-title"><div class="guide-section-label">02 / CHOOSE</div><h2 id="guide-plans-title">选择合适的套餐</h2><p><strong>自备网盘</strong>适合已有 115 网盘、希望使用自己网盘的用户；<strong>免自备网盘</strong>使用负载均衡模式，无需提供自己的 115 网盘。卡密用于兑换所选套餐的服务时长。</p>
            <div class="guide-table-wrap"><table class="guide-table"><caption class="visually-hidden">主要套餐的网盘要求和服务时长</caption><thead><tr><th scope="col">套餐</th><th scope="col">网盘要求</th><th scope="col">服务时长</th></tr></thead><tbody><tr><th scope="row">free 月卡</th><td>自备 115 网盘</td><td>30 天</td></tr><tr><th scope="row">VIP 体验卡</th><td>免自备网盘</td><td>1 天</td></tr><tr><th scope="row">SVIP 年卡</th><td>自备 115 网盘</td><td>365 天</td></tr><tr><th scope="row">VVIP 年卡</th><td>免自备网盘</td><td>365 天</td></tr></tbody></table></div><p class="guide-note">价格、是否可购买，以及其他体验规格的时长，以「观影套餐」页面或闲鱼实际选择的商品为准。网盘模式不同，请在付款前确认。</p>
          </section>
          <section class="guide-section" id="guide-purchase" aria-labelledby="guide-purchase-title"><div class="guide-section-label">03 / PURCHASE</div><h2 id="guide-purchase-title">下单与付款</h2><div class="guide-channel-grid"><article><h3>网页购买</h3><p>在「观影套餐」页面选择套餐并创建订单，按订单页展示的方式付款，保留订单信息。收款需人工核对；创建订单或提交付款信息，不代表已经到账或开通。</p><p>后台未配置网页收款时，创建待付款订单按钮不可用，请通过其它入口完成购买。</p></article><article><h3>闲鱼购买</h3><p>在闲鱼选择对应商品规格并付款。付款后留意订单与卖家聊天消息，按收到的卡密或领取链接操作。</p><p>付款与订单沟通请保留在原购买渠道，便于核对。</p></article></div><div class="guide-callout"><strong>支付后还没有收到卡密？</strong><p>先查看原订单的核对状态与聊天消息。核对尚未完成时，请保留订单号联系卖家，不要重复付款。</p></div></section>
          <section class="guide-section" id="guide-claim" aria-labelledby="guide-claim-title"><div class="guide-section-label">04 / ACTIVATE</div><h2 id="guide-claim-title">领取与兑换卡密</h2><ol class="guide-steps"><li><div><h3>取回你的卡密</h3><p>网页订单在到账确认后，按订单页提示领取。闲鱼订单可能直接收到卡密，也可能收到领取链接。若收到的是领取凭证，请到<a href="/claim">「领取卡密」</a>页面按提示操作。</p></div></li><li><div><h3>打开用户中心</h3><p>从网站导航进入${center}，按页面提示登录或完成所需账号步骤。领取凭证与兑换卡密用途不同，请先完成领取再兑换。</p></div></li><li><div><h3>兑换所购服务</h3><p>在用户中心按当前页面的兑换提示提交卡密，核对兑换结果、套餐和到期时间。若页面提示失败或卡密已使用，先检查兑换记录，再联系原购买渠道。</p></div></li></ol><p class="guide-note">领取凭证、领取链接和卡密请自行保管，不要公开转发。有效期和可用权限以兑换后用户中心显示为准。</p></section>
          <section class="guide-section" id="guide-client" aria-labelledby="guide-client-title">
            <div class="guide-section-label">05 / WATCH</div><h2 id="guide-client-title">推荐软件与连接图解</h2>
            <p>先在用户中心确认服务已开通，获取<strong>播放线路、播放账号和播放密码</strong>，再选择支持 Emby 的客户端。下面四款均支持 Emby；选择适合你设备的软件即可，无需全部安装。如需付费解锁播放器功能，费用与影视服套餐分开计算；具体功能与收费以软件当前说明为准。</p>
            <div class="guide-player-grid">
              <article class="guide-player-card"><span class="guide-player-label">注重播放与苹果设备协同</span><h3>Infuse</h3><p class="guide-player-devices">iPhone · iPad · Mac · Apple TV · Apple Vision</p><dl><dt>优点</dt><dd>支持多种视频、音轨与字幕格式，提供海报库、Trakt 和多设备同步，适合苹果设备间接续观看。</dd><dt>取舍</dt><dd>部分格式、音频与同步功能需要 Pro；实际 HDR、音频效果也受设备和片源限制。</dd></dl><details><summary>如何添加影视服</summary><ol><li>iPhone / iPad / Mac：设置 → 添加文件 → 媒体服务器 → 添加媒体服务器。Apple TV：设置 → 共享 → 添加媒体服务器。</li><li>选择 Emby；未自动发现时手动添加用户中心提供的远程地址。</li><li>按下方通用图解填写地址、端口和播放账号，保存后打开片库。</li></ol><a class="guide-client-flow" href="/static/guide-images/infuse-steps.svg" target="_blank" rel="noopener noreferrer" aria-label="放大查看 Infuse 连接步骤"><img src="/static/guide-images/infuse-steps.svg" alt="Infuse 连接路线图：添加 Emby 媒体服务器，填写播放线路和账号，保存后打开片库。" width="1000" height="880" loading="lazy"></a><a class="guide-source-link" href="/static/guide-images/infuse-steps.svg" download="影视服-Infuse-连接步骤.svg">保存步骤图片</a></details><a class="guide-source-link" href="https://apps.apple.com/app/id1136220934" target="_blank" rel="noopener noreferrer">查看官方商店介绍 ↗</a></article>
              <article class="guide-player-card"><span class="guide-player-label">片库与多种来源集中管理</span><h3>VidHub</h3><p class="guide-player-devices">Apple 设备 · Android / Android TV · 鸿蒙</p><dl><dt>优点</dt><dd>同时支持 Emby、Jellyfin、Plex 及网盘等来源，提供海报整理、字幕和 iCloud 同步，适合集中浏览不同片库。</dd><dt>取舍</dt><dd>来源类型较多，首次添加要选对 Emby；不同平台功能有差异，iCloud 同步适用于苹果设备。</dd></dl><details><summary>如何添加影视服</summary><ol><li>在当前版本中找到添加来源或媒体服务器入口；入口名称和可选服务可能因平台不同而变化。</li><li>选择 Emby（若该版本提供）；不要把它当作网盘、SMB 或 WebDAV 来源。</li><li>按下方图解填写播放地址与账号，登录后浏览服务器片库。</li></ol><a class="guide-client-flow" href="/static/guide-images/vidhub-steps.svg" target="_blank" rel="noopener noreferrer" aria-label="放大查看 VidHub 连接步骤"><img src="/static/guide-images/vidhub-steps.svg" alt="VidHub 连接路线图：添加 Emby 媒体服务器，填写播放线路和账号，保存后打开片库。" width="1000" height="880" loading="lazy"></a><a class="guide-source-link" href="/static/guide-images/vidhub-steps.svg" download="影视服-VidHub-连接步骤.svg">保存步骤图片</a></details><a class="guide-source-link" href="https://zh.okaapps.com/product/1659622164" target="_blank" rel="noopener noreferrer">查看官方软件介绍 ↗</a></article>
              <article class="guide-player-card"><span class="guide-player-label">喜欢追剧日历与观看记录</span><h3>Rex - 聚合媒体播放器</h3><p class="guide-player-devices">iPhone · iPad · M1 及以上 Mac · Apple Vision（兼容运行）</p><dl><dt>优点</dt><dd>结合追剧日历、观看统计、Trakt 与媒体服务器播放，适合管理追剧进度和想看清单。</dd><dt>取舍</dt><dd>本教程以 iPhone / iPad 版为准；追剧日历与提醒不等于影视服已入库，也不会自动替你提交求片申请。</dd></dl><details><summary>如何添加影视服</summary><ol><li>在媒体来源或服务器管理中找到新增入口。</li><li>选择 Emby，并使用用户中心提供的播放信息登录。</li><li>打开已连接的片库选择影片；需要求片时回到本站「求片订阅」。</li></ol><a class="guide-client-flow" href="/static/guide-images/rex-steps.svg" target="_blank" rel="noopener noreferrer" aria-label="放大查看 REX 连接步骤"><img src="/static/guide-images/rex-steps.svg" alt="Rex 连接路线图：添加 Emby 媒体服务器，填写播放线路和账号，保存后打开片库。" width="1000" height="880" loading="lazy"></a><a class="guide-source-link" href="/static/guide-images/rex-steps.svg" download="影视服-Rex-连接步骤.svg">保存步骤图片</a></details><a class="guide-source-link" href="https://apps.apple.com/app/id6801583759" target="_blank" rel="noopener noreferrer">查看官方商店介绍 ↗</a></article>
              <article class="guide-player-card"><span class="guide-player-label">在意字幕与播放细节</span><h3>SenPlayer</h3><p class="guide-player-devices">iPhone · iPad · Mac · Apple TV</p><dl><dt>优点</dt><dd>提供双字幕、字幕样式与时间调整、音轨切换、章节浏览，并支持多种媒体服务器来源。</dd><dt>取舍</dt><dd>播放选项较丰富，需要一点设置时间；Pro 有单平台与多平台授权，购买前确认覆盖的设备。画质增强也依赖设备性能。</dd></dl><details><summary>如何添加影视服</summary><ol><li>找到添加媒体服务器或来源入口。</li><li>服务类型选择 Emby，按下方图解填写地址、协议与端口。</li><li>用播放账号登录，进入片库试播；再按需要调整字幕与音轨。</li></ol><a class="guide-client-flow" href="/static/guide-images/senplayer-steps.svg" target="_blank" rel="noopener noreferrer" aria-label="放大查看 SenPlayer 连接步骤"><img src="/static/guide-images/senplayer-steps.svg" alt="SenPlayer 连接路线图：添加 Emby 媒体服务器，填写播放线路和账号，保存后打开片库。" width="1000" height="880" loading="lazy"></a><a class="guide-source-link" href="/static/guide-images/senplayer-steps.svg" download="影视服-SenPlayer-连接步骤.svg">保存步骤图片</a></details><a class="guide-source-link" href="https://apps.apple.com/app/id6443975850" target="_blank" rel="noopener noreferrer">查看官方商店介绍 ↗</a></article>
            </div>
            <p class="guide-note">资料依据各软件官方商店介绍核对至 2026 年 10 月 12 日。不同系统与版本的按钮名称可能不同，请寻找「添加来源 / 添加服务器 / Emby」等入口；以上步骤描述操作目的，配图为通用示意，非软件界面截图。</p>
            <h3 class="guide-subheading" id="guide-connection">通用教程：地址、协议、端口怎么填</h3>
            <ol class="guide-steps"><li><div><h3>先取得你的播放信息</h3><p>从用户中心复制当前播放线路和播放账号。不要填写本站购卡地址、用户中心网址、领取链接或管理后台地址。播放密码不是兑换卡密，也不是 115 Cookie。</p></div></li><li><div><h3>服务类型选 Emby</h3><p><strong>Emby 是服务类型，HTTP / HTTPS 是连接协议。</strong>选择 Emby 后，再按线路地址决定是否启用 HTTPS / SSL；不要选成 WebDAV、SMB、FTP 或 Emby Connect。</p></div></li><li><div><h3>看客户端是一栏还是分栏</h3><p>只有「服务器 URL / 完整地址」一栏时，填写完整地址；若分别有「主机、端口、HTTPS」选项，就拆开填写。名称或备注可以填「影视服」，不影响连接。</p></div></li></ol>
            <div class="guide-diagrams">
              <figure><a href="/static/guide-images/address-anatomy.svg" target="_blank" rel="noopener noreferrer" aria-label="打开完整地址拆解图"><img src="/static/guide-images/address-anatomy.svg" alt="示例地址 https://media.example.com:8443 分为 HTTPS 协议、media.example.com 主机和 8443 端口。示例不可连接。" width="1000" height="1100" loading="lazy"></a><figcaption>01 · 拆开看地址 <a href="/static/guide-images/address-anatomy.svg" download="影视服-地址拆解.svg">保存图片</a></figcaption></figure>
              <figure><a href="/static/guide-images/emby-fields.svg" target="_blank" rel="noopener noreferrer" aria-label="打开完整 Emby 表单填写图"><img src="/static/guide-images/emby-fields.svg" alt="分栏表单示意：类型 Emby，主机 media.example.com，HTTPS 开启，端口 8443，账号与密码使用自己的播放信息。" width="1000" height="1100" loading="lazy"></a><figcaption>02 · 对照填写表单 <a href="/static/guide-images/emby-fields.svg" download="影视服-Emby填写示意.svg">保存图片</a></figcaption></figure>
              <figure><a href="/static/guide-images/connection-check.svg" target="_blank" rel="noopener noreferrer" aria-label="打开完整协议与端口核对图"><img src="/static/guide-images/connection-check.svg" alt="端口核对：HTTPS 未指定端口用 443，HTTP 未指定端口用 80；地址写了端口就照填，不要擅自改成 8096 或 8920。" width="1000" height="1100" loading="lazy"></a><figcaption>03 · 核对协议与端口 <a href="/static/guide-images/connection-check.svg" download="影视服-协议端口核对.svg">保存图片</a></figcaption></figure>
            </div>
            <p class="guide-note">点击图片可放大查看或保存。media.example.com 仅用于示例，不是真实播放线路；请替换为用户中心给你的地址。</p>
            <div class="guide-table-wrap"><table class="guide-table guide-ports"><caption>照着你的线路填写；有指定端口时，优先使用指定值</caption><thead><tr><th scope="col">拿到的地址（示例）</th><th scope="col">协议 / SSL</th><th scope="col">分栏主机</th><th scope="col">端口</th></tr></thead><tbody><tr><th scope="row"><code>https://media.example.com</code></th><td data-label="协议 / SSL">HTTPS / 开启</td><td data-label="分栏主机">media.example.com</td><td data-label="端口">443</td></tr><tr><th scope="row"><code>https://media.example.com:8443</code></th><td data-label="协议 / SSL">HTTPS / 开启</td><td data-label="分栏主机">media.example.com</td><td data-label="端口">8443</td></tr><tr><th scope="row"><code>http://media.example.com:8096</code></th><td data-label="协议 / SSL">HTTP / 关闭</td><td data-label="分栏主机">media.example.com</td><td data-label="端口">8096</td></tr><tr><th scope="row"><code>http://media.example.com</code></th><td data-label="协议 / SSL">HTTP / 关闭</td><td data-label="分栏主机">media.example.com</td><td data-label="端口">80</td></tr></tbody></table></div>
            <p>若地址带有路径（例如 <code>/emby</code>），完整 URL 中需要保留；分栏表单有「路径 / Base URL」时单独填写。不要自行添加路径；遇到客户端无法填写的情况，按用户中心的客户端说明处理。</p>
            <div class="guide-callout"><strong>三个容易填错的地方</strong><p>分栏主机不要重复写 https:// 或端口；启用 HTTPS 不等于一律填 8920；8096 / 8920 是常见 Emby 服务端口，不代表你的公网线路使用它们。按发给你的完整地址填写，不要试着猜端口或关闭证书校验。</p></div>
            <h3 class="guide-subheading">保存后，先试播一部影片</h3><p>确认能登录、能看到片库，再播放影片并尝试拖动进度、切换字幕或音轨。能登录不代表每部影片都一定能播放。遇到问题时按下面排查：</p>
            <dl class="guide-definitions"><div><dt>连接超时 / 无法连接</dt><dd>先核对主机、协议和端口是否与用户中心一致，再试用户中心提供的另一条线路。不要自行填写服务器内网地址。</dd></div><div><dt>账号或密码错误</dt><dd>使用播放账号与播放密码，检查多余空格、服务是否开通或到期；不要用卡密代替密码。</dd></div><div><dt>证书错误</dt><dd>核对设备时间和线路域名；不要关闭证书校验，也不要擅自把 HTTPS 改为 HTTP。</dd></div><div><dt>能进片库但不能播放</dt><dd>记录片名、软件版本和错误提示，用同一账号对比另一部影片或已提供的线路；向卖家提供遮挡敏感信息后的截图。</dd></div></dl>
            <p class="guide-note">自备网盘套餐如需绑定 115，请在用户中心按指引操作；连接 Emby 客户端时不需要填写 115 Cookie。上述播放器功能不能替代套餐权限。</p>
          </section>
          <section class="guide-section" id="guide-status" aria-labelledby="guide-status-title"><div class="guide-section-label">06 / STATUS</div><h2 id="guide-status-title">看懂服务状态</h2><p><a href="/status">服务状态页</a>显示 CDN、源站和线路机的公开别名与监测结果。买家页面不展示源站或线路机的真实连接地址。</p><dl class="guide-definitions"><div><dt>CDN HTTP 响应</dt><dd>由你当前的浏览器测量，受本地网络和测速服务处理耗时影响。它不等于 Ping，也不是下载速度；“连接有响应”不代表完整播放正常。</dd></div><div><dt>探针响应 / 心跳</dt><dd>反映探针监测目标的响应或在线心跳，不是你到该服务器的延迟。心跳正常也不能单独证明播放体验。</dd></div><div><dt>服务端响应 / Ping</dt><dd>从监测服务器发起探测，和你当前网络的访问路径不同。</dd></div><div><dt>待接入 / 暂不可测</dt><dd>可能尚未配置监测，或当前没有获得有效数据。若实际使用异常，请结合客户端提示联系卖家，不必仅凭这一项判断服务是否可用。</dd></div></dl></section>
          <section class="guide-section guide-faq" id="guide-faq" aria-labelledby="guide-faq-title"><div class="guide-section-label">07 / HELP</div><h2 id="guide-faq-title">常见问题</h2><details><summary>付款后为什么还没有开通？</summary><p>网页收款需要人工核对。先查看订单是否已确认到账，收到卡密后还需到用户中心完成兑换。请勿重复支付。</p></details><details><summary>领取凭证和卡密是同一回事吗？</summary><p>不是。领取凭证或专属链接用于从订单中取回卡密；兑换卡密用于在用户中心开通对应服务。以收到的消息说明为准。</p></details><details><summary>套餐选错了，或者兑换时提示不匹配怎么办？</summary><p>先停止继续兑换，保留订单号，联系原购买渠道说明所购套餐和提示内容。不要将卡密公开发到评论区。</p></details><details><summary>显示“心跳正常”，为什么我还是无法观看？</summary><p>监测只反映被测项目的状态。请先核对账号是否已兑换、服务是否到期，以及客户端是否使用用户中心当前提供的连接信息，再提供具体错误提示排查。</p></details><details><summary>客户端应该填写哪个地址？</summary><p>以用户中心当前的客户端连接说明为准。不要使用管理后台地址、监测目标或自行猜测的服务器地址。</p></details><details><summary>找不到订单或领取消息怎么办？</summary><p>网页购买请保留订单页和订单信息；闲鱼购买请查看原交易订单与聊天记录。仍无法找到时，通过原购买渠道联系卖家核查。</p></details></section>
          <div class="guide-bottom"><p>准备好了，就从适合你的套餐开始。</p><a class="text-link" href="/plans">查看套餐 <span aria-hidden="true">→</span></a><a class="guide-back-top" href="#guide-top">回到顶部 ↑</a></div>
        </div>
      </div>
    </main>`;
  }

  window.MediaDockGuide = Object.freeze({markup});
}());
