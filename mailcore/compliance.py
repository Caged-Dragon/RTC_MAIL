"""'Mail lock' checks: is the company's e-mail held ONLY by this system?

The pure functions (parse_*, classify_mx, evaluate) use the standard library only so they can be unit-tested
anywhere. `fetch_dns` does the live look-ups over DNS-over-HTTPS (no extra packages)."""
import re
import time
from urllib.parse import quote

# Hosts of mailbox providers / forwarders that must NOT hold or relay company mail.
THIRD_PARTY = [
    ("google.com", "Google Workspace / Gmail"), ("googlemail.com", "Google Workspace / Gmail"),
    ("outlook.com", "Microsoft 365 / Outlook"), ("protection.outlook.com", "Microsoft 365 / Outlook"),
    ("zoho.", "Zoho Mail"), ("yahoodns.net", "Yahoo Mail"), ("secureserver.net", "GoDaddy mail"),
    ("titan.email", "Titan mail"), ("privateemail.com", "Namecheap Private Email"),
    ("registrar-servers.com", "Namecheap email forwarding"), ("improvmx.com", "ImprovMX forwarding"),
    ("forwardemail.net", "Forward Email"), ("mailgun.org", "Mailgun"), ("emailsrvr.com", "Rackspace mail"),
    ("fastmail.com", "Fastmail"), ("messagingengine.com", "Fastmail"), ("protonmail.ch", "Proton Mail"),
    ("proton.ch", "Proton Mail"), ("yandex.net", "Yandex Mail"), ("icloud.com", "iCloud Mail"),
    ("mxroute.com", "MXroute"), ("ovh.net", "OVH mail"), ("hostinger.com", "Hostinger mail"),
]
THIRD_PARTY_SPF = ("_spf.google.com", "spf.protection.outlook.com", "zoho.", "secureserver.net", "mailgun.org", "sendgrid.net", "mailchimp", "mandrillapp.com")


def parse_mx(answers: list[dict]) -> list[tuple[int, str]]:
    """DNS-JSON MX answers -> [(priority, host)] sorted by priority."""
    out = []
    for a in answers or []:
        m = re.match(r"^\s*(\d+)\s+(\S+)\s*$", str(a.get("data", "")))
        if m:
            out.append((int(m.group(1)), m.group(2).lower().rstrip(".")))
    return sorted(out)


def parse_txt(answers: list[dict]) -> list[str]:
    """DNS-JSON TXT answers -> plain strings (multi-part TXT strings are joined)."""
    out = []
    for a in answers or []:
        data = str(a.get("data", ""))
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', data)
        out.append("".join(parts) if parts else data.strip('"'))
    return out


def classify_mx(host: str, allowed: list[str]) -> tuple[bool, str]:
    """(is_allowed, provider_name). Allowed = matches one of the configured regular expressions."""
    h = host.lower().rstrip(".")
    if any(re.search(p, h) for p in allowed):
        return True, "Resend inbound"
    for needle, name in THIRD_PARTY:
        if needle in h:
            return False, name
    return False, "an unknown mail server"


def _check(cid: str, title: str, status: str, detail: str, fix: str = "") -> dict:
    return {"id": cid, "title": title, "status": status, "detail": detail, "fix": fix}


