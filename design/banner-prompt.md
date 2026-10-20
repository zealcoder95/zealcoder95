# Profile design

The header was made with the built-in Imagegen tool. Its checked profile-preview
image was used to restore it after an interrupted session. The final web asset
is `assets/zealcoder-banner-v2.webp`; the original banner is retained unchanged.

## Banner prompt

Reconstruct only the wide banner at the top of the supplied profile preview.
Keep the same 3:1 composition: navy grid, restrained cyan-violet data-node waves,
white typography on the left, and the original glossy black robotic ZealCat on
the right. Preserve purple and blue eyes, galaxy ears, headphones, the Z emblem,
orb tail, and complete anatomy. Use the exact text:

- zealcoder / Gizem Gülcü
- DATA SCIENCE
- & ARTIFICIAL INTELLIGENCE
- Explore data. Build models. Create useful AI.
- Electrical & Electronics Engineer

Keep all text legible and inside safe margins. Render only the final banner,
with no webpage frame, additional characters, badges or invented qualifications.

## Project cards and toolkit

Project cards use deterministic vector layouts rendered with Node.js and sharp.
Run `node scripts/render-profile-cards.cjs` after installing sharp. Exact data
values and figure provenance are in `assets/profile-project-sources.json`.
Technology and social badges are local SVG assets; brand paths are from Simple
Icons (CC0-1.0). Link, database and plotting symbols are original generic icons.
Source metadata is recorded in `assets/profile-badges.json`.
