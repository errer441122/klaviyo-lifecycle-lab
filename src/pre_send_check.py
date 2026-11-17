"""Pre-send check: run it before pasting a template into Klaviyo.

It looks for the mistakes that survive a read-through, because the email looks
fine in the editor and only breaks for some recipients:

- a merge tag with no fallback, or an empty one: a profile without a first
  name reads "Ciao , grazie..."
- a link that goes nowhere: empty, "#", plain http or a relative path
- a hand-typed UTM that breaks the taxonomy in docs/flows.md. Links without
  UTMs pass, because Klaviyo's UTM tracking adds them when the email is sent;
  check those by clicking a link in a test send
- no unsubscribe link
- a subject longer than a phone shows, or a preview text that repeats it

    python src/pre_send_check.py
    python src/pre_send_check.py welcome.html --subject "Benvenuto" --preview "..."

With no files it checks the seven flow emails in build_templates.py; CI runs
it that way, so a broken template fails the build before it reaches Klaviyo.
With files it checks any Klaviyo template: copy the HTML out of the code
editor into a file. Subject and preview text are checked only if given.
Exits 1 if anything needs fixing.

Pure standard library.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from build_templates import EMAILS, render_html, render_text

MAX_SUBJECT = 50  # roughly what a phone inbox shows before cutting the subject
TAG = re.compile(r"{{\s*(.*?)\s*}}")
GUARD = re.compile(r"{%\s*if\s+([\w.]+)\s*%}(.*?){%\s*endif\s*%}", re.S)
HREF = re.compile(r'href="([^"]*)"')


def _unguarded_tags(source: str) -> list[str]:
    """Merge tags, minus the ones inside an {% if x %} block that tests x."""

    def drop(m: re.Match) -> str:
        return re.sub(r"{{\s*" + re.escape(m.group(1)) + r"\s*}}", "", m.group(2))

    return TAG.findall(GUARD.sub(drop, source))


def _fallback_problem(tag: str) -> str | None:
    name, *filters = (part.strip() for part in tag.split("|"))
    for f in filters:
        if f.startswith("default:"):
            if f[len("default:"):].strip().strip("'\""):
                return None
            return f"{{{{ {name} }}}} falls back to nothing, so a profile without it reads as a gap"
    return f"{{{{ {name} }}}} has no fallback"


def _link_problems(html: str) -> list[str]:
    problems = []
    for href in HREF.findall(html):
        url = href.strip()
        if url.startswith(("{{", "{%")):
            continue  # filled in by Klaviyo; the merge tag itself is checked separately
        if url in ("", "#"):
            problems.append(f'link goes nowhere: href="{href}"')
        elif not url.startswith("https://"):
            problems.append(f"link is not an https address: {url}")
        else:
            utm = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items() if k.startswith("utm_")}
            if utm and (utm.get("utm_source") != "klaviyo" or utm.get("utm_medium") != "email"
                        or not utm.get("utm_campaign", "").startswith("lifecycle_")):
                problems.append(f"UTMs do not follow docs/flows.md: {url}")
    return problems


def _plain(s: str) -> str:
    return re.sub(r"\W+", "", s).lower()


def check(html: str, text: str | None = None, subject: str | None = None,
          preheader: str | None = None) -> list[str]:
    """Problems a recipient would see. An empty list means ready to paste.

    A part passed as None is not checked: a template copied out of Klaviyo is
    only the HTML, the subject lives on the flow action.
    """
    problems = [p for tag in _unguarded_tags(html) if (p := _fallback_problem(tag))]
    problems += _link_problems(html)
    if "{% unsubscribe %}" not in html or (text is not None and "{% unsubscribe %}" not in text):
        problems.append("no {% unsubscribe %} link in the HTML or the plain-text part")
    if subject is not None:
        subject = subject.strip()
        if not subject:
            problems.append("no subject")
        elif len(subject) > MAX_SUBJECT:
            problems.append(f"subject is {len(subject)} characters; a phone cuts it at about {MAX_SUBJECT}")
    if preheader is not None:
        preheader = preheader.strip()
        if not preheader:
            problems.append("no preview text: the inbox shows the first line of the email instead")
        elif subject and _plain(preheader) == _plain(subject):
            problems.append("preview text repeats the subject")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Check email templates before they go live.")
    p.add_argument("files", nargs="*", type=Path,
                   help="HTML templates copied out of Klaviyo (default: the seven flow emails here)")
    p.add_argument("--subject", help="subject line to check together with the files")
    p.add_argument("--preview", help="preview text to check together with the files")
    args = p.parse_args(argv)

    if args.files:
        results = [
            (f.name, args.subject or "",
             check(f.read_text(encoding="utf-8", errors="replace"), None, args.subject, args.preview))
            for f in args.files
        ]
    else:
        results = [
            (e["key"], e["subject"], check(render_html(e), render_text(e), e["subject"], e["preheader"]))
            for e in EMAILS
        ]
    failed = 0
    for name, subject, problems in results:
        failed += bool(problems)
        print(f"  {name}  {'FIX' if problems else 'ok '}  {subject}")
        for problem in problems:
            print(f"         - {problem}")
    print(f"\n{len(results) - failed} of {len(results)} ready to paste.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # subjects may carry accents; Windows consoles default to cp1252
    raise SystemExit(main())
