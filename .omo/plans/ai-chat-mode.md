# AI 智能聊天模式

## TL;DR

> **Quick Summary**: 新增基于 DeepSeek API 的 AI 聊天模式。读取目标群组最新 5-10 条消息作为上下文，通过可配置的 system prompt 让 AI 生成自然对话回复。与现有 txt 随机选句模式通过 UI 按钮全局切换，API 失败自动回退到 txt。
> 
> **Deliverables**:
> - `src/ai_client.py` — DeepSeek LLM 客户端封装
> - `src/ai_sender.py` — AI 消息生成（上下文获取 + API 调用）
> - 修改 `ui/config_form.py` — API 配置 + prompt 输入 + 模式切换
> - 修改 `ui/send_loop.py` — AI 模式分支
> - 修改 `src/config.py` — Settings 新增 AI 字段
> - 更新 `.env.example`
> 
> **Estimated Effort**: Medium-Large
> **Parallel Execution**: YES — 2 waves
> **Critical Path**: AI 客户端 → AI 发送逻辑 → UI 集成

---

## Context

### Original Request
保留 txt 随机句子的同时，新增 AI 模式：读取群组最新消息 → 喂给 DeepSeek API → 生成接近真人的聊天回复。

### Interview Summary
- **API**: DeepSeek（OpenAI 兼容格式），Key 和 Base URL 存 .env
- **模式切换**: 全局统一，UI 按钮一键切换
- **上下文**: 每次取最近 5-10 条群聊消息
- **失败回退**: 重试 2-3 次后自动回退到 txt
- **Prompt**: GUI 内可编辑，带合理默认值
- **记忆**: AI 记住本群自己最近的 3-5 条发言，重启清空

### Research Findings
- Telethon 支持 `client.get_messages(entity, limit=N)` 获取历史消息
- DeepSeek API 完全兼容 `openai` Python SDK：`https://api.deepseek.com/v1`
- 现有重试逻辑在 send_loop 中：3 次退避 [30,60,120] 秒
- Settings dataclass 可通过添加新字段扩展

---

## Work Objectives

### Core Objective
新增 AI 驱动的聊天回复模式，与现有 txt 模式共存，通过 UI 无缝切换。

### Must Have
- DeepSeek API 集成（兼容 openai SDK）
- 获取群组最新 5-10 条消息作为上下文
- GUI 可编辑的 system prompt（带默认值）
- 全局 AI/txt 模式切换按钮
- API 失败重试 2-3 次后回退 txt
- .env 管理 API 配置
- 短期记忆：AI 记住自己在本群最近的 3-5 条发言

### Must NOT Have
- 不做每群组独立 prompt 配置
- 不删除 txt 模式任何功能
- 不引入新的外部依赖（需新增 openai SDK）
- 不跨会话持久化记忆（重启即清空）

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: Tests-after
- **Framework**: pytest

### QA Policy
- AI 客户端单元测试（mock API 响应）
- Settings 向后兼容测试

---

## Execution Strategy

```
Wave 1 (基础 — AI 客户端 + 配置):
├── Task 1: src/ai_client.py — DeepSeek 客户端封装 [quick]
├── Task 2: src/config.py + .env.example — Settings 扩展 [quick]
└── Task 3: AI 客户端单元测试 [quick]

Wave 2 (集成 — 发送逻辑 + UI):
├── Task 4: src/ai_sender.py — 上下文获取 + 生成 [deep]
├── Task 5: ui/send_loop.py — AI 模式分支 [quick]
├── Task 6: ui/config_form.py — API 配置 + prompt + 切换 [visual-engineering]
└── Task 7: ui/app.py — 模式切换按钮 + 参数传递 [quick]

Wave FINAL:
├── F1: pytest 全部测试 + 导入验证
├── F2: 代码审查
└── F3: 范围完整性检查
```

---

## TODOs

- [x] 1. `src/ai_client.py` — DeepSeek LLM 客户端封装

  **What to do**:
  - 创建 `src/ai_client.py`
  - 封装 `AIClient` 类，使用 `openai.OpenAI(api_key=..., base_url=...)` 连接 DeepSeek
  - 方法 `chat(messages: list[dict]) -> str`：发送多轮对话，返回 AI 回复文本
  - 支持 `model`、`temperature`、`max_tokens` 参数（Settings 中配置）
  - 超时设置为 30 秒
  - 不依赖 openai 库需先安装：`pip install openai`

  **Must NOT do**:
  - 不依赖 asyncio（用 `openai` 同步 SDK，在 send_loop 中用 `await asyncio.to_thread()` 包装）
  - 不处理重试逻辑（由 send_loop 处理）

  **Agent**: `quick`
  **Wave**: 1

