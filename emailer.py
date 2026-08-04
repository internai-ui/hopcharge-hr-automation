"""
emailer.py — Personalised email dispatch for Hopcharge recruitment.

Loads candidate data from the parsed JSON output, composes an HTML email
for each candidate with a valid email address, and sends it via Gmail SMTP
using an App Password (OAuth is not required).

Gmail setup (one-time):
  1. Enable 2-Step Verification on the sending Gmail account.
  2. Go to: Google Account → Security → App Passwords
  3. Generate an app password for "Mail / Other (custom)" and use it here.
     Do NOT use your regular Gmail password — SMTP will reject it.
"""

import base64
import json
import logging
import smtplib
import ssl
import socket
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path
from typing import Optional

from googleapiclient.errors import HttpError

from config import OUTPUT_DIR

# ── Brand logo (white wordmark) embedded inline in email headers via CID ──
# CID embedding is the most reliable way to show a logo in email: the image
# ships inside the message, so it renders even when a client blocks external
# images. The file is looked up next to this module first, then in the app's
# static folder. If it can't be found, the email still sends — the header
# falls back to the text wordmark.
def _logo_path() -> Optional[Path]:
    candidates = []
    try:
        here = Path(__file__).resolve().parent
        candidates += [here / "hopcharge-white.png", here / "static" / "hopcharge-white.png"]
    except Exception:
        pass
    try:
        from app_paths import BUNDLE_DIR, STATIC_DIR
        candidates += [STATIC_DIR / "hopcharge-white.png", BUNDLE_DIR / "hopcharge-white.png"]
    except Exception:
        pass
    for p in candidates:
        try:
            if p.exists():
                return p
        except Exception:
            continue
    return None


LOGO_CID = "hopcharge_logo_white"

logger = logging.getLogger("volt_cv.emailer")

# ──────────────────────────────────────────────
# IPv4-forced SMTP for macOS compatibility
# ──────────────────────────────────────────────

class SMTP_IPv4(smtplib.SMTP):
    """SMTP client that forces IPv4 (works around macOS IPv6 issues)."""
    def _get_socket(self, host, port, timeout):
        """Create socket with IPv4 only."""
        for family, socktype, proto, canonname, sockaddr in socket.getaddrinfo(
            host, port, socket.AF_INET, socket.SOCK_STREAM
        ):
            try:
                sock = socket.socket(family, socktype, proto)
                if timeout is not None:
                    sock.settimeout(timeout)
                sock.connect(sockaddr)
                return sock
            except socket.error:
                continue
        raise socket.error("Could not connect to %s:%s" % (host, port))


# ──────────────────────────────────────────────

SUBJECT = "Thank You for Applying to Hopcharge — Next Steps"

# HTML email template.
# Placeholders: {name}, {email}, {form_link}
HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Hopcharge — Next Steps</title></head>
<body style="margin:0;padding:0;background:#eef1f6;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef1f6;padding:28px 12px;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border:1px solid #e6e9f0;border-radius:12px;overflow:hidden;">
        <tr><td style="background:#1F2D59;padding:30px 40px;">
          <img src="cid:hopcharge_logo_white" alt="Hopcharge" height="30" style="display:block;height:30px;width:auto;border:0;outline:none;text-decoration:none;">
          <div style="color:#9FB0D8;font-size:11px;letter-spacing:3px;text-transform:uppercase;margin-top:10px;">Recruitment</div>
        </td></tr>
        <tr><td style="padding:34px 40px;">
          <div style="color:#1F2D59;font-size:22px;font-weight:700;margin-bottom:16px;">Dear {name},</div>
          {body_html}
          <div style="margin:0 0 22px;">
            <a href="{form_link}" style="display:inline-block;background:#2F5BEA;color:#ffffff;font-size:15px;font-weight:700;text-decoration:none;padding:14px 32px;border-radius:28px;">Complete Assessment Form &rarr;</a>
          </div>
          {status_block}
          <hr style="border:none;border-top:1px solid #e6e9f0;margin:24px 0;">
          <p style="margin:0 0 16px;font-size:15px;line-height:1.7;color:#5b6472;">Should you have any questions, please do not hesitate to reach out to our recruitment team. We look forward to learning more about you.</p>
          <p style="margin:0;font-size:15px;line-height:1.7;color:#5b6472;">Warm regards,<br><strong style="color:#1F2D59;">Hopcharge Recruitment Team</strong></p>
        </td></tr>
        <tr><td style="padding:20px 40px 26px;border-top:1px solid #eef1f6;">
          <p style="margin:0 0 8px;font-size:13px;color:#5b6472;">Questions? Write to <a href="mailto:{sender}" style="color:#E8537F;font-weight:700;text-decoration:underline;">{sender}</a></p>
          <p style="margin:0;font-size:11px;line-height:1.6;color:#9aa3b6;">This email was sent to {email} as part of the Hopcharge recruitment process. If you believe this was received in error, please disregard this message.<br>&#169; 2026 Hopcharge. All rights reserved.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

