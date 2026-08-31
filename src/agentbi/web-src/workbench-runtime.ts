/** Strict browser contracts shared by the AgentBI workbench features. */
namespace AgentBI {
  export type Role = 'admin' | 'user' | string;
  export type SourceType = 'revenue' | 'structure' | 'table';
  export type VisualizationType = 'bar' | 'line' | 'donut' | 'table';

  export interface ManagedChart {
    id: string;
    chart_key: string;
    title: string;
    metric: string;
    dataset_name: string;
    visualization_type: VisualizationType;
    semantic_model: string;
    dimensions: string[];
    status: string;
    is_published: boolean;
    created_at: string;
  }

  export interface GovernedUser {
    id: string; username: string; display_name: string; role: string; role_name: string;
    data_scope: string; is_active: boolean; last_login_at: string | null;
  }
  export interface GovernedRole {
    code: string; name: string; description: string; permissions: string[];
    user_count: number; builtin: boolean;
  }
  export interface GovernedPermission { code: string; name: string }
  export interface SemanticModelAsset {
    name: string; subject_area: string; description: string; status: string;
    metrics: string[]; charts: number; is_system: boolean;
  }
  export interface SupersetDatabaseAsset {
    superset_id: number; name: string; backend: string; database: string; expose_in_sqllab: boolean;
    allow_file_upload: boolean; dataset_count: number;
  }
  export interface SupersetDatasetAsset {
    superset_id: number; name: string; database_id: number; database_name: string;
    schema: string; kind: string; description: string; explore_url: string;
  }
  export interface SupersetEngineAsset {
    engine: string; name: string; drivers: string[]; placeholder: string;
  }
  export interface SupersetDataAssets {
    databases: SupersetDatabaseAsset[]; datasets: SupersetDatasetAsset[];
    available_engines: SupersetEngineAsset[];
  }
  export interface SavedAnalysisReport {
    id: string; title: string; dashboard_name: string; data_scope: string;
    summary: string; evidence_path: string; created_at: string;
  }
  export interface AuditEvent {
    id: string; event_type: string; outcome: string; actor: string;
    source_ip: string; detail: string; created_at: string;
  }

  export interface SessionUser {
    subject: string;
    username: string;
    display_name: string;
    role: Role;
    role_label: string;
    permissions: string[];
    data_scope: string;
    csrf_token: string;
  }

  export interface SupersetWorkspace {
    available: boolean;
    can_edit: boolean;
    status?: string;
    dashboard_id?: number;
    dashboard_title?: string;
    view_url?: string;
    edit_url?: string;
    message?: string;
  }

  export interface DrillConfiguration {
    metric: string;
    semanticModel: string;
    sourceType: SourceType;
    dimensions: string[];
    pageTitle: string;
    sourceTitle: string;
    breadcrumb: string;
    connectorOne: string;
    levelOneLabel: string;
    levelOneTitle: string;
    bars: Array<[string, number, string]>;
    connectorTwo: string;
    levelTwoLabel: string;
    levelTwoTitle: string;
    tableDimension: string;
    total: string;
    rows: Array<[string, string, string]>;
    context: string;
    questions: string[];
    insight: string;
    insightSource: string;
    evidence: string;
    published?: boolean;
    sourceColumns?: string[];
    sourceRows?: string[][];
  }

  export interface DrillRegistry {
    readonly version: 1;
    readonly charts: Readonly<Record<string, DrillConfiguration>>;
  }

  export interface ApiErrorBody { detail?: unknown }

  interface SessionEnvelope { user: unknown }
  interface WorkspaceEnvelope { workspace: unknown }

