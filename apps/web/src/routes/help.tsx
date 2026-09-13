/** 帮助中心(公开):快速开始卡(三步 + 真实 SSH 命令)+ 搜索过滤 + 分类锚点(≥lg 左侧 sticky rail,<lg 顶部 chips)
 *  + 分类下的问答(每条锚点 faq-<key>,直链到达即展开并滚动;末尾「没解决?提交工单」按登录态分流)+ 联系方式(未配置不展示)。 */

import { SearchOutlined } from "@ant-design/icons";
import { controlWidth, fontSize, layout, space } from "@superdl/ui";
import { CopyField, EmptyState, PageContainer } from "@superdl/ui/components";
import { createFileRoute, Link, useRouterState } from "@tanstack/react-router";
import { Button, Card, Collapse, Grid, Input, Space, Steps, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ContactCard } from "../components/ContactCard";
import { AppTopBar } from "../components/layout/AppTopBar";
import { SiteFooter } from "../components/layout/SiteFooter";
import { useIsLoggedIn } from "../stores/auth";

export const Route = createFileRoute("/help")({
  component: HelpPage,
});

/** 四个分类与其下的问答键(问/答 locale 键成对)。顺序即页面顺序;问答锚点 id = faq-<key>,分类锚点 id = help-cat-<key>。 */
const CATEGORIES = [
  { key: "connect", faqs: ["connectSsh", "connectJupyter"] },
  { key: "billing", faqs: ["billingStart", "billingStop", "billingDisk"] },
  { key: "data", faqs: ["dataPersist"] },
  { key: "trouble", faqs: ["createFailed", "arrears"] },
] as const;

type CategoryKey = (typeof CATEGORIES)[number]["key"];
type FaqKey = (typeof CATEGORIES)[number]["faqs"][number];

const FAQ_KEYS: readonly FaqKey[] = CATEGORIES.flatMap((c) => c.faqs);

function isFaqKey(key: string): key is FaqKey {
  return (FAQ_KEYS as readonly string[]).includes(key);
}

/** 分类标题锚点 id(rail / chips 都指向它)。 */
function categoryAnchor(key: CategoryKey): string {
  return `help-cat-${key}`;
}

