/** 帮助与支持(公开):FAQ(连接/计费/数据/故障)+ 联系方式(来自平台配置,未配置不展示)。FAQ 每条带锚点 faq-<key>,直链到达即展开并滚动。 */

import { PageContainer } from "@superdl/ui/components";
import { createFileRoute, Link, useRouterState } from "@tanstack/react-router";
import { Collapse, Typography } from "antd";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ContactCard } from "../components/ContactCard";
import { AppTopBar } from "../components/layout/AppTopBar";
import { SiteFooter } from "../components/layout/SiteFooter";

export const Route = createFileRoute("/help")({
  component: HelpPage,
});

/** FAQ 条目键(问/答 locale 键成对)。顺序即页面顺序;锚点 id = faq-<key>。 */
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
  // 锚点直达:展开目标条目并滚动(原生 hash 跳转落空,手动补)
  const hash = useRouterState({ select: (s) => s.location.hash });
  const [active, setActive] = useState<FaqKey[]>([FAQ_KEYS[0]]);
  // hash 变化 → 展开对应条目:渲染期派生态
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
      <div style={{ flex: 1, padding: "32px 24px" }}>
        <PageContainer width="narrow">
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

          <ContactCard
            title={t("help.contactTitle")}
            emailLabel={t("help.contactEmail")}
            wechatLabel={t("help.contactWechat")}
            hint={t("help.contactHint")}
            missingText={t("help.contactMissing")}
            style={{ marginTop: 24 }}
          />

          <Typography.Paragraph style={{ marginTop: 24 }}>
            <Link to="/legal/terms">{t("footer.linkTerms")}</Link> ·{" "}
            <Link to="/legal/privacy">{t("footer.linkPrivacy")}</Link> · <Link to="/">{t("common.backHome")}</Link>
          </Typography.Paragraph>
        </PageContainer>
      </div>
      <SiteFooter />
    </div>
  );
}
