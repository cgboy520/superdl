-- 应用角色:非 superuser、非 owner、只有 DML。以 owner(superdl)身份对 superdl 库执行一次(幂等,已存在则刷新口令与权限);
-- 口令经 psql 变量传入:psql -v app_password="'...'" -v ON_ERROR_STOP=1 -f roles.sql
SELECT format('CREATE ROLE superdl_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L', :app_password)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'superdl_app') \gexec
SELECT format('ALTER ROLE superdl_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L', :app_password)
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'superdl_app') \gexec

GRANT CONNECT ON DATABASE superdl TO superdl_app;
GRANT USAGE ON SCHEMA public TO superdl_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO superdl_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO superdl_app;
-- 追加式表:资金流水不许改删;审计行不许改(365 天保留期由 worker DELETE,保留 DELETE)
REVOKE UPDATE, DELETE ON balance_ledger FROM superdl_app;
REVOKE UPDATE ON audit_log FROM superdl_app;
-- 迁移(以 superdl 身份跑)新建的表 / 序列自动授权
ALTER DEFAULT PRIVILEGES FOR ROLE superdl IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO superdl_app;
ALTER DEFAULT PRIVILEGES FOR ROLE superdl IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO superdl_app;
