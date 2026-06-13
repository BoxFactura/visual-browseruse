---
id: farmacia-san-pablo
description: CFDI 4.0 self-invoicing for Farmacia San Pablo purchase tickets.
match:
  domains: [farmaciasanpablo.com.mx]
  rfcs: []
portal_url: https://farmaciasanpablo.com.mx/
required_ticket_fields: [invoice_data.facturacion_folio, purchase.total]
required_fiscal_fields: [rfc, nombre, cp, email]
stop:
  before_labels: ["Emitir Factura"]
patience: { max_reload_cycles: 3, wait_seconds: 10 }
last_verified: 1970-01-01
---
## Preconditions
- Ticket: número de referencia and total amount.
- Fiscal: RFC, nombre, CP, email.

## Steps
1. You start on the merchant site or billing portal (navigation may or may not be pre-done). If the main site is shown, open the billing flow by clicking "Facturación" and then "Generar Factura". If it looks blank, apply the patience policy.
   expected: the merchant site header may show "Facturación"; the portal landing may show a "Generar Factura" button.
   verify: a billing screen is visible with either "Generar Factura" or the ticket-capture form.
2. If "Generar Factura" is visible, click it.
   verify: the ticket-capture form appears.
3. Fill "No. Referencia" with {facturacion_folio} and "Total" with {total}.
   expected: the form also shows buttons "Obtener Factura" and "Limpiar Datos".
   verify: both fields show the exact values.
4. Click "Obtener Factura".
   verify: the customer fiscal-data section or invoice summary appears.
5. If a fiscal-data form is shown, fill RFC {rfc}, nombre {nombre}, CP {cp}, email {email}.
   expected: the form may include extra address fields such as apellidos, calle, colonia, localidad, municipio, estado, país, referencia; live-page required fields win.
   verify: every filled required field shows the exact values provided.
6. If the portal requires a customer lookup before continuing, use the visible lookup control and remain on the fiscal-data page after the required fields are present.
   expected: a button "Buscar cliente" may be visible near the RFC field.
   verify: the fiscal-data section remains visible and the required fields are populated.
7. Continue until the invoice summary/review screen is visible, but do not click the final emission button.
   expected: the summary may show concept columns like "Cantidad", "Descripcion", "V. Unitario", "Importe".
   verify: "Emitir Factura" is visible and all required fields remain correctly populated.

## Quirks
| symptom | workaround |
|---|---|
| page blank for >10s | patience policy: wait, reload, max cycles per policy |
| tutorial shows many address fields not in canonical inputs | fill only live-page required fields from available placeholders; do not invent values for extra fields |

## Error codes
| portal message contains | meaning | action |
|---|---|---|
| ya facturado / previamente facturado | this ticket already has an invoice | abort with status already_invoiced |

## Stop & completion
The final button is "Emitir Factura" — NEVER click it. The tutorial shows a confirmation dialog with "Aceptar" after that, but that is part of the irreversible submit chain and must remain human-only. When the summary is visible, all required fields are filled and verified, and the only remaining action is "Emitir Factura", call ready_for_review with the exact button label and all field values.