function HelpPage() {
  const { t } = useTranslation();
  const screens = Grid.useBreakpoint();
  const wide = screens.lg ?? false;
  const loggedIn = useIsLoggedIn();
  const [query, setQuery] = useState("");
  // 锚点直达:展开目标条目并滚动(原生 hash 跳转落空,手动补)
  const hash = useRouterState({ select: (s) => s.location.hash });
  const [active, setActive] = useState<FaqKey[]>(["connectSsh"]);
  // hash 变化 → 展开对应条目:渲染期派生态
  const [prevHash, setPrevHash] = useState(hash);
  if (hash !== prevHash) {
    setPrevHash(hash);
    const key = hash.replace(/^#?faq-/, "");
    if (isFaqKey(key) && !active.includes(key)) {
      setActive([...active, key]);
    }
  }
  const targetKey = hash.replace(/^#?faq-/, "");
  useEffect(() => {
    if (!hash || !isFaqKey(targetKey)) return;
    // 等展开动画与面板挂载后再滚
    const timer = setTimeout(() => {
      document.getElementById(`faq-${targetKey}`)?.scrollIntoView({ behavior: "smooth" });
    }, 50);
    return () => clearTimeout(timer);
  }, [hash, targetKey]);

  // 静态键拼装(extract 只识别字面量键,故按既有写法断言到样板键)
  const faqQuestion = (k: FaqKey) => t(`help.faq.${k}.q` as "help.faq.connectSsh.q");
  const faqAnswer = (k: FaqKey) => t(`help.faq.${k}.a` as "help.faq.connectSsh.a");
  const categoryLabel = (k: CategoryKey) => t(`help.category.${k}` as "help.category.connect");

  // 客户端过滤:问 + 答子串命中(大小写不敏感)
  const needle = query.trim().toLowerCase();
  const matched = (k: FaqKey) => needle === "" || `${faqQuestion(k)}\n${faqAnswer(k)}`.toLowerCase().includes(needle);
  const visible = CATEGORIES.map((c) => ({ key: c.key, faqs: c.faqs.filter(matched) })).filter(
    (c) => c.faqs.length > 0,
  );
  const anchors = visible.map((c) => ({ key: c.key, href: `#${categoryAnchor(c.key)}`, label: categoryLabel(c.key) }));

  const stillStuck = loggedIn ? (
    <Link to="/support">{t("help.stillStuck")}</Link>
  ) : (
    <Link to="/login" search={{ redirect: "/support" }}>
      {t("help.stillStuck")}
    </Link>
  );

  // ≥lg 竖向 sticky rail;<lg 顶部横向 chips(同一份锚点)
  const rail =
    anchors.length === 0 ? null : wide ? (
      <nav
        aria-label={t("help.categoryNav")}
        style={{ width: 140, flex: "none", position: "sticky", top: layout.scrollMarginTop, alignSelf: "flex-start" }}
      >
        <Space orientation="vertical" size={space.sm}>
          {anchors.map((a) => (
            <a key={a.key} href={a.href}>
              {a.label}
            </a>
          ))}
        </Space>
      </nav>
    ) : (
      <nav aria-label={t("help.categoryNav")} style={{ marginBottom: space.lg }}>
        <Space size={space.sm} wrap>
          {anchors.map((a) => (
            <a key={a.key} href={a.href} style={{ textDecoration: "none" }}>
              <Tag style={{ marginInlineEnd: 0, cursor: "pointer" }}>{a.label}</Tag>
            </a>
          ))}
        </Space>
      </nav>
    );

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <AppTopBar variant="public" />
      <main style={{ flex: 1, padding: `${space.xxl}px ${layout.contentPadding}px` }}>
        {/* 阅读宽度沿用 narrow:分类 rail 收在同一列内,快速开始卡与搜索框与问答同宽 */}
        <PageContainer width="narrow">
          <Typography.Title level={2}>{t("help.title")}</Typography.Title>
          <Typography.Paragraph type="secondary">{t("help.intro")}</Typography.Paragraph>

          <Card style={{ marginBottom: space.xl }}>
            <Typography.Title level={3} style={{ marginTop: 0, fontSize: fontSize.sectionTitle }}>
              {t("help.quickStart.title")}
            </Typography.Title>
            <Steps
              size="small"
              current={-1}
              orientation="vertical"
              items={[
                { title: t("onboarding.step1"), content: t("help.quickStart.step1") },
                { title: t("onboarding.step2"), content: t("help.quickStart.step2") },
                {
                  title: t("help.quickStart.step3Title"),
                  content: (
                    <Space orientation="vertical" size={space.xs}>
                      <span>{t("help.quickStart.step3")}</span>
                      <CopyField value={t("help.quickStart.sshCommand")} code />
                    </Space>
                  ),
                },
              ]}
            />
          </Card>

          <div style={{ marginBottom: space.lg }}>
            <Input
              allowClear
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              prefix={<SearchOutlined />}
              aria-label={t("help.searchLabel")}
              placeholder={t("help.searchPlaceholder")}
              style={{ width: controlWidth.md, maxWidth: "100%" }}
            />
          </div>

          <div style={{ display: "flex", gap: space.xl, alignItems: "flex-start" }}>
            {wide && rail}
            <div style={{ flex: 1, minWidth: 0 }}>
              {!wide && rail}
              {visible.length === 0 ? (
                <EmptyState
                  scene="search"
                  description={t("help.searchEmpty")}
                  secondaryAction={
                    <Button size="small" onClick={() => setQuery("")}>
                      {t("help.clearSearch")}
                    </Button>
                  }
                />
              ) : (
                visible.map((c) => (
                  <section key={c.key} aria-labelledby={categoryAnchor(c.key)} style={{ marginBottom: space.xl }}>
                    <Typography.Title
                      level={3}
                      id={categoryAnchor(c.key)}
                      style={{ fontSize: fontSize.sectionTitle, scrollMarginTop: layout.scrollMarginTop }}
                    >
                      {categoryLabel(c.key)}
                    </Typography.Title>
                    <Collapse
                      activeKey={active}
                      onChange={(keys) => setActive(keys.filter(isFaqKey))}
                      items={c.faqs.map((k) => ({
                        key: k,
                        label: <span id={`faq-${k}`}>{faqQuestion(k)}</span>,
                        children: (
                          <>
                            <Typography.Paragraph style={{ whiteSpace: "pre-line" }}>
                              {faqAnswer(k)}
                            </Typography.Paragraph>
                            {stillStuck}
                          </>
                        ),
                      }))}
                    />
                  </section>
                ))
              )}
            </div>
          </div>

          <ContactCard
            title={t("help.contactTitle")}
            emailLabel={t("help.contactEmail")}
            wechatLabel={t("help.contactWechat")}
            hint={t("help.contactHint")}
            missingText={t("help.contactMissing")}
            style={{ marginTop: space.xl }}
          />

          <Typography.Paragraph style={{ marginTop: space.xl }}>
            <Link to="/legal/terms">{t("footer.linkTerms")}</Link> ·{" "}
            <Link to="/legal/privacy">{t("footer.linkPrivacy")}</Link> · <Link to="/">{t("common.backHome")}</Link>
          </Typography.Paragraph>
        </PageContainer>
      </main>
      <SiteFooter />
    </div>
  );
}
