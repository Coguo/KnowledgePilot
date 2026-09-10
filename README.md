# KnowledgePilot — AI Research Agent

针对用户提出的开放性研究问题，自主完成 **任务拆解 → 资料搜索 → 网页抓取 → 知识库构建（RAG）→ 检索 → 分析 → 生成带引用报告** 的全流程研究 Agent。

**当前进度**：
- ✅ **Phase 0：基础 Research Chat**（`用户 → LLM → 搜索 → 流式答案`）
- ✅ **Phase 1：RAG**（搜索网页动态建库 → 本地 BGE-M3 Embedding → Chroma 向量检索 → 带来源引用作答）
- ✅ **Phase 2：RAG 优化**（Recursive Chunk / BM25+向量 Hybrid / Reranker / Query Rewrite / 离线评测矩阵）
- ✅ **Phase 3：LangGraph Agent 编排**（Planner 拆解 → 多轮研究 → 评估充分性 → 带引用报告；`AGENT_MODE=graph|loop`）
- ✅ **Phase 4：Memory**（研究历史落库 → 同类问题自动召回复用；图 checkpoint 磁盘持久化 SqliteSaver；`MEMORY_ENABLED=true`）
- ✅ **Phase 5：Knowledge Graph**（LLM 抽取实体/关系 → 手写内存图存储 → 关键词匹配 + BFS 检索 → 注入报告；`KG_ENABLED=true`）
- ✅ **Phase 6：MCP**（官方 mcp SDK client + FastMCP stdio server：`search_memory` / `recent_research` / `search_papers` 三个 LLM 可主动调用的只读研究辅助工具；`MCP_ENABLED=true`，仅 graph 模式）
- ✅ **Phase 7：Agent Evaluation**（图级/全栈端到端评测：五档变体离线确定性指标 + `--real` DeepSeek LLM judge；`python -m knowledge_pilot.agent.eval`）
- ⬜ Phase 8+：工程化（MCP 进程复用 / Redis / PostgreSQL / Model Gateway / Docker）

完整规划见 `AI_Research_Agent_Project_Context.md`（已 gitignore，本地保留）。

## 架构

```
knowledge_pilot/
├── config.py      # 配置（环境变量 / .env，不硬编码密钥）
├── llm/           # LLM 客户端封装（openai SDK，默认 DeepSeek，流式 + 工具调用）
├── search/        # 搜索抽象层（stub 占位 / tavily 真实搜索，可插拔）
├── agent/         # 核心：LangGraph 编排（Planner→Research→Evaluate→Synthesis，
│   │              #   Phase 3）+ 手写 tool-calling 循环（Research 节点复用），事件流（UI 无关）
│   └── eval/      #   Agent Evaluation（Phase 7）：五档变体图级评测（离线确定性 +
│                  #      --real DeepSeek judge）+ 数据集/指标/CLI
├── memory/        # Memory（Phase 4）：研究历史 SQLite 落库 + 关键词召回复用（纯 stdlib）
├── kg/            # Knowledge Graph（Phase 5）：LLM 抽取实体/关系 → 手写 dict 邻接图 →
│   │              #   关键词匹配 + BFS 检索 → 注入报告（纯 stdlib，零依赖）
├── mcp/           # MCP（Phase 6）：官方 mcp SDK client（网关）+ FastMCP stdio server
│   │              #   （memory / papers 只读辅助工具），结果经 notes 进报告
│   └── servers/   #   stdio 子进程 server：memory / papers（FastMCP，LLM 可主动调用）
├── rag/           # RAG：抓取→分块→Embedding→向量库→检索；Phase 2 叠加
│   │              #   RecursiveChunker / BM25 Hybrid(RRF) / Reranker / QueryRewrite
│   └── eval/      #   离线评测（Recall@K/MRR/Latency/TokenCost 矩阵 + CLI）
├── api/           # FastAPI 层：把事件流映射为 SSE
└── web/           # 前端页面（单个 index.html）
```

