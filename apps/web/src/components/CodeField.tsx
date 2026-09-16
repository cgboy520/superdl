import { Button, Form, Input, Space } from "antd";

import type { VerificationCodeSender } from "../lib/useVerificationCode";

/** One-time code input with the "send code" button and its countdown. */
export function CodeField({
  code,
  label,
  placeholder,
  requiredMessage,
  getCodeLabel,
  onSend,
  name = "code",
}: {
  code: VerificationCodeSender;
  /** Visible label; the aria-label is always the placeholder. */
  label?: string;
  placeholder: string;
  requiredMessage: string;
  getCodeLabel: string;
  onSend: () => void;
  name?: string;
}) {
  return (
    <Form.Item label={label} htmlFor={name} required={label !== undefined}>
      <Space.Compact style={{ width: "100%", alignItems: "flex-start" }}>
        <Form.Item
          name={name}
          rules={[{ required: true, message: requiredMessage }]}
          style={{ flex: 1, marginBottom: 0 }}
        >
          <Input placeholder={placeholder} maxLength={6} autoComplete="one-time-code" aria-label={placeholder} />
        </Form.Item>
        <Button disabled={code.countdown > 0} loading={code.sending} onClick={onSend}>
          {code.countdown > 0 ? `${code.countdown}s` : getCodeLabel}
        </Button>
      </Space.Compact>
    </Form.Item>
  );
}
