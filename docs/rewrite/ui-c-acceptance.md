# C方向正式前端接入验收

## 改动

用户明确选择：`C`。选择记录见`design-demos/direction-approved.md`。

仅修改正式前端三文件：

- `rewrite/templates/index.html`：浅绿任务工作台结构；三类独立任务；完整群状态、额度和暂停操作保留在可展开明细中；手机页面选择框。
- `rewrite/static/app.css`：C方向浅绿/白视觉、状态层级、任务卡、44px控件触达区域和响应式布局。
- `rewrite/static/app.js`：手机页面切换、真实快照任务分组、只在确有未知结果时强调计数。任务板和原明细共用同一个状态计算函数，继续使用纯文本节点。

原有全部DOM操作入口和表单字段保留。没有更换技术栈、增加依赖、修改后端、替换API或简化业务表单。

## 恢复与数据保护

接入前的三个真实前端文件保存为`.hermes/rewrite/ui-c-backup/frontend-before-c.zip`，逐成员SHA256校验通过。此包只含前端，不含账号和运行数据。

接入前对后端模块及原配置、会话、数据库记录摘要，接入后已校验全部未变。完整前后差异保存为`.hermes/rewrite/evidence/ui-c/frontend-changes.diff`。

未进行git add、commit、push或发布。

## 实际验证

```text
./rewrite/.venv/Scripts/pytest.exe -c rewrite/pytest.ini rewrite/tests -q
84 passed in 5.54s

./.venv/Scripts/pytest.exe tests -q
512 passed, 80 warnings in 19.98s
```

旧版80条警告来自Flet旧按钮API，不属于本次新增错误。以上是首次运行原始输出；最终复跑结果另存evidence/ui-c/rewrite-tests.xml和legacy-tests.xml，以最终XML与终端输出为准。

```text
node .hermes/rewrite/verify-ui-c.cjs
status: passed
viewports: 3
pagesPerViewport: 7
layoutChecks: 26
scriptErrors: 0
externalRequests: 0
expectedValidationErrors: 1
minContrast: 5.17
```

视口：1440×900、1280×900、375×812。检查七个真实页面、可展开群明细、手机选择框以及未知结果状态。26项包括21个页面组合、3项群明细与2项未知结果渲染。

真实浏览器操作覆盖：

- 群聊天开关关闭/恢复、提交配置并通过API读回；任务卡随真实保存结果更新。
- 非法额度0返回预期422、没有写入；合法额度保存后API读回正确。
- 消息库两行保存，句中逗号保留，重新读取与输入逐字一致。
- 匿名平台端口下使用真实控制器开始、暂停、恢复、停止。
- 三类任务共用明细状态；模拟未知结果的只读渲染，以及恶意群名不作为HTML解释。
- 手机不显示易截字的横向导航；表单和表格操作全部保留。

使用隔离Flask服务与匿名FakeTelegram。服务拒绝外部socket连接，只允许标准库和Waitress自身的本机唤醒；浏览器也仅放行该隔离服务。用于未知结果/XSS的输入仅改变浏览器DOM，没有伪造服务器执行结果。

验收曾遇到测试环境问题：5061被Chrome禁止；Waitress的内部唤醒需要精确放行；旧测试替身的掩码手机号不符合当前启动校验。均只调整隔离验证工具与测试数据，未放宽正式校验或网络安全规则。最后截图等待保存提示自然消失，没有删除提示来掩盖遮挡。

截图和机器结果位于`.hermes/rewrite/evidence/ui-c/`。`node --check`和已缓存Prettier格式化已执行。没有可用的`lsp_diagnostics`，不声称运行过它。

## 使用与边界

源码方式双击项目根目录的`start-v2.bat`；若页面已打开，强制刷新以重新加载CSS和JavaScript。打开页面或保存设置不会自行开始账号任务。

未连接真实Telegram或AI，未运行生产迁移，未改动真实运行数据。已有Windows ZIP仍是此前版本，本次未重新打包，不能声称ZIP已经包含C界面。
