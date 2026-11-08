import test from 'node:test';
import assert from 'node:assert/strict';

import {
  appendActivationLog,
  getActivationStep,
  getSmokeRuntimeMinutes,
  hasExistingSmoke,
  isSmokeCompleted,
  isSmokePolling,
  shouldCreateSmoke,
  shouldAcceptSmoke,
} from '../src/lib/activation-resume.ts';

const step = (key, done = false) => ({ key, label: key, done });

function snapshot(overrides = {}) {
  return {
    steps: [step('validate'), step('real_smoke'), step('accept_smoke')],
    smoke: null,
    smoke_job_completed: false,
    ...overrides,
  };
}

test('step decisions always use the refreshed activation snapshot', () => {
  const next = snapshot({ steps: [step('validate', true), step('real_smoke')] });

  assert.equal(getActivationStep(next, 'validate')?.done, true);
  assert.equal(getActivationStep(next, 'real_smoke')?.done, false);
});

test('queued and running smoke jobs are resumable and must not create another job', () => {
  for (const state of ['queued', 'running']) {
    const current = snapshot({ smoke: { job_id: `job-${state}`, state } });
    assert.equal(hasExistingSmoke(current), true);
    assert.equal(isSmokePolling(current), true);
    assert.equal(shouldCreateSmoke(current), false);
  }
});

test('completed smoke is accepted directly without creating a paid job', () => {
  const current = snapshot({
    smoke: { job_id: 'job-completed', state: 'completed' },
    smoke_job_completed: true,
  });

  assert.equal(hasExistingSmoke(current), true);
  assert.equal(isSmokeCompleted(current), true);
  assert.equal(shouldCreateSmoke(current), false);
  assert.equal(shouldAcceptSmoke(current), true);
});

test('failed smoke is not treated as a resumable completed task', () => {
  const current = snapshot({ smoke: { job_id: 'job-failed', state: 'failed' } });

  assert.equal(hasExistingSmoke(current), false);
  assert.equal(shouldCreateSmoke(current), true);
  assert.equal(shouldAcceptSmoke(current), false);
});

test('runtime copy reads the backend-provided limit instead of a UI constant', () => {
  assert.equal(getSmokeRuntimeMinutes({ max_runtime_minutes: 17 }), 17);
  assert.equal(getSmokeRuntimeMinutes({ runtime_limit_seconds: 90 }), 1.5);
  assert.equal(getSmokeRuntimeMinutes({}), null);
});

test('polling logs are bounded and do not repeat the same wait line forever', () => {
  let log = [];
  for (let i = 0; i < 100; i += 1) log = appendActivationLog(log, '等待 GPU 冒烟…');
  assert.deepEqual(log, ['等待 GPU 冒烟…']);

  for (let i = 0; i < 50; i += 1) log = appendActivationLog(log, `状态 ${i}`, 10);
  assert.equal(log.length, 10);
  assert.deepEqual(log, Array.from({ length: 10 }, (_, i) => `状态 ${i + 40}`));
});
