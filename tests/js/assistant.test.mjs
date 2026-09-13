/**
 * `views/assistant.js` —— 左下角浮动助手,**全前端唯一的对话 UI**。
 *
 * 这一组盯三类东西:
 *
 *   1. **字符串契约**(纯函数层):胶囊上的节点名、上下文那两个真实数字、面板的禁用态。
 *      没有它们,助手在「没有选中节点」时会看起来能打字,而按下去什么也不发生。
 *   2. **历史的拉取时机**:收起时不拉(那是一次白发的 GET),展开时拉、且只拉一次。
 *      这条是「点了节点发现输入框里是别人对话」与「每帧重绘都发一次 GET」的分界。
 *   3. **流式**:token 逐字进尾部气泡、「重新研究」重绘后**仍然**进新气泡(R3)、
 *      中途切节点后旧帧被丢弃(R5)、`done` 之后整块重绘成 Markdown。
 *
 * 桩不能证明「好不好看」,但能证明「字有没有浪费在脱离文档的元素上」——
 * 后者正是没有浏览器时最难发现的一类。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { loadKP, makeShellElements, plain } from './harness.mjs';
import * as F from './fixtures/graphs.mjs';

const enc = new TextEncoder();
const frame = (obj) => `data: ${JSON.stringify(obj)}\n\n`;
const tick = () => new Promise((r) => setTimeout(r, 0));

/** 一条**手动驱动**的 SSE 流。`push` / `close` 由用例决定时机。 */
function controllable() {
  let ctrl;
  const stream = new ReadableStream({ start(c) { ctrl = c; } });
  return {
    stream,
    push(evt) { ctrl.enqueue(enc.encode(frame(evt))); },
    close() { ctrl.close(); },
  };
}

/** 助手面板里的尾部气泡。**每次都要重新查** —— 重绘后旧的那个已经脱离文档。 */
function tailBubbleOf(m) {
  const box = m.KP.dom.q(m.elements.assistant, 'a-chat');
  if (!box || !box.lastElementChild) return null;
  return box.lastElementChild.querySelector('.bubble');
}

/** 装好外壳并让助手可以渲染。`node` / `nodeId` 由用例自己设 —— 那是 Inspector 的活。 */
async function setup(routes = {}) {
  const m = await loadKP({ elements: makeShellElements(), routes });
  m.KP.views.shell.init();
  return m;
}

/**
 * 把节点放到「已经选中、助手已经展开」的状态 —— 即用户点了节点的样子。
 *
 * 直接调 `render()` 而不走 `openFor`:**不触发历史 GET**,于是每条例外都只需要
 * 配自己关心的那条路由。要测历史拉取本身就用 `openFor`(见下面那组)。
 */
function openOn(m, node) {
  m.KP.S.node = node;
  m.KP.S.nodeId = node.id;
  m.KP.S.assistant.open = true;
  m.KP.views.assistant.render();
}

// ================= 纯字符串层 =================

const bare = await loadKP();
const A = bare.KP.views.assistant;

test('launcherHTML:带上节点名 —— 「继续学 RAG」比一个光秃秃的 ✨ 有用得多', () => {
  const html = A.launcherHTML(F.node({ name: '向量检索' }));
  assert.ok(html.includes('向量检索'), html);
  assert.ok(html.includes('as-launch'));
  assert.ok(html.includes('type="button"'), '默认是 submit 的话,它会把外面的表单一起提交');
});

test('launcherHTML:带上「展开」这个动作词 —— 光一个节点名会被读成标签', () => {
  // 走查反馈 ① 的后半句:「关闭后没有办法再次打开」。上一版收起态是一颗漂在画布
  // 左下角、只写着节点名的小胶囊 —— 它看起来像个装饰性标签,不是一个能点的入口,
  // 于是用户收起一次就找不到回来的路。判据就是这个动作词。
  const html = A.launcherHTML(F.node({ name: '向量检索' }));
  assert.ok(html.includes('展开'), `收起态没有「展开」二字,只有个名字:${html}`);
  assert.ok(html.includes('as-open'), '入口掉了个 class —— 上面那些样式就都落不到它身上');
  assert.ok(html.includes('title="展开 AI 助手"'), '光标悬停时也该说清它是干什么的');
});

