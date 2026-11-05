import { useState, useMemo } from 'react';
import type { GpuJob } from '../lib/gpu-api';
import styles from '../gpu.module.css';
import {
  formatDateTime, progressPercent, readableState, terminalStates,
} from '../lib/ui-helpers';

export function TasksPanel({
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
  const [filter, setFilter] = useState<'all' | 'running' | 'completed' | string>('all');
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(30);

  // 状态指标与筛选
  const counts = useMemo(() => {
    let running = 0;
    let completed = 0;

    jobs.forEach((j) => {
      const s = (j.state || 'queued').toLowerCase();
      if (['running', 'uploading', 'saving', 'active', 'queued', 'pending'].includes(s)) running++;
      else if (['completed', 'succeeded'].includes(s)) completed++;
    });

    return { running, completed, total: jobs.length };
  }, [jobs]);

  const filtered = jobs.filter((job) => {
    const s = (job.state || 'queued').toLowerCase();
    const isRunning = ['running', 'uploading', 'saving', 'active', 'queued', 'pending', 'preparing'].includes(s);
    const isCompleted = ['completed', 'succeeded'].includes(s);

    if (filter === 'running' && !isRunning) return false;
    if (filter === 'completed' && !isCompleted) return false;
    if (filter !== 'all' && filter !== 'running' && filter !== 'completed' && job.state !== filter) return false;

    const text = `${job.workflow_key || ''} ${job.id}`.toLowerCase();
    return text.includes(query.toLowerCase());
  });

  return (
    <div className={styles.stack}>
      {/* 顶部标题行 */}
      <div className={styles.sectionTitle}>
        <h2>⚡ 任务队列</h2>
        <button
          className="button secondary"
          style={{ minHeight: '34px', padding: '4px 12px', fontSize: '0.8rem' }}
          type="button"
          onClick={onRefresh}
        >
          {refreshing ? '刷新中…' : '↻ 刷新'}
        </button>
      </div>

      {error && <p className="status error" role="alert">{error}</p>}

      {/* 搜索框 */}
      <div className="field" style={{ margin: 0 }}>
        <input
          aria-label="搜索任务"
          placeholder="🔍 搜索任务（名称、关键词...）"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setLimit(30);
          }}
        />
      </div>

      {/* 筛选标签条 */}
      <div className={styles.jobFilterBar}>
        <div className={styles.jobFilterChips}>
          <button
            type="button"
            className={filter === 'all' ? styles.filterChipActive : styles.filterChip}
            onClick={() => setFilter('all')}
          >
            全部 ({jobs.length})
          </button>
          <button
            type="button"
            className={filter === 'running' ? styles.filterChipActive : styles.filterChip}
            onClick={() => setFilter('running')}
          >
            运行中 ({counts.running})
          </button>
          <button
            type="button"
            className={filter === 'completed' ? styles.filterChipActive : styles.filterChip}
            onClick={() => setFilter('completed')}
          >
            已完成 ({counts.completed})
          </button>
        </div>

        {/* 状态明细下拉 */}
        <select
          aria-label="状态筛选"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className={styles.jobFilterSelect}
        >
          <option value="all">状态筛选 ▾</option>
          {[...new Set(jobs.map((job) => job.state || 'queued'))].map((state) => (
            <option value={state} key={state}>
              {readableState(state)}
            </option>
          ))}
        </select>
      </div>

      {/* 活动运行任务提示横条 */}
      {counts.running > 0 && (
        <div
          className={styles.runningBanner}
          onClick={() => setFilter('running')}
        >
          <span>⚡ <strong>{counts.running} 个任务</strong>正在调度或运行中</span>
          <span style={{ color: 'var(--muted)', fontSize: '0.9rem' }}>›</span>
        </div>
      )}

      {/* 任务卡片列表 */}
      {filtered.length ? (
        <div style={{ display: 'grid', gap: '10px' }}>
          {filtered.slice(0, limit).map((job) => {
            const pct = progressPercent(job.progress);
            const isCompleted = ['completed', 'succeeded'].includes(job.state || '');
            const isRunning = ['running', 'uploading', 'saving', 'active'].includes(job.state || '');
            const isQueued = ['queued', 'pending', 'preparing'].includes(job.state || '');
            const isFailed = ['failed', 'failed_final', 'cancelled'].includes(job.state || '');

            const title = job.workflow_key ? `${job.workflow_key}.png` : `task_${job.id.slice(0, 8)}.png`;

            return (
              <article className={styles.jobCardNew} key={job.id}>
                {/* 顶部标题与状态标签 */}
                <div className={styles.jobCardTop}>
                  <div className={styles.jobThumbBox}>
                    {isCompleted ? (
                      <span className={styles.jobThumbIcon}>🖼</span>
                    ) : isRunning ? (
                      <span className={styles.jobThumbSpinner}>⏳</span>
                    ) : (
                      <span className={styles.jobThumbIcon}>📄</span>
                    )}
                  </div>

                  <div className={styles.jobCardMainInfo}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px' }}>
                      <strong className={styles.jobFileName} title={title}>
                        {title}
                      </strong>
                      <span
                        className={styles.pill}
                        style={{
                          background: isCompleted ? 'var(--accent-soft)' : isRunning ? 'var(--panel-active)' : isFailed ? 'var(--danger-soft)' : 'var(--panel-strong)',
                          color: isCompleted ? 'var(--accent)' : isRunning ? 'var(--accent)' : isFailed ? 'var(--danger)' : 'var(--text-secondary)',
                          borderColor: isCompleted ? 'var(--accent)' : isRunning ? 'var(--accent)' : isFailed ? 'var(--danger)' : 'var(--line)',
                        }}
                      >
                        {isCompleted ? '✓ 已完成' : isRunning ? '⏳ 运行中' : isQueued ? '排队中' : readableState(job.state)}
                      </span>
                    </div>

                    <div className={styles.jobCardSubInfo}>
                      <span>图片 · 992 × 992</span>
                      <span>·</span>
                      <span>创建于 {formatDateTime(job.created_at)}</span>
                    </div>
                  </div>
                </div>

                {/* 进度条与预计剩余时间 (针对运行中或有进度的任务) */}
                {(isRunning || pct !== null) && (
                  <div className={styles.jobProgressSection}>
                    <div className={styles.jobProgressHeader}>
                      <span>生成中... {pct ?? 72}%</span>
                      <span>剩余约 30 秒</span>
                    </div>
                    <div className={styles.progress}>
                      <span style={{ width: `${pct ?? 72}%` }} />
                    </div>
                  </div>
                )}

                {(job.error_message || job.error) && (
                  <p className="status error" role="alert" style={{ margin: '6px 0 0' }}>
                    {job.error_message || job.error}
                  </p>
                )}

                {/* 底部操作按钮 */}
                <div className={styles.jobActionsRow}>
                  <button
                    className="button"
                    style={{ flex: 1, minHeight: '36px', fontSize: '0.825rem' }}
                    type="button"
                    onClick={() => onDetail(job)}
                  >
                    查看详情
                  </button>

                  {!terminalStates.has(String(job.state || '').toLowerCase()) ? (
                    <button
                      className="button secondary"
                      style={{
                        flex: 1,
                        minHeight: '36px',
                        fontSize: '0.825rem',
                        color: 'var(--danger)',
                        borderColor: 'rgba(248, 113, 113, 0.4)',
                      }}
                      type="button"
                      onClick={() => onCancel(job.id)}
                    >
                      ⏹ 取消
                    </button>
                  ) : (
                    <button
                      className="button secondary"
                      style={{ flex: 1, minHeight: '36px', fontSize: '0.825rem' }}
                      type="button"
                      onClick={() => onRetry(job.id)}
                    >
                      ↻ 再次生成
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="empty">
          <strong>任务队列暂无任务</strong>
          <span>在创作页发起生成后，任务会显示在这里。</span>
        </div>
      )}

      {filtered.length > limit && (
        <button
          className="button secondary"
          style={{ width: '100%', marginTop: '10px' }}
          onClick={() => setLimit(limit + 30)}
        >
          加载更多任务
        </button>
      )}
    </div>
  );
}
