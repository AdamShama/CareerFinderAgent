# 🚀 Career Agent — Real-Time Job Intelligence (Discord Edition)

Stop searching for jobs. Let them come to you.

Career Agent is an automation-first system that continuously monitors company career pages and delivers real-time alerts directly to Discord the moment new roles are published.

Designed for modern job seekers, this tool replaces manual browsing with a proactive pipeline that detects opportunities as soon as they go live.

---

## 🔥 Features

* ⚡ Real-time job detection across multiple company career pages
* 🔔 Instant Discord alerts via webhook integration
* 🧠 Smart deduplication using hashing + SQLite (no duplicate notifications)
* 🌐 Works with dynamic sites (Playwright browser automation)
* 🎯 Keyword filtering (e.g., internships, student positions )
* ⚙️ Fully configurable and easily extendable

---

## 🛠 Tech Stack

* Python
* Playwright (browser automation)
* SQLite (state management)
* Discord Webhooks

---

## ⚙️ Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
python -m playwright install chromium
```


### 2. Seed existing jobs (avoid spam)

```bash
python main.py --seed --once
```

---

### 3. Start monitoring

```bash
python main.py --interval-minutes 15
```

---

## 🧠 How It Works

The agent launches a headless browser using Playwright, loads each company’s careers page, and extracts job postings in real time.

Each job is fingerprinted and stored locally using SQLite. When a new, unseen job appears, the system instantly sends a formatted notification to Discord.

---

## 📡 Current Companies Tracked

* HP 
* NVIDIA
* Intel (Workday)
and you can add any company you like ;-)

---

## ⚠️ Notes

* Workday-based sites may require selector adjustments if their structure changes
* Logs are written to `agent.log`
* Seen jobs are stored in `jobs.db`
* Do NOT upload `config.json` (contains your webhook)

---

## 💡 Use Case

Built for students and engineers who want an unfair advantage in the job market by automating opportunity discovery.

---

## 🚀 Future Improvements

* Advanced filtering (data analyst / BI / software / anything )
* Ranked job relevance
* Telegram / email notifications
* Auto-apply workflows

---

> Built to give me an unfair advantge over others to successfully complete my job hunt.