test('launcherHTML:没有节点时是一句通用名字,不是空白也不是 undefined', () => {
  for (const node of [null, undefined, {}, { name: '' }]) {
    const html = A.launcherHTML(node);
    assert.ok(html.includes('AI 助手'), `${JSON.stringify(node)} 产出了 ${html}`);
    assert.ok(!/undefined/.test(html));
  }
});

test('launcherHTML:节点名被转义(它来自 LLM 抽取,不是可信文本)', () => {
  const html = A.launcherHTML(F.node({ name: F.XSS }));
  assert.ok(!html.includes('<img'), html);
});

test('ctxHTML:节点 + 主题进度 → 「正在学习」加真实的已点亮数', () => {
  const html = A.ctxHTML(F.node({ name: 'RAG' }), F.topic({ total: 8, mastered: 3 }));
  assert.ok(html.includes('当前正在学习'), html);
  assert.ok(html.includes('RAG'));
  assert.ok(html.includes('<b>3 / 8</b>'), html);
});

test('ctxHTML:说的是个数,不是百分比 —— 百分比那条路一走就会差 1%', () => {
  // 后端 `round(mastered*100/total)` 是银行家舍入(1/8 → Python 给 12),
  // JS 的 Math.round(12.5) 给 13。助手头上这个数字一旦自算,就会与侧栏、中心环
  // 差 1% —— 而它正是设计稿最强调的那个数。夹具里特意放一份 `percent`,
  // 断言的是产物里**没有**它。
  const topic = F.topic({ total: 8, mastered: 3 });
  topic.progress = { total: 8, mastered: 3, recommended: 0, unlearned: 5, percent: 38 };
  const html = A.ctxHTML(F.node({ name: 'RAG' }), topic);
  assert.ok(html.includes('3 / 8'), html);
  assert.ok(!html.includes('38'), '把 percent 掺进这句话了');
});

test('ctxHTML:没有节点时不留「已点亮 m / n」—— 那句会更抢眼,却没指谁', () => {
  const html = A.ctxHTML(null, F.topic({ total: 8, mastered: 3 }));
  assert.ok(html.includes('还没有选中知识点'));
  assert.ok(!html.includes('已点亮'), html);
});

test('ctxHTML:主题没进度 / total 为 0 时不画那句 —— 「已点亮 0 / 0」是句废话', () => {
  for (const topic of [null, {}, F.topic({ total: 0, mastered: 0 })]) {
    const html = A.ctxHTML(F.node({ name: 'RAG' }), topic);
    assert.ok(!html.includes('已点亮'), `${JSON.stringify(topic)} 还是画了`);
    assert.ok(!html.includes('undefined') && !html.includes('NaN'), html);
  }
});

test('ctxHTML:节点名与都不是的字段被转义', () => {
  const html = A.ctxHTML(F.node({ name: F.XSS }), F.topic({ total: 1, mastered: 0 }));
  assert.ok(!html.includes('<img'), html);
});

test('panelHTML:有节点时可输入、无节点时整块禁用', () => {
  const on = A.panelHTML(F.node({ name: 'RAG' }), [], null, '');
  assert.ok(on.includes('就这个知识点提问…'), on);
  assert.ok(!on.includes('disabled'), '有节点时不该有禁用的东西');
  const off = A.panelHTML(null, [], null, '');
  assert.ok(off.includes('先在图谱里点一个知识点'), off);
  // 两个 `disabled` —— 输入框一个、发送按钮一个。少一个就会出现「能打字但发不出去」
  // 或「按钮亮着但点了没反应」。
  assert.equal((off.match(/disabled/g) || []).length, 2, off);
});

test('panelHTML:历史拉取失败的那行说明会显示且被转义;成功时整行不留', () => {
  const withErr = A.panelHTML(F.node({ name: 'RAG' }), [], null, '<img src=x>');
  assert.ok(withErr.includes('as-err'));
  assert.ok(!withErr.includes('<img'), withErr);
  assert.ok(!A.panelHTML(F.node({ name: 'RAG' }), [], null, '').includes('as-err'));
});

