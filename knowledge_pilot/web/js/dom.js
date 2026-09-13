/**
 * DOM 作用域查询小工具。
 *
 * **存在的理由**:三栏工作台里同一个组件可能同时挂两处(右栏 Inspector 与左下角浮动
 * 助手),而 `document.getElementById` 在重复 id 时**返回文档序第一个** —— 于是
 * 「助手里的发送按钮控制了 Inspector 的输入框」这种 bug 在任何静态检查里都看不出来。
 * 规则:
 *
 *   - `document.getElementById` 只允许出现在 `views/shell.js` 与 `app.js`(挂载点);
 *   - 所有 view 内部一律走 `q(root, id)`,root 是该 view 自己的容器。
 *
 * 用 `root.querySelector('#id')` 而不是自己给元素起 `data-*` 名:选择器与
 * `css/*.css` 里写的是同一套 id,拆分时**不需要迁移任何一条 CSS 选择器** ——
 * 迁移就意味着「漏一条 → 某个元素静默失去样式」,而这种失败测试看不见。
 *
 * 这条规则由 `tests/js/assembly.test.mjs` 的源码扫描机械钉住。
 */
(function (KP) {
  'use strict';

  /** 在 root 子树里按 id 找元素。root 为 null 时返回 null(测试桩里常见)。 */
  function q(root, id) {
    if (!root || !root.querySelector) return null;
    return root.querySelector('#' + id);
  }

  /** 在 root 子树里按选择器找全部。用于类选择器(如面板里的 `.pt` 要点芯片)。 */
  function qa(root, selector) {
    if (!root || !root.querySelectorAll) return [];
    return Array.prototype.slice.call(root.querySelectorAll(selector));
  }

  /**
   * 事件委托:从 `el` 往上找最近的带某属性的祖先(不含 `stopAt`)。
   * 图谱节点用 `data-id`,侧栏主题卡用 `data-topic`。
   */
  function closestAttr(el, attr, stopAt) {
    let node = el;
    while (node && node !== stopAt) {
      if (node.getAttribute && node.getAttribute(attr) != null) return node;
      node = node.parentNode;
    }
    return null;
  }

  function clear(el) {
    if (el) el.innerHTML = '';
  }

  KP.dom = { q, qa, closestAttr, clear };
})(window.KP = window.KP || {});
