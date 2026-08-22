/** 快捷入口四宫格:快速开始 / GPU 选型 / 透明计费 / 数据无忧。 */

import {
  AccountBookOutlined,
  AimOutlined,
  DatabaseOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { colorPrimary } from "@superdl/ui";
import { useNavigate } from "@tanstack/react-router";
import { Card, Col, Row, Typography } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useIsLoggedIn } from "../../stores/auth";

const ENTRIES: Array<{ key: "start" | "gpu" | "billing" | "data"; icon: ReactNode }> = [
  { key: "start", icon: <ThunderboltOutlined /> },
  { key: "gpu", icon: <AimOutlined /> },
  { key: "billing", icon: <AccountBookOutlined /> },
  { key: "data", icon: <DatabaseOutlined /> },
];

export function QuickEntrySection() {
  const { t } = useTranslation();
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

  const TITLE = {
    start: t("landing.quickEntries.start.title"),
    gpu: t("landing.quickEntries.gpu.title"),
    billing: t("landing.quickEntries.billing.title"),
    data: t("landing.quickEntries.data.title"),
  } as const;
  const DESC = {
    start: t("landing.quickEntries.start.desc"),
    gpu: t("landing.quickEntries.gpu.desc"),
    billing: t("landing.quickEntries.billing.desc"),
    data: t("landing.quickEntries.data.desc"),
  } as const;

  return (
    <section style={{ maxWidth: 1200, margin: "0 auto", padding: "48px 24px 16px" }}>
      <Row gutter={[16, 16]}>
        {ENTRIES.map((e) => (
          <Col key={e.key} xs={12} md={6}>
            <Card
              hoverable
              onClick={() => go(e.key)}
              styles={{ body: { padding: 20 } }}
              style={{ height: "100%" }}
              // Card 无原生键盘语义:快捷入口必须可 Tab 聚焦、Enter/Space 触发
              role="button"
              tabIndex={0}
              aria-label={`${TITLE[e.key]} — ${DESC[e.key]}`}
              onKeyDown={(ev) => {
                if (ev.key === "Enter" || ev.key === " ") {
                  ev.preventDefault();
                  go(e.key);
                }
              }}
            >
              <div style={{ fontSize: 22, color: colorPrimary, marginBottom: 8 }}>{e.icon}</div>
              <Typography.Text strong style={{ display: "block", marginBottom: 4 }}>
                {TITLE[e.key]}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                {DESC[e.key]}
              </Typography.Text>
            </Card>
          </Col>
        ))}
      </Row>
    </section>
  );
}
