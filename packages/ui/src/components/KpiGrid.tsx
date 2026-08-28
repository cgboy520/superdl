/** KPI 卡网格:总览/费用中心顶部 Statistic 卡组的统一响应式栅格
 *  (窄屏两块一排,lg 起均分)。收敛各页重复的 Row/Col inline 写法。
 */

import { Col, Row } from "antd";
import type { ReactNode } from "react";

import { layout } from "../tokens";

export function KpiGrid({ items }: { items: ReactNode[] }) {
  const span = Math.floor(24 / Math.max(items.length, 1));
  return (
    <Row gutter={[layout.cardGap, layout.cardGap]}>
      {items.map((node, i) => (
        // KPI 数量稳定(2~4),下标作 key 与各页现状一致
        <Col xs={12} lg={span} key={i}>
          {node}
        </Col>
      ))}
    </Row>
  );
}
