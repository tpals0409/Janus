"""로컬 모델 셋업 — 크기 파서·경로 해석·디스크 게이트·단일 실행."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from janus_server import model_setup


class SizeParsingTests(unittest.TestCase):
    def test_parses_the_units_hf_dry_run_emits(self):
        self.assertEqual(int(5.3 * 1024**3), model_setup.parse_size("5.3G"))
        self.assertEqual(int(20.0 * 1024**2), model_setup.parse_size("20.0M"))
        self.assertEqual(1638, model_setup.parse_size("1.6K"))
        self.assertEqual(632, model_setup.parse_size("632.0"))
        self.assertEqual(0, model_setup.parse_size(""))
        self.assertEqual(0, model_setup.parse_size("   "))


class HubRootTests(unittest.TestCase):
    """Electron이 준 값이 이겨야 한다 — 양쪽이 따로 계산하면 HF_HOME 사용자에게
    '다운로드는 됐는데 앱은 없다고 한다'가 생긴다."""

    def _with(self, **env):
        return mock.patch.dict(os.environ, env, clear=False)

    def test_janus_override_wins_over_everything(self):
        with self._with(
            JANUS_HF_HUB_ROOT="/tmp/janus-hub", HF_HUB_CACHE="/tmp/other", HF_HOME="/tmp/home"
        ):
            self.assertEqual(Path("/tmp/janus-hub"), model_setup.hub_root())

    def test_falls_back_through_hf_env_then_default(self):
        clean = {
            key: None for key in ("JANUS_HF_HUB_ROOT", "HF_HUB_CACHE", "HF_HOME")
        }
        saved = {key: os.environ.pop(key, None) for key in clean}
        try:
            with self._with(HF_HUB_CACHE="/tmp/cache"):
                self.assertEqual(Path("/tmp/cache"), model_setup.hub_root())
            os.environ.pop("HF_HUB_CACHE", None)
            with self._with(HF_HOME="/tmp/hfhome"):
                self.assertEqual(Path("/tmp/hfhome/hub"), model_setup.hub_root())
            os.environ.pop("HF_HOME", None)
            self.assertEqual(
                Path.home() / ".cache" / "huggingface" / "hub", model_setup.hub_root()
            )
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_repo_dir_uses_the_hf_cache_naming(self):
        with self._with(JANUS_HF_HUB_ROOT="/tmp/hub"):
            self.assertEqual(
                Path("/tmp/hub/models--mlx-community--Qwen3.8-27B-4bit"),
                model_setup.repo_dir("mlx-community/Qwen3.8-27B-4bit"),
            )


class CatalogTests(unittest.TestCase):
    def test_each_repo_carries_its_own_layout(self):
        """repo마다 스냅샷 안 배치가 다르다 — 한 가지 모양을 가정하면 하나가 깨진다."""
        stock = model_setup.MODEL_CATALOG["qwen3.8-27b"]
        uncensored = model_setup.MODEL_CATALOG["qwen3.8-27b-uncensored"]
        self.assertEqual("", stock["subpath"])
        self.assertIsNone(stock["include"])
        self.assertEqual("4-bit", uncensored["subpath"])
        self.assertEqual("4-bit/*", uncensored["include"])
        self.assertIn(model_setup.DEFAULT_MODEL_ID, model_setup.MODEL_CATALOG)


class DownloaderTests(unittest.TestCase):
    def test_rejects_an_unknown_model(self):
        downloader = model_setup.ModelDownloader()
        with self.assertRaises(ValueError):
            downloader.start("no-such-model")

    def test_refuses_when_the_disk_cannot_hold_it(self):
        downloader = model_setup.ModelDownloader()
        with mock.patch.object(model_setup, "plan", return_value={"total_bytes": 20 * 1024**3}), \
             mock.patch.object(model_setup, "_plan_repo", return_value={"total_bytes": 1024**3}), \
             mock.patch.object(model_setup, "disk", return_value={"free_bytes": 5 * 1024**3}):
            with self.assertRaises(RuntimeError) as caught:
                downloader.start(model_setup.DEFAULT_MODEL_ID)
        self.assertIn("디스크 여유", str(caught.exception))
        self.assertIsNone(downloader.snapshot())  # 실패한 시도는 잡을 남기지 않는다

    def test_headroom_is_required_on_top_of_the_download(self):
        """딱 맞게 비어 있으면 받자마자 로드할 자리가 없다."""
        downloader = model_setup.ModelDownloader()
        needed = 16 * 1024**3
        with mock.patch.object(model_setup, "plan", return_value={"total_bytes": needed}), \
             mock.patch.object(model_setup, "_plan_repo", return_value={"total_bytes": 0}), \
             mock.patch.object(model_setup, "disk", return_value={"free_bytes": needed + 1024}):
            with self.assertRaises(RuntimeError):
                downloader.start(model_setup.DEFAULT_MODEL_ID)

    def test_only_one_download_runs_at_a_time(self):
        downloader = model_setup.ModelDownloader()
        started = []

        def fake_download(repo, include):
            started.append(repo)
            import time
            time.sleep(0.4)

        with mock.patch.object(model_setup, "plan", return_value={"total_bytes": 1}), \
             mock.patch.object(model_setup, "_plan_repo", return_value={"total_bytes": 1}), \
             mock.patch.object(model_setup, "disk", return_value={"free_bytes": 10 * 1024**4}), \
             mock.patch.object(model_setup.ModelDownloader, "_download", fake_download):
            downloader.start(model_setup.DEFAULT_MODEL_ID)
            with self.assertRaises(RuntimeError) as caught:
                downloader.start(model_setup.DEFAULT_MODEL_ID)
            self.assertIn("이미 다운로드가 진행 중", str(caught.exception))

    def test_publishes_progress_and_settles_completed(self):
        events: list[tuple[str, str]] = []
        downloader = model_setup.ModelDownloader(
            publish=lambda topic, event, **_: events.append((topic, event))
        )
        with mock.patch.object(model_setup, "plan", return_value={"total_bytes": 100}), \
             mock.patch.object(model_setup, "_plan_repo", return_value={"total_bytes": 0}), \
             mock.patch.object(model_setup, "disk", return_value={"free_bytes": 10 * 1024**4}), \
             mock.patch.object(model_setup.ModelDownloader, "_download", lambda *a: None):
            downloader.start(model_setup.DEFAULT_MODEL_ID, with_draft=False)
            downloader._thread.join(timeout=5)
        job = downloader.snapshot()
        self.assertEqual("completed", job["status"])
        self.assertEqual(job["total_bytes"], job["downloaded_bytes"])
        self.assertIn(("model", "ready"), events)

    def test_a_failure_reaches_the_ui_with_a_reason(self):
        events: list[tuple[str, str]] = []
        downloader = model_setup.ModelDownloader(
            publish=lambda topic, event, **_: events.append((topic, event))
        )

        def boom(*_args):
            raise RuntimeError("network is down")

        with mock.patch.object(model_setup, "plan", return_value={"total_bytes": 1}), \
             mock.patch.object(model_setup, "_plan_repo", return_value={"total_bytes": 0}), \
             mock.patch.object(model_setup, "disk", return_value={"free_bytes": 10 * 1024**4}), \
             mock.patch.object(model_setup.ModelDownloader, "_download", boom):
            downloader.start(model_setup.DEFAULT_MODEL_ID, with_draft=False)
            downloader._thread.join(timeout=5)
        job = downloader.snapshot()
        self.assertEqual("failed", job["status"])
        self.assertIn("network is down", job["error"])
        self.assertIn(("model", "failed"), events)

    def test_cancel_without_a_job_is_not_an_error(self):
        self.assertIsNone(model_setup.ModelDownloader().cancel())


class DiskTests(unittest.TestCase):
    def test_measures_an_existing_ancestor_when_the_hub_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "not" / "there" / "hub"
            with mock.patch.dict(os.environ, {"JANUS_HF_HUB_ROOT": str(missing)}):
                usage = model_setup.disk()
        self.assertGreater(usage["free_bytes"], 0)
        self.assertEqual(str(missing), usage["path"])

    def test_dir_size_tolerates_files_vanishing_mid_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.bin").write_bytes(b"x" * 1000)
            (root / "nested").mkdir()
            (root / "nested" / "b.bin").write_bytes(b"y" * 500)
            self.assertEqual(1500, model_setup._dir_size(root))
            self.assertEqual(0, model_setup._dir_size(root / "missing"))


if __name__ == "__main__":
    unittest.main()


class ResolveLocalModelTests(unittest.TestCase):
    """Electron이 해석한 경로가 이겨야 한다 — 캐시 루트를 양쪽이 따로 계산하던 버그의 회귀."""

    def test_prefers_the_path_electron_resolved(self):
        from janus_server import runtime

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"JANUS_LOCAL_MODEL_PATH": tmp}):
                self.assertEqual(tmp, runtime.resolve_local_model("qwen3.8-27b"))
                # 카탈로그에 없는 이름이어도 Electron이 준 경로면 통한다.
                self.assertEqual(tmp, runtime.resolve_local_model("anything"))

    def test_ignores_a_stale_path_and_falls_back_to_the_glob(self):
        from janus_server import runtime

        saved = os.environ.pop("JANUS_LOCAL_MODEL_PATH", None)
        try:
            os.environ["JANUS_LOCAL_MODEL_PATH"] = "/nonexistent/snapshot"
            with self.assertRaises(Exception) as caught:
                runtime.resolve_local_model("no-such-model")
            self.assertIn("모르는 모델", str(caught.exception))
        finally:
            os.environ.pop("JANUS_LOCAL_MODEL_PATH", None)
            if saved is not None:
                os.environ["JANUS_LOCAL_MODEL_PATH"] = saved

    def test_catalog_ids_match_between_electron_and_the_backend(self):
        from janus_server import runtime

        self.assertEqual(
            set(model_setup.MODEL_CATALOG), set(runtime.LOCAL_MODELS),
            "model_setup과 runtime.LOCAL_MODELS의 모델 id가 어긋나면 폴백 경로가 깨진다",
        )
