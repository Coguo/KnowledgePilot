/**
 * `js/chat.js` —— 对话帧的 reducer。
 *
 * 这一组盯的是两个**丢了页面也照常工作、只是静默变差**的行为:
 *
 *   R2 `done.content` 兜底 —— 非流式 provider 一个 token 帧都不发,正文只在
 *      `done` 里。少了兜底,气泡从头到尾空白,用户以为模型挂了。
 *   R5 流式中途切节点 —— 在途 token 落到新节点的气泡上,而服务端已经把它存进
 *      **旧**节点了;本地与服务端不一致,刷新才「诈尸」。
 *
 * 加上「先渲染后转义」那条(R4):`bubblesHTML` 直接吃 LLM 输出,是 XSS 的最前线。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { loadKP, plain, FakeElement } from './harness.mjs';

const { KP } = await loadKP();
const chat = KP.chat;

/** 造一个会话:末条是空的助手消息(视图在 `send()` 里就是这么建的)。 */
function session(nodeId = 'n1') {
  const messages = [
    { role: 'user', content: '问' },
    { role: 'assistant', content: '' },
  ];
  return chat.createSession(nodeId, messages);
}

const feed = (sess, ...frames) => frames.map((f) => chat.applyFrame(sess, f));

// ---- token 累加与绘制 --------------------------------------------------------

test('token 帧累加到 acc,只回「要重绘」而不动消息', () => {
  const s = session();
  const intents = feed(s, { type: 'token', content: '甲' }, { type: 'token', content: '乙' });
  assert.deepEqual(plain(intents), [{ paint: true }, { paint: true }]);
  assert.equal(s.acc, '甲乙');
  assert.equal(s.messages[1].content, '', '流式期间不该往消息上写 —— 半截内容会让 Markdown 抖动');
});

test('token 帧缺 content 不炸(空串而不是 undefined 串)', () => {
  const s = session();
  chat.applyFrame(s, { type: 'token' });
  assert.equal(s.acc, '');
});

test('未知帧类型被忽略,且不改变任何状态', () => {
  const s = session();
  assert.deepEqual(plain(chat.applyFrame(s, { type: 'status', message: 'x' })), {});
  assert.deepEqual(plain(chat.applyFrame(s, {})), {});
  assert.deepEqual(plain(chat.applyFrame(s, null)), {});
  assert.deepEqual(plain(chat.applyFrame(null, { type: 'token' })), {});
  assert.equal(s.acc, '');
});

// ---- done:兜底是最要紧的一条(R2) -------------------------------------------

test('done 帧:流式过就把流出来的内容落盘', () => {
  const s = session();
  feed(s, { type: 'token', content: '甲' });
  const [intent] = feed(s, { type: 'done', content: '甲' });
  assert.deepEqual(plain(intent), { rerender: true, done: true });
  assert.equal(s.messages[1].content, '甲');
  assert.equal(s.open, false);
});

test('done 帧:非流式 provider 只发 done —— acc 为空时必须用 evt.content(R2)', () => {
  // 这条是「气泡永远空白」的唯一防线。`stream_capable` 为假时后端一个 token
  // 都不发,正文只在 done 里;少了 `|| evt.content` 就什么都不显示。
  const s = session();
  const [intent] = feed(s, { type: 'done', content: '完整回答' });
  assert.equal(intent.rerender, true);
  assert.equal(s.messages[1].content, '完整回答', '非流式路径的正文被丢了');
});

test('done 帧:acc 非空时以 acc 为准(不重复追加 done 里的全文)', () => {
  const s = session();
  feed(s, { type: 'token', content: '流式版' });
  feed(s, { type: 'done', content: '流式版' });
  assert.equal(s.messages[1].content, '流式版');
});

test('done 帧:两者都空 → 空串,不是 undefined', () => {
  const s = session();
  feed(s, { type: 'done' });
  assert.equal(s.messages[1].content, '');
});

// ---- recommend:先落正文再重绘 ----------------------------------------------

