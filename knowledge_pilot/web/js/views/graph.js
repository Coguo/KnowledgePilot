/**
 * 图谱主视图:空态 / 生成卡片 / 图谱画布。
 *
 * 除最后那几行 `…innerHTML = ...` 之外全是纯字符串拼装。这一点是有意为之 —— 没有浏览器
 * 自动化时,「渲染是纯函数、DOM 只剩一行赋值」是拿到真实回归保护的唯一办法:
 * 整张图的视觉契约(几个盒子、谁选中、谁点亮、每条边有没有箭头、名字有没有被转义)
 * 因此全部可以在 Node 里断言。**不要为了「顺手」把 DOM 操作揉进拼装过程。**
 */
(function (KP) {
  'use strict';

  const group = (KP.views = KP.views || {});
  const esc = KP.esc;

  function stage() { return KP.views.shell.stage(); }

  function renderEmpty() {
    const el = stage();
    if (!el) return;
    el.innerHTML = `
      <div class="empty">
        <h2>还没有选择主题</h2>
        <p>在左侧新建一个主题，系统会先做一轮研究，再从资料中整理出一张学习图谱。<br>
           每个知识点都可以点开对话，讲透了系统会推荐你点亮它。</p>
      </div>`;
  }

  /**
   * 生成卡片的**纯模板**(卡片本身,不含它落在哪一栏的包装)。
   *
   * 抽出来是因为「卡片长什么样」与「它渲染到哪个容器」现在是两件事了(见
   * `renderGenerateCard`),而后者是这一步唯一会判错的地方 —— 混在一起写,
   * 那次搬家就只能靠肉眼看。文案里的 `retry` 判据一字未动。
   */
  function generateCardHTML(topic) {
    const status = topic.status || 'empty';
    const failed = status === 'failed';
    const interrupted = status === 'generating';
    const retry = failed || interrupted || (topic.progress && topic.progress.total);
    return `
      <div class="gen-card">
        <h3>${esc(topic.title || '未命名主题')}</h3>
        <p class="q">研究问题：${esc(topic.query || '')}</p>
        ${failed ? `<div class="notice">上次生成失败：${esc(topic.error || '未知错误')}</div>` : ''}
        ${interrupted ? '<div class="notice">上次生成没有跑完（服务可能中途重启）。重新生成即可，已完成的记录不受影响。</div>' : ''}
        <div class="p-actions">
          <button class="btn primary" id="gen-go">${retry ? '重新研究并生成' : '开始生成学习图谱'}</button>
          <span class="timer" id="gen-timer"></span>
        </div>
        <div id="gen-log"></div>
        <div id="gen-body"></div>
      </div>`;
  }

  /**
   * 中间列在生成阶段的说明页(纯函数)。
   *
   * 它必须说清**动作在哪** —— 卡片搬进右栏之后,中间这一栏只剩一句提示,而用户
   * 的视线习惯是落在中间的。不写「在右侧」,这一屏看起来就是「什么都没有」。
   */
  function pendingHTML(topic) {
    return `
      <div class="empty">
        <h2>${esc(topic.title || '未命名主题')}</h2>
        <p>这张学习图谱还没有生成。<b>在右侧</b>点「开始生成」—— 研究过程与图谱大纲都在那一栏，<br>
           生成完成后，图谱会出现在这里。</p>
      </div>`;
  }

  /**
   * 生成阶段的入口页。**这一屏跨两栏**:卡片在右栏(`#panel`),中间列只留一句说明。
   *
   * 走查反馈 ④ 之前整张卡片(含那份逐字长文)渲染在 `#stage`,而中间列**没有
   * 滚动容器**(`#stage` 是 `flex: 1` 且没有 `overflow`,`.app` 又钉着 `100vh`)——
   * 报告一长过视口,卡片下半截与按钮就永远看不到,页面也不会滚。右栏本来就是
   * 「一列 + 一个 `.scroll`」的结构(见 `inspector.css`),长内容在栏内滚,比例不动,
   * 所以这里套一层 `.scroll`。
   *
   * `#panel` 的其余内容归 `views/inspector.js`,两边不会同时写:生成阶段没有选中
   * 节点,而选中节点时不会回到这一页。`generate()` 里那四处 DOM 查找也跟着换成了
   * `panel()` —— 只改一半的话按钮挂上去但点不动(找的是另一个容器里的 null)。
   */
  function renderGenerateCard(topic) {
    const hint = stage();
    if (hint) hint.innerHTML = pendingHTML(topic);
    const el = KP.views.shell.panel();
    if (!el) return;
    el.innerHTML = `<div class="scroll">${generateCardHTML(topic)}</div>`;
    KP.dom.q(el, 'gen-go').addEventListener('click', () => generate());
  }

  /**
   * 主题加载失败(**非** 404)时的可重试错误页。
   *
   * 与空态的区别是**它明确说了这是暂时的**,并给一个重试按钮 —— 原实现把这种
   * 情况也显示成「还没有选择主题」,用户唯一能做的是反复刷新碰运气。
   */
  function renderLoadError(err) {
    const el = stage();
    if (!el) return;
    el.innerHTML = `
      <div class="empty">
        <h2>主题加载失败</h2>
        <div class="notice">${esc((err && err.message) || '未知错误')}</div>
        <p>这通常是一次网络抖动或服务端错误，主题本身多半还在。</p>
        <div class="p-actions">
          <button class="btn primary" id="reload-topic">重试</button>
        </div>
      </div>`;
    const btn = KP.dom.q(el, 'reload-topic');
    if (btn) btn.addEventListener('click', () => KP.views.shell.loadTopic());
  }

  /**
   * 生成跑完之后走哪条分支。**纯函数**,因为它是那个 `finally` 块里唯一会判错的地方。
   *
   * `'reload'` —— 主题已就绪:清掉节点选择、把 hash 落到主题级、重新加载图谱。
   * `'retry'`  —— 生成失败或被打断:按钮恢复成「重试」,让用户能再来一次。
   *
   * 判据只看 `status === 'ready'`;拿不到 topic(列表还没刷回来)时按 `'retry'` 处理 ——
   * 宁可让用户看到一次「重试」,也不要因为一次刷新失败就假装图谱已经好了。
   */
  function generateOutcome(topic) {
    return topic && topic.status === 'ready' ? 'reload' : 'retry';
  }

  /**
   * 生成卡要用**哪个主题记录**来画。**纯函数** —— 它是这条路径上唯一会画错的地方。
   *
   * 走查反馈 ⑥：新建主题后点「开始生成」，卡片与中间列的标题会变成「未命名主题」。
   * 原因是这里原来写的是 `{ ...(S.graph ? S.graph.topic : {}), status: 'empty' }` ——
   * 从**整图**里反推主题。而新建的主题从来没走过 `loadTopic`（`onNewTopic` 直接拿
   * 接口返回的那条记录画卡），`S.graph` 于是是 `null` → 展开成 `{}` → 标题为
   * `undefined` → 模板里那两个 `|| '未命名主题'` 兜底生效。
   *
   * 两种拿法各有各的正确性，所以**两个都要，有主次**：
   *
   * - 主：主题列表里按 id 查出来的那一条（`KP.currentTopic` 本身就以 id 为键，
   *   查到的必然是这个主题），它带着**刚敲进去**的 title/query；
   * - 补：整图接口里的 `topic`，它带列表接口没有的字段（`error`、以及更完整的
   *   `progress`）——**只在它确实是同一个主题时**才合并。少这一条判断的话，
   *   `S.graph` 还留着上一个主题的图时，卡片上会写着上一个主题的名字。
   *
   * `status` 由调用方给（生成中要按 `'empty'` 画，避免把上一次的 `failed` 提示
   * 又顶在脸上）。
   */
  function cardTopic(topics, topicId, graph, status) {
    const row = KP.currentTopic(topics, topicId) || {};
    const g = graph && graph.topic;
    const same = g && g.id === topicId ? g : {};
    return { ...row, ...same, status: status || same.status || row.status || '' };
  }

  /** 生成:研究 → 抽知识点 → 落库 → `graph_ready`（右栏在最后收到一份图谱大纲）。 */
  async function generate() {
    const S = KP.S;
    if (S.busyGenerate) return;
    S.busyGenerate = true;
    const topicId = S.topicId;
    renderGenerateCard(cardTopic(S.topics, topicId, S.graph, 'empty'));

    // 卡片在**右栏**(见 `renderGenerateCard`),四处查找都得跟着换容器。
    const el = KP.views.shell.panel();
    const go = KP.dom.q(el, 'gen-go');
    const logEl = KP.dom.q(el, 'gen-log');
    const bodyEl = KP.dom.q(el, 'gen-body');
    const timerEl = KP.dom.q(el, 'gen-timer');
    if (go) { go.disabled = true; go.textContent = '生成中…'; }

    const t0 = Date.now();
    const timer = setInterval(() => {
      if (timerEl) timerEl.textContent = `已用时 ${Math.round((Date.now() - t0) / 1000)}s`;
    }, 500);

    const line = (text, cls) => {
      if (!logEl) return;
      const node = document.createElement('div');
      node.className = 'line' + (cls ? ' ' + cls : '');
      node.textContent = text;
      logEl.appendChild(node);
    };
    // 右栏那块长文:`token` 帧累加到同一块,避免每个 delta 建一个 DOM 节点。
    //
    // 两种模式下它收到的**不是同一种东西**,但都不需要区别对待:
    //   - 图谱模式(现在走的路):生成结束时**一次性**发来渲染好的图谱大纲;
    //   - loop 模式:研究报告逐字到达。
    // 累加 + 贴底滚动这个写法对两者都成立,所以这里一行都不用分叉。
    let report = '';
    const addReport = (delta) => {
      report += delta;
      if (bodyEl) { bodyEl.textContent = report; bodyEl.scrollTop = bodyEl.scrollHeight; }
    };

    try {
      await KP.api.generateTopic(topicId, (evt) => {
        // 长文到达:累加到同一块,不是一行日志。
        if (evt.type === 'token') { addReport(evt.content || ''); return; }
        // 其余「过程」帧的文案与对话页**共用同一张映射表**(`KP.chat.line`)。
        // 原来这里是一整张 switch,对话页又抄了一整张 —— 两份一定会分叉,
        // 而分叉只表现为「同一件事在生成页说得多、在对话页说得少」,没有任何报错。
        // `recommend` 在这里天然不产出行(line() 返回 null),与原来一致。
        const ln = KP.chat.line(evt);
        if (ln) line(ln.text, ln.cls);
      });
    } catch (err) {
      line('⚠️ ' + err.message, 'err');
    } finally {
      // `finally` 里的三件事都不能漏:停表、**放开 `busyGenerate`**、按结果分支。
      // 放开忙碌标志是这里最要紧的一条 —— 漏了它按钮永远停在「生成中…」,
      // 不刷新页面就再也点不动。由 `views.test.mjs` 的抛错用例钉住。
      clearInterval(timer);
      S.busyGenerate = false;
      await KP.views.shell.loadTopics();
      const cur = S.topics.find((x) => x.id === topicId);
      if (generateOutcome(cur) === 'reload') {
        // **不要在这里自己清 `S.nodeId`** —— `close()` 自己会清,而它会被「我的笔记」那道
        // 确认拦住(用户点了「取消」= 别丢我的草稿)。先清的话,用户点一次取消就留下
        // 「图里没有选中节点、右栏还挂着那个节点的详情」的错位状态:草稿虽然在 `S` 里,
        // 但那个节点已经选不回来了,而下一次打开它会走 `open()` 里的 `S.note` 归零 ——
        // 等于用户点了「取消」之后**照样丢了**。让状态变化只由 `close()` 拥有。
        KP.views.inspector.close();
        KP.views.shell.writeHash();
        await KP.views.shell.loadTopic();
      } else if (go) {
        go.disabled = false;
        go.textContent = '重试';
      }
    }
  }

  // ================= 图谱 =================
  //
  // 渲染分三层,**每一层都是纯函数**(只吃参数、只吐字符串,不碰 DOM 也不读 `KP.S`):
  //
  //   svg(g, opts)    → `<svg>…</svg>`   节点与边,视觉契约就落在这里
  //   header(g, opts) → 顶栏 + 图例
  //   view(g, opts)   → header + 滚动容器 + svg,也就是 `renderGraph` 赋给 stage 的东西
  //
  // 拆开是为了能在 Node 里断言「几个盒子、谁选中、每条边有没有箭头、名字有没有被转义」。
  // 没有浏览器自动化时,这是拿到真实回归保护的唯一办法。**别把 DOM 操作揉进这三层**。

  /**
   * 给一张图算布局。**这是布局选择的唯一入口。**
   *
   * `mode`(`'layered'` / `'radial'` / null)由**调用方**从 `S.view.mode` 取好传进来 ——
   * 这里不读全局状态,以便单测直接喂参数。传 null 时 `KP.layout.layout()` 会调
   * `choose()` 按图的形状定(链式 → 横向,星形/宽环 → 径向)。视图层完全不知道
   * 最终跑的是哪个:两种布局返回同一个契约(见 `graph/layout.js` 顶部)。
   *
   * 目前每次渲染都重算,没有缓存。这是有意的:`choose()` 只读 `depth`,是纯函数,
   * 同一份数据的结果恒定,所以不存在「两次渲染之间节点换位」。真要加缓存,键必须
   * 是 `(topicId, mode)` —— 只按 mode 缓存会让换主题后拿旧坐标去查新节点,全部落空。
   */
  function layoutOf(g, mode) {
    return KP.layout.layout((g && g.nodes) || [], (g && g.edges) || [], mode);
  }

  /** 选中节点时与它直接相连的边 —— 加粗显示,帮用户看清「学了它才能学谁」。 */
  function hotEdges(g, sel) {
    const hot = new Set();
    if (!sel) return hot;
    (g.edges || []).forEach((e) => {
      if (e.source_id === sel || e.target_id === sel) hot.add(e.source_id + '>' + e.target_id);
    });
    return hot;
  }

  /**
   * 中心环进度的几何。**纯数字** —— 它错起来不报错,只表现为「环画的长度和数字对不上」。
   *
   * @param {number} percent 后端算好的 `progress.percent`(**别在这里自算**,见 `KP.pct`)
   * @param {number} r       环半径
   * @returns `{c, offset}`:`c` 是周长(给 `stroke-dasharray`),`offset` 是空白段长度。
   */
  function ringGeom(percent, r) {
    const p = KP.clamp(Number(percent) || 0, 0, 100);
    const c = 2 * Math.PI * r;
    return { c, offset: c * (1 - p / 100) };
  }

  /**
   * 中心节点外那一圈进度(设计稿 §12「中心节点环形进度」)。**纯函数**。
   *
   * 只在布局给出了「唯一那个 depth-0 节点」时才画(见 `KP.layout.centerOf` 的注释:
   * 0 个或 ≥2 个根时不合成中心,因为径向布局在原点没有留位置)。所以横向布局、
   * 多根图都返回空串 —— 这不是漏了,是那两种图上「中心」不是一个确定的位置。
   *
   * 百分比读 `topic.progress.percent`,**绝不自己算** —— 后端是 Python 的银行家舍入
   * (`round(12.5) == 12`),前端自算会让这个环与侧栏差 1%,而那正是这张图上最显眼的数字。
   */
  function centerRingHTML(g, lay) {
    const c = KP.layout.centerOf(g, lay);
    if (!c || !c.node) return '';
    const box = lay.pos.get(c.node.id);
    if (!box) return '';
    // 半径 = 盒子的外接圆 + 26:盒子是 158×54,贴着画会切到字;留 26px 也让环
    // 与第 1 环的盒子之间还有余量(第 1 环半径 ≥ DIAG ≈ 169,盒高只占 27)。
    const r = Math.max(box.w, box.h) / 2 + 26;
    const geo = ringGeom(KP.pct((g.topic || {}).progress), r);
    // 起点转到正上方(`rotate(-90)`)—— 不转的话进度从头开始的地方在三点钟方向,
    // 与「12 点是起点」的钟表直觉不符,读起来像差了 90°。
    return `<g class="centerglow" transform="translate(${c.x} ${c.y})">` +
             `<circle class="ring-track" r="${r}"></circle>` +
             `<circle class="ring-bar" id="center-ring" r="${r}" ` +
               `stroke-dasharray="${geo.c.toFixed(2)}" ` +
               `stroke-dashoffset="${geo.offset.toFixed(2)}" ` +
               `transform="rotate(-90)"></circle>` +
           `</g>`;
  }

  /**
   * 一个节点盒子内部的全部 SVG。**全图渲染与单点 patch 共用这一个来源。**
   *
   * 抽出来是 Stage 5 的 `patchNodeEl` 要求的:点亮一个节点时如果要重画整个盒子,
   * 那这段标记就有第二份实现,而两份实现一定会分叉 —— 分叉的表现是「流式对话里
   * 点亮的那一下,盒子比别的小一圈 / 尾注还写着『未学』」,截图对比才发现。
   *
   * 收的是**算好的 `box`** 而不是 `L`,因为 `patchNodeEl` 只有那一个盒子。
   */
  function nodeParts(n, box, isSel) {
    const st = KP.stateOf(n);
    const lines = KP.layout.wrapText(n.name, 13.5, KP.layout.NODE_W - 30, 2);
    const textY = box.y + box.h / 2 - (lines.length - 1) * 8 - 1;
    const parts = [];
    // `rx` 只管圆角、不参与几何 —— 布局的盒子尺寸与 `DIAG`(不重叠判据)都按
    // 直角算,所以改它不会让任何一条不重叠断言失效。设计稿 §28:节点圆角 16~24。
    parts.push(`<rect class="box${isSel ? ' sel' : ''}" x="${box.x}" y="${box.y}" ` +
               `width="${box.w}" height="${box.h}" rx="14" ` +
               `fill="${st.fill}" stroke="${st.stroke}" ` +
               (st.dash ? `stroke-dasharray="${st.dash}" ` : '') +
               `stroke-width="${isSel ? 2.4 : 1.6}"></rect>`);
    lines.forEach((ln, k) => {
      // 文字色 = `--ink`(`#1f2937`)。SVG 的 `fill=` 是属性,不认 CSS 变量,
      // 所以这里是第二处字面量;tokens.css 顶部的注释说明了这个例外。
      parts.push(`<text x="${box.x + box.w / 2}" y="${textY + k * 17}" ` +
                 `text-anchor="middle" font-size="13.5" fill="#1f2937">${esc(ln)}</text>`);
    });
    // 尾注走 `KP.nodeFooter` 而不是 `st.label` —— 「学习中」那一条要带上真实的
    // 「已对话 N 轮」(见 `store.js`)。它是四通道编码里的**文字通道**。
    parts.push(`<text x="${box.x + box.w / 2}" y="${box.y + box.h - 7}" text-anchor="middle" ` +
               `font-size="10.5" fill="${st.stroke}">${esc(KP.nodeFooter(n))}</text>`);
    parts.push(`<circle cx="${box.x + box.w - 13}" cy="${box.y + 13}" r="8" ` +
               `fill="${st.gfill}" stroke="${st.gstroke}"></circle>`);
    parts.push(`<text x="${box.x + box.w - 13}" y="${box.y + 17}" text-anchor="middle" ` +
               `font-size="10" fill="${st.gink}">${st.glyph}</text>`);
    return parts.join('');
  }

  /** 节点 `<g>` 的类名。状态类给 CSS 用(已掌握有 Glow、点亮的弹一下)。 */
  function nodeClass(n, isSel) {
    return 'node st-' + KP.nodeState(n) + (isSel ? ' sel' : '');
  }

  /**
   * 节点与边的 SVG。**纯函数**。
   *
   * @param {object} g      `{topic, nodes, edges}`
   * @param {object} [opts] `{selected, layout, mode, visible, scale}`
   *   - `layout`  已算好的布局(渲染路径上算一次给这里、一次给 `applyView`,不必算两遍)。
   *               不传就自己算,所以单独调 `svg(g, {selected})` 也成立。
   *   - `mode`    **用户手动选的**布局(null = 自动),只影响按钮高亮。
   *   - `visible` 可见节点 id 的 Set(`KP.filterNodes` 给的)。**布局仍然在全量节点上算** ——
   *               筛选只决定谁被画出来,位置一个都不动。不传 = 全可见。
   *   - `scale`   缩放比,乘到 SVG 的 width/height 上。不传 = 1。
   */
  function svg(g, opts) {
    const o = opts || {};
    const sel = o.selected || null;
    const L = o.layout || layoutOf(g);
    const vis = o.visible || null;
    const scale = Number(o.scale) > 0 ? Number(o.scale) : 1;
    const hot = hotEdges(g || {}, sel);

    const parts = [];
    // 层标注的**几何**由布局给(`marks`),文字在这里拼 —— 布局不该知道文案。
    // 径向布局的 `marks` 是空的:环标注没有稳的落点,层信息改由图例那句话承担。
    L.marks.forEach((mk) => {
      parts.push(`<text class="leghead" x="${mk.x}" y="${mk.y}">第 ${mk.level + 1} 层</text>`);
    });

    // 中心环在**边与节点之下** —— 它是背景性的进度指示,压住任何一条线都会让人
    // 以为那条前置关系断了。
    parts.push(centerRingHTML(g || {}, L));

    parts.push('<g class="edges">');
    L.paths.forEach((p) => {
      // 筛选:两端都可见才画。**在渲染层丢而不是在布局层丢** —— 布局层拿的是全量
      // 边(位置要稳定),所以一条边可能是「两端之一被筛掉了」的,画出来就是一根
      // 指向空气的线。
      if (vis && !(vis.has(p.source) && vis.has(p.target))) return;
      // 每条边都要带箭头 —— `hot` 的语义**只是加粗描边**。曾经这里写成
      // 「hot 就不加 marker-end」,结果是「选中一个节点之后,与它相连的边反而
      // 没了箭头」:一条静默的语义反转,截图上看不出、点一下才困惑。
      // 由 `views.test.mjs` 的「每条边都有 marker-end」钉住。
      //
      // 反向边(`p.back`,由破环强制放行产生)多一个 class:它不能用正向的画法,
      // 否则一条线横穿整张图。形状差异已经落在 `p.d` 里,这里只负责让它能被样式选中。
      const cls = 'edge' + (hot.has(p.id) ? ' hot' : '') + (p.back ? ' back' : '');
      parts.push(`<path class="${cls}" d="${p.d}" marker-end="url(#arrow)"></path>`);
    });
    parts.push('</g>');

    ((g && g.nodes) || []).forEach((n) => {
      if (vis && !vis.has(n.id)) return;
      const box = L.pos.get(n.id);
      if (!box) return;
      parts.push(`<g class="${nodeClass(n, n.id === sel)}" data-id="${esc(n.id)}">` +
                 nodeParts(n, box, n.id === sel) + '</g>');
    });

    return `<svg class="graph" id="graph-svg" width="${Math.round(L.width * scale)}" ` +
           `height="${Math.round(L.height * scale)}" viewBox="0 0 ${L.width} ${L.height}">
           <defs>
             <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6"
                     markerHeight="6" orient="auto-start-reverse">
               <path d="M0,1 L9,5 L0,9 z" fill="#cbd5e1"></path>
             </marker>
           </defs>
           ${parts.join('')}
         </svg>`;
  }

  // ---- 筛选芯片 -----------------------------------------------------------------

  /** 状态筛选芯片的顺序。**与 `KP.STATE` 的键序一致**,但显式写出来 ——
   *  从对象键序推顺序,将来谁往 `STATE` 里加一个键,芯片就会无声地多一个。 */
  const STATUS_ORDER = ['unlearned', 'learning', 'recommended', 'mastered'];

  /**
   * 筛选条。**它同时是图例。**
   *
   * 上一版这里是一条纯展示的 `.legend`(四个色块 + 文字),筛选芯片另起一行 ——
   * 两行说的是同一件事:四种状态。合成一行之后,色块既解释编码、又能点着筛,
   * 而且省掉一整行高度(720 高的窗口里,顶栏下面每一行都是中央画布的地)。
   *
   * 芯片上**不带数量**。带了就有个说不圆的地方:数量按全量算、而画面上是「状态 × 类型」
   * 两个筛选的交集,于是点一个类型芯片之后每个状态数量都对不上 —— 用户会以为筛选坏了。
   * 总数已经在顶栏(`N 个知识点 · 已掌握 m`)。
   *
   * 类型行只在**不止一个类型**时才出现:只有一个 facet 时「类型:全部 / 章节」是纯噪音。
   */
  function filtersHTML(g, filters, mode) {
    const f = filters || {};
    const st = f.status || 'all';
    const ty = f.type || 'all';
    const statusChip = (key, label) =>
      `<button class="chipbtn${st === key ? ' on' : ''}" data-fstatus="${esc(key)}" ` +
      `aria-pressed="${st === key}" type="button">` +
      (key === 'all' ? '' : `<span class="swatch ${esc(key)}"></span>`) + esc(label) + '</button>';
    const status = ['<span class="flabel">状态</span>', statusChip('all', '全部')]
      .concat(STATUS_ORDER.map((k) => statusChip(k, KP.STATE[k].label)))
      .join('');

    const facets = KP.typeFacets((g && g.nodes) || []);
    const typeRow = facets.length > 1
      ? '<span class="flabel">类型</span>' +
        `<button class="chipbtn${ty === 'all' ? ' on' : ''}" data-ftype="all" ` +
        `aria-pressed="${ty === 'all'}" type="button">全部</button>` +
        facets.map((fc) =>
          `<button class="chipbtn${ty === fc.key ? ' on' : ''}" data-ftype="${esc(fc.key)}" ` +
          `aria-pressed="${ty === fc.key}" type="button">${esc(fc.label)}</button>`).join('')
      : '';

    // 层标注只存在于横向布局,所以那句「由浅入深」的方向也得跟着变 —— 径向是
    // 由内向外,写反了会让第一次看这张图的用户找错起点。
    //
    // `mode` 由 `header` 传进来(那里已经从 `layout.mode` 或 `choose()` 拿到了)。
    // **不要在这里读 `g`** —— `g` 是后端 JSON,往上面挂 `__mode` 就跟当初把
    // `S.graph` 当草稿纸用是同一类事,而且下次 GET 回来就没了。
    const depthHint = mode === 'radial'
      ? '箭头 = 前置关系，由内向外由浅入深'
      : '箭头 = 前置关系，从左往右由浅入深';

    return `<div class="filters">${status}</div>` +
           (typeRow ? `<div class="filters">${typeRow}</div>` : '') +
           `<div class="filters"><span class="lghint">${depthHint}</span></div>`;
  }

  /** 画布工具栏。**纯展示**,事件在 `renderGraph` 里绑。 */
  function toolbarHTML() {
    const UI = KP.icons.UI;
    const btn = (id, label, title) =>
      `<button class="tbtn" id="${id}" title="${esc(title)}" type="button">${label}</button>`;
    return `<div class="toolbar">
      ${btn('zoom-out', UI.minus, '缩小')}
      ${btn('zoom-in', UI.plus, '放大')}
      ${btn('zoom-reset', '100%', '实际大小（100%）')}
      ${btn('zoom-fit', UI.fit, '适应画布')}
    </div>`;
  }

  /** 布局模式的中文名。`modeOf` 只能是这两个值,所以不必兜底。 */
  const MODE_LABEL = { layered: '横向', radial: '放射' };

  /**
   * 布局切换按钮。**放在这里是有理由的**:`choose()` 是启发式,它对多数图选得对,
   * 但总有一张图用户更想换个看法;而 Stage 3 的验收标准正是「人工对比两种模式」——
   * 没有这个开关,对比就只能改代码。
   *
   * 高亮的判据是 **`opts.mode`(用户手动选过什么)**,不是 `layout.mode`(当前实际
   * 跑的是哪个):没选过时不点亮任何一个,免得「自动选了径向」看起来像是用户自己选的。
   *
   * 注意 `cur` 来自参数而不是 `KP.S` —— `header`/`view` 是不读全局状态的纯函数,
   * 这条契约让它们能在 Node 里随便喂参数断言。要读 `S.view.mode` 的只有 `renderGraph`。
   */
  function layoutToggle(cur) {
    const btn = (m) => `<button class="btn sm seg${cur === m ? ' on' : ''}" ` +
                       `data-mode="${m}" title="${cur === m ? '当前手动指定，再点一次回到自动' : '切到' + MODE_LABEL[m] + '布局'}">` +
                       `${MODE_LABEL[m]}</button>`;
    return `<span class="segwrap"><span class="lghint">布局</span>${btn('layered')}${btn('radial')}</span>`;
  }

  /**
   * 顶栏 + 筛选/图例。纯函数(除了 `esc`,无副作用)。
   *
   * **这里没有「重新研究并生成」。** 设计稿 §23 把知识图谱定为「一次构建」:
   * 构好之后顶栏最显眼的位置上留一个重生成按钮,等于每天都在邀请用户把那件事
   * 再做一遍 —— 而重跑会按新的 `order_index` 重算 `note_path`(`service.py:82`),
   * 用户写在正文里的「## 我的笔记」会留在磁盘上成孤儿,界面上再也看不到。
   * 那个能力还在,但挪进设置页的「进阶」,和它的代价写在一起(见 `views/settings.js`)。
   */
  function header(g, opts) {
    const topic = (g && g.topic) || {};
    const prog = topic.progress || {};
    const o = opts || {};
    const mode = (o.layout && o.layout.mode) || KP.layout.choose((g && g.nodes) || []);
    return `<div class="topbar">
         <h2>${esc(topic.title || '未命名主题')}</h2>
         <span class="chip ${esc(topic.status || '')}">${KP.topicLabel(topic.status)}</span>
         <span class="timer">${prog.total || 0} 个知识点 · 已掌握 ${prog.mastered || 0} · 待确认 ${prog.recommended || 0} · ${KP.pct(prog)}%</span>
         ${layoutToggle(o.mode || null)}
       </div>
       ${filtersHTML(g, o.filters, mode)}`;
  }

  // ---- 首次进入 / 全部点亮 两种遮罩(设计稿 §24 / §25) --------------------------

  /**
   * 首次进入(§24)。**这是「遮罩」而不是一页** —— 底下的图已经画好了,它只是
   * 盖在上面说一句「你的知识地图已经建立」。所以它的按钮点下去是**揭开图**,
   * 不是「进入某个页面」。
   *
   * 两个数字都是真的:`N` = `nodes.length`,`M` = `edges.length`。设计稿 §23 那句
   * 「本次探索已生成 N 个知识节点 / M 个核心关系」里的 M 同理 —— 关系条数后端给了。
   */
  function welcomeHTML(g) {
    const topic = (g && g.topic) || {};
    return `<div class="overlay">
      <div class="ov-card" role="dialog" aria-modal="true" aria-labelledby="ov-title">
        <p class="ov-eyebrow">${esc(topic.title || '未命名主题')}</p>
        <h2 id="ov-title">你的知识地图已经建立</h2>
        <p class="ov-stats"><b>${((g && g.nodes) || []).length}</b> 个知识节点 ·
           <b>${((g && g.edges) || []).length}</b> 条关系</p>
        <p class="ov-note">从中心节点开始探索，逐步点亮你的知识体系。</p>
        <button class="btn primary" id="ov-go" type="button">开始学习</button>
      </div>
    </div>`;
  }

  /**
   * 全部点亮(§25)。
   *
   * **节点写 `m / n`,关系只写 `M 条`,不写 `M / M`。** 设计稿 §25 画的是
   * 「43 / 43 个核心关系」,但关系**没有状态** —— 后端没有「这条前置关系学会了」
   * 这回事,那个 `/ 43` 是为了排版对称编出来的。分母是假的,就一个都不写:
   * 节点那个 `m / n` 是真数字(`progress.mastered` / `progress.total`)。
   */
  function completeHTML(g) {
    const topic = (g && g.topic) || {};
    const prog = topic.progress || {};
    const total = (g && g.nodes) ? g.nodes.length : 0;
    return `<div class="overlay">
      <div class="ov-card lit" role="dialog" aria-modal="true" aria-labelledby="ov-title">
        <div class="ov-spark">${KP.icons.STATE_GLYPH.mastered}</div>
        <h2 id="ov-title">知识体系已点亮</h2>
        <p class="ov-eyebrow">${esc(topic.title || '未命名主题')} · 已完成学习</p>
        <p class="ov-stats"><b>${Number(prog.mastered) || 0} / ${total}</b> 个节点 ·
           <b>${((g && g.edges) || []).length}</b> 条核心关系</p>
        <button class="btn primary" id="ov-go" type="button">继续探索</button>
      </div>
    </div>`;
  }

  /** 遮罩 HTML。`kind` 由 `KP.overlayKind()` 给,认不出的值一律不画。 */
  function overlayHTML(kind, g) {
    if (kind === 'welcome') return welcomeHTML(g);
    if (kind === 'complete') return completeHTML(g);
    return '';
  }

  /** stage 的完整内容。纯函数 —— `renderGraph` 只做 `innerHTML = view(...)` + 绑事件。
   *
   *  工具栏与画布是**兄弟**:工具栏绝对定位在 `.canvas` 上,不在 `#graph-scroll` 里 ——
   *  放进去的话它会跟着内容一起滚走(缩放后图比容器大,工具栏就跑到屏幕外了)。
   *
   *  遮罩同理放在 `.canvas` 里、`#graph-scroll` **外面**:跟着滚的话,图被拖到一边时
   *  遮罩也跟着偏,而它要盖住的是整个画布。 */
  function view(g, opts) {
    const o = opts || {};
    return header(g, o) +
      `<div class="canvas">
         <div id="graph-scroll"><div id="graph-fit">${svg(g, o)}</div></div>
         ${toolbarHTML()}
         ${overlayHTML(o.overlay, g)}
       </div>`;
  }

  // 当前渲染用的布局。给滚轮处理器用 —— 它必须知道图的原始宽高才能算缩放后的
  // `scrollLeft` 修正。**不能从 SVG 的 width 反推**:那是四舍五入过的整数,
  // 反推出来的比例与 `S.view.scale` 会有微小偏差,滚几次之后光标下的点就开始漂。
  let curLayout = null;

  // 当前画在屏幕上的遮罩。给 `syncOverlay()` 用 —— 它只在**该出的遮罩变了**的时候
  // 才肯全量重绘一次,不然每次进度刷新都会把缩放/平移清掉。
  let curOverlay = null;

  /**
   * 现在该出哪个遮罩。**读状态、查偏好,然后把纯判据交给 `KP.overlayKind()`。**
   *
   * `seen` 是**跨会话**的偏好(`localStorage`),`completeSeen` 是**本会话**的
   * (`S.view.completeSeenFor` = 已经为哪个主题按过「继续探索」)。两者刻意不同:
   * 首次进入提示一辈子只该出现一次,而完成态是「这次点亮做到头了」的现场反应 ——
   * 换一个主题、或者重新进这个主题,它应该再出现(否则用户看不到完成这件事)。
   */
  function overlayKindNow() {
    const S = KP.S;
    return KP.overlayKind(S.graph, {
      seen: KP.prefs.get('onboarded', false) === true,
      completeSeen: S.view.completeSeenFor === S.topicId,
      deepLink: S.entry.deepLink,
      nodeId: S.nodeId,
    });
  }

  /**
   * 进度变了之后,只在**遮罩该换了**的时候重绘一次。
   *
   * 这是「点亮只 patch 一个盒子」与「全部点亮时要弹完成态」之间唯一的接口。没有它,
   * 点亮最后一个节点只会 patch 那个盒子,完成遮罩要等到用户碰一下筛选才出现 ——
   * 而那一下正是「我做完了」最该被承认的时刻。
   *
   * @returns 是否重绘了。
   */
  function syncOverlay() {
    if (!KP.S.graph) return false;
    if (overlayKindNow() === curOverlay) return false;
    renderGraph();
    return true;
  }

  /**
   * 把 `S.view` 里的缩放落到 SVG 上。返回最终用的比例。
   *
   * `S.view.fit` 是「跟随容器」的意思 —— 它在窗口尺寸变化后要重新算(窗口拉窄了,
   * 「适应」的结果也得变小),所以它是一个**标志**而不是一个算好的数。用户一旦
   * 手动缩放(按钮或 Ctrl+滚轮),`fit` 归 false,缩放比就被钉住了。
   */
  function applyView(scroller, layout) {
    const S = KP.S;
    const svgEl = scroller && KP.dom.q(scroller, 'graph-svg');
    if (!svgEl || !layout) return 0;
    if (S.view.fit) {
      S.view.scale = KP.graph.canvas.fitScale(
        layout.width, layout.height, scroller.clientWidth, scroller.clientHeight);
    } else {
      S.view.scale = KP.graph.canvas.clampScale(S.view.scale);
    }
    KP.graph.canvas.applyScale(svgEl, layout.width, layout.height, S.view.scale);
    return S.view.scale;
  }

  function renderGraph() {
    const el = stage();
    const S = KP.S;
    const g = S.graph;
    if (!el) return;
    if (!g) { el.innerHTML = ''; curLayout = null; curOverlay = null; return; }

    // 布局算一次,`svg()` / `applyView()` / `centerOn()` 共用 —— 布局是纯函数,
    // 但没必要算三遍。**布局永远在全量节点上算**:筛选只决定谁被画出来,
    // 一个位置都不动(见 `store.js` 的 `filterNodes`)。
    const layout = layoutOf(g, S.view.mode);
    curLayout = layout;
    // 筛选:重绘前先把滚动位置记下来。**不记的话每点一次筛选芯片,视图就跳回左上角** ——
    // 而筛选恰恰是「看着某一片、想把它摘出来看」的操作,跳走等于把用户正在做的事毁掉。
    const prev = KP.dom.q(el, 'graph-scroll');
    const keep = prev ? { left: prev.scrollLeft, top: prev.scrollTop } : null;

    const vis = KP.filterNodes(g.nodes || [], g.edges || [], S.filters);
    curOverlay = overlayKindNow();
    const opts = {
      selected: S.nodeId, layout, mode: S.view.mode,
      visible: vis.ids, scale: 1,   // scale 由 applyView 落,这里占位
      filters: S.filters,
      overlay: curOverlay,
    };
    el.innerHTML = view(g, opts);

    const ovGo = KP.dom.q(el, 'ov-go');
    if (ovGo) ovGo.addEventListener('click', () => {
      // 两个遮罩的「按过」记在不同地方,理由见 `overlayKindNow()`:
      // 首次进入是跨会话的一次性事实,完成态是本会话的现场反应。
      if (curOverlay === 'complete') S.view.completeSeenFor = S.topicId;
      else KP.prefs.set('onboarded', true);
      renderGraph();
    });
    // 布局切换:改的是偏好,所以走全量重绘(布局换了,所有盒子都要重排)。
    // 这与「点亮只 patch 一个盒子」不冲突 —— 那说的是不换布局的场合。
    KP.dom.qa(el, '[data-mode]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const m = btn.getAttribute('data-mode');
        // 手动切布局时**放弃缩放**回到「适应画布」:两种布局的宽高差异极大
        // (12 节点的链是 3000×300,径向可能是 900×900),沿用旧比例会让切过去
        // 的那张图要么小得看不清、要么大到找不到北。
        S.view.mode = S.view.mode === m ? null : m;   // 再点一次回到「自动」
        S.view.fit = true;
        renderGraph();
      });
    });
    KP.dom.qa(el, '[data-fstatus]').forEach((btn) => {
      btn.addEventListener('click', () => {
        S.filters.status = btn.getAttribute('data-fstatus');
        renderGraph();
      });
    });
    KP.dom.qa(el, '[data-ftype]').forEach((btn) => {
      btn.addEventListener('click', () => {
        S.filters.type = btn.getAttribute('data-ftype');
        renderGraph();
      });
    });

    const scroller = KP.dom.q(el, 'graph-scroll');
    if (scroller) {
      /**
       * 节点选中:**命中判定必须在 `pointerdown` 那一刻做,不能在 `click` 里读 `e.target`。**
       *
       * 原因在 `graph/canvas.js` 的 `onDown`:它在 `pointerdown` 时对 `#graph-scroll` 调了
       * `setPointerCapture`。指针一旦被捕获,后续的 `pointerup` / `click` 会被**重定向到
       * 捕获元素**,`click` 的 `e.target` 于是变成 `#graph-scroll` 自己 ——
       * 从它往上 `closestAttr` 永远找不到那个 `<g data-id>`,`inspector.open()` 一次都没被
       * 调用过,右栏就一直停在「点击图谱中的任意节点」。
       *
       * **它不报错、不打日志**,只表现为「点节点没反应」;而 Node 测试是对 `<g>` 直接派发
       * `click` 的,正好绕过这条被重定向的路径 —— 所以这一处坏掉时测试是全绿的。
       *
       * 这条自洽性反过来印证了归因:全前端依赖 `click` 目标的**只有这一处**,而工具栏、
       * 缩放按钮、筛选芯片都是 `#graph-scroll` 的**兄弟**(见 `view()` 里的注释),
       * 压根不在捕获元素里 —— 它们一直好用,坏的正好是唯一那一处。
       *
       * 选中落在 `pointerup` 而不是 `pointerdown`:按下就选中会在拖动平移时**每拖一次
       * 就换一个节点**,而用户的意思是「把画布挪一下」。
       */
      let downHit = null;
      scroller.addEventListener('pointerdown', (e) => {
        if (e.button !== undefined && e.button !== 0) return;
        downHit = KP.dom.closestAttr(e.target, 'data-id', el);
      });
      // 系统抢走指针(触控板手势)时不该选中 —— 那一下不是「点击」。
      scroller.addEventListener('pointercancel', () => { downHit = null; });
      scroller.addEventListener('pointerup', (e) => {
        if (e.button !== undefined && e.button !== 0) { downHit = null; return; }
        // 捕获生效时 `e.target` 是容器,那时靠 `downHit`;没生效时两条都能命中。
        const hit = KP.dom.closestAttr(e.target, 'data-id', el) || downHit;
        downHit = null;
        // 拖动刚结束的那一次不算选中 —— 用户是在平移画布,不是点节点。
        // 阈值判定在 `graph/canvas.js`(`dragExceeds`),这里只消费结论。
        if (KP.graph.canvas.dragged()) return;
        if (hit) KP.views.inspector.open(hit.getAttribute('data-id'));
      });
      KP.graph.canvas.wire(scroller, {
        svg: () => KP.dom.q(scroller, 'graph-svg'),
        layout: () => curLayout,
        scale: () => S.view.scale,
        commit: (s) => { S.view.scale = s; S.view.fit = false; },
      });
      // 按钮 = 滚轮那条路径,只是把「光标」换成视口中心。**共用 `scrollAfterZoom`**:
      // 光标修正的式子只有一份,分开写的话按钮那条很容易写成 `scrollLeft * k` ——
      // 小比例下看不出来,缩到 2.5 倍时明显偏。
      const zoomBy = (dir) => {
        const before = S.view.scale;
        const after = KP.graph.canvas.scaleAfter(before, dir);
        if (after === before) return;   // 已到边界:别产生一次没有变化的滚动修正
        S.view.scale = after;
        S.view.fit = false;
        KP.graph.canvas.applyScale(KP.dom.q(scroller, 'graph-svg'),
                                   layout.width, layout.height, after);
        const at = KP.graph.canvas.scrollAfterZoom(
          scroller.scrollLeft, scroller.scrollTop,
          (scroller.clientWidth || 0) / 2, (scroller.clientHeight || 0) / 2,
          after / before);
        scroller.scrollLeft = at.left;
        scroller.scrollTop = at.top;
      };
      const zoomIn = KP.dom.q(el, 'zoom-in');
      const zoomOut = KP.dom.q(el, 'zoom-out');
      const zoomReset = KP.dom.q(el, 'zoom-reset');
      const zoomFit = KP.dom.q(el, 'zoom-fit');
      if (zoomIn) zoomIn.addEventListener('click', () => zoomBy(1));
      if (zoomOut) zoomOut.addEventListener('click', () => zoomBy(-1));
      if (zoomReset) zoomReset.addEventListener('click', () => {
        S.view.fit = false; S.view.scale = 1; renderGraph();
      });
      if (zoomFit) zoomFit.addEventListener('click', () => {
        S.view.fit = true; renderGraph();
      });
      applyView(scroller, layout);
      if (keep) { scroller.scrollLeft = keep.left; scroller.scrollTop = keep.top; }
    }
    if (S.nodeId) centerOn(S.nodeId, layout);
  }

  /**
   * 只重画一个节点盒子。**返回是否成功** —— 调用方拿 false 时应当退回全量渲染。
   *
   * **为什么要它**:点亮一个节点原来走 `renderGraph()`,而那是 `stage.innerHTML = ...`
   * 全量重建 —— 于是「点亮」这一个动作会①把缩放和平移清零 ②让画布上所有呼吸动画
   * 的相位从头开始 ③把视图拽回去跟用户刚做的平移打架。设计稿里「点亮 = 有完成动画」
   * 这条特色,在全量重建下根本做不出来。
   *
   * 返回 false 的场合:节点不在当前布局里(被筛掉了 / 换了布局)、或者对应的 `<g>`
   * 已经因为一次全量重建而消失。**这时候不能沉默** —— 沉默的表现是「点亮了但图上没变」,
   * 而用户不知道该刷新。所以调用方要按返回值决定要不要退回全量渲染。
   */
  function patchNodeEl(n) {
    const S = KP.S;
    const el = stage();
    if (!el || !n || !curLayout) return false;
    const box = curLayout.pos.get(n.id);
    if (!box) return false;
    const hosts = KP.dom.qa(el, '.node');
    const host = hosts.find((x) => x.getAttribute('data-id') === n.id);
    if (!host) return false;

    host.innerHTML = nodeParts(n, box, n.id === S.nodeId);
    // `st-*` 必须**整串重写**而不是 add —— 一次点亮只从 `unlearned` 换到 `mastered`,
    // 但对话中途后端也可能把它改成 `recommended`,残留的旧类会让两套样式打架
    // (视觉上像「点亮了但边框还是灰的」)。
    host.setAttribute('class', nodeClass(n, n.id === S.nodeId));
    // 「点亮」的弹一下。先读一次布局(`getBoundingClientRect`)把上面的类名改动
    // 刷进样式系统,再加 `pop`,否则浏览器会把两帧合并、动画根本不播。
    // 用 `getBoundingClientRect` 而不是 `offsetWidth`:SVG 元素没有 `offsetWidth`。
    if (typeof host.getBoundingClientRect === 'function') host.getBoundingClientRect();
    host.classList.add('pop');
    patchCenterRing();
    return true;
  }

  /**
   * 把中心环的进度跟着一次局部 patch 一起更新。
   *
   * 不更新的话,点亮一个节点之后环还停在旧长度 —— 而「环长 = 进度」是这张图上
   * 最直接的一个读数,差一格就是错的。之所以能这么做而不是全量重绘:`stroke-dashoffset`
   * 是**表现属性**,改属性不会重建 DOM,于是缩放/平移与邻居节点的动画相位全都保住。
   *
   * 半径为 0 / 环不存在(横向布局、多根图)时安静地什么都不做 —— 那不是错误状态。
   */
  function patchCenterRing() {
    const S = KP.S;
    const el = stage();
    const ring = el && KP.dom.q(el, 'center-ring');
    if (!ring || !S.graph) return;
    const r = Number(ring.getAttribute('r'));
    if (!(r > 0)) return;
    const geo = ringGeom(KP.pct((S.graph.topic || {}).progress), r);
    ring.setAttribute('stroke-dashoffset', geo.offset.toFixed(2));
  }

  /** 选中的节点滚进视野(图谱比容器宽时尤其需要)。 */
  function centerOn(nodeId, L) {
    const box = L.pos.get(nodeId);
    const el = stage();
    const scroller = el && KP.dom.q(el, 'graph-scroll');
    if (!box || !scroller) return;
    const s = KP.S.view.scale || 1;
    scroller.scrollLeft = Math.max(0, (box.x + box.w / 2) * s - (scroller.clientWidth || 0) / 2);
    scroller.scrollTop = Math.max(0, (box.y + box.h / 2) * s - (scroller.clientHeight || 0) / 2);
  }

  group.graph = {
    // 纯函数(可在 Node 里直接断言)
    svg, header, view, hotEdges, generateOutcome, cardTopic, nodeParts, nodeClass,
    filtersHTML, toolbarHTML, ringGeom, centerRingHTML, overlayHTML,
    welcomeHTML, completeHTML, overlayKindNow,
    // 生成阶段的模板(纯字符串):`generateCardHTML` 落右栏,`pendingHTML` 落中间列
    generateCardHTML, pendingHTML,
    // 落到 DOM 的那一层
    renderEmpty, renderGenerateCard, renderLoadError, renderGraph, generate, centerOn,
    patchNodeEl, syncOverlay,
  };
})(window.KP = window.KP || {});
