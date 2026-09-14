/**
 * 前端测试夹具。**字段形状逐条对齐后端真实契约**(见 `api/learning.py` 与 `learning/store.py`),
 * 特别是这几条容易记错的:
 *
 *   - 整图里的 node **没有** `prerequisites` / `body` / `topic_title` —— 那三个字段只在
 *     `GET /api/learning/nodes/{id}` 的单节点响应里。前置关系在整图里只能从 `edges` 推。
 *   - `edges[].relation` 恒为字符串 `"前置"`(`learning/path.py:41` RELATION)。
 *   - 所有标量字段都是 `NOT NULL DEFAULT`,没有 null;但 `type` / `summary` / `note_path`
 *     **可以是空字符串**,`key_points` 可以是 `[]`。
 *   - `progress.percent` 由后端算好(`round(mastered*100/total)`,`total==0` 时为 0),
 *     前端**不得重算** —— 两边舍入规则不同(银行家舍入 vs Math.round)。
 */

/** 一个字段齐全的 node。 */
export function node(over = {}) {
  const i = over.order_index ?? 0;
  return {
    id: over.id ?? `n${i}`,
    topic_id: over.topic_id ?? 'topic-1',
    name: over.name ?? `知识点${i}`,
    slug: over.slug ?? `node-${i}`,
    type: over.type ?? '概念',
    summary: over.summary ?? `这是第 ${i} 个知识点的摘要。`,
    key_points: over.key_points ?? ['要点甲', '要点乙'],
    depth: over.depth ?? 0,
    order_index: i,
    note_path: over.note_path ?? `data/knowledge/topic-1/0${i}_知识点${i}.md`,
    created_at: over.created_at ?? '2026-09-10T12:00:00+08:00',
    status: over.status ?? 'unlearned',
    recommend_reason: over.recommend_reason ?? '',
    confidence: over.confidence ?? 0,
    recommended_at: over.recommended_at ?? '',
    mastered_at: over.mastered_at ?? '',
    chat_turns: over.chat_turns ?? 0,
    ...over,
  };
}

export function topic(over = {}) {
  const total = over.total ?? 0;
  const mastered = over.mastered ?? 0;
  const recommended = over.recommended ?? 0;
  return {
    id: over.id ?? 'topic-1',
    title: over.title ?? '人工智能',
    query: over.query ?? '人工智能',
    summary: over.summary ?? '人工智能是通过计算系统模拟、延伸和扩展人类智能的技术与学科。',
    slug: over.slug ?? 'ai',
    status: over.status ?? 'ready',
    error: over.error ?? '',
    report_path: over.report_path ?? 'data/knowledge/topic-1/report.md',
    created_at: over.created_at ?? '2026-09-10T12:00:00+08:00',
    updated_at: over.updated_at ?? '2026-09-10T12:05:00+08:00',
    progress: {
      total,
      mastered,
      recommended,
      unlearned: total - mastered - recommended,
      // 后端是 round(mastered*100/total);这里照抄同样的语义,但**由夹具算好**,
      // 前端只读它 —— 测试要证明的就是「前端没有自己算」。
      percent: total === 0 ? 0 : Math.round((mastered * 100) / total),
      ...(over.progress || {}),
    },
    ...over,
  };
}

/** 把 nodes 按 order_index 排好、生成 edges、算出 topic 的 progress。 */
export function graph(nodes, edges, topicOver = {}) {
  const sorted = [...nodes].sort((a, b) => a.order_index - b.order_index);
  const mastered = sorted.filter((n) => n.status === 'mastered').length;
  const recommended = sorted.filter((n) => n.status === 'recommended').length;
  return {
    topic: topic(topicOver),
    nodes: sorted,
    edges: edges.map(([source, target]) =>
      Object.assign({}, { source_id: source, target_id: target, relation: '前置' })),
    order: sorted.map((n) => n.id),
    ...(topicOver.graphOver || {}),
  };
}

/** 降级链:LLM 抽取失败时 `nodes_from_headings` 产出的纯线性章节链。 */
export function chain(n = 12) {
  const nodes = Array.from({ length: n }, (_, i) =>
    node({
      id: `c${i}`,
      order_index: i,
      depth: i,
      name: `章节${i + 1}`,
      type: '章节',
      summary: '',
      key_points: [],
    }));
  const edges = Array.from({ length: n - 1 }, (_, i) => [`c${i}`, `c${i + 1}`]);
  return graph(nodes, edges, { title: '线性章节主题' });
}

