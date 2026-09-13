/**
 * 图标与字形集中管理。
 *
 * 前端是**零依赖、离线可跑**的(禁 CDN、禁构建步骤),所以不能引图标库 ——
 * 不集中在这里,同一个字形就会以不同的 Unicode 码点散落在五个 view 的内联字符串里,
 * 于是「未学习」的圈在两处长得不一样。
 *
 * 状态字形特意用**形状差异明显**的字符(空心圈 / 实心圈 / 惊叹号 / 四角星),
 * 而不是同一族的不同填充:节点状态是四通道编码的一部分,靠的就是形状而非颜色。
 */
(function (KP) {
  'use strict';

  const NAV = {
    graph: '◈',
    explore: '❐',
    chat: '⌘',
    settings: '⚙',
  };

  const STATE_GLYPH = {
    unlearned: '○',
    learning: '◉',
    recommended: '!',
    mastered: '✦',
  };

  const UI = {
    // 进度卡的题头标记(设计稿 §8「◇ 知识图谱学习进度」)。**刻意与 `NAV.graph`
    // 的 ◈ 不同** —— 侧栏里导航项和进度卡上下相邻,同一个字形会让两者读成一组。
    diamond: '◇',
    back: '←',
    close: '✕',
    plus: '＋',
    minus: '－',
    fit: '⛶',
    reset: '⟳',
    send: '➤',
    arrow: '→',
    pin: '◆',
    spark: '✨',
    // 助手收起态那条细栏右侧的「展开」箭头。**朝上**是有意的:那条栏停在中间列
    // 底部,内容是从它**上方**展开的 —— 朝下的箭头的下一帧会是「往下掉」。
    up: '▲',
  };

  KP.icons = { NAV, STATE_GLYPH, UI };
})(window.KP = window.KP || {});
