import { useState, useMemo } from 'react';
import { assetUrl, type GalleryAsset } from '../lib/gpu-api';
import styles from '../gpu.module.css';

export function GalleryPanel({
  gallery,
  onFavorite,
  onDelete,
  onOpen,
  onReuse,
  onGoCreate,
}: {
  gallery: GalleryAsset[];
  onFavorite: (asset: GalleryAsset) => void;
  onDelete: (asset: GalleryAsset) => void;
  onOpen: (asset: GalleryAsset) => void;
  onReuse?: (asset: GalleryAsset) => void;
  onGoCreate?: () => void;
}) {
  const [filter, setFilter] = useState<'all' | 'image' | 'video' | 'favorite'>('all');
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(24);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState(false);

  const filtered = useMemo(() => {
    return gallery.filter((asset) => {
      const isVideo = asset.mime.startsWith('video/');
      const isImage = asset.mime.startsWith('image/');

      if (filter === 'image' && !isImage) return false;
      if (filter === 'video' && !isVideo) return false;
      if (filter === 'favorite' && !asset.favorite) return false;

      const text = `${asset.filename || ''} ${asset.source_job_id || ''}`.toLowerCase();
      return text.includes(query.toLowerCase());
    });
  }, [gallery, filter, query]);

  const selectedAsset = useMemo(() => {
    return gallery.find((a) => a.id === selectedAssetId) || null;
  }, [gallery, selectedAssetId]);

  return (
    <div className={styles.stack} style={{ position: 'relative' }}>
      {/* 顶部标题行 */}
      <div className={styles.sectionTitle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <h2>🖼 作品画廊</h2>
          <span className="pill">{gallery.length}</span>
        </div>
      </div>

      {/* 搜索框 */}
      <div className="field" style={{ margin: 0 }}>
        <input
          aria-label="搜索作品"
          placeholder="🔍 搜索作品（标题、提示词等）"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setLimit(24);
          }}
        />
      </div>

      {/* 筛选标签条：全部 / 图片 / 视频 / 收藏 */}
      <div className={styles.galleryFilterRow}>
        <button
          type="button"
          className={filter === 'all' ? styles.filterChipActive : styles.filterChip}
          onClick={() => setFilter('all')}
        >
          全部
        </button>
        <button
          type="button"
          className={filter === 'image' ? styles.filterChipActive : styles.filterChip}
          onClick={() => setFilter('image')}
        >
          图片
        </button>
        <button
          type="button"
          className={filter === 'video' ? styles.filterChipActive : styles.filterChip}
          onClick={() => setFilter('video')}
        >
          视频
        </button>
        <button
          type="button"
          className={filter === 'favorite' ? styles.filterChipActive : styles.filterChip}
          onClick={() => setFilter('favorite')}
        >
          ★ 收藏
        </button>
      </div>

      {/* 作品网格 (双列自适应) */}
      {filtered.length ? (
        <div className={styles.galleryDualGrid}>
          {filtered.slice(0, limit).map((asset) => {
            const isVideo = asset.mime.startsWith('video/');
            const isJson = asset.mime.includes('json') || asset.filename?.toLowerCase().endsWith('.json');
            const isSelected = selectedAssetId === asset.id;

            return (
              <article
                className={styles.galleryCardNew}
                key={asset.id}
                data-selected={isSelected}
                onClick={() => {
                  setSelectedAssetId(isSelected ? null : asset.id);
                  setDeleteConfirm(false);
                }}
              >
                {/* 媒体封面与悬浮信息 */}
                <button
                  className={styles.galleryCover}
                  type="button"
                  aria-label={`查看 ${asset.filename || '作品'}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedAssetId(asset.id);
                    onOpen(asset);
                  }}
                >
                  {isVideo ? (
                    <video src={assetUrl(asset)} preload="metadata" muted aria-label={asset.filename || '视频'} />
                  ) : isJson ? (
                    <div className={styles.diagnosticsThumb}>
                      <span>JSON</span>
                      <small>诊断文件</small>
                    </div>
                  ) : (
                    <img loading="lazy" src={assetUrl(asset, true)} alt={asset.filename || '作品'} />
                  )}

                  {/* 左上角媒体类型标 */}
                  <span className={styles.mediaTypePill}>
                    {isVideo ? '视频' : '图片'}
                  </span>

                  {/* 右上角选中复选标或收藏星 */}
                  <div className={styles.coverTopRight}>
                    {isSelected && <span className={styles.selectedCheckmark}>✓</span>}
                  </div>

                  {/* 左下角分辨率/时长 HUD 叠层 */}
                  <span className={styles.mediaHudOverlay}>
                    {isVideo ? '▶ 00:05' : '992 × 992'}
                  </span>
                </button>

                {/* 底部元数据 */}
                <div className={styles.galleryMetaRow}>
                  <div className={styles.galleryTitleGroup}>
                    <strong className={styles.galleryItemTitle} title={asset.filename || asset.id}>
                      {asset.filename || `生成图 · ${asset.id.slice(0, 6)}`}
                    </strong>
                    <span className={styles.galleryItemSub}>
                      {asset.source_job_id ? `任务 ${asset.source_job_id.slice(0, 6)}` : '本地素材'}
                    </span>
                  </div>

                  <button
                    type="button"
                    className={styles.starFavBtn}
                    data-active={Boolean(asset.favorite)}
                    onClick={(e) => {
                      e.stopPropagation();
                      onFavorite(asset);
                    }}
                  >
                    {asset.favorite ? '★' : '☆'}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="empty">
          <span style={{ fontSize: '2rem' }}>🖼</span>
          <strong>暂无生成作品</strong>
          <span>生成成功的图像与视频会自动保存在这里，支持点击预览与一键复用参数。</span>
          {onGoCreate && (
            <button className="button" style={{ marginTop: '8px' }} type="button" onClick={onGoCreate}>
              ✦ 去创作新作品
            </button>
          )}
        </div>
      )}

      {filtered.length > limit && (
        <button
          className="button secondary"
          style={{ width: '100%', marginTop: '12px' }}
          onClick={() => setLimit(limit + 24)}
        >
          加载更多作品
        </button>
      )}

      {/* 底部悬浮操作栏 (Floating Action Bar - 选中作品时触发) */}
      {selectedAsset && (
        <div className={styles.floatingActionBar}>
          <button
            type="button"
            className={styles.fabItem}
            onClick={() => onOpen(selectedAsset)}
          >
            <span style={{ fontSize: '1rem' }}>👁</span>
            <span>预览</span>
          </button>

          <div className={styles.fabDivider} />

          <button
            type="button"
            className={styles.fabItem}
            disabled={!onReuse || !selectedAsset.source_job_id}
            onClick={() => onReuse && onReuse(selectedAsset)}
          >
            <span style={{ fontSize: '1rem' }}>↻</span>
            <span>再次生成</span>
          </button>

          <div className={styles.fabDivider} />

          <button
            type="button"
            className={styles.fabItem}
            style={{ color: deleteConfirm ? 'var(--danger)' : 'inherit' }}
            onClick={() => {
              if (!deleteConfirm) {
                setDeleteConfirm(true);
              } else {
                onDelete(selectedAsset);
                setSelectedAssetId(null);
                setDeleteConfirm(false);
              }
            }}
          >
            <span style={{ fontSize: '1rem' }}>🗑</span>
            <span>{deleteConfirm ? '确认删?' : '删除'}</span>
          </button>
        </div>
      )}
    </div>
  );
}
