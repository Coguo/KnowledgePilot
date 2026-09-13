/**
 * 对话的**帧 reducer + 气泡 HTML**。抽出来是因为对话 UI 会长在两处
 * (`views/inspector.js` 的节点面板、以及后面要加的浮动助手 / 对话页)——
 * 复制一份就会分叉:一边修了 `done.content` 兜底、另一边没有。
 *
 * 这个模块只吐**字符串**和**意图对象**,不摸 DOM。视图拿到意图后自己决定怎么落地
 * (重绘面板?patch 节点?滚动到气泡?)。所以 reducer 能在 Node 里表驱动地断言。
 *
 * 两个必须保留的行为(都是「丢了不报错、只是静默变差」的那类):
 *
 * 1. **`done.content` 兜底**(`sess.acc || evt.content`)。非流式 provider 一个
 *    token 帧都不发,正文只在 `done` 里 —— 少了这行,气泡从头到尾空白。
 * 2. **尾部气泡每帧重读**。`recommend` 帧会触发视图重绘,重绘后旧的气泡节点已经
 *    脱离文档;若提前把引用存进闭包,后续 token 就写进了一个不在页面上的节点,
 *    文字**凭空消失**。所以 `sess.tail` 由视图在每次重绘后重新赋值,reducer 只用它。
 */
