---
id: amorino-gelato
description: CFDI 4.0 self-invoicing for Amorino Gelato purchase tickets.
match:
  domains: [amorinogelato.com]
  rfcs: []
portal_url: https://facturacion.amorinogelato.com/
required_ticket_fields: [invoice_data.facturacion_folio]
required_fiscal_fields: [rfc, nombre, cp, regimen_fiscal, uso_cfdi, email]
stop:
  before_labels: ["GENERAR FACTURA"]
patience: { max_reload_cycles: 3, wait_seconds: 10 }
last_verified: 1970-01-01
---
## Preconditions
- Ticket: número de factura.
- Fiscal: RFC, nombre, CP, régimen fiscal, uso CFDI, email.

## Steps
1. You start on the billing page (navigation is pre-done). If it looks blank, apply the patience policy.
   verify: a "NÚMERO DE FACTURA" input is visible.
2. Fill "NÚMERO DE FACTURA" with {facturacion_folio} and "RFC" with {rfc}.
   expected: step 1 shows only those two fields before continuing.
   verify: both fields show the exact values.
3. Click "SIGUIENTE PASO".
   verify: the fiscal-data form appears.
4. Fill RFC {rfc}, nombre {nombre}, CP {cp}, régimen {regimen_fiscal}, uso {uso_cfdi}, email {email}.
   expected: the name field may be labeled "NOMBRE O RAZÓN SOCIAL".
   verify: every field shows the exact values provided.

## Quirks
| symptom | workaround |
|---|---|
| page blank for >10s | patience policy: wait, reload, max cycles per policy |
| tutorial says to use a Box Factura email | ignore product-specific guidance and use {email} |

## Error codes
| portal message contains | meaning | action |
|---|---|---|
| ya facturado / previamente facturado | this ticket already has an invoice | abort with status already_invoiced |
| problema técnico / problemas técnicos | the portal failed to generate the invoice due to a technical issue | abort with status aborted_error_code: tell the user to email clientes@amorinogelato.com |

## Stop & completion
The final button is "GENERAR FACTURA" — NEVER click it. If a technical-error screen appears after human review submits, the tutorial directs the user to contact clientes@amorinogelato.com, but that path is outside agent scope. When every fiscal field is filled and verified and the only remaining action is that button, call ready_for_review with the exact button label and all field values.
