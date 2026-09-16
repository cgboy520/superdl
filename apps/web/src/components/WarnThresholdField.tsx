/** Low-balance warning threshold (N hours ahead): number input + save button + note; shared by the billing balance card and account settings. */
import { CheckOutlined } from "@ant-design/icons";
import { fontSize } from "@superdl/ui";
import { App, Button, InputNumber, Space, Typography } from "antd";
import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useSetWarnThreshold } from "../api/mutations";
import { useMe } from "../api/queries";

export function WarnThresholdField({ size, style }: { size?: "small" | "middle"; style?: CSSProperties }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { data: me } = useMe();
  const [warnHours, setWarnHours] = useState<number>();
  const [saved, setSaved] = useState(false);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (savedTimer.current) clearTimeout(savedTimer.current);
    },
    [],
  );
  const setThreshold = useSetWarnThreshold({
    onSuccess: () => {
      message.success(t("billing.thresholdSaved"));
      if (savedTimer.current) clearTimeout(savedTimer.current);
      setSaved(true);
      savedTimer.current = setTimeout(() => setSaved(false), 1500);
    },
  });
  const save = () => {
    const v = warnHours ?? me?.low_balance_warn_hours;
    if (v != null) setThreshold.mutate(v);
  };
  return (
    <Space style={style}>
      <Typography.Text type="secondary">{t("settings.warnThresholdLabel")}</Typography.Text>
      <InputNumber
        size={size}
        min={1}
        max={168}
        aria-label={t("settings.warnThresholdLabel")}
        value={warnHours ?? me?.low_balance_warn_hours}
        onChange={(v) => setWarnHours(v ?? undefined)}
        onPressEnter={save}
      />
      <Button size={size} loading={setThreshold.isPending} icon={saved ? <CheckOutlined /> : undefined} onClick={save}>
        {saved ? t("billing.saved") : t("billing.save")}
      </Button>
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        {t("settings.warnThresholdHint")}
      </Typography.Text>
    </Space>
  );
}
