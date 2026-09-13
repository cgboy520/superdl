/** 页面容器:最大宽度居中 + 可选页头(PageHeader)。width 四档:default 1280 / wide 1200 / narrow 880 / full(不限宽,管理端宽表页)。 */

import type { ReactNode } from "react";

import { layout } from "../tokens";
import { PageHeader, type PageHeaderProps } from "./PageHeader";

const widthMap = {
  default: layout.pageMaxWidth,
  wide: layout.pageMaxWidthWide,
  narrow: layout.pageMaxWidthNarrow,
  full: undefined,
} as const;

export type PageWidth = keyof typeof widthMap;

export function PageContainer({
  title,
  width = "default",
  children,
  ...header
}: Partial<PageHeaderProps> & {
  /** 页宽档位 */
  width?: PageWidth;
  children: ReactNode;
}) {
  const maxWidth = widthMap[width];
  // 任一页头槽位有值就渲染页头(详情页只给面包屑 / 返回,标题由 EntityHeader 承担)
  const hasHeader =
    title !== undefined ||
    header.breadcrumb !== undefined ||
    header.back !== undefined ||
    header.extra !== undefined ||
    header.description !== undefined ||
    header.freshness !== undefined ||
    header.tags !== undefined;
  return (
    <div style={{ maxWidth, margin: maxWidth ? "0 auto" : undefined, minWidth: 0 }}>
      {hasHeader && <PageHeader title={title} {...header} />}
      {children}
    </div>
  );
}
