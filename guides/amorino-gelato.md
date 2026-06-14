---
id: amorino-gelato
description: CFDI 4.0 self-invoicing for Amorino Gelato purchase tickets.
match:
  domains: [amorinogelato.com]
  rfcs: []
portal_url: https://facturacion.amorinogelato.com/
required_fiscal_fields: [rfc, nombre, cp, regimen_fiscal, uso_cfdi, email]
ticket_field_map:
  facturacion_folio: invoice.invoice_number
  total: summary.total
  purchase_date: invoice.date
stop:
  before_labels: ["GENERAR FACTURA"]
patience: { max_reload_cycles: 3, wait_seconds: 10 }
last_verified: 2026-06-12
---
## Preconditions
- Ticket: número de factura.
- Fiscal: RFC, nombre, CP, régimen fiscal, uso CFDI, email.

## Steps
1. You start on the billing page (navigation is pre-done). Screen 1 has only two
   inputs: "NÚMERO DE FACTURA" and "RFC". Fill them with {facturacion_folio} and
   {rfc}, then click "SIGUIENTE PASO" in the same step.
   verify: both fields held the exact values and the URL advances to
   .../invoice/fiscal-data.
2. The fiscal-data form is a slow SPA — it renders a moment after the URL change;
   if its inputs aren't interactive yet, wait briefly (do NOT re-probe the DOM
   repeatedly). The form has these fields — fill in one step:
   - "Nombre o Razón Social" → {nombre}
   - email / "Correo" → {email}
   - "Código Postal" → {cp}
   - RFC carries over from screen 1 — verify it reads {rfc}; do NOT re-type it.
   - "Uso del CFDI" usually pre-defaults to {uso_cfdi} — verify it; change only if different.
   - "Régimen Fiscal" is a click-to-open dropdown (a button labeled "Seleccionar
     régimen fiscal" / a listbox, NOT a native <select>): click it to open.
   verify: nombre, email, CP are exact and the régimen dropdown is open.
3. From the open régimen list, click the option whose text exactly matches the
   name in {regimen_fiscal}.
   verify: the régimen field now shows that exact name (not a different régimen).

## Quirks
| symptom | workaround |
|---|---|
| fiscal form inputs not interactive right after "Siguiente paso" | wait briefly for the SPA; don't loop find_elements |
| Régimen fiscal won't take a typed value | it's a click-to-open listbox: click "Seleccionar régimen fiscal", then click the option by name |
| page blank for >10s | patience policy: wait, reload, max cycles per policy |
| tutorial says to use a Box Factura email | ignore product-specific guidance and use {email} |

## Error codes
| portal message contains | meaning | action |
|---|---|---|
| ya facturado / previamente facturado | this ticket already has an invoice | abort with status already_invoiced |
| problema técnico / problemas técnicos | the portal failed to generate the invoice due to a technical issue | abort with status aborted_error_code: tell the user to email clientes@amorinogelato.com |

## Stop & completion
The final button is "GENERAR FACTURA" — NEVER click it. If a technical-error screen appears after human review submits, the tutorial directs the user to contact clientes@amorinogelato.com, but that path is outside agent scope. When every fiscal field is filled and verified and the only remaining action is that button, call ready_for_review with the exact button label and all field values.
