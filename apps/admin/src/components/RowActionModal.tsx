/** 「对某一行做一次带表单的动作」弹窗(登记打款 / 开票共用):顶部说明条 + 表单,
 *  校验 → 提交 → 成功提示 + onDone + 关闭,失败走 message_key 目录映射。 */

import { Alert, App, Form, type FormInstance, Modal, Space } from "antd";
import type { ReactNode } from "react";
import { useState } from "react";

import { useApiErrorText } from "@superdl/ui";

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
  // form 实例由调用方持有(常驻),卸载/关闭时清空 store:destroyOnHidden 只销毁 DOM,
  // 不重置外部 store,不在这里 reset 会让上一目标的已填值残留到下一目标
  const close = () => {
    form.resetFields();
    onClose();
  };
  return (
    <Modal
      open
      title={title}
      okText={okText}
      okButtonProps={{ loading }}
      onCancel={close}
      // 表单随弹窗销毁:同一弹窗组件服务多行目标时,上一目标的已填值不得残留到下一目标
      destroyOnHidden
      onOk={async () => {
        let values: Values;
        try {
          values = await form.validateFields();
        } catch {
          // 校验失败:antd 已在字段下给出红字反馈,静默停留
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
