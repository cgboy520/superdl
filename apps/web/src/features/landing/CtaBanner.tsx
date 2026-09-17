/** CTA banner: live idle card count, degrading to a static slogan when unavailable. */

import { brand, brandInverseButtonStyle, layout, space } from "@superdl/ui";
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
    <section
      style={{
        background: brand.heroBg,
        padding: `${layout.sectionPaddingY}px ${layout.contentPadding}px`,
        textAlign: "center",
      }}
    >
      <Typography.Title level={3} style={{ color: brand.onHero, marginTop: 0, marginBottom: space.xl }}>
        {text}
      </Typography.Title>
      <Link to={loggedIn ? "/market" : "/login"} search={loggedIn ? {} : { mode: "register" }}>
        <Button size="large" style={{ ...brandInverseButtonStyle, paddingInline: space.xxl }}>
          {loggedIn ? t("common.goRent") : t("landing.cta.button")}
        </Button>
      </Link>
    </section>
  );
}
