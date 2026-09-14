/**
 * 视图层:`views/graph.js` 的字符串产物 + 用 DOM 桩驱动的状态机。
 *
 * 这一组补的是「纯函数测不到」的那部分 —— 生成流程的 `finally`、503 不可用分支、
 * 主题加载失败的两种走法。它们的共同点是**失败时页面看起来只是「没反应」**:
 * 按钮停在「生成中…」、侧栏空白、错误被显示成空态。没有异常可抓,所以只能真的
 * 把流程跑一遍再断言状态。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { loadKP, makeShellElements, plain, FakeElement } from './harness.mjs';
import * as F from './fixtures/graphs.mjs';

/** 只加载、不开挂载点(测纯函数用)。 */
const bare = await loadKP();
const graphView = bare.KP.views.graph;

// 取**整个 `<rect>` 元素**,不是只取 class 那一段 —— 只取 class 的话
// 「选中盒子 stroke-width 是 2.4」这类断言永远为假,而且看起来像代码写错了。
const boxesOf = (html) => html.match(/<rect class="box[^"]*"[^>]*>/g) || [];
const edgesOf = (html) => html.match(/<path class="edge[^"]*"[^>]*><\/path>/g) || [];

// ---- svg():纯函数,视觉契约全落在这里 ---------------------------------------

test('svg:盒子数量 == 可见节点数,每个都有 data-id 分组', () => {
  for (const name of Object.keys(F.ALL)) {
    const g = F.ALL[name]();
    const html = graphView.svg(g, {});
    assert.equal(boxesOf(html).length, g.nodes.length, `${name} 的盒子数不对`);
    for (const n of g.nodes) {
      assert.ok(html.includes(`data-id="${n.id}"`), `${name} 缺少节点 ${n.id}`);
    }
  }
});

test('svg:每条边都有 marker-end —— 选中时**也不**能丢', () => {
  // 这是拆分时修掉的笔误:原实现写成「hot 的边不加 marker-end」,于是
  // 「选中一个节点之后,与它相连的边反而没了箭头」——语义整个反了。
  // 加粗只是描边,和有没有箭头是正交的两件事。
  const g = F.star(4);
  const rootId = g.nodes[0].id;
  for (const opts of [{}, { selected: rootId }]) {
    const html = graphView.svg(g, opts);
    const edges = edgesOf(html);
    assert.equal(edges.length, 4);
    for (const e of edges) {
      assert.ok(e.includes('marker-end="url(#arrow)"'), `丢箭头了:${e}`);
    }
  }
  // 反过来也要成立:hot 的边确实被标了 hot(否则上面那条断言可能因为
  // 「一条 hot 边都没有」而恒真)。
  const sel = graphView.svg(g, { selected: rootId });
  assert.ok(sel.includes('class="edge hot"'), '选中节点后相连的边没有被标 hot');
});

test('svg:选中态只加在选中那个盒子上', () => {
  const g = F.star(3);
  const html = graphView.svg(g, { selected: g.nodes[1].id });
  const selected = boxesOf(html).filter((c) => c.includes('sel'));
  assert.equal(selected.length, 1);
  assert.ok(selected[0].includes('stroke-width="2.4"'));
});

test('svg:三态各自的底色/虚线/字形出现在产物里', () => {
  const g = F.fourStates();
  const html = graphView.svg(g, {});
  assert.ok(html.includes('stroke-dasharray="5 4"'), '虚线通道丢了');
  // 字形**从状态表现取**,不写死成某个字符 —— 断言的是「每个状态的字形都落进了产物」,
  // 这一条在任何一次改配色/换字形之后都还该成立(Stage 4 把已掌握从 ✓ 换成 ✦,
  // 写死字符会让这条跟着改,那就等于把「字形通道存在」这个契约测成了「字形是 ✓」)。
  const glyphs = Object.values(bare.KP.STATE).map((s) => s.glyph);
  for (const gl of glyphs) assert.ok(html.includes(`>${gl}</text>`), `字形 ${gl} 没进产物`);
  assert.ok(html.includes(bare.KP.STATE.mastered.fill), '已掌握的底色没进产物');
});

test('svg:XSS 夹具全域 —— 每个字符串字段塞 payload,产物不得出现真实元素', () => {
  for (const name of ['xssGraph', 'pathological']) {
    const html = graphView.svg(F.ALL[name](), { selected: 'x0' });
    assert.ok(!html.includes('<img'), `${name} 里有未转义的元素`);
    assert.ok(!html.includes('onerror=') || !html.includes('<img src'), name);
  }
});

test('svg:病态夹具全域扫描 —— 产物不含 undefined / NaN / [object Object]', () => {
  for (const name of Object.keys(F.ALL)) {
    const g = F.ALL[name]();
    const html = graphView.svg(g, { selected: g.nodes[0] && g.nodes[0].id });
    for (const bad of ['undefined', 'NaN', '[object Object]']) {
      assert.ok(!html.includes(bad), `${name} 的 svg() 产物里出现了 ${bad}`);
    }
  }
});

test('view():顶栏 / 筛选条 / 滚动容器 / 工具栏 / svg 都在,且同样干净', () => {
  // `class="legend"` 换成了 `class="filters"`:阶段 5 把纯展示的图例和筛选芯片
  // 合成了一行 —— 两者说的都是「四种状态」。断言跟着换,但**没有放松**:色块、
  // 状态名、方向说明仍在下一条用例里逐个钉住。
  for (const name of Object.keys(F.ALL)) {
    const g = F.ALL[name]();
    const html = graphView.view(g, { selected: null });
    // 这里原本断言 `id="regen"`。第 7 阶段按设计稿 §23 把它从顶栏撤了(能力挪进
    // 设置页),换上的 `class="canvas"` 是它原来所在的容器 —— 撤的是那一个按钮,
    // 不是「顶栏/画布」这一组结构,所以这条断言的**强度没有变**。
    for (const frag of ['class="topbar"', 'class="filters"', 'id="graph-scroll"',
                        'id="graph-fit"', 'class="graph"', 'class="canvas"',
                        'class="toolbar"', 'id="zoom-fit"']) {
      assert.ok(html.includes(frag), `${name} 缺少 ${frag}`);
    }
    for (const bad of ['undefined', 'NaN', '[object Object]']) {
      assert.ok(!html.includes(bad), `${name} 的 view() 里出现了 ${bad}`);
    }
  }
});

test('view():百分比只读后端值 —— 1/8 显示 12 而不是 13(R6)', () => {
  const g = F.star(7);
  g.topic.progress = { total: 8, mastered: 1, recommended: 0, unlearned: 7, percent: 12 };
  const html = graphView.view(g, {});
  assert.ok(html.includes('12%'), '没有读到后端给的 12');
  assert.ok(!html.includes('13%'), '前端自己算了百分比');
});

// ---- 降级可见(第七轮)-------------------------------------------------------
//
// 抽取失败时后端静默降级成「拿研究计划的每一步当节点」,而产物看起来完全像一张
// 正常的图(节点数、边数、`status=ready` 都对)。这三条钉住的是那唯一的补救:
// 让用户在图上**看得见**它不是抽出来的,并且知道能去哪儿重来。

test('view():每个节点都没有关键词 → 出提示,并指路设置页', () => {
  const html = graphView.view(F.degraded(), {});
  assert.ok(html.includes('class="notice"'), '降级图没有提示,用户无法分辨');
  assert.ok(html.includes('没有经过知识点抽取'), '提示没说清这张图是怎么来的');
  // 指路要具体到「设置页 →「当前探索」→「重新研究并生成」」。只说「可以重新生成」
  // 等于没说 —— 顶栏按设计稿 §23 本来就没有那个按钮(见 graph.js 的 header 注释),
  // 用户找不到入口。这条路径是不是**真的存在**,由下面「设置页」那组里的
  // 「降级提示指的路,在设置页里真的存在」机械守住。
  assert.ok(html.includes('设置页 →「当前探索」→「重新研究并生成」'),
    '提示没有告诉用户去哪儿重来');
  // 顶栏那个按钮**不许**因为这条提示回来:重跑会按新的 order_index 重算 note_path,
  // 把用户写在正文里的「## 我的笔记」变成孤儿。
  assert.ok(!html.includes('id="regen"'), '为了提示把顶栏那个重生成按钮加回来了');
  assert.ok(!html.includes('id="regen-graph"'), '新增了一个顶栏重生成入口');
});

test('view():只要有一个节点带关键词,就不出提示', () => {
  // 「每个都空」是判据,不是「有空的」。判据松成「任一为空」的话,一张正常图里
  // 偶尔有一个抽得潦草的节点就会让整张图被打上「没抽过」的标签 —— 那比不提示更糟。
  const g = F.degraded();
  g.nodes[1] = F.node({ id: 'p1', order_index: 1, key_points: ['交叉编码器重排'] });
  const html = graphView.view(g, {});

  assert.ok(!html.includes('没有经过知识点抽取'), '有一个节点有要点,不该判成降级');
  // 提示没了,但图本身一个都不能少 —— 「不加提示」不等于「少画东西」。
  assert.equal(boxesOf(html).length, g.nodes.length);
  assert.ok(html.includes('id="graph-scroll"'));
});

test('view():空图不出提示 —— 那是「还没生成」,另有说法', () => {
  const html = graphView.view(F.emptyGraph(), {});
  assert.ok(!html.includes('没有经过知识点抽取'), '空图被当成了抽取失败');
  assert.ok(!html.includes('class="notice"'), '空图不该出现这条提示');
});

test('view():判据认的是症状,不是「章节」这个类型', () => {
  // 降级有**两条**路径(`nodes_from_headings` 按标题分章 / `nodes_from_plan` 拿
  // 研究计划每步当节点),`chain` 夹具是前一条、`degraded` 是后一条。判据不依赖
  // 是哪一条来的,所以两条都要提示。
  const chainHTML = graphView.view(F.chain(4), {});
  assert.ok(chainHTML.includes('没有经过知识点抽取'), '按标题分章的那条降级路径漏掉了');

  // 反过来:类型写着「章节」但关键词是有内容的 → **不能**提示。这张图是模型
  // 真抽出来的,只是它给这个知识点标了「章节」;判据写成 `type === '章节'` 时
  // 这里会误报,而误报的代价是让用户以为自己的图坏了。
  const g = F.chain(2);
  assert.equal(g.nodes[0].type, '章节', '夹具的前提是类型真的写着「章节」');
  g.nodes[0].key_points = ['定长切分'];
  g.nodes[1].key_points = ['递归切分'];
  assert.ok(!graphView.view(g, {}).includes('没有经过知识点抽取'),
    '按类型判的话这里会误报 —— 判据必须是空关键词');
});

test('header():空主题 total 为 0 时不显示「已掌握 0 · 0%」以外的完成态措辞', () => {
  const g = F.emptyGraph();
  const html = graphView.header(g, {});
  assert.ok(html.includes('0 个知识点'));
  assert.ok(html.includes('0%'));
});

test('hotEdges:双向都算 —— 前置与被前置都该点亮', () => {
  const g = F.star(3);
  const rootId = g.nodes[0].id;
  const leafId = g.nodes[1].id;
  assert.equal(graphView.hotEdges(g, rootId).size, 3, '以根为选中,三条出边都该 hot');
  assert.equal(graphView.hotEdges(g, leafId).size, 1, '以叶为选中,只有它那条入边');
  assert.equal(graphView.hotEdges(g, null).size, 0);
});

test('generateOutcome:只有 ready 才重新加载,其余一律回到可重试', () => {
  assert.equal(graphView.generateOutcome({ status: 'ready' }), 'reload');
  for (const s of ['empty', 'generating', 'failed', 'weird', undefined]) {
    assert.equal(graphView.generateOutcome({ status: s }), 'retry', `${s} 判错了`);
  }
  assert.equal(graphView.generateOutcome(null), 'retry', '主题没刷回来就假装成功是最坏的');
});

// ---- cardTopic:生成卡画的是**当前**主题(走查反馈 ⑥)----------------------

test('cardTopic:刚新建的主题(S.graph 还是 null)也画得出标题与研究问题', () => {
  // 走查反馈 ⑥ 的原始症状:新建主题后点「开始生成」,卡片与中间列变成「未命名主题」。
  // 原因是重画卡片时从**整图**里反推主题,而 `onNewTopic` 建完就直接画卡,没走过
  // `loadTopic` —— `S.graph` 是 null,于是 title 是 undefined,模板里的
  // `|| '未命名主题'` 兜底生效。现在列表行才是主来源。
  const topics = [{ id: 't1', title: 'RAG 的 chunking 策略', query: 'RAG chunking', status: 'empty' }];
  const t = graphView.cardTopic(topics, 't1', null, 'empty');
  assert.equal(t.title, 'RAG 的 chunking 策略');
  assert.equal(t.query, 'RAG chunking');
  assert.equal(t.status, 'empty');

  // 一路画到两栏的产物上:卡片和中间列都得是那个查询,不是「未命名主题」
  for (const html of [graphView.generateCardHTML(t), graphView.pendingHTML(t)]) {
    assert.ok(html.includes('RAG 的 chunking 策略'), `标题丢了:${html.slice(0, 80)}`);
    assert.ok(!html.includes('未命名主题'), '又画成「未命名主题」了');
  }
  assert.ok(graphView.generateCardHTML(t).includes('研究问题：RAG chunking'));
});

test('cardTopic:S.graph 还留着**上一个**主题的图时,不许把它的名字画上来', () => {
  // 比「未命名主题」更坏的一种:上一个主题的图还在内存里,于是卡片上写着上一个主题
  // 的名字与研究问题 —— 用户以为自己在看新主题,其实看到的是别人的标题。
  const topics = [{ id: 't2', title: '新主题', query: '新问题', status: 'empty' }];
  const staleGraph = {
    topic: { id: 't1', title: '旧主题', query: '旧问题', status: 'ready', error: '旧错误' },
    nodes: [],
  };
  const t = graphView.cardTopic(topics, 't2', staleGraph, 'empty');
  assert.equal(t.title, '新主题');
  assert.equal(t.query, '新问题');
  assert.equal(t.error, undefined, '旧主题的失败原因跟着整个记录漏过来了');
  const html = graphView.generateCardHTML(t);
  assert.ok(!html.includes('旧主题') && !html.includes('旧问题'));
});

test('cardTopic:同一个主题时用整图那条补上列表没有的字段', () => {
  // 反过来的一半:整图接口带 `error` 与更新的 `progress`,列表接口没有 ——
  // 但**只在 id 对得上**时才合并。
  const topics = [{ id: 't1', title: '主题', query: 'q', status: 'empty' }];
  const graph = {
    topic: { id: 't1', title: '主题', status: 'failed', error: '上游 500',
             progress: { total: 3, mastered: 1 } },
    nodes: [],
  };
  const t = graphView.cardTopic(topics, 't1', graph, 'empty');
  assert.equal(t.error, '上游 500');
  assert.deepEqual(t.progress, { total: 3, mastered: 1 });
  assert.equal(t.status, 'empty', '生成中要按 empty 画,不能把上一次的 failed 顶在脸上');
  // 调用方没给 status 时,整图的状态才作数(生成卡以外的路径会这么用)
  assert.equal(graphView.cardTopic(topics, 't1', graph).status, 'failed');
});

test('cardTopic:两条记录都没有时给一个空壳,而不是抛异常', () => {
  // 不抛异常是重点:调用方紧接着就要 `generateCardHTML(t)`,那里读的是
  // `t.status` / `t.title` —— 没主题时能画出一张「未命名主题」的空卡即可。
  for (const topics of [null, [], undefined]) {
    const t = graphView.cardTopic(topics, 'nope', null, 'empty');
    // 逐字段比而不是 `deepEqual`:对象是在 `vm` 沙箱里造的,跨 realm 的
    // `deepStrictEqual` 会因为原型不同而报「结构相同但引用不等」。
    assert.equal(t.status, 'empty');
    assert.equal(t.title, undefined);
  }
});

test('generate:点下去当场重画的卡片仍写着这个主题的名字(走查反馈 ⑥)', async () => {
  // 端到端那一半:`generate()` 开头那次 `renderGenerateCard` 用的就得是 cardTopic 的产物。
  // 用户报的就是这一屏 —— 新建主题(从没 loadTopic,`S.graph` 是 null)后点「开始生成」,
  // 右栏卡片与中间列同时变成「未命名主题」。
  const m = await mounted({
    routes: {
      '/api/learning/topics/t1/generate': { json: {} },
      '/api/learning/topics/t1': { json: F.emptyGraph() },
      '/api/learning/topics': { json: { topics: [] } },
    },
  });
  m.KP.S.topics = [
    { id: 't1', title: 'RAG 的 chunking 策略', query: 'RAG chunking', status: 'empty' },
  ];
  m.KP.S.topicId = 't1';
  m.KP.S.graph = null;

  await m.KP.views.graph.generate();

  assert.ok(m.panel.innerHTML.includes('RAG 的 chunking 策略'), '右栏卡片标题丢了');
  assert.ok(!m.panel.innerHTML.includes('未命名主题'), '右栏又画成「未命名主题」了');
  assert.ok(m.stage.innerHTML.includes('RAG 的 chunking 策略'), '中间列标题丢了');
});

// ---- 生成流的 `#gen-body`:第六轮起它装的是**一次性到达的大纲** ---------------

