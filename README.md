# tvbench

**Measure what a caller actually hears from a telephony voice agent.**

[![CI](https://github.com/ictinnovations/telephony-voice-agent-benchmark/actions/workflows/ci.yml/badge.svg)](https://github.com/ictinnovations/telephony-voice-agent-benchmark/actions)
[![PyPI](https://img.shields.io/pypi/v/tvbench.svg)](https://pypi.org/project/tvbench/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Voice agents get benchmarked on transcription accuracy and model latency. Callers do not experience either of those directly. What they experience is whether the agent's words arrive evenly, whether there are holes in the middle of a sentence, and how long the agent keeps talking after they interrupt.

tvbench measures those three things, from the only position that can honestly measure them: the far end of the call.

```bash
pip install tvbench

# Point it at any agent listening on AudioSocket
tvbench run --host 127.0.0.1 --port 9092 --scenario greeting --runs 5
tvbench run --host 127.0.0.1 --port 9092 --scenario bargein --runs 5
```

## Why timing rather than byte counts

`app_audiosocket` hands every frame it receives to the channel immediately. It does not buffer for you. So an agent that synthesises 2.4 seconds of speech and writes it to the socket in one call has delivered every byte perfectly, and the caller hears the last fraction of a second and nothing before it, because the far end's jitter buffer keeps a handful of frames and discards the rest.

That failure is invisible to anything counting bytes, and invisible in the agent's own logs, which is what makes it expensive to find. It is trivially visible in arrival times.

Everything here is computed from **when** audio arrived, never from how much.

## What it measures

| Metric | Question it answers |
|---|---|
| `opening.first_audible_ms` | How long did the caller wait before hearing anything? |
| `pacing.worst_burst_frames` | Most 20 ms frames that landed inside a single 20 ms window. One is correct. |
| `pacing.realtime_ratio` | Audio delivered divided by wall clock. One is correct. |
| `continuity.worst_gap_ms` | Longest hole inside the agent's own speech. |
| `barge_in.cut_ms` | How long the agent kept talking after the caller started. |
| `barge_in.audio_wasted_ms` | How much speech was rendered and then thrown away. |

## Does it work? The falsification pass

A benchmark that has only ever seen well-behaved agents proves nothing. So the tool ships a reference agent with switchable defects, and `scripts/validate.py` runs the whole matrix. If a defect does not move the metric that is supposed to catch it, the tool is broken.

Five runs per row, Linux 6.6 on 8 cores, Python 3.14. Reproduce with `python scripts/validate.py`.

| Reference behaviour | first audible | worst burst | realtime ratio | worst hole | barge-in cut |
|---|---|---|---|---|---|
| **paced** (correct) | 421 ms | 2 frames | 1.00 | 21 ms | |
| **burst** (whole utterance at once) | 420 ms | **300 frames** | **2764x** | 0 ms | |
| **gappy** (stops mid-turn) | 425 ms | 2 frames | 0.93 | **520 ms** | |
| **paced**, stops when interrupted | 421 ms | 2 frames | 1.02 | 21 ms | **97 ms** |
| **deaf**, ignores the interruption | 421 ms | 2 frames | 1.00 | 21 ms | **4,974 ms** |

Read the rows against each other. Bursting produces a *better* continuity number than correct pacing, because all the audio arrived at once and there were no holes between frames that arrived simultaneously. That is exactly why continuity alone is not a proxy for quality, and why the pacing columns exist.

The two barge-in rows differ by a factor of fifty on the only number the caller notices.

## Scenarios

**`greeting`** answers the call and stays silent. The agent's opening line is the one part of a voice pipeline that runs without a transcript or a model round trip, which makes it the cleanest look at pacing you can get. Callers are silent at exactly this moment anyway.

**`bargein`** waits for the agent to start talking, lets it get a second in, then talks over it. The cut is timed from the first frame of caller speech the harness puts on the wire to the last frame of agent audio that comes back, so it includes voice detection, whatever the agent does to stop playback, and anything it had already queued and could not take back.

## Running it against a real agent

```bash
# Agents with a call allowlist need the id pre-registered first
tvbench run --port 9092 \
  --register-url http://127.0.0.1:9091/register \
  --scenario bargein --runs 5 \
  --label "my-agent 1.2.0" --out results/my-agent.json
```

The harness speaks AudioSocket and nothing else. It does not know or care what is behind the socket, so it works against any agent that accepts an AudioSocket connection, whatever language it is written in.

## Caller audio, and what this does not measure

The caller signal is band-limited noise, amplitude modulated at roughly syllable rate, generated deterministically from a seed. Voice activity detectors accept it as speech, which is all the timing scenarios need.

It is not speech. **tvbench cannot tell you anything about transcription accuracy, and does not try.** A benchmark that claimed a word error rate from synthetic noise would be lying. Use a real speech corpus for that, and use this for timing.

Other limits worth stating plainly:

- **Run it on Linux.** `asyncio.sleep(0.02)` takes about 31 ms on Windows, so the harness cannot pace accurately there and every number drifts. The tool runs on Windows and will warn you with a `realtime_ratio` well under 1.0.
- Loopback is not a network. Add jitter and loss yourself if that is what you want to know.
- One run is an anecdote. The default is five, and the report gives you median and range.

## Submit your results

`results/` takes pull requests. If you maintain a voice agent and you think these numbers are wrong, or unflattering, or measured badly, the fastest way to prove it is a run of your own with the command line included.

Disagreement backed by a reproducible number is the entire point.

## Who made this

Built at [ICT Innovations](https://www.ictinnovations.com/) while fixing our own agent, which had every one of the defects the reference agent now simulates. The write-up of how each was found and measured is [here](https://ictinnovations.com/asterisk-ai-voice-agent-lessons-audiosocket-barge-in/).

Related open source: [asterisk-ai-voice-agent](https://github.com/ictinnovations/asterisk-ai-voice-agent), [asterisk-audiosocket](https://github.com/ictinnovations/asterisk-audiosocket) for the protocol in TypeScript, and [piper-tts-server](https://github.com/ictinnovations/piper-tts-server) for the paced synthesis layer.

MIT licensed.
