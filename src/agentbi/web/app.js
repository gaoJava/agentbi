const loginView = document.querySelector('#login-view');
const workbenchView = document.querySelector('#workbench-view');
const loginForm = document.querySelector('#login-form');
const loginError = document.querySelector('#login-error');
const loginButton = document.querySelector('#login-button');
let currentUser;

const drillRegistry = window.AGENTBI_DRILLDOWN_REGISTRY;
if (!drillRegistry || drillRegistry.version !== 1 || !drillRegistry.charts) {
  throw new Error('AgentBI 下钻注册中心未加载或版本不兼容');
}
const drillConfigurations = drillRegistry.charts;
let managedCharts = [];
const managedChartKeys = new Set();
let selectedDrillChart = sessionStorage.getItem('agentbi.drillSource');
if (!drillConfigurations[selectedDrillChart]) selectedDrillChart = 'revenue';

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
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.detail || '请求失败，请稍后重试');
  return body;
}

function showLogin() {
  currentUser = undefined;
  workbenchView.hidden = true;
  loginView.hidden = false;
}

function showWorkbench(user) {
  currentUser = user;
  const permissions = new Set(user.permissions);
  document.querySelectorAll('[data-permission]').forEach(item => { item.hidden = !permissions.has(item.dataset.permission); });
  document.querySelectorAll('.admin-only').forEach(item => { item.hidden = user.role !== 'admin'; });
  document.querySelectorAll('.admin-data').forEach(item => { item.hidden = user.role !== 'admin'; });
  document.querySelectorAll('.chart-menu-trigger').forEach(item => { item.hidden = !permissions.has('drilldown:use'); });
  document.querySelector('#user-name').textContent = user.display_name;
  document.querySelector('#account-username').textContent = user.username;
  document.querySelector('#user-role').textContent = user.role === 'admin' ? '系统管理员' : '数据分析师';
  document.querySelector('#avatar').textContent = user.display_name.slice(0, 1);
  document.querySelector('#scope-badge').textContent = `⌖ 数据范围：${user.data_scope}`;
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
}

function switchView(view) {
  const drilldown = view === 'drilldown';
  const registry = view === 'drill-registry';
  const chartManagement = view === 'chart-management';
  document.querySelectorAll('.dashboard-section').forEach(item => { item.hidden = drilldown || registry || chartManagement; });
  document.querySelector('#drilldown-view').hidden = !drilldown;
  document.querySelector('#drill-registry-view').hidden = !registry;
  document.querySelector('#chart-management-view').hidden = !chartManagement;
  document.querySelector('#dashboard-agent').hidden = drilldown;
  document.querySelector('#drill-agent').hidden = !drilldown;
  document.querySelector('.agent-input').hidden = drilldown;
  document.querySelectorAll('[data-view]').forEach(item => {
    item.classList.toggle('active', item.dataset.view === view);
  });
  if (!drilldown && !registry && !chartManagement && currentUser) {
    const admin = currentUser.role === 'admin';
    document.querySelector('#sales-dashboard').hidden = admin;
    document.querySelector('#admin-overview').hidden = !admin;
    document.querySelector('#user-permissions').hidden = admin;
    document.querySelector('#admin-permissions').hidden = !admin;
  }
  if (registry) renderRegistryCenter();
  if (chartManagement) renderChartManagement();
}

async function loadManagedCharts() {
  if (!currentUser) return;
  try {
    managedChartKeys.forEach(chartKey => delete drillConfigurations[chartKey]);
    managedChartKeys.clear();
    const endpoint = currentUser.role === 'admin' ? '/api/v1/admin/charts' : '/api/v1/charts';
    const body = await request(endpoint);
    managedCharts = body.charts || [];
    managedCharts.forEach(chart => {
      managedChartKeys.add(chart.chart_key);
      drillConfigurations[chart.chart_key] = configurationFromManagedChart(chart);
    });
    if (!isValidDrillConfiguration(drillConfigurations[selectedDrillChart])) {
      selectedDrillChart = 'revenue';
      sessionStorage.setItem('agentbi.drillSource', selectedDrillChart);
    }
    renderManagedDashboardCharts();
    initializeDrillableCharts();
    bindChartMenuEvents();
  } catch (error) {
    console.warn('无法加载数据库图表配置', error);
  }
}

