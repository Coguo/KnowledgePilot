/**
 * 启动。**全前端唯一有顶层副作用的文件** —— 其余每个文件在加载期只做
 * `window.KP.xxx = ...` 定义,不碰 `document`/`location`/`fetch`。
 *
 * 这条纪律不是洁癖:`tests/js` 用 `vm` 沙箱 + 一个**会抛异常的 `document` Proxy**
 * 逐个加载这些文件,于是「谁在加载期碰了 DOM」立刻变成一条带文件名的失败,
 * 而不是某个只在上线后才出现的时序 bug。
 *
 * 因此 `app.js` 是 harness 唯一跳过的文件(且断言它排在 `<script>` 列表**最后**、
 * 是唯一的那个)。要往这里加逻辑前先想清楚:它不能被单测覆盖。
 */
(function (KP) {
  'use strict';

  async function boot() {
    KP.views.shell.init();
    // 右栏是常驻的一列,先画上占位提示 —— 否则「没有选中节点」在页面上表现为一片
    // 空白窄条,读起来像加载失败。(`shell.init()` 里不能做这件事:inspector 在这个
    // 文件之前加载,init 那一刻它还没定义。)
    KP.views.inspector.close();
    try {
      await KP.views.shell.loadTopics();
    } catch (err) {
      // 主题列表都拉不到 —— 多半是后端没起来。直接把错误摊在主区上。
      KP.views.shell.showDisabled(err.message);
      return;
    }
    if (KP.S.disabled) return;   // 学习图谱未启用:侧栏已空,主区已给出说明
    await KP.views.shell.route();
  }

  KP.app = { boot };

  // `defer` 保证此刻 DOM 已解析完、且各模块都已定义,所以直接启动即可。
  boot();
})(window.KP = window.KP || {});
