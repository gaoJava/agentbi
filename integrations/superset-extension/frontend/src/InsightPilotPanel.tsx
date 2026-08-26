import React, { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import type { AnalyzeResult, ScreenContext } from './contracts';
import { CONTEXT_EVENT, CONTEXT_REQUEST_EVENT } from './contracts';
import { saveContextSettings } from './supersetContextBridge';

const endpoint = '/extensions/agentbi/insight-pilot/analyze';
const quickQuestions = ['alice 停留时长', '访问次数最高的部门', '最近7天访问人数趋势'];
let csrfTokenPromise: Promise<string> | undefined;

async function csrfToken(): Promise<string> {
  csrfTokenPromise ??= fetch('/api/v1/security/csrf_token/', { credentials: 'same-origin' })
    .then(async response => {
      const body = (await response.json()) as { result?: string };
      if (!response.ok || !body.result) throw new Error('无法获取安全令牌');
      return body.result;
    })
    .catch(reason => {
      csrfTokenPromise = undefined;
      throw reason;
    });
  return csrfTokenPromise;
}

function shortValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value).slice(0, 80);
  return String(value).slice(0, 80);
}

function initialViewport(): { width: number; height: number } {
  const local = ['127.0.0.1', 'localhost'].includes(window.location.hostname);
  const preview = Number(new URLSearchParams(window.location.search).get('agentbi_preview_width'));
  // Local-only preview enables deterministic responsive checks when the host browser
  // cannot emulate a device viewport. It never changes data or request behavior.
  const width = local && preview >= 320 && preview <= 600 ? preview : window.innerWidth;
  return { width, height: window.innerHeight };
}

