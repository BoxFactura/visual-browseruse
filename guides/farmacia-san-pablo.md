---
id: farmacia-san-pablo
description: CFDI 4.0 self-invoicing on Farmacia San Pablo's portal (in-store tickets).
match:
  domains: [farmaciasanpablo.com.mx]
  rfcs: [PPL961114GZ1]
portal_url: https://emision-sanpablo-portal-auto-prod.pegasotecnologia.mx/
required_fiscal_fields: [rfc, nombre, cp, regimen_fiscal, uso_cfdi, email]
invoicing_window: { max_days_after_purchase: 180 }
stop:
  before_labels: ["Emitir Factura", "Generar Factura y Enviar"]
patience: { max_reload_cycles: 3, wait_seconds: 10 }
last_verified: 2026-06-14
---
## Preconditions
- Ticket: facturación folio (21 digits) and total amount.
- Fiscal: RFC, nombre (exactly as on the Constancia), CP, régimen fiscal, uso CFDI, email.

## Steps
1. You start directly on the San Pablo emisión portal (navigation is pre-done to the
   direct URL). It should show the ticket-capture form (Total + Folio). If it looks blank,
   apply the patience policy — this SPA renders slowly; blank does NOT mean down. If a
   landing page or menu shows instead, find the option to facturar a purchase ticket.
2. Close any promo popup (X button) if one appears.
3. Fill "Folio" with the ticket's invoicing folio ("folio para facturación" / invoice
   folio — a short number like 2093278283), NOT the long reference number, order number,
   or barcode. If the ticket has several id-looking fields, pick the invoice/facturación
   folio.
   verify: the folio field holds that invoicing folio.
4. Fill "Total" with type_slowly, typing the EXACT amount including decimals (e.g. "2306.00").
   It types real keys with a delay and blurs, so the badly-implemented mask formats it.
   verify: the field shows the amount (e.g. "2306.00" / "$2,306.00"), NOT raw digits like
   230600; if it's wrong, retry type_slowly with a larger delay before continuing.
5. Click "Obtener Factura".
   If an alert says "No se encontró el recibo": re-verify folio and total once; if it
   repeats, abort — the ticket data is wrong.
   verify: the receptor fiscal-data form appears with the purchase concept listed.
6. Receptor fiscal data — do these IN ORDER; each step unlocks the next:
   a. Enter RFC {rfc}.
   b. Click "Buscar cliente" — it autofills the receptor data on file (e.g. nombre,
      código postal). Verify they match {nombre}, {cp}; if one differs, don't override it
      blindly — a mismatch triggers CFDI40147 at submit; stop and report.
   c. Select Régimen Fiscal {regimen_fiscal} (match it by name).
   d. Selecting régimen ENABLES the Uso de CFDI dropdown — only now select the FIRST
      option from {uso_cfdi} that it offers (it may only offer some, e.g. D01/D02/D04/S01).
   e. Fill email {email} if the lookup left it empty.
   expected: régimen fiscal and uso de CFDI are dropdowns — select by the name after the
   code; the uso de CFDI option may not appear until régimen fiscal is set.

## Quirks
| symptom | workaround |
|---|---|
| Total mask mangles the amount | type it with type_slowly (real keys + blur), the exact value e.g. "2306.00"; then verify |
| page blank for >10s | patience policy: wait, reload, max cycles per policy — never declare the site down before exhausting them |
| promo popup blocks the page | close it via its X before anything else |
| receptor fields blank / CP disabled | enter RFC, then click "Buscar cliente" — it autofills nombre/CP; verify them, don't hand-type over them, then select régimen and uso |
| Uso de CFDI dropdown is empty/disabled | it stays locked until Régimen Fiscal is selected — set régimen first, then the uso options appear |

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
