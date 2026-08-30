# Be Water TG

面向**已取得管理员明确许可**的 Telegram 群组的低打扰自动参与工具。Flask 提供本地 Web 控制台，Telethon 负责 Telegram 连接；可选 AI 仅用于受限的内容识别或生成，不能扩大自动化能力。

> **授权与安全前提：** 本项目只处理 `TARGET_GROUPS` 中显式配置的授权群。它不用于隐藏自动化身份、绕过 Telegram 风控、自动加群、向未授权群发言或访问外部服务以完成群内任务。

## 能力与边界

- 普通参与：按全局配额、概率、冷场阈值和可中断等待进行低频参与；明确投诉或管理员警告会暂停对应群。
- 每日签到：以 `Asia/Shanghai` 自然日为准，每个每日批次快照群最多尝试一次；失败当天阻止普通参与，但不阻止抽奖扫描与抽奖参与。
- 抽奖扫描：仅扫描 `TARGET_GROUPS`；冷启动不追溯普通历史，只扫描上海当日仍置顶的消息；离线恢复仅处理最近一小时普通消息。
- AI 外发：只有 `AI_ENABLED=true` 且操作者明确授权后，才向配置的 AI 服务发送正文、必要上下文和按钮元数据。绝不发送媒体二进制、Telegram 凭据、session 或数据库；AI 仅返回固定结构化识别，不能生成动作、代码或参与文案。
- 发布者与动作：仅 owner/admin/bot 且权限查询成功的抽奖可自动执行。动作仅限原消息内恰好一个明确 callback，或最多 32 个 Unicode 字符的固定白名单顶层/回复文本；URL、Web App、登录、支付、提及、链接、手机号、邮箱、换行、控制字符、多个参与按钮或可选加码均转人工。
- 去重与复核：以 `peer_id + message_id` 优先去重，并按正文和按钮的七日跨群基础指纹去重；执行前重新读取消息、按钮、群和发布者。
- 风控：首个外部动作前原子占用群和账号日配额，全账号抽奖外发串行，SQLite 不可用时 fail closed。回调超时或断连为 `RESULT_UNKNOWN`，不重试、不退款，只冻结该群抽奖；打开该提醒才解除冻结，且不改变参与状态。
- 单实例与清理：同一账号与状态库只允许一个后台实例。历史清理只能在停止/空闲时人工执行；清理后旧置顶可能再次被识别或参与，这是已接受风险，界面应二次确认。

## Windows 安装

