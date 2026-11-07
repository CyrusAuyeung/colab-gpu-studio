export function formatInstanceTime(value?: string | number | null): string {
  if (value == null || value === '') return '—';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}
