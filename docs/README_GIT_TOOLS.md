# Git Push Helper Suite

Complete Python toolkit for intelligent, logged Git commits and pushes with full version history tracking.

## What's Included

| Tool | Purpose | File |
|------|---------|------|
| **git_push_helper.py** | Main tool: Smart commit grouping + push with logging | `git_push_helper.py` |
| **view-history.py** | View/analyze version history and statistics | `view-history.py` |
| **gpush.bat** | Windows shortcut wrapper | `gpush.bat` |
| **gpush.sh** | Unix/Mac shortcut wrapper | `gpush.sh` |
| **Documentation** | Full guides and examples | `*.md`, `*.py` |

## Quick Start

### Installation
```bash
# Clone or download all files to your project (or ~/bin)
cd ~/my-project
git clone <repo> .git_push_helper_tools
# Or just copy: git_push_helper.py, gpush.bat, gpush.sh

# Make executable
chmod +x git_push_helper.py gpush.sh view-history.py
```

### Basic Usage
```bash
# Interactive mode (recommended)
python git_push_helper.py

# Or use wrapper
./gpush.sh                    # Unix/Mac
gpush.bat                     # Windows

# Bundle all changes into one commit
python git_push_helper.py --strategy bundle --message "Feature: add auth" --no-prompt

# Split by category (MCP, backend, frontend, etc.)
python git_push_helper.py --strategy split --no-prompt
```

## Features

### 🎯 Smart Commit Grouping
Automatically categorizes files by type:
- **MCP**: Machine-readable content, MCP servers
- **Backend**: `.py`, `.go`, `.java`, `.rs`, etc.
- **Frontend**: `.jsx`, `.tsx`, `.html`, `.css`, `.js`
- **Tests**: `*test*`, `*spec*` files
- **Data**: CSV, JSON, data files
- **Config**: Settings, environment files
- **Docs**: Markdown, text files

### 📝 Full Logging
Two levels of logging for every push:

**Session Logs** (~/.git_push_helper/push_*.log):
- Detailed console output of the entire process
- Repository status, file lists, commit messages
- Git command outputs and timestamps
- Useful for debugging specific push sessions

**Version History** (~/.git_push_helper/version_history.json):
- Structured JSON archive of all pushes
- Commit hashes, messages, authors, file changes
- Timestamps, user info, strategy used
- Allows analysis, auditing, and rollbacks

### 🛡️ Safety Checks
- Validates branch sync before push
- Checks for untracked changes
- Pre-push validations
- Confirmations before destructive operations
- Dry-run mode to test without pushing

### 🚀 Multiple Strategies
1. **Interactive** (default): Asks for strategy, lets you customize each commit message
2. **Bundle**: All changes in one commit
3. **Split**: Separate commits per logical category

## Command Examples

### Push with Interactive Splits
```bash
$ python git_push_helper.py

Repository Status
Branch: master → origin/master
Commits: 0 ahead, 0 behind

Staged Files
Found 13 file(s)...

Commit Strategy
  → 1. Bundle all into one commit
    2. Split by logical category

Select: 2

Identified 4 logical group(s):
  1. MCP Server (1 file)
  2. Backend Changes (2 files)
  3. Frontend Changes (2 files)
  4. Data Updates (3 files)

Committing: MCP Server
  Files: mcp_tradingview/server.py
  Message [MCP Server: MCP server and integration]: Add TradingView MCP (11 tools)
  ✓ Committed: Add TradingView MCP (11 tools)

... (continues for other groups)

✓ Pushed to origin/master
Log saved to: ~/.git_push_helper/push_20260518_113745.log
```

### Push Bundled
```bash
$ python git_push_helper.py --strategy bundle --message "v1.2: Feature complete" --no-prompt

[INFO] Staged 13 file(s)
[INFO] ✓ Committed: v1.2: Feature complete
[INFO] ✓ Pushed to origin/master
```

### Test Before Pushing
```bash
$ python git_push_helper.py --dry-run

[DRY-RUN MODE] Changes will not actually be pushed
```

### Push to Different Remote/Branch
```bash
$ python git_push_helper.py --remote upstream --branch develop
```

## Version History Commands

### View All Pushes
```bash
$ python view-history.py

# Output:
#   Date                User         Branch     Remote   Commits  Changes        Status
1   2026-05-18 11:37:45 AbhiAbhiAbhi master     origin   4        +3955/-52      ✓ OK
2   2026-05-17 14:20:30 AbhiAbhiAbhi master     origin   1        +142/-8        ✓ OK
```

### View Push Details
```bash
$ python view-history.py show 1

Version 1 - 2026-05-18 11:37:45
================================

Metadata:
  User:       AbhiAbhiAbhi
  Branch:     master → origin/master
  Strategy:   split
  Status:     success

Range:
  From:       6cc830a
  To:         299992b

Commits (4):
  1. 00ec279 - Add TradingView MCP server (11 tools, Playwright-driven)
     Files:     5
     Changes:   +1109/-0

  2. 9e7d21c - Add pre-market news pipeline with FinBERT sentiment
     Files:     3
     Changes:   +671/-0

  ... (more commits)
```

### View Statistics
```bash
$ python view-history.py stats

Version History Statistics
==========================

Overall:
  Total pushes:      42
  Total commits:     127
  Total insertions:  +18,542
  Total deletions:   -1,203
  Net changes:       +17,339

By User:
  AbhiAbhiAbhi:
    Pushes:  42
    Commits: 127
    Changes: +18,542/-1,203

By Branch:
  master: 42 pushes, 127 commits
  develop: 0 pushes, 0 commits
```

