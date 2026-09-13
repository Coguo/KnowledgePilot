/**
 * 前端测试桩:把 `knowledge_pilot/web/` 的多文件前端在 Node 里**按线上顺序**加载起来。
 *
 * 它做四件事:
 *   1. 读 `index.html`,抽出有序的 `<script defer src>` 列表 —— 这是唯一的「装配清单」,
 *      测试因此能断言「文件都在、顺序对、没有孤儿文件」,而不是靠人肉维护一份重复列表。
 *   2. 在 `vm` 沙箱里造一个最小全局环境(`window` / `TextDecoder` / `fetch` 桩 / `location` 桩)。
 *   3. 依次 `runInContext` 每个 js —— 任何**加载期**的异常都会当场炸出来。
 *   4. 把 `document` 设成**一碰就抛的 Proxy**。这条是把「文件加载时不得触碰 DOM」
 *      从口头纪律变成机械强制:现存单文件前端在 IIFE 顶层写了 `const app = $('app')`
 *      和 `topicsEl.addEventListener(...)`,这种写法在浏览器里完全正常、只有在
 *      「先加载模块、稍后再渲染」的测试与拆分场景下才会出问题 —— 所以必须让它炸。
 *
 * 用法:
 *   import { loadKP, readIndexHtml, scriptSrcs, WEB_DIR } from './harness.mjs';
 *   const { KP } = await loadKP();
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const HERE = dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = resolve(HERE, '..', '..');
export const WEB_DIR = join(REPO_ROOT, 'knowledge_pilot', 'web');
export const INDEX_HTML = join(WEB_DIR, 'index.html');

/** 唯一允许有顶层副作用的文件(见 `loadKP` 的跳过逻辑)。 */
export const BOOT_FILE = 'js/app.js';

export function readIndexHtml() {
  return readFileSync(INDEX_HTML, 'utf8');
}

/**
 * 抽出 index.html 里 `<script ... src="...">` 的 src,**保持文档顺序**。
 *
 * 刻意不解析 CSS:`<link href>` 的清单由 Python 侧的 `test_index_declares_expected_modules`
 * 与资源可达性测试守,这里只管 JS 的**执行顺序**——那是 `defer` 语义下唯一会错就有后果的东西。
 */
