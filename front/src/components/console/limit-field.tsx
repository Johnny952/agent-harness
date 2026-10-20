/**
 * The spending limit field of the start dialogs (*Start epic*, ADR 218;
 * *Start development*, ADR 230): optional, blank for no limit, in measured
 * tokens, and under it why the typed value is refused (`lib/limit.ts:readLimit`).
 * `children` are the dialog's own hints, shown between the field and the reason.
 */
import type { ReactNode } from "react";
import { Input } from "@/components/ui/input";
import type { LimitReading } from "@/lib/limit";

export function LimitField({
  value,
  onChange,
  reading,
  label = "Spending limit (measured tokens, optional)",
  testId,
  children,
}: {
  value: string;
  onChange: (text: string) => void;
  reading: LimitReading;
  label?: string | undefined;
  testId?: string;
  children?: ReactNode;
}) {
  return (
    <>
      <label className="space-y-1 text-xs" data-testid={testId}>
        {label}
        <Input
          aria-label="Spending limit"
          type="number"
          step={1}
          min={1}
          className="h-7 w-40 text-xs"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
      {children}
      {!reading.ok && <p className="text-xs text-destructive">{reading.why}</p>}
    </>
  );
}
