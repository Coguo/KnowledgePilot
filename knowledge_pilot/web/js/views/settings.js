/**
 * 设置页 —— **纯客户端**。
 *
 * 设计稿 §6 的侧栏里有「设置」,但后端**没有任何配置端点**:`/api/learning/*` 全是
 * 主题与知识点,`/api/chat` 是一次性问答。所以这一页只能放两类东西:
 *
 *   1. **本机偏好**(`KP.prefs`,走 `localStorage`)。刷新后还在,换台机器就没有 ——
 *      页面上明说了这一点,不让用户以为它跟着账号走。
 *   2. **真的会打到后端的动作**:`重置当前探索`(真实 `DELETE /topics/{id}`)、
 *      `重新研究并生成`(真实 SSE 生成)、研究问答的轮次上限(真实请求参数)。
 *
 * **禁止在这一页新增任何「读服务端配置」的调用。** 计划里对第 7 阶段的约束是
 * 「纯前端」:一个看起来能设置服务端、实际只是本地值、刷新后与服务端真实行为不符
 * 的开关,比没有这个开关糟得多 —— 用户会照着它去理解系统的行为。
 *
 * 「重新研究并生成」为什么在这儿(设计稿 §23 要求撤掉顶栏那个显眼的按钮):
 * 重跑会按新的 `order_index` 重算 `note_path`(`service.py:82`),用户写在正文里的
 * 「## 我的笔记」会留在磁盘上成孤儿、界面上再也看不到。这不是缺陷,是「一次构建」
 * 这个定位的代价 —— 它该是一个需要读一行说明才按得下去的动作,而不是顶栏上
 * 一个每天路过的按钮。
 */
