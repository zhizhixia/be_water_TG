# 可核实事实与来源

采集日期：2026-10-19。

## 项目事实

本次是用户本机 be_water_TG 中文控制台的交互/外观探索。当前新版版本2.0.0，依据本机 `rewrite/bewater/models.py` 和已验收界面截图；线上仓库不替代本机重写版本作为功能规格。模板、CSS、JavaScript的SHA256均与 `.hermes/rewrite/evidence/reviewed-source-manifest.json` 一致。

实际入口与功能以 `rewrite/templates/index.html`、`rewrite/static/app.css`、`rewrite/static/app.js` 和 `docs/rewrite/requirements.md` 为准。

## 真实设计参照

- Linear官方看板文档：https://linear.app/docs/board-layout 。网页已真实截图；首图是弱化的功能示意，不能据此声称量到了完整产品卡片字体。
- Linear显示选项：https://linear.app/docs/display-options 。核实列表与看板是不同信息组织方式。
- GitHub CLI公开Issues：https://github.com/cli/cli/issues 。独立浏览器实际打开，无登录墙；借鉴列表的信息层级，不复制任务内容和计数。
- Dieter Rams理念一手整理：https://vitsoe.com/us/about/good-design 。用于克制、用途优先的设计思路，不声称本项目由该设计师设计。
- Studio Dumbar官方服务介绍：https://studiodumbar.com/services 。仅用作大胆候选的方法参考，不冒充其作品。
- Telegram官方标识：https://telegram.org/img/t_logo.svg 。已下载并验证为SVG；只用作连接平台标识，不把它当本项目Logo。

## 工具边界

Python版Playwright未安装，技能原生ref_board.py明确返回需要Playwright；现有浏览器配置被占用，不关闭用户浏览器、不修改Hermes配置。改用已安装Node Playwright和Chrome独立临时上下文做真实截图与交互验证，生产项目依赖不变。
