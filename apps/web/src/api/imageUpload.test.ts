/** Chunked upload: bitmap decoding, name derivation, chunk 0 first on resume, transient retries and the stop on a final refusal. */
import type { CustomImageOut } from "@superdl/api-client";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { IMAGE_CHUNK_BYTES, missingChunks, repoNameOf, sendChunks } from "./imageUpload";

const { putChunk } = vi.hoisted(() => ({ putChunk: vi.fn() }));

vi.mock("@superdl/api-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@superdl/api-client")>()),
  putChunkApiV1CustomImagesUuidChunksIndexPut: putChunk,
}));

const bitmap = (bits: number[], total: number) => {
  const bytes = new Uint8Array(Math.ceil(total / 8));
  for (const i of bits) bytes[i >> 3] = (bytes[i >> 3] ?? 0) | (1 << (i & 7));
  return btoa(String.fromCharCode(...bytes));
};

const image = (total: number, received: number[]): CustomImageOut =>
  ({
    uuid: "u1",
    chunk_bytes: IMAGE_CHUNK_BYTES,
    chunks_total: total,
    received_bitmap: bitmap(received, total),
  }) as CustomImageOut;

/** A file of `chunks` chunks without allocating them: slice() is all the uploader touches. */
const fakeFile = (chunks: number) => {
  const size = (chunks - 1) * IMAGE_CHUNK_BYTES + 10;
  return {
    size,
    slice: (start: number, end: number) => new Blob([new Uint8Array(Math.max(0, Math.min(end, size) - start))]),
  } as unknown as File;
};

beforeEach(() => {
  putChunk.mockReset();
});

describe("missingChunks", () => {
  it("reads bit i of byte i/8, least significant bit first (PostgreSQL set_bit order)", () => {
    expect(missingChunks(image(10, [0, 1, 8]))).toEqual([2, 3, 4, 5, 6, 7, 9]);
    expect(missingChunks({ received_bitmap: null, chunks_total: 3 })).toEqual([0, 1, 2]);
  });
});

describe("repoNameOf", () => {
  it("strips the archive suffix and folds what the registry refuses", () => {
    expect(repoNameOf("My Env_v2.tar.gz")).toBe("my-env_v2");
    expect(repoNameOf("--a..b--.zip")).toBe("a-b");
    expect(repoNameOf("镜像.tar")).toBe("image"); // cjk-ok
  });
});

describe("sendChunks", () => {
  it("re-sends chunk 0 before anything else on resume and skips received chunks", async () => {
    putChunk.mockResolvedValue({ chunks_received: 0, chunks_total: 4 });
    await sendChunks(fakeFile(4), image(4, [0, 2]), { signal: new AbortController().signal, onProgress: vi.fn() });
    const sent = putChunk.mock.calls.map((c) => c[1] as number);
    expect(sent[0]).toBe(0);
    expect(sent.slice(1).sort()).toEqual([1, 3]);
  });

  it("retries a network failure, then succeeds", async () => {
    vi.useFakeTimers();
    putChunk
      .mockRejectedValueOnce({ code: "NETWORK_ERROR", message: "", status: 0 })
      .mockResolvedValue({ chunks_received: 1, chunks_total: 1 });
    const done = sendChunks(fakeFile(1), image(1, []), { signal: new AbortController().signal, onProgress: vi.fn() });
    await vi.waitFor(() => expect(putChunk).toHaveBeenCalledTimes(2), { timeout: 5000, interval: 250 });
    await done;
    vi.useRealTimers();
  });

  it("a final refusal is thrown without retrying", async () => {
    const refusal = { code: "VALIDATION_ERROR", message: "", status: 422, message_key: "customImages.differentFile" };
    putChunk.mockRejectedValue(refusal);
    await expect(
      sendChunks(fakeFile(3), image(3, []), { signal: new AbortController().signal, onProgress: vi.fn() }),
    ).rejects.toBe(refusal);
    expect(putChunk).toHaveBeenCalledTimes(1);
  });
});
