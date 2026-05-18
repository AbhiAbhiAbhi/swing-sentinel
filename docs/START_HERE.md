# Git Push Helper Suite - Complete Installation

**9 files totaling ~78 KB | Zero external dependencies | Works on Windows/Mac/Linux**

## 📦 What You Have

### Core Tools
| File | Size | Purpose |
|------|------|---------|
| `git_push_helper.py` | 17K | Main tool: Smart commits + push with logging |
| `view-history.py` | 7K | View/analyze version history and statistics |
| `git_version_logger.py` | 20K | Version tracking backend (reference) |
| `gpush.bat` | 1K | Windows shortcut (run as: `gpush` or `gpush.bat`) |
| `gpush.sh` | 1K | Unix/Mac shortcut (run as: `./gpush.sh` or `gpush`) |

### Documentation
| File | Size | Contents |
|------|------|----------|
| `README_GIT_TOOLS.md` | 11K | **START HERE** - Overview and workflows |
| `GIT_PUSH_HELPER_README.md` | 5K | Detailed push tool guide |
| `VERSION_HISTORY_GUIDE.md` | 13K | Version tracking, auditing, rollbacks |
| `QUICKSTART.py` | 4K | Quick reference (run to display) |

## 🚀 Getting Started (30 seconds)

### Step 1: One-time setup
```bash
# On Windows
set PATH=%PATH%;C:\path\to\git_push_helper

# On Mac/Linux
export PATH="$PATH:$HOME/git_push_helper"
# Add above line to ~/.bashrc or ~/.zshrc to make permanent
```

### Step 2: Stage your changes
```bash
git add .
```

### Step 3: Push with logging
```bash
# Interactive mode (asks you everything)
python git_push_helper.py

# Or use wrapper
gpush                      # Windows
./gpush.sh                 # Mac/Linux
```

### Step 4: View history anytime
```bash
python view-history.py              # All pushes
python view-history.py show 1       # Details of push #1
python view-history.py stats        # Statistics
```

## 📋 Feature Summary

### ✅ Smart Commit Grouping
Automatically organizes files by type:
```
MCP Server (mcp_tradingview/server.py) → separate commit
Backend Changes (core_*.py) → separate commit
Frontend Changes (*.jsx, *.html) → separate commit
Data Updates (data/*.json) → separate commit
```

### ✅ Full Audit Trail
Every push is logged with:
- Commit hashes, messages, authors
- File changes (+insertions, -deletions)
- Timestamps, user info, strategy used
- Session logs for detailed debugging

### ✅ Multiple Strategies
```bash
--strategy bundle      # One commit for everything
--strategy split       # Separate commits per category
--strategy interactive # Ask which one to use (default)
```

### ✅ Safety Checks
- Validates branch sync before push
- Detects untracked changes
- Confirmations before risky operations
- Dry-run mode for testing

## 🎯 Common Commands

### Recommended: Interactive Split
```bash
$ python git_push_helper.py
# Asks: bundle or split?
# Chooses split, groups files, lets you customize each commit message
# Pushes 4 separate commits with logging
```

### Fast: Bundle Everything
```bash
$ python git_push_helper.py --strategy bundle --message "v1.2 release" --no-prompt
# One commit, instant push
```

### Safe: Test Before Push
```bash
$ python git_push_helper.py --dry-run
# Shows what would happen without actually pushing
```

### Analysis: View History
```bash
$ python view-history.py              # All pushes
$ python view-history.py show 1       # Details
$ python view-history.py stats        # Statistics
$ python view-history.py export csv   # Excel/Sheets
```

## 📊 Logging

### Session Logs (detailed)
Located: `~/.git_push_helper/push_YYYYMMDD_HHMMSS.log`
- Full console output of each push
- Repository status, file lists, commit messages
- Git command outputs
- Useful for debugging

**Example contents:**
```
2026-05-18 11:37:45 [INFO] Repository Status
2026-05-18 11:37:45 [INFO] Branch: master → origin/master
2026-05-18 11:37:45 [INFO] Commits: 0 ahead, 0 behind
2026-05-18 11:37:46 [INFO] Found 13 staged file(s)
2026-05-18 11:37:47 [INFO] Strategy: Split by logical category
2026-05-18 11:37:48 [INFO] ✓ Committed: Add TradingView MCP server
2026-05-18 11:37:49 [INFO] ✓ Pushed to origin/master
```

### Version History (structured)
Located: `~/.git_push_helper/version_history.json`
- Persistent JSON archive of all pushes
- Organized by timestamp, branch, user
- Includes all commit info (hashes, messages, stats)
- Supports auditing, analysis, rollbacks

**Example structure:**
```json
{
  "timestamp": "2026-05-18T11:37:45.123456",
  "branch": "master",
  "remote": "origin",
  "strategy": "split",
  "num_commits": 4,
  "commits": [
    {
      "hash": "00ec279",
      "message": "Add TradingView MCP server",
      "author": "AbhiAbhiAbhi",
      "insertions": 1109,
      "deletions": 0
    },
    ...
  ],
  "status": "success"
}
```

## 🔧 Setup (Optional)

### Make it globally accessible