test('panelHTML:没有消息时是「还没有对话」,不是一片空白', () => {
  assert.ok(A.panelHTML(F.node({ name: 'RAG' }), [], null, '').includes('还没有对话'));
});

test('render:没有挂载点时安静返回,不抛', () => {
  // `loadKP()` 不传 elements 时 `#assistant` 取到 null。真实页面里这对应
  // 「index.html 漏了挂载点」—— 那时该是「助手不出现」,不是一个白屏。
  assert.doesNotThrow(() => A.render());
  assert.doesNotThrow(() => A.renderChat());
});

// ================= 落到 DOM 的那一层 =================

test('render:收起时是胶囊,点一下展开成面板,再点「收起」回去', async () => {
  const m = await setup();
  const S = m.KP.S;
  S.node = F.node({ name: 'RAG' });
  S.assistant.open = false;
  m.KP.views.assistant.render();

  assert.ok(m.elements.assistant.innerHTML.includes('as-launch'));
  const launch = m.KP.dom.q(m.elements.assistant, 'a-launch');
  launch.fire('click');
  assert.equal(S.assistant.open, true);
  assert.ok(m.elements.assistant.innerHTML.includes('as-composer'));

  m.KP.dom.q(m.elements.assistant, 'a-min').fire('click');
  assert.equal(S.assistant.open, false);
  assert.ok(m.elements.assistant.innerHTML.includes('as-launch'), '收起后应当只剩胶囊');
});

test('render:在「对话」页上整块清空 —— 那里自带一个完整输入框,再叠一个是纯混乱', async () => {
  const m = await setup();
  m.KP.S.assistant.open = true;
  m.KP.S.node = F.node({ name: 'RAG' });
  m.KP.S.route = { view: 'chat' };
  m.KP.views.assistant.render();
  assert.equal(m.elements.assistant.innerHTML, '');
  // 切回图谱页要能重新出现(不是被永久清掉了)。
  m.KP.S.route = { view: 'graph' };
  m.KP.views.assistant.render();
  assert.ok(m.elements.assistant.innerHTML.includes('as-composer'));
});

test('render:在「对话」页上要连容器一起藏 —— 清空 innerHTML 不够', async () => {
  // 停靠之后 `#assistant` 自己带一条上边框和底色(css/assistant.css):只清空
  // innerHTML 会在对话页底部留一条无来由的横线。判据是 `hidden`,**不是**
  // `innerHTML === ''` —— 后者在上一版是对的,这一版不够。
  // (CSS 那边成对的 `#assistant[hidden] { display: none; }` 由
  //  `tests/test_api.py` 的回退哨盯着,因为 `display: flex` 会盖掉 `[hidden]`。)
  const m = await setup();
  m.KP.S.assistant.open = true;
  m.KP.S.node = F.node({ name: 'RAG' });
  m.KP.S.route = { view: 'chat' };
  m.KP.views.assistant.render();
  assert.equal(m.elements.assistant.hidden, true, '容器没藏 —— 对话页底部会留一条空横线');

  // 切回图谱页必须**放回来**,否则助手会被永久藏掉(那比不藏更糟)。
  m.KP.S.route = { view: 'graph' };
  m.KP.views.assistant.render();
  assert.equal(m.elements.assistant.hidden, false, '切回图谱页后没放回来 —— 助手永远消失了');

  // 收起态也一样:收起只该换成那条细栏,不该把容器也藏掉。
  m.KP.S.assistant.open = false;
  m.KP.views.assistant.render();
  assert.equal(m.elements.assistant.hidden, false);
  assert.ok(m.elements.assistant.innerHTML.includes('as-launch'));
});

test('render:打字打到一半时重绘不冲掉草稿', async () => {
  // 「点要点芯片 → 输入框被填上问题 → 历史刚好拉回来 → 填的字被冲掉」——
  // 用户只会觉得自己手滑了。
  const m = await setup();
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));
  m.KP.dom.q(m.elements.assistant, 'a-input').value = '我想问的是……';
  m.KP.views.assistant.render();
  assert.equal(m.KP.dom.q(m.elements.assistant, 'a-input').value, '我想问的是……');
});

