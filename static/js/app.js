"use strict";

const appState = {
  config: {},
  dashboard: {state: "idle", total: 0, groups: [], alerts: []},
  lotteryPage: {offset: 0, limit: 50, total: 0, items: []},
  dirty: false,
  configured: null,
  evtSource: null,
  toastTimer: null,
};

const byId = (id) => document.getElementById(id);
const intValue = (id, fallback) => {
  const value = Number.parseInt(byId(id)?.value ?? "", 10);
  return Number.isFinite(value) ? value : fallback;
};
const floatValue = (id, fallback) => {
  const value = Number.parseFloat(byId(id)?.value ?? "");
  return Number.isFinite(value) ? value : fallback;
};

function showToast(message, type = "") {
  const toast = byId("toast");
  toast.textContent = message;
  toast.className = `toast ${type} show`;
  clearTimeout(appState.toastTimer);
  appState.toastTimer = setTimeout(() => { toast.className = "toast"; }, 3000);
}

function setView(name) {
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
  byId(`view${name.charAt(0).toUpperCase()}${name.slice(1)}`)?.classList.add("active");
  document.querySelector(`.nav-item[data-view="${name}"]`)?.classList.add("active");
  if (name === "audit") loadAudit();
}

function updateStatus(state) {
  appState.dashboard.state = state;
  const labels = {
    idle: "未运行", starting: "正在启动", running: "运行中", pausing: "正在暂停",
    paused: "已暂停", stopping: "正在停止", stopped: "已停止", waiting_code: "等待验证码",
  };
  byId("statusText").textContent = labels[state] || state;
  byId("statusDot").className = `status-dot ${state}`;
  const active = ["starting", "running", "pausing", "paused", "waiting_code"].includes(state);
  byId("btnStart").hidden = active;
  byId("btnPause").hidden = state !== "running";
  byId("btnResume").hidden = state !== "paused";
  byId("btnStop").hidden = !active;
}

function groupName(group) {
  return group.replace(/\/+$/, "").split("/").pop().replace(/^@/, "") || group;
}

function normalizeGroupLink(group) {
  const value = String(group || "").trim().replace(/\/+$/, "");
  if (value.startsWith("@")) return `https://t.me/${value.slice(1)}`;
  if (value.startsWith("t.me/")) return `https://${value}`;
  return value;
}

function stateBadge(group) {
  if (group.paused) return `<span class="badge paused">● ${escapeHtml(group.pause_kind)}</span>`;
  return '<span class="badge">● 观察中</span>';
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value ?? "");
  return div.innerHTML;
}

function renderDashboard() {
  const dashboard = appState.dashboard;
  const groups = dashboard.groups || [];
  const paused = groups.filter((group) => group.paused);
  byId("metricTotal").textContent = dashboard.total || 0;
  byId("sidebarTotal").textContent = dashboard.total || 0;
  byId("metricWatching").textContent = groups.length - paused.length;
  byId("metricPaused").textContent = paused.length;
  byId("metricLimit").textContent = `${groups.length} 群 · 每群 ${appState.config.daily_limit || 30} 条上限`;
  const rows = byId("overviewGroupRows");
  if (!groups.length) {
    rows.innerHTML = '<tr><td colspan="4" class="empty">尚未配置授权群组</td></tr>';
  } else {
    rows.innerHTML = groups.map((group) => `<tr>
      <td>${escapeHtml(groupName(group.group))}</td><td>${stateBadge(group)}</td>
      <td>${group.sent_count} / ${appState.config.daily_limit || 30}</td>
      <td>${escapeHtml(group.pause_reason || "等待新活动")}</td></tr>`).join("");
  }
  const alertList = byId("alertList");
  if (!paused.length) {
    alertList.innerHTML = '<div class="empty">暂无告警</div>';
  } else {
    alertList.innerHTML = paused.map((group) => `<div class="alert-item"><strong>${escapeHtml(groupName(group.group))}</strong><span>${escapeHtml(group.pause_reason || group.pause_kind)}</span>${group.pause_kind === "safety" || group.pause_kind === "manual" ? `<button class="button secondary resume-group" data-group="${escapeHtml(group.group)}">检查后恢复</button>` : ""}</div>`).join("");
  }
  updateStatus(dashboard.state || "idle");
}

