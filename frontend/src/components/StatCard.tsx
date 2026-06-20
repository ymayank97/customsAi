import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "neutral" | "danger" | "success" | "warn" | "accent";
}) {
  const toneCls = {
    neutral: "text-ink",
    danger: "text-danger",
    success: "text-success",
    warn: "text-warn",
    accent: "text-accent",
  }[tone];

  return (
    <div className="surface p-4">
      <div className="text-[11px] font-medium uppercase tracking-wider text-ink-muted">
        {label}
      </div>
      <div className={cn("mt-2 font-serif text-[28px] leading-none tracking-tight", toneCls)}>
        {value}
      </div>
      {hint ? <div className="mt-1.5 text-[11px] text-ink-muted">{hint}</div> : null}
    </div>
  );
}
