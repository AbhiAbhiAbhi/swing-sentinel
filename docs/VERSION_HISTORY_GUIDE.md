# Git Push Helper - Version History & Audit Trail

Complete guide to tracking and auditing every push to GitHub.

## Overview

Every time you push with `git_push_helper.py`, a permanent record is created:
- **Session logs**: `~/.git_push_helper/push_YYYYMMDD_HHMMSS.log` (detailed console output)
- **Version history**: `~/.git_push_helper/version_history.json` (structured archive)

## Version History File Structure

Located at: `~/.git_push_helper/version_history.json`

```json
[
  {
    "timestamp": "2026-05-18T11:37:45.123456",
    "branch": "master",
    "remote": "origin",
    "strategy": "split",
    "num_commits": 4,
    "from_hash": "6cc830a",
    "to_hash": "299992b",
    "user": "AbhiAbhiAbhi",
    "status": "success",
    "commits": [
      {
        "hash": "00ec279",
        "message": "Add TradingView MCP server (11 tools, Playwright-driven)",
        "author": "AbhiAbhiAbhi",
        "timestamp": "2026-05-18T11:37:10.000000",
        "files_changed": 5,
        "insertions": 1109,
        "deletions": 0
      },
      {
        "hash": "9e7d21c",
        "message": "Add pre-market news pipeline with FinBERT sentiment",
        "author": "AbhiAbhiAbhi",
        "timestamp": "2026-05-18T11:37:20.000000",
        "files_changed": 3,
        "insertions": 671,
        "deletions": 0
      },
      {
        "hash": "33e111a",
        "message": "Make scan/risk thresholds UI-configurable + wire news endpoints",
        "author": "AbhiAbhiAbhi",
        "timestamp": "2026-05-18T11:37:30.000000",
        "files_changed": 4,
        "insertions": 788,
        "deletions": 52
      },
      {
        "hash": "299992b",
        "message": "Add swing-trading checklist React mockup",
        "author": "AbhiAbhiAbhi",
        "timestamp": "2026-05-18T11:37:45.000000",
        "files_changed": 1,
        "insertions": 1387,
        "deletions": 0
      }
    ],
    "session_log": "/home/user/.git_push_helper/push_20260518_113745.log",
    "error_msg": null
  },
  {
    "timestamp": "2026-05-17T14:20:30.000000",
    "branch": "master",
    "remote": "origin",
    "strategy": "bundle",
    "num_commits": 1,
    "from_hash": "abc1234",
    "to_hash": "def5678",
    "user": "AbhiAbhiAbhi",
    "status": "success",
    "commits": [...],
    "session_log": "/home/user/.git_push_helper/push_20260517_142030.log",
    "error_msg": null
  }
]
```

## Session Logs

Each push creates a detailed log file: `push_YYYYMMDD_HHMMSS.log`

**Contents:**
- Repository status (branch, commits ahead/behind)
- Staged files with changes
- Commit strategy chosen
- File groupings (for split mode)
- Each commit message and file list
- Pre-push validation results
- Git push output
- Timing and completion status

**Example:**
```
2026-05-18 11:37:45,123 [INFO] ================================================================================
2026-05-18 11:37:45,123 [INFO] Git Push Helper started. Log: /home/user/.git_push_helper/push_20260518_113745.log
2026-05-18 11:37:45,123 [INFO] ================================================================================
2026-05-18 11:37:45,456 [INFO] Repository Status
2026-05-18 11:37:45,456 [INFO] Branch: master → origin/master
2026-05-18 11:37:45,456 [INFO] Commits: 0 ahead, 0 behind
2026-05-18 11:37:45,789 [INFO] Recent commits:
2026-05-18 11:37:45,789 [INFO]   299992b Add swing-trading checklist React mockup
2026-05-18 11:37:46,012 [INFO] Staged Files
2026-05-18 11:37:46,012 [INFO] Found 13 staged file(s):
2026-05-18 11:37:46,012 [INFO]   • mcp_tradingview/server.py
2026-05-18 11:37:46,345 [INFO] Strategy: Split by logical category
2026-05-18 11:37:46,678 [INFO] Identified 4 logical group(s):
2026-05-18 11:37:46,678 [INFO]   1. MCP Server (1 file)
2026-05-18 11:37:47,901 [INFO] ✓ Committed: Add TradingView MCP server
2026-05-18 11:37:48,234 [INFO] ✓ Pushed to origin/master
2026-05-18 11:37:48,567 [INFO] Success!
```

## Analyzing Version History

### View All Pushes
```bash
# See the full version history (formatted JSON)
cat ~/.git_push_helper/version_history.json | python -m json.tool

# Pretty-print with jq (if installed)
cat ~/.git_push_helper/version_history.json | jq '.'

# Or just view raw
cat ~/.git_push_helper/version_history.json
```