/** 把若干帧编成 SSE 字节流(与 `sse.test.mjs` 同一个造法)。 */
const sseBytes = (frames) =>
  new ReadableStream({
    start(c) {
      for (const f of frames) c.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(f)}\n\n`));
      c.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));
      c.close();
    },
  });

test('generate:图谱模式的日志与右栏各自收到该收到的东西(走查反馈 ⑥)', async () => {
  // 图谱模式下 `#gen-body` 里**只有一帧** token(整份大纲),它不再逐字到达 ——
  // 也就是说「这一帧丢了」不会被任何东西兜住:内联累加器漏掉它,右栏就是空的,
  // 而页面看起来只是「生成完了但没有正文」。所以这条钉的是那一帧真的落了地。
  // 同时钉住 `nodes` 帧:它在左栏该出现一行「已抽取出 N 个知识点」。
  const m = await mounted({
    routes: {
      '/api/learning/topics/t1/generate': {
        body: sseBytes([
          { type: 'status', message: '正在从资料中抽取知识点…' },
          { type: 'nodes', count: 3, summary: '一句话结论' },
          { type: 'token', content: '## 1. 文本切分\n\n切块\n\n关键词：定长、递归\n' },
          { type: 'graph_ready', topic_id: 't1', nodes: 3, edges: 2, degraded: false },
        ]),
      },
      '/api/learning/topics/t1': { json: F.star(3) },
      '/api/learning/topics': { json: { topics: [] } },
    },
  });
  m.KP.S.topicId = 't1';
  m.KP.S.topics = [{ id: 't1', title: 'T', status: 'ready', progress: { total: 3, percent: 0 } }];

  await m.KP.views.graph.generate();

  const body = m.KP.dom.q(m.panel, 'gen-body');
  assert.ok(body, '#gen-body 不在右栏里');
  assert.ok(body.textContent.includes('## 1. 文本切分'), '大纲那一帧没落进 #gen-body');
  assert.ok(body.textContent.includes('关键词：定长、递归'));

  const log = m.KP.dom.q(m.panel, 'gen-log').children.map((c) => c.textContent).join('\n');
  assert.ok(log.includes('3 个知识点'), `nodes 帧没有变成一行日志:${log}`);
  assert.ok(!log.includes('降级'), '没有降级却报了降级');
});

// ---- 双布局:视图层必须对两种布局一视同仁 -------------------------------------

/** 星形图(1 根 n 叶)—— `choose()` 会选径向。 */
const starG = (n) => F.star(n);
/** 链式图 —— `choose()` 会选横向。 */
const chainG = (n) => F.chain(n);

test('svg:层标注的文案在视图层拼,几何来自布局的 marks', () => {
  // 横向布局有「第 N 层」;径向**没有** —— 环标注没有稳的落点,层信息交给图例那句话。
  // 这条钉住的不只是文案,还有分工:布局给坐标,视图给字。
  const g = chainG(3);
  const L = bare.KP.layout.layout(g.nodes, g.edges, 'layered');
  const html = graphView.svg(g, { layout: L });
  assert.equal(L.marks.length, 3);
  for (const mk of L.marks) {
    assert.ok(html.includes(`x="${mk.x}" y="${mk.y}"`), `第 ${mk.level + 1} 层的锚点没进产物`);
  }
  assert.ok(html.includes('>第 1 层</text>'), '第 1 层的文案丢了');
  assert.ok(html.includes('>第 3 层</text>'));

  const R = bare.KP.layout.layout(g.nodes, g.edges, 'radial');
  assert.equal(R.marks.length, 0);
  assert.ok(!graphView.svg(g, { layout: R }).includes('层</text>'), '径向不该出现层标注');
});

test('svg:两条布局产出的边都带箭头,反向边多一个 back class', () => {
  // `back` 是破环强制放行的产物。它的几何(二次曲线)已经在布局里,这里只确认
  // 那个 class 真的落到了 DOM 上 —— 样式表靠它做视觉弱化。
  // 匹配用「class 里含 back 这个词」而不是整串相等:反向边同时被选中时会是
  // `class="edge hot back"`,写死整串会让那条路径悄悄漏测。
  const backOf = (html) => edgesOf(html).filter((e) => /class="edge[^"]*\bback\b/.test(e));

  const g = F.cycle();
  for (const mode of ['layered', 'radial']) {
    const L = bare.KP.layout.layout(g.nodes, g.edges, mode);
    for (const opts of [{ layout: L }, { layout: L, selected: 'a' }]) {
      const edges = edgesOf(graphView.svg(g, opts));
      assert.equal(edges.length, 2, mode);
      for (const e of edges) assert.ok(e.includes('marker-end="url(#arrow)"'), `${mode}:${e}`);
      assert.equal(backOf(graphView.svg(g, opts)).length, 1, `${mode} 里反向边没有被标出来`);
    }
  }
  // hot 与 back 是正交的两个标记,同时出现时都要在(选中 'a' 时 a→b 那条既是反向边
  // 又是相连边)。
  const both = backOf(graphView.svg(g, { layout: bare.KP.layout.layout(g.nodes, g.edges, 'radial'),
                                        selected: 'a' }));
  assert.equal(both.length, 1);
  assert.ok(/\bhot\b/.test(both[0]), '同时相连的反向边丢了 hot');

  // 正常图里一条 back 都不该有(否则上面那条会因为「到处都是 back」而失去意义)。
  assert.equal(backOf(graphView.svg(chainG(3), {})).length, 0);
});

