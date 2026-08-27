import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import {
  VRMAnimationLoaderPlugin,
  createVRMAnimationClip,
} from "@pixiv/three-vrm-animation";
import { WLipSyncEngine, VISEME_NAMES } from "three-vrm-lip-sync";

const state = {
  active: false,
  confUid: "",
  settings: null,
  profile: null,
  vrm: null,
  renderer: null,
  scene: null,
  camera: null,
  canvas: null,
  live2dCanvas: null,
  resizeObserver: null,
  elapsed: 0,
  lastFrame: 0,
  lipEngine: null,
  audioContext: null,
  audioNodes: new WeakMap(),
  emotion: "neutral",
  emotionUntil: 0,
  speaking: false,
  gesture: "idle",
  gestureStartedAt: 0,
  gestureDuration: 0,
  gestureCooldownUntil: 0,
  lastGesture: "",
  mixer: null,
  vrmaActions: new Map(),
  mocapActions: new Map(),
  builtinActions: new Map(),
  activeVrmaAction: null,
  motionSource: "idle",
  motionCombos: [],
  comboQueue: [],
  activeCombo: null,
  drag: null,
  gaze: { x: 0, y: 0, targetX: 0, targetY: 0 },
  lookAtTarget: null,
  lookAtBase: new THREE.Vector3(0, 1.5, 1),
  host: null,
  lastError: "",
  lastAssistantIntentText: "",
  lastAssistantIntentAt: 0,
};

const expressionMap = {
  happy: "happy",
  happiness: "happy",
  joy: "happy",
  smile: "happy",
  smirk: "happy",
  sad: "sad",
  sadness: "sad",
  sorrow: "sad",
  angry: "angry",
  anger: "angry",
  surprise: "surprised",
  surprised: "surprised",
  excited: "happy",
  shy: "relaxed",
  fear: "surprised",
  scared: "surprised",
  disgust: "angry",
  relaxed: "relaxed",
  calm: "relaxed",
  caring: "relaxed",
};

const emotionGestureMap = {
  happy: "celebrate",
  happiness: "celebrate",
  joy: "celebrate",
  excited: "celebrate",
  sad: "shy",
  sadness: "shy",
  sorrow: "shy",
  shy: "shy",
  angry: "angry",
  anger: "angry",
  disgust: "angry",
  surprise: "surprised",
  surprised: "surprised",
  fear: "surprised",
};

const gestureDurations = {
  wave: 2.4,
  nod: 1.45,
  shake: 1.55,
  think: 2.8,
  shy: 2.5,
  emphasize: 1.7,
  celebrate: 2.2,
  surprised: 1.4,
  bow: 2.5,
  dance: 4.0,
  meditate: 4.0,
  angry: 2.5,
  confused: 3.0,
  listen: 3.0,
  cheer: 3.0,
  shiver: 3.0,
  tired: 3.0,
  idle: 0,
};

const mesh2MotionRigMap = {
  pelvis: "hips",
  spine_01: "spine",
  spine_02: "chest",
  spine_03: "upperChest",
  neck_01: "neck",
  head: "head",
  clavicle_l: "leftShoulder",
  upperarm_l: "leftUpperArm",
  lowerarm_l: "leftLowerArm",
  hand_l: "leftHand",
  clavicle_r: "rightShoulder",
  upperarm_r: "rightUpperArm",
  lowerarm_r: "rightLowerArm",
  hand_r: "rightHand",
  thigh_l: "leftUpperLeg",
  calf_l: "leftLowerLeg",
  foot_l: "leftFoot",
  ball_l: "leftToes",
  thigh_r: "rightUpperLeg",
  calf_r: "rightLowerLeg",
  foot_r: "rightFoot",
  ball_r: "rightToes",
};

for (const side of ["l", "r"]) {
  const prefix = side === "l" ? "left" : "right";
  Object.assign(mesh2MotionRigMap, {
    [`thumb_01_${side}`]: `${prefix}ThumbMetacarpal`,
    [`thumb_02_${side}`]: `${prefix}ThumbProximal`,
    [`thumb_03_${side}`]: `${prefix}ThumbDistal`,
    [`index_01_${side}`]: `${prefix}IndexProximal`,
    [`index_02_${side}`]: `${prefix}IndexIntermediate`,
    [`index_03_${side}`]: `${prefix}IndexDistal`,
    [`middle_01_${side}`]: `${prefix}MiddleProximal`,
    [`middle_02_${side}`]: `${prefix}MiddleIntermediate`,
    [`middle_03_${side}`]: `${prefix}MiddleDistal`,
    [`ring_01_${side}`]: `${prefix}RingProximal`,
    [`ring_02_${side}`]: `${prefix}RingIntermediate`,
    [`ring_03_${side}`]: `${prefix}RingDistal`,
    [`pinky_01_${side}`]: `${prefix}LittleProximal`,
    [`pinky_02_${side}`]: `${prefix}LittleIntermediate`,
    [`pinky_03_${side}`]: `${prefix}LittleDistal`,
  });
}

const mocapGestureClips = {
  wave: "Greeting",
  nod: "Head Nod",
  shake: "Reject",
  think: "Confused",
  shy: "Idle Listening",
  celebrate: "Victory",
  surprised: "Confused",
  bow: "Bow",
  dance: "Dance Body Roll",
  meditate: "Meditate",
  angry: "Angry",
  confused: "Confused",
  listen: "Idle Listening",
  cheer: "Victory Fist Pump",
  shiver: "Shivering",
  tired: "Tired Hunched",
};

function emitStatus(status, detail = "") {
  document.documentElement.dataset.companionRenderer = state.active ? "vrm" : "live2d";
  document.documentElement.dataset.companionVrmStatus = status;
  document.documentElement.dataset.companionVrmDetail = detail;
  window.dispatchEvent(
    new CustomEvent("companion-vrm-status", {
      detail: { status, detail, active: state.active, confUid: state.confUid },
    }),
  );
}

function emitGesture(name, source = "procedural") {
  document.documentElement.dataset.companionGesture = name;
  document.documentElement.dataset.companionMotionSource = source;
  window.dispatchEvent(
    new CustomEvent("companion-vrm-gesture", { detail: { name, source } }),
  );
}

function observeWebSockets() {
  if (window.__companionObservedWebSocket) return;
  const NativeWebSocket = window.WebSocket;
  class ObservedWebSocket extends NativeWebSocket {
    constructor(...args) {
      super(...args);
      this.addEventListener("message", (event) => {
        if (typeof event.data !== "string") return;
        try {
          handleMessage(JSON.parse(event.data));
        } catch (_) {}
      });
    }
  }
  window.WebSocket = ObservedWebSocket;
  window.__companionObservedWebSocket = true;
}

function handleMessage(message) {
  if (message?.type === "set-model-and-conf" && message.conf_uid) {
    void selectCharacter(String(message.conf_uid));
    return;
  }
  if (message?.type === "companion-ai-intent") {
    const expressions = message.actions?.expressions || [];
    const requested = expressions.find((item) => typeof item === "string");
    if (requested) setEmotion(requested, 5000);
    const text = String(message.display_text?.text || "");
    const gesture = chooseSemanticGesture(text, requested || "neutral");
    state.lastAssistantIntentText = text;
    state.lastAssistantIntentAt = performance.now();
    document.documentElement.dataset.companionAiIntent = text.slice(0, 80);
    document.documentElement.dataset.companionAiGesture = gesture || "none";
    const triggered = gesture ? triggerGesture(gesture, text, true) : false;
    document.documentElement.dataset.companionAiGestureTriggered = String(
      triggered,
    );
    return;
  }
  if (message?.type === "audio") {
    const expressions = message.actions?.expressions || [];
    const requested = expressions.find((item) => typeof item === "string");
    if (requested) setEmotion(requested, 5000);
    const text = String(message.display_text?.text || "");
    const gesture = chooseSemanticGesture(text, requested || "neutral");
    const explicitGesture =
      Boolean(requested) || ["wave", "nod", "shake", "think"].includes(gesture);
    const repeatsIntent =
      text === state.lastAssistantIntentText &&
      performance.now() - state.lastAssistantIntentAt < 15000;
    if (gesture && !repeatsIntent) triggerGesture(gesture, text, explicitGesture);
  }
}

async function waitForLive2DCanvas(timeoutMs = 12000) {
  const existing = document.querySelector("#canvas");
  if (existing) return existing;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      observer.disconnect();
      reject(new Error("Live2D canvas was not created"));
    }, timeoutMs);
    const observer = new MutationObserver(() => {
      const canvas = document.querySelector("#canvas");
      if (!canvas) return;
      clearTimeout(timeout);
      observer.disconnect();
      resolve(canvas);
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  });
}

