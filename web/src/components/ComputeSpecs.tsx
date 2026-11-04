import type { ComputeSpec } from '../lib/gpu-api';

export function ComputeSpecs({ specs }: { specs: ComputeSpec[] }) {
  return <div>{specs.map((spec, index) => {
    const key = spec.key;
    if (key && typeof key === 'object' && !Array.isArray(key)) {
      const value = key as Record<string, unknown>;
      const variant = String(value.variant || '');
      if (!['VARIANT_GPU', 'GPU'].includes(variant)) return null;
      const accelerator = String(value.accelerator || '未知 GPU');
      const shape = ['SHAPE_HIGHMEM', 'HIGHMEM'].includes(String(value.shape)) ? '高内存' : '标准内存';
      return <p className="pill" key={JSON.stringify(key)}>{accelerator} · {shape} · {spec.eligible === true ? '有申请资格' : '未确认申请资格'}</p>;
    }
    return <p className="pill" key={spec.id || spec.name || index}>{spec.name || spec.gpu || 'GPU'} · {spec.available === true ? '可用' : spec.available === false ? '不可用' : '状态未知'}{spec.reason ? ` · ${spec.reason}` : ''}</p>;
  })}<p className="muted">申请资格不保证实时库存；查询不会创建运行时。未确认资格的设备不会被自动选择。</p></div>;
}
