const loginView = document.querySelector('#login-view');
const workbenchView = document.querySelector('#workbench-view');
const loginForm = document.querySelector('#login-form');
const loginError = document.querySelector('#login-error');
const loginButton = document.querySelector('#login-button');
let currentUser;

const drillRegistry = window.AgentBI.drilldownRegistry;
if (!drillRegistry || drillRegistry.version !== 1 || !drillRegistry.charts) {
  throw new Error('AgentBI 下钻注册中心未加载或版本不兼容');
}
// 下钻结果必须来自真实查询。旧演示注册表不再注入运行时，避免把模拟值当作业务结果。
const drillConfigurations = {};
let managedCharts = [];
let loadedUsers = [];
let loadedSemanticModels = [];
let loadedLiveSemanticModels = [];
let activeSemanticDraft;
let loadedDataSources = [];
let semanticCatalogDatabases = [];
let semanticCatalogDomains = [];
let loadedRoles = [];
let loadedPermissions = [];
let loadedSupersetDashboards = [];
let supersetHomeSnapshot;
let loadedSupersetDatabases = [];
let loadedLlmProviders = [];
let editingLlmProviderId;
let editingSupersetDatabaseId;
let editingSupersetDataset;
let editingSupersetDashboard;
let supersetDashboardEditorMode = 'create';
let pendingSupersetDashboardDelete;
let managingSupersetDashboard;
let editingSupersetChart;
let pendingReport;
let pendingAsset;
let dashboardCanvasMode = 'superset';
let supersetWorkspace;
let supersetWorkspaceDirty = true;
let supersetFrameMode = 'view';
const managedChartKeys = new Set();
let activeView = 'dashboard';
let selectedDrillChart = sessionStorage.getItem('agentbi.drillSource');
if (!drillConfigurations[selectedDrillChart]) selectedDrillChart = undefined;
let workbenchAnalysis;
let workbenchChatId;
let workbenchSelected;
let selectedSupersetContext;
let agentPanelExpanded = false;
let workbenchDrillDepth = 0;
let workbenchPendingDrill = false;

function isValidDrillConfiguration(config) {
  return Boolean(
    config?.published !== false &&
    config?.metric && config?.semanticModel && config?.sourceTitle &&
    ['revenue', 'structure', 'table'].includes(config.sourceType) &&
    Array.isArray(config.bars) && config.bars.length > 0 &&
    Array.isArray(config.rows) && config.rows.length > 0 &&
    Array.isArray(config.questions) && config.questions.length >= 2 &&
    (config.sourceType !== 'table' || (
      Array.isArray(config.sourceColumns) && Array.isArray(config.sourceRows)
    ))
  );
}

async function request(url, options = {}) {
  return window.AgentBI.request(url, options);
}

function showLogin() {
  currentUser = undefined;
  window.AgentBI.session.clear();
  window.AgentBI.supersetWorkspace.clear();
  supersetWorkspace = undefined;
  supersetWorkspaceDirty = true;
  const frame = document.querySelector('#superset-frame');
  frame.removeAttribute('src');
  frame.hidden = true;
  workbenchView.hidden = true;
  loginView.hidden = false;
}

function renderDashboardCanvasMode() {
  const dashboard = activeView === 'dashboard';
  const superset = dashboard;
  document.querySelector('#sales-dashboard').hidden = true;
  document.querySelector('#admin-overview').hidden = true;
  document.querySelector('#permission-card').hidden = true;
  document.querySelector('#superset-canvas').hidden = !superset;
  if (dashboard && currentUser) {
    document.querySelector('#agent-dashboard-name').textContent = superset
      ? (supersetWorkspace?.dashboard_title || '经营总览')
      : (admin ? '全域经营治理' : '华东销售经营分析');
  }
}

function showSupersetUnavailable(message) {
  document.querySelector('#superset-loading').hidden = true;
  document.querySelector('#superset-frame').hidden = true;
  document.querySelector('#native-dashboard-grid').hidden = true;
  document.querySelector('#superset-unavailable-message').textContent = message;
  document.querySelector('#superset-unavailable').hidden = false;
  document.querySelector('#edit-dashboard').disabled = true;
}

function formatNativeValue(value) {
  if (typeof value === 'number') return new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2}).format(value);
  return value === null || value === undefined ? '—' : String(value);
}

function selectNativeChart(chart, dashboardId = supersetWorkspace?.dashboard_id) {
  selectedSupersetContext = {dashboard_id: String(dashboardId || ''), chart_id: String(chart.superset_id)};
  const chip = document.querySelector('#agent-chart-context');
  chip.textContent = `${chart.title} · Chart ${chart.superset_id}`; chip.hidden = false;
  document.querySelector('#agent-context-description').textContent = '已固定当前 AgentBI 图表；问答将携带真实仪表盘与 Chart ID';
  document.querySelector('#agent-question').placeholder = `针对“${chart.title}”提问…`;
  setAgentPanelExpanded(true);
}

function resolveNativeChartAdapter(visualizationType) {
  const type = String(visualizationType || '').toLowerCase();
  if (type.includes('big_number')) return 'big_number';
  if (type.includes('pie') || type.includes('donut')) return 'pie';
  if (type.includes('area')) return 'area';
  if (type.includes('scatter')) return 'scatter';
  if (type.includes('funnel')) return 'funnel';
  if (type.includes('line')) return 'line';
  if (type.includes('bar')) return 'bar';
  if (type.includes('table')) return 'table';
  return 'unsupported';
}

function nativeChartIcon(visualizationType) {
  return {
    big_number: '◉', pie: '◔', area: '⌁', scatter: '∷', funnel: '▽',
    line: '⌁', bar: '▥', table: '▦', unsupported: '?',
  }[resolveNativeChartAdapter(visualizationType)];
}

