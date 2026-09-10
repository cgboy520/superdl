/**
 * 新建服务访问 Key 的弹窗。成功态是一次性展示:明文只在创建响应里出现一次,库里只有 HMAC 摘要。
 * 与管理端 TOTP 恢复码同一套:大号等宽全值 + 复制 + 红字警告 + 必须勾「我已保存」才能关,
 * X 与遮罩关闭一并封掉(手滑关掉就只能吊销后重建)。
 */

import type { ApiKeyCreateOut } from "@superdl/api-client";
import { fontSize } from "@superdl/ui";
import { Button, Checkbox, Card, Input, Modal, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useCreateServiceApiKey } from "../../api/mutations";
import { CopyButton } from "../common";

export function ApiKeyModal({
  slug,
  open,
  onClose,
}: {
  slug: string;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [created, setCreated] = useState<ApiKeyCreateOut | null>(null);
  const [saved, setSaved] = useState(false);
  const create = useCreateServiceApiKey(slug, { onSuccess: (d) => setCreated(d) });
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
        title={t("services.keys.created")}
        // 明文只此一次:X 与遮罩关闭全部封掉,只留勾选后的那个按钮
        closable={false}
        mask={{ closable: false }}
        keyboard={false}
        onCancel={close}
        footer={
          <Button type="primary" disabled={!saved} onClick={close}>
            {t("services.keys.close")}
          </Button>
        }
      >
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Typography.Text type="danger" strong>
            {t("services.keys.onceWarn")}
          </Typography.Text>
          <Card size="small">
            <CopyButton text={created.key} label={t("services.keys.copy")} />
            <pre
              style={{
                margin: "8px 0 0",
                fontSize: fontSize.sectionTitle,
                lineHeight: 1.8,
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}
            >
              {created.key}
            </pre>
          </Card>
          <Checkbox checked={saved} onChange={(e) => setSaved(e.target.checked)}>
            {t("services.keys.savedConfirm")}
          </Checkbox>
        </Space>
      </Modal>
    );
  }

  return (
    <Modal
      open={open}
      title={t("services.keys.new")}
      onCancel={close}
      okText={t("services.keys.createOk")}
      cancelText={t("services.keys.cancel")}
      confirmLoading={create.isPending}
      okButtonProps={{ disabled: name.trim() === "" }}
      onOk={() => create.mutate(name.trim())}
    >
      <Space orientation="vertical" size={8} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("services.keys.nameLabel")}</Typography.Text>
        <Input
          maxLength={64}
          autoFocus
          aria-label={t("services.keys.nameLabel")}
          placeholder={t("services.keys.namePlaceholder")}
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
