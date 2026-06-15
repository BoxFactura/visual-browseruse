---
id: bodegas-alianza
description: CFDI 4.0 self-invoicing for Bodegas Alianza in-store (physical-store kiosco) tickets.
match:
  names: [Bodegas Alianza]
  rfcs: [CVA991118C63]
portal_url: https://bodegasalianzakiosco.azurewebsites.net/
stop:
  before_labels: ["Convertir a Factura", "Generar Factura"]
last_verified: never
---
## Hints
- This is the physical-store ("Tienda Física") kiosco. To look up the purchase the portal asks
  for THREE values from the ticket: the Serie, the Folio / número de ticket, and a Código de
  facturación. The raw ticket JSON is included below and its field names vary from ticket to
  ticket — read it and find the value that MEANS each one (a short letter/number series, the
  ticket/folio number, and a separate billing/facturación code), then enter each into its
  matching field and look up the ticket.
- Then choose "Completar mis datos" and fill the receptor fiscal data: RFC {rfc}, nombre
  {nombre}, CP {cp}, régimen {regimen_fiscal}, uso de CFDI {uso_cfdi}, email {email}.
- The total and purchase date the portal shows for the looked-up ticket should match the
  ticket — verify them, don't invent. The final step converts the ticket to an invoice and
  emails the XML/PDF.

## Stop & completion
First time on this portal — a human verifies. Do NOT click the final convert/emit button
("Convertir a Factura" / "Generar Factura"). When the fiscal data is filled and only that
button remains, call ready_for_review with its EXACT label.
