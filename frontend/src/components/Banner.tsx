import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

export function Banner({ icon = "info", tone = "info", children }: { icon?: IconName; tone?: string; children: ReactNode }) {
  return (
    <div className="banner" role="note">
      <Icon name={icon} className={`ico--${tone}`} />
      <div>{children}</div>
    </div>
  );
}
