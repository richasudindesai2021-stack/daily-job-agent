# Daily Job Agent

An agent that finds 20 fresh, sponsorship-friendly roles every morning (a mix of big, mid-size and small companies) and writes a tailored one-page resume PDF for each. You just apply.

Free to run: on your laptop or on free hosting. Bring your own AI key: Gemini or Groq (free tiers), Claude, OpenAI, OpenRouter, or any OpenAI-compatible API.

## How it works

```
Your targets ──► 1. Fetch   Public Greenhouse / Lever / Ashby job feeds for your company list
                 2. Filter  Rules first: title, location, posted < 24h, salary, industry,
                            and "no sponsorship / citizens only / clearance" wording
                 3. Check   Optional USCIS H-1B employer history
                 4. Score   AI rates fit 0-100 against your resume, says why and what to lead with
                 5. Pick    Balanced daily list (e.g. 7 big / 7 mid / 6 small), one role per company
                 6. Tailor  AI rephrases and reorders your bullets toward the posting's keywords
                 7. Guard   Any bullet with a number that isn't in your original is reverted
                 8. Output  One-page PDF per role + tracker
You ◄─────────── 9. Apply   Mark applied / skipped / not interested; the next run learns from it
```

Design choices worth noting:
- **Rules before AI.** Cheap, predictable filters run first, so only about 60 shortlisted postings reach the model. That keeps the free quota safe.
- **Honesty guardrail.** The model may only rephrase. Code checks every rewritten bullet and reverts any that add numbers. Skills can be reordered but never added.
- **Human in the loop.** Nothing gets submitted for you. You review each role and resume and apply yourself.
- **Swappable model.** Any OpenAI-compatible provider works (Gemini, Groq).

## Setup (about 10 minutes)

1. Install Python 3.10+ (python.org).
2. Open a terminal in this folder and run:
   ```
   pip install -r requirements.txt
   streamlit run app.py
   ```
   Your browser opens the app.
3. Get a free key at https://aistudio.google.com/apikey, paste it in the sidebar, and click **Remember key on this laptop**.
4. **My targets** tab: set roles, locations, work authorization, industries and the size mix, then click **Save targets**.
5. Upload your resume PDF, click **Read my resume with AI**, fix anything it misread, and click **Save resume**.
6. Click **Check which companies work**. Fix or delete any ❌ rows and add companies you like. To find a company's token, open its careers page: if job links look like `boards.greenhouse.io/NAME`, `jobs.lever.co/NAME` or `jobs.ashbyhq.com/NAME`, NAME is the token.
7. Optional: download employer data from the USCIS H-1B Employer Data Hub and upload the CSV.

## Company lists: one master list, a personal list for each person

**Once (whoever runs the app):** build the master list from the U.S. Department of Labor's public H-1B filings.
Download the 4 latest quarterly LCA disclosure files (.xlsx) from https://www.dol.gov/agencies/eta/foreign-labor/performance
(section "LCA Programs (H-1B, H-1B1, E-3)"), put them in a folder called `lca_files`, and run:

    python build_master.py lca_files

Or use **Setup → Your company list → Build the master list** in the app. It ranks every employer that sponsored entry-level
roles, records which kinds of roles they hire for (product, analyst, data, consulting, project, finance, marketing, engineering),
finds which have public job pages, and writes `data/companies_master.csv`. Repeat when DOL publishes new data each quarter.

**Each person:** in **Setup → Your company list**, one click builds their own list (up to 3,000) from the master list:
- **Needs sponsorship** (F-1 OPT, STEM OPT, CPT, H-1B transfer): only companies that sponsored entry-level hires.
- **U.S. citizen or green card:** every company.
- **Industries** they chose in Step 4 filter it, and the **roles** they want decide the ranking.
- The size mix follows their daily mix. They can add any company by name, and the app finds its job page.

## Every morning

Run `streamlit run app.py`, open **Today's 20**, click **Find today's 20**, then work down the list: open the posting, download the tailored PDF, apply, and mark it **applied**.

PDFs are saved in `output/<date>/`.

## Resume chat

The **Resume chat** tab lets you edit your base resume or any tailored one by chatting with Gemini. Examples: "make this more product-focused," "shorten every bullet," "what's missing for this posting?"

The AI proposes changes and you click **Apply** or **Discard**. Applying rebuilds the PDF. If the AI adds a number you never gave it, that bullet keeps its original wording. Numbers you type in the chat yourself are allowed.

