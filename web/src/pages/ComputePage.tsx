import { useState } from 'react';
import type { GpuSettings, GpuUsage } from '../lib/gpu-api';
import styles from '../gpu.module.css';
import { formatCu, shown } from '../lib/ui-helpers';

export function ComputePage({
  settings,
  usage,
  onStop,
  onCheck,
  loadingStop,
  loadingCheck,
}: {
  settings: GpuSettings | null;
  usage: GpuUsage | null;
  onStop?: () => void;
  onCheck?: () => void;
  loadingStop?: boolean;
  loadingCheck?: boolean;
}) {
  const offline = settings?.offline_mode === true;
  const isMock = offline || !settings?.real_enabled;
  const realEnabled = Boolean(settings?.real_enabled);
  const activeCount = Number(usage?.active_runtimes ?? 0);

  return (
    <div className={styles.stack}>
      {/* 头部标题与说明 */}
      <div className={styles.pageHeader}>
        <h2>⚡ 算力工作台</h2>
        <p className="muted" style={{ margin: '4px 0 0', fontSize: '0.8rem' }}>
          任务在后台推进，手机关闭后仍可回来查看。
        </p>
      </div>

      {/* 1. 当前环境卡片 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <div>
            <h3 style={{ margin: 0, fontSize: '0.95rem', fontWeight: 700 }}>当前环境</h3>
            <span className="muted" style={{ fontSize: '0.75rem' }}>
              {isMock ? '本地 Mock Provider' : 'Google Colab GPU Provider'}
            </span>
          </div>
          <span
            className="pill"
            style={{
              background: realEnabled ? 'var(--accent-soft)' : 'var(--panel-strong)',
              color: realEnabled ? 'var(--accent)' : 'var(--text-secondary)',
              borderColor: realEnabled ? 'var(--accent)' : 'var(--line)',
            }}
          >
            {realEnabled ? '真实 GPU' : '模拟模式'}
          </span>
        </div>

        {/* 三列关键指标（带分割线） */}
        <div className={styles.computeEnvColumns}>
          <div className={styles.envCol}>
            <span className={styles.envColLabel}>真实 GPU</span>
            <span className={styles.envColBadge} data-enabled={realEnabled}>
              {realEnabled ? '已启用' : '未启用'}
            </span>
          </div>

          <div className={styles.envDivider} />

          <div className={styles.envCol}>
            <span className={styles.envColLabel}>活跃运行时</span>
            <strong className={styles.envColValue}>{activeCount}</strong>
          </div>

          <div className={styles.envDivider} />

          <div className={styles.envCol}>
            <span className={styles.envColLabel}>存储</span>
            <strong className={styles.envColValue}>{settings?.storage || 'local'}</strong>
          </div>
        </div>
      </section>

      {/* 2. 算力统计卡片 (2×2 宫格) */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h3 style={{ margin: 0, fontSize: '0.95rem', fontWeight: 700 }}>📊 算力统计</h3>
        </div>

        <div className={styles.statGrid2x2}>
          <div className={styles.statItemBox}>
            <div className={styles.statItemHead}>
              <span>📅 今日估算</span>
            </div>
            <strong className={styles.statValueLarge}>
              {formatCu((usage as Record<string, unknown> | null)?.estimated_units, isMock)}
            </strong>
          </div>

          <div className={styles.statItemBox}>
            <div className={styles.statItemHead}>
              <span>⚡ 实际消耗</span>
            </div>
            <strong className={styles.statValueLarge}>
              {formatCu((usage as Record<string, unknown> | null)?.actual_units, false)}
            </strong>
          </div>

          <div className={styles.statItemBox}>
            <div className={styles.statItemHead}>
              <span>🛡 单日限额</span>
            </div>
            <strong className={styles.statValueLarge}>
              {shown((usage as Record<string, unknown> | null)?.daily_limit || settings?.budget?.daily_units)} CU
            </strong>
          </div>

          <div className={styles.statItemBox}>
            <div className={styles.statItemHead}>
              <span>▶ 活跃运行时</span>
            </div>
            <strong className={styles.statValueLarge}>
              {activeCount}
            </strong>
          </div>
        </div>
      </section>

      {/* 3. 运行策略列表卡片 (3 条策略项) */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h3 style={{ margin: 0, fontSize: '0.95rem', fontWeight: 700 }}>🎛 运行策略</h3>
        </div>

        <div className={styles.strategyRows}>
          <div className={styles.strategyRowItem}>
            <div className={styles.strategyRowLeft}>
              <span className={styles.strategyIcon}>🍃</span>
              <strong>节省</strong>
              <span className={styles.strategyRowDivider}>|</span>
              <span className={styles.strategyRowDesc}>队列结束即释放</span>
            </div>
            <span className={styles.strategyChevron}>›</span>
          </div>

          <div className={styles.strategyRowItem}>
            <div className={styles.strategyRowLeft}>
              <span className={styles.strategyIcon}>🗂</span>
              <strong>批次</strong>
              <span className={styles.strategyRowDivider}>|</span>
              <span className={styles.strategyRowDesc}>兼容任务串行复用</span>
            </div>
            <span className={styles.strategyChevron}>›</span>
          </div>

          <div className={styles.strategyRowItem}>
            <div className={styles.strategyRowLeft}>
              <span className={styles.strategyIcon}>✦</span>
              <strong>智能</strong>
              <span className={styles.strategyRowDivider}>|</span>
              <span className={styles.strategyRowDesc}>短暂保留空闲，可能持续消耗 CU</span>
            </div>
            <span className={styles.strategyChevron}>›</span>
          </div>
        </div>
      </section>

      {/* 4. Mock 提示横条 */}
      <div className={styles.mockNoticeBanner}>
        <span style={{ fontSize: '0.9rem' }}>ℹ</span>
        <span>Mock 数据仅供演示，不代表实际账单。未连接真实账单的费用为模拟估算。</span>
      </div>

      {/* 5. 底部主副操作按钮 (检查可用 GPU & 停止全部任务) */}
      <div className={styles.computeActionRow}>
        <button
          className="button"
          style={{ flex: 1, minHeight: '44px' }}
          disabled={loadingCheck}
          type="button"
          onClick={onCheck}
        >
          {loadingCheck ? '检查中…' : '检查可用 GPU'}
        </button>

        <button
          className="button secondary"
          style={{
            flex: 1,
            minHeight: '44px',
            color: 'var(--danger)',
            borderColor: 'rgba(248, 113, 113, 0.4)',
          }}
          disabled={loadingStop}
          type="button"
          onClick={onStop}
        >
          {loadingStop ? '停止中…' : '⏹ 停止全部任务'}
        </button>
      </div>
    </div>
  );
}

export function ThemePicker() {
  const [theme, setTheme] = useState(() => localStorage.getItem('studio-theme') || 'system');
  function change(value: string) {
    setTheme(value);
    localStorage.setItem('studio-theme', value);
    document.documentElement.dataset.theme = value;
  }
  return (
    <div className="field">
      <label htmlFor="theme-select">界面外观主题</label>
      <select id="theme-select" value={theme} onChange={(e) => change(e.target.value)}>
        <option value="system">跟随系统偏好</option>
        <option value="dark">纯黑极客深色 (Dark)</option>
        <option value="light">清爽专业浅色 (Light)</option>
      </select>
    </div>
  );
}
