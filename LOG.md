# KnowledgePilot 项目日志

> 记录每个阶段的工作：决策、实现、测试、已知问题、下一步。

## 阶段概览

| Phase | 内容 | 状态 | 完成日期 |
|-------|------|------|----------|
| 0 | 基础 Research Chat（用户 → LLM → 搜索 → 答案） | ✅ 完成 | 2026-08-16 |
| 1 | RAG（Chunk → Embedding → VectorDB → Retrieval） | ✅ 完成 | 2026-08-25 |
| 2 | RAG 优化（Recursive Chunk / Hybrid / Reranker / Query Rewrite / Evaluation） | ✅ 完成 | 2026-08-25 |
| 3 | LangGraph Agent 编排 | ✅ 完成 | 2026-08-29 |
| 4 | Memory（研究历史 / 用户画像） | ✅ 完成 | 2026-09-01 |
| 5 | Knowledge Graph / GraphRAG | ✅ 完成 | 2026-09-01 |
| 6 | MCP | ✅ 完成 | 2026-09-06 |
| 7 | Agent Evaluation（图级/全栈离线评测 + --real LLM judge） | ✅ 完成 | 2026-09-09 |
| 8 | 工程化（MCP 进程复用 / Model Gateway） | ✅ 完成 | 2026-09-10 |
| 9 | 学习图谱（研究报告 → 持久化学习路径 + 点亮机制） | ✅ 完成 | 2026-09-10 |
| 10 | 前端拆分与知识图谱三栏重设计（7 阶段） | ✅ 完成（阶段 0–7） | 2026-09-11 |
| 11 | 工程化续（Docker） | ⬜ 未开始 | - |

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

---

## Phase 3 — LangGraph Agent 编排（2026-08-29）

### 项目方向

用 **LangGraph** 把 Phase 0-2 的单轮线性研究升级为多阶段工作流：`Planner（拆解）→ Research（Agentic 工具循环）→ Evaluate（评估充分性）→ 不充分条件循环 → Synthesis（带引用报告）`。详细设计见 `docs/phase-3.md`。

### 关键决策

- **研究任务本质是多阶段 + 条件循环**，这是引入 LangGraph（State/Node/Edge/Conditional Edge/Checkpoint）的"实际需求"时点（规格 §2）。**核心版范围**：不引入 KG/MCP/复杂 Memory；`AGENT_MODE=graph|loop` 开关保留旧单轮循环做回归对比。
- **Research 节点复用现有 LLM tool-calling 循环（Agentic）**：图为高层编排，工具执行为底层能力，不重复造轮子；在工具边界经 `on_search_results` 钩子采集结构化证据（默认 None，Phase 0-2 路径零改动）。
- **事件流式走 langgraph 原生 `stream_mode="custom"` + `get_stream_writer()`**：节点内实时推事件、runner 一行消费——替代了手写 asyncio.Queue + create_task 方案，少一堆轮询/异常/孤儿任务坑。
- **证据用 `Annotated[list, operator.add]` reducer**（research 循环多次写，整体替换会丢前几轮）；`iteration` 唯一写者在 evaluate；`max_iterations` 由 runner 注入初始 state 不硬编码。
- **DeepSeek `json_object` 模式硬性要求 prompt 含 "json" 字样**（否则 HTTP 400）——planner/evaluate 的 system prompt 写死"输出 JSON" + 示例结构；解析失败分别回退单步计划 / 默认充分（防死循环）。`llm/json_utils.py` 提供健壮 JSON 解析。
- **MemorySaver（内存）**：满足 Checkpoint 学习目标且零基建；每次运行现建现编译 + 唯一 `thread_id`（MemorySaver 强制要求）即隔离并发。跨重启断点恢复（SqliteSaver）留到 Phase 4。

### 技术栈

| 层 | 选择 |
|----|------|
| 编排 | langgraph>=0.4（base dependencies；`requires-python` 提到 >=3.11） |
| 新模块 | `agent/graph.py`（ResearchState + 4 async node + 条件边 + runner）、`llm/json_utils.py` |
| 事件 | `agent/events.py` 新增 PlanEvent / StatusEvent / EvalEvent |
| 配置 | `agent_mode`（graph/loop，默认 graph）、`agent_max_iterations=3` |

### 实现内容

- 图拓扑 `START → planner → research → evaluate →(条件)→ synthesize → END`；条件边 `route_after_evaluate`：`sufficient 或 iteration>=max_iterations → synthesize`，否则回 research。
- research 节点：`query + plan + refined_instruction` 组研究指令 → `run_research`（agentic 循环）→ 钩子采集 `EvidenceItem(source/title/snippet)`；**丢弃研究阶段 TokenEvent 与内层 DoneEvent**（防污染最终报告），转发 tool 事件。
- synthesize：基于 query+plan+evidence 写带 `[1][2]` 引用 markdown 报告，`DoneEvent` 结尾（事件流语义与 Phase 0-2 一致）。
- `/api/chat` 按 `agent_mode` 分支 graph/loop；`_sse_frame` 扩展三类新事件；前端最小渲染 plan/status/eval。
- `llm/client.py`：`complete()` 加 `response_format` 透传（DeepSeek json_object）。

### 测试

- 新增 `tests/test_agent_graph.py`（7 个）：充分直通事件序列、不足循环+证据 reducer 累计、达上限兜底防死循环、planner/evaluate JSON 解析失败回退、证据采集、事件不串轮。
- `tests/test_api.py`：graph 模式 SSE 帧含 plan/eval/done + `complete_calls==3`；loop 模式回归旧序列。`tests/test_config.py` 新增 agent 默认值。
- **测试全离线**（FakeChatClient 脚本化 LLM + Stub 搜索），旧 82 测试零改动。预计全绿：旧 82 + 6 跳过 + 新增 graph 测试（沙箱被 Device Guard 拦截无法运行 env python，**需本地验证**）。

### 已知问题

- `get_stream_writer()` 依赖 langgraph>=0.4（依赖下限已固定）；runner 有 `aget_state` 兜底补发 DoneEvent，但中间事件若缺失会表现为测试失败。
- 报告非流式（synthesize 用 `complete()` 一次返回）；RAG 增强片段不进 evidence（只覆盖 web 搜索原始结果）；图内 LLM 调用异常无重试。
- `evidence` 跨轮可能重复（只做轮内按 URL 去重）。

### 下一步

- 进入 **Phase 4：Memory**——研究历史（query/plan/report/来源）落库复用；MemorySaver 换 SqliteSaver + 断点恢复；合成流式化；RAG hits 进证据；图级离线评测（Agent Evaluation）回答「图相比单轮循环是否真的更好」。

---

## Phase 4 — Memory（2026-09-01）

### 项目方向

回答规格 §9 的核心问题：**RAG 回答「世界上关于这个问题有哪些资料」，Memory 回答「这个用户之前研究过什么」**。研究完成后把 query/plan/evidence/report/sources 落库；新研究开始时按关键词召回相关历史注入规划流程复用；图 checkpoint 从内存（MemorySaver）升级为磁盘持久化（SqliteSaver）。详细设计见 `docs/phase-4.md`。

### 关键决策

- **Memory 与 RAG 分离、用 SQLite（stdlib）而非向量库**：历史记录数据量小、结构固定，SQLite 零重依赖、离线可测；语义向量召回留作后续可选增强（复用 RAG embedder）。
- **默认关（`MEMORY_ENABLED=false`），opt-in**：与 RAG 同款模式，默认行为与 Phase 3 逐字节一致，旧测试零改动。
- **关键词召回（中文双字重叠 + 拉丁整词，独立于 rag/lexical）**：零依赖、确定性、离线可测；不引入 jieba/embedding，符合「不为展示技术强行加」原则。
- **记忆注入 planner（不注入 research/synthesize）**：研究计划最能反映「已研究过什么 → 深化或补缺」；research/synthesize 注入留作后续。
- **SqliteSaver 用 sync 版**：只依赖 stdlib sqlite3，官方推荐轻量场景；async 版需 aiosqlite 且有「async checkpointer + sync 调用挂起」已知坑。懒导入失败回退 MemorySaver。
- **知识状态不单独建表**：一条历史记录（query + 日期 + 报告摘要）本身即在表达「研究过什么、结论如何」，召回即知识状态。
- **`memory` 为可选参数、`run_research_graph` 向后兼容**；每请求建临时 store、请求结束关闭（与 RAG 同款生命周期）。

### 技术栈

| 层 | 选择 |
|----|------|
| 新模块 | `knowledge_pilot/memory/`（store.py / context.py / __init__.py，纯 stdlib sqlite3） |
| 图集成 | `agent/graph.py`：`run_research_graph(..., memory, memory_top_k, checkpoint_db)` + planner memory_context + `_drive` 抽取 |
| Checkpoint | `langgraph-checkpoint-sqlite>=1.0`（sync SqliteSaver；base dependencies） |
| 事件 | `MemoryEvent(found)`；SSE `memory` 帧；前端「🧠 找到 N 条历史研究记录」 |

### 实现内容

- `memory/store.py`：`ResearchMemoryStore`——`research_runs` 表 + `save_run` / `search`（关键词打分召回，rowid 定序）/ `recent` / `count` / `close`；线程锁 + `check_same_thread=False`。
- `memory/context.py`：`build_memory_context` 拼「历史研究背景」prompt 块。
- `agent/graph.py`：开跑前召回 → `MemoryEvent` + 注入 planner；跑完 `aget_state` → `save_run`（sources 按 URL 去重）；SqliteSaver 懒导入回退。
- `config.py` / `.env.example`：`memory_enabled` / `memory_db_path` / `memory_checkpoint_db_path` / `memory_top_k`。
- `api/main.py`：`ChatDeps.memory` + 按开关建/关 store + `memory` 帧。
- 前端副标题 + memory 事件渲染。

### 测试

- **新增 19 个**：`test_memory_store.py`（11，不依赖 langgraph 可独立跑）+ `test_memory_graph.py`（6）+ `test_api.py`（2：帧编码 + graph 模式 memory 帧）+ `test_config.py`（2）。
- 全离线（Fake LLM + Stub 搜索 + tmp_path SQLite）；旧测试零改动。
- 已本地跑通：`test_memory_store.py` + `test_config.py` **16 通过**（不依赖 langgraph）。
- **graph/api 测试需本地执行**：env 尚未安装 langgraph / langgraph-checkpoint-sqlite（Phase 3 依赖同样未装），沙箱无法运行。

### 已知问题

- 召回是浅层关键词匹配，无语义相似度（同义表达可能召回不到；后续可加 embedding 分支）。
- SqliteSaver 路径未经本环境运行验证（依赖未装）；sync saver 写入短暂阻塞事件循环（本地可忽略）。
- 跨重启「断点恢复」UX 依赖 HITL（本阶段 checkpoint 只保证持久化到磁盘）。
- 承袭 Phase 3：报告非流式、RAG hits 不进 evidence、图内 LLM 无重试。

### 下一步

- **向量语义召回**（Memory search 加 embedding 分支，与关键词混合）→ 需要 `[rag]` extra。
- **记忆注入 research/synthesize**；**SqliteSaver 断点恢复 + HITL**（研究可暂停/恢复、人工介入）。
- **图级离线评测（Agent Evaluation，规格 §13）**：planner 拆解质量 / evaluate 判定准确率，用数据回答「图 + 记忆相比单轮循环是否真的更好」。
- 之后按规格推进 Knowledge Graph / MCP（各有明确触发条件）。

---

## Phase 5 — Knowledge Graph（2026-09-01）

### 项目方向

回答规格 §8 的核心命题：**Vector RAG + Knowledge Graph 并存**——RAG 给原始文本证据，KG 给结构化关系信息。研究结束后从已收集资料用 LLM 抽取实体与关系，建**本次任务的内存知识图谱**；按研究问题关键词匹配实体、BFS 展开子图，把命中的三元组作为「相关实体关系」块注入综合报告 prompt。用户已确认：**核心版 + 纯 stdlib 手写图存储（零新依赖）**，并选择跳过 Phase 4 本地验证直接开工。详细设计见 `docs/phase-5.md`。

### 关键决策

- **触发条件已确认**：用户选择按规格 Phase 5 推进 KG 核心版（「文本检索无法充分表达实体关系」时引入）。**范围外**：跨任务图谱积累（GraphRAG）、图谱持久化、`query_knowledge_graph` 工具、查询实体 LLM 抽取/语义匹配——都留后续，避免堆功能。
- **图存储：纯 stdlib 手写 dict 邻接表**（用户确认，不用 networkx）：per-task 图很小（几十实体），BFS 遍历手动写就够；与 Memory（sqlite）、BM25、分词器手写的零依赖风格一致；离线确定性可测。无新增依赖，`pyproject.toml` 零改动。
- **抽实体/关系 = 一次 LLM 调用**（`complete(json_object)` + 健壮解析，prompt 含 "json" 满足 DeepSeek 硬性要求）：从拼接证据抽取，失败/无实体 → `KgEvent(0,0,0)`、`kg_context=""`，**绝不阻断研究**（KG 是可选增强）。
- **查询→实体匹配用确定性关键词重叠**（中文双字 + 拉丁整词，独立实现不耦合 memory/rag）：词干匹配（chunk⊆chunking）、大小写归一、单字符误报防护（len≥2）、命中上限 10 防通用词拉爆整图。不二次调 LLM，全离线可测；LLM/嵌入语义匹配留作后续增强。
- **图谱只注入 synthesize（不注入 planner/research）**：KG 的价值在「报告考虑结构化关系」，规划/研究中不需要。
- **`kg_enabled=False` 默认关**，与 Phase 4 逐字节一致、旧测试零改动；KG 只在 `agent_mode="graph"` 生效（loop 模式无 KG）。**无工厂、无 ChatDeps 改动**：图是 per-task 临时对象，API 层只透传 `kg_enabled` / `kg_hops` 两个开关。

### 技术栈

| 层 | 选择 |
|----|------|
| 新模块 | `knowledge_pilot/kg/`（graph.py 存储/分词/匹配 + extract.py 抽取/格式化 + __init__.py，纯 stdlib） |
| 图集成 | `agent/graph.py`：evaluate 与 synthesize 之间插 `kg` 节点 + `kg_context` 进 ResearchState/synthesize prompt |
| 事件 | `KgEvent(entities, relations, found_triples)`；SSE `kg` 帧；前端「🕸️ 知识图谱：N 实体 / M 关系，命中 K 条」 |
| 配置 | `kg_enabled`（默认 false）、`kg_hops`（默认 2）；`.env.example` 新增段 |
| 依赖 | **零新增**（纯 stdlib dict 邻接） |

### 实现内容

- `kg/graph.py`：`GraphStore`（dict 邻接 + 三元组 set 去重；`add_entities`/`add_relations` 自动建端点 + 归一化去重；`query` 沿出边+入边 BFS，hops≥1 防误配）；`tokenize`/`_token_matches`/`match_query_entities`（关键词匹配 + 命中上限）。
- `kg/extract.py`：`KG_EXTRACT_PROMPT` + `extract_entities_relations`（空文本短路、结构校验、异常吞掉返回空）+ `format_triples`/`build_kg_context`。
- `agent/graph.py`：`ResearchState` 加 `kg_context`；`kg_node`（StatusEvent → 拼证据文本(≤8000 字符) → 抽取 → 建图 → 关键词匹配 → BFS → KgEvent + kg_context）；`_build_app` 条件加 `kg` 节点与 `evaluate→kg→synthesize` 边（禁用时折叠回 Phase 3 字面量，逐字节一致）；`synthesize_node` 在计划与证据之间插图谱块；`run_research_graph` 透传 `kg_enabled`/`kg_hops`。
- `api/main.py`：`_sse_frame` 加 `KgEvent` → `{"type":"kg",...}`；call site 透传开关。前端副标题 + `case 'kg'` 渲染。
- 配置 `config.py` + `.env.example`。

### 测试

- **新增 27 个**（其中 20 个不依赖 langgraph）：`test_kg_store.py`（11：分词/建图/归一化/去重/hops 展开/入边命中/防误配/关键词匹配）+ `test_kg_extract.py`（7：抽取切分/空文本短路/乱码/结构过滤/API 异常吞掉/格式化）+ `test_kg_graph.py`（6：注入 synthesize + KgEvent 计数、禁用零改动、空结果与禁用逐字节一致、抽取失败不阻断、循环时只跑一次、事件顺序）+ `test_api.py`（2）+ `test_config.py`（2）。
- **本地已跑通 93+27 通过（+6 跳过）**：纯 stdlib 的 kg/config/rag/search 测试全绿，旧测试零改动。graph/api 集成测试需 langgraph（env 尚未装，与 Phase 3/4 同情形），**需本地验证**。

### 已知问题

- 关键词匹配是浅层重叠：无语义/LLM/嵌入实体匹配，同义词不命中；通用 CJK 双字可能过匹配（已用 2 字符守卫 + 10 实体上限缓解）。
- 拉丁实体名归一化小写 → 三元组展示为小写（去重鲁棒性的取舍）。
- 每任务 +1 次 LLM 调用（抽取，延迟/成本）；抽取输入截断 8000 字符，尾部关系可能丢；抽取 API 异常被吞 → `KgEvent(0,0,0)`。
- KG 只在 graph 模式生效；loop 模式忽略。graph/api 测试需 langgraph 本地装（用户已选择跳过 Phase 4 验证）。

### 下一步

- **GraphRAG 雏形**：跨任务图谱积累 + 图谱持久化（研究历史与图谱联动）。
- **查询实体 LLM 抽取 / 嵌入语义匹配**（提升同义/泛化召回）。
- **`query_knowledge_graph` 工具**（研究循环内显式查图）——随工具数量增长再考虑 MCP 统一管理。
- 规格 §13 **Agent Evaluation（图级离线评测）**：用数据回答「图 + 记忆相比单轮循环是否真的更好」。
- 之后进入 Phase 6 MCP（工具数量增加时引入）。

---

## Phase 6 — MCP（2026-09-06）

### 项目方向

按规格 §11/§15 Phase 6 触发条件（「当工具数量越来越多，需要统一 Tool 接口」）引入 **MCP**。用户已确认三点：**① 扩真实工具再包 MCP**——先把已有但「被自动注入、LLM 不能主动调」的能力（记忆检索 / 论文检索）变成真实工具，再让 Agent 作为官方 `mcp` SDK 的 **client**，由 server 声明与调度；**② 实现用官方 mcp SDK（FastMCP server + stdio client）**；**③ 首批 = Memory + Papers 两个 server**。`search_web` 保持原生进程内工具不动。详细设计见 `docs/phase-6.md`。

### 关键决策

- **为什么 search_web 不迁 MCP**：它的执行深度耦合父进程——`run_tool` 拿到结构化 `SearchResult` 后触发证据采集钩子（→ EvidenceItem → 来源列表 + KG 抽取）与 RAG 向量增强（`rag.enrich_search`）。stdio MCP 只回文本，这两者会断。这是刻意、可讲的「为什么」（原则 5）。
- **扩真实工具**：新增 `search_memory` / `recent_research`（读既有 SQLite 研究记忆，只读）与 `search_papers`（arXiv API，无 key）三个 LLM 可主动调用的真实工具，全部经 MCP 声明。
- **notes 累加器（核心设计）**：research 节点内层 LLM token 被丢弃，MCP 文本输出若不落库，demo 就是空的。方案：MCP 结果经工具边界钩子（`on_extra_tool_result`，与 `on_search_results` 同构）截断去重后进 `ResearchState.notes`（`Annotated[list, operator.add]`），synthesize 末尾追加「工具补充资料（MCP，非网页搜索来源…）」块。**papers/memory 结果不进 evidence/来源列表/KG**——无可靠 source 槽位，硬造会污染来源语义与落库 sources（语义诚实）。
- **向后兼容神圣**：`mcp_enabled=False`（默认）→ Agent 行为与 Phase 5 逐字节一致、旧测试零改动；MCP 只在 `agent_mode="graph"` 生效。`mcp=None` 时 `tools_effective is ALL_TOOLS`、system prompt 仍是同一常量。
- **依赖锁 v1**：`mcp>=1.9,<2`——v2（2026-07）是破坏性重写（FastMCP→MCPServer、字段蛇形化）。网关只暴露 5 个方法（names/has/tool_schemas/prompt_hint/call），`call_result_to_text` 用 `getattr(result,"isError",getattr(result,"is_error",False))` 双兼容，升级爆炸半径被圈住。
- **纯净分层保离线单测**：`mcp/__init__.py` 纯净（不 re-export gateway，否则 import 触发 mcp）；`convert.py`/`arxiv.py` 零 mcp 依赖，沙箱离线可测。api 顶层零 mcp 依赖，仅在 `mcp_enabled and graph` 时懒 import 网关。
- **每请求 spawn server**：stdio client 是 async → 网关挂在 `event_stream` 的 `async with gw:` 里按请求开/关（sync DI 装不下），断开/异常都走 `__aexit__` 杀子进程。单 server 启动失败只告警 stderr 跳过（可选增强绝不挂研究）。

### 技术栈

| 层 | 选择 |
|----|------|
| 协议 | 官方 `mcp>=1.9,<2`（base dependencies）：FastMCP server + ClientSession/stdio_client |
| 新模块 | `knowledge_pilot/mcp/`（convert.py / arxiv.py 纯函数；gateway.py 网关；servers/{memory,papers}.py 子进程 server） |
| Agent | `loop.py` 可选 `tools/mcp/on_extra_tool_result` + `_dispatch_tool` 路由；`graph.py` `notes` 累加器 |
| 配置 | `mcp_enabled`（默认 false）；`.env.example` 新增段 |
| 前端 | tool_call 状态行按工具名展示（search_web 保留「🔍 正在搜索」，MCP 工具「🛠 正在调用 name」） |

