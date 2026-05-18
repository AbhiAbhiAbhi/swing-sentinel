#!/usr/bin/env python3
"""
Quick reference for git_push_helper.py

SETUP
─────
1. Place git_push_helper.py in your repo or ~/bin
2. Run: python git_push_helper.py  (or use gpush.bat / gpush.sh wrapper)

QUICK START
───────────
Interactive (asks you everything):
    python git_push_helper.py

Bundle all changes:
    python git_push_helper.py --strategy bundle --message "Your message" --no-prompt

Split by category:
    python git_push_helper.py --strategy split --no-prompt

Test without pushing:
    python git_push_helper.py --dry-run

COMMON PATTERNS
───────────────
# Save as aliases in your shell profile (~/.bashrc, ~/.zshrc, ~/.bash_profile)

# Quick bundle commit
alias gpush='python git_push_helper.py --strategy bundle --no-prompt'

# Interactive split (recommended)
alias gpush-split='python git_push_helper.py'

# Dry run to test
alias gpush-dry='python git_push_helper.py --dry-run'

# Push to a specific remote/branch
alias gpush-dev='python git_push_helper.py --remote origin --branch develop'

FLAGS
─────
--strategy {bundle,split,interactive}  How to organize commits (default: interactive)
--message MESSAGE                      Commit message for bundle mode
--remote REMOTE                        Git remote (default: origin)
--branch BRANCH                        Branch to push (default: master)
--dry-run                              Simulate without pushing
--no-prompt                            Skip user confirmations

WHAT IT DOES
────────────
1. Shows repo status (branch, ahead/behind, recent commits)
2. Lists all staged files
3. Analyzes files and suggests logical grouping (MCP, backend, frontend, etc.)
4. Either:
   a) Bundles everything into one commit, or
   b) Creates separate commits for each logical category (you can customize messages)
5. Validates pre-push conditions (not behind remote, etc.)
6. Pushes to remote
7. Logs everything to ~/.git_push_helper/push_*.log

EXAMPLE OUTPUT
──────────────
$ python git_push_helper.py

Repository Status
Branch: master → origin/master
Commits: 0 ahead, 0 behind
Recent commits:
  299992b Add swing-trading checklist React mockup
  33e111a Make scan/risk thresholds UI-configurable + wire news endpoints

Staged Files
Found 13 staged file(s):
  • mcp_tradingview/server.py
  • core_news_pipeline.py
  • core_chartink_fetcher.py
  • dashboard/swing_agent_app.html
  • ...

Commit Strategy
How should I structure the commits?
  → 1. Bundle all into one commit
    2. Split by logical category

Select option [1-2] (default: 2): 2

Identified 4 logical group(s):
  1. MCP Server (1 file)
  2. Backend Changes (2 files)
  3. Frontend Changes (2 files)
  4. Data Updates (3 files)

Committing: MCP Server
  Files: mcp_tradingview/server.py
  Message [MCP Server: MCP server and integration]: Add TradingView MCP server
  [master 00ec279] Add TradingView MCP server

Committing: Backend Changes
  Files: core_news_pipeline.py, core_chartink_fetcher.py
  Message [Backend Changes: Core backend logic and APIs]:
  [master 9e7d21c] Add news pipeline + configurable filters

Pre-Push Checks
Current branch: master
Remote branch: origin/master

Pushing
Push to origin/master? [y/N]: y
✓ Pushed to origin/master

Success!
Log saved to: /home/user/.git_push_helper/push_20260518_113645.log

LOGS
────
Logs are saved automatically to:
  ~/.git_push_helper/push_YYYYMMDD_HHMMSS.log

View latest log:
  tail ~/.git_push_helper/push_*.log

Or open in editor:
  vim ~/.git_push_helper/push_*.log

TROUBLESHOOTING
───────────────
Q: "No staged changes"
A: Stage files first: git add <files>

Q: "Command failed: git push"
A: Check the log file for details. Usually: fetch/pull first, check permissions.

Q: "Branch is behind remote"
A: Pull first: git pull origin <branch>

Q: Script not in PATH
A: Use full path: python /path/to/git_push_helper.py
   Or add directory to PATH in your shell profile

REQUIREMENTS
────────────
• Python 3.7+
• Git installed
• No external dependencies (uses only stdlib)
• Works on Windows, macOS, Linux
"""

print(__doc__)
