# Text preparation: the unit takes 7-bit ASCII, and some control characters
# (Ctrl+C, Ctrl+N/O, XON/XOFF) have special meaning on the line.

import re
import unicodedata

# Written as escapes: several of these are invisible.
_CHAR_MAP = {
	"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
	"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
	"\u00ab": '"', "\u00bb": '"',
	"\u2013": "-", "\u2015": "-", "\u2212": "-",
	"\u2014": " - ",
	"\u2022": "-",
	"\u00b7": ".",
	"\u2026": "...",
	"\u00a0": " ", "\u2007": " ", "\u2009": " ", "\u202f": " ",
	"\ufeff": "",
	"\u00e6": "ae", "\u00c6": "AE",
	"\u00f8": "oe", "\u00d8": "OE",
	"\u00df": "ss",
	"\u0153": "oe", "\u0152": "OE",
	"\u00f0": "d", "\u00d0": "D",
	"\u00fe": "th", "\u00de": "Th",
	"\u0142": "l", "\u0141": "L",
}
_CHAR_TABLE = str.maketrans(_CHAR_MAP)

_CONTROL_RE = re.compile("[\x00-\x1f\x7f]+")


def toAscii(text):
	"""Fold `text` to printable ASCII; control characters become spaces."""
	text = text.translate(_CHAR_TABLE)
	folded = unicodedata.normalize("NFKD", text)
	out = "".join(
		ch if ord(ch) < 128 else ("" if unicodedata.combining(ch) else " ")
		for ch in folded
	)
	return _CONTROL_RE.sub(" ", out)


def stripCommands(text):
	"""Remove square brackets so text can't carry inline commands."""
	return text.replace("[", " ").replace("]", " ")


def balanceBrackets(text):
	"""Keep only complete, non-nested [...] groups. An unmatched "[" would
	swallow the following text and the driver's index marks."""
	out = list(text)
	openPos = None
	for i, c in enumerate(text):
		if c == "[":
			if openPos is not None:
				out[openPos] = " "
			openPos = i
		elif c == "]":
			if openPos is None:
				out[i] = " "
			else:
				openPos = None
	if openPos is not None:
		out[openPos] = " "
	return "".join(out)


def splitMultiCase(text):
	"""Split mixed-case words ("DECTalk" -> "DEC Talk"), outside brackets."""
	out = []
	depth = 0
	n = len(text)
	for i, c in enumerate(text):
		if c == "[":
			depth += 1
		elif c == "]" and depth > 0:
			depth -= 1
		if i > 0 and depth == 0:
			prev = text[i - 1]
			if (prev.islower() and c.isupper()) or (
				prev.isupper() and c.isupper() and i + 1 < n and text[i + 1].islower()
			):
				out.append(" ")
		out.append(c)
	return "".join(out)
