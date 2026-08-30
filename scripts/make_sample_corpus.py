"""Generate a sample corpus with deliberately planted issues so the demo has
something to find:

- 'OrderFlow' billing system: documented, but only by one author (bus factor)
- 'legacy reconciliation script': mentioned in 3 docs, explained nowhere (gap)
- Deployment runbook: backdated file mtime to look ~2.5 years old (stale)
- Two docs that disagree about the database failover procedure (conflict)
- One doc with no author metadata (orphan)

Usage: python scripts/make_sample_corpus.py [output_dir]
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "sample_corpus")
OUT.mkdir(parents=True, exist_ok=True)


def make_pptx():
    from pptx import Presentation

    prs = Presentation()
    slides = [
        ("OrderFlow Billing System — Onboarding", "Training deck for new engineers joining the payments team."),
        ("Architecture Overview",
         "OrderFlow ingests orders from the storefront API, validates them against the customer ledger, "
         "and emits invoices to the billing queue. The invoice worker consumes the billing queue and posts "
         "charges to the payment gateway. Failed charges go to the retry queue with exponential backoff."),
        ("Data Stores",
         "OrderFlow uses PostgreSQL for the customer ledger and order state. Redis holds idempotency keys "
         "for 24 hours. Nightly, the legacy reconciliation script compares the ledger against gateway "
         "settlement reports — ask Priya if the reconciliation numbers look off."),
        ("Deployment",
         "Deployments go through the standard pipeline. See the deployment runbook for the full procedure "
         "including database migration ordering and the rollback protocol."),
        ("On-call Notes",
         "Most pages are retry-queue depth alarms. If the legacy reconciliation script fails, invoices can "
         "be double-posted the next day, so treat reconciliation failures as high priority."),
    ]
    for title, body in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
    notes = prs.slides[1].notes_slide.notes_text_frame
    notes.text = ("Presenter note: the billing queue is Kafka topic 'billing.invoices.v2'. "
                  "The v1 topic still receives traffic from the mobile app — nobody has documented why.")
    prs.core_properties.author = "Priya Sharma"
    prs.core_properties.title = "OrderFlow Billing System Onboarding"
    now = datetime.now(timezone.utc)
    prs.core_properties.created = now - timedelta(days=200)
    prs.core_properties.modified = now - timedelta(days=45)
    prs.save(OUT / "orderflow_onboarding.pptx")


def make_docx_runbook():
    import docx

    d = docx.Document()
    d.add_heading("Deployment Runbook — OrderFlow", 0)
    d.add_heading("Pre-deployment checks", 1)
    d.add_paragraph("Confirm the retry queue is below 100 messages. Announce the deploy in #payments-eng. "
                    "Run the schema diff tool against production.")
    d.add_heading("Database failover procedure", 1)
    d.add_paragraph("If the primary PostgreSQL instance fails during deployment, promote the standby using "
                    "pg_ctl promote, then update the DNS CNAME db.orderflow.internal to point at the standby. "
                    "Application restart is NOT required; connections recover automatically.")
    d.add_heading("Rollback protocol", 1)
    d.add_paragraph("Redeploy the previous artifact tag. Database migrations are forward-only; use the "
                    "compensating migration script from the legacy reconciliation script folder if ledger "
                    "rows were written by the failed release.")
    d.core_properties.author = "Priya Sharma"
    # Backdate ~2.5 years to trigger the staleness finding.
    old = datetime.now(timezone.utc) - timedelta(days=int(2.5 * 365))
    d.core_properties.created = old - timedelta(days=30)
    d.core_properties.modified = old
    path = OUT / "deployment_runbook.docx"
    d.save(path)
    os.utime(path, (old.timestamp(), old.timestamp()))


def make_docx_dr():
    import docx

    d = docx.Document()
    d.add_heading("Disaster Recovery Plan — Payments Platform", 0)
    d.add_heading("Scope", 1)
    d.add_paragraph("Covers OrderFlow, the payment gateway integration, and settlement reporting.")
    d.add_heading("Database failover procedure", 1)
    d.add_paragraph("On primary PostgreSQL failure, page the DBA on-call. Do NOT promote the standby manually — "
                    "failover is handled automatically by Patroni. After failover, all application pods must be "
                    "restarted to pick up the new primary, or writes will fail silently.")
    d.add_heading("Settlement recovery", 1)
    d.add_paragraph("Re-run the legacy reconciliation script in recovery mode to rebuild settlement state. "
                    "Recovery mode flags are undocumented; contact the payments team.")
    d.core_properties.author = "Marcus Webb"
    now = datetime.now(timezone.utc)
    d.core_properties.created = now - timedelta(days=90)
    d.core_properties.modified = now - timedelta(days=20)
    d.save(OUT / "disaster_recovery_plan.docx")


def make_md_faq():
    (OUT / "payments_faq.md").write_text(
        "# Payments Team FAQ\n\n"
        "## Who owns OrderFlow?\n"
        "The payments team owns OrderFlow end to end, including the customer ledger and invoice worker.\n\n"
        "## What is the billing queue?\n"
        "A Kafka topic that decouples order validation from charging. The invoice worker is the only consumer.\n\n"
        "## The nightly reconciliation failed. What do I do?\n"
        "Re-run the legacy reconciliation script. If it fails twice, escalate — double-posted invoices are "
        "possible. Note the script's retry flags are folklore; there is no written doc for them.\n\n"
        "## How do refunds work?\n"
        "Refunds are issued through the gateway console manually. An automated refund service has been "
        "'planned' since 2023.\n"
    )


def main():
    make_pptx()
    make_docx_runbook()
    make_docx_dr()
    make_md_faq()
    print(f"Sample corpus written to {OUT.resolve()}")


if __name__ == "__main__":
    main()
