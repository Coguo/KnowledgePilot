/**
 * `js/sse.js` —— 全前端最微妙的一段。
 *
 * 这里每一条用例对应一种**静默失败**:帧被丢弃时不报错、不抛异常,只是正文少了
 * 一段而界面照常。正因为它安静,才必须由测试而不是眼睛来守。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { loadKP } from './harness.mjs';

const enc = new TextEncoder();

/** 用一段段字节造一个 ReadableStream。`pull` 按需喂,模拟逐 chunk 到达。 */
function streamOf(chunks) {
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i >= chunks.length) { controller.close(); return; }
      const c = chunks[i++];
      controller.enqueue(typeof c === 'string' ? enc.encode(c) : c);
    },
  });
}

/** 把若干帧编成 SSE 字节流。 */
function frame(obj) { return `data: ${JSON.stringify(obj)}\n\n`; }

async function collect(chunks) {
  const { KP } = await loadKP();
  const seen = [];
  await KP.sse.readStream(streamOf(chunks), (evt) => seen.push(evt));
  return seen;
}

test('完整帧:一 chunk 多帧全部到达', async () => {
  const seen = await collect([frame({ type: 'token', content: '甲' }) + frame({ type: 'token', content: '乙' })]);
  assert.deepEqual(seen.map((e) => e.content), ['甲', '乙']);
});

test('半帧缓冲:一个帧被切成三段仍然只解析出一次', async () => {
  const whole = frame({ type: 'token', content: '完整' });
  const seen = await collect([whole.slice(0, 5), whole.slice(5, 20), whole.slice(20)]);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].content, '完整');
});

test('切点落在一个 3 字节汉字中间,正文不得变成乱码', async () => {
  // 中文正文必然发生这件事:一个汉字 3 字节,网络分片不会照顾字符边界。
  // 少了 `decoder.decode(value, { stream: true })`,这里解出来就是 U+FFFD。
  const bytes = enc.encode(frame({ type: 'token', content: '知识图谱' }));
  const cut = bytes.indexOf(enc.encode('知')[0]) + 1;   // 切在「知」的第 2 个字节之后
  const seen = await collect([bytes.slice(0, cut), bytes.slice(cut)]);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].content, '知识图谱');
  assert.ok(!seen[0].content.includes('�'), '出现了替换字符 —— 解码没有按流处理');
});

test('一 chunk 含 3 完整帧 + 1 半帧:半帧留到下一 chunk', async () => {
  const a = frame({ n: 1 }), b = frame({ n: 2 }), c = frame({ n: 3 }), d = frame({ n: 4 });
  const seen = await collect([a + b + c + d.slice(0, 6), d.slice(6)]);
  assert.deepEqual(seen.map((e) => e.n), [1, 2, 3, 4]);
});

test('`data: [DONE]` 之后立即停止,后续帧不再回调', async () => {
  const seen = await collect([frame({ n: 1 }) + 'data: [DONE]\n\n' + frame({ n: 2 })]);
  assert.deepEqual(seen.map((e) => e.n), [1]);
});

test('畸形 JSON 帧被跳过,但**后续帧照常到达**', async () => {
  const seen = await collect(['data: {不是合法 JSON\n\n' + frame({ n: 2 })]);
  assert.deepEqual(seen.map((e) => e.n), [2]);
});

test('onFrame 自己抛异常不连累后续帧', async () => {
  const { KP } = await loadKP();
  const seen = [];
  let first = true;
  await KP.sse.readStream(
    streamOf([frame({ n: 1 }) + frame({ n: 2 })]),
    (evt) => {
      seen.push(evt.n);
      if (first) { first = false; throw new Error('故意抛'); }
    },
  );
  assert.deepEqual(seen, [1, 2]);
});

test('CRLF 分隔的帧(反向代理规范化换行)不得整条丢光', async () => {
  // 只按 '\n\n' 切的话,`\r\n\r\n` 里没有 '\n\n' → 一帧都切不出来 →
  // 所有正文被静默丢弃、页面一直空着,没有报错、没有失败。
  const crlf = 'data: {"n":1}\r\n\r\ndata: {"n":2}\r\n\r\n';
  const seen = await collect([crlf]);
  assert.deepEqual(seen.map((e) => e.n), [1, 2]);
});

test('流结束时 buffer 里剩下的最后一帧也要给出(服务端末帧没跟空行)', async () => {
  const seen = await collect(['data: {"n":9}']);
  assert.deepEqual(seen.map((e) => e.n), [9]);
});

test('非 data: 开头的行(注释 / 心跳)被忽略', async () => {
  const seen = await collect([': 心跳\n\n' + frame({ n: 1 })]);
  assert.deepEqual(seen.map((e) => e.n), [1]);
});
