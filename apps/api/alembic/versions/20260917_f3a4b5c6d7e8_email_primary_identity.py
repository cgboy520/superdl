"""Email becomes the primary login handle; phone becomes optional E.164; KYC columns renamed;
sms_codes becomes verification_codes.

Pre-flight on a populated database (must return no rows, otherwise fix the data first):
    SELECT id, phone FROM users WHERE phone NOT LIKE 'del:%' AND phone !~ '^1[3-9][0-9]{9}$';
Existing mainland-China numbers are prefixed with +86; anonymized placeholders become NULL.
verification_codes rows are dropped (codes live five minutes; the release is stop-the-world).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "e2b7c4d9a1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column(
        "users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("kyc_provider", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("kyc_ref", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("kyc_verified_at", sa.DateTime(timezone=True), nullable=True))

    op.drop_constraint("uq_users_phone", "users", type_="unique")
    op.alter_column("users", "phone", existing_type=sa.String(length=40), nullable=True)
    op.execute("UPDATE users SET phone = NULL WHERE phone LIKE 'del:%'")
    op.execute("UPDATE users SET phone = '+86' || phone WHERE phone ~ '^1[3-9][0-9]{9}$'")
    op.alter_column(
        "users",
        "phone",
        existing_type=sa.String(length=40),
        type_=sa.String(length=20),
        existing_nullable=True,
    )
    op.create_index(
        "uq_users_phone",
        "users",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("phone IS NOT NULL"),
    )
    op.create_index(
        "uq_users_email",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )

    op.alter_column("users", "verification_status", new_column_name="kyc_status")
    op.alter_column("users", "id_name", new_column_name="kyc_name")
    op.alter_column(
        "users",
        "kyc_name",
        existing_type=sa.String(length=64),
        type_=sa.String(length=128),
        existing_nullable=True,
    )
    op.alter_column("users", "id_number", new_column_name="kyc_identity_masked")
    op.alter_column("users", "id_number_hmac", new_column_name="kyc_identity_hmac")
    op.execute("ALTER INDEX ix_users_id_number_hmac RENAME TO ix_users_kyc_identity_hmac")
    op.execute(
        "UPDATE users SET kyc_provider = 'aliyun_mobile3', kyc_verified_at = created_at "
        "WHERE kyc_status = 'verified'"
    )

    op.execute("DELETE FROM sms_codes")
    op.rename_table("sms_codes", "verification_codes")
    op.execute("ALTER INDEX pk_sms_codes RENAME TO pk_verification_codes")
    op.execute("ALTER SEQUENCE sms_codes_id_seq RENAME TO verification_codes_id_seq")
    op.drop_index("ix_sms_codes_phone", table_name="verification_codes")
    op.alter_column("verification_codes", "phone", new_column_name="target")
    op.alter_column(
        "verification_codes",
        "target",
        existing_type=sa.String(length=20),
        type_=sa.String(length=254),
        existing_nullable=False,
    )
    op.alter_column(
        "verification_codes",
        "purpose",
        existing_type=sa.String(length=16),
        type_=sa.String(length=24),
        existing_nullable=False,
    )
    op.add_column("verification_codes", sa.Column("channel", sa.String(length=8), nullable=False))
    op.create_index(
        "ix_verification_codes_channel_target", "verification_codes", ["channel", "target"]
    )

    op.execute(
        "UPDATE platform_settings SET value = '+86' || value "
        "WHERE key = 'oncall_phone' AND value ~ '^1[3-9][0-9]{9}$'"
    )


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