/** 健康形状:1 个根 + n 个叶子。径向布局的目标形态。 */
export function star(n = 11) {
  const nodes = [
    node({ id: 'root', order_index: 0, depth: 0, name: '人工智能', type: '概念' }),
    ...Array.from({ length: n }, (_, i) =>
      node({
        id: `l${i}`,
        order_index: i + 1,
        depth: 1,
        name: `子领域${i + 1}`,
        type: i % 2 ? '技术' : '方法',
      })),
  ];
  const edges = Array.from({ length: n }, (_, i) => ['root', `l${i}`]);
  return graph(nodes, edges, {
    title: '人工智能',
    mastered: 3,
    total: n + 1,
    recommended: 2,
  });
}

/** 同环巨宽:depth 1 挤 12 个节点 —— 径向布局最容易重叠的最坏情况。 */
export function wideRing(n = 12) {
  const nodes = [
    node({ id: 'root', order_index: 0, depth: 0, name: '根', type: '概念' }),
    ...Array.from({ length: n }, (_, i) =>
      node({ id: `w${i}`, order_index: i + 1, depth: 1, name: `环上知识点${i + 1}` })),
  ];
  const edges = Array.from({ length: n }, (_, i) => ['root', `w${i}`]);
  return graph(nodes, edges, { title: '宽环主题' });
}

/**
 * 反向边:破环时强制放行产生的 `depth` 反向边(`learning/path.py:219-222`)。
 * A 依赖 B、B 依赖 A → order 被强制成 [B, A] → depth[B]=0, depth[A]=1,但边 A→B 指向内环。
 */
export function cycle() {
  const nodes = [
    node({ id: 'b', order_index: 0, depth: 0, name: 'B' }),
    node({ id: 'a', order_index: 1, depth: 1, name: 'A' }),
  ];
  return graph(nodes, [['b', 'a'], ['a', 'b']], { title: '环' });
}

/** 空图:没生成出任何知识点。`progress.total === 0` —— 完成态判据必须挡住这个。 */
export function emptyGraph() {
  return graph([], [], { title: '空主题', status: 'empty', total: 0, mastered: 0 });
}

/**
 * **降级产物**:抽取失败时后端拿研究计划的每一步当节点(`learning/path.py` 的
 * `nodes_from_plan`,以及 `nodes_from_headings` 那条同形状的路径)。
 *
 * 形状上它跟一张正常的图**一模一样** —— 节点数、边数、`status='ready'` 都对,
 * 差别只在两处,而且两处都是「少东西」而不是「多东西」:每个节点的 `key_points`
 * 是空的,`summary` 就是那一步的 purpose(研究问题式的句子,不是技术方法)。
 *
 * 这正是第七轮那个 bug 的样子:用户找「rerank 技术」,期待看到「交叉编码器重排」
 * 这类具体方法,拿到的却是「有哪些主流实现方案」——而界面上没有任何东西提示他
 * 这张图不是抽出来的。夹具照真实记录的字段抄,`type` 用降级路径会给的那个值。
 */
export function degraded() {
  const names = [
    '重排技术的主流方案有哪些',
    '重排模型的训练数据从哪来',
    '怎么评估重排的效果',
    '重排的工程落地与延迟',
  ];
  const nodes = names.map((name, i) => node({
    id: `p${i}`,
    order_index: i,
    depth: i === 0 ? 0 : 1,
    name,
    type: '章节',
    summary: `研究这一步是为了搞清楚${name}。`,
    key_points: [],
  }));
  const edges = names.slice(1).map((_, i) => ['p0', `p${i + 1}`]);
  return graph(nodes, edges, { title: 'rerank 技术', total: names.length });
}

/** 单节点:1 个 depth-0 —— 单根时中心应当就是它本人。 */
export function single() {
  return graph([node({ id: 'solo', order_index: 0, depth: 0, name: '唯一知识点' })], [], {
    title: '单点主题',
  });
}

