/** Deletion notice (public): body from the backend's current published version; the account deletion dialog links here. */

import { createFileRoute } from "@tanstack/react-router";

import { LegalDocPage } from "../features/legal/LegalDocPage";

export const Route = createFileRoute("/legal/deletion-notice")({
  component: () => <LegalDocPage docKey="deletion_notice" />,
});
