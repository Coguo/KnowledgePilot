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
- ✅ **Phase 8：工程化**（MCP server 进程常驻复用——消掉每个 SSE 请求 0.3–0.5s 的子进程握手；Model Gateway——落在 Agent 与 provider 之间的统一访问层：多 provider / Timeout / Retry / Fallback / 流式 usage 日志；**默认全关，关闭路径与 Phase 7 逐字节一致**）
- ✅ **Phase 9：学习图谱**（从「研究报告生成器」转向**持久化学习伙伴**：研究 → 生成学习路径知识图谱 → 逐点讲解 → 系统推荐点亮 + 用户确认；图谱结构与学习状态落 SQLite、每个知识点的讲解正文另存 Markdown；研究过程改为**逐字流式**，错误发 `error` 帧而非静默挂起；`/` 换成图谱主导的新主页且**默认开**——它是产品门面，不该由后端开关决定画不画）
- ✅ **Phase 9 第六轮：生成不再写报告**（学习侧收尾从「写长报告再从报告里抽知识点」换成**直接从资料抽**
  （`extract_node`）：报告只是中间产物，而它是最贵、最容易被推理模型的 `reasoning_content` 吃光预算的一步
  ——预算吃光时正文是空字符串且不报错，下游表现为「一张只有一个点的图」。`report.md` 于是改成装**图谱大纲**
  （每节点：名字 + 一句话 + 关键词，纯函数渲染、零 LLM）；图建不出来时**交白卷**落 `failed`，
  不再拿一张单节点图假装成功。`/api/chat` 与 `AGENT_MODE=loop` 仍写报告，逐字节未变）
- ⬜ Phase 10：Docker（容器化，顺延）

完整规划见 `AI_Research_Agent_Project_Context.md`（已 gitignore，本地保留）。

## 架构

```
knowledge_pilot/
├── config.py      # 配置（环境变量 / .env，不硬编码密钥）
├── llm/           # LLM 客户端封装（openai SDK，默认 DeepSeek，流式 + 工具调用）
│   │              #   Phase 8：protocol.py（StreamChunk/LLMClient，零 SDK 依赖）
│   │              #   errors.py（错误分类）/ providers.py / gateway.py（Model Gateway）
│   │              #   Phase 9：streaming.py（能力探测式流式：stream_capable/stream_text）
├── search/        # 搜索抽象层（stub 占位 / tavily 真实搜索，可插拔）
├── agent/         # 核心：LangGraph 编排（Planner→Research→Evaluate→Synthesis，
│   │              #   Phase 3）+ 手写 tool-calling 循环（Research 节点复用），事件流（UI 无关）
│   └── eval/      #   Agent Evaluation（Phase 7）：五档变体图级评测（离线确定性 +
│                  #      --real DeepSeek judge）+ 数据集/指标/CLI
├── memory/        # Memory（Phase 4）：研究历史 SQLite 落库 + 关键词召回复用（纯 stdlib）
├── learning/      # 学习图谱（Phase 9）：store（SQLite 5 表）/ notes（Markdown 原子写）/
│   │              #   path（确定性建图：拓扑排序+破环+分层，永不阻断的降级链）/
│   │              #   session（逐点讲解流式 + 点亮判定）/ service（编排，不依赖 FastAPI）
├── kg/            # Knowledge Graph（Phase 5）：LLM 抽取实体/关系 → 手写 dict 邻接图 →
│   │              #   关键词匹配 + BFS 检索 → 注入报告（纯 stdlib，零依赖）
├── mcp/           # MCP（Phase 6）：官方 mcp SDK client（网关）+ FastMCP stdio server
│   │              #   （memory / papers 只读辅助工具），结果经 notes 进报告
│   │              #   Phase 8：runtime.py = 进程级常驻单例（按 specs 指纹复用/补连）
│   └── servers/   #   stdio 子进程 server：memory / papers（FastMCP，LLM 可主动调用）
├── rag/           # RAG：抓取→分块→Embedding→向量库→检索；Phase 2 叠加
│   │              #   RecursiveChunker / BM25 Hybrid(RRF) / Reranker / QueryRewrite
│   └── eval/      #   离线评测（Recall@K/MRR/Latency/TokenCost 矩阵 + CLI）
├── api/           # FastAPI 层：把事件流映射为 SSE
│   │              #   Phase 9：deps.py（依赖装配）/ sse.py（帧编码 + 错误脱敏）
│   │              #            learning.py（/api/learning/* 9 条路由）
└── web/           # 前端页面（单个 index.html，零依赖离线可跑）
                   #   Phase 9：图谱主导的新主页（手写 SVG 分层布局 + 流式对话 + 点亮三态）
```

