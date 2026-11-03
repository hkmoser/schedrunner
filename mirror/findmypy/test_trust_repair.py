#!/usr/bin/env python3
"""
Tests for afm_trust_repair and its integration into afm_live_daemon.

Runs anywhere: pyicloud, afm_pipeline and the HTTP call are stubbed, so no Apple
credentials and no network. State files are redirected into a temp dir, so the
real ~/.pyicloud and ~/.pyicloud-trust-repair-last are never touched.

    python3 -m unittest test_trust_repair -v
"""
import json
import os
import shutil
import sys
import tempfile
import types
import unittest


class PyiCloudException(Exception):
    pass


class PyiCloudFailedLoginException(PyiCloudException):
    pass


class PyiCloudAuthRequiredException(PyiCloudException):
    pass


class PyiCloud2FARequiredException(PyiCloudException):
    pass


class PyiCloudAPIResponseException(PyiCloudException):
    def __init__(self, reason="", code=None):
        super().__init__(reason)
        self.code = code


class PyiCloudServiceNotActivatedException(PyiCloudException):
    pass


class PyiCloud2SARequiredException(PyiCloudException):
    pass


class PyiCloudConnectionException(PyiCloudException):
    pass


class PyiCloudAcceptTermsException(PyiCloudException):
    pass


_exc_mod = types.ModuleType("pyicloud.exceptions")
for _name, _obj in list(globals().items()):
    if _name.startswith("PyiCloud"):
        setattr(_exc_mod, _name, _obj)

_session_mod = types.ModuleType("pyicloud.session")
_session_mod.HEADER_DATA = {
    "X-Apple-ID-Account-Country": "account_country",
    "X-Apple-ID-Session-Id": "session_id",
    "X-Apple-Session-Token": "session_token",
    "X-Apple-TwoSV-Trust-Token": "trust_token",
    "scnt": "scnt",
}


class _Jar(list):
    def __init__(self):
        super().__init__()
        self.saved = 0

    def save(self, ignore_discard=False):
        self.saved += 1


class _StubSession:
    def __init__(self, data, path):
        self.data = data
        self.cookies = _Jar()
        self.session_path = path

    def request(self, *args, **kwargs):
        raise AssertionError("the pyicloud wrapper must be bypassed")


class StubService:
    """Stands in for PyiCloudService: loads the session file, authenticates nothing."""

    accountlogin_outcome = "ok"
    split_dicts = False

    def __init__(self, apple_id, password=None, cookie_directory=None):
        self.account_name = apple_id
        self._cookie_directory = cookie_directory
        self._auth_endpoint = "https://idmsa.apple.com/appleauth/auth"
        clean = "".join(c for c in apple_id if c.isalnum())
        path = os.path.join(cookie_directory, clean + ".session")
        data = {}
        if os.path.exists(path):
            with open(path) as handle:
                data = json.load(handle)
        self.session = _StubSession(data, path)
        if type(self).split_dicts:
            self.session_data = dict(data)
            self.session_data["extra_only_here"] = "E"
        self.accountlogin_calls = 0
        self.authenticate()

    def authenticate(self, force_refresh=False, service=None):
        raise AssertionError("authenticate() must be overridden away")

    def _get_auth_headers(self, overrides=None):
        return {"scnt": "s", "X-Apple-ID-Session-Id": "x"}

    def _authenticate_with_token(self):
        self.accountlogin_calls += 1
        if type(self).accountlogin_outcome == "ok":
            return
        raise PyiCloudFailedLoginException("Invalid authentication token.")


_pyicloud_mod = types.ModuleType("pyicloud")
_pyicloud_mod.PyiCloudService = StubService
_pyicloud_mod.exceptions = _exc_mod
_pyicloud_mod.session = _session_mod
sys.modules["pyicloud"] = _pyicloud_mod
sys.modules["pyicloud.exceptions"] = _exc_mod
sys.modules["pyicloud.session"] = _session_mod


