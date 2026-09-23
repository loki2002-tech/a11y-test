# 🧭 The Complete Guide to Your AI Accessibility Agent

Take a deep breath. You are building an incredibly advanced piece of software that combines traditional web testing with cutting-edge AI. It is completely normal to feel overwhelmed! 

This document breaks down exactly what we have built, why we built it, and how it works—in plain English.

---

## 1. Why are we building this? (The Problem)
Standard accessibility testing tools (like Google Lighthouse) are dumb. 
1. They tell you a button is broken, but they **don't tell you how to fix the code**.
2. They only scan the page exactly as it loads. They **don't know how to click dropdowns or open menus** to find hidden bugs.

We are building a tool that solves both problems. It clicks around like a human to find hidden bugs, and it uses AI to write the code to fix them.

---

## 2. How does it actually find issues?
**The AI DOES NOT find the bugs.** AI is too prone to hallucinating (making things up) to be trusted to find bugs. 

Instead, we use strict, mathematical rules to find the bugs:
*   **The axe-core Engine:** This is an industry-standard, non-AI script. We inject it into the browser, and it scans the HTML against official WCAG (Web Content Accessibility Guidelines) rules.
*   **The Keyboard Tester:** We wrote a custom script that forces the browser to press the `Tab` key 50 times to make sure every button on the page shows a visible "focus ring" for users who can't use a mouse.

---

## 3. What does the AI do?
If the AI doesn't find the bugs, what is it doing? 
**The AI acts as your Senior Developer.**

Once `axe-core` finds a bug (e.g., "This image is missing alt text"), our program grabs the raw HTML of that broken image and hands it to the AI. 
The AI looks at it and generates:
1. A plain-English explanation of why it's broken.
2. The exact, copy-pasteable HTML code to fix it.

---

## 4. Why Ollama? Which model are we using?
*   **The Provider (Ollama):** Initially, we sent our broken HTML to Google's supercomputers (Gemini) to generate the fixes. But Google strictly limits free accounts to 20 requests per day. You hit that limit almost immediately. **Ollama** is a free program that lets you run an AI completely on your own laptop's graphics card. It is 100% free and unlimited.
*   **The Model (Llama 3.2):** This is the specific "AI Brain" we downloaded into Ollama. It was created by Meta (Facebook). We chose it because it is small enough (3 Billion parameters) to run on a standard laptop without crashing it, but smart enough to read HTML and write code fixes.

---

## 5. What is the "Agentic Loop" we just built?
A normal scanner loads `Register.html`, scans it, and stops. It misses all the bugs hidden inside the "Languages" dropdown.

An **Agentic Scanner** (what we built in Version 0.5) has a "virtual mouse."
1. It looks at the page.
2. It sends the HTML to Llama 3.2 and asks: *"What buttons should I click?"*
3. Llama 3.2 replies: *"Click the `#dropdown-btn`."*
4. Our program physically clicks the dropdown, waits for the menu to open, and runs the scanner *again* on the newly revealed HTML.

---

## 6. Why does the report look like shit?
You are 100% correct—the current HTML report is ugly and hard to read. 

Why? Because right now, we are in the "engine building" phase. We just dumped all the raw data (the bugs, the AI reasoning, the screenshots) into a basic HTML file just to prove that the "engine" works. 

**We don't actually want you to read reports in the future.**
In our final version (Version 0.6), the goal is for the AI to take the fixes it generated and automatically write a `.patch` file (a code update) directly into your project. The tool will just fix your code for you!

---

## 💡 Still confused about anything?
If any part of this is still confusing, tell me exactly which section (1 through 6) doesn't make sense, and I will explain it using a different analogy!
