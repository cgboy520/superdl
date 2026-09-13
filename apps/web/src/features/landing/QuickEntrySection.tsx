/** 快捷入口四宫格:快速开始 / GPU 选型 / 透明计费 / 数据无忧。信息型入口指向 /help FAQ 锚点;GPU 选型页内滚动到排名区。 */

import { LandingSection } from "./LandingSection";
import { AccountBookOutlined, AimOutlined, DatabaseOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { fontSize, useThemeColors } from "@superdl/ui";
import { useNavigate } from "@tanstack/react-router";
import { Card, Col, Row, Typography } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

const ENTRIES: { key: "start" | "gpu" | "billing" | "data"; icon: ReactNode }[] = [
  { key: "start", icon: <ThunderboltOutlined /> },
  { key: "gpu", icon: <AimOutlined /> },
  { key: "billing", icon: <AccountBookOutlined /> },
  { key: "data", icon: <DatabaseOutlined /> },
];

/** key → /help 的 FAQ 锚点(与 help.tsx FAQ_KEYS 一一对应) */
const FAQ_ANCHOR = {
  start: "faq-connectSsh",
  billing: "faq-billingStart",
  data: "faq-dataPersist",
} as const;

export function QuickEntrySection() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { primary } = useThemeColors();

  const go = (key: string) => {
    if (key === "gpu") {
      document.getElementById("ranking")?.scrollIntoView({ behavior: "smooth" });
      return;
    }
    const anchor = FAQ_ANCHOR[key as keyof typeof FAQ_ANCHOR];
    void navigate({ to: "/help", hash: anchor });
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
    <LandingSection paddingBottom={16}>
      <Row gutter={[16, 16]}>
        {ENTRIES.map((e) => (
          <Col key={e.key} xs={12} md={6}>
            <Card
              hoverable
              onClick={() => go(e.key)}
              styles={{ body: { padding: 20 } }}
              style={{ height: "100%" }}
              // Card 无原生键盘语义:可 Tab 聚焦、Enter/Space 触发
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
              <div style={{ fontSize: fontSize.pageTitle, color: primary, marginBottom: 8 }}>{e.icon}</div>
              <Typography.Text strong style={{ display: "block", marginBottom: 4 }}>
                {TITLE[e.key]}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {DESC[e.key]}
              </Typography.Text>
            </Card>
          </Col>
        ))}
      </Row>
    </LandingSection>
  );
}
