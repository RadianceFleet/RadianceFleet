# Getting Started with RadianceFleet

**For journalists, OSINT researchers, and NGO analysts — no technical background required.**

---

## What RadianceFleet Does

Ships broadcast their position via AIS (Automatic Identification System) — think of it as GPS tracking that everyone can see. When a ship turns off this broadcast, it "goes dark." RadianceFleet watches for these dark periods on shipping routes used by Russian oil tankers and scores each one based on how suspicious it looks.

Beyond tracking dark periods, the tool also detects ships that broadcast false positions (spoofing), two ships meeting at sea to transfer cargo without port records (ship-to-ship transfers), and vessels whose names appear on sanctions lists. Each suspicious event gets a risk score from 0 to 100+ that helps you prioritize which ships deserve deeper investigation.

RadianceFleet does not prove anything. It triages. The judgment is yours.

---

## Who Is This For

- **Journalists** investigating Russian oil sanctions evasion who want to identify vessels worth deeper research
- **OSINT researchers** tracking shadow fleet movements using public data sources
- **NGO analysts** at organizations monitoring sanctions compliance or maritime human rights
- **Maritime policy researchers** who need a repeatable, documented methodology for fleet-wide pattern analysis

You do not need to know how to code. You will need to run a small number of commands in a terminal window, and this guide explains each one step by step.

---

## What You Will Need

| Requirement | Details |
|---|---|
| Computer | Windows 10/11, macOS 12+, or Linux (Ubuntu 20.04+) |
| Time | About 30 minutes for the initial setup |
| Disk space | About 2 GB (mostly for the database software) |
| Internet | Required for the initial setup and for downloading sanctions data |

---

## Glossary

These terms appear throughout this guide and throughout the RadianceFleet interface. All are defined here in plain language.

