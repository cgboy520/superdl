/** Component health card: state, name, headline figure and two facts; click opens the diagnosis drawer. */

import { RightOutlined } from "@ant-design/icons";
import { Link } from "@tanstack/react-router";
import { Card, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { adminColors, componentHealthMap, fontSize, fontWeight, metaOf, space, useThemeColors } from "@superdl/ui";
import { CopyField, Mono, StatusTag } from "@superdl/ui/components";

import type { ClusterComponent, ComponentFact } from "../../api";
import { COMPONENT_LABEL, FACT_LABEL } from "./-componentMeta";

/** At most two facts on the front of the panel. */
const FACE_FACTS = 2;

export function ComponentPanel({ component }: { component: ClusterComponent }) {
  const { t } = useTranslation(["admin", "shared"]);
  const colors = useThemeColors();
  const state = component.state;
  const attention = state === "down" || state === "degraded";
  const headlineColor =
    state === "down" ? colors.negative : state === "degraded" ? colors.warning : colors.textSecondary;
  const faceFacts = (component.facts ?? []).slice(0, FACE_FACTS);

  return (
    <Link
      to="/cluster/$component"
      params={{ component: component.key }}
      aria-label={t(COMPONENT_LABEL[component.key])}
      style={{ display: "block", height: "100%", color: "inherit" }}
    >
      <Card
        size="small"
        style={{ height: "100%", borderColor: attention ? headlineColor : undefined }}
        styles={{ body: { display: "flex", flexDirection: "column", gap: space.sm } }}
      >
        <Space size={space.sm} style={{ justifyContent: "space-between", width: "100%" }}>
          <Space size={space.sm}>
            <StatusTag map={componentHealthMap} value={state} variant="dot" />
            <Typography.Text strong={attention}>{t(COMPONENT_LABEL[component.key])}</Typography.Text>
          </Space>
          <RightOutlined style={{ fontSize: fontSize.caption, color: adminColors.textMuted }} />
        </Space>

        {component.headline && (
          <div>
            <div
              style={{
                fontSize: fontSize.kpi,
                fontWeight: fontWeight.semibold,
                fontVariantNumeric: "tabular-nums",
                lineHeight: 1.1,
                color: headlineColor,
              }}
            >
              {component.headline.value}
            </div>
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              <FactLabel factKey={component.headline.key} />
            </Typography.Text>
          </div>
        )}

        <Space orientation="vertical" size={space.xs} style={{ width: "100%" }}>
          {faceFacts.map((f) => (
            <FactLine key={f.key} fact={f} />
          ))}
        </Space>

        {attention && component.fix_hint && (
          <CopyField value={component.fix_hint} code display={t("cluster.fixHint")} />
        )}
      </Card>
    </Link>
  );
}

export function FactLine({ fact }: { fact: ComponentFact }) {
  const colors = useThemeColors();
  const tone = fact.tone === "bad" ? colors.negative : fact.tone === "warn" ? colors.warning : undefined;
  return (
    <Space size={space.sm} style={{ justifyContent: "space-between", width: "100%" }}>
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        <FactLabel factKey={fact.key} />
      </Typography.Text>
      <Mono style={{ fontSize: fontSize.caption, color: tone }}>{fact.value}</Mono>
    </Space>
  );
}

/** Fact label; a key the frontend does not know yet is echoed raw, not through t(). */
export function FactLabel({ factKey }: { factKey: string }) {
  const { t } = useTranslation(["admin", "shared"]);
  const key = metaOf(FACT_LABEL, factKey);
  return <>{key ? t(key) : factKey}</>;
}
