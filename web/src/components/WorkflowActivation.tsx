import { useCallback, useEffect, useRef, useState } from 'react';
import {
  acceptGpuWorkflowSmoke, apiMessage, getGpuComputeSpecs, getGoogleAccounts, getProjects, getWorkflowActivation,
  publishGpuWorkflow, realSmokeGpuWorkflow, reviewGpuModel, reviewGpuWorkflow, validateGpuWorkflow,
  type ComputeSpec, type GpuWorkflow, type WorkflowActivation,
} from '../lib/gpu-api';
import {
  appendActivationLog, getActivationStep, getSmokeRuntimeMinutes, hasExistingSmoke,
  isSmokeCompleted, isSmokePolling, shouldAcceptSmoke, shouldCreateSmoke,
} from '../lib/activation-resume';
import { resolveGpuCheckRequest, selectEligibleColabGpuSpec } from '../lib/colab-check';
import styles from '../gpu.module.css';

const STEPS_POLL_MS = 6000;

const sleep = (ms: number) => new Promise((resolve) => { window.setTimeout(resolve, ms); });

function runtimeText(minutes: number | null): string {
  if (minutes === null) return '运行时长以服务端任务状态为准';
  const value = Number.isInteger(minutes) ? String(minutes) : minutes.toFixed(1);
  return `服务端运行上限 ${value} 分钟`;
}

function smokeStateText(state: string): string {
  if (state === 'queued' || state === 'pending') return '冒烟任务已恢复，正在排队';
  if (state === 'running') return '冒烟任务已恢复，正在运行';
  if (state === 'completed' || state === 'succeeded' || state === 'success') return '冒烟任务已完成，可直接验收';
  return `冒烟任务状态：${state}`;
}