function renderLotteryAlerts() {
  const page = appState.lotteryPage;
  byId("lotteryAlertTotal").textContent = page.total;
  byId("lotteryPageInfo").textContent = `第 ${Math.floor(page.offset / page.limit) + 1} 页，共 ${page.total} 条`;
  byId("lotteryPrev").disabled = page.offset === 0;
  byId("lotteryNext").disabled = page.offset + page.limit >= page.total;
  const list = byId("lotteryAlertList");
  if (!page.items.length) { list.innerHTML = '<div class="empty">暂无待处理抽奖告警</div>'; return; }
  list.innerHTML = page.items.map((item) => `<div class="alert-item"><strong>抽奖 #${escapeHtml(item.id)}</strong><span>${escapeHtml(item.summary || item.status)}</span><button class="button secondary open-lottery" data-id="${escapeHtml(item.id)}">打开消息</button></div>`).join("");
}

async function api(url, options = {}) {
  const response = await fetch(url, {headers: {"Content-Type": "application/json"}, ...options});
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.success === false) {
    const error = new Error(data.detail || data.error || `HTTP ${response.status}`);
    error.field = data.field;
    throw error;
  }
  return data;
}

async function loadHealth() {
  // 拉取进程内健康状态；网络异常时保守按已配置处理，避免误导已配置用户跳转
  try {
    const data = await api("/api/health");
    appState.configured = Boolean(data.configured);
  } catch (error) {
    appState.configured = true;
  }
  return appState.configured;
}

async function loadDashboard() {
  try {
    const data = await api("/api/dashboard");
    appState.dashboard = data.dashboard;
    renderDashboard();
    if (data.dashboard.lottery_alert_total !== undefined) byId("lotteryAlertTotal").textContent = data.dashboard.lottery_alert_total;
  } catch (error) {
    showToast(`状态加载失败：${error.message}`, "error");
  }
}

async function loadLotteryAlerts(offset = appState.lotteryPage.offset) {
  try {
    const data = await api(`/api/lotteries?offset=${offset}&limit=${appState.lotteryPage.limit}`);
    appState.lotteryPage = {offset: data.offset, limit: data.limit, total: data.total, items: data.items};
    renderLotteryAlerts();
  } catch (error) { showToast(`抽奖告警加载失败：${error.message}`, "error"); }
}

async function openLottery(id) {
  try {
    const data = await api(`/api/lotteries/${encodeURIComponent(id)}/open`, {method: "POST", body: "{}"});
    window.open(data.url, "_blank", "noopener");
    if (data.fallback) showToast(`无法生成消息深链，请在群消息 ID ${data.message_id} 处查看`);
    else showToast("已打开消息；请自行完成核验", "success");
    await loadLotteryAlerts();
    await loadDashboard();
  } catch (error) { showToast(`抽奖操作失败：${error.message}`, "error"); }
}

function setInput(id, value) {
  const element = byId(id);
  if (element && value !== undefined && value !== null) element.value = value;
}

function rebuildMessageFiles() {
  const container = byId("messageFiles");
  const groups = byId("target_groups").value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
  const files = appState.config.message_files || {};
  if (!groups.length) {
    container.innerHTML = '<div class="empty">请先配置授权群组</div>';
    return;
  }
  container.innerHTML = "";
  groups.forEach((group) => {
    const normalizedGroup = normalizeGroupLink(group);
    const row = document.createElement("div");
    row.className = "file-row";
    const label = document.createElement("span");
    label.textContent = groupName(group);
    const input = document.createElement("input");
    input.dataset.group = normalizedGroup;
    input.value = files[normalizedGroup] || files[group] || "";
    input.placeholder = "例如 messages.txt";
    input.addEventListener("input", markDirty);
    row.append(label, input);
    container.append(row);
  });
}

function toggleConditionalFields() {
  byId("aiFields").hidden = !byId("ai_enabled").checked;
  byId("scheduleFields").hidden = !byId("schedule_enabled").checked;
}

