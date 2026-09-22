# Bevro brand package

Generated 2026-09-20 from `BEVRO_BRAND_BRIEF.md` and the supplied logo artwork.
The browsable manual — construction drawings, usage rules, colour, typography, voice, and the
record of what was measured versus decided — lives in the Bevro Brand Manual design system.

## What changed from what you supplied

The file you had as `bevro-full-logo-two-line-tagline.svg` was **not vector**: a 517 KB raster
PNG of the mark wrapped in SVG, with the wordmark and tagline as live Arial `<text>`. Everything
here is true vector with the wordmark converted to outlines, so nothing depends on a font being
installed and nothing pixelates.

The mark was **measured** from your raster, not redrawn by eye: circle and line fits to
0.28–0.55 px RMS, final path at **IoU 0.9843** against the original. Your original files are kept
under `legacy/` unchanged.

## Contents

```
logo/     11 SVG variants + PNG exports at 800 / 1600 / 2400 px
icon/     favicon (svg, dark svg, ico 16/32/48), apple-touch, PWA 192/512,
          maskable 512, OG 1200x630, site.webmanifest
tokens/   bevro-tokens.css  (--bv-* custom properties, light + dark)
          bevro-tokens.scss (variables + light/dark maps)
          bevro-tokens.json (source of truth, incl. logo gradient stops)
          tailwind.preset.js
          contrast-audit.json
legacy/   your original brief, SVG and PNG, untouched
```

## Which logo file

`bevro-logo-primary` on light, `-primary-dark` on dark, `bevro-logo-lockup` where the tagline
would be noise, `bevro-mark` alone where the name is already present, `bevro-mark-flat` below
48 px, and the `-mono-` pair for one-colour reproduction.

Clear space on all four sides = the height of the lowercase *e* in *Bevro*
(0.196 × the mark's height). Minimum 120 px wide for the full lockup, 20 px for the mark alone.

## Head snippet

```html
<link rel="icon" href="/icon/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/icon/favicon-dark.svg" type="image/svg+xml"
      media="(prefers-color-scheme: dark)">
<link rel="icon" href="/icon/favicon.ico" sizes="16x16 32x32 48x48">
<link rel="apple-touch-icon" href="/icon/apple-touch-icon.png">
<link rel="manifest" href="/icon/site.webmanifest">
<meta name="theme-color" content="#7A1837">
<meta property="og:image" content="/icon/og-image.png">
<link rel="stylesheet" href="/tokens/bevro-tokens.css">
```

The favicon is the **solid burgundy** mark, not the gradient: at 16 px the gradient's gold top
and mauve foot both lose contrast against a tab strip. App icons above 48 px carry the full
gradient mark.

## Accessibility

Every token pair named in the manual was checked against WCAG 2.1; all pass AA or better in both
themes. Two values were adjusted from their first draft to get there — `text-subtle` on light,
and the dark-theme accent. Figures are in `tokens/contrast-audit.json`.

## Open decisions

Five things were deliberately **not** decided for you — palette-versus-gradient reconciliation,
the wordmark typeface, the mark's 4.1° arm asymmetry, the icon set, and status colours. Each is
written up with its options in the manual's Provenance section.
