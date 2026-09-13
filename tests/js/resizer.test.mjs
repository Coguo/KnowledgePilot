/**
 * 三栏之间的可拖拽分隔条(`js/resizer.js` + `views/shell.js` 的落盘那一半)。
 *
 * 这一组存在的理由:拖动是**唯一一类「错了不报错、只表现为手感不对」**的交互 ——
 * 方向写反(拖右栏往右却变宽)、夹错区间(把画布挤没)、丢了 `pointerup`(分隔条
 * 一直粘在鼠标上),三种都不会抛异常,而且都要用户真的上手拖才看得出来。
 *
 * 桩元素没有布局,所以宽度不是「量」出来的 —— `js/resizer.js` 只算「拖到了多少」,
 * 「写哪个变量、记不记得住」走调用方传进来的 `commit`。测试因此断言的是
 * **往哪个 CSS 变量写了什么值**与**偏好里存了什么**,而不是像素。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { loadKP, makeShellElements, plain, FakeElement } from './harness.mjs';

/** 只加载、不开挂载点(测纯函数与直接 `wire` 时用)。 */
const bare = await loadKP();
const DEFAULTS = { side: 248, insp: 300 };

/** 装在真实外壳里的分隔条:`clientWidth` 给一个值,好让「给画布留位」那条上限生效。 */
async function shell(width = 1400, seed = null) {
  const elements = makeShellElements();
  elements.app.clientWidth = width;
  const m = await loadKP({ elements });
  if (seed) m.KP.prefs.set('layout.cols', seed);
  m.KP.views.shell.init();
  return m;
}

const colVar = (m, name) => m.elements.app.style.getPropertyValue(name);
const savedCols = (m) => m.KP.prefs.get('layout.cols', null);

/** 拖一次:从 `from` 按下、经过 `to` 松开。 */
function drag(handle, from, to) {
  handle.fire('pointerdown', { button: 0, clientX: from });
  handle.fire('pointermove', { clientX: to });
  handle.fire('pointerup', { clientX: to });
}

// ---- 纯函数:区间 ------------------------------------------------------------

test('clampWidth:区间、取整与「存坏了退回默认」', () => {
  const { clampWidth } = bare.KP.resizer;
  // 没传 avail → 只按 LIMITS 夹(桩与旧浏览器里量不到宽度,量不到就不该假装量到了)
  assert.equal(clampWidth('side', 300), 300);
  assert.equal(clampWidth('side', 10), 176, '低于下限没有被抬回来');
  assert.equal(clampWidth('side', 9999), 420, '高于上限没有被压住');
  assert.equal(clampWidth('insp', 10), 248);
  assert.equal(clampWidth('insp', 9999), 560);
  // 存坏了 / 没设过 → 默认,而不是 0 或 NaN
  for (const bad of [null, undefined, NaN, '', 'abc', {}]) {
    assert.equal(clampWidth('side', bad), DEFAULTS.side, `${String(bad)} 没有退回默认`);
  }
  assert.equal(clampWidth('side', 300.7), 301, '没有取整 —— 会写出一串浮点小数');
});

test('clampWidth:窗口放不下时给画布让位,但下限优先', () => {
  const { clampWidth } = bare.KP.resizer;
  // 1400 总宽、另一栏 300、画布至少 360 → 这一栏最多 740,仍受自己的上限 420 约束
  assert.equal(clampWidth('side', 9999, 1400, 300), 420);
  // 900 总宽 → 上限收紧到 240,这才轮到窗口说话
  assert.equal(clampWidth('side', 9999, 900, 300), 240, '没给画布留位');
  // 窗口窄到连下限都放不下时,**下限优先** —— 一条挤的栏好过一条宽度为负的栏
  assert.equal(clampWidth('side', 9999, 300, 300), 176);
  // 量不到宽度(0 / NaN)时不设这条上限
  assert.equal(clampWidth('side', 9999, 0, 300), 420);
});

// ---- wire:指针骨架 ----------------------------------------------------------