test('svg:径向用直线辐条,横向用横向三次曲线 —— 两种形状都不含非法坐标', () => {
  const star = starG(6);
  const radialHTML = graphView.svg(star, { layout: bare.KP.layout.layout(star.nodes, star.edges, 'radial') });
  // 星形图的每条边都从根射向一个叶子,边界求交后是直线。
  assert.ok(/ d="M[-\d.]+,[-\d.]+ L[-\d.]+,[-\d.]+"/.test(radialHTML), '径向的边不是直线辐条');

  const chain = chainG(4);
  const layeredHTML = graphView.svg(chain, { layout: bare.KP.layout.layout(chain.nodes, chain.edges, 'layered') });
  assert.ok(/ d="M[-\d.]+,[-\d.]+ C/.test(layeredHTML), '横向的边不是三次曲线');
  for (const html of [radialHTML, layeredHTML]) {
    assert.ok(!/NaN|undefined/.test(html), '产物里出现了非法坐标');
  }
});

test('header():图例那句方向随布局变 —— 径向是由内向外,写反了会让人找错起点', () => {
  const g = starG(4);
  const radial = graphView.header(g, { layout: bare.KP.layout.layout(g.nodes, g.edges, 'radial') });
  const layered = graphView.header(g, { layout: bare.KP.layout.layout(g.nodes, g.edges, 'layered') });
  assert.ok(radial.includes('由内向外'), '径向没有说明方向');
  assert.ok(!radial.includes('从左往右'), '径向沿用了横向的措辞');
  assert.ok(layered.includes('从左往右'));
});

test('header():布局切换按钮高亮的是**用户选的**,不是自动选中的那个', () => {
  // 自动选了径向却点亮「放射」按钮,会让用户以为是自己选的,再点一下「回到自动」
  // 的语义也就没了。所以高亮只看 `opts.mode`。
  const g = starG(4);
  assert.equal(bare.KP.layout.choose(g.nodes), 'radial', '这张图默认就该是径向');

  const auto = graphView.header(g, {});
  assert.ok(auto.includes('data-mode="layered"') && auto.includes('data-mode="radial"'),
    '切换按钮没渲染出来');
  assert.ok(!auto.includes('class="btn sm seg on"'), '自动选择被显示成了用户的选择');

  const manual = graphView.header(g, { mode: 'radial' });
  const on = manual.match(/class="btn sm seg on"[^>]*data-mode="(\w+)"/);
  assert.ok(on, '手动选过之后没有任何按钮被点亮');
  assert.equal(on[1], 'radial');
});

test('view():两种模式下顶栏 / 筛选条 / 画布都在', () => {
  const g = starG(5);
  for (const mode of ['layered', 'radial']) {
    const html = graphView.view(g, { mode, layout: bare.KP.layout.layout(g.nodes, g.edges, mode) });
    for (const frag of ['class="topbar"', 'class="filters"', 'id="graph-scroll"',
                        'id="graph-fit"', 'class="graph"', 'data-mode="radial"']) {
      assert.ok(html.includes(frag), `${mode} 缺少 ${frag}`);
    }
  }
});

test('DOM 桩:重新赋值 innerHTML 换掉子节点,而不是往后追加', () => {
  // 这条是 Stage 3 撞出来的真 harness bug:`innerHTML` 的 setter 原本没有清空
  // `children`,而 `parseRough` 是追加语义 —— 于是同一个容器渲染两遍会同时留着
  // 两代节点,`querySelector('#id')` 命中的是**上一代**那个,事件绑在已脱离文档的
  // 元素上。它的可怕之处不在「测试红了」,而在「测试绿着,但断言的是旧 DOM」——
  // 那会让一整套断言悄悄失去意义。所以给桩本身补一条。
  const el = new FakeElement('div');
  el.innerHTML = '<button data-mode="a"></button>';
  assert.equal(el.children.length, 1);
  assert.equal(bare.KP.dom.qa(el, '[data-mode]').length, 1);

  el.innerHTML = '<button data-mode="a"></button><button data-mode="b"></button>';
  assert.equal(el.children.length, 2, '旧子节点没被清掉 —— 查询会命中上一代的元素');
  assert.equal(bare.KP.dom.qa(el, '[data-mode]').length, 2);
  assert.equal(bare.KP.dom.qa(el, '[data-mode="b"]').length, 1);

  el.innerHTML = '';
  assert.equal(el.children.length, 0);
  assert.equal(bare.KP.dom.qa(el, '[data-mode]').length, 0);
});

// ---- 用 DOM 桩跑真实流程 -----------------------------------------------------

/** 一套完整的挂载点 + `KP.views.shell.init()`,让视图真的能写进 `#stage` / `#panel`。 */
async function mounted(opts = {}) {
  const elements = makeShellElements();
  const loaded = await loadKP({ elements, ...opts });
  loaded.KP.views.shell.init();
  return { ...loaded, elements, stage: elements.stage, panel: elements.panel };
}

test('loadTopics:503 → 进不可用态并在 stage 里说明原因(R11)', async () => {
  const m = await mounted({
    routes: { '/api/learning/topics': { ok: false, status: 503, json: { detail: '学习图谱未启用' } } },
  });
  await m.KP.views.shell.loadTopics();
  assert.equal(m.KP.S.disabled, true, '503 没有进不可用态');
  assert.ok(m.stage.innerHTML.includes('学习图谱未启用'), m.stage.innerHTML);
  assert.ok(m.stage.innerHTML.includes('LEARNING_ENABLED'), '没告诉用户怎么打开');
});

test('loadTopics:非 503 的错误应当抛出,不假装「不可用」', async () => {
  const m = await mounted({
    routes: { '/api/learning/topics': { ok: false, status: 500, json: { detail: '炸了' } } },
  });
  await assert.rejects(() => m.KP.views.shell.loadTopics(), /炸了/);
});

// ---- 走查反馈 ③:助手整块静默失效 ---------------------------------------------
//
// 用户报的是「点击开始学习没有任何反应」+「关闭后没有办法再次打开」。根因不在
// 助手身上,而在 `S.route`:**它此前只有 `route()` 会写**,而「侧栏点主题卡 /
// 新建主题 / 生成完落 hash」这几条路径走的是 `writeHash()` —— 里面用的是
// `history.replaceState`,**不触发 `hashchange`**。于是 hash 已经指着图谱了,
// `S.route` 还停在 `chat`,`views/assistant.js` 那句
// `if (S.route.view === 'chat') { innerHTML = ''; hidden = true; return; }`
// 就把每一个入口(开始学习 / 要点与提纲芯片 / 收起后那条栏)一起吞掉了。
//
// 这一组钉的就是「离开对话页之后助手必须还能打开」——此前 `onTopicsClick`
// **零覆盖**,而它一条路径就能让助手整体失效。

/** 侧栏里第 i 张主题卡(它带着 `data-topic`,事件委托认的就是它)。 */
const topicCard = (m, i = 0) => bare.KP.dom.qa(m.elements.topics, '.topic')[i];

test('侧栏点主题卡:`S.route` 跟着走,助手不会从此再也打不开(走查反馈 ③)', async () => {
  const m = await mounted({
    hash: '#/chat',
    routes: {
      '/api/learning/topics/t1': { json: F.star(3) },
      '/nodes/l0/messages': { json: { messages: [] } },
    },
  });
  // 起点是真实的那一个:停在「对话」页,助手按设计整块藏起来。
  await m.KP.views.shell.route();
  assert.equal(m.KP.S.route.view, 'chat');
  assert.equal(m.elements.assistant.hidden, true, '对话页上助手本来就该是藏着的');

  m.KP.S.topics = [F.topic({ id: 't1', title: '混合检索' })];
  m.KP.views.shell.renderSidebar();
  const card = topicCard(m);
  assert.ok(card, '侧栏没有主题卡 —— 这条用例就空了');
  m.elements.topics.fire('click', { target: card });
  await FLUSH();
  await FLUSH();

  // 用户现在在看图谱:`S.route` 必须已经跟着 hash 走了。
  assert.equal(m.KP.S.topicId, 't1');
  assert.equal(m.KP.S.route.view, 'graph', 'S.route 停在了 chat —— 助手接下来会整块静默失效');
  assert.equal(m.elements.assistant.hidden, false, '助手没跟着切回来');

  // 于是「开始学习」这条路能走通 —— 用户报的就是它「没有任何反应」。
  m.KP.views.assistant.openFor('l0');
  await FLUSH();
  assert.equal(m.KP.S.assistant.open, true, '点了「开始学习」但助手没有展开');
  assert.ok(bare.KP.dom.q(m.elements.assistant, 'a-input'), '输入框不在 —— 用户没地方提问');
});

test('writeHash 是 `S.route` 的写入点 —— 一次调用同时落 hash 与状态', async () => {
  // 这条是上面那条的**机制**层:写在这里而不是各个调用点,才不会漏掉下一个调用点
  // (上一版只在 `views/settings.js` 的「重新研究」里手工补过一次)。
  const m = await mounted({ hash: '#/chat' });
  await m.KP.views.shell.route();
  m.KP.S.topicId = 't1';
  m.KP.S.nodeId = 'l0';
  m.KP.views.shell.writeHash();
  assert.deepEqual(plain(m.KP.S.route), { view: 'graph', topicId: 't1', nodeId: 'l0' });
  // `dropNode` 是同一件事的另一半:hash 里丢掉节点时,状态也得跟着丢。
  m.KP.views.shell.writeHash(true);
  assert.deepEqual(plain(m.KP.S.route), { view: 'graph', topicId: 't1', nodeId: null });
});

test('新建主题:同样不经过 route(),助手也要跟着回到图谱形态', async () => {
  const created = F.topic({ id: 't9', title: '新主题' });
  // 键的顺序有意义:桩按 `url.includes(key)` 取**第一个**命中的,而
  // `/api/learning/topics/t9` 也包含 `/api/learning/topics` —— 长的那条必须先写。
  // 同一个 URL 上还叠着两种方法(POST = 新建,GET = 列表),按 `init.method` 分。
  const m = await mounted({
    hash: '#/chat',
    routes: {
      '/api/learning/topics/t9': { json: F.emptyGraph() },
      '/api/learning/topics': (url, init) => ((init || {}).method === 'POST'
        ? { json: created }
        : { json: { topics: [created] } }),
    },
  });
  await m.KP.views.shell.route();
  assert.equal(m.KP.S.route.view, 'chat');
  assert.equal(m.elements.assistant.hidden, true);

  m.elements['new-query'].value = '想学点什么';
  m.elements['new-topic'].fire('submit', { preventDefault() {} });
  await FLUSH();
  await FLUSH();
  await FLUSH();

  assert.equal(m.KP.S.topicId, 't9', '主题没建出来 —— 这条用例就空了');
  assert.equal(m.KP.S.route.view, 'graph', 'S.route 停在了 chat');
  assert.equal(m.elements.assistant.hidden, false, '助手没跟着切回来');
});

test('loadTopic:404 → 清掉选择**并清 hash**,否则刷新还是死链', async () => {
  const m = await mounted({
    hash: '#/g/gone',
    routes: { '/api/learning/topics/gone': { ok: false, status: 404, json: { detail: '没有这个主题' } } },
  });
  m.KP.S.topicId = 'gone';
  await m.KP.views.shell.loadTopic();
  assert.equal(m.KP.S.topicId, null, '主题不存在却没有清掉选择');
  assert.ok(m.stage.innerHTML.includes('还没有选择主题'), m.stage.innerHTML);
});

test('loadTopic:500 → 保留选择与 hash,给出可重试的错误页(不是空态)', async () => {
  const m = await mounted({
    hash: '#/g/flaky',
    routes: { '/api/learning/topics/flaky': { ok: false, status: 500, json: { detail: '数据库忙' } } },
  });
  m.KP.S.topicId = 'flaky';
  await m.KP.views.shell.loadTopic();
  assert.equal(m.KP.S.topicId, 'flaky', '一次网络抖动不该丢掉用户的深链接');
  assert.ok(m.stage.innerHTML.includes('主题加载失败'), m.stage.innerHTML);
  assert.ok(m.stage.innerHTML.includes('数据库忙'), '没把真实原因显示出来');
  assert.ok(m.stage.innerHTML.includes('id="reload-topic"'), '没有给重试入口');
});

test('loadTopic:成功且 ready → 渲染图谱;未就绪 → 渲染生成卡', async () => {
  const ready = F.star(3);
  const m = await mounted({
    hash: '#/g/t1',
    routes: { '/api/learning/topics/t1': { json: ready } },
  });
  m.KP.S.topicId = 't1';
  await m.KP.views.shell.loadTopic();
  assert.ok(m.stage.innerHTML.includes('class="graph"'), '就绪的主题没有画出图谱');

  const empty = F.emptyGraph();
  const m2 = await mounted({
    hash: '#/g/t2',
    routes: { '/api/learning/topics/t2': { json: empty } },
  });
  m2.KP.S.topicId = 't2';
  await m2.KP.views.shell.loadTopic();
  // 生成卡住**右栏**(走查反馈 ④:中间列没有滚动容器,长报告会撑破页面)。
  // 中间列只剩一句说明,断言那句说明在、卡片不在 —— 只断言「卡片在右栏」的话,
  // 两张都渲染也能过,而重复渲染正是这次搬家最容易留下的东西。
  assert.ok(m2.panel.innerHTML.includes('id="gen-go"'), '空主题没有在右栏渲染生成卡');
  assert.ok(!m2.stage.innerHTML.includes('id="gen-go"'), '生成卡还留在中间列 —— 会和右栏那张重复');
  assert.ok(m2.stage.innerHTML.includes('在右侧'), '中间列没告诉用户动作在右栏');
});

test('generate:流挂掉也要放开忙碌标志,按钮不能永远停在「生成中…」(R12/R15)', async () => {
  // 这条是那个 `finally` 存在的唯一理由。漏掉 `S.busyGenerate = false` 时
  // 页面完全正常、只是按钮再也点不动,而且不刷新不复原 —— 没有异常可抓。
  const m = await mounted({
    // 路由表按**最长优先**手排:桩用 `url.includes(key)` 匹配,把 `/api/learning/topics`
    // 放在前面会把 `/api/learning/topics/t1/generate` 也吃掉。
    routes: {
      '/api/learning/topics/t1/generate': { ok: false, status: 500, json: { detail: '上游挂了' } },
      '/api/learning/topics/t1': { json: F.emptyGraph() },
      '/api/learning/topics': { json: { topics: [] } },
    },
  });
  m.KP.S.topicId = 't1';
  m.KP.views.graph.renderGenerateCard({ title: 'X', status: 'empty', progress: { total: 0 } });

  await m.KP.views.graph.generate();

  assert.equal(m.KP.S.busyGenerate, false, '忙碌标志没放开 —— 按钮会永远停在「生成中…」');
  // 查找容器是**右栏**(卡片搬过去了,`generate()` 里的四处查找也跟着搬——
  // 只搬卡片不搬查找的话,按钮挂上去但点不动:找的是中间列里的 null)。
  const go = m.KP.dom.q(m.panel, 'gen-go');
  assert.ok(go, '生成按钮不见了');
  assert.equal(go.disabled, false, '按钮还是禁用的');
  assert.equal(go.textContent, '重试');
  // 日志行是 `appendChild` 加进去的**子节点**,不在 `innerHTML` 里(kind 与
  // `line()` 的实现一致),所以要读 children 的 textContent。
  const logEl = m.KP.dom.q(m.panel, 'gen-log');
  const logged = logEl.children.map((c) => c.textContent).join('\n');
  assert.ok(logged.includes('上游挂了'), `错误没有显示给用户,日志只有:${logged}`);
});

test('generate:成功生成后回到图谱,并丢掉旧的节点选择', async () => {
  const ready = F.star(3);
  const m = await mounted({
    routes: {
      '/api/learning/topics/t1/generate': { json: {} },
      '/api/learning/topics/t1': { json: ready },
      '/api/learning/topics': { json: { topics: [{ id: 't1', title: 'T', status: 'ready', progress: { total: 4, mastered: 0, recommended: 0, percent: 0 } }] } },
    },
  });
  m.KP.S.topicId = 't1';
  m.KP.S.nodeId = 'stale';
  await m.KP.views.graph.generate();
  assert.equal(m.KP.S.busyGenerate, false);
  assert.equal(m.KP.S.nodeId, null, '换了图谱还留着旧节点 id,会指向一个不存在的盒子');
  assert.ok(m.stage.innerHTML.includes('class="graph"'), '没有切到图谱');
});

test('generate:重复点击不会并发跑两次(R15)', async () => {
  const calls = [];
  const m = await mounted({
    routes: {
      '/api/learning/topics/t1/generate': () => { calls.push(1); return { json: {} }; },
      '/api/learning/topics/t1': { json: F.emptyGraph() },
      '/api/learning/topics': { json: { topics: [] } },
    },
  });
  m.KP.S.topicId = 't1';
  const first = m.KP.views.graph.generate();
  await m.KP.views.graph.generate();     // 第二次应当在入口就被 busyGenerate 挡掉
  await first;
  assert.equal(calls.length, 1, '并发跑了两次生成');
});

test('布局切换:点一下换布局并记住,再点一次回到自动', async () => {
  // 这是 Stage 3 的验收动作 —— 「人工对比两种模式」如果没有这个开关,就只能在
  // 控制台里改代码。所以它本身也得有回归保护:按钮点了没反应、或者回不到自动,
  // 都会让下一次对比悄悄变成「只能看一种」。
  const ready = chainG(5);   // 默认 laid out 成横向
  const m = await mounted({
    hash: '#/g/t1',
    routes: { '/api/learning/topics/t1': { json: ready } },
  });
  m.KP.S.topicId = 't1';
  await m.KP.views.shell.loadTopic();
  assert.ok(m.stage.innerHTML.includes('>第 1 层</text>'), '默认没走横向布局');

  const btn = (mode) => m.KP.dom.qa(m.stage, `[data-mode="${mode}"]`)[0];
  assert.ok(btn('radial'), '放射按钮不在 stage 里');

  btn('radial').fire('click');
  assert.equal(m.KP.S.view.mode, 'radial', '点了按钮但偏好没变');
  assert.ok(!m.stage.innerHTML.includes('层</text>'), '切到径向后还在画层标注');
  const on = m.stage.innerHTML.match(/class="btn sm seg on"[^>]*data-mode="(\w+)"/);
  assert.ok(on && on[1] === 'radial', '切换后按钮没有进入选中态');

  // 再点一次 → 回到「自动」。`choose()` 对这条链会选横向,所以层标注又回来了。
  btn('radial').fire('click');
  assert.equal(m.KP.S.view.mode, null, '再点一次没有回到自动');
  assert.ok(m.stage.innerHTML.includes('>第 1 层</text>'), '回到自动后没有按形状重排');
});

test('renderTopics:XSS 夹具里的主题标题被转义', async () => {
  const m = await mounted({ routes: {} });
  m.KP.S.topics = [{
    id: 'x', title: F.XSS, summary: F.XSS, status: 'ready',
    progress: { total: 1, mastered: 0, recommended: 0, percent: 0 },
  }];
  m.KP.views.shell.renderTopics();
  assert.ok(!m.elements.topics.innerHTML.includes('<img'), m.elements.topics.innerHTML);
});

// ---- Stage 4:三栏外壳(导航 / 我的探索 / 进度卡) ------------------------------

const shell = bare.KP.views.shell;

test('navHTML:只高亮当前视图,其余不亮', () => {
  const on = (html) => html.match(/class="navitem on"[^>]*>[\s\S]*?<\/a>/g) || [];
  // 第 6 阶段加进「对话」、第 7 阶段加进「设置」之后,nav 终于不止一项了 —— 也就是
  // 这条断言真正开始干活的时候:两项同时高亮、或者切过去一项都不亮,页面看起来都
  // 完全正常。`explore` 仍然是 0:它没有视图,也就没有入口。
  for (const [route, n] of [[{ view: 'graph' }, 1], [{ view: 'chat' }, 1],
                            [{ view: 'settings' }, 1], [{ view: 'explore' }, 0], [null, 1]]) {
    assert.equal(on(shell.navHTML(route, null)).length, n, `${JSON.stringify(route)} 的高亮数不对`);
  }
  for (const v of ['graph', 'chat', 'settings']) {
    assert.ok(shell.navHTML({ view: v }, null).includes('aria-current="page"'), `${v} 这一项没有 aria-current`);
  }
});

test('navHTML:切到「对话」时知识图谱那一项不许跟着亮', () => {
  // 上面那条只数了高亮的**个数**;这条钉住是**哪一项**亮。个数对而项错是可能的:
  // `on` 的条件如果写成 `it.view === cur || it.view === 'graph'`,个数仍然是 1。
  const chat = shell.navHTML({ view: 'chat' }, 't1');
  const onLabel = chat.match(/class="navitem on"[^>]*>[\s\S]*?<\/a>/)[0];
  assert.ok(onLabel.includes('对话'), onLabel);
  // 而「知识图谱」仍要指向当前主题 —— 从对话页点回去应当回到刚才那张图,
  // 不是首页。清掉 topicId 的话用户丢的正是「我刚才在哪儿」。
  assert.ok(chat.includes('href="#/g/t1"'), chat);
});

test('navHTML:知识图谱指向当前主题,没有主题就指向首页', () => {
  assert.ok(shell.navHTML({ view: 'graph' }, null).includes('href="#/"'));
  assert.ok(shell.navHTML({ view: 'graph' }, 't1').includes('href="#/g/t1"'));
  // 带 `/` 的主题 id 必须编码 —— 不编码时 `#/g/a/b` 会被 parseHash 读成
  // 「主题 a、段名 b」,点一下导航就跳到别的主题上。
  const h = shell.navHTML({ view: 'graph' }, 'a/b');
  assert.ok(h.includes('href="#/g/a%2Fb"'), h);
});

test('navHTML:每一项的 view 都是路由认得的 —— 别放点下去没反应的入口', () => {
  // 加导航项最容易犯的错,是写一个 `parseHash` 不认得的 view:它不会报错,
  // 只会让 `route()` 落回空态。用户点下去看到一片空白,还以为自己点错了。
  const html = shell.navHTML({ view: 'graph' }, null);
  const views = [...html.matchAll(/href="#\/([\w-]*)/g)].map((m) => m[1]);
  assert.ok(views.length >= 1, '导航一项都没有');
  for (const v of views) {
    const r = bare.KP.route.parseHash('#/' + v);
    assert.equal(r.view, v || 'graph', `导航里的 #/${v} 路由不认得`);
  }
});

test('progressHTML:没有主题就整张卡不渲染', () => {
  // 显示一张 0% 的空卡会被读成「这个主题一个知识点都没学」。
  assert.equal(shell.progressHTML(null), '');
  assert.equal(shell.progressHTML(undefined), '');
});

test('progressHTML:没生成出节点时不说「已学习 0 / 0」', () => {
  const html = shell.progressHTML({ status: 'generating', progress: { total: 0, mastered: 0, percent: 0 } });
  assert.ok(html.includes('生成中'), html);
  assert.ok(!html.includes('0 / 0'), '把「还没生成」显示成了「一个都没学」');
  assert.ok(!html.includes('pc-row'), '没有节点却画了一条 0% 的进度条');
});

test('progressHTML:百分比只读后端值,且钳在 0~100', () => {
  const html = shell.progressHTML({
    status: 'ready', progress: { total: 8, mastered: 1, percent: 12 },
  });
  assert.ok(html.includes('>12%<'), html);
  assert.ok(html.includes('1 / 8'), '分母或分子显示错了');
  // 后端数据脏(percent 越界)时不该让进度条溢出容器。
  for (const p of [{ percent: 140 }, { percent: -20 }, { percent: 'x' }]) {
    const h = shell.progressHTML({ status: 'ready', progress: { total: 3, mastered: 1, ...p } });
    assert.ok(!/width:(1[0-9][1-9]|-)/.test(h), `percent=${p.percent} 没有被钳住:${h}`);
  }
});

test('renderSidebar:503 不可用时侧栏三段一起清空,不留半截', async () => {
  // 分开渲染就会出现「主题列表空了、但进度卡还挂着上一个主题的 42%」这种半更新。
  const m = await mounted({
    routes: { '/api/learning/topics': { ok: false, status: 503, json: { detail: '学习图谱未启用' } } },
  });
  // 走真实路径:boot() 也是先 loadTopics()(它的 503 分支置 `S.disabled`)再渲染。
  // 直接调 renderSidebar() 是在测一个页面上不存在的顺序。
  await m.KP.views.shell.loadTopics();
  assert.equal(m.KP.S.disabled, true, '前置条件没成立');
  assert.equal(m.elements.nav.innerHTML, '', '禁用时导航还在');
  assert.equal(m.elements.progress.innerHTML, '', '禁用时进度卡还在');
  assert.equal(m.elements.topics.innerHTML, '', '禁用时主题列表还在');
});

test('renderSidebar:进度卡跟着选中的主题走', async () => {
  const m = await mounted({ routes: {} });
  m.KP.S.topics = [
    { id: 'a', title: 'A', status: 'ready', progress: { total: 4, mastered: 1, percent: 25 } },
    { id: 'b', title: 'B', status: 'ready', progress: { total: 10, mastered: 9, percent: 90 } },
  ];
  m.KP.S.topicId = 'b';
  m.KP.views.shell.renderSidebar();
  assert.ok(m.elements.progress.innerHTML.includes('90%'), m.elements.progress.innerHTML);
  assert.ok(m.elements.progress.innerHTML.includes('9 / 10'));

  m.KP.S.topicId = null;
  m.KP.views.shell.renderSidebar();
  assert.equal(m.elements.progress.innerHTML, '', '取消选中后进度卡还在,用户会以为它是全局的');
});

test('topic 条目:空主题不画进度条,改为显示主题状态', async () => {
  const m = await mounted({ routes: {} });
  m.KP.S.topics = [
    { id: 'a', title: 'A', status: 'generating', progress: { total: 0, mastered: 0, percent: 0 } },
  ];
  m.KP.views.shell.renderTopics();
  const html = m.elements.topics.innerHTML;
  assert.ok(html.includes('生成中'), html);
  assert.ok(!html.includes('已掌握 0/0'), '把「还没生成」写成了「已掌握 0/0」');
  assert.ok(!html.includes('class="bar"'), '空主题画了一条 0% 的进度条');
});

test('t-meta:只显示日期部分,不做时区换算', async () => {
  // 后端给的是带时区的本地 ISO。`new Date()` 解析再格式化会把
  // 「2026-09-10T00:30:00+08:00」在 UTC 机器上显示成 09-09 —— 差一天。
  const m = await mounted({ routes: {} });
  m.KP.S.topics = [{
    id: 'a', title: 'A', status: 'ready', created_at: '2026-09-10T00:30:00+08:00',
    progress: { total: 2, mastered: 0, percent: 0 },
  }];
  m.KP.views.shell.renderTopics();
  assert.ok(m.elements.topics.innerHTML.includes('2026-09-10'), m.elements.topics.innerHTML);
  assert.ok(!m.elements.topics.innerHTML.includes('2026-09-09'), '被按 UTC 换算了');
});

test('右栏常驻:关掉节点详情后是占位提示,不是一片空白,也不是隐藏', async () => {
  // 三栏里右栏一直在(见 css/layout.css)。上一版是 `el.hidden = true` + 摘掉
  // `.with-panel`,于是「没选中节点」在页面上表现为一条空白窄条,读起来像加载失败。
  const m = await mounted({ routes: {} });
  assert.ok(m.KP.views.inspector.renderPlaceholder().includes('点击图谱中的任意节点'));

  m.KP.S.nodeId = 'n1';
  m.KP.S.node = { id: 'n1', name: 'N1', status: 'unlearned' };
  m.KP.views.inspector.close();
  const html = m.panel.innerHTML;
  assert.ok(html.includes('p-empty'), '关闭后没有占位提示:' + html);
  assert.equal(m.panel.hidden, false, '右栏被藏起来了 —— 它该常驻');
  assert.equal(m.KP.S.nodeId, null, '关闭后没清掉节点选择');
});

// ---- 阶段 5:四态视觉 / 筛选 / 工具栏 / 局部 patch ------------------------------

test('svg:四态各自的角标字形与尾注都进了产物(文字通道不靠颜色)', () => {
  const g = F.fourStates();
  const html = graphView.svg(g, {});
  // 字形逐个对，而不是写死四个 Unicode 字面量 —— 后者测的是「字形正好是 ◉」，
  // 而真正的契约是「每个状态的字形都真的画出来了」。
  for (const [key, st] of Object.entries(bare.KP.STATE)) {
    assert.ok(html.includes(`>${st.glyph}</text>`), `${key} 的字形 ${st.glyph} 没进产物`);
  }
  // 尾注:四态四句,「学习中」那句带**真实轮数**。
  for (const frag of ['>未学</text>', '>已对话 3 轮</text>', '>待确认</text>', '>已掌握</text>']) {
    assert.ok(html.includes(frag), `尾注 ${frag} 没进产物`);
  }
});

test("svg:节点 <g> 带 st-* 状态类 —— CSS 的 Glow 靠它命中", () => {
  const g = F.fourStates();
  const html = graphView.svg(g, {});
  for (const cls of ['st-unlearned', 'st-learning', 'st-recommended', 'st-mastered']) {
    assert.ok(html.includes(`class="node ${cls}"`), `缺少 ${cls}:` + html.slice(0, 200));
  }
  // 选中那个要**同时**有状态类和 sel。
  const sel = graphView.svg(g, { selected: 'm' });
  assert.ok(sel.includes('class="node st-mastered sel"'), '选中的已掌握节点少了类');
});

test('svg:visible 是「不画」而不是「不摆」—— 位置仍由全量布局给', () => {
  const g = F.fourStates();
  const L = bare.KP.layout.layout(g.nodes, g.edges, 'layered');
  const vis = bare.KP.filterNodes(g.nodes, g.edges, { status: 'unlearned' });
  const html = graphView.svg(g, { layout: L, visible: vis.ids });
  // 只画筛出来的那一个盒子,但它的坐标必须和全量布局里那个盒子逐字一致。
  assert.equal(boxesOf(html).length, 1, '筛选没有生效');
  const box = L.pos.get('u');
  assert.ok(html.includes(`x="${box.x}" y="${box.y}"`), '被筛出来的节点位置不是全量布局给的');
  assert.ok(!html.includes('data-id="m"'), '被筛掉的节点还在画');
  // 边:两端都可见才画。这里四条边全部一端不可见 → 一条都不留。
  assert.equal(edgesOf(html).length, 0);
  assert.equal(boxesOf(graphView.svg(g, { layout: L })).length, g.nodes.length, '不传 visible 应当全画');
});

test('filtersHTML:状态芯片四个 + 全部,当前项高亮且 aria-pressed 正确', () => {
  // 用 star 而不是 fourStates:后者四个节点全是「概念」,类型行会被整个省掉
  // (见下一条),那样这条就测不到「状态 + 类型各一个 aria-pressed」。
  const g = F.star(4);
  const html = graphView.filtersHTML(g, { status: 'learning', type: 'all' }, 'layered');
  assert.equal((html.match(/data-fstatus=/g) || []).length, 5, '状态芯片数量不对(四个状态 + 全部)');
  const on = html.match(/class="chipbtn on" data-fstatus="(\w+)"/);
  assert.ok(on && on[1] === 'learning', '当前选中的状态芯片没有高亮');
  assert.equal((html.match(/aria-pressed="true"/g) || []).length, 2,
    '选中的状态 + 类型各一个,aria-pressed=true 应当恰好两个');
  // 色块:图例与筛选合成了一行,色块必须还在 —— 它是「哪个颜色是哪个状态」的唯一说明。
  for (const k of ['unlearned', 'learning', 'recommended', 'mastered']) {
    assert.ok(html.includes(`class="swatch ${k}"`), `缺少 ${k} 的色块`);
  }
});

test('filtersHTML:类型只有一个 facet 时整行不出现', () => {
  // 只有一个 facet 时「类型:全部 / 章节」是纯噪音 —— 点了「全部」和点「章节」
  // 看到的是同一张图。链条夹具正好只有「章节」一种,**四态夹具四个都是「概念」**,
  // 两者都不该有类型行。
  for (const name of ['chain', 'fourStates']) {
    const one = graphView.filtersHTML(F[name](), { status: 'all', type: 'all' }, 'layered');
    assert.ok(!one.includes('data-ftype'), `${name} 只有一个类型却渲染了类型行`);
    assert.ok(one.includes('data-fstatus="all"'), `${name} 的状态行不该跟着消失`);
  }
  // 多种类型时才出现(star:概念 / 技术 / 方法)。
  const many = graphView.filtersHTML(F.star(4), { status: 'all', type: 'all' }, 'layered');
  assert.ok(many.includes('data-ftype="all"'), '多类型时类型行没出现');
  assert.ok(many.includes('data-ftype="概念"'));
  assert.ok(many.includes('data-ftype="技术"'));
});

test('filtersHTML:方向说明跟着布局走 —— 径向是由内向外,不是从左往右', () => {
  const g = F.star(4);
  assert.ok(graphView.filtersHTML(g, {}, 'layered').includes('从左往右'));
  assert.ok(graphView.filtersHTML(g, {}, 'radial').includes('由内向外'));
  assert.ok(!graphView.filtersHTML(g, {}, 'radial').includes('从左往右'),
    '径向却写着「从左往右」—— 第一次看这张图的用户会找错起点');
});

test('toolbarHTML:四个按钮齐全,且都在画布容器里(不跟着内容滚)', () => {
  const html = graphView.toolbarHTML();
  for (const id of ['zoom-out', 'zoom-in', 'zoom-reset', 'zoom-fit']) {
    assert.ok(html.includes(`id="${id}"`), `缺少 ${id}`);
  }
  assert.ok(html.includes('100%'), '缺「实际大小」的标识');
  // 工具栏必须是 `#graph-scroll` 的**兄弟**而不是子节点 —— 放进去它会跟着
  // 内容一起滚走(图放大后比容器大,工具栏就滚出屏幕了)。
  const v = graphView.view(F.star(4), {});
  const toolbarAt = v.indexOf('class="toolbar"');
  assert.ok(v.indexOf('class="canvas"') < toolbarAt, '工具栏不在画布容器里');
  assert.ok(v.lastIndexOf('id="graph-fit"') < toolbarAt, '工具栏被放进了滚动容器内部');
});

test('patchNodeEl:只换那一个盒子,不动布局也不动别的节点', async () => {
  const m = await mounted({ routes: {} });
  m.KP.S.graph = F.fourStates();
  m.KP.views.graph.renderGraph();
  const before = m.stage.innerHTML;
  assert.ok(before.includes('data-id="u"'), '前置条件:图渲染出来了');

  // 点亮「未学」那个节点:本地状态先改(与 setMastery 的路径一致)。
  const n = m.KP.S.graph.nodes.find((x) => x.id === 'u');
  n.status = 'mastered';
  assert.equal(m.KP.views.graph.patchNodeEl(n), true, 'patch 报告失败');

  const after = m.stage.innerHTML;
  assert.ok(after.includes('class="node st-mastered"'), '状态类没有换成 st-mastered');
  assert.ok(after.includes('>已掌握</text>'), '尾注没有换');
  assert.ok(after.includes('✦'), '角标没有换');
  // **别的节点原样不动** —— 这才是「局部 patch」的意义,也是动画相位能保住的原因。
  assert.ok(after.includes('class="node st-learning"'), 'patch 把别的节点也改了');
  assert.ok(after.includes('data-id="s"') && after.includes('data-id="r"'));
});

test('patchNodeEl:节点被筛掉时如实返回 false,交给调用方全量重绘', async () => {
  // 返回 false 而不是「安静地什么都不做」:后者在页面上表现为「点了但图上没变」,
  // 用户不知道要刷新。
  const m = await mounted({ routes: {} });
  m.KP.S.graph = F.fourStates();
  m.KP.views.graph.renderGraph();
  assert.equal(m.KP.views.graph.patchNodeEl({ id: '不存在的节点' }), false);
  assert.equal(m.KP.views.graph.patchNodeEl(null), false);
});

/** 装一张图,并把 `inspector.open` 换成记录调用的探针。 */
async function graphWithOpenSpy() {
  const m = await mounted({ routes: {} });
  m.KP.S.graph = F.fourStates();
  m.KP.views.graph.renderGraph();
  const opened = [];
  m.KP.views.inspector.open = (id) => { opened.push(id); };
  const scroller = bare.KP.dom.q(m.stage, 'graph-scroll');
  const g = bare.KP.dom.qa(m.stage, '[data-id]')[0];
  assert.ok(scroller && g, '前置条件:图画出来了、节点盒子也在');
  return { m, opened, scroller, g };
}

test('点节点:**指针捕获把 click 重定向到容器**时依然要选中', async () => {
  // 这条用例存在的唯一理由是 `graph/canvas.js` 在 pointerdown 里调了
  // `setPointerCapture`。捕获生效后,后续的 `pointerup` / `click` 会被**重定向到
  // 捕获元素** —— 也就是说 `e.target` 是 `#graph-scroll`,不是那个 `<g data-id>`。
  //
  // 早先的实现在 `click` 里读 `e.target` 去找 `data-id`,于是它拿到的是容器、
  // `closestAttr` 返回 null、`inspector.open()` **一次都没被调用过**:右栏永远停在
  // 「点击图谱中的任意节点」。**不报错、不打日志**,而当时的测试又是直接对 `<g>` 派发
  // `click`(`evt.target` 由测试自己给),正好绕过了被重定向的那条路径 —— 于是全绿。
  //
  // 所以这里必须**如实模拟重定向**:`pointerdown` 的 target 是节点,
  // `pointerup` 的 target 换成容器 —— 后者在真实浏览器里就是捕获造成的那个值。
  const { opened, scroller, g } = await graphWithOpenSpy();
  scroller.fire('pointerdown', { button: 0, target: g, clientX: 40, clientY: 40 });
  scroller.fire('pointerup', { button: 0, target: scroller, clientX: 40, clientY: 40 });
  assert.deepEqual(opened, [g.getAttribute('data-id')],
    '捕获把 pointerup 的 target 换成了容器,选中就丢了 —— 命中判定必须在 pointerdown 时做');
});

test('点节点:没有捕获重定向时同样选中(两条路径都要活)', async () => {
  // 不依赖「捕获一定生效」这个前提:万一某个浏览器不重定向、或 `setPointerCapture`
  // 抛了(被 catch 掉),`pointerup` 的 target 就还是那个 `<g>`,那时也必须在。
  const { opened, scroller, g } = await graphWithOpenSpy();
  scroller.fire('pointerdown', { button: 0, target: g, clientX: 40, clientY: 40 });
  scroller.fire('pointerup', { button: 0, target: g, clientX: 40, clientY: 40 });
  assert.deepEqual(opened, [g.getAttribute('data-id')]);
});

test('拖动画布之后松手:不选中 —— 那一下是平移,不是点节点', async () => {
  const { opened, scroller, g } = await graphWithOpenSpy();
  scroller.fire('pointerdown', { button: 0, target: g, clientX: 40, clientY: 40 });
  scroller.fire('pointermove', { clientX: 140, clientY: 120 });   // 越过 4px 阈值
  scroller.fire('pointerup', { button: 0, target: scroller, clientX: 140, clientY: 120 });
  assert.deepEqual(opened, [], '拖完画布松手被当成了点击');

  // 下一次干净的点按要恢复正常 —— `moved` 由 pointerdown 归零,不该被上一次拖动卡住。
  scroller.fire('pointerdown', { button: 0, target: g, clientX: 40, clientY: 40 });
  scroller.fire('pointerup', { button: 0, target: scroller, clientX: 40, clientY: 40 });
  assert.deepEqual(opened, [g.getAttribute('data-id')], '上一次拖动把后续的点击永久污染了');
});

test('非主键按在节点上:不选中(右键留给浏览器)', async () => {
  const { opened, scroller, g } = await graphWithOpenSpy();
  scroller.fire('pointerdown', { button: 2, target: g, clientX: 40, clientY: 40 });
  scroller.fire('pointerup', { button: 2, target: g, clientX: 40, clientY: 40 });
  assert.deepEqual(opened, []);
});

test('点空白处:不选中,也不该报错', async () => {
  const { opened, scroller } = await graphWithOpenSpy();
  scroller.fire('pointerdown', { button: 0, target: scroller, clientX: 5, clientY: 5 });
  scroller.fire('pointerup', { button: 0, target: scroller, clientX: 5, clientY: 5 });
  assert.deepEqual(opened, []);
});

test('筛选芯片:点一下换筛选并重绘,而且**滚动位置不跳回原点**', async () => {
  // 筛选是「看着某一片、想把它摘出来看」的操作。重绘时把 scrollLeft 归零,
  // 等于把用户正在做的事毁掉 —— 而且他只会觉得「点了筛选,图乱了」。
  const m = await mounted({ routes: {} });
  m.KP.S.graph = F.fourStates();
  m.KP.views.graph.renderGraph();

  const scroller = bare.KP.dom.q(m.stage, 'graph-scroll');
  scroller.scrollLeft = 123;
  scroller.scrollTop = 45;

  const chip = bare.KP.dom.qa(m.stage, '[data-fstatus="learning"]')[0];
  assert.ok(chip, '找不到「学习中」的筛选芯片');
  chip.fire('click', {});

  assert.equal(m.KP.S.filters.status, 'learning', '筛选状态没写进 store');
  assert.equal(boxesOf(m.stage.innerHTML).length, 1, '筛选没有生效');
  const after = bare.KP.dom.q(m.stage, 'graph-scroll');
  assert.equal(after.scrollLeft, 123, '重绘后滚动位置被清零了');
  assert.equal(after.scrollTop, 45);
});

test('图层:换筛选不换布局 —— 剩下的盒子坐标逐字不变', async () => {
  const m = await mounted({ routes: {} });
  m.KP.S.graph = F.fourStates();
  m.KP.views.graph.renderGraph();
  const posOf = (html) => (html.match(/<rect class="box[^"]*" x="([^"]+)" y="([^"]+)"/g) || []);
  const before = posOf(m.stage.innerHTML);
  bare.KP.dom.qa(m.stage, '[data-fstatus="mastered"]')[0].fire('click', {});
  const after = posOf(m.stage.innerHTML);
  assert.ok(after.length < before.length, '筛选后盒子没减少,这条断言没有守卫意义');
  // 筛出来的那个「已掌握」盒子,坐标必须与筛选前**逐字相同**。
  assert.ok(before.some((b) => after.includes(b)), '筛选让节点挪位置了');
});

// ---- 阶段 6:右栏的学习状态 / 相关节点 / 来源 ---------------------------------
//
// 这三段都是**纯字符串函数**,所以能直接断言 —— 而它们各自的失败方式都是静默的:
// 状态写反了用户会照着错的方向学、来源显示了绝对路径会漏出服务器布局,
// 而这些在页面上「看起来完全正常」。

const insp = bare.KP.views.inspector;
const anyAction = { label: '开始学习', cls: 'primary' };

test('statusHTML:三种「还没有轮数」的情形各说各的话', () => {
  // 「已对话 0 轮」读起来像一句系统统计;「还没有开始对话」才是一句指路的话。
  const plainNode = F.node({ status: 'unlearned', chat_turns: 0 });
  assert.ok(insp.statusHTML(plainNode, anyAction).includes('还没有开始对话'));
  assert.ok(insp.statusHTML(plainNode, anyAction).includes('开始学习'));
  assert.ok(insp.statusHTML(F.node({ status: 'unlearned', chat_turns: 3 }), anyAction)
    .includes('已对话 <b>3</b> 轮'));
  // 已标记为掌握、但一轮都没聊过 —— 这时说「还没有开始对话」是错的。
  const mastered = F.node({ status: 'mastered', chat_turns: 0 });
  assert.ok(insp.statusHTML(mastered, anyAction).includes('已标记为掌握'));
});

test('statusHTML:有轮数时按钮变成「继续学习」,没有时是「开始学习」', () => {
  assert.ok(insp.statusHTML(F.node({ chat_turns: 2 }), anyAction).includes('继续学习'));
  assert.ok(!insp.statusHTML(F.node({ chat_turns: 2 }), anyAction).includes('>开始学习<'));
});

test('statusHTML:四态标签都带字形与文字 —— 颜色单打独斗时色盲看不见', () => {
  for (const [status, turns] of [['unlearned', 0], ['unlearned', 2], ['recommended', 1], ['mastered', 1]]) {
    const html = insp.statusHTML(F.node({ status, chat_turns: turns }), anyAction);
    const st = bare.KP.stateOf(F.node({ status, chat_turns: turns }));
    assert.ok(html.includes(st.glyph) && html.includes(st.label), `${status}/${turns} 少了文字通道`);
  }
});

test('statusHTML:脏数据(status 未知 / chat_turns 是字符串)不产生 undefined', () => {
  const html = insp.statusHTML({ id: 'x', status: '没见过的状态', chat_turns: null }, anyAction);
  assert.ok(!/undefined|NaN|\[object/.test(html), html);
  assert.ok(insp.statusHTML({ id: 'x', status: 'unlearned', chat_turns: 'x' }, anyAction)
    .includes('还没有开始对话'));
});

test('relatedHTML:列表为空时整段不渲染 —— 不留一个空标题', () => {
  assert.equal(insp.relatedHTML([]), '');
});

test('relatedHTML:箭头方向与 dir 对应,写反了用户会倒着学', () => {
  const rels = bare.KP.relatedNodes(F.chain(3), 'c1');   // out: c2,in: c0
  const html = insp.relatedHTML(rels);
  const btn = (id) => html.match(new RegExp(`data-rel="${id}"[\\s\\S]*?</button>`))[0];
  assert.ok(btn('c2').includes(bare.KP.icons.UI.arrow), '「后续」用了前置的箭头');
  assert.ok(btn('c0').includes(bare.KP.icons.UI.back), '「前置」用了后续的箭头');
  assert.notEqual(bare.KP.icons.UI.arrow, bare.KP.icons.UI.back);
});

test('relatedHTML:每一项都是可点的按钮,且带着目标节点 id', () => {
  const html = insp.relatedHTML(bare.KP.relatedNodes(F.star(3), 'root'));
  assert.equal((html.match(/data-rel=/g) || []).length, 3);
  assert.ok(html.includes('<button') && html.includes('type="button"'), '默认是 submit 的话会连带提交外层表单');
});

test('relatedHTML:节点名被转义(它来自 LLM 抽取)', () => {
  const html = insp.relatedHTML([{ node: { id: 'a"b', name: F.XSS }, dir: 'out' }]);
  assert.ok(!html.includes('<img'), html);
  assert.ok(!html.includes('data-rel="a"b"'), 'id 没转义 —— 属性会被提前闭合');
});

test('sourcesHTML:只显示文件名,绝不显示服务器上的绝对路径', () => {
  // 绝对路径会暴露部署布局,而它对用户没有任何用。这里用的是**真实形状**的
  // note_path(带盘符/多级目录),断言的是产物里连目录都不剩。
  const node = F.node({ note_path: 'D:/Code/Project/srv/data/knowledge/topic-1/03_向量检索.md' });
  const topic = F.topic({ report_path: 'data/knowledge/topic-1/report.md' });
  const html = insp.sourcesHTML(node, topic);
  assert.ok(html.includes('03_向量检索.md'), html);
  assert.ok(html.includes('report.md'));
  assert.ok(!html.includes('D:'), '绝对路径漏出来了:' + html);
  // 目录还在 = `baseName` 没生效。断言的是「那一段路径里的目录名一个都不剩」,
  // 而不是「产物里没有 /」—— 关闭标签 `<//div>` 里本来就有斜杠。
  assert.ok(!html.includes('knowledge/') && !html.includes('Project'), '目录还留在产物里:' + html);
  assert.ok(!html.includes('topic-1/'), html);
});

test('sourcesHTML:两份产物各自有说明;一份都没有时整段不渲染', () => {
  const both = insp.sourcesHTML(F.node({ note_path: 'a/正文.md' }), F.topic({ report_path: 'a/report.md' }));
  assert.ok(both.includes('图谱大纲') && both.includes('本知识点的正文'));
  assert.ok(!insp.sourcesHTML(F.node({ note_path: '' }), F.topic({ report_path: '' })).includes('sec-title'));
  assert.equal(insp.sourcesHTML(null, null), '');
  assert.equal(insp.sourcesHTML({ id: 'x' }, {}), '');
});

test('sourcesHTML:来源为空串(降级路径的常态)时不留一个空条目', () => {
  const html = insp.sourcesHTML(F.node({ note_path: '' }), F.topic({ report_path: 'a/report.md' }));
  assert.equal((html.match(/class="src"/g) || []).length, 1);
  assert.ok(!/undefined/.test(html));
});

test('渲染右栏:要点芯片带着整句话,而不是把内容塞进 id', async () => {
  // 「点一下即可就这点提问」靠的是 `data-ask` 里那句原文 —— 丢了它,
  // 芯片点下去助手会问一个空问题。
  const m = await mounted({ routes: {} });
  m.KP.S.graph = F.star(3);
  m.KP.S.node = F.node({ id: 'l0', name: '子领域1', key_points: ['要点甲'] });
  m.KP.S.nodeId = 'l0';
  m.KP.views.inspector.render();
  const pt = bare.KP.dom.qa(m.panel, '.pt')[0];
  assert.ok(pt, '要点芯片没渲染');
  assert.equal(pt.getAttribute('data-ask'), '要点甲');
});

test('渲染右栏:相关节点从 S.graph 的 edges 推,点一下切过去', async () => {
  let opened = null;
  const m = await mounted({ routes: {} });
  m.KP.S.graph = F.star(3);
  m.KP.S.node = F.node({ id: 'root', name: '根', key_points: [] });
  m.KP.S.nodeId = 'root';
  m.KP.views.inspector.render();
  const rels = bare.KP.dom.qa(m.panel, '.rel');
  assert.equal(rels.length, 3, '相关节点是空的 —— 多半是改去读节点上的 prerequisites 了(整图里没有那个字段)');
  // 点击处理器会调 `open(id)`,而 `open` 会发 GET;这里只断言它取到了正确的 id。
  opened = rels[0].getAttribute('data-rel');
  assert.ok(['l0', 'l1', 'l2'].includes(opened));
});

test('渲染右栏:一个输入框都没有(唯一的对话 UI 是浮动助手)', async () => {
  const m = await mounted({ routes: {} });
  m.KP.S.graph = F.star(3);
  m.KP.S.node = F.node({ id: 'root', name: '根', key_points: ['甲'] });
  m.KP.S.nodeId = 'root';
  m.KP.views.inspector.render();
  assert.equal(bare.KP.dom.qa(m.panel, 'input').length, 0, '右栏里又冒出输入框了');
  // 「开始学习」这个入口要在 —— 否则右栏就没有任何进入对话的路。
  assert.ok(bare.KP.dom.q(m.panel, 'p-learn'), '没有「开始学习」入口');
});

test('渲染右栏:每个字符串字段塞 payload,产物不得出现真实元素(R4)', async () => {
  const m = await mounted({ routes: {} });
  const g = F.xssGraph();
  m.KP.S.graph = g;
  m.KP.S.node = g.nodes[0];
  m.KP.S.nodeId = g.nodes[0].id;
  m.KP.views.inspector.render();
  assert.ok(!m.panel.innerHTML.includes('<img'), m.panel.innerHTML);
});

test('渲染右栏:病态夹具全域扫描,不留 undefined / NaN / [object Object]', async () => {
  const m = await mounted({ routes: {} });
  const g = F.pathological();
  m.KP.S.graph = g;
  for (const n of g.nodes) {
    m.KP.S.node = n;
    m.KP.S.nodeId = n.id;
    m.KP.views.inspector.render();
    assert.ok(!/undefined|NaN|\[object Object\]/.test(m.panel.innerHTML),
      `${n.id} 的产物里有脏字:` + m.panel.innerHTML.slice(0, 300));
  }
});

// ---- 走查反馈 ④:打开节点时按需生成提纲 ----------------------------------------
//
// 用户报的原话是「针对某个知识点进行查询并获得提纲和知识图谱后,最后的 markdown
// 记录文件中关于该知识点并没有列出提纲」—— 他要的不是把提纲写进文件（那是后端的
// 事,`test_api_learning.py` 管），而是**在右栏真能看见、并且能照着走**。
//
// 这一组盯的就是那两步:缺了才去要（已经有提纲的节点一次请求都不发）、要回来的
// 那一列是**可点的**（点一下就着这一点提问）、以及三种收场都不静默。
//
// **走查反馈 ② 之后,夹具里的条目换成了 8~14 字的小点**（「先弄懂为什么要分块」→
// 「有哪些分块方法」）。换的不是文案:条目现在是名词短语而不是一句话,所以断言里的
// 原文、`data-ask`、助手输入框的预填**三处必须同步** —— 它们本来就是同一条数据的
// 三个出口,分头改会得到「点下去问了一个空问题」而屏幕上看不出异常。

const FLUSH = () => new Promise((r) => setTimeout(r, 0));

/** 一个**已经有正文、但没有提纲**的节点 —— 打开它就该去要一份。 */
const bodyNoOutline = '# 子领域1\n\n## 要点\n- 甲\n\n## 我的笔记\n\n（自己写）\n';
const bodyWithOutline = '# 子领域1\n\n## 提纲\n\n1. 有哪些分块方法\n2. 各自的切分规则\n\n## 要点\n- 甲\n';

/**
 * 节点 GET 与提纲 POST 两条路由。
 *
 * 键用的是**不会互相包含**的两个片段（`/outline` 与 `/nodes/l0`）：桩是按
 * `url.includes(key)` 取的,而 `/nodes/l0` 正是 `/nodes/l0/outline` 的子串 ——
 * 用后者当键的话,先注册谁就决定了谁被命中,那是个只在改动顺序时才炸的坑。
 */
function outlineRoutes(node, post, seen = []) {
  return {
    '/outline': () => { seen.push('outline'); return post; },
    '/nodes/l0': () => ({ json: node }),
  };
}

test('outlineHTML:一列可点的小点,每一条都带着原文', () => {
  // `data-ask` 是那一点的原文 —— 丢了它,点下去助手会问一个空问题(与要点芯片同理)。
  const html = insp.outlineHTML(['有哪些分块方法', '各自的切分规则'], 'idle', '');
  assert.equal((html.match(/\.ol-item|ol-item/g) || []).length, 2);
  assert.ok(html.includes('data-ask="有哪些分块方法"'), html);
  assert.ok(html.includes('>1<') && html.includes('>2<'), '编号没画出来');
  assert.ok(html.includes('点一下即可就这点提问'), '没有说明它是可点的');
  // 数 `ol-item` 自己的 `type`,不数整个片段里的 ——这一支里还多了「重新生成」按钮,
  // 数总数的话下一个人加一个动作词就会弄红这条与模板无关的断言。
  assert.equal((html.match(/<button class="ol-item"[^>]*type="button"/g) || []).length, 2,
    '条目默认是 submit 的话会连带提交外层表单');
});

test('outlineHTML:条目被转义(它来自 LLM,不是可信文本)', () => {
  const html = insp.outlineHTML([F.XSS], 'idle', '');
  assert.ok(!html.includes('<img'), html);
});

test('outlineHTML:加载中与失败各说各的话,失败给一个可点的重试', () => {
  const loading = insp.outlineHTML([], 'loading', '');
  assert.ok(loading.includes('正在为这个知识点生成提纲'), loading);
  assert.ok(!loading.includes('p-ol-retry'), '还在生成就给重试按钮 —— 用户会点它');

  const failed = insp.outlineHTML([], 'error', '模型超时');
  assert.ok(failed.includes('模型超时'), failed);
  assert.ok(failed.includes('p-ol-retry'), '失败没有出口 —— 用户只能刷新');
  // 「还没去要」与「要失败了」必须长不一样:前者什么都不该画。
  assert.equal(insp.outlineHTML([], 'idle', ''), '');
  assert.equal(insp.outlineHTML([], 'idle', '上一轮的旧错误'), '');
});

test('打开节点:没有提纲才发 POST;回来之后是**可点的一列**', async () => {
  const seen = [];
  const node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: bodyNoOutline });
  const m = await mounted({
    routes: outlineRoutes(node, {
      json: { outline: ['有哪些分块方法', '各自的切分规则'], body: bodyWithOutline },
    }, seen),
  });
  await m.KP.views.inspector.open('l0');
  // 请求已经出去了,但**响应还没回来** —— 此刻屏幕上必须是「正在生成」,不是空白。
  // (一次模型调用要几秒,没有这句话用户只会以为这个知识点没有提纲。)
  assert.equal(m.KP.S.outline.state, 'loading', m.KP.S.outline.state);
  assert.ok(m.panel.innerHTML.includes('正在为这个知识点生成提纲'), m.panel.innerHTML);
  await FLUSH();
  await FLUSH();

  assert.deepEqual(seen, ['outline'], `发了 ${seen.length} 次提纲请求`);
  const rows = bare.KP.dom.qa(m.panel, '.ol-item');
  assert.equal(rows.length, 2, '提纲没长成可点的一列');
  // 渲染的是**后端带回来的正文里**那一段,不是响应里的 `outline` 数组 ——
  // 后端同时返回两者,而文件才是唯一事实来源(前端不复刻「插在哪一行」的规则)。
  // 两份夹具因此刻意写成一致的:这是有意的重复,不是巧合。
  assert.equal(rows[0].getAttribute('data-ask'), '有哪些分块方法');
  // 前端**不**自己复刻「`## 提纲` 插在哪一行」的规则 —— 后端把更新后的正文一起
  // 带回来(`notes.insert_outline` 是那份规则的唯一实现),这里直接换上那一条。
  assert.equal(m.KP.S.node.body, bodyWithOutline, '正文没换成后端带回来的那一份');
  assert.ok(!m.panel.innerHTML.includes('正在为这个知识点生成提纲'), '加载提示没撤掉');
});

test('打开节点:已经有提纲时**一次请求都不发**(零 LLM)', async () => {
  const seen = [];
  const node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: bodyWithOutline });
  const m = await mounted({ routes: outlineRoutes(node, { json: {} }, seen) });
  await m.KP.views.inspector.open('l0');
  await FLUSH();
  await FLUSH();
  assert.deepEqual(seen, [], '已经有提纲还去生成 —— 每次打开节点都白烧一次模型调用');
  assert.equal(bare.KP.dom.qa(m.panel, '.ol-item').length, 2, '文件里的提纲没渲染出来');
  // 提纲那一段**不能**在正文里再来一遍(一次可点、一次不可点的死文字)。
  // 走查反馈 ④ 之后节点页**不再渲染正文**（那个 `<details>` 换成了「讲解记录」页的
  // 入口），于是这条断言的形状也跟着换了：原来断的是「正文预览里没有提纲那一段」，
  // 现在断的是那条更硬的契约 —— 节点页上根本没有正文容器。正文只在那**一个**页面上
  // 出现（见下面「讲解记录页」那组用例）。
  assert.equal(bare.KP.dom.q(m.panel, 'note-md'), null, '节点页又渲染了一遍正文');
});

test('打开节点:生成失败时留下可点的重试,不静默', async () => {
  const seen = [];
  const node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: bodyNoOutline });
  const m = await mounted({
    routes: outlineRoutes(node, { ok: false, status: 500, json: { detail: '上游炸了' } }, seen),
  });
  await m.KP.views.inspector.open('l0');
  await FLUSH();
  await FLUSH();
  assert.ok(m.KP.S.outline.state === 'error', m.KP.S.outline.state);
  const retry = bare.KP.dom.q(m.panel, 'p-ol-retry');
  assert.ok(retry, '失败之后右栏什么也没说 —— 用户看到的是一个「没有提纲」的知识点');
  retry.fire('click');
  await FLUSH();
  await FLUSH();
  assert.equal(seen.length, 2, '重试按钮点了没反应');
});

