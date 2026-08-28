/** 「对某一行做一次带表单的动作」弹窗(登记打款 / 开票共用):顶部说明条 + 表单,
 *  校验 → 提交 → 成功提示 + onDone + 关闭,失败走 message_key 目录映射。 */

import { Alert, App, Form, type FormInstance, Modal, Space } from "antd";
import type { ReactNode } from "react";
import { useState } from "react";

import { useApiErrorText } from "../lib/apiError";

export function RowActionModal<Values>({
  title,
  okText,
  note,
  form,
  children,
  submit,
  successText,
  failText,
  onClose,
  onDone,
}: {
  title: string;
  okText: string;
  note: string;
  form: FormInstance<Values>;
  children: ReactNode;
  submit: (values: Values) => Promise<unknown>;
  successText: string;
  failText: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  return (
    <Modal
      open
      title={title}
      okText={okText}
      okButtonProps={{ loading }}
      onCancel={onClose}
      onOk={async () => {
        const values = await form.validateFields();
        setLoading(true);
        try {
          await submit(values);
          message.success(successText);
          onDone();
          onClose();
        } catch (e) {
          message.error(errText(e, failText));
        } finally {
          setLoading(false);
        }
      }}
    >
      <Space orientation="vertical" size={12} style={{ width: "100%" }}>
        <Alert type="info" showIcon title={note} />
        <Form form={form} layout="vertical">
          {children}
        </Form>
      </Space>
    </Modal>
  );
}
