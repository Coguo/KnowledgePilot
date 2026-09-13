/**
 * 画布视口:缩放 / 平移 / 适应画布 / 点击-拖动判定。
 *
 * **为什么单独一个文件,而不是塞进 `views/graph.js`**:这个文件里真正难的部分全是
 * **纯数字函数**(`clampScale` / `scaleAfter` / `fitScale` / `dragExceeds`),它们没有
 * 浏览器也能断言;而 `views/graph.js` 里的其余部分全在拼字符串。混在一起的话,缩放
 * 数学就成了「只能靠人眼看」的东西 —— 而缩放数学恰恰是最容易差一点(比如 0.4 与 0.4
 * 的钳制方向反了,要缩到底才会发现)的地方。
 *
 * **视图模型:`改 SVG 的 width/height`,不是 CSS `transform`。** 理由见计划 D5:
 * ① `centerOn()` 靠 `scrollLeft/scrollTop`,一行都不用改;
 * ② 点击命中测试交给浏览器 —— 缩放/平移过的坐标里,「点到哪个盒子」如果自己做逆变换,
 *    就成了另一份必须与渲染层保持一致的几何,而它错了只会表现为「点这里选中了旁边那个」;
 * ③ 规避各浏览器对 `transform` 是否计入 `scrollWidth` 的分歧。
 *
 * 于是这里有两条不变量:**`viewBox` 永远不跟着缩放变**(变的是 width/height 属性),
 * 以及**缩放后要保住光标下那个点**(见 `scaleAfter` 的调用点)。前者错了图会糊,后者
 * 错了图会把用户正在看的地方缩跑到屏幕外。
 */
