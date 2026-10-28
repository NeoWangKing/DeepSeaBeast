/* 大肥鱼控制台（核心层）——无框架，纯 DOM。
   加载态按博客规范：延迟 350ms 才出现，出现后最短停留 300ms。 */
const MIN_LOADING = 300;
const LOADING_DELAY = 350;

let CORE = null;
let DIRTY = new Map();      // section -> {key: value}

const $ = (s, r = document) => r.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const c of kids) if (c) n.append(c);
  return n;
};

function withMinLoading(promise) {
  const t0 = performance.now();
  return promise.then(async (v) => {
    const spent = performance.now() - t0;
    if (spent < MIN_LOADING) await new Promise((r) => setTimeout(r, MIN_LOADING - spent));
    return v;
  });
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const d = await r.json().catch(() => ({ ok: false, error: '响应不是 JSON' }));
  if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
  return d;
}

/* ── 主题 ── */
$('#theme-btn').addEventListener('click', () => {
  const cur = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = cur;
  localStorage.setItem('dsb-theme', cur);
});

/* ── 导航高亮 ── */
function markNav(id) {
  document.querySelectorAll('.nav a.item').forEach((a) => {
    a.classList.toggle('on', a.dataset.sec === id);
  });
}
document.querySelectorAll('.nav a.item').forEach((a) => {
  a.addEventListener('click', (e) => {
    if (a.dataset.sec === 'pending') {
      e.preventDefault();
      document.getElementById('pending')?.scrollIntoView({ behavior: 'smooth' });
      markNav('pending');
      return;
    }
    markNav(a.dataset.sec);
  });
});
window.addEventListener('scroll', () => {
  const secs = [...document.querySelectorAll('.block[id]')];
  let cur = secs[0]?.id;
  for (const s of secs) if (s.getBoundingClientRect().top < 120) cur = s.id;
  if (cur) markNav(cur);
}, { passive: true });

/* ── 下拉：自己画（原生 select 的弹层改不了样式，跟莱茵生命不搭）── */
const OP_LABELS = { strict: '只认被叫到', balanced: '被叫必问·否则打分', loose: '每条都问模型' };

function selectControl(field, value, onChange) {
  const wrap = el('div', { class: 'sel' });
  const trigger = el('button', {
    class: 'sel-trigger', type: 'button', 'aria-haspopup': 'listbox', 'aria-expanded': 'false',
  }, el('span', { class: 'val', text: value || field.options[0] }), el('span', { class: 'caret' }));
  const menu = el('div', { class: 'sel-menu', role: 'listbox', hidden: '' });
  let cur = value || field.options[0];

  const opts = field.options.map((o) => {
    const opt = el('div', {
      class: 'sel-opt', role: 'option', 'aria-selected': String(o === cur),
      onclick: () => pick(o),
    }, el('span', { text: o }), el('span', { class: 'meta', text: OP_LABELS[o] || '' }));
    menu.append(opt);
    return opt;
  });

  function highlight(i) {
    opts.forEach((o, k) => o.classList.toggle('active', k === i));
  }
  function open() {
    wrap.classList.add('open');
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    const i = Math.max(0, field.options.indexOf(cur));
    highlight(i);
    document.addEventListener('mousedown', outside, true);
  }
  function close() {
    wrap.classList.remove('open');
    menu.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    document.removeEventListener('mousedown', outside, true);
  }
  function outside(e) { if (!wrap.contains(e.target)) close(); }
  function pick(o) {
    cur = o;
    trigger.querySelector('.val').textContent = o;
    opts.forEach((n) => n.setAttribute('aria-selected', String(n.textContent.startsWith(o))));
    close();
    onChange(o);
  }

  trigger.addEventListener('click', () => (menu.hidden ? open() : close()));
  trigger.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); menu.hidden ? open() : close(); }
    else if (e.key === 'Escape') close();
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (menu.hidden) return open();
      const curIdx = field.options.indexOf(cur);
      const next = e.key === 'ArrowDown'
        ? Math.min(field.options.length - 1, curIdx + 1)
        : Math.max(0, curIdx - 1);
      highlight(next);
    }
  });
  menu.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); const a = opts.find((o) => o.classList.contains('active')); a?.click(); }
  });

  wrap.append(trigger, menu);
  return wrap;
}

