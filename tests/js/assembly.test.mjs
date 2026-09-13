/**
 * 装配组:index.html 到底挂了哪些文件、顺序对不对、有没有孤儿文件。
 *
 * 这一类失败**全部是静默的**:少挂一个 js,浏览器不报错,只是某个功能永远不出现;
 * 文件名打错,页面直接白屏而控制台干净;加了一个新文件却忘了挂上,源码树里它
 * 明明在,测试也「通过」。所以清单必须由测试而不是由人眼守。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { relative, sep } from 'node:path';

import {
  BOOT_FILE, REPO_ROOT, WEB_DIR, assetPath, loadKP, plain, readIndexHtml,
  scriptSrcs, styleHrefs, walkAssets,
} from './harness.mjs';

const EXPECTED_SCRIPTS = [
  'js/util.js',
  'js/dom.js',
  'js/icons.js',
  'js/sse.js',
  'js/markdown.js',
  'js/store.js',
  'js/chat.js',
  'js/graph/layout.js',
  'js/graph/canvas.js',
  'js/api.js',
  'js/resizer.js',
  'js/views/shell.js',
  'js/views/graph.js',
  'js/views/inspector.js',
  'js/views/assistant.js',
  'js/views/chat.js',
  'js/views/settings.js',
  'js/app.js',
];

const EXPECTED_STYLES = [
  'css/tokens.css',
  'css/base.css',
  'css/layout.css',
  'css/sidebar.css',
  'css/graph.css',
  'css/inspector.css',
  'css/assistant.css',
  'css/chat.css',
  'css/settings.css',
];

/** `/static/js/x.js` → `js/x.js`(posix 相对 WEB_DIR)。 */
function rel(ref) {
  return relative(WEB_DIR, assetPath(ref)).split(sep).join('/');
}

test('index.html 的 <script src> 有序列表 == 预期', () => {
  assert.deepEqual(plain(scriptSrcs().map(rel)), EXPECTED_SCRIPTS);
});

test('index.html 的 <link href> 有序列表 == 预期', () => {
  assert.deepEqual(plain(styleHrefs().map(rel)), EXPECTED_STYLES);
});

test('web/js 下每个文件都被 index.html 引用(没有孤儿文件)', () => {
  const referenced = new Set(scriptSrcs().map(rel));
  const orphans = walkAssets(WEB_DIR, '.js').filter((p) => !referenced.has(p));
  assert.deepEqual(plain(orphans), [],
    '有 js 文件没挂到 index.html 上 —— 它在源码树里存在、测试也全绿,但浏览器永远不会加载它');
});

test('web/css 下每个文件都被 index.html 引用(没有孤儿文件)', () => {
  const referenced = new Set(styleHrefs().map(rel));
  const orphans = walkAssets(WEB_DIR, '.css').filter((p) => !referenced.has(p));
  assert.deepEqual(plain(orphans), []);
});

test('全部脚本按序加载不抛异常(加载期门禁开启)', async () => {
  const { KP } = await loadKP();
  // 每个模块对外暴露的**命名空间入口**。注意 store.js 不是 `KP.store` ——
  // 它把状态与选择器摊在 `KP.S` / `KP.stateOf` 这类名字上,这里按真实名字断言。
  for (const name of ['esc', 'dom', 'sse', 'markdown', 'chat', 'layout', 'api', 'views',
                      'S', 'STATE', 'stateOf', 'corePhase', 'pct', 'route',
                      'prefs', 'overlayKind', 'researchOpts', 'resizer']) {
    assert.ok(KP[name], `window.KP.${name} 没有定义 —— 对应模块的 IIFE 挂载写错了`);
  }
});

