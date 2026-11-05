import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import {
  apiMessage, assetUrl, cancelGpuJob, createGpuJob, createProject, deleteAsset, disconnectGoogle,
  getGpuComputeSpecs, getGpuGallery, getGpuJob, getGpuJobs, getGpuModels,
  getGpuPresets, getReferenceAssets, getGpuSettings, getGpuUsage, getGpuWorkflowSchema, getGpuWorkflows, getProjects, importGpuWorkflow,
  isLoggedIn, login, publishGpuWorkflow, retryGpuJob, startGoogleAuth, stopGpuCompute, updateAsset, uploadAsset,
  validateGpuWorkflow, rollbackGpuWorkflow, saveGpuPreset, type ComputeSpec, type GalleryAsset, type GpuJob, type GpuPreset,
  type GpuManifest, type GpuModel, type GpuParameter, type GpuSettings, type GpuUsage, type GpuWorkflow, type Project, type Provider,
} from './lib/gpu-api';
import { GpuParamForm } from './components/GpuParamForm';
import { ReferencePicker, referenceAssets, supportsReferences } from './components/ReferencePicker';
import { GoogleOAuthSetup } from './components/GoogleOAuthSetup';
import { ComputeSpecs } from './components/ComputeSpecs';
import styles from './gpu.module.css';

const tabs = ['创作', '任务', '作品', '设置'] as const;
type Tab = typeof tabs[number];
const terminalStates = new Set(['completed', 'succeeded', 'failed', 'failed_final', 'cancelled', 'cancelled_local', 'blocked']);

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function parameterList(manifest: GpuManifest | null): GpuParameter[] {
  if (!manifest?.parameters) return [];
  if (Array.isArray(manifest.parameters)) return manifest.parameters;
  return Object.entries(manifest.parameters).map(([key, item]) => {
    const normalized = item as GpuParameter;
    return { ...normalized, key, id: normalized.id || key };
  });
}

function parameterKey(item: GpuParameter, index: number) {
  return item.key || item.id || item.name || `parameter_${index + 1}`;
}

function defaultParams(manifest: GpuManifest | null) {
  return Object.fromEntries(
    parameterList(manifest).map((item, index) => [
      parameterKey(item, index),
      item.default !== undefined
        ? item.default
        : item.type === 'boolean'
        ? false
        : item.type === 'integer' || item.type === 'number'
        ? item.min ?? 0
        : '',
    ])
  );
}

