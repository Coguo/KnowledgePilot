/**
 * 图谱布局。**纯几何** —— 输入节点/边,输出坐标,不认识 DOM。
 *
 * 不用力导向:确定性、可断言、与「由浅入深」语义同构;力导向的物理循环会抖动、
 * 让点击目标乱跑。`depth` 由后端算好(`path.py` 的 `depth = max(前置 depth)+1`),
 * 这里只做像素映射。
 *
 * 输出的 `paths[]` 里已经带了 `d`(SVG path 字符串),渲染器只做字符串拼接 ——
 * 这样「每条边有没有箭头」「坐标有没有 NaN」都能在 Node 里断言,不必开浏览器。
 *
 * ## 两种布局,一个契约
 *
 * `layered`(分层/Sugiyama-lite)与 `radial`(径向)返回**同一个形状**:
 *
 *   { pos: Map<id,{x,y,w,h}>, paths: [{id,source,target,from,to,back,d}],
 *     width, height, depths, marks: [{x,y,level}], mode }
 *
 * `depths` 是图里出现过的层号(升序)—— 也就是契约里的 levels,沿用旧名以免动渲染器。
 * 渲染器因此完全不关心跑的是哪个布局。**`marks` 是层标注的几何锚点** —— 布局只给
 * `{x,y,level}`(`level` 是 0 基层号),「第 N 层」这几个字由视图层拼:布局不该知道
 * 文案,视图不该知道几何。径向布局的 `marks` 是空的,原因写在 `radial()` 里。
 *
 * 为什么要有 `choose()`:纯径向在**降级链**上会退化成笑话。LLM 抽取失败时
 * `nodes_from_headings` 产出的是纯线性章节链(`depth 0..n-1`,每层 1 个节点),
 * 12 个节点 → 12 个同心环、每环只站 1 个、最外环半径上千像素,90% 圆周是空的 ——
 * 而那条路径存在的意义正是「至少让用户看到点东西」。
 */
