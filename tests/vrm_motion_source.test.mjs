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
  assert.match(source, /solveIkLink/);
  assert.match(source, /rightMiddleProximal/);
  assert.match(source, /companionWaveDepth/);
  assert.match(source, /retargetMesh2MotionClip/);
  assert.match(source, /mocapActions/);
  assert.match(source, /beginAvatarDrag/);
  assert.match(source, /stage_x/);
  assert.match(source, /startMotionCombo/);
  assert.match(source, /comboQueue/);
  assert.match(source, /applySelfCollisionAvoidance/);
  assert.match(source, /torsoPenetration/);
  assert.match(source, /headPenetration/);
  assert.match(source, /companionCollisionCorrections/);
  assert.match(source, /loadBundledIdleAction/);
  assert.match(source, /hikari-idle\.vrma/);
  assert.match(source, /idleArmTargets/);
  assert.match(source, /applyIdleHandClearance/);
  assert.match(source, /companionIdleHandCorrections/);
  assert.match(source, /applyIdleArmRestPose/);
  assert.match(source, /idleArmRestQuaternions/);
  assert.match(source, /classifyAssistantMotion/);
  assert.match(source, /triggerSemanticMotion/);
  assert.match(source, /companionSemanticSequence/);
  assert.match(source, /motion\.gestures/);
  assert.match(source, /companion-ai-intent/);
  assert.match(source, /companionAiGesture/);
  assert.match(source, /companionAiGestureTriggered/);
  assert.match(source, /companionLastActionDuration/);
  assert.match(source, /playbackDuration/);
  assert.match(source, /setDuration/);
  assert.match(source, /companionActionHoldMs/);
  assert.match(source, /remainingMs/);
  assert.match(source, /repeatsIntent/);
  assert.doesNotMatch(source, /companion-semantic-input/);
  assert.doesNotMatch(source, /updateProceduralPose/);

  const mocap = "companion_assets/motions/human-addon-animations.glb";
  assert.ok(fs.statSync(mocap).size > 1_000_000);
  assert.match(
    fs.readFileSync("companion_assets/motions/MESH2MOTION_LICENSE.md", "utf8"),
    /CC0 1\.0 Universal/,
  );
  assert.ok(fs.statSync("companion_assets/motions/hikari-idle.vrma").size > 90_000);
  assert.match(
    fs.readFileSync("companion_assets/motions/HIKARI_IDLE_LICENSE.md", "utf8"),
    /MIT License/,
  );
});