(function (KP) {
  'use strict';

  const esc = KP.esc;

  /** 助手消息出错时的前缀。用转义写,避免源码里出现不易审阅的组合字符。 */
  const WARN = '⚠️ ';

  const EMPTY_HINT = '<div class="hint">还没有对话。问一句开始——讲透之后系统会建议你点亮它。</div>';

  /**
   * 建一个流式会话。`nodeId` 记着这一轮是给谁问的 —— 用户「边聊边点图」时切到别的
   * 节点,在途的帧必须被丢弃,否则 token 会落到新节点的气泡上,而服务端其实已经
   * 把它存进**旧**节点了:本地与服务端不一致,刷新才「诈尸」。
   */
  function createSession(nodeId, messages) {
    return { nodeId, messages, acc: '', tail: null, open: true };
  }

  const lastMsg = (sess) => (sess.messages.length ? sess.messages[sess.messages.length - 1] : null);

  /**
   * 把一个 SSE 帧应用到会话上,返回**意图**:视图照着做,不做别的。
   *
   * @returns {{paint?:boolean, rerender?:boolean, done?:boolean, node?:object}}
   */
  function applyFrame(sess, evt) {
    if (!sess || !evt) return {};
    const target = lastMsg(sess);
    switch (evt.type) {
      case 'token':
        sess.acc += evt.content || '';
        return { paint: true };

      case 'recommend':
        // 先把已流出的正文落到消息上,再让视图重绘 —— 重绘后 sess.tail 指向新气泡。
        if (target) target.content = sess.acc;
        return {
          rerender: true,
          node: {
            status: 'recommended',
            recommend_reason: evt.reason || '',
            confidence: evt.confidence || 0,
          },
        };

      case 'done': {
        const final = sess.acc || evt.content || '';
        if (target) target.content = final;
        sess.open = false;
        return { rerender: true, done: true };
      }

      case 'error':
        if (target) target.content = WARN + (evt.message || '');
        sess.open = false;
        return { rerender: true, done: true };

      default:
        return {};
    }
  }

  /**
   * 「过程」类帧 → 一行日志。**纯函数,不碰会话状态。**
   *
   * 它与 `applyFrame` 是两件事,所以刻意**不**做成 `applyFrame` 的一个返回值:
   * `applyFrame` 管的是**消息内容**(气泡里写什么、会话结束了没有),这个管的是
   * **过程日志**(「正在检索…」「第 2 轮评估…」)。一帧可能两者都产 —— `error`
   * 既写进气泡、也留一行日志 —— 也可能只产其一。合成一个返回值会让「一个函数
   * 同时决定消息与日志」变成隐式契约,将来加一种帧就得同时想两件事。
   *
   * 抽出来的真正理由:**同一张映射表原来有两份** —— `views/graph.js` 的生成日志
   * 一份、对话页的过程日志一份。两份实现一定会分叉(一边补了「降级为线性路径」的
   * 说明、另一边没有),而分叉的表现只是「同一件事在这个页面说得多、在那个页面说得少」,
   * 没有任何报错、没有任何测试会红。现在只有一份,由 `chat.test.mjs` 表驱动地钉住。
   *
   * `undefined` / `NaN` 一律兜成 0:后端一定给全字段,但这个函数也会被喂前端拼的帧,
   * 而 `已生成 undefined 个知识点` 是那种会被截图发出来的 bug。
   *
   * @returns {{text:string, cls:string}|null} `null` = 这一帧不产出行。
   */
  function line(evt) {
    const e = evt || {};
    const n = (v) => Number(v) || 0;
    switch (e.type) {
      case 'plan': {
        const steps = (e.plan || []).map((s) => (s && (s.question || s.goal)) || '').filter(Boolean);
        return { text: '📋 研究计划：' + steps.join(' → '), cls: '' };
      }
      case 'status': return { text: '· ' + (e.message || ''), cls: '' };
      case 'eval':
        return {
          text: `第 ${n(e.iteration)} 轮评估：` +
                `${e.sufficient ? '✅ 信息已充分' : '🔁 继续研究'} — ${e.reason || ''}`,
          cls: '',
        };
      case 'memory': return { text: `🧠 复用 ${n(e.found)} 条历史研究记录`, cls: '' };
      case 'kg':
        return { text: `🕸️ 知识图谱：${n(e.entities)} 实体 / ${n(e.relations)} 关系`, cls: '' };
      case 'nodes':
        // 生成流程的收尾抽取出结果（第六轮:不再写报告,直接给知识点）。
        // 数量为 0 是**有信息量**的一件事(接下来会按研究计划降级),照实说。
        return { text: `🧩 已抽取出 ${n(e.count)} 个知识点`, cls: '' };
      case 'tool_call': return { text: '🔍 正在检索：' + (e.arguments || ''), cls: '' };
      case 'tool_result': return { text: '✅ 已获取结果：' + (e.summary || ''), cls: '' };
      case 'graph_ready':
        // 降级**不在这里解释原因**：后端的 `StatusEvent` 已经在前一帧说了完整的一句话
        // （「未能从资料中抽取出知识点，已按研究计划生成线性学习路径」），而这里原来
        // 硬编码的「已按报告标题生成线性路径」是第二份实现——第六轮换了降级源之后它
        // 就变成了假话。这一行只留一个「降级过」的标记。
        return {
          text: `✅ 已生成 ${n(e.nodes)} 个知识点 / ${n(e.edges)} 条前置关系` +
                (e.degraded ? '（已降级为线性路径）' : ''),
          cls: '',
        };
      case 'error': return { text: WARN + (e.message || ''), cls: 'err' };
      default: return null;
    }
  }

  /** 流式期间的绘制:纯文本,不渲染 Markdown —— 半截 Markdown 会让页面抖动。 */
  function paint(sess) {
    if (!sess || !sess.tail) return;
    sess.tail.textContent = sess.acc;
    if (sess.tail.scrollIntoView) sess.tail.scrollIntoView({ block: 'nearest' });
  }

  /** 会话失败(网络错、非 2xx)时的收尾:把错误写进最后一条助手消息。 */
  function failSession(sess, message) {
    const target = lastMsg(sess);
    if (target) target.content = WARN + (message || '');
    sess.open = false;
    return { rerender: true };
  }

  /**
   * 气泡 HTML。role 只有 user/assistant 两种(后端 `messages.role`)。
   *
   * `emptyHint` 可覆盖空态那句话:默认那句说的是「讲透之后系统会建议你点亮它」——
   * 那是**节点对话**的规则,摆在「对话」页(一次性研究问答,不建图谱)上是在
   * 承诺一件那里不会发生的事。
   */
  function bubblesHTML(messages, emptyHint) {
    const list = messages || [];
    if (!list.length) return emptyHint || EMPTY_HINT;
    return list.map((m) => {
      const mine = m.role === 'user';
      return `<div class="msg ${mine ? 'user' : 'assistant'}">` +
             `<div class="role">${mine ? '我' : '学习伙伴'}</div>` +
             `<div class="bubble md">${KP.markdown.renderMarkdown(m.content)}</div>` +
             `</div>`;
    }).join('');
  }

  /**
   * 重绘后重新定位「尾部气泡」。
   *
   * 末条是**用户**消息时返回 `null` —— 此时还没有助手气泡可写,流式帧会被
   * `paint()` 忽略,直到第一条助手内容出现(与拆分前的行为一致)。
   */
  function tailBubble(box, messages) {
    if (!box || !box.lastElementChild) return null;
    const list = messages || [];
    const last = list[list.length - 1];
    if (!last || last.role === 'user') return null;
    return box.lastElementChild.querySelector('.bubble');
  }

  KP.chat = {
    createSession, applyFrame, paint, failSession,
    bubblesHTML, tailBubble, line, WARN,
  };
})(window.KP = window.KP || {});
