/** Strict browser contracts shared by the AgentBI workbench features. */
namespace AgentBI {
  export type Role = 'admin' | 'user' | string;
  export type SourceType = 'revenue' | 'structure' | 'table';

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

  export interface ApiErrorBody { detail?: string }

  export async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
    const response = await fetch(url, { credentials: 'same-origin', ...options });
    const body: unknown = response.status === 204
      ? null
      : await response.json().catch((): null => null);
    if (!response.ok) {
      const detail = typeof body === 'object' && body !== null && 'detail' in body
        ? String((body as ApiErrorBody).detail || '')
        : '';
      throw new Error(detail || '请求失败，请稍后重试');
    }
    return body as T;
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
