#!/usr/bin/env python3
"""Launcher: python -I charts.py <command> ...   (see README.md).  Isolated mode (-I) keeps stray modules out, so add the package dir explicitly."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sailwind_charts.cli import main  # noqa: E402

sys.exit(main())