test('打开节点:后端这次没生成出条目也算失败(它有意不写半成品)', async () => {
  const node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: bodyNoOutline });
  const m = await mounted({ routes: outlineRoutes(node, { json: { outline: [], body: bodyNoOutline } }) });
  await m.KP.views.inspector.open('l0');
  await FLUSH();
  await FLUSH();
  assert.ok(bare.KP.dom.q(m.panel, 'p-ol-retry'), '空结果被当成了成功 —— 用户找不到重试的路');
});

test('打开节点:提纲回来时用户已经切走了 → 整份丢弃', async () => {
  // 不丢弃的话,A 的提纲会挂到 B 的面板上,而 B 的正文其实没有那一段 ——
  // 点一条问助手,问的是别人的学习路径。
  const node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: bodyNoOutline });
  const m = await mounted({
    routes: outlineRoutes(node, { json: { outline: ['属于 A 的小点'], body: bodyWithOutline } }),
  });
  await m.KP.views.inspector.open('l0');
  m.KP.S.nodeId = 'other';                       // 用户在响应回来之前点了别的节点
  await FLUSH();
  await FLUSH();
  assert.equal(bare.KP.dom.qa(m.panel, '.ol-item').length, 0, 'A 的提纲挂到了 B 的面板上');
});

test('点一条提纲:就着这一点去问助手(与要点芯片同一条路)', async () => {
  const node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: bodyWithOutline });
  const m = await mounted({
    routes: {
      '/nodes/l0/messages': { json: { messages: [] } },
      '/nodes/l0': () => ({ json: node }),
    },
  });
  await m.KP.views.inspector.open('l0');
  const row = bare.KP.dom.qa(m.panel, '.ol-item')[1];
  assert.ok(row, '第二条提纲没渲染');
  row.fire('click');
  await FLUSH();
  assert.equal(m.KP.S.assistant.open, true, '点提纲没有把助手打开');
  assert.equal(bare.KP.dom.q(m.elements.assistant, 'a-input').value, '请讲讲：各自的切分规则');
});

