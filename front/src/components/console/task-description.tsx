/**
 * A task's `description.md` on its page, cut into the sections the dispatcher
 * reads (`lib/description.ts`, ADR 139). A description that does not parse is
 * shown whole, as written, with the dispatcher's reason above it: the page
 * never hides text because it broke a rule.
 */
import { Absent } from "@/components/console/primitives";
import { Markdown } from "@/components/console/markdown";
import { parseDescription } from "@/lib/description";

export function DescriptionSections({ description }: { description: string | null }) {
  if (description === null) return <Absent label="no description" />;
  if (typeof description !== "string" || !description.trim()) {
    return <Absent label="an empty description" />;
  }
  const parsed = parseDescription(description);
  if (!parsed.ok) {
    return (
      <div className="space-y-2" data-testid="description-raw">
        <p className="text-[11px] italic text-muted-foreground">
          Shown as written: the description {parsed.reason}.
        </p>
        <pre className="whitespace-pre-wrap break-words text-xs">{description}</pre>
      </div>
    );
  }
  return (
    <div className="space-y-3" data-testid="description-sections">
      {parsed.sections.map((s) => (
        <div key={s.name}>
          <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            {s.name}
          </h3>
          <Markdown source={s.body} />
        </div>
      ))}
    </div>
  );
}
