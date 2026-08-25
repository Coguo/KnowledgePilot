# KnowledgePilot — AI Research Agent

针对用户提出的开放性研究问题，自主完成 **任务拆解 → 资料搜索 → 网页抓取 → 知识库构建（RAG）→ 检索 → 分析 → 生成带引用报告** 的全流程研究 Agent。

**当前进度**：
- ✅ **Phase 0：基础 Research Chat**（`用户 → LLM → 搜索 → 流式答案`）
- ✅ **Phase 1：RAG**（搜索网页动态建库 → 本地 BGE-M3 Embedding → Chroma 向量检索 → 带来源引用作答）
- ⬜ Phase 2+：RAG 优化 / LangGraph / Memory / Knowledge Graph / MCP / 工程化

完整规划见 `AI_Research_Agent_Project_Context.md`（已 gitignore，本地保留）。

## 架构

```
knowledge_pilot/
├── config.py      # 配置（环境变量 / .env，不硬编码密钥）
├── llm/           # LLM 客户端封装（openai SDK，默认 DeepSeek，流式 + 工具调用）
├── search/        # 搜索抽象层（stub 占位 / tavily 真实搜索，可插拔）
├── agent/         # 核心：手写 tool-calling 循环，对外发事件流（UI 无关）
├── rag/           # Phase 1：网页抓取 → 分块 → BGE-M3 Embedding → Chroma → 检索
├── api/           # FastAPI 层：把事件流映射为 SSE
└── web/           # 前端页面（单个 index.html）
```

> 设计要点：**Agent 引擎与 UI 完全解耦**——它只产出事件流。网页版把事件映射为 SSE；未来做桌面版只需新增一个前端消费同一接口，不返工。RAG 通过 `search_web` 工具内部透明增强接入（自动抓取搜索结果建库并检索），LLM 无需学习新工具，事件协议不变。

## 快速开始

前置：conda 环境 `knowledgepilot`（Python 3.11）已建好。

```bash
conda activate knowledgepilot
cd d:\Code\Project\Python\KnowledgePilot
pip install -e ".[dev]"          # 基础（Phase 0）
pip install -e ".[dev,rag]"      # 含 RAG（Phase 1，可选，体积较大）
```

### 配置

```bash
copy .env.example .env     # Windows
# 编辑 .env：
#   DEEPSEEK_API_KEY    ← LLM 密钥（必填）
#   TAVILY_API_KEY      ← 搜索密钥（SEARCH_PROVIDER=tavily 时必填）
#   RAG_ENABLED=true     ← 开启 RAG（需先安装 [rag] 依赖；首次会下载约 2GB 的 BGE-M3 模型）
#   EMBEDDING_CACHE_DIR=data/models   ← 模型缓存目录
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

**33 通过 + 2 跳过**，全部离线运行（Fake LLM / Fake Embedding / 内存向量库注入，不联网）。未安装 `[rag]` 依赖时，chromadb / trafilatura 相关测试自动跳过（`pytest.importorskip`）。

## 阶段规划（渐进而来）

- ✅ **Phase 0**：基础 Research Chat + Tavily 搜索
- ✅ **Phase 1**：RAG（动态抓取网页 → 分块 → Embedding → 向量检索 → 带来源引用）
- ⬜ Phase 2：RAG 优化（Hybrid Search / Reranker / Query Rewrite / Evaluation）
- ⬜ Phase 3：LangGraph Agent 编排
- ⬜ Phase 4：Memory（研究历史）
- ⬜ Phase 5：Knowledge Graph / GraphRAG
- ⬜ Phase 6：MCP
- ⬜ Phase 7：工程化（Redis / PostgreSQL / Model Gateway / Docker）

## 阶段文档

每阶段的实现说明见 `docs/phase-*.md`（已 gitignore，本地保留）；公开工作日志见 `LOG.md`。