// ---- 走查反馈 ②:「重新生成」 ------------------------------------------------
//
// 提纲的颗粒度改了(一句话 → 8~14 字的小点),而**已经写进文件的旧提纲是换不掉的**:
// 打开节点时那道「已有就回读」的闸门次次命中,不发请求。所以这里多了一个明确的
// 出口。三条不变量:它在;它带着 `force` 走;它在途时**旧的那一列还在屏幕上**,
// 并且明说「正在重新生成」——只画旧的那一列,用户会以为按钮没反应。

test('outlineHTML:有内容时带一个「重新生成」,在途时换成一行说明', () => {
  const items = ['有哪些分块方法', '各自的切分规则'];

  const idle = insp.outlineHTML(items, 'idle', '');
  assert.ok(idle.includes('p-ol-regen'), '有提纲却没有换一版的出口');
  assert.ok(!idle.includes('正在重新生成'), '还没开始就写着正在生成');

  // 在途:旧的一列留着(换的时候屏幕上不该空),但按钮必须消失 ——
  // 留着的话用户会再点一次,而那个动作每次都要花一次模型调用。
  const busy = insp.outlineHTML(items, 'loading', '');
  assert.ok(busy.includes('正在重新生成'), busy);
  assert.ok(!busy.includes('p-ol-regen'), '正在生成时还留着按钮');
  assert.equal((busy.match(/ol-item/g) || []).length, 2, '重新生成时把旧的一列藏起来了');

  // 换失败了:**两者都要说**——上一版还在,以及这次没成。只留下旧的那一列,
  // 用户读到的就是「点了按钮,什么都没发生」。
  const failed = insp.outlineHTML(items, 'error', '模型超时');
  assert.ok(failed.includes('模型超时'), failed);
  assert.ok(failed.includes('重新生成没成功'), '换失败没有任何痕迹');
  assert.equal((failed.match(/ol-item/g) || []).length, 2, '失败时把旧的一列吃掉了');
  assert.ok(failed.includes('p-ol-regen'), '失败之后没有重试的路');
});

