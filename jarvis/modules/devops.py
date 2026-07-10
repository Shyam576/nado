"""
modules/devops.py — Kubernetes health and logs, via Grafana's datasource proxy.

Deliberately does NOT talk to the Kubernetes API directly (no kubeconfig, no
VPN dependency on the bot's host) — instead queries Prometheus (via
kube-state-metrics) and Loki through Grafana's own API, authenticated with a
Grafana service account Bearer token. This matches the finance.py pattern:
plain HTTP GET, parse JSON, format for Telegram.

Requires (see config.py / .env):
  GRAFANA_URL, GRAFANA_API_TOKEN, PROMETHEUS_DATASOURCE_UID, LOKI_DATASOURCE_UID
"""

import collections
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from config import (
    GRAFANA_API_TOKEN,
    GRAFANA_URL,
    K8S_NAMESPACE,
    LOKI_DATASOURCE_UID,
    PROMETHEUS_DATASOURCE_UID,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 15
_LOG_LOOKBACK_SECONDS = 24 * 3600
_MAX_LINE_CHARS = 300  # some app logs (e.g. raw SQL dumps) are absurdly long on their own
_MAX_REPLY_CHARS = 3500  # Telegram caps messages at 4096; leave headroom

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
    data = _grafana_get(
        f"/api/datasources/proxy/uid/{PROMETHEUS_DATASOURCE_UID}/api/v1/query",
        {"query": f'kube_pod_status_phase{{namespace="{namespace}"}} == 1'},
    )
    if data is None or data.get("status") != "success":
        return f"Couldn't reach Prometheus for namespace '{namespace}' — try again shortly."

    results = data["data"]["result"]
    if not results:
        return f"No pod status data found for namespace '{namespace}'."

    # deployment -> {phase: count}
    by_deployment: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for row in results:
        metric = row["metric"]
        pod = metric.get("pod", "unknown")
        phase = metric.get("phase", "Unknown")
        by_deployment[_deployment_name(pod)][phase] += 1

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
