"use strict";

const state = {
  config: {},
  revision: "",
  csrf: "",
  snapshot: {},
  groups: [],
  dirty: new Set(),
  lotteryOffset: 0,
  auditOffset: 0,
  lotteryTotal: 0,
  auditTotal: 0,
  libraryRevision: "missing",
  libraryName: "",
  libraryDetached: false,
  libraryRequest: 0,
  libraryLoading: false,
  events: null,
  challenge: null,
};
const fields = {
  chat: [
    ["daily_limit", "每群每日文本上限", "number"],
    ["idle_threshold_minutes", "冷场阈值（分钟）", "number"],
    ["question_reply_pct", "问题参与概率（%）", "number"],
    ["discussion_reply_pct", "讨论与冷场概率（%）", "number"],
    ["reply_delay_min", "发送前等待下限（秒）", "number"],
    ["reply_delay_max", "发送前等待上限（秒）", "number"],
  ],
  lottery: [
    ["lottery_scan_interval_minutes", "扫描间隔（分钟）", "number"],
    [
      "daily_pinned_lottery_scan_enabled",
      "检查上海当日仍置顶的抽奖",
      "checkbox",
    ],
    ["lottery_per_group_daily_limit", "每群每日抽奖上限", "number"],
    ["lottery_account_daily_limit", "全账号每日抽奖上限", "number"],
    ["lottery_action_gap_min_seconds", "抽奖动作间隔下限（秒）", "number"],
    ["lottery_action_gap_max_seconds", "抽奖动作间隔上限（秒）", "number"],
  ],
  ai: [
    ["ai_enabled", "启用 AI 文本生成", "checkbox"],
    ["ai_consent", "已同意向配置的服务发送必要群聊文本", "checkbox"],
    ["ai_api_key", "AI API 密钥", "password"],
    ["ai_base_url", "AI 接口地址", "url"],
    ["ai_model", "模型", "text"],
    ["ai_prompt", "生成提示词", "textarea"],
    ["ai_context_count", "上下文条数", "number"],
    ["ai_temperature", "温度", "decimal"],
    ["ai_max_tokens", "最大输出 Token", "number"],
    ["ai_timeout", "超时（秒）", "number"],
  ],
  connection: [
    ["api_id", "Telegram API ID", "number"],
    ["api_hash", "Telegram API Hash", "password"],
    ["phone", "含国际区号的手机号", "tel"],
    ["proxy_host", "代理地址（可选）", "text"],
    ["proxy_port", "代理端口（不用时填0）", "number"],
    ["proxy_type", "代理类型", "select"],
  ],
  schedule: [
    ["schedule_enabled", "限制工作时段（上海时间）", "checkbox"],
    ["schedule_morning_start", "上午开始", "time"],
    ["schedule_morning_end", "上午结束", "time"],
    ["schedule_afternoon_start", "下午开始", "time"],
    ["schedule_afternoon_end", "下午结束", "time"],
  ],
};
const labels = {
  stopped: "已停止",
  starting: "正在连接",
  waiting_auth: "等待账号认证",
  running: "运行中",
  paused: "已暂停",
  stopping: "正在停止",
  SENT: "已发送",
  EXECUTED: "已执行",
  READY: "待执行",
  CALLING: "正在提交",
  UNKNOWN: "结果未知",
  FAILED: "平台拒绝",
  MANUAL: "需人工查看",
  PLANNED: "等待复核",
  EXPIRED: "已过期",
  BLOCKED: "已处理或额度不足",
  SUPERSEDED: "条件已变化",
  RECOVERED: "已从历史确认",
  chat: "普通参与",
  checkin: "每日签到",
  lottery: "抽奖",
  skip: "跳过",
  migration: "旧数据导入",
  chat_result: "普通参与结果",
  lottery_result: "抽奖结果",
  safety_pause: "安全停发",
  safety_resumed: "人工恢复",
  lottery_resumed: "抽奖恢复",
  checkin_recovered: "签到恢复",
};

