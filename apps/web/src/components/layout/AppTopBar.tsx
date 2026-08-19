/**
 * 全宽品牌顶栏(56px,渐变靛蓝,AutoDL「顶栏压侧栏」结构的上半)。
 * public:主页/公开页 —— 中部锚点导航 + 右侧 登录/免费注册(已登录换「进入控制台」)。
 * console:控制台 —— 右区(余额/通知/用户)由壳经 right 注入。
 */

import { brand, colorPrimary } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { Button, Space } from "antd";
import type { ReactNode } from "react";

import { useIsLoggedIn } from "../../stores/auth";
import { BrandLogo } from "./BrandLogo";

export function AppTopBar({
  variant,
  right,
}: {
  variant: "public" | "console";
  right?: ReactNode;
}) {
  const loggedIn = useIsLoggedIn();
  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 100,
        height: 56,
        background: brand.topBarBg,
        display: "flex",
        alignItems: "center",
        paddingInline: 24,
        gap: 20,
      }}
    >
      <Link to="/" style={{ display: "inline-flex", textDecoration: "none" }}>
        <BrandLogo variant="light" />
      </Link>
      {/* 布局收敛到 CSS 类:内联 display 会压过窄屏媒体查询的 display:none(390px 折行的根因) */}
      <nav className="topbar-nav-center">
        <Link to="/market" className="topbar-link">
          算力市场
        </Link>
        {variant === "public" && (
          <>
            <a href="/#pricing" className="topbar-link">
              GPU 价格
            </a>
            <a href="/#ranking" className="topbar-link">
              算力排名
            </a>
          </>
        )}
      </nav>
      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
        {variant === "public" ? (
          loggedIn ? (
            <Link to="/instances">
              <Button ghost>进入控制台</Button>
            </Link>
          ) : (
            <Space size={8}>
              <Link to="/login" className="topbar-link">
                登录
              </Link>
              <Link to="/login">
                <Button
                  style={{
                    background: "#fff",
                    color: colorPrimary,
                    borderColor: "transparent",
                    fontWeight: 600,
                  }}
                >
                  免费注册
                </Button>
              </Link>
            </Space>
          )
        ) : (
          right
        )}
      </div>
    </header>
  );
}
