// ===== SSE 连接 =====
let evtSource = null;
let sseErrorNotified = false;

function connectSSE() {
  if (evtSource) evtSource.close();
  const lastId = localStorage.getItem('sse_last_id') || '';
  const url = lastId ? '/api/events?last_event_id=' + encodeURIComponent(lastId) : '/api/events';
  evtSource = new EventSource(url);

  evtSource.onmessage = function(event) {
    try {
      if (event.lastEventId) {
        localStorage.setItem('sse_last_id', event.lastEventId);
      }
      sseErrorNotified = false;
      const msg = JSON.parse(event.data);
      switch (msg.type) {
        case 'log': appendLog(msg.data.level, msg.data.message); break;
        case 'status': updateStatus(msg.data.state); break;
        case 'counter': updateCounter(msg.data.total, msg.data.per_group); break;
        case 'countdown': updateCountdown(msg.data.seconds); break;
        case 'code_required': showCodeInput(); break;
      }
    } catch(e) {}
  };

  evtSource.onerror = function() {
    // EventSource 会自动重连；连续失败时给用户一次提示，避免刷屏
    if (!sseErrorNotified) {
      sseErrorNotified = true;
      showToast('实时日志连接断开，正在尝试重连...', 'warning');
    }
  };
}

// ===== 日志 =====
const logTerminal = document.getElementById('logTerminal');
let autoScroll = true;

function appendLog(level, message) {
  const wasAtBottom = logTerminal.scrollHeight - logTerminal.scrollTop - logTerminal.clientHeight < 30;
  const timestamp = new Date().toLocaleTimeString('zh-CN', {hour12: false});
  const cls = level === 'error' ? 'log-error' : level === 'warning' ? 'log-warning' : '';
  const line = document.createElement('div');
  line.className = 'log-entry ' + cls;
  line.textContent = `[${timestamp}] ${message}`;
  logTerminal.appendChild(line);
  // 限制日志行数
  while (logTerminal.children.length > 500) logTerminal.removeChild(logTerminal.firstChild);
  if (wasAtBottom) logTerminal.scrollTop = logTerminal.scrollHeight;
}

// ===== 状态指示 =====
function updateStatus(state) {
  const dot = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  dot.className = 'status-dot ' + state;

  const statusMap = {
    idle: '就绪',
    starting: '启动中...',
    running: '运行中...',
    paused: '已暂停',
    pausing: '暂停中...',
    stopping: '停止中...',
    stopped: '已停止',
    waiting_code: '等待验证码...'
  };
  text.textContent = statusMap[state] || state;

  // 控制按钮显隐
  document.getElementById('btnStart').style.display =
    (state === 'idle' || state === 'stopped') ? '' : 'none';
  document.getElementById('btnPause').style.display = state === 'running' ? '' : 'none';
  document.getElementById('btnResume').style.display = state === 'paused' ? '' : 'none';
  document.getElementById('btnStop').style.display =
    (state === 'starting' || state === 'running' || state === 'pausing' || state === 'paused' || state === 'waiting_code') ? '' : 'none';
}

// ===== 计数器 =====
function updateCounter(total, perGroup) {
  document.getElementById('totalCount').textContent = total;
  document.getElementById('totalCountDetail').textContent = total;
  const container = document.getElementById('perGroupCounts');
  if (!perGroup) { container.innerHTML = ''; return; }
  const parts = [];
  for (const [link, count] of Object.entries(perGroup)) {
    const name = link.replace(/\/+$/, '').split('/').pop().replace(/^@/, '');
    parts.push(`<span class="counter-item">${name}: <strong>${count}</strong> 条</span>`);
  }
  container.innerHTML = parts.join(' &nbsp;|&nbsp; ');
}

// ===== 倒计时 =====
function updateCountdown(seconds) {
  const item = document.getElementById('countdownItem');
  const text = document.getElementById('countdownText');
  if (seconds <= 0) {
    item.style.display = 'none';
  } else {
    item.style.display = '';
    text.textContent = formatDuration(seconds);
  }
}

function formatDuration(seconds) {
  if (seconds < 60) return seconds + '秒';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m + '分' + s + '秒';
}

// ===== 验证码 =====
function showCodeInput() {
  document.getElementById('codeArea').classList.add('visible');
  document.getElementById('codeInput').focus();
}

async function submitCode() {
  const code = document.getElementById('codeInput').value.trim();
  if (!code) return;
  try {
    const resp = await fetch('/api/code', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code: code})
    });
    const data = await resp.json();
    if (!resp.ok || !data.success) {
      showToast(data.error || '验证码提交失败', 'error');
      return;
    }
    document.getElementById('codeInput').value = '';
    document.getElementById('codeArea').classList.remove('visible');
  } catch(e) {
    showToast('网络错误: ' + e.message, 'error');
  }
}

// 回车提交验证码
document.addEventListener('DOMContentLoaded', function() {
  document.getElementById('codeInput').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') submitCode();
  });
});

