"""Compile a human tutorial (markdown + screenshots) into an agent-friendly guide draft.

    uv run compile_guides.py sanpablo.md
    uv run compile_guides.py path/to/tutorial.md --id some-merchant

Pipeline: distill each screenshot to text (verbatim labels, marked as
"expected:" hints) → one compile call producing the guide format → validate
with the SAME loader the runner uses → write to guides/_drafts/.

Drafts are not loadable by the runner (the loader only globs guides/*.md).
Promotion to guides/ is a human act, after reviewing the draft — especially
its stop.before_labels — and ideally after one supervised run.
"""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from cfdi.guides import GuideError, parse_guide

BASE = Path(__file__).parent
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")

DISTILL_PROMPT = """\
This is a screenshot from a tutorial about a Mexican CFDI invoicing portal.
Extract ONLY what is visibly attested, as JSON:
{"buttons": ["verbatim label", ...], "fields": ["verbatim field label/placeholder", ...],
 "notes": "one sentence: what part of the flow this shows"}
Transcribe labels EXACTLY as written (case, accents). If text is too small or
blurry to read with certainty, do not guess — leave it out. Reply with JSON only."""

# A fictional exemplar, deliberately NOT one of our real guides: the compiler
# must learn the FORMAT from it, not copy real content into new drafts.
EXEMPLAR = """\
---
id: abarrotes-don-eladio
description: CFDI 4.0 self-invoicing for Abarrotes Don Eladio in-store tickets.
match:
  domains: [doneladio.com.mx]
  rfcs: []
portal_url: https://facturacion.doneladio.com.mx/
required_ticket_fields: [invoice_data.facturacion_folio, purchase.total, purchase.date]
required_fiscal_fields: [rfc, nombre, cp, regimen_fiscal, uso_cfdi, email]
stop:
  before_labels: ["Generar CFDI"]
patience: { max_reload_cycles: 3, wait_seconds: 10 }
last_verified: 1970-01-01
---
## Preconditions
- Ticket: facturación folio and total amount.
- Fiscal: RFC, nombre (exactly as on the Constancia), CP, régimen fiscal, uso CFDI, email.

## Steps
1. You start on the billing page (navigation is pre-done). If it looks blank, apply the
   patience policy.
   verify: a folio input is visible.
2. Fill "Folio" with {facturacion_folio} and "Total" with {total}.
   expected: the Total field sits right of the Folio field.
   verify: both fields show the exact values.
3. Click "Buscar ticket".
   verify: the receptor fiscal-data form appears.
4. Fill RFC {rfc}, nombre {nombre}, CP {cp}, régimen {regimen_fiscal}, uso {uso_cfdi},
   email {email}.
   verify: every field shows the exact values provided.

## Quirks
| symptom | workaround |
|---|---|
| page blank for >10s | patience policy: wait, reload, max cycles per policy |

## Error codes
| portal message contains | meaning | action |
|---|---|---|
| ya facturado / previamente facturado | this ticket already has an invoice | abort with status already_invoiced |

## Stop & completion
The final button is "Generar CFDI" — NEVER click it. When every fiscal field is filled
and verified and the only remaining action is that button, call ready_for_review with
the exact button label and all field values."""

COMPILE_PROMPT = f"""\
You convert human tutorials about Mexican CFDI invoicing portals into compact
agent-playbooks for a browser agent. Output ONLY the playbook file content
(YAML frontmatter + markdown body), no code fences, no commentary.

FORMAT — follow this exemplar exactly (structure, section names, conventions):

{EXEMPLAR}

RULES
- The body is for a browser agent that sees the live page. Keep steps numbered,
  imperative, each with a one-line "verify:" of observable state.
- Facts known only from tutorial screenshots are hints: write them as
  "expected:" lines. The agent is told the live page wins.
- Map tutorial vocabulary to canonical placeholders: folio/número de referencia/
  número de factura/número de facturación → {{facturacion_folio}}; total/importe
  → {{total}}; RFC/nombre/CP/régimen/uso CFDI/correo → {{rfc}} {{nombre}} {{cp}}
  {{regimen_fiscal}} {{uso_cfdi}} {{email}}. Use ONLY these placeholders.
- required_ticket_fields takes dotted TICKET-JSON paths only, from:
  invoice_data.facturacion_folio, invoice_data.customer_id,
  invoice_data.transaction_id, purchase.total, purchase.date,
  store.store_number. Receptor data (RFC, nombre, CP, régimen, uso, email) is
  NEVER a ticket field — even when the portal asks for it on the first screen,
  it belongs only in required_fiscal_fields.
- Error-table actions use ONLY this vocabulary: "abort with status
  already_invoiced", "abort with status aborted_error_code: <one-line user
  instruction>", or "one re-verify, then abort". Never invent status names.
- STRIP marketing, sign-up CTAs, and product-specific instructions (e.g.
  "use your <product> email", "register here") — the agent uses {{email}}.
- match.domains: the portal's bare eTLD+1 (no scheme, no www, no path).
  match.rfcs: only if the tutorial states the merchant's RFC; usually [].
- stop.before_labels — SAFETY CRITICAL, never guess:
  * Identify the SUBMIT CHAIN: the button that starts irreversible invoice
    emission, plus any confirmation dialog after it.
  * stop.before_labels gets ONLY the EARLIEST button of that chain (later
    dialog buttons like "Aceptar"/"Confirmar" are unreachable once the first
    is blocked, and such generic labels would false-positive on popups).
  * Mention the rest of the chain in "## Stop & completion" prose as human
    territory.
  * If the tutorial does not clearly show which button emits the invoice,
    write stop.before_labels: [REVIEW_REQUIRED].
- last_verified: 1970-01-01 — a draft has never been verified; a real
  supervised run sets the true date at promotion.
- patience: default {{ max_reload_cycles: 3, wait_seconds: 10 }} unless the
  tutorial says otherwise.
- Keep the universal "ya facturado" error row; add error rows the tutorial
  shows; invent none.
- invoicing_window: include only if the tutorial states a deadline.
- The file ENDS after the "## Stop & completion" section. Never repeat these
  RULES or any instructions in the output.
"""


