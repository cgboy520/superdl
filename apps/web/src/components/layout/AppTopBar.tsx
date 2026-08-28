/**
 * 全宽品牌顶栏(56px,渐变靛蓝)。
 * public:中部锚点导航 + 右侧 登录/免费注册(已登录换「进入控制台」)。
 * console:右区(余额/通知/用户)由壳经 right 注入。
 * 窄屏(≤768px)中部导航收进汉堡 Drawer;console 变体的 Drawer 置顶控制台 7 页分段,
 * 保持控制台页窄屏可达(侧栏在 lg 断点整体消失)。
 */

import { MenuOutlined, MoonOutlined, SunOutlined } from "@ant-design/icons";
import { brand, colorPrimary } from "@superdl/ui";
import { Link, useRouterState } from "@tanstack/react-router";
import { Button, Drawer, Grid, Space } from "antd";
import { useTranslation } from "react-i18next";
import { useState, type ReactNode } from "react";

import { useIsLoggedIn } from "../../stores/auth";
import { useThemeMode, useThemeToggle } from "../../stores/theme";
import { BrandLogo } from "./BrandLogo";
import { CONSOLE_NAV, consoleNavSelected } from "./consoleNav";
import { LangSwitcher } from "./LangSwitcher";

/** 主题切换(顶栏右区图标钮;状态存 localStorage,初值跟系统,见 stores/theme)。 */
function ThemeToggle() {
  const { t } = useTranslation();
  const mode = useThemeMode();
  const toggle = useThemeToggle();
  const dark = mode === "dark";
  return (
    <Button
      type="text"
      aria-label={dark ? t("topbar.themeToLight") : t("topbar.themeToDark")}
      title={dark ? t("topbar.themeToLight") : t("topbar.themeToDark")}
      icon={
        dark ? (
          <SunOutlined style={{ color: "#fff", fontSize: 16 }} />
        ) : (
          <MoonOutlined style={{ color: "#fff", fontSize: 16 }} />
        )
      }
      onClick={toggle}
    />
  );
}

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
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const navSelected = consoleNavSelected(pathname);
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
        <ThemeToggle />
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
        {variant === "console" && loggedIn && (
          <>
            <div className="drawer-section">{t("topbar.consoleSection")}</div>
            <nav style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 8 }}>
              {CONSOLE_NAV.map((n) => (
                <Link
                  key={n.key}
                  to={n.key}
                  className={
                    navSelected === n.key ? "drawer-link drawer-link--active" : "drawer-link"
                  }
                  onClick={() => setMenuOpen(false)}
                >
                  {t(n.labelKey)}
                </Link>
              ))}
            </nav>
            <div className="drawer-section">{t("topbar.siteSection")}</div>
          </>
        )}
        <nav style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <Link to="/market" className="drawer-link" onClick={() => setMenuOpen(false)}>
            {t("topbar.market")}
          </Link>
          <a href="/#pricing" className="drawer-link" onClick={() => setMenuOpen(false)}>
            {t("topbar.pricing")}
          </a>
          <a href="/#ranking" className="drawer-link" onClick={() => setMenuOpen(false)}>
            {t("topbar.ranking")}
          </a>
        </nav>
      </Drawer>
    </header>
  );
}