(function (KP) {
  'use strict';

  const graph = (KP.graph = KP.graph || {});

  // 缩放下限 0.4:再小节点的字就不可读了,而图本身在 100% 下就已经能滚 —— 缩到 0.2
  // 除了「看得见全貌」以外没有价值,而全貌有专门的一键「适应画布」。
  const MIN_SCALE = 0.4;
  const MAX_SCALE = 2.5;
  const STEP = 1.2;

  // 点击 vs 拖动的阈值(px)。**4 而不是 0**:触控板/鼠标按下时几乎不可能零位移,
  // 阈值太小会让每一次「点击选中节点」都变成拖动、于是永远选不中。
  const DRAG_SLOP = 4;

  /**
   * 缩放到合法区间。
   *
   * **非正数一律退化成 1,而不是钳到 `MIN_SCALE`。** 这一条是有意的:`null`、`''`、
   * 缺字段都会 `Number()` 成 `0` —— 它们的意思是「没设过」,而「没设过」的正确答案是
   * 100%,不是 40%。真钳到下限的话,一次坏状态会让图缩成一小团,而 40% 看起来
   * **像是有人特意缩的**,没人会想到是缺了个字段。负数和 0 同理:它们从来不是
   * 合法的缩放比,当「脏数据」处理比当「想缩到最小」处理更接近真相。
   */
  function clampScale(s) {
    const n = Number(s);
    if (!Number.isFinite(n) || n <= 0) return 1;
    return Math.min(MAX_SCALE, Math.max(MIN_SCALE, n));
  }

  /**
   * 一步缩放。`dir > 0` 放大,否则缩小。
   *
   * **先钳再乘**:直接 `clampScale(cur * STEP)` 在 `cur` 已经是 2.5 时得到同一个值,
   * 行为上没错;但先钳能保证「传进来的 cur 是脏的(比如 1e9)」时结果也一样 ——
   * 而脏 cur 正是从 `S.view.scale` 这个可变状态里最可能出现的东西。
   */
  function scaleAfter(cur, dir) {
    return clampScale(clampScale(cur) * (dir > 0 ? STEP : 1 / STEP));
  }

  /**
   * 适应画布的比例。**永远不超过 1** —— 「适应画布」的意思是「让我看见全部」,
   * 不是「把图放大到填满」。一张 3 个节点的小图被放大到 2.4 倍会很吓人,而且
   * 用户想回去得先按 100% 再自己找位置。
   *
   * 减法在**容器**上做:160px 是给四角留的余量,否则贴边的节点框会被工具栏压住。
   * 容器尺寸拿不到(0 / NaN)→ 返回 1,免得除出 Infinity。
   */
  function fitScale(w, h, viewW, viewH, pad) {
    const cw = Number(viewW);
    const ch = Number(viewH);
    const gw = Number(w);
    const gh = Number(h);
    if (!(gw > 0) || !(gh > 0) || !(cw > 0) || !(ch > 0)) return 1;
    const p = pad === undefined ? 40 : Number(pad) || 0;
    const availW = cw - 2 * p;
    const availH = ch - 2 * p;
    // 容器比留白还小(窗口被拖到很窄)时 availW/H 为负 → min 为负 → 会被钳到 MIN。
    // 那不是「适应」,是不小心缩到最小,但至少画面上还有东西,且用户还能按 100%。
    return clampScale(Math.min(availW / gw, availH / gh, 1));
  }

  /** 本次拖动是否已经超过「算点击」的范围。 */
  function dragExceeds(dx, dy, slop) {
    const s = slop === undefined ? DRAG_SLOP : Number(slop) || 0;
    return Math.abs(Number(dx) || 0) > s || Math.abs(Number(dy) || 0) > s;
  }

  /**
   * 缩放后要设的 `scrollLeft` / `scrollTop`,让**内容坐标系里 `(cx, cy)` 那个点
   * 停在屏幕上原来的位置**。纯函数。
   *
   * 推导:设内容坐标 x,屏幕上它在 `x - scrollLeft`。缩放前后要同一个 x 停在同一个
   * 屏幕位置 c:`x·k - scrollLeft' = x - scrollLeft`,而 `x = scrollLeft + c`。代入得
   * `scrollLeft' = (scrollLeft + c)·k - c`。
   *
   * **必须只有一份实现。** 滚轮(以光标为中心)与工具栏按钮(以视口中心为中心)
   * 走的是同一个式子,区别只在传进来的 c;分成两份的话,按钮那条会写成
   * `scrollLeft * k` —— 在小比例下看起来没错,缩到 2.5 倍时就明显偏了。
   */
  function scrollAfterZoom(scrollLeft, scrollTop, cx, cy, k) {
    const sl = Number(scrollLeft) || 0;
    const st = Number(scrollTop) || 0;
    const x = Number(cx) || 0;
    const y = Number(cy) || 0;
    const r = Number(k);
    const ratio = Number.isFinite(r) && r > 0 ? r : 1;
    return {
      left: (sl + x) * ratio - x,
      top: (st + y) * ratio - y,
    };
  }

  /**
   * 把缩放乘到 SVG 的 width/height **属性**上。`viewBox` 不动 —— 这正是整张图
   * 跟着变大的原因(坐标系没变,是坐标系里的每个单位变大了)。
   */
  function applyScale(svgEl, w, h, scale) {
    if (!svgEl || typeof svgEl.setAttribute !== 'function') return;
    const s = clampScale(scale);
    const gw = Number(w) || 0;
    const gh = Number(h) || 0;
    svgEl.setAttribute('width', String(Math.round(gw * s)));
    svgEl.setAttribute('height', String(Math.round(gh * s)));
  }

  // ---- 交互装配 ----------------------------------------------------------------

  // 模块级:一次只有一处拖动,而 `dragged()` 是给 `views/graph.js` 的点击处理器读的。
  // 不放在闭包里的局部变量,是因为点击处理器在另一个文件里。
  let drag = null;
  let moved = false;
  /** window 级的「拖动中切窗口」兜底只挂一次,见 `wire()` 末尾。 */
  let windowBlurBound = false;
  function onWindowBlur() { drag = null; }

  /** 上一次指针操作是否是一次拖动(而非点击)。视图的点击处理器据此决定要不要选中。 */
  const dragged = () => moved;

  /**
   * 绑上指针与滚轮。**幂等** —— `renderGraph()` 每次全量重建 DOM,但绑定的是
   * `#graph-scroll` 这个**容器**(它由 `view()` 一次性产出,不随图重建),所以
   * 重复绑就会让一次拖动跑两遍处理器(`scrollLeft` 被减两次,拖动速度翻倍)。
   * 用元素上的标记位挡住,而不是靠调用方自觉 —— 调用方正是那个会重建 DOM 的地方。
   */
  function wire(scroller, opts) {
    const o = opts || {};
    if (!scroller || typeof scroller.addEventListener !== 'function') return;
    if (scroller.__kpCanvasWired) return;
    scroller.__kpCanvasWired = true;

    const onDown = (e) => {
      // 只响应主键。右键(0 以外)与中键留给浏览器/未来的上下文菜单。
      if (e.button !== undefined && e.button !== 0) return;
      drag = {
        x: e.clientX,
        y: e.clientY,
        left: scroller.scrollLeft,
        top: scroller.scrollTop,
      };
      moved = false;
      // 指针捕获:拖出容器外(比如拖到右栏上)仍能收到 move,否则松手事件丢了,
      // `drag` 永远不为 null,下一次移动会以旧原点继续拖 —— 表现为「图自己漂」。
      if (typeof scroller.setPointerCapture === 'function' && e.pointerId !== undefined) {
        try { scroller.setPointerCapture(e.pointerId); } catch (err) { /* 捕获失败不影响拖动 */ }
      }
    };

    const onMove = (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x;
      const dy = e.clientY - drag.y;
      if (!moved && dragExceeds(dx, dy)) moved = true;
      // 还没到阈值就不动 —— 否则「点击」会带出 1~3px 的位移,松手后图比点之前偏了一点。
      if (!moved) return;
      scroller.scrollLeft = drag.left - dx;
      scroller.scrollTop = drag.top - dy;
      if (typeof e.preventDefault === 'function') e.preventDefault();
    };

    const onUp = () => {
      drag = null;
      // **`moved` 不在这里清** —— 它要活到随后的 `click` 事件(在 pointerup 之后派发),
      // 由点击处理器消费。下一次 `pointerdown` 才把它归零。
    };

    /**
     * 滚轮。**默认滚轮 = 平移(交给浏览器),Ctrl/⌘+滚轮 = 缩放。**
     *
     * 三栏应用里劫持普通滚轮会让人烦躁:用户想「往下看看」的时候图突然放大,
     * 而放大会改变他正在看的位置。设计稿的四个按钮已经覆盖了「我要缩放」的显式意图。
     */
    const onWheel = (e) => {
      if (!e.ctrlKey && !e.metaKey) return;
      if (typeof e.preventDefault === 'function') e.preventDefault();
      onZoom(e.deltaY < 0 ? 1 : -1, e);
    };

    /** 以**光标为不动点**缩放(修正的推导见 `scrollAfterZoom`)。 */
    function onZoom(dir, e) {
      const svgEl = o.svg ? o.svg() : null;
      const layout = o.layout ? o.layout() : null;
      if (!svgEl || !layout) return;
      const before = o.scale();
      const after = scaleAfter(before, dir);
      if (after === before) return; // 已到边界:不要产生一次没有变化的滚动修正
      const k = after / before;

      const rect = typeof scroller.getBoundingClientRect === 'function'
        ? scroller.getBoundingClientRect()
        : { left: 0, top: 0 };
      const cx = (Number(e && e.clientX) || 0) - (Number(rect.left) || 0);
      const cy = (Number(e && e.clientY) || 0) - (Number(rect.top) || 0);

      o.commit(after);
      applyScale(svgEl, layout.width, layout.height, after);
      const at = scrollAfterZoom(scroller.scrollLeft, scroller.scrollTop, cx, cy, k);
      scroller.scrollLeft = at.left;
      scroller.scrollTop = at.top;
    }

    scroller.addEventListener('pointerdown', onDown);
    scroller.addEventListener('pointermove', onMove);
    scroller.addEventListener('pointerup', onUp);
    // `pointercancel`(系统抢走了指针,比如触控板手势)也要按「松手」处理,
    // 否则 `drag` 挂着不清,回来后图会跟着鼠标自己漂。
    scroller.addEventListener('pointercancel', onUp);
    scroller.addEventListener('wheel', onWheel, { passive: false });

    // 拖动中切窗口(Alt+Tab)时 `pointerup` 不会派发到我们这里 —— 补一个 window 级的
    // 兜底。**它必须整个会话只注册一次**:`renderGraph()` 每次重新渲染都会调 `wire()`,
    // 每次都挂一遍的话,一次会话下来 window 上会积起成百上千个同样的监听器。
    // 它们做的事幂等(只是把 `drag` 清空),所以不会表现出 bug —— 正因如此才更该现在挡住。
    // 上面那个 `__kpCanvasWired` 标记挡不住这条:`scroller` 每次都是新元素。
    if (!windowBlurBound && typeof window !== 'undefined' && window.addEventListener) {
      windowBlurBound = true;
      window.addEventListener('blur', onWindowBlur);
    }
  }

  graph.canvas = {
    MIN_SCALE, MAX_SCALE, STEP, DRAG_SLOP,
    clampScale, scaleAfter, fitScale, dragExceeds, applyScale, scrollAfterZoom,
    wire, dragged,
  };
})(window.KP = window.KP || {});
