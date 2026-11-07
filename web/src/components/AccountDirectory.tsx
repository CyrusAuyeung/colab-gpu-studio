import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { RefreshCw } from 'lucide-react';
import { apiMessage, createGoogleAccount, disconnectGoogleAccount, getGoogleAccounts, startGoogleAccountAuth, updateGoogleAccount } from '../lib/gpu-api';
import styles from './v020.module.css';

export type AccountStatus = 'connected' | 'needs_reauth' | 'disabled' | 'error' | string;
export type AuthorizationStatus = 'connected' | 'needs_reauth' | 'disabled' | 'error' | 'not_connected' | string;

export interface GpuAccount {
  account_id: string;
  alias: string;
  status: AccountStatus;
  colab?: { status?: AuthorizationStatus; scopes?: string[]; checked_at?: string; expires_at?: string };
  drive?: { status?: AuthorizationStatus; scopes?: string[]; checked_at?: string; expires_at?: string };
  colab_status?: AuthorizationStatus;
  drive_status?: AuthorizationStatus;
  drive_account_id?: string | null;
  drive_account_alias?: string | null;
  authorization?: { colab?: { connected?: boolean; configured?: boolean; expires_at?: string | null; message?: string }; drive?: { connected?: boolean; configured?: boolean; expires_at?: string | null; message?: string; delegated_to?: string; delegated_alias?: string } };
  allow_allocation?: boolean;
  enabled?: boolean;
  budget?: number | Record<string, unknown> | null;
  budget_limit?: number | null;
  strategy?: string;
  instance_count?: number;
  active_instance_count?: number;
  active_instances?: number;
  active_tasks?: number;
  last_checked_at?: string;
  last_error?: string;
  email_masked?: string;
  [key: string]: unknown;
}

export interface GpuInstance {
  /** Backend presents the durable resource id as `id`; instance_id is legacy input compatibility. */
  id?: string;
  instance_id?: string;
  instance_label?: string;
  label?: string;
  account_id?: string | null;
  account_alias_snapshot?: string;
  provider?: 'mock' | 'colab' | string;
  state?: string;
  lifecycle?: string;
  gpu_reported?: string;
  runtime_spec_reported?: string | Record<string, unknown>;
  current_job_id?: string | null;
  current_job_title?: string;
  created_at?: string;
  last_seen_at?: string;
  retained_until?: string | null;
  idle_deadline_at?: string | null;
  hard_deadline_at?: string | null;
  estimated_units?: number | null;
  actual_units?: number | null;
  unit_rate?: number | null;
  verified?: boolean;
  runtime_verified?: boolean;
  last_error_code?: string;
  last_error?: string;
  [key: string]: unknown;
}

export interface GpuInstanceJob {
  id: string;
  title?: string;
  state?: string;
  progress?: number;
  created_at?: string;
  updated_at?: string;
  account_alias_snapshot?: string;
  [key: string]: unknown;
}

export interface GpuInstanceEvent {
  id?: string;
  event_type?: string;
  state?: string;
  message?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface AccountDirectoryApi {
  getGpuAccounts: () => Promise<GpuAccount[]>;
  createGpuAccount: (body: { alias: string }, idempotencyKey: string) => Promise<GpuAccount>;
  updateGpuAccount: (accountId: string, patch: Record<string, unknown>, idempotencyKey: string) => Promise<GpuAccount>;
  disconnectGpuAccount: (accountId: string, idempotencyKey: string, confirmActiveStop: boolean) => Promise<unknown>;
  startGpuAccountAuth: (accountId: string, purpose: 'colab' | 'drive', idempotencyKey: string) => Promise<{ authorization_url?: string } | string>;
}

export interface AccountDirectoryProps {
  /** Optional externally loaded list. When supplied it is kept in sync with the view. */
  accounts?: GpuAccount[];
  api?: Partial<AccountDirectoryApi>;
  onAccountsChange?: (accounts: GpuAccount[]) => void;
}

let idSequence = 0;
function newIdempotencyKey(prefix: string): string {
  idSequence += 1;
  return `${prefix}-${Date.now().toString(36)}-${idSequence.toString(36)}`;
}

function listPayload(value: unknown): GpuAccount[] {
  if (Array.isArray(value)) return value as GpuAccount[];
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    for (const key of ['accounts', 'items', 'data']) if (Array.isArray(record[key])) return record[key] as GpuAccount[];
  }
  return [];
}

function accountPayload(value: unknown): GpuAccount {
  if (value && typeof value === 'object' && 'account_id' in value) return value as GpuAccount;
  if (value && typeof value === 'object' && 'data' in value) return accountPayload((value as Record<string, unknown>).data);
  return value as GpuAccount;
}