### 实现内容

- `mcp/convert.py`：`to_openai_function_schema`（MCP inputSchema → OpenAI function schema，空/畸形给空骨架防拒收）+ `call_result_to_text`（CallToolResult → 文本，v1/v2 字段双兼容）。
- `mcp/arxiv.py`：`build_arxiv_params` / `parse_arxiv_feed`（xml.etree，命名空间容错，URL 剥版本后缀）/ `search_arxiv`（httpx，不吞异常）/ `format_papers`。
- `mcp/gateway.py`：`MCPServerSpec` + `MCPGateway`（AsyncExitStack 逐个 spawn `python -m` + 握手 + list_tools + 路由/schema/prompt_hint/call）+ `build_mcp_specs`（固定注册表：memory 带 `MEMORY_DB_PATH` 绝对路径 + papers）。
- `mcp/servers/memory.py`：`search_memory`（store.search + build_memory_context）/ `recent_research`（store.recent）；参数夹紧；**绝不 print stdout**。`mcp/servers/papers.py`：`search_papers`（arXiv，异常转可读中文）。
- `loop.py`：可选参数 + `_dispatch_tool`（search_web→原生 run_tool 不动；mcp 工具→`mcp.call` + 钩子；未知→ValueError）。
- `graph.py`：`notes` 进 ResearchState；research_node 增 collect_note 闭包 + MCP prompt_hint 拼研究 system prompt；synthesize 末尾 notes 块（空则逐字节一致）；`run_research_graph(..., mcp=None)` 透传。
- `config.py` + `.env.example`：`mcp_enabled`。
- `api/main.py`：`get_chat_deps` mcp 可用性预检（清晰 500）；`event_stream` graph 分支 `async with gw:` 包 runner。前端 tool_call 按工具名显示 + 副标题 Phase 6。
- `pyproject.toml`：base 依赖 `mcp>=1.9,<2`。

### 测试

- **新增（A/B 双轨）**：A 轨（零 mcp、本地即跑）`test_mcp_convert.py`（13）+ `test_mcp_arxiv.py`（15）+ `test_mcp_config.py`（3）；需 langgraph 的 `test_mcp_graph.py`（4：禁用逐字节一致 / 空网关=禁用 / schema 并入 + 工具事件 + notes 到报告 / 跨轮累计 + 不污染落库 sources）。B 轨（需 mcp）`test_mcp_servers.py`（3）+ `test_mcp_servers_stdio.py`（2，真实 stdio 子进程：握手/list_tools/call 命中）+ `test_api.py` 追加 1（graph+mcp_enabled → search_memory 的 tool_call 帧 + notes 进 synthesize）。
- **本地已跑通**：convert + arxiv + config + 旧 config **= 40+ 通过**。graph/api/B 轨需 langgraph + mcp（用户 `pip install -e ".[dev,rag]"` 后 `-m pytest`）。
- 语义红线测试：MCP 的 arXiv URL 不进 evidence → 不污染 memory 落库 sources（只含 search_web 的 stub URL）。

### 已知问题

- **v1 pin**：装依赖后第一件事 `pip show mcp` 确认 1.x；若只有 2.x → 网关已隔离，按 `MCPServer`+蛇形字段适配。
- **每请求 spawn 2 解释器**：延迟 ~0.3–0.5s，进程复用留 Phase 7 工程化。
- **MCP 结果是自由文本**：进 notes 不进 evidence/来源/KG；报告里「工具补充资料」中的论文 URL 不能当 `[n]` 引用（notes 引导语已注明可不引用）；结构化证据化留后续。
- MCP 只在 graph 模式；arXiv 网络/HTTP 异常在 server 工具内转可读中文；search_papers 联网（无 key），smoke 依赖网络。
- Windows 中文 stdio：子进程 env 显式 `PYTHONUTF8=1`（新旧 SDK 兼容）。

### 下一步

- **Agent Evaluation（规格 §13）**：planner/evaluate/图+记忆+MCP 效果建数据集，用数据回答各阶段是否「真的更好」——下一个大阶段前的验证。
- **工程化 Phase 7**：MCP server 进程复用/常驻、Model Gateway、Docker；向量语义召回、记忆注入 research/synthesize、SqliteSaver 断点 + HITL 等累积项。

---

## Phase 7 — Agent Evaluation（2026-09-09）

### 方向与诚实边界

用户确认 Phase 7 = **Agent Evaluation（规格 §13）**，工程化顺延为 Phase 8。核心问题：**图 + 记忆 + KG + MCP（`all`）相比单轮手写循环（`loop`）是否真的更好**——在进工程化大阶段前用数据回答。

**关键诚实边界（docs/phase-7.md 有完整口径）**：离线 LLM 是**脚本化的**（`ScriptedChatClient` 按 profile 预写每步返回）→ 五档变体在报告质量/覆盖上**不具区分度**（跑通路径的 canonical 报告覆盖率都=1）；离线区分的是**机制 + 系统指标**——脚本扰动下能否完成、工具调用轨迹、complete/stream 次数、error_rate、memory/kg 事件落点、延迟与 token 启发式成本。**质量与「哪个变体真的更好」的答案在 `--real`**：真实 DeepSeek 跑 + LLM judge 逐条判（rubric 判不出的相关性/引用合理性/答非所问）。这套「离线确定性 + --real」镜像 `rag/eval` 双模分层，自洽可讲。

### 关键决策

- **五档变体 = 对比轴**：`loop` / `graph` / `graph+memory` / `graph+kg` / `all`(=graph+memory+kg+mcp)。离线用脚本化 LLM + 确定性 rubric judge（零 key 可复现）；`--real` 换 CountingChatClient 包真实 ChatClient + 真实 search + DeepSeek LLM judge（独立 ChatClient，judge token 不计入 agent 计数）。
- **评测只驱动 agent，不改 `graph/loop/events/tools`**；唯一前置小重构 `agent/__init__.py` 改 PEP 562 懒导出（`run_research_graph` 不再顶层 import）——否则离线纯模块在未装 langgraph 环境 import 即炸。全仓无消费方依赖顶层导出（全走子模块），安全。
- **新包 `agent/eval/`**（镜像 rag/eval）：`dataset`（frozen dataclass + 位置化 ValueError + 白名单单一来源）/ `metrics`（词边界 CJK 守卫、工具选择=required∩requested、工具参数=贪心对齐+宽松相等+容忍多余键）/ `offline`（ScriptedChatClient、StubMCPGateway、canonical 报告、脚本 profile 库、组件工厂）/ `judge`（RubricJudge 确定性 + DeepSeekJudge 含 "json" 词、解析失败回退 rubric 不崩）/ `runner`（driver 懒导入、事件重建 transcript、聚合）/ `real` / `__main__`。**离线组件独立于 tests/fakes**（分层纪律，不 import tests）。
- **Task Success = rubric 覆盖度判定（离线）+ DeepSeek judge（--real）**。rubric 判"期望要点到没到齐"；LLM judge 读完整报告按 `item.aspects` 逐条给判，回答"这报告真的算成功吗"。
- **错误 = 跑通才算 ok**（loop/graph 都无异常哨兵）：抛异常或无 Done 记 error_kind，task_success=0、coverage=0。未知工具 ValueError 一路冒泡（已核验）→ `mcp_only_tool` 条目在非 mcp 档报错、`all` 档经桩网关跑通——离线演示**工具可用性轴**（docs 注明这是人为构造：真实模型不会调 tools= 未暴露的工具，崩溃轴归 error_rate、selection 只查"该调的调了没"）。
- **memory 每 (variant, item) 独立全新库 + pre_seed 预置**（隔离保逐字节确定）；`--real` 用 tempfile 全新 db，**绝不动用户 MEMORY_DB_PATH**。kg 条目只在"会产生结构化证据的搜索"时进脚本（否则证据空 → kg 节点不消费 complete → 索引错位，synth 会吃到 kg JSON——修过）。
- **graph 调用序（离线 canary 依据）**：planner complete(1) → research 每迭代内层 run_research 每轮一次 stream_chat → evaluate complete(1/迭代) → [evidence 非空时 kg complete(1)] → synthesize complete(1) → Done。iterate_to_cap 脚本长度 = `1 + max_iterations + 1 (+kg)`。warmup：graph 档先弃一次首跑（吸收懒导入/首编译），不计延迟。

### 实现

- `agent/eval/`：dataset / metrics / judge / offline / runner / real / __main__ + 包 re-export（`__init__.py` 24 个导出，real 不在此 re-export——需 key 由 CLI/B 轨懒加载）。
- 数据集 `tests/fixtures/eval_agent/small.json`：4 条端到端研究任务——item1 ideal GraphRAG、item2 iterate_to_cap RAG 召回、item3 `mcp_only_tool` agentic RAG/arXiv（可用性轴）、item4 ideal memory（带 pre_seed 历史）。queries 都含 standalone "RAG" 供 kg 匹配。
- CLI：`python -m knowledge_pilot.agent.eval --dataset ... [--variants loop,graph+memory,all] [--max-iterations 3] [--real] [--json-out docs/agent-eval-results.json]`。省略 `--variants` = 五档全跑（"all" 是档名不是哨兵，见 help）。

### 测试（A/B 双轨，同 Phase 3-6 纪律）

- **A 轨纯 stdlib（沙箱即跑，64 通过）**：dataset 加载/位置化 ValueError；metrics 词边界守卫（"RAG" 不命中 "RAGDOLL"、单中文字不命中、NFKC、宽松相等数值/子串、选择/参数贪心对齐）；judge rubric + DeepSeekJudge 脚本化 LLM（合法 JSON→aspects、不可解析→回退 rubric 不 raise、prompt 含 "json"）；offline ScriptedChatClient（complete 按序末条重复/参数两半累加/空脚本 ""/token 记账）、loop E2E、脚本长度 canary；runner loop 档聚合冒烟。
- **B 轨 `pytest.importorskip("langgraph")`（需本地装依赖后跑）**：graph-drift canary（run_research_graph 消费的 complete/stream 与脚本长度严格一致——graph 改动会显式打破 eval 信任）；完整五档矩阵——mcp_only 条目的可用性轴、memory 档只在该档 mem_found>0、kg 档在证据条目上 complete_calls_avg 比 graph 高 0.75、字节确定性（monkeypatch perf_counter 两跑全等）。
- 本地沙箱已验证：A 轨 63 通过 + CLI `--variants loop` 离线冒烟（输出见 docs/phase-7.md）；B 轨/完整矩阵需用户装依赖后 `-m pytest`。

### 已知问题

- graph/runner/B 轨需 langgraph（用户本地 `pip install -e ".[dev,rag]"` 后跑）；沙箱只能跑 A 轨 + loop-only。
- `--real` 需 `.env` 的 `DEEPSEEK_API_KEY`，联网费 key（每条多一次 judge complete）；SEARCH_PROVIDER=stub 可纯 LLM+judge。
- MCP 轴只在离线可完整演示：`--real` 不接线 MCP server（需独立进程），`all` 在 --real 下实际 = graph+memory+kg。
- 离线 token 是启发式（字符数/2，与 rag/eval 同口径）；延迟含真实测量开销（graph 档 warmup 已弃首跑）。
- 现有 bug 与累积项延续 Phase 6 的"下一步"：**工程化 Phase 8**（MCP 进程复用/常驻、Model Gateway、Docker），期间可补向量语义召回、记忆注入 research/synthesize、`query_knowledge_graph` 工具、GraphRAG 跨任务积累。


---

## Phase 8 — 工程化（MCP 进程复用 / Model Gateway）（2026-09-10）

### 项目方向

Phase 0–7 把功能做齐了，Phase 8 补的是**工程化**这一轴：让已经能跑的东西在真实使用下更省、更稳。规格 §15 的原始清单是「MCP 进程复用 / Redis / PostgreSQL / Model Gateway / Docker」。本轮经用户拍板**只做两个子项，Docker 顺延**：

- **去掉 Redis / PostgreSQL**：规格里没有任何一处功能真的需要它们——Memory 是单机 SQLite（个人研究助手，无并发写）、图 checkpoint 是 SqliteSaver、RAG 向量库是 Chroma 本地持久化。为了"看起来完整"而引入两个需要额外运维的中间件，正好违反 §16 的「不堆技术」。验收看的是**能跑通 + 说清为什么**，不是组件清单的长度。
- 两个留下来的子项，动机都是**已量化**的真实痛点，不是想象中的优化：
  - **MCP**：Phase 6 的 `event_stream` 用 `async with MCPGateway(...)`，**每个 SSE 请求 spawn memory+papers 两个 stdio 解释器**，握手 0.3–0.5s，而这两次握手完全不产出任何用户可见内容。
  - **LLM**：Phase 3 起就挂在「已知问题」里的「图内 LLM 调用无超时/无重试」。规格 §12 要的是一层落在 Agent 与 provider 之间的统一访问层。

### 关键决策

- **MCP 常驻：专用 serving task 模型，而不是把连接挂在请求 task 上。** 第一版按「网关对象持有连接、请求进来 connect、请求结束 aclose」实现，结果在 `test_chat_graph_mcp_mode_streams_tool_and_notes` 上炸出 `RuntimeError: Attempted to exit a cancel scope that isn't the current tasks's current cancel scope`。根因是 **anyio cancel scope 是任务绑定的**：`mcp` 的 `stdio_client` 内部用 `anyio.create_task_group()`，每个已连 server 都在**进入它的那个 task** 的 cancel scope 栈上占一层；在 SSE 请求 task 里进入、又在 Starlette 的 task group 退出流程里关闭，栈就烂了（`StreamingResponse` 用的是 `create_collapsing_task_group`）。改成：`MCPGateway` 内部起一个**长驻 `asyncio.Task`**（`_serve`），所有连接的 enter/exit 都只在这个 task 里发生；调用方经 `asyncio.Queue` 提交 `(op, payload, future)` 并 await Future。连接生命周期与请求 task 彻底解耦。
- **关闭必须严格 LIFO。** 解决了上面那条之后仍有 `CancelledError ... by <Task ... _serve>`：`_close_all` 按 dict 插入顺序（memory→papers）关，正好反了。改为 `_close_order()` 返回逆序；重连单个 spec 时也**连同其后所有会话一起逆序拆掉、再按原顺序重建**（`_reconnect_spec`），保证栈序不变。
- **收尾异常必须 catch `BaseException`。** 这是**挂死**的直接原因（`tests/test_api.py` 卡满 120s 超时）：`asyncio.CancelledError` 继承自 `BaseException` 而非 `Exception`，`except Exception` 拦不住 → 它逃出 `_serve` → serving task 被杀 → 调用方 await 的 future 永不 resolve。`_serve` 的 close 分支改 `try/finally` 保证 future 一定被置位，`_close_handle` 用 `except BaseException` 且一个失败不拖累其余会话。
- **Model Gateway 用「务实内核」，不做神话。** 落在 `LLMClient` Protocol 后面 → graph/loop/rag/rewrite/judge **零改动**；只把 `api/main.py::get_chat_deps` 的 `ChatClient(settings)` 换成 `build_llm_client(settings)` 一行。能力限于：多 provider（DeepSeek/Qwen/OpenAI 兼容）、仅瞬时错的退避重试、耗尽后 fallback、流式**只在首字节前**重试、usage 日志。不做限流/配额/成本核算那套——没有真实需求。
- **默认全关是硬约束（逐字节一致）。** 所有新配置默认值都等价于「不传参给 SDK」：`llm_retry_enabled=false`、`llm_fallback_enabled=false`、`llm_log_usage=false`、`llm_timeout=None`。最关键的暗雷是 **openai SDK 自带 `max_retries=2`**：网关层一旦自己开重试，构造 `AsyncOpenAI` 时必须传 `max_retries=0`，否则重试次数叠乘；默认路径则**绝不传该参数**，保留 SDK 原行为。B 轨用 byte-parity 测试锁死。
- **`__init__` 去掉 `reconnect_backoff` 参数与 `_call_lock`**：退避由 `llm/errors.py::backoff_seconds` 统一；串行化改由 serving task 天然保证（单 task 顺序取队列），显式锁已无意义（且 `asyncio.Lock` 绑定事件循环，跨 pytest 事件循环会炸）。
- **`time.monotonic()` → `time.time()`**：指纹/冷却时间戳要跨父进程与 stdio 子进程比较，monotonic 是**每进程**起点，不可比。

### 技术栈

无新增依赖。MCP 复用与 Model Gateway 都只用了 `asyncio` / `contextlib` / 既有 `openai` / `mcp`。新模块**顶层不 import 重依赖**（`llm/protocol.py`、`llm/errors.py`、`mcp/runtime.py` 纯 stdlib），重依赖一律函数内懒导入——这既是 Phase 3–7 的既有纪律，也让 A 轨测试能在无 openai/mcp 的沙箱里真跑。

### 实现内容

- **`llm/protocol.py`（新）**：`StreamChunk` / `LLMClient` 从 `llm/client.py` 原样搬出。`client.py` 顶部改为 re-export → 全仓 `from knowledge_pilot.llm.client import LLMClient` 无感。搬家的唯一目的是：`client.py` 顶层 import openai，网关若从它引这两个名字会把 openai 拉进 A 轨。
- **`llm/errors.py`（新）**：`ErrorKind` 枚举 + `classify_error`（优先读 `status_code` 属性 → httpx 异常类型 → 内建 OSError/EOFError → 类名含 `APITimeout`/`APIConnection` 兜底）/ `is_retryable_status` / `is_transient_error` / `is_connection_lost` / `backoff_seconds`。零 SDK import，纯函数可测；MCP 网关的断线判定也复用它。
- **`llm/providers.py`（新）**：`ProviderClient` 单 provider 单次调用边界 + `get_shared_async_openai()` 进程级客户端缓存。**防重试叠乘**在这里落地。
- **`llm/gateway.py`（新）**：`ModelGateway` 实现 `LLMClient`；`complete()` 决策链 = 直调 →（开了重试）仅瞬时错退避重试 →（开了 fallback 且有备用）切下一 provider；400/401/403/404 等**非瞬时错直接 raise**，不改变 planner/evaluate/kg/judge 既有的「parse 失败就回退」语义。`stream_chat()` **只重试开流阶段**，首字节之后出错绝不重试（防重复产出）。工厂 `build_llm_client(settings)`。
- **`mcp/gateway.py`（重写）**：`MCPGateway` 5 方法公开面（`names` / `has` / `tool_schemas` / `prompt_hint` / `call`）+ `connect` / `refresh` / `aclose`；内部换成 serving task + 队列（`_submit` / `_serve` / `_connect_specs` / `_refresh_specs` / `_call_tool` / `_close_all` / `_close_order` / `_reconnect_spec` / `_connect_one`）。删除 `reconnect_backoff` 与 `_call_lock`。
- **`mcp/runtime.py`（新）**：`MCPRuntime` 进程级单例——`ensure(specs)` 按指纹复用/重建常驻网关、`refresh()` 只对失败/断开的 spec 做冷却限流补连（默认 5s，绝不每请求狂重连）、`aclose()` / `reset()` 幂等关闭并把 loop 绑定的 `asyncio.Lock` 复位（跨 pytest 事件循环复用会报 bound to a different event loop）。
- **`mcp/servers/memory.py`**：删掉进程级 `_store` 单例，改每次 tool call **现开现关**（`open_read_store`）。长驻连接会缓存旧快照（父进程两次请求之间写了库，子进程读不到新数据），并在 Windows 上常驻 db 文件句柄（父进程/测试重建或删除文件会 PermissionError）。开销微秒级，远小于被消掉的 0.3–0.5s spawn。
- **`api/main.py`**：新增 `lifespan`，退出时 `await get_mcp_runtime().aclose()`；`event_stream()` 改「取常驻网关 → refresh → 传给 runner」单路径，恒 `None` 时与 Phase 5 一致；**不再在 finally 里关网关**。网关启动失败（`ensure` 抛错）退回无 MCP 路径，研究照常完成、不 500。
- **`agent/loop.py`**：`_dispatch_tool` 的 MCP 分支包 try/except，失败转为可读文本回填给模型——MCP 是可选补充资料，不该崩掉整次研究；失败结果**不进** `on_extra_tool_result`（notes 只收真实结果）。
- **`agent/graph.py`（顺带修的既有生产 bug）**：memory 开启时图用同步 `SqliteSaver`，而图由 `app.astream` 异步驱动 → `NotImplementedError: The SqliteSaver does not support async methods`，即 **memory 开启的 graph 研究整条链路不可用**。改用 `AsyncSqliteSaver`（懒导入，缺 `aiosqlite` 时回退 `MemorySaver`）。
- **`rag/fetcher.py`（顺带修的既有缺陷）**：`trafilatura.extract` 加 `favor_precision=True`。默认档在短页面上会把 `<nav>` / 侧栏当正文留下，而模块 docstring 承诺的正是「去导航」——导航文本混进 chunk 会直接污染检索与 BM25 打分。**这是本轮唯一改变既有生产行为的改动**（不涉及任何默认关闭的 opt-in 开关）。

### 测试

A/B 双轨（同 Phase 3–7 纪律）。**本轮全量 356 passed / 0 failed**（此前 353 collected 里 10 failed）。

