/** 法务文档页:从 GET /api/v1/legal/{doc_key} 拉取当前 published 版渲染。
 * en-US 缺失时服务端回落 zh-CN(fallback=true),顶部给一行提示。 */

import { formatDateTime } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { Alert, Skeleton, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useLegalDoc } from "../../api/queries";
import { LegalMarkdown } from "../../components/LegalMarkdown";
import { DataErrorAlert } from "../../components/QueryState";
import { AppTopBar } from "../../components/layout/AppTopBar";
import { SiteFooter } from "../../components/layout/SiteFooter";

export type LegalDocKey = "terms" | "privacy" | "deletion_notice";

export function LegalDocPage({ docKey }: { docKey: LegalDocKey }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language.startsWith("en") ? "en-US" : "zh-CN";
  const { data, isLoading, isError, refetch } = useLegalDoc(docKey, lang);
  // 静态 t() 调用:i18n extract 只识别字面量键
  const links = [
    { key: "terms", to: "/legal/terms", label: t("legal.docTerms") },
    { key: "privacy", to: "/legal/privacy", label: t("legal.docPrivacy") },
    { key: "deletion_notice", to: "/legal/deletion-notice", label: t("legal.docDeletionNotice") },
  ].filter((l) => l.key !== docKey);
  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <AppTopBar variant="public" />
      <div style={{ flex: 1, maxWidth: 800, width: "100%", margin: "0 auto", padding: "48px 24px" }}>
        {isLoading && <Skeleton active paragraph={{ rows: 10 }} />}
        {isError && <DataErrorAlert onRetry={() => void refetch()} />}
        {data && (
          <>
            {data.fallback && (
              <Alert
                type="info"
                showIcon
                title={t("legal.fallbackNote")}
                style={{ marginBottom: 24 }}
              />
            )}
            <Typography.Title level={2}>{data.title}</Typography.Title>
            <LegalMarkdown content={data.content_md} />
            <Typography.Paragraph type="secondary" style={{ marginTop: 32, fontSize: 12 }}>
              {t("legal.versionLine", {
                version: data.version,
                date: data.published_at ? formatDateTime(data.published_at) : "—",
              })}
            </Typography.Paragraph>
            <Typography.Paragraph>
              {links.map((l, i) => (
                <span key={l.key}>
                  {i > 0 && " · "}
                  <Link to={l.to}>{l.label}</Link>
                </span>
              ))}
              {" · "}
              <Link to="/">{t("legal.backHome")}</Link>
            </Typography.Paragraph>
          </>
        )}
      </div>
      <SiteFooter />
    </div>
  );
}