export function WorkflowActivationPanel({
  workflow, onClose, onDone,
}: {
  workflow: GpuWorkflow;
  onClose: () => void;
  onDone: () => void;
}) {
  const [data, setData] = useState<WorkflowActivation | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [consent, setConsent] = useState(false);
  const [projectId, setProjectId] = useState('');
  const [accountId, setAccountId] = useState('');
  const [spec, setSpec] = useState<ComputeSpec | null>(null);
  const [specsLoaded, setSpecsLoaded] = useState(false);
  const aliveRef = useRef(false);
  const executionRef = useRef(0);

  const isCurrent = useCallback((token: number) => aliveRef.current && executionRef.current === token, []);

  const say = useCallback((line: string, token?: number) => {
    if (!aliveRef.current || (token !== undefined && executionRef.current !== token)) return;
    setLog((current) => appendActivationLog(current, line));
  }, []);

  const refresh = useCallback(async (token: number): Promise<WorkflowActivation> => {
    const next = await getWorkflowActivation(workflow.id);
    // The server snapshot is authoritative, but never update a closed or replaced panel.
    if (isCurrent(token)) setData(next);
    return next;
  }, [isCurrent, workflow.id]);

  const waitForSmoke = useCallback(async (
    initial: WorkflowActivation,
    token: number,
  ): Promise<WorkflowActivation | null> => {
    let snapshot = initial;
    let announcedState = '';
    while (isCurrent(token)) {
      const state = String(snapshot.smoke?.state || '').toLowerCase();
      if (state && state !== announcedState) {
        announcedState = state;
        say(smokeStateText(state), token);
      }
      if (isSmokeCompleted(snapshot) || ['failed', 'cancelled', 'canceled', 'error'].includes(state)) {
        return snapshot;
      }
      // Do not emit a log entry for every poll. The service snapshot remains visible in the steps.
      await sleep(STEPS_POLL_MS);
      if (!isCurrent(token)) return null;
      snapshot = await refresh(token);
      if (!isCurrent(token)) return null;
    }
    return null;
  }, [isCurrent, refresh, say]);

  const runAll = useCallback(async () => {
    if (busy || !aliveRef.current) return;
    const token = executionRef.current + 1;
    executionRef.current = token;
    setError('');
    setBusy(true);
    try {
      let snapshot = await refresh(token);
      if (!isCurrent(token)) return;

      // Existing queued/running/completed jobs are authoritative. In particular,
      // do not make account/spec discovery a prerequisite for accepting one.
      if (shouldCreateSmoke(snapshot) && snapshot.blockers.length) {
        throw new Error(snapshot.blockers[0]);
      }
      if (getActivationStep(snapshot, 'validate')?.done !== true) {
        say('正在校验模板…', token);
        await validateGpuWorkflow(workflow.id);
        if (!isCurrent(token)) return;
        snapshot = await refresh(token);
        if (!isCurrent(token)) return;
      }
      if (getActivationStep(snapshot, 'model_review')?.done !== true) {
        if (!snapshot.model) throw new Error('找不到绑定的模型记录');
        say('正在记录模型来源与许可…', token);
        await reviewGpuModel(snapshot.model.model_id);
        if (!isCurrent(token)) return;
        snapshot = await refresh(token);
        if (!isCurrent(token)) return;
      }
      if (getActivationStep(snapshot, 'workflow_review')?.done !== true) {
        say('正在锁定权重与节点…', token);
        await reviewGpuWorkflow(workflow.id);
        if (!isCurrent(token)) return;
        snapshot = await refresh(token);
        if (!isCurrent(token)) return;
      }

      if (getActivationStep(snapshot, 'real_smoke')?.done !== true) {
        if (hasExistingSmoke(snapshot)) {
          say('发现已有冒烟任务，继续读取服务端进度…', token);
          if (!isSmokeCompleted(snapshot)) {
            const resumed = await waitForSmoke(snapshot, token);
            if (!resumed) return;
            snapshot = resumed;
          }
        } else {
          if (!consent) throw new Error('请先确认会真实开 GPU 并产生费用');
          if (!projectId) throw new Error('请先创建一个项目，冒烟任务要落在项目里');
          if (!accountId || !spec) throw new Error('当前账号没有可用的 GPU 规格，请先授权 Colab');
          say('正在提交真实 GPU 冒烟…', token);
          await realSmokeGpuWorkflow(
            workflow.id,
            {
              confirmed: true,
              project_id: projectId,
              params: {},
              asset_ids: [],
              account_id: accountId,
              spec,
            },
            `smoke-${workflow.id}-${Date.now()}`,
          );
          if (!isCurrent(token)) return;
          snapshot = await refresh(token);
          if (!isCurrent(token)) return;
          snapshot = (await waitForSmoke(snapshot, token)) || snapshot;
          if (!isCurrent(token)) return;
        }
      }

      if (!isSmokeCompleted(snapshot)) {
        throw new Error(snapshot.smoke?.error_message || '冒烟未通过，详见任务详情');
      }
      if (shouldAcceptSmoke(snapshot)) {
        if (!snapshot.smoke?.job_id) throw new Error('找不到可验收的冒烟任务');
        say('正在验收冒烟输出…', token);
        await acceptGpuWorkflowSmoke(workflow.id, snapshot.smoke.job_id);
        if (!isCurrent(token)) return;
        snapshot = await refresh(token);
        if (!isCurrent(token)) return;
      }
      if (!snapshot.done) {
        say('正在发布并设为当前版本…', token);
        await publishGpuWorkflow(workflow.id);
        if (!isCurrent(token)) return;
        snapshot = await refresh(token);
      }
      if (!isCurrent(token)) return;
      say('完成，可以回创作页用了', token);
      onDone();
    } catch (err) {
      if (isCurrent(token)) setError(apiMessage(err));
    } finally {
      if (isCurrent(token)) setBusy(false);
    }
  }, [accountId, busy, consent, isCurrent, onDone, projectId, refresh, say, spec, waitForSmoke, workflow.id]);

  useEffect(() => {
    aliveRef.current = true;
    const token = executionRef.current + 1;
    executionRef.current = token;
    const load = async () => {
      try {
        const snapshot = await refresh(token);
        if (!isCurrent(token)) return;
        if (hasExistingSmoke(snapshot)) {
          setSpecsLoaded(true);
          if (isSmokePolling(snapshot)) {
            // Initial mount polling owns the continuation until it settles.
            setBusy(true);
            say('已恢复服务端冒烟进度…', token);
            try { await waitForSmoke(snapshot, token); }
            finally { if (isCurrent(token)) setBusy(false); }
          } else if (isSmokeCompleted(snapshot)) {
            say('已找到已完成的冒烟任务，可直接验收', token);
          }
          return;
        }
        const [projects, accounts] = await Promise.all([getProjects(), getGoogleAccounts()]);
        if (!isCurrent(token)) return;
        setProjectId(projects[0]?.id || '');
        const request = resolveGpuCheckRequest(
          { real_enabled: snapshot.real_enabled, offline_mode: false },
          accounts,
          null,
        );
        if (request.provider !== 'colab') {
          throw new Error('没有已启用且已连接 Colab 的账号，无法读取真实 GPU 规格');
        }
        const eligible = selectEligibleColabGpuSpec(await getGpuComputeSpecs('colab', request.accountId));
        if (!isCurrent(token)) return;
        if (!eligible) throw new Error('当前账号没有可用的真实 GPU 规格（已过滤 CPU/TPU）');
        // Keep the account identity and structured spec from the same discovery call.
        setAccountId(request.accountId);
        setSpec(eligible);
      } catch (err) {
        if (isCurrent(token)) setError(apiMessage(err));
      } finally {
        if (isCurrent(token)) setSpecsLoaded(true);
      }
    };
    void load();
    return () => {
      aliveRef.current = false;
      // This only stops local continuation/polling. It deliberately does not cancel the remote job.
      executionRef.current += 1;
    };
  }, [isCurrent, refresh, say, waitForSmoke]);

  const existingSmoke = hasExistingSmoke(data);
  const needsNewSmoke = data ? !data.done && shouldCreateSmoke(data) : true;
  const runtimeLimit = getSmokeRuntimeMinutes(data);

  return (
    <div className={styles.activationPanel}>
      <p className="muted small">
        这一步会真实分配 GPU 跑一次冒烟（{runtimeText(runtimeLimit)}，按实际用量计费）。通过后自动发布并设为当前版本。
      </p>
      {data?.blockers.length && !existingSmoke ? (
        <div className={styles.notice}>
          {data.blockers.map((item) => <p key={item} className="small">{item}</p>)}
        </div>
      ) : null}
      <ol className={styles.activationSteps}>
        {(data?.steps || []).map((step) => (
          <li key={step.key} data-state={step.done ? 'done' : step.failed ? 'failed' : step.running ? 'running' : step.next ? 'next' : 'todo'}>
            <span>{step.label}</span>
            <span className="pill" data-state={step.done ? 'active' : undefined}>
              {step.done ? '已完成' : step.failed ? '失败' : step.running ? '进行中' : step.next ? '待执行' : '待执行'}
            </span>
          </li>
        ))}
      </ol>
      {error ? <p className={styles.errorText}>{error}</p> : null}
      {log.length ? (
        <details className="foldable" open>
          <summary>执行记录</summary>
          <div className="foldContent">
            {log.map((line, index) => <p key={`${line}-${index}`} className="muted small">{line}</p>)}
          </div>
        </details>
      ) : null}
      {needsNewSmoke ? (
        <label className={styles.checkline}>
          <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
          <span>我知道会真实开 GPU 并产生费用</span>
        </label>
      ) : null}
      <div className="actions">
        <button
          className="button"
          type="button"
          disabled={busy || !specsLoaded || (needsNewSmoke && !consent)}
          onClick={() => void runAll()}
        >
          {busy ? '执行中…' : data?.done ? '重新检查' : existingSmoke ? '继续验收并启用' : '一键跑通并启用'}
        </button>
        <button className="button secondary" type="button" onClick={() => {
          executionRef.current += 1;
          onClose();
        }}>关闭</button>
      </div>
    </div>
  );
}