/* ── 字段控件 ── */
function controlFor(field, value, onChange) {
  const wrap = el('div', { class: 'ctl' });
  if (field.type === 'bool') {
    const id = 'f-' + field.key;
    const label = el('label', { class: 'switch', for: id },
      el('input', { type: 'checkbox', id }),
      el('span', { class: 'track' }),
      el('span', { class: 'text', text: value ? '开启' : '关闭' })
    );
    const box = label.querySelector('input');
    box.checked = !!value;
    box.addEventListener('change', () => {
      label.querySelector('.text').textContent = box.checked ? '开启' : '关闭';
      onChange(box.checked);
    });
    wrap.append(label);
    return wrap;
  }
  if (field.type === 'enum') {
    wrap.append(selectControl(field, value, onChange));
    return wrap;
  }
  // 数值有范围 → 用滑条（细线 + 方形握柄），读数可直接输入精确值
  if ((field.type === 'int' || field.type === 'float')
      && field.min !== undefined && field.max !== undefined) {
    const wrap2 = el('div', { class: 'slider' });
    const rail = el('div', { class: 'rail' });
    const range = el('input', {
      type: 'range', min: String(field.min), max: String(field.max),
      step: String(field.step || (field.type === 'int' ? 1 : 0.1)),
      value: String(value === undefined || value === null ? field.min : value),
    });
    const readout = el('input', { class: 'val', type: 'text', inputmode: 'decimal' });
    const paint = () => {
      const lo = Number(range.min), hi = Number(range.max), v = Number(range.value);
      const pct = hi > lo ? ((v - lo) / (hi - lo)) * 100 : 0;
      range.style.setProperty('--pct', pct.toFixed(2) + '%');
      readout.value = String(range.value);
    };
    const emit = (raw) => {
      const v = field.type === 'int' ? parseInt(raw, 10) : parseFloat(raw);
      if (Number.isFinite(v)) onChange(v);
    };
    range.addEventListener('input', () => { paint(); emit(range.value); });
    readout.addEventListener('change', () => {
      let v = field.type === 'int' ? parseInt(readout.value, 10) : parseFloat(readout.value);
      if (!Number.isFinite(v)) { paint(); return; }
      v = Math.min(Number(range.max), Math.max(Number(range.min), v));
      range.value = String(v);
      paint();
      emit(v);
    });
    paint();
    rail.append(range);
    // 读数紧贴轨道左侧（每行对齐成一列），范围提示放到标签下面，不再挤在轨道右边
    wrap2.append(readout, rail);
    return wrap2;
  }

  const input = el('input', {
    type: field.type === 'int' || field.type === 'float' ? 'number' : 'text',
    value: value === undefined || value === null ? '' : String(value),
  });
  if (field.step) input.step = String(field.step);
  if (field.min !== undefined) input.min = String(field.min);
  if (field.max !== undefined) input.max = String(field.max);
  input.addEventListener('input', () => {
    const raw = input.value;
    onChange(field.type === 'int' ? parseInt(raw, 10) : field.type === 'float' ? parseFloat(raw) : raw);
  });
  wrap.append(input);
  if (field.min !== undefined || field.max !== undefined) {
    wrap.append(el('span', { class: 'rl-meta', text: `${field.min ?? '−∞'} … ${field.max ?? '∞'}` }));
  }
  return wrap;
}