- **A 轨（注入假对象，不 import openai/mcp，沙箱即跑）**：`test_llm_errors.py`（状态码分类、无 openai 的传输分类、退避递增）、`test_llm_gateway.py`（瞬时错重试至成功/穷尽 raise、**400 只调 1 次绝不重试**、瞬时错 fallback 而 401 不 fallback、流式仅首字节前重试、usage-only 块不 yield 且被记、默认不发 `include_usage`、`model` == primary）、`test_llm_config.py`（新字段默认 + env 覆盖 + JSON 数组解析）、`test_mcp_gateway_lifecycle.py` + `test_mcp_memory_reopen.py`（复用不重连、指纹变重建、禁用/无工具 = 禁用路径、失败跳过、断线自动重连、重连失败 raise、并发串行、loop MCP 失败回可读文本、现开现关读到新写行）。
- **本轮新补的 3 条网关不变量用例**（此前查不出来、正是本次挂起事故的根因）：`test_close_is_reverse_of_connect_order`（LIFO 关闭）、`test_reconnect_reestablishes_later_sessions_in_order`（补连时连同其后会话逆序拆/原序建）、`test_aclose_survives_base_exception_from_a_session_close`（会话收尾抛 `CancelledError` 不得逃逸、不得挂死，其余会话照常关完）。用 `CancelledError` 而非 `Exception` 子类，才复现得了原始故障。
- **B 轨（`pytest.importorskip("openai")`，真实 `AsyncOpenAI` + `httpx.MockTransport`）**：complete 解析 usage、流 include_usage 收尾块、429 重试成功而 400 只 1 次、双 provider fallback、timeout 透传、**byte-parity**（默认 `ModelGateway` 与 `ChatClient` 同 messages 下外发 JSON 与返回 content 逐字节相同）。
- **实机冒烟**（真实 stdio 子进程）：`connect ok` → `call ok` → `close ok in 0.11s`，`aclose` 幂等，无 anyio warning，`tasklist python.exe` 前后均为 0（无子进程泄漏）。

### 已知问题

- **顺带修掉了 10 条一直红着的既有测试**（经 `git stash` 两次基线实验确认在 HEAD 上同样失败，非本轮回归）。其中 **1 条是真实生产 bug**（`SqliteSaver` + `astream` → memory 开启的 graph 全链路不可用）、**1 条是生产行为缺陷**（trafilatura 默认档不去导航），其余 8 条按性质都是测试自身的 bug：SDK 漂移（mcp ≥1.30 的 `ToolManager.list_tools()` 变成同步、内部 `Tool` 字段从 `inputSchema` 改名 `parameters`）、rank_bm25 在 N=2 语料上 idf 恒为 0、`FakeChatClient` 脚本长度不够（跨轮工具调用被「越界重复最后一条」吃成纯文本）、`store.close()` 写在读取断言之前、以及用浮点 `n*0.001` 假时钟造成 `perf_counter` 差值带 1e-18 残差。
- **Docker 顺延**。容器化对当前形态（本地 uvicorn + 本地 BGE-M3 + 本地 Chroma/SQLite）收益有限，且会把「零 key 离线可跑」的演示路径复杂化。留作 Phase 9。
- **缺一份「结论产物」**：Agent Evaluation 的 `--real` 结果目前只落在 gitignore 的 `docs/`，仓库里没有一个可公开引用的评测结果文件（应放到不受 gitignore 的 `results/`）。
- **Model Gateway 的评测侧未接线**：`agent/eval/real.py` 与 `rag/eval/real.py` 仍直接用 `ChatClient`——这是**刻意的**：`CountingChatClient` 记的是「应用层调用次数 + 启发式 token」，网关内部的 HTTP 重试不应混入该口径。将来若要换，`CountingChatClient(build_llm_client(...))` 一行即可，文档已注明。
- 累积项延续 Phase 6/7：向量语义召回、记忆注入 research/synthesize、`query_knowledge_graph` 工具、GraphRAG 跨任务积累。

### 下一步

- **Docker**（容器化 + 一键起服务）——写作时排在 Phase 9，实际被新插进来的学习图谱顶到 **Phase 10**（见下）。
- 补 `results/` 下的真实评测结论产物。
- 累积增强项见上。

---

## Phase 9 — 学习图谱（研究报告 → 持久化学习路径 + 点亮机制）（2026-09-10）

### 项目方向

Phase 0–8 交付的是一个能用的研究 Agent，但**第一次真机当用户用**就暴露了产品形态问题——这一阶段因此不是加功能，而是**改产物形态**：

- **报告根本不显示**（graph 模式）。`synthesize_node` 用非流式 `complete()` 一次取全文、只发一个 `DoneEvent`，而前端 `case 'done'` 只清状态、**丢弃 `evt.content`**。请求 200 正常结束、无任何报错，用户看到的是「正在综合撰写报告…」永久挂着 + 空正文。**这是本次的起点。**
- **等待零反馈**。即使修好渲染，30–90 秒静默仍让用户分不清「在跑」与「死了」。
- **产物是一次性文档，不是可积累的学习资产**。用户真正要的是：按主题组织、知识点逐步点亮、进度持久化、**重启后直接读回、不重新检索**。

本阶段把定位从「研究报告生成器」改为**学习伙伴**：主题 → 研究 → 生成学习路径知识图谱 → 逐点讲解 → 系统推荐点亮 + 用户确认 → 全落 SQLite + Markdown。用户在动手前拍板了四项决策（点亮判定 = 系统推荐 + 用户确认；界面 = 替换单页聊天为图谱主导的新主页；持久化 = SQLite 存结构 + Markdown 存正文；轮数上限**数值不变**但要把调整入口做出来）。

### 关键决策

- **流式必须走「能力探测式降级」，不能改调用签名。** 这是本阶段最关键的取舍：
  ```python
  stream_complete = getattr(llm, "stream_complete", None)
  if stream_complete is None:          # Fake / CountingChatClient → 原路径
      return await llm.complete(prompt, max_tokens=max_tokens)
  ```
  `test_api` / `test_agent_graph` / `test_kg_graph` / `test_mcp_graph` / `test_agent_eval_*` 里约 **40 条断言**依赖「Fake 客户端用 `complete` 产出报告」（`complete_calls` 计数、`frames[-2]["content"]`、事件类型精确序列）。无条件改 `stream_chat` 会让 5 个文件全改，直接违反「既有测试不得需要修改」的铁律。而 `agent/eval/real.py::CountingChatClient` 只实现 `model`/`stream_chat`/`complete`——**没有 `stream_complete`** → 自动降级 → 真实评测的调用计数口径也不漂移。全仓原先**无人**实现 `stream_complete`，所以是纯增量。付出的代价是「生产流式、测试非流式」的分叉，用不变量 `DoneEvent.content == "".join(TokenEvent.content)` + 新增的流式假客户端覆盖真实路径来兑现，**债务写进 `tests/test_agent_streaming.py` 顶部**。
- **降级路径刻意不补发合成 token。** 发一个假 TokenEvent 会改变观察到的帧序列，`test_graph_report_falls_back_without_streaming` 就废了；前端由 `DoneEvent.content` 兜底渲染。
- **`event_stream` 补 `except Exception` → 发 `error` 帧。** 现状异常直接穿出生成器，客户端拿到 **200 + 截断 body**，前端一个错误都不显示——这正是「用户分不清模型失败与仍在处理」的根因。`CancelledError` 继承 `BaseException`，不会被吞掉，取消语义保持正确。错误文本经 `_safe_error_text` 脱敏 `sk-*` + 截断，不进日志/前端原文。
- **点亮判定的失败策略与 `evaluate` 刻意相反。** `evaluate` 解析失败默认「充分」（它只是推进流程，判错最多多跑一轮研究）；点亮判定**解析失败一律不推荐**——点亮是用户的学习资产（「已掌握」会被记进 DB 与 Markdown），误推荐污染可信记录，代价远大于少推荐一次。
- **图谱生成不复用 `kg_node`。** `extract_entities_relations` 抽的是无方向、无粒度、无顺序的共现关系，喂不出「前置关系 + 推荐学习顺序」；新写 `LEARNING_PATH_PROMPT`。`kg_node` 一行不改。
- **`build_learning_graph` 是确定性纯函数**：归一化 → 同名合并 → `max_nodes` 截断 → 建边（丢自环/未知前置）→ **Kahn 拓扑排序 + 破环** → 迭代算 depth（不用递归，防深链 `RecursionError`）→ `order_index` 用**拓扑序**，LLM 的 `order` 只在同层平局时 tie-break。**前置一致性优先于 LLM 的主观顺序**，否则会出现「箭头向下游指、序号却在前」的视觉矛盾。破环的副作用踩过一次真 bug：Kahn 破环是「强制放行」一个节点，它的未解前置会排到拓扑序后面，depth 若无条件读 `depth[p]` 会读到未计算/失效的值，深度整体飘一格（实测 `[1,2,3,4,5,6]` 而非 `[0..5]`）→ 只认 `position[p] < position[name]` 的前置。
- **永不阻断的降级链**：LLM 抽取 → `build_learning_graph`；解析失败/非 JSON → `nodes_from_headings(report)`（`##`/`###` 标题拼成线性路径，零 LLM、确定性、**永远成功**）；无标题 → 单节点。降级时发 `StatusEvent` 告知用户「这不是模型抽的」。意义不是「更好」而是**有**——模型质量波动不该让用户面对空图，更不该让生成失败。
- **API 读写分离**：只有 create / generate / chat / mastery 可能触发 LLM；**GET 一律零 LLM、不需要 API key**（GET 路由**不** `Depends(get_chat_deps)`）。「重启后不重新检索」落在代码里就是这句话。M4 实机验收时直接把 LLM 上游**杀掉**，所有 GET 仍 200 且数据完整。
- **不需要 `sessions` 表**：「新主题 = 全新对话」由 `nodes.topic_id` 天然隔离，会话身份就是 `topic_id`，避免两套 id 互相漂移。**`replace_graph` 按 `(topic_id, name)` 复用既有 node id / node_state / note_path** → 重生成不丢进度。连接**每请求现开现关**（延续 Phase 8 `open_read_store` 的 Windows 句柄结论）。**计划里说的模块级 `_SCHEMA_READY` DDL 缓存刻意没做**：`CREATE TABLE IF NOT EXISTS` 本就幂等，而缓存会引入一个真实故障——db 文件被删后在同路径重建时，缓存的「已建表」标记让新文件一张表都没有，之后所有查询以 `no such table` 炸掉，且症状取决于进程内是否开过别的库。DDL 走一遍是微秒级，换这个隐患不划算。
- **前端 SVG 用分层布局（Sugiyama-lite），不用力导向。** `depth` 在后端算好，前端只做像素映射（`layout()` 是纯函数，可在 node 里直测）。力导向的物理循环会抖动、点击目标乱跑、**无法断言**；分层布局确定性、可单测，且与「由浅入深」语义同构。点亮三态用 **fill + stroke（含虚线）+ 角标字形 + 尾注文字**四通道编码，不靠颜色单一通道（色盲/黑白打印/截图压缩后仍可辨）。
- **Markdown 渲染零依赖：先 `esc()` 再渲染。** 顺序反了就是 XSS（正文是 LLM 生成的，按不可信输入对待）。转义后字符串里已没有 `<`，所以之后插入的每个 `<` 都只可能来自渲染函数自己——这就是安全性所依赖的不变量。链接只放行 `http(s):`（`javascript:` 当普通文字）+ `rel="noopener noreferrer"`。
- **轮数只做「配置化」不改数值（用户明示）**：`loop.py` 的 `MAX_TOOL_ROUNDS = 4` **保留**（两个测试文件直接 import 它），新增 keyword-only `run_research(..., max_tool_rounds=None)`（None → 用常量，行为不变）；`ChatRequest` 扩为可选 `max_iterations` / `max_tool_rounds`（老请求体逐字节不变），`clamp_rounds` **只钳请求值**到 `[1, AGENT_ROUNDS_HARD_CAP]`，`.env` 的值从不被钳。
- **`learning_enabled` 默认值 = 全仓唯一的例外：默认 `True`（用户在使用中指出后改的）**。最初按「opt-in 默认关」的老规矩做成默认关，并被用户当场指出「为什么我的默认界面不能就是这个样子」——**这次是我把产品形态和后端开关搞混了**：用户早已拍板「界面形态 = 替换单页聊天为图谱主导的新主页」，那主页就是产品门面，**门面不该由后端引擎开关决定画不画**；默认关的结果是第一次打开的人看到一张空白提示卡，而那个页面本身就是本阶段要交付的东西。三点要点：① 「默认开」不等于「会花钱」——GET 端点零 LLM、不需 key；② 「默认开」不等于「不可关」——`LEARNING_ENABLED=false` 仍可退回纯研究形态，并有测试专锁这个方向；③ **代价是量过才改的**：翻转后全量 523 条只有 **1 条**失败，且正是那条钉住默认值的断言（它断言的**就是**被改的东西，所以跟着改正当，不是「为了改代码而改测试」），已重命名并把这个 false 方向的覆盖移到 `test_learning_env_overrides`。其余 RAG / Memory / KG / MCP 与 Phase 8 网关旋钮**仍全部默认关**（它们开不开是成本问题，不是门面问题）。

### 技术栈

**无新增依赖**。持久化是 stdlib `sqlite3` + `pathlib`（Markdown 用 `os.replace` 原子写）；前端仍是单文件 `index.html`、手写 SVG、零外链零 CDN；`learning/` 包内不 import FastAPI（编排逻辑可脱离 Web 层单测）。`stream_complete` 走既有 `openai` SDK 的流式接口，`providers.open_stream` 按「**仅 max_tokens 非 None 才塞进 kwargs**」构造，保证默认外发 JSON 逐字节不变。

### 实现内容

| 文件 | 变化 |
|------|------|
| `knowledge_pilot/llm/streaming.py`（新） | `stream_capable(llm)` 谓词 + `stream_text(llm, prompt, on_delta)` 回调式流式。单开谓词是因为 `learning/session.py` 是**异步生成器**，增量要当场 `yield`，没法通过回调转交——异步生成器只能在自己的函数体里 yield |
| `llm/protocol.py` / `client.py` / `gateway.py` / `providers.py` | 各加 `stream_complete()`；`LLMClient` 签名一字不改，新增可选 `StreamingLLMClient` Protocol |
| `agent/graph.py` | `synthesize_node` 的报告改用 `stream_text` 逐字发出；`research_node` 的 token 过滤不动；`max_tool_rounds` 经 `functools.partial` 透传（**不写进 `ResearchState`**，避免动被 checkpoint 的 TypedDict） |
| `agent/events.py` | 新增 `ErrorEvent` / `GraphReadyEvent` / `RecommendEvent` |
| `api/deps.py`（新） | `ChatDeps` / `get_chat_deps` / `acquire_mcp_gateway` / `close_rag` / `close_memory` 从 `main.py` 搬出；`main.py` re-export 保持既有 import 路径 |
| `api/sse.py`（新） | `sse_frame` / `safe_error_text` |
| `api/learning.py`（新） | `/api/learning/*` 全部 9 条路由 |
| `api/main.py` | 瘦身为「研究入口 + 应用装配」；`event_stream` 补错误帧 |
| `learning/store.py`（新） | `LearningStore`：5 张表（topics / nodes / edges / node_state / messages），状态白名单校验，`replace_graph` 按名字复用、`prerequisites_of` |
| `learning/notes.py`（新） | Markdown：slug、front-matter、stub、追加讲解（只追加不覆写）、**用户区程序永不触碰**、`.tmp` → `os.replace`、读缺失时用 DB 自愈 |
| `learning/path.py`（新） | `LEARNING_PATH_PROMPT` + `build_learning_graph`（内含 `_topological_order`；**分层与 depth 就在这一步算完**，没有再单独的 `assign_layers`）+ `nodes_from_headings` |
| `learning/session.py`（新） | `EXPLAIN_PROMPT` / `JUDGE_PROMPT` / `run_node_chat`（流式 + 双写 DB·Markdown）/ `judge_coverage` + 三道成本闸门 |
| `learning/service.py`（新） | 编排（不依赖 FastAPI）：`create_topic_shell` / `build_graph_from_report` / `persist_graph` / `load_node_payload` / `rehome_note` |
| `web/index.html` | **重写**为图谱主导的新主页（~1050 行，单文件零依赖） |

数据模型（`data/learning.db`）：`topics`(id, title, query, summary, slug, status∈{empty,generating,ready,failed}, error, report_path, run_id, …) / `nodes`(…, depth, order_index, note_path, **UNIQUE(topic_id,name)**) / `edges`(source_id, target_id, relation) / `node_state`(node_id PK, status∈{unlearned,recommended,mastered}, recommend_reason, confidence, chat_turns) / `messages`(id, node_id, role, content, sources, created_at)。`PRAGMA foreign_keys=ON` 必须**显式**执行（级联是 per-connection 的），但删除仍走显式事务删子表（FK 只作第二道防线）。

Markdown 布局 `data/knowledge/<topic-slug>-<id6>/`：`report.md`（研究总报告）+ `<序:02d>_<slug>.md`（每知识点，序号建文件时冻结）。每份含 front-matter（镜像 DB）+ `## 要点` + `## 讲解记录`（只追加）+ `## 我的笔记`（用户区，追加时靠它做锚点插在前面）。

状态机：`unlearned --(LLM covered && confidence≥阈值)--> recommended --(用户确认)--> mastered`，另有 dismiss 回退与手动标记；`set_node_status` 白名单校验非法转移直接 `ValueError`、**永不自动降级**，只有 `POST /mastery`（用户的显式动作）能置 `mastered`。

### 测试

全量 **523 passed**（Phase 8 基线 356）。新增 7 个测试文件共 161 条（path 38 / store 31 / api_learning 34 / session 27 / agent_rounds 16 / agent_streaming 9 / llm_stream_complete 6），既有文件补 6 条。

- **A 轨**：`test_learning_path.py`（空输入 / 线性链 / 菱形 / 拓扑序压过 LLM 序 / 平局 tie-break / 同名合并 / 截断 / 丢自环与未知前置 / 2 与 6 节点成环 / **500 节点深链不 RecursionError** / `nodes_from_headings` / 抽取防御清洗）、`test_learning_store.py`（5 表 CRUD / 状态白名单 / **混合路径分隔符归一**）、`test_learning_session.py`（流式 token→done 不变量 / 无 `stream_complete` 时零 TokenEvent / 双写 / **判定的每个失败分支都不推荐** / 三道闸门 / 阈值）、`test_api_learning.py`（9 路由 503 / 关时不建库 / 建壳零 LLM / generate happy path + 降级到标题 + 失败可重试 / **GET 端点零 LLM 调用** / **新 store 实例重读图与正文一致且 LLM 调用数不增** / 删除保留 md / mastery / 对话流）。
- **`test_agent_streaming.py`**：流式假客户端 → 每 delta 一个 TokenEvent 且拼接 == `done.content`；**原 `FakeChatClient` → 零 TokenEvent**（显式锁住降级路径，防后人误删）。**`test_llm_stream_complete.py`**：`ChatClient` 的增量输出、**除 `stream` 外的请求体外发字节与 `complete` 一致**、`max_tokens=None` 时不塞进 kwargs、**开流失败可重试而首字节之后绝不重试**。**`test_agent_rounds.py`**：`clamp_rounds` 边界、**默认数值与 Phase 8 相同**、`run_research` 默认与显式覆盖、graph 经 `partial` 透传、请求级覆盖钳到硬上限。
- **唯一改动的既有测试**：`test_api.py::test_index_served`（`/` 从单页聊天换成新主页，断言随之更新）+ 新增 `test_index_is_offline_self_contained`（页面不得出现 `<link>` / `src=` / 绝对 URL——零依赖离线可跑这条铁律很容易被顺手加个字体 CDN 破坏，而破坏后只在断网/内网环境才白屏，故用测试钉住）。
- **计划里的 `test_learning_e2e.py` 未单独建**：其意图（generate → 新 store 实例重读 → 图与正文一致且 LLM 调用数不增）已由 `test_api_learning.py::test_graph_survives_a_process_restart_without_any_llm_call` 实现，另建文件只是重复。
- **前端怎么测（无浏览器）**：没有测试框架（零依赖是硬约束），用 stdlib 的 Node 脚本 + DOM 桩把**真实文件里的真实函数**跑起来，三类 60+ 条检查——① 纯函数（XSS 转义顺序、`javascript:` 不放行、代码块内不做行内渲染、front-matter 剥离、折行、分层布局、三态回落）；② **真实 payload → 真实渲染函数**（把实机 GET 回的 JSON 灌进 `renderTopics`/`renderGraph`/`renderPanel`，断言节点数/边数/`data-id`/进度百分比/三态样式/按钮随状态变化——专抓「JS 读的字段名和后端给的不一样」）；③ SSE 消费（按 `[1]`/`[3,7]`/`[11,2,5]`/`[4096]` 字节切分，中文跨 chunk 不损坏、`[DONE]` 后不再派发、坏 JSON 帧跳过、503 detail 抛出、气泡被逐字写入且 `recommend` 重绘后仍写对气泡）。
- **契约核对**：33 条字段级断言，把 JS 真正读的每个 JSON 路径对照实机返回——包括最容易错的 `graph.topic.progress.percent`（进度在 `topic` 里，不在图的顶层）与 `node.prerequisites[].name`。
- **实机验收（M4/M5，stdlib 假上游，不花费用、不涉及密钥）**：建壳零 LLM → generate 2.1s 流 23 个 token 帧（间隔 0.06s）→ `graph_ready{nodes:3, edges:2, degraded:false}` → 图带 depth 0/1/2 与 order_index → `data/knowledge/` 下 4 个 Markdown；**杀掉上游后所有 GET 仍 200**；聊 1 轮 → 23 token 帧 + `recommend{reason, confidence:0.92}` → `mastery` → 刷新仍 `mastered` → 再聊一轮判定调用数 **1 → 1**（全程只花 1 次）。关掉时（显式 `LEARNING_ENABLED=false`）实测：`/` 200 且仍是新主页、`/api/learning/topics` 503 且 detail 含 `LEARNING_ENABLED`、`/api/chat` 仍路由——这条路径仍被测试覆盖，只是不再是默认态（见下条）。

