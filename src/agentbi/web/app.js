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
let loadedUsers = [];
let loadedSemanticModels = [];
let loadedDataSources = [];
let loadedRoles = [];
let loadedPermissions = [];
let loadedSupersetDashboards = [];
let pendingReport;
let pendingAsset;
let dashboardCanvasMode = 'superset';
let supersetWorkspace;
let supersetFrameMode = 'view';
const managedChartKeys = new Set();
let activeView = 'dashboard';
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
  supersetWorkspace = undefined;
  const frame = document.querySelector('#superset-frame');
  frame.removeAttribute('src');
  frame.hidden = true;
  workbenchView.hidden = true;
  loginView.hidden = false;
}

function renderDashboardCanvasMode() {
  const dashboard = activeView === 'dashboard';
  const superset = dashboard && dashboardCanvasMode === 'superset';
  const admin = currentUser?.role === 'admin';
  document.querySelector('#sales-dashboard').hidden = !dashboard || superset || admin;
  document.querySelector('#admin-overview').hidden = !dashboard || superset || !admin;
  document.querySelector('#permission-card').hidden = !dashboard || superset;
  document.querySelector('#superset-canvas').hidden = !superset;
  document.querySelector('#local-canvas-tab').classList.toggle('active', !superset);
  document.querySelector('#superset-canvas-tab').classList.toggle('active', superset);
  if (dashboard && currentUser) {
    document.querySelector('#agent-dashboard-name').textContent = superset
      ? (supersetWorkspace?.dashboard_title || '经营总览')
      : (admin ? '全域经营治理' : '华东销售经营分析');
  }
}

function showSupersetUnavailable(message) {
  document.querySelector('#superset-loading').hidden = true;
  document.querySelector('#superset-frame').hidden = true;
  document.querySelector('#superset-unavailable-message').textContent = message;
  document.querySelector('#superset-unavailable').hidden = false;
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
  document.querySelector('#superset-view-mode').classList.toggle('active', mode === 'view');
  document.querySelector('#superset-edit-mode').classList.toggle('active', mode === 'edit');
  document.querySelector('#superset-mode-label').textContent = mode === 'edit'
    ? '编辑态 · 修改保存于 Superset'
    : '查看态 · 隐藏重复导航';
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
  document.querySelector('#superset-unavailable').hidden = true;
  document.querySelector('#superset-loading').hidden = false;
  if (supersetWorkspace?.available && !force) {
    setSupersetFrameMode(supersetFrameMode);
    return;
  }
  try {
    const body = await request('/api/v1/superset/workspace');
    supersetWorkspace = body.workspace;
    if (!supersetWorkspace.available) {
      showSupersetUnavailable(supersetWorkspace.message || 'Superset 服务当前不可用，可继续使用本地降级画布。');
      return;
    }
    setSupersetFrameMode(supersetFrameMode === 'edit' && supersetWorkspace.can_edit ? 'edit' : 'view');
  } catch (error) {
    showSupersetUnavailable(error.message);
  }
}

function selectDashboardCanvas(mode) {
  dashboardCanvasMode = mode;
  renderDashboardCanvasMode();
  if (mode === 'superset') loadSupersetWorkspace();
}

function showWorkbench(user) {
  currentUser = user;
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
  selectDashboardCanvas('superset');
  loadManagedCharts();
}

