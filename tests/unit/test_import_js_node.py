"""The Search results page's upload message (import.js) under Node: the verdict and message the upload route answers
are shown as text. Synthetic values only; nothing is uploaded."""
import json
import re

from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, out, run_node


def test_upload_verdict_is_text():
    reply = {"file": "f.ris", "verdict": "<b>x</b>", "message": "<i>y</i>", "renamed": False}
    code = (STATIC / "import.js").read_text(encoding="utf-8")
    r = run_node(f"{ESC}\n{PAGE}\nglobal.loadImport = async () => {{}}; global.FormData = class {{ append() {{}} }};\n"
                 # the page asks /api/import/sources first (no choice needed here), then posts the upload through api()
                 f"global.api = async (url) => url.startsWith('/api/import/sources') ? {{upload_choice: false}} : {json.dumps(reply)};\n"
                 f"{code}\n"
                 "$('#imp-upload-platform').value = 'scopus'; $('#imp-upload-file').files = [{name: 'f.ris'}];"
                 "await $('#imp-upload').onclick();" + out("$('#imp-upload-msg').innerHTML"))
    assert "&lt;b&gt;x&lt;/b&gt;" in r and "&lt;i&gt;y&lt;/i&gt;" in r
    assert "<b>" not in r and "<i>" not in r and "<b " not in r and "<i " not in r


IMPORT = {"root": "/x/inputs", "final_at": None, "summary": [{"platform": "scopus", "files": 3, "records": 52}],
          "files": [{"platform": "scopus", "file": "good.ris", "imported": True, "source_id": 1, "included": True},
                    {"platform": "scopus", "file": "off.ris", "imported": True, "source_id": 2, "included": True},
                    {"platform": "scopus", "file": "late.ris", "imported": False}]}
CHECK = {"files": [{"platform": "scopus", "file": "good.ris", "verdict": "ok", "message": "looks like a complete, on-topic export"},
                   {"platform": "scopus", "file": "off.ris", "verdict": "off_topic",
                    "message": "this file probably is not the result of your query: 0 of 6 seed papers"},
                   {"platform": "scopus", "file": "late.ris", "verdict": "empty", "message": "no records could be read"}]}


def _import(body: str) -> dict:
    code = (STATIC / "import.js").read_text(encoding="utf-8")
    return run_node(f"{ESC}\n{PAGE}\nglobal.loadImport = async () => {{}};\n{code}\n{body}")


def test_files_table_shows_the_verdict():
    """ui-redesign D32 F8: the files table has the file check's verdict beside the status; a flagged file shows amber
    "check this file" (not a green "imported") with the check's sentence; ticked, it says it enters deduplication; unticked
    (how the import leaves it, owner 30 Oct), it says it stays out until ticked."""
    r = _import(f"const html = filesTable({json.dumps(IMPORT)}, {json.dumps(CHECK)});" + out("html"))
    head, *rows = re.findall(r"<tr[^>]*>.*?</tr>", r)
    assert re.findall(r"<th>([^<]+)</th>", head) == ["platform", "file", "status", "file check", "Enters deduplication",
                                                         "Random sample of"]
    good, off, late, total = rows
    assert '<span class="ok">imported</span>' in good and "verdict-ok" in good and "check this file" not in good
    assert "imp-why" not in good
    assert '<span class="imp-flag">check this file</span>' in off and '<span class="ok">' not in off
    assert 'class="imp-flagged"' in off and "verdict-off_topic" in off and ">off topic<" in off
    assert "0 of 6 seed papers; it enters deduplication while it is ticked." in off
    assert 'data-source="2" checked' in off
    assert '<span class="muted">new</span>' in late and "verdict-empty" in late and "checkbox" not in late
    assert "52</b> records" in total
    unticked = json.loads(json.dumps(IMPORT))
    unticked["files"][1]["included"] = False
    r = _import(f"const html = filesTable({json.dumps(unticked)}, {json.dumps(CHECK)});" + out("html"))
    off = re.findall(r"<tr[^>]*>.*?</tr>", r)[2]
    assert "0 of 6 seed papers; it stays out of deduplication until you tick it." in off and 'data-source="2" checked' not in off
    # without a check (refused or not yet), the table still lists the files with an empty verdict cell
    r = _import(f"const html = filesTable({json.dumps(IMPORT)}, null);" + out("html"))
    assert r.count('<span class="ok">imported</span>') == 2 and "check this file" not in r


