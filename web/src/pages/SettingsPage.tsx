import { useState } from 'react';
import { WorkflowsPanel } from './WorkflowManager';
import { ThemePicker } from './ComputePage';
import type {
  ComputeSpec, GpuSettings, GpuUsage, GpuWorkflow,
} from '../lib/gpu-api';
import { GoogleOAuthSetup } from '../components/GoogleOAuthSetup';
import { ComputeSpecs } from '../components/ComputeSpecs';
import styles from '../gpu.module.css';
import { formatCu, shown } from '../lib/ui-helpers';

export function SettingsPanel({
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
  onBack,
}: {
  settings: GpuSettings | null;
  usage?: GpuUsage | null;
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
  onBack?: () => void;
}) {
  const [oauthConfigured, setOauthConfigured] = useState(false);

  const activeCount = Number(usage?.active_runtimes ?? 0);
  const dailyCap = Number(usage?.daily_limit || settings?.budget?.daily_units || 200);
  const usedUnits = Number(usage?.actual_units || usage?.estimated_units || 0);
  const pct = Math.min(100, Math.round((usedUnits / (dailyCap || 1)) * 100));

  return (
    <div className={styles.stack}>
      {/* 顶部导航与返回 */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <button className="button secondary" style={{ minHeight: '34px' }} type="button" onClick={onBack}>
          ← 返回工作台
        </button>
        <span className="pill">全站设置大本营</span>
      </div>

      {/* 1. 运行概览与配额 (Matching Image 4) */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>运行概览与配额</h2>
          <span className="pill">配额占用 {pct}%</span>
        </div>

        <div className={styles.statGrid}>
          <div className={styles.stat}>
            <span>运行任务</span>
            <strong style={{ fontSize: '1.25rem' }}>{activeCount}</strong>
          </div>
          <div className={styles.stat}>
            <span>总配额</span>
            <strong style={{ fontSize: '1.25rem' }}>{dailyCap} CU</strong>
          </div>
          <div className={styles.stat}>
            <span>已用配额</span>
            <strong style={{ fontSize: '1.25rem' }}>{formatCu(usedUnits, false)}</strong>
          </div>
          <div className={styles.stat}>
            <span>使用率</span>
            <strong style={{ fontSize: '1.25rem', color: pct > 80 ? 'var(--danger)' : 'var(--accent)' }}>
              {pct}%
            </strong>
          </div>
        </div>
      </section>

      {/* 2. Google 账号与授权连接 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>Google 连接</h2>
          <span className="pill">
            {settings?.offline_mode ? '离线 Mock 保护' : 'OAuth 授权'}
          </span>
        </div>

        {(['colab', 'drive'] as const).map((purpose) => {
          const state = settings?.google?.[purpose];
          const isConnected = Boolean(state?.connected);
          const isConfigured = oauthConfigured || Boolean(state?.configured);

          return (
            <div className={styles.workflowCard} key={purpose} style={{ marginBottom: '8px' }}>
              <div className={styles.workflowHeader}>
                <div>
                  <strong>{purpose === 'colab' ? 'Google Colab 算力' : 'Google Drive 存储'}</strong>
                  <div className={styles.workflowMeta}>
                    <span>{isConnected ? '✓ 授权令牌有效' : isConfigured ? '已填 ClientID' : '尚未绑定'}</span>
                  </div>
                </div>

                <span
                  className="pill"
                  style={{
                    background: isConnected ? 'var(--accent-soft)' : 'var(--panel-strong)',
                    color: isConnected ? 'var(--accent)' : 'var(--text-secondary)',
                    borderColor: isConnected ? 'var(--accent)' : 'var(--line)',
                  }}
                >
                  {isConnected ? '已连接' : isConfigured ? '已配置未连' : '未配置'}
                </span>
              </div>

              <div className="actions" style={{ marginTop: '8px' }}>
                <button
                  className="button secondary"
                  type="button"
                  disabled={settings?.offline_mode === true || (!isConfigured && !isConnected)}
                  onClick={() => onConnect(purpose)}
                >
                  {isConnected ? '重新授权' : `连接 ${purpose === 'colab' ? 'Colab' : 'Drive'}`}
                </button>
                {isConnected && (
                  <button className="button danger" type="button" onClick={() => onDisconnect(purpose)}>
                    断开连接
                  </button>
                )}
              </div>
            </div>
          );
        })}

        {!settings?.offline_mode && (
          <details className="foldable" style={{ marginTop: '6px' }}>
            <summary>OAuth 凭据配置面板</summary>
            <div className="foldContent">
              <GoogleOAuthSetup onConfigured={() => setOauthConfigured(true)} />
            </div>
          </details>
        )}
      </section>

      {/* 3. GPU 资源管理 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>GPU 资源管理</h2>
        </div>
        <div className="actions">
          <button className="button secondary" disabled={loading === 'check'} type="button" onClick={onCheck}>
            {loading === 'check' ? '探查中…' : '检查可用 GPU'}
          </button>
          <button className="button danger" disabled={loading === 'stop'} type="button" onClick={onStop}>
            {loading === 'stop' ? '急停中…' : '停止本地计算任务'}
          </button>
        </div>
        {specs.length ? <ComputeSpecs specs={specs} /> : null}
      </section>

      {/* 4. 工作流与模型管理 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>工作流与模型管理</h2>
          <span className="pill">{workflows.length} 个版本</span>
        </div>
        <WorkflowsPanel
          workflows={workflows}
          onImport={onImport}
          onValidate={onValidate}
          onPublish={onPublish}
          onRollback={onRollback}
        />
      </section>

      {/* 5. 外观与退出登录 */}
      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>界面偏好</h2>
        </div>
        <ThemePicker />
        <div style={{ textAlign: 'center', marginTop: '16px' }}>
          <button className="button danger" style={{ width: '100%' }} type="button" onClick={onLogout}>
            退出工作台登录
          </button>
        </div>
      </section>

      {error && <p className="status error" role="alert">{error}</p>}
    </div>
  );
}