class _Response:
    def __init__(self, status=204, headers=None, text=""):
        self.status_code = status
        self.reason = "No Content" if status == 204 else "Error"
        self.headers = headers if headers is not None else {}
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 300


TRUST_HEADERS = {
    "Date": "Tue, 03 Nov 2026 05:17:19 GMT",
    "Server": "Apple",
    "X-Apple-TwoSV-Trust-Token": "T" * 208,
    "X-Apple-Session-Token": "S" * 300,
    "X-Apple-ID-Session-Id": "SID",
    "scnt": "SCNT",
    "X-Apple-ID-Account-Country": "USA",
}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
import afm_trust_repair  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="afm-repair-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cookie_dir = os.path.join(self.tmp, ".pyicloud")
        os.makedirs(self.cookie_dir)
        self.session_file = os.path.join(self.cookie_dir, "joejoemosercom.session")
        with open(self.session_file, "w") as handle:
            json.dump({"session_token": "OLD", "session_id": "SID0"}, handle)

        self._orig_cookie = afm_trust_repair.COOKIE_DIR
        self._orig_state = afm_trust_repair.COOLDOWN_PATH
        afm_trust_repair.COOKIE_DIR = self.cookie_dir
        afm_trust_repair.COOLDOWN_PATH = os.path.join(self.tmp, "repair-last")
        self.addCleanup(setattr, afm_trust_repair, "COOKIE_DIR", self._orig_cookie)
        self.addCleanup(setattr, afm_trust_repair, "COOLDOWN_PATH", self._orig_state)

        StubService.accountlogin_outcome = "ok"
        StubService.split_dicts = False
        self.addCleanup(setattr, StubService, "accountlogin_outcome", "ok")
        self.addCleanup(setattr, StubService, "split_dicts", False)

        self.requests_calls = []
        self._orig_request = requests.Session.request
        self.addCleanup(setattr, requests.Session, "request", self._orig_request)
        self.set_response(_Response(204, dict(TRUST_HEADERS)))

        self.logged = []

    def set_response(self, response):
        calls = self.requests_calls

        def fake(session, method, url, **kwargs):
            calls.append((method, url))
            if isinstance(response, Exception):
                raise response
            return response

        requests.Session.request = fake

    def log(self, msg):
        self.logged.append(str(msg))

    def log_text(self):
        return "\n".join(self.logged)

    def stored(self):
        with open(self.session_file) as handle:
            return json.load(handle)


class TestRepairSuccess(_Base):
    def test_returns_true_and_persists_every_mapped_header(self):
        self.assertTrue(afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log))
        data = self.stored()
        self.assertEqual(data["trust_token"], "T" * 208)
        self.assertEqual(data["session_token"], "S" * 300)
        self.assertEqual(data["session_id"], "SID")
        self.assertEqual(data["account_country"], "USA")
        self.assertEqual(data["scnt"], "SCNT")

    def test_replaces_the_stale_session_token(self):
        self.assertEqual(self.stored()["session_token"], "OLD")
        afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log)
        self.assertNotEqual(self.stored()["session_token"], "OLD")

    def test_keeps_keys_the_response_did_not_carry(self):
        with open(self.session_file, "w") as handle:
            json.dump({"session_token": "OLD", "client_id": "KEEPME"}, handle)
        afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log)
        self.assertEqual(self.stored()["client_id"], "KEEPME")

    def test_hits_the_trust_endpoint_once(self):
        afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log)
        self.assertEqual(len(self.requests_calls), 1)
        method, url = self.requests_calls[0]
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith("/appleauth/auth/2sv/trust"))

    def test_clears_the_cooldown_so_a_later_failure_can_retry(self):
        afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log)
        self.assertEqual(afm_trust_repair.cooldown_remaining(), 0)

    def test_saves_the_cookie_jar(self):
        service = afm_trust_repair.build_probe_service(self.cookie_dir)
        afm_trust_repair.attempt_repair(service, cookie_dir=self.cookie_dir, log=self.log)
        self.assertEqual(service.session.cookies.saved, 1)

    def test_merges_both_session_dicts_when_the_build_splits_them(self):
        StubService.split_dicts = True
        service = afm_trust_repair.build_probe_service(self.cookie_dir)
        self.assertTrue(
            afm_trust_repair.attempt_repair(service, cookie_dir=self.cookie_dir, log=self.log)
        )
        data = self.stored()
        self.assertEqual(data["extra_only_here"], "E")
        self.assertEqual(data["trust_token"], "T" * 208)
        self.assertEqual(service.session.data["trust_token"], "T" * 208)
        self.assertEqual(service.session_data["trust_token"], "T" * 208)


