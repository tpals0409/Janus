import assert from 'node:assert/strict'
import test from 'node:test'
import { BoundedCapture, normalizePreviewUrl, taskBrowserPartition } from './task-browser.ts'

test('preview URL accepts only loopback HTTP endpoints', () => {
  assert.equal(normalizePreviewUrl('localhost:5173/app'), 'http://localhost:5173/app')
  assert.equal(normalizePreviewUrl('https://127.0.0.1:8443/'), 'https://127.0.0.1:8443/')
  assert.throws(() => normalizePreviewUrl('https://example.com'), /localhost/)
  assert.throws(() => normalizePreviewUrl('file:///tmp/secret'), /localhost/)
})

test('browser profile partition is stable and isolated by Task', () => {
  const first = taskBrowserPartition('task_abc123')
  assert.equal(first, taskBrowserPartition('task_abc123'))
  assert.notEqual(first, taskBrowserPartition('task_def456'))
  assert.match(first, /^persist:janus-task-[a-f0-9]{24}$/)
  assert.throws(() => taskBrowserPartition('../task_escape'))
})

test('console and network captures remain bounded', () => {
  const capture = new BoundedCapture<number>(3)
  for (let value = 0; value < 6; value += 1) capture.add(value)
  assert.deepEqual(capture.snapshot(), [3, 4, 5])
})
