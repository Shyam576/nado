"""tests/test_devops_restart.py — gated deployment restart, without a real K8s API.

Covers the confirmation gate directly, plus its intent.py entry point —
mirrors tests/test_command_confirmation.py's shape for the run_command gate.
"""

import pytest

import command_confirmation
from modules import devops, intent


@pytest.fixture(autouse=True)
def _clear_pending():
    devops._pending_restarts.clear()
    command_confirmation._pending.clear()  # route() checks this gate first
    yield
    devops._pending_restarts.clear()
    command_confirmation._pending.clear()


def test_request_restart_requires_a_deployment():
    assert "Usage" in devops.request_restart("owner", "")


def test_request_restart_stages_without_executing(monkeypatch):
    called = False

    def _fail(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(devops, "_restart_deployment", _fail)

    prompt = devops.request_restart("owner", "auth-service")
    assert "Restart deployment 'auth-service'" in prompt
    assert "dev" in prompt  # default K8S_NAMESPACE
    assert not called


def test_request_restart_uses_namespace_override():
    prompt = devops.request_restart("owner", "auth-service", "prod")
    assert "prod" in prompt


def test_check_restart_confirmation_confirms_and_executes(monkeypatch):
    monkeypatch.setattr(devops, "_restart_deployment", lambda ns, dep: f"restarted {dep} in {ns}")

    devops.request_restart("owner", "auth-service")
    result = devops.check_restart_confirmation("owner", "yes")
    assert result == "restarted auth-service in dev"

    # Consumed — a second "yes" has nothing to resolve
    assert devops.check_restart_confirmation("owner", "yes") is None


def test_check_restart_confirmation_cancels():
    devops.request_restart("owner", "auth-service")
    assert devops.check_restart_confirmation("owner", "no") == "Cancelled — nothing was restarted."


def test_check_restart_confirmation_expires(monkeypatch):
    devops.request_restart("owner", "auth-service")
    namespace, deployment, _ = devops._pending_restarts["owner"]
    devops._pending_restarts["owner"] = (namespace, deployment, 0.0)

    result = devops.check_restart_confirmation("owner", "yes")
    assert "expired" in result


def test_check_restart_confirmation_noop_without_pending():
    assert devops.check_restart_confirmation("owner", "yes") is None


def test_restart_deployment_fails_closed_without_config(monkeypatch):
    monkeypatch.setattr(devops, "K8S_API_URL", "")
    monkeypatch.setattr(devops, "K8S_SERVICE_ACCOUNT_TOKEN", "")
    result = devops._restart_deployment("dev", "auth-service")
    assert "not configured" in result


def test_restart_deployment_sends_typed_patch_request(monkeypatch):
    monkeypatch.setattr(devops, "K8S_API_URL", "https://k8s.example.com:6443")
    monkeypatch.setattr(devops, "K8S_SERVICE_ACCOUNT_TOKEN", "fake-token")

    captured = {}

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(request, timeout, context):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.headers)
        captured["body"] = request.data
        return _FakeResponse()

    monkeypatch.setattr(devops.urllib.request, "urlopen", _fake_urlopen)

    result = devops._restart_deployment("prod", "auth-service")
    assert "Restart triggered for 'auth-service' in namespace 'prod'" in result
    assert captured["method"] == "PATCH"
    assert "prod" in captured["url"]
    assert "auth-service" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer fake-token"
    assert b"restartedAt" in captured["body"]


def test_restart_deployment_handles_404(monkeypatch):
    import urllib.error
    import io

    monkeypatch.setattr(devops, "K8S_API_URL", "https://k8s.example.com:6443")
    monkeypatch.setattr(devops, "K8S_SERVICE_ACCOUNT_TOKEN", "fake-token")

    def _fake_urlopen(request, timeout, context):
        raise urllib.error.HTTPError(
            request.full_url, 404, "Not Found", {}, io.BytesIO(b'{"message": "not found"}')
        )

    monkeypatch.setattr(devops.urllib.request, "urlopen", _fake_urlopen)

    result = devops._restart_deployment("dev", "nonexistent")
    assert "not found" in result.lower()


def test_dispatch_restart_deployment_stages_via_intent(monkeypatch):
    called = False

    def _fail(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(devops, "_restart_deployment", _fail)

    reply = intent._dispatch_restart_deployment("owner", {"deployment": "auth-service", "namespace": ""})
    assert not called
    assert "Restart deployment 'auth-service'" in reply.text


def test_restart_confirmation_reachable_via_intent_route(monkeypatch):
    monkeypatch.setattr(devops, "_restart_deployment", lambda ns, dep: f"restarted {dep}")

    intent._dispatch_restart_deployment("owner", {"deployment": "auth-service", "namespace": ""})
    reply = intent.route("owner", "yes")
    assert reply is not None
    assert reply.text == "restarted auth-service"
