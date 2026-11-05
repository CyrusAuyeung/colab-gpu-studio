import { useMemo, useState, useEffect } from 'react';
import type {
  GalleryAsset, GpuManifest, GpuModel, GpuPreset, GpuSettings, GpuWorkflow, Provider,
} from '../lib/gpu-api';
import { GpuParamForm } from '../components/GpuParamForm';
import { ReferencePicker, supportsReferences } from '../components/ReferencePicker';
import styles from '../gpu.module.css';
import { shown } from '../lib/ui-helpers';

type TaskCategory = 'image' | 'video';

const RATIO_PRESETS = [
  { label: '1:1', desc: '方形头像', w: 992, h: 992 },
  { label: '3:4', desc: '竖屏立绘', w: 768, h: 1024 },
  { label: '9:16', desc: '全屏海报', w: 576, h: 1024 },
  { label: '4:3', desc: '经典横版', w: 1024, h: 768 },
  { label: '16:9', desc: '宽屏壁纸', w: 1024, h: 576 },
];

export function CreatePanel({
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
  batchCount,
  setBatchCount,
  batchMode,
  setBatchMode,
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
  batchCount: number;
  setBatchCount: (n: number) => void;
  batchMode: 'save' | 'smart' | 'batch';
  setBatchMode: (m: 'save' | 'smart' | 'batch') => void;
  loading: string;
  error: string;
  onSubmit: () => void;
  onApplyPreset: (preset: GpuPreset) => void;
  onSavePreset: (name: string) => void;
}) {
  const [presetName, setPresetName] = useState('');
  const [showPresetSave, setShowPresetSave] = useState(false);

  // 1. 工作流家族整理
  const workflowFamilies = useMemo(() => {
    const map = new Map<string, { modelId: string; items: GpuWorkflow[]; modality: string; title: string }>();
    workflows.forEach((wf) => {
      const list = map.get(wf.workflow_key)?.items || [];
      list.push(wf);
      list.sort((a, b) => String(b.workflow_version).localeCompare(String(a.workflow_version), undefined, { numeric: true }));

      const isVideo = wf.workflow_key.includes('i2v') || wf.workflow_key.includes('video') || wf.workflow_key.includes('ref2v');
      const modality = isVideo ? 'video' : 'image';
      const model = models.find((m) => m.model_id === wf.model_id);
      const title = model?.display_name || wf.workflow_key;

      map.set(wf.workflow_key, { modelId: wf.model_id, items: list, modality, title });
    });
    return [...map.entries()];
  }, [workflows, models]);

  // 当前选中的活跃工作流
  const activeWorkflow = workflows.find((item) => item.id === workflowId);

  // 创作分类判定
  const currentCategory: TaskCategory = useMemo(() => {
    if (!activeWorkflow) return 'image';
    const key = activeWorkflow.workflow_key.toLowerCase();
    return key.includes('i2v') || key.includes('video') || key.includes('ref2v') ? 'video' : 'image';
  }, [activeWorkflow]);

  const filteredFamilies = useMemo(() => {
    return workflowFamilies.filter(([, fam]) => fam.modality === currentCategory);
  }, [workflowFamilies, currentCategory]);

  function switchCategory(cat: TaskCategory) {
    if (cat === currentCategory) return;
    const candidates = workflowFamilies.filter(([, fam]) => fam.modality === cat);
    if (candidates.length > 0) {
      const targetFam = candidates[0][1];
      const targetWf = targetFam.items.find((i) => i.active) || targetFam.items[0];
      if (targetWf) {
        setModelId(targetWf.model_id);
        setWorkflowId(targetWf.id);
      }
    }
  }

  const currentFamily = activeWorkflow ? workflowFamilies.find(([k]) => k === activeWorkflow.workflow_key)?.[1] : null;
  const currentModel = models.find((m) => m.model_id === currentFamily?.modelId);

  // 画面尺寸状态与联动
  const hasWidth = Boolean(manifest?.parameters && ('width' in manifest.parameters || (Array.isArray(manifest.parameters) && manifest.parameters.some(p => p.key === 'width'))));
  const hasHeight = Boolean(manifest?.parameters && ('height' in manifest.parameters || (Array.isArray(manifest.parameters) && manifest.parameters.some(p => p.key === 'height'))));
  const hasDimensionPair = hasWidth && hasHeight;

  const currentW = Number(params.width) || 992;
  const currentH = Number(params.height) || 992;
  const matchedRatio = RATIO_PRESETS.find((p) => p.w === currentW && p.h === currentH);
  const [selectedRatio, setSelectedRatio] = useState<string>(matchedRatio ? matchedRatio.label : 'custom');

  useEffect(() => {
    const match = RATIO_PRESETS.find((p) => p.w === currentW && p.h === currentH);
    if (match) setSelectedRatio(match.label);
    else setSelectedRatio('custom');
  }, [currentW, currentH]);

  function applyRatio(preset: (typeof RATIO_PRESETS)[number]) {
    setSelectedRatio(preset.label);
    setParams({ ...params, width: preset.w, height: preset.h });
  }

  // 提示词键位
  const promptKey = manifest?.parameters
    ? ('prompt' in manifest.parameters ? 'prompt' : 'caption' in manifest.parameters ? 'caption' : 'prompt')
    : 'prompt';

  const promptValue = String(params[promptKey] ?? '');

  const maxMinutesCap = typeof settings?.budget?.max_runtime_minutes === 'number'
    ? settings.budget.max_runtime_minutes
    : 60;

  if (!projectId) {
    return (
      <div className="empty">
        <strong>请先在顶栏选择或新建项目</strong>
        <span>项目用于归档参数预设、输入素材和输出作品。</span>
      </div>
    );
  }

  return (
    <div className={styles.stack}>
      {/* 1. 创作类型卡片 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>✦ 创作类型</h2>
        </div>
        <div className={styles.categoryTabs}>
          <button
            type="button"
            className={currentCategory === 'image' ? styles.categoryTabActive : styles.categoryTab}
            onClick={() => switchCategory('image')}
          >
            <span>🖼</span>
            <span>图片生成</span>
          </button>
          <button
            type="button"
            className={currentCategory === 'video' ? styles.categoryTabActive : styles.categoryTab}
            onClick={() => switchCategory('video')}
          >
            <span>🎬</span>
            <span>视频生成</span>
          </button>
        </div>
      </section>

      {/* 2. 选用工作流卡片 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>⬡ 工作流</h2>
          {/* 预设管理下拉 */}
          <details style={{ position: 'relative' }}>
            <summary className="pill" style={{ cursor: 'pointer', userSelect: 'none' }}>
              预设管理 ▾
            </summary>
            <div className={styles.presetDropdown}>
              <div className="field" style={{ margin: 0 }}>
                <label>套用预设</label>
                <select
                  defaultValue=""
                  onChange={(e) => {
                    const preset = presets.find((p) => p.id === e.target.value);
                    if (preset) onApplyPreset(preset);
                  }}
                >
                  <option value="">选择已有预设…</option>
                  {presets
                    .filter((p) => p.workflow_version_id === workflowId)
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                </select>
              </div>

              <div style={{ borderTop: '1px solid var(--line)', paddingTop: '8px' }}>
                {!showPresetSave ? (
                  <button
                    className="button secondary"
                    style={{ width: '100%', minHeight: '34px' }}
                    type="button"
                    onClick={() => setShowPresetSave(true)}
                  >
                    + 存为新预设
                  </button>
                ) : (
                  <div style={{ display: 'grid', gap: '8px' }}>
                    <input
                      className="fieldInput"
                      style={{ minHeight: '34px' }}
                      placeholder="预设名称"
                      value={presetName}
                      onChange={(e) => setPresetName(e.target.value)}
                    />
                    <div className="actions">
                      <button
                        className="button"
                        style={{ flex: 1, minHeight: '32px' }}
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
                      <button
                        className="button secondary"
                        style={{ minHeight: '32px' }}
                        type="button"
                        onClick={() => setShowPresetSave(false)}
                      >
                        取消
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </details>
        </div>

        {/* 内嵌紧凑工作流展示与切换 */}
        <div className={styles.workflowSelectorBlock}>
          <div className="field" style={{ margin: 0, flex: 1 }}>
            <label htmlFor="workflow-family-select">选择具体流程</label>
            <select
              id="workflow-family-select"
              aria-label="选用工作流"
              value={activeWorkflow?.workflow_key || ''}
              onChange={(e) => {
                const targetFam = workflowFamilies.find(([k]) => k === e.target.value)?.[1];
                if (targetFam) {
                  const targetWf = targetFam.items.find((i) => i.active) || targetFam.items[0];
                  if (targetWf) {
                    setModelId(targetWf.model_id);
                    setWorkflowId(targetWf.id);
                  }
                }
              }}
            >
              {filteredFamilies.map(([key, fam]) => (
                <option key={key} value={key}>
                  {fam.title} ({key})
                </option>
              ))}
            </select>
          </div>

          {currentFamily && currentFamily.items.length > 1 && (
            <div className="field" style={{ margin: 0, width: '110px' }}>
              <label htmlFor="workflow-version-select">版本</label>
              <select
                id="workflow-version-select"
                aria-label="切换版本"
                value={workflowId}
                onChange={(e) => {
                  const target = currentFamily.items.find((i) => i.id === e.target.value);
                  if (target) {
                    setModelId(target.model_id);
                    setWorkflowId(target.id);
                  }
                }}
              >
                {currentFamily.items.map((i) => (
                  <option key={i.id} value={i.id}>
                    v{i.workflow_version} {i.active ? '· 活跃' : ''}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        {activeWorkflow && (
          <div className={styles.workflowSummaryLine}>
            <span className={styles.wfMetaBadge}>底座：{currentModel?.display_name || activeWorkflow.model_id}</span>
            <span className={styles.wfMetaBadge}>版本：v{activeWorkflow.workflow_version}</span>
            {currentCategory === 'video' && <span className={styles.wfMetaBadge}>耗显存任务</span>}
          </div>
        )}
      </section>

      {/* 3. 提示词卡片 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>✎ 提示词</h2>
        </div>
        <div className="field" style={{ margin: 0 }}>
          <textarea
            aria-label="提示词"
            value={promptValue}
            placeholder="描述你想创作的画面、风格、主体与细节……"
            rows={3}
            onChange={(e) => setParams({ ...params, [promptKey]: e.target.value })}
          />
        </div>
      </section>

      {/* 4. 基础参数卡片 (尺寸、数量) */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>🎛 基础参数</h2>
          {currentCategory === 'image' && (
            <span className={styles.sizeDisplayBadge}>
              {currentW} × {currentH} px
            </span>
          )}
        </div>

        {/* 画面比例快捷胶囊 (图片生成模式下始终呈现) */}
        {currentCategory === 'image' && (
          <div className={styles.ratioChips} style={{ marginBottom: '10px' }}>
            {RATIO_PRESETS.map((p) => {
              const active = selectedRatio === p.label;
              return (
                <button
                  key={p.label}
                  type="button"
                  className={active ? styles.ratioChipActive : styles.ratioChip}
                  onClick={() => applyRatio(p)}
                >
                  <strong>{p.label}</strong>
                  <span>{p.w}×{p.h}</span>
                </button>
              );
            })}
            <button
              type="button"
              className={selectedRatio === 'custom' ? styles.ratioChipActive : styles.ratioChip}
              onClick={() => setSelectedRatio('custom')}
            >
              <strong>自定义</strong>
              <span>微调</span>
            </button>
          </div>
        )}

        {/* 并排输入行：宽、高、生成数量 */}
        <div style={{ display: 'grid', gridTemplateColumns: currentCategory === 'image' ? '1fr 1fr 1fr' : '1fr', gap: '8px', alignItems: 'end' }}>
          {currentCategory === 'image' && (
            <div className="field" style={{ margin: 0 }}>
              <label>宽</label>
              <input
                type="number"
                min={256}
                max={2048}
                step={16}
                value={String(params.width ?? 992)}
                onChange={(e) => {
                  setSelectedRatio('custom');
                  setParams({ ...params, width: Number(e.target.value) || 0 });
                }}
              />
            </div>
          )}

          {currentCategory === 'image' && (
            <div className="field" style={{ margin: 0 }}>
              <label>高</label>
              <input
                type="number"
                min={256}
                max={2048}
                step={16}
                value={String(params.height ?? 992)}
                onChange={(e) => {
                  setSelectedRatio('custom');
                  setParams({ ...params, height: Number(e.target.value) || 0 });
                }}
              />
            </div>
          )}

          <div className="field" style={{ margin: 0 }}>
            <label>生成数量</label>
            <div className={styles.stepperControl}>
              <button
                type="button"
                onClick={() => setBatchCount(Math.max(1, batchCount - 1))}
              >
                -
              </button>
              <input
                aria-label="生成数量"
                type="number"
                min={1}
                max={100}
                value={batchCount}
                onChange={(e) => setBatchCount(Math.max(1, Math.min(100, Number(e.target.value) || 1)))}
              />
              <button
                type="button"
                onClick={() => setBatchCount(Math.min(100, batchCount + 1))}
              >
                +
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* 5. 高级参数 / LoRA / 负向提示词 (折叠手风琴) */}
      {manifest && (
        <section className={styles.card}>
          <div className={styles.cardHead}>
            <h2>⚙ 详细参数与素材</h2>
          </div>

          <GpuParamForm
            manifest={manifest}
            values={params}
            assets={gallery}
            uploadProgress={uploadProgress}
            onChange={(k, v) => setParams({ ...params, [k]: v })}
            onUpload={onUpload}
            excludeKeys={hasDimensionPair ? [promptKey, 'width', 'height'] : [promptKey]}
          />

          {supportsReferences(manifest) && (
            <div style={{ marginTop: '12px' }}>
              <ReferencePicker
                manifest={manifest}
                assets={gallery}
                selected={selectedAssets}
                onChange={setSelectedAssets}
                onUpload={onUpload}
              />
            </div>
          )}
        </section>
      )}

      {/* 6. 运行设置卡片 (必须在立即生成之前！) */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>▶ 运行设置</h2>
          <span className="pill">{runtime === 'colab' ? '真实 GPU' : 'Mock 模拟'}</span>
        </div>

        {/* 运行环境切换双大按钮 */}
        <div className={styles.segmentedControl}>
          <button
            type="button"
            className={runtime === 'mock' ? styles.segmentedActive : styles.segmentedItem}
            onClick={() => setRuntime('mock')}
          >
            离线模拟 (Mock)
          </button>
          <button
            type="button"
            className={runtime === 'colab' ? styles.segmentedActive : styles.segmentedItem}
            disabled={!settings?.real_enabled}
            onClick={() => setRuntime('colab')}
          >
            Colab GPU {settings?.real_enabled ? '' : '（未启用）'}
          </button>
        </div>

        {/* 运行策略下拉 */}
        <div className="field" style={{ marginTop: '12px' }}>
          <label htmlFor="batch-mode-select">运行策略</label>
          <select
            id="batch-mode-select"
            aria-label="运行策略"
            value={batchMode}
            onChange={(e) => setBatchMode(e.target.value as 'save' | 'smart' | 'batch')}
          >
            <option value="save">节省模式 · 队列结束即释放运行时</option>
            <option value="batch">批次复用 · 兼容同批任务串行复用</option>
            <option value="smart">智能模式 · 短暂保留 120s 空闲待命</option>
          </select>
        </div>

        {runtime === 'colab' && (
          <div className="field" style={{ marginTop: '8px' }}>
            <label>单任务超时（分钟）</label>
            <input
              type="number"
              min={5}
              max={maxMinutesCap}
              value={maxRuntime}
              onChange={(e) => setMaxRuntime(Number(e.target.value))}
            />
          </div>
        )}

        <p className="muted" style={{ fontSize: '0.75rem', margin: '6px 0 16px', lineHeight: 1.5 }}>
          {runtime === 'mock'
            ? '✓ Mock 离线演示模式，零 CU 扣除。Seed ≥ 0 时多批次按序递增。'
            : `Colab GPU 模式，日限额保护：${shown(settings?.budget?.daily_units)} CU。`}
        </p>

        {/* 7. 立即生成通栏主操作按钮 */}
        <button
          className="button"
          style={{
            width: '100%',
            minHeight: '50px',
            fontSize: '1.05rem',
            fontWeight: 700,
            letterSpacing: '0.02em',
          }}
          disabled={!workflowId || loading === 'submit'}
          type="button"
          onClick={onSubmit}
        >
          {loading === 'submit' ? '提交任务中…' : '✦ 立即生成'}
        </button>

        {error && <p className="status error" role="alert">{error}</p>}
      </section>
    </div>
  );
}
