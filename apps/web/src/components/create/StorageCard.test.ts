/** Which disks a new workload mounts, and when the create form still offers a /data disk. */
import type { DiskOut } from "@superdl/api-client";
import { describe, expect, it } from "vitest";

import { hasDataDisk, mountedDisks } from "./StorageCard";

const disk = (mount_path: string, status: string) => ({ mount_path, status }) as DiskOut;

describe("mounted disks", () => {
  it("active and grace disks mount; frozen, deleting and deleted ones do not", () => {
    const disks = [disk("/data", "active"), disk("/x", "grace"), disk("/y", "frozen"), disk("/z", "deleting")];
    expect(mountedDisks(disks).map((d) => d.mount_path)).toEqual(["/data", "/x"]);
  });
  it("offers a new /data disk only while no disk but a deleted one holds the path (the API refuses a taken path)", () => {
    expect(hasDataDisk([disk("/data", "frozen")])).toBe(true);
    expect(hasDataDisk([disk("/data", "deleted"), disk("/data2", "active")])).toBe(false);
    expect(hasDataDisk(undefined)).toBe(false);
  });
});
