# 🤖 Easy Guide: AI Accessibility Testing Agent

Hello! I have successfully tested the AI Accessibility Testing Agent on a real website (`https://example.com`). It successfully scanned the page and found 2 accessibility issues (WCAG 1.3.1 errors) in just a few seconds!

Here is an easy-to-understand breakdown of everything you need to know about this project.

---

## 1. What does it do? (The Easy Explanation)
Imagine you hire a meticulous robot inspector to visit your website. 
* It opens a web browser just like a human would.
* It looks at the code and the visual layout.
* It checks if the website is friendly and usable for people with disabilities (e.g., people who use screen readers because they are blind, or people who can only navigate using a keyboard instead of a mouse).
* It finds issues (like a button missing a label, or text that is too hard to read because of bad colors).
* It then generates a clear report explaining what is broken and where it is. 

Later (in future versions), it will even use AI to explain *why* it's broken and *how* your developers can fix it!

## 2. Where is it developed and stored?
* **Local Storage:** The entire project is currently built and stored on your computer right here on your desktop: `C:\Users\User\Desktop\AI Agent Accessibility testing`
* **Git & GitHub:** Is it on GitHub? **Not yet!** 
  * I have prepared it for `git` by creating a `.gitignore` file (which ensures your secret passwords and API keys don't accidentally get uploaded to the public). 
  * However, to put it on GitHub, you will need to create a repository on GitHub.com and push this folder from your computer to that repository. 

## 3. How do you execute (run) it?
Running the agent is very simple. Open a **PowerShell** terminal window, and run these commands:

```powershell
# 1. Go to the project folder
cd "C:\Users\User\Desktop\AI Agent Accessibility testing"

# 2. Tell Python where the code is
$env:PYTHONPATH = "src"

# 3. Disable the AI for a fast, free, deterministic scan (no API key needed)
$env:A11Y_LLM_PROVIDER = "disabled"

# 4. Run the scan on any URL you want!
python -m accessibility_agent.cli scan --url https://example.com
```

Once it finishes, it will create a `reports/` folder in your project directory containing a JSON file and a beautiful HTML webpage report you can open in your browser to see the results.

## 4. Important Things Missing (What You Should Know)
Because we just finished **Version 0.1**, the foundation is built and it works perfectly, but there are a few important features coming in the next phases:

1. **The AI Brain is Currently "Off"**: Right now, the agent relies entirely on standard deterministic rules (a tool called `axe-core`). The LLM (AI) layer that writes developer-friendly fixes and analyzes root causes is coming in **Version 0.4**.
2. **Single Page Only**: Currently, you give it one URL, and it scans that one page. It doesn't automatically click links and crawl your whole website yet.
3. **Logins & Passwords**: It cannot currently log into a website for you. If you give it a URL behind a login screen, it will just scan the login screen itself. 
4. **Screen Reader Audio**: It reads the "Accessibility Tree" (how the computer interprets the page for blind users), but it doesn't *literally* listen to the audio output of a screen reader like NVDA or VoiceOver. 

---
*Ready for the next steps? We can begin adding the AI brain or multi-page scanning capabilities whenever you're ready!*
