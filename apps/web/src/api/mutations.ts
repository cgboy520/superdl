/**
 * 写操作统一封装:生成的 fetcher 函数 + useMutation。
 * 成功后按 invalidates 声明的域失效相关查询;未声明时退化为全站广播。
 */

import type { ApiError,
  SshKeyOut,
} from "@superdl/api-client";
import {
  addSshKeyApiV1SshKeysPost,
  createDiskApiV1DisksPost,
  createInstanceApiV1InstancesPost,
  createRechargeApiV1WalletRechargesPost,
  deleteDiskApiV1DisksUuidDelete,
  deleteSshKeyApiV1SshKeysKeyIdDelete,
  expandDiskApiV1DisksUuidPatch,
  loginApiV1AuthLoginPost,
  logoutApiV1AuthLogoutPost,
  markReadApiV1NotificationsNotificationIdReadPost,
  mockWebhookApiV1WebhooksMockPost,
  registerApiV1AuthRegisterPost,
  resetPasswordApiV1AuthPasswordResetPost,
  releaseInstanceApiV1InstancesUuidDelete,
  renameInstanceApiV1InstancesUuidPatch,
  resetJupyterTokenApiV1InstancesUuidResetJupyterTokenPost,
  restartInstanceApiV1InstancesUuidRestartPost,
  sendSmsCodeApiV1AuthSmsCodePost,
  setWarnThresholdApiV1MeWarnThresholdPatch,
  submitRealNameApiV1MeRealNamePost,
  startInstanceApiV1InstancesUuidStartPost,
  stopInstanceApiV1InstancesUuidStopPost,
} from "@superdl/api-client";
import type {
  DiskCreate,
  DiskExpand,
  InstanceCreate,
  LoginRequest,
  RealNameRequest,
  RechargeCreate,
  PasswordResetRequest,
  RegisterRequest,
  SmsCodeRequest,
} from "@superdl/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";
import { useCallback } from "react";

import { useApiErrorText } from "../lib/apiError";
import { authStore, readTokens } from "../stores/auth";

interface MutationOpts<TData> {
  onSuccess?: (data: TData) => void;
  silentError?: boolean;
  /** 成功后失效的查询键前缀;缺省 = 全站广播(仅用于影响面确实全站的操作) */
  invalidates?: readonly string[];
}

export function useApiMutation<TVars, TData>(
  fn: (vars: TVars) => Promise<TData>,
  opts?: MutationOpts<TData>,
) {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const errText = useApiErrorText();
  return useMutation<TData, ApiError, TVars>({
    mutationFn: fn,
    onSuccess: (data) => {
      // 不 await:失效是广播式的,await 会让「改名成功」这类回调等到全站 refetch 完
      if (opts?.invalidates) {
        for (const key of opts.invalidates) void queryClient.invalidateQueries({ queryKey: [key] });
      } else {
        void queryClient.invalidateQueries();
      }
      opts?.onSuccess?.(data);
    },
    onError: (err) => {
      if (!opts?.silentError) message.error(errText(err));
    },
  });
}

// ---------- auth ----------
export const useSendSmsCode = (o?: { onSuccess?: () => void; silentError?: boolean }) =>
  useApiMutation((body: SmsCodeRequest) => sendSmsCodeApiV1AuthSmsCodePost(body), { ...o, invalidates: [] });
export const useRegister = (o?: Parameters<typeof useApiMutation>[1]) =>
  useApiMutation((body: RegisterRequest) => registerApiV1AuthRegisterPost(body), { ...o, invalidates: [] });
export const useLogin = (o?: Parameters<typeof useApiMutation>[1]) =>
  useApiMutation((body: LoginRequest) => loginApiV1AuthLoginPost(body), { ...o, invalidates: [] });
/** 设置/修改/找回密码(手机号 + 验证码);成功返回新 token 对,旧会话已被撤销。 */
export const useResetPassword = (o?: Parameters<typeof useApiMutation>[1]) =>
  useApiMutation((body: PasswordResetRequest) => resetPasswordApiV1AuthPasswordResetPost(body), { ...o, invalidates: [] });

/**
 * 登出:调后端撤销 refresh token(一次性消费位),再清本地并整页刷新。
 * 后端对无效 token 也回 204;请求失败不阻断本地登出。
 */