| Term | What it means |
|---|---|
| **AIS** | Automatic Identification System. A radio broadcast that ships use to announce their position, speed, and identity. Required by international law for large vessels. |
| **MMSI** | Maritime Mobile Service Identity. The unique 9-digit number that identifies a ship's AIS transponder — like a phone number for a ship's radio. |
| **IMO** | International Maritime Organization number. A permanent 7-digit identifier assigned to a ship at construction. Unlike the MMSI, the IMO number does not change when a ship is sold or reflagged. |
| **STS** | Ship-to-ship transfer. When one vessel pulls alongside another at sea to move cargo without docking at a port. Legitimate uses exist, but STS transfers are also a documented technique for disguising the origin of Russian crude oil. |
| **SOG** | Speed over ground. How fast a ship is actually moving across the sea surface, measured in knots. |
| **COG** | Course over ground. The direction a ship is actually traveling, measured in degrees (0° = north, 90° = east, 180° = south, 270° = west). |
| **Flag state** | The country whose flag a ship flies, which determines which laws apply to the vessel. Shadow fleet ships frequently re-register under the flags of countries with weak enforcement ("flags of convenience"), such as Palau, Gabon, or Cameroon. |
| **DWT** | Deadweight tonnage. A measure of a ship's carrying capacity — how many tonnes of cargo, fuel, and stores it can carry. Large oil tankers typically exceed 80,000 DWT (VLCC class). |
| **Corridor** | A geographic area that RadianceFleet monitors, defined as a polygon on the map. Corridors represent known export routes, STS zones, or GPS jamming areas relevant to Russian oil trade. |
| **Dark zone** | A corridor marked as a known GPS jamming area, where AIS gaps are less suspicious because jamming causes involuntary signal loss. The Baltic Approaches and parts of the Black Sea are examples. |
| **Risk score** | A number from 0 to 100+ that RadianceFleet assigns to each suspicious event. Higher means more suspicious. See [Understanding the Risk Score](#-understanding-the-risk-score) below. |
| **Evidence card** | A structured report RadianceFleet exports when an analyst has reviewed an alert. Contains the event details, score breakdown, and a mandatory disclaimer. |

---

## Choose Your Path

Select the description that fits what you want to do.

### Path A — "I want to investigate vessels" (Analyst)

You want to load data, review alerts, look at maps, and export evidence cards for your investigation. You are not a developer and do not plan to modify the code.

**Continue reading this guide from [Path A: Step-by-Step Setup](#path-a-step-by-step-setup) below.**

---

### Path B — "I want to develop or contribute" (Developer)

You want to run tests, modify detection logic, add new data sources, or contribute code back to the project.

**See [quickstart.md](quickstart.md) for the developer-oriented setup.**

---

### Path C — "I want to use the API from scripts" (Integrator)

You want to query RadianceFleet programmatically — from Python scripts, notebooks, or other tools — rather than using the web interface.

**See [API_INTEGRATION.md](API_INTEGRATION.md) for the REST API reference and integration examples.**

---

## Path A: Step-by-Step Setup

Follow these steps in order. Each step tells you what you are about to do before asking you to do it.

---

### Step 1: Install Docker Desktop

Docker is software that runs a database on your computer without a complex installation. You do not need to understand how it works — you just need it running in the background.

**macOS** (using Homebrew — if you have it):

```bash
brew install --cask docker
```

Then open Docker Desktop from your Applications folder and wait for the whale icon in your menu bar to stop animating.

**macOS** (without Homebrew): Download the installer from https://www.docker.com/products/docker-desktop/ and run it.

**Windows**: Download Docker Desktop from https://www.docker.com/products/docker-desktop/ and run the installer. When prompted, choose the WSL 2 backend. After installation, restart your computer.

**Linux (Ubuntu/Debian)**:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin
sudo systemctl start docker
sudo usermod -aG docker $USER
```

After running the last command, log out and log back in so the group membership takes effect.

**Verify Docker is working** — open a terminal and run this command:

```bash
docker --version
```

You should see output like:

```
Docker version 26.1.4, build 5650f9b
```

Any recent version number is fine. If you see "command not found", Docker is not installed correctly — refer to the Docker documentation at https://docs.docker.com/desktop/.

---

### Step 2: Clone the Repository

"Cloning" downloads the RadianceFleet code to your computer. Open a terminal and run:

```bash
git clone https://github.com/radiancefleet/radiancefleet.git
cd radiancefleet
```

If you do not have `git` installed:
- **macOS**: Run `xcode-select --install` in your terminal
- **Windows**: Download Git from https://git-scm.com/download/win
- **Linux**: Run `sudo apt-get install git`

After cloning, your terminal should be inside the `radiancefleet` folder. Verify this by running:

```bash
pwd
```

The output should end with `/radiancefleet`.

---

### Step 3: Start the Database

RadianceFleet uses PostgreSQL with PostGIS (geographic data extensions) as its database. Docker will download and start it for you automatically.

Run this command from the `radiancefleet` folder:

```bash
docker compose up -d
```

The first time you run this, Docker downloads the database image — this takes 1-2 minutes depending on your internet speed. You will see output like:

```
[+] Pulling postgres (postgis/postgis:16-3.4)...
[+] Running 2/2
 ✔ Network radiancefleet_default  Created
 ✔ Container radiancefleet_db     Started
```

Now verify that the database is healthy and ready to accept connections:

```bash
docker compose ps
```

You should see:

```
NAME                SERVICE    STATUS          PORTS
radiancefleet_db    postgres   running (healthy)   0.0.0.0:5432->5432/tcp
```

Wait until the STATUS column shows `running (healthy)`. If it shows `starting`, wait 30 seconds and run the command again. If it shows `unhealthy`, see [Troubleshooting](#common-questions-and-troubleshooting) at the bottom of this guide.

---

### Step 4: Install Python 3.12+ and uv

RadianceFleet is a Python application. You need Python 3.12 or newer and a tool called `uv` that manages its dependencies.

**Check if you already have Python 3.12+:**

```bash
python3 --version
```

If the output shows `Python 3.12.x` or higher, skip the Python installation step.

**Install Python 3.12 if needed:**
- **macOS**: `brew install python@3.12` or download from https://www.python.org/downloads/
- **Windows**: Download from https://www.python.org/downloads/windows/ — check "Add python.exe to PATH" during installation
- **Linux**: `sudo apt-get install python3.12 python3.12-venv`

**Install uv** — this is the package manager RadianceFleet uses:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

After installation, close and reopen your terminal, then verify:

```bash
uv --version
```

You should see output like:

```
uv 0.5.21 (some-hash)
```

---

### Step 5: Install RadianceFleet's Python Dependencies

Move into the backend folder and let `uv` install everything RadianceFleet needs:

```bash
cd backend
uv sync
```

This downloads all the Python packages RadianceFleet uses. You will see a progress indicator. When it finishes:

```
Resolved 87 packages in 0.43s
Installed 87 packages in 3.21s
```

Now activate the virtual environment so your terminal uses the installed packages:

**macOS / Linux:**

```bash
source .venv/bin/activate
```

**Windows (Command Prompt):**

```
.venv\Scripts\activate.bat
```

**Windows (PowerShell):**

```
.venv\Scripts\Activate.ps1
```

After activation, your terminal prompt changes to show `(.venv)` at the beginning:

```
(.venv) you@computer:~/radiancefleet/backend$
```

---

### Step 6: Initialize and Load Sample Data

This single command sets up the database tables, imports the 11 monitoring corridors (shipping routes and STS zones), and loads 7 synthetic vessels that demonstrate every detection type:

```bash
radiancefleet setup --with-sample-data
```

You will see output like:

```
[setup] Initializing database schema...
[setup] Created 18 tables.
[setup] Importing corridors from ../config/corridors.yaml...
[setup] Imported 11 corridors (4 export routes, 5 STS zones, 2 dark zones).
[setup] Generating sample vessels...
[setup] Generated 7 vessels with 129 AIS position records.
[setup] Running detection pipeline...
[setup] Detected 6 AIS gap events.
[setup] Detected 2 spoofing patterns.
[setup] Detected 1 STS event.
[setup] Scored all alerts.
[setup] Done. Open the web interface to begin reviewing alerts.
```

The sample data is explained in [Understanding the Sample Data](#understanding-the-sample-data) below.

---

### Step 7: Start the Web Interface

Run the server:

```bash
radiancefleet serve
```

You will see:

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

Open your browser and go to: **http://localhost:8000**

The RadianceFleet web interface will load.

To stop the server later, press `Ctrl+C` in the terminal.

---

### Step 8: Explore the Web Interface

Once the interface loads, you will see four main areas:

**Alert Queue (left panel)**
This is a list of every suspicious event RadianceFleet has detected. Each row shows the vessel identifier, the type of anomaly, the risk score, and the current review status. You can sort by score (highest first) and filter by status (`new`, `under_review`, `confirmed`, `dismissed`).

**Map View (center)**
When you click an alert, the map shows:
- A green dot at the vessel's last known position before going dark
- A red dot at the vessel's first position after reappearing
- A blue polygon showing the area the vessel could plausibly have reached during the gap (the "movement envelope")
- The monitoring corridor overlaid as a colored zone

**Vessel Detail (right panel)**
This panel shows everything RadianceFleet knows about the vessel — its MMSI, IMO number, flag state, vessel type, deadweight tonnage, and its full position history.

**Score Breakdown**
Below the vessel detail, every signal that contributed to the risk score is listed individually, with its weight and a plain-language description. This is what you use to explain the score in your notes.

---

## Understanding the Sample Data

The 7 synthetic vessels loaded by `--with-sample-data` are teaching tools. They are not real ships. They cover every detection type so you can understand what each anomaly looks like before working with real data.

| Vessel | What It Demonstrates |
|---|---|
| **A** | A 26-hour silence on the Baltic shipping route — the core gap detection scenario. Scores high because of the long duration, corridor location, and vessel size. |
| **B** | Fake position data. The vessel's AIS broadcast a circular loop of positions that is geometrically impossible for a real ship — a pattern called "circle spoofing." Often used to falsely show a vessel anchored while it is actually transiting. |
| **C** | Two ships meeting at sea. Vessel C approached another vessel, slowed to near-zero speed, maintained close proximity for several hours, then departed on a new course — the pattern of a ship-to-ship transfer. |
| **D** | A ship whose MMSI matches a vessel on the US Office of Foreign Assets Control (OFAC) sanctions list. The watchlist match alone adds significant risk score points. |
| **E** | A brand-new ship identifier with no history in the database. New MMSIs with no prior voyage record are a flag — it may indicate a vessel that recently changed its identity. |
| **F** | A clean vessel with no anomalies. Use this as a comparison baseline. Its score should be near zero, which shows the tool is not generating false positives across the board. |
| **G** | A ship that "teleported." After a dark period, the vessel reappeared at a location too far away to have reached at any plausible speed — indicating either a manipulated position report or a vessel that moved much faster than its reported class allows. |

---

## Loading Real Data

Once you are comfortable with the sample data, you can load real vessel and sanctions data.

### Step 1: Download Sanctions Lists

This command automatically downloads the latest OFAC SDN (US Treasury sanctions) list and OpenSanctions vessel data:

```bash
radiancefleet data fetch
```

You will see download progress for each source. The files are saved into the `data/` folder.

### Step 2: Get AIS Position Data

RadianceFleet needs AIS position records to detect anomalies. Two free sources cover different needs:

**For historical Baltic Sea research (2006–2016):**
The Danish Maritime Authority (DMA) publishes a free archive of AIS data from the Baltic Sea. Download the files you need from https://www.dma.dk/safety-at-sea/navigational-information/ais-data and save them to the `data/` folder. Files are organized by month. The data comes as CSV files with one position record per row.

**For recent and near-real-time data:**
[aisstream.io](https://aisstream.io) provides a free WebSocket feed of live AIS data and allows you to export time-windowed batches for specific regions. Create a free account, configure a geographic bounding box around the area you are investigating, and export the data as CSV. Note that aisstream.io does not maintain a historical archive — you can only capture data going forward from when you start.

**For professional investigations in high-priority regions:**
Free sources do not provide reliable coverage of the Black Sea or Persian Gulf — the two regions where AIS data is most actively manipulated. For those regions, commercial providers such as Spire Global, exactEarth, and S&P Global Maritime are the standard. See [coverage-limitations.md](coverage-limitations.md) for a full breakdown by region.

### Step 3: Import and Run Detection

Once you have AIS data files in the `data/` folder, this single command imports the positions, runs all detectors, and scores every alert:

```bash
radiancefleet data refresh
```

This is equivalent to running the ingestion, gap detection, spoofing detection, loitering detection, STS detection, corridor correlation, and scoring steps individually. For large datasets, expect it to take several minutes.

---

## The Analyst Workflow

This is the day-to-day process for investigating alerts.

### 1. Check the Alert Queue

Open the web interface and look at the alert queue sorted by risk score, highest first. Focus on alerts in `new` status. A score above 50 is worth a closer look; above 75 warrants a serious review.

### 2. Open the Highest-Scored Alert

Click the alert to open its detail view. The first thing to read is the **score breakdown** — the list of individual signals that fired. This tells you why the score is high and which signals are strongest. Two alerts with the same score can have very different stories: one might be driven by a long gap in a high-risk corridor; another might be a short gap combined with a sanctions watchlist match.

### 3. Look at the Map

Examine the movement envelope (the blue polygon). Does the vessel's reappearance position fall inside the envelope? If it reappears outside the envelope — farther than any plausible speed could have taken it — that is a strong indicator of a manipulated position report. If it reappears inside the envelope, the gap may still be significant, but the position data is at least internally consistent.

### 4. Cross-Reference with Satellite Imagery

For high-scoring alerts, generate a satellite check package. This command builds a pre-filled search URL for the Copernicus Browser (the European Space Agency's free satellite archive) using the exact time window and location of the gap:

```bash
radiancefleet satellite prepare --alert <alert-id>
```

Replace `<alert-id>` with the number shown in the alert queue. The output includes a direct link to Sentinel-1 SAR imagery for the gap window. SAR imagery works regardless of cloud cover and can detect vessels as bright dots on the water surface. It cannot identify vessels by name, but it can confirm whether a ship was present at its last known position during the gap.

### 5. Update the Status and Add Notes

Before you can export an evidence card, you must change the alert status from `new` to `under_review`. This is a deliberate gate — it prevents raw automated output from being mistaken for verified findings.

In the alert detail panel:
1. Click **Set Status** and choose `under_review`
2. Type your analyst notes in the notes field — what you checked, what you found, what you are uncertain about

Your notes become part of the permanent record for this alert.

### 6. Export the Evidence Card

Once the alert is in `under_review` or higher status, you can export a structured evidence card for your records or for sharing with editors and legal reviewers:

```bash
radiancefleet export evidence --alert <alert-id> --format md
```

For a file you can open in Word or share via email:

```bash
radiancefleet export evidence --alert <alert-id> --format md --output evidence_alert_42.md
```

Every evidence card includes a mandatory disclaimer stating that this is investigative triage, not a legal determination. Do not remove this disclaimer in any downstream use.

### 7. Read the Overclaiming Guide Before Publishing

Before citing any RadianceFleet finding in a published article, briefing, or report, read [avoiding-overclaiming.md](avoiding-overclaiming.md). It explains in precise terms what the tool can and cannot show, how to interpret scores, and which verification steps are required before making any claims about sanctions violations or criminal activity.

---

## Understanding the Risk Score

Every alert has a risk score from 0 to approximately 100 (some signals can push it higher).

| Band | Score | What it means | Recommended action |
|---|---|---|---|
| **Low** | 0-20 | Normal operational pattern. Likely a routine transmission gap or coverage area. | No action needed. Note for baseline. |
| **Medium** | 21-50 | One or more signals worth a closer look. Could be an equipment issue, a coverage gap, or an early indicator. | Check satellite imagery. Review vessel history. Do not publish without further verification. |
| **High** | 51-75 | Multiple corroborating signals consistent with documented shadow fleet behavior. | Publication-ready only with analyst review, satellite cross-check, and independent vessel research. |
| **Critical** | 76+ | Strong cluster of shadow fleet indicators: long gap, spoofing detected, watchlist match, STS activity, high-risk corridor. | Escalate. Verify with satellite imagery. Consult a maritime law expert before making legal claims. |

**Important:** A score is an ordinal ranking, not a probability. A score of 75 does not mean "75% chance of sanctions evasion." It means "this event scores higher than most events across the signals we track." Two alerts with identical scores can have very different risk profiles. Always read the score breakdown.

---

## Common Questions and Troubleshooting

**What does a score of 75 mean?**

It means this gap event scores in the high-confidence anomaly band — it has multiple corroborating signals that are collectively unusual. It does not mean the vessel was conducting sanctioned activity. Read the score breakdown to understand which signals fired and with what weights, then verify independently before drawing conclusions.

**Can I use RadianceFleet output as legal evidence?**

No. RadianceFleet is a triage tool for investigative research. Its output is not admissible evidence of sanctions violations or criminal conduct. It identifies patterns that warrant further investigation by qualified experts. The mandatory disclaimer on every evidence card reflects this. Consult a maritime law expert before making any legal claim.

**How do I get more recent AIS data beyond what's freely available?**

For current data, [aisstream.io](https://aisstream.io) provides free near-real-time feeds you can capture going forward. For historical data in the Baltic beyond 2016, or for coverage in the Black Sea, Persian Gulf, or open ocean, you need a commercial AIS provider. Industry-standard sources include Spire Global, exactEarth, and S&P Global Maritime Intelligence. Many investigative journalism organizations have existing subscriptions — check with your newsroom's data team before purchasing.

**Can I add my own watchlist?**

Yes. If you have a CSV file of vessel identifiers, import it with:

```bash
radiancefleet watchlist import --source kse your_watchlist.csv
```

Replace `kse` with whatever label identifies your source. The tool uses fuzzy matching to handle minor variations in vessel names (85% similarity threshold). Supported watchlist formats include OFAC SDN, KSE shadow fleet list, and OpenSanctions vessel data. For custom formats, see the CLI reference at [CLI_REFERENCE.md](CLI_REFERENCE.md).

**Can I work offline?**

Yes, for detection and analysis. Once you have AIS data loaded and sanctions lists downloaded, RadianceFleet runs entirely locally. The only features that require internet are `radiancefleet data fetch` (downloading sanctions lists) and the Copernicus satellite imagery links (which open in your browser). The database, detection pipeline, and web interface all run locally on your computer.

**The database shows "unhealthy" in `docker compose ps`. What do I do?**

First, check that Docker Desktop is running (look for the whale icon in your menu bar or system tray). Then try:

```bash
docker compose down
docker compose up -d
```

Wait 30 seconds and run `docker compose ps` again. If the database still shows unhealthy, check whether port 5432 is in use by another application — PostgreSQL uses this port by default. If you have another PostgreSQL installation running, stop it first.

**I get "command not found: radiancefleet" after Step 5. What do I do?**

Make sure you activated the virtual environment. In the `backend` folder, run:

```bash
source .venv/bin/activate
```

Your terminal prompt should show `(.venv)` after this. On Windows, use `.venv\Scripts\activate.bat` instead.

**I found a bug or the tool gave me incorrect output. What do I do?**

Open an issue at https://github.com/radiancefleet/radiancefleet/issues. Include the alert ID, the command you ran, and the output you saw. The more detail you provide, the faster it can be diagnosed.

---

## Next Steps

Once you are comfortable with the basics:

| Document | What it covers |
|---|---|
| [quickstart.md](quickstart.md) | Developer setup, running tests, contributing to the codebase |
| [API_INTEGRATION.md](API_INTEGRATION.md) | Using the REST API from Python scripts, notebooks, or other tools |
| [risk-scoring-config.md](risk-scoring-config.md) | How scoring weights are configured, how to adjust thresholds for your investigation context |
| [avoiding-overclaiming.md](avoiding-overclaiming.md) | Required reading before publishing. What the tool can and cannot show. |
| [coverage-limitations.md](coverage-limitations.md) | AIS data quality by region. Which areas are reliable, which are not, and why. |

---

*RadianceFleet is released under the Apache 2.0 License. It is a community tool, not an official product of any government or law enforcement body. All output carries the disclaimer that it is investigative triage, not legal determination.*
