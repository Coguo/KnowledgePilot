/**
 * `js/store.js` —— 状态与派生纯函数。
 *
 * 这里守的是三类**算错了也不报错**的东西:
 *   1. 视觉编码的通道数(色盲/黑白可辨靠的是「四个通道互不重合」,删掉一个没人看得见);
 *   2. 百分比与完成态(**银行家舍入**让前端自算的值和后端差 1%,而那是页面上最显眼的数字);
 *   3. 路由往返(**`location.hash =` 与 `hashchange` 监听形成死循环**会表现为页面卡死)。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { loadKP, plain } from './harness.mjs';
import * as F from './fixtures/graphs.mjs';

const { KP } = await loadKP();
const { S, STATE, stateOf, corePhase, isComplete, pct, topicLabel, route } = KP;

// ---- 视觉编码 ----------------------------------------------------------------

test('STATE 全表:四态各自字段齐全,取值与设计稿调色逐字一致', () => {
  // **这张表逐字钉住是有意的**(Stage 1 立、Stage 4 与 Stage 5 各改过一次值):
  // 这几个色值是写进 SVG 的 `fill=` / `stroke=` **属性**的,属性不认 CSS 变量,
  // 所以它们是 `css/tokens.css` 之外的第二份字面量。改配色时总会漏掉一边 ——
  // 那张表就是为了让漏掉的一边当场变红。(Stage 4 换调色、Stage 5 加 `learning`
  // 时,这里都确实红过一次,而且红得很准。)
  assert.deepEqual(plain(STATE), {
    unlearned: {
      label: '未学', fill: '#ffffff', stroke: '#cbd5e1', dash: '5 4',
      glyph: '○', gfill: '#f1f5f9', gstroke: '#cbd5e1', gink: '#64748b',
    },
    learning: {
      // 紫 = `--accent-2`(`#7c6cff`)那一族。用紫而不是蓝:蓝色已经归主色
      // (选中态、链接、主按钮),「学习中」再抢蓝色会让两者在画布上分不开。
      // 字形圈是**描边**的(与 `unlearned`/`recommended` 一致)—— 实心圈留给
      // `mastered`:填满 = 完成,这是不用看颜色就能读出来的一层。
      label: '学习中', fill: '#f1efff', stroke: '#7c6cff', dash: '',
      glyph: '◉', gfill: '#e9e5ff', gstroke: '#7c6cff', gink: '#4c3fd6',
    },
    recommended: {
      label: '待确认', fill: '#fff8e1', stroke: '#d4a72c', dash: '',
      glyph: '!', gfill: '#fdf3d3', gstroke: '#d4a72c', gink: '#9a6700',
    },
    mastered: {
      label: '已掌握', fill: '#e4f7f0', stroke: '#20b486', dash: '',
      glyph: '✦', gfill: '#20b486', gstroke: '#20b486', gink: '#ffffff',
    },
  });
});

test('四通道不变式:每种状态都靠字形与文字认出自己,不靠颜色单打独斗', () => {
  // 色盲用户与黑白打印下,**底色和边框会同时失效**。字形与尾注文字是另外两个通道。
  // 别「顺手统一」掉角标或尾注 —— 那不会弄红任何东西,只会让一部分人读不出状态。
  const keys = ['label', 'fill', 'stroke', 'dash', 'glyph', 'gfill', 'gstroke', 'gink'];
  for (const [name, st] of Object.entries(STATE)) {
    for (const k of keys) assert.ok(st[k] !== undefined, `${name} 缺少通道 ${k}`);
  }
  const glyphs = Object.values(STATE).map((s) => s.glyph);
  assert.equal(new Set(glyphs).size, glyphs.length, '角标字形必须互不相同 —— 它是唯一的形状通道');
  const fills = Object.values(STATE).map((s) => s.fill);
  assert.equal(new Set(fills).size, fills.length, '底色互不相同');
  // 至少有一个非空 dash,否则虚线通道等于不存在。
  assert.ok(Object.values(STATE).some((s) => s.dash), '虚线通道被删空了');
  // **虚线通道在四态下做不到「四选一」**:只有 `unlearned` 是虚线,这正是它的
  // 语义(「还没开始」)。第四个通道因此是**尾注文字** —— `nodeFooter()` 对
  // 「学习中」给的是动态的「已对话 N 轮」,与 `STATE.learning.label` 不同。
  // 下面那条断言钉的就是这件事:尾注不是 `label` 的简单回显。
  assert.equal(KP.nodeFooter({ status: 'unlearned', chat_turns: 3 }), '已对话 3 轮');
  assert.notEqual(KP.nodeFooter({ status: 'unlearned', chat_turns: 3 }), STATE.learning.label);
});

test('nodeState 全表:四态 × chat_turns 的边界', () => {
  const of = KP.nodeState;
  assert.equal(of({ status: 'mastered' }), 'mastered');
  assert.equal(of({ status: 'recommended' }), 'recommended');
  assert.equal(of({ status: 'unlearned', chat_turns: 0 }), 'unlearned');
  assert.equal(of({ status: 'unlearned', chat_turns: 1 }), 'learning');
  assert.equal(of({ status: 'unlearned' }), 'unlearned', '缺 chat_turns 当 0');
  // **`mastered`/`recommended` 不会被 chat_turns 拉回「学习中」** —— 判定顺序
  // 写反的话,一个已掌握的节点只要聊过天就会退回紫色,而它看起来完全说得通
  // (「学过但还在聊」),没人会觉得是 bug。
  assert.equal(of({ status: 'mastered', chat_turns: 9 }), 'mastered');
  assert.equal(of({ status: 'recommended', chat_turns: 9 }), 'recommended');
  // 后端字段是 SQLite 出来的,可能是字符串。
  assert.equal(of({ status: 'unlearned', chat_turns: '2' }), 'learning');
  assert.equal(of({ status: 'unlearned', chat_turns: null }), 'unlearned');
  for (const bad of [{ status: 'brand-new' }, { status: '' }, {}, null, undefined]) {
    assert.equal(of(bad), 'unlearned', `${JSON.stringify(bad)} 没有兜底`);
  }
});

test('nodeFooter:四种状态给四句不同的话(文字通道)', () => {
  assert.equal(KP.nodeFooter({ status: 'unlearned', chat_turns: 0 }), '未学');
  assert.equal(KP.nodeFooter({ status: 'unlearned', chat_turns: 4 }), '已对话 4 轮');
  assert.equal(KP.nodeFooter({ status: 'recommended' }), '待确认');
  assert.equal(KP.nodeFooter({ status: 'mastered' }), '已掌握');
  // 脏数据不得产出 `已对话 NaN 轮` —— 那是页面上最显眼的「代码漏出来了」。
  // 这里**不加 `|| 0` 硬凑成「已对话 0 轮」**:轮数字段是垃圾值时,`nodeState()`
  // 判的是「没有聊过的证据」,于是状态是「未学」。尾注若自己说「已对话 0 轮」,
  // 就会出现「盒子画成未学、尾注说聊过 0 轮」这种自相矛盾 —— 脏数据的正确落法
  // 是**退回一致的那一侧**,而不是每个通道各自编一个像样的值。
  assert.equal(KP.nodeFooter({ status: 'unlearned', chat_turns: 'x' }), '未学');
  assert.equal(KP.nodeFooter(null), '未学');
});

test('stateOf:未知 status / null / 缺字段一律兜底成 unlearned,绝不 undefined', () => {
  assert.equal(stateOf({ status: 'mastered' }), STATE.mastered);
  assert.equal(stateOf({ status: 'recommended' }), STATE.recommended);
  assert.equal(stateOf({ status: 'unlearned' }), STATE.unlearned);
  for (const bad of [{ status: 'something-new' }, { status: '' }, {}, null, undefined]) {
    assert.equal(stateOf(bad), STATE.unlearned, `${JSON.stringify(bad)} 没有兜底`);
  }
});

// ---- 百分比与完成态 ----------------------------------------------------------

test('pct 只读后端值:1/8 走 API 的 12,绝不自己算成 13', () => {
  // Python `round(12.5) == 12`(银行家舍入),JS `Math.round(12.5) == 13`。
  // 前端自算会让侧栏(读 API)与中心环差 1%,而那正是设计稿最显眼的数字。
  const p = { total: 8, mastered: 1, percent: 12 };
  assert.equal(pct(p), 12);
  assert.notEqual(pct(p), Math.round((p.mastered * 100) / p.total));
});

test('pct:缺字段 / 非数字 / null 一律退化成 0,不产生 NaN', () => {
  for (const bad of [null, undefined, {}, { percent: undefined }, { percent: 'x' }]) {
    assert.equal(pct(bad), 0, `${JSON.stringify(bad)} 没有退化成 0`);
  }
  assert.equal(pct({ percent: '50' }), 50, '后端若把数字序列化成字符串,仍应读出数值');
});

test('corePhase:total === 0 是 idle,绝不是 done', () => {
  // 空主题的 percent 后端返回 0;若把「没有节点」当「全部掌握」,
  // 新建的空主题会立刻显示「已完成 / 全部点亮」。
  assert.equal(corePhase({ total: 0, mastered: 0 }), 'idle');
  assert.equal(corePhase({}), 'idle');
  assert.equal(corePhase(null), 'idle');
  assert.equal(isComplete({ total: 0, mastered: 0 }), false);
});

test('corePhase:未开始 / 进行中 / 全部完成', () => {
  assert.equal(corePhase({ total: 4, mastered: 0 }), 'idle');
  assert.equal(corePhase({ total: 4, mastered: 1 }), 'active');
  assert.equal(corePhase({ total: 4, mastered: 3 }), 'active');
  assert.equal(corePhase({ total: 4, mastered: 4 }), 'done');
  assert.equal(isComplete({ total: 4, mastered: 4 }), true);
});

test('corePhase:mastered 因数据脏而大于 total 时仍算 done,不抛也不回退', () => {
  assert.equal(corePhase({ total: 3, mastered: 5 }), 'done');
});

test('topicLabel:四个后端状态全覆盖,未知值原样回吐而不是 undefined', () => {
  assert.equal(topicLabel('empty'), '待生成');
  assert.equal(topicLabel('generating'), '生成中');
  assert.equal(topicLabel('ready'), '已就绪');
  assert.equal(topicLabel('failed'), '生成失败');
  assert.equal(topicLabel('weird'), 'weird');
  assert.equal(topicLabel(null), '');
  assert.equal(topicLabel(undefined), '');
});

// ---- 加载失败的分类 ----------------------------------------------------------

test('currentTopic:取不到一律返回 null,不抛也不返回 undefined', () => {
  // 侧栏的进度卡与标题都读它。返回 undefined 会让 `progressHTML(undefined)`
  // 一路走到「属性 of undefined」;返回 null 则是「不渲染」这个明确的意图。
  const list = [{ id: 'a', title: 'A' }, { id: 'b', title: 'B' }];
  assert.equal(KP.currentTopic(list, 'b').title, 'B');
  for (const [ts, id] of [[list, null], [list, ''], [list, 'zzz'], [[], 'a'],
                          [null, 'a'], [undefined, 'a'], [list, undefined]]) {
    assert.equal(KP.currentTopic(ts, id), null, `topics=${JSON.stringify(ts)} id=${id} 没有返回 null`);
  }
  // 列表里混进 null 项(接口异常)时也不能抛 —— 侧栏渲染崩了整页就白了。
  assert.equal(KP.currentTopic([null, { id: 'a' }], 'a').id, 'a');
  assert.equal(KP.currentTopic([null], 'a'), null);
});

test('loadFailureKind:只有 404 算「主题没了」,其余都算可重试', () => {
  // 两个分支的行为正好相反,混在一起就会退化成「所有错误都当成主题不存在」:
  // 一次网络抖动会清掉用户的选择与深链接,而用户唯一能做的是反复刷新碰运气。
  const err = (status) => Object.assign(new Error('x'), { status });
  assert.equal(KP.loadFailureKind(err(404)), 'missing');
  for (const s of [500, 502, 503, 0, 200]) {
    assert.equal(KP.loadFailureKind(err(s)), 'error', `status ${s} 被判成了「已删除」`);
  }
});

test('loadFailureKind:拿不到状态码时按「可重试」处理', () => {
  // 网络层异常(断网、DNS、超时)没有 status。宁可多给一次重试入口,
  // 也不要误判成「主题已被删除」把用户的选择扔了。
  for (const bad of [null, undefined, {}, new Error('Failed to fetch')]) {
    assert.equal(KP.loadFailureKind(bad), 'error');
  }
});

// ---- 路由 --------------------------------------------------------------------

const ROUTES = [
  '#/',
  '#/g/topic-1',
  '#/g/topic-1/n/node-7',
  '#/explore',
  '#/chat',
  '#/settings',
];

test('parseHash → formatHash 往返稳定', () => {
  for (const h of ROUTES) {
    assert.equal(route.formatHash(route.parseHash(h)), h, `${h} 往返后变了`);
  }
});

test('parseHash:逐个形状', () => {
  assert.deepEqual(plain(route.parseHash('#/')), { view: 'graph', topicId: null, nodeId: null });
  assert.deepEqual(plain(route.parseHash('')), { view: 'graph', topicId: null, nodeId: null });
  assert.deepEqual(plain(route.parseHash('#/g/a')), { view: 'graph', topicId: 'a', nodeId: null });
  assert.deepEqual(plain(route.parseHash('#/g/a/n/b')), { view: 'graph', topicId: 'a', nodeId: 'b' });
  assert.deepEqual(plain(route.parseHash('#/explore')), { view: 'explore', topicId: null, nodeId: null });
});

test('parseHash:未知 hash 落回图谱首页,而不是白屏或抛异常', () => {
  for (const h of ['#/nope', '#/g/a/x/b', '#////', '#/g']) {
    const r = route.parseHash(h);
    assert.equal(r.view, 'graph', `${h} 没有落回图谱`);
  }
  assert.equal(route.parseHash('#/g').topicId, null);
  assert.equal(route.parseHash('#/g/a/x/b').nodeId, null, '段名不是 n 就不该当成节点');
});

test('formatHash:非图谱视图忽略残留的 topicId/nodeId', () => {
  assert.equal(route.formatHash({ view: 'explore', topicId: 'a', nodeId: 'b' }), '#/explore');
  assert.equal(route.formatHash({ view: 'graph', topicId: null, nodeId: 'b' }), '#/');
  assert.equal(route.formatHash({}), '#/');
  assert.equal(route.formatHash(null), '#/');
});

test('formatHash 与 parseHash 的编码成对:id 里的 / 不会把一段劈成两段', () => {
  // 只编码不解码时,`#/g/a%2Fb` 会被读成字面量 `a%2Fb`,与真实 id 不等 ——
  // 表现是「点进主题正常、刷新后提示主题不存在」。
  const h = route.formatHash({ view: 'graph', topicId: 'a/b', nodeId: 'c/d' });
  assert.ok(!h.slice(3).includes('a/b'), `未编码:${h}`);
  const r = route.parseHash(h);
  assert.equal(r.topicId, 'a/b');
  assert.equal(r.nodeId, 'c/d');
});

test('parseHash:畸形百分号编码不得让路由抛异常(抛了就是整页白屏)', () => {
  for (const h of ['#/g/%', '#/g/a%ZZ', '#/g/%E4%B8', '#/g/a/n/%']) {
    const r = route.parseHash(h);
    assert.equal(r.view, 'graph', `${h} 抛了或没落回图谱`);
    assert.equal(typeof r.topicId, 'string');
  }
});

test('状态形状:忙碌标志已拆开,不存在单个 busy(R15)', () => {
  // 原来是一个 `busy`:生成主题和「某个节点在对话」合成一个标志,会让
  // 「生成中不能聊天」这种限制凭空出现。拆开这件事靠这条断言钉住。
  assert.equal(S.busyGenerate, false);
  assert.equal(S.busyChat, null);
  assert.ok(!('busy' in S), '又冒出单个 busy 了 —— 两个互不相干的忙碌状态不该共用一个标志');
  assert.ok('disabled' in S && 'disabledReason' in S, '503 不可用状态丢了,关掉图谱的部署会显示空白侧栏');
  // 布局偏好:`null` = 由 `choose()` 按图的形状定。丢了它,用户手动切换的布局
  // 会在下一次重绘时被自动选择悄悄顶掉 —— 表现是「点了按钮,过一会儿自己变回去」。
  assert.ok('view' in S && 'mode' in S.view, '布局偏好丢了');
  assert.equal(S.view.mode, null, '默认必须是「自动」,不能预设成某个布局');
});

test('状态形状:同一份夹具的 progress 全表不炸选择器', () => {
  for (const name of Object.keys(F.ALL)) {
    const g = F.ALL[name]();
    const p = g.topic.progress;
    assert.ok(['idle', 'active', 'done'].includes(corePhase(p)), `${name} 的 corePhase 越界`);
    assert.ok(Number.isFinite(pct(p)), `${name} 的 pct 不是有限数`);
    for (const n of g.nodes) assert.ok(stateOf(n), `${name}/${n.id} 的 stateOf 是空值`);
  }
});

// ---- 筛选(阶段 5)-------------------------------------------------------------

test('typeFacet:pipe 串与空值都归「未分类」,正常值原样保留', () => {
  const f = KP.typeFacet;
  assert.equal(f('概念'), '概念');
  assert.equal(f('  技术  '), '技术', '两边空白要剥掉,否则会多出两个看起来一样的 facet');
  // 下面这两条是 R9 的核心:prompt 里写的是 `概念|技术|方法|工具`,**不强制**,
  // 而 LLM 有时会把那串枚举**整串回吐**。不归一的话筛选条上会出现一个叫
  // 「概念|技术|方法|工具」的芯片 —— 它看起来像是设计如此。
  assert.equal(f('概念|技术|方法|工具'), '未分类');
  assert.equal(f('概念｜技术'), '未分类', '全角竖线也要认');
  assert.equal(f(''), '未分类');
  assert.equal(f('   '), '未分类');
  assert.equal(f(null), '未分类');
  assert.equal(f(undefined), '未分类');
});

test('typeFacets:数量降序 + 同名按名字,顺序必须**稳定**', () => {
  const nodes = [
    { type: '概念' }, { type: '概念' }, { type: '概念' },
    { type: '技术' }, { type: '技术' },
    { type: '方法' },
  ];
  const got = plain(KP.typeFacets(nodes)).map((x) => [x.key, x.count]);
  assert.deepEqual(got, [['概念', 3], ['技术', 2], ['方法', 1]]);
  // 同数量的两个按名字排 —— 顺序不稳的话每次渲染芯片都会换位置,
  // 用户按「技术」的那一下可能正好按到刚挪过来的「概念」,而它不会报错。
  const tied = plain(KP.typeFacets([{ type: '乙' }, { type: '甲' }])).map((x) => x.key);
  assert.deepEqual(tied, plain(KP.typeFacets([{ type: '甲' }, { type: '乙' }])).map((x) => x.key));
  assert.deepEqual(tied, ['甲', '乙'], 'localeCompare 的 zh 序');
  // 不截断:想过给 8 个上限,放弃了 —— 截断会让被砍掉的节点再也筛不出来,
  // 而用户不知道自己少了什么。这条断言把「不截断」钉住。
  const many = Array.from({ length: 12 }, (_, i) => ({ type: `T${i}` }));
  assert.equal(KP.typeFacets(many).length, 12);
  assert.deepEqual(plain(KP.typeFacets([])), []);
  assert.deepEqual(plain(KP.typeFacets(null)), []);
});

test('filterNodes:筛掉不该看的,边只留两端都可见的', () => {
  const g = F.star(4);   // root + l0..l3,其中 l0/l2 是「方法」、l1/l3 是「技术」
  const byType = KP.filterNodes(g.nodes, g.edges, { status: 'all', type: '技术' });
  assert.equal(byType.nodes.length, 2, '按类型筛出来的数量不对');
  assert.ok(byType.nodes.every((n) => n.type === '技术'));
  // root 是「概念」,被筛掉了 → 四条边全部一端可见、一端不可见,一条都不该留。
  // 留着它们会画出四根指向空气的线。
  assert.equal(byType.edges.length, 0);
  assert.ok(byType.ids.has('l1') && !byType.ids.has('root'));
});

test("filterNodes:'all' / 缺字段 / null 一律等于不筛", () => {
  const g = F.fourStates();
  const all = KP.filterNodes(g.nodes, g.edges, { status: 'all', type: 'all' });
  assert.equal(all.nodes.length, g.nodes.length);
  assert.equal(all.edges.length, g.edges.length);
  for (const f of [null, undefined, {}]) {
    assert.equal(KP.filterNodes(g.nodes, g.edges, f).nodes.length, g.nodes.length);
  }
});

test('filterNodes:按状态筛走的是**四态**,不是后端的三态', () => {
  // 「在学」那个节点后端 `status` 是 `unlearned`,但 `chat_turns === 3`。
  // 用后端三态筛的话它会被算进「未学习」,而图上它画的是紫色的「学习中」——
  // 筛选结果与画面自相矛盾。
  const g = F.fourStates();
  const learning = KP.filterNodes(g.nodes, g.edges, { status: 'learning' });
  assert.deepEqual(plain(learning.nodes.map((n) => n.id)), ['s']);
  const unlearned = KP.filterNodes(g.nodes, g.edges, { status: 'unlearned' });
  assert.deepEqual(plain(unlearned.nodes.map((n) => n.id)), ['u']);
});

test('filterNodes:筛选不重排布局 —— 位置由全量节点决定', () => {
  // 这一条是刻意的:按筛选后的子集重排,点一下「未学习」整张图会重新布局,
  // 剩下的节点换个位置,再点回「全部」又换一次 —— 用户失去的正是「我在这张
  // 地图上的位置」这个唯一有价值的东西。
  const g = F.fourStates();
  const full = plain(KP.layout.layout(g.nodes, g.edges, 'layered'));
  const vis = KP.filterNodes(g.nodes, g.edges, { status: 'unlearned' });
  assert.ok(vis.nodes.length < g.nodes.length, '夹具没筛掉任何东西,这条断言就没意义');
  // 被筛出来的那些节点在**全量布局**里的坐标,与在「只拿可见节点重新布局」
  // 里的坐标**不同** —— 证明这两个布局确实是两回事,从而证明渲染层必须走
  // 全量那一份。
  const sub = plain(KP.layout.layout(vis.nodes, vis.edges, 'layered'));
  const differing = vis.nodes.filter((n) => {
    const a = full.pos[n.id];
    const b = sub.pos[n.id];
    return !a || !b || a.x !== b.x || a.y !== b.y;
  });
  assert.ok(differing.length > 0, '两种布局碰巧一致,这条断言失去了守卫意义');
});

// ---- 相关节点(阶段 6:右栏「相关节点」+ 助手的邻居)-------------------------

test('状态形状:助手默认收起、研究页默认空且不忙', () => {
  // 助手是**辅助入口**。默认展开的话,每点一个节点都会弹出一块面板,
  // 而用户只是想看看那个点的详情 —— 「弹出来了」这件事本身就在逼他处理它。
  assert.equal(S.assistant.open, false);
  assert.equal(S.assistant.err, '');
  assert.equal(S.research.busy, false);
  assert.deepEqual(plain(S.research.messages), []);
  // 「对话」页不持久化(后端没有历史 GET),所以状态里也不该出现「假装有历史」的
  // 字段 —— 有的话下一个人会真的往里写,然后刷新即丢。
  assert.ok(!('history' in S.research) && !('persisted' in S.research));
});

test('relatedNodes:从 edges 双向推导,方向标对', () => {
  // 整图的 node **没有** `prerequisites` 字段(那是单节点接口的冗余)。
  // 只要实现改成读节点字段,这条立刻变红 —— 而在浏览器里它只表现为
  // 「相关节点永远是空的」,看起来像「这个点没有邻居」。
  const g = F.star(4);
  const fromRoot = plain(KP.relatedNodes(g, 'root'));
  assert.deepEqual(fromRoot.map((r) => [r.node.id, r.dir]),
    [['l0', 'out'], ['l1', 'out'], ['l2', 'out'], ['l3', 'out']]);
  const fromLeaf = plain(KP.relatedNodes(g, 'l1'));
  assert.deepEqual(fromLeaf.map((r) => [r.node.id, r.dir]), [['root', 'in']]);
});

test('relatedNodes:出边排在入边前面(「下一步能学什么」比「它从哪来」更常被用)', () => {
  const g = F.chain(3);   // c0 → c1 → c2
  assert.deepEqual(plain(KP.relatedNodes(g, 'c1').map((r) => [r.node.id, r.dir])),
    [['c2', 'out'], ['c0', 'in']]);
});

test('relatedNodes:同向按 (order_index, name) 排 —— 顺序必须稳定', () => {
  // 不稳的话每次点节点列表顺序都变,而用户正想按着这个列表找下一个。
  const nodes = [
    F.node({ id: 'hub', order_index: 0, depth: 0, name: '中心' }),
    F.node({ id: 'z', order_index: 5, depth: 1, name: 'Z' }),
    F.node({ id: 'a', order_index: 2, depth: 1, name: 'A' }),
    F.node({ id: 'y', order_index: 9, depth: 0, name: 'Y' }),
    F.node({ id: 'b', order_index: 1, depth: 0, name: 'B' }),
  ];
  const g = F.graph(nodes, [['hub', 'z'], ['hub', 'a'], ['y', 'hub'], ['b', 'hub']]);
  const ids = plain(KP.relatedNodes(g, 'hub')).map((r) => r.node.id);
  assert.deepEqual(ids, ['a', 'z', 'b', 'y']);
  // 同一份输入两次结果必须一样(排序里若混进 `Map` 的插入序之外的东西就会飘)。
  assert.deepEqual(plain(KP.relatedNodes(g, 'hub')).map((r) => r.node.id), ids);
  // order_index 相同时退回名字排序,且仍是确定序。
  const tie = F.graph(
    [F.node({ id: 'h', order_index: 0, depth: 0 }), F.node({ id: 'm', order_index: 1, name: '乙' }),
     F.node({ id: 'k', order_index: 1, name: '甲' })],
    [['h', 'm'], ['h', 'k']]);
  assert.deepEqual(plain(KP.relatedNodes(tie, 'h')).map((r) => r.node.id), ['k', 'm']);
});

test('relatedNodes:自环不把自己列成自己的邻居', () => {
  // 列进去的话点一下只是原地不动,而列表上看起来是一个正常的入口。
  const g = F.graph(
    [F.node({ id: 'x', order_index: 0, depth: 0 }), F.node({ id: 'y', order_index: 1, depth: 1 })],
    [['x', 'x'], ['x', 'y']]);
  assert.deepEqual(plain(KP.relatedNodes(g, 'x')).map((r) => r.node.id), ['y']);
});

test('relatedNodes:重复边去重,指向不存在节点的边丢掉(不留 {node: undefined})', () => {
  const g = F.graph(
    [F.node({ id: 'x', order_index: 0, depth: 0 }), F.node({ id: 'y', order_index: 1, depth: 1 })],
    [['x', 'y'], ['x', 'y'], ['x', '幽灵']]);
  const rels = plain(KP.relatedNodes(g, 'x'));
  assert.deepEqual(rels.map((r) => r.node.id), ['y']);
  assert.ok(rels.every((r) => r.node && r.node.name), '留下了没有 node 的条目 —— 渲染时会炸在 r.node.name 上');
});

test('relatedNodes:每条都带 dir 与 relation 两个字段,取值只在闭集内', () => {
  const rels = plain(KP.relatedNodes(F.star(3), 'root'));
  for (const r of rels) {
    assert.ok(r.dir === 'out' || r.dir === 'in', `dir 越界:${r.dir}`);
    assert.equal(typeof r.relation, 'string');
  }
});

test('relatedNodes:空图 / 没选中节点 / 缺字段一律返回 [],不抛', () => {
  assert.deepEqual(plain(KP.relatedNodes(F.emptyGraph(), 'x')), []);
  assert.deepEqual(plain(KP.relatedNodes(F.star(3), '')), []);
  assert.deepEqual(plain(KP.relatedNodes(F.star(3), null)), []);
  assert.deepEqual(plain(KP.relatedNodes(null, 'x')), []);
  assert.deepEqual(plain(KP.relatedNodes({}, 'x')), []);
  // 只有节点没有 edges 字段(病态响应)时也不该抛 —— 它只是没有邻居。
  assert.deepEqual(plain(KP.relatedNodes({ nodes: [F.node({ id: 'x' })] }, 'x')), []);
});

// ---- Stage 7:遮罩判据 / 本机偏好 / 研究参数 --------------------------------

test('overlayKind:total === 0 两个遮罩都不出 —— 空主题绝不能显示「已点亮」', () => {
  // 与 `corePhase` 那条同源:空主题的 `percent` 后端给 0,若把「没有节点」当成
  // 「全部掌握」,新建一个主题进去就是「知识体系已点亮」。
  for (const g of [F.emptyGraph(), { nodes: [], edges: [], topic: {} }, null, {}]) {
    assert.equal(KP.overlayKind(g, {}), null, `${JSON.stringify(g)} 不该出遮罩`);
  }
});

test('overlayKind:「全部点亮」优先,而且不因 seen 变成「开始学习」', () => {
  const g = F.star(3);
  g.topic.progress = { total: 4, mastered: 4, recommended: 0, percent: 100 };
  assert.equal(KP.overlayKind(g, {}), 'complete');
  // 已经按过「继续探索」 → 本会话不再出。
  assert.equal(KP.overlayKind(g, { completeSeen: true }), null);
  // **关键的一条**:这张图就算从没出过首进入提示,也不该出「开始学习」——
  // 所有节点都亮着的时候让用户「开始学习」自相矛盾。
  assert.equal(KP.overlayKind(g, { seen: false, deepLink: false, nodeId: null }), 'complete');
});

test('overlayKind:深链接 / 已选中节点 / 已经看过 —— 三种都不出首进入', () => {
  const g = F.star(3);
  assert.equal(KP.overlayKind(g, {}), 'welcome');
  assert.equal(KP.overlayKind(g, { seen: true }), null);
  assert.equal(KP.overlayKind(g, { deepLink: true }), null);
  assert.equal(KP.overlayKind(g, { nodeId: 'root' }), null);
  // 三个条件任何一个成立就够 —— 别写成 `&&`(那样只有三条全占才挡得住)。
  assert.equal(KP.overlayKind(g, { seen: true, deepLink: true, nodeId: 'root' }), null);
  assert.equal(KP.overlayKind(g, { deepLink: true, nodeId: 'root' }), null);
});

test('overlayKind:开始学了一点之后不再出首进入(它说的是「地图已经建好」)', () => {
  const g = F.star(3);
  g.topic.progress = { total: 4, mastered: 1, recommended: 1, percent: 25 };
  assert.equal(KP.overlayKind(g, {}), 'welcome');
  assert.equal(KP.overlayKind(g, { seen: true }), null);
});

test('prefs:读没设过的键给兜底值,写进去能按原类型读回来', () => {
  const p = KP.prefs;
  assert.equal(p.get('从没写过的键', '兜底'), '兜底');
  assert.equal(p.set('布尔', true), true);
  assert.equal(p.get('布尔', false), true);
  assert.equal(p.set('数字', 3), true);
  assert.equal(p.get('数字', 0), 3);
  assert.equal(p.set('字符串', '甲'), true);
  assert.equal(p.get('字符串', ''), '甲');
});

test('prefs:别的代码按「字符串」写进去的 false 也要读成布尔 false', () => {
  // 这条是 `JSON.stringify` 那个决定的理由:不记类型的话,一个「关掉的开关」在
  // 下一次打开时会自己变回开,而没人看得出为什么。
  KP.prefs.set('伪装', false);
  assert.equal(KP.prefs.get('伪装', true), false);
});

test('prefs:localStorage 抛异常时读给兜底、写返回 false,绝不往外抛', async () => {
  // Safari 无痕模式的 `setItem` 会抛 `QuotaExceededError`,一些企业策略下连读都抛。
  // 让一次隐私模式的浏览把整页炸掉,是「为一点点便利把主要功能搭进去」。
  const boom = {
    getItem() { throw new Error('storage 被禁用了'); },
    setItem() { throw new Error('storage 被禁用了'); },
    removeItem() { throw new Error('storage 被禁用了'); },
  };
  const m = await loadKP({ globals: { localStorage: boom } });
  assert.equal(m.KP.prefs.get('任意', '兜底'), '兜底');
  assert.equal(m.KP.prefs.set('任意', 1), false);
  assert.equal(m.KP.prefs.remove('任意'), false);
});

test('researchOpts:设置页存的轮次上限会进请求参数,没设过就一个都不带', () => {
  assert.deepEqual(plain(KP.researchOpts()), {}, '没设过的时候不该凭空多一个字段');
  KP.prefs.set('research.maxToolRounds', 3);
  assert.deepEqual(plain(KP.researchOpts()), { max_tool_rounds: 3 });
  // 0 / 负数 / 脏值 = 没设过(设置页的「用服务端配置」就是这个),整个不发 ——
  // 发一个 0 过去会被后端 `clamp_rounds` 钳成 1,变成「只跑一轮」这个没选过的语义。
  for (const bad of [0, -1, 'abc']) {
    KP.prefs.set('research.maxToolRounds', bad);
    assert.deepEqual(plain(KP.researchOpts()), {}, `${bad} 不该变成请求参数`);
  }
  KP.prefs.remove('research.maxToolRounds');
});

test('researchOpts:读的是**当下**的偏好,不是启动时快照的那一份', () => {
  // 「改完设置刷新一次就丢」是这一条要挡的形态:偏好如果在启动时读一次塞进
  // `S.research.opts`,用户唯一能给出的解释是「这个开关没用」。
  KP.prefs.remove('research.maxToolRounds');
  assert.deepEqual(plain(KP.researchOpts()), {});
  KP.prefs.set('research.maxToolRounds', 2);
  assert.deepEqual(plain(KP.researchOpts()), { max_tool_rounds: 2 });
  KP.prefs.remove('research.maxToolRounds');
});
