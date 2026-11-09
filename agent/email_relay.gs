// Daily Job Agent: the website's email sender (runs free on Google Apps Script).
// Setup: script.google.com > New project > paste this > Deploy > New deployment > Web app,
// "Execute as: Me", "Who has access: Anyone" > Deploy > Authorize > copy the Web app URL into the app.
// Privacy: nothing is stored. The sent copy is moved to Trash right away (Gmail empties Trash after 30 days),
// and the per-address counter keeps only a scrambled fingerprint of the address for 6 hours.
const KEY = '__KEY__';
const PER_ADDRESS_PER_DAY = 3;

function doPost(e) {
  try {
    const d = JSON.parse(e.postData.contents);
    if (d.key !== KEY) return reply({ok: false, error: 'wrong key'});
    const to = String(d.to || '').trim();
    if (!/^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$/.test(to)) return reply({ok: false, error: 'not a valid email address'});
    const hash = Utilities.base64Encode(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, to.toLowerCase()));
    const cache = CacheService.getScriptCache();
    const n = Number(cache.get(hash) || 0);
    if (n >= PER_ADDRESS_PER_DAY) return reply({ok: false, error: 'too many emails to this address today'});
    cache.put(hash, String(n + 1), 21600);
    const subject = 'Daily Job Agent: ' + String(d.subject || '').replace(/^Daily Job Agent:\s*/, '').slice(0, 180);
    const atts = (d.attachments || []).slice(0, 40).map(function (a) {
      return Utilities.newBlob(Utilities.base64Decode(a.data), a.type || 'application/pdf', String(a.name).slice(0, 120));
    });
    const opts = {name: 'Daily Job Agent', attachments: atts};
    if (d.html) opts.htmlBody = String(d.html);
    GmailApp.sendEmail(to, subject, String(d.text || ''), opts);
    try {  // don't keep a copy
      Utilities.sleep(2000);
      GmailApp.search('in:sent newer_than:1d to:' + to).forEach(function (t) { t.moveToTrash(); });
    } catch (err) {}
    return reply({ok: true, left_today: MailApp.getRemainingDailyQuota()});
  } catch (err) {
    return reply({ok: false, error: String(err).slice(0, 200)});
  }
}

function doGet() { return reply({ok: true, service: 'Daily Job Agent email sender'}); }

function reply(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