test('setNode:收起状态下**不**拉历史 —— 用户可能只是在图上逛', async () => {
  const m = await setup({ '/messages': { json: { messages: [{ role: 'user', content: '旧的' }] } } });
  m.KP.S.assistant.open = false;
  m.KP.S.messages = [{ role: 'user', content: '上一个节点的' }];
  m.KP.views.assistant.setNode('n1');
  await tick();
  assert.equal(m.fetch.calls.length, 0, '收起时发了一次白发的 GET');
  assert.deepEqual(plain(m.KP.S.messages), [], '旧节点的对话没被清掉 —— 展开时会先闪一下别人的');
  assert.ok(m.elements.assistant.innerHTML.includes('as-launch'));
});

test('openFor:展开时把历史拉回来,而且**只拉一次**', async () => {
  // 只拉一次靠 `loadedFor`。少了它,`render()` 在一次对话里会被调很多次,
  // 每一次都是一发 GET —— 症状是「聊着聊着历史气泡闪一下」。
  let n = 0;
  const m = await setup({
    '/nodes/n1/messages': () => { n += 1; return { json: { messages: [{ role: 'user', content: '历史问题' }] } }; },
  });
  // 收起状态:用户刚在图谱上点了这个节点,还没碰助手。
  m.KP.S.node = F.node({ id: 'n1', name: 'RAG' });
  m.KP.S.nodeId = 'n1';
  m.KP.views.assistant.openFor('n1');
  await tick();
  assert.equal(m.KP.S.assistant.open, true, 'openFor 没把助手展开');
  assert.equal(n, 1, `历史拉了 ${n} 次`);
  assert.ok(m.elements.assistant.innerHTML.includes('历史问题'), '历史没进气泡');

  m.KP.views.assistant.openFor('n1');
  await tick();
  assert.equal(n, 1, '同一个节点又拉了一次');
});

test('setNode 之后再 openFor:收起时清掉的 loadedFor 会让展开重新拉一次', async () => {
  // 这两条路径的交界处:切节点(收起)→ 再点「开始学习」。`setNode` 有意不拉
  // 历史,所以它必须把 `loadedFor` 清掉 —— 不清的话 `openFor` 会以为历史已经
  // 有了,于是展开后气泡一直空着,直到刷新。
  let n = 0;
  const m = await setup({
    '/nodes/n1/messages': () => { n += 1; return { json: { messages: [{ role: 'user', content: '历史问题' }] } }; },
  });
  m.KP.S.assistant.open = false;
  m.KP.S.node = F.node({ id: 'n1', name: 'RAG' });
  m.KP.S.nodeId = 'n1';
  m.KP.views.assistant.setNode('n1');
  await tick();
  assert.equal(n, 0, '收起时不该拉');
  m.KP.views.assistant.openFor('n1');
  await tick();
  assert.equal(n, 1, '展开时没拉 —— 气泡会一直空着');
  assert.ok(m.elements.assistant.innerHTML.includes('历史问题'));
});

test('openFor:换节点一定要重拉 —— 不重拉显示的就是别人的对话', async () => {
  const seen = [];
  const m = await setup({
    '/nodes/n1/messages': () => { seen.push('n1'); return { json: { messages: [{ role: 'user', content: '一句话甲' }] } }; },
    '/nodes/n2/messages': () => { seen.push('n2'); return { json: { messages: [{ role: 'user', content: '两句话乙' }] } }; },
  });
  m.KP.S.node = F.node({ id: 'n1', name: '甲' });
  m.KP.S.nodeId = 'n1';
  m.KP.views.assistant.openFor('n1');
  await tick();
  assert.ok(m.elements.assistant.innerHTML.includes('一句话甲'));

  // 点图上另一个节点 —— Inspector.open 会改写 nodeId 再让助手跟着走。
  m.KP.S.node = F.node({ id: 'n2', name: '乙' });
  m.KP.S.nodeId = 'n2';
  m.KP.views.assistant.openFor('n2');
  await tick();
  assert.deepEqual(seen, ['n1', 'n2']);
  assert.ok(m.elements.assistant.innerHTML.includes('两句话乙'));
  assert.ok(!m.elements.assistant.innerHTML.includes('甲'), '上一个节点的对话还留在气泡里');
});

