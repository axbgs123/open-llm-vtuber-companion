import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.open_llm_vtuber import (
    companion_routes,
    memory_core,
    memory_fts,
    memory_records,
    memory_semantic,
    proactive_manager,
    runtime_manager,
)


class CompanionFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous_cwd = Path.cwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.previous_cwd)
        self.temp.cleanup()

    def test_core_memory_is_per_character(self):
        with patch.object(memory_core, "CHAT_HISTORY_DIR", str(self.root / "history")):
            self.assertTrue(memory_core.save_core_memory("alice", "- 喜欢苹果"))
            self.assertTrue(memory_core.save_core_memory("bob", "- 喜欢咖啡"))
            self.assertEqual(memory_core.load_core_memory("alice"), "- 喜欢苹果")
            self.assertEqual(memory_core.load_core_memory("bob"), "- 喜欢咖啡")
            self.assertTrue(memory_core.clear_core_memory("alice"))
            self.assertEqual(memory_core.load_core_memory("alice"), "")

    def test_fts_indexes_and_finds_cjk_history(self):
        history = self.root / "chat_history" / "test_role"
        history.mkdir(parents=True)
        (history / "turn.json").write_text(
            json.dumps(
                [
                    {"role": "human", "content": "我周末喜欢去爬山", "timestamp": "1"},
                    {"role": "ai", "content": "下次可以聊聊路线", "timestamp": "2"},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.assertEqual(memory_fts.rebuild_index("test_role"), 2)
        results = memory_fts.search("test_role", "周末喜欢", 3)
        self.assertTrue(any("爬山" in item for item in results))
        detailed = memory_fts.search_detailed("test_role", "周末喜欢", 3)
        self.assertEqual(detailed[0]["history_uid"], "turn")

    def test_proactive_settings_are_bounded_and_write_prompt(self):
        data_dir = self.root / "companion_data"
        state_path = data_dir / "state.json"
        prompt_path = self.root / "prompts" / "proactive.txt"
        with (
            patch.object(companion_routes, "DATA_DIR", data_dir),
            patch.object(companion_routes, "STATE_PATH", state_path),
            patch.object(companion_routes, "PROACTIVE_PROMPT_PATH", prompt_path),
        ):
            state = companion_routes._default_state()
            state["proactive"].update(
                {"idle_seconds": 1, "cooldown_seconds": 2, "topics": ["电影"]}
            )
            companion_routes._save_state(state)
            settings = companion_routes.get_proactive_settings()
            self.assertEqual(settings["idle_seconds"], 15)
            self.assertGreaterEqual(settings["cooldown_seconds"], 15)
            companion_routes.write_proactive_prompt(settings)
            self.assertIn("电影", prompt_path.read_text(encoding="utf-8"))

    def test_voice_backend_is_confined_to_loopback(self):
        self.assertTrue(companion_routes._loopback_api("http://127.0.0.1:9880/tts"))
        self.assertTrue(companion_routes._loopback_api("http://localhost:9880/tts"))
        self.assertFalse(companion_routes._loopback_api("https://example.com/tts"))

    def test_small_model_memory_echo_is_cleaned(self):
        raw = (
            "（目前还没有任何记忆）\n\n"
            "用户说：我叫小白，我每周六去游泳。\n"
            "AI 回复：记住了。"
        )
        self.assertEqual(
            memory_core._clean_consolidation_output(raw),
            "- 我叫小白，我每周六去游泳。",
        )

    def test_memory_conflict_keeps_old_fact_until_resolved(self):
        history_dir = str(self.root / "records")
        with patch.object(memory_records, "CHAT_HISTORY_DIR", history_dir):
            memory_records.update_from_turn("role", "我叫小白。", "- 用户叫小白", "h1")
            records = memory_records.update_from_turn(
                "role", "我叫小黑。", "- 用户叫小黑", "h2"
            )
            pending = [r for r in records if r["status"] == "pending_conflict"]
            self.assertEqual(len(pending), 1)
            self.assertIn("小白", memory_records.active_memory_text("role"))
            memory_records.resolve_conflict("role", pending[0]["id"])
            self.assertIn("小黑", memory_records.active_memory_text("role"))
            self.assertNotIn("小白", memory_records.active_memory_text("role"))

    def test_proactive_policy_dedupes_and_backs_off(self):
        state_path = self.root / "proactive_runtime.json"
        settings = {
            "enabled": True,
            "idle_seconds": 100,
            "adaptive_enabled": True,
            "daily_limit": 6,
            "recent_topic_window": 2,
            "topics": ["电影", "音乐", "旅行"],
            "quiet_hours": {"enabled": False, "start": "23:00", "end": "08:00"},
        }
        with patch.object(proactive_manager, "STATE_PATH", state_path):
            first = proactive_manager.choose_topic("role", settings)
            proactive_manager.record_trigger("role", first)
            second = proactive_manager.choose_topic("role", settings)
            self.assertNotEqual(first, second)
            proactive_manager.record_trigger("role", second)
            self.assertGreater(
                proactive_manager.effective_idle_seconds("role", settings), 100
            )
            proactive_manager.record_human_response("role")
            self.assertFalse(proactive_manager.get_runtime("role")["awaiting_response"])

    def test_cosine_similarity(self):
        self.assertAlmostEqual(memory_semantic._cosine([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertAlmostEqual(memory_semantic._cosine([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_runtime_settings_are_bounded(self):
        data_dir = self.root / "runtime"
        with (
            patch.object(runtime_manager, "DATA_DIR", data_dir),
            patch.object(runtime_manager, "SETTINGS_PATH", data_dir / "runtime.json"),
        ):
            settings = runtime_manager.save_settings(
                {"llm_idle_unload_seconds": 1, "voice_idle_unload_seconds": 999999}
            )
            self.assertEqual(settings["llm_idle_unload_seconds"], 60)
            self.assertEqual(settings["voice_idle_unload_seconds"], 86400)


if __name__ == "__main__":
    unittest.main()
