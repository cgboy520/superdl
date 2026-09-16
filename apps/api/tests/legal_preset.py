"""Preset legal documents seeded by conftest: the zh-CN published v1 of the baseline migration and
the en-US draft v1 templates of the en-US seed migration (same text as the migrations)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import now_utc
from app.modules.legal.models import LegalDocVersion

TERMS_TITLE = "SuperDL 用户协议"
TERMS_MD = """\
## 一、服务说明

SuperDL(下称「本平台」)向用户提供 GPU 容器实例租赁与配套存储服务,按量计费、按秒累计、
账单可自查。实例创建、计费起止均以平台实例事件流水为准。

## 二、账号与实名

用户须以本人手机号注册,并根据《网络安全法》要求在使用付费功能前完成实名认证。账号仅限本人使用,因转借、出售账号造成的损失由用户自行承担。

## 三、使用限制

禁止利用本平台从事任何违法活动;明确禁止虚拟货币挖矿、网络攻击、流量代理等行为,一经发现平台有权立即停止服务并冻结账号,情节严重的将报告有关部门。

## 四、计费与退款

实例按秒累计、按小时出账;关机即停 GPU 计费,数据盘按日计费。创建失败不产生费用。
余额不足时实例将自动关机并进入冻结与回收流程,具体参数以「费用中心」公示为准。

## 五、数据与备份

数据盘在实例释放后独立保留;欠费超过公示宽限期后平台有权回收并删除数据。用户应自行对重要数据保留副本,平台对因用户欠费回收导致的数据损失不承担责任。

## 六、服务变更与终止

平台因维护、升级需要暂停服务的,将提前公告。用户可随时释放实例并申请余额处理。本协议未尽事宜以平台公告为准。"""

PRIVACY_TITLE = "SuperDL 隐私政策"
PRIVACY_MD = """\
## 一、我们收集的信息

注册与登录:手机号、登录 IP、设备信息;实名认证:姓名与身份证号(仅用于三要素核验,
身份证号只保存脱敏形态,不存储原文);服务使用:SSH 公钥、实例操作记录、账单与资金流水。

## 二、信息的使用

上述信息仅用于:身份核验与账号安全(登录风控、验证码)、计费与开票、依法配合监管要求。我们不会将您的个人信息用于本政策载明目的之外的用途,不向第三方出售个人信息。

## 三、信息的存储与保护

数据存储于中华人民共和国境内。密码以 bcrypt 加盐哈希存储;身份证号仅存脱敏串;
全部管理操作留存审计日志。我们采取访问控制、传输加密(TLS)等措施保护您的信息。

## 四、信息的保留与删除

账号注销后,法律法规要求留存的日志与交易记录(如资金流水)将按法定期限保留,其余个人信息将删除或匿名化。验证码等临时数据在过期后定期清除。

## 五、您的权利

您可以查询、更正个人信息,设置余额预警阈值,或联系平台申请注销账号。对个人信息处理有异议的,可通过页脚联系方式与我们联系。"""

DELETION_NOTICE_TITLE = "数据删除说明"
DELETION_NOTICE_MD = """\
账号注销前请完整阅读本说明。注销一经执行不可撤销。

## 一、资源清空

名下实例须全部释放、数据盘须全部删除,否则注销将被驳回。实例释放后其本地盘数据不可恢复,请提前备份。

## 二、余额处理

钱包余额须先经「费用中心 · 退款」流程提现,余额非零的账号将被自动驳回。
退款到账后方可重新提交注销申请。

## 三、身份匿名化

注销执行后,手机号与实名信息将被匿名化,注销后不可恢复;该手机号可重新注册为新账号,但原账号数据不再关联。

## 四、法定保留

账本与账单按法定义务保留,但不再关联您的身份信息;依法要求留存的日志与交易记录在法定期限届满后删除或匿名化。

## 五、冷静期

提交申请后进入 7 天冷静期,期间可随时撤销;冷静期届满由管理员审核执行。"""

PRESET_DOCS: list[tuple[str, str, str]] = [
    ("terms", TERMS_TITLE, TERMS_MD),
    ("privacy", PRIVACY_TITLE, PRIVACY_MD),
    ("deletion_notice", DELETION_NOTICE_TITLE, DELETION_NOTICE_MD),
]

EN_TERMS_TITLE = "Terms of Service"
EN_TERMS_MD = """\
> Draft template seeded by the platform. Replace every bracketed placeholder and have counsel
> review the text before publishing.

## 1. The service

[Operator legal name] ("we", "the platform") rents GPU container instances and related storage
to registered users. Usage is metered per second and billed per hour; every charge is itemised in
the billing pages of your console. Instance lifecycle events recorded by the platform are the
authoritative record for when billing starts and stops.

## 2. Accounts

You must register with a working email address that you control and keep it current. Where the
platform indicates that a verified phone number or identity verification is required in your
region, you must complete it before using the affected features. Accounts are personal; you are
responsible for all activity under your credentials.

## 3. Acceptable use

You may not use the platform for unlawful activity, cryptocurrency mining, network attacks,
unsolicited traffic or proxying, or any activity that degrades the service for others. We may
suspend or terminate an account that violates these rules and may report unlawful activity to the
competent authorities.

## 4. Billing, balances and refunds