test('ensureMessages:历史拉失败时留一行说明 —— 「拉不到」与「没聊过」不能长一样', async () => {
  const m = await setup({ '/nodes/n1/messages': { ok: false, status: 500, json: { detail: '炸了' } } });
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));
  m.KP.views.assistant.openFor('n1');
  await tick();
  assert.ok(m.KP.S.assistant.err.includes('历史对话加载失败'), m.KP.S.assistant.err);
  assert.ok(m.elements.assistant.innerHTML.includes('as-err'), '错误没有显示出来');
  assert.ok(m.elements.assistant.innerHTML.includes('还没有对话'), '同时还要说明没有历史');
});

test('askAbout:预填问题并在历史回来之后仍然保留', async () => {
  // 预填必须放在 `setNode` 之后:那里面的 `ensureMessages` 是异步的,回来会再
  // `render()` 一次 —— 靠「保留草稿」把那句话带回来,两条路径都不丢字。
  const m = await setup({ '/nodes/n1/messages': { json: { messages: [{ role: 'user', content: '旧' }] } } });
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));
  m.KP.S.assistant.open = false;
  m.KP.views.assistant.askAbout('n1', '请讲讲：分块策略');
  await tick();
  assert.equal(m.KP.dom.q(m.elements.assistant, 'a-input').value, '请讲讲：分块策略');
});

// ================= 流式 =================

test('renderChat:本来就贴底时跟着新消息走 —— 上一版那句 scrollIntoView 是 no-op', async () => {
  // `scrollIntoView({block:'nearest'})` 滚的是**祖先**容器(让这个盒子进视野),
  // 而 `.as-log` 自己就是滚动容器、它的盒子一直在视野里 —— 于是正文流出可视区
  // 之后从来没人把它滚下来过。停靠到栏底之后消息区成了主角,这个坑更明显。
  const m = await setup();
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));
  const box = m.KP.dom.q(m.elements.assistant, 'a-chat');
  box.scrollHeight = 500; box.clientHeight = 300; box.scrollTop = 200;   // 恰好贴底
  m.KP.views.assistant.renderChat();
  assert.equal(box.scrollTop, 500, '贴底时新消息没跟下来 —— 用户得自己往下拖');
});

test('renderChat:用户往上翻看历史时**不许**把视图拽回底部', async () => {
  // 这是这类面板最招人烦的行为,而判据只有「他离底有多远」——桩只要摆出这个距离
  // 就够了。60px 明显超过 40px 的余量,不是边界值。
  const m = await setup();
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));
  const box = m.KP.dom.q(m.elements.assistant, 'a-chat');
  box.scrollHeight = 500; box.clientHeight = 300; box.scrollTop = 140;   // 离底 60px
  m.KP.views.assistant.renderChat();
  assert.equal(box.scrollTop, 140, '用户翻上去看历史,视图被拽回了底部');
});

test('renderChat:吸底判据有 40px 余量 —— 像素级「恰好贴底」几乎不成立', async () => {
  const m = await setup();
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));
  const box = m.KP.dom.q(m.elements.assistant, 'a-chat');
  box.scrollHeight = 500; box.clientHeight = 300; box.scrollTop = 180;   // 离底 20px
  m.KP.views.assistant.renderChat();
  assert.equal(box.scrollTop, 500, '差 20px 就不跟了 —— 每次换行都会漏跟一次');
});

test('send:token 逐字进尾部气泡,流式期间不渲染 Markdown', async () => {
  const cs = controllable();
  const m = await setup({ '/nodes/n1/chat': { body: cs.stream } });
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));

  m.KP.dom.q(m.elements.assistant, 'a-input').value = '讲讲 RAG';
  m.KP.dom.q(m.elements.assistant, 'a-form').fire('submit', { preventDefault() {} });
  await tick();

  const b1 = tailBubbleOf(m);
  assert.ok(b1, '没有尾部气泡 —— token 会无处可写');
  cs.push({ type: 'token', content: '甲' });
  await tick();
  cs.push({ type: 'token', content: '乙' });
  await tick();
  const b2 = tailBubbleOf(m);
  assert.equal(b2.textContent, '甲乙');
  assert.equal(b2.innerHTML, '', '流式期间渲染 Markdown 会让页面抖动(半截的 ** 会重新排版)');

  cs.push({ type: 'done', content: '甲乙' });
  cs.close();
  await tick();
  await tick();
  assert.equal(m.KP.S.messages[1].content, '甲乙', 'done 之后正文没落到消息上');
  assert.equal(m.KP.S.busyChat, null, '忙标志没放开 —— 输入框会永远禁用');
  assert.ok(m.elements.assistant.innerHTML.includes('甲乙'), 'done 之后要整块重绘(那一遍才会渲染 Markdown)');
});

