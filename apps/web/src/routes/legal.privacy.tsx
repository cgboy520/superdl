/** Privacy policy (public): body from the backend's current published version. */

import { createFileRoute } from "@tanstack/react-router";

import { LegalDocPage } from "../features/legal/LegalDocPage";

export const Route = createFileRoute("/legal/privacy")({
  component: () => <LegalDocPage docKey="privacy" />,
});
