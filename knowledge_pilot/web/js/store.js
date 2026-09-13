/**
 * 单一状态源 + 派生纯函数。**零 DOM、零 fetch、零副作用** —— 每个选择器都只吃参数、
 * 只吐数据,所以 Node 里可以直接 `KP.STATE` / `KP.stateOf(fixture)` 断言,
 * 不需要浏览器。
 *
 * 为什么状态要集中:`setMastery()` 以前是 `renderPanel(); renderGraph();`,
 * 而 `renderGraph` 是 `stage.innerHTML = ...` 全量重建 —— 一引入缩放/平移,
 * 每次点亮都会把视图拽回去、呼吸动画相位归零。集中之后才能做「只 patch 那一个盒子」。
 */
(function (KP) {
  'use strict';

  /**
   * 全局可变状态。**只有 `app.js` 与各 view 的事件处理器会写它**;
   * 选择器一律读参不读它,以便单测。
   */
  const S = {
    // 数据
    topics: [],          // GET /api/learning/topics
    graph: null,         // GET /api/learning/topics/{id} → {topic, nodes, edges, order}
    node: null,          // GET /api/learning/nodes/{id}
    messages: [],        // GET /api/learning/nodes/{id}/messages

    // 选择
    topicId: null,
    nodeId: null,

    // 忙碌标志。**刻意拆成三个**(原来是一个 `busy`):生成主题和「某个节点在对话」
    // 是互不相干的两件事,合成一个会让「生成中不能聊天」这种限制凭空出现。
    busyGenerate: false,
    busyChat: null,      // 正在流式对话的 nodeId,没有则是 null

    // 不可用(后端把学习图谱关掉了 → 503)
    disabled: false,
    disabledReason: '',

    // 视图与筛选(第 5 阶段把视图状态挪进来,点亮才能走局部 patch)
    // `mode` 为 null 表示「由 `KP.layout.choose()` 按图的形状定」;用户手动切过一次
    // 就钉在他选的那个上。**不进 hash**:布局偏好不是位置,刷新丢了也不影响能不能用。
    view: { scale: 1, fit: true, mode: null, completeSeenFor: null },
    filters: { status: 'all', type: 'all' },

    // 左下角浮动助手(设计稿 §21)。**`open` 默认 false** —— 它是「辅助学习入口」
    // (设计稿 §18 的「开始学习」),不是一个自动弹出来的东西:每次切节点都自动
    // 展开会盖住画布,而用户此刻可能只是在找路。
    // `err` 是历史拉取失败时的一行说明;不留着它的话,「拉不到历史」和「还没有
    // 聊过」在界面上长得一模一样。
    assistant: { open: false, err: '' },

    /**
     * 提纲(`## 提纲`)的生成状态。**按需生成** —— 打开一个还没有提纲的节点时
     * 才发一次 POST,结果由后端写进 Markdown 并随响应回传新的 `body`。
     *
     * `state`:'idle'(还没试)/ 'loading' / 'error'(失败了,面板上给重试按钮)。
     * `forNode` 是这份状态属于哪个节点 —— 与 `busyChat` 同一个理由:切节点之后
     * 晚到的响应绝不能再往**新**节点的面板上写。提纲本身不在 `S` 里存一份,
     * 它就在 `S.node.body` 里(那是唯一的真相,而且刷一次页面就回来)。
     */
    outline: { forNode: null, state: 'idle', err: '' },

    /**
     * 右栏「讲解记录」页（走查反馈 ④）。`open` 时右栏换成那一页：讲解记录（只读，
     * 正文渲染）+「我的笔记」的编辑框 + 保存。
     *
     * `draft` 是**没保存的编辑内容**，`null` = 没改过（于是显示文件里那一段的原文）。
     * 刻意不用 `''` 表示「没改过」——空字符串是一个合法的编辑结果（用户清空了笔记），
     * 用同一个值表示两件事的话，「清空 → 切走 → 回来」会静默地把原文重新填回输入框，
     * 而用户以为自己清掉了（同 `prefs` 那段「别把 undefined 当布尔用」的理由）。
     *
     * 草稿住在 `S` 而不是 DOM 里：右栏每次重绘都换掉 `innerHTML`，草稿留在文本框里
     * 就一定会在某一次 `refreshNode()`（聊完一轮）之后消失——那种丢失是静默的。
     * 于是每处渲染都要用 `draft ?? base ?? 原文` 把值写回 `.value`。
     *
     * `base`（服务端那一段的**基准**，也是「改过没有」的比较对象）刻意**不**从
     * `S.node.body` 现取：正文有好几个异步写入者（`refreshNode` / `ensureOutline`），
     * 一份在保存之后才到的旧正文会把基准拽回去，于是刚存好的字被判成「未保存」
     * ——屏幕上「保存了却显示未保存」，磁盘上却早就写好了（走查反馈 ⑤）。
     */
    note: { open: false, draft: null, base: null, busy: false, err: '', msg: '' },

    // 「对话」页(`#/chat`,对 `/api/chat` 的一次性研究问答)。**不落库、不跨刷新**:
    // 后端那个端点本来就没有历史 GET,假装有历史比没有历史更糟。
    // `opts` 是调用方显式覆盖的接缝(目前无人写);设置页那个轮次上限走 `prefs`,
    // 由 `KP.researchOpts()` 合进来 —— 两条来源各只有一处写点,不互相盖。
    research: { messages: [], busy: false, opts: {} },

    // 路由
    route: { view: 'graph', topicId: null, nodeId: null },

    /**
     * 这一屏是**怎么进来的**。`deepLink` 只在**首次**路由时判定一次:
     * 地址栏带着 `#/g/<topic>` 或 `#/g/<topic>/n/<node>` 打开 = 用户手里有链接,
     * 他不是「第一次看到这张图」——首次进入遮罩挡的就是这种人(见 `overlayKind`)。
     * 页内后续的跳转(点侧栏主题、生成完自动落 hash)都不再算深链接。
     */
    entry: { deepLink: false },
  };

  /**
   * 三态视觉表。**四通道编码** —— 底色 + 边框(含虚线)+ 角标字形 + 尾注文字。
   * 这不是冗余:色盲用户与黑白打印下,只有底色和边框两种通道会同时失效,
   * 字形与文字仍能区分。**改动时不要「顺手统一」掉虚线或角标。**
   *
   * 色值在 Stage 4 换成了设计稿的调色(§27:主色 #356dff / 成功 #20b486),
   * 与 `css/tokens.css` 里的 `--ok`、`--warn`、`--line` 是同一套 —— 但**不能**直接
   * 用 `var(--ok)`,因为这几个值是写进 SVG 的 `fill=` / `stroke=` **属性**的,
   * 属性不认 CSS 变量(只有 `style=` 才认)。所以这里有第二次字面量,改配色时两边都要改;
   * `store.test.mjs` 里那条「逐字一致」的表就是为了让漏改一处当场变红。
   *
   * 字形取自 `KP.icons.STATE_GLYPH`,不在这里再抄一遍 —— `icons.js` 存在的全部理由
   * 就是「同一个字形别散落在五个地方」。`learning`(学习中)那一个要等 Stage 5
   * 把四态接上才会用到。
   */
  const G = KP.icons.STATE_GLYPH;
  const STATE = {
    unlearned: {
      label: '未学', fill: '#ffffff', stroke: '#cbd5e1', dash: '5 4',
      glyph: G.unlearned, gfill: '#f1f5f9', gstroke: '#cbd5e1', gink: '#64748b',
    },
    learning: {
      label: '学习中', fill: '#f1efff', stroke: '#7c6cff', dash: '',
      glyph: G.learning, gfill: '#e9e5ff', gstroke: '#7c6cff', gink: '#4c3fd6',
    },
    recommended: {
      label: '待确认', fill: '#fff8e1', stroke: '#d4a72c', dash: '',
      glyph: G.recommended, gfill: '#fdf3d3', gstroke: '#d4a72c', gink: '#9a6700',
    },
    mastered: {
      label: '已掌握', fill: '#e4f7f0', stroke: '#20b486', dash: '',
      glyph: G.mastered, gfill: '#20b486', gstroke: '#20b486', gink: '#ffffff',
    },
  };

  /**
   * 节点 → 状态键。**四态,不是三态。**
   *
   * 后端的 `status` 只有三个值(`unlearned` / `recommended` / `mastered`),而设计稿
   * §3 要的是「未学习 / 学习中 / 已学习」三态加一个「待确认」。缺的那一个用**真实
   * 存在的** `chat_turns` 判:`status === 'unlearned'` 但**已经聊过**的节点就是「学习中」。
   *
   * 为什么不用设计稿 §3 的「学习中 8/15」分数:后端**没有子任务模型**,那个分数无处可来。
   * 编一个出来会让整个进度语义变假,而 `chat_turns` 是库里真有的数字 —— 「已对话 N 轮」
   * 是设计稿「学习中」唯一诚实的落法。
   *
   * 未知 `status` 一律兜底成 `unlearned`:显示成「未学」比显示成空白或 `[object Object]` 好。
   */
  function nodeState(n) {
    const status = String((n && n.status) || 'unlearned');
    if (status === 'mastered' || status === 'recommended') return status;
    if (status !== 'unlearned') return 'unlearned';
    return (Number(n && n.chat_turns) || 0) > 0 ? 'learning' : 'unlearned';
  }

  /**
   * 节点 → 视觉表项。未知 status **兜底成 `unlearned`**,绝不 `undefined`,
   * 否则渲染出来就是 `[object Object]` 或空白盒子。
   */
  function stateOf(n) {
    return STATE[nodeState(n)] || STATE.unlearned;
  }

  /**
   * 盒子底部那行字 —— **第四个通道**。四种状态给四句不同的话,所以「学习中」与
   * 「未学」在黑白打印下也分得开(底色、边框、字形之外的兜底)。
   *
   * 「学习中」那句要带上真实轮数,所以它不能是 `STATE` 表里的一个静态字符串 ——
   * 表保持纯查表,动态的部分放在这里。
   */
  function nodeFooter(n) {
    if (nodeState(n) === 'learning') {
      return `已对话 ${Number(n && n.chat_turns) || 0} 轮`;
    }
    return stateOf(n).label;
  }

  // ---- 筛选 -------------------------------------------------------------------

  const TYPE_FALLBACK = '未分类';

  /**
   * `node.type` 归一。**它不是一个可信的闭集。**
   *
   * prompt 里建议的是 `概念|技术|方法|工具`,但**不强制**;降级路径(`nodes_from_headings`)
   * 固定给「章节」;而 LLM 有时会把 prompt 里那串枚举**整串回吐**。不归一的话,筛选条上
   * 会出现一个叫「概念|技术|方法|工具」的选项 —— 它看起来像是设计如此。
   *
   * 整串回吐归「未分类」而不是拆成四个:一个节点只能落在一个 facet 里,拆开会让四个
   * 芯片各显示一遍同一个节点,点哪个都只有它,反而更费解。
   */
  function typeFacet(value) {
    const raw = String(value == null ? '' : value).trim();
    if (!raw) return TYPE_FALLBACK;
    if (raw.includes('|') || raw.includes('｜')) return TYPE_FALLBACK;
    return raw;
  }

  /**
   * 类型筛选芯片的数据。**不截断** —— 想过给个 8 个的上限,放弃了:截断会让被砍掉的
   * 那些节点再也筛不出来(它们还在「全部」里,但用户不知道自己少了什么),而这种
   * **静默的能力缺失**比多几个芯片难查得多。芯片行本来就是 `flex-wrap: wrap`。
   *
   * 排序必须**稳定**:按数量降序、同数量按名字。不稳的话每次渲染芯片都换位置,
   * 用户按「技术」的那一下可能正好按到刚挪过来的「概念」——而它不会报错。
   */
  function typeFacets(nodes) {
    const count = new Map();
    (nodes || []).forEach((n) => {
      const f = typeFacet(n && n.type);
      count.set(f, (count.get(f) || 0) + 1);
    });
    return [...count.entries()]
      .map(([key, n]) => ({ key, label: key, count: n }))
      .sort((a, b) => (b.count - a.count) || a.key.localeCompare(b.key, 'zh'));
  }

  /**
   * 按筛选条件算出**可见的节点集合**。**不重排布局** —— 布局永远在**全量**节点上算,
   * 筛选只决定谁被画出来。
   *
   * 这一条是刻意的:如果按筛选后的子集重排,点一下「未学习」整张图会重新布局、
   * 剩下的节点换个位置,再点回「全部」又换一次 —— 用户失去的正是「我在这张地图上
   * 的位置」这个唯一有价值的东西。
   *
   * 边:**两端都可见才留**。只留一端会让那条线指向空气(布局层会把它丢掉,但
   * 不如在这里就丢,省得每次都要解释「为什么传入了一条画不出来的边」)。
   *
   * @returns `{nodes, edges, ids}` —— `ids` 是可见节点 id 的 Set,渲染层用它判断。
   */
  function filterNodes(nodes, edges, filters) {
    const f = filters || {};
    const keep = (n) => {
      if (f.status && f.status !== 'all' && nodeState(n) !== f.status) return false;
      if (f.type && f.type !== 'all' && typeFacet(n && n.type) !== f.type) return false;
      return true;
    };
    const visible = (nodes || []).filter(keep);
    const ids = new Set(visible.map((n) => n.id));
    const kept = (edges || []).filter((e) => ids.has(e.source_id) && ids.has(e.target_id));
    return { nodes: visible, edges: kept, ids };
  }

  /**
   * 与某个知识点直接相连的节点(右栏 Inspector 的「相关节点」,设计稿 §17)。
   *
   * **只从 `edges` 推,不看节点字段** —— 整图接口返回的 node **没有** `prerequisites`
   * 字段,那是单节点接口才有的一份冗余。拿它当唯一来源的话,右栏会依赖一个图上
   * 根本不存在的字段,于是「相关节点」永远是空的,而页面看起来只是「这个点没有
   * 邻居」。
   *
   * `dir`:`'out'` = 以它为前置(学了才能学它),`'in'` = 它的前置。前端显示箭头方向
   * 用得上,而且两种方向对「下一步该学什么」的含义正好相反。
   *
   * **排序必须确定**:先出后入,同向按 `(order_index, name)`。不稳的话每次点节点
   * 列表顺序都变,而用户正想按着这个列表找下一个 —— 列表在眼前跳来跳去。
   *
   * 自环(`source === target === nodeId`)会被排除:把自己列成自己的相关节点,
   * 点进去只是原地不动。
   *
   * @returns `[{node, dir, relation}]`,取不到的边一律丢掉(不返回 `{node: undefined}`)。
   */
  function relatedNodes(graph, nodeId) {
    const g = graph || {};
    if (!nodeId) return [];
    const byId = new Map(((g.nodes) || []).map((n) => [n.id, n]));
    const out = new Set();
    const into = new Set();
    (((g.edges) || [])).forEach((e) => {
      // 先判 out:一条边两端都指着 nodeId 时(理论上不该有)按「出」处理,与
      // 「优先取 out」的排序意图一致。
      if (e.source_id === nodeId) out.add(e.target_id);
      else if (e.target_id === nodeId) into.add(e.source_id);
    });
    const ids = [...new Set([...out, ...into])].filter((id) => id !== nodeId);
    return ids
      .map((id) => ({ node: byId.get(id), dir: out.has(id) ? 'out' : 'in', relation: '' }))
      .filter((r) => !!r.node)
      .sort((a, b) => {
        if (a.dir !== b.dir) return a.dir === 'out' ? -1 : 1;
        const oa = Number(a.node.order_index) || 0;
        const ob = Number(b.node.order_index) || 0;
        return (oa - ob) || String(a.node.name || '').localeCompare(String(b.node.name || ''), 'zh');
      });
  }

  /**
   * 主题的阶段。**`total === 0` 绝不算完成** —— 空主题的 `percent` 后端返回 0
   * (`store.py:550` `round(...) if total else 0`),若把「无节点」当「全掌握」,
   * 新建的空主题会立刻显示「已完成」。
   */
  function corePhase(progress) {
    const p = progress || {};
    const total = Number(p.total) || 0;
    const mastered = Number(p.mastered) || 0;
    if (total <= 0) return 'idle';
    if (mastered >= total) return 'done';
    return mastered > 0 ? 'active' : 'idle';
  }

  const isComplete = (progress) => corePhase(progress) === 'done';

  /**
   * 进度百分比。**只读后端的 `percent`,绝不自己算。**
   *
   * 后端是 Python `round(mastered * 100 / total)` —— 银行家舍入:`round(12.5) == 12`。
   * 而 JS 的 `Math.round(12.5) == 13`。自己算就会让侧栏(用 API 的 percent)与中心环
   * 在 1/8 这类比例上**差 1%**,而那正是设计稿最显眼的数字。宁可两侧共用同一个值。
   */
  function pct(progress) {
    const v = Number(progress && progress.percent);
    return Number.isFinite(v) ? v : 0;
  }

  /** 主题状态 → 中文标签(`TOPIC_STATUSES = empty|generating|ready|failed`)。 */
  const TOPIC_LABEL = { empty: '待生成', generating: '生成中', ready: '已就绪', failed: '生成失败' };
  const topicLabel = (status) => TOPIC_LABEL[status] || String(status == null ? '' : status);

  /**
   * 从主题列表里取出当前选中的那一条。**取不到就返回 null,不抛**。
   *
   * 侧栏的进度卡与顶栏都读它,两边必须显示**同一个数**:进度卡用列表接口的
   * `progress`,顶栏用的是整图接口里 `topic.progress` —— 两条路径在后端是同一段
   * 计算(`store.py` 的 `_progress`),但一个是列表时的快照、一个可能是更新过的。
   * 所以侧栏一律以列表为准,顶栏以整图为准,谁也不去"顺手补算"。
   *
   * 返回 null 的两种情况(没有选中 / 选中的主题已从列表里消失)在视图层是同一件事:
   * 不渲染进度卡。显示一张 0% 的卡会被读成「这个主题一个知识点都没学」。
   */
  function currentTopic(topics, topicId) {
    if (!topicId) return null;
    return (topics || []).find((t) => t && t.id === topicId) || null;
  }

  /**
   * 加载主题失败时把错误分成两类。**这是拆出纯函数的理由**:两个分支的正确行为
   * 完全相反,混在一起就会退化成「所有错误都当成主题不存在」——
   *
   *   - `404` → 主题真的没了:清掉 `topicId` **并同步清 hash**,否则刷新后
   *     仍然指着一个失效 id,再次失败,用户看不到任何可恢复的出口。
   *   - 其它(网络抖动 / 500 / 超时)→ 主题大概还在:保留 `topicId` 与 hash,
   *     给一个「重试」入口。清掉就等于因为一次网络抖动把用户的深链接扔了。
   *
   * 判据只看 `err.status`(`KP.api` 的 `errorFrom` 会带上响应码);拿不到状态码的
   * 时候按 `error` 处理 —— 宁可多给一次重试,也不要误判成「已删除」。
   */
  function loadFailureKind(err) {
    return Number(err && err.status) === 404 ? 'missing' : 'error';
  }

  /**
   * 首次进入 / 全部点亮 两种遮罩该出哪一个。**纯函数**。
   *
   * `null` / `'welcome'`(设计稿 §24)/ `'complete'`(§25)。判据全部来自传入的参数,
   * 不读 `KP.S`、不读 `localStorage` —— 调用方(`views/graph.js`)把 `seen` 这类
   * 带副作用的东西查好了传进来,所以这张表在 Node 里可以正面枚举。
   *
   * 三条容易被写错的规则,都钉在测试里:
   * 1. **`total === 0` 两个都不出**。空主题的 `percent` 后端给 0,若按「没节点 = 全掌握」
   *    判完成,新建的空主题一进去就是「知识体系已点亮」。
   * 2. **「全部点亮」优先于「首次进入」**,而且完成态不因 `seen` 而出「开始学习」——
   *    所有节点都亮着的时候让用户「开始学习」是自相矛盾的提示。
   * 3. **深链接 / 已选节点都不出首进入遮罩**。地址栏带着 `#/g/t1/n/n5` 打开的人手里
   *    有链接,他要看的是那个节点;再糊一层「你的知识地图已经建立」等于把深链接废掉。
   */
  function overlayKind(graph, ctx) {
    const g = graph || {};
    const c = ctx || {};
    const prog = (g.topic && g.topic.progress) || {};
    if ((Number(prog.total) || 0) <= 0) return null;
    if (corePhase(prog) === 'done') return c.completeSeen ? null : 'complete';
    if (c.seen || c.deepLink || c.nodeId) return null;
    return 'welcome';
  }

  // ---- 浏览器本地偏好 ---------------------------------------------------------

  const PREFIX = 'kp.';

  /**
   * **全前端唯一碰 `localStorage` 的地方。**
   *
   * 存在的理由:设计稿 §23 把「研究问答轮次上限」这类偏好归到设置页,而那一屏是
   * 纯客户端的 —— 后端没有配置端点,计划里明确禁止为设置页新增「读服务端配置」的
   * 调用。偏好于是只能留在这台浏览器里。
   *
   * 三条纪律:
   * 1. **只在函数体里访问 `localStorage`**,顶层一次都不碰 —— 与「加载期不得触碰
   *    DOM/location」同源。顶层读一次偏好会顺带把「模块可以单独加载」这个前提弄没。
   * 2. **一律 try/catch**。`localStorage` 真的会抛:Safari 无痕模式下 `setItem` 抛
   *    `QuotaExceededError`,一些企业策略下连读都抛。为一点便利把整页搭进去不值。
   *    读失败 → 兜底值;写失败 → `false`,让调用方知道*没存上*(而不是假装成功)。
   * 3. **值一律 `JSON.stringify`**。`localStorage` 只存字符串,不记类型的话
   *    `'false'` 读回来是**真值** —— 一个「关掉的开关」会在下次打开时自己变回开。
   *
   * `fallback` 参数是必需的而不是可选的:调用点必须想清楚「没这回事时按什么算」。
   * 有默认值的地方就会有人不写,而 `undefined` 当布尔用永远是对的 —— 于是静默出错。
   */
  const prefs = {
    get(key, fallback) {
      try {
        const raw = globalThis.localStorage.getItem(PREFIX + key);
        return raw == null ? fallback : JSON.parse(raw);
      } catch (err) {
        return fallback;
      }
    },
    set(key, value) {
      try {
        globalThis.localStorage.setItem(PREFIX + key, JSON.stringify(value));
        return true;
      } catch (err) {
        return false;
      }
    },
    remove(key) {
      try {
        globalThis.localStorage.removeItem(PREFIX + key);
        return true;
      } catch (err) {
        return false;
      }
    },
  };

  /** 「对话」页那一轮研究的请求参数。设置页选的轮次上限在这里合进来。 */
  function researchOpts() {
    const opts = Object.assign({}, S.research.opts);
    const rounds = Number(prefs.get('research.maxToolRounds', 0));
    // 后端把 `max_tool_rounds` 钳到 `[1, agent_rounds_hard_cap]`(`clamp_rounds`),
    // 所以这里传一个超界的值不会出错,只会被钳。0 / NaN / 负数 = 没设过,整个不发。
    if (rounds > 0) opts.max_tool_rounds = rounds;
    return opts;
  }

  // ---- 路由 -------------------------------------------------------------------
  //
  // 形如 `#/`、`#/g/<topicId>`、`#/g/<topicId>/n/<nodeId>`、`#/explore`、`#/chat`、`#/settings`。
  // 纯字符串进出 —— 不读 `location`,所以可单测;真正的写入在 `views/shell.js`,
  // 且**只能用 `history.replaceState`**:赋 `location.hash` 会触发 `hashchange`,
  // 与监听器形成死循环。

  /**
   * 解码一个 hash 段。**必须容错** —— `decodeURIComponent('%')` 会抛 `URIError`,
   * 而这里是在路由入口上:抛出去就是整页白屏,且用户只看到地址栏里一个手写的
   * 百分号。解不开就原样用,至少页面还在。
   */
  function decodePart(part) {
    try {
      return decodeURIComponent(part);
    } catch (err) {
      return part;
    }
  }

  function parseHash(hash) {
    const raw = String(hash == null ? '' : hash).replace(/^#/, '');
    const parts = raw.split('/').filter(Boolean);
    if (parts.length === 0) return { view: 'graph', topicId: null, nodeId: null };
    if (parts[0] === 'g') {
      // 与 `formatHash` 的 `encodeURIComponent` **必须成对**:只编码不解码时,
      // id 里带 `/` 的主题(`#/g/a%2Fb`)会被读成字面量 `a%2Fb`,与真实 id 不等,
      // 于是刷新后「主题不存在」。编码解码是同一个往返,不能只做一半。
      const topicId = parts[1] ? decodePart(parts[1]) : null;
      const nodeId = parts[2] === 'n' && parts[3] ? decodePart(parts[3]) : null;
      return { view: 'graph', topicId, nodeId };
    }
    const known = ['explore', 'chat', 'settings'];
    if (known.indexOf(parts[0]) >= 0) {
      return { view: parts[0], topicId: null, nodeId: null };
    }
    // 未知 hash 落回图谱首页,而不是白屏。
    return { view: 'graph', topicId: null, nodeId: null };
  }

  function formatHash(route) {
    const r = route || {};
    if (r.view === 'explore' || r.view === 'chat' || r.view === 'settings') return '#/' + r.view;
    if (r.topicId) {
      return r.nodeId
        ? '#/g/' + encodeURIComponent(r.topicId) + '/n/' + encodeURIComponent(r.nodeId)
        : '#/g/' + encodeURIComponent(r.topicId);
    }
    return '#/';
  }

  KP.S = S;
  KP.STATE = STATE;
  KP.stateOf = stateOf;
  KP.nodeState = nodeState;
  KP.nodeFooter = nodeFooter;
  KP.typeFacet = typeFacet;
  KP.typeFacets = typeFacets;
  KP.filterNodes = filterNodes;
  KP.relatedNodes = relatedNodes;
  KP.corePhase = corePhase;
  KP.isComplete = isComplete;
  KP.pct = pct;
  KP.topicLabel = topicLabel;
  KP.currentTopic = currentTopic;
  KP.loadFailureKind = loadFailureKind;
  KP.overlayKind = overlayKind;
  KP.prefs = prefs;
  KP.researchOpts = researchOpts;
  KP.route = { parseHash, formatHash };
})(window.KP = window.KP || {});