function configurationFromManagedChart(chart) {
  const dimensions = chart.dimensions;
  const first = dimensions[0];
  const second = dimensions[1];
  const sourceType = chart.visualization_type === 'donut' ? 'structure' :
    chart.visualization_type === 'table' ? 'table' : 'revenue';
  const labels = ['华东', '华南', '华北', '西南'];
  const bars = labels.map((label, index) => [label, 88 - index * 17, String(820 - index * 145)]);
  const rows = ['第一类', '第二类', '第三类', '其他'].map((label, index) =>
    [label, String(410 - index * 75), `${42 - index * 9}%`]);
  return {
    published: chart.is_published,
    metric: chart.metric,
    semanticModel: chart.semantic_model,
    sourceType,
    pageTitle: `${chart.metric}下钻分析`,
    sourceTitle: chart.title,
    breadcrumb: `分析工作台 › ${chart.title} › ${dimensions.join(' › ')}`,
    connectorOne: `点击当前数据点，下钻维度：${first}`,
    levelOneLabel: `第 1 层 · ${first}`,
    levelOneTitle: `${chart.metric}按${first}分析`,
    bars,
    connectorTwo: `点击 华东，下钻维度：${second}`,
    levelTwoLabel: `第 2 层 · ${second}`,
    levelTwoTitle: `华东${second}贡献`,
    tableDimension: second,
    total: '820', rows,
    sourceColumns: [first, chart.metric, '占比'],
    sourceRows: bars.map(([label, , value], index) => [label, value, `${40 - index * 7}%`]),
    context: `第 2 层 · 华东${second}贡献`,
    questions: [`${chart.metric}主要来自哪个${first}？`, `哪个${second}表现异常？`],
    insight: `华东是当前${chart.metric}的主要贡献区域，建议继续按${second}定位变化来源。`,
    insightSource: `洞察来源：${chart.semantic_model} 语义模型`,
    evidence: `分析工作台 → ${chart.title} → ${dimensions.join(' → ')}`,
  };
}

function renderManagedDashboardCharts() {
  document.querySelectorAll('.managed-dashboard-chart').forEach(card => card.remove());
  const dashboard = document.querySelector('#sales-dashboard');
  managedCharts.filter(chart => chart.is_published).forEach(chart => {
    const card = document.createElement('article');
    card.className = 'chart-card managed-dashboard-chart revenue-chart';
    card.dataset.drillChart = chart.chart_key;
    const header = document.createElement('header');
    const titleGroup = document.createElement('div');
    const title = document.createElement('strong'); title.textContent = chart.title;
    const subtitle = document.createElement('small'); subtitle.textContent = `${chart.metric} · ${chart.dataset_name}`;
    titleGroup.append(title, subtitle); header.append(titleGroup); card.append(header);
    const preview = document.createElement('div');
    preview.className = `managed-chart-preview ${chart.visualization_type}`;
    if (chart.visualization_type === 'table') {
      preview.innerHTML = '<table><thead><tr><th>维度</th><th>指标值</th><th>同比</th></tr></thead><tbody><tr><td>华东</td><td>820</td><td>+18.6%</td></tr><tr><td>华南</td><td>675</td><td>+12.4%</td></tr><tr><td>华北</td><td>530</td><td>+8.2%</td></tr></tbody></table>';
    } else if (chart.visualization_type === 'donut') {
      preview.innerHTML = '<div class="managed-donut"><strong>42%</strong></div><p>华东 42%　华南 30%　其他 28%</p>';
    } else {
      preview.innerHTML = '<i style="--h:42%"></i><i style="--h:58%"></i><i style="--h:51%"></i><i style="--h:72%"></i><i style="--h:88%"></i><i style="--h:76%"></i>';
    }
    card.append(preview); dashboard.append(card);
  });
}

