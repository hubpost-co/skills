# Clipper

A free agent skill that turns a livestream, its VOD, a podcast or any long video into finished vertical clips (9:16, 1080x1920) on your own computer. No account, no API key, no paid service.

## What it does

- **Watches a live stream** and saves each moment as it happens: it records in short pieces, keeps only the last minutes on disk, and when the chat or the sound spikes it keeps the stretch around it.
- **Scans a VOD of many hours without downloading it whole**: it pulls only the audio and the chat replay, finds where the moments are, and downloads just those stretches.
- **Finds moments three ways**: chat spikes (how many different people reacted, against what is normal for that chat), sound (screams, laughs, sudden silence) and the transcript.
- **Your agent picks the clips**: each one has to make sense to a stranger, open on a hook and end on its payoff. Weak moments are dropped, not padded.
- **Stream layout**: finds the facecam by itself and stacks it over the gameplay, keeping chat and counters out of the frame. Talking heads and podcasts are reframed on whoever is speaking.
- **Edit**: captions two or three words at a time with key words highlighted, punch-in zooms on the reactions, audio levelled for social.
- **Credit**: the creator's link on screen and in the post text of every clip, unless the video is your own.
- **Check**: every clip is checked from real frames (a contact sheet per clip) before it is handed to you.
- **Remembers your preferences**: it asks its questions once.

## Install

```bash
npx skills add hubpost-co/skills
```

Or copy this folder to `~/.claude/skills/hubpost-clipper`. Then open your agent and say **set this up for me**: it checks your computer and installs what is missing.

Then say what you want:

- `watch <channel> and clip it`
- `clip this VOD <link>`
- `clip this video <link or file>`

## What you need

- An agent that can run commands on your computer (Claude Code, Codex, Cursor, Gemini CLI). A chat window in a browser cannot download or cut video.
- macOS or Linux. On Windows, run it inside WSL.
- ffmpeg and Python 3.10 or newer (the setup step installs them), about 2 GB of disk for the tools and the speech model, plus room for the video.

## The honest part

- **Live**: your computer has to stay awake and online for as long as it watches. A moment is saved a little after it happens.
- **Slow chats** give few chat spikes. The sound covers for it, and sound in a game is noisier than chat.
- **Twitch** chat is read the way a logged-out viewer's browser reads it. If Twitch changes that, chat reading stops until this skill is updated; sound still works. VODs expire and subscriber-only VODs cannot be read.
- **YouTube** VODs use the chat replay and the "most replayed" curve when the video has them. Live YouTube and other sites are watched by sound only.
- **Captions** mix the game's voices with the streamer's. The skill fixes what it catches; read them before you post.
- **It picks, you decide.** The scores are a judgement, not a promise of views.

## Other people's streams

A credit and a link are not permission. Clip a stream when its creator runs a clipping programme, when a campaign brief covers it, or when you have their written OK. Platforms may not monetise reused footage. The skill refuses to drop the credit on someone else's video.

## What is inside

| File | Job |
|---|---|
| `SKILL.md` | What the agent follows, including how it picks moments |
| `scripts/doctor.sh` | Checks the computer, prints one fix per missing tool |
| `scripts/prefs.py` | Saved preferences |
| `scripts/watch.py` | Live: records, reads chat and sound, saves moments |
| `scripts/scout.py` | VOD: audio and chat only, finds the stretches worth downloading |
| `scripts/windows.py` | Downloads those stretches, or gathers live moments, into one source |
| `scripts/chat.py` | Chat readers and the spike maths |
| `scripts/fetch.py`, `transcribe.py` | A whole short video, and the word-timed transcript |
| `scripts/layout.py` | Talk or stream, facecam and gameplay areas |
| `scripts/moments.py` | Sound and chat candidates with setup and breathe |
| `scripts/cut.py`, `edit.py`, `check.py` | Reframe, edit, verify |

## After the clips

The skill stops at finished files. If you want them scheduled across your accounts from the same agent, [HubPost](https://hubpost.co/?utm_source=github&utm_medium=skill&utm_campaign=clipper&utm_content=readme) does that; it is made by the same people.

## Credits

Written from scratch. Ideas studied in other MIT-licensed projects, with thanks: reading Twitch chat as a guest and scoring a chat window by distinct people and reactions ([stream-clip-maker](https://github.com/kathennn/stream-clip-maker)), paging the Twitch chat replay by time offset ([sharkyclips](https://github.com/utkualsir/sharkyclips)), cutting from a stream without downloading it whole ([vodkit](https://github.com/valheru7/vodkit)), and the doctor / questions-with-defaults / check-frames-not-code method (Jack Roberts' claude-video-2). Font: Montserrat (SIL Open Font License, `fonts/OFL.txt`). Tools: FFmpeg, yt-dlp, streamlink, faster-whisper, OpenCV.

## License

MIT.
