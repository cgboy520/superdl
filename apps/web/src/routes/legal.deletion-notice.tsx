/** 数据删除说明(公开,F7):正文来自后端当前 published 版;F4 注销弹窗链接至此。 */

import { createFileRoute } from "@tanstack/react-router";

import { LegalDocPage } from "../features/legal/LegalDocPage";

export const Route = createFileRoute("/legal/deletion-notice")({
  component: () => <LegalDocPage docKey="deletion_notice" />,
});