async function loadConfig() {
  try {
    const data = await api("/api/config");
    const config = data.config;
    appState.config = config;
    setInput("api_id", config.api_id);
    setInput("phone", config.phone);
    setInput("target_groups", (config.target_groups || []).join("\n"));
    byId("api_hash").placeholder = config.api_hash_set ? "已保存；留空保持不变" : "请输入 API Hash";
    byId("ai_api_key").placeholder = config.ai_api_key_set ? "已保存；留空保持不变" : "请输入 API Key";
    ["daily_limit", "idle_threshold_minutes", "question_reply_pct", "discussion_reply_pct", "reply_delay_min", "reply_delay_max", "ai_base_url", "ai_model", "ai_prompt", "ai_context_count", "ai_temperature", "ai_max_tokens", "proxy_host", "proxy_port", "proxy_type", "schedule_morning_start", "schedule_morning_end", "schedule_afternoon_start", "schedule_afternoon_end"].forEach((id) => setInput(id, config[id]));
    byId("ai_enabled").checked = Boolean(config.ai_enabled);
    byId("schedule_enabled").checked = Boolean(config.schedule_enabled);
    toggleConditionalFields();
    rebuildMessageFiles();
    appState.dirty = false;
  } catch (error) {
    showToast(`配置加载失败：${error.message}`, "error");
  }
}

function validateConfig() {
  document.querySelectorAll(".field-error").forEach((item) => { item.textContent = ""; });
  document.querySelectorAll("input.invalid").forEach((item) => item.classList.remove("invalid"));
  const errors = [];
  const check = (id, valid, message) => {
    if (valid) return;
    const input = byId(id);
    input.classList.add("invalid");
    input.closest(".field")?.querySelector(".field-error")?.append(message);
    errors.push(message);
  };
  check("daily_limit", intValue("daily_limit", 0) > 0, "每日上限必须大于 0");
  check("idle_threshold_minutes", intValue("idle_threshold_minutes", 0) > 0, "冷场阈值必须大于 0");
  ["question_reply_pct", "discussion_reply_pct"].forEach((id) => check(id, intValue(id, -1) >= 0 && intValue(id, 101) <= 100, "概率必须为 0–100"));
  check("reply_delay_min", intValue("reply_delay_min", -1) >= 0, "等待时间不能为负数");
  check("reply_delay_max", intValue("reply_delay_max", -1) >= intValue("reply_delay_min", 0), "上限必须不小于下限");
  if (!byId("target_groups").value.trim()) errors.push("至少配置一个授权群组");
  return errors;
}

function collectConfig() {
  const messageFiles = {};
  document.querySelectorAll("#messageFiles input[data-group]").forEach((input) => {
    if (input.value.trim()) messageFiles[input.dataset.group] = input.value.trim();
  });
  return {
    ...appState.config,
    api_id: intValue("api_id", 0), api_hash: byId("api_hash").value.trim(), phone: byId("phone").value.trim(),
    target_groups: byId("target_groups").value.split(/[,，\n]/).map(normalizeGroupLink).filter(Boolean),
    message_files: messageFiles,
    daily_limit: intValue("daily_limit", 30), idle_threshold_minutes: intValue("idle_threshold_minutes", 10),
    question_reply_pct: intValue("question_reply_pct", 70), discussion_reply_pct: intValue("discussion_reply_pct", 15),
    reply_delay_min: intValue("reply_delay_min", 20), reply_delay_max: intValue("reply_delay_max", 90),
    ai_enabled: byId("ai_enabled").checked, ai_api_key: byId("ai_api_key").value.trim(), ai_base_url: byId("ai_base_url").value.trim(),
    ai_model: byId("ai_model").value.trim(), ai_prompt: byId("ai_prompt").value.trim(), ai_context_count: intValue("ai_context_count", 5),
    ai_temperature: floatValue("ai_temperature", .7), ai_max_tokens: intValue("ai_max_tokens", 500),
    proxy_host: byId("proxy_host").value.trim(), proxy_port: byId("proxy_port").value ? intValue("proxy_port", null) : null, proxy_type: byId("proxy_type").value,
    schedule_enabled: byId("schedule_enabled").checked, schedule_morning_start: byId("schedule_morning_start").value.trim(), schedule_morning_end: byId("schedule_morning_end").value.trim(), schedule_afternoon_start: byId("schedule_afternoon_start").value.trim(), schedule_afternoon_end: byId("schedule_afternoon_end").value.trim(),
  };
}