- [x] 2. `src/config.py` — Settings 扩展 + `.env.example` 更新

  **What to do**:
  - Settings 新增字段：`ai_enabled: bool = False`、`ai_api_key: str = ""`、`ai_base_url: str = "https://api.deepseek.com/v1"`、`ai_model: str = "deepseek-chat"`、`ai_prompt: str = ""`（默认 prompt 在 GUI 层提供）、`ai_context_count: int = 5`
  - load_settings() 读取 `AI_ENABLED`、`AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL`、`AI_PROMPT`、`AI_CONTEXT_COUNT` 环境变量
  - save_settings() 写出这些字段
  - .env.example 增加 AI 配置注释
  - 测试向后兼容：Settings 默认值不应破坏现有行为

  **Agent**: `quick`
  **Wave**: 1

- [x] 3. `tests/test_ai_client.py` — AI 客户端单元测试

  **What to do**:
  - 测试 `AIClient.chat()` 正常返回（mock openai）
  - 测试 API 调用失败时抛异常
  - 测试空上下文处理
  - 测试 Settings 新字段默认值

  **Agent**: `quick`
  **Wave**: 1

- [x] 4. `src/ai_sender.py` — AI 消息生成逻辑

  **What to do**:
  - 创建 `src/ai_sender.py`
  - `AISender` 类：接收 `TelegramSender`（获取消息）和 `AIClient`（调 API）
  - `async def generate_message(group: str, prompt: str, context_count: int) -> str`
    - 用 `client.get_messages(group, limit=context_count)` 获取最近 N 条
    - 构建 messages 时同时包含群聊上下文 + AI 本群自己最近 5 条发言（`collections.deque` 内存存储，重启清空）
    - 调用 `AIClient.chat()`
    - 生成回复后存入 deque，保持最近 5 条
    - 处理消息中的 sender 信息（让 AI 知道谁说了什么）
  - 用 `await asyncio.to_thread(client.chat, messages)` 包装同步调用

  **Agent**: `deep`
  **Wave**: 2

- [ ] 5. `ui/send_loop.py` — AI 模式分支

  **What to do**:
  - send_loop 收到 `settings.ai_enabled` 时走 AI 分支
  - AI 分支: `message = await ai_sender.generate_message(group, settings.ai_prompt, settings.ai_context_count)`
  - API 失败：利用现有 retry 机制（3 次退避），全失败后回退到 `message_manager.get_message(group)`
  - 不影响 txt 模式原有逻辑

  **Agent**: `quick`
  **Wave**: 2

- [ ] 6. `ui/config_form.py` — API 配置 + prompt + 模式切换

  **What to do**:
  - 在 API ID/Hash 下方新增 AI 配置区域（可折叠或分组）
  - 字段：`ai_api_key`（密码框）、`ai_base_url`（默认 https://api.deepseek.com/v1）、`ai_model`（默认 deepseek-chat）
  - System prompt 多行文本框，默认值："你是一个普通群聊参与者，请根据对话上下文自然地回复消息。回复要简短、口语化，像真人聊天一样。不要使用 AI 语气，不要提供帮助或自我介绍。"
  - 模式切换：`ft.Switch` 或下拉框 "AI 模式 / TXT 模式"
  - save/load 支持新字段

  **Agent**: `visual-engineering`
  **Wave**: 2

- [ ] 7. `ui/app.py` — 创建 AISender + 传递新参数

  **What to do**:
  - start_sending() 中：如果 ai_enabled，创建 AIClient → AISender
  - 将 ai_sender 传入 send_loop（可选参数）
  - 更新 requirements.txt 添加 `openai`

  **Agent**: `quick`
  **Wave**: 2

---

## Final Verification Wave

- [ ] F1. pytest 全部测试 + 导入验证
- [ ] F2. 代码审查
- [ ] F3. 范围完整性检查（Must Have 5 条）

---

## Commit Strategy

- **1**: `feat: 新增 DeepSeek AI 客户端 (ai_client.py)` — ai_client.py, requirements.txt
- **2**: `feat: Settings 扩展 AI 配置字段` — config.py, .env.example
- **3**: `test: AI 客户端单元测试` — test_ai_client.py
- **4**: `feat: AI 消息生成器 (ai_sender.py)` — ai_sender.py
- **5**: `feat: send_loop AI 模式分支` — send_loop.py
- **6**: `feat: UI AI 配置面板 + 模式切换` — config_form.py
- **7**: `feat: app.py 集成 AISender` — app.py