async function ensureRenderer() {
  if (state.renderer) return;
  state.live2dCanvas = await waitForLive2DCanvas();
  const host = state.live2dCanvas.parentElement;
  if (!host) throw new Error("Live2D canvas has no container");
  if (getComputedStyle(host).position === "static") host.style.position = "relative";
  state.host = host;
  host.addEventListener("pointermove", (event) => {
    const rect = host.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    state.gaze.targetX = THREE.MathUtils.clamp(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -1,
      1,
    );
    state.gaze.targetY = THREE.MathUtils.clamp(
      ((event.clientY - rect.top) / rect.height) * 2 - 1,
      -1,
      1,
    );
  });
  host.addEventListener("pointerleave", () => {
    state.gaze.targetX = 0;
    state.gaze.targetY = 0;
  });
  host.addEventListener("pointerdown", beginAvatarDrag);
  window.addEventListener("companion-speech-start", () => {
    state.speaking = true;
  });
  window.addEventListener("companion-speech-end", () => {
    state.speaking = false;
  });

  const canvas = document.createElement("canvas");
  canvas.id = "companion-vrm-canvas";
  canvas.setAttribute("aria-label", "VRM 3D角色");
  Object.assign(canvas.style, {
    position: "absolute",
    inset: "0",
    width: "100%",
    height: "100%",
    pointerEvents: "none",
    display: "none",
    zIndex: "2",
  });
  host.appendChild(canvas);

  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = true;

  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xe9eeff, 0x29334d, 2.1));
  const key = new THREE.DirectionalLight(0xfff3e0, 2.4);
  key.position.set(-1.5, 2.7, -2.5);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0x9ab7ff, 1.1);
  fill.position.set(2.5, 1.4, -1);
  scene.add(fill);
  const lookAtTarget = new THREE.Object3D();
  lookAtTarget.name = "CompanionLookAtTarget";
  scene.add(lookAtTarget);

  const camera = new THREE.PerspectiveCamera(28, 1, 0.01, 100);
  state.canvas = canvas;
  state.renderer = renderer;
  state.scene = scene;
  state.camera = camera;
  state.lookAtTarget = lookAtTarget;

  const resize = () => {
    const rect = host.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    renderer.setSize(rect.width, rect.height, false);
    camera.aspect = rect.width / rect.height;
    camera.updateProjectionMatrix();
  };
  state.resizeObserver = new ResizeObserver(resize);
  state.resizeObserver.observe(host);
  resize();
  state.lastFrame = performance.now();
  requestAnimationFrame(renderFrame);
}

function disposeCurrentModel() {
  if (!state.vrm) return;
  state.scene?.remove(state.vrm.scene);
  VRMUtils.deepDispose(state.vrm.scene);
  state.vrm = null;
  state.lipEngine?.dispose();
  state.lipEngine = null;
  state.audioContext?.close().catch(() => {});
  state.audioContext = null;
  state.mixer?.stopAllAction();
  state.mixer = null;
  state.vrmaActions.clear();
  state.mocapActions.clear();
  state.builtinActions.clear();
  state.activeVrmaAction = null;
}

const basePose = {
  head: [0, 0, 0],
  neck: [0, 0, 0],
  chest: [0, 0, 0],
  spine: [0, 0, 0],
  leftShoulder: [0, 0, 0],
  rightShoulder: [0, 0, 0],
  leftUpperArm: [-0.08, 0, 1.22],
  rightUpperArm: [-0.08, 0, -1.22],
  leftLowerArm: [0, 0, 0.12],
  rightLowerArm: [0, 0, -0.12],
  leftHand: [0, 0, 0],
  rightHand: [0, 0, 0],
};

function poseWith(overrides = {}) {
  const settings = state.settings || {};
  const styleFactor = settings.action_style === "subtle" ? 0.72 : settings.action_style === "expressive" ? 1.16 : 1;
  const intensity = THREE.MathUtils.clamp(
    (Number(settings.gesture_intensity) || 0) * styleFactor,
    0,
    1.15,
  );
  return Object.fromEntries(
    Object.entries(basePose).map(([bone, rotation]) => [
      bone,
      overrides[bone]
        ? rotation.map(
            (value, index) => value + (overrides[bone][index] - value) * intensity,
          )
        : rotation,
    ]),
  );
}

// Mirrors the official three-vrm animation example: normalized humanoid bones
// are driven by QuaternionKeyframeTracks and blended by a single AnimationMixer.
function createPoseClip(name, duration, keyframes, { loop = false } = {}) {
  const tracks = [];
  const boneNames = new Set(keyframes.flatMap((frame) => Object.keys(frame.pose)));
  for (const boneName of boneNames) {
    const node = state.vrm?.humanoid?.getNormalizedBoneNode(boneName);
    if (!node) continue;
    const times = [];
    const values = [];
    for (const frame of keyframes) {
      const rotation = frame.pose[boneName];
      if (!rotation) continue;
      const quaternion = new THREE.Quaternion().setFromEuler(
        new THREE.Euler(rotation[0], rotation[1], rotation[2], "XYZ"),
      );
      times.push(frame.time);
      values.push(...quaternion.toArray());
    }
    if (times.length >= 2) {
      tracks.push(
        new THREE.QuaternionKeyframeTrack(
          `${node.name}.quaternion`,
          times,
          values,
          THREE.InterpolateLinear,
        ),
      );
    }
  }

  const hips = state.vrm?.humanoid?.getNormalizedBoneNode("hips");
  const positionFrames = keyframes.filter((frame) => frame.hips);
  if (hips && positionFrames.length >= 2) {
    const rest = hips.position.clone();
    tracks.push(
      new THREE.VectorKeyframeTrack(
        `${hips.name}.position`,
        positionFrames.map((frame) => frame.time),
        positionFrames.flatMap((frame) => [
          rest.x + (frame.hips[0] || 0),
          rest.y + (frame.hips[1] || 0),
          rest.z + (frame.hips[2] || 0),
        ]),
        THREE.InterpolateSmooth,
      ),
    );
  }

  const clip = new THREE.AnimationClip(name, duration, tracks);
  const action = state.mixer.clipAction(clip);
  action.enabled = true;
  action.clampWhenFinished = false;
  action.zeroSlopeAtStart = true;
  action.zeroSlopeAtEnd = true;
  action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, loop ? Infinity : 1);
  state.builtinActions.set(name, { action, profile: { loop, name, gesture: name } });
}

