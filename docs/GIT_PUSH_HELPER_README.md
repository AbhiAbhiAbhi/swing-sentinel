# Git Push Helper

A Python CLI tool that intelligently analyzes staged changes, groups them into logical commits, and pushes to GitHub with clean logging.

## Features

✓ **Smart categorization**: Automatically groups files by type (MCP, backend, frontend, tests, data, config, docs)  
✓ **Interactive or automated**: Choose between bundled or split commits, customize messages  
✓ **Clean logging**: Dual output to console + timestamped file in `~/.git_push_helper/`  
✓ **Safety checks**: Pre-push validation, branch tracking, behind/ahead status  
✓ **Dry-run mode**: Test push logic without actually pushing  

## Installation

```bash
# Just one file - no dependencies beyond Python 3.7+
cp git_push_helper.py ~/.local/bin/  # or anywhere in your PATH
chmod +x ~/.local/bin/git_push_helper.py
```

## Usage

### Interactive (default) - prompts you for strategy
```bash
python git_push_helper.py
# Stages changes → groups by category → lets you customize each message → pushes
```

### Bundle all into one commit
```bash
python git_push_helper.py --strategy bundle --message "My changes"
```

### Split by category (no prompts)
```bash
python git_push_helper.py --strategy split --no-prompt
```

### Dry-run (simulate without pushing)
```bash
python git_push_helper.py --dry-run
```

### Custom remote/branch
```bash
python git_push_helper.py --remote upstream --branch develop
```

## Examples

**Example 1: Interactive split (typical workflow)**
```bash
$ python git_push_helper.py
Repository Status
=================
Branch: master → origin/master
Commits: 0 ahead, 0 behind
Recent commits:
  299992b Add swing-trading checklist React mockup
  33e111a Make scan/risk thresholds UI-configurable + wire news endpoints
  9e7d21c Add pre-market news pipeline with FinBERT sentiment

Staged Files
============
Found 13 staged file(s):
  • mcp_tradingview/server.py
  • core_news_pipeline.py
  • core_chartink_fetcher.py
  • dashboard/swing_agent_app.html
  • (... etc ...)

Commit Strategy
===============
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
  Message [MCP Server: MCP server and integration]: Add TradingView MCP (11 tools)
  [master 00ec279] Add TradingView MCP (11 tools)

Committing: Backend Changes
  Files: core_news_pipeline.py, core_chartink_fetcher.py
  Message [Backend Changes: Core backend logic and APIs]: Add news pipeline + configurable filters
  [master 9e7d21c] Add news pipeline + configurable filters

... (continues for other groups)

Pre-Push Checks
===============
Current branch: master
Remote branch: origin/master

Pushing
=======
Push to origin/master? [y/N]: y
✓ Pushed to origin/master

Success!
========
Log saved to: /home/user/.git_push_helper/push_20260518_113645.log
```

**Example 2: Automated bundle**
```bash
$ python git_push_helper.py --strategy bundle --message "Feature: add user auth" --no-prompt
[INFO] Staged 13 file(s)
[INFO] Strategy: Bundle all into one commit
[INFO] ✓ Committed: Feature: add user auth
[INFO] ✓ Pushed to origin/master
Log saved to: ~/.git_push_helper/push_20260518_113700.log
```

## Configuration

All settings are CLI flags - no config files needed. Common patterns:

```bash
# Alias for quick bundled pushes
alias gpush='python git_push_helper.py --strategy bundle --no-prompt'

# Alias for safe interactive pushes
alias gpush-split='python git_push_helper.py --strategy split'

# Test before pushing
alias gpush-dry='python git_push_helper.py --dry-run'
```

## Logs

Every run creates a timestamped log in `~/.git_push_helper/push_YYYYMMDD_HHMMSS.log`:
- Full command outputs
- Git operation details
- Error messages with context
- Useful for debugging or audit trail

## Troubleshooting

**"No staged changes. Nothing to commit."**  
→ Stage files first: `git add <files>`

**"Local branch is X commits behind remote!"**  
→ Pull first: `git pull origin <branch>` then re-run

**"Command failed" errors**  
→ Check the log file for full error details: `~/.git_push_helper/push_*.log`

**Script not found**  
→ Ensure it's in your PATH or run with full path: `python /path/to/git_push_helper.py`

## How it Categorizes Files

| Category | Pattern | Example |
|----------|---------|---------|
| MCP | Contains `mcp_` or `/mcp` | `mcp_tradingview/server.py` |
| Backend | `.py`, `.go`, `.java`, `.rs` | `core_news_pipeline.py` |
| Frontend | `.jsx`, `.tsx`, `.html`, `.css`, `.js` | `dashboard/app.html` |
| Tests | `*test*`, `*spec*` | `tests/test_fetch.py` |
| Data | `/data`, `.csv`, `.json` | `data/config.json` |
| Config | `settings`, `config`, `.env`, `.yaml` | `config.toml` |
| Docs | `.md`, `.txt`, `.rst` | `README.md` |
| Other | Everything else | `setup.sh` |

When splitting, each category becomes a separate commit (if it has files).

## Requirements

- Python 3.7+
- Git installed and in PATH
- No external dependencies (uses only stdlib)

## License

Use freely. No warranty.
