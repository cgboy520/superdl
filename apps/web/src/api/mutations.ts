/** Write wrappers: generated fetcher + useMutation; on success the queries declared in each hook's invalidates are invalidated. */

import type { ApiError, SshKeyOut, UserOut } from "@superdl/api-client";
import {
  addSshKeyApiV1SshKeysPost,
  appendMessageApiV1TicketsTicketIdMessagesPost,
  cancelDeletionRequestApiV1MeDeletionRequestCancelPost,
  closeTicketApiV1TicketsTicketIdClosePost,
  confirmHandleApiV1MeHandlesConfirmPost,
  convertToOnDemandApiV1InstancesUuidToOnDemandPost,
  createApiKeyApiV1ServicesSlugApiKeysPost,
  createDeletionRequestApiV1MeDeletionRequestPost,
  createDiskApiV1DisksPost,
  createInstanceApiV1InstancesPost,
  createInvoiceApiV1BillingInvoicesPost,
  createRechargeApiV1WalletRechargesPost,
  createRefundApiV1WalletRefundsPost,
  createRevisionApiV1ServicesSlugRevisionsPost,
  createServiceApiV1ServicesPost,
  createTicketApiV1TicketsPost,
  deleteServiceApiV1ServicesSlugDelete,
  deleteDiskApiV1DisksUuidDelete,
  deleteSshKeyApiV1SshKeysKeyIdDelete,
  expandDiskApiV1DisksUuidPatch,
  loginApiV1AuthLoginPost,
  logoutAllApiV1AuthLogoutAllPost,
  logoutApiV1AuthLogoutPost,
  markAllReadApiV1NotificationsReadAllPost,
  markReadApiV1NotificationsNotificationIdReadPost,
  mockWebhookApiV1WebhooksMockPost,
  patchServiceApiV1ServicesSlugPatch,
  registerApiV1AuthRegisterPost,
  resetPasswordApiV1AuthPasswordResetPost,
  releaseInstanceApiV1InstancesUuidDelete,
  removePhoneApiV1MeHandlesPhoneDelete,
  renameInstanceApiV1InstancesUuidPatch,
  requestHandleCodeApiV1MeHandlesCodePost,
  renewInstanceApiV1InstancesUuidRenewPost,
  resetJupyterTokenApiV1InstancesUuidResetJupyterTokenPost,
  restartInstanceApiV1InstancesUuidRestartPost,
  revokeApiKeyApiV1ServicesSlugApiKeysKeyIdDelete,
  sendVerificationCodeApiV1AuthVerificationCodePost,
  setAutoRenewApiV1InstancesUuidAutoRenewPost,
  setWarnThresholdApiV1MeWarnThresholdPatch,
  submitKycApiV1MeKycPost,
  startInstanceApiV1InstancesUuidStartPost,
  startServiceApiV1ServicesSlugStartPost,
  stopInstanceApiV1InstancesUuidStopPost,
  stopServiceApiV1ServicesSlugStopPost,
  subscribeInstanceApiV1InstancesUuidSubscribePost,
} from "@superdl/api-client";
import type {
  ApiKeyCreateOut,
  DeletionRequestCreate,
  DiskCreate,
  DiskExpand,
  HandleCodeRequest,
  HandleConfirmRequest,
  InstanceCreate,
  InstanceOut,
  InstanceRenew,
  InvoiceCreate,
  LoginRequest,
  KycSubmitRequest,
  RechargeCreate,
  RefundCreate,
  PasswordResetRequest,
  RegisterRequest,
  RenewOut,
  ServiceCreate,
  ServiceOut,
  ServicePatch,
  ServiceRevisionCreate,
  TicketCreate,
  TicketMessageCreate,
  TicketOut,
  VerificationCodeRequest,
} from "@superdl/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";
import { useCallback } from "react";

import { useApiErrorText } from "@superdl/ui";
import { authStore, readAuthSession } from "../stores/auth";
import { keys } from "./keys";

interface MutationOpts<TData> {
  /** Awaited by TanStack Query, including asynchronous auth persistence. */
  onSuccess?: (data: TData) => unknown;
  silentError?: boolean;
  /** Query keys invalidated by prefix on success; an empty array invalidates nothing. */
  invalidates: readonly (readonly unknown[])[];
}
/** Options the page may pass; the invalidation domain is declared by each hook */
type CallerOpts<TData = unknown> = Omit<MutationOpts<TData>, "invalidates">;

