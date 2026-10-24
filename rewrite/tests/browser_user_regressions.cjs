"use strict";

/**
 * 五项修复的真实浏览器回归：只访问独占本地匿名服务，阻断其他网络。
 *
 * 用法：node rewrite/tests/browser_user_regressions.cjs [baseURL]
 * Playwright 先按普通模块解析；缺失时用 PLAYWRIGHT_MODULE 指定安装目录。
 * 证据目录：EVIDENCE_DIR（默认系统临时目录下的 bewater-five-fixes-browser）。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

/** 先尝试普通依赖解析，再允许显式指定已安装模块。 */
function loadPlaywright() {
  try {
    return require("playwright");
  } catch (error) {
    if (!process.env.PLAYWRIGHT_MODULE) {
      throw new Error(
        "未找到 playwright；请安装或设置 PLAYWRIGHT_MODULE 指向已安装模块",
      );
    }
    return require(process.env.PLAYWRIGHT_MODULE);
  }
}

const BASE = (
  process.argv[2] ||
  process.env.BASE_URL ||
  "http://127.0.0.1:5091"
).replace(/\/+$/, "");
const EVIDENCE =
  process.env.EVIDENCE_DIR ||
  path.join(os.tmpdir(), "bewater-five-fixes-browser");
const EXTERNAL = [];
const CHECKS = [];

/** 记录一条通过的断言，便于生成机器可读证据。 */
function passed(name, detail) {
  CHECKS.push({ name, detail, ok: true });
  console.log(`PASS ${name}${detail ? `: ${detail}` : ""}`);
}

/** 等待条件成立，失败时输出明确原因。 */
async function eventually(check, message, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.fail(message);
}

/** 读取页面固定元素文本。 */
async function text(locator) {
  return ((await locator.textContent()) || "").trim();
}

/** 打开完整控制台并等待首次快照。 */
async function openApp(context, viewport = { width: 1280, height: 900 }) {
  const page = await context.newPage();
  if (viewport) await page.setViewportSize(viewport);
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.locator("#state").waitFor();
  await eventually(
    async () =>
      (await text(page.locator("#state"))) !== "正在读取状态" &&
      (await text(page.locator("#state"))) !== "正在读取后台状态",
    "页面未在限定时间内取得快照",
  );
  return page;
}

/** 进入指定工作区。 */
async function goPage(page, name) {
  await page.locator(`nav [data-page="${name}"]`).click();
}

/** 通过同源 API 读取 JSON。 */
async function getJSON(request, url) {
  const response = await request.get(`${BASE}${url}`);
  assert.equal(response.status(), 200, `GET ${url} 未返回 200`);
  return response.json();
}

/** 抽奖记录（可按待处理过滤）。 */
async function lotteries(request, pending = false) {
  return (
    await getJSON(
      request,
      `/api/records/lotteries?offset=0&limit=50${pending ? "&pending=true" : ""}`,
    )
  ).items;
}

/** 找到指定 id 的记录。 */
function findRecord(items, id) {
  return items.find((item) => item.id === id);
}

/** 等待抽奖表出现指定行数且不是空状态。 */
async function waitLotteryRows(page, count) {
  await eventually(
    async () =>
      (await page.locator("#lottery-rows tr").count()) === count &&
      (await page.locator("#lottery-rows .empty").count()) === 0,
    `抽奖记录表未出现 ${count} 行`,
  );
}

/** 等待下一次消息库保存响应。 */
function librarySaveResponse(page) {
  return page.waitForResponse((item) => {
    const pathname = new URL(item.url()).pathname;
    return (
      item.request().method() === "POST" &&
      /^\/api\/messages\/[^/]+$/.test(pathname)
    );
  });
}

/** 等待一次消息库保存请求并确认状态码。 */
async function saveLibrary(page, expected = 200) {
  const response = librarySaveResponse(page);
  await page.getByRole("button", { name: "保存消息库" }).click();
  const result = await response;
  assert.equal(result.status(), expected, `消息库保存返回 ${result.status()}`);
  return result;
}

