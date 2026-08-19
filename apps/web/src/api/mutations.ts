/**
 * 写操作统一封装:生成的纯函数 + TanStack useMutation。
 * (orval 配置将 POST 也生成了 query 形态 hook,故 mutation 走生成函数,仍是契约客户端。)
 * 成功后广播失效全部查询 —— 应用规模下最稳的一致性策略。
 */

import type { ApiError } from "@superdl/api-client";
import {
  addSshKeyApiV1SshKeysPost,
  createDiskApiV1DisksPost,
  createInstanceApiV1InstancesPost,
  createRechargeApiV1WalletRechargesPost,
  deleteDiskApiV1DisksUuidDelete,
  deleteSshKeyApiV1SshKeysKeyIdDelete,
  expandDiskApiV1DisksUuidPatch,
  loginApiV1AuthLoginPost,
  markReadApiV1NotificationsNotificationIdReadPost,
  mockWebhookApiV1WebhooksMockPost,
  registerApiV1AuthRegisterPost,
  releaseInstanceApiV1InstancesUuidDelete,
  renameInstanceApiV1InstancesUuidPatch,
  resetJupyterTokenApiV1InstancesUuidResetJupyterTokenPost,
  restartInstanceApiV1InstancesUuidRestartPost,
  sendSmsCodeApiV1AuthSmsCodePost,
  setWarnThresholdApiV1MeWarnThresholdPatch,
  startInstanceApiV1InstancesUuidStartPost,
  stopInstanceApiV1InstancesUuidStopPost,
} from "@superdl/api-client";
import type {
  DiskCreate,
  DiskExpand,
  InstanceCreate,
  LoginRequest,
  RechargeCreate,
  RegisterRequest,
  SmsCodeRequest,
} from "@superdl/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";

export function useApiMutation<TVars, TData>(
  fn: (vars: TVars) => Promise<TData>,
  opts?: { onSuccess?: (data: TData) => void; silentError?: boolean },
) {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  return useMutation<TData, ApiError, TVars>({
    mutationFn: fn,
    onSuccess: async (data) => {
      await queryClient.invalidateQueries();
      opts?.onSuccess?.(data);
    },
    onError: (err) => {
      if (!opts?.silentError) message.error(err.message ?? "请求失败");
    },
  });
}

// ---------- auth ----------
export const useSendSmsCode = (o?: { onSuccess?: () => void }) =>
  useApiMutation((body: SmsCodeRequest) => sendSmsCodeApiV1AuthSmsCodePost(body), o);
export const useRegister = (o?: Parameters<typeof useApiMutation>[1]) =>
  useApiMutation((body: RegisterRequest) => registerApiV1AuthRegisterPost(body), o);
export const useLogin = (o?: Parameters<typeof useApiMutation>[1]) =>
  useApiMutation((body: LoginRequest) => loginApiV1AuthLoginPost(body), o);

// ---------- instances ----------
export const useCreateInstance = (o?: { onSuccess?: (d: unknown) => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: InstanceCreate; idempotencyKey: string }) =>
      createInstanceApiV1InstancesPost(body, {
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    o,
  );
export const useStartInstance = () =>
  useApiMutation((uuid: string) => startInstanceApiV1InstancesUuidStartPost(uuid));
export const useStopInstance = () =>
  useApiMutation((uuid: string) => stopInstanceApiV1InstancesUuidStopPost(uuid));
export const useRestartInstance = () =>
  useApiMutation((uuid: string) => restartInstanceApiV1InstancesUuidRestartPost(uuid));
export const useReleaseInstance = (o?: { onSuccess?: () => void }) =>
  useApiMutation((uuid: string) => releaseInstanceApiV1InstancesUuidDelete(uuid), o);
export const useRenameInstance = () =>
  useApiMutation(({ uuid, name }: { uuid: string; name: string }) =>
    renameInstanceApiV1InstancesUuidPatch(uuid, { name }),
  );
export const useResetJupyterToken = () =>
  useApiMutation((uuid: string) => resetJupyterTokenApiV1InstancesUuidResetJupyterTokenPost(uuid));

// ---------- wallet / billing ----------
export const useCreateRecharge = (o?: { onSuccess?: (d: unknown) => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: RechargeCreate; idempotencyKey: string }) =>
      createRechargeApiV1WalletRechargesPost(body, {
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    o,
  );
export const useMockPay = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    (vars: { order_no: string; amount: string }) =>
      mockWebhookApiV1WebhooksMockPost({ body: JSON.stringify(vars) }),
    o,
  );
export const useSetWarnThreshold = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    (hours: number) => setWarnThresholdApiV1MeWarnThresholdPatch({ low_balance_warn_hours: hours }),
    o,
  );

// ---------- disks ----------
export const useCreateDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation((body: DiskCreate) => createDiskApiV1DisksPost(body), o);
export const useExpandDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ uuid, body }: { uuid: string; body: DiskExpand }) => expandDiskApiV1DisksUuidPatch(uuid, body),
    o,
  );
export const useDeleteDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation((uuid: string) => deleteDiskApiV1DisksUuidDelete(uuid), o);

// ---------- ssh keys / notify ----------
export const useAddSshKey = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    (body: { name: string; public_key: string }) => addSshKeyApiV1SshKeysPost(body),
    o,
  );
export const useDeleteSshKey = () =>
  useApiMutation((keyId: number) => deleteSshKeyApiV1SshKeysKeyIdDelete(keyId));
export const useMarkNotificationRead = () =>
  useApiMutation((id: number) => markReadApiV1NotificationsNotificationIdReadPost(id));
