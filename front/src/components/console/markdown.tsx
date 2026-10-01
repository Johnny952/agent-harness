/** Minimal markdown renderer — headings, quotes, lists, inline code. */
export function Markdown({ source }: { source: string }) {
  const lines = source.split("\n");
  return (
    <div className="space-y-1.5 text-xs leading-relaxed">
      {lines.map((line, i) => {
        if (!line.trim()) return <div key={i} className="h-1" />;
        if (line.startsWith("### "))
          return (
            <h4 key={i} className="pt-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              {line.slice(4)}
            </h4>
          );
        if (line.startsWith("## "))
          return (
            <h3 key={i} className="pt-1 text-xs font-semibold">
              {line.slice(3)}
            </h3>
          );
        if (line.startsWith("> "))
          return (
            <p key={i} className="border-l-2 border-warning/60 pl-2 text-warning">
              {line.slice(2)}
            </p>
          );
        if (line.startsWith("- "))
          return (
            <p key={i} className="pl-3 text-muted-foreground">
              • {line.slice(2)}
            </p>
          );
        return (
          <p key={i} className="text-foreground/90">
            {inline(line)}
          </p>
        );
      })}
    </div>
  );
}

function inline(text: string) {
  const parts = text.split(/(`[^`]+`)/g);
  return parts.map((p, i) =>
    p.startsWith("`") && p.endsWith("`") ? (
      <code key={i} className="mono rounded-sm bg-surface-2 px-1 py-[1px] text-[11px]">
        {p.slice(1, -1)}
      </code>
    ) : (
      <span key={i}>{p}</span>
    ),
  );
}
