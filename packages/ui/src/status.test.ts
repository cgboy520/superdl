/** Regression of the service transitional set and the isServiceStatus allow-list. */
import { describe, expect, it } from "vitest";

import {
  isNodeStatus,
  isServiceStatus,
  isTransientServiceStatus,
  serviceStatusMap,
  SEVERITY_ORDER,
  severityMap,
} from "./status";

describe("serviceStatusMap", () => {
  it("transitional states are only deploying / stopping / releasing; unready may never become ready and is not transitional", () => {
    const transient = Object.keys(serviceStatusMap).filter(isTransientServiceStatus);
    expect(transient).toEqual(["deploying", "stopping", "releasing"]);
    expect(isTransientServiceStatus("unready")).toBe(false);
  });

  it("isServiceStatus accepts only table values (the ?status= allow-list on the URL relies on it)", () => {
    expect(isServiceStatus("running")).toBe(true);
    expect(isServiceStatus("creating")).toBe(false);
    expect(isServiceStatus("toString")).toBe(false);
    expect(isServiceStatus(1)).toBe(false);
  });
});

describe("nodeStatusMap / severityMap", () => {
  it("isNodeStatus accepts only table values (the /nodes?status= allow-list relies on it)", () => {
    expect(isNodeStatus("Cordoned")).toBe(true);
    expect(isNodeStatus("ready")).toBe(false);
    expect(isNodeStatus(null)).toBe(false);
  });

  it("SEVERITY_ORDER covers exactly the severityMap keys", () => {
    expect([...SEVERITY_ORDER].sort()).toEqual(Object.keys(severityMap).sort());
  });
});
