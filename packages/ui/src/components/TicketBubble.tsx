/** 工单对话气泡(web 工单详情与 admin 工单抽屉共用):side 决定左右停靠与底色(右 = 本端发送,品牌浅底)。 */

import { theme, Typography } from "antd";
import type { ReactNode } from "react";

import { fontSize } from "../tokens";

export function TicketBubble({
  side,
  label,
  time,
  body,
  maxWidth = "75%",
}: {
  side: "left" | "right";
  /** 发送者称谓(如「我」/「客服」),由调用方走各端 i18n */
  label: ReactNode;
  /** 已格式化时间串(调用方走 formatDateTime) */
  time: ReactNode;
  body: ReactNode;
  maxWidth?: number | string;
}) {
  const { token } = theme.useToken();
  const right = side === "right";
  return (
    <div style={{ display: "flex", justifyContent: right ? "flex-end" : "flex-start" }}>
      <div
        style={{
          maxWidth,
          padding: "8px 12px",
          borderRadius: 8,
          background: right ? token.colorPrimaryBg : token.colorFillTertiary,
        }}
      >
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {label} · {time}
        </Typography.Text>
        <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{body}</div>
      </div>
    </div>
  );
}