function prepareBuiltinAnimations() {
  const base = poseWith();
  createPoseClip(
    "idle",
    6.4,
    [
      { time: 0, pose: base, hips: [0, 0, 0] },
      { time: 1.6, pose: poseWith({ chest: [-0.008, 0.008, 0], head: [0.008, -0.012, 0.006] }), hips: [0, 0.003, 0] },
      { time: 3.2, pose: poseWith({ chest: [0.006, -0.006, 0], head: [-0.004, 0.014, -0.005] }), hips: [0, 0, 0] },
      { time: 4.8, pose: poseWith({ chest: [-0.006, 0.004, 0], head: [0.006, 0.006, 0.004] }), hips: [0, 0.002, 0] },
      { time: 6.4, pose: base, hips: [0, 0, 0] },
    ],
    { loop: true },
  );

  createPoseClip("wave", 2.75, [
    { time: 0, pose: base },
    { time: 0.16, pose: poseWith({ chest: [0, 0.025, 0], head: [0, -0.035, 0] }) },
    { time: 0.58, pose: poseWith({ rightShoulder: [0, 0.1, -0.08], rightUpperArm: [-0.17, 0.38, -0.42], rightLowerArm: [0.05, 0.08, 1.55], rightHand: [0, 1.02, 0], chest: [0, -0.025, 0.012], spine: [0, -0.01, 0], head: [0, 0.035, 0.018] }) },
    { time: 0.92, pose: poseWith({ rightShoulder: [0, 0.11, -0.08], rightUpperArm: [-0.18, 0.46, -0.38], rightLowerArm: [0.18, 0.1, 1.68], rightHand: [0, 1.02, 0], chest: [0, -0.03, 0.014], spine: [0, -0.012, 0], head: [0, 0.04, 0.015] }) },
    { time: 1.22, pose: poseWith({ rightShoulder: [0, 0.1, -0.08], rightUpperArm: [-0.17, 0.42, -0.4], rightLowerArm: [-0.17, 0.06, 1.57], rightHand: [0, 1.02, 0], chest: [0, -0.025, 0.012], spine: [0, -0.01, 0], head: [0, 0.032, 0.012] }) },
    { time: 1.52, pose: poseWith({ rightShoulder: [0, 0.11, -0.08], rightUpperArm: [-0.18, 0.46, -0.38], rightLowerArm: [0.17, 0.1, 1.68], rightHand: [0, 1.02, 0], chest: [0, -0.03, 0.014], spine: [0, -0.012, 0], head: [0, 0.04, 0.015] }) },
    { time: 1.82, pose: poseWith({ rightShoulder: [0, 0.1, -0.08], rightUpperArm: [-0.17, 0.42, -0.4], rightLowerArm: [-0.15, 0.06, 1.57], rightHand: [0, 1.02, 0], chest: [0, -0.025, 0.012], spine: [0, -0.01, 0], head: [0, 0.032, 0.012] }) },
    { time: 2.18, pose: poseWith({ rightUpperArm: [-0.14, 0.2, -0.62], rightLowerArm: [0.02, 0.03, 0.84], rightHand: [0, 0.35, 0], chest: [0, -0.012, 0], head: [0, 0.018, 0] }) },
    { time: 2.75, pose: base },
  ]);

  createPoseClip("nod", 1.65, [
    { time: 0, pose: base },
    { time: 0.18, pose: poseWith({ head: [-0.035, 0, 0], chest: [-0.008, 0, 0] }) },
    { time: 0.48, pose: poseWith({ head: [0.15, 0, 0], neck: [0.035, 0, 0], chest: [0.018, 0, 0] }) },
    { time: 0.72, pose: poseWith({ head: [-0.055, 0, 0], neck: [-0.012, 0, 0] }) },
    { time: 1.02, pose: poseWith({ head: [0.105, 0, 0], neck: [0.02, 0, 0], chest: [0.01, 0, 0] }) },
    { time: 1.28, pose: poseWith({ head: [-0.025, 0, 0] }) },
    { time: 1.65, pose: base },
  ]);

  createPoseClip("shake", 1.8, [
    { time: 0, pose: base },
    { time: 0.18, pose: poseWith({ head: [0, 0.04, 0], chest: [0, -0.008, 0] }) },
    { time: 0.48, pose: poseWith({ head: [0, -0.17, -0.008], neck: [0, -0.035, 0], chest: [0, 0.025, 0] }) },
    { time: 0.78, pose: poseWith({ head: [0, 0.18, 0.008], neck: [0, 0.035, 0], chest: [0, -0.025, 0] }) },
    { time: 1.08, pose: poseWith({ head: [0, -0.13, -0.006], neck: [0, -0.025, 0] }) },
    { time: 1.38, pose: poseWith({ head: [0, 0.07, 0.004] }) },
    { time: 1.8, pose: base },
  ]);

  createPoseClip("think", 3.2, [
    { time: 0, pose: base },
    { time: 0.32, pose: poseWith({ head: [-0.015, 0.025, -0.025], chest: [0, -0.012, 0] }) },
    { time: 0.85, pose: poseWith({ head: [0.045, -0.105, 0.105], neck: [0.018, -0.028, 0.025], chest: [0.018, -0.035, 0.01], rightShoulder: [0, 0, -0.025] }) },
    { time: 2.25, pose: poseWith({ head: [0.035, -0.09, 0.095], neck: [0.012, -0.025, 0.022], chest: [0.014, -0.03, 0.008], rightShoulder: [0, 0, -0.02] }) },
    { time: 2.72, pose: poseWith({ head: [-0.018, 0.018, -0.018], chest: [-0.006, 0.01, 0] }) },
    { time: 3.2, pose: base },
  ]);

  createPoseClip("shy", 2.8, [
    { time: 0, pose: base },
    { time: 0.4, pose: poseWith({ head: [0.04, 0.025, -0.025], chest: [0.025, 0, 0], leftShoulder: [0, 0, 0.025], rightShoulder: [0, 0, -0.025] }) },
    { time: 0.95, pose: poseWith({ head: [0.11, -0.045, 0.065], neck: [0.025, -0.012, 0.015], chest: [0.06, 0.018, 0], leftUpperArm: [-0.04, 0.06, 1.31], rightUpperArm: [-0.04, -0.06, -1.31] }) },
    { time: 2.05, pose: poseWith({ head: [0.095, -0.035, 0.055], neck: [0.02, -0.01, 0.012], chest: [0.052, 0.015, 0], leftUpperArm: [-0.04, 0.05, 1.3], rightUpperArm: [-0.04, -0.05, -1.3] }) },
    { time: 2.8, pose: base },
  ]);

  createPoseClip("emphasize", 1.95, [
    { time: 0, pose: base },
    { time: 0.2, pose: poseWith({ chest: [0, 0.018, 0], head: [0, -0.018, 0] }) },
    { time: 0.58, pose: poseWith({ rightShoulder: [0, 0.08, -0.035], rightUpperArm: [-0.12, 0.32, -0.7], rightLowerArm: [0.04, 0.05, 0.55], rightHand: [0, 0.82, 0], chest: [-0.015, -0.04, 0.008], head: [0.01, 0.035, 0.01] }) },
    { time: 0.94, pose: poseWith({ rightShoulder: [0, 0.05, -0.025], rightUpperArm: [-0.1, 0.2, -0.85], rightLowerArm: [0.02, 0.03, 0.35], rightHand: [0, 0.68, 0], chest: [0, -0.02, 0], head: [0, 0.015, 0] }) },
    { time: 1.28, pose: poseWith({ rightShoulder: [0, 0.09, -0.04], rightUpperArm: [-0.13, 0.35, -0.68], rightLowerArm: [0.04, 0.06, 0.58], rightHand: [0, 0.84, 0], chest: [-0.012, -0.038, 0.008], head: [0.008, 0.032, 0.008] }) },
    { time: 1.95, pose: base },
  ]);

  createPoseClip("celebrate", 2.65, [
    { time: 0, pose: base, hips: [0, 0, 0] },
    { time: 0.22, pose: poseWith({ chest: [0.025, 0, 0], head: [0.025, 0, 0] }), hips: [0, -0.008, 0] },
    { time: 0.72, pose: poseWith({ leftShoulder: [0, 0.02, 0.06], rightShoulder: [0, -0.025, -0.07], leftUpperArm: [-0.18, 0.14, 0.64], rightUpperArm: [-0.14, -0.08, -0.82], leftLowerArm: [0.08, -0.03, 0.5], rightLowerArm: [0.02, 0.04, -0.18], leftHand: [0, -0.1, 0.12], rightHand: [0, 0.05, -0.05], chest: [-0.055, -0.035, -0.012], spine: [-0.018, -0.012, 0], head: [-0.035, 0.035, 0.025] }), hips: [-0.006, 0.012, 0] },
    { time: 1.15, pose: poseWith({ leftUpperArm: [-0.16, 0.1, 0.72], rightUpperArm: [-0.18, -0.13, -0.66], leftLowerArm: [0.04, 0, 0.34], rightLowerArm: [0.04, 0, -0.3], chest: [-0.04, 0.02, 0.01], head: [-0.02, -0.02, -0.018] }), hips: [0.004, 0.002, 0] },
    { time: 1.58, pose: poseWith({ leftShoulder: [0, 0.015, 0.05], rightShoulder: [0, -0.02, -0.065], leftUpperArm: [-0.17, 0.12, 0.66], rightUpperArm: [-0.15, -0.09, -0.78], leftLowerArm: [0.06, -0.02, 0.46], rightLowerArm: [0.03, 0.03, -0.2], chest: [-0.05, -0.025, -0.01], head: [-0.03, 0.025, 0.02] }), hips: [-0.004, 0.009, 0] },
    { time: 2.08, pose: poseWith({ leftUpperArm: [-0.1, 0.04, 0.98], rightUpperArm: [-0.1, -0.04, -0.98], chest: [-0.012, 0, 0], head: [0.005, 0, 0] }), hips: [0, 0, 0] },
    { time: 2.65, pose: base, hips: [0, 0, 0] },
  ]);

  createPoseClip("surprised", 1.7, [
    { time: 0, pose: base },
    { time: 0.16, pose: poseWith({ head: [0.035, 0, 0], chest: [0.025, 0, 0] }) },
    { time: 0.48, pose: poseWith({ head: [-0.07, 0, 0], neck: [-0.015, 0, 0], chest: [-0.045, 0, 0], leftShoulder: [0, 0, 0.04], rightShoulder: [0, 0, -0.04], leftUpperArm: [-0.1, 0.04, 0.97], rightUpperArm: [-0.1, -0.04, -0.97] }) },
    { time: 1.05, pose: poseWith({ head: [-0.045, 0, 0], chest: [-0.025, 0, 0], leftUpperArm: [-0.09, 0.02, 1.05], rightUpperArm: [-0.09, -0.02, -1.05] }) },
    { time: 1.7, pose: base },
  ]);
}

// Mesh2Motion publishes its animation assets under CC0. This retargeter follows
// the same normalized-rest-pose approach used by Google's XRBlocks VRM demo.
function retargetMesh2MotionClip(sourceClip, sourceScene, vrm) {
  const tracks = [];
  const sourceRestInverse = new THREE.Quaternion();
  const sourceParentRest = new THREE.Quaternion();
  const animatedQuaternion = new THREE.Quaternion();
  const isVrm0 = vrm.meta?.metaVersion === "0";
  sourceScene.updateMatrixWorld(true);

  for (const track of sourceClip.tracks) {
    if (!(track instanceof THREE.QuaternionKeyframeTrack)) continue;
    const [sourceName] = track.name.split(".");
    const sourceNode =
      sourceScene.getObjectByName(sourceName) ||
      sourceScene.getObjectByProperty("uuid", sourceName);
    const vrmBoneName = sourceNode ? mesh2MotionRigMap[sourceNode.name] : null;
    const targetNode = vrmBoneName
      ? vrm.humanoid?.getNormalizedBoneNode(vrmBoneName)
      : null;
    if (!sourceNode || !sourceNode.parent || !targetNode) continue;

    sourceNode.getWorldQuaternion(sourceRestInverse).invert();
    sourceNode.parent.getWorldQuaternion(sourceParentRest);
    const values = new Float32Array(track.values.length);
    for (let index = 0; index < track.values.length; index += 4) {
      animatedQuaternion
        .fromArray(track.values, index)
        .premultiply(sourceParentRest)
        .multiply(sourceRestInverse);
      if (isVrm0) {
        animatedQuaternion.x *= -1;
        animatedQuaternion.z *= -1;
      }
      animatedQuaternion.toArray(values, index);
    }
    tracks.push(
      new THREE.QuaternionKeyframeTrack(
        `${targetNode.name}.quaternion`,
        track.times,
        values,
        THREE.InterpolateLinear,
      ),
    );
  }

  return new THREE.AnimationClip(
    `mocap:${sourceClip.name}`,
    sourceClip.duration,
    tracks,
  ).optimize();
}