> 设计要点：**Agent 引擎与 UI 完全解耦**——它只产出事件流。网页版把事件映射为 SSE；未来做桌面版只需新增一个前端消费同一接口，不返工。RAG 通过 `search_web` 工具内部透明增强接入（自动抓取搜索结果建库并检索），LLM 无需学习新工具，事件协议不变。Phase 2 的 Hybrid / Reranker / Rewrite 均为可插拔组件（Protocol 接缝 + 配置开关），全部默认按评测推荐组合开启。Phase 3 引入 **LangGraph 编排**（`AGENT_MODE=graph`）：Planner 拆解研究问题 → Research 节点复用现有 Agentic 工具循环并采集证据 → Evaluate 判定充分性（不足则条件循环再研究）→ Synthesis 综合带引用报告；`AGENT_MODE=loop` 可切回 Phase 2 的单轮循环做对比。Phase 4 加入 **Memory**（`MEMORY_ENABLED=true`）：研究完成后把 query/plan/report/来源 落库到 SQLite，下次同类问题自动按关键词召回历史注入规划流程（前端显示「🧠 找到 N 条历史研究记录」），图 checkpoint 从 MemorySaver 升级为 SqliteSaver 跨重启持久化。Phase 5 加入 **Knowledge Graph**（`KG_ENABLED=true`，仅 graph 模式）：研究结束后用 LLM 从证据抽取实体/关系，构建**本次任务的内存知识图谱**（纯 stdlib 手写 dict 邻接，零新依赖），按研究问题关键词匹配实体 + BFS 展开子图，把命中三元组作为「相关实体关系」块注入综合报告（前端显示「🕸️ 知识图谱：N 实体 / M 关系」）；RAG 给原始文本证据、KG 给结构化关系信息，两者并存。Phase 6 加入 **MCP**（`MCP_ENABLED=true`，仅 graph 模式，官方 `mcp` SDK 随 base 安装）：Agent 作为 mcp client，经 stdio 子进程连接 FastMCP server，把「结果本质是文本」的研究辅助能力变成 LLM 可主动调用的真实工具——`search_memory` / `recent_research`（只读历史库）+ `search_papers`（arXiv）；`search_web` 保持原生进程内工具（其证据采集 + RAG 增强需结构化结果，见 docs/phase-6.md）。MCP 自由文本结果经**工具边界 notes 累加器**去重截断，作为「工具补充资料（MCP）」块渲染进最终报告，**不进** evidence/来源列表/KG（语义红线）。Phase 7 加入 **Agent Evaluation**（规格 §13，`python -m knowledge_pilot.agent.eval`）：五档架构变体（loop / graph / graph+memory / graph+kg / all）端到端评测——离线用确定性 rubric judge + 脚本化扰动量化**机制与系统指标**（工具轨迹/调用次数/error_rate/memory·kg 事件/延迟·token），`--real` 用真实 DeepSeek + LLM judge 判定**质量**（报告是否真的成功）；回答「图 + 记忆 + KG + MCP 相比单轮手写循环是否真的更好」。

## 快速开始

前置：conda 环境 `knowledgepilot`（Python 3.11）已建好。

```bash
conda activate knowledgepilot
cd d:\Code\Project\Python\KnowledgePilot
pip install -e ".[dev]"          # 基础（Phase 0）
pip install -e ".[dev,rag]"      # 含 RAG（Phase 1/2，可选，体积较大）
```

### 配置

```bash
copy .env.example .env     # Windows
# 编辑 .env：
#   DEEPSEEK_API_KEY    ← LLM 密钥（必填）
#   TAVILY_API_KEY      ← 搜索密钥（SEARCH_PROVIDER=tavily 时必填）
#   AGENT_MODE=graph    ← graph（LangGraph 编排，默认）/ loop（旧单轮循环）
#   AGENT_MAX_ITERATIONS=3  ← 研究-评估循环上限
#   MEMORY_ENABLED=true  ← 开启 Memory（Phase 4：研究历史落库 + 同类问题复用 + checkpoint 持久化）
#   KG_ENABLED=true      ← 开启 Knowledge Graph（Phase 5：抽实体关系建图，注入报告；仅 graph 模式生效）
#   KG_HOPS=2            ← 图谱 BFS 展开层数
#   MCP_ENABLED=true     ← 开启 MCP（Phase 6：search_memory/recent_research/search_papers 研究辅助工具，仅 graph 模式）
#   RAG_ENABLED=true     ← 开启 RAG（需先安装 [rag] 依赖；首次会下载约 2GB 的 BGE-M3 模型）
#   EMBEDDING_CACHE_DIR=data/models   ← 模型缓存目录
#   RAG_CHUNK_STRATEGY=recursive  RAG_HYBRID_ENABLED=true  RAG_RERANK_ENABLED=true
#   RAG_QUERY_REWRITE_ENABLED=false   ← Phase 2 默认组合（详见 .env.example）
#   国内下载模型慢可设置系统环境变量 HF_ENDPOINT=https://hf-mirror.com
```

