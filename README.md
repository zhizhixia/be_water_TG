# Be Water TG

面向**已取得管理员明确许可**的 Telegram 群组的低打扰自动参与工具。程序在本机启动一个只监听 `127.0.0.1` 的 Web 控制台（Flask + Waitress），Telethon 负责连接 Telegram；可选 AI 仅用于受限的内容识别，不能扩大自动化能力。

> **授权与安全前提：** 本工具只处理你在 `TARGET_GROUPS` 中显式配置、并已取得群管理员许可的授权群。它不用于隐藏自动化身份、绕过 Telegram 风控、自动加群、向未授权群发言，也不会访问外部服务来完成群内任务。

## 整体结构

- **程序目录**：只放只读资源（模板、样式、消息样例、`.env.example`）。程序运行期间不会往这里写任何数据。
- **运行数据目录**：所有配置、数据库、登录会话、日志都集中固定在 `%LOCALAPPDATA%\BeWaterTG\`（详见「运行数据位置与说明」）。升级、替换程序目录不会影响这些数据。
- **操作入口**：浏览器打开本机控制台 `http://127.0.0.1:5000`。控制台只监听本机回环地址，不对外网开放。

## 能力与边界

- 普通参与：按全局配额、概率、冷场阈值和可中断等待进行低频参与；投诉或管理员警告会暂停对应群。
- 每日签到：以 `Asia/Shanghai` 自然日为准，每群每自然日最多尝试一次。
- 抽奖扫描：仅扫描授权群；冷启动不追溯普通历史，只扫描上海当日仍置顶的消息。
- AI 外发：仅 `AI_ENABLED=true` 且操作者明确授权后，才向配置的 AI 服务发送正文与必要上下文；绝不发送媒体二进制、Telegram 凭据、session 或数据库；AI 仅返回固定结构化识别，不能生成动作、代码或参与文案。
- 动作白名单：仅 owner/admin/bot 且权限查询成功的抽奖可自动执行；动作仅限单个明确 callback，或最多 32 个 Unicode 字符的固定白名单文本；URL、Web App、登录、支付、提及、手机号、邮箱、换行、多个参与按钮等均转人工。
- 去重与复核：按 `peer_id + message_id` 优先去重，并按正文与按钮的七日跨群基础指纹去重；执行前重新读取消息、按钮、群和发布者。
- 风控：首个外部动作前原子占用群与账号日配额；抽奖外发全账号串行；SQLite 不可用时 fail closed（宁可不动，也不乱动）。回调超时或断连记为 `RESULT_UNKNOWN`，不重试、不退款，只冻结该群抽奖。
- 单实例与清理：同一账号与状态库只允许一个后台实例。历史清理只能在停止/空闲时人工执行，清理后旧置顶可能再次被识别，界面会二次确认。

## 下载与安装（发布包）

发布包是**完整目录 ZIP**（`BeWaterTG-win64.zip`），**不能只拷贝其中的单个 `BeWaterTG.exe`**——程序运行需要同目录下的模板、样式、配置样例和消息样例。

1. 解压 ZIP 到任意目录（例如 `D:\BeWaterTG`；中文或含空格的路径也可以）。
2. 双击 `BeWaterTG.exe` 启动。首次运行会在本机创建运行数据目录，控制台只监听 `127.0.0.1:5000`。
3. 用浏览器打开 `http://127.0.0.1:5000`。

> 启动文件是解压目录根下的 `BeWaterTG.exe`，不要把它单独挪到别处运行。

## 首次启动与配置向导

第一次打开控制台时，程序还没有保存任何配置，会自动把你引导到左侧「系统设置」页，并提示“首次使用：请先完成系统设置”。

需要填写的内容：

- **API ID / API Hash**：Telegram 开发者凭据（在 <https://my.telegram.org> 申请，属于你要用来参与的**专用测试账号**）。
- **手机号**：该账号绑定的手机号，含国际区号。
- **授权群组**：在「授权群组」页填写你**已取得群管理员许可**的群链接（`https://t.me/...`），逗号或换行分隔。系统不会自动发现、加入或向列表外的群发送消息。

点「保存系统设置」后**立即生效，无需重启**。安全说明：

- **API Hash 与 AI Key 保存后不再回显**，界面只显示“已设置”；留空表示保持原值不变，不会覆盖成空。
- 保存操作本身**不会连接 Telegram**，只是把配置写到本地 `%LOCALAPPDATA%\BeWaterTG\config\.env`。

配置保存后，点左侧「开始自动参与」。从这一刻起，程序才会真正连接 Telegram 并开始运行；首次登录可能要求输入验证码（见「日常使用」）。

健康检查：浏览器访问 `http://127.0.0.1:5000/api/health`，返回 `app`（应用名）、`version`（版本）、`configured`（是否已配置）三个字段。`configured` 为 `false` 即尚未完成配置。

## 运行数据位置与说明

所有运行数据都集中放在 Windows 的每用户应用数据目录下：

```text
%LOCALAPPDATA%\BeWaterTG\
├── config\.env                    配置文件（API 凭据、授权群、策略等）
├── config\messages\               用户消息文件（TXT 兜底文案）
├── state\be_water.db              状态数据库（SQLite）
├── session\sender_session.session Telegram 登录会话
├── logs\be_water.log              日志
└── backups\                       备份与迁移状态
```

