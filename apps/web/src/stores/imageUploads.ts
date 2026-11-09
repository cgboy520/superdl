/** Archive uploads running in this tab, by image uuid: the chunk loop outlives route changes; a reload drops the File and the images page offers to resume by picking it again. */

import type { CustomImageOut } from "@superdl/api-client";
import { createStore } from "zustand/vanilla";
import { useStore } from "zustand";

import { uploadArchive } from "../api/imageUpload";

export type UploadPhase = "sending" | "completing" | "done" | "failed" | "cancelled";

export interface UploadState {
  uuid: string;
  fileName: string;
  sentBytes: number;
  totalBytes: number;
  phase: UploadPhase;
  error: unknown;
}

const controllers = new Map<string, AbortController>();

export const imageUploadStore = createStore<{
  uploads: Record<string, UploadState>;
  patch: (uuid: string, change: Partial<UploadState>) => void;
  clear: (uuid: string) => void;
}>()((set) => ({
  uploads: {},
  patch: (uuid, change) =>
    set((s) => {
      const current = s.uploads[uuid];
      if (!current) return s;
      return { uploads: { ...s.uploads, [uuid]: { ...current, ...change } } };
    }),
  clear: (uuid) =>
    set((s) => {
      const { [uuid]: _gone, ...rest } = s.uploads;
      return { uploads: rest };
    }),
}));

export const isUploadActive = (u: UploadState | undefined) => u?.phase === "sending" || u?.phase === "completing";

/** Start (or resume) sending `file` into the upload session `image`; onSettled runs after completion or failure (refresh queries there). */
export function startImageUpload(file: File, image: CustomImageOut, onSettled: () => void): void {
  controllers.get(image.uuid)?.abort();
  const controller = new AbortController();
  controllers.set(image.uuid, controller);
  imageUploadStore.setState((s) => ({
    uploads: {
      ...s.uploads,
      [image.uuid]: {
        uuid: image.uuid,
        fileName: file.name,
        sentBytes: 0,
        totalBytes: file.size,
        phase: "sending",
        error: null,
      },
    },
  }));
  const { patch } = imageUploadStore.getState();
  void uploadArchive(file, image, {
    signal: controller.signal,
    onProgress: ({ sentBytes, totalBytes }) =>
      patch(image.uuid, {
        sentBytes,
        totalBytes,
        phase: sentBytes >= totalBytes ? "completing" : "sending",
      }),
  })
    .then(() => patch(image.uuid, { phase: "done" }))
    .catch((error: unknown) =>
      patch(image.uuid, controller.signal.aborted ? { phase: "cancelled" } : { phase: "failed", error }),
    )
    .finally(() => {
      if (controllers.get(image.uuid) === controller) controllers.delete(image.uuid);
      onSettled();
    });
}

export function cancelImageUpload(uuid: string): void {
  controllers.get(uuid)?.abort();
}

export function useImageUploads(): Record<string, UploadState> {
  return useStore(imageUploadStore, (s) => s.uploads);
}

/** Leaving the tab mid-upload asks first (the browser shows its own wording). */
if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", (event) => {
    if (Object.values(imageUploadStore.getState().uploads).some(isUploadActive)) event.preventDefault();
  });
}
