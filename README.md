<br>

<h1 align="center">RealtimeSTT plugin for LiveKit</h1>

<p align="center">
  <strong>Use RealtimeSTT as the speech-to-text component in LiveKit</strong>
</p>

<br>

[LiveKit](https://github.com/livekit/livekit) is an open-source WebRTC platform with support for AI agents.

[RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) is a speech-to-text library with advanced voice activity detection, wake-word activation and instant transcription.

This is a plugin for LiveKit that uses RealtimeSTT for speech-to-text generation. It supports only streaming mode, which means VAD and turn-detection components should be disabled in LiveKit.

## Installation

```bash
pip install livekit-plugins-realtimestt
```

## Example

```python
from livekit.agents import Agent
from livekit-plugins-realtimestt import STT

agent = Agent(
    stt=STT(
      # For the full list of options, see RealtimeSTT documentation.
      options={
        # When "use_client" is True, the plugin will try to connect to a RealtimeSTT server
        # via WebSockets, otherwise it will run the library in-process. In that case,
        # it is recommended to pre-initialize the plugin by calling `stt.prewarm()`
        # to preload the model and other resources.
        "use_client": True,
        # When "enable_realtime_transcription" is True, interim (partial) transcriptions
        # will be generated in real-time.
        "enable_realtime_transcription": True,
        "language": "de",
      }
    )
)
```
