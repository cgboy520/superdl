/** Chunked, resumable upload of a docker save archive through the generated fetchers: each chunk carries its sha256, the server's bitmap says which ones are still missing, and chunk 0 always goes first so a resumed upload proves it is the same file. */

import {
  completeUploadApiV1CustomImagesUuidCompletePost,
  isApiError,
  putChunkApiV1CustomImagesUuidChunksIndexPut,
} from "@superdl/api-client";
import type { CustomImageOut } from "@superdl/api-client";

/** Must equal the server's chunk size (CustomImageOut.chunk_bytes); chunk 0's sha256 is sent before the session exists. */
export const IMAGE_CHUNK_BYTES = 8 * 1024 * 1024;
const PARALLEL = 3;
const ATTEMPTS = 4;

export const ARCHIVE_ACCEPT = ".tar,.tar.gz,.tgz,.zip";
const ARCHIVE_SUFFIXES = [".tar.gz", ".tgz", ".tar", ".zip"] as const;

export function isArchiveName(name: string): boolean {
  const lower = name.toLowerCase();
  return ARCHIVE_SUFFIXES.some((s) => lower.endsWith(s));
}

/** File name → a repository name the API accepts (lowercase, single . _ - separators), "image" when nothing is left. */
export function repoNameOf(fileName: string): string {
  let base = fileName.toLowerCase();
  for (const s of ARCHIVE_SUFFIXES) if (base.endsWith(s)) base = base.slice(0, -s.length);
  const cleaned = base
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/[._-]{2,}/g, "-")
    .replace(/^[._-]+|[._-]+$/g, "")
    .slice(0, 64)
    .replace(/[._-]+$/g, "");
  return cleaned || "image";
}

export async function sha256Hex(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function firstChunk(file: Blob): Blob {
  return file.slice(0, IMAGE_CHUNK_BYTES);
}

/** Indexes the server has not received; bit i of the base64 bitmap (least significant first in each byte) is chunk i. */
export function missingChunks(image: Pick<CustomImageOut, "received_bitmap" | "chunks_total">): number[] {
  const raw = image.received_bitmap ? atob(image.received_bitmap) : "";
  const missing: number[] = [];
  for (let i = 0; i < image.chunks_total; i++) {
    const byte = i >> 3 < raw.length ? raw.charCodeAt(i >> 3) : 0;
    if (((byte >> (i & 7)) & 1) === 0) missing.push(i);
  }
  return missing;
}

function chunkLength(size: number, index: number): number {
  return Math.max(0, Math.min(IMAGE_CHUNK_BYTES, size - index * IMAGE_CHUNK_BYTES));
}

/** Network failures, 5xx and 429 are retried; any other refusal (mismatch, expiry, quota) is final. */
function transient(err: unknown): boolean {
  if (!isApiError(err)) return false;
  return err.status === 0 || err.status === 429 || err.status >= 500;
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(signal.reason as Error);
      },
      { once: true },
    );
  });
}

async function putWithRetry(uuid: string, index: number, blob: Blob, signal: AbortSignal): Promise<void> {
  const sha = await sha256Hex(blob);
  for (let attempt = 1; ; attempt++) {
    signal.throwIfAborted();
    try {
      await putChunkApiV1CustomImagesUuidChunksIndexPut(uuid, index, blob, { "x-chunk-sha256": sha }, { signal });
      return;
    } catch (err) {
      if (attempt >= ATTEMPTS || !transient(err)) throw err;
      await pause(1000 * 2 ** (attempt - 1), signal);
    }
  }
}

export interface UploadProgress {
  sentBytes: number;
  totalBytes: number;
}

/** The client and server disagree on the chunk size: the page is older than the API and must be reloaded. */
export class ChunkSizeMismatch extends Error {}

/** Chunk 0 first (re-sent when already received, so the server can refuse a different file), then the rest PARALLEL at a time; the first final failure stops the others and is thrown. */
export async function sendChunks(
  file: File,
  image: CustomImageOut,
  opts: { signal: AbortSignal; onProgress: (p: UploadProgress) => void },
): Promise<void> {
  if (image.chunk_bytes !== IMAGE_CHUNK_BYTES) throw new ChunkSizeMismatch("chunk size changed");
  const missing = missingChunks(image);
  let sent = file.size - missing.reduce((acc, i) => acc + chunkLength(file.size, i), 0);
  if (missing[0] !== 0) sent -= chunkLength(file.size, 0);
  opts.onProgress({ sentBytes: sent, totalBytes: file.size });
  const stop = new AbortController();
  const onAbort = () => stop.abort(opts.signal.reason);
  opts.signal.addEventListener("abort", onAbort, { once: true });
  const send = async (index: number) => {
    const blob = file.slice(index * IMAGE_CHUNK_BYTES, (index + 1) * IMAGE_CHUNK_BYTES);
    await putWithRetry(image.uuid, index, blob, stop.signal);
    sent += blob.size;
    opts.onProgress({ sentBytes: sent, totalBytes: file.size });
  };
  try {
    await send(0);
    const queue = missing.filter((i) => i !== 0);
    const worker = async () => {
      for (let index = queue.shift(); index !== undefined; index = queue.shift()) await send(index);
    };
    await Promise.all(
      Array.from({ length: Math.min(PARALLEL, queue.length) }, () =>
        worker().catch((err: unknown) => {
          stop.abort(err);
          throw err;
        }),
      ),
    );
  } finally {
    opts.signal.removeEventListener("abort", onAbort);
  }
}

/** Send what is missing, then close the upload: the import starts on the server. */
export async function uploadArchive(
  file: File,
  image: CustomImageOut,
  opts: { signal: AbortSignal; onProgress: (p: UploadProgress) => void },
): Promise<CustomImageOut> {
  await sendChunks(file, image, opts);
  opts.signal.throwIfAborted();
  return completeUploadApiV1CustomImagesUuidCompletePost(image.uuid, { signal: opts.signal });
}
