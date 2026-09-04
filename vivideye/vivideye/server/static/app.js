/* ============================================================
 * VividEye 前端应用（纯原生 JavaScript，无框架、无 CDN 依赖）
 * ------------------------------------------------------------
 * 四个底部 Tab：高光墙 / 实时画面 / 日报 / 设置
 * 数据接口：/api/status /api/highlights /api/digest
 *          /api/config /api/live /api/pipeline/run
 * ============================================================ */

'use strict';

/* ---------- DOM 快捷选择 ---------- */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/* ---------- 全局状态 ---------- */
const state = {
  tab: 'highlights',      // 当前 Tab
  items: [],               // 已加载的高光列表
  offset: 0,               // 分页偏移
  limit: 24,               // 单页条数
  hasMore: false,          // 是否还有更多
  favoriteOnly: false,     // 仅看收藏
  loading: false,          // 防重复加载
  current: null,          // 播放弹层当前高光（state.items 内的引用）
  digestDate: todayStr(), // 日报日期
  cfg: null,               // /api/config 返回（密钥已打码）
  status: null,            // /api/status 最近一次返回（空状态三态判定用）
};

/* ============================================================
 * 通用工具函数
 * ============================================================ */

/** 本地时区的今天，格式 YYYY-MM-DD */
function todayStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** fetch JSON 包装：统一错误提示 */
async function fetchJSON(url, options = {}) {
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  let data = null;
  try { data = await resp.json(); } catch (_) { /* 非 JSON 响应体 */ }
  if (!resp.ok) {
    const msg = (data && (data.detail || data.message)) || `请求失败（${resp.status}）`;
    throw new Error(msg);
  }
  return data;
}

/** 秒 → "m:ss"（超过 1 小时则 "h:mm:ss"） */
function fmtClock(sec) {
  sec = Math.max(0, Math.round(Number(sec) || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const mm = String(m).padStart(2, '0');
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}

/** 时间戳 → 今天显示 "14:30"，其他天显示 "9月1日 14:30" */
function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const now = new Date();
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  return d.toDateString() === now.toDateString()
    ? hm
    : `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
}

/** 字节数 → 人类可读 */
function fmtBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + ' GB';
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(0) + ' MB';
  return (n / 1024).toFixed(0) + ' KB';
}

/** HTML 转义（所有后端内容渲染前必须经过它） */
function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** 轻提示 toast */
let toastTimer = null;
function toast(msg, ms = 2400) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), ms);
}

/* ============================================================
 * 极简 Markdown 渲染器（内置实现，离线可用）
 * 覆盖日报用到的语法：标题/粗斜体/行内代码/有序无序列表/
 * 引用/分割线/链接/代码块
 * ============================================================ */
function renderMarkdown(md) {
  const lines = String(md || '').replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let i = 0;

  /** 行内元素：转义后再做富文本替换 */
  const inline = (s) => {
    s = esc(s);
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
    s = s.replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>',
    );
    return s;
  };

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) { i++; continue; } // 空行：段落分隔

    if (line.startsWith('```')) {          // 围栏代码块
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) buf.push(lines[i++]);
      i++; // 跳过收尾 ```
      out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`);
      continue;
    }

    const m = line.match(/^(#{1,6})\s+(.*)$/); // 标题
    if (m) {
      const lv = m[1].length;
      out.push(`<h${lv}>${inline(m[2])}</h${lv}>`);
      i++;
      continue;
    }

    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { // 分割线
      out.push('<hr>');
      i++;
      continue;
    }

    if (/^\s*>/.test(line)) {              // 引用块（连续行合并）
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        buf.push(lines[i++].replace(/^\s*>\s?/, ''));
      }
      out.push(`<blockquote>${buf.map(inline).join('<br>')}</blockquote>`);
      continue;
    }

    if (/^\s*[-*+]\s+/.test(line)) {       // 无序列表
      const buf = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        buf.push(lines[i++].replace(/^\s*[-*+]\s+/, ''));
      }
      out.push(`<ul>${buf.map((x) => `<li>${inline(x)}</li>`).join('')}</ul>`);
      continue;
    }

    if (/^\s*\d+[.、)]\s+/.test(line)) {   // 有序列表
      const buf = [];
      while (i < lines.length && /^\s*\d+[.、)]\s+/.test(lines[i])) {
        buf.push(lines[i++].replace(/^\s*\d+[.、)]\s+/, ''));
      }
      out.push(`<ol>${buf.map((x) => `<li>${inline(x)}</li>`).join('')}</ol>`);
      continue;
    }

    // 普通段落：连续非格式行合并，单换行转 <br>
    const buf = [line];
    i++;
    while (i < lines.length && lines[i].trim() &&
           !/^(#{1,6}\s|```|\s*>|\s*[-*+]\s|\s*\d+[.、)]\s)/.test(lines[i])) {
      buf.push(lines[i++]);
    }
    out.push(`<p>${buf.map(inline).join('<br>')}</p>`);
  }
  return out.join('\n');
}

