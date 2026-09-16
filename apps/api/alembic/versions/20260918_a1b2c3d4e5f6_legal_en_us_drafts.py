"""Seed en-US draft templates (terms, privacy, deletion_notice) as version 1 drafts so an
international deployment starts with reviewable English text; nothing is published. Documents that
already have any en-US row are left alone.

Revision ID: a1b2c3d4e5f6
Revises: f3a4b5c6d7e8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TERMS_TITLE = "Terms of Service"
TERMS_MD = """\
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

PRIVACY_TITLE = "Privacy Policy"
PRIVACY_MD = """\
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

DELETION_NOTICE_TITLE = "Data Deletion Notice"
DELETION_NOTICE_MD = """\
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

EFFECTIVE_NOTE = "Seeded template — review with counsel before publishing"

_INSERT = sa.text(
    "INSERT INTO legal_doc_versions"
    " (doc_key, locale, version, title, content_md, status, effective_note)"
    " SELECT CAST(:doc_key AS VARCHAR(32)), 'en-US', 1, CAST(:title AS VARCHAR(128)),"
    " CAST(:content_md AS TEXT), 'draft', CAST(:note AS VARCHAR(512))"
    " WHERE NOT EXISTS (SELECT 1 FROM legal_doc_versions"
    " WHERE doc_key = CAST(:doc_key AS VARCHAR(32)) AND locale = 'en-US')"
)


def upgrade() -> None:
    bind = op.get_bind()
    for doc_key, title, content_md in (
        ("terms", TERMS_TITLE, TERMS_MD),
        ("privacy", PRIVACY_TITLE, PRIVACY_MD),
        ("deletion_notice", DELETION_NOTICE_TITLE, DELETION_NOTICE_MD),
    ):
        bind.execute(
            _INSERT,
            {"doc_key": doc_key, "title": title, "content_md": content_md, "note": EFFECTIVE_NOTE},
        )


def downgrade() -> None:
    raise RuntimeError("downgrade is not supported: restore from backup")
