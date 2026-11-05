import { useMemo, useRef, useState, useEffect } from 'react';
import type { GpuManifest, GpuParameter, GalleryAsset } from '../lib/gpu-api';
import styles from './gpu-param-form.module.css';

type Props = {
  manifest?: GpuManifest | null;
  values: Record<string, unknown>;
  assets: GalleryAsset[];
  uploadProgress: Record<string, number>;
  onChange: (key: string, value: unknown) => void;
  onUpload: (file: File) => Promise<void>;
  excludeKeys?: string[];
};

function parametersOf(manifest?: GpuManifest | null): GpuParameter[] {
  const raw = manifest?.parameters;
  if (Array.isArray(raw)) return raw;
  if (!raw || typeof raw !== 'object') return [];
  return Object.entries(raw).map(([key, parameter]) => {
    const normalized = parameter as GpuParameter;
    return { ...normalized, key, id: normalized.id || key };
  });
}

function keyOf(parameter: GpuParameter, index: number) {
  return parameter.key || parameter.id || parameter.name || `parameter_${index + 1}`;
}

function labelOf(parameter: GpuParameter, key: string) {
  return parameter.label || parameter.name || key;
}

function optionOf(option: unknown): { value: string; label: string } {
  if (typeof option === 'object' && option !== null && 'value' in option) {
    return {
      value: String(option.value),
      label: 'label' in option && option.label ? String(option.label) : String(option.value),
    };
  }
  return { value: String(option), label: String(option) };
}

function initialValue(parameter: GpuParameter): unknown {
  if (parameter.default !== undefined) return parameter.default;
  if (parameter.widget === 'checkbox' || parameter.type === 'boolean') return false;
  if (parameter.type === 'integer' || parameter.type === 'number' || parameter.widget === 'number') return parameter.min ?? 0;
  return '';
}

// 常见画面比例与分辨率对照表
const RATIO_PRESETS = [
  { label: '1:1', desc: '方形头像/插画', w: 992, h: 992 },
  { label: '3:4', desc: '纵向壁纸/立绘', w: 768, h: 1024 },
  { label: '9:16', desc: '移动端全屏海报', w: 576, h: 1024 },
  { label: '4:3', desc: '经典横幅', w: 1024, h: 768 },
  { label: '16:9', desc: '宽屏横屏', w: 1024, h: 576 },
];

