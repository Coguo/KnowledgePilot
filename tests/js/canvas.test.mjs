/**
 * `js/graph/canvas.js` —— 视口数学与拖动判定。
 *
 * 这一组存在的理由:**缩放/平移的错误几乎不报错,只表现为「手感不对」**。
 * 钳制方向反了要缩到底才发现;光标修正少了,图会把用户正在看的地方缩到屏幕外;
 * 拖动阈值太小则每一次点击都变成拖动、节点永远选不中。三样都不是异常,是错觉。
 *
 * 所以这里全部是纯数字断言,不开浏览器 —— 而「图好不好看」那部分仍然只能人工看。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { loadKP, plain, FakeElement } from './harness.mjs';

const { KP } = await loadKP();
const C = KP.graph.canvas;

// ---- clampScale --------------------------------------------------------------

test('clampScale:钳在 [MIN, MAX],非有限数退化成 1', () => {
  assert.equal(C.clampScale(1), 1);
  assert.equal(C.clampScale(C.MIN_SCALE), C.MIN_SCALE);
  assert.equal(C.clampScale(C.MAX_SCALE), C.MAX_SCALE);
  // 下限**不能**是 0 或负数:0 会让 SVG 的 width 变成 0,图整个消失;
  // 负数在部分浏览器上直接是无效属性 —— 两者都表现为「一片空白」。
  assert.ok(C.MIN_SCALE > 0, '缩放下限必须为正');
  assert.ok(C.MAX_SCALE > C.MIN_SCALE);
  assert.equal(C.clampScale(0.0001), C.MIN_SCALE);
  assert.equal(C.clampScale(99), C.MAX_SCALE);
  // 脏输入:`S.view.scale` 是会被人手写坏的可变状态。
  for (const bad of [NaN, Infinity, -Infinity, null, undefined, 'x', {}]) {
    assert.equal(C.clampScale(bad), 1, `${JSON.stringify(bad)} 没有退化成 1`);
  }
  // 数字字符串要认(后端/JSON 里可能来字符串)。
  assert.equal(C.clampScale('1.5'), 1.5);
});

test('scaleAfter:一步一步地乘,并在边界处停住不越界', () => {
  assert.equal(C.scaleAfter(1, 1), C.STEP);
  assert.equal(C.scaleAfter(1, -1), 1 / C.STEP);
  // 到顶再加 = 到顶(而不是越过去)。调用方靠 `after === before` 判断
  // 「已经到头了,别再修正 scrollLeft」,所以这里必须**同一个数**。
  assert.equal(C.scaleAfter(C.MAX_SCALE, 1), C.MAX_SCALE);
  assert.equal(C.scaleAfter(C.MIN_SCALE, -1), C.MIN_SCALE);
  assert.equal(C.scaleAfter(C.MAX_SCALE + 100, 1), C.MAX_SCALE, '脏 cur 也要先钳再乘');
  // 反复放大缩小回到原点是**近似**的,不是精确的 —— 这说明白写出来,
  // 免得将来谁拿它做往返断言然后困惑。
  const there = C.scaleAfter(C.scaleAfter(1, 1), -1);
  assert.ok(Math.abs(there - 1) < 1e-9);
});

// ---- fitScale ----------------------------------------------------------------

test('fitScale:包住整张图,但**绝不放大**(上限是 1)', () => {
  // 小图:200×100 放进 800×600,两边都能放大(4 倍 / 6 倍)—— 但被 clamp 到 1。
  // 「适应画布」的意思是「让我看见全部」,不是「把图放大到填满」。
  assert.equal(C.fitScale(200, 100, 800, 600), 1, '小图被放大了');
  // 大图:按**较小的那个方向**定比例,保证两边都装得下(这里宽的更紧:0.8 < 1.2)。
  assert.equal(C.fitScale(1000, 500, 800, 600, 0), 0.8);
  // 极端比例会撞到 MIN_SCALE —— 这不是「算错了」,是「这张图在这个窗口里
  // 本来就放不下」,钳制保证画面上至少还有东西、且用户能按 100% 自己找。
  assert.equal(C.fitScale(40000, 3000, 800, 600, 0), C.MIN_SCALE);
});

test('fitScale:容器尺寸拿不到时退化成 1,不产生 Infinity', () => {
  // 首次渲染时 `clientWidth` 可能是 0(容器还没布局完)。除出来的 Infinity
  // 会被 clampScale 兜住,但这条断言把它钉死在「就是 1」—— 因为「适应画布」
  // 在尺寸未知时唯一诚实的答案是「按 1 来」,不是「缩到最小」。
  for (const bad of [[0, 0], [NaN, NaN], [undefined, undefined]]) {
    assert.equal(C.fitScale(1000, 1000, bad[0], bad[1]), 1);
  }
  assert.equal(C.fitScale(0, 0, 800, 600), 1, '图宽高为 0(空图)时也不该乱算');
  assert.equal(C.fitScale(NaN, NaN, 800, 600), 1);
});

test('fitScale:留白算在容器上 —— 贴边的节点不会被工具栏压住', () => {
  // 800 宽的容器、图正好 800 宽:不留白时比例是 1,最右边的节点会紧贴边缘,
  // 而工具栏就在右下角。留 40 的边距之后应当是 (800-80)/800 = 0.9。
  assert.equal(C.fitScale(800, 800, 800, 800, 40), 0.9);
  // pad 传 0 是合法的(测试里要确定性的 1)。
  assert.equal(C.fitScale(800, 800, 800, 800, 0), 1);
});

// ---- dragExceeds -------------------------------------------------------------

test('dragExceeds:4px 以内算点击,超过才算拖动', () => {
  assert.equal(C.dragExceeds(0, 0), false);
  assert.equal(C.dragExceeds(4, 0), false, '阈值上应当是「不超出」');
  assert.equal(C.dragExceeds(0, -4), false);
  assert.equal(C.dragExceeds(5, 0), true);
  assert.equal(C.dragExceeds(-5, 0), true, '负方向同样要判');
  assert.equal(C.dragExceeds(3, 5), true, '任一轴超出就算拖动');
  // **阈值必须为正** —— 0 会让每一次按下都算拖动,于是节点永远选不中:
  // 这是个「界面上什么都没坏,但点不动」的故障。
  assert.ok(C.DRAG_SLOP > 0);
  // 脏输入不抛。
  assert.equal(C.dragExceeds(undefined, undefined), false);
  assert.equal(C.dragExceeds(NaN, NaN), false);
});

// ---- scrollAfterZoom ---------------------------------------------------------

test('scrollAfterZoom:光标下的那个点停在原地(k=1 时什么都不动)', () => {
  // k = 1:没有缩放,滚动位置必须**逐字不变**,否则一次「到边界了」的缩放
  // 会让视图凭空漂一点。
  assert.deepEqual(plain(C.scrollAfterZoom(100, 50, 200, 150, 1)), { left: 100, top: 50 });
});

test('scrollAfterZoom:放大时按光标位置重算 —— 公式反过来写会立刻露馅', () => {
  // 内容坐标 x = scrollLeft + c = 100 + 200 = 300。放大 2 倍后要 x·2 仍在屏幕 200 处:
  // scrollLeft' = 300·2 - 200 = 400。
  assert.deepEqual(plain(C.scrollAfterZoom(100, 50, 200, 150, 2)), { left: 400, top: 250 });
  // 缩小同理(注意 y 用的是内容坐标 50+150=200,不是滚动位置 50):
  // scrollTop' = 200·0.5 - 150 = -50。负值由浏览器钳成 0,不是我们的事。
  assert.deepEqual(plain(C.scrollAfterZoom(100, 50, 200, 150, 0.5)), { left: -50, top: -50 });
  // **错法对照**:`scrollLeft * k` 在 k=1.2、scrollLeft=0 时永远等于 0 ——
  // 看起来「缩小的时候没问题」,放大到 2.5 倍才明显偏移。上面那两条能抓到它。
  assert.notDeepEqual(plain(C.scrollAfterZoom(100, 50, 200, 150, 2)).left, 100 * 2);
});

test('scrollAfterZoom:脏 k 当 1 处理,不产生 NaN', () => {
  for (const bad of [0, -1, NaN, null, undefined, 'x']) {
    assert.deepEqual(plain(C.scrollAfterZoom(100, 50, 200, 150, bad)), { left: 100, top: 50 },
      `k=${JSON.stringify(bad)} 时没有退化成恒等`);
  }
  // 脏滚动位置当 0(而不是 NaN 传下去 —— 赋给 scrollLeft 的 NaN 会被浏览器
  // 静默忽略,于是「缩放之后滚动位置没变」,而那看起来就像修正逻辑没生效)。
  assert.deepEqual(plain(C.scrollAfterZoom(undefined, NaN, 10, 10, 2)), { left: 10, top: 10 });
});

// ---- applyScale --------------------------------------------------------------

test('applyScale:改的是 width/height **属性**,不是 viewBox', () => {
  // 这条契约是整套缩放能成立的前提:`viewBox` 不动,坐标系里每个单位变大,
  // 于是图整体变大而内部几何一个数都不用改。反过来(改 viewBox)会让
  // 所有绝对定位的坐标、`d` 路径、文字大小全部要跟着重算。
  const svg = new FakeElement('svg');
  svg.setAttribute('viewBox', '0 0 400 200');
  C.applyScale(svg, 400, 200, 2);
  assert.equal(svg.getAttribute('width'), '800');
  assert.equal(svg.getAttribute('height'), '400');
  assert.equal(svg.getAttribute('viewBox'), '0 0 400 200', 'viewBox 不该被缩放动到');
  // 缩回去。
  C.applyScale(svg, 400, 200, 0.5);
  assert.equal(svg.getAttribute('width'), '200');
  assert.equal(svg.getAttribute('height'), '100');
});

test('applyScale:脏比例走 clamp,结果永远是合法尺寸', () => {
  const svg = new FakeElement('svg');
  C.applyScale(svg, 400, 200, NaN);
  assert.equal(svg.getAttribute('width'), '400', 'NaN 应退化成 1 倍');
  C.applyScale(svg, 400, 200, 1e9);
  assert.equal(svg.getAttribute('width'), String(Math.round(400 * C.MAX_SCALE)));
  // 元素不在(渲染还没落地)时不抛 —— 这条路径会在 `renderGraph` 的
  // 「stage 还没挂载」分支里真的走到。
  assert.doesNotThrow(() => C.applyScale(null, 400, 200, 2));
  assert.doesNotThrow(() => C.applyScale({}, 400, 200, 2));
});

// ---- wire(装配)--------------------------------------------------------------

test('wire:重复调用只绑一遍 —— 每次全量重绘都会调它', () => {
  // `renderGraph()` 每次都重建 DOM 并重新装配,而其实绑在**不重建的容器**上。
  // 不挡的话一次拖动会被处理两遍(scrollLeft 减两次),表现为「拖动速度翻倍」——
  // 一个看起来像「浏览器设置不同」的 bug。
  const scroller = new FakeElement('div');
  C.wire(scroller, {});
  const after1 = scroller.listeners.get('pointerdown').length;
  C.wire(scroller, {});
  C.wire(scroller, {});
  assert.equal(scroller.listeners.get('pointerdown').length, after1);
});

test('wire:拖动改的是 scrollLeft/Top,且松手后 pointercancel 也能收尾', () => {
  const scroller = new FakeElement('div');
  scroller.scrollLeft = 100;
  scroller.scrollTop = 100;
  C.wire(scroller, {});
  scroller.fire('pointerdown', { button: 0, clientX: 300, clientY: 300 });
  assert.equal(C.dragged(), false, '按下的瞬间还没算拖动');
  scroller.fire('pointermove', { clientX: 250, clientY: 290 });
  assert.equal(C.dragged(), true, '位移超过阈值却没算拖动');
  assert.equal(scroller.scrollLeft, 150, '往左拖应当把内容往右带(scrollLeft 变大)');
  assert.equal(scroller.scrollTop, 110);
  // `pointercancel` 也要清掉拖动状态 —— 否则「系统抢走指针」之后回到页面,
  // 鼠标一动图就自己漂。
  scroller.fire('pointercancel', {});
  const before = scroller.scrollLeft;
  scroller.fire('pointermove', { clientX: 200, clientY: 200 });
  assert.equal(scroller.scrollLeft, before, 'pointercancel 之后还在跟着鼠标走');
});

test('wire:阈值以内的微小移动完全不改滚动位置', () => {
  // 否则「点击选中节点」这个动作会顺带把画布平移 1~3px —— 点十次就明显偏了,
  // 而用户不知道自己点错了什么。
  const scroller = new FakeElement('div');
  scroller.scrollLeft = 100;
  C.wire(scroller, {});
  scroller.fire('pointerdown', { button: 0, clientX: 300, clientY: 300 });
  scroller.fire('pointermove', { clientX: 302, clientY: 301 });
  assert.equal(scroller.scrollLeft, 100);
  assert.equal(C.dragged(), false);
});

test('wire:普通滚轮不动,只有 Ctrl/⌘ + 滚轮才缩放', () => {
  // 三栏应用里劫持普通滚轮会让人烦躁:用户想往下看看,图却突然放大、位置也变了。
  // 这条钉的就是「普通滚轮原样交给浏览器」。
  const scroller = new FakeElement('div');
  const svg = new FakeElement('svg');
  let scale = 1;
  const commits = [];
  C.wire(scroller, {
    svg: () => svg,
    layout: () => ({ width: 400, height: 200 }),
    scale: () => scale,
    commit: (s) => { scale = s; commits.push(s); },
  });
  const plain = { deltaY: -1, clientX: 10, clientY: 10, preventDefault: () => { plain.prevented = true; } };
  scroller.fire('wheel', plain);
  assert.deepEqual(commits, [], '普通滚轮不该触发缩放');
  assert.ok(!plain.prevented, '普通滚轮不该被 preventDefault');

  const ctrl = { deltaY: -1, ctrlKey: true, clientX: 10, clientY: 10, preventDefault: () => {} };
  scroller.fire('wheel', ctrl);
  assert.deepEqual(commits.length, 1);
  assert.equal(commits[0], C.STEP, 'Ctrl + 上滚应当是放大一步');
  assert.equal(svg.getAttribute('width'), String(Math.round(400 * C.STEP)),
    '缩放没有落到 SVG 的 width 上');
});

test('wire:Ctrl + 滚轮到顶之后再滚不产生一次空提交', () => {
  const scroller = new FakeElement('div');
  const svg = new FakeElement('svg');
  let scale = C.MAX_SCALE;
  const commits = [];
  C.wire(scroller, {
    svg: () => svg,
    layout: () => ({ width: 400, height: 200 }),
    scale: () => scale,
    commit: (s) => { scale = s; commits.push(s); },
  });
  for (let i = 0; i < 3; i += 1) {
    scroller.fire('wheel', { deltaY: -1, ctrlKey: true, clientX: 10, clientY: 10, preventDefault: () => {} });
  }
  assert.deepEqual(commits, [], '已经到 MAX 还在提交缩放');
});
