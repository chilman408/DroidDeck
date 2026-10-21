# DroidDeck Logo

The user selected the wordless Android/browser/casting concept on 2026-10-17.
`droiddeck-logo.png` is its transparent production asset. It replaces the former
DD monogram in About and browser/Apple-touch icons without changing HTML or
media/display code. Two About-logo CSS declarations add a white background and
8px radius so the dark frame remains visible on the app's dark theme. No play
symbol or embedded wordmark. Real generated alpha
is preserved; no manual image editing/resizing/compositing. A logo PNG is not
a streaming video fallback.

## Current Asset

SHA256: `ea8e12af127eba9bc533d805770e06e3da9f7a0f277f36a2eac574c363fdeb29`.
1254x1254, 8-bit RGBA PNG, 603649 bytes. The selected source concept was
`exec-33ebf9ea-e1e8-4224-9b4d-396c9a446ff6.png`. Built-in image tool transparent
preparation output: `exec-5278a5a4-9cc7-42b9-89fa-0a2eb3f40d43.png`; final
edge-cleanup output: `exec-880b260b-d376-4738-be24-427366f3f0f0.png`.
Only the final image is consumed by source and portable builds.

The preparation prompt preserved the approved browser outline/tab/address bar,
Android head/antennae/eyes and lower-left casting dot/two arcs, removed white
background and eye interiors to transparent alpha, and reframed onto a square
with modest balanced padding. Final cleanup retained the arrangement and asked
for clean flat green/near-black shapes, no stray pixels, text, play symbol or
new elements. This is a raster logo, not a claim of editable vector geometry.

`logo-receipt.json` pins the current asset. `node runtime/branding/logo.js`
verifies bounded single-link regular bytes, PNG header/dimensions/alpha format
and SHA256. Portable frontend CLI builds verify the receipt BEFORE output
creation and enforce the pin again on bytes actually copied into the bundle.
Source integrity is not publisher authenticity or installer authority.

The NEW fixed `--droiddeck-logo` profile changes only the PNG and that About-logo
styling, with an independent code-only backup/rollback; no service restart or
preferences restore.
The old first-branding deployment must NOT be replayed. No downloadable installer
or release is implied by this identity update.

## Historical Monogram

Before 2026-10-17, the following asset/prompt applied (not the current logo).

`droiddeck-logo.png` is the original transparent PNG generated with the built-in
OpenAI image-generation tool on 2026-10-11. The generated alpha is preserved;
no resizing, compositing or manual image edits were applied. It is used in About
and as the browser/Apple-touch icon. Product name approved by the user.

## Historical Generation Prompt

Use case: logo-brand. Asset type: original DroidDeck application logo and favicon.
Create one polished, distinctive compact symbol for DroidDeck, an Android
virtual-machine workspace streamed to a browser. Subject: two interlocking
capital-D-inspired screen shapes, suggesting paired displays and a connected
deck; a clean geometric DD monogram that reads instantly at small sizes.
Flat vector-like graphic as a high-quality bitmap, bold balanced proportions,
crisp edges, generous negative space, centered square composition with the
symbol filling most of the canvas. Fresh cyan and emerald accents with a deep
ink outline or structural shape, legible on light and dark UI. Transparent
background with real alpha. No words, no tagline, no Android robot, no Tesla
emblem, no gradients, no shadows, no glow, no ornamental frame, no mockup,
no extra symbols. One finished logo, not a contact sheet.

## Historical Integrity

SHA256: `6dfbe17b8b505c1bd5a334c067c96d4f1a7d0d00390b6a5f163d1fa2547dbc30`.
Original generated file: `exec-5394683f-1a3f-4a73-bae1-1c82e4120608.png`.
This preview identity does not imply a published downloadable release.
