/** 控制台顶栏右区:余额入口 + 通知铃 + 用户菜单(深底白字适配)。未登录(公开市场页)显示登录入口。 */

import {
  BellOutlined,
  LogoutOutlined,
  SettingOutlined,
  UserOutlined,
  WalletOutlined,
} from "@ant-design/icons";
import { colorPrimary, formatDateTime, formatMoney } from "@superdl/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import { Badge, Button, Dropdown, List, Popover, Space, Typography } from "antd";

import { useMarkNotificationRead } from "../../api/mutations";
import { useMe, useNotifications, useWallet } from "../../api/queries";
import { authStore, useIsLoggedIn } from "../../stores/auth";

const WHITE = { color: "#fff" } as const;

function NotificationBell() {
  const { data: unread } = useNotifications({ unread: true }, { refetchInterval: 30_000 });
  const { data: all } = useNotifications({});
  const markRead = useMarkNotificationRead();
  return (
    <Popover
      trigger="click"
      placement="bottomRight"
      content={
        <List
          style={{ width: 360, maxHeight: 420, overflow: "auto" }}
          dataSource={all ?? []}
          locale={{ emptyText: "暂无通知" }}
          renderItem={(n) => (
            <List.Item
              style={{ opacity: n.read_at ? 0.55 : 1, cursor: n.read_at ? undefined : "pointer" }}
              onClick={() => {
                if (!n.read_at) markRead.mutate(n.id);
              }}
            >
              <List.Item.Meta
                title={n.title}
                description={
                  <>
                    <div>{n.content}</div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {formatDateTime(n.created_at)}
                    </Typography.Text>
                  </>
                }
              />
            </List.Item>
          )}
        />
      }
    >
      <Badge count={unread?.length ?? 0} size="small">
        <Button type="text" icon={<BellOutlined style={WHITE} />} />
      </Badge>
    </Popover>
  );
}

export function TopBarUser() {
  const navigate = useNavigate();
  const loggedIn = useIsLoggedIn();
  const { data: me } = useMe({ enabled: loggedIn });
  const { data: wallet } = useWallet({ enabled: loggedIn });

  if (!loggedIn) {
    return (
      <Button
        style={{ background: "#fff", color: colorPrimary, borderColor: "transparent", fontWeight: 600 }}
        onClick={() => navigate({ to: "/login" })}
      >
        登录 / 注册
      </Button>
    );
  }
  return (
    <Space size={12}>
      <Link to="/billing" className="topbar-link">
        <Space size={4}>
          <WalletOutlined />
          <span>{formatMoney(wallet?.balance)}</span>
        </Space>
      </Link>
      <NotificationBell />
      <Dropdown
        menu={{
          items: [
            { key: "settings", icon: <SettingOutlined />, label: "账户设置" },
            { key: "logout", icon: <LogoutOutlined />, label: "退出登录" },
          ],
          onClick: ({ key }) => {
            if (key === "logout") {
              authStore.getState().logout();
              void navigate({ to: "/login" });
            } else {
              void navigate({ to: "/settings" });
            }
          },
        }}
      >
        <Button type="text" icon={<UserOutlined style={WHITE} />} style={WHITE}>
          {me?.phone}
        </Button>
      </Dropdown>
    </Space>
  );
}
