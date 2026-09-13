/**
 * `views/chat.js` —— 「对话」页(`#/chat`,对 `POST /api/chat` 的一次性研究问答)。
 *
 * 它与浮动助手**是两件事**,所以分开测:助手问的是某个知识点、答案会落进那个节点的
 * 学习记录;这一页问的是任何问题,跑完一轮完整的 research agent,答完就没了。
 *
 * 这一组盯四件事:
 *   1. 它**不假装有历史** —— 空态那句话必须说清楚「不建图谱、不保存历史」,
 *      而不是复用节点对话那句「讲透之后系统会建议你点亮它」(承诺一件不会发生的事)。
 *   2. 过程日志来自共用的 `KP.chat.line`,而且**每轮清零** —— 累积整场会把正文淹掉。
 *   3. 流式:token 进尾部气泡、done 之后整块重绘、输入框重新可用。
 *   4. 同一时刻只允许一轮在流。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { loadKP, makeShellElements, plain } from './harness.mjs';

const enc = new TextEncoder();
const frame = (obj) => `data: ${JSON.stringify(obj)}\n\n`;
const tick = () => new Promise((r) => setTimeout(r, 0));

/** 一条手动驱动的 SSE 流,时机由用例决定。 */
function controllable() {
  let ctrl;
  const stream = new ReadableStream({ start(c) { ctrl = c; } });
  return {
    stream,
    push(evt) { ctrl.enqueue(enc.encode(frame(evt))); },
    close() { ctrl.close(); },
  };
}

// ================= 纯字符串层 =================

const bare = await loadKP();
const C = bare.KP.views.chat;

test('pageHTML:带着输入框与说明,不是一个空壳', () => {
  const html = C.pageHTML();
  assert.ok(html.includes('id="r-form"') && html.includes('id="r-input"'));
  assert.ok(html.includes('id="r-go"'));
  assert.ok(html.includes('对话'), html);
});

test('pageHTML:开宗明义地说明「不建图谱、不保存历史」', () => {
  // 这是这一页唯一诚实的做法:后端那个端点没有历史 GET,假装有历史、结果刷新后
  // 空了,比一开始就说明白更糟。文案写在这里,测试把它钉住。
  const html = C.pageHTML();
  assert.ok(html.includes('不建图谱'), html);
  assert.ok(html.includes('不保存历史'), html);
});

test('bodyHTML:空态用的是这一页的提示,不是节点对话那句', () => {
  // 默认那句承诺「讲透之后系统会建议你点亮它」—— 在这一页不会发生。
  const html = C.bodyHTML([]);
  assert.ok(!html.includes('建议你点亮'), html);
  assert.ok(html.includes('不会建图谱'), html);
  assert.ok(html.includes('不保存历史'), html);
  assert.ok(html.includes('id="r-log"') && html.includes('id="r-chat"'));
});

test('bodyHTML:有消息时不再显示空态提示', () => {
  const html = C.bodyHTML([{ role: 'user', content: '问一句' }]);
  assert.ok(!html.includes('不建图谱'));
  assert.ok(html.includes('问一句'));
});

// ================= 落到 DOM 的那一层 =================

async function setup(routes = {}) {
  const m = await loadKP({ elements: makeShellElements(), routes });
  m.KP.views.shell.init();
  m.KP.views.chat.render();
  return m;
}

/** 在页面上填一个问题并提交。 */
function ask(m, text) {
  m.KP.dom.q(m.elements.stage, 'r-input').value = text;
  m.KP.dom.q(m.elements.stage, 'r-form').fire('submit', { preventDefault() {} });
}

const logOf = (m) => m.KP.dom.q(m.elements.stage, 'r-log');

test('render:把整页挂进 #stage,并且是幂等的', async () => {
  const m = await setup();
  assert.ok(m.KP.dom.q(m.elements.stage, 'r-input'), '输入框没挂上');
  m.KP.views.chat.render();
  assert.equal(m.KP.dom.qa(m.elements.stage, '.r-composer').length, 1, '重绘把页面叠了两份');
});