export function useApiMutation<TVars = void, TData = unknown>(
  fn: (vars: TVars) => Promise<TData>,
  opts: MutationOpts<TData>,
) {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const errText = useApiErrorText();
  return useMutation<TData, ApiError, TVars>({
    mutationFn: fn,
    onSuccess: async (data) => {
      for (const key of opts.invalidates) void queryClient.invalidateQueries({ queryKey: key });
      await opts.onSuccess?.(data);
    },
    onError: (err) => {
      if (!opts.silentError) message.error(errText(err));
    },
  });
}

export const useSendVerificationCode = (o?: { onSuccess?: () => void; silentError?: boolean }) =>
  useApiMutation((body: VerificationCodeRequest) => sendVerificationCodeApiV1AuthVerificationCodePost(body), {
    ...o,
    invalidates: [],
  });
export const useRequestHandleCode = (o?: { onSuccess?: () => void; silentError?: boolean }) =>
  useApiMutation((body: HandleCodeRequest) => requestHandleCodeApiV1MeHandlesCodePost(body), {
    ...o,
    invalidates: [],
  });
export const useConfirmHandle = (o?: CallerOpts<UserOut>) =>
  useApiMutation((body: HandleConfirmRequest) => confirmHandleApiV1MeHandlesConfirmPost(body), {
    ...o,
    invalidates: [keys.me],
  });
export const useRemovePhone = (o?: CallerOpts<UserOut>) =>
  useApiMutation(() => removePhoneApiV1MeHandlesPhoneDelete(), { ...o, invalidates: [keys.me] });
export const useRegister = (o?: CallerOpts) =>
  useApiMutation((body: RegisterRequest) => registerApiV1AuthRegisterPost(body), { ...o, invalidates: [] });
export const useLogin = (o?: CallerOpts) =>
  useApiMutation((body: LoginRequest) => loginApiV1AuthLoginPost(body), { ...o, invalidates: [] });
/** Set, change or recover the password with a handle and a verification code. */
export const useResetPassword = (o?: CallerOpts) =>
  useApiMutation((body: PasswordResetRequest) => resetPasswordApiV1AuthPasswordResetPost(body), {
    ...o,
    invalidates: [],
  });

/** Revoke this device or all account sessions. Clear only the initiating session, even on failure. */
export function useLogout() {
  return useCallback(async (scope: "current" | "all" = "current") => {
    const { sessionId } = readAuthSession();
    // Do not hold the persistence lock across a mutator call: a 401 may itself need logout.
    try {
      if (scope === "all") {
        await logoutAllApiV1AuthLogoutAllPost();
      } else {
        await logoutApiV1AuthLogoutPost({ headers: { "X-Requested-With": "fetch" } });
      }
    } catch {
      /* ignored */
    }
    const cleared = await authStore.getState().logout(sessionId);
    if (cleared || !readAuthSession().accessToken) window.location.assign("/login");
  }, []);
}

const INSTANCE_INVALIDATES = [keys.instances.all, keys.wallet, keys.bills.all, keys.billDailySummary.all] as const;
export const useCreateInstance = (o?: { onSuccess?: (d: unknown) => void; silentError?: boolean }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: InstanceCreate; idempotencyKey: string }) =>
      createInstanceApiV1InstancesPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [...INSTANCE_INVALIDATES, keys.skus] },
  );
export const useStartInstance = () =>
  useApiMutation((uuid: string) => startInstanceApiV1InstancesUuidStartPost(uuid), {
    invalidates: [...INSTANCE_INVALIDATES, keys.skus],
  });
export const useStopInstance = () =>
  useApiMutation((uuid: string) => stopInstanceApiV1InstancesUuidStopPost(uuid), {
    invalidates: [...INSTANCE_INVALIDATES, keys.skus],
  });
export const useRestartInstance = () =>
  useApiMutation((uuid: string) => restartInstanceApiV1InstancesUuidRestartPost(uuid), {
    invalidates: [...INSTANCE_INVALIDATES],
  });
export const useReleaseInstance = (o?: { onSuccess?: () => void }) =>
  useApiMutation((uuid: string) => releaseInstanceApiV1InstancesUuidDelete(uuid), {
    ...o,
    invalidates: [...INSTANCE_INVALIDATES, keys.skus, keys.disks],
  });
export const useRenameInstance = () =>
  useApiMutation(
    ({ uuid, name }: { uuid: string; name: string }) => renameInstanceApiV1InstancesUuidPatch(uuid, { name }),
    { invalidates: [keys.instances.all] },
  );
