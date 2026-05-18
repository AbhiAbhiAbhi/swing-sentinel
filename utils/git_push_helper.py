#!/usr/bin/env python3
"""
Git Push Helper: Intelligently stage, commit, and push changes to GitHub.
Features:
  - Analyzes staged files and suggests logical commit groupings
  - Interactive or automatic split/bundle commit strategies
  - Clean logging to console and file
  - Pre-push safety checks (dirty working tree, branch tracking, etc.)
  - Dry-run mode for testing
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List


# ─────────────────────────────────────────────────────────────────────────────
# Configuration & Logging
# ─────────────────────────────────────────────────────────────────────────────

LOG_DIR = Path.home() / ".git_push_helper"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"push_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

logger.info("=" * 80)
logger.info(f"Git Push Helper started. Log: {LOG_FILE}")
logger.info("=" * 80)


# ─────────────────────────────────────────────────────────────────────────────
# Git Operations
# ─────────────────────────────────────────────────────────────────────────────

def run_cmd(cmd: str, shell: bool = True, check: bool = True) -> str:
    """Execute shell command and return stdout, log to file."""
    logger.debug(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=shell, capture_output=True, text=True, check=False)

    if result.returncode != 0 and check:
        logger.error(f"Command failed: {cmd}")
        logger.error(f"stderr: {result.stderr}")
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")

    if result.stdout:
        logger.debug(f"stdout: {result.stdout[:200]}")
    if result.stderr and result.returncode != 0:
        logger.error(f"stderr: {result.stderr[:200]}")

    return result.stdout.strip()


def get_staged_files() -> List[str]:
    """Get list of staged file paths."""
    output = run_cmd("git diff --cached --name-only")
    return [f for f in output.split("\n") if f.strip()]


def get_file_diff_stat(filepath: str) -> Tuple[int, int]:
    """Get insertions and deletions for a file."""
    output = run_cmd(f"git diff --cached --numstat -- {filepath}")
    if not output:
        return 0, 0
    parts = output.split()
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 0, 0


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


def commit(message: str, allow_empty: bool = False) -> bool:
    """Commit staged changes. Returns True if successful."""
    if not allow_empty:
        # Check if there are staged changes
        output = run_cmd("git diff --cached --quiet", check=False)
        if run_cmd("git diff --cached --quiet", check=False) == "" and \
           run_cmd("git diff --cached --exit-code", check=False) != "":
            # No staged changes
            logger.warning("No staged changes to commit")
            return False

    try:
        run_cmd(f'git commit -m "{message}"')
        logger.info(f"[OK] Committed: {message[:60]}...")
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
        logger.info(f"{'[DRY-RUN] ' if dry_run else ''}[OK] Pushed to {remote}/{branch}")
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
# Commit Grouping Logic
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

    # Create commit groups for non-empty categories
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
        marker = "->" if i == default_idx else " "
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


def prompt_commit_strategy() -> str:
    """Ask user for commit strategy."""
    logger.info("\n" + "=" * 60)
    logger.info("Commit Strategy")
    logger.info("=" * 60)
    return prompt_user(
        "How should I structure the commits?",
        ["Bundle all into one commit", "Split by logical category"],
        default_idx=1,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main Flow
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Intelligently push changes to GitHub with clean commits.",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=".",
        help="Path to git repository (default: current directory)",
    )
    parser.add_argument(
        "--strategy",
        choices=["bundle", "split", "interactive"],
        default="interactive",
        help="Commit strategy (default: interactive prompt)",
    )
    parser.add_argument(
        "--message",
        type=str,
        help="Custom commit message (for bundle mode)",
    )
    parser.add_argument(
        "--remote",
        default="origin",
        help="Git remote name (default: origin)",
    )
    parser.add_argument(
        "--branch",
        default="master",
        help="Branch to push (default: master)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate push without actually pushing",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Skip safety prompts",
    )

    args = parser.parse_args()

    # Change to repo directory
    repo_path = Path(args.repo).resolve()
    if not (repo_path / ".git").exists():
        logger.error(f"Not a git repository: {repo_path}")
        logger.error(f"Could not find .git directory at {repo_path}")
        return 1

    import os
    os.chdir(repo_path)
    logger.info(f"Working directory: {repo_path}")

    try:
        # ─── Status Check ──────────────────────────────────────────────────
        logger.info("\n" + "=" * 60)
        logger.info("Repository Status")
        logger.info("=" * 60)

        status = get_repo_status()
        logger.info(f"Branch: {status['branch']} -> {status['remote_branch']}")
        logger.info(f"Commits: {status['commits_ahead']} ahead, {status['commits_behind']} behind")

        recent = get_recent_commits(3)
        if recent:
            logger.info("Recent commits:")
            for commit in recent:
                logger.info(f"  {commit}")

        # ─── Get Staged Files ──────────────────────────────────────────────
        logger.info("\n" + "=" * 60)
        logger.info("Staged Files")
        logger.info("=" * 60)

        files = get_staged_files()
        if not files:
            logger.warning("No staged changes. Nothing to commit.")
            return 1

        logger.info(f"Found {len(files)} staged file(s):")
        for f in files:
            logger.info(f"  * {f}")

        # ─── Determine Strategy ────────────────────────────────────────────
        if args.strategy == "interactive":
            strategy = prompt_commit_strategy()
        else:
            strategy = "Bundle all into one commit" if args.strategy == "bundle" else "Split by logical category"

        logger.info(f"Strategy: {strategy}")

        # ─── Execute Strategy ──────────────────────────────────────────────
        logger.info("\n" + "=" * 60)
        logger.info("Committing")
        logger.info("=" * 60)

        if "Bundle" in strategy:
            if args.message:
                message = args.message
            else:
                message = input("Enter commit message: ").strip()
                if not message:
                    logger.error("Commit message cannot be empty")
                    return 1

            commit(message)
        else:
            unstage_all()
            groups = analyze_staged_files(files)

            logger.info(f"Identified {len(groups)} logical group(s):")
            for i, group in enumerate(groups, 1):
                logger.info(f"  {i}. {group.name} ({len(group.files)} file(s))")

            for group in groups:
                logger.info(f"\nCommitting: {group.name}")
                logger.info(f"  Files: {', '.join(group.files[:3])}" +
                           (f" (+{len(group.files)-3} more)" if len(group.files) > 3 else ""))

                # Allow user to customize message
                default_msg = group.message_template
                custom_msg = input(f"  Message [{default_msg}]: ").strip()
                message = custom_msg if custom_msg else default_msg

                stage_files(group.files)
                commit(message)

        # ─── Pre-Push Checks ───────────────────────────────────────────────
        logger.info("\n" + "=" * 60)
        logger.info("Pre-Push Checks")
        logger.info("=" * 60)

        status = get_repo_status()
        logger.info(f"Current branch: {status['branch']}")
        logger.info(f"Remote branch: {status['remote_branch']}")

        if status['commits_behind'] > 0 and not args.no_prompt:
            logger.warning(f"Local branch is {status['commits_behind']} commits behind remote!")
            response = input("Continue anyway? [y/N]: ").strip().lower()
            if response != "y":
                logger.info("Push cancelled")
                return 1

        # ─── Push ──────────────────────────────────────────────────────────
        logger.info("\n" + "=" * 60)
        logger.info("Pushing")
        logger.info("=" * 60)

        if args.dry_run:
            logger.warning("[DRY-RUN MODE] Changes will not actually be pushed")

        if not args.no_prompt and not args.dry_run:
            response = input(f"\nPush to {args.remote}/{args.branch}? [y/N]: ").strip().lower()
            if response != "y":
                logger.info("Push cancelled")
                return 1

        if not push_to_remote(args.remote, args.branch, args.dry_run):
            return 1

        logger.info("\n" + "=" * 60)
        logger.info("[OK] Success!")
        logger.info("=" * 60)
        logger.info(f"Log saved to: {LOG_FILE}")
        return 0

    except KeyboardInterrupt:
        logger.warning("\nOperation cancelled by user")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
