/** Data freshness row (shared by both consoles): updated at · auto-refresh every N s · pause / refresh. Page headers use it via PageHeader.freshness, local panels directly. */

import { PauseCircleOutlined, PlayCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Space, Tooltip, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { formatDateTime } from "../format";
import { useNow } from "../hooks/useNow";
import { fontSize, space } from "../tokens";

export interface FreshnessProps {
  /** react-query dataUpdatedAt (0 = no data yet) */
  updatedAt: number;
  /** Auto-refresh period (ms); false = the page does not poll, only "updated at" and manual refresh */
  intervalMs: number | false;
  paused?: boolean;
  onTogglePause?: () => void;
  onRefresh?: () => void;
  refreshing?: boolean;
}

export function Freshness({ updatedAt, intervalMs, paused, onTogglePause, onRefresh, refreshing }: FreshnessProps) {
  const { t } = useTranslation("shared");
  const now = useNow(1_000);
  const seconds = updatedAt > 0 ? Math.max(0, Math.round((now - updatedAt) / 1000)) : null;
  return (
    <Space size={space.sm} wrap style={{ fontSize: fontSize.caption }}>
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        {seconds === null
          ? t("freshness.noData")
          : seconds < 60
            ? t("freshness.updatedSecondsAgo", { count: seconds })
            : t("freshness.updatedAt", { time: formatDateTime(new Date(updatedAt).toISOString()) })}
      </Typography.Text>
      {intervalMs !== false && (
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {paused ? t("freshness.paused") : t("freshness.autoRefresh", { seconds: Math.round(intervalMs / 1000) })}
        </Typography.Text>
      )}
      {intervalMs !== false && onTogglePause && (
        <Tooltip title={paused ? t("freshness.resume") : t("freshness.pause")}>
          <Button
            type="text"
            size="small"
            aria-label={paused ? t("freshness.resume") : t("freshness.pause")}
            icon={paused ? <PlayCircleOutlined /> : <PauseCircleOutlined />}
            onClick={onTogglePause}
          />
        </Tooltip>
      )}
      {onRefresh && (
        <Tooltip title={t("freshness.refresh")}>
          <Button
            type="text"
            size="small"
            aria-label={t("freshness.refresh")}
            icon={<ReloadOutlined />}
            loading={refreshing}
            onClick={onRefresh}
          />
        </Tooltip>
      )}
    </Space>
  );
}
