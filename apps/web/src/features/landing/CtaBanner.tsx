/** CTA 横幅:实时空闲卡数,取不到则退化为静态口号。 */

import { brand, colorPrimary } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { Button, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useSkus } from "../../api/queries";
import { dedupAvailableTotal } from "../../lib/inventory";
import { useIsLoggedIn } from "../../stores/auth";

export function CtaBanner() {
  const { t } = useTranslation();
  const loggedIn = useIsLoggedIn();
  const { data: skus } = useSkus();
  const freeCards = dedupAvailableTotal(skus ?? []);
  const text = freeCards > 0 ? t("landing.cta.withStock", { count: freeCards }) : t("landing.cta.fallback");

  return (
    <section style={{ background: brand.heroBg, padding: "48px 24px", textAlign: "center" }}>
      <Typography.Title level={3} style={{ color: "#fff", marginTop: 0, marginBottom: 24 }}>
        {text}
      </Typography.Title>
      <Link to={loggedIn ? "/market" : "/login"}>
        <Button
          size="large"
          style={{
            background: "#fff",
            color: colorPrimary,
            borderColor: "transparent",
            fontWeight: 600,
            paddingInline: 40,
          }}
        >
          {loggedIn ? t("common.goRent") : t("landing.cta.button")}
        </Button>
      </Link>
    </section>
  );
}
