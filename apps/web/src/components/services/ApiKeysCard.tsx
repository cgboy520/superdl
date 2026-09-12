/** 访问密钥卡:名称 / Key 前缀 / 最近使用 / 创建时间 / 吊销 + 新建(一次性展示)。公开访问的服务仍渲染,附「网关不校验 Key」说明。 */

import type { ApiKeyOut } from "@superdl/api-client";
import { formatDateTime } from "@superdl/ui";
import { TableErrorEmpty, useConfirm } from "@superdl/ui/components";
import { Alert, App, Button, Card, Space, Table, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useRevokeServiceApiKey } from "../../api/mutations";
import { useServiceApiKeys } from "../../api/queries";
import { ApiKeyModal } from "./ApiKeyModal";

export function ApiKeysCard({
  slug,
  requireApiKey,
  released,
}: {
  slug: string;
  requireApiKey: boolean;
  /** 已删除的服务不能再建钥(后端 409),按钮灰置 */
  released: boolean;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const [newKeyOpen, setNewKeyOpen] = useState(false);
  const keysQ = useServiceApiKeys(slug);
  const revoke = useRevokeServiceApiKey(slug, {
    onSuccess: () => {
      message.success(t("services.keys.revokedMsg"));
    },
  });
  const keys = keysQ.data ?? [];
  return (
    <Card
      size="small"
      title={t("services.keys.card")}
      extra={
        <Button type="primary" size="small" disabled={released} onClick={() => setNewKeyOpen(true)}>
          {t("services.keys.new")}
        </Button>
      }
    >
      <Space orientation="vertical" size={8} style={{ width: "100%" }}>
        {!requireApiKey && <Alert type="warning" showIcon title={t("services.keys.publicNote")} />}
        <Table<ApiKeyOut>
          rowKey="id"
          size="small"
          pagination={false}
          loading={keysQ.isLoading}
          dataSource={keys}
          scroll={{ x: 640 }}
          locale={{
            emptyText: keysQ.isError ? (
              <TableErrorEmpty isError onRetry={() => void keysQ.refetch()} />
            ) : (
              t("services.keys.empty")
            ),
          }}
          columns={[
            { title: t("services.keys.colName"), render: (_, r) => r.name },
            {
              title: t("services.keys.colKey"),
              render: (_, r) => <Typography.Text code>{r.key_prefix}…</Typography.Text>,
            },
            {
              title: t("services.keys.colLastUsed"),
              render: (_, r) => (r.last_used_at ? formatDateTime(r.last_used_at) : t("services.keys.neverUsed")),
            },
            { title: t("services.keys.colCreated"), render: (_, r) => formatDateTime(r.created_at) },
            {
              title: t("services.keys.colActions"),
              render: (_, r) =>
                r.revoked_at ? (
                  <Typography.Text type="secondary">{t("services.keys.revoked")}</Typography.Text>
                ) : (
                  <Button
                    size="small"
                    danger
                    onClick={() =>
                      confirm({
                        title: t("services.keys.revokeConfirmTitle"),
                        consequences: [t("services.keys.revokeConfirmBody")],
                        danger: true,
                        onOk: async () => {
                          await revoke.mutateAsync(r.id);
                        },
                      })
                    }
                  >
                    {t("services.keys.revoke")}
                  </Button>
                ),
            },
          ]}
        />
        <Typography.Text type="secondary">{t("copy.apiKeyOnce")}</Typography.Text>
      </Space>
      <ApiKeyModal slug={slug} open={newKeyOpen} onClose={() => setNewKeyOpen(false)} />
    </Card>
  );
}
