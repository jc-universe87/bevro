/** Tailwind config. Colours are the Bevro semantic tokens (CSS custom
 * properties from tokens/bevro-tokens.css) so that light/dark switching is
 * handled by the token sheet, not by per-component dark: variants. The
 * supplied preset is loaded as-is; components use the semantic names below,
 * never the raw palette. */
const preset = require("./bevro.tailwind.preset.cjs");

module.exports = {
  presets: [preset],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bv-bg)",
        surface: "var(--bv-surface)",
        sunken: "var(--bv-surface-sunken)",
        line: "var(--bv-border)",
        "line-strong": "var(--bv-border-strong)",
        ink: "var(--bv-text)",
        muted: "var(--bv-text-muted)",
        subtle: "var(--bv-text-subtle)",
        accent: "var(--bv-accent)",
        "accent-hover": "var(--bv-accent-hover)",
        "accent-fg": "var(--bv-accent-fg)",
        "accent-subtle": "var(--bv-accent-subtle)",
        focus: "var(--bv-focus)",
        highlight: "var(--bv-highlight)",
      },
      fontSize: {
        xs: ["var(--bv-size-xs)", { lineHeight: "var(--bv-leading-normal)" }],
        sm: ["var(--bv-size-sm)", { lineHeight: "var(--bv-leading-normal)" }],
        base: ["var(--bv-size-base)", { lineHeight: "var(--bv-leading-normal)" }],
        lg: ["var(--bv-size-lg)", { lineHeight: "var(--bv-leading-snug)" }],
        xl: ["var(--bv-size-xl)", { lineHeight: "var(--bv-leading-snug)" }],
        "2xl": ["var(--bv-size-2xl)", { lineHeight: "var(--bv-leading-tight)" }],
        "3xl": ["var(--bv-size-3xl)", { lineHeight: "var(--bv-leading-tight)" }],
      },
      maxWidth: { prompt: "40rem", page: "52rem" },
    },
  },
  plugins: [],
};
