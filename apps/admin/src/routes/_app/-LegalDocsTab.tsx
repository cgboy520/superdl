/** 法务文档 Tab:doc_key × locale 状态格 + 左编辑右预览 + 版本历史;写仅 admin;发布确认带行级 diff 统计。 */

import { adminColors, formatDateTime, legalDocStatusMap, useApiErrorText, useFormDraft } from "@superdl/ui";
import { DataErrorAlert, LegalMarkdown, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Card, Collapse, Input, Modal, Space, Table, Tooltip, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  isApiError,
  useArchiveLegalDocVersion,
  useCreateLegalDocVersion,
  useLegalDocs,
  useLegalDocVersions,
  usePublishLegalDocVersion,
  useUpdateLegalDocVersion,
  type LegalDocCell,
  type LegalDocVersion,
  type LegalLocale,
} from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { StatusTag } from "../../components/StatusTag";
import { useAdminRole } from "../../stores/auth";

const DOC_KEYS = ["terms", "privacy", "deletion_notice"] as const;
const LOCALES: readonly LegalLocale[] = ["zh-CN", "en-US"];

/** 行级 diff 统计(LCS):+added/−removed。 */
function diffStats(oldText: string, newText: string): { added: number; removed: number } {
  const a = oldText.split("\n");
  const b = newText.split("\n");
  const m = a.length;
  const n = b.length;
  const dp: Uint32Array[] = Array.from({ length: m + 1 }, () => new Uint32Array(n + 1));
  for (let i = m - 1; i >= 0; i--) {
    const row = dp[i];
    const next = dp[i + 1];
    if (!row || !next) continue;
    for (let j = n - 1; j >= 0; j--) {
      // 单元格在构造时已零填充,?? 0 只为满足 noUncheckedIndexedAccess
      row[j] = a[i] === b[j] ? (next[j + 1] ?? 0) + 1 : Math.max(next[j] ?? 0, row[j + 1] ?? 0);
    }
  }
  const lcs = dp[0]?.[0] ?? 0;
  return { added: n - lcs, removed: m - lcs };
}

export function LegalDocsTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const role = useAdminRole();
  const writable = role === "admin";
  const qc = useQueryClient();
  const overview = useLegalDocs();
  const [selected, setSelected] = useState<{ docKey: string; locale: LegalLocale } | null>(null);

  const cells = useMemo(() => {
    const map = new Map<string, LegalDocCell>();
    for (const c of overview.data ?? []) map.set(`${c.doc_key}:${c.locale}`, c);
    return map;
  }, [overview.data]);

  const docLabel = (docKey: string) =>
    docKey === "terms"
      ? t("settings.legal.docTerms")
      : docKey === "privacy"
        ? t("settings.legal.docPrivacy")
        : t("settings.legal.docDeletionNotice");

  return (
    <Space orientation="vertical" size={12} style={{ width: "100%" }}>
      {!writable && <Alert type="info" showIcon title={t("settings.legal.adminOnlyTip")} />}
      {overview.isError && (
        <DataErrorAlert
          title={t("common.loadFailed", { ns: "shared" })}
          description={null}
          onRetry={() => void overview.refetch()}
        />
      )}
      <Table
        rowKey="docKey"
        size="small"
        loading={overview.isLoading}
        pagination={false}
        dataSource={DOC_KEYS.map((docKey) => ({ docKey }))}
        columns={[
          { title: t("settings.legal.colDoc"), dataIndex: "docKey", render: docLabel },
          ...LOCALES.map((locale) => ({
            title: locale,
            render: (_: unknown, r: { docKey: string }) => {
              const cell = cells.get(`${r.docKey}:${locale}`);
              const active = selected?.docKey === r.docKey && selected.locale === locale;
              return (
                <Card
                  size="small"
                  hoverable
                  onClick={() => setSelected({ docKey: r.docKey, locale })}
                  style={active ? { borderColor: adminColors.dataAccent } : undefined}
                >
                  {cell?.published && (
                    <div>{t("settings.legal.statusPublished", { version: cell.published.version })}</div>
                  )}
                  {cell?.draft && (
                    <div style={{ color: adminColors.alertAccent }}>
                      {t("settings.legal.statusDraftV", { version: cell.draft.version })}
                    </div>
                  )}
                  {!cell?.published && !cell?.draft && (
                    <span style={{ color: adminColors.textSecondary }}>{t("settings.legal.missing")}</span>
                  )}
                </Card>
              );
            },
          })),
        ]}
      />
      {selected ? (
        <CellEditor
          key={`${selected.docKey}:${selected.locale}`}
          docKey={selected.docKey}
          locale={selected.locale}
          docLabel={docLabel(selected.docKey)}
          writable={writable}
          onChanged={() => void qc.invalidateQueries({ queryKey: overview.queryKey })}
          errText={errText}
        />
      ) : (
        <Typography.Text type="secondary">{t("settings.legal.emptyEditor")}</Typography.Text>
      )}
    </Space>
  );
}

