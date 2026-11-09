/** My images: upload `docker save` archives (.tar / .tar.gz / .tgz / .zip) in resumable 8 MiB chunks, follow the import, share a ready image with the tenant, delete. Quotas come from /policies; dev-box readiness (the platform SSH / Jupyter entrypoint) and the architecture are read from the image config. */

import type { CustomImageOut } from "@superdl/api-client";
import {
  customImageStatusMap,
  fontSize,
  formatBytes,
  formatDateTime,
  idemKeyOf,
  space,
  useApiErrorText,
} from "@superdl/ui";
import { CopyField, PageContainer, StatusTag, TableErrorEmpty, useConfirm } from "@superdl/ui/components";
import { InboxOutlined } from "@ant-design/icons";
import { createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Collapse,
  Empty,
  Input,
  Modal,
  Progress,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ARCHIVE_ACCEPT,
  ChunkSizeMismatch,
  firstChunk,
  isArchiveName,
  repoNameOf,
  sha256Hex,
} from "../api/imageUpload";
import { useDeleteCustomImage, useStartImageUpload, useUpdateCustomImage } from "../api/mutations";
import { useCustomImages, useMyOrg, usePolicies } from "../api/queries";
import { keys } from "../api/keys";
import { Field } from "../components/Field";
import { imageLabel } from "../components/create/MyImagePicker";
import { requireAuth } from "../lib/guard";
import {
  cancelImageUpload,
  imageUploadStore,
  isUploadActive,
  startImageUpload,
  useImageUploads,
  type UploadState,
} from "../stores/imageUploads";
import { useQueryClient } from "@tanstack/react-query";

export const Route = createFileRoute("/_console/images")({
  beforeLoad: requireAuth,
  component: ImagesPage,
});

const GIB = 1024 ** 3;
const QUOTA_STATUSES = new Set(["uploading", "importing", "ready", "failed"]);
const TAG_RE = /^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$/;
const REPO_RE = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/;

const imageBytes = (img: CustomImageOut) => img.size_bytes ?? img.upload_bytes;
/** registry/…/name:tag@sha256:<64 hex> → "sha256:<first 12 hex>" for the copy chip; the full reference is what gets copied. */
const shortDigest = (ref: string) => (ref.split("@")[1] ?? ref).slice(0, 19);

function useRefreshImages() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: keys.customImages.all });
}

/** Upload failures from the chunk loop: API refusals by catalog key, a stale page asks for a reload. */
function useUploadErrorText() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  return (err: unknown) => (err instanceof ChunkSizeMismatch ? t("images.reloadPage") : errText(err));
}

function QuotaCard({ images }: { images: CustomImageOut[] }) {
  const { t } = useTranslation();
  const { data: policies } = usePolicies();
  const mine = images.filter((i) => i.owned && QUOTA_STATUSES.has(i.status));
  const usedBytes = mine.reduce((acc, i) => acc + imageBytes(i), 0);
  if (!policies) return null;
  const maxBytes = policies.custom_image_max_total_gb * GIB;
  return (
    <Card size="small">
      <Space size={space.xl} wrap style={{ width: "100%" }}>
        <Space orientation="vertical" size={0} style={{ minWidth: 200 }}>
          <Typography.Text type="secondary">{t("images.quotaCount")}</Typography.Text>
          <Typography.Text strong>
            {mine.length} / {policies.custom_image_max_count}
          </Typography.Text>
        </Space>
        <Space orientation="vertical" size={0} style={{ minWidth: 240 }}>
          <Typography.Text type="secondary">{t("images.quotaSpace")}</Typography.Text>
          <Typography.Text strong>
            {formatBytes(usedBytes)} / {policies.custom_image_max_total_gb} GB
          </Typography.Text>
          <Progress
            percent={Math.min(100, Math.round((usedBytes / Math.max(1, maxBytes)) * 100))}
            size="small"
            showInfo={false}
            aria-label={t("images.quotaSpace")}
          />
        </Space>
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption, maxWidth: 420 }}>
          {t("images.quotaNote", { max: policies.custom_image_max_upload_gb })}
        </Typography.Text>
      </Space>
    </Card>
  );
}

