/** 联系方式卡(site-config support 组驱动):/help 与 /support 共用。加载失败不伪装成「未配置」;未就绪不闪「未配置」。 */

import { fontSize } from "@superdl/ui";
import { DataErrorAlert } from "@superdl/ui/components";
import { Alert, Card, Space, Typography } from "antd";
import type { CSSProperties } from "react";

import { useSiteConfig } from "../api/queries";
import { CopyButton } from "./common";

export function ContactCard({
  title,
  emailLabel,
  wechatLabel,
  hint,
  missingText,
  style,
}: {
  title: string;
  emailLabel: string;
  wechatLabel: string;
  hint: string;
  missingText: string;
  style?: CSSProperties;
}) {
  const siteQ = useSiteConfig();
  const { data: site } = siteQ;
  const email = site?.support_email;
  const wechat = site?.support_wechat;
  return (
    <Card title={title} style={style}>
      {siteQ.isError ? (
        // 加载失败不伪装成「联系方式未配置」
        <DataErrorAlert onRetry={() => void siteQ.refetch()} />
      ) : email || wechat ? (
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          {wechat && (
            <Space>
              <Typography.Text strong>{wechatLabel}</Typography.Text>
              <Typography.Text code>{wechat}</Typography.Text>
              <CopyButton text={wechat} />
            </Space>
          )}
          {email && (
            <Space>
              <Typography.Text strong>{emailLabel}</Typography.Text>
              <a href={`mailto:${email}`}>{email}</a>
              <CopyButton text={email} />
            </Space>
          )}
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {hint}
          </Typography.Text>
        </Space>
      ) : (
        !siteQ.isLoading && <Alert type="info" showIcon title={missingText} />
      )}
    </Card>
  );
}
