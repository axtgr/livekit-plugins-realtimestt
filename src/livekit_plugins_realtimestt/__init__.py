from .stt import STT, SpeechStream
from .log import logger
from livekit.agents import Plugin

__version__ = "0.0.0"
__all__ = ["STT", "SpeechStream", "__version__"]


class RealtimeSTTPlugin(Plugin):
    def __init__(self):
        super().__init__(__name__, __version__, __package__, logger)


Plugin.register_plugin(RealtimeSTTPlugin())
