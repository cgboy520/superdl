"""Export MESSAGES as i18next JSON to packages/ui/locales/en-US/errors.json."""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.messages import MESSAGES

OUT = Path(__file__).resolve().parents[3] / "packages" / "ui" / "locales" / "en-US" / "errors.json"


def to_i18next(template: str) -> str:
    return re.sub(r"\{(\w+)\}", r"{{\1}}", template)


def main() -> None:
    tree: dict = {}
    for key in sorted(MESSAGES):
        node = tree
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = to_i18next(MESSAGES[key])
    OUT.write_text(
        json.dumps(tree, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sys.stdout.write(f"wrote {OUT}\n")


if __name__ == "__main__":
    main()
