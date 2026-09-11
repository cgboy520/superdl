/** KPI 卡网格:CSS grid 自适应列数(每卡 ≥ minWidth,默认 180px),任意块数在任意视口都能换行;loading 渲染同数量骨架卡。 */

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
  /** 单卡最小宽度(px);窄屏按此值自动折行 */
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