// ===== 发送请求 =====
async function sendRequest(url) {
  try {
    const resp = await fetch(url, {method: 'POST'});
    const data = await resp.json();
    if (!resp.ok || !data.success) {
      showToast(data.error || '操作失败', 'error');
    }
  } catch(e) {
    showToast('网络错误: ' + e.message, 'error');
  }
}

// ===== 配置表单 =====

// 群组输入变化时重建消息文件行
document.addEventListener('DOMContentLoaded', function() {
  const groupsField = document.getElementById('target_groups');
  if (groupsField) {
    groupsField.addEventListener('input', rebuildMessageFiles);
  }

  // AI 开关切换
  const aiToggle = document.getElementById('ai_enabled');
  if (aiToggle) {
    aiToggle.addEventListener('change', function() {
      const visible = this.checked;
      document.querySelectorAll('.ai-field').forEach(el => el.style.display = visible ? '' : 'none');
    });
  }

  // 定时开关切换
  const schedToggle = document.getElementById('schedule_enabled');
  if (schedToggle) {
    schedToggle.addEventListener('change', function() {
      const visible = this.checked;
      document.querySelectorAll('.schedule-field').forEach(el => el.style.display = visible ? '' : 'none');
    });
  }

  // 反检测开关切换
  const antiToggle = document.getElementById('anti_detect');
  if (antiToggle) {
    antiToggle.addEventListener('change', function() {
      const visible = this.checked;
      document.querySelectorAll('.anti-field').forEach(el => el.style.display = visible ? '' : 'none');
    });
  }

  // 导航切换
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', function() {
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      this.classList.add('active');
      const target = document.getElementById('panel' + this.dataset.panel.charAt(0).toUpperCase() + this.dataset.panel.slice(1));
      if (target) target.classList.add('active');
    });
  });
});

