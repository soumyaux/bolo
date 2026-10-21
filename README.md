<p align="center">
  <img src="assets/icon.png" alt="Bolo" width="110">
</p>

<h1 align="center">Bolo</h1>

<p align="center">
  <b>Hold a key, speak, let go. Your words appear wherever you're typing.</b><br>
  Free, open-source dictation for Apple Silicon Macs. Works fully offline if you want it to.
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#how-to-use-it">How to use it</a> ·
  <a href="#privacy">Privacy</a> ·
  <a href="#troubleshooting">Troubleshooting</a>
</p>

---

*Bolo* (बोलो) means "speak" in Hindi.

## What it does

You hold `fn`, say what you want to write, and let go. A second later the text is typed into whatever app you're in: Slack, Mail, Notes, VS Code, a browser, anything with a text box.

- **Clean text, not a transcript.** "Um", "uh" and repeated words are removed, and punctuation and lists are fixed.
- **Fits the app.** Casual in Slack, formal in Mail, structured in code editors.
- **Prompt mode.** Start with *"as a prompt…"* and your rambling becomes a tidy prompt for ChatGPT, Claude or Cursor.
- **Learns your words.** Correct a name once and Bolo spells it right from then on.
- **Never loses what you said.** If transcription fails, the audio is kept and retried.
- **Private by default.** Without an API key, nothing leaves your Mac.

## Before you start

You need:

- A Mac with **Apple Silicon** (M1, M2, M3, M4 or newer). Intel Macs are not supported.
- **macOS 13 Ventura** or newer.
- About **4 GB** of free disk space.
- 5 to 10 minutes. Most of it is download time.

To check your Mac, open the Apple menu  › **About This Mac**. "Chip" should say Apple M-something.

## Install

**1. Open Terminal.** Press `⌘ Space`, type **Terminal**, and press Return.

**2. Paste this line and press Return:**

```bash
curl -fsSL https://raw.githubusercontent.com/soumyaux/bolo/main/install.sh | bash
```

**3. Answer the questions.** There are at most four, and pressing Return accepts the suggested answer:

| Question | What to choose |
| --- | --- |
| Install Ollama for text cleanup? | **Yes.** It's what makes the text tidy. |
| Which cleanup model? | **1** is the best. Pick **2** if you use Bolo for commercial work (license note below). |
| Groq API key | Optional. Paste a free key from [console.groq.com/keys](https://console.groq.com/keys) for faster, more accurate results, or just press Return to stay 100% offline. |
| Start Bolo when you log in? | **Yes**, unless you'd rather open it yourself. |

The installer downloads everything Bolo needs, so you don't need Python or anything else installed first. It builds **Bolo.app** in your `Applications` folder and opens it. A 🎙 icon appears in the menu bar.

**4. Give Bolo three permissions.** macOS will ask. Allow Bolo in **System Settings › Privacy & Security** under:

| Permission | Why Bolo needs it |
| --- | --- |
| Microphone | To hear you |
| Accessibility | To type the text into other apps |
| Input Monitoring | To notice when you hold the talk key |

**5. Restart Bolo.** Click 🎙 in the menu bar › **Quit**, then open Bolo again from your Applications folder. macOS only applies new permissions after a restart.

You're done. Click into any text box, hold `fn` and speak.

## How to use it

| Do this | What happens |
| --- | --- |
| **Hold `fn`**, speak, let go | Your words are typed where the cursor is |
| **Double-tap `fn`** | Hands-free: keeps listening until you tap `fn` again |
| **Esc** | Cancels the recording |
| Start with **"as a prompt"** | Turns what you say into a structured AI prompt |
| **⌃⌥F** (Control-Option-F) | Fix the last dictation; Bolo remembers the fix |
| **⌃⌥V** (Control-Option-V) | Paste a dictation that was saved after a failure |

The menu bar icon tells you what's happening: 🎙 ready · 🔴 listening · ⏳ working · ⚠️ something needs attention (click it to see what).

On a MacBook with a notch, a small black "island" also drops down from the notch while you speak.

### Tips

- **`fn` opens emoji or Apple's dictation instead?** Go to **System Settings › Keyboard** and set **"Press 🌐 key to"** to **Do Nothing**.
- **Prefer another key?** Use 🎙 › **Settings… › General › Talk key** to choose right ⌘, right ⌥ or right ⌃ instead.
- **Add names and jargon** under 🎙 › **Settings…** so Bolo spells them right from the start.

## Update

Bolo checks for new versions on its own. When one is out, the 🎙 menu shows **Update to Bolo x.y.z…**. Click it, and Bolo downloads the update, quits and opens again in about a minute. Your settings and learned words are kept.

You can also click **Check for Updates…** any time, or run the install command again.

To answer the setup questions again (for example, to add a Groq key later):

```bash
curl -fsSL https://raw.githubusercontent.com/soumyaux/bolo/main/install.sh | bash -s -- --reconfigure
```

See what changed in each version on the [Releases](https://github.com/soumyaux/bolo/releases) page.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/soumyaux/bolo/main/uninstall.sh | bash
```

It asks before deleting your settings and saved words. Ollama is left installed, since other apps may use it.

## Privacy

- **Without a Groq key**, your voice never leaves your Mac.
- **With a Groq key**, the audio is sent to [Groq](https://groq.com/privacy-policy/) to be turned into text. Nothing else is sent.
- Text cleanup always runs on your Mac.
- No accounts, no tracking, no analytics.
- Every few hours Bolo asks GitHub for the list of Bolo versions, to offer updates. That request carries nothing about you or what you said.
- What Bolo keeps stays on your Mac in `~/Library/Application Support/Bolo/`, readable only by your user account: your settings, the text of your last 200 dictations (so you can fix them and Bolo can learn), and the audio of failed dictations for up to 24 hours. The full list is in [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).

## Troubleshooting

**Nothing happens when I hold `fn`.**
Check that Bolo is allowed under both **Accessibility** and **Input Monitoring** in System Settings › Privacy & Security, then quit and reopen Bolo. If it's already allowed, remove Bolo from both lists with the **–** button and add it again. macOS sometimes forgets an app after it's updated.

**The text shows up in Bolo but isn't typed into the app.**
Click inside a text box first. If nothing that accepts text has focus, Bolo puts the text on the clipboard so you can paste it with ⌘V.

**The ⚠️ icon says "Cleanup model missing".**
Open Terminal and run the `ollama pull …` command the menu shows.

**Groq stopped working.**
Groq's free limits sometimes change. Bolo switches to the offline model on its own, so you can keep dictating.

**Something else is wrong.**
The log is in `~/Library/Logs/Bolo.log`. It never contains what you said. [Open an issue](https://github.com/soumyaux/bolo/issues/new/choose) and paste the lines from around the problem.

## Settings

Most options are in 🎙 › **Settings…**. Everything is in `~/Library/Application Support/Bolo/config.toml`. Every option and its default is listed in [config.default.toml](config.default.toml). Restart Bolo after you edit the file by hand.

## Models and licenses

| Model | Used for | License |
| --- | --- | --- |
| Whisper large-v3 (Groq) and large-v3-turbo (on your Mac) | Speech to text | MIT |
| qwen2.5:3b (option 1) | Text cleanup | Qwen Research License (**non-commercial use only**) |
| llama3.2:3b (option 2) | Text cleanup | Llama 3.2 Community License |

To use any other [Ollama](https://ollama.com) model, set `[cleanup] model` in your config and run `ollama pull <model>`.

## Contributing

Bug reports and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) explains how to run Bolo from source, and [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) walks through the code.

## License

[MIT](LICENSE). Free to use, change and share.