function CellEditor({
  docKey,
  locale,
  docLabel,
  writable,
  onChanged,
  errText,
}: {
  docKey: string;
  locale: LegalLocale;
  docLabel: string;
  writable: boolean;
  onChanged: () => void;
  errText: (e: unknown, fallback?: string) => string;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const { message } = App.useApp();
  const qc = useQueryClient();
  const versionsQ = useLegalDocVersions(docKey, locale);
  const versions = useMemo(() => versionsQ.data ?? [], [versionsQ.data]);
  const draft = versions.find((v) => v.status === "draft") ?? null;
  const published = versions.find((v) => v.status === "published") ?? null;

  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [note, setNote] = useState("");
  const [publishOpen, setPublishOpen] = useState(false);
  // 编辑器草稿(sessionStorage),保存/发布成功后清除
  const localDraft = useFormDraft<{ title: string; content: string; note: string }>(`legal-doc-${docKey}-${locale}`);
  // 数据源切换时同步表单(渲染期派生态)
  const sourceKey = draft ? `d${draft.id}` : published ? `p${published.id}` : "none";
  const [loadedKey, setLoadedKey] = useState("");
  if (!versionsQ.isLoading && loadedKey !== sourceKey) {
    setLoadedKey(sourceKey);
    // 本地草稿优先于服务端值
    const saved = localDraft.load();
    setTitle(saved?.title ?? draft?.title ?? published?.title ?? "");
    setContent(saved?.content ?? draft?.content_md ?? published?.content_md ?? "");
    setNote(saved?.note ?? draft?.effective_note ?? "");
  }
  useEffect(() => {
    if (loadedKey !== "") localDraft.save({ title, content, note });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- localDraft 引用稳定(纯 storage 封装)
  }, [loadedKey, title, content, note]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: versionsQ.queryKey });
    onChanged();
  };
  const create = useCreateLegalDocVersion({
    mutation: {
      onSuccess: () => invalidate(),
      onError: (e) => message.error(errText(e)),
    },
  });
  const update = useUpdateLegalDocVersion({
    mutation: {
      onSuccess: () => {
        message.success(t("settings.legal.saved"));
        localDraft.clear();
        invalidate();
      },
      onError: (e) => message.error(errText(e)),
    },
  });
  const publish = usePublishLegalDocVersion({
    mutation: {
      onSuccess: (v) => {
        message.success(t("settings.legal.publishDone", { version: v.version }));
        localDraft.clear();
        setPublishOpen(false);
        invalidate();
      },
      onError: (e) => message.error(errText(e)),
    },
  });
  const archive = useArchiveLegalDocVersion({
    mutation: {
      // 反馈由 ReasonAction 承担,这里只刷新数据
      onSuccess: () => invalidate(),
    },
  });

  const diff = useMemo(
    () => (published && draft ? diffStats(published.content_md, content) : null),
    [published, draft, content],
  );
  const dirty =
    draft !== null && (title !== draft.title || content !== draft.content_md || note !== (draft.effective_note ?? ""));

  return (
    <Card
      title={`${docLabel} · ${locale}`}
      extra={
        <Space>
          {!draft && (
            <Tooltip title={writable ? "" : t("settings.legal.adminOnlyTip")}>
              <Button
                type="primary"
                disabled={!writable}
                loading={create.isPending}
                onClick={() => create.mutate({ docKey, data: { locale } })}
              >
                {t("settings.legal.create")}
              </Button>
            </Tooltip>
          )}
          {draft && (
            <>
              <Button
                disabled={!writable || !dirty}
                loading={update.isPending}
                onClick={() =>
                  update.mutate({
                    versionId: draft.id,
                    data: { title, content_md: content, effective_note: note || null },
                  })
                }
              >
                {t("settings.legal.saveDraft")}
              </Button>
              <Button type="primary" disabled={!writable} onClick={() => setPublishOpen(true)}>
                {t("settings.legal.publish")}
              </Button>
              <ReasonAction
                label={t("settings.legal.archive")}
                target={`${docLabel} · v${draft.version}`}
                title={t("settings.legal.archive")}
                confirmText={t("settings.legal.confirmArchive")}
                danger
                size="middle"
                disabled={!writable}
                disabledReason={t("settings.legal.adminOnlyTip")}
                onSubmit={async (reason) => {
                  await archive.mutateAsync({ versionId: draft.id, data: { reason } });
                }}
              />
            </>
          )}
        </Space>
      }
    >
      <Space size={12} align="start" style={{ width: "100%" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Typography.Text type="secondary">{t("settings.legal.editTitle")}</Typography.Text>
          <Input
            value={title}
            disabled={!draft || !writable}
            maxLength={128}
            onChange={(e) => setTitle(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <Typography.Text type="secondary">{t("settings.legal.editContent")}</Typography.Text>
          <Input.TextArea
            value={content}
            disabled={!draft || !writable}
            rows={16}
            onChange={(e) => setContent(e.target.value)}
            style={{ fontFamily: "monospace", marginBottom: 8 }}
          />
          <Typography.Text type="secondary">{t("settings.legal.effectiveNote")}</Typography.Text>
          <Input
            value={note}
            disabled={!draft || !writable}
            maxLength={512}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Typography.Text type="secondary">{t("settings.legal.preview")}</Typography.Text>
          <div
            style={{
              border: `1px solid ${adminColors.divider}`,
              borderRadius: 8,
              padding: "8px 16px",
              maxHeight: 560,
              overflow: "auto",
            }}
          >
            <LegalMarkdown content={content} />
          </div>
        </div>
      </Space>
      <Collapse
        style={{ marginTop: 12 }}
        items={[
          {
            key: "history",
            label: t("settings.legal.history"),
            children: (
              <Table<LegalDocVersion>
                rowKey="id"
                size="small"
                loading={versionsQ.isLoading}
                locale={{
                  emptyText: (
                    <TableErrorEmpty
                      isError={versionsQ.isError}
                      isForbidden={isApiError(versionsQ.error) && versionsQ.error.status === 403}
                      onRetry={() => void versionsQ.refetch()}
                    />
                  ),
                }}
                pagination={false}
                dataSource={versions}
                columns={[
                  { title: t("settings.legal.colVersion"), dataIndex: "version", width: 80 },
                  {
                    title: t("settings.legal.colStatus"),
                    dataIndex: "status",
                    width: 100,
                    render: (s: string) => {
                      return <StatusTag map={legalDocStatusMap} value={s} />;
                    },
                  },
                  {
                    title: t("settings.legal.colCreatedAt"),
                    dataIndex: "created_at",
                    render: formatDateTime,
                  },
                  {
                    title: t("settings.legal.colPublishedAt"),
                    dataIndex: "published_at",
                    render: formatDateTime,
                  },
                  { title: t("settings.legal.colNote"), dataIndex: "effective_note" },
                ]}
              />
            ),
          },
        ]}
      />
      <Modal
        title={draft ? t("settings.legal.confirmPublishTitle", { version: draft.version }) : ""}
        open={publishOpen}
        onCancel={() => setPublishOpen(false)}
        okButtonProps={{ loading: publish.isPending }}
        onOk={() => draft && publish.mutate({ versionId: draft.id })}
      >
        {diff ? (
          <Typography.Text>
            {t("settings.legal.diffLine", { added: diff.added, removed: diff.removed })}
          </Typography.Text>
        ) : (
          <Typography.Text>{t("settings.legal.firstPublish")}</Typography.Text>
        )}
      </Modal>
    </Card>
  );
}
