"""ssh_keys 指纹唯一约束收窄为 (user_id, fingerprint)

Revision ID: 9f2c4a6b8e15
Revises: e5a91c2f7d04
Create Date: 2026-08-22 12:35:00.000000

全局唯一指纹是跨租户枚举面(可探测他租户是否注册了某把公钥,也可占位阻断他租户
添加自己的钥匙),收窄为按用户唯一。存量查重:旧约束更严(全局唯一),其数据天然
满足新约束,无需清洗,直接换约束。
"""

from collections.abc import Sequence

from alembic import op


revision: str = "9f2c4a6b8e15"
down_revision: str | None = "e5a91c2f7d04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("uq_ssh_keys_fingerprint"), "ssh_keys", type_="unique")
    # 在线姿势:并发唯一索引 + USING INDEX 提升为约束(UNIQUE 无 NOT VALID 形态)
    with op.get_context().autocommit_block():
        op.create_index(
            "uq_ssh_keys_user_id_fingerprint",
            "ssh_keys",
            ["user_id", "fingerprint"],
            unique=True,
            postgresql_concurrently=True,
        )
    op.execute(
        "ALTER TABLE ssh_keys ADD CONSTRAINT uq_ssh_keys_user_id_fingerprint "
        "UNIQUE USING INDEX uq_ssh_keys_user_id_fingerprint"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_ssh_keys_user_id_fingerprint"), "ssh_keys", type_="unique")
    op.create_unique_constraint(op.f("uq_ssh_keys_fingerprint"), "ssh_keys", ["fingerprint"])
