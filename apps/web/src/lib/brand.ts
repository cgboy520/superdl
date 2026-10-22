/** Site name and slogan: the admin-set values from /site-config, else the built-in name and copy. */

import type { SiteConfigOut } from "@superdl/api-client";

import { useSiteConfig } from "../api/queries";

export const DEFAULT_SITE_NAME = "SuperDL";

export interface SiteBrand {
  name: string;
  /** null = each page keeps its own built-in slogan copy. */
  slogan: string | null;
}

export function siteBrand(site: Pick<SiteConfigOut, "site_name" | "site_slogan"> | undefined): SiteBrand {
  return { name: site?.site_name || DEFAULT_SITE_NAME, slogan: site?.site_slogan || null };
}

export function useSiteBrand(): SiteBrand {
  const { data: site } = useSiteConfig();
  return siteBrand(site);
}