def strip_leaked_rules(content: str) -> str:
    """Drop an echoed RULES block — a known leak mode of the compile prompt."""
    return content.split("\nRULES\n")[0].rstrip() + "\n"


def extract_image_urls(markdown: str) -> list[str]:
    return IMAGE_RE.findall(markdown)


def strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -len("```")]
    return text.strip() + "\n"


def distill_image(client: OpenAI, model: str, url: str) -> dict:
    response = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": DISTILL_PROMPT},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        }],
    )
    return json.loads(strip_code_fence(response.choices[0].message.content))


def compile_guide(client: OpenAI, model: str, tutorial: str, distilled: list[dict],
                  guide_id: str | None, feedback: str | None = None) -> str:
    user_content = (
        f"TUTORIAL:\n\n{tutorial}\n\n"
        f"SCREENSHOT DISTILLATIONS (in tutorial order; 'expected:' material):\n"
        f"{json.dumps(distilled, ensure_ascii=False, indent=2)}"
    )
    if guide_id:
        user_content += f"\n\nUse id: {guide_id}"
    if feedback:
        user_content += f"\n\nYour previous attempt failed validation: {feedback}\nFix and re-emit the full file."
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": COMPILE_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    return strip_leaked_rules(strip_code_fence(response.choices[0].message.content))


def validate_draft(content: str) -> tuple[str | None, object | None]:
    """Returns (error, guide). Uses the same parser the runner uses."""
    with tempfile.TemporaryDirectory() as tmp:
        draft = Path(tmp) / "draft.md"
        draft.write_text(content, encoding="utf-8")
        try:
            # REVIEW_REQUIRED is a valid *draft* outcome; only the runner's loader rejects it
            return None, parse_guide(draft, allow_review_placeholder=True)
        except Exception as exc:
            return str(exc), None


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tutorial", type=Path)
    parser.add_argument("--id", dest="guide_id", help="force the guide id")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--out", type=Path, default=BASE / "guides" / "_drafts")
    args = parser.parse_args()

    tutorial = args.tutorial.read_text(encoding="utf-8")
    client = OpenAI()

    urls = extract_image_urls(tutorial)
    print(f"distilling {len(urls)} screenshot(s)...")
    distilled = []
    for url in urls:
        try:
            info = distill_image(client, args.model, url)
            distilled.append(info)
            print(f"  ✓ {url}: buttons={info.get('buttons', [])}")
        except Exception as exc:
            distilled.append({"buttons": [], "fields": [], "notes": f"image unavailable ({url})"})
            print(f"  ✗ {url}: {exc}")

    print("compiling...")
    content = compile_guide(client, args.model, tutorial, distilled, args.guide_id)
    error, guide = validate_draft(content)
    if error:
        print(f"validation failed, retrying once: {error}")
        content = compile_guide(client, args.model, tutorial, distilled, args.guide_id, feedback=error)
        error, guide = validate_draft(content)
    if error:
        print(f"FAILED after retry: {error}")
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / f"{guide.id}.md"
    out_path.write_text(content, encoding="utf-8")

    print(f"\ndraft written: {out_path}")
    print(f"  id: {guide.id} | domains: {list(guide.domains)} | rfcs: {list(guide.rfcs)}")
    print(f"  stop.before_labels: {list(guide.stop_before_labels)}")
    print(f"  body: {len(guide.body)} chars (~{len(guide.body) // 4} tokens)")
    print("\nREVIEW before promoting to guides/ — especially stop.before_labels.")
    print("Promotion: confirm labels, fill match.rfcs when known, run one supervised")
    print("E2E, set last_verified to that date, then move the file into guides/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