test('recommend 帧:先把已流出的正文落到消息上,再要求重绘(R3 的前提)', () => {
  // 重绘会重建气泡节点,`sess.tail` 因此重新指派。如果这里不先落正文,
  // 重绘时读的还是消息上的空串 —— 已经流出来的内容当场消失。
  const s = session();
  feed(s, { type: 'token', content: '讲清楚了' });
  const [intent] = feed(s, { type: 'recommend', reason: '覆盖了三要素', confidence: 0.8 });
  assert.equal(s.messages[1].content, '讲清楚了');
  assert.equal(intent.rerender, true);
  assert.equal(intent.node.status, 'recommended');
  assert.equal(intent.node.recommend_reason, '覆盖了三要素');
  assert.equal(intent.node.confidence, 0.8);
  assert.equal(s.open, true, 'recommend 之后还要继续收 token,不能算结束');
});

test('recommend 帧:缺字段时给安全默认值,不把 undefined 写进节点', () => {
  const s = session();
  const [intent] = feed(s, { type: 'recommend' });
  assert.equal(intent.node.recommend_reason, '');
  assert.equal(intent.node.confidence, 0);
});

// ---- error / failSession ----------------------------------------------------

test('error 帧:写进气泡并结束会话,前缀是 WARN', () => {
  const s = session();
  const [intent] = feed(s, { type: 'error', message: '上游超时' });
  assert.equal(s.messages[1].content, chat.WARN + '上游超时');
  assert.equal(intent.done, true);
  assert.equal(s.open, false);
});

test('failSession:网络层异常也走同一条收尾(内容不为空、会话关闭)', () => {
  const s = session();
  feed(s, { type: 'token', content: '半截' });
  chat.failSession(s, '连接中断');
  assert.equal(s.messages[1].content, chat.WARN + '连接中断', '半截内容应当被错误替换掉,而不是留着');
  assert.equal(s.open, false);
});

test('failSession:没有助手消息时不抛', () => {
  const s = chat.createSession('n1', [{ role: 'user', content: '只有我问' }]);
  assert.doesNotThrow(() => chat.failSession(s, 'x'));
});

// ---- 流式中途切节点(R5):守卫在视图层,这里钉住它依赖的判据 ---------------

test('会话记着 nodeId —— 视图靠它丢弃「属于上一轮」的迟到帧(R5)', () => {
  const s = session('node-A');
  // 视图里的守卫是 `if (!session || session.nodeId !== nodeId) return;`。
  // 这里钉住的是它读得到的那个字段确实存在且就是本轮节点。
  assert.equal(s.nodeId, 'node-A');
  const other = session('node-B');
  assert.notEqual(s.nodeId, other.nodeId);
});

test('切节点后旧会话的帧不会碰到新会话的消息(R5)', () => {
  // 模拟视图的守卫:旧会话仍在流,但当前会话已经换成 B。
  const oldSess = session('A');
  const newSess = session('B');
  let current = newSess;
  const onFrame = (forNode, evt) => {
    if (!current || current.nodeId !== forNode) return;   // ← 视图里那一行
    chat.applyFrame(current, evt);
  };
  onFrame('A', { type: 'token', content: '迟到的 A 的正文' });
  assert.equal(newSess.acc, '', '旧节点的 token 落到了新节点上');
  assert.equal(newSess.messages[1].content, '');
  void oldSess;
});

// ---- paint / tailBubble(R3) -------------------------------------------------

test('paint:只写 tail 的 textContent,不渲染 Markdown', () => {
  const s = session();
  s.tail = new FakeElement('div');
  feed(s, { type: 'token', content: '**还没写完' });
  chat.paint(s);
  assert.equal(s.tail.textContent, '**还没写完', '流式期间渲染 Markdown 会让页面抖动');
  assert.equal(s.tail.innerHTML, '', '不该往 innerHTML 里写');
});

test('paint:tail 为 null(末条是用户消息)时安静跳过', () => {
  const s = session();
  s.tail = null;
  feed(s, { type: 'token', content: 'x' });
  assert.doesNotThrow(() => chat.paint(s));
  assert.doesNotThrow(() => chat.paint(null));
});

test('tailBubble:末条是助手消息时返回那个气泡', () => {
  const box = new FakeElement('div');
  box.innerHTML = '<div class="msg assistant"><div class="bubble md"></div></div>';
  const msgs = [{ role: 'user', content: '问' }, { role: 'assistant', content: '' }];
  const tail = chat.tailBubble(box, msgs);
  assert.ok(tail, '没找到尾部气泡 —— token 会无处可写');
  assert.equal(tail.getAttribute('class'), 'bubble md');
});