### 已知问题

- **前端只做到「函数级 + 契约级」验证，没做真人点击走查。** 像素映射、事件委托、hash 路由、滚动居中只在 DOM 桩里验过形态，**视觉与手感仍需人在浏览器里过一遍**。这是本阶段最大的验证缺口。
- **每 token 一帧**：帧数从个位数涨到几千。实机未观察到卡顿；若要优化，在 `_report_text` 内做本地小批量（~64 字符 / 50ms），改动局限在一个函数且不破坏不变量。
- **重生成会留下孤儿 Markdown**：`replace_graph` 按名字复用，名字变了就是新节点新文件，旧文件留在磁盘上（**不会删**）。这是「不改名」取舍的另一面。
- **`data/` 被 gitignore 与「想把学习资料纳入版本管理」冲突**：`.env.example` 写明两种做法（改 `LEARNING_NOTES_DIR` 到仓库外，或单独反忽略 `data/knowledge/`），**没有擅自改 `.gitignore`**。
- **前端单文件已到 1000 行量级**：拆分触发条件（1200 行）写在文件头注释里。
- 累积项延续 Phase 6/7/8：向量语义召回、记忆注入 research/synthesize、`query_knowledge_graph` 工具、GraphRAG 跨任务积累、`results/` 下的真实评测产物。

### 下一步

- **Phase 11：Docker**（容器化 + 一键起服务）。
- 真人浏览器走查新主页（本阶段唯一的验证缺口）。
- 补 `results/` 下的真实评测结论产物；累积增强项见上。

---

## Phase 10 — 前端拆分与知识图谱三栏重设计（2026-09-10 ~ 2026-09-11，阶段 0–7 全部完成）

### 项目方向

Phase 9 交付的新主页是个 **1083 行的单文件前端**，而它自己的头注释就写着「超 1200 行时应拆分为
index.html + app.js + app.css」——已经贴着这条线。用户同时**重新出了设计稿**
（`KnowledgePilot_知识图谱核心网页设计说明.md` + `Design.png`），把产品定位为 **AI Native Knowledge
Graph Workspace**：三栏工作台（侧栏 240~260 / 中央图谱画布 60~70% / 右栏 Node Inspector 300~340）、
径向知识图谱、「学习 = 点亮知识图谱」为核心视觉隐喻。

> 用户先前那版 `KnowledgePilot_前端界面重新设计需求.md` 是 **chat-first** 的，与 Phase 9 已拍板的
> 「学习图谱即门面」冲突；**用户自己重新设计后该冲突已解除**，旧需求书作废。这条值得记，是因为
> 如果没有新设计稿，按旧需求书做就等于把用户明确要的门面又藏回去一遍。

用户 2026-09-10 另拍板两条范围：① **纯前端改动** —— 不改后端功能、不新增数据源，后端只做「为多文件
静态资源服务」的基础设施改动；② **侧栏只放有后端支撑的项** —— 知识库/文档管理/检索测试零端点，直接不放。

### 关键决策

- **拆成 ~25 个文件，但用普通 `<script defer src>` + `window.KP` 命名空间，不用 ES module。** `defer`
  无 CORS/协议限制，`file://` 也能跑，且 `index.html` 里不写任何内联 `<script>`/`<style>`。
- **「任何文件在加载期不得触碰 `document`/`location`/`history`/`fetch`」是铁律，且由测试机械强制。**
  现存单文件前端恰好违反它（顶层 `const app = $('app')`、顶层 `addEventListener`、末尾直接 `boot()`）。
  `tests/js/harness.mjs` 用一个**只装配期抛错、运行期放开的 Proxy 门禁**把这条从口号变成会当场炸的断言
  —— 全程抛错会让任何真实调用都失败，于是测试要么不测这些路径、要么被迫关掉门禁，两种结果都更差。
- **`document.getElementById` 只允许出现在 `js/views/shell.js` 与 `js/app.js`**，其余一律
  `KP.dom.q(root, id)`（作用域查询）。同名 id 出现两次时 `getElementById` 返回**文档序第一个** ——
  「助手里的发送按钮控制了 Inspector 的输入框」这类 bug 只能靠源码扫描挡，有测试钉住。
- **`/static` 只挂 `WEB_DIR`，绝不挂项目根** —— 挂根等于把 `.env` 与 `data/learning.db` 暴露成可下载文件。
  实测三种穿越探针（`/static/../.env`、`%2e%2e%2f`、`../data/learning.db`）全部 404。
- **`package-data` 写显式多模式，不写 `web/**/*`。** setuptools 的 glob 在部分实现下要求 `**/` 至少匹配
  一层，会**恰好漏掉 `web/index.html`** —— 而开发时没人会发现，因为大家跑的是源码树里的 uvicorn，
  只有 `pip install` 出来的 wheel 才会少一整个前端。已实际打 wheel 用 zipfile 确认 **20 个资源全部入包**。
- **两处**有意的**行为变化**（不是 bug，写在这里以免日后被当成 bug）：
  - `S.busy` 单标志**拆成** `busyGenerate` + `busyChat`。原设计下「生成中不能聊天」，而生成与某节点对话
    是互不相干的两件事，合成一个标志让这条限制凭空出现。
  - hash 格式由 `#/t/<id>/n/<id>` 改为 `#/g/<topicId>/n/<nodeId>`。**旧格式的深链接会落到图谱首页**
    （未知 hash 一律落回首页而不是白屏）。无迁移逻辑 —— 该格式此前只在同一次会话内产生。
- **编码/解码必须成对。** `formatHash` 用 `encodeURIComponent`，初版 `parseHash` 却没有对应的
  `decodeURIComponent` → id 里带 `/` 的主题会被读成字面量 `a%2Fb`，表现是「点进去正常、刷新后提示主题
  不存在」。已补上，且解码**必须容错**（`decodeURIComponent('%')` 抛 `URIError`，在路由入口上抛就是整页白屏）。

### 实现内容

| 文件 | 变化 |
|------|------|
| `web/index.html` | **重写**为骨架 + 挂载点 + 6 个 `<link>` + 13 个 `<script defer src>`，零内联 |
| `web/css/{tokens,base,layout,sidebar,graph,inspector}.css`（新） | 零 `@import`、零绝对 URL；`layout.css` 的 `@media` 块放**文件末尾**以保住同特异性覆盖 |
| `web/js/{util,dom,icons,sse,markdown,store,chat,api}.js`（新） | 纯函数层：转义/作用域查询/字形/SSE 半帧缓冲/Markdown/状态与选择器/帧 reducer/唯一知道 URL 的地方 |
| `web/js/graph/layout.js`（新） | Sugiyama-lite 逐字移植 + CJK 宽度与折行 |
| `web/js/views/{shell,graph,inspector}.js`（新） | 视图层 |
| `web/js/app.js`（新） | **唯一**有顶层副作用的文件（`boot()`），必须最后加载 |
| `api/main.py` | `app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")` |
| `pyproject.toml` | `[tool.setuptools.package-data]`：`web/*.html` / `web/css/*.css` / `web/js/*.js` / `web/js/*/*.js` |
| `.gitignore` | 补 `dist/`、`build/`（打包产物） |
| `tests/js/{harness.mjs, fixtures/graphs.mjs}`（新） | Node `vm` 沙箱按 `index.html` 顺序加载前端；夹具字段形状**逐条对齐后端真实契约** |
| `tests/js/{sse,markdown,layout,store,assembly}.test.mjs`（新） | 66 条断言 |
| `tests/test_web_js.py`、`tests/test_packaging.py`（新） | `node --test` 的 Python 入口（无 node 则 skip）；package-data 覆盖度静态检查 |
| `tests/test_api.py` | 改写两条（断言的是被拆分的东西）+ 新增 7 条资源可达/同源/离线/内容类型/挂载不暴露 |

### 测试

- **Python：540 passed**（基线 523 + 17 条新增），`0 failed`。
- **Node：66 passed / 0 failed**（`node --test`，零 npm 依赖，本机 v18.19.0）。
- **轮子实证**：`python -m build --wheel` → zipfile 列出 **20 个 web 资源全部入包**。
- **真机 HTTP**：起 uvicorn，`/` 200 且 19 个同源 `/static/` 引用**全部 200 非空**；css → `text/css`、
  js → `application/javascript`；未知 `/api/nope` 仍是 **JSON 404**（没被静态挂载吞成纯文本）；
  目录穿越探针三个全 404。

### 阶段 2 — 纯函数加固 + 修既有 bug（完成）

**渲染拆成三层纯函数。** `views/graph.js` 从「一个 `renderGraph` 既拼字符串又绑事件」
拆成:

| 函数 | 产出 | 用途 |
|---|---|---|
| `svg(g, opts)` | `<svg>…</svg>` | 节点与边的视觉契约 —— 盒子数、选中态、三态配色、箭头、转义 |
| `header(g, opts)` | 顶栏 + 图例 | 进度数字、状态 chip |
| `view(g, opts)` | header + 滚动容器 + svg | 也就是 `renderGraph` 赋给 `stage.innerHTML` 的东西 |

`renderGraph` 缩成「`innerHTML = view(...)` + 绑事件」。布局经 `layoutOf(g)` **算一次**
给 `svg()` 与 `centerOn()` 共用；`layoutOf` 也是 Stage 3/5 换径向布局的唯一入口。
另把图例末尾那句的行内 `style="color:var(--ink-3)"` 挪进 `graph.css` 的 `.lghint`，
好让 `header()` 保持「零行内样式、可整段断言」。

**修掉的 bug（各带测试）:**
1. **`hot` 边丢箭头** —— 原实现写成「`hot` 的边**不加** `marker-end`」，于是「选中一个
   节点之后，与它相连的边反而没了箭头」，语义整个反了。加粗只是描边，与有没有箭头正交。
   现在**每条边都带** `marker-end`，由 `views.test.mjs` 的断言钉住（并反向断言「确实有边
   被标了 `hot`」，免得断言因为「一条 hot 边都没有」而恒真）。
2. **`loadTopic` 把所有错误吞成「主题不存在」** —— 抽出纯函数 `KP.loadFailureKind(err)`：
   `404 → 'missing'`（清选择**并清 hash**，否则刷新仍是死链）；其余（网络抖动 / 500）
   → `'error'`（保留选择与 hash，渲染带「重试」按钮的错误页）。拿不到状态码时按
   `'error'` 处理 —— 宁可多给一次重试，也不要误判成「已删除」把用户的深链接扔了。
3. **`sse.js` 的 `\r\n` 帧** —— 已在拆分的 `sse.js` 里处理，`sse.test.mjs` 有专项用例。

**拆分自身引入的一个 bug（被新加的源码扫描抓到并修掉）:** `views/graph.js` 生成成功后
与 `views/inspector.js` 点「稍后再说」后都调用 `KP.views.shell.loadTopic()`，而
`group.shell` **没有导出 `loadTopic`**。它在加载期不报错，只在「生成成功之后」那条路径上
抛 `is not a function`。抓它的那条检查（`KP.xxx` 引用必须在加载出来的命名空间上存在）
**最初只查两层**，`KP.views.shell.loadTopic` 是三层 —— 正好漏网。已加深到**任意深度**。

**测试:** Node **110 passed / 0 failed**（新增 `views.test.mjs` 20 条、`chat.test.mjs` 22 条、
store 补 2 条）；Python **540 passed**。

**测试桩的两处能力补强（都是「桩不像真的，于是测试在一个与被测逻辑无关的地方失败」）:**
- `harness.mjs` 补 `window.addEventListener` 桩 —— `shell.init()` 会注册 `hashchange`，
  缺了它任何走到 `init()` 的测试都以一个无关的 `TypeError` 失败。
- `harness.mjs` 的 `parseRough` 从**拍平**改成**建树**。拍平之后 `lastElementChild` 返回的是
  文档里最后一个标签而不是最后一个**直接子**元素、`closest()` 的 parentNode 链也断了，
  于是 `chat.tailBubble` 恒返回 `null` —— 那等于「流式正文一个字都写不出来」。现在
  `querySelector('#id')` / `.bubble` / `closest('[data-id]')` 都在真实的嵌套关系上工作。
- 新增 `makeShellElements()` + `loadKP({elements})`：把 `#stage`/`#panel` 等挂载点注册进去后，
  视图能真的走渲染路径（默认不注册是有意的 —— 让「忘挂 `#stage`」表现为静默无输出，
  而不是一个碰巧被测试抓到的 TypeError）。

### 阶段 3 — 径向布局作为第二布局（完成）

**两种布局,一个契约。** `graph/layout.js` 现在同时提供：

| 布局 | 形状 | 何时用 |
|---|---|---|
| `layered` | 同 `depth` 一列,列内按 `order_index` 排 | 链式 / 层数多的图 |
| `radial` | 环层 = `depth`,环内按 `order_index` **均分角度** | 星形 / 宽环 |

两者返回**同一个形状**,渲染器因此完全不关心跑的是哪个：

```js
{ pos: Map<id,{x,y,w,h}>, paths: [{id, source, target, from, to, back, d}],
  width, height, depths, marks: [{x, y, level}], mode }
```

（计划里把这个契约叫 `edges` / `levels`；沿用既有的 `paths` / `depths` 名字以免动渲染器，
`marks` 是新增的。**布局只给几何,文字由视图层拼** —— `marks` 是层标注的锚点,「第 N 层」
这几个字在 `views/graph.js` 里拼。)

- **`KP.layout.choose(nodes)`** 按**层的密度**选:`levels > 6 || levels / nodes.length > 0.55`
  → `layered`,否则 `radial`。
  **纯径向在降级链上会退化成笑话**:LLM 抽取失败时 `nodes_from_headings` 产出的是纯线性章节链
  （每层 1 个节点）,12 个节点 → 12 个同心环、每环站 1 个、盒子叠成一条竖线、画布高两千多像素 ——
  而那条路径存在的意义正是「至少让用户看到点东西」。判据只影响「哪个更好看」,不影响正确性:
  **不重叠与包含性对两种布局都成立,都有单测**。
- **环半径只由层数与环内节点数决定,与节点名长度无关。** 计划里写的是「把参与算半径的宽度 clamp 到
  ~8 个 CJK 字符」,实际做到了更强的性质 —— 半径公式里根本没有名字,`wrapText` 仍按真实宽度折行
  （只影响盒内文字）。200 字长名与 2 字短名的布局**逐字段相同**,两种布局都有断言。
- **不重叠靠一个可证明的充分条件,不是「看起来差不多」**:两轴对齐矩形不重叠 ⟺ `|dx| ≥ w` 或
  `|dy| ≥ h`;由 `|dx|²+|dy|² = 距离²` 可知「中心距 ≥ 盒子对角线」蕴含两者不可能同时成立,于是必然
  不重叠。所以 `r(n) = DIAG / (2·sin(π/n))`（圆周长够放下 n 个盒子）且 `r(d) ≥ r(d-1) + DIAG`
  （相邻环之间也留够）。`wideRing(13)` 是不重叠的最坏输入,有专项断言。
- **`anchor()` 是两种布局共用的纯函数**:从盒子中心朝目标画射线,取与**矩形边界的交点**。拆分前
  这里写死成「源取右边缘中点、目标取左边缘中点」—— 在分层里看着还行,到径向就完全错（连线该指向
  各个方向）。判据用「端点到中心的归一化**切比雪夫**距离恰好为 1」断言,两种布局都过。
- **反向边单独标记 `back`**（破环强制放行产生,`path.py:219-222`）。分层走竖直偏移的二次曲线、
  径向走切向偏移;按正向画会横穿整张图。`path.edge.back` 再压一道虚线 + 降透明度做视觉弱化。
  `hot` 与 `back` 是**正交**的两个标记,同时出现时都在。
- **径向不画环标注**（`marks` 为空）。环标注要么压住环上的节点、要么挤在环缝里,而角度随节点数变化,
  没有稳的落点。层信息改由图例那句承担,且**方向跟着布局变**:横向是「从左往右由浅入深」,径向是
  「由内向外由浅入深」—— 写反了会让第一次看这张图的用户找错起点。
- **加了一个布局切换按钮**（顶栏 `data-mode`,再点一次回到「自动」）。`choose()` 是启发式,总有一张图
  用户想换个看法;而本阶段的验收标准正是「**人工对比两种模式**」—— 没有这个开关,对比就只能改代码。
  高亮的判据是 `S.view.mode`（**用户选过什么**）而不是实际跑的是哪个布局:自动选了径向却点亮按钮,
  会让用户以为是自己选的。`S.view.mode` 为 null 表示「自动」,**不进 hash**（布局偏好不是位置）。

**顺带修掉的一个既有 bug(在测试桩里,不在产品代码里):** `FakeElement` 的 `innerHTML` setter 没有
清空 `children`,而 `parseRough` 是**追加**语义 —— 同一个容器渲染两遍会同时留着两代节点。表现是布局
切换用例点一次按钮就**无限循环**:`[data-mode]` 的命中数 4 → 6 → 8 一路涨,点击处理器在自增的列表上
反复触发。**这类桩 bug 的可怕之处不是「测试红了」,而是「测试绿着、断言的却是上一代 DOM」** ——
`querySelector('#id')` 返回的是已被替换掉的旧元素,事件绑在脱离文档的节点上,整套断言悄悄失去意义。
已修,并给桩本身补了回归用例。

**测试:** Node **130 passed / 0 failed**（layout 26 条 —— 新增径向/契约/锚点/`choose()` 阈值共 15 条;
views 补 7 条含布局切换的状态机用例;store 补 1 条钉 `S.view.mode` 默认 null）。Python **540 passed**。

### 阶段 4 — 三栏外壳 + 设计 token（完成）

**三栏落地，右栏从「选中才长出来」改成常驻一列。** `layout.css` 的
`grid-template-columns: var(--side-w) minmax(0,1fr) var(--insp-w)`（248 / 弹性 / `clamp(300px,23vw,340px)`）。

上一版是两栏 + 一个 `hidden` 的 `#panel`，靠 `.with-panel` 切列数。它有两个毛病：① 没选中时画布占满、
选中后被挤窄一次，**每点一个节点画面就重新流式布局一遍**；② 「没有选中」和「面板不可用」在视觉上分不清。
改成常驻列后 `inspector.close()` 渲染一个占位提示而不是把列藏起来 —— 否则「没有选中节点」在页面上
是一条空白窄条，读起来像加载失败。1000px 以下仍回落成浮层（`position: fixed` 自动脱离网格）。

**设计 token 集中在 `tokens.css`，但它们必须被抄第二遍 —— 这一点值得记住。**
节点三态的色值是写进 SVG **属性**（`fill=` / `stroke=`）的，**属性不认 CSS 变量**（只有 `style=` 才认）。
所以 `js/store.js` 的 `STATE` 表里必然有第二份字面量。上一版就有 `#1a7f37` 同时躺在状态表和图例色块里，
后来只改了其中一处。对策不是消灭第二份（消灭不掉），而是**让它漏改时当场变红**：
`store.test.mjs` 那张「逐字一致」的全表断言就是干这个的 —— 本轮换调色时它确实红了一次。
（同理 `views/graph.js` 里箭头 `marker` 的 `#cbd5e1` 与节点文字 `#1f2937` 也是第二份，注释指向 `--edge` / `--ink`。）

**导航只放这一屏真的能打开的视图。** `NAV_ITEMS` 现在只有「知识图谱」一项，`对话`/`设置` 随各自的视图
在阶段 6/7 一起进来。设计稿 §6 列了六项，其中知识库/文档管理/检索测试已与用户拍板省略，而**加一个点下去
没有反应的入口比少一个入口更糟** —— 它不报错，`route()` 只是安静地落回空态，用户只会以为自己点错了。
导航项写成 `<a href="#/…">` 而不是 button：点它只改 hash，由 `hashchange` 驱动路由，中键/新标签打开
与地址栏回退都白拿，也不用在 shell 里再绑一次点击。有一条测试专门断言**每个导航项的 view 都是
`parseHash` 认得的** —— 挡住的正是一个 `#/settings`（parser 认得，但视图还不存在）悄悄溜进来。

**进度卡读的是「当前主题」的进度，与顶栏那个 42% 同一个数**（同为后端 `percent`，前端绝不重算）。
没选中主题时整张卡不渲染 —— 显示一张 0% 的空卡会被读成「这个主题一个知识点都没学」；同理
`total === 0` 时不画进度条、也不说「已学习 0 / 0」，改说主题状态（「待生成」/「生成中」）。

**侧栏三段（导航 / 我的探索 / 进度卡）走同一个 `renderSidebar()`。** 分开渲染就会出现
「主题列表空了但进度卡还挂着上一个主题的 42%」这种半更新，而它看起来完全正常。

