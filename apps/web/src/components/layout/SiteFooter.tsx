/** 三栏页脚(仅公开页;控制台保持单行合规页脚)。备案号经 VITE_ICP_NUMBER 注入。 */

const ICP_NUMBER = import.meta.env.VITE_ICP_NUMBER as string | undefined;

import { copy, marketing } from "@superdl/ui";
import { theme, Typography } from "antd";

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
  const { token } = theme.useToken();
  const f = marketing.footer;
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
          <FooterCol title={f.product.title} links={f.product.links} />
          <FooterCol title={f.support.title} links={f.support.links} />
          <FooterCol title={f.compliance.title} links={f.compliance.links} />
          <div style={{ maxWidth: 320 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {copy.antiMiningNotice}
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
            {f.copyright}
            {" · "}
            {ICP_NUMBER ? (
              <a
                href="https://beian.miit.gov.cn/"
                target="_blank"
                rel="noreferrer"
                style={{ color: "inherit" }}
              >
                {ICP_NUMBER}
              </a>
            ) : (
              "备案号待配置(VITE_ICP_NUMBER)"
            )}
          </Typography.Text>
        </div>
      </div>
    </footer>
  );
}
