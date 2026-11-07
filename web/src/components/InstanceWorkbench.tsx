import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiMessage, getWorkbench, workbenchAction, type GpuParameter, type Workbench } from '../lib/gpu-api';
import type { GpuInstance } from './AccountDirectory';
import styles from './v020.module.css';

export interface ReviewedWorkflow {
  workflow_key: string;
  workflow_version: string | number;
  model_id?: string;
  description?: string;
  mock?: boolean;
  parameters?: GpuParameter[];
  draft?: { values?: Record<string, unknown> };
  [key: string]: unknown;
}
export interface WorkbenchArtifact { id?: string; name?: string; filename?: string; mime?: string; thumbnail_url?: string; status?: string; [key: string]: unknown; }
export interface WorkbenchLog { id?: string; message?: string; created_at?: string; [key: string]: unknown; }
export interface InstanceWorkbenchState extends Workbench {
  allowed_workflows?: ReviewedWorkflow[];
  read_only?: boolean;
  comfy?: { available?: boolean; verified?: boolean };
  artifacts?: WorkbenchArtifact[];
  logs?: Array<WorkbenchLog | string>;
  parameters?: GpuParameter[];
  values?: Record<string, unknown>;
  graph?: unknown;
  [key: string]: unknown;
}
export interface WorkbenchApi {
  getInstanceWorkbench: (instanceId: string) => Promise<InstanceWorkbenchState>;
  workbenchAction: (instanceId: string, action: string, params: Record<string, unknown>, idempotencyKey: string) => Promise<unknown>;
}
export interface InstanceWorkbenchProps { instance: GpuInstance; api?: Partial<WorkbenchApi>; onBack?: () => void; onQueued?: (result: unknown) => void; }

let keySequence = 0;
function newKey(prefix: string) { keySequence += 1; return `${prefix}-${Date.now().toString(36)}-${keySequence.toString(36)}`; }
function instanceId(instance: GpuInstance) { return instance.id || instance.instance_id || ''; }
function recordPayload(value: unknown): InstanceWorkbenchState { return (value && typeof value === 'object' && 'data' in value ? (value as { data: unknown }).data : value || {}) as InstanceWorkbenchState; }
function workflowIdentity(workflow: ReviewedWorkflow) { return `${workflow.workflow_key}@${workflow.workflow_version}`; }
function historyOnly(instance: GpuInstance, state?: InstanceWorkbenchState) { return state?.read_only === true || ['unknown', 'deleted', 'releasing', 'cleanup_pending', 'quarantined'].includes(String(instance.state || '').toLowerCase()) || (instance.provider === 'colab' && instance.verified !== true && instance.runtime_verified !== true); }
function safeAssetUrl(url?: string): string | null { if (!url) return null; if (url.startsWith('/') && !url.startsWith('//')) return url; if (typeof window === 'undefined') return null; try { const parsed = new URL(url, window.location.origin); return parsed.origin === window.location.origin ? parsed.pathname + parsed.search : null; } catch { return null; } }

const defaultWorkbenchApi: WorkbenchApi = {
  getInstanceWorkbench: async (id) => getWorkbench(id) as Promise<InstanceWorkbenchState>,
  workbenchAction: (id, action, params, key) => workbenchAction(id, action as Parameters<typeof workbenchAction>[1], params, key),
};

