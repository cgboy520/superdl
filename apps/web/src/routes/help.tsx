/**
 * 帮助与支持(公开):FAQ(连接/计费/数据/故障)+ 联系方式。
 * 联系方式来自平台配置,未配置即不展示对应入口。
 */

import { createFileRoute, Link } from "@tanstack/react-router";
import { Alert, Card, Collapse, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useSiteConfig } from "../api/queries";
import { AppTopBar } from "../components/layout/AppTopBar";
import { CopyButton } from "../components/common";
import { SiteFooter } from "../components/layout/SiteFooter";

export const Route = createFileRoute("/help")({
  component: HelpPage,
});

/** FAQ 条目键(问/答两条 locale 键成对)。顺序即页面顺序。 */
const FAQ_KEYS = [
  "connectSsh",
  "connectJupyter",
  "billingStart",
  "billingStop",
  "billingDisk",
  "dataPersist",
  "createFailed",
  "arrears",
] as const;

function HelpPage() {
  const { t } = useTranslation();
  const { data: site } = useSiteConfig();
  const email = site?.support_email;
  const wechat = site?.support_wechat;

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <AppTopBar variant="public" />
      <div style={{ flex: 1, maxWidth: 880, width: "100%", margin: "0 auto", padding: "32px 24px" }}>
        <Typography.Title level={2}>{t("help.title")}</Typography.Title>
        <Typography.Paragraph type="secondary">{t("help.intro")}</Typography.Paragraph>

        <Collapse
          defaultActiveKey={[FAQ_KEYS[0]]}
          items={FAQ_KEYS.map((k) => ({
            key: k,
            label: t(`help.faq.${k}.q` as "help.faq.connectSsh.q"),
            children: (
              <Typography.Paragraph style={{ marginBottom: 0, whiteSpace: "pre-line" }}>
                {t(`help.faq.${k}.a` as "help.faq.connectSsh.a")}
              </Typography.Paragraph>
            ),
          }))}
        />

        <Card title={t("help.contactTitle")} style={{ marginTop: 24 }}>
          {email || wechat ? (
            <Space orientation="vertical" size={12} style={{ width: "100%" }}>
              {email && (
                <Space>
                  <Typography.Text strong>{t("help.contactEmail")}</Typography.Text>
                  <a href={`mailto:${email}`}>{email}</a>
                  <CopyButton text={email} />
                </Space>
              )}
              {wechat && (
                <Space>
                  <Typography.Text strong>{t("help.contactWechat")}</Typography.Text>
                  <Typography.Text code>{wechat}</Typography.Text>
                  <CopyButton text={wechat} />
                </Space>
              )}
              <Typography.Text type="secondary">{t("help.contactHint")}</Typography.Text>
            </Space>
          ) : (
            <Alert type="info" showIcon message={t("help.contactMissing")} />
          )}
        </Card>

        <Typography.Paragraph style={{ marginTop: 24 }}>
          <Link to="/legal/terms">{t("footer.linkTerms")}</Link> ·{" "}
          <Link to="/legal/privacy">{t("footer.linkPrivacy")}</Link> ·{" "}
          <Link to="/">{t("common.backHome")}</Link>
        </Typography.Paragraph>
      </div>
      <SiteFooter />
    </div>
  );
}