function renderNativeChartVisual(chart) {
  const visual = document.createElement('div'); visual.className = 'native-chart-visual';
  if (chart.status !== 'ready') {
    visual.classList.add('native-chart-warning');
    visual.innerHTML = '<strong>需要重新配置</strong><p>该图表缺少标准查询上下文。请在 AgentBI 中重新创建后查看真实结果。</p>';
    return visual;
  }
  const rows = Array.isArray(chart.rows) ? chart.rows : []; const columns = Array.isArray(chart.columns) ? chart.columns : [];
  if (!rows.length || !columns.length) {
    visual.classList.add('native-chart-warning'); visual.innerHTML = '<strong>查询成功，暂无数据</strong><p>请检查筛选范围或数据源内容。</p>'; return visual;
  }
  const numericColumns = columns.filter(column => rows.some(row => typeof row[column] === 'number'));
  const numeric = numericColumns.at(-1);
  const dimension = columns.find(column => column !== numeric) || columns[0];
  const adapter = resolveNativeChartAdapter(chart.visualization_type);
  if (adapter === 'big_number' && numeric) {
    visual.classList.add('native-big-number'); const strong = document.createElement('strong'); strong.textContent = formatNativeValue(rows[0][numeric]);
    const small = document.createElement('small'); small.textContent = numeric; visual.append(strong, small); return visual;
  }
  if (adapter === 'pie' && numeric) {
    const palette = ['#3478f6', '#20b5b9', '#7555e8', '#f5ad32', '#ef6c72', '#5f91ee', '#40bf83', '#9a67dc'];
    const data = rows
      .map(row => ({label: formatNativeValue(row[dimension]), value: Math.max(0, Number(row[numeric]) || 0)}))
      .filter(item => item.value > 0)
      .slice(0, palette.length);
    const total = data.reduce((sum, item) => sum + item.value, 0);
    if (!total) {
      visual.classList.add('native-chart-warning');
      visual.innerHTML = '<strong>暂无可绘制数据</strong><p>饼图指标值需要大于 0。</p>';
      return visual;
    }
    visual.classList.add('native-pie');
    const chart = document.createElement('div'); chart.className = 'native-pie-chart';
    let cursor = 0;
    chart.style.background = `conic-gradient(${data.map((item, index) => {
      const start = cursor; cursor += item.value / total * 100;
      return `${palette[index]} ${start}% ${cursor}%`;
    }).join(',')})`;
    const center = document.createElement('div'); center.className = 'native-pie-center';
    const totalValue = document.createElement('strong'); totalValue.textContent = formatNativeValue(total);
    const metric = document.createElement('small'); metric.textContent = numeric;
    center.append(totalValue, metric); chart.append(center);
    const legend = document.createElement('div'); legend.className = 'native-pie-legend';
    data.forEach((item, index) => {
      const row = document.createElement('div'); const dot = document.createElement('i'); dot.style.background = palette[index];
      const label = document.createElement('span'); label.textContent = item.label;
      const value = document.createElement('strong'); value.textContent = `${(item.value / total * 100).toFixed(1)}%`;
      const amount = document.createElement('small'); amount.textContent = formatNativeValue(item.value);
      row.append(dot, label, value, amount); legend.append(row);
    });
    visual.append(chart, legend); return visual;
  }
  if ((adapter === 'line' || adapter === 'area') && numeric) {
    const data = rows.slice(0, 16).map(row => ({
      label: formatNativeValue(row[dimension]), value: Number(row[numeric]) || 0,
    }));
    const values = data.map(item => item.value); const min = Math.min(...values, 0); const max = Math.max(...values, 0);
    const range = max - min || 1; const width = 640; const height = 260; const left = 58; const right = 20; const top = 20; const bottom = 42;
    const plotWidth = width - left - right; const plotHeight = height - top - bottom;
    const x = index => left + (data.length === 1 ? plotWidth / 2 : index / (data.length - 1) * plotWidth);
    const y = value => top + (max - value) / range * plotHeight;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`); svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `${chart.title}${adapter === 'area' ? '面积图' : '折线图'}`); visual.classList.add('native-line');
    if (adapter === 'area') visual.classList.add('native-area');
    for (let index = 0; index <= 4; index += 1) {
      const gridY = top + index / 4 * plotHeight; const value = max - index / 4 * range;
      const grid = document.createElementNS(svg.namespaceURI, 'line'); grid.setAttribute('x1', left); grid.setAttribute('x2', width - right); grid.setAttribute('y1', gridY); grid.setAttribute('y2', gridY); grid.setAttribute('class', 'native-line-grid');
      const label = document.createElementNS(svg.namespaceURI, 'text'); label.setAttribute('x', left - 10); label.setAttribute('y', gridY + 4); label.setAttribute('class', 'native-line-y-label'); label.textContent = formatNativeValue(value);
      svg.append(grid, label);
    }
    const area = document.createElementNS(svg.namespaceURI, 'polygon');
    area.setAttribute('points', `${left},${top + plotHeight} ${data.map((item, index) => `${x(index)},${y(item.value)}`).join(' ')} ${width - right},${top + plotHeight}`); area.setAttribute('class', 'native-line-area'); svg.append(area);
    const line = document.createElementNS(svg.namespaceURI, 'polyline'); line.setAttribute('points', data.map((item, index) => `${x(index)},${y(item.value)}`).join(' ')); line.setAttribute('class', 'native-line-path'); svg.append(line);
    data.forEach((item, index) => {
      const point = document.createElementNS(svg.namespaceURI, 'circle'); point.setAttribute('cx', x(index)); point.setAttribute('cy', y(item.value)); point.setAttribute('r', 4); point.setAttribute('class', 'native-line-point');
      const tooltip = document.createElementNS(svg.namespaceURI, 'title'); tooltip.textContent = `${item.label}：${formatNativeValue(item.value)}`; point.append(tooltip); svg.append(point);
      if (index === 0 || index === data.length - 1 || index % Math.max(1, Math.ceil(data.length / 6)) === 0) {
        const label = document.createElementNS(svg.namespaceURI, 'text'); label.setAttribute('x', x(index)); label.setAttribute('y', height - 15); label.setAttribute('class', 'native-line-x-label'); label.textContent = item.label; svg.append(label);
      }
    });
    visual.append(svg); return visual;
  }
  if (adapter === 'scatter' && numeric) {
    const data = rows.slice(0, 40).map((row, index) => ({
      label: formatNativeValue(row[dimension]),
      x: numericColumns.length > 1 ? Number(row[numericColumns[0]]) || 0 : index + 1,
      y: Number(row[numeric]) || 0,
    }));
    const xValues = data.map(item => item.x); const yValues = data.map(item => item.y);
    const xMin = Math.min(...xValues); const xRange = Math.max(...xValues) - xMin || 1;
    const yMin = Math.min(...yValues); const yRange = Math.max(...yValues) - yMin || 1;
    const width = 640; const height = 260; const left = 52; const right = 20; const top = 18; const bottom = 35;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', `0 0 ${width} ${height}`); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', `${chart.title}散点图`);
    visual.classList.add('native-scatter');
    for (let index = 0; index <= 4; index += 1) {
      const gridY = top + index / 4 * (height - top - bottom); const grid = document.createElementNS(svg.namespaceURI, 'line');
      grid.setAttribute('x1', left); grid.setAttribute('x2', width - right); grid.setAttribute('y1', gridY); grid.setAttribute('y2', gridY); grid.setAttribute('class', 'native-line-grid'); svg.append(grid);
    }
    data.forEach(item => {
      const point = document.createElementNS(svg.namespaceURI, 'circle');
      point.setAttribute('cx', left + (item.x - xMin) / xRange * (width - left - right));
      point.setAttribute('cy', top + (1 - (item.y - yMin) / yRange) * (height - top - bottom));
      point.setAttribute('r', 6); point.setAttribute('class', 'native-scatter-point');
      const tooltip = document.createElementNS(svg.namespaceURI, 'title'); tooltip.textContent = `${item.label}：${formatNativeValue(item.y)}`; point.append(tooltip); svg.append(point);
    });
    visual.append(svg); return visual;
  }
  if (adapter === 'funnel' && numeric) {
    const data = rows.map(row => ({label: formatNativeValue(row[dimension]), value: Math.max(0, Number(row[numeric]) || 0)})).filter(item => item.value > 0).sort((a, b) => b.value - a.value).slice(0, 8);
    const max = Math.max(...data.map(item => item.value), 1); visual.classList.add('native-funnel');
    data.forEach((item, index) => {
      const row = document.createElement('div'); const bar = document.createElement('i'); const label = document.createElement('span'); const value = document.createElement('strong');
      bar.style.width = `${Math.max(18, item.value / max * 100)}%`; bar.style.setProperty('--funnel-index', index); label.textContent = item.label; value.textContent = formatNativeValue(item.value);
      row.append(bar, label, value); visual.append(row);
    }); return visual;
  }
  if (adapter === 'bar' && numeric) {
    visual.classList.add('native-bars'); const data = rows.slice(0, 12); const max = Math.max(...data.map(row => Number(row[numeric]) || 0), 1);
    data.forEach(row => { const item = document.createElement('div'); const label = document.createElement('span'); label.textContent = formatNativeValue(row[dimension]);
      const track = document.createElement('i'); const fill = document.createElement('b'); fill.style.width = `${Math.max(2, (Number(row[numeric]) || 0) / max * 100)}%`;
      const amount = document.createElement('em'); amount.textContent = formatNativeValue(row[numeric]); track.append(fill); item.append(label, track, amount); visual.append(item); });
    return visual;
  }
  if (adapter === 'unsupported') {
    visual.classList.add('native-chart-warning');
    const title = document.createElement('strong'); title.textContent = '该图表类型尚未适配';
    const message = document.createElement('p'); message.textContent = `Superset 类型：${chart.visualization_type || '未知'}。数据已连接，但 AgentBI 暂不使用错误图形替代。`;
    visual.append(title, message); return visual;
  }
  visual.classList.add('native-table-wrap'); const table = document.createElement('table'); const head = document.createElement('thead'); const headRow = document.createElement('tr');
  columns.slice(0, 8).forEach(column => { const th = document.createElement('th'); th.textContent = column; headRow.append(th); }); head.append(headRow); const body = document.createElement('tbody');
  rows.slice(0, 20).forEach(row => { const tr = document.createElement('tr'); columns.slice(0, 8).forEach(column => { const td = document.createElement('td'); td.textContent = formatNativeValue(row[column]); tr.append(td); }); body.append(tr); });
  table.append(head, body); visual.append(table); return visual;
}

function renderNativeDashboard(dashboard) {
  const grid = document.querySelector('#native-dashboard-grid'); document.querySelector('#dashboard-title').textContent = dashboard.title || '经营总览';
  const charts = Array.isArray(dashboard.charts) ? dashboard.charts : [];
  if (!charts.length) grid.innerHTML = '<div class="native-dashboard-empty"><strong>当前仪表盘还没有图表</strong><p>请通过“仪表盘管理 → 添加图表”创建真实分析图表。</p></div>';
  else grid.replaceChildren(...charts.map(chart => { const card = document.createElement('article'); card.className = 'native-chart-card'; const header = document.createElement('header');
    const title = document.createElement('div'); const strong = document.createElement('strong'); strong.textContent = chart.title; const small = document.createElement('small'); small.textContent = `Chart ${chart.superset_id} · ${chart.status === 'ready' ? '真实查询' : '待配置'}`; title.append(strong, small);
    const ai = document.createElement('button'); ai.type = 'button'; ai.textContent = '✦ AI 分析'; ai.addEventListener('click', () => selectNativeChart(chart)); header.append(title, ai); card.append(header, renderNativeChartVisual(chart)); return card; }));
  grid.hidden = false;
}

function setSupersetFrameMode(mode) {
  if (!supersetWorkspace?.available) return;
  if (mode === 'edit' && !supersetWorkspace.can_edit) {
    showManagementFeedback('当前账号没有编辑 Superset 仪表盘的权限', true);
    return;
  }
  supersetFrameMode = mode;
  const frame = document.querySelector('#superset-frame');
  const loading = document.querySelector('#superset-loading');
  const target = mode === 'edit' ? supersetWorkspace.edit_url : supersetWorkspace.view_url;
  document.querySelector('#superset-mode-label').textContent = mode === 'edit'
    ? '正在 Superset 中编辑，修改直接保存至上游'
    : '浏览模式';
  document.querySelector('#edit-dashboard').textContent = mode === 'edit'
    ? '返回浏览模式' : '在 Superset 中编辑';
  document.querySelector('#superset-unavailable').hidden = true;
  loading.hidden = false;
  frame.hidden = true;
  frame.onload = () => {
    if (dashboardCanvasMode !== 'superset') return;
    loading.hidden = true;
    frame.hidden = false;
  };
  frame.src = target;
  document.querySelector('#agent-dashboard-name').textContent = supersetWorkspace.dashboard_title || '经营总览';
}

async function loadSupersetWorkspace({ force = false } = {}) {
  if (!currentUser || dashboardCanvasMode !== 'superset') return;
  document.querySelector('#edit-dashboard').disabled = true;
  document.querySelector('#superset-unavailable').hidden = true;
  document.querySelector('#superset-loading').hidden = false;
  try {
    supersetWorkspace = await window.AgentBI.supersetWorkspace.load(force);
    if (!supersetWorkspace.available) {
      showSupersetUnavailable(supersetWorkspace.message || 'Superset 服务当前不可用，可继续使用本地降级画布。');
      return;
    }
    document.querySelector('#edit-dashboard').disabled = !supersetWorkspace.can_edit;
    const native = await request('/api/v1/superset/workspace/native');
    document.querySelector('#superset-loading').hidden = true; document.querySelector('#superset-unavailable').hidden = true;
    document.querySelector('#superset-frame').hidden = true; document.querySelector('#superset-mode-label').textContent = '原生模式 · 真实查询';
    renderNativeDashboard(native.dashboard); document.querySelector('#agent-dashboard-name').textContent = native.dashboard.title || '经营总览';
    supersetHomeSnapshot = native.dashboard;
    renderSupersetAssetSummary();
    supersetWorkspaceDirty = false;
  } catch (error) {
    showSupersetUnavailable(error.message);
  }
}

function selectDashboardCanvas(mode) {
  dashboardCanvasMode = 'superset';
  renderDashboardCanvasMode();
  loadSupersetWorkspace();
}

function showWorkbench(user) {
  currentUser = window.AgentBI.session.accept(user);
  const permissions = new Set(user.permissions);
  document.querySelectorAll('[data-permission]').forEach(item => { item.hidden = !permissions.has(item.dataset.permission); });
  const hasGovernance = ['dashboard:manage', 'semantic_model:manage', 'datasource:manage', 'user:manage', 'audit:view']
    .some(permission => permissions.has(permission));
  document.querySelectorAll('.admin-only:not([data-permission])').forEach(item => { item.hidden = !hasGovernance; });
  document.querySelectorAll('.admin-data').forEach(item => { item.hidden = user.role !== 'admin'; });
  document.querySelectorAll('.chart-menu-trigger').forEach(item => { item.hidden = !permissions.has('drilldown:use'); });
  document.querySelector('#user-name').textContent = user.display_name;
  document.querySelector('#account-username').textContent = user.username;
  document.querySelector('#user-role').textContent = user.role_label;
  document.querySelector('#avatar').textContent = user.display_name.slice(0, 1);
  document.querySelector('#agent-scope').textContent = user.data_scope;
  const admin = user.role === 'admin';
  document.querySelector('#dashboard-title').textContent = admin ? '全域经营与系统治理' : '华东销售经营分析';
  document.querySelector('#dashboard-subtitle').textContent = admin ? '统一管理数据资产、语义模型与安全状态' : '实时洞察关键指标与业务变化';
  document.querySelector('#agent-dashboard-name').textContent = admin ? '全域经营治理' : '华东销售经营分析';
  document.querySelector('#sales-dashboard').hidden = admin;
  document.querySelector('#admin-overview').hidden = !admin;
  document.querySelector('#user-permissions').hidden = admin;
  document.querySelector('#admin-permissions').hidden = !admin;
  loginView.hidden = true;
  workbenchView.hidden = false;
  switchView('dashboard');
  loadManagedCharts();
  loadLiveSemanticModels().catch(error => showManagementFeedback(error.message, true));
}

function populateLiveModelSelectors() {
  const agentSelect = document.querySelector('#agent-semantic-model');
  const chartSelect = document.querySelector('#new-chart-model');
  const agentValue = agentSelect.value;
  const chartValue = chartSelect.value;
  const agentOptions = loadedLiveSemanticModels.map(model => {
    const option = document.createElement('option');
    option.value = String(model.id);
    option.textContent = `${model.name}（${model.domain_name} · ID ${model.id}）`;
    return option;
  });
  const chartOptions = loadedLiveSemanticModels.map(model => {
    const option = document.createElement('option');
    option.value = model.key;
    option.textContent = `${model.name}（${model.domain_name} · ID ${model.id}）`;
    return option;
  });
  agentSelect.replaceChildren(...agentOptions);
  chartSelect.replaceChildren(...chartOptions);
  if (agentValue && agentOptions.some(option => option.value === agentValue)) agentSelect.value = agentValue;
  if (chartValue && chartOptions.some(option => option.value === chartValue)) chartSelect.value = chartValue;
}

async function loadLiveSemanticModels() {
  const body = await request('/api/v1/supersonic/models');
  const models = Array.isArray(body.models) ? body.models : [];
  loadedLiveSemanticModels = models.filter(model =>
    Number.isInteger(model?.id) && model.id > 0 && typeof model.key === 'string' &&
    typeof model.name === 'string' && typeof model.domain_name === 'string'
  );
  populateLiveModelSelectors();
  const total = document.querySelector('#live-model-total');
  if (total) total.textContent = `${loadedLiveSemanticModels.length} 个真实模型`;
  return loadedLiveSemanticModels;
}

function fillSelect(select, items, label) {
  select.replaceChildren(...items.map(item => {
    const option = document.createElement('option');
    option.value = String(item.id);
    option.textContent = label(item);
    return option;
  }));
}

function formatDraftItems(items, suffix) {
  return items.map(item => `${item.name}=${item.field}${suffix(item)}`).join('\n');
}

function parseDraftItems(value, kind, originals) {
  const lines = value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  return lines.map(line => {
    const equal = line.indexOf('=');
    if (equal < 1) throw new Error(`“${line}”格式错误，应为：名称=字段`);
    const name = line.slice(0, equal).trim();
    const [field, qualifier = ''] = line.slice(equal + 1).split(':').map(item => item.trim());
    if (!name || !field) throw new Error(`“${line}”缺少名称或字段`);
    const original = originals.find(item => item.field === field) || {};
    if (kind === 'identifier') return { name, field, type: 'primary', synonyms: original.synonyms || [] };
    if (kind === 'dimension') {
      const type = qualifier || 'categorical';
      if (!['categorical', 'time'].includes(type)) throw new Error(`维度类型仅支持 categorical 或 time：${line}`);
      return { name, field, type, synonyms: original.synonyms || [] };
    }
    const aggregation = (qualifier || 'SUM').toUpperCase();
    if (!['SUM', 'AVG', 'MAX', 'MIN', 'COUNT'].includes(aggregation)) throw new Error(`不支持的聚合方式：${line}`);
    return { name, field, aggregation, synonyms: original.synonyms || [] };
  });
}

async function openSemanticDraftEditor() {
  const error = document.querySelector('#semantic-draft-error');
  error.hidden = true; activeSemanticDraft = undefined;
  document.querySelector('#semantic-draft-review').hidden = true;
  document.querySelector('#publish-semantic-draft').disabled = true;
  document.querySelector('#semantic-generation-source').textContent = '正在读取真实 Superset Dataset 与 SuperSonic 建模目录…';
  document.querySelector('#semantic-draft-editor').hidden = false;
  try {
    const [assetsBody, catalog, provider] = await Promise.all([
      request('/api/v1/admin/superset/data-assets'),
      request('/api/v1/admin/semantic-drafts/catalog'),
      request('/api/v1/admin/llm-provider'),
    ]);
    const assets = window.AgentBI.parseSupersetDataAssets(assetsBody);
    loadedDataSources = assets.datasets;
    loadedSupersetDatabases = assets.databases;
    semanticCatalogDatabases = catalog.databases || [];
    semanticCatalogDomains = catalog.domains || [];
    fillSelect(document.querySelector('#semantic-draft-dataset'),
      loadedDataSources.map(item => ({...item, id: item.superset_id})),
      item => `${item.name}（${item.database_name} · ID ${item.superset_id}）`);
    const providerSelect = document.querySelector('#semantic-draft-provider');
    providerSelect.innerHTML = '<option value="">仅用字段元数据推断</option>';
    (provider.items || []).forEach(item => {
      const option = document.createElement('option');
      option.value = String(item.id);
      option.textContent = `${item.model}${item.id === provider.active_id ? '（当前生效）' : ''}`;
      option.dataset.model = item.model;
      providerSelect.append(option);
    });
    providerSelect.value = provider.active_id ? String(provider.active_id) : '';
    fillSelect(document.querySelector('#semantic-draft-domain'), semanticCatalogDomains,
      item => `${item.name}（ID ${item.id}）`);
    fillSelect(document.querySelector('#semantic-draft-database'), semanticCatalogDatabases,
      item => `${item.name}${item.type ? ` · ${item.type}` : ''}（ID ${item.id}）`);
    if (!loadedDataSources.length || !(catalog.domains || []).length || !(catalog.databases || []).length) {
      throw new Error('建模前至少需要一个 Superset Dataset、SuperSonic 主题域和数据库连接');
    }
    const updateProviderNote = () => {
      const option = providerSelect.selectedOptions[0];
      document.querySelector('#semantic-generation-source').textContent = option?.value
        ? `本次将使用 ${option.dataset.model} 推荐维度、指标、同义词和下钻路径；不会改变系统默认模型。仅发送 Dataset 名称和字段元数据。`
        : '本次不调用 LLM，仅使用可解释的 Dataset 字段元数据推断。';
    };
    providerSelect.onchange = updateProviderNote;
    updateProviderNote();
    syncSemanticDatabaseSelection();
  } catch (cause) {
    error.textContent = cause.message; error.hidden = false;
  }
}

function syncSemanticDatabaseSelection() {
  const datasetSelect = document.querySelector('#semantic-draft-dataset');
  const databaseSelect = document.querySelector('#semantic-draft-database');
  const note = document.querySelector('#semantic-database-match');
  const dataset = loadedDataSources.find(item => item.superset_id === Number(datasetSelect.value));
  const source = loadedSupersetDatabases.find(item => item.superset_id === dataset?.database_id);
  const sourceName = String(dataset?.database_name || '').trim().toLowerCase();
  const sourceType = String(source?.backend || '').trim().toLowerCase();
  const match = semanticCatalogDatabases.find(item =>
    String(item.name || '').trim().toLowerCase() === sourceName &&
    String(item.type || '').trim().toLowerCase() === sourceType
  );
  databaseSelect.value = match ? String(match.id) : '';
  note.classList.toggle('match-ok', Boolean(match));
  note.textContent = match
    ? `已匹配同源连接：${match.name} · ${match.type}`
    : `未找到与 Superset「${dataset?.database_name || '未知来源'} · ${source?.backend || '未知类型'}」对应的 SuperSonic 连接，请先配置同源连接。`;
}

function closeSemanticDraftEditor() {
  document.querySelector('#semantic-draft-editor').hidden = true;
  activeSemanticDraft = undefined;
}

async function generateSemanticDraft() {
  const error = document.querySelector('#semantic-draft-error');
  const button = document.querySelector('#generate-semantic-draft');
  error.hidden = true; button.disabled = true; button.textContent = '正在分析字段…';
  try {
    const datasetId = Number(document.querySelector('#semantic-draft-dataset').value);
    const providerValue = document.querySelector('#semantic-draft-provider').value;
    const body = await request('/api/v1/admin/semantic-drafts/generate', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token},
      body: JSON.stringify({
        dataset_id: datasetId,
        provider_id: providerValue ? Number(providerValue) : null,
        use_llm: Boolean(providerValue),
      }),
    });
    activeSemanticDraft = body.draft;
    const draft = activeSemanticDraft;
    document.querySelector('#semantic-draft-name').value = draft.model.name;
    document.querySelector('#semantic-draft-biz-name').value = draft.model.biz_name;
    document.querySelector('#semantic-draft-description').value = draft.model.description;
    document.querySelector('#semantic-draft-identifiers').value = formatDraftItems(draft.identifiers, () => '');
    document.querySelector('#semantic-draft-dimensions').value = formatDraftItems(draft.dimensions, item => `:${item.type}`);
    document.querySelector('#semantic-draft-measures').value = formatDraftItems(draft.measures, item => `:${item.aggregation}`);
    document.querySelector('#semantic-draft-drilldown').value = draft.drilldown_path.join('，');
    const source = document.querySelector('#semantic-generation-source');
    source.textContent = `${draft.generation.label}。所有推荐项必须由管理员审核后才能发布。`;
    source.classList.toggle('ai-source', Boolean(draft.generation.ai_generated));
    const warnings = document.querySelector('#semantic-draft-warnings');
    warnings.hidden = !draft.warnings.length;
    warnings.textContent = draft.warnings.join(' ');
    document.querySelector('#semantic-draft-review').hidden = false;
    document.querySelector('#publish-semantic-draft').disabled = draft.measures.length === 0;
  } catch (cause) {
    error.textContent = cause.message; error.hidden = false;
  } finally {
    button.disabled = false; button.textContent = '生成草稿';
  }
}

function switchView(view) {
  activeView = view;
  const drilldown = view === 'drilldown';
  const registry = view === 'drill-registry';
  const chartManagement = view === 'chart-management';
  const dashboard = view === 'dashboard';
  const agentVisible = dashboard || drilldown;
  workbenchView.classList.toggle('agent-hidden', !agentVisible);
  document.querySelector('.agent-panel').hidden = !agentVisible;
  if (dashboard) setAgentPanelExpanded(false);
  if (drilldown) setAgentPanelExpanded(true);
  document.querySelectorAll('.dashboard-section').forEach(item => { item.hidden = !dashboard; });
  document.querySelectorAll('.drilldown-view,.registry-view').forEach(item => { item.hidden = true; });
  if (!dashboard) {
    const target = document.querySelector(`#${view}-view`);
    if (target) target.hidden = false;
  }
  document.querySelector('#dashboard-agent').hidden = !dashboard;
  document.querySelector('#drill-agent').hidden = !drilldown || !workbenchAnalysis;
  document.querySelector('.agent-input').hidden = drilldown && !workbenchAnalysis;
  document.querySelectorAll('.nav-item[data-view]').forEach(item => {
    item.classList.toggle('active', item.dataset.view === view);
  });
  if (dashboard && currentUser) {
    const admin = currentUser.role === 'admin';
    document.querySelector('#sales-dashboard').hidden = admin;
    document.querySelector('#admin-overview').hidden = !admin;
    document.querySelector('#user-permissions').hidden = admin;
    document.querySelector('#admin-permissions').hidden = !admin;
    renderDashboardCanvasMode();
    loadSupersetWorkspace({force: supersetWorkspaceDirty});
  }
  if (registry) renderRegistryCenter();
  if (chartManagement) {
    renderChartManagement();
    loadSupersetDashboardAssets().catch(error => showManagementFeedback(error.message, true));
  }
  if (document.querySelector(`#${view}-view.module-view`)) loadModuleView(view);
}

function setAgentPanelExpanded(expanded) {
  agentPanelExpanded = expanded;
  const collapsible = activeView === 'dashboard';
  workbenchView.classList.toggle('agent-collapsed', collapsible && !expanded);
  const button = document.querySelector('#agent-panel-toggle');
  button.setAttribute('aria-expanded', String(expanded));
  button.setAttribute('aria-label', expanded ? '收起 AgentBI 分析助手' : '展开 AgentBI 分析助手');
  button.title = expanded ? '收起分析助手' : '展开分析助手';
}

function receiveSupersetChartSelection(event) {
  const frame = document.querySelector('#superset-frame');
  let supersetOrigin;
  try {
    supersetOrigin = new URL(frame.src, window.location.href).origin;
  } catch {
    return;
  }
  if (event.origin !== supersetOrigin || event.source !== frame.contentWindow) return;
  const payload = event.data;
  if (!payload || payload.type !== 'agentbi:chart-selected' || payload.version !== 1) return;
  const context = payload.context;
  if (!context || typeof context.dashboard_id !== 'string' || typeof context.chart_id !== 'string') return;
  if (!/^[A-Za-z0-9_.:-]{1,128}$/.test(context.chart_id)) return;
  const datasetId = typeof context.dataset_id === 'string' && /^[A-Za-z0-9_.:-]{1,128}$/.test(context.dataset_id)
    ? context.dataset_id : undefined;
  selectedSupersetContext = {
    dashboard_id: context.dashboard_id.slice(0, 128),
    chart_id: context.chart_id,
    ...(datasetId ? {dataset_id: datasetId} : {}),
  };
  const title = typeof payload.title === 'string' && payload.title.trim()
    ? payload.title.trim().slice(0, 200) : `图表 ${context.chart_id}`;
  const chartChip = document.querySelector('#agent-chart-context');
  chartChip.textContent = `${title} · Chart ${context.chart_id}`;
  chartChip.hidden = false;
  document.querySelector('#agent-context-description').textContent =
    '已固定当前 Superset 图表；问答将携带 Chart ID 与 Dataset ID';
  document.querySelector('#agent-question').placeholder = `针对“${title}”提问…`;
  setAgentPanelExpanded(true);
}

window.addEventListener('message', receiveSupersetChartSelection);

async function loadManagedCharts() {
  if (!currentUser) return;
  try {
    managedChartKeys.forEach(chartKey => delete drillConfigurations[chartKey]);
    managedChartKeys.clear();
    const endpoint = currentUser.role === 'admin' ? '/api/v1/admin/charts' : '/api/v1/charts';
    const body = await request(endpoint);
    managedCharts = window.AgentBI.parseManagedCharts(body.charts);
    managedCharts.forEach(chart => managedChartKeys.add(chart.chart_key));
    selectedDrillChart = undefined;
    sessionStorage.removeItem('agentbi.drillSource');
    initializeDrillableCharts();
    bindChartMenuEvents();
  } catch (error) {
    console.warn('无法加载数据库图表配置', error);
  }
}

