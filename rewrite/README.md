# Be Water TG 新版源码（v2）

新版业务与中文控制台都在本目录；根目录的 `main.py`、`run.bat`、`src/`、`ui/` 属于旧版，不是这里的新入口。

**首次下载、安装、配置、升级和排障请看[根目录 README](../README.md)。** 本文只面向源码运行、测试和构建。

## 当前状态

- 当前源码用于预发布 `v2.0.0-rc.3`；程序内部版本仍是 `2.0.0`（`bewater/models.py` 的 `VERSION`）。
- 只做过离线测试、浏览器流程和隔离数据目录下的 Windows EXE 冒烟；真实 Telegram/AI 动作与生产数据迁移尚未验收。
- 旧版测试会用默认相对路径 `state/be_water.db` 写库。不要在含真实 `.env`、session、数据库的目录运行旧版测试；本机源码目录的旧库已出现保护差异，业务影响尚未只读对账。

## 目录

```text
rewrite/
├── main.py                 本机 Waitress 入口：单实例锁、日志、升级准备
├── bewater/
│   ├── app.py              Flask 路由、配置读写、快照与安全头
│   ├── runtime.py          任务线程、启动/暂停/停止、认证挑战
│   ├── engine.py           三业务调度、统一外发门禁、额度与恢复
│   ├── config.py           settings.json 校验、版本保护、旧 .env 导入
│   ├── store.py            SQLite 表、预留、审计、抽奖与清理
│   ├── telegram.py         Telethon 适配、身份解析、动作执行
│   ├── conversation.py     普通参与候选与冷场判断
│   ├── lottery.py          扫描、发现、复核与受限执行
│   ├── policy.py           概率、安全暂停、抽奖白名单与截止解析
│   ├── content.py          AI 请求边界与 TXT 消息库
│   ├── upgrade.py          升级准备与消息导入
│   ├── runtime_migration.py 旧运行数据迁移与阻断
│   └── legacy.py           旧 SQLite 事实导入
├── templates/index.html    七个中文页面
├── static/app.js|app.css   控制台前端
├── tests/                  匿名 pytest 与浏览器回归脚本
├── build.py                Windows 目录 EXE、ZIP、SHA256SUMS 与回环冒烟
├── compare.py              新旧同口径反例（需要旧版源码）
├── pyproject.toml          runtime/dev/build 三组依赖，Python >=3.11,<3.12
└── uv.lock                 独立锁文件
```

## 运行

要求：Windows、Python 3.11、[uv](https://docs.astral.sh/uv/)。

```bash
uv sync --project rewrite --all-groups
```

在仓库根目录双击 `start-v2.bat`，或执行：

```bash
uv run --project rewrite --frozen --group runtime python rewrite/main.py
```

浏览器打开 `http://127.0.0.1:5000`。打开页面、保存设置不会连接 Telegram；只有点击“开始”才连接。正式运行时数据固定在 `%LOCALAPPDATA%\BeWaterTG`，与程序目录分离。

不要同时运行新旧两版：正式入口共用 Windows 应用互斥体，后台另有账号/数据库锁。

## 测试

新版测试全匿名：`tests/conftest.py` 把 `LOCALAPPDATA` 指向临时目录，并拦截真实 socket；Telegram 使用 fake 适配器。

在仓库根目录执行：

```bash
uv run --project rewrite --all-groups pytest -c rewrite/pytest.ini rewrite/tests -q
```

旧版测试不在本目录，且存在默认相对路径写库风险；需要在干净克隆或不含真实运行数据的源码副本中单独执行。

`compare.py` 会同时导入新旧实现，只适合在保留旧版源码和根项目依赖的仓库里跑：

```bash
uv run --all-groups python rewrite/compare.py
```

它输出的是几个同口径缺陷反例，不是性能、成熟度或全部功能的基准。

## 构建 Windows 包

```bash
uv run --project rewrite --frozen --all-groups python rewrite/build.py
```

构建器只在 Windows 运行，使用 PyInstaller `--onedir`，收集 `templates/`、`static/`、`messages.txt`、`settings.example.json`，随后真实启动 EXE 做隔离冒烟：健康检查（`version` 为 `2.0.0`）、静态资源、第二实例退出、不创建 Telegram session、退出后释放端口。最后生成完整目录、ZIP 和 `SHA256SUMS.txt`。

输出在仓库内私有目录 `.hermes/rewrite/release/`。该目录可能同时存放旧版回退资料和本机验证数据，**不要整体上传或提交**；发布只取审计过的 `BeWaterTG-win64.zip` 与 `SHA256SUMS.txt`。

## 发布流程

推送 `v2.*` 标签会触发 `.github/workflows/windows-release-verify.yml`：在 Windows/Python 3.11 上运行旧版与新版测试、构建新版包并上传为预发布草稿，由维护者核验后再公开。具体步骤和条件以工作流文件为准；CI 结果不代表真实 Telegram/AI 或生产迁移已验收。

## 数据与迁移

- 新版配置写 `config/settings.json`；旧 `config/.env` 只作首次导入来源，不覆盖、不删除。
- 旧库继续使用 `state/be_water.db`：旧表保留，新增 `bw_` 表；旧授权群需要重新确认许可。
- 旧自定义数据库路径可能阻断启动，需要人工把旧库复制到固定位置；新版不会自动搬完全部旧状态，也不会生成整目录备份。
- 降级不能拿升级后的库覆盖旧备份；升级前先停止任务、退出后台并复制整个运行目录。

完整步骤、备份和风险说明见[根目录 README](../README.md)的“从旧版升级”“备份与恢复”。

## 文档边界

`docs/rewrite/` 下的验收记录是历史证据，不是本次 RC 的发布承诺，也不替代真实账号、真实 AI 和生产迁移验证。
