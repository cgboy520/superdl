#!/usr/bin/env python3
"""页面骨架闸门:控制台每个路由页必须自持 PageContainer(docs/ui-ux-spec.md §1 规则 2)。

挂了说明:有页面又手搓了 Typography.Title 当页头,页宽 / 页头操作区 / 新鲜度条会各写各的。
扫描范围:apps/web/src/routes/_console*.tsx 与 apps/admin/src/routes/_app/*.tsx;`-` 开头的文件是同目录的非路由片段,跳过。
公开层(首页 / 登录 / 帮助 / 法务)不在控制台骨架内,不扫。
"""

import pathlib

TARGETS = (
    ("apps/web/src/routes", "_console*.tsx", {"_console.tsx"}),
    ("apps/admin/src/routes/_app", "*.tsx", set()),
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
