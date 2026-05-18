#!/usr/bin/env python3
"""
View git version history with filtering and formatting options.
"""

import json
import sys
from datetime import datetime
from pathlib import Path


VERSION_HISTORY_FILE = Path.home() / ".git_push_helper" / "version_history.json"


def load_history():
    """Load version history."""
    if not VERSION_HISTORY_FILE.exists():
        print("No version history found.")
        print(f"Expected at: {VERSION_HISTORY_FILE}")
        sys.exit(1)

    with open(VERSION_HISTORY_FILE) as f:
        return json.load(f)


def format_timestamp(ts):
    """Format ISO timestamp to readable."""
    return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M:%S")


def print_short(history):
    """Print compact list."""
    print("\n" + "=" * 120)
    print(f"{'#':<3} {'Date':<19} {'User':<12} {'Branch':<12} {'Remote':<10} {'Commits':<8} {'Changes':<15} {'Status':<8}")
    print("=" * 120)

    for i, record in enumerate(reversed(history), 1):
        ts = format_timestamp(record['timestamp'])
        user = record['user'][:11]
        branch = record['branch'][:11]
        remote = record['remote'][:9]
        commits = record['num_commits']
        total_ins = sum(c.get('insertions', 0) for c in record['commits'])
        total_del = sum(c.get('deletions', 0) for c in record['commits'])
        changes = f"+{total_ins}/-{total_del}"
        status = "✓ OK" if record['status'] == 'success' else "✗ FAIL"

        print(f"{i:<3} {ts:<19} {user:<12} {branch:<12} {remote:<10} {commits:<8} {changes:<15} {status:<8}")

    print("=" * 120 + "\n")


def print_detailed(history, index):
    """Print detailed info for a specific version."""
    if index < 1 or index > len(history):
        print(f"Invalid index. Available: 1-{len(history)}")
        sys.exit(1)

    record = history[-(index)]

    print("\n" + "=" * 100)
    print(f"Version {index} - {format_timestamp(record['timestamp'])}")
    print("=" * 100)

    print(f"\nMetadata:")
    print(f"  User:       {record['user']}")
    print(f"  Branch:     {record['branch']} → {record['remote']}/{record['branch']}")
    print(f"  Strategy:   {record['strategy']}")
    print(f"  Status:     {record['status']}")
    if record.get('error_msg'):
        print(f"  Error:      {record['error_msg']}")

    print(f"\nRange:")
    print(f"  From:       {record['from_hash']}")
    print(f"  To:         {record['to_hash']}")

    commits = record['commits']
    print(f"\nCommits ({len(commits)}):")
    for i, c in enumerate(commits, 1):
        print(f"\n  {i}. {c['hash']} - {c['message'][:60]}")
        print(f"     Author:    {c['author']}")
        print(f"     Timestamp: {format_timestamp(c['timestamp'])}")
        print(f"     Files:     {c['files_changed']}")
        print(f"     Changes:   +{c['insertions']}/-{c['deletions']}")

    print(f"\nLog file: {record['session_log']}")
    print("=" * 100 + "\n")


def print_stats(history):
    """Print overall statistics."""
    total_pushes = len(history)
    total_commits = sum(r['num_commits'] for r in history)
    total_insertions = sum(sum(c.get('insertions', 0) for c in r['commits']) for r in history)
    total_deletions = sum(sum(c.get('deletions', 0) for c in r['commits']) for r in history)

    # By user
    by_user = {}
    for r in history:
        user = r['user']
        if user not in by_user:
            by_user[user] = {'pushes': 0, 'commits': 0, 'insertions': 0, 'deletions': 0}
        by_user[user]['pushes'] += 1
        by_user[user]['commits'] += r['num_commits']
        by_user[user]['insertions'] += sum(c.get('insertions', 0) for c in r['commits'])
        by_user[user]['deletions'] += sum(c.get('deletions', 0) for c in r['commits'])

    # By branch
    by_branch = {}
    for r in history:
        branch = r['branch']
        if branch not in by_branch:
            by_branch[branch] = {'pushes': 0, 'commits': 0}
        by_branch[branch]['pushes'] += 1
        by_branch[branch]['commits'] += r['num_commits']

    print("\n" + "=" * 60)
    print("Version History Statistics")
    print("=" * 60)

    print(f"\nOverall:")
    print(f"  Total pushes:      {total_pushes}")
    print(f"  Total commits:     {total_commits}")
    print(f"  Total insertions:  +{total_insertions}")
    print(f"  Total deletions:   -{total_deletions}")
    print(f"  Net changes:       +{total_insertions - total_deletions}")

    print(f"\nBy User:")
    for user, stats in sorted(by_user.items()):
        print(f"  {user}:")
        print(f"    Pushes:  {stats['pushes']}")
        print(f"    Commits: {stats['commits']}")
        print(f"    Changes: +{stats['insertions']}/-{stats['deletions']}")

    print(f"\nBy Branch:")
    for branch, stats in sorted(by_branch.items()):
        print(f"  {branch}: {stats['pushes']} pushes, {stats['commits']} commits")

    print("=" * 60 + "\n")


def main():
    if len(sys.argv) < 2:
        history = load_history()
        print_short(history)
        return

    command = sys.argv[1]

    if command == "--help" or command == "-h":
        print(__doc__)
        print(f"\nUsage:")
        print(f"  {sys.argv[0]}              - Show all pushes (compact)")
        print(f"  {sys.argv[0]} show <N>    - Show details of push #N")
        print(f"  {sys.argv[0]} stats       - Show statistics")
        print(f"  {sys.argv[0]} export csv  - Export as CSV")
        print(f"  {sys.argv[0]} export json - Export as JSON")
        return

    history = load_history()

    if command == "show":
        if len(sys.argv) < 3:
            print("Usage: view-history.py show <number>")
            sys.exit(1)
        try:
            index = int(sys.argv[2])
            print_detailed(history, index)
        except ValueError:
            print("Invalid number")
            sys.exit(1)

    elif command == "stats":
        print_stats(history)

    elif command == "export":
        if len(sys.argv) < 3:
            print("Usage: view-history.py export <csv|json>")
            sys.exit(1)

        fmt = sys.argv[2].lower()

        if fmt == "csv":
            import csv
            writer = csv.writer(sys.stdout)
            writer.writerow(['Timestamp', 'User', 'Branch', 'Remote', 'Strategy', 'Commits', '+Lines', '-Lines', 'Status'])
            for record in history:
                total_ins = sum(c.get('insertions', 0) for c in record['commits'])
                total_del = sum(c.get('deletions', 0) for c in record['commits'])
                writer.writerow([
                    record['timestamp'][:19],
                    record['user'],
                    record['branch'],
                    record['remote'],
                    record['strategy'],
                    record['num_commits'],
                    total_ins,
                    total_del,
                    record['status'],
                ])

        elif fmt == "json":
            print(json.dumps(history, indent=2))

        else:
            print(f"Unknown format: {fmt}")
            sys.exit(1)

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