# Plain-text fallback for email clients that cannot render HTML
TEXT_TEMPLATE = """\
Dear {name},

Thank you for your interest in joining Hopcharge. We appreciate the time and effort
you have invested in your application and are pleased to confirm receipt.

As part of our recruitment process, please complete the following assessment form
at your earliest convenience:

{form_link}

Completing this form is a mandatory step. Candidates who do not respond within five
business days may not be considered for the current hiring cycle.

Should you have any questions, please reach out to our recruitment team.

Warm regards,
Hopcharge Recruitment Team
"""


# ──────────────────────────────────────────────
# Data helpers
# ──────────────────────────────────────────────

def load_candidates() -> list[dict]:
    """
    Load the most recently parsed candidates from candidates.json.
    Raises FileNotFoundError if the output file does not exist yet.
    """
    path = OUTPUT_DIR / "candidates.json"
    if not path.exists():
        raise FileNotFoundError(
            "No parsed candidates found. Please parse CV files first."
        )
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ──────────────────────────────────────────────
# Email composition
# ──────────────────────────────────────────────

def _sanitize_name(name: str) -> str:
    """
    Normalize a few "smart" punctuation characters that look odd if copy-pasted
    from a resume (curly quotes, non-breaking spaces, en/em dashes).

    Does NOT strip non-ASCII letters: every message here is sent with
    _charset="utf-8" and SMTPUTF8 is enabled on the connection, so genuine
    unicode names (accents, non-Latin scripts) are fully supported end to end.
    An earlier version did `name.encode('ascii', errors='ignore')`, which
    silently mangled names mid-word instead of just dropping accents (e.g.
    a name with an e-acute lost that letter entirely instead of keeping a
    plain "e").
    """
    replacements = {
        '\xa0': ' ',     # non-breaking space → space
        '\u2013': '-',   # en dash → hyphen
        '\u2014': '-',   # em dash → hyphen
        '\u2019': "'",   # right single quote → apostrophe
        '\u201c': '"',   # left double quote → quote
        '\u201d': '"',   # right double quote → quote
    }
    for uni, ascii_char in replacements.items():
        name = name.replace(uni, ascii_char)
    return name


def _body_to_html(body_text: str) -> str:
    """Turn an editable plain-text body (paragraphs separated by blank lines)
    into the styled <p> blocks the email template expects. HTML-escapes the
    text so admin input can't break the layout; {name} is already substituted
    before this is called."""
    import html as _html
    paras = [p.strip() for p in (body_text or "").split("\n\n") if p.strip()]
    style = "margin:0 0 16px;font-size:15px;line-height:1.7;color:#5b6472;"
    return "".join(
        f'<p style="{style}">{_html.escape(p).replace(chr(10), "<br>")}</p>'
        for p in paras
    )