/** Subscription renewal, the idempotency key comes from the caller. */
export const useRenewInstance = (uuid: string, o?: CallerOpts<RenewOut>) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: InstanceRenew; idempotencyKey: string }) =>
      renewInstanceApiV1InstancesUuidRenewPost(uuid, body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [...INSTANCE_INVALIDATES] },
  );
/** On-demand → subscription: same request / response shape as renewal, counting from now; the invalidation domain includes bills. */
export const useSubscribeInstance = (uuid: string, o?: CallerOpts<RenewOut>) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: InstanceRenew; idempotencyKey: string }) =>
      subscribeInstanceApiV1InstancesUuidSubscribePost(uuid, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    { ...o, invalidates: [...INSTANCE_INVALIDATES] },
  );
/** Spot → on-demand: only flips market and the unit price, no idempotency key; the invalidation domain includes bills. */
export const useConvertToOnDemand = (uuid: string, o?: CallerOpts<InstanceOut>) =>
  useApiMutation(() => convertToOnDemandApiV1InstancesUuidToOnDemandPost(uuid), {
    ...o,
    invalidates: [...INSTANCE_INVALIDATES],
  });
/** Auto-renew switch: only changes the subscription row, the invalidation domain is instances only. */
export const useSetAutoRenew = (uuid: string, o?: CallerOpts<InstanceOut>) =>
  useApiMutation((enabled: boolean) => setAutoRenewApiV1InstancesUuidAutoRenewPost(uuid, { enabled }), {
    ...o,
    invalidates: [keys.instances.all],
  });
export const useResetJupyterToken = () =>
  useApiMutation((uuid: string) => resetJupyterTokenApiV1InstancesUuidResetJupyterTokenPost(uuid), {
    invalidates: [keys.instances.all],
  });

const SERVICE_INVALIDATES = [keys.services.all, keys.wallet, keys.bills.all, keys.billDailySummary.all] as const;
/** Deploy a service: the idempotency key derives from the form snapshot. */
export const useCreateService = (o?: { onSuccess?: (d: ServiceOut) => void; silentError?: boolean }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: ServiceCreate; idempotencyKey: string }) =>
      createServiceApiV1ServicesPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [...SERVICE_INVALIDATES, keys.skus, keys.disks] },
  );
/** Revision update (recreate): the idempotency key derives from the form snapshot. */
export const useCreateRevision = (slug: string, o?: { onSuccess?: (d: ServiceOut) => void; silentError?: boolean }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: ServiceRevisionCreate; idempotencyKey: string }) =>
      createRevisionApiV1ServicesSlugRevisionsPost(slug, body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [...SERVICE_INVALIDATES, keys.skus] },
  );
export const useStopService = (o?: CallerOpts<ServiceOut>) =>
  useApiMutation((slug: string) => stopServiceApiV1ServicesSlugStopPost(slug), {
    ...o,
    invalidates: [...SERVICE_INVALIDATES, keys.skus],
  });
export const useStartService = (o?: CallerOpts<ServiceOut>) =>
  useApiMutation((slug: string) => startServiceApiV1ServicesSlugStartPost(slug), {
    ...o,
    invalidates: [...SERVICE_INVALIDATES, keys.skus],
  });
export const useDeleteService = (o?: CallerOpts<ServiceOut>) =>
  useApiMutation((slug: string) => deleteServiceApiV1ServicesSlugDelete(slug), {
    ...o,
    invalidates: [...SERVICE_INVALIDATES, keys.skus, keys.disks],
  });
/** Rename / auth switch: the invalidation domain is services only. */
export const useUpdateService = (slug: string, o?: CallerOpts<ServiceOut>) =>
  useApiMutation((body: ServicePatch) => patchServiceApiV1ServicesSlugPatch(slug, body), {
    ...o,
    invalidates: [keys.services.all],
  });
/** Create a service access key: the plaintext key appears once in the response and goes only to the one-off success state, never into caches or logs. */
export const useCreateServiceApiKey = (slug: string, o?: CallerOpts<ApiKeyCreateOut>) =>
  useApiMutation((name: string) => createApiKeyApiV1ServicesSlugApiKeysPost(slug, { name }), {
    ...o,
    invalidates: [keys.services.all],
  });
/** Revoke a key: writes revoked_at without deleting the row. */
export const useRevokeServiceApiKey = (slug: string, o?: CallerOpts) =>
  useApiMutation((keyId: number) => revokeApiKeyApiV1ServicesSlugApiKeysKeyIdDelete(slug, keyId), {
    ...o,
    invalidates: [keys.services.all],
  });