**Windows:**
```cmd
# Add to PATH or copy to C:\Windows\System32
copy git_push_helper.py C:\Windows\System32\
copy gpush.bat C:\Windows\System32\

# Then use from anywhere
gpush
```

**Mac/Linux:**
```bash
# Copy to /usr/local/bin or ~/bin
cp git_push_helper.py ~/bin/
cp gpush.sh ~/bin/gpush
chmod +x ~/bin/gpush

# Then use from anywhere
gpush
```

### Add shell aliases
Add to `~/.bashrc`, `~bashrc`, or `~/.bash_profile`:

```bash
# Quick push (bundle, no prompts)
alias gpush='python git_push_helper.py --strategy bundle --no-prompt'

# Interactive split (recommended)
alias gpush-split='python git_push_helper.py'

# View history
alias ghist='python view-history.py'
alias ghist-stats='python view-history.py stats'
```

Then use:
```bash
gpush              # Bundle push
gpush-split        # Interactive split
ghist              # View all pushes
ghist-stats        # Show statistics
```

## 📖 Next Steps

1. **Read this first**: `README_GIT_TOOLS.md` (workflows and examples)
2. **Try the tool**: `python git_push_helper.py` (interactive mode)
3. **View history**: `python view-history.py` (see your pushes logged)
4. **Deep dive**: `GIT_PUSH_HELPER_README.md` (detailed options)
5. **Auditing**: `VERSION_HISTORY_GUIDE.md` (version tracking)
6. **Quick reference**: `python QUICKSTART.py` (display in terminal)

## ✨ Examples

### Example 1: First Push (Interactive)
```bash
$ git add .
$ python git_push_helper.py

Repository Status
Branch: master → origin/master

Staged Files
Found 13 file(s)...

Commit Strategy
  → 1. Bundle all into one commit
    2. Split by logical category
Select: 2

Identified 4 groups:
  1. MCP Server (1 file)
  2. Backend (2 files)
  3. Frontend (2 files)
  4. Data (3 files)

Committing: MCP Server
  Message [default]: Add TradingView MCP
  ✓ Committed

Committing: Backend
  Message [default]:
  ✓ Committed

... (continues for other groups)

✓ Pushed to origin/master
Log: ~/.git_push_helper/push_20260518_113745.log
```

### Example 2: Quick Bundle
```bash
$ git add .
$ python git_push_helper.py --strategy bundle --message "Hotfix: auth bug" --no-prompt

[INFO] ✓ Committed: Hotfix: auth bug
[INFO] ✓ Pushed to origin/master
```

### Example 3: View Your Work
```bash
$ python view-history.py

#   Date                User         Commits  Changes        Status
1   2026-05-18 11:37:45 AbhiAbhiAbhi 4        +3955/-52      ✓ OK
2   2026-05-17 14:20:30 AbhiAbhiAbhi 1        +142/-8        ✓ OK
3   2026-05-17 10:15:00 AbhiAbhiAbhi 2        +89/-12        ✓ OK

$ python view-history.py stats

Overall:
  Total pushes: 3
  Total commits: 7
  Total insertions: +4186
  Total deletions: -72
  Net changes: +4114
```

## 🔐 Security & Privacy

✓ **No external dependencies** — only Python stdlib (subprocess, json, logging, argparse)  
✓ **No cloud uploads** — everything stays on your computer  
✓ **No credentials stored** — only logs commits (not passwords/tokens)  
✓ **Local audit trail** — full history in `~/.git_push_helper/`  
✓ **Reversible** — all operations can be undone  

## 📌 Key Differences from Standard Git

| Feature | Git | This Suite |
|---------|-----|-----------|
| Automatic file grouping | ❌ | ✅ Smart categorization |
| Suggested commit messages | ❌ | ✅ Automatic + customizable |
| Full push logging | ❌ | ✅ Session + version history |
| Audit trail | Limited | ✅ Complete JSON history |
| Pre-push checks | Basic | ✅ Comprehensive validation |
| Interactive prompts | ❌ | ✅ Optional |
| Rollback info | ❌ | ✅ Automatic logging |
| Statistics | ❌ | ✅ User, branch, time-based |
| Export capabilities | ❌ | ✅ CSV, JSON, filtered |

## 🆘 Help & Troubleshooting

**Command not found?**
```bash
# Use full path
python /path/to/git_push_helper.py

# Or add to PATH and restart terminal
export PATH="$PATH:/path/to/dir"
```

**"No staged changes"?**
```bash
# Stage files first
git add .
```

**"Branch is behind remote"?**
```bash
# Pull first
git pull origin master
# Then retry
python git_push_helper.py
```

**Want more details?**
```bash
python QUICKSTART.py         # Quick ref
cat GIT_PUSH_HELPER_README.md      # Detailed guide
cat VERSION_HISTORY_GUIDE.md       # Auditing guide
```

## 📞 Support

For issues:
1. Check session log: `~/.git_push_helper/push_*.log`
2. Read `GIT_PUSH_HELPER_README.md` troubleshooting section
3. Review `VERSION_HISTORY_GUIDE.md` for version/audit questions
4. Run with `--dry-run` to test without pushing

---

**Status**: ✅ Ready to use. All 9 files installed and documented.

**Next command to run:**
```bash
python git_push_helper.py
```

Enjoy clean, logged commits! 🎉
