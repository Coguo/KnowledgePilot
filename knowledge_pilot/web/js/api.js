/**
 * 后端接口封装。**全前端唯一知道 URL 的地方** —— 视图层只调 `KP.api.xxx()`。
 *
 * `KP.env.fetch` 是**测试接缝**:单测可以用它替换掉全局 fetch 来注入 503、500、网络错误,
 * 从而在不碰网络的情况下验证「学习图谱未启用」等分支。所有请求都经 `KP.env.fetch`
 * 出去,视图层不许直接写 `fetch(...)`(由 `tests/js/assembly.test.mjs` 的源码扫描守住)。
 */
(function (KP) {
  'use strict';

  const LEARNING = '/api/learning';

  KP.env = {
    /**
     * 调用时解析全局 fetch —— 刻意不在加载期读取,遵守「加载时不得触碰 fetch」的纪律。
     */
    fetch(url, init) {
      if (typeof globalThis.fetch !== 'function') {
        throw new Error('环境没有 fetch;若是测试,请通过 KP.env.fetch 注入桩。');
      }
      return globalThis.fetch(url, init);
    },
  };

  /** 从响应里抠出可读的错误文本:优先 FastAPI 的 `detail`。 */
  async function errorFrom(resp) {
    const detail = await resp.json().then((d) => d && d.detail).catch(() => null);
    const err = new Error(detail || resp.statusText || `请求失败 (${resp.status})`);
    err.status = resp.status;
    return err;
  }

  async function get(path) {
    const resp = await KP.env.fetch(path);
    if (!resp.ok) throw await errorFrom(resp);
    return resp.json();
  }

  async function post(path, body) {
    const resp = await KP.env.fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body == null ? {} : body),
    });
    if (!resp.ok) throw await errorFrom(resp);
    return resp.json();
  }

  async function del(path) {
    const resp = await KP.env.fetch(path, { method: 'DELETE' });
    if (!resp.ok) throw await errorFrom(resp);
    // 当前 `DELETE /topics/{id}` 返回 `{deleted, notes_kept}`,但调用方并不用它;
    // 若某天改成 204 空体,`resp.json()` 会抛 —— 吞掉即可,不要让「删除已成功」报错。
    return resp.json().catch(() => null);
  }

  /** SSE 走 KP.sse.post(半帧缓冲那套在这里被复用,不重复实现)。 */
  function stream(path, body, onFrame) {
    return KP.sse.post(path, body, onFrame);
  }

  KP.api = {
    errorFrom,

    // ---- 主题(「我的探索」) ----
    listTopics: () => get(`${LEARNING}/topics`),
    createTopic: (query, title) => post(`${LEARNING}/topics`, { query, title: title || '' }),
    getTopic: (id) => get(`${LEARNING}/topics/${encodeURIComponent(id)}`),
    deleteTopic: (id) => del(`${LEARNING}/topics/${encodeURIComponent(id)}`),
    /** SSE:研究 → 报告 → 抽知识点 → 落库。帧类型见 agent/events.py。 */
    generateTopic: (id, onFrame) =>
      stream(`${LEARNING}/topics/${encodeURIComponent(id)}/generate`, undefined, onFrame),

    // ---- 知识点 ----
    getNode: (id) => get(`${LEARNING}/nodes/${encodeURIComponent(id)}`),
    getMessages: (id) => get(`${LEARNING}/nodes/${encodeURIComponent(id)}/messages`),
    /** SSE:讲解逐字流出 →(够格时)recommend → done。 */
    chatNode: (id, message, onFrame) =>
      stream(`${LEARNING}/nodes/${encodeURIComponent(id)}/chat`, { message }, onFrame),
    setMastery: (id, mastered) =>
      post(`${LEARNING}/nodes/${encodeURIComponent(id)}/mastery`, { mastered: !!mastered }),
    /**
     * 按需生成某个知识点的提纲。**POST 而不是 GET** —— 它可能调一次 LLM,而
     * 「刷新页面只发 GET」是这个前端恢复状态的全部依赖(见 api 模块顶部)。
     * 已经有提纲的节点这一步只回读文件,零 LLM;返回里带最新 `body`。
     */
    // `force` = 重新生成（会消耗一次 LLM 调用）：后端拿它跳过「已经有就回读」那道
    // 闸门，把 `## 提纲` 那一段换掉。默认那条路上这个字段是 false，行为与以前一样。
    ensureOutline: (id, force) =>
      post(`${LEARNING}/nodes/${encodeURIComponent(id)}/outline`, { force: !!force }),
    /**
     * 保存「我的笔记」那一段。**POST 而不是 GET** —— 它写用户磁盘上的文件。
     *
     * 纯写、零 LLM,所以**不需要 API key**(后端那条路由与其他写口不同,不带
     * `deps`)。返回里带改完之后的完整 `body`:前端直接换上重画,不必自己拼
     * 「`## 我的笔记` 插在哪」——那条规则只该有一个实现(`notes.write_user_notes`)。
     */
    saveNote: (id, note) =>
      post(`${LEARNING}/nodes/${encodeURIComponent(id)}/note`, { note: note || '' }),

    // ---- 研究问答(不建图谱的一次性研究) ----
    /** SSE:token / tool_call / tool_result / done。轮数可覆盖(会被后端钳到硬上限)。 */
    researchChat: (message, opts, onFrame) =>
      stream('/api/chat', Object.assign({ message }, opts || {}), onFrame),
  };
})(window.KP = window.KP || {});
