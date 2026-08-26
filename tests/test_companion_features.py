import json
import os
import tempfile
import unittest
import datetime as dt
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
    backup_manager,
    data_migrations,
    environment_awareness,
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

    def test_encrypted_incremental_backup_and_restore(self):
        root = self.root / "app"
        root.mkdir()
        (root / "conf.yaml").write_text("value: one\n", encoding="utf-8")
        (root / "characters").mkdir()
        (root / "chat_history").mkdir()
        (root / "companion_data").mkdir()
        backup_dir = root / "backups"
        with (
            patch.object(backup_manager, "ROOT", root),
            patch.object(backup_manager, "BACKUP_DIR", backup_dir),
            patch.object(backup_manager, "INDEX_PATH", backup_dir / "index.json"),
            patch.object(
                backup_manager, "KEY_PATH", root / "companion_data" / ".backup.key"
            ),
        ):
            full = backup_manager.create_backup(scope="global", reason="test")
            encrypted = (backup_dir / full["filename"]).read_bytes()
            self.assertTrue(encrypted.startswith(backup_manager.MAGIC))
            self.assertNotIn(b"value: one", encrypted)
            (root / "conf.yaml").write_text("value: two\n", encoding="utf-8")
            incremental = backup_manager.create_backup(
                scope="global", incremental=True, reason="test"
            )
            self.assertEqual(incremental["mode"], "incremental")
            (root / "conf.yaml").write_text("broken\n", encoding="utf-8")
            backup_manager.restore_backup(incremental["filename"])
            self.assertEqual(
                (root / "conf.yaml").read_text(encoding="utf-8"), "value: two\n"
            )
            self.assertEqual(len(backup_manager.list_backups()), 3)

    def test_data_migration_adds_memory_metadata(self):
        root = self.root / "migration"
        data = root / "companion_data"
        records_dir = root / "chat_history" / "role"
        records_dir.mkdir(parents=True)
        record_path = records_dir / "memory_records.json"
        record_path.write_text(
            json.dumps([{"id": "1", "status": "active", "confidence": 0.5}]),
            encoding="utf-8",
        )
        with (
            patch.object(data_migrations, "ROOT", root),
            patch.object(data_migrations, "DATA_DIR", data),
            patch.object(data_migrations, "SCHEMA_PATH", data / "schema.json"),
        ):
            result = data_migrations.run_migrations()
            migrated = json.loads(record_path.read_text(encoding="utf-8"))[0]
            self.assertEqual(result["to"], data_migrations.CURRENT_SCHEMA)
            self.assertEqual(migrated["status"], "pending_confirmation")
            self.assertEqual(migrated["importance"], 3)

    def test_richer_claims_and_expiration(self):
        claims = memory_records.extract_claims(
            "我生日是5月2日，我有个姐姐叫小雨。我正在开发一个日记应用，明天要去医院。"
        )
        categories = {claim["category"] for claim in claims}
        self.assertTrue({"重要日期", "家庭", "项目", "近期计划"}.issubset(categories))
        expired = {
            "status": "active",
            "expires_at": (
                dt.datetime.now().astimezone() - dt.timedelta(days=1)
            ).isoformat(),
        }
        self.assertTrue(memory_records._is_expired(expired))

    def test_environment_policy_blocks_meeting_and_away(self):
        fake = {
            "frontmost_app": "zoom.us",
            "idle_seconds": 1200,
            "screen_locked": False,
            "on_console": True,
            "full_screen": False,
            "focus_mode": False,
            "meeting_apps": ["zoom.us"],
            "microphone_likely_in_use": True,
        }
        settings = {
            "environment": {
                "block_meeting_apps": True,
                "block_full_screen": True,
                "block_focus_mode": True,
                "block_when_locked": True,
                "block_when_away": True,
                "block_microphone": True,
                "away_after_seconds": 900,
                "blocked_apps": ["zoom.us"],
            }
        }
        with patch.object(environment_awareness, "signals", return_value=fake):
            result = environment_awareness.evaluate(settings)
        self.assertFalse(result["allowed"])
        self.assertIn("meeting_app", result["reasons"])
        self.assertIn("user_away", result["reasons"])


if __name__ == "__main__":
    unittest.main()
