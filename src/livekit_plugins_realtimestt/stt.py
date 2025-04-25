import threading
import typing
import weakref

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
from livekit.agents.utils import AudioBuffer

from RealtimeSTT import AudioToTextRecorder, AudioToTextRecorderClient

from .log import logger


SAMPLE_RATE = 16_000
BITS_PER_SAMPLE = 16
NUM_CHANNELS = 1


class STT(stt.STT):
    def __init__(
        self,
        *,
        use_client: bool = False,
        options: typing.Dict[str, typing.Any],
    ):
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=options.get("enable_realtime_transcription", False),
            )
        )
        self._use_client = use_client
        self._options = options
        self._SpeechStream = SpeechStream
        self._streams = weakref.WeakSet[SpeechStream]()
        self._recorder = None

    def prewarm(self):
        self._init_recorder()

    def _init_recorder(self):
        if self._recorder:
            return
        elif self._use_client:
            self._recorder = AudioToTextRecorderClient(
                autostart_server=False,
                **self._options,
                use_microphone=False,
                on_realtime_transcription_update=self._on_interim_transcript,
            )
        else:
            self._recorder = AudioToTextRecorder(
                **self._options,
                use_microphone=False,
                spinner=False,
                on_realtime_transcription_update=self._on_interim_transcript,
            )

    async def aclose(self):
        for stream in self._streams:
            await stream.aclose()

        if self._recorder:
            self._recorder.abort()
            self._recorder.stop()

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
        conn_options: APIConnectOptions,
        recorder: typing.Any,
    ) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=SAMPLE_RATE)

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
            language=self._recorder.language,
            text=text,
        )
        interim_event = stt.SpeechEvent(
            type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
            alternatives=[speech_data],
        )
        self._event_ch.send_nowait(interim_event)

    def _on_final_transcript(self, text: str):
        speech_data = stt.SpeechData(
            language=self._recorder.language,
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