export function GpuParamForm({ manifest, values, assets, uploadProgress, onChange, onUpload, excludeKeys = [] }: Props) {
  const inputRefs = useRef<Record<string, HTMLInputElement | null>>({});
  const parameters = useMemo(() => parametersOf(manifest), [manifest]);

  // 检测是否存在可配对的宽高
  const hasWidth = parameters.some((p) => (p.key || p.id || p.name) === 'width');
  const hasHeight = parameters.some((p) => (p.key || p.id || p.name) === 'height');
  const hasDimensionPair = hasWidth && hasHeight;

  // 判断当前比例
  const currentW = Number(values.width) || 992;
  const currentH = Number(values.height) || 992;
  const matchedPreset = RATIO_PRESETS.find((p) => p.w === currentW && p.h === currentH);
  const [selectedRatio, setSelectedRatio] = useState<string>(matchedPreset ? matchedPreset.label : 'custom');

  useEffect(() => {
    const match = RATIO_PRESETS.find((p) => p.w === currentW && p.h === currentH);
    if (match) setSelectedRatio(match.label);
    else setSelectedRatio('custom');
  }, [currentW, currentH]);

  function applyRatio(preset: (typeof RATIO_PRESETS)[number]) {
    setSelectedRatio(preset.label);
    onChange('width', preset.w);
    onChange('height', preset.h);
  }

  // 参数智能分层：提示词等核心参数放在前台，运行细节与步数、种子等收纳进高级参数
  const { coreParams, advancedParams } = useMemo(() => {
    const core: Array<{ param: GpuParameter; index: number }> = [];
    const adv: Array<{ param: GpuParameter; index: number }> = [];

    parameters.forEach((param, index) => {
      const key = keyOf(param, index);
      if (excludeKeys.includes(key)) {
        return;
      }
      // width and height are handled specially if both exist
      if (hasDimensionPair && (key === 'width' || key === 'height')) {
        return;
      }

      const isPrompt = key.includes('prompt') || key.includes('caption');
      const isExplicitAdv = Boolean(param.advanced) || param.group === 'advanced';
      const isTechDetail = ['steps', 'seed', 'cfg_scale', 'sampler_name', 'scheduler', 'denoise', 'refiner_steps', 'clip_skip'].includes(key);

      if (isPrompt || (!isExplicitAdv && !isTechDetail)) {
        core.push({ param, index });
      } else {
        adv.push({ param, index });
      }
    });

    return { coreParams: core, advancedParams: adv };
  }, [parameters, hasDimensionPair, excludeKeys]);

  function renderField(parameter: GpuParameter, index: number) {
    const key = keyOf(parameter, index);
    const label = labelOf(parameter, key);
    const current = values[key] === undefined ? initialValue(parameter) : values[key];
    const options = (parameter.options || []).map(optionOf);
    const widget =
      parameter.widget ||
      (parameter.type === 'text'
        ? 'textarea'
        : parameter.type === 'boolean'
        ? 'checkbox'
        : parameter.type === 'integer' || parameter.type === 'number'
        ? 'number'
        : parameter.type === 'image' || parameter.type === 'video' || parameter.type === 'file'
        ? 'file'
        : options.length
        ? 'select'
        : 'text');

    const hasRange = parameter.min !== undefined || parameter.max !== undefined;
    const rangeText = hasRange ? ` · 范围 ${parameter.min ?? 0} ~ ${parameter.max ?? '∞'}` : '';

    return (
      <div className={styles.field} key={key}>
        <label htmlFor={`gpu-${key}`}>
          <span>
            {label}
            {parameter.required && <strong className={styles.requiredMark}>*</strong>}
          </span>
          {rangeText && <span className={styles.rangeBadge}>{rangeText}</span>}
        </label>

        {widget === 'textarea' ? (
          <textarea
            id={`gpu-${key}`}
            value={String(current ?? '')}
            placeholder={key.includes('prompt') ? '描述你想创作的画面、风格、主体与细节……' : '输入文本…'}
            rows={key.includes('prompt') ? 3 : 2}
            onChange={(event) => onChange(key, event.target.value)}
          />
        ) : widget === 'select' ? (
          <select id={`gpu-${key}`} value={String(current ?? '')} onChange={(event) => onChange(key, event.target.value)}>
            <option value="">请选择</option>
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        ) : widget === 'checkbox' ? (
          <label className={styles.checkbox}>
            <input
              id={`gpu-${key}`}
              type="checkbox"
              checked={Boolean(current)}
              onChange={(event) => onChange(key, event.target.checked)}
            />
            <span>启用 {label}</span>
          </label>
        ) : widget === 'number' ? (
          <input
            id={`gpu-${key}`}
            type="number"
            value={String(current ?? '')}
            min={parameter.min}
            max={parameter.max}
            step={parameter.step || (parameter.type === 'integer' ? 1 : 'any')}
            onChange={(event) =>
              onChange(
                key,
                parameter.type === 'integer'
                  ? event.target.value === ''
                    ? ''
                    : Number.parseInt(event.target.value, 10)
                  : event.target.value === ''
                  ? ''
                  : Number(event.target.value)
              )
            }
          />
        ) : widget === 'file' ? (
          <div className={styles.fileControl}>
            <select id={`gpu-${key}`} value={String(current ?? '')} onChange={(event) => onChange(key, event.target.value)}>
              <option value="">选择已有素材库文件…</option>
              {assets
                .filter((asset) => !parameter.type || parameter.type === 'file' || asset.mime.startsWith(`${parameter.type}/`))
                .map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.filename || asset.id}
                  </option>
                ))}
            </select>
            <button type="button" className="button secondary" onClick={() => inputRefs.current[key]?.click()}>
              本地上传
            </button>
            <input
              ref={(node) => {
                inputRefs.current[key] = node;
              }}
              type="file"
              hidden
              accept={parameter.type === 'image' ? 'image/*' : parameter.type === 'video' ? 'video/*' : undefined}
              onChange={async (event) => {
                const file = event.target.files?.[0];
                if (file) await onUpload(file);
                event.target.value = '';
              }}
            />
            {Object.entries(uploadProgress).map(([name, progress]) => (
              <small className={styles.hint} key={name}>
                上传 {name} · {progress}%
              </small>
            ))}
          </div>
        ) : (
          <input id={`gpu-${key}`} type="text" value={String(current ?? '')} onChange={(event) => onChange(key, event.target.value)} />
        )}

        {parameter.description && <span className={styles.hint}>{parameter.description}</span>}
      </div>
    );
  }

  if (!manifest) {
    return (
      <div className="empty">
        <strong>尚未读取工作流参数</strong>
        <span>选择可用工作流后，将自动加载对应参数表单。</span>
      </div>
    );
  }

  if (!parameters.length) {
    return (
      <div className="empty">
        <strong>此工作流没有可编辑参数</strong>
        <span>无需调整参数，可直接配置运行并生成。</span>
      </div>
    );
  }

  return (
    <div className={styles.form}>
      {/* 1. 核心主要参数（提示词等） */}
      {coreParams.map(({ param, index }) => renderField(param, index))}

      {/* 2. 画面尺寸选择（如果有宽高配对参数） */}
      {hasDimensionPair && (
        <div className={styles.sizeSection}>
          <label className={styles.sizeSectionLabel}>
            <span>画面尺寸与画幅比例</span>
            <span className={styles.sizeDisplayBadge}>
              {currentW} × {currentH} px
            </span>
          </label>

          {/* 快捷比例胶囊条 */}
          <div className={styles.ratioChips}>
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

          {/* 紧凑双列宽高输入 */}
          <div className={styles.dimensionInputs}>
            <div className={styles.dimInputItem}>
              <span className={styles.dimPrefix}>宽</span>
              <input
                type="number"
                min={256}
                max={2048}
                step={16}
                value={String(values.width ?? 992)}
                onChange={(e) => {
                  setSelectedRatio('custom');
                  onChange('width', Number(e.target.value) || 0);
                }}
              />
              <span className={styles.dimUnit}>px</span>
            </div>
            <span className={styles.dimCross}>×</span>
            <div className={styles.dimInputItem}>
              <span className={styles.dimPrefix}>高</span>
              <input
                type="number"
                min={256}
                max={2048}
                step={16}
                value={String(values.height ?? 992)}
                onChange={(e) => {
                  setSelectedRatio('custom');
                  onChange('height', Number(e.target.value) || 0);
                }}
              />
              <span className={styles.dimUnit}>px</span>
            </div>
          </div>
        </div>
      )}

      {/* 3. 高级参数与运行细节收纳手风琴 */}
      {advancedParams.length > 0 && (
        <details className={styles.advanced}>
          <summary>
            <span>高级参数 / 采样步数 / 种子与细节</span>
            <span className="pill">{advancedParams.length} 项参数</span>
          </summary>
          <div className={styles.advancedContent}>
            {advancedParams.map(({ param, index }) => renderField(param, index))}
          </div>
        </details>
      )}
    </div>
  );
}
