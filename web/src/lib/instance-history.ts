import { apiFetch } from './gpu-api';
import type { TimingDetail } from './job-history';

export interface HistoryLog { id?: string; state?: string; created_at?: string; message?: string; source?: string; }
export interface HistoryArtifact { id?: string; filename?: string; name?: string; mime?: string; status?: string; thumbnail_url?: string; }
export interface InstanceHistoryJob {
  id: string;
  job_id?: string;
  state?: string;
  provider?: string;
  error?: { code?: string; message?: string } | null;
  timings?: Record<string, number>;
  timing_details?: Record<string, TimingDetail>;
  logs?: HistoryLog[];
  artifacts?: HistoryArtifact[];
  [key: string]: unknown;
}
export interface InstanceHistory { instance_id: string; offline: boolean; source: string; jobs: InstanceHistoryJob[]; }

export const getInstanceHistory = (instanceId: string) =>
  apiFetch<InstanceHistory>(`/api/gpu/instances/${encodeURIComponent(instanceId)}/history`);
