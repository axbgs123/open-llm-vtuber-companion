import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyAssistantMotion,
  extractInlineEmotion,
} from "../integrations/vrm_web/src/semantic_motion.js";

test("Chinese happiness synonyms share an emotion but vary by intensity", () => {
  const mild = classifyAssistantMotion("这个结果还不错，我很满意。", "neutral");
  const happy = classifyAssistantMotion("我今天真的很开心！", "neutral");
  const ecstatic = classifyAssistantMotion("我简直欣喜若狂，乐疯了！", "neutral");

  assert.equal(mild.emotion, "happy");
  assert.equal(mild.intensity, 1);
  assert.equal(happy.emotion, "happy");
  assert.equal(happy.intensity, 2);
  assert.equal(ecstatic.emotion, "happy");
  assert.equal(ecstatic.intensity, 3);
  assert.notEqual(mild.gesture, ecstatic.gesture);
  assert.ok(
    ["celebrate,cheer", "cheer,dance"].includes(ecstatic.sequence.join(",")),
  );
});

test("negation prevents opposite emotion motions", () => {
  const unhappy = classifyAssistantMotion("其实我一点也不开心。", "neutral");
  const notAngry = classifyAssistantMotion("我并不生气，只是在说明情况。", "anger");
  const notAfraid = classifyAssistantMotion("我不害怕，放心吧。", "fear");

  assert.equal(unhappy.emotion, "sad");
  assert.ok(["shy", "tired"].includes(unhappy.gesture));
  assert.notEqual(notAngry?.emotion, "angry");
  assert.notEqual(notAfraid?.emotion, "fear");
  assert.equal(
    classifyAssistantMotion("我不是不开心，只是有点累。", "neutral").emotion,
    "happy",
  );
});

test("contrast clauses prefer the assistant's final emotional stance", () => {
  const recovered = classifyAssistantMotion(
    "虽然刚才很难过，但是现在真的很开心。",
    "neutral",
  );
  const drained = classifyAssistantMotion(
    "本来挺开心，不过现在我真的好累。",
    "neutral",
  );
  assert.equal(recovered.emotion, "happy");
  assert.equal(drained.gesture, "tired");
});

test("assistant tags and emotional wording map to distinct motions", () => {
  assert.equal(extractInlineEmotion("【sadness】唉。"), "sadness");
  assert.equal(classifyAssistantMotion("[smirk] 哼。", "").intensity, 1);
  assert.equal(classifyAssistantMotion("去你的！", "neutral").gesture, "angry");
  assert.equal(classifyAssistantMotion("我好累，想睡了。", "neutral").gesture, "tired");
  assert.equal(classifyAssistantMotion("这也太意外了！", "neutral").gesture, "surprised");
  assert.equal(classifyAssistantMotion("好冷，我在发抖。", "neutral").gesture, "shiver");
  assert.equal(classifyAssistantMotion("你继续说，我在听。", "neutral").gesture, "listen");
});

test("high-intensity states use composed choreography", () => {
  assert.ok(
    ["angry,emphasize", "angry,shake"].includes(
      classifyAssistantMotion("我气得火冒三丈！", "neutral").sequence.join(","),
    ),
  );
  assert.deepEqual(
    classifyAssistantMotion("下次见，拜拜。", "neutral").sequence,
    ["wave", "bow"],
  );
  assert.deepEqual(
    classifyAssistantMotion("真的非常感谢你！", "neutral").sequence,
    ["bow", "nod"],
  );
});
