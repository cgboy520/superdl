/** 数据盘卡:「不需要 / 新建 / 挂载已有盘」。新建为行内直建(名称 + 容量滑块,单价来自 /policies 折日展示),建盘动作由页面在提交时执行;盘清单加载失败不伪装成「没有可挂载的盘」。 */

import { diskDailyEstimate, formatSizeGb } from "@superdl/ui";
import { DataErrorAlert } from "@superdl/ui/components";
import { Card, Flex, Input, InputNumber, Radio, Select, Slider, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useDisks, usePolicies } from "../../api/queries";

export type DiskMode = "none" | "new" | "existing";

export function defaultDiskName(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `data-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
}

export function DataDiskCard({
  mode,
  onModeChange,
  newName,
  onNewNameChange,
  newGb,
  onNewGbChange,
  existingId,
  onExistingIdChange,
}: {
  mode: DiskMode;
  onModeChange: (mode: DiskMode) => void;
  newName: string;
  onNewNameChange: (name: string) => void;
  newGb: number;
  onNewGbChange: (gb: number) => void;
  existingId: number | undefined;
  onExistingIdChange: (id: number | undefined) => void;
}) {
  const { t } = useTranslation();
  const disksQ = useDisks();
  const { data: policies } = usePolicies();
  const diskPriceGbMonth = policies?.disk_price_gb_month;
  // 「约 ¥X/日」为展示层估算(月价/30,BigInt);入账以后端日结为准
  const diskDaily = diskDailyEstimate(diskPriceGbMonth, newGb);

  return (
    <Card title={t("create.diskCard")}>
      <Space orientation="vertical" size={12} style={{ width: "100%" }}>
        <Radio.Group
          value={mode}
          onChange={(e) => onModeChange(e.target.value as DiskMode)}
          options={[
            { value: "none", label: t("create.diskNone") },
            { value: "new", label: t("create.diskNew") },
            { value: "existing", label: t("create.diskExisting") },
          ]}
        />
        {mode === "new" && (
          <>
            <Space size={12} style={{ width: "100%", maxWidth: 420 }}>
              <Typography.Text type="secondary">{t("storage.nameLabel")}</Typography.Text>
              <Input
                style={{ width: "100%", maxWidth: 260 }}
                maxLength={64}
                aria-label={t("storage.nameLabel")}
                value={newName}
                onChange={(e) => onNewNameChange(e.target.value)}
              />
            </Space>
            {/* 容量:Slider 与 InputNumber 联动同值 */}
            <Flex gap={12} align="center">
              <Slider
                style={{ flex: 1 }}
                min={policies?.disk_min_gb}
                max={policies?.disk_max_gb}
                step={10}
                value={newGb}
                onChange={onNewGbChange}
                disabled={!policies}
              />
              <InputNumber
                min={policies?.disk_min_gb}
                max={policies?.disk_max_gb}
                step={10}
                value={newGb}
                onChange={(v) => {
                  if (typeof v === "number") onNewGbChange(v);
                }}
                disabled={!policies}
                style={{ width: 110 }}
                aria-label={t("create.diskSizeAria")}
              />
            </Flex>
            <Typography.Text type="secondary">
              {formatSizeGb(newGb)}
              {diskPriceGbMonth
                ? ` · ${t("common.gbMonthPrice", { price: diskPriceGbMonth })},${t("common.dailyApprox", { amount: diskDaily })}`
                : ""}
              ;{t("create.diskAutoCreateNote")}
            </Typography.Text>
          </>
        )}
        {mode === "existing" &&
          (disksQ.isError ? (
            <DataErrorAlert onRetry={() => void disksQ.refetch()} />
          ) : (
            <Select
              style={{ width: "100%", maxWidth: 320 }}
              placeholder={t("create.selectDiskPlaceholder")}
              value={existingId}
              onChange={onExistingIdChange}
              options={(disksQ.data ?? [])
                .filter((d) => d.status === "active" && d.mounted_instance_id == null)
                .map((d) => ({
                  value: d.id,
                  label: `${d.name}(${formatSizeGb(d.size_gb)})`,
                }))}
              notFoundContent={t("create.noMountableDisks")}
            />
          ))}
        <Typography.Text type="secondary">{t("create.diskIndependentNote")}</Typography.Text>
      </Space>
    </Card>
  );
}
