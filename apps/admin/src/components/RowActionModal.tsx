/** 行级带表单动作弹窗(登记打款 / 开票共用):说明条 + 表单,校验 → 提交 → 成功提示 + onDone + 关闭。 */

import { Alert, App, Form, type FormInstance, Modal, Space } from "antd";
import type { ReactNode } from "react";
import { useState } from "react";

import { space, useApiErrorText } from "@superdl/ui";

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
  // form 实例由调用方持有,关闭时清空 store
  const close = () => {
    form.resetFields();
    onClose();
  };
  const handleOk = async () => {
    let values: Values;
    try {
      values = await form.validateFields();
    } catch {
      // 校验失败:antd 已给红字
      return;
    }
    setLoading(true);
    try {
      await submit(values);
      message.success(successText);
      onDone();
      close();
    } catch (e) {
      message.error(errText(e, failText));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      open
      title={title}
      okText={okText}
      okButtonProps={{ loading }}
      onCancel={close}
      destroyOnHidden
      onOk={() => void handleOk()}
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Alert type="info" showIcon title={note} />
        <Form form={form} layout="vertical">
          {children}
        </Form>
      </Space>
    </Modal>
  );
}
