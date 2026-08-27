/**
 * 新建服务访问 Key 的弹窗。成功态是一次性展示:明文只在创建响应里出现一次,库里只有
 * HMAC 摘要 —— 与管理端 TOTP 恢复码同一套(大号等宽全值 + 复制 + 红字警告 +
 * 必须勾「我已保存」才能关)。手滑关掉就只能吊销后重建,所以 X 与遮罩关闭一并封掉。
 */

import type { ApiKeyCreateOut } from "@superdl/api-client";
import { Button, Checkbox, Card, Input, Modal, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useCreateApiKey } from "../api/mutations";
import { CopyButton } from "./common";

export function ApiKeyModal({
  uuid,
  open,
  onClose,
}: {
  uuid: string;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [created, setCreated] = useState<ApiKeyCreateOut | null>(null);
  const [saved, setSaved] = useState(false);
  const create = useCreateApiKey(uuid, { onSuccess: (d) => setCreated(d) });
  const close = () => {
    setName("");
    setCreated(null);
    setSaved(false);
    onClose();
  };

  if (created) {
    return (
      <Modal
        open={open}
        title={t("instances.apiKeyCreated")}
        // 明文只此一次:X 与遮罩关闭全部封掉,只留勾选后的那个按钮
        closable={false}
        maskClosable={false}
        keyboard={false}
        onCancel={close}
        footer={
          <Button type="primary" disabled={!saved} onClick={close}>
            {t("instances.apiKeyClose")}
          </Button>
        }
      >
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Typography.Text type="danger" strong>
            {t("instances.apiKeyOnceWarn")}
          </Typography.Text>
          <Card size="small">
            <CopyButton text={created.key} label={t("instances.apiKeyCopy")} />
            <pre
              style={{
                margin: "8px 0 0",
                fontSize: 15,
                lineHeight: 1.8,
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}
            >
              {created.key}
            </pre>
          </Card>
          <Checkbox checked={saved} onChange={(e) => setSaved(e.target.checked)}>
            {t("instances.apiKeySavedConfirm")}
          </Checkbox>
        </Space>
      </Modal>
    );
  }

  return (
    <Modal
      open={open}
      title={t("instances.apiKeyNew")}
      onCancel={close}
      okText={t("instances.apiKeyCreateOk")}
      cancelText={t("instances.apiKeyCancel")}
      confirmLoading={create.isPending}
      okButtonProps={{ disabled: name.trim() === "" }}
      onOk={() => create.mutate(name.trim())}
    >
      <Space orientation="vertical" size={8} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("instances.apiKeyNameLabel")}</Typography.Text>
        <Input
          maxLength={64}
          autoFocus
          aria-label={t("instances.apiKeyNameLabel")}
          placeholder={t("instances.apiKeyNamePlaceholder")}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onPressEnter={() => {
            if (name.trim() !== "") create.mutate(name.trim());
          }}
        />
        <Typography.Text type="secondary">{t("copy.apiKeyOnce")}</Typography.Text>
      </Space>
    </Modal>
  );
}