def _build_message(
    sender_address: str,
    recipient_name: str,
    recipient_email: str,
    form_link: str,
) -> MIMEMultipart:
    """Compose a MIME email with both HTML and plain-text parts."""
    safe_name = _sanitize_name(recipient_name)
    
    # Application Status button disabled for now (per request). To re-enable,
    # uncomment the two lines below and remove the status_block = "" line.
    # try:
    #     import status_portal
    #     status_block = status_portal.status_button_html(recipient_email)
    # except Exception:
    #     status_block = ""
    status_block = ""

    # Pull editable subject + body from admin settings (falls back to defaults).
    try:
        from admin_settings import get_email
        _cfg = get_email("recruitment")
        _subject = _cfg.get("subject") or SUBJECT
        _body_text = (_cfg.get("body") or "").replace("{name}", safe_name)
    except Exception:
        _subject = SUBJECT
        _body_text = (
            "Thank you for your interest in joining Hopcharge. We have received your "
            "application successfully.\n\nPlease complete the assessment form below."
        )
    body_html = _body_to_html(_body_text)

    # multipart/related wraps the alternative parts + the inline logo image,
    # so the cid:hopcharge_logo_white reference in the HTML resolves.
    msg = MIMEMultipart("related")
    msg["Subject"] = _subject
    msg["From"]    = f"Hopcharge Recruitment <{sender_address}>"
    msg["To"]      = recipient_email

    alt = MIMEMultipart("alternative")
    # Attach plain text first — email clients use the last part they can render
    alt.attach(MIMEText(
        TEXT_TEMPLATE.format(name=safe_name, email=recipient_email, form_link=form_link),
        "plain",
        _charset="utf-8",
    ))
    alt.attach(MIMEText(
        HTML_TEMPLATE.format(name=safe_name, email=recipient_email, form_link=form_link, sender=sender_address, status_block=status_block, body_html=body_html),
        "html",
        _charset="utf-8",
    ))
    msg.attach(alt)

    # Attach the white logo inline (best-effort; email still sends if missing,
    # falling back to the img alt text "Hopcharge").
    lp = _logo_path()
    if lp is not None:
        try:
            img = MIMEImage(lp.read_bytes())
            img.add_header("Content-ID", f"<{LOGO_CID}>")
            img.add_header("Content-Disposition", "inline", filename="hopcharge-white.png")
            msg.attach(img)
        except Exception as exc:
            logger.warning("Could not embed email logo: %s", exc)
    return msg


# ──────────────────────────────────────────────
# Main dispatch function
# ──────────────────────────────────────────────

