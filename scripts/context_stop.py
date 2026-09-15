#!/usr/bin/env python3
"""
context_stop — shut down background context server(s).

Usage:
  python3 context_stop.py              # stop ALL running instances
  python3 context_stop.py <instance>   # stop specific instance (e.g. "AbCd123-1-23")

Reads .figma-to-code/5-context-server/.server-{instance}.pid for each instance.

Exit codes:
  0 — all servers stopped (or none were running)
  1 — no pid files found
  2 — could not kill a process
"""

from __future__ import annotations

import glob
import os
import sys
import time
from pathlib import Path

CACHE_ROOT = Path.cwd() / '.figma-to-code' / '5-context-server'


def _stop_one(pid_file: Path) -> bool:
    if not pid_file.exists():
        return True
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return True

    # Check if alive
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        return True
    except PermissionError:
        print(f'permission denied signalling pid {pid}', file=sys.stderr)
        return False

    # SIGTERM
    try:
        os.kill(pid, 15)
    except OSError as e:
        print(f'SIGTERM failed for pid {pid}: {e}', file=sys.stderr)
        return False

    # Wait up to 3s
    for _ in range(15):
        time.sleep(0.2)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print(f'server pid {pid} stopped')
            pid_file.unlink(missing_ok=True)
            port_file = pid_file.with_suffix('.port')
            port_file.unlink(missing_ok=True)
            return True

    # Force kill
    try:
        os.kill(pid, 9)
        print(f'server pid {pid} killed (SIGKILL)')
    except OSError as e:
        print(f'SIGKILL failed for pid {pid}: {e}', file=sys.stderr)
        return False

    pid_file.unlink(missing_ok=True)
    return True


def main():
    if not CACHE_ROOT.exists():
        print('no context server cache dir found — nothing to stop')
        sys.exit(1)

    # Specific instance or all?
    if len(sys.argv) > 1:
        instance_id = sys.argv[1]
        pid_file = CACHE_ROOT / f'.server-{instance_id}.pid'
        if not pid_file.exists():
            print(f'no pid file for instance "{instance_id}" — nothing to stop')
            sys.exit(1)
        ok = _stop_one(pid_file)
        sys.exit(0 if ok else 2)
    else:
        # Stop all instances
        pid_files = list(CACHE_ROOT.glob('.server-*.pid'))
        if not pid_files:
            print('no running server instances found')
            sys.exit(0)
        all_ok = True
        for pf in pid_files:
            if not _stop_one(pf):
                all_ok = False
        print(f'stopped {len(pid_files)} instance(s)')
        sys.exit(0 if all_ok else 2)


if __name__ == '__main__':
    main()
