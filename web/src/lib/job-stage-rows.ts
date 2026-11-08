const stages: Array<[string, string]> = [
  ['queued', '排队等待'], ['queue', '排队等待'], ['prep_cache', '准备共享缓存'],
  ['allocate', '分配算力'], ['restore_env', '恢复运行环境'],
  ['restore_weights', '恢复模型权重'], ['warm_reuse', '复用已就绪环境'],
  ['start_comfy', '启动 ComfyUI'], ['comfy_start', '启动 ComfyUI'],
  ['sampling', '采样生成'], ['upload', '保存产物'], ['cleanup', '释放算力'], ['delete', '释放算力'],
  ['prep_env', '准备运行环境'], ['prep_weights', '准备模型权重'],
];

export function jobStageRows(value: unknown): Array<{key: string; label: string; seconds: number}> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
  const data = value as Record<string, unknown>;
  const labels = new Map(stages);
  const keys = [...stages.map(([key]) => key), ...Object.keys(data).filter((key) => !labels.has(key))];
  return keys.flatMap((key) => {
    const seconds = data[key];
    return typeof seconds === 'number' && Number.isFinite(seconds) && seconds >= 0
      ? [{ key, label: labels.get(key) || key, seconds }] : [];
  });
}
