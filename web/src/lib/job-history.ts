export interface TimingDetail { seconds?: number | null; source?: 'events' | 'worker' | string; status?: 'completed' | 'in_progress' | string; }

export function formatDuration(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const rest = rounded % 60;
  const parts: string[] = [];
  if (hours) parts.push(`${hours}小时`);
  if (minutes || hours) parts.push(`${minutes}分`);
  parts.push(`${rest}秒`);
  return parts.join('');
}

export function formatTiming(detail?: TimingDetail | null): string {
  if (!detail || detail.status === 'in_progress') return '进行中';
  return formatDuration(detail.seconds);
}