export function useLogout() {
  return useCallback(async () => {
    const rt = readTokens().refreshToken;
    try {
      if (rt) await logoutApiV1AuthLogoutPost({ refresh_token: rt });
    } catch {
      // 登出是尽力而为:本地清理不依赖远端结果
    }
    authStore.getState().logout();
    // 整页刷新:清干净全部内存态(查询缓存由 main.tsx 的 token 变更订阅兜底清理)
    window.location.assign("/login");
  }, []);
}

// ---------- instances ----------
// 实例写操作影响:实例域(列表/详情/事件/账单)与钱包余额(启停即结算)
const INSTANCE_INVALIDATES = ["instances", "wallet", "bills", "bill-daily-summary"] as const;
export const useCreateInstance = (o?: { onSuccess?: (d: unknown) => void; silentError?: boolean }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: InstanceCreate; idempotencyKey: string }) =>
      createInstanceApiV1InstancesPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [...INSTANCE_INVALIDATES, "skus"] },
  );
export const useStartInstance = () =>
  useApiMutation((uuid: string) => startInstanceApiV1InstancesUuidStartPost(uuid), {
    invalidates: [...INSTANCE_INVALIDATES, "skus"],
  });
export const useStopInstance = () =>
  useApiMutation((uuid: string) => stopInstanceApiV1InstancesUuidStopPost(uuid), {
    invalidates: [...INSTANCE_INVALIDATES, "skus"],
  });
export const useRestartInstance = () =>
  useApiMutation((uuid: string) => restartInstanceApiV1InstancesUuidRestartPost(uuid), {
    invalidates: [...INSTANCE_INVALIDATES],
  });
export const useReleaseInstance = (o?: { onSuccess?: () => void }) =>
  useApiMutation((uuid: string) => releaseInstanceApiV1InstancesUuidDelete(uuid), {
    ...o,
    invalidates: [...INSTANCE_INVALIDATES, "skus", "disks"],
  });
export const useRenameInstance = () =>
  useApiMutation(
    ({ uuid, name }: { uuid: string; name: string }) =>
      renameInstanceApiV1InstancesUuidPatch(uuid, { name }),
    { invalidates: ["instances"] },
  );
export const useResetJupyterToken = () =>
  useApiMutation((uuid: string) => resetJupyterTokenApiV1InstancesUuidResetJupyterTokenPost(uuid), {
    invalidates: ["instances"],
  });

// ---------- wallet / billing ----------
export const useCreateRecharge = (o?: { onSuccess?: (d: unknown) => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: RechargeCreate; idempotencyKey: string }) =>
      createRechargeApiV1WalletRechargesPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: ["wallet", "ledger"] },
  );
export const useMockPay = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    (vars: { order_no: string; amount: string }) =>
      mockWebhookApiV1WebhooksMockPost({ body: JSON.stringify(vars) }),
    { ...o, invalidates: ["wallet", "recharge", "ledger"] },
  );
export const useSubmitRealName = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    (body: RealNameRequest) => submitRealNameApiV1MeRealNamePost(body),
    { ...o, invalidates: ["me"] },
  );
export const useSetWarnThreshold = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    (hours: number) => setWarnThresholdApiV1MeWarnThresholdPatch({ low_balance_warn_hours: hours }),
    { ...o, invalidates: ["me"] },
  );

// ---------- disks ----------
/** 建盘同样要幂等键:响应丢失后重提不会多出一块按日计费的盘。 */
export const useCreateDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: DiskCreate; idempotencyKey: string }) =>
      createDiskApiV1DisksPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: ["disks", "wallet"] },
  );
export const useExpandDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ uuid, body }: { uuid: string; body: DiskExpand }) => expandDiskApiV1DisksUuidPatch(uuid, body),
    { ...o, invalidates: ["disks", "wallet"] },
  );
export const useDeleteDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation((uuid: string) => deleteDiskApiV1DisksUuidDelete(uuid), {
    ...o,
    invalidates: ["disks", "wallet", "instances"],
  });

// ---------- ssh keys / notify ----------
export const useAddSshKey = (o?: { onSuccess?: (key: SshKeyOut) => void }) =>
  useApiMutation(
    (body: { name: string; public_key: string }) => addSshKeyApiV1SshKeysPost(body),
    { ...o, invalidates: ["ssh-keys"] },
  );
export const useDeleteSshKey = () =>
  useApiMutation((keyId: number) => deleteSshKeyApiV1SshKeysKeyIdDelete(keyId), {
    invalidates: ["ssh-keys"],
  });
export const useMarkNotificationRead = () =>
  useApiMutation((id: number) => markReadApiV1NotificationsNotificationIdReadPost(id), {
    invalidates: ["notifications"],
  });
