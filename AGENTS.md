# 仓库开发规范

## 项目结构

- `main.py` 是 Flask 程序入口，`web_app.py` 定义接口，`web_manager.py` 管理后台状态和服务器发送事件。
- 核心逻辑位于 `src/`；`ui/send_loop.py` 负责编排候选消息，其余 `ui/` 模块包含旧版 Flet 界面代码。
- 浏览器资源位于 `templates/` 和 `static/`，Windows 打包配置位于 `packaging/`，构建与发布审计脚本位于 `scripts/`。
- 测试位于 `tests/`，公共 fixture 统一放在 `tests/conftest.py`。

## 环境与常用命令

项目要求 Python `>=3.11,<3.12`，依赖由 `pyproject.toml` 和 `uv.lock` 管理。

```bash
# 安装运行、开发和构建依赖
uv sync --all-groups

# 启动本地 Web 控制台
uv run python main.py

# 运行完整测试
uv run pytest tests -v

# 运行单个测试模块
uv run pytest tests/test_selector.py -v
```

不得恢复或新增 `requirements.txt` 安装流程。`run.bat` 会优先使用项目 `.venv`，也可用于启动本地界面。

## Windows 构建与发布审计

Windows 发布不是无构建步骤的源码分发，必须使用项目脚本完成构建和审计：

```bash
# 构建目录型 EXE、ZIP 和 SHA-256 校验文件，并执行本机烟雾测试与发布审计
uv run python scripts/build_windows.py

# 对已有发布 ZIP 单独执行审计与本机回环验收
uv run python scripts/verify_release.py dist/BeWaterTG-win64.zip
```

构建输出位于 `dist/`。审计必须检查发布文件清单、敏感文件排除、隔离的 `LOCALAPPDATA`、本机回环健康检查、单实例约束和端口释放。

## 代码规范

- 使用四空格缩进、LF 换行、文件末尾换行和 Python 类型标注。
- 模块、函数、fixture 和变量使用 `snake_case`，类使用 `PascalCase`，常量使用 `UPPER_CASE`。
- 函数和方法必须包含简体中文 Docstring；代码注释、文档和面向运维的日志使用简体中文。
- 异步操作显式使用 `async`/`await`，保持 Flask/线程协调与 Telegram 异步逻辑之间的边界。
- 当前未配置统一格式化器或静态检查器；修改时遵循相邻代码的导入和排版风格。

## 测试规范

- 测试使用 `pytest` 和 `pytest-asyncio`，文件命名为 `test_<领域>.py`，测试命名为 `test_<行为>()`。
- 修改状态转换、并发、配置兼容、接口响应或错误处理时，应补充对应回归覆盖。
- 测试必须使用 fake/mock，默认隔离 `LOCALAPPDATA`、配置文件和 socket；禁止登录真实 Telegram、发送真实消息、调用真实 AI 或访问外部网络。
- 需要测试配置场景时，应通过 fixture、`monkeypatch` 或显式 `RuntimePaths` 注入，不得依赖开发机的环境变量或 `%LOCALAPPDATA%\BeWaterTG\config\.env`。
- 完整测试通过是最低验收基线，不设置固定通过数量。

## 提交与合并请求

- 提交标题使用 Conventional Commits 前缀，例如 `feat:`、`fix:`、`test:`、`refactor:`、`docs:`、`chore:`，标题使用简体中文并保持单一范围。
- 合并请求应说明行为变化、列出实际验证命令与结果、关联相关问题；修改 `templates/` 或 `static/` 的可见界面时附截图。

## 安全与配置

- 禁止提交 `.env`、`*.session`、`*.session-journal`、`state/`、API 密钥、电话号码、Telegram 凭据或审计数据库。
- 新增配置项时同步更新 `.env.example`，只使用安全占位值，并在需要时保留已有配置键的兼容解析。
- 运行数据只能写入 `%LOCALAPPDATA%\BeWaterTG`；程序目录和发布包不得包含真实配置、状态、会话或日志。
