# DECtalk Express parameter tables.
# Limits: DECtalk PC 4.1 reference manual, appendix D.
# Voice defaults: DECtalk Express firmware 4.2CD.

from collections import OrderedDict

#: (code, display name, category) for each [:dv] parameter, in editor order.
VOICE_PARAMS = (
	("ap", "Average pitch (Hz)", "Pitch & Intonation"),
	("pr", "Pitch range (%)", "Pitch & Intonation"),
	("as", "Assertiveness (%)", "Pitch & Intonation"),
	("bf", "Baseline fall (Hz)", "Pitch & Intonation"),
	("hr", "Hat rise (Hz)", "Pitch & Intonation"),
	("sr", "Stress rise (Hz)", "Pitch & Intonation"),
	("hs", "Head size (%)", "Voice Quality"),
	("sm", "Smoothness (%)", "Voice Quality"),
	("ri", "Richness (%)", "Voice Quality"),
	("br", "Breathiness (dB)", "Voice Quality"),
	("la", "Laryngealization (%)", "Voice Quality"),
	("lx", "Lax breathiness (%)", "Voice Quality"),
	("qu", "Quickness (%)", "Voice Quality"),
	("nf", "Fixed open-glottis samples", "Voice Quality"),
	("sx", "Sex (0=female, 1=male)", "Voice Quality"),
	("f4", "Formant 4 frequency (Hz)", "Formants"),
	("b4", "Formant 4 bandwidth (Hz)", "Formants"),
	("f5", "Formant 5 frequency (Hz)", "Formants"),
	("b5", "Formant 5 bandwidth (Hz)", "Formants"),
	("gv", "Gain: voicing (dB)", "Source Gains"),
	("gh", "Gain: aspiration (dB)", "Source Gains"),
	("gf", "Gain: frication (dB)", "Source Gains"),
	("gn", "Gain: nasalization (dB)", "Source Gains"),
	("g1", "Gain: cascade formant 1 (dB)", "Source Gains"),
	("g2", "Gain: cascade formant 2 (dB)", "Source Gains"),
	("g3", "Gain: cascade formant 3 (dB)", "Source Gains"),
	("g4", "Gain: cascade formant 4 (dB)", "Source Gains"),
	("g5", "Gain: loudness (dB)", "Source Gains"),
)

PARAM_CODES = tuple(code for code, _label, _cat in VOICE_PARAMS)

#: (min, max) for each [:dv] parameter.
VOICE_LIMITS = {
	"sx": (0, 1), "hs": (65, 145), "f4": (2000, 4650), "f5": (2500, 4950),
	"b4": (100, 2048), "b5": (100, 2048),
	"br": (0, 72), "lx": (0, 100), "sm": (0, 100), "ri": (0, 100),
	"nf": (0, 100), "la": (0, 100),
	"bf": (0, 40), "hr": (2, 100), "sr": (1, 100), "as": (0, 100),
	"qu": (0, 100), "ap": (50, 350), "pr": (0, 250),
	"gv": (0, 86), "gh": (0, 86), "gf": (0, 86), "gn": (0, 86),
	"g1": (0, 86), "g2": (0, 86), "g3": (0, 86), "g4": (0, 86), "g5": (0, 86),
}

#: Voice id -> ([:n] letter, display name).
VOICES = OrderedDict((
	("paul", ("p", "Perfect Paul")),
	("betty", ("b", "Beautiful Betty")),
	("harry", ("h", "Huge Harry")),
	("frank", ("f", "Frail Frank")),
	("dennis", ("d", "Doctor Dennis")),
	("kit", ("k", "Kit the Kid")),
	("ursula", ("u", "Uppity Ursula")),
	("rita", ("r", "Rough Rita")),
	("wendy", ("w", "Whispering Wendy")),
	("val", ("v", "Variable Val")),
))

DEFAULT_VOICE = "paul"


def _v(**kw):
	# "as" is a keyword, so the tables spell it "as_".
	kw["as"] = kw.pop("as_")
	return kw


