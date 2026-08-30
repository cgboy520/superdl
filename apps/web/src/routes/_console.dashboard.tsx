/** 概览:轻量首屏 —— 实例数/余额/今日消费/未读通知 + 快捷入口。 */

import { addAmounts, localToday } from "@superdl/ui";
import { DataErrorAlert, KpiGrid, moneyOr } from "@superdl/ui/components";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Card, Skeleton, Space, Statistic, Steps, Typography } from "antd";

import { useFormat } from "@superdl/ui";
import {
  useDailySummary,
  useInstances,
  useNotifications,
  useRefundableOrders,
  useUnreadCount,
  useWallet,
} from "../api/queries";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/dashboard")({
  beforeLoad: requireAuth,
  component: Overview,
});

const ONBOARDING_DISMISS_KEY = "superdl.web.onboardingDismissed";

/** 逐卡 KPI 骨架:各卡只等自己的 query——整体 loading 会让先就绪的卡先出「—」再跳数,
 *  也会被最慢的一张拖住;失败语义留在各卡内(DataErrorAlert 或 moneyOr 「—」,不伪装成数据)。 */
function KpiCard({ pending, children }: { pending: boolean; children?: ReactNode }) {
  if (pending) {
    return (
      <Card>
        <Skeleton active title={{ width: "40%" }} paragraph={{ rows: 1, width: "70%" }} />
      </Card>
    );
  }
  return <Card>{children}</Card>;
}

/** 新用户引导:无实例且无已支付充值时显示 充值→选规格→开机 三步卡;
 * 有实例即永久隐藏(后端状态派生),手动关闭记 localStorage。 */
function OnboardingCard() {
  const { t } = useTranslation();
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(ONBOARDING_DISMISS_KEY) === "1",
  );
  const { data: instances } = useInstances();
  const { data: orders } = useRefundableOrders();
  if (dismissed || instances == null || orders == null) return null;
  if (instances.length > 0) return null; // 已完成全流程:派生隐藏
  const hasPaid = orders.length > 0;
  const current = hasPaid ? 1 : 0;
  return (
    <Card
      title={t("dashboard.onboardingTitle")}
      extra={
        <Button
          size="small"
          type="text"
          onClick={() => {
            localStorage.setItem(ONBOARDING_DISMISS_KEY, "1");
            setDismissed(true);
          }}
        >
          {t("dashboard.onboardingDismiss")}
        </Button>
      }
    >
      <Steps
        size="small"
        current={current}
        items={[
          {
            title: t("dashboard.onboardingStep1"),
            description: (
              <Link to="/billing">{t("dashboard.onboardingStep1Action")}</Link>
            ),
          },
          {
            title: t("dashboard.onboardingStep2"),
            description: <Link to="/market">{t("dashboard.onboardingStep2Action")}</Link>,
          },
          {
            title: t("dashboard.onboardingStep3"),
            description: t("dashboard.onboardingStep3Desc"),
          },
        ]}
      />
    </Card>
  );
}

function Overview() {
  const { t } = useTranslation();
  const { formatMoney } = useFormat();
  const instancesQ = useInstances();
  const walletQ = useWallet();
  const { data: instances } = instancesQ;
  const { data: wallet } = walletQ;
  // 未读列表只用于余额告警/公告横幅;未读数走轻端点(与顶栏角标同一缓存)
  const { data: unread } = useNotifications({ unread: true });
  const unreadCountQ = useUnreadCount();
  const { data: unreadCount } = unreadCountQ;
  const { date, tzOffsetMinutes } = localToday();
  const dailyQ = useDailySummary(date, tzOffsetMinutes);
  const { data: daily } = dailyQ;
  const todayTotal = daily ? formatMoney(addAmounts(daily.gpu_total, daily.disk_total)) : "—";

  const running = instances?.filter((i) => i.status === "running").length ?? 0;
  const hasWarn = (unread?.items ?? []).some((n) => n.type === "balance_warn" || n.type === "arrears");
  const announcement = (unread?.items ?? []).find((n) => n.type === "announcement");

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("dashboard.title")}
      </Typography.Title>
      <OnboardingCard />
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
      {/* 逐卡骨架:不再整体 loading(AND 会让先就绪的卡先出「—」再跳数);
          各卡 isPending 骨架 / 失败嵌 DataErrorAlert 或 moneyOr「—」 */}
      <KpiGrid
        items={[
          <KpiCard key="instances" pending={instancesQ.isPending}>
            {instancesQ.isError ? (
              <DataErrorAlert onRetry={() => void instancesQ.refetch()} />
            ) : (
              <>
                <Statistic title={t("dashboard.totalInstances")} value={instances ? instances.length : "—"} />
                <Typography.Text type="secondary">
                  {instances ? t("dashboard.runningCount", { count: running }) : " "}
                </Typography.Text>
              </>
            )}
          </KpiCard>,
          <KpiCard key="balance" pending={walletQ.isPending}>
            {walletQ.isError ? (
              <DataErrorAlert onRetry={() => void walletQ.refetch()} />
            ) : (
              <Statistic
                title={t("billing.availableBalance")}
                value={moneyOr(formatMoney(wallet?.balance), wallet != null)}
              />
            )}
          </KpiCard>,
          <KpiCard key="today" pending={dailyQ.isPending}>
            {dailyQ.isError ? (
              <DataErrorAlert onRetry={() => void dailyQ.refetch()} />
            ) : (
              <Statistic title={t("instances.labelToday")} value={todayTotal} />
            )}
          </KpiCard>,
          <KpiCard key="unread" pending={unreadCountQ.isPending}>
            {/* 与其它三卡同一纪律:查询失败明示可重试,「—」只表达未就绪 */}
            {unreadCountQ.isError ? (
              <DataErrorAlert onRetry={() => void unreadCountQ.refetch()} />
            ) : (
              <Link to="/notifications" style={{ color: "inherit" }}>
                <Statistic title={t("dashboard.unread")} value={unreadCount ? unreadCount.unread_count : "—"} />
              </Link>
            )}
          </KpiCard>,
        ]}
      />
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
