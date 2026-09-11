/** 危险区卡(两端共用):红边 Card + 说明 + 一个或多个危险动作;动作禁用原因走 Tooltip(全站禁用项同一处理)。 */

import { Button, Card, Space, Tooltip, Typography, theme } from "antd";
import type { ReactNode } from "react";

import { space } from "../tokens";

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
            <Tooltip key={a.key} title={a.disabled ? a.disabledReason : undefined}>
              <Button danger disabled={a.disabled} loading={a.loading} onClick={a.onClick}>
                {a.label}
              </Button>
            </Tooltip>
          ))}
        </Space>
      </Space>
    </Card>
  );
}
