from .base import STT, STTOptions

from RealtimeSTT import (
    AudioToTextRecorder,
)


class EmbeddedSTT(STT):
    def __init__(
        self,
        *,
        options: STTOptions = STTOptions(),
    ):
        super().__init__(options=options)

    def _init_recorder(self):
        if not self._recorder:
            self._recorder = AudioToTextRecorder(
                model="large-v2",
                initial_prompt="",
                realtime_model_type="base",
                initial_prompt_realtime="",
                compute_type="int8",
                **self._options.__dict__,
                use_microphone=False,
                spinner=False,
                on_realtime_transcription_update=self._on_interim_transcript,
            )
