import threading
import weakref
from dataclasses import dataclass

from livekit import rtc
from livekit.agents import (
    APIConnectOptions,
    stt,
    utils,
)
from livekit.agents.stt import SpeechEventType, STTCapabilities
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    NotGivenOr,
)
from livekit.agents.utils import AudioBuffer, is_given

from RealtimeSTT import (
    AudioToTextRecorderClient,
)

SAMPLE_RATE = 16_000
BITS_PER_SAMPLE = 16
NUM_CHANNELS = 1


@dataclass
class STTOptions:
    language: str = ""
    realtime: bool = False


class STT(stt.STT):
    def __init__(
        self,
        *,
        options: STTOptions = STTOptions(),
    ):
        options = STTOptions(**options)
        super().__init__(
            capabilities=STTCapabilities(
                streaming=True, interim_results=options.realtime
            )
        )
        self._options = options
        self._streams = weakref.WeakSet[SpeechStream]()

    async def aclose(self):
        for stream in self._streams:
            await stream.aclose()

    def update_options(self, *, language: NotGivenOr[str] = NOT_GIVEN) -> None:
        if is_given(language):
            self._language = language

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        raise Exception(
            "Non-streaming speech-to-text is not supported by RealtimeSTT at the moment"
        )

    def _transcription_to_speech_event(
        self,
        text: str,
        event_type: SpeechEventType = stt.SpeechEventType.INTERIM_TRANSCRIPT,
    ) -> stt.SpeechEvent:
        return stt.SpeechEvent(
            type=event_type,
            alternatives=[stt.SpeechData(text=text, language=self._language)],
        )

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ):
        stream = SpeechStream(
            stt=self,
            options=self._options,
            conn_options=conn_options,
            language=language,
        )
        self._streams.add(stream)
        return stream


class SpeechStream(stt.SpeechStream):
    def __init__(
        self,
        *,
        stt: STT,
        options: STTOptions,
        conn_options: APIConnectOptions,
        language: NotGivenOr[str] = NOT_GIVEN,
    ) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=SAMPLE_RATE)

        self._options = options
        self._language = language if is_given(language) else options.language or ""

        self._speaking = False

    async def aclose(self):
        if self._client:
            self._client._recording = False
            self._client.abort()
            self._client.stop()

    def _on_speech_start(self):
        self._speaking = True
        start_event = stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH)
        self._event_ch.send_nowait(start_event)

    def _on_interim_transcript(self, text: str):
        if not self._speaking:
            self._on_speech_start()
        speech_data = stt.SpeechData(
            language=self._language,
            text=text,
        )
        interim_event = stt.SpeechEvent(
            type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
            alternatives=[speech_data],
        )
        self._event_ch.send_nowait(interim_event)

    def _on_final_transcript(self, text: str):
        speech_data = stt.SpeechData(
            language=self._language,
            text=text,
        )
        final_event = stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[speech_data],
        )
        self._event_ch.send_nowait(final_event)
        self._speaking = False

    async def _run(self) -> None:
        self._client = AudioToTextRecorderClient(
            language=self._language,
            use_microphone=False,
            autostart_server=False,
            on_realtime_transcription_update=self._on_interim_transcript,
            enable_realtime_transcription=self._options.realtime,
            realtime_model_type="base",
        )

        async def send():
            samples_50ms = self._needed_sr // 20
            audio_bstream = utils.audio.AudioByteStream(
                sample_rate=self._needed_sr,
                num_channels=NUM_CHANNELS,
                samples_per_channel=samples_50ms,
            )

            async for data in self._input_ch:
                frames: list[rtc.AudioFrame] = []

                if isinstance(data, rtc.AudioFrame):
                    frames.extend(audio_bstream.write(data.data.tobytes()))
                elif isinstance(data, self._FlushSentinel):
                    frames.extend(audio_bstream.flush())

                for frame in frames:
                    self._client.feed_audio(frame.data.tobytes(), None)

        def read():
            self._client._recording = True
            while self._client._recording:
                try:
                    text = self._client.text()
                except Exception:
                    continue
                self._on_final_transcript(text)

        read_thread = threading.Thread(target=read)
        read_thread.start()

        while self._client._recording:
            await send()
