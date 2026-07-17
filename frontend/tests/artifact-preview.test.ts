import assert from 'node:assert/strict';
import test from 'node:test';

import {
  hardenHtmlArtifactPreview,
  HTML_ARTIFACT_PREVIEW_MAX_BYTES,
} from '../src/features/chat/lib/artifact-preview.ts';

test('HTML artifact preview starts with a network-denying CSP', () => {
  const remoteMarker = 'https://private.example/image.png';
  const secured = hardenHtmlArtifactPreview(`<img src="${remoteMarker}">`);

  assert.ok(secured.startsWith('<meta http-equiv="Content-Security-Policy"'));
  assert.match(secured, /default-src 'none'/);
  assert.match(secured, /connect-src 'none'/);
  assert.match(secured, /img-src data: blob:/);
  assert.match(secured, /form-action 'none'/);
  assert.ok(secured.indexOf('Content-Security-Policy') < secured.indexOf(remoteMarker));
});

test('HTML artifact preview removes meta refresh navigation', () => {
  const secured = hardenHtmlArtifactPreview(
    '<META content="0; url=https://private.example" HTTP-EQUIV=refresh><p>report</p>',
  );

  assert.doesNotMatch(secured, /private\.example/);
  assert.match(secured, /<p>report<\/p>/);
});

test('HTML artifact preview has an explicit bounded size', () => {
  assert.equal(HTML_ARTIFACT_PREVIEW_MAX_BYTES, 5 * 1024 * 1024);
});