### Filter by Date
```bash
# Pushes on specific date
cat ~/.git_push_helper/version_history.json | \
  python -c "import sys,json; \
  data=json.load(sys.stdin); \
  [print(v) for v in data if '2026-05-18' in v.get('timestamp', '')]"
```

### Filter by User
```bash
# Pushes by specific user
cat ~/.git_push_helper/version_history.json | \
  python -c "import sys,json; \
  data=json.load(sys.stdin); \
  [print(v) for v in data if v.get('user') == 'AbhiAbhiAbhi']"
```

### List All Commits
```bash
# Extract all commits from history
cat ~/.git_push_helper/version_history.json | \
  python -c "import sys,json; \
  data=json.load(sys.stdin); \
  [print(f\"{c['hash']} {c['message']}\") \
  for v in data for c in v.get('commits', [])]"
```

### Get Total Stats
```bash
# Total commits, insertions, deletions
cat ~/.git_push_helper/version_history.json | python3 << 'EOF'
import sys, json
data = json.load(sys.stdin)
total_commits = sum(len(v.get('commits', [])) for v in data)
total_insertions = sum(sum(c.get('insertions', 0) for c in v.get('commits', [])) for v in data)
total_deletions = sum(sum(c.get('deletions', 0) for c in v.get('commits', [])) for v in data)
print(f"Total pushes: {len(data)}")
print(f"Total commits: {total_commits}")
print(f"Total insertions: {total_insertions}")
print(f"Total deletions: {total_deletions}")
print(f"Net changes: +{total_insertions - total_deletions}")
EOF
```

## Rollback & Recovery

### Find a Previous Version
```bash
# List all pushes (human readable)
cat ~/.git_push_helper/version_history.json | python3 << 'EOF'
import sys, json
data = json.load(sys.stdin)
for i, v in enumerate(data, 1):
    ts = v.get('timestamp', '')[:19]
    print(f"{i}. {ts} | {v['user']} | {v['branch']} | {len(v['commits'])} commits | {v['from_hash']}..{v['to_hash']}")
EOF
```

Output:
```
1. 2026-05-17T14:20:30 | AbhiAbhiAbhi | master | 1 commits | abc1234..def5678
2. 2026-05-18T11:37:45 | AbhiAbhiAbhi | master | 4 commits | 6cc830a..299992b
```

### Rollback to Previous Push
```bash
# Get the hash before a problematic push
BEFORE_HASH=$(cat ~/.git_push_helper/version_history.json | \
  python3 -c "import sys,json; \
  data=json.load(sys.stdin); \
  print(data[-2]['from_hash'])")  # Previous push's from_hash

# Rollback locally (doesn't affect remote yet)
git reset --hard $BEFORE_HASH

# Review changes
git log --oneline

# Force push to revert remote (use with caution!)
git push -f origin master
```

### Detailed Rollback Info
```bash
# See exactly what would be undone
cat ~/.git_push_helper/version_history.json | python3 << 'EOF'
import sys, json
data = json.load(sys.stdin)
last_push = data[-1]
print(f"Last push committed:")
for c in last_push['commits']:
    print(f"  {c['hash']} | {c['message']}")
    print(f"    Files: {c['files_changed']}, +{c['insertions']}/-{c['deletions']}")
print(f"\nRollback command:")
print(f"  git reset --hard {last_push['from_hash']}")
print(f"  git push -f origin {last_push['branch']}")
EOF
```

## Audit Trail for Teams

### Who pushed what, when?
```bash
cat ~/.git_push_helper/version_history.json | python3 << 'EOF'
import sys, json
from datetime import datetime
data = json.load(sys.stdin)
for v in data:
    ts = datetime.fromisoformat(v['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
    print(f"{ts} | {v['user']} | {v['branch']} → {v['remote']}")
    print(f"  Strategy: {v['strategy']}, Commits: {v['num_commits']}")
    if v['status'] != 'success':
        print(f"  Status: FAILED - {v['error_msg']}")
    print()
EOF
```

### Commit Velocity
```bash
# Commits per week
cat ~/.git_push_helper/version_history.json | python3 << 'EOF'
import sys, json
from datetime import datetime
from collections import defaultdict
data = json.load(sys.stdin)
by_week = defaultdict(int)
for v in data:
    ts = datetime.fromisoformat(v['timestamp'])
    week = ts.strftime('%Y-W%U')
    by_week[week] += v['num_commits']
for week in sorted(by_week):
    print(f"Week {week}: {by_week[week]} commits")
EOF
```

### Large Changes Detection
```bash
# Find pushes with major changes
cat ~/.git_push_helper/version_history.json | python3 << 'EOF'
import sys, json
data = json.load(sys.stdin)
for v in data:
    total_insertions = sum(c.get('insertions', 0) for c in v['commits'])
    total_deletions = sum(c.get('deletions', 0) for c in v['commits'])
    total_changes = total_insertions + total_deletions
    if total_changes > 1000:
        ts = v['timestamp'][:19]
        print(f"{ts} | {v['user']} | +{total_insertions}/-{total_deletions} | {v['num_commits']} commits")
        for c in v['commits']:
            print(f"    {c['hash']} {c['message'][:60]}")
EOF
```

