/** KPI 卡网格(窄屏两块一排,lg 起均分);loading 渲染同数量骨架卡。 */

import { Card, Col, Row, Skeleton } from "antd";
import type { ReactNode } from "react";

import { layout } from "../tokens";

export function KpiGrid({ items, loading }: { items: ReactNode[]; loading?: boolean }) {
  // ≤4 块 24 栅格均分;>4 块 flex 等分
  const gridable = items.length >= 1 && items.length <= 4;
  const span = Math.floor(24 / Math.max(items.length, 1));
  return (
    <Row gutter={[layout.cardGap, layout.cardGap]}>
      {items.map((node, i) => (
        <Col xs={12} lg={gridable ? span : undefined} flex={gridable ? undefined : "1 1 0"} key={i}>
          {loading ? (
            <Card>
              <Skeleton active title={{ width: "40%" }} paragraph={{ rows: 1, width: "70%" }} />
            </Card>
          ) : (
            node
          )}
        </Col>
      ))}
    </Row>
  );
}
