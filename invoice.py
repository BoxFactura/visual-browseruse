"""Drive the San Pablo Farmacia self-invoicing (facturación) portal.

Reads the purchase ticket from data.json and the receptor's fiscal data from
rfc.txt, fills out the CFDI request form, and STOPS before the final submit so a
human can verify and issue the invoice manually.

Run it yourself so you own the browser window:

    uv run invoice.py

It opens a visible Chrome, fills everything up to the final confirmation screen,
then leaves the window open for you to review and click the final button.
"""

from browser_use import Agent, Browser, ChatOpenAI
from dotenv import load_dotenv
import asyncio
import json
import os
import sys
from pathlib import Path

load_dotenv()

BASE = Path(__file__).parent
# Capable model: this is a multi-step Spanish form, and it must respect the
# "do not submit" constraint. gpt-5.4 is OpenAI's current flagship.
# Override with INVOICE_MODEL (e.g. gpt-5.4-mini for faster/cheaper).
MODEL = os.getenv("INVOICE_MODEL", "gpt-5.4")


def build_task(ticket: dict, fiscal: str) -> str:
    ticket_json = json.dumps(ticket, ensure_ascii=False, indent=2)
    return f"""
You are filling out a CFDI (factura electrónica) request on the San Pablo
Farmacia self-invoicing portal. The site is in Spanish; work in Spanish.

# GOAL
Fill the invoice-request form COMPLETELY, then STOP at the final confirmation
screen. A human will review and submit. You must NOT issue the invoice yourself.

# STEP 0 — PATIENCE (this site is a slow SPA)
The San Pablo site renders slowly. If a page looks blank/empty, DO NOT conclude
it is down. Instead: use the `wait` action for ~8-10s, scroll, and if still
blank, reload the page. Repeat up to 3 times. The pages DO render real content
and buttons after a short delay — NEVER report the site as "caído"/down.

# STEP 1 — Open the invoicing portal
- Navigate directly to https://www.farmaciasanpablo.com.mx/electronic-billing
- If it looks blank at first, apply STEP 0 (wait + reload) until content appears.
  Fallback: open https://www.farmaciasanpablo.com.mx and click "Facturación".
- The billing page shows a FEW OPTIONS/BUTTONS (e.g. type of invoicing). Find and
  click the option to invoice an IN-STORE PURCHASE TICKET (we have a physical
  store ticket) — e.g. a button like "Facturar", "Generar factura", "Facturación
  de tickets", or "Compra en tienda". Choose the ticket / in-store option, not a
  global or online-order option.

# STEP 2 — Look up the purchase with this TICKET data
{ticket_json}
Map these to whatever the form asks for, e.g.:
- Folio de facturación / folio del ticket -> "facturacion_folio"
- Número de transacción -> "transaction_id"
- Número de tienda / sucursal -> "store" ("store_number" / "branch")
- Fecha de compra -> "purchase.date"
- Total / importe -> "purchase.total"
- Número de cliente -> "invoice_data.customer_id"
Use ONLY values present above. Never invent a value.

IMPORTANT — the "Total"/importe field is a CURRENCY-MASKED input. Typing
"474.00" gets mangled (it shows e.g. "$74.00"). To set it correctly:
1. Click the field and clear it completely (select-all + delete).
2. Type ONLY the digits, no dot and no symbols: for $474.00 type "47400".
3. VERIFY the field visually reads "474.00" (or "$474.00") BEFORE you submit.
   If it is still wrong, try typing the digits one more time or use keyboard
   backspace to clear, then retype. Do not submit until it reads 474.00.

# STEP 3 — Enter the RECEPTOR (customer) fiscal data EXACTLY as given
{fiscal}
Notes:
- This is a persona física. Enter the name exactly as written; do not add any
  suffix or change capitalization beyond what the form requires.
- "uso de cfdi: gastos en general" = the option "G03 - Gastos en general".
- Use the email to receive the invoice.
- If the form requires a field NOT provided above (most likely "Régimen Fiscal"
  del receptor), DO NOT guess it. Leave it empty and report it at the end.

# HARD CONSTRAINTS (most important)
- NEVER click the final button that issues/sends the invoice. Examples of
  buttons to AVOID on the last step: "Generar", "Generar factura", "Facturar",
  "Timbrar", "Emitir", "Descargar factura", "Enviar".
- The moment the only remaining action is that final submit, call `done`.
- Fill every field you can up to the final confirmation/preview, then STOP.
- If you hit a CAPTCHA, a login/registration you cannot complete, or a blocking
  required field you have no data for, STOP and report it.

# WHEN YOU STOP, report:
- The exact URL you ended on.
- Every field you filled and the value used.
- Every field still empty and why.
- The exact label of the final button the human needs to click to submit.
""".strip()


async def main() -> None:
    # Legacy paths: superseded by facturar.py; kept until Phase 1 parity passes.
    ticket = json.loads((BASE / "tickets/san-pablo-2026-06-01.json").read_text(encoding="utf-8"))
    fiscal_data = json.loads((BASE / "rfcs/UAP370423PP3.json").read_text(encoding="utf-8"))
    fiscal = "\n".join(f"{k}: {v}" for k, v in fiscal_data.items())

    # Visible browser so you can watch and take over for the final submit.
    # keep_alive keeps the window open after the agent stops.
    # Dedicated profile dir avoids the "Failed to open a new tab" lock conflict.
    browser = Browser(
        headless=bool(os.getenv("HEADLESS", "").strip()),
        keep_alive=True,
        window_size={"width": 1280, "height": 900},
        user_data_dir="~/.config/browseruse/profiles/sanpablo",
        # This portal is a slow SPA — give it time to render before each capture.
        minimum_wait_page_load_time=3.0,
        wait_for_network_idle_page_load_time=6.0,
    )

    agent = Agent(
        task=build_task(ticket, fiscal),
        browser=browser,
        llm=ChatOpenAI(model=MODEL),
    )

    history = await agent.run(max_steps=60)

    print("\n" + "=" * 70)
    print("STOPPED BEFORE FINAL SUBMIT — review the open window before issuing.")
    print("=" * 70)
    print(history.final_result() or "(no final report returned)")
    print("=" * 70)

    # Hold the browser open so you can verify and submit manually.
    try:
        if sys.stdin and sys.stdin.isatty():
            input("\nPress Enter here to close the browser when you're done... ")
        else:
            await asyncio.sleep(3600)
    except (EOFError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    asyncio.run(main())
