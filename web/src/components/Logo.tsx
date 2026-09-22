/** The supplied Bevro artwork, referenced by file, never redrawn.
 *
 * variant "primary": logo with tagline (light / dark files).
 * variant "lockup":  mark + wordmark, for the app shell.
 * variant "mark":    mark alone; below 48px the flat file is used, as the
 *                    brand README asks.
 *
 * Clear space follows the written rule (0.196 x mark height) rather than the
 * 0.548 token, which is awaiting review. */
import { useIsDark } from "../lib/theme";

type Variant = "primary" | "lockup" | "mark";

const FILES: Record<Variant, { light: string; dark: string; ratio: number }> = {
  primary: { light: "/brand/logo/bevro-logo-primary.svg", dark: "/brand/logo/bevro-logo-primary-dark.svg", ratio: 2439.92 / 1000 },
  lockup: { light: "/brand/logo/bevro-logo-lockup.svg", dark: "/brand/logo/bevro-logo-lockup-dark.svg", ratio: 2439.92 / 1000 },
  mark: { light: "/brand/logo/bevro-mark.svg", dark: "/brand/logo/bevro-mark-dark.svg", ratio: 852.15 / 1000 },
};
const MARK_FLAT = "/brand/logo/bevro-mark-flat.svg";
export const CLEARSPACE_RATIO = 0.196;

export default function Logo({ variant = "lockup", height, className = "" }: { variant?: Variant; height: number; className?: string }) {
  const dark = useIsDark();
  const spec = FILES[variant];
  let src = dark ? spec.dark : spec.light;
  if (variant === "mark" && height < 48) src = MARK_FLAT;
  // Every file's viewBox height is the mark height (1000 units), so clear
  // space is a fixed fraction of the rendered height.
  const clear = Math.round(height * CLEARSPACE_RATIO);
  return (
    <img
      src={src}
      alt="Bevro"
      width={Math.round(height * spec.ratio)}
      height={height}
      className={className}
      style={{ margin: clear, display: "block" }}
      draggable={false}
    />
  );
}