function renderChartManagement() {
  document.querySelector('#dynamic-chart-total').textContent = String(managedCharts.length);
  document.querySelector('#managed-drill-total').textContent = String(
    managedCharts.filter(chart => chart.is_published).length + 2
  );
  const body = document.querySelector('#managed-chart-table-body');
  const builtin = [
    { title: '季度销售收入趋势', chart_key: 'revenue', dataset_name: 'sales_orders', metric: '销售收入', visualization_type: 'bar', dimensions: ['区域', '产品线'], status: 'published' },
    { title: '销售结构（按产品大类）', chart_key: 'structure', dataset_name: 'sales_orders', metric: '销售收入', visualization_type: 'donut', dimensions: ['区域', '渠道'], status: 'published' },
  ];
  const typeLabels = { bar: '柱状图', line: '折线图', donut: '环图', table: '指标表格' };
  body.replaceChildren(...[...builtin.map(chart => ({ ...chart, builtin: true, is_published: true })), ...managedCharts].map(chart => {
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
    appendGovernanceActions(action, chart, true);
    row.append(action);
    return row;
  }));
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
  const entries = Object.entries(drillConfigurations);
  const validEntries = entries.filter(([, config]) => isValidDrillConfiguration(config));
  const models = new Set(entries.map(([, config]) => config.semanticModel).filter(Boolean));
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
    const title = document.createElement('strong'); title.textContent = config.sourceTitle || chartId;
    const metric = document.createElement('small'); metric.textContent = `Chart ID：${chartId} · 指标：${config.metric || '未配置'}`;
    chart.append(title, metric);
    const model = document.createElement('td'); model.textContent = config.semanticModel || '未配置';
    const type = document.createElement('td'); type.textContent = typeLabels[config.sourceType] || '未知类型';
    const path = document.createElement('td'); path.textContent = config.evidence || config.breadcrumb || '未配置';
    const status = document.createElement('td');
    const badge = document.createElement('span');
    const managed = managedCharts.find(item => item.chart_key === chartId);
    const valid = isValidDrillConfiguration(config);
    const offline = managed?.is_published === false;
    badge.className = offline ? 'registry-status offline' : valid ? 'registry-status ready' : 'registry-status invalid';
    badge.textContent = offline ? '● 已下线' : valid ? '● 可用' : '● 待完善';
    status.append(badge);
    const action = document.createElement('td');
    appendGovernanceActions(action, managed || { chart_key: chartId, builtin: true, is_published: true }, valid);
    row.append(chart, model, type, path, status, action);
    return row;
  }));
}

function renderDrilldown(chartId) {
  const config = drillConfigurations[chartId];
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
  document.querySelector('#drill-agent-context').textContent = config.context;
  document.querySelector('#drill-question-one').firstChild.textContent = `${config.questions[0]} `;
  document.querySelector('#drill-question-two').firstChild.textContent = `${config.questions[1]} `;
  document.querySelector('#drill-insight').textContent = config.insight;
  document.querySelector('#drill-insight-source').textContent = config.insightSource;
  document.querySelector('#drill-evidence').textContent = config.evidence;
  document.querySelectorAll('[data-drill-chart]').forEach(card => {
    if (!card.classList.contains('chart-card')) return;
    card.classList.toggle('drill-source-selected', card.dataset.drillChart === chartId);
    const badge = card.querySelector('.drill-ready-badge');
    if (badge) badge.hidden = card.dataset.drillChart !== chartId;
  });
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
    document.querySelector('#new-chart-model').value = chart.semantic_model;
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
    const body = await request('/api/v1/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: document.querySelector('#username').value, password: document.querySelector('#password').value }),
    });
    document.querySelector('#password').value = '';
    showWorkbench(body.user);
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
  try { await request('/api/v1/auth/logout', { method: 'POST', headers: { 'X-AgentBI-CSRF': currentUser.csrf_token } }); }
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

request('/api/v1/auth/me').then(body => showWorkbench(body.user)).catch(showLogin);
renderDrilldown(selectedDrillChart);
