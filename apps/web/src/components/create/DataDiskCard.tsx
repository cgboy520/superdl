/** 数据盘卡:「不需要 / 新建 / 挂载已有盘」三个 tile(副行写口径与可挂载盘数)。新建为行内直建(DiskSizeField 在前,名称折在「高级」里;单价来自 /policies 折日展示),建盘动作由页面在提交时执行;盘清单加载失败不伪装成「没有可挂载的盘」。
 *  实例盘为本地盘、不做冗余的说明放在本卡脚注(与「要不要数据盘」这个决定直接相关),不做页顶常驻条。 */

import { controlWidth, fontSize, formatSizeGb, space } from "@superdl/ui";
import { DataErrorAlert, DiskSizeField, OptionTileGroup } from "@superdl/ui/components";
import { Card, Collapse, Input, Select, Space, Typography } from "antd";
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
  // 可挂载 = 正常状态且未挂在别的实例上
  const mountable = (disksQ.data ?? []).filter((d) => d.status === "active" && d.mounted_instance_id == null);

  const body = (
    <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
      <OptionTileGroup
        label={t("create.diskCard")}
        hideLabel
        columns={3}
        size="sm"
        value={mode}
        onChange={onModeChange}
        options={[
          { value: "none", title: t("create.diskNone"), description: t("create.diskNoneDesc") },
          { value: "new", title: t("create.diskNew"), description: t("create.diskNewDesc") },
          {
            value: "existing",
            title: t("create.diskExisting"),
            description: t("create.diskExistingDesc", { count: mountable.length }),
          },
        ]}
      />
      {mode === "new" && (
        <>
          {/* 容量:滑块与数字框联动;min/max 与单价取 /policies,折日估算由 DiskSizeField 出 */}
          <DiskSizeField
            value={newGb}
            onChange={onNewGbChange}
            min={policies?.disk_min_gb}
            max={policies?.disk_max_gb}
            priceGbMonth={policies?.disk_price_gb_month}
            ariaLabel={t("create.diskSizeAria")}
          />
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("create.diskAutoCreate")}
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
            options={mountable.map((d) => ({
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