/* ============================================================
 * 顶部状态栏
 * ============================================================ */

/** "未录制"原因推断（数据源：recorder.json 心跳透传的 paused / last_file） */
function recordReason(rec) {
  if (rec.paused) return '磁盘空间不足';
  if (rec.last_file) return '摄像头无信号';
  return '未检测到摄像头';
}

async function loadStatus() {
  try {
    const s = await fetchJSON('/api/status');
    state.status = s;

    // 录制状态灯：录制中=绿点；未录制=灰点 + 尽量带上原因
    const rec = s.recording || {};
    const on = !!rec.recording;
    $('#record-dot').classList.toggle('on', on);
    $('#record-dot').classList.remove('err');
    $('#chip-record').classList.toggle('rec', on);
    $('#chip-record').classList.remove('err');
    $('#record-text').textContent = on ? '录制中' : `未录制 · ${recordReason(rec)}`;

    // 今日高光数 / 磁盘
    const today = (s.db && s.db.highlights_today) || 0;
    $('#chip-today').textContent = `今日 ✨ ${today}`;
    $('#chip-disk').textContent = `💾 ${fmtBytes(s.disk_free)}`;

    // pipeline 状态（设置页展示）
    const p = s.pipeline || {};
    const ps = $('#pipeline-status');
    if (p.running) ps.textContent = '⏳ 处理任务进行中…';
    else if (p.last_run_str) {
      ps.textContent = `上次处理：${p.last_run_str}` + (p.last_error ? ' · 上次出错' : '');
    } else {
      ps.textContent = p.available ? '还没有处理记录' : 'AI 分析模块未就绪';
    }

    // 状态就绪后刷新一次空状态文案（首屏可能与列表加载竞争）
    if (!state.items.length) renderHighlights();
  } catch (_) {
    // 状态拉取失败：红点明确提示服务未响应，不再静默
    $('#record-dot').classList.remove('on');
    $('#record-dot').classList.add('err');
    $('#chip-record').classList.remove('rec');
    $('#chip-record').classList.add('err');
    $('#record-text').textContent = '服务未响应';
  }
}

/* ============================================================
 * 高光墙
 * ============================================================ */

/** 拉取高光列表（reset=true 重置分页） */
async function loadHighlights(reset = false) {
  if (state.loading) return;
  state.loading = true;
  if (reset) { state.offset = 0; state.items = []; }

  const params = new URLSearchParams({
    limit: String(state.limit),
    offset: String(state.offset),
    favorite: state.favoriteOnly ? 'true' : 'false',
  });
  try {
    const data = await fetchJSON(`/api/highlights?${params}`);
    const items = data.items || [];
    state.items = reset ? items : state.items.concat(items);
    state.hasMore = items.length >= state.limit;
    renderHighlights();
  } catch (e) {
    toast('加载高光失败：' + e.message);
  } finally {
    state.loading = false;
  }
}

/** 正常等待态提示：下一批分析预计 xx:xx（last_run + run_interval 推算）+ 原有温馨文案 */
function nextRunHint() {
  const p = (state.status && state.status.pipeline) || {};
  const interval = Number(
    (state.cfg && state.cfg.pipeline && state.cfg.pipeline.run_interval_minutes)) || 30;
  const warm = '毛孩子们正在酝酿精彩…';
  if (p.running) return `AI 分析进行中，${warm}`;
  const last = Number(p.last_run);
  if (!last) return `每 ${interval} 分钟自动分析一批，${warm}`;
  const next = new Date((last + interval * 60) * 1000);
  if (next <= new Date()) return `下一批分析马上开始，${warm}`;
  const hm = `${String(next.getHours()).padStart(2, '0')}:${String(next.getMinutes()).padStart(2, '0')}`;
  return `下一批分析预计 ${hm}，${warm}`;
}

