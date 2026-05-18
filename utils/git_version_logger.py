#!/usr/bin/env python3
"""
Git Version Logger: Track every push with commit history and metadata.
Maintains a persistent version history file for audit trail and rollback reference.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

LOG_DIR = Path.home() / ".git_push_helper"
LOG_DIR.mkdir(exist_ok=True)

VERSION_HISTORY_FILE = LOG_DIR / "version_history.json"
SESSION_LOG_FILE = LOG_DIR / f"push_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(SESSION_LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Version Tracking
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CommitInfo:
    """Information about a single commit."""
    hash: str
    message: str
    author: str
    timestamp: str
    files_changed: int
    insertions: int
    deletions: int


@dataclass
class PushRecord:
    """Record of a single push event."""
    timestamp: str
    branch: str
    remote: str
    strategy: str
    num_commits: int
    commits: List[CommitInfo]
    from_hash: str
    to_hash: str
    user: str
    status: str  # "success" or "failed"
    error_msg: Optional[str] = None
    session_log: str = ""


def load_version_history() -> List[Dict]:
    """Load existing version history."""
    if VERSION_HISTORY_FILE.exists():
        try:
            with open(VERSION_HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load version history: {e}")
    return []


def save_version_history(history: List[Dict]):
    """Save version history to file."""
    try:
        with open(VERSION_HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
        logger.info(f"✓ Version history saved: {VERSION_HISTORY_FILE}")
    except Exception as e:
        logger.error(f"Failed to save version history: {e}")


def get_commit_info(commit_hash: str) -> CommitInfo:
    """Extract detailed info about a commit."""
    try:
        message = run_cmd(f"git log -1 --format=%B {commit_hash}", check=False).strip()
        author = run_cmd(f"git log -1 --format=%an {commit_hash}", check=False).strip()
        timestamp = run_cmd(f"git log -1 --format=%aI {commit_hash}", check=False).strip()

        # Get file change stats
        stats = run_cmd(
            f"git diff-tree --no-commit-id --numstat -r {commit_hash}",
            check=False
        )

        files_changed = 0
        insertions = 0
        deletions = 0

        for line in stats.split("\n"):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 3:
                files_changed += 1
                try:
                    insertions += int(parts[0]) if parts[0] != "-" else 0
                    deletions += int(parts[1]) if parts[1] != "-" else 0
                except ValueError:
                    pass

        return CommitInfo(
            hash=commit_hash[:7],
            message=message[:80],
            author=author,
            timestamp=timestamp,
            files_changed=files_changed,
            insertions=insertions,
            deletions=deletions,
        )
    except Exception as e:
        logger.warning(f"Could not get commit info for {commit_hash}: {e}")
        return CommitInfo(
            hash=commit_hash[:7],
            message="<unknown>",
            author="<unknown>",
            timestamp=datetime.now().isoformat(),
            files_changed=0,
            insertions=0,
            deletions=0,
        )


def get_commits_in_range(from_hash: str, to_hash: str) -> List[CommitInfo]:
    """Get all commits between two hashes."""
    try:
        output = run_cmd(f"git log {from_hash}..{to_hash} --format=%H", check=False)
        commits = []
        for commit_hash in output.split("\n"):
            if commit_hash.strip():
                commits.append(get_commit_info(commit_hash.strip()))
        return list(reversed(commits))  # Oldest first
    except Exception as e:
        logger.warning(f"Could not get commits in range: {e}")
        return []


def record_push(
    branch: str,
    remote: str,
    strategy: str,
    from_hash: str,
    to_hash: str,
    status: str,
    error_msg: Optional[str] = None,
):
    """Record a push event to version history."""
    try:
        user = run_cmd("git config user.name", check=False).strip()
        commits = get_commits_in_range(from_hash, to_hash)

        record = PushRecord(
            timestamp=datetime.now().isoformat(),
            branch=branch,
            remote=remote,
            strategy=strategy,
            num_commits=len(commits),
            commits=[asdict(c) for c in commits],
            from_hash=from_hash[:7],
            to_hash=to_hash[:7],
            user=user,
            status=status,
            error_msg=error_msg,
            session_log=str(SESSION_LOG_FILE),
        )

        history = load_version_history()
        history.append(asdict(record))
        save_version_history(history)

        return record
    except Exception as e:
        logger.error(f"Failed to record push: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Git Operations
# ─────────────────────────────────────────────────────────────────────────────

def run_cmd(cmd: str, shell: bool = True, check: bool = True) -> str:
    """Execute shell command and return stdout."""
    logger.debug(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=shell, capture_output=True, text=True, check=False)

    if result.returncode != 0 and check:
        logger.error(f"Command failed: {cmd}")
        logger.error(f"stderr: {result.stderr}")
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")

    return result.stdout.strip()


def get_staged_files() -> List[str]:
    """Get list of staged file paths."""
    output = run_cmd("git diff --cached --name-only")
    return [f for f in output.split("\n") if f.strip()]


def unstage_all():
    """Unstage all changes."""
    run_cmd("git restore --staged .", check=False)
    logger.info("Unstaged all changes")


def stage_files(files: List[str]):
    """Stage specific files."""
    if not files:
        logger.warning("No files to stage")
        return
    file_args = " ".join(f'"{f}"' for f in files)
    run_cmd(f"git add {file_args}")
    logger.info(f"Staged {len(files)} file(s)")


def get_current_hash() -> str:
    """Get current HEAD commit hash."""
    return run_cmd("git rev-parse HEAD", check=False)


def commit(message: str) -> bool:
    """Commit staged changes."""
    try:
        run_cmd(f'git commit -m "{message}"')
        logger.info(f"✓ Committed: {message[:60]}...")
        return True
    except RuntimeError as e:
        logger.error(f"Failed to commit: {e}")
        return False


def push_to_remote(remote: str = "origin", branch: str = "master", dry_run: bool = False) -> bool:
    """Push commits to remote."""
    cmd = f"git push {remote} {branch}"
    if dry_run:
        cmd += " --dry-run"

    try:
        output = run_cmd(cmd)
        logger.info(f"{'[DRY-RUN] ' if dry_run else ''}✓ Pushed to {remote}/{branch}")
        if output:
            logger.info(output)
        return True
    except RuntimeError as e:
        logger.error(f"Failed to push: {e}")
        return False


def get_repo_status() -> dict:
    """Get current repo status."""
    branch = run_cmd("git rev-parse --abbrev-ref HEAD")
    remote_branch = run_cmd("git rev-parse --abbrev-ref --symbolic-full-name @{u}", check=False)
    ahead = run_cmd("git rev-list --count HEAD..@{u}", check=False)
    behind = run_cmd("git rev-list --count @{u}..HEAD", check=False)

    return {
        "branch": branch,
        "remote_branch": remote_branch if remote_branch else "untracked",
        "commits_ahead": int(ahead) if ahead and ahead.isdigit() else 0,
        "commits_behind": int(behind) if behind and behind.isdigit() else 0,
    }


def get_recent_commits(count: int = 5) -> List[str]:
    """Get recent commit messages."""
    output = run_cmd(f"git log --oneline -n {count}")
    return output.split("\n") if output else []


# ─────────────────────────────────────────────────────────────────────────────
# Commit Grouping
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CommitGroup:
    """Represents a logical group of files to commit together."""
    name: str
    description: str
    files: List[str]
    message_template: str


def analyze_staged_files(files: List[str]) -> List[CommitGroup]:
    """Analyze staged files and suggest logical grouping."""
    groups = []
    categorized = {
        "mcp": [],
        "frontend": [],
        "backend": [],
        "tests": [],
        "docs": [],
        "config": [],
        "data": [],
        "other": [],
    }

    for f in files:
        if "mcp_" in f or "/mcp" in f:
            categorized["mcp"].append(f)
        elif any(ext in f for ext in [".jsx", ".tsx", ".html", ".css", ".js"]):
            if "test" not in f and "spec" not in f:
                categorized["frontend"].append(f)
            else:
                categorized["tests"].append(f)
        elif any(ext in f for ext in [".py", ".go", ".java", ".rs"]):
            if "test" not in f:
                categorized["backend"].append(f)
            else:
                categorized["tests"].append(f)
        elif any(ext in f for ext in [".md", ".txt", ".rst"]):
            categorized["docs"].append(f)
        elif any(name in f for name in [".json", ".yaml", ".yml", ".toml", ".env", "settings", "config"]):
            categorized["config"].append(f)
        elif any(ext in f for ext in [".csv", ".json", ".parquet", "/data"]):
            categorized["data"].append(f)
        else:
            categorized["other"].append(f)

    category_info = {
        "mcp": ("MCP Server", "MCP server and integration"),
        "backend": ("Backend Changes", "Core backend logic and APIs"),
        "frontend": ("Frontend Changes", "UI and frontend components"),
        "tests": ("Tests", "Test suite additions/updates"),
        "data": ("Data Updates", "Data files and configurations"),
        "docs": ("Documentation", "Documentation updates"),
        "config": ("Configuration", "Configuration and settings"),
    }

    for category, (name, description) in category_info.items():
        if categorized[category]:
            groups.append(CommitGroup(
                name=name,
                description=description,
                files=categorized[category],
                message_template=f"{name}: {description}",
            ))

    if categorized["other"]:
        groups.append(CommitGroup(
            name="Other Changes",
            description="Miscellaneous updates",
            files=categorized["other"],
            message_template="Miscellaneous updates",
        ))

    return groups


def prompt_user(message: str, options: List[str], default_idx: int = 0) -> str:
    """Prompt user to select from options."""
    logger.info(message)
    for i, opt in enumerate(options):
        marker = "→" if i == default_idx else " "
        logger.info(f"  {marker} {i+1}. {opt}")

    while True:
        try:
            choice = input(f"\nSelect option [1-{len(options)}] (default: {default_idx+1}): ").strip()
            if not choice:
                return options[default_idx]
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx]
            logger.warning(f"Invalid choice. Please enter 1-{len(options)}")
        except ValueError:
            logger.warning("Invalid input. Please enter a number.")


# ─────────────────────────────────────────────────────────────────────────────
# Version History Commands
# ─────────────────────────────────────────────────────────────────────────────

def cmd_history(limit: int = 10, json_output: bool = False):
    """Display version history."""
    history = load_version_history()

    if not history:
        logger.info("No push history found.")
        return

    recent = history[-limit:]

    if json_output:
        print(json.dumps(recent, indent=2))
        return

    logger.info("\n" + "=" * 100)
    logger.info("Version History (most recent first)")
    logger.info("=" * 100)

    for i, record in enumerate(reversed(recent), 1):
        ts = record.get("timestamp", "")[:19]
        branch = record.get("branch", "?")
        remote = record.get("remote", "?")
        num_commits = record.get("num_commits", 0)
        from_h = record.get("from_hash", "?")
        to_h = record.get("to_hash", "?")
        user = record.get("user", "?")
        status = record.get("status", "?")
        status_icon = "✓" if status == "success" else "✗"

        logger.info(f"\n[{i}] {status_icon} {ts} | {user}")
        logger.info(f"    {remote}/{branch}: {from_h}..{to_h} ({num_commits} commits)")
        logger.info(f"    Strategy: {record.get('strategy', '?')}")

        commits = record.get("commits", [])
        if commits:
            logger.info("    Commits:")
            for c in commits[:3]:
                logger.info(f"      • {c.get('hash', '?')} {c.get('message', '?')[:50]}")
            if len(commits) > 3:
                logger.info(f"      ... and {len(commits) - 3} more")

        if record.get("error_msg"):
            logger.error(f"    Error: {record.get('error_msg')}")


def cmd_show_version(version_idx: int):
    """Show details of a specific version."""
    history = load_version_history()

    if not history or version_idx < 1 or version_idx > len(history):
        logger.error(f"Invalid version index. Available: 1-{len(history)}")
        return

    record = history[-(version_idx)]

    logger.info("\n" + "=" * 100)
    logger.info(f"Version {version_idx} Details")
    logger.info("=" * 100)

    for key, value in record.items():
        if key == "commits":
            logger.info(f"\n{key.upper()}:")
            for c in value:
                logger.info(f"  {c['hash']} | {c['author']}")
                logger.info(f"    {c['message']}")
                logger.info(f"    Files: {c['files_changed']}, +{c['insertions']}/-{c['deletions']}")
        else:
            logger.info(f"{key}: {value}")


def cmd_rollback_info(version_idx: int):
    """Show how to rollback to a previous version."""
    history = load_version_history()

    if not history or version_idx < 1 or version_idx > len(history):
        logger.error(f"Invalid version index. Available: 1-{len(history)}")
        return

    record = history[-(version_idx)]
    from_hash = record.get("from_hash")

    logger.info("\n" + "=" * 60)
    logger.info(f"To rollback to version {version_idx}:")
    logger.info("=" * 60)
    logger.info(f"git reset --hard {from_hash}")
    logger.info(f"git push -f origin {record.get('branch')}")


# ─────────────────────────────────────────────────────────────────────────────
# Main Flow
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Git version logger: Track every push with version history.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Push command
    push_parser = subparsers.add_parser("push", help="Push changes")
    push_parser.add_argument("--strategy", choices=["bundle", "split", "interactive"], default="interactive")
    push_parser.add_argument("--message", type=str)
    push_parser.add_argument("--remote", default="origin")
    push_parser.add_argument("--branch", default="master")
    push_parser.add_argument("--dry-run", action="store_true")
    push_parser.add_argument("--no-prompt", action="store_true")

    # History command
    history_parser = subparsers.add_parser("history", help="Show version history")
    history_parser.add_argument("--limit", type=int, default=10)
    history_parser.add_argument("--json", action="store_true")

    # Show command
    show_parser = subparsers.add_parser("show", help="Show specific version")
    show_parser.add_argument("version", type=int, help="Version index (from history)")

    # Rollback command
    rollback_parser = subparsers.add_parser("rollback-info", help="Show rollback info")
    rollback_parser.add_argument("version", type=int, help="Version index to rollback to")

    args = parser.parse_args()

    # If no command, default to push
    if not args.command:
        args.command = "push"
        args.strategy = "interactive"
        args.message = None
        args.remote = "origin"
        args.branch = "master"
        args.dry_run = False
        args.no_prompt = False

    try:
        logger.info("=" * 80)
        logger.info(f"Git Version Logger - {args.command.upper()}")
        logger.info(f"Log: {SESSION_LOG_FILE}")
        logger.info(f"History: {VERSION_HISTORY_FILE}")
        logger.info("=" * 80)

        if args.command == "history":
            cmd_history(args.limit, args.json)
        elif args.command == "show":
            cmd_show_version(args.version)
        elif args.command == "rollback-info":
            cmd_rollback_info(args.version)
        else:  # push
            before_hash = get_current_hash()

            # ... (push logic from before) ...
            logger.info("\n[Push logic would execute here]")

            after_hash = get_current_hash()

            # Record the push
            record_push(
                branch=args.branch,
                remote=args.remote,
                strategy=args.strategy,
                from_hash=before_hash,
                to_hash=after_hash,
                status="success",
            )

        return 0

    except Exception as e:
        logger.exception(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
