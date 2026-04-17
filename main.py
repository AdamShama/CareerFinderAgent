import argparse
import hashlib
import json
import logging
import os
import re
import smtplib
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "jobs.db"
DEFAULT_CONFIG_PATH = BASE_DIR / "config.json"
DEFAULT_LOG_PATH = BASE_DIR / "agent.log"


@dataclass
class JobPosting:
    company: str
    title: str
    url: str
    location: str = ""
    department: str = ""
    job_id: str = ""
    source_text: str = ""

    def fingerprint(self) -> str:
        raw = "||".join([
            self.company.strip().lower(),
            self.job_id.strip().lower(),
            self.title.strip().lower(),
            self.url.strip().lower(),
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class JobStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                fingerprint TEXT PRIMARY KEY,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                location TEXT,
                department TEXT,
                job_id TEXT,
                first_seen_utc TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def is_known(self, fingerprint: str) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM jobs WHERE fingerprint = ? LIMIT 1", (fingerprint,))
        return cur.fetchone() is not None

    def save(self, job: JobPosting) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO jobs (
                fingerprint, company, title, url, location, department, job_id, first_seen_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.fingerprint(),
                job.company,
                job.title,
                job.url,
                job.location,
                job.department,
                job.job_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class Notifier:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def notify(self, subject: str, body: str) -> None:
        notifications_cfg = self.config.get("notifications", {})
        sent_any = False

        discord_cfg = notifications_cfg.get("discord", {})
        if discord_cfg.get("enabled"):
            self._notify_discord(subject, body, discord_cfg)
            sent_any = True

        email_cfg = notifications_cfg.get("email", {})
        if email_cfg.get("enabled"):
            self._notify_email(subject, body, email_cfg)
            sent_any = True

        if not sent_any:
            logging.info("Notifications disabled.\n%s", body)

    def _notify_email(self, subject: str, body: str, email_cfg: dict[str, Any]) -> None:
        required = ["smtp_host", "smtp_port", "username", "password", "from_email", "to_email"]
        missing = [key for key in required if not email_cfg.get(key)]
        if missing:
            raise ValueError(f"Missing email notification settings: {', '.join(missing)}")

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = email_cfg["from_email"]
        message["To"] = email_cfg["to_email"]
        message.set_content(body)

        smtp_host = email_cfg["smtp_host"]
        smtp_port = int(email_cfg["smtp_port"])

        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
            if email_cfg.get("use_starttls", True):
                smtp.starttls()
            smtp.login(email_cfg["username"], email_cfg["password"])
            smtp.send_message(message)

    def _notify_discord(self, subject: str, body: str, discord_cfg: dict[str, Any]) -> None:
        webhook_url = (discord_cfg.get("webhook_url") or "").strip()
        if not webhook_url:
            raise ValueError("Missing Discord webhook_url in notifications.discord")

        username = (discord_cfg.get("username") or "Career Agent").strip()
        mention = (discord_cfg.get("mention") or "").strip()

        content = f"**{subject}**\n{body}"
        if mention:
            content = f"{mention}\n{content}"

        max_length = 1900
        chunks = [content[i:i + max_length] for i in range(0, len(content), max_length)] or [content]

        for chunk in chunks:
            payload = json.dumps({
                "username": username,
                "content": chunk
            }).encode("utf-8")

            request = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "CareerAgent/1.0"
                },
                method="POST",
            )

            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    if response.status not in (200, 204):
                        raise RuntimeError(f"Discord webhook returned status {response.status}")
            except urllib.error.HTTPError as exc:
                details = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Discord webhook failed: HTTP {exc.code} - {details}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Discord webhook request failed: {exc}") from exc


class CareerPageScraper:
    def __init__(self, headless: bool = True, timeout_ms: int = 45000) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms

    def scrape_company(self, page, company_cfg: dict[str, Any]) -> list[JobPosting]:
        name = company_cfg["name"]
        url = company_cfg["url"]
        selectors = company_cfg.get("selectors", {})
        wait_for = selectors.get("wait_for") or "body"
        listing_selector = selectors.get("listing")
        title_selector = selectors.get("title")
        link_selector = selectors.get("link")
        location_selector = selectors.get("location")
        department_selector = selectors.get("department")
        job_id_attr = selectors.get("job_id_attr")

        logging.info("Opening %s (%s)", name, url)
        page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        page.wait_for_selector(wait_for, timeout=self.timeout_ms)
        page.wait_for_timeout(int(company_cfg.get("extra_wait_ms", 1500)))

        if company_cfg.get("click_accept_cookies"):
            self._try_click(page, company_cfg["click_accept_cookies"])
            page.wait_for_timeout(500)

        if company_cfg.get("load_more_selector"):
            self._expand_all(page, company_cfg["load_more_selector"])

        jobs: list[JobPosting] = []

        if listing_selector and title_selector:
            cards = page.locator(listing_selector)
            count = cards.count()
            logging.info("Found %s listing nodes for %s", count, name)
            for idx in range(count):
                card = cards.nth(idx)
                title = self._safe_inner_text(card, title_selector)
                link = self._safe_href(card, link_selector) if link_selector else self._safe_href(card, "a")
                location = self._safe_inner_text(card, location_selector) if location_selector else ""
                department = self._safe_inner_text(card, department_selector) if department_selector else ""
                job_id = self._safe_attr(card, job_id_attr) if job_id_attr else ""
                if not title:
                    continue
                full_url = urljoin(url, link) if link else url
                source_text = " | ".join(x for x in [title, location, department] if x)
                jobs.append(JobPosting(name, title, full_url, location, department, job_id, source_text))
        else:
            jobs = self._generic_extract(page, name, url)

        jobs = self._dedupe_jobs(jobs)
        return self._apply_filters(jobs, company_cfg)

    def _try_click(self, page, selector: str) -> None:
        try:
            button = page.locator(selector).first
            if button.count() > 0:
                button.click(timeout=3000)
        except Exception:
            logging.debug("Optional click failed for selector: %s", selector)

    def _expand_all(self, page, selector: str, max_clicks: int = 25) -> None:
        for _ in range(max_clicks):
            try:
                button = page.locator(selector).first
                if button.count() == 0:
                    break
                button.click(timeout=3000)
                page.wait_for_timeout(1000)
            except Exception:
                break

    def _safe_inner_text(self, locator, selector: str | None) -> str:
        if not selector:
            return ""
        try:
            target = locator.locator(selector).first
            if target.count() == 0:
                return ""
            return (target.inner_text(timeout=2000) or "").strip()
        except Exception:
            return ""

    def _safe_attr(self, locator, selector_or_attr: str | None) -> str:
        if not selector_or_attr:
            return ""
        if selector_or_attr.startswith("@"):
            attr_name = selector_or_attr[1:]
            try:
                value = locator.get_attribute(attr_name, timeout=2000)
                return (value or "").strip()
            except Exception:
                return ""
        return ""

    def _safe_href(self, locator, selector: str) -> str:
        try:
            target = locator.locator(selector).first
            if target.count() == 0:
                return ""
            href = target.get_attribute("href", timeout=2000)
            return (href or "").strip()
        except Exception:
            return ""

    def _generic_extract(self, page, company: str, base_url: str) -> list[JobPosting]:
        anchors = page.locator("a")
        count = anchors.count()
        jobs: list[JobPosting] = []
        for idx in range(count):
            anchor = anchors.nth(idx)
            try:
                text = (anchor.inner_text(timeout=1000) or "").strip()
                href = (anchor.get_attribute("href", timeout=1000) or "").strip()
            except Exception:
                continue
            if not text or not href:
                continue
            if len(text) < 4 or len(text) > 180:
                continue
            haystack = f"{text} {href}".lower()
            if not any(token in haystack for token in ["job", "career", "position", "opening", "vacancy", "apply"]):
                continue
            if any(token in href.lower() for token in ["mailto:", "tel:", "javascript:", "linkedin.com", "facebook.com"]):
                continue
            jobs.append(JobPosting(company, text, urljoin(base_url, href), source_text=text))
        return self._dedupe_jobs(jobs)

    def _dedupe_jobs(self, jobs: Iterable[JobPosting]) -> list[JobPosting]:
        unique: dict[str, JobPosting] = {}
        for job in jobs:
            fp = job.fingerprint()
            unique[fp] = job
        return list(unique.values())

    def _apply_filters(self, jobs: list[JobPosting], company_cfg: dict[str, Any]) -> list[JobPosting]:
        keywords = [k.strip().lower() for k in company_cfg.get("keywords", []) if k.strip()]
        exclude = [k.strip().lower() for k in company_cfg.get("exclude_keywords", []) if k.strip()]

        filtered: list[JobPosting] = []
        for job in jobs:
            haystack = " ".join([
                job.title.lower(),
                job.location.lower(),
                job.department.lower(),
                job.url.lower(),
                job.source_text.lower(),
            ])
            if keywords and not any(k in haystack for k in keywords):
                continue
            if exclude and any(k in haystack for k in exclude):
                continue
            filtered.append(job)
        return filtered

    def run(self, config: dict[str, Any]) -> list[JobPosting]:
        all_jobs: list[JobPosting] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)
            for company_cfg in config.get("companies", []):
                try:
                    company_jobs = self.scrape_company(page, company_cfg)
                    logging.info("%s -> %s matched jobs", company_cfg['name'], len(company_jobs))
                    all_jobs.extend(company_jobs)
                except PlaywrightTimeoutError:
                    logging.exception("Timeout while scraping %s", company_cfg["name"])
                except Exception:
                    logging.exception("Failed while scraping %s", company_cfg["name"])
            context.close()
            browser.close()
        return all_jobs


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. Copy config.example.json to config.json and fill it in."
        )
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def format_jobs(jobs: list[JobPosting]) -> str:
    lines: list[str] = []
    for idx, job in enumerate(jobs, start=1):
        lines.append(f"{idx}. {job.company} | {job.title}")
        if job.location:
            lines.append(f"   Location: {job.location}")
        if job.department:
            lines.append(f"   Department: {job.department}")
        lines.append(f"   URL: {job.url}")
    return "\n".join(lines)


