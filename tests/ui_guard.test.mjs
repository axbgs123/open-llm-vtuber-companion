import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

test("long VAD diagnostic is shortened and constrained", () => {
  const classes = new Set();
  const parentElement = { classList: { add: (name) => classes.add(name) } };
  const textNode = {
    nodeType: 3,
    nodeValue:
      "检测到语音但过于简短，请尝试提高音量或说得更久一些，或调整识别设置",
    parentElement,
  };
  const appended = [];
  const document = {
    body: {},
    head: { appendChild: (node) => appended.push(node) },
    createElement: () => ({ textContent: "" }),
    createTreeWalker: () => {
      let used = false;
      return {
        nextNode() {
          if (used) return null;
          used = true;
          return textNode;
        },
      };
    },
    addEventListener() {},
  };
  class MutationObserver {
    observe() {}
  }

  vm.runInNewContext(
    fs.readFileSync("companion_assets/ui_guard.js", "utf8"),
    {
      document,
      MutationObserver,
      NodeFilter: { SHOW_TEXT: 4 },
      Node: { TEXT_NODE: 3, ELEMENT_NODE: 1 },
      Map,
      String,
    },
  );

  assert.equal(
    textNode.nodeValue,
    "录音太短。请提高音量或说得更久一些，也可在设置中降低语音检测阈值。",
  );
  assert.ok(classes.has("companion-readable-diagnostic"));
  assert.match(appended[0].textContent, /max-width: min\(920px, 100%\)/);
});
