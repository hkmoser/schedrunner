#!/usr/bin/env python3
"""
Tests for the dead-session hold (afm_session_hold) and its integration into
afm_live_daemon.

Runs anywhere: pyicloud, afm_pipeline and requests are stubbed before import, so
no Apple credentials, no BigQuery, no network. All state files are redirected
into a temp dir, so the real ~/.pyicloud and ~/.pyicloud-daemon-session-dead are
never touched.

    python3 -m unittest test_session_hold -v
"""

import os
import sys
import types
import unittest
import shutil
import tempfile

# ── stub pyicloud before afm_live_daemon imports it ───────────────────────────


class PyiCloudException(Exception):
    pass


class PyiCloudFailedLoginException(PyiCloudException):
    pass


class PyiCloudAuthRequiredException(PyiCloudException):
    pass


class PyiCloud2FARequiredException(PyiCloudException):
    pass


class PyiCloudAPIResponseException(PyiCloudException):
    def __init__(self, message="", code=None):
        super().__init__(message)
        self.code = code


_pyicloud = types.ModuleType("pyicloud")
_exceptions = types.ModuleType("pyicloud.exceptions")
for _cls in (PyiCloudException, PyiCloudFailedLoginException,
             PyiCloudAuthRequiredException, PyiCloud2FARequiredException,
             PyiCloudAPIResponseException):
    setattr(_exceptions, _cls.__name__, _cls)
_pyicloud.exceptions = _exceptions
_pyicloud.PyiCloudService = object
sys.modules.setdefault("pyicloud", _pyicloud)
sys.modules.setdefault("pyicloud.exceptions", _exceptions)


class _StubPipeline(types.ModuleType):
    """Stand-in for afm_pipeline. Records every build_api() call — that call IS
    the /accountLogin against Apple, so counting it counts auth attempts."""

    def __init__(self):
        super().__init__("afm_pipeline")
        self.reset()

    def reset(self):
        self.build_calls = []          # one entry per Apple contact
        self.build_error = None        # exception instance to raise, or None
        self.pipeline_error = None
        self.now = [1_000_000.0]

    def build_api(self):
        self.build_calls.append(self.now[0])
        if self.build_error is not None:
            raise self.build_error
        return types.SimpleNamespace(session=types.SimpleNamespace(cookies=[]))

    def install_accountlogin_logging(self, api, log=print):
        pass

    def accountlogin_total(self):
        return len(self.build_calls)

    def accountlogin_count_since(self, secs):
        return 0

    def cleanup_old_csvs(self, log=print):
        return (0, 0)

    def run_pipeline(self, api, log=print):
        if self.pipeline_error is not None:
            raise self.pipeline_error
        return 7


_stub_pipeline = _StubPipeline()
sys.modules["afm_pipeline"] = _stub_pipeline

import afm_session_hold  # noqa: E402
import afm_live_daemon   # noqa: E402


