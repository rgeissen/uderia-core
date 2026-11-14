"""
Shared author-notification helpers for Uderia agent pack build scripts.

Two functions are provided:

  notify_author()        — first-deployment introduction email (sent once ever)
  notify_author_update() — update email sent once per source commit

Usage in a build script:

    from agent_packs.common.notify import notify_author, notify_author_update

    # First deployment
    notify_author(
        source_repo_url = "https://github.com/owner/repo.git",
        pack_dir        = PACK_DIR,          # for .notified.json guard
        pack_name       = "My Pack",
        pack_tags       = ["@TAG1", "@TAG2"],
        uderia_url      = "http://localhost:5050",
        username        = "admin",
        password        = "admin",
        author_email    = None,              # supply to skip auto-resolution
    )

    # Update notification (once per source commit)
    notify_author_update(
        source_repo_url = "https://github.com/owner/repo.git",
        pack_dir        = PACK_DIR,          # for .update_notified.json guard
        source_dir      = SOURCE_DIR,        # cloned repo, for git log
        pack_name       = "My Pack",
        pack_tags       = ["@TAG1", "@TAG2"],
        uderia_url      = "http://localhost:5050",
        username        = "admin",
        password        = "admin",
        author_email    = None,              # supply to skip auto-resolution
        current_commit  = "abc1234",         # from skill.json source_commit field
    )

Requires `requests` (pip install requests).
"""

import json
import os
import subprocess
from datetime import datetime, timezone

# ── Console helpers (mirror the style used in individual build scripts) ────────

def _head(msg):  print(f"\n── {msg} " + "─" * max(0, 60 - len(msg)))
def _step(msg):  print(f"  → {msg}")
def _ok(msg):    print(f"  ✓ {msg}")
def _err(msg):   print(f"  ✗ {msg}", flush=True)


# ── GitHub API ─────────────────────────────────────────────────────────────────

def _github_api(path: str, pack_name: str = "uderia-agent-pack") -> dict:
    """Call the GitHub REST API (unauthenticated, 60 req/hr)."""
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://api.github.com{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"{pack_name}/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


# ── License check ──────────────────────────────────────────────────────────────

_OPEN_LICENSE_IDS = {
    "mit", "apache-2.0", "gpl-2.0", "gpl-3.0", "lgpl-2.1", "lgpl-3.0",
    "bsd-2-clause", "bsd-3-clause", "isc", "mpl-2.0", "cc0-1.0",
    "unlicense", "agpl-3.0", "eupl-1.2",
}

def is_open_license(license_name: str) -> bool:
    return license_name.lower().replace(" ", "-") in _OPEN_LICENSE_IDS


# ── Author resolution ──────────────────────────────────────────────────────────

def resolve_author_info(source_repo_url: str, source_dir: str, pack_name: str = "uderia-agent-pack") -> dict:
    """
    Resolve author name, email, and license from a GitHub repo URL.

    Resolution order for email:
      1. GitHub public user profile
      2. git log from the cloned source repo (skips noreply addresses)

    Returns dict: {name, email, login, license}  — any field may be empty string.
    """
    _head("Resolving author info")

    parts = source_repo_url.rstrip(".git").rstrip("/").split("/")
    owner, repo = parts[-2], parts[-1]

    repo_data    = _github_api(f"/repos/{owner}/{repo}", pack_name)
    license_name = (repo_data.get("license") or {}).get("spdx_id") or \
                   (repo_data.get("license") or {}).get("name") or "Unknown"
    _ok(f"License: {license_name}")

    user_data = _github_api(f"/users/{owner}", pack_name)
    login     = user_data.get("login") or owner
    name      = user_data.get("name") or login
    email     = (user_data.get("email") or "").strip()

    if email:
        _ok(f"Email (GitHub profile): {email}")
    else:
        _step("GitHub profile email not public — falling back to git log")
        git_dir = os.path.join(source_dir, ".git")
        if os.path.isdir(git_dir):
            result = subprocess.run(
                ["git", "log", "--format=%ae", "-10"],
                cwd=source_dir, capture_output=True, text=True,
            )
            for candidate in result.stdout.splitlines():
                if candidate and "noreply" not in candidate and "@" in candidate:
                    email = candidate.strip()
                    _ok(f"Email (git log): {email}")
                    break
        if not email:
            _step("No public email found — pass --author-email to supply it directly")

    return {"name": name, "login": login, "email": email, "license": license_name}