function formatTimestamp(value) {
  if (!value) return '尚未登录';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false });
}

function replaceTableRows(bodyId, rows) {
  const body = document.querySelector(`#${bodyId}`);
  body.replaceChildren(...rows.map(values => {
    const row = document.createElement('tr');
    values.forEach(value => {
      const cell = document.createElement('td');
      cell.textContent = String(value);
      row.append(cell);
    });
    return row;
  }));
}

function renderDashboardList(dashboards) {
  const container = document.querySelector('#dashboard-list');
  container.replaceChildren(...dashboards.map(dashboard => {
    const card = document.createElement('article'); card.className = 'asset-card';
    const header = document.createElement('header');
    const title = document.createElement('strong'); title.textContent = dashboard.title;
    const scope = document.createElement('span'); scope.textContent = dashboard.data_scope;
    header.append(title, scope);
    const description = document.createElement('p'); description.textContent = dashboard.description;
    const meta = document.createElement('div');
    meta.textContent = `${dashboard.chart_count} 个图表　·　${dashboard.role}`;
    const open = document.createElement('button'); open.type = 'button'; open.textContent = '打开仪表盘';
    open.addEventListener('click', () => {
      switchView('dashboard');
      selectDashboardCanvas(dashboard.source === 'superset' ? 'superset' : 'local');
    });
    card.append(header, description, meta, open);
    return card;
  }));
}

function renderReportList(reports) {
  const container = document.querySelector('#report-list');
  if (!reports.length) {
    const empty = document.createElement('section'); empty.className = 'module-empty';
    const title = document.createElement('strong'); title.textContent = '还没有已保存的分析报告';
    const text = document.createElement('p'); text.textContent = '点击右上角生成当前仪表盘快照，系统会保存数据范围和证据路径。';
    empty.append(title, text); container.replaceChildren(empty); return;
  }
  container.replaceChildren(...reports.map(report => {
    const card = document.createElement('article'); card.className = 'asset-card report-card';
    const header = document.createElement('header');
    const title = document.createElement('strong'); title.textContent = report.title;
    const time = document.createElement('span'); time.textContent = formatTimestamp(report.created_at);
    header.append(title, time);
    const summary = document.createElement('p'); summary.textContent = report.summary;
    const meta = document.createElement('div'); meta.textContent = `${report.dashboard_name}　·　${report.data_scope}`;
    const evidence = document.createElement('small'); evidence.className = 'report-evidence';
    evidence.textContent = `证据路径：${report.evidence_path}`; evidence.hidden = true;
    const actions = document.createElement('div'); actions.className = 'report-actions';
    actions.append(
      actionButton('查看证据', '', event => {
        evidence.hidden = !evidence.hidden;
        event.currentTarget.textContent = evidence.hidden ? '查看证据' : '收起证据';
      }),
      actionButton('删除报告', 'danger-action', () => openReportDeleteConfirmation(report)),
    );
    card.append(header, summary, meta, evidence, actions); return card;
  }));
}

function renderModuleRows(bodyId, items, valuesFor, actionFor) {
  const body = document.querySelector(`#${bodyId}`);
  body.replaceChildren(...items.map(item => {
    const row = document.createElement('tr');
    valuesFor(item).forEach(value => {
      const cell = document.createElement('td'); cell.textContent = String(value); row.append(cell);
    });
    if (actionFor) {
      const action = document.createElement('td'); action.append(actionFor(item)); row.append(action);
    }
    return row;
  }));
}

async function runModuleAction(button, url, successView) {
  const original = button.textContent;
  button.disabled = true; button.textContent = '检查中…';
  try {
    const body = await request(url, {
      method: 'POST', headers: { 'X-AgentBI-CSRF': currentUser.csrf_token },
    });
    showManagementFeedback(body.result.message, body.result.status !== 'ready');
    await loadModuleView(successView);
  } catch (error) {
    showManagementFeedback(error.message, true);
  } finally {
    button.disabled = false; button.textContent = original;
  }
}

function assetActionGroup(kind, asset) {
  const group = document.createElement('div'); group.className = 'registry-actions';
  const checkLabel = kind === 'semantic-models' ? '检查同步' : '测试连接';
  const checkSuffix = kind === 'semantic-models' ? 'sync' : 'test';
  group.append(actionButton(checkLabel, 'module-action', event => runModuleAction(
    event.currentTarget,
    `/api/v1/admin/${kind}/${encodeURIComponent(asset.name)}/${checkSuffix}`,
    kind,
  )));
  group.append(actionButton('修改', 'module-action', () => openAssetEditor(kind, asset)));
  group.append(actionButton(asset.status === 'active' ? '下线' : '上线', 'warning-action', () => {
    updateAssetStatus(kind, asset, asset.status === 'active' ? 'offline' : 'active');
  }));
  const remove = actionButton('删除', 'danger-action', () => openAssetDeleteConfirmation(kind, asset));
  remove.disabled = asset.is_system || asset.charts > 0;
  if (remove.disabled) remove.title = '系统资产或存在图表引用，不能删除';
  group.append(remove);
  return group;
}

