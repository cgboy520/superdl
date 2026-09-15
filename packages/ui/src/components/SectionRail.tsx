/** 分段表单导航:宽屏竖向固定、窄屏横向排列,段状态由问题、触碰与提交状态派生。 */

import { Card, Grid, Steps } from "antd";
import type { ReactNode } from "react";

import { layout, space } from "../tokens";

export interface SectionDef {
  id: string;
  title: ReactNode;
  /** 当前段的第一个问题(无问题传 null / undefined) */
  issue?: ReactNode | null;
  /** 用户是否碰过本段(默认 true) */
  touched?: boolean;
}

export type SectionStatus = "wait" | "process" | "error" | "finish";

export function deriveSectionStatus(sections: SectionDef[], submitted = false): SectionStatus[] {
  const firstIssue = sections.findIndex((s) => Boolean(s.issue));
  return sections.map((s, i) => {
    const touched = s.touched ?? true;
    if (s.issue) {
      if (i === firstIssue) return "process";
      return touched || submitted ? "error" : "wait";
    }
    return touched ? "finish" : "wait";
  });
}

export function scrollToSection(id: string): void {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** 带锚点的段:默认渲染为 Card;card=false 时只包一层带 scroll-margin 的 div。 */
export function SectionAnchor({
  id,
  title,
  extra,
  children,
  card = true,
}: {
  id: string;
  title?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
  card?: boolean;
}) {
  const style = { scrollMarginTop: layout.scrollMarginTop } as const;
  if (!card) {
    return (
      <div id={id} style={style}>
        {children}
      </div>
    );
  }
  return (
    <Card id={id} title={title} extra={extra} style={style}>
      {children}
    </Card>
  );
}

export function SectionRail({
  sections,
  submitted,
  children,
  railWidth = 200,
  ariaLabel,
}: {
  sections: SectionDef[];
  /** 提交后其余问题段标为 error;首个问题段仍为 process。 */
  submitted?: boolean;
  children: ReactNode;
  railWidth?: number;
  ariaLabel: string;
}) {
  const screens = Grid.useBreakpoint();
  const wide = screens.md ?? true;
  const statuses = deriveSectionStatus(sections, submitted);
  const items = sections.map((s, i) => ({
    title: s.title,
    status: statuses[i],
    description: s.issue && statuses[i] !== "wait" ? s.issue : undefined,
  }));
  const steps = (
    <Steps
      size="small"
      orientation={wide ? "vertical" : "horizontal"}
      responsive={false}
      items={items}
      onChange={(i) => {
        const target = sections[i];
        if (target) scrollToSection(target.id);
      }}
    />
  );
  if (!wide) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: space.lg }}>
        <nav aria-label={ariaLabel}>{steps}</nav>
        {children}
      </div>
    );
  }
  return (
    <div style={{ display: "flex", gap: space.xl, alignItems: "flex-start" }}>
      <nav
        aria-label={ariaLabel}
        style={{ width: railWidth, flex: "none", position: "sticky", top: layout.scrollMarginTop }}
      >
        {steps}
      </nav>
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: space.lg }}>{children}</div>
    </div>
  );
}