test('tailBubble:末条是用户消息时返回 null(还没轮到助手说话)', () => {
  const box = new FakeElement('div');
  box.innerHTML = '<div class="msg user"><div class="bubble md"></div></div>';
  assert.equal(chat.tailBubble(box, [{ role: 'user', content: '问' }]), null);
});

test('tailBubble:空盒子 / 空列表不抛', () => {
  assert.equal(chat.tailBubble(new FakeElement('div'), []), null);
  assert.equal(chat.tailBubble(null, []), null);
});

// ---- bubblesHTML(R4:XSS 最前线) ---------------------------------------------

test('bubblesHTML:LLM 输出里的标签被转义,不产生真实元素(R4)', () => {
  const html = chat.bubblesHTML([{ role: 'assistant', content: '<img src=x onerror=alert(1)>' }]);
  assert.ok(!html.includes('<img'), `未转义:${html}`);
  assert.ok(html.includes('&lt;img'));
});

test('bubblesHTML:user 与 assistant 的角色类名正确', () => {
  const html = chat.bubblesHTML([
    { role: 'user', content: '甲' },
    { role: 'assistant', content: '乙' },
  ]);
  assert.ok(html.includes('class="msg user"'));
  assert.ok(html.includes('class="msg assistant"'));
  assert.ok(html.includes('我') && html.includes('学习伙伴'));
});

test('bubblesHTML:空列表给提示语,不是空白', () => {
  assert.ok(chat.bubblesHTML([]).includes('还没有对话'));
  assert.ok(chat.bubblesHTML(null).includes('还没有对话'));
});

test('bubblesHTML:空态提示可覆盖 —— 默认那句承诺的是节点对话才有的行为', () => {
  // 默认提示写的是「讲透之后系统会建议你点亮它」。「对话」页是一次性研究问答,
  // 不建图谱、不点亮 —— 把默认那句摆上去,等于在一个不会发生的地方写承诺。
  const html = chat.bubblesHTML([], '<div class="hint">这一页不建图谱</div>');
  assert.ok(html.includes('这一页不建图谱'));
  assert.ok(!html.includes('建议你点亮'));
});

// ---- line():过程类帧 → 一行日志 ----------------------------------------------
//
// 这张表原来有**两份**(`views/graph.js` 的生成日志一份、对话页的过程日志一份)。
// 两份一定会分叉,而分叉的表现只是「同一件事在这个页面说得多、在那个页面说得少」——
// 没有报错、没有任何东西会红。所以下面每条都把文案一起钉住:改文案可以,但必须
// 一次改掉两份调用方共用的这一处,也必须顺手改这条测试。

/** 取出 `line()` 的文案(顺带断言它确实产出了行)。 */
function textOf(evt) {
  const ln = chat.line(evt);
  assert.ok(ln, `${JSON.stringify(evt)} 没有产出行`);
  return ln.text;
}

test('line:plan 把每步的问题用箭头串起来', () => {
  const t = textOf({ type: 'plan', plan: [{ question: '是什么' }, { question: '怎么用' }] });
  assert.ok(t.includes('研究计划'));
  assert.ok(t.includes('是什么 → 怎么用'), t);
});

test('line:plan 兼容 goal 字段,并丢掉空步骤(不留「 → 」孤零零挂着)', () => {
  const t = textOf({ type: 'plan', plan: [{ goal: '甲' }, null, { question: '' }, { question: '乙' }] });
  assert.ok(t.includes('甲 → 乙'), t);
  assert.ok(!t.includes('→  →'), t);
  assert.ok(!t.includes('null'), t);
});

test('line:plan 缺 plan 字段时不吐出 undefined', () => {
  assert.ok(!textOf({ type: 'plan' }).includes('undefined'));
  assert.ok(!textOf({ type: 'plan', plan: null }).includes('undefined'));
});

