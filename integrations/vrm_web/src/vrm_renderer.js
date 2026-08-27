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
  activeVrmaAction: null,
  gaze: { x: 0, y: 0, targetX: 0, targetY: 0 },
  lookAtTarget: null,
  lookAtBase: new THREE.Vector3(0, 1.5, 1),
  host: null,
  lastError: "",
};

const expressionMap = {
  happy: "happy",
  joy: "happy",
  smile: "happy",
  sad: "sad",
  sorrow: "sad",
  angry: "angry",
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
  joy: "celebrate",
  excited: "celebrate",
  sad: "shy",
  sorrow: "shy",
  shy: "shy",
  angry: "emphasize",
  surprise: "surprised",
  surprised: "surprised",
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
  idle: 0,
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
  if (message?.type === "audio") {
    const expressions = message.actions?.expressions || [];
    const requested = expressions.find((item) => typeof item === "string");
    if (requested) setEmotion(requested, 5000);
    const text = String(message.display_text?.text || "");
    const gesture = chooseSemanticGesture(text, requested || "neutral");
    if (gesture) triggerGesture(gesture, text);
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
  state.activeVrmaAction = null;
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
    state.activeVrmaAction?.fadeOut(0.2);
    state.activeVrmaAction = null;
    state.gesture = "idle";
    emitGesture("idle", "vrma");
    playVrmaGesture("idle");
  });
  vrm.scene.rotation.y = Math.PI;
  state.scene.add(vrm.scene);
  state.elapsed = 0;
  updateProceduralPose(0, performance.now());
  vrm.update(0);
  fitCamera();
  applyRendererVisibility(true);
  await loadAnimations(animations);
  emitStatus("ready", state.profile?.name || "VRM角色已就绪");
  const previewGesture = new URLSearchParams(window.location.search).get("gesture");
  if (gestureDurations[previewGesture]) {
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

async function selectCharacter(confUid) {
  state.confUid = confUid;
  try {
    const response = await fetch(`/api/companion/avatar/${encodeURIComponent(confUid)}`);
    if (!response.ok) throw new Error(`角色渲染设置读取失败：${response.status}`);
    const data = await response.json();
    state.settings = data.settings;
    state.profile = data.active;
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
  if (/(你好|嗨|哈喽|再见|拜拜|早上好|晚上好)/.test(clean)) return "wave";
  if (/(谢谢|多谢|没错|当然|好的|好呀|可以|答应你)/.test(clean)) return "nod";
  if (/(不行|不是|不要|不能|并不是|别这样)/.test(clean)) return "shake";
  if (/(让我想想|想一想|我觉得|或许|可能|思考一下)/.test(clean)) return "think";
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

function playVrmaGesture(name) {
  const group = state.vrmaActions.get(name);
  if (!group?.length || !state.mixer) return false;
  const index = Math.abs(Math.floor(state.elapsed * 10)) % group.length;
  const { action, profile } = group[index];
  if (state.activeVrmaAction && state.activeVrmaAction !== action) {
    state.activeVrmaAction.fadeOut(0.18);
  }
  action.reset();
  action.enabled = true;
  action.clampWhenFinished = !profile.loop;
  action.setLoop(profile.loop ? THREE.LoopRepeat : THREE.LoopOnce, profile.loop ? Infinity : 1);
  action.fadeIn(0.2).play();
  state.activeVrmaAction = action;
  state.gesture = name;
  state.gestureStartedAt = performance.now();
  state.gestureDuration = Math.max(0.5, action.getClip().duration);
  emitGesture(name, "vrma");
  return true;
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
  state.lastGesture = name;
  state.gestureCooldownUntil = now + 1250;
  if (playVrmaGesture(name)) return true;
  if (state.activeVrmaAction) {
    state.activeVrmaAction.fadeOut(0.18);
    state.activeVrmaAction = null;
  }
  state.gesture = name;
  state.gestureStartedAt = now;
  state.gestureDuration = gestureDurations[name];
  emitGesture(name, "procedural");
  return true;
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

function updateProceduralPose(elapsed, now) {
  if (!state.vrm) return;
  const head = state.vrm.humanoid?.getNormalizedBoneNode("head");
  const chest = state.vrm.humanoid?.getNormalizedBoneNode("chest");
  const hips = state.vrm.humanoid?.getNormalizedBoneNode("hips");
  const leftUpperArm = state.vrm.humanoid?.getNormalizedBoneNode("leftUpperArm");
  const rightUpperArm = state.vrm.humanoid?.getNormalizedBoneNode("rightUpperArm");
  const leftLowerArm = state.vrm.humanoid?.getNormalizedBoneNode("leftLowerArm");
  const rightLowerArm = state.vrm.humanoid?.getNormalizedBoneNode("rightLowerArm");
  const expressive = performance.now() < state.emotionUntil ? state.emotion : "neutral";
  const pulse = Math.sin(elapsed * 4.2);
  const armLift = expressive === "happy" ? 0.16 + pulse * 0.035 : 0;
  const guarded = expressive === "sad" ? 0.1 : 0;
  const settings = state.settings || {};
  const styleFactor = settings.action_style === "subtle" ? 0.72 : settings.action_style === "expressive" ? 1.2 : 1;
  const intensity = THREE.MathUtils.clamp(
    (Number(settings.gesture_intensity) || 0) * styleFactor,
    0,
    1.25,
  );
  let gesturePhase = 0;
  let gestureEnvelope = 0;
  if (state.gesture !== "idle" && state.gestureDuration > 0) {
    gesturePhase = (now - state.gestureStartedAt) / (state.gestureDuration * 1000);
    if (gesturePhase >= 1) {
      state.gesture = "idle";
      state.gestureDuration = 0;
      emitGesture("idle");
      gesturePhase = 0;
    } else {
      gestureEnvelope = Math.sin(Math.PI * THREE.MathUtils.clamp(gesturePhase, 0, 1));
    }
  }
  if (head) {
    head.rotation.y = Math.sin(elapsed * 0.37) * 0.035;
    head.rotation.z = Math.sin(elapsed * 0.23) * 0.018 + (expressive === "sad" ? 0.07 : 0);
    head.rotation.x = expressive === "sad" ? 0.12 : expressive === "surprised" ? -0.05 : 0;
  }
  if (chest) {
    chest.rotation.x = Math.sin(elapsed * 1.25) * 0.012 + (expressive === "angry" ? -0.045 : guarded);
  }
  if (hips) hips.position.y = Math.sin(elapsed * 1.25) * 0.004;
  if (leftUpperArm) {
    leftUpperArm.rotation.z = 1.22 - armLift + Math.sin(elapsed * 0.7) * 0.015;
    leftUpperArm.rotation.x = -0.08;
  }
  if (rightUpperArm) {
    rightUpperArm.rotation.z = -1.22 + armLift - Math.sin(elapsed * 0.7) * 0.015;
    rightUpperArm.rotation.x = -0.08;
  }
  if (leftLowerArm) leftLowerArm.rotation.z = 0.12;
  if (rightLowerArm) rightLowerArm.rotation.z = -0.12;

  const amount = gestureEnvelope * intensity;
  switch (state.gesture) {
    case "wave":
      if (rightUpperArm) {
        rightUpperArm.rotation.z += 1.28 * amount;
        rightUpperArm.rotation.x -= 0.22 * amount;
      }
      if (rightLowerArm) {
        rightLowerArm.rotation.z += 1.5 * amount;
        rightLowerArm.rotation.x = Math.sin(gesturePhase * Math.PI * 8) * 0.22 * amount;
      }
      break;
    case "nod":
      if (head) head.rotation.x += Math.sin(gesturePhase * Math.PI * 4) * 0.14 * amount;
      break;
    case "shake":
      if (head) head.rotation.y += Math.sin(gesturePhase * Math.PI * 4) * 0.18 * amount;
      break;
    case "think":
      if (head) {
        head.rotation.z += 0.11 * amount;
        head.rotation.y -= 0.1 * amount;
        head.rotation.x += 0.035 * amount;
      }
      if (rightUpperArm) rightUpperArm.rotation.z += 0.12 * amount;
      if (rightLowerArm) rightLowerArm.rotation.z += 0.1 * amount;
      if (chest) chest.rotation.y = -0.025 * amount;
      break;
    case "shy":
      if (head) head.rotation.x += 0.1 * amount;
      if (leftUpperArm) leftUpperArm.rotation.z -= 0.15 * amount;
      if (rightUpperArm) rightUpperArm.rotation.z += 0.15 * amount;
      if (chest) chest.rotation.x += 0.06 * amount;
      break;
    case "celebrate":
      if (leftUpperArm) leftUpperArm.rotation.z -= 0.62 * amount;
      if (rightUpperArm) rightUpperArm.rotation.z += 0.62 * amount;
      if (chest) chest.rotation.x -= 0.035 * amount;
      break;
    case "surprised":
      if (leftUpperArm) leftUpperArm.rotation.z -= 0.26 * amount;
      if (rightUpperArm) rightUpperArm.rotation.z += 0.26 * amount;
      if (head) head.rotation.x -= 0.06 * amount;
      break;
    case "emphasize": {
      const beat = (0.5 + 0.5 * Math.sin(gesturePhase * Math.PI * 4)) * amount;
      if (rightUpperArm) rightUpperArm.rotation.z += 0.42 * beat;
      if (rightLowerArm) rightLowerArm.rotation.z += 0.3 * beat;
      if (chest) chest.rotation.y = -0.035 * beat;
      break;
    }
  }
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
  if (!state.activeVrmaAction) updateProceduralPose(state.elapsed, now);
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
