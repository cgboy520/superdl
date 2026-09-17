/** KPI card grid: CSS grid with adaptive column count (each card ≥ minWidth, default 180px), any number of blocks wraps on any viewport; loading renders the same number of skeleton cards. */

import { Card, Skeleton } from "antd";
import type { ReactNode } from "react";

import { layout } from "../tokens";

export function KpiGrid({
  items,
  loading,
  minWidth = 180,
}: {
  items: ReactNode[];
  loading?: boolean;
  /** Minimum card width (px); narrow screens wrap by it */
  minWidth?: number;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(auto-fit, minmax(min(${minWidth}px, 100%), 1fr))`,
        gap: layout.cardGap,
      }}
    >
      {items.map((node, i) =>
        loading ? (
          <Card key={i}>
            <Skeleton active title={{ width: "40%" }} paragraph={{ rows: 1, width: "70%" }} />
          </Card>
        ) : (
          <div key={i} style={{ minWidth: 0 }}>
            {node}
          </div>
        ),
      )}
    </div>
  );
}