async function saveConfig() {
  const errors = validateConfig();
  if (errors.length) { showToast(errors[0], "error"); return; }
  const wasUnconfigured = appState.configured === false;
  try {
    await api("/api/config", {method: "POST", body: JSON.stringify(collectConfig())});
    appState.dirty = false;
    await loadConfig();
    if (wasUnconfigured) {
      appState.configured = true;
      setView("overview");
      connectSSE();
      setInterval(loadDashboard, 30000);
      await loadDashboard();
      showToast("配置已保存，可开始自动参与", "success");
    } else {
      await loadDashboard();
      showToast("配置已保存", "success");
    }
  } catch (error) {
    if (error.field) {
      const input = byId(error.field);
      input?.classList.add("invalid");
      input?.closest(".field")?.querySelector(".field-error")?.append(error.message);
    }
    showToast(`保存失败：${error.message}`, "error");
  }
}

function markDirty() { appState.dirty = true; }

async function control(action) {
  if ((action === "stop") && !window.confirm("确定停止当前自动参与任务？")) return;
  try {
    await api(`/api/${action}`, {method: "POST", body: "{}"});
    showToast(action === "start" ? "正在启动" : "操作已提交", "success");
    setTimeout(loadDashboard, 300);
  } catch (error) { showToast(error.message, "error"); }
}

function appendLog(level, message) {
  const terminal = byId("logTerminal");
  if (terminal.children.length === 1 && terminal.firstElementChild?.classList.contains("muted")) terminal.innerHTML = "";
  const line = document.createElement("div");
  line.className = `log-line ${level}`;
  const time = document.createElement("span"); time.className = "time"; time.textContent = new Date().toLocaleTimeString("zh-CN", {hour12: false});
  const tag = document.createElement("span"); tag.className = "level"; tag.textContent = level.toUpperCase();
  const body = document.createElement("span"); body.textContent = message;
  line.append(time, tag, body); terminal.append(line); terminal.scrollTop = terminal.scrollHeight;
  while (terminal.children.length > 500) terminal.firstElementChild.remove();
}

function connectSSE() {
  appState.evtSource?.close();
  const lastId = localStorage.getItem("sse_last_id") || "";
  appState.evtSource = new EventSource(lastId ? `/api/events?last_event_id=${encodeURIComponent(lastId)}` : "/api/events");
  appState.evtSource.onopen = () => {
    byId("offlineBanner").hidden = true; byId("sseState").textContent = "SSE 已连接"; byId("activityConnection").textContent = "已连接";
  };
  appState.evtSource.onerror = () => {
    byId("offlineBanner").hidden = false; byId("sseState").textContent = "SSE 重连中"; byId("activityConnection").textContent = "重连中";
  };
  appState.evtSource.onmessage = (event) => {
    if (event.lastEventId) localStorage.setItem("sse_last_id", event.lastEventId);
    let payload; try { payload = JSON.parse(event.data); } catch { return; }
    const data = payload.data || {};
    if (payload.type === "status") updateStatus(data.state);
    if (payload.type === "counter") { appState.dashboard.total = data.total; appState.dashboard.per_group = data.per_group; renderDashboard(); }
    if (payload.type === "countdown") byId("countdownText").textContent = data.seconds > 0 ? `${data.seconds}s` : "--";
    if (payload.type === "log") appendLog(data.level || "info", data.message || "");
    if (payload.type === "decision") { const text = `${groupName(data.group)} · ${data.action} · ${data.reason}`; byId("latestDecision").textContent = text; appendLog("decision", text); }
    if (payload.type === "alert") { appendLog("warning", `${groupName(data.group)} · ${data.message}`); loadDashboard(); }
    if (payload.type === "group_state") loadDashboard();
    if (["lottery_alert", "lottery_updated", "scan_health", "checkin_health"].includes(payload.type)) { loadLotteryAlerts(); loadDashboard(); }
    if (payload.type === "code_required") { byId("codeArea").hidden = false; setView("activity"); byId("codeInput").focus(); }
  };
}

