/** 表单草稿(sessionStorage)。仅非敏感新建表单;密码/凭据/支付字段禁入;提交成功后 clear。 */

import { useMemo } from "react";

export interface FormDraft<T extends object> {
  /** 读出草稿(无或损坏 → undefined) */
  load: () => Partial<T> | undefined;
  /** 覆盖写 */
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
