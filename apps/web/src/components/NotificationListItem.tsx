/** Notification list item (one rendering for the notification centre and the top-bar Popover): unread = left dot + light background + left border; the whole row is clickable with keyboard semantics. */

import type { NotificationOut } from "@superdl/api-client";
import { fontSize, formatDateTime, space } from "@superdl/ui";
import { Badge, List, Space, theme, Typography } from "antd";

export function NotificationListItem({ n, onOpen }: { n: NotificationOut; onOpen: (n: NotificationOut) => void }) {
  const { token } = theme.useToken();
  const isUnread = n.read_at == null;
  return (
    <List.Item
      style={{
        cursor: "pointer",
        background: isUnread ? token.colorPrimaryBg : undefined,
        borderInlineStart: isUnread ? `3px solid ${token.colorPrimary}` : "3px solid transparent",
        paddingInline: 12,
      }}
      onClick={() => onOpen(n)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(n);
        }
      }}
    >
      <List.Item.Meta
        title={
          <Space size={space.sm}>
            {isUnread && <Badge color={token.colorPrimary} />}
            <Typography.Text strong={isUnread}>{n.title}</Typography.Text>
          </Space>
        }
        description={
          <>
            <div>{n.content}</div>
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {formatDateTime(n.created_at)}
            </Typography.Text>
          </>
        }
      />
    </List.Item>
  );
}
