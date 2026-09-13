/**
 * AI 助手 —— **全前端唯一的对话 UI**(设计稿 §21),停靠在中间列底部。
 *
 * **为什么对话只有这一处。** 设计稿让对话同时出现在右栏 Inspector 与助手面板,
 * 那会造成两类静默 bug:① 同一个 `id` 出现两次时 `document.getElementById` 返回
 * **文档序第一个** —— 「助手里的发送按钮控制了 Inspector 的输入框」;② `chatTail`
 * 在用户「边聊边点图」时指到别的节点的气泡上,而常驻助手会让这条成为**主路径**
 * 而不是边缘情况。所以 Inspector 里一个输入框都没有,只有「开始学习」这个入口。
 *
 * 对话的**流式状态机不在这里** —— 在 `js/chat.js`(与对话页共用)。这里只负责把
 * reducer 返回的意图落到 DOM 上。
 *
 * 它问的是**某个知识点**,答案会落进那个节点的学习记录 —— 与 `views/chat.js` 的
 * 一次性研究问答是两件事,不要合并。
 */
(function (KP) {
  'use strict';

  const group = (KP.views = KP.views || {});
  const esc = KP.esc;

  function host() { return KP.views.shell.assistant(); }

  /**
   * 当前流式会话(`KP.chat` 的 session 对象)。模块级、视图内唯一 —— 同一时刻
   * 只可能有一轮在流。
   *
   * ⚠️ 尾部气泡的引用**不在这里缓存**,存在 `sess.tail` 上,且每次重绘由
   * `renderChat()` 重新指派。原因见 `js/chat.js` 顶部注释。
   */
  let session = null;

  /**
   * 已经为哪个节点把历史拉回来了。没有它的话每次重绘都会再发一次 GET ——
   * 而 `render()` 在一次对话里会被调很多次。
   */
  let loadedFor = null;

  /**
   * 本节点上**用户已经发出去几轮**。只增,换节点归零。
   *
   * 它是「那份历史快照还新不新」的判据(见 `ensureMessages`)。判据刻意不用
   * 「此刻有没有在途的 session」:一轮跑完之后 `session` 就被清掉了,而更旧的
   * 那份快照完全可能在它**之后**才落地 —— 那时按 session 判会漏掉,回答照样丢。
   */
  let roundsSent = 0;

  // ================= 纯字符串层(可在 Node 里直接断言)=================

  /**
   * 收起态那条细栏。**永远看得见,而且明确写着「展开」**。
   *
   * 带着节点名 —— 「继续学 RAG」比一个光秃秃的 ✨ 有用得多。但光有名字不够:
   * 上一版收起后就是一颗写着节点名的胶囊漂在画布左下角,看起来像个装饰性的标签,
   * 用户收起一次就再也找不到回来的路(这是他报的第一个问题)。所以这里多一个
   * 「展开 ▲」的尾巴 —— 一条**带动作词**的栏,读起来才是个能点的入口。
   */
  function launcherHTML(node) {
    const label = node && node.name ? esc(node.name) : 'AI 助手';
    return `<button class="as-launch" id="a-launch" type="button" title="展开 AI 助手">` +
           `<span class="as-spark">${KP.icons.UI.spark}</span>` +
           `<span class="as-lt">${label}</span>` +
           `<span class="as-open">展开 <span class="as-chev">${KP.icons.UI.up}</span></span></button>`;
  }

  /**
   * 助手的上下文行:正在学谁 + 本主题点了几个。
   *
   * 两个数都是**真实存在**的:「已对话 N 轮」来自 `chat_turns`,「已点亮 m / n」
   * 来自后端的 `progress`(前端绝不重算,见 `store.js` 的 `pct`)。设计稿 §21 那张
   * 图写的是「已完成 1 / 4」—— 后端没有子任务模型,那个分数无处可来,不编。
   */
  function ctxHTML(node, topic) {
    const p = (topic && topic.progress) || {};
    const total = Number(p.total) || 0;
    const mastered = Number(p.mastered) || 0;
    const cur = node
      ? `当前正在学习：<b>${esc(node.name)}</b>`
      : '还没有选中知识点 —— 在图谱里点一个';
    // 没有节点时那句「已点亮 m / n」反而更抢眼,所以它跟着节点一起出现/消失。
    const prog = (node && total) ? `<span class="as-sep">·</span>本主题已点亮 <b>${mastered} / ${total}</b>` : '';
    return `<div class="as-ctx"><span class="as-cur">${cur}</span>${prog}</div>`;
  }

  /**
   * 展开的那一块。**纯函数** —— 事件在 `render()` 里绑。
   *
   * 没有 `.card` —— 它不再是一张浮在角落的卡片,而是贴着中间列底部的一整块
   * (见 css/assistant.css)。加上圆角与投影会把它读回成浮层。
   */
  function panelHTML(node, messages, topic, err) {
    const on = !!node;
    const ph = on ? '就这个知识点提问…' : '先在图谱里点一个知识点';
    return `<div class="asst">
      <div class="as-head">
        <span class="as-title"><span class="as-spark">${KP.icons.UI.spark}</span>KnowledgePilot AI</span>
        <button class="as-min" id="a-min" type="button" title="收起面板,点底部那条栏可以再展开">收起 <span class="as-chev">▾</span></button>
      </div>
      ${ctxHTML(node, topic)}
      ${err ? `<div class="as-err">${esc(err)}</div>` : ''}
      <div class="as-log" id="a-chat">${KP.chat.bubblesHTML(messages)}</div>
      <form class="as-composer" id="a-form" autocomplete="off">
        <input id="a-input" type="text" placeholder="${ph}"${on ? '' : ' disabled'}>
        <button class="btn primary" type="submit" id="a-go"${on ? '' : ' disabled'}>${KP.icons.UI.send}</button>
      </form>
    </div>`;
  }

  // ================= 落到 DOM 的那一层 =================

  /**
   * 重绘整块。
   *
   * **输入框里已经打的字会保留** —— `ensureMessages` 是异步的,它拉完历史会再调一次
   * `render()`;不保留的话,「点要点芯片 → 输入框被填上问题 → 历史刚好拉回来 → 填的字
   * 被冲掉」,而用户只会觉得自己手滑了。
   */
  function render() {
    const S = KP.S;
    const el = host();
    if (!el) return;
    // 「对话」页本身就是一个完整的对话界面,再叠一个停靠输入框是纯粹的混乱。
    // **必须连容器一起藏掉**,不能只清空 innerHTML:`#assistant` 自己带一条上边框
    // 和底色(css/assistant.css),空着不藏会在对话页底部留一条无来由的横线。
    //
    // 用 `[hidden]` 而不是 `display:none` 的来源是 css 那句 `display: flex` ——
    // 它会盖掉浏览器给 `[hidden]` 的默认 `display: none`,所以那边另有一条
    // `#assistant[hidden]` 兜底。这两处**必须成对存在**,漏掉后者的表现是
    // 「设置了 hidden 但什么都没发生」。
    if (S.route && S.route.view === 'chat') { el.innerHTML = ''; el.hidden = true; return; }
    el.hidden = false;

    const prev = KP.dom.q(el, 'a-input');
    const keep = prev ? prev.value : '';

    if (!S.assistant.open) {
      el.innerHTML = launcherHTML(S.node);
      const launch = KP.dom.q(el, 'a-launch');
      if (launch) launch.addEventListener('click', () => { S.assistant.open = true; render(); });
      return;
    }

    el.innerHTML = panelHTML(S.node, S.messages, KP.currentTopic(S.topics, S.topicId), S.assistant.err);
    const min = KP.dom.q(el, 'a-min');
    // **收起不等于放弃这一轮。** 这里此前会 `session = null`,于是收起草稿的同一个
    // 动作把在途的回答也一起丢了:帧被那句 `if (!session …) return` 全部拦下,`done`
    // 的正文也没机会落到 `S.messages` 上 —— 服务端答完并落了库,而面板上什么都没有
    // (又是「刷新才诈尸」)。留着 session,帧继续进 `S.messages`(气泡此刻不在文档里,
    // 画上去看不见也无所谓),展开时 `renderChat()` 会把全文补回来。
    if (min) min.addEventListener('click', () => { S.assistant.open = false; render(); });
    const form = KP.dom.q(el, 'a-form');
    if (form) form.addEventListener('submit', (e) => { e.preventDefault(); send(); });

    const input = KP.dom.q(el, 'a-input');
    if (input && keep && !S.busyChat) input.value = keep;
    renderChat();
  }

  /** 只重绘气泡。流式里每帧走它,不走 `render()` —— 否则输入框会被整块重建。 */
  function renderChat() {
    const S = KP.S;
    const box = KP.dom.q(host(), 'a-chat');
    if (!box) return;
    // **必须在 `innerHTML =` 之前读滚动位置** —— 重写 innerHTML 会把滚动位置清零,
    // 之后再读就永远是「贴在顶部」了。
    const gap = (box.scrollHeight || 0) - (box.scrollTop || 0) - (box.clientHeight || 0);
    const pinned = gap < 40;   // 留一点余量:像素级「恰好贴底」几乎不成立
    box.innerHTML = KP.chat.bubblesHTML(S.messages);
    // 尾部气泡**每帧重读**(见 `js/chat.js` 顶部):`recommend` 帧会重绘,重绘后
    // 旧的气泡节点已脱离文档,提前存进闭包的引用会让后续 token 写进空气里。
    if (session) session.tail = KP.chat.tailBubble(box, S.messages);
    // 流式中重绘(比如收起后重新展开)要把已流出的字补回去;`done` 之后不能补 ——
    // 那时 `bubblesHTML` 已经把正文渲染成了 Markdown,`paint` 会把纯文本盖回去。
    if (session && session.open) KP.chat.paint(session);
    // 跟着最新一条走,**但只在用户本来就贴底时**。用户往上翻看历史时把视图拽回底部
    // 是这类面板最招人烦的行为;而「翻上去」与「跟着流」的判据就是他离底有多远。
    //
    // 上一版这里写的是 `box.scrollIntoView({block:'nearest'})` —— 那是**no-op**:
    // `scrollIntoView` 滚的是**祖先**容器(让这个盒子进入视野),而 `.as-log` 是
    // 滚动容器本身、它的盒子一直都在视野里。所以正文流出可视区之后从来没人把它
    // 滚下来过。停靠到栏底之后消息区成了主角,这个坑会更明显,顺手修掉。
    if (pinned) box.scrollTop = box.scrollHeight;
  }

  /**
   * 打开助手并把目标切到某个节点。设计稿 §18 的「开始学习」走这里。
   * `nodeId` 省略时用当前选中的那个。
   */
  function openFor(nodeId) {
    const S = KP.S;
    S.assistant.open = true;
    const id = nodeId || S.nodeId;
    // 收起状态下 `loadedFor` 一定是 null,所以展开时这里会走一次 `setNode`
    // 把历史拉回来 —— 那正是 `setNode` 有意不拉、留给这里的时刻。
    if (id !== loadedFor) setNode(id);
    else render();
  }

  /**
   * 打开助手并预填一个问题(右栏「要点」芯片的「点一下即可就这点提问」)。
   *
   * 预填放在 `setNode` **之后**:`setNode` 里的 `ensureMessages` 是异步的,它拉完
   * 历史会再 `render()` 一次 —— 那时 `render` 的「保留已输入内容」会把这里填的问题
   * 带回来,所以两条路径都不会丢字。
   */
  function askAbout(nodeId, text) {
    openFor(nodeId);
    const input = KP.dom.q(host(), 'a-input');
    if (input) { input.value = text; input.focus(); }
  }

  /**
   * 目标节点变了。**立刻清掉旧节点的对话**,再去拉新的。
   *
   * 不清的话,在途的 token 会落到新节点的气泡上,而服务端其实已经把它存进**旧**
   * 节点了 —— 本地与服务端不一致,刷新才「诈尸」。做法是两条:`session = null`
   * (帧处理器的 `session.nodeId !== nodeId` 判定因此成立)、`S.messages = []`。
   *
   * `null` 是合法入参:没有选中任何节点。此时输入框禁用,而不是让它看起来能打字。
   *
   * **收起状态下不拉历史** —— 那是一次白发的 GET(用户可能只是在图上逛)。只把
   * `loadedFor` 清空,等 `openFor` 真正展开时再拉。
   */
  function setNode(nodeId) {
    const S = KP.S;
    session = null;
    S.messages = [];
    S.assistant.err = '';
    loadedFor = null;
    roundsSent = 0;
    render();
    if (nodeId && S.assistant.open) { loadedFor = nodeId; ensureMessages(nodeId); }
  }

  /**
   * 拉某个节点的对话历史。失败时留一行说明 —— 空了就是「还没有聊过」,两者不能长一样。
   *
   * **这份快照可能比本地那份旧,那就整份丢掉。** 一次 GET 的往返里用户完全可能
   * 已经把新一轮发出去了(走查反馈 ①:点一条提纲 → 点「继续学习」 → 回车,这条路上
   * 历史 GET 正打在中间),而这个快照是**提问之前**那一刻的历史:服务端要等这一轮
   * 流完才把回答落库,所以它的末尾是一条**用户**消息。拿它覆盖 `S.messages` 的后果
   * 分两步,两步都静默:
   *
   *   1. 本地那份「用户问 + 助手占位」被抹掉,屏幕上刚流出半句的回答**立刻变空**;
   *   2. 更糟的是尾部气泡的判据 —— `chat.tailBubble` 看见末条是用户消息就返回
   *      `null`,于是**余下的 token 全部写进空气**,`done` 之后也不会回来。
   *
   * 用户看到的就是「AI 不出答案」,而服务端其实答完并落库了(刷新才诈尸)。
   * 判据用 `roundsSent` 而不是「有没有在途 session」:一轮结束后 `session` 就清了,
   * 快照仍可能在那之后落地(见 `chat.tailBubble` 那条不变量的上游)。
   *
   * 代价是这一屏暂时少了更早的历史 —— 与用户切走节点时丢弃在途的响应是同一条
   * 取舍:响应比本地状态**旧**就丢。重新打开这个节点会再拉一次(`setNode` 会清
   * `loadedFor`),前后文不会永久丢掉。
   */
  async function ensureMessages(nodeId) {
    const S = KP.S;
    const issuedAt = roundsSent;
    let data = null;
    let err = null;
    try {
      data = await KP.api.getMessages(nodeId);
    } catch (e) {
      err = e;
    }
    if (S.nodeId !== nodeId) return;                 // 已经切走了
    if (roundsSent !== issuedAt) return;             // 这中间发过问 → 本地那份更新
    if (err) {
      S.messages = [];
      S.assistant.err = '历史对话加载失败：' + ((err && err.message) || '未知错误');
    } else {
      S.messages = (data && data.messages) || [];
    }
    if (S.assistant.open) render();
  }

  /** 一轮对话:讲解逐字流入 →(够格时)推荐点亮 → 落库。 */
  async function send() {
    const S = KP.S;
    const el = host();
    const input = KP.dom.q(el, 'a-input');
    const go = KP.dom.q(el, 'a-go');
    if (!input) return;
    const text = input.value.trim();
    const nodeId = S.nodeId;
    // 同一时刻只允许一轮在流(`busyChat` 是 nodeId 而不是布尔)。已经有一轮在跑时
    // 换到别的节点再发也会被挡住 —— 这是有意的:两个 LLM 流同时写两个节点,
    // 用户没法同时读。
    if (!text || S.busyChat || !nodeId) return;
    S.busyChat = nodeId;
    input.disabled = true;
    if (go) go.disabled = true;
    S.assistant.err = '';

    // 先记账再推消息:`roundsSent` 一变,所有**在途**的历史快照就作废了(见
    // `ensureMessages`)。反过来的话,快照有可能刚好在推消息之前落地并被覆盖。
    roundsSent += 1;
    S.messages.push({ role: 'user', content: text });
    S.messages.push({ role: 'assistant', content: '' });
    session = KP.chat.createSession(nodeId, S.messages);
    renderChat();

    try {
      await KP.api.chatNode(nodeId, text, (evt) => {
        // 会话已被换掉(用户切到了别的节点)→ 这一帧属于上一轮,丢弃(R5)。
        if (!session || session.nodeId !== nodeId) return;
        const intent = KP.chat.applyFrame(session, evt);
        if (intent.paint) KP.chat.paint(session);
        if (intent.node) {
          Object.assign(S.node, intent.node);
          KP.views.inspector.patchGraphNode(S.node);
        }
        if (intent.rerender) {
          renderChat();                       // 重绘 → session.tail 指向新的气泡
          if (intent.node) KP.views.inspector.render();
        }
        // 一轮结束:把节点重新拉一次,让图上那个盒子换成正确的状态。
        if (intent.done) KP.views.inspector.refreshNode(nodeId);
      });
    } catch (err) {
      if (session && session.nodeId === nodeId) {
        KP.chat.failSession(session, err.message);
        renderChat();
      }
    } finally {
      S.busyChat = null;
      session = null;
      // 整块重绘而不是去动那两个已脱离文档的元素:输入框与按钮在 `render()` 里是
      // 新建的,天然是启用的。原来那版逐个 `disabled = false` 在「流畅式中途换了
      // 节点」时会写到一个不在页面上的旧输入框,而新输入框仍然是禁用的。
      render();
      const fresh = KP.dom.q(host(), 'a-input');
      if (fresh && S.nodeId) fresh.focus();
      await KP.views.shell.refreshProgress();
    }
  }

  group.assistant = {
    // 纯函数(可在 Node 里直接断言)
    launcherHTML, ctxHTML, panelHTML,
    // 落到 DOM 的那一层
    render, renderChat, openFor, askAbout, setNode, send,
  };
})(window.KP = window.KP || {});