/* ── 区块 ── */
function sectionBlock(sec, data) {
  const dirty = DIRTY.get(sec) || {};
  const block = el('section', { class: 'block', id: sec });
  const head = el('div', { class: 'block-head' },
    el('div', {},
      el('p', { class: 'rl-label', text: data.label }),
      el('h2', { text: data.desc }),
    ),
    el('div', { class: 'ctl' },
      el('span', { class: 'save-state', id: 'state-' + sec, text: '' }),
      el('button', { class: 'btn primary', type: 'button', text: 'SAVE / 保存本段',
        onclick: () => save(sec) },
        )
    )
  );
  block.append(head, el('hr', { class: 'rl-rule' }));

  for (const f of data.fields) {
    const val = f.key in dirty ? dirty[f.key] : f.value;
    const [en, zh] = (f.label || '').split(' / ');
    const hasRange = (f.type === 'int' || f.type === 'float')
      && f.min !== undefined && f.max !== undefined;
    const lab = el('div', { class: 'lab' },
      el('div', { class: 'en', text: en || f.key }),
      el('div', { class: 'zh', text: zh || '' }),
      f.help ? el('div', { class: 'help', text: f.help }) : null,
      hasRange ? el('div', { class: 'help range', text: `范围 ${f.min} … ${f.max}` }) : null,
    );
    const row = el('div', { class: 'field' }, lab,
      controlFor(f, val, (v) => {
        const m = DIRTY.get(sec) || {};
        m[f.key] = v;
        DIRTY.set(sec, m);
        const st = document.getElementById('state-' + sec);
        st.textContent = '未保存 ' + Object.keys(m).length + ' 项';
        st.className = 'save-state show';
      })
    );
    block.append(row);
  }
  return block;
}

function gateChats(raw) {
  const block = el('section', { class: 'block', id: 'gate-chats' });
  block.append(
    el('div', { class: 'block-head' },
      el('div', {},
        el('p', { class: 'rl-label', text: 'GATE CHATS / 按会话覆盖' }),
        el('h2', { text: '每个会话的档位与开关' })
      )
    ),
    el('hr', { class: 'rl-rule' })
  );
  const keys = Object.keys(raw || {});
  if (!keys.length) {
    block.append(el('div', { class: 'empty', text: '还没有任何按会话的覆盖配置。' }));
    return block;
  }
  const table = el('table', { class: 'grid' },
    el('thead', {}, el('tr', {},
      el('th', { text: 'SCOPE / 会话' }),
      el('th', { text: 'PRESET / 档位' }),
      el('th', { text: 'MEMORY' }),
      el('th', { text: 'HISTORY' }),
      el('th', { text: 'TOOLS' }),
      el('th', { text: 'CONTINUATION' }),
    ))
  );
  const tb = el('tbody');
  for (const k of keys) {
    const c = raw[k] || {};
    tb.append(el('tr', {},
      el('td', { class: 'num', text: k }),
      el('td', { text: c.preset || '—' }),
      el('td', { text: c.memory || 'full' }),
      el('td', { text: c.history === false ? 'off' : 'on' }),
      el('td', { text: c.tools || 'all' }),
      el('td', { text: c.continuation === false ? 'off' : 'on' }),
    ));
  }
  table.append(tb);
  block.append(table,
    el('p', { class: 'empty', text: '这张表这一版只读；改它我会放到下一步（避免和核心编辑混在一起）。' }));
  return block;
}

function overviewBlock(status) {
  const c = status.counts || {};
  const s = status.service || {};
  const block = el('section', { class: 'block', id: 'overview' });
  block.append(
    el('div', { class: 'block-head' },
      el('div', {},
        el('p', { class: 'rl-label', text: 'OVERVIEW / 总览' }),
        el('h2', { text: '核心此刻的状态' }),
        el('div', { class: 'desc', text: '只读；数据来自 systemd 与 data/ 目录' })
      )
    ),
    el('hr', { class: 'rl-rule' }),
    el('div', { class: 'kv' },
      el('div', {}, el('p', { class: 'rl-label', text: 'SERVICE / 服务' }),
        el('div', { class: 'v', text: s.active || 'unknown' })),
      el('div', {}, el('p', { class: 'rl-label', text: 'MEMORY / 内存' }),
        el('div', { class: 'v', text: (s.memory_mb || 0) + ' MB' })),
      el('div', {}, el('p', { class: 'rl-label', text: 'STICKERS / 贴图' }),
        el('div', { class: 'v', text: `${c.stickers || 0} / 待打标 ${c.pending_stickers || 0}` })),
      el('div', {}, el('p', { class: 'rl-label', text: 'MEMORY ENTRIES / 记忆' }),
        el('div', { class: 'v', text: String(c.memory_entries || 0) })),
      el('div', {}, el('p', { class: 'rl-label', text: 'TODOS / 待办' }),
        el('div', { class: 'v', text: String(c.todos || 0) })),
      el('div', {}, el('p', { class: 'rl-label', text: 'CONFIG / 配置' }),
        el('div', { class: 'v', text: CORE.meta.path.split('/').pop() })),
    ),
    el('hr', { class: 'rl-rule' })
  );
  if (!CORE.meta.writable) {
    block.append(el('div', { class: 'banner', text: '配置文件不是 *__custom__* 文件，控制台只读。' }));
  }
  return block;
}