小的几处：主题条目按设计稿 §7 收成「标题 + 日期 + 已掌握 n/m」，日期只截 `YYYY-MM-DD`（`KP.fmtDate`
**不做时区换算** —— `new Date()` 会把 `2026-09-10T00:30:00+08:00` 在 UTC 机器上显示成 09-09）；
画布铺了一层极淡点阵（`radial-gradient` + `background-size`，随 `#graph-scroll` 一起滚动，
于是平移时「地」是动的、节点是静的）；新增 `prefers-reduced-motion` 把 `--dur-*` 归零，
新加的动画自动继承这条纪律，而不是逐个动画去关。

**顺带削掉一处重复的真源：** `STATE` 的字形改成取自 `KP.icons.STATE_GLYPH`，不再在 store.js 里抄一遍
—— `icons.js` 存在的全部理由就是「同一个字形别散落在五个地方」。`views.test.mjs` 那条断言也从
「产物里有 `>✓</text>`」改成「**每个状态的字形都落进了产物**」：前者测的是「字形是 ✓」，
换一次字形就得跟着改一次，等于没测到「字形通道存在」这个真正要守的契约。

**测试:** Node **142 passed / 0 failed**（新增 12 条：导航高亮/编码/路由可认、进度卡的三种退化、
503 三段齐清、进度卡跟随选中、空主题不画条、日期不跨时区、右栏常驻；store 补 1 条 `currentTopic`）。
Python **540 passed**（无改动 —— 没有新文件，资源解析测试本就递归覆盖全部 css）。

### 阶段 5 — 画布交互 + 筛选 + 节点四态视觉（完成）

**缩放模型：改 SVG 的 `width`/`height` 属性，不改 CSS `transform`，也不改 `viewBox`。**
`viewBox` 不动正是整张图变大的原因（坐标系没变，是坐标系里每个单位变大了），于是
`centerOn()` 的 `scrollLeft/scrollTop` 一行不用改，点击命中测试交给浏览器（自己做逆变换等于
再养一份必须与渲染层保持一致的几何，它错了只表现为「点这里选中了旁边那个」）。
两个数字函数被单独拎出来（`clampScale` / `scaleAfter` / `fitScale` / `dragExceeds` /
`scrollAfterZoom`）放在 `js/graph/canvas.js` —— 缩放数学的错误**几乎不报错**，只表现为手感不对，
所以它必须是能在 Node 里断言的纯数字。

**`clampScale` 对「没设过」和「想缩到最小」给了不同答案。** `null` / `''` / 缺字段都会
`Number()` 成 `0`，退化成 1（100%）而不是钳到 `MIN_SCALE`。钳到 0.4 的话，一次坏状态会让图缩成
一小团，而 **40% 看起来像是有人特意缩的**，没人会想到是缺了个字段。这条是写测试时才发现的真 bug
（断言 `clampScale(null) === 1` 先红后绿）。

**「适应画布」的上限是 1 —— 它不放大。** 它的意思是「让我看见全部」，不是「把图放大到填满」；
一张 3 节点的小图被放大到 2.4 倍会很吓人，而且用户想回去得先按 100% 再自己找位置。
`fit` 在 `S.view` 里是一个**标志**而不是算好的数：窗口拉窄了「适应」的结果也得跟着变小。

**滚轮默认平移，Ctrl/⌘+滚轮才缩放。** 三栏应用里劫持普通滚轮会让人烦躁 —— 用户想「往下看看」
的时候图突然放大，而放大会改变他正在看的位置。显式的缩放意图由右下角 ＋/－/100%/适应 四个按钮覆盖。
`scrollAfterZoom` 是**唯一一份实现**，滚轮（以光标为中心）与按钮（以视口中心为中心）走同一个式子，
区别只在传进来的 `c`。分成两份的话按钮那条会被写成 `scrollLeft * k` —— 在小比例下看起来没错，
缩到 2.5 倍时就明显偏了。

**拖动阈值 4px，且 `moved` 不在 `pointerup` 时清空。** 它在等随后的 `click` 事件（在 `pointerup`
之后派发）来读；下一次 `pointerdown` 才归零。另外拖动用的是 `setPointerCapture` +
`pointercancel` + 一个 **window 级 `blur` 兜底** —— 拖动中 Alt+Tab 时 `pointerup` 不会派发到
我们这里，`drag` 会挂着不清，回来后图跟着鼠标自己漂。那个 window 监听器**整个会话只注册一次**：
`renderGraph()` 每次重建 DOM 都会重新 `wire()`，每次挂一遍的话一次会话下来 window 上会积起成百上千个
同样的监听器，而它们做的事幂等（只是清 `drag`），所以不会表现出 bug —— 正因如此才更该现在挡住。

**筛选与图例合成一行（`.filters`），图上筛选不动一个节点。** 状态芯片上带色块：色块解释编码、芯片
本身可点着筛，因为两者说的本来就是同一件事（那四种状态），两行并排纯属占高度。而
**布局永远在全量节点上算**，筛选只决定谁被画出来（`svg()` 收一个可见 id 的 `Set`）—— 点一次芯片就
重排一次的话，用户脑子里那张图就白建了。同理重绘前存、重绘后恢复 `scrollLeft/Top`：筛选是
「看着某一片、想把它摘出来看」的操作，跳回左上角等于把用户正在做的事毁掉。
芯片**不带计数**：计数是在全量节点上算的，而画布显示的是「状态 × 类型」的交集，两个数摆在一起会互相矛盾。
`typeFacets` 也**故意不截断**：截断会让某些节点静默地筛不出来。
旧那条纯展示的 `.legend` 因此删掉了，`views.test.mjs` 里两条 `class="legend"` 断言随之改成
`class="filters"`（注释里写明了「断言变了但没被削弱」）。

**第四态「学习中」用真实的 `chat_turns > 0` 判据，不编造分数。** 设计稿写的是「学习中 8/15」，
但后端没有子任务模型 —— 只有 `status` 与 `chat_turns`。造一个分母为 15 的假分数会让进度语义从第一天
就是假的。所以四态是 `unlearned` / `learning`（`chat_turns > 0`）/ `recommended` / `mastered`，
尾注写「已对话 N 轮」。四通道编码（底色 + 边框含虚线 + 角标字形 + 尾注文字）一条不少 ——
它是「色盲与黑白打印可辨」的保证，`dash` 与 `glyph` 两个集合互异的断言继续钉着。

**点亮走局部 patch（`patchNodeEl`），不重绘。** 这是阶段 5 存在的理由之一：全量重建会让
「点亮」动画的相位每次从头开始，看起来像卡顿，`centerOn` 还会把视图拽回去跟用户刚做的平移打架。
`patchNodeEl` 在节点不在当前画布上时**返回 `false`**，由 `inspector.setMastery` 回落到全量
`renderGraph()` —— 而不是安静地什么都不做，那样用户只会觉得「我点了但没反应」。
对话一轮结束时 `refreshNode(nodeId)` 重新拉一次那个节点（后端在 `session.py:75`
是**先 bump `chat_turns` 再调 LLM** 的，所以一次 GET 比本地 `+1` 诚实：网络失败不会凭空多出一轮）。

**一处只有真跑 DOM 才会暴露的布局 bug：`#graph-fit` 必须 `width: max-content`。**
只写 `min-width: 100%` 时，比容器宽的 SVG 会作为一个**溢出**的 flex item 被 `justify-content: center`
从**两边**顶出去，而 LTR 下前缘（左边）的溢出不可滚动（`scrollLeft` 最小是 0）—— 表现是
「图左边一截怎么拖都看不见，右边滚得很好」，读起来像布局没对齐而不像滚动区域算错。
阶段 5 让图可以被放大之后，这条从「12 节点链条才有」变成了常见状态。

**测试:** Node **177 passed / 0 failed**（142 → 177，新增 35 条：`canvas.test.mjs` 16 条
`clampScale`/`scaleAfter`/`fitScale`/`dragExceeds`/`scrollAfterZoom`/`applyScale`/`wire`
（含幂等、`pointercancel` 收尾、微移不滚动、普通滚轮不动、到顶不空提交）；`store.test.mjs` 加
`nodeState` 全表 / `nodeFooter` / `typeFacet` / `typeFacets` / `filterNodes`；`views.test.mjs` 加
四态字形与尾注、`st-*` class、`visible` 筛选（**位置仍取自全量布局**）、筛选芯片计数与
`aria-pressed`、单 facet 时不渲染类型行、方向提示随布局翻转、工具栏与画布的兄弟关系、
`patchNodeEl` 的两条路径、点筛选保留滚动位置）。STATE 全表断言本轮又红了一次（换调色），
这是它第二次抓到漏改 —— 它就是为了这个才写成逐字一致的。
Python **540 passed**（无改动；本轮没有新文件，`test_index_declares_expected_modules` 与
`assembly.test.mjs` 的 `EXPECTED_SCRIPTS` 各加了一行 `js/graph/canvas.js`）。

### 阶段 6 — 浮动助手 + Inspector + 对话页（完成）

**对话 UI 收敛到一处：左下角的浮动助手。** 设计稿让右栏 Inspector 与左下角也各放一套对话，那会
造出两类**静默** bug：① 同一个 `id` 出现两次时 `document.getElementById` 返回**文档序第一个** ——
「助手里的发送按钮控制了 Inspector 的输入框」；② 尾部气泡在用户「边聊边点图」时指到别的节点上，
而浮动助手会让这条从边缘情况变成主路径。所以右栏一个输入框都没有，它只有「开始学习」这个入口
（点下去展开助手，并把焦点交过去 —— 那是**用**唯一那个入口，不是再开一处）。
这条规则由源码扫描机械钉住：`views/inspector.js` 里不许出现 `<input` / `<form` / `<textarea` /
`composer` / `chat-input` / `KP.chat`，同时反向断言助手确实有输入框、确实在用共用 reducer；
再加「`document.getElementById` 只出现在 `views/shell.js` 与 `app.js`」与「源码里
`getElementById` 取的每个 id 都在 index.html 里存在」（挂载点改名是静默失效：`el.assistant`
变 null → `render()` 走「没有容器就返回」→ 页面照常、控制台干净，只是助手永远不出现）。

**帧 → 日志行抽成一张表（`KP.chat.line`）。** 它原来有**两份**：`views/graph.js` 的生成日志一份、
新的对话页过程日志一份。两份实现一定会分叉（一边补了「已降级为线性路径」的说明、另一边没有），
而分叉的表现只是「同一件事在这个页面说得多、在那个页面说得少」—— 不报错、没有测试会红。
`line` 刻意**不**做成 `applyFrame` 的一个返回值：前者管**过程日志**（正在检索… / 第 2 轮评估…），
后者管**消息内容**，一帧可能两者都产（`error` 既写进气泡、也留一行日志），合成一个返回值会让
「一个函数同时决定消息与日志」变成隐式契约。`token` / `done` / `recommend` **必须不在表里** ——
`token` 漏进去的话一次回答会往日志里塞几百行，把真正有用的那几行冲得看不见（这条有专门用例）。
所有数字字段 `Number(v) || 0`：「已生成 undefined 个知识点」是那种会被截图发出来的 bug。

**「相关节点」只从 `edges` 推，绝不读节点字段。** 整图接口返回的 node **没有** `prerequisites`
（那是单节点接口才有的冗余），拿它当唯一来源的话右栏会依赖一个图上不存在的字段 —— 于是
「相关节点」永远是空的，而页面看起来只是「这个点没有邻居」。出边排在入边前面（「下一步能学什么」
比「它从哪来」更常被用），同向按 `(order_index, name)` 稳定排序（不稳的话每次点节点列表顺序都变，
而用户正想按着这个列表找下一个）。自环排除（点进去只是原地不动），指向不存在节点的边丢掉
（不留 `{node: undefined}`，否则渲染时会炸在 `r.node.name` 上）。

**右栏不画设计稿那条 `8 / 15` 进度条。** 后端没有子任务模型，那个分母无处可来 —— 编一个会让
整页最显眼的数字从第一天起就是假的。改成如实写两件真事：四态标签 + 真实的 `chat_turns`。
「已对话 0 轮」读起来像一句系统统计，所以第一次对话之前写「还没有开始对话」；而
`chat_turns === 0 且 status === 'mastered'` 时说「已标记为掌握」（这时说「还没有开始对话」是错的）。

**「来源」只显示文件名。** 设计稿那张节点 ↔ PDF 映射没有数据源（`messages[].sources` 生产恒为
`[]`），所以给的是真实存在的两份产物：`topic.report_path`（整篇研究报告）与 `node.note_path`
（本节点的 Markdown 正文）。**只取 `KP.baseName`，绝不显示绝对路径** —— 绝对路径会暴露服务器上的
部署布局，而它对用户没有任何用。断言里直接放了带盘符的真实形状 path，检查产物里连目录名都不剩。

**助手的两个时机问题各自有解。** ① **什么时候拉历史**：`setNode` 在**收起状态下不拉**（用户可能
只是在图上逛，那是一发白发的 GET），只把 `loadedFor` 清空；`openFor` 真正展开时才拉。`loadedFor`
让一次对话只拉一次 —— 少了它，`render()` 在一次对话里会被调很多次，每次都是一发 GET。② **重绘后
尾部气泡重读**：`recommend` 帧会触发重绘，重绘后旧气泡已脱离文档，提前把引用存进闭包会让后续
token 写进空气里（文字**凭空消失**、零报错）。`renderChat()` 每帧重新指派 `session.tail`，这条
用「推完 recommend 再推 token，断言新查出来的气泡里有全部内容」钉住。

**中途切节点：两条守卫一起才成立。** 帧处理器里 `if (!session || session.nodeId !== nodeId) return;`
丢弃属于上一轮的帧（否则 token 落到新节点的气泡上，而服务端已经把它存进**旧**节点 —— 本地与服务端
不一致，刷新才「诈尸」）；`send()` 的 `finally` 里**整块 `render()`** 而不是逐个把两个元素
`disabled = false` —— 后者在「流畅式中途换了节点」时会写到一个已脱离文档的旧输入框，而页面上
那个新输入框仍然是禁用的。`busyChat` 存的是 nodeId 而不是布尔：已经有一轮在跑时换到别的节点再发
也会被挡住，这是有意的 —— 两个 LLM 流同时写两个节点，用户没法同时读。

**「对话」页（`#/chat`）与助手是两件事，不合并。** 助手问的是**某个知识点**，答案落进那个节点的
学习记录、下次打开还在；这一页问的是**任何问题**，跑完一轮完整的 research agent，答完就没了
（`main.py` 只有 POST，没有历史 GET）。所以这一页**不做任何持久化**：不写 localStorage、不假装
有历史 —— 假装有历史、结果刷新后空了，比一开始就说明白更糟。空态文案因此自己带一句「这一页的
问答不会建图谱，也不保存历史」，为此 `bubblesHTML` 加了可选的 `emptyHint` 覆盖参数（默认那句
承诺的是「讲透之后系统会建议你点亮它」，摆在这一页是在承诺一件不会发生的事）。过程日志**每轮清零**
—— 累积整场会把正文淹掉，而它的价值就是「让你知道它现在在干什么」。路由 `#/chat` 刻意**不清**
`S.topicId` / `S.graph`：清了的话从左栏点回「知识图谱」就掉到首页，而用户失去的正是他刚才在哪儿。

**测试:** Node **255 passed / 0 failed**（177 → 255，新增 78 条）。新增
`tests/js/assistant.test.mjs`（26 条：胶囊/上下文/面板的字符串契约、收起时不拉历史、展开只拉一次、
`askAbout` 的预填在历史回来之后仍在、流式 token 进尾部气泡、**中途重绘后 token 仍进新气泡**、
**中途切节点后旧帧被丢弃**、空输入/无节点/已在流式一律不发请求、网络层失败仍要放开忙标志）、
`tests/js/chatview.test.mjs`（13 条：不假装有历史、过程日志每轮清零、日志里的 LLM 输出被转义、
流式与 done、同一时刻只允许一轮在流）；`chat.test.mjs` 加 12 条 `line()` 表驱动用例
（含「token/done/recommend 不产日志行」）；`store.test.mjs` 加 9 条 `relatedNodes` + 助手状态形状；
`assembly.test.mjs` 加「右栏无对话 UI」源码扫描与「挂载点 id 必须存在」；
`views.test.mjs` 加 18 条 Inspector 的 `statusHTML` / `relatedHTML` / `sourcesHTML` 与右栏渲染，
并把 `navHTML` 那条高亮用例补上第二项（它的注释写着「价值在加第二项的那天」—— 那天到了）。
Python **540 passed**（`test_index_declares_expected_modules` 与 `assembly.test.mjs` 的清单各加了两行）。

**这一轮的测试做了变异验证**（不然「新用例全绿」只说明它们没红过）：把 `relatedNodes` 改去读
节点字段 → 5 条红；让 `line()` 给 `token` 产行 → 1 条红；`renderChat()` 不再重读尾部气泡 →
2 条红；`sourcesHTML` 改回完整路径 → 1 条红；右栏塞一个 `<input>` → 「唯一对话 UI」扫描红；
`getElementById('assistant')` 改名 → 「挂载点 id」扫描红。六个变异全部被对应的用例抓住。

### 阶段 7 — 首次进入 / 完成态 / 设置页 / 响应式（完成）

**两种遮罩的「按过」记在两个不同的地方，这是这一阶段最容易写错的一处。** 首进入用
`KP.prefs`（`localStorage`，**跨会话**），完成态用 `S.view.completeSeenFor`（**本会话**）。
理由：首进入提示一辈子只该出现一次；而完成态是「这次点亮做到头了」的**现场反应** ——
换一个主题、或者重新进这个主题，它都该再出现，否则用户永远看不到「做完」这件事被承认。
写成一个标志的后果是「第一次全点亮之后，以后再也看不到完成态」，而它在页面上表现为
「好像本来就没有这个功能」。

- **`deepLink` 只在首次路由时判定一次**（`S.entry.deepLink` + `views/shell.js` 的 `routedOnce`）。
  每次 `route()` 都重新判的话，页内跳转时 `S.topicId` **已经**非空 → `deepLink` 恒真 →
  首进入遮罩永远不出现。这是「把当前状态当成进入方式」的经典写错法：两个值在页内看起来
  完全一样，只有「刚进来那一次」不同。
- **完成态是 `syncOverlay()` 带出来的，不是靠一次全量渲染带出来的。** 阶段 5 的铁律是
  「点亮只 patch 一个盒子」，于是点亮最后一个节点也**不**重绘整个 stage —— 完成态若只挂在
  全量渲染上，就要等用户碰一下筛选才出现，而那一下正是「我做完了」最该被承认的时刻。
  `syncOverlay()` 因此比较「算出来的 kind」与「当前画着的 `curOverlay`」，**只在变了的时候**
  才重绘一次（它挂在 `shell.refreshProgress()` 后面，那是 mastery 变化后唯一的汇聚点）。
  无脑重绘会把缩放/平移清掉 —— 而「图看哪儿」正是用户做完这件事时的现场状态。
- **设置页的两个危险动作都留着，但都写着代价。** 设计稿 §23 要求撤掉顶栏那个显眼的「重新研究
  并生成」—— 照做了；能力挪进设置页，且**不是只留一个破坏性的「重置」**：「重新研究并生成」是
  非破坏性的（同名知识点的进度与讲解记录保留），「重置当前探索」才是真实 `DELETE`。各自的代价
  写在按钮**旁边**：重跑会按新的 `order_index` 重算 `note_path`（`service.py:82`），用户写在正文里
  的「## 我的笔记」会留在磁盘上成孤儿、界面上再也看不到；重置不可撤销。**「重新研究并生成」先切回
  图谱视图再生成** —— `generate()` 把报告逐字写进 `#stage`，而设置页正占着 `#stage`，不先切走的话
  用户看到的是设置页被顶掉、以为出了故障。
- **轮次偏好在「发送那一刻」读（`KP.researchOpts()`），不是启动时的快照。** 快照的表现是
  「改完设置、刷新一下，它又被忽略了」—— 与「这个开关根本没用」在用户眼里没有区别。
- **中心环读 `progress.percent`，绝不自己算。** 这是 R6 的**第二个落点**（第一个是侧栏的进度卡）：
  后端是 Python 的银行家舍入（`round(12.5) == 12`），JS 的 `Math.round(12.5) == 13`，自算会让环上
  那个数字与侧栏**差 1%** —— 而它正是设计稿最强调的那个数字。测试用 12% 的夹具把这条钉住。
- **`localStorage` 全部收在 `KP.prefs` 一处**，一律 `try/catch`、一律 `JSON.stringify`。
  `'false'` 读回来是**真值** —— 一个「关掉的开关」会在下次打开时自己变回开。写失败时返回 `false`，
  设置页据此**当场**说「这个浏览器不允许本地存储，设置只在本次会话有效」（无痕模式与企业策略下
  `setItem` 真的会抛），而不是让用户改一个看起来生效、刷新就没了的开关。

**两处对计划字面措辞的有意偏离**（都写在代码注释里，理由在此）：

1. **不自造中心节点。** 计划 D2 要求「`depth-0` 为 0 个时合成一个主题中心」。实际做成
   `centerOf()` 在 0 个或 ≥2 个根时返回 `node: null`、环整个不画 —— 因为径向布局在原点**没有留
   位置**：第 0 环半径是 `DIAG/(2·sin(π/n))`，n=2 时是 `DIAG/2`，比一个盒子还窄。在最常见的
   2~3 节点星形图上，合成出来的中心**必然重叠**，而「无重叠」是布局测试的硬断言。