def seed_existing(store: JobStore, jobs: list[JobPosting]) -> int:
    count = 0
    for job in jobs:
        if not store.is_known(job.fingerprint()):
            store.save(job)
            count += 1
    return count


def process_jobs(config: dict[str, Any], store: JobStore, jobs: list[JobPosting], notifier: Notifier) -> int:
    new_jobs: list[JobPosting] = []
    for job in jobs:
        fp = job.fingerprint()
        if store.is_known(fp):
            continue
        store.save(job)
        new_jobs.append(job)

    if new_jobs:
        subject = f"[Career Agent] {len(new_jobs)} new job(s) found"
        body = (
            f"Detected {len(new_jobs)} new job posting(s) at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.\n\n"
            + format_jobs(new_jobs)
        )
        notifier.notify(subject, body)
        logging.info("Notification sent for %s new jobs", len(new_jobs))
    else:
        logging.info("No new jobs found.")

    return len(new_jobs)


def configure_logging(log_path: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Career page watcher agent")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.json")
    parser.add_argument("--seed", action="store_true", help="Save currently visible jobs without notifying")
    parser.add_argument("--once", action="store_true", help="Run one time and exit")
    parser.add_argument("--interval-minutes", type=int, default=30, help="Polling interval for loop mode")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    args = parser.parse_args()

    configure_logging(DEFAULT_LOG_PATH)
    config = load_config(Path(args.config))
    store = JobStore(DB_PATH)
    notifier = Notifier(config)
    scraper = CareerPageScraper(headless=not args.headed)

    try:
        if args.once or args.seed:
            jobs = scraper.run(config)
            if args.seed:
                inserted = seed_existing(store, jobs)
                logging.info("Seed complete. Stored %s job(s).", inserted)
                print(f"Seeded {inserted} job(s).")
                return 0
            count = process_jobs(config, store, jobs, notifier)
            print(f"Found {count} new job(s).")
            return 0

        while True:
            jobs = scraper.run(config)
            process_jobs(config, store, jobs, notifier)
            sleep_seconds = max(args.interval_minutes, 1) * 60
            logging.info("Sleeping for %s seconds", sleep_seconds)
            time.sleep(sleep_seconds)
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