### Export History
```bash
# As CSV (for Excel/Sheets)
python view-history.py export csv > git_push_history.csv

# As JSON (for processing)
python view-history.py export json | jq '.[]'
```

## Practical Workflows

### Workflow 1: Safe Interactive Splits
```bash
# Stage your changes
git add .

# Push interactively, customizing each commit message
python git_push_helper.py

# Review the push history
python view-history.py stats
```

### Workflow 2: Automated CI/CD Pipeline
```bash
# In your CI script
python git_push_helper.py \
  --strategy split \
  --remote upstream \
  --branch develop \
  --no-prompt

# Verify it was logged
python view-history.py show 1
```

### Workflow 3: Team Auditing
```bash
# Who pushed what, when?
python view-history.py | grep "AbhiAbhiAbhi"

# Export for compliance
python view-history.py export csv > compliance_audit_$(date +%Y%m%d).csv

# Find large changes
python view-history.py stats | grep "insertions"
```

### Workflow 4: Rollback Recovery
```bash
# See what was pushed recently
python view-history.py show 1

# Get the commit hash before the problematic push
FROM_HASH=$(python view-history.py export json | jq '.[0].from_hash' | tr -d '"')

# Rollback locally
git reset --hard $FROM_HASH

# Force push to revert remote (use with caution!)
git push -f origin master
```

## Files & Locations

### Generated Files
- **Session logs**: `~/.git_push_helper/push_YYYYMMDD_HHMMSS.log` (one per push)
- **Version history**: `~/.git_push_helper/version_history.json` (persistent)

### Tool Files
- `git_push_helper.py` — Main tool (17 KB)
- `view-history.py` — History viewer (7 KB)
- `gpush.bat` — Windows wrapper (1 KB)
- `gpush.sh` — Unix wrapper (1 KB)
- `GIT_PUSH_HELPER_README.md` — Detailed guide
- `VERSION_HISTORY_GUIDE.md` — Version tracking guide
- `QUICKSTART.py` — Quick reference

## Shell Aliases

Add to `~/.bashrc`, `~/.zshrc`, or `~/.bash_profile`:

```bash
# Quick push (bundle, no prompts)
alias gpush='python ~/git_push_helper.py --strategy bundle --no-prompt'

# Interactive split (recommended)
alias gpush-split='python ~/git_push_helper.py'

# Test push without actually pushing
alias gpush-dry='python ~/git_push_helper.py --dry-run'

# View history
alias ghist='python ~/view-history.py'
alias ghist-stats='python ~/view-history.py stats'

# View last 5 pushes
alias ghist-recent='python ~/view-history.py | head -8'
```

Then use:
```bash
gpush                    # Bundle push
gpush-split             # Interactive split
gpush --dry-run         # Test
ghist                   # View all pushes
ghist show 1            # Show push #1
ghist-stats             # Statistics
```

## Configuration

No configuration file needed — all settings are CLI flags:

```
--strategy {bundle,split,interactive}  Commit strategy (default: interactive)
--message MESSAGE                      Commit message (for bundle mode)
--remote REMOTE                        Git remote (default: origin)
--branch BRANCH                        Branch (default: master)
--dry-run                              Test without pushing
--no-prompt                            Skip confirmations
```

## Requirements

- **Python 3.7+**
- **Git** installed and in PATH
- **No external dependencies** (uses only stdlib: subprocess, json, logging, argparse, etc.)
- Works on **Windows, macOS, Linux**

## Troubleshooting

### "No staged changes"
→ Stage files first: `git add <files>` then run tool

### "Command failed: git push"
→ Check the session log: `~/.git_push_helper/push_*.log`
→ Usually: pull first (`git pull origin <branch>`), check permissions

### "Branch is behind remote"
→ Pull first: `git pull origin <branch>` then retry

### "Version history not found"
→ First push hasn't been logged yet. Run once and try again.

### Script not found
→ Use full path: `python /path/to/git_push_helper.py`
→ Or add directory to PATH in your shell profile

## Examples

See example outputs in:
- `GIT_PUSH_HELPER_README.md` — Push examples
- `VERSION_HISTORY_GUIDE.md` — Audit and analysis examples
- `QUICKSTART.py` — Quick reference with usage patterns

## Performance

- **Small pushes** (< 50 files): < 5 seconds
- **Large pushes** (> 1000 files): < 30 seconds
- **History analysis** (all pushes): < 2 seconds
- **Logs**: ~50 KB per push session
- **Version history**: ~10 KB per push record

## Security

- ✓ No external dependencies (no supply chain risk)
- ✓ No cloud uploads (everything stays local)
- ✓ No credentials stored (just logs commits)
- ✓ Safe defaults (confirmations before destructive ops)
- ✓ Audit trail (every push is logged)

## License

Use freely. No warranty.

---

**For detailed guides, see:**
- `GIT_PUSH_HELPER_README.md` — Main tool guide
- `VERSION_HISTORY_GUIDE.md` — Version tracking & auditing
- `QUICKSTART.py` — Quick reference (run to display)

**Quick commands:**
```bash
python git_push_helper.py              # Interactive push
python view-history.py                 # View all pushes
python view-history.py show 1          # Details of push #1
python view-history.py stats           # Statistics
python view-history.py export csv      # Export to Excel
```
