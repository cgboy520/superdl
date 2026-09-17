/** 法务文档页:文档切换、版本信息、二级标题目录与正文打印样式。 */

import { fontSize, formatDateTime, layout, space } from "@superdl/ui";
import { DataErrorAlert, LegalMarkdown, PageContainer } from "@superdl/ui/components";
import { Link, useNavigate } from "@tanstack/react-router";
import { Alert, Grid, Segmented, Skeleton, Space, Typography } from "antd";
import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";

import { useLegalDoc } from "../../api/queries";
import { AppTopBar } from "../../components/layout/AppTopBar";
import { SiteFooter } from "../../components/layout/SiteFooter";

export type LegalDocKey = "terms" | "privacy" | "deletion_notice";

/** 打印时隐藏导航与目录,正文使用黑字白底。 */
const PRINT_CSS = `
.legal-chrome { display: contents; }
@media print {
  .legal-chrome, .legal-print-hide { display: none !important; }
  .legal-page { background: white !important; padding: 0 !important; }
  .legal-page, .legal-page * { color: black !important; background: transparent !important; box-shadow: none !important; }
}
`;

interface TocItem {
  id: string;
  text: string;
}

/** 从原始 markdown 取二级标题(跳过围栏代码块);id 按出现序生成,与渲染后的 h2 一一对应。 */
export function parseTocHeadings(md: string): TocItem[] {
  const out: TocItem[] = [];
  let inFence = false;
  for (const line of md.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const m = /^##\s+(.+?)\s*$/.exec(line);
    const text = m?.[1];
    if (text === undefined) continue;
    out.push({ id: `legal-h2-${out.length}`, text });
  }
  return out;
}

export function LegalDocPage({ docKey }: { docKey: LegalDocKey }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const wide = screens.lg ?? false;
  const lang = i18n.language.startsWith("zh") ? "zh-CN" : "en-US";
  const { data, isLoading, isError, refetch } = useLegalDoc(docKey, lang);
  const bodyRef = useRef<HTMLDivElement>(null);
  const toc = useMemo(() => parseTocHeadings(data?.content_md ?? ""), [data?.content_md]);
  useEffect(() => {
    const nodes = bodyRef.current?.querySelectorAll("h2");
    nodes?.forEach((el, i) => {
      const item = toc[i];
      if (!item) return;
      el.id = item.id;
      el.style.scrollMarginTop = `${layout.scrollMarginTop}px`;
    });
  }, [toc]);

  const goTo = (key: string) => {
    if (key === "privacy") void navigate({ to: "/legal/privacy" });
    else if (key === "deletion_notice") void navigate({ to: "/legal/deletion-notice" });
    else void navigate({ to: "/legal/terms" });
  };

  return (
    <div className="legal-page" style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <style>{PRINT_CSS}</style>
      <div className="legal-chrome">
        <AppTopBar variant="public" />
      </div>
      <main style={{ flex: 1, padding: `${layout.sectionPaddingY}px ${layout.contentPadding}px` }}>
        <PageContainer>
          <Segmented
            className="legal-print-hide"
            value={docKey}
            onChange={goTo}
            options={[
              { label: t("legal.docTerms"), value: "terms" },
              { label: t("legal.docPrivacy"), value: "privacy" },
              { label: t("legal.docDeletionNotice"), value: "deletion_notice" },
            ]}
            style={{ marginBottom: space.xl }}
          />
          {isLoading && <Skeleton active paragraph={{ rows: 10 }} />}
          {isError && <DataErrorAlert onRetry={() => void refetch()} />}
          {data && (
            <>
              {data.fallback && (
                <Alert type="info" showIcon title={t("legal.fallbackNote")} style={{ marginBottom: space.xl }} />
              )}
              <Typography.Title level={2} style={{ marginBottom: space.xs }}>
                {data.title}
              </Typography.Title>
              <Typography.Paragraph type="secondary" style={{ fontSize: fontSize.caption }}>
                {t("legal.versionLine", {
                  version: data.version,
                  date: data.published_at ? formatDateTime(data.published_at) : "—",
                })}
              </Typography.Paragraph>
              <div style={{ display: "flex", gap: space.xxl, alignItems: "flex-start" }}>
                <div ref={bodyRef} style={{ flex: 1, minWidth: 0, maxWidth: layout.pageMaxWidthNarrow }}>
                  <LegalMarkdown content={data.content_md} />
                  <Typography.Paragraph style={{ marginTop: space.xxl }}>
                    <Link to="/">{t("legal.backHome")}</Link>
                  </Typography.Paragraph>
                </div>
                {wide && toc.length > 0 && (
                  <nav
                    className="legal-print-hide"
                    aria-label={t("legal.toc")}
                    style={{
                      width: 220,
                      flex: "none",
                      position: "sticky",
                      top: layout.scrollMarginTop,
                      alignSelf: "flex-start",
                    }}
                  >
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                      {t("legal.toc")}
                    </Typography.Text>
                    <Space orientation="vertical" size={space.xs} style={{ display: "flex", marginTop: space.sm }}>
                      {toc.map((h) => (
                        <a key={h.id} href={`#${h.id}`}>
                          {h.text}
                        </a>
                      ))}
                    </Space>
                  </nav>
                )}
              </div>
            </>
          )}
        </PageContainer>
      </main>
      <div className="legal-chrome">
        <SiteFooter />
      </div>
    </div>
  );
}
