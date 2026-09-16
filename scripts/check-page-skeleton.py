#!/usr/bin/env python3
"""Check that non-exempt console route files contain the PageContainer text.

Scans apps/web/src/routes/_console*.tsx and apps/admin/src/routes/_app/*.tsx,
skipping the exempt files in TARGETS and non-route fragments whose file name starts with '-'.
"""

import pathlib

TARGETS = (
    ("apps/web/src/routes", "_console*.tsx", {"_console.tsx"}),
    ("apps/admin/src/routes/_app", "*.tsx", {"cluster.$component.tsx"}),
)


def main() -> int:
    missing: list[str] = []
    checked = 0
    for folder, pattern, skip in TARGETS:
        for path in sorted(pathlib.Path(folder).glob(pattern)):
            if path.name.startswith("-") or path.name in skip:
                continue
            checked += 1
            if "PageContainer" not in path.read_text(encoding="utf-8"):
                missing.append(path.as_posix())
    for m in missing:
        print(f"missing PageContainer: {m}")
    if missing:
        return 1
    print(f"page skeleton check passed ({checked} route pages)")
    return 0


raise SystemExit(main())
