const INLINE_EMOTION_PATTERN = /[\[【](neutral|anger|angry|disgust|fear|joy|happy|happiness|smirk|sad|sadness|sorrow|surprise|surprised|excited|shy|relaxed)[\]】]/i;

const emotionAliases = {
  happiness: "happy",
  joy: "happy",
  excited: "happy",
  sadness: "sad",
  sorrow: "sad",
  anger: "angry",
  disgust: "angry",
  fear: "fear",
  surprise: "surprised",
};

const allowedGestures = new Set([
  "wave", "nod", "shake", "think", "shy", "emphasize", "celebrate",
  "surprised", "bow", "dance", "meditate", "angry", "confused",
  "listen", "cheer", "shiver", "tired",
]);

function hashRatio(text) {
  let hash = 2166136261;
  for (const char of String(text)) {
    hash ^= char.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

function choose(text, choices) {
  if (choices.length === 1) return choices[0];
  return choices[Math.min(choices.length - 1, Math.floor(hashRatio(text) * choices.length))];
}

function result(text, reason, emotion, intensity, gestures, sequence = null) {
  return {
    reason,
    emotion,
    intensity,
    gestures,
    gesture: choose(`${reason}:${text}`, gestures),
    sequence,
  };
}

export function normalizeEmotion(emotion) {
  const clean = String(emotion || "neutral").toLowerCase();
  return emotionAliases[clean] || clean;
}

export function extractInlineEmotion(text) {
  return String(text || "").match(INLINE_EMOTION_PATTERN)?.[1]?.toLowerCase() || "";
}

export function resolveAssistantMotion(text, actions = {}) {
  const supplied = actions?.motion;
  if (supplied && typeof supplied === "object") {
    const gesture = String(supplied.gesture || "");
    const sequence = Array.isArray(supplied.sequence)
      ? supplied.sequence.map(String).filter((item) => allowedGestures.has(item)).slice(0, 5)
      : null;
    if (allowedGestures.has(gesture)) {
      return {
        reason: String(supplied.reason || "structured-intent").slice(0, 80),
        emotion: normalizeEmotion(supplied.emotion || "neutral"),
        intensity: Math.max(1, Math.min(3, Number(supplied.intensity) || 1)),
        gestures: [gesture],
        gesture,
        sequence: sequence?.length >= 2 ? sequence : null,
        explicit: true,
        structured: true,
      };
    }
  }
  const expressions = Array.isArray(actions?.expressions) ? actions.expressions : [];
  const requested = expressions.find((item) => typeof item === "string") ||
    extractInlineEmotion(text);
  return classifyAssistantMotion(text, requested || "neutral");
}

export function classifyAssistantMotion(text, suppliedEmotion = "") {
  const clean = String(text || "").trim();
  const inlineEmotion = extractInlineEmotion(clean);
  let emotion = normalizeEmotion(suppliedEmotion || inlineEmotion || "neutral");
  const withoutTags = clean.replace(
    new RegExp(INLINE_EMOTION_PATTERN.source, "gi"),
    "",
  );

  const contrastTail = withoutTags.match(/(?:但是|不过|然而|可是|但)([^但]+)$/)?.[1]?.trim();
  if (contrastTail && contrastTail !== withoutTags) {
    const contrasted = classifyAssistantMotion(contrastTail, emotion);
    if (contrasted) return { ...contrasted, reason: `contrast:${contrasted.reason}` };
  }

  if (/(不是不开心|并非不开心|不是不高兴|并非不高兴)/.test(withoutTags)) {
    return result(clean, "double-negation-happiness", "happy", 1, ["nod", "shy"]);
  }

  if (/(不开心|不高兴|不快乐|开心不起来|高兴不起来)/.test(withoutTags)) {
    return result(clean, "negated-happiness", "sad", 2, ["shy", "tired"]);
  }
  const deniesAnger = /(并不生气|没有生气|没生气|不是生气)/.test(withoutTags);
  const deniesFear = /(并不害怕|没有害怕|不害怕|没在怕)/.test(withoutTags);
  if (deniesAnger && emotion === "angry") emotion = "neutral";
  if (deniesFear && emotion === "fear") emotion = "neutral";

  if (/(再见|拜拜|告辞|回头见|晚安|下次见)/.test(withoutTags)) {
    return result(clean, "farewell", "relaxed", 2, ["wave"], ["wave", "bow"]);
  }
  if (/(你好|嗨|哈喽|早上好|晚上好|很高兴见到你|见到你真好|欢迎|好久不见|又见面)/.test(withoutTags)) {
    return result(clean, "greeting", "happy", 2, ["wave"]);
  }
  if (/(万分感谢|太感谢|非常感谢|感激不尽|郑重道歉|十分抱歉|真的对不起)/.test(withoutTags)) {
    return result(clean, "strong-gratitude-apology", "relaxed", 2, ["bow"], ["bow", "nod"]);
  }
  if (/(谢谢|多谢|感谢|抱歉|对不起)/.test(withoutTags)) {
    return result(clean, "gratitude-apology", "relaxed", 1, ["bow", "nod"]);
  }
  if (/(没错|当然|好的|好呀|可以|答应你|没问题|我同意|说得对|确实|明白|知道了|交给我|放心)/.test(withoutTags)) {
    return result(clean, "agreement", "relaxed", 1, ["nod"]);
  }
  if (/(不行|不是|不要|不能|并不是|别这样|不可以|办不到|我拒绝|我不同意|别想|休想|没门)/.test(withoutTags)) {
    return result(clean, "refusal", "angry", 2, ["shake"]);
  }
  if (/(让我想想|我想想|想一想|我觉得|或许|可能|思考一下|分析一下|考虑一下|得想想|需要分析)/.test(withoutTags)) {
    return result(clean, "thinking", "neutral", 1, ["think"]);
  }
  if (/(我在听|听着呢|你继续说|请继续|接着说|我听明白了)/.test(withoutTags)) {
    return result(clean, "listening", "relaxed", 1, ["listen"]);
  }
  if (/(完全不懂|搞不明白|莫名其妙|怎么回事|为什么会这样|困惑|迷惑)/.test(withoutTags)) {
    return result(clean, "confusion", "neutral", 2, ["confused", "think"]);
  }
  if (/(狂喜|欣喜若狂|乐疯了|高兴疯了|兴奋极了|激动坏了|太爽了)/.test(withoutTags)) {
    const sequence = choose(`ecstasy-sequence:${clean}`, [
      ["celebrate", "cheer"],
      ["cheer", "dance"],
    ]);
    return result(clean, "ecstasy", "happy", 3, [sequence[0]], sequence);
  }
  if (/(开心|快乐|高兴|愉快|哈哈|太好了|真棒|太棒|喜欢)/.test(withoutTags)) {
    return result(clean, "happiness", "happy", 2, ["celebrate", "cheer"]);
  }
  if (/(满意|欣慰|不错|还不错|轻松|安心)/.test(withoutTags)) {
    return result(clean, "mild-happiness", "happy", 1, ["nod", "shy"]);
  }
  if (/(暴怒|狂怒|气炸|气疯|怒不可遏|火冒三丈)/.test(withoutTags)) {
    const sequence = choose(`rage-sequence:${clean}`, [
      ["angry", "emphasize"],
      ["angry", "shake"],
    ]);
    return result(clean, "rage", "angry", 3, ["angry"], sequence);
  }
  if (!deniesAnger && /(生气|愤怒|去你的|去死|滚开|混蛋|闭嘴|可恶|气死|惹我)/.test(withoutTags)) {
    return result(clean, "anger", "angry", 2, ["angry"]);
  }
  if (/(烦人|别烦|讨厌|不耐烦|挑衅)/.test(withoutTags)) {
    return result(clean, "annoyance", "angry", 1, ["emphasize", "shake"]);
  }
  if (/(崩溃|绝望|痛苦|心碎|悲痛欲绝)/.test(withoutTags)) {
    return result(clean, "grief", "sad", 3, ["tired"], ["shy", "tired"]);
  }
  if (/(难过|伤心|难受|失落|沮丧|想哭|委屈)/.test(withoutTags)) {
    return result(clean, "sadness", "sad", 2, ["shy", "tired"]);
  }
  if (!deniesFear && /(吓死|吓坏|恐惧|惊恐|毛骨悚然)/.test(withoutTags)) {
    return result(clean, "terror", "fear", 3, ["shiver"]);
  }
  if (!deniesFear && /(害怕|担心|紧张|怕了)/.test(withoutTags)) {
    return result(clean, "fear", "fear", 2, ["shiver", "surprised"]);
  }
  if (/(震惊|惊呆|目瞪口呆|难以置信)/.test(withoutTags)) {
    return result(clean, "shock", "surprised", 3, ["surprised"], ["surprised", "confused"]);
  }
  if (/(居然|竟然|真的吗|没想到|天哪|吓一跳|太意外)/.test(withoutTags)) {
    return result(clean, "surprise", "surprised", 2, ["surprised"]);
  }
  if (/(好累|累死|疲惫|没力气|困死|想睡)/.test(withoutTags)) {
    return result(clean, "fatigue", "sad", 2, ["tired"]);
  }
  if (/(好冷|冷死|发抖|冻坏)/.test(withoutTags)) {
    return result(clean, "cold", "fear", 2, ["shiver"]);
  }
  if (/(冷静|放松|平静|深呼吸|冥想)/.test(withoutTags)) {
    return result(clean, "calm", "relaxed", 1, ["meditate", "listen"]);
  }

  const emotionDefaults = {
    happy: () => result(clean, "tag-happy", "happy", 2, ["celebrate", "cheer"]),
    angry: () => result(clean, "tag-angry", "angry", 2, ["angry"]),
    sad: () => result(clean, "tag-sad", "sad", 2, ["shy", "tired"]),
    fear: () => result(clean, "tag-fear", "fear", 2, ["shiver"]),
    surprised: () => result(clean, "tag-surprise", "surprised", 2, ["surprised"]),
    shy: () => result(clean, "tag-shy", "relaxed", 1, ["shy"]),
    relaxed: () => result(clean, "tag-relaxed", "relaxed", 1, ["listen", "nod"]),
    smirk: () => result(clean, "tag-smirk", "happy", 1, ["nod", "shy"]),
  };
  return emotionDefaults[emotion]?.() || null;
}
