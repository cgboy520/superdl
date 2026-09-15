/** 顶栏:public 使用品牌渐变与锚点导航;console 使用中性底与 right 插槽。 */

import { MenuOutlined, MoonOutlined, SunOutlined } from "@ant-design/icons";
import { brand, brandInverseButtonStyle, fontSize, layout, space } from "@superdl/ui";
import { LangSwitcher } from "@superdl/ui/components";
import { Link, useRouterState } from "@tanstack/react-router";
import { Button, Drawer, Grid, Space, theme } from "antd";
import { useTranslation } from "react-i18next";
import { useState, type CSSProperties, type ReactNode } from "react";

import { useIsLoggedIn } from "../../stores/auth";
import { useThemeMode, useThemeToggle } from "../../stores/theme";
import { BrandLogo } from "./BrandLogo";
import { CONSOLE_HOME } from "./consoleNav";
import { ConsoleNavMenu } from "./ConsoleNavMenu";

export function ThemeToggle({ variant = "brand" }: { variant?: "brand" | "plain" }) {
  const { t } = useTranslation();
  const mode = useThemeMode();
  const toggle = useThemeToggle();
  const dark = mode === "dark";
  const color = variant === "brand" ? brand.onHero : undefined;
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

export function AppTopBar({ variant, right }: { variant: "public" | "console"; right?: ReactNode }) {
  const { t } = useTranslation();
  const loggedIn = useIsLoggedIn();
  const screens = Grid.useBreakpoint();
  const { token } = theme.useToken();
  const mode = useThemeMode();
  const [menuOpen, setMenuOpen] = useState(false);
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const isPublic = variant === "public";
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
  const barStyle: CSSProperties = isPublic
    ? { background: brand.topBarBg }
    : {
        background: token.colorBgContainer,
        borderBottom: `1px solid ${token.colorBorderSecondary}`,
      };
  const iconColor = isPublic ? brand.onHero : token.colorText;
  return (
    <header
      className={`app-topbar ${isPublic ? "app-topbar--brand" : "app-topbar--neutral"}`}
      style={{ ...barStyle, height: layout.topBarHeight }}
    >
      {!screens.lg && (
        <Button
          type="text"
          aria-label={t("topbar.openMenu")}
          icon={<MenuOutlined style={{ color: iconColor, fontSize: fontSize.pageTitle }} />}
          onClick={() => setMenuOpen(true)}
        />
      )}
      <Link to={isPublic ? "/" : CONSOLE_HOME} style={{ display: "inline-flex", textDecoration: "none" }}>
        <BrandLogo variant={isPublic || mode === "dark" ? "light" : "dark"} />
      </Link>
      {isPublic && (
        <nav className="topbar-nav-center" aria-label={t("nav.site")}>
          <Link to="/market" className="topbar-link">
            {t("topbar.market")}
          </Link>
          <Link to="/" hash="pricing" className="topbar-link">
            {t("topbar.pricing")}
          </Link>
          <Link to="/" hash="ranking" className="topbar-link">
            {t("topbar.ranking")}
          </Link>
          <Link to="/help" className="topbar-link">
            {t("topbar.help")}
          </Link>
        </nav>
      )}
      <div className="app-topbar-right">
        {isPublic ? (
          <>
            <ThemeToggle />
            <LangSwitcher />
            {loggedIn ? (
              <Link to={CONSOLE_HOME}>
                <Button ghost>{t("common.enterConsole")}</Button>
              </Link>
            ) : (
              <Space size={space.sm}>
                <Link to="/login" className="topbar-link">
                  {t("topbar.login")}
                </Link>
                <Link to="/login" search={{ mode: "register" }}>
                  <Button style={brandInverseButtonStyle}>{t("topbar.register")}</Button>
                </Link>
              </Space>
            )}
          </>
        ) : (
          right
        )}
      </div>
      <Drawer
        title={<BrandLogo />}
        placement="left"
        size={layout.navDrawerWidth}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        styles={{ body: { padding: isPublic ? undefined : 0 } }}
      >
        {isPublic ? (
          <nav style={{ display: "flex", flexDirection: "column", gap: 4 }} aria-label={t("nav.site")}>
            {(
              [
                { to: "/market", hash: undefined, label: t("topbar.market") },
                { to: "/", hash: "pricing", label: t("topbar.pricing") },
                { to: "/", hash: "ranking", label: t("topbar.ranking") },
                { to: "/help", hash: undefined, label: t("topbar.help") },
              ] as const
            ).map((l) => (
              <Link
                key={`${l.to}#${l.hash ?? ""}`}
                to={l.to}
                hash={l.hash}
                className="drawer-link"
                style={drawerLinkStyle(l.hash === undefined && pathname.startsWith(l.to))}
                onClick={() => setMenuOpen(false)}
              >
                {l.label}
              </Link>
            ))}
          </nav>
        ) : (
          <nav aria-label={t("nav.primary")}>
            <ConsoleNavMenu onNavigate={() => setMenuOpen(false)} />
          </nav>
        )}
      </Drawer>
    </header>
  );
}
