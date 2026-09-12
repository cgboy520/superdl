/** 到期 / 冻结 / 失败三类实例条目(与通知类条目一起进 AttentionBar)。到期条目带「立即续费」「开启自动续费」。 */

import { isBillingPeriod, periodMap, space } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { App, Button, Space } from "antd";
import { useTranslation } from "react-i18next";

import type { InstanceOut } from "@superdl/api-client";
import { formatDateTime } from "@superdl/ui";
import { useFormat } from "@superdl/ui";
import { useSetAutoRenew } from "../api/mutations";
import { useExpiringInstances, usePolicies } from "../api/queries";
import type { AttentionItem } from "./AttentionBar";

export function useInstanceAttention(rows: InstanceOut[], onRenew: (i: InstanceOut) => void): AttentionItem[] {
  const { t } = useTranslation(["web", "shared"]);
  const { formatExpiry, formatReclaimCountdown } = useFormat();
  const { message } = App.useApp();
  const { data: policies } = usePolicies();
  const { data: soon } = useExpiringInstances(policies?.period_expire_warn_days);
  const autoRenew = useSetAutoRenew(soon?.[0]?.uuid ?? "", {
    onSuccess: () => {
      message.success(t("period.autoRenewOn"));
    },
  });
  const items: AttentionItem[] = [];
  for (const inst of (soon ?? []).slice(0, 3)) {
    const sub = inst.subscription;
    if (!sub) continue;
    const periodKey = isBillingPeriod(sub.period) ? periodMap[sub.period].labelKey : null;
    items.push({
      key: `expiring:${inst.uuid}`,
      severity: "warning",
      title: t("attention.expiring", {
        name: inst.name,
        period: periodKey ? t(periodKey) : sub.period,
        left: formatExpiry(sub.expires_at) ?? "",
        time: formatDateTime(sub.expires_at),
      }),
      description: policies
        ? t("copy.periodExpirePolicy", { hours: policies.freeze_grace_hours })
        : t("copy.periodExpirePolicyFallback"),
      action: (
        <Space size={space.sm}>
          <Button size="small" type="primary" onClick={() => onRenew(inst)}>
            {t("period.renewNow")}
          </Button>
          {!sub.auto_renew && inst.uuid === soon?.[0]?.uuid && (
            <Button size="small" loading={autoRenew.isPending} onClick={() => autoRenew.mutate(true)}>
              {t("period.autoRenewOnMenu")}
            </Button>
          )}
        </Space>
      ),
    });
  }
  for (const inst of rows.filter((r) => r.status === "frozen").slice(0, 3)) {
    items.push({
      key: `frozen:${inst.uuid}`,
      severity: "error",
      title: t("attention.frozen", {
        name: inst.name,
        left: inst.frozen_deadline ? formatReclaimCountdown(inst.frozen_deadline) : "",
      }),
      action: (
        <Link to="/billing">
          <Button size="small" type="primary" danger>
            {t("attention.goRecharge")}
          </Button>
        </Link>
      ),
    });
  }
  for (const inst of rows.filter((r) => r.status === "failed").slice(0, 3)) {
    items.push({
      key: `failed:${inst.uuid}`,
      severity: "error",
      title: t("attention.failed", { name: inst.name }),
      action: (
        <Link to="/instances/$uuid" params={{ uuid: inst.uuid }} search={{ tab: "events" }}>
          <Button size="small">{t("attention.viewEvents")}</Button>
        </Link>
      ),
    });
  }
  return items;
}
