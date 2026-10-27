# Media evidence stage validation

This is an unreleased development branch. The production checkout, database and personal launchers were not changed.

## Implemented

- Configurable retained images/videos/frames, fair per-request input quotas, provider limit confirmation and old-install four-input default.
- Public-address media downloads with pinned socket destinations and original TLS hostname, per-hop redirect validation, byte/time limits and sanitized logs.
- Shared cache accounting for reservations and outputs, active-file pins, content-based decode reuse and bounded local-only FFmpeg processing.
- JPEG data URLs for chat and responses, allowed evidence IDs, shared-SKU/conflict safeguards, seller text in visual inputs/cache identity and manual forced reanalysis.
- Media settings with explicit cost confirmation, controlled image viewing independent of vision configuration, frame positions and scoreless observations/conflicts.
- Wheel package data now includes templates; installed applications can render pages outside the source checkout.

## Verification

- Before independent review: 337 passed, one host FFmpeg skip, coverage 88.94%.
- Review fixes and dormant stage-three foundations together: **356 passed, one skip, coverage 88.88%**. Ruff and lock check passed. Logs remain in ignored local execution scratch.
- One independent review found nine important defects, all addressed by regression tests. Duplicate content was regraded important because it wastes paid input slots, and deduplicated.
- A deliberately invalid Range response reproduces the pin leak when the response object is retained; cleanup now executes in a response-level `finally`.
- `second-eye:upgrade-phase2` built successfully. A container with networking disabled generated a synthetic video and verified actual FFmpeg probe/start-middle-end frames, image resizing, optional SDK import, application pages and media settings validation. No production volume or external model/notification credentials were used.
- Windows host lacks FFmpeg; application startup remains supported and video capability reports unavailable. The corresponding real-tool test was run in Linux Docker instead.

## Limits and remaining work

Actual Goofish video widgets and SKU/media associations still need sanitized real samples. Only main-media DOM adapters and direct HTTP(S) files are implemented; blob/HLS/DASH/DRM are explicitly unsupported. Live provider compatibility and real DNS/TLS timing were not exercised against external services. No macOS or ARM runtime was tested.

The media stage does not complete the overall upgrade. Persistent execution foundations are being developed; the application still uses the old task queue until the staged coordinator, delivery lifecycle and resource controls are integrated. Full navigation/settings interaction work and final performance comparison remain required. No speedup is claimed here.