async function updateAssetStatus(kind, asset, statusValue) {
  const payload = kind === 'semantic-models'
    ? { subject_area: asset.subject_area, description: asset.description, status: statusValue }
    : { source_type: asset.type, description: asset.description, status: statusValue };
  try {
    await request(`/api/v1/admin/${kind}/${encodeURIComponent(asset.name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token },
      body: JSON.stringify(payload),
    });
    await loadModuleView(kind);
    showManagementFeedback(`${asset.name} 已${statusValue === 'active' ? '上线' : '下线'}`);
  } catch (error) { showManagementFeedback(error.message, true); }
}

async function loadModuleView(view) {
  const target = document.querySelector(`#${view}-view`);
  target.setAttribute('aria-busy', 'true');
  try {
    if (view === 'my-dashboards') {
      renderDashboardList((await request('/api/v1/dashboards')).dashboards || []);
    } else if (view === 'reports') {
      renderReportList(window.AgentBI.parseSavedReports(
        (await request('/api/v1/reports')).reports,
      ));
    } else if (view === 'semantic-models') {
      const [models, providerBody, catalog] = await Promise.all([
        loadLiveSemanticModels(), request('/api/v1/admin/llm-provider'),
        request('/api/v1/admin/semantic-drafts/catalog'),
      ]);
      loadedSemanticModels = models;
      loadedLlmProviders = providerBody.items || [];
      semanticCatalogDomains = catalog.domains || [];
      renderLlmProviderRows();
      renderSemanticDomainRows();
      renderModuleRows('semantic-model-table-body', loadedSemanticModels, model => [
        model.id, model.name, model.domain_name, model.biz_name || '—',
        model.status === 'active' ? '● 已启用' : '● 已下线',
      ]);
    } else if (view === 'data-sources') {
      const assets = window.AgentBI.parseSupersetDataAssets(
        await request('/api/v1/admin/superset/data-assets'),
      );
      loadedDataSources = assets.datasets;
      const databases = assets.databases;
      loadedSupersetDatabases = databases;
      document.querySelector('#real-database-total').textContent = String(databases.length);
      document.querySelector('#real-dataset-total').textContent = String(loadedDataSources.length);
      document.querySelector('#real-database-engines').textContent =
        [...new Set(databases.map(database => database.backend))].join('、') || '—';
      document.querySelector('#real-source-status').textContent = '已同步';
      document.querySelector('#available-engine-list').textContent =
        (assets.available_engines || []).map(engine => engine.name).join('、') || '未发现可用连接器';
      renderModuleRows('superset-database-table-body', databases, database => [
        database.name, database.backend, database.dataset_count,
        database.expose_in_sqllab ? '● 已开放' : '—', database.superset_id,
      ], database => {
        const group = document.createElement('div'); group.className = 'registry-actions';
        group.append(actionButton('修改', '', () => openDatabaseEditor(database)));
        const remove = actionButton('删除', 'danger-action', () => deleteSupersetAsset('databases', database));
        remove.disabled = database.dataset_count > 0;
        if (remove.disabled) remove.title = `仍有 ${database.dataset_count} 个 Dataset，不能删除`;
        group.append(remove); return group;
      });
      renderModuleRows('data-source-table-body', loadedDataSources, dataset => [
        dataset.name, dataset.database_name, dataset.schema,
        dataset.kind === 'virtual' ? '虚拟数据集' : '物理表', dataset.superset_id,
      ], dataset => {
        const group = document.createElement('div'); group.className = 'registry-actions';
        group.append(actionButton('创建图表', 'module-action', () =>
          openSupersetChartEditor(dataset)));
        group.append(actionButton('查看字段', '', () => openDatasetDetail(dataset)));
        group.append(actionButton('修改说明', '', () => openDatasetDescriptionEditor(dataset)));
        group.append(actionButton('删除', 'danger-action', () => deleteSupersetAsset('datasets', dataset)));
        return group;
      });
    } else if (view === 'user-roles') {
      const [userBody, roleBody, permissionBody] = await Promise.all([
        request('/api/v1/admin/users'), request('/api/v1/admin/roles'),
        request('/api/v1/admin/permissions'),
      ]);
      loadedUsers = window.AgentBI.parseGovernedUsers(userBody.users);
      loadedRoles = window.AgentBI.parseGovernedRoles(roleBody.roles);
      loadedPermissions = window.AgentBI.parseGovernedPermissions(permissionBody.permissions);
      renderModuleRows('user-role-table-body', loadedUsers, user => [
        user.display_name, user.username, user.role_name,
        user.data_scope, user.is_active ? '● 正常' : '● 已停用', formatTimestamp(user.last_login_at),
      ], user => actionButton('编辑权限', 'module-action', () => openUserEditor(user)));
      renderModuleRows('role-table-body', loadedRoles, role => [
        role.name, role.code, role.user_count, role.permissions.length,
        role.builtin ? '内置角色' : '自定义角色',
      ], role => roleActionGroup(role));
    } else if (view === 'audit-security') {
      const events = window.AgentBI.parseAuditEvents(
        (await request('/api/v1/admin/audit-events')).events,
      );
      const eventLabels = {
        login: '用户登录', logout: '用户退出', chart_created: '创建图表', chart_updated: '修改图表',
        chart_published: '上线图表', chart_offlined: '下线图表', chart_deleted: '删除图表',
        report_created: '生成报告', report_deleted: '删除报告', user_created: '新增用户',
        user_updated: '修改用户权限',
        semantic_model_checked: '检查语义模型', data_source_tested: '测试数据源',
        semantic_model_created: '新增语义模型', semantic_model_updated: '修改语义模型',
        semantic_model_deleted: '删除语义模型', data_source_created: '新增数据源',
        data_source_updated: '修改数据源', data_source_deleted: '删除数据源',
        role_created: '新增角色', role_updated: '修改角色', role_deleted: '删除角色',
      };
      replaceTableRows('audit-event-table-body', events.map(event => [
        formatTimestamp(event.created_at), eventLabels[event.event_type] || event.event_type,
        event.actor, event.outcome === 'success' ? '成功' : event.outcome === 'failed' ? '失败' : '拒绝',
        event.source_ip || '本机', event.detail || '—',
      ]));
    }
  } catch (error) {
    showManagementFeedback(error.message, true);
  } finally {
    target.setAttribute('aria-busy', 'false');
  }
}

function renderManagedDashboardCharts() {
  document.querySelectorAll('.managed-dashboard-chart').forEach(card => card.remove());
}

function renderChartManagement() {
  const body = document.querySelector('#managed-chart-table-body');
  const typeLabels = { bar: '柱状图', line: '折线图', donut: '环图', table: '指标表格' };
  body.replaceChildren(...managedCharts.map(chart => {
    const row = document.createElement('tr');
    const values = [chart.title, chart.dataset_name, chart.metric, typeLabels[chart.visualization_type], chart.dimensions.join(' → ')];
    values.forEach((value, index) => {
      const cell = document.createElement('td');
      if (index === 0) {
        const strong = document.createElement('strong'); strong.textContent = value;
        const small = document.createElement('small'); small.textContent = `Chart ID：${chart.chart_key}`;
        cell.append(strong, small);
      } else cell.textContent = value;
      row.append(cell);
    });
    const status = document.createElement('td');
    status.innerHTML = chart.is_published
      ? '<span class="registry-status ready">● 已发布</span>'
      : '<span class="registry-status offline">● 已下线</span>';
    row.append(status);
    const action = document.createElement('td');
    appendGovernanceActions(action, chart, false);
    row.append(action);
    return row;
  }));
}

function renderSupersetAssetSummary() {
  const home = loadedSupersetDashboards.find(dashboard => dashboard.is_home);
  const available = loadedSupersetDashboards.filter(dashboard => dashboard.available).length;
  const charts = home && Number(supersetHomeSnapshot?.superset_id) === Number(home.superset_id)
    ? (supersetHomeSnapshot.charts || []) : undefined;
  const ready = charts?.filter(chart => chart.status === 'ready').length;
  document.querySelector('#superset-summary-dashboard-total').textContent = String(loadedSupersetDashboards.length);
  document.querySelector('#superset-summary-dashboard-note').textContent =
    `${available} 个可访问 · 来自 Superset`;
  document.querySelector('#superset-summary-home-chart-total').textContent = home ? String(home.chart_count) : '—';
  document.querySelector('#superset-summary-home-chart-note').textContent = home
    ? `${home.title} · Dashboard ${home.superset_id}` : '尚未设置经营总览';
  document.querySelector('#superset-summary-ready-total').textContent = ready === undefined ? '—' : String(ready);
  document.querySelector('#superset-summary-ready-note').textContent = charts
    ? `共 ${charts.length} 张图表已执行真实查询` : '等待验证经营总览查询';
  const connection = document.querySelector('#superset-summary-connection');
  const connected = loadedSupersetDashboards.length > 0 && available > 0;
  connection.textContent = connected ? '正常' : '不可用';
  connection.classList.toggle('healthy-number', connected);
  document.querySelector('#superset-summary-connection-note').textContent = connected
    ? `${available}/${loadedSupersetDashboards.length} 个仪表盘可访问` : '请检查 Superset 服务与账号配置';
}

async function loadSupersetHomeSummary() {
  const home = loadedSupersetDashboards.find(dashboard => dashboard.is_home && dashboard.available);
  if (!home) { supersetHomeSnapshot = undefined; renderSupersetAssetSummary(); return; }
  try {
    const response = await request(`/api/v1/admin/superset/dashboards/${home.superset_id}/native`);
    supersetHomeSnapshot = response.dashboard;
  } catch {
    supersetHomeSnapshot = undefined;
  }
  renderSupersetAssetSummary();
}

function renderSupersetDashboardAssets() {
  document.querySelector('#superset-dashboard-total').textContent = `${loadedSupersetDashboards.length} 个仪表盘`;
  renderSupersetAssetSummary();
  const body = document.querySelector('#superset-dashboard-table-body');
  if (!loadedSupersetDashboards.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td'); cell.colSpan = 6;
    cell.textContent = '尚未同步，请点击“同步 Superset”获取真实仪表盘。';
    row.append(cell); body.replaceChildren(row); return;
  }
  body.replaceChildren(...loadedSupersetDashboards.map(dashboard => {
    const row = document.createElement('tr');
    const values = [dashboard.title, dashboard.superset_id, dashboard.chart_count,
      dashboard.published && dashboard.available ? '● 已发布' : '● 不可用',
      dashboard.is_home ? '✓ 当前总览' : '—'];
    values.forEach(value => { const cell = document.createElement('td'); cell.textContent = String(value); row.append(cell); });
    const action = document.createElement('td'); action.className = 'registry-actions';
    action.append(actionButton('管理图表', '', () => openDashboardChartManager(dashboard)));
    action.append(actionButton('修改', '', () => openSupersetDashboardEditor('edit', dashboard)));
    action.append(actionButton('添加图表', '', () => openSupersetChartEditor(undefined, dashboard)));
    action.append(actionButton(dashboard.published ? '下线' : '发布', 'warning-action', () =>
      updateSupersetDashboardState(dashboard, !dashboard.published)));
    action.append(actionButton('复制', '', () => openSupersetDashboardEditor('copy', dashboard)));
    action.append(actionButton('删除', 'danger-action', () => deleteSupersetDashboard(dashboard)));
    const home = actionButton('设为经营总览', '', async () => {
      try {
        await request(`/api/v1/admin/superset/dashboards/${dashboard.superset_id}/home`, {
          method: 'POST', headers: { 'X-AgentBI-CSRF': currentUser.csrf_token },
        });
        supersetWorkspace = undefined;
        supersetWorkspaceDirty = true;
        window.AgentBI.supersetWorkspace.clear();
        await loadSupersetDashboardAssets();
        switchView('dashboard');
        await loadSupersetWorkspace({ force: true });
        showManagementFeedback(`${dashboard.title} 已设为经营总览，分析工作台已更新`);
      } catch (error) { showManagementFeedback(error.message, true); }
    });
    home.disabled = dashboard.is_home || !dashboard.published || !dashboard.available;
    action.append(home); row.append(action); return row;
  }));
}

async function openDashboardChartManager(dashboard) {
  managingSupersetDashboard = dashboard;
  const section = document.querySelector('#dashboard-chart-manager');
  const body = document.querySelector('#dashboard-chart-manager-body');
  document.querySelector('#dashboard-chart-manager-title').textContent = `${dashboard.title} · 图表管理`;
  body.innerHTML = '<div class="dashboard-chart-loading">正在读取真实图表资产…</div>';
  section.hidden = false; section.scrollIntoView({behavior: 'smooth', block: 'start'});
  try {
    const response = await request(`/api/v1/admin/superset/dashboards/${dashboard.superset_id}/native`);
    if (dashboard.is_home) {
      supersetHomeSnapshot = response.dashboard;
      renderSupersetAssetSummary();
    }
    const charts = response.dashboard?.charts || [];
    if (!charts.length) {
      body.innerHTML = '<div class="dashboard-chart-loading">当前仪表盘还没有图表，可点击上方“添加图表”。</div>';
      return;
    }
    body.replaceChildren(...charts.map(chart => {
      const card = document.createElement('article');
      const icon = document.createElement('span'); icon.className = 'dashboard-chart-icon'; icon.textContent = nativeChartIcon(chart.visualization_type);
      const info = document.createElement('div'); const title = document.createElement('strong'); title.textContent = chart.title;
      const meta = document.createElement('div'); meta.className = 'dashboard-chart-meta';
      const id = document.createElement('small'); id.textContent = `Chart ${chart.superset_id}`;
      const status = document.createElement('span'); status.className = chart.status === 'ready' ? 'chart-state-ready' : 'chart-state-warning'; status.textContent = chart.status === 'ready' ? '● 真实查询可用' : '● 需要重新配置';
      meta.append(id, status); info.append(title, meta); const actions = document.createElement('div'); actions.className = 'dashboard-chart-actions';
      const edit = actionButton('修改图表', '', () => openSupersetChartEditor(undefined, dashboard, chart));
      const remove = actionButton('删除图表', 'danger-action', async () => {
        if (!window.confirm(`确认永久删除图表“${chart.title}”吗？\n该操作会同步删除数据引擎中的图表资产。`)) return;
        remove.disabled = true;
        try {
          await request(`/api/v1/admin/superset/dashboards/${dashboard.superset_id}/charts/${chart.superset_id}`, {
            method: 'DELETE', headers: {'X-AgentBI-CSRF': currentUser.csrf_token},
          });
          await refreshSupersetDashboardsAfterWrite(`${chart.title} 已从 ${dashboard.title} 删除`);
          await openDashboardChartManager(dashboard);
        } catch (error) { showManagementFeedback(error.message, true); remove.disabled = false; }
      });
      actions.append(edit, remove); card.append(icon, info, actions); return card;
    }));
  } catch (error) {
    body.innerHTML = ''; const message = document.createElement('div'); message.className = 'dashboard-chart-loading'; message.textContent = error.message; body.append(message);
  }
}

function openSupersetPath(path) {
  const base = supersetWorkspace?.view_url || supersetWorkspace?.edit_url;
  if (!base) {
    showManagementFeedback('尚未连接 Superset，请返回分析工作台重新加载后再试', true);
    return;
  }
  const target = new URL(path, base);
  target.searchParams.set('lang', 'zh');
  if (target.origin !== new URL(base).origin) {
    showManagementFeedback('Superset 地址校验失败', true);
    return;
  }
  window.open(target.href, '_blank', 'noopener,noreferrer');
}

function closeSupersetDashboardEditor() {
  document.querySelector('#superset-dashboard-editor').hidden = true;
  document.querySelector('#superset-dashboard-editor-error').hidden = true;
}

function openSupersetDashboardEditor(mode, dashboard) {
  supersetDashboardEditorMode = mode;
  editingSupersetDashboard = dashboard;
  const copy = mode === 'copy';
  document.querySelector('#superset-dashboard-editor-title').textContent =
    mode === 'create' ? '新建仪表盘' : copy ? '复制仪表盘' : '修改仪表盘';
  document.querySelector('#superset-dashboard-title').value = dashboard
    ? `${dashboard.title}${copy ? ' - 副本' : ''}` : '';
  document.querySelector('#superset-dashboard-published').checked = dashboard?.published || false;
  document.querySelector('#superset-dashboard-copy-charts-row').hidden = !copy;
  document.querySelector('#superset-dashboard-copy-charts').checked = false;
  document.querySelector('#superset-dashboard-editor-error').hidden = true;
  document.querySelector('#superset-dashboard-editor').hidden = false;
}

async function refreshSupersetDashboardsAfterWrite(message) {
  const body = await request('/api/v1/admin/superset/dashboards/sync', {
    method: 'POST', headers: { 'X-AgentBI-CSRF': currentUser.csrf_token },
  });
  loadedSupersetDashboards = body.dashboards || [];
  supersetHomeSnapshot = undefined;
  renderSupersetDashboardAssets();
  await loadSupersetHomeSummary();
  supersetWorkspace = undefined;
  supersetWorkspaceDirty = true;
  window.AgentBI.supersetWorkspace.clear();
  if (activeView === 'dashboard') await loadSupersetWorkspace({force: true});
  showManagementFeedback(message);
}

async function updateSupersetDashboardState(dashboard, published) {
  try {
    await request(`/api/v1/admin/superset/dashboards/${dashboard.superset_id}`, {
      method: 'PUT', headers: {
        'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token,
      },
      body: JSON.stringify({title: dashboard.title, published}),
    });
    await refreshSupersetDashboardsAfterWrite(`${dashboard.title} 已${published ? '发布' : '下线'}`);
  } catch (error) { showManagementFeedback(error.message, true); }
}

function deleteSupersetDashboard(dashboard) {
  pendingSupersetDashboardDelete = dashboard;
  document.querySelector('#delete-superset-dashboard-name').textContent = dashboard.title;
  document.querySelector('#delete-superset-dashboard-error').hidden = true;
  document.querySelector('#delete-superset-dashboard-confirm').hidden = false;
}

function closeSupersetDashboardDeleteConfirmation() {
  document.querySelector('#delete-superset-dashboard-confirm').hidden = true;
  document.querySelector('#delete-superset-dashboard-error').hidden = true;
  pendingSupersetDashboardDelete = undefined;
}

async function confirmSupersetDashboardDelete() {
  const dashboard = pendingSupersetDashboardDelete;
  if (!dashboard) return;
  const button = document.querySelector('#confirm-delete-superset-dashboard');
  const errorBox = document.querySelector('#delete-superset-dashboard-error');
  button.disabled = true; errorBox.hidden = true;
  try {
    await request(`/api/v1/admin/superset/dashboards/${dashboard.superset_id}`, {
      method: 'DELETE', headers: { 'X-AgentBI-CSRF': currentUser.csrf_token },
    });
    closeSupersetDashboardDeleteConfirmation();
    await refreshSupersetDashboardsAfterWrite(`${dashboard.title} 已删除`);
  } catch (error) {
    errorBox.textContent = error.message; errorBox.hidden = false;
  } finally { button.disabled = false; }
}

function closeSupersetChartEditor() {
  document.querySelector('#superset-chart-editor').hidden = true;
  document.querySelector('#superset-chart-editor-error').hidden = true;
  editingSupersetChart = undefined;
}

function updateSupersetChartPreview() {
  const value = id => document.querySelector(id)?.value || '—';
  const text = id => document.querySelector(id)?.selectedOptions?.[0]?.textContent || '—';
  const typeLabels = {table: '表格', bar: '柱状图', line: '折线图', area: '面积图', pie: '饼图', donut: '环图', scatter: '散点图', funnel: '漏斗图', big_number: '指标卡'};
  const rawTitle = document.querySelector('#superset-chart-title')?.value.trim();
  document.querySelector('#superset-chart-preview-title').textContent =
    rawTitle || '尚未填写图表名称';
  document.querySelector('#superset-chart-editor .preview-chart-icon').textContent =
    nativeChartIcon(value('#superset-chart-type'));
  const rows = [
    ['图表类型', typeLabels[value('#superset-chart-type')] || '—'],
    ['分析维度', text('#superset-chart-dimension')],
    ['业务指标', `${value('#superset-chart-aggregation')}(${value('#superset-chart-metric-column')})`],
    ['时间字段', text('#superset-chart-time-column')],
    ['目标仪表盘', text('#superset-chart-dashboard')],
  ];
  document.querySelector('#superset-chart-config-summary').replaceChildren(...rows.map(([label, content]) => {
    const row = document.createElement('div');
    const term = document.createElement('dt'); term.textContent = label;
    const detail = document.createElement('dd'); detail.textContent = content;
    row.append(term, detail); return row;
  }));
}

function renderSupersetChartPresets(dataset, columns) {
  const names = new Set(columns.map(column => column.name));
  const firstDimension = columns.find(column =>
    /string|text|char|object/i.test(column.type))?.name || columns[0]?.name;
  const firstMetric = columns.find(column =>
    /int|numeric|decimal|float|double|real|number/i.test(column.type))?.name || columns[0]?.name;
  const presets = dataset.name === 'video_game_sales' &&
    ['genre', 'platform', 'publisher', 'global_sales'].every(name => names.has(name))
    ? [
        {title: '游戏类型销量', note: '按类型汇总全球销量', type: 'bar', dimension: 'genre', metric: 'global_sales', aggregation: 'SUM'},
        {title: '平台销量对比', note: '比较不同游戏平台表现', type: 'bar', dimension: 'platform', metric: 'global_sales', aggregation: 'SUM'},
        {title: '发行商销售排名', note: '查看发行商销量明细', type: 'table', dimension: 'publisher', metric: 'global_sales', aggregation: 'SUM'},
        {title: '游戏类型占比', note: '查看各类型销量结构', type: 'pie', dimension: 'genre', metric: 'global_sales', aggregation: 'SUM'},
      ]
    : [
        {title: '分类指标对比', note: '按维度汇总核心指标', type: 'bar', dimension: firstDimension, metric: firstMetric, aggregation: 'SUM'},
        {title: '分类指标明细', note: '以表格查看汇总结果', type: 'table', dimension: firstDimension, metric: firstMetric, aggregation: 'SUM'},
      ];
  const container = document.querySelector('#superset-chart-presets');
  container.replaceChildren(...presets.map(preset => {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'chart-preset';
    const title = document.createElement('strong'); title.textContent = preset.title;
    const note = document.createElement('small'); note.textContent = preset.note;
    button.append(title, note); button.addEventListener('click', () => {
      document.querySelector('#superset-chart-title').value = preset.title;
      document.querySelector('#superset-chart-type').value = preset.type;
      document.querySelector('#superset-chart-dimension').value = preset.dimension;
      document.querySelector('#superset-chart-metric-column').value = preset.metric;
      document.querySelector('#superset-chart-aggregation').value = preset.aggregation;
      document.querySelector('#superset-chart-time-column').value = '';
      updateSupersetChartPreview();
    }); return button;
  }));
  container.firstElementChild?.click();
}

async function loadSupersetChartFields() {
  const datasetId = Number(document.querySelector('#superset-chart-dataset').value);
  const dimensionSelect = document.querySelector('#superset-chart-dimension');
  const metricSelect = document.querySelector('#superset-chart-metric-column');
  const timeSelect = document.querySelector('#superset-chart-time-column');
  dimensionSelect.disabled = true; metricSelect.disabled = true; timeSelect.disabled = true;
  try {
    const body = await request(`/api/v1/admin/superset/datasets/${datasetId}`);
    const dataset = body.dataset || {};
    const columns = body.dataset?.columns || [];
    if (!columns.length) throw new Error('当前 Dataset 没有可配置字段');
    fillSelect(dimensionSelect, columns.map(column => ({...column, id: column.name})),
      column => `${column.name} · ${column.type}`);
    const numeric = columns.filter(column =>
      /int|numeric|decimal|float|double|real|number/i.test(column.type));
    fillSelect(metricSelect, (numeric.length ? numeric : columns)
      .map(column => ({...column, id: column.name})), column => `${column.name} · ${column.type}`);
    const timeColumns = columns.filter(column => column.is_time);
    timeSelect.replaceChildren(new Option('不使用时间字段', ''),
      ...timeColumns.map(column => new Option(`${column.name} · ${column.type}`, column.name)));
    document.querySelector('#superset-chart-dataset-summary').textContent =
      `${dataset.name || 'Dataset'} · ${columns.length} 个字段 · ${numeric.length} 个数值字段 · ${timeColumns.length} 个时间字段`;
    renderSupersetChartPresets(dataset, columns);
    updateSupersetChartPreview();
  } finally {
    dimensionSelect.disabled = false; metricSelect.disabled = false; timeSelect.disabled = false;
  }
}

async function openSupersetChartEditor(dataset, dashboard, chart) {
  try {
    editingSupersetChart = chart ? {chart, dashboard} : undefined;
    if (!loadedDataSources.length) {
      const assets = window.AgentBI.parseSupersetDataAssets(
        await request('/api/v1/admin/superset/data-assets'));
      loadedDataSources = assets.datasets;
    }
    if (!loadedSupersetDashboards.length) await loadSupersetDashboardAssets();
    fillSelect(document.querySelector('#superset-chart-dataset'),
      loadedDataSources.map(item => ({...item, id: item.superset_id})),
      item => `${item.name} · ${item.database_name}`);
    fillSelect(document.querySelector('#superset-chart-dashboard'),
      loadedSupersetDashboards.filter(item => item.available)
        .map(item => ({...item, id: item.superset_id})),
      item => item.title);
    document.querySelector('#superset-chart-editor-form').reset();
    const configuration = chart?.configuration || {};
    const selectedDatasetId = configuration.dataset_id || dataset?.superset_id;
    if (selectedDatasetId) document.querySelector('#superset-chart-dataset').value = String(selectedDatasetId);
    if (dashboard) document.querySelector('#superset-chart-dashboard').value = String(dashboard.superset_id);
    await loadSupersetChartFields();
    if (chart) {
      const type = String(chart.visualization_type || 'table');
      const chartType = type.includes('bar') ? 'bar' : type.includes('area') ? 'area'
        : type.includes('line') ? 'line' : type.includes('scatter') ? 'scatter'
          : type.includes('funnel') ? 'funnel' : type.includes('big_number') ? 'big_number'
            : type === 'donut' ? 'donut' : type === 'pie' ? 'pie' : 'table';
      document.querySelector('#superset-chart-editor-title').textContent = `修改图表 · ${chart.title}`;
      document.querySelector('#superset-chart-title').value = chart.title;
      document.querySelector('#superset-chart-type').value = chartType;
      if (configuration.dimension) document.querySelector('#superset-chart-dimension').value = configuration.dimension;
      if (configuration.metric_column) document.querySelector('#superset-chart-metric-column').value = configuration.metric_column;
      if (configuration.aggregation) document.querySelector('#superset-chart-aggregation').value = configuration.aggregation;
      document.querySelector('#superset-chart-time-column').value = configuration.time_column || '';
      document.querySelector('#superset-chart-dashboard').disabled = true;
      updateSupersetChartPreview();
    } else {
      document.querySelector('#superset-chart-editor-title').textContent = '创建业务分析图表';
      document.querySelector('#superset-chart-dashboard').disabled = false;
    }
    document.querySelector('#superset-chart-editor-error').hidden = true;
    document.querySelector('#superset-chart-editor').hidden = false;
  } catch (error) { showManagementFeedback(error.message, true); }
}

async function loadSupersetDashboardAssets() {
  const body = await request('/api/v1/admin/superset/dashboards');
  loadedSupersetDashboards = body.dashboards || [];
  renderSupersetDashboardAssets();
  await loadSupersetHomeSummary();
}

function actionButton(label, className, handler) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  if (className) button.className = className;
  if (handler) button.addEventListener('click', handler);
  return button;
}

function appendGovernanceActions(container, chart, valid) {
  container.className = 'registry-actions';
  const view = actionButton('查看下钻', '', () => selectDrillChart(chart.chart_key, true));
  view.disabled = !valid || chart.is_published === false;
  container.append(view);
  if (chart.builtin) {
    const builtin = document.createElement('span');
    builtin.className = 'builtin-label'; builtin.textContent = '内置配置';
    container.append(builtin);
    return;
  }
  container.append(actionButton('修改', '', () => openChartWizard(chart)));
  container.append(actionButton(chart.is_published ? '下线' : '上线', 'warning-action', () => {
    setChartPublication(chart, !chart.is_published);
  }));
  const remove = actionButton('删除', 'danger-action', () => openDeleteConfirmation(chart));
  remove.disabled = chart.is_published;
  if (chart.is_published) remove.title = '请先下线图表';
  container.append(remove);
}

async function setChartPublication(chart, published) {
  try {
    await request(`/api/v1/admin/charts/${encodeURIComponent(chart.chart_key)}/publication`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token },
      body: JSON.stringify({ published }),
    });
    await loadManagedCharts();
    renderRegistryCenter();
    renderChartManagement();
    showManagementFeedback(`${chart.title}已${published ? '上线' : '下线'}`);
  } catch (error) {
    showManagementFeedback(error.message, true);
  }
}

function showManagementFeedback(message, isError = false) {
  const feedback = document.querySelector('#management-feedback');
  feedback.textContent = message;
  feedback.classList.toggle('error', isError);
  feedback.hidden = false;
  window.clearTimeout(showManagementFeedback.timer);
  showManagementFeedback.timer = window.setTimeout(() => { feedback.hidden = true; }, 3000);
}

function renderRegistryCenter() {
  const entries = managedCharts.map(chart => [chart.chart_key, chart]);
  const validEntries = [];
  const models = new Set(managedCharts.map(chart => chart.semantic_model).filter(Boolean));
  document.querySelector('#registry-count').textContent = `${entries.length} 个已注册图表`;
  document.querySelector('#registry-chart-total').textContent = String(entries.length);
  document.querySelector('#registry-model-total').textContent = String(models.size);
  document.querySelector('#registry-ready-total').textContent = String(validEntries.length);
  document.querySelector('#registry-invalid-total').textContent = String(entries.length - validEntries.length);
  const typeLabels = { revenue: '趋势图', structure: '结构图', table: '指标表格' };
  const body = document.querySelector('#registry-table-body');
  body.replaceChildren(...entries.map(([chartId, config]) => {
    const row = document.createElement('tr');
    const chart = document.createElement('td');
    const title = document.createElement('strong'); title.textContent = config.title || chartId;
    const metric = document.createElement('small'); metric.textContent = `Chart ID：${chartId} · 指标：${config.metric || '未配置'}`;
    chart.append(title, metric);
    const model = document.createElement('td'); model.textContent = config.semantic_model || '未配置';
    const type = document.createElement('td'); type.textContent = config.visualization_type || '未知类型';
    const path = document.createElement('td'); path.textContent = config.dimensions?.join(' → ') || '未配置';
    const status = document.createElement('td');
    const badge = document.createElement('span');
    const managed = managedCharts.find(item => item.chart_key === chartId);
    const valid = false;
    const offline = managed?.is_published === false;
    badge.className = offline ? 'registry-status offline' : valid ? 'registry-status ready' : 'registry-status invalid';
    badge.textContent = offline ? '● 已下线' : '● 待绑定真实查询';
    status.append(badge);
    const action = document.createElement('td');
    appendGovernanceActions(action, managed || { chart_key: chartId, builtin: true, is_published: true }, valid);
    row.append(chart, model, type, path, status, action);
    return row;
  }));
}

