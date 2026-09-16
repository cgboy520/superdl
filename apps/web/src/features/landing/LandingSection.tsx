/** Landing section skeleton: full-width background + 1200 centred content + unified vertical spacing; optional centred title / subtitle. */

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
  /** Override the bottom spacing (tightened when the next section follows closely) */
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