test('line:eval 按 sufficient 分流,并把 iteration 兜成数字', () => {
  assert.ok(textOf({ type: 'eval', sufficient: true, iteration: 2, reason: '够了' })
    .includes('第 2 轮评估：✅ 信息已充分 — 够了'));
  assert.ok(textOf({ type: 'eval', sufficient: false, iteration: 1, reason: '还差' })
    .includes('🔁 继续研究'));
  // 后端一定给全字段,但这条函数也会被喂前端拼的帧 ——
  // 「已生成 undefined 个知识点」是那种会被截图发出来的 bug。
  const t = textOf({ type: 'eval' });
  assert.ok(t.includes('第 0 轮评估'), t);
  assert.ok(!t.includes('undefined'), t);
});

test('line:数字字段缺失/NaN 一律兜成 0,绝不出现 undefined/NaN', () => {
  const cases = [
    { type: 'memory' },
    { type: 'memory', found: 'x' },
    { type: 'kg' },
    { type: 'kg', entities: null, relations: undefined },
    { type: 'graph_ready' },
    { type: 'graph_ready', nodes: 'NaN', edges: null },
  ];
  for (const evt of cases) {
    const t = textOf(evt);
    assert.ok(!/undefined|NaN/.test(t), `${JSON.stringify(evt)} 产出了 ${t}`);
  }
});

test('line:graph_ready 只在降级时补那句说明', () => {
  const ok = textOf({ type: 'graph_ready', nodes: 12, edges: 11 });
  assert.ok(ok.includes('12 个知识点') && ok.includes('11 条前置关系'));
  assert.ok(!ok.includes('线性路径'), '正常路径不该说降级的话');
  const bad = textOf({ type: 'graph_ready', nodes: 12, edges: 11, degraded: true });
  assert.ok(bad.includes('线性路径'), '降级了却没说 —— 用户只会觉得图长得奇怪');
});

test('line:检索过程的两种帧各自成行', () => {
  assert.ok(textOf({ type: 'tool_call', arguments: 'rag chunking' }).includes('正在检索：rag chunking'));
  assert.ok(textOf({ type: 'tool_result', summary: '3 条' }).includes('已获取结果：3 条'));
  assert.ok(textOf({ type: 'status', message: '正在抽取知识点' }).includes('正在抽取知识点'));
});

test('line:error 带 WARN 前缀且 cls 为 err(那一行要变红)', () => {
  const ln = chat.line({ type: 'error', message: '上游超时' });
  assert.equal(ln.cls, 'err');
  assert.equal(ln.text, chat.WARN + '上游超时');
});

test('line:error 之外的行 cls 一律为空 —— 除了错误,日志里没有第二种颜色', () => {
  const types = ['plan', 'status', 'eval', 'memory', 'kg', 'tool_call', 'tool_result', 'graph_ready'];
  for (const type of types) {
    assert.equal(chat.line({ type }).cls, '', `${type} 的 cls 不是空串`);
  }
});

test('line:token/done/recommend 不产日志行 —— 否则每个 token 都会刷一行', () => {
  // 这是本组最要紧的一条:`token` 是**消息**类帧,不是**过程**类帧。它一旦漏进这张表,
  // 一次回答就会往日志里塞几百行,把真正有用的「正在检索…」冲得看不见。
  for (const evt of [{ type: 'token', content: '甲' }, { type: 'done' },
                     { type: 'recommend', reason: 'x' }, {}, null, { type: '没见过的帧' }]) {
    assert.equal(chat.line(evt), null, `${JSON.stringify(evt)} 不该产出行`);
  }
});

test('line:是纯函数 —— 同一帧调两次结果相同,且不碰会话', () => {
  const evt = { type: 'kg', entities: 3, relations: 2 };
  assert.deepEqual(plain(chat.line(evt)), plain(chat.line(evt)));
  // 它**不接收** session,所以「一帧既改消息又产日志」这件事不可能被它偷偷做掉。
  // `error` 是最典型的一帧:applyFrame 把它写进气泡,line 把它留成一行日志,两件事。
  const s = session();
  chat.applyFrame(s, { type: 'error', message: '炸了' });
  assert.equal(s.messages[1].content, chat.WARN + '炸了');
  assert.equal(chat.line({ type: 'error', message: '炸了' }).text, chat.WARN + '炸了');
});
