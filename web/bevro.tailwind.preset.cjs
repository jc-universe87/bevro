/** Bevro Tailwind preset. `presets: [require('./tokens/tailwind.preset.js')]` */
module.exports = {
  theme: {
    extend: {
      colors: {
        bevro: {
                "burgundy": "#7A1837",
                "burgundy-deep": "#5E1029",
                "mauve": "#A86D82",
                "mauve-soft": "#C596A7",
                "gold": "#E7C080",
                "charcoal": "#17181C",
                "offwhite": "#F8F7F4"
        },
        neutralbv: {
                "50": "#F8F7F4",
                "100": "#EDECE9",
                "200": "#DFDEDC",
                "300": "#CBCAC9",
                "400": "#A7A7A6",
                "500": "#717172",
                "600": "#646465",
                "700": "#48494C",
                "800": "#303134",
                "900": "#17181C"
        },
      },
      fontFamily: { sans: ["system-ui", "-apple-system", "BlinkMacSystemFont", "\"Segoe UI\"", "Roboto", "Helvetica", "Arial", "sans-serif"], mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "\"Liberation Mono\"", "monospace"] },
      borderRadius: {
              "sm": "6px",
              "md": "10px",
              "lg": "16px",
              "xl": "24px",
              "pill": "999px"
      },
      boxShadow: {
              "sm": "0 1px 2px rgba(23,24,28,.06)",
              "md": "0 2px 8px rgba(23,24,28,.08)",
              "lg": "0 8px 28px rgba(23,24,28,.10)"
      },
      letterSpacing: { tagline: '0.16em', wordmark: '-0.031em' },
    },
  },
};
