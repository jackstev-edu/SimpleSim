"""Print a part's setup: python -m src.pydiscovery <part> [--json]"""

import sys

from .part_file import open_part

if len(sys.argv) < 2:
    raise SystemExit("usage: python -m src.pydiscovery <part file> [--json]")
part = open_part(sys.argv[1])
print(part.model_dump_json(indent=2) if "--json" in sys.argv else part.setup_text())