## Backup & Export

### Backup version history
```bash
# Daily backup
cp ~/.git_push_helper/version_history.json \
   ~/.git_push_helper/backup_version_history_$(date +%Y%m%d).json
```

### Export to CSV
```bash
cat ~/.git_push_helper/version_history.json | python3 << 'EOF'
import sys, json, csv
data = json.load(sys.stdin)
writer = csv.writer(sys.stdout)
writer.writerow(['Timestamp', 'User', 'Branch', 'Remote', 'Commits', 'Insertions', 'Deletions'])
for v in data:
    total_ins = sum(c.get('insertions', 0) for c in v['commits'])
    total_del = sum(c.get('deletions', 0) for c in v['commits'])
    writer.writerow([
        v['timestamp'][:19],
        v['user'],
        v['branch'],
        v['remote'],
        v['num_commits'],
        total_ins,
        total_del,
    ])
EOF
```

### Export to Excel/Google Sheets
```bash
# Save CSV then open in Excel/Sheets
cat ~/.git_push_helper/version_history.json | python3 << 'EOF'
import sys, json, csv
data = json.load(sys.stdin)
with open('git_push_history.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['Timestamp', 'User', 'Branch', 'Commits', 'Message', 'Files', '+Lines', '-Lines'])
    for v in data:
        for c in v['commits']:
            writer.writerow([
                v['timestamp'][:19],
                v['user'],
                v['branch'],
                v['num_commits'],
                c['message'][:80],
                c['files_changed'],
                c['insertions'],
                c['deletions'],
            ])
print("Saved to: git_push_history.csv")
EOF
```

## Session Logs for Debugging

### Find a specific push session
```bash
# List all session logs by date
ls -lhtr ~/.git_push_helper/push_*.log | tail -10

# Open the most recent log
cat ~/.git_push_helper/push_*.log | tail -1 | tail -100

# Search logs for errors
grep -r "ERROR\|FAILED" ~/.git_push_helper/push_*.log

# Find session for specific commit
grep -l "299992b" ~/.git_push_helper/push_*.log
```

### Analyze a session log
```bash
# Count log lines by level
grep "\[INFO\]\|\[ERROR\]\|\[WARNING\]" ~/.git_push_helper/push_*.log | \
  cut -d'[' -f2 | cut -d']' -f1 | sort | uniq -c

# Timeline of a session
grep "\[INFO\]" ~/.git_push_helper/push_YYYYMMDD_HHMMSS.log | \
  awk '{print $1, $2, $NF}' | head -20
```

## Integration with CI/CD

### Log to a central repository
```bash
# Archive version history in git (optional)
git add ~/.git_push_helper/version_history.json
git commit -m "Update version history"
git push

# Or copy to .git_history in repo
cp ~/.git_push_helper/version_history.json ./.git_history
git add .git_history
git commit -m "Log: version history update"
```

### GitHub Actions Integration
```yaml
# .github/workflows/log-push.yml
name: Log Push History
on: [push]
jobs:
  log:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Collect version history
        run: |
          # Copy from local if available
          mkdir -p .github/history
          cp ~/.git_push_helper/version_history.json .github/history/ 2>/dev/null || echo "No history yet"
      - name: Commit history
        run: |
          git config user.email "ci@example.com"
          git config user.name "CI Bot"
          git add .github/history/ 2>/dev/null || true
          git commit -m "ci: update push history" 2>/dev/null || true
          git push
```

## Retention Policy

Version history grows over time. Implement cleanup:

```bash
#!/bin/bash
# Keep last 90 days of version history

cat ~/.git_push_helper/version_history.json | python3 << 'EOF'
import sys, json
from datetime import datetime, timedelta

data = json.load(sys.stdin)
cutoff = datetime.now() - timedelta(days=90)

filtered = [
    v for v in data
    if datetime.fromisoformat(v['timestamp']) > cutoff
]

# Backup old data
with open(f'{Path.home()}/.git_push_helper/archived_history.json', 'w') as f:
    json.dump([v for v in data if v not in filtered], f)

# Write cleaned history
with open(f'{Path.home()}/.git_push_helper/version_history.json', 'w') as f:
    json.dump(filtered, f)

print(f"Kept: {len(filtered)} pushes (last 90 days)")
print(f"Archived: {len(data) - len(filtered)} pushes")
EOF
```

---

**Summary**: Every push is logged with full commit history, file changes, timestamps, and user info. Use version_history.json for audits, rollbacks, and analysis. Use session logs for detailed debugging.