test('点「重新生成」:带着 force 走,回来换成新的一列', async () => {
  const bodies = [];
  const newBody = '# 子领域1\n\n## 提纲\n\n1. 有哪些分块方法\n2. 各自的切分规则\n3. 会遇到什么问题\n\n## 要点\n- 甲\n';
  const node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: bodyWithOutline });
  const m = await mounted({
    routes: {
      '/outline': (url, init) => {
        bodies.push(JSON.parse((init && init.body) || '{}'));
        return { json: { outline: ['有哪些分块方法', '各自的切分规则', '会遇到什么问题'], body: newBody } };
      },
      '/nodes/l0': () => ({ json: node }),
    },
  });
  await m.KP.views.inspector.open('l0');
  await FLUSH();
  await FLUSH();
  // 已经有提纲 → 打开的那一下**一次请求都不发**(老闸门还在)
  assert.deepEqual(bodies, [], '打开一个有提纲的节点就去重新生成了');
  assert.equal(bare.KP.dom.qa(m.panel, '.ol-item').length, 2);

  bare.KP.dom.q(m.panel, 'p-ol-regen').fire('click');
  // **在这里立刻断言,不要先 await** ——假客户端是同步 resolve 的,`await` 一步之后
  // 整个来回就已经结束了,在途那一帧根本无从观察(那时面板上已经是新的一列)。
  assert.ok(m.panel.innerHTML.includes('正在重新生成'), '点了之后没有任何在途反馈');
  assert.equal(bare.KP.dom.qa(m.panel, '.ol-item').length, 2, '在途时把旧的一列藏起来了');
  await FLUSH();
  await FLUSH();
  assert.deepEqual(bodies, [{ force: true }], '重新生成没带 force —— 后端会照旧回读旧的那一版');
  assert.equal(m.KP.S.node.body, newBody, '正文没换成后端带回来的那一份');
  const rows = bare.KP.dom.qa(m.panel, '.ol-item');
  assert.equal(rows.length, 3, '屏幕上还是旧的那一列');
  assert.equal(rows[2].getAttribute('data-ask'), '会遇到什么问题');
});

// ---- 走查反馈 ④:右栏的「讲解记录」页 + 我的笔记可编辑 -----------------------
//
// 用户报的是「点 Markdown 正文之后没有下文」:那一块原来是个 `<details>`,summary
// 上写着「可编辑 · 已落盘」而里面是只读的 Markdown —— **那句话是假的**。
// 现在它是一个真的页面(大标题「讲解记录」+ 返回 + 可编辑的「我的笔记」)。
//
// 这一组的三条不变量**都是「错了也不报错」的那种**:草稿被静默丢掉、保存失败看起来
// 像保存成功、返回之后回不到节点页。所以每条都要有守卫。

const NOTE_BODY = [
  '---',
  'node_id: l0',
  '---',
  '',
  '# 子领域1',
  '',
  '## 提纲',
  '',
  '1. 有哪些分块方法',
  '',
  '## 讲解记录',
  '',
  '### 请讲讲切分粒度（2026-09-13 10:00）',
  '',
  '把长文档切成可检索的块。',
  '',
  '## 我的笔记',
  '',
  '我自己写的一段。',
  '',
].join('\n');

/**
 * 节点 GET / 会话 GET / 保存笔记 POST 三条路由。
 *
 * 键之间**不能互相包含**(桩按 `url.includes(key)` 取**首个**匹配),所以顺序是硬约束:
 * `/note` 在 `/nodes/l0` 前面 —— 否则 `/nodes/l0/note` 会被节点 GET 那条吃掉,保存
 * 请求拿回一个节点对象,而 `res.body` 恰好也是字符串(节点正文),于是屏幕上一切正常,
 * 只有文件没变。`/messages` 同理,它也得排在 `/nodes/l0` 之前。
 */
function noteRoutes(node, save, seen = []) {
  return {
    '/note': (url, init) => { seen.push(JSON.parse(init.body)); return save; },
    '/messages': { json: { messages: [] } },
    '/nodes/l0': () => ({ json: node }),
  };
}

async function openNotePage(over = {}) {
  const node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: NOTE_BODY });
  const seen = [];
  const m = await mounted({
    globals: { confirm: () => true },
    routes: noteRoutes(node, over.save || { json: { body: NOTE_BODY, saved: true } }, seen),
  });
  await m.KP.views.inspector.open('l0');
  await FLUSH();
  await FLUSH();
  bare.KP.dom.q(m.panel, 'p-note-open').fire('click');
  return { m, seen };
}

test('noteHTML:大标题、返回、讲解记录、编辑框四样都在', () => {
  const html = insp.noteHTML(F.node({ id: 'l0', body: NOTE_BODY }), { draft: null });
  assert.ok(html.includes('讲解记录'), '大标题不是「讲解记录」');
  assert.ok(html.includes('p-note-back'), '没有回节点页的路');
  // 讲解记录那一段渲染出来了(它是只读的正文渲染,不是原文)
  assert.ok(html.includes('把长文档切成可检索的块。'), html);
  assert.ok(html.includes('<h3>请讲讲切分粒度（2026-09-13 10:00）</h3>') ||
    html.includes('请讲讲切分粒度'), '讲解记录没渲染');
  // 编辑框的初始值 = 文件里「我的笔记」那一段的原文
  assert.ok(html.includes('id="p-note-text"'), html);
  assert.ok(html.includes('我自己写的一段。'), '编辑框里不是「我的笔记」的原文');
  // 提纲那一段**不该**出现在这一页(它归节点页那一列可点的小点)
  assert.ok(!html.includes('有哪些分块方法'), '提纲漏进了讲解记录页');
});

test('noteHTML:有草稿时编辑框显示草稿,不是文件里那一版', () => {
  const html = insp.noteHTML(F.node({ id: 'l0', body: NOTE_BODY }), { draft: '还没保存的新内容' });
  assert.ok(html.includes('还没保存的新内容'), html);
  assert.ok(!html.includes('我自己写的一段。'), '草稿被文件里的旧版本盖住了');
});

test('noteHTML:每个字段都被转义(正文来自 LLM / 用户自己,都不可信)', () => {
  const node = F.node({ id: 'l0', note_path: F.XSS, body: `## 讲解记录\n\n${F.XSS}\n\n## 我的笔记\n\n${F.XSS}\n` });
  const html = insp.noteHTML(node, { draft: F.XSS });
  assert.ok(!html.includes('<img'), html);
});

test('noteHTML:没有讲解记录时给一句话,不留白', () => {
  const html = insp.noteHTML(F.node({ id: 'l0', body: '# A\n\n## 要点\n- 甲\n' }), { draft: null });
  assert.ok(html.includes('还没有讲解记录'), html);
  assert.ok(html.includes('id="p-note-text"'), '编辑框不该跟着讲解记录一起消失');
});

test('讲解记录页:点入口进新页,点返回回到节点详情', async () => {
  const { m } = await openNotePage();
  // 进的是**新的一页**:节点页的标题条(带 ✕ 的那个)不在了,编辑框在了
  assert.equal(bare.KP.dom.q(m.panel, 'p-note-text') !== null, true, '没进讲解记录页');
  assert.equal(bare.KP.dom.q(m.panel, 'p-close'), null, '两页的标题条叠在一起了');
  assert.equal(m.KP.S.note.open, true);

  bare.KP.dom.q(m.panel, 'p-note-back').fire('click');
  assert.equal(m.KP.S.note.open, false, '返回没生效');
  assert.ok(bare.KP.dom.q(m.panel, 'p-note-open'), '返回之后回不到节点页');
  assert.equal(bare.KP.dom.q(m.panel, 'p-note-text'), null, '返回之后编辑框还在');
});

test('讲解记录页:保存请求带的是编辑框里那一份,成功后清掉草稿', async () => {
  const saved = NOTE_BODY.replace('我自己写的一段。', '改过的一段。');
  const { m, seen } = await openNotePage({ save: { json: { body: saved, saved: true } } });
  const ta = bare.KP.dom.q(m.panel, 'p-note-text');
  ta.value = '改过的一段。';
  ta.fire('input');
  bare.KP.dom.q(m.panel, 'p-note-save').fire('click');
  await FLUSH();
  await FLUSH();

  assert.deepEqual(seen, [{ note: '改过的一段。' }], '请求体不是编辑框里的内容');
  assert.equal(m.KP.S.note.draft, null, '保存成功后草稿没清 —— 下一次重绘会以为还有未保存的修改');
  assert.equal(m.KP.S.node.body, saved, '正文没换成后端带回来的那一份');
  assert.ok(m.panel.innerHTML.includes('已保存'), '保存成功之后什么都不说');
  assert.equal(bare.KP.dom.q(m.panel, 'p-note-text').value, '改过的一段。');
});

test('讲解记录页:保存失败时草稿留着,并把失败说出来', async () => {
  // 用户刚敲的一段字是这一屏上唯一不可再生的东西 —— 请求挂了就把它清掉,
  // 等于用一次网络抖动换掉用户五分钟。
  const { m } = await openNotePage({ save: { ok: false, status: 500, json: { detail: '磁盘满了' } } });
  const ta = bare.KP.dom.q(m.panel, 'p-note-text');
  ta.value = '写了很久的一段。';
  ta.fire('input');
  bare.KP.dom.q(m.panel, 'p-note-save').fire('click');
  await FLUSH();
  await FLUSH();

  assert.equal(m.KP.S.note.draft, '写了很久的一段。', '失败之后草稿被清掉了');
  assert.ok(m.panel.innerHTML.includes('磁盘满了'), m.panel.innerHTML.slice(0, 300));
  assert.equal(bare.KP.dom.q(m.panel, 'p-note-text').value, '写了很久的一段。',
    '失败之后编辑框里换成了文件里的旧版本');
});

// ---- 走查反馈 ⑤:保存状态必须说实话 ------------------------------------
//
// 用户报的是「实际上已经是保存了 但是会显示未保存」。文件确实写好了(磁盘上有),屏幕上
// 却挂着「有未保存的修改」、切节点还要再问一句 —— 这是一类**状态判据与落盘判据分叉**
// 的毛病,四条路各错一点,全都不报错:
//
//   1. 判「改过没有」用的是**字符比较**,而写盘与读回各带一次归一化(`strip("\n")` /
//      `trim()`)—— 末尾多敲一个换行就永远比不平;
//   2. 判「改过没有」用的是**真值**(`draft ? …`),而空串是合法的草稿(用户全选删掉);
//   3. 送出去的内容用 `draft || ''` 兜底 —— 没改过就按保存 = 把用户的笔记整段删掉;
//   4. 后端说了 `saved: false`、或者回传的正文里那一段和刚送出去的不一样,前端一概不看。

test('讲解记录页:存好之后不许再说「有未保存的修改」(走查反馈 ⑤)', async () => {
  // 用户报的就是这一条。文本框里那一份(用户敲的)和后端读回来的那一份**走的不是同一次
  // 归一化**:`write_user_notes` 把首尾换行 `strip("\n")`,前端 `splitNoteSections` 再
  // `trim()`。于是「敲的时候末尾带一个换行」这种最常见的输入,存完之后文本框的值永远
  // 不等于基准,下一帧 `captureDraft()` 就把它当成本地改动收回来,入口按钮上一直挂着
  // 「有未保存的修改」—— 而文件里其实早就写好了。
  const saved = NOTE_BODY.replace('我自己写的一段。', '存好的这一段。');
  const { m } = await openNotePage({ save: { json: { body: saved, saved: true } } });
  const ta = bare.KP.dom.q(m.panel, 'p-note-text');
  ta.value = '存好的这一段。\n';           // 末尾多了一个换行,文件里留不住它
  ta.fire('input');
  bare.KP.dom.q(m.panel, 'p-note-save').fire('click');
  await FLUSH();
  await FLUSH();

  assert.equal(m.KP.S.note.draft, null, '存完之后草稿没清 —— 屏幕上会说还有未保存的修改');
  bare.KP.dom.q(m.panel, 'p-note-back').fire('click');
  assert.ok(!m.panel.innerHTML.includes('有未保存的修改'), m.panel.innerHTML.slice(0, 400));
});

test('讲解记录页:一次迟到的节点刷新不许把「未保存」带回来', async () => {
  // 这是同一句抱怨的另一条来路,而且**不需要用户多敲一个空白字符**。
  // 聊完一轮右栏会 `refreshNode`(它自己也只按 `nodeId` 挡一道),`Object.assign(S.node, fresh)`
  // 把**新鲜但陈旧**的正文铺回来 —— 那份响应可能是在保存之前从服务端读的,却后到。
  // 于是下一帧 `captureDraft` 拿文本框里刚存好的字去和旧基准比,又把草稿收了回来:
  // 「实际上已经是保存了 但是会显示未保存」,一字不差。
  // 根因是「我的笔记」的基准被挂在 `S.node.body` 这座**渲染投影**上,而它有好几个写入者。
  // 基准归 `S.note` —— 只有开节点与保存这两件事能改它。
  const saved = NOTE_BODY.replace('我自己写的一段。', '存好的这一段。');
  // 桩每次给一份**新对象**、内容是保存之前那一版 —— 这就是「迟到的快照」的样子。
  // (不能直接把夹具对象交出去:那样 `S.node` 与桩里的东西是同一个引用,保存时的
  // `S.node.body = …` 会顺手把它也改掉,这一类测试会**假绿**。)
  const snapshot = F.node({ id: 'l0', name: '子领域1', key_points: [], body: NOTE_BODY });
  const m = await mounted({
    globals: { confirm: () => true },
    routes: {
      '/note': { json: { body: saved, saved: true } },
      '/messages': { json: { messages: [] } },
      '/nodes/l0': () => ({ json: Object.assign({}, snapshot) }),
    },
  });
  await m.KP.views.inspector.open('l0');
  await FLUSH();
  await FLUSH();
  bare.KP.dom.q(m.panel, 'p-note-open').fire('click');

  const ta = bare.KP.dom.q(m.panel, 'p-note-text');
  ta.value = '存好的这一段。';
  ta.fire('input');
  bare.KP.dom.q(m.panel, 'p-note-save').fire('click');
  await FLUSH();
  await FLUSH();
  assert.equal(m.KP.S.note.draft, null, '夹具:这一步之后本该是干净的');
  assert.equal(m.KP.S.node.body, saved, '夹具:保存后正文该是后端回传的那一份');

  // 迟到的刷新:它手里那份是在保存**之前**从服务端读的。
  await m.KP.views.inspector.refreshNode('l0');
  await FLUSH();

  assert.equal(m.KP.S.note.draft, null, '一次迟到的刷新把「未保存」带了回来');
  assert.equal(bare.KP.dom.q(m.panel, 'p-note-text').value, '存好的这一段。',
    '迟到的旧正文盖住了刚存好的那一份');
});

test('讲解记录页:没改过就按保存,送出去的是文件里那一份,不是一个空串', async () => {
  // `S.note.draft` 为 `null` = 没改过。以前这里写的是 `S.note.draft || ''` ——
  // 「打开讲解记录、看一眼、顺手按一下保存」就会把「我的笔记」整段清空,而且**看起来和
  // 成功一模一样**(后端确实写了一个空段落回来、也回传了正文)。更要紧的是第二下:
  // 存完之后 `draft` 是 `null`,再按一次保存就把刚存好的那一段抹掉。
  const { m, seen } = await openNotePage();
  const ta = bare.KP.dom.q(m.panel, 'p-note-text');
  assert.equal(ta.value, '我自己写的一段。', '夹具:编辑框里本该是文件里那一份');
  bare.KP.dom.q(m.panel, 'p-note-save').fire('click');
  await FLUSH();
  await FLUSH();

  assert.deepEqual(seen, [{ note: '我自己写的一段。' }],
    '没改过就保存却送了一个空串 —— 那是把用户的笔记删掉');
});

test('讲解记录页:把笔记清空也是一次改动(空串不是「没改过」)', async () => {
  // `draft` 用 `null` 表示「没改过」,所以判「改过没有」必须写 `!= null`。用真值判断
  // (`S.note.draft ? …`)的话,「全选 + 删除」之后按钮上不显示未保存、切节点也不问
  // —— 用户清空笔记的意图会被静默丢掉。**两处都要钉**:入口按钮上那句话(`render`)与
  // 离开前那句确认(`confirmDiscard`)。
  const node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: NOTE_BODY });
  const asked = [];
  const m = await mounted({
    globals: { confirm: (msg) => { asked.push(msg); return false; } },   // 用户点「取消」
    routes: noteRoutes(node, { json: { body: NOTE_BODY, saved: true } }),
  });
  await m.KP.views.inspector.open('l0');
  await FLUSH();
  await FLUSH();
  bare.KP.dom.q(m.panel, 'p-note-open').fire('click');
  const ta = bare.KP.dom.q(m.panel, 'p-note-text');
  ta.value = '';
  ta.fire('input');
  bare.KP.dom.q(m.panel, 'p-note-back').fire('click');

  assert.equal(m.KP.S.note.draft, '', '夹具:清空之后草稿该是空串');
  assert.ok(m.panel.innerHTML.includes('有未保存的修改'), m.panel.innerHTML.slice(0, 400));

  await m.KP.views.inspector.open('l1');
  assert.equal(asked.length, 1, '清空笔记之后切节点连问都没问 —— 那次清空被静默丢掉了');
  assert.equal(m.KP.S.nodeId, 'l0', '用户点了取消,节点还是切走了');
});

test('讲解记录页:后端说没写进去(saved: false)就不能说「已保存」', async () => {
  // `/note` 的返回值里有 `saved`(笔记文件被移走时是 `false`)与 `body`。以前只把 `body`
  // 铺回状态、`saved` 一眼都不看,于是「一个字节也没写」和「写好了」在屏幕上完全一样。
  const { m } = await openNotePage({ save: { json: { body: NOTE_BODY, saved: false } } });
  const ta = bare.KP.dom.q(m.panel, 'p-note-text');
  ta.value = '写了很久的一段。';
  ta.fire('input');
  bare.KP.dom.q(m.panel, 'p-note-save').fire('click');
  await FLUSH();
  await FLUSH();

  assert.ok(!m.panel.innerHTML.includes('已保存'), m.panel.innerHTML.slice(0, 400));
  assert.ok(m.panel.innerHTML.includes('保存失败'), m.panel.innerHTML.slice(0, 400));
  assert.equal(m.KP.S.note.draft, '写了很久的一段。', '说没写进去却把草稿清了');
});

