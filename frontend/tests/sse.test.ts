import assert from 'node:assert/strict';
import test from 'node:test';

import {
  consumeServerSentEvents,
  type ServerSentEventFrame,
} from '../src/shared/api/sse.ts';

const streamChunks = (chunks: Uint8Array[]): ReadableStream<Uint8Array> => (
  new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(chunk));
      controller.close();
    },
  })
);

test('SSE frames survive UTF-8 chunk splits and a terminal frame without a newline', async () => {
  const encoder = new TextEncoder();
  const payload = encoder.encode(
    'event: messages\r\ndata: {"text":"你好"}\r\n\r\nevent: end\r\ndata: [DONE]',
  );
  const splitInsideChineseCharacter = payload.indexOf(0xe4) + 1;
  const frames: ServerSentEventFrame[] = [];

  const stopped = await consumeServerSentEvents(
    streamChunks([
      payload.slice(0, splitInsideChineseCharacter),
      payload.slice(splitInsideChineseCharacter, splitInsideChineseCharacter + 2),
      payload.slice(splitInsideChineseCharacter + 2),
    ]),
    (frame) => {
      frames.push(frame);
      return frame.event === 'end';
    },
  );

  assert.equal(stopped, true);
  assert.deepEqual(frames, [
    { event: 'messages', data: '{"text":"你好"}' },
    { event: 'end', data: '[DONE]' },
  ]);
});

test('SSE multi-line data removes only the protocol separator space', async () => {
  const frames: ServerSentEventFrame[] = [];
  const encoder = new TextEncoder();

  const stopped = await consumeServerSentEvents(
    streamChunks([encoder.encode('event: custom\ndata: first\ndata:   second\n\n')]),
    (frame) => {
      frames.push(frame);
    },
  );

  assert.equal(stopped, false);
  assert.deepEqual(frames, [
    { event: 'custom', data: 'first\n  second' },
  ]);
});
