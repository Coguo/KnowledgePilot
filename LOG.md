# KnowledgePilot 项目日志

> 记录每个阶段的工作：决策、实现、测试、已知问题、下一步。

## 阶段概览

| Phase | 内容 | 状态 | 完成日期 |
|-------|------|------|----------|
| 0 | 基础 Research Chat（用户 → LLM → 搜索 → 答案） | ✅ 完成 | 2026-08-16 |
| 1 | RAG（Chunk → Embedding → VectorDB → Retrieval） | ✅ 完成 | 2026-08-25 |
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
- **搜索服务商**：先建 `search` 抽象层 + Stub 占位，Phase 0 收尾时接入 **Tavily**（REST API + httpx，只新增一个类就完成插拔）；后续想换 DuckDuckGo / Brave 同理。
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
- **Tavily 搜索**：`TavilySearchProvider` 通过 httpx 调 Tavily REST API，返回真实标题/链接/正文摘要；未配置 `TAVILY_API_KEY` 时给出清晰错误。httpx 升级为运行时依赖（Phase 1 抓网页正文也要用）。
- **Agent 循环**：流式调用 → 累积 tool_calls（参数分片累加）→ 执行工具 → 回填 tool message → 直到无工具调用产出最终答案；`MAX_TOOL_ROUNDS = 4` 轮次上限兜底防死循环。
- **SSE 事件协议**：`token` / `tool_call` / `tool_result` / `done` 四类帧。
- **前端**：原生 JS + fetch 流式读取，渲染流式 token 与搜索状态行；无构建工具链。

### 测试

- 15 个测试全部通过（离线运行）。
- 覆盖：直接回答 / 先搜索再回答（验证事件序列与 tool message 回填）/ 轮次上限兜底 / 未知工具报错 / SSE 端点冒烟 / 配置默认值与覆盖 / Stub schema / Tavily 构造校验与响应映射（mock httpx）。

### 已知问题

- 前端为极简版：无 Markdown 渲染、无多轮上下文、无历史记录（Phase 0 有意裁剪）。
- LLM 网络异常会中断流，前端仅显示错误文案，无重试。
- 流式 tool_calls 累加按 OpenAI 兼容格式编写，个别模型分片方式不同可能需微调。
- Tavily 用 basic 深度（返回正文摘要，非完整原文）；Phase 1 RAG 需要完整正文时会加独立的网页抓取步骤。

### 下一步

进入 Phase 1：RAG——研究资料动态获取 → 解析 → Chunk → Embedding → 向量库检索，回答问题基于检索资料并带来源引用。

---

## Phase 1 — RAG（2026-08-25）

### 项目方向

把「搜索到的网页动态建库 → 向量检索 → 带来源引用作答」接进现有 Agent，数据随研究任务动态获取（不依赖预置 PDF）。详细设计见 `docs/phase-1.md`。

### 关键决策

- **Embedding：本地 BGE-M3**（用户选型；sentence-transformers 加载，懒加载 + 进程级单例）。首次使用下载约 2GB 模型，国内需 `HF_ENDPOINT=https://hf-mirror.com`。
- **向量库：Chroma**（免费嵌入式；persist_dir 空→内存 EphemeralClient 离线测试，非空→PersistentClient）。每研究任务独立 collection `task_{uuid}`。
- **网页正文：trafilatura**（正文提取开箱即用，不选 bs4）；httpx 抓取，失败回退 `SearchResult.content`（Tavily 摘要）。
- **Agent 接入：search_web 工具内部透明增强**——执行后自动「抓取→chunk→embed→存库→检索」，把【知识库检索结果】拼进 tool message。不改事件协议、不改 `ALL_TOOLS`、`rag=None` 时与 Phase 0 逐字节一致、现有测试零改动。
- **重依赖懒加载 + `[rag]` extra**：`RAG_ENABLED=false` 默认关，未启用时应用照常启动；`pip install -e ".[dev,rag]"` 安装。
- **chunk 策略**：fixed-size 800 / overlap 200（Phase 2 再做 recursive/semantic 对照实验）。

### 技术栈

| 层 | 选择 |
|----|------|
| RAG 模块 | `knowledge_pilot/rag/`（documents/chunker/embedder/fetcher/store/retriever/ingestion/pipeline + `create_rag_pipeline` 工厂） |
| Embedding | 本地 BGE-M3（sentence-transformers，CPU） |
| 向量库 | chromadb（EphemeralClient / PersistentClient，cosine） |
| 抓取/解析 | httpx + trafilatura |

### 实现内容

- `search_web` 工具执行后自动建库并检索，回答带【知识库检索结果】与来源（标题 + URL）。
- 配置新增 `RAG_ENABLED` / `EMBEDDING_*` / `CHROMA_DIR` / `RAG_*` 等字段；`.env.example`、`.gitignore`（`data/`）、`pyproject.toml`（`[rag]` extra）同步更新。
- 未启用 RAG 或未安装 `[rag]` extra 时，应用照常启动（`get_chat_deps` 捕获 ImportError → 清晰 HTTPException）。

### 测试

- 33 通过 + 2 跳过（chromadb / trafilatura 未安装时自动跳过，`importorskip` 兜底）。
- 全离线：FakeEmbedder / InMemoryVectorStore / StubFetcher（`tests/fakes.py`）；覆盖分块 / 懒加载 / 抓取 / 入库 / 检索 / Agent×RAG 集成 / 配置。

### 已知问题

- 首次模型加载慢、CPU embedding 慢；首次下载 2GB 模型。
- torch / chromadb 依赖体积大；`data/chroma` 的 `task_{uuid}` collection 不清理会缓慢增长。
- 检索片段拼入 tool message 增大 token 消耗（top_k=3 + 每段 600 字符，约 +1500-2000 token）。

### 下一步

进入 Phase 2：RAG 优化——recursive/semantic chunk 对照、BM25 Hybrid Search、Reranker、Query Rewrite、Evaluation Dataset（Recall@K / MRR / Faithfulness）。