function pendingBlock() {
  const b = el('section', { class: 'block', id: 'pending' },
    el('div', { class: 'block-head' },
      el('div', {},
        el('p', { class: 'rl-label', text: 'PENDING / 后续' }),
        el('h2', { text: '通道层 · 插件层 · 运维' })
      )
    ),
    el('hr', { class: 'rl-rule' }),
    el('p', { class: 'empty', text: '这一版只做核心层。通道（适配器）与插件（人格卡/贴图/黑话）按层分页，下一步再接。' })
  );
  return b;
}

/* ── 保存 ── */
async function save(sec) {
  const changes = DIRTY.get(sec);
  const st = document.getElementById('state-' + sec);
  if (!changes || !Object.keys(changes).length) {
    st.textContent = '没有改动';
    st.className = 'save-state show';
    return;
  }
  st.textContent = '保存中…';
  st.className = 'save-state show';
  try {
    await withMinLoading(api('/api/core', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ changes: { [sec]: changes } }),
    }));
    DIRTY.delete(sec);
    st.textContent = '已保存 · 重启核心后生效';
    st.className = 'save-state show ok';
    await loadCore(true);
  } catch (e) {
    st.textContent = '失败：' + e.message;
    st.className = 'save-state show err';
  }
}

/* ── 加载 ── */
async function loadCore(keepScroll) {
  const y = window.scrollY;
  const [core, status] = await withMinLoading(Promise.all([api('/api/core'), api('/api/status')]));
  CORE = core;
  const main = $('#main');
  main.textContent = '';
  main.append(overviewBlock(status));
  for (const [key, data] of Object.entries(core.sections)) {
    main.append(sectionBlock(key, data));
    if (key === 'gate') main.append(gateChats(core.chats));
  }
  main.append(pendingBlock());

  const dot = $('#dot-core');
  const ok = (status.service || {}).active === 'active';
  dot.className = 'dot ' + (ok ? 'ok' : 'bad');
  $('#txt-core').textContent = ok ? 'CORE ACTIVE' : 'CORE ' + ((status.service || {}).active || '?');
  $('#stamp').textContent = 'v' + status.version + ' · ' + new Date(status.time * 1000).toLocaleTimeString();
  if (keepScroll) window.scrollTo({ top: y });
}

async function boot() {
  const main = $('#main');
  let timer = setTimeout(() => {
    if (!CORE) main.innerHTML = '<div class="empty">读取中…</div>';
  }, LOADING_DELAY);
  try {
    await loadCore();
  } catch (e) {
    main.innerHTML = '<div class="empty">读取失败：' + e.message + '</div>';
  } finally {
    clearTimeout(timer);
  }
  markNav('overview');
  setInterval(async () => {
    try {
      const s = await api('/api/status');
      const ok = (s.service || {}).active === 'active';
      $('#dot-core').className = 'dot ' + (ok ? 'ok' : 'bad');
      $('#txt-core').textContent = ok ? 'CORE ACTIVE' : 'CORE ' + ((s.service || {}).active || '?');
    } catch (e) {}
  }, 30000);
}
boot();