function setDrillAgentLayer(config, level, title, evidence) {
  document.querySelector('#drill-agent-context').textContent = `第 ${level} 层 · ${title}`;
  document.querySelector('#drill-evidence').textContent = evidence;
  document.querySelector('#drill-insight-source').textContent =
    `${config.insightSource} · 当前分析第 ${level} 层`;
}

function drillEvidenceForLevel(config, level) {
  const path = String(config.evidence || '').split('→').map(item => item.trim()).filter(Boolean);
  const dimensions = Array.isArray(config.dimensions) ? config.dimensions : [];
  const baseLength = Math.max(1, path.length - dimensions.length);
  return path.slice(0, Math.min(path.length, baseLength + level)).join(' → ');
}

function renderDynamicDrillLevels(config) {
  const container = document.querySelector('#drill-dynamic-levels');
  const dimensions = Array.isArray(config.dimensions) ? config.dimensions : [];
  const extraDimensions = dimensions.slice(2);
  let selectedValue = config.rows[0]?.[0] || '第一项';
  let deepestTitle = config.levelTwoTitle;
  const nodes = [];
  extraDimensions.forEach((dimension, offset) => {
    const level = offset + 3;
    const connector = document.createElement('div');
    connector.className = 'drill-connector';
    const arrow = document.createElement('i'); arrow.textContent = '↓';
    const connectorText = document.createElement('span');
    connectorText.textContent = `点击 ${selectedValue}，下钻维度：${dimension}`;
    connector.append(arrow, connectorText);

    const article = document.createElement('article');
    article.className = 'drill-level dynamic-drill-level';
    const header = document.createElement('header');
    const heading = document.createElement('div');
    const label = document.createElement('span'); label.textContent = `第 ${level} 层 · ${dimension}`;
    const title = document.createElement('strong');
    title.textContent = `${selectedValue}${dimension}贡献`;
    deepestTitle = title.textContent;
    heading.append(label, title);
    const analyze = document.createElement('button');
    analyze.type = 'button'; analyze.textContent = '✦ 让 Agent 分析此层';
    const evidence = drillEvidenceForLevel(config, level);
    analyze.addEventListener('click', () => setDrillAgentLayer(config, level, title.textContent, evidence));
    header.append(heading, analyze);

    const content = document.createElement('div'); content.className = 'dynamic-level-content';
    const bars = document.createElement('div'); bars.className = 'dynamic-level-bars';
    const candidates = dimension.includes('渠道')
      ? ['直营网', '电商平台', '经销商', '其他']
      : dimension.includes('客户')
        ? ['重点客户', '成长客户', '一般客户', '待激活客户']
        : [`${dimension} A`, `${dimension} B`, `${dimension} C`, '其他'];
    candidates.forEach((name, index) => {
      const row = document.createElement('div');
      const itemName = document.createElement('span'); itemName.textContent = name;
      const bar = document.createElement('i'); bar.style.setProperty('--w', `${88 - index * 17}%`);
      const value = document.createElement('b'); value.textContent = String(320 - index * 58);
      row.append(itemName, bar, value); bars.append(row);
    });
    const meta = document.createElement('div'); meta.className = 'dynamic-level-meta';
    const summary = document.createElement('strong'); summary.textContent = `${candidates[0]}贡献最高`;
    const description = document.createElement('span');
    description.textContent = `当前路径下 ${candidates[0]} 占比 42%，可继续沿下一维度下钻或交给 Agent 分析。`;
    meta.append(summary, description); content.append(bars, meta);
    article.append(header, content); nodes.push(connector, article);
    selectedValue = candidates[0];
  });
  container.replaceChildren(...nodes);
  return extraDimensions.length
    ? { level: dimensions.length, title: deepestTitle, evidence: drillEvidenceForLevel(config, dimensions.length) }
    : { level: 2, title: config.levelTwoTitle, evidence: drillEvidenceForLevel(config, 2) };
}

function renderDrilldown(chartId) {
  const config = drillConfigurations[chartId];
  const empty = document.querySelector('#drilldown-empty');
  const detail = document.querySelector('#drilldown-view .drill-scroll');
  const header = document.querySelector('#drilldown-view .drill-header');
  if (workbenchAnalysis) {
    renderRealDrilldownResult(empty, detail, header);
    return;
  }
  if (!isValidDrillConfiguration(config)) {
    empty.hidden = false;
    detail.hidden = true;
    header.hidden = true;
    return;
  }
  empty.hidden = true;
  detail.hidden = false;
  header.hidden = false;
  document.querySelector('#drill-page-title').textContent = config.pageTitle;
  document.querySelector('#drill-breadcrumb').textContent = config.breadcrumb;
  document.querySelector('#drill-source-title').textContent = config.sourceTitle;
  document.querySelector('#drill-source-revenue').hidden = config.sourceType !== 'revenue';
  document.querySelector('#drill-source-structure').hidden = config.sourceType !== 'structure';
  renderDrillSourceTable(config);
  document.querySelector('#drill-connector-one').textContent = config.connectorOne;
  document.querySelector('#drill-level-one-label').textContent = config.levelOneLabel;
  document.querySelector('#drill-level-one-title').textContent = config.levelOneTitle;
  const bars = document.querySelector('#drill-level-one-bars');
  bars.replaceChildren(...config.bars.map(([label, width, value], index) => {
    const row = document.createElement('div');
    if (index === 0) row.className = 'selected';
    const name = document.createElement('span'); name.textContent = label;
    const bar = document.createElement('i'); bar.style.setProperty('--w', `${width}%`);
    const amount = document.createElement('b'); amount.textContent = value;
    row.append(name, bar, amount);
    return row;
  }));
  document.querySelector('#drill-connector-two').textContent = config.connectorTwo;
  document.querySelector('#drill-level-two-label').textContent = config.levelTwoLabel;
  document.querySelector('#drill-level-two-title').textContent = config.levelTwoTitle;
  document.querySelector('#drill-table-dimension').textContent = config.tableDimension;
  document.querySelector('#drill-total').textContent = config.total;
  const table = document.querySelector('#drill-table-body');
  table.replaceChildren(...config.rows.map(([label, value, share]) => {
    const row = document.createElement('tr');
    [label, value, share].forEach((text, index) => {
      const cell = document.createElement('td');
      cell.textContent = index === 0 ? `● ${text}` : text;
      row.append(cell);
    });
    return row;
  }));
  const deepest = renderDynamicDrillLevels(config);
  const layerButtons = document.querySelectorAll('#drilldown-view > .drill-header + .drill-scroll > .drill-level > header > button');
  const layerTitles = [config.sourceTitle, config.levelOneTitle, config.levelTwoTitle];
  layerButtons.forEach((button, level) => {
    button.onclick = () => setDrillAgentLayer(
      config, level, layerTitles[level], drillEvidenceForLevel(config, level),
    );
  });
  document.querySelector('#drill-agent-context').textContent = Array.isArray(config.dimensions) && config.dimensions.length > 2
    ? `第 ${deepest.level} 层 · ${deepest.title}` : config.context;
  document.querySelector('#drill-question-one').firstChild.textContent = `${config.questions[0]} `;
  document.querySelector('#drill-question-two').firstChild.textContent = `${config.questions[1]} `;
  document.querySelector('#drill-insight').textContent = config.insight;
  document.querySelector('#drill-insight-source').textContent = config.insightSource;
  document.querySelector('#drill-evidence').textContent = deepest.evidence;
  document.querySelectorAll('[data-drill-chart]').forEach(card => {
    if (!card.classList.contains('chart-card')) return;
    card.classList.toggle('drill-source-selected', card.dataset.drillChart === chartId);
    const badge = card.querySelector('.drill-ready-badge');
    if (badge) badge.hidden = card.dataset.drillChart !== chartId;
  });
}

function resultTable(rows) {
  const wrapper = document.createElement('div');
  wrapper.className = 'real-query-table';
  if (!rows.length) {
    const message = document.createElement('p');
    message.textContent = '真实查询已执行，但没有返回数据行。';
    wrapper.append(message);
    return wrapper;
  }
  const columns = Object.keys(rows[0]).slice(0, 6);
  const table = document.createElement('table');
  const head = document.createElement('thead');
  const headRow = document.createElement('tr');
  columns.forEach(column => {
    const cell = document.createElement('th'); cell.textContent = column; headRow.append(cell);
  });
  head.append(headRow);
  const body = document.createElement('tbody');
  rows.slice(0, 20).forEach(row => {
    const tableRow = document.createElement('tr');
    columns.forEach(column => {
      const cell = document.createElement('td');
      const value = row[column];
      cell.textContent = value === null || value === undefined ? '—' : String(value);
      tableRow.append(cell);
    });
    body.append(tableRow);
  });
  table.append(head, body); wrapper.append(table);
  return wrapper;
}

function renderRealDrilldownResult(empty, detail, header) {
  header.hidden = true;
  detail.hidden = true;
  empty.hidden = false;
  const heading = document.createElement('strong');
  heading.textContent = `第 ${workbenchDrillDepth} 层 · 真实语义查询结果`;
  const answer = document.createElement('p'); answer.textContent = workbenchAnalysis.answer;
  const evidence = document.createElement('small');
  evidence.textContent = `查询编号 ${workbenchAnalysis.evidence.query_id} · ${workbenchAnalysis.evidence.row_count} 行 · SQL 指纹 ${workbenchAnalysis.evidence.sql_fingerprint || '—'}`;
  empty.replaceChildren(heading, answer, resultTable(workbenchAnalysis.data), evidence);
}

function workbenchContext() {
  const semanticModel = Number(document.querySelector('#agent-semantic-model').value);
  if (!Number.isInteger(semanticModel) || semanticModel < 1) throw new Error('请输入有效的 SuperSonic 语义模型 ID');
  const timeRange = document.querySelector('#agent-time-range').value.trim();
  if (!timeRange) throw new Error('请输入时间范围');
  return {
    dashboard_id: selectedSupersetContext?.dashboard_id || String(supersetWorkspace?.dashboard_id || 'workbench-home'),
    ...(selectedSupersetContext?.chart_id ? {chart_id: selectedSupersetContext.chart_id} : {}),
    ...(selectedSupersetContext?.dataset_id ? {dataset_id: selectedSupersetContext.dataset_id} : {}),
    semantic_model_id: semanticModel,
    time_range: timeRange,
    filters: [],
    ...(workbenchSelected ? { selected: workbenchSelected } : {}),
  };
}

async function analyzeFromWorkbench() {
  const question = document.querySelector('#agent-question').value.trim();
  const button = document.querySelector('#agent-analyze');
  const status = document.querySelector('#agent-query-status');
  if (question.length < 2) { status.textContent = '请输入至少 2 个字符的问题'; return; }
  button.disabled = true; status.textContent = '正在执行 SuperSonic 真实语义查询…';
  try {
    const body = await request('/api/v1/workbench/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token },
      body: JSON.stringify({
        question,
        context: workbenchContext(),
        client_request_id: crypto.randomUUID(),
        ...(workbenchChatId ? { chat_id: workbenchChatId } : {}),
      }),
    });
    workbenchAnalysis = body;
    workbenchChatId = body.chat_id || workbenchChatId;
    if (workbenchPendingDrill) {
      workbenchDrillDepth += 1;
      workbenchPendingDrill = false;
    }
    document.querySelector('#agent-answer').textContent = body.answer;
    document.querySelector('#agent-result-table').replaceChildren(resultTable(body.data));
    document.querySelector('#agent-evidence-summary').textContent = `查询编号 ${body.evidence.query_id} · ${body.evidence.row_count} 行 · SQL 指纹 ${body.evidence.sql_fingerprint || '—'}`;
    document.querySelector('#agent-query-result').hidden = false;
    document.querySelector('#start-result-drilldown').hidden = !body.data.length;
    status.textContent = body.warnings?.length ? body.warnings.join('；') : '真实查询完成';
    if (activeView === 'drilldown') {
      document.querySelector('#drill-agent-context').textContent = workbenchSelected
        ? `${workbenchSelected.dimension}=${workbenchSelected.value}` : '原始查询结果';
      document.querySelector('#drill-insight').textContent = body.answer;
      document.querySelector('#drill-insight-source').textContent = `查询编号 ${body.evidence.query_id}`;
      document.querySelector('#drill-evidence').textContent = `返回 ${body.evidence.row_count} 行 · SQL 指纹 ${body.evidence.sql_fingerprint || '—'}`;
      renderDrilldown();
    }
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function renderDrillSourceTable(config) {
  let container = document.querySelector('#drill-source-table');
  if (!container) {
    container = document.createElement('div');
    container.id = 'drill-source-table';
    container.className = 'drill-source-table';
    document.querySelector('#drill-source-structure').after(container);
  }
  container.hidden = config.sourceType !== 'table';
  if (container.hidden) return;
  const table = document.createElement('table');
  const head = document.createElement('thead');
  const headRow = document.createElement('tr');
  config.sourceColumns.forEach(label => {
    const cell = document.createElement('th'); cell.textContent = label; headRow.append(cell);
  });
  head.append(headRow);
  const body = document.createElement('tbody');
  config.sourceRows.forEach(values => {
    const row = document.createElement('tr');
    values.forEach(value => {
      const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
    });
    body.append(row);
  });
  table.append(head, body);
  container.replaceChildren(table);
}

function selectDrillChart(chartId, navigate = false) {
  selectedDrillChart = chartId;
  sessionStorage.setItem('agentbi.drillSource', chartId);
  renderDrilldown(chartId);
  closeChartMenus();
  if (navigate) switchView('drilldown');
}

function closeChartMenus() {
  document.querySelectorAll('.chart-menu').forEach(menu => { menu.hidden = true; });
  document.querySelectorAll('.chart-menu-trigger').forEach(button => button.setAttribute('aria-expanded', 'false'));
}

function initializeDrillableCharts() {
  document.querySelectorAll('.chart-card[data-drill-chart]').forEach(card => {
    const chartId = card.dataset.drillChart;
    const config = drillConfigurations[chartId];
    if (!isValidDrillConfiguration(config)) {
      card.dataset.drillStatus = 'unconfigured';
      card.title = '该图表尚未配置指标、语义模型或下钻维度';
      return;
    }
    card.dataset.drillStatus = 'ready';
    const titleGroup = card.querySelector('header>div:first-child');
    if (titleGroup && !card.querySelector('.drill-ready-badge')) {
      const badge = document.createElement('span');
      badge.className = 'drill-ready-badge';
      badge.textContent = '当前下钻起点';
      badge.hidden = chartId !== selectedDrillChart;
      titleGroup.append(badge);
    }
    if (card.querySelector('.chart-actions')) return;
    const header = card.querySelector('header');
    if (!header) return;
    const actions = document.createElement('div');
    actions.className = 'chart-actions';
    const trigger = document.createElement('button');
    trigger.className = 'chart-menu-trigger';
    trigger.type = 'button';
    trigger.dataset.drillChart = chartId;
    trigger.setAttribute('aria-label', `${config.sourceTitle}操作`);
    trigger.setAttribute('aria-expanded', 'false');
    trigger.textContent = '···';
    const menu = document.createElement('div');
    menu.className = 'chart-menu';
    menu.dataset.chartMenu = chartId;
    menu.setAttribute('role', 'menu');
    menu.hidden = true;
    [['select', '◎ 设为下钻起点'], ['enter', '↓ 进入下钻分析']].forEach(([action, label]) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('role', 'menuitem');
      button.dataset.drillAction = action;
      button.dataset.drillChart = chartId;
      button.textContent = label;
      menu.append(button);
    });
    actions.append(trigger, menu);
    header.append(actions);
  });
}

initializeDrillableCharts();

document.querySelectorAll('.role-tab').forEach(tab => tab.addEventListener('click', () => {
  document.querySelectorAll('.role-tab').forEach(item => item.classList.remove('active'));
  tab.classList.add('active');
  document.querySelector('#username').value = tab.dataset.account;
  document.querySelector('#password').focus();
}));

document.querySelectorAll('[data-view]').forEach(item => item.addEventListener('click', () => {
  if (item.dataset.view === 'drilldown') renderDrilldown(selectedDrillChart);
  switchView(item.dataset.view);
}));

document.querySelector('#back-local-canvas').addEventListener('click', () => selectDashboardCanvas('superset'));
document.querySelector('#retry-superset').addEventListener('click', () => loadSupersetWorkspace({ force: true }));
document.querySelector('#agent-panel-toggle').addEventListener('click', () => {
  setAgentPanelExpanded(!agentPanelExpanded);
});
document.querySelector('#agent-analyze').addEventListener('click', analyzeFromWorkbench);
document.querySelector('#agent-question').addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') analyzeFromWorkbench();
});
document.querySelector('#start-result-drilldown').addEventListener('click', () => {
  const row = workbenchAnalysis?.data?.[0];
  if (!row) return;
  const dimension = Object.keys(row).find(key => ['string', 'number'].includes(typeof row[key]));
  if (!dimension) return;
  workbenchSelected = { label: dimension, dimension, value: row[dimension] };
  workbenchPendingDrill = true;
  document.querySelector('#agent-question').value = `请围绕 ${dimension}=${row[dimension]} 下钻分析，并说明主要差异`;
  document.querySelector('#drill-agent-context').textContent = `待执行：${dimension}=${row[dimension]}`;
  document.querySelector('#drill-insight').textContent = workbenchAnalysis.answer;
  document.querySelector('#drill-insight-source').textContent = `查询编号 ${workbenchAnalysis.evidence.query_id}`;
  document.querySelector('#drill-evidence').textContent = `返回 ${workbenchAnalysis.evidence.row_count} 行 · SQL 指纹 ${workbenchAnalysis.evidence.sql_fingerprint || '—'}`;
  switchView('drilldown');
  renderDrilldown();
});
document.querySelector('#refresh-dashboard').addEventListener('click', async () => {
  if (dashboardCanvasMode === 'superset') {
    await loadSupersetWorkspace({ force: true });
    return;
  }
  await loadManagedCharts();
  showManagementFeedback('经营总览已刷新');
});
document.querySelector('#edit-dashboard').addEventListener('click', () => {
  if (dashboardCanvasMode === 'superset') {
    switchView('chart-management');
    return;
  }
  switchView('chart-management');
});
document.querySelector('#create-superset-dashboard').addEventListener('click', () =>
  openSupersetDashboardEditor('create'));
