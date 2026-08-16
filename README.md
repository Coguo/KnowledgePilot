# KnowledgePilot — AI Research Agent

针对用户提出的开放性研究问题，自主完成 **任务拆解 → 资料搜索 → 知识库构建 → RAG 检索 → 分析 → 验证 → 生成带引用报告** 的全流程研究 Agent。

当前处于 **Phase 0：基础 Research Chat**（`用户 → LLM → 搜索 → 答案`，流式）。完整规划见 `AI_Research_Agent_Project_Context.md`（该文件与 CLAUDE.md、memory/ 已 gitignore，不随仓库上传）。

## 架构

```
knowledge_pilot/
├── config.py      # 配置（环境变量 / .env，不硬编码密钥）
├── llm/           # LLM 客户端封装（openai SDK，默认 DeepSeek，流式 + 工具调用）
├── search/        # 搜索抽象层（当前 stub；后续可插拔 Tavily / DuckDuckGo / Brave）
├── agent/         # 核心：手写 tool-calling 循环，对外发事件流（UI 无关）
├── api/           # FastAPI 层：把事件流映射为 SSE
└── web/           # 前端页面（单个 index.html）
```

> 设计要点：**Agent 引擎与 UI 完全解耦**——它只产出事件流。网页版把事件映射为 SSE；未来做桌面版只需新增一个前端消费同一接口，不返工。

## 快速开始

前置：conda 环境 `knowledgepilot`（Python 3.11）已建好。

```bash
conda activate knowledgepilot
cd d:\Code\Project\Python\KnowledgePilot
pip install -e ".[dev]"
```

### 配置密钥

```bash
copy .env.example .env     # Windows
# 然后编辑 .env，填入 DEEPSEEK_API_KEY
```

未配置密钥时也可以启动（界面能打开），但发消息会得到清晰提示，提示你先填密钥。

### 运行

```bash
uvicorn knowledge_pilot.api.main:app --reload
```

浏览器打开 <http://127.0.0.1:8000>，输入研究问题（例如"RAG 的 chunking 策略有哪些"），可看到流式回答与工具调用状态。

### 测试

```bash
pytest
```

12 个测试全部离线运行（Fake LLM 注入，不联网）。

## 阶段规划（渐进而来）

- ✅ **Phase 0**：基础 Research Chat（本阶段）
- ⬜ Phase 1：RAG（Chunk → Embedding → VectorDB → Retrieval）
- ⬜ Phase 2：RAG 优化（Hybrid Search / Reranker / Query Rewrite / Evaluation）
- ⬜ Phase 3：LangGraph Agent 编排
- ⬜ Phase 4：Memory（研究历史）
- ⬜ Phase 5：Knowledge Graph / GraphRAG
- ⬜ Phase 6：MCP
- ⬜ Phase 7：工程化（FastAPI 完善 / Redis / PostgreSQL / Model Gateway / Docker）

## 阶段文档

每阶段的实现说明见 `docs/phase-*.md`。
