import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
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
  if (message?.type === "audio" && message.actions) {
    const expressions = message.actions.expressions || [];
    const requested = expressions.find((item) => typeof item === "string");
    if (requested) setEmotion(requested, 5000);
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

  const camera = new THREE.PerspectiveCamera(28, 1, 0.01, 100);
  state.canvas = canvas;
  state.renderer = renderer;
  state.scene = scene;
  state.camera = camera;

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
}

async function loadModel(modelUrl) {
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
  vrm.scene.rotation.y = Math.PI;
  state.scene.add(vrm.scene);
  state.elapsed = 0;
  updateIdle(0);
  vrm.update(0);
  fitCamera();
  applyRendererVisibility(true);
  emitStatus("ready", state.profile?.name || "VRM角色已就绪");
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
    await loadModel(data.model_url);
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

function updateIdle(elapsed) {
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
}

function renderFrame(now) {
  requestAnimationFrame(renderFrame);
  const delta = Math.min(Math.max(0, (now - state.lastFrame) / 1000), 0.05);
  state.lastFrame = now;
  state.elapsed += delta;
  if (!state.active || !state.vrm) return;
  updateIdle(state.elapsed);
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
  fitCamera,
  useLive2D() {
    applyRendererVisibility(false);
  },
};
