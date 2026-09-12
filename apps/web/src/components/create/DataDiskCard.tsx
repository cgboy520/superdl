/** 数据盘卡:「不需要 / 新建 / 挂载已有盘」。新建为行内直建(容量滑块在前,名称折在「高级」里;单价来自 /policies 折日展示),建盘动作由页面在提交时执行;盘清单加载失败不伪装成「没有可挂载的盘」。
 *  实例盘为本地盘、不做冗余的说明放在本卡脚注(与「要不要数据盘」这个决定直接相关),不做页顶常驻条。 */

import { controlWidth, diskDailyEstimate, fontSize, formatSizeGb, space } from "@superdl/ui";
import { DataErrorAlert } from "@superdl/ui/components";
import { Card, Collapse, Flex, Input, InputNumber, Radio, Select, Slider, Space, Typography } from "antd";
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
  id,
  variant = "card",
}: {
  mode: DiskMode;
  onModeChange: (mode: DiskMode) => void;
  newName: string;
  onNewNameChange: (name: string) => void;
  newGb: number;
  onNewGbChange: (gb: number) => void;
  existingId: number | undefined;
  onExistingIdChange: (id: number | undefined) => void;
  /** 锚点 id(未完成项清单跳转用) */
  id?: string;
  /** card = 独立卡(创建页);section = 嵌在别的卡里,只出标题行(部署页容器配置段) */
  variant?: "card" | "section";
}) {
  const { t } = useTranslation();
  const disksQ = useDisks();
  const { data: policies } = usePolicies();
  const diskPriceGbMonth = policies?.disk_price_gb_month;
  // 「约 ¥X/日」为展示层估算(月价/30,BigInt);入账以后端日结为准
  const diskDaily = diskDailyEstimate(diskPriceGbMonth, newGb);

  const body = (
    <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
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
          {/* 容量:Slider 与 InputNumber 联动同值;min/max 取 /policies */}
          <Flex gap={space.md} align="center">
            <Slider
              style={{ flex: 1, maxWidth: 480 }}
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
              style={{ width: controlWidth.xs + 14 }}
              addonAfter="GB"
              aria-label={t("create.diskSizeAria")}
            />
          </Flex>
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {diskPriceGbMonth
              ? t("create.diskNewSummary", {
                  size: formatSizeGb(newGb),
                  price: t("common.gbMonthPrice", { price: diskPriceGbMonth }),
                  daily: t("common.dailyApprox", { amount: diskDaily }),
                })
              : formatSizeGb(newGb)}
          </Typography.Text>
          <Collapse
            ghost
            size="small"
            items={[
              {
                key: "advanced",
                label: t("create.diskAdvanced"),
                children: (
                  <Space size={space.md} align="center">
                    <Typography.Text type="secondary">{t("storage.nameLabel")}</Typography.Text>
                    <Input
                      style={{ width: controlWidth.md }}
                      maxLength={64}
                      aria-label={t("storage.nameLabel")}
                      value={newName}
                      onChange={(e) => onNewNameChange(e.target.value)}
                    />
                  </Space>
                ),
              },
            ]}
          />
        </>
      )}
      {mode === "existing" &&
        (disksQ.isError ? (
          <DataErrorAlert onRetry={() => void disksQ.refetch()} />
        ) : (
          <Select
            style={{ width: "100%", maxWidth: controlWidth.lg }}
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
      <Space orientation="vertical" size={0}>
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("create.diskIndependentNote")}
        </Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("copy.instanceDiskLocalNotice")}
        </Typography.Text>
      </Space>
    </Space>
  );
  if (variant === "section") {
    return (
      <div id={id}>
        <Typography.Text strong style={{ display: "block", marginBottom: space.sm }}>
          {t("create.diskCard")}
        </Typography.Text>
        {body}
      </div>
    );
  }
  return (
    <Card id={id} title={t("create.diskCard")} className="anchor-card">
      {body}
    </Card>
  );
}
