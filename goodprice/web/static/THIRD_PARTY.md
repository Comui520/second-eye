# Bundled browser assets

- HTMX 1.9.12: https://github.com/bigskysoftware/htmx/tree/v1.9.12
  `htmx.min.js` SHA-256: `449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452`.
  License: `htmx.LICENSE` (Zero-Clause BSD).
- Tailwind CSS 3.4.17: https://github.com/tailwindlabs/tailwindcss/tree/v3.4.17
  License: `tailwind.LICENSE` (MIT). `app.css` is generated from the existing templates.

Normal application startup needs neither Node.js nor a CDN connection. Maintainers
changing utility classes can regenerate the committed stylesheet with:

```sh
npx --yes tailwindcss@3.4.17 -i ./goodprice/web/static/tailwind.input.css -o ./goodprice/web/static/app.css --content './goodprice/web/templates/**/*.html' --minify
```
