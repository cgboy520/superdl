/** Terms of service (public): body from the backend's current published version. */

import { createFileRoute } from "@tanstack/react-router";

import { LegalDocPage } from "../features/legal/LegalDocPage";

export const Route = createFileRoute("/legal/terms")({
  component: () => <LegalDocPage docKey="terms" />,
});
