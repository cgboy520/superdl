/** E.164 phone input: dial-code picker + national digits, emitting `+<dial><digits>` or undefined.
 *  Works as an antd Form.Item child (value / onChange). */

import { Input, Select, Space } from "antd";
import { useState } from "react";

import { composeE164, DIAL_CODES, splitE164 } from "../dialCodes";

export interface PhoneFieldProps {
  value?: string | null;
  onChange?: (value: string | undefined) => void;
  /** Restrict the picker to these dial codes (compliance profile); empty = all. */
  dialCodes?: readonly string[];
  disabled?: boolean;
  placeholder?: string;
  "aria-label"?: string;
  id?: string;
}

export function PhoneField({ value, onChange, dialCodes = [], disabled, placeholder, id, ...rest }: PhoneFieldProps) {
  const options = (dialCodes.length > 0 ? DIAL_CODES.filter((d) => dialCodes.includes(d.dial)) : DIAL_CODES).map(
    (d) => ({ value: d.dial, label: `+${d.dial} ${d.iso}` }),
  );
  const initial = value ? splitE164(value) : null;
  const [dial, setDial] = useState(initial?.dial ?? options[0]?.value ?? "1");
  const [national, setNational] = useState(initial?.national ?? "");
  const [seenValue, setSeenValue] = useState(value);
  if (value !== seenValue) {
    setSeenValue(value);
    const parts = value ? splitE164(value) : null;
    if (parts) {
      setDial(parts.dial);
      setNational(parts.national);
    }
  }

  const emit = (nextDial: string, nextNational: string) => {
    onChange?.(composeE164(nextDial, nextNational) ?? undefined);
  };

  return (
    <Space.Compact style={{ width: "100%" }}>
      <Select
        showSearch={{ optionFilterProp: "label" }}
        disabled={disabled}
        value={dial}
        options={options}
        style={{ width: 120 }}
        onChange={(d) => {
          setDial(d);
          emit(d, national);
        }}
      />
      <Input
        id={id}
        inputMode="tel"
        autoComplete="tel-national"
        disabled={disabled}
        placeholder={placeholder}
        aria-label={rest["aria-label"] ?? placeholder}
        value={national}
        maxLength={15}
        onChange={(e) => {
          const digits = e.target.value.replace(/\D/g, "");
          setNational(digits);
          emit(dial, digits);
        }}
      />
    </Space.Compact>
  );
}
