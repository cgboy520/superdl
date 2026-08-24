/** 用户协议(公开,F7):正文来自后端当前 published 版。 */

import { createFileRoute } from "@tanstack/react-router";

import { LegalDocPage } from "../features/legal/LegalDocPage";

export const Route = createFileRoute("/legal/terms")({
  component: () => <LegalDocPage docKey="terms" />,
});
