/** Data disks: default names and mount points, with the API's mount-path rules (disks.mountPathInvalid / mountPathTaken) applied before submit. */

export const DEFAULT_MOUNT_PATH = "/data";
/** Tenant storage mounts live under this directory; data disks cannot take it. */
export const ORG_MOUNT_ROOT = "/mnt/org";

const MOUNT_PATH_RE =
  /^\/(?:data[A-Za-z0-9._-]{0,32}|workspace[A-Za-z0-9._-]{0,32}|mnt\/(?!org$)[A-Za-z0-9][A-Za-z0-9._-]{0,63})$/;

export function isValidMountPath(path: string): boolean {
  return MOUNT_PATH_RE.test(path);
}

/** /data when free, else the first free /data2, /data3, … */
export function suggestMountPath(taken: readonly string[]): string {
  const used = new Set(taken);
  if (!used.has(DEFAULT_MOUNT_PATH)) return DEFAULT_MOUNT_PATH;
  for (let n = 2; ; n++) {
    const candidate = `${DEFAULT_MOUNT_PATH}${n}`;
    if (!used.has(candidate)) return candidate;
  }
}

export function defaultDiskName(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `data-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
}
