# DECtalk Express synthesizer driver for NVDA 2019.3 and later.
# Serial line: 9600 baud, 8N1, XON/XOFF (see _serialio).
# Driver id "dectalkexpress" avoids clashes with the old "express" driver
# and the DECtalk Software add-on ("dectalknew").

import time
from collections import OrderedDict

from logHandler import log
from synthDriverHandler import SynthDriver as BaseSynthDriver
from synthDriverHandler import VoiceInfo, synthDoneSpeaking, synthIndexReached

try:
	from autoSettingsUtils.driverSetting import BooleanDriverSetting, NumericDriverSetting
except ImportError:
	from driverHandler import BooleanDriverSetting, NumericDriverSetting

try:
	from speech.commands import (
		CharacterModeCommand,
		IndexCommand,
		LangChangeCommand,
		PitchCommand,
		RateCommand,
	)
except ImportError:
	from speech import (
		CharacterModeCommand,
		IndexCommand,
		LangChangeCommand,
		PitchCommand,
		RateCommand,
	)

from . import _conf, _params, _serialio, _text, _voicestore

#: Resend the full voice state at least this often (s), in case the unit was power cycled.
RESYNC_INTERVAL = 30.0

#: NVDA slider -> [:dv] parameter it sets directly (0 % = minimum, 100 % = maximum).
SLIDER_PARAMS = {"pitch": "ap", "inflection": "pr"}


def _clamp(value, lo, hi):
	return max(lo, min(hi, value))


