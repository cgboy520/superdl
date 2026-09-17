/** Danger zone card (shared by both consoles): red-bordered Card + description + one or more dangerous actions; preconditions go through GatedButton (visible, focusable, readable reason). */

import { Card, Space, Typography, theme } from "antd";
import type { ReactNode } from "react";

import { space } from "../tokens";
import { GatedButton } from "./GatedButton";

export interface DangerAction {
  key: string;
  label: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  /** Precondition note shown while disabled */
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
