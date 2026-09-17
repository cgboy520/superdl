/** Bottom full-width checkout bar (sticky, shared by the market / create / deploy pages): cost items laid out one by one, daily fees and configuration fees in separate columns.
 *  Below sm it folds into one line: the price figure + primary button stay, details / balance / incomplete items go into the "Details ▴" bottom sheet (ui-ux-spec §3.5). */

import { fontSize, fontWeight, motion as motionToken, shadow, space, useThemeColors, zIndex } from "@superdl/ui";
import { moneyOr } from "@superdl/ui/components";
import { Button, Drawer, Grid, Popover, Space, theme, Typography } from "antd";
import { AnimatePresence, motion } from "motion/react";
import { useState, type ReactNode } from "react";

import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";
/** Selection-change fade-in (motion token fast tier; zero under reducedMotion, see the root MotionConfig) */
const FADE_TRANSITION = {
  duration: motionToken.fast,
  ease: [...motionToken.easeOut] as [number, number, number, number],
};

export interface CheckoutItem {
  label: string;
  value: ReactNode;
  hint?: string;
  /** Basis suffix after the big figure ("× 2 cards" / "whole machine"), body size */
  suffix?: string;
  /** Non-money items (expiry etc.) drop to body size and default text colour */
  muted?: boolean;
}

export function CheckoutBar({
  summary,
  items,
  detail,
  balance,
  balanceReady = true,
  actions,
  changeKey,
  breakdown,
  notice,
  noticeSummary,
}: {
  /** Spec summary on the left (indigo block) */
  summary?: ReactNode;
  /** Cost items (small label above, large value below) */
  items: CheckoutItem[];
  /** "Cost breakdown" Popover content */
  detail?: ReactNode;
  /** Balance (omit when signed out) */
  balance?: string | null;
  /** Whether the balance is ready; false renders "—". */
  balanceReady?: boolean;
  actions: ReactNode;
  /** Selection-change marker (e.g. spec id + billing mode): the figures fade in when it changes; no animation when omitted */
  changeKey?: string;
  /** Details laid out on the bar's second line (subscription "list / discount / payable" directly visible, not in the Popover) */
  breakdown?: ReactNode;
  /** Notice above the bar (incomplete items / disk creation failure), does not scroll away with the content */
  notice?: ReactNode;
  /** One-line summary replacing notice below sm (e.g. "2 items left"); the bottom sheet shows the full text */
  noticeSummary?: ReactNode;
}) {
  const { token } = theme.useToken();
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const colors = useThemeColors();
  const dark = colors.mode === "dark";
  const narrow = !Grid.useBreakpoint().sm;
  const [sheetOpen, setSheetOpen] = useState(false);

  const summaryChip = summary ? (
    <div
      style={{
        background: colors.primarySoft,
        color: colors.primary,
        padding: `${space.sm}px 14px`,
        borderRadius: token.borderRadius,
        fontSize: fontSize.caption,
        fontWeight: fontWeight.medium,
        maxWidth: 420,
      }}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={changeKey ?? "summary"}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={FADE_TRANSITION}
        >
          {summary}
        </motion.div>
      </AnimatePresence>
    </div>
  ) : null;

  const itemValue = (it: CheckoutItem) => (
    <AnimatePresence mode="wait" initial={false}>
      <motion.span
        key={changeKey ?? it.label}
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={FADE_TRANSITION}
        style={{
          display: "inline-flex",
          alignItems: "baseline",
          gap: space.xs,
          fontSize: it.muted ? fontSize.body : fontSize.pageTitle,
          fontWeight: it.muted ? fontWeight.regular : fontWeight.semibold,
          color: it.muted ? token.colorText : colors.primary,
        }}
      >
        {it.value}
        {it.suffix && (
          <Typography.Text type="secondary" style={{ fontSize: fontSize.body }}>
            {it.suffix}
          </Typography.Text>
        )}
      </motion.span>
    </AnimatePresence>
  );

  const itemBlocks = items.map((it) => (
    <div key={it.label}>
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption, display: "block" }}>
        {it.label}
        {it.hint ? `(${it.hint})` : ""}
      </Typography.Text>
      {itemValue(it)}
    </div>
  ));

  const balanceNode =
    balance !== undefined ? (
      <Typography.Text type="secondary">
        {t("common.balance")}{" "}
        <span style={{ fontWeight: 600 }}>{moneyOr(formatMoney(balance ?? "0.00"), balanceReady)}</span>
      </Typography.Text>
    ) : null;

  const firstItem = items[0];

  return (
    <div
      style={{
        position: "sticky",
        bottom: 0,
        zIndex: zIndex.stickyBar,
        background: token.colorBgContainer,
        borderTop: `1px solid ${token.colorBorderSecondary}`,
        boxShadow: dark ? shadow.dark.upMd : shadow.light.upMd,
        borderRadius: `${token.borderRadiusLG}px ${token.borderRadiusLG}px 0 0`,
        padding: `${space.md}px ${space.xl}px`,
        display: "flex",
        flexDirection: "column",
        gap: space.sm,
      }}
    >
      {narrow ? (
        <>
          {noticeSummary != null ? (
            <Button
              type="link"
              size="small"
              style={{ paddingInline: 0, alignSelf: "flex-start", fontSize: fontSize.caption }}
              onClick={() => setSheetOpen(true)}
            >
              {noticeSummary} ›
            </Button>
          ) : (
            notice
          )}
          <div style={{ display: "flex", alignItems: "center", gap: space.md }}>
            <div
              style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "baseline", gap: space.xs, flexWrap: "wrap" }}
            >
              {firstItem && itemValue(firstItem)}
              <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={() => setSheetOpen(true)}>
                {t("common.detailsToggle")} ▴
              </Button>
            </div>
            <Space size={space.md}>{actions}</Space>
          </div>
          <Drawer
            placement="bottom"
            size="auto"
            open={sheetOpen}
            onClose={() => setSheetOpen(false)}
            title={t("common.costDetail")}
          >
            <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
              {notice && <div onClickCapture={() => setSheetOpen(false)}>{notice}</div>}
              {summaryChip}
              {itemBlocks}
              {breakdown}
              {detail}
              {balanceNode}
            </Space>
          </Drawer>
        </>
      ) : (
        <>
          {notice}
          <div style={{ display: "flex", alignItems: "center", gap: space.xl, flexWrap: "wrap" }}>
            {summaryChip}
            <Space size={space.xl} style={{ flex: 1, flexWrap: "wrap" }}>
              {itemBlocks}
              {detail && (
                <Popover content={detail} title={t("common.costDetail")} placement="topLeft">
                  <Button type="link" size="small">
                    {t("common.costDetail")}
                  </Button>
                </Popover>
              )}
              {balanceNode}
            </Space>
            <Space size={space.md}>{actions}</Space>
          </div>
          {breakdown && (
            <div style={{ borderTop: `1px dashed ${token.colorBorderSecondary}`, paddingTop: space.sm }}>
              {breakdown}
            </div>
          )}
        </>
      )}
    </div>
  );
}
