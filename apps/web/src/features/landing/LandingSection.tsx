/** 落地页 section 骨架:满宽底色 + 内容区 1200 居中 + 统一纵向留白;可选居中标题 / 副标题。 */

import { layout, space } from "@superdl/ui";
import { Typography } from "antd";
import type { CSSProperties, ReactNode } from "react";

export function LandingSection({
  id,
  background,
  title,
  subtitle,
  children,
  paddingBottom,
  style,
}: {
  id?: string;
  background?: string;
  title?: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  /** 覆盖底部留白(紧接下一段时收窄) */
  paddingBottom?: number;
  style?: CSSProperties;
}) {
  return (
    <section id={id} style={{ background, ...style }}>
      <div
        style={{
          maxWidth: layout.pageMaxWidthWide,
          margin: "0 auto",
          padding: `${layout.sectionPaddingY}px ${layout.contentPadding}px ${paddingBottom ?? layout.sectionPaddingY}px`,
        }}
      >
        {title && (
          <Typography.Title level={2} style={{ textAlign: "center", marginBottom: space.xs }}>
            {title}
          </Typography.Title>
        )}
        {subtitle && (
          <Typography.Paragraph type="secondary" style={{ textAlign: "center", marginBottom: space.xl }}>
            {subtitle}
          </Typography.Paragraph>
        )}
        {children}
      </div>
    </section>
  );
}
