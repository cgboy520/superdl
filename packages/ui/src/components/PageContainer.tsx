/** Page container: centred max width + optional header (PageHeader). Four widths: default 1280 / wide 1200 / narrow 880 / full (unbounded, admin wide-table pages). */

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
  /** Page width tier */
  width?: PageWidth;
  children: ReactNode;
}) {
  const maxWidth = widthMap[width];
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