test('send:中途重绘后 token 仍进**新**气泡(R3)', async () => {
  // `recommend` 帧会让视图重绘,重绘后旧气泡已脱离文档。若把引用提前存进闭包,
  // 后续 token 就写进了一个不在页面上的节点 —— 文字**凭空消失**,而且没有任何报错。
  // 这里推完 recommend 再推 token,断言新查出来的气泡里有全部内容。
  const cs = controllable();
  const m = await setup({ '/nodes/n1/chat': { body: cs.stream } });
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));
  m.KP.dom.q(m.elements.assistant, 'a-input').value = '讲讲';
  m.KP.dom.q(m.elements.assistant, 'a-form').fire('submit', { preventDefault() {} });
  await tick();

  cs.push({ type: 'token', content: '甲乙' });
  await tick();
  const before = tailBubbleOf(m);
  cs.push({ type: 'recommend', reason: '够了', confidence: 0.9 });
  await tick();
  assert.ok(m.KP.S.node.status === 'recommended', 'recommend 帧没落到节点上');
  const after = tailBubbleOf(m);
  assert.notEqual(after, before, '重绘后尾部气泡还是同一个引用 —— 这条用例失去了守卫意义');

  cs.push({ type: 'token', content: '丙' });
  await tick();
  assert.equal(tailBubbleOf(m).textContent, '甲乙丙', 'token 写进了已脱离文档的旧气泡');
  cs.push({ type: 'done' });
  cs.close();
  await tick();
  await tick();
});

test('send:中途切节点,旧流的帧被丢弃(R5)', async () => {
  // 不丢弃的话 token 会落到**新**节点的气泡上,而服务端其实已经把它存进**旧**
  // 节点了 —— 本地与服务端不一致,刷新才「诈尸」。
  const cs = controllable();
  const m = await setup({
    '/nodes/n1/chat': { body: cs.stream },
    '/nodes/n2/messages': { json: { messages: [] } },
  });
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));
  m.KP.dom.q(m.elements.assistant, 'a-input').value = '讲讲';
  m.KP.dom.q(m.elements.assistant, 'a-form').fire('submit', { preventDefault() {} });
  await tick();

  // 用户点了图上另一个节点:Inspector.open 会同步做这两件事。
  m.KP.S.node = F.node({ id: 'n2', name: '别的' });
  m.KP.S.nodeId = 'n2';
  m.KP.views.assistant.setNode('n2');
  await tick();

  cs.push({ type: 'token', content: '迟到的甲' });
  await tick();
  assert.deepEqual(plain(m.KP.S.messages), [], '旧节点的 token 落到了新节点上');
  assert.ok(!m.elements.assistant.innerHTML.includes('迟到的甲'));

  cs.push({ type: 'done', content: '迟到的甲' });
  cs.close();
  await tick();
  await tick();
  assert.deepEqual(plain(m.KP.S.messages), [], 'done 也不许把旧节点的正文写进来');
  assert.equal(m.KP.S.busyChat, null, '忙标志没放开');
});