> 设计要点：**Agent 引擎与 UI 完全解耦**——它只产出事件流。网页版把事件映射为 SSE；未来做桌面版只需新增一个前端消费同一接口，不返工。RAG 通过 `search_web` 工具内部透明增强接入（自动抓取搜索结果建库并检索），LLM 无需学习新工具，事件协议不变。Phase 2 的 Hybrid / Reranker / Rewrite 均为可插拔组件（Protocol 接缝 + 配置开关），全部默认按评测推荐组合开启。Phase 3 引入 **LangGraph 编排**（`AGENT_MODE=graph`）：Planner 拆解研究问题 → Research 节点复用现有 Agentic 工具循环并采集证据 → Evaluate 判定充分性（不足则条件循环再研究）→ Synthesis 综合带引用报告；`AGENT_MODE=loop` 可切回 Phase 2 的单轮循环做对比。Phase 4 加入 **Memory**（`MEMORY_ENABLED=true`）：研究完成后把 query/plan/report/来源 落库到 SQLite，下次同类问题自动按关键词召回历史注入规划流程（前端显示「🧠 找到 N 条历史研究记录」），图 checkpoint 从 MemorySaver 升级为 SqliteSaver 跨重启持久化。Phase 5 加入 **Knowledge Graph**（`KG_ENABLED=true`，仅 graph 模式）：研究结束后用 LLM 从证据抽取实体/关系，构建**本次任务的内存知识图谱**（纯 stdlib 手写 dict 邻接，零新依赖），按研究问题关键词匹配实体 + BFS 展开子图，把命中三元组作为「相关实体关系」块注入综合报告（前端显示「🕸️ 知识图谱：N 实体 / M 关系」）；RAG 给原始文本证据、KG 给结构化关系信息，两者并存。Phase 6 加入 **MCP**（`MCP_ENABLED=true`，仅 graph 模式，官方 `mcp` SDK 随 base 安装）：Agent 作为 mcp client，经 stdio 子进程连接 FastMCP server，把「结果本质是文本」的研究辅助能力变成 LLM 可主动调用的真实工具——`search_memory` / `recent_research`（只读历史库）+ `search_papers`（arXiv）；`search_web` 保持原生进程内工具（其证据采集 + RAG 增强需结构化结果，见 docs/phase-6.md）。MCP 自由文本结果经**工具边界 notes 累加器**去重截断，作为「工具补充资料（MCP）」块渲染进最终报告，**不进** evidence/来源列表/KG（语义红线）。Phase 7 加入 **Agent Evaluation**（规格 §13，`python -m knowledge_pilot.agent.eval`）：五档架构变体（loop / graph / graph+memory / graph+kg / all）端到端评测——离线用确定性 rubric judge + 脚本化扰动量化**机制与系统指标**（工具轨迹/调用次数/error_rate/memory·kg 事件/延迟·token），`--real` 用真实 DeepSeek + LLM judge 判定**质量**（报告是否真的成功）；回答「图 + 记忆 + KG + MCP 相比单轮手写循环是否真的更好」。Phase 8 做**工程化**（两个子项，均已交付）：① **MCP 进程复用**——`knowledge_pilot/mcp/runtime.py` 进程级单例按 specs 指纹缓存常驻网关，把 Phase 6「每个 SSE 请求 spawn memory+papers 两个 stdio 解释器」改为跨请求复用，`api/main.py` 的 `lifespan` 只负责退出时关闭；网关内部改为**专用 serving task** 模型（长驻 task 独占连接 enter/exit，调用方经 `asyncio.Queue` + Future 提交），因为 `mcp` 的 stdio client 用 anyio task group，连接生命周期必须与请求 task 解耦、且**严格 LIFO** 关闭（详见 docs/phase-8.md）。② **Model Gateway**——`knowledge_pilot/llm/gateway.py` 实现 `LLMClient` Protocol，调用方（graph/loop/rag/rewrite/judge）零改动；提供 DeepSeek/Qwen/OpenAI 兼容多 provider、仅对瞬时错生效的指数退避重试、耗尽后 fallback、流式仅首字节前重试、usage 日志；`llm/errors.py` 做零 SDK 依赖的错误分类。**默认全关**（`LLM_RETRY_ENABLED=false` 等），生产构造点只换 `ChatClient(settings)` → `build_llm_client(settings)` 一行，未开启时逐字节等价于 Phase 7。Phase 9 把产物从「一份报告」改成「一张可持久化的学习图谱」——这是本项目**唯一一个刻意默认开的开关**（`LEARNING_ENABLED=true`，因为 `/` 就是这个界面；其余 RAG/Memory/KG/MCP 与 Phase 8 的网关旋钮仍全部默认关）。「默认开」不等于「会花钱」：GET 端点零 LLM 调用，不配 key 也能浏览已有图谱。主线是：建主题 → 研究（报告**逐字流式**到达）→ 用 LLM 抽出知识点与前置关系，交给 `build_learning_graph` 做**确定性清洗**（归一化 / 同名合并 / 丢自环与未知前置 / Kahn 拓扑排序 + 破环 / 迭代算 depth / order 取拓扑序）→ 前端按 `depth` 分列渲染手写 SVG 分层图 → 点节点展开讲解对话（同样流式）→ 助手回合结束后由 `judge_coverage` 判定「讲透了吗」，达标则发 `recommend` 帧推给用户，**用户点确认才置为已掌握**（`unlearned → recommended → mastered`，白名单校验、永不自动降级、解析失败一律不推荐）。结构与状态落 `data/learning.db`（stdlib sqlite3，5 表），每个知识点的讲解正文另存 `data/knowledge/<主题>/<序号>_<slug>.md`（front-matter + 要点 + 只追加的讲解记录 + 程序永不触碰的用户区），重生成按名字复用 node id / 状态 / 文件。**「重启后不重新检索」是靠 API 读写分离实现的**：只有 create / generate / chat / mastery 可能触发 LLM，**GET 一律零 LLM、不需要 API key**（实测把 LLM 上游整个杀掉，所有 GET 仍 200 且数据完整）。流式改造用的是**能力探测式降级**（`getattr(llm, "stream_complete", None)`）——生产客户端流式、测试 Fake 与非流式的 `CountingChatClient` 自动落回 `complete`，因此约 40 条既有断言与真实评测的调用计数口径都不受影响；不变量 `DoneEvent.content == "".join(TokenEvent.content)` 由构造保证。`event_stream` 补发 `error` 帧（脱敏 `sk-*`）——此前异常直接穿出生成器，客户端拿到 200 + 截断 body，前端一个错误都不显示。轮数上限本阶段**数值不变**，只把硬编码常量配置化并加了请求级覆盖（`AGENT_MAX_TOOL_ROUNDS` / `AGENT_ROUNDS_HARD_CAP`，`clamp_rounds` 只钳请求值）。

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
#   ---- Phase 8（全部可选，默认关；不改则与 Phase 7 行为逐字节一致）----
#   LLM_TIMEOUT=30               ← 单次调用超时秒数（不设则用 openai SDK 默认）
#   LLM_RETRY_ENABLED=true       ← 仅对瞬时错（连接/超时/429/5xx）退避重试
#   LLM_RETRY_MAX_ATTEMPTS=3     LLM_RETRY_BACKOFF_SECONDS=1.0
#   LLM_FALLBACK_ENABLED=true    ← 主 provider 瞬时错耗尽后切到备用
#   LLM_EXTRA_PROVIDERS=[{"name":"qwen","model":"qwen-plus","base_url":"https://dashscope.aliyuncs.com/compatible-mode/v1","api_key":"sk-..."}]
#   LLM_LOG_USAGE=true           ← 把每次调用的 token 用量打到 stderr
#   ---- Phase 9（学习图谱；**默认开**——`/` 就是这个界面）----
#   LEARNING_ENABLED=true        ← 默认 true。设为 false 退回纯研究聊天，此时 /api/learning/* 统一 503
#   LEARNING_DB_PATH=./data/learning.db        ← 图谱结构与学习状态
#   LEARNING_NOTES_DIR=./data/knowledge        ← 每个知识点的讲解正文（Markdown）
#   LEARNING_MAX_NODES=12        ← 单主题知识点上限（超出截断，防大图糊成一团）
#   LEARNING_RECOMMEND_ENABLED=true   ← 讲解后让 LLM 判定「可以点亮了吗」
#   LEARNING_RECOMMEND_MIN_TURNS=1    ← 至少聊够几轮才判定（省掉没必要的调用）
#   LEARNING_MASTERY_CONFIDENCE=0.7   ← 推荐点亮的置信度阈值
#   LEARNING_AUTO_EXPLAIN=false  ← 打开节点是否自动开讲；默认否 = 纯读已存正文
#   ---- Phase 9（轮数：数值与 Phase 8 相同，只是不再硬编码）----
#   AGENT_MAX_TOOL_ROUNDS=4      ← 工具轮次上限（请求体 max_tool_rounds 可覆盖）
#   AGENT_ROUNDS_HARD_CAP=10     ← 请求级覆盖的硬上限
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

