/** 组件诊断抽屉体:判据 → 事实 → 对象明细 → 影响面 → 下一步。
 *  判据放第一段是刻意的:先说清这一项在判什么,再给数字,读者不必猜绿勾代表什么。 */

import { Alert, Card, Space, Table, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { componentHealthMap, fontSize, formatDateTime, space } from "@superdl/ui";
import { CopyField, EmptyValue, Mono, StatusTag, KeyValue } from "@superdl/ui/components";
import type { KeyValueItem } from "@superdl/ui/components";

import type { ClusterComponent, ComponentObject } from "../../api";
import {
  COMPONENT_CRITERION,
  COMPONENT_IMPACT,
  OBJECT_COLUMN_LABEL,
  OBJECT_COLUMNS,
  type ObjectColumn,
} from "./-componentMeta";
import { FactLabel } from "./-ComponentPanel";

export function ComponentDrawerBody({ component, probedAt }: { component: ClusterComponent; probedAt: string | null }) {
  const { t } = useTranslation(["admin", "shared"]);
  const state = component.state;
  const columns: readonly ObjectColumn[] = OBJECT_COLUMNS[component.key];
  const objects = component.objects ?? [];

  const facts: KeyValueItem[] = (component.facts ?? []).map((f) => ({
    label: <FactLabel factKey={f.key} />,
    value: <Mono>{f.value}</Mono>,
    copy: f.value,
  }));

  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
      <Card size="small" title={t("cluster.criterionTitle")}>
        <Typography.Text>{t(COMPONENT_CRITERION[component.key])}</Typography.Text>
      </Card>

      {state === "unknown" && (
        <Alert
          type="warning"
          showIcon
          title={t("cluster.staleSnapshot")}
          description={probedAt ? formatDateTime(probedAt) : t("cluster.neverProbed")}
        />
      )}

      <Card size="small" title={t("cluster.factsTitle")}>
        {facts.length > 0 ? <KeyValue items={facts} layout="grid" size="small" /> : <EmptyValue />}
      </Card>

      {objects.length > 0 && (
        <Card size="small" title={t("cluster.objectsTitle")}>
          <Table<ComponentObject>
            size="small"
            rowKey="name"
            pagination={false}
            dataSource={objects}
            scroll={{ x: true }}
            columns={[
              {
                title: t(OBJECT_COLUMN_LABEL.name),
                dataIndex: "name",
                render: (v: string) => <Mono>{v}</Mono>,
              },
              ...columns.map((col) => ({
                title: t(OBJECT_COLUMN_LABEL[col]),
                key: col,
                render: (_: unknown, row: ComponentObject) =>
                  row.fields[col] ? <Mono>{row.fields[col]}</Mono> : <EmptyValue />,
              })),
            ]}
          />
        </Card>
      )}

      {state !== "ok" && (
        <Card size="small" title={t("cluster.impactTitle")}>
          <Space orientation="vertical" size={space.sm}>
            <StatusTag map={componentHealthMap} value={state} variant="badge" icon />
            <Typography.Text>{t(COMPONENT_IMPACT[component.key])}</Typography.Text>
          </Space>
        </Card>
      )}

      <Card size="small" title={t("cluster.nextStepTitle")}>
        <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
          {component.diag_hint && (
            <div>
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {t("cluster.diagHint")}
              </Typography.Text>
              <CopyField value={component.diag_hint} code block />
            </div>
          )}
          {component.fix_hint && (
            <div>
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {t("cluster.fixHint")}
              </Typography.Text>
              <CopyField value={component.fix_hint} code block />
            </div>
          )}
        </Space>
      </Card>
    </Space>
  );
}
