"""Error-message catalog: the single source for `AppError(key=...)` text. Keys are
`<module>.<camelCase>`, placeholders `{name}` (exported as `{{name}}`). The English source is
exported to packages/ui/locales/en-US/errors.json by apps/api/scripts/export_error_messages.py;
zh-CN/errors.json is hand-maintained and kept key-for-key in parity by tests."""

from collections.abc import Mapping
from typing import Any

from app.core.logging import get_logger

MESSAGES: dict[str, str] = {
    "account.accountDeleted": "Account deleted",
    "account.captchaChannelError": (
        "Human verification service is temporarily unavailable — try again later"
    ),
    "account.captchaRequired": "Complete the human verification first",
    "account.captchaVerifyFailed": "Human verification failed — complete it again and retry",
    "account.codeInvalid": "Incorrect or expired verification code",
    "account.codeSendFailed": "Could not send the verification code, please try again later",
    "account.codeTooFrequent": "Too many requests, try again in {seconds} s",
    "account.credentialRequired": "Provide an SMS code or password",
    "account.deletionBalanceRemaining": (
        "Balance {balance} not withdrawn — withdraw it via the refund process first, then "
        "delete the account after it arrives"
    ),
    "account.deletionCooldown": (
        "Deletion cooling-off period not over (about {hours} hours left) — cannot execute yet"
    ),
    "account.deletionHandleMismatch": "The account you typed does not match the signed-in account",
    "account.deletionLeftovers": (
        "You still have {instances} unreleased instance(s) and {disks} undeleted data disk(s) "
        "— clear all resources before deleting your account"
    ),
    "account.deletionNotCancellable": "Deletion request is {status} and cannot be cancelled",
    "account.deletionNotPending": "Deletion request is {status} and cannot be processed",
    "account.emailRequiredFirst": "Add an email address before removing the phone number",
    "account.emailTaken": "This email is already registered — sign in instead",
    "account.handleTaken": "This contact is already bound to another account",
    "account.kycIdentityInvalid": "The identity number is malformed or fails its checksum",
    "account.kycNotAvailable": "Identity verification is not available on this site",
    "account.kycRegionUnsupported": (
        "The identity-verification provider needs a +86 phone number on the account"
    ),
    "account.loginFailed": "Incorrect account or credentials",
    "account.phoneRegionNotAllowed": (
        "This site only accepts phone numbers with dial codes: {codes}"
    ),
    "account.phoneRequired": (
        "This site requires a phone number: enter it together with the SMS code"
    ),
    "account.phoneRequiredByProfile": (
        "This site requires a phone number on file; it cannot be removed"
    ),
    "account.phoneTaken": "This phone number is already registered — log in instead",
    "account.realNameChannelError": (
        "Identity verification service is unavailable — try again later"
    ),
    "account.realNameDisabled": "Identity verification is not available yet",
    "account.realNameDone": "Identity already verified — no need to resubmit",
    "account.realNameIdentityLimit": (
        "This ID is already linked to {max} accounts and cannot be linked to another"
    ),
    "account.realNameMismatch": "Identity details do not match carrier records — check and retry",
    "account.sshKeyDuplicate": "This public key was already added",
    "account.sshKeyLimitReached": (
        "You have reached the limit of {max} SSH keys — delete unused keys before adding more"
    ),
    "account.termsNotAccepted": (
        "Please read and accept the Terms of Service and Privacy Policy first"
    ),
    "account.userFrozen": "Account frozen — contact support",
    "adminapi.adjustAlreadyProcessed": "Adjustment already processed",
    "adminapi.adjustNotZero": "Adjustment amount cannot be 0",
    "adminapi.adjustReviewerTooNew": (
        "The reviewer account must be at least 24 hours older than the adjustment — not a "
        "valid second review"
    ),
    "adminapi.adjustSecondReviewer": "Adjustments must be reviewed by a second administrator",
    "adminapi.adminUsernameTaken": "That username already exists",
    "adminapi.alertAlreadyAcked": "This alert has already been acknowledged",
    "adminapi.announcementAlreadyRevoked": "Announcement already revoked — no need to repeat",
    "adminapi.badDayFormat": "day must be YYYY-MM-DD",
    "adminapi.cannotChangeSelf": (
        "You cannot disable or downgrade your own account — ask another super admin to do it"
    ),
    "adminapi.emailTestFailed": "Test email failed: {message}",
    "adminapi.loginFailed": "Wrong username or password",
    "adminapi.mfaCodeInvalid": "Incorrect or expired verification code — try again",
    "adminapi.mfaNotBound": "This account has no authenticator bound",
    "adminapi.mfaResetSelfForbidden": (
        "You cannot reset your own 2FA — use a recovery code or ask another super admin"
    ),
    "adminapi.mfaTicketInvalid": "Login ticket expired — sign in with username and password again",
    "adminapi.reviewerNotIndependent": (
        "The reviewer account has no administrative actions on record before this adjustment "
        "— not an independent review"
    ),
    "adminapi.roleRequired": "Required role: {roles}",
    "adminapi.roleRequiredAdmin": "Super admin privileges required",
    "adminapi.smsTestFailed": "Send failed: {message}",
    "adminapi.taskNotFound": "Task not found",
    "adminapi.taskStateNotIgnorable": "Task status {status} cannot be ignored",
    "adminapi.taskStateNotReplayable": "Task status {status} cannot be replayed",
    "adminapi.userDisabled": "Account disabled",
    "billing.alipayCallbackMerchantMismatch": (
        "The Alipay notification's merchant details do not match this platform"
    ),
    "billing.alipayCallbackVerifyFailed": "Alipay callback signature verification failed",
    "billing.alipayCreateFailed": "Alipay order creation failed: {message}",
    "billing.alipayCredentialsIncomplete": (
        "Alipay merchant credentials incomplete (Admin · Platform Config)"
    ),
    "billing.alipayQueryFailed": "Alipay order query failed: {message}",
    "billing.alipaySellerIdRequired": (
        "Alipay is enabled but the seller PID (seller_id) is not configured (Admin · Platform "
        "Config)"
    ),
    "billing.amountMismatchAdjust": (
        "Channel amount {channel} does not match order amount {order} — requires a manual "
        "adjustment"
    ),
    "billing.backfillKeyInUse": (
        "This idempotency key was already used to backfill order {order_no} — replay with the "
        "original order, or use a new key for a different order"
    ),
    "billing.badDateFormat": "Date must be YYYY-MM-DD",
    "billing.badMonthFormat": "Month must be YYYY-MM",
    "billing.callbackAmountMismatch": "Callback amount does not match the order",
    "billing.currencyMismatch": "The channel currency does not match the order currency",
    "billing.callbackChannelMismatch": "Callback channel does not match the order",
    "billing.channelCurrencyUnsupported": (
        "This payment channel does not settle in the platform currency — choose another"
    ),
    "billing.channelNotEnabled": "This payment channel is not enabled — choose another",
    "billing.channelStateNotBackfillable": "Channel-side status is {status} — cannot backfill",
    "billing.insufficientAvailableFrozen": (
        "Insufficient available balance: {frozen} is frozen pending chargeback "
        "reconciliation and cannot be spent on new purchases; contact support if in doubt"
    ),
    "billing.insufficientBalance": "Insufficient balance — top up first",
    "billing.insufficientForInFlight": (
        "Insufficient balance: in-flight resources are expected to burn {inflight} more; "
        "this operation requires a balance of at least {required} (current {balance}) — "
        "top up first"
    ),
    "billing.invoiceAmountStale": (
        "The invoiceable amount has changed (now {expected}, requested {requested}): a "
        "refund occurred in this period — reject the request and ask the user to resubmit "
        "with the new amount"
    ),
    "billing.invoiceNotFound": "Invoice request not found",
    "billing.invoiceNothingToBill": (
        "Nothing to invoice for period {period} (no paid top-ups, or already fully requested)"
    ),
    "billing.invoicePeriodAlreadyApplied": (
        "Period {period} already has a pending or issued invoice — do not resubmit"
    ),
    "billing.invoicePeriodNotOpen": (
        "Period {period} has not ended yet: request the current month's invoice from the 1st "
        "of next month"
    ),
    "billing.invoiceStateNotIssuable": (
        "Invoice request is {status} — only pending requests can be issued"
    ),
    "billing.invoiceStateNotRejectable": (
        "Invoice request is {status} — only pending requests can be rejected"
    ),
    "billing.mockCallbackParseFailed": "Failed to parse mock callback",
    "billing.mockDevOnly": "The mock channel is dev-only",
    "billing.orderAlreadyPaid": "Order already credited — no backfill needed",
    "billing.orderNotFound": "Order not found",
    "billing.orderStateNotBackfillable": "Order status {status} cannot be backfilled",
    "billing.realNameRequiredForRecharge": (
        "Regulations require identity verification before topping up"
    ),
    "billing.refundAlreadyApplied": (
        "This order already has an active refund request — do not resubmit"
    ),
    "billing.refundAmountExceeded": (
        "Refund amount exceeds the refundable cap {max} (order {order}, already "
        "refunded {refunded}, refundable balance {refundable})"
    ),
    "billing.refundBalanceConsumed": (
        "Balance has since been spent and cannot cover this refund (current {balance}, "
        "required {amount}) — cancel the request instead"
    ),
    "billing.refundChannelReversed": (
        "This order's payment was reversed (charged back) by the payment channel and cannot "
        "be refunded — contact support"
    ),
    "billing.refundCumulativeExceeded": (
        "Cumulative refunds would exceed the order amount (order {order}, already refunded "
        "{refunded}, this request {amount}) — data anomaly, investigate and cancel this "
        "refund"
    ),
    "billing.refundInvoiceIssued": (
        "An invoice has been issued for this order; it must be voided (red-letter) before a "
        "refund — contact support"
    ),
    "billing.refundNotFound": "Refund request not found",
    "billing.refundNotRefundable": (
        "Refundable balance is insufficient ({refundable} remains of channel-paid funds "
        "after consumption/refunds, {amount} required) — cancel the request instead"
    ),
    "billing.refundOrderNotPaid": "Only successfully paid top-up orders can be refunded",
    "billing.refundPayoutChannelMismatch": (
        "Payout channel must match the order's payment channel (expected {expected}); choose "
        "offline only when necessary and keep the voucher"
    ),
    "billing.refundPayoutSamePerson": (
        "The payout registrar must not be the reviewer (two-person rule) — ask another "
        "finance admin"
    ),
    "billing.refundStateNotCancellable": "Refund request in status {status} cannot be cancelled",
    "billing.refundStateNotPayable": "Refund request in status {status} cannot be paid out",
    "billing.refundStateNotReviewable": "Refund request in status {status} cannot be reviewed",
    "billing.settlementBehind": (
        "Settlement is catching up; try converting to a subscription again shortly"
    ),
    "billing.settlementGapNotFound": "Settlement gap not found",
    "billing.settlementGapNotReplayable": (
        "This gap type ({reason}) cannot be replayed — investigate and resolve manually"
    ),
    "billing.settlementGapObjectGone": (
        "The object (id={objectId}) no longer exists — replay impossible, resolve manually"
    ),
    "billing.subscriptionAlreadyActive": (
        "This instance already has an active subscription; use renew to extend it"
    ),
    "billing.subscriptionCancelled": (
        "This instance's subscription has been cancelled and cannot be renewed"
    ),
    "billing.subscriptionExpired": (
        "The subscription has expired. Renew it before starting the instance"
    ),
    "billing.subscriptionMissing": "This instance has no renewable subscription",
    "billing.unknownChannel": "Unknown payment channel: {name}",
    "billing.wechatCallbackMerchantMismatch": (
        "The WeChat Pay notification's merchant details do not match this platform"
    ),
    "billing.wechatCallbackVerifyFailed": "WeChat Pay callback signature verification failed",
    "billing.wechatCreateFailed": "WeChat Pay order creation failed: {message}",
    "billing.wechatCredentialsIncomplete": (
        "WeChat Pay merchant credentials incomplete (Admin · Platform Config)"
    ),
    "billing.wechatQueryFailed": "WeChat Pay order query failed: {message}",
    "catalog.cpuSkuGpuFieldsMustBeZero": (
        "CPU specs carry no GPU: leave the GPU model empty, set compute share / VRAM / GPUs "
        "per instance to 0, and leave the MIG profile blank"
    ),
    "catalog.gpuSkuNeedsGpuFields": (
        "GPU specs need a GPU model, and compute share / VRAM / GPUs per instance cannot be 0"
    ),
    "catalog.imageRefExists": "This image_ref already exists",
    "catalog.isolationChangeNeedsOffSale": (
        "A listed SKU cannot change its pool or MIG profile: both decide the isolation "
        "mechanism and the spec buyers see, so changing them makes it a different product. "
        "Delist it first, or create a new SKU"
    ),
    "catalog.migProfileMismatch": (
        "The mig pool requires a slice profile; other pools must leave it empty"
    ),
    "catalog.prewarmDisabled": "Prewarming is disabled for this image — enable it first",
    "catalog.priceHourlyTwoDecimals": (
        "Hourly-billed specs allow at most 2 decimal places (billed per hour at 2 decimals — "
        "more digits cause rounding drift); 4-digit precision is only for data-disk GB-month "
        "prices"
    ),
    "catalog.priceTooSmall": "Unit price too small: it must not round to 0 at four decimal places",
    "catalog.skuBusinessKeyExists": (
        "A SKU with the same model, tier, pool, MIG profile, cores share and vCPU/memory "
        "already exists — edit that one instead"
    ),
    "catalog.skuNotSellable": (
        "No Ready node of “{model} × {pool} pool” exists in the cluster; instances of this "
        "spec would never start. Confirm to force-list it"
    ),
    "catalog.skuNotSellableCpu": (
        "No Ready node in the {pool} pool; users will not be able to start instances after "
        "listing. Confirm to force listing"
    ),
    "catalog.skuOffSale": "This spec has been delisted",
    "catalog.tierPoolMismatch": "Tier {tier} may only run on the {pools} pool, but this is {pool}",
    "common.auditUnavailable": (
        "Audit writes are failing persistently; write operations are temporarily rejected — "
        "retry later or contact the platform"
    ),
    "common.badCursor": "Invalid pagination cursor",
    "common.forbidden": "You do not have access",
    "common.httpError": "Request failed ({status})",
    "common.idempotencyKeyMismatch": (
        "The same idempotency key was used with different request parameters and was rejected "
        "as a conflict — retry with a new key for a new operation"
    ),
    "common.internal": "Internal server error — try again later",
    "common.methodNotAllowed": "This endpoint does not support the request method",
    "common.networkError": "Network connection failed. Check your connection and try again.",
    "common.notFound": "Resource not found",
    "common.payloadTooLarge": "Request body too large — trim the content and try again",
    "common.rateLimited": "Too many attempts — try again later",
    "common.retryableConflict": "Conflicts with another operation in progress — please retry",
    "common.unauthorized": "Not signed in or session expired",
    "common.validation": "Request validation failed",
    "disks.capacityQuota": (
        "Total data disk capacity would exceed the limit ({max} GB) — delete unused disks or "
        "contact support to raise it"
    ),
    "disks.countQuota": (
        "Data disk limit reached ({max}) — delete unused disks or contact support to raise it"
    ),
    "disks.expandNeedsActive": "Only active data disks can be expanded",
    "disks.inUseDelete": "The data disk is mounted — release the instance first",
    "disks.mountedElsewhere": "The data disk is mounted on another instance",
    "disks.notMountable": "The data disk cannot be mounted in its current state",
    "disks.notProvisioned": (
        "Data disk is still being provisioned — retry shortly; contact support if it persists"
    ),
    "disks.realNameRequired": (
        "Regulations require identity verification before provisioning storage — complete it "
        "under Settings · Identity Verification first"
    ),
    "disks.shrinkForbidden": "Data disks can only be expanded, not shrunk",
    "disks.sizeMax": "Maximum size is {max} GB",
    "disks.sizeRange": "Size must be between {min} and {max} GB",
    "legal.docNotFound": "The legal document does not exist or has not been published",
    "legal.draftExists": "A draft already exists for this document and locale — handle it first",
    "legal.publishedNotArchivable": "A published version cannot be archived directly",
    "legal.versionNotDraft": "Version is in status {status} — only drafts allow this operation",
    "metering.badNodeName": "Invalid node name",
    "metering.badRange": "range must be one of 1h/6h/24h",
    "metering.unavailable": (
        "Metrics are temporarily unavailable; billing is unaffected (it relies on the "
        "instance event log)"
    ),
    "nodes.alreadyTerminal": "Status {status} is already terminal — no need to revoke",
    "nodes.clusterNotConfigured": (
        "Cluster access not configured: a super admin must enter the RKE2 server address and "
        "join token under Platform Config · Cluster Access"
    ),
    "nodes.clusterNotReady": (
        "The cluster scheduler is not ready, so instances cannot start right now. The "
        "platform is checking automatically — please retry shortly"
    ),
    "nodes.clusterProbeFailed": "Cluster connection failed: {error}",
    "nodes.componentProbeFailed": (
        "Live cluster probe returned nothing — the values below are still the latest patrol "
        "snapshot"
    ),
    "nodes.enrollTransition": "Enrollment status transition {from} → {to} is not allowed",
    "nodes.hostnameMismatch": (
        "Hostname does not match the registration; the token is revoked — verify in the admin "
        "console and regenerate"
    ),
    "nodes.nodeHasInstances": (
        "This node still has {count} instance(s) that are not released. Release them first (a "
        "stopped instance's instance disk is pinned to this node and will not start after a "
        "pool change)."
    ),
    "nodes.nodeNotFound": (
        "Node is not in the ledger: check the node name, or wait for the next patrol round "
        "(60s) to pick it up and retry"
    ),
    "nodes.poolIncompatible": (
        "A node without GPUs can only stay in the cpu pool, and a node with GPUs cannot move "
        "into it."
    ),
    "nodes.poolMigUnsupported": (
        "{model} does not support MIG partitioning, so it cannot move to the mig pool."
    ),
    "nodes.poolNotSwitchable": "Node pools can only be switched among {pools}.",
    "nodes.poolPassthroughUnsupported": (
        "{model} does not support whole-GPU passthrough, so it cannot move to the kata pool."
    ),
    "nodes.poolUnchanged": "This node is already in the {pool} pool.",
    "nodes.regenerateNotAllowed": (
        "Status {status} does not allow regeneration (only pending/expired/failed)"
    ),
    "nodes.storageClassMissing": (
        "Cluster storage is not ready (missing {names}), so this cannot be provisioned right "
        "now — please contact platform operations"
    ),
    "orchestrator.accessNeedsRunning": "Connection info is available while the instance is running",
    "orchestrator.convertNeedsRunningOrStopped": (
        "Only running or stopped instances can be converted to a subscription"
    ),
    "orchestrator.convertNotOnDemand": (
        "Only pay-as-you-go instances can be converted to a subscription"
    ),
    "orchestrator.cpuSkuNoGpu": "This spec is a CPU instance (no GPU); the GPU count cannot be set",
    "orchestrator.envEntryTooLong": (
        "Environment variable “{name}” is too long: names up to {key_max} characters, values "
        "up to {value_max} characters"
    ),
    "orchestrator.envKeyInvalid": (
        "Environment variable name “{name}” is not valid: letters, digits and underscores "
        "only, and it cannot start with a digit"
    ),
    "orchestrator.envKeyReserved": (
        "Environment variable name “{name}” is reserved by the platform (the JUPYTER_ and "
        "SUPERDL_ prefixes, and AUTHORIZED_KEYS) — pick another"
    ),
    "orchestrator.envSecretKeyUnknown": (
        "Environment variable “{name}” is marked secret but is not in the variable list"
    ),
    "orchestrator.envTooMany": "At most {max} environment variables are allowed",
    "orchestrator.forceStopNeedsRunning": "Only running instances can be force-stopped",
    "orchestrator.frozenNeedsRecharge": "Frozen due to arrears — top up to unfreeze, then start",
    "orchestrator.gpuCountRange": "GPU count must be between 1 and {max}",
    "orchestrator.gpuQuota": (
        "Total GPUs would exceed the limit ({max}) — release some or contact support to raise it"
    ),
    "orchestrator.healthPathSlash": "The health check path has to start with /",
    "orchestrator.imageRefInvalid": (
        "Invalid image reference. Example: registry.example.com/pytorch:2.9"
    ),
    "orchestrator.imageRefNotAllowed": (
        "This image registry is not allowed. Use a platform image or one of: {registries}"
    ),
    "orchestrator.imageRefNotPinned": (
        "Service images must pin a version; latest is not accepted. Use a fixed tag or "
        "digest, e.g. registry.example.com/vllm:v0.6.3"
    ),
    "orchestrator.instanceQuota": (
        "Instance limit reached ({max}) — release some or contact support to raise it"
    ),
    "orchestrator.invalidTransition": (
        "The instance's current state ({from}) does not allow this operation"
    ),
    "orchestrator.logsNeedsRunning": (
        "Logs are available only while the instance is running or stopping — a stopped "
        "instance has no pod logs; start it first"
    ),
    "orchestrator.logsUnavailable": "Failed to read logs — please retry shortly",
    "orchestrator.noCapacity": (
        "No allocatable capacity in “{model} × {pool} pool” right now — retry later or pick "
        "another spec"
    ),
    "orchestrator.noCapacityCpu": (
        "The {pool} pool has no allocatable CPU capacity right now. Try again later or pick "
        "another spec"
    ),
    "orchestrator.nodeUnreachable": (
        "The node hosting this instance's disk is unreachable and the instance cannot start "
        "for now — the platform is handling it. Data stays on that node's local disk; contact "
        "support if it persists"
    ),
    "orchestrator.periodNotEnabled": (
        "This spec does not support subscriptions; choose pay-as-you-go"
    ),
    "orchestrator.periodOnOnDemand": "Pay-as-you-go instances cannot carry a billing period",
    "orchestrator.periodRequired": "A subscription instance must have a billing period",
    "orchestrator.preemptNotSpot": "Only spot instances can be reclaimed",
    "orchestrator.realNameRequired": (
        "Regulations require identity verification before renting compute — complete it under "
        "Settings · Identity Verification first"
    ),
    "orchestrator.releaseNeedsStopped": "Stop the instance before releasing it",
    "orchestrator.renewNotSubscription": "Only subscription instances can be renewed",
    "orchestrator.renewReleased": (
        "The instance is being released or already released and cannot be renewed"
    ),
    "orchestrator.restartNeedsRunning": "Only running instances can be restarted",
    "orchestrator.serviceInstanceLifecycle": (
        "This instance belongs to an online service; stop, start or delete the service under "
        '"Services" instead'
    ),
    "orchestrator.servicePortReserved": (
        "Port {port} is reserved by the platform (22 for SSH, 8888 for JupyterLab) — move "
        "your service to another port"
    ),
    "orchestrator.spotNotEnabled": (
        "This spec is not offered on spot; choose pay-as-you-go or a subscription"
    ),
    "orchestrator.sshKeyRequired": (
        "Select at least one SSH public key (instances accept key login only)"
    ),
    "orchestrator.sshPortsExhausted": (
        "No SSH port available right now — try again later or contact support"
    ),
    "orchestrator.startNeedsStopped": "Only stopped instances can be started",
    "orchestrator.stateChangedRetry": (
        "The instance state was changed by another operation — refresh and retry"
    ),
    "orchestrator.stopNeedsRunning": "Only running instances can be stopped",
    "orchestrator.toOnDemandNotSpot": "Only spot instances can be switched to pay-as-you-go",
    "orchestrator.vcpuQuota": (
        "CPU instances would exceed your vCPU limit ({max} vCPU). Release one first or "
        "contact support to raise the limit"
    ),
    "services.apiKeyInvalid": "Invalid API key",
    "services.apiKeyNotFound": "API key not found",
    "services.apiKeyQuota": (
        "This service already has the maximum number of API keys ({max}); revoke unused keys first"
    ),
    "services.deleteNeedsStopped": "Stop the service before deleting it",
    "services.envKeepUnknown": (
        "Secret variables to keep do not exist on the current revision: {keys}"
    ),
    "services.notFound": "Service not found",
    "services.released": "The service has been deleted and can no longer be operated",
    "services.rolloutInFlight": (
        "The service is updating to a new version; try again once it finishes"
    ),
    "services.rolloutNeedsSettled": (
        "The current revision is still changing (deploying / stopping / releasing); update it "
        "once it settles"
    ),
    "services.rolloutSubscriptionUnsupported": (
        "Subscription services do not support revision updates yet"
    ),
    "tickets.messageLimitReached": (
        "This ticket has reached the limit of {max} replies — please open a new ticket to continue"
    ),
    "tickets.notFound": "Ticket not found",
    "tickets.openLimitReached": (
        "You have reached the limit of {max} open tickets — wait for a reply or close some "
        "before submitting a new one"
    ),
    "tickets.stateNotClosable": "A ticket in status {status} cannot be closed",
    "tickets.stateNotRepliable": (
        "This ticket is resolved or closed and no longer accepts replies — please open a new "
        "ticket if the issue persists"
    ),
    "tickets.stateNotResolvable": "A ticket in status {status} cannot be marked resolved",
}


def render_message(key: str, params: Mapping[str, Any] | None) -> str:
    """Render the English text for a key; unknown keys and missing params fall back and log."""
    template = MESSAGES.get(key)
    if template is None:
        get_logger("app.messages").warning("message_key_missing", key=key)
        return key
    try:
        return template.format(**(params or {}))
    except (KeyError, IndexError):
        get_logger("app.messages").warning("message_params_mismatch", key=key)
        return template