浏览器打开 <http://127.0.0.1:8000>。

**默认就是图谱主导的学习主页**（`LEARNING_ENABLED=true`）：左侧新建/切换主题；主区看研究过程与**逐字流出**的报告，随后渲染学习路径知识图谱；点节点展开要点与讲解对话；讲解达标后出现「确认点亮」按钮。**刷新后状态与对话完整恢复**（DevTools 网络面板里只有 GET，没有任何重新检索）。

> 不想用学习图谱时，`.env` 里设 `LEARNING_ENABLED=false` 即可——`/api/learning/*` 统一 503，页面显示启用指引。`/api/chat` 研究聊天端点在任何情况下都保留可用（引擎通用入口，例如"RAG 的 chunking 策略有哪些"）。

> 说明：应用服务、本地 BGE-M3、Chroma 向量库都在本地运行；LLM 对话（DeepSeek API）、真实搜索（Tavily）、网页抓取依赖网络。<br>
> 学习图谱的正文默认落在 gitignored 的 `data/knowledge/`。若想把学习资料纳入版本管理，`LEARNING_NOTES_DIR` 指到仓库外，或单独反忽略 `data/knowledge/`（`.env.example` 里两种做法都写了）。

### 测试

```bash
pytest
```

**旧测试 + Phase 3/4 graph 测试 + Phase 5 kg 测试 + Phase 6 mcp 测试 + Phase 7/8 评测与工程化测试 + Phase 9 学习图谱测试**（当前 **523 passed**），全部离线运行（Fake LLM / Fake Embedding / 内存向量库 / tmp SQLite 注入，不联网；MCP 的 convert/arxiv/config 纯 stdlib 即跑，真实 stdio 子进程测试只读本地库不联网；Phase 8 网关生命周期测试注入假 connector，**不 import mcp**）。未安装 `[rag]` 依赖时，rank-bm25 / chromadb / trafilatura 相关测试自动跳过（`pytest.importorskip`）；MCP 需 `mcp`（随 base 安装），未装时 `test_mcp_servers*.py` 整模块跳过。首次运行前先 `pip install -e ".[dev,rag]"` 安装 langgraph + langgraph-checkpoint-sqlite + mcp（Phase 3/4/6 依赖，base dependencies），graph/api/kg-graph/mcp-stdio 集成测试方可执行。测试矩阵详见 `docs/phase-6.md`。

