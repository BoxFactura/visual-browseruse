---
id: los-pollos-hermanos
description: CFDI invoicing for Los Pollos Hermanos restaurant tickets.
match:
  domains: [lospolloshermanos.com.mx]
  rfcs: [PHE850315GH7]
portal_url: https://factura.lospolloshermanos.com.mx/
required_ticket_fields: [invoice_data.facturacion_folio, purchase.total]
required_fiscal_fields: [rfc, nombre, cp, regimen_fiscal, uso_cfdi, email]
invoicing_window: { max_days_after_purchase: 30 }
stop:
  before_labels: ["Facturar"]
patience: { max_reload_cycles: 2, wait_seconds: 5 }
last_verified: 2026-06-01
---
## Steps
1. Enter the folio and click "Buscar".
