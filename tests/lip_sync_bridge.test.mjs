import assert from "node:assert/strict";
import test from "node:test";

test("lip-sync PCM is ready before browser audio starts", async () => {
  let browserPlayCalls = 0;
  let resolveDecode;

  class FakeMediaElement {
    constructor() {
      this.src = "data:audio/wav;base64,UklGRg==";
      this.currentSrc = "";
    }

    play() {
      browserPlayCalls += 1;
      return Promise.resolve();
    }

    addEventListener() {}
  }

  const decodePromise = new Promise((resolve) => {
    resolveDecode = resolve;
  });
  const handler = {
    _sampleOffset: 9,
    _userTimeSeconds: 9,
    _lastRms: 9,
    loadWavFile() {
      return decodePromise;
    },
    start() {
      throw new Error("the old duplicate decoder should not run");
    },
    getRms() {
      return 0.1;
    },
  };

  globalThis.HTMLMediaElement = FakeMediaElement;
  globalThis.window = globalThis;
  globalThis.dispatchEvent = () => true;
  globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
      this.type = type;
      this.detail = options.detail;
    }
  };
  globalThis.document = { documentElement: { dataset: {} } };
  globalThis.getLAppAdapter = () => ({
    getModel: () => ({ _wavFileHandler: handler }),
  });

  await import("../companion_assets/lip_sync_bridge.js");
  const media = new FakeMediaElement();
  const playback = media.play();
  const reusedDecode = handler.start(media.src);

  assert.equal(browserPlayCalls, 0);
  assert.equal(reusedDecode, decodePromise);
  resolveDecode(true);
  await playback;

  assert.equal(browserPlayCalls, 1);
  assert.equal(handler._sampleOffset, 0);
  assert.equal(handler._userTimeSeconds, 0);
  assert.equal(globalThis.CompanionLipSync.status.prepared, 1);
});
