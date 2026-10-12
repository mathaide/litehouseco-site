# Litehouse DeepTech: website and product harness

| Folder | What it is | Runs on | Address |
| --- | --- | --- | --- |
| `site/` | Public website (static HTML) | Cloudflare Pages | litehouseco.com |
| `gateway/gate/` | Access-code gate, one six-digit code per product | Cloudflare Worker `litehouse-gate` | origin8 / myvault / jugaad .litehouseiq.com, psl.litehouseco.com |
| `gateway/admin/` | Private admin panel (codes, Ikigai, site health, activity) | Cloudflare Worker `litehouse-admin` behind Cloudflare Access | admin.litehouseiq.com |
| `origin8/` | Origin8 Revenue OS, Python (FastAPI) | Railway | origin8.litehouseiq.com (through the gate) |
| `myvault/` | MyVault Lender Intelligence OS, Python | Railway | myvault.litehouseiq.com (through the gate) |

DocLens is Python and lives in its own repository (mathaide/doclens).

## Access codes

* Each gated product has its own code, stored in Cloudflare KV under `code:<product>`.
* Codes are exactly six digits. A product with no code set stays locked to everyone.
* Codes are changed only in the admin panel. A change signs everyone out of that product.

## How the gate protects the Python services

The gate signs every request it forwards with an ECDSA P-256 key that it generates
and keeps in KV. It publishes the public key at `/__lh/pubkey`. The Python services
(`lhgate.py`) refuse any request without a valid, recent signature, so they cannot be
reached around the gate. No shared secret is typed into any dashboard.

## Deploys

Every push to `main` deploys automatically: Cloudflare Workers Builds for the two
Workers, Cloudflare Pages for `site/`, and Railway for `origin8/` and `myvault/`.

## Local runs

```
cd origin8 && pip install -r requirements.txt && GATE_PUBKEY_URL=off DATA_DIR=./var python app/main.py
cd myvault && pip install -r requirements.txt && python app.py
```

Origin8 has no dependency on any outside AI or app-builder service.