/** 四个知识点、状态各不同 —— 用于四态视觉与筛选测试。 */
export function fourStates() {
  const nodes = [
    node({ id: 'u', order_index: 0, depth: 0, name: '未学', status: 'unlearned', chat_turns: 0 }),
    node({ id: 's', order_index: 1, depth: 1, name: '在学', status: 'unlearned', chat_turns: 3 }),
    node({
      id: 'r',
      order_index: 2,
      depth: 1,
      name: '待确认',
      status: 'recommended',
      chat_turns: 5,
      recommend_reason: '已经讲清楚了三要素',
      confidence: 0.82,
    }),
    node({
      id: 'm',
      order_index: 3,
      depth: 2,
      name: '已掌握',
      status: 'mastered',
      chat_turns: 4,
      mastered_at: '2026-09-10T13:00:00+08:00',
    }),
  ];
  return graph(nodes, [['u', 's'], ['s', 'r'], ['s', 'm']], {
    title: '四态主题',
    total: 4,
    mastered: 1,
    recommended: 1,
  });
}

/** 全部点亮 —— 完成态。 */
export function allMastered() {
  const nodes = Array.from({ length: 3 }, (_, i) =>
    node({ id: `m${i}`, order_index: i, depth: i, name: `点${i}`, status: 'mastered' }));
  return graph(nodes, [['m0', 'm1'], ['m1', 'm2']], {
    title: '全点亮',
    total: 3,
    mastered: 3,
  });
}

export const XSS = '<img src=x onerror=alert(1)>';

/**
 * 对抗性夹具:每个字符串字段都被塞进 XSS payload。
 * 任何一处漏了 `esc()`,渲染产物里就会冒出 `<img`。
 */
export function xssGraph() {
  const bad = {
    name: XSS,
    type: XSS,
    summary: XSS,
    key_points: [XSS],
    recommend_reason: XSS,
    note_path: XSS,
    slug: XSS,
  };
  const nodes = [
    node({ id: 'x0', order_index: 0, depth: 0, ...bad }),
    node({ id: 'x1', order_index: 1, depth: 1, ...bad }),
    node({ id: 'x2', order_index: 2, depth: 1, ...bad }),
  ];
  const g = graph(nodes, [['x0', 'x1'], ['x0', 'x2']], {
    title: XSS,
    query: XSS,
    summary: XSS,
    error: XSS,
    total: 3,
  });
  g.edges[0].relation = XSS;
  return g;
}

/**
 * 病态夹具:空串、空数组、0、未知枚举。
 * 渲染产物**不得**出现 `undefined` / `NaN` / `[object Object]`。
 */
export function pathological() {
  const nodes = [
    node({
      id: 'p0', order_index: 0, depth: 0, name: '', type: '', summary: '',
      key_points: [], note_path: '', status: 'unlearned', chat_turns: 0,
    }),
    node({
      id: 'p1', order_index: 1, depth: 1, name: '未知状态',
      type: '概念|技术|方法|工具', status: 'something-new', chat_turns: 0,
    }),
    node({
      id: 'p2', order_index: 2, depth: 1, name: '空类型',
      type: '', summary: '', key_points: [], status: 'recommended',
      recommend_reason: '', confidence: 0,
    }),
  ];
  const g = graph(nodes, [['p0', 'p1'], ['p0', 'p2']], {
    title: '', query: '', summary: '', error: '', status: 'weird', total: 0, mastered: 0,
  });
  g.topic.progress = { total: 0, mastered: 0, recommended: 0, unlearned: 0, percent: 0 };
  return g;
}

/** 超长名称 —— 径向布局的环半径必须被 clamp,不能被它撑爆。 */
export function longName(len = 200) {
  const nodes = [
    node({ id: 'ln0', order_index: 0, depth: 0, name: '根' }),
    node({ id: 'ln1', order_index: 1, depth: 1, name: '甲'.repeat(len) }),
    node({ id: 'ln2', order_index: 2, depth: 1, name: '乙'.repeat(len) }),
  ];
  return graph(nodes, [['ln0', 'ln1'], ['ln0', 'ln2']], { title: '长名' });
}

export const ALL = {
  chain, star, wideRing, cycle, emptyGraph, single, fourStates,
  allMastered, xssGraph, pathological, longName,
};
