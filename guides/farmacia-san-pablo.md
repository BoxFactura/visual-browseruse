---
id: farmacia-san-pablo
description: CFDI 4.0 self-invoicing on Farmacia San Pablo's portal (in-store tickets).
match:
  domains: [farmaciasanpablo.com.mx]
  rfcs: [PPL961114GZ1]
portal_url: https://www.farmaciasanpablo.com.mx/electronic-billing
required_ticket_fields: [invoice_data.facturacion_folio, purchase.total, purchase.date]
required_fiscal_fields: [rfc, nombre, cp, regimen_fiscal, uso_cfdi, email]
invoicing_window: { max_days_after_purchase: 180 }
stop:
  before_labels: ["Emitir Factura", "Generar Factura y Enviar"]
patience: { max_reload_cycles: 3, wait_seconds: 10 }
last_verified: 2026-06-12
---
## Preconditions
- Ticket: facturación folio (21 digits) and total amount.
- Fiscal: RFC, nombre (exactly as on the Constancia), CP, régimen fiscal, uso CFDI, email.

## Steps
1. You start on the billing page (navigation is pre-done). If it looks blank, apply the
   patience policy — this SPA renders slowly; blank does NOT mean down.
   verify: a "Generar Factura" button is visible.
2. Close any promo popup (X button) if one appears.
3. Click "Generar Factura". This ENTERS the ticket-capture flow — it is not a submit;
   the final button has a different label ("Emitir Factura").
   verify: a form asking for Total and Folio appears.
4. Fill "Folio" with {facturacion_folio}.
   verify: the field shows all 21 digits.
5. Fill "Total" using the set_masked_input action with digits only (e.g. "47400" for $474.00).
   Never type the amount with a decimal point — the currency mask mangles it.
   verify: the field visually reads exactly the ticket total (e.g. "$474.00") before continuing.
6. Click "Obtener Factura".
   If an alert says "No se encontró el recibo": re-verify folio and total once; if it
   repeats, abort — the ticket data is wrong.
   verify: the receptor fiscal-data form appears with the purchase concept listed.
7. Order matters on the fiscal form: select Régimen Fiscal {regimen_fiscal} FIRST, then
   Uso de CFDI {uso_cfdi}.
   expected: the Uso de CFDI dropdown does not offer {uso_cfdi} until régimen is selected.
8. Fill RFC {rfc}, nombre {nombre} (exactly as written, no extra suffixes), email {email}.
   expected: the CP field is disabled until a "Buscar cliente" lookup near the RFC field
   runs; use it, then confirm CP reads {cp}.
   verify: every fiscal field shows the exact values provided — no autocompleted surprises.

## Quirks
| symptom | workaround |
|---|---|
| Total shows "$74.00" or "$74,474.00" after typing | use set_masked_input with digits only; never retype decimals |
| page blank for >10s | patience policy: wait, reload, max cycles per policy — never declare the site down before exhausting them |
| promo popup blocks the page | close it via its X before anything else |
| CP field disabled | run the "Buscar cliente" lookup first; CP enables after |

## Error codes
| portal message contains | meaning | action |
|---|---|---|
| CFDI40147 | receptor name/CP/régimen don't match the SAT registry (DomicilioFiscalReceptor) | abort with status aborted_error_code: tell the user to match their Constancia de Situación Fiscal exactly — this is a data problem, retrying navigation will not fix it |
| No se encontró el recibo | folio/total don't match a purchase | one re-verify of folio+total, then abort |
| ya facturado / previamente facturado | this ticket already has an invoice | abort with status already_invoiced |

## Stop & completion
The final button is "Emitir Factura" — NEVER click it (nor "Generar Factura y Enviar").
When every fiscal field is filled and verified and the only remaining action is that
button, call ready_for_review with the exact button label and all field values.
