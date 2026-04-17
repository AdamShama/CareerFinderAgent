# Career Agent - Discord Edition

This agent checks selected company career pages and posts to Discord when it sees a new job.

## 1) Setup
- Install Python 3.10+
- Open this folder in Terminal / CMD
- Run:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## 2) Configure Discord
- Copy `config.example.json` to `config.json`
- Paste your Discord webhook URL into `config.json`

## 3) First run (save current jobs without sending alerts)
```bash
python main.py --seed --once
```

## 4) Start watching
```bash
python main.py --interval-minutes 15
```

## Notes
- Forcepoint and Intel use Workday, so the config uses Workday selectors.
- NVIDIA is set to wait for job links and then uses generic extraction.
- If a site changes its HTML, selectors may need a small update.
- Logs are written to `agent.log` and seen jobs are stored in `jobs.db`.

## Current companies included
- Forcepoint (Israel query)
- NVIDIA (Israel query)
- Intel (Israel query)