#: Built-in [:dv] values of each voice.
VOICE_DEFAULTS = {
	"paul": _v(
		sx=1, hs=100, f4=3300, b4=260, f5=3650, b5=330, br=0, lx=0, sm=3,
		ri=70, nf=0, la=0, bf=18, hr=18, sr=32, as_=100, qu=40, ap=122, pr=100,
		gv=65, gh=70, gf=70, gn=74, g1=68, g2=60, g3=48, g4=64, g5=86,
	),
	"betty": _v(
		sx=0, hs=100, f4=4450, b4=260, f5=2500, b5=2048, br=0, lx=80, sm=4,
		ri=40, nf=0, la=0, bf=0, hr=14, sr=20, as_=35, qu=55, ap=208, pr=240,
		gv=65, gh=70, gf=72, gn=68, g1=69, g2=65, g3=50, g4=56, g5=81,
	),
	"harry": _v(
		sx=1, hs=115, f4=3300, b4=200, f5=3850, b5=240, br=0, lx=0, sm=12,
		ri=86, nf=10, la=0, bf=9, hr=20, sr=30, as_=100, qu=10, ap=89, pr=80,
		gv=65, gh=70, gf=70, gn=73, g1=71, g2=60, g3=52, g4=62, g5=81,
	),
	"frank": _v(
		sx=1, hs=90, f4=3650, b4=280, f5=4200, b5=300, br=50, lx=50, sm=46,
		ri=40, nf=0, la=5, bf=9, hr=20, sr=22, as_=65, qu=0, ap=155, pr=90,
		gv=63, gh=68, gf=68, gn=75, g1=63, g2=58, g3=56, g4=66, g5=86,
	),
	"dennis": _v(
		sx=1, hs=105, f4=3200, b4=240, f5=3600, b5=280, br=38, lx=70, sm=100,
		ri=0, nf=10, la=0, bf=9, hr=20, sr=22, as_=100, qu=50, ap=110, pr=135,
		gv=63, gh=68, gf=68, gn=76, g1=75, g2=60, g3=52, g4=61, g5=84,
	),
	"kit": _v(
		sx=0, hs=77, f4=2500, b4=2048, f5=2500, b5=2048, br=47, lx=75, sm=5,
		ri=70, nf=0, la=0, bf=0, hr=20, sr=22, as_=65, qu=50, ap=296, pr=180,
		gv=65, gh=70, gf=72, gn=71, g1=69, g2=69, g3=52, g4=50, g5=73,
	),
	"ursula": _v(
		sx=0, hs=95, f4=4450, b4=260, f5=2500, b5=2048, br=0, lx=50, sm=60,
		ri=100, nf=10, la=0, bf=8, hr=20, sr=32, as_=100, qu=30, ap=240,
		pr=135, gv=65, gh=70, gf=70, gn=71, g1=67, g2=65, g3=51, g4=58, g5=80,
	),
	"rita": _v(
		sx=0, hs=95, f4=4000, b4=250, f5=2500, b5=2048, br=46, lx=0, sm=24,
		ri=20, nf=0, la=4, bf=0, hr=20, sr=32, as_=65, qu=30, ap=106, pr=80,
		gv=65, gh=70, gf=72, gn=73, g1=69, g2=72, g3=48, g4=54, g5=83,
	),
	"wendy": _v(
		sx=0, hs=100, f4=4500, b4=400, f5=2500, b5=2048, br=55, lx=80, sm=100,
		ri=0, nf=10, la=0, bf=0, hr=20, sr=22, as_=50, qu=10, ap=200, pr=175,
		gv=59, gh=68, gf=70, gn=75, g1=69, g2=62, g3=53, g4=55, g5=83,
	),
}
# Val starts out as Paul.
VOICE_DEFAULTS["val"] = dict(VOICE_DEFAULTS["paul"])

#: Spectral tilt (ft) per voice, from the firmware tables. Not settable on the
#: Express; only written to .dtv exports, which the DECtalk Software add-on needs.
SOFTWARE_FT = {
	"paul": 75, "betty": 75, "harry": 60, "frank": 100, "dennis": 100,
	"kit": 75, "ursula": 100, "rita": 0, "wendy": 100, "val": 75,
}

RATE_RANGE = (75, 650)  # [:ra], words per minute
DEFAULT_RATE = 180
COMMA_PAUSE_RANGE = (-40, 2000)  # [:cp], ms added
PERIOD_PAUSE_RANGE = (-380, 2000)  # [:pp], ms added


def clampParam(code, value):
	lo, hi = VOICE_LIMITS[code]
	return max(lo, min(hi, int(value)))


def clampAll(params):
	return {code: clampParam(code, value) for code, value in params.items() if code in VOICE_LIMITS}


def toPercent(code, value):
	"""Parameter value as 0-100 % of its range."""
	lo, hi = VOICE_LIMITS[code]
	return int(round((value - lo) * 100.0 / (hi - lo)))


def fromPercent(code, percent):
	"""Parameter value for `percent` % of its range."""
	lo, hi = VOICE_LIMITS[code]
	return clampParam(code, round(lo + percent * (hi - lo) / 100.0))