test('wire:拖右侧的分隔条向右 = 右栏变窄(方向写反不会报错,只会很别扭)', async () => {
  const m = await shell();
  drag(m.elements['rz-insp'], 1000, 1030);   // 往右拖 30
  assert.equal(colVar(m, '--insp-w'), '270px',
    `往右拖右栏的分隔条应当把它拖**窄**(默认 ${DEFAULTS.insp} - 30)`);
  // 左栏那条方向相反:往右拖 = 变宽
  drag(m.elements['rz-side'], 200, 260);
  assert.equal(colVar(m, '--side-w'), '308px', '往右拖左栏的分隔条应当把它拖宽');
});

test('wire:拖动立刻落盘 —— 刷新之后宽度还在', async () => {
  const m = await shell();
  drag(m.elements['rz-side'], 200, 260);
  assert.deepEqual(plain(savedCols(m)), { side: 308 });
  // 松手之后不该再多写一次(拖动过程里逐帧写,不是「松手才写」)
  const after = plain(savedCols(m));
  m.elements['rz-side'].fire('pointermove', { clientX: 300 });
  assert.deepEqual(plain(savedCols(m)), after, '松手之后还在跟着鼠标写');
});

test('wire:一次拖动只提交一次 —— 重复绑定会让宽度按两倍速度走', () => {
  const handle = new FakeElement('div');
  const seen = [];
  const opts = {
    kind: 'side',
    get: () => 200,
    fit: (px) => px,
    commit: (_kind, px) => seen.push(px),
  };
  assert.equal(bare.KP.resizer.wire(handle, opts), true, '第一次绑定应当成功');
  assert.equal(bare.KP.resizer.wire(handle, opts), false, '同一个元素不该绑第二遍');
  handle.fire('pointerdown', { button: 0, clientX: 0 });
  handle.fire('pointermove', { clientX: 30 });
  assert.deepEqual(seen, [230], '一次移动提交了多次 —— 处理器绑重复了');
});

test('wire:捕获失败(浏览器不支持)时拖动照常', () => {
  const handle = new FakeElement('div');
  handle.setPointerCapture = () => { throw new Error('这个浏览器不认'); };
  const seen = [];
  bare.KP.resizer.wire(handle, {
    kind: 'side', get: () => 200, fit: (px) => px,
    commit: (_kind, px) => seen.push(px),
  });
  handle.fire('pointerdown', { button: 0, clientX: 0, pointerId: 7 });
  handle.fire('pointermove', { clientX: 20 });
  assert.deepEqual(seen, [220], '捕获抛异常把整条拖动带塌了');
});

test('wire:丢一次 pointerup 不该让分隔条粘在鼠标上(pointercancel / 切窗口)', async () => {
  // `pointermove` **不需要按键** —— 所以只要 `drag` 挂着没清,悬停移动就会一直改宽度,
  // 表现是「松手之后它自己还在动」。这条是这类状态机唯一会被用户遇到的失败。
  const m = await shell();
  const rz = m.elements['rz-side'];

  rz.fire('pointerdown', { button: 0, clientX: 200 });
  rz.fire('pointermove', { clientX: 210 });
  const afterCancel = colVar(m, '--side-w');
  rz.fire('pointercancel', {});
  rz.fire('pointermove', { clientX: 400 });      // 没清干净的话这下会拖到边界
  assert.equal(colVar(m, '--side-w'), afterCancel, 'pointercancel 之后还在跟着鼠标走');

  // 切窗口(Alt+Tab):上面三条都不会派发,只有 window 的 blur 兜底
  rz.fire('pointerdown', { button: 0, clientX: 200 });
  rz.fire('pointermove', { clientX: 210 });
  const afterReDown = colVar(m, '--side-w');
  m.winListeners.fire('blur', {});
  rz.fire('pointermove', { clientX: 900 });      // 同上
  assert.equal(colVar(m, '--side-w'), afterReDown, '切回来之后宽度还在跟着鼠标走');
});

test('wire:非主键按在分隔条上不拖(右键留给浏览器)', async () => {
  const m = await shell();
  m.elements['rz-side'].fire('pointerdown', { button: 2, clientX: 200 });
  m.elements['rz-side'].fire('pointermove', { clientX: 400 });
  assert.equal(colVar(m, '--side-w'), '', '右键也在拖');
});

// ---- 重置与键盘 --------------------------------------------------------------