const defaultAccountApi: AccountDirectoryApi = {
  getGpuAccounts: async () => listPayload(await getGoogleAccounts()),
  createGpuAccount: async (body, key) => accountPayload(await createGoogleAccount(body, key)),
  updateGpuAccount: async (accountId, patch, key) => accountPayload(await updateGoogleAccount(accountId, patch as Parameters<typeof updateGoogleAccount>[1], key)),
  disconnectGpuAccount: (accountId, key, _confirmActiveStop) => disconnectGoogleAccount(accountId, key),
  startGpuAccountAuth: (accountId, purpose, key) => startGoogleAccountAuth(accountId, purpose, key),
};

function authStatus(account: GpuAccount, purpose: 'colab' | 'drive'): AuthorizationStatus {
  const authorization = account.authorization?.[purpose];
  if (authorization) return authorization.connected ? 'connected' : authorization.message ? 'needs_reauth' : 'not_connected';
  return account[purpose]?.status || account[`${purpose}_status`] || 'not_connected';
}

function statusLabel(status: string): string {
  return ({ connected: '已连接', needs_reauth: '需重新授权', disabled: '已停用', error: '错误', not_connected: '未连接' } as Record<string, string>)[status] || status;
}

function activeCount(account: GpuAccount): number {
  return Number(account.active_instance_count ?? account.active_instances ?? 0);
}