def send_campaign(
    gmail_address: str,
    app_password: str,
    form_link: str,
    candidates: Optional[list[dict]] = None,
    tracking_base_url: Optional[str] = None,
    gmail_service=None,
) -> dict:
    """
    Send a personalised email to every candidate that has a valid email address.

    Parameters
    ----------
    gmail_address     : str   The Gmail account used to send (e.g. hr@hopcharge.com).
                              With gmail_service set, this is the connected OAuth
                              account's own address (used only for the "From" header
                              and the reply-to hint in the template) — not user-typed.
    app_password      : str   A Gmail App Password. Ignored when gmail_service is set.
    form_link         : str   The Google Forms URL (used as fallback / stored as base).
    candidates        : list  Optional — uses the parsed JSON output if not supplied.
    tracking_base_url : str   Optional — if set (e.g. "https://hr.hopcharge.com" or
                              "http://localhost:8000"), each email links to
                              "{tracking_base_url}/t/{token}" so we can measure the
                              time each candidate takes to complete the form. If not
                              set, the raw form_link is used (no timing).
    gmail_service      : Resource  Optional — a Gmail API service object (see
                              gmail_oauth.get_gmail_service()). When provided, mail is
                              sent via the Gmail API instead of SMTP + App Password,
                              and each send is registered with email_replies for
                              reply tracking. When None (default), behaviour is
                              unchanged from before this feature existed.

    Returns
    -------
    dict with keys: total, sent, failed, skipped_no_email, tracked, results
    """
    if candidates is None:
        candidates = load_candidates()

    # Separate candidates with usable email addresses
    valid   = [c for c in candidates if c.get("email") and "@" in c["email"]]
    skipped = len(candidates) - len(valid)

    if not valid:
        return {
            "total": len(candidates),
            "sent": 0, "failed": 0,
            "skipped_no_email": skipped,
            "tracked": 0,
            "results": [],
        }

    results = []
    tracked = 0

    # If tracking is enabled, import the tracking module lazily
    _ft = None
    if tracking_base_url:
        try:
            import form_tracking as _ft
            tracking_base_url = tracking_base_url.rstrip("/")
        except Exception as exc:
            logger.warning("Tracking unavailable (%s) — sending raw form links.", exc)
            _ft = None

    def _candidate_link(email: str, name: str) -> str:
        nonlocal tracked
        if not _ft:
            return form_link
        try:
            token = _ft.issue_token(email, name)
            tracked += 1
            return f"{tracking_base_url}/t/{token}"
        except Exception as exc:
            logger.warning("Could not issue token for %s: %s", email, exc)
            return form_link

    if gmail_service is not None:
        # ── Gmail API transport (OAuth) ──
        for cand in valid:
            name  = (cand.get("full_name") or "").strip() or "Applicant"
            email = cand["email"].strip()
            candidate_link = _candidate_link(email, name)
            try:
                msg = _build_message(gmail_address, name, email, candidate_link)
                raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
                sent_msg = gmail_service.users().messages().send(
                    userId="me", body={"raw": raw}
                ).execute()
                results.append({"name": name, "email": email, "status": "sent"})
                logger.info("Sent (Gmail API) → %s <%s>", name, email)
                try:
                    import email_replies
                    email_replies.register_sent(
                        sent_msg.get("threadId"), sent_msg.get("id"),
                        email, name, form_link,
                    )
                except Exception as exc:
                    # A broken replies store must never block sending.
                    logger.warning("Reply tracking registration failed for %s "
                                    "(send still succeeded): %s", email, exc)
            except HttpError as exc:
                if exc.status_code in (401, 403):
                    raise ValueError(
                        "Gmail connection has expired or was revoked. "
                        "Please reconnect Gmail on the Send Emails page."
                    )
                results.append({"name": name, "email": email,
                                "status": "failed", "error": str(exc)})
                logger.error("Failed (Gmail API) → %s <%s>: %s", name, email, exc)
            except Exception as exc:
                results.append({"name": name, "email": email,
                                "status": "failed", "error": str(exc)})
                logger.error("Failed (Gmail API) → %s <%s>: %s", name, email, exc)

    else:
        # ── SMTP transport (App Password, fallback) ──
        context = ssl.create_default_context()
        try:
            with SMTP_IPv4("smtp.gmail.com", 587, timeout=10) as server:
                server.ehlo()
                server.starttls(context=context)
                # Enable UTF8 support for headers/content
                if server.has_extn("SMTPUTF8"):
                    server.enable_smtputf8()
                server.login(gmail_address, app_password)

                for cand in valid:
                    name  = (cand.get("full_name") or "").strip() or "Applicant"
                    email = cand["email"].strip()
                    candidate_link = _candidate_link(email, name)

                    try:
                        msg = _build_message(gmail_address, name, email, candidate_link)
                        # Use as_bytes() and encode to ensure proper UTF-8 handling with SMTP
                        msg_bytes = msg.as_bytes()
                        server.sendmail(gmail_address, email, msg_bytes)
                        results.append({"name": name, "email": email, "status": "sent"})
                        logger.info("Sent  → %s <%s>", name, email)

                    except Exception as exc:
                        results.append({"name": name, "email": email,
                                        "status": "failed", "error": str(exc)})
                        logger.error("Failed → %s <%s>: %s", name, email, exc)

        except smtplib.SMTPAuthenticationError:
            raise ValueError(
                "Gmail authentication failed. Verify the sender address and ensure "
                "you are using a Gmail App Password, not your account password."
            )
        except OSError as exc:
            # Network connectivity issues
            raise RuntimeError(
                f"Network error connecting to Gmail SMTP:\n"
                f"  Error: {exc}\n"
                f"  Possible causes:\n"
                f"    • No internet connection\n"
                f"    • Firewall blocking port 587\n"
                f"    • Corporate network restrictions\n"
                f"  Try: Restart your internet or use a different network"
            )
        except Exception as exc:
            raise RuntimeError(f"SMTP connection error: {exc}")

    sent   = sum(1 for r in results if r["status"] == "sent")
    failed = sum(1 for r in results if r["status"] == "failed")

    logger.info("Campaign complete — sent: %d, failed: %d, skipped: %d",
                sent, failed, skipped)

    return {
        "total":            len(candidates),
        "sent":             sent,
        "failed":           failed,
        "skipped_no_email": skipped,
        "tracked":          tracked,
        "results":          results,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ONBOARDING EMAIL  — sent to candidates finalised after Round 2
# ══════════════════════════════════════════════════════════════════════════════

ONBOARD_SUBJECT = "Welcome to the Hopcharge Family 🎉"

ONBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Welcome to Hopcharge</title></head>
<body style="margin:0;padding:0;background:#eef1f6;font-family:Arial,Helvetica,sans-serif;">

  <!-- Outer wrapper -->
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:#eef1f6;padding:28px 12px;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0"
             style="max-width:600px;width:100%;background:#ffffff;
                    border:1px solid #e6e9f0;border-radius:12px;overflow:hidden;">

        <!-- ── HERO BAND ── -->
        <tr><td style="background:#1F2D59;padding:30px 40px;text-align:center;">
          <div style="color:#ffffff;font-size:25px;font-weight:700;letter-spacing:-0.5px;">Hopcharge</div>
          <div style="color:#9FB0D8;font-size:11px;letter-spacing:3px;text-transform:uppercase;margin-top:6px;">People · Growth · Impact</div>
          <div style="font-size:52px;line-height:1;margin-top:24px;margin-bottom:16px;">&#127881;</div>
          <h1 style="margin:0 0 8px;font-size:26px;font-weight:800;color:#ffffff;line-height:1.2;letter-spacing:-0.5px;">
            Welcome aboard, {first_name}!
          </h1>
          <p style="margin:0;font-size:14px;color:#9FB0D8;line-height:1.6;">
            You are officially part of the Hopcharge family.
          </p>
        </td></tr>

        <!-- ── PERSONAL MESSAGE ── -->
        <tr><td style="padding:34px 40px 28px;">
          <p style="margin:0 0 16px;font-size:15px;line-height:1.7;color:#5b6472;">
            Dear <strong style="color:#1F2D59;">{name}</strong>,
          </p>
          <p style="margin:0 0 16px;font-size:15px;line-height:1.7;color:#5b6472;">
            We are thrilled to welcome you to <strong style="color:#1F2D59;">Hopcharge</strong>.
            After a competitive selection process, you stood out — and we could not be more
            excited to have you join us as <strong style="color:#1F2D59;">{role_display}</strong>.
          </p>
          <p style="margin:0 0 24px;font-size:15px;line-height:1.7;color:#5b6472;">
            Before your first day, we need a few details from you to complete the onboarding
            process. Please take a few minutes to fill in the form below — it covers everything
            HR needs to get you set up from day one.
          </p>

          <!-- CTA -->
          <div style="margin:0 0 22px;">
            <a href="{form_link}"
               style="display:inline-block;background:#2F5BEA;color:#ffffff;font-size:15px;
                      font-weight:700;text-decoration:none;padding:14px 32px;border-radius:28px;">
              Complete Onboarding Form &rarr;
            </a>
          </div>

          {status_block}

          <p style="margin:0;font-size:13px;line-height:1.7;color:#5b6472;">
            Please complete this form within <strong style="color:#3a4150;">3 business days</strong>
            so we can prepare everything for your arrival on time.
          </p>
        </td></tr>

        <!-- ── MILESTONE STRIP ── -->
        <tr><td style="background:#f7f9fc;padding:24px 40px;border-top:1px solid #e6e9f0;border-bottom:1px solid #e6e9f0;">
          <p style="margin:0 0 18px;font-size:11px;letter-spacing:3px;text-transform:uppercase;
                    color:#8a93a6;text-align:center;">What happens next</p>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td width="33%" style="text-align:center;padding:0 8px;">
                <div style="width:40px;height:40px;border-radius:50%;background:#1F2D59;
                            display:inline-block;line-height:40px;font-size:18px;">&#128203;</div>
                <div style="margin-top:8px;font-size:12px;font-weight:700;color:#1f2a44;">Fill the form</div>
                <div style="margin-top:3px;font-size:11px;color:#8a93a6;">Within 3 days</div>
              </td>
              <td width="33%" style="text-align:center;padding:0 8px;">
                <div style="width:40px;height:40px;border-radius:50%;background:#2F5BEA;
                            display:inline-block;line-height:40px;font-size:18px;">&#128222;</div>
                <div style="margin-top:8px;font-size:12px;font-weight:700;color:#1f2a44;">HR will reach out</div>
                <div style="margin-top:3px;font-size:11px;color:#8a93a6;">Joining details &amp; offer</div>
              </td>
              <td width="33%" style="text-align:center;padding:0 8px;">
                <div style="width:40px;height:40px;border-radius:50%;background:#E8537F;
                            display:inline-block;line-height:40px;font-size:18px;">&#9889;</div>
                <div style="margin-top:8px;font-size:12px;font-weight:700;color:#1f2a44;">First day!</div>
                <div style="margin-top:3px;font-size:11px;color:#8a93a6;">Plug in &amp; charge up</div>
              </td>
            </tr>
          </table>
        </td></tr>

        <!-- ── WARM CLOSE ── -->
        <tr><td style="padding:28px 40px 34px;">
          <p style="margin:0 0 16px;font-size:15px;line-height:1.75;color:#5b6472;">
            We are building something meaningful at Hopcharge — and we are genuinely glad to have
            you on this journey with us. If you have any questions before your first day, just
            reply to this email and we will get back to you promptly.
          </p>
          <p style="margin:0 0 4px;font-size:15px;color:#5b6472;">Warm regards &amp; welcome aboard,</p>
          <p style="margin:0;font-size:15px;font-weight:800;color:#1F2D59;">Hopcharge People Team</p>
        </td></tr>

        <!-- ── FOOTER ── -->
        <tr><td style="padding:18px 40px 24px;border-top:1px solid #eef1f6;">
          <p style="margin:0 0 6px;font-size:13px;color:#5b6472;">
            Questions? Write to
            <a href="mailto:{sender}" style="color:#E8537F;font-weight:700;text-decoration:underline;">{sender}</a>
          </p>
          <p style="margin:0;font-size:11px;line-height:1.6;color:#9aa3b6;">
            This email was sent to {email}. &#169; 2026 Hopcharge. All rights reserved.
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>
"""

ONBOARD_TEXT = """\
Welcome to the Hopcharge Family!

Dear {name},

We are thrilled to welcome you to Hopcharge as {role_display}.

Please complete your onboarding form within 3 business days:
{form_link}

What happens next:
1. Complete the onboarding form (within 3 days)
2. HR will reach out with your joining details and offer letter
3. First day — plug in and charge up!

If you have any questions, just reply to this email.

Warm regards,
Hopcharge People Team
"""


def _build_onboarding_message(
    sender_address: str,
    recipient_name: str,
    recipient_email: str,
    form_link: str,
    role: str = "",
) -> MIMEMultipart:
    """Compose the special onboarding congratulations email."""
    safe_name = _sanitize_name(recipient_name)
    first_name = safe_name.split()[0] if safe_name else "there"
    role_display = role.replace("_", " ").title() if role else "your new role"

    # Application Status button disabled for now (per request). To re-enable,
    # uncomment the two lines below and remove the status_block = "" line.
    # try:
    #     import status_portal
    #     status_block = status_portal.status_button_html(recipient_email)
    # except Exception:
    #     status_block = ""
    status_block = ""

    try:
        from admin_settings import get_email
        _ob_subject = get_email("onboarding").get("subject") or ONBOARD_SUBJECT
    except Exception:
        _ob_subject = ONBOARD_SUBJECT

    msg = MIMEMultipart("alternative")
    msg["Subject"] = _ob_subject
    msg["From"] = f"Hopcharge People Team <{sender_address}>"
    msg["To"] = recipient_email

    msg.attach(MIMEText(
        ONBOARD_TEXT.format(
            name=safe_name, first_name=first_name,
            email=recipient_email, form_link=form_link,
            role_display=role_display,
        ),
        "plain", _charset="utf-8",
    ))
    msg.attach(MIMEText(
        ONBOARD_HTML.format(
            name=safe_name, first_name=first_name,
            email=recipient_email, form_link=form_link,
            role_display=role_display,
            sender=sender_address, status_block=status_block,
        ),
        "html", _charset="utf-8",
    ))
    return msg


def send_onboarding(
    gmail_address: str,
    app_password: str,
    form_link: str,
    candidates: list,
) -> dict:
    """
    Send the onboarding congratulations email to a list of finalised candidates.
    `candidates` should be a list of accepted-candidate dicts (name, email, role).
    """
    valid = [c for c in candidates if c.get("email") and "@" in c["email"]]
    skipped = len(candidates) - len(valid)

    results = []
    context = ssl.create_default_context()

    try:
        with SMTP_IPv4("smtp.gmail.com", 587, timeout=10) as server:
            server.ehlo()
            server.starttls(context=context)
            if server.has_extn("SMTPUTF8"):
                server.enable_smtputf8()
            server.login(gmail_address, app_password)

            for cand in valid:
                name  = (cand.get("name") or "").strip() or "Candidate"
                email = cand["email"].strip()
                role  = cand.get("role", "")
                try:
                    msg = _build_onboarding_message(
                        gmail_address, name, email, form_link, role)
                    server.sendmail(gmail_address, email, msg.as_bytes())
                    results.append({"name": name, "email": email, "status": "sent"})
                    logger.info("Onboarding sent → %s <%s>", name, email)
                except Exception as exc:
                    results.append({"name": name, "email": email,
                                    "status": "failed", "error": str(exc)})
                    logger.error("Onboarding failed → %s <%s>: %s", name, email, exc)

    except smtplib.SMTPAuthenticationError:
        raise ValueError(
            "Gmail authentication failed. Check the sender address and App Password.")
    except OSError as exc:
        raise RuntimeError(f"Network error: {exc}")

    sent   = sum(1 for r in results if r["status"] == "sent")
    failed = sum(1 for r in results if r["status"] == "failed")
    return {"total": len(candidates), "sent": sent, "failed": failed,
            "skipped_no_email": skipped, "results": results}
