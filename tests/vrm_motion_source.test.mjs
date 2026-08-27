import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

test("built-in VRM gestures use quaternion clips and mixer cross-fades", () => {
  const source = fs.readFileSync(
    "integrations/vrm_web/src/vrm_renderer.js",
    "utf8",
  );
  assert.match(source, /QuaternionKeyframeTrack/);
  assert.match(source, /AnimationMixer/);
  assert.match(source, /crossFadeFrom/);
  assert.match(source, /prepareBuiltinAnimations/);
  assert.doesNotMatch(source, /updateProceduralPose/);
});
