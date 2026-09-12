/** HexTag 渲染回归:暗色主题下档位/竞价/包周期 Tag 底色必须是 token 原值,不被 antd dark algorithm 调亮。 */
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

/** 未过期包月订阅(colorPrimary 分支) */
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

describe("HexTag 替换后的 Tag 渲染", () => {
  it("暗色主题:TierTag/SpotTag/SubscriptionTag 底色不被 algorithm 调亮,白字", () => {
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
    expect(styles[0]?.backgroundColor).toBe("rgb(79, 70, 229)"); // skuTierMap.dedicated #4F46E5
    expect(styles[1]?.backgroundColor).toBe("rgb(194, 65, 12)"); // statusColors.orange #C2410C
    expect(styles[2]?.backgroundColor).toBe("rgb(79, 70, 229)"); // colorPrimary
    for (const s of styles) {
      expect(s.color).toBe("rgb(255, 255, 255)");
    }
  });

  it("已过期的包周期标记转橙(statusColors.orange)", () => {
    const expired: InstanceSubscriptionOut = { ...SUB, expires_at: "2020-01-01T00:00:00Z" };
    const { container } = renderThemed(<SubscriptionTag market="subscription" subscription={expired} />, true);
    const tag = container.querySelector(".ant-tag");
    expect(tag).not.toBeNull();
    expect(getComputedStyle(tag as Element).backgroundColor).toBe(
      // statusColors.orange 原值,暗色下不调亮
      `rgb(${[194, 65, 12].join(", ")})`,
    );
  });
});
