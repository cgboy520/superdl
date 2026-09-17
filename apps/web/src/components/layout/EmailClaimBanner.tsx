/** Accounts created before email became the primary handle have no address yet: prompt them to
 *  add one (sign-in and password recovery depend on it). */

import { space } from "@superdl/ui";
import { useNavigate } from "@tanstack/react-router";
import { Alert, Button } from "antd";
import { useTranslation } from "react-i18next";

import { useMe } from "../../api/queries";
import { useIsLoggedIn } from "../../stores/auth";

export function EmailClaimBanner() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const loggedIn = useIsLoggedIn();
  const { data: me } = useMe({ enabled: loggedIn });
  if (!me || me.email) return null;
  return (
    <Alert
      type="warning"
      showIcon
      title={t("emailClaim.title")}
      description={t("emailClaim.desc")}
      action={
        <Button size="small" onClick={() => void navigate({ to: "/settings", search: { tab: "account" } })}>
          {t("emailClaim.action")}
        </Button>
      }
      style={{ marginBottom: space.lg }}
    />
  );
}