function RecipeCard() {
  const { t } = useTranslation();
  return (
    <Collapse
      size="small"
      items={[
        {
          key: "recipe",
          label: t("images.recipeTitle"),
          children: (
            <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
              <Typography.Text>{t("images.recipeSave")}</Typography.Text>
              <CopyField code value="docker save my-env:v1 | gzip > my-env.tar.gz" />
              <Typography.Text>{t("images.recipeArch")}</Typography.Text>
              <CopyField code value="docker buildx build --platform linux/arm64 -t my-env:v1 --load ." />
              <Typography.Text>{t("images.recipeDev")}</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {t("images.recipeDevDetail")}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {t("images.recipeService")}
              </Typography.Text>
            </Space>
          ),
        },
      ]}
    />
  );
}

/** Pick a file, name the image, open the upload session and hand the chunks to the upload store. */
function UploadModal({ open, onClose, inTenant }: { open: boolean; onClose: () => void; inTenant: boolean }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const refresh = useRefreshImages();
  const uploadError = useUploadErrorText();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [tag, setTag] = useState("latest");
  const [description, setDescription] = useState("");
  const [shared, setShared] = useState(false);
  const [hashing, setHashing] = useState(false);
  const [seq] = useState(() => crypto.randomUUID());
  const start = useStartImageUpload({ silentError: true });
  const nameError = name && !REPO_RE.test(name) ? t("images.nameInvalid") : null;
  const tagError = tag && !TAG_RE.test(tag) ? t("images.tagInvalid") : null;
  const ready = file != null && name !== "" && tag !== "" && !nameError && !tagError;

  const pick = (f: File) => {
    if (!isArchiveName(f.name)) {
      message.error(t("images.formatHint"));
      return;
    }
    setFile(f);
    if (!name) setName(repoNameOf(f.name));
  };
  const submit = async () => {
    if (!file || !ready) return;
    setHashing(true);
    try {
      const firstSha = await sha256Hex(firstChunk(file));
      const image = await start.mutateAsync({
        body: {
          name,
          tag,
          filename: file.name,
          size_bytes: file.size,
          first_chunk_sha256: firstSha,
          description: description.trim() || null,
          shared_with_org: inTenant && shared,
        },
        idempotencyKey: idemKeyOf("image", [seq, name, tag, file.name, file.size, firstSha]),
      });
      startImageUpload(file, image, refresh);
      message.success(t("images.uploadStarted", { name: imageLabel(image) }));
      onClose();
    } catch (err) {
      message.error(uploadError(err));
    } finally {
      setHashing(false);
    }
  };
  return (
    <Modal
      title={t("images.uploadTitle")}
      open={open}
      onCancel={onClose}
      onOk={() => void submit()}
      okText={t("images.uploadSubmit")}
      okButtonProps={{ disabled: !ready }}
      confirmLoading={hashing || start.isPending}
      width={640}
      destroyOnHidden
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Upload.Dragger
          accept={ARCHIVE_ACCEPT}
          multiple={false}
          showUploadList={false}
          beforeUpload={(f) => {
            pick(f);
            return false;
          }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">{file ? `${file.name} · ${formatBytes(file.size)}` : t("images.dropHint")}</p>
          <p className="ant-upload-hint">{t("images.formatHint")}</p>
        </Upload.Dragger>
        <Space size={space.md} style={{ width: "100%" }} wrap>
          <Field label={t("images.nameLabel")} required error={nameError}>
            <Input
              maxLength={64}
              aria-label={t("images.nameLabel")}
              value={name}
              onChange={(e) => setName(e.target.value.toLowerCase())}
              style={{ width: 280 }}
              className="mono"
            />
          </Field>
          <Field label={t("images.tagLabel")} required error={tagError}>
            <Input
              maxLength={128}
              aria-label={t("images.tagLabel")}
              value={tag}
              onChange={(e) => setTag(e.target.value)}
              style={{ width: 200 }}
              className="mono"
            />
          </Field>
        </Space>
        <Field label={t("images.descriptionLabel")}>
          <Input
            maxLength={200}
            aria-label={t("images.descriptionLabel")}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        {inTenant && (
          <Checkbox checked={shared} onChange={(e) => setShared(e.target.checked)}>
            {t("images.shareWithTenant")}
          </Checkbox>
        )}
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("images.uploadNote")}
        </Typography.Text>
      </Space>
    </Modal>
  );
}