test('send:空输入 / 没有节点 / 已在流式 一律不发请求', async () => {
  const cs = controllable();
  const m = await setup({ '/nodes/n1/chat': { body: cs.stream } });
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));

  m.KP.dom.q(m.elements.assistant, 'a-input').value = '   ';
  m.KP.dom.q(m.elements.assistant, 'a-form').fire('submit', { preventDefault() {} });
  await tick();
  assert.equal(m.fetch.calls.length, 0, '空输入发了一轮请求');

  m.KP.dom.q(m.elements.assistant, 'a-input').value = '有内容';
  m.KP.S.busyChat = 'n2';
  m.KP.dom.q(m.elements.assistant, 'a-form').fire('submit', { preventDefault() {} });
  await tick();
  assert.equal(m.fetch.calls.length, 0, '已经有一轮在流时又发了一轮 —— 两个流同时写两个节点,用户没法同时读');

  m.KP.S.busyChat = null;
  m.KP.S.nodeId = null;
  m.KP.dom.q(m.elements.assistant, 'a-input').value = '有内容';
  m.KP.dom.q(m.elements.assistant, 'a-form').fire('submit', { preventDefault() {} });
  await tick();
  assert.equal(m.fetch.calls.length, 0, '没有选中节点也发了请求');
});

test('send:网络层失败把错误写进气泡,忙标志仍要放开', async () => {
  const m = await setup({ '/nodes/n1/chat': { ok: false, status: 500, json: { detail: '上游炸了' } } });
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));
  m.KP.dom.q(m.elements.assistant, 'a-input').value = '讲讲';
  m.KP.dom.q(m.elements.assistant, 'a-form').fire('submit', { preventDefault() {} });
  await tick();
  await tick();
  assert.ok(m.KP.S.messages[1].content.includes('上游炸了'), m.KP.S.messages[1].content);
  assert.equal(m.KP.S.busyChat, null, '忙标志没放开 —— 输入框会永远禁用,不刷新就没法再发');
});

// ---- 走查反馈 ①:「点提纲 → 点继续学习」之后 AI 出不了答案 ---------------------
//
// 用户报的原话:「如果我先输入提纲中的问题,然后再点继续学习,可能会出现 AI 无法
// 输出答案的情况」。根因不在提纲那一段,而在**历史快照的新旧**:展开助手时会发一次
// `GET /messages`,而用户在那一次往返里就能把新一轮发出去(点一条提纲 = 展开助手 +
// 预填问题,紧接着点「继续学习」并回车)。那份快照是**提问之前**的历史 —— 服务端要等
// 这一轮流完才把回答落库,所以它末尾是一条**用户**消息。它一落地:
//
//   ① `S.messages` 被换掉,正在流的那一轮凭空消失;
//   ② 更要命的是尾部气泡的判据 `chat.tailBubble` 看见末条是用户消息就返回 `null`,
//      于是余下的 token 全部写进空气,`done` 之后也不会回来。
//
// 两步都**不报错**:页面看起来一切正常,只是回答没了 —— 而服务端已经答完并落库
// (重新打开节点才「诈尸」)。判据因此是 `roundsSent`(发过问就作废所有在途快照),
// 不是「有没有在途 session」:一轮结束后 session 就清了,快照仍可能在那之后落地。

test('send:慢的历史快照不得冲掉在途那一轮 —— 回答会从气泡里凭空消失(①)', async () => {
  // 让 `GET /messages` 卡住不返回:`json` 给一个 promise,桩里那句
  // `async () => r.json` 会把它 await 掉,于是响应什么时候落地由用例说了算。
  // 现实里这一次 GET 慢,最常见的原因是浏览器把它的连接排在几条 SSE 长连接后面。
  let release;
  const gate = new Promise((r) => { release = r; });
  const cs = controllable();
  const m = await setup({
    '/nodes/n1/messages': { json: gate },
    '/nodes/n1/chat': { body: cs.stream },
  });
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));
  m.KP.S.assistant.open = false;                       // 收起态:展开时才去拉历史

  // 点一条提纲 = 展开助手 + 预填问题(于是历史 GET 出发),再点「继续学习」、回车。
  m.KP.views.assistant.askAbout('n1', '请讲讲这一步：有哪些分块技术');
  await tick();
  m.KP.dom.q(m.elements.assistant, 'a-form').fire('submit', { preventDefault() {} });
  await tick();

  cs.push({ type: 'token', content: '分块技术主要有' });
  await tick();
  cs.push({ type: 'token', content: '定长、递归、语义三种。' });
  await tick();
  assert.equal(tailBubbleOf(m).textContent, '分块技术主要有定长、递归、语义三种。',
    '这一轮还没流完 —— 下面那条断言就失去意义了');

  // 慢的那份快照现在才落地,它比本地这份**旧**。
  release({ messages: [{ role: 'user', content: '请讲讲这一步：有哪些分块技术' }] });
  await tick();
  await tick();
  assert.equal(tailBubbleOf(m).textContent, '分块技术主要有定长、递归、语义三种。',
    '旧快照在流中间覆盖了 S.messages —— 尾部气泡判成了 null,余下的 token 全写进空气');
  assert.equal(m.KP.S.messages.length, 2, '在途那一轮被旧快照抹掉了');

  cs.push({ type: 'done', content: '分块技术主要有定长、递归、语义三种。' });
  cs.close();
  await tick();
  await tick();
  assert.equal(m.KP.S.messages[1].content, '分块技术主要有定长、递归、语义三种。',
    'done 的正文没落到消息上 —— 屏幕上这一轮永远是空的');
  assert.ok(m.elements.assistant.innerHTML.includes('定长、递归、语义三种'), m.elements.assistant.innerHTML);
});