def test_platform_select_has_other():
    """The upload's platform is a list of the known platforms (the project's, the catalogue's, the folders already
    imported) ending in "Other…", which shows the free-text key; the upload sends the chosen key."""
    plat = {"chosen": [{"key": "alpha", "label": "alpha"}],
            "catalogue": [{"key": "scopus", "label": "Scopus"}, {"key": "wos", "label": "Web of Science"}]}
    d = {"summary": [{"platform": "delta"}, {"platform": "scopus"}]}
    reply = {"file": "f.ris", "verdict": "ok", "message": "fine", "renamed": False}
    r = _import(f"global.FormData = class {{ constructor() {{ this.parts = []; global.lastForm = this; }} append(k, v) {{ this.parts.push([k, typeof v === 'string' ? v : 'file']); }} }};\n"
                f"replies['/api/platforms'] = {json.dumps(plat)}; replies['/api/import/sources'] = {{upload_choice: false}};\n"
                f"replies['/api/import/upload'] = {json.dumps(reply)};\n"
                f"await loadUploadPlatforms({json.dumps(d)});\n"
                "const html = $('#imp-upload-pick').innerHTML, hiddenFirst = $('#imp-upload-platform').hidden;\n"
                "$('#imp-upload-pick').value = '__other'; $('#imp-upload-pick').onchange(); const shown = !$('#imp-upload-platform').hidden;\n"
                "$('#imp-upload-platform').value = ' JSTOR '; $('#imp-upload-file').files = [{name: 'f.ris'}];\n"
                "await $('#imp-upload').onclick(); const other = lastForm.parts;\n"
                "$('#imp-upload-pick').value = 'scopus'; $('#imp-upload-pick').onchange(); const hiddenAgain = $('#imp-upload-platform').hidden;\n"
                "await $('#imp-upload').onclick(); const picked = lastForm.parts;\n"
                + out("{html, hiddenFirst, shown, hiddenAgain, other, picked}"))
    keys = re.findall(r'<option value="([^"]*)"', r["html"])
    assert keys == ["", "alpha", "scopus", "wos", "delta", "__other"]
    assert ">Scopus (scopus)<" in r["html"] and ">Other…<" in r["html"]
    assert r["hiddenFirst"] is True and r["shown"] is True and r["hiddenAgain"] is True
    assert ["platform", "jstor"] in r["other"] and ["platform", "scopus"] in r["picked"]
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<select id="imp-upload-pick"' in html and '<option value="__other">Other…</option>' in html
    assert 'id="imp-upload-platform"' in html and 'placeholder="platform key' not in html


def test_uploads_final_is_the_main_button():
    """"These uploads are final" is the page's filled button while there are files and they are not final."""
    r = _import("const cls = {};\n"
                "showUploadsFinal(null, 3); cls.open = $('#imp-final').className;\n"
                "showUploadsFinal(null, 0); cls.none = $('#imp-final').className;\n"
                "showUploadsFinal('2026-10-15T09:00:00+00:00', 3); cls.final = $('#imp-final').className; cls.hidden = $('#imp-final').hidden;\n"
                + out("cls"))
    assert r == {"open": "main-act", "none": "secondary", "final": "secondary", "hidden": True}   # main-act: an outline once the step is done
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    sec = html[html.index('<section id="page-import"'):html.index('<section id="page-dedup"')]
    assert re.findall(r"<button(?![^>]*class=)[^>]*>", sec) == []   # no other filled button on the page