export function scriptSrcs(html = readIndexHtml()) {
  const out = [];
  const re = /<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["'][^>]*>/gi;
  let m;
  while ((m = re.exec(html)) !== null) out.push(m[1]);
  return out;
}

/** `<link rel=stylesheet href>` 的 href,保持文档顺序。 */
export function styleHrefs(html = readIndexHtml()) {
  const out = [];
  const re = /<link\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>/gi;
  let m;
  while ((m = re.exec(html)) !== null) out.push(m[1]);
  return out;
}

/** 把 `/static/js/x.js` 或 `js/x.js` 解析成绝对磁盘路径。 */
export function assetPath(ref) {
  const clean = ref.replace(/^\.?\//, '');
  const rest = clean.startsWith('static/') ? clean.slice('static/'.length) : clean;
  return join(WEB_DIR, rest);
}

/** 递归列出 web/ 下所有指定后缀的文件,返回相对 WEB_DIR 的 posix 风格路径。 */
export function walkAssets(dir = WEB_DIR, ext = '.js') {
  const out = [];
  const visit = (d) => {
    for (const name of readdirSync(d).sort()) {
      const full = join(d, name);
      if (statSync(full).isDirectory()) visit(full);
      else if (full.endsWith(ext)) out.push(relative(WEB_DIR, full).split(sep).join('/'));
    }
  };
  visit(dir);
  return out;
}

/**
 * **加载期**一碰就抛的门禁。加载结束后按真实桩工作。
 *
 * 为什么是「只在加载期」而不是全程:视图在**运行时**本来就要碰 DOM
 * (`KP.views.shell.init()` 取挂载点、`generate()` 建日志行)。全程抛错会让
 * 任何一次真实调用都失败,于是测试要么不测这些路径、要么被迫关掉门禁 ——
 * 两种结果都比「加载期严格、运行期放开」差。
 *
 * 装配期统一由 `loadKP` 的 `busy` 开关控制,调用方不用手动切。
 */
export function makeLoadGate(label, target) {
  const boom = (what) => {
    throw new Error(
      `加载期触碰了 ${label}.${what} —— ` +
        '前端铁律:任何文件在加载时不得触碰 DOM/location/fetch,顶层只做 ' +
        'window.KP.xxx 定义。把这段逻辑挪进函数里(通常是 render / init)。',
    );
  };
  return new Proxy(target, {
    get(t, prop) {
      if (gate.busy) boom(String(prop));
      return Reflect.get(t, prop);
    },
    set(t, prop, value) {
      if (gate.busy) boom(String(prop));
      return Reflect.set(t, prop, value);
    },
    has: () => true,
  });
}

/** 全局加载开关。所有门禁共用它,只有 `loadKP` 会改。 */
export const gate = { busy: false };

/**
 * 运行时用的最小 `document` 桩。
 *
 * `elements` 是「id → FakeElement」的注册表。默认全空 —— 于是 `shell.init()` 取到的
 * 挂载点全是 `null`,视图函数会走各自的「没有容器就返回」分支而**不抛**。这一点是
 * 有意的:它让「忘了挂 `#stage`」在沙箱里表现为静默无输出,而不是一个碰巧被测试
 * 抓到的 TypeError。要驱动真实渲染路径,就把挂载点传进来(见下面的 `loadKP`)。
 */
export function makeDocumentStub(elements = {}) {
  return {
    createElement: (tag) => new FakeElement(tag),
    getElementById: (id) => elements[id] || null,
    readyState: 'complete',
  };
}

/** 一套完整的挂载点桩,id 与 `index.html` 里的一致。 */
export function makeShellElements(html = '') {
  return {
    app: new FakeElement('div'),
    stage: new FakeElement('div', html),
    topics: new FakeElement('div'),
    panel: new FakeElement('div'),
    nav: new FakeElement('nav'),
    progress: new FakeElement('div'),
    assistant: new FakeElement('div'),
    'new-topic': new FakeElement('form'),
    'new-query': new FakeElement('input'),
    'new-go': new FakeElement('button'),
    // 两条列分隔条。挂上它们之后 `shell.init()` 的 `wireColumns()` 才会真的跑到绑定
    // 那一步 —— 不挂的话它会安静地在「元素是 null」处返回,于是三分之一的路径没测。
    'rz-side': new FakeElement('div'),
    'rz-insp': new FakeElement('div'),
  };
}

/** 记录调用的 fetch 桩。默认拒绝,证明「没有任何顶层网络访问」。 */
export function makeFetchStub(routes = {}) {
  const calls = [];
  const stub = async (url, init) => {
    calls.push({ url, init });
    const key = Object.keys(routes).find((k) => url.includes(k));
    if (!key) throw new Error(`fetch 桩没有为 ${url} 配置响应(调用序号 ${calls.length})`);
    const r = typeof routes[key] === 'function' ? routes[key](url, init) : routes[key];
    return {
      ok: r.ok !== false,
      status: r.status ?? 200,
      statusText: r.statusText ?? 'OK',
      json: async () => r.json ?? {},
      text: async () => r.text ?? '',
      body: r.body ?? null,
    };
  };
  stub.calls = calls;
  return stub;
}

/** `location` / `history` 桩;`history.replaceState` 记录调用,用来抓路由死循环。 */
export function makeLocationStub(hash = '') {
  const history = {
    calls: [],
    replaceState(_state, _title, url) {
      this.calls.push(url);
      if (typeof url === 'string' && url.startsWith('#')) location.hash = url;
    },
  };
  const location = { hash, href: `http://test/${hash}` };
  return { location, history };
}

/**
 * 在沙箱里按 index.html 的顺序加载全部前端脚本。
 *
 * @param {object}  [opts]
 * @param {object}  [opts.routes]     fetch 桩的路由表(键为 URL 片段)
 * @param {string}  [opts.hash]       初始 location.hash
 * @param {boolean} [opts.strictDom]  默认 true:document 一碰就抛
 * @param {object}  [opts.elements]   「id → FakeElement」挂载点注册表;传了就返回
 *                                    真实可渲染的 `#stage` / `#panel`,不传则视图
 *                                    走「没有容器」分支(测不到渲染路径)。
 * @param {object}  [opts.globals]    额外注入沙箱的全局(如 `confirm` / `alert` 桩)
 * @returns {Promise<{KP: object, ctx: object, sandbox: object, fetch: Function,
 *                    elements: object, doc: object}>}
 */
export async function loadKP(opts = {}) {
  const { routes = {}, hash = '', strictDom = true, elements = null, globals = {} } = opts;
  const { location, history } = makeLocationStub(hash);
  const fetchStub = makeFetchStub(routes);
  const doc = makeDocumentStub(elements || {});
  const winListeners = makeListeners();

  const sandbox = {
    console,
    TextDecoder,
    TextEncoder,
    ReadableStream,
    Promise,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    JSON,
    Math,
    Date,
    fetch: fetchStub,
    location,
    history,
    localStorage: makeLocalStorageStub(),
    // `shell.init()` 会 `window.addEventListener('hashchange', …)`。真实的全局对象
    // 有这个方法,所以桩里必须有 —— 少了它,任何走到 `init()` 的测试都会以一个
    // 与被测逻辑毫无关系的 TypeError 失败,掩盖掉真正要断言的失败。
    addEventListener: (type, fn) => winListeners.add(type, fn),
    removeEventListener: (type, fn) => winListeners.remove(type, fn),
    ...globals,
  };
  if (strictDom) {
    // 换成加载期会抛的门禁。`location` / `history` / `fetch` 也一并看着 ——
    // 它们在浏览器里同样是「加载期不该碰」的东西,只靠 document 一个门禁
    // 漏得掉「顶层读 location.hash 做初始路由」这类写法。
    sandbox.document = makeLoadGate('document', doc);
    sandbox.location = makeLoadGate('location', location);
    sandbox.history = makeLoadGate('history', history);
    sandbox.fetch = makeLoadGate('fetch', fetchStub);
  } else {
    sandbox.document = doc;
  }

  const ctx = vm.createContext(sandbox);
  vm.runInContext('globalThis.window = globalThis;', ctx);

  gate.busy = true;
  for (const src of scriptSrcs()) {
    // `js/app.js` 是唯一被**有意跳过**的文件:它按设计就有顶层副作用(直接 boot),
    // 而 boot 会 getElementById + fetch。它的正确性由「它必须是最后一个脚本」
    // (`assembly.test.mjs`)与 Python 侧的端到端测试守,不在这里。
    // 比较用 `assetPath` 归一化后的值:index.html 里的引用是 `/static/js/app.js`,
    // 直接拿原串比会永远不等 —— 于是一个顶层 getElementById 就悄悄溜进来了。
    if (assetPath(src).endsWith(BOOT_FILE.split('/').join(sep))) continue;
    const file = assetPath(src);
    const code = readFileSync(file, 'utf8');
    try {
      vm.runInContext(code, ctx, { filename: file });
    } catch (err) {
      err.message = `加载 ${relative(REPO_ROOT, file)} 失败:${err.message}`;
      throw err;
    }
  }
  // 整个装配过程都在门禁下;跑完再放开,后面的用例才能真正调用 init / render。
  gate.busy = false;

  const KP = ctx.window.KP;
  if (!KP) throw new Error('加载完成后 window.KP 不存在 —— 检查各文件的 IIFE 是否挂在 window.KP 上');
  return { KP, ctx, sandbox, fetch: fetchStub, elements, doc, winListeners };
}

/**
 * 把 `vm` 沙箱里造出来的对象/数组还原成**当前 realm** 的普通对象。
 *
 * 必须用它:沙箱里 `{...}` 字面量的原型是沙箱自己的 `Object.prototype`,与测试文件
 * 的不是同一个 —— `deepStrictEqual` 会以 "same structure but not reference-equal"
 * 失败,而那看起来像是断言写错了,实际是 realm 差异。字符串/数字是原始值,不受影响。
 */
export function plain(v) {
  return v === undefined ? undefined : JSON.parse(JSON.stringify(v));
}

/** `window.addEventListener` 的最小实现 —— 记录监听器,`fire` 手动触发。 */
export function makeListeners() {
  const map = new Map();
  return {
    add(type, fn) {
      if (!map.has(type)) map.set(type, []);
      map.get(type).push(fn);
    },
    remove(type, fn) {
      map.set(type, (map.get(type) || []).filter((f) => f !== fn));
    },
    fire(type, evt = {}) {
      for (const fn of map.get(type) || []) fn(evt);
    },
    count: (type) => (map.get(type) || []).length,
  };
}

export function makeLocalStorageStub(seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    clear: () => map.clear(),
    _map: map,
  };
}

/**
 * 迷你 DOM 桩。够 `views/*` 的 render 函数与事件绑定用,不追求 DOM 完整语义。
 *
 * 刻意**不实现** `document.getElementById` 之外的全局查找语义 —— 测试里要证明
 * view 内部用的是 `root.querySelector('[data-kp=...]')`(作用域查询),而不是全局查找。
 */
export function makeDomStub(html = '') {
  return new FakeElement('div', html);
}

class FakeElement {
  constructor(tag, html = '') {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = new Map();
    this.listeners = new Map();
    this._html = '';
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this.textContent = '';
    // 滚动位置的三个数字。**不是**布局引擎:桩不去推算内容有多高,由用例自己
    // 摆成想要的样子(`el.scrollHeight = 500; el.clientHeight = 300; el.scrollTop = 160`)。
    // 它是后补的,理由与 `style` 那一条相同:少了它,任何「读一下滚动位置再决定
    // 要不要吸底」的代码在沙箱里只会读到 `undefined`,而那种失败看起来像被测代码
    // 写错了。有了它,「流式时自动滚到底、用户手动往回翻则不打扰」这条才有守卫。
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
    this.classList = makeClassList();
    // `style` 的最小实现。**这一条是后补的**:此前 FakeElement 根本没有 `style`,
    // 于是任何「把宽度写成 CSS 变量」的代码在沙箱里必然 TypeError —— 而那种失败
    // 看起来像是被测代码写错了,实际是桩缺了一个标准属性。
    // 只实现 `setProperty` / `getPropertyValue` / `removeProperty`,够断言用。
    this.style = makeStyleStub();
    this.isConnected = true;
    // 走 setter 而不是直接赋 `_html`:setter 才会解析出 children,否则
    // `new FakeElement('div', html)` 建出来的桩查不到任何子元素。
    if (html) this.innerHTML = html;
  }

  get innerHTML() {
    return this._html;
  }

  set innerHTML(v) {
    this._html = String(v ?? '');
    // **必须先清空** —— `parseRough` 是「往 `parent.children` 里追加并返回它」的语义。
    // 少了这一行,每次重新赋值都是往上一代子节点后面再接一段:同一个容器渲染两遍
    // 就同时留着两代的按钮,`querySelector('#id')` 命中的是**上一代**那个,事件绑在
    // 早已脱离文档的元素上 —— 而页面看起来完全正常。这个坑是 Stage 3 的布局切换
    // 用例撞出来的:点一次按钮,`[data-mode]` 的命中数从 4 涨到 6 再到 8,点击处理器
    // 在自增的列表上无限触发。
    this.children = [];
    parseRough(this._html, this);
  }

  /** 粗略解析:只认带 `data-kp` / `data-id` / `class` 的标签,足够断言用。 */
  get firstElementChild() {
    return this.children[0] || null;
  }

  get lastElementChild() {
    return this.children[this.children.length - 1] || null;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    this.children = this.children.filter((c) => c !== child);
    child.parentNode = null;
    return child;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }

  removeEventListener() {}

  /** 触发一次事件;`target` 默认为自身。 */
  fire(type, evt = {}) {
    for (const fn of this.listeners.get(type) || []) fn(evt);
  }

  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }

  /**
   * 只认三种选择器:`#id`、`.class`、`[attr]` / `[attr=value]`。
   * 够 `KP.dom.q`(按 id 作用域查询)与 `KP.dom.qa`(按类查要点芯片)用。
   * 刻意不复刻 DOM 的选择器引擎 —— 复刻得越像,测试越容易依赖真实的浏览器行为,
   * 那正是这套桩要避免的。
   */
  querySelectorAll(sel) {
    const idSel = sel.match(/^#([\w-]+)$/);
    const clsSel = sel.match(/^\.([\w-]+)$/);
    const attrSel = sel.match(/^\[([\w-]+)(?:=([^\]]*))?\]$/);
    if (!idSel && !clsSel && !attrSel) return [];
    const want = attrSel && attrSel[2] !== undefined
      ? attrSel[2].replace(/^["']|["']$/g, '')
      : null;
    const hit = (el) => {
      if (idSel) return el.getAttribute('id') === idSel[1];
      if (clsSel) return String(el.getAttribute('class') || '').split(/\s+/).includes(clsSel[1]);
      const got = el.getAttribute(attrSel[1]);
      return got !== null && (want === null || got === want);
    };
    const out = [];
    const visit = (node) => {
      for (const child of node.children) {
        if (hit(child)) out.push(child);
        visit(child);
      }
    };
    visit(this);
    return out;
  }

  closest(sel) {
    let node = this;
    while (node) {
      const attr = sel.match(/^\[([\w-]+)\]$/);
      if (attr && node.getAttribute(attr[1]) !== null) return node;
      node = node.parentNode;
    }
    return null;
  }

  scrollIntoView() {}
  focus() {}
  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }
}

/**
 * `element.style` 的最小桩:只记自定义属性(`setProperty` / `getPropertyValue` /
 * `removeProperty`)。**不复刻 CSSOM** —— 复刻得越像,测试越容易依赖真实的浏览器
 * 布局行为,而那正是这套桩要避免的。它要能回答的唯一问题是
 * 「这段代码往哪个变量上写了什么值」。
 */
function makeStyleStub() {
  const vars = new Map();
  return {
    setProperty: (name, value) => { vars.set(String(name), String(value)); },
    getPropertyValue: (name) => (vars.has(String(name)) ? vars.get(String(name)) : ''),
    removeProperty: (name) => { vars.delete(String(name)); },
    _vars: vars,
  };
}

function makeClassList() {
  const set = new Set();
  return {
    add: (...cs) => cs.forEach((c) => set.add(c)),
    remove: (...cs) => cs.forEach((c) => set.delete(c)),
    contains: (c) => set.has(c),
    toggle: (c) => (set.has(c) ? set.delete(c) : set.add(c)),
    _set: set,
  };
}

/** HTML 里本来就没有子节点的标签。 */
const VOID_TAGS = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                           'link', 'meta', 'param', 'source', 'track', 'wbr']);

