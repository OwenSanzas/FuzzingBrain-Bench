"""Terminal colour helpers."""
from __future__ import annotations

import os
import sys

_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _USE_COLOR else s


def bold(s: str) -> str:   return _c("1", s)
def dim(s: str) -> str:    return _c("2", s)
def green(s: str) -> str:  return _c("32", s)
def red(s: str) -> str:    return _c("31", s)
def yellow(s: str) -> str: return _c("33", s)
def cyan(s: str) -> str:   return _c("36", s)
