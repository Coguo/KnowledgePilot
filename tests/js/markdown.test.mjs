/**
 * `js/markdown.js` —— 「先转义,再渲染」这条不变量的守门人。
 *
 * 顺序写反后,**正常内容下页面完全正常**,只在模型输出(或用户输入)里带标签时
 * 才炸。这类 bug 靠人工冒烟测不到,只能靠对抗性夹具。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { loadKP, plain } from './harness.mjs';
import { XSS } from './fixtures/graphs.mjs';

const { KP } = await loadKP();
const md = KP.markdown;

test('XSS:行内 HTML 一律被转义成文本,不产生元素', () => {
  const html = md.renderMarkdown(XSS);
  assert.ok(!html.includes('<img'), `产物里出现了真实元素:${html}`);
  assert.ok(html.includes('&lt;img'), '应当以转义文本出现');
});

test('XSS:围栏代码块内的标签也被转义', () => {
  const html = md.renderMarkdown('```\n<script>alert(1)</script>\n```');
  assert.ok(!html.includes('<script'), html);
  assert.ok(html.includes('&lt;script&gt;'), html);
});

test('XSS:front-matter 里的 payload 不进正文', () => {
  const { meta, rest } = md.splitFrontMatter(`---\nname: ${XSS}\n---\n\n正文`);
  assert.equal(meta.name, XSS);
  assert.equal(rest, '正文');
});

test('行内 Markdown:粗体 / 斜体 / 行内代码', () => {
  assert.equal(md.renderMarkdown('**粗**'), '<p><strong>粗</strong></p>');
  assert.equal(md.renderMarkdown('*斜*'), '<p><em>斜</em></p>');
  assert.equal(md.renderMarkdown('`码`'), '<p><code>码</code></p>');
});

test('链接只放行 http(s);伪协议当普通文字', () => {
  const bad = md.renderMarkdown('[点我](javascript:alert(1))');
  assert.ok(!bad.includes('<a '), `javascript: 伪协议被放行了:${bad}`);
  assert.ok(!bad.includes('href'), bad);

  const good = md.renderMarkdown('[例子](https://example.com/a?b=1)');
  assert.ok(good.includes('<a href="https://example.com/a?b=1"'), good);
  assert.ok(good.includes('rel="noopener noreferrer"'), good);
  assert.ok(good.includes('target="_blank"'), good);
});

test('链接文字里的标签被转义,但链接本身仍然生成', () => {
  const html = md.renderMarkdown(`[${XSS}](https://a.b)`);
  assert.ok(!html.includes('<img'), html);
  assert.ok(html.includes('href="https://a.b"'), html);
});

test('块级:标题 / 有序无序列表 / 引用 / 分隔线', () => {
  assert.equal(md.renderMarkdown('# 标题'), '<h1>标题</h1>');
  assert.equal(md.renderMarkdown('###### 深标题'), '<h3>深标题</h3>');   // 只保留到 h3
  assert.equal(md.renderMarkdown('- 甲\n- 乙'), '<ul><li>甲</li><li>乙</li></ul>');
  assert.equal(md.renderMarkdown('1. 甲\n2. 乙'), '<ol><li>甲</li><li>乙</li></ol>');
  assert.equal(md.renderMarkdown('> 引用'), '<blockquote>引用</blockquote>');
  assert.equal(md.renderMarkdown('---'), '<hr>');
});

test('段落内的换行渲染成 <br>,空行断段', () => {
  assert.equal(md.renderMarkdown('甲\n乙'), '<p>甲<br>乙</p>');
  assert.equal(md.renderMarkdown('甲\n\n乙'), '<p>甲</p><p>乙</p>');
});

test('空输入不抛,返回空串', () => {
  for (const v of [null, undefined, '']) assert.equal(md.renderMarkdown(v), '');
});

// ---- splitFrontMatter:边界很脆(`indexOf('\\n---',3)` 与 `slice(end+4)`) ----

test('splitFrontMatter:正常闭合', () => {
  const { meta, rest } = md.splitFrontMatter('---\nname: 甲\nslug: jia\n---\n\n# 标题\n\n正文');
  assert.deepEqual(plain(meta), { name: '甲', slug: 'jia' });
  assert.equal(rest, '# 标题\n\n正文');
});

test('splitFrontMatter:没有闭合围栏时必须原样返回', () => {
  const raw = '---\nname: 甲\n\n# 标题';
  const { meta, rest } = md.splitFrontMatter(raw);
  assert.deepEqual(plain(meta), {});
  assert.equal(rest, raw, '没有闭合围栏就不能动正文');
});

test('splitFrontMatter:不以 --- 开头时原样返回', () => {
  const raw = '# 标题\n\n---\n\n正文';
  const { meta, rest } = md.splitFrontMatter(raw);
  assert.deepEqual(plain(meta), {});
  assert.equal(rest, raw);
});

test('splitFrontMatter:正文内部再出现 --- 不干扰(只认第一个闭合)', () => {
  const { rest } = md.splitFrontMatter('---\na: 1\n---\n\n上文\n\n---\n\n下文');
  assert.equal(rest, '上文\n\n---\n\n下文');
});

test('splitFrontMatter:真实 render_node_note 输出(逐字副本)', () => {
  // 取自 `learning/notes.py` 的 render_node_note:front-matter + 三节固定标题。
  // 这份副本就是「前端看到的真实输入」——手写简化版会漏掉那行 hint 文本,
  // 于是 pickNoteSections 的行为在测试里是对的、在线上是错的。
  const note = [
    '---',
    'name: 混合检索',
    'slug: 03_混合检索',
    'type: 技术',
    'depth: 2',
    'order_index: 2',
    '---',
    '',
    '# 混合检索',
    '',
    '## 要点',
    '- 向量召回 + 词法召回互补',
    '- RRF 按排名融合,不需要分数可比',
    '',
    '## 讲解记录',
    '',
    '（还没有讲解记录——点开本知识点开始对话后会追加到这里）',
    '',
    '## 我的笔记',
    '',
    '（在这里记笔记——程序不会改写这一节）',
    '',
    '',
  ].join('\n');

  const { meta, rest } = md.splitFrontMatter(note);
  assert.equal(meta.name, '混合检索');
  assert.equal(meta.order_index, '2');
  assert.ok(rest.startsWith('# 混合检索'), `正文首行丢了:${JSON.stringify(rest.slice(0, 20))}`);
  assert.ok(!rest.includes('order_index'), 'front-matter 不该出现在正文里');
  assert.ok(rest.includes('## 我的笔记'));
});

test('pickNoteSections:只留「要点」与「我的笔记」,丢掉会长大的讲解记录', () => {
  const note = [
    '## 要点', '- 甲', '',
    '## 讲解记录', '（第 1 轮）很长很长……', '',
    '## 我的笔记', '我自己写的', '',
  ].join('\n');
  const kept = md.pickNoteSections(note);
  assert.deepEqual(plain(kept.map((s) => s.title)), ['要点', '我的笔记']);
  assert.equal(kept[0].body, '- 甲');
  assert.equal(kept[1].body, '我自己写的');
});

// ---- splitNoteSections:全部二级段落,保序(「讲解记录」页与它共用一份规则)----
//
// 走查反馈 ④ 之前,段落切分只存在于 `pickNoteSections` 里(一个 filter)。现在
// 「讲解记录」页要按标题**取**两段(`讲解记录` 与 `我的笔记`),而文件的写口
// (`notes.write_user_notes`)**只换**其中一段 —— 两边的判据必须是同一个,不然会出现
// 「界面上显示的那一段」与「保存时被换掉的那一段」不是同一段:屏幕上看不出任何异常,
// 保存之后文件里少掉的却是讲解记录。

test('splitNoteSections:全部二级段落,保序,body 两端空白剥掉', () => {
  const note = [
    '## 要点', '- 甲', '',
    '## 讲解记录', '第 1 轮', '',
    '## 我的笔记', '我自己写的', '',
  ].join('\n');
  const secs = md.splitNoteSections(note);
  assert.deepEqual(plain(secs.map((s) => s.title)), ['要点', '讲解记录', '我的笔记']);
  // 段落只吃**下一行 `##` 之前**的内容 —— 把后一段的正文并进前一段的话,
  // 「我的笔记」那一段会连带讲解记录一起被写口换掉(整段覆盖),而页面上看不出来。
  assert.deepEqual(plain(secs.map((s) => s.body)), ['- 甲', '第 1 轮', '我自己写的']);
});

test('splitNoteSections:front-matter 先剥掉,`# 一级标题` 不算一段', () => {
  const note = [
    '---', 'name: 混合检索', '---', '',
    '# 混合检索', '',
    '## 要点', '- 甲', '',
  ].join('\n');
  const secs = md.splitNoteSections(note);
  assert.deepEqual(plain(secs.map((s) => s.title)), ['要点'], 'front-matter 或一级标题被当成了一段');
  assert.equal(secs[0].body, '- 甲');
});

test('splitNoteSections:讲解记录里的 `###` 子标题**不**切断段落', () => {
  // `learning/session.py` 每一轮都会往「讲解记录」里追加一段 `### 标题 + 时间`,
  // 所以这一段天然含有三级标题。要是判据写成了「以 # 开头」,讲解记录会被切成几十段,
  // 而页面上只会看到「讲解记录只剩最后一轮」。
  const note = [
    '## 讲解记录', '',
    '### 第 1 轮（10:00）', '先讲这个。', '',
    '### 第 2 轮（10:05）', '再讲这个。', '',
    '## 我的笔记', '我写的', '',
  ].join('\n');
  const secs = md.splitNoteSections(note);
  assert.deepEqual(plain(secs.map((s) => s.title)), ['讲解记录', '我的笔记']);
  assert.ok(secs[0].body.includes('先讲这个。') && secs[0].body.includes('再讲这个。'),
    `两轮被切开了:${secs[0].body}`);
});

test('splitNoteSections:正文里提到 `## 提纲` 的一句不被当成新段落', () => {
  // 判据是**整行** `^## `,与 `notes.py` 的 `heading_index` 同一套。少了这个约束,
  // 讲解记录里写一句「参见上面的 ## 提纲」就会把段落从中间切开。
  const note = '## 讲解记录\n\n参见上面的 ## 提纲 那一节。\n\n## 我的笔记\n\n我写的\n';
  assert.deepEqual(plain(md.splitNoteSections(note).map((s) => s.title)), ['讲解记录', '我的笔记']);
});

test('splitNoteSections:没有二级段落时返回空数组(不吐一段空标题)', () => {
  assert.deepEqual(plain(md.splitNoteSections('# 只有一级标题\n正文\n')), []);
  assert.deepEqual(plain(md.splitNoteSections('')), []);
  assert.deepEqual(plain(md.splitNoteSections(null)), []);
});

test('pickNoteSections 就是 splitNoteSections 的一次过滤(行为逐字不变)', () => {
  const note = '## 提纲\n1. 甲\n\n## 要点\n- 乙\n\n## 讲解记录\n长的\n\n## 我的笔记\n我写的\n';
  const all = md.splitNoteSections(note);
  const picked = md.pickNoteSections(note);
  assert.deepEqual(plain(picked), plain(all.filter((s) => /要点|我的笔记/.test(s.title))));
});

// ---- splitNoteOutline:提纲单独拆出来(它要在面板上长成**可点的一列**)--------

test('splitNoteOutline:条目拆出来,正文里那一段被摘掉', () => {
  const note = [
    '---', 'node_id: n1', '---', '',
    '# 混合检索', '',
    '## 提纲', '', '1. 先说清为什么要混合', '2. 再比较两路召回的优劣', '',
    '## 要点', '- 甲', '',
    '## 我的笔记', '我自己写的', '',
  ].join('\n');

  const { items, rest } = md.splitNoteOutline(note);
  assert.deepEqual(plain(items), ['先说清为什么要混合', '再比较两路召回的优劣']);
  // 留下的正文里**不能**再有提纲那一段 —— 否则同一份提纲会在面板上出现两次
  // (一次是可点的一列,一次是不可点的死文字)
  assert.ok(!rest.includes('提纲'), `正文里还留着提纲段:${rest}`);
  assert.ok(!rest.includes('先说清为什么要混合'));
  assert.ok(rest.startsWith('# 混合检索'), '正文首行丢了');
  assert.ok(rest.includes('## 要点') && rest.includes('## 我的笔记'));
});

test('splitNoteOutline:「我的笔记」里的列表**不算**提纲条目', () => {
  // 用户很可能在建了提纲之后自己在笔记里也列几条。判据必须止于下一个二级标题,
  // 否则用户手写的清单会变成面板上的「学习步骤」。
  const note = [
    '# A', '',
    '## 提纲', '1. 第一步', '',
    '## 我的笔记', '', '- 我自己列的一条', '- 又一条', '',
  ].join('\n');
  const { items, rest } = md.splitNoteOutline(note);
  assert.deepEqual(plain(items), ['第一步']);
  assert.ok(rest.includes('- 我自己列的一条'), '用户笔记被当成提纲吞掉了');
});

test('splitNoteOutline:没有提纲段时原样返回,正文一字不动', () => {
  const note = '---\na: 1\n---\n\n# A\n\n## 要点\n- 甲\n';
  const { items, rest } = md.splitNoteOutline(note);
  assert.deepEqual(plain(items), []);
  assert.equal(rest, '# A\n\n## 要点\n- 甲');
});

test('splitNoteOutline:三级标题不算,空的提纲段不吐出条目', () => {
  // `### 提纲` 是用户自己写的小标题;同一份文件里它不该被当成机器写的那一段。
  const note = '# A\n\n### 提纲\n\n1. 这不是机器写的那一段\n\n## 我的笔记\n\nx\n';
  assert.deepEqual(plain(md.splitNoteOutline(note).items), []);
  // 提纲段存在但没有条目(模型没给出可用条目时被摘掉的那种) → 空数组,不报错
  assert.deepEqual(plain(md.splitNoteOutline('# A\n\n## 提纲\n\n## 要点\n- 甲\n').items), []);
});

test('splitNoteOutline:条目的编号/项目符号不进条目文本,前导空白也剥掉', () => {
  const note = '# A\n\n## 提纲\n\n-  带空格的项目符号\n2) 括号编号\n3.普通编号\n\n## 要点\n- 甲\n';
  // 三种编号都认(`-` / `2)` / `3.`),各自的前缀与多余空白剥掉。
  // `3.普通编号` 没有空格,按 CommonMark **不是**列表项 —— 于是它既不成条目、
  // 也不留在正文里:提纲段整段被摘掉,它跟着一起走了。
  const { items, rest } = md.splitNoteOutline(note);
  assert.deepEqual(plain(items), ['带空格的项目符号', '括号编号']);
  assert.ok(!rest.includes('3.普通编号'), '提纲段里没被认成条目的行也要跟着摘掉');
});
