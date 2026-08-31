/** 空态(两端统一):品牌线稿插画 + 一句话 + 至多两个动作。
 *  「错误」不归这里 —— 查询失败用 TableErrorEmpty/DataErrorAlert,绝不渲染成空数据。
 *  插画为自绘线稿 SVG,stroke=currentColor 随 antd colorTextDescription 自适应浅/暗/深色三主题;
 *  scene 决定插画与默认文案(shared:empty.*,可被 description 覆盖)。
 */

import { theme, Typography } from "antd";
import type { CSSProperties, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { fontSize, space } from "../tokens";

export type EmptyScene = "list" | "search" | "notification" | "disk" | "ticket";

const SCENE_PATHS: Record<EmptyScene, ReactNode> = {
  // 空托盘(列表无数据)
  list: (
    <>
      <path d="M20 44h80l-8 36a8 8 0 0 1-8 6H36a8 8 0 0 1-8-6l-8-36Z" />
      <path d="M20 44l10-14h60l10 14" />
      <path d="M48 58h24" strokeLinecap="round" strokeDasharray="2 5" />
    </>
  ),
  // 放大镜 + 空(搜索/筛选无结果)
  search: (
    <>
      <circle cx="54" cy="54" r="24" />
      <path d="M72 72l16 16" strokeLinecap="round" />
      <path d="M46 54h16" strokeLinecap="round" strokeDasharray="2 5" />
    </>
  ),
  // 铃铛(无通知)
  notification: (
    <>
      <path d="M60 88a8 8 0 0 0 16 0" />
      <path d="M60 30c-14 0-22 10-22 24 0 14-6 20-10 24h64c-4-4-10-10-10-24 0-14-8-24-22-24Z" />
      <path d="M56 30a4 4 0 0 1 8 0" strokeLinecap="round" />
    </>
  ),
  // 硬盘(无数据盘)
  disk: (
    <>
      <rect x="26" y="42" width="68" height="40" rx="6" />
      <path d="M26 66h68" />
      <circle cx="78" cy="74" r="3" strokeLinecap="round" />
      <path d="M34 34h52" strokeLinecap="round" strokeDasharray="2 5" />
    </>
  ),
  // 对话气泡(无工单)
  ticket: (
    <>
      <path d="M28 40h64a8 8 0 0 1 8 8v24a8 8 0 0 1-8 8H56l-14 12v-12h-14a8 8 0 0 1-8-8V48a8 8 0 0 1 8-8Z" />
      <path d="M44 58h32M44 68h20" strokeLinecap="round" strokeDasharray="2 5" />
    </>
  ),
};

function SceneIllustration({ scene, size }: { scene: EmptyScene; size: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 120 120"
      fill="none"
      stroke="currentColor"
      strokeWidth={3}
      strokeLinejoin="round"
      aria-hidden
      focusable="false"
    >
      {SCENE_PATHS[scene]}
    </svg>
  );
}

export function EmptyState({
  scene = "list",
  description,
  action,
  secondaryAction,
  compact,
  style,
}: {
  /** 场景(决定插画与默认文案);description 传入则覆盖默认文案 */
  scene?: EmptyScene;
  description?: ReactNode;
  /** 主动作(空态规范:一句话 + 一个动作) */
  action?: ReactNode;
  /** 次动作(如「清空筛选」) */
  secondaryAction?: ReactNode;
  /** 卡内/表内紧凑形态(插画 80,纵距收紧) */
  compact?: boolean;
  style?: CSSProperties;
}) {
  const { t } = useTranslation("shared");
  const { token } = theme.useToken();
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: space.md,
        padding: compact ? `${space.xl}px 0` : `${space.xxl}px 0`,
        ...style,
      }}
    >
      <span style={{ color: token.colorTextDescription, opacity: 0.75, lineHeight: 0 }}>
        <SceneIllustration scene={scene} size={compact ? 80 : 112} />
      </span>
      <Typography.Text type="secondary" style={{ fontSize: fontSize.body, textAlign: "center" }}>
        {description ?? t(`empty.${scene}`)}
      </Typography.Text>
      {(action || secondaryAction) && (
        <div style={{ display: "flex", gap: space.md, flexWrap: "wrap", justifyContent: "center" }}>
          {action}
          {secondaryAction}
        </div>
      )}
    </div>
  );
}
