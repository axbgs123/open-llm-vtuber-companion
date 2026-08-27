/* Keep long microphone diagnostics readable on narrow companion screens. */
(() => {
  "use strict";

  const replacements = new Map([
    [
      "检测到语音但过于简短",
      "录音太短。请提高音量或说得更久一些，也可在设置中降低语音检测阈值。",
    ],
    [
      "Voice detected but too brief",
      "Recording was too short. Speak louder or longer, or lower the speech threshold in Settings.",
    ],
  ]);
  const className = "companion-readable-diagnostic";
  const style = document.createElement("style");
  style.textContent = `
    .${className} {
      box-sizing: border-box !important;
      display: -webkit-box !important;
      width: 100% !important;
      max-width: min(920px, 100%) !important;
      max-height: 4.2em !important;
      margin-inline: auto !important;
      padding: 10px 16px !important;
      overflow: hidden !important;
      white-space: normal !important;
      overflow-wrap: anywhere !important;
      word-break: break-word !important;
      font-size: clamp(15px, 2.35vw, 24px) !important;
      line-height: 1.35 !important;
      text-align: center !important;
      -webkit-box-orient: vertical !important;
      -webkit-line-clamp: 3 !important;
    }
  `;
  document.head.appendChild(style);

  function markDiagnostics(root = document.body) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const text = String(node.nodeValue || "").trim();
      const match = [...replacements.entries()].find(([marker]) => text.includes(marker));
      if (!match) continue;
      const element = node.parentElement;
      if (element) element.classList.add(className);
      node.nodeValue = match[1];
    }
  }

  const observer = new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType === Node.TEXT_NODE) {
          markDiagnostics(node.parentElement);
        } else if (node.nodeType === Node.ELEMENT_NODE) {
          markDiagnostics(node);
        }
      }
      if (record.type === "characterData") markDiagnostics(record.target.parentElement);
    }
  });

  const start = () => {
    markDiagnostics();
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  };
  if (document.body) start();
  else document.addEventListener("DOMContentLoaded", start, { once: true });
})();
