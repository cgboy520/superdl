/** CTA 横幅:实时空闲卡数(取不到退化为静态口号),不编造数字。 */

import { brand, colorPrimary, marketing } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { Button, Typography } from "antd";

import { useSkus } from "../../api/queries";
import { useIsLoggedIn } from "../../stores/auth";

export function CtaBanner() {
  const loggedIn = useIsLoggedIn();
  const { data: skus } = useSkus();
  const freeCards = (skus ?? []).reduce((sum, s) => sum + (s.available_count ?? 0), 0);
  const text = freeCards > 0 ? marketing.ctaBanner.withStock(freeCards) : marketing.ctaBanner.fallback;

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
          {loggedIn ? "去租用" : marketing.ctaBanner.button}
        </Button>
      </Link>
    </section>
  );
}
