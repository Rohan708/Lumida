# Lumida website

The public landing page (what Lumida is, how to install it, early-access sign-up): plain HTML, CSS and JavaScript, with no build step and no dependencies. The Lumida *app* is different: it runs on each customer's own machines. Fonts load from Google Fonts; the page still looks right without them, using system fonts. The design follows the "Luminous Void" direction from Stitch (2026-10-18), rebuilt by hand.

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

## Going live (Netlify, free)
`netlify.toml` at the repository root has everything needed: it publishes `website/` with no build step, and sets the security headers.
1. Sign up at https://netlify.com with GitHub, then *Add new site → Import an existing project → GitHub → Rohan708/Lumida*. The settings come from `netlify.toml`, so just press **Deploy**.
2. The site is live at `https://<name>.netlify.app`, and every push to `main` updates it.
3. **Sign-ups:** in Netlify, go to *Site configuration → Forms → Enable form detection*, then redeploy. "Request early access" emails then appear under **Forms → early-access**; you can get an email per sign-up under *Form notifications*. A hidden honeypot field keeps most spam out.
4. **Domain (optional):** buy one (check `lumida.dev`, `lumida.io` or `getlumida.com`), then *Domain management → Add a domain*. Netlify sets up HTTPS by itself.

Previewed locally, the form doesn't collect anything; it says so.

## Keeping it honest
Every number on the page comes from the test lab and says so (15/15 relationships, 13/13 planted problems, 12 of 13 rules, 0 false alarms on quiet nights). Don't add:
- compliance claims (SOC 2, HIPAA, …) before they're true
- a company name ("Inc.") before one exists
- integrations that aren't built (only Slack is)
- demo, docs or pricing links before those pages exist

Fonts load from Google Fonts. To load nothing from other sites, put Geist and JetBrains Mono (both under the open font licence) in `assets/fonts/` and replace the link with `@font-face` rules.

## Brand
- **Colours:**
  - canvas `#09090B`; surfaces `#111113` / `#18181B`
  - hairlines `rgba(255,255,255,0.08)`
  - text white `#FFFFFF`, zinc `#E4E4E7` / `#A1A1AA` / `#71717A`
  - **champagne `#F5D0A9`**, the one warm accent, used sparingly for labels and "found it" moments
  - **lumen amber `#F59E0B`**, only for the point of light (logo, live dots)
- **Type:** Geist (headings and text, tight letter-spacing), JetBrains Mono (labels, numbers, code, terminal).
- **Tone:** calm and precise. Real numbers, no hype, no robots or brains.
