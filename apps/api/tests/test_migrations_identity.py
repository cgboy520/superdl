"""Identity migration rehearsal on a scratch database: upgrade to the pre-identity head, seed
legacy phone-first rows, upgrade to head, then assert the backfill, the partial unique indexes and
a clean `alembic check`."""

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

API_DIR = Path(__file__).resolve().parent.parent
PRE_IDENTITY_HEAD = "e2b7c4d9a1f6"
SCRATCH_DB = "identity_migration_scratch"


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SUPERDL_DATABASE_URL": url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
async def scratch_url(pg_url: str) -> AsyncIterator[str]:
    admin = create_async_engine(pg_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{SCRATCH_DB}"'))
    await admin.dispose()
    yield pg_url.rsplit("/", 1)[0] + f"/{SCRATCH_DB}"
    admin = create_async_engine(pg_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
    await admin.dispose()


async def test_identity_migration_backfills_legacy_rows(scratch_url: str) -> None:
    up = _alembic(scratch_url, "upgrade", PRE_IDENTITY_HEAD)
    assert up.returncode == 0, up.stderr
    engine = create_async_engine(scratch_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (phone, status, low_balance_warn_hours, token_version,"
                " verification_status, id_name, id_number, id_number_hmac) VALUES"
                " ('13800000001', 'active', 24, 0, 'unverified', NULL, NULL, NULL),"
                " ('del:42:0123456789abcdef', 'deleted', 24, 3, 'unverified', NULL, NULL, NULL),"
                " ('13800000002', 'active', 24, 0, 'verified', '张三', '1101************34', 'abc')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO sms_codes (phone, code_hash, purpose, expires_at, attempts)"
                " VALUES ('13800000001', repeat('0', 64), 'register', now() + interval '5 min', 0)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO platform_settings (key, value) VALUES ('oncall_phone', '13900001111')"
            )
        )
    await engine.dispose()

    head = _alembic(scratch_url, "upgrade", "head")
    assert head.returncode == 0, head.stderr

    engine = create_async_engine(scratch_url)
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT phone, email, kyc_status, kyc_name, kyc_identity_hmac, kyc_provider,"
                    " kyc_verified_at IS NOT NULL AS has_verified_at FROM users ORDER BY id"
                )
            )
        ).all()
        assert [r.phone for r in rows] == ["+8613800000001", None, "+8613800000002"]
        assert all(r.email is None for r in rows)
        assert (rows[2].kyc_status, rows[2].kyc_name, rows[2].kyc_identity_hmac) == (
            "verified",
            "张三",
            "abc",
        )
        assert rows[2].kyc_provider == "aliyun_mobile3" and rows[2].has_verified_at
        assert rows[0].kyc_provider is None and not rows[0].has_verified_at

        tables = {
            r[0]
            for r in (
                await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables"
                        " WHERE table_schema = 'public'"
                    )
                )
            ).all()
        }
        assert "verification_codes" in tables and "sms_codes" not in tables
        assert (await conn.execute(text("SELECT count(*) FROM verification_codes"))).scalar() == 0
        columns = {
            r[0]
            for r in (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'verification_codes'"
                    )
                )
            ).all()
        }
        assert {"channel", "target", "purpose", "code_hash"} <= columns
        oncall = (
            await conn.execute(text("SELECT value FROM platform_settings WHERE key='oncall_phone'"))
        ).scalar()
        assert oncall == "+8613900001111"

        await conn.execute(
            text(
                "INSERT INTO users (status, low_balance_warn_hours, token_version, kyc_status)"
                " VALUES ('deleted', 24, 0, 'unverified'), ('deleted', 24, 0, 'unverified')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO users (email, status, low_balance_warn_hours, token_version,"
                " kyc_status) VALUES ('a@test.local', 'active', 24, 0, 'unverified')"
            )
        )
    async with engine.connect() as conn:
        with pytest.raises(Exception, match="uq_users_email"):
            await conn.execute(
                text(
                    "INSERT INTO users (email, status, low_balance_warn_hours, token_version,"
                    " kyc_status) VALUES ('A@TEST.LOCAL', 'active', 24, 0, 'unverified')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO users (email, status, low_balance_warn_hours, token_version,"
                    " kyc_status) VALUES ('a@test.local', 'active', 24, 0, 'unverified')"
                )
            )
    await engine.dispose()

    check = _alembic(scratch_url, "check")
    assert check.returncode == 0, check.stdout + check.stderr


async def test_legal_en_us_drafts_seeded_once(scratch_url: str) -> None:
    """Head seeds one en-US draft v1 per document next to the baseline's zh-CN published v1;
    re-running the statement (any en-US row present) inserts nothing."""
    up = _alembic(scratch_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    engine = create_async_engine(scratch_url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT doc_key, locale, version, status FROM legal_doc_versions"
                    " ORDER BY doc_key, locale"
                )
            )
        ).all()
    await engine.dispose()
    assert [tuple(r) for r in rows] == [
        ("deletion_notice", "en-US", 1, "draft"),
        ("deletion_notice", "zh-CN", 1, "published"),
        ("privacy", "en-US", 1, "draft"),
        ("privacy", "zh-CN", 1, "published"),
        ("terms", "en-US", 1, "draft"),
        ("terms", "zh-CN", 1, "published"),
    ]