document.querySelector('#create-superset-chart').addEventListener('click', () =>
  openSupersetChartEditor());
document.querySelector('#close-dashboard-chart-manager').addEventListener('click', () => {
  managingSupersetDashboard = undefined;
  document.querySelector('#dashboard-chart-manager').hidden = true;
});

document.querySelector('#close-superset-dashboard-editor').addEventListener('click', closeSupersetDashboardEditor);
document.querySelector('#cancel-superset-dashboard-editor').addEventListener('click', closeSupersetDashboardEditor);
document.querySelector('#close-delete-superset-dashboard').addEventListener('click', closeSupersetDashboardDeleteConfirmation);
document.querySelector('#cancel-delete-superset-dashboard').addEventListener('click', closeSupersetDashboardDeleteConfirmation);
document.querySelector('#delete-superset-dashboard-confirm').addEventListener('click', event => {
  if (event.target.id === 'delete-superset-dashboard-confirm') closeSupersetDashboardDeleteConfirmation();
});
document.querySelector('#confirm-delete-superset-dashboard').addEventListener('click', confirmSupersetDashboardDelete);
document.querySelector('#superset-dashboard-editor-form').addEventListener('submit', async event => {
  event.preventDefault();
  const errorBox = document.querySelector('#superset-dashboard-editor-error'); errorBox.hidden = true;
  const title = document.querySelector('#superset-dashboard-title').value.trim();
  const published = document.querySelector('#superset-dashboard-published').checked;
  try {
    let endpoint = '/api/v1/admin/superset/dashboards';
    let method = 'POST';
    let payload = {title, published};
    if (supersetDashboardEditorMode === 'edit') {
      endpoint += `/${editingSupersetDashboard.superset_id}`; method = 'PUT';
    } else if (supersetDashboardEditorMode === 'copy') {
      endpoint += `/${editingSupersetDashboard.superset_id}/copy`;
      payload = {title, duplicate_charts: document.querySelector('#superset-dashboard-copy-charts').checked};
    }
    await request(endpoint, {method, headers: {
      'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token,
    },
      body: JSON.stringify(payload)});
    closeSupersetDashboardEditor();
    await refreshSupersetDashboardsAfterWrite(`${title} 已保存到 Superset`);
  } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
});

document.querySelector('#close-superset-chart-editor').addEventListener('click', closeSupersetChartEditor);
document.querySelector('#cancel-superset-chart-editor').addEventListener('click', closeSupersetChartEditor);
document.querySelector('#superset-chart-dataset').addEventListener('change', async () => {
  const errorBox = document.querySelector('#superset-chart-editor-error'); errorBox.hidden = true;
  try { await loadSupersetChartFields(); }
  catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
});
document.querySelector('#superset-chart-editor-form').addEventListener('input', updateSupersetChartPreview);
document.querySelector('#superset-chart-editor-form').addEventListener('change', updateSupersetChartPreview);
document.querySelector('#superset-chart-editor-form').addEventListener('submit', async event => {
  event.preventDefault();
  const errorBox = document.querySelector('#superset-chart-editor-error'); errorBox.hidden = true;
  try {
    const targetDashboardId = Number(document.querySelector('#superset-chart-dashboard').value);
    const targetDashboard = loadedSupersetDashboards.find(
      dashboard => dashboard.superset_id === targetDashboardId);
    const editing = editingSupersetChart;
    const endpoint = editing
      ? `/api/v1/admin/superset/dashboards/${editing.dashboard.superset_id}/charts/${editing.chart.superset_id}`
      : '/api/v1/admin/superset/charts';
    const body = await request(endpoint, {
      method: editing ? 'PUT' : 'POST', headers: {
        'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token,
      },
      body: JSON.stringify({
        title: document.querySelector('#superset-chart-title').value.trim(),
        dataset_id: Number(document.querySelector('#superset-chart-dataset').value),
        dashboard_id: targetDashboardId,
        visualization_type: document.querySelector('#superset-chart-type').value,
        dimension: document.querySelector('#superset-chart-dimension').value,
        metric_column: document.querySelector('#superset-chart-metric-column').value,
        aggregation: document.querySelector('#superset-chart-aggregation').value,
        time_column: document.querySelector('#superset-chart-time-column').value || null,
      }),
    });
    closeSupersetChartEditor();
    await refreshSupersetDashboardsAfterWrite(
      `${body.chart.title} 已${editing ? '更新' : '创建并加入'} ${targetDashboard?.title || '目标仪表盘'}`);
    const refreshedDashboard = loadedSupersetDashboards.find(
      dashboard => dashboard.superset_id === targetDashboardId)
      || editing?.dashboard
      || targetDashboard;
    switchView('chart-management');
    if (refreshedDashboard) await openDashboardChartManager(refreshedDashboard);
    showManagementFeedback(
      `${body.chart.title} 已${editing ? '更新' : '加入'} ${refreshedDashboard?.title || '目标仪表盘'}，图表列表已刷新`);
  } catch (error) {
    errorBox.textContent = error.message; errorBox.hidden = false;
  }
});

document.querySelector('#sync-superset-dashboards').addEventListener('click', async event => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = '正在同步…';
  try {
    const body = await request('/api/v1/admin/superset/dashboards/sync', {
      method: 'POST', headers: { 'X-AgentBI-CSRF': currentUser.csrf_token },
    });
    loadedSupersetDashboards = body.dashboards || [];
    supersetHomeSnapshot = undefined;
    renderSupersetDashboardAssets();
    await loadSupersetHomeSummary();
    supersetWorkspace = undefined;
    supersetWorkspaceDirty = true;
    window.AgentBI.supersetWorkspace.clear();
    showManagementFeedback(`已同步 ${body.count} 个 Superset 仪表盘`);
  } catch (error) {
    showManagementFeedback(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = '⟳ 同步 Superset';
  }
});

document.querySelectorAll('.module-refresh').forEach(button => {
  button.addEventListener('click', () => loadModuleView(button.dataset.module));
});

function closeDatabaseEditor() {
  document.querySelector('#database-editor').hidden = true;
  document.querySelector('#database-editor-error').hidden = true;
}

function databasePayload() {
  return {
    database_name: document.querySelector('#database-name').value.trim(),
    connection_mode: document.querySelector('#database-mode').value,
    engine: document.querySelector('#database-engine').value,
    host: document.querySelector('#database-host').value.trim(),
    port: Number(document.querySelector('#database-port').value) || null,
    database: document.querySelector('#database-catalog').value.trim(),
    username: document.querySelector('#database-username').value.trim(),
    password: document.querySelector('#database-password').value,
    sqlalchemy_uri: document.querySelector('#database-uri').value.trim(),
    expose_in_sqllab: document.querySelector('#database-sqllab').checked,
  };
}

function openDatabaseEditor(database) {
  editingSupersetDatabaseId = database?.superset_id;
  document.querySelector('#database-editor-form').reset();
  document.querySelector('#database-editor-title').textContent = database ? '修改数据库连接' : '新增数据库连接';
  document.querySelector('#database-name').value = database?.name || '';
  document.querySelector('#database-sqllab').checked = database?.expose_in_sqllab ?? true;
  document.querySelector('#database-mode').value = database ? 'uri' : 'form';
  document.querySelector('#database-engine').value = database?.backend === 'mysql' ? 'mysql' : 'postgresql';
  document.querySelector('#database-uri-help').textContent = database
    ? '留空则沿用 Superset 中的原连接密钥；填写后将测试并替换'
    : '仅提交给 Superset，不在 AgentBI 保存或回显';
  renderDatabaseConnectionMode();
  document.querySelector('#database-editor-error').hidden = true;
  document.querySelector('#database-editor').hidden = false;
  document.querySelector('#database-name').focus();
}

async function submitDatabaseAction(path, successMessage) {
  const errorBox = document.querySelector('#database-editor-error');
  errorBox.hidden = true;
  try {
    const body = await request(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token },
      body: JSON.stringify(databasePayload()),
    });
    showManagementFeedback(body.message || successMessage);
    return true;
  } catch (error) {
    errorBox.textContent = error.message; errorBox.hidden = false; return false;
  }
}