test('render:切走再切回来时草稿还在', async () => {
  const m = await setup();
  m.KP.dom.q(m.elements.stage, 'r-input').value = '打到一半的问题';
  m.KP.views.chat.render();
  assert.equal(m.KP.dom.q(m.elements.stage, 'r-input').value, '打到一半的问题');
});

test('send:token 进气泡、tool_call 进日志、done 之后整块重绘', async () => {
  const cs = controllable();
  const m = await setup({ '/api/chat': { body: cs.stream } });
  ask(m, 'RAG 的分块策略有哪些?');
  await tick();

  assert.equal(m.KP.S.research.busy, true, '忙标志没立起来');
  assert.equal(m.KP.dom.q(m.elements.stage, 'r-input').disabled, true, '流式期间输入框还能打字');

  cs.push({ type: 'tool_call', arguments: 'rag chunking' });
  await tick();
  assert.ok(logOf(m).innerHTML.includes('正在检索：rag chunking'), logOf(m).innerHTML);
  // 日志不该长进气泡里 —— 它是「它现在在干什么」,不是答案的一部分。
  const box = m.KP.dom.q(m.elements.stage, 'r-chat');
  assert.ok(!box.innerHTML.includes('正在检索'));

  cs.push({ type: 'token', content: '有固定长度、' });
  await tick();
  const bubble = box.lastElementChild.querySelector('.bubble');
  assert.equal(bubble.textContent, '有固定长度、');
  assert.equal(bubble.innerHTML, '', '流式期间渲染 Markdown 会让页面抖');

  cs.push({ type: 'token', content: '语义、递归三种。' });
  await tick();
  // `done.content` 是后端把同一份正文再发一遍(非流式 provider 时才只有它)。
  cs.push({ type: 'done', content: '有固定长度、语义、递归三种。' });
  cs.close();
  await tick();
  await tick();
  assert.equal(m.KP.S.research.messages[1].content, '有固定长度、语义、递归三种。', 'done 之后正文没落到消息上');
  assert.equal(m.KP.S.research.busy, false, '忙标志没放开');
  assert.equal(m.KP.dom.q(m.elements.stage, 'r-input').disabled, false, '输入框没恢复 —— 不刷新就没法再问');
  assert.ok(m.KP.dom.q(m.elements.stage, 'r-chat').innerHTML.includes('递归三种'));
});

test('send:过程日志每轮清零 —— 累积整场会把正文淹掉', async () => {
  // 路由用函数返回**当前**那条流,好让第二轮换一条新的(桩每次请求都会重新调它)。
  let cur = null;
  const m = await setup({ '/api/chat': () => ({ body: cur.stream }) });

  const first = controllable();
  cur = first;
  ask(m, '第一轮');
  await tick();
  first.push({ type: 'status', message: '第一轮在检索' });
  await tick();
  assert.ok(logOf(m).innerHTML.includes('第一轮在检索'));
  first.push({ type: 'done', content: '答一' });
  first.close();
  await tick();
  await tick();

  // 第二轮一开始就清空,而不是等第一行新日志出现才盖掉旧的那几行 ——
  // 后者会让「正在检索…」下面还挂着上一轮的进度,读起来像同一轮里有两次检索。
  const second = controllable();
  cur = second;
  ask(m, '第二轮');
  await tick();
  assert.equal(logOf(m).innerHTML, '', '换了新一轮,上一轮的过程日志还挂着');

  second.push({ type: 'done', content: '答二' });
  second.close();
  await tick();
  await tick();
  assert.equal(m.KP.S.research.messages.length, 4, '两轮应当各有问与答');
});

test('send:已经有一轮在流时,第二次提交不发出请求', async () => {
  const cs = controllable();
  const m = await setup({ '/api/chat': { body: cs.stream } });
  ask(m, '第一问');
  await tick();
  const n = m.fetch.calls.length;
  assert.equal(n, 1);
  ask(m, '第二问');
  await tick();
  assert.equal(m.fetch.calls.length, n, '两轮研究问答同时在跑 —— 两个流会把正文写进同一个气泡');
  cs.push({ type: 'done', content: '答' });
  cs.close();
  await tick();
  await tick();
  assert.equal(m.KP.S.research.busy, false);
});