export const useCreateRecharge = (o?: { onSuccess?: (d: unknown) => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: RechargeCreate; idempotencyKey: string }) =>
      createRechargeApiV1WalletRechargesPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [keys.wallet, keys.ledger.all] },
  );
export const useMockPay = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    (vars: { order_no: string; amount: string }) => mockWebhookApiV1WebhooksMockPost({ body: JSON.stringify(vars) }),
    { ...o, invalidates: [keys.wallet, keys.recharge.all, keys.ledger.all] },
  );
/** Request a refund: the idempotency key derives from the form snapshot. */
export const useCreateRefund = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: RefundCreate; idempotencyKey: string }) =>
      createRefundApiV1WalletRefundsPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [keys.refunds.all, keys.refundableOrders, keys.wallet, keys.ledger.all] },
  );
/** Request an invoice: the amount is computed server-side per period; an idempotency replay returns the existing request. */
export const useCreateInvoice = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: InvoiceCreate; idempotencyKey: string }) =>
      createInvoiceApiV1BillingInvoicesPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [keys.invoices.all, keys.invoiceEligible] },
  );
export const useSubmitKyc = (o?: { onSuccess?: () => void }) =>
  useApiMutation((body: KycSubmitRequest) => submitKycApiV1MeKycPost(body), { ...o, invalidates: [keys.me] });
export const useSetWarnThreshold = (o?: { onSuccess?: () => void }) =>
  useApiMutation((hours: number) => setWarnThresholdApiV1MeWarnThresholdPatch({ low_balance_warn_hours: hours }), {
    ...o,
    invalidates: [keys.me],
  });

/** Request deletion: the server is idempotent per (user_id, pending). */
export const useCreateDeletionRequest = (o?: { onSuccess?: () => void }) =>
  useApiMutation((body: DeletionRequestCreate) => createDeletionRequestApiV1MeDeletionRequestPost(body), {
    ...o,
    invalidates: [keys.deletionRequest],
  });
export const useCancelDeletionRequest = (o?: { onSuccess?: () => void }) =>
  useApiMutation(() => cancelDeletionRequestApiV1MeDeletionRequestCancelPost(), {
    ...o,
    invalidates: [keys.deletionRequest],
  });

/** Disk creation carries an idempotency key. */
export const useCreateDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: DiskCreate; idempotencyKey: string }) =>
      createDiskApiV1DisksPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [keys.disks, keys.wallet] },
  );
export const useExpandDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation(({ uuid, body }: { uuid: string; body: DiskExpand }) => expandDiskApiV1DisksUuidPatch(uuid, body), {
    ...o,
    invalidates: [keys.disks, keys.wallet],
  });
export const useDeleteDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation((uuid: string) => deleteDiskApiV1DisksUuidDelete(uuid), {
    ...o,
    invalidates: [keys.disks, keys.wallet, keys.instances.all, keys.services.all],
  });

export const useAddSshKey = (o?: { onSuccess?: (key: SshKeyOut) => void }) =>
  useApiMutation((body: { name: string; public_key: string }) => addSshKeyApiV1SshKeysPost(body), {
    ...o,
    invalidates: [keys.sshKeys],
  });
export const useDeleteSshKey = () =>
  useApiMutation((keyId: number) => deleteSshKeyApiV1SshKeysKeyIdDelete(keyId), {
    invalidates: [keys.sshKeys],
  });
export const useMarkNotificationRead = () =>
  useApiMutation((id: number) => markReadApiV1NotificationsNotificationIdReadPost(id), {
    invalidates: [keys.notifications.all],
  });
export const useMarkAllNotificationsRead = () =>
  useApiMutation(() => markAllReadApiV1NotificationsReadAllPost(), {
    invalidates: [keys.notifications.all],
  });

/** New ticket: the idempotency key derives from the form snapshot. */
export const useCreateTicket = (o?: { onSuccess?: (d: TicketOut) => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: TicketCreate; idempotencyKey: string }) =>
      createTicketApiV1TicketsPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [keys.tickets.all] },
  );
export const useAppendTicketMessage = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ ticketId, body }: { ticketId: number; body: TicketMessageCreate }) =>
      appendMessageApiV1TicketsTicketIdMessagesPost(ticketId, body),
    { ...o, invalidates: [keys.tickets.all] },
  );
export const useCloseTicket = (o?: { onSuccess?: () => void }) =>
  useApiMutation((ticketId: number) => closeTicketApiV1TicketsTicketIdClosePost(ticketId), {
    ...o,
    invalidates: [keys.tickets.all],
  });
