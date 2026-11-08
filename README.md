# SC Rock ID

A small always-on-top HUD that reads the mining-scan signature number off your screen and tells you which ore it is. A green dot means it's an ore you picked, red means it isn't, and yellow means the number fits more than one ore.

## Anti-cheat safety
- It only takes screenshots of a small screen area you choose. This is the same thing OBS or the Snipping Tool does.
- It reads the number with the Windows built-in OCR engine.
- It never touches the game's process, memory or files, and it sends no input to the game.

## Setup
**Easy way:** download `RockID.exe` from the Releases page and double-click it. It needs no Python or install, and you can copy it to any folder or PC with Windows 10 or 11.

**From source:** run `install.bat` once, then `Start Rock ID.bat`.

Run Star Citizen in **Borderless** or **Windowed** mode, because exclusive fullscreen screenshots come out black. On first start, the HUD shows a **📍 Pick signature area** button. Open your mining scanner in game, then click the button. You get 5 seconds to switch to the game, then the screen freezes and you drag a box around the signature number. Click **⌖** to pick the area again later.

## Where settings are saved
`config.json`, next to `RockID.exe` (or `rockid.py`). It holds your scan area, the ores you picked, the window position and options. Delete it to start fresh, or click ⌖ to choose a new scan area. If you put a `data.json` next to the exe, the exe uses it instead of its built-in values.

## HUD buttons
| Button | What it does |
|---|---|
| **Ores** | Pick the ores you want (one or many). Presets, OCR tolerance, scan speed, opacity |
| **Refinery** | Show or hide the best refineries for your picked ores and the detected ore |
| **⌖** | Choose the screen area again |
| **⏸** | Pause or resume scanning |
| **?** | Debug view: what the OCR sees |
| **✕** | Close |

Drag the title bar to move the HUD. Right-click it for a menu.

## How matching works
A cluster's signature is the single-rock value × the number of rocks. For example, Quantainium is 3170, so 6340 means Quantainium ×2. ROC gems are 4000 each, hand-mining gems 3000 each and salvage panels 2000 each.

## After a patch
All values are in `data.json`, which you can edit. The current values are from patch 4.10.2.

## Updates
At start, the exe checks GitHub for a newer release. If there is one, it offers **Download and install**, **Later** or **Skip this version**. The download is checked against the size and SHA-256 that GitHub reports, then the exe swaps itself and restarts. You can turn the check off in **Ores** settings, or check by hand from the right-click menu: **Check for updates…**

## Support
☕ [Buy me a coffee](https://buymeacoffee.com/KuzuCevirme)
New to Star Citizen? Use referral code **STAR-SFQ9-CL7C**: [enlist here](https://www.robertsspaceindustries.com/enlist?referral=STAR-SFQ9-CL7C)

*Fan-made tool, not affiliated with Cloud Imperium Games.*
