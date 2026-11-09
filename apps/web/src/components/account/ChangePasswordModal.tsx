/** Change the password with the current one (the only way for accounts a tenant created, which have no code channel). The API ends every other session and returns a fresh token pair, which replaces this tab's session. */

import { space } from "@superdl/ui";
import { App, Form, Input, Modal, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useChangePassword } from "../../api/mutations";
import { authStore } from "../../stores/auth";

interface Values {
  current_password: string;
  new_password: string;
  confirm: string;
}

export function ChangePasswordModal({
  open,
  onClose,
  forced = false,
}: {
  open: boolean;
  onClose?: () => void;
  /** First sign-in of a tenant-created account (or after an admin reset): no cancel until it is done. */
  forced?: boolean;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<Values>();
  const change = useChangePassword({
    onSuccess: async (data) => {
      await authStore.getState().login(data.access_token);
      message.success(t("settings.passwordChanged"));
      form.resetFields();
      onClose?.();
    },
  });
  return (
    <Modal
      title={forced ? t("settings.forcedPasswordTitle") : t("settings.changePassword")}
      open={open}
      okText={t("settings.savePassword")}
      confirmLoading={change.isPending}
      closable={!forced}
      mask={{ closable: !forced }}
      keyboard={!forced}
      cancelButtonProps={forced ? { style: { display: "none" } } : undefined}
      onCancel={() => !forced && onClose?.()}
      onOk={() => {
        void form
          .validateFields()
          .then((v) => change.mutate({ current_password: v.current_password, new_password: v.new_password }));
      }}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" style={{ marginTop: space.sm }}>
        {forced && <Typography.Paragraph type="secondary">{t("settings.forcedPasswordDesc")}</Typography.Paragraph>}
        <Form.Item
          name="current_password"
          label={forced ? t("settings.passwordGiven") : t("settings.passwordCurrent")}
          rules={[{ required: true, message: t("settings.passwordCurrentRequired") }]}
        >
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item
          name="new_password"
          label={t("settings.passwordNew")}
          rules={[{ required: true, min: 12, message: t("settings.passwordMin") }]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="confirm"
          label={t("settings.passwordConfirm")}
          dependencies={["new_password"]}
          rules={[
            { required: true, message: t("settings.passwordConfirmRequired") },
            ({ getFieldValue }) => ({
              validator: (_, value: string) =>
                !value || getFieldValue("new_password") === value
                  ? Promise.resolve()
                  : Promise.reject(new Error(t("settings.passwordMismatch"))),
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

/** Mounted once in the console layout: holds the console until a required password change is done. */
export function ForcedPasswordChange({ required }: { required: boolean }) {
  return <ChangePasswordModal open={required} forced />;
}
