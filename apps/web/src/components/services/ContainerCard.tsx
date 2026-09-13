/** 容器配置段:镜像地址(钉版本)/ 启动命令 / 启动参数 / 环境变量(含密文)。部署页与创建页共用;镜像可变 tag 即时红框(后端是硬闸)。 */

import { Input, Space } from "antd";
import { useTranslation } from "react-i18next";

import { isPinnedImageRef, type ArgRow, type EnvRow } from "../../lib/serviceSpec";
import { Field } from "../Field";
import { ArgRowsEditor } from "../create/ArgRowsEditor";
import { EnvRowsEditor } from "../create/EnvRowsEditor";
import { space } from "@superdl/ui";

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
}: {
  image: string;
  onImage: (v: string) => void;
  command: string;
  onCommand: (v: string) => void;
  argRows: ArgRow[];
  onArgRows: (rows: ArgRow[]) => void;
  envRows: EnvRow[];
  onEnvRows: (rows: EnvRow[]) => void;
  /** 更新版本抽屉:环境变量折叠为「N 个变量 · 展开编辑」 */
  collapsibleEnv?: boolean;
}) {
  const { t } = useTranslation();
  const { t: tErr } = useTranslation("errors");
  const pinned = image.trim() === "" || isPinnedImageRef(image.trim());
  return (
    <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
      <Field
        label={t("services.form.imageLabel")}
        required
        error={pinned ? undefined : tErr("orchestrator.imageRefNotPinned")}
        hint={t("copy.serviceImagePinned")}
      >
        <Input
          placeholder="registry.example.com/your/image:v1.2.0"
          aria-label={t("services.form.imageLabel")}
          value={image}
          onChange={(e) => onImage(e.target.value)}
          status={pinned ? undefined : "error"}
          className="mono"
        />
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
