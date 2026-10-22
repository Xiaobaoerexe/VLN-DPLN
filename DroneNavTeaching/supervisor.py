#!/usr/bin/env python3
"""Foreground platform lifecycle owner; used by start.sh and manage.py start."""
import sys
from scripts.manage import main
if __name__ == '__main__':
    sys.argv.insert(1, 'run')
    raise SystemExit(main())
