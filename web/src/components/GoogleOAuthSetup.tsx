import { useEffect, useState } from 'react';
import { apiMessage, getGoogleClientConfig, saveGoogleClientConfig, type GoogleClientConfig } from '../lib/gpu-api';
import styles from '../gpu.module.css';

export function GoogleOAuthSetup({ onConfigured }: { onConfigured: () => void }) {
  const [config, setConfig] = useState<GoogleClientConfig | null>(null);
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [webClient, setWebClient] = useState<Record<string, unknown> | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const callback = config?.redirect_uri || `${window.location.origin}/api/gpu/auth/google/callback`;
  useEffect(() => { let alive = true; getGoogleClientConfig().then((next) => { if (alive) setConfig(next); }).catch((cause) => { if (alive) setError(apiMessage(cause)); }); return () => { alive = false; }; }, []);
  async function save() {
    if (busy || !confirmed) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const next = await saveGoogleClientConfig(webClient ? { client_json: webClient, confirmed: true } : { client_id: clientId.trim(), client_secret: clientSecret, confirmed: true });
      setConfig(next); setClientId(''); setClientSecret(''); setWebClient(null); setConfirmed(false);
      setNotice('应用配置已加密保存。下一步点击“连接 Google Colab”和“连接 Google Drive”完成本人授权；不会自动开 GPU。');
      onConfigured();
    } catch (cause) { setError(apiMessage(cause)); } finally { setBusy(false); }
  }
  return <section className={styles.card} aria-label="Google OAuth 应用配置"><h2>Google OAuth 应用配置</h2>
    <p className="muted">{config?.configured ? '应用已配置。连接账户与启用 GPU 是另外两步。' : '尚未配置 Google 应用，因此现在不能直接跳转授权。你需要先创建自己的 OAuth 网页客户端。'}</p>
    <ol className={styles.oauthSteps}>
      <li>打开 <a href="https://console.cloud.google.com/" target="_blank" rel="noreferrer">Google Cloud 控制台</a>，创建或选择自己的项目。</li>
      <li>在“API 和服务 → 库”启用 Google Drive API，并按 Colab 官方文档启用 Colab API。</li>
      <li>配置 Google Auth Platform：应用名称、受众选“外部”；测试模式把自己的 Google 邮箱加入测试用户。</li>
      <li>在“客户端”创建 OAuth 客户端，类型选“Web 应用”，填入下方回调地址，下载客户端 JSON。</li>
      <li>在这里导入 JSON 并保存，再点击下面两个 Google 连接按钮，使用有 Colab 权益的账户授权。</li>
    </ol>
    <label className="field">授权回调地址<input aria-label="Google OAuth 回调地址" readOnly value={callback} onFocus={(event) => event.target.select()} /></label>
    <button className="button secondary" type="button" onClick={async () => { try { await navigator.clipboard.writeText(callback); setNotice('已复制回调地址'); } catch { setNotice('请点击回调地址输入框，全选后复制'); } }}>复制回调地址</button>
    <details><summary>需要的 OAuth 权限</summary><p className="muted">在 Google Auth Platform 的“数据访问权限”中添加下列 scopes；正式连接时也会由本站申请。测试模式仅允许你加入的测试用户授权。</p><code className={styles.oauthScope}>https://www.googleapis.com/auth/colaboratory</code><code className={styles.oauthScope}>https://www.googleapis.com/auth/drive.file</code></details>
    <p className="muted">如填写“已获授权的 JavaScript 来源”，使用 {window.location.origin}。授权不会产生 GPU 费用；不要把客户端 JSON 或 Secret 发到群里。</p>
    <details><summary>导入 / 更换 OAuth 客户端</summary><div className={styles.stack}>
      <label className="button secondary">选择 Google 客户端 JSON<input data-testid="google-client-file" hidden type="file" accept="application/json,.json" onChange={async (event) => { const file = event.target.files?.[0]; event.target.value = ''; if (!file) return; try { if (file.size > 65536) throw new Error('客户端 JSON 文件过大'); const value: unknown = JSON.parse(await file.text()); if (!value || typeof value !== 'object' || !('web' in value)) throw new Error('请选择 Web 应用客户端 JSON，不是桌面客户端或服务账号密钥'); setWebClient(value as Record<string, unknown>); setClientId(''); setClientSecret(''); setNotice('已读取文件，尚未保存'); setError(''); } catch (cause) { setWebClient(null); setError(apiMessage(cause)); } }} /></label>
      {!webClient && <><label className="field">或手动填写 Client ID<input autoComplete="off" autoCapitalize="none" spellCheck={false} value={clientId} onChange={(event) => setClientId(event.target.value)} /></label><label className="field">Client Secret<input type="password" autoComplete="new-password" value={clientSecret} onChange={(event) => setClientSecret(event.target.value)} /></label></>}
      <label className={styles.oauthConfirm}><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>这是我自己的 Google 应用，确认加密保存到本站后端。</span></label>
      <button className="button" type="button" disabled={busy || !confirmed || (!webClient && (!clientId || !clientSecret))} onClick={() => void save()}>{busy ? '保存中…' : '保存 OAuth 应用配置'}</button>
    </div></details>
    {error && <p className="status error" role="alert">{error}</p>}{notice && <p className="status" role="status">{notice}</p>}
  </section>;
}
