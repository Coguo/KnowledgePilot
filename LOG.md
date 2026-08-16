# KnowledgePilot 项目日志

> 记录每个阶段的工作：决策、实现、测试、已知问题、下一步。

## 阶段概览

| Phase | 内容 | 状态 | 完成日期 |
|-------|------|------|----------|
| 0 | 基础 Research Chat（用户 → LLM → 搜索 → 答案） | ✅ 完成 | 2026-08-16 |
| 1 | RAG（Chunk → Embedding → VectorDB → Retrieval） | ⬜ 未开始 | - |
| 2 | RAG 优化（Hybrid Search / Reranker / Query Rewrite / Evaluation） | ⬜ 未开始 | - |
| 3 | LangGraph Agent 编排 | ⬜ 未开始 | - |
| 4 | Memory（研究历史 / 用户画像） | ⬜ 未开始 | - |
| 5 | Knowledge Graph / GraphRAG | ⬜ 未开始 | - |
| 6 | MCP | ⬜ 未开始 | - |
| 7 | 工程化（Redis / PostgreSQL / Model Gateway / Docker） | ⬜ 未开始 | - |

---

## Phase 0 — 基础 Research Chat（2026-08-16）

### 项目方向

**AI Research Agent**：针对开放性研究问题，自主完成 任务拆解 → 资料搜索 → 知识库构建 → RAG 检索 → 分析 → 验证 → 生成带引用报告。定位是"能独立做研究的 Agent"，不是教学系统；第一用户是作者本人。

### 关键决策

- **网页版优先、桌面版后置**：Agent 引擎对外只发事件流、与 UI 完全解耦；网页版把事件映射为 SSE。未来若做桌面版，只需新增一个前端消费同一接口，不返工。
- **默认 LLM：DeepSeek**（OpenAI 兼容接口，国内直连、成本低）。
- **搜索服务商暂不定**：先建 `search` 抽象层 + Stub 占位，后续接 Tavily / DuckDuckGo / Brave 只加一个类。
- **手写 tool-calling 循环**，不引 LangChain/LangGraph——Phase 0 的目标就是学透 LLM API / Prompt / Tool Calling / Streaming；到 Phase 3 需要状态编排时再引入 LangGraph。

### 技术栈

| 层 | 选择 |
|----|------|
| 语言 | Python 3.11（conda 环境 `knowledgepilot`） |
| LLM | `openai` SDK（AsyncOpenAI，流式 + 工具调用） |
| Web | FastAPI + uvicorn，SSE 手写 `StreamingResponse` |
| 配置 | `pydantic-settings` + `.env` |
| 测试 | `pytest` + `pytest-asyncio`（Fake LLM 注入，全离线） |

### 目录结构

```
knowledge_pilot/
├── config.py      # 配置（环境变量 / .env，不硬编码密钥）
├── llm/           # LLM 客户端封装（默认 DeepSeek）
├── search/        # 搜索抽象层（当前 stub，可插拔）
├── agent/         # 手写 tool-calling 循环，对外发事件流
├── api/           # FastAPI：事件流 → SSE
└── web/           # 前端页面（单个 index.html）
```

### 实现内容

- **配置**：`Settings` 读 `.env` / 环境变量；未配置密钥时给出清晰错误。
- **LLM 客户端**：封装 `AsyncOpenAI`，`stream_chat` 产出内容增量 + tool_call 增量。
- **工具**：`search_web` function schema + 执行注册表；执行结果格式化后回填。
- **Agent 循环**：流式调用 → 累积 tool_calls（参数分片累加）→ 执行工具 → 回填 tool message → 直到无工具调用产出最终答案；`MAX_TOOL_ROUNDS = 4` 轮次上限兜底防死循环。
- **SSE 事件协议**：`token` / `tool_call` / `tool_result` / `done` 四类帧。
- **前端**：原生 JS + fetch 流式读取，渲染流式 token 与搜索状态行；无构建工具链。

### 测试

- 12 个测试全部通过（离线运行）。
- 覆盖：直接回答 / 先搜索再回答（验证事件序列与 tool message 回填）/ 轮次上限兜底 / 未知工具报错 / SSE 端点冒烟 / 配置默认值与覆盖 / Stub schema。

### 已知问题

- 前端为极简版：无 Markdown 渲染、无多轮上下文、无历史记录（Phase 0 有意裁剪）。
- LLM 网络异常会中断流，前端仅显示错误文案，无重试。
- 流式 tool_calls 累加按 OpenAI 兼容格式编写，个别模型分片方式不同可能需微调。

### 下一步

1. 接真实搜索服务商（如 Tavily），让搜索从占位变成真实结果。
2. 进入 Phase 1：RAG——研究资料动态获取 → 解析 → Chunk → Embedding → 向量库检索，回答问题基于检索资料。
