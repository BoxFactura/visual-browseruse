"""Minimal web UI for the invoicing agent.

Upload one ticket photo → OpenAI turns it into ticket JSON (with a top-level
`facturadata` block) → we launch facturar.py on that JSON so the agent starts
working. The receptor is chosen by RFC (rfcs/<RFC>.json) and the run honours the
auto-submit / no-guide toggles.

    uv run webapp.py            # serves http://127.0.0.1:5000
"""

import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request
from openai import OpenAI

BASE = Path(__file__).parent
UPLOADS = BASE / "uploads"

# The transcription prompt is fixed: it must yield the standardized facturadata
# block the matcher keys off (store_name, optional rfc). Everything else in the
# ticket stays free-form — the invoicing agent figures it out.
TRANSCRIBE_PROMPT = (
    "convert this to a json file, it must include a top level item: facturadata; "
    "it must have: store_name and optionally rfc"
)

load_dotenv()
app = Flask(__name__)


def available_rfcs() -> list[str]:
    return sorted(p.stem for p in (BASE / "rfcs").glob("*.json"))


def parse_json(text: str) -> dict | None:
    """Best-effort parse of the model's reply into a dict (handles a stray code
    fence or surrounding prose by grabbing the outermost {...})."""
    text = (text or "").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ticket → CFDI invoice</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; max-width: 640px; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.3rem; }
  form { display: grid; gap: 1rem; }
  .drop { border: 2px dashed #999; border-radius: 12px; padding: 1.5rem; text-align: center; color: #888; }
  body.dragging { outline: 4px dashed #4af; outline-offset: -8px; }
  body.dragging::after { content: "Drop the ticket image"; position: fixed; inset: 0;
    background: rgba(0,0,0,.55); color: #fff; display: grid; place-items: center;
    font-size: 1.5rem; z-index: 10; }
  label.flag { display: flex; gap: .5rem; align-items: center; }
  #preview { max-width: 100%; max-height: 240px; border-radius: 8px; display: none; margin-top: 1rem; }
  button { padding: .7rem 1rem; font-size: 1rem; border-radius: 8px; cursor: pointer; }
  #out { white-space: pre-wrap; background: #8881; padding: 1rem; border-radius: 8px; font-size: .85rem; }
</style>
</head>
<body>
<h1>Ticket → CFDI invoice</h1>
<form id="f">
  <div class="drop">
    <input type="file" name="image" id="image" accept="image/*" required>
    <p>or drop a ticket image anywhere on the page</p>
    <img id="preview" alt="ticket preview">
  </div>
  <label>RFC (receptor)
    <select name="rfc" required>
      {% for r in rfcs %}<option value="{{ r }}">{{ r }}</option>{% endfor %}
    </select>
  </label>
  <label class="flag"><input type="checkbox" name="auto_submit" checked> Auto-submit (emit the invoice)</label>
  <label class="flag"><input type="checkbox" name="no_guide" checked> No guide (let the agent figure it out)</label>
  <button type="submit">Transcribe &amp; run agent</button>
</form>
<pre id="out" hidden></pre>
<script>
  const body = document.body, fileInput = document.getElementById('image'),
        preview = document.getElementById('preview'), form = document.getElementById('f'),
        out = document.getElementById('out');
  let depth = 0;
  function showPreview(file) {
    if (!file) return;
    preview.src = URL.createObjectURL(file);
    preview.style.display = 'block';
  }
  // Whole-page drop zone. A depth counter avoids flicker from nested dragenter/leave.
  ['dragenter', 'dragover'].forEach(e => document.addEventListener(e, ev => ev.preventDefault()));
  document.addEventListener('dragenter', () => { if (depth++ === 0) body.classList.add('dragging'); });
  document.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; body.classList.remove('dragging'); } });
  document.addEventListener('drop', ev => {
    ev.preventDefault(); depth = 0; body.classList.remove('dragging');
    const file = ev.dataTransfer.files[0];
    if (!file) return;
    const dt = new DataTransfer(); dt.items.add(file);
    fileInput.files = dt.files;
    showPreview(file);
  });
  fileInput.addEventListener('change', () => showPreview(fileInput.files[0]));
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    if (!fileInput.files[0]) { alert('Choose or drop a ticket image first.'); return; }
    out.hidden = false; out.textContent = 'Transcribing the ticket and launching the agent…';
    try {
      const r = await fetch('/run', { method: 'POST', body: new FormData(form) });
      const j = await r.json();
      out.textContent = r.ok
        ? `✅ ${j.message}\n\ncommand: ${j.command}\nticket:  ${j.ticket_file}\nlog:     ${j.log_file}\n\n${JSON.stringify(j.ticket, null, 2)}`
        : `❌ ${j.error}${j.raw ? '\n\n' + j.raw : ''}`;
    } catch (e) { out.textContent = '❌ ' + e; }
  });
</script>
</body>
</html>"""


@app.get("/")
def index():
    return render_template_string(PAGE, rfcs=available_rfcs())


@app.post("/run")
def run():
    image = request.files.get("image")
    rfc = (request.form.get("rfc") or "").strip().upper()
    auto_submit = request.form.get("auto_submit") == "on"
    no_guide = request.form.get("no_guide") == "on"

    if not image or not image.filename:
        return jsonify(error="no ticket image uploaded"), 400
    if rfc not in available_rfcs():
        return jsonify(error=f"unknown RFC {rfc!r}; add rfcs/{rfc}.json"), 400

    data_url = f"data:{image.mimetype or 'image/jpeg'};base64," + base64.b64encode(image.read()).decode()
    model = os.getenv("TICKET_TRANSCRIBE_MODEL") or os.getenv("INVOICE_MODEL", "gpt-5.4")
    try:
        completion = OpenAI().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": TRANSCRIBE_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}],
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # network / auth / model errors
        return jsonify(error=f"OpenAI transcription failed: {exc}"), 502

    raw = completion.choices[0].message.content or ""
    ticket = parse_json(raw)
    if ticket is None:
        return jsonify(error="the model did not return valid JSON", raw=raw), 502

    UPLOADS.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    ticket_path = UPLOADS / f"{stamp}-{rfc}.json"
    ticket_path.write_text(json.dumps(ticket, ensure_ascii=False, indent=2), encoding="utf-8")

    cmd = [sys.executable, str(BASE / "facturar.py"), rfc, str(ticket_path)]
    if auto_submit:
        cmd.append("--auto-submit")
    if no_guide:
        cmd.append("--no-guide")
    log_path = UPLOADS / f"{stamp}-{rfc}.log"
    # Fire-and-forget: the agent opens its own (headed) browser and runs for a while;
    # the request returns the transcription immediately and the run logs to its file.
    subprocess.Popen(cmd, cwd=str(BASE), stdout=open(log_path, "w"), stderr=subprocess.STDOUT)

    return jsonify(
        message="agent launched — watch the browser window",
        ticket=ticket,
        ticket_file=str(ticket_path.relative_to(BASE)),
        log_file=str(log_path.relative_to(BASE)),
        command=" ".join([Path(cmd[0]).name, *cmd[1:]]),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")))