Fees are charged against your prepaid balance at the published prices. Stopping an instance stops
GPU billing; attached data disks are billed daily while they exist. Failed instance creations are
not charged. When a balance is exhausted the platform stops instances and starts the freeze and
reclamation process described in the console. Refunds of unused balance are handled through the
refund request flow and returned by the channel indicated there.

## 5. Data and backups

You are responsible for backing up your data. Data on released instances and deleted disks is
removed as described in the Data Deletion Notice. We do not access the contents of your instances
except as required to operate the service, investigate abuse or comply with law.

## 6. Availability and liability

The service is provided as is. To the extent permitted by law, our aggregate liability for any
claim is limited to the fees you paid for the affected service during the preceding [3] months.

## 7. Changes and termination

We may update these terms; material changes are announced in the console before they take effect.
You may close your account at any time through the account deletion flow.

## 8. Governing law

These terms are governed by the laws of [Jurisdiction]. Contact: [Contact email].
"""

EN_PRIVACY_TITLE = "Privacy Policy"
EN_PRIVACY_MD = """\
> Draft template seeded by the platform. Replace every bracketed placeholder and have counsel
> review the text before publishing.

## 1. Who we are

[Operator legal name] operates this platform and is the controller of the personal data described
here. Contact: [Contact email].

## 2. Data we collect

- Account data: email address, optional phone number, password hash, login timestamps and IP
  addresses.
- Identity verification data where required in your region: the name you submit, a masked copy of
  the identity number and a keyed digest of it; the plaintext number is never stored.
- Billing data: orders, payment channel references, balances, invoices and refund requests.
- Usage data: instance and disk lifecycle events, metering samples, support tickets and audit
  records of the actions you take in the console.

## 3. Why we process it

To provide and bill the service, to secure accounts and prevent abuse, to meet legal and tax
obligations, and to answer your support requests. We do not sell personal data.

## 4. Sharing

Data is shared with the processors that operate the service on our behalf: payment channels,
email and SMS delivery providers, human-verification and identity-verification providers, and our
hosting and monitoring infrastructure. Each processor only receives the data needed for its role.

## 5. Retention

Account data is kept while your account exists and deleted or anonymised after account deletion,
except for records we must keep for accounting, tax or legal reasons, which are kept for [N]
years. Instance data is removed as described in the Data Deletion Notice.

## 6. Security

Data is encrypted in transit; secrets and identity digests are encrypted or hashed at rest; access
is limited to staff who need it and every administrative action is audited.

## 7. Your rights

Depending on your jurisdiction you may access, correct, export or delete your data and object to
certain processing. Use the account settings or contact [Contact email].

## 8. Changes

We announce material changes to this policy in the console before they take effect.
"""

EN_DELETION_NOTICE_TITLE = "Data Deletion Notice"
EN_DELETION_NOTICE_MD = """\
> Draft template seeded by the platform. Replace every bracketed placeholder and have counsel
> review the text before publishing.

## Instances

Releasing an instance deletes its system disk immediately; the data cannot be recovered afterwards.

## Data disks

Deleting a data disk removes the volume and its data. Disks attached to an unpaid, frozen instance
are reclaimed after the freeze period shown in the console.

## Account deletion

You can request account deletion from the account settings. Requests wait for a cooling-off period
of [N] hours, during which you can cancel. Deletion requires that no instances or disks remain and
that any remaining balance has been refunded. When the request is executed, your login handles and
identity data are anonymised and the account can no longer sign in.

## What we keep

Orders, invoices, ledger entries and audit records are retained for [N] years to satisfy accounting
and legal obligations; they no longer reference your login handles.

## Backups

Platform backups are rotated within [N] days; data deleted from the live system ages out of backups
on that schedule.
"""

EN_EFFECTIVE_NOTE = "Seeded template — review with counsel before publishing"

EN_DRAFTS: list[tuple[str, str, str]] = [
    ("terms", EN_TERMS_TITLE, EN_TERMS_MD),
    ("privacy", EN_PRIVACY_TITLE, EN_PRIVACY_MD),
    ("deletion_notice", EN_DELETION_NOTICE_TITLE, EN_DELETION_NOTICE_MD),
]


async def _has_rows(session: AsyncSession, doc_key: str, locale: str) -> bool:
    return (
        await session.execute(
            select(LegalDocVersion.id)
            .where(LegalDocVersion.doc_key == doc_key, LegalDocVersion.locale == locale)
            .limit(1)
        )
    ).scalar_one_or_none() is not None


async def seed_preset_docs(session: AsyncSession) -> None:
    """zh-CN published v1 plus en-US draft v1 per document; a (doc_key, locale) with any row is
    skipped."""
    for doc_key, title, content_md in PRESET_DOCS:
        if await _has_rows(session, doc_key, "zh-CN"):
            continue
        session.add(
            LegalDocVersion(
                doc_key=doc_key,
                locale="zh-CN",
                version=1,
                title=title,
                content_md=content_md,
                status="published",
                effective_note="系统预置",
                published_at=now_utc(),
            )
        )
    for doc_key, title, content_md in EN_DRAFTS:
        if await _has_rows(session, doc_key, "en-US"):
            continue
        session.add(
            LegalDocVersion(
                doc_key=doc_key,
                locale="en-US",
                version=1,
                title=title,
                content_md=content_md,
                status="draft",
                effective_note=EN_EFFECTIVE_NOTE,
            )
        )
    await session.commit()
