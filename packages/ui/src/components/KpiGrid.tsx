/** KPI 卡网格:总览/费用中心顶部 Statistic 卡组的统一响应式栅格
 *  (窄屏两块一排,lg 起均分)。loading 时渲染同数量骨架卡,避免 "—" 占位与「无数据」混淆。
 */

import { Card, Col, Row, Skeleton } from "antd";
import type { ReactNode } from "react";

import { layout } from "../tokens";

export function KpiGrid({ items, loading }: { items: ReactNode[]; loading?: boolean }) {
  // ≤4 块用 24 栅格均分;>4 块改 flex 等分(span 取整会让末行铺不满)
  const gridable = items.length >= 1 && items.length <= 4;
  const span = Math.floor(24 / Math.max(items.length, 1));
  return (
    <Row gutter={[layout.cardGap, layout.cardGap]}>
      {items.map((node, i) => (
        // KPI 数量稳定(2~4),下标作 key
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