2. **`prefers-reduced-motion` 只关呼吸，保留静态 Glow。** 计划的验收写的是「关呼吸与 Glow」。
   Glow 是「已掌握」在四通道编码之外的识别通道，而那个系统偏好说的是**动效**，不是信息 ——
   关掉它，会让开了减少动效的用户少一条「哪几个点亮了」的线索。

**另有一处设计稿**要求、我拒绝做的**：§25 画的是「43 / 43 个核心关系」。关系**没有状态** ——
后端没有「这条前置关系学会了」这回事，那个 `/ 43` 是为了排版对称编出来的。所以完成态只写
`<b>mastered / total</b> 个节点`（真数字）+ `<b>M</b> 条核心关系`（同样是真数字，但没有分母）。
测试把它钉成断言（`!html.includes('4 / 4')`），免得日后有人「补全对称」。

**1280 断点只改宽度、不改结构。** 248 + 画布 + 300 在 1280 宽时画布只剩 732，那一档把两侧各收
一点（212 + 画布 + ~800）。计划写「侧栏可收起、Inspector 可折叠」，但收起是一个需要按钮 + 状态 +
记住偏好的交互，在 1280 这一档的收益只是 732→800；真正的取舍在 **1000** 那一档，那里 `#panel`
原本就挤没了，所以改成 `position: fixed` 浮层、网格回落成两列。顺手修掉了 **1000 与 1001 的侧栏宽
倒挂**：1000 那档写死 240 而 1280 档是 212，于是窄的那一屏反而更宽；现在两处都走 `var(--side-w)`。

**测试:** Node **287 passed / 0 failed**（255 → 287，新增 32 条）。新增 `store.test.mjs` 9 条
（`overlayKind` 的全表 + `prefs` 三种情形含「`localStorage` 抛异常」+ `researchOpts` 的 0/负数/脏字符串
一律不发）、`layout.test.mjs` 3 条（`centerOf` 单根/多根/横向/`depth: '-0'` 脏值）、
`views.test.mjs` 19 条（`ringGeom` 的越界与脏值不得产生 NaN、中心环长度**用后端的 12 而不是自算的 13**、
横向/多根/空图返回空串、两个遮罩的文案与真实数字、`roundsHTML` 的选中项恰好一个、
以及四条遮罩 DOM 流程与五条设置页 DOM 流程）、`chatview.test.mjs` 1 条（设置页写的轮次上限
**真的进了请求体**，没设过时字段整个不发）。

**这一轮也做了变异验证**（不然「新用例全绿」只说明它们没红过）。四个变异，逐个应用 → 跑 →
还原，**每一个都被**对应的那一条**抓住**，且只抓住那一条：

| 变异 | 红掉的用例 |
|---|---|
| `centerRingHTML` 改成自算百分比（`mastered/total*100`） | 中心环那条（12% 的夹具算成了 13%） |
| `completeHTML` 改回给关系写 `M / M` | 完成态那条（假分母被断言挡住） |
| `syncOverlay()` 去掉「只在 kind 变了才重绘」的判断 | 「全部点亮」那条（它先断言了「遮罩没变时不得重绘」） |
| `roundsHTML` 给认不出的值补一个选中项 | 设置页 `roundsHTML` 那条（「恰好一个选中项」被打破） |

**一处实现里的取舍如实记下，它不是缺陷**：`roundsHTML` 对**认不出的值**（手改过
`localStorage`、或者将来改了那组分档）**一项都不选中**，浏览器于是显示第一个选项
「用服务端配置」。我**没有**把它补成一个额外选项（凭空多一个只在这种情形下出现的 UI 元素），
也**没有**在 `readRounds()` 里把它归零 —— 归零会让「下拉框显示的服务端配置」与「请求里真的
带着的值」对不上，而那正是这一页要避免的那类错；请求照常带着它出门，后端的 `clamp_rounds`
负责收口。这条取舍写成了断言 + 注释，是「已知」而不是待修。

**阶段收尾实证（2026-09-11）：打包。** `python -m build --wheel` 之后直接读 wheel 的 `namelist`：**27 个
web 资源全部入包**（`web/css/*.css` 9 + `web/js/**/*.js` 17 + `web/index.html` 1），并且**逐个对上了
`index.html` 里那 26 个 `/static/` 引用 —— 缺失 0、包内多余 0**（唯一的「多余」就是 `index.html` 自己，
它不引用自己）。数量比阶段 1 记的 20 多 7 个，**不是漏包而是阶段 3~7 新加的文件**（`assistant.css/js`、
`chat.css/js`、`views/chat.js`、`settings.css`、`views/settings.js`）。这一步只能打真轮子看清单：
`tests/test_packaging.py` 只静态检查 `package-data` 的**模式**，查不出「模式写对了但 setuptools 的 glob
没匹配上」（`web/**/*` 那类写法会恰好漏掉 `web/index.html`）。

### 已知问题 / 验证缺口

- **仍需用户本人在浏览器里过一遍**：新建 → 生成 → 点节点 → 对话 → 点亮 → **刷新回原位**。测试覆盖的是
  纯函数与字符串产物，覆盖不了「图能不能看懂」「动画手感」「三栏比例」—— 与 Phase 9 留下的同一个缺口，
  没有替代品。**欠着的人工检查现在有四项**：① 阶段 3 的「在一张真实的 12 节点主题上对比两种布局」，
  以决定 `choose()` 的阈值要不要调（无重叠 ≠ 好看）；② 阶段 4 的 **1280×720 与 1440×900 实机观感**；
  ③ 阶段 5 的**拖 / 点 / Ctrl+滚轮手感** —— 尤其是「点击选中节点」在触控板上会不会被 4px 阈值误判成拖动，
  以及缩小到 0.4 之后节点名还可不可读（`MIN_SCALE` 定在 0.4 是估的，不是量的）；
  ④ 阶段 7 的**两种遮罩的实际观感与 1280px 下的三栏比例** —— 遮罩是 `var(--scrim)` 的半透明层，
  它在真实配色下到底「够不够挡、又不至于把底下的图糊掉」只能眼睛看；而 1280 那一档两侧收窄之后
  画布是 800 还是 780，取决于实测的滚动条与字号，不是算出来的。这两项都改一个 CSS 值就能调，
  所以是「看一眼就知道要不要动」，不是需要返工的缺口。
- **上面四项之外还有一项，而且这一项我无论如何测不到**：阶段 6 的**非流式 provider 路径**。
  一个 token 帧都不发的 provider 只在 `done` 里给正文（`done.content` 兜底因此存在），而本机配的是
  流式 provider —— `chat.test.mjs` 里那条 `done` 兜底用例是**构造**出来的，它证明代码写得对，
  不证明真实的那条路走通。要验它得换一个 `stream_capable` 为假的 provider 跑一次对话，
  确认气泡里有正文而不是一片空白。这是设计稿之外唯一无法用测试替代的验收项。
- **`tests/js/harness.mjs` 有意不加载 `app.js`**（它顶层就 `boot()`），所以它的正确性由「必须是最后一个
  脚本」+ 源码扫描 + Python 侧端到端守，不在沙箱里。
- **`.env`（含 API key）与 `data/learning.db`（全部学习记录）的暴露面**由
  `test_static_mount_does_not_expose_repo` 正面钉着（静态挂载
  只挂 `WEB_DIR`）。本轮没动挂载，但三栏改动让它仍是这一 Phase 里唯一「出错即泄漏」的地方。
- 累积项延续 Phase 6~9：向量语义召回、记忆注入、`query_knowledge_graph` 工具、`results/` 真实评测产物。

### 下一步

**阶段 0–7 全部落地，这一 Phase 的代码工作到此为止。** 剩下的是一条交接清单，按「谁都做得动」
排序：

1. **用户本人的浏览器走查（唯一的验收动作，没有替代品）**：新建 → 生成 → 点节点 → 对话 →
   点亮 → **刷新回原位**；再看上面「已知问题」里那四项人工检查（两种布局的真实观感、1280×720
   与 1440×900、拖/点/缩放手感、两种遮罩与 1280px 三栏比例）。四项都是「看一眼就知道要不要
   动一个 CSS 值」，不需要返工。
2. **非流式 provider 的那条路**（见「已知问题」）—— 本机测不到，要换一个 `stream_capable`
   为假的 provider 跑一次对话。
3. **文档收尾**：`docs/phase-10.md`（如有必要）、`README.md` 里的截图与「三栏工作台」描述。
4. **第 11 阶段（Docker）** 在这里接上：Phase 10 交付的静态挂载与 package-data 就是它要打包的东西。
   （打包本身已在阶段收尾时实证过 —— 见上面「阶段收尾实证」，27 个资源、26 个引用全部对上。）

每阶段结束跑 `pytest` + `node --test` 并**至少人工冒烟一次**这条纪律沿用下来 —— 上面的测试覆盖
纯函数与字符串产物，但覆盖不了「图能不能看懂」。

---

## 走查反馈修复 —— 上面那份交接清单的第 1 项真的做了（2026-09-12）

用户在浏览器里跑了应用，提出三条：

1. 三条栏的宽度不能左右自行调整，希望像别的项目那样让用户拖；
2. 对话（检索）页聊天框太小、没占满中间那一列，内容超过一屏之后滚不下去；
3. 知识图谱生成之后，点节点不出对应的知识内容。

第 3 条附了一句它自己就是判据的话：**「面板完全没变，还是那句『点击图谱中的任意节点』」**。
这条把我先前的判断直接证伪了 —— 我当时说「后端和点击链路都是好的，真凶是节点的正文被默认折叠
在一个 `<details>` 里」。面板一个字都没变，说明 `KP.views.inspector.open()` **根本没被调用到**，
折叠与否是另一回事（那一步最终**没有动**，它本来就是设计）。

### ③ 点击节点无反应：指针捕获把 `click` 重定向了

`graph/canvas.js` 的 `wire()` 在 `pointerdown` 里对 `#graph-scroll` 调了 `setPointerCapture`。
指针一旦被捕获，后续的**兼容性 `click` 会被派发到捕获元素**上 —— 于是选中逻辑里那句
`KP.dom.closestAttr(e.target, 'data-id', el)` 读到的 `e.target` 是容器自己，永远匹配不上任何 `data-id`。

把归因收紧的是这条自洽性：**全前端只有这一处依赖 `click` 的目标**，而工具栏、缩放按钮、筛选芯片、
两种遮罩全都是 `#graph-scroll` 的**兄弟**，捕获影响不到它们 —— 它们一直好用，坏的正好是唯一那一处。

改成 `pointerdown` 记命中、`pointerup` 再选中：

```js
let downHit = null;
scroller.addEventListener('pointerdown', (e) => {
  if (e.button !== undefined && e.button !== 0) return;      // 右键不选
  downHit = KP.dom.closestAttr(e.target, 'data-id', el);      // 命中必须在**这一**刻判定
});
scroller.addEventListener('pointercancel', () => { downHit = null; });
scroller.addEventListener('pointerup', (e) => {
  if (e.button !== undefined && e.button !== 0) { downHit = null; return; }
  const hit = KP.dom.closestAttr(e.target, 'data-id', el) || downHit;   // 捕获与否都能命中
  downHit = null;
  if (KP.graph.canvas.dragged()) return;                     // 拖动过的那一下不算点击
  if (hit) KP.views.inspector.open(hit.getAttribute('data-id'));
});
```

`|| downHit` 是有意的：**我没能在真浏览器里复现**这条，所以写法在「捕获生效」与「不生效」两种
情形下都要能选上。哪一条理论与事实不符都不会让功能再挂一次。

**为什么既有测试没抓到**：Node 桩测试是**直接**对指定元素派发 `click` 的（`fire` 不设 `e.target`），
绕开了捕获这条路径 —— 这是一个「测试的调用方式覆盖不到真实调用方式」的缺口，不是断言写少了。
现在补了 5 条用例，其中最关键的一条**显式模拟重定向**：`pointerdown` 的目标是 `<g>`，
`pointerup` 的目标是容器；另有「拖动之后那一下不许选中，但下一次干净的轻点必须选中」。

### ② 对话页：一列一条滚动轴，输入框吸底

用户选的是「**铺满中间整列，整页随内容滚动，输入框吸在底部**」。

我按「**中间那一列**自己滚」来落，而不是让整个文档滚：三栏工作台里让 `<body>` 滚会把侧栏与右栏
一起滚出视野，那与设计稿的三栏常驻是矛盾的。具体是 `.research` 去掉 `max-width:860px` 的居中收缩、
改为 `overflow-y:auto` + `scroll-padding-bottom:78px`；`.r-msgs` 交出滚动轴（改 `flex` 列 + `gap`）；
`.r-composer` 改 `position:sticky; bottom:0` 吸在滚动区底部。`scrollToEnd()` 改成优先
`scrollIntoView({block:'end'})`（这样才吃得到 `scroll-padding-bottom`），失败再退回 `scrollTop = scrollHeight`。

两个刻意的例外，都写进注释了：`.r-log`（请求日志）保留自己的 `max-height:88px` 小滚动区 —— 它是
诊断用的旁支信息，跟着主列一起长会把正文挤没；输入框上沿保留了 `border-top` 分隔线，否则滚动内容
会从它底下"穿"过去。

**这一处与用户的措辞有偏差**（「整页」vs「整列」），已单独向他说明，改一行 CSS 就能换。

### ① 可拖拽的三栏分隔条

新增 `js/resizer.js`（约 170 行）与两条 `role="separator"` 的竖条（`index.html` 里 `.app` 的直系
子元素）。拖 = 改宽度，双击 / `Home` = 恢复默认，方向键 = 16px 微调；持久化在既有 `KP.prefs` 的
`layout.cols` 键下（`S.view` 照旧不落盘）。区间 `side 176~420 / insp 248~560`，并且**画布至少留 360px**
——把画布挤没是这一处唯一会造成实际伤害的拖法。

三个值得记下来的决定：

- **宽度写 `.app` 的**内联** CSS 变量**，不是写 `.app{--side-w:...}` 之类。因为 `--side-w`/`--insp-w`
  在 1280 断点会在 `:root` 上被**重新定义**，内联值在每一个断点上都赢，这正是「用户拖过的宽度
  不该被断点悄悄改掉」想要的。
- **`clampWidth` 沿用 `canvas.js` 的 `clampScale` 规矩：「没设过」≠「想缩到最小」**。`Number(null)`
  恰好是 0，拿它去夹会得到下限 176px —— 一条被挤窄的栏，而真相是偏好里根本没有这一栏。
  这条是**测试逼出来的**：第一版就是这么错的，测试红了才改。
- `resizer.js` 只算「拖到了多少」，写哪个变量、记不记得住走调用方传进来的 `commit(kind, px)`。
  桩元素没有布局，这一层不碰 `el.style` 才能在 Node 里整条跑通。

分隔条是 `role="separator"` + `tabindex="0"` 的**可聚焦控件**，所以 `aria-valuemin/max/now` 跟着
宽度一起走 —— 聚焦之后读不出当前值的控件比不聚焦更让人摸不着头脑。

### 测试侧的两件事

- **`tests/js/harness.mjs` 的 `FakeElement` 根本没有 `style` 属性**，新代码一写宽度就在桩里抛异常。
  修的是**桩**（加了 Map 支撑的 `setProperty`/`getPropertyValue`/`removeProperty`），不是把生产代码
  弯过去 —— 那种失败看起来像是被测代码写错了，实际是桩少了一个标准属性。
- 新 JS 文件要同时进 `tests/js/assembly.test.mjs` 的 `EXPECTED_SCRIPTS` 与 `tests/test_api.py` 的脚本清单，
  否则「孤儿文件」与「有序列表 == 预期」两条会红。

**这一轮的测试有牙，是验过的**（不是「跑绿了就算」）：把 `pointerup` 的命中表达式改成只留
`closestAttr(e.target, ...)` → views 组两条红；把 `const grow` 改成 `1`（右栏方向写反）→ resizer
组三条红；把 `max-width:860px` 塞回 `chat.css`、把 `position:sticky` 去掉 → 两条 CSS 守卫断言翻成 `False`。
三处都改回来了。

### 收尾实证（2026-09-12）

- `node --test tests/js/` —— **307 pass / 0 fail**；`pytest -q` —— **541 passed**。
- 端到端走的是**进程内 ASGI client**，**没有**绑 8000 端口：`/` 200（`rz-side`/`rz-insp` 都在 `#app` 里）、
  `/static/js/resizer.js` 200 且 `application/javascript`、每个 `/static/` 引用都 200、
  脚本顺序以 `['views/assistant.js','views/chat.js','views/settings.js','app.js']` 收尾。
  （刻意不绑端口：此前留过一个 uvicorn 占着 8000，用户自己启动时撞上过 `[WinError 10013]`。）
- 顺手关掉的一个疑问：上一轮记的「`index.html` 5114 bytes」与本轮实测不符 —— 直接逐字比对后确认
  服务端返回与磁盘**逐字相同**（4533 字符 / 6125 字节），差值就是本轮新加的那些行，不是截断。

**仍然欠着的人工验收**：这三个修法都只能在**你的浏览器**里最后确认一次 —— ③ 点节点出内容；
② 长回答下能不能一路滚到底、输入框是否稳稳吸底；① 拖起来的手感、以及 1280px 下两侧同时收窄
还够不够用。与前面那份缺口清单是同一个性质：一个 CSS 值就能调，看一眼就知道。

## 走查反馈第二轮 —— ①②③④（2026-09-12）

用户在浏览器里又跑了一遍，提了四条：

1. 学习对话框固定在左下角，影响观感；而且**关闭后没有办法再次打开**。希望提问框固定在中间列下面一部分；
2. 右侧可拖拽的那条线**与屏幕上呈现的线不重合**；「学习状态」那一栏贴齐左边，不符合居中；
3. 点「标记为已掌握」之后，其他节点**没办法再开始学习**（点「开始学习」没有任何反应），要点也点不动；
4. 针对某个知识点查询、拿到提纲与知识图谱之后，**最后的 markdown 文件里没有该知识点的提纲** ——
   于是「在形成知识图谱前就该有针对相应知识点的提纲」这件事没有落点。

四条里有**两条是同一个根因**（①的后半句与③），而且它是个纯前端的**静默失效**：没有任何报错，
画布看起来完全正常。四处修法的共同点是「错起来不报错」，所以这一轮的重点是把它们各自钉在测试上。

### ③ 点「开始学习」没有任何反应 —— `S.route` 停在了上一屏

**根因不在助手身上，而在 `S.route`。** 它是 `location.hash` 的一份缓存，而此前**只有 `route()` 会写它**
（`views/shell.js`），`route()` 的日间入口只有两个：`init()` 与 `hashchange` 监听。但
「侧栏点主题卡 / 新建主题 / 生成完落 hash / 右栏开关节点」这几条路径走的是 `writeHash()`，
而它按设计**只用 `history.replaceState`**（注释里写明了理由：赋 `location.hash` 会触发 `hashchange`，
`hashchange` 又调 `route()`，形成死循环）。`replaceState` **不触发 `hashchange`** —— 于是 hash 已经
指着图谱了，`S.route` 还停在 `chat`。

放大它的是 `views/assistant.js:121`：

```js
if (S.route && S.route.view === 'chat') { el.innerHTML = ''; el.hidden = true; return; }
```

这一句在「对话」页是**对的**（那一屏自带完整输入框，再叠一个停靠输入框是纯混乱）。但当 `S.route`
是**过期的 `chat`** 时，它把每一个入口一起吞掉：「开始学习」→ `openFor` → `render()`；要点与提纲芯片
→ `askAbout` → `render()`；收起后那条栏上的「展开」连长都没长出来（`innerHTML` 是空的）。这正是
用户报的①后半句与③ —— **同一个 bug 的两个症状**。

「点了『标记为已掌握』之后才坏」是**相关性不是因果**：`setMastery` 的 `finally` 会调
`assistant.render()` + `refreshProgress()`，它们一起落进同一个 early-return，看起来像点亮动作
把助手弄坏了；其实在导航那一步就已经坏了。

**修法写在 `writeHash()` 里**（它是「当前状态 = 这个 hash」这同一件事的另一半 —— 它反正已经算出了
目标 route），而不是逐个调用点补：

```js
const nodeId = dropNode ? null : S.nodeId;
const hash = KP.route.formatHash({ view: 'graph', topicId: S.topicId, nodeId });
if (location.hash !== hash) history.replaceState(null, '', hash);
S.route = { view: 'graph', topicId: S.topicId, nodeId };   // ← 新增
KP.views.assistant.render();                                // ← 新增：route 变了就得重画依赖它的东西
```

`views/settings.js` 的「重新研究」里原本**手工补过一次**同一个洞（注释还写着 `replaceState` 不触发
`hashchange`）—— 那行因此撤掉了：同一个根因散着补，只会漏掉下一个调用点。顺带修掉的还有一个
同源的轻症：从设置页点侧栏主题卡时，导航高亮会停在「设置」上。

### ① 助手停靠到中间列底部（不是浮层）

`#assistant` 从 `.app` 里 `position: fixed; left/bottom: 18px` 的浮层挪进 `<main>`，成为与 `#stage`
并列的 flex 子项：画布 `flex: 1` 吃掉其余高度，它 `flex: 0 0 auto` —— 「画布让出一块地方给它」
是**布局本身**保证的，不需要 z-index，也不可能盖住任何东西。收起态不再是漂在画布上的小胶囊，
而是**横铺满整列**的一条栏，右侧固定挂着「展开 ▲」—— 一个带动作词、位置固定的入口（上一版只写
节点名，读起来像个装饰性标签，这就是「关闭后找不到回来的路」）。

