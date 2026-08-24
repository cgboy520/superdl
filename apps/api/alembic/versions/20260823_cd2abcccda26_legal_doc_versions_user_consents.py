"""F7 法务文档:legal_doc_versions(版本化 + 每 (doc_key,locale) 仅一条 published
的部分唯一索引)+ user_consents(注册同意存证)。

预置 zh-CN published v1:terms/privacy 正文来自用户端原静态页(迁移自洽,
正文内联,不 import 前端文件);deletion_notice 由 F4 注销弹窗 5 条说明扩写。
en-US 不预置(公开端点回落 zh-CN)。

Revision ID: cd2abcccda26
Revises: 9d3618e9e0db
Create Date: 2026-08-23 14:30:00.000000

"""

from collections.abc import Sequence
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision: str = "cd2abcccda26"
down_revision: str | None = "9d3618e9e0db"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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


def upgrade() -> None:
    op.create_table(
        "legal_doc_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("doc_key", sa.String(length=32), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("effective_note", sa.String(length=512), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_by", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name=op.f("ck_legal_doc_versions_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legal_doc_versions")),
        sa.UniqueConstraint(
            "doc_key",
            "locale",
            "version",
            name=op.f("uq_legal_doc_versions_doc_key_locale_version"),
        ),
    )
    op.create_index(
        "uq_legal_doc_versions_one_published",
        "legal_doc_versions",
        ["doc_key", "locale"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_index(
        op.f("ix_legal_doc_versions_status"), "legal_doc_versions", ["status"], unique=False
    )
    op.create_table(
        "user_consents",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("doc_key", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "accepted_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("client_ip", sa.String(length=45), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_consents")),
    )
    op.create_index(op.f("ix_user_consents_user_id"), "user_consents", ["user_id"], unique=False)

    # 预置 zh-CN published v1(发布人留空 = 系统预置)
    versions = sa.table(
        "legal_doc_versions",
        sa.column("doc_key", sa.String),
        sa.column("locale", sa.String),
        sa.column("version", sa.Integer),
        sa.column("title", sa.String),
        sa.column("content_md", sa.Text),
        sa.column("status", sa.String),
        sa.column("effective_note", sa.String),
        sa.column("published_at", sa.TIMESTAMP(timezone=True)),
    )
    op.bulk_insert(
        versions,
        [
            {
                "doc_key": doc_key,
                "locale": "zh-CN",
                "version": 1,
                "title": title,
                "content_md": content_md,
                "status": "published",
                "effective_note": "系统预置",
                "published_at": datetime.now(UTC),
            }
            for doc_key, title, content_md in [
                ("terms", TERMS_TITLE, TERMS_MD),
                ("privacy", PRIVACY_TITLE, PRIVACY_MD),
                ("deletion_notice", DELETION_NOTICE_TITLE, DELETION_NOTICE_MD),
            ]
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_consents_user_id"), table_name="user_consents")
    op.drop_table("user_consents")
    op.drop_index(op.f("ix_legal_doc_versions_status"), table_name="legal_doc_versions")
    op.drop_index("uq_legal_doc_versions_one_published", table_name="legal_doc_versions")
    op.drop_table("legal_doc_versions")
