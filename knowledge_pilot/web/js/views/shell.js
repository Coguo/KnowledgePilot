/**
 * 外壳:挂载点、侧栏主题列表、路由。
 *
 * **这是全前端仅有的两个允许用 `document.getElementById` 的文件之一**
 * (另一个是 `app.js`)。理由见 `js/dom.js`:同 id 元素出现两次时
 * `getElementById` 返回文档序第一个,「助手里的发送按钮控制了 Inspector 的输入框」
 * 这类 bug 静态检查看不出来。view 内部一律走 `KP.dom.q(root, name)`。
 *
 * 与拆分前的一处**有意差异**:hash 从 `#/t/<topic>/n/<node>` 改成 `#/g/<topic>/n/<node>`。
 * 旧格式没有数据迁移价值(它指向的是临时选择而非持久实体),`parseHash` 对未知
 * 前缀会安全落回图谱首页,不会白屏。提早在同一个改动里换掉,免得后面再迁一次。
 */
(function (KP) {
  'use strict';

  const group = (KP.views = KP.views || {});

  // 挂载点。**加载期不解析** —— 全部在 init() 里取,否则违反「顶层只做定义」。
  const el = { app: null, stage: null, topics: null, panel: null, nav: null,
               progress: null, assistant: null, rzSide: null, rzInsp: null };

  function init() {
    el.app = document.getElementById('app');
    el.stage = document.getElementById('stage');
    el.topics = document.getElementById('topics');
    el.panel = document.getElementById('panel');
    el.nav = document.getElementById('nav');
    el.progress = document.getElementById('progress');
    el.assistant = document.getElementById('assistant');
    el.rzSide = document.getElementById('rz-side');
    el.rzInsp = document.getElementById('rz-insp');
    renderNav();

    // 三栏宽度:先把记忆里的宽度落到界面上,再把分隔条绑上。
    applyColumnPrefs();
    wireColumns();

    // 事件委托绑在侧栏容器上:主题卡片是整段重建的,绑到卡片上会在重绘后失效。
    el.topics.addEventListener('click', onTopicsClick);

    const form = document.getElementById('new-topic');
    form.addEventListener('submit', onNewTopic);

    window.addEventListener('hashchange', () => { route(); });
    // 窗口变窄之后,记忆里的栏宽可能已经把画布挤没了 —— 重新夹一次就回来。
    window.addEventListener('resize', applyColumnPrefs);
  }

  /** 供其它 view 取容器(它们自己不做 getElementById)。 */
  const stage = () => el.stage;
  const panel = () => el.panel;
  const appEl = () => el.app;
  const assistant = () => el.assistant;

  // ================= 三栏宽度(可拖拽分隔条)=================
  //
  // 宽度写成 **`.app` 上的内联 CSS 变量**,而不是 `:root` 上的:1280 那个断点把
  // `--side-w` / `--insp-w` 重新定义在 `:root` 上(见 `css/layout.css`),写在元素上的
  // 值在里面一层、任何断点都盖得住它 —— 用户拖过的宽度在每一档屏幕上都算数,
  // 而那正是他拖它的意思。
  //
  // 计算与指针行为都在 `js/resizer.js`;这里只负责「写哪儿」与「记不记得住」。

  /** 栏 → CSS 变量名。 */
  const COL_VAR = { side: '--side-w', insp: '--insp-w' };
  const COL_KINDS = ['side', 'insp'];

  function colVar(kind) { return COL_VAR[kind] || COL_VAR.side; }
  function colHandle(kind) { return kind === 'insp' ? el.rzInsp : el.rzSide; }

  function setColWidth(kind, px) {
    const w = Math.round(px);
    // 分隔条是 `role="separator"` **且可聚焦**(键盘能调),所以它必须报得出自己的值 ——
    // 一个可聚焦却读不出当前值的控件,比不聚焦更让人摸不着头脑。
    const handle = colHandle(kind);
    if (handle && handle.setAttribute) handle.setAttribute('aria-valuenow', String(w));
    if (!el.app || !el.app.style) return;   // 桩里没有 style 时**静默跳过**,不是抛
    el.app.style.setProperty(colVar(kind), w + 'px');
  }

  /**
   * 双击/Home「重置」:**把内联变量删掉**,而不是写一个等于默认的数值。
   *
   * 右栏的默认是 `clamp(300px, 23vw, 340px)`(`tokens.css`),写死 300px 等于
   * 「重置之后它不再随窗口变宽」—— 与用户按下重置想要的意思正好相反。
   */
  function clearColWidth(kind) {
    if (!el.app || !el.app.style) return;
    el.app.style.removeProperty(colVar(kind));
  }

  /** 这一栏现在多宽:先看用户拖过的内联值,没拖过就退回默认。 */
  function colWidthNow(kind) {
    const inline = el.app && el.app.style
      ? parseFloat(el.app.style.getPropertyValue(colVar(kind)))
      : NaN;
    return Number.isFinite(inline) ? inline : KP.resizer.defaults[kind];
  }

  /**
   * 三栏总共有多宽。**量不到就返回 0** —— 那意味着「不设『给画布留位』这条上限」。
   * 桩与旧浏览器里没有 `clientWidth`,量不到就不该假装量到了。
   */
  function appWidth() {
    const w = el.app ? Number(el.app.clientWidth) : NaN;
    if (Number.isFinite(w) && w > 0) return w;
    const iw = typeof window !== 'undefined' ? Number(window.innerWidth) : NaN;
    return Number.isFinite(iw) && iw > 0 ? iw : 0;
  }

  /** 这一栏能动到的范围。另一栏的宽度按它**现在**的值算,两栏因此各自夹各自的。 */
  function fitWidth(kind) {
    const other = kind === 'insp' ? 'side' : 'insp';
    return (px) => KP.resizer.clampWidth(kind, px, appWidth(), colWidthNow(other));
  }

  function rememberColWidth(kind, px) {
    const saved = KP.prefs.get('layout.cols', null);
    const box = saved && typeof saved === 'object' ? saved : {};
    box[kind] = Math.round(px);
    KP.prefs.set('layout.cols', box);
  }

  /**
   * 拖动 / 方向键落到界面上。**先写变量再写偏好** —— 偏好写失败时(无痕模式与
   * 企业策略下 `setItem` 真的会抛)界面已经跟着动了,不会出现「拖了没反应」。
   */
  function commitColWidth(kind, px) {
    setColWidth(kind, px);
    rememberColWidth(kind, px);
  }

  /** 只忘掉这一栏。两栏都忘光了就整条扔掉,免得在 localStorage 里留一个空对象。 */
  function forgetColWidth(kind) {
    const saved = KP.prefs.get('layout.cols', null);
    if (!saved || typeof saved !== 'object') return;
    delete saved[kind];
    if (Object.keys(saved).length) KP.prefs.set('layout.cols', saved);
    else KP.prefs.remove('layout.cols');
  }

  /** 把记住的宽度落到界面上。启动时与窗口尺寸变化时都走它。 */
  function applyColumnPrefs() {
    const saved = KP.prefs.get('layout.cols', null);
    if (!saved || typeof saved !== 'object') return;
    for (const kind of COL_KINDS) {
      const v = Number(saved[kind]);
      if (Number.isFinite(v)) setColWidth(kind, fitWidth(kind)(v));
    }
  }

  function wireColumns() {
    for (const kind of COL_KINDS) {
      const handle = colHandle(kind);
      // 取值范围是常量,绑定时写一次就够;`aria-valuenow` 之后由 `setColWidth` 跟着走。
      // 这里也写一次:没拖过任何栏时,它是唯一一次把当前值告诉屏幕阅读器的机会。
      if (handle && handle.setAttribute) {
        const L = KP.resizer.LIMITS[kind] || KP.resizer.LIMITS.side;
        handle.setAttribute('aria-valuemin', String(L.min));
        handle.setAttribute('aria-valuemax', String(L.max));
        handle.setAttribute('aria-valuenow', String(Math.round(colWidthNow(kind))));
      }
      KP.resizer.wire(handle, {
        kind,
        get: () => colWidthNow(kind),
        fit: fitWidth(kind),
        commit: commitColWidth,
        reset: () => { clearColWidth(kind); forgetColWidth(kind); },
      });
    }
  }

  // ================= 侧栏:导航 / 我的探索 / 进度卡 =================
  //
  // 三段都先有**纯字符串构造函数**(`navHTML` / `progressHTML`),再由 `render*` 落到
  // DOM。理由与图谱视图一样:没有浏览器自动化时,「路由切换后哪一项高亮」「没有选中
  // 主题时不出现进度卡」这类契约只有变成字符串才断得出来。

  /**
   * 导航项表。**只放这一屏真的能打开的视图。**
   *
   * 设计稿 §6 的导航是「对话 / 知识图谱 / 知识库 / 文档管理 / 检索测试 / 设置」,
   * 其中知识库、文档管理、检索测试三项后端零端点(已与用户拍板省略)。对话与设置
   * 会随各自的视图实现一起进来(第 6 / 7 阶段)—— **加一个点下去没有反应的入口,
   * 比少一个入口更糟**:它不报错,用户只会以为自己点错了。
   *
   * `icon` 是 `KP.icons.NAV` 的键,不是字形本身 —— 字形集中管理是 icons.js 存在的理由。
   */
  const NAV_ITEMS = [
    { view: 'graph', icon: 'graph', label: '知识图谱' },
    { view: 'chat', icon: 'chat', label: '对话' },
    { view: 'settings', icon: 'settings', label: '设置' },
  ];

  /** 导航条。纯函数。`topicId` 决定「知识图谱」指向哪个主题(没有就是首页)。 */
  function navHTML(route, topicId) {
    const cur = (route && route.view) || 'graph';
    return NAV_ITEMS.map((it) => {
      const on = it.view === cur;
      const href = it.view === 'graph'
        ? (topicId ? '#/g/' + encodeURIComponent(topicId) : '#/')
        : '#/' + it.view;
      return `<a class="navitem${on ? ' on' : ''}" href="${href}"` +
             (on ? ' aria-current="page"' : '') + `>` +
             `<span class="ni">${KP.icons.NAV[it.icon]}</span>${it.label}</a>`;
    }).join('');
  }

  /**
   * 学习进度卡。纯函数。**没有选中主题时返回空串**(§8)。
   *
   * 百分比只读 `progress.percent`(后端算好的)—— 前端自算会因为银行家舍入
   * 和顶栏差 1%,而那是页面上最显眼的数字。`total` 为 0 时不画进度条:
   * 一条 0% 的槽会被读成「一个知识点都没学」,而它其实是「还没生成」。
   */
  function progressHTML(topic) {
    if (!topic) return '';
    const p = topic.progress || {};
    const percent = KP.clamp(KP.pct(p), 0, 100);
    const total = Number(p.total) || 0;
    const mastered = Number(p.mastered) || 0;
    // 还没生成出节点时**不画进度条、也不说「已学习 0 / 0」** —— 那两样都会被读成
    // 「这个主题一个知识点都没学」,而它其实是「还没生成」。这时候该说的是主题状态。
    const body = total
      ? `<div class="pc-row">
           <div class="bar"><i style="width:${percent}%"></i></div>
           <span class="pc-pct">${percent}%</span>
         </div>
         <div class="pc-sub">已学习 <strong>${mastered} / ${total}</strong> 个节点</div>`
      : `<div class="pc-sub">${KP.topicLabel(topic.status)}</div>`;
    return `<div class="card progcard">
      <div class="pc-head"><span class="ni">${KP.icons.UI.diamond}</span>知识图谱学习进度</div>
      ${body}
    </div>`;
  }

  function renderNav() {
    const S = KP.S;
    if (!el.nav) return;
    el.nav.innerHTML = S.disabled ? '' : navHTML(S.route, S.topicId);
  }

  function renderProgress() {
    const S = KP.S;
    if (!el.progress) return;
    // 学习图谱没启用(503)时整条侧栏都不该有内容 —— 主题列表、进度、导航一起空掉。
    el.progress.innerHTML = S.disabled ? '' : progressHTML(KP.currentTopic(S.topics, S.topicId));
  }

  /** 侧栏三段的统一入口。**所有会改到 `S.topics` / `S.topicId` / `S.route` 的路径都走它** ——
   *  分开调就会出现「导航高亮了但进度卡还是上一个主题」这种半更新。 */
  function renderSidebar() {
    renderNav();
    renderTopics();
    renderProgress();
  }

  function renderTopics() {
    const S = KP.S;
    if (!el.topics) return;
    if (S.disabled) { el.topics.innerHTML = ''; return; }
    if (!S.topics.length) {
      el.topics.innerHTML = '<div class="hint">还没有主题。<br>在上面输入一个想学的主题开始。</div>';
      return;
    }
    el.topics.innerHTML = S.topics.map((t) => {
      const p = t.progress || {};
      const percent = KP.clamp(KP.pct(p), 0, 100);
      const total = Number(p.total) || 0;
      const mastered = Number(p.mastered) || 0;
      // 只截 `YYYY-MM-DD`(**不做时区换算**):`new Date()` 解析再格式化会把
      // 「2026-09-10T00:30:00+08:00」在 UTC 机器上显示成 09-09,差一天的那种 bug。
      const date = KP.fmtDate(t.updated_at || t.created_at);
      return `<div class="topic${t.id === S.topicId ? ' active' : ''}" data-topic="${KP.esc(t.id)}">
        <div class="t-row">
          <span class="t-title">${KP.esc(t.title)}</span>
          <button class="t-del" data-del="${KP.esc(t.id)}" title="删除探索（保留 Markdown 正文）">✕</button>
        </div>
        <div class="t-meta">
          ${date ? `<span>${KP.esc(date)}</span>` : ''}
          <span>${total ? `已掌握 ${mastered}/${total}` : KP.topicLabel(t.status)}</span>
        </div>
        ${total ? `<div class="bar"><i style="width:${percent}%"></i></div>` : ''}
      </div>`;
    }).join('');
  }

  async function onTopicsClick(e) {
    const S = KP.S;
    const delBtn = e.target.closest('[data-del]');
    if (delBtn) {
      const id = delBtn.getAttribute('data-del');
      const t = S.topics.find((x) => x.id === id);
      if (!confirm(`删除主题「${t ? t.title : id}」？\n图谱与对话记录会被删除，Markdown 正文文件保留。`)) return;
      await KP.api.deleteTopic(id);
      if (S.topicId === id) { S.topicId = null; S.graph = null; KP.views.inspector.close(); }
      await loadTopics();
      route();
      return;
    }
    const card = e.target.closest('[data-topic]');
    if (card) {
      S.topicId = card.getAttribute('data-topic');
      S.nodeId = null;
      KP.views.inspector.close();
      writeHash();
      await loadTopic();
    }
  }

  async function onNewTopic(e) {
    e.preventDefault();
    const S = KP.S;
    const input = document.getElementById('new-query');
    const go = document.getElementById('new-go');
    const q = input.value.trim();
    if (!q) return;
    go.disabled = true; input.disabled = true;
    try {
      const topic = await KP.api.createTopic(q);
      input.value = '';
      S.topicId = topic.id; S.nodeId = null;
      KP.views.inspector.close();
      writeHash();
      await loadTopics();
      KP.views.graph.renderGenerateCard(topic);
    } catch (err) {
      alert('新建失败：' + err.message);
    } finally {
      go.disabled = false; input.disabled = false; input.focus();
    }
  }

  // ================= 加载 =================

  async function loadTopics() {
    const S = KP.S;
    try {
      const data = await KP.api.listTopics();
      S.topics = data.topics || [];
      S.disabled = false;
    } catch (err) {
      if (err.status === 503) { S.disabled = true; showDisabled(err.message); }
      else { throw err; }
    }
    renderSidebar();
  }

  /** 仅刷进度统计的轻量版(纯 GET,零 LLM)。失败不值得打断用户。 */
  async function refreshProgress() {
    const S = KP.S;
    try {
      const data = await KP.api.listTopics();
      S.topics = data.topics || [];
      renderSidebar();
      const cur = S.topics.find((t) => t.id === S.topicId);
      if (S.graph && cur) S.graph.topic = { ...S.graph.topic, progress: cur.progress, status: cur.status };
      // 进度变了之后,「全部点亮」那张完成遮罩可能刚刚该出现(或该消失)。
      // 它只在**该出的遮罩真的换了**的时候才重绘一次,所以正常点亮走的仍然是
      // 局部 patch —— 这条调用不会把缩放/平移清掉(见 `views/graph.js`)。
      if (KP.views.graph.syncOverlay) KP.views.graph.syncOverlay();
      // 助手头上那句「本主题已点亮 m / n」读的就是 `S.topics` —— 不重画的话
      // 它会在每次点亮之后停留在旧数字上,而那正是用户刚做的操作。
      if (KP.views.assistant) KP.views.assistant.render();
    } catch (e) { /* 进度刷新失败不打断用户 */ }
  }

  function showDisabled(detail) {
    if (!el.stage) return;
    el.stage.innerHTML = `
      <div class="empty">
        <h2>学习图谱未启用</h2>
        <p>${KP.esc(detail || '')}</p>
        <div class="notice">
          在项目根目录的 <code>.env</code> 里设置 <code>LEARNING_ENABLED=true</code>，重启服务后刷新本页。<br>
          相关配置：<code>LEARNING_DB_PATH</code>（图谱数据库）、<code>LEARNING_NOTES_DIR</code>（Markdown 正文目录）。
        </div>
      </div>`;
  }

  async function loadTopic() {
    const S = KP.S;
    if (!S.topicId) { KP.views.graph.renderEmpty(); return; }
    let g;
    try {
      g = await KP.api.getTopic(S.topicId);
    } catch (err) {
      // 原实现把**所有**错误吞成「主题不存在」:一次网络抖动或 500 也会清掉
      // topicId、显示空态,而 hash 没跟着清 —— 于是刷新后又去加载同一个失效 id、
      // 再次失败,用户永远看不到出口。两个分支的行为正好相反,分开处理:
      const kind = KP.loadFailureKind(err);
      if (kind === 'missing') {
        // 主题真的被删了:清掉选择**并同步清 hash**,否则刷新还是死链。
        S.topicId = null;
        S.graph = null;
        KP.views.inspector.close();
        if (location.hash !== '#/') history.replaceState(null, '', '#/');
        KP.views.graph.renderEmpty();
      } else {
        // 可能是暂时的:保留 topicId 与 hash,给一个能重试的错误页。
        KP.views.graph.renderLoadError(err);
      }
      renderSidebar();
      return;
    }
    S.graph = g;
    const status = (g.topic || {}).status;
    if ((g.nodes || []).length && status === 'ready') { KP.views.graph.renderGraph(); }
    else { KP.views.graph.renderGenerateCard(g.topic || {}); }
    renderSidebar();
  }

  // ================= 路由 =================

  /**
   * 写 hash。**只用 `history.replaceState`,绝不赋 `location.hash`** ——
   * 赋值会触发 `hashchange`,而 `hashchange` 又调 `route()`,形成死循环。
   *
   * **它同时是 `S.route` 的写入点**,不只是 hash 的。理由:「用 `replaceState`」
   * 的代价正是 `replaceState` 不触发 `hashchange`,而 `S.route` 此前**只有**
   * `route()` 会写(`hashchange` 是它的唯一日间入口)。于是这几条路径走过去之后
   * ——侧栏点主题卡 / 新建主题 / 生成完自动落 hash / 右栏开关节点——hash 已经
   * 指着图谱了,`S.route` 还停在上一屏,最常见的是停在 `chat`。
   *
   * 那是个**整块功能静默失效**的 bug,不是「高亮错一项」:`views/assistant.js`
   * 靠 `S.route.view === 'chat'` 判断「这一屏自带完整输入框,把助手整块藏掉」,
   * 于是从对话页点一下侧栏的主题卡,助手就再也打不开了 —— 「开始学习」、要点与
   * 提纲芯片、收起后那条栏上的「展开」全部落进同一个 early-return,页面上没有
   * 任何报错,而画布看起来完全正常。用户报的原话正是「点击开始学习没有任何反应」
   * 与「关闭后没有办法再次打开」(走查反馈 ③)。
   *
   * 写在这里而不是各个调用点:这函数的语义就是「当前状态 = 这个 hash」,它已经
   * 算出了目标 route,把 `S.route` 落成同一个值是同一件事的另一半;散在各处补
   * 只会漏掉下一个调用点(上一版只在 `views/settings.js` 的「重新研究」里手工补过
   * 一次 —— 同一个根因,所以那一屏的症状没暴露出来)。
   */
  function writeHash(dropNode) {
    const S = KP.S;
    const nodeId = dropNode ? null : S.nodeId;
    const hash = KP.route.formatHash({ view: 'graph', topicId: S.topicId, nodeId });
    if (location.hash !== hash) history.replaceState(null, '', hash);
    // hash 与 `S.route` 的形状必须逐字段一致 —— `route()` 写的是
    // `KP.route.parseHash`(`js/store.js`)的产物。这里手工拼同一个形状而不是回读
    // hash:`parseHash` 会把各段 **decode** 一遍,而 `S.topicId` / `S.nodeId` 装的
    // 本来就是解码后的值,回读等于再解一次。
    S.route = { view: 'graph', topicId: S.topicId, nodeId };
    // route 变了就得让**依赖它的东西**重画。目前只有一个:助手 —— 它的存在与否
    // 完全由 `S.route.view === 'chat'` 决定,所以从对话页切回图谱时它必须被重画
    // 一次才会重新出现。放在这里而不是各个调用点,是因为「漏掉下一个调用点」正是
    // 这个 bug 的成因(见上面那段说明)。
    //
    // `render()` 是幂等的,而且自己会保留已输入的内容 —— 多调一次的代价是零。
    KP.views.assistant.render();
  }

  /**
   * 「这一次是深链接进来的吗」只在**首次**路由时判定一次。
   *
   * 页内的每一次跳转都会调 `route()`(点侧栏主题、生成完自动落 hash、删完主题回首页),
   * 每次都重判的话,`deepLink` 会退化成「当前 hash 里有主题」= 恒真,于是
   * 首次进入遮罩**永远不会出现**。它要区分的从来不是「现在有没有主题」,而是
   * 「用户是拿着一张地图进来的,还是刚让系统画了一张」。
   */
  let routedOnce = false;

  async function route() {
    const S = KP.S;
    const r = KP.route.parseHash(location.hash);
    S.route = r;
    if (!routedOnce) {
      routedOnce = true;
      S.entry.deepLink = !!(r.topicId || r.nodeId);
    }
    if (r.view === 'settings') {
      // 与「对话」页同理:这一屏是配置,不是学习现场,浮动助手留成收起状态就好
      // (它自己会渲染那个启动按钮,不必在这里决定)。
      KP.views.settings.render();
      renderSidebar();
      KP.views.assistant.render();
      return;
    }
    if (r.view === 'chat') {
      // 「对话」页是一次性研究问答,与图谱是两条独立的路径。**刻意不清
      // `S.topicId` / `S.graph`** —— 清了的话从左栏点回「知识图谱」就掉到首页,
      // 而用户失去的正是他刚才在哪儿。深链接直接进 `#/chat` 时它们本来就是空的,
      // 于是自然回落到首页。
      KP.views.chat.render();
      renderSidebar();
      // 这一页自带一个完整的对话界面,再叠一个浮动输入框是纯粹的混乱。
      KP.views.assistant.render();
      return;
    }
    if (r.view !== 'graph' || !r.topicId) {
      // 尚未实现的视图(explore)或「没有选中主题」→ 回到空态。
      // 两者都**不**清 hash:hash 是用户的深链接意图,状态清了下次刷新还能回来。
      S.topicId = null; S.graph = null;
      KP.views.inspector.close();
      KP.views.graph.renderEmpty();
      renderSidebar();
      KP.views.assistant.render();
      return;
    }
    S.topicId = r.topicId;
    renderSidebar();
    KP.views.assistant.render();
    await loadTopic();
    if (r.nodeId) await KP.views.inspector.open(r.nodeId);
  }

  group.shell = {
    init, route, writeHash,
    // `loadTopic` 必须导出:`views/graph.js` 生成成功后、`views/inspector.js`
    // 点「稍后再说」后都要重新加载当前主题。漏掉它不会在加载期报错,只在
    // 那条路径上抛 `is not a function` —— 而那条路径恰好是「生成成功之后」。
    loadTopic, loadTopics,
    refreshProgress, renderTopics, renderSidebar, showDisabled,
    // 纯字符串构造函数(可在 Node 里直接断言,不用挂载点)
    navHTML, progressHTML,
    stage, panel, appEl, assistant,
  };
})(window.KP = window.KP || {});
