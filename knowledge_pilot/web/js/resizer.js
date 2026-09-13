/**
 * 三栏之间的**可拖拽分隔条** —— 左右两栏的宽度由用户自己定。
 *
 * **为什么不把宽度写死在这里,而走 `opts.commit(kind, px)`:** 这一层只负责算
 * 「拖到了多少」,「写成哪个 CSS 变量、要不要记住」是外壳的决定(`views/shell.js`)。
 * 这样这个文件在 Node 桩里能整条跑通 —— 桩元素没有布局,碰 `el.style` 只会得到
 * 一个与控制流无关的异常,而那种失败看起来像是被测代码错了。
 *
 * 指针骨架与 `graph/canvas.js` 的 `wire()` 同源(捕获要 try/catch、`pointercancel`
 * 要按松手处理、幂等标记写在元素上而不是靠调用方自觉),理由是同一批:拖动这种
 * 「按下→移动→松开」的状态机,**唯一会被用户遇到的失败是「松手之后它还在跟着鼠标
 * 漂」**,而那是丢了 `pointerup` 的后果。
 *
 * 加载期只做 `window.KP.resizer = ...`,不碰 DOM(`tests/js/harness.mjs` 会炸)。
 */
(function (KP) {
  'use strict';

  /**
   * 每一栏宽度的合法区间与默认值。**默认值与 `css/tokens.css` 里的
   * `--side-w` / `--insp-w` 是同一组数字** —— 两边不一致的话,「双击重置」
   * 之后栏宽会跳到一个它自己都不认识的值。
   *
   * 上限的来历:侧栏再窄放不下「我的探索」里的主题标题与进度条;右栏再窄要点芯片
   * 会一个字一行,再宽画布就没地方了 —— 而画布是这个页面的主体(设计稿 §30)。
   */
  const LIMITS = {
    side: { min: 176, max: 420, def: 248 },
    insp: { min: 248, max: 560, def: 300 },
  };

  /** 中间画布至少留这么宽。拖到两边加起来把画布挤没,是这一处唯一会造成实际伤害的拖法。 */
  const MIN_CENTER = 360;

  /** 键盘一次移动多少(方向键)。与拖动共用同一条 `commit` 路径。 */
  const KEY_STEP = 16;

  /**
   * 已绑定的分隔条的「松开」回调。`window` 的 blur 兜底要一次清掉**全部** ——
   * 只清最后绑上的那一条的话,Alt+Tab 之后另一条会一直粘在鼠标上。
   */
  const cancels = [];
  let blurBound = false;

  function limits(kind) {
    return LIMITS[kind] || LIMITS.side;
  }

  /**
   * 把宽度夹到**当前窗口真的放得下**的区间。纯函数。
   *
   * @param {string} kind      'side' | 'insp'
   * @param {number} px        期望宽度(CSS px)
   * @param {number} [avail]   三栏可用的总宽度(`.app` 的宽度)。缺省/非正数时不设
   *                           「给画布留位」这条上限,只按 `LIMITS` 夹 —— 桩和
   *                           旧浏览器里量不到宽度,量不到就不该假装量到了。
   * @param {number} [other]   另一栏当前的宽度;只在 `avail` 给定时参与计算。
   *                           另一栏没被拖过时调用方传默认值 —— 那点误差只影响
   *                           「最多能拖到多少」这一个上限,且只在窗口刚好卡在临界点时看得出来。
   * @returns {number} 整数宽度
   */
  function clampWidth(kind, px, avail, other) {
    const L = limits(kind);
    let v = Number(px);
    // **`null` / `''` / 缺字段是「没设过」,不是 0。** `Number(null)` 恰好等于 0,
    // 拿它去夹会得到下限(176px)—— 一条被挤窄的栏,而真相是偏好里根本没有这一栏。
    // 与 `graph/canvas.js` 的 `clampScale` 同一条规矩:「没设过」和「想缩到最小」
    // 是两个不同的意思,只有显式给了数字(包括 0)才算用户说了话。
    if (px === null || px === undefined || px === '' || !Number.isFinite(v)) v = L.def;
    let max = L.max;
    if (Number.isFinite(avail) && avail > 0) {
      const rest = Number.isFinite(other) && other > 0 ? other : L.def;
      max = Math.min(max, Math.floor(avail - rest - MIN_CENTER));
    }
    // 窗口窄到连 `min` 都放不下时,**下限优先**:留一条挤一点的栏,好过留一条
    // 宽度为负、表现为「看不见」的栏。
    if (max < L.min) max = L.min;
    return Math.round(Math.max(L.min, Math.min(max, v)));
  }

  /**
   * 给一条分隔条绑上拖动、双击重置与方向键。
   *
   * **幂等**:同一个元素重复 `wire` 会直接返回 `false` —— 重复绑会让一次拖动跑两遍
   * 处理器(宽度按两倍速度走),而那种 bug 只在手速上体现得出来。
   *
   * @param {object} handle  分隔条元素。
   * @param {object} opts
   * @param {string} opts.kind     'side' | 'insp'
   * @param {Function} opts.get    () => 当前宽度
   * @param {Function} opts.fit    (px) => 夹好之后的宽度
   * @param {Function} opts.commit (kind, px) => void  落盘 + 写成 CSS 变量
   * @param {Function} [opts.reset] 双击 / Home 键时调用
   * @returns {boolean} 这次调用真的绑上了吗
   */
  function wire(handle, opts) {
    const o = opts || {};
    if (!handle || typeof handle.addEventListener !== 'function') return false;
    if (handle.__kpResizerWired) return false;
    handle.__kpResizerWired = true;

    const kind = o.kind;
    // 「分隔条朝哪边移」→「这一栏的宽度加还是减」。侧栏在左,往右拖 = 变宽;
    // 右栏在右,往右拖 = **变窄**。方向写反的表现是「拖着很别扭」,不会报错。
    const grow = kind === 'insp' ? -1 : 1;

    /** 拖动中的起点。`null` = 现在没在拖。 */
    let drag = null;

    const apply = (px) => { if (o.commit) o.commit(kind, px); };

    const onDown = (e) => {
      if (e.button !== undefined && e.button !== 0) return;
      drag = { x: Number(e.clientX) || 0, start: Number(o.get ? o.get() : 0) || 0 };
      // 与画布拖动同一个理由:指针拖到栏外(比如拖过右栏)时 `pointerup` 还会回来。
      // 捕获失败不影响拖动本身,所以吞掉异常。
      if (typeof handle.setPointerCapture === 'function' && e.pointerId !== undefined) {
        try { handle.setPointerCapture(e.pointerId); } catch (err) { /* 见上 */ }
      }
    };

    const onMove = (e) => {
      if (!drag) return;
      const dx = (Number(e.clientX) || 0) - drag.x;
      // 立刻提交,不做「松手才落盘」的两段式:`commit` 只管写变量与偏好,
      // 两者都是廉价的 —— 而分批会让拖动过程看起来是卡住的。
      apply(o.fit ? o.fit(drag.start + grow * dx) : drag.start + grow * dx);
      // 抑制拖动过程中的文本选择。放在 move 里而不是 down 里:down 里 preventDefault
      // 会连**聚焦**一起挡掉,而这条分隔条是要能用 Tab 聚焦、用方向键调的。
      if (typeof e.preventDefault === 'function') e.preventDefault();
    };

    const onUp = () => { drag = null; };

    const onKey = (e) => {
      const dir = e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowRight' ? 1 : 0;
      if (dir) {
        if (typeof e.preventDefault === 'function') e.preventDefault();
        apply(o.fit ? o.fit((Number(o.get ? o.get() : 0) || 0) + grow * dir * KEY_STEP)
                    : (Number(o.get ? o.get() : 0) || 0) + grow * dir * KEY_STEP);
        return;
      }
      // Home / Enter 当作「重置」—— 双击在触屏上没有,而这条是唯一的触屏复位方式。
      if ((e.key === 'Home' || e.key === 'Enter') && o.reset) {
        if (typeof e.preventDefault === 'function') e.preventDefault();
        o.reset();
      }
    };

    const onDblClick = () => { if (o.reset) o.reset(); };
    const onCancel = () => { drag = null; };

    handle.addEventListener('pointerdown', onDown);
    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onUp);
    // 系统抢走指针(触控板手势、系统弹窗)时不会有 `pointerup`,`drag` 会挂着不清。
    // 那时**悬停移动就会改宽度**(`pointermove` 不需要按键),表现是「分隔条一直
    // 跟着鼠标跑」—— 所以按「松手」处理。
    handle.addEventListener('pointercancel', onCancel);
    handle.addEventListener('dblclick', onDblClick);
    handle.addEventListener('keydown', onKey);

    // 拖动中切窗口(Alt+Tab)时上面前三条都不会派发。**整个会话只注册一次** ——
    // `wire` 幂等,但那条幂等是绑在**元素**上的,而这条绑在 window 上。
    cancels.push(onCancel);
    if (!blurBound && typeof window !== 'undefined' && window.addEventListener) {
      blurBound = true;
      window.addEventListener('blur', () => { for (const fn of cancels) fn(); });
    }
    return true;
  }

  KP.resizer = { LIMITS, MIN_CENTER, KEY_STEP, defaults: { side: LIMITS.side.def, insp: LIMITS.insp.def },
                 clampWidth, wire };
})(window.KP = window.KP || {});