async function loadBundledMocapActions() {
  state.mocapActions.clear();
  if (!state.vrm || !state.mixer) return;
  try {
    const loader = new GLTFLoader();
    const gltf = await loader.loadAsync(
      "/companion-assets/motions/human-addon-animations.glb",
    );
    for (const [gesture, clipName] of Object.entries(mocapGestureClips)) {
      const sourceClip = THREE.AnimationClip.findByName(gltf.animations, clipName);
      if (!sourceClip) continue;
      const clip = retargetMesh2MotionClip(sourceClip, gltf.scene, state.vrm);
      if (!clip.tracks.length) continue;
      const action = state.mixer.clipAction(clip);
      const loop = gesture === "idle";
      action.enabled = true;
      action.clampWhenFinished = !loop;
      action.zeroSlopeAtStart = true;
      action.zeroSlopeAtEnd = true;
      action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, loop ? Infinity : 1);
      state.mocapActions.set(gesture, {
        action,
        profile: { loop, name: clipName, gesture },
      });
    }
    document.documentElement.dataset.companionMocapCount = String(
      state.mocapActions.size,
    );
  } catch (error) {
    document.documentElement.dataset.companionMocapCount = "0";
    console.warn("[Companion VRM] CC0动作库加载失败，使用内置动作", error);
  }
}

async function loadBundledIdleAction() {
  if (!state.vrm || !state.mixer) return;
  try {
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMAnimationLoaderPlugin(parser));
    const gltf = await loader.loadAsync(
      "/companion-assets/motions/hikari-idle.vrma",
    );
    const animation = gltf.userData.vrmAnimations?.[0];
    if (!animation) throw new Error("文件中没有VRMC_vrm_animation数据");
    const clip = createVRMAnimationClip(animation, state.vrm);
    const idleArmTargets = new Map();
    for (const [boneName, rotationZ] of [
      ["leftUpperArm", 1.45],
      ["rightUpperArm", -1.45],
    ]) {
      const node = state.vrm.humanoid?.getNormalizedBoneNode(boneName);
      if (!node) continue;
      idleArmTargets.set(
        node.name,
        new THREE.Quaternion().setFromEuler(
          new THREE.Euler(-0.045, 0, rotationZ, "XYZ"),
        ),
      );
    }
    const animatedArmQuaternion = new THREE.Quaternion();
    let adjustedArmTracks = 0;
    for (const track of clip.tracks) {
      if (!(track instanceof THREE.QuaternionKeyframeTrack)) continue;
      const targetName = track.name.slice(0, track.name.lastIndexOf("."));
      const targetQuaternion = idleArmTargets.get(targetName);
      if (!targetQuaternion) continue;
      for (let index = 0; index < track.values.length; index += 4) {
        animatedArmQuaternion
          .fromArray(track.values, index)
          .slerp(targetQuaternion, 0.82)
          .toArray(track.values, index);
      }
      adjustedArmTracks += 1;
    }
    document.documentElement.dataset.companionIdleArmTracksAdjusted = String(
      adjustedArmTracks,
    );
    clip.resetDuration().optimize();
    clip.name = "Hikari Idle";
    const action = state.mixer.clipAction(clip);
    action.enabled = true;
    action.clampWhenFinished = false;
    action.zeroSlopeAtStart = true;
    action.zeroSlopeAtEnd = true;
    action.setLoop(THREE.LoopRepeat, Infinity);
    state.mocapActions.set("idle", {
      action,
      profile: {
        loop: true,
        name: "Hikari Idle",
        gesture: "idle",
        source: "bundled-vrma",
      },
    });
    document.documentElement.dataset.companionIdleSource = "hikari-vrma";
  } catch (error) {
    document.documentElement.dataset.companionIdleSource = "builtin";
    console.warn("[Companion VRM] Hikari待机动作加载失败，使用内置站姿", error);
  }
}

async function loadModel(modelUrl, animations = []) {
  await ensureRenderer();
  disposeCurrentModel();
  emitStatus("loading", "正在加载VRM角色");
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(modelUrl);
  const vrm = gltf.userData.vrm;
  if (!vrm) throw new Error("文件中没有VRM扩展数据");
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons(gltf.scene);
  state.vrm = vrm;
  if (vrm.lookAt && state.settings?.gaze_enabled !== false) {
    vrm.lookAt.target = state.lookAtTarget;
  }
  state.mixer = new THREE.AnimationMixer(vrm.scene);
  state.mixer.addEventListener("finished", (event) => {
    if (event.action !== state.activeVrmaAction) return;
    const finishAction = () => {
      if (event.action !== state.activeVrmaAction) return;
      document.documentElement.dataset.companionLastActionEndedAt = String(
        Math.round(performance.now()),
      );
      if (state.activeCombo) {
        if (state.comboQueue.length) {
          playNextComboStep();
          return;
        }
        state.activeCombo = null;
        delete document.documentElement.dataset.companionMotionCombo;
      }
      state.gesture = "idle";
      emitGesture("idle", "mixer");
      playVrmaGesture("idle");
    };
    const elapsedMs = performance.now() - state.gestureStartedAt;
    const remainingMs = state.gestureDuration * 1000 - elapsedMs;
    if (remainingMs > 50) {
      document.documentElement.dataset.companionActionHoldMs = String(
        Math.round(remainingMs),
      );
      setTimeout(finishAction, remainingMs);
      return;
    }
    finishAction();
  });
  prepareBuiltinAnimations();
  vrm.scene.rotation.y = Math.PI;
  state.scene.add(vrm.scene);
  state.elapsed = 0;
  playVrmaGesture("idle");
  state.mixer.update(0);
  vrm.update(0);
  fitCamera();
  applyRendererVisibility(true);
  await loadBundledMocapActions();
  await loadBundledIdleAction();
  await loadAnimations(animations);
  emitStatus("ready", state.profile?.name || "VRM角色已就绪");
  const query = new URLSearchParams(window.location.search);
  const previewCombo = query.get("combo");
  const previewGesture = query.get("gesture");
  const combo = state.motionCombos.find((item) => item.id === previewCombo);
  if (combo) {
    setTimeout(() => startMotionCombo(combo), 350);
  } else if (gestureDurations[previewGesture]) {
    setTimeout(() => triggerGesture(previewGesture, "preview", true), 350);
  }
}

async function loadAnimations(profiles) {
  state.vrmaActions.clear();
  if (!state.vrm || !state.mixer) return;
  for (const profile of profiles || []) {
    if (!profile?.url || profile.enabled === false) continue;
    try {
      const loader = new GLTFLoader();
      loader.register((parser) => new VRMAnimationLoaderPlugin(parser));
      const gltf = await loader.loadAsync(profile.url);
      const vrmAnimation = gltf.userData.vrmAnimations?.[0];
      if (!vrmAnimation) throw new Error("文件中没有VRMC_vrm_animation数据");
      const clip = createVRMAnimationClip(vrmAnimation, state.vrm);
      clip.name = profile.name || profile.gesture || "VRMA";
      const action = state.mixer.clipAction(clip);
      action.enabled = true;
      action.clampWhenFinished = !profile.loop;
      action.setLoop(profile.loop ? THREE.LoopRepeat : THREE.LoopOnce, profile.loop ? Infinity : 1);
      const gesture = String(profile.gesture || "emphasize");
      const group = state.vrmaActions.get(gesture) || [];
      group.push({ action, profile });
      state.vrmaActions.set(gesture, group);
    } catch (error) {
      console.warn(`[Companion VRM] VRMA加载失败：${profile.name || profile.id}`, error);
    }
  }
  document.documentElement.dataset.companionVrmaCount = String(
    [...state.vrmaActions.values()].reduce((total, group) => total + group.length, 0),
  );
  playVrmaGesture("idle");
}

function fitCamera() {
  if (!state.vrm || !state.camera) return;
  state.vrm.scene.position.set(0, 0, 0);
  state.vrm.scene.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(state.vrm.scene);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const settings = state.settings || {};
  const scale = Number(settings.scale) || 1;
  state.vrm.scene.scale.setScalar(scale);
  const framing = Math.max(0.6, Number(settings.camera_distance) || 1.8);
  const verticalDistance = (size.y * 0.5) / Math.tan(THREE.MathUtils.degToRad(state.camera.fov * 0.5));
  const horizontalDistance =
    (size.x * 0.5) /
    Math.tan(THREE.MathUtils.degToRad(state.camera.fov * 0.5)) /
    Math.max(0.3, state.camera.aspect);
  const motionMargin = 1.08;
  const distance =
    Math.max(verticalDistance, horizontalDistance) * framing * scale * motionMargin;
  const targetY = center.y + (Number(settings.y_offset) || 0) * size.y;
  state.camera.position.set(center.x, targetY, center.z + distance);
  state.camera.lookAt(center.x, targetY, center.z);
  state.camera.near = Math.max(0.01, distance / 100);
  state.camera.far = Math.max(20, distance * 10);
  state.camera.updateProjectionMatrix();
  state.lookAtBase.set(center.x, targetY, center.z + Math.max(0.8, distance * 0.35));
  state.lookAtTarget?.position.copy(state.lookAtBase);
  state.vrm.scene.position.set(
    Number(settings.stage_x) || 0,
    Number(settings.stage_y) || 0,
    0,
  );
  document.documentElement.dataset.companionVrmBounds = [size.x, size.y, size.z]
    .map((value) => value.toFixed(2))
    .join("×");
}

