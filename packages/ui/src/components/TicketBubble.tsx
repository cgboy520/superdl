/** Ticket conversation bubble (shared by both consoles): side decides alignment and background (right = sent by this side). */

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
  /** Sender label */
  label: ReactNode;
  /** Pre-formatted time string */
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