test('send:快照在一轮**跑完之后**才落地,也不许把这一轮抹掉(①)', async () => {
  // 判据用 `roundsSent` 而不是「有没有在途 session」的理由就是这条:一轮结束时
  // `session` 已经清掉了,按 session 判会漏 —— 回答照样从屏幕上消失。
  let release;
  const gate = new Promise((r) => { release = r; });
  const cs = controllable();
  const m = await setup({
    '/nodes/n1/messages': { json: gate },
    '/nodes/n1/chat': { body: cs.stream },
  });
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));
  m.KP.S.assistant.open = false;
  m.KP.views.assistant.askAbout('n1', '讲讲分块');
  await tick();
  m.KP.dom.q(m.elements.assistant, 'a-form').fire('submit', { preventDefault() {} });
  await tick();

  cs.push({ type: 'done', content: '分块有三种。' });
  cs.close();
  await tick();
  await tick();
  assert.equal(m.KP.S.busyChat, null, '这一轮还没跑完 —— 下面那条断言就失去意义了');
  assert.ok(m.elements.assistant.innerHTML.includes('分块有三种'));

  release({ messages: [{ role: 'user', content: '讲讲分块' }] });
  await tick();
  await tick();
  assert.equal(m.KP.S.messages.length, 2, '跑完之后落地的旧快照把刚答完的那一轮抹掉了');
  assert.ok(m.elements.assistant.innerHTML.includes('分块有三种'), '屏幕上的回答没了 —— 刷新才诈尸');
});

test('收起助手不吃掉在途的那一轮 —— 重新展开要能看见全文(①)', async () => {
  // 收起此前会 `session = null`,于是帧被那句 `if (!session …) return` 全拦下、
  // `done` 的正文也没机会落到 `S.messages` 上:服务端答完并落了库,面板上一片空白。
  const cs = controllable();
  const m = await setup({ '/nodes/n1/chat': { body: cs.stream } });
  openOn(m, F.node({ id: 'n1', name: 'RAG' }));
  m.KP.dom.q(m.elements.assistant, 'a-input').value = '讲讲';
  m.KP.dom.q(m.elements.assistant, 'a-form').fire('submit', { preventDefault() {} });
  await tick();

  cs.push({ type: 'token', content: '分块技术' });
  await tick();
  m.KP.dom.q(m.elements.assistant, 'a-min').fire('click');   // 用户收起面板
  await tick();
  assert.equal(m.KP.S.assistant.open, false, '没收起 —— 这条用例就测不到收起路径了');

  cs.push({ type: 'token', content: '主要有三种。' });
  await tick();
  cs.push({ type: 'done', content: '分块技术主要有三种。' });
  cs.close();
  await tick();
  await tick();
  assert.equal(m.KP.S.messages[1].content, '分块技术主要有三种。',
    '收起把在途的回答丢了 —— 服务端答完了,屏幕上什么都没有');

  m.KP.dom.q(m.elements.assistant, 'a-launch').fire('click');   // 再展开
  await tick();
  assert.ok(m.elements.assistant.innerHTML.includes('分块技术主要有三种。'), m.elements.assistant.innerHTML);
});