## Instant alerts (see new jobs within minutes)

Job boards don't announce new postings, so the watcher checks your companies every 10 minutes and pushes each new match to your phone.

1. Install the free **ntfy** app (iOS or Android) and subscribe to a hard-to-guess topic, e.g. `richa-jobs-7f3k9q`.
2. Enter the same topic in **My targets → Instant alerts**, click **Send test alert**, then click **Save targets**.
3. In a second terminal window, run `python watch.py` (or `python watch.py --every 5`) and leave it open.
   The first check only records what's already posted. After that, every new matching role pings your phone with a tap-to-open link.

It runs while your laptop is awake. To keep checking when the laptop is off, you can use the included GitHub Actions schedule (`.github/workflows/watch.yml`, every 15 minutes, free on public repos).
Commit `data/settings.json` and `data/companies.csv` so it knows your targets, and add your topic as the `NTFY_TOPIC` repo secret. GitHub sometimes starts scheduled runs late, so the laptop watcher is faster.

## Daily email with your 30 roles

1. Turn on 2-Step Verification for your Google account, then create an **app password** (Google Account → Security → App passwords).
2. In **My targets → Daily email**, enter your Gmail address and the app password, tick **Email me my daily list**, pick the time and time zone you want, and click **Save email settings**. Then click **Send me a test email**.
3. Keep the watcher running (`python watch.py` or start_agent.bat). At your chosen time it builds the day's list, tailors the resumes, and emails you the 30 roles with apply links and the PDFs attached. If the laptop was asleep at that time, it sends as soon as it wakes up.

To send one right now: `python watch.py --daily-now`, or the **📧 Email me this list** button in Today's list.

## Claude polish (optional, free)

After a run, open **Today's 20 → Claude polish** and download the handoff file. It contains the prompt (see `CLAUDE_PROMPT.md`) plus every posting and its tailored resume. Drop it into a Claude chat and upload or paste Claude's JSON reply back into the app. The PDFs are rebuilt with the same honesty check.

## Hosting it for other people (free, Streamlit Community Cloud)

Before you deploy, build the shared company list once on your laptop (Steps A and B in My targets), then upload the resulting `data/companies.csv` with the code. Every visitor starts from that list, so nobody has to process the large DOL files themselves. Your own resume, project bank, key, settings and tracker are excluded by `.gitignore`. Leave them out when you upload.

Visitors get a private workspace: they paste their own key, upload their resume, build their project bank with AI, and run their daily list. The phone-alert watcher runs only on someone's own laptop, not on the hosted site.


1. Upload this folder's contents to a new GitHub repo. Never upload `.streamlit/secrets.toml`, `data/resume.json` or `output/`.
2. Go to share.streamlit.io, sign in with GitHub, and click **Create app**. Pick the repo, branch `main` and file `app.py`.
3. Under **Advanced settings → Secrets**, add exactly `HOSTED_MODE = "true"`. Don't put your own API key there.
4. Click **Deploy** and share the `*.streamlit.app` link.

In hosted mode, every visitor gets a private workspace, uses their own free Gemini key, and can download and re-upload their profile, since the server clears its files when it restarts.

## Privacy

- **On your laptop:** everything stays on your computer: resume, tracker, PDFs, keys and Gmail app password (in `.streamlit/secrets.toml`). The app refuses connections from any other computer, even on the same Wi-Fi. `.gitignore` keeps these files out of GitHub.
- **Hosted:** each visitor gets a private workspace tied to their browser session, identified by a random ID that can't be guessed. Keys are kept only in that session and never saved. No one can see another person's resume or list. Workspaces are temporary and wiped when the server restarts, so people download their profile to keep it. Email and alerts are turned off in hosted mode, so no Gmail passwords ever go to the server.
- **Either way:** your resume text goes to the AI provider you chose, under that provider's terms.

## Limits

- It only sees companies on your list that use Greenhouse, Lever or Ashby. Many large companies (Google, Amazon, Apple) use their own systems and aren't covered.
- Sponsorship detection reads the posting's wording. "Unknown" means the posting didn't say either way.
- Free-tier limits change. If you hit a rate limit, wait a minute or switch to Groq in the sidebar.
- Free-tier prompts may be used by the provider to improve their models. Keep personal details (phone, address) out of the resume you upload if that matters to you.
- Always read the tailored PDF before sending it.