test('send:空输入不发请求', async () => {
  const m = await setup({ '/api/chat': { body: controllable().stream } });
  ask(m, '   ');
  await tick();
  assert.equal(m.fetch.calls.length, 0);
});

test('send:网络层失败把错误写进气泡,忙标志仍要放开', async () => {
  const m = await setup({ '/api/chat': { ok: false, status: 500, json: { detail: '上游炸了' } } });
  ask(m, '会失败的问题');
  await tick();
  await tick();
  assert.ok(m.KP.S.research.messages[1].content.includes('上游炸了'), m.KP.S.research.messages[1].content);
  assert.equal(m.KP.S.research.busy, false, '忙标志没放开 —— 输入框会永远禁用');
  assert.equal(m.KP.dom.q(m.elements.stage, 'r-input').disabled, false);
});

test('send:过程日志里的 LLM 输出被转义(日志也是一条注入路径)', async () => {
  // `tool_call.arguments` 是模型自己写的一行检索式 —— 它会原样进 innerHTML。
  const cs = controllable();
  const m = await setup({ '/api/chat': { body: cs.stream } });
  ask(m, '问');
  await tick();
  cs.push({ type: 'tool_call', arguments: '<img src=x onerror=alert(1)>' });
  await tick();
  assert.ok(!logOf(m).innerHTML.includes('<img'), logOf(m).innerHTML);
  assert.ok(logOf(m).innerHTML.includes('&lt;img'));
  cs.push({ type: 'done', content: '答' });
  cs.close();
  await tick();
  await tick();
});

test('页面状态形状:不持久化,也没有「假装有历史」的字段', async () => {
  const m = await setup();
  assert.deepEqual(plain(m.KP.S.research.messages), []);
  assert.ok(!('history' in m.KP.S.research) && !('storage' in m.KP.S.research));
});

test('轮次上限:设置页写下的值真的进了请求体;没设过时整个字段都不发', async () => {
  // 设置页做的唯一一件「真的会打到后端」的事就是这个 —— 它写 `KP.prefs`,请求读
  // `KP.researchOpts()`。两端各测一半的话,中间那条线断了谁都不知道:设置页看起来
  // 记住了,而请求里一个字节都没变。
  const one = controllable();
  const two = controllable();
  let cur = one.stream;
  const m = await setup({ '/api/chat': () => ({ body: cur }) });

  m.KP.prefs.set('research.maxToolRounds', 3);
  ask(m, '问一句');
  await tick();
  const a = JSON.parse(m.fetch.calls[0].init.body);
  assert.equal(a.max_tool_rounds, 3, '设置页设的轮次上限没有跟着请求出门');
  assert.equal(a.message, '问一句');
  one.push({ type: 'done', content: '答一' });
  one.close();
  await tick();
  await tick();

  // 换回「用服务端配置」:字段**整个不发**,而不是发一个 0 —— 后端的 `clamp_rounds`
  // 会把 0 钳到 1,那是「最少跑一轮」,与「让服务端按 .env 决定」是两件不同的事。
  m.KP.prefs.remove('research.maxToolRounds');
  cur = two.stream;
  ask(m, '再问一句');
  await tick();
  const b = JSON.parse(m.fetch.calls[m.fetch.calls.length - 1].init.body);
  assert.equal('max_tool_rounds' in b, false, `没设过却发了轮次参数:${JSON.stringify(b)}`);
  assert.equal(b.message, '再问一句');
  two.push({ type: 'done', content: '答二' });
  two.close();
  await tick();
  await tick();

  // 而它读的是**发送的那一刻**的偏好,不是启动时的快照 —— 快照的表现是「改完设置、
  // 刷新一下,结果它又被忽略了」,而那与「这个开关根本没用」在用户眼里没有区别。
  m.KP.prefs.set('research.maxToolRounds', 6);
  assert.equal(m.KP.researchOpts().max_tool_rounds, 6, '改了偏好但组装出来的参数没变');
});
