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

    def test_already_cached_files_report_a_dash(self):
        """실기기에서 잡은 결함 — hf는 이미 받은 파일의 크기를 "-"로 낸다.
        여기서 예외가 나면 '내려받기'를 누른 사용자가 파이썬 오류를 본다."""
        self.assertEqual(0, model_setup.parse_size("-"))
        self.assertEqual(0, model_setup.parse_size("unknown"))
        self.assertEqual(0, model_setup.parse_size(None))


class FailureMessageTests(unittest.TestCase):
    """트레이스백 꼬리를 UI에 흘리지 않는다."""

    def test_offline_reads_as_a_network_problem(self):
        message = model_setup.classify_hf_failure(
            "huggingface_hub.errors.DryRunError: Dry run cannot be performed as the "
            "repository cannot be accessed. Please check your internet connection or "
            "authentication token.", "some/repo")
        self.assertIn("네트워크", message)
        self.assertNotIn("Traceback", message)

    def test_gated_repo_tells_the_user_to_log_in(self):
        message = model_setup.classify_hf_failure("401 Client Error: Unauthorized", "gated/repo")
        self.assertIn("hf auth login", message)
        self.assertIn("gated/repo", message)

    def test_missing_repo_is_not_confused_with_being_offline(self):
        message = model_setup.classify_hf_failure(
            "RepositoryNotFoundError: 404 Client Error", "gone/repo")
        self.assertIn("찾을 수 없습니다", message)
        self.assertNotIn("네트워크", message)

    def test_an_unrecognized_failure_still_says_something_useful(self):
        message = model_setup.classify_hf_failure("weird explosion", "a/b")
        self.assertIn("a/b", message)
        self.assertIn("weird explosion", message)


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


class EtaTests(unittest.TestCase):
    """실기기에서 ETA가 3시간→7시간→10시간으로 요동쳤다. 신호가 쌓이기 전엔 침묵한다."""

    def _job(self, elapsed_s, downloaded, total=100 * 1024**3):
        job = model_setup.DownloadJob(
            model_id="m", repo="r", total_bytes=total, baseline_bytes=0,
        )
        job.downloaded_bytes = downloaded
        job.started_at = -elapsed_s  # time.monotonic()과의 차이가 elapsed가 된다
        return job

    def test_stays_silent_until_there_is_enough_signal(self):
        import time as _t
        now = _t.monotonic()
        early = model_setup.DownloadJob(
            model_id="m", repo="r", total_bytes=100 * 1024**3, baseline_bytes=0,
        )
        early.started_at = now - 5          # 5초, 0.01%
        early.downloaded_bytes = 10 * 1024**2
        self.assertIsNone(early.view()["eta_ms"], "초반 표본으로 ETA를 내면 안 된다")

        slow = model_setup.DownloadJob(
            model_id="m", repo="r", total_bytes=100 * 1024**3, baseline_bytes=0,
        )
        slow.started_at = now - 60          # 60초인데 아직 0.01% — 비율이 모자라다
        slow.downloaded_bytes = 10 * 1024**2
        self.assertIsNone(slow.view()["eta_ms"])

    def test_reports_once_the_rate_is_measurable(self):
        import time as _t
        job = model_setup.DownloadJob(
            model_id="m", repo="r", total_bytes=100 * 1024**3, baseline_bytes=0,
        )
        job.started_at = _t.monotonic() - 100      # 100초에 10GB = 100MB/s
        job.downloaded_bytes = 10 * 1024**3
        eta = job.view()["eta_ms"]
        self.assertIsNotNone(eta)
        # 남은 90GB / 100MB/s ≈ 900초
        self.assertAlmostEqual(900_000, eta, delta=60_000)

    def test_a_zero_byte_job_never_divides(self):
        import time as _t
        job = model_setup.DownloadJob(
            model_id="m", repo="r", total_bytes=0, baseline_bytes=0,
        )
        job.started_at = _t.monotonic() - 100
        self.assertIsNone(job.view()["eta_ms"])