未配置密钥时也可以启动（界面能打开），但发消息会得到清晰提示。

### 运行

```bash
uvicorn knowledge_pilot.api.main:app --reload
```

浏览器打开 <http://127.0.0.1:8000>，输入研究问题（例如"RAG 的 chunking 策略有哪些"），可看到流式回答、工具调用状态与带来源引用的检索结果。

> 说明：应用服务、本地 BGE-M3、Chroma 向量库都在本地运行；LLM 对话（DeepSeek API）、真实搜索（Tavily）、网页抓取依赖网络。

### 测试

```bash
pytest
```

**旧测试 + Phase 3/4 graph 测试 + Phase 5 kg 测试 + Phase 6 mcp 测试**，全部离线运行（Fake LLM / Fake Embedding / 内存向量库 / tmp SQLite 注入，不联网；MCP 的 convert/arxiv/config 纯 stdlib 即跑，真实 stdio 子进程测试只读本地库不联网）。未安装 `[rag]` 依赖时，rank-bm25 / chromadb / trafilatura 相关测试自动跳过（`pytest.importorskip`）；MCP 需 `mcp`（随 base 安装），未装时 `test_mcp_servers*.py` 整模块跳过。首次运行前先 `pip install -e ".[dev,rag]"` 安装 langgraph + langgraph-checkpoint-sqlite + mcp（Phase 3/4/6 依赖，base dependencies），graph/api/kg-graph/mcp-stdio 集成测试方可执行。测试矩阵详见 `docs/phase-6.md`。

### 离线评测（Phase 2）

```bash
python -m knowledge_pilot.rag.eval --dataset tests/fixtures/eval/small.json --top-k 3
```

输出 16 行 Spec 矩阵（chunk × retrieval × rerank × rewrite 全组合）的 Recall@K / MRR / Latency / Token Cost，量化每个优化轴的收益。加 `--real` 换真实模型（BGE-M3 / bge-reranker / DeepSeek 改写）测量真实数据。详见 `docs/phase-2.md`。

### Agent Evaluation（Phase 7）

```bash
python -m knowledge_pilot.agent.eval --dataset tests/fixtures/eval_agent/small.json   # 离线五档（零 key 可复现）
python -m knowledge_pilot.agent.eval --dataset ... --variants loop,graph+memory,all     # 只跑某几档
python -m knowledge_pilot.agent.eval --dataset ... --real                              # 真实 DeepSeek + LLM judge（需 .env key）
```

五档变体（loop / graph / graph+memory / graph+kg / all）跑带金标准的端到端研究任务，聚合 Task Success / 工具选择·参数精度 / error rate / 报告覆盖 / Latency P50·P95 / 调用次数 / token 成本（memory·kg 事件另计）。离线 LLM 是脚本化的 → 区分**机制与系统指标**；质量与「哪个变体更好」交给 `--real`（DeepSeek judge 判逐条质量，rubric 判不出的相关性/引用合理性）。详见 `docs/phase-7.md`。

## 阶段规划（渐进而来）

- ✅ **Phase 0**：基础 Research Chat + Tavily 搜索
- ✅ **Phase 1**：RAG（动态抓取网页 → 分块 → Embedding → 向量检索 → 带来源引用）
- ✅ **Phase 2**：RAG 优化（Recursive Chunk / Hybrid / Reranker / Query Rewrite / Evaluation）
- ✅ **Phase 3**：LangGraph Agent 编排（Planner → 多轮研究 → 评估充分性 → 带引用报告）
- ✅ **Phase 4**：Memory（研究历史落库复用 + Checkpoint 持久化）
- ✅ **Phase 5**：Knowledge Graph（实体关系抽取 → 内存图 → 关键词+BFS 检索 → 注入报告）
- ✅ **Phase 6**：MCP（扩真实工具再包 MCP：官方 mcp SDK client + FastMCP stdio server，Memory/Papers 研究辅助工具）
- ✅ **Phase 7**：Agent Evaluation（图级/全栈离线评测：五档变体机制+系统指标；`--real` LLM judge 判质量）
- ⬜ Phase 8：工程化（MCP 进程复用 / Redis / PostgreSQL / Model Gateway / Docker）

## 阶段文档

每阶段的实现说明见 `docs/phase-*.md`（已 gitignore，本地保留）；公开工作日志见 `LOG.md`。