test('讲解记录页:写进去的内容读回来对不上时,不许说「已保存」', async () => {
  // 前端靠后端回传的正文来判定「这一份存好了」(它不复刻「那一段插在哪」的规则,那份规则
  // 只该有一个实现:`notes.write_user_notes`)。回传的正文里「我的笔记」那一段和刚送出去的
  // 不一样,就说明这一次写没有按用户的意思落盘 —— 最典型的是正文里有一行以 `## ` 开头
  // (前后端都把它当成段的结束,于是文本框里只看得到前半段)。这时候说「已保存」是在替
  // 一次被截断的写入背书:用户下次打开看到的不是他写的那份。
  const truncated = NOTE_BODY.replace('我自己写的一段。', '前半段。\n\n## 手写的小标题\n');
  const { m } = await openNotePage({ save: { json: { body: truncated, saved: true } } });
  const ta = bare.KP.dom.q(m.panel, 'p-note-text');
  const typed = '前半段。\n\n## 手写的小标题\n\n后半段还在。';
  ta.value = typed;
  ta.fire('input');
  bare.KP.dom.q(m.panel, 'p-note-save').fire('click');
  await FLUSH();
  await FLUSH();

  assert.ok(!m.panel.innerHTML.includes('已保存'), m.panel.innerHTML.slice(0, 400));
  assert.equal(m.KP.S.note.draft, typed, '读回来对不上,却把草稿清了');
  assert.equal(bare.KP.dom.q(m.panel, 'p-note-text').value, typed, '草稿没写回文本框');
});

test('讲解记录页:聊完一轮重绘之后草稿还在(DOM 是投影,草稿住在 S 里)', async () => {
  const node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: NOTE_BODY });
  const m = await mounted({
    globals: { confirm: () => true },
    routes: noteRoutes(node, { json: { body: NOTE_BODY, saved: true } }),
  });
  await m.KP.views.inspector.open('l0');
  await FLUSH();
  await FLUSH();
  bare.KP.dom.q(m.panel, 'p-note-open').fire('click');

  const ta = bare.KP.dom.q(m.panel, 'p-note-text');
  ta.value = '还没保存的草稿';
  ta.fire('input');
  // 一轮对话结束 → 右栏重绘一次(它要更新「已对话 N 轮」)。草稿必须活过这次重绘:
  // 重绘换掉整个 innerHTML,只把草稿留在文本框里的话,用户敲的字就在这里消失,
  // 而且**没有任何报错**。
  await m.KP.views.inspector.refreshNode('l0');
  await FLUSH();
  assert.equal(m.KP.S.note.draft, '还没保存的草稿', '草稿在重绘中丢了');
  assert.equal(bare.KP.dom.q(m.panel, 'p-note-text').value, '还没保存的草稿',
    '重绘之后编辑框里不是草稿');
});

test('讲解记录页:返回节点页不丢草稿,入口按钮上写着「有未保存的修改」', async () => {
  const { m } = await openNotePage();
  const ta = bare.KP.dom.q(m.panel, 'p-note-text');
  ta.value = '边走边写';
  ta.fire('input');
  bare.KP.dom.q(m.panel, 'p-note-back').fire('click');

  assert.equal(m.KP.S.note.draft, '边走边写', '「返回」把草稿清掉了 —— 它只是切了一页');
  assert.ok(m.panel.innerHTML.includes('有未保存的修改'), m.panel.innerHTML.slice(0, 400));

  // 再进去,草稿还在(否则用户回来看到的是一份「被还原」的笔记)
  bare.KP.dom.q(m.panel, 'p-note-open').fire('click');
  assert.equal(bare.KP.dom.q(m.panel, 'p-note-text').value, '边走边写');
});

test('讲解记录页:脏草稿下切到别的节点要先问一句,取消就不切', async () => {
  const node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: NOTE_BODY });
  const asked = [];
  const m = await mounted({
    globals: { confirm: (msg) => { asked.push(msg); return false; } },   // 用户点「取消」
    routes: noteRoutes(node, { json: { body: NOTE_BODY, saved: true } }),
  });
  await m.KP.views.inspector.open('l0');
  await FLUSH();
  await FLUSH();
  bare.KP.dom.q(m.panel, 'p-note-open').fire('click');
  const ta = bare.KP.dom.q(m.panel, 'p-note-text');
  ta.value = '别丢了我';
  ta.fire('input');

  await m.KP.views.inspector.open('l1');
  assert.equal(asked.length, 1, '切节点没问一句 —— 草稿会静默消失');
  assert.equal(m.KP.S.nodeId, 'l0', '用户点了取消,节点还是切走了');
  assert.equal(m.KP.S.note.draft, '别丢了我', '取消之后草稿也没了');
});

test('关掉节点详情:脏草稿下要先问;取消时 hash 与面板都不动', async () => {
  const node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: NOTE_BODY });
  let allow = false;
  const asked = [];
  const m = await mounted({
    globals: { confirm: (msg) => { asked.push(msg); return allow; } },
    routes: noteRoutes(node, { json: { body: NOTE_BODY, saved: true } }),
  });
  await m.KP.views.inspector.open('l0');
  await FLUSH();
  await FLUSH();
  bare.KP.dom.q(m.panel, 'p-note-open').fire('click');
  const ta = bare.KP.dom.q(m.panel, 'p-note-text');
  ta.value = '别丢了我';
  ta.fire('input');
  bare.KP.dom.q(m.panel, 'p-note-back').fire('click');

  const hashCalls = m.sandbox.history.calls.length;
  bare.KP.dom.q(m.panel, 'p-close').fire('click');
  assert.equal(asked.length, 1, '关详情没问一句 —— 草稿会静默消失');
  assert.equal(m.KP.S.nodeId, 'l0', '用户点了取消,节点还是关掉了');
  // 点 ✕ 那条路上 hash 与图谱的重画必须跟着「没关成」一起回退,否则屏幕上会留下
  // 一次「关了但没关」的错位状态(hash 已经落到主题级、右边的面板却还开着)。
  assert.equal(m.sandbox.history.calls.length, hashCalls, '取消了却把 hash 改掉了');

  allow = true;
  bare.KP.dom.q(m.panel, 'p-close').fire('click');
  assert.equal(m.KP.S.nodeId, null, '确认之后还是没关掉');
  assert.equal(m.KP.S.note.draft, null, '关掉之后草稿还留着 —— 它已经没有入口了');
});

test('生成完成那条路也要尊重「别丢我的草稿」:点了取消就什么都别动', async () => {
  // `generate()` 的收尾会 `KP.views.inspector.close()` 清掉节点选择,而 `close()` 现在会被
  // 「我的笔记」那道确认拦住。**先清 `S.nodeId` 再 close** 的话,用户点一次「取消」就留下
  // 「图里没有选中节点、右栏还挂着那个节点详情」的错位状态 —— 草稿虽然在 `S` 里,但那个
  // 节点已经选不回来了,下一次打开它会走 `open()` 里的 `S.note` 归零:用户点了「取消」,
  // 草稿照样丢。所以状态变化只由 `close()` 拥有。
  const asked = [];
  const ready = F.star(3);
  const m = await mounted({
    globals: { confirm: (msg) => { asked.push(msg); return false; } },   // 用户点「取消」
    routes: {
      '/api/learning/topics/t1/generate': { json: {} },
      '/api/learning/topics/t1': { json: ready },
      '/api/learning/topics': { json: { topics: [{ id: 't1', title: 'T', status: 'ready', progress: { total: 4, mastered: 0, recommended: 0, percent: 0 } }] } },
    },
  });
  m.KP.S.topicId = 't1';
  m.KP.S.nodeId = 'l0';
  m.KP.S.node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: NOTE_BODY });
  m.KP.S.note = { open: true, draft: '别丢了我', busy: false, err: '', msg: '' };
  await m.KP.views.graph.generate();

  // 先确认真走到了「生成成功 → reload」那一支,否则这条用例是空的(没收尾就没 close,
  // 节点当然还在,而这跟守卫有没有牙毫无关系)。
  assert.ok(m.stage.innerHTML.includes('class="graph"'), '没走到 reload 分支,这条用例没测到东西');
  assert.equal(asked.length, 1, '生成收尾把草稿静默清掉了 —— 连问都没问');
  assert.equal(m.KP.S.nodeId, 'l0', '用户点了取消,节点选择还是被清掉了');
  assert.equal(m.KP.S.note.draft, '别丢了我', '取消之后草稿没了');
});

test('节点页:没有 Markdown 正文时不出入口按钮', async () => {
  const node = F.node({ id: 'l0', name: '子领域1', key_points: [], note_path: '', body: '' });
  const m = await mounted({ routes: { '/nodes/l0': { json: node } } });
  await m.KP.views.inspector.open('l0');
  await FLUSH();
  assert.equal(bare.KP.dom.q(m.panel, 'p-note-open'), null, '没有正文文件却给了一个打不开的入口');
});

// ---- 阶段 7:中心环 / 两种遮罩 / 设置页 ----------------------------------------
//
// 这一组测的东西有一个共同点:**它们错起来都不报错**。环画短了一截只是「看起来
// 差不多」;遮罩该出的时刻没出,用户只会以为没有这个功能;设置页的下拉框写不进
// `localStorage` 时如果不说,用户会把「改了不生效」当成自己的错觉。

const settingsView = bare.KP.views.settings;
const LAY = bare.KP.layout;
const tick = () => new Promise((r) => setTimeout(r, 0));

test('ringGeom:偏移只由百分比决定 —— 越界与脏值都不得产生 NaN', () => {
  // `Number(undefined) || 0` 与 `Math.min/max` 这两处一旦漏一个,环就会变成
  // `stroke-dashoffset="NaN"` —— SVG 对非法属性**不报错**,只是那一圈什么都不画。
  const r = 100;
  const c = 2 * Math.PI * r;
  assert.equal(graphView.ringGeom(0, r).c, c);
  assert.equal(graphView.ringGeom(0, r).offset, c, '0% 应当整圈都是空白段');
  assert.equal(graphView.ringGeom(100, r).offset, 0, '100% 应当画满一整圈');
  assert.equal(graphView.ringGeom(50, r).offset, c / 2);
  for (const bad of [NaN, undefined, null, 'abc', {}, [], -20, 120, Infinity, -Infinity]) {
    const g = graphView.ringGeom(bad, r);
    assert.ok(Number.isFinite(g.offset), `${String(bad)} 产生了非有限数`);
    assert.ok(g.offset >= 0 && g.offset <= c, `${String(bad)} 的偏移越界:${g.offset}`);
  }
});

test('centerRingHTML:长度读后端的 percent,**不自算**(R6 的第二个落点)', () => {
  // 侧栏的进度卡读 API 的 `percent`;中心环如果自己 `mastered/total*100`,1/8 这类
  // 比例上两边会差 1%(后端银行家舍入 `round(12.5) == 12`,JS `Math.round` 是 13)——
  // 而环上那个数字正是这张图最显眼的一处。
  const g = F.star(3);
  g.topic.progress = { total: 8, mastered: 1, recommended: 0, unlearned: 7, percent: 12 };
  const L = LAY.layout(g.nodes, g.edges, 'radial');
  const html = graphView.centerRingHTML(g, L);
  assert.ok(html.includes('id="center-ring"'), html);

  const r = Number(html.match(/class="ring-bar" id="center-ring" r="([\d.]+)"/)[1]);
  assert.ok(r > 0, '环半径算成了 0');
  const c = 2 * Math.PI * r;
  assert.ok(html.includes(`stroke-dasharray="${c.toFixed(2)}"`), html);
  assert.ok(html.includes(`stroke-dashoffset="${(c * (1 - 12 / 100)).toFixed(2)}"`), html);
  // 反过来说一句:如果是自算的 13%,上面那条会失败 —— 这条注释就是它的说明书。

  // 圆心 = `centerOf` 给的那个点。环要套在中心盒子上,偏心在截图上看只是「差一点」。
  const ctr = LAY.centerOf(g, L);
  assert.ok(html.includes(`translate(${ctr.x} ${ctr.y})`), html);
  assert.ok(html.includes('rotate(-90)'), '起点没转到正上方 —— 进度从三点钟开始读起来像差了 90°');
});

test('centerRingHTML:横向布局 / 多根图 / 空图一律返回空串', () => {
  // 这三种图上「中心」不是一个确定的位置(径向布局在原点没留位置,见 layout.js 的
  // `centerOf`)。返回空串是**结论**,不是漏了。
  const chained = F.chain(4);
  assert.equal(graphView.centerRingHTML(chained, LAY.layout(chained.nodes, chained.edges)), '');
  const twoRoots = F.graph(
    [F.node({ id: 'a', depth: 0, order_index: 0 }), F.node({ id: 'b', depth: 0, order_index: 1 })],
    [], { title: '双根' });
  assert.equal(graphView.centerRingHTML(twoRoots, LAY.layout(twoRoots.nodes, twoRoots.edges, 'radial')), '');
  const empty = F.emptyGraph();
  assert.equal(graphView.centerRingHTML(empty, LAY.layout(empty.nodes, empty.edges)), '');
  assert.equal(graphView.centerRingHTML(null, null), '', '空参数该静默返回空串,不是抛');
});

test('welcomeHTML:两个数字都来自真实数据,主题名过了 esc', () => {
  const html = graphView.welcomeHTML(F.star(4));
  assert.ok(html.includes('你的知识地图已经建立'), html);
  assert.ok(html.includes('<b>5</b> 个知识节点'), html);
  assert.ok(html.includes('<b>4</b> 条关系'), html);
  assert.ok(html.includes('id="ov-go"') && html.includes('开始学习'), html);
  assert.ok(html.includes('人工智能'), '主题名没当眉标出现');
  assert.ok(graphView.welcomeHTML({ topic: { title: F.XSS }, nodes: [], edges: [] })
    .indexOf('<img') === -1, '主题名没转义');
});

test('completeHTML:节点写 m / n,关系只写条数 —— 不编一个假分母', () => {
  // 设计稿 §25 画的是「43 / 43 个核心关系」,但关系**没有状态**:后端没有「这条前置
  // 关系学会了」这回事,那个 `/ 43` 是为了排版对称编出来的。分母是假的,就一个都别写。
  const g = F.star(4);
  g.topic.progress = { total: 5, mastered: 5, recommended: 0, percent: 100 };
  const html = graphView.completeHTML(g);
  assert.ok(html.includes('知识体系已点亮'), html);
  assert.ok(html.includes('<b>5 / 5</b> 个节点'), html);
  assert.ok(html.includes('<b>4</b> 条核心关系'), html);
  assert.ok(!html.includes('4 / 4'), '给关系编出了一个分母 —— 那是个不存在的状态');
  assert.ok(html.includes('继续探索') && html.includes('已完成学习'), html);
  assert.ok(html.includes(bare.KP.icons.STATE_GLYPH.mastered), '完成态连自己的字形都没有');
});

test('overlayHTML:认不出的 kind 一律不画 —— 不是画一张空卡片', () => {
  const g = F.star(3);
  for (const bad of [null, undefined, '', 'welcome2', 'complete!', 0, false]) {
    assert.equal(graphView.overlayHTML(bad, g), '', `${String(bad)} 不该画出东西`);
  }
  assert.ok(graphView.overlayHTML('welcome', g).includes('class="overlay"'));
  assert.ok(graphView.overlayHTML('complete', g).includes('class="overlay"'));
});

test('view():遮罩是画布上的一层,默认不出现', () => {
  const g = F.star(3);
  assert.ok(!graphView.view(g, {}).includes('class="overlay"'), '默认不该有遮罩');
  assert.ok(!graphView.view(g, { overlay: null }).includes('class="overlay"'));
  assert.ok(graphView.view(g, { overlay: 'welcome' }).includes('class="overlay"'));
  // 遮罩要在 `#graph-scroll` **外面**:跟着滚的话,图被拖到一边时遮罩也跟着偏,
  // 而它要盖住的是整个画布。
  const html = graphView.view(g, { overlay: 'welcome' });
  assert.ok(html.indexOf('class="overlay"') > html.indexOf('</div></div>') ||
            html.indexOf('class="overlay"') > html.indexOf('id="graph-fit"'),
    '遮罩被放进了滚动容器里');
});

// ---- 设置页:纯字符串层 ---------------------------------------------------------

test('设置页:两个危险动作都写着代价,而且**不在顶栏**', () => {
  const g = F.star(3);
  g.topic.progress = { total: 8, mastered: 1, recommended: 0, unlearned: 7, percent: 12 };
  const html = settingsView.pageHTML(g.topic, 0, true);
  assert.ok(html.includes('id="st-reset"') && html.includes('重置当前探索'), html);
  assert.ok(html.includes('id="st-regen"') && html.includes('重新研究并生成'), html);
  // 「重跑会按新的 order_index 重算 note_path,用户写在正文里的笔记成孤儿」——
  // 这是这条路径真实存在的代价,得写在按钮旁边,而不是等用户自己发现。
  assert.ok(html.includes('我的笔记'), html);
  assert.ok(html.includes('不能撤销'), '删除没有说明不可逆');
  // 百分比只读后端值:1/8 是 12 而不是 13。
  assert.ok(html.includes('12%'), '没有读到后端给的 percent');
  assert.ok(!html.includes('13%'), '自己算成了 13% —— 银行家舍入');
});