/**
 * 极粗的 HTML → FakeElement **树**。只提取标签名与属性,不建文本节点。
 * 目的是让 `root.querySelector('#id')` / `.bubble` / `closest('[data-id]')` 在渲染
 * 产物上可用,而不是复刻 DOM。
 *
 * **必须建树,不能拍平。** 拍平之后 `lastElementChild` 返回的是文档里最后一个标签
 * (而不是最后一个**直接子**元素),`closest()` 的 parentNode 链也断了 ——
 * `chat.tailBubble` 靠 `box.lastElementChild.querySelector('.bubble')` 找尾部气泡,
 * 拍平会让它恒返回 null,而那等于「流式正文一个字都写不出来」。
 *
 * 已知限制:属性值里不能有裸的 `>`。渲染代码里没有这种写法(属性都过 `esc()`),
 * 真出现了也只是解析少一个标签,不会静默影响断言结果。
 */
function parseRough(html, parent) {
  const stack = [parent];
  const re = /<(\/?)([a-zA-Z][\w-]*)\b([^>]*?)(\/?)>/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    const [, closing, tag, rawAttrs, selfClose] = m;
    const name = tag.toLowerCase();
    if (closing) {
      // 只在栈顶匹配时出栈 —— 未闭合的标签不该把上层结构一起带塌。
      if (stack.length > 1 && stack[stack.length - 1].tagName === name.toUpperCase()) stack.pop();
      continue;
    }
    const el = new FakeElement(tag);
    const host = stack[stack.length - 1];
    el.parentNode = host;
    host.children.push(el);
    // 顺带把属性存进 attributes,好让 id/class/attr 选择器与 `closest` 能用。
    const are = /([\w:-]+)\s*=\s*"([^"]*)"/g;
    let a;
    while ((a = are.exec(rawAttrs)) !== null) el.attributes.set(a[1], a[2]);
    if (!selfClose && !VOID_TAGS.has(name)) stack.push(el);
  }
  return parent.children;
}

export { FakeElement };
