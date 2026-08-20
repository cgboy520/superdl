/** 快捷入口四宫格:注册礼包 / GPU 选型 / 开具发票 / 新手入门。 */

import {
  AccountBookOutlined,
  AimOutlined,
  DatabaseOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { colorPrimary, marketing } from "@superdl/ui";
import { useNavigate } from "@tanstack/react-router";
import { Card, Col, Row, Typography } from "antd";
import type { ReactNode } from "react";

import { useIsLoggedIn } from "../../stores/auth";

const ICONS: Record<string, ReactNode> = {
  start: <ThunderboltOutlined />,
  gpu: <AimOutlined />,
  billing: <AccountBookOutlined />,
  data: <DatabaseOutlined />,
};

export function QuickEntrySection() {
  const navigate = useNavigate();
  const loggedIn = useIsLoggedIn();

  const go = (key: string) => {
    if (key === "gpu") {
      document.getElementById("ranking")?.scrollIntoView({ behavior: "smooth" });
    } else if (key === "start") {
      void navigate({ to: loggedIn ? "/market" : "/login" });
    } else if (key === "billing") {
      void navigate({ to: "/billing" });
    } else {
      void navigate({ to: "/storage" });
    }
  };

  return (
    <section style={{ maxWidth: 1200, margin: "0 auto", padding: "48px 24px 16px" }}>
      <Row gutter={[16, 16]}>
        {marketing.quickEntries.map((e) => (
          <Col key={e.key} xs={12} md={6}>
            <Card
              hoverable
              onClick={() => go(e.key)}
              styles={{ body: { padding: 20 } }}
              style={{ height: "100%" }}
            >
              <div style={{ fontSize: 22, color: colorPrimary, marginBottom: 8 }}>
                {ICONS[e.key]}
              </div>
              <Typography.Text strong style={{ display: "block", marginBottom: 4 }}>
                {e.title}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                {e.desc}
              </Typography.Text>
            </Card>
          </Col>
        ))}
      </Row>
    </section>
  );
}
