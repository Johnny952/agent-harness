/**
 * The pool floor field of the start dialogs (*Start development*, *Start epic*;
 * ADR 226, ADR 227): optional, blank for the worker's default, and under it
 * either what the start would wait on or why the typed value is refused
 * (`lib/floor.ts:readFloor`).
 */
import { Input } from "@/components/ui/input";
import { DEFAULT_POOL_FLOOR_PCT, readFloor } from "@/lib/floor";

export function FloorField({
  value,
  onChange,
}: {
  value: string;
  onChange: (text: string) => void;
}) {
  const floor = readFloor(value);
  return (
    <>
      <label className="space-y-1 text-xs" data-testid="start-floor">
        Pool floor (% free on the primary account, optional)
        <Input
          aria-label="Pool floor"
          type="number"
          step={1}
          min={0}
          max={100}
          placeholder={`default ${DEFAULT_POOL_FLOOR_PCT}`}
          className="h-7 w-40 text-xs"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
      {floor.ok ? (
        <p className="text-xs text-muted-foreground" data-testid="start-floor-help">
          The start waits while the primary account has less than{" "}
          {floor.value === null ? `the default ${DEFAULT_POOL_FLOOR_PCT}%` : `${floor.value}%`}{" "}
          free.
        </p>
      ) : (
        <p className="text-xs text-destructive">{floor.why}</p>
      )}
    </>
  );
}
