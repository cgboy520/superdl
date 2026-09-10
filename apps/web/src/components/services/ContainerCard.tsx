/** 容器配置段:镜像地址(钉版本)/ 启动命令 / 启动参数 / 环境变量(含密文)。
 *  部署页与创建页的服务形态共用;镜像可变 tag 即时红框(后端是硬闸,这里提前报错)。 */

import { Input, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { isPinnedImageRef, type ArgRow, type EnvRow } from "../../lib/serviceSpec";
import { ArgRowsEditor } from "../create/ArgRowsEditor";
import { EnvRowsEditor } from "../create/EnvRowsEditor";

export function ContainerFields({
  image,
  onImage,
  command,
  onCommand,
  argRows,
  onArgRows,
  envRows,
  onEnvRows,
}: {
  image: string;
  onImage: (v: string) => void;
  command: string;
  onCommand: (v: string) => void;
  argRows: ArgRow[];
  onArgRows: (rows: ArgRow[]) => void;
  envRows: EnvRow[];
  onEnvRows: (rows: EnvRow[]) => void;
}) {
  const { t } = useTranslation();
  const { t: tErr } = useTranslation("errors");
  const pinned = image.trim() === "" || isPinnedImageRef(image.trim());
  return (
    <Space orientation="vertical" size={12} style={{ width: "100%" }}>
      <Space orientation="vertical" size={4} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("services.form.imageLabel")}</Typography.Text>
        <Input
          placeholder="registry.example.com/your/image:v1.2.0"
          aria-label={t("services.form.imageLabel")}
          value={image}
          onChange={(e) => onImage(e.target.value)}
          status={pinned ? undefined : "error"}
        />
        {!pinned && (
          <Typography.Text type="danger">{tErr("orchestrator.imageRefNotPinned")}</Typography.Text>
        )}
        <Typography.Text type="secondary">{t("copy.serviceImagePinned")}</Typography.Text>
      </Space>
      <Space orientation="vertical" size={4} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("services.form.commandLabel")}</Typography.Text>
        <Input
          placeholder={t("services.form.commandPlaceholder")}
          aria-label={t("services.form.commandLabel")}
          value={command}
          onChange={(e) => onCommand(e.target.value)}
        />
        <Typography.Text type="secondary">{t("services.form.commandHint")}</Typography.Text>
      </Space>
      <ArgRowsEditor rows={argRows} onChange={onArgRows} />
      <EnvRowsEditor rows={envRows} onChange={onEnvRows} />
    </Space>
  );
}
