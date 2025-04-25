from abc import abstractmethod
import threading
import typing
import weakref
from dataclasses import dataclass

from livekit import rtc
from livekit.agents import (
    APIConnectOptions,
    stt,
    utils,
)
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    NotGivenOr,
)
from livekit.agents.utils import AudioBuffer, is_given

from .log import logger


SAMPLE_RATE = 16_000
BITS_PER_SAMPLE = 16
NUM_CHANNELS = 1


@dataclass
class STTOptions:
    language: str = ""
    enable_realtime_transcription: bool = False


class STT(stt.STT):
    def __init__(
        self,
        *,
        options: STTOptions = STTOptions(),
    ):
        options = STTOptions(**options)
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True, interim_results=options.enable_realtime_transcription
            )
        )
        self._options = options
        self._SpeechStream = SpeechStream
        self._streams = weakref.WeakSet[SpeechStream]()
        self._recorder = None

    def prewarm(self):
        self._init_recorder()

    @abstractmethod
    async def _init_recorder(
        self,
    ) -> None: ...

    async def aclose(self):
        for stream in self._streams:
            await stream.aclose()

        if self._recorder:
            self._recorder.abort()
            self._recorder.stop()

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

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ):
        self._init_recorder()
        stream = SpeechStream(
            stt=self,
            options=self._options,
            conn_options=conn_options,
            recorder=self._recorder,
        )
        self._streams.add(stream)
        return stream

    def _on_interim_transcript(self, text: str):
        for stream in self._streams:
            stream._on_interim_transcript(text)


class SpeechStream(stt.SpeechStream):
    def __init__(
        self,
        *,
        stt: STT,
        options: STTOptions,
        conn_options: APIConnectOptions,
        recorder: typing.Any,
    ) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=SAMPLE_RATE)

        self._options = options
        self._recorder = recorder
        self._speaking = False
        self._recording = False

    async def aclose(self):
        if self._recorder:
            if hasattr(self._recorder, "_recording"):
                self._recorder._recording = False
            self._recording = False
            self._speaking = False

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
        @utils.log_exceptions(logger=logger)
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
                    self._recorder.feed_audio(frame.data.tobytes(), None)

        def read():
            self._recording = True
            if hasattr(self._recorder, "_recording"):
                self._recorder._recording = True
            while self._recording:
                try:
                    text = self._recorder.text()
                except Exception:
                    continue
                self._on_final_transcript(text)

        read_thread = threading.Thread(target=read)
        read_thread.start()

        while self._recording:
            await send()