/** 获取固定页面元素。 */
function id(name) {
  return document.getElementById(name);
}
/** 创建只含纯文本的节点，群消息绝不作为 HTML 解释。 */
function node(tag, text = "", className = "") {
  const value = document.createElement(tag);
  value.textContent = String(text ?? "");
  if (className) value.className = className;
  return value;
}
/** 将状态码映射为中文。 */
function label(value) {
  return labels[value] || value || "—";
}
/** 将时间显示为中文上海时区。 */
function dateText(value) {
  return value
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "—";
}
/** 展示不会覆盖表单的反馈。 */
function toast(text, error = false) {
  const box = id("toast");
  box.textContent = text;
  box.className = error ? "error" : "";
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    box.hidden = true;
  }, 6000);
}
/** 同源 API 与每会话写标识。 */
async function api(url, method = "GET", body) {
  const response = await fetch(url, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || "请求失败");
    error.field = data.field;
    throw error;
  }
  return data;
}
/** 不让事件回调的异常变成无提示失败。 */
async function attempt(operation) {
  try {
    await operation();
  } catch (error) {
    toast(error.message, true);
    if (error.field)
      document
        .querySelector(`[data-key="${CSS.escape(error.field)}"]`)
        ?.focus();
  }
}
/** 切换工作区，保留未提交内容。 */
function page(name) {
  document.querySelectorAll(".page").forEach((section) => {
    section.hidden = section.id !== name;
  });
  document
    .querySelectorAll("[data-page]")
    .forEach((button) =>
      button.classList.toggle("selected", button.dataset.page === name),
    );
  id("mobile-page").value = name;
  document
    .querySelectorAll("nav [data-page]")
    .forEach((button) =>
      button.setAttribute(
        "aria-current",
        button.dataset.page === name ? "page" : "false",
      ),
    );
  if (name === "audit") attempt(loadAudit);
  if (name === "lottery") attempt(loadLotteries);
}
/** 按完整服务端字段模型创建输入。 */
function renderForm(name) {
  const root = id(`${name}-fields`);
  root.replaceChildren();
  for (const [key, title, type] of fields[name]) {
    const wrapper = node(
      "div",
      "",
      type === "textarea" ? "field full" : "field",
    );
    const caption = node("label", title);
    caption.htmlFor = key;
    let input;
    if (type === "textarea") {
      input = node("textarea");
      input.rows = 5;
    } else if (type === "select") {
      input = node("select");
      for (const value of ["http", "socks5", "socks4"]) {
        const option = node("option", value.toUpperCase());
        option.value = value;
        input.append(option);
      }
    } else {
      input = node("input");
      input.type = type === "decimal" ? "number" : type;
      if (type === "decimal") input.step = "0.1";
    }
    input.id = key;
    input.dataset.key = key;
    if (type === "checkbox") input.checked = Boolean(state.config[key]);
    else input.value = state.config[key] ?? "";
    if (type === "password") {
      input.value = "";
      input.autocomplete = "new-password";
      input.placeholder = state.config[`${key}_set`]
        ? "已设置；留空保持不变"
        : "尚未设置";
    }
    input.addEventListener("input", () => state.dirty.add(name));
    wrapper.append(caption, input);
    root.append(wrapper);
  }
}
/** 收集当前表单明确字段，不把缺值偷偷替换为默认。 */
function collect(name) {
  const patch = {};
  id(`${name}-form`)
    .querySelectorAll("[data-key]")
    .forEach((input) => {
      const key = input.dataset.key;
      patch[key] =
        input.type === "checkbox"
          ? input.checked
          : input.type === "number"
            ? Number(input.value)
            : input.value;
    });
  return patch;
}
/** 带版本提交，其他页面未保存内容不被重绘覆盖。 */
async function save(patch, section) {
  const response = await api("/api/config", "POST", {
    revision: state.revision,
    patch,
  });
  state.config = response.values;
  state.revision = response.revision;
  state.dirty.delete(section);
  if (fields[section]) renderForm(section);
  if (section === "groups") {
    state.groups = structuredClone(state.config.groups);
    renderGroupEditor();
    libraries();
  }
  await refresh();
  toast("配置已保存；运行中其他参数在重新开始后生效");
}
/** 创建可编辑的群任务表，不拼接用户 HTML。 */
function renderGroupEditor() {
  const root = id("group-editor");
  root.replaceChildren();
  for (const [index, group] of state.groups.entries()) {
    const row = node("tr");
    row.append(node("td", group.ref));
    for (const key of ["chat", "checkin", "lottery", "authorized"]) {
      const cell = node("td");
      const check = node("input");
      check.type = "checkbox";
      check.checked = group[key];
      check.setAttribute(
        "aria-label",
        `${group.ref} ${key === "authorized" ? "已获管理员许可" : label(key)}`,
      );
      check.addEventListener("change", () => {
        group[key] = check.checked;
        state.dirty.add("groups");
      });
      cell.append(check);
      row.append(cell);
    }
    const cell = node("td");
    const input = node("input");
    input.value = group.messages;
    input.setAttribute("aria-label", "消息文件名");
    input.addEventListener("input", () => {
      group.messages = input.value;
      state.dirty.add("groups");
    });
    cell.append(input);
    row.append(cell);
    const actions = node("td");
    const remove = node("button", "移除");
    remove.addEventListener("click", () => {
      state.groups.splice(index, 1);
      state.dirty.add("groups");
      renderGroupEditor();
    });
    actions.append(remove);
    row.append(actions);
    root.append(row);
  }
}
/** 添加别名；授权确认与自动任务默认保持关闭。 */
function addGroups() {
  for (let ref of id("group-input")
    .value.split(/[,，\n]+/)
    .map((part) => part.trim())
    .filter(Boolean)) {
    if (ref.startsWith("@")) ref = `https://t.me/${ref.slice(1)}`;
    if (ref.startsWith("t.me/")) ref = `https://${ref}`;
    if (!state.groups.some((group) => group.ref === ref))
      state.groups.push({
        ref,
        chat: true,
        checkin: false,
        lottery: false,
        authorized: false,
        messages: "messages.txt",
      });
  }
  id("group-input").value = "";
  state.dirty.add("groups");
  renderGroupEditor();
}
/** 从持久快照渲染，不冒充会话计数。 */
function renderSnapshot(snapshot) {
  state.snapshot = snapshot;
  id("state").textContent = label(snapshot.state);
  id("runtime-description").textContent =
    {
      stopped: "当前不会执行新任务，开始后才会连接账号。",
      starting: "正在连接账号并检查授权群。",
      waiting_auth: "请在下方完成本次账号认证。",
      running: "正在按授权群与任务设置运行。",
      paused: "已暂停新的自动动作，恢复后继续。",
      stopping: "正在等待后台任务安全退出。",
    }[snapshot.state] || "请查看运行动态。";
  id("count-unknown").classList.toggle(
    "needs-attention",
    snapshot.counts.unknown > 0,
  );
  id("state-dot").className = `dot ${snapshot.state}`;
  const active = snapshot.state !== "stopped";
  for (const [key, shown] of Object.entries({
    start: !active,
    pause: snapshot.state === "running",
    resume: snapshot.state === "paused",
    stop: active && snapshot.state !== "stopping",
  }))
    id(key).hidden = !shown;
  id("pending-config").hidden = !snapshot.pending_config;
  for (const key of ["chat", "checkin", "lottery", "unknown"])
    id(`count-${key}`).textContent = snapshot.counts[key];
  state.challenge = snapshot.challenge;
  id("auth").hidden = !snapshot.challenge;
  if (snapshot.challenge)
    id("auth-title").textContent =
      snapshot.challenge.kind === "password"
        ? "需要 Telegram 两步验证密码"
        : "需要 Telegram 验证码";
  const root = id("group-status");
  root.replaceChildren();
  for (const group of snapshot.groups)
    root.append(groupStatusRow(group, snapshot));
  if (!snapshot.groups.length)
    emptyRow(root, 7, "先添加授权群，再选择需要的任务");
  renderScanStatus(snapshot);
  renderAttention(snapshot);
  renderTaskBoard(snapshot);
}
/** 三类任务共享同一份状态口径，避免卡片与明细表出现矛盾。 */
function taskStatuses(group, snapshot) {
  const status = group.state;
  const shared =
    snapshot.state === "paused"
      ? "全局暂停"
      : status.pause === "manual"
        ? "人工暂停"
        : status.pause === "safety"
          ? "安全暂停"
          : snapshot.state === "running"
            ? "观察中"
            : "待开始";
  const chat = !group.chat
    ? "已关闭"
    : status.pause === "unanswered" && status.pause_day === snapshot.today
      ? "无人回应暂停"
      : shared;
  const sign = !group.checkin
    ? "已关闭"
    : status.checkin_day === snapshot.today
      ? label(status.checkin_status)
      : "等待当日任务";
  const lottery = !group.lottery
    ? "已关闭"
    : group.frozen
      ? "结果未知，已冻结"
      : `${group.lottery_used} 次 / ${shared === "观察中" ? "按间隔扫描" : shared}`;
  return [chat, sign, lottery];
}
/** 将现有快照按独立任务展示，群名和状态保持纯文本。 */
function renderTaskBoard(snapshot) {
  for (const [index, kind] of ["chat", "checkin", "lottery"].entries()) {
    const root = id(`task-${kind}`);
    root.replaceChildren();
    const groups = snapshot.groups.filter((group) => group[kind]);
    for (const group of groups) {
      const item = node("div", "", "task-item");
      const title = node("strong", group.title);
      const status = node(
        "span",
        group.authorized
          ? taskStatuses(group, snapshot)[index]
          : "尚未确认群许可",
        "task-status",
      );
      if ((kind === "lottery" && group.frozen) || !group.authorized)
        status.classList.add("warning");
      item.append(title, status);
      root.append(item);
    }
    if (!groups.length)
      root.append(node("p", "尚未为群启用该任务", "task-empty"));
    id(`task-${kind}-total`).textContent = `${groups.length} 个群已配置`;
  }
}
/** 每群一行明确区分功能关闭、全局暂停、单群暂停与抽奖冻结。 */
function groupStatusRow(group, snapshot) {
  const row = node("tr");
  const status = group.state;
  const [chat, sign, lottery] = taskStatuses(group, snapshot);
  const identity = node("td");
  const groupLink = node("a", group.title);
  groupLink.href = group.ref;
  groupLink.target = "_blank";
  groupLink.rel = "noopener noreferrer";
  identity.append(groupLink);
  row.append(
    identity,
    node("td", chat),
    node("td", sign),
    node("td", lottery),
    node("td", `${group.text_used} / ${state.config.daily_limit}`),
    node(
      "td",
      !group.authorized
        ? "尚未确认群许可"
        : status.pause_reason ||
            group.scan.error ||
            status.last_decision ||
            "按参与策略调度",
    ),
  );
  const actions = node("td");
  if (group.peer != null && !["safety", "manual"].includes(status.pause)) {
    const button = node("button", "暂停该群");
    button.addEventListener("click", () =>
      attempt(async () => {
        if (!window.confirm("暂停该群全部自动外发？其他群不受影响。")) return;
        await api(`/api/groups/${group.peer}/pause`, "POST", {});
        await refresh();
      }),
    );
    actions.append(button);
  } else if (group.peer != null) actions.textContent = "在“需要处理”中恢复";
  else actions.textContent = "暂无操作";
  row.append(actions);
  return row;
}
/** 扫描失败时间也可见，不能把没有发现误报成没有扫描。 */
function renderScanStatus(snapshot) {
  const root = id("scan-status");
  root.replaceChildren();
  for (const group of snapshot.groups) {
    if (!group.lottery) continue;
    const scan = group.scan;
    const row = node("tr");
    row.append(
      node("td", group.title),
      node("td", dateText(scan.last_attempt)),
      node(
        "td",
        snapshot.state === "running" ? dateText(scan.next_scan) : "开始后恢复",
      ),
      node("td", scan.found ?? 0),
      node(
        "td",
        group.frozen
          ? "结果未知，需人工检查"
          : scan.error ||
              (scan.cursor == null ? "尚未建立基线" : "扫描游标已保存"),
      ),
    );
    root.append(row);
  }
  if (!root.children.length) emptyRow(root, 5, "没有启用抽奖的群");
}
/** 只在明确确认后恢复对应安全状态。 */
function renderAttention(snapshot) {
  const root = id("attention");
  root.replaceChildren();
  if (snapshot.error) root.append(node("p", snapshot.error, "error"));
  for (const group of snapshot.groups) {
    if (group.state.pause === "safety" || group.state.pause === "manual")
      addAttention(
        root,
        group,
        group.state.pause === "manual" ? "人工暂停" : "安全暂停",
        "safety",
      );
    if (group.frozen) addAttention(root, group, "抽奖结果未知", "lottery");
  }
  if (!root.children.length)
    root.append(node("p", "暂无需要人工处理的冻结或安全暂停", "muted"));
}
/** 恢复按钮与普通打开消息是不同操作。 */
function addAttention(root, group, text, kind) {
  const row = node("div", "", "attention-row");
  row.append(node("span", `${group.title} · ${text}`));
  const button = node("button", "检查后明确恢复");
  button.addEventListener("click", () =>
    attempt(async () => {
      const confirm = window.prompt(
        "请先检查原因。恢复不代表旧动作成功，也不会自动重试未知动作。输入：确认恢复",
      );
      if (confirm !== "确认恢复") return;
      await api(`/api/groups/${group.peer}/resume`, "POST", { kind, confirm });
      await refresh();
    }),
  );
  row.append(button);
  root.append(row);
}
/** 空列表必须是明确空状态。 */
function emptyRow(root, columns, text) {
  const row = node("tr");
  const cell = node("td", text, "empty");
  cell.colSpan = columns;
  row.append(cell);
  root.append(row);
}
/** 先识别全局事件，再按 peer 显示已知群名。 */
function groupName(peer) {
  if (peer == null) return "全局";
  return (
    state.snapshot.groups?.find((group) => group.peer === peer)?.title ||
    `群 ${peer}`
  );
}
/** 刷新不覆盖未保存的表单。 */
async function refresh() {
  renderSnapshot(await api("/api/snapshot"));
}
/** 服务重启后刷新写标识；未保存表单保留旧版本以避免覆盖冲突。 */
async function syncSession() {
  const data = await api("/api/bootstrap");
  state.csrf = data.csrf;
  if (!state.dirty.size && data.config.revision !== state.revision) {
    state.config = data.config.values;
    state.revision = data.config.revision;
    state.groups = structuredClone(state.config.groups);
    for (const name of Object.keys(fields)) renderForm(name);
    renderGroupEditor();
    libraries();
  }
  renderSnapshot(data.snapshot);
}
/** 带总数的抽奖分页。 */
async function loadLotteries() {
  const page = await api(
    `/api/records/lotteries?offset=${state.lotteryOffset}&limit=20&pending=${id("only-pending").checked}`,
  );
  state.lotteryTotal = page.total;
  const root = id("lottery-rows");
  root.replaceChildren();
  for (const record of page.items) {
    const row = node("tr");
    row.append(
      node("td", dateText(record.created_at)),
      node("td", groupName(record.peer)),
      node("td", label(record.status)),
      node("td", record.reason),
    );
    const actions = node("td");
    const open = node("button", "打开原消息");
    open.addEventListener("click", () =>
      attempt(async () => {
        const target = window.open("about:blank", "_blank");
        if (!target) {
          toast("浏览器阻止了新窗口，请允许本地页面打开 Telegram 消息", true);
          return;
        }
        target.opener = null;
        try {
          const result = await api(
            `/api/lotteries/${record.id}/open`,
            "POST",
            {},
          );
          target.location.href = result.url;
        } catch (error) {
          target.close();
          throw error;
        }
      }),
    );
    actions.append(open);
    if (record.pending) {
      const viewed = node("button", "标记已查看");
      viewed.addEventListener("click", () =>
        attempt(async () => {
          if (
            !window.confirm(
              "标记为已查看？只关闭提醒，不会恢复冻结或改变外发结果。",
            )
          )
            return;
          await api(`/api/lotteries/${record.id}/viewed`, "POST", {
            confirm: true,
          });
          await loadLotteries();
          toast("已标记为已查看");
        }),
      );
      actions.append(viewed);
    }
    row.append(actions);
    root.append(row);
  }
  if (!page.items.length) emptyRow(root, 5, "暂无符合条件的抽奖记录");
  pager("lottery", state.lotteryOffset, page.total);
}
/** 审计分页和外发账本都从数据库读取。 */
async function loadAudit() {
  const page = await api(
    `/api/records/audit?offset=${state.auditOffset}&limit=30`,
  );
  state.auditTotal = page.total;
  const root = id("audit-rows");
  root.replaceChildren();
  for (const record of page.items) {
    const row = node("tr");
    row.append(
      node("td", dateText(record.occurred_at)),
      node("td", groupName(record.peer)),
      node("td", label(record.event)),
      node("td", label(record.reason)),
    );
    root.append(row);
  }
  if (!page.items.length) emptyRow(root, 4, "暂无审计记录");
  pager("audit", state.auditOffset, page.total);
  const actions = await api("/api/records/actions?limit=30");
  const actionRoot = id("action-rows");
  actionRoot.replaceChildren();
  for (const record of actions.items) {
    const row = node("tr");
    row.append(
      node("td", dateText(record.created_at)),
      node("td", groupName(record.peer)),
      node("td", label(record.kind)),
      node("td", label(record.status)),
      node("td", record.reason),
    );
    actionRoot.append(row);
  }
  if (!actions.items.length) emptyRow(actionRoot, 5, "暂无外发记录");
}
/** 控制分页按钮，避免总数与枚举不一致。 */
function pager(kind, offset, total) {
  const size = kind === "audit" ? 30 : 20;
  id(`${kind}-page`).textContent =
    `共 ${total} 条 · 第 ${Math.floor(offset / size) + 1} 页`;
  id(`${kind}-prev`).disabled = offset === 0;
  id(`${kind}-next`).disabled = offset + size >= total;
}
/** 消息库候选仅来自群配置；重建时保留正在编辑的绑定对象。 */
function libraries() {
  const select = id("library-select");
  const previous = select.value;
  if (state.libraryLoading) {
    state.libraryRequest += 1;
    setLibraryLoading(false);
  }
  const names = new Set([
    "messages.txt",
    ...state.config.groups.map((group) => group.messages),
  ]);
  select.replaceChildren();
  for (const name of names) {
    const option = node("option", name);
    option.value = name;
    select.append(option);
  }
  if (names.has(state.libraryName || previous))
    select.value = state.libraryName || previous;
  if (state.libraryName && !names.has(state.libraryName)) {
    state.libraryName = "";
    state.libraryRevision = "missing";
    state.libraryDetached = true;
    id("library-info").textContent =
      "原消息文件已不在群配置中；内容仍保留，请重新选择并加载后再保存。";
  }
}
/** 加载期间阻止输入或保存旧内容，下拉仍允许取消或改选。 */
function setLibraryLoading(loading) {
  state.libraryLoading = loading;
  id("library-text").disabled = loading;
  id("library-save").disabled = loading;
}
/** 明确展示旧格式转换后的消息条数；取消或失败不改变编辑对象。 */
async function loadLibrary() {
  const select = id("library-select");
  if (
    state.dirty.has("library") &&
    !window.confirm("消息库有未保存修改，确定重新加载？")
  ) {
    state.libraryRequest += 1;
    select.value = state.libraryName;
    setLibraryLoading(false);
    return;
  }
  const name = select.value;
  if (!name) return;
  const request = ++state.libraryRequest;
  setLibraryLoading(true);
  try {
    const data = await api(`/api/messages/${encodeURIComponent(name)}`);
    if (request !== state.libraryRequest) return;
    id("library-text").value = data.text;
    state.libraryName = data.name;
    state.libraryRevision = data.revision;
    state.libraryDetached = false;
    state.dirty.delete("library");
    id("library-info").textContent =
      `${data.count} 条消息。${data.legacy ? "当前为旧逗号格式的转换预览，保存后改为一行一条。" : "每行一条，保留句中的逗号。"}`;
  } catch (error) {
    if (request !== state.libraryRequest) return;
    select.value = state.libraryName;
    throw error;
  } finally {
    if (request === state.libraryRequest) setLibraryLoading(false);
  }
}
/** 事件代际由服务端明确提供，重连先恢复完整快照。 */
function connectEvents() {
  state.events?.close();
  state.events = new EventSource("/api/events");
  state.events.onopen = () => {
    id("offline").hidden = true;
    id("connection").textContent = "页面已连接";
    attempt(syncSession);
  };
  state.events.onerror = () => {
    id("offline").hidden = false;
    id("connection").textContent = "后台状态需重新确认";
  };
  state.events.addEventListener("snapshot", (event) =>
    renderSnapshot(JSON.parse(event.data)),
  );
  state.events.onmessage = (event) => {
    const data = JSON.parse(event.data);
    const root = id("events");
    const line = node("div", "", "event");
    line.append(
      node("time", dateText(data.at)),
      node("span", `${groupName(data.peer)} · ${data.message}`),
    );
    root.prepend(line);
    while (root.children.length > 150) root.lastElementChild.remove();
    clearTimeout(connectEvents.timer);
    connectEvents.timer = setTimeout(() => attempt(refresh), 200);
  };
}
/** 初始装配只读取状态，不自动开始账号任务。 */
async function initialize() {
  const data = await api("/api/bootstrap");
  state.csrf = data.csrf;
  state.config = data.config.values;
  state.revision = data.config.revision;
  state.groups = structuredClone(state.config.groups);
  id("version").textContent = `Be Water ${data.version}`;
  for (const name of Object.keys(fields)) renderForm(name);
  renderGroupEditor();
  libraries();
  renderSnapshot(data.snapshot);
  if (data.migration !== "ready") {
    id("migration").hidden = false;
    id("migration").textContent =
      "旧数据迁移阻断：请保留源文件并处理冲突，不要重复开始。";
  }
  if (!state.config.api_id) page("settings");
  connectEvents();
  await loadLotteries();
  setInterval(() => attempt(refresh), 5000);
}