function applyRendererVisibility(useVrm) {
  state.active = Boolean(useVrm && state.vrm);
  if (state.canvas) state.canvas.style.display = state.active ? "block" : "none";
  if (state.live2dCanvas) state.live2dCanvas.style.visibility = state.active ? "hidden" : "visible";
  document.documentElement.dataset.companionRenderer = state.active ? "vrm" : "live2d";
}

const avatarRaycaster = new THREE.Raycaster();
const avatarPointer = new THREE.Vector2();

function pointerHitsAvatar(event) {
  if (!state.active || !state.vrm || !state.camera || !state.host) return false;
  const rect = state.host.getBoundingClientRect();
  avatarPointer.set(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1,
  );
  avatarRaycaster.setFromCamera(avatarPointer, state.camera);
  return avatarRaycaster.intersectObject(state.vrm.scene, true).length > 0;
}

function beginAvatarDrag(event) {
  if (event.button !== 0 || !pointerHitsAvatar(event)) return;
  event.preventDefault();
  state.drag = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    sceneX: state.vrm.scene.position.x,
    sceneY: state.vrm.scene.position.y,
  };
  state.host.style.cursor = "grabbing";
  document.documentElement.dataset.companionAvatarDrag = "active";
  window.addEventListener("pointermove", moveAvatarDrag);
  window.addEventListener("pointerup", endAvatarDrag, { once: true });
}

function moveAvatarDrag(event) {
  if (!state.drag || !state.camera || !state.host || !state.vrm) return;
  const rect = state.host.getBoundingClientRect();
  const distance = state.camera.position.distanceTo(state.lookAtBase);
  const worldHeight =
    2 * distance * Math.tan(THREE.MathUtils.degToRad(state.camera.fov * 0.5));
  const worldPerPixel = worldHeight / Math.max(1, rect.height);
  const nextX = state.drag.sceneX + (event.clientX - state.drag.startX) * worldPerPixel;
  const nextY = state.drag.sceneY - (event.clientY - state.drag.startY) * worldPerPixel;
  state.vrm.scene.position.x = THREE.MathUtils.clamp(nextX, -3, 3);
  state.vrm.scene.position.y = THREE.MathUtils.clamp(nextY, -3, 3);
}

