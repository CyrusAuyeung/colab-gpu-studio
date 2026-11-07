import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { transform } from 'esbuild';
test('instance timestamps accept Unix seconds and ISO dates', async () => {
 const source=readFileSync('src/lib/instance-time.ts','utf8');
 const {code}=await transform(source,{loader:'ts',format:'esm'});
 const {formatInstanceTime}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
 assert.equal(formatInstanceTime(1794018994),new Date(1794018994000).toLocaleString());
 assert.equal(formatInstanceTime('2026-11-07T00:00:00Z'),new Date('2026-11-07T00:00:00Z').toLocaleString());
 assert.equal(formatInstanceTime(null),'—');
});
