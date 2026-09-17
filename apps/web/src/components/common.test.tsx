/** HexTag rendering regression: tier / spot / subscription Tag backgrounds in dark mode must be the raw token values, not lightened by the antd dark algorithm. */
import type { InstanceSubscriptionOut } from "@superdl/api-client";
import { webDarkTheme, webTheme } from "@superdl/ui";
import { ConfigProvider, theme as antdTheme } from "antd";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { SpotTag, SubscriptionTag, TierTag } from "./common";

function renderThemed(node: ReactElement, dark: boolean) {
  return render(
    <ConfigProvider theme={dark ? { algorithm: antdTheme.darkAlgorithm, ...webDarkTheme } : webTheme}>
      {node}
    </ConfigProvider>,
  );
}

/** Unexpired monthly subscription (colorPrimary branch) */
const SUB: InstanceSubscriptionOut = {
  period: "month",
  period_count: 1,
  started_at: "2026-08-01T00:00:00Z",
  expires_at: "2099-01-01T00:00:00Z",
  status: "active",
  auto_renew: false,
  amount_paid: "100.00",
  unit_price: "1.0000",
};

describe("Tag rendering after the HexTag replacement", () => {
  it("dark theme: TierTag/SpotTag/SubscriptionTag backgrounds are not lightened by the algorithm, white text", () => {
    const { container } = renderThemed(
      <>
        <TierTag tier="dedicated" pool={null} />
        <SpotTag market="spot" />
        <SubscriptionTag market="subscription" subscription={SUB} />
      </>,
      true,
    );
    const styles = [...container.querySelectorAll(".ant-tag")].map((el) => getComputedStyle(el));
    expect(styles).toHaveLength(3);
    expect(styles[0]?.backgroundColor).toBe("rgb(79, 70, 229)");
    expect(styles[1]?.backgroundColor).toBe("rgb(194, 65, 12)");
    expect(styles[2]?.backgroundColor).toBe("rgb(79, 70, 229)");
    for (const s of styles) {
      expect(s.color).toBe("rgb(255, 255, 255)");
    }
  });

  it("an expired subscription marker turns orange (statusColors.orange)", () => {
    const expired: InstanceSubscriptionOut = { ...SUB, expires_at: "2020-01-01T00:00:00Z" };
    const { container } = renderThemed(<SubscriptionTag market="subscription" subscription={expired} />, true);
    const tag = container.querySelector(".ant-tag");
    expect(tag).not.toBeNull();
    expect(getComputedStyle(tag as Element).backgroundColor).toBe(`rgb(${[194, 65, 12].join(", ")})`);
  });
});
