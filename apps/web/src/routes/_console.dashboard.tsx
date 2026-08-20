/** 概览:轻量首屏 —— 实例数/余额/今日消费/未读通知 + 快捷入口。 */

import { addAmounts, copy, formatMoney, localToday } from "@superdl/ui";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Alert, Button, Card, Col, Row, Space, Statistic, Typography } from "antd";

import { useDailySummary, useInstances, useNotifications, useWallet } from "../api/queries";
import { DataErrorAlert, moneyOr } from "../components/QueryState";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/dashboard")({
  beforeLoad: requireAuth,
  component: Overview,
});

function Overview() {
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
  const hasWarn = (unread ?? []).some((n) => n.type === "balance_warn" || n.type === "arrears");
  const announcement = (unread ?? []).find((n) => n.type === "announcement");
  const hasError = instancesQ.isError || walletQ.isError || dailyQ.isError;

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        概览
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
          title={`公告:${announcement.title}`}
          description={announcement.content}
        />
      )}
      {hasWarn && (
        <Alert
          type="warning"
          showIcon
          title="有余额或欠费相关预警,请查看通知并及时充值"
          action={
            <Link to="/billing">
              <Button size="small">去充值</Button>
            </Link>
          }
        />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={12} lg={6}>
          <Card>
            <Statistic title="实例总数" value={instances ? instances.length : "—"} />
            <Typography.Text type="secondary">
              {instances ? `运行中 ${running} 台` : " "}
            </Typography.Text>
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card>
            <Statistic
              title="可用余额"
              value={moneyOr(wallet?.balance, wallet != null)}
            />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card>
            <Statistic title="今日消费" value={todayTotal} />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card>
            <Statistic title="未读通知" value={unread ? unread.length : "—"} />
          </Card>
        </Col>
      </Row>
      <Card title="快捷入口">
        <Space wrap>
          <Link to="/market">
            <Button type="primary">租用新实例</Button>
          </Link>
          <Link to="/instances">
            <Button>管理实例</Button>
          </Link>
          <Link to="/storage">
            <Button>数据盘</Button>
          </Link>
          <Link to="/billing">
            <Button>充值</Button>
          </Link>
        </Space>
        <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
          {copy.diskRetention}
        </Typography.Paragraph>
      </Card>
    </Space>
  );
}
