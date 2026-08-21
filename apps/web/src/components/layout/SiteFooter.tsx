/** 三栏页脚(仅公开页;控制台保持单行合规页脚)。
 * 备案号走后端 site-config(管理端·平台配置在线维护),VITE_ICP_NUMBER 仅作构建期兜底。 */

const ICP_FALLBACK = import.meta.env.VITE_ICP_NUMBER as string | undefined;


import { theme, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useSiteConfig } from "../../api/queries";

function FooterCol({
  title,
  links,
}: {
  title: string;
  links: readonly { label: string; to: string }[];
}) {
  const { token } = theme.useToken();
  return (
    <div style={{ minWidth: 160 }}>
      <Typography.Text strong style={{ display: "block", marginBottom: 12 }}>
        {title}
      </Typography.Text>
      {links.map((l) => (
        <div key={l.label} style={{ marginBottom: 8 }}>
          {l.to ? (
            <a href={l.to} style={{ color: token.colorTextSecondary }}>
              {l.label}
            </a>
          ) : (
            <Typography.Text type="secondary">{l.label}</Typography.Text>
          )}
        </div>
      ))}
    </div>
  );
}

export function SiteFooter() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const { data: site } = useSiteConfig();
  const icp = site?.icp_number ?? ICP_FALLBACK;
  const police = site?.police_record_number;
  const productLinks = [
    { label: t("footer.linkMarket"), to: "/market" },
    { label: t("footer.linkPricing"), to: "/#pricing" },
    { label: t("footer.linkRanking"), to: "/#ranking" },
  ];
  // 只列真实存在的入口,不放 to="" 的占位链接
  const supportLinks = [
    { label: t("footer.linkHelp"), to: "/help" },
    ...(site?.support_email
      ? [{ label: t("footer.linkContact"), to: `mailto:${site.support_email}` }]
      : []),
  ];
  const complianceLinks = [
    { label: t("footer.linkTerms"), to: "/legal/terms" },
    { label: t("footer.linkPrivacy"), to: "/legal/privacy" },
  ];
  return (
    <footer style={{ background: token.colorBgContainer, borderTop: `1px solid ${token.colorBorderSecondary}` }}>
      <div
        style={{
          maxWidth: 1200,
          margin: "0 auto",
          padding: "40px 24px 24px",
        }}
      >
        <div style={{ display: "flex", flexWrap: "wrap", gap: 32, justifyContent: "space-between" }}>
          <FooterCol title={t("footer.productTitle")} links={productLinks} />
          <FooterCol title={t("footer.supportTitle")} links={supportLinks} />
          <FooterCol title={t("footer.complianceTitle")} links={complianceLinks} />
          <div style={{ maxWidth: 320 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t("copy.antiMiningNotice")}
            </Typography.Text>
          </div>
        </div>
        <div
          style={{
            marginTop: 32,
            paddingTop: 16,
            borderTop: `1px solid ${token.colorBorderSecondary}`,
            textAlign: "center",
          }}
        >
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("footer.copyright")}
            {icp && (
              <>
                {" · "}
                <a
                  href="https://beian.miit.gov.cn/"
                  target="_blank"
                  rel="noreferrer"
                  style={{ color: "inherit" }}
                >
                  {icp}
                </a>
              </>
            )}
            {police && (
              <>
                {" · "}
                <a
                  href="https://beian.mps.gov.cn/"
                  target="_blank"
                  rel="noreferrer"
                  style={{ color: "inherit" }}
                >
                  {police}
                </a>
              </>
            )}
          </Typography.Text>
        </div>
      </div>
    </footer>
  );
}