async function loadAudit() {
  try {
    const data = await api("/api/audit?limit=200");
    const rows = byId("auditRows");
    if (!data.events.length) { rows.innerHTML = '<tr><td colspan="5" class="empty">暂无审计记录</td></tr>'; return; }
    rows.innerHTML = data.events.map((event) => `<tr><td>${escapeHtml(new Date(event.occurred_at).toLocaleString("zh-CN"))}</td><td>${escapeHtml(groupName(event.group))}</td><td>${escapeHtml(event.event_type)}</td><td>${escapeHtml(event.reason)}</td><td></td></tr>`).join("");
  } catch (error) { showToast(`审计加载失败：${error.message}`, "error"); }
}

async function resumeGroup(group) {
  if (!window.confirm(`确认已检查 ${groupName(group)} 的告警并恢复？`)) return;
  try { await api("/api/groups/resume", {method: "POST", body: JSON.stringify({group})}); await loadDashboard(); await loadAudit(); showToast("群组已恢复", "success"); }
  catch (error) { showToast(error.message, "error"); }
}

async function submitCode() {
  const code = byId("codeInput").value.trim();
  if (!code) return showToast("请输入验证码", "error");
  try { await api("/api/code", {method: "POST", body: JSON.stringify({code})}); byId("codeArea").hidden = true; byId("codeInput").value = ""; showToast("验证码已提交", "success"); }
  catch (error) { showToast(error.message, "error"); }
}

document.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".nav-item").forEach((item) => item.addEventListener("click", () => setView(item.dataset.view)));
  document.querySelectorAll("[data-go]").forEach((item) => item.addEventListener("click", () => setView(item.dataset.go)));
  document.querySelectorAll("[data-save]").forEach((item) => item.addEventListener("click", saveConfig));
  document.querySelectorAll("input, textarea, select").forEach((item) => item.addEventListener("input", markDirty));
  byId("target_groups").addEventListener("input", rebuildMessageFiles);
  byId("ai_enabled").addEventListener("change", toggleConditionalFields);
  byId("schedule_enabled").addEventListener("change", toggleConditionalFields);
  byId("btnStart").addEventListener("click", () => control("start"));
  byId("btnPause").addEventListener("click", () => control("pause"));
  byId("btnResume").addEventListener("click", () => control("resume"));
  byId("btnStop").addEventListener("click", () => control("stop"));
  byId("refreshDashboard").addEventListener("click", loadDashboard);
  byId("refreshAudit").addEventListener("click", loadAudit);
  byId("clearLog").addEventListener("click", () => { byId("logTerminal").innerHTML = '<div class="log-line muted">视图已清空</div>'; });
  byId("submitCode").addEventListener("click", submitCode);
  byId("codeInput").addEventListener("keydown", (event) => { if (event.key === "Enter") submitCode(); });
  document.addEventListener("click", (event) => { const resume = event.target.closest(".resume-group"); if (resume) resumeGroup(resume.dataset.group); const lottery = event.target.closest(".open-lottery"); if (lottery) openLottery(lottery.dataset.id); });
  byId("lotteryPrev").addEventListener("click", () => loadLotteryAlerts(Math.max(0, appState.lotteryPage.offset - appState.lotteryPage.limit)));
  byId("lotteryNext").addEventListener("click", () => loadLotteryAlerts(appState.lotteryPage.offset + appState.lotteryPage.limit));
  window.addEventListener("beforeunload", (event) => { if (appState.dirty) { event.preventDefault(); event.returnValue = ""; } });
  const configured = await loadHealth();
  if (!configured) {
    setView("system");
    showToast("首次使用：请先完成系统设置", "info");
    return;
  }
  await loadConfig();
  await loadDashboard();
  await loadLotteryAlerts();
  connectSSE();
  setInterval(loadDashboard, 30000);
});