test('源码里引用的每个 KP.xxx 都真的存在(拼错只会在浏览器里静默半死)', async () => {
  // `KP.dom.qs(...)` 这种拼错在浏览器里是**运行时**才炸的:控制台一条报错、
  // 按钮没反应,而 pytest 与 node --test 全绿(没有任何测试调用那一行)。
  // 拆成 40 个文件之后,跨文件引用变多,这类错误只会更多。
  const { KP } = await loadKP();
  const sources = new Map();
  for (const file of walkAssets(WEB_DIR, '.js')) {
    sources.set(file, stripComments(readFileSync(`${WEB_DIR}/${file}`, 'utf8'), file));
  }
  // 源码里**被赋值的**顶层名字。`app.js` 是 harness 有意不加载的(顶层有副作用),
  // 所以它挂的 `KP.app` 不会出现在加载出来的对象上 —— 但那是它自己定义的,不算拼错。
  const assigned = new Set();
  for (const text of sources.values()) {
    for (const m of text.matchAll(/(?:window\.)?KP\.([A-Za-z_$][\w$]*)\s*=[^=]/g)) assigned.add(m[1]);
  }

  const missing = new Map();
  for (const [file, text] of sources) {
    // **任意深度**。只查两层会漏掉 `KP.views.shell.loadTopic` 这种 —— 而那种漏网
    // 恰好是最贵的:它在加载期不报错,只在「生成成功之后」那条路径上抛
    // `is not a function`。(这个检查最初写成两层,就真的漏掉过一次。)
    const re = /\bKP(?:\.([A-Za-z_$][\w$]*))+/g;
    let m;
    while ((m = re.exec(text)) !== null) {
      const path = m[0].slice(3).split('.');
      let cur = KP;
      let trace = 'KP';
      for (let i = 0; i < path.length; i += 1) {
        const seg = path[i];
        trace += '.' + seg;
        // 第一个段允许是「本仓库某处自己赋过值的名字」—— app.js 有意不被加载。
        if (i === 0 && !(seg in cur) && assigned.has(seg)) break;
        if (cur === null || typeof cur !== 'object' || !(seg in cur)) {
          missing.set(trace, `${file}: ${trace}`);
          break;
        }
        cur = cur[seg];
      }
    }
  }
  assert.deepEqual(plain([...missing.values()]), [],
    '这些引用在 window.KP 上不存在 —— 在浏览器里只有点击到那一刻才会炸');
});

test('app.js 是唯一有顶层副作用的文件,且必须排在最后', () => {
  const srcs = scriptSrcs().map(rel);
  assert.equal(srcs[srcs.length - 1], BOOT_FILE, 'app.js 必须最后加载');
  assert.equal(srcs.filter((s) => s === BOOT_FILE).length, 1, 'app.js 只能出现一次');
});

test('`document.getElementById` 只出现在 views/shell.js 与 app.js', () => {
  // 同 id 元素出现两次时 getElementById 返回**文档序第一个** ——
  // 「助手里的发送按钮控制了 Inspector 的输入框」这类 bug 只能靠这条扫描挡。
  // view 内部一律用 `KP.dom.q(root, id)`(作用域查询)。
  const allowed = new Set(['js/views/shell.js', 'js/app.js']);
  const offenders = [];
  for (const file of walkAssets(WEB_DIR, '.js')) {
    const text = stripComments(readFileSync(`${WEB_DIR}/${file}`, 'utf8'), file);
    if (/document\s*\.\s*getElementById/.test(text) && !allowed.has(file)) {
      offenders.push(file);
    }
  }
  assert.deepEqual(plain(offenders), [],
    '这些文件用了 document.getElementById;请改用 KP.dom.q(root, id)');
});

