/** Bulk action bar: appears after table selection as "N selected · actions · clear"; without a backend bulk endpoint the calls run one by one concurrently with a single "N succeeded / M failed" feedback (runBulk). */

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

/** Start one operation per item concurrently and return success/failure counts; the caller turns them into one summary message. */
export async function runBulk<T>(
  items: T[],
  fn: (item: T) => Promise<unknown>,
): Promise<{ ok: number; failed: number }> {
  const results = await Promise.allSettled(items.map((it) => fn(it)));
  const ok = results.filter((r) => r.status === "fulfilled").length;
  return { ok, failed: results.length - ok };
}
