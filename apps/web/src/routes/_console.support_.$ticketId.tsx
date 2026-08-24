/**
 * 工单详情:对话流(用户/客服气泡区分)+ 关联实例链接 + [关闭工单]。
 * resolved/closed 不可再回复(提示新建);关闭入口仅在 resolved 出现。
 */

import { ArrowLeftOutlined } from "@ant-design/icons";
import { formatDateTime, metaOf, ticketCategoryMap, ticketStatusMap } from "@superdl/ui";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Badge,
  Button,
  Card,
  Input,
  Popconfirm,
  Skeleton,
  Space,
  Tag,
  theme,
  Typography,
} from "antd";
import { useState } from "react";

import type { TicketMessageOut } from "@superdl/api-client";
import { useAppendTicketMessage, useCloseTicket } from "../api/mutations";
import { useTicketDetail } from "../api/queries";
import { DataErrorAlert } from "../components/QueryState";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/support_/$ticketId")({
  beforeLoad: requireAuth,
  component: TicketDetailPage,
});

// resolved/closed 终态不可再回复(服务端同口径 409,此处只是不渲染输入框)
const REPLIABLE = new Set(["open", "pending_staff", "pending_user"]);

function Bubble({ msg }: { msg: TicketMessageOut }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const mine = msg.sender_kind === "user";
  return (
    <div style={{ display: "flex", justifyContent: mine ? "flex-end" : "flex-start" }}>
      <div
        style={{
          maxWidth: "75%",
          padding: "8px 12px",
          borderRadius: 8,
          background: mine ? token.colorPrimaryBg : token.colorFillTertiary,
        }}
      >
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {mine ? t("support.msgMe") : t("support.msgStaff")} · {formatDateTime(msg.created_at)}
        </Typography.Text>
        <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{msg.body}</div>
      </div>
    </div>
  );
}

function TicketDetailPage() {
  const { t } = useTranslation(["web", "shared"]);
  const { ticketId } = Route.useParams();
  const id = Number(ticketId);
  const detail = useTicketDetail(id, { refetchInterval: 15_000 });
  const [draft, setDraft] = useState("");
  const reply = useAppendTicketMessage({ onSuccess: () => setDraft("") });
  const close = useCloseTicket();

  if (detail.isError) {
    return <DataErrorAlert onRetry={() => void detail.refetch()} />;
  }
  const ticket = detail.data;
  if (!ticket) {
    return <Skeleton active paragraph={{ rows: 6 }} />;
  }
  const sm = metaOf(ticketStatusMap, ticket.status);
  const cm = metaOf(ticketCategoryMap, ticket.category);
  const repliable = REPLIABLE.has(ticket.status);

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Link to="/support">
        <Button type="text" icon={<ArrowLeftOutlined />}>
          {t("support.backToList")}
        </Button>
      </Link>
      <Card
        title={
          <Space size={8} wrap>
            <Typography.Text code>{ticket.ticket_no}</Typography.Text>
            {cm && <Tag>{t(cm.labelKey)}</Tag>}
            <Badge status={sm?.badge ?? "default"} text={sm ? t(sm.labelKey) : ticket.status} />
          </Space>
        }
        extra={
          ticket.status === "resolved" && (
            <Popconfirm
              title={t("support.closeConfirm")}
              onConfirm={() => close.mutate(id)}
            >
              <Button size="small" loading={close.isPending}>
                {t("support.closeTicket")}
              </Button>
            </Popconfirm>
          )
        }
      >
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          {ticket.subject}
        </Typography.Title>
        <Space size={16} wrap>
          <Typography.Text type="secondary">
            {t("support.createdAt")}: {formatDateTime(ticket.created_at)}
          </Typography.Text>
          {ticket.instance_uuid && (
            <Link to="/instances/$uuid" params={{ uuid: ticket.instance_uuid }}>
              {t("support.linkedInstance")}
            </Link>
          )}
        </Space>
      </Card>
      <Card title={t("support.conversation")}>
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          {(ticket.messages ?? []).map((m) => (
            <Bubble key={m.id} msg={m} />
          ))}
          {repliable ? (
            <Space.Compact style={{ width: "100%" }}>
              <Input.TextArea
                rows={3}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                maxLength={4000}
                placeholder={t("support.replyPlaceholder")}
              />
              <Button
                type="primary"
                style={{ height: "auto" }}
                loading={reply.isPending}
                disabled={draft.trim().length < 2}
                onClick={() => reply.mutate({ ticketId: id, body: { body: draft.trim() } })}
              >
                {t("support.replySend")}
              </Button>
            </Space.Compact>
          ) : (
            <Alert type="info" showIcon title={t("support.terminalHint")} />
          )}
        </Space>
      </Card>
    </Space>
  );
}
