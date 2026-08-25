# KnowledgePilot 项目日志

> 记录每个阶段的工作：决策、实现、测试、已知问题、下一步。

## 阶段概览

| Phase | 内容 | 状态 | 完成日期 |
|-------|------|------|----------|
| 0 | 基础 Research Chat（用户 → LLM → 搜索 → 答案） | ✅ 完成 | 2026-08-16 |
| 1 | RAG（Chunk → Embedding → VectorDB → Retrieval） | ✅ 完成 | 2026-08-25 |
| 2 | RAG 优化（Recursive Chunk / Hybrid / Reranker / Query Rewrite / Evaluation） | ✅ 完成 | 2026-08-25 |
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

---

## Phase 2 — RAG 优化（2026-08-25）

### 项目方向

在 Phase 1 的 fixed-size 向量检索基线上，沿 **Chunk / Retrieval / Rerank / Rewrite** 四个轴各加可插拔优化，并建立**离线评测矩阵**（Recall@K / MRR / Latency / Token Cost）回答"为什么新方案更好"。详细设计见 `docs/phase-2.md`。

### 关键决策

- **RecursiveChunker**（不引 semantic）：按 `\n\n→\n→。！？；，→空格→单字符` 递归切到自然边界再拼回，保留段落/句子语义；overlap 衔接不变量可精确测试。新增 `create_chunker(strategy, ...)` 工厂与 `RAG_CHUNK_STRATEGY`。
- **BM25 + 手写 CJK 分词**（不引 jieba）：拉丁词整词保留 + 中文双字重叠，零依赖；`rank-bm25` 随 `[rag]` extra。向量抓语义、BM25 抓精确关键词（缩写/标识符），互补。
- **RRF 融合**（不调权重）：两路分数量纲不同不能直接加，只取排名、k=60 标准常数，双路命中加成，零调参。
- **CrossEncoder 精排**：bge-reranker-base，query×chunk 联合编码，比双塔粗排更准；只精排候选池（`RAG_RERANK_CANDIDATES=20`），共享单例懒加载。
- **Query Rewrite 默认关**：用一次 LLM 调用换更好检索，收益未量化前不常开；`LLMClient.complete()` 非流式接口复用 ChatClient。
- **运行时默认 = 评测推荐组合**：Hybrid ON / Rerank ON / Rewrite OFF / recursive 可选。
- **评测双模式**：离线（确定性替身，零重依赖，可复现）验证链路与趋势；`--real` 用真实模型出可引用数据。离线组件独立于 `tests/fakes.py`（正确分层）。
- **顺手修两个 Phase 1 遗留**：`search_score` 元数据丢失（chunker 透传 + store 持久化双修）、`task_{uuid}` collection 泄漏（`RAGPipeline.close()` + api finally 清理）。

### 技术栈

| 层 | 选择 |
|----|------|
| 新增组件 | `rag/lexical.py`（BM25+分词）、`rag/hybrid.py`（RRF）、`rag/reranker.py`（CrossEncoder）、`rag/rewrite.py`（LLM 改写） |
| 评测 | `rag/eval/`（dataset/metrics/runner/offline/real/__main__ + CLI） |
| 依赖 | `rank-bm25>=0.2.2`（加入 `[rag]` extra）；rerank 复用已装的 sentence-transformers |

### 实现内容

- `RecursiveChunker` / `create_chunker` / metadata 透传（`chunker.py`）。
- `Bm25Index`（rank-bm25 懒加载、dirty 重建）+ `tokenize` CJK 分词（`lexical.py`）；ingestion 可选喂入词法索引。
- `HybridRetriever` RRF 融合 + `format_hits_context` 抽为模块级函数（`hybrid.py`/`retriever.py`）。
- `CrossEncoderReranker` 共享单例（`reranker.py`）；`LLMQueryRewriter` + `complete()`（`rewrite.py`/`llm/client.py`）。
- `RAGPipeline` 重接线：ingest(带 BM25) → rewrite → retrieve(候选池) → rerank/截断 → 带来源片段；`close()` 幂等清理（`pipeline.py`）。
- `create_rag_pipeline(settings, llm)` 按开关装配（`rag/__init__.py`）；`api/main.py` 先建 LLM 再传工厂、finally 里 close。
- 配置新增 6 个旋钮（`config.py`）+ `.env.example` 文档化。
- 评测包 + fixture `tests/fixtures/eval/small.json` + CLI（轴过滤 / `--json-out` / `--real`）。

### 测试

- **82 通过 + 6 跳过**（rank-bm25 / chromadb / trafilatura 未装时 `importorskip`）。Phase 2 新增约 49 个：递归分块不变量、CJK 分词、RRF 融合、rerank 懒加载、rewrite 回退、pipeline 混合/精排/改写路径与 close、评测指标数学与 16 行矩阵、Agent 端到端（事件协议仍不变）、api close 清理。
- 过程中修掉一个真 bug：评测词法索引原为跨 item 共享、`add_chunks` 累积导致结果不可复现 → 改为每 item 独立工厂实例，`test_run_eval_deterministic` 守护可复现性。

### 离线评测结论（small.json，top_k=3，趋势性）

- 基线 fixed/vector/no/original：Recall@3=0.667、MRR=0.500。
- **Hybrid 补召回**：Recall→1.000、MRR→0.833；**Rerank 提排序**：MRR→1.000。成本为 rerank 约 0.2–0.7ms/item 与少量 token。
- 改写（离线关键词替身）在 vector+无精排时把 Recall 拉到 1.000 —— 改写价值的第一个迹象，待 `--real` 确认。
- recursive 与 fixed 在小数据集上几乎无差（需扩数据集或真实模型验证）。

### 已知问题

- CPU rerank 延迟 ~1ms 级；`--real` 需下载 bge-reranker-base ~1.1GB（国内走 HF 镜像）。
- 离线组件是确定性替身，不等价真实语义质量；评测数据集仅 3 条 query（smoke 级），正式结论需扩充。
- 改写默认关，收益待 `--real` 数据量化。

### 下一步

- 扩充评测数据集（几十条 query），`--real` 出可引用矩阵。
- semantic chunk 对照进同一矩阵；知识库检索暴露为独立 tool（`search_knowledge_base`）。
- 之后进入 Phase 3（LangGraph Agent 编排），`rag/` 各组件已是 Protocol 接缝可直接复用。
