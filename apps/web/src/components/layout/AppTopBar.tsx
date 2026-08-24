/**
 * 全宽品牌顶栏(56px,渐变靛蓝)。
 * public:中部锚点导航 + 右侧 登录/免费注册(已登录换「进入控制台」)。
 * console:右区(余额/通知/用户)由壳经 right 注入。
 * 窄屏(≤768px)中部导航收进汉堡 Drawer,保持可达。
 */

import { MenuOutlined } from "@ant-design/icons";
import { brand, colorPrimary } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { Button, Drawer, Grid, Space } from "antd";
import { useTranslation } from "react-i18next";
import { useState, type ReactNode } from "react";

import { useIsLoggedIn } from "../../stores/auth";
import { BrandLogo } from "./BrandLogo";
import { LangSwitcher } from "./LangSwitcher";

export function AppTopBar({
  variant,
  right,
}: {
  variant: "public" | "console";
  right?: ReactNode;
}) {
  const { t } = useTranslation();
  const loggedIn = useIsLoggedIn();
  const screens = Grid.useBreakpoint();
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <header className="app-topbar" style={{ background: brand.topBarBg }}>
      {!screens.md && (
        <Button
          type="text"
          aria-label={t("topbar.openMenu")}
          icon={<MenuOutlined style={{ color: "#fff", fontSize: 18 }} />}
          onClick={() => setMenuOpen(true)}
        />
      )}
      <Link to="/" style={{ display: "inline-flex", textDecoration: "none" }}>
        <BrandLogo variant="light" />
      </Link>
      {/* 内联 display 会压过窄屏媒体查询的 display:none,布局须走 CSS 类 */}
      <nav className="topbar-nav-center">
        <Link to="/market" className="topbar-link">
          {t("topbar.market")}
        </Link>
        {variant === "public" && (
          <>
            <a href="/#pricing" className="topbar-link">
              {t("topbar.pricing")}
            </a>
            <a href="/#ranking" className="topbar-link">
              {t("topbar.ranking")}
            </a>
          </>
        )}
      </nav>
      <div className="app-topbar-right">
        <LangSwitcher />
        {variant === "public" ? (
          loggedIn ? (
            <Link to="/instances">
              <Button ghost>{t("common.enterConsole")}</Button>
            </Link>
          ) : (
            <Space size={8}>
              <Link to="/login" className="topbar-link">
                {t("topbar.login")}
              </Link>
              <Link to="/login" search={{ mode: "register" }}>
                <Button
                  style={{
                    background: "#fff",
                    color: colorPrimary,
                    borderColor: "transparent",
                    fontWeight: 600,
                  }}
                >
                  {t("topbar.register")}
                </Button>
              </Link>
            </Space>
          )
        ) : (
          right
        )}
      </div>
      <Drawer
        title={<BrandLogo />}
        placement="left"
        size={260}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
      >
        <nav style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <Link to="/market" className="drawer-link" onClick={() => setMenuOpen(false)}>
            {t("topbar.market")}
          </Link>
          {variant === "public" && (
            <>
              <a href="/#pricing" className="drawer-link" onClick={() => setMenuOpen(false)}>
                {t("topbar.pricing")}
              </a>
              <a href="/#ranking" className="drawer-link" onClick={() => setMenuOpen(false)}>
                {t("topbar.ranking")}
              </a>
            </>
          )}
        </nav>
      </Drawer>
    </header>
  );
}
