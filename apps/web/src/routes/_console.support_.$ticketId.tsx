/** Ticket detail (PageContainer narrow, title = ticket subject, back to the list): conversation (user / staff bubbles) + linked instance + [Close ticket]. resolved/closed accept no replies; the close entry is permanent, greyed with a reason unless resolved. */

import { POLL, space } from "@superdl/ui";
import { fontSize, formatDateTime, isTicketRepliable, metaOf, ticketCategoryMap, ticketStatusMap } from "@superdl/ui";
import {
  DataErrorAlert,
  GatedButton,
  isMacPlatform,
  PageContainer,
  TicketBubble,
  useConfirm,
} from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Alert, Badge, Card, Input, Skeleton, Space, Tag, Typography } from "antd";
import { useEffect, useRef, useState } from "react";

import { useAppendTicketMessage, useCloseTicket } from "../api/mutations";
import { useTicketDetail } from "../api/queries";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/support_/$ticketId")({
  beforeLoad: requireAuth,
  component: TicketDetailPage,
});

/** Platform hint of the send shortcut (the same decision as the CommandPalette COMMAND_KBD_HINT) */
const SEND_KBD_HINT = isMacPlatform() ? "⌘⏎" : "Ctrl+Enter";

function TicketDetailPage() {
  const { t } = useTranslation(["web", "shared"]);
  const { ticketId } = Route.useParams();
  const id = Number(ticketId);
  const navigate = useNavigate();
  const back = { label: t("support.backToList"), onClick: () => void navigate({ to: "/support" }) };
  const detail = useTicketDetail(id, {
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status === "open" || status === "pending_staff" || status === "pending_user" ? POLL.ticket : false;
    },
  });
  const [draft, setDraft] = useState("");
  const reply = useAppendTicketMessage({ onSuccess: () => setDraft("") });
  const close = useCloseTicket();
  const confirm = useConfirm();
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
    return (
      <PageContainer width="narrow" title={t("support.title")} back={back}>
        <DataErrorAlert onRetry={() => void detail.refetch()} />
      </PageContainer>
    );
  }
  const ticket = detail.data;
  if (!ticket) {
    return (
      <PageContainer width="narrow">
        <Skeleton active paragraph={{ rows: 6 }} />
      </PageContainer>
    );
  }
  const sm = metaOf(ticketStatusMap, ticket.status);
  const cm = metaOf(ticketCategoryMap, ticket.category);
  const repliable = isTicketRepliable(ticket.status);

  return (
    <PageContainer width="narrow" title={ticket.subject} back={back}>
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
        <Card
          title={
            <Space size={space.sm} wrap>
              <Typography.Text code>{ticket.ticket_no}</Typography.Text>
              {cm && <Tag>{t(cm.labelKey)}</Tag>}
              <Badge status={sm?.badge ?? "default"} text={sm ? t(sm.labelKey) : ticket.status} />
            </Space>
          }
          extra={
            <GatedButton
              size="small"
              loading={close.isPending}
              reason={ticket.status === "resolved" ? undefined : t("support.closeNeedsResolved")}
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
            </GatedButton>
          }
        >
          <Space size={space.lg} wrap>
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
          <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
            <div ref={scrollRef} onScroll={onScroll} style={{ maxHeight: 480, overflow: "auto" }}>
              <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
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
                  <GatedButton
                    type="primary"
                    style={{ height: "auto" }}
                    loading={reply.isPending}
                    reason={draft.trim().length < 2 ? t("support.replyTooShort") : undefined}
                    onClick={() => reply.mutate({ ticketId: id, body: { body: draft.trim() } })}
                  >
                    {t("support.replySend")}
                  </GatedButton>
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
    </PageContainer>
  );
}
