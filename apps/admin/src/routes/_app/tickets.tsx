/** 工单:status/category 筛选 + 详情抽屉(对话流 + 回复 + 标记解决/关闭)。
 * 读:全管理角色;写:ops/admin(canWriteOps),其余角色按钮置灰(后端 403 兜底)。
 */

import { formatDateTime, metaOf, ticketCategoryMap, ticketStatusMap } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  App,
  Badge,
  Button,
  Card,
  Drawer,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  theme,
  Tooltip,
  Typography,
} from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { AdminTicketDetailOut, AdminTicketOut } from "@superdl/api-client";
import {
  useReplyTicket,
  useTicketDetail,
  useTickets,
  useUpdateTicketStatus,
} from "../../api";
import { LIST_CAPS, ListCapNote } from "../../components/ListCapNote";
import { StatusTag } from "../../components/StatusTag";
import { TenantLink } from "../../components/TenantLink";
import { useApiErrorText } from "../../lib/apiError";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/tickets")({
  component: TicketsPage,
});

// resolved/closed 终态不可再回复(服务端同口径 409,此处只是不渲染输入框)
const REPLIABLE = new Set(["open", "pending_staff", "pending_user"]);

function Bubble({ msg }: { msg: NonNullable<AdminTicketDetailOut["messages"]>[number] }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const staff = msg.sender_kind === "staff";
  return (
    <div style={{ display: "flex", justifyContent: staff ? "flex-end" : "flex-start" }}>
      <div
        style={{
          maxWidth: "85%",
          padding: "8px 12px",
          borderRadius: 8,
          background: staff ? token.colorPrimaryBg : token.colorFillTertiary,
        }}
      >
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {staff ? t("tickets.msgStaff") : t("tickets.msgUser")} · {formatDateTime(msg.created_at)}
        </Typography.Text>
        <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{msg.body}</div>
      </div>
    </div>
  );
}

function TicketDrawer({
  ticketId,
  onClose,
  writable,
}: {
  ticketId: number | null;
  onClose: () => void;
  writable: boolean;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const detail = useTicketDetail(ticketId);
  const [draft, setDraft] = useState("");
  const reply = useReplyTicket();
  const updateStatus = useUpdateTicketStatus();
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "tickets"] });
    void qc.invalidateQueries({ queryKey: ["admin", "ticket", ticketId] });
  };

  const ticket = detail.data;
  const sm = ticket ? metaOf(ticketStatusMap, ticket.status) : undefined;
  const cm = ticket ? metaOf(ticketCategoryMap, ticket.category) : undefined;
  const repliable = ticket != null && REPLIABLE.has(ticket.status);
  const noPerm = t("tickets.opsOnly");

  const sendReply = () => {
    if (ticketId === null || draft.trim().length < 2) return;
    reply.mutate(
      { ticketId, data: { body: draft.trim() } },
      {
        onSuccess: () => {
          setDraft("");
          message.success(t("tickets.replied"));
          refresh();
        },
        onError: (e) => message.error(errText(e, t("tickets.replyFailed"))),
      },
    );
  };
  const setStatus = (action: "resolve" | "close") => {
    if (ticketId === null) return;
    updateStatus.mutate(
      { ticketId, data: { action } },
      {
        onSuccess: () => {
          message.success(t(action === "resolve" ? "tickets.resolvedOk" : "tickets.closedOk"));
          refresh();
        },
        onError: (e) => message.error(errText(e, t("tickets.statusFailed"))),
      },
    );
  };

  return (
    <Drawer
      open={ticketId !== null}
      onClose={onClose}
      size={640}
      title={
        ticket ? (
          <Space size={8} wrap>
            <Typography.Text code>{ticket.ticket_no}</Typography.Text>
            {cm && <Tag>{t(cm.labelKey)}</Tag>}
            {sm && <StatusTag color={sm.color}>{t(sm.labelKey)}</StatusTag>}
          </Space>
        ) : (
          t("tickets.detailTitle")
        )
      }
    >
      {ticket && (
        <Space orientation="vertical" size={16} style={{ width: "100%" }}>
          <div>
            <Typography.Title level={5} style={{ marginTop: 0 }}>
              {ticket.subject}
            </Typography.Title>
            <Space size={16} wrap>
              <TenantLink id={ticket.user_id} />
              <Typography.Text type="secondary">
                {t("tickets.colCreatedAt")}: {formatDateTime(ticket.created_at)}
              </Typography.Text>
              {ticket.instance_uuid && (
                <Typography.Text type="secondary">
                  {t("tickets.instanceUuid")}: {ticket.instance_uuid}
                </Typography.Text>
              )}
            </Space>
          </div>
          <Space orientation="vertical" size={12} style={{ width: "100%" }}>
            {(ticket.messages ?? []).map((m) => (
              <Bubble key={m.id} msg={m} />
            ))}
          </Space>
          {repliable ? (
            <Space.Compact style={{ width: "100%" }}>
              <Input.TextArea
                rows={3}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                maxLength={4000}
                placeholder={t("tickets.replyPlaceholder")}
                disabled={!writable}
              />
              <Tooltip title={writable ? "" : noPerm}>
                <Button
                  type="primary"
                  style={{ height: "auto" }}
                  loading={reply.isPending}
                  disabled={!writable || draft.trim().length < 2}
                  onClick={sendReply}
                >
                  {t("tickets.replySend")}
                </Button>
              </Tooltip>
            </Space.Compact>
          ) : (
            <Alert type="info" showIcon title={t("tickets.terminalHint")} />
          )}
          <Space wrap>
            {ticket.status !== "resolved" && ticket.status !== "closed" && (
              <Tooltip title={writable ? "" : noPerm}>
                <Button
                  type="primary"
                  disabled={!writable}
                  loading={updateStatus.isPending}
                  onClick={() => setStatus("resolve")}
                >
                  {t("tickets.resolve")}
                </Button>
              </Tooltip>
            )}
            {ticket.status === "resolved" && (
              <Popconfirm title={t("tickets.closeConfirm")} onConfirm={() => setStatus("close")}>
                <Tooltip title={writable ? "" : noPerm}>
                  <Button disabled={!writable} loading={updateStatus.isPending}>
                    {t("tickets.close")}
                  </Button>
                </Tooltip>
              </Popconfirm>
            )}
          </Space>
        </Space>
      )}
    </Drawer>
  );
}

