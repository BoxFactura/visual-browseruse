---
id: vamonos-pest
description: Duplicate-claim fixture A.
match:
  rfcs: [PHE850315GH7]
portal_url: https://vamonospest.com.mx/factura
required_ticket_fields: [purchase.total]
required_fiscal_fields: [rfc]
stop:
  before_labels: ["Emitir"]
patience: { max_reload_cycles: 1, wait_seconds: 5 }
last_verified: 2026-06-01
---
body
