# Custom voices and the .dtv file format.
#
# A custom voice is a built-in base voice plus a value for every [:dv]
# parameter; built-in voices are never changed. .dtv files use the same JSON
# format as the DECtalk Software add-on:
#   {"format": "dectalk-voice", "version": 1,
#    "name": "...", "base": "paul", "params": {"ap": 122, ...}}

import json

import config

from . import _conf, _params

DTV_FORMAT = "dectalk-voice"
DTV_VERSION = 1

#: Prefix of custom voice ids in the voice list.
CUSTOM_PREFIX = "custom:"


class VoiceStoreError(Exception):
	pass


def _cleanBase(base):
	return base if base in _params.VOICES else _params.DEFAULT_VOICE


def _cleanParams(params, base):
	"""Full, clamped parameter set; missing or invalid values come from `base`."""
	defaults = _params.VOICE_DEFAULTS[_cleanBase(base)]
	if not isinstance(params, dict):
		params = {}
	cleaned = {}
	for code in _params.PARAM_CODES:
		try:
			value = int(params.get(code, defaults[code]))
		except (TypeError, ValueError):
			value = defaults[code]
		cleaned[code] = _params.clampParam(code, value)
	return cleaned


def load():
	"""All custom voices: {name: {"base": id, "params": {...}}}."""
	try:
		data = json.loads(config.conf[_conf.SECTION]["customVoices"])
	except Exception:
		return {}
	voices = {}
	if isinstance(data, dict):
		for name, rec in data.items():
			if isinstance(rec, dict):
				base = _cleanBase(rec.get("base"))
				voices[str(name)] = {"base": base, "params": _cleanParams(rec.get("params"), base)}
	return voices


def get(name):
	return load().get(name)


def _save(voices):
	config.conf[_conf.SECTION]["customVoices"] = json.dumps(voices, sort_keys=True)


def create(name, base, params):
	"""Save a custom voice, replacing any voice with the same name."""
	name = (name or "").strip()
	if not name:
		raise VoiceStoreError("the voice needs a name")
	if base not in _params.VOICES:
		raise VoiceStoreError("unknown base voice %r" % base)
	voices = load()
	voices[name] = {"base": base, "params": _cleanParams(params, base)}
	_save(voices)
	return name


def delete(name):
	voices = load()
	if name in voices:
		del voices[name]
		_save(voices)


def exportDtv(name, path):
	rec = load().get(name)
	if rec is None:
		raise VoiceStoreError("no custom voice named %r" % name)
	params = dict(rec["params"])
	# Required by the DECtalk Software add-on's importer.
	params.setdefault("ft", _params.SOFTWARE_FT[rec["base"]])
	doc = {
		"format": DTV_FORMAT,
		"version": DTV_VERSION,
		"name": name,
		"base": rec["base"],
		"params": params,
	}
	with open(path, "w", encoding="utf-8") as f:
		json.dump(doc, f, indent=2, sort_keys=True)


def importDtv(path):
	"""Import a .dtv file; returns the (possibly deduplicated) voice name."""
	try:
		with open(path, "r", encoding="utf-8") as f:
			doc = json.load(f)
	except (OSError, ValueError) as e:
		raise VoiceStoreError("could not read voice file: %s" % e)
	if not isinstance(doc, dict) or doc.get("format") != DTV_FORMAT:
		raise VoiceStoreError("not a DECtalk voice (.dtv) file")
	try:
		version = int(doc.get("version", 0))
	except (TypeError, ValueError):
		version = 0
	if version > DTV_VERSION:
		raise VoiceStoreError("this voice file needs a newer version of the add-on")
	name = str(doc.get("name", "")).strip() or "Imported voice"
	base = _cleanBase(doc.get("base"))
	params = _cleanParams(doc.get("params"), base)
	voices = load()
	unique = name
	counter = 2
	while unique in voices:
		unique = "%s (%d)" % (name, counter)
		counter += 1
	voices[unique] = {"base": base, "params": params}
	_save(voices)
	return unique
