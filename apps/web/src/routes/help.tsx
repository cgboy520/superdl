/**
 * 帮助与支持(公开):FAQ(连接/计费/数据/故障)+ 联系方式。
 * 联系方式来自平台配置,未配置即不展示对应入口。
 * FAQ 每条带锚点(faq-<key>):落地页快捷入口直链 /help#faq-xxx,到达即展开并滚动到位。
 */

import { createFileRoute, Link, useRouterState } from "@tanstack/react-router";
import { Alert, Card, Collapse, Space, Typography } from "antd";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useSiteConfig } from "../api/queries";
import { AppTopBar } from "../components/layout/AppTopBar";
import { CopyButton } from "../components/common";
import { SiteFooter } from "../components/layout/SiteFooter";

export const Route = createFileRoute("/help")({
  component: HelpPage,
});

/** FAQ 条目键(问/答两条 locale 键成对)。顺序即页面顺序;锚点 id = faq-<key>。 */
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

type FaqKey = (typeof FAQ_KEYS)[number];

function HelpPage() {
  const { t } = useTranslation();
  const { data: site } = useSiteConfig();
  const email = site?.support_email;
  const wechat = site?.support_wechat;
  // 锚点直达:展开目标条目并滚动到位(React 晚渲染,原生 hash 跳转会落空,须手动补)
  const hash = useRouterState({ select: (s) => s.location.hash });
  const [active, setActive] = useState<FaqKey[]>([FAQ_KEYS[0]]);
  // hash 变化 → 展开对应条目:渲染期派生态(不进 effect,防 react-hooks/set-state-in-effect)
  const [prevHash, setPrevHash] = useState(hash);
  if (hash !== prevHash) {
    setPrevHash(hash);
    const key = hash.replace(/^#?faq-/, "") as FaqKey;
    if ((FAQ_KEYS as readonly string[]).includes(key) && !active.includes(key)) {
      setActive([...active, key]);
    }
  }
  const targetKey = hash.replace(/^#?faq-/, "");
  useEffect(() => {
    if (!hash || !(FAQ_KEYS as readonly string[]).includes(targetKey)) return;
    // 等展开动画与面板挂载后再滚
    const timer = setTimeout(() => {
      document.getElementById(`faq-${targetKey}`)?.scrollIntoView({ behavior: "smooth" });
    }, 50);
    return () => clearTimeout(timer);
  }, [hash, targetKey]);

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <AppTopBar variant="public" />
      <div style={{ flex: 1, maxWidth: 880, width: "100%", margin: "0 auto", padding: "32px 24px" }}>
        <Typography.Title level={2}>{t("help.title")}</Typography.Title>
        <Typography.Paragraph type="secondary">{t("help.intro")}</Typography.Paragraph>

        <Collapse
          activeKey={active}
          onChange={(keys) => setActive(keys as FaqKey[])}
          items={FAQ_KEYS.map((k) => ({
            key: k,
            label: <span id={`faq-${k}`}>{t(`help.faq.${k}.q` as "help.faq.connectSsh.q")}</span>,
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
            <Alert type="info" showIcon title={t("help.contactMissing")} />
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
