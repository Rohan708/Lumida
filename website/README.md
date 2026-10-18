# Lumida website

The product's landing page: plain HTML, CSS and JavaScript, with no build step and no dependencies. Fonts load from Google Fonts; the page still looks right without them, using system fonts. The design follows the "Luminous Void" direction from Stitch (2026-10-18), rebuilt by hand.

| File | What it is |
|---|---|
| `index.html` | the page |
| `styles.css` | colours, type and layout (design tokens at the top) |
| `main.js` | small interactions: the hero console, the findings inbox, the question card, the terminal, scroll reveal, the sign-up form |
| `assets/mark.svg` | the logo mark: an aperture (a dark rounded square and a lens) with one point of light, the problem Lumida found |
| `assets/logo.svg` | mark + wordmark (the wordmark uses Geist; convert the text to outlines before using the file outside the site) |
| `assets/favicon.svg` | the mark simplified for 16 px |

## View it locally
```bash
python -m http.server 8765 --directory website
```
Then open http://localhost:8765.

## Before going live
- **Sign-up form:** set `EARLY_ACCESS_ENDPOINT` in `main.js` to a form service (e.g. Netlify Forms or Formspree) or your own API. Until then the form says, honestly, that sign-ups aren't open yet.
- **Claims:** every number on the page comes from the test lab and says so (15/15 relationships, 13/13 planted problems, 12 of 13 rules, 0 false alarms on quiet nights). Keep it that way. Don't add:
  - compliance claims (SOC 2, HIPAA, …) before they're true
  - a company name ("Inc.") before one exists
  - integrations that aren't built (only Slack is)
  - demo, docs or pricing links before those pages exist
- **Fonts:** to load nothing from other sites, download Geist and JetBrains Mono (both under the open font licence), put them in `assets/fonts/`, and replace the Google Fonts link with `@font-face` rules.
- **Hosting:** any static host works. The simplest are Netlify or Cloudflare Pages: connect the repo and set the publish directory to `website`.
- **Domain:** check availability of `lumida.dev` / `lumida.io` / `getlumida.com` before printing it anywhere.

## Brand
- **Colours:**
  - canvas `#09090B`; surfaces `#111113` / `#18181B`
  - hairlines `rgba(255,255,255,0.08)`
  - text white `#FFFFFF`, zinc `#E4E4E7` / `#A1A1AA` / `#71717A`
  - **champagne `#F5D0A9`**, the one warm accent, used sparingly for labels and "found it" moments
  - **lumen amber `#F59E0B`**, only for the point of light (logo, live dots)
- **Type:** Geist (headings and text, tight letter-spacing), JetBrains Mono (labels, numbers, code, terminal).
- **Tone:** calm and precise. Real numbers, no hype, no robots or brains.