test('没有文件在加载期就被迫初始化(每个 IIFE 顶层只做定义)', () => {
  // 加载期门禁已经能抓住 document/location/fetch 的顶层访问;这条补充的是
  // 「顶层调用自己的 init/boot」这类写法 —— 它在沙箱里不会炸,只会让
  // 「模块可单独加载」这个前提失效。
  const offenders = [];
  for (const file of walkAssets(WEB_DIR, '.js')) {
    if (rel(file) === BOOT_FILE) continue;   // app.js 是设计上唯一的例外
    const text = stripComments(readFileSync(`${WEB_DIR}/${file}`, 'utf8'), file);
    const body = text.slice(text.indexOf('(function'));
    const lines = body.split('\n');
    for (const line of lines) {
      // 顶层行 = 行首无缩进且不以 `}` / `)` / `*` 开头的调用
      if (/^\S/.test(line) && /^[A-Za-z_$][\w$.]*\s*\(/.test(line)) {
        offenders.push(`${file}: ${line.trim()}`);
      }
    }
  }
  assert.deepEqual(plain(offenders), []);
});

/** 剥掉注释 —— 文档里写「不要用 document.getElementById」不该把测试弄红。 */
function stripComments(text, file) {
  const noBlock = text.replace(/\/\*[\s\S]*?\*\//g, '');
  if (file.endsWith('.css')) return noBlock;
  return noBlock.replace(/(?<!:)\/\/[^\n]*/g, '');
}

test('对话 UI 只有浮动助手一处:右栏里不得再出现输入框', () => {
  // 全前端唯一的对话 UI 是左下角的浮动助手(`views/assistant.js`)。设计稿让右栏
  // Inspector 也放一个输入框,那会造出两类静默 bug:① 同 id 元素出现两次时
  // `document.getElementById` 返回**文档序第一个** —— 「助手里的发送按钮控制了
  // Inspector 的输入框」;② 「边聊边点图」时在途 token 落到别的节点的气泡上,
  // 而浮动助手会让这条成为主路径。两条都不报错、都能过 pytest。
  // 扫的是「**造**输入框」和「**管**会话」这两件事。右栏允许 `KP.dom.q(助手, 'a-input')`
  // 去给助手的输入框上焦点(点「开始学习」之后)—— 那是把唯一那个入口用起来,
  // 正好相反于「再开一处」;但它一个字都不许自己建,也不许碰 `KP.chat`。
  //
  // **走查反馈 ④ 之后右栏多了一个 `<textarea>`**:「讲解记录」页上编辑「我的笔记」
  // 的那一个。它与对话无关(保存走 `KP.api.saveNote`,不经过 `KP.chat`),所以这里
  // 从「一个 `<textarea>` 都不许有」改成「**只许有那一个**」:`<input>` / `<form>` /
  // `composer` / `chat-input` / `KP.chat` 仍然一个都不许出现。允许一个**点名的**例外,
  // 而不是把整条检查放宽 —— 放宽之后,下一个人往右栏塞一个聊天输入框不会被栏住。
  const text = stripComments(readFileSync(`${WEB_DIR}/js/views/inspector.js`, 'utf8'), 'inspector.js');
  for (const marker of ['<input', '<form', 'composer', 'chat-input', 'KP.chat']) {
    assert.ok(!text.includes(marker),
      `views/inspector.js 里出现了 ${marker} —— 对话只该长在 views/assistant.js 一处`);
  }
  const areas = text.match(/<textarea/g) || [];
  assert.equal(areas.length, 1,
    `views/inspector.js 里有 ${areas.length} 个 <textarea> —— 只允许「我的笔记」那一个`);
  assert.ok(text.includes('id="p-note-text"'),
    '那唯一的 <textarea> 不再是笔记编辑框 —— 这条例外失去前提,该跟着改');
  // 反向也要成立:助手确实有输入框、也确实在用那套 reducer。否则上面那条可能
  // 因为「右栏文件名写错了」而恒真。
  const asst = readFileSync(`${WEB_DIR}/js/views/assistant.js`, 'utf8');
  assert.ok(asst.includes('<input') && asst.includes('a-form'));
  assert.ok(asst.includes('KP.chat.'), '助手没接上共用的 reducer —— 它多半复制了一份');
  // 而「开始学习」那个 reach-in 是真实存在的,写清楚免得下一个人以为它被漏掉了:
  assert.ok(text.includes("'a-input'"), '右栏不再把焦点交给助手的输入框了?那「开始学习」点下去没有落点');
});

test('源码里 getElementById 取的每个 id 都在 index.html 里存在', () => {
  // 挂载点改名是**静默**的:`el.assistant` 变成 null,`render()` 走「没有容器就
  // 返回」的分支 —— 页面照常、控制台干净,只是助手/侧栏永远不出现。没有任何
  // 别的测试能抓到它(视图函数逐个都测过,它们各自都对)。
  const html = readIndexHtml();
  const declared = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
  const wanted = new Set();
  for (const file of ['js/views/shell.js', BOOT_FILE]) {
    const text = stripComments(readFileSync(`${WEB_DIR}/${file}`, 'utf8'), file);
    for (const m of text.matchAll(/getElementById\(\s*['"]([^'"]+)['"]\s*\)/g)) wanted.add(m[1]);
  }
  assert.ok(wanted.has('assistant'), '助手挂载点没被取过 —— 它永远不会渲染');
  const missing = [...wanted].filter((id) => !declared.has(id));
  assert.deepEqual(plain(missing), [],
    '这些 id 被 getElementById 取,但 index.html 里没有 —— 少一个挂载点是静默失效');
});

test('REPO_ROOT 没被误当成 WEB_DIR(静态挂载只该暴露 web/)', () => {
  assert.ok(!WEB_DIR.endsWith(REPO_ROOT), '挂载目录不该是仓库根');
  assert.ok(WEB_DIR.includes('web'));
});
