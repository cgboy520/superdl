import { Button, Form, Input, Space } from "antd";

import type { useSmsCode } from "../lib/useSmsCode";

export function SmsCodeField({
  sms,
  label,
  placeholder,
  requiredMessage,
  getCodeLabel,
  onSend,
  name = "sms_code",
}: {
  sms: ReturnType<typeof useSmsCode>;
  /** 可见标签;aria-label 始终使用 placeholder。 */
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
        <Button disabled={sms.countdown > 0} loading={sms.sending} onClick={onSend}>
          {sms.countdown > 0 ? `${sms.countdown}s` : getCodeLabel}
        </Button>
      </Space.Compact>
    </Form.Item>
  );
}