function switchView(view) {
  activeView = view;
  const drilldown = view === 'drilldown';
  const registry = view === 'drill-registry';
  const chartManagement = view === 'chart-management';
  const dashboard = view === 'dashboard';
  document.querySelectorAll('.dashboard-section').forEach(item => { item.hidden = !dashboard; });
  document.querySelectorAll('.drilldown-view,.registry-view').forEach(item => { item.hidden = true; });
  if (!dashboard) {
    const target = document.querySelector(`#${view}-view`);
    if (target) target.hidden = false;
  }
  document.querySelector('#dashboard-agent').hidden = drilldown;
  document.querySelector('#drill-agent').hidden = !drilldown;
  document.querySelector('.agent-input').hidden = drilldown;
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
  }
  if (registry) renderRegistryCenter();
  if (chartManagement) {
    renderChartManagement();
    loadSupersetDashboardAssets().catch(error => showManagementFeedback(error.message, true));
  }
  if (document.querySelector(`#${view}-view.module-view`)) loadModuleView(view);
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
      renderReportList((await request('/api/v1/reports')).reports || []);
    } else if (view === 'semantic-models') {
      loadedSemanticModels = (await request('/api/v1/admin/semantic-models')).models || [];
      renderModuleRows('semantic-model-table-body', loadedSemanticModels, model => [
        model.name, model.metrics.join('、'), model.charts,
        model.status === 'active' ? '● 已启用' : '● 已下线',
      ], model => assetActionGroup('semantic-models', model));
    } else if (view === 'data-sources') {
      const assets = await request('/api/v1/admin/superset/data-assets');
      loadedDataSources = assets.datasets || [];
      const databases = assets.databases || [];
      document.querySelector('#real-database-total').textContent = String(databases.length);
      document.querySelector('#real-dataset-total').textContent = String(loadedDataSources.length);
      document.querySelector('#real-database-engines').textContent =
        [...new Set(databases.map(database => database.backend))].join('、') || '—';
      document.querySelector('#real-source-status').textContent = '已同步';
      renderModuleRows('superset-database-table-body', databases, database => [
        database.name, database.backend, database.dataset_count,
        database.expose_in_sqllab ? '● 已开放' : '—', database.superset_id,
      ]);
      renderModuleRows('data-source-table-body', loadedDataSources, dataset => [
        dataset.name, dataset.database_name, dataset.schema,
        dataset.kind === 'virtual' ? '虚拟数据集' : '物理表', dataset.superset_id,
      ]);
    } else if (view === 'user-roles') {
      const [userBody, roleBody, permissionBody] = await Promise.all([
        request('/api/v1/admin/users'), request('/api/v1/admin/roles'),
        request('/api/v1/admin/permissions'),
      ]);
      loadedUsers = userBody.users || [];
      loadedRoles = roleBody.roles || [];
      loadedPermissions = permissionBody.permissions || [];
      renderModuleRows('user-role-table-body', loadedUsers, user => [
        user.display_name, user.username, user.role_name,
        user.data_scope, user.is_active ? '● 正常' : '● 已停用', formatTimestamp(user.last_login_at),
      ], user => actionButton('编辑权限', 'module-action', () => openUserEditor(user)));
      renderModuleRows('role-table-body', loadedRoles, role => [
        role.name, role.code, role.user_count, role.permissions.length,
        role.builtin ? '内置角色' : '自定义角色',
      ], role => roleActionGroup(role));
    } else if (view === 'audit-security') {
      const events = (await request('/api/v1/admin/audit-events')).events || [];
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
    dimensions,
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

function renderSupersetDashboardAssets() {
  document.querySelector('#superset-dashboard-total').textContent = `${loadedSupersetDashboards.length} 个仪表盘`;
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
    const home = actionButton('设为经营总览', '', async () => {
      try {
        await request(`/api/v1/admin/superset/dashboards/${dashboard.superset_id}/home`, {
          method: 'POST', headers: { 'X-AgentBI-CSRF': currentUser.csrf_token },
        });
        supersetWorkspace = undefined;
        await loadSupersetDashboardAssets();
        showManagementFeedback(`${dashboard.title} 已设为经营总览`);
      } catch (error) { showManagementFeedback(error.message, true); }
    });
    home.disabled = dashboard.is_home || !dashboard.published || !dashboard.available;
    action.append(home); row.append(action); return row;
  }));
}

async function loadSupersetDashboardAssets() {
  const body = await request('/api/v1/admin/superset/dashboards');
  loadedSupersetDashboards = body.dashboards || [];
  renderSupersetDashboardAssets();
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

document.querySelector('#local-canvas-tab').addEventListener('click', () => selectDashboardCanvas('local'));
document.querySelector('#superset-canvas-tab').addEventListener('click', () => selectDashboardCanvas('superset'));
document.querySelector('#back-local-canvas').addEventListener('click', () => selectDashboardCanvas('local'));
document.querySelector('#retry-superset').addEventListener('click', () => loadSupersetWorkspace({ force: true }));
document.querySelector('#superset-view-mode').addEventListener('click', () => setSupersetFrameMode('view'));
document.querySelector('#superset-edit-mode').addEventListener('click', () => setSupersetFrameMode('edit'));
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
    setSupersetFrameMode('edit');
    return;
  }
  switchView('chart-management');
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
    renderSupersetDashboardAssets();
    supersetWorkspace = undefined;
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
    await request(`/api/v1/admin/${kind}/${encodeURIComponent(asset.name)}`, {
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
