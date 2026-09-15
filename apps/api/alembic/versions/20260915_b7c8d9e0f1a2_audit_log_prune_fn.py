"""审计保洁函数 audit_log_prune(days):SECURITY DEFINER,只删早于 days 天的行,days 下限 30。
应用角色对 audit_log 无 DELETE 权限,保洁只经此函数;执行权由 deploy/pg/roles.sql 授。"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "a1f2c3d4e5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FN = """
CREATE OR REPLACE FUNCTION audit_log_prune(days integer) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE deleted bigint;
BEGIN
  IF days IS NULL OR days < 30 THEN
    RAISE EXCEPTION 'audit_log_prune: days must be >= 30 (got %)', days;
  END IF;
  DELETE FROM audit_log WHERE created_at < now() - make_interval(days => days);
  GET DIAGNOSTICS deleted = ROW_COUNT;
  RETURN deleted;
END;
$$;
"""


def upgrade() -> None:
    op.execute(_FN)
    op.execute("REVOKE ALL ON FUNCTION audit_log_prune(integer) FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'superdl_app') THEN
            GRANT EXECUTE ON FUNCTION audit_log_prune(integer) TO superdl_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