document.querySelector('#open-database-editor').addEventListener('click', () => {
  openDatabaseEditor();
});
document.querySelector('#close-database-editor').addEventListener('click', closeDatabaseEditor);
document.querySelector('#cancel-database-editor').addEventListener('click', closeDatabaseEditor);
document.querySelector('#test-database-connection').addEventListener('click', async () => {
  const form = document.querySelector('#database-editor-form');
  if (!form.reportValidity()) return;
  const payload = databasePayload();
  if (editingSupersetDatabaseId && payload.connection_mode === 'uri' && !payload.sqlalchemy_uri) {
    showManagementFeedback('连接串未变更，将沿用 Superset 中的原密钥'); return;
  }
  await submitDatabaseAction('/api/v1/admin/superset/databases/test', '连接测试成功');
});
document.querySelector('#database-editor-form').addEventListener('submit', async event => {
  event.preventDefault();
  const payload = databasePayload();
  const hasNewConnection = payload.connection_mode === 'form' || Boolean(payload.sqlalchemy_uri);
  if (hasNewConnection &&
      !await submitDatabaseAction('/api/v1/admin/superset/databases/test', '连接测试成功')) return;
  if (!editingSupersetDatabaseId) {
    if (!await submitDatabaseAction('/api/v1/admin/superset/databases', '数据库连接创建成功')) return;
  } else {
    const errorBox = document.querySelector('#database-editor-error');
    try {
      const body = await request(`/api/v1/admin/superset/databases/${editingSupersetDatabaseId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token },
        body: JSON.stringify(payload),
      });
      showManagementFeedback(body.message);
    } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; return; }
  }
  document.querySelector('#database-uri').value = '';
  closeDatabaseEditor();
  await loadModuleView('data-sources');
});

const databaseDefaultPorts = { postgresql: 5432, mysql: 3306, doris: 9030, trino: 8080, presto: 8080, druid: 8888 };
function renderDatabaseConnectionMode() {
  const advanced = document.querySelector('#database-mode').value === 'uri';
  document.querySelector('#database-form-fields').hidden = advanced;
  document.querySelector('#database-uri-field').hidden = !advanced;
  document.querySelector('#database-uri').required = advanced && !editingSupersetDatabaseId;
  ['database-host', 'database-port', 'database-catalog', 'database-username'].forEach(id => {
    document.querySelector(`#${id}`).required = !advanced;
  });
}
document.querySelector('#database-mode').addEventListener('change', renderDatabaseConnectionMode);
document.querySelector('#database-engine').addEventListener('change', event => {
  const engine = event.currentTarget.value;
  document.querySelector('#database-port').value = String(databaseDefaultPorts[engine]);
  document.querySelector('#database-port-help').textContent = `${event.currentTarget.selectedOptions[0].textContent} 默认 ${databaseDefaultPorts[engine]}`;
});

function closeDatasetEditor() {
  document.querySelector('#dataset-editor').hidden = true;
  document.querySelector('#dataset-editor-error').hidden = true;
}

document.querySelector('#open-dataset-editor').addEventListener('click', () => {
  const select = document.querySelector('#dataset-database');
  select.replaceChildren(...loadedSupersetDatabases.map(database => {
    const option = document.createElement('option');
    option.value = String(database.superset_id);
    option.textContent = `${database.name}（${database.backend}）`;
    return option;
  }));
  document.querySelector('#dataset-editor-form').reset();
  document.querySelector('#dataset-editor-error').hidden = true;
  document.querySelector('#dataset-editor').hidden = false;
});
document.querySelector('#close-dataset-editor').addEventListener('click', closeDatasetEditor);
document.querySelector('#cancel-dataset-editor').addEventListener('click', closeDatasetEditor);
document.querySelector('#dataset-editor-form').addEventListener('submit', async event => {
  event.preventDefault();
  const errorBox = document.querySelector('#dataset-editor-error'); errorBox.hidden = true;
  try {
    const body = await request('/api/v1/admin/superset/datasets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token },
      body: JSON.stringify({
        database_id: Number(document.querySelector('#dataset-database').value),
        schema_name: document.querySelector('#dataset-schema').value.trim(),
        table_name: document.querySelector('#dataset-table').value.trim(),
      }),
    });
    closeDatasetEditor(); showManagementFeedback(body.message); await loadModuleView('data-sources');
  } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
});

async function openDatasetDetail(dataset) {
  try {
    const detail = (await request(`/api/v1/admin/superset/datasets/${dataset.superset_id}`)).dataset;
    document.querySelector('#dataset-detail-title').textContent = detail.name;
    document.querySelector('#dataset-detail-meta').textContent =
      `${detail.database_name} · ${detail.schema} · ${detail.columns.length} 个字段 · 指标：${detail.metrics.join('、') || '无'}`;
    renderModuleRows('dataset-column-table-body', detail.columns, column => [
      column.name, column.type, column.is_time ? '是' : '否', column.filterable ? '是' : '否',
    ]);
    document.querySelector('#dataset-detail').hidden = false;
  } catch (error) { showManagementFeedback(error.message, true); }
}

async function deleteSupersetAsset(kind, asset) {
  const label = kind === 'databases' ? '数据库连接' : 'Dataset';
  if (!window.confirm(`确认删除${label}“${asset.name}”？系统会先检查 Superset 引用关系。`)) return;
  try {
    await request(`/api/v1/admin/superset/${kind}/${asset.superset_id}`, {
      method: 'DELETE', headers: { 'X-AgentBI-CSRF': currentUser.csrf_token },
    });
    showManagementFeedback(`${label}已删除`); await loadModuleView('data-sources');
  } catch (error) { showManagementFeedback(error.message, true); }
}

function closeDatasetDetail() { document.querySelector('#dataset-detail').hidden = true; }
document.querySelector('#close-dataset-detail').addEventListener('click', closeDatasetDetail);
document.querySelector('#cancel-dataset-detail').addEventListener('click', closeDatasetDetail);

function openDatasetDescriptionEditor(dataset) {
  editingSupersetDataset = dataset;
  document.querySelector('#dataset-description-title').textContent = `修改 ${dataset.name} 说明`;
  document.querySelector('#dataset-description').value = dataset.description || '';
  document.querySelector('#dataset-description-error').hidden = true;
  document.querySelector('#dataset-description-editor').hidden = false;
}
function closeDatasetDescriptionEditor() {
  document.querySelector('#dataset-description-editor').hidden = true;
}
document.querySelector('#close-dataset-description').addEventListener('click', closeDatasetDescriptionEditor);
document.querySelector('#cancel-dataset-description').addEventListener('click', closeDatasetDescriptionEditor);
document.querySelector('#dataset-description-form').addEventListener('submit', async event => {
  event.preventDefault();
  const errorBox = document.querySelector('#dataset-description-error'); errorBox.hidden = true;
  try {
    const body = await request(`/api/v1/admin/superset/datasets/${editingSupersetDataset.superset_id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token },
      body: JSON.stringify({ description: document.querySelector('#dataset-description').value.trim() }),
    });
    closeDatasetDescriptionEditor(); showManagementFeedback(body.message); await loadModuleView('data-sources');
  } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
});

document.querySelector('#create-report').addEventListener('click', async () => {
  const button = document.querySelector('#create-report');
  button.disabled = true; button.textContent = '正在生成…';
  const dashboardName = currentUser.role === 'admin' ? '全域经营与系统治理' : '销售经营分析';
  try {
    await request('/api/v1/reports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token },
      body: JSON.stringify({ title: `${dashboardName}快照报告`, dashboard_name: dashboardName }),
    });
    await loadModuleView('reports');
    showManagementFeedback('分析报告已保存');
  } catch (error) {
    showManagementFeedback(error.message, true);
  } finally {
    button.disabled = false; button.textContent = '＋ 生成当前快照报告';
  }
});

document.querySelector('#registry-refresh').addEventListener('click', renderRegistryCenter);

document.querySelectorAll('.drill-trigger').forEach(item => {
  item.addEventListener('click', () => selectDrillChart('revenue', true));
  item.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') selectDrillChart('revenue', true);
  });
});

function bindChartMenuEvents() {
  document.querySelectorAll('.chart-menu-trigger:not([data-bound])').forEach(button => {
    button.dataset.bound = 'true';
    button.addEventListener('click', event => {
      event.stopPropagation();
      const menu = document.querySelector(`[data-chart-menu="${button.dataset.drillChart}"]`);
      const willOpen = menu.hidden;
      closeChartMenus();
      menu.hidden = !willOpen;
      button.setAttribute('aria-expanded', String(willOpen));
    });
  });
  document.querySelectorAll('[data-drill-action]:not([data-bound])').forEach(button => {
    button.dataset.bound = 'true';
    button.addEventListener('click', event => {
      event.stopPropagation();
      selectDrillChart(button.dataset.drillChart, button.dataset.drillAction === 'enter');
    });
  });
}

bindChartMenuEvents();

const chartWizard = document.querySelector('#chart-wizard');
const chartWizardForm = document.querySelector('#chart-wizard-form');
const chartWizardError = document.querySelector('#chart-wizard-error');
let editingChartKey;
let wizardReturnView = 'chart-management';
let pendingDeleteChart;

function openChartWizard(chart) {
  editingChartKey = chart?.chart_key;
  wizardReturnView = document.querySelector('#drill-registry-view').hidden
    ? 'chart-management' : 'drill-registry';
  chartWizardForm.reset();
  chartWizardError.hidden = true;
  document.querySelector('#chart-wizard-title').textContent = chart
    ? '修改受治理分析图表' : '创建受治理分析图表';
  document.querySelector('#save-chart-wizard').textContent = chart ? '保存修改' : '创建并发布';
  const keyInput = document.querySelector('#new-chart-key');
  keyInput.disabled = Boolean(chart);
  if (chart) {
    keyInput.value = chart.chart_key;
    document.querySelector('#new-chart-title').value = chart.title;
    document.querySelector('#new-chart-dataset').value = chart.dataset_name;
    document.querySelector('#new-chart-metric').value = chart.metric;
    document.querySelector('#new-chart-type').value = chart.visualization_type;
    const modelSelect = document.querySelector('#new-chart-model');
    if (![...modelSelect.options].some(option => option.value === chart.semantic_model)) {
      const legacy = document.createElement('option');
      legacy.value = chart.semantic_model;
      legacy.textContent = `${chart.semantic_model}（旧映射，建议更换）`;
      modelSelect.append(legacy);
    }
    modelSelect.value = chart.semantic_model;
    document.querySelector('#new-chart-dimensions').value = chart.dimensions.join(' → ');
  }
  chartWizard.hidden = false;
  (chart ? document.querySelector('#new-chart-title') : keyInput).focus();
}

function closeChartWizard() {
  chartWizard.hidden = true;
  chartWizardError.hidden = true;
  editingChartKey = undefined;
  chartWizardForm.reset();
  document.querySelector('#new-chart-key').disabled = false;
}

document.querySelectorAll('.open-chart-wizard').forEach(button => button.addEventListener('click', () => openChartWizard()));
document.querySelector('#close-chart-wizard').addEventListener('click', closeChartWizard);
document.querySelector('#cancel-chart-wizard').addEventListener('click', closeChartWizard);
chartWizard.addEventListener('click', event => { if (event.target === chartWizard) closeChartWizard(); });

function openDeleteConfirmation(chart) {
  if (chart.is_published) return;
  pendingDeleteChart = chart;
  document.querySelector('#delete-chart-name').textContent = `${chart.title}（${chart.chart_key}）`;
  document.querySelector('#delete-chart-confirm').hidden = false;
}

function closeDeleteConfirmation() {
  document.querySelector('#delete-chart-confirm').hidden = true;
  pendingDeleteChart = undefined;
}

document.querySelector('#cancel-delete-chart').addEventListener('click', closeDeleteConfirmation);
document.querySelector('#close-delete-chart').addEventListener('click', closeDeleteConfirmation);
document.querySelector('#delete-chart-confirm').addEventListener('click', event => {
  if (event.target.id === 'delete-chart-confirm') closeDeleteConfirmation();
});
document.querySelector('#confirm-delete-chart').addEventListener('click', async () => {
  if (!pendingDeleteChart) return;
  const chart = pendingDeleteChart;
  const button = document.querySelector('#confirm-delete-chart');
  button.disabled = true; button.textContent = '正在删除…';
  try {
    await request(`/api/v1/admin/charts/${encodeURIComponent(chart.chart_key)}`, {
      method: 'DELETE', headers: { 'X-AgentBI-CSRF': currentUser.csrf_token },
    });
    closeDeleteConfirmation();
    await loadManagedCharts();
    renderRegistryCenter();
    renderChartManagement();
    showManagementFeedback(`${chart.title}已删除`);
  } catch (error) {
    showManagementFeedback(error.message, true);
  } finally {
    button.disabled = false; button.textContent = '确认删除';
  }
});

const assetEditor = document.querySelector('#asset-editor');
const assetEditorForm = document.querySelector('#asset-editor-form');
let editingAssetKind;
let editingAssetName;

function openAssetEditor(kind, asset = null) {
  editingAssetKind = kind; editingAssetName = asset?.name;
  assetEditorForm.reset();
  const semantic = kind === 'semantic-models';
  document.querySelector('#asset-editor-title').textContent = asset
    ? `修改${semantic ? '语义模型' : '数据源'}` : `新增${semantic ? '语义模型' : '数据源'}`;
  const name = document.querySelector('#asset-name');
  name.disabled = Boolean(asset); name.value = asset?.name || '';
  document.querySelector('#asset-secondary-label').textContent = semantic ? '主题域' : '数据源类型';
  document.querySelector('#asset-secondary-help').textContent = semantic
    ? '用于业务语义分类' : '例如 Superset Dataset';
  document.querySelector('#asset-secondary').value = semantic
    ? asset?.subject_area || '通用主题域' : asset?.type || 'Superset Dataset';
  document.querySelector('#asset-description').value = asset?.description || '';
  document.querySelector('#asset-status').value = asset?.status || 'active';
  document.querySelector('#asset-status-field').hidden = !asset;
  document.querySelector('#asset-editor-error').hidden = true;
  assetEditor.hidden = false; (asset ? document.querySelector('#asset-secondary') : name).focus();
}

function closeAssetEditor() {
  assetEditor.hidden = true; assetEditorForm.reset();
  editingAssetKind = undefined; editingAssetName = undefined;
  document.querySelector('#asset-name').disabled = false;
}

document.querySelectorAll('.open-asset-editor').forEach(button => {
  button.addEventListener('click', () => openAssetEditor(button.dataset.assetKind));
});
document.querySelector('#close-asset-editor').addEventListener('click', closeAssetEditor);
document.querySelector('#cancel-asset-editor').addEventListener('click', closeAssetEditor);
assetEditor.addEventListener('click', event => { if (event.target === assetEditor) closeAssetEditor(); });
assetEditorForm.addEventListener('submit', async event => {
  event.preventDefault();
  const kind = editingAssetKind; const editing = Boolean(editingAssetName);
  const name = editingAssetName || document.querySelector('#asset-name').value.trim();
  const semantic = kind === 'semantic-models';
  const secondary = document.querySelector('#asset-secondary').value.trim();
  const payload = semantic
    ? { subject_area: secondary, description: document.querySelector('#asset-description').value.trim() }
    : { source_type: secondary, description: document.querySelector('#asset-description').value.trim() };
  if (!editing) payload.name = name;
  else payload.status = document.querySelector('#asset-status').value;
  const button = assetEditorForm.querySelector('button[type="submit"]');
  const errorBox = document.querySelector('#asset-editor-error');
  button.disabled = true; errorBox.hidden = true;
  try {
    await request(editing ? `/api/v1/admin/${kind}/${encodeURIComponent(name)}` : `/api/v1/admin/${kind}`, {
      method: editing ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token },
      body: JSON.stringify(payload),
    });
    closeAssetEditor(); await loadModuleView(kind);
    showManagementFeedback(`${name} 已${editing ? '更新' : '创建'}`);
  } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
  finally { button.disabled = false; }
});

function openAssetDeleteConfirmation(kind, asset, returnView = kind) {
  pendingAsset = { kind, asset, returnView };
  const llmProvider = kind === 'llm-provider';
  document.querySelector('#delete-asset-title').textContent = llmProvider
    ? '确认删除模型服务' : '确认删除资产';
  document.querySelector('#delete-asset-confirm p').textContent = llmProvider
    ? '将永久删除这个非活动模型服务配置：' : '将永久删除未被图表引用的资产：';
  document.querySelector('#delete-asset-confirm small').textContent = llmProvider
    ? 'API Key 密文将同时删除；当前生效模型受服务端保护。'
    : '系统内置资产或存在图表引用时，服务端会拒绝删除。';
  document.querySelector('#delete-asset-name').textContent = asset.name;
  document.querySelector('#delete-asset-confirm').hidden = false;
}
function closeAssetDeleteConfirmation() {
  document.querySelector('#delete-asset-confirm').hidden = true; pendingAsset = undefined;
}
document.querySelector('#close-delete-asset').addEventListener('click', closeAssetDeleteConfirmation);
document.querySelector('#cancel-delete-asset').addEventListener('click', closeAssetDeleteConfirmation);
document.querySelector('#delete-asset-confirm').addEventListener('click', event => {
  if (event.target.id === 'delete-asset-confirm') closeAssetDeleteConfirmation();
});
document.querySelector('#confirm-delete-asset').addEventListener('click', async () => {
  if (!pendingAsset) return;
  const { kind, asset, returnView } = pendingAsset;
  try {
    const endpoint = kind === 'llm-provider'
      ? `/api/v1/admin/llm-provider/${asset.id}`
      : `/api/v1/admin/${kind}/${encodeURIComponent(asset.name)}`;
    await request(endpoint, {
      method: 'DELETE', headers: { 'X-AgentBI-CSRF': currentUser.csrf_token },
    });
    closeAssetDeleteConfirmation(); await loadModuleView(returnView);
    showManagementFeedback(`${asset.name} 已删除`);
  } catch (error) { showManagementFeedback(error.message, true); }
});

function roleActionGroup(role) {
  const group = document.createElement('div'); group.className = 'registry-actions';
  if (role.builtin) {
    const label = document.createElement('span'); label.className = 'builtin-label';
    label.textContent = '系统保护'; group.append(label); return group;
  }
  group.append(actionButton('修改', 'module-action', () => openRoleEditor(role)));
  const remove = actionButton('删除', 'danger-action', () => {
    openAssetDeleteConfirmation('roles', { name: role.code }, 'user-roles');
  });
  remove.disabled = role.user_count > 0;
  if (remove.disabled) remove.title = '该角色已分配用户，不能删除';
  group.append(remove); return group;
}

const roleEditor = document.querySelector('#role-editor');
const roleEditorForm = document.querySelector('#role-editor-form');
let editingRoleCode;

function renderPermissionOptions(selected = []) {
  const selectedSet = new Set(selected);
  const container = document.querySelector('#role-permission-options');
  container.replaceChildren(...loadedPermissions.map(permission => {
    const label = document.createElement('label');
    const input = document.createElement('input'); input.type = 'checkbox';
    input.value = permission.code; input.checked = selectedSet.has(permission.code);
    const text = document.createElement('span'); text.textContent = permission.name;
    label.append(input, text); return label;
  }));
}

function openRoleEditor(role = null) {
  editingRoleCode = role?.code; roleEditorForm.reset();
  document.querySelector('#role-editor-title').textContent = role ? '修改自定义角色' : '新增角色';
  const code = document.querySelector('#role-code'); code.disabled = Boolean(role);
  code.value = role?.code || ''; document.querySelector('#role-name').value = role?.name || '';
  document.querySelector('#role-description').value = role?.description || '';
  renderPermissionOptions(role?.permissions || ['workspace:view', 'dashboard:view']);
  document.querySelector('#role-editor-error').hidden = true; roleEditor.hidden = false;
  (role ? document.querySelector('#role-name') : code).focus();
}
function closeRoleEditor() {
  roleEditor.hidden = true; roleEditorForm.reset(); editingRoleCode = undefined;
  document.querySelector('#role-code').disabled = false;
}
document.querySelector('#create-role').addEventListener('click', () => openRoleEditor());
document.querySelector('#close-role-editor').addEventListener('click', closeRoleEditor);
document.querySelector('#cancel-role-editor').addEventListener('click', closeRoleEditor);
roleEditor.addEventListener('click', event => { if (event.target === roleEditor) closeRoleEditor(); });
roleEditorForm.addEventListener('submit', async event => {
  event.preventDefault();
  const permissions = [...document.querySelectorAll('#role-permission-options input:checked')]
    .map(input => input.value);
  const errorBox = document.querySelector('#role-editor-error');
  if (!permissions.length) {
    errorBox.textContent = '请至少选择一项权限'; errorBox.hidden = false; return;
  }
  const editing = Boolean(editingRoleCode);
  const payload = {
    name: document.querySelector('#role-name').value.trim(),
    description: document.querySelector('#role-description').value.trim(), permissions,
  };
  if (!editing) payload.code = document.querySelector('#role-code').value.trim();
  try {
    await request(editing ? `/api/v1/admin/roles/${encodeURIComponent(editingRoleCode)}` : '/api/v1/admin/roles', {
      method: editing ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token },
      body: JSON.stringify(payload),
    });
    closeRoleEditor(); await loadModuleView('user-roles');
    showManagementFeedback(`角色已${editing ? '更新' : '创建'}`);
  } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
});

const userEditor = document.querySelector('#user-editor');
const userEditorForm = document.querySelector('#user-editor-form');

function openUserEditor(user = null) {
  const creating = !user;
  document.querySelector('#user-editor-title').textContent = creating ? '新增用户' : '编辑用户权限';
  const username = document.querySelector('#edit-user-name');
  username.disabled = !creating; username.value = user?.username || '';
  document.querySelectorAll('.create-user-field').forEach(field => { field.hidden = !creating; });
  const displayName = document.querySelector('#edit-user-display-name');
  const password = document.querySelector('#edit-user-password');
  displayName.required = creating; password.required = creating;
  displayName.value = ''; password.value = '';
  const roleSelect = document.querySelector('#edit-user-role');
  roleSelect.replaceChildren(...loadedRoles.map(role => {
    const option = document.createElement('option'); option.value = role.code;
    option.textContent = role.name; return option;
  }));
  roleSelect.value = user?.role || 'user';
  document.querySelector('#edit-user-scope').value = user?.data_scope || '全部区域';
  document.querySelector('#edit-user-active').checked = user?.is_active ?? true;
  userEditorForm.querySelector('button[type="submit"]').textContent = creating ? '创建用户' : '保存权限';
  document.querySelector('#user-editor-error').hidden = true;
  userEditor.hidden = false;
  (creating ? username : document.querySelector('#edit-user-role')).focus();
}

function closeUserEditor() {
  userEditor.hidden = true;
  userEditorForm.reset();
  document.querySelector('#edit-user-name').disabled = true;
  document.querySelector('#user-editor-error').hidden = true;
}

document.querySelector('#create-user').addEventListener('click', () => openUserEditor());
document.querySelector('#close-user-editor').addEventListener('click', closeUserEditor);
document.querySelector('#cancel-user-editor').addEventListener('click', closeUserEditor);
userEditor.addEventListener('click', event => { if (event.target === userEditor) closeUserEditor(); });
userEditorForm.addEventListener('submit', async event => {
  event.preventDefault();
  const errorBox = document.querySelector('#user-editor-error');
  const button = userEditorForm.querySelector('button[type="submit"]');
  errorBox.hidden = true; button.disabled = true; button.textContent = '正在保存…';
  const username = document.querySelector('#edit-user-name').value;
  const creating = !document.querySelector('#edit-user-name').disabled;
  try {
    const payload = {
      role: document.querySelector('#edit-user-role').value,
      data_scope: document.querySelector('#edit-user-scope').value.trim(),
      is_active: document.querySelector('#edit-user-active').checked,
    };
    if (creating) {
      payload.username = username.trim();
      payload.password = document.querySelector('#edit-user-password').value;
      payload.display_name = document.querySelector('#edit-user-display-name').value.trim();
    }
    await request(creating ? '/api/v1/admin/users' : `/api/v1/admin/users/${encodeURIComponent(username)}`, {
      method: creating ? 'POST' : 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token },
      body: JSON.stringify(payload),
    });
    closeUserEditor();
    await loadModuleView('user-roles');
    showManagementFeedback(creating ? `${username} 已创建` : `${username} 的权限已更新`);
  } catch (error) {
    errorBox.textContent = error.message; errorBox.hidden = false;
  } finally {
    button.disabled = false; button.textContent = creating ? '创建用户' : '保存权限';
  }
});

function openReportDeleteConfirmation(report) {
  pendingReport = report;
  document.querySelector('#delete-report-name').textContent = report.title;
  document.querySelector('#delete-report-confirm').hidden = false;
}

function closeReportDeleteConfirmation() {
  document.querySelector('#delete-report-confirm').hidden = true;
  pendingReport = undefined;
}

document.querySelector('#close-delete-report').addEventListener('click', closeReportDeleteConfirmation);
document.querySelector('#cancel-delete-report').addEventListener('click', closeReportDeleteConfirmation);
document.querySelector('#delete-report-confirm').addEventListener('click', event => {
  if (event.target.id === 'delete-report-confirm') closeReportDeleteConfirmation();
});
document.querySelector('#confirm-delete-report').addEventListener('click', async () => {
  if (!pendingReport) return;
  const report = pendingReport;
  const button = document.querySelector('#confirm-delete-report');
  button.disabled = true; button.textContent = '正在删除…';
  try {
    await request(`/api/v1/reports/${encodeURIComponent(report.id)}`, {
      method: 'DELETE', headers: { 'X-AgentBI-CSRF': currentUser.csrf_token },
    });
    closeReportDeleteConfirmation();
    await loadModuleView('reports');
    showManagementFeedback(`${report.title}已删除`);
  } catch (error) {
    showManagementFeedback(error.message, true);
  } finally {
    button.disabled = false; button.textContent = '确认删除';
  }
});

chartWizardForm.addEventListener('submit', async event => {
  event.preventDefault();
  chartWizardError.hidden = true;
  const saveButton = document.querySelector('#save-chart-wizard');
  const dimensions = document.querySelector('#new-chart-dimensions').value
    .split(/[,，→>]+/).map(item => item.trim()).filter(Boolean);
  if (dimensions.length < 2 || new Set(dimensions).size !== dimensions.length) {
    chartWizardError.textContent = '请配置至少两个不重复的下钻维度';
    chartWizardError.hidden = false;
    return;
  }
  saveButton.disabled = true;
  const editingKey = editingChartKey;
  const returnView = wizardReturnView;
  saveButton.textContent = editingKey ? '正在保存…' : '正在创建…';
  try {
    const payload = {
      title: document.querySelector('#new-chart-title').value.trim(),
      dataset_name: document.querySelector('#new-chart-dataset').value.trim(),
      metric: document.querySelector('#new-chart-metric').value.trim(),
      visualization_type: document.querySelector('#new-chart-type').value,
      semantic_model: document.querySelector('#new-chart-model').value.trim(),
      dimensions,
    };
    if (!editingKey) payload.chart_key = document.querySelector('#new-chart-key').value.trim();
    await request(editingKey
      ? `/api/v1/admin/charts/${encodeURIComponent(editingKey)}`
      : '/api/v1/admin/charts', {
      method: editingKey ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token },
      body: JSON.stringify(payload),
    });
    closeChartWizard();
    await loadManagedCharts();
    switchView(returnView);
    showManagementFeedback(editingKey ? '图表与下钻配置已更新' : '图表已创建并发布');
  } catch (error) {
    chartWizardError.textContent = error.message;
    chartWizardError.hidden = false;
  } finally {
    saveButton.disabled = false;
    saveButton.textContent = editingKey ? '保存修改' : '创建并发布';
  }
});

loginForm.addEventListener('submit', async event => {
  event.preventDefault();
  loginError.hidden = true;
  loginButton.disabled = true;
  loginButton.textContent = '正在安全登录…';
  try {
    const user = await window.AgentBI.session.login(
      document.querySelector('#username').value,
      document.querySelector('#password').value,
    );
    document.querySelector('#password').value = '';
    showWorkbench(user);
  } catch (error) {
    loginError.textContent = error.message;
    loginError.hidden = false;
  } finally {
    loginButton.disabled = false;
    loginButton.textContent = '登录分析工作台';
  }
});

const accountMenu = document.querySelector('#account-menu');
const accountMenuButton = document.querySelector('#account-menu-button');
const sidebarToggle = document.querySelector('#sidebar-toggle');

function setSidebarCollapsed(collapsed, { persist = true } = {}) {
  workbenchView.classList.toggle('sidebar-collapsed', collapsed);
  sidebarToggle.setAttribute('aria-expanded', String(!collapsed));
  sidebarToggle.setAttribute('aria-label', collapsed ? '展开功能菜单' : '收起功能菜单');
  sidebarToggle.title = collapsed ? '展开功能菜单' : '收起功能菜单';
  if (persist) {
    try { localStorage.setItem('agentbi.sidebarCollapsed', String(collapsed)); }
    catch { /* Storage may be disabled; the current page state still works. */ }
  }
}

document.querySelectorAll('.sidebar .nav-item').forEach(item => {
  item.title = item.textContent.trim();
});
let sidebarInitiallyCollapsed = false;
try { sidebarInitiallyCollapsed = localStorage.getItem('agentbi.sidebarCollapsed') === 'true'; }
catch { /* Keep the expanded default when storage is unavailable. */ }
setSidebarCollapsed(sidebarInitiallyCollapsed, { persist: false });
sidebarToggle.addEventListener('click', event => {
  event.stopPropagation();
  accountMenu.hidden = true;
  setSidebarCollapsed(!workbenchView.classList.contains('sidebar-collapsed'));
});

accountMenuButton.addEventListener('click', event => {
  event.stopPropagation();
  accountMenu.hidden = !accountMenu.hidden;
  accountMenuButton.setAttribute('aria-expanded', String(!accountMenu.hidden));
});

document.addEventListener('click', () => {
  closeChartMenus();
  accountMenu.hidden = true;
  accountMenuButton.setAttribute('aria-expanded', 'false');
});

async function endSession({ switchAccount = false } = {}) {
  if (!currentUser) return;
  try { await window.AgentBI.session.logout(); }
  finally {
    accountMenu.hidden = true;
    document.querySelector('#password').value = '';
    if (switchAccount) document.querySelector('#username').value = '';
    showLogin();
    document.querySelector('#username').focus();
  }
}

document.querySelector('#switch-account-button').addEventListener('click', () => endSession({ switchAccount: true }));
document.querySelector('#logout-button').addEventListener('click', () => endSession());

function renderLlmProviderRows() {
  document.querySelector('#llm-provider-count').textContent = `${loadedLlmProviders.length} 个配置`;
  renderModuleRows('llm-provider-table-body', loadedLlmProviders, provider => [
    provider.model, provider.base_url, provider.api_key_masked,
    provider.enabled ? '● 当前生效' : '待切换', formatTimestamp(provider.updated_at),
  ], provider => {
    const group = document.createElement('div'); group.className = 'registry-actions';
    if (provider.enabled) {
      const active = document.createElement('span'); active.className = 'registry-status ready';
      active.textContent = '使用中'; group.append(active);
    } else {
      group.append(actionButton('测试并切换', 'primary-small', () => activateLlmProvider(provider)));
    }
    group.append(actionButton('修改', 'module-action', () => openLlmProviderEditor(provider)));
    const remove = actionButton('删除', 'danger-action', () => {
      openAssetDeleteConfirmation('llm-provider', { id: provider.id, name: provider.model }, 'semantic-models');
    });
    remove.disabled = provider.enabled;
    if (remove.disabled) remove.title = '当前生效模型不能删除，请先切换其他模型';
    group.append(remove); return group;
  });
}

async function activateLlmProvider(provider) {
  try {
    await request(`/api/v1/admin/llm-provider/${provider.id}/activate`, {
      method: 'POST', headers: { 'X-AgentBI-CSRF': currentUser.csrf_token },
    });
    await loadModuleView('semantic-models');
    showManagementFeedback(`已切换至 ${provider.model}`);
  } catch (cause) { showManagementFeedback(cause.message, true); }
}

function openLlmProviderEditor(provider = null) {
  const error = document.querySelector('#llm-provider-error');
  error.hidden = true;
  editingLlmProviderId = provider?.id;
  document.querySelector('#llm-provider-title').textContent = provider ? '修改 AI 模型服务' : '新增 AI 模型服务';
  document.querySelector('#llm-base-url').value = provider?.base_url || '';
  document.querySelector('#llm-model-name').value = provider?.model || '';
  document.querySelector('#llm-api-key').value = '';
  document.querySelector('#llm-api-key').placeholder = provider ? '留空则沿用已保存密钥' : '新建配置必须填写';
  document.querySelector('#llm-key-hint').textContent = provider
    ? `${provider.api_key_masked} 已加密保存，留空沿用` : '仅服务端加密保存';
  document.querySelector('#llm-enabled').checked = Boolean(provider?.enabled);
  document.querySelector('#llm-provider-editor').hidden = false;
}

function closeLlmProviderEditor() {
  document.querySelector('#llm-api-key').value = '';
  document.querySelector('#llm-provider-error').hidden = true;
  document.querySelector('#llm-provider-editor').hidden = true;
  editingLlmProviderId = undefined;
}

document.querySelector('#open-llm-provider').addEventListener('click', () => openLlmProviderEditor());
document.querySelector('#close-llm-provider').addEventListener('click', closeLlmProviderEditor);
document.querySelector('#cancel-llm-provider').addEventListener('click', closeLlmProviderEditor);
document.querySelector('#llm-provider-form').addEventListener('submit', async event => {
  event.preventDefault();
  const error = document.querySelector('#llm-provider-error');
  const button = event.currentTarget.querySelector('button[type="submit"]');
  error.hidden = true;
  button.disabled = true;
  button.textContent = '正在测试连接…';
  try {
    const editing = editingLlmProviderId;
    const body = await request(editing ? `/api/v1/admin/llm-provider/${editing}` : '/api/v1/admin/llm-provider', {
      method: editing ? 'PUT' : 'POST',
      headers: {'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token},
      body: JSON.stringify({
        base_url: document.querySelector('#llm-base-url').value.trim(),
        model: document.querySelector('#llm-model-name').value.trim(),
        api_key: document.querySelector('#llm-api-key').value,
        enabled: document.querySelector('#llm-enabled').checked,
      }),
    });
    closeLlmProviderEditor();
    await loadModuleView('semantic-models');
    showManagementFeedback(`${body.model} 模型服务已保存${body.enabled ? '并设为当前模型' : ''}`);
  } catch (cause) {
    error.textContent = cause.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = '保存模型服务';
  }
});

document.querySelector('#open-semantic-draft').addEventListener('click', openSemanticDraftEditor);
document.querySelector('#close-semantic-draft').addEventListener('click', closeSemanticDraftEditor);
document.querySelector('#cancel-semantic-draft').addEventListener('click', closeSemanticDraftEditor);
document.querySelector('#generate-semantic-draft').addEventListener('click', generateSemanticDraft);
document.querySelector('#semantic-draft-dataset').addEventListener('change', syncSemanticDatabaseSelection);
function openSemanticDomainEditor(selectedId = '') {
  document.querySelector('#semantic-domain-error').hidden = true;
  renderSemanticDomainEditor(selectedId);
  document.querySelector('#semantic-domain-editor').hidden = false;
}

function renderSemanticDomainRows() {
  renderModuleRows('semantic-domain-table-body', semanticCatalogDomains, domain => [
    domain.id,
    domain.name,
    domain.biz_name || '—',
    domain.description || '—',
    loadedSemanticModels.filter(model => Number(model.domain_id) === Number(domain.id)).length,
  ], domain => {
    const group = document.createElement('div');
    group.className = 'registry-actions';
    group.append(actionButton('修改', '', () => openSemanticDomainEditor(domain.id)));
    const remove = actionButton('删除', 'danger-action', () => deleteSemanticDomain(domain));
    const modelCount = loadedSemanticModels.filter(model => Number(model.domain_id) === Number(domain.id)).length;
    remove.disabled = modelCount > 0;
    if (remove.disabled) remove.title = `仍有 ${modelCount} 个语义模型引用，不能删除`;
    group.append(remove);
    return group;
  });
}

document.querySelector('#open-semantic-domain').addEventListener('click', () =>
  openSemanticDomainEditor(document.querySelector('#semantic-draft-domain').value));
document.querySelector('#create-semantic-domain').addEventListener('click', () => openSemanticDomainEditor());

function renderSemanticDomainEditor(selectedId = '') {
  const select = document.querySelector('#semantic-domain-existing');
  select.innerHTML = '<option value="">＋ 新建主题域</option>';
  semanticCatalogDomains.forEach(item => {
    const option = document.createElement('option');
    option.value = String(item.id); option.textContent = `${item.name}（ID ${item.id}）`;
    select.append(option);
  });
  select.value = selectedId ? String(selectedId) : '';
  const selected = semanticCatalogDomains.find(item => item.id === Number(select.value));
  document.querySelector('#semantic-domain-name').value = selected?.name || '';
  document.querySelector('#semantic-domain-biz-name').value = selected?.biz_name || '';
  document.querySelector('#semantic-domain-description').value = selected?.description || '';
  document.querySelector('#delete-semantic-domain').hidden = !selected;
  document.querySelector('#save-semantic-domain').textContent = selected ? '保存修改' : '创建主题域';
}

function closeSemanticDomainEditor() {
  document.querySelector('#semantic-domain-editor').hidden = true;
}

document.querySelector('#semantic-domain-existing').addEventListener('change', event => renderSemanticDomainEditor(event.target.value));
document.querySelector('#close-semantic-domain').addEventListener('click', closeSemanticDomainEditor);
document.querySelector('#cancel-semantic-domain').addEventListener('click', closeSemanticDomainEditor);
document.querySelector('#semantic-domain-form').addEventListener('submit', async event => {
  event.preventDefault();
  const error = document.querySelector('#semantic-domain-error');
  const selectedId = document.querySelector('#semantic-domain-existing').value;
  const button = document.querySelector('#save-semantic-domain');
  error.hidden = true; button.disabled = true;
  try {
    const payload = {
      name: document.querySelector('#semantic-domain-name').value.trim(),
      biz_name: document.querySelector('#semantic-domain-biz-name').value.trim(),
      description: document.querySelector('#semantic-domain-description').value.trim(),
    };
    const body = await request(`/api/v1/admin/semantic-drafts/domains${selectedId ? `/${selectedId}` : ''}`, {
      method: selectedId ? 'PUT' : 'POST',
      headers: {'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token},
      body: JSON.stringify(payload),
    });
    const catalog = await request('/api/v1/admin/semantic-drafts/catalog');
    semanticCatalogDomains = catalog.domains || [];
    fillSelect(document.querySelector('#semantic-draft-domain'), semanticCatalogDomains,
      item => `${item.name}（ID ${item.id}）`);
    document.querySelector('#semantic-draft-domain').value = String(body.domain.id);
    renderSemanticDomainRows();
    closeSemanticDomainEditor();
    showManagementFeedback(`${body.domain.name} 已保存到 SuperSonic 主题域`);
  } catch (cause) {
    error.textContent = cause.message; error.hidden = false;
  } finally {
    button.disabled = false;
  }
});
async function deleteSemanticDomain(selected) {
  if (!selected || !window.confirm(`确定删除主题域“${selected.name}”吗？已有语义模型引用时系统会拒绝删除。`)) return;
  const error = document.querySelector('#semantic-domain-error');
  error.hidden = true;
  try {
    await request(`/api/v1/admin/semantic-drafts/domains/${selected.id}`, {
      method: 'DELETE', headers: {'X-AgentBI-CSRF': currentUser.csrf_token},
    });
    const catalog = await request('/api/v1/admin/semantic-drafts/catalog');
    semanticCatalogDomains = catalog.domains || [];
    fillSelect(document.querySelector('#semantic-draft-domain'), semanticCatalogDomains,
      item => `${item.name}（ID ${item.id}）`);
    renderSemanticDomainRows();
    closeSemanticDomainEditor();
    showManagementFeedback(`${selected.name} 已删除`);
  } catch (cause) {
    error.textContent = cause.message; error.hidden = false;
    openSemanticDomainEditor(selected.id);
  }
}
document.querySelector('#delete-semantic-domain').addEventListener('click', () => {
  const selectedId = document.querySelector('#semantic-domain-existing').value;
  deleteSemanticDomain(semanticCatalogDomains.find(item => item.id === Number(selectedId)));
});
document.querySelector('#open-sonic-database').addEventListener('click', () => {
  document.querySelector('#sonic-database-error').hidden = true;
  document.querySelector('#sonic-database-editor').hidden = false;
});
function closeSonicDatabaseEditor() {
  document.querySelector('#sonic-db-password').value = '';
  document.querySelector('#sonic-database-editor').hidden = true;
}
document.querySelector('#close-sonic-database').addEventListener('click', closeSonicDatabaseEditor);
document.querySelector('#cancel-sonic-database').addEventListener('click', closeSonicDatabaseEditor);
document.querySelector('#sonic-db-engine').addEventListener('change', event => {
  document.querySelector('#sonic-db-port').value = event.target.value === 'postgresql' ? '5432' : '3306';
});
document.querySelector('#sonic-database-form').addEventListener('submit', async event => {
  event.preventDefault();
  const error = document.querySelector('#sonic-database-error');
  const button = event.currentTarget.querySelector('button[type="submit"]');
  error.hidden = true; button.disabled = true; button.textContent = '正在测试连接…';
  try {
    const payload = {
      name: document.querySelector('#sonic-db-name').value.trim(),
      engine: document.querySelector('#sonic-db-engine').value,
      host: document.querySelector('#sonic-db-host').value.trim(),
      port: Number(document.querySelector('#sonic-db-port').value),
      database: document.querySelector('#sonic-db-database').value.trim(),
      username: document.querySelector('#sonic-db-username').value.trim(),
      password: document.querySelector('#sonic-db-password').value,
    };
    const body = await request('/api/v1/admin/semantic-drafts/databases', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token},
      body: JSON.stringify(payload),
    });
    closeSonicDatabaseEditor();
    const catalog = await request('/api/v1/admin/semantic-drafts/catalog');
    semanticCatalogDatabases = catalog.databases || [];
    fillSelect(document.querySelector('#semantic-draft-database'), semanticCatalogDatabases,
      item => `${item.name}${item.type ? ` · ${item.type}` : ''}（ID ${item.id}）`);
    syncSemanticDatabaseSelection();
    if (!document.querySelector('#semantic-draft-database').value) {
      document.querySelector('#semantic-draft-database').value = String(body.database.id);
    }
    showManagementFeedback(`${body.database.name} 已通过测试并保存到 SuperSonic`);
  } catch (cause) {
    error.textContent = cause.message; error.hidden = false;
  } finally {
    button.disabled = false; button.textContent = '测试并保存';
  }
});
document.querySelector('#semantic-draft-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!activeSemanticDraft) return;
  const error = document.querySelector('#semantic-draft-error');
  const button = document.querySelector('#publish-semantic-draft');
  error.hidden = true; button.disabled = true; button.textContent = '正在发布并校验…';
  try {
    const draft = activeSemanticDraft;
    const payload = {
      dataset_id: draft.dataset.superset_id,
      domain_id: Number(document.querySelector('#semantic-draft-domain').value),
      database_id: Number(document.querySelector('#semantic-draft-database').value),
      name: document.querySelector('#semantic-draft-name').value.trim(),
      biz_name: document.querySelector('#semantic-draft-biz-name').value.trim(),
      description: document.querySelector('#semantic-draft-description').value.trim(),
      identifiers: parseDraftItems(document.querySelector('#semantic-draft-identifiers').value, 'identifier', draft.identifiers),
      dimensions: parseDraftItems(document.querySelector('#semantic-draft-dimensions').value, 'dimension', draft.dimensions),
      measures: parseDraftItems(document.querySelector('#semantic-draft-measures').value, 'measure', draft.measures),
      fields: draft.fields,
      drilldown_path: document.querySelector('#semantic-draft-drilldown').value
        .split(/[,，→>]+/).map(item => item.trim()).filter(Boolean),
    };
    if (!payload.identifiers.length || !payload.measures.length) throw new Error('至少保留一个标识符和一个度量');
    await request('/api/v1/admin/semantic-drafts/publish', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-AgentBI-CSRF': currentUser.csrf_token},
      body: JSON.stringify(payload),
    });
    closeSemanticDraftEditor();
    await loadModuleView('semantic-models');
    showManagementFeedback(`${payload.name} 已发布到 SuperSonic`);
  } catch (cause) {
    error.textContent = cause.message; error.hidden = false;
  } finally {
    button.disabled = false; button.textContent = '审核并发布到 SuperSonic';
  }
});

window.AgentBI.session.restore().then(showWorkbench).catch(showLogin);
renderDrilldown(selectedDrillChart);