(function (KP) {
  'use strict';

  const group = (KP.views = KP.views || {});

  function stage() { return KP.views.shell.stage(); }

  /**
   * 轮次上限的可选项。`0` = 不设(用服务端 `.env` 的值)。
   *
   * 上限写成固定的几档而不是一个自由输入框:后端会把请求值 `clamp_rounds` 到
   * `[1, agent_rounds_hard_cap]`,而那个硬上限是**服务端配置**(默认值会随 .env 变),
   * 前端报不出一个准确的「最大」。给几档常用的,比让用户填一个 7 然后被悄悄钳成 5 好。
   */
  const ROUND_CHOICES = [0, 1, 2, 3, 4, 6];

  const roundsHTML = (cur) => ROUND_CHOICES.map((n) =>
    `<option value="${n}"${n === cur ? ' selected' : ''}>` +
    `${n ? n + ' 轮' : '用服务端配置'}</option>`).join('');

  /** 读当前的轮次偏好。`0` = 没设过 —— 与「选了 0」是同一件事,不必区分。 */
  function readRounds() {
    const n = Number(KP.prefs.get('research.maxToolRounds', 0));
    return Number.isFinite(n) && n > 0 ? n : 0;
  }

  /** 主题概览卡。**没有选中主题时返回一句说明,而不是一张 0% 的空卡。** */
  function topicCardHTML(topic) {
    if (!topic) {
      return `<section class="card s-card">
        <h3>当前探索</h3>
        <p class="s-note">还没有选中主题。在左侧「我的探索」里点一个，或者在上面输入一个新的想学的主题。</p>
      </section>`;
    }
    const p = topic.progress || {};
    const total = Number(p.total) || 0;
    const mastered = Number(p.mastered) || 0;
    // 百分比只读后端的 `percent`(银行家舍入,见 `KP.pct`)—— 自算会让这里与
    // 侧栏的进度卡差 1%。
    const percent = KP.clamp(KP.pct(p), 0, 100);
    return `<section class="card s-card">
      <h3>当前探索</h3>
      <p class="s-title">${KP.esc(topic.title || '未命名主题')}</p>
      <div class="pc-row">
        <div class="bar"><i style="width:${percent}%"></i></div>
        <span class="pc-pct">${percent}%</span>
      </div>
      <div class="pc-sub">${total
        ? `已点亮 <strong>${mastered} / ${total}</strong> 个节点`
        : KP.topicLabel(topic.status)}</div>
      <div class="s-actions">
        <button class="btn" id="st-regen" type="button">重新研究并生成</button>
        <button class="btn danger" id="st-reset" type="button">重置当前探索</button>
      </div>
      <p class="s-note">
        <strong>重新研究并生成</strong>：同名知识点的学习进度与讲解记录会保留，但节点顺序可能变化 ——
        写在正文里的「## 我的笔记」会因为文件名变了而在界面上看不到（文件仍在磁盘上）。<br>
        <strong>重置当前探索</strong>：知识图谱、学习进度与全部对话记录都会被删除，不能撤销；
        已生成的 Markdown 正文文件会保留在磁盘上。
      </p>
    </section>`;
  }

  /**
   * 整页骨架。**纯函数** —— 事件在 `render()` 里绑。
   *
   * @param {object} topic  当前主题(取不到传 null)
   * @param {number} rounds 当前的轮次上限偏好(0 = 用服务端配置)
   * @param {boolean} stored 偏好上一次写盘是否成功。`false` 时页面上要说明
   *        「这次改的只在本会话有效」—— 悄悄不生效比报错更难查。
   */
  function pageHTML(topic, rounds, stored) {
    return `<div class="topbar">
        <h2>设置</h2>
        <span class="timer">这些偏好只存在这台浏览器里 — 服务端没有配置接口</span>
      </div>
      <div class="settings">
        ${topicCardHTML(topic)}
        <section class="card s-card">
          <h3>研究问答</h3>
          <label class="s-row" for="st-rounds">
            <span>单次研究的工具调用轮次上限</span>
            <select id="st-rounds">${roundsHTML(rounds)}</select>
          </label>
          <p class="s-note">
            只影响「对话」页那种一次性研究问答，不影响学习图谱的生成。
            ${stored ? '' : '<br><strong>这个浏览器不允许本地存储，设置只在本次会话有效。</strong>'}
          </p>
        </section>
        <section class="card s-card">
          <h3>提示</h3>
          <button class="btn" id="st-onboard" type="button">重新显示「首次进入」提示</button>
          <p class="s-note">按下之后，下次打开一张已经建好的知识图谱时会再显示一次那张引导卡片。</p>
        </section>
      </div>`;
  }

  function render() {
    const el = stage();
    const S = KP.S;
    if (!el) return;
    el.innerHTML = pageHTML(KP.currentTopic(S.topics, S.topicId), readRounds(),
                            KP.prefs.get('storageOk', true) !== false);

    const sel = KP.dom.q(el, 'st-rounds');
    if (sel) sel.addEventListener('change', () => {
      const n = Number(sel.value) || 0;
      const ok = n > 0 ? KP.prefs.set('research.maxToolRounds', n)
                       : KP.prefs.remove('research.maxToolRounds');
      KP.prefs.set('storageOk', ok);
      // 写不进去(无痕模式 / 策略禁用)时**当场把话说清楚**,而不是让用户改完
      // 一个看起来生效、刷新就没了的下拉框。
      render();
    });

    const regen = KP.dom.q(el, 'st-regen');
    if (regen) regen.addEventListener('click', () => regenerate());

    const reset = KP.dom.q(el, 'st-reset');
    if (reset) reset.addEventListener('click', () => resetTopic());

    const onboard = KP.dom.q(el, 'st-onboard');
    if (onboard) onboard.addEventListener('click', () => {
      KP.prefs.remove('onboarded');
      onboard.disabled = true;
      onboard.textContent = '好了 — 下次打开图谱时会再显示';
    });
  }

  /**
   * 重新研究并生成。**先回到图谱视图再生成** —— `generate()` 把过程日志与图谱大纲
   * 写进**右栏** `#panel` 里那张生成卡(`views/graph.js` 的 `generateCardHTML`),
   * 而这一屏正占着 `#panel`。不先切走的话,生成过程会把这一页顶掉,用户以为设置崩了。
   *
   * (走查反馈 ④ 之后卡片从 `#stage` 搬到了 `#panel`,报告也换成了图谱大纲 ——
   * 原来这里写的是「把报告逐字写进 `#stage`」,两处都过时了。)
   *
   * `S.route` 不必在这里改 —— `writeHash()` 自己会把它落成「图谱视图」
   * (走查反馈 ③ 的修复:`replaceState` 不触发 `hashchange`,所以 `S.route`
   * 只能由写入点负责)。这里此前手工补过一次的那行因此撤掉了。
   */
  async function regenerate() {
    const S = KP.S;
    if (!S.topicId || S.busyGenerate) return;
    if (!confirm('重新研究并生成？\n' +
                 '同名知识点的学习进度与讲解记录会保留。\n' +
                 '但节点顺序可能变化，写在正文里的「## 我的笔记」会因为文件名变了而在界面上看不到（文件仍在磁盘上）。')) return;
    // 关掉节点详情会**再问一次** —— 这一屏的「我的笔记」里可能有没保存的草稿
    // (走查反馈 ④)。被拒绝时就整个中止,而不是继续往下跑:用户说的正是「别丢我的
    // 草稿」,而这次重新生成会让那些笔记从界面上消失(文件名变了,见上面那句提示)。
    // `S.nodeId` 由 `close()` 自己清 —— 先清的话,用户一点「取消」就留下「图里没有
    // 选中节点、右栏还挂着那个节点详情」的错位状态。`false` 归 `close()`,不归这里。
    if (!KP.views.inspector.close()) return;
    KP.views.shell.writeHash();
    KP.views.shell.renderSidebar();
    await KP.views.graph.generate();
  }

  /**
   * 重置当前探索。**这是一次真实的 `DELETE`**,不是「清一下本地状态」——
   * 本地清掉而服务端还在的话,刷新一次知识图谱就全回来了,而用户以为自己删掉了。
   *
   * 删完之后走 `route()` 而不是直接 `render()`:路由那一条路径还要负责清 hash、
   * 收起右栏、重画侧栏与助手,漏掉任何一件都会留下半截旧界面。
   */
  async function resetTopic() {
    const S = KP.S;
    const id = S.topicId;
    if (!id) return;
    const t = KP.currentTopic(S.topics, id);
    if (!confirm(`重置当前探索？\n「${t ? t.title : id}」的知识图谱、学习进度与全部对话记录` +
                 '都会被删除，这一步不能撤销。\n已生成的 Markdown 正文文件会保留在磁盘上。')) return;
    try {
      await KP.api.deleteTopic(id);
    } catch (err) {
      alert('重置失败：' + err.message);
      return;
    }
    S.topicId = null; S.graph = null; S.node = null; S.messages = [];
    KP.views.inspector.close();
    KP.views.shell.writeHash();
    await KP.views.shell.loadTopics();
    await KP.views.shell.route();
  }

  group.settings = { pageHTML, topicCardHTML, roundsHTML, readRounds,
                     render, regenerate, resetTopic };
})(window.KP = window.KP || {});
