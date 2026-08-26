import type { ScreenContext, ScreenFilter } from './contracts';
import {
  CONTEXT_EVENT,
  CONTEXT_REQUEST_EVENT,
  CONTEXT_SETTINGS_EVENT,
} from './contracts';

const DASHBOARD_PATH = /\/superset\/dashboard\/([^/?#]+)/;
const DEFAULT_TIME_RANGE = '最近7天';
const DEFAULT_SEMANTIC_MODEL_ID = 1;

interface ContextSettings {
  semanticModelId: number;
  timeRange: string;
}

interface DashboardStateSnapshot {
  dashboardId: string | number;
  semanticModelId: number;
  chartId?: string | number;
  datasetId?: string | number;
  timeRange: string;
  filters: ScreenFilter[];
  selected?: ScreenContext['selected'];
}

function positiveInteger(value: string | null): number | undefined {
  if (!value || !/^\d+$/.test(value)) return undefined;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : undefined;
}

function dashboardIdFromLocation(): string | undefined {
  const match = window.location.pathname.match(DASHBOARD_PATH);
  return match ? decodeURIComponent(match[1]) : undefined;
}

function storageKey(dashboardId: string, field: string): string {
  return `agentbi.dashboard.${dashboardId}.${field}`;
}

function readSettings(dashboardId: string): ContextSettings {
  const query = new URLSearchParams(window.location.search);
  const semanticModelId =
    positiveInteger(query.get('agentbi_semantic_model_id')) ??
    positiveInteger(window.localStorage.getItem(storageKey(dashboardId, 'semanticModelId'))) ??
    DEFAULT_SEMANTIC_MODEL_ID;
  const timeRange =
    query.get('agentbi_time_range')?.trim() ||
    window.localStorage.getItem(storageKey(dashboardId, 'timeRange'))?.trim() ||
    DEFAULT_TIME_RANGE;
  return { semanticModelId, timeRange: timeRange.slice(0, 256) };
}

function filtersFromLocation(): ScreenFilter[] {
  const filters: ScreenFilter[] = [];
  const query = new URLSearchParams(window.location.search);
  query.forEach((value, key) => {
    if (!key.startsWith('agentbi_filter_') || filters.length >= 50) return;
    const field = key.slice('agentbi_filter_'.length).trim();
    if (field) filters.push({ field: field.slice(0, 128), operator: 'EQ', value });
  });
  return filters;
}

function closestAttribute(target: EventTarget | null, names: string[]): string | undefined {
  if (!(target instanceof Element)) return undefined;
  let element: Element | null = target;
  while (element) {
    for (const name of names) {
      const value = element.getAttribute(name);
      if (value) return value.slice(0, 128);
    }
    element = element.parentElement;
  }
  return undefined;
}

/** Publish only identifiers and visible filters; raw chart rows never cross this boundary. */
export function publishScreenContext(snapshot: DashboardStateSnapshot): void {
  const detail: ScreenContext = {
    dashboard_id: String(snapshot.dashboardId),
    chart_id: snapshot.chartId === undefined ? undefined : String(snapshot.chartId),
    dataset_id: snapshot.datasetId === undefined ? undefined : String(snapshot.datasetId),
    semantic_model_id: snapshot.semanticModelId,
    time_range: snapshot.timeRange,
    filters: snapshot.filters.slice(0, 50),
    selected: snapshot.selected,
  };
  window.dispatchEvent(new CustomEvent<ScreenContext>(CONTEXT_EVENT, { detail }));
}

/**
 * Install a zero-trust fallback bridge for extension-only deployments. A deeper host adapter can
 * publish the same event from Redux selectors without changing the panel implementation.
 */
export function installSupersetContextBridge(): () => void {
  let chartId: string | undefined;
  let datasetId: string | undefined;
  let lastFingerprint = '';

  const publish = (force = false) => {
    const dashboardId = dashboardIdFromLocation();
    if (!dashboardId) return;
    const settings = readSettings(dashboardId);
    const snapshot: DashboardStateSnapshot = {
      dashboardId,
      chartId,
      datasetId,
      semanticModelId: settings.semanticModelId,
      timeRange: settings.timeRange,
      filters: filtersFromLocation(),
    };
    const fingerprint = JSON.stringify(snapshot);
    if (force || fingerprint !== lastFingerprint) {
      lastFingerprint = fingerprint;
      publishScreenContext(snapshot);
    }
  };

  const onClick = (event: MouseEvent) => {
    chartId = closestAttribute(event.target, ['data-chart-id', 'data-test-chart-id']) ?? chartId;
    datasetId = closestAttribute(event.target, ['data-dataset-id']) ?? datasetId;
    publish();
  };
  const onSettings = () => {
    lastFingerprint = '';
    publish();
  };
  const onContextRequest = () => publish(true);
  const onPopState = () => publish();

  document.addEventListener('click', onClick, true);
  window.addEventListener('popstate', onPopState);
  window.addEventListener(CONTEXT_REQUEST_EVENT, onContextRequest);
  window.addEventListener(CONTEXT_SETTINGS_EVENT, onSettings);
  const interval = window.setInterval(publish, 1000);
  window.setTimeout(publish, 0);
  window.setTimeout(publish, 750);

  return () => {
    document.removeEventListener('click', onClick, true);
    window.removeEventListener('popstate', onPopState);
    window.removeEventListener(CONTEXT_REQUEST_EVENT, onContextRequest);
    window.removeEventListener(CONTEXT_SETTINGS_EVENT, onSettings);
    window.clearInterval(interval);
  };
}

export function saveContextSettings(
  dashboardId: string,
  semanticModelId: number,
  timeRange: string,
): void {
  window.localStorage.setItem(storageKey(dashboardId, 'semanticModelId'), String(semanticModelId));
  window.localStorage.setItem(storageKey(dashboardId, 'timeRange'), timeRange.trim());
  window.dispatchEvent(new Event(CONTEXT_SETTINGS_EVENT));
}
