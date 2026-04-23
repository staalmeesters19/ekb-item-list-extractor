# EKB / Agyle — Frontend Brand Brief

## 1. Agyle Brand Findings

**Agyle (agyle.nl)** — The public site returned only its meta title to automated fetchers ("AI in de Bouw- en Installatiesector | Automatisering & Training"). Stylesheets, logo tags, and inline colors were not retrievable via fetch — I cannot verify Agyle's palette with confidence and will not invent hex codes.

**EKB (ekb.nl)** — Verified directly from the official SVG logo at `https://www.ekb.nl/wp-content/uploads/2019/02/ekb-full-color.svg`:

- **EKB Dark Blue — `#003478`** (primary mark, wordmark body)
- **EKB Lime/Chartreuse — `#b6be00`** (accent bars in the logo)

White-on-dark variant: `https://www.ekb.nl/wp-content/uploads/2019/02/ekb-white.svg`

Visual patterns observed on ekb.nl: industrial/engineering tone, modular solution cards (Machine Vision, Robotics, Data Analytics, Motion & Control), footer notes a "Group Eiffage" affiliation. Overall positioning is serious, technical, B2B.

Because EKB has its own verified mark and the app is the "EKB Item List" tool, I recommend anchoring the frontend to the EKB palette rather than guessing Agyle's.

## 2. SaaS Design Principles (Linear, Vercel, Stripe, Notion)

**Linear** — Feels premium through restraint: near-monochrome UI, a single saturated accent (indigo/purple), tight type scale (Inter Display), generous negative space, fast micro-interactions. Shadows are almost absent; hierarchy comes from weight and subtle borders (`~1px` at low opacity).

**Vercel** — Pure black/white with geometric precision. Geist Sans + Geist Mono. Uses thin hairline borders (`#e5e5e5` light, `#2a2a2a` dark) instead of shadows. Hovers are instant opacity/border shifts, never bouncy.

**Stripe** — Warmer and more chromatic. Deep indigo (`~#635bff`) over off-white (`#f6f9fc`). Soft, diffuse shadows on cards (large blur, low opacity), gradients reserved for hero moments. Typography is confident sans-serif, 1.5+ line-height on body.

**Notion** — Neutral/paper-like (off-white `~#fbfbfa`, ink `#37352f`). Almost no color until a user adds it. Teaches that a "canvas" feel (muted background + strong typographic hierarchy) reads as calm and professional — ideal for a document/item-list tool.

Common thread for a B2B engineering tool: **neutral canvas, one confident accent, hairline borders, modest shadows, tight type, no decorative gradients.**

## 3. Recommended Color Palette

Anchored to verified EKB brand colors. Dark-blue primary, lime as the distinctive accent tying back to the logo.

| Role | Name | Hex |
|---|---|---|
| Primary (brand) | EKB Deep Blue | `#003478` |
| Primary hover/active | Deep Blue 600 | `#002A63` |
| Secondary | Slate 700 | `#1F2937` |
| Surface / canvas | Off-white | `#F8FAFC` |
| Surface elevated | Pure white | `#FFFFFF` |
| Border / hairline | Slate 200 | `#E2E8F0` |
| Muted text | Slate 500 | `#64748B` |
| Body text | Slate 900 | `#0F172A` |
| Accent (EKB tie-in) | EKB Lime | `#B6BE00` |
| Accent soft (tint) | Lime 100 | `#F1F4C8` |
| Success | Emerald 600 | `#059669` |
| Warning | Amber 500 | `#F59E0B` |
| Error | Rose 600 | `#E11D48` |
| Info | Sky 600 | `#0284C7` |

Use the lime sparingly — pull-quote highlights, active-state underline, "verified" chips. Keep the blue for primary buttons, active nav, and selection states.

## 4. Logo & Fonts

**Logo asset**: reference `https://www.ekb.nl/wp-content/uploads/2019/02/ekb-full-color.svg` on light surfaces. For any dark header/sidebar use `https://www.ekb.nl/wp-content/uploads/2019/02/ekb-white.svg`. SVG — scales cleanly in Streamlit. Do not download; reference by URL or ask the EKB team for the official asset pack.

**Font**: **Inter** (variable) for UI. Safe default, excellent Latin + Dutch glyph coverage, pairs well with the engineering tone. Google Fonts: `https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap`.

- Body: Inter 400, 14–15px, line-height 1.55
- UI/labels: Inter 500, 13px, letter-spacing `-0.005em`
- Headings: Inter 600, tight tracking (`-0.02em` on H1/H2)
- Numerics / PDF coordinates / IDs: **JetBrains Mono** 13px

## 5. Visual Direction

Think "engineering cockpit, not marketing site." A calm off-white canvas (`#F8FAFC`) with content living on crisp white cards separated by single-pixel slate-200 hairlines — no heavy drop shadows, at most a `0 1px 2px rgba(15,23,42,0.04)` lift on hover. Primary actions carry EKB deep blue; the lime appears only as a deliberate accent so it reads as brand, not decoration. Typography does the heavy lifting: Inter at tight tracking for density, JetBrains Mono for coordinates and IDs from the electrical drawings. Dense information (item tables, PDF previews, detected components) is the point — the UI should recede and let the data feel authoritative, trustworthy, and fast, closer to Linear or Vercel than to a consumer dashboard.
