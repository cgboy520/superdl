/**
 * 全宽品牌顶栏(56px,渐变靛蓝)。
 * public:中部锚点导航 + 右侧 登录/免费注册(已登录换「进入控制台」)。
 * console:右区(余额/通知/用户)由壳经 right 注入。
 * 窄屏(≤768px)中部导航收进汉堡 Drawer;console 变体的 Drawer 置顶控制台 7 页分段,
 * 保持控制台页窄屏可达(侧栏在 lg 断点整体消失)。
 */

import { MenuOutlined, MoonOutlined, SunOutlined } from "@ant-design/icons";
import { brand, colorPrimary, fontSize } from "@superdl/ui";
import { LangSwitcher } from "@superdl/ui/components";
import { Link, useRouterState } from "@tanstack/react-router";
import { Button, Drawer, Grid, Space, theme } from "antd";
import { useTranslation } from "react-i18next";
import { useState, type CSSProperties, type ReactNode } from "react";

import { useIsLoggedIn } from "../../stores/auth";
import { useThemeMode, useThemeToggle } from "../../stores/theme";
import { BrandLogo } from "./BrandLogo";
import { CONSOLE_NAV, consoleNavSelected } from "./consoleNav";

/** 主题切换(状态存 localStorage,初值跟系统,见 stores/theme)。
 *  brand = 顶栏品牌渐变底上的白色图标;plain = 浅色容器底(登录页等)跟随文本色。 */
export function ThemeToggle({ variant = "brand" }: { variant?: "brand" | "plain" }) {
  const { t } = useTranslation();
  const mode = useThemeMode();
  const toggle = useThemeToggle();
  const dark = mode === "dark";
  const color = variant === "brand" ? "#fff" : undefined;
  return (
    <Button
      type="text"
      aria-label={dark ? t("topbar.themeToLight") : t("topbar.themeToDark")}
      title={dark ? t("topbar.themeToLight") : t("topbar.themeToDark")}
      icon={
        dark ? (
          <SunOutlined style={{ color, fontSize: fontSize.sectionTitle }} />
        ) : (
          <MoonOutlined style={{ color, fontSize: fontSize.sectionTitle }} />
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
  const { token } = theme.useToken();
  const [menuOpen, setMenuOpen] = useState(false);
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const navSelected = consoleNavSelected(pathname);
  // 抽屉导航链接:颜色走 antd token(暗色主题自适应),布局/hover/focus 在 styles.css 类里
  const drawerLinkStyle = (active: boolean): CSSProperties => ({
    display: "block",
    padding: "10px 12px",
    borderRadius: token.borderRadius,
    color: active ? token.colorPrimary : token.colorText,
    background: active ? token.colorPrimaryBg : undefined,
    fontWeight: active ? 600 : 400,
    textDecoration: "none",
    fontSize: fontSize.sectionTitle,
  });
  const drawerSectionStyle: CSSProperties = {
    padding: "4px 12px",
    fontSize: fontSize.caption,
    color: token.colorTextSecondary,
    letterSpacing: "0.04em",
  };
  return (
    <header className="app-topbar" style={{ background: brand.topBarBg }}>
      {/* 汉堡断点跟侧栏走:侧栏 lg(992)收为 0 宽,768~992 区间只能靠汉堡 Drawer 进控制台页 */}
      {!screens.lg && (
        <Button
          type="text"
          aria-label={t("topbar.openMenu")}
          icon={<MenuOutlined style={{ color: "#fff", fontSize: fontSize.pageTitle }} />}
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
            <Link to="/" hash="pricing" className="topbar-link">
              {t("topbar.pricing")}
            </Link>
            <Link to="/" hash="ranking" className="topbar-link">
              {t("topbar.ranking")}
            </Link>
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
            <div style={drawerSectionStyle}>{t("topbar.consoleSection")}</div>
            <nav style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 8 }}>
              {CONSOLE_NAV.map((n) => (
                <Link
                  key={n.key}
                  to={n.key}
                  className="drawer-link"
                  style={drawerLinkStyle(navSelected === n.key)}
                  onClick={() => setMenuOpen(false)}
                >
                  {t(n.labelKey)}
                </Link>
              ))}
            </nav>
            <div style={drawerSectionStyle}>{t("topbar.siteSection")}</div>
          </>
        )}
        <nav style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <Link
            to="/market"
            className="drawer-link"
            style={drawerLinkStyle(false)}
            onClick={() => setMenuOpen(false)}
          >
            {t("topbar.market")}
          </Link>
          <Link
            to="/"
            hash="pricing"
            className="drawer-link"
            style={drawerLinkStyle(false)}
            onClick={() => setMenuOpen(false)}
          >
            {t("topbar.pricing")}
          </Link>
          <Link
            to="/"
            hash="ranking"
            className="drawer-link"
            style={drawerLinkStyle(false)}
            onClick={() => setMenuOpen(false)}
          >
            {t("topbar.ranking")}
          </Link>
        </nav>
      </Drawer>
    </header>
  );
}
