/** Site-wide AttentionBar items derived from unread notifications: announcements / balance warnings / arrears; expiring / frozen / failed items live in InstanceAttention. */

import type { AttentionItem } from "@superdl/ui/components";
import { Link } from "@tanstack/react-router";
import { Button } from "antd";
import { useTranslation } from "react-i18next";

import { useNotifications } from "../api/queries";

export function useNotificationAttention(): AttentionItem[] {
  const { t } = useTranslation();
  const { data: unread } = useNotifications({ unread: true });
  const items: AttentionItem[] = [];
  const list = unread?.items ?? [];
  const announcement = list.find((n) => n.type === "announcement");
  if (announcement) {
    items.push({
      key: `announcement:${announcement.id}`,
      severity: "info",
      title: t("attention.announcement", { title: announcement.title }),
      description: announcement.content,
      action: (
        <Link to="/notifications">
          <Button size="small">{t("attention.viewNotifications")}</Button>
        </Link>
      ),
    });
  }
  if (list.some((n) => n.type === "arrears")) {
    items.push({
      key: "arrears",
      severity: "error",
      title: t("attention.arrears"),
      action: (
        <Link to="/billing">
          <Button size="small" type="primary" danger>
            {t("attention.goRecharge")}
          </Button>
        </Link>
      ),
    });
  } else if (list.some((n) => n.type === "balance_warn")) {
    items.push({
      key: "balance_warn",
      severity: "warning",
      title: t("attention.balanceWarn"),
      action: (
        <Link to="/billing">
          <Button size="small" type="primary">
            {t("attention.goRecharge")}
          </Button>
        </Link>
      ),
    });
  }
  return items;
}
