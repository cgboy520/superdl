/** 工单详情:对话流(用户/客服气泡)+ 关联实例链接 + [关闭工单]。resolved/closed 不可再回复;关闭入口仅在 resolved 出现。 */

import { ArrowLeftOutlined } from "@ant-design/icons";
import {
  fontSize,
  formatDateTime,
  isTicketRepliable,
  metaOf,
  ticketCategoryMap,
  ticketStatusMap,
} from "@superdl/ui";
import { DataErrorAlert, isMacPlatform, TicketBubble, useConfirm } from "@superdl/ui/components";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Badge,
  Button,
  Card,
  Input,
  Skeleton,
  Space,
  Tag,
  Typography,
} from "antd";
import { useEffect, useRef, useState } from "react";

import { useAppendTicketMessage, useCloseTicket } from "../api/mutations";
import { useTicketDetail } from "../api/queries";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/support_/$ticketId")({
  beforeLoad: requireAuth,
  component: TicketDetailPage,
});

/** 发送快捷键的平台提示(与 CommandPalette 的 COMMAND_KBD_HINT 同一判定) */
const SEND_KBD_HINT = isMacPlatform() ? "⌘⏎" : "Ctrl+Enter";

function TicketDetailPage() {
  const { t } = useTranslation(["web", "shared"]);
  const { ticketId } = Route.useParams();
  const id = Number(ticketId);
  // 进行中 15s 轮询,resolved/closed 终态即停
  const detail = useTicketDetail(id, {
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status === "open" || status === "pending_staff" || status === "pending_user"
        ? 15_000
        : false;
    },
  });
  const [draft, setDraft] = useState("");
  const reply = useAppendTicketMessage({ onSuccess: () => setDraft("") });
  const close = useCloseTicket();
  const confirm = useConfirm();
  // 对话容器贴底跟随(同 LogsPanel):距底 ≤40px 视为贴底,新消息仅在贴底时自动滚底
  const scrollRef = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);

  const messageCount = detail.data?.messages?.length ?? 0;
  useEffect(() => {
    if (!pinned) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messageCount, pinned]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight <= 40);
  };

  if (detail.isError) {
    return <DataErrorAlert onRetry={() => void detail.refetch()} />;
  }
  const ticket = detail.data;
  if (!ticket) {
    return <Skeleton active paragraph={{ rows: 6 }} />;
  }
  const sm = metaOf(ticketStatusMap, ticket.status);
  const cm = metaOf(ticketCategoryMap, ticket.category);
  const repliable = isTicketRepliable(ticket.status);

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
            // L1 确认(可逆性低但影响面 = 1)
            <Button
              size="small"
              loading={close.isPending}
              onClick={() =>
                confirm({
                  title: t("support.closeConfirm"),
                  consequences: [t("support.closeBody")],
                  okText: t("support.closeTicket"),
                  onOk: async () => {
                    await close.mutateAsync(id);
                  },
                })
              }
            >
              {t("support.closeTicket")}
            </Button>
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
          <div
            ref={scrollRef}
            onScroll={onScroll}
            style={{ maxHeight: 480, overflow: "auto" }}
          >
            <Space orientation="vertical" size={12} style={{ width: "100%" }}>
              {(ticket.messages ?? []).map((m) => (
                <TicketBubble
                  key={m.id}
                  side={m.sender_kind === "user" ? "right" : "left"}
                  label={m.sender_kind === "user" ? t("support.msgMe") : t("support.msgStaff")}
                  time={formatDateTime(m.created_at)}
                  body={m.body}
                />
              ))}
            </Space>
          </div>
          {repliable ? (
            <>
              <Space.Compact style={{ width: "100%" }}>
                <Input.TextArea
                  rows={3}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  aria-label={t("support.replyPlaceholder")}
                  onKeyDown={(e) => {
                    // Ctrl/Cmd+Enter 发送(与发送按钮同一提交条件)
                    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                      e.preventDefault();
                      if (!reply.isPending && draft.trim().length >= 2) {
                        reply.mutate({ ticketId: id, body: { body: draft.trim() } });
                      }
                    }
                  }}
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
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {t("support.replySendHint", { kbd: SEND_KBD_HINT })}
              </Typography.Text>
            </>
          ) : (
            <Alert type="info" showIcon title={t("support.terminalHint")} />
          )}
        </Space>
      </Card>
    </Space>
  );
}