export function InstanceWorkbench({ instance, api, onBack, onQueued }: InstanceWorkbenchProps) {
  const workbenchApi = useMemo(() => ({ ...defaultWorkbenchApi, ...api }), [api]);
  const [data, setData] = useState<InstanceWorkbenchState | null>(null);
  const [tab, setTab] = useState<'parameters' | 'artifacts' | 'logs'>('parameters');
  const [workflow, setWorkflow] = useState('');
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [graphOpen, setGraphOpen] = useState(false);
  const [graph, setGraph] = useState<unknown>();
  const [busy, setBusy] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const keyRef = useRef<Record<string, string>>({});
  const pollRef = useRef(false);
  const id = instanceId(instance);

  const load = useCallback(async (silent = false) => {
    if (!id || pollRef.current) return;
    pollRef.current = true;
    try {
      const next = recordPayload(await workbenchApi.getInstanceWorkbench(id));
      setData(next);
      setWorkflow((current) => current || (next.allowed_workflows?.[0] ? workflowIdentity(next.allowed_workflows[0]) : ''));
      setValues((current) => Object.keys(current).length ? current : { ...(next.values || {}) });
      if (!silent) setStatus('工作台状态已刷新。');
    } catch (cause) { if (!silent) setError(apiMessage(cause)); }
    finally { pollRef.current = false; }
  }, [id, workbenchApi]);
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(true), 8000); return () => window.clearInterval(timer); }, [load]);

  const workflows = data?.allowed_workflows || [];
  const selected = workflows.find((item) => workflowIdentity(item) === workflow);
  const parameters = selected?.parameters || data?.parameters || [];
  const readOnly = historyOnly(instance, data || undefined);
  const canQueue = !readOnly && data?.capabilities?.includes('queue_reviewed_workflow') === true && Boolean(selected);
  const canSetParams = !readOnly && data?.capabilities?.includes('set_reviewed_params') === true && Boolean(selected);
  function keyFor(action: string) { return keyRef.current[action] ||= newKey(`workbench-${action}`); }
  function clearKey(action: string) { delete keyRef.current[action]; }
  async function action(actionName: 'set_reviewed_params' | 'queue_reviewed_workflow' | 'get_reviewed_graph', params: Record<string, unknown>, success: string, after?: (result: unknown) => void) {
    if (busy || !selected || (readOnly && actionName !== 'get_reviewed_graph')) return;
    setBusy(actionName); setError(''); setStatus('正在请求受控工作台操作…');
    try { const result = await workbenchApi.workbenchAction(id, actionName, params, keyFor(actionName)); const next = recordPayload(result); if (next.graph) setGraph(next.graph); clearKey(actionName); setStatus(success); after?.(result); }
    catch (cause) { setError(apiMessage(cause)); setStatus('操作未确认；再次重试会复用同一幂等键。'); }
    finally { setBusy(null); }
  }
  const selectWorkflow = (value: string) => {
    setWorkflow(value); setGraph(undefined);
    const next = workflows.find((item) => workflowIdentity(item) === value);
    setValues({ ...(next?.draft?.values || {}) });
  };
  const saveParameters = () => { if (selected && canSetParams) void action('set_reviewed_params', { workflow_key: selected.workflow_key, workflow_version: selected.workflow_version, params: values }, '受审查参数已保存。'); };
  const queueWorkflow = () => {
    if (!selected || !canQueue) return;
    const jobKey = keyFor('queue_job');
    void action('queue_reviewed_workflow', { workflow_key: selected.workflow_key, workflow_version: selected.workflow_version, job_key: jobKey, job_input: { project_id: instance.project_id, model_id: selected.model_id || 'unknown', params: values, asset_ids: [], execution_policy: { provider: instance.provider || 'mock', max_runtime_minutes: 10, allocation: 'existing', account_id: instance.account_id || null, instance_id: id, lifecycle: 'save' } } }, '已提交受审查工作流到队列。', onQueued);
  };
  const openGraph = () => { if (selected) void action('get_reviewed_graph', { workflow_key: selected.workflow_key, workflow_version: selected.workflow_version }, '受审查 Graph 已读取。', () => setGraphOpen(true)); };

  return <section className={styles.workbench} aria-label="受控实例工作台">
    <header className={styles.workbenchHeader}><div><p className={styles.eyebrow}>受控工作台</p><h2>{instance.instance_label || instance.label || id}</h2><p className={styles.muted}>{instance.gpu_reported || 'GPU 未确认'} · {instance.state || '状态未知'} · 不暴露 Runtime 凭据</p></div>{onBack && <button type="button" className={styles.buttonSecondary} onClick={onBack}>返回实例</button>}</header>
    {status && <p className={styles.status} role="status">{status}</p>}{error && <p className={styles.error} role="alert">{error}</p>}
    {readOnly && <p className={styles.readOnlyNotice}>此实例处于历史或未验证状态，工作台只读，不会发送真实操作。</p>}
    <div className={styles.healthLine}><span className={`${styles.stateBadge} ${data?.comfy?.verified ? styles.good : ''}`}>Comfy：{data?.comfy?.available ? (data.comfy.verified ? '已验证' : '可用但未验证') : '未确认'}</span><span className={styles.stateBadge}>允许动作：{data?.capabilities?.length || 0}</span></div>
    {!data ? <div className={styles.empty}><strong>正在读取受审查工作台…</strong><span>不会发送任意节点、脚本、Shell 或 URL 代理请求。</span></div> : <>
      <div className={styles.tabRow} role="tablist" aria-label="工作台内容">{([['parameters', '参数'], ['artifacts', '产物'], ['logs', '日志']] as const).map(([key, label]) => <button type="button" role="tab" aria-selected={tab === key} className={styles.tabButton} key={key} onClick={() => setTab(key)}>{label}</button>)}</div>
      {tab === 'parameters' && <div className={styles.workbenchPanel}>
        <div className={styles.field}><label htmlFor="reviewed-workflow">受审查工作流</label><select id="reviewed-workflow" value={workflow} onChange={(event) => selectWorkflow(event.target.value)} disabled={readOnly || Boolean(busy)}><option value="">请选择已审核工作流</option>{workflows.map((item) => <option key={workflowIdentity(item)} value={workflowIdentity(item)}>{item.workflow_key} · v{item.workflow_version}{item.mock ? ' · Mock' : ''}</option>)}</select></div>
        {selected?.description && <p className={styles.muted}>{selected.description}</p>}
        <div className={styles.fieldList}>{parameters.map((parameter, index) => { const key = String(parameter.key || parameter.id || parameter.name || `parameter_${index + 1}`); const current = values[key] ?? parameter.default ?? ''; const editable = canSetParams && parameter.editable !== false; return <div className={styles.field} key={key}><label htmlFor={`workbench-param-${key}`}>{parameter.label || parameter.name || key}{editable ? ' · 受审查可编辑' : ' · 只读'}</label><input id={`workbench-param-${key}`} type={parameter.type === 'number' || parameter.type === 'integer' ? 'number' : 'text'} value={String(current)} disabled={!editable} min={parameter.min} max={parameter.max} step={parameter.step || (parameter.type === 'integer' ? 1 : 'any')} onChange={(event) => setValues((old) => ({ ...old, [key]: parameter.type === 'number' ? Number(event.target.value) : parameter.type === 'integer' ? Number.parseInt(event.target.value, 10) : event.target.value }))} />{parameter.description && <small>{parameter.description}</small>}</div>; })}</div>
        {!parameters.length && <p className={styles.muted}>服务端当前只返回工作流 allowlist；没有可编辑参数。</p>}
        <button type="button" className={styles.buttonSecondary} onClick={openGraph} disabled={!selected || Boolean(busy)}>读取受审查 Graph</button><div className={styles.workbenchActions}><button type="button" className={styles.buttonSecondary} onClick={saveParameters} disabled={!canSetParams || Boolean(busy)}>保存参数</button><button type="button" className={styles.button} onClick={queueWorkflow} disabled={!canQueue || Boolean(busy)}>{busy === 'queue_reviewed_workflow' ? '提交中…' : '提交到队列'}</button></div>
      </div>}
      {tab === 'artifacts' && <div className={styles.workbenchPanel}><div className={styles.artifactList}>{(data.artifacts || []).map((artifact, index) => { const src = safeAssetUrl(artifact.thumbnail_url); return <article className={styles.artifactItem} key={artifact.id || `${artifact.name}-${index}`}>{src ? <img className={styles.artifactThumb} src={src} alt="" /> : <div className={styles.artifactThumb} aria-hidden="true" />}<div><b>{artifact.name || artifact.filename || artifact.id || '未命名产物'}</b><small className={styles.muted}>{artifact.mime || '类型未确认'} · {artifact.status || '已记录'}</small></div></article>; })}</div>{!data.artifacts?.length && <div className={styles.empty}><strong>还没有产物</strong><span>产物来自服务端安全返回。</span></div>}</div>}
      {tab === 'logs' && <div className={styles.workbenchPanel}><div className={styles.logList}>{(data.logs || []).map((item, index) => <article className={styles.logItem} key={typeof item === 'object' && item.id ? item.id : index}><time>{typeof item === 'object' ? item.created_at || '' : ''}</time><pre>{typeof item === 'string' ? item : item.message || JSON.stringify(item)}</pre></article>)}</div>{!data.logs?.length && <div className={styles.empty}><strong>暂无安全日志</strong><span>仅显示服务端 allowlist 返回的脱敏日志。</span></div>}</div>}
    </>}
    {graphOpen && <div className={styles.graphOverlay} role="dialog" aria-modal="true" aria-label="受审查 Graph 查看器"><div className={styles.graphToolbar}><strong>受审查 Graph · 只读</strong><button type="button" className={styles.buttonSecondary} onClick={() => setGraphOpen(false)}>关闭</button></div><div className={styles.graphCanvas}><pre>{JSON.stringify(graph, null, 2)}</pre></div></div>}
  </section>;
}

export { defaultWorkbenchApi };