/** 高光墙空状态文案（三态：未配 AI Key / 未在录制 / 正常等待） */
function emptyStateCopy() {
  if (state.favoriteOnly) {
    return { emoji: '💛', title: '还没有收藏的高光', sub: '看到喜欢的瞬间，点亮小红心吧' };
  }
  const st = state.status;
  if (!st) {   // 状态未就绪：先给温馨文案，loadStatus 回来后会刷新
    return { emoji: '🐾', title: '今天还没有高光时刻', sub: '毛孩子们正在酝酿精彩…' };
  }
  // 1) 未配置 AI Key（优先 /api/status.has_api_key，/api/config 掩码兜底）
  const cfgKey = state.cfg && state.cfg.ai && state.cfg.ai.api_key;
  const hasKey = st.has_api_key === true
    || (cfgKey && cfgKey !== '******' && String(cfgKey).trim() !== '');
  if (!hasKey) {
    return {
      emoji: '🔑',
      title: '还没配置 AI 分析',
      sub: '去设置页粘贴 API Key，就能自动挑选高光时刻啦',
      goSettings: true,
    };
  }
  // 2) 未在录制：多半是摄像头侧没启动
  if (!(st.recording && st.recording.recording)) {
    return {
      emoji: '📷',
      title: '摄像头未工作',
      sub: '检查 IP Webcam 是否启动，启动后就会开始录制',
    };
  }
  // 3) 一切正常，等待下一批分析
  return { emoji: '🐾', title: '今天还没有高光时刻', sub: nextRunHint() };
}

/** 渲染高光墙（网格 + 空状态 + 加载更多） */
function renderHighlights() {
  $('#hl-grid').innerHTML = state.items.map(cardHTML).join('');

  // 空状态：收藏视图 / 未配Key / 未在录制 / 正常等待
  const empty = $('#hl-empty');
  const showEmpty = state.items.length === 0;
  empty.classList.toggle('hidden', !showEmpty);
  if (showEmpty) {
    const copy = emptyStateCopy();
    $('#empty-emoji').textContent = copy.emoji;
    $('#empty-title').textContent = copy.title;
    $('#empty-sub').textContent = copy.sub;
    $('#btn-empty-settings').classList.toggle('hidden', !copy.goSettings);
  }

  $('#more-wrap').classList.toggle(
    'hidden', !(state.hasMore && state.items.length));
  $('#hl-count').textContent = state.items.length
    ? `已加载 ${state.items.length} 条` : '';
}

/** 分数（0~1）→ 五星展示（整星四舍五入），如 0.85 → ★★★★☆ */
function scoreStars(score) {
  const n = Math.min(5, Math.max(0, Math.round((Number(score) || 0) * 5)));
  return '★'.repeat(n) + '☆'.repeat(5 - n);
}

/** 单张高光卡片 HTML（内容一律 esc 转义） */
function cardHTML(h) {
  const tags = (h.tags || []).slice(0, 4)
    .map((t) => `<span class="tag">${esc(t)}</span>`).join('');
  const score = Number(h.score) || 0;
  const ts = h.started_at || h.created_at || 0;
  const btBadge = h.bullet_time_path
    ? '<span class="bt-badge" title="已合成子弹时间短片">⚡ 子弹时间</span>' : '';
  return `
  <article class="card-hl" data-id="${esc(h.id)}">
    <div class="thumb-wrap">
      <span class="thumb-emoji">🐾</span>
      <img class="thumb" src="/api/highlights/${esc(h.id)}/thumb"
           alt="缩略图" loading="lazy"
           onerror="this.classList.add('broken');this.onerror=null;">
      ${btBadge}
      <span class="dur">${fmtClock(h.duration)}</span>
      <button class="fav-btn" data-fav="${esc(h.id)}" aria-label="收藏">
        ${h.favorite ? '❤️' : '🤍'}
      </button>
    </div>
    <div class="card-body">
      <h4 class="hl-title">${esc(h.title || '未命名时刻')}</h4>
      ${h.caption ? `<p class="hl-caption">${esc(h.caption)}</p>` : ''}
      <div class="hl-meta">
        <span>${fmtTime(ts)}</span>
        ${score ? `<span class="hl-score" title="评分 ${score.toFixed(2)}">${scoreStars(score)}</span>` : ''}
      </div>
      ${tags ? `<div class="hl-tags">${tags}</div>` : ''}
    </div>
  </article>`;
}

