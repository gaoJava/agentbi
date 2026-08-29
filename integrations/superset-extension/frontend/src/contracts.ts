export type FilterOperator = 'EQ' | 'IN' | 'GTE' | 'LTE';

export interface ScreenFilter {
  field: string;
  operator: FilterOperator;
  value: string | number | Array<string | number>;
}

export interface ScreenContext {
  dashboard_id: string;
  chart_id?: string;
  dataset_id?: string;
  semantic_model_id: number;
  time_range: string;
  filters: ScreenFilter[];
  selected?: { label: string; value?: string | number; dimension?: string };
}

export interface Evidence {
  query_id: string;
  semantic_model_id: number;
  time_range: string;
  row_count: number;
  query_time_ms?: number;
  sql_fingerprint?: string;
}

export interface AnalyzeResult {
  request_id: string;
  chat_id?: number;
  answer: string;
  data: Array<Record<string, unknown>>;
  evidence: Evidence;
  report: {
    title: string;
    source_url?: string;
    summary: string;
    observations: string[];
    suggested_actions: string[];
    markdown: string;
  };
  steps: Array<{
    name: string;
    status: 'completed' | 'failed';
    duration_ms: number;
    detail?: string;
  }>;
  warnings: string[];
}

export const CONTEXT_EVENT = 'agentbi:screen-context';
export const CONTEXT_REQUEST_EVENT = 'agentbi:screen-context-request';
export const CONTEXT_SETTINGS_EVENT = 'agentbi:context-settings';
export const CHART_SELECTED_MESSAGE = 'agentbi:chart-selected';
