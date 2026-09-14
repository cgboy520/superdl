/** 组件诊断抽屉体:判据 → 事实 → 对象明细 → 影响面 → 下一步。
 *  判据放第一段是刻意的:先说清这一项在判什么,再给数字,读者不必猜绿勾代表什么。 */

import { Alert, Card, Space, Table, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { componentHealthMap, fontSize, formatDateTime, space, useApiErrorText } from "@superdl/ui";
import { CopyField, EmptyValue, Mono, StatusTag, KeyValue } from "@superdl/ui/components";
import type { KeyValueItem } from "@superdl/ui/components";

import { type ClusterComponent, type ComponentObject, useComponentProbe } from "../../api";
import {
  COMPONENT_CRITERION,
  COMPONENT_IMPACT,
  OBJECT_COLUMN_LABEL,
  OBJECT_COLUMNS,
  LIVE_EVENT_COLUMNS,
  LIVE_POD_COLUMNS,
  type ObjectColumn,
} from "./-componentMeta";
import { FactLabel } from "./-ComponentPanel";

export function ComponentDrawerBody({ component, probedAt }: { component: ClusterComponent; probedAt: string | null }) {
  const { t } = useTranslation(["admin", "shared"]);
  const state = component.state;
  const columns: readonly ObjectColumn[] = OBJECT_COLUMNS[component.key];
  const objects = component.objects ?? [];
  // 抽屉打开即取一次现场:快照答「就绪几个」,深探答「为什么不就绪」
  const probe = useComponentProbe(component.key);

  const facts: KeyValueItem[] = [...(component.facts ?? []), ...(probe.data?.facts ?? [])].map((f) => ({
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

      <LiveDetail probe={probe} />

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

/** 实时深探区:未就绪对象的现场状态与它们的 Warning 事件。
 *  取不到只提示一行,不阻断抽屉 —— 集群 API 抖动不该放大成页面故障。 */
function LiveDetail({ probe }: { probe: ReturnType<typeof useComponentProbe> }) {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const pods = probe.data?.pods ?? [];
  const events = probe.data?.events ?? [];
  if (probe.isLoading) return <Card size="small" title={t("cluster.liveTitle")} loading />;
  if (probe.isError)
    return (
      <Card size="small" title={t("cluster.liveTitle")}>
        <Typography.Text type="secondary">{errText(probe.error, t("cluster.liveUnavailable"))}</Typography.Text>
      </Card>
    );
  if (pods.length === 0 && events.length === 0)
    return (
      <Card size="small" title={t("cluster.liveTitle")}>
        <Typography.Text type="secondary">{t("cluster.liveAllHealthy")}</Typography.Text>
      </Card>
    );
  return (
    <Card size="small" title={t("cluster.liveTitle")}>
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        {pods.length > 0 && (
          <Table<ComponentObject>
            size="small"
            rowKey="name"
            pagination={false}
            dataSource={pods}
            scroll={{ x: true }}
            columns={[
              {
                title: t(OBJECT_COLUMN_LABEL.name),
                dataIndex: "name",
                render: (v: string) => <Mono>{v}</Mono>,
              },
              ...LIVE_POD_COLUMNS.map((col) => ({
                title: t(OBJECT_COLUMN_LABEL[col]),
                key: col,
                render: (_: unknown, row: ComponentObject) =>
                  row.fields[col] ? <Mono>{row.fields[col]}</Mono> : <EmptyValue />,
              })),
            ]}
          />
        )}
        {events.length > 0 && (
          <Table<ComponentObject>
            size="small"
            rowKey={(r, i) => `${r.name}-${i ?? 0}`}
            pagination={false}
            dataSource={events}
            scroll={{ x: true }}
            columns={[
              {
                title: t(OBJECT_COLUMN_LABEL.name),
                dataIndex: "name",
                render: (v: string) => <Mono>{v}</Mono>,
              },
              ...LIVE_EVENT_COLUMNS.map((col) => ({
                title: t(OBJECT_COLUMN_LABEL[col]),
                key: col,
                render: (_: unknown, row: ComponentObject) =>
                  row.fields[col] ? <Mono>{row.fields[col]}</Mono> : <EmptyValue />,
              })),
            ]}
          />
        )}
      </Space>
    </Card>
  );
}