/** 绑定明确入口，防止重新渲染时重复注册后台操作。 */
function bind() {
  id("mobile-page").addEventListener("change", (event) =>
    page(event.target.value),
  );
  document
    .querySelectorAll("[data-page],[data-go]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        page(button.dataset.page || button.dataset.go),
      ),
    );
  for (const name of Object.keys(fields))
    id(`${name}-form`).addEventListener("submit", (event) => {
      event.preventDefault();
      attempt(() => save(collect(name), name));
    });
  document.querySelectorAll("[data-control]").forEach((button) =>
    button.addEventListener("click", () =>
      attempt(async () => {
        const action = button.dataset.control;
        if (action === "stop" && !window.confirm("确定停止全部后台任务？"))
          return;
        await api(`/api/control/${action}`, "POST", {});
        await refresh();
      }),
    ),
  );
  id("add-groups").addEventListener("click", addGroups);
  id("save-groups").addEventListener("click", () =>
    attempt(() => save({ groups: state.groups }, "groups")),
  );
  id("refresh").addEventListener("click", () => attempt(refresh));
  id("refresh-audit").addEventListener("click", () => attempt(loadAudit));
  id("clear-events").addEventListener("click", () =>
    id("events").replaceChildren(),
  );
  id("auth-form").addEventListener("submit", (event) => {
    event.preventDefault();
    attempt(async () => {
      if (!state.challenge) return;
      const answer = id("auth-answer").value;
      id("auth-answer").value = "";
      await api("/api/auth", "POST", { id: state.challenge.id, answer });
      toast("认证内容已提交");
    });
  });
  id("only-pending").addEventListener("change", () => {
    state.lotteryOffset = 0;
    attempt(loadLotteries);
  });
  for (const kind of ["lottery", "audit"])
    for (const direction of ["prev", "next"])
      id(`${kind}-${direction}`).addEventListener("click", () => {
        const key = `${kind}Offset`;
        const size = kind === "audit" ? 30 : 20;
        state[key] = Math.max(
          0,
          state[key] + (direction === "next" ? size : -size),
        );
        attempt(kind === "audit" ? loadAudit : loadLotteries);
      });
  id("library-load").addEventListener("click", () => attempt(loadLibrary));
  id("library-select").addEventListener("change", () => attempt(loadLibrary));
  id("library-text").addEventListener("input", () => {
    if (!state.libraryName && !state.libraryDetached)
      state.libraryName = id("library-select").value;
    state.dirty.add("library");
  });
  id("library-save").addEventListener("click", () =>
    attempt(async () => {
      const name = state.libraryName || id("library-select").value;
      if (!name || state.libraryDetached) {
        toast("当前内容没有可保存的消息文件，请重新选择并加载", true);
        return;
      }
      const text = id("library-text").value;
      const revision = state.libraryRevision;
      const request = ++state.libraryRequest;
      state.libraryName = name;
      const data = await api(
        `/api/messages/${encodeURIComponent(name)}`,
        "POST",
        { text, revision },
      );
      if (request !== state.libraryRequest || state.libraryName !== name)
        return;
      state.libraryRevision = data.revision;
      if (id("library-text").value === text) {
        state.dirty.delete("library");
        id("library-info").textContent = `已保存 ${data.count} 条消息`;
        toast("消息库已保存");
      } else {
        id("library-info").textContent =
          `已保存请求时的 ${data.count} 条消息；当前修改尚未保存`;
        toast("已保存请求时的内容；当前输入尚未保存");
      }
    }),
  );
  document.querySelectorAll("[data-clear-secret]").forEach((button) =>
    button.addEventListener("click", () =>
      attempt(async () => {
        if (window.confirm("确定清除已保存密钥？此操作需要先停止任务。"))
          await save(
            { clear_secrets: [button.dataset.clearSecret] },
            button.dataset.clearSecret === "api_hash" ? "connection" : "ai",
          );
      }),
    ),
  );
  id("cleanup-preview").addEventListener("click", () =>
    attempt(async () => {
      const data = await api("/api/cleanup", "POST", {
        before: id("cleanup-date").value,
        preview: true,
      });
      id("cleanup-count").textContent =
        `将清理 ${data.lotteries} 条抽奖记录；关联动作也会清理，旧内容可能再次被识别。`;
      id("cleanup-run").disabled = false;
    }),
  );
  id("cleanup-date").addEventListener("change", () => {
    id("cleanup-run").disabled = true;
  });
  id("cleanup-run").addEventListener("click", () =>
    attempt(async () => {
      const confirm = window.prompt(
        "清理会移除部分去重信息，旧抽奖可能再次被处理。输入：确认清理",
      );
      if (confirm !== "确认清理") return;
      await api("/api/cleanup", "POST", {
        before: id("cleanup-date").value,
        confirm,
      });
      id("cleanup-run").disabled = true;
      state.lotteryOffset = 0;
      await loadLotteries();
      toast("历史已清理");
    }),
  );
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty.size) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
}

bind();
attempt(initialize);