/** 收藏 / 取消收藏（乐观更新，失败回滚） */
async function toggleFavorite(id) {
  const h = state.items.find((x) => x.id === id);
  if (!h) return;
  const next = !h.favorite;
  h.favorite = next; // current 与 items 里是同一引用，弹层同步生效
  refreshFavUI(id, next);
  try {
    await fetchJSON(`/api/highlights/${id}/favorite`, {
      method: 'POST',
      body: JSON.stringify({ favorite: next }),
    });
  } catch (e) {
    h.favorite = !next;
    refreshFavUI(id, !next);
    toast('操作失败：' + e.message);
  }
}

/** 同步卡片与弹层上的心形按钮 */
function refreshFavUI(id, on) {
  const btn = $(`.fav-btn[data-fav="${id}"]`);
  if (btn) btn.textContent = on ? '❤️' : '🤍';
  if (state.current && state.current.id === id) {
    $('#player-fav').textContent = on ? '❤️' : '🤍';
  }
}

/* ============================================================
 * 视频播放弹层
 * ============================================================ */

/* ---------- 播放弹层：子弹时间切换 ---------- */
// 当前弹层是否正在播子弹时间短片（关闭弹层时复位）
let btPlaying = false;

/** 重置子弹时间按钮到初始态（"⚡ 播放子弹时间"） */
function resetBtButton() {
  btPlaying = false;
  const btn = $('#player-bt');
  btn.classList.remove('active');
  btn.textContent = '⚡ 播放子弹时间';
}

/** 子弹时间 ⇄ 原视频 来回切换（仅对已合成子弹时间的高光可见） */
function toggleBulletTime() {
  const h = state.current;
  if (!h || !h.bullet_time_path) return;
  btPlaying = !btPlaying;
  const video = $('#player-video');
  video.src = btPlaying
    ? `/api/highlights/${h.id}/bullettime`
    : `/api/highlights/${h.id}/video`;
  const btn = $('#player-bt');
  btn.textContent = btPlaying ? '▶ 播放原视频' : '⚡ 播放子弹时间';
  btn.classList.toggle('active', btPlaying);
  video.play().catch(() => { /* 自动播放被拒时等用户手动点 */ });
}

function openPlayer(id) {
  const h = state.items.find((x) => x.id === id);
  if (!h) return;
  state.current = h;
  $('#player-title').textContent = h.title || '未命名时刻';
  $('#player-caption').textContent = h.caption || '';
  $('#player-fav').textContent = h.favorite ? '❤️' : '🤍';

  // 有子弹时间短片才显示切换按钮（bullet_time_path 为 null/空即未合成）
  $('#player-bt').classList.toggle('hidden', !h.bullet_time_path);
  resetBtButton();

  const video = $('#player-video');
  video.src = `/api/highlights/${h.id}/video`;
  $('#player-modal').classList.remove('hidden');
  document.body.classList.add('modal-open');
  video.play().catch(() => { /* 自动播放被拒时等用户手动点 */ });
}

function closePlayer() {
  const video = $('#player-video');
  video.pause();
  video.removeAttribute('src');
  video.load(); // 释放连接，避免占用手机带宽
  resetBtButton(); // esc/关闭时复位子弹时间切换状态
  state.current = null;
  $('#player-modal').classList.add('hidden');
  document.body.classList.remove('modal-open');
}

/** 删除当前播放的高光（连带视频/缩略图文件） */
async function deleteCurrent() {
  const h = state.current;
  if (!h) return;
  const name = h.title || '这个时刻';
  if (!confirm(`确定删除「${name}」吗？视频文件也会一并删除哦`)) return;
  try {
    await fetchJSON(`/api/highlights/${h.id}`, { method: 'DELETE' });
    state.items = state.items.filter((x) => x.id !== h.id);
    renderHighlights();
    closePlayer();
    toast('已删除');
    loadStatus(); // 顺便刷新今日数量
  } catch (e) {
    toast('删除失败：' + e.message);
  }
}

/* ============================================================
 * Tab 切换
 * ============================================================ */