function TicketsPage() {
  const { t } = useTranslation(["admin", "shared"]);
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const [status, setStatus] = useState<string | undefined>();
  const [category, setCategory] = useState<string | undefined>();
  const { data, isLoading } = useTickets({
    ...(status ? { status } : {}),
    ...(category ? { category } : {}),
  });
  const rows: AdminTicketOut[] = data ?? [];
  const [openId, setOpenId] = useState<number | null>(null);

  return (
    <Card
      title={t("tickets.title")}
      extra={
        <Space wrap>
          <Select
            allowClear
            placeholder={t("tickets.filterStatus")}
            style={{ width: 160 }}
            value={status}
            onChange={setStatus}
            options={Object.entries(ticketStatusMap).map(([v, m]) => ({
              value: v,
              label: t(m.labelKey),
            }))}
          />
          <Select
            allowClear
            placeholder={t("tickets.filterCategory")}
            style={{ width: 160 }}
            value={category}
            onChange={setCategory}
            options={Object.entries(ticketCategoryMap).map(([v, m]) => ({
              value: v,
              label: t(m.labelKey),
            }))}
          />
        </Space>
      }
    >
      <Table<AdminTicketOut>
        scroll={{ x: 960 }}
        rowKey="id"
        loading={isLoading}
        dataSource={rows}
        onRow={(r) => ({ onClick: () => setOpenId(r.id), style: { cursor: "pointer" } })}
        columns={[
          { title: t("tickets.colTicketNo"), dataIndex: "ticket_no", width: 140 },
          {
            title: t("tickets.colTenant"),
            dataIndex: "user_id",
            width: 80,
            render: (v: number) => <TenantLink id={v} />,
          },
          {
            title: t("tickets.colCategory"),
            dataIndex: "category",
            width: 110,
            render: (v: string) => {
              const m = metaOf(ticketCategoryMap, v);
              return m ? t(m.labelKey) : v;
            },
          },
          { title: t("tickets.colSubject"), dataIndex: "subject", ellipsis: true },
          {
            title: t("tickets.colStatus"),
            dataIndex: "status",
            width: 110,
            render: (v: string) => {
              const m = metaOf(ticketStatusMap, v);
              return (
                <Badge
                  status={m?.badge ?? "default"}
                  text={m ? t(m.labelKey) : v}
                />
              );
            },
          },
          { title: t("tickets.colUpdatedAt"), dataIndex: "updated_at", width: 150, render: formatDateTime },
          { title: t("tickets.colCreatedAt"), dataIndex: "created_at", width: 150, render: formatDateTime },
        ]}
      />
      <ListCapNote rows={rows.length} cap={LIST_CAPS.tickets} />
      <TicketDrawer ticketId={openId} onClose={() => setOpenId(null)} writable={writable} />
    </Card>
  );
}
