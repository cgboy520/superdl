/** "My images" tab of the create / deploy pages: ready uploaded images (own and tenant-shared) with their architecture and, for dev boxes, the SSH / Jupyter readiness check. */

import type { CustomImageOut } from "@superdl/api-client";
import { controlWidth, fontSize, space } from "@superdl/ui";
import { CopyField, DataErrorAlert } from "@superdl/ui/components";
import { Link } from "@tanstack/react-router";
import { Alert, Select, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useCustomImages } from "../../api/queries";

export const imageLabel = (img: Pick<CustomImageOut, "name" | "tag">) => `${img.name}:${img.tag}`;

export function MyImagePicker({
  value,
  onChange,
  purpose,
  archs,
}: {
  value: string | undefined;
  onChange: (ref: string | undefined) => void;
  /** dev = SSH + Jupyter box (warns about images without the platform entrypoint); service = runs the image's own command. */
  purpose: "dev" | "service";
  /** CPU architectures of the chosen spec's nodes; an image built for another one cannot run there. */
  archs?: readonly string[];
}) {
  const { t } = useTranslation();
  const q = useCustomImages();
  if (q.isError) return <DataErrorAlert onRetry={() => void q.refetch()} />;
  const ready = (q.data ?? []).filter((i) => i.status === "ready" && i.image_ref);
  const selected = ready.find((i) => i.image_ref === value);
  const archMismatch = (img: CustomImageOut) =>
    Boolean(img.architecture && archs && archs.length > 0 && !archs.includes(img.architecture));
  return (
    <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
      <Select
        style={{ width: "100%", maxWidth: controlWidth.lg }}
        loading={q.isLoading}
        placeholder={t("create.myImagePlaceholder")}
        aria-label={t("create.tabMine")}
        value={selected?.image_ref ?? undefined}
        onChange={(ref: string) => onChange(ref)}
        showSearch={{ optionFilterProp: "label" }}
        options={ready.map((img) => ({
          value: img.image_ref as string,
          label: [
            imageLabel(img),
            img.architecture,
            img.owned ? null : img.owner_label,
            archMismatch(img) ? t("create.myImageArchShort") : null,
          ]
            .filter(Boolean)
            .join(" · "),
        }))}
        notFoundContent={
          <Space orientation="vertical" size={0}>
            <Typography.Text type="secondary">{t("create.myImageEmpty")}</Typography.Text>
            <Link to="/images">{t("create.myImageUpload")}</Link>
          </Space>
        }
      />
      {selected && (
        <Space orientation="vertical" size={space.xs} style={{ width: "100%" }}>
          <Space size={space.sm} wrap>
            <CopyField value={selected.image_ref ?? ""} code />
            {selected.architecture && <Tag variant="filled">{selected.architecture}</Tag>}
            {!selected.owned && (
              <Tag variant="filled">{t("create.myImageShared", { owner: selected.owner_label ?? "" })}</Tag>
            )}
          </Space>
          {selected.description && (
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {selected.description}
            </Typography.Text>
          )}
          {archMismatch(selected) && (
            <Alert
              type="error"
              showIcon
              title={t("create.myImageArchMismatch", {
                arch: selected.architecture ?? "",
                archs: (archs ?? []).join(" / "),
              })}
            />
          )}
          {purpose === "dev" && !selected.dev_ready && (
            <Alert
              type="warning"
              showIcon
              title={t("create.myImageNotDevReady")}
              description={
                <span>
                  {t("create.myImageNotDevReadyBody")} <Link to="/images">{t("create.myImageRecipe")}</Link>
                </span>
              }
            />
          )}
          {purpose === "dev" && selected.dev_ready && (
            <Typography.Text type="success" style={{ fontSize: fontSize.caption }}>
              {t("create.myImageDevReady")}
            </Typography.Text>
          )}
          {purpose === "service" && (selected.exposed_ports ?? []).length > 0 && (
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {t("create.myImagePorts", { ports: (selected.exposed_ports ?? []).join(", ") })}
            </Typography.Text>
          )}
        </Space>
      )}
    </Space>
  );
}
