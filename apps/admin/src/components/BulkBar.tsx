/** 批量操作条:表格勾选后出现「已选 N 项 · 动作 · 清除」;后端无批量端点时逐条并发调用,统一给「成功 N / 失败 M」反馈(runBulk)。 */

import { fontSize, space } from "@superdl/ui";
import { Button, Space, Typography, theme } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export function BulkBar({ count, onClear, children }: { count: number; onClear: () => void; children: ReactNode }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  if (count === 0) return null;
  return (
    <div
      role="region"
      aria-label={t("bulk.selected", { count })}
      style={{
        display: "flex",
        alignItems: "center",
        gap: space.md,
        flexWrap: "wrap",
        padding: `${space.sm}px ${space.md}px`,
        marginBottom: space.md,
        borderRadius: token.borderRadius,
        background: token.colorFillTertiary,
        border: `1px solid ${token.colorBorderSecondary}`,
      }}
    >
      <Typography.Text strong style={{ fontSize: fontSize.body }}>
        {t("bulk.selected", { count })}
      </Typography.Text>
      <Space size={space.sm} wrap>
        {children}
      </Space>
      <Button type="link" size="small" onClick={onClear} style={{ marginInlineStart: "auto" }}>
        {t("bulk.clear")}
      </Button>
    </div>
  );
}

/** 逐条并发执行,返回成败计数;调用方据此给一条汇总 message。 */
export async function runBulk<T>(items: T[], fn: (item: T) => Promise<unknown>): Promise<{ ok: number; failed: number }> {
  const results = await Promise.allSettled(items.map((it) => fn(it)));
  const ok = results.filter((r) => r.status === "fulfilled").length;
  return { ok, failed: results.length - ok };
}
