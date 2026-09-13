/** 财务对账卡:事件流水 vs 指标估算的差异,超阈值给 warning;对账日期由财务页的 ?day= 持有。 */

import { Link } from "@tanstack/react-router";
import { Button, Card, Col, DatePicker, Row, Space, Statistic, Table, Tag } from "antd";
import dayjs from "dayjs";
import { useTranslation } from "react-i18next";

import { adminColors } from "@superdl/ui";
import { DataErrorAlert, Mono, moneyOr } from "@superdl/ui/components";
import { useCsvExport } from "@superdl/ui";
import { useFormat } from "@superdl/ui";

import { type ReconciliationReport, exportReconciliationCsv, useReconciliation } from "../../api";

// 对账 diff 标红阈值(%)
export const RECONCILE_DIFF_WARN_PCT = 2;

export function ReconciliationCard({
  day: dayParam,
  onDayChange,
}: {
  /** ?day=(YYYY-MM-DD);缺省 = 今天(默认值不入 URL) */
  day?: string;
  onDayChange: (day: string) => void;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  const day = dayParam ? dayjs(dayParam) : dayjs();
  const { data: report, isError, refetch } = useReconciliation(day.format("YYYY-MM-DD"));
  const diffHigh = report != null && report.diff_pct > RECONCILE_DIFF_WARN_PCT;
  const { doExport, exporting } = useCsvExport((_tz, lang) => exportReconciliationCsv(day.format("YYYY-MM-DD"), lang));

  return (
    <Card
      title={t("finance.reconTitle")}
      extra={
        <Space>
          <DatePicker
            value={day}
            onChange={(d) => {
              if (d) onDayChange(d.format("YYYY-MM-DD"));
            }}
            allowClear={false}
            disabledDate={(d) => d.isAfter(dayjs(), "day")}
          />
          <Button onClick={() => void doExport()} loading={exporting}>
            {t("common.exportCsv")}
          </Button>
        </Space>
      }
    >
      {isError && (
        <DataErrorAlert
          style={{ marginBottom: 12 }}
          title={t("common.loadFailed", { ns: "shared" })}
          description={null}
          onRetry={() => void refetch()}
        />
      )}
      <Row gutter={16}>
        <Col xs={24} sm={12} md={8}>
          <Statistic
            title={t("finance.billedTotal")}
            value={moneyOr(formatMoney(report?.billed_total ?? "0.00"), report != null)}
          />
        </Col>
        <Col xs={24} sm={12} md={8}>
          <Statistic
            title={t("finance.estimatedTotal")}
            value={moneyOr(formatMoney(report?.estimated_total ?? "0.00"), report != null)}
          />
        </Col>
        <Col xs={24} sm={12} md={8}>
          <Statistic
            title={t("finance.diffRate")}
            value={report ? report.diff_pct : "—"}
            suffix={report ? "%" : undefined}
            styles={{
              content:
                report == null
                  ? undefined
                  : diffHigh
                    ? { color: adminColors.negative }
                    : { color: adminColors.positive },
            }}
          />
        </Col>
      </Row>
      {report && report.outliers.length > 0 && (
        <Table<ReconciliationReport["outliers"][number]>
          size="small"
          style={{ marginTop: 16 }}
          rowKey="instance_id"
          dataSource={report.outliers}
          pagination={false}
          columns={[
            {
              title: t("finance.colInstanceId"),
              dataIndex: "instance_id",
              // 差异实例直链到全局实例表(按 id 检索)
              render: (v: number) => (
                <Link to="/tenants" search={{ tab: "instances", iq: String(v) }}>
                  <Mono>{String(v)}</Mono>
                </Link>
              ),
            },
            {
              title: t("finance.colBilled"),
              dataIndex: "billed",
              align: "right",
              render: (v: string) => formatMoney(v),
            },
            {
              title: t("finance.colEstimated"),
              dataIndex: "estimated",
              align: "right",
              render: (v: string) => formatMoney(v),
            },
            {
              title: t("finance.diffRate"),
              dataIndex: "diff_pct",
              align: "right",
              render: (v: number) => <Tag color="red">{v}%</Tag>,
            },
          ]}
        />
      )}
    </Card>
  );
}