/** A reload lost the File: pick the same archive again to continue where the server's bitmap left off. */
function ResumeModal({ image, onClose }: { image: CustomImageOut | null; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const refresh = useRefreshImages();
  return (
    <Modal
      title={t("images.resumeTitle", { name: image ? imageLabel(image) : "" })}
      open={Boolean(image)}
      onCancel={onClose}
      footer={null}
      destroyOnHidden
    >
      {image && (
        <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
          <Typography.Text>
            {t("images.resumeBody", { file: image.filename, size: formatBytes(image.upload_bytes) })}
          </Typography.Text>
          <Upload.Dragger
            accept={ARCHIVE_ACCEPT}
            multiple={false}
            showUploadList={false}
            beforeUpload={(f) => {
              if (f.size !== image.upload_bytes) {
                message.error(t("images.resumeWrongFile"));
                return false;
              }
              startImageUpload(f, image, refresh);
              onClose();
              return false;
            }}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">{t("images.resumePick")}</p>
          </Upload.Dragger>
        </Space>
      )}
    </Modal>
  );
}

function StatusCell({ image, upload }: { image: CustomImageOut; upload: UploadState | undefined }) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const uploadError = useUploadErrorText();
  if (image.status === "uploading" && upload && upload.phase !== "done") {
    const pct = Math.floor((upload.sentBytes / Math.max(1, upload.totalBytes)) * 100);
    return (
      <Space orientation="vertical" size={0} style={{ minWidth: 180 }}>
        <StatusTag map={customImageStatusMap} value="uploading" variant="badge" />
        {upload.phase === "failed" ? (
          <Typography.Text type="danger" style={{ fontSize: fontSize.caption }}>
            {uploadError(upload.error)}
          </Typography.Text>
        ) : upload.phase === "cancelled" ? (
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("images.uploadPaused")}
          </Typography.Text>
        ) : (
          <Progress percent={pct} size="small" aria-label={t("images.uploadProgress")} />
        )}
      </Space>
    );
  }
  if (image.status === "uploading") {
    return (
      <Space orientation="vertical" size={0}>
        <StatusTag map={customImageStatusMap} value="uploading" variant="badge" />
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {t("images.uploadInterrupted", {
            received: image.chunks_received,
            total: image.chunks_total,
            time: formatDateTime(image.upload_expires_at),
          })}
        </Typography.Text>
      </Space>
    );
  }
  if (image.status === "importing") {
    const pct = Math.floor((image.progress_bytes / Math.max(1, image.upload_bytes)) * 100);
    return (
      <Space orientation="vertical" size={0} style={{ minWidth: 180 }}>
        <StatusTag map={customImageStatusMap} value="importing" variant="badge" />
        <Progress percent={pct} size="small" status="active" aria-label={t("images.importProgress")} />
      </Space>
    );
  }
  if (image.status === "failed") {
    return (
      <Space orientation="vertical" size={0} style={{ maxWidth: 320 }}>
        <StatusTag map={customImageStatusMap} value="failed" variant="badge" />
        <Typography.Text type="danger" style={{ fontSize: fontSize.caption }}>
          {errText(
            { message_key: image.error_key, message: image.error_detail ?? undefined },
            t("images.failedUnknown"),
          )}
        </Typography.Text>
      </Space>
    );
  }
  return <StatusTag map={customImageStatusMap} value={image.status} variant="badge" />;
}

function ImagesPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const q = useCustomImages();
  const { data: org } = useMyOrg();
  const inTenant = Boolean(org?.org);
  const uploads = useImageUploads();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [resumeTarget, setResumeTarget] = useState<CustomImageOut | null>(null);
  const update = useUpdateCustomImage();
  const del = useDeleteCustomImage({ onSuccess: () => message.success(t("images.deleted")) });
  const images = (q.data ?? []).filter((i) => i.status !== "deleting" || i.owned);

  const askDelete = (img: CustomImageOut) => {
    const upload = uploads[img.uuid];
    confirm({
      title: t("images.deleteTitle", { name: imageLabel(img) }),
      consequences: [
        img.status === "ready" ? t("images.deleteReady") : t("images.deleteUpload"),
        ...(img.shared_with_org ? [t("images.deleteShared")] : []),
      ],
      danger: true,
      okText: t("images.delete"),
      onOk: async () => {
        if (isUploadActive(upload)) cancelImageUpload(img.uuid);
        await del.mutateAsync(img.uuid);
        imageUploadStore.getState().clear(img.uuid);
      },
    });
  };

  return (
    <PageContainer
      title={t("images.title")}
      description={t("images.description")}
      extra={
        <Button type="primary" onClick={() => setUploadOpen(true)}>
          {t("images.upload")}
        </Button>
      }
    >
      <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
        <QuotaCard images={q.data ?? []} />
        <RecipeCard />
        <Card>
          {q.isError ? (
            <TableErrorEmpty isError onRetry={() => void q.refetch()} />
          ) : images.length === 0 && !q.isLoading ? (
            <Empty description={t("images.empty")}>
              <Button type="primary" onClick={() => setUploadOpen(true)}>
                {t("images.upload")}
              </Button>
            </Empty>
          ) : (
            <Table<CustomImageOut>
              rowKey="uuid"
              loading={q.isLoading}
              pagination={false}
              scroll={{ x: 1100 }}
              dataSource={images}
              columns={[
                {
                  title: t("images.colImage"),
                  render: (_, r) => (
                    <Space orientation="vertical" size={0}>
                      <Typography.Text strong className="mono">
                        {imageLabel(r)}
                      </Typography.Text>
                      {r.description && (
                        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                          {r.description}
                        </Typography.Text>
                      )}
                      {!r.owned && (
                        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                          {t("images.sharedBy", { owner: r.owner_label ?? "" })}
                        </Typography.Text>
                      )}
                    </Space>
                  ),
                },
                { title: t("images.colStatus"), render: (_, r) => <StatusCell image={r} upload={uploads[r.uuid]} /> },
                {
                  title: t("images.colArch"),
                  render: (_, r) => (r.architecture ? <Tag variant="filled">{r.architecture}</Tag> : "—"),
                },
                { title: t("images.colSize"), render: (_, r) => formatBytes(imageBytes(r)) },
                {
                  title: t("images.colDev"),
                  render: (_, r) =>
                    r.status !== "ready" ? (
                      "—"
                    ) : r.dev_ready ? (
                      <Tooltip title={t("images.devReadyTip")}>
                        <Tag color="green" variant="filled">
                          {t("images.devReady")}
                        </Tag>
                      </Tooltip>
                    ) : (
                      <Tooltip title={t("images.serviceOnlyTip")}>
                        <Tag variant="filled">{t("images.serviceOnly")}</Tag>
                      </Tooltip>
                    ),
                },
                {
                  title: t("images.colShared"),
                  render: (_, r) =>
                    r.owned && inTenant ? (
                      <Switch
                        size="small"
                        checked={r.shared_with_org}
                        aria-label={t("images.shareWithTenant")}
                        disabled={r.status === "deleting"}
                        loading={update.isPending && update.variables.uuid === r.uuid}
                        onChange={(v) => update.mutate({ uuid: r.uuid, body: { shared_with_org: v } })}
                      />
                    ) : r.shared_with_org ? (
                      <Tag variant="filled">{t("images.shared")}</Tag>
                    ) : (
                      "—"
                    ),
                },
                { title: t("images.colCreated"), render: (_, r) => formatDateTime(r.created_at) },
                {
                  title: t("images.colActions"),
                  render: (_, r) => {
                    const upload = uploads[r.uuid];
                    return (
                      <Space size={space.xs} wrap>
                        {r.status === "ready" && r.image_ref && (
                          <CopyField value={r.image_ref} display={shortDigest(r.image_ref)} code />
                        )}
                        {r.owned && r.status === "uploading" && isUploadActive(upload) && (
                          <Button size="small" onClick={() => cancelImageUpload(r.uuid)}>
                            {t("images.pause")}
                          </Button>
                        )}
                        {r.owned && r.status === "uploading" && !isUploadActive(upload) && (
                          <Button size="small" onClick={() => setResumeTarget(r)}>
                            {t("images.resume")}
                          </Button>
                        )}
                        {r.owned && r.status !== "deleting" && (
                          <Button size="small" danger disabled={r.status === "importing"} onClick={() => askDelete(r)}>
                            {t("images.delete")}
                          </Button>
                        )}
                      </Space>
                    );
                  },
                },
              ]}
            />
          )}
        </Card>
        {!inTenant ? null : <Alert type="info" showIcon title={t("images.tenantShareNote")} />}
      </Space>
      <UploadModal open={uploadOpen} onClose={() => setUploadOpen(false)} inTenant={inTenant} />
      <ResumeModal image={resumeTarget} onClose={() => setResumeTarget(null)} />
    </PageContainer>
  );
}