class TestRepairFailure(_Base):
    def test_error_response_returns_false_and_writes_nothing(self):
        self.set_response(_Response(412, {"content-type": "application/json"},
                                    '{"serviceErrors":[{"code":"-21669"}]}'))
        self.assertFalse(afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log))
        self.assertEqual(self.stored()["session_token"], "OLD")
        self.assertIn("-21669", self.log_text())

    def test_200_without_a_trust_token_returns_false(self):
        self.set_response(_Response(200, {"content-type": "text/html"}, "<html/>"))
        self.assertFalse(afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log))
        self.assertEqual(self.stored()["session_token"], "OLD")

    def test_network_exception_is_swallowed(self):
        self.set_response(RuntimeError("connection reset"))
        self.assertFalse(afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log))
        self.assertIn("connection reset", self.log_text())

    def test_accountlogin_still_failing_returns_false(self):
        StubService.accountlogin_outcome = "fail"
        self.assertFalse(afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log))
        self.assertIn("still failing after trust", self.log_text())

    def test_persisted_tokens_survive_a_failed_accountlogin(self):
        StubService.accountlogin_outcome = "fail"
        afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log)
        self.assertEqual(self.stored()["trust_token"], "T" * 208)

    def test_empty_session_dir_returns_false_without_calling_apple(self):
        os.remove(self.session_file)
        self.assertFalse(afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log))
        self.assertEqual(self.requests_calls, [])
        self.assertIn("no session data", self.log_text())


class TestCooldown(_Base):
    def test_second_attempt_inside_the_window_is_skipped(self):
        StubService.accountlogin_outcome = "fail"
        afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log)
        calls_after_first = len(self.requests_calls)
        self.assertFalse(afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log))
        self.assertEqual(len(self.requests_calls), calls_after_first)
        self.assertIn("cooldown", self.log_text())

    def test_force_bypasses_the_cooldown(self):
        StubService.accountlogin_outcome = "fail"
        afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log)
        StubService.accountlogin_outcome = "ok"
        self.assertTrue(
            afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log, force=True)
        )

    def test_window_expiry_allows_another_attempt(self):
        StubService.accountlogin_outcome = "fail"
        afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log)
        with open(afm_trust_repair.COOLDOWN_PATH, "w") as handle:
            handle.write(str(__import__("time").time() - afm_trust_repair.REPAIR_COOLDOWN_SECS - 1))
        StubService.accountlogin_outcome = "ok"
        self.assertTrue(afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log))

    def test_unreadable_state_file_does_not_block_a_repair(self):
        with open(afm_trust_repair.COOLDOWN_PATH, "w") as handle:
            handle.write("not-a-number")
        self.assertEqual(afm_trust_repair.cooldown_remaining(), 0)
        self.assertTrue(afm_trust_repair.attempt_repair(cookie_dir=self.cookie_dir, log=self.log))


