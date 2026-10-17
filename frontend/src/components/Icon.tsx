export type IconName = "check" | "pause" | "x" | "clock" | "alert" | "info" | "down" | "shield";

// 16x16 stroke icons. Status is never colour alone: every status colour ships with one of these and a label.
const PATHS: Record<IconName, string> = {
  check: "M3 8.5l3.2 3.2L13 4.8",
  pause: "M5.5 3.5v9M10.5 3.5v9",
  x: "M4 4l8 8M12 4l-8 8",
  clock: "M8 2.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11zM8 5v3.2l2 1.3",
  alert: "M8 2.5l6 10.5H2L8 2.5zM8 6.5v3M8 11.2v.1",
  info: "M8 2.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11zM8 7.2v4M8 4.9v.1",
  down: "M8 3v9M4.5 8.5L8 12l3.5-3.5",
  shield: "M8 2l5 2v4c0 3-2.2 5-5 6-2.8-1-5-3-5-6V4l5-2zM5.8 8.2l1.6 1.6 2.8-3",
};

export function Icon({ name, size = 14, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