test('降级提示指的路,在设置页里真的存在', () => {
  // 第七轮的真 bug,也是这条测试存在的理由:提示让人去「设置页 →「进阶」」,而设置页
  // 里**根本没有**「进阶」这个分区(整个 `views/settings.js` 里连这两个字都 grep 不到)。
  // 用户照着找,找不到,多半会认为「这系统就是这样」。**指错路的提示比不指路更糟。**
  //
  // 所以不把两个名字各抄一遍(那样只是把同一句话写在两处,一起烂掉),而是把提示里
  // 用「」引起来的每个词都当成**指针**来验:它必须能在设置页渲染出来的 HTML 里找到。
  // 将来改设置页的分区名或按钮名(或改提示却指向一个不存在的地方),这里就红。
  const notice = graphView.degradedHTML(F.degraded());
  const names = (notice.match(/「[^」]+」/g) || []).map((s) => s.slice(1, -1));
  assert.ok(names.length >= 2, `提示里没有用「」标出具体位置:${notice}`);

  // 比的是**设置页上有名字的东西**(分区标题 + 按钮文字),不是整段 HTML 的子串。
  // `page.includes('当前探索')` 是恒真的:删除按钮的标签写着「重置当前探索」,把它兜住了 ——
  // 真去找的话,把卡片标题改掉这条断言也不红(实测过,所以这里多这层)。
  const named = (html) => [
    ...html.matchAll(/<h3>([^<]+)<\/h3>/g),
    ...html.matchAll(/<button[^>]*>([^<]+)<\/button>/g),
  ].map((m) => m[1].trim());

  const g = F.degraded();
  const pageNames = named(settingsView.pageHTML(g.topic, 0, true));
  for (const name of names) {
    assert.ok(pageNames.includes(name), `提示让人去「${name}」,而设置页上没有这个名字`);
  }
});

test('设置页:轮次下拉反映当前偏好,并说明它存在哪儿', () => {
  const html = settingsView.pageHTML(null, 3, true);
  assert.ok(html.includes('id="st-rounds"'), html);
  assert.ok(html.includes('<option value="3" selected>3 轮</option>'), html);
  assert.ok(html.includes('<option value="0">用服务端配置</option>'), html);
  assert.ok(html.includes('服务端没有配置接口'), '没说清楚这些偏好到底存在哪儿');
  // 写不进去时(无痕模式 / 企业策略)要**当场**说,而不是让用户改一个看起来生效、
  // 刷新就没了的开关。
  assert.ok(settingsView.pageHTML(null, 0, false).includes('不允许本地存储'));
  assert.ok(!settingsView.pageHTML(null, 0, true).includes('不允许本地存储'));
});

test('设置页:没有选中主题时给一句说明,不给一张 0% 的空卡', () => {
  const html = settingsView.pageHTML(null, 0, true);
  assert.ok(html.includes('还没有选中主题'), html);
  assert.ok(!html.includes('id="st-reset"'), '没有主题却给了删除按钮');
  assert.ok(!html.includes('id="st-regen"'), '没有主题却给了重生成按钮');
  assert.ok(!html.includes('0%'), '显示了一张 0% 的卡 —— 会被读成「一个知识点都没学」');
});

test('设置页:主题名过了 esc,且 roundsHTML 的选中项恰好一个', () => {
  assert.ok(!settingsView.pageHTML({ title: F.XSS, progress: {} }, 0, true).includes('<img'));
  for (const n of [0, 1, 2, 3, 4, 6]) {
    const h = settingsView.roundsHTML(n);
    assert.equal((h.match(/selected/g) || []).length, 1, `rounds=${n} 的选中项不是恰好一个`);
    assert.ok(h.includes(`<option value="${n}" selected>`), `rounds=${n} 没有选中自己`);
  }
  // 认不出的值(手改过 localStorage,或者将来改了上面那组分档):**一项都不选中**,
  // 浏览器于是显示第一个选项「用服务端配置」。这条把取舍写成「已知」而不是留着当 bug:
  // 不把那个值补成一个额外选项(凭空多一个只在这种情形下出现的 UI 元素),**也不在
  // `readRounds()` 里把它悄悄归零** —— `researchOpts()` 读的是偏好本身,归零会让
  // 「下拉框显示的服务端配置」与「请求里真的带着的值」对不上,而那正是这一页要避免
  // 的那类错。请求照常带着它出门,服务端的 `clamp_rounds` 负责收口。
  assert.equal((settingsView.roundsHTML(99).match(/selected/g) || []).length, 0,
    '给认不出的值编了一项 —— 那会凭空多出一个 UI 元素');
  assert.ok(settingsView.roundsHTML(99).includes('<option value="0">用服务端配置</option>'));
});

// ---- 遮罩:落到 DOM 的真实流程 --------------------------------------------------

test('遮罩:页内点进一个刚建好的主题 → 出首进入;按过之后不再出', async () => {
  const g = F.star(3);
  const m = await mounted({
    hash: '',
    routes: { '/api/learning/topics/t1': { json: g }, '/api/learning/topics': { json: { topics: [] } } },
  });
  await m.KP.views.shell.route();
  assert.ok(!m.stage.innerHTML.includes('class="overlay"'), '还没选主题就出了遮罩');

  // 页内跳转(点侧栏主题)不是深链接 —— `route()` 只在**首次**判一次 deepLink。
  m.KP.S.topicId = 't1';
  await m.KP.views.shell.loadTopic();
  assert.ok(m.stage.innerHTML.includes('你的知识地图已经建立'), m.stage.innerHTML.slice(0, 400));
  assert.ok(m.stage.innerHTML.includes('class="graph"'), '遮罩底下应当已经把图画好了');

  m.KP.dom.q(m.stage, 'ov-go').fire('click');
  assert.ok(!m.stage.innerHTML.includes('class="overlay"'), '按了「开始学习」遮罩还在');
  assert.equal(m.sandbox.localStorage.getItem('kp.onboarded'), 'true',
               '没把这个选择记下来 —— 每次进来都会再弹一次');
  await m.KP.views.shell.loadTopic();
  assert.ok(!m.stage.innerHTML.includes('class="overlay"'), '看过一次了还出');
});

test('遮罩:深链接直接进图谱 → 首进入不挡(用户手里已经有那张图)', async () => {
  // 挡住的代价不是「多看一眼」而是「看不到自己要的东西」—— 他手里那个链接指向的
  // 就是那张图,再盖一层引导卡只会挡路。
  const g = F.star(3);
  const m = await mounted({
    hash: '#/g/t1',
    routes: { '/api/learning/topics/t1': { json: g }, '/api/learning/topics': { json: { topics: [g.topic] } } },
  });
  await m.KP.views.shell.route();
  assert.equal(m.KP.S.topicId, 't1', '深链接没有被路由采纳');
  assert.equal(m.KP.S.entry.deepLink, true, '首次路由没有认出这是深链接');
  assert.ok(m.stage.innerHTML.includes('class="graph"'), '深链接没有画出图谱');
  assert.ok(!m.stage.innerHTML.includes('class="overlay"'), '深链接被首进入遮罩挡住了');
});

test('遮罩:全部点亮 → 完成态是**进度刷新把它带出来的**', async () => {
  // 关键的一条:点亮最后一个节点走的是局部 patch(`patchNodeEl`),不重绘整个 stage
  // (否则缩放/平移会被清掉,呼吸动画的相位也从头开始)。完成态如果只挂在「一次全量
  // 渲染」上,它就要等到用户碰一下筛选才出现 —— 而那一下正是「我做完了」最该被
  // 承认的时刻。
  const g = F.star(3);
  g.topic.id = 't1';
  const done = { ...g.topic, progress: { total: 4, mastered: 4, recommended: 0, unlearned: 0, percent: 100 } };
  const m = await mounted({
    hash: '#/g/t1',
    routes: {
      '/api/learning/topics/t1': { json: g },
      '/api/learning/topics': { json: { topics: [done] } },
    },
  });
  await m.KP.views.shell.route();
  assert.ok(!m.stage.innerHTML.includes('知识体系已点亮'), '还没点亮完就弹了完成态');
  // 遮罩没换的时候**一次都不该重绘** —— 重绘会把用户刚拖到的位置清掉。
  assert.equal(m.KP.views.graph.syncOverlay(), false, '遮罩没变却重绘了一次');

  await m.KP.views.shell.refreshProgress();
  assert.ok(m.stage.innerHTML.includes('知识体系已点亮'), m.stage.innerHTML.slice(0, 400));
  assert.ok(m.stage.innerHTML.includes('<b>4 / 4</b> 个节点'), m.stage.innerHTML.slice(0, 400));

  m.KP.dom.q(m.stage, 'ov-go').fire('click');
  assert.ok(!m.stage.innerHTML.includes('class="overlay"'), '按了「继续探索」遮罩还在');
  assert.equal(m.KP.S.view.completeSeenFor, 't1');
  // 「继续探索」是本会话的现场反应,**不该**写进 localStorage —— 换一个主题进来
  // 该看到的是那个主题自己的完成态(或者首进入)。
  assert.equal(m.sandbox.localStorage.getItem('kp.onboarded'), null);
});

test('遮罩:空主题(total 0)不弹完成态 —— 那是「还没生成」,不是「全学会了」(R7)', async () => {
  const g = F.emptyGraph();
  g.topic.id = 't1';
  const m = await mounted({
    hash: '#/g/t1',
    routes: { '/api/learning/topics/t1': { json: g }, '/api/learning/topics': { json: { topics: [g.topic] } } },
  });
  m.KP.S.topicId = 't1';
  await m.KP.views.shell.loadTopic();
  assert.ok(m.panel.innerHTML.includes('id="gen-go"'), '空主题该出的是生成卡（它在右栏）');
  assert.ok(!m.stage.innerHTML.includes('class="overlay"'), '空主题被当成了「全部点亮」');
});

// ---- 设置页:落到 DOM 的真实流程 ----------------------------------------------

test('设置页:「重置当前探索」打的是一次真实 DELETE,然后回到空态', async () => {
  // 本地清掉而服务端还在的话,刷新一次知识图谱就全回来了 —— 而用户以为自己删掉了。
  const g = F.star(3);
  g.topic.id = 't1';
  const m = await mounted({
    hash: '#/settings',
    globals: { confirm: () => true },
    routes: {
      '/api/learning/topics/t1': { json: g },
      '/api/learning/topics': { json: { topics: [g.topic] } },
    },
  });
  m.KP.S.topicId = 't1';
  m.KP.S.topics = [g.topic];
  await m.KP.views.shell.route();
  assert.ok(m.stage.innerHTML.includes('重置当前探索'), '路由没有落到设置页');

  m.KP.dom.q(m.stage, 'st-reset').fire('click');
  await tick();
  await tick();
  const dels = m.fetch.calls.filter((c) => (c.init || {}).method === 'DELETE');
  assert.equal(dels.length, 1, '「重置」只在本地清了一下 —— 刷新一次就全回来了');
  assert.ok(dels[0].url.includes('/topics/t1'), dels[0].url);
  assert.equal(m.KP.S.topicId, null);
  assert.ok(m.stage.innerHTML.includes('还没有选择主题'), m.stage.innerHTML.slice(0, 300));
});

test('设置页:确认框里点「取消」时什么都不发生 —— 不能点一下就没', async () => {
  const g = F.star(3);
  g.topic.id = 't1';
  const m = await mounted({
    hash: '#/settings',
    globals: { confirm: () => false },
    routes: {
      '/api/learning/topics/t1': { json: g },
      '/api/learning/topics': { json: { topics: [g.topic] } },
    },
  });
  m.KP.S.topicId = 't1';
  m.KP.S.topics = [g.topic];
  await m.KP.views.shell.route();
  m.KP.dom.q(m.stage, 'st-reset').fire('click');
  await tick();
  assert.equal(m.fetch.calls.filter((c) => (c.init || {}).method === 'DELETE').length, 0);
  assert.equal(m.KP.S.topicId, 't1', '取消之后选择也没了');
});

test('设置页:「重新研究并生成」先切回图谱视图,再真的发起生成', async () => {
  // 不先切走的话,生成卡会把整栏顶掉 —— 卡片住在 `#panel`,而这一屏的按钮在
  // `#stage`(`views/settings.js`);生成过程还要写 `#panel` 的进度行与逐字报告。
  // 用户看到的是设置页被顶掉,以为出了故障。
  const g = F.star(3);
  g.topic.id = 't1';
  const m = await mounted({
    hash: '#/settings',
    globals: { confirm: () => true },
    routes: {
      '/generate': { ok: false, status: 500, json: { detail: '上游炸了' } },
      '/api/learning/topics/t1': { json: g },
      // 返回一个**没就绪**的主题:否则 `generateOutcome` 会走 reload 分支去加载图谱,
      // 那样就测不到「失败后按钮回到可重试」这一段了。
      '/api/learning/topics': { json: { topics: [{ ...g.topic, status: 'failed' }] } },
    },
  });
  m.KP.S.topicId = 't1';
  m.KP.S.topics = [g.topic];
  await m.KP.views.shell.route();
  m.KP.dom.q(m.stage, 'st-regen').fire('click');
  await tick();
  await tick();

  assert.equal(m.KP.S.route.view, 'graph', '没有切回图谱视图');
  assert.ok(m.fetch.calls.some((c) => c.url.includes('/generate')), '没有真的发起生成');
  assert.equal(m.KP.S.busyGenerate, false, '生成失败后按钮卡在「生成中…」—— 不刷新就再也点不动');
  // 按钮文案读 `textContent` 而不是 `innerHTML`:`generate()` 是给已建好的按钮
  // 改文字,不是重画那一块 DOM —— 于是它不在 `innerHTML` 里(同 `generate` 那组用例)。
  const go = m.KP.dom.q(m.panel, 'gen-go');
  assert.ok(go, '生成卡没有渲染出来（它在右栏）—— 那说明按钮根本没恢复');
  assert.equal(go.disabled, false, '按钮还是禁用的');
  assert.equal(go.textContent, '重试', m.panel.innerHTML.slice(0, 300));
});

test('设置页:「我的笔记」有没保存的草稿时,重新生成被拒绝就整个中止', async () => {
  // 这条路径上会连着问两次:先问「重新研究并生成?」,再问「我的笔记还有没保存的修改」。
  // 第二次被拒绝时必须**中止**,而不是继续往下跑 —— 用户说的就是「别丢我的草稿」,
  // 而这次重新生成会让那些笔记从界面上消失(`note_path` 按新的 order_index 重算)。
  const g = F.star(3);
  g.topic.id = 't1';
  const asked = [];
  const m = await mounted({
    hash: '#/settings',
    // 第一个确认(重新生成)放行,第二个(丢草稿)拒绝。**按次数判,不按文案判** ——
    // 那句「重新研究并生成」的提示里也写着「## 我的笔记」(它得说明代价),按文案判会把
    // 两次确认认成同一句,于是这条用例会在「压根没问第二次」的情况下绿着。
    globals: { confirm: (msg) => { asked.push(msg); return asked.length === 1; } },
    routes: {
      '/generate': { json: {} },
      '/api/learning/topics/t1': { json: g },
      '/api/learning/topics': { json: { topics: [g.topic] } },
    },
  });
  m.KP.S.topicId = 't1';
  m.KP.S.topics = [g.topic];
  m.KP.S.nodeId = 'l0';
  m.KP.S.node = F.node({ id: 'l0', name: '子领域1', key_points: [], body: NOTE_BODY });
  m.KP.S.note = { open: true, draft: '别丢了我', busy: false, err: '', msg: '' };
  await m.KP.views.shell.route();

  m.KP.dom.q(m.stage, 'st-regen').fire('click');
  await tick();
  await tick();

  assert.equal(asked.length, 2, '没有问「我的笔记」那一句 —— 草稿会静默消失');
  assert.ok(!m.fetch.calls.some((c) => c.url.includes('/generate')),
    '用户说了别丢草稿,生成还是发起了');
  assert.equal(m.KP.S.busyGenerate, false, '中止得不干净 —— 忙碌标志留在原地');
  assert.equal(m.KP.S.nodeId, 'l0', '取消之后节点选择被清掉了(先清 nodeId 再 close 就会这样)');
  assert.equal(m.KP.S.note.draft, '别丢了我', '取消之后草稿没了');
});

test('设置页:轮次下拉写进本机偏好,并且写成功时不留假的警告', async () => {
  const m = await mounted({
    hash: '#/settings',
    routes: { '/api/learning/topics': { json: { topics: [] } } },
  });
  await m.KP.views.shell.route();
  const sel = m.KP.dom.q(m.stage, 'st-rounds');
  assert.ok(sel, '设置页没有渲染出轮次下拉');
  sel.value = '3';
  sel.fire('change');

  assert.equal(m.sandbox.localStorage.getItem('kp.research.maxToolRounds'), '3',
               '偏好没有落盘 —— 刷新一次就丢');
  assert.ok(!m.stage.innerHTML.includes('不允许本地存储'), '写成功了却还报「存不上」');
  // 而它必须**在这一刻**能被请求用上:偏好是发送时读的,不是启动时快照的。
  assert.deepEqual(plain(m.KP.researchOpts()), { max_tool_rounds: 3 });

  // 换回「用服务端配置」:偏好要**被删掉**而不是留一个 0 —— `0` 是个有效数字,
  // 下一次 `Number(prefs.get(...))` 把它读回来会让人以为「用户显式设了 0 轮」。
  m.KP.dom.q(m.stage, 'st-rounds').value = '0';
  m.KP.dom.q(m.stage, 'st-rounds').fire('change');
  assert.equal(m.sandbox.localStorage.getItem('kp.research.maxToolRounds'), null);
  assert.deepEqual(plain(m.KP.researchOpts()), {}, '回到服务端配置后仍然发了一个轮次参数');
});
