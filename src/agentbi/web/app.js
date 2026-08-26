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
let selectedDrillChart = sessionStorage.getItem('agentbi.drillSource');
if (!drillConfigurations[selectedDrillChart]) selectedDrillChart = 'revenue';

function isValidDrillConfiguration(config) {
  return Boolean(
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
}

function switchView(view) {
  const drilldown = view === 'drilldown';
  document.querySelectorAll('.dashboard-section').forEach(item => { item.hidden = drilldown; });
  document.querySelector('#drilldown-view').hidden = !drilldown;
  document.querySelector('#dashboard-agent').hidden = drilldown;
  document.querySelector('#drill-agent').hidden = !drilldown;
  document.querySelector('.agent-input').hidden = drilldown;
  document.querySelectorAll('[data-view]').forEach(item => {
    item.classList.toggle('active', item.dataset.view === view);
  });
  if (!drilldown && currentUser) {
    const admin = currentUser.role === 'admin';
    document.querySelector('#sales-dashboard').hidden = admin;
    document.querySelector('#admin-overview').hidden = !admin;
    document.querySelector('#user-permissions').hidden = admin;
    document.querySelector('#admin-permissions').hidden = !admin;
  }
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

document.querySelectorAll('.drill-trigger').forEach(item => {
  item.addEventListener('click', () => selectDrillChart('revenue', true));
  item.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') selectDrillChart('revenue', true);
  });
});

document.querySelectorAll('.chart-menu-trigger').forEach(button => button.addEventListener('click', event => {
  event.stopPropagation();
  const menu = document.querySelector(`[data-chart-menu="${button.dataset.drillChart}"]`);
  const willOpen = menu.hidden;
  closeChartMenus();
  menu.hidden = !willOpen;
  button.setAttribute('aria-expanded', String(willOpen));
}));

document.querySelectorAll('[data-drill-action]').forEach(button => button.addEventListener('click', event => {
  event.stopPropagation();
  selectDrillChart(button.dataset.drillChart, button.dataset.drillAction === 'enter');
}));

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