function checkedAt(account: GpuAccount): string {
  const value = account.last_checked_at || account.colab?.checked_at || account.drive?.checked_at;
  if (!value) return '尚未检查';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function safeAuthRedirect(value: { authorization_url?: string } | string): string | null {
  const candidate = typeof value === 'string' ? value : value.authorization_url;
  if (!candidate || typeof window === 'undefined') return null;
  try {
    const url = new URL(candidate, window.location.origin);
    if (url.protocol !== 'https:' && url.origin !== window.location.origin) return null;
    return url.href;
  } catch {
    return null;
  }
}

export function AccountDirectory({ accounts: suppliedAccounts, api, onAccountsChange }: AccountDirectoryProps) {
  const accountApi = useMemo(() => ({ ...defaultAccountApi, ...api }), [api]);
  const [accounts, setAccounts] = useState<GpuAccount[]>(suppliedAccounts || []);
  const [alias, setAlias] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editAlias, setEditAlias] = useState('');
  const [budgetDraft, setBudgetDraft] = useState<Record<string, string>>({});
  const [disconnecting, setDisconnecting] = useState<GpuAccount | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const keyRef = useRef<Record<string, string>>({});
  const loadRef = useRef(false);

  const replaceAccounts = useCallback((next: GpuAccount[]) => {
    setAccounts(next);
    onAccountsChange?.(next);
  }, [onAccountsChange]);

  const refresh = useCallback(async () => {
    if (loadRef.current) return;
    loadRef.current = true;
    setError('');
    try {
      replaceAccounts(await accountApi.getGpuAccounts());
    } catch (cause) {
      setError(apiMessage(cause));
    } finally {
      loadRef.current = false;
    }
  }, [accountApi, replaceAccounts]);

  useEffect(() => {
    if (suppliedAccounts) setAccounts(suppliedAccounts);
  }, [suppliedAccounts]);
  useEffect(() => { void refresh(); }, [refresh]);

  function keyFor(operation: string): string {
    return keyRef.current[operation] ||= newIdempotencyKey(operation);
  }
  function finishKey(operation: string) { delete keyRef.current[operation]; }

  async function createAccount(event: FormEvent) {
    event.preventDefault();
    const nextAlias = alias.trim();
    if (!nextAlias || busy) return;
    const operation = 'account-create';
    setBusy(operation); setError(''); setStatus('正在创建账号目录…');
    try {
      const created = await accountApi.createGpuAccount({ alias: nextAlias }, keyFor(operation));
      replaceAccounts([...accounts, created]);
      setAlias(''); finishKey(operation); setStatus(`已创建「${created.alias || nextAlias}」，请分别授权 Colab 与 Drive。`);
    } catch (cause) {
      setError(apiMessage(cause)); setStatus('创建未确认；再次重试会复用同一幂等键。');
    } finally { setBusy(null); }
  }

  async function updateAccount(account: GpuAccount, patch: Record<string, unknown>, operation: string, success: string) {
    if (busy) return;
    setBusy(operation); setError(''); setStatus('正在保存账号设置…');
    try {
      const updated = await accountApi.updateGpuAccount(account.account_id, patch, keyFor(operation));
      replaceAccounts(accounts.map((item) => item.account_id === account.account_id ? updated : item));
      finishKey(operation); setStatus(success);
    } catch (cause) {
      setError(apiMessage(cause)); setStatus('保存未确认；再次重试会复用同一幂等键。');
    } finally { setBusy(null); }
  }

  async function authorize(account: GpuAccount, purpose: 'colab' | 'drive') {
    const operation = `account-auth-${account.account_id}-${purpose}`;
    if (busy) return;
    setBusy(operation); setError(''); setStatus(`正在准备 ${purpose === 'colab' ? 'Colab' : 'Drive'} 授权…`);
    try {
      const response = await accountApi.startGpuAccountAuth(account.account_id, purpose, keyFor(operation));
      const target = safeAuthRedirect(response);
      if (!target) throw new Error('授权地址无效或未返回；没有打开外部地址。');
      finishKey(operation);
      window.location.assign(target);
    } catch (cause) {
      setError(apiMessage(cause)); setStatus('授权尚未开始；再次重试会复用同一幂等键。'); setBusy(null);
    }
  }

  async function disconnect() {
    const account = disconnecting;
    if (!account || busy) return;
    const operation = `account-disconnect-${account.account_id}`;
    setBusy(operation); setError(''); setStatus('正在请求解除连接…');
    try {
      await accountApi.disconnectGpuAccount(account.account_id, keyFor(operation), activeCount(account) > 0);
      replaceAccounts(accounts.filter((item) => item.account_id !== account.account_id));
      finishKey(operation); setDisconnecting(null); setStatus('解除连接请求已确认；历史记录保留匿名快照。');
    } catch (cause) {
      setError(apiMessage(cause)); setStatus('解除连接未确认；再次重试会复用同一幂等键。');
    } finally { setBusy(null); }
  }

  return (
    <section className={styles.directory} aria-label="Google 账号目录">
      <header className={styles.sectionHeader}>
        <div><p className={styles.eyebrow}>设置 · 单一账号入口</p><h2>账号目录</h2></div>
        <button type="button" className={styles.iconButton} onClick={() => void refresh()} disabled={Boolean(busy)} aria-label="刷新账号"><RefreshCw size={16} aria-hidden /></button>
      </header>
      <p className={styles.muted}>每个账号的 Colab 与 Drive 授权分开管理。只会使用当前 owner 已授权、允许分配且预算可用的账号。</p>
      {status && <p className={styles.status} role="status">{status}</p>}
      {error && <p className={styles.error} role="alert">{error}</p>}

      <form className={styles.createRow} onSubmit={createAccount}>
        <label className={styles.srOnly} htmlFor="new-gpu-account-alias">新账号别名</label>
        <input id="new-gpu-account-alias" value={alias} onChange={(event) => setAlias(event.target.value)} placeholder="例如：主账号" maxLength={80} />
        <button type="submit" className={styles.button} disabled={!alias.trim() || Boolean(busy)}>新增账号</button>
      </form>

      {!accounts.length ? (
        <div className={styles.empty}><strong>还没有账号目录</strong><span>先新增别名，再分别完成 Colab / Drive 授权。</span></div>
      ) : <div className={styles.stack}>
        {accounts.map((account) => {
          const active = activeCount(account);
          const colab = authStatus(account, 'colab');
          const drive = authStatus(account, 'drive');
          const isBusy = busy?.includes(account.account_id) || false;
          return (
            <article className={styles.accountCard} key={account.account_id}>
              <div className={styles.cardTop}>
                <div className={styles.minWidth}>
                  {editingId === account.account_id ? (
                    <div className={styles.inlineEdit}><input value={editAlias} onChange={(event) => setEditAlias(event.target.value)} autoFocus aria-label="账号别名" /><button type="button" className={styles.button} disabled={!editAlias.trim() || Boolean(busy)} onClick={() => { void updateAccount(account, { alias: editAlias.trim() }, `account-rename-${account.account_id}`, '账号别名已更新。'); setEditingId(null); }}>保存</button><button type="button" className={styles.buttonSecondary} onClick={() => setEditingId(null)}>取消</button></div>
                  ) : <><h3>{account.alias || '未命名账号'}</h3><p className={styles.muted}>{account.email_masked || '身份邮箱已隐藏'} · 最近检查：{checkedAt(account)}</p></>}
                </div>
                <span className={`${styles.badge} ${account.status === 'connected' ? styles.good : account.status === 'error' ? styles.bad : ''}`}>{statusLabel(account.status)}</span>
              </div>
              {account.last_error && <p className={styles.warning}>{account.last_error}</p>}
              <div className={styles.authGrid}>
                {(['colab', 'drive'] as const).map((purpose) => {
                  const auth = purpose === 'colab' ? colab : drive;
                  const delegatedAlias = purpose === 'drive' ? (account.drive_account_alias || account.authorization?.drive?.delegated_alias) : null;
                  return <div className={styles.authCell} key={purpose}><span>{purpose === 'colab' ? 'Colab' : 'Drive'}</span><b>{delegatedAlias ? `复用「${delegatedAlias}」(${statusLabel(auth)})` : statusLabel(auth)}</b><button type="button" className={styles.buttonSecondary} disabled={Boolean(busy)} onClick={() => void authorize(account, purpose)}>{auth === 'connected' ? '重新授权' : '授权'}</button></div>;
                })}
              </div>
              <div className={styles.accountControls}>
                <label className={styles.toggleRow}><input type="checkbox" checked={account.enabled !== false} disabled={Boolean(busy)} onChange={(event) => void updateAccount(account, { enabled: event.target.checked }, `account-enabled-${account.account_id}`, event.target.checked ? '账号已启用。' : '账号已停用，不会分配新任务。')} /><span>允许使用</span></label>
                <label className={styles.toggleRow}><input type="checkbox" checked={account.allow_allocation === true} disabled={Boolean(busy) || account.enabled === false} onChange={(event) => void updateAccount(account, { allow_allocation: event.target.checked }, `account-allocation-${account.account_id}`, event.target.checked ? '已允许新任务分配。' : '已禁止新任务分配。')} /><span>允许分配</span></label>
                {accounts.length > 1 && (
                  <label className={styles.budgetField}>
                    <span>Drive 存储来源</span>
                    <select
                      aria-label="Drive 存储来源"
                      value={account.drive_account_id || ''}
                      disabled={Boolean(busy)}
                      onChange={(event) => {
                        const target = event.target.value || null;
                        void updateAccount(
                          account,
                          { drive_account_id: target },
                          `account-drive-source-${account.account_id}`,
                          target ? '已切换为复用指定账号的 Drive 存储。' : '已切换为使用本账号独立 Drive。',
                        );
                      }}
                    >
                      <option value="">本账号独立 Drive</option>
                      {accounts
                        .filter((item) => item.account_id !== account.account_id && !item.drive_account_id && authStatus(item, 'drive') === 'connected')
                        .map((item) => (
                          <option key={item.account_id} value={item.account_id}>
                            复用：{item.alias || item.account_id.slice(0, 8)}
                          </option>
                        ))}
                    </select>
                  </label>
                )}
                <label className={styles.budgetField}><span>安全预算（站内估算 CU）</span><input type="number" min="0" step="any" value={budgetDraft[account.account_id] ?? (typeof account.budget === 'object' && account.budget ? String((account.budget as Record<string, unknown>).daily_units ?? (account.budget as Record<string, unknown>).daily ?? '') : account.budget_limit ?? '')} disabled={Boolean(busy)} onChange={(event) => setBudgetDraft({ ...budgetDraft, [account.account_id]: event.target.value })} onBlur={(event) => { const value = event.target.value.trim(); if (value !== '') void updateAccount(account, { budget: { ...(typeof account.budget === 'object' && account.budget ? account.budget : {}), daily: Number(value), daily_units: Number(value) } }, `account-budget-${account.account_id}`, '账号预算已更新。'); }} /></label>
              </div>
              <div className={styles.cardActions}>
                <button type="button" className={styles.buttonSecondary} disabled={Boolean(busy)} onClick={() => { setEditingId(account.account_id); setEditAlias(account.alias || ''); }}>改名</button>
                <button type="button" className={styles.buttonDanger} disabled={Boolean(busy)} onClick={() => setDisconnecting(account)}>解除连接</button>
                {active > 0 && <span className={styles.warningInline}>活动资源 {active} 个，不能直接解除</span>}
                {isBusy && <span className={styles.muted}>处理中…</span>}
              </div>
            </article>
          );
        })}
      </div>}

      {disconnecting && <div className={styles.sheetBackdrop} role="presentation"><section className={styles.sheet} role="dialog" aria-modal="true" aria-labelledby="disconnect-title"><h3 id="disconnect-title">确认解除「{disconnecting.alias}」？</h3>{activeCount(disconnecting) > 0 ? <p className={styles.warning}>检测到 {activeCount(disconnecting)} 个活动实例/任务。解除前必须由服务端执行安全停机检查；若仍无法安全停机，操作会失败，历史记录不会丢失。</p> : <p className={styles.muted}>只解除本站授权连接，不会删除历史任务和作品。服务端会再次检查活动资源。</p>}<div className={styles.sheetActions}><button type="button" className={styles.buttonSecondary} onClick={() => setDisconnecting(null)} disabled={Boolean(busy)}>取消</button><button type="button" className={styles.buttonDanger} onClick={() => void disconnect()} disabled={Boolean(busy)}>{activeCount(disconnecting) ? '确认安全检查后解除' : '确认解除连接'}</button></div></section></div>}
    </section>
  );
}

export { defaultAccountApi };