要求：Windows、Python 3.12+、[uv](https://docs.astral.sh/uv/)。只有实际运行并填写凭据后才会连接 Telegram。

```powershell
uv venv --python 3.12
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，把以下占位符替换为**专用测试账号**和已获许可的群；AI 默认 `false`，而每日签到、抽奖总开关和每日置顶扫描按已确认默认值为 `true`。功能启用不替代授权：只允许对显式配置的授权群运行。

```env
API_ID=填写你的_api_id
API_HASH=填写你的_api_hash
PHONE=填写你的账号手机号
TARGET_GROUPS=https://t.me/已授权群组
```

所有 `TARGET_GROUPS` 条目都必须事先获得群管理员许可。`TARGET_GROUP` 仅为旧配置兼容项；新配置应只使用 `TARGET_GROUPS`。所有策略配置都是全局值，不支持按群覆盖。

## 本地运行

```powershell
.\.venv\Scripts\python.exe main.py
```

浏览器访问 <http://127.0.0.1:5000>。首次真实连接 Telegram 时，控制台会请求登录验证码；请只在已授权测试账号与群组上完成该流程。`run.bat` 也可启动服务，但其中 Python 路径可能需要按本机环境调整。

## Windows 发布构建（v1.0.1）

发布构建只使用项目虚拟环境，不读取 `.env`、session 或本地数据库：

```powershell
.\.venv\Scripts\python.exe scripts\build_windows.py
.\.venv\Scripts\python.exe scripts\verify_release.py dist\BeWaterTG-win64.zip
```

构建会生成 `dist\BeWaterTG\`（onedir、保留控制台）、`dist\BeWaterTG-win64.zip` 与 `dist\SHA256SUMS.txt`。需先在项目环境中安装 PyInstaller；依赖清单集成后应将其固定版本。

烟雾测试骨架仅允许 `http://127.0.0.1`；它支持注入子进程和就绪探针，当前不要求 `main.py` 提供专用健康检查接口。

## 测试（不连接真实服务）

以下命令只运行 pytest；测试使用 fake/mock 和网络隔离，不登录 Telegram、不发送群消息、不调用 AI 或其他真实网络服务。不要在测试命令中填入真实凭据，也不需要启动 Web 服务。

```powershell
# 完整回归
.\.venv\Scripts\python.exe -m pytest tests\ -v

# 签到与抽奖相关测试（文件存在时）
.\.venv\Scripts\python.exe -m pytest tests\test_lottery_*.py tests\test_daily_task_scheduler.py tests\test_daily_checkin_service.py -v

# 配置与普通参与回归
.\.venv\Scripts\python.exe -m pytest tests\test_config_compat.py tests\test_send_loop_participation.py -v
```

如尚未创建虚拟环境，也可先按“Windows 安装”完成 `uv venv` 和依赖安装。测试结果以实际命令输出为准，不在文档中写死通过数量。

## 关键配置

`.env.example` 包含全部全局配置及安全默认值。重点项：

| 配置 | 含义与安全要求 |
|---|---|
| `TARGET_GROUPS` | 逗号分隔的显式授权群；不得通过其他来源扩展范围。 |
| `AI_ENABLED` | 默认 `false`；开启表示操作者授权向 `AI_BASE_URL` 外发限定文本数据。 |
| `DAILY_CHECKIN_ENABLED` | 默认 `true`；每群每日最多一次签到，可由操作者关闭。 |
| `LOTTERY_ENABLED` | 默认 `true`；允许抽奖扫描和安全动作规划，可由操作者关闭。 |
| `DAILY_PINNED_LOTTERY_SCAN_ENABLED` | 默认 `true`；扫描上海当日仍置顶的抽奖消息，可由操作者关闭。 |
| `LOTTERY_SCAN_INTERVAL_MINUTES` | 全局扫描间隔，允许范围 15–1440 分钟。 |
| `LOTTERY_PER_GROUP_DAILY_LIMIT` / `LOTTERY_ACCOUNT_DAILY_LIMIT` | 每群/账号日配额；账号上限不得小于每群上限。 |
| `LOTTERY_ACTION_GAP_MIN_SECONDS` / `LOTTERY_ACTION_GAP_MAX_SECONDS` | 抽奖间随机等待，范围 5–600 秒且最大值不小于最小值。 |
| `STATE_DB_PATH` | 本地 SQLite 状态路径；数据库不可用时不应外发。 |

## 数据、恢复与已接受风险

- 不要提交 `.env`、`*.session`、数据库、`state/`、API key、手机号、Telegram 凭据或 callback 原始敏感数据；这些路径由 `.gitignore` 排除。
- `state/be_water.db` 含群标识、计数、审计、去重和冻结状态。备份或恢复前必须停止程序；Telegram session 应单独按凭据保管。
- 崩溃后，已占用或无法确认的外发配额不会退款，避免重复动作。SQLite 预写失败、消息变化、发布者不可信或执行结果不确定时会停止外发或转人工。
- `RESULT_UNKNOWN` 仅冻结该群的抽奖执行，不影响签到或普通参与；用户打开提醒后解除抽奖冻结，但不把结果改写为成功。
- 手动历史清理会移除消息键和指纹；之后旧置顶或重投消息可能重新进入识别范围。这是明确接受的风险，清理前应人工确认。打开原消息不等于系统已核验其真实性、有效性或结果。

## 项目结构

```text
main.py / web_app.py / web_manager.py  Flask、API、SSE 与后台任务
src/                                    Telegram、策略、状态、签到与抽奖服务
templates/ / static/                    本地 Web 控制台
tests/                                  pytest 回归测试（fake/mock）
```
