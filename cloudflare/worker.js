/**
 * worker.js — Cloudflare Workers version of app.py's /t/<token> route.
 *
 * Does exactly what form_tracking.py's track_and_redirect() does, just
 * running on Cloudflare's free tier instead of your own server:
 *   1. Look up the token in KV (issued locally by form_tracking.issue_token(),
 *      pushed here by push_token_to_cloudflare()).
 *   2. Record the click (first-click-wins, same as RECLICK_POLICY="first").
 *   3. Redirect to the real Google Form, pre-filling the candidate's email
 *      if an email_entry_id is configured.
 *
 * KV layout (namespace bound as TRACKING_KV — see wrangler.toml):
 *   "token:<token>"   -> { email, name, issued_at, click_time,
 *                          click_count, last_click_time }
 *   "config:tracking" -> { base_form_url, email_entry_id }
 *     (pushed whenever the dashboard's tracking config is saved — same
 *     config _load_tracking_config() reads for the self-hosted route)
 *
 * Deploy: see cloudflare/README.md.
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const match = url.pathname.match(/^\/t\/([A-Za-z0-9_-]+)$/);
    if (!match) {
      return new Response("Not found", { status: 404 });
    }
    const token = match[1];

    const [rawToken, rawConfig] = await Promise.all([
      env.TRACKING_KV.get(`token:${token}`),
      env.TRACKING_KV.get("config:tracking"),
    ]);

    const config = rawConfig ? JSON.parse(rawConfig) : null;

    if (!rawToken) {
      // Unknown/expired token — same fallback app.py's route uses: if a
      // base form URL is configured, send them there anyway rather than
      // showing an error (a candidate should never see a dead link).
      if (config && config.base_form_url) {
        return Response.redirect(config.base_form_url, 302);
      }
      return new Response("Invalid or expired link.", { status: 404 });
    }

    if (!config || !config.base_form_url) {
      return new Response("Form URL not configured yet.", { status: 500 });
    }

    const rec = JSON.parse(rawToken);
    const now = new Date().toISOString();
    rec.click_count = (rec.click_count || 0) + 1;
    rec.last_click_time = now;
    if (!rec.click_time) {
      rec.click_time = now;      // first open — this is the timestamp
      rec.status = "opened";     // time-to-fill is measured from
    }
    // Fire-and-forget-ish, but awaited so the write lands before we
    // respond — Workers can be recycled right after fetch() returns.
    await env.TRACKING_KV.put(`token:${token}`, JSON.stringify(rec));

    let target = config.base_form_url;
    if (config.email_entry_id && rec.email) {
      const sep = target.includes("?") ? "&" : "?";
      target = `${target}${sep}${encodeURIComponent(config.email_entry_id)}=${encodeURIComponent(rec.email)}`;
    }
    return Response.redirect(target, 302);
  },
};