class TestHelpers(_Base):
    def test_session_dicts_orders_richest_first_and_dedupes(self):
        StubService.split_dicts = True
        service = afm_trust_repair.build_probe_service(self.cookie_dir)
        dicts = afm_trust_repair.session_dicts(service)
        self.assertEqual(len(dicts), 2)
        self.assertGreaterEqual(len(dicts[0]), len(dicts[1]))
        self.assertIn("extra_only_here", dicts[0])

    def test_session_dicts_dedupes_when_both_attrs_are_the_same_object(self):
        service = afm_trust_repair.build_probe_service(self.cookie_dir)
        service.session_data = service.session.data
        self.assertEqual(len(afm_trust_repair.session_dicts(service)), 1)

    def test_probe_service_never_authenticates(self):
        service = afm_trust_repair.build_probe_service(self.cookie_dir)
        self.assertEqual(service.accountlogin_calls, 0)

    def test_header_data_prefers_pyicloud(self):
        self.assertEqual(afm_trust_repair.header_data(), _session_mod.HEADER_DATA)

    def test_session_file_path_falls_back_to_the_glob(self):
        service = afm_trust_repair.build_probe_service(self.cookie_dir)
        del service.session.session_path
        self.assertEqual(
            afm_trust_repair.session_file_path(service, self.cookie_dir), self.session_file
        )

    def test_trust_token_header_is_case_insensitive(self):
        resp = _Response(204, {"x-apple-twosv-trust-token": "T"})
        self.assertEqual(afm_trust_repair.trust_token_header(resp),
                         "x-apple-twosv-trust-token")


class TestDaemonIntegration(_Base):
    """_build_api_repairing() must retry after a repair and stay silent otherwise."""

    def setUp(self):
        super().setUp()

        class _StubPipeline(types.ModuleType):
            def __init__(self):
                super().__init__("afm_pipeline")
                self.build_calls = 0
                self.build_error = None

            def build_api(self):
                self.build_calls += 1
                if self.build_error is not None and self.build_calls == 1:
                    raise self.build_error
                return types.SimpleNamespace(session=types.SimpleNamespace(cookies=[]))

            def install_accountlogin_logging(self, api, log=print):
                pass

            def cleanup_old_csvs(self, log=print):
                return (0, 0)

        self.pipeline = _StubPipeline()
        sys.modules["afm_pipeline"] = self.pipeline
        self.addCleanup(sys.modules.pop, "afm_pipeline", None)

        for name in ("afm_session_hold", "afm_live_daemon"):
            sys.modules.pop(name, None)
            self.addCleanup(sys.modules.pop, name, None)
        os.environ["AFM_HOLD_STATE"] = os.path.join(self.tmp, "hold")
        os.environ["AFM_COOKIE_DIR"] = self.cookie_dir
        self.addCleanup(os.environ.pop, "AFM_HOLD_STATE", None)
        self.addCleanup(os.environ.pop, "AFM_COOKIE_DIR", None)

        import afm_live_daemon
        self.daemon = afm_live_daemon
        self.daemon.afm_trust_repair = afm_trust_repair
        self.daemon.log = self.log

    def test_successful_repair_rebuilds_the_session(self):
        self.pipeline.build_error = PyiCloudFailedLoginException("Invalid authentication token.")
        api = self.daemon._build_api_repairing()
        self.assertIsNotNone(api)
        self.assertEqual(self.pipeline.build_calls, 2)
        self.assertIn("attempting trust replay", self.log_text())

    def test_failed_repair_reraises_the_original_exception(self):
        self.pipeline.build_error = PyiCloudFailedLoginException("Invalid authentication token.")
        self.set_response(_Response(412, {}, "nope"))
        with self.assertRaises(PyiCloudFailedLoginException):
            self.daemon._build_api_repairing()
        self.assertEqual(self.pipeline.build_calls, 1)
        self.assertIn("did not recover", self.log_text())

    def test_healthy_startup_never_touches_the_trust_endpoint(self):
        self.daemon._build_api_repairing()
        self.assertEqual(self.requests_calls, [])
        self.assertEqual(self.pipeline.build_calls, 1)

    def test_a_2fa_required_startup_is_also_repair_eligible(self):
        self.pipeline.build_error = PyiCloud2FARequiredException("is_trusted_session=False")
        self.assertIsNotNone(self.daemon._build_api_repairing())
        self.assertEqual(self.pipeline.build_calls, 2)


if __name__ == "__main__":
    unittest.main()
