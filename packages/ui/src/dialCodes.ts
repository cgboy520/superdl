/** Dial codes offered by PhoneField. The server validates the E.164 shape and the compliance
 *  profile's region rule; this list only drives the picker. */

export interface DialCode {
  dial: string;
  iso: string;
}

export const DIAL_CODES: readonly DialCode[] = [
  { dial: "1", iso: "US" },
  { dial: "7", iso: "RU" },
  { dial: "20", iso: "EG" },
  { dial: "27", iso: "ZA" },
  { dial: "30", iso: "GR" },
  { dial: "31", iso: "NL" },
  { dial: "32", iso: "BE" },
  { dial: "33", iso: "FR" },
  { dial: "34", iso: "ES" },
  { dial: "36", iso: "HU" },
  { dial: "39", iso: "IT" },
  { dial: "40", iso: "RO" },
  { dial: "41", iso: "CH" },
  { dial: "43", iso: "AT" },
  { dial: "44", iso: "GB" },
  { dial: "45", iso: "DK" },
  { dial: "46", iso: "SE" },
  { dial: "47", iso: "NO" },
  { dial: "48", iso: "PL" },
  { dial: "49", iso: "DE" },
  { dial: "52", iso: "MX" },
  { dial: "55", iso: "BR" },
  { dial: "60", iso: "MY" },
  { dial: "61", iso: "AU" },
  { dial: "62", iso: "ID" },
  { dial: "63", iso: "PH" },
  { dial: "64", iso: "NZ" },
  { dial: "65", iso: "SG" },
  { dial: "66", iso: "TH" },
  { dial: "81", iso: "JP" },
  { dial: "82", iso: "KR" },
  { dial: "84", iso: "VN" },
  { dial: "86", iso: "CN" },
  { dial: "90", iso: "TR" },
  { dial: "91", iso: "IN" },
  { dial: "852", iso: "HK" },
  { dial: "853", iso: "MO" },
  { dial: "886", iso: "TW" },
  { dial: "966", iso: "SA" },
  { dial: "971", iso: "AE" },
];

export const E164_RE = /^\+[1-9]\d{6,14}$/;

/** Split an E.164 value into the longest known dial code and the national part. */
export function splitE164(value: string): { dial: string; national: string } | null {
  if (!E164_RE.test(value)) return null;
  const digits = value.slice(1);
  const dial = [...DIAL_CODES]
    .map((d) => d.dial)
    .filter((d) => digits.startsWith(d))
    .sort((a, b) => b.length - a.length)[0];
  if (!dial) return null;
  return { dial, national: digits.slice(dial.length) };
}

/** Compose an E.164 value; returns null while the national part is empty or the result is invalid. */
export function composeE164(dial: string, national: string): string | null {
  const digits = national.replace(/\D/g, "");
  if (digits === "") return null;
  const value = `+${dial}${digits}`;
  return E164_RE.test(value) ? value : null;
}