Phase 9 的学习图谱测试全离线、零 key：`test_learning_path.py`（确定性建图：同名合并 / 拓扑序压过 LLM 序 / 成环 / 500 节点深链不 `RecursionError`）、`test_learning_store.py`、`test_learning_session.py`（流式不变量 / **判定的每个失败分支都不推荐** / 三道成本闸门）、`test_api_learning.py`（9 路由 503 / 建壳零 LLM / **GET 端点零 LLM 调用** / **换新 store 实例重读图与正文一致且调用数不增**）、`test_agent_streaming.py`（流式假客户端 → 每 delta 一个 token 帧且拼接 == `done.content`；**原 Fake → 零 token 帧**，显式锁住降级路径）。另有一条前端离线守卫：页面不得出现 `<link>` / `src=` / 绝对 URL（零依赖离线可跑这条铁律很容易被顺手加个字体 CDN 破坏，且只在断网环境才暴露，故用测试钉住）。前端没有测试框架，用 stdlib 的 Node 脚本 + DOM 桩把**真实页面里的真实函数**跑起来（纯函数 / 真实 payload 渲染 / 分片 SSE 消费，60+ 条断言）。

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
- ✅ **Phase 8**：工程化（MCP server 进程常驻复用 + Model Gateway：多 provider/超时/重试/降级/用法日志）
- ✅ **Phase 9**：学习图谱（研究 → 学习路径知识图谱 → 逐点讲解 → 系统推荐点亮 + 用户确认；SQLite 存结构 + Markdown 存正文；报告逐字流式 + 错误帧；图谱主导的新主页）
- ⬜ **Phase 10**：Docker（容器化，顺延）

## 阶段文档

每阶段的实现说明见 `docs/phase-*.md`（已 gitignore，本地保留）；公开工作日志见 `LOG.md`。
