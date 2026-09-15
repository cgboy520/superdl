/** 支持:自助排查(FAQ 锚点)+ 联系客服(平台配置 support 组)+ 我的工单。新建工单走 Modal,详情为独立对话页。 */

import {
  flattenPages,
  fontSize,
  formatDateTime,
  idemKeyOf,
  metaOf,
  space,
  ticketCategoryMap,
  ticketStatusMap,
  type TicketStatus,
} from "@superdl/ui";
import { LoadMore, Mono, PageContainer, StatusTag, TableErrorEmpty } from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { App, Button, Card, Col, Empty, Form, Input, List, Modal, Row, Select, Space, Tag, Typography } from "antd";
import { useMemo, useState } from "react";

import type { TicketCreate, TicketOut } from "@superdl/api-client";
import { useCreateTicket } from "../api/mutations";
import { useInstances, useTicketPages } from "../api/queries";
import { ContactCard } from "../components/ContactCard";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/support")({
  beforeLoad: requireAuth,
  validateSearch: (search: Record<string, unknown>): { new?: "1"; status?: TicketStatus } => ({
    ...(search.new === "1" ? { new: "1" as const } : {}),
    ...(typeof search.status === "string" && search.status in ticketStatusMap
      ? { status: search.status as TicketStatus }
      : {}),
  }),
  component: SupportPage,
});

/** 自助排查:静态锚点直达 /help FAQ(条目键与 help.tsx 的 FAQ_KEYS 对齐)。 */
const SELF_HELP_KEYS = ["createFailed", "billingStart", "dataPersist", "arrears"] as const;

function SelfHelpCard() {
  const { t } = useTranslation();
  return (
    <Card title={t("support.selfHelpTitle")} style={{ height: "100%" }}>
      <List
        size="small"
        dataSource={[...SELF_HELP_KEYS]}
        renderItem={(k) => (
          <List.Item style={{ paddingInline: 0 }}>
            <Link to="/help" hash={`faq-${k}`}>
              {t(`support.selfHelp.${k}` as "support.selfHelp.createFailed")}
            </Link>
          </List.Item>
        )}
      />
      <Link to="/help">{t("support.selfHelpMore")}</Link>
    </Card>
  );
}

function CreateTicketModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation(["web", "shared"]);
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [form] = Form.useForm<TicketCreate>();
  const { data: instances } = useInstances();
  const [submitSeq, setSubmitSeq] = useState(0);
  const create = useCreateTicket({
    onSuccess: (ticket) => {
      message.success(t("support.created"));
      setSubmitSeq((s) => s + 1);
      form.resetFields();
      onClose();
      void navigate({ to: "/support/$ticketId", params: { ticketId: String(ticket.id) } });
    },
  });
  const category = Form.useWatch("category", form);

  return (
    <Modal
      title={t("support.createTitle")}
      open={open}
      onCancel={onClose}
      okText={t("support.createSubmit")}
      okButtonProps={{ loading: create.isPending }}
      onOk={() => {
        void form
          .validateFields()
          .then((values) => {
            create.mutate({
              body: values,
              idempotencyKey: idemKeyOf("ticket", [
                submitSeq,
                values.category,
                values.instance_uuid ?? null,
                values.subject,
                values.body,
              ]),
            });
          })
          .catch(() => undefined);
      }}
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: fontSize.caption }}>
        {t("support.createNote")}
      </Typography.Paragraph>
      <Form form={form} layout="vertical" initialValues={{ category: "instance" }}>
        <Form.Item name="category" label={t("support.colCategory")} rules={[{ required: true }]}>
          <Select
            options={Object.entries(ticketCategoryMap).map(([v, m]) => ({
              value: v,
              label: t(m.labelKey),
            }))}
          />
        </Form.Item>
        {category === "instance" && (
          <Form.Item name="instance_uuid" label={t("support.fieldInstance")}>
            <Select
              allowClear
              placeholder={t("support.fieldInstancePlaceholder")}
              options={(instances ?? []).map((i) => ({ value: i.uuid, label: i.name }))}
            />
          </Form.Item>
        )}
        <Form.Item name="subject" label={t("support.fieldSubject")} rules={[{ required: true, min: 2, max: 128 }]}>
          <Input maxLength={128} showCount placeholder={t("support.fieldSubjectPlaceholder")} />
        </Form.Item>
        <Form.Item name="body" label={t("support.fieldBody")} rules={[{ required: true, min: 2, max: 4000 }]}>
          <Input.TextArea rows={5} maxLength={4000} showCount placeholder={t("support.fieldBodyPlaceholder")} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function SupportPage() {
  const { t } = useTranslation(["web", "shared"]);
  const navigate = useNavigate();
  const { new: openNew, status: statusFilter } = Route.useSearch();
  const [creating, setCreating] = useState(false);
  const [prevNew, setPrevNew] = useState(openNew);
  if (openNew !== prevNew) {
    setPrevNew(openNew);
    if (openNew === "1") setCreating(true);
  }
  const closeCreate = () => {
    setCreating(false);
    if (openNew === "1")
      void navigate({
        to: "/support",
        search: (prev) => ({ status: prev.status as TicketStatus | undefined }),
        replace: true,
      });
  };
  const tickets = useTicketPages(20);
  const rows = useMemo(() => flattenPages(tickets.data), [tickets.data]);
  const filtered = useMemo<TicketOut[]>(
    () => (statusFilter ? rows.filter((r) => r.status === statusFilter) : rows),
    [rows, statusFilter],
  );

  return (
    <PageContainer
      width="narrow"
      title={t("support.title")}
      extra={
        <Button type="primary" onClick={() => setCreating(true)}>
          {t("support.create")}
        </Button>
      }
    >
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
        <Row gutter={16}>
          <Col xs={24} md={12}>
            <SelfHelpCard />
          </Col>
          <Col xs={24} md={12}>
            <ContactCard
              title={t("support.contactTitle")}
              emailLabel={t("support.contactEmail")}
              wechatLabel={t("support.contactWechat")}
              hint={t("support.contactHint")}
              missingText={t("support.contactMissing")}
              style={{ height: "100%" }}
            />
          </Col>
        </Row>
        <Card
          title={t("support.myTickets")}
          extra={
            <Select
              size="small"
              style={{ width: 150 }}
              aria-label={t("support.filterStatus")}
              value={statusFilter ?? "all"}
              onChange={(v: string) =>
                void navigate({
                  to: "/support",
                  search: (prev) => ({
                    ...prev,
                    status: v === "all" ? undefined : (v as TicketStatus),
                  }),
                  replace: true,
                })
              }
              options={[
                { value: "all", label: t("support.filterAll") },
                ...Object.keys(ticketStatusMap).map((s) => {
                  const meta = metaOf(ticketStatusMap, s);
                  return { value: s, label: meta ? t(meta.labelKey) : s };
                }),
              ]}
            />
          }
        >
          <List
            loading={tickets.isLoading}
            dataSource={filtered}
            locale={{
              emptyText: tickets.isError ? (
                <TableErrorEmpty isError onRetry={() => void tickets.refetch()} />
              ) : statusFilter ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("support.noneWithStatus")} />
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("support.none")}>
                  <Button type="primary" onClick={() => setCreating(true)}>
                    {t("support.create")}
                  </Button>
                </Empty>
              ),
            }}
            renderItem={(r) => {
              const cm = metaOf(ticketCategoryMap, r.category);
              return (
                <List.Item>
                  <List.Item.Meta
                    title={
                      <Space size={space.sm} wrap>
                        <Mono>{r.ticket_no}</Mono>
                        {cm && <Tag>{t(cm.labelKey)}</Tag>}
                        <Link to="/support/$ticketId" params={{ ticketId: String(r.id) }}>
                          {r.subject}
                        </Link>
                      </Space>
                    }
                  />
                  <Space size={space.md}>
                    <StatusTag map={ticketStatusMap} value={r.status} variant="badge" />
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                      {formatDateTime(r.updated_at)}
                    </Typography.Text>
                  </Space>
                </List.Item>
              );
            }}
          />
          <LoadMore
            hasNextPage={tickets.hasNextPage}
            loading={tickets.isFetchingNextPage}
            isError={tickets.isFetchNextPageError}
            loadedCount={filtered.length}
            onLoadMore={() => void tickets.fetchNextPage()}
          />
        </Card>
        <CreateTicketModal open={creating} onClose={closeCreate} />
      </Space>
    </PageContainer>
  );
}
