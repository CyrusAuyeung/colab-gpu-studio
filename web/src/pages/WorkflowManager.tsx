import { ComputePage, ThemePicker } from './ComputePage';
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import {
  apiMessage, assetUrl, cancelGpuJob, createGpuJob, createGpuBatch, createProject, deleteAsset, disconnectGoogle,
  getGpuComputeSpecs, getGpuGallery, getGpuJob, getGpuJobs, getGpuModels,
  getGpuPresets, getReferenceAssets, getGpuSettings, getGpuUsage, getGpuWorkflowSchema, getGpuWorkflows, getProjects, importGpuWorkflow,
  isLoggedIn, login, publishGpuWorkflow, retryGpuJob, startGoogleAuth, stopGpuCompute, updateAsset, uploadAsset,
  validateGpuWorkflow, rollbackGpuWorkflow, saveGpuPreset, type ComputeSpec, type GalleryAsset, type GpuJob, type GpuPreset,
  type GpuManifest, type GpuModel, type GpuParameter, type GpuSettings, type GpuUsage, type GpuWorkflow, type Project, type Provider,
} from '../lib/gpu-api';
import { GpuParamForm } from '../components/GpuParamForm';
import { ReferencePicker, referenceAssets, supportsReferences } from '../components/ReferencePicker';
import { GoogleOAuthSetup } from '../components/GoogleOAuthSetup';
import { ComputeSpecs } from '../components/ComputeSpecs';
import styles from '../gpu.module.css';

import { terminalStates,record,parameterList,parameterKey,defaultParams,keyId,shown,readableState,workflowState,progressPercent,assetIdsFromParams } from '../lib/ui-helpers';

export function WorkflowsPanel({
  workflows,
  onImport,
  onValidate,
  onPublish,
  onRollback,
}: {
  workflows: GpuWorkflow[];
  onImport: (bundle: string) => void;
  onValidate: (workflow: GpuWorkflow) => void;
  onPublish: (workflow: GpuWorkflow) => void;
  onRollback: (workflow: GpuWorkflow) => void;
}) {
  const [text, setText] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  return (
    <div className={styles.stack}>
      <details className="foldable">
        <summary>导入新工作流草稿</summary>
        <div className="foldContent">
          <textarea
            className={styles.code}
            placeholder="粘贴工作流 JSON bundle (含 manifest, api_graph, object_info)"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="actions">
            <button className="button secondary" type="button" onClick={() => fileRef.current?.click()}>
              读取 JSON 文件
            </button>
            <input
              ref={fileRef}
              hidden
              type="file"
              accept="application/json,.json"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) setText(await file.text());
                e.target.value = '';
              }}
            />
            <button className="button" type="button" disabled={!text.trim()} onClick={() => onImport(text)}>
              导入草稿
            </button>
          </div>
        </div>
      </details>

      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h2>已注册工作流版本</h2>
          <span className="pill">{workflows.length} 个版本</span>
        </div>
        {workflows.length ? (
          workflows.map((wf) => (
            <article className={styles.workflowCard} key={wf.id}>
              <div className={styles.workflowHeader}>
                <div>
                  <strong>{wf.workflow_key} · v{wf.workflow_version}</strong>
                  <p className="muted" style={{ margin: '0.5vw 0 0', fontSize: '3vw' }}>
                    模型 {wf.model_id}
                  </p>
                </div>
                <span className="pill">{workflowState(wf.state, wf.active)}</span>
              </div>
              <div className="actions">
                <button
                  className="button secondary"
                  type="button"
                  disabled={wf.state === 'published'}
                  onClick={() => onValidate(wf)}
                >
                  校验
                </button>
                <button
                  className="button"
                  type="button"
                  disabled={wf.state === 'published'}
                  onClick={() => onPublish(wf)}
                >
                  确认发布
                </button>
                <button
                  className="button secondary"
                  type="button"
                  disabled={wf.state !== 'published'}
                  onClick={() => onRollback(wf)}
                >
                  回滚为此版本
                </button>
              </div>
            </article>
          ))
        ) : (
          <div className="empty">
            <strong>未检测到工作流</strong>
          </div>
        )}
      </section>
    </div>
  );
}

