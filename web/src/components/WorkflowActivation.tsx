import { useCallback, useEffect, useState } from 'react';
import {
  acceptGpuWorkflowSmoke, apiMessage, getGpuComputeSpecs, getProjects, getWorkflowActivation,
  publishGpuWorkflow, realSmokeGpuWorkflow, reviewGpuModel, reviewGpuWorkflow, validateGpuWorkflow,
  type GpuWorkflow, type WorkflowActivation,
} from '../lib/gpu-api';
import styles from '../gpu.module.css';

const STEPS_POLL_MS = 6000;
const POLL_LIMIT = 60;

const sleep = (ms: number) => new Promise((resolve) => { window.setTimeout(resolve, ms); });

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
  const [specKey, setSpecKey] = useState('');
  const [specsLoaded, setSpecsLoaded] = useState(false);

  const say = (line: string) => setLog((curr) => [...curr, line]);

  const refresh = useCallback(async () => {
    const next = await getWorkflowActivation(workflow.id);
    setData(next);
    return next;
  }, [workflow.id]);

  useEffect(() => {
    void refresh().catch((err) => setError(apiMessage(err)));
    void getProjects()
      .then((list) => setProjectId(list[0]?.id || ''))
      .catch(() => undefined);
    void getGpuComputeSpecs('colab')
      .then((list) => {
        const first = list.find((item) => String(item.key || '').length > 0);
        setSpecKey(first ? String(first.key) : '');
        setSpecsLoaded(true);
      })
      .catch(() => setSpecsLoaded(true));
  }, [refresh]);

  const stepDone = (key: string) => data?.steps.find((step) => step.key === key)?.done === true;

  async function waitForSmoke(): Promise<WorkflowActivation> {
    for (let i = 0; i < POLL_LIMIT; i += 1) {
      const snapshot = await refresh();
      const state = snapshot.smoke?.state;
      if (state && ['completed', 'failed', 'cancelled'].includes(state)) {
        say(state === 'completed' ? '冒烟任务已完成' : `冒烟任务结束：${state}`);
        return snapshot;
      }
      say(`等待 GPU 冒烟…（${i + 1}）`);
      await sleep(STEPS_POLL_MS);
    }
    throw new Error('冒烟超时，请稍后回来查看任务状态');
  }

  async function runAll() {
    if (busy) return;
    setError('');
    setBusy(true);
    try {
      let snapshot = await refresh();
      if (snapshot.blockers.length) throw new Error(snapshot.blockers[0]);
      if (!stepDone('validate')) {
        say('正在校验模板…');
        await validateGpuWorkflow(workflow.id);
        snapshot = await refresh();
      }
      if (!stepDone('model_review')) {
        if (!snapshot.model) throw new Error('找不到绑定的模型记录');
        say('正在记录模型来源与许可…');
        await reviewGpuModel(snapshot.model.model_id);
        snapshot = await refresh();
      }
      if (!stepDone('workflow_review')) {
        say('正在锁定权重与节点…');
        await reviewGpuWorkflow(workflow.id);
        snapshot = await refresh();
      }
      if (!stepDone('real_smoke')) {
        if (!projectId) throw new Error('请先创建一个项目，冒烟任务要落在项目里');
        if (!specKey) throw new Error('当前账号没有可用的 GPU 规格，请先授权 Colab');
        say('正在提交真实 GPU 冒烟…');
        await realSmokeGpuWorkflow(
          workflow.id,
          { confirmed: true, project_id: projectId, params: {}, asset_ids: [], spec: { key: specKey } },
          `smoke-${workflow.id}-${Date.now()}`,
        );
        say('已进入队列，最长 5 分钟');
        snapshot = await waitForSmoke();
      }
      if (!snapshot.smoke_job_completed) {
        throw new Error(snapshot.smoke?.error_message || '冒烟未通过，详见任务详情');
      }
      if (!stepDone('accept_smoke')) {
        if (!snapshot.smoke?.job_id) throw new Error('找不到可验收的冒烟任务');
        say('正在验收冒烟输出…');
        await acceptGpuWorkflowSmoke(workflow.id, snapshot.smoke.job_id);
        snapshot = await refresh();
      }
      if (!snapshot.done) {
        say('正在发布并设为当前版本…');
        await publishGpuWorkflow(workflow.id);
        snapshot = await refresh();
      }
      say('完成，可以回创作页用了');
      onDone();
    } catch (err) {
      setError(apiMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.activationPanel}>
      <p className="muted small">
        这一步会真实分配 GPU 跑一次冒烟（最长 5 分钟，按分钟计费）。通过后自动发布并设为当前版本。
      </p>
      {data?.blockers.length ? (
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
      <label className={styles.checkline}>
        <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
        <span>我知道会真实开 GPU 并产生费用</span>
      </label>
      <div className="actions">
        <button className="button" type="button" disabled={!consent || busy || !specsLoaded} onClick={() => void runAll()}>
          {busy ? '执行中…' : data?.done ? '重新检查' : '一键跑通并启用'}
        </button>
        <button className="button secondary" type="button" disabled={busy} onClick={onClose}>关闭</button>
      </div>
    </div>
  );
}
