/** 底部通栏结算条(sticky,市场页 / 创建页 / 部署页共用):费用项逐项摊开,日常费用与配置费用分栏。
 *  <sm 折叠为一行:价格大字 + 主按钮常驻,明细 / 余额 / 未完成项进「明细 ▴」底部 sheet(ui-ux-spec §3.5)。 */

import { fontSize, fontWeight, motion as motionToken, shadow, space, useThemeColors, zIndex } from "@superdl/ui";
import { moneyOr } from "@superdl/ui/components";
import { Button, Drawer, Grid, Popover, Space, theme, Typography } from "antd";
import { AnimatePresence, motion } from "motion/react";
import { useState, type ReactNode } from "react";

import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";
/** 选中变更淡入过渡(motion token fast 档;reducedMotion 下归零,见根 MotionConfig) */
const FADE_TRANSITION = {
  duration: motionToken.fast,
  ease: [...motionToken.easeOut] as [number, number, number, number],
};

export interface CheckoutItem {
  label: string;
  value: ReactNode;
  hint?: string;
  /** 大字后的口径后缀(「× 2 卡」/「整机」),正文字号 */
  suffix?: string;
  /** 非金额项(到期时间等)降级为正文字号与默认文字色 */
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
  /** 左侧规格汇总(靛蓝底块) */
  summary?: ReactNode;
  /** 费用项(label 小字在上,value 大号在下) */
  items: CheckoutItem[];
  /** 「费用明细」Popover 内容 */
  detail?: ReactNode;
  /** 余额(未登录不传) */
  balance?: string | null;
  /** 余额是否已就绪;false 时渲染 "—"。 */
  balanceReady?: boolean;
  actions: ReactNode;
  /** 选中变更标识(如 规格id+计费方式):变化时数字淡入;不传则无动效 */
  changeKey?: string;
  /** 摊开在条内第二行的明细(包周期「原价 / 优惠 / 应付」直接可见,不进 Popover) */
  breakdown?: ReactNode;
  /** 条上方的提示(未完成项清单 / 建盘失败告知),不随内容滚走 */
  notice?: ReactNode;
  /** <sm 时替代 notice 的一行摘要文案(如「还差 2 项」);点开底部 sheet 看全文 */
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
