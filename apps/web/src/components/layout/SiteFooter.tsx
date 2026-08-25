/** 三栏页脚(仅公开页;控制台保持单行合规页脚)。备案号等走后端 site-config(管理端·平台配置在线维护)。 */

import { Link } from "@tanstack/react-router";
import { theme, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useSiteConfig } from "../../api/queries";

/** 站内路径(单 `/` 开头)走 SPA Link 避免整页刷新;锚点/mailto/外链保持 <a>。 */
function isInternal(to: string): boolean {
  return to.startsWith("/") && !to.startsWith("/#");
}

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
          {isInternal(l.to) ? (
            <Link to={l.to} style={{ color: token.colorTextSecondary }}>
              {l.label}
            </Link>
          ) : (
            <a href={l.to} style={{ color: token.colorTextSecondary }}>
              {l.label}
            </a>
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
  const icp = site?.icp_number;
  const police = site?.police_record_number;
  // 经营主体公示(《电子商务法》第十五条):留空的项不展示
  const companyName = site?.company_name;
  const companyAddress = site?.company_address;
  const companyPhone = site?.company_phone;
  const licenseUrl = site?.business_license_url;
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
          {(companyName || companyAddress || companyPhone) && (
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 4 }}>
              {companyName &&
                (licenseUrl ? (
                  <a href={licenseUrl} target="_blank" rel="noreferrer" style={{ color: "inherit" }}>
                    {companyName}
                  </a>
                ) : (
                  companyName
                ))}
              {companyAddress && ` · ${companyAddress}`}
              {companyPhone && ` · ${companyPhone}`}
            </Typography.Paragraph>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("footer.copyright", { year: new Date().getFullYear() })}
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
