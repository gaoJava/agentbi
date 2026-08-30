import type { ScreenContext, ScreenFilter } from './contracts';
import {
  CHART_SELECTED_MESSAGE,
  CONTEXT_EVENT,
  CONTEXT_REQUEST_EVENT,
  CONTEXT_SETTINGS_EVENT,
} from './contracts';

const DASHBOARD_PATH = /\/superset\/dashboard\/([^/?#]+)/;
const DEFAULT_TIME_RANGE = '最近7天';
const DEFAULT_SEMANTIC_MODEL_ID = 1;
const ANALYZE_BUTTON_CLASS = 'agentbi-chart-analyze';
const ANALYZE_STYLE_ID = 'agentbi-chart-analyze-style';

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

function chartContainer(target: EventTarget | null): HTMLElement | null {
  if (!(target instanceof Element)) return null;
  return target.closest<HTMLElement>(
    '[data-chart-id],[data-test-chart-id],.dashboard-component-chart-holder',
  );
}

function attributeWithin(element: Element, names: string[]): string | undefined {
  for (const name of names) {
    const direct = element.getAttribute(name);
    if (direct) return direct.slice(0, 128);
    const child = element.querySelector(`[${name}]`);
    const nested = child?.getAttribute(name);
    if (nested) return nested.slice(0, 128);
  }
  return undefined;
}

function chartIdWithin(element: Element): string | undefined {
  const attributeId = attributeWithin(element, ['data-chart-id', 'data-test-chart-id']);
  if (attributeId) return attributeId;
  // Superset 6.x exposes the slice id on ChartHolder as
  // `dashboard-chart-id-<id>` rather than a data attribute. Browse mode may
  // also omit the Explore link, so this class is the most reliable source.
  const classId = Array.from(element.classList)
    .map(className => className.match(/^dashboard-chart-id-(\d+)$/)?.[1])
    .find(Boolean);
  if (classId) return positiveInteger(classId)?.toString();
  const href = element.querySelector<HTMLAnchorElement>('a[href*="slice_id="]')?.href;
  if (!href) return undefined;
  try {
    return positiveInteger(new URL(href, window.location.href).searchParams.get('slice_id'))?.toString();
  } catch {
    return undefined;
  }
}

function chartTitle(element: Element, chartId: string): string {
  const title = element.querySelector<HTMLElement>(
    '[data-test="chart-title"],.chart-header .header-title,.slice-header',
  )?.innerText.trim();
  return (title || `图表 ${chartId}`).slice(0, 200);
}

function parentOrigin(): string | undefined {
  if (window.parent === window || !document.referrer) return undefined;
  try {
    const origin = new URL(document.referrer).origin;
    return /^https?:\/\//.test(origin) ? origin : undefined;
  } catch {
    return undefined;
  }
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
  const installAnalyzeButton = (container: HTMLElement) => {
    if (container.querySelector(`:scope > .${ANALYZE_BUTTON_CLASS}`)) return;
    const hoveredChartId = chartIdWithin(container);
    if (!hoveredChartId) return;
    const analyzeButton = document.createElement('button');
    analyzeButton.type = 'button';
    analyzeButton.className = ANALYZE_BUTTON_CLASS;
    analyzeButton.textContent = '✦ AI 分析此图';
    analyzeButton.setAttribute('aria-label', `使用 AI 分析${chartTitle(container, hoveredChartId)}`);
    Object.assign(analyzeButton.style, {
      position: 'absolute', top: '10px', right: '42px', zIndex: '100', border: '1px solid #9cc0ff',
      borderRadius: '16px', padding: '6px 11px', color: '#155eef', background: '#ffffffee',
      boxShadow: '0 4px 14px rgba(21,94,239,.18)', cursor: 'pointer', fontWeight: '700',
      opacity: '0', pointerEvents: 'none', transition: 'opacity 120ms ease',
    });
    if (window.getComputedStyle(container).position === 'static') container.style.position = 'relative';
    analyzeButton.addEventListener('click', buttonEvent => {
      buttonEvent.preventDefault();
      buttonEvent.stopPropagation();
      chartId = hoveredChartId;
      datasetId = attributeWithin(container, ['data-dataset-id']) ?? datasetId;
      publish(true);
      const dashboardId = dashboardIdFromLocation();
      const targetOrigin = parentOrigin();
      if (!dashboardId || !targetOrigin) return;
      const settings = readSettings(dashboardId);
      window.parent.postMessage({
        type: CHART_SELECTED_MESSAGE,
        version: 1,
        title: chartTitle(container, hoveredChartId),
        context: {
          dashboard_id: dashboardId,
          chart_id: hoveredChartId,
          dataset_id: datasetId,
          semantic_model_id: settings.semanticModelId,
          time_range: settings.timeRange,
          filters: filtersFromLocation(),
        },
      }, targetOrigin);
    });
    container.appendChild(analyzeButton);
  };
  const scanChartContainers = () => {
    document.querySelectorAll<HTMLElement>(
      '[data-chart-id],[data-test-chart-id],[data-test="dashboard-component-chart-holder"],.dashboard-component-chart-holder',
    ).forEach(installAnalyzeButton);
  };
  const onChartHover = (event: MouseEvent) => {
    const container = chartContainer(event.target);
    if (container) installAnalyzeButton(container);
  };
  const onSettings = () => {
    lastFingerprint = '';
    publish();
  };
  const onContextRequest = () => publish(true);
  const onPopState = () => publish();

  document.addEventListener('click', onClick, true);
  document.addEventListener('mouseover', onChartHover, true);
  if (!document.getElementById(ANALYZE_STYLE_ID)) {
    const style = document.createElement('style');
    style.id = ANALYZE_STYLE_ID;
    style.textContent = `
      [data-test="dashboard-component-chart-holder"]:hover > .${ANALYZE_BUTTON_CLASS},
      .dashboard-component-chart-holder:hover > .${ANALYZE_BUTTON_CLASS},
      [data-chart-id]:hover > .${ANALYZE_BUTTON_CLASS},
      [data-test-chart-id]:hover > .${ANALYZE_BUTTON_CLASS} {
        opacity: 1 !important;
        pointer-events: auto !important;
      }
    `;
    document.head.appendChild(style);
  }
  window.addEventListener('popstate', onPopState);
  window.addEventListener(CONTEXT_REQUEST_EVENT, onContextRequest);
  window.addEventListener(CONTEXT_SETTINGS_EVENT, onSettings);
  const interval = window.setInterval(publish, 1000);
  const chartScanInterval = window.setInterval(scanChartContainers, 750);
  window.setTimeout(publish, 0);
  window.setTimeout(publish, 750);
  window.setTimeout(scanChartContainers, 0);
  window.setTimeout(scanChartContainers, 750);

  return () => {
    document.removeEventListener('click', onClick, true);
    document.removeEventListener('mouseover', onChartHover, true);
    document.querySelectorAll(`.${ANALYZE_BUTTON_CLASS}`).forEach(button => button.remove());
    document.getElementById(ANALYZE_STYLE_ID)?.remove();
    window.removeEventListener('popstate', onPopState);
    window.removeEventListener(CONTEXT_REQUEST_EVENT, onContextRequest);
    window.removeEventListener(CONTEXT_SETTINGS_EVENT, onSettings);
    window.clearInterval(interval);
    window.clearInterval(chartScanInterval);
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