  export async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
    const response = await fetch(url, { credentials: 'same-origin', ...options });
    const body: unknown = response.status === 204
      ? null
      : await response.json().catch((): null => null);
    if (!response.ok) {
      const rawDetail = typeof body === 'object' && body !== null && 'detail' in body
        ? (body as ApiErrorBody).detail
        : undefined;
      const detail = typeof rawDetail === 'string'
        ? rawDetail
        : Array.isArray(rawDetail)
          ? rawDetail.map(item => {
              if (typeof item !== 'object' || item === null) return String(item);
              const issue = item as {loc?: unknown[]; msg?: unknown};
              const field = Array.isArray(issue.loc) ? issue.loc.slice(1).join('.') : '';
              return `${field ? `${field}：` : ''}${String(issue.msg || '请求参数无效')}`;
            }).join('；')
          : '';
      throw new Error(detail || '请求失败，请稍后重试');
    }
    return body as T;
  }

  function parseSessionUser(value: unknown): SessionUser {
    if (typeof value !== 'object' || value === null) throw new Error('登录身份响应无效');
    const user = value as Partial<SessionUser>;
    const requiredStrings: Array<keyof SessionUser> = [
      'subject', 'username', 'display_name', 'role', 'role_label', 'data_scope', 'csrf_token',
    ];
    if (requiredStrings.some(key => typeof user[key] !== 'string')) {
      throw new Error('登录身份响应缺少必要字段');
    }
    if (!Array.isArray(user.permissions) || user.permissions.some(item => typeof item !== 'string')) {
      throw new Error('登录权限响应无效');
    }
    return user as SessionUser;
  }

  export class SessionClient {
    private activeUser: SessionUser | undefined;

    get user(): SessionUser | undefined { return this.activeUser; }

    accept(value: unknown): SessionUser {
      this.activeUser = parseSessionUser(value);
      return this.activeUser;
    }

    clear(): void { this.activeUser = undefined; }

    async login(username: string, password: string): Promise<SessionUser> {
      const body = await request<SessionEnvelope>('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      return this.accept(body.user);
    }

    async restore(): Promise<SessionUser> {
      const body = await request<SessionEnvelope>('/api/v1/auth/me');
      return this.accept(body.user);
    }

    csrfHeaders(json = false): Record<string, string> {
      if (!this.activeUser) throw new Error('登录会话已失效');
      return {
        ...(json ? { 'Content-Type': 'application/json' } : {}),
        'X-AgentBI-CSRF': this.activeUser.csrf_token,
      };
    }

    async logout(): Promise<void> {
      if (!this.activeUser) return;
      try {
        await request<null>('/api/v1/auth/logout', {
          method: 'POST', headers: this.csrfHeaders(),
        });
      } finally {
        this.clear();
      }
    }
  }

  export const session = new SessionClient();

  function safeWorkspaceUrl(value: unknown, field: string): string | undefined {
    if (value === undefined || value === null || value === '') return undefined;
    if (typeof value !== 'string') throw new Error(`Superset ${field} 地址无效`);
    let parsed: URL;
    try { parsed = new URL(value, window.location.origin); }
    catch { throw new Error(`Superset ${field} 地址无效`); }
    if (!['http:', 'https:'].includes(parsed.protocol)) {
      throw new Error(`Superset ${field} 地址协议不安全`);
    }
    return parsed.href;
  }

  function parseSupersetWorkspace(value: unknown): SupersetWorkspace {
    if (typeof value !== 'object' || value === null) throw new Error('Superset 工作区响应无效');
    const raw = value as Record<string, unknown>;
    if (typeof raw.available !== 'boolean' || typeof raw.can_edit !== 'boolean') {
      throw new Error('Superset 工作区状态缺少必要字段');
    }
    const viewUrl = safeWorkspaceUrl(raw.view_url, '查看');
    const editUrl = safeWorkspaceUrl(raw.edit_url, '编辑');
    if (raw.available && !viewUrl) throw new Error('Superset 工作区缺少查看地址');
    if (raw.available && raw.can_edit && !editUrl) throw new Error('Superset 工作区缺少编辑地址');
    return {
      available: raw.available,
      can_edit: raw.can_edit,
      ...(typeof raw.status === 'string' ? { status: raw.status } : {}),
      ...(typeof raw.dashboard_id === 'number' ? { dashboard_id: raw.dashboard_id } : {}),
      ...(typeof raw.dashboard_title === 'string' ? { dashboard_title: raw.dashboard_title } : {}),
      ...(typeof raw.message === 'string' ? { message: raw.message } : {}),
      ...(viewUrl ? { view_url: viewUrl } : {}),
      ...(editUrl ? { edit_url: editUrl } : {}),
    };
  }

  export class SupersetWorkspaceClient {
    private cached: SupersetWorkspace | undefined;

    clear(): void { this.cached = undefined; }

    async load(force = false): Promise<SupersetWorkspace> {
      if (this.cached && !force) return this.cached;
      const body = await request<WorkspaceEnvelope>('/api/v1/superset/workspace');
      this.cached = parseSupersetWorkspace(body.workspace);
      return this.cached;
    }
  }

  export const supersetWorkspace = new SupersetWorkspaceClient();

  function requiredText(raw: Record<string, unknown>, key: string): string {
    const value = raw[key];
    if (typeof value !== 'string' || !value.trim()) throw new Error(`图表字段 ${key} 无效`);
    return value.trim();
  }

  function requiredBoolean(raw: Record<string, unknown>, key: string): boolean {
    if (typeof raw[key] !== 'boolean') throw new Error(`治理字段 ${key} 无效`);
    return raw[key] as boolean;
  }

  function requiredNumber(raw: Record<string, unknown>, key: string): number {
    const value = raw[key];
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
      throw new Error(`治理字段 ${key} 无效`);
    }
    return value;
  }

  function textList(value: unknown, field: string): string[] {
    if (!Array.isArray(value) || value.some(item => typeof item !== 'string')) {
      throw new Error(`治理字段 ${field} 无效`);
    }
    return value.map(item => item.trim()).filter(Boolean);
  }

  function objectList(value: unknown, field: string): Array<Record<string, unknown>> {
    if (!Array.isArray(value) || value.some(item => typeof item !== 'object' || item === null)) {
      throw new Error(`${field}列表响应无效`);
    }
    return value as Array<Record<string, unknown>>;
  }

  export function parseGovernedUsers(value: unknown): GovernedUser[] {
    return objectList(value, '用户').map(raw => ({
      id: requiredText(raw, 'id'), username: requiredText(raw, 'username'),
      display_name: requiredText(raw, 'display_name'), role: requiredText(raw, 'role'),
      role_name: requiredText(raw, 'role_name'), data_scope: requiredText(raw, 'data_scope'),
      is_active: requiredBoolean(raw, 'is_active'),
      last_login_at: raw.last_login_at === null ? null : requiredText(raw, 'last_login_at'),
    }));
  }

  export function parseGovernedRoles(value: unknown): GovernedRole[] {
    return objectList(value, '角色').map(raw => ({
      code: requiredText(raw, 'code'), name: requiredText(raw, 'name'),
      description: typeof raw.description === 'string' ? raw.description : '',
      permissions: textList(raw.permissions, 'permissions'),
      user_count: requiredNumber(raw, 'user_count'), builtin: requiredBoolean(raw, 'builtin'),
    }));
  }

  export function parseGovernedPermissions(value: unknown): GovernedPermission[] {
    return objectList(value, '权限').map(raw => ({
      code: requiredText(raw, 'code'), name: requiredText(raw, 'name'),
    }));
  }

  export function parseSemanticModels(value: unknown): SemanticModelAsset[] {
    return objectList(value, '语义模型').map(raw => ({
      name: requiredText(raw, 'name'), subject_area: requiredText(raw, 'subject_area'),
      description: typeof raw.description === 'string' ? raw.description : '',
      status: requiredText(raw, 'status'), metrics: textList(raw.metrics, 'metrics'),
      charts: requiredNumber(raw, 'charts'), is_system: requiredBoolean(raw, 'is_system'),
    }));
  }

  export function parseSupersetDataAssets(value: unknown): SupersetDataAssets {
    if (typeof value !== 'object' || value === null) throw new Error('Superset 数据资产响应无效');
    const raw = value as Record<string, unknown>;
    const databases = objectList(raw.databases, 'Database').map(item => ({
      superset_id: requiredNumber(item, 'superset_id'), name: requiredText(item, 'name'),
      backend: requiredText(item, 'backend'), database: requiredText(item, 'database'),
      expose_in_sqllab: requiredBoolean(item, 'expose_in_sqllab'),
      allow_file_upload: requiredBoolean(item, 'allow_file_upload'),
      dataset_count: requiredNumber(item, 'dataset_count'),
    }));
    const datasets = objectList(raw.datasets, 'Dataset').map(item => ({
      superset_id: requiredNumber(item, 'superset_id'), name: requiredText(item, 'name'),
      database_id: requiredNumber(item, 'database_id'), database_name: requiredText(item, 'database_name'),
      schema: requiredText(item, 'schema'), kind: requiredText(item, 'kind'),
      description: typeof item.description === 'string' ? item.description : '',
      explore_url: typeof item.explore_url === 'string' ? item.explore_url : '',
    }));
    const available_engines = objectList(raw.available_engines, '连接器').map(item => ({
      engine: requiredText(item, 'engine'), name: requiredText(item, 'name'),
      drivers: textList(item.drivers, 'drivers'),
      placeholder: typeof item.placeholder === 'string' ? item.placeholder : '',
    }));
    return { databases, datasets, available_engines };
  }

  export function parseSavedReports(value: unknown): SavedAnalysisReport[] {
    return objectList(value, '报告').map(raw => ({
      id: requiredText(raw, 'id'), title: requiredText(raw, 'title'),
      dashboard_name: requiredText(raw, 'dashboard_name'), data_scope: requiredText(raw, 'data_scope'),
      summary: requiredText(raw, 'summary'), evidence_path: requiredText(raw, 'evidence_path'),
      created_at: requiredText(raw, 'created_at'),
    }));
  }

  export function parseAuditEvents(value: unknown): AuditEvent[] {
    return objectList(value, '审计事件').map(raw => ({
      id: requiredText(raw, 'id'), event_type: requiredText(raw, 'event_type'),
      outcome: requiredText(raw, 'outcome'), actor: requiredText(raw, 'actor'),
      source_ip: typeof raw.source_ip === 'string' ? raw.source_ip : '',
      detail: typeof raw.detail === 'string' ? raw.detail : '',
      created_at: requiredText(raw, 'created_at'),
    }));
  }

  function parseManagedChart(value: unknown): ManagedChart {
    if (typeof value !== 'object' || value === null) throw new Error('图表配置响应无效');
    const raw = value as Record<string, unknown>;
    const types: VisualizationType[] = ['bar', 'line', 'donut', 'table'];
    const visualization = requiredText(raw, 'visualization_type');
    if (!types.includes(visualization as VisualizationType)) throw new Error('图表展示类型不受支持');
    if (!Array.isArray(raw.dimensions) || raw.dimensions.length < 2 || raw.dimensions.length > 5) {
      throw new Error('图表下钻维度必须为 2 至 5 层');
    }
    const dimensions = raw.dimensions.map(item => {
      if (typeof item !== 'string' || !item.trim()) throw new Error('图表下钻维度无效');
      return item.trim();
    });
    if (new Set(dimensions).size !== dimensions.length) throw new Error('图表下钻维度不能重复');
    if (typeof raw.is_published !== 'boolean') throw new Error('图表发布状态无效');
    return {
      id: requiredText(raw, 'id'), chart_key: requiredText(raw, 'chart_key'),
      title: requiredText(raw, 'title'), metric: requiredText(raw, 'metric'),
      dataset_name: requiredText(raw, 'dataset_name'),
      visualization_type: visualization as VisualizationType,
      semantic_model: requiredText(raw, 'semantic_model'), dimensions,
      status: requiredText(raw, 'status'), is_published: raw.is_published,
      created_at: requiredText(raw, 'created_at'),
    };
  }

  export function parseManagedCharts(value: unknown): ManagedChart[] {
    if (!Array.isArray(value)) throw new Error('图表配置列表无效');
    return value.map(parseManagedChart);
  }

  export function configurationFromManagedChart(chart: ManagedChart): DrillConfiguration {
    const [first, second] = chart.dimensions;
    if (!first || !second) throw new Error('图表缺少基础下钻维度');
    const sourceType: SourceType = chart.visualization_type === 'donut'
      ? 'structure' : chart.visualization_type === 'table' ? 'table' : 'revenue';
    const labels = ['华东', '华南', '华北', '西南'];
    const bars: Array<[string, number, string]> = labels.map((label, index) =>
      [label, 88 - index * 17, String(820 - index * 145)]);
    const rows: Array<[string, string, string]> = ['第一类', '第二类', '第三类', '其他']
      .map((label, index) => [label, String(410 - index * 75), `${42 - index * 9}%`]);
    return {
      published: chart.is_published, metric: chart.metric,
      semanticModel: chart.semantic_model, dimensions: chart.dimensions, sourceType,
      pageTitle: `${chart.metric}下钻分析`, sourceTitle: chart.title,
      breadcrumb: `分析工作台 › ${chart.title} › ${chart.dimensions.join(' › ')}`,
      connectorOne: `点击当前数据点，下钻维度：${first}`,
      levelOneLabel: `第 1 层 · ${first}`, levelOneTitle: `${chart.metric}按${first}分析`, bars,
      connectorTwo: `点击 华东，下钻维度：${second}`,
      levelTwoLabel: `第 2 层 · ${second}`, levelTwoTitle: `华东${second}贡献`,
      tableDimension: second, total: '820', rows,
      sourceColumns: [first, chart.metric, '占比'],
      sourceRows: bars.map(([label, , amount], index) => [label, amount, `${40 - index * 7}%`]),
      context: `第 2 层 · 华东${second}贡献`,
      questions: [`${chart.metric}主要来自哪个${first}？`, `哪个${second}表现异常？`],
      insight: `华东是当前${chart.metric}的主要贡献区域，建议继续按${second}定位变化来源。`,
      insightSource: `洞察来源：${chart.semantic_model} 语义模型`,
      evidence: `分析工作台 → ${chart.title} → ${chart.dimensions.join(' → ')}`,
    };
  }

  export const drilldownRegistry = Object.freeze({
    version: 1,
    charts: {
      revenue: {
        metric: '销售收入', semanticModel: 'sales_model', sourceType: 'revenue',
        dimensions: ['区域', '产品线', '渠道'], pageTitle: '销售收入下钻分析',
        sourceTitle: '季度销售收入趋势', breadcrumb: '销售总览 › 2024 Q4 › 华东 › 产品线',
        connectorOne: '点击 2024 Q4，下钻维度：区域', levelOneLabel: '第 1 层 · 区域',
        levelOneTitle: '2024 Q4 各区域销售收入',
        bars: [['华东', 88, '420'], ['华南', 60, '280'], ['华北', 40, '190'], ['西南', 28, '130']],
        connectorTwo: '点击 华东，下钻维度：产品线', levelTwoLabel: '第 2 层 · 产品线',
        levelTwoTitle: '华东产品线贡献', tableDimension: '产品线', total: '420',
        rows: [['消费电子', '176', '42%'], ['家电', '126', '30%'], ['数码配件', '76', '18%'], ['其他', '42', '10%']],
        context: '第 2 层 · 华东产品线贡献', questions: ['华东增长来自哪里？', '哪个产品线拖累毛利？'],
        insight: '消费电子贡献 42%，但毛利率环比下降 3.2 个百分点。', insightSource: '洞察来源：与 2024 Q3 环比对比',
        evidence: '销售总览 → 2024 Q4 → 华东 → 产品线 → 渠道',
      },
      structure: {
        metric: '销售收入', semanticModel: 'sales_model', sourceType: 'structure',
        dimensions: ['区域', '渠道'], pageTitle: '销售结构下钻分析', sourceTitle: '销售结构（按产品大类）',
        breadcrumb: '销售总览 › 销售结构 › 消费电子 › 华东 › 渠道',
        connectorOne: '点击 消费电子，下钻维度：区域', levelOneLabel: '第 1 层 · 区域',
        levelOneTitle: '消费电子各区域销售收入',
        bars: [['华东', 88, '76'], ['华南', 63, '54'], ['华北', 41, '31'], ['西南', 24, '15']],
        connectorTwo: '点击 华东，下钻维度：渠道', levelTwoLabel: '第 2 层 · 渠道',
        levelTwoTitle: '华东消费电子渠道贡献', tableDimension: '渠道', total: '76',
        rows: [['直营门店', '31', '41%'], ['电商平台', '25', '33%'], ['经销商', '14', '18%'], ['其他', '6', '8%']],
        context: '第 2 层 · 华东消费电子渠道贡献', questions: ['消费电子主要由哪个区域贡献？', '哪个渠道增长最快？'],
        insight: '华东贡献消费电子收入的 43%，其中直营门店占华东渠道收入的 41%。',
        insightSource: '洞察来源：销售结构与渠道贡献交叉分析',
        evidence: '销售总览 → 销售结构 → 消费电子 → 华东 → 渠道',
      },
    },
  } satisfies DrillRegistry);
}