def evaluate(domain: str, mx: list[tuple[int, str]], spf: list[str], dmarc: list[str], dkim: list[str], allowed: list[str]) -> dict:
    checks = []

    # 1) Where does inbound mail go?  Every MX must be the system's own receiver.
    if not mx:
        checks.append(_check("mx", "Inbound mail route", "fail",
                             f"{domain} has no MX record, so nobody can send mail to your addresses.",
                             "Add the MX record shown in Resend → Domains → Receiving (priority 10)."))
    else:
        bad = [(p, h, classify_mx(h, allowed)[1]) for p, h in mx if not classify_mx(h, allowed)[0]]
        if bad:
            names = "; ".join(f"{h} ({who})" for _, h, who in bad)
            checks.append(_check("mx", "Inbound mail route", "fail",
                                 f"Mail for {domain} can also be delivered to: {names}. That mailbox would hold copies outside this system.",
                                 "In your domain's DNS panel delete every MX record except Resend's inbound MX, and cancel/disable any mailbox, "
                                 "alias or forwarding rule at that provider."))
        else:
            checks.append(_check("mx", "Inbound mail route", "ok",
                                 "All MX records point only to your own receiver: " + ", ".join(h for _, h in mx) + "."))

    # 2) SPF: other senders authorised to send as the domain?
    spf_rec = next((t for t in spf if t.lower().startswith("v=spf1")), "")
    if not spf_rec:
        checks.append(_check("spf", "SPF (who may send as you)", "warning", "No SPF record found.",
                             'Add a TXT record: v=spf1 include:amazonses.com ~all'))
    else:
        others = [x for x in THIRD_PARTY_SPF if x in spf_rec.lower()]
        if others:
            checks.append(_check("spf", "SPF (who may send as you)", "warning",
                                 "SPF also authorises other senders: " + ", ".join(others) + ".",
                                 "Remove services you no longer use from the SPF record."))
        else:
            checks.append(_check("spf", "SPF (who may send as you)", "ok", "SPF lists only your sending service."))

    # 3) DMARC: stops other servers from impersonating the domain.
    dm = next((t for t in dmarc if t.lower().startswith("v=dmarc1")), "")
    policy = re.search(r"\bp=([a-z]+)", dm.lower())
    if not dm:
        checks.append(_check("dmarc", "DMARC (blocks impersonation)", "warning", "No DMARC record found.",
                             f"Add TXT _dmarc.{domain}: v=DMARC1; p=quarantine; rua=mailto:admin@{domain}"))
    elif policy and policy.group(1) == "none":
        checks.append(_check("dmarc", "DMARC (blocks impersonation)", "warning", "DMARC is in monitor-only mode (p=none).",
                             "Change to p=quarantine, then p=reject once mail is flowing."))
    else:
        checks.append(_check("dmarc", "DMARC (blocks impersonation)", "ok", f"DMARC policy is {policy.group(1) if policy else 'set'}."))

    # 4) DKIM signature published for the sending service.
    ok_dkim = any("v=dkim1" in t.lower() or "p=" in t.lower() for t in dkim)
    checks.append(_check("dkim", "DKIM (signs your outgoing mail)", "ok" if ok_dkim else "warning",
                         "Resend DKIM key found." if ok_dkim else "No DKIM key found at resend._domainkey.",
                         "" if ok_dkim else "Copy the DKIM TXT record from Resend → Domains."))

    order = {"fail": 2, "warning": 1, "ok": 0}
    worst = max((c["status"] for c in checks), key=lambda s: order[s])
    return {"domain": domain, "status": worst, "checks": checks, "mx": [{"priority": p, "host": h} for p, h in mx],
            "checked_at": int(time.time())}


def _doh(client, name: str, rtype: str) -> list[dict]:
    r = client.get("https://cloudflare-dns.com/dns-query?name=" + quote(name) + "&type=" + rtype,
                   headers={"accept": "application/dns-json"}, timeout=6)
    r.raise_for_status()
    return r.json().get("Answer") or []


def fetch_dns(domain: str) -> dict:
    """Live DNS look-ups. Raises on network failure so the caller can report 'could not check'."""
    import httpx  # imported here so the pure functions above need nothing installed

    with httpx.Client() as c:
        return {
            "mx": parse_mx(_doh(c, domain, "MX")),
            "spf": parse_txt(_doh(c, domain, "TXT")),
            "dmarc": parse_txt(_doh(c, "_dmarc." + domain, "TXT")),
            "dkim": parse_txt(_doh(c, "resend._domainkey." + domain, "TXT")),
        }
