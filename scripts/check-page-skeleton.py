#!/usr/bin/env python3
"""检查控制台非豁免路由文件是否包含 PageContainer 文本。

扫描 apps/web/src/routes/_console*.tsx 与 apps/admin/src/routes/_app/*.tsx,
跳过 TARGETS 中的豁免文件和文件名以 '-' 开头的非路由片段。
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
        print(f"缺少 PageContainer:{m}")
    if missing:
        return 1
    print(f"页面骨架检查通过({checked} 个路由页)")
    return 0


raise SystemExit(main())
