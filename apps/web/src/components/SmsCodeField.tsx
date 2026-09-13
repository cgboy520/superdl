/** 短信验证码字段(登录页 / 改密弹窗共用):验证码输入 + 「获取验证码 / Ns」按钮;校验挂内层 Form.Item(唯一控件是 Input),不挂 Space.Compact 的 div(axe aria-allowed-attr)。
 *  可见标签(登录页传 label)挂外层 Form.Item,htmlFor 指到内层输入框的 id。发码由调用方给 onSend(登录页先校验手机号)。 */

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
  /** 可见标签;不传则只有 aria-label(弹窗内字段) */
  label?: string;
  placeholder: string;
  requiredMessage: string;
  getCodeLabel: string;
  onSend: () => void;
  name?: string;
}) {
  return (
    // 内层规则是必填,带可见标签时外层补星号,免得同一表单里手机号有星、验证码没有
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
