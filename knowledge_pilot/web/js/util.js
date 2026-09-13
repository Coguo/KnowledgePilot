/**
 * 通用工具。**纯函数,不碰 DOM、不发请求。**
 *
 * 加载纪律(全部 js 文件通用):顶层只做 `window.KP.xxx = ...` 定义,
 * 不得触碰 `document` / `location` / `fetch`。测试用一碰就抛的 `document` Proxy 强制这条。
 */
(function (KP) {
  'use strict';

  /** HTML 转义。**必须先做**:正文是 LLM 生成的,按不可信输入对待。 */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  /** 钳到 [lo, hi]。非数字(NaN / null / undefined)一律返回 lo。 */
  function clamp(v, lo, hi) {
    const n = Number(v);
    if (!Number.isFinite(n)) return lo;
    return Math.min(hi, Math.max(lo, n));
  }

  /**
   * 只取 ISO 日期串的 `YYYY-MM-DD` 部分,**不做时区换算**。
   *
   * 后端给的是带时区的本地 ISO(`store.py` 用 `timespec="seconds"`)。用 `new Date()`
   * 解析再格式化会把「2026-09-10T00:30:00+08:00」在 UTC 机器上显示成 09-09 —— 日期
   * 显示差一天是那种没人会报、但每次看到都别扭的 bug。所以只做字符串截取。
   */
  function fmtDate(iso) {
    const s = String(iso == null ? '' : iso);
    const m = s.match(/^(\d{4}-\d{2}-\d{2})/);
    return m ? m[1] : '';
  }

  /** 只取路径的最后一段(文件名)。**绝不显示服务器绝对路径** —— 会暴露部署布局。 */
  function baseName(path) {
    const s = String(path == null ? '' : path).replace(/\\/g, '/');
    const i = s.lastIndexOf('/');
    return i < 0 ? s : s.slice(i + 1);
  }

  /** 空值兜底:null / undefined / 非字符串 → 空串。用于任何要进 innerHTML 的文本。 */
  function str(v) {
    return v == null ? '' : String(v);
  }

  KP.esc = esc;
  KP.clamp = clamp;
  KP.fmtDate = fmtDate;
  KP.baseName = baseName;
  KP.str = str;
})(window.KP = window.KP || {});
