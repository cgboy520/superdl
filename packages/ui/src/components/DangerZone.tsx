/** 危险区卡(两端共用):红边 Card + 说明 + 一个或多个危险动作;动作前置条件经 GatedButton(可见、可聚焦、原因可读)。 */

import { Card, Space, Typography, theme } from "antd";
import type { ReactNode } from "react";

import { space } from "../tokens";
import { GatedButton } from "./GatedButton";

export interface DangerAction {
  key: string;
  label: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  /** 禁用时的前置条件说明 */
  disabledReason?: ReactNode;
  loading?: boolean;
}

export function DangerZone({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions: DangerAction[];
}) {
  const { token } = theme.useToken();
  return (
    <Card title={title} style={{ borderColor: token.colorErrorBorder }}>
      <Space orientation="vertical" size={space.sm}>
        {description && <Typography.Text type="secondary">{description}</Typography.Text>}
        <Space wrap>
          {actions.map((a) => (
            <GatedButton
              key={a.key}
              danger
              reason={a.disabled ? a.disabledReason : undefined}
              loading={a.loading}
              onClick={a.onClick}
            >
              {a.label}
            </GatedButton>
          ))}
        </Space>
      </Space>
    </Card>
  );
}
