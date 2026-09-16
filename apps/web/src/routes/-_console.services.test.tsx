/** URL state round trip of the three service routes: invalid values must be stripped back to defaults (no legacy-name compatibility in the URL). */
import { describe, expect, it } from "vitest";

import { parseDeployDeepLink } from "../lib/deployLink";
import { servicesValidateSearch } from "./_console.services";
import { serviceDetailValidateSearch } from "./_console.services_.$slug";

describe("servicesValidateSearch", () => {
  it("status accepts only the derived-status allow-list without released; blank q is stripped", () => {
    expect(servicesValidateSearch({ status: "running", q: " qwen " })).toEqual({
      status: "running",
      q: " qwen ",
    });
    expect(servicesValidateSearch({ status: "released" })).toEqual({});
    expect(servicesValidateSearch({ status: "creating", q: "   " })).toEqual({});
  });
});

describe("serviceDetailValidateSearch", () => {
  it("the six valid tabs are kept; legacy names (revisions/events/bills/service) and unknown values fall back to the default", () => {
    for (const tab of ["overview", "keys", "metrics", "logs", "history", "settings"]) {
      expect(serviceDetailValidateSearch({ tab })).toEqual({ tab });
    }
    expect(serviceDetailValidateSearch({ tab: "revisions" })).toEqual({});
    expect(serviceDetailValidateSearch({ tab: "events" })).toEqual({});
    expect(serviceDetailValidateSearch({ tab: "bills" })).toEqual({});
    expect(serviceDetailValidateSearch({ tab: "service" })).toEqual({});
    expect(serviceDetailValidateSearch({ tab: "xyz" })).toEqual({});
  });
});

describe("parseDeployDeepLink", () => {
  it("sku_id positive integer, gpus 1–8, period wins over market=spot, count only in subscription modes", () => {
    expect(parseDeployDeepLink({ sku_id: "3", gpus: "2", period: "month", market: "spot", count: "6" })).toEqual({
      sku_id: 3,
      gpus: 2,
      period: "month",
      count: 6,
    });
    expect(parseDeployDeepLink({ sku_id: "0", gpus: "9", market: "spot", count: "6" })).toEqual({
      market: "spot",
    });
    expect(parseDeployDeepLink({ period: "quarter" })).toEqual({});
  });
});
