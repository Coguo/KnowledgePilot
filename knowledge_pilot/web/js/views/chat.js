/**
 * 「对话」页 —— 对 `POST /api/chat` 的**一次性研究问答**(不建图谱、不落库)。
 *
 * **它与浮动助手是两件不同的事,不要合并:**
 *   - 浮动助手问的是**某个知识点**,答案会落进那个节点的学习记录,下次打开还在;
 *   - 这一页问的是**任何问题**,后端跑一轮完整的 research agent(检索 / 评估 /
 *     可能多轮),答完就没了 —— `main.py` 只有 POST,没有历史 GET(见 `api.js`)。
 *
 * 因为「答完就没了」是后端的事实而不是省略,这里**不做任何持久化**:不写
 * localStorage、不假装有历史。刷新即清空 —— 假装有历史、结果刷新后空了,比
 * 一开始就说明白更糟。`S.research.messages` 只活在内存里,用来支持页面内切走再
 * 切回来。
 *
 * 流式状态机复用 `js/chat.js`(`applyFrame` / `paint` / `tailBubble`),不复制一份
 * ——「非流式 provider 只发 done」那个兜底只需要修一处。
 */
(function (KP) {
  'use strict';

  const group = (KP.views = KP.views || {});

  function stage() { return KP.views.shell.stage(); }

  /** 当前流式会话。模块级、唯一 —— 这一页同时只可能有一轮在流。 */
  let session = null;

  /**
   * 本轮的过程日志(「正在检索…」「已获取结果…」)。**只保留当前这一轮** ——
   * 累积整场对话的日志会让它淹没正文,而它的价值就是「让你知道它现在在干什么」。
   */
  let logLines = [];

  const EMPTY_HINT = '<div class="hint">问一个需要查资料的问题,例如「RAG 的 chunking 有哪些常见策略?」。<br>' +
                     '这一页的问答不会建图谱，也不保存历史。</div>';

  // ================= 纯字符串层 =================

  /** 消息列表 + 过程日志。纯函数,可在 Node 里断言。 */
  function bodyHTML(messages) {
    return `<div class="r-log" id="r-log">${logHTML()}</div>
      <div class="r-msgs" id="r-chat">${KP.chat.bubblesHTML(messages, EMPTY_HINT)}</div>`;
  }

  /** 过程日志。`cls` 让错误那行变红(与生成日志同一套 .line 样式)。 */
  function logHTML() {
    if (!logLines.length) return '';
    return logLines
      .map((l) => `<div class="line${l.cls ? ' ' + l.cls : ''}">${KP.esc(l.text)}</div>`)
      .join('');
  }

  /** 整页骨架。**纯函数**;事件在 `bind()` 里绑。 */
  function pageHTML() {
    return `<div class="topbar">
        <h2>对话</h2>
        <span class="timer">一次性研究问答 · 不建图谱、不保存历史</span>
      </div>
      <div class="research" id="research">
        ${bodyHTML(KP.S.research.messages)}
        <form class="r-composer" id="r-form" autocomplete="off">
          <input id="r-input" type="text" placeholder="问点什么…">
          <button class="btn primary" type="submit" id="r-go">${KP.icons.UI.send} 发送</button>
        </form>
      </div>`;
  }

  // ================= 落到 DOM 的那一层 =================

  /**
   * 整页渲染。
   *
   * **已经输入的草稿会保留** —— 与助手同一个理由:打字打到一半时一次重绘把内容
   * 冲掉,用户只会以为自己手滑。这里只有恢复历史时才重绘整页,但草稿保留是白拿的。
   */
  function render() {
    const el = stage();
    if (!el) return;
    const prev = KP.dom.q(el, 'r-input');
    const keep = prev ? prev.value : '';

    el.innerHTML = pageHTML();
    const form = KP.dom.q(el, 'r-form');
    if (form) form.addEventListener('submit', (e) => { e.preventDefault(); send(); });
    const input = KP.dom.q(el, 'r-input');
    if (input && keep) input.value = keep;
    scrollToEnd();
  }

  function scrollToEnd() {
    const el = stage();
    const box = KP.dom.q(el, 'r-chat');
    if (!box) return;
    // **滚动容器是 `.research`,不是 `#r-chat`**(见 `css/chat.css`)—— 消息区已经不再
    // 自己滚了。用 `scrollIntoView` 而不是直接把 `scrollTop` 拉到底:容器上有
    // `scroll-padding-bottom`(给吸底的输入框留出的位置),只有前者会把它算进去。
    // 直接拉到底的话,最后一行正文正好被输入框压住,而那是用户最想读的一行。
    // `scrollIntoView` 在桩里不存在(真实的 div 一定有);退回直接设容器的 scrollTop。
    if (typeof box.scrollIntoView === 'function') box.scrollIntoView({ block: 'end' });
    else {
      const col = KP.dom.q(el, 'research');
      if (col) col.scrollTop = col.scrollHeight;
    }
  }

  /**
   * 重绘气泡与日志。**流式里每帧走它,不走 `render()`** —— `render()` 会重建整个
   * 表单,把用户正在打的下一个问题连同输入焦点一起丢掉。
   */
  function renderChat() {
    const el = stage();
    const S = KP.S;
    const box = KP.dom.q(el, 'r-chat');
    if (!box) { render(); return; }
    box.innerHTML = KP.chat.bubblesHTML(S.research.messages, EMPTY_HINT);
    // 尾部气泡每帧重读(见 `js/chat.js` 顶部):重绘后旧气泡已脱离文档。
    if (session) session.tail = KP.chat.tailBubble(box, S.research.messages);
    const log = KP.dom.q(el, 'r-log');
    if (log) log.innerHTML = logHTML();
    scrollToEnd();
  }

  /** 追加一行过程日志。只写 DOM 的那一小块,不整页重绘。 */
  function log(text, cls) {
    logLines.push({ text, cls: cls || '' });
    const el = KP.dom.q(stage(), 'r-log');
    if (el) el.innerHTML = logHTML();
  }

  /** 一轮研究问答。 */
  async function send() {
    const S = KP.S;
    const el = stage();
    const input = KP.dom.q(el, 'r-input');
    if (!input) return;
    const text = input.value.trim();
    if (!text || S.research.busy) return;
    S.research.busy = true;
    input.value = '';
    input.disabled = true;
    const go = KP.dom.q(el, 'r-go');
    if (go) go.disabled = true;

    logLines = [];
    S.research.messages.push({ role: 'user', content: text });
    S.research.messages.push({ role: 'assistant', content: '' });
    // 会话身份用**对象本身**判定(见下面的 `session !== sess`),不用 `nodeId` ——
    // 这一页没有「切到别的节点」这回事,而给 `nodeId` 编一个假值会在下一处代码里
    // 被当成真的读。
    const sess = KP.chat.createSession(null, S.research.messages);
    session = sess;
    renderChat();

    try {
      // 参数在**发出去的这一刻**才组装(`KP.researchOpts()`):设置页改的轮次上限
      // 存的是本机偏好,启动时读一次塞进状态的话,「改完设置刷新一次」就会丢掉它,
      // 而用户唯一能给出的解释是「这个开关没用」。
      await KP.api.researchChat(text, KP.researchOpts(), (evt) => {
        if (session !== sess) return;   // 上一轮的迟到帧,丢弃
        const intent = KP.chat.applyFrame(sess, evt);
        if (intent.paint) KP.chat.paint(sess);
        // 过程日志与生成日志**共用同一张映射表**(`KP.chat.line`)—— 两份实现一定
        // 会分叉,而分叉只表现为「同一件事在这个页面说得多、在那个页面说得少」。
        const ln = KP.chat.line(evt);
        if (ln) log(ln.text, ln.cls);
        if (intent.rerender) renderChat();
      });
    } catch (err) {
      if (session === sess) {
        KP.chat.failSession(sess, err.message);
        renderChat();
      }
    } finally {
      S.research.busy = false;
      session = null;
      const box = KP.dom.q(stage(), 'r-input');
      if (box) { box.disabled = false; box.focus(); }
      const btn = KP.dom.q(stage(), 'r-go');
      if (btn) btn.disabled = false;
    }
  }

  group.chat = { pageHTML, bodyHTML, logHTML, render, renderChat, send };
})(window.KP = window.KP || {});