class SynthDriver(BaseSynthDriver):
	name = "dectalkexpress"
	description = "DECtalk Express"

	supportedSettings = (
		BaseSynthDriver.VoiceSetting(),
		BaseSynthDriver.RateSetting(),
		BaseSynthDriver.PitchSetting(),
		BaseSynthDriver.InflectionSetting(),
		NumericDriverSetting(
			"periodPause", "Sentence pause (ms)", defaultVal=0,
			minVal=_params.PERIOD_PAUSE_RANGE[0], maxVal=_params.PERIOD_PAUSE_RANGE[1],
			normalStep=20, largeStep=100,
		),
		NumericDriverSetting(
			"commaPause", "Comma pause (ms)", defaultVal=0,
			minVal=_params.COMMA_PAUSE_RANGE[0], maxVal=_params.COMMA_PAUSE_RANGE[1],
			normalStep=20, largeStep=100,
		),
		BooleanDriverSetting(
			"splitMultiCase", "Split mixed-case words (DECTalk -> DEC Talk)",
			defaultVal=True,
		),
		BooleanDriverSetting(
			"inlineCommands", "Allow inline DECtalk commands in spoken text",
			defaultVal=True,
		),
	)

	supportedCommands = {
		IndexCommand,
		CharacterModeCommand,
		LangChangeCommand,
		PitchCommand,
		RateCommand,
	}
	supportedNotifications = {synthIndexReached, synthDoneSpeaking}

	@classmethod
	def check(cls):
		# The port can only be tested by opening it; that happens on load.
		return True

	def __init__(self):
		self._voice = _params.DEFAULT_VOICE
		self._wpm = _params.DEFAULT_RATE
		self._slider = {}  # [:dv] code -> value set by the pitch/inflection sliders
		self._resetSliders()
		self._periodPause = 0
		self._commaPause = 0
		self._splitMultiCase = True
		self._inlineCommands = True
		self._needPrefix = True
		self._lastPrefix = 0.0
		self._pausesSent = False
		port = _conf.getPort()
		try:
			self._conn = self._connect(port)
		except Exception as e:
			# NVDA then reports that the synthesizer could not be loaded.
			log.error("DECtalk Express: %s: %s" % (port, e))
			raise RuntimeError("DECtalk Express not available on %s: %s" % (port, e))
		super(SynthDriver, self).__init__()
		log.info("DECtalk Express: using %s" % port)

	def _connect(self, port):
		return _serialio.Connection(port, onIndex=self._onIndex, onDone=self._onDone)

	def terminate(self):
		try:
			super(SynthDriver, self).terminate()
		finally:
			conn, self._conn = self._conn, None
			if conn is not None:
				conn.close()

	@property
	def portName(self):
		return self._conn.portName if self._conn is not None else None

	def reopen(self, port):
		"""Switch to `port`; keeps the old port and re-raises on failure."""
		old = self._conn
		if old is not None and old.portName.upper() == port.upper():
			return
		if old is not None:
			old.close()
		try:
			self._conn = self._connect(port)
		except Exception:
			self._conn = None
			if old is not None:
				try:
					self._conn = self._connect(old.portName)
				except Exception:
					log.error("DECtalk Express: could not reopen %s" % old.portName, exc_info=True)
			raise
		self._needPrefix = True

	# Called on the serial reader thread.
	def _onIndex(self, index):
		synthIndexReached.notify(synth=self, index=index)

	def _onDone(self):
		synthDoneSpeaking.notify(synth=self)

	# -- speech ---------------------------------------------------------------

	def speak(self, speechSequence):
		if self._conn is None:
			return
		segments = []
		hasText = False
		letterMode = False
		changed = set()  # inline prosody changes to undo at the end
		for item in speechSequence:
			if isinstance(item, str):
				text = self._prepareText(item)
				if text.strip():
					spoken = len(text) * (4 if letterMode else 1)
					segments.append(("text", (text + " ").encode("ascii", "replace"), spoken))
					hasText = True
			elif isinstance(item, IndexCommand):
				segments.append(("mark", item.index))
			elif isinstance(item, CharacterModeCommand):
				letterMode = bool(item.state)
				segments.append(("cmd", b"[:sa le]" if letterMode else b"[:sa c]"))
			elif isinstance(item, PitchCommand):
				segments.append(("cmd", self._sliderCommand("pitch", item)))
				changed.add("pitch")
			elif isinstance(item, RateCommand):
				pct = self._commandValue(item, self._get_rate())
				segments.append(("cmd", b"[:ra %d]" % self._percentToWpm(pct)))
				changed.add("rate")
			# LangChangeCommand: the unit only speaks US English.
		restore = []
		if letterMode:
			restore.append(b"[:sa c]")
		if "pitch" in changed:
			restore.append(b"[:dv ap %d]" % self._slider["ap"])
		if "rate" in changed:
			restore.append(b"[:ra %d]" % self._wpm)
		if restore:
			segments.append(("cmd", b"".join(restore)))
		now = time.time()
		# Only with text: an utterance without text may never reach the unit.
		if hasText and (self._needPrefix or now - self._lastPrefix > RESYNC_INTERVAL):
			segments.insert(0, ("cmd", self._prefix().encode("ascii")))
			self._needPrefix = False
			self._lastPrefix = now
		segments.append(("mark", _serialio.DONE))
		segments.append(("cmd", _serialio.CTRL_K))
		self._conn.speak(segments, self._charsPerSecond())

	def cancel(self):
		if self._conn is not None:
			self._conn.cancel()
		self._needPrefix = True

	def pause(self, switch):
		if self._conn is not None:
			self._conn.pause(switch)

	def _prepareText(self, text):
		text = _text.toAscii(text)
		if self._inlineCommands:
			text = _text.balanceBrackets(text)
		else:
			text = _text.stripCommands(text)
		if self._splitMultiCase:
			text = _text.splitMultiCase(text)
		return text

	@staticmethod
	def _commandValue(command, current):
		"""Absolute percentage a prosody command asks for."""
		try:
			return command.newValue
		except Exception:
			try:
				return current * command.multiplier
			except Exception:
				return current

	def _sliderCommand(self, setting, command):
		code = SLIDER_PARAMS[setting]
		pct = self._commandValue(command, _params.toPercent(code, self._slider[code]))
		return b"[:dv %s %d]" % (code.encode("ascii"), _params.fromPercent(code, pct))

	def previewVoice(self, base, params, text=None):
		"""Speak a sample with `base` + `params` (voice manager Test button)."""
		if self._conn is None:
			return
		if base not in _params.VOICES:
			base = _params.DEFAULT_VOICE
		params = _voicestore._cleanParams(params, base)
		sample = text or "DECtalk voice preview. The quick brown fox jumps over the lazy dog."
		self.cancel()
		prefix = self._prefixFor(base, params)
		self._conn.speak(
			[
				("cmd", prefix.encode("ascii")),
				("text", self._prepareText(sample).encode("ascii", "replace"), len(sample)),
				("cmd", _serialio.CTRL_K),
			],
			self._charsPerSecond(),
		)
		self._needPrefix = True

	def speakRaw(self, text):
		"""Speak `text` unchanged (settings panel Test button)."""
		if self._conn is not None:
			data = _text.toAscii(text).encode("ascii", "replace")
			self._conn.speak([("text", data, len(text)), ("cmd", _serialio.CTRL_K)], self._charsPerSecond())

	# -- voice state ------------------------------------------------------------

	def _prefix(self):
		params = self._voiceParams()
		params.update(self._slider)
		return self._prefixFor(self._voiceBase(), params)

	def _prefixFor(self, base, params):
		parts = ["[:n%s]" % _params.VOICES[base][0], "[:sa c]", "[:ra %d]" % self._wpm]
		if self._periodPause or self._commaPause or self._pausesSent:
			parts.append("[:pp %d :cp %d]" % (self._periodPause, self._commaPause))
			self._pausesSent = True
		# [:nX] loads the built-in definition; send only what differs from it.
		defaults = _params.VOICE_DEFAULTS[base]
		dv = " ".join(
			"%s %d" % (code, params[code]) for code in _params.PARAM_CODES
			if params[code] != defaults[code]
		)
		if dv:
			parts.append("[:dv %s]" % dv)
		return "".join(parts)

	def _resetSliders(self):
		"""Load pitch and inflection from the current voice."""
		params = self._voiceParams()
		self._slider = {code: params[code] for code in SLIDER_PARAMS.values()}

	# -- voices -------------------------------------------------------------------

	def _getAvailableVoices(self):
		voices = OrderedDict(
			(vid, VoiceInfo(vid, displayName, "en"))
			for vid, (_letter, displayName) in _params.VOICES.items()
		)
		for name in sorted(_voicestore.load(), key=lambda n: n.lower()):
			vid = _voicestore.CUSTOM_PREFIX + name
			voices[vid] = VoiceInfo(vid, name, "en")
		return voices

	def _get_availableVoices(self):
		# Custom voices change at runtime: never cache.
		return self._getAvailableVoices()

	def refreshVoices(self):
		if hasattr(self, "_availableVoices"):
			del self._availableVoices
		self._needPrefix = True

	def _get_voice(self):
		return self._voice

	def _set_voice(self, value):
		if value not in _params.VOICES and self._customVoice(value) is None:
			value = _params.DEFAULT_VOICE
		self._voice = value
		self._resetSliders()
		self._needPrefix = True

	def _customVoice(self, voiceId=None):
		voiceId = self._voice if voiceId is None else voiceId
		if not voiceId or not voiceId.startswith(_voicestore.CUSTOM_PREFIX):
			return None
		return _voicestore.get(voiceId[len(_voicestore.CUSTOM_PREFIX):])

	def _voiceBase(self):
		custom = self._customVoice()
		if custom:
			return custom["base"]
		return self._voice if self._voice in _params.VOICES else _params.DEFAULT_VOICE

	def _voiceParams(self):
		"""Full [:dv] set of the current voice, without slider changes."""
		custom = self._customVoice()
		if custom:
			return dict(custom["params"])
		return dict(_params.VOICE_DEFAULTS[self._voiceBase()])

	# -- settings ---------------------------------------------------------------

	@staticmethod
	def _wpmToPercent(wpm):
		lo, hi = _params.RATE_RANGE
		return _clamp(int(round((wpm - lo) * 100.0 / (hi - lo))), 0, 100)

	@staticmethod
	def _percentToWpm(pct):
		lo, hi = _params.RATE_RANGE
		return _clamp(int(round(lo + pct * (hi - lo) / 100.0)), lo, hi)

	def _charsPerSecond(self):
		return self._wpm / 10.0  # about 6 characters per word

	def _get_rate(self):
		return self._wpmToPercent(self._wpm)

	def _set_rate(self, value):
		# Same percentage as now: keep the exact value (e.g. 180 wpm).
		if int(value) != self._get_rate():
			self._wpm = self._percentToWpm(value)
			self._needPrefix = True

	def _getSlider(self, setting):
		code = SLIDER_PARAMS[setting]
		return _params.toPercent(code, self._slider[code])

	def _setSlider(self, setting, value):
		if int(value) != self._getSlider(setting):
			code = SLIDER_PARAMS[setting]
			self._slider[code] = _params.fromPercent(code, value)
			self._needPrefix = True

	def _get_pitch(self):
		return self._getSlider("pitch")

	def _set_pitch(self, value):
		self._setSlider("pitch", value)

	def _get_inflection(self):
		return self._getSlider("inflection")

	def _set_inflection(self, value):
		self._setSlider("inflection", value)

	def _get_periodPause(self):
		return self._periodPause

	def _set_periodPause(self, value):
		self._periodPause = _clamp(int(value), *_params.PERIOD_PAUSE_RANGE)
		self._needPrefix = True

	def _get_commaPause(self):
		return self._commaPause

	def _set_commaPause(self, value):
		self._commaPause = _clamp(int(value), *_params.COMMA_PAUSE_RANGE)
		self._needPrefix = True

	def _get_splitMultiCase(self):
		return self._splitMultiCase

	def _set_splitMultiCase(self, value):
		self._splitMultiCase = bool(value)

	def _get_inlineCommands(self):
		return self._inlineCommands

	def _set_inlineCommands(self, value):
		self._inlineCommands = bool(value)
