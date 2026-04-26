"""DLP scan — detecta padrões de PII no código-fonte."""

import pathlib
import re
import sys

from security.output_validators import _PII_PATTERNS

skip_dirs = {".git", ".github", "__pycache__", "tests", "scripts"}
violations = []

for f in pathlib.Path(".").rglob("*.py"):
    if any(p in f.parts for p in skip_dirs):
        continue
    text = f.read_text(errors="ignore")
    for pat, _ in _PII_PATTERNS:
        if re.search(pat, text):
            violations.append(str(f))
            break

if violations:
    print("PII patterns found in:", violations)
    sys.exit(1)

print("DLP scan passed")
