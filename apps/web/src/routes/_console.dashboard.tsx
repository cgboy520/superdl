/** 概览:轻量首屏 —— 实例数/余额/今日消费/未读通知 + 快捷入口。 */

import { addAmounts, localToday } from "@superdl/ui";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Alert, Button, Card, Col, Row, Space, Statistic, Typography } from "antd";

import { useFormat } from "../lib/format";
import { useDailySummary, useInstances, useNotifications, useWallet } from "../api/queries";
import { DataErrorAlert, moneyOr } from "../components/QueryState";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/dashboard")({
  beforeLoad: requireAuth,
  component: Overview,
});

function Overview() {
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const instancesQ = useInstances();
  const walletQ = useWallet();
  const { data: instances } = instancesQ;
  const { data: wallet } = walletQ;
  const { data: unread } = useNotifications({ unread: true });
  const { date, tzOffsetMinutes } = localToday();
  const dailyQ = useDailySummary(date, tzOffsetMinutes);
  const { data: daily } = dailyQ;
  const todayTotal = daily ? formatMoney(addAmounts(daily.gpu_total, daily.disk_total)) : "—";

  const running = instances?.filter((i) => i.status === "running").length ?? 0;
  const hasWarn = (unread?.items ?? []).some((n) => n.type === "balance_warn" || n.type === "arrears");
  const announcement = (unread?.items ?? []).find((n) => n.type === "announcement");
  const hasError = instancesQ.isError || walletQ.isError || dailyQ.isError;

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("dashboard.title")}
      </Typography.Title>
      {hasError && (
        <DataErrorAlert
          onRetry={() => {
            void instancesQ.refetch();
            void walletQ.refetch();
            void dailyQ.refetch();
          }}
        />
      )}
      {announcement && (
        <Alert
          type="info"
          showIcon
          title={t("dashboard.announcementPrefix", { title: announcement.title })}
          description={announcement.content}
        />
      )}
      {hasWarn && (
        <Alert
          type="warning"
          showIcon
          title={t("dashboard.balanceWarn")}
          action={
            <Link to="/billing">
              <Button size="small">{t("dashboard.goRecharge")}</Button>
            </Link>
          }
        />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={12} lg={6}>
          <Card>
            <Statistic title={t("dashboard.totalInstances")} value={instances ? instances.length : "—"} />
            <Typography.Text type="secondary">
              {instances ? t("dashboard.runningCount", { count: running }) : " "}
            </Typography.Text>
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card>
            <Statistic
              title={t("billing.availableBalance")}
              value={moneyOr(formatMoney(wallet?.balance), wallet != null)}
            />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card>
            <Statistic title={t("instances.labelToday")} value={todayTotal} />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card>
            <Statistic title={t("dashboard.unread")} value={unread ? unread.items.length : "—"} />
          </Card>
        </Col>
      </Row>
      <Card title={t("dashboard.quickEntries")}>
        <Space wrap>
          <Link to="/market">
            <Button type="primary">{t("instances.rentNew")}</Button>
          </Link>
          <Link to="/instances">
            <Button>{t("dashboard.manageInstances")}</Button>
          </Link>
          <Link to="/storage">
            <Button>{t("dashboard.dataDisks")}</Button>
          </Link>
          <Link to="/billing">
            <Button>{t("billing.recharge")}</Button>
          </Link>
        </Space>
      </Card>
    </Space>
  );
}
