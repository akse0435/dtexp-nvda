# Serial link to a DECtalk Express. No NVDA dependency except the logger.
#
# Protocol:
#   9600 baud, 8N1, XON/XOFF.
#   Ctrl+C stops and flushes speech; the unit answers Ctrl+A.
#   Ctrl+N pauses, Ctrl+O resumes, Ctrl+K forces out buffered text.
#   [:i r N] makes the unit answer "[:index N]" when speech reaches it.
#   An index is reported with the word that follows it, so the driver never
#   ends an utterance with an index (see moveTrailingMarks).
# A cut command makes the unit speak an error, so writes are never aborted or
# purged, and chunks only end outside square brackets.

import collections
import logging
import queue
import re
import threading
import time

import serial

try:
	from logHandler import log
except ImportError:  # outside NVDA (tests)
	log = logging.getLogger("dectalkexpress")

BAUD_RATE = 9600
BYTES_PER_SECOND = BAUD_RATE / 10.0
#: Preferred bytes per write; a cancel waits for at most one chunk.
WRITE_CHUNK = 32
#: Wait for Ctrl+A after Ctrl+C.
ACK_TIMEOUT = 0.35
#: Wait for Ctrl+A when opening the port (the unit answers in about 20 ms).
PROBE_TIMEOUT = 0.25
#: A status answer is complete after this much silence.
QUERY_IDLE = 0.4
#: A text line from the unit is complete after this much silence.
LINE_IDLE = 0.15
#: Quiet time after a "speak" status command: the unit inserts the spoken
#: text into its input later, which garbles any command arriving meanwhile.
SPEAK_SETTLE = 2.0
#: Index values cycle through 1..MAX_WIRE_INDEX.
MAX_WIRE_INDEX = 9999
#: Release unanswered indexes this long (plus the estimated duration) after
#: they were due, so NVDA never waits forever.
WATCHDOG_GRACE = 8.0

CTRL_A = 0x01
CTRL_C = b"\x03"
CTRL_K = b"\x0b"
CTRL_N = b"\x0e"
CTRL_O = b"\x0f"

#: End-of-utterance mark value.
DONE = object()

_TEXT_RE = re.compile(rb"\[\s*:\s*i(?:ndex)?\s*(?:r(?:eply)?\s*)?(\d+)\s*\]", re.I)
_DCS_RE = re.compile(rb"(?:\x1bP|\x90)\s*0?\s*;\s*31\s*;\s*(\d+)\s*z")

_CTL_CANCEL = "cancel"
_CTL_PAUSE = "pause"
_CTL_RESUME = "resume"
_CTL_QUERY = "query"
_WAKE = object()


def _decode(line):
	"""Printable text of a line from the unit."""
	return "".join(chr(b) for b in bytearray(line) if 32 <= b < 127).strip()


def _collect(lines, timeout, idle):
	"""Lines from queue `lines`: wait up to `timeout` for the first, then
	until `idle` seconds pass without more."""
	result = []
	deadline = time.time() + timeout
	while True:
		wait = idle if result else deadline - time.time()
		if wait <= 0:
			break
		try:
			line = lines.get(timeout=wait)
		except queue.Empty:
			break
		if line is None:  # connection closed
			break
		result.append(line)
	return [line for line in result if line]


class NoAnswerError(Exception):
	"""The port opened, but no DECtalk Express answered."""


def openPort(portName, timeout=0.05):
	"""Open `portName` with the DECtalk Express line settings."""
	return serial.Serial(
		port=portName,
		baudrate=BAUD_RATE,
		bytesize=serial.EIGHTBITS,
		parity=serial.PARITY_NONE,
		stopbits=serial.STOPBITS_ONE,
		xonxoff=True,
		rtscts=False,
		dsrdtr=False,
		timeout=timeout,
		write_timeout=None,
	)


def _modemLines(port):
	try:
		return "DSR=%d CTS=%d CD=%d" % (port.dsr, port.cts, port.cd)
	except Exception:
		return "modem lines unknown"


