/** 工单对话气泡(两端共用):side 决定左右停靠与底色(右 = 本端发送)。 */

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
  /** 发送者称谓 */
  label: ReactNode;
  /** 已格式化时间串 */
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
