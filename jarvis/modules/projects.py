"""
modules/projects.py — Per-repo developer status: git state + running containers.

/project <name> finds a repository by folder name under the configured roots
and reports branch, last commit, working-tree state, and any Docker
containers whose name matches the project.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Where to look for project folders, in order. First match wins.
PROJECT_ROOTS: list[Path] = [
    Path.home() / "Desktop" / "nado",
    Path.home() / "Desktop",
    Path.home(),
]

_GIT_TIMEOUT = 10


def _git(repo: Path, *args: str) -> str | None:
    """Run a git command in repo and return stripped stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("git %s failed in %s: %s", args, repo, exc)
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _find_project(name: str) -> Path | None:
    """Locate a directory whose name matches (case-insensitive) under the roots."""
    needle = name.lower()
    for root in PROJECT_ROOTS:
        if not root.is_dir():
            continue
        try:
            for entry in root.iterdir():
                if entry.is_dir() and entry.name.lower() == needle:
                    return entry
        except OSError:
            continue
    # Second pass: substring match, so "permit" finds "permit-service"
    for root in PROJECT_ROOTS:
        if not root.is_dir():
            continue
        try:
            for entry in root.iterdir():
                if entry.is_dir() and needle in entry.name.lower():
                    return entry
        except OSError:
            continue
    return None


def _docker_containers(project_name: str) -> list[str]:
    """Return status lines for running containers whose name matches the project."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}",
             "--filter", f"name={project_name}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []  # docker not installed or daemon not running — not an error here
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.strip().splitlines() if line]


def project_status(chat_id: str = "", args: list[str] | None = None) -> str:
    """Report git + container status for a project folder.

    Args:
        chat_id: Unused.
        args: [project_name] — the folder name (or a distinctive part of it).

    Returns:
        A multi-line status report, or a usage/not-found message.
    """
    args = args or []
    if not args:
        return "Usage: /project <name> — e.g. /project jarvis"

    name = " ".join(args).strip()
    repo = _find_project(name)
    if repo is None:
        return f"No project folder matching '{name}' found under {', '.join(str(r) for r in PROJECT_ROOTS)}."

    lines = [f"📁 {repo}"]

    # rev-parse (not a .git check) so folders living inside a parent repo
    # (e.g. nado/jarvis where the repo root is nado/) still report correctly.
    if _git(repo, "rev-parse", "--is-inside-work-tree") != "true":
        lines.append("Not a git repository.")
    else:
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        if branch:
            lines.append(f"Branch: {branch}")

        last_commit = _git(repo, "log", "-1", "--format=%h %s (%cr)")
        if last_commit:
            lines.append(f"Last commit: {last_commit}")

        status = _git(repo, "status", "--porcelain")
        if status is not None:
            changed = status.splitlines()
            if changed:
                preview = "\n".join(f"  {line}" for line in changed[:8])
                more = f"\n  … and {len(changed) - 8} more" if len(changed) > 8 else ""
                lines.append(f"Uncommitted changes ({len(changed)}):\n{preview}{more}")
            else:
                lines.append("Working tree clean.")

        ahead_behind = _git(repo, "rev-list", "--left-right", "--count", "@{upstream}...HEAD")
        if ahead_behind:
            behind, ahead = ahead_behind.split()
            if ahead != "0" or behind != "0":
                lines.append(f"vs upstream: {ahead} ahead, {behind} behind")

    containers = _docker_containers(repo.name)
    if containers:
        lines.append("Containers:")
        lines.extend(f"  {line}" for line in containers)

    return "\n".join(lines)
