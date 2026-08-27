import asyncio
import json
import os
import tempfile
import unittest
import datetime as dt
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.open_llm_vtuber import (
    companion_routes,
    memory_core,
    memory_fts,
    memory_records,
    memory_semantic,
    proactive_manager,
    commitment_manager,
    relationship_state,
    companion_diagnostics,
    runtime_manager,
    backup_manager,
    data_migrations,
    environment_awareness,
)
from src.open_llm_vtuber.tts.gpt_sovits_tts import TTSEngine as GPTSoVITSEngine
from src.open_llm_vtuber.tts.cosyvoice_tts import TTSEngine as CosyVoiceEngine
from src.open_llm_vtuber.conversations.tts_manager import TTSTaskManager
from src.open_llm_vtuber.agent.output_types import Actions, DisplayText
from src.open_llm_vtuber.server import inject_companion_lipsync
from src.open_llm_vtuber.chat_history_manager import get_history_list


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

    def test_lipsync_bridge_is_injected_once(self):
        html = '<html><head><script type="module" src="main.js"></script></head></html>'
        injected = inject_companion_lipsync(html)
        self.assertIn("/companion-assets/lip_sync_bridge.js", injected)
        self.assertIn("/companion-assets/ui_guard.js", injected)
        self.assertIn("/companion-assets/vrm_renderer.mjs", injected)
        self.assertEqual(
            inject_companion_lipsync(injected).count("lip_sync_bridge.js"), 1
        )
        self.assertEqual(
            inject_companion_lipsync(injected).count("vrm_renderer.mjs"), 1
        )

    def test_avatar_settings_are_per_character_and_bounded(self):
        data_dir = self.root / "companion_data"
        with (
            patch.object(companion_routes, "DATA_DIR", data_dir),
            patch.object(companion_routes, "STATE_PATH", data_dir / "state.json"),
        ):
            state = companion_routes._default_state()
            state["avatars"]["alice"] = {
                "renderer": "vrm",
                "active_vrm": "model1",
                "scale": 99,
                "camera_distance": 0,
                "y_offset": -9,
                "action_style": "wild",
                "gesture_intensity": 8,
                "gesture_frequency": -2,
                "gaze_enabled": False,
                "stage_x": 99,
                "stage_y": -99,
            }
            state["vrm_models"]["alice"] = [{"id": "model1", "name": "Alice 3D"}]
            state["vrm_animations"]["alice"] = [
                {"id": "motion1", "name": "Wave", "gesture": "wave"}
            ]
            state["motion_combos"]["alice"] = [
                {
                    "id": "combo1",
                    "name": "Formal hello",
                    "trigger": "wave",
                    "steps": ["wave", "nod", "bow"],
                }
            ]
            companion_routes._save_state(state)
            settings = companion_routes.get_avatar_settings("alice")
            self.assertEqual(settings["renderer"], "vrm")
            self.assertEqual(settings["scale"], 3.0)
            self.assertEqual(settings["camera_distance"], 0.6)
            self.assertEqual(settings["y_offset"], -2.0)
            self.assertEqual(settings["action_style"], "natural")
            self.assertEqual(settings["gesture_intensity"], 1.0)
            self.assertEqual(settings["gesture_frequency"], 0.0)
            self.assertFalse(settings["gaze_enabled"])
            self.assertEqual(settings["stage_x"], 3.0)
            self.assertEqual(settings["stage_y"], -3.0)
            self.assertEqual(
                companion_routes.get_avatar_settings("bob")["renderer"], "live2d"
            )
            self.assertEqual(
                companion_routes.get_vrm_profiles("alice")[0]["name"], "Alice 3D"
            )
            self.assertEqual(
                companion_routes.get_vrma_profiles("alice")[0]["gesture"], "wave"
            )
            self.assertEqual(
                companion_routes.get_motion_combos("alice")[0]["steps"],
                ["wave", "nod", "bow"],
            )

    def test_character_backup_includes_vrma_files(self):
        root = self.root / "motion-backup"
        motion_dir = root / "companion_data" / "vrm_animations" / "alice"
        motion_dir.mkdir(parents=True)
        motion = motion_dir / "wave.vrma"
        motion.write_bytes(b"glTF-motion")
        (root / "characters").mkdir()
        (root / "chat_history").mkdir()
        with patch.object(backup_manager, "ROOT", root):
            self.assertIn(motion, backup_manager._character_files("alice"))

    def test_lipsync_settings_are_bounded(self):
        data_dir = self.root / "companion_data"
        with (
            patch.object(companion_routes, "DATA_DIR", data_dir),
            patch.object(companion_routes, "STATE_PATH", data_dir / "state.json"),
        ):
            state = companion_routes._default_state()
            state["lipsync"].update(
                {"gain": 99, "silence_threshold": -1, "attack": 0, "release": 8}
            )
            companion_routes._save_state(state)
            settings = companion_routes.get_lipsync_settings()
            self.assertEqual(settings["gain"], 3.0)
            self.assertEqual(settings["silence_threshold"], 0.0)
            self.assertEqual(settings["attack"], 0.05)
            self.assertEqual(settings["release"], 1.0)

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

    def test_companion_state_files_are_not_chat_transcripts(self):
        history_root = self.root / "history"
        role_dir = history_root / "role"
        role_dir.mkdir(parents=True)
        (role_dir / "turn.json").write_text(
            json.dumps(
                [
                    {
                        "role": "human",
                        "content": "你好",
                        "timestamp": "2026-01-01T00:00:00+08:00",
                    }
                ]
            ),
            encoding="utf-8",
        )
        (role_dir / "relationship_state.json").write_text("{}", encoding="utf-8")
        (role_dir / "commitments.json").write_text("[]", encoding="utf-8")
        (role_dir / "memory_records.json").write_text("[]", encoding="utf-8")
        with patch(
            "src.open_llm_vtuber.chat_history_manager._ensure_conf_dir",
            return_value=str(role_dir),
        ):
            self.assertEqual(
                [item["uid"] for item in get_history_list("role")], ["turn"]
            )
        with patch.object(memory_fts, "_conf_dir", return_value=str(role_dir)):
            self.assertEqual(
                [Path(path).name for path in memory_fts._list_transcripts("role")],
                ["turn.json"],
            )

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
        removed_path = root / "companion_data" / "removed.json"
        removed_path.write_text("{}", encoding="utf-8")
        backup_dir = root / "backups"
        with (
            patch.object(backup_manager, "ROOT", root),
            patch.object(backup_manager, "BACKUP_DIR", backup_dir),
            patch.object(backup_manager, "INDEX_PATH", backup_dir / "index.json"),
            patch.object(
                backup_manager, "KEY_PATH", root / "companion_data" / ".backup.key"
            ),
            patch.object(
                backup_manager, "KEYRING_DIR", root / "companion_data" / "backup_keys"
            ),
            patch.object(
                backup_manager, "RETENTION_PATH", backup_dir / "retention.json"
            ),
        ):
            full = backup_manager.create_backup(scope="global", reason="test")
            encrypted = (backup_dir / full["filename"]).read_bytes()
            self.assertTrue(encrypted.startswith(backup_manager.MAGIC))
            self.assertNotIn(b"value: one", encrypted)
            (root / "conf.yaml").write_text("value: two\n", encoding="utf-8")
            removed_path.unlink()
            incremental = backup_manager.create_backup(
                scope="global", incremental=True, reason="test"
            )
            self.assertEqual(incremental["mode"], "incremental")
            removed_path.write_text("current", encoding="utf-8")
            (root / "conf.yaml").write_text("broken\n", encoding="utf-8")
            preview = backup_manager.preview_backup(incremental["filename"])
            self.assertEqual(preview["counts"]["overwritten"], 1)
            self.assertIn("companion_data/removed.json", preview["deleted"])
            self.assertNotIn("companion_data/removed.json", preview["overwritten"])
            self.assertEqual(len(preview["chain"]), 2)
            backup_manager.restore_backup(incremental["filename"])
            self.assertEqual(
                (root / "conf.yaml").read_text(encoding="utf-8"), "value: two\n"
            )
            self.assertEqual(len(backup_manager.list_backups()), 3)

    def test_backup_keyring_and_retention(self):
        root = self.root / "vault"
        root.mkdir()
        (root / "conf.yaml").write_text("value: one\n", encoding="utf-8")
        for folder in ("characters", "chat_history", "companion_data"):
            (root / folder).mkdir()
        backup_dir = root / "backups"
        with (
            patch.object(backup_manager, "ROOT", root),
            patch.object(backup_manager, "BACKUP_DIR", backup_dir),
            patch.object(backup_manager, "INDEX_PATH", backup_dir / "index.json"),
            patch.object(
                backup_manager, "KEY_PATH", root / "companion_data" / ".backup.key"
            ),
            patch.object(
                backup_manager, "KEYRING_DIR", root / "companion_data" / "backup_keys"
            ),
            patch.object(
                backup_manager, "RETENTION_PATH", backup_dir / "retention.json"
            ),
        ):
            old_key = backup_manager.Fernet.generate_key()
            backup_manager.KEY_PATH.write_bytes(old_key + b"\n")
            foreign = backup_manager.create_backup(scope="global", reason="test")
            backup_manager.KEY_PATH.write_bytes(
                backup_manager.Fernet.generate_key() + b"\n"
            )
            imported = backup_manager.import_recovery_key(old_key)
            self.assertEqual(imported["status"], "imported")
            self.assertEqual(len(list(backup_manager.KEYRING_DIR.glob("*.key"))), 1)
            self.assertEqual(
                backup_manager.preview_backup(foreign["filename"])["scope"], "global"
            )
            for index in range(7):
                (root / "conf.yaml").write_text(f"value: {index}\n", encoding="utf-8")
                backup_manager.create_backup(scope="global", reason="test")
            backup_manager.save_retention({"max_backups": 5, "keep_safety": 1})
            result = backup_manager.prune_backups()
            self.assertEqual(len(result["removed"]), 3)
            self.assertEqual(len(backup_manager.list_backups()), 5)

    def test_retention_keeps_incremental_base(self):
        root = self.root / "retention-chain"
        (root / "chat_history" / "role").mkdir(parents=True)
        (root / "companion_data").mkdir()
        (root / "characters").mkdir()
        (root / "conf.yaml").write_text(
            "character_config:\n  conf_uid: role\n", encoding="utf-8"
        )
        backup_dir = root / "backups"
        with (
            patch.object(backup_manager, "ROOT", root),
            patch.object(backup_manager, "BACKUP_DIR", backup_dir),
            patch.object(backup_manager, "INDEX_PATH", backup_dir / "index.json"),
            patch.object(
                backup_manager, "KEY_PATH", root / "companion_data" / ".backup.key"
            ),
            patch.object(
                backup_manager, "KEYRING_DIR", root / "companion_data" / "backup_keys"
            ),
            patch.object(
                backup_manager, "RETENTION_PATH", backup_dir / "retention.json"
            ),
        ):
            full = backup_manager.create_backup(scope="global", reason="manual")
            for index in range(6):
                (root / "chat_history" / "role" / f"{index}.json").write_text("{}")
                backup_manager.create_backup(
                    scope="character", conf_uid="role", reason="manual"
                )
            backup_manager.save_retention({"max_backups": 5, "keep_safety": 1})
            backup_manager.prune_backups()
            self.assertTrue((backup_dir / full["filename"]).is_file())
            (root / "conf.yaml").write_text(
                "character_config:\n  conf_uid: role\nchanged: true\n", encoding="utf-8"
            )
            incremental = backup_manager.create_backup(scope="global", incremental=True)
            self.assertEqual(incremental["base"], full["filename"])
            self.assertEqual(
                backup_manager.preview_backup(incremental["filename"])["scope"],
                "global",
            )
            (backup_dir / full["filename"]).unlink()
            fallback = backup_manager.create_backup(scope="global", incremental=True)
            self.assertEqual(fallback["mode"], "full")
            self.assertEqual(fallback["base"], "")

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

    def test_commitments_extract_and_complete(self):
        with patch.object(
            commitment_manager,
            "_path",
            return_value=self.root / "history" / "role" / "commitments.json",
        ):
            created = commitment_manager.extract_from_turn(
                "role", "提醒我明天9点提交项目周报"
            )
            self.assertEqual(len(created), 1)
            self.assertIn("提交项目周报", created[0]["content"])
            self.assertIn("提交项目周报", commitment_manager.due_topic("role", 48))
            self.assertEqual(commitment_manager.due_topic("role", 48), "")
            rows = commitment_manager.update("role", created[0]["id"], "done")
            self.assertEqual(rows[0]["status"], "done")

    def test_chinese_periods_and_relative_weekdays(self):
        now = dt.datetime(2026, 8, 25, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        self.assertIn("T15:00", commitment_manager.parse_due("下午3点提醒我吃药", now))
        self.assertIn("T20:00", commitment_manager.parse_due("今晚8点提醒我", now))
        self.assertTrue(
            commitment_manager.parse_due("下周三提醒我开会", now).startswith(
                "2026-09-02"
            )
        )
        self.assertGreater(
            dt.datetime.fromisoformat(
                commitment_manager.parse_due("今天要写周报", now)
            ),
            now,
        )

    def test_relationship_continuity_tracks_recent_mood(self):
        with patch.object(
            relationship_state,
            "_path",
            return_value=self.root / "history" / "role" / "relationship_state.json",
        ):
            state = relationship_state.update_from_turn(
                "role", "今天压力很大，有点累", "先休息一下"
            )
            self.assertEqual(state["recent_moods"][-1]["mood"], "低落")
            self.assertIn("偏低落", relationship_state.prompt_context("role"))
            state = relationship_state.update_from_turn(
                "role", "我今天很不开心", "我在"
            )
            self.assertEqual(state["recent_moods"][-1]["mood"], "低落")
            state = relationship_state.update_from_turn(
                "role", "我不喜欢这件事", "明白"
            )
            self.assertEqual(state["recent_moods"][-1]["mood"], "低落")

    def test_diagnostics_aggregates_cache_hits(self):
        with patch.object(companion_diagnostics, "PATH", self.root / "diag.json"):
            companion_diagnostics.record("voice_synthesis", 20, cached=True)
            companion_diagnostics.record("voice_synthesis", 40)
            metric = companion_diagnostics.summary()["metrics"][0]
            self.assertEqual(metric["average_ms"], 30)
            self.assertEqual(metric["cache_hits"], 1)

    def test_diagnostics_concurrent_writes_do_not_fail(self):
        with (
            patch.object(companion_diagnostics, "DATA_DIR", self.root),
            patch.object(
                companion_diagnostics, "PATH", self.root / "concurrent-diag.json"
            ),
        ):
            with ThreadPoolExecutor(max_workers=20) as pool:
                futures = [
                    pool.submit(companion_diagnostics.record, "voice", index)
                    for index in range(100)
                ]
                for future in futures:
                    future.result()
            self.assertEqual(companion_diagnostics.summary()["events"], 100)

    def test_diagnostics_failure_never_breaks_caller(self):
        with patch.object(
            companion_diagnostics, "_save", side_effect=OSError("disk busy")
        ):
            companion_diagnostics.record("voice", 10)

    def test_gpt_voice_uses_progressive_phrase_tasks(self):
        async def exercise():
            engine = GPTSoVITSEngine()
            text = "这是第一段较短的话，用来尽快开始播放；这是第二段内容，用来验证后续片段按顺序生成。"
            self.assertGreater(len(engine.split_streaming_text(text)), 1)
            manager = TTSTaskManager()
            manager._process_tts = AsyncMock()
            await manager.speak(
                text,
                DisplayText(text=text, name="AI"),
                Actions(),
                None,
                engine,
                AsyncMock(),
            )
            await asyncio.gather(*manager.task_list)
            calls = manager._process_tts.await_args_list
            self.assertGreater(len(calls), 1)
            self.assertEqual(calls[0].kwargs["display_text"].text, text)
            self.assertEqual(calls[1].kwargs["display_text"].text, "")
            manager.clear()

        asyncio.run(exercise())

    def test_first_voice_fragment_is_queued_before_later_generation_finishes(self):
        class FragmentEngine:
            def __init__(self, root: Path):
                self.root = root
                self.calls = 0
                self.second_started = asyncio.Event()
                self.release_second = asyncio.Event()

            async def async_generate_audio(self, text, file_name_no_ext=None):
                self.calls += 1
                if self.calls == 2:
                    self.second_started.set()
                    await self.release_second.wait()
                path = self.root / f"fragment-{self.calls}.wav"
                with wave.open(str(path), "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(16000)
                    audio.writeframes(b"\1\0" * 160)
                return str(path)

            def remove_file(self, path):
                Path(path).unlink(missing_ok=True)

        async def exercise():
            manager = TTSTaskManager()
            engine = FragmentEngine(self.root)
            task = asyncio.create_task(
                manager._process_tts_fragments(
                    ["第一片", "第二片"],
                    DisplayText(text="完整文字", name="AI"),
                    Actions(),
                    None,
                    engine,
                    0,
                )
            )
            await asyncio.wait_for(engine.second_started.wait(), timeout=2)
            self.assertEqual(manager._payload_queue.qsize(), 1)
            engine.release_second.set()
            await task
            self.assertEqual(manager._payload_queue.qsize(), 2)

        asyncio.run(exercise())

    def test_voice_cache_works_without_starting_backend(self):
        engine = GPTSoVITSEngine(ref_audio_path="voice.wav", prompt_text="参考")
        cached = self.root / "cached.wav"
        output = self.root / "output.wav"
        cached.write_bytes(b"RIFF" + b"0" * 256)
        with (
            patch.object(engine, "_cache_path", return_value=cached),
            patch.object(engine, "generate_cache_file_name", return_value=str(output)),
            patch.object(companion_diagnostics, "PATH", self.root / "voice-diag.json"),
        ):
            self.assertEqual(engine.generate_audio("你好"), str(output))
            self.assertEqual(output.read_bytes(), cached.read_bytes())

    def test_cosyvoice_maps_character_expression_to_voice_emotion(self):
        self.assertEqual(
            CosyVoiceEngine._emotion(Actions(expressions=["excited"])), "excited"
        )
        self.assertEqual(CosyVoiceEngine._emotion(Actions()), "neutral")


if __name__ == "__main__":
    unittest.main()
