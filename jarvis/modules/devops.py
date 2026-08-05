"""
modules/devops.py — Kubernetes health, logs, and (gated) restarts.

/status and /logs deliberately do NOT talk to the Kubernetes API directly
(no kubeconfig, no VPN dependency on the bot's host) — instead they query
Prometheus (via kube-state-metrics) and Loki through Grafana's own API,
authenticated with a Grafana service account Bearer token. This matches the
finance.py pattern: plain HTTP GET, parse JSON, format for Telegram.

request_restart()/_restart_deployment() are the one exception: restarting a
deployment mutates cluster state, which Grafana has no way to proxy (it only
reads metrics/logs) — so that path talks to the Kubernetes API directly via
a typed PATCH request (never a shell/kubectl call), gated behind an explicit
confirmation, and requires the bot's host to actually reach the API server.
See config.py's K8S_API_URL/K8S_SERVICE_ACCOUNT_TOKEN for what that needs.

Requires (see config.py / .env):
  GRAFANA_URL, GRAFANA_API_TOKEN, PROMETHEUS_DATASOURCE_UID, LOKI_DATASOURCE_UID
  K8S_API_URL, K8S_SERVICE_ACCOUNT_TOKEN (restart only)
"""

import collections
import datetime
import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

import memory
from config import (
    GRAFANA_API_TOKEN,
    GRAFANA_URL,
    K8S_API_URL,
    K8S_CA_CERT_PATH,
    K8S_NAMESPACE,
    K8S_SERVICE_ACCOUNT_TOKEN,
    LOKI_DATASOURCE_UID,
    PROMETHEUS_DATASOURCE_UID,
)
from modules import tasks

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 15
_LOG_LOOKBACK_SECONDS = 24 * 3600
_MAX_LINE_CHARS = 300  # some app logs (e.g. raw SQL dumps) are absurdly long on their own
_MAX_REPLY_CHARS = 3500  # Telegram caps messages at 4096; leave headroom

# Confirmation gate for restart requests — separate from
# command_confirmation.py (which is specifically about shell commands) since
# this confirms a typed API call instead. Same shape (park, confirm/cancel,
# timeout) deliberately duplicated rather than generalizing that module for
# a single additional caller.
_RESTART_CONFIRM_WINDOW_SECONDS = 60
_pending_restarts: dict[str, tuple[str, str, float]] = {}  # chat_id -> (namespace, deployment, queued_at)
_RESTART_CONFIRM_RE = re.compile(r"^(?:yes|y|confirm|do it|restart it)[.!]?$", re.IGNORECASE)
_RESTART_CANCEL_RE = re.compile(r"^(?:no|n|cancel|stop|abort)[.!]?$", re.IGNORECASE)

# Re-alert (and consider a fresh task) for a still-unhealthy deployment at
# most this often — avoids re-notifying and re-tasking on every poll while
# an issue is already known and presumably being worked.
_UNHEALTHY_REALERT_SECONDS = 6 * 3600

# Strips the ReplicaSet-hash and pod-suffix segments off a pod name, e.g.
# "admin-auth-service-85f87894c8-5bwft" -> "admin-auth-service"
_POD_SUFFIX_RE = re.compile(r"-[a-f0-9]{7,10}-[a-z0-9]{5}$")

# Strips ANSI colour escape codes (e.g. from NestJS's coloured logger output)
# so log lines are readable as plain Telegram text.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _grafana_get(path: str, params: dict) -> Optional[dict]:
    """GET a Grafana API path with Bearer auth, returning parsed JSON.

    Args:
        path: Path relative to GRAFANA_URL, e.g. "/api/datasources/proxy/uid/xyz/api/v1/query".
        params: Query string parameters.

    Returns:
        Parsed JSON response, or None if the request failed or config is missing.
    """
    if not GRAFANA_URL or not GRAFANA_API_TOKEN:
        logger.error("GRAFANA_URL / GRAFANA_API_TOKEN not configured.")
        return None

    url = f"{GRAFANA_URL}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {GRAFANA_API_TOKEN}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.error("Grafana request failed (%s): %s", path, exc)
        return None


def _deployment_name(pod_name: str) -> str:
    """Best-effort strip of the ReplicaSet-hash/pod-suffix from a pod name."""
    return _POD_SUFFIX_RE.sub("", pod_name)