function rebuildMessageFiles() {
  const area = document.getElementById('message-files-area');
  const text = document.getElementById('target_groups').value;
  const groups = text.split(/[,，]/).map(s => s.trim()).filter(Boolean);

  // 保留已填写的路径，避免编辑群组时丢失用户输入
  const existing = {};
  area.querySelectorAll('input[id^="file_"]').forEach(input => {
    existing[input.id] = input.value;
  });

  if (!groups.length) {
    area.innerHTML = '<p style="color:var(--text-muted);font-size:12px;font-style:italic;">输入目标群组链接后，此处将显示文件路径输入</p>';
    return;
  }
  let html = '';
  for (const g of groups) {
    const name = g.replace(/\/+$/, '').split('/').pop() || g;
    const fieldId = 'file_' + name.replace(/[^a-zA-Z0-9]/g, '_');
    const savedValue = existing[fieldId] ? ` value="${escapeHtml(existing[fieldId])}"` : '';
    html += '<div class="file-row form-group">';
    html += `<label for="${fieldId}">消息文件 (${name.substring(0, 20)})</label>`;
    html += `<input type="text" id="${fieldId}" placeholder="如 messages_${name}.txt"${savedValue}>`;
    html += '</div>';
  }
  area.innerHTML = html;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

async function loadConfig() {
  try {
    const resp = await fetch('/api/config');
    const data = await resp.json();
    if (!data.success) { showToast(data.error || '加载失败', 'error'); return; }
    const c = data.config;
    document.getElementById('api_id').value = c.api_id || '';
    document.getElementById('api_hash').value = c.api_hash || '';
    document.getElementById('phone').value = c.phone || '';
    document.getElementById('target_groups').value = (c.target_groups || []).join(', ');
    document.getElementById('min_interval').value = c.min_interval || 60;
    document.getElementById('max_interval').value = c.max_interval || 180;
    document.getElementById('proxy_host').value = c.proxy_host || '';
    document.getElementById('proxy_port').value = c.proxy_port || '';
    document.getElementById('proxy_type').value = c.proxy_type || 'http';

    document.getElementById('ai_enabled').checked = c.ai_enabled || false;
    document.getElementById('ai_api_key').value = c.ai_api_key || '';
    document.getElementById('ai_base_url').value = c.ai_base_url || '';
    document.getElementById('ai_model').value = c.ai_model || 'deepseek-chat';
    document.getElementById('ai_prompt').value = c.ai_prompt || '';
    document.getElementById('ai_context_count').value = c.ai_context_count || 5;
    document.getElementById('ai_enabled').dispatchEvent(new Event('change'));

    document.getElementById('schedule_enabled').checked = c.schedule_enabled || false;
    document.getElementById('schedule_morning_start').value = c.schedule_morning_start || '08:00';
    document.getElementById('schedule_morning_end').value = c.schedule_morning_end || '11:00';
    document.getElementById('schedule_afternoon_start').value = c.schedule_afternoon_start || '14:00';
    document.getElementById('schedule_afternoon_end').value = c.schedule_afternoon_end || '18:00';
    document.getElementById('schedule_enabled').dispatchEvent(new Event('change'));

    document.getElementById('anti_detect').checked = c.anti_detect || false;
    document.getElementById('typing_delay_min').value = c.typing_delay_min || 3;
    document.getElementById('typing_delay_max').value = c.typing_delay_max || 8;
    document.getElementById('thinking_delay_min').value = c.thinking_delay_min || 5;
    document.getElementById('thinking_delay_max').value = c.thinking_delay_max || 25;
    document.getElementById('skip_round_pct').value = c.skip_round_pct || 10;
    document.getElementById('anti_detect').dispatchEvent(new Event('change'));

    // 消息文件
    rebuildMessageFiles();
    if (c.message_files) {
      for (const [group, path] of Object.entries(c.message_files)) {
        const name = group.replace(/\/+$/, '').split('/').pop() || group;
        const fieldId = 'file_' + name.replace(/[^a-zA-Z0-9]/g, '_');
        const el = document.getElementById(fieldId);
        if (el) el.value = path;
      }
    }

  } catch(e) {
    showToast('加载失败: ' + e.message, 'error');
  }
}

async function saveConfig() {
  // 收集消息文件映射
  const messageFiles = {};
  const groups = document.getElementById('target_groups').value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
  for (const g of groups) {
    const name = g.replace(/\/+$/, '').split('/').pop() || g;
    const fieldId = 'file_' + name.replace(/[^a-zA-Z0-9]/g, '_');
    const el = document.getElementById(fieldId);
    if (el && el.value.trim()) messageFiles[g] = el.value.trim();
  }

  const payload = {
    api_id: parseInt(document.getElementById('api_id').value) || 0,
    api_hash: document.getElementById('api_hash').value.trim(),
    phone: document.getElementById('phone').value.trim(),
    target_groups: groups,
    min_interval: parseInt(document.getElementById('min_interval').value) || 60,
    max_interval: parseInt(document.getElementById('max_interval').value) || 180,
    proxy_host: document.getElementById('proxy_host').value.trim() || null,
    proxy_port: document.getElementById('proxy_port').value ? parseInt(document.getElementById('proxy_port').value) : null,
    proxy_type: document.getElementById('proxy_type').value,
    message_files: messageFiles,
    ai_enabled: document.getElementById('ai_enabled').checked,
    ai_api_key: document.getElementById('ai_api_key').value.trim(),
    ai_base_url: document.getElementById('ai_base_url').value.trim() || 'https://api.deepseek.com/v1',
    ai_model: document.getElementById('ai_model').value.trim() || 'deepseek-chat',
    ai_prompt: document.getElementById('ai_prompt').value.trim(),
    ai_context_count: parseInt(document.getElementById('ai_context_count').value) || 5,
    schedule_enabled: document.getElementById('schedule_enabled').checked,
    schedule_morning_start: document.getElementById('schedule_morning_start').value.trim(),
    schedule_morning_end: document.getElementById('schedule_morning_end').value.trim(),
    schedule_afternoon_start: document.getElementById('schedule_afternoon_start').value.trim(),
    schedule_afternoon_end: document.getElementById('schedule_afternoon_end').value.trim(),
    anti_detect: document.getElementById('anti_detect').checked,
    typing_delay_min: parseInt(document.getElementById('typing_delay_min').value) || 3,
    typing_delay_max: parseInt(document.getElementById('typing_delay_max').value) || 8,
    thinking_delay_min: parseInt(document.getElementById('thinking_delay_min').value) || 5,
    thinking_delay_max: parseInt(document.getElementById('thinking_delay_max').value) || 25,
    skip_round_pct: parseInt(document.getElementById('skip_round_pct').value) || 10
  };

  try {
    const resp = await fetch('/api/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await resp.json();
    if (data.success) {
      showToast('✅ 配置已保存到 .env', 'success');
    } else {
      showToast('❌ ' + (data.error || '保存失败'), 'error');
    }
  } catch(e) {
    showToast('保存失败: ' + e.message, 'error');
  }
}

// ===== Toast =====
const toastQueue = [];
let toastShowing = false;

function showToast(msg, type) {
  toastQueue.push({msg, type});
  if (!toastShowing) showNextToast();
}

function showNextToast() {
  if (!toastQueue.length) {
    toastShowing = false;
    return;
  }
  toastShowing = true;
  const {msg, type} = toastQueue.shift();
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.className = 'toast ' + type + ' show';
  clearTimeout(toast._hide);
  toast._hide = setTimeout(() => {
    toast.classList.remove('show');
    // 等淡出动画结束再显示下一条（若 CSS 无淡出动画则立即）
    setTimeout(showNextToast, 150);
  }, 3000);
}

// ===== 初始化 =====
document.addEventListener('DOMContentLoaded', function() {
  connectSSE();
  // 默认加载配置
  loadConfig();
});