配套两条容易漏的：`display: flex` 会盖掉浏览器给 `[hidden]` 的默认 `display: none`，所以
「对话」页那句 `el.hidden = true` 必须配一条 `#assistant[hidden] { display: none; }`；
`.as-log` 需要 `min-height: 0`，否则 flex 子项的 `min-height: auto` 宁可撑破父级的 `max-height`。

顺手修的一个**no-op**：`renderChat()` 里的 `box.scrollIntoView({block:'nearest'})` 滚的是**祖先**容器
（让这个盒子进视野），而 `.as-log` 自己就是滚动容器、它的盒子一直在视野里 —— 所以正文流出可视区
之后从来没人把它滚下来过。改成「读一下离底多远，本来就贴底（< 40px）才跟」：跟随最新一条，但
用户往上翻看历史时绝不把视图拽回底部。

### ② 分隔线：只剩常驻的那一条

旧态是两样东西叠在同一个接缝上：`.col-resizer` 一条**悬停才出现**的 1px 线，加上 `#sidebar` /
`#panel` 各自的静态 `border-right` / `border-left`。用户看到的「可调整的线与实际呈现的线有距离」
就是这两条对不上（画布自己的滚动条还在它左边 9px 处，那条灰条看起来更像「线」）。现在栏自己的
静态边框撤掉，只剩常驻的 `#rz-side::after` / `#rz-insp::after`，居中在 9px 命中区里（`left: 4px` +
`translateX(-50%)`）—— 「你能拖的那条线」与「你看到的那条线」从设计上就是同一条。唯一的例外是
≤1000px：那一档右栏变浮层、`#rz-insp` 已隐藏，浮层左边缘需要它自己的边框。右栏「学习状态」段
（`.sec-status`）改为居中。

### ④ 提纲：打开节点时**按需**生成，写进 `## 提纲`

用户选的策略是「打开节点时按需生成」（不是建图时批量生成）—— 多数节点用户永远不会点开，批量
会让成本翻倍。实现分三层：

- `learning/outline.py`（新）：`generate_outline` / `clean_outline` / `_excerpt`。**永不抛异常，失败
  返回 `[]`**。这条与 `service.build_graph_from_report` 的降级链是**相反**的取舍：降级链的产物立刻
  显示在屏幕上、有总比没有好，而提纲会被**永久写进用户自己的文件**，且 `notes.insert_outline`
  **从不覆写** —— 写坏了没有第二次机会。所以这里不降级、不留占位、宁可让用户看到一次「重试」。
- `learning/notes.py`：`insert_outline` 是**纯新增**的一段（幂等、空条目不写、插在第一个 `^##\s`
  之前即 `## 要点` 之上、没有二级标题则接末尾），既有内容一字不改 —— 与 `update_note_status` /
  `append_explanation` 同一条先例。`rehome_note` 也因此第一次有了调用方（此前是死代码）。
- `api/learning.py`：`POST /nodes/{id}/outline`（**POST 因为它会调 LLM**；GET 路由必须零 LLM 这条
  纪律不变）。前端 `views/inspector.js` 在打开节点后**只在文件里没有那一段时**才去要一次，
  三种收场都有 UI：「正在生成 / 一列可点的步骤 / 一句可重试的失败」。条目点一下走 `askAbout`
  的同一路径（预填「请讲讲这一步：…」），与要点芯片一致。

一个实现细节值得记：前端**不**复刻「`## 提纲` 插在哪一行」的规则，它换用后端带回来的整份正文
（`notes.insert_outline` 是那份规则的唯一实现）。`markdown.splitNoteOutline` 只负责把那段拆出来
渲染成一列 —— 留在正文里会让同一份提纲在面板上出现两次（一次可点、一次不可点）。

`clean_outline` 的第一版有个真险：`str()` 对任何对象都有返回值，于是 `None` 会变成 `"None"`、
`[]` 会变成 `"[]"`，一路通过后面所有检查、最后作为一条提纲写进用户的文件。是**我自己的参数化
用例**（`test_clean_outline_returns_nothing_for_any_odd_shape`）抓到的，改成先筛类型再 `str()`。

### 测试（这一轮的测试有牙，是验过的）

新增/扩展：`tests/js/markdown.test.mjs`（`splitNoteOutline` 五个边界，含「我的笔记里的列表不算
提纲条目」与「`### 提纲` 不算」）、`tests/js/assistant.test.mjs`（「展开」这个动作词、`hidden` 而不是
只有 `innerHTML === ''`、吸底跟随与**用户往上翻时不打扰**）、`tests/js/views.test.mjs`（提纲的
渲染与请求时机、以及 ③ 的三条回归用例）、`tests/test_api.py` 三条 CSS 回退哨（助手不再是浮层 /
分隔线居中在命中区里 / 「学习状态」居中）、`tests/test_learning_store.py`、`tests/test_learning_outline.py`、
`tests/test_api_learning.py`。

**变异验证**（每条断言都要有守卫意义，不是「跑绿了就算」）：

- CSS 七处改写 → 三条回退哨各自翻红（助手回到 `position: fixed`、`#assistant[hidden]` 丢掉
  `display: none`、收起态退回胶囊、线偏离 4px、`#sidebar` 加回 `border-right`、「学习状态」改回
  `text-align: left`、按钮不铺满）；
- `writeHash` 三处改写 → `S.route` 同步去掉：③ 三条 + 设置页一条红；助手重画去掉：③ 两条红；
  `dropNode` 不丢 `nodeId`：机制层那条红。

一条**测试桩的补丁**（与上一轮给 `FakeElement` 补 `style` 同一性质）：桩此前没有
`scrollTop`/`scrollHeight`/`clientHeight`，于是「读一下离底多远再决定要不要吸底」的代码在沙箱里
只读到 `undefined` —— 那种失败看起来像被测代码写错了。补成可写的数字，由用例自己摆位置。

`node --test tests/js/` —— **329 pass / 0 fail**；`pytest -q` —— **579 passed**。

**仍然欠着的人工验收**：① 停靠之后画布与助手的比例（4 成高在真机上够不够）、收起/展开那条栏
读起来像不像按钮；② 拖起来时那条线与鼠标位置是否严丝合缝；③ 从对话页点侧栏主题卡、再从右栏点
「开始学习」这条真实路径；④ 一个真的没打开过的节点，首次打开等那几秒时的观感，以及生成之后
文件里 `## 提纲` 的位置是否符合预期。与前面几轮同一个性质：测试覆盖纯函数与字符串产物，
**「图能不能看懂、手感对不对」没有替代品**。

## 走查反馈第三轮 —— ①②③（2026-09-13）

用户在浏览器里又跑了一遍，提了三条：

1. 先从提纲里点一个问题（助手预填）→ 再点「继续学习」→ 有时 **AI 出不了答案**；
2. 提纲里知识点的**列法不对**：应该是「有哪些分块技术 → 各自怎么处理 → 会碰到什么问题 → 怎么解决」
   这样一条条**小点**，不要很多字；
3. 控制台里看到在下载 `https://huggingface.co/BAAI/bge-m3/resolve/main/adapter_config.json`，
   不知道那是什么。

### ① 「AI 出不了答案」—— 一份**过期的历史快照**盖掉了在途那一轮

复现路径很具体，而且**服务端其实答完并落库了**（刷新就能看到全文），所以它是一条纯前端的静默失效。

机制在 `views/assistant.js` 的 `ensureMessages`：助手打开时发一次 `GET /nodes/{id}/messages`。而
「点一条提纲 → 点开始学习」这条路上，历史 GET 正打在用户提问**之后**才落地 —— 它是**提问之前**
那一刻的历史（服务端要等这一轮流完才把回答落库，所以那份快照的末尾是一条**用户**消息）。拿它
覆盖 `S.messages` 的后果分两步，两步都静默：

1. 本地那份「用户问 + 助手占位」被抹掉，屏幕上刚流出半句的回答**立刻变空**；
2. 更糟的是尾部气泡的判据 —— `chat.tailBubble` 看见末条是用户消息就返回 `null`，于是**余下的
   token 全部写进空气**，`done` 之后也不会回来。用户看到的就是「AI 不出答案」。

修法是给「这份快照还新不新」一个判据：模块级计数器 `roundsSent`（**只增，换节点归零**），
`ensureMessages` 在发请求前记下 `issuedAt`，回来时对不上就整份丢弃。判据刻意**不是**「此刻有没有
在途 session」—— 一轮跑完之后 `session` 就被清掉了，而更旧的那份快照完全可能在它之后才落地，
按 session 判会漏掉，回答照样丢。代价（这一屏暂时少了更早的历史）与既有的「用户切走节点就丢弃
在途响应」是同一条取舍：响应比本地状态旧就丢；重开节点会再拉一次。

顺手修掉同一族的第二个入口：**收起助手时此前会 `session = null`** —— 于是「收起草稿」这个动作把
在途的那一轮也一起丢了（帧被 `if (!session …) return` 全部拦下，服务端答完落了库，面板上什么
都没有）。收起现在只改 `open`，帧继续进 `S.messages`（气泡不在文档里，画上去看不见也无所谓），
展开时 `renderChat()` 会把全文补回来。

### ② 提纲的每一条改成**小点**（8~14 字），并给一条「重新生成」的出口

用户的原话是「这里的每一点都应该是列小点 不用很多的文字」。这不是排版问题，是**颗粒度**问题：
第一版 prompt 写的是「说清分块粒度为什么会改变检索召回」那样的一句话，读起来像任务清单 —— 每
一条都得先读懂，才知道该学什么。改动分三处：

- `learning/outline.py`：prompt 里把**长度（8~14 字）、词性（名词短语或极短问句）、覆盖次序
  （① 有哪些种类 → ② 每一类怎么做 → ③ 会遇到什么问题 → ④ 怎么处理）**全部写死，并给了一个
  跨领域的风格示例（手冲咖啡）说明只看长度与颗粒度；`ITEM_LIMIT` 从 80 收到 **30** —— 它是
  **安全网而不是目标**，80 字足够塞下一整句，于是「模型写长句」时截断也救不回来（产物看起来
  仍然是一份合法提纲，只是每一条都读不动）。
- 前端 `views/inspector.js`：动作词跟着改（「就**这一点**提问」、预填 `请讲讲：`），与要点芯片
  完全同前缀。
- **已有的节点文件换不掉**：`ensure_node_outline` 的第一道闸门是「已经有提纲就回读、零 LLM」，
  而 `insert_outline` 见了整行标题就返回 False。所以新增一条明确的出口 ——
  `POST /nodes/{id}/outline {"force": true}`：跳过那道闸门重生成一次，再用新的
  `notes.replace_outline` **把 `## 提纲` 那一段整段换掉**（其余字节逐字保留，换失败时保留上一版）。
  右栏「提纲」标题行右侧多了一个一行小字的「重新生成」（刻意不做成按钮：它每次都要花一次模型
  调用，而这一段的主动作是「点一条去问助手」）。

`replace_outline` 与 `insert_outline` 是**互斥的一对**：前者只在**有**那一段时动手、后者只在
**没有**时动手，两条都不接的情况宁可什么都不做 —— 半成品（比如被抹成空白的 `## 提纲`）比旧提纲
糟得多。

### ③ 那个 `adapter_config.json` 是什么 —— 没关系，那是 RAG 的本地向量模型

它不是这个项目在下载什么可疑东西，而是 **`BAAI/bge-m3` 这个 embedding 模型仓库里的一份小配置文件**。
链路是 `knowledge_pilot/rag/embedder.py` 懒加载 `SentenceTransformer("BAAI/bge-m3")`，首次真正用到
embedding 时由 huggingface_hub 把仓库里的文件挨个取回来：先是一批几百字节的 JSON（
`adapter_config.json`、`config.json`、`modules.json`、`sentence_bert_config.json`…），**跟着来的才是
几 GB 的权重**（BGE-M3 约 2.2GB）。它只在 **RAG 开着**（`RAG_ENABLED`，默认 `false`）时才会发生，
下载一次之后走本地缓存，不再重复下载。

国内网络可设 `HF_ENDPOINT=https://hf-mirror.com`（README §首次运行、`.env.example` 第 19/48 行与
`docs/phase-1.md` 都已写明）；缓存位置由 `EMBEDDING_CACHE_DIR` 决定（空 = HF 默认缓存，建议
`data/models`）。开 `RAG_RERANK_ENABLED` 还会再多一个 `BAAI/bge-reranker-base`（约 1.1GB，首次
rerank 时下载）。**这一条只解释，没有改代码**。

### 测试（这一轮的测试有牙，是验过的）

新增：`tests/js/assistant.test.mjs` 三条 ① 的回归（慢的历史快照不得冲掉在途那一轮 / 一轮结束后
落地的快照同样丢弃 / 收起中途那一轮不丢、展开能看到全文）、`tests/js/views.test.mjs` 两条 ② 的
（`outlineHTML` 三种状态与「重新生成」按钮的有无、点它是否带着 `force` 走并换成新的一列）、
`tests/test_learning_store.py` 三条 `replace_outline`（只换那一段 / 提纲是最后一段时不咬掉尾巴 /
三种「什么都不做」）、`tests/test_api_learning.py` 三条（`force` 真的换了 / 换失败保留上一版 /
不带 body 与 `force:false` 都还是回读）、`tests/test_learning_outline.py` 一组 **回退哨**
（长度区间与覆盖次序写在 prompt 里、输出仍是严格 JSON、`ITEM_LIMIT` 贴着目标而不是形同虚设）。

**变异验证**：① 的 `roundsSent` 守卫去掉 → 两条红；`session = null` 加回去 → 一条红；
② 的后端 `force` 忽略 → 两条红、`replace_outline` 换成 `insert_outline` → 一条红、前端不带
`force` 与在途时不留旧的一列 → 各一条红；`ITEM_LIMIT` 放宽到 80 与 prompt 抽掉长度区间 → 各红。

`node --test tests/js/` —— **334 pass / 0 fail**；`pytest -q` —— **590 passed**。

**仍然欠着的人工验收**（与前面几轮同一个性质，测试覆盖纯函数与字符串产物）：① 那条路径在真
浏览器里连点几次是否还会丢回答；② 新口径的提纲在一个**真节点**上读起来是不是「一眼扫过就知道
漏了哪一块」，以及旧节点点「重新生成」之后文件里那一段的样子；③ 等模型下载完，embedding 那
一步是否正常出结果。

## 走查反馈第四轮 —— ①②③（2026-09-13）

用户第四遍走查，提了三条：

1. 生成知识图谱时那张卡片在**中间列**，内容一长「页面就不匹配」——下半截与按钮都看不到了；
   希望把这块挪到右栏（「显示节点详细知识点的位置」），且**只在生成图谱之前**；
2. 右栏那条**可拖的分隔线**画出来的位置比真实分界**偏左一点**；
3. 点「Markdown 正文」之后**没有下文** —— 想要右栏进一个全新的页面、大标题「讲解记录」、
   能返回节点界面，并且**真的能改笔记**。

①③ 的范围由用户当场拍板：① 生成卡**连那份逐字研究报告整块**进右栏；③ 可编辑的范围**只有**
`## 我的笔记`（讲解记录是只追加的学习资产，程序还要往里写）。

### ① 那张卡为什么会被撑破 —— 中间列**没有滚动容器**

卡片渲染在 `#stage` 里，而 `#stage` 是 `flex: 1; min-height: 0` 却**没有 `overflow`**，外面
`.app` 又钉着 `height: 100vh`。于是卡片里那份逐字研究报告一超过视口，多出来的部分**既不在文档流里
可滚，也不在任何容器里可滚**：它被直接裁掉，页面也不会滚。这不是「样式不好看」，而是**内容不可达**
—— 生成时用户最想看的正是那份报告，且按钮也在被裁掉的那半截里。

修法不是给 `#stage` 加 `overflow: auto`（那会让整张画布连带滚动条一起变），而是**按用户说的把这块
搬进右栏**：右栏本来就是「一列 + 一个 `.scroll`（`flex: 1; min-height: 0; overflow-y: auto`）」的
结构，长内容天然在栏内滚、页面比例不动。三处配套：

- `views/graph.js` 拆出两个纯函数 —— `generateCardHTML(topic)`（原模板一字不改）与
  `pendingHTML(topic)`（中间列的空态：「这张学习图谱还没有生成。**在右侧**点『开始生成』…」）；
  `renderGenerateCard()` 变成「中间写说明 + 右栏写卡片」，`generate()` 里查 `gen-go`/`gen-log`/
  `gen-body`/`gen-timer` 的四处容器一并从 `stage()` 换成 `views.shell.panel()`；
- 卡片外面套一层 `<div class="scroll">` —— 右栏唯一会滚的那个容器；
- CSS 规则从 `graph.css` 搬去 `inspector.css`（规则跟着**容器**走），并加一条 `#panel .gen-card`
  覆盖把「浮在画布上的卡片」那套（`max-width: 780px` / 圆角 / 阴影 / 边框）去掉 —— 它现在是
  **整栏的一页**。`padding` 也归零：外面那层 `.scroll` 自带 `13px 17px`，两边都留会让卡片左右各空 34px。

**`#gen-body` 保留自己的 `max-height: 40vh` 与内部滚动**，这一条容易被当成「多余的一层滚动」删掉：
`generate()` 每收一个 token 就写一次 `bodyEl.scrollTop = bodyEl.scrollHeight`（流式跟随），容器没有
滚动条的话那行是**空操作**，报告会涨出栏外。

### ② 分隔线偏左 9px —— 上一轮只修了「线居中在命中区里」这半条

`.col-resizer` 是 9px 宽的命中区、里面 1px 的线（`left: 4px` + `translateX(-50%)`，阶段 0–7 那轮
修过）。这一次错的是**另一半**：命中区本身有没有居中在**网格边界**上。

```
#rz-side { left: var(--side-w); transform: translateX(-50%) }   /* 左锚定:-4.5 + 4.5 = 0 ✓ */
#rz-insp { right: var(--insp-w); transform: translateX(-50%) }  /* 右锚定:+? - 4.5 = 0 ✗ */
```

`right` 锚定下，`-50%` 把整个命中区**再往左挪了 4.5px**（线也跟着挪）。改成 `translateX(50%)` 之后
两侧的算法一致：命中区落在分界两侧各 4.5px，与左栏那条完全对称。**为什么一直没被发现**：左栏那条
用 `left:` 锚定，同样的 `-50%` 恰好是对的 —— 只有右栏错，而它偏出去之后正好压在画布自己的滚动条上，
看起来像「滚动条的位置」。

测试相应地从「线在 9px 命中区里居中」扩成**两个等式**：`anchor_offset + shift ± hit/2 == 0`，两个
分隔条各算一遍。只钉半条的代价就是这一次要走查第二轮才发现。

### ③ 「Markdown 正文」那句话是假的 —— 现在它是一个真的页面

原来是个 `<details class="file">`，summary 上写着「可编辑 · 已落盘」，打开却是一段只读的
`renderMarkdown` 产物。**最糟的不是功能缺失，是那句说明**：用户会以为自己改过的东西已经存了。

现在点入口 → 右栏换成「讲解记录」页：大标题 + `← 返回知识点` + 只读的讲解记录（正文渲染）+ 可编辑的
「我的笔记」。这是**面板内的一页、不进 hash**（刷新回到节点详情 —— 草稿本来也只在内存里），返回入口
占的位置与节点页的 ✕ 相同，换页时标题那一条不会跳。

后端只加**一个只动那一段**的写口，与提纲的 `insert_outline`/`replace_outline` 同一套路：

- `notes.write_user_notes(path, text)` —— **只换 `## 我的笔记` 那一段**（到文件末尾或下一个整行 `##`），
  front-matter / 提纲 / 要点 / 讲解记录**逐字保留**；没有这一段就追加到末尾；**空文本也保留标题行**
  （那一行是 `append_explanation` 找的锚点，删掉它会让下一次追加顺手补一个用户区、位置会漂）；
  文件不在 → `False`（调用方先 `rehome_note`，与 `ensure_node_outline` 同一道闸门）；
- `service.note_file(store, node_id, notes_dir)` —— 拿路径，文件不在就先重建骨架；
- `POST /nodes/{id}/note`，body `{note: str}`（`max_length=100_000`），返回**改完之后**的完整正文。
  **纯写、零 LLM**，所以与 `/mastery` 一样**不带 `ChatDeps`** —— API key 缺失不该挡住一次落盘。

前端三件要留意的事：

- **`markdown.splitNoteSections` 与后端是同一条判据。** 判据是**整行** `^## `（与 `notes.py` 的
  `heading_index` / `write_user_notes` 一样）。判据不同的话会出现「界面上显示的那一段」与「保存时被
  换掉的那一段」不是同一段 —— 讲解记录里的 `### 第 N 轮` 子标题尤其危险：按「以 # 开头」切会把讲解
  记录切成几十段，而屏幕上只看得出来「怎么只剩最后一轮」。`pickNoteSections` 改成它的一个过滤
  （一份规则只留一个实现），既有四条测试逐字不变。