def waitForAck(port, timeout):
	"""Send Ctrl+C on an open port; True if Ctrl+A comes back in time."""
	try:
		port.reset_input_buffer()
	except Exception:
		pass
	port.write(CTRL_C)
	deadline = time.time() + timeout
	while time.time() < deadline:
		data = port.read(64)
		if data and CTRL_A in bytearray(data):
			return True
	return False


def splitChunks(data, size=WRITE_CHUNK):
	"""Split `data` into pieces of about `size` bytes, only outside [...]."""
	chunks = []
	start = 0
	depth = 0
	for i, b in enumerate(bytearray(data)):
		if b == 0x5B:  # [
			depth += 1
		elif b == 0x5D and depth:  # ]
			depth -= 1
		if depth == 0 and i + 1 - start >= size:
			chunks.append(data[start:i + 1])
			start = i + 1
	if start < len(data):
		chunks.append(data[start:])
	return chunks


def moveTrailingMarks(segments):
	"""Move indexes after the last text to just before its last word.

	The unit reports an index with the following word; a trailing index
	would only be reported after the unit's 5 second timeout.
	"""
	last = None
	for i, seg in enumerate(segments):
		if seg[0] == "text":
			last = i
	if last is None:
		return segments
	trailing = [seg for seg in segments[last + 1:] if seg[0] == "mark"]
	if not trailing:
		return segments
	rest = [seg for seg in segments[last + 1:] if seg[0] != "mark"]
	_kind, data, spoken = segments[last]
	stripped = data.rstrip()
	cut = max(stripped.rfind(b" "), stripped.rfind(b"]")) + 1
	if cut <= 0:
		before = []
		after = [segments[last]]
	else:
		before = [("text", data[:cut], spoken * cut // max(1, len(data)))]
		after = [("text", data[cut:], spoken - before[0][2])]
	return segments[:last] + before + trailing + after + rest


class _Mark(object):
	__slots__ = ("gen", "value", "start", "due")

	def __init__(self, gen, value, start, due):
		self.gen = gen
		self.value = value
		self.start = start
		self.due = due


class Connection(object):
	"""Link to a DECtalk Express on one port.

	Opening fails with NoAnswerError if the unit doesn't answer. The writer
	thread does all writes; the reader thread parses answers and calls
	onIndex/onDone. speak, cancel and pause never block.
	"""

	def __init__(self, portName, onIndex=None, onDone=None):
		self.portName = portName
		self._onIndex = onIndex
		self._onDone = onDone
		self._port = openPort(portName)
		try:
			if not waitForAck(self._port, PROBE_TIMEOUT):
				raise NoAnswerError(
					"no DECtalk Express answered on %s (%s)" % (portName, _modemLines(self._port))
				)
		except Exception:
			self._port.close()
			raise
		log.info("DECtalk Express: unit answers on %s (%s)" % (portName, _modemLines(self._port)))
		self._lock = threading.Lock()
		self._gen = 0
		self._pending = 0  # utterances queued but not yet registered
		self._queue = queue.Queue()
		self._ctl = collections.deque()
		self._marks = collections.OrderedDict()  # wire index -> _Mark
		self._nextWire = 0
		self._estimateEnd = 0.0
		self._wireFree = 0.0  # when the bytes written so far have left the port
		self._ackEvent = threading.Event()
		# Text sent since the last Ctrl+C. Not cleared when the last index is
		# reported: that happens as the last word starts.
		self._dirty = False
		self._message = bytearray()  # text line sent by the unit
		self._queries = []  # unanswered status commands: [bytes, sent]
		self._listeners = []  # queues receiving the unit's text lines
		self._paused = False
		self._pausedAt = None
		self._running = True
		self._closing = False
		self._holdUntil = 0.0  # no writes before this time (SPEAK_SETTLE)
		self._lastWriteError = 0.0
		self._writer = threading.Thread(target=self._writerLoop, name="DECtalkExpressWriter")
		self._writer.daemon = True
		self._reader = threading.Thread(target=self._readerLoop, name="DECtalkExpressReader")
		self._reader.daemon = True
		self._reader.start()
		self._writer.start()

	# -- public API (main thread) ---------------------------------------------

	def speak(self, segments, charsPerSecond):
		"""Queue an utterance: a list of ("cmd", bytes), ("text", bytes, spokenChars)
		and ("mark", index or DONE). charsPerSecond is used for the watchdog.
		"""
		with self._lock:
			self._pending += 1
			gen = self._gen
		self._queue.put((gen, segments, max(1.0, float(charsPerSecond))))

	def cancel(self):
		with self._lock:
			self._gen += 1
			self._pending = 0
			self._marks.clear()
			self._estimateEnd = 0.0
		try:
			while True:
				self._queue.get_nowait()
		except queue.Empty:
			pass
		self._ctl.append(_CTL_CANCEL)
		self._queue.put(_WAKE)

	def pause(self, switch):
		self._ctl.append(_CTL_PAUSE if switch else _CTL_RESUME)
		self._queue.put(_WAKE)

	def sendRaw(self, data, settle=0.0):
		"""Send commands such as "[:version speak]." after current speech.
		Nothing else is written for `settle` seconds afterwards."""
		segments = [("text", data, len(data))]
		if settle:
			segments.append(("hold", settle))
		self.speak(segments, 10.0)

	def query(self, command, timeout=10.0, idle=QUERY_IDLE):
		"""Send a status command and return the text lines the unit answers.

		Blocks; never call it from NVDA's main thread. The command waits for
		current speech, and is sent again if speech is cancelled before the
		unit answered.
		"""
		lines = queue.Queue()
		entry = [command, False]
		with self._lock:
			self._listeners.append(lines)
			self._queries.append(entry)
		try:
			self._ctl.append(_CTL_QUERY)
			self._queue.put(_WAKE)
			return _collect(lines, timeout, idle)
		finally:
			with self._lock:
				self._listeners.remove(lines)
				if entry in self._queries:
					self._queries.remove(entry)

	def close(self, stop=True):
		"""Close the port. stop=False lets queued writes finish instead of
		stopping speech."""
		self._closing = True
		with self._lock:
			del self._queries[:]
			for listener in self._listeners:
				listener.put(None)
		if stop:
			self.cancel()
		self._queue.put(None)
		self._writer.join(2.0)
		self._running = False
		try:
			cancelRead = getattr(self._port, "cancel_read", None)
			if cancelRead is not None:
				cancelRead()
		except Exception:
			pass
		self._reader.join(1.0)
		try:
			self._port.close()
		except Exception:
			pass

	# -- writer thread ------------------------------------------------------------

	def _writerLoop(self):
		while True:
			item = self._queue.get()
			try:
				self._processControls()
				if item is None:
					break
				if item is _WAKE:
					continue
				gen, segments, cps = item
				if gen != self._gen:
					continue
				self._writeUtterance(gen, segments, cps)
			except Exception:
				log.exception("DECtalk Express: writer error")
		log.debug("DECtalk Express: writer stopped")

	def _processControls(self, queries=True):
		cancel = False
		while True:
			try:
				op = self._ctl.popleft()
			except IndexError:
				break
			if op == _CTL_CANCEL:
				cancel = True
			elif op == _CTL_PAUSE:
				if not self._paused:
					self._paused = True
					self._pausedAt = time.time()
					self._write(CTRL_N)
			elif op == _CTL_RESUME:
				if self._paused:
					self._resumed()
					self._write(CTRL_O)
		if cancel:
			self._doCancel()
		if queries:
			# Only between utterances: never inside an utterance's text.
			self._sendQueries()

	def _sendQueries(self):
		with self._lock:
			todo = [q for q in self._queries if not q[1]]
		if not todo or self._closing:
			return
		self._waitHold()
		for q in todo:
			q[1] = True
			self._dirty = True
			self._write(q[0])

	def _waitHold(self):
		"""Wait out SPEAK_SETTLE; Ctrl+C, Ctrl+N and Ctrl+O still go out."""
		while not self._closing:
			delay = self._holdUntil - time.time()
			if delay <= 0:
				return
			time.sleep(min(delay, 0.05))
			if self._ctl:
				self._processControls(queries=False)

	def _resumed(self):
		self._paused = False
		if self._pausedAt is not None:
			shift = time.time() - self._pausedAt
			self._pausedAt = None
			with self._lock:
				for m in self._marks.values():
					m.start += shift
					m.due += shift
				if self._estimateEnd:
					self._estimateEnd += shift

	def _doCancel(self):
		wasPaused = self._paused
		if not self._dirty and not wasPaused:
			return
		self._ackEvent.clear()
		if self._write(CTRL_C + (CTRL_O if wasPaused else b"")):
			self._dirty = False
			self._paused = False
			self._pausedAt = None
		if not self._ackEvent.wait(ACK_TIMEOUT):
			log.debug("DECtalk Express: no Ctrl+A after Ctrl+C")
		# Ctrl+C also discarded unanswered status commands.
		with self._lock:
			for q in self._queries:
				q[1] = False

	def _allocWire(self):
		self._nextWire = self._nextWire % MAX_WIRE_INDEX + 1
		return self._nextWire

	def _writeUtterance(self, gen, segments, cps):
		parts = []
		entries = []  # (wire, value, spoken chars before it)
		spoken = 0
		hold = 0.0
		for seg in moveTrailingMarks(segments):
			kind = seg[0]
			if kind == "hold":
				hold = seg[1]
			elif kind == "cmd":
				parts.append(seg[1])
			elif kind == "text":
				parts.append(seg[1])
				spoken += seg[2]
			elif kind == "mark":
				wire = self._allocWire()
				parts.append(b"[:i r %d]" % wire)
				entries.append((wire, seg[1], spoken))
		data = b"".join(parts)
		with self._lock:
			if gen != self._gen:
				return
			self._pending = max(0, self._pending - 1)
			if spoken == 0 and not self._marks:
				# Nothing to say and unit idle: report the indexes at once.
				fire = [(gen, value) for _wire, value, _pos in entries]
			else:
				fire = None
				start = max(time.time(), self._estimateEnd)
				for wire, value, pos in entries:
					self._marks[wire] = _Mark(gen, value, start, start + pos / cps)
				self._estimateEnd = start + spoken / cps
		if fire is not None:
			self._fire(fire)
			return
		self._waitHold()
		if gen != self._gen:
			return
		self._dirty = True
		ok = self._writeData(gen, data)
		if hold:
			self._holdUntil = max(time.time(), self._wireFree) + hold
		if not ok and gen == self._gen and entries:
			# Port failed: release the indexes so NVDA doesn't wait.
			self._reached(entries[-1][0])

	def _writeData(self, gen, data):
		for chunk in splitChunks(data):
			if self._ctl:
				self._processControls(queries=False)
			if gen != self._gen:
				return True
			# Stay at most one chunk ahead of the wire, so a cancel is quick.
			delay = self._wireFree - time.time() - WRITE_CHUNK / BYTES_PER_SECOND
			if delay > 0:
				time.sleep(delay)
			if self._write(chunk) is None:
				return False
		return True

	def _write(self, data):
		"""Write all of `data`; returns bytes written, or None if the port failed."""
		if log.isEnabledFor(logging.DEBUG):
			log.debug("DECtalk Express TX: %r" % (data,))
		try:
			n = self._port.write(data)
		except Exception:
			now = time.time()
			if now - self._lastWriteError > 5:
				log.error("DECtalk Express: could not write to %s" % self.portName, exc_info=True)
			self._lastWriteError = now
			self._tryReopen()
			return None
		self._wireFree = max(time.time(), self._wireFree) + len(data) / BYTES_PER_SECOND
		return len(data) if n is None else n

	def _tryReopen(self):
		try:
			self._port.close()
		except Exception:
			pass
		try:
			self._port = openPort(self.portName)
			log.info("DECtalk Express: reopened %s" % self.portName)
		except Exception:
			pass

	# -- reader thread -------------------------------------------------------------

	def _readerLoop(self):
		buf = bytearray()
		lastData = 0.0
		while self._running:
			port = self._port
			try:
				waiting = port.in_waiting
				data = port.read(waiting if waiting > 0 else 1)
			except Exception:
				if not self._running:
					break
				time.sleep(0.2)
				continue
			try:
				if data:
					lastData = time.time()
					self._feed(buf, data)
				elif self._message and time.time() - lastData > LINE_IDLE:
					# The unit starts its lines with CR LF CR but doesn't end
					# them; a line is complete when the unit goes quiet.
					self._logMessage()
				self._checkWatchdog()
			except Exception:
				log.exception("DECtalk Express: reader error")
				del buf[:]
		log.debug("DECtalk Express: reader stopped")

	def _feed(self, buf, data):
		if log.isEnabledFor(logging.DEBUG):
			log.debug("DECtalk Express RX: %r" % (data,))
		for b in bytearray(data):
			if b == CTRL_A:
				# Ctrl+C may cut a reply short; drop the partial text.
				self._ackEvent.set()
				del buf[:]
				del self._message[:]
			elif b in (0x0A, 0x0D):
				self._logMessage()
			elif b not in (0x11, 0x13):  # stray XON/XOFF
				buf.append(b)
				self._message.append(b)
		while buf:
			m = _TEXT_RE.search(buf) or _DCS_RE.search(buf)
			if not m:
				break
			wire = int(m.group(1))  # before del: the match refers to buf
			del buf[:m.end()]
			self._reached(wire)
		# Keep only the start of an incomplete reply.
		start = max(buf.rfind(b"["), buf.rfind(b"\x1b"), buf.rfind(b"\x90"))
		if start < 0:
			del buf[:]
		else:
			del buf[:start]
			if len(buf) > 64:
				del buf[:]

	def _logMessage(self):
		"""Log a text line from the unit, such as "serial errors=0019 overflows=0000"."""
		# An index reply can run straight into a status answer.
		line = _DCS_RE.sub(b"", _TEXT_RE.sub(b"", bytes(self._message))).strip()
		del self._message[:]
		if not line:
			return
		with self._lock:
			listeners = list(self._listeners)
			if listeners and self._queries:
				self._queries.pop(0)
		if listeners:
			log.debug("DECtalk Express answers: %r" % (line,))
			for q in listeners:
				q.put(_decode(line))
		else:
			log.warning("DECtalk Express says: %r" % (line,))

	def _checkWatchdog(self):
		if self._paused:
			return
		now = time.time()
		target = None
		with self._lock:
			for wire, m in self._marks.items():
				if m.due + WATCHDOG_GRACE + (m.due - m.start) > now:
					break
				target = wire
		if target is not None:
			log.warning("DECtalk Express: index replies overdue; releasing them")
			self._reached(target)

	# -- index bookkeeping ------------------------------------------------------

	def _reached(self, wire):
		"""Index `wire` was spoken: report it and every earlier pending one."""
		with self._lock:
			if wire not in self._marks:
				return
			fire = []
			while self._marks:
				w, m = self._marks.popitem(last=False)
				fire.append((m.gen, m.value))
				if w == wire:
					break
		self._fire(fire)

	def _fire(self, fire):
		with self._lock:
			gen = self._gen
			moreDone = self._pending > 0 or any(
				m.value is DONE for m in self._marks.values()
			)
		done = False
		for markGen, value in fire:
			if markGen != gen:
				continue
			if value is DONE:
				done = True
			elif self._onIndex is not None:
				try:
					self._onIndex(value)
				except Exception:
					log.exception("DECtalk Express: index callback failed")
		if done and not moreDone:
			if self._onDone is not None:
				try:
					self._onDone()
				except Exception:
					log.exception("DECtalk Express: done callback failed")


# -- settings panel helpers --


def probePort(portName):
	"""True if a DECtalk Express answers on `portName`."""
	try:
		port = openPort(portName)
	except Exception:
		return False
	try:
		return waitForAck(port, PROBE_TIMEOUT)
	except Exception:
		return False
	finally:
		try:
			port.close()
		except Exception:
			pass


def sendText(portName, text):
	"""Open `portName`, speak `text` and close it."""
	if not isinstance(text, bytes):
		text = text.encode("ascii", "replace")
	port = openPort(portName)
	try:
		if not waitForAck(port, PROBE_TIMEOUT):
			raise NoAnswerError("no DECtalk Express answered on %s" % portName)
		port.write(text + CTRL_K)
		port.flush()
	finally:
		port.close()