(function (KP) {
  'use strict';

  const NODE_W = 158, NODE_H = 54, GAP_X = 98, GAP_Y = 22, PAD = 26;

  /**
   * 径向布局里相邻两环的**最小半径差**。取盒子的对角线是为了拿到一个充分条件:
   * 两轴对齐矩形不重叠 ⟺ `|dx| ≥ w` 或 `|dy| ≥ h`;由 `|dx|²+|dy|² = 距离²` 可知
   * 「中心距 ≥ 对角线」蕴含「不可能同时 |dx| < w 且 |dy| < h」,于是必然不重叠。
   * 用直径而非 `max(w,h)` 会宽松一点点(少数角度上留白稍多),换来的是一个可证明
   * 而不是「看起来差不多」的不重叠保证 —— 这条性质有单测。
   */
  const DIAG = Math.sqrt(NODE_W * NODE_W + NODE_H * NODE_H) + 12;

  // 全角/CJK 的判据用**码点范围**写,别写字面字符 —— 那种正则没法审阅,
  // 在编辑器里还会被不同字体渲染成一堆看不出边界的符号。
  // U+2E80-9FFF 部首扩展+CJK 统一表意  U+3000-303F 中文标点
  // U+FF00-FFEF 全角形式  U+AC00-D7AF 谚文  U+F900-FAFF 兼容表意
  const WIDE = /[⺀-鿿　-〿＀-￯가-힯豈-﫿]/;

  /** 估算文本宽度:全角按字号,其余按 0.56 字号。只用于折行,不追求精确。 */
  function textWidth(s, size) {
    let w = 0;
    for (const ch of String(s)) {
      w += WIDE.test(ch) ? size : size * 0.56;
    }
    return w;
  }

  /** 按像素宽度折行,最多 maxLines 行,超出部分末行加省略号。 */
  function wrapText(text, size, maxWidth, maxLines) {
    const lines = [];
    let cur = '';
    for (const ch of String(text == null ? '' : text)) {
      if (cur && textWidth(cur + ch, size) > maxWidth) { lines.push(cur); cur = ch; }
      else cur += ch;
    }
    if (cur) lines.push(cur);
    if (lines.length <= maxLines) return lines;
    const kept = lines.slice(0, maxLines);
    let last = kept[maxLines - 1];
    while (last && textWidth(last + '…', size) > maxWidth) last = last.slice(0, -1);
    kept[maxLines - 1] = last + '…';
    return kept;
  }

  /** 节点的层号。脏数据(depth 缺失、负数、字符串)一律归到第 0 层。 */
  const depthOf = (n) => Math.max(0, Math.floor(Number(n && n.depth)) || 0);

  /**
   * 同一层/同一环内的排序:`order_index` 主键 + `name` 次序键。
   *
   * **次序键不能省** —— 正常数据里 `order_index` 唯一,但数据脏时(重复值)没有
   * 次序键就会退化成 `Array.sort` 的实现相关顺序,于是同一份数据两次渲染节点
   * 换位、点击目标乱跑。径向布局会把这个后果放大到刺眼。
   */
  const byOrder = (a, b) =>
    ((a.order_index || 0) - (b.order_index || 0)) ||
    String(a.name).localeCompare(String(b.name));

  /** 按层分组 + 层号升序 + 层内排序。两种布局共用,以保证「同输入同输出」。 */
  function groupByDepth(nodes) {
    const byDepth = new Map();
    (nodes || []).forEach((n) => {
      const d = depthOf(n);
      if (!byDepth.has(d)) byDepth.set(d, []);
      byDepth.get(d).push(n);
    });
    const depths = Array.from(byDepth.keys()).sort((a, b) => a - b);
    depths.forEach((d) => byDepth.get(d).sort(byOrder));
    return { byDepth, depths };
  }

  /**
   * 一个盒子**自身**的中心。名字刻意不叫 `centerOf` —— 那个名字留给下面那个
   * 面向整张图的 `centerOf(graph, layout)`:两者都叫「中心」,但一个是盒子几何、
   * 一个是「这张图的核心节点在哪儿」,混在同一个名字下会让调用点读不出在问什么。
   */
  const boxCenter = (b) => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 });

  /**
   * 从 `box` 的中心朝 `toward` 画射线,**与盒子边界的交点**。两种布局共用。
   *
   * 拆分前这里写死成「源取右边缘中点、目标取左边缘中点」—— 那在分层布局里看起来
   * 还行(连线大致水平),到径向布局就完全错了:节点绕着中心排,连线该指向各个方向。
   * 换成真正的边界求交之后,两种布局通用,而且箭头正好停在框线上。
   */
  function anchor(box, toward) {
    const c = boxCenter(box);
    const dx = toward.x - c.x, dy = toward.y - c.y;
    if (!dx && !dy) return c;
    // 沿各轴走到边界所需的缩放系数,取小的那个 = 最先碰到的那条边。
    const sx = dx ? (box.w / 2) / Math.abs(dx) : Infinity;
    const sy = dy ? (box.h / 2) / Math.abs(dy) : Infinity;
    const s = Math.min(sx, sy);
    return { x: c.x + dx * s, y: c.y + dy * s };
  }

  /** 二次贝塞尔:控制点落在中垂线上,偏移 `bowL` 倍的弦长。用于反向边的视觉弱化。 */
  function bow(from, to, factor) {
    const mx = (from.x + to.x) / 2, my = (from.y + to.y) / 2;
    const dx = to.x - from.x, dy = to.y - from.y;
    const len = Math.hypot(dx, dy) || 1;
    // 法线方向 = 弦旋转 90°。
    const cx = mx + (-dy / len) * len * factor;
    const cy = my + (dx / len) * len * factor;
    return `M${from.x},${from.y} Q${cx},${cy} ${to.x},${to.y}`;
  }

  /**
   * 由布局算出的 `pos` 与一张「哪些边是反向边」的判据,拼出边的 path 列表。
   *
   * `isBack(sourceId, targetId)` 由各布局给出 —— 分层看列号、径向看环号。
   * 反向边只由破环时的强制放行产生(`path.py:219-222`),数量极少,但真出现时
   * 若按正向画,一条线会横穿整张图。
   */
  function buildPaths(edges, pos, isBack, forward) {
    const paths = [];
    (edges || []).forEach((e) => {
      const a = pos.get(e.source_id), b = pos.get(e.target_id);
      if (!a || !b) return;   // 端点不在图里就整条跳过,而不是画一条从 (0,0) 出发的线
      const back = !!isBack(e.source_id, e.target_id);
      const from = anchor(a, boxCenter(b));
      const to = anchor(b, boxCenter(a));
      paths.push({
        id: e.source_id + '>' + e.target_id,
        source: e.source_id, target: e.target_id,
        from, to, back,
        d: forward(from, to, back),
      });
    });
    return paths;
  }

  // ================= 分层布局 =================

  /**
   * 分层布局(Sugiyama-lite):同 `depth` 的节点排成一列,列内按 `order_index` 排。
   * 连线是水平三次贝塞尔 —— 「从左往右、由浅入深」的语义在视觉上最直白。
   */
  function layered(nodes, edges) {
    const { byDepth, depths } = groupByDepth(nodes);

    const tallest = Math.max(1, ...depths.map((d) => byDepth.get(d).length));
    const height = PAD * 2 + tallest * NODE_H + (tallest - 1) * GAP_Y;
    const colOf = new Map();
    const pos = new Map();
    depths.forEach((d, col) => {
      const layer = byDepth.get(d);
      layer.forEach((n) => colOf.set(n.id, col));
      const layerH = layer.length * NODE_H + (layer.length - 1) * GAP_Y;
      const top = PAD + (height - PAD * 2 - layerH) / 2;
      layer.forEach((n, row) => {
        pos.set(n.id, { x: PAD + col * (NODE_W + GAP_X),
                        y: top + row * (NODE_H + GAP_Y), w: NODE_W, h: NODE_H });
      });
    });
    const width = PAD * 2 + Math.max(1, depths.length) * NODE_W +
                  Math.max(0, depths.length - 1) * GAP_X;

    // 反向边 = 目标列不在源列右侧。正常数据里不会出现(`depth` 由前置推出来),
    // 只有破环时才会有,所以判据用「≤」而不是「<」。
    const isBack = (s, t) => (colOf.get(t) || 0) <= (colOf.get(s) || 0);
    const paths = buildPaths(edges, pos, isBack, (from, to) => {
      const dx = Math.max(30, (to.x - from.x) * 0.45);
      return `M${from.x},${from.y} C${from.x + dx},${from.y} ${to.x - dx},${to.y} ${to.x},${to.y}`;
    });

    const marks = depths.map((d, col) => ({
      x: PAD + col * (NODE_W + GAP_X), y: 14, level: d,
    }));

    return { pos, paths, width, height, depths, marks, mode: 'layered' };
  }

  // ================= 径向布局 =================

  /**
   * 径向布局:环层 = `depth`(保住「由浅入深」的拓扑语义),环内按 `order_index`
   * 均分角度(确定性、可断言)。**单根时根节点恰好落在圆心** —— 设计稿要的
   * 「中心节点 = 最基础的那一个」不需要合成元素,那个位置本来就是它。
   *
   * 角度从正上方(-90°)起,再偏移**半个步长**:这样 n ≥ 2 时没有任何节点正好落在
   * 正上方,相邻两环的节点也不会在视觉上叠成一条竖线。
   *
   * 环半径的取法见 `DIAG` 的注释 —— 「中心距 ≥ 对角线」是不重叠的充分条件,
   * 所以 `r(n) = DIAG / (2·sin(π/n))`(圆周长够放下 n 个盒子)且
   * `r(d) ≥ r(d-1) + DIAG`(相邻环之间也留够)。半径只与**层数与环内节点数**有关,
   * 与节点名长度无关 —— 名字再长也不该把整张图撑变形。
   */
  function radial(nodes, edges) {
    const { byDepth, depths } = groupByDepth(nodes);

    const radius = new Map();
    let prev = 0;
    depths.forEach((d, i) => {
      const n = byDepth.get(d).length;
      // 环内要放下 n 个盒子:弦长 2r·sin(π/n) ≥ 对角线。
      const need = n >= 2 ? DIAG / (2 * Math.sin(Math.PI / n)) : 0;
      const r = i === 0 ? need : Math.max(prev + DIAG, need);
      radius.set(d, r);
      prev = r;
    });

    const pos = new Map();
    depths.forEach((d) => {
      const ring = byDepth.get(d);
      const n = ring.length, r = radius.get(d);
      ring.forEach((node, k) => {
        const ang = n === 1 ? -Math.PI / 2
                            : -Math.PI / 2 + Math.PI / n + (2 * Math.PI * k) / n;
        // n === 1 且 r === 0 时落在圆心 —— 那就是「单根」的情形。
        const cx = r * Math.cos(ang), cy = r * Math.sin(ang);
        pos.set(node.id, { x: cx - NODE_W / 2, y: cy - NODE_H / 2, w: NODE_W, h: NODE_H });
      });
    });

    // 把包围盒挪到 (PAD, PAD) —— 圆心在原点,不挪的话有一半坐标是负的。
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    pos.forEach((b) => {
      minX = Math.min(minX, b.x); minY = Math.min(minY, b.y);
      maxX = Math.max(maxX, b.x + b.w); maxY = Math.max(maxY, b.y + b.h);
    });
    if (!pos.size) return { pos, paths: [], width: PAD * 2, height: PAD * 2,
                            depths: [], marks: [], mode: 'radial' };
    const shiftX = PAD - minX, shiftY = PAD - minY;
    pos.forEach((b) => { b.x += shiftX; b.y += shiftY; });

    const depthLookup = new Map();
    (nodes || []).forEach((n) => depthLookup.set(n.id, depthOf(n)));
    // 反向边 = 目标环不在源环外侧。
    const isBack = (s, t) => (depthLookup.get(t) || 0) <= (depthLookup.get(s) || 0);
    const paths = buildPaths(edges, pos, isBack, (from, to, back) => (back
      ? bow(from, to, 0.22)
      // 正向边走直线:径向图里的边就该是从一个框指向另一个框的辐条。
      : `M${from.x},${from.y} L${to.x},${to.y}`));

    // 径向**不画环标注**。环标注要么压在环上的节点上,要么只能挤在环之间那点
    // 缝隙里 —— 两种都需要按具体角度算,而角度随节点数变化,没有稳的落点。
    // 层信息改由图例那句话承担(见 `views/graph.js` 的 header)。
    return {
      pos, paths,
      width: (maxX - minX) + PAD * 2,
      height: (maxY - minY) + PAD * 2,
      depths, marks: [], mode: 'radial',
    };
  }

  // ================= 图的核心在哪儿 =================

  /**
   * 这张图的「中心」——**中心环进度**(设计稿 §12)画的就落在这里。
   *
   * 返回 `{x, y, node}`:`x`/`y` 是**布局坐标**里的圆心,`node` 是**站在那儿的真实
   * 节点**(没有就是 `null`)。与计划 D2 的一处**有意的偏差**:D2 写的是「0 个或
   * ≥2 个 depth-0 时合成一个 topic 中心节点」,这里改成**不合成、也不画环**。
   *
   * 为什么不合成:径向布局的环半径只保证「相邻两环之间 ≥ DIAG」,**原点处没有
   * 留任何位置**。环 0 只有 1 个节点时半径恰好是 0(那个节点就落在圆心),而
   * 2 个节点时半径是 `DIAG/(2·sin(π/2)) = DIAG/2` —— 比一个盒子还窄。往原点硬塞
   * 一个合成盒子,在 n=2、n=3 这类小图上是**必然重叠**,而那正是「星形小图」的
   * 常见形状。要真做得先改 `radial()` 让原点预留半径,那会改动每一条环半径、
   * 于是每一个节点的坐标 —— 为了一张多数图根本用不上的合成中心,不值得。
   *
   * 为什么 `x`/`y` 直接取包围盒中点:径向的节点全部落在**以原点为心**的同心环上,
   * 所以包围盒关于原点对称,「包围盒中点」与「原点」是同一个点(不是近似)。
   * `layered` 没有「中心」这个概念(图从左往右长),返回 `null`。
   *
   * @param {object} graph  `{nodes, edges}` —— 认的是 `depth`
   * @param {object} lay    已算好的布局(`layout()` 的返回值)
   */
  function centerOf(graph, lay) {
    if (!lay || lay.mode !== 'radial') return null;
    const roots = ((graph && graph.nodes) || []).filter((n) => depthOf(n) === 0);
    return {
      x: lay.width / 2,
      y: lay.height / 2,
      node: roots.length === 1 ? roots[0] : null,
    };
  }

  // ================= 选哪个 =================

  /**
   * 按图的形状选布局。判据是**层的密度**:层数占比高 = 形状是一条链,径向会退化成
   * 「每环一个节点的一堆同心圆」;层数少而节点多 = 星形/扇形的健康形态,径向最好看。
   *
   * 阈值 6 层 / 0.55 是经验值。它只影响「哪个更好看」,不影响正确性 ——
   * 两种布局都能画任意图,不重叠与包含性对两者都成立(有单测)。
   */
  function choose(nodes) {
    const list = nodes || [];
    if (!list.length) return 'layered';
    const levels = new Set(list.map(depthOf)).size;
    return (levels > 6 || levels / Math.max(1, list.length) > 0.55) ? 'layered' : 'radial';
  }

  /**
   * 布局分发。`mode` 是这两个值之一就用它,否则由 `choose()` 定 —— 认不出的值
   * 退回自动选择,而不是悄悄变成 layered。拼错一个 mode 字符串就静默换掉整张图,
   * 是那种「看起来能用、只是不对」的失败。
   */
  function layout(nodes, edges, mode) {
    const m = (mode === 'radial' || mode === 'layered') ? mode : choose(nodes);
    return m === 'radial' ? radial(nodes, edges) : layered(nodes, edges);
  }

  KP.layout = {
    layered, radial, choose, layout, anchor, bow, byOrder, depthOf, groupByDepth,
    centerOf, boxCenter, textWidth, wrapText,
    NODE_W, NODE_H, GAP_X, GAP_Y, PAD, DIAG,
  };
})(window.KP = window.KP || {});