def _pod_status_by_deployment(namespace: str) -> Optional[dict[str, "collections.Counter"]]:
    """Query Prometheus for pod phases in `namespace`, grouped by deployment.

    Returns:
        A dict of deployment name -> Counter({phase: count}), or None if the
        query couldn't be made (missing config or request failure). An empty
        dict means the query succeeded but found no pods.
    """
    if not PROMETHEUS_DATASOURCE_UID:
        return None

    data = _grafana_get(
        f"/api/datasources/proxy/uid/{PROMETHEUS_DATASOURCE_UID}/api/v1/query",
        {"query": f'kube_pod_status_phase{{namespace="{namespace}"}} == 1'},
    )
    if data is None or data.get("status") != "success":
        return None

    by_deployment: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for row in data["data"]["result"]:
        metric = row["metric"]
        pod = metric.get("pod", "unknown")
        phase = metric.get("phase", "Unknown")
        by_deployment[_deployment_name(pod)][phase] += 1
    return by_deployment


def k8s_health(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Summarise pod status for the configured namespace, grouped by deployment.

    Args:
        chat_id: Unused — kept for a consistent command-handler signature.
        args: Optional namespace override as args[0]; defaults to K8S_NAMESPACE.

    Returns:
        A plain-text summary flagging any deployment with non-Running pods.
    """
    if not PROMETHEUS_DATASOURCE_UID:
        return "PROMETHEUS_DATASOURCE_UID is not configured — see .env."

    namespace = (args or [None])[0] or K8S_NAMESPACE
    by_deployment = _pod_status_by_deployment(namespace)
    if by_deployment is None:
        return f"Couldn't reach Prometheus for namespace '{namespace}' — try again shortly."
    if not by_deployment:
        return f"No pod status data found for namespace '{namespace}'."

    lines = [f"Namespace: {namespace}"]
    for deployment in sorted(by_deployment):
        phases = by_deployment[deployment]
        total = sum(phases.values())
        unhealthy = {p: c for p, c in phases.items() if p != "Running"}
        if unhealthy:
            detail = ", ".join(f"{count} {phase}" for phase, count in unhealthy.items())
            running = phases.get("Running", 0)
            lines.append(f"⚠ {deployment}: {detail}" + (f", {running} Running" if running else ""))
        else:
            lines.append(f"✓ {deployment}: {total} Running")

    return "\n".join(lines)


def check_k8s_health(chat_id: str) -> list[str]:
    """Poll pod health and surface newly (or still) unhealthy deployments.

    Unlike k8s_health() (the pull-based /status command), this is meant to
    be polled periodically by a background job. Each unhealthy deployment
    both sends an alert AND gets a pending task created for it (deduped so
    a repeat poll while the same issue persists doesn't spam a fresh task
    every cycle) — a chat notification alone can scroll past; a task
    survives into /today and the daily digest.

    Args:
        chat_id: The owner to create investigation tasks for.

    Returns:
        A list of alert message strings for any unhealthy deployment
        (usually empty).
    """
    namespace = K8S_NAMESPACE
    by_deployment = _pod_status_by_deployment(namespace)
    if not by_deployment:
        return []

    alerts: list[str] = []
    now = time.time()
    for deployment in sorted(by_deployment):
        phases = by_deployment[deployment]
        unhealthy = {p: c for p, c in phases.items() if p != "Running"}
        if not unhealthy:
            continue

        state_key = f"k8s_unhealthy_alerted_{namespace}_{deployment}"
        last_alerted = memory.get_preference(state_key)
        if last_alerted and now - float(last_alerted) < _UNHEALTHY_REALERT_SECONDS:
            continue
        memory.set_preference(state_key, now)

        detail = ", ".join(f"{count} {phase}" for phase, count in unhealthy.items())
        alerts.append(f"K8s: '{deployment}' in {namespace} has {detail}.")

        if not tasks.find_pending_tasks_matching(chat_id, deployment):
            tasks.add_task(chat_id, f"Investigate {deployment} ({namespace}): {detail}".split())

    return alerts


def _fetch_log_lines(service: str, namespace: str) -> tuple[Optional[list[str]], Optional[str]]:
    """Fetch and clean up recent log lines for a service — shared by tail_logs and summarize_logs.

    Returns:
        (lines, None) on success (lines may be empty), or (None, error_message) on failure.
    """
    if not LOKI_DATASOURCE_UID:
        return None, "LOKI_DATASOURCE_UID is not configured — see .env."

    logql = f'{{namespace="{namespace}", pod=~"{service}-.*"}}'
    now_ns = int(time.time() * 1e9)
    start_ns = now_ns - int(_LOG_LOOKBACK_SECONDS * 1e9)
    data = _grafana_get(
        f"/api/datasources/proxy/uid/{LOKI_DATASOURCE_UID}/loki/api/v1/query_range",
        {
            "query": logql,
            "limit": "30",
            "direction": "backward",
            "start": str(start_ns),
            "end": str(now_ns),
        },
    )
    if data is None or data.get("status") != "success":
        return None, f"Couldn't reach Loki for '{service}' in namespace '{namespace}' — try again shortly."

    streams = data["data"]["result"]
    lines = []
    for stream in streams:
        pod = stream["stream"].get("pod", service)
        for _timestamp, line in stream["values"]:
            clean_line = _ANSI_ESCAPE_RE.sub("", line).rstrip("\n")
            if len(clean_line) > _MAX_LINE_CHARS:
                clean_line = clean_line[:_MAX_LINE_CHARS] + "…"
            lines.append(f"[{pod}] {clean_line}")

    return lines[-30:], None


def tail_logs(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Return the most recent log lines for pods matching a service name prefix.

    Args:
        chat_id: Unused — kept for a consistent command-handler signature.
        args: [service_name] required; optional args[1] as a namespace override.

    Returns:
        The last ~30 log lines, or a usage/error message.
    """
    args = args or []
    if not args:
        return "Usage: /logs <service-name>"

    service = args[0]
    namespace = args[1] if len(args) > 1 else K8S_NAMESPACE

    lines, error = _fetch_log_lines(service, namespace)
    if error:
        return error
    if not lines:
        return f"No logs found for pods matching '{service}-*' in namespace '{namespace}'."

    return _fit_to_reply_limit(lines)


_LOG_SUMMARY_SYSTEM = (
    "You are analysing raw Kubernetes pod logs. Summarise what's actually going on in "
    "2-4 sentences: any errors/crashes/failures and their likely cause if evident from "
    "the text, or confirm things look healthy if there's nothing concerning. Be specific "
    "(name the actual error message or pattern) rather than vague. Plain text only."
)


def summarize_logs(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Fetch recent logs for a service and have the LLM summarise what's going on.

    Args:
        chat_id: Unused — kept for a consistent command-handler signature.
        args: [service_name] required; optional args[1] as a namespace override.

    Returns:
        A short LLM-generated summary, or a usage/error message.
    """
    args = args or []
    if not args:
        return "Usage: /logs summary <service-name>"

    service = args[0]
    namespace = args[1] if len(args) > 1 else K8S_NAMESPACE

    lines, error = _fetch_log_lines(service, namespace)
    if error:
        return error
    if not lines:
        return f"No logs found for pods matching '{service}-*' in namespace '{namespace}'."

    import brain  # local import — avoids loading the LLM at module import time

    try:
        llm = brain._get_llm()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _LOG_SUMMARY_SYSTEM},
                {"role": "user", "content": "\n".join(lines)},
            ],
            max_tokens=250,
            temperature=0.3,
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Log summarization LLM call failed: %s", exc)
        return "Something went wrong summarising those logs — try again shortly."


def _fit_to_reply_limit(lines: list[str]) -> str:
    """Join lines, dropping the oldest as needed to fit Telegram's message cap.

    Args:
        lines: Log lines, oldest first.

    Returns:
        A joined string under _MAX_REPLY_CHARS, noting how many lines were
        dropped if any were.
    """
    kept = list(lines)
    while kept and len("\n".join(kept)) > _MAX_REPLY_CHARS:
        kept.pop(0)

    dropped = len(lines) - len(kept)
    text = "\n".join(kept)
    if dropped:
        text = f"(showing last {len(kept)} of {len(lines)} lines)\n" + text
    return text


# ---------------------------------------------------------------------------
# /restart — gated deployment restart via the Kubernetes API directly
# ---------------------------------------------------------------------------


def request_restart(chat_id: str, deployment: str, namespace: str = "") -> str:
    """Stage a deployment restart for confirmation — never executes immediately.

    Args:
        chat_id: The owner requesting the restart.
        deployment: The deployment name to restart.
        namespace: Namespace override; defaults to K8S_NAMESPACE.

    Returns:
        A confirmation prompt. Execution happens in
        check_restart_confirmation() only after an explicit "yes".
    """
    if not deployment.strip():
        return "Usage: /restart <deployment> [namespace]"

    namespace = namespace.strip() or K8S_NAMESPACE
    _pending_restarts[chat_id] = (namespace, deployment, time.time())
    return (
        f"Restart deployment '{deployment}' in namespace '{namespace}'?\n"
        f"Reply “yes” within {_RESTART_CONFIRM_WINDOW_SECONDS}s to confirm, “no” to cancel."
    )


def check_restart_confirmation(chat_id: str, text: str) -> Optional[str]:
    """Resolve a parked restart request if `text` confirms or cancels it.

    Args:
        chat_id: The owner who may have a pending restart.
        text: The next message from that chat.

    Returns:
        The result string if `text` was consumed as a confirm/cancel/expiry
        reply, or None if there's nothing pending / text isn't a yes/no —
        callers should treat None as "not a confirmation".
    """
    pending = _pending_restarts.get(chat_id)
    if pending is None:
        return None

    namespace, deployment, queued_at = pending
    if time.time() - queued_at > _RESTART_CONFIRM_WINDOW_SECONDS:
        del _pending_restarts[chat_id]
        if _RESTART_CONFIRM_RE.match(text):
            return "That restart request expired — ask again if you still want it."
        return None

    if _RESTART_CONFIRM_RE.match(text):
        del _pending_restarts[chat_id]
        return _restart_deployment(namespace, deployment)

    if _RESTART_CANCEL_RE.match(text):
        del _pending_restarts[chat_id]
        return "Cancelled — nothing was restarted."

    del _pending_restarts[chat_id]
    return None


def _ssl_context() -> ssl.SSLContext:
    """Build the SSL context for the K8s API request — system trust store,
    or a private CA bundle if K8S_CA_CERT_PATH is set. No insecure/skip-
    verification option is offered."""
    if K8S_CA_CERT_PATH:
        return ssl.create_default_context(cafile=K8S_CA_CERT_PATH)
    return ssl.create_default_context()


def _restart_deployment(namespace: str, deployment: str) -> str:
    """PATCH the deployment's pod template annotation to trigger a rolling restart.

    Same mechanism `kubectl rollout restart` uses under the hood — stamping
    a fresh `kubectl.kubernetes.io/restartedAt` annotation onto the pod
    template, which the Deployment controller treats as a spec change and
    rolls pods accordingly. Talks to the Kubernetes API directly (typed
    PATCH, bearer-token auth) — the one function in this module that does,
    since Grafana has no facility for mutating cluster state.

    Args:
        namespace: The namespace the deployment lives in.
        deployment: The deployment name.

    Returns:
        A confirmation or error string.
    """
    if not K8S_API_URL or not K8S_SERVICE_ACCOUNT_TOKEN:
        return "K8S_API_URL / K8S_SERVICE_ACCOUNT_TOKEN not configured — see .env."

    url = f"{K8S_API_URL}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}"
    patch_body = json.dumps(
        {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": datetime.datetime.now(
                                datetime.timezone.utc
                            ).isoformat()
                        }
                    }
                }
            }
        }
    ).encode()

    request = urllib.request.Request(
        url,
        data=patch_body,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {K8S_SERVICE_ACCOUNT_TOKEN}",
            "Content-Type": "application/strategic-merge-patch+json",
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=_REQUEST_TIMEOUT_SECONDS, context=_ssl_context()
        ) as resp:
            if resp.status not in (200, 201):
                return f"Restart request for '{deployment}' returned unexpected status {resp.status}."
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        logger.error("K8s restart failed (%s) for %s/%s: %s", exc.code, namespace, deployment, body)
        if exc.code == 404:
            return f"Deployment '{deployment}' not found in namespace '{namespace}'."
        if exc.code in (401, 403):
            return "Not authorized to restart that deployment — check the service account's RBAC role."
        return f"Restart request failed (HTTP {exc.code})."
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.error("K8s API unreachable for restart of %s/%s: %s", namespace, deployment, exc)
        return "Couldn't reach the Kubernetes API server — check K8S_API_URL and network/VPN access."

    return f"Restart triggered for '{deployment}' in namespace '{namespace}'."
