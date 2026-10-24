# Be Water 界面方向预览

## 当前状态

用户已明确选择C「轻量任务板」，已接入rewrite正式源码。对比页默认展示C，其余两版作为设计备选保留；本目录仍是无后端的设计演示，不是正式应用。

- `index.html`：自包含对比页，内嵌三套原型和六格方向图，无需安装依赖或启动服务。
- `dir-1.html`：A，深色任务台，侧栏导航、执行器状态与群任务表。
- `dir-2.html`：B，清晰工作台，浅色横向导航与群列表，推荐优先试用。
- `dir-3.html`：C，轻量任务板，按普通参与、每日签到和抽奖分类。
- `thumbs.html`、`thumbs.png`：六个结构候选；A对应候选4、B对应候选3、C对应候选1。

用浏览器打开index.html即可。每版包含七个可切换视图，可试用开始、暂停、恢复、停止、群开关、消息库编辑和表单校验；均为本页模拟，不连接Telegram或AI、不读取配置或数据库。不要填写真实凭据。

## 已实际执行的验证

命令：`python design-demos/build-demos.py && node --check design-demos/verify-demos.cjs && node design-demos/verify-demos.cjs`。

真实输出摘要：

```text
status: passed
variants: 3
viewports: 2
viewsPerVariant: 7
layoutChecks: 48
interactionRuns: 6
networkRequests: 0
consoleErrors: 0
SOURCE_MANIFEST_MATCH True FILES_CHECKED 37
MIN_TEXT_CONTRAST 5.27
```

- 视口为1440×900与375×812；48项布局检查包含42个页面组合及6个未知结果组合。
- 控件触达区域至少44×44px；可见文字不小于12px，移动端主要正文16px。对比度按浏览器计算样式与不透明祖先背景抽取，不等于完整无障碍认证。
- 六轮交互验证覆盖取消开始、开始、暂停、恢复、停止、单群开关、群搜索、额度错误、消息空值、逗号保留、查看不解冻、恢复必须输入确认文字，以及连接中断提示。
- 手机改用明确页面选择框，避免横向导航末项被截断。
- Chrome真实截图见screenshots目录；已查看桌面和手机截图，并修正导航截字和任务按钮对齐。
- 对比页另行验证三版沙箱装载、手机切换、九个群开关；页面脚本错误与外部页面请求均为0。
- `verification.json`、`comparison-verification.json`保存可追溯结果。
- 原技能的Python版Playwright体检因依赖未安装而跳过；以上为已安装Node Playwright与真实Chrome的替代验证。当前工具没有提供`lsp_diagnostics`，未声称执行过它。

## 不变项与未验证边界

方向探索阶段曾对照已验收源码清单核对37个文件，全部SHA256一致，当时只改design-demos。此结论是历史阶段记录，不代表接入后的前端仍未变化。现在已接入C界面并通过隔离服务验证，后端及受保护数据未变；最新验收见../docs/rewrite/ui-c-acceptance.md。

这是视觉与本页交互验证，不是Telegram、AI、生产迁移或业务闭环验收。选定方向后仍需复用正式应用现有字段、接口、业务门禁与数据，不可拿原型中的简化表单替换实际完整功能。

用户原话C已记录在direction-approved.md。正式接入证据见../docs/rewrite/ui-c-acceptance.md；下文原型检查结果仅指方向探索阶段。