术语解释：

- **`%LOCALAPPDATA%`**：Windows 给每个用户分配的“本地应用数据”目录，通常是 `C:\Users\<你的用户名>\AppData\Local`。运行数据不写在程序目录里，因此升级或替换程序目录不会影响你的数据。
- **session（登录会话）**：Telegram 登录成功后留下的会话文件，下次启动无需重新输入验证码。它等同于登录凭据，**必须妥善保管，不能泄露或提交**。
- **SQLite 数据库**：一个本地单文件数据库，保存群组计数、审计、去重、冻结等状态，即 `state\be_water.db`。

程序目录本身只放**只读资源**，不会写入任何运行数据。

## 日常使用

- **启动**：双击 `BeWaterTG.exe`（发布版）或运行 `uv run python main.py`（源码）。启动后浏览器打开 `http://127.0.0.1:5000`。
- **开始 / 暂停 / 恢复 / 停止**：使用控制台左侧边栏的按钮。停止只停掉自动参与循环，不会删除任何数据。
- **验证码**：首次真实登录（或 Telegram 要求重新验证时），控制台「实时动态」页会出现“需要 Telegram 验证码”输入框，填入手机收到的验证码提交即可。验证码只提交给当前本地会话，不会外发。
- **单实例**：同一账号与同一状态库只允许一个后台实例。如果已有一个在运行，再启动第二个实例会检测到“本机服务已在运行”并安全退出。

## 升级

1. 先停止正在运行的程序。
2. 用新版本的**完整目录**整体替换旧程序目录（删掉旧的 `BeWaterTG` 目录，放入新的）。运行数据在 `%LOCALAPPDATA%\BeWaterTG\`，替换程序目录不会影响它。
3. 重新启动 `BeWaterTG.exe`。

> 升级 = 替换整个程序目录。不要只替换单个 `BeWaterTG.exe`，也不要改动 `%LOCALAPPDATA%\BeWaterTG\` 下的运行数据。

## 备份与恢复

- 要备份的数据都在 `%LOCALAPPDATA%\BeWaterTG\`，**备份或恢复前必须先停止程序**。
- 直接复制整个 `BeWaterTG` 数据目录即可。其中 `session\sender_session.session` 是登录凭据，务必单独妥善保管，不要提交到版本库或公开分享。
- 恢复时把备份目录放回 `%LOCALAPPDATA%\BeWaterTG\`（覆盖前确认程序已停止），再启动即可。

## 排障

**端口被占用 / 已有一个实例在运行**

程序只监听 `127.0.0.1:5000`。如果 5000 端口被其他程序占用，或本机已有一个 BeWaterTG 在运行，第二个实例会安全退出并提示。解决：关掉已运行的实例或释放 5000 端口后重新启动。

**首次启动停留在“未配置”**

控制台会自动引导到「系统设置」。完成填写并保存后，`/api/health` 的 `configured` 变为 `true` 才算配置完成。

**旧数据迁移被阻断（自定义数据库路径）**

如果从旧版本升级，且旧 `.env` 曾把数据库路径改到过非默认位置，程序不会自动继续，而是停下并提示你把旧数据库文件人工复制到固定位置：

```text
%LOCALAPPDATA%\BeWaterTG\state\be_water.db
```

复制完成后重新启动，程序会校验该库并完成恢复。迁移过程不会覆盖或删除旧程序目录里的源文件。

**看不到配置或状态**

确认浏览器访问的是 `127.0.0.1:5000`；本服务不监听局域网或公网地址。

## 从源码运行（开发者）

前提：Windows、Python 3.11、[uv](https://docs.astral.sh/uv/)。

```bash
# 1. 安装全部依赖（runtime / dev / build 三组）
uv sync --all-groups

# 2. 本地运行 Web 控制台
uv run python main.py

# 3. 跑测试（fake/mock，不连 Telegram / AI / 真实网络）
uv run pytest tests -v

# 4. 构建 Windows 发布包
uv run python scripts/build_windows.py

# 5. 审计发布包
uv run python scripts/verify_release.py dist/BeWaterTG-win64.zip
```

说明：

- 依赖声明在 `pyproject.toml`，锁文件是 `uv.lock`，不再使用 `requirements.txt`。要求 Python 版本为 `>=3.11,<3.12`。
- `build_windows.py` 会生成 `dist\BeWaterTG\`（目录型 EXE）、`dist\BeWaterTG-win64.zip` 与 `dist\SHA256SUMS.txt`，并在构建末尾自动执行严格的本地烟雾验收（启动、健康检查、单实例、数据隔离、端口释放）。
- 测试只使用 fake/mock 并隔离网络，不会登录 Telegram、发送群消息或调用 AI；测试通过数量以实际命令输出为准，不在此写死。

## 项目结构

```text
main.py / web_app.py / web_manager.py  Flask 入口、API 与后台任务
src/                                    Telegram、策略、状态、签到与抽奖服务
templates/ / static/                    本地 Web 控制台
tests/                                  pytest 回归测试（fake/mock）
scripts/                                发布构建与审计脚本
```
