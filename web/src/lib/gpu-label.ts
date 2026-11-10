type DeviceRecord = {
  gpu_reported?: unknown;
  runtime_spec_requested?: unknown;
  provider?: unknown;
  prep?: unknown;
};

export function gpuLabel(value: DeviceRecord): string {
  if (value.provider === 'mock') return '模拟 GPU';
  if (typeof value.gpu_reported === 'string' && value.gpu_reported.trim()) return `实测 GPU：${value.gpu_reported}`;
  if (value.prep === true) return 'CPU 预热';
  const spec = value.runtime_spec_requested;
  if (spec && typeof spec === 'object' && !Array.isArray(spec)) {
    const { accelerator, variant } = spec as Record<string, unknown>;
    if (typeof accelerator === 'string' && accelerator && accelerator !== 'NONE') return `申请 GPU：${accelerator}（未实测）`;
    if (variant === 'VARIANT_DEFAULT' || variant === 'VARIANT_CPU') return 'CPU';
  }
  return 'GPU 未记录';
}

export function canRetainJobInstance(job: { instance_id?: unknown; instance_state?: unknown; runtime_deleted?: unknown }): boolean {
  return Boolean(job.instance_id) && job.runtime_deleted !== true && ['ready', 'busy', 'retained_idle'].includes(String(job.instance_state || ''));
}
