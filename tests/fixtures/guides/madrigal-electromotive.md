---
id: madrigal-electromotive
description: CFDI invoicing for Madrigal Electromotive industrial purchases.
match:
  domains: [madrigal.com.mx]
  rfcs: [MEL721104RT2]
portal_url: https://madrigal.com.mx/facturacion
required_ticket_fields: [invoice_data.facturacion_folio, purchase.total]
required_fiscal_fields: [rfc, nombre, cp, regimen_fiscal, uso_cfdi, email]
stop:
  before_labels: ["Timbrar"]
patience: { max_reload_cycles: 2, wait_seconds: 5 }
last_verified: 2026-06-01
---
## Steps
1. Enter the reference and click "Continuar".
