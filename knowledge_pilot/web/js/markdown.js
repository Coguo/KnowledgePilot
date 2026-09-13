/**
 * 极简 Markdown 渲染。**先转义,再渲染** —— 顺序不能反。
 *
 * 先渲染后转义会把我们自己生成的标签也转义掉;先转义后渲染则模型写的
 * `<img onerror=...>` 变成纯文本。**转义后字符串里已经没有 `<`**,所以后面插入的
 * 每一个 `<` 都只可能来自本模块的函数 —— 这就是安全性所依赖的不变量。
 *
 * 这个不变量是**看不见的**:顺序写反后页面在正常内容下完全正常,只在模型输出(或
 * 用户输入)里带标签时才炸。所以 `tests/js/markdown.test.mjs` 用 XSS 夹具钉住它。
 */
(function (KP) {
  'use strict';

  const esc = KP.esc;

  /** 行内标记。入参**必须**是已转义文本。 */
  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
      // 链接只放行 http(s):`javascript:` 之类的伪协议会被当成普通文字。
      // 这里的 $2 已是转义结果,不能再 esc 一次(会变成 &amp;amp;);它含有的
      // `"` 早已变成 `&quot;`,放进双引号属性里是安全的。
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }

  function renderMarkdown(md) {
    const lines = String(md == null ? '' : md).replace(/\r\n?/g, '\n').split('\n');
    const out = [];
    let i = 0, list = null, para = [];

    const flushPara = () => {
      if (para.length) { out.push('<p>' + para.map(inline).join('<br>') + '</p>'); para = []; }
    };
    const closeList = () => { if (list) { out.push('</' + list + '>'); list = null; } };
    const openList = (kind) => {
      flushPara();
      if (list !== kind) { closeList(); out.push('<' + kind + '>'); list = kind; }
    };

    while (i < lines.length) {
      const line = lines[i].replace(/\s+$/, '');

      if (/^```/.test(line)) {                       // 围栏代码块:内部一切原样
        flushPara(); closeList();
        const lang = esc(line.slice(3).trim());
        const buf = [];
        i += 1;
        while (i < lines.length && !/^```/.test(lines[i])) { buf.push(esc(lines[i])); i += 1; }
        i += 1;
        out.push('<pre class="code"' + (lang ? ' data-lang="' + lang + '"' : '') +
                 '><code>' + buf.join('\n') + '</code></pre>');
        continue;
      }
      if (!line.trim()) { flushPara(); closeList(); i += 1; continue; }

      let m;
      if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {   // 标题
        flushPara(); closeList();
        const lvl = Math.min(m[1].length, 3);
        out.push('<h' + lvl + '>' + inline(esc(m[2])) + '</h' + lvl + '>');
      } else if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
        flushPara(); closeList(); out.push('<hr>');
      } else if ((m = line.match(/^>\s?(.*)$/))) {   // 引用
        flushPara(); closeList();
        out.push('<blockquote>' + inline(esc(m[1])) + '</blockquote>');
      } else if ((m = line.match(/^[-*+]\s+(.*)$/))) {
        openList('ul'); out.push('<li>' + inline(esc(m[1])) + '</li>');
      } else if ((m = line.match(/^\d+[.)]\s+(.*)$/))) {
        openList('ol'); out.push('<li>' + inline(esc(m[1])) + '</li>');
      } else {
        closeList(); para.push(esc(line));
      }
      i += 1;
    }
    flushPara(); closeList();
    return out.join('');
  }

  /**
   * 拆出 front-matter(`notes.py` 写的 `key: value` 块),正文单独返回。
   *
   * 边界很脆:`indexOf('\n---', 3)` 与 `slice(end + 4)` 差一个字符就会让正文少第一行、
   * 或把闭合的 `---` 渲染出来。所以本文件随附四条边界测试
   * (含「没有闭合围栏时必须原样返回」)。
   */
  function splitFrontMatter(text) {
    const raw = String(text == null ? '' : text);
    if (!raw.startsWith('---')) return { meta: {}, rest: raw };
    const end = raw.indexOf('\n---', 3);
    if (end < 0) return { meta: {}, rest: raw };
    const meta = {};
    raw.slice(3, end).split('\n').forEach((l) => {
      const at = l.indexOf(':');
      if (at > 0) meta[l.slice(0, at).trim()] = l.slice(at + 1).trim();
    });
    return { meta, rest: raw.slice(end + 4).replace(/^\n+/, '') };
  }

  /**
   * 拆出正文里**全部**二级段落(`{title, body}` 数组,保序,先剥 front-matter)。
   *
   * 判据是整行 `^## `(与 `learning/notes.py` 的 `heading_index` / `write_user_notes`
   * 同一个判据):少了这个约束,正文里写一句「参见上面的 ## 提纲」也会被当成一段的
   * 开始。**前端的读与后端的写必须用同一条规则** —— 用 `##` 定位段落这件事在两处
   * 各写一遍,迟早会出现「界面上显示的那一段」与「保存时被换掉的那一段」不是同一段。
   *
   * `# 一级标题` 不进结果:那是知识点自己的名字,右栏顶上已经写着它了。
   */
  function splitNoteSections(body) {
    const { rest } = splitFrontMatter(body);
    const lines = String(rest || '').split('\n');
    const out = [];
    let current = null;
    for (const line of lines) {
      const h = line.match(/^##\s+(.*)$/);
      if (h) {
        current = { title: h[1].trim(), body: [] };
        out.push(current);
        continue;
      }
      if (current) current.body.push(line);
    }
    return out.map((s) => ({ title: s.title, body: s.body.join('\n').trim() }));
  }

  /**
   * 从节点正文里挑出「值得在 Inspector 里展开」的段落。
   *
   * 背景:`learning/session.py` 每轮对话都会 `notes.append_explanation`,把**整段回答原文**
   * 追加进 `## 讲解记录`。所以 `body` 会随时间增长,里面有一份 chat 的完整副本 ——
   * 在 Inspector 里全文渲染会与对话区重复且越来越长。
   * 这里只保留 `## 要点`(模型的提炼)与 `## 我的笔记`(用户自己写的,唯一性最高)。
   *
   * 就是 `splitNoteSections` 的一个过滤(一份规则只留一个实现)。
   */
  function pickNoteSections(body) {
    return splitNoteSections(body).filter((s) => /要点|我的笔记/.test(s.title));
  }

  /**
   * 从节点正文里拆出 `## 提纲`：条目单独返回，正文里**去掉这一段**。
   *
   * 拆出来而不是留在正文里渲染，是因为提纲在右栏要长成**可点的一列**
   * （点一下就就着那一步提问），而 `renderMarkdown` 只会把它渲染成一段死文字。
   * 留在 `rest` 里会让同一份提纲在面板上出现两次（一次可点、一次不可点）。
   *
   * 判据是「这一段是否存在」而不是「条目解析出几条」：`items` 为空时那一段同样被
   * 摘掉——一份写着 `## 提纲` 却一条都列不出来的段落渲染给用户看没有意义，而
   * 后端 `notes.read_outline` 的空结果也会让前端重新请求一次。
   */
  function splitNoteOutline(body) {
    const { rest } = splitFrontMatter(body);
    const lines = String(rest || '').split('\n');
    const items = [];
    const keep = [];
    let inOutline = false;
    for (const line of lines) {
      const h = line.match(/^##\s+(.*)$/);
      if (h) {
        inOutline = /^提纲$/.test(h[1].trim());
        if (!inOutline) keep.push(line);
        continue;
      }
      if (inOutline) {
        const item = line.match(/^\s*(?:\d+[.)]|[-*+])\s+(.*)$/);
        if (item && item[1].trim()) items.push(item[1].trim());
        continue;
      }
      keep.push(line);
    }
    return { items, rest: keep.join('\n').replace(/\n{3,}/g, '\n\n').trim() };
  }

  KP.markdown = { inline, renderMarkdown, splitFrontMatter, splitNoteSections,
                  pickNoteSections, splitNoteOutline };
})(window.KP = window.KP || {});