# ── Main entry point ───────────────────────────────────────────────────────────

def notify_author(
    source_repo_url: str,
    pack_dir: str,
    pack_name: str,
    pack_tags: list,
    uderia_url: str,
    username: str,
    password: str,
    author_email: str | None = None,
    skip_license_check: bool = False,
) -> None:
    """
    Send a one-time courtesy notification email to the upstream repo author.

    Parameters
    ----------
    source_repo_url : str
        HTTPS URL of the upstream repository (with or without .git suffix).
    pack_dir : str
        Directory where .notified.json is written (the pack's own folder).
    pack_name : str
        Human-readable pack name shown in the email.
    pack_tags : list[str]
        Profile tags shown as orange pill badges in the email.
    uderia_url : str
        Base URL of the running Uderia server.
    username / password : str
        Admin credentials for authenticating against Uderia.
    author_email : str | None
        Supply directly to skip GitHub / git-log auto-resolution entirely.
        When provided, license check is still performed; author name defaults
        to the GitHub login if not resolved.
    """
    _head("Author notification")

    notified_path = os.path.join(pack_dir, ".notified.json")
    if os.path.exists(notified_path):
        with open(notified_path) as f:
            state = json.load(f)
        _ok(f"Already sent on {state.get('sent_at', '(unknown)')} to {state.get('to')} — skipping")
        return

    try:
        import requests
    except ImportError:
        _err("requests library not available — run: pip install requests")
        return

    clean_url = source_repo_url.rstrip(".git").rstrip("/")
    repo_name = clean_url.split("/")[-1]
    slug      = clean_url.replace("https://", "").replace("http://", "")
    parts     = slug.split("/")
    owner     = parts[1] if len(parts) >= 2 else ""

    # Always fetch license + author name (needed for email even when email is supplied)
    author = resolve_author_info(source_repo_url, os.path.join(pack_dir, "source"), repo_name)

    # Override email if supplied directly
    if author_email:
        _ok(f"Using supplied email: {author_email}")
        author["email"] = author_email.strip()

    if not author["email"]:
        _err("No email address found — pass --author-email <address> to supply it directly")
        return

    license_name = author["license"]
    if not is_open_license(license_name):
        if skip_license_check:
            _step(f"License '{license_name}' is unrecognised — proceeding anyway (--skip-license-check)")
        else:
            _err(
                f"License '{license_name}' is not a recognised open-source license.\n"
                "  Skipping — verify the repo license or pass --skip-license-check to override."
            )
            return

    # Authenticate
    r = requests.post(
        f"{uderia_url}/api/v1/auth/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    if r.status_code != 200:
        _err(f"Login failed: {r.text}")
        return
    token = r.json().get("token")

    _step(f"Sending notification to {author['email']} ({author['name']}) ...")
    r2 = requests.post(
        f"{uderia_url}/api/v1/agent-packs/notify-author",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "author_email": author["email"],
            "author_name":  author["name"],
            "repo_url":     clean_url,
            "repo_name":    repo_name,
            "pack_name":    pack_name,
            "pack_tags":    pack_tags,
            "license_name": license_name,
        },
        timeout=15,
    )

    if r2.status_code == 200:
        _ok(f"Notification sent to {author['email']}")
        with open(notified_path, "w") as f:
            json.dump({
                "to":       author["email"],
                "name":     author["name"],
                "repo_url": source_repo_url,
                "sent_at":  datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)
        _ok("Guard file written: .notified.json (prevents duplicate sends)")
    else:
        _err(f"Notification failed (HTTP {r2.status_code}): {r2.text}")
        _step("Check that EMAIL_PROVIDER and its credentials are configured in Uderia")


# ── Update notification ────────────────────────────────────────────────────────

def _get_commit_log(source_dir: str, since_commit: str | None) -> list[str]:
    """Return one-line commit messages from source_dir since since_commit (or last 10)."""
    if not os.path.isdir(os.path.join(source_dir, ".git")):
        return []
    if since_commit:
        cmd = ["git", "log", "--oneline", f"{since_commit}..HEAD"]
    else:
        cmd = ["git", "log", "--oneline", "-10"]
    result = subprocess.run(cmd, cwd=source_dir, capture_output=True, text=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def notify_author_update(
    source_repo_url: str,
    pack_dir: str,
    source_dir: str,
    pack_name: str,
    pack_tags: list,
    uderia_url: str,
    username: str,
    password: str,
    author_email: str | None = None,
    current_commit: str | None = None,
    skip_license_check: bool = False,
) -> None:
    """
    Send a one-time update notification email to the upstream repo author for the
    current source commit. Skips if this commit was already notified.

    Guard file: <pack_dir>/.update_notified.json  (stores last-notified commit).

    Parameters
    ----------
    source_repo_url : str
        HTTPS URL of the upstream repository.
    pack_dir : str
        Directory where .update_notified.json is written.
    source_dir : str
        Path to the cloned source repo (used for git log between commits).
    pack_name : str
        Human-readable pack name shown in the email.
    pack_tags : list[str]
        Profile tags shown as orange pill badges.
    uderia_url / username / password : str
        Uderia server credentials.
    author_email : str | None
        Supply directly to skip GitHub / git-log email resolution.
    current_commit : str | None
        Commit hash of the current build (from skill.json source_commit).
        Falls back to HEAD of source_dir if omitted.
    """
    _head("Author update notification")

    notified_path = os.path.join(pack_dir, ".update_notified.json")

    # Resolve current commit
    if not current_commit and os.path.isdir(os.path.join(source_dir, ".git")):
        current_commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=source_dir, capture_output=True, text=True,
        ).stdout.strip()

    if not current_commit:
        _err("Cannot determine current source commit — run --sync first or ensure source/ is cloned")
        return

    # Read previous notified commit from guard
    previous_commit = None
    if os.path.exists(notified_path):
        with open(notified_path) as f:
            state = json.load(f)
        previous_commit = state.get("commit")
        if previous_commit == current_commit:
            _ok(f"Commit {current_commit} already notified on {state.get('sent_at', '(unknown)')} — skipping")
            return
        _step(f"New commit detected: {previous_commit} → {current_commit}")
    else:
        _step(f"First update notification for commit {current_commit}")

    try:
        import requests
    except ImportError:
        _err("requests library not available — run: pip install requests")
        return

    # Resolve author info (email + license)
    clean_url = source_repo_url.rstrip(".git").rstrip("/")
    repo_name = clean_url.split("/")[-1]
    author    = resolve_author_info(source_repo_url, source_dir, repo_name)

    if author_email:
        _ok(f"Using supplied email: {author_email}")
        author["email"] = author_email.strip()

    if not author["email"]:
        _err("No email address found — pass --author-email <address> to supply it directly")
        return

    license_name = author["license"]
    if not is_open_license(license_name):
        if skip_license_check:
            _step(f"License '{license_name}' is unrecognised — proceeding anyway (--skip-license-check)")
        else:
            _err(
                f"License '{license_name}' is not a recognised open-source license — skipping.\n"
                "  Pass --skip-license-check to override."
            )
            return

    # Collect commit messages for the email
    changes = _get_commit_log(source_dir, previous_commit)
    if not changes:
        changes = [f"Updated to commit {current_commit}"]

    # Authenticate
    r = requests.post(
        f"{uderia_url}/api/v1/auth/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    if r.status_code != 200:
        _err(f"Login failed: {r.text}")
        return
    token = r.json().get("token")

    _step(f"Sending update notification to {author['email']} ({author['name']}) ...")
    r2 = requests.post(
        f"{uderia_url}/api/v1/agent-packs/notify-author-update",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "author_email":    author["email"],
            "author_name":     author["name"],
            "repo_url":        clean_url,
            "repo_name":       repo_name,
            "pack_name":       pack_name,
            "pack_tags":       pack_tags,
            "license_name":    license_name,
            "previous_commit": previous_commit or "",
            "current_commit":  current_commit,
            "changes":         changes,
        },
        timeout=15,
    )

    if r2.status_code == 200:
        _ok(f"Update notification sent to {author['email']}")
        with open(notified_path, "w") as f:
            json.dump({
                "to":              author["email"],
                "name":            author["name"],
                "repo_url":        source_repo_url,
                "commit":          current_commit,
                "previous_commit": previous_commit or "",
                "sent_at":         datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)
        _ok("Guard file updated: .update_notified.json")
    else:
        _err(f"Update notification failed (HTTP {r2.status_code}): {r2.text}")
        _step("Check that EMAIL_PROVIDER and its credentials are configured in Uderia")