def test_upload_then_import_reads_in_order():
    """ui-redesign 5.9.13 (M120 walk, 31 Oct): the Import button sits under the upload box, not in the card title, so
    the card reads upload first, then import; after a saved upload the message says to import it now, and while an
    uploaded file is not imported the Import button is the page's main button with a line saying how many wait."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    card = html[html.index("<h2>Exports"):html.index('id="imp-final-row"')]
    assert "<h2>Exports</h2>" in card and card.index('id="imp-upload"') < card.index('id="imp-run"') < card.index('id="imp-files"')
    assert re.search(r'<button class="secondary" id="imp-run">Import the uploaded files</button> <span id="imp-msg" class="small"', card)
    r = _import("const cls = {};\n"
                "showUploadsFinal(null, 3, 1); cls.one = [$('#imp-run').className, $('#imp-final').className, $('#imp-msg').textContent];\n"
                "showUploadsFinal(null, 3, 2); cls.two = $('#imp-msg').textContent;\n"
                "$('#imp-msg').textContent = 'kept';\n"
                "showUploadsFinal(null, 3, 0); cls.none = [$('#imp-run').className, $('#imp-final').className, $('#imp-msg').textContent];\n"
                + out("cls"))
    assert r["one"] == ["main-act", "secondary", "1 uploaded file is not imported yet."]
    assert r["two"] == "2 uploaded files are not imported yet."
    assert r["none"] == ["secondary", "main-act", "kept"]   # nothing waiting: final is the main button again, the message stays
    code = (STATIC / "import.js").read_text(encoding="utf-8")
    for saved, says in ((True, True), (False, False)):
        reply = {"file": "f.ris", "verdict": "ok", "message": "fine", "renamed": False, "saved": saved}
        m = run_node(f"{ESC}\n{PAGE}\nglobal.loadImport = async () => {{}}; global.FormData = class {{ append() {{}} }};\n"
                     f"global.api = async (url) => url.startsWith('/api/import/sources') ? {{upload_choice: false}} : {json.dumps(reply)};\n"
                     f"{code}\n"
                     "$('#imp-upload-platform').value = 'scopus'; $('#imp-upload-file').files = [{name: 'f.ris'}];"
                     "await $('#imp-upload').onclick();" + out("$('#imp-upload-msg').innerHTML"))
        assert ('<b>Now import it: press "Import the uploaded files" below.</b>' in m) is says   # a duplicate saves nothing


def test_sample_field_sends_the_mark():
    """Scenario "Mark a sample on the CLI and on the page" (UI half): an imported row has a "Random sample of" field
    with its mark; Save sends the number, an empty field sends null (clear), and a refusal shows the route's message."""
    marked = json.loads(json.dumps(IMPORT))
    marked["files"][0]["sample_of"] = 650000
    r = _import(f"const html = filesTable({json.dumps(marked)}, null);" + out("html"))
    good, off, late = re.findall(r"<tr[^>]*>.*?</tr>", r)[1:4]
    assert 'data-sample="1"' in good and 'value="650000"' in good and 'data-sample-save="1"' in good
    assert 'data-sample="2"' in off and 'value=""' in off and "data-sample" not in late
    r = _import(f"$('#imp-files').innerHTML = filesTable({json.dumps(IMPORT)}, null);"
                "replies['/api/import/sources/1/sample'] = (u, o) => ({source_id: 1, file: 'good.ris', already: false, "
                "sample_of: JSON.parse(o.body).of, change_id: 7});"
                "replies['/api/import/sources/2/sample'] = () => { throw new Error('File 2 (off.ris) holds 52 records'); };"
                "wireSampleFields($('#imp-files'));"
                "$('[data-sample=\"1\"]').value = '650000';"
                "await click('#imp-files', '[data-sample-save]', (b) => b.dataset.sampleSave === '1');"
                "const first = $('#imp-msg').textContent;"
                "$('[data-sample=\"1\"]').value = '';"
                "await click('#imp-files', '[data-sample-save]', (b) => b.dataset.sampleSave === '1');"
                "const second = $('#imp-msg').textContent;"
                "$('[data-sample=\"2\"]').value = '40';"
                "await click('#imp-files', '[data-sample-save]', (b) => b.dataset.sampleSave === '2');"
                "const third = $('#imp-msg').textContent;"
                "$('[data-sample=\"2\"]').value = '0';"
                "await click('#imp-files', '[data-sample-save]', (b) => b.dataset.sampleSave === '2');"
                "const fourth = $('#imp-msg').textContent;"
                # a number the field cannot hold (1e400) reads as empty with badInput: refused, not a clear
                "$('[data-sample=\"1\"]').value = ''; $('[data-sample=\"1\"]').validity = {badInput: true};"
                "await click('#imp-files', '[data-sample-save]', (b) => b.dataset.sampleSave === '1');"
                + out("{calls, first, second, third, fourth, fifth: $('#imp-msg').textContent}"))
    assert r["calls"] == [["/api/import/sources/1/sample", "POST", {"of": 650000}],
                          ["/api/import/sources/1/sample", "POST", {"of": None}],
                          ["/api/import/sources/2/sample", "POST", {"of": 40}]]
    assert "random sample of 650,000 results" in r["first"] and "estimates" in r["first"]
    assert "complete export again" in r["second"]
    assert r["third"] == "File 2 (off.ris) holds 52 records"
    assert "Nothing was changed" in r["fourth"] and "Nothing was changed" in r["fifth"]