/** 将浏览器外部访问全部阻断，只允许独占本地服务。 */
async function blockExternal(context) {
  await context.route("**/*", async (route) => {
    const url = route.request().url();
    if (url === BASE || url.startsWith(`${BASE}/`)) return route.continue();
    EXTERNAL.push(url);
    return route.abort();
  });
}

/** 串行验证匿名控制台的五项用户路径，失败保留真实退出状态。 */
async function main() {
  const { chromium } = loadPlaywright();
  fs.mkdirSync(EVIDENCE, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    locale: "zh-CN",
  });
  await blockExternal(context);
  const pageErrors = [];
  context.on("page", (page) =>
    page.on("pageerror", (error) => pageErrors.push(error.message)),
  );
  const request = context.request;
  let page = await openApp(context);
  page.removeAllListeners("pageerror");
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    // ---------- R02：打开链接不消提醒，显式标记才关闭 ----------
    const pendingBefore = await lotteries(request, true);
    assert.equal(pendingBefore.length, 2, "匿名数据应有两待处理记录");
    const unknown = pendingBefore.find((item) => item.status === "UNKNOWN");
    const manual = pendingBefore.find((item) => item.status === "MANUAL");
    assert.ok(unknown && manual, "缺少 UNKNOWN/MANUAL 待处理样例");
    await goPage(page, "lottery");
    await waitLotteryRows(page, 2);
    let unknownRow = page
      .locator("#lottery-rows tr")
      .filter({ hasText: "结果未知" });
    let manualRow = page
      .locator("#lottery-rows tr")
      .filter({ hasText: "需人工查看" });
    await unknownRow.waitFor();

    let openCalls = 0;
    page.on("request", (item) => {
      if (/\/api\/lotteries\/\d+\/open$/.test(new URL(item.url()).pathname))
        openCalls += 1;
    });
    await page.evaluate(() => {
      window.open = () => null;
    });
    const callsBeforeBlocked = openCalls;
    await unknownRow.getByRole("button", { name: "打开原消息" }).click();
    await page.waitForFunction(() =>
      document.getElementById("toast").textContent.includes("阻止"),
    );
    await page.waitForTimeout(300);
    assert.equal(openCalls, callsBeforeBlocked, "弹窗受阻仍调用了 open 接口");
    const afterBlocked = findRecord(
      await lotteries(request, false),
      unknown.id,
    );
    assert.equal(afterBlocked.pending, 1, "弹窗受阻却改变了 pending");
    assert.equal((await lotteries(request, true)).length, 2);
    await page.screenshot({
      path: path.join(EVIDENCE, "r02-blocked-popup.png"),
    });
    passed("R02 弹窗受阻不调用 open 且 pending 不变", `记录 ${unknown.id}`);

    page.removeAllListeners("request");
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("#state").waitFor();
    await eventually(
      async () => (await text(page.locator("#state"))) === "已停止",
      "刷新后未恢复停止状态",
    );
    await goPage(page, "lottery");
    await waitLotteryRows(page, 2);
    unknownRow = page
      .locator("#lottery-rows tr")
      .filter({ hasText: "结果未知" });
    manualRow = page
      .locator("#lottery-rows tr")
      .filter({ hasText: "需人工查看" });

    const openResponse = page.waitForResponse(
      (response) =>
        /\/api\/lotteries\/\d+\/open$/.test(new URL(response.url()).pathname) &&
        response.request().method() === "POST",
    );
    const popupPromise = page.waitForEvent("popup");
    await unknownRow.getByRole("button", { name: "打开原消息" }).click();
    const popup = await popupPromise;
    assert.equal((await openResponse).status(), 200, "open 接口未成功");
    await page.waitForTimeout(600);
    assert.ok(
      EXTERNAL.some((url) => url.includes("t.me/")),
      "未观察到 Telegram 外部导航尝试",
    );
    assert.equal(
      findRecord(await lotteries(request, false), unknown.id).pending,
      1,
      "打开外部链接却关闭了提醒",
    );
    await popup.close();
    passed("R02 正常取链接/外部导航失败仍保留提醒");

    await page.once("dialog", (dialog) => dialog.accept());
    await unknownRow.getByRole("button", { name: "标记已查看" }).click();
    await unknownRow.waitFor({ state: "detached" });
    let allRecords = await lotteries(request, false);
    assert.equal(findRecord(allRecords, unknown.id).pending, 0);
    assert.equal(findRecord(allRecords, unknown.id).status, "UNKNOWN");
    let snapshot = await getJSON(request, "/api/snapshot");
    assert.equal(
      snapshot.groups.find((group) => group.title === "抽奖记录群").frozen,
      true,
      "标记已查看不应解冻",
    );
    await page.screenshot({
      path: path.join(EVIDENCE, "r02-viewed-pending.png"),
    });
    passed("R02 显式标记只清 pending，UNKNOWN 与冻结保持");

    await page.locator("#only-pending").uncheck();
    await waitLotteryRows(page, 2);
    unknownRow = page
      .locator("#lottery-rows tr")
      .filter({ hasText: "结果未知" });
    manualRow = page
      .locator("#lottery-rows tr")
      .filter({ hasText: "需人工查看" });
    assert.equal(
      await unknownRow.getByRole("button", { name: "标记已查看" }).count(),
      0,
      "非 pending 记录仍显示标记按钮",
    );
    await page.once("dialog", (dialog) => dialog.dismiss());
    await manualRow.getByRole("button", { name: "标记已查看" }).click();
    await page.waitForTimeout(300);
    assert.equal(
      findRecord(await lotteries(request, false), manual.id).pending,
      1,
      "取消确认仍关闭了提醒",
    );
    await page.once("dialog", (dialog) => dialog.accept());
    await manualRow.getByRole("button", { name: "标记已查看" }).click();
    await eventually(
      async () =>
        (await manualRow
          .getByRole("button", { name: "标记已查看" })
          .count()) === 0,
      "确认后标记按钮未消失",
    );
    assert.equal((await lotteries(request, true)).length, 0);
    allRecords = await lotteries(request, false);
    assert.equal(findRecord(allRecords, manual.id).pending, 0);
    assert.equal(findRecord(allRecords, manual.id).status, "MANUAL");
    snapshot = await getJSON(request, "/api/snapshot");
    assert.equal(
      snapshot.groups.find((group) => group.title === "抽奖记录群").frozen,
      true,
      "全部标记后冻结被改变",
    );
    passed("R02 取消确认保留提醒，确认后记录仍可查且不解冻");

    // ---------- R03：全局事件显示“全局” ----------
    await goPage(page, "overview");
    await page.locator("#start").click();
    await eventually(
      async () => (await text(page.locator("#state"))) === "运行中",
      "匿名执行器未进入运行中",
      15000,
    );
    await page.waitForFunction(() =>
      [...document.querySelectorAll("#events .event")].some((item) =>
        item.textContent.includes("全局 · 运行中"),
      ),
    );
    const eventTexts = await page.locator("#events .event").allTextContents();
    assert.ok(eventTexts.some((item) => item.includes("全局 · 正在连接账号")));
    assert.ok(eventTexts.some((item) => item.includes("全局 · 运行中")));
    assert.ok(
      eventTexts.some((item) => item.includes("匿名授权群 · 已确认授权群身份")),
      "真实群事件没有显示群名",
    );
    assert.ok(
      eventTexts.every((item) => !item.includes("review_unused")),
      "全局事件被标成未启用群",
    );
    await page.screenshot({
      path: path.join(EVIDENCE, "r03-global-events.png"),
    });
    await goPage(page, "audit");
    await eventually(
      async () => (await page.locator("#audit-rows tr").count()) >= 2,
      "审计记录未加载",
    );
    const globalAudit = page
      .locator("#audit-rows tr")
      .filter({ hasText: "安全停发" });
    const groupAudit = page
      .locator("#audit-rows tr")
      .filter({ hasText: "人工恢复" });
    assert.equal(await text(globalAudit.locator("td").nth(1)), "全局");
    assert.equal(await text(groupAudit.locator("td").nth(1)), "匿名授权群");
    passed("R03 SSE 与审计的 peer=null 事件均显示全局");

    // ---------- R10：关闭网页不停止后台 ----------
    await goPage(page, "overview");
    const notice = page.locator("#keep-running");
    await notice.waitFor();
    assert.match(await text(notice), /关闭网页不会停止后台任务/);
    assert.match(await text(notice), /先点击“停止”/);
    let box = await notice.boundingBox();
    assert.ok(box && box.y + box.height <= 900, "桌面运行区提示不在首屏");
    await page.screenshot({
      path: path.join(EVIDENCE, "r10-desktop-running.png"),
    });

    await page.setViewportSize({ width: 375, height: 812 });
    await page.evaluate(() => window.scrollTo(0, 0));
    box = await notice.boundingBox();
    assert.ok(box, "手机宽度下提示不可见");
    assert.ok(
      box.x >= 0 && box.x + box.width <= 375 && box.y + box.height <= 812,
      `375px 下提示不在可视区域: ${JSON.stringify(box)}`,
    );
    await page.screenshot({
      path: path.join(EVIDENCE, "r10-phone-running.png"),
      fullPage: false,
    });
    passed("R10 桌面与 375px 运行区均可见关闭网页说明");

    await page.close();
    page = await openApp(context);
    assert.equal(
      await text(page.locator("#state")),
      "运行中",
      "关闭网页后后台未保持运行",
    );
    assert.match(await text(page.locator("#keep-running")), /关闭网页不会停止/);
    page.once("dialog", (dialog) => dialog.accept());
    await page.locator("#stop").click();
    await eventually(
      async () => (await text(page.locator("#state"))) === "已停止",
      "点击停止后后台未结束",
      15000,
    );
    assert.equal((await getJSON(request, "/api/snapshot")).state, "stopped");
    passed("R10 关闭页面后台仍运行，点击停止才结束");

    // ---------- R01：编辑与保存绑定同一文件 ----------
    await page.setViewportSize({ width: 1280, height: 900 });
    await goPage(page, "content");
    await page.selectOption("#library-select", "a.txt");
    await eventually(
      async () => (await page.inputValue("#library-text")) === "初始消息",
      "a.txt 未加载",
    );
    await page.fill("#library-text", "绑定保存");
    await page.evaluate(() => {
      document.getElementById("library-select").value = "b.txt";
    });
    await saveLibrary(page);
    let fileA = await getJSON(request, "/api/messages/a.txt");
    let fileB = await getJSON(request, "/api/messages/b.txt");
    assert.equal(fileA.text, "绑定保存", "保存未写入绑定文件 A");
    assert.equal(fileB.text, "初始消息", "保存误写到下拉新值 B");
    passed("R01 保存使用绑定文件而非刚改变的下拉值");

    await page.fill("#library-text", "取消后保存");
    page.once("dialog", (dialog) => {
      assert.match(dialog.message(), /未保存修改/);
      return dialog.dismiss();
    });
    await page.selectOption("#library-select", "b.txt");
    await eventually(
      async () => (await page.inputValue("#library-select")) === "a.txt",
      "取消切换后下拉未恢复 A",
    );
    assert.equal(await page.inputValue("#library-text"), "取消后保存");
    await saveLibrary(page);
    fileA = await getJSON(request, "/api/messages/a.txt");
    fileB = await getJSON(request, "/api/messages/b.txt");
    assert.equal(fileA.text, "取消后保存", "取消切换后未保存到 A");
    assert.equal(fileB.text, "初始消息", "取消切换后 B 被改写");
    passed("R01 取消切换恢复选择并保存回旧文件");

    await page.route("**/api/messages/b.txt", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ error: "测试注入失败" }),
      }),
    );
    await page.selectOption("#library-select", "b.txt");
    await page.waitForFunction(() =>
      document.getElementById("toast").textContent.includes("测试注入失败"),
    );
    await eventually(
      async () => (await page.inputValue("#library-select")) === "a.txt",
      "加载失败后下拉未恢复 A",
    );
    assert.equal(
      await page.inputValue("#library-text"),
      "取消后保存",
      "加载失败清空了编辑内容",
    );
    await page.unroute("**/api/messages/b.txt");
    passed("R01 加载失败恢复旧选择和内容");

    await page.route("**/api/messages/b.txt", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 700));
      return route.continue();
    });
    await page.selectOption("#library-select", "b.txt");
    assert.equal(
      await page.locator("#library-text").isEnabled(),
      false,
      "加载期间仍允许输入，会被迟到响应覆盖",
    );
    assert.equal(
      await page.locator("#library-save").isEnabled(),
      false,
      "加载期间仍允许保存旧文件的内容",
    );
    await page.selectOption("#library-select", "a.txt");
    await page.waitForTimeout(1200);
    assert.equal(await page.inputValue("#library-select"), "a.txt");
    assert.equal(
      await page.inputValue("#library-text"),
      "取消后保存",
      "迟到加载响应覆盖了当前文件内容",
    );
    assert.equal(await page.locator("#library-text").isEnabled(), true);
    assert.equal(await page.locator("#library-save").isEnabled(), true);
    await page.unroute("**/api/messages/b.txt");
    passed("R01 迟到加载响应不覆盖当前文件");

    await page.fill("#library-text", "冲突内容");
    const revision = await page.evaluate(() => state.libraryRevision);
    const outsideStatus = await page.evaluate(async (currentRevision) => {
      const response = await fetch("/api/messages/a.txt", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": state.csrf,
        },
        body: JSON.stringify({ text: "外部修改", revision: currentRevision }),
      });
      return response.status;
    }, revision);
    assert.equal(outsideStatus, 200, "测试未能制造版本冲突");
    await saveLibrary(page, 409);
    await page.waitForFunction(() =>
      document.getElementById("toast").textContent.includes("已被修改"),
    );
    assert.equal(await page.inputValue("#library-text"), "冲突内容");
    assert.equal(
      await page.evaluate(() => state.dirty.has("library")),
      true,
      "保存失败却清除了未保存标记",
    );
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "加载" }).click();
    await eventually(
      async () => (await page.inputValue("#library-text")) === "外部修改",
      "冲突后重新加载未取得服务端内容",
    );
    passed("R01 版本冲突保留未保存内容与 dirty");

    await page.route("**/api/messages/a.txt", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      await new Promise((resolve) => setTimeout(resolve, 700));
      return route.continue();
    });
    await page.fill("#library-text", "保存版本");
    const firstSave = librarySaveResponse(page);
    await page.getByRole("button", { name: "保存消息库" }).click();
    await page.waitForTimeout(150);
    await page.locator("#library-text").evaluate((element) => {
      element.focus();
      element.setSelectionRange(element.value.length, element.value.length);
    });
    await page.locator("#library-text").pressSequentially("追加");
    assert.equal((await firstSave).status(), 200);
    await eventually(
      async () =>
        (await text(page.locator("#library-info"))).includes(
          "当前修改尚未保存",
        ),
      "保存期间的新输入未保留 dirty",
    );
    assert.equal(
      await page.evaluate(() => state.dirty.has("library")),
      true,
      "迟到保存响应清除了 dirty",
    );
    await page.unroute("**/api/messages/a.txt");
    await saveLibrary(page);
    fileA = await getJSON(request, "/api/messages/a.txt");
    assert.equal(fileA.text, "保存版本追加");
    passed("R01 保存期间继续输入不被旧响应清 dirty");

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("#state").waitFor();
    await goPage(page, "content");
    await page.evaluate(() => {
      document.getElementById("library-select").value = "fresh.txt";
      const area = document.getElementById("library-text");
      area.value = "首次创建";
      area.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await saveLibrary(page);
    const fresh = await getJSON(request, "/api/messages/fresh.txt");
    assert.equal(fresh.text, "首次创建");
    passed("R01 首次未加载编辑可回退到当前选择保存");

    await page.selectOption("#library-select", "a.txt");
    await eventually(
      async () => (await page.inputValue("#library-text")) === "保存版本追加",
      "移除前未能重新加载 A",
    );
    await page.fill("#library-text", "移除前内容");
    await goPage(page, "groups");
    const groupRow = page
      .locator("#group-editor tr")
      .filter({ hasText: "anonymous_one" });
    await groupRow.getByRole("button", { name: "移除" }).click();
    await page.locator("#save-groups").click();
    await page.waitForFunction(() =>
      document.getElementById("toast").textContent.includes("配置已保存"),
    );
    await goPage(page, "content");
    assert.equal(
      await page.inputValue("#library-text"),
      "移除前内容",
      "下拉重建丢弃了未保存文本",
    );
    assert.match(
      await text(page.locator("#library-info")),
      /不在群配置中/,
      "绑定文件移除后没有明确提示",
    );
    assert.equal(
      await page.evaluate(() => state.libraryDetached),
      true,
      "绑定文件移除后没有重置保存对象",
    );
    await page.getByRole("button", { name: "保存消息库" }).click();
    await page.waitForFunction(() =>
      document.getElementById("toast").textContent.includes("没有可保存"),
    );
    fileA = await getJSON(request, "/api/messages/a.txt");
    assert.equal(fileA.text, "保存版本追加", "移除绑定后内容被写到其他文件");
    await page.screenshot({
      path: path.join(EVIDENCE, "r01-library-detached.png"),
    });
    passed("R01 下拉重建不换保存对象，绑定移除时保留文本并阻止误写");

    // ---------- R09：不指向不存在的恢复 ----------
    await goPage(page, "overview");
    await page.evaluate(() => {
      document.querySelector("details.group-details").open = true;
    });
    const unresolved = page
      .locator("#group-status tr")
      .filter({ hasText: "review_unused" });
    const manualGroupRow = page
      .locator("#group-status tr")
      .filter({ hasText: "人工暂停群" });
    const safetyGroupRow = page
      .locator("#group-status tr")
      .filter({ hasText: "安全暂停群" });
    assert.equal(
      await text(unresolved.locator("td").last()),
      "暂无操作",
      "未解析群仍指向不存在的恢复动作",
    );
    assert.equal(
      await page.locator("#group-status").getByText("见下方恢复").count(),
      0,
    );
    assert.match(await text(manualGroupRow.locator("td").last()), /需要处理/);
    assert.match(await text(safetyGroupRow.locator("td").last()), /需要处理/);
    const manualAttention = page
      .locator("#attention .attention-row")
      .filter({ hasText: "人工暂停群" });
    const safetyAttention = page
      .locator("#attention .attention-row")
      .filter({ hasText: "安全暂停群" });
    await manualAttention
      .getByRole("button", { name: "检查后明确恢复" })
      .waitFor();
    await safetyAttention
      .getByRole("button", { name: "检查后明确恢复" })
      .waitFor();
    const resumeCalls = [];
    page.on("request", (item) => {
      if (/\/api\/groups\/-?\d+\/resume$/.test(new URL(item.url()).pathname))
        resumeCalls.push(item.url());
    });
    page.once("dialog", (dialog) => {
      assert.match(dialog.message(), /确认恢复/);
      return dialog.dismiss();
    });
    await manualAttention
      .getByRole("button", { name: "检查后明确恢复" })
      .click();
    await page.waitForTimeout(300);
    assert.equal(resumeCalls.length, 0, "未确认的恢复仍发出请求");
    page.removeAllListeners("request");
    await page.screenshot({
      path: path.join(EVIDENCE, "r09-group-status.png"),
    });
    passed("R09 无操作行显示暂无操作，真实暂停对应现有恢复入口");

    const errorText = pageErrors.filter(
      (message) => !message.includes("net::ERR_FAILED"),
    );
    assert.deepEqual(
      errorText,
      [],
      `页面出现脚本错误: ${errorText.join(" | ")}`,
    );
    passed("浏览器页面无未处理脚本错误");

    fs.writeFileSync(
      path.join(EVIDENCE, "checks.json"),
      JSON.stringify(
        { baseURL: BASE, externalRequests: EXTERNAL, checks: CHECKS },
        null,
        2,
      ),
      "utf8",
    );
    console.log(`RESULT ${CHECKS.length} checks passed; evidence=${EVIDENCE}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error("FAIL", error.stack || error.message);
  process.exit(1);
});
