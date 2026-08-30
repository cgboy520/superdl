/** 底部通栏结算条(sticky,市场页与创建页共用):费用项逐项摊开,日常费用与配置费用分栏。 */

import { brand, colorPrimary, fontSize, fontWeight, motion as motionToken, shadow, space, webDarkColors, zIndex } from "@superdl/ui";
import { moneyOr } from "@superdl/ui/components";
import { Button, Grid, Popover, Space, theme, Typography } from "antd";
import { AnimatePresence, motion } from "motion/react";
import type { ReactNode } from "react";

import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";
import { useThemeMode } from "../stores/theme";

/** 选中变更淡入过渡(motion token fast 档;装饰性动效在 reducedMotion 下归零,见根 MotionConfig) */
const FADE_TRANSITION = {
  duration: motionToken.fast,
  ease: [...motionToken.easeOut] as [number, number, number, number],
};

export interface CheckoutItem {
  label: string;
  value: ReactNode;
  hint?: string;
}

export function CheckoutBar({
  summary,
  items,
  detail,
  balance,
  balanceReady = true,
  actions,
  changeKey,
}: {
  /** 左侧规格汇总(靛蓝底块) */
  summary?: ReactNode;
  /** 费用项(label 小字在上,value 大号在下) */
  items: CheckoutItem[];
  /** 「费用明细」Popover 内容 */
  detail?: ReactNode;
  /** 余额(未登录不传) */
  balance?: string | null;
  /** 余额是否已就绪。false 时必须渲染 "—" 而不是假 ¥0.00(查询失败时 data 恒为 undefined)。 */
  balanceReady?: boolean;
  actions: ReactNode;
  /** 选中变更标识(如 规格id+计费方式):变化时汇总/费用数字淡入;不传则无动效 */
  changeKey?: string;
}) {
  const { token } = theme.useToken();
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const dark = useThemeMode() === "dark";
  // <sm 断点:两个动作按钮竖排整行(走类,内联样式选不到子代 button)
  const narrow = !Grid.useBreakpoint().sm;
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
        alignItems: "center",
        gap: space.xl,
        flexWrap: "wrap",
      }}
    >
      {summary && (
        <div
          style={{
            // 暗色下浅靛块脱节:换暗色「菜单选中」配对(menuSelectedBg/Color 是 tokens.test 回归的 AA 对)
            background: dark ? webDarkColors.menuSelectedBg : brand.indigo50,
            color: dark ? webDarkColors.menuSelectedColor : colorPrimary,
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
      )}
      <Space size={24} style={{ flex: 1, flexWrap: "wrap" }}>
        {items.map((it) => (
          <div key={it.label}>
            <Typography.Text
              type="secondary"
              style={{ fontSize: fontSize.caption, display: "block" }}
            >
              {it.label}
              {it.hint ? `(${it.hint})` : ""}
            </Typography.Text>
            <AnimatePresence mode="wait" initial={false}>
              <motion.span
                key={changeKey ?? it.label}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={FADE_TRANSITION}
                style={{
                  display: "inline-block",
                  fontSize: fontSize.pageTitle,
                  fontWeight: fontWeight.semibold,
                  color: colorPrimary,
                }}
              >
                {it.value}
              </motion.span>
            </AnimatePresence>
          </div>
        ))}
        {detail && (
          <Popover content={detail} title={t("common.costDetail")} placement="topLeft">
            {/* 纯动作触发器用 Button 不用 Typography.Link:无 href 的链接不可聚焦、无键盘语义 */}
            <Button type="link" size="small">
              {t("common.costDetail")}
            </Button>
          </Popover>
        )}
        {balance !== undefined && (
          <Typography.Text type="secondary">
            {t("common.balance")}{" "}
            <span style={{ fontWeight: 600 }}>{moneyOr(formatMoney(balance), balanceReady)}</span>
          </Typography.Text>
        )}
      </Space>
      <Space
        size={12}
        orientation={narrow ? "vertical" : "horizontal"}
        className={narrow ? "checkout-bar-actions-block" : undefined}
        style={narrow ? { width: "100%" } : undefined}
      >
        {actions}
      </Space>
    </div>
  );
}