export default function InsightPilotPanel() {
  const [onDashboard, setOnDashboard] = useState(() =>
    window.location.pathname.includes('/superset/dashboard/'),
  );
  const [viewport, setViewport] = useState(initialViewport);
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState('alice 停留时长');
  const [context, setContext] = useState<ScreenContext>();
  const [result, setResult] = useState<AnalyzeResult>();
  const [chatId, setChatId] = useState<number>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [semanticModelId, setSemanticModelId] = useState(1);
  const [timeRange, setTimeRange] = useState('最近7天');
  const requestIdentity = useRef<{ fingerprint: string; id: string }>();

  useEffect(() => {
    // Superset loads extensions once, before subsequent client-side navigation.
    // Polling keeps this lightweight global contribution route-aware even though
    // React Router navigation does not emit a native popstate event.
    const detectRoute = () => {
      const next = window.location.pathname.includes('/superset/dashboard/');
      setOnDashboard(next);
      if (!next) {
        setOpen(false);
        setContext(undefined);
      }
    };
    window.addEventListener('popstate', detectRoute);
    const timer = window.setInterval(detectRoute, 500);
    return () => {
      window.removeEventListener('popstate', detectRoute);
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const resize = () => setViewport({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);

  useEffect(() => {
    const receive = (event: Event) => {
      const next = (event as CustomEvent<ScreenContext>).detail;
      setContext(next);
      setSemanticModelId(next.semantic_model_id);
      setTimeRange(next.time_range);
    };
    window.addEventListener(CONTEXT_EVENT, receive);
    // The bridge may have published before React registered this listener.
    // Request a fresh snapshot so the initial dashboard context cannot be lost.
    window.dispatchEvent(new Event(CONTEXT_REQUEST_EVENT));
    return () => window.removeEventListener(CONTEXT_EVENT, receive);
  }, []);

  const columns = useMemo(
    () => (result?.data[0] ? Object.keys(result.data[0]).slice(0, 6) : []),
    [result],
  );
  const compact = viewport.width <= 600;

  if (!onDashboard) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!context) {
      setError('尚未识别仪表盘上下文，请确认当前位于 Superset 仪表盘页面。');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const fingerprint = JSON.stringify({ question, context, chatId });
      if (requestIdentity.current?.fingerprint !== fingerprint) {
        requestIdentity.current = { fingerprint, id: crypto.randomUUID() };
      }
      const token = await csrfToken();
      const response = await fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
        body: JSON.stringify({
          question,
          context,
          client_request_id: requestIdentity.current.id,
          ...(chatId ? { chat_id: chatId } : {}),
        }),
      });
      const body = (await response.json()) as { result?: AnalyzeResult; message?: string };
      if (!response.ok || !body.result) throw new Error(body.message || '分析请求失败');
      setResult(body.result);
      setChatId(body.result.chat_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '分析请求失败');
    } finally {
      setLoading(false);
    }
  }

  function saveSettings(): void {
    if (!context || semanticModelId <= 0 || !timeRange.trim()) return;
    saveContextSettings(context.dashboard_id, semanticModelId, timeRange);
    setSettingsOpen(false);
  }

  function startNewConversation(): void {
    setChatId(undefined);
    setResult(undefined);
    setError('');
    setQuestion('alice 停留时长');
    requestIdentity.current = undefined;
  }

  function downloadReport(): void {
    if (!result) return;
    const blob = new Blob([result.report.markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `agentbi-report-${result.request_id.slice(0, 8)}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function prepareDrilldown(): void {
    if (!context || !result?.data[0]) return;
    const row = result.data[0];
    const dimension = Object.keys(row).find(key => row[key] !== null && row[key] !== undefined);
    if (!dimension) return;
    const value = shortValue(row[dimension]);
    setContext({ ...context, selected: { label: dimension, dimension, value } });
    setQuestion(`请围绕 ${dimension}=${value} 下钻分析，并说明主要差异`);
  }

  return (
    <div style={{ ...shellStyle, right: compact ? 8 : 24, bottom: compact ? 8 : 24 }}>
      {!open ? (
        <button type="button" onClick={() => setOpen(true)} style={launcherStyle}>
          <span aria-hidden="true">✦</span> AI 洞察
          <span style={launcherBadgeStyle}>{context ? '已连接' : '等待大屏'}</span>
        </button>
      ) : (
        <aside
          aria-label="InsightPilot AgentBI"
          style={{
            ...panelStyle,
            width: compact ? Math.max(0, viewport.width - 16) : 440,
            maxHeight: compact ? Math.max(0, viewport.height - 16) : 'calc(100vh - 48px)',
          }}
        >
          <header style={{ ...headerStyle, padding: compact ? '12px 14px' : '16px 18px' }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <strong style={{ fontSize: 17 }}>✦ InsightPilot</strong>
                <span style={secureBadgeStyle}>安全代理</span>
              </div>
              <div style={{ fontSize: 12, marginTop: 4, opacity: 0.84 }}>可验证的经营分析 Agent</div>
            </div>
            <button type="button" aria-label="关闭" onClick={() => setOpen(false)} style={iconButtonStyle}>×</button>
          </header>

          <div style={{ ...contentStyle, padding: compact ? 12 : 16 }}>
            <section style={contextCardStyle}>
              <div style={sectionTitleStyle}>
                <span>屏幕上下文</span>
                <button type="button" onClick={() => setSettingsOpen(value => !value)} style={linkButtonStyle}>设置</button>
              </div>
              {context ? (
                <div style={chipRowStyle}>
                  <span style={chipStyle}>大屏 {context.dashboard_id}</span>
                  <span style={chipStyle}>模型 {context.semantic_model_id}</span>
                  <span style={chipStyle}>{context.time_range}</span>
                  <span style={chipStyle}>{context.filters.length} 个筛选</span>
                  {chatId && <span style={conversationChipStyle}>会话 #{chatId}</span>}
                </div>
              ) : <div style={mutedStyle}>正在识别当前 Superset 仪表盘…</div>}
              {settingsOpen && context && (
                <div style={settingsStyle}>
                  <label style={labelStyle}>SuperSonic 语义模型 ID
                    <input type="number" min={1} value={semanticModelId} onChange={event => setSemanticModelId(Number(event.target.value))} style={inputStyle} />
                  </label>
                  <label style={labelStyle}>分析时间范围
                    <input value={timeRange} maxLength={256} onChange={event => setTimeRange(event.target.value)} style={inputStyle} />
                  </label>
                  <button type="button" onClick={saveSettings} style={smallPrimaryButtonStyle}>保存映射</button>
                </div>
              )}
            </section>

            <div style={quickRowStyle}>
              {quickQuestions.map(item => (
                <button key={item} type="button" onClick={() => setQuestion(item)} style={quickButtonStyle}>{item}</button>
              ))}
            </div>

            <form onSubmit={submit}>
              <textarea aria-label="分析问题" value={question} onChange={event => setQuestion(event.target.value)} maxLength={2000} style={textareaStyle} />
              <button type="submit" disabled={loading || !context || question.trim().length < 2} style={{ ...submitStyle, opacity: loading || !context ? 0.55 : 1 }}>
                {loading ? '正在执行语义分析…' : result ? '继续追问' : '开始分析'}
              </button>
            </form>

            {chatId && (
              <div style={conversationBarStyle}>
                <span>后续问题将沿用会话 #{chatId} 的语义上下文</span>
                <button type="button" onClick={startNewConversation} style={linkButtonStyle}>新对话</button>
              </div>
            )}

            {error && <p role="alert" style={errorStyle}>{error}</p>}
            {result && (
              <section aria-live="polite" style={resultStyle}>
                <div style={sectionTitleStyle}><span>分析结论</span><span style={requestIdStyle}>#{result.request_id.slice(0, 8)}</span></div>
                <p style={{ margin: '8px 0 14px', lineHeight: 1.65 }}>{result.answer}</p>

                <div style={insightGridStyle}>
                  <div style={insightCardStyle}>
                    <strong>关键观察</strong>
                    {result.report.observations.map(item => <div key={item} style={insightItemStyle}>• {item}</div>)}
                  </div>
                  <div style={insightCardStyle}>
                    <strong>建议动作</strong>
                    {result.report.suggested_actions.map(item => <div key={item} style={insightItemStyle}>• {item}</div>)}
                  </div>
                </div>

                {result.data.length > 0 && (
                  <button type="button" onClick={prepareDrilldown} style={drillButtonStyle}>基于首行结果继续下钻</button>
                )}

                <div style={stepListStyle}>
                  {result.steps.map(step => (
                    <div key={step.name} style={stepStyle}>
                      <span style={{ color: step.status === 'completed' ? '#12b76a' : '#d92d20' }}>{step.status === 'completed' ? '●' : '×'}</span>
                      <span style={{ flex: 1 }}>{step.name}</span><span style={mutedStyle}>{step.duration_ms}ms</span>
                    </div>
                  ))}
                </div>

                {columns.length > 0 && (
                  <div style={{ overflowX: 'auto', marginTop: 14 }}>
                    <table style={tableStyle}>
                      <thead><tr>{columns.map(column => <th key={column} style={thStyle}>{column}</th>)}</tr></thead>
                      <tbody>{result.data.slice(0, 5).map((row, index) => (
                        <tr key={`${result.request_id}-${index}`}>{columns.map(column => <td key={column} style={tdStyle}>{shortValue(row[column])}</td>)}</tr>
                      ))}</tbody>
                    </table>
                  </div>
                )}

                {result.warnings.map(warning => <div key={warning} style={warningStyle}>⚠ {warning}</div>)}
                <button type="button" onClick={downloadReport} style={reportButtonStyle}>下载可审计 Markdown 报告</button>
                {result.report.source_url && (
                  <a href={result.report.source_url} style={sourceLinkStyle}>返回报告来源大屏</a>
                )}
                <details style={evidenceStyle}>
                  <summary style={{ cursor: 'pointer', fontWeight: 600 }}>数据证据与审计信息</summary>
                  <dl style={evidenceGridStyle}>
                    <dt>查询编号</dt><dd>{result.evidence.query_id}</dd>
                    <dt>返回行数</dt><dd>{result.evidence.row_count}</dd>
                    <dt>查询耗时</dt><dd>{result.evidence.query_time_ms ?? '—'} ms</dd>
                    <dt>SQL 指纹</dt><dd>{result.evidence.sql_fingerprint || '—'}</dd>
                  </dl>
                </details>
              </section>
            )}
          </div>
        </aside>
      )}
    </div>
  );
}

const shellStyle: React.CSSProperties = { position: 'fixed', right: 24, bottom: 24, zIndex: 1100, fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif' };
const launcherStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 8, border: 0, borderRadius: 24, background: '#155eef', color: '#fff', padding: '10px 12px 10px 16px', boxShadow: '0 10px 30px rgba(21,94,239,.3)', cursor: 'pointer', fontWeight: 700 };
const launcherBadgeStyle: React.CSSProperties = { borderRadius: 12, background: 'rgba(255,255,255,.18)', padding: '3px 8px', fontSize: 11, fontWeight: 500 };
const panelStyle: React.CSSProperties = { width: 440, maxWidth: 'calc(100vw - 32px)', maxHeight: 'calc(100vh - 48px)', overflow: 'hidden', background: '#fff', border: '1px solid #e4e7ec', borderRadius: 16, boxShadow: '0 20px 60px rgba(16,24,40,.24)', color: '#182230' };
const headerStyle: React.CSSProperties = { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 18px', color: '#fff', background: 'linear-gradient(135deg, #155eef, #6938ef)' };
const secureBadgeStyle: React.CSSProperties = { fontSize: 10, border: '1px solid rgba(255,255,255,.42)', borderRadius: 10, padding: '2px 7px', fontWeight: 500 };
const iconButtonStyle: React.CSSProperties = { border: 0, background: 'transparent', color: '#fff', fontSize: 26, cursor: 'pointer', lineHeight: 1 };
const contentStyle: React.CSSProperties = { padding: 16, maxHeight: 'calc(100vh - 128px)', overflowY: 'auto' };
const contextCardStyle: React.CSSProperties = { background: '#f8f9fc', border: '1px solid #eaecf0', borderRadius: 10, padding: 12 };
const sectionTitleStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontWeight: 700 };
const chipRowStyle: React.CSSProperties = { display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 };
const chipStyle: React.CSSProperties = { background: '#eef4ff', color: '#3538cd', borderRadius: 12, padding: '4px 8px', fontSize: 11 };
const conversationChipStyle: React.CSSProperties = { ...chipStyle, background: '#ecfdf3', color: '#027a48' };
const mutedStyle: React.CSSProperties = { color: '#667085', fontSize: 12 };
const linkButtonStyle: React.CSSProperties = { border: 0, background: 'transparent', color: '#155eef', cursor: 'pointer', padding: 0, fontSize: 12 };
const settingsStyle: React.CSSProperties = { display: 'grid', gap: 9, marginTop: 12, paddingTop: 12, borderTop: '1px solid #e4e7ec' };
const labelStyle: React.CSSProperties = { display: 'grid', gap: 4, color: '#475467', fontSize: 11 };
const inputStyle: React.CSSProperties = { border: '1px solid #d0d5dd', borderRadius: 6, padding: '7px 8px', color: '#101828', background: '#fff' };
const smallPrimaryButtonStyle: React.CSSProperties = { justifySelf: 'start', border: 0, borderRadius: 6, padding: '7px 12px', background: '#155eef', color: '#fff', cursor: 'pointer' };
const quickRowStyle: React.CSSProperties = { display: 'flex', gap: 6, overflowX: 'auto', padding: '12px 0 8px' };
const quickButtonStyle: React.CSSProperties = { flex: '0 0 auto', border: '1px solid #d0d5dd', borderRadius: 14, background: '#fff', padding: '5px 9px', color: '#475467', cursor: 'pointer', fontSize: 11 };
const textareaStyle: React.CSSProperties = { boxSizing: 'border-box', width: '100%', minHeight: 82, resize: 'vertical', border: '1px solid #b2ccff', borderRadius: 9, padding: 10, outlineColor: '#155eef', font: 'inherit' };
const submitStyle: React.CSSProperties = { width: '100%', marginTop: 8, border: 0, borderRadius: 8, padding: 10, background: '#155eef', color: '#fff', cursor: 'pointer', fontWeight: 700 };
const errorStyle: React.CSSProperties = { color: '#b42318', background: '#fef3f2', borderRadius: 8, padding: 10, fontSize: 12 };
const conversationBarStyle: React.CSSProperties = { display: 'flex', justifyContent: 'space-between', gap: 10, marginTop: 8, color: '#475467', fontSize: 11 };
const resultStyle: React.CSSProperties = { marginTop: 16, borderTop: '1px solid #eaecf0', paddingTop: 14 };
const requestIdStyle: React.CSSProperties = { color: '#667085', fontSize: 10, fontWeight: 500 };
const stepListStyle: React.CSSProperties = { display: 'grid', gap: 5, background: '#f9fafb', borderRadius: 8, padding: 10 };
const stepStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 };
const insightGridStyle: React.CSSProperties = { display: 'grid', gap: 8, marginBottom: 12 };
const insightCardStyle: React.CSSProperties = { border: '1px solid #e4e7ec', borderRadius: 8, padding: 10, background: '#fff' };
const insightItemStyle: React.CSSProperties = { marginTop: 6, color: '#475467', fontSize: 12, lineHeight: 1.5 };
const tableStyle: React.CSSProperties = { width: '100%', borderCollapse: 'collapse', fontSize: 11 };
const thStyle: React.CSSProperties = { textAlign: 'left', color: '#475467', background: '#f2f4f7', padding: 7, borderBottom: '1px solid #d0d5dd', whiteSpace: 'nowrap' };
const tdStyle: React.CSSProperties = { padding: 7, borderBottom: '1px solid #eaecf0', whiteSpace: 'nowrap' };
const warningStyle: React.CSSProperties = { marginTop: 8, color: '#b54708', background: '#fffaeb', borderRadius: 6, padding: 8, fontSize: 11 };
const reportButtonStyle: React.CSSProperties = { width: '100%', marginTop: 12, border: '1px solid #b2ccff', borderRadius: 8, padding: 9, background: '#eef4ff', color: '#3538cd', cursor: 'pointer', fontWeight: 700 };
const drillButtonStyle: React.CSSProperties = { width: '100%', marginBottom: 10, border: '1px solid #d0d5dd', borderRadius: 8, padding: 9, background: '#fff', color: '#344054', cursor: 'pointer', fontWeight: 700 };
const sourceLinkStyle: React.CSSProperties = { display: 'block', marginTop: 8, color: '#155eef', textAlign: 'center', fontSize: 11 };
const evidenceStyle: React.CSSProperties = { marginTop: 12, border: '1px solid #e4e7ec', borderRadius: 8, padding: 10, fontSize: 12 };
const evidenceGridStyle: React.CSSProperties = { display: 'grid', gridTemplateColumns: '90px 1fr', gap: '6px 10px', marginBottom: 0, wordBreak: 'break-all' };