function keyId() {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `gpu-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function shown(value: unknown) {
  return value === null || value === undefined || value === '' ? '未知' : typeof value === 'object' ? JSON.stringify(value) : String(value);
}

function readableState(value?: string) {
  return value
    ? ({
        queued: '排队中', pending: '等待中', preparing: '准备中', validating: '校验中',
        creating: '创建运行时', reserved: '已预留', active: '运行时已启动', started: '已启动',
        uploading: '上传中', running: '运行中', saving: '保存中', cleanup_pending: '清理中',
        deleted: '已释放', quarantined: '已隔离', completed: '已完成', succeeded: '已完成',
        failed: '失败', failed_final: '最终失败', cancelled: '已取消', cancelled_local: '已取消', blocked: '已阻止',
      }[value] || value)
    : '未知';
}

function workflowState(value?: string, active?: boolean) {
  return active ? '已发布 · 当前版本' : ({ draft: '草稿', validated: '已校验', published: '已发布 · 历史版本' }[value || ''] || value || '未知');
}

function progressPercent(value?: number) {
  return typeof value === 'number' ? Math.round(Math.max(0, Math.min(1, value)) * 100) : null;
}

function assetIdsFromParams(params: Record<string, unknown>, validIds: Set<string>) {
  return Object.values(params).filter((value): value is string => typeof value === 'string' && validIds.has(value));
}

function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await login(password);
      onLoggedIn();
    } catch (cause) {
      setError(apiMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <form className="loginCard" onSubmit={submit}>
        <h1>Colab GPU Studio</h1>
        <p className="muted">输入访问密码解锁独立工作台</p>
        <label>
          访问密码
          <input
            autoFocus
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            placeholder="请输入 STUDIO_PASSWORD"
            required
          />
        </label>
        {error && <p className="status error" role="alert">{error}</p>}
        <button className="button" disabled={busy} type="submit">
          {busy ? '登录中…' : '进入工作台'}
        </button>
      </form>
    </main>
  );
}

function ProjectSheet({
  open,
  projects,
  projectId,
  onClose,
  onSelect,
  onCreate,
}: {
  open: boolean;
  projects: Project[];
  projectId: string;
  onClose: () => void;
  onSelect: (id: string) => void;
  onCreate: (title: string) => Promise<void>;
}) {
  const [title, setTitle] = useState('');
  const [creating, setCreating] = useState(false);

  if (!open) return null;

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    setCreating(true);
    try {
      await onCreate(title.trim());
      setTitle('');
      onClose();
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className={styles.dialogBackdrop} role="presentation" onClick={onClose}>
      <section
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-label="项目管理"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.cardHead}>
          <h2>项目管理</h2>
          <button className="button secondary" type="button" onClick={onClose}>完成</button>
        </div>
        <div className="field">
          <label>选择当前项目</label>
          <select
            value={projectId}
            onChange={(e) => {
              onSelect(e.target.value);
              onClose();
            }}
          >
            <option value="">未选择项目</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.title}</option>
            ))}
          </select>
        </div>
        <form className="field" onSubmit={handleCreate}>
          <label>新建项目</label>
          <div className="actions" style={{ display: 'flex', gap: '2vw' }}>
            <input
              className="fieldInput"
              style={{ flex: 1 }}
              placeholder="项目名称"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
            <button className="button" disabled={creating || !title.trim()} type="submit">
              {creating ? '创建中…' : '创建'}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function CreatePanel({
  projectId,
  models,
  workflows,
  presets,
  settings,
  manifest,
  params,
  setParams,
  gallery,
  selectedAssets,
  setSelectedAssets,
  uploadProgress,
  onUpload,
  modelId,
  setModelId,
  workflowId,
  setWorkflowId,
  runtime,
  setRuntime,
  maxRuntime,
  setMaxRuntime,
  loading,
  error,
  onSubmit,
  onApplyPreset,
  onSavePreset,
}: {
  projectId: string;
  models: GpuModel[];
  workflows: GpuWorkflow[];
  presets: GpuPreset[];
  settings: GpuSettings | null;
  manifest: GpuManifest | null;
  params: Record<string, unknown>;
  setParams: (value: Record<string, unknown>) => void;
  gallery: GalleryAsset[];
  selectedAssets: string[];
  setSelectedAssets: (value: string[]) => void;
  uploadProgress: Record<string, number>;
  onUpload: (file: File) => Promise<void>;
  modelId: string;
  setModelId: (value: string) => void;
  workflowId: string;
  setWorkflowId: (value: string) => void;
  runtime: Provider;
  setRuntime: (value: Provider) => void;
  maxRuntime: number;
  setMaxRuntime: (value: number) => void;
  loading: string;
  error: string;
  onSubmit: () => void;
  onApplyPreset: (preset: GpuPreset) => void;
  onSavePreset: (name: string) => void;
}) {
  const [presetName, setPresetName] = useState('');
  const [showPresetSave, setShowPresetSave] = useState(false);

  const workflowFamilies = useMemo(() => {
    const map = new Map<string, { modelId: string; items: GpuWorkflow[] }>();
    workflows.forEach((wf) => {
      if (wf.state !== 'published' && wf.active !== true) return;
      const list = map.get(wf.workflow_key)?.items || [];
      list.push(wf);
      list.sort((a, b) => String(b.workflow_version).localeCompare(String(a.workflow_version), undefined, { numeric: true }));
      map.set(wf.workflow_key, { modelId: wf.model_id, items: list });
    });
    return [...map.entries()];
  }, [workflows]);

  const disabledModels = useMemo(() => models.filter((m) => !m.enabled), [models]);
  const activeWorkflow = workflows.find((item) => item.id === workflowId);

  useEffect(() => {
    if (!workflowId && workflowFamilies.length > 0) {
      const firstFam = workflowFamilies[0][1];
      const defaultWf = firstFam.items.find((i) => i.active) || firstFam.items[0];
      if (defaultWf) {
        setModelId(defaultWf.model_id);
        setWorkflowId(defaultWf.id);
      }
    }
  }, [workflowFamilies, workflowId, setModelId, setWorkflowId]);

  if (!projectId) {
    return (
      <div className="empty">
        <strong>请先在顶栏选择或新建项目</strong>
        <span>项目用于归档参数预设、输入素材和输出作品。</span>
      </div>
    );
  }

  const maxMinutesCap = typeof settings?.budget?.max_runtime_minutes === 'number'
    ? settings.budget.max_runtime_minutes
    : 60;

  return (
    <div className={styles.stack}>
      {/* 1. 工作流卡片选择 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>1. 选择工作流</h2>
          <span className="pill">{workflowFamilies.length} 个可用流程</span>
        </div>

        {workflowFamilies.length === 0 ? (
          <div className="empty">
            <strong>暂无已发布工作流</strong>
            <span>请前往“设置 → 工作流管理”发布或配置所需工作流。</span>
          </div>
        ) : (
          <div className={styles.stack} style={{ gap: '2.5vw' }}>
            {workflowFamilies.map(([key, fam]) => {
              const isSelected = activeWorkflow?.workflow_key === key;
              const currentWf = isSelected ? activeWorkflow : (fam.items.find((i) => i.active) || fam.items[0]);
              const model = models.find((m) => m.model_id === fam.modelId);

              return (
                <div
                  key={key}
                  className={styles.workflowCard}
                  data-selected={isSelected}
                  onClick={() => {
                    if (!isSelected && currentWf) {
                      setModelId(currentWf.model_id);
                      setWorkflowId(currentWf.id);
                    }
                  }}
                >
                  <div className={styles.workflowHeader}>
                    <div>
                      <strong>{key}</strong>
                      <p className="muted" style={{ margin: '0.5vw 0 0', fontSize: '3vw' }}>
                        模型：{model?.display_name || fam.modelId}
                      </p>
                    </div>
                    {fam.items.length > 1 ? (
                      <select
                        aria-label="切换版本"
                        value={isSelected ? workflowId : currentWf.id}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => {
                          const target = fam.items.find((i) => i.id === e.target.value);
                          if (target) {
                            setModelId(target.model_id);
                            setWorkflowId(target.id);
                          }
                        }}
                        style={{ width: 'auto', minHeight: '8vw', padding: '1vw 2vw' }}
                      >
                        {fam.items.map((i) => (
                          <option key={i.id} value={i.id}>
                            v{i.workflow_version} {i.active ? '（当前）' : ''}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="pill" style={{ fontSize: '2.6vw' }}>v{currentWf.workflow_version}</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {disabledModels.length > 0 && (
          <details className="foldable">
            <summary>暂未开放模型（{disabledModels.length}）</summary>
            <div className="foldContent">
              {disabledModels.map((m) => (
                <p key={m.model_id} className="muted" style={{ margin: 0, fontSize: '3vw' }}>
                  • <strong>{m.display_name}</strong>：{m.reason || '待管理员审核后开放'}
                </p>
              ))}
            </div>
          </details>
        )}
      </section>

      {/* 2. 参数配置 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>2. 参数设置</h2>
          <details style={{ position: 'relative' }}>
            <summary className="pill" style={{ cursor: 'pointer' }}>预设管理</summary>
            <div
              style={{
                position: 'absolute', right: 0, top: '100%', zIndex: 10, minWidth: '60vw',
                background: 'rgba(20, 28, 26, 0.95)', border: '1px solid var(--line-strong)',
                borderRadius: '3vw', padding: '3vw', marginTop: '1vw', backdropFilter: 'blur(16px)'
              }}
            >
              <div className="field">
                <label>套用预设</label>
                <select
                  defaultValue=""
                  onChange={(e) => {
                    const preset = presets.find((p) => p.id === e.target.value);
                    if (preset) onApplyPreset(preset);
                  }}
                >
                  <option value="">选择预设</option>
                  {presets.filter((p) => p.workflow_version_id === workflowId).map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
              <div style={{ marginTop: '2vw' }}>
                {!showPresetSave ? (
                  <button className="button secondary" style={{ width: '100%', minHeight: '8vw' }} type="button" onClick={() => setShowPresetSave(true)}>
                    存为新预设
                  </button>
                ) : (
                  <div style={{ display: 'flex', gap: '1.5vw' }}>
                    <input
                      className="fieldInput"
                      style={{ flex: 1, minHeight: '8vw', padding: '1vw 2vw' }}
                      placeholder="预设名称"
                      value={presetName}
                      onChange={(e) => setPresetName(e.target.value)}
                    />
                    <button
                      className="button"
                      style={{ minHeight: '8vw', padding: '1vw 3vw' }}
                      type="button"
                      disabled={!presetName.trim()}
                      onClick={() => {
                        onSavePreset(presetName.trim());
                        setPresetName('');
                        setShowPresetSave(false);
                      }}
                    >
                      保存
                    </button>
                  </div>
                )}
              </div>
            </div>
          </details>
        </div>

        {manifest && (
          <GpuParamForm
            manifest={manifest}
            values={params}
            assets={gallery}
            uploadProgress={uploadProgress}
            onChange={(k, v) => setParams({ ...params, [k]: v })}
            onUpload={onUpload}
          />
        )}

        {supportsReferences(manifest) && (
          <ReferencePicker
            manifest={manifest}
            assets={gallery}
            selected={selectedAssets}
            onChange={setSelectedAssets}
            onUpload={onUpload}
          />
        )}
      </section>

      {/* 3. 运行控制台 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>3. 生成与运行</h2>
        </div>

        <div className="field">
          <label>运行环境</label>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2vw' }}>
            <button
              type="button"
              className={runtime === 'mock' ? 'button' : 'button secondary'}
              onClick={() => setRuntime('mock')}
            >
              离线模拟 (Mock)
            </button>
            <button
              type="button"
              className={runtime === 'colab' ? 'button' : 'button secondary'}
              disabled={!settings?.real_enabled}
              onClick={() => setRuntime('colab')}
            >
              Colab GPU {settings?.real_enabled ? '' : '（未启用）'}
            </button>
          </div>
        </div>

        {runtime === 'colab' && (
          <div className="twoCol">
            <div className="field">
              <label>最长运行（分钟）</label>
              <input
                type="number"
                min={5}
                max={maxMinutesCap}
                value={maxRuntime}
                onChange={(e) => setMaxRuntime(Number(e.target.value))}
              />
            </div>
            <div className="field">
              <label>预算限额状态</label>
              <div className="pill" style={{ width: '100%', minHeight: 'calc(var(--u) * 10)' }}>
                日限额：{shown(settings?.budget?.daily_units)} CU
              </div>
            </div>
          </div>
        )}

        <button
          className="button"
          style={{ width: '100%', minHeight: 'calc(var(--u) * 12)', fontSize: 'clamp(4vw, 1vw + 1vh, 4.8vw)' }}
          disabled={!workflowId || loading === 'submit'}
          type="button"
          onClick={onSubmit}
        >
          {loading === 'submit' ? '提交任务中…' : '立即生成'}
        </button>
        {error && <p className="status error" role="alert">{error}</p>}
      </section>
    </div>
  );
}

function TasksPanel({
  jobs,
  error,
  refreshing,
  onRefresh,
  onCancel,
  onRetry,
  onDetail,
}: {
  jobs: GpuJob[];
  error: string;
  refreshing: boolean;
  onRefresh: () => void;
  onCancel: (id: string) => void;
  onRetry: (id: string) => void;
  onDetail: (job: GpuJob) => void;
}) {
  return (
    <div className={styles.stack}>
      <section className={styles.card}>
        <div className={styles.sectionTitle}>
          <h2>当前项目任务</h2>
          <button className="button secondary" style={{ minHeight: '8vw', padding: '1vw 3vw' }} type="button" onClick={onRefresh}>
            {refreshing ? '刷新中…' : '刷新'}
          </button>
        </div>
        {error && <p className="status error" role="alert">{error}</p>}
        {jobs.length ? (
          jobs.map((job) => (
            <article className={styles.job} key={job.id}>
              <div className={styles.jobHeader}>
                <strong>{job.workflow_key || job.id}</strong>
                <span className="pill">{job.provider === 'mock' ? 'MOCK · ' : ''}{readableState(job.state)}</span>
              </div>
              <div className={styles.jobMeta}>
                <span>任务：{job.id.slice(0, 8)}</span>
                <span>创建：{job.created_at || '刚刚'}</span>
                {job.estimated_units ? <span>预估：{shown(job.estimated_units)} CU</span> : null}
              </div>
              {progressPercent(job.progress) !== null && (
                <div className={styles.progress} aria-label={`进度 ${progressPercent(job.progress)}%`}>
                  <span style={{ width: `${progressPercent(job.progress)}%` }} />
                </div>
              )}
              {(job.error_message || job.error) && (
                <p className="status error" role="alert">{job.error_message || job.error}</p>
              )}
              <div className="actions">
                <button className="button secondary" type="button" onClick={() => onDetail(job)}>
                  查看详情
                </button>
                {!terminalStates.has(String(job.state || '').toLowerCase()) && (
                  <button className="button danger" type="button" onClick={() => onCancel(job.id)}>
                    取消
                  </button>
                )}
                {String(job.state || '').toLowerCase().includes('failed') && (
                  <button className="button secondary" type="button" onClick={() => onRetry(job.id)}>
                    重试
                  </button>
                )}
              </div>
            </article>
          ))
        ) : (
          <div className="empty">
            <strong>当前项目暂无任务</strong>
            <span>在创作页发起生成后，任务会显示在这里。</span>
          </div>
        )}
      </section>
    </div>
  );
}

function GalleryPanel({
  gallery,
  onFavorite,
  onDelete,
  onOpen,
}: {
  gallery: GalleryAsset[];
  onFavorite: (asset: GalleryAsset) => void;
  onDelete: (asset: GalleryAsset) => void;
  onOpen: (asset: GalleryAsset) => void;
}) {
  return (
    <section className={styles.card}>
      <div className={styles.cardHead}>
        <h2>作品画廊</h2>
        <span className="pill">{gallery.length} 件作品</span>
      </div>
      {gallery.length ? (
        <div className={styles.assetGrid}>
          {gallery.map((asset) => {
            const isVideo = asset.mime.startsWith('video/');
            const isJson = asset.mime.includes('json') || asset.filename?.toLowerCase().endsWith('.json');
            return (
              <article className={styles.asset} key={asset.id}>
                <button className={styles.assetImage} type="button" onClick={() => onOpen(asset)}>
                  {isVideo ? (
                    <video src={assetUrl(asset)} preload="metadata" muted aria-label={asset.filename || '视频产物'} />
                  ) : isJson ? (
                    <div className={styles.diagnosticsThumb}>JSON<br /><small>诊断文件</small></div>
                  ) : (
                    <img src={assetUrl(asset, true)} alt={asset.filename || '产物'} />
                  )}
                </button>
                <footer>
                  <span className={styles.assetName}>{asset.filename || asset.id}</span>
                  <div className="actions" style={{ gap: '1vw' }}>
                    <button className="button secondary" style={{ flex: 1, minHeight: '7vw', padding: '1vw' }} type="button" onClick={() => onFavorite(asset)}>
                      {asset.favorite ? '已收藏' : '收藏'}
                    </button>
                    <button className="button danger" style={{ minHeight: '7vw', padding: '1vw 2vw' }} type="button" onClick={() => onDelete(asset)}>
                      删除
                    </button>
                  </div>
                </footer>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="empty">
          <strong>暂无生成作品</strong>
          <span>生成成功的图像与视频会自动保存在这里。</span>
        </div>
      )}
    </section>
  );
}

function WorkflowsPanel({
  workflows,
  onImport,
  onValidate,
  onPublish,
  onRollback,
}: {
  workflows: GpuWorkflow[];
  onImport: (bundle: string) => void;
  onValidate: (workflow: GpuWorkflow) => void;
  onPublish: (workflow: GpuWorkflow) => void;
  onRollback: (workflow: GpuWorkflow) => void;
}) {
  const [text, setText] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  return (
    <div className={styles.stack}>
      <details className="foldable">
        <summary>导入新工作流草稿</summary>
        <div className="foldContent">
          <textarea
            className={styles.code}
            placeholder="粘贴工作流 JSON bundle (含 manifest, api_graph, object_info)"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="actions">
            <button className="button secondary" type="button" onClick={() => fileRef.current?.click()}>
              读取 JSON 文件
            </button>
            <input
              ref={fileRef}
              hidden
              type="file"
              accept="application/json,.json"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) setText(await file.text());
                e.target.value = '';
              }}
            />
            <button className="button" type="button" disabled={!text.trim()} onClick={() => onImport(text)}>
              导入草稿
            </button>
          </div>
        </div>
      </details>

      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>已注册工作流版本</h2>
          <span className="pill">{workflows.length} 个版本</span>
        </div>
        {workflows.length ? (
          workflows.map((wf) => (
            <article className={styles.workflowCard} key={wf.id}>
              <div className={styles.workflowHeader}>
                <div>
                  <strong>{wf.workflow_key} · v{wf.workflow_version}</strong>
                  <p className="muted" style={{ margin: '0.5vw 0 0', fontSize: '3vw' }}>
                    模型 {wf.model_id}
                  </p>
                </div>
                <span className="pill">{workflowState(wf.state, wf.active)}</span>
              </div>
              <div className="actions">
                <button
                  className="button secondary"
                  type="button"
                  disabled={wf.state === 'published'}
                  onClick={() => onValidate(wf)}
                >
                  校验
                </button>
                <button
                  className="button"
                  type="button"
                  disabled={wf.state === 'published'}
                  onClick={() => onPublish(wf)}
                >
                  确认发布
                </button>
                <button
                  className="button secondary"
                  type="button"
                  disabled={wf.state !== 'published'}
                  onClick={() => onRollback(wf)}
                >
                  回滚为此版本
                </button>
              </div>
            </article>
          ))
        ) : (
          <div className="empty">
            <strong>未检测到工作流</strong>
          </div>
        )}
      </section>
    </div>
  );
}

function SettingsPanel({
  settings,
  usage,
  specs,
  workflows,
  loading,
  error,
  onConnect,
  onDisconnect,
  onCheck,
  onStop,
  onImport,
  onValidate,
  onPublish,
  onRollback,
  onLogout,
}: {
  settings: GpuSettings | null;
  usage: GpuUsage | null;
  specs: ComputeSpec[];
  workflows: GpuWorkflow[];
  loading: string;
  error: string;
  onConnect: (purpose: 'drive' | 'colab') => void;
  onDisconnect: (purpose: 'drive' | 'colab') => void;
  onCheck: () => void;
  onStop: () => void;
  onImport: (bundle: string) => void;
  onValidate: (workflow: GpuWorkflow) => void;
  onPublish: (workflow: GpuWorkflow) => void;
  onRollback: (workflow: GpuWorkflow) => void;
  onLogout: () => void;
}) {
  const [oauthConfigured, setOauthConfigured] = useState(false);

  return (
    <div className={styles.stack}>
      {/* 1. 运行与配额概览 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>运行概览与配额</h2>
          <span className="pill">真实 GPU: {settings?.real_enabled ? '已启用' : '未启用'}</span>
        </div>
        <div className={styles.statGrid}>
          <div className={styles.stat}>
            <span>今日估算</span>
            <strong>{shown((usage as Record<string, unknown> | null)?.estimated_units)}</strong>
          </div>
          <div className={styles.stat}>
            <span>实际消耗</span>
            <strong>{shown((usage as Record<string, unknown> | null)?.actual_units)}</strong>
          </div>
          <div className={styles.stat}>
            <span>日限额</span>
            <strong>{shown((usage as Record<string, unknown> | null)?.daily_limit)}</strong>
          </div>
          <div className={styles.stat}>
            <span>活跃运行时</span>
            <strong>{shown((usage as Record<string, unknown> | null)?.active_runtimes)}</strong>
          </div>
        </div>
      </section>

      {/* 2. Google 连接 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>Google 连接</h2>
        </div>
        {(['colab', 'drive'] as const).map((purpose) => {
          const state = settings?.google?.[purpose];
          return (
            <div className={styles.workflowCard} key={purpose}>
              <div className={styles.workflowHeader}>
                <strong>{purpose === 'colab' ? 'Google Colab' : 'Google Drive'}</strong>
                <span className="pill">
                  {state?.connected ? '已连接' : (oauthConfigured || state?.configured) ? '已配置未连接' : '未配置'}
                </span>
              </div>
              <div className="actions">
                <button
                  className="button secondary"
                  type="button"
                  disabled={!oauthConfigured && !state?.configured}
                  onClick={() => onConnect(purpose)}
                >
                  连接 {purpose === 'colab' ? 'Google Colab' : 'Google Drive'}
                </button>
                <button className="button danger" type="button" onClick={() => onDisconnect(purpose)}>
                  断开
                </button>
              </div>
            </div>
          );
        })}
        <details className="foldable">
          <summary>Google OAuth 应用配置</summary>
          <div className="foldContent">
            <GoogleOAuthSetup onConfigured={() => setOauthConfigured(true)} />
          </div>
        </details>
      </section>

      {/* 3. GPU 资源与急停 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>GPU 资源管理</h2>
        </div>
        <div className="actions">
          <button className="button secondary" disabled={loading === 'check'} type="button" onClick={onCheck}>
            {loading === 'check' ? '检查中…' : '检查可用 GPU'}
          </button>
          <button className="button danger" disabled={loading === 'stop'} type="button" onClick={onStop}>
            {loading === 'stop' ? '急停中…' : '停止本站计算任务'}
          </button>
        </div>
        {specs.length ? <ComputeSpecs specs={specs} /> : null}
      </section>

      {/* 4. 工作流管理收纳入口 */}
      <details className="foldable">
        <summary>工作流与模型管理</summary>
        <div className="foldContent">
          <WorkflowsPanel
            workflows={workflows}
            onImport={onImport}
            onValidate={onValidate}
            onPublish={onPublish}
            onRollback={onRollback}
          />
        </div>
      </details>

      {/* 5. 退出登录 */}
      <div style={{ textAlign: 'center', marginTop: '2vw' }}>
        <button className="button danger" type="button" onClick={onLogout}>
          退出登录
        </button>
      </div>

      {error && <p className="status error" role="alert">{error}</p>}
    </div>
  );
}

export default function App() {
  const [loggedIn, setLoggedIn] = useState(isLoggedIn());
  const [tab, setTab] = useState<Tab>('创作');
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState('');
  const [showProjectSheet, setShowProjectSheet] = useState(false);
  const currentProjectId = useRef(projectId);
  currentProjectId.current = projectId;

  const [models, setModels] = useState<GpuModel[]>([]);
  const [workflows, setWorkflows] = useState<GpuWorkflow[]>([]);
  const [presets, setPresets] = useState<GpuPreset[]>([]);
  const [modelId, setModelId] = useState('');
  const [workflowId, setWorkflowId] = useState('');
  const [manifest, setManifest] = useState<GpuManifest | null>(null);
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [gallery, setGallery] = useState<GalleryAsset[]>([]);
  const [referenceImages, setReferenceImages] = useState<GalleryAsset[]>([]);
  const [jobs, setJobs] = useState<GpuJob[]>([]);
  const [selectedAssets, setSelectedAssets] = useState<string[]>([]);
  const [runtime, setRuntime] = useState<Provider>('mock');
  const [maxRuntime, setMaxRuntime] = useState(30);
  const [settings, setSettings] = useState<GpuSettings | null>(null);
  const [usage, setUsage] = useState<GpuUsage | null>(null);
  const [specs, setSpecs] = useState<ComputeSpec[]>([]);
  const [error, setError] = useState('');
  const [jobError, setJobError] = useState('');
  const [loading, setLoading] = useState('');
  const [uploadProgress, setUploadProgress] = useState<Record<string, number>>({});
  const [confirm, setConfirm] = useState<'submit' | 'stop' | null>(null);
  const [detail, setDetail] = useState<GpuJob | null>(null);
  const [viewer, setViewer] = useState<GalleryAsset | null>(null);
  const [againJob, setAgainJob] = useState<GpuJob | null>(null);
  const [loginRefresh, setLoginRefresh] = useState(0);
  const [refreshingJobs, setRefreshingJobs] = useState(false);
  const idempotencyKey = useRef('');
  const retryKeys = useRef<Record<string, string>>({});
  const jobSequence = useRef(0);

  const activeProject = projects.find((p) => p.id === projectId);
  const activeWorkflow = workflows.find((item) => item.id === workflowId);

  const activeJobsCount = useMemo(() => {
    return jobs.filter((j) => !terminalStates.has(String(j.state || '').toLowerCase())).length;
  }, [jobs]);

  useEffect(() => {
    const expire = () => setLoggedIn(false);
    window.addEventListener('gpu-auth-expired', expire);
    return () => window.removeEventListener('gpu-auth-expired', expire);
  }, []);

  useEffect(() => {
    if (!loggedIn) return;
    let alive = true;
    Promise.allSettled([
      getProjects(),
      getGpuModels(),
      getGpuWorkflows(),
      getGpuPresets(),
      getGpuSettings(),
      getGpuUsage(),
    ]).then((results) => {
      if (!alive) return;
      const [projR, modR, wfR, preR, setR, useR] = results;
      if (projR.status === 'fulfilled') {
        setProjects(projR.value);
        setProjectId((curr) => curr || projR.value[0]?.id || '');
      } else setError(apiMessage(projR.reason));
      if (modR.status === 'fulfilled') setModels(modR.value);
      if (wfR.status === 'fulfilled') setWorkflows(wfR.value);
      if (preR.status === 'fulfilled') setPresets(preR.value);
      if (setR.status === 'fulfilled') setSettings(setR.value);
      if (useR.status === 'fulfilled') setUsage(useR.value);
    }).catch((cause) => alive && setError(apiMessage(cause)));
    return () => { alive = false; };
  }, [loggedIn, loginRefresh]);

  useEffect(() => {
    if (!modelId) return;
    const allowed = workflows.filter((w) => w.model_id === modelId);
    if (!allowed.some((w) => w.id === workflowId)) {
      setWorkflowId('');
      setManifest(null);
      setParams({});
    }
  }, [modelId, workflows, workflowId]);

  useEffect(() => {
    if (!workflowId) return;
    let alive = true;
    setLoading('schema');
    getGpuWorkflowSchema(workflowId).then((next) => {
      if (alive) {
        setManifest(next);
        setParams(defaultParams(next));
      }
    }).catch((cause) => alive && setError(`参数 schema：${apiMessage(cause)}`)).finally(() => alive && setLoading(''));
    return () => { alive = false; };
  }, [workflowId]);

  useEffect(() => {
    if (!projectId || !loggedIn) return;
    let alive = true;
    const refresh = async () => {
      const seq = ++jobSequence.current;
      setRefreshingJobs(true);
      try {
        const next = await getGpuJobs(projectId);
        if (!alive || seq !== jobSequence.current) return;
        setJobs(next.filter((j) => j.project_id === projectId));
        setJobError('');
      } catch (cause) {
        if (alive && seq === jobSequence.current) setJobError(apiMessage(cause));
      } finally {
        if (alive && seq === jobSequence.current) setRefreshingJobs(false);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 4000);
    return () => {
      alive = false;
      window.clearInterval(timer);
      ++jobSequence.current;
    };
  }, [projectId, loggedIn]);

  useEffect(() => {
    setReferenceImages([]);
    setSelectedAssets([]);
    if (!projectId || !loggedIn) return;
    let alive = true;
    getReferenceAssets(projectId).then((next) => {
      if (alive) setReferenceImages(referenceAssets(next));
    }).catch((cause) => {
      if (alive) setError(`素材：${apiMessage(cause)}`);
    });
    return () => { alive = false; };
  }, [projectId, loggedIn]);

  useEffect(() => {
    setSelectedAssets([]);
  }, [workflowId]);

  useEffect(() => {
    if (!projectId || !loggedIn) return;
    let alive = true;
    getGpuGallery(projectId).then((next) => alive && setGallery(next)).catch((cause) => alive && setError(`作品：${apiMessage(cause)}`));
    return () => { alive = false; };
  }, [projectId, loggedIn, tab]);

  useEffect(() => {
    if (againJob && (againJob.resolved_params || againJob.params)) {
      setModelId(String(againJob.model_id || ''));
      const workflow = workflows.find((item) => item.workflow_key === againJob.workflow_key && String(item.workflow_version) === String(againJob.workflow_version));
      if (workflow) setWorkflowId(workflow.id);
      setParams({ ...(againJob.resolved_params || againJob.params) });
      setAgainJob(null);
      setTab('创作');
    }
  }, [againJob, workflows]);

  async function createLocalProject(title: string) {
    try {
      const created = await createProject(title);
      setProjects((curr) => [created, ...curr]);
      setProjectId(created.id);
    } catch (cause) {
      setError(apiMessage(cause));
    }
  }

  async function refreshJobs() {
    if (!projectId) return;
    setRefreshingJobs(true);
    try {
      setJobs((await getGpuJobs(projectId)).filter((j) => j.project_id === projectId));
      setJobError('');
    } catch (cause) {
      setJobError(apiMessage(cause));
    } finally {
      setRefreshingJobs(false);
    }
  }

  async function handleUpload(file: File) {
    const uploadProjectId = projectId;
    if (!uploadProjectId) return;
    setUploadProgress((curr) => ({ ...curr, [file.name]: 0 }));
    try {
      const asset = await uploadAsset(uploadProjectId, file, (progress) => {
        if (currentProjectId.current === uploadProjectId) setUploadProgress((curr) => ({ ...curr, [file.name]: progress }));
      });
      if (currentProjectId.current !== uploadProjectId || asset.project_id !== uploadProjectId) return;
      setReferenceImages((curr) => [asset, ...curr.filter((item) => item.id !== asset.id)]);
      setSelectedAssets((curr) => [...new Set([...curr, asset.id])]);
    } catch (cause) {
      if (currentProjectId.current === uploadProjectId) setError(`上传 ${file.name}：${apiMessage(cause)}`);
    } finally {
      if (currentProjectId.current === uploadProjectId) setUploadProgress((curr) => {
        const next = { ...curr };
        delete next[file.name];
        return next;
      });
    }
  }

  async function submitJob() {
    if (!projectId || !modelId || !workflowId || !activeWorkflow) return;
    if (!idempotencyKey.current) idempotencyKey.current = keyId();
    setLoading('submit');
    setError('');
    try {
      const validAssetIds = new Set(referenceAssets(referenceImages).filter((a) => a.project_id === projectId).map((a) => a.id));
      const body = {
        project_id: projectId,
        model_id: modelId,
        workflow_key: activeWorkflow.workflow_key,
        workflow_version: activeWorkflow.workflow_version,
        params,
        asset_ids: supportsReferences(manifest)
          ? [...new Set([...selectedAssets.filter((id) => validAssetIds.has(id)), ...assetIdsFromParams(params, validAssetIds)])]
          : [],
        execution_policy: {
          provider: runtime,
          max_runtime_minutes: maxRuntime,
          shutdown_after_job: true as const,
          idle_grace_minutes: 0 as const,
        },
      };
      await createGpuJob(body, idempotencyKey.current);
      setConfirm(null);
      setTab('任务');
      idempotencyKey.current = '';
      await refreshJobs();
    } catch (cause) {
      setError(`提交任务：${apiMessage(cause)}`);
    } finally {
      setLoading('');
    }
  }

  async function action(name: string, operation: () => Promise<unknown>) {
    setLoading(name);
    setError('');
    try {
      await operation();
    } catch (cause) {
      setError(apiMessage(cause));
    } finally {
      setLoading('');
    }
  }

  async function importWorkflow(raw: string) {
    try {
      const value = JSON.parse(raw) as unknown;
      const source = record(value) && record(value.workflow) ? value.workflow : value;
      if (!record(source) || !record(source.manifest) || !record(source.api_graph) || !record(source.object_info)) {
        throw new Error('JSON 必须包含 manifest、api_graph、object_info');
      }
      await action('import', async () => {
        const workflow = await importGpuWorkflow(value as never);
        setWorkflows((curr) => [workflow, ...curr.filter((item) => item.id !== workflow.id)]);
        setError('工作流已作为草稿导入；请校验后发布。');
      });
    } catch (cause) {
      setError(`导入失败：${apiMessage(cause)}`);
    }
  }

  async function reuseAsset(asset: GalleryAsset) {
    const sourceJobId = asset.source_job_id || asset.gpu_job_id;
    if (!sourceJobId) {
      setError('该素材没有来源任务记录。');
      return;
    }
    try {
      setAgainJob(await getGpuJob(sourceJobId));
    } catch (cause) {
      setError(`再次生成：${apiMessage(cause)}`);
    }
  }

  async function favorite(asset: GalleryAsset) {
    try {
      const next = await updateAsset(asset.id, !asset.favorite);
      setGallery((curr) => curr.map((i) => i.id === next.id ? next : i));
    } catch (cause) {
      setError(apiMessage(cause));
    }
  }

  async function remove(asset: GalleryAsset) {
    try {
      await deleteAsset(asset.id);
      setGallery((curr) => curr.filter((i) => i.id !== asset.id));
      setViewer(null);
    } catch (cause) {
      setError(`删除失败：${apiMessage(cause)}`);
    }
  }

  async function savePreset(name: string) {
    if (!workflowId) return;
    await action('preset', async () => {
      const next = await saveGpuPreset({ name, workflow_version_id: workflowId, overrides: params });
      setPresets((curr) => [next, ...curr]);
    });
  }

  function applyPreset(preset: GpuPreset) {
    if (preset.workflow_version_id !== workflowId) {
      setError('预设版本不兼容。');
      return;
    }
    setParams({ ...params, ...preset.overrides });
  }

  async function checkGpu() {
    await action('check', async () => setSpecs(await getGpuComputeSpecs(settings?.google?.colab?.connected ? 'colab' : 'mock')));
  }

  async function stopCompute() {
    await action('stop', async () => {
      await stopGpuCompute();
      setConfirm(null);
      setError('已请求急停。');
    });
  }

  if (!loggedIn) {
    return <Login onLoggedIn={() => { setLoggedIn(true); setLoginRefresh((c) => c + 1); }} />;
  }

  return (
    <div className="app">
      {/* 磨砂顶栏 */}
      <header className="topbar">
        <div className="brand">
          Colab GPU Studio
          <small>v0.1.28</small>
        </div>
        <button
          className="projectChip"
          type="button"
          onClick={() => setShowProjectSheet(true)}
        >
          <span>📁 {activeProject ? activeProject.title : '选择项目'}</span>
          <small style={{ color: 'var(--muted)', marginLeft: '1vw' }}>▾</small>
        </button>
      </header>

      {/* 项目切换弹层 */}
      <ProjectSheet
        open={showProjectSheet}
        projects={projects}
        projectId={projectId}
        onClose={() => setShowProjectSheet(false)}
        onSelect={setProjectId}
        onCreate={createLocalProject}
      />

      {/* 主工作区 */}
      <main className={styles.page}>
        {tab === '创作' && (
          <CreatePanel
            projectId={projectId}
            models={models}
            workflows={workflows}
            presets={presets}
            settings={settings}
            manifest={manifest}
            params={params}
            setParams={setParams}
            gallery={referenceImages}
            selectedAssets={selectedAssets}
            setSelectedAssets={setSelectedAssets}
            uploadProgress={uploadProgress}
            onUpload={handleUpload}
            modelId={modelId}
            setModelId={setModelId}
            workflowId={workflowId}
            setWorkflowId={setWorkflowId}
            runtime={runtime}
            setRuntime={setRuntime}
            maxRuntime={maxRuntime}
            setMaxRuntime={setMaxRuntime}
            loading={loading}
            error={error}
            onSubmit={() => setConfirm('submit')}
            onApplyPreset={applyPreset}
            onSavePreset={(name) => void savePreset(name)}
          />
        )}

        {tab === '任务' && (
          <TasksPanel
            jobs={jobs}
            error={jobError}
            refreshing={refreshingJobs}
            onRefresh={() => void refreshJobs()}
            onCancel={(id) => void action('cancel', async () => { await cancelGpuJob(id); await refreshJobs(); })}
            onRetry={(id) => void action('retry', async () => {
              const job = jobs.find((item) => item.id === id);
              const key = retryKeys.current[id] || job?.idempotency_key || `retry:${id}`;
              retryKeys.current[id] = key;
              await retryGpuJob(id, key);
              await refreshJobs();
            })}
            onDetail={(job) => void action('inspect', async () => setDetail(await getGpuJob(job.id)))}
          />
        )}

        {tab === '作品' && (
          <GalleryPanel
            gallery={gallery}
            onFavorite={(asset) => void favorite(asset)}
            onDelete={(asset) => void remove(asset)}
            onOpen={setViewer}
          />
        )}

        {tab === '设置' && (
          <SettingsPanel
            settings={settings}
            usage={usage}
            specs={specs}
            workflows={workflows}
            loading={loading}
            error={error}
            onConnect={(purpose) => void action('connect', async () => {
              const url = await startGoogleAuth(purpose);
              if (url) window.location.href = url;
            })}
            onDisconnect={(purpose) => void action('disconnect', async () => {
              await disconnectGoogle(purpose);
              setSettings(await getGpuSettings());
            })}
            onCheck={() => void checkGpu()}
            onStop={() => setConfirm('stop')}
            onImport={(bundle) => void importWorkflow(bundle)}
            onValidate={(wf) => void action('validate', async () => {
              await validateGpuWorkflow(wf.id);
              setWorkflows(await getGpuWorkflows());
            })}
            onPublish={(wf) => void action('publish', async () => {
              await publishGpuWorkflow(wf.id);
              setWorkflows(await getGpuWorkflows());
            })}
            onRollback={(wf) => void action('rollback', async () => {
              await rollbackGpuWorkflow(wf.workflow_key, wf.workflow_version);
              setWorkflows(await getGpuWorkflows());
            })}
            onLogout={() => {
              sessionStorage.removeItem('colab-gpu-studio-token');
              setLoggedIn(false);
            }}
          />
        )}
      </main>

      {/* 底部磨砂导航栏 */}
      <nav className="bottomNav" aria-label="底栏导航">
        {tabs.map((item) => {
          const isActive = tab === item;
          return (
            <button
              key={item}
              type="button"
              aria-current={isActive ? 'page' : undefined}
              onClick={() => setTab(item)}
            >
              <span>{item === '创作' ? '✦' : item === '任务' ? '⚡' : item === '作品' ? '🖼' : '⚙'}</span>
              <span>{item}</span>
              {item === '任务' && activeJobsCount > 0 && (
                <span className="tabBadge">{activeJobsCount}</span>
              )}
            </button>
          );
        })}
      </nav>

      {/* 确认生成弹窗 */}
      {confirm === 'submit' && activeWorkflow && (
        <div className={styles.dialogBackdrop} role="presentation" onClick={() => setConfirm(null)}>
          <section
            className={styles.dialog}
            role="dialog"
            aria-modal="true"
            aria-label="确认提交任务"
            onClick={(e) => e.stopPropagation()}
          >
            <h2>确认开始生成</h2>
            <p>
              工作流：<strong>{activeWorkflow.workflow_key} · v{activeWorkflow.workflow_version}</strong><br />
              运行环境：<strong>{runtime === 'colab' ? 'Colab GPU' : '离线模拟'}</strong><br />
              {runtime === 'colab' && <>最长限时：<strong>{maxRuntime} 分钟</strong></>}
            </p>
            <div className="actions">
              <button className="button secondary" type="button" onClick={() => setConfirm(null)}>取消</button>
              <button className="button" disabled={loading === 'submit'} type="button" onClick={() => void submitJob()}>
                {loading === 'submit' ? '提交中…' : '确认启动'}
              </button>
            </div>
          </section>
        </div>
      )}

      {/* 确认急停弹窗 */}
      {confirm === 'stop' && (
        <div className={styles.dialogBackdrop} role="presentation" onClick={() => setConfirm(null)}>
          <section
            className={styles.dialog}
            role="dialog"
            aria-modal="true"
            aria-label="确认急停"
            onClick={(e) => e.stopPropagation()}
          >
            <h2>确认急停全部计算任务？</h2>
            <p className="error">这会请求取消当前所有运行中任务并释放关联的 Colab GPU 运行时。</p>
            <div className="actions">
              <button className="button secondary" type="button" onClick={() => setConfirm(null)}>取消</button>
              <button className="button danger" type="button" onClick={() => void stopCompute()}>确认急停</button>
            </div>
          </section>
        </div>
      )}

      {/* 任务详情抽屉 */}
      {detail && (
        <div className={styles.dialogBackdrop} role="presentation" onClick={() => setDetail(null)}>
          <section
            className={styles.dialog}
            role="dialog"
            aria-modal="true"
            aria-label="任务详情"
            onClick={(e) => e.stopPropagation()}
          >
            <div className={styles.cardHead}>
              <h2>任务详情 · {detail.id.slice(0, 8)}</h2>
              <button className="button secondary" type="button" onClick={() => setDetail(null)}>关闭</button>
            </div>
            <p>
              状态：<strong>{readableState(detail.state)}</strong> · 环境：<strong>{detail.provider}</strong><br />
              创建时间：{String(detail.created_at || '未知')}
            </p>
            {detail.timings ? (
              <details className="foldable" open>
                <summary>阶段耗时记录</summary>
                <div className="foldContent">
                  <pre className={styles.diagnostics}>{JSON.stringify(detail.timings, null, 2)}</pre>
                </div>
              </details>
            ) : null}
            {detail.resolved_params ? (
              <details className="foldable">
                <summary>参数快照</summary>
                <div className="foldContent">
                  <pre className={styles.diagnostics}>{JSON.stringify(detail.resolved_params, null, 2)}</pre>
                </div>
              </details>
            ) : null}
          </section>
        </div>
      )}

      {/* 作品查看器抽屉 */}
      {viewer && (
        <div className={styles.dialogBackdrop} role="presentation" onClick={() => setViewer(null)}>
          <section
            className={styles.dialog}
            role="dialog"
            aria-modal="true"
            aria-label="查看作品"
            onClick={(e) => e.stopPropagation()}
          >
            <div className={styles.cardHead}>
              <h2>{viewer.filename || viewer.id}</h2>
              <button className="button secondary" type="button" onClick={() => setViewer(null)}>关闭</button>
            </div>
            {viewer.mime.startsWith('video/') ? (
              <video className={styles.viewerVideo} src={assetUrl(viewer)} controls autoPlay />
            ) : viewer.mime.startsWith('image/') ? (
              <img className={styles.viewerImage} src={assetUrl(viewer)} alt="" />
            ) : (
              <p className="muted">诊断文件不可直接预览，请下载查看。</p>
            )}
            <div className="actions">
              <button className="button secondary" type="button" onClick={() => void favorite(viewer)}>
                {viewer.favorite ? '取消收藏' : '收藏'}
              </button>
              <button className="button secondary" type="button" onClick={() => void reuseAsset(viewer)}>
                以此参数再来一次
              </button>
              <a className="button secondary" href={assetUrl(viewer)} download={viewer.filename || viewer.id} target="_blank" rel="noreferrer">
                下载产物
              </a>
              <button className="button danger" type="button" onClick={() => void remove(viewer)}>
                删除作品
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
