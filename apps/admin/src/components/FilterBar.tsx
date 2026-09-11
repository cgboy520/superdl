/** 筛选条:左侧筛选控件、右侧「共 N 条 · 清除筛选」;筛选态由页面持有(入 URL),这里只负责排版与清除。 */

import { fontSize, space } from "@superdl/ui";
import { Button, Space, Typography } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export function FilterBar({
  children,
  count,
  hasFilter,
  onClear,
  extra,
}: {
  children: ReactNode;
  /** 当前结果条数(未知不传) */
  count?: number;
  hasFilter: boolean;
  onClear: () => void;
  /** 右侧附加动作(刷新 / 导出) */
  extra?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: space.md,
        flexWrap: "wrap",
        marginBottom: space.md,
      }}
    >
      <Space size={space.sm} wrap>
        {children}
        {hasFilter && (
          <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={onClear}>
            {t("filter.clear")}
          </Button>
        )}
      </Space>
      <Space size={space.sm} wrap>
        {count != null && (
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("filter.count", { count })}
          </Typography.Text>
        )}
        {extra}
      </Space>
    </div>
  );
}
