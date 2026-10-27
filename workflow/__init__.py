"""Portable, file-based site-analysis workflow."""

from pathlib import Path
import sys

# Optional dependencies may be installed locally without changing the desktop's Python.
vendor = Path(__file__).resolve().parents[1] / ".vendor"
if vendor.is_dir():
    sys.path.append(str(vendor))

__version__ = "0.4.0"
TITLE = "图省事 01｜建筑景观规划·分析图工作流"
