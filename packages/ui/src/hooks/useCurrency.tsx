/** Deployment currency for money formatting. Apps mount `CurrencyProvider` with the ISO code from
 *  `/site-config` (web) or the platform-config `deployment` block (admin); until it is known the
 *  formatters render plain numbers. `setFallbackCurrency` exists for tests and stories. */
import { createContext, useContext, type ReactNode } from "react";

const CurrencyContext = createContext<string | null>(null);

let fallbackCurrency: string | null = null;

export function setFallbackCurrency(currency: string | null): void {
  fallbackCurrency = currency;
}

export function CurrencyProvider({ currency, children }: { currency: string | null; children: ReactNode }) {
  return <CurrencyContext.Provider value={currency}>{children}</CurrencyContext.Provider>;
}

export function useCurrency(): string | null {
  return useContext(CurrencyContext) ?? fallbackCurrency;
}