class HoldTestBase(unittest.TestCase):
    """Redirects all state (hold file, session dir, alert-dedup files) to a temp
    dir and installs a controllable clock."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="afm-hold-test-")
        self.session_dir = os.path.join(self.tmp, "pyicloud")
        os.makedirs(self.session_dir)
        self.hold_file = os.path.join(self.tmp, "session-dead")

        self._orig = (afm_session_hold.HOLD_STATE_PATH, afm_session_hold.SESSION_DIR)
        afm_session_hold.HOLD_STATE_PATH = self.hold_file
        afm_session_hold.SESSION_DIR = self.session_dir

        self._orig_daemon = (afm_live_daemon.DEAD_ALERT_FILE,
                             afm_live_daemon.EXPIRY_WARN_FILE)
        afm_live_daemon.DEAD_ALERT_FILE = os.path.join(self.tmp, "alert-last")
        afm_live_daemon.EXPIRY_WARN_FILE = os.path.join(self.tmp, "expiry-warned")

        _stub_pipeline.reset()
        self.write_session("session-v1")

        # Controllable clock shared by the module under test and the daemon.
        self.now = _stub_pipeline.now
        self.now[0] = 1_000_000.0
        self._orig_time = (afm_session_hold.time.time, afm_live_daemon.time.time,
                           afm_live_daemon.time.sleep)
        afm_session_hold.time.time = lambda: self.now[0]
        afm_live_daemon.time.time = lambda: self.now[0]
        afm_live_daemon.time.sleep = lambda s: None

        # Capture alerts instead of POSTing to Pushcut.
        self.alerts = []
        self._orig_alert = afm_live_daemon.send_alert
        afm_live_daemon.send_alert = lambda text: self.alerts.append(text)

        # Silence daemon logging during tests.
        self.logs = []
        self._orig_log = afm_live_daemon.log
        afm_live_daemon.log = lambda msg="": self.logs.append(msg)

        # Replace the flock acquisition: record calls so we can assert a held
        # restart never takes /tmp/afm_live.lock, and avoid same-process
        # lock retention across simulated restarts.
        self.lock_calls = []
        afm_live_daemon._acquire_lock = self._fake_lock

    def _fake_lock(self):
        self.lock_calls.append(self.now[0])
        return object()

    def tearDown(self):
        afm_session_hold.HOLD_STATE_PATH, afm_session_hold.SESSION_DIR = self._orig
        afm_live_daemon.DEAD_ALERT_FILE, afm_live_daemon.EXPIRY_WARN_FILE = self._orig_daemon
        (afm_session_hold.time.time, afm_live_daemon.time.time,
         afm_live_daemon.time.sleep) = self._orig_time
        afm_live_daemon.send_alert = self._orig_alert
        afm_live_daemon.log = self._orig_log
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── helpers ──────────────────────────────────────────────────────────────

    def write_session(self, content):
        """Write a session file. Distinct content => distinct fingerprint."""
        with open(os.path.join(self.session_dir, "abc123.session"), "w") as f:
            f.write(content)

    def reinstall_session_like_auth_sh(self):
        """Reproduce util/auth.py's install: build a new dir, atomic-rename it
        over ~/.pyicloud. Every file is new, so the fingerprint must change."""
        staging = self.session_dir + ".installing"
        os.makedirs(staging)
        with open(os.path.join(staging, "abc123.session"), "w") as f:
            f.write("fresh-session-from-auth-sh")
        shutil.rmtree(self.session_dir)
        os.rename(staging, self.session_dir)

    def run_daemon_once(self):
        """Simulate one launchd start. Returns the process exit code."""
        try:
            afm_live_daemon.main()
        except SystemExit as e:
            return e.code if e.code is not None else 0
        return 0


class TestFingerprint(HoldTestBase):

    def test_fingerprint_stable_when_untouched(self):
        self.assertEqual(afm_session_hold.session_fingerprint(),
                         afm_session_hold.session_fingerprint())

    def test_fingerprint_changes_after_auth_sh_reinstall(self):
        before = afm_session_hold.session_fingerprint()
        self.reinstall_session_like_auth_sh()
        self.assertNotEqual(before, afm_session_hold.session_fingerprint())

    def test_missing_dir_yields_unknown_sentinel(self):
        shutil.rmtree(self.session_dir)
        self.assertEqual(afm_session_hold.session_fingerprint(), "")

    def test_empty_dir_yields_unknown_sentinel(self):
        os.remove(os.path.join(self.session_dir, "abc123.session"))
        self.assertEqual(afm_session_hold.session_fingerprint(), "")


class TestHoldStateMachine(HoldTestBase):

    def test_no_state_file_means_no_hold(self):
        status = afm_session_hold.check_hold()
        self.assertFalse(status.active)
        self.assertEqual(status.detail, "no-state")

    def test_rejection_activates_hold(self):
        afm_session_hold.record_rejection("PyiCloudFailedLoginException: dead", "startup")
        status = afm_session_hold.check_hold()
        self.assertTrue(status.active)
        self.assertEqual(status.rejections, 1)
        self.assertAlmostEqual(status.retry_in, 900, delta=1)

    def test_escalation_15_30_60_and_capped(self):
        expected = [900, 1800, 3600, 3600, 3600]
        for i, delay in enumerate(expected, start=1):
            state = afm_session_hold.record_rejection("dead", "startup")
            self.assertEqual(state["rejections"], i)
            self.assertAlmostEqual(state["next_probe_at"] - self.now[0], delay, delta=1)
            # advance past the probe window so the next rejection is consecutive
            self.now[0] = state["next_probe_at"]

    def test_probe_window_allows_one_attempt_without_clearing_state(self):
        afm_session_hold.record_rejection("dead", "startup")
        self.now[0] += 900
        status = afm_session_hold.check_hold()
        self.assertFalse(status.active)
        self.assertEqual(status.detail, "probe-window")
        # state must survive so a repeat rejection escalates rather than resets
        self.assertIsNotNone(afm_session_hold.read_hold())

    def test_auth_sh_reinstall_clears_the_hold(self):
        afm_session_hold.record_rejection("dead", "startup")
        self.assertTrue(afm_session_hold.check_hold().active)

        self.reinstall_session_like_auth_sh()

        status = afm_session_hold.check_hold()
        self.assertFalse(status.active)
        self.assertEqual(status.detail, "session-replaced")
        # self-clearing: nothing left for the user to delete by hand
        self.assertFalse(os.path.exists(self.hold_file))

    def test_rejection_of_a_new_session_restarts_the_ladder(self):
        afm_session_hold.record_rejection("dead", "startup")
        afm_session_hold.record_rejection("dead", "startup")
        self.assertEqual(afm_session_hold.read_hold()["rejections"], 2)

        self.reinstall_session_like_auth_sh()
        state = afm_session_hold.record_rejection("dead", "startup")
        self.assertEqual(state["rejections"], 1)
        self.assertAlmostEqual(state["next_probe_at"] - self.now[0], 900, delta=1)

    def test_clear_hold_is_idempotent(self):
        afm_session_hold.record_rejection("dead", "startup")
        self.assertTrue(afm_session_hold.clear_hold())
        self.assertFalse(afm_session_hold.clear_hold())
        self.assertFalse(afm_session_hold.check_hold().active)


class TestFailOpen(HoldTestBase):
    """A bug or bad state here must never keep a working session offline."""

    def test_corrupt_json_means_no_hold(self):
        with open(self.hold_file, "w") as f:
            f.write("{not json at all")
        self.assertFalse(afm_session_hold.check_hold().active)

    def test_truncated_json_means_no_hold(self):
        with open(self.hold_file, "w") as f:
            f.write('{"rejections": 3, "next_probe_at":')
        self.assertFalse(afm_session_hold.check_hold().active)

    def test_wrong_json_type_means_no_hold(self):
        with open(self.hold_file, "w") as f:
            f.write('["not", "a", "dict"]')
        self.assertFalse(afm_session_hold.check_hold().active)

    def test_empty_file_means_no_hold(self):
        open(self.hold_file, "w").close()
        self.assertFalse(afm_session_hold.check_hold().active)

    def test_nonsense_field_types_mean_no_hold(self):
        with open(self.hold_file, "w") as f:
            f.write('{"rejections": "many", "next_probe_at": "soon"}')
        self.assertFalse(afm_session_hold.check_hold().active)

    def test_non_numeric_next_probe_means_no_hold(self):
        with open(self.hold_file, "w") as f:
            f.write('{"rejections": 2, "next_probe_at": "later", "fingerprint": "%s"}'
                    % afm_session_hold.session_fingerprint())
        status = afm_session_hold.check_hold()
        self.assertFalse(status.active)
        self.assertEqual(status.detail, "unreadable")

    @unittest.skipIf(os.geteuid() == 0, "root bypasses file permissions")
    def test_unreadable_state_file_means_no_hold(self):
        afm_session_hold.record_rejection("dead", "startup")
        os.chmod(self.hold_file, 0o000)
        try:
            self.assertFalse(afm_session_hold.check_hold().active)
        finally:
            os.chmod(self.hold_file, 0o644)

    def test_missing_session_dir_means_no_hold(self):
        afm_session_hold.record_rejection("dead", "startup")
        shutil.rmtree(self.session_dir)
        status = afm_session_hold.check_hold()
        self.assertFalse(status.active)
        self.assertEqual(status.detail, "fingerprint-unknown")

    def test_record_rejection_never_raises_when_unwritable(self):
        afm_session_hold.HOLD_STATE_PATH = "/nonexistent-dir/hold-state"
        self.assertIsNone(afm_session_hold.record_rejection("dead", "startup"))
        self.assertFalse(afm_session_hold.check_hold().active)

    def test_daemon_survives_a_broken_hold_module(self):
        """Even if check_hold() itself explodes, the daemon must still start."""
        orig = afm_session_hold.check_hold
        afm_session_hold.check_hold = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("boom"))
        try:
            _stub_pipeline.build_error = None
            _stub_pipeline.pipeline_error = None
            self._stop_after_one_cycle()
            self.run_daemon_once()
            self.assertEqual(len(_stub_pipeline.build_calls), 1,
                             "a broken hold module must not block startup")
        finally:
            afm_session_hold.check_hold = orig

    def _stop_after_one_cycle(self):
        real = _stub_pipeline.run_pipeline

        def once(api, log=print):
            afm_live_daemon._running = False
            return real(api, log=log)

        _stub_pipeline.run_pipeline = once
        self.addCleanup(setattr, _stub_pipeline, "run_pipeline", real)
        afm_live_daemon._running = True


class TestRestartStorm(HoldTestBase):
    """The regression this whole change exists to prevent."""

    def _storm(self, duration_secs, throttle=300):
        """Drive launchd-style restarts every `throttle` seconds."""
        codes = []
        elapsed = 0
        while elapsed <= duration_secs:
            codes.append(self.run_daemon_once())
            self.now[0] += throttle
            elapsed += throttle
        return codes

    def test_one_hour_of_throttled_restarts_contacts_apple_3_times_not_13(self):
        _stub_pipeline.build_error = PyiCloudFailedLoginException(
            "Invalid authentication token.")

        codes = self._storm(3600)

        restarts = len(codes)
        contacts = len(_stub_pipeline.build_calls)
        self.assertEqual(restarts, 13, "sanity: 3600s/300s throttle + t=0")
        # Without the hold, every one of those 13 restarts hits Apple. With it:
        # the initial rejection at t=0, then probes at +15m and +45m.
        self.assertEqual(contacts, 3,
                         f"expected 3 Apple contacts in an hour, got {contacts}")
        offsets = [t - 1_000_000.0 for t in _stub_pipeline.build_calls]
        self.assertEqual(offsets, [0, 900, 2700])

    def test_held_restarts_exit_cleanly_and_never_take_the_lock(self):
        _stub_pipeline.build_error = PyiCloudFailedLoginException("dead")

        codes = self._storm(3600)

        # Only the 3 restarts that actually contact Apple take afm_live.lock.
        self.assertEqual(len(self.lock_calls), 3)
        # A suppressed restart is a deliberate no-op, not a crash.
        self.assertEqual(codes.count(0), 10)
        self.assertEqual(codes.count(1), 3)

    def test_24h_projection_is_about_24_attempts_not_288(self):
        _stub_pipeline.build_error = PyiCloudFailedLoginException("dead")

        self._storm(24 * 3600)

        restarts_without_hold = 24 * 3600 // 300 + 1  # 289
        contacts = len(_stub_pipeline.build_calls)
        self.assertEqual(restarts_without_hold, 289)
        self.assertLessEqual(contacts, 26)
        self.assertGreaterEqual(contacts, 22)

    def test_auth_sh_during_the_storm_restores_tracking_on_next_restart(self):
        _stub_pipeline.build_error = PyiCloudFailedLoginException("dead")
        self._storm(1800)                       # session dies, hold engages
        contacts_before = len(_stub_pipeline.build_calls)

        # Operator runs `bash util/auth.sh` on the Mac.
        self.reinstall_session_like_auth_sh()
        _stub_pipeline.build_error = None

        # Next launchd restart — no manual daemon restart, no file to delete.
        afm_live_daemon._running = True
        real = _stub_pipeline.run_pipeline

        def once(api, log=print):
            afm_live_daemon._running = False
            return real(api, log=log)

        _stub_pipeline.run_pipeline = once
        try:
            self.run_daemon_once()
        finally:
            _stub_pipeline.run_pipeline = real

        self.assertEqual(len(_stub_pipeline.build_calls), contacts_before + 1,
                         "the post-auth.sh restart must reconnect")
        self.assertFalse(os.path.exists(self.hold_file),
                         "hold must be self-clearing after auth.sh")


class TestDaemonIntegration(HoldTestBase):

    def _one_cycle_then_stop(self):
        real = _stub_pipeline.run_pipeline

        def once(api, log=print):
            afm_live_daemon._running = False
            return real(api, log=log)

        _stub_pipeline.run_pipeline = once
        self.addCleanup(setattr, _stub_pipeline, "run_pipeline", real)
        afm_live_daemon._running = True

    def test_healthy_startup_clears_a_stale_hold(self):
        afm_session_hold.record_rejection("dead", "startup")
        self.now[0] += 3600            # past the probe window
        self._one_cycle_then_stop()

        self.run_daemon_once()

        self.assertEqual(len(_stub_pipeline.build_calls), 1)
        self.assertFalse(os.path.exists(self.hold_file))

    def test_mid_cycle_session_death_arms_the_hold(self):
        _stub_pipeline.pipeline_error = PyiCloud2FARequiredException("untrusted")
        afm_live_daemon._running = True

        code = self.run_daemon_once()

        self.assertEqual(code, 1)
        state = afm_session_hold.read_hold()
        self.assertIsNotNone(state)
        self.assertEqual(state["phase"], "cycle")

        # And the next restart is suppressed.
        before = len(_stub_pipeline.build_calls)
        self.now[0] += 300
        self.assertEqual(self.run_daemon_once(), 0)
        self.assertEqual(len(_stub_pipeline.build_calls), before)

    def test_transient_errors_do_not_arm_the_hold(self):
        """5xx / 429 / network keep the existing 5-minute cadence, untouched."""
        for err in (PyiCloudAPIResponseException("server error", code=503),
                    PyiCloudAPIResponseException("rate limited", code=429),
                    PyiCloudAPIResponseException("network", code=None)):
            with self.subTest(err=err):
                afm_session_hold.clear_hold()
                _stub_pipeline.build_error = err
                self.run_daemon_once()
                self.assertIsNone(afm_session_hold.read_hold(),
                                  "transient failure must not create a hold")
                self.assertFalse(afm_session_hold.check_hold().active)

    def test_startup_alert_semantics_are_unchanged(self):
        _stub_pipeline.build_error = PyiCloudFailedLoginException("dead")
        self.run_daemon_once()
        self.assertEqual(self.alerts,
                         ["iCloud session needs re-auth (run util/auth.sh)."])

    def test_untrusted_session_alert_semantics_are_unchanged(self):
        _stub_pipeline.build_error = PyiCloud2FARequiredException("untrusted")
        self.run_daemon_once()
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("Session untrusted", self.alerts[0])

    def test_suppressed_restart_sends_no_new_alert(self):
        """The hold must not turn into an alert storm of its own."""
        _stub_pipeline.build_error = PyiCloudFailedLoginException("dead")
        self.run_daemon_once()
        self.assertEqual(len(self.alerts), 1)

        for _ in range(5):
            self.now[0] += 300
            self.run_daemon_once()
        self.assertEqual(len(self.alerts), 1, "held restarts must stay silent")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestCannotStrandAWorkingSession(HoldTestBase):
    """The hold must never be able to keep a working session offline. These are
    the 'won't break stable functioning' guarantees."""

    def test_self_healing_session_recovers_at_the_first_probe(self):
        """Apple rejects, then starts accepting again WITHOUT the session dir
        changing (an Apple-side blip rather than a truly dead token). The hold must
        not strand it: recovery is delayed, bounded, and automatic."""
        _stub_pipeline.build_error = PyiCloudFailedLoginException("dead")
        self.run_daemon_once()                       # t=0: rejected, hold armed

        _stub_pipeline.build_error = None            # Apple starts working again
        contacts_at_rejection = len(_stub_pipeline.build_calls)

        # Restarts during the hold window do nothing...
        for _ in range(2):
            self.now[0] += 300
            self.run_daemon_once()
        self.assertEqual(len(_stub_pipeline.build_calls), contacts_at_rejection,
                         "held window must stay quiet")

        # ...but the probe at +15m reconnects, with no human involvement.
        self.now[0] = 1_000_000.0 + 900
        self._one_cycle_then_stop()
        self.run_daemon_once()

        self.assertEqual(len(_stub_pipeline.build_calls), contacts_at_rejection + 1)
        self.assertFalse(os.path.exists(self.hold_file))
        # Bounded cost of a false positive: 15 minutes, self-healing.

    def test_healthy_daemon_never_creates_the_state_file(self):
        """Zero footprint when nothing is wrong."""
        self._one_cycle_then_stop()
        self.run_daemon_once()
        self.assertFalse(os.path.exists(self.hold_file))
        self.assertEqual(len(_stub_pipeline.build_calls), 1)

    def test_machine_with_no_session_dir_behaves_exactly_as_before(self):
        """No ~/.pyicloud (fresh machine, first run) => no fingerprint => the
        hold can never engage, so behavior is byte-for-byte the old behavior."""
        shutil.rmtree(self.session_dir)
        _stub_pipeline.build_error = PyiCloudFailedLoginException("dead")

        for _ in range(6):
            self.run_daemon_once()
            self.now[0] += 300

        # Every restart still contacts Apple — the hold never suppresses a
        # machine whose session state it cannot fingerprint.
        self.assertEqual(len(_stub_pipeline.build_calls), 6)

    def test_cp_r_session_restore_also_clears_the_hold(self):
        """deploy_timlaing.sh and its rollback install the session with `cp -r`,
        not an atomic rename. That path must self-clear too."""
        afm_session_hold.record_rejection("dead", "startup")
        self.assertTrue(afm_session_hold.check_hold().active)

        backup = self.session_dir + "-bak"
        shutil.copytree(self.session_dir, backup)
        with open(os.path.join(backup, "abc123.session"), "w") as f:
            f.write("restored-by-rollback")
        shutil.rmtree(self.session_dir)
        shutil.copytree(backup, self.session_dir)     # cp -r

        status = afm_session_hold.check_hold()
        self.assertFalse(status.active)
        self.assertEqual(status.detail, "session-replaced")

    def test_hold_never_outlives_its_own_probe_schedule(self):
        """No reachable state can suppress Apple for longer than the cap."""
        state = afm_session_hold.record_rejection("dead", "startup")
        for _ in range(50):
            state = afm_session_hold.record_rejection(
                "dead", "startup", now=state["next_probe_at"])
            gap = state["next_probe_at"] - state["last_rejected_at"]
            self.assertLessEqual(gap, max(afm_session_hold.PROBE_BACKOFF_SECS))

    def _one_cycle_then_stop(self):
        real = _stub_pipeline.run_pipeline

        def once(api, log=print):
            afm_live_daemon._running = False
            return real(api, log=log)

        _stub_pipeline.run_pipeline = once
        self.addCleanup(setattr, _stub_pipeline, "run_pipeline", real)
        afm_live_daemon._running = True
