/** Container configuration section: image (a pinned registry address or one of the user's uploaded images) / command / arguments / environment variables (secrets included). Shared by the deploy page and the revision drawer; a mutable image tag gets an immediate red frame (the backend is the hard gate). */

import { Input, Segmented, Space } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useCustomImages } from "../../api/queries";
import { isPinnedImageRef, type ArgRow, type EnvRow } from "../../lib/serviceSpec";
import { Field } from "../Field";
import { ArgRowsEditor } from "../create/ArgRowsEditor";
import { EnvRowsEditor } from "../create/EnvRowsEditor";
import { MyImagePicker } from "../create/MyImagePicker";
import { space } from "@superdl/ui";

type ImageSource = "registry" | "mine";

export function ContainerFields({
  image,
  onImage,
  command,
  onCommand,
  argRows,
  onArgRows,
  envRows,
  onEnvRows,
  collapsibleEnv,
  archs,
}: {
  image: string;
  onImage: (v: string) => void;
  command: string;
  onCommand: (v: string) => void;
  argRows: ArgRow[];
  onArgRows: (rows: ArgRow[]) => void;
  envRows: EnvRow[];
  onEnvRows: (rows: EnvRow[]) => void;
  /** Revision drawer: environment variables collapsed to "N variables · expand to edit" */
  collapsibleEnv?: boolean;
  /** CPU architectures of the chosen spec's nodes (flags an uploaded image built for another one). */
  archs?: readonly string[];
}) {
  const { t } = useTranslation();
  const { t: tErr } = useTranslation("errors");
  const customQ = useCustomImages();
  const [picked, setPicked] = useState<ImageSource>();
  const isUploaded = image.trim() !== "" && (customQ.data ?? []).some((i) => i.image_ref === image.trim());
  const source: ImageSource = picked ?? (isUploaded ? "mine" : "registry");
  const pinned = image.trim() === "" || isPinnedImageRef(image.trim());
  return (
    <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
      <Field
        label={t("services.form.imageLabel")}
        required
        error={source === "registry" && !pinned ? tErr("orchestrator.imageRefNotPinned") : undefined}
        hint={source === "registry" ? t("copy.serviceImagePinned") : undefined}
      >
        <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
          <Segmented<ImageSource>
            value={source}
            onChange={(v) => {
              setPicked(v);
              onImage("");
            }}
            options={[
              { value: "registry", label: t("services.form.imageFromRegistry") },
              { value: "mine", label: t("services.form.imageFromUploads") },
            ]}
          />
          {source === "registry" ? (
            <Input
              placeholder="registry.example.com/your/image:v1.2.0"
              aria-label={t("services.form.imageLabel")}
              value={image}
              onChange={(e) => onImage(e.target.value)}
              status={pinned ? undefined : "error"}
              className="mono"
            />
          ) : (
            <MyImagePicker
              value={image.trim() || undefined}
              onChange={(ref) => onImage(ref ?? "")}
              purpose="service"
              {...(archs ? { archs } : {})}
            />
          )}
        </Space>
      </Field>
      <Field label={t("services.form.commandLabel")} hint={t("services.form.commandHint")}>
        <Input
          placeholder={t("services.form.commandPlaceholder")}
          aria-label={t("services.form.commandLabel")}
          value={command}
          onChange={(e) => onCommand(e.target.value)}
          className="mono"
        />
      </Field>
      <ArgRowsEditor rows={argRows} onChange={onArgRows} />
      <EnvRowsEditor rows={envRows} onChange={onEnvRows} collapsible={collapsibleEnv} />
    </Space>
  );
}
