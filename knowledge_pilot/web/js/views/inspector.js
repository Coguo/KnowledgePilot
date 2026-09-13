/**
 * 右侧节点面板(设计稿 §17~§20):知识点详情 + 学习状态 + 相关节点 + 来源 + 点亮操作。
 *
 * **这里没有对话输入框** —— 全前端的对话 UI 只有左下角的浮动助手一处
 * (`views/assistant.js`)。设计稿让两边都放输入框,那会造成两类静默 bug:
 * ① 同一个 `id` 出现两次时 `document.getElementById` 返回**文档序第一个**
 * (「助手里的发送按钮控制了 Inspector 的输入框」);② 「边聊边点图」时在途的
 * token 会落到别的节点的气泡上,而浮动助手会让这条成为**主路径**。
 * 面板里的入口只有一个:「开始学习」→ 展开助手。
 *
 * 面板里的每个元素都通过 `KP.dom.q(panel, name)` 取,**不用 `getElementById`**
 * (`js/dom.js` 顶部的规则)。
 */
(function (KP) {
  'use strict';

  const group = (KP.views = KP.views || {});
  const esc = KP.esc;

  function panel() { return KP.views.shell.panel(); }
  function stage() { return KP.views.shell.stage(); }

  async function open(nodeId) {
    const S = KP.S;
    const el = panel();
    if (!el) return;
    // 切到**另一个**节点会静默丢掉「我的笔记」里没保存的草稿,所以先问一句。
    // 同一个节点重新打开不算(点侧栏刷新那种):那种情况下草稿本来就该留着。
    if (S.nodeId !== nodeId && !confirmDiscard()) return;
    S.nodeId = nodeId;
    // 讲解记录页与它的草稿跟着节点走 —— 上一个节点的草稿写到这一个的文本框里是错的。
    S.note = { open: false, draft: null, base: null, busy: false, err: '', msg: '' };
    // 提纲的生成状态跟着节点走 —— 上一个节点是「失败」还是「正在生成」都与这一个无关。
    S.outline = { forNode: nodeId, state: 'idle', err: '' };
    KP.views.shell.writeHash();
    // 先渲染骨架(立刻有反馈),再等 GET 回来填内容。
    el.innerHTML = '<div class="scroll"><div class="hint">载入中…</div></div>';
    // 助手跟着走:先清掉上一个节点的对话,再去拉这一个的(仅当它展开着)。
    // 这两件事必须**同步发生** —— 晚一步的话,在途的 token 会落到新节点的气泡上。
    KP.views.assistant.setNode(nodeId);
    KP.views.graph.renderGraph();

    let node;
    try {
      node = await KP.api.getNode(nodeId);
    } catch (err) {
      el.innerHTML = `<div class="scroll"><div class="notice">载入失败：${esc(err.message)}</div></div>`;
      return;
    }
    // 用户在等待期间又点了别的节点 → 这份数据已经不是屏幕上要的那份了。
    // 不判的话,后到的响应会把先到的那份覆盖掉,而页面上没有任何迹象。
    if (S.nodeId !== nodeId) return;
    S.node = node;
    // 「我的笔记」的基准取**这一刻**服务端正文里那一段(见 `noteDraftValue` 里
    // 「`base` 为什么不能省」):它只在这里与保存成功时更新,`S.node.body` 后来被谁
    // 铺成什么都不影响它。
    S.note.base = sectionOf(node);
    render();
    // 还没有提纲的知识点,此刻按需补一份(设计稿 §18「开始学习」的前提:
    // 一个完全不了解它的人,得先知道这东西该分几步学)。
    //
    // **判断成本是零** —— 正文就在手里,只看有没有 `## 提纲` 这一段。所以这里不需要
    // 任何「是否已请求过」的记账,重复调用也不会多发请求(有提纲的节点根本不会走到
    // 这一步)。后端的同名端点也是幂等的,两道防线各管一层。
    if (!KP.markdown.splitNoteOutline(node.body || '').items.length) ensureOutline(nodeId);
  }

  /**
   * 生成(或重试 / 重新生成)当前节点的提纲。
   *
   * 三种收场都写进 `S.outline.state`,面板据此渲染「正在生成 / 一列可点的小点 /
   * 一句可重试的失败」—— **不要让失败静默**：右栏什么也不显示时,用户看到的是一个
   * 没有提纲的知识点,而不是「这次没成功」。
   *
   * `force=true`(「重新生成」按钮)会**花一次 LLM 调用**:后端拿它跳过「已经有就
   * 回读」那道闸门,把文件里那一段换掉。走查反馈 ② 改了提纲的颗粒度,而旧节点文件
   * 里躺着的是老口径的那一版 —— 没有这条出口,用户只能自己去文件里删那一段。
   */
  async function ensureOutline(nodeId, force) {
    const S = KP.S;
    if (!nodeId || (S.outline.forNode === nodeId && S.outline.state === 'loading')) return;
    S.outline = { forNode: nodeId, state: 'loading', err: '' };
    render();
    let items = [];
    try {
      const res = await KP.api.ensureOutline(nodeId, force);
      // 切走了就整个放弃:这份提纲属于上一个节点,写到它的面板上是错的。
      if (S.nodeId !== nodeId) return;
      // 后端把新的正文一起带回来,直接换上即可 —— 前端不必复刻一遍「`## 提纲`
      // 插在哪一行」的规则(那份规则只该有一个实现:`notes.insert_outline`)。
      if (res && typeof res.body === 'string') S.node.body = res.body;
      items = (res && res.outline) || [];
    } catch (err) {
      if (S.nodeId !== nodeId) return;
      S.outline = { forNode: nodeId, state: 'error', err: (err && err.message) || '未知错误' };
      render();
      return;
    }
    // `outline` 为空 = 后端这次没能生成出可用条目(它有意不写半成品,见
    // `learning/outline.py`)。这不是异常,但对用户是同一件事:**这次没成,可以重试**。
    S.outline = items.length
      ? { forNode: nodeId, state: 'idle', err: '' }
      : { forNode: nodeId, state: 'error', err: '这次没能生成出可用的提纲' };
    render();
  }

  /**
   * 关掉节点详情。**面板不隐藏,改成占位提示** —— 右栏是常驻的一列(见 `css/layout.css`)。
   *
   * 上一版这里做的是 `el.hidden = true` + 从网格里摘掉 `.with-panel`:没选中节点时
   * 中央画布占满整个宽度,选中之后又被挤窄一次,每点一个节点画面就重新流式布局一遍。
   */
  function close() {
    const S = KP.S;
    const el = panel();
    // 关掉详情是**真的丢掉草稿**(右栏回到占位提示,连入口都没了)—— 问一句。
    // 返回 false 让调用方知道「什么都没发生」:点 ✕ 那条路上,hash 与图谱的重画
    // 必须跟着这件事一起回退,否则屏幕上会留下一次「关了但没关」的错位状态。
    if (!confirmDiscard()) return false;
    S.nodeId = null; S.node = null;
    // 提纲状态一并归零:在途的那次生成回来时会因为 `S.nodeId !== nodeId` 被丢弃,
    // 但状态本身得清掉 —— 否则下次打开同一个节点会先闪一下上次的「失败」。
    S.outline = { forNode: null, state: 'idle', err: '' };
    // 「讲解记录」页与它的草稿也归零:没有节点就没有它的入口,留着只会让下一个节点
    // 一打开就先闪一个别人的文本框。
    S.note = { open: false, draft: null, base: null, busy: false, err: '', msg: '' };
    // 助手的会话也跟着清 —— 否则收起时它还挂着上一个节点的历史,下次展开
    // 会先闪一下别人的对话。助手自己判断要不要保留展开状态。
    if (KP.views.assistant) KP.views.assistant.setNode(null);
    if (!el) return true;
    el.innerHTML = renderPlaceholder();
    return true;
  }

  /**
   * 「提纲」那一段(走查反馈 ④)。
   *
   * 这是面板里唯一回答**「这个知识点里有哪些东西要弄明白」**的东西:`## 要点` 给的是
   * 结论,一个完全不了解这个知识点的人拿到结论也动不了手。所以每一条都做成**可点的
   * 小点** —— 点一下就着这一点去问助手,和 要点芯片 是同一条路(`askAbout`)。
   *
   * 条目是**小点**(8~14 字,见 `learning/outline.py` 的走查反馈 ②),所以这里的
   * 动作词是「这点」而不是「这一步」:它列的是组成/坑/对策,不是一条条要执行的任务。
   *
   * 序号是有意义的(后端就是按序生成的:种类 → 做法 → 问题 → 处理),所以左边那个
   * 数字不是装饰。三种状态都在这里落地:有内容 / 正在生成 / 失败可重试;**失败不留白**。
   *
   * **有内容这一支也要能说「正在生成」和「这次没成」**:「重新生成」是拿新的一版去
   * 换旧的,而换的过程中旧的那一列一直在屏幕上 —— 只画旧的那一列、别的什么都不说,
   * 用户会以为按钮没反应;换失败时更是如此(文件里留着的就是这一版,屏幕上却没有任何
   * 「这次没成功」的痕迹)。
   */
  function outlineHTML(items, state, err) {
    if (items.length) {
      const rows = items.map((text, i) =>
        `<button class="ol-item" data-ask="${esc(text)}" type="button">` +
        `<span class="ol-n">${i + 1}</span>` +
        `<span class="ol-t">${esc(text)}</span></button>`).join('');
      const side = state === 'loading'
        ? '<span class="ol-note">正在重新生成…</span>'
        : '<button class="ol-regen" id="p-ol-regen" type="button" title="按当前口径重新生成这一列（会花一次模型调用）">重新生成</button>';
      const warn = state === 'error'
        ? `<div class="hint">重新生成没成功：${esc(err || '未知错误')}。下面是上一版。</div>` : '';
      return `<div class="sec sec-outline">
        <div class="sec-title"><span>提纲（点一下即可就这点提问）</span>${side}</div>
        ${warn}
        <div class="olist">${rows}</div>
      </div>`;
    }
    if (state === 'loading') {
      return `<div class="sec sec-outline">
        <div class="sec-title">提纲</div>
        <div class="hint">正在为这个知识点生成提纲…（几秒）</div>
      </div>`;
    }
    if (state === 'error') {
      return `<div class="sec sec-outline">
        <div class="sec-title">提纲</div>
        <div class="hint">${esc(err || '提纲生成失败')}，可以重试。</div>
        <button class="btn sm" id="p-ol-retry" type="button">重新生成提纲</button>
      </div>`;
    }
    // 还没有、也还没去要(比如打开的就是一个已经有提纲的节点)—— 什么都不画。
    return '';
  }

  /** 右栏的空态。纯字符串,便于在 Node 里断言「关闭之后不是一片空白」。 */
  function renderPlaceholder() {
    return `<div class="p-empty">
      <span class="pe-ic">${KP.icons.NAV.graph}</span>
      点击图谱中的任意节点<br>查看知识点详情与讲解
    </div>`;
  }

  function masteryAction(status) {
    if (status === 'mastered') return { label: '撤销掌握', cls: '' };
    if (status === 'recommended') return { label: '✓ 确认已掌握', cls: 'primary' };
    return { label: '标记为已掌握', cls: '' };
  }

  /**
   * 「学习状态」那一段(设计稿 §17)。
   *
   * **画不出设计稿那条 `8 / 15` 进度条** —— 后端没有子任务模型,那个分数无处可来。
   * 编一个会让整页最显眼的数字变假。这里如实写两件真事:四态标签 + 真实对话轮数。
   *
   * 第一次对话之前 `chat_turns` 是 0,此时说「还没有开始对话」而不是「已对话 0 轮」——
   * 后者读起来像一句系统统计,前者才是一句指路的话(设计稿 §18 的「开始学习」)。
   */
  function statusHTML(n, action) {
    const st = KP.stateOf(n);
    const turns = Number(n && n.chat_turns) || 0;
    const detail = turns
      ? `已对话 <b>${turns}</b> 轮`
      : (KP.nodeState(n) === 'mastered' ? '已标记为掌握' : '还没有开始对话');
    return `<div class="sec sec-status">
      <div class="sec-title">学习状态</div>
      <div class="st-row">
        <span class="chip" style="color:${st.stroke};border-color:${st.stroke}">${st.glyph} ${st.label}</span>
        <span class="st-detail">${detail}</span>
      </div>
      <button class="btn ${action.cls} sm" id="p-learn">${turns ? '继续学习' : '开始学习'}</button>
    </div>`;
  }

  /**
   * 「相关节点」(设计稿 §17)。列表项**可点**,点了就切到那个节点 —— 这是设计稿
   * 「学习路径」这个隐喻唯一能被用起来的地方。
   *
   * 箭头方向有意义:`→` 是「它是这个知识点的后续」(学了当前这个才能学它),
   * `←` 是「它是前置」。写反了会让用户按着列表倒着学。
   */
  function relatedHTML(list) {
    if (!list.length) return '';
    const items = list.map((r) =>
      `<button class="rel" data-rel="${esc(r.node.id)}" type="button">` +
      `<span class="rel-name">${esc(r.node.name)}</span>` +
      `<span class="rel-arrow">${r.dir === 'out' ? KP.icons.UI.arrow : KP.icons.UI.back}</span>` +
      `</button>`).join('');
    return `<div class="sec">
      <div class="sec-title">相关节点</div>
      <div class="rels">${items}</div>
    </div>`;
  }

  /**
   * 「来源」(设计稿 §17 的「来源文档」)。
   *
   * **后端没有「节点 ↔ PDF」这个映射** —— `messages[].sources` 生产上恒为 `[]`,
   * 所以不画那种文件卡片。这里给的是真实存在的两份产物:本次生成的**图谱大纲**
   * (`topic.report_path`,文件仍叫 `report.md`,里面是每个知识点 + 关键词)与本节点
   * 的 Markdown 正文(`node.note_path`)。
   *
   * 只显示**文件名**,不显示绝对路径 —— 绝对路径会暴露服务器上的部署布局,
   * 而它对用户没有任何用。
   */
  function sourcesHTML(node, topic) {
    const files = [];
    if (topic && topic.report_path) files.push({ label: '图谱大纲', path: topic.report_path });
    if (node && node.note_path) files.push({ label: '本知识点的正文', path: node.note_path });
    if (!files.length) return '';
    return `<div class="sec">
      <div class="sec-title">来源</div>
      <div class="srcs">${files.map((f) =>
        `<div class="src"><span class="src-ic">📄</span>` +
        `<span class="src-name" title="${esc(f.label)}">${esc(KP.baseName(f.path))}</span></div>`).join('')}</div>
    </div>`;
  }

  // ---- 「讲解记录」页(走查反馈 ④)--------------------------------------
  //
  // 这一段标题是**文件格式的一部分**,与 `learning/notes.py` 的 `EXPLAIN_SECTION` /
  // `USER_SECTION` 逐字对应。前端必须知道它们(要按段落切分正文),就像
  // `splitNoteOutline` 已经知道「提纲」一样 —— 两边各有一份字面量是这个格式的固有代价,
  // 所以改文件名里的段标题时**三处一起改**(notes.py / 这里 / markdown.js)。
  const EXPLAIN_TITLE = '讲解记录';
  const USER_TITLE = '我的笔记';

  // **已知取舍:文件里那句默认提示会原样出现在文本框里。**
  // 新建的节点文件在这一段写着「（在这里记笔记——程序不会改写这一节）」（`notes.py` 的
  // `DEFAULT_USER_HINT`，第 45 行），用户要自己删掉它才能写下第一行。**刻意不在前端把它
  // 滤掉、也不在前端复制一份那个字面量**：两份一定会分叉，而分叉只表现为「提示删不干净」
  // ——一种谁也查不出原因的小毛病。要改就改 `notes.py` 那一份（改完对**已经建好的**
  // 文件无效：那句话是写进文件的内容，不是渲染时的默认值）。

  /**
   * 两份笔记文本算不算同一份。**按「写盘再读回来」的那两次归一化比,不按字符比。**
   *
   * 用户敲的那一份要经过后端 `strip("\n")`（`notes.write_user_notes`）再经过前端
   * `splitNoteSections` 的 `trim()`，才会变成下一次打开时文本框里的基准 ——
   * **两次归一化都不在用户输入这一侧**。拿原始输入去和归一化之后的基准做字符比较，
   * 于是「敲的时候末尾顺手带了一个换行」这种最常见的输入，存完之后永远比不平，
   * 下一帧就被当成「还有未保存的修改」（走查反馈 ⑤:用户报的「已经保存了却显示未保存」）。
   *
   * **已知取舍:只差首尾空白的改动算「没改过」。** 首尾换行本来就留不住（后端
   * `strip("\n")` 会去掉），首尾空格会被下一次 `trim()` 吃掉 —— 写进去也看不出来。
   * 与其让用户面对一个永远清不掉的「未保存」，不如认为它们相等。
   */
  function sameNoteText(a, b) {
    return String(a == null ? '' : a).trim() === String(b == null ? '' : b).trim();
  }

  /**
   * 把文本框里**当前**的内容收进 `S.note.draft`。**每次重绘之前调。**
   *
   * 「草稿在 `S` 里、不在 DOM 里」这条不变量的兜底:`render()` 换掉整个 `innerHTML`,
   * 而右栏会在聊完一轮后（`refreshNode`）重绘一次 —— 只靠 `input` 监听器的话，
   * 任何一条没绑监听的路径（或在测试里直接写 `.value`）都会让用户敲的字**静默消失**。
   *
   * **与文本框里「本该显示的那一份」按 `sameNoteText` 比过再收**:一样就说明用户没改过，
   * 草稿保持 `null`。少了这一次比较，保存成功那条路就永远清不掉草稿 —— 重绘时会把
   * 文本框里那份（已经存好的）内容当成本地改动收回来，于是入口按钮上一直挂着
   * 「有未保存的修改」、切节点还要多问一句，而屏幕上完全看不出哪里不对。
   */
  function captureDraft() {
    const S = KP.S;
    if (!S.note.open) return;
    const ta = KP.dom.q(panel(), 'p-note-text');
    if (!ta || sameNoteText(ta.value, noteDraftValue(S.node, S.note))) return;
    S.note.draft = ta.value;
  }

  /**
   * 草稿要丢之前问一句。**只有两条路会真的丢掉它**:切到另一个节点、关掉节点详情。
   *
   * 「返回知识点」不算:草稿留在 `S.note.draft` 里，入口按钮上写着「有未保存的修改」。
   * 把它也做成一次确认的话，用户每来回看一次讲解记录都要点掉一个弹窗。
   *
   * 判「有没有草稿」必须写 `== null`（`null` 才是「没改过」）:用真值判断的话，
   * 用户「全选 + 删除」清空笔记之后这里一声不吭 —— 清空是一次真实的改动。
   */
  function confirmDiscard() {
    if (KP.S.note.draft == null) return true;
    return confirm('「我的笔记」还有没保存的修改，离开就会丢掉。要继续吗？');
  }

  /** 正文里「我的笔记」那一段(**渲染投影**里的那一份)。 */
  function sectionOf(node) {
    const mine = KP.markdown.splitNoteSections((node && node.body) || '')
      .filter((s) => s.title === USER_TITLE)[0];
    return (mine && mine.body) || '';
  }

  /**
   * 文本框里这一刻该显示什么,以及**「什么算改过」的基准**。
   *
   * 三级:草稿(`draft != null`)→ 基准(`base != null`)→ 正文投影。
   *
   * 单独一个函数,因为**三处都要用它**:`noteHTML` 把它写进标签之间(用户看见的)、
   * `bindNote` 把它写进 `.value`(程序读到的)、`captureDraft` 拿它当「什么算改过」的
   * 基准。三处各算一遍的话,迟早会出现「屏幕上显示的是草稿、读出来的是文件里那一版」
   * —— 而那种不一致在浏览器里完全看不见(用户用的就是屏幕上那份),只在保存时把旧
   * 内容写回去,或者反复声称「有未保存的修改」。
   *
   * **`base` 为什么不能省:`S.node.body` 是渲染投影,而它有好几个写入者,其中几个是
   * 异步的、后到的。** 聊完一轮的 `refreshNode` 会 `Object.assign(S.node, fresh)`
   * (只按 `nodeId` 挡一道)、补提纲的 `ensureOutline` 也会铺一次正文 —— 只要其中任何
   * 一次的响应**在保存之后**才到,它带回的就是保存**之前**的那一段。拿它当基准,
   * `captureDraft` 就会把文本框里刚存好的字当成本地改动收回来:屏幕上「已经保存了却
   * 显示未保存」,而磁盘上早就写好了(走查反馈 ⑤ 的另一条来路,与空白字符无关)。
   * 「我的笔记」只有两件事能改它 —— 打开一个节点、保存成功 —— 所以基准就挂在这两处。
   */
  function noteDraftValue(node, note) {
    if (note.draft != null) return note.draft;
    if (note.base != null) return note.base;
    return sectionOf(node);
  }

  /**
   * 「讲解记录」页的纯模板(走查反馈 ④)。
   *
   * 三块:大标题 + 返回、讲解记录(只读正文渲染)、我的笔记(可编辑)。
   *
   * **输入框的值走 `noteDraftValue`,不是原文照搬** —— 有草稿就用草稿。这是「重绘不丢
   * 草稿」那条不变量的落点(值不只写进 `innerHTML`,渲染之后 `bindNote` 还会再写一次
   * `.value`,于是小程序里也能直接断言)。
   *
   * 讲解记录那一段**只渲染正文**、不再渲染一个 `## 讲解记录` 标题:页面的大标题已经是
   * 它了,再来一遍就成了同一个词连着出现两次。
   */
  function noteHTML(node, note) {
    const secs = KP.markdown.splitNoteSections((node && node.body) || '');
    const explain = secs.filter((s) => s.title === EXPLAIN_TITLE)[0];
    const draft = noteDraftValue(node, note);
    // 段不存在、或只有一行标题时,说一句「还没有」——不然页面中间是一片空白,
    // 而空白读起来像「加载失败」。
    const explainHTML = explain && explain.body
      ? `<div class="md note-exp">${KP.markdown.renderMarkdown(explain.body)}</div>`
      : '<div class="hint">还没有讲解记录。回到知识点页点「开始学习」，讲完一轮就会追加到这里。</div>';
    // `note.err` 里放的是**一整句话**(谁写进去的谁负责把话说完)。三种失败的区别是实质性的:
    // 请求没到、后端说没写进去、写进去了但读回来对不上。统一加一个「保存失败：」前缀会把
    // 第三种说成假话 —— 那一次文件其实改了,只是改出来的东西用户下次看不到。
    const status = note.err
      ? `<div class="note-msg bad">${esc(note.err)}</div>`
      : (note.msg ? `<div class="note-msg ok">${esc(note.msg)}</div>` : '');
    return `
      <div class="p-head">
        <div class="p-title">
          <button class="p-back" id="p-note-back" type="button">← 返回知识点</button>
        </div>
      </div>
      <div class="scroll">
        <h2 class="note-title">${EXPLAIN_TITLE}</h2>
        ${explainHTML}
        <div class="sec note-edit-sec">
          <div class="sec-title">${USER_TITLE}（可编辑 · 保存后写入 Markdown）</div>
          ${node && node.note_path
            ? `<div class="path">${esc(KP.baseName(node.note_path))}</div>` : ''}
          <textarea class="note-edit" id="p-note-text" spellcheck="false">${esc(draft)}</textarea>
          <div class="hint note-tip">这里的改动只会写进「${USER_TITLE}」那一段 ——
            讲解记录是只追加的学习资产，程序还要往里写，整份可编辑会让两边互相覆盖。<br>
            也<strong>别用「## 」开新行</strong>:前后端都把它当成段的结束，那后面的字会
            留在文件里、却再也显示不到这个框里（保存时会当场告诉你）。</div>
          ${status}
          <button class="btn primary sm" id="p-note-save" type="button"
            ${note.busy ? 'disabled' : ''}>${note.busy ? '保存中…' : '保存笔记'}</button>
        </div>
      </div>`;
  }

  /** 绑定「讲解记录」页上那几个动作,并把草稿写回文本框的值(见 `noteDraftValue`)。 */
  function bindNote(el, node, note) {
    const S = KP.S;
    const back = KP.dom.q(el, 'p-note-back');
    if (back) back.addEventListener('click', () => closeNote());
    const ta = KP.dom.q(el, 'p-note-text');
    if (ta) {
      ta.value = noteDraftValue(node, note);
      ta.addEventListener('input', () => { S.note.draft = ta.value; });
    }
    const save = KP.dom.q(el, 'p-note-save');
    if (save) save.addEventListener('click', () => saveNote());
  }

  /** 进「讲解记录」页。**草稿留着** —— 从这一页退出去再回来不该丢东西。 */
  function openNote() {
    const S = KP.S;
    if (!S.node) return;
    S.note.open = true;
    S.note.err = ''; S.note.msg = '';
    render();
  }

  /** 回知识点页。不进 hash:**这一页是面板内的一页**,刷新回到节点详情(草稿本来也只在内存里)。 */
  function closeNote() {
    const S = KP.S;
    S.note.open = false;
    S.note.err = ''; S.note.msg = '';
    render();
  }

  /**
   * 保存「我的笔记」。**只有确实落了盘的写入才清草稿**;其余一律留着草稿并把话说清楚。
   *
   * 失败时留住草稿是与提纲同一条纪律(宁可旧、不可空):用户刚敲的一段字是这一屏上
   * 唯一不可再生的东西,请求挂了就把它清掉,等于用一次网络抖动换掉用户五分钟。
   *
   * 「成功」的判据有三层,缺一层就会出现「显示已保存、其实没存上」这种要用户自己
   * 去磁盘上核对的谎(走查反馈 ⑤):
   *
   *   1. **请求回来了**(不然就是 `catch`);
   *   2. **后端说写了** —— `/note` 的 `saved` 在笔记文件被移走时是 `false`,那是一次
   *      200 的失败,以前前端一眼都不看它;
   *   3. **读回来和送出去的是同一份** —— 后端回传的完整正文是这一步唯一的落盘凭证
   *      (前端不复刻「那一段插在哪」的规则,那份规则只该有一个实现:
   *      `notes.write_user_notes`)。对不上就说明这次写没有按用户的意思进去,最典型的
   *      是正文里有一行以 `## ` 开头(前后端都把它当成段的结束)。
   */
  async function saveNote() {
    const S = KP.S;
    const nodeId = S.nodeId;
    if (!nodeId || S.note.busy) return;
    captureDraft();
    // **送的是文本框里这一刻该显示的那一份**,不是 `S.note.draft || ''` —— 后面那种写法在
    // 「没改过就按保存」时送一个空串,把用户的笔记整段删掉;而存好一次之后 `draft` 又是
    // `null`,于是**再按一下保存就把刚存好的那一段抹掉**,屏幕上看起来还像是成功了。
    const text = noteDraftValue(S.node, S.note);
    S.note.busy = true; S.note.err = ''; S.note.msg = '';
    render();
    try {
      const res = await KP.api.saveNote(nodeId, text);
      if (S.nodeId !== nodeId) return;
      let fail = '';
      if (res && res.saved === false) {
        fail = '保存失败：一个字节也没写进去（笔记文件可能已被移走或删除）。改动还留在文本框里。';
      } else if (res && typeof res.body === 'string') {
        S.node.body = res.body;
        const back = sectionOf(S.node);
        if (sameNoteText(back, text)) {
          // **只在这一刻推进基准** —— 保存是「我的笔记」仅有的两个写入点之一(另一个
          // 是开节点,见 `noteDraftValue`)。
          S.note.base = back;
        } else {
          fail = '内容写进去了，但读回来和文本框里的不一样（「我的笔记」里以「## 」开头的行' +
            '会被当成下一段）—— 改动还留在文本框里，文件里的那一段需要你自己核对。';
        }
      } else {
        // 后端没把正文带回来 → 这一份无从核对。请求是 2xx、`saved` 也没说假话,就按
        // 「写进去了」算,但基准只能取刚送出去的那一份(下次开这个节点以服务端为准)。
        S.note.base = text;
      }
      if (fail) {
        S.note.draft = text;
        S.note.err = fail;
      } else {
        S.note.draft = null;
        S.note.msg = '已保存';
      }
    } catch (err) {
      if (S.nodeId !== nodeId) return;
      S.note.draft = text;
      S.note.err = '保存失败：' + ((err && err.message) || '未知错误') + '（改动还在，可以再试一次）';
    } finally {
      S.note.busy = false;
      // 期间切走了节点就不要再画:那一次 `open()` 自己画过新的了。
      if (S.nodeId === nodeId) render();
    }
  }

  function render() {
    const S = KP.S;
    const el = panel();
    // 重绘会换掉整个 `innerHTML` —— 先把文本框里的内容收进草稿(见 `captureDraft`)。
    captureDraft();
    const n = S.node;
    if (!el || !n) return;
    if (S.note.open) {
      el.innerHTML = noteHTML(n, S.note);
      bindNote(el, n, S.note);
      return;
    }

    const st = KP.stateOf(n);
    const pts = n.key_points || [];
    const action = masteryAction(n.status);
    // 相关节点从 `S.graph` 的 **edges** 推 —— 节点对象本身没有 `prerequisites`
    // 字段(那是单节点接口才有的冗余),拿它当来源的话这里会永远是空的。
    const rels = KP.relatedNodes(S.graph, n.id);
    const topic = (S.graph && S.graph.topic) || {};
    // 提纲从正文里拆出来单独渲染(可点的一列),所以正文预览里要把那一段去掉——
    // 否则同一份提纲在同一个面板上出现两次,一次能点一次不能。
    const ol = KP.markdown.splitNoteOutline(n.body || '');
    const olState = S.outline.forNode === n.id ? S.outline.state : 'idle';

    el.innerHTML = `
      <div class="p-head">
        <div class="p-title">
          <span>${esc(n.name)}</span>
          <button class="p-close" id="p-close" title="关闭">✕</button>
        </div>
        <div class="p-chips">
          <span class="chip" style="color:${st.stroke};border-color:${st.stroke}">${st.glyph} ${st.label}</span>
          ${n.type ? `<span class="chip">${esc(n.type)}</span>` : ''}
          <span class="chip">第 ${(Number(n.order_index) || 0) + 1} 个</span>
        </div>
      </div>
      ${n.status === 'recommended' && n.recommend_reason
        ? `<div class="reason"><strong>系统建议点亮这个知识点</strong>${esc(n.recommend_reason)}${n.confidence ? `（把握度 ${(n.confidence * 100).toFixed(0)}%）` : ''}</div>`
        : ''}
      ${statusHTML(n, action)}
      <div class="p-actions">
        <button class="btn ${action.cls} sm" id="p-mastery">${action.label}</button>
        ${n.status !== 'unlearned' && n.status !== 'mastered'
          ? '<button class="btn sm" id="p-dismiss">稍后再说</button>' : ''}
      </div>
      <div class="scroll">
        ${n.summary ? `<div class="sec"><div class="sec-title">概览</div><p class="summary">${esc(n.summary)}</p></div>` : ''}
        ${outlineHTML(ol.items, olState, S.outline.err)}
        ${pts.length ? `<div class="sec"><div class="sec-title">要点（点一下即可就这点提问）</div>
          <div class="points">${pts.map((p) => `<span class="pt" data-ask="${esc(p)}">${esc(p)}</span>`).join('')}</div></div>` : ''}
        ${relatedHTML(rels)}
        ${sourcesHTML(n, topic)}
        ${n.note_path ? `<div class="sec">
          <div class="sec-title">讲解记录与笔记</div>
          <div class="path">${esc(KP.baseName(n.note_path))}</div>
          <button class="btn sm" id="p-note-open" type="button">打开讲解记录${
            // `!= null`,不是真值:空串（用户把笔记清空）也是一次未保存的改动。
            S.note.draft != null ? '（有未保存的修改）' : ''}</button>
        </div>` : ''}
      </div>`;

    // 走查反馈 ④ 之前这里是 `<details class="file">` + 一段只读的 Markdown:summary 上
    // 写着「可编辑 · 已落盘」而里面根本没有输入框 —— 那句话是假的。现在正文不在这里
    // 渲染,而是进「讲解记录」页(`#p-note-open`)。
    const openNoteBtn = KP.dom.q(el, 'p-note-open');
    if (openNoteBtn) openNoteBtn.addEventListener('click', () => openNote());

    KP.dom.q(el, 'p-close').addEventListener('click', () => {
      // 先关(它可能被草稿那道确认拦住),关成了才动 hash 与图 —— 反过来的话,
      // 用户点「取消」之后 hash 已经落到主题级了,屏幕右边的面板却还开着。
      if (!close()) return;
      KP.views.shell.writeHash(true);
      KP.views.graph.renderGraph();
    });
    // 设计稿 §18 的入口:展开左下角助手,并把输入焦点给过去。
    const learn = KP.dom.q(el, 'p-learn');
    if (learn) learn.addEventListener('click', () => {
      KP.views.assistant.openFor(n.id);
      const input = KP.dom.q(KP.views.shell.assistant(), 'a-input');
      if (input) input.focus();
    });
    KP.dom.q(el, 'p-mastery').addEventListener('click', () => setMastery(n.status !== 'mastered'));
    const dismiss = KP.dom.q(el, 'p-dismiss');
    if (dismiss) dismiss.addEventListener('click', () => setMastery(false, true));
    KP.dom.qa(el, '.pt').forEach((p) => p.addEventListener('click', () => {
      // 要点芯片 → 助手里预填一个问题。**不在这里建输入框** —— 那正是要消灭的第二处。
      KP.views.assistant.askAbout(n.id, '请讲讲：' + p.getAttribute('data-ask'));
    }));
    // 提纲的每一条 = 这个知识点里的一块,点它就着这一点去问助手(和要点芯片同一条路)。
    KP.dom.qa(el, '.ol-item').forEach((item) => item.addEventListener('click', () => {
      KP.views.assistant.askAbout(n.id, '请讲讲：' + item.getAttribute('data-ask'));
    }));
    const olRetry = KP.dom.q(el, 'p-ol-retry');
    if (olRetry) olRetry.addEventListener('click', () => ensureOutline(n.id));
    // 「重新生成」= 明确要求换一版(花一次模型调用)。它与 `p-ol-retry` 是同一件事的
    // 两个入口,区别只在**屏幕上有没有旧的那一列**:没有是「失败重试」,有是「换一版」。
    const olRegen = KP.dom.q(el, 'p-ol-regen');
    if (olRegen) olRegen.addEventListener('click', () => ensureOutline(n.id, true));
    KP.dom.qa(el, '.rel').forEach((r) => r.addEventListener('click', () => {
      open(r.getAttribute('data-rel'));
    }));
  }

  /** 本地打补丁:点亮状态变了就立刻反映到图与侧栏,不必等一次整图 GET。 */
  function patchGraphNode(node) {
    const S = KP.S;
    if (!S.graph) return;
    S.graph.nodes = (S.graph.nodes || []).map((n) => (n.id === node.id ? { ...n, ...node } : n));
  }

  /**
   * 一轮对话结束后把**这一个**节点重新拉一次。
   *
   * 后端在轮次一开始就自增 `chat_turns`(`session.py:75`,在 LLM 调用**之前**),
   * 而前端手里的 `S.graph` 还是进页面时那份 —— 不重拉的话,聊完一轮图上依然写着
   * 「未学」,要等下一次整图加载才变成「学习中」。而「学习 = 点亮知识图谱」这件事
   * 的全部价值就在于**当下看得见**。
   *
   * 用一次 GET 而不是在前端 `+1`:请求压根没到服务端时(断网)fetch 会 reject,
   * 那时轮数并没有涨 —— 前端自己加就成了编造。一次 GET 换一个准确的数字。
   *
   * 节点已被切走(`S.nodeId` 变了)就整个放弃:这次更新的主体已经不是屏幕上那个了。
   */
  async function refreshNode(nodeId) {
    const S = KP.S;
    if (S.nodeId !== nodeId) return;
    try {
      const fresh = await KP.api.getNode(nodeId);
      if (S.nodeId !== nodeId) return;
      Object.assign(S.node, fresh);
      patchGraphNode(S.node);
      // 右栏也重画 —— 「已对话 N 轮」就在这里,不重画的话聊完一轮它还是 0。
      render();
      if (!KP.views.graph.patchNodeEl(S.node)) KP.views.graph.renderGraph();
    } catch (err) {
      // 取不到就保持现状:图上少一次状态更新,比弹一个与对话无关的报错好。
    }
  }

  async function setMastery(mastered, dismiss) {
    const S = KP.S;
    const nodeId = S.nodeId;
    if (!nodeId || S.busyChat) return;
    S.busyChat = nodeId;
    try {
      const res = await KP.api.setMastery(nodeId, mastered);
      S.node.status = res.status;
      if (res.status !== 'recommended') { S.node.recommend_reason = ''; S.node.confidence = 0; }
      if (res.status !== 'mastered') S.node.mastered_at = '';
      patchGraphNode(S.node);
      render();
      // **只 patch 那一个盒子,不全量重绘。** 全量重绘会清掉缩放/平移,并把画布上
      // 所有动画的相位归零 —— 而「点亮」正是设计稿里那个要有完成动画的动作。
      // 返回值 false 表示那个盒子不在当前画面上(被筛掉了、或布局换过了),
      // 这时才退回全量渲染;静默什么也不做是最糟的:用户会看到「点了但图上没变」。
      if (!KP.views.graph.patchNodeEl(S.node)) KP.views.graph.renderGraph();
      // 助手上的「本主题已点亮 m / n」也跟着更新。
      if (KP.views.assistant) KP.views.assistant.render();
      await KP.views.shell.refreshProgress();
    } catch (err) {
      alert('操作失败：' + err.message);
    } finally {
      S.busyChat = null;
      if (dismiss) KP.views.shell.loadTopic();
    }
  }

  group.inspector = { open, close, render, renderPlaceholder,
                      setMastery, masteryAction, patchGraphNode, refreshNode,
                      ensureOutline, statusHTML, relatedHTML, sourcesHTML, outlineHTML,
                      // 「讲解记录」页(走查反馈 ④):模板与三个动作
                      noteHTML, openNote, closeNote, saveNote };
})(window.KP = window.KP || {});