test('重置:双击把内联变量**删掉**,而不是写一个等于默认的数值', async () => {
  // 右栏的默认是 `clamp(300px, 23vw, 340px)`(`tokens.css`)。写死 300px 等于
  // 「重置之后它不再随窗口变宽」—— 与用户按重置想要的意思正好相反。
  const m = await shell();
  drag(m.elements['rz-side'], 200, 260);
  drag(m.elements['rz-insp'], 1000, 1030);
  m.elements['rz-side'].fire('dblclick', {});
  assert.equal(colVar(m, '--side-w'), '', '内联变量没被删掉');
  assert.equal(colVar(m, '--insp-w'), '270px', '重置左栏把右栏也重置了');
  assert.deepEqual(plain(savedCols(m)), { insp: 270 }, '偏好没有跟着删干净');
});

test('重置:两栏都重置过之后,偏好整条不留在 localStorage 里', async () => {
  const m = await shell();
  drag(m.elements['rz-side'], 200, 260);
  m.elements['rz-side'].fire('dblclick', {});
  assert.equal(savedCols(m), null, '留下了一个空对象');
});

test('键盘:方向键微调、Home 重置(触屏上没有双击)', async () => {
  const m = await shell();
  const rz = m.elements['rz-side'];
  rz.fire('keydown', { key: 'ArrowRight' });
  assert.equal(colVar(m, '--side-w'), '264px', `方向键一次应当走 ${bare.KP.resizer.KEY_STEP}px`);
  rz.fire('keydown', { key: 'ArrowLeft' });
  assert.equal(colVar(m, '--side-w'), '248px');
  // 右栏那条方向相反:ArrowLeft = 往左移分隔条 = 右栏变宽
  m.elements['rz-insp'].fire('keydown', { key: 'ArrowLeft' });
  assert.equal(colVar(m, '--insp-w'), '316px', '右栏的方向键方向反了');
  // 别的键不动它
  rz.fire('keydown', { key: 'a' });
  assert.equal(colVar(m, '--side-w'), '248px');
  rz.fire('keydown', { key: 'Home' });
  assert.equal(colVar(m, '--side-w'), '', 'Home 没有重置');
});

// ---- 启动时恢复 --------------------------------------------------------------

test('分隔条是可就聚焦控件:报得出自己的取值范围与当前值', async () => {
  // `role="separator"` + `tabindex="0"` 是个「可聚焦的控件」。聚焦之后读不出当前值的
  // 控件比不聚焦更让人摸不着头脑 —— 所以 `aria-valuenow` 必须跟着宽度一起走。
  const m = await shell();
  const rz = m.elements['rz-side'];
  assert.equal(rz.getAttribute('aria-valuemin'), String(bare.KP.resizer.LIMITS.side.min));
  assert.equal(rz.getAttribute('aria-valuemax'), String(bare.KP.resizer.LIMITS.side.max));
  drag(rz, 200, 260);
  assert.equal(rz.getAttribute('aria-valuenow'), '308',
    '拖动之后 `aria-valuenow` 还停在旧值上 —— 屏幕阅读器读到的是假数字');
});

test('启动:把记住的宽度落到 `.app` 的内联变量上', async () => {
  const m = await shell(1400, { side: 300, insp: 360 });
  assert.equal(colVar(m, '--side-w'), '300px');
  assert.equal(colVar(m, '--insp-w'), '360px');
});

test('启动:没有偏好时不写内联变量(让样式表里的默认与断点说话)', async () => {
  const m = await shell();
  assert.equal(colVar(m, '--side-w'), '', '没拖过却写了一个内联宽度 —— 1280 断点就白设了');
});

test('启动与窗口变化:记太大 / 存坏了的宽度要被重新夹一次', async () => {
  // 窗口拉窄之后,记忆里的宽度可能已经把画布挤没了 —— 夹回来的第一现场是启动。
  const wide = await shell(1400, { side: 9999 });
  assert.equal(colVar(wide, '--side-w'), '420px', '超上限的宽度没有被夹');

  const narrow = await shell(900, { side: 400 });
  assert.equal(colVar(narrow, '--side-w'), '240px', '窄窗口下没有给画布让位');

  const junk = await shell(1400, { side: 'abc' });
  assert.equal(colVar(junk, '--side-w'), '', '存坏了的值被当成有效值写进了界面');
});
