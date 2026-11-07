import test from 'node:test';
import assert from 'node:assert/strict';

import { resolveGpuCheckRequest, selectEligibleColabGpuSpec } from '../src/lib/colab-check.ts';

const real = { real_enabled: true, offline_mode: false };
const connectedCli = {
  account_id: 'account-cli',
  alias: 'CLI 算力号',
  status: 'connected',
  enabled: true,
  allow_allocation: true,
  authorization: { colab: { connected: true } },
};
const connectedLegacyOwner = {
  account_id: 'legacy-default-owner',
  alias: '历史Google账号',
  status: 'connected',
  enabled: true,
  allow_allocation: true,
  authorization: { colab: { connected: true } },
};
const colabConnectedWithOtherStatus = {
  account_id: 'account-colab-only',
  alias: '仅Colab已连接',
  status: 'needs_reauth',
  enabled: true,
  allow_allocation: false,
  authorization: { colab: { connected: true } },
};
const disabled = {
  account_id: 'account-disabled',
  alias: '停用账号',
  status: 'connected',
  enabled: false,
  allow_allocation: true,
  authorization: { colab: { connected: true } },
};
const unauthorized = {
  account_id: 'account-unauthorized',
  alias: '未授权账号',
  status: 'not_connected',
  enabled: true,
  allow_allocation: true,
  authorization: { colab: { connected: false } },
};

const request = (settings = real, accounts = [], selectedAccountId = null) =>
  resolveGpuCheckRequest(settings, accounts, selectedAccountId);

test('selected connected CLI account is queried with its accountId', () => {
  assert.deepEqual(request(real, [connectedCli], 'account-cli'), {
    provider: 'colab',
    accountId: 'account-cli',
  });
});

test('automatic pool skips disabled and unauthorized accounts', () => {
  assert.deepEqual(request(real, [disabled, unauthorized, connectedCli]), {
    provider: 'colab',
    accountId: 'account-cli',
  });
});

test('explicit disabled or unauthorized account does not silently fail over', () => {
  assert.deepEqual(request(real, [disabled, connectedCli], 'account-disabled'), {
    provider: 'mock',
    accountId: null,
    reason: 'account_unavailable',
  });
  assert.deepEqual(request(real, [unauthorized, connectedCli], 'account-unauthorized'), {
    provider: 'mock',
    accountId: null,
    reason: 'account_unavailable',
  });
});

test('legacy owner uses the migrated explicit account, not global settings.google', () => {
  const legacyGlobalSlot = { real_enabled: true, offline_mode: false, google: { colab: { connected: true } } };
  assert.deepEqual(request(legacyGlobalSlot, [connectedLegacyOwner]), {
    provider: 'colab',
    accountId: 'legacy-default-owner',
  });
});

test('query only requires an enabled account with connected Colab, not allocation permission or pool status', () => {
  assert.deepEqual(request(real, [colabConnectedWithOtherStatus]), {
    provider: 'colab',
    accountId: 'account-colab-only',
  });
});

test('offline mode and disabled real GPU always use mock without an accountId', () => {
  assert.deepEqual(request({ real_enabled: true, offline_mode: true }, [connectedCli]), {
    provider: 'mock',
    accountId: null,
    reason: 'offline',
  });
  assert.deepEqual(request({ real_enabled: false, offline_mode: false }, [connectedCli]), {
    provider: 'mock',
    accountId: null,
    reason: 'real_disabled',
  });
});

test('migrated connected owner can query specs even when auto-allocation is disabled', () => {
  const migrated = { ...connectedLegacyOwner, allow_allocation: false };
  assert.deepEqual(request(real, [migrated]), {
    provider: 'colab',
    accountId: 'legacy-default-owner',
  });
  assert.deepEqual(resolveGpuCheckRequest(real, [migrated], null, true), {
    provider: 'mock',
    accountId: null,
    reason: 'account_unavailable',
  });
});

test('workflow activation selects eligible GPU and preserves its structured key', () => {
  const cpu = { key: { variant: 'VARIANT_CPU', accelerator: 'NONE', shape: 'SHAPE_STANDARD' }, eligible: true };
  const tpu = { key: { variant: 'VARIANT_TPU', accelerator: 'V5E', shape: 'SHAPE_STANDARD' }, eligible: true };
  const gpu = { key: { variant: 'VARIANT_GPU', accelerator: 'T4', shape: 'SHAPE_STANDARD' }, eligible: true };
  assert.deepEqual(selectEligibleColabGpuSpec([cpu, tpu, gpu]), gpu);
  assert.notStrictEqual(selectEligibleColabGpuSpec([cpu, tpu, gpu]).key, String(gpu.key));
});
