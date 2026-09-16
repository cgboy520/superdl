/** Form drafts (sessionStorage). Non-sensitive creation forms only; passwords / credentials / payment fields are banned; clear after a successful submit. */

import { useMemo } from "react";

export interface FormDraft<T extends object> {
  /** Read the draft (undefined when missing or corrupt) */
  load: () => Partial<T> | undefined;
  /** Overwrite */
  save: (values: Partial<T>) => void;
  clear: () => void;
}

export function useFormDraft<T extends object>(key: string): FormDraft<T> {
  const storageKey = `superdl-form-draft:${key}`;
  return useMemo(
    () => ({
      load: () => {
        try {
          const raw = sessionStorage.getItem(storageKey);
          return raw ? (JSON.parse(raw) as Partial<T>) : undefined;
        } catch {
          return undefined;
        }
      },
      save: (values: Partial<T>) => {
        try {
          sessionStorage.setItem(storageKey, JSON.stringify(values));
        } catch {
          /* ignored */
        }
      },
      clear: () => {
        sessionStorage.removeItem(storageKey);
      },
    }),
    [storageKey],
  );
}
