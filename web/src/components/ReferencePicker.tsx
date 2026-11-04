import type { GalleryAsset, GpuManifest } from '../lib/gpu-api';
import styles from '../gpu.module.css';

export function referenceAssets(assets: GalleryAsset[]): GalleryAsset[] {
  return assets.filter((asset) => !asset.mock && asset.metadata?.mock !== true && /^(image|video|audio)\//.test(asset.mime));
}

export function supportsReferences(manifest: GpuManifest | null): boolean {
  if (!manifest) return false;
  const declaration = manifest.assets as { required?: unknown[]; optional?: unknown[] } | undefined;
  if (declaration?.required?.length || declaration?.optional?.length) return true;
  const params = Array.isArray(manifest.parameters) ? manifest.parameters : Object.values(manifest.parameters || {});
  return params.some((p) => ['image', 'video', 'file', 'audio'].includes(String(p.type)) || p.widget === 'file');
}

export function ReferencePicker({ manifest, assets, selected, onChange, onUpload }: {
  manifest: GpuManifest | null; assets: GalleryAsset[]; selected: string[];
  onChange: (ids: string[]) => void; onUpload: (file: File) => Promise<void>;
}) {
  if (!manifest) return null;
  if (!supportsReferences(manifest)) return <p className="muted">此工作流不支持参考素材，不会提交图片输入。</p>;
  const available = referenceAssets(assets);
  return <section className={styles.referencePicker} role="group" aria-label="参考素材">
    <div className={styles.projectRow}><strong>参考素材</strong><label className="button secondary">上传素材<input hidden type="file" accept="image/*,video/*,audio/*" onChange={async (event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) await onUpload(file); }} /></label></div>
    <p className="muted">仅显示可用素材；Mock 测试图和诊断文件不作为参考图。</p>
    {available.length ? <div className={styles.referenceList}>{available.map((asset) => <label className={styles.referenceRow} key={asset.id}>
      <input type="checkbox" aria-label={`选用 ${asset.filename || asset.id}`} checked={selected.includes(asset.id)} onChange={(event) => onChange(event.target.checked ? [...new Set([...selected, asset.id])] : selected.filter((id) => id !== asset.id))} />
      {asset.mime.startsWith('image/') ? <img src={asset.thumbnail_url || `/api/assets/${asset.id}/thumbnail`} alt="" loading="lazy" /> : <span className={styles.referenceMedia}>{asset.mime.startsWith('video/') ? '视频' : '音频'}</span>}
      <span className={styles.referenceName}>{asset.filename || asset.id}</span>
    </label>)}</div> : <p className="muted">暂无参考素材，可上传自己的图片。</p>}
  </section>;
}
