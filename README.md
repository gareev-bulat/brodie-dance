# Brodie Dance

**An AI dance coach you call on FaceTime. No app to install.**

🥈 2nd place at IvyHacks 2026 (University of Pennsylvania). Built by a team of five, then launched to real users.

<!-- Add a screenshot or a short demo GIF here. It is the most important part of this README. -->

## The idea

Everyone wants to learn the latest TikTok dance, and everyone learns it the same way: pause, rewind, squint, give up. A 15-second clip has no counts, no feedback, and no way to tell which part you are getting wrong.

So we asked: what if you could call a coach who watches you?

## What it does

- Brodie shows you the dance, then teaches it part by part (8-counts) with the song
- It tracks your whole body live and compares you to the creator, joint by joint
- A model we trained scores each part, and an angle check names the body part that is off
- Brodie coaches you by voice between parts, with one specific tip, and never talks over the music
- It all happens on a real FaceTime call, with no app to install

## How it works

| Piece | What it does |
|---|---|
| **Eyes** | MediaPipe pose tracking: 33 body points, 10 times a second, streamed live to the lesson |
| **Judge** | Aligns your moves with the creator's in time (DTW) and scores them with a trained model, with an angle comparison as backup |
| **Voice** | Gemini Live coaches between parts |
| **Music** | Each part is one audio clip: the song for exactly those beats with the count mixed on top, so they never drift apart |
| **Call** | LiveKit carries audio and video. Chert connects it to a real FaceTime line |
| **Any dance** | One command turns a TikTok video into a lesson: poses, beats, 8-count parts and the song |

```
FaceTime call
   -> FaceTime line (Chert)
   -> real-time video room (LiveKit)
   -> worker running the Brodie coach agent (Python)
        pose tracking -> judge -> voice coaching

Website (static, on Vercel)
   -> API + SQLite (Docker container on a VM)
        sign-ups, line status, stats page
```

**The line.** The FaceTime line takes one call at a time, so the website manages a short queue: a visitor enters their email, sees whether the line is free, claims a 15-minute session, and gets the number. When a call ends, the worker tells the website, and the line frees itself about 15 seconds later. An "I'm done dancing" button and the 15-minute limit are backups.

**Outage detection.** When calls stop reaching Brodie, the site detects it and tells visitors, instead of sending them to a dead number.

## What we learned from launching

- Free usage limits on platforms like LiveKit run out faster than you expect, so watch them from day one
- Bugs show up fast once users arrive, so fast troubleshooting matters
- Real-time is a different world: syncing music, counts, voice and pose tracking to the beat took more work than the AI itself
- After a change to the server code, redeploy the server too. Merging alone is not enough
- A demo only has to work once. A launch has to keep working while strangers use it

## My role

During launch week I focused on keeping the team in sync, planning next steps and maintaining the product.

<!-- Add one or two lines on your technical contributions, for example the line auto-release change and its tests, deployments, monitoring and fixes after launch. Only include what you did. -->

## Team

- Steven Peng
- Jason Xiao
- Thanish Malineni
- Abrar Islam
- Bulat Gareev

## Thanks

- James Huang and Seraphim Labs for Brodie
- Chert and Gary Gao for the FaceTime line
- studyspace, AI @ Penn and WUEC for organizing IvyHacks

## Running it

This project depends on third-party services and API keys that are not included in this repository (LiveKit, Gemini, the FaceTime line provider, and admin and heartbeat keys). Create your own `.env.local` with placeholder values like `YOUR_KEY_HERE`, and never commit real keys.

<!-- Add the real setup steps here if you want others to be able to run it. -->

## Status

The FaceTime line is closed. This repository is kept as a record of what we built.
