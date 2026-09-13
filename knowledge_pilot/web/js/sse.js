/**
 * SSE 读取。**这是整个前端最微妙的一段代码**,所以刻意单独成文件 + 做成纯函数
 * (`readStream` 只接收一个 ReadableStream,不认识 fetch,也不认识路径)——
 * 这样 Node 测试可以喂假的字节流,把每种切分情况都跑一遍。
 *
 * 三条不能「顺手简化」的东西:
 *
 * 1. **半帧缓冲**。帧以空行分隔,但一个 chunk 完全可能只到半帧。把 chunk 直接当帧解析
 *    会偶发 JSON 解析失败 —— 网络越慢越常见。残段必须留在 buf 里等下一个 chunk。
 *
 * 2. **按字节流解码**。`decoder.decode(value, { stream: true })` 才能正确处理**被 chunk
 *    边界切开的多字节字符**。中文正文必然发生这件事(一个汉字 3 字节,切在中间就会解出
 *    乱码甚至让 JSON.parse 失败)。传 `{ stream: true }` 后不完整的尾字节会留在解码器里。
 *
 * 3. **`\r\n` 分隔**。帧分隔符按 `/\r?\n\r?\n/` 匹配。若只按 `'\n\n'` 切,一个把换行
 *    规范化成 CRLF 的反向代理就会让**整条流一帧都切不出来**(`\n\r\n` 里没有 `\n\n`),
 *    于是所有正文被静默丢弃、页面一直空着 —— 没有报错、没有失败,最难查的一类。
 *
 * 失败模式全部是**静默**的:丢帧被 `catch { continue }` 吞掉,正文少一段而界面照常。
 */
(function (KP) {
  'use strict';

  const DONE = '[DONE]';
  const FRAME_SEP = /\r?\n\r?\n/;

  /**
   * 消费一条 SSE 字节流,每解析出一个 JSON 帧回调一次。
   * 遇到 `data: [DONE]` 立即停止并取消剩余读取。
   *
   * @param {ReadableStream} stream
   * @param {(evt: object) => void} onFrame
   */
  async function readStream(stream, onFrame) {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let sawDone = false;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const frames = buf.split(FRAME_SEP);
        buf = frames.pop() || '';        // 最后一段可能是半帧,留到下一个 chunk 再拼
        for (const frame of frames) {
          if (emitFrame(frame, onFrame)) { sawDone = true; return; }
        }
      }
      // 流结束时 buf 里若还剩一段,按 SSE 规范它也是一个完整事件(服务端最后一帧
      // 没跟空行的情况)。截断的半帧会在 JSON.parse 处失败并被跳过,所以这里不会误伤。
      buf += decoder.decode();
      if (buf.trim()) emitFrame(buf, onFrame);
    } finally {
      if (sawDone) {
        // 提前收工时把连接放掉,不然响应体会一直挂着。
        try { reader.cancel(); } catch (e) { /* 取消失败无所谓,反正已经拿到 [DONE] */ }
      }
    }
  }

  /**
   * 解析一帧,返回是否遇到 `[DONE]`。
   *
   * 行尾的 `\r` 单独剥掉:SSE 允许多行 `data:`,一旦按 CRLF 分割,`data: {...}\r` 的
   * `slice(6)` 会带上 `\r`,JSON.parse 直接失败 → 又是一种静默丢帧。
   */
  function emitFrame(frame, onFrame) {
    for (const raw of String(frame).split('\n')) {
      const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw;
      if (!line.startsWith('data: ')) continue;
      const data = line.slice(6);
      if (data === DONE) return true;
      let evt;
      // 畸形帧跳过但**不中断循环** —— 后面还有正常帧。
      try { evt = JSON.parse(data); } catch (e) { continue; }
      try {
        onFrame(evt);
      } catch (e) {
        // 回调自己抛异常不该连累后续帧;交给 console 留痕,继续读。
        if (typeof console !== 'undefined' && console.error) console.error('[sse] onFrame 抛异常', e);
      }
    }
    return false;
  }

  /**
   * POST 一条 SSE 请求。`opts.fetchImpl` 可覆盖 fetch(测试注入桩)。
   *
   * 非 2xx 时抛出一个带 `.status` 的错误 —— 上层靠 `status === 503` 区分「后端未启用」。
   */
  async function post(path, body, onFrame, opts) {
    const fetchImpl = (opts && opts.fetchImpl) || KP.env.fetch;
    const resp = await fetchImpl(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!resp.ok || !resp.body) {
      const detail = await resp.json().then((d) => d.detail).catch(() => resp.statusText);
      const err = new Error(detail || `请求失败 (${resp.status})`);
      err.status = resp.status;
      throw err;
    }
    return readStream(resp.body, onFrame);
  }

  KP.sse = { readStream, post, DONE, FRAME_SEP };
})(window.KP = window.KP || {});