function endAvatarDrag() {
  window.removeEventListener("pointermove", moveAvatarDrag);
  if (!state.drag || !state.vrm) return;
  state.drag = null;
  state.host.style.cursor = "";
  document.documentElement.dataset.companionAvatarDrag = "saved";
  const stageX = Number(state.vrm.scene.position.x.toFixed(4));
  const stageY = Number(state.vrm.scene.position.y.toFixed(4));
  state.settings.stage_x = stageX;
  state.settings.stage_y = stageY;
  fetch(`/api/companion/avatar/${encodeURIComponent(state.confUid)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stage_x: stageX, stage_y: stageY }),
  }).catch((error) => console.warn("[Companion VRM] 角色位置保存失败", error));
}

async function selectCharacter(confUid) {
  state.confUid = confUid;
  try {
    const response = await fetch(`/api/companion/avatar/${encodeURIComponent(confUid)}`);
    if (!response.ok) throw new Error(`角色渲染设置读取失败：${response.status}`);
    const data = await response.json();
    state.settings = data.settings;
    state.profile = data.active;
    state.motionCombos = data.motion_combos || [];
    if (data.settings?.renderer !== "vrm" || !data.model_url) {
      applyRendererVisibility(false);
      emitStatus("live2d", "使用Live2D渲染");
      return;
    }
    await loadModel(data.model_url, data.animations || []);
  } catch (error) {
    state.lastError = String(error?.message || error);
    applyRendererVisibility(false);
    emitStatus("error", state.lastError);
    console.error("[Companion VRM]", error);
  }
}

function setEmotion(name, durationMs = 4000) {
  const mapped = expressionMap[String(name).toLowerCase()] || "neutral";
  state.emotion = mapped;
  state.emotionUntil = performance.now() + durationMs;
}

function hashRatio(text) {
  let hash = 2166136261;
  for (const char of String(text)) {
    hash ^= char.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

function chooseSemanticGesture(text, emotion) {
  const clean = String(text || "").trim();
  const loweredEmotion = String(emotion || "").toLowerCase();
  if (
    /(你好|嗨|哈喽|再见|拜拜|早上好|晚上好|很高兴见到你|见到你真好|欢迎|好久不见|又见面)/.test(
      clean,
    )
  ) return "wave";
  if (
    /(谢谢|多谢|没错|当然|好的|好呀|可以|答应你|没问题|我同意|说得对|确实|明白|知道了|交给我|放心)/.test(
      clean,
    )
  ) return "nod";
  if (
    /(不行|不是|不要|不能|并不是|别这样|不可以|办不到|我拒绝|我不同意|别想|休想|没门)/.test(
      clean,
    )
  ) return "shake";
  if (
    /(让我想想|我想想|想一想|我觉得|或许|可能|思考一下|分析一下|考虑一下|得想想|需要分析)/.test(
      clean,
    )
  ) return "think";
  if (
    /(生气|烦人|别烦|滚开|滚吧|去你的|去死|讨厌|闭嘴|混蛋|可恶|气死|挑衅|惹我|不耐烦)/.test(
      clean,
    )
  ) return "angry";
  if (/(难过|伤心|难受|失落|沮丧|想哭|委屈)/.test(clean)) return "shy";
  if (/(居然|竟然|真的吗|没想到|天哪|吓一跳|太意外)/.test(clean)) {
    return "surprised";
  }
  if (/(哈哈|开心|太好了|真棒|太棒|喜欢|高兴)/.test(clean)) {
    return "celebrate";
  }
  const emotional = emotionGestureMap[loweredEmotion];
  if (emotional) return emotional;
  const settings = state.settings || {};
  const styleFactor = settings.action_style === "subtle" ? 0.72 : settings.action_style === "expressive" ? 1.18 : 1;
  const frequency = THREE.MathUtils.clamp(
    (Number(settings.gesture_frequency) || 0) * styleFactor,
    0,
    1,
  );
  if (clean.length >= 18 && hashRatio(clean) <= frequency) return "emphasize";
  return null;
}

function playVrmaGesture(name, forceOnce = false) {
  const group = state.vrmaActions.get(name);
  const mocap = state.mocapActions.get(name);
  const builtin = state.builtinActions.get(name);
  if ((!group?.length && !mocap && !builtin) || !state.mixer) return false;
  const selected = group?.length
    ? group[Math.abs(Math.floor(state.elapsed * 10)) % group.length]
    : mocap || builtin;
  const { action, profile } = selected;
  const selectedSource = group?.length ? "vrma" : mocap ? "mocap" : "builtin";
  const previous = state.activeVrmaAction;
  if (previous === action && name === "idle" && action.isRunning()) return true;
  action.reset();
  action.enabled = true;
  action.clampWhenFinished = forceOnce || !profile.loop;
  action.zeroSlopeAtStart = true;
  action.zeroSlopeAtEnd = true;
  action.setEffectiveTimeScale(1);
  const clipDuration = Math.max(0.001, action.getClip().duration);
  const playbackDuration =
    selectedSource === "mocap" && name !== "idle"
      ? Math.max(clipDuration, gestureDurations[name] || clipDuration)
      : clipDuration;
  if (playbackDuration > clipDuration) action.setDuration(playbackDuration);
  action.setLoop(
    profile.loop && !forceOnce ? THREE.LoopRepeat : THREE.LoopOnce,
    profile.loop && !forceOnce ? Infinity : 1,
  );
  if (previous && previous !== action) {
    action.play().crossFadeFrom(previous, name === "idle" ? 0.34 : 0.24, false);
  } else {
    action.fadeIn(0.24).play();
  }
  state.activeVrmaAction = action;
  state.motionSource = profile.source || selectedSource;
  state.gesture = name;
  state.gestureStartedAt = performance.now();
  state.gestureDuration = Math.max(0.5, playbackDuration);
  if (name !== "idle") {
    document.documentElement.dataset.companionLastActionStartedAt = String(
      Math.round(state.gestureStartedAt),
    );
    document.documentElement.dataset.companionLastAction = name;
    document.documentElement.dataset.companionLastActionSource = state.motionSource;
    document.documentElement.dataset.companionLastActionDuration =
      state.gestureDuration.toFixed(3);
  }
  emitGesture(name, state.motionSource);
  return true;
}

function playNextComboStep() {
  const step = state.comboQueue.shift();
  if (!step) return false;
  return playVrmaGesture(step, true);
}

function startMotionCombo(combo) {
  if (!combo?.enabled || !Array.isArray(combo.steps) || combo.steps.length < 2) {
    return false;
  }
  state.activeCombo = combo;
  state.comboQueue = combo.steps.slice(0, 5);
  document.documentElement.dataset.companionMotionCombo = combo.name || combo.id;
  return playNextComboStep();
}

function triggerGesture(name, sourceText = "", force = false) {
  if (!gestureDurations[name] || !state.active) return false;
  const now = performance.now();
  if (now < state.gestureCooldownUntil) return false;
  if (state.lastGesture === name && now - state.gestureStartedAt < 5000) return false;
  const settings = state.settings || {};
  const frequency = THREE.MathUtils.clamp(Number(settings.gesture_frequency) || 0, 0, 1);
  if (!force && frequency <= 0) return false;
  const strongGesture = ["wave", "nod", "shake", "celebrate", "surprised"].includes(name);
  if (!force && !strongGesture && hashRatio(`${sourceText}:${name}`) > frequency) return false;
  const automaticEmotion = {
    wave: "happy",
    celebrate: "happy",
    shy: "relaxed",
    surprised: "surprised",
  }[name];
  if (automaticEmotion && now >= state.emotionUntil) {
    setEmotion(automaticEmotion, (gestureDurations[name] || 2) * 1000);
  }
  state.lastGesture = name;
  state.gestureCooldownUntil = now + 1250;
  const combo = state.motionCombos.find(
    (item) => item.enabled !== false && item.trigger === name,
  );
  if (combo && startMotionCombo(combo)) return true;
  if (playVrmaGesture(name)) return true;
  return false;
}

function updateExpressions(now) {
  const manager = state.vrm?.expressionManager;
  if (!manager) return;
  const phase = (now / 1000) % 4.6;
  const blink = phase < 0.13 ? Math.sin((phase / 0.13) * Math.PI) : 0;
  manager.setValue("blink", blink);
  const activeEmotion = now < state.emotionUntil ? state.emotion : "neutral";
  for (const emotion of ["happy", "sad", "angry", "surprised", "relaxed"]) {
    manager.setValue(emotion, emotion === activeEmotion ? 0.72 : 0);
  }
  if (state.lipEngine) {
    const weights = state.lipEngine.weights;
    for (const viseme of VISEME_NAMES) manager.setValue(viseme, weights[viseme] || 0);
  }
}

const ikTarget = new THREE.Vector3();
const ikHead = new THREE.Vector3();
const ikEffector = new THREE.Vector3();
const ikLinkPosition = new THREE.Vector3();
const ikEffectorDirection = new THREE.Vector3();
const ikTargetDirection = new THREE.Vector3();
const ikCameraDirection = new THREE.Vector3();
const ikCameraRight = new THREE.Vector3();
const ikWorldQuaternion = new THREE.Quaternion();
const ikParentQuaternion = new THREE.Quaternion();
const ikDeltaQuaternion = new THREE.Quaternion();
const ikDesiredWorldQuaternion = new THREE.Quaternion();
const ikDesiredLocalQuaternion = new THREE.Quaternion();
const ikIdentityQuaternion = new THREE.Quaternion();
const ikFingerUp = new THREE.Vector3(0, 1, 0);
const ikFingerDirection = new THREE.Vector3();
const ikThumbDirection = new THREE.Vector3();
const ikPalmNormal = new THREE.Vector3();
const ikNormalAfterFinger = new THREE.Vector3();
const ikDesiredPalmNormal = new THREE.Vector3();
const ikCross = new THREE.Vector3();
const ikMiddlePosition = new THREE.Vector3();
const ikThumbPosition = new THREE.Vector3();
const ikFingerCorrection = new THREE.Quaternion();
const ikPalmCorrection = new THREE.Quaternion();

const collisionHips = new THREE.Vector3();
const collisionChest = new THREE.Vector3();
const collisionNeck = new THREE.Vector3();
const collisionHead = new THREE.Vector3();
const collisionLeftShoulder = new THREE.Vector3();
const collisionRightShoulder = new THREE.Vector3();
const collisionTorsoCenter = new THREE.Vector3();
const collisionRight = new THREE.Vector3();
const collisionUp = new THREE.Vector3();
const collisionForward = new THREE.Vector3();
const collisionCameraForward = new THREE.Vector3();
const collisionRelative = new THREE.Vector3();
const collisionHandPosition = new THREE.Vector3();
const collisionElbowPosition = new THREE.Vector3();
const collisionForearmPosition = new THREE.Vector3();
const collisionPalmPosition = new THREE.Vector3();
const collisionFingerPosition = new THREE.Vector3();
const collisionTarget = new THREE.Vector3();
const collisionElbowTarget = new THREE.Vector3();
const collisionHeadCenter = new THREE.Vector3();
const collisionHeadOffset = new THREE.Vector3();
const idleUpperLegPosition = new THREE.Vector3();
const idleKneePosition = new THREE.Vector3();
const idleClosestThighPoint = new THREE.Vector3();
const idleThighSegment = new THREE.Vector3();
const idleOutward = new THREE.Vector3();
const idleClearanceTarget = new THREE.Vector3();
// VRM defines relaxed shoulders as the neutral baseline. The 15° elbow bend
// and 8° wrist droop follow 3D-Avatar-Chatbot's documented naturalIdle chain;
// shoulder roll is reduced and sign-calibrated for normalized VRoid bones.
const idleArmRestQuaternions = new Map(
  [
    ["leftShoulder", [0, 0, THREE.MathUtils.degToRad(-3.5)]],
    ["rightShoulder", [0, 0, THREE.MathUtils.degToRad(3.5)]],
    ["leftLowerArm", [0, THREE.MathUtils.degToRad(-15), 0]],
    ["rightLowerArm", [0, THREE.MathUtils.degToRad(15), 0]],
    ["leftHand", [0, 0, THREE.MathUtils.degToRad(8)]],
    ["rightHand", [0, 0, THREE.MathUtils.degToRad(-8)]],
  ].map(([boneName, euler]) => [
    boneName,
    new THREE.Quaternion().setFromEuler(new THREE.Euler(...euler, "XYZ")),
  ]),
);

function solveIkLink(link, effector, target, blend) {
  link.updateWorldMatrix(true, true);
  effector.getWorldPosition(ikEffector);
  link.getWorldPosition(ikLinkPosition);
  ikEffectorDirection.copy(ikEffector).sub(ikLinkPosition).normalize();
  ikTargetDirection.copy(target).sub(ikLinkPosition).normalize();
  if (!Number.isFinite(ikEffectorDirection.x) || !Number.isFinite(ikTargetDirection.x)) return;
  ikDeltaQuaternion.setFromUnitVectors(ikEffectorDirection, ikTargetDirection);
  const angle = 2 * Math.acos(THREE.MathUtils.clamp(ikDeltaQuaternion.w, -1, 1));
  if (angle > 0.45) {
    ikDeltaQuaternion.slerpQuaternions(
      ikIdentityQuaternion,
      ikDeltaQuaternion,
      0.45 / angle,
    );
  }
  link.getWorldQuaternion(ikWorldQuaternion);
  ikWorldQuaternion.premultiply(ikDeltaQuaternion);
  link.parent.getWorldQuaternion(ikParentQuaternion).invert();
  ikDesiredLocalQuaternion.copy(ikParentQuaternion).multiply(ikWorldQuaternion);
  link.quaternion.slerp(ikDesiredLocalQuaternion, blend);
  link.updateWorldMatrix(true, true);
}

function applyWaveIk() {
  if (
    state.gesture !== "wave" ||
    state.motionSource !== "builtin" ||
    !state.activeVrmaAction ||
    !state.camera
  ) {
    return;
  }
  const humanoid = state.vrm?.humanoid;
  const upperArm = humanoid?.getNormalizedBoneNode("rightUpperArm");
  const lowerArm = humanoid?.getNormalizedBoneNode("rightLowerArm");
  const hand = humanoid?.getNormalizedBoneNode("rightHand");
  const middle = humanoid?.getNormalizedBoneNode("rightMiddleProximal");
  const thumb =
    humanoid?.getNormalizedBoneNode("rightThumbMetacarpal") ||
    humanoid?.getNormalizedBoneNode("rightThumbProximal");
  const head = humanoid?.getNormalizedBoneNode("head");
  if (!upperArm || !lowerArm || !hand || !head) return;

  const duration = Math.max(0.001, state.activeVrmaAction.getClip().duration);
  const progress = THREE.MathUtils.clamp(state.activeVrmaAction.time / duration, 0, 1);
  const rise = THREE.MathUtils.smoothstep(progress, 0.12, 0.3);
  const fall = 1 - THREE.MathUtils.smoothstep(progress, 0.76, 0.96);
  const blend = rise * fall;
  if (blend <= 0.001) return;

  state.vrm.scene.updateMatrixWorld(true);
  hand.getWorldQuaternion(ikDesiredWorldQuaternion);
  hand.getWorldPosition(ikEffector);
  head.getWorldPosition(ikHead);
  state.camera.getWorldDirection(ikCameraDirection).multiplyScalar(-1).normalize();
  ikCameraRight.set(1, 0, 0).applyQuaternion(state.camera.quaternion).normalize();
  if (middle && thumb) {
    middle.getWorldPosition(ikMiddlePosition);
    thumb.getWorldPosition(ikThumbPosition);
    ikFingerDirection.copy(ikMiddlePosition).sub(ikEffector).normalize();
    ikThumbDirection.copy(ikThumbPosition).sub(ikEffector).normalize();
    ikPalmNormal.crossVectors(ikThumbDirection, ikFingerDirection).normalize();
    if (ikPalmNormal.dot(ikCameraDirection) < 0) ikPalmNormal.negate();
    ikFingerCorrection.setFromUnitVectors(ikFingerDirection, ikFingerUp);
    ikNormalAfterFinger.copy(ikPalmNormal).applyQuaternion(ikFingerCorrection);
    ikNormalAfterFinger.addScaledVector(
      ikFingerUp,
      -ikNormalAfterFinger.dot(ikFingerUp),
    ).normalize();
    ikDesiredPalmNormal.copy(ikCameraDirection);
    ikDesiredPalmNormal.addScaledVector(
      ikFingerUp,
      -ikDesiredPalmNormal.dot(ikFingerUp),
    ).normalize();
    const palmAngle = Math.atan2(
      ikFingerUp.dot(
        ikCross.crossVectors(ikNormalAfterFinger, ikDesiredPalmNormal),
      ),
      THREE.MathUtils.clamp(
        ikNormalAfterFinger.dot(ikDesiredPalmNormal),
        -1,
        1,
      ),
    );
    ikPalmCorrection.setFromAxisAngle(ikFingerUp, palmAngle);
    ikDesiredWorldQuaternion
      .premultiply(ikFingerCorrection)
      .premultiply(ikPalmCorrection);
  }
  const waveOffset = Math.sin(progress * Math.PI * 6) * 0.035;
  ikTarget
    .copy(ikEffector)
    .addScaledVector(ikCameraRight, waveOffset)
    .addScaledVector(ikCameraDirection, 0.08);

  for (let iteration = 0; iteration < 6; iteration += 1) {
    solveIkLink(lowerArm, hand, ikTarget, 0.76 * blend);
    solveIkLink(upperArm, hand, ikTarget, 0.66 * blend);
  }

  hand.parent.updateWorldMatrix(true, false);
  hand.parent.getWorldQuaternion(ikParentQuaternion).invert();
  ikDesiredLocalQuaternion
    .copy(ikParentQuaternion)
    .multiply(ikDesiredWorldQuaternion);
  hand.quaternion.slerp(ikDesiredLocalQuaternion, blend);
  hand.updateWorldMatrix(true, true);
  hand.getWorldPosition(ikEffector);
  document.documentElement.dataset.companionWaveReach = ikEffector
    .distanceTo(ikTarget)
    .toFixed(3);
  document.documentElement.dataset.companionWaveDepth = ikEffector
    .sub(ikHead)
    .dot(ikCameraDirection)
    .toFixed(3);
}

function applyIdleArmRestPose() {
  if (state.gesture !== "idle" || state.motionSource !== "bundled-vrma") return;
  const humanoid = state.vrm?.humanoid;
  for (const [boneName, target] of idleArmRestQuaternions) {
    const bone = humanoid?.getNormalizedBoneNode(boneName);
    if (!bone) continue;
    bone.quaternion.slerp(target, 0.86);
  }
}

function applyIdleHandClearance() {
  if (
    state.gesture !== "idle" ||
    state.motionSource !== "bundled-vrma" ||
    !state.camera
  ) {
    document.documentElement.dataset.companionIdleHandCorrections = "0";
    return;
  }
  const humanoid = state.vrm?.humanoid;
  const hips = humanoid?.getNormalizedBoneNode("hips");
  const leftShoulder =
    humanoid?.getNormalizedBoneNode("leftUpperArm") ||
    humanoid?.getNormalizedBoneNode("leftShoulder");
  const rightShoulder =
    humanoid?.getNormalizedBoneNode("rightUpperArm") ||
    humanoid?.getNormalizedBoneNode("rightShoulder");
  const leftUpperLeg = humanoid?.getNormalizedBoneNode("leftUpperLeg");
  const rightUpperLeg = humanoid?.getNormalizedBoneNode("rightUpperLeg");
  if (!hips || !leftShoulder || !rightShoulder || !leftUpperLeg || !rightUpperLeg) {
    return;
  }
  state.vrm.scene.updateMatrixWorld(true);
  hips.getWorldPosition(collisionHips);
  leftShoulder.getWorldPosition(collisionLeftShoulder);
  rightShoulder.getWorldPosition(collisionRightShoulder);
  leftUpperLeg.getWorldPosition(collisionPalmPosition);
  rightUpperLeg.getWorldPosition(collisionFingerPosition);
  collisionRight.copy(collisionRightShoulder).sub(collisionLeftShoulder).normalize();
  state.camera.getWorldDirection(collisionCameraForward).multiplyScalar(-1).normalize();
  const shoulderWidth = collisionLeftShoulder.distanceTo(collisionRightShoulder);
  const hipWidth = collisionPalmPosition.distanceTo(collisionFingerPosition);
  const minimumSideDistance = hipWidth * 0.5 + shoulderWidth * 0.14;
  const minimumFrontDistance = shoulderWidth * 0.065;
  let corrections = 0;
  const clearanceSamples = [];

  for (const side of ["left", "right"]) {
    const upperArm = humanoid.getNormalizedBoneNode(`${side}UpperArm`);
    const lowerArm = humanoid.getNormalizedBoneNode(`${side}LowerArm`);
    const hand = humanoid.getNormalizedBoneNode(`${side}Hand`);
    const upperLeg = humanoid.getNormalizedBoneNode(`${side}UpperLeg`);
    const lowerLeg = humanoid.getNormalizedBoneNode(`${side}LowerLeg`);
    if (!upperArm || !lowerArm || !hand || !upperLeg || !lowerLeg) continue;
    hand.getWorldPosition(collisionHandPosition);
    hand.getWorldQuaternion(ikDesiredWorldQuaternion);
    upperLeg.getWorldPosition(idleUpperLegPosition);
    lowerLeg.getWorldPosition(idleKneePosition);
    idleThighSegment.copy(idleKneePosition).sub(idleUpperLegPosition);
    const segmentLengthSquared = idleThighSegment.lengthSq();
    const segmentT = segmentLengthSquared
      ? THREE.MathUtils.clamp(
          collisionRelative
            .copy(collisionHandPosition)
            .sub(idleUpperLegPosition)
            .dot(idleThighSegment) / segmentLengthSquared,
          0,
          1,
        )
      : 0;
    idleClosestThighPoint
      .copy(idleUpperLegPosition)
      .addScaledVector(idleThighSegment, segmentT);
    idleOutward
      .copy(collisionRight)
      .multiplyScalar(side === "left" ? -1 : 1);
    const sideDistance = collisionRelative
      .copy(collisionHandPosition)
      .sub(collisionHips)
      .dot(idleOutward);
    const frontDistance = collisionRelative
      .copy(collisionHandPosition)
      .sub(idleClosestThighPoint)
      .dot(collisionCameraForward);
    clearanceSamples.push(
      `${side}:${sideDistance.toFixed(3)}/${frontDistance.toFixed(3)}`,
    );
    const sidePush = Math.max(0, minimumSideDistance - sideDistance);
    const frontPush = Math.max(0, minimumFrontDistance - frontDistance);
    if (sidePush <= 0.001 && frontPush <= 0.001) continue;
    idleClearanceTarget
      .copy(collisionHandPosition)
      .addScaledVector(idleOutward, sidePush + shoulderWidth * 0.012)
      .addScaledVector(collisionCameraForward, frontPush + shoulderWidth * 0.008);
    for (let iteration = 0; iteration < 4; iteration += 1) {
      solveIkLink(lowerArm, hand, idleClearanceTarget, 0.46);
      solveIkLink(upperArm, hand, idleClearanceTarget, 0.34);
    }
    hand.parent.updateWorldMatrix(true, false);
    hand.parent.getWorldQuaternion(ikParentQuaternion).invert();
    ikDesiredLocalQuaternion
      .copy(ikParentQuaternion)
      .multiply(ikDesiredWorldQuaternion);
    hand.quaternion.slerp(ikDesiredLocalQuaternion, 0.74);
    corrections += 1;
  }
  document.documentElement.dataset.companionIdleHandCorrections = String(
    corrections,
  );
  document.documentElement.dataset.companionIdleHandClearance = clearanceSamples.join(
    ",",
  );
  document.documentElement.dataset.companionIdleHandMinimum = `${minimumSideDistance.toFixed(
    3,
  )}/${minimumFrontDistance.toFixed(3)}`;
}

function torsoPenetration(point, halfWidth, halfHeight, frontDepth) {
  collisionRelative.copy(point).sub(collisionTorsoCenter);
  const x = collisionRelative.dot(collisionRight);
  const y = collisionRelative.dot(collisionUp);
  const z = collisionRelative.dot(collisionForward);
  const verticalRatio = Math.abs(y) / Math.max(halfHeight, 0.001);
  if (verticalRatio >= 1) return 0;
  const taperedWidth = halfWidth * (0.82 + 0.18 * (1 - verticalRatio));
  if (Math.abs(x) >= taperedWidth || z <= -frontDepth * 0.75 || z >= frontDepth) {
    return 0;
  }
  return frontDepth - z;
}

function headPenetration(point, radius) {
  collisionHeadOffset.copy(point).sub(collisionHeadCenter);
  const distance = collisionHeadOffset.length();
  if (distance >= radius) return 0;
  return radius - distance;
}

function applySelfCollisionAvoidance() {
  if (
    state.gesture === "idle" ||
    !["mocap", "vrma"].includes(state.motionSource) ||
    !state.camera
  ) {
    document.documentElement.dataset.companionCollisionCorrections = "0";
    document.documentElement.dataset.companionCollisionProbe = "0.000";
    return;
  }
  const humanoid = state.vrm?.humanoid;
  const hips = humanoid?.getNormalizedBoneNode("hips");
  const chest =
    humanoid?.getNormalizedBoneNode("upperChest") ||
    humanoid?.getNormalizedBoneNode("chest");
  const neck = humanoid?.getNormalizedBoneNode("neck");
  const head = humanoid?.getNormalizedBoneNode("head");
  const leftShoulder =
    humanoid?.getNormalizedBoneNode("leftUpperArm") ||
    humanoid?.getNormalizedBoneNode("leftShoulder");
  const rightShoulder =
    humanoid?.getNormalizedBoneNode("rightUpperArm") ||
    humanoid?.getNormalizedBoneNode("rightShoulder");
  if (!hips || !chest || !neck || !head || !leftShoulder || !rightShoulder) return;

  state.vrm.scene.updateMatrixWorld(true);
  hips.getWorldPosition(collisionHips);
  chest.getWorldPosition(collisionChest);
  neck.getWorldPosition(collisionNeck);
  head.getWorldPosition(collisionHead);
  leftShoulder.getWorldPosition(collisionLeftShoulder);
  rightShoulder.getWorldPosition(collisionRightShoulder);
  collisionTorsoCenter
    .copy(collisionHips)
    .lerp(collisionNeck, 0.53)
    .lerp(collisionChest, 0.16);
  collisionRight.copy(collisionRightShoulder).sub(collisionLeftShoulder).normalize();
  collisionUp.copy(collisionNeck).sub(collisionHips).normalize();
  collisionForward.crossVectors(collisionRight, collisionUp).normalize();
  state.camera.getWorldDirection(collisionCameraForward).multiplyScalar(-1).normalize();
  if (collisionForward.dot(collisionCameraForward) < 0) collisionForward.negate();

  const shoulderWidth = collisionLeftShoulder.distanceTo(collisionRightShoulder);
  const torsoHeight = collisionHips.distanceTo(collisionNeck);
  const halfWidth = shoulderWidth * 0.5;
  const halfHeight = torsoHeight * 0.56;
  const frontDepth = shoulderWidth * 0.34;
  const forearmRadius = shoulderWidth * 0.085;
  const handRadius = shoulderWidth * 0.11;
  const headNeckDistance = collisionHead.distanceTo(collisionNeck);
  collisionHeadCenter
    .copy(collisionHead)
    .addScaledVector(collisionUp, headNeckDistance * 0.42);
  // Hair and sleeves extend well beyond the humanoid head/hand bones, so the
  // safety radius deliberately includes a small visual-geometry margin.
  const headRadius = Math.max(shoulderWidth * 0.53, headNeckDistance * 1.08);
  let corrections = 0;
  let strongestPenetration = 0;

  for (const side of ["left", "right"]) {
    const upperArm = humanoid.getNormalizedBoneNode(`${side}UpperArm`);
    const lowerArm = humanoid.getNormalizedBoneNode(`${side}LowerArm`);
    const hand = humanoid.getNormalizedBoneNode(`${side}Hand`);
    const middle =
      humanoid.getNormalizedBoneNode(`${side}MiddleDistal`) ||
      humanoid.getNormalizedBoneNode(`${side}MiddleProximal`);
    if (!upperArm || !lowerArm || !hand) continue;
    hand.getWorldQuaternion(ikDesiredWorldQuaternion);
    hand.getWorldPosition(collisionHandPosition);
    lowerArm.getWorldPosition(collisionElbowPosition);
    collisionForearmPosition
      .copy(collisionElbowPosition)
      .lerp(collisionHandPosition, 0.52);
    if (middle) middle.getWorldPosition(collisionFingerPosition);
    else collisionFingerPosition.copy(collisionHandPosition);
    collisionPalmPosition
      .copy(collisionHandPosition)
      .lerp(collisionFingerPosition, 0.48);

    const handTorsoPush = torsoPenetration(
      collisionHandPosition,
      halfWidth + shoulderWidth * 0.12,
      halfHeight,
      frontDepth * 1.12 + handRadius,
    );
    const forearmTorsoPush = torsoPenetration(
      collisionForearmPosition,
      halfWidth * 0.94,
      halfHeight,
      frontDepth * 0.92 + forearmRadius,
    );
    const palmTorsoPush = torsoPenetration(
      collisionPalmPosition,
      halfWidth + shoulderWidth * 0.16,
      halfHeight,
      frontDepth * 1.16 + handRadius,
    );
    const fingerTorsoPush = torsoPenetration(
      collisionFingerPosition,
      halfWidth + shoulderWidth * 0.18,
      halfHeight,
      frontDepth * 1.12 + handRadius * 0.72,
    );
    const elbowTorsoPush = torsoPenetration(
      collisionElbowPosition,
      halfWidth * 0.84,
      halfHeight * 0.92,
      frontDepth * 0.82 + forearmRadius,
    );
    const handHeadPush = headPenetration(
      collisionHandPosition,
      headRadius + handRadius,
    );
    const palmHeadPush = headPenetration(
      collisionPalmPosition,
      headRadius + handRadius * 0.9,
    );
    const fingerHeadPush = headPenetration(
      collisionFingerPosition,
      headRadius + handRadius * 0.55,
    );
    const forearmHeadPush = headPenetration(
      collisionForearmPosition,
      headRadius * 0.9 + forearmRadius,
    );
    const handPush = Math.min(
      shoulderWidth * 0.58,
      Math.max(
        handTorsoPush,
        forearmTorsoPush,
        palmTorsoPush,
        fingerTorsoPush,
        handHeadPush,
        palmHeadPush,
        fingerHeadPush,
        forearmHeadPush,
      ),
    );
    strongestPenetration = Math.max(
      strongestPenetration,
      handPush,
      elbowTorsoPush,
    );

    if (elbowTorsoPush > shoulderWidth * 0.025) {
      collisionElbowTarget
        .copy(collisionElbowPosition)
        .addScaledVector(
          collisionForward,
          Math.min(elbowTorsoPush, shoulderWidth * 0.42),
        );
      for (let iteration = 0; iteration < 3; iteration += 1) {
        solveIkLink(upperArm, lowerArm, collisionElbowTarget, 0.34);
      }
      corrections += 1;
    }
    if (handPush <= shoulderWidth * 0.018) continue;
    collisionTarget
      .copy(collisionHandPosition)
      .addScaledVector(collisionForward, handPush + shoulderWidth * 0.025);
    for (let iteration = 0; iteration < 4; iteration += 1) {
      solveIkLink(lowerArm, hand, collisionTarget, 0.46);
      solveIkLink(upperArm, hand, collisionTarget, 0.34);
    }
    hand.parent.updateWorldMatrix(true, false);
    hand.parent.getWorldQuaternion(ikParentQuaternion).invert();
    ikDesiredLocalQuaternion
      .copy(ikParentQuaternion)
      .multiply(ikDesiredWorldQuaternion);
    hand.quaternion.slerp(ikDesiredLocalQuaternion, 0.72);
    corrections += 1;
  }
  document.documentElement.dataset.companionCollisionCorrections = String(
    corrections,
  );
  document.documentElement.dataset.companionCollisionProbe = strongestPenetration.toFixed(3);
}

function updateGaze(delta) {
  if (!state.lookAtTarget || state.settings?.gaze_enabled === false) return;
  const response = 1 - Math.exp(-delta * 4.5);
  state.gaze.x += (state.gaze.targetX - state.gaze.x) * response;
  state.gaze.y += (state.gaze.targetY - state.gaze.y) * response;
  state.lookAtTarget.position.set(
    state.lookAtBase.x + state.gaze.x * 0.55,
    state.lookAtBase.y - state.gaze.y * 0.32,
    state.lookAtBase.z,
  );
}

function renderFrame(now) {
  requestAnimationFrame(renderFrame);
  const delta = Math.min(Math.max(0, (now - state.lastFrame) / 1000), 0.05);
  state.lastFrame = now;
  state.elapsed += delta;
  if (!state.active || !state.vrm) return;
  state.mixer?.update(delta);
  applyWaveIk();
  applyIdleArmRestPose();
  applyIdleHandClearance();
  applySelfCollisionAvoidance();
  updateGaze(delta);
  updateExpressions(now);
  state.vrm.update(delta);
  state.renderer.render(state.scene, state.camera);
}

async function attachSpeechMedia(media) {
  if (!state.active || !state.vrm || !(media instanceof HTMLMediaElement)) return;
  try {
    if (!state.audioContext) {
      state.audioContext = new AudioContext();
      state.lipEngine = await WLipSyncEngine.create(state.audioContext, {
        gain: 1.22,
        smoothness: 0.045,
      });
    }
    await state.audioContext.resume();
    if (state.audioNodes.has(media)) return;
    const node = state.audioContext.createMediaElementSource(media);
    node.connect(state.lipEngine.input);
    node.connect(state.audioContext.destination);
    state.audioNodes.set(media, node);
    media.addEventListener(
      "ended",
      () => {
        try {
          node.disconnect();
        } catch (_) {}
        for (const viseme of VISEME_NAMES) {
          state.vrm?.expressionManager?.setValue(viseme, 0);
        }
      },
      { once: true },
    );
  } catch (error) {
    state.lastError = String(error?.message || error);
    console.warn("[Companion VRM] lip sync fallback", error);
  }
}

observeWebSockets();
window.CompanionAudioBridge?.registerBeforePlay(attachSpeechMedia);
window.CompanionVRM = {
  state,
  selectCharacter,
  setEmotion,
  triggerGesture,
  chooseSemanticGesture,
  fitCamera,
  useLive2D() {
    applyRendererVisibility(false);
  },
};