function switchTab(name) {
  state.tab = name;
  $$('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  $$('.page').forEach((p) => p.classList.toggle('hidden', p.id !== `page-${name}`));

  if (name === 'live') enterLive();
  else leaveLive();          // 离开实时页时断流，省电省流量
  if (name === 'digest') loadDigest();
  if (name === 'settings') loadConfigIntoUI();
}

/* ============================================================
 * 实时画面
 * ============================================================ */
function enterLive() {
  const img = $('#live-img');
  if (!img.getAttribute('src')) img.src = '/api/live';
}

function leaveLive() {
  // 断开 MJPEG 连接：移除 src 即停止拉流
  $('#live-img').removeAttribute('src');
}

function retryLive() {
  $('#live-offline').classList.add('hidden');
  $('#live-img').src = `/api/live?_=${Date.now()}`; // 加时间戳防缓存
}

function initLive() {
  const img = $('#live-img');
  img.addEventListener('error', () => {
    $('#live-offline').classList.remove('hidden');
  });
  $('#btn-live-retry').addEventListener('click', retryLive);
}

/* ============================================================
 * 日报
 * ============================================================ */
async function loadDigest() {
  const input = $('#digest-date');
  input.value = state.digestDate;
  input.max = todayStr(); // 不能选未来

  $('#digest-loading').classList.remove('hidden');
  $('#digest-body').innerHTML = '';
  try {
    const data = await fetchJSON(`/api/digest?date=${encodeURIComponent(state.digestDate)}`);
    // 空状态：当天还没有任何高光素材，给出可操作的提示
    const total = (data.stats && (data.stats.total ?? data.stats.highlight_count)) || 0;
    if (!total) {
      const when = state.digestDate === todayStr() ? '今天' : '这天';
      $('#digest-body').innerHTML =
        `<div class="digest-empty">${when}还没有素材，先确认摄像头在工作哦</div>`;
    } else {
      $('#digest-body').innerHTML = renderMarkdown(data.markdown || '');
    }
  } catch (e) {
    $('#digest-body').innerHTML =
      `<div class="digest-error">日报加载失败：${esc(e.message)}</div>`;
  } finally {
    $('#digest-loading').classList.add('hidden');
  }
}

/** 日报日期前后翻页 */
function shiftDigestDate(days) {
  const d = new Date(state.digestDate + 'T00:00:00');
  d.setDate(d.getDate() + days);
  const p = (n) => String(n).padStart(2, '0');
  const target = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  if (target > todayStr()) { toast('还不能穿越到未来哦'); return; }
  state.digestDate = target;
  loadDigest();
}

/* ============================================================
 * 设置页
 * ============================================================ */

/** 拉取配置并回填 UI（有缓存时直接用） */
async function loadConfigIntoUI(force = false) {
  if (state.cfg && !force) { applyConfigToUI(); return; }
  try {
    state.cfg = await fetchJSON('/api/config');
    applyConfigToUI();
    // 配置就绪后空状态文案可能变化（Key 判断 / 下一批分析时间）
    if (!state.items.length) renderHighlights();
  } catch (e) {
    toast('读取配置失败：' + e.message);
  }
}

/** 把配置回填到表单控件 */
function applyConfigToUI() {
  const cfg = state.cfg || {};
  const scene = (cfg.pipeline && cfg.pipeline.scene_mode) || 'auto';
  $$('#scene-seg .seg-btn').forEach(
    (b) => b.classList.toggle('active', b.dataset.mode === scene));
  // AI 开关：配置缺省视为开启
  $('#ai-switch').checked = !(cfg.ai && cfg.ai.enabled === false);
  // 子弹时间：开关缺省视为开启；阈值超出滑杆范围时回退 0.75
  const bt = cfg.bullet_time || {};
  $('#bt-switch').checked = bt.enabled !== false;
  const ms = Number(bt.min_score);
  const slider = $('#bt-min-score');
  slider.value = (ms >= 0.6 && ms <= 0.95) ? ms : 0.75;
  $('#bt-score-val').textContent = Number(slider.value).toFixed(2);
  $('#api-key').value = '';
}

/** 保存设置（场景模式 + AI 开关 + 子弹时间 + 可选的新 API Key） */
async function saveConfig() {
  const sceneBtn = $('#scene-seg .seg-btn.active');
  const body = {
    pipeline: { scene_mode: sceneBtn ? sceneBtn.dataset.mode : 'auto' },
    ai: { enabled: $('#ai-switch').checked },
    bullet_time: {
      enabled: $('#bt-switch').checked,
      min_score: Number($('#bt-min-score').value) || 0.75,
    },
  };
  const key = $('#api-key').value.trim();
  if (key) body.ai.api_key = key; // 留空表示不修改密钥

  const btn = $('#btn-save-config');
  btn.disabled = true;
  btn.textContent = '保存中…';
  try {
    const resp = await fetchJSON('/api/config', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    if (resp.config) state.cfg = resp.config;
    applyConfigToUI();
    toast(resp.message || '设置已保存 ✅');
  } catch (e) {
    toast('保存失败：' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '保存设置';
  }
}

/** 触发 pipeline 立即处理 */
async function runPipelineNow() {
  const btn = $('#btn-run-pipeline');
  btn.disabled = true;
  btn.textContent = '⏳ 触发中…';
  try {
    const r = await fetchJSON('/api/pipeline/run', { method: 'POST' });
    toast(r.message || '已触发立即处理');
    loadStatus();
  } catch (e) {
    toast('触发失败：' + e.message); // pipeline 未部署时这里是 503
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = '▶ 立即处理';
    }, 1500);
  }
}

/* ============================================================
 * 入口：事件绑定 + 初始加载
 * ============================================================ */
document.addEventListener('DOMContentLoaded', () => {
  // —— Tab 切换 ——
  $$('.tab').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));

  // —— 高光墙 ——
  // 事件委托：点卡片播放、点心形收藏
  $('#hl-grid').addEventListener('click', (ev) => {
    const favBtn = ev.target.closest('.fav-btn');
    if (favBtn) { toggleFavorite(favBtn.dataset.fav); return; }
    const card = ev.target.closest('.card-hl');
    if (card) openPlayer(card.dataset.id);
  });

  // 收藏筛选（全部 / 仅收藏）
  $('#fav-filter').addEventListener('click', (ev) => {
    const btn = ev.target.closest('.seg-btn');
    if (!btn) return;
    $$('#fav-filter .seg-btn').forEach((b) => b.classList.toggle('active', b === btn));
    state.favoriteOnly = btn.dataset.fav === 'true';
    loadHighlights(true);
  });

  $('#btn-more').addEventListener('click', () => {
    state.offset += state.limit;
    loadHighlights(false);
  });
  $('#btn-empty-refresh').addEventListener('click', () => loadHighlights(true));
  // 未配 Key 空状态的一键直达设置页
  $('#btn-empty-settings').addEventListener('click', () => switchTab('settings'));

  // —— 播放弹层 ——
  $('#player-close').addEventListener('click', closePlayer);
  $('#player-mask').addEventListener('click', closePlayer);
  $('#player-fav').addEventListener(
    'click', () => state.current && toggleFavorite(state.current.id));
  $('#player-del').addEventListener('click', deleteCurrent);
  $('#player-bt').addEventListener('click', toggleBulletTime);
  // esc 关闭弹层（与关闭按钮同样会复位子弹时间切换状态）
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape'
        && !$('#player-modal').classList.contains('hidden')) {
      closePlayer();
    }
  });

  // —— 实时画面 ——
  initLive();

  // —— 日报 ——
  $('#digest-prev').addEventListener('click', () => shiftDigestDate(-1));
  $('#digest-next').addEventListener('click', () => shiftDigestDate(1));
  $('#digest-date').addEventListener('change', (e) => {
    state.digestDate = e.target.value || todayStr();
    loadDigest();
  });

  // —— 设置页 ——
  $('#scene-seg').addEventListener('click', (ev) => {
    const btn = ev.target.closest('.seg-btn');
    if (!btn) return;
    $$('#scene-seg .seg-btn').forEach((b) => b.classList.toggle('active', b === btn));
  });
  $('#btn-save-config').addEventListener('click', saveConfig);
  $('#btn-run-pipeline').addEventListener('click', runPipelineNow);
  // 子弹时间阈值滑杆：拖动时实时显示当前值
  $('#bt-min-score').addEventListener('input', (e) => {
    $('#bt-score-val').textContent = Number(e.target.value).toFixed(2);
  });

  // —— 初始加载 ——
  loadStatus();
  loadConfigIntoUI();          // 空状态三态需要（Key 判断 / 分析周期）
  loadHighlights(true);
  setInterval(loadStatus, 10000); // 状态栏每 10 秒自动刷新
});
