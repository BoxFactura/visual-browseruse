"""Minimal web UI for the invoicing agent.

Upload one ticket photo → OpenAI turns it into ticket JSON (with a top-level
`facturadata` block) → we launch facturar.py on that JSON so the agent starts
working. The receptor is chosen by RFC (rfcs/<RFC>.json) and the run honours the
auto-submit / no-guide toggles. The page streams the agent's log and remembers
the last RFC + flags you used (via localStorage — never the ticket image).

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

# The transcription prompt is fixed. It must (a) transcribe the WHOLE ticket so
# the invoicing agent has every lookup field (serie, folio, billing code, total,
# date, items…), and (b) add the standardized top-level facturadata block the
# matcher keys off (store_name, optional rfc). Everything outside facturadata
# stays free-form — the agent figures out which field means what.
TRANSCRIBE_PROMPT = (
    "Transcribe this ticket image into a single JSON object. Include EVERY piece of "
    "information visible on the ticket — store/merchant details, date and time, the "
    "folio / ticket number and any billing or facturación code, every line item with "
    "its quantity and prices, subtotal, taxes and total, payment info, and anything "
    "else printed. Transcribe exactly what you see; do not omit fields and do not "
    "invent values. In ADDITION, include a top-level key \"facturadata\" with "
    "\"store_name\" (the merchant's name) and, only if the ticket shows it, \"rfc\" "
    "(the merchant's RFC). Return only the JSON object."
)

load_dotenv()
app = Flask(__name__)

# Launched agent processes, keyed by log filename, so /log can stream each run's
# output and report when it finishes.
RUNS: dict[str, subprocess.Popen] = {}


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
  #log { white-space: pre-wrap; background: #0b0b0b; color: #9fe69f; padding: 1rem; border-radius: 8px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .8rem;
    max-height: 380px; overflow: auto; }
</style>
</head>
<body>
<h1>Ticket → CFDI invoice</h1>
<form id="f" method="post" action="/run" enctype="multipart/form-data">
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
<pre id="log" hidden></pre>
{% raw %}<script>
  const body = document.body, fileInput = document.getElementById('image'),
        preview = document.getElementById('preview'), form = document.getElementById('f'),
        out = document.getElementById('out'), logEl = document.getElementById('log');
  const PREFS_KEY = 'factura.prefs';

  // remember the last RFC + flags (never the ticket image)
  function loadPrefs() {
    let p; try { p = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}'); } catch { p = {}; }
    if (p.rfc && [...form.elements.rfc.options].some(o => o.value === p.rfc)) form.elements.rfc.value = p.rfc;
    if ('auto_submit' in p) form.elements.auto_submit.checked = !!p.auto_submit;
    if ('no_guide' in p) form.elements.no_guide.checked = !!p.no_guide;
  }
  function savePrefs() {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify({
        rfc: form.elements.rfc.value,
        auto_submit: form.elements.auto_submit.checked,
        no_guide: form.elements.no_guide.checked,
      }));
    } catch (e) { /* storage disabled — ignore */ }
  }
  function showPreview(file) {
    if (!file) return;
    preview.src = URL.createObjectURL(file);
    preview.style.display = 'block';
  }
  // stream the agent's log until its process exits
  async function streamLog(name) {
    logEl.hidden = false;
    logEl.textContent = '(waiting for the agent to start…)';
    while (true) {
      let j;
      try { j = await (await fetch('/log/' + encodeURIComponent(name))).json(); }
      catch (e) { logEl.textContent += '\\n(log unavailable: ' + e + ')'; return; }
      if (j.text) { logEl.textContent = j.text; logEl.scrollTop = logEl.scrollHeight; }
      if (!j.running) { logEl.textContent += `\\n\\n— agent finished (exit ${j.returncode}) —`; return; }
      await new Promise(res => setTimeout(res, 1500));
    }
  }

  // Attach submit FIRST so nothing below can stop it from posting via fetch. The
  // form also has method=post action=/run enctype=multipart as a no-JS fallback.
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    if (!fileInput.files[0]) { alert('Choose or drop a ticket image first.'); return; }
    savePrefs();
    out.hidden = false; logEl.hidden = true;
    out.textContent = 'Transcribing the ticket and launching the agent…';
    try {
      const r = await fetch('/run', { method: 'POST', body: new FormData(form) });
      const j = await r.json();
      if (!r.ok) { out.textContent = `❌ ${j.error}${j.raw ? '\\n\\n' + j.raw : ''}`; return; }
      out.textContent = `✅ ${j.message}\\n\\ncommand: ${j.command}\\nticket:  ${j.ticket_file}\\nlog:     ${j.log_file}\\n\\n${JSON.stringify(j.ticket, null, 2)}`;
      streamLog(j.log_file.split('/').pop());
    } catch (e) { out.textContent = '❌ ' + e; }
  });

  // Progressive enhancements — a failure here must never block submit.
  try {
    loadPrefs();
    ['rfc', 'auto_submit', 'no_guide'].forEach(n => form.elements[n].addEventListener('change', savePrefs));
    fileInput.addEventListener('change', () => showPreview(fileInput.files[0]));
    let depth = 0;  // whole-page drop zone; counter avoids nested dragenter/leave flicker
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
  } catch (e) { console.error('enhancement init failed', e); }
</script>{% endraw %}
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
    # the request returns the transcription immediately, the run logs to its file, and
    # the UI streams it via /log/<name> until the process exits.
    RUNS[log_path.name] = subprocess.Popen(
        cmd, cwd=str(BASE), stdout=open(log_path, "w"), stderr=subprocess.STDOUT
    )

    return jsonify(
        message="agent launched — watch the browser window and the log below",
        ticket=ticket,
        ticket_file=str(ticket_path.relative_to(BASE)),
        log_file=str(log_path.relative_to(BASE)),
        command=" ".join([Path(cmd[0]).name, *cmd[1:]]),
    )


@app.get("/log/<name>")
def log(name: str):
    """Stream a run's log file and whether its process is still alive."""
    if not re.fullmatch(r"[\w.-]+\.log", name):
        return jsonify(error="bad log name"), 400
    path = (UPLOADS / name).resolve()
    if not str(path).startswith(str(UPLOADS.resolve()) + os.sep):
        return jsonify(error="bad log name"), 400
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    proc = RUNS.get(name)
    code = proc.poll() if proc else None
    return jsonify(text=text, running=proc is not None and code is None, returncode=code)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), threaded=True)
