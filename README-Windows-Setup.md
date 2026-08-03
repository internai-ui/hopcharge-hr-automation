# HopCharge HR Dashboard — Windows Setup

## How to install (one time)

1. Copy this whole folder onto the computer (anywhere — Desktop or Documents is fine).
2. Double-click **`Install-HopCharge.bat`**.
3. Wait. The first time, it sets up everything automatically (a few minutes).
   - If Python isn't on the PC, it downloads and installs it for you.
   - If a blue "Windows protected your PC" box appears, click **More info → Run anyway** (this is normal for a script that isn't code-signed).
4. When it says **INSTALLATION COMPLETE**, you're done.

A shortcut called **HopCharge HR Dashboard** is added to the **Desktop** and the **Start Menu**.

## How to open it (every day)

Double-click the **HopCharge HR Dashboard** shortcut. The dashboard opens in your web browser automatically.

A small app window appears in the background — that's the engine. **To stop the dashboard, close that window.**

## Before the first use — add your two config files

The app keeps your private files in this folder (it's created during install):

```
C:\Users\<your-name>\HopchargeHR\
```

Put these in place:

| File | Where it goes | What it is |
|------|---------------|------------|
| `neon.env` | `HopchargeHR\` | Your Neon Postgres connection settings |
| Google service-account `.json` files | `HopchargeHR\credentials\` | For Google Forms + Drive |

Without `neon.env`, the app still runs in **JSON-only mode** (data saved to local files in `HopchargeHR\output\`). Add `neon.env` later to turn on the cloud database — no reinstall needed.

## If something goes wrong

If the app fails to start, a pop-up shows the reason. The most common cause is a missing or incorrect `neon.env`. Fix the file and double-click the shortcut again.

To completely remove the app: delete this folder, the two shortcuts, and the `HopchargeHR` folder in your user directory.