- **草稿住在 `S.note.draft`，不在 DOM 里。** `render()` 换掉整个 `innerHTML`，而右栏在**聊完一轮后
  会重绘一次**（`refreshNode` 更新「已对话 N 轮」）—— 只靠 `input` 监听器的话，用户敲的字会在那次
  重绘里**静默消失**。所以 `render()` 第一件事是 `captureDraft()`，`bindNote` 渲染后再把草稿写回
  `.value`（值在两个地方都落，于是「重绘不丢草稿」可以直接断言）。`draft === null` 表示「没改过」，
  回落到文件里那一段；**`captureDraft` 拿「本该显示的那一份」当基准比过再收** —— 少了这一步，
  保存成功那条路永远清不掉草稿（重绘会把已经存好的内容再收成一次「本地改动」），入口按钮上就会
  一直挂着「有未保存的修改」。
- **只有两条路会真的丢掉草稿**：切到另一个节点、关掉节点详情 —— 那两条走一次 `confirm`。
  「返回知识点」不算（草稿留着，入口按钮上写「有未保存的修改」）：把它也做成确认的话，用户每来回看
  一次讲解记录都要点掉一个弹窗。`close()` 因此**返回布尔**：点 ✕ 那条路上 hash 与图谱的重画必须
  跟着「没关成」一起回退，否则会留下一次「关了但没关」的错位状态。

**「谁拥有状态变化」这条纪律连带修掉两处**（`close()` 变成可被拒绝之后才暴露出来）：`generate()`
的收尾与设置页的「重新研究并生成」此前都是**先自己 `S.nodeId = null`、再调 `close()`**。于是用户
在「别丢我的草稿」上点一次「取消」，就得到一个「图里没有选中节点、右栏还挂着那个节点详情」的错位
状态 —— 草稿虽然还在 `S` 里，但那个节点已经选不回来了，下一次打开它会走 `open()` 里的 `S.note`
归零：**用户点了「取消」，草稿照样丢**。两处都改成只由 `close()` 拥有这个变化：生成那条直接删掉
那行、设置页那条把 `close()` 的返回值当成中止信号（`if (!close()) return;` —— 重新生成会让笔记
从界面上消失，被拒绝时根本不该继续往下跑）。

`views/inspector.js` 里「不得出现任何输入框」那条机械检查相应地从「一个 `<textarea>` 都不许有」改成
「**只许有那一个**」（点名 `id="p-note-text"` 并断言它还在）——`<input>`/`<form>`/`composer`/
`chat-input`/`KP.chat` 照旧一个都不许出现。允许一个点名的例外，而不是把整条检查放宽：放宽之后，
下一个人往右栏塞一个聊天输入框不会被拦住。

### 测试（这一轮的测试有牙，是验过的）

新增：`tests/js/markdown.test.mjs` 六条 `splitNoteSections`（段序 / `###` 子标题不切断 / front-matter
剥掉 / 正文里一句「参见 ## 提纲」不切段 / 空输入 / `pickNoteSections` 就是它的一个过滤）、
`tests/js/views.test.mjs` 十四条（`noteHTML` 四样都在、草稿优先于原文、对抗性夹具无 `<img`、没有
讲解记录时说话；进页/返回；保存成功与 500；`refreshNode` 重绘后草稿还在；返回不丢草稿且入口按钮
报「有未保存的修改」；脏草稿切节点与关详情各问一次且取消就不动；没有 `note_path` 时不出入口；
生成收尾与设置页「重新研究并生成」被草稿确认拒绝时**什么都不动**）、
`tests/test_learning_store.py` 七条 `write_user_notes`、`tests/test_api_learning.py` 七条 `POST /note`
（含「不需要 API key」那条 —— 第一版写成了假绿的，本地 `.env` 有真 key，所以改成把
`get_chat_deps` 覆盖成一个**会记账并抛错**的依赖、断言它一次都没被调用）。

**变异验证**（改坏 → 确认变红 → 改回）：`#rz-insp` 回到 `translateX(-50%)` → 分隔线那条红；
`write_user_notes` 改成整份覆写 → 「讲解记录逐字还在」红；生成卡写回 `stage()` → 「卡片在右栏、
中间没有 `gen-go`」红；草稿搬出 `S`（`captureDraft` 不落盘 + 监听器写进无人读的字段）→ 六条红
（含点名的「聊完一轮重绘之后草稿还在」）；而那两处「先清 `S.nodeId` 再 `close()`」各自只有**一条**
红（正好是对应的那条新用例），说明这两条守卫是**点对点**的、不是被别的断言顺带兜住的。

`node --test tests/js/` —— **354 pass / 0 fail**；`pytest -q` —— **604 passed**。

**仍然欠着的人工验收**（与前面几轮同一个性质，测试覆盖的是纯函数与字符串产物，不是浏览器）：

1. **那条线**：悬停高亮的那条是否**正好**落在两栏分界上，拖一下看是否跟着光标走（≤1000px 那一档
   右栏变浮层、`#rz-insp` 被 `display: none`，别拿那一档当判据）；
2. **生成一个新主题**：卡片在右栏、逐字报告在栏内滚、按钮与计时器都看得见、页面不再被撑破；
3. **笔记**：打开知识点 → 点「讲解记录」→ 返回；改一行 → 保存 → 用编辑器打开
   `data/knowledge/<topic>/01_*.md`，确认**只有** `## 我的笔记` 那一段变了、讲解记录一字未动。

---

## 走查反馈第五轮 —— 保存状态要说实话（2026-09-13）

用户报的是一句话：**「实际上已经是保存了 但是会显示未保存」**。文件确实写好了（`data/knowledge/`
里那份笔记的 mtime 就是那一次保存），屏幕上却挂着「有未保存的修改」、切节点还要多问一句。

**诚实说明:这一句抱怨有两条都能单独成立的来路，我无法从磁盘上的痕迹判定用户撞的是哪一条。**
所以两条都修了，并各自留了一条点对点的用例。顺带在这一段代码里又找出三处「说了假话也不报错」
的地方（其中两处会**删掉用户的内容**）——都在同一屏上，一起改了。

### ① 判「改过没有」比的是**字符**，而写盘与读回各带一次归一化

`captureDraft` 原来做的是 `ta.value === noteDraftValue(...)`，也就是**字符比较**。可这一份文本要走
两次归一化才会变成基准：后端 `write_user_notes` 的 `body.strip("\n")`、前端 `splitNoteSections` 的
`.trim()`。**两次归一化都不在用户输入这一侧** —— 于是「敲的时候末尾顺手带了一个换行」这种最常见的
输入，存完之后永远比不平：下一帧就把它当成本地改动收回来，入口按钮上一直挂着「有未保存的修改」，
而磁盘上早就写好了。数据目录里那份笔记的形态（4 字 / 1 行、段落末尾有换行）与这一条相符。

- 新增 `sameNoteText(a, b)`：两边都 `trim()` 再比 —— **这就是判「改过没有」的判据**，`captureDraft` 用它。
- **已知取舍写进了注释**：只差首尾空白的改动算「没改过」。首尾换行本来就留不住（后端会 strip）、
  首尾空格会被下一次 `trim()` 吃掉，写进去也看不出来；与其让用户面对一个永远清不掉的「未保存」，
  不如认为它们相等。
- 顺手核了一遍前端与后端的**段落判据**是否真的一致：24 份真实节点文件上，`read_user_notes` 与
  `splitNoteSections` 的复刻**零处分歧**（只读脚本，只打印布尔与长度，不打印笔记内容）。

### ② 一次**迟到的**节点刷新把旧基准带了回来（与空白字符无关）

`refreshNode`（聊完一轮）与 `ensureOutline`（补提纲）都会写 `S.node.body`，而两者都只按 `nodeId`
挡一道。只要其中任何一次的响应**在保存之后**才到，它带回的就是保存**之前**的那一段 —— 而
「我的笔记」的基准当时正挂在那座**渲染投影**上。于是下一帧 `captureDraft` 拿文本框里刚存好的字和
旧基准一比，又把草稿收了回来：屏幕上是「已经保存了却显示未保存」，磁盘上早写好了。
**这一条不需要用户多敲任何字符**，只需要一次时序巧合。

修法是把基准从投影上摘下来：`S.note.base`（服务端那一段的基准）只在**两件事**上更新 —— 打开一个
节点、保存成功。`S.node.body` 后来被谁铺成什么都不再影响「改过没有」。`noteDraftValue` 因此成了
三级：草稿 → 基准 → 正文投影（第三级是给纯函数渲染留的，`noteHTML` 的单元测试就靠它）。

### ③ 「清空笔记」也是一次改动 —— 判据必须写 `!= null`

`draft` 用 `null` 表示「没改过」，可两处判「有没有草稿」用的是**真值**（`S.note.draft ? …` /
`if (!KP.S.note.draft) return true;`）。于是用户「全选 + 删除」之后：入口按钮上不显示未保存、
切节点连问都不问 —— 清空笔记的意图被静默丢掉。两处都改成 `!= null`。

### ④ 送出去的内容原来是一个**可能为空**的草稿 —— 两处会删掉用户的内容

`const text = S.note.draft || ''`。这条兜底有两个后果：

1. **「打开讲解记录、看一眼、顺手按一下保存」= 把「我的笔记」整段清空**，而且看起来和成功一模一样
   （后端确实写了一个空段落回来、也回传了正文）；
2. 更要紧的是第二下：存好一次之后 `draft` 又是 `null`，**再按一次保存就把刚存好的那一段抹掉**。

改成 `noteDraftValue(S.node, S.note)`（文本框里这一刻该显示的那一份）。

### ⑤ 「已保存」不能是一句谎话 —— 后端说了什么、读回来是什么，都要看

`/note` 的返回值里 `saved`（笔记文件被移走时是 `false`）与 `body` 原来一个都没看：只把 `body` 铺回
状态就宣布成功。现在「成功」的判据有三层：请求回来了、后端说写了（`saved !== false`）、**回传的正文
里那一段和刚送出去的读回来对得上**（`sameNoteText`）。第三条最要紧 —— 它是「正文里有一行以 `## `
开头」这个陷阱的探针：前后端都把它当成段的结束，于是那之后的字留在文件里、却再也显示不到文本框里。
以前这一次写入会被报成「已保存」，而用户下次打开看到的不是他写的那份。

三种失败的区别是实质性的（请求没到 / 后端说没写 / 写了但读回来对不上），所以 `note.err` 现在装的是
**一整句话**，`noteHTML` 不再统一加「保存失败：」前缀 —— 第三种的正确说法里没有「失败」二字。

**顺带补回 tip 里那句话**：`notes.py` 的 docstring 一直写着「所以前端在编辑框下写了一句『别用 `##`
开新行』」，而实际上没有那一句 —— 那条注释是假话。补的是真话，不是把注释删掉。

### 测试（有牙，逐条验过）

`tests/js/views.test.mjs` 新增六条：迟到的刷新（**夹具是真快照** —— 桩每次给一份新对象，直接把
夹具对象交出去会让 `S.node.body = …` 顺手改掉桩里那一份，测试会**假绿**；第一版就是这么假绿的）、
末尾带换行的保存、没改过就按保存送的是文件里那一份、清空笔记（入口按钮与离开确认**两处都断言**）、
`saved: false`、回读对不上。

**变异验证**（改坏 → 确认变红 → 改回，每条都只剩对应的那几条红）：`sameNoteText` 退回字符比较 → 1；
`draft || ''` → 1；真值判断（入口按钮与 `confirmDiscard` **各改一次**）→ 同一个用例各红一次；
忽略 `res.saved` → 1；跳过回读核对 → 1；`base` 不参与 → 1；保存成功不推进 `base` → 3。

`node --test tests/js/` —— **360 pass / 0 fail**；`pytest -q` —— **604 passed**（后端一行未改）。

**人工验收缺口**（与前面几轮同一个性质）：改一行 —— **末尾多敲一个换行** —— 保存，返回节点页，
入口按钮上**不该**再有「有未保存的修改」；再点进去，文本框里应是刚存的那份。

---

## 走查反馈第六轮 —— 生成不再写报告（2026-09-13）

用户报了两件事，随后自己给出了第三件（也是最好的那件）：

> 1、点击生成知识图谱后 标题会变成未命名主题 应该是直接把查询的主题用上
> 2、为什么第二次生成的知识图谱只剩下一个中心节点了
> 我有一个想法 能不能就是不用生成报告 我只需要有最后的完整知识图谱和每个节点的相应关键词即可
> 因为目前设置的在每个知识点节点相应知识点学习时是会在首次学习是花费一定时间生成提纲的

**第二件事已在本机复现并定位，第一件是一行取错了数据源。** 两条都修了，但真正把整类故障消掉的是第
三件事：用户的判断是对的 —— 报告是中间产物（学的是节点，前端只有一个不可点击的文件名 chip），
而它偏偏是全流程里最容易「花钱但交白卷」的一步。这一轮把它从**学习侧的链路上删掉**，
而不是把它的预算调大。

### ② 为什么第二次只剩一个中心节点（实测数据）

`DEEPSEEK_MODEL=deepseek-flash` 是**推理模型**：`reasoning_content` 与正文**共用 `max_tokens`**，
而 `stream_complete` 只透出 `delta.content`。生产同形 prompt + 生产同参实测：

| max_tokens | 推理增量 | 正文 | finish_reason |
|---|---|---|---|
| 64 | 64 | **0 字** | `length` |
| 4096（生产参数） | 3175 | 1665 字 | `length` |
| 8192 | 2088 | 959 字 | `stop` |

正文写在推理**之后**，推理一旦吃满预算就一个字都不剩，而 API 把这种结果当**成功响应**。
故障链：空报告 → `DoneEvent(content="")` → 建图降到第三级兜底（当时是「拿主题名造一个单节点图」）
→ `replace_graph` + `status=ready`，**全程零异常**。用户看到的「只剩一个中心节点」就是这个单节点兜底。
前几次「能生成」也是被截断的：第一次报告 4003 字**结尾断在半句**（计划 4 节只写到第 2 节），
chunk 那次 1452 字断在表格行中间 —— 只是那两次**断的地方还有字**，所以看起来像成功。

顺带查到的同类隐患：按需生成的讲解（`session.py`）`max_tokens=2048` 比实测推理开销还短，
空正文原来照常落库 + 追加进用户 Markdown → 界面一条空气泡、文件里一条空标题的「讲解记录」。
**建议一并修**的那一项这轮也修了（见下）。

### ① 标题变成「未命名主题」

`graph.js` 重画生成卡时从**整图**里反推主题（`S.graph.topic`）。而新建主题后 `onNewTopic` 是拿
接口返回的那条记录直接画卡的，**没走过 `loadTopic`** → `S.graph` 是 `null` → 展开成 `{}` →
`title` 为 `undefined` → 卡片与中间列的 `|| '未命名主题'` 兜底生效。更坏的一种：`S.graph` 还留着
**上一个主题**的图时，卡上会写上一个主题的名字与研究问题。

新增纯函数 `cardTopic(topics, topicId, graph, status)`：列表行（按 id 查，天然属于当前主题）打底，
**只有 `graph.topic.id === topicId` 时**才用整图那条补 `error` / 更完整的 `progress`。

### ③ 生成流程：把「写报告」换成「抽知识点」

- `agent/events.py` 新增 `NodesEvent(nodes, summary)`；`api/sse.py` 加一行映射（**只报个数**，
  不把节点塞进 SSE —— 与 `GraphReadyEvent` 同一规矩；漏了这个映射会在流内抛 `TypeError` 而整次生成失败）。
- `agent/graph.py` 新增 `extract_node`：研究问题 + 研究计划 + 已收集资料 → 同一份
  `LEARNING_PATH_PROMPT` → `json_object` 一次拿到节点与一句话结论；**不流式**（JSON 吐给用户没意义）、
  **不抛异常**（抽不到就交白卷，由调用方降级）。`_build_app(extract_prompt=...)` 是**参数而非布尔**：
  非 None 既选中 `extract` 终端节点、又提供它的 system prompt，`None` 则与今天逐字节相同
  —— `/api/chat`、评测、约 40 条既有 graph 断言一行都没改。输出预算 8192（比 synthesize 的 4096 宽）。
- `learning/path.py` 新增 `nodes_from_plan`：计划是这条路上**一定存在**的东西（planner 先跑），
  所以它取代「从报告标题降级」成为降级链第二级。`key_points` 刻意留空 —— 计划里的句子是**研究问题**，
  不是关键词，塞进芯片就是编造。
- `learning/service.py` 新增 `build_graph_from_material`（纯函数、零 LLM）：
  抽到的节点 → `build_learning_graph`；没有 → 计划拼线性路径；计划也空 → **空图 + 原因**。
  **第三级刻意不给「单节点兜底」** —— 那正是用户报的故障的样子。
- `learning/notes.py` 新增 `render_outline`（纯函数）：把**最终图谱**渲染成 `report.md` 的正文
  （`## N. 名字` + 一句话 + `关键词：…` + `前置：…`）。于是「文件里有什么」重新变成**确定性函数**，
  不再取决于那一次调用有没有被推理吃光。
- `api/learning.py`：graph 模式记下 `PlanEvent.plan` / `NodesEvent.nodes,summary` → 建图 →
  大纲作为**一帧** `TokenEvent` 发给前端（`#gen-body` 仍有内容可看，但不再是逐字长文）→
  `persist_graph(report=大纲)` → 主题 `summary` 用那句结论。**闸门**：图建不出来就 `raise`，
  由既有的 `except` 落 `failed` + `error` 帧，`persist_graph` 一次都不调用 —— **上一次的好图原样留着**。
- `memory/store.py::search` 的召回文本纳入 **plan 与 evidence**；`memory/context.py::_run_head`
  报告为空时用计划标题。不修的话，报告变短之后「历史研究背景」会退化成几乎只剩查询字符串。
- 前端文案：`#gen-body` 里的「逐字报告」→「图谱大纲」，`inspector.js` 的 chip「整篇研究报告」→「图谱大纲」
  （文件名仍是 `report.md`，`report_path` 列名不动）。

### 代价（不假装没有）

- **生成期不再有逐字正文**：改成研究日志 + 结束时一次性出现的图谱大纲（**少一次长文调用，生成更快**）。
- `report.md` 从「研究报告」变成「图谱大纲（含每节点关键词）」；`outline.py::_excerpt` 拿它给按需提纲
  当上下文 —— 内容变了，但提纲生成仍以节点自身数据为主，功能未变。
- **节点命名质量要用真实主题复验**：抽取输入从「成稿报告」换成「计划 + 证据片段」。降级链保证最差也落在
  计划的 2–4 条上（不再是一个点的图），但**「抽得差」和「抽不到」是两件事，后者这次修了，前者只能靠实机看**。
- 不在本轮：彻底删掉 `report.md` 与 `#gen-body`；chat 侧空回答（**看得见**的失败，用户至少知道没回答）
  只补了 session 侧那道闸门。

### 测试（有牙，逐条验过）

`pytest -q` —— **648 passed**（第五轮基线 604）；`node --test tests/js/` —— **366 pass / 0 fail**（基线 360）。

新增/改写：`tests/test_agent_extract.py`（新，8 条：收尾换成抽取、注入的 prompt 与 8192 预算、
输入含计划与证据、无证据时写「暂无」、垃圾回复交白卷、非 dict 节点被滤掉、**抽取那次抛错也被吃掉**、
`extract_prompt=None` 时仍是 `synthesize`）；`tests/test_learning_service.py`（新，11 条：两级降级 + 交白卷 +
loop 模式空报告；`build_learning_graph` 遇到 `None`/字符串**不抛异常**这条是测试先炸出来的真 bug）；
`test_learning_store.py` + 5 条 `render_outline`；`test_learning_path.py` + 7 条 `nodes_from_plan`；
`test_memory_store.py` + 4 条（plan/evidence 参与召回、匹配文本上限、`_run_head` 计划兜底）；
`test_learning_session.py` + 4 条（空讲解抛错且不落库/不落文件/不烧判定、预算 4096）；
`test_api_learning.py` 改写生成流断言（`nodes` 帧、**恰好一帧** `token` = 大纲、`llm.calls == 0`、
按计划降级、闸门保旧图、loop 模式不变）+ 空讲解 → `error` 帧；JS + 5 条 `cardTopic`/端到端卡片标题，
+ 1 条 `token` 帧 → `#gen-body`（**图谱模式下右栏那一屏只有这一帧**，丢了没有任何东西兜底，
所以单独钉住；配套变异：把 `token` 分支改成 `return`（不累加）→ 1 红）。

**变异验证**（改坏 → 确认变红 → 改回，每条都只剩对应的那几条红）：`cardTopic` 退回只读 `S.graph.topic`
→ 4 红；`extract_prompt` 被忽略 → 7 红；拿掉闸门 → 1 红；降级链去掉 `nodes_from_plan` → 3 红；
召回不纳入 plan/evidence → 3 红；拿掉空讲解闸门 → 4 红。

**人工验收缺口**（与前几轮同一个性质，需要浏览器 + 真实模型）：

1. 新建主题 → 点「开始生成」——**标题与研究问题当场仍是刚敲的那个名字**；
2. 生成更快，日志照常滚动，结束时右栏出现「图谱大纲」（每个知识点带关键词），中间出现多节点图谱；
   左栏日志里**没有**降级提示；
3. `data/knowledge/<主题>/report.md` 存在 = 图谱大纲；打开任一节点，**要点（关键词）与提纲都在**；
4. 断网重跑 → 主题落 `failed` + 「重试」，**上一次的图谱还在**；
5. 老的那张一点图：点「重新研究并生成」→ 换成多节点图谱；
6. **新增要看的一件事**：抽取输入变了（计划 + 证据片段），节点命名质量是否还够好
   —— 这是本轮唯一「改好了失败模式、但可能改差了一样东西」的地方。
